/**
 * 应用启动与交互冒烟测试（真实 DOM + 桩 HTTP 响应）。
 *
 * 验证目标 —— 这些是"跑起来才会炸"的集成契约，静态检查与单视图渲染都覆盖不到：
 *   1. index.html 提供的挂载点（#space-tabs / #team-switch / #sidebar / #main …）与 app.js 的选择器逐一对齐；
 *   2. 会话有效时能自动进入应用外壳并完成首屏渲染，不抛异常；
 *   3. 空间切换、侧栏导航、全局搜索、快速收集、打开详情抽屉等交互链路可用；
 *   4. 写操作（快速收集）确实落到 work_items，且不携带 owner_id（归属由服务端注入，客户端不该提交）。
 *   5. 页面不引任何外部脚本（不依赖 CDN，离线也能跑）。
 *
 * 运行：node .smoke/app-boot-test.mjs
 */
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { parseHTML } from './vendor/linkedom.js'

const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(here, '..')

/* ---------- 用真实的 index.html 搭建 DOM ---------- */
const html = readFileSync(path.join(root, 'index.html'), 'utf8')
const { document, Event, window: domWindow } = parseHTML(html)

globalThis.document = document
globalThis.Event = Event
globalThis.window = globalThis

/** Node 里部分全局（navigator/location/localStorage）是只读 getter，必须用 defineProperty 覆盖 */
function defineGlobal(name, value) {
  try {
    globalThis[name] = value
    if (globalThis[name] === value) return
  } catch { /* 落到 defineProperty */ }
  Object.defineProperty(globalThis, name, { value, writable: true, configurable: true })
}

/** 剪贴板写入记录：用来证明「复制」按钮真的把口令交给了用户 */
const clipboard = []
defineGlobal('navigator', {
  userAgent: 'smoke-test/1.0',
  clipboard: { writeText: async (text) => { clipboard.push(String(text)) } },
})
defineGlobal('location', { href: 'http://127.0.0.1:8787/', origin: 'http://127.0.0.1:8787' })

/* addEventListener：window 与 document 都需要（app.js 绑全局委托与异常日志） */
const listeners = new Map()
defineGlobal('addEventListener', (type, fn) => {
  if (!listeners.has(type)) listeners.set(type, [])
  listeners.get(type).push(fn)
})
defineGlobal('removeEventListener', () => {})

/* localStorage */
const lsData = new Map()
defineGlobal('localStorage', {
  getItem: (k) => (lsData.has(k) ? lsData.get(k) : null),
  setItem: (k, v) => lsData.set(k, String(v)),
  removeItem: (k) => lsData.delete(k),
  clear: () => lsData.clear(),
})

/* confirm / alert */
defineGlobal('confirm', () => true)

/**
 * FormData：Node 内置的 FormData（undici）只接受原生表单，喂 linkedom 的元素会直接抛
 * "Argument 1 could not be converted"。浏览器里 `new FormData(form)` 是标准用法，
 * 所以这里补一个最小实现（与 auth-flow-test 同一份逻辑），而不是去改源码迁就测试环境。
 */
class MiniFormData extends Map {
  constructor(form) {
    super()
    if (!form) return
    for (const input of form.querySelectorAll('input[name]')) {
      this.set(input.getAttribute('name'), input.getAttribute('value') ?? input.value ?? '')
    }
  }
}
defineGlobal('FormData', MiniFormData)

/* 让 input.focus / select 可用（linkedom 未实现） */
{
  const proto = Object.getPrototypeOf(document.createElement('input'))
  if (!proto.focus) proto.focus = function () {}
  if (!proto.select) proto.select = function () {}
  if (!proto.setSelectionRange) proto.setSelectionRange = function () {}
}

/* ---------- 夹具数据 ---------- */
const pad = (n) => String(n).padStart(2, '0')
const ds = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
const now = new Date()
const iso = now.toISOString()
const day = (n) => ds(new Date(now.getTime() + n * 86400000))
const hours = (n) => new Date(now.getTime() + n * 3600000).toISOString()

const UID = 'uid-owner-0001'
const TEAM = 1

const TABLES = {
  teams: [{ id: TEAM, owner_id: UID, owner_name: 'owner', name: '基础设施运维组', mission: '保障核心系统 7×24 稳定运行', description: '6 人小组', join_code: 'OPS-4F2K', board_config: null, sla_config: null, archived: false, created_at: iso, updated_at: iso }],
  team_members: [
    { id: 11, team_id: TEAM, user_id: UID, email: 'owner@example.com', display_name: '李沛', role: 'owner', title: '运维负责人', capacity: 6, status: 'active', created_at: iso, updated_at: iso },
    { id: 12, team_id: TEAM, user_id: 'uid-member-0002', email: 'member@example.com', display_name: '王敏', role: 'member', title: '系统工程师', capacity: 5, status: 'active', created_at: iso, updated_at: iso },
  ],
  work_items: [
    { id: 101, team_id: TEAM, owner_id: UID, title: '每日机房巡检', item_type: 'inspection', status: 'doing', priority: 'p3', swimlane: 'planned', assignee_id: UID, assignee_name: '李沛', progress: 40, due_date: day(0), focus_date: day(0), sla_due_at: hours(3), recurrence: { mode: 'after', interval: 1, unit: 'day' }, flagged: true, is_next: false, kanban_order: 1, created_at: iso, updated_at: iso, completed_at: null },
    { id: 102, team_id: TEAM, owner_id: UID, title: '核心交换机端口告警', item_type: 'incident', status: 'doing', priority: 'p1', swimlane: 'unplanned', assignee_id: 'uid-member-0002', assignee_name: '王敏', progress: 60, due_date: day(0), sla_due_at: hours(-1), kanban_order: 2, created_at: iso, updated_at: iso, completed_at: null },
    { id: 103, team_id: TEAM, owner_id: UID, title: '新同事账号开通', item_type: 'request', status: 'ready', priority: 'p4', swimlane: 'unplanned', progress: 0, kanban_order: 3, created_at: iso, updated_at: iso, completed_at: null },
    { id: 201, team_id: null, owner_id: UID, title: '读《凤凰项目》第三章', item_type: 'routine', status: 'intake', priority: 'p4', swimlane: 'planned', progress: 0, created_at: iso, updated_at: iso },
    { id: 202, team_id: null, owner_id: UID, title: '研究 Ansible 自动化改造', item_type: 'routine', status: 'ready', priority: 'p3', swimlane: 'planned', due_date: day(0), focus_date: day(0), flagged: true, is_next: true, progress: 0, created_at: iso, updated_at: iso },
  ],
  projects: [{ id: 201, team_id: TEAM, owner_id: UID, name: '监控与告警平台升级', status: 'active', kind: 'improve', lead_id: UID, lead_name: '李沛', progress: 35, color: '#14867a', sort_order: 0, created_at: iso, updated_at: iso }],
  areas: [{ id: 1, team_id: TEAM, owner_id: UID, name: '网络与链路', criticality: 'high', description: '路由交换', archived: false, sort_order: 0, created_at: iso, updated_at: iso }],
  contexts: [
    { id: 1, team_id: TEAM, owner_id: UID, name: '值守台', kind: 'mode', sort_order: 0, created_at: iso, updated_at: iso },
    { id: 4, team_id: null, owner_id: UID, name: '深度专注', kind: 'energy', sort_order: 1, created_at: iso, updated_at: iso },
  ],
  resources: [{ id: 301, team_id: TEAM, owner_id: UID, kind: 'runbook', title: '核心交换机应急处置手册', category: '网络', criticality: 'high', pinned: true, archived: false, created_at: iso, updated_at: iso }],
  reviews: [{ id: 401, team_id: null, owner_id: UID, kind: 'daily', period_key: day(0), notes: '收集箱已清空', stats: {}, completed_at: iso, created_at: iso, updated_at: iso }],
  on_call: [],
  progress_logs: [{ id: 601, team_id: TEAM, owner_id: UID, item_id: 102, kind: 'progress', note: '已更换光纤跳线', progress: 60, created_at: iso }],
}

/* ---------- 桩本地服务（拦 fetch，而不是拦某个 SDK） ---------- *
 * 从"桩云服务 SDK"改成"桩 HTTP"是有意的：
 * 现在 api.js 走的是同源 `/api/*`，打桩 fetch 才能真正覆盖
 * "接口路径 / 请求方法 / body 形状"这层契约 —— 桩 SDK 反而会把契约掩盖掉。
 */
const writes = []
const calls = []
/** 需要断言"请求体形状"的用例读这里（calls 只记 `${method} ${path}`） */
const requests = []
/** 运行时日志的上报体：用来证明一次性口令没有被写进日志 */
const logRows = []
/** 返回 true 时，对应请求被改写成 `{ rows: [] }` —— 模拟被服务端 policy 拦下 */
let rowsOverride = null
/** 建号端点的错误桩：非 null 时 POST /api/admin/accounts 返回该错误 */
let accountError = null
/** 返回 true 时，除白名单外一律 403 PASSWORD_CHANGE_REQUIRED —— 模拟强制改密闸门 */
let gateForbidden = false
/** 证书状态桩：GET 返回它，POST/DELETE 改写它 */
let certStub = { configured: false, source: 'none', pendingApply: false }
/** 证书上传的请求体（用来断言 JSON + PEM 文本，不是 multipart） */
const certUploads = []
/** 非 null 时 POST /api/admin/cert 返回该错误（用于验证失败路径的私钥清空） */
let certPostError = null
const TEMP_PASSWORD = 'Xk7pQ2mRt9vWb4Ns'
let SESSION_USER = { id: UID, email: 'lpnl@nnit.com', name: 'lpnl', isSystemAdmin: true, mustChangePassword: false }
/** 集成域：令牌列表 / 一次性明文 / request_id 日志桩（REQ-055） */
const INT_TOKEN = 'gtd_9f8e7d6c5b4a3210'
const INT_ROTATED_TOKEN = 'gtd_r7t8y9u0i1o2p3q4'
const tokenCreates = []
const tokenRotates = []
const tokenRevokes = []
const logQueries = []
let tokenSeq = 1
let tokenStub = [
  { id: 501, userId: UID, label: '备份脚本', tokenHint: 'a1b2c3d4…', scopes: ['read'],
    createdAt: iso, lastUsedAt: hours(-2), expiresAt: hours(24 * 90), revokedAt: null, revoked: false },
]
let logStub = [
  { ts: iso, request_id: 'deadbeef', uid: UID, method: 'GET', path: '/api/workspace',
    status: 500, errcode: 'INTERNAL', duration_ms: 12.3 },
]
/** 集成配置状态桩：GET 返回它；configError 非 null 时该请求返回错误（验证降级） */
let configStub = null
let configError = null

function rowsFor(table) {
  return TABLES[table] || []
}

function jsonResponse(payload, status = 200) {
  return { ok: status >= 200 && status < 300, status, text: async () => JSON.stringify(payload) }
}

const ROUTES = {
  'GET /api/health': () => ({ ok: true, app: 'GTD4Teams', version: 'boottest',
    storage: 'sqlite', local: true, llmConfigured: false }),
  'GET /api/auth/session': () => ({ user: SESSION_USER }),
  'GET /api/auth/profile': () => ({ user: SESSION_USER, ...SESSION_USER }),
  'POST /api/auth/login': () => ({ user: SESSION_USER }),
  'POST /api/auth/logout': () => ({ ok: true }),
  'POST /api/auth/password': () => {
    // 改密成功后服务端把 must_change_password 清 0 —— 会话随即可用
    SESSION_USER = { ...SESSION_USER, mustChangePassword: false }
    return { ok: true }
  },
  'GET /api/workspace': () => ({
    teams: rowsFor('teams'), members: rowsFor('team_members'), items: rowsFor('work_items'),
    projects: rowsFor('projects'), areas: rowsFor('areas'), contexts: rowsFor('contexts'),
    resources: rowsFor('resources'), reviews: rowsFor('reviews'), onCall: rowsFor('on_call'),
    logs: rowsFor('progress_logs'),
  }),
  'POST /api/rows': (body) => {
    const { table, op, id, payload } = body
    writes.push({ table, op, id, payload })
    if (op === 'insert') return { rows: [{ id: 9001, ...payload }] }
    if (op === 'update') return { rows: [{ id, ...payload }] }
    return { rows: [{ id }] }
  },
  'POST /api/teams/create': (body) => ({ team: { id: 9001, owner_id: UID, ...body } }),
  'POST /api/teams/join': () => ({ teamId: TEAM }),
  'POST /api/reviews/save': (body) => ({ rows: [{ id: 9500, ...(body.payload || {}) }] }),
  'POST /api/logs': (body) => { logRows.push(body); return { rows: [{ id: 1 }] } },

  /* ---- 证书管理（契约：主理人下发）：GET 读状态，POST/DELETE 改状态 ---- */
  'GET /api/admin/cert': () => certStub,
  'POST /api/admin/cert': (body) => {
    certUploads.push(body)
    certStub = {
      configured: true, source: 'uploaded', subject: 'CN=new.example.com', issuer: 'CN=new.example.com',
      sans: ['DNS:new.example.com'], notBefore: iso, notAfter: hours(24 * 90), daysRemaining: 90,
      fingerprintSha256: 'AA:BB:CC:DD', selfSigned: true, uploadedAt: iso, uploadedBy: 'lpnl@nnit.com',
      pendingApply: false,   // 故意给 false：让测试不必等 30 秒轮询
    }
    return certStub
  },
  /* ---- 集成域令牌与日志（REQ-055）---- */
  'GET /api/admin/api-tokens': () => ({ tokens: tokenStub, total: tokenStub.length }),
  'GET /api/admin/config': () => configStub,
  'POST /api/admin/api-tokens': (body) => {
    tokenCreates.push(body)
    const id = 600 + tokenSeq++
    const row = { id, userId: UID, label: String(body.label || ''), tokenHint: INT_TOKEN.slice(0, 8) + '…',
      scopes: Array.isArray(body.scopes) ? body.scopes : [], createdAt: iso, lastUsedAt: null,
      expiresAt: hours(24 * Number(body.expiresInDays || 90)), revokedAt: null, revoked: false }
    tokenStub = [row, ...tokenStub]
    return { token: INT_TOKEN, id, tokenHint: row.tokenHint, userId: UID, label: row.label,
      scopes: row.scopes, expiresAt: row.expiresAt, warning: '令牌只显示这一次，请立即复制保存；关闭后无法再次查看。' }
  },
  'GET /api/logs': () => ({ logs: logStub, total: logStub.length, truncated: false,
    fileLogging: true, degradeReason: null }),
  'DELETE /api/admin/cert': () => {
    certStub = { configured: false, source: 'none', pendingApply: false }
    return certStub
  },
}

globalThis.fetch = async (url, options = {}) => {
  const method = (options.method || 'GET').toUpperCase()
  const path = String(url).split('?')[0]
  const key = `${method} ${path}`
  calls.push(key)
  const body = options.body ? JSON.parse(options.body) : {}
  requests.push({ key, body })

  // —— 账号体系变更（CR-2026-001）的两个特殊桩，必须在通用路由之前 ——
  // 建号：201 + 一次性明文口令（`06` §10.3.2）
  if (key === 'POST /api/admin/accounts') {
    if (accountError) return jsonResponse({ error: accountError }, accountError.status || 400)
    return jsonResponse({
      user: { id: 'u_new0001', email: body.email, name: body.name || null,
        isSystemAdmin: false, mustChangePassword: true },
      teamId: body.teamId ?? null,
      role: body.role ?? null,
      password: TEMP_PASSWORD,
    }, 201)
  }
  // 强制改密闸门：白名单之外一律 403（`06` §10.5.1）
  if (gateForbidden && key !== 'GET /api/auth/session' && key !== 'POST /api/auth/password'
    && key !== 'POST /api/auth/logout') {
    return jsonResponse({ error: { code: 'PASSWORD_CHANGE_REQUIRED',
      message: '初始密码尚未修改，请先设置新密码后再继续（当前操作已锁定）' } }, 403)
  }
  // 证书上传的失败桩：用来验证"提交失败后私钥必须清空、证书保留"
  if (key === 'POST /api/admin/cert' && certPostError) {
    certUploads.push(body)
    return jsonResponse({ error: certPostError }, certPostError.status || 400)
  }
  // 集成配置状态失败桩：非 null 时 GET /api/admin/config 返回该错误（验证降级）
  if (key === 'GET /api/admin/config' && configError) {
    return jsonResponse({ error: configError }, configError.status || 500)
  }

  // 令牌 id 是路径参数，不能用精确 ROUTES key 命中
  if (key.startsWith('DELETE /api/admin/api-tokens/')) {
    const id = Number(key.split('/').pop())
    tokenRevokes.push(id)
    tokenStub = tokenStub.map((t) => (t.id === id ? { ...t, revoked: true, revokedAt: iso } : t))
    return jsonResponse({ id, revoked: true })
  }
  if (key.startsWith('POST /api/admin/api-tokens/') && key.endsWith('/rotate')) {
    const id = Number(key.split('/').slice(-2)[0])
    tokenRotates.push(id)
    const old = tokenStub.find((t) => t.id === id) || {}
    const newId = 700 + tokenSeq++
    const row = { ...old, id: newId, tokenHint: INT_ROTATED_TOKEN.slice(0, 8) + '…',
      lastUsedAt: null, createdAt: iso, revoked: false, revokedAt: null }
    tokenStub = [row, ...tokenStub.filter((t) => t.id !== id)]
    return jsonResponse({ token: INT_ROTATED_TOKEN, id: newId, tokenHint: row.tokenHint,
      rotatedFrom: id, revokedImmediately: true })
  }
  if (key === 'GET /api/logs') logQueries.push(String(url))
  const handler = ROUTES[key]
  if (!handler) {
    return jsonResponse({ error: { code: 'NOT_FOUND', message: `${key} 未打桩` } }, 404)
  }
  if (rowsOverride && rowsOverride(key, body)) {
    // 请求确实发出去了（写入队列照记），但服务端返回空数组 —— 即"没改到"
    if (key === 'POST /api/rows') {
      writes.push({ table: body.table, op: body.op, id: body.id, payload: body.payload })
    }
    return jsonResponse({ rows: [] })
  }
  return jsonResponse(handler(body))
}

/* ---------- 测试基础设施 ---------- */
let pass = 0
let fail = 0
const errors = []
function assert(cond, name, detail = '') {
  if (cond) { pass++; console.log('  OK   ' + name) }
  else { fail++; console.log('  FAIL ' + name + (detail ? ' -> ' + detail : '')) }
}

/* 捕获未处理异常与 Promise 拒绝，任何一条都视为集成失败 */
process.on('unhandledRejection', (r) => errors.push('unhandledRejection: ' + (r?.stack || r)))

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const $ = (sel) => document.querySelector(sel)
const text = (sel) => ($(sel) ? $(sel).textContent.trim() : '')

/** 等待条件成立 */
async function until(fn, label, ms = 4000) {
  const deadline = Date.now() + ms
  while (Date.now() < deadline) {
    if (fn()) return true
    await sleep(20)
  }
  console.log(`  TIMEOUT 等待超时: ${label}`)
  return false
}

function click(el) {
  if (!el) throw new Error('click: 目标不存在')
  el.dispatchEvent(new Event('click', { bubbles: true, cancelable: true }))
}

/** 构造带自定义属性的键盘事件（linkedom 的 Event 构造器不接收 key 等属性） */
function keyEvent(type, key, opts = {}) {
  const ev = new Event(type, { bubbles: true, cancelable: true })
  Object.defineProperty(ev, 'key', { value: key, configurable: true })
  Object.assign(ev, opts)
  return ev
}

/* ================= 用例 ================= */

console.log('\n[1] 启动前：index.html 必须提供 app.js 依赖的全部挂载点')
{
  const required = ['auth-root', 'app-root', 'space-tabs', 'team-switch', 'sidebar', 'main',
    'quick-capture', 'quick-submit', 'global-search', 'user-btn', 'user-avatar', 'user-name', 'user-email',
    'user-panel', 'drawer-root', 'modal-root', 'toast-root']
  const missing = required.filter((id) => !document.getElementById(id))
  assert(missing.length === 0, `${required.length} 个挂载点齐备`, '缺: ' + missing.join(','))

  // 零外部依赖：不得再有 CDN 脚本，否则"随处可运行"就不成立
  const external = [...html.matchAll(/<script[^>]+src="([^"]+)"/g)]
    .map((m) => m[1]).filter((src) => /^(https?:)?\/\//.test(src))
  assert(external.length === 0, 'index.html 不引用任何外部脚本', external.join(','))
  assert(!/workbuddy-cloud-sdk/.test(html), '已彻底移除云服务 SDK 的引用')
}

console.log('\n[2] 启动：包装 SDK 存在时进入应用外壳并完成首屏（含数据加载）')
{
  await import(pathToFileURL(path.join(root, 'js/app.js')).href + '?boottest=1')
  // 注意：首次 render() 在数据到达前就会给出"正在同步"占位，
  // 因此必须等到数据真正落地（团队选择器出现）才算启动完成。
  const ok = await until(() => {
    const app = $('#app-root')
    return app && !app.hasAttribute('hidden')
      && !!$('#team-select')
      && !/正在同步/.test($('#main').innerHTML)
  }, '应用外壳与数据加载完成')
  assert(ok, '会话有效时自动进入应用外壳并完成首屏（数据已落地）')
  assert(!$('#auth-root') || $('#auth-root').hasAttribute('hidden'), '认证界面已隐藏')
  assert(!/加载失败/.test($('#main').innerHTML), '工作区加载未失败')
}

console.log('\n[2b] 登录者身份：顶栏与左下角必须显示真实用户名（不得退化占位文案）')
{
  // 会话里没有 email，只能靠 auth.getUser()；这里验证整条链路真的接上了
  const okName = await until(() => text('#user-name').length > 0, '顶栏用户名渲染')
  assert(okName, '顶栏显示了用户名', `user-name="${text('#user-name')}"`)
  assert(text('#user-email') === 'lpnl@nnit.com', '顶栏显示了账号邮箱（来自 auth.getUser）', `user-email="${text('#user-email')}"`)

  const sidebar = $('#sidebar').innerHTML
  assert(!/已登录/.test(sidebar), '左下角不再出现占位文案"已登录"')
  assert(sidebar.includes('lpnl'), '左下角显示了登录用户名')
  assert(sidebar.includes('个人空间'), '左下角标明了当前所在空间')

  // 切到团队空间后，左下角必须换成团队名称（而不是"团队空间"四个空泛的字）
  click(document.querySelector('#space-tabs [data-space="team"]'))
  await sleep(30)
  const teamSidebar = $('#sidebar').innerHTML
  assert(teamSidebar.includes('lpnl'), '团队空间下左下角仍显示登录用户名')
  assert(teamSidebar.includes('基础设施运维组'), '团队空间下左下角显示了团队名称')
  click(document.querySelector('#space-tabs [data-space="personal"]'))
  await sleep(20)

  const panel = $('#user-panel')
  click($('#user-btn'))
  await sleep(20)
  assert(!panel.hasAttribute('hidden'), '账号面板可打开')
  assert(/lpnl@nnit.com/.test(panel.innerHTML), '账号面板含邮箱')
  assert(/基础设施运维组/.test(panel.innerHTML), '账号面板含当前团队')
  click($('#user-btn'))
  await sleep(20)
  assert(panel.hasAttribute('hidden'), '账号面板可关闭')
}

console.log('\n[3] 外壳：空间切换与团队选择器')
{
  const tabs = document.querySelectorAll('#space-tabs .spacetab')
  assert(tabs.length === 2, '渲染了个人/团队两个空间标签', 'count=' + tabs.length)
  const teamTab = document.querySelector('#space-tabs [data-space="team"]')
  assert(!!teamTab, '团队空间标签存在')

  const sel = $('#team-select')
  assert(!!sel, '团队选择器已渲染（说明 activeTeamId 生效）')
  if (sel) {
    const opts = sel.querySelectorAll('option')
    assert(opts.length >= 1, '团队选择器含至少一个团队', 'count=' + opts.length)
  }
}

console.log('\n[4] 个人空间导航：点击进入各视图不报错')
{
  click(document.querySelector('#space-tabs [data-space="personal"]'))
  await sleep(20)
  const personalViews = ['inbox', 'clarify', 'focus', 'next', 'waiting', 'calendar', 'someday', 'assigned', 'myProjects', 'contexts', 'review', 'done']
  let rendered = 0
  let sawPersonal = false
  for (const v of personalViews) {
    const btn = document.querySelector(`#sidebar [data-action="nav"][data-view="${v}"]`)
    if (!btn) { continue }
    click(btn)
    await sleep(10)
    const main = $('#main').innerHTML
    if (main.length > 100 && !/undefined|NaN|\[object Object\]/.test(main)) rendered++
    else console.log(`    BAD 视图 ${v} 渲染异常`)
    if (/凤凰项目|Ansible/.test(main)) sawPersonal = true
  }
  assert(rendered === personalViews.length, `个人空间 ${personalViews.length} 个视图全部渲染正常`, 'ok=' + rendered)
  assert(sawPersonal, '个人视图确实渲染了真实数据（我的事项标题）')
}

console.log('\n[5] 团队空间导航：点击进入各视图不报错，且用到真实团队数据')
{
  click(document.querySelector('#space-tabs [data-space="team"]'))
  await sleep(30)
  const teamViews = ['overview', 'routine', 'desk', 'board', 'projects', 'oncall', 'resources', 'tools', 'reports', 'members', 'teamReview', 'teamSettings']
  let rendered = 0
  let sawTeamName = false
  for (const v of teamViews) {
    const btn = document.querySelector(`#sidebar [data-action="nav"][data-view="${v}"]`)
    if (!btn) { continue }
    click(btn)
    await sleep(10)
    const main = $('#main').innerHTML
    if (main.length > 100 && !/undefined|NaN|\[object Object\]/.test(main)) rendered++
    else console.log(`    BAD 团队视图 ${v} 渲染异常`)
    if (/基础设施运维组|核心交换机端口告警|每日机房巡检/.test(main)) sawTeamName = true
  }
  assert(rendered === teamViews.length, `团队空间 ${teamViews.length} 个视图全部渲染正常`, 'ok=' + rendered)
  assert(sawTeamName, '团队视图确实渲染了真实数据（团队名 / 工作项标题）')
}

console.log('\n[5b] REQ-039：例行工作筛选「周期巡检」后切到服务台，不得显示假空队列')
{
  click(document.querySelector('#space-tabs [data-space="team"]'))
  await sleep(20)
  click(document.querySelector('#sidebar [data-action="nav"][data-view="routine"]'))
  await sleep(20)
  const insTab = document.querySelector('#main [data-action="filter"][data-key="itemType"][data-value="inspection"]')
  assert(!!insTab, '例行工作页存在「周期巡检」筛选按钮')
  if (insTab) {
    click(insTab)
    await sleep(20)
    assert(/每日机房巡检/.test($('#main').innerHTML), '筛选后例行工作页确实显示巡检事项')

    // 复现缺陷的触发点：只切视图，不清筛选
    click(document.querySelector('#sidebar [data-action="nav"][data-view="desk"]'))
    await sleep(20)
    const deskHtml = $('#main').innerHTML
    assert(!/队列已清空/.test(deskHtml), '切到服务台后不得沿用 itemType=inspection 显示空队列（REQ-039）',
      (deskHtml.match(/队列已清空[^<]*/) || [''])[0])
    assert(/核心交换机端口告警/.test(deskHtml), '服务台应显示事件类事项（跨视图筛选已清空）')

    // 复位筛选：无论红灯绿灯都不污染后续用例
    const allTab = document.querySelector('#main [data-action="filter"][data-key="itemType"][data-value="all"]')
    if (allTab) { click(allTab); await sleep(20) }
  }
}

console.log('\n[6] 全局搜索：输入后切换到 search 视图并命中真实事项')
{
  const input = $('#global-search')
  input.value = '巡检'
  input.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }))
  const ok = await until(() => /搜索「巡检」/.test($('#main').innerHTML), '搜索视图渲染')
  assert(ok, '输入关键字后进入搜索视图（renderSearchHtml 已接入 render 分发）')
  const main = $('#main').innerHTML
  assert(!/undefined/.test(main), '搜索结果无 undefined 泄漏')
  assert(/每日机房巡检/.test(main), '命中了真实的工作项标题', '')
  assert(!/核心交换机端口告警/.test(main), '未命中标题的事项没有被错误包含')
  // 清空恢复
  input.value = ''
  input.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }))
  const restored = await until(() => !/搜索「/.test($('#main').innerHTML), '清空后恢复原视图')
  assert(restored, '清空搜索框后恢复到原视图')
}

console.log('\n[7] 快速收集：写入 work_items 且不携带 owner_id')
{
  writes.length = 0
  const cap = $('#quick-capture')
  cap.value = '给机房门禁换电池'
  cap.dispatchEvent(keyEvent('keydown', 'Enter'))
  const ok = await until(() => writes.some((w) => w.table === 'work_items'), '快速收集写入')
  assert(ok, '回车后写入了 work_items')
  const w = writes.find((x) => x.table === 'work_items' && x.op === 'insert')
  assert(!!w, '存在一条 work_items insert')
  assert(w && w.payload && w.payload.title === '给机房门禁换电池', '标题正确落库', JSON.stringify(w?.payload?.title))
  assert(w && !('owner_id' in w.payload), '写入不携带 owner_id（归属由服务端注入）', JSON.stringify(Object.keys(w?.payload || {})))
  cap.value = ''
}

console.log('\n[8] 打开详情抽屉：渲染并与挂载点对齐')
{
  const drawer = $('#drawer-root')
  // 切到团队看板，那里一定有可点击的卡片
  click(document.querySelector('#sidebar [data-action="nav"][data-view="board"]'))
  await sleep(30)
  const row = document.querySelector('#main [data-action="open-item"]')
  assert(!!row, '看板视图中存在可打开的事项卡片')
  if (row) {
    const id = row.getAttribute('data-id')
    click(row)
    const ok = await until(() => !drawer.hasAttribute('hidden') && drawer.innerHTML.length > 200, '抽屉渲染')
    assert(ok, `点击事项卡片打开了详情抽屉（item ${id}）`)
    assert(/<div/.test(drawer.innerHTML), '抽屉内容是 HTML 而非文本')
    assert(/核心交换机端口告警|每日机房巡检|新同事账号开通/.test(drawer.innerHTML), '抽屉渲染的是被点击事项的真实内容')
    const closeBtn = drawer.querySelector('[data-action="close-drawer"]') || drawer.querySelector('.icon-btn')
    assert(!!closeBtn, '抽屉有关闭按钮')
    if (closeBtn) {
      click(closeBtn)
      // 关闭带 180ms 退场动画，要等它真正隐藏
      const closed = await until(() => drawer.hasAttribute('hidden'), '抽屉关闭')
      assert(closed, '抽屉可关闭')
    }
  }
}

console.log('\n[9] 团队邀请面板：所有者可打开并看到邀请码')
{
  click(document.querySelector('#space-tabs [data-space="team"]'))
  await sleep(20)
  click(document.querySelector('#sidebar [data-action="nav"][data-view="members"]'))
  await sleep(20)
  const inviteBtn = document.querySelector('[data-action="invite-member"]')
  assert(!!inviteBtn, '成员页存在「邀请成员」入口（替代原先会污染归属的按邮箱添加）')
  if (inviteBtn) {
    click(inviteBtn)
    await sleep(20)
    const modal = $('#modal-root')
    assert(!modal.hasAttribute('hidden') && /邀请码/.test(modal.innerHTML), '邀请面板已打开且含邀请码说明')
    assert(/OPS-4F2K/.test(modal.innerHTML), '展示了当前团队的邀请码', '')
    const codeField = modal.querySelector('#invite-code')
    assert(!!codeField && codeField.getAttribute('value') === 'OPS-4F2K', '邀请码字段值正确')
    click(modal.querySelector('[data-action="close-modal"]'))
    const closed = await until(() => modal.hasAttribute('hidden'), '邀请面板关闭')
    assert(closed, '邀请面板可关闭')
  }
}

console.log('\n[10] 修改密码：面板必须真的渲染出表单，并提交到 /api/auth/password')
{
  const panel = $('#user-panel')
  // 面板内容在每次打开时重建，必须重新打开才能拿到最新 innerHTML
  if (panel.hasAttribute('hidden')) click($('#user-btn'))
  await sleep(30)
  const trigger = panel.querySelector('[data-action="change-password"]')
  assert(!!trigger, '账号面板中存在「修改密码」入口')
  if (trigger) click(trigger)
  await sleep(30)

  const form = $('#pwd-form')
  assert(!!form, '点击「修改密码」后出现了 #pwd-form（修复前该元素从未被渲染，点击静默无反应）')
  assert(!!form && !form.hasAttribute('hidden'), '#pwd-form 已展开（不再 hidden）')
  const oldEl = $('#pwd-old')
  const newEl = $('#pwd-new')
  const confirmEl = $('#pwd-confirm')
  assert(!!oldEl && oldEl.getAttribute('type') === 'password', '#pwd-old 是 password 输入框')
  assert(!!newEl && newEl.getAttribute('type') === 'password', '#pwd-new 是 password 输入框')
  assert(!!oldEl && oldEl.getAttribute('autocomplete') === 'current-password', '#pwd-old 带 autocomplete=current-password')
  assert(!!newEl && newEl.getAttribute('autocomplete') === 'new-password', '#pwd-new 带 autocomplete=new-password')
  assert(!!confirmEl && confirmEl.getAttribute('autocomplete') === 'new-password', '#pwd-confirm 带 autocomplete=new-password')
  const submit = panel.querySelector('[data-action="submit-password"]')
  assert(!!submit, '存在 data-action="submit-password" 的提交按钮')
  assert(!!panel.querySelector('label[for="pwd-old"], #pwd-old ~ *') || /当前密码/.test(panel.innerHTML), '#pwd-form 有可见的字段说明（不是裸输入框）')

  if (oldEl && newEl && confirmEl && submit) {
    // 口令规则必须与后端 security.py 对齐：MIN=8 / MAX=200
    const pwdCalls = () => requests.filter((r) => r.key === 'POST /api/auth/password').length
    const before = pwdCalls()

    oldEl.value = 'old-pass-123'
    newEl.value = '1234567'                // 7 位：低于下限
    click(submit)
    await sleep(60)
    assert(pwdCalls() === before, '新密码不足 8 位时前端拦下，不发请求', `calls=${pwdCalls() - before}`)

    newEl.value = 'x'.repeat(201)          // 201 位：超过上限
    click(submit)
    await sleep(60)
    assert(pwdCalls() === before, '新密码超过 200 位时前端拦下，不发请求', `calls=${pwdCalls() - before}`)

    // FDS-15 / N-06：两次输入的新密码不一致 —— 前端拦下，不发请求，就地给出提示
    newEl.value = 'new-pass-456'
    confirmEl.value = 'new-pass-789'   // 与 new 不一致
    click(submit)
    await sleep(60)
    assert(pwdCalls() === before, '两次输入的新密码不一致时前端拦下，不发请求', `calls=${pwdCalls() - before}`)
    assert(text('#pwd-error') === '两次输入的新密码不一致', '不一致时就地展示与闸门页完全一致的提示文案', text('#pwd-error'))

    // 两次一致：正常提交
    newEl.value = 'new-pass-456'
    confirmEl.value = 'new-pass-456'
    click(submit)
    const hit = await until(() => pwdCalls() > before, '修改密码请求发出')
    assert(hit, '合法输入提交后发出了 POST /api/auth/password')
    const req = requests.filter((r) => r.key === 'POST /api/auth/password').pop()
    assert(!!req && req.body?.oldPassword === 'old-pass-123' && req.body?.newPassword === 'new-pass-456',
      '请求体形状为 { oldPassword, newPassword }', JSON.stringify(req?.body))
    const cleared = await until(() => ($('#pwd-old')?.value || '') === '' && ($('#pwd-new')?.value || '') === '' && ($('#pwd-confirm')?.value || '') === '', '输入框清空')
    assert(cleared, '成功后清空了三个输入框（不把口令留在 DOM 里）')
    const collapsed = await until(() => !$('#pwd-form') || $('#pwd-form').hasAttribute('hidden'), '表单收起')
    assert(collapsed, '成功后收起了修改密码表单')
  }
}

console.log('\n[11] 写操作防重复提交：连点两次「创建」只应写入一次')
{
  click(document.querySelector('#space-tabs [data-space="personal"]'))
  await sleep(30)
  click(document.querySelector('#sidebar [data-action="nav"][data-view="inbox"]'))
  await sleep(30)
  const openBtn = document.querySelector('#main [data-action="new-item"]')
  assert(!!openBtn, '收集箱存在新建入口')
  if (openBtn) {
    click(openBtn)
    await sleep(30)
    const modal = $('#modal-root')
    assert(!modal.hasAttribute('hidden'), '新建表单已打开')
    const titleEl = $('#q-title')
    const saveBtn = modal.querySelector('[data-action="save-new-item"]')
    assert(!!titleEl && !!saveBtn, '新建表单含标题输入框与保存按钮')
    if (titleEl && saveBtn) {
      titleEl.value = '连点两次只应写一次'
      writes.length = 0
      // 同一 tick 内连点两次：修复前没有任何重入锁，会发出两次 insert
      click(saveBtn)
      click(saveBtn)
      await until(() => writes.some((w) => w.table === 'work_items' && w.op === 'insert'), '新建写入')
      await sleep(150)   // 给（可能存在的）第二个请求留出落地时间
      const inserts = writes.filter((w) => w.table === 'work_items' && w.op === 'insert')
      assert(inserts.length === 1, '连点两次只发出一次 insert（防重复提交生效）', `inserts=${inserts.length}`)
      const closed = await until(() => $('#modal-root').hasAttribute('hidden'), '新建表单关闭')
      assert(closed, '写入成功后浮层关闭')
    }
  }
}

console.log('\n[12] 写结果如实提示：事项更新被拦下时不得提示「进度已写入」')
{
  click(document.querySelector('#space-tabs [data-space="team"]'))
  await sleep(30)
  click(document.querySelector('#sidebar [data-action="nav"][data-view="board"]'))
  await sleep(30)
  const cardEl = document.querySelector('#main [data-action="open-item"]')
  assert(!!cardEl, '看板中存在可打开的事项卡片')
  if (cardEl) {
    click(cardEl)
    const opened = await until(() => !$('#drawer-root').hasAttribute('hidden') && !!$('#p-note'), '详情抽屉与进度表单打开')
    assert(opened, '详情抽屉打开且含进度录入表单')
    const noteEl = $('#p-note')
    const addBtn = $('#drawer-root [data-action="add-progress"]')
    assert(!!noteEl && !!addBtn, '进度表单含说明输入框与「写入记录」按钮')
    if (noteEl && addBtn) {
      noteEl.value = '已定位到存储多路径配置问题'
      // 让事项（work_items.update）返回空数组：日志写进去了，事项状态没改到
      rowsOverride = (key, body) => key === 'POST /api/rows' && body.table === 'work_items' && body.op === 'update'
      writes.length = 0
      click(addBtn)
      await until(() => writes.some((w) => w.table === 'progress_logs'), '进度日志写入')
      await sleep(180)
      const toastText = $('#toast-root')?.textContent || ''
      assert(/进度已记录/.test(toastText), '如实提示"进度已记录，但事项状态未更新"', `toast="${toastText}"`)
      assert(!/进度已写入/.test(toastText), '未出现"进度已写入"这种假装成功的提示', `toast="${toastText}"`)
      rowsOverride = null
    }
  }
}

console.log('\n[13] 建号：一次性初始口令只出现这一次')
{
  click(document.querySelector('#space-tabs [data-space="team"]'))
  await sleep(20)
  click(document.querySelector('#sidebar [data-action="nav"][data-view="members"]'))
  await sleep(30)

  const memberBtn = document.querySelector('#main [data-action="new-member-account"]')
  assert(!!memberBtn, '成员页存在「创建成员账号」入口（团队管理员）')
  const adminBtn = document.querySelector('#main [data-action="new-admin-account"]')
  assert(!!adminBtn, '成员页存在「创建团队管理员」入口（系统管理员才可见）')

  const modal = $('#modal-root')
  if (!memberBtn) {
    console.log('    SKIP 后续建号断言（入口不存在）')
  } else {
    click(memberBtn)
    await sleep(30)
    assert(!modal.hasAttribute('hidden'), '建号表单已打开')
    assert(!modal.querySelector('#a-role'), '团队管理员入口不渲染角色选择器（只能创建成员）')
    assert(/基础设施运维组/.test(modal.innerHTML), '团队锁定为当前团队')

    const emailEl = modal.querySelector('#a-email')
    const nameEl = modal.querySelector('#a-name')
    assert(!!emailEl && !!nameEl, '建号表单含邮箱与显示名')
    emailEl.value = 'newbie@example.com'
    nameEl.value = '新同事'
    logRows.length = 0
    click(modal.querySelector('[data-action="save-account"]'))

    const sent = await until(() => requests.some((r) => r.key === 'POST /api/admin/accounts'), '建号请求发出')
    assert(sent, '提交后发出 POST /api/admin/accounts')
    const req = requests.filter((r) => r.key === 'POST /api/admin/accounts').pop()
    assert(req.body.email === 'newbie@example.com', 'body 携带邮箱', JSON.stringify(req?.body))
    assert(Number(req.body.teamId) === TEAM, 'body 携带本团队 teamId（团队管理员不能跨团队）', String(req?.body?.teamId))
    assert(req.body.role === 'member', 'team 管理员建号的角色恒为 member', String(req?.body?.role))
    assert(!('isSystemAdmin' in req.body) && !('password' in req.body) && !('passwordHash' in req.body),
      'body 不携带 isSystemAdmin / password（服务端恒 0 + 服务端生成口令）', JSON.stringify(Object.keys(req?.body || {})))

    // —— 一次性口令展示 ——
    const ok = await until(() => !!modal.querySelector('#a-password'), '一次性口令展示')
    assert(ok, '建号成功后展示了初始口令')
    const pwdEl = modal.querySelector('#a-password')
    assert(!!pwdEl && pwdEl.value === TEMP_PASSWORD, '展示的就是服务端返回的口令', pwdEl?.value)
    assert(/只显示一次/.test(modal.innerHTML) && /关闭后无法再次查看/.test(modal.innerHTML),
      '明确写了"只显示一次、关闭后无法再次查看"')
    assert(!modal.querySelector('#a-role'), '成功页不再有可编辑的角色选择器')
    assert(!!modal.querySelector('[data-action="copy-temp-password"]'), '提供「复制」按钮（复用剪贴板处理）')
    clipboard.length = 0
    click(modal.querySelector('[data-action="copy-temp-password"]'))
    await sleep(30)
    assert(clipboard.length === 1 && clipboard[0] === TEMP_PASSWORD,
      '「复制」把一次性口令写进剪贴板（用户可当场转交）', JSON.stringify(clipboard))

    // —— 口令不得落在任何持久化位置 ——
    const lsDump = JSON.stringify([...lsData.entries()])
    assert(!lsDump.includes(TEMP_PASSWORD), '一次性口令没有写进 localStorage', lsDump.slice(0, 120))
    await sleep(200)   // 等日志缓冲刷写（内部 1.2s 定时，这里只做一次快照）
    assert(!JSON.stringify(logRows).includes(TEMP_PASSWORD), '一次性口令没有写进任何一条运行日志')
    assert(!calls.some((c) => String(c).includes(TEMP_PASSWORD)), '一次性口令没有出现在请求 URL 里（只能走响应体）')

    // —— 关闭后不可再查看 ——
    click(modal.querySelector('[data-action="close-modal"]'))
    const closed = await until(() => modal.hasAttribute('hidden'), '建号成功页关闭')
    assert(closed, '建号成功页可关闭')
    assert(!String(modal.innerHTML).includes(TEMP_PASSWORD), '关闭后口令从 DOM 中消失（无法再次查看）')
  }
}

console.log('\n[14] 建号失败：如实给出错误与出路，不假装成功')
{
  const modal = $('#modal-root')
  click(document.querySelector('#sidebar [data-action="nav"][data-view="members"]'))
  await sleep(20)
  const adminBtn = document.querySelector('#main [data-action="new-admin-account"]')
  assert(!!adminBtn, '系统管理员入口可见')
  if (adminBtn) {
    click(adminBtn)
    await sleep(30)
    assert(!modal.querySelector('#a-role'), '系统管理员入口没有角色选择器（只能建 admin，`06` v1.3）')
    assert(!!modal.querySelector('#a-team'), '系统管理员入口允许选择团队')
    assert(!/value="member"/.test(modal.innerHTML), '系统管理员入口不出现「成员」选项')
    assert(/角色由/.test(modal.innerHTML), '说明了角色由"是否选团队"决定')

    const emailEl = modal.querySelector('#a-email')
    emailEl.value = 'taken@example.com'
    accountError = { status: 409, code: 'EMAIL_EXISTS', message: '该邮箱已注册，请改用邀请码把该账号加入团队' }
    click(modal.querySelector('[data-action="save-account"]'))

    const shown = await until(() => {
      const err = modal.querySelector('#a-error')
      return err && !err.hasAttribute('hidden') && err.textContent.trim().length > 0
    }, 'EMAIL_EXISTS 内联提示')
    assert(shown, 'EMAIL_EXISTS 在表单内联提示（不是一闪而过的 toast）')
    const errText = modal.querySelector('#a-error')?.textContent || ''
    assert(/已有账号/.test(errText), '说明该邮箱已有账号', errText)
    assert(/邀请码/.test(errText) && /对方登录/.test(errText),
      '出路指向"把邀请码发给对方、由对方自行加入"（管理员不能替人入队）', errText)

    const req1 = requests.filter((r) => r.key === 'POST /api/admin/accounts').pop()
    assert(Number(req1?.body?.teamId) === TEAM && req1?.body?.role === 'admin',
      '选了团队 → role=admin（不是 member）', JSON.stringify(req1?.body))
    assert(!modal.querySelector('#a-password'), '失败时不会展示任何口令')
    assert(!modal.hasAttribute('hidden'), '失败后表单保留，用户可以直接改完重试')

    // role 与 teamId 必须同现同缺：清空团队后 role 必须是 null（传 member 会稳定 400）
    accountError = { status: 403, code: 'NOT_TEAM_ADMIN', message: '你不是该团队的管理员' }
    // linkedom 的 <select> 只有 value getter，没有 setter：改选项的 selected 才是可移植写法
    for (const opt of modal.querySelectorAll('#a-team option')) {
      opt.selected = opt.getAttribute('value') === ''
    }
    click(modal.querySelector('[data-action="save-account"]'))
    const shown2 = await until(() => /不是该团队的管理员/.test(modal.querySelector('#a-error')?.textContent || ''),
      'NOT_TEAM_ADMIN 内联提示')
    assert(shown2, 'NOT_TEAM_ADMIN 如实展示服务端文案', modal.querySelector('#a-error')?.textContent)
    const req2 = requests.filter((r) => r.key === 'POST /api/admin/accounts').pop()
    assert(req2?.body?.teamId === null && req2?.body?.role === null,
      '不选团队 → teamId 与 role 同时为 null', JSON.stringify(req2?.body))

    // 引导态（NO_SYSTEM_ADMIN）：这不是"你没权限"，重试永远不会成功 → 灰底指引 + 禁掉提交
    accountError = { status: 403, code: 'NO_SYSTEM_ADMIN',
      message: '本实例还没有系统管理员。请先在服务器执行 python server.py --create-admin <邮箱> 完成引导。' }
    click(modal.querySelector('[data-action="save-account"]'))
    const guided = await until(() => /create-admin/.test(modal.querySelector('#a-error')?.textContent || ''),
      'NO_SYSTEM_ADMIN 引导提示')
    assert(guided, 'NO_SYSTEM_ADMIN 展示引导文案（含 --create-admin）',
      modal.querySelector('#a-error')?.textContent)
    assert(/is-guide/.test(modal.querySelector('#a-error')?.getAttribute('class') || ''),
      '引导态用中性样式（不是红色权限报错）')
    assert(modal.querySelector('[data-action="save-account"]')?.hasAttribute('disabled'),
      '引导态禁掉提交按钮（重试不会成功）')

    accountError = null
    click(modal.querySelector('[data-action="close-modal"]'))
    await until(() => modal.hasAttribute('hidden'), '建号表单关闭')
  }
}

console.log('\n[15] 系统管理：证书状态读取、上传、移除')
{
  // 系统管理员的侧栏必须有实例级入口（与个人/团队空间并列）
  const sysBtn = document.querySelector('#sidebar [data-action="nav"][data-view="system"]')
  assert(!!sysBtn, '系统管理员的侧栏存在「系统管理」入口')
  if (sysBtn) {
    calls.length = 0
    click(sysBtn)
    const loaded = await until(() => /还没有配置证书/.test($('#main').innerHTML), '证书状态卡片渲染')
    assert(loaded, '进入系统管理页后渲染出证书状态（未配置）')
    assert(calls.includes('GET /api/admin/cert'), '进入页面即拉取 GET /api/admin/cert')
    assert(/实例级配置/.test($('#main').innerHTML), '副标题说明这是实例级配置（不是团队配置）')
    assert(!!document.getElementById('cert-crt-file'), '有证书文件选择框')
    assert(!!document.getElementById('cert-key-file'), '有私钥文件选择框')

    // 直接粘贴模式的切换
    const paste = document.getElementById('cert-paste')
    assert(!!paste && paste.hasAttribute('hidden'), '粘贴区默认收起（文件选择优先）')
    click(document.querySelector('[data-action="toggle-cert-paste"]'))
    await sleep(20)
    assert(!document.getElementById('cert-paste').hasAttribute('hidden'), '点击后展开粘贴区')

    // —— 前端轻量预校验：不合格不发请求 ——
    const certReqs = () => requests.filter((r) => r.key === 'POST /api/admin/cert').length
    const before = certReqs()
    click(document.querySelector('[data-action="upload-cert"]'))
    await sleep(40)
    assert(certReqs() === before, '空内容不发请求', `calls=${certReqs() - before}`)
    assert(!document.getElementById('cert-error').hasAttribute('hidden'), '空内容给出内联提示')

    const crt = document.getElementById('cert-crt-pem')
    const key = document.getElementById('cert-key-pem')
    crt.value = 'not a pem'
    key.value = 'not a key'
    click(document.querySelector('[data-action="upload-cert"]'))
    await sleep(40)
    assert(certReqs() === before, '缺少 BEGIN 标记时不发请求')
    assert(/BEGIN CERTIFICATE/.test(document.getElementById('cert-error').textContent),
      '提示缺少 -----BEGIN CERTIFICATE-----', document.getElementById('cert-error').textContent)

    // —— 合法 PEM：JSON 提交，不是 multipart ——
    const CERT_PEM = '-----BEGIN CERTIFICATE-----\nMIIBszCCAVmgAwIBAgIUQ\n-----END CERTIFICATE-----'
    const KEY_PEM = '-----BEGIN PRIVATE KEY-----\nMIGHAgEAMBMGByqGSM49\n-----END PRIVATE KEY-----'
    crt.value = CERT_PEM
    key.value = KEY_PEM
    certUploads.length = 0
    click(document.querySelector('[data-action="upload-cert"]'))
    const sent = await until(() => certReqs() > before, '上传请求发出')
    assert(sent, '合法 PEM 提交后发出 POST /api/admin/cert')
    assert(certUploads.length === 1, '只发了一次（写操作重入锁生效）', `n=${certUploads.length}`)
    const body = certUploads[0] || {}
    assert(Object.keys(body).sort().join(',') === 'certPem,keyPem', '请求体只有 { certPem, keyPem }',
      Object.keys(body).join(','))
    assert(body.certPem === CERT_PEM && body.keyPem === KEY_PEM, '提交的是 PEM 文本原文')

    const refreshed = await until(() => /已上传|运维自签名/.test($('#main').innerHTML), '上传后刷新状态')
    assert(refreshed, '上传成功后重新拉取并渲染证书状态')
    assert(/AA:BB:CC:DD/.test($('#main').innerHTML), '展示了服务端返回的 SHA256 指纹')
    assert(/自签名证书，浏览器会显示安全警告/.test($('#main').innerHTML), 'selfSigned=true 时提示安全警告')
    const keyAfter = document.getElementById('cert-key-pem')
    assert(!keyAfter || keyAfter.value === '', '提交成功后清空私钥输入框（不把私钥留在 DOM 里）')
    assert(!JSON.stringify(logRows).includes('MIGHAgEAMBMGByqGSM49'), '私钥没有进运行日志')
    assert(!calls.some((c) => String(c).includes('BEGIN PRIVATE KEY')), '私钥没有出现在请求 URL 里')

    // —— 移除证书：产品内模态二次确认（取消 / 确定两分支，FDS-16）——
    const delBtn = document.querySelector('[data-action="delete-cert"]')
    assert(!!delBtn, '已配置时提供「移除证书」')
    calls.length = 0

    // 取消分支：点移除弹模态，点「取消」不发 DELETE，证书仍在
    click(delBtn)
    const dlgShown = await until(() => { const m = $('#modal-root'); return m && !m.hasAttribute('hidden') && !!m.querySelector('[data-action="dialog-confirm"]') }, '移除证书弹出产品内确认模态')
    assert(dlgShown, '点移除弹出产品内确认模态（不再用原生 confirm）')
    assert(/移除当前证书/.test($('#modal-root').innerHTML), '模态展示危险操作说明')
    const dangerBtn = $('#modal-root [data-action="dialog-confirm"]')
    assert(/btn-danger/.test(dangerBtn?.className || ''), '危险操作确定按钮使用 btn-danger（统一 var(--danger)）', dangerBtn?.className)
    click($('#modal-root [data-action="dialog-cancel"]'))
    await sleep(80)
    assert(!calls.includes('DELETE /api/admin/cert'), '点取消时不发 DELETE 请求（取消不会照样执行）')
    assert(!!document.querySelector('[data-action="delete-cert"]'), '取消后证书仍在（未误删）')

    // 确定分支：再次点移除 → 模态点确定 → 真正删除
    click(document.querySelector('[data-action="delete-cert"]'))
    const dlgAgain = await until(() => !!$('#modal-root [data-action="dialog-confirm"]'), '再次弹出确认模态')
    assert(dlgAgain, '取消后可再次点移除并弹出确认模态')
    click($('#modal-root [data-action="dialog-confirm"]'))
    const removed = await until(() => /还没有配置证书/.test($('#main').innerHTML), '移除后回到未配置')
    assert(removed, '模态点确定后删除证书并刷新为"未配置"')
    assert(calls.includes('DELETE /api/admin/cert'), '走 DELETE /api/admin/cert')
    assert(!document.querySelector('[data-action="delete-cert"]'), '未配置时不再显示「移除证书」')
  }
}

console.log('\n[15b] 证书上传失败：私钥清空、证书保留（三个时机分开判）')
{
  const CERT_PEM = '-----BEGIN CERTIFICATE-----\nMIIBszCCAVmgAwIBAgIUQ\n-----END CERTIFICATE-----'
  const KEY_PEM = '-----BEGIN PRIVATE KEY-----\nMIGHAgEAMBMGByqGSM49\n-----END PRIVATE KEY-----'
  const clickToggle = () => click(document.querySelector('[data-action="toggle-cert-paste"]'))

  // [15] 结束时已移除证书并重渲染，粘贴区回到收起状态
  clickToggle()
  await sleep(20)

  // ① 前置校验失败（未提交）：两个框都**不清** —— 用户正在改的内容不能被清掉
  const crt = document.getElementById('cert-crt-pem')
  const key = document.getElementById('cert-key-pem')
  assert(!!crt && !!key, '粘贴区已展开')
  crt.value = CERT_PEM
  key.value = ''
  const before = requests.filter((r) => r.key === 'POST /api/admin/cert').length
  click(document.querySelector('[data-action="upload-cert"]'))
  await sleep(40)
  assert(requests.filter((r) => r.key === 'POST /api/admin/cert').length === before,
    '前置校验失败不发请求')
  assert(document.getElementById('cert-crt-pem').value === CERT_PEM,
    '前置校验失败时证书输入框保留（用户还没提交）')

  // ② 提交失败（已提交过）：私钥清、证书留
  key.value = KEY_PEM
  certPostError = { status: 400, code: 'CERT_KEY_MISMATCH', message: '证书与私钥不匹配，请确认两者是同一对' }
  click(document.querySelector('[data-action="upload-cert"]'))
  const failed = await until(() => /不匹配/.test(document.getElementById('cert-error')?.textContent || ''),
    'CERT_KEY_MISMATCH 提示')
  assert(failed, 'CERT_KEY_MISMATCH 如实展示服务端文案')
  assert(document.getElementById('cert-key-pem').value === '',
    '提交失败后私钥输入框已清空（服务端已收到过这份私钥，DOM 里不该再留）',
    String(document.getElementById('cert-key-pem').value).slice(0, 20))
  assert(document.getElementById('cert-crt-pem').value === CERT_PEM,
    '提交失败后证书保留（证书不是秘密，方便"只换私钥再试"）')
  assert(/提交失败后已清空/.test(document.getElementById('cert-key-name')?.textContent || ''),
    '私钥标签说"提交失败后已清空"，避免"显示已选、内容却空"',
    document.getElementById('cert-key-name')?.textContent)
  assert(!String(document.getElementById('cert-error')?.innerHTML || '').includes('MIGHAgEAMBMGByqGSM49'),
    '错误提示里没有回显私钥内容')

  // ③ 只为私钥重新粘贴即可重试成功（证书确实被保留下来了），成功后两个都清
  certPostError = null
  document.getElementById('cert-key-pem').value = KEY_PEM
  click(document.querySelector('[data-action="upload-cert"]'))
  const retried = await until(() => /已上传|运维自签名/.test($('#main').innerHTML), '重试成功')
  assert(retried, '只重填私钥即可重试成功（证明证书确实被保留）')
  assert((document.getElementById('cert-key-pem')?.value || '') === '', '成功后私钥输入框为空')
  assert((document.getElementById('cert-crt-pem')?.value || '') === '', '成功后证书输入框为空')
}

console.log('\n[15c] N-02：刷新证书状态时保住草稿；旧P3：成功/失败标签分开')
{
  // [15b] 最后一步是成功重试 → 私钥标签应说"提交成功后"，不能还是"提交失败后"
  // 成功路径随后 render() 会重建卡片、标签回到「未选择文件」——此处只验证
  // "不残留失败文案"；失败文案的正面断言在 [15b]（那条路径不重渲染，标签真的会显示）。
  assert(!/提交失败后已清空/.test($('#main').innerHTML), '成功路径不得在页面上残留失败文案')

  const paste = document.getElementById('cert-paste')
  if (paste?.hasAttribute('hidden')) { click(document.querySelector('[data-action="toggle-cert-paste"]')); await sleep(20) }
  const crt0 = document.getElementById('cert-crt-pem')
  const key0 = document.getElementById('cert-key-pem')
  assert(!!crt0 && !!key0, '刷新前粘贴区可用')
  if (crt0 && key0) {
    const DRAFT_CRT = '-----BEGIN CERTIFICATE-----\nDRAFT-CRT-LINE\n-----END CERTIFICATE-----'
    const DRAFT_KEY = '-----BEGIN PRIVATE KEY-----\nDRAFT-KEY-LINE\n-----END PRIVATE KEY-----'
    crt0.value = DRAFT_CRT
    key0.value = DRAFT_KEY
    const before = calls.filter((c) => c === 'GET /api/admin/cert').length
    click(document.querySelector('[data-action="load-cert"]'))
    await until(() => calls.filter((c) => c === 'GET /api/admin/cert').length > before, '刷新状态请求发出')
    await sleep(60)
    assert((document.getElementById('cert-crt-pem')?.value || '') === DRAFT_CRT,
      '刷新状态后证书草稿保留（N-02）')
    assert((document.getElementById('cert-key-pem')?.value || '') === DRAFT_KEY,
      '刷新状态后私钥草稿保留（N-02）')
  }
}

console.log('\n[15d] REQ-055：系统管理页集成域三区块可达（本地 API / MCP / 日志排障）')
{
  const mainHtml = () => $('#main').innerHTML
  calls.length = 0
  click(document.querySelector('#sidebar [data-action="nav"][data-view="system"]'))
  const ready = await until(() => /本地 API（供脚本\/集成调用，默认关闭）/.test(mainHtml()), '本地 API 区块渲染')
  assert(ready, '系统管理页出现「本地 API（供脚本/集成调用，默认关闭）」区块')
  const html = mainHtml()
  const tokenReq = await until(() => calls.includes('GET /api/admin/api-tokens'), '令牌列表请求发出')
  assert(tokenReq, '进入系统管理页即读取令牌列表')
  assert(/MCP 服务（供 AI 客户端调用，默认关闭且只读）/.test(html), 'MCP 区块标题说明用途与默认状态')
  assert(/运行日志排障（按报错编号查单条，不提供日志浏览）/.test(html), '日志区块只提供按编号查单条')
  assert(/GTD_API_ENABLED=1/.test(html) && /GTD_MCP_ENABLED=1/.test(html), '开关无写端点时给出 .env 指引')
  assert(!/data-action="toggle-integration"/.test(html), '不渲染后端不支持的假开关')
  assert(!/运行中/.test(html), '接口未回开关值时不显示假的「运行中」')
  assert(/备份脚本/.test(html) && /a1b2c3d4/.test(html), '令牌列表显示备注与 hash 前 8 位提示')
  assert(!html.includes(INT_TOKEN), '令牌列表不回显明文')
  assert(/本身没有写权限/.test(html), 'MCP 区块声明 mcp 本身无写权限')
  assert(/无 SSE/.test(html) && /无 batch/.test(html), 'MCP 区块声明不支持项')
}
console.log('\n[15d2] REQ-055：集成配置状态——真实值接入与失败降级（GET /api/admin/config）')
{
  const mainHtml = () => $('#main').innerHTML

  // —— 真实值：API 开 / MCP 关，端口用回传值 ——
  configStub = { apiEnabled: true, apiPort: 8791, mcpEnabled: false, mcpPort: 8723 }
  configError = null
  calls.length = 0
  click(document.querySelector('#sidebar [data-action="nav"][data-view="system"]'))
  const cfgReq = await until(() => calls.includes('GET /api/admin/config'), '配置状态请求发出')
  assert(cfgReq, '再次进入系统管理页时请求 GET /api/admin/config')
  assert(calls.includes('GET /api/admin/api-tokens'), '配置与令牌列表同一次读取中并行发出')
  const real = await until(() => /运行中/.test(mainHtml()) && /已停用/.test(mainHtml()), '真实开关状态渲染')
  assert(real, '配置回传后按真实值显示开关状态（API 运行中 / MCP 已停用）')
  const okHtml = mainHtml()
  assert(/<span class="int-fact-value">8791<\/span>/.test(okHtml), 'API 端口显示配置回传的真实端口')
  assert(okHtml.includes('127.0.0.1:8723/mcp'), 'MCP 连接地址使用配置回传的真实端口')
  assert(!/开关状态未知/.test(okHtml), '配置已知时不再显示「开关状态未知」')
  assert(!/当前接口未提供实时值/.test(okHtml), '配置已知时不再显示端口未知提示')
  assert(/备份脚本/.test(okHtml), '读取配置不影响令牌列表展示')

  // —— 失败：保持 null，视图降级为状态未知，令牌列表照常加载 ——
  configStub = null
  configError = { code: 'CONFIG_UNAVAILABLE', message: '配置读取失败' }
  calls.length = 0
  click(document.querySelector('#sidebar [data-action="nav"][data-view="system"]'))
  const failReq = await until(() => calls.includes('GET /api/admin/config'), '失败态配置请求发出')
  assert(failReq, '配置读取失败时请求照样发出')
  const degraded = await until(() => /开关状态未知/.test(mainHtml()), '降级为状态未知')
  assert(degraded, '配置读取失败后降级显示「开关状态未知」')
  const failHtml = mainHtml()
  assert(!/运行中/.test(failHtml) && !/已停用/.test(failHtml), '失败态不显示假的开关状态')
  assert(/当前接口未提供实时值/.test(failHtml), '失败态端口回到未提供实时值提示')
  assert(/备份脚本/.test(failHtml), '配置失败不中断令牌列表加载')
  assert(!/加载失败/.test(failHtml), '配置失败不把整页打成错误态')

  // 恢复默认，避免影响后续签发/轮换用例
  configError = null
  configStub = null
}


console.log('\n[15e] REQ-055：签发令牌——scope 契约、一次性明文、不入 state/localStorage/日志')
{
  $('#int-token-label').value = '夜间巡检脚本'
  for (const s of ['mcp', 'write:edit']) {
    const el = document.querySelector(`#main .int-issue input[data-scope="${s}"]`)
    assert(!!el, `签发表单含 ${s} scope 勾选项`)
    if (el) el.setAttribute('checked', '')
  }
  click(document.querySelector('[data-action="int-create-token"]'))
  const shown = await until(() => {
    const m = $('#modal-root')
    return m && !m.hasAttribute('hidden') && !!m.querySelector('#int-token-value')
  }, '一次性令牌卡出现')
  assert(shown, '签发成功后弹出一次性明文卡')
  const req = tokenCreates.at(-1) || {}
  assert(req.label === '夜间巡检脚本', '请求体 label 取表单值', JSON.stringify(req))
  assert(JSON.stringify(req.scopes) === JSON.stringify(['read', 'mcp', 'write:edit']),
    '请求体 scopes 与勾选一致（read 默认勾）', JSON.stringify(req.scopes))
  assert(req.expiresInDays === 90, '默认有效期 90 天', JSON.stringify(req))
  assert($('#int-token-value').value === INT_TOKEN, '一次性卡展示服务端返回的明文')
  assert(!$('#main').innerHTML.includes(INT_TOKEN), '明文不写回令牌列表')
  assert(!JSON.stringify([...lsData.values()]).includes(INT_TOKEN), '明文不写 localStorage')
  assert(!JSON.stringify(logRows).includes(INT_TOKEN), '明文不写运行日志')
  const n = clipboard.length
  click($('#modal-root [data-action="int-copy-token"]'))
  await until(() => clipboard.length > n, '明文复制完成')
  assert(clipboard.at(-1) === INT_TOKEN, '复制按钮交付的是明文令牌')
  click($('#modal-root [data-action="close-modal"]'))
  await until(() => $('#modal-root').hasAttribute('hidden'), '一次性卡关闭')
  await sleep(220)
  assert(!$('#modal-root').innerHTML.includes(INT_TOKEN), '关闭 180ms 后 DOM 中明文被摘除')
  assert(/夜间巡检脚本/.test($('#main').innerHTML), '签发后列表刷新出现新令牌')
}

console.log('\n[15f] REQ-055：轮换 / 撤销都要二次确认，且轮换明文也只展示一次')
{
  const target = tokenStub.find((t) => !t.revoked)
  assert(!!target, '存在可轮换的有效令牌')
  if (target) {
    click(document.querySelector(`[data-action="int-rotate-token"][data-id="${target.id}"]`))
    const dlg = await until(() => /轮换访问令牌？/.test($('#modal-root').innerHTML), '轮换确认模态')
    assert(dlg, '轮换弹出产品内二次确认')
    click($('#modal-root [data-action="dialog-confirm"]'))
    const shown = await until(() => /令牌已轮换/.test($('#modal-root').innerHTML), '轮换后明文卡')
    assert(shown, '轮换成功后展示新的明文令牌')
    assert(tokenRotates.at(-1) === target.id, '轮换请求带上正确的令牌 id', `id=${tokenRotates.at(-1)}`)
    assert($('#int-token-value').value === INT_ROTATED_TOKEN, '轮换明文来自响应且只展示一次')
    click($('#modal-root [data-action="close-modal"]'))
    await sleep(220)

    const before = tokenRevokes.length
    const revokeBtn = document.querySelector('[data-action="int-revoke-token"]:not([disabled])')
    assert(!!revokeBtn, '令牌列表提供撤销按钮')
    if (revokeBtn) {
      click(revokeBtn)
      const confirm = await until(() => /撤销访问令牌？/.test($('#modal-root').innerHTML), '撤销确认模态')
      assert(confirm, '撤销弹出产品内二次确认')
      click($('#modal-root [data-action="dialog-confirm"]'))
      await until(() => tokenRevokes.length > before, '撤销请求发出')
      assert(/已撤销/.test($('#main').innerHTML), '撤销后列表标记「已撤销」')
    }
  }
}

console.log('\n[15g] REQ-055：MCP 连接配置可复制但不含令牌；日志只按编号查单条')
{
  const n = clipboard.length
  click(document.querySelector('[data-action="int-copy-conn"]'))
  await until(() => clipboard.length > n, '连接配置复制完成')
  const conn = String(clipboard.at(-1))
  assert(conn.includes('http://127.0.0.1:8722/mcp'), '连接配置含 MCP 地址')
  assert(/Authorization: Bearer <你的令牌>/.test(conn), '连接配置用占位符而非真实令牌')
  assert(!conn.includes(INT_TOKEN) && !conn.includes(INT_ROTATED_TOKEN), '连接配置不含任何明文令牌')

  const before = logQueries.length
  $('#int-log-rid').value = 'zzzz'
  click(document.querySelector('[data-action="int-find-log"]'))
  await sleep(30)
  assert(logQueries.length === before, '非法编号不发查询请求')
  assert(!$('#int-log-error').hasAttribute('hidden'), '非法编号给出内联提示')
  $('#int-log-rid').value = 'deadbeef'
  click(document.querySelector('[data-action="int-find-log"]'))
  const found = await until(() => /deadbeef/.test($('#int-log-result').innerHTML)
    && /500/.test($('#int-log-result').innerHTML), '按编号查到单条日志')
  assert(found, '按 request_id 查到对应的一条排障记录')
  assert(logQueries.at(-1)?.includes('q=deadbeef'), '查询走 GET /api/logs?q=<编号>', logQueries.at(-1))
  assert(/INTERNAL/.test($('#int-log-result').innerHTML), '展示该条记录的错误码')
  $('#int-log-rid').value = 'cafebabe'
  click(document.querySelector('[data-action="int-find-log"]'))
  const none = await until(() => /没有找到该编号对应的记录/.test($('#int-log-result').innerHTML), '查无此编号')
  assert(none, '编号不存在时给出明确空态（不伪装成日志列表）')
}

console.log('\n[16] 闸门：业务请求 403 PASSWORD_CHANGE_REQUIRED → 跳强制改密页（不是 toast）')
{
  const appRoot = $('#app-root')
  const cap = $('#quick-capture')
  gateForbidden = true
  cap.value = '这条会撞上强制改密闸门'
  cap.dispatchEvent(keyEvent('keydown', 'Enter'))

  const jumped = await until(() => !!$('#gate-old') && appRoot.hasAttribute('hidden'), '跳到强制改密页')
  assert(jumped, '收到 403 PASSWORD_CHANGE_REQUIRED 时跳转强制改密页')
  assert($('#app-root').hasAttribute('hidden'), '业务界面已整体隐藏（不留下半屏业务数据）')
  assert(!/初始密码尚未修改/.test($('#toast-root')?.textContent || ''),
    '没有把闸门当成 toast（一闪而过等于没提示）', $('#toast-root')?.textContent)
  assert(/bob@|lpnl@nnit\.com/i.test($('#auth-root').innerHTML), '改密页标明了当前账号', '')
  cap.value = ''
}

console.log('\n[17] 改密成功后重新拉会话并进入业务界面')
{
  gateForbidden = false
  const set = (id, v) => { const el = document.getElementById(id); if (el) { el.value = v; el.setAttribute('value', v) } }
  set('gate-old', 'init-pass-123')
  set('gate-new', 'new-pass-456')
  set('gate-confirm', 'new-pass-456')
  $('#gate-form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))

  const entered = await until(() => !$('#app-root').hasAttribute('hidden') && !/正在同步/.test($('#main').innerHTML),
    '改密后进入业务界面')
  assert(entered, '改密成功后重新读取会话并进入正常界面')
  assert(calls.includes('POST /api/auth/password'), '确实调用了 POST /api/auth/password')
  const pwdReq = requests.filter((r) => r.key === 'POST /api/auth/password').pop()
  assert(pwdReq?.body?.oldPassword === 'init-pass-123' && pwdReq?.body?.newPassword === 'new-pass-456',
    '请求体为 { oldPassword, newPassword }', JSON.stringify(pwdReq?.body))
  assert($('#auth-root').hasAttribute('hidden'), '改密页已隐藏')
}

console.log('\n[18] 首登账号：mustChangePassword=true 时一行业务界面都不渲染')
{
  // 退出 → 用"仍在使用初始口令"的账号登录，走的是真实登录链路
  click($('#user-btn'))
  await sleep(20)
  const outBtn = document.querySelector('#user-panel [data-action="sign-out"]')
  assert(!!outBtn, '账号面板存在退出登录')
  if (outBtn) {
    click(outBtn)
    const signoutDlg = await until(() => { const m = $('#modal-root'); return m && !m.hasAttribute('hidden') && !!m.querySelector('[data-action="dialog-confirm"]') }, '退出登录确认模态')
    assert(signoutDlg, '退出登录也走产品内确认模态')
    if (signoutDlg) click($('#modal-root [data-action="dialog-confirm"]'))
    const atAuth = await until(() => !$('#auth-root').hasAttribute('hidden'), '回到登录页')
    assert(atAuth, '退出后回到登录页')

    SESSION_USER = { id: 'u_new0001', email: 'newbie@example.com', name: '新同事',
      isSystemAdmin: false, mustChangePassword: true }
    const before = calls.filter((c) => c === 'GET /api/workspace').length

    const emailEl = document.querySelector('#auth-form input[name="email"]')
    const pwdEl = document.querySelector('#auth-form input[name="password"]')
    emailEl.value = 'newbie@example.com'
    pwdEl.value = 'init-pass-123'
    $('#auth-form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))

    const gated = await until(() => !!$('#gate-old') && $('#app-root').hasAttribute('hidden'), '登录后被拦到改密页')
    assert(gated, 'mustChangePassword=true → 直接渲染强制改密页')
    assert($('#app-root').hasAttribute('hidden'), '业务外壳始终没有被显示（不会闪出业务数据）')
    const after = calls.filter((c) => c === 'GET /api/workspace').length
    assert(after === before, '被拦住的会话一次都没有请求 /api/workspace', `before=${before} after=${after}`)
    assert(/newbie@example\.com/.test($('#auth-root').innerHTML), '改密页显示了被拦账号的邮箱')
  }
}

console.log('\n[19] 运行时错误捕获')
{
  assert(errors.length === 0, '全程没有未处理的 Promise 拒绝', errors.join(' | '))
}

console.log('\n' + (fail ? `>>> 失败 ${fail} 项，通过 ${pass} 项` : `>>> 全部通过（${pass} 项）`))
process.exit(fail ? 1 : 0)
