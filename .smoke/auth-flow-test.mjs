/**
 * 认证流程行为测试（真实 DOM + 桩 HTTP 响应）。
 *
 * CR-2026-001 之后的账号链路只有三条，本文件把它们逐条钉住：
 *  - 登录：邮箱 + 密码 → POST /api/auth/login；
 *  - 强制改密：`user.mustChangePassword === true` 时**只能用**旧口令换新口令
 *    （POST /api/auth/password 要 oldPassword，所以页面必须有"当前密码"一栏）；
 *  - 自助注册：**已整体下线**（`/api/auth/register` 与 `/api/auth/check-email` 都不存在了）。
 *
 * 关键断言：
 *  1. 登录页**不得**出现任何注册入口（标签页 / 链接 / 表单字段），也不得再调已下线端点；
 *  2. 登录只校验非空 —— 历史账号存在短密码，按"设置新密码"的 8 位下限拦截会把它们永久锁死；
 *  3. 强制改密页：三个 password 字段 + autocomplete 正确；口令规则与 `pyapp/security.py` 对齐；
 *     弱口令 / 两次不一致**在前端拦下**，不打服务端；失败时不清空已填内容、按钮必须恢复；
 *  4. 强制改密成功：清空输入框 → 重新 `GET /api/auth/session` → 才把会话交给宿主
 *     （少了这一步，用户会带着"仍需改密"的旧状态再被拦一次）；
 *  5. 服务端文案（INVALID_OLD_PASSWORD / WEAK_PASSWORD）原样落到内联提示；
 *  6. `js/api.js` 的导出增删与 `06` §10.7 一致：仍 27 个，注册两函数已删、建号两函数已加；
 *  7. 已下线的错误码（SIGNUP_DISABLED / INVALID_SIGNUP_CODE）不再映射。
 *
 * 运行：node .smoke/auth-flow-test.mjs
 */
import { parseHTML } from './vendor/linkedom.js'

/* ---------- 最小 DOM 环境 ---------- */
const { document, Event } = parseHTML(`<!DOCTYPE html><html><body>
  <div id="auth-root"></div><div id="toast-root"></div>
</body></html>`)

{
  const proto = Object.getPrototypeOf(document.createElement('input'))
  if (!proto.focus) proto.focus = function () {}
  if (!proto.select) proto.select = function () {}
}

class MiniFormData extends Map {
  constructor(form) {
    super()
    for (const input of form.querySelectorAll('input[name]')) {
      this.set(input.getAttribute('name'), input.getAttribute('value') ?? input.value ?? '')
    }
  }
}

globalThis.document = document
globalThis.Event = Event
globalThis.FormData = MiniFormData
globalThis.window = globalThis
globalThis.location = { origin: 'http://127.0.0.1:8787', href: 'http://127.0.0.1:8787/' }

/* ---------- 桩本地服务（拦 fetch） ---------- */
const calls = []                 // { method, path, body, raw }
const logRows = []
let loginError = null
let passwordError = null         // { status, code, message }
let sessionUser = { id: 'u_test', email: 'demo@example.com', name: 'demo', isSystemAdmin: false, mustChangePassword: false }
let hang = false

function jsonResponse(payload, status = 200) {
  return { ok: status >= 200 && status < 300, status, text: async () => JSON.stringify(payload) }
}

globalThis.fetch = async (url, options = {}) => {
  const method = (options.method || 'GET').toUpperCase()
  const path = String(url).split('?')[0]
  const body = options.body ? JSON.parse(options.body) : null
  // 运行日志走缓冲异步上报，会干扰"这次提交有没有打服务端"的断言，单独归类
  if (path !== '/api/logs') calls.push({ method, path, body, raw: String(url) })
  if (hang) return new Promise(() => {})         // 模拟请求挂起

  if (path === '/api/auth/login') {
    if (loginError) return jsonResponse({ error: loginError }, loginError.status || 401)
    return jsonResponse({ user: sessionUser })
  }
  if (path === '/api/auth/session') return jsonResponse(sessionUser ? { user: sessionUser } : {})
  if (path === '/api/auth/password') {
    if (passwordError) return jsonResponse({ error: passwordError }, passwordError.status || 400)
    // 改密成功：服务端把 must_change_password 清 0，会话随即可用
    sessionUser = { ...sessionUser, mustChangePassword: false }
    return jsonResponse({ ok: true })
  }
  if (path === '/api/auth/logout') return jsonResponse({ ok: true })
  if (path === '/api/logs') { logRows.push(body); return jsonResponse({ rows: [{ id: 1 }] }) }
  // 已下线端点与未打桩端点：一律 404，任何调用都会被断言抓住
  return jsonResponse({ error: { code: 'NOT_FOUND', message: path + ' 不存在' } }, 404)
}

/* ---------- 加载真实源码 ---------- */
const AUTH = await import('../js/auth-ui.js')
const { renderAuth, renderPasswordGate, resetAuthState } = AUTH
const api = await import('../js/api.js')
const { log, friendlyError } = api

/* ---------- 测试基础设施 ---------- */
let pass = 0
let fail = 0
function assert(cond, name, detail = '') {
  if (cond) { pass++; console.log('  OK   ' + name) }
  else { fail++; console.log('  FAIL ' + name + (detail ? ' -> ' + detail : '')) }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const root = document.getElementById('auth-root')
const $ = (sel, scope = root) => scope.querySelector(sel)
const submitBtn = () => $('button[type="submit"]')
const alertText = () => {
  const el = $('.auth-alert:not([hidden])')
  return el ? el.textContent.trim() : ''
}

/** 提交处理器是 async：等待「按钮进入忙态 → 解除忙态」这一完整过程 */
async function settle(ms = 5000) {
  const deadline = Date.now() + ms
  let sawBusy = false
  while (Date.now() < deadline) {
    const btn = submitBtn()
    const busy = !!btn && btn.hasAttribute('disabled')
    if (busy) sawBusy = true
    else if (sawBusy) return true
    await sleep(15)
  }
  return sawBusy
}

function fill(name, value) {
  const input = $(`input[name="${name}"]`)
  if (!input) return false
  input.setAttribute('value', value)
  input.value = value
  return true
}

function fillId(domId, value) {
  const input = $(`#${domId}`)
  if (!input) return false
  input.setAttribute('value', value)
  input.value = value
  return true
}

async function submit(selector = '#auth-form') {
  const form = $(selector)
  form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  await settle()
  await sleep(20)
}

function reset() {
  calls.length = 0
  resetAuthState()
  sessionUser = { id: 'u_test', email: 'demo@example.com', name: 'demo', isSystemAdmin: false, mustChangePassword: false }
  loginError = null
  passwordError = null
  renderAuth(root)
}

/** 直接进入强制改密页（等价于 app.js 在 applySession 里判定 mustChangePassword 后的动作） */
let gateChanged = null
let gateSignedOut = false
function gotoGate(email = 'bob@example.com') {
  calls.length = 0
  gateChanged = null
  gateSignedOut = false
  renderPasswordGate(root, {
    email,
    version: 'test',
    onChanged: async (session) => { gateChanged = session },
    onSignOut: async () => { gateSignedOut = true },
  })
}

let authenticated = null
AUTH.setOnAuthenticated(async (session) => { authenticated = session })

/* ================= 用例 ================= */

console.log('\n[1] 登录页结构：注册入口已整体下线')
{
  reset()
  assert(document.querySelectorAll('.auth-tab').length === 0, '不再有"密码登录/注册"标签页',
    'count=' + document.querySelectorAll('.auth-tab').length)
  assert(!/注册/.test(root.innerHTML), '登录页不再出现"注册"字样', '')
  assert(!$('[data-tab="signup"]') && !$('[data-goto="signup"]'), '不再有指向注册页的入口')
  assert(!!$('input[name="email"]') && !!$('input[name="password"]'), '登录表单有邮箱与密码字段')
  assert(!$('input[name="code"]'), '登录表单不再有验证码字段（邮件链路已移除）')
  assert(!$('input[name="confirm"]'), '登录表单没有确认密码字段')
  assert(!$('input[name="signupCode"]'), '登录表单没有注册口令字段')
  assert(/账号由管理员创建/.test(root.innerHTML), '底部给出中性说明"账号由管理员创建"')
  assert(!/系统管理员/.test(root.innerHTML), '登录页不向普通用户暴露"系统管理员"这类内部层级', '')
}

console.log('\n[2] 登录')
{
  reset()
  fill('email', 'bad-email')
  fill('password', 'whatever-123')
  await submit()
  assert(calls.length === 0, '非法邮箱不打服务端', JSON.stringify(calls.map((c) => c.path)))

  reset()
  fill('email', 'demo@example.com')
  fill('password', '')
  await submit()
  assert(calls.length === 0, '空密码不打服务端')
  assert(/请输入密码/.test(alertText()), '空密码给出内联提示', alertText())

  // 登录只校验非空：历史账号可能存在短密码，若按"设置新密码"的 8 位下限拦截，
  // 这些账号会在根本打不到服务端的情况下被永久锁在门外（8 位规则只属于设置新密码）。
  reset()
  loginError = { status: 401, code: 'INVALID_CREDENTIALS', message: '邮箱或密码不正确' }
  fill('email', 'demo@example.com')
  fill('password', 'short')
  await submit()
  assert(calls.some((c) => c.path === '/api/auth/login'), '短密码也能打到服务端（登录不做长度下限校验）',
    JSON.stringify(calls.map((c) => c.path)))
  assert(/邮箱或密码不正确/.test(alertText()), '凭据错误给出内联提示', alertText())
  assert(submitBtn() && !submitBtn().hasAttribute('disabled'), '失败后按钮已恢复（不卡在处理中）')
  assert(submitBtn().textContent === '登录', '按钮文案已复原', submitBtn().textContent)

  reset()
  loginError = { status: 429, code: 'RATE_LIMITED', message: '尝试次数过多，请 15 分钟后再试' }
  fill('email', 'demo@example.com')
  fill('password', 'wrong-pass-123')
  await submit()
  assert(/尝试次数过多/.test(alertText()), '限流错误有明确提示', alertText())

  reset()
  authenticated = null
  fill('email', 'demo@example.com')
  fill('password', 'good-pass-123')
  await submit()
  const login = calls.find((c) => c.path === '/api/auth/login')
  assert(!!login && login.method === 'POST', '登录走 POST /api/auth/login')
  assert(login && login.body.password === 'good-pass-123', 'body 携带密码')
  assert(login && Object.keys(login.body).sort().join(',') === 'email,password', 'body 字段集合与契约一致',
    Object.keys(login?.body || {}).join(','))
  assert(!!authenticated && authenticated.user.email === 'demo@example.com', '登录成功后把会话交给宿主')
  assert(!calls.some((c) => /\/api\/auth\/(register|check-email)/.test(c.path)), '整个登录流程不会调用已下线端点')
}

console.log('\n[3] js/api.js 导出契约：删 2 增 2 + 证书 3 个')
{
  const names = Object.keys(api).filter((k) => k !== 'default').sort()
  // v1.1：注册两函数删除、建号两函数新增（27）；v1.3：证书三函数新增（30）
  assert(names.length === 30, '导出总数为 30', 'count=' + names.length)
  assert(typeof api.createAccount === 'function', '新增 createAccount')
  assert(typeof api.listAccounts === 'function', '新增 listAccounts')
  assert(!('registerWithPassword' in api), '已删除 registerWithPassword（端点 404）')
  assert(!('checkEmail' in api), '已删除 checkEmail（端点 404）')
  assert(typeof api.changePassword === 'function' && typeof api.getSession === 'function',
    '改密与读会话接口仍在（强制改密页依赖它们）')
  assert(typeof api.getCert === 'function' && typeof api.uploadCert === 'function'
    && typeof api.deleteCert === 'function', '证书三接口齐备（GET/POST/DELETE /api/admin/cert）')
}

console.log('\n[4] 强制改密页：结构')
{
  gotoGate('bob@example.com')
  const oldEl = $('#gate-old')
  const newEl = $('#gate-new')
  const confirmEl = $('#gate-confirm')
  assert(!!oldEl && oldEl.getAttribute('type') === 'password', '有"当前密码"字段（POST /api/auth/password 要 oldPassword）')
  assert(!!oldEl && oldEl.getAttribute('autocomplete') === 'current-password', '当前密码 autocomplete=current-password')
  assert(!!newEl && newEl.getAttribute('autocomplete') === 'new-password', '新密码 autocomplete=new-password')
  assert(!!confirmEl && confirmEl.getAttribute('autocomplete') === 'new-password', '确认新密码 autocomplete=new-password')
  assert(!!newEl && newEl.getAttribute('minlength') === '8' && newEl.getAttribute('maxlength') === '200',
    '新密码长度上下限与 security.py 对齐（8 / 200）')
  assert(/首次登录/.test(root.innerHTML) && /重置/.test(root.innerHTML), '说明了"为什么被拦在这里"')
  assert(/bob@example\.com/.test(root.innerHTML), '显示了当前账号，用户知道在改哪个账号')
  assert(!!$('[data-gate="signout"]'), '提供"换一个账号登录"出口（被锁时不能只能干等）')
  assert(!/注册/.test(root.innerHTML), '强制改密页不出现注册入口')
}

console.log('\n[5] 强制改密：前端校验（不合格不打服务端）')
{
  gotoGate()
  calls.length = 0
  fillId('gate-old', '')                     // 少填当前密码
  fillId('gate-new', 'good-pass-123')
  fillId('gate-confirm', 'good-pass-123')
  await submit('#gate-form')
  assert(calls.length === 0, '未填当前密码不打服务端', JSON.stringify(calls.map((c) => c.path)))
  assert(/当前密码/.test(alertText()), '未填当前密码给出内联提示', alertText())

  gotoGate()
  calls.length = 0
  fillId('gate-old', 'init-pass-123')
  fillId('gate-new', '1234567')              // 7 位，低于下限
  fillId('gate-confirm', '1234567')
  await submit('#gate-form')
  assert(calls.length === 0, '新密码不足 8 位不打服务端', JSON.stringify(calls.map((c) => c.path)))
  assert(/至少/.test(alertText()) && /8/.test(alertText()), '弱口令给出内联提示', alertText())

  gotoGate()
  calls.length = 0
  fillId('gate-old', 'init-pass-123')
  fillId('gate-new', 'x'.repeat(201))        // 超过上限
  fillId('gate-confirm', 'x'.repeat(201))
  await submit('#gate-form')
  assert(calls.length === 0, '新密码超过 200 位不打服务端', JSON.stringify(calls.map((c) => c.path)))
  assert(/200/.test(alertText()), '超长给出内联提示', alertText())

  gotoGate()
  calls.length = 0
  fillId('gate-old', 'init-pass-123')
  fillId('gate-new', 'good-pass-123')
  fillId('gate-confirm', 'good-pass-124')
  await submit('#gate-form')
  assert(calls.length === 0, '两次不一致不打服务端', JSON.stringify(calls.map((c) => c.path)))
  assert(/不一致/.test(alertText()), '两次不一致给出内联提示', alertText())
}

console.log('\n[6] 强制改密：提交与成功收敛')
{
  gotoGate()
  calls.length = 0
  gateChanged = null
  fillId('gate-old', 'init-pass-123')
  fillId('gate-new', 'new-pass-456')
  fillId('gate-confirm', 'new-pass-456')
  await submit('#gate-form')

  const req = calls.find((c) => c.path === '/api/auth/password')
  assert(!!req && req.method === 'POST', '提交走 POST /api/auth/password')
  assert(req && req.body.oldPassword === 'init-pass-123', 'body 携带 oldPassword（首登场景用户知道初始口令）',
    JSON.stringify(req?.body))
  assert(req && req.body.newPassword === 'new-pass-456', 'body 携带 newPassword')
  assert(req && Object.keys(req.body).sort().join(',') === 'newPassword,oldPassword', 'body 字段集合与契约一致',
    Object.keys(req?.body || {}).join(','))
  assert(($('#gate-old')?.value || '') === '' && ($('#gate-new')?.value || '') === '',
    '成功后清空输入框（不把口令留在 DOM 里）')
  assert(!!gateChanged && gateChanged.user && gateChanged.user.mustChangePassword === false,
    '成功后重新拉了一次会话，且新会话已不再要求改密', JSON.stringify(gateChanged))
  assert(calls.some((c) => c.path === '/api/auth/session'), '重新读了 /api/auth/session 才收敛')
  assert(!calls.some((c) => /\/api\/auth\/(register|check-email)/.test(c.path)), '改密流程不会调用已下线端点')
  assert(submitBtn() && !submitBtn().hasAttribute('disabled'), '成功后按钮恢复可用态')
}

console.log('\n[7] 强制改密：服务端文案如实展示，且不丢已填内容')
{
  gotoGate()
  passwordError = { status: 401, code: 'INVALID_OLD_PASSWORD', message: '原密码不正确' }
  fillId('gate-old', 'wrong-old-123')
  fillId('gate-new', 'new-pass-456')
  fillId('gate-confirm', 'new-pass-456')
  await submit('#gate-form')
  assert(/原密码不正确/.test(alertText()), 'INVALID_OLD_PASSWORD 映射为可读文案', alertText())
  assert(($('#gate-old')?.value || '') === 'wrong-old-123', '失败后不清空当前密码输入框（用户可直接改）',
    $('#gate-old')?.value)
  assert(submitBtn() && !submitBtn().hasAttribute('disabled'), '失败后按钮已恢复')
  assert(!gateChanged, '失败时不得把会话交给宿主（仍必须停在改密页）')

  gotoGate()
  passwordError = { status: 400, code: 'WEAK_PASSWORD', message: '密码强度不足，请使用更长的密码' }
  fillId('gate-old', 'init-pass-123')
  fillId('gate-new', 'good-pass-123')
  fillId('gate-confirm', 'good-pass-123')
  await submit('#gate-form')
  assert(/密码强度不足|更长/.test(alertText()), 'WEAK_PASSWORD 用服务端文案', alertText())

  // 改密页的"换一个账号"出口
  gotoGate()
  $('[data-gate="signout"]').dispatchEvent(new Event('click', { bubbles: true }))
  await sleep(20)
  assert(gateSignedOut === true, '"换一个账号登录"触发登出回调（被锁的人必须能退出）')
}

console.log('\n[8] 服务不可用：给出可操作提示而不是空白')
{
  reset()
  hang = true
  fill('email', 'demo@example.com')
  fill('password', 'good-pass-123')
  $('#auth-form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  await sleep(60)
  assert(submitBtn().hasAttribute('disabled'), '请求进行中按钮处于禁用态（防重复提交）')
  hang = false
  assert(!/undefined/.test(root.innerHTML), '界面没有渲染出 undefined')
}

console.log('\n[9] 运行日志：留痕但不记录明文密码')
{
  reset()
  await sleep(1600)                       // 先让前面用例缓冲的日志刷写完，避免混进本节样本
  logRows.length = 0
  log('info', 'probe.test', '探针', { password: 'plain-secret-value', errCode: 'X1', token: 'tok-abc' })
  log('info', 'probe.test2', '探针2')
  await sleep(1500)                       // 等缓冲刷写（内部 1.2s 定时）
  assert(logRows.length >= 1, '日志已上报到服务端', 'rows=' + logRows.length)
  const row = logRows[0] || {}
  assert(row.detail && row.detail.password !== 'plain-secret-value', '密码字段被脱敏',
    JSON.stringify(row.detail?.password))
  assert(/已脱敏/.test(String(row.detail?.password || '')), '脱敏占位文案正确')
  assert(row.detail?.errCode === 'X1', '排障关键字段 errCode 不被误伤', JSON.stringify(row.detail))
  assert(row.detail?.token !== 'tok-abc', '令牌同样被脱敏')
  assert(!!row.detail?.sid, '日志带会话号，便于串联同一次会话的全部事件')
  assert(row.scope === 'probe', 'scope 由事件名首段推导')
}

console.log('\n[10] 错误码映射（表驱动，message 为空时看 MAP 兜底）')
{
  const cases = [
    ['UNAUTHENTICATED', /重新登录/],
    ['INVALID_CREDENTIALS', /邮箱或密码不正确/],
    ['INVALID_OLD_PASSWORD', /原密码不正确/],
    ['EMAIL_EXISTS', /已有账号/],
    ['INVALID_JOIN_CODE', /邀请码无效/],
    ['PERMISSION_DENIED', /没有权限/],
    ['RATE_LIMITED', /频繁|次数过多/],
    ['UNKNOWN_TABLE', /数据表/],
    // CR-2026-001 新增
    ['PASSWORD_CHANGE_REQUIRED', /初始密码尚未修改/],
    ['FORBIDDEN', /没有权限执行该操作/],
    ['NOT_TEAM_ADMIN', /你不是该团队的管理员/],
    ['ROLE_NOT_ALLOWED', /你无权授予该角色/],
    ['TEAM_REQUIRED', /请先选择要加入的团队/],
    ['TEAM_NOT_FOUND', /团队不存在或已归档/],
    ['NO_SYSTEM_ADMIN', /还没(有|没有)系统管理员|--create-admin/],
    // 证书管理（契约：主理人下发）
    ['CERT_INVALID_PEM', /完整 PEM/],
    ['CERT_KEY_MISMATCH', /不匹配/],
    ['CERT_EXPIRED', /已过期/],
    ['CERT_KEY_ENCRYPTED', /未加密的私钥/],
    // 已下线端点
    ['NOT_FOUND', /已下线或不存在/],
  ]
  for (const [code, pattern] of cases) {
    const msg = friendlyError({ code, message: '' })
    assert(pattern.test(msg), `${code} 映射为可读文案`, msg)
  }
  assert(/本地服务/.test(friendlyError({ code: null, kind: 'network' })), '网络故障指向本地服务')
  assert(/超时/.test(friendlyError({ code: null, kind: 'timeout' })), '超时单独提示')

  // 已下线的码：不得再有任何映射，否则会把"能力已移除"表述成"此刻暂不可用"
  assert(friendlyError({ code: 'SIGNUP_DISABLED', message: '' }) === '操作失败，请稍后重试。',
    'SIGNUP_DISABLED 已从映射表移除', friendlyError({ code: 'SIGNUP_DISABLED', message: '' }))
  assert(friendlyError({ code: 'INVALID_SIGNUP_CODE', message: '' }) === '操作失败，请稍后重试。',
    'INVALID_SIGNUP_CODE 已从映射表移除', friendlyError({ code: 'INVALID_SIGNUP_CODE', message: '' }))
}

console.log('\n[11] 服务端文案优先：MAP 不得顶掉服务端打磨过的措辞')
{
  // 这三条的语义随调用者/场景变化（登录 vs 建号 vs 加入团队），必须让服务端说
  const server = {
    EMAIL_EXISTS: '该邮箱已有账号。请把本团队邀请码发给对方，由对方登录后自行加入；若对方无法登录，请联系运维重置密码。',
    ROLE_NOT_ALLOWED: '没有团队就没有团队角色，请先选择团队',
    FORBIDDEN: '只有系统管理员可以创建团队管理员',
  }
  for (const [code, message] of Object.entries(server)) {
    const got = friendlyError({ code, message })
    assert(got === message, `${code} 优先展示服务端文案（MAP 只做兜底）`, got)
  }
  // 反过来：服务端没给文案时仍有中性兜底
  assert(/已有账号/.test(friendlyError({ code: 'EMAIL_EXISTS', message: '' })), 'EMAIL_EXISTS 有中性兜底')
  assert(/你无权授予该角色/.test(friendlyError({ code: 'ROLE_NOT_ALLOWED', message: '' })),
    'ROLE_NOT_ALLOWED 兜底是中性的（不能写"只能创建成员"）')
  // 语义唯一的码：仍以服务端文案为准（便于后端微调）
  assert(/初始密码/.test(friendlyError({ code: 'PASSWORD_CHANGE_REQUIRED', message: '服务端文案' })),
    'PASSWORD_CHANGE_REQUIRED 不会被服务端文案覆盖（它只有一种含义）')
}

console.log('\n[12] 404：code 优先，status 只在 code 缺失时兜底')
{
  // 有 code 的 404：走 MAP，说清具体原因（TEAM_NOT_FOUND 本身就是 404）
  const team404 = friendlyError({ code: 'TEAM_NOT_FOUND', status: 404, message: '团队不存在或已归档' })
  assert(/团队不存在或已归档/.test(team404), 'TEAM_NOT_FOUND 的 404 不会被 404 兜底吞掉', team404)
  assert(!/已下线/.test(team404), 'TEAM_NOT_FOUND 不应被说成"功能已下线"', team404)
  const notFoundCode = friendlyError({ code: 'NOT_FOUND', status: 404, message: '接口 /api/auth/register 不存在' })
  assert(/已下线或不存在/.test(notFoundCode), '已下线端点给面向用户的说法（不是"接口 xxx 不存在"）', notFoundCode)
  assert(!/\/api\/auth\/register/.test(notFoundCode), '不把服务端技术文案甩给用户', notFoundCode)

  // 没有 code 的 404：静态资源（text/plain，无 JSON 体）→ 只能按 status 兜底
  const static404 = friendlyError({ code: null, status: 404, message: '请求失败（HTTP 404）' })
  assert(/已下线或不存在/.test(static404), '无 code 的 404 按 status 兜底', static404)
  assert(!/HTTP 404/.test(static404), '兜底文案不暴露 HTTP 状态码细节', static404)

  // 其它状态码不受 404 兜底影响
  const bad500 = friendlyError({ code: null, status: 500, message: '' })
  assert(bad500 === '操作失败，请稍后重试。', '非 404 的未知错误保持原兜底', bad500)
}

console.log('\n' + (fail ? `>>> 失败 ${fail} 项，通过 ${pass} 项` : `>>> 全部通过（${pass} 项）`))
process.exit(fail ? 1 : 0)
