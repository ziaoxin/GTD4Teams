/**
 * 数据访问层（本地 Python 服务 + 同源 REST）。
 *
 * 这一版与上一版的唯一区别是**后端换了**：
 *   上一版：浏览器直连云平台 —— 身份靠平台托管（邮件验证码），数据靠数据库 REST 接口，
 *           权限靠服务端 RLS；代价是整套代码绑死在那个平台上，换台机器就跑不起来。
 *   这一版：浏览器只跟同源的 `/api/*` 说话，背后是自带的 Python 服务
 *           （自带账号体系 + 自持 SQLite 存储 + 服务端权限判定）。
 *
 * 对外导出（v1.1 起以**本地实现**为准，增删都必须登记在 `06` §10.7 的表里）：
 * 不再有 `registerWithPassword` / `checkEmail`（自助注册已在 CR-2026-001 下线，
 * 对应端点返回 404，留着导出等于给前端一个永远失败的函数）；
 * 新增 `createAccount` / `listAccounts`（管理员建号），导出总数仍为 27。
 * `js/app.js` 的业务逻辑与渲染层因此没有大改 —— 这也是这次替换能够被验证的原因。
 *
 * 三条仍然成立的纪律
 *   1. 归属永不提交：`owner_id` 一律由服务端注入，客户端给什么都不采纳；
 *   2. 被拦下不等于报错：更新/删除返回空数组即"没改到"，由调用方如实提示；
 *   3. 数据隔离在服务端：前端不做"这条是不是我的"判断（那只是显示层）。
 */

import { cleanMessage } from './util.js?v=20261118b'

const REQUEST_TIMEOUT = 30000

/* ---------------- 传输层 ---------------- */

/** 结构化错误：带上机读的 code / status / kind，供 friendlyError 判定。*/
function apiError(message, { code = null, status = 0, kind = null } = {}) {
  const err = new Error(message)
  err.code = code
  err.status = status
  err.kind = kind
  return err
}

/**
 * 统一的请求出口。
 *
 * 为什么要自带超时：网络层一旦挂起而不 settle，界面就会永久停在「处理中」。
 * util.withTimeout 只能保护调用方那一侧，这里再兜一层，保证 fetch 不会真的悬着。
 */
async function request(path, { method = 'GET', body, timeout = REQUEST_TIMEOUT } = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)
  let response
  try {
    response = await fetch(path, {
      method,
      credentials: 'same-origin',        // 会话 Cookie 同源携带
      signal: controller.signal,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (err) {
    if (err?.name === 'AbortError') throw apiError('请求超时，请稍后重试', { kind: 'timeout' })
    throw apiError('无法连接本地服务，请确认服务进程仍在运行', { kind: 'network' })
  } finally {
    clearTimeout(timer)
  }

  const text = await response.text()
  let payload = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = null
    }
  }

  if (!response.ok) {
    const detail = payload?.error || {}
    throw apiError(cleanMessage(detail.message || `请求失败（HTTP ${response.status}）`), {
      code: detail.code || null,
      status: response.status,
      kind: response.status === 401 ? 'unauthenticated' : null,
    })
  }
  return payload
}

/* ---------------- 会话 ---------------- */

/**
 * 读取当前会话；无会话或会话失效返回 null（不抛错，便于门禁判断）。
 *
 * 形状不变；`user` 上新增两个布尔（`06` §10.5.5）：
 * `isSystemAdmin`（是否有实例级建号/配置权）、`mustChangePassword`（是否必须先改初始口令）。
 * 这里**不做**"是否强制改密"的判断 —— 门禁属于 `app.js` 的启动流程，
 * 放在接缝里会让"什么时候该跳转"出现第二个答案。
 */
export async function getSession() {
  try {
    const data = await request('/api/auth/session')
    return data?.user ? data : null
  } catch {
    return null
  }
}

export async function getCurrentUser() {
  return (await getSession())?.user || null
}

/**
 * 账号资料 { id, name, email }。
 *
 * 上一版必须单独调它、因为平台的会话对象里**没有 email**（界面因此显示过字面量"已登录"）。
 * 本地版的服务端两者都给，但保留这条接口：它是"权威资料"这一层语义，
 * 也让 app.js 的 hydrateIdentity() 不必改动。
 * 与 `getSession()` 同口径：`user` 上带 `isSystemAdmin` / `mustChangePassword`。
 */
export async function getProfile() {
  return await request('/api/auth/profile')
}

export async function signOut() {
  try {
    await request('/api/auth/logout', { method: 'POST', body: {} })
  } catch {
    /* 网络失败不应阻断登出：本地会话可能已失效，界面必须能回到登录页 */
  }
}

/* ---------------- 认证动作 ---------------- */

export async function loginWithPassword(email, password) {
  return await request('/api/auth/login', { method: 'POST', body: { email, password } })
}

/**
 * 已登录状态下修改密码（服务端先校验原密码，并踢掉其它会话）。
 *
 * 两处调用，语义完全一样：
 *   1. 账号面板里的自助改密；
 *   2. 首次登录/口令被重置后的**强制改密页** —— 该场景下用户知道自己的初始口令，
 *      所以仍然要传 `oldPassword`，不能用"免旧密码重置"那套。
 * 成功后服务端会把 `must_change_password` 清 0（同一事务），因此调用方必须**重新读会话**
 * 再进业务界面，否则界面会带着旧的"仍需改密"状态再被拦一次。
 */
export async function changePassword(oldPassword, newPassword) {
  await request('/api/auth/password', { method: 'POST', body: { oldPassword, newPassword } })
}

/* ---------------- 读取 ---------------- */

/** 一次性拉取当前账号可见的全部工作区数据（服务端已按权限收敛范围）。 */
export async function loadWorkspace() {
  return await request('/api/workspace')
}

/* ---------------- 写操作 ---------------- */

async function mutate(table, op, payload, id) {
  const data = await request('/api/rows', {
    method: 'POST',
    body: { table, op, id: id ?? null, payload: payload || {} },
  })
  return data?.rows || []
}

export const createRow = (table, payload) => mutate(table, 'insert', payload)
export const updateRow = (table, id, payload) => mutate(table, 'update', payload, id)
export const deleteRow = (table, id) => mutate(table, 'delete', {}, id)

export const createItem = (payload) => createRow('work_items', payload)
export const updateItem = (id, payload) => updateRow('work_items', id, payload)
export const deleteItem = (id) => deleteRow('work_items', id)

/* ---------------- 团队生命周期 ---------------- */

/**
 * 创建团队并把创建者登记为 owner。
 * 两步在服务端同一事务内完成 —— 如果成员行没建成功而团队行留下了，
 * 用户会得到一个谁也进不去的"孤儿空间"（云版本正是这么失败过的）。
 */
export async function createTeamWithOwner({ name, description, mission, joinCode, ownerEmail, ownerName }) {
  const data = await request('/api/teams/create', {
    method: 'POST',
    body: { name, description, mission, joinCode, ownerEmail, ownerName },
  })
  const team = data?.team
  if (!team) throw apiError('团队创建失败，请重试')
  return team
}

/** 用邀请码加入团队。返回 team_id（调用方按 Number() 使用）。 */
export async function joinTeamByCode(code) {
  const data = await request('/api/teams/join', {
    method: 'POST',
    body: { code: String(code || '').trim() },
  })
  return data?.teamId
}

/* ---------------- 账号管理（管理员建号） ---------------- */

/**
 * 创建一个账号 —— 团队管理员建「成员」/ 系统管理员建「团队管理员」都走这一条。
 *
 * 契约（`06` §10.3）：`POST /api/admin/accounts` → **201**
 *   { user: {id,email,name,isSystemAdmin,mustChangePassword}, teamId, role, password }
 *
 * ⚠️ 响应里的 `password` 是**一次性明文初始口令，只出现这一次**。
 * 本函数原样返回整个响应体（不吞 201、不改字段名）；调用方有三条硬约束：
 *   · 立刻交给视图展示，**不要**放进任何 state；
 *   · **不要**写 localStorage / sessionStorage（"只显示一次"就是"刷新即失"）；
 *   · **不要**传进 log()（`SENSITIVE_KEYS` 只是兜底，不是许可）。
 *
 * `teamId = null` 表示只建号、不加入任何团队（只有系统管理员能这么做；
 * 团队管理员省略会得到 400 `TEAM_REQUIRED`，由服务端判定，前端不做例外）。
 */
export async function createAccount({ email, name = '', teamId = null, role = null }) {
  return await request('/api/admin/accounts', {
    method: 'POST',
    body: { email, name, teamId, role },
  })
}

/**
 * 读取可见范围内的账号列表（用于避免"靠撞 409 猜现状"）。
 *
 * `teamId == null` 时不拼 query：系统管理员 = 实例级；团队管理员省略会得 400 `TEAM_REQUIRED`。
 * 两种范围都由服务端判定，"能看到谁"不在这层各写一遍。
 */
export async function listAccounts({ teamId = null } = {}) {
  const query = teamId == null ? '' : `?teamId=${encodeURIComponent(teamId)}`
  return await request(`/api/admin/accounts${query}`)
}

/* ---------------- 复盘记录 ---------------- */

/**
 * 保存复盘记录：同一 (账号, 团队, 类型, 周期) 只保留一条，重复提交即更新。
 * 唯一键的匹配在服务端完成（传不传 existingId 都落到同一行）。
 */
export async function saveReview(payload, existingId) {
  const data = await request('/api/reviews/save', {
    method: 'POST',
    body: { payload, existingId: existingId ?? null },
  })
  return data?.rows || []
}

/** 写入一条进度日志。 */
export async function createLog(payload) {
  return await createRow('progress_logs', payload)
}

/* ---------------- 运行日志 ---------------- */

/**
 * 把运行日志送到服务端（落到 app_logs 表，等价于"后台日志文件"）。
 * 允许未登录上报：登录失败、改密失败这类故障正是最需要留证的场景。
 *
 * 纪律：
 *  1. 日志失败绝不能影响业务 —— 全程静默吞掉异常；
 *  2. 绝不记录密码、完整令牌等敏感值。
 */
const logBuffer = []
let logTimer = null
const sessionId = Math.random().toString(36).slice(2, 10)

export const getSessionId = () => sessionId

/** 需要脱敏的字段名 —— 必须「精确匹配」，避免误伤 errCode 等排障关键字段。 */
const SENSITIVE_KEYS =
  /^(password|passwd|pwd|secret|token|accesstoken|refreshtoken|nonce|code|verificationcode|verificationid|verificationtoken|challenge|authorization|sudotoken|apikey|signupcode)$/i

function sanitize(detail) {
  if (!detail || typeof detail !== 'object') return detail
  const out = {}
  for (const [k, v] of Object.entries(detail)) {
    if (v == null || typeof v === 'boolean' || typeof v === 'number') {
      out[k] = v
      continue
    }
    if (SENSITIVE_KEYS.test(k)) {
      out[k] = `***(长度 ${String(v).length}，已脱敏)`
      continue
    }
    if (typeof v === 'string' && v.length > 500) { out[k] = v.slice(0, 500) + '…(截断)'; continue }
    out[k] = v
  }
  return out
}

function flushLogs() {
  logTimer = null
  const rows = logBuffer.splice(0, logBuffer.length)
  for (const row of rows) {
    try {
      const p = request('/api/logs', { method: 'POST', body: row, timeout: 8000 })
      if (p && typeof p.then === 'function') p.then(() => {}, () => {})
    } catch { /* 日志失败绝不影响业务 */ }
  }
}

/**
 * 写一条运行日志。签名：log(level, event, message, detail)
 */
export async function log(level, event, message = '', detail = {}) {
  const scope = String(event).split('.')[0] || 'app'
  const row = {
    level,
    scope,
    event,
    message: cleanMessage(message).slice(0, 1000),
    detail: { ...sanitize(detail), sid: sessionId },
    page_url: typeof location !== 'undefined' ? String(location.href).slice(0, 300) : null,
    user_agent: typeof navigator !== 'undefined' ? String(navigator.userAgent).slice(0, 300) : null,
  }
  if (typeof console !== 'undefined') {
    const line = `[${level}] ${event} :: ${row.message}`
    if (level === 'error') console.error(line, detail)
    else if (level === 'warn') console.warn(line, detail)
    else console.log(line, detail)
  }
  logBuffer.push(row)
  if (logBuffer.length >= 8) flushLogs()
  else if (!logTimer) logTimer = setTimeout(flushLogs, 1200)
}

/** 结构化一个错误，保留可机读字段 */
export function describeError(error) {
  return {
    errCode: error?.code || null,
    kind: error?.kind || null,
    status: error?.status ?? null,
    name: error?.name || null,
    message: cleanMessage(readMessage(error)),
  }
}

/**
 * 提取可读的错误文案。
 *
 * 只接受字符串来源：早期实现写了 `error?.message || String(error)`，
 * 传进来一个普通对象时会得到 `"[object Object]"`，并因为它是非空字符串而**顶掉**
 * 兜底文案显示到界面上。抛字符串的写法仍然兼容。
 */
function readMessage(error) {
  if (!error) return ''
  if (typeof error === 'string') return error
  const message = error.message
  return typeof message === 'string' ? message : ''
}

/* ---------------- 服务可用性 ---------------- */

/** 探测本地服务。启动门禁用它，避免"页面打开了但后端没起来"变成一片空白。 */
export async function health() {
  return await request('/api/health', { timeout: 6000 })
}

/* ---------------- AI（自带密钥，密钥只在服务端） ---------------- */

export async function getLlmConfig() {
  return await request('/api/llm/config')
}

/** 保存 AI 配置。apiKey 传空串表示清除。 */
export async function saveLlmConfig({ baseUrl, apiKey, model }) {
  return await request('/api/llm/config', { method: 'POST', body: { baseUrl, apiKey, model } })
}

/** 一次性对话：返回 { text, reasoning, model, usage }。messages 可含 image_url 分片。 */
export async function llmChat({ messages, model, temperature, maxTokens, jsonMode }) {
  return await request('/api/llm/chat', {
    method: 'POST',
    timeout: 180000,
    body: {
      messages,
      model,
      temperature,
      max_tokens: maxTokens,
      response_format: jsonMode ? { type: 'json_object' } : undefined,
    },
  })
}

/* ---------------- 证书管理（系统管理员，实例级） ---------------- */

/**
 * 读取当前 HTTPS 证书状态。
 *
 * 响应有两个形态（证书契约：主理人下发，待并入 `06`）：
 *   未配置 `{configured:false, source:'none', pendingApply:false}`
 *   已配置 `{configured:true, source, subject, issuer, sans[], notBefore, notAfter,
 *           daysRemaining, fingerprintSha256, selfSigned, uploadedAt, uploadedBy, pendingApply}`
 * **详情字段可能是 `null`**（服务端解析证书失败时会降级为"只知道有证书"），
 * 所以渲染层一律按"可能缺字段"处理，不要假设一定存在。
 */
export async function getCert() {
  return await request('/api/admin/cert')
}

/**
 * 上传证书与私钥（PEM 文本，JSON 传输 —— 后端不是 multipart）。
 *
 * ⚠️ `keyPem` 是**私钥明文**：只在这一次请求体里出现，
 * 不写 state、不写 localStorage、不 log()；提交成功后调用方必须立刻清空输入框。
 */
export async function uploadCert({ certPem, keyPem }) {
  return await request('/api/admin/cert', { method: 'POST', body: { certPem, keyPem } })
}

/** 移除当前证书（回退到 HTTP 明文或运维自签名）。响应带 `pendingApply`，需要轮询等生效。 */
export async function deleteCert() {
  return await request('/api/admin/cert', { method: 'DELETE' })
}

/* ---------------- 错误码语义 ---------------- */

export function friendlyError(error) {
  const kind = error?.kind || ''
  const code = String(error?.code || '').toUpperCase()
  const msg = cleanMessage(readMessage(error))

  if (kind === 'timeout') return msg || '请求超时，请稍后重试'
  if (kind === 'network' || kind === 'backend-unavailable') {
    return '无法连接本地服务，请确认服务进程仍在运行（python server.py）。'
  }

  // 服务端已经把绝大多数情况写成了可直接展示的中文文案，这里只做少量必要的归一。
  //
  // 判定规则（顺序不能换）：
  //   ① 已知语义**唯一**的码 → 硬编码一句（PASSWORD_CHANGE_REQUIRED / NOT_TEAM_ADMIN / …）；
  //   ② 语义**随调用者或场景变化**的码 → `msg || 兜底`，让服务端决定措辞。
  //      MAP 命中即返回（`if (MAP[code]) return MAP[code]`），硬编码会把服务端打磨过的
  //      文案整段顶掉 —— EMAIL_EXISTS 在建号场景下说"请直接登录"就是错的且有害。
  //   ③ `code` 缺失时才按 `status` 兜底（静态资源 404 是 text/plain、没有 JSON 体，
  //      `err.code` 为 null）。**先判 code 再判 status**：`TEAM_NOT_FOUND` 本身就是 404，
  //      反过来的话"团队已归档"会被说成"功能已下线"，用户去刷新页面永远修不好。
  const MAP = {
    UNAUTHENTICATED: '登录状态已失效，请重新登录。',
    INVALID_CREDENTIALS: '邮箱或密码不正确。',
    INVALID_OLD_PASSWORD: '原密码不正确。',
    // 语义随场景变化：登录/建号/加入团队各有各的出路，交给服务端说
    EMAIL_EXISTS: msg || '该邮箱已有账号。',
    INVALID_EMAIL: '请输入有效的邮箱地址。',
    WEAK_PASSWORD: msg || '密码强度不足。',
    RATE_LIMITED: '尝试次数过多，请稍后再试。',
    PERMISSION_DENIED: '没有权限执行该操作（可能你不是该团队成员）。',
    INVALID_JOIN_CODE: '邀请码无效，请向团队所有者确认。',
    JOIN_CODE_TAKEN: '该邀请码已被占用，请重新生成。',
    MEMBER_DISABLED: msg || '你已被移出该团队，请联系团队所有者。',
    UNKNOWN_TABLE: msg || '数据表不存在或不可写。',
    BAD_PAYLOAD: msg || '提交的数据格式不正确。',
    BAD_JSON: msg || '提交的数据格式不正确。',
    PAYLOAD_TOO_LARGE: msg || '提交的内容过大。',
    CROSS_ORIGIN_BLOCKED: '请求被安全策略拒绝（跨站来源）。',
    // ---- CR-2026-001 新增（`06` §10.3.5 / §10.5.1）----
    // 闸门文案与服务端逐字一致：这句话会出现在业务被拦的每一种场景里，且只有一种含义
    PASSWORD_CHANGE_REQUIRED: '初始密码尚未修改，请先设置新密码后再继续（当前操作已锁定）',
    // 通用码：不只用于建号，硬编码成"创建账号"的文案会让别处抛 FORBIDDEN 时说着不相关的话
    FORBIDDEN: msg || '没有权限执行该操作。',
    NOT_TEAM_ADMIN: '你不是该团队的管理员',
    // 系统管理员传 member 也会收到这个码 —— 兜底必须是中性句，不能写"只能创建成员"
    ROLE_NOT_ALLOWED: msg || '你无权授予该角色。',
    TEAM_REQUIRED: '请先选择要加入的团队',
    TEAM_NOT_FOUND: '团队不存在或已归档',
    // 引导态：不是"你没权限"，而是这个实例还没初始化。UI 要按"操作指引"渲染，不给重试
    NO_SYSTEM_ADMIN: msg || '本实例还没有系统管理员。请先在服务器执行 python server.py --create-admin <邮箱> 完成引导。',
    // ---- 证书管理（证书契约：主理人下发，待并入 `06`）：四种失败原因对用户都是"换一份材料再传"，措辞固定 ----
    CERT_INVALID_PEM: msg || '证书或私钥格式不正确，请粘贴完整 PEM（含 BEGIN/END 行）',
    CERT_KEY_MISMATCH: msg || '证书与私钥不匹配，请确认两者是同一对',
    CERT_EXPIRED: msg || '该证书已过期，请上传有效证书',
    CERT_KEY_ENCRYPTED: msg || '私钥带有口令保护，请上传未加密的私钥',
    LLM_NOT_CONFIGURED: msg || '尚未配置 AI 服务。',
    LLM_UNREACHABLE: msg || '无法连接 AI 服务。',
    LLM_UPSTREAM_ERROR: msg || 'AI 服务返回错误。',
    LLM_TIMEOUT: msg || 'AI 服务响应超时。',
    // 已下线的端点：服务端会回「接口 /api/auth/register 不存在」这种技术文案，
    // 旧标签页上的用户会当成系统故障去重启服务。这里给一句面向用户的通用说法。
    NOT_FOUND: '该功能已下线或不存在，请刷新页面后重试。',
  }
  if (MAP[code]) return MAP[code]

  // code 缺失时才按状态码兜底（静态资源 404 是 text/plain，没有 JSON 体 → code 为 null）。
  // 顺序必须"先 code 后 status"：TEAM_NOT_FOUND 也是 404，反过来会被说成"功能已下线"。
  if (!code && error?.status === 404) return '该功能已下线或不存在，请刷新页面后重试。'

  return msg || '操作失败，请稍后重试。'
}
