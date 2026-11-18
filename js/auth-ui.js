/**
 * 认证界面。
 *
 * 两屏（都只跟本地 Python 服务说话，不依赖任何云平台）：
 *  - 登录：邮箱 + 密码 → `POST /api/auth/login`；
 *  - 强制改密：`user.mustChangePassword === true` 时唯一能做的事 —— 用当前（初始）口令
 *    换一个新口令（`POST /api/auth/password`）。改完才能进工作台。
 *
 * **注册已整体下线（CR-2026-001）**：`POST /api/auth/register` 与 `GET /api/auth/check-email`
 * 都不存在了（命中 404）。账号只能由管理员创建（团队管理员建成员 / 系统管理员建团队管理员），
 * 创建时服务端生成一次性初始口令，本人首次登录即被拦到本文件的第二屏。
 * 因此这里既没有注册标签页，也没有注册口令字段 —— 有的话就是给用户指一条 404 的路。
 *
 * 为什么强制改密是**整页**而不是弹窗：服务端此刻已经拒绝了一切业务请求，
 * 若先渲染主界面再盖弹窗，业务数据会在被拦下之前闪一下（首屏是实时的）。
 *
 * 健壮性约定（沿用历史教训）：
 *  - 每个网络动作都必须通过 withTimeout，避免 Promise 挂起导致按钮永久「处理中」；
 *  - 错误必须同时写入表单内联提示条，仅靠 toast 会让用户以为"点了没反应"；
 *  - 事件委托只在容器上绑一次（dataset.listeners 守卫），重渲染不累积监听器；
 *  - 登录只校验非空 —— 8 位下限只属于"设置新密码"，用它拦历史短密码会把账号永久锁死。
 */
import {
  loginWithPassword, changePassword, getSession,
  log, describeError, friendlyError,
} from './api.js?v=20261118b'
import { $, escapeHtml, isValidEmail, toast, withTimeout } from './util.js?v=20261118b'
import { APP_VERSION, MIN_PASSWORD_LENGTH, MAX_PASSWORD_LENGTH } from './config.js?v=20261118b'
import { passwordGateHtml } from './views.js?v=20261118b'

const state = {
  busy: false,
  alert: '',        // 表单内联错误
}

/** 由 app.js 注入：认证成功后主动收敛会话并切换主界面 */
let onAuthenticated = null
export function setOnAuthenticated(fn) {
  onAuthenticated = fn
}

const NET_TIMEOUT = 30000

/* ---------------- 渲染：登录 ---------------- */

export function renderAuth(root) {
  root.innerHTML = `
    <div class="auth-wrap">
      <section class="auth-aside">
        <div class="auth-brand"><span class="brand-mark"></span> GTD 工作台</div>
        <h1>把脑子里的东西，全都倒出来。</h1>
        <p class="auth-lead">收集、厘清、组织、执行、复盘 —— 一套完整的 Getting Things Done 落地系统。</p>
        <ul class="auth-points">
          <li><b>收集箱</b> 一秒捕获任何想法，不让它在脑中占位</li>
          <li><b>专注视图</b> 只呈现此刻真正能推进的下一步</li>
          <li><b>团队空间</b> 派活、巡检、值班与资源台账一处收口</li>
          <li><b>每周复盘</b> 引导式检查，让清单保持可信</li>
        </ul>
        <div class="auth-iso">账号与数据都在你自己的机器上：本地 SQLite 单文件存储，权限由服务端强制，不依赖任何云平台。</div>
      </section>

      <section class="auth-card">
        <h2 class="auth-form-title">登录到工作台</h2>
        <div class="auth-panels">${alertHtml()}${panelHtml()}</div>
      </section>

      <div class="auth-version" title="构建版本，用于确认页面是否为最新">v${APP_VERSION} · 本地版</div>
    </div>
  `
  bindAuthEvents(root)
  const firstInput = $('input', $('.auth-panels', root))
  if (firstInput) firstInput.focus()
}

function alertHtml() {
  return state.alert
    ? `<div class="auth-alert" role="alert">${escapeHtml(state.alert)}</div>`
    : ''
}

function panelHtml() {
  return `
    <form class="auth-form" id="auth-form" autocomplete="on">
      <label class="field">
        <span class="field-label">邮箱</span>
        <input name="email" type="email" required placeholder="you@example.com" autocomplete="email" />
      </label>
      <label class="field">
        <span class="field-label">密码</span>
        <input name="password" type="password" required placeholder="输入密码" autocomplete="current-password" />
      </label>
      <button class="btn btn-primary btn-block" type="submit">登录</button>
      <p class="auth-hint">账号由管理员创建，请联系你的团队管理员</p>
    </form>`
}

/* ---------------- 渲染：强制改密 ---------------- */

/** 强制改密页的宿主回调（每次渲染时由 app.js 注入） */
let gate = { busy: false, onChanged: null, onSignOut: null }

/**
 * 渲染强制改密页。
 *
 * @param {HTMLElement} root     挂载点（app.js 用 #auth-root —— 业务外壳此时必须保持隐藏）
 * @param {object} opts
 * @param {string} opts.email    当前账号邮箱（让用户知道自己改的是哪个账号）
 * @param {string} [opts.alert]  预置的内联错误
 * @param {(session:object)=>Promise<void>} [opts.onChanged] 改密成功并重读会话后的回调
 * @param {()=>Promise<void>} [opts.onSignOut]                "换一个账号登录"
 */
export function renderPasswordGate(root, { email = '', alert = '', onChanged = null, onSignOut = null } = {}) {
  gate = { busy: false, onChanged, onSignOut }
  root.innerHTML = passwordGateHtml({ email, version: APP_VERSION, alert })
  bindGateEvents(root)
  const firstInput = $('#gate-old', root)
  if (firstInput) firstInput.focus()
}

function bindGateEvents(root) {
  const form = $('#gate-form', root)
  if (form && !form.dataset.bound) {
    form.dataset.bound = '1'
    form.addEventListener('submit', async (e) => {
      e.preventDefault()
      if (gate.busy) return
      await submitGate(root)
    })
  }
  const out = $('[data-gate="signout"]', root)
  if (out && !out.dataset.bound) {
    out.dataset.bound = '1'
    out.addEventListener('click', async (e) => {
      e.preventDefault()
      if (gate.busy) return
      if (typeof gate.onSignOut === 'function') await gate.onSignOut()
    })
  }
}

/**
 * 提交改密。
 *
 * 校验规则必须与 `pyapp/security.py` 的 validate_password() 逐字对齐
 * （MIN=8 / MAX=200，常量来自 config.js）：前端拦下的必须正好是服务端会拒绝的，
 * 否则用户会在"前端说可以、服务端说不行"之间反复试错。
 *
 * 成功后**必须**重新读会话：服务端在同一事务里把 `must_change_password` 清 0，
 * 前端手里的旧会话对象还带着 true —— 不重读就会立刻被自己的门禁再拦一次。
 */
async function submitGate(root) {
  const oldEl = $('#gate-old', root)
  const newEl = $('#gate-new', root)
  const confirmEl = $('#gate-confirm', root)
  const oldPassword = oldEl?.value || ''
  const newPassword = newEl?.value || ''
  const confirm = confirmEl?.value || ''

  if (!oldPassword) return gateFail(root, '请输入当前密码')
  if (!newPassword) return gateFail(root, '请输入新密码')
  if (newPassword.length < MIN_PASSWORD_LENGTH) return gateFail(root, `新密码至少 ${MIN_PASSWORD_LENGTH} 位`)
  if (newPassword.length > MAX_PASSWORD_LENGTH) return gateFail(root, `新密码最长 ${MAX_PASSWORD_LENGTH} 位`)
  if (newPassword !== confirm) return gateFail(root, '两次输入的新密码不一致')

  gate.busy = true
  setGateBusy(root, true)
  gateAlert(root, '')
  try {
    await withTimeout(changePassword(oldPassword, newPassword), NET_TIMEOUT, '设置新密码')
    // 成功：立刻清空三个输入框（不把口令留在 DOM 里）
    for (const el of [oldEl, newEl, confirmEl]) if (el) el.value = ''
    log('info', 'auth.password.changed', '初始密码已修改，正在重新读取会话')
    const session = await withTimeout(getSession(), NET_TIMEOUT, '重新读取会话')
    if (!session?.user) return gateFail(root, '密码已修改，请用新密码重新登录')
    if (session.user.mustChangePassword) {
      return gateFail(root, '密码已提交，但服务端仍要求修改初始密码，请稍后重试或联系管理员')
    }
    if (typeof gate.onChanged === 'function') await gate.onChanged(session)
  } catch (err) {
    log('error', 'auth.password.fail', '设置新密码失败', describeError(err))
    gateFail(root, friendlyError(err))
  } finally {
    gate.busy = false
    setGateBusy(root, false)
  }
}

/* ---------------- 登录事件 ---------------- */

function bindAuthEvents(root) {
  if (!root.dataset.listeners) {
    // 注册相关的点击分支（data-tab / data-goto / data-back）已随注册流程整体删除。
    // 保留这个守卫是为了防止将来重复绑定；当前没有需要委托的点击。
    root.dataset.listeners = '1'
  }

  const form = $('#auth-form', root)
  if (form) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault()
      if (state.busy) return
      const data = Object.fromEntries(new FormData(form).entries())
      await handleSubmit(root, data)
    })
  }
}

async function handleSubmit(root, data) {
  const email = String(data.email || '').trim()
  state.alert = ''

  if (!isValidEmail(email)) return fail(root, '请输入有效的邮箱地址')
  // 登录只校验非空 —— **不要在这里套用设置新密码的 8 位下限**。
  // 服务端的 validate_password() 只作用于设置新密码；历史账号里存在短密码，
  // 前端若按新规则拦截，这些账号会在根本打不到服务端的情况下被永久锁在门外。
  if (!String(data.password || '').length) return fail(root, '请输入密码')

  state.busy = true
  setBusy(root, true)
  renderAlert(root)
  try {
    const session = await withTimeout(loginWithPassword(email, data.password), NET_TIMEOUT, '登录')
    // 是否"必须先改密"由 app.js 的启动流程判定（依据 session.user.mustChangePassword），
    // 这里只负责把会话交出去 —— 认证页不该知道业务门禁的规则。
    await settleAfterSuccess(root, session)
  } catch (err) {
    log('error', 'auth.fail', '登录失败', { email, ...describeError(err) })
    fail(root, friendlyError(err), true)
  } finally {
    state.busy = false
    setBusy(root, false)
  }
}

/**
 * 成功后的收敛：不依赖任何事件推送、也不等轮询 —— 把刚拿到的会话交给宿主切换界面。
 * 会话对象由登录过程直接返回，不再事后回读，规避竞态导致"卡在登录页"。
 */
async function settleAfterSuccess(root, session) {
  log('info', 'auth.ok', '登录成功，正在进入工作台', { email: session?.user?.email })
  try {
    if (typeof onAuthenticated === 'function') {
      await withTimeout(onAuthenticated(session), NET_TIMEOUT, '进入工作台')
    }
  } catch (err) {
    log('error', 'auth.enter.fail', '登录成功但未能进入工作台', describeError(err))
    fail(root, friendlyError(err))
    return
  }
  state.alert = ''
}

function fail(root, message, keepForm = true) {
  state.alert = message
  if (keepForm) renderAlert(root)
  toast(message, 'error')
}

/* ---------------- 辅助 ---------------- */

function renderAlert(root) {
  const host = $('.auth-panels', root)
  if (!host) return
  const existing = $('.auth-alert', host)
  if (state.alert) {
    const html = alertHtml()
    if (existing) existing.outerHTML = html
    else host.insertAdjacentHTML('afterbegin', html)
  } else if (existing) {
    existing.remove()
  }
}

function setBusy(root, busy, label) {
  const btn = $('button[type="submit"]', root)
  if (!btn) return
  if (busy) {
    if (!btn.dataset.label) btn.dataset.label = btn.textContent
    btn.disabled = true
    btn.textContent = label || '处理中…'
  } else {
    btn.disabled = false
    if (btn.dataset.label) btn.textContent = btn.dataset.label
  }
}

/** 强制改密页的错误：只在内联提示条里显示（这一屏没有别的信息可看，不需要再叠 toast） */
function gateFail(root, message) {
  gateAlert(root, message)
  toast(message, 'error')
}

function gateAlert(root, message) {
  const host = $('#gate-error', root)
  if (!host) return
  host.textContent = message || ''
  host.hidden = !message
}

function setGateBusy(root, busy) {
  const btn = $('#gate-submit', root)
  if (!btn) return
  if (busy) {
    if (!btn.dataset.label) btn.dataset.label = btn.textContent
    btn.disabled = true
    btn.textContent = '正在保存…'
  } else {
    btn.disabled = false
    if (btn.dataset.label) btn.textContent = btn.dataset.label
  }
}

export function resetAuthState() {
  state.busy = false
  state.alert = ''
  gate = { busy: false, onChanged: null, onSignOut: null }
}
