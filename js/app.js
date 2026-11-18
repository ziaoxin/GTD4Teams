/**
 * 应用主控：会话门禁、状态、路由、事件委托与业务动作。
 *
 * 数据隔离的执行位置在数据库 RLS（成员制策略），本文件只做四件事：
 *   - 会话门禁（未登录绝不触碰任何数据）
 *   - 写结果校验（受影响行数为 0 即视为被 RLS 拦下，明确提示而不是假装成功）
 *   - 状态与路由（空间 / 团队 / 视图三层）
 *   - 事件委托（单一入口 dispatch，视图层不绑事件）
 */
import {
  getSession, getProfile, signOut, changePassword, loadWorkspace,
  createRow, updateRow, deleteRow, createItem, updateItem, deleteItem,
  createTeamWithOwner, joinTeamByCode, saveReview, createLog,
  createAccount, getCert, uploadCert, deleteCert,
  friendlyError, log, describeError, health,
} from './api.js?v=20261118b'
import { renderAuth, renderPasswordGate, resetAuthState, setOnAuthenticated } from './auth-ui.js?v=20261118b'
import {
  renderSidebar, renderView, renderSpaceTabs, renderTeamSelect, renderOnboarding,
  makeLookups, itemDrawerHtml, projectDrawerHtml, newItemFormHtml, teamFormHtml,
  joinTeamFormHtml, projectFormHtml, contextFormHtml, resourceFormHtml,
  memberFormHtml, onCallFormHtml, userPanelHtml, personalItems, assignedToMe, isOpen,
  invitePanelHtml, accountFormHtml, accountCreatedHtml, identityOf, confirmDialogHtml,
  tokenCreatedHtml,
  card, emptyState, itemRow,
} from './views.js?v=20261118b'
import {
  $, $$, escapeHtml, debounce, initials, diffDays, toast, todayStr, addDays,
  addMonths, withTimeout, makeJoinCode, isoNow, clamp, shortName, uniqBy,
  describeIdentity, isPlaceholderIdentity, emailPrefix, urlSafety, isValidEmail,
} from './util.js?v=20261118b'
import {
  APP_VERSION, DEFAULT_CONTEXTS, DEFAULT_AREAS, INSPECTION_TEMPLATE, HANDOVER_TEMPLATE,
  ITEM_TYPES, PRIORITY, ROLES, MIN_PASSWORD_LENGTH, MAX_PASSWORD_LENGTH,
} from './config.js?v=20261118b'

/* ---------------- 状态 ---------------- */

const emptyData = () => ({
  teams: [], members: [], items: [], projects: [], areas: [],
  contexts: [], resources: [], reviews: [], onCall: [], logs: [],
})

const LS_KEY = 'gtd4teams.ui.v1'

function loadUiPrefs() {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (!raw) return {}
    const v = JSON.parse(raw)
    return v && typeof v === 'object' ? v : {}
  } catch {
    return {}
  }
}

function saveUiPrefs() {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify({
      space: state.space,
      activeTeamId: state.activeTeamId,
      view: state.view,
    }))
  } catch { /* 偏好写入失败不影响使用 */ }
}

const prefs = loadUiPrefs()


/** 集成域本地态：只存列表与查询结果，明文令牌永不进入此处 */
function emptyIntegration() {
  return {
    loaded: false, loading: false, error: '', config: null, tokens: [],
    logQuery: '', logLoading: false, logError: '', logResult: null, logDegraded: '',
  }
}
const state = {
  ready: false,
  user: null,
  profile: null,   // auth.getUser() 的资料；getSession() 的 user 不含 email，必须靠它补
  identity: { id: '', name: '', email: '' },
  data: emptyData(),
  space: prefs.space === 'team' ? 'team' : 'personal',
  activeTeamId: prefs.activeTeamId ?? null,
  view: prefs.view || (prefs.space === 'team' ? 'overview' : 'focus'),
  search: '',
  filters: {},
  weekAnchor: todayStr(),
  openItemId: null,
  openProjectId: null,
  loading: false,
  // 正在执行的写操作名（null = 空闲）。写操作串行化，防止连点导致重复入库。
  busy: null,
  // 当前会话是否被"必须先改初始口令"拦住（true 时业务界面一帧都不渲染）
  mustChangePassword: false,
  // 系统管理页的证书状态（实例级，按需拉取；不含任何私钥材料）
  cert: { loaded: false, loading: false, error: '', applying: false, data: null },
  // 系统管理页的集成域状态（令牌列表 + 按编号查日志；明文令牌绝不写进这里）
  integration: emptyIntegration(),
}

/** 团队视图与个人视图的默认落地页 —— 切换空间时用于纠偏 */
const TEAM_VIEWS = ['overview', 'routine', 'desk', 'board', 'projects', 'oncall', 'resources', 'tools', 'reports', 'members', 'teamReview', 'teamSettings']
const PERSONAL_VIEWS = ['inbox', 'clarify', 'focus', 'next', 'waiting', 'calendar', 'someday', 'assigned', 'myProjects', 'contexts', 'review', 'done']
/** 实例级视图：与"个人/团队空间"无关，两个空间下都允许停留 */
const SYSTEM_VIEWS = ['system']

function normalizeView() {
  if (SYSTEM_VIEWS.includes(state.view)) {
    // 系统管理是权限相关视图：不是系统管理员就不该停在这一页。
    // 这一条也兜住"localStorage 里存着 system、但账号已被取消管理员身份"的情况 ——
    // 否则他每次登录都会先撞一次"权限不足"。
    if (state.user?.isSystemAdmin) return
    state.view = state.space === 'team' ? 'overview' : 'focus'
    return
  }
  const allowed = state.space === 'team' ? TEAM_VIEWS : PERSONAL_VIEWS
  if (!allowed.includes(state.view)) {
    state.view = state.space === 'team' ? 'overview' : 'focus'
  }
}

/* ---------------- 渲染 ---------------- */

function activeTeam() {
  return (state.data.teams || []).find((t) => t.id === state.activeTeamId) || null
}

function render() {
  // 闸门期内不渲染任何业务内容 —— 这是一道纯前端兜底：
  // 即使某条异步链路在切到改密页之后才回来，也不会把团队数据重新画进 #main（那会闪一下）。
  if (state.mustChangePassword) return

  const lk = makeLookups(state.data)
  const hasAnything = (state.data.teams || []).length > 0
    || personalItems(state.data, state.user?.id).length > 0
    || assignedToMe(state.data, state.user?.id).length > 0

  $('#space-tabs').innerHTML = renderSpaceTabs(state)
  $('#team-switch').innerHTML = renderTeamSelect(state, lk)
  $('#sidebar').innerHTML = renderSidebar(state)

  const main = $('#main')
  if (state.loading && !state.ready) {
    main.innerHTML = '<div class="loading">正在同步你的工作区…</div>'
    return
  }
  if (!hasAnything) {
    main.innerHTML = renderOnboarding(state)
    return
  }
  // 搜索视图由本文件承担（要跨团队/个人两种作用域取数），不走 views 的分发
  main.innerHTML = state.view === 'search' ? renderSearchHtml() : renderView(state)

  if (state.view === 'search') {
    const input = $('#global-search')
    if (input && document.activeElement !== input) {
      input.value = state.search
      input.focus()
    }
  }
  bindRangeOutputs()
}

/** 滑块旁边实时显示数值，避免用户拖完不知道为什么没生效 */
function bindRangeOutputs() {
  const pairs = [['#f-progress', '#f-progress-val'], ['#p-progress', '#p-progress-val'], ['#p-progress', '#f-progress-val']]
  for (const [input, out] of pairs) {
    const el = $(input)
    const o = $(out)
    if (el && o) {
      el.addEventListener('input', () => { o.textContent = `${el.value}%` })
    }
  }
  const pEl = $('#p-progress')
  const fEl = $('#f-progress')
  const pv = $('#p-progress-val')
  const fv = $('#f-progress-val')
  if (pEl && fv) pEl.addEventListener('input', () => { fv.textContent = `${pEl.value}%` })
  if (fEl && pv) fEl.addEventListener('input', () => { pv.textContent = `${fEl.value}%` })
}

/* ---------------- 登录者身份 ---------------- */

/**
 * 重算 state.identity —— 纯同步，不请求。
 *
 * 事实依据（本次线上缺陷的根因）：
 *   getSession() 返回的 user 里**没有 email**（SDK 只给 { id, isAnonymous, raw }），
 *   权威邮箱只能来自 auth.getUser()（见 api.getProfile）。因此分两级：
 *     · 同步级：用现有会话 + 已知资料算一个可用的 { id, name, email }；
 *     · 异步级：hydrateIdentity() 取回权威资料后再算一次。
 */
function refreshIdentity() {
  const base = describeIdentity(state.user, state.profile)
  state.identity = { ...base, id: base.id || state.user?.id || '' }
  return state.identity
}

/** 取权威账号资料（失败即回退，绝不阻断登录流程 —— 这是纯增强） */
async function hydrateIdentity() {
  if (!state.user) return
  try {
    state.profile = await withTimeout(getProfile(), 12000, '读取账号资料')
  } catch (err) {
    log('warn', 'app.profile.fail', '读取账号资料失败，身份显示回退到会话可推断范围', describeError(err))
  }
  refreshIdentity()
}

/** 把身份画到顶栏用户按钮上：用户名 + 邮箱两行 */
function renderUserChip() {
  if (!state.user) return
  const ident = identityOf(state)
  const av = $('#user-avatar')
  const nm = $('#user-name')
  const em = $('#user-email')
  if (av) av.textContent = initials(ident.email || ident.name)
  if (nm) nm.textContent = ident.name
  if (em) {
    em.textContent = ident.email || ''
    em.hidden = !ident.email
  }
}

/* ---------------- 会话门禁 ---------------- */

async function bootstrap(session) {
  if (!session) {
    session = await getSession().catch((err) => {
      log('error', 'app.session.fail', '读取会话失败', describeError(err))
      return null
    })
  }
  log('info', 'app.bootstrap', '启动并完成会话检查', { hasUser: !!session?.user })
  await applySession(session)
}

async function applySession(session) {
  if (!session?.user) {
    state.user = null
    state.profile = null
    state.identity = { id: '', name: '', email: '' }
    state.ready = false
    state.mustChangePassword = false
    log('info', 'app.gate', '无有效会话，停留在认证界面')
    showScreen('auth')
    return
  }
  state.user = session.user

  // 强制改密闸门的第一道（前端侧）：服务端此刻已经拒绝了一切业务请求，
  // 界面就不该先渲染一遍业务数据再弹窗 —— 那会让人看到一闪而过的团队内容。
  // 第二道在每一个业务响应上（403 PASSWORD_CHANGE_REQUIRED，见 handleGateError）。
  if (session.user.mustChangePassword) {
    state.mustChangePassword = true
    state.ready = false
    refreshIdentity()
    log('info', 'app.gate.password', '会话要求先修改初始密码，停留在改密页', { uid: state.identity.id })
    enterPasswordGate()
    return
  }
  state.mustChangePassword = false

  // 先把"我是谁"确定下来：后续写库的归属字段（成员邮箱、作者名）都取自它
  refreshIdentity()
  renderUserChip()
  log('info', 'app.enter', '会话有效，加载工作区', { uid: state.identity.id })
  showScreen('app')
  await hydrateIdentity()
  renderUserChip()
  await refresh()
  // 必须放在 refresh 之后：成员行要等数据到位才看得到，否则这里等于空跑
  await syncMemberIdentity()
  state.ready = true
}

/**
 * 把当前账号的邮箱与显示名回填到本人名下的成员行。
 *
 * 两个必要条件（历史实现两条都不满足，等于从未生效）：
 *   1. 必须在工作区数据到位之后调用 —— 否则 state.data.members 为空，直接空跑；
 *   2. 必须把历史占位符（"已登录"）也视为缺失并覆盖 —— 否则脏数据永久驻留，
 *      成员列表、责任人下拉框会一直显示它（线上实际就是这个状态）。
 */
async function syncMemberIdentity() {
  const ident = state.identity || {}
  const uid = ident.id
  const email = ident.email
  if (!uid || (!email && !ident.name)) return
  const need = (state.data.members || []).filter((m) => m.user_id === uid
    && (isPlaceholderIdentity(m.email) || isPlaceholderIdentity(m.display_name)))
  if (!need.length) return

  const patches = new Map()
  for (const m of need) {
    const patch = {
      email: email || null,
      display_name: isPlaceholderIdentity(m.display_name)
        ? (ident.name || emailPrefix(email) || null)
        : m.display_name,
    }
    try {
      const rows = await updateRow('team_members', m.id, patch)
      if (rows && rows.length) patches.set(m.id, patch)
    } catch (err) {
      // 非所有者/管理员改不动成员行（RLS 拦下），属预期：留给团队所有者修正
      log('warn', 'app.member.sync.fail', '回填成员身份失败', describeError(err))
    }
  }
  if (!patches.size) return
  // 本地就地合并，避免为这几行再拉一次全量工作区
  state.data.members = state.data.members.map((m) => (patches.has(m.id) ? { ...m, ...patches.get(m.id) } : m))
  render()
}

/** 未登录时不允许任何读用户数据的动作 —— 这是硬门禁 */
async function guard() {
  const session = await getSession().catch(() => null)
  if (!session?.user) {
    state.user = null
    state.mustChangePassword = false
    showScreen('auth')
    toast('登录状态已失效，请重新登录', 'warn')
    return false
  }
  state.user = session.user
  // 会话刚被服务端标成"必须先改密"（例如另一个标签页触发了重置）：同样不许继续
  if (session.user.mustChangePassword) {
    await handleGateError({ code: 'PASSWORD_CHANGE_REQUIRED' })
    return false
  }
  if (!state.identity?.id) refreshIdentity()
  renderUserChip()
  return true
}

/**
 * 是否是"必须先改初始口令"的闸门错误。
 *
 * 判定只看机读的 `code`，不看 403 这个状态码 —— 同是 403 的还有 `FORBIDDEN` /
 * `NOT_TEAM_ADMIN`，把它们也当成"去改密"会把用户带到错误的页面。
 */
function isPasswordGateError(err) {
  return String(err?.code || '').toUpperCase() === 'PASSWORD_CHANGE_REQUIRED'
}

/**
 * 业务请求撞上闸门：切到强制改密页，**不弹 toast**。
 * toast 3.2 秒就消失，用户只会看到"操作失败"而不知道下一步该做什么。
 */
async function handleGateError(err) {
  log('warn', 'app.gate.password', '业务请求被强制改密闸门拦下，切换到改密页', describeError(err))
  state.mustChangePassword = true
  state.ready = false
  // 把业务数据清掉：改密页背后不应残留上一次的团队内容
  state.data = emptyData()
  closeDrawer()
  closeModal()
  enterPasswordGate()
}

/** 渲染强制改密页；已经在改密页时不再重渲染（否则用户填了一半的输入会被冲掉） */
function enterPasswordGate() {
  const authRoot = $('#auth-root')
  if (authRoot && !authRoot.hidden && authRoot.dataset.rendered === 'gate') return
  showScreen('password')
}

function showScreen(which) {
  const authRoot = $('#auth-root')
  const appRoot = $('#app-root')

  if (which === 'app') {
    authRoot.hidden = true
    appRoot.hidden = false
    resetAuthState()
    delete authRoot.dataset.rendered
    return
  }

  // 认证类整屏（登录 / 强制改密）：业务外壳必须整体隐藏，不能只盖一层
  authRoot.hidden = false
  appRoot.hidden = true

  if (which === 'password') {
    renderPasswordGate(authRoot, {
      email: state.identity?.email || state.user?.email || '',
      onChanged: async (session) => { await applySession(session) },
      onSignOut: async () => { await doSignOut() },
    })
    authRoot.dataset.rendered = 'gate'
    return
  }

  // 登录页每次都重渲染：上一屏可能是改密页，残留的输入/提示不该带到登录页
  resetAuthState()
  renderAuth(authRoot)
  authRoot.dataset.rendered = 'auth'
}

/* ---------------- 数据同步 ---------------- */

async function refresh() {
  state.loading = true
  render()
  let board
  try {
    board = await withTimeout(loadWorkspace(), 30000, '工作区加载')
  } catch (err) {
    state.loading = false
    log('error', 'data.refresh.fail', '工作区加载失败', describeError(err))
    // 闸门错误不是"加载失败"：跳改密页，而不是把错误糊在空白页上
    if (isPasswordGateError(err)) { await handleGateError(err); return }
    toast(friendlyError(err), 'error')
    $('#main').innerHTML = `<div class="loading">加载失败：${escapeHtml(friendlyError(err))}</div>`
    return
  }
  state.loading = false
  state.data = { ...emptyData(), ...board }

  // 团队选择自愈：团队被归档、被移出、或首次登录时，挑一个可用团队
  const teams = (state.data.teams || []).filter((t) => !t.archived)
  if (!teams.some((t) => t.id === state.activeTeamId)) {
    state.activeTeamId = teams.length ? teams[0].id : null
  }
  if (!teams.length && state.space === 'team') {
    state.space = state.data.items.some((i) => i.team_id != null) ? 'personal' : state.space
  }
  if (state.data.teams.length && state.space === 'team' && !state.activeTeamId) {
    state.activeTeamId = state.data.teams[0].id
  }

  // 详情对象可能已被删除或移出可见范围，做一次自愈
  if (state.openItemId && !state.data.items.some((i) => i.id === state.openItemId)) {
    state.openItemId = null
    closeDrawer()
  }
  if (state.openProjectId && !state.data.projects.some((p) => p.id === state.openProjectId)) {
    state.openProjectId = null
    closeDrawer()
  }
  normalizeView()
  saveUiPrefs()
  render()
}

function assertAffected(rows, what = '条目', hint = '') {
  if (!Array.isArray(rows) || rows.length === 0) {
    toast(hint || `未能修改该${what} —— 它可能不存在，或已不在你可见的团队范围内。`, 'warn')
    return false
  }
  return true
}

/**
 * 会写库的 action 清单（写操作需要串行化）。
 *
 * 为什么不锁全部按钮：导航、打开抽屉、切空间必须随时可点，
 * 一律 disabled 会让界面"点了没反应"，反而制造更差的体验。
 * 这里只列真正发写请求的动作，新增写动作时**必须同步加到这里**，
 * 否则就又回到"连点两次插两条"的状态（.smoke/contract-test.mjs 会核对清单）。
 */
const WRITE_ACTIONS = new Set([
  'save-new-item', 'save-item', 'save-project', 'save-context',
  'save-team-new', 'save-join', 'save-team', 'save-wip', 'save-member',
  'save-resource', 'save-oncall', 'submit-password',
  'delete-item', 'delete-project', 'delete-context', 'delete-resource', 'delete-oncall',
  'quick-done', 'set-status', 'add-progress', 'add-to-project',
  'archive-team', 'rotate-invite', 'remove-member', 'finish-review',
  // 建号：漏进这个清单的后果不是"多一条记录"，而是**多一个账号**（没有撤销按钮，只能删库）
  'save-account',
  // 证书：上传会触发服务重启监听，连点两次会让后台连续应用两次
  'upload-cert', 'delete-cert',

  // 集成域：签发/轮换/撤销都可能产生不可逆后果，必须串行化
  'int-create-token', 'int-rotate-token', 'int-revoke-token',])

/**
 * 进入写操作时加锁，返回恢复函数（必须放在 finally 里，异常也要恢复 UI）。
 *
 * 为什么需要它：浮层有 180ms 退场动画，`closeModal()` 只是移除 class，
 * 内容要等动画结束才从 DOM 上摘掉 —— 这段窗口里按钮**仍然可点**，
 * 而 `createItem` 没有幂等键，连点就会重复插入工作项 / 重复建团队 / 重复建项目。
 *
 * 三重保险：①state.busy 做重入拦截（同一 tick 内的第二次点击直接丢弃）；
 * ②把触发元素与同表单内的写按钮置 disabled；③给浮层根节点加 is-busy（CSS 断掉指针事件）。
 */
function beginWrite(action, el) {
  if (state.busy) return null
  state.busy = action

  const overlay = el?.closest?.('#modal-root, #drawer-root') || null
  const host = el?.closest?.('form, .modal-foot, .drawer-foot, .upanel-pwd, #user-panel') || null
  const candidates = new Set()
  if (el?.tagName === 'BUTTON') candidates.add(el)
  if (host) for (const b of host.querySelectorAll('button[data-action]')) candidates.add(b)

  const changed = []
  for (const btn of candidates) {
    if (WRITE_ACTIONS.has(btn.dataset.action) && !btn.hasAttribute('disabled')) {
      btn.setAttribute('disabled', '')
      changed.push(btn)
    }
  }
  if (overlay) {
    overlay.classList.add('is-busy')
    overlay.setAttribute('aria-busy', 'true')
  }

  return () => {
    for (const btn of changed) {
      // data-locked：业务逻辑主动禁用的按钮（例如"重试也不会成功"的引导态），
      // 不该在锁释放时被重新启用 —— 否则按钮会闪一下又变回可点，用户以为可以再试
      if (btn.hasAttribute('data-locked')) continue
      btn.removeAttribute('disabled')
    }
    if (overlay) {
      overlay.classList.remove('is-busy')
      overlay.removeAttribute('aria-busy')
    }
    state.busy = null
  }
}

/* ---------------- 浮层 ---------------- */

/** 退场动画定时器：重新打开浮层时必须取消上一次的清理，否则新内容会被一起抹掉 */
let drawerCloseTimer = null
let modalCloseTimer = null

/**
 * 下一帧执行。
 * 进入动画用 class 切换触发，必须等元素已经插入并布局完成，否则过渡不会播放。
 * 这里保留 requestAnimationFrame 的语义，但加一层兜底：
 * 某些宿主环境（无渲染循环的测试容器、被禁用的浏览器特性）没有 rAF，
 * 若直接调用会抛 ReferenceError，导致浮层停在"已插入但未标记打开"的半开状态。
 */
const nextFrame = (fn) => {
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(fn)
  else setTimeout(fn, 16)
}

function openDrawer(html) {
  clearTimeout(drawerCloseTimer)
  const root = $('#drawer-root')
  root.classList.remove('is-closing')
  root.hidden = false
  root.innerHTML = `<aside class="drawer">${html}</aside>`
  nextFrame(() => root.classList.add('is-open'))
  bindRangeOutputs()
}

function closeDrawer() {
  const root = $('#drawer-root')
  if (!root) return
  root.classList.remove('is-open')
  // 退场动画 180ms 期间内容仍在 DOM 上：立刻加 is-closing 断掉指针事件，
  // 否则这段时间里的点击会落到"已经提交过"的按钮上（重复写库的直接来源）。
  root.classList.add('is-closing')
  clearTimeout(drawerCloseTimer)
  drawerCloseTimer = setTimeout(() => {
    root.hidden = true
    root.innerHTML = ''
    root.classList.remove('is-closing')
  }, 180)
}

function openModal(html) {
  clearTimeout(modalCloseTimer)
  const root = $('#modal-root')
  root.classList.remove('is-closing')
  root.hidden = false
  root.innerHTML = `<div class="modal">${html}</div>`
  nextFrame(() => root.classList.add('is-open'))
  bindRangeOutputs()
}

function closeModal() {
  settleConfirm(false)   // 浮层被关闭（含 ESC / 其它动作）即视为取消，避免 Promise 悬挂
  const root = $('#modal-root')
  if (!root) return
  root.classList.remove('is-open')
  root.classList.add('is-closing')   // 同上：退场期间不可交互
  clearTimeout(modalCloseTimer)
  modalCloseTimer = setTimeout(() => {
    root.hidden = true
    root.innerHTML = ''
    root.classList.remove('is-closing')
  }, 180)
}

/* ---------------- 产品内二次确认（替代原生 confirm） ---------------- */

/** 当前挂起的确认框结算器；同一时刻只允许一个 */
let confirmPending = null

/** 结算挂起的确认框（幂等）：ok=true 表示用户点了确定 */
function settleConfirm(ok) {
  const resolve = confirmPending
  if (!resolve) return
  confirmPending = null
  resolve(!!ok)
}

/**
 * 危险操作的二次确认：复用 #modal-root / .modal 浮层，返回 Promise<boolean>。
 * 原生 confirm 在测试桩里恒 true，"取消" 支路无法验证；产品内模态让取消成为可测的真实分支。
 * 按钮文案与危险色由调用方传入，统一走 var(--danger) 令牌（btn-danger）。
 */
function confirmAction(message, { title = '请确认', okText = '确定', danger = false } = {}) {
  // 上一个确认还未结算（异常路径）就再来一个：先按取消结算，避免 Promise 永久挂起
  settleConfirm(false)
  return new Promise((resolve) => {
    confirmPending = resolve
    openModal(confirmDialogHtml({ title, message, okText, danger }))
  })
}

/* ---------------- 快速收集语法 ---------------- */

/** 支持：#项目  @情境  今天/明天/后天/3天后  其余作为标题 */
function parseCapture(raw, data) {
  const tokens = String(raw).trim().split(/\s+/)
  const words = []
  let projectId = null
  let contextId = null
  let dateStr = null

  for (const tk of tokens) {
    if (tk.startsWith('#') && tk.length > 1) {
      const name = tk.slice(1)
      const hit = (data.projects || []).find((p) => p.team_id === state.activeTeamId && (p.name === name || p.name.startsWith(name)))
      if (hit) projectId = hit.id
      else words.push(tk)
      continue
    }
    if (tk.startsWith('@') && tk.length > 1) {
      const name = tk.slice(1)
      const hit = (data.contexts || []).find((c) => c.name === name || c.name.endsWith(name))
      if (hit) contextId = hit.id
      else words.push(tk)
      continue
    }
    const rel = relativeDate(tk)
    if (rel) { dateStr = rel; continue }
    words.push(tk)
  }
  return { title: words.join(' ').trim(), projectId, contextId, dateStr }
}

function relativeDate(token) {
  const today = todayStr()
  const table = { 今天: 0, 明日: 1, 明天: 1, 后天: 2, 昨天: -1 }
  if (token in table) return addDays(today, table[token])
  const m = String(token).match(/^(\d+)(天|周|月)后$/)
  if (m) {
    const n = Number(m[1])
    if (m[2] === '天') return addDays(today, n)
    if (m[2] === '周') return addDays(today, n * 7)
    return addMonths(today, n)
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(token)) return token
  return null
}

/* ---------------- 重复任务 ---------------- */

function shiftDate(dateStr, interval, unit) {
  if (unit === 'week') return addDays(dateStr, interval * 7)
  if (unit === 'month') return addMonths(dateStr, interval)
  return addDays(dateStr, interval)
}

/** 完成一个重复任务后，生成下一次实例。巡检与例行维护靠它保持节奏。 */
async function spawnNextOccurrence(item) {
  const rec = item.recurrence
  if (!rec?.mode) return
  const today = todayStr()
  let nextStart
  if (rec.mode === 'fixed' && (item.start_date || item.due_date)) {
    let d = item.start_date || item.due_date
    let guard = 0
    do { d = shiftDate(d, rec.interval || 1, rec.unit || 'day'); guard += 1 } while (d <= today && guard < 400)
    nextStart = d
  } else {
    nextStart = shiftDate(today, rec.interval || 1, rec.unit || 'day')
  }
  // SLA 相对时限随实例一并顺延，否则新实例一出生就是超时状态
  let nextSla = null
  if (item.sla_due_at) {
    const hours = Math.max(1, (new Date(item.sla_due_at).getTime() - new Date(item.created_at || Date.now()).getTime()) / 3600000)
    nextSla = new Date(new Date(`${nextStart}T09:00:00`).getTime() + hours * 3600000).toISOString()
  }
  try {
    await createItem({
      title: item.title,
      notes: item.notes ?? null,
      item_type: item.item_type,
      status: 'ready',
      priority: item.priority,
      swimlane: item.swimlane || 'planned',
      team_id: item.team_id ?? null,
      assignee_id: item.assignee_id ?? null,
      assignee_name: item.assignee_name ?? null,
      project_id: item.project_id ?? null,
      area_id: item.area_id ?? null,
      context_id: item.context_id ?? null,
      start_date: nextStart,
      due_date: nextStart,
      recurrence: rec,
      est_hours: item.est_hours ?? null,
      sla_due_at: nextSla,
      progress: 0,
      flagged: !!item.flagged,
      is_next: false,
    })
  } catch (err) {
    log('warn', 'item.recurrence.fail', '生成下一次实例失败', describeError(err))
    toast('下一次实例生成失败，请手动创建', 'warn')
  }
}

/* ---------------- 事件委托 ---------------- */

/**
 * 业务动作的统一错误出口。
 *
 * 存在的理由（两个都必须解决）：
 *  1. **闸门优先**：`403 PASSWORD_CHANGE_REQUIRED` 表达的是"先去改密码"，对任何动作都一样，
 *     所以只在一个地方判定；散在每个动作里迟早漏一个，而漏掉的那一个会把用户留在
 *     "点了没反应"的状态（toast 3.2 秒就消失）。
 *  2. **不留未处理的 Promise 拒绝**：快速收集（回车）与看板拖放都不经过 dispatch，
 *     它们抛错时原来无人接住 —— 线上表现为"按回车没反应且控制台一条红字"。
 *     所有业务入口都必须走这里。
 */
async function runAction(fn, meta = {}) {
  try {
    return await fn()
  } catch (err) {
    if (isPasswordGateError(err)) { await handleGateError(err); return undefined }
    log('error', 'app.action.fail', `交互动作失败：${meta.action || 'unknown'}`, {
      action: meta.action || null, id: meta.id ?? null, ...describeError(err),
    })
    toast(friendlyError(err), 'error')
    return undefined
  }
}

function bindGlobal() {
  document.addEventListener('click', async (e) => {
    const el = e.target.closest('[data-action]')
    if (!el) {
      if (!e.target.closest('#user-panel')) $('#user-panel').hidden = true
      return
    }
    const action = el.dataset.action
    if (action === 'noop') return
    // 写操作重入锁：必须在任何 await 之前**同步**拿到，
    // 否则同一 tick 内的连点会双双通过检查（await 让出了执行权）。
    const unlock = WRITE_ACTIONS.has(action) ? beginWrite(action, el) : null
    if (WRITE_ACTIONS.has(action) && !unlock) return
    try {
      await runAction(() => dispatch(action, el.dataset, el, e), { action, id: el.dataset.id })
    } finally {
      if (unlock) unlock()
    }
  })

  // 注意：不要在 debounce 回调里读 e.target —— 延时期间事件对象可能已被回收
  // （部分环境会把 target 置空），直接读元素本身才是稳定取值方式。
  $('#global-search').addEventListener('input', debounce(() => {
    const q = ($('#global-search')?.value || '').trim()
    state.search = q
    if (q) {
      if (state.view !== 'search') state.searchPrev = state.view
      state.view = 'search'
      render()
      return
    }
    state.view = state.searchPrev || (state.space === 'team' ? 'overview' : 'focus')
    render()
  }, 220))

  $('#global-search').addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      const el = $('#global-search')
      if (el) el.value = ''
      state.search = ''
      state.view = state.searchPrev || (state.space === 'team' ? 'overview' : 'focus')
      render()
    }
  })

  const submitCapture = () => {
    const input = $('#quick-capture')
    const raw = (input?.value || '').trim()
    if (!raw) return
    input.value = ''
    // 必须走 runAction：这条链路不经过 dispatch，抛错时没有任何人接住
    runAction(() => handleCapture(raw), { action: 'quick-capture' })
  }
  $('#quick-submit').addEventListener('click', submitCapture)
  $('#quick-capture').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') { e.preventDefault(); submitCapture() }
  })

  $('#user-btn').addEventListener('click', (e) => {
    e.stopPropagation()
    const panel = $('#user-panel')
    panel.hidden = !panel.hidden
    if (!panel.hidden) {
      const ident = identityOf(state)
      const member = (state.data.members || [])
        .find((m) => m.user_id && m.user_id === ident.id && m.team_id === state.activeTeamId)
      panel.innerHTML = userPanelHtml({
        ...ident,
        teamName: activeTeam()?.name || '',
        roleLabel: member ? (ROLES[member.role]?.label || '') : '',
        space: state.space,
        // 系统管理员在面板里多一个"创建团队管理员"入口 —— 他可能一个团队都不属于，
        // 「成员与角色」页进不去，而引导流程第 4 步就在他手上
        isSystemAdmin: !!state.user?.isSystemAdmin,
      }, APP_VERSION)
    }
  })

  // 团队切换与文件选择：用事件委托接住，因为下拉框/输入框每次渲染都会被替换
  document.addEventListener('change', async (e) => {
    if (e.target?.id === 'team-select') {
      state.activeTeamId = Number(e.target.value)
      state.filters = {}
      normalizeView()
      saveUiPrefs()
      render()
      return
    }
    // 证书文件：选择后读成 PEM 文本回填（后端收 JSON + 文本，不收 multipart）
    if (e.target?.dataset?.action === 'cert-file-picked') {
      await runAction(() => dispatch('cert-file-picked', e.target.dataset, e.target, e), { action: 'cert-file-picked' })
    }
  })

  document.addEventListener('keydown', (e) => {
    const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || '')
    if (e.key === 'Escape') { closeDrawer(); closeModal(); return }
    if (!typing && (e.key === 'n' || e.key === 'N') && !e.metaKey && !e.ctrlKey) {
      e.preventDefault()
      $('#quick-capture').focus()
    }
  })

  bindBoardDnd()
}

async function dispatch(action, ds, el, e) {
  const id = ds.id ? Number(ds.id) : null
  const lk = makeLookups(state.data)

  switch (action) {
    /* ---- 导航与筛选 ---- */
    case 'nav': {
      state.view = ds.view
      state.search = ''
      // REQ-039：筛选键跨视图共用（routine/desk 都用 itemType），不清会带着
      // itemType=inspection 跳到服务台 → 交集为空 → 假「队列已清空」。
      // 必须与 search 同批清空，且早于 saveUiPrefs() 与 system 页早退。
      state.filters = {}
      if ($('#global-search')) $('#global-search').value = ''
      saveUiPrefs()
      // 系统管理页的数据要现拉：证书状态是实例级的，缓存一份旧值比"空着"更危险
      if (state.view === 'system') { await loadCert(); await loadIntegration(); break }
      render()
      break
    }

    case 'switch-space': {
      state.space = ds.space === 'team' ? 'team' : 'personal'
      state.filters = {}
      if (state.space === 'team' && !state.activeTeamId) {
        const t = (state.data.teams || []).find((x) => !x.archived)
        state.activeTeamId = t ? t.id : null
      }
      normalizeView()
      saveUiPrefs()
      render()
      break
    }

    case 'filter': {
      const key = ds.key
      const value = ds.value
      state.filters = { ...state.filters, [key]: value }
      if (ds.goto) state.view = ds.goto
      if (key !== 'context') {
        // 列表类筛选无需跳转；只有情境卡片需要跳到"下一步行动"
      }
      render()
      break
    }

    case 'week': {
      const delta = Number(ds.delta) || 0
      state.weekAnchor = delta === 0 ? todayStr() : addDays(state.weekAnchor, delta)
      render()
      break
    }

    /* ---- 事项 ---- */
    case 'open-item': {
      if (!(await guard())) return
      const item = state.data.items.find((x) => x.id === id)
      if (!item) return toast('该条目不在此范围内', 'warn')
      state.openItemId = id
      openDrawer(itemDrawerHtml(item, state, lk))
      break
    }
    case 'close-drawer': closeDrawer(); state.openItemId = null; state.openProjectId = null; break

    case 'new-item':
      openModal(newItemFormHtml(ds.scope === 'team' ? 'team' : 'personal', ds.type || 'routine', ds.status || 'intake', state, lk))
      setTimeout(() => $('#q-title')?.focus(), 60)
      break

    case 'save-new-item': {
      if (!(await guard())) return
      const scope = ds.scope === 'team' ? 'team' : 'personal'
      const title = ($('#q-title')?.value || '').trim()
      if (!title) return toast('请输入标题', 'warn')
      const itemType = $('#q-type')?.value || 'routine'
      const priority = $('#q-priority')?.value || 'p3'
      const status = $('#q-status')?.value || (scope === 'team' ? 'intake' : 'inbox')
      const due = $('#q-due')?.value || null
      const assigneeId = scope === 'team' ? ($('#q-assignee')?.value || null) : null
      const projectId = scope === 'team' && $('#q-project')?.value ? Number($('#q-project').value) : null
      // 先写库、成功后再关浮层。
      // 反过来（先关后写）会让按钮在 180ms 退场动画里仍然可点，而 createItem 没有幂等键，
      // 连点就会重复插入。失败时保留浮层与已填内容，用户可以直接改完重试。
      const created = await doCreateItem({
        scope, itemType, priority, status, due, assigneeId, projectId, title,
        notes: itemType === 'inspection' ? INSPECTION_TEMPLATE : null,
      })
      if (created) closeModal()
      break
    }

    case 'quick-done': {
      e?.stopPropagation?.()
      if (!(await guard())) return
      const item = state.data.items.find((x) => x.id === id)
      if (!item) return toast('该条目不在此范围内', 'warn')
      const completing = item.status !== 'done'
      const rows = await updateItem(id, {
        status: completing ? 'done' : 'ready',
        completed_at: completing ? isoNow() : null,
        progress: completing ? 100 : item.progress,
        updated_at: isoNow(),
      })
      if (!assertAffected(rows)) return
      if (completing) {
        await spawnNextOccurrence(item)
        toast('已完成')
      }
      await refresh()
      break
    }

    case 'set-status': {
      if (!(await guard())) return
      const rows = await updateItem(id, { status: ds.status, updated_at: isoNow() })
      if (!assertAffected(rows)) return
      await refresh()
      break
    }

    case 'save-item': await saveItem(id); break

    case 'delete-item': {
      if (!(await confirmAction('删除这条记录？该操作不可撤销，相关进度记录也会一并移除。', { title: '删除记录', okText: '确认删除', danger: true }))) return
      if (!(await guard())) return
      const rows = await deleteItem(id)
      if (!assertAffected(rows, '记录')) return
      closeDrawer()
      toast('已删除')
      await refresh()
      break
    }

    case 'add-progress': await addProgress(id); break

    case 'add-to-project': {
      if (!(await guard())) return
      const input = $('#project-quick-add')
      const title = (input?.value || '').trim()
      if (!title) return toast('请输入行动标题', 'warn')
      input.value = ''
      await doCreateItem({ scope: 'team', itemType: 'project_task', priority: 'p3', status: 'ready', projectId: id, title })
      const p = state.data.projects.find((x) => x.id === id)
      if (p) openDrawer(projectDrawerHtml(p, state, makeLookups(state.data)))
      break
    }

    /* ---- 项目 ---- */
    case 'open-project': {
      const p = state.data.projects.find((x) => x.id === id)
      if (!p) return toast('该项目不在此范围内', 'warn')
      state.openProjectId = id
      openDrawer(projectDrawerHtml(p, state, lk))
      break
    }
    case 'new-project': openModal(projectFormHtml(null, state, lk)); break
    case 'edit-project': {
      const p = state.data.projects.find((x) => x.id === id)
      if (!p) return toast('该项目不在此范围内', 'warn')
      openModal(projectFormHtml(p, state, lk))
      break
    }
    case 'save-project': await saveProject(id); break
    case 'delete-project': {
      if (!id) return
      if (!(await confirmAction('删除该项目？项目下的行动不会被删除，只会解除关联。', { title: '删除项目', okText: '确认删除', danger: true }))) return
      if (!(await guard())) return
      const rows = await deleteRow('projects', id)
      if (!assertAffected(rows, '项目')) return
      closeModal(); closeDrawer()
      state.openProjectId = null
      toast('项目已删除')
      await refresh()
      break
    }

    /* ---- 情境 ---- */
    case 'new-context': openModal(contextFormHtml(null, state)); break
    case 'edit-context': {
      const c = state.data.contexts.find((x) => x.id === id)
      if (c) openModal(contextFormHtml(c, state))
      break
    }
    case 'save-context': await saveContext(id); break
    case 'delete-context': {
      if (!(await confirmAction('删除该情境？使用它标记的事项不会被删除，只会失去情境归属。', { title: '删除情境', okText: '确认删除', danger: true }))) return
      if (!(await guard())) return
      const rows = await deleteRow('contexts', id)
      if (!assertAffected(rows, '情境')) return
      closeModal()
      toast('已删除')
      await refresh()
      break
    }

    /* ---- 团队 ---- */
    case 'new-team': openModal(teamFormHtml()); setTimeout(() => $('#t-name')?.focus(), 60); break
    case 'join-team': openModal(joinTeamFormHtml()); setTimeout(() => $('#j-code')?.focus(), 60); break
    case 'save-team-new': await saveTeamNew(); break
    case 'save-join': await saveJoin(); break
    case 'save-team': await saveTeam(); break
    case 'save-wip': await saveWip(); break
    case 'regen-code':
      if ($('#team-code')) $('#team-code').value = makeJoinCode()
      toast('新邀请码已生成，记得点击保存')
      break
    case 'archive-team': await archiveTeam(id); break

    /* ---- 成员 ---- */
    case 'invite-member': openModal(invitePanelHtml(state, lk)); break
    // 建号有两个入口、同一套组件：团队管理员建「成员」（团队与角色都由 UI 收窄），
    // 系统管理员建「团队管理员」（可选团队与角色）。真正的授权在服务端。
    case 'new-member-account':
      openModal(accountFormHtml(state, lk, { mode: 'member' }))
      setTimeout(() => $('#a-email')?.focus(), 60)
      break
    case 'new-admin-account':
      // 从账号面板点进来时面板还开着，先收起，避免浮层被下拉挡住
      if ($('#user-panel')) $('#user-panel').hidden = true
      openModal(accountFormHtml(state, lk, { mode: 'admin' }))
      setTimeout(() => $('#a-email')?.focus(), 60)
      break
    case 'save-account': await saveAccount(ds.mode === 'admin' ? 'admin' : 'member'); break
    case 'copy-temp-password':
      await copyText($('#a-password')?.value || '', '#a-password', '初始密码已复制，请立即转交给本人')
      break
    case 'copy-invite': {
      const code = ds?.code || $('#invite-code')?.value || ''
      if (!code) { toast('邀请码为空，请先换一个', 'warn'); break }
      await copyText(code, '#invite-code', '邀请码已复制，发给对方即可')
      break
    }
    case 'rotate-invite': await rotateInvite(id); break
    case 'edit-member': {
      const m = state.data.members.find((x) => x.id === id)
      if (m) openModal(memberFormHtml(m, state, lk))
      break
    }
    case 'save-member': await saveMember(id); break
    case 'remove-member': await removeMember(id); break

    /* ---- 资源与工具 ---- */
    case 'new-resource': openModal(resourceFormHtml(null, state, lk)); setTimeout(() => $('#r-title')?.focus(), 60); break
    case 'open-resource': {
      const r = state.data.resources.find((x) => x.id === id)
      if (r) openModal(resourceFormHtml(r, state, lk))
      break
    }
    case 'save-resource': await saveResource(id); break
    case 'delete-resource': {
      if (!(await confirmAction('删除该资源条目？该操作不可撤销。', { title: '删除资源', okText: '确认删除', danger: true }))) return
      if (!(await guard())) return
      const rows = await deleteRow('resources', id)
      if (!assertAffected(rows, '资源')) return
      closeModal()
      toast('已删除')
      await refresh()
      break
    }

    /* ---- 值班 ---- */
    case 'new-oncall': openModal(onCallFormHtml(null, state, lk, { date: ds.date, slot: ds.slot })); break
    case 'open-oncall': {
      const r = state.data.onCall.find((x) => x.id === id)
      if (r) openModal(onCallFormHtml(r, state, lk))
      break
    }
    case 'save-oncall': await saveOnCall(id); break
    case 'delete-oncall': {
      if (!(await confirmAction('删除该排班？', { title: '删除排班', okText: '确认删除', danger: true }))) return
      if (!(await guard())) return
      const rows = await deleteRow('on_call', id)
      if (!assertAffected(rows, '排班')) return
      closeModal()
      toast('已删除')
      await refresh()
      break
    }

    /* ---- 复盘 ---- */
    case 'finish-review': await finishReview(ds.kind, ds.key, ds.team); break

    /* ---- 系统管理：HTTPS 证书（实例级） ---- */
    case 'load-cert': await loadCert(); break
    case 'upload-cert': await uploadCertFlow(); break
    case 'delete-cert': await deleteCertFlow(); break
    case 'toggle-cert-paste': {
      const box = $('#cert-paste')
      if (!box) return toast('证书粘贴区未渲染，请刷新页面后重试', 'error')
      box.hidden = !box.hidden
      if (!box.hidden) $('#cert-crt-pem')?.focus()
      break
    }
    case 'cert-file-picked': await certFilePicked(ds.kind, el); break

    /* ---- 系统管理：集成域（本地 API / MCP / 运行日志排障） ---- */
    case 'int-load-tokens': await loadIntegration(); break;
    case 'int-create-token': await createTokenFlow(); break;
    case 'int-rotate-token': await rotateTokenFlow(id); break;
    case 'int-revoke-token': await revokeTokenFlow(id); break;
    case 'int-copy-token':
      await copyText($('#int-token-value')?.value || '', '#int-token-value', '令牌已复制，请立即保存');
      break;
    case 'int-copy-conn': {
      const port = Number(ds.port) || 8722
      const conn = `传入方式：Streamable HTTP\n地址：http://127.0.0.1:${port}/mcp\n认证：Authorization: Bearer <你的令牌>`
      await copyText(conn, '', '连接配置已复制（令牌请自行填写）')
      break
    }
    case 'int-find-log': await findLogFlow(); break;

    /* ---- 账号 ---- */
    case 'close-modal': closeModal(); break
    /* ---- 产品内二次确认：confirmAction() 两个按钮出口，结算挂起的 Promise ---- */
    case 'dialog-confirm': settleConfirm(true); closeModal(); break
    case 'dialog-cancel': settleConfirm(false); closeModal(); break
    case 'change-password': {
      const form = $('#pwd-form')
      if (!form) {
        // 视图层没渲染表单就说明契约断裂（历史缺陷正是如此：点了完全没反应）。
        // 宁可明确报错，也不要让用户对着一个不动的按钮反复点。
        return toast('修改密码面板未加载，请刷新页面后重试', 'error')
      }
      form.hidden = !form.hidden
      if (!form.hidden) {
        showPasswordError('')
        $('#pwd-old')?.focus()
      }
      break
    }
    case 'submit-password': await submitPassword(); break
    case 'sign-out': {
      if (!(await confirmAction('退出登录？', { title: '退出登录', okText: '确认退出' }))) return
      await doSignOut()
      break
    }
    default:
      break
  }
}

/* ---------------- 动作实现：事项 ---------------- */

async function handleCapture(raw) {
  if (!(await guard())) return
  const scope = state.space === 'team' && state.activeTeamId ? 'team' : 'personal'
  const parsed = parseCapture(raw, state.data)
  if (!parsed.title) return toast('请输入有效内容', 'warn')
  await doCreateItem({
    scope,
    title: parsed.title,
    status: 'intake',
    itemType: scope === 'team' ? 'request' : 'routine',
    priority: 'p3',
    projectId: parsed.projectId,
    contextId: parsed.contextId,
    due: parsed.dateStr,
  })
  const bits = [parsed.projectId && '已归到项目', parsed.contextId && '已打情境', parsed.dateStr && '已排日期'].filter(Boolean).join(' · ')
  toast(bits ? `已收录（${bits}）` : (scope === 'team' ? '已登记到团队待受理' : '已收入收集箱'))
}

async function doCreateItem(payload) {
  const lk = makeLookups(state.data)
  const scope = payload.scope === 'team' ? 'team' : 'personal'
  const priority = payload.priority || 'p3'
  const itemType = payload.itemType || 'routine'
  const teamId = scope === 'team' ? state.activeTeamId : null
  if (scope === 'team' && !teamId) {
    toast('请先选择或创建团队', 'warn')
    return false
  }

  // 团队事项默认按优先级目标建立 SLA 时限：没有承诺时限的工作无法被度量
  const targetHours = PRIORITY[priority]?.slaHours || 24
  const slaDue = scope === 'team' ? new Date(Date.now() + targetHours * 3600000).toISOString() : null

  const rows = await createItem({
    title: payload.title,
    notes: payload.notes ?? null,
    gtd_note: null,
    item_type: itemType,
    status: payload.status || (scope === 'team' ? 'intake' : 'inbox'),
    priority,
    swimlane: ITEM_TYPES[itemType]?.lane || 'planned',
    team_id: teamId,
    assignee_id: payload.assigneeId ?? null,
    assignee_name: payload.assigneeId ? (lk.nameOfUser(payload.assigneeId) || null) : null,
    project_id: payload.projectId ?? null,
    area_id: null,
    context_id: payload.contextId ?? null,
    start_date: payload.due || null,
    due_date: payload.due || null,
    recurrence: null,
    est_hours: null,
    progress: 0,
    sla_due_at: slaDue,
    flagged: false,
    is_next: false,
  })
  if (!assertAffected(rows, '工作项')) return false
  await refresh()
  return true
}

async function saveItem(id) {
  if (!(await guard())) return
  const item = state.data.items.find((x) => x.id === id)
  if (!item) return toast('该条目不在此范围内', 'warn')
  const lk = makeLookups(state.data)
  const val = (sel) => $(sel)?.value ?? null

  const title = (val('#f-title') || '').trim()
  if (!title) return toast('标题不能为空', 'warn')

  const scope = item.team_id == null ? 'personal' : 'team'
  const status = val('#f-status') || item.status
  const recMode = val('#f-rec-mode')
  const rec = recMode
    ? { mode: recMode, interval: Math.max(1, Number(val('#f-rec-interval')) || 1), unit: val('#f-rec-unit') || 'day' }
    : null

  const payload = {
    title,
    notes: val('#f-notes') || null,
    gtd_note: val('#f-gtd-note') || null,
    item_type: val('#f-type') || item.item_type,
    status,
    priority: val('#f-priority') || item.priority,
    context_id: val('#f-context') ? Number(val('#f-context')) : null,
    start_date: val('#f-start') || null,
    due_date: val('#f-due') || null,
    est_hours: val('#f-est') !== '' && val('#f-est') != null ? Number(val('#f-est')) : null,
    focus_date: val('#f-focus') || null,
    progress: clamp(Number(val('#f-progress')) || 0, 0, 100),
    waiting_for: val('#f-waiting') || null,
    blocked_reason: val('#f-blocked') || null,
    flagged: !!$('#f-flagged')?.checked,
    is_next: !!$('#f-next')?.checked,
    recurrence: rec,
    completed_at: status === 'done' ? (item.completed_at || isoNow()) : null,
    updated_at: isoNow(),
  }

  if (scope === 'team') {
    const assigneeId = val('#f-assignee') || null
    payload.assignee_id = assigneeId
    payload.assignee_name = assigneeId ? (lk.nameOfUser(assigneeId) || null) : null
    payload.project_id = val('#f-project') ? Number(val('#f-project')) : null
    payload.area_id = val('#f-area') ? Number(val('#f-area')) : null
    payload.swimlane = val('#f-swimlane') || 'planned'
    payload.source = val('#f-source') || null
    const slaVal = val('#f-sla')
    payload.sla_due_at = slaVal ? new Date(slaVal).toISOString() : null
    // 状态推进到"处理中"以上时补记首次响应时间，用于后续度量响应速度
    if (!item.responded_at && ['doing', 'review', 'done'].includes(status)) {
      payload.responded_at = isoNow()
    }
  }

  const rows = await updateItem(id, payload)
  if (!assertAffected(rows)) return

  if (rec && status === 'done' && item.status !== 'done') await spawnNextOccurrence(item)

  closeDrawer()
  state.openItemId = null
  toast('已保存')
  await refresh()
}

async function addProgress(id) {
  if (!(await guard())) return
  const item = state.data.items.find((x) => x.id === id)
  if (!item) return toast('该条目不在此范围内', 'warn')
  const lk = makeLookups(state.data)
  const note = ($('#p-note')?.value || '').trim()
  const kind = $('#p-kind')?.value || 'progress'
  const progress = clamp(Number($('#p-progress')?.value) || 0, 0, 100)
  if (!note) return toast('请填写说明', 'warn')

  // 作者名可能来自成员行（历史脏数据）或身份推断，逐级取第一个非占位值
  const authorName = [lk.nameOfUser(state.user?.id), state.identity?.name, emailPrefix(state.identity?.email)]
    .map((v) => String(v || '').trim())
    .find((v) => v && !isPlaceholderIdentity(v)) || null

  const rows = await createLog({
    team_id: item.team_id ?? null,
    item_id: item.id,
    item_title: item.title,
    kind,
    progress,
    note,
    author_name: authorName,
  })
  if (!assertAffected(rows, '进度记录')) return

  const patch = { progress, updated_at: isoNow(), last_update: note.slice(0, 200) }
  if (kind === 'blocked') patch.status = 'blocked'
  if (progress >= 100) {
    patch.status = 'done'
    patch.completed_at = isoNow()
  }
  // 进度日志与事项更新是两次独立写入：前者成功不代表后者生效。
  // 历史实现漏了这一步校验，policy 拦下（返回空数组）时界面照样显示"进度已写入"——
  // 这正是 README 明令禁止的"假装成功"，也是最难排查的一类数据不一致。
  const patched = await updateItem(id, patch)
  if (!assertAffected(patched, '事项状态', '进度已记录，但事项状态未更新 —— 该事项可能已不在你可见的团队范围内。')) {
    log('warn', 'progress.patch.skipped', '进度日志已写入，但事项更新未生效（可能被权限策略拦下）', { itemId: id })
    await refresh()
    return
  }
  toast('进度已写入')
  await refresh()
  const fresh = state.data.items.find((x) => x.id === id)
  if (fresh && state.openItemId === id) openDrawer(itemDrawerHtml(fresh, state, makeLookups(state.data)))
}

/* ---------------- 动作实现：项目 / 情境 ---------------- */

async function saveProject(id) {
  if (!(await guard())) return
  const name = ($('#p-name')?.value || '').trim()
  if (!name) return toast('项目名称不能为空', 'warn')
  const lk = makeLookups(state.data)
  const leadId = $('#p-lead')?.value || null
  const payload = {
    name,
    description: $('#p-desc')?.value || null,
    kind: $('#p-kind')?.value || 'build',
    status: $('#p-status')?.value || 'active',
    lead_id: leadId,
    lead_name: leadId ? (lk.nameOfUser(leadId) || null) : null,
    area_id: $('#p-area')?.value ? Number($('#p-area').value) : null,
    due_date: $('#p-due')?.value || null,
    progress: clamp(Number($('#p-progress')?.value) || 0, 0, 100),
    updated_at: isoNow(),
  }
  const rows = id
    ? await updateRow('projects', id, payload)
    : await createRow('projects', { ...payload, team_id: state.activeTeamId, sort_order: state.data.projects.length })
  if (!assertAffected(rows, '项目')) return
  closeModal()
  toast(id ? '项目已更新' : '项目已创建')
  await refresh()
}

async function saveContext(id) {
  if (!(await guard())) return
  const name = ($('#c-name')?.value || '').trim().replace(/^@/, '')
  if (!name) return toast('情境名称不能为空', 'warn')
  const scope = $('#c-scope')?.value === 'team' ? 'team' : 'personal'
  if (scope === 'team' && !state.activeTeamId) return toast('请先选择团队', 'warn')
  const payload = {
    name,
    team_id: scope === 'team' ? state.activeTeamId : null,
    updated_at: isoNow(),
  }
  const rows = id
    ? await updateRow('contexts', id, payload)
    : await createRow('contexts', { ...payload, kind: 'mode', sort_order: state.data.contexts.length })
  if (!assertAffected(rows, '情境')) return
  closeModal()
  toast(id ? '情境已更新' : '情境已创建')
  await refresh()
}

/* ---------------- 动作实现：团队 ---------------- */

/**
 * 新建团队后种入默认情境与责任领域。
 * 目的：团队空间从第一分钟起就是可用的，而不是让所有者面对一堆空列表自己想办法。
 */
async function seedTeamDefaults(teamId) {
  const rows = []
  DEFAULT_CONTEXTS.forEach((c, i) => rows.push(
    { table: 'contexts', payload: { team_id: teamId, name: c.name, kind: c.kind, sort_order: i } },
  ))
  DEFAULT_AREAS.forEach((a, i) => rows.push(
    {
      table: 'areas',
      payload: { team_id: teamId, name: a.name, description: a.description, criticality: a.criticality, sort_order: i },
    },
  ))
  for (const r of rows) {
    try {
      await createRow(r.table, r.payload)
    } catch (err) {
      log('warn', 'team.seed.fail', `初始化默认数据失败：${r.table}`, describeError(err))
    }
  }
}

async function saveTeamNew() {
  if (!(await guard())) return
  const name = ($('#t-name')?.value || '').trim()
  if (!name) return toast('请输入团队名称', 'warn')
  const description = ($('#t-desc')?.value || '').trim() || null
  const mission = ($('#t-mission')?.value || '').trim() || null
  const joinCode = makeJoinCode()

  const team = await createTeamWithOwner({
    name,
    description,
    mission,
    joinCode,
    ownerEmail: state.identity?.email || '',
    ownerName: state.identity?.name || '',
  })
  if (!state.identity?.email) {
    // 宁可留空也不要写占位文案：owner 行之后可由本人在成员页补全
    log('warn', 'team.create.no-email', '创建团队时未能取得账号邮箱，所有者显示名可能不完整')
  }
  await seedTeamDefaults(team.id)
  closeModal()
  state.space = 'team'
  state.activeTeamId = team.id
  state.view = 'overview'
  toast(`团队已创建，邀请码 ${joinCode}`)
  await refresh()
}

async function saveJoin() {
  if (!(await guard())) return
  const code = ($('#j-code')?.value || '').trim()
  if (!code) return toast('请输入邀请码', 'warn')
  const teamId = await joinTeamByCode(code)
  closeModal()
  state.space = 'team'
  state.activeTeamId = Number(teamId)
  state.view = 'overview'
  toast('已加入团队，可以在团队空间看到共享工作了')
  await refresh()
}

async function saveTeam() {
  if (!(await guard())) return
  const id = state.activeTeamId
  const team = activeTeam()
  if (!team || team.id !== id) return toast('请先选择团队', 'warn')
  const name = ($('#team-name')?.value || '').trim()
  if (!name) return toast('团队名称不能为空', 'warn')
  const code = ($('#team-code')?.value || '').trim().toUpperCase()
  if (!code) return toast('邀请码不能为空', 'warn')
  const rows = await updateRow('teams', id, {
    name,
    description: ($('#team-desc')?.value || '').trim() || null,
    mission: ($('#team-mission')?.value || '').trim() || null,
    join_code: code,
    updated_at: isoNow(),
  })
  if (!assertAffected(rows, '团队')) return
  toast('团队信息已保存')
  await refresh()
}

async function saveWip() {
  if (!(await guard())) return
  const id = state.activeTeamId
  const team = activeTeam()
  if (!team) return toast('请先选择团队', 'warn')
  const cfg = {}
  for (const col of ['intake', 'triage', 'ready', 'doing', 'review', 'blocked']) {
    const v = Number($(`#wip-${col}`)?.value)
    cfg[col] = Number.isFinite(v) ? clamp(v, 0, 99) : 0
  }
  const rows = await updateRow('teams', id, { board_config: cfg, updated_at: isoNow() })
  if (!assertAffected(rows, '团队设置')) return
  toast('看板上限已更新')
  await refresh()
}

async function archiveTeam(id) {
  if (!id) return
  if (!(await confirmAction('归档该团队？归档后团队不再出现在导航中，数据会保留，但成员将看不到它。', { title: '归档团队', okText: '确认归档', danger: true }))) return
  if (!(await guard())) return
  const rows = await updateRow('teams', id, { archived: true, updated_at: isoNow() })
  if (!assertAffected(rows, '团队')) return
  state.space = 'personal'
  state.view = 'focus'
  normalizeView()
  toast('团队已归档')
  await refresh()
}

/**
 * 换一个邀请码。立即写库生效，旧码随之失效（已加入的成员不受影响）。
 * 之所以要能轮换：邀请码等于"团队准入凭证"，人员流动后必须能作废重发。
 */
async function rotateInvite(teamIdRaw) {
  if (!(await guard())) return
  const teamId = Number(teamIdRaw) || state.activeTeamId
  if (!teamId) return toast('请先选择团队', 'warn')
  const lk = makeLookups(state.data)
  const me = lk.memberOf(teamId, state.user?.id)
  if (!me || (me.role !== 'owner' && me.role !== 'admin')) {
    return toast('只有团队所有者或管理员可以轮换邀请码', 'warn')
  }
  let code = makeJoinCode()
  let rows = await updateRow('teams', teamId, { join_code: code, updated_at: isoNow() })
  // 极小概率撞上已有邀请码：唯一索引拒绝后自动重试一次
  if (!rows || !rows.length) {
    code = makeJoinCode()
    rows = await updateRow('teams', teamId, { join_code: code, updated_at: isoNow() })
  }
  if (!assertAffected(rows, '邀请码')) return
  const el = $('#invite-code')
  if (el) el.value = code
  const btn = $('[data-action="copy-invite"]')
  if (btn) btn.setAttribute('data-code', code)
  toast('已生成新的邀请码，旧码立即失效')
  await refresh()
}

async function saveMember(id) {
  if (!(await guard())) return
  const teamId = state.activeTeamId
  if (!teamId) return toast('请先选择团队', 'warn')
  if (!id) return toast('成员关系只能由本人凭邀请码建立', 'warn')
  const lk = makeLookups(state.data)
  const memberId = Number(id)
  const target = lk.memberById.get(memberId)
  if (!target) return toast('找不到该成员', 'warn')
  // 邮箱是账号标识，不随成员记录变更；只调整可治理的属性
  const payload = {
    display_name: ($('#m-name')?.value || '').trim() || target.email?.split('@')[0] || null,
    role: $('#m-role')?.value || target.role || 'member',
    title: ($('#m-title')?.value || '').trim() || null,
    capacity: clamp(Number($('#m-capacity')?.value) || 6, 1, 50),
    status: $('#m-status')?.value || 'active',
    updated_at: isoNow(),
  }
  // 不允许把成员行降级成"零个所有者"
  if (target.role === 'owner' && payload.role !== 'owner') {
    const owners = lk.members(teamId).filter((m) => m.role === 'owner')
    if (owners.length <= 1) return toast('团队至少需要保留一名所有者', 'warn')
  }
  // 自己把自己停用会导致立刻失去团队访问权，先确认
  if (target.user_id === state.user?.id && payload.status !== 'active') {
    if (!(await confirmAction('停用自己的账号会立即失去该团队的访问权限，需要其他所有者或管理员重新启用。继续？', { title: '停用自己', okText: '确认停用', danger: true }))) return
  }
  const rows = await updateRow('team_members', memberId, payload)
  if (!assertAffected(rows, '成员')) return
  closeModal()
  toast('成员信息已更新')
  await refresh()
}

async function removeMember(id) {
  if (!id) return
  const lk = makeLookups(state.data)
  const target = lk.memberById.get(Number(id))
  if (!target) return
  if (target.role === 'owner') return toast('无法移出团队所有者', 'warn')
  if (!(await confirmAction(`将「${target.display_name || target.email || '该成员'}」移出团队？其已承接的工作不会自动转移。`, { title: '移出团队', okText: '确认移出', danger: true }))) return
  if (!(await guard())) return
  const rows = await deleteRow('team_members', Number(id))
  if (!assertAffected(rows, '成员')) return
  toast('已移出团队')
  await refresh()
}

async function saveResource(id) {
  if (!(await guard())) return
  const teamId = state.activeTeamId
  if (!teamId) return toast('请先选择团队', 'warn')
  const lk = makeLookups(state.data)
  const title = ($('#r-title')?.value || '').trim()
  if (!title) return toast('名称不能为空', 'warn')
  const custodianId = $('#r-custodian')?.value || null
  const vendor = ($('#r-vendor')?.value || '').trim() || null
  const renewal = $('#r-renewal')?.value || null
  // 链接协议白名单（与服务端同批）。`<input type="url">` 只校验"像不像 URL"，
  // 不限制 scheme —— `javascript:` / `data:` 都能填进来，随后被渲染成可点的 href。
  const url = ($('#r-url')?.value || '').trim()
  if (url && !urlSafety(url).safe) {
    toast('链接只支持 http / https / mailto 协议', 'warn')
    return
  }
  const payload = {
    kind: $('#r-kind')?.value || 'runbook',
    title,
    category: ($('#r-category')?.value || '').trim() || null,
    url: url || null,
    description: ($('#r-desc')?.value || '').trim() || null,
    custodian_id: custodianId,
    custodian_name: custodianId ? (lk.nameOfUser(custodianId) || null) : null,
    criticality: $('#r-critical')?.value || 'normal',
    review_due: $('#r-review')?.value || null,
    pinned: !!$('#r-pinned')?.checked,
    meta: { vendor, renewal_date: renewal },
    updated_at: isoNow(),
  }
  const rows = id
    ? await updateRow('resources', Number(id), payload)
    : await createRow('resources', { ...payload, team_id: teamId })
  if (!assertAffected(rows, '资源')) return
  closeModal()
  toast(id ? '已更新' : '已登记')
  await refresh()
}

async function saveOnCall(id) {
  if (!(await guard())) return
  const teamId = state.activeTeamId
  if (!teamId) return toast('请先选择团队', 'warn')
  const lk = makeLookups(state.data)
  const date = $('#o-date')?.value
  if (!date) return toast('请选择日期', 'warn')
  const primaryId = $('#o-primary')?.value || null
  const backupId = $('#o-backup')?.value || null
  const payload = {
    shift_date: date,
    slot: $('#o-slot')?.value || 'day',
    primary_id: primaryId,
    primary_name: primaryId ? (lk.nameOfUser(primaryId) || null) : null,
    backup_id: backupId,
    backup_name: backupId ? (lk.nameOfUser(backupId) || null) : null,
    notes: ($('#o-notes')?.value || '').trim() || null,
    handover_notes: ($('#o-handover')?.value || '').trim() || null,
    updated_at: isoNow(),
  }
  const rows = id
    ? await updateRow('on_call', Number(id), payload)
    : await createRow('on_call', { ...payload, team_id: teamId, status: 'planned' })
  if (!assertAffected(rows, '排班')) return
  closeModal()
  toast(id ? '排班已更新' : '排班已保存')
  await refresh()
}

/* ---------------- 动作实现：复盘 / 账号 ---------------- */

async function finishReview(kind, key, teamIdRaw) {
  if (!(await guard())) return
  const teamId = teamIdRaw ? Number(teamIdRaw) : null
  const lk = makeLookups(state.data)
  let notes = ''
  let stats = null

  if (teamId) {
    notes = ($('#team-weekly-note')?.value || '').trim()
    const lkTeam = lk.team(teamId)
    const m = computeTeamSnapshot(teamId, lkTeam, lk)
    stats = m
  } else if (kind === 'daily') {
    notes = ($('#daily-note')?.value || '').trim()
    stats = personalSnapshot()
  } else {
    notes = ($('#weekly-note')?.value || '').trim()
    stats = personalSnapshot()
  }

  // 同一 (账号, 团队, 类型, 周期) 只保留一条：已有则改，没有则建。
  // 冲突判定放在应用层，避免依赖服务端对表达式唯一索引的 ON CONFLICT 推断。
  const exist = (state.data.reviews || []).find((r) => r.kind === kind
    && r.period_key === key
    && (r.team_id ?? null) === teamId)

  try {
    const payload = {
      team_id: teamId,
      kind,
      period_key: key,
      notes: notes || null,
      stats,
      completed_at: isoNow(),
    }
    const rows = await saveReview(payload, exist?.id)
    if (!assertAffected(rows, '复盘记录')) return
    toast(teamId ? '团队复盘已记录' : (kind === 'weekly' ? '本周复盘已完成，系统回到可信状态' : '今日回顾已保存'))
    await refresh()
  } catch (err) {
    log('error', 'review.save.fail', '复盘记录保存失败', describeError(err))
    toast(friendlyError(err), 'error')
  }
}

function personalSnapshot() {
  const uid = state.user?.id
  const mine = [...personalItems(state.data, uid), ...assignedToMe(state.data, uid)].filter(isOpen)
  return {
    open: mine.length,
    inbox: mine.filter((i) => i.status === 'intake').length,
    triage: mine.filter((i) => i.status === 'triage').length,
    ready: mine.filter((i) => i.status === 'ready').length,
    waiting: mine.filter((i) => i.status === 'waiting').length,
    someday: mine.filter((i) => i.status === 'someday').length,
    capturedAt: isoNow(),
  }
}

function computeTeamSnapshot(teamId, team, lk) {
  const items = (state.data.items || []).filter((i) => i.team_id === teamId)
  const active = items.filter(isOpen)
  const done = items.filter((i) => i.status === 'done')
  const slaScoped = done.filter((i) => i.sla_due_at && i.completed_at)
  const slaOk = slaScoped.filter((i) => new Date(i.completed_at) <= new Date(i.sla_due_at)).length
  return {
    team: team?.name || null,
    total: items.length,
    active: active.length,
    done: done.length,
    blocked: active.filter((i) => i.status === 'blocked').length,
    unplanned: items.filter((i) => i.swimlane === 'unplanned').length,
    slaOk,
    slaScoped: slaScoped.length,
    capturedAt: isoNow(),
  }
}

/**
 * 修改密码。
 *
 * 口令规则与后端 `pyapp/security.py` 的 validate_password() 完全对齐
 * （MIN=8 / MAX=200，常量来自 config.js）—— 前端拦下的必须正好是服务端会拒绝的，
 * 否则用户会在"前端说可以、服务端说不行"之间反复试错。
 *
 * 失败时不能只丢一个 3.2 秒就消失的 toast：错误文案要留在表单里，
 * 用户回看时才知道是哪一步不对。
 */
async function submitPassword() {
  const oldEl = $('#pwd-old')
  const newEl = $('#pwd-new')
  const confirmEl = $('#pwd-confirm')
  const oldPassword = oldEl?.value || ''
  const newPassword = newEl?.value || ''
  const confirm = confirmEl?.value || ''

  if (!oldPassword) return showPasswordError('请输入当前密码')
  if (!newPassword) return showPasswordError('请输入新密码')
  if (newPassword.length < MIN_PASSWORD_LENGTH) return showPasswordError(`新密码至少 ${MIN_PASSWORD_LENGTH} 位`)
  if (newPassword.length > MAX_PASSWORD_LENGTH) return showPasswordError(`新密码最长 ${MAX_PASSWORD_LENGTH} 位`)
  if (newPassword !== confirm) return showPasswordError('两次输入的新密码不一致')

  try {
    await withTimeout(changePassword(oldPassword, newPassword), 30000, '修改密码')
    // 成功：清空输入框（不把口令留在 DOM 里）并收起表单
    if (oldEl) oldEl.value = ''
    if (newEl) newEl.value = ''
    if (confirmEl) confirmEl.value = ''
    showPasswordError('')
    const form = $('#pwd-form')
    if (form) form.hidden = true
    $('#user-panel').hidden = true
    toast('密码已更新，其它设备上的登录已失效')
  } catch (err) {
    log('warn', 'account.password.fail', '修改密码失败', describeError(err))
    const message = friendlyError(err)
    showPasswordError(message)
    toast(message, 'error')
  }
}

/** 在修改密码表单内就地显示错误（面板不关，用户可以直接改了重试） */
function showPasswordError(message) {
  const host = $('#pwd-error')
  if (!host) {
    if (message) toast(message, 'warn')
    return
  }
  host.textContent = message || ''
  host.hidden = !message
}

/**
 * 退出登录。
 *
 * 抽成函数是因为它有两条入口：账号面板的「退出登录」（要二次确认），
 * 以及强制改密页的「换一个账号登录」（不需要确认 —— 那一屏没有别的出路）。
 * 两条入口必须做完全一样的状态清理，否则会出现"改密页登出后还留着上一个人的业务数据"。
 */
async function doSignOut() {
  await signOut()
  state.data = emptyData()
  state.user = null
  state.profile = null
  state.identity = { id: '', name: '', email: '' }
  state.ready = false
  state.mustChangePassword = false
  state.cert = { loaded: false, loading: false, error: '', applying: false, data: null }
  resetAuthState()
  closeDrawer(); closeModal()
  showScreen('auth')
  toast('已退出登录')
}

/**
 * 复制一段文本到剪贴板（邀请码与一次性初始口令共用）。
 *
 * 剪贴板在非安全上下文（http 局域网地址）或未授权时会抛错 —— 这不是异常路径而是常态，
 * 所以失败时退化为"把内容选中，你自己 Ctrl+C"，并把原因说清楚，
 * 而不是只报一句"复制失败"让用户卡住。
 */
async function copyText(text, fieldSelector, okMessage) {
  const value = String(text || '')
  if (!value) { toast('内容为空，无法复制', 'warn'); return }
  try {
    await navigator.clipboard.writeText(value)
    toast(okMessage)
  } catch {
    const el = fieldSelector ? $(fieldSelector) : null
    if (el) {
      el.removeAttribute('readonly')
      el.select?.()
      el.setAttribute('readonly', '')
    }
    toast('已为你选中内容，请手动复制', 'warn')
  }
}

/**
 * 建号（团队管理员建成员 / 系统管理员建团队管理员）。
 *
 * 三条与"一次性口令"直接相关的纪律：
 *   1. 响应里的 `password` 只交给 `accountCreatedHtml()` 渲染一次，
 *      **不进 state**（`state.data` / `state.user` 里都没有它的位置）、
 *      **不写 localStorage**、**不传进 log()**；
 *   2. 失败时把服务端文案留在表单内联提示里（不是一闪而过的 toast），并保留已填内容；
 *   3. 被闸门拦下（403 PASSWORD_CHANGE_REQUIRED）时不报"建号失败"，而是先去改密。
 */
async function saveAccount(mode) {
  if (!(await guard())) return
  const email = ($('#a-email')?.value || '').trim()
  const name = ($('#a-name')?.value || '').trim()
  if (!email) return showAccountError('请输入邮箱地址')
  if (!isValidEmail(email)) return showAccountError('请输入有效的邮箱地址')

  let teamId = null
  let role = null
  if (mode === 'member') {
    // 团队管理员入口：团队锁死为当前团队，角色恒为 member（服务端仍会再判一次）
    teamId = state.activeTeamId
    if (!teamId) return showAccountError('请先选择团队')
    role = 'member'
  } else {
    const picked = $('#a-team')?.value || ''
    teamId = picked ? Number(picked) : null
    // `06` v1.3：系统管理员只能建 admin，且 role 必须与 teamId **同现同缺**
    // —— 不选团队时传 role 会稳定 400（ROLE_NOT_ALLOWED），所以这里给 null 而不是回填 member
    role = teamId ? 'admin' : null
  }

  showAccountError('')
  let result
  try {
    result = await withTimeout(createAccount({ email, name, teamId, role }), 30000, '创建账号')
  } catch (err) {
    // 注意：这里只记录邮箱 / 团队 / 角色，绝不记录任何口令（请求里本来也没有口令）
    log('error', 'account.create.fail', '创建账号失败', { email, teamId, role, ...describeError(err) })
    if (isPasswordGateError(err)) { await handleGateError(err); return }
    // 引导态不是"你没权限"，而是实例还没初始化：按操作指引渲染，别让用户对着重试按钮点
    if (String(err?.code || '').toUpperCase() === 'NO_SYSTEM_ADMIN') {
      showAccountError(friendlyError(err), { guide: true })
      return
    }
    showAccountError(accountErrorText(err))
    return
  }

  log('info', 'account.create.ok', '账号已创建', {
    email, teamId, role, targetUserId: result?.user?.id || null,
  })
  // 团队名只在展示时从本地数据补（响应里只有 teamId）；角色读响应，不硬编码
  const teamName = result?.teamId
    ? ((state.data.teams || []).find((t) => t.id === Number(result.teamId))?.name || '')
    : ''
  // 一次性口令在这里交接给视图：只存在于 modal 的 DOM 中，关闭即消失
  openModal(accountCreatedHtml(result, { teamName }))
  await refresh()
}

/** 建号失败文案：EMAIL_EXISTS 要指向**正确的出路**，不能只说"已注册" */
function accountErrorText(err) {
  if (String(err?.code || '').toUpperCase() === 'EMAIL_EXISTS') {
    // 管理员没有"把人加入团队"的能力（join 用的是当前会话身份），所以出路只有两条：
    // 换邮箱，或把邀请码发给对方、由对方登录后自行加入（`10` EX-026 / `06` §10.3.5）
    return '该邮箱已有账号。请把邀请码发给对方，由对方登录后自行加入；若对方无法登录，请联系运维重置密码。'
  }
  return friendlyError(err)
}

/** 在建号表单内就地显示错误（表单不关，用户可以直接改了重试） */
function showAccountError(message, { guide = false } = {}) {
  const host = $('#a-error')
  if (!host) {
    if (message) toast(message, guide ? 'warn' : 'error')
    return
  }
  // guide=true 用于"这不是你没权限，而是实例还没初始化"这类**操作指引**：
  // 渲染成灰底说明而不是红字报错，并禁掉提交按钮 —— 重试永远不会成功。
  host.classList.toggle('is-guide', !!guide)
  host.textContent = message || ''
  host.hidden = !message
  if (guide) {
    const btn = $('[data-action="save-account"]')
    if (btn) {
      // data-locked 让 beginWrite 的解锁逻辑别把这次禁用撤销掉（见 beginWrite）
      btn.setAttribute('disabled', '')
      btn.setAttribute('data-locked', '')
    }
  }
}

/* ---------------- 系统管理：HTTPS 证书 ---------------- */

const CERT_POLL_TIMES = 6              // 最多查 6 次
const CERT_POLL_INTERVAL_MS = 5000     // 每次间隔 5 秒 → 总计约 30 秒
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

/**
 * 读取证书状态。
 *
 * 非系统管理员直接跳过：那一页对他们是"权限不足"，打了也只会拿到 403，
 * 白白在后台日志里留下一条越权记录。
 */
/**
 * 证书状态刷新时保住用户已粘贴的草稿（N-02）。
 * render() 会整套重建 #main，而两个 textarea 没有 value 绑定 → 草稿被冲掉。
 * 快照只取 DOM 现值、只在本次同步块内存活，不进 state / localStorage / log()。
 * 每次 render 前重新取样：await 期间用户新输入的内容同样要保住。
 */
function renderKeepingCertDraft() {
  const draft = {
    crt: $('#cert-crt-pem')?.value,
    key: $('#cert-key-pem')?.value,
    crtName: $('#cert-crt-name')?.textContent,
    keyName: $('#cert-key-name')?.textContent,
  }
  render()
  if (draft.crt !== undefined) { const el = $('#cert-crt-pem'); if (el) el.value = draft.crt }
  if (draft.key !== undefined) { const el = $('#cert-key-pem'); if (el) el.value = draft.key }
  if (draft.crtName !== undefined) { const el = $('#cert-crt-name'); if (el) el.textContent = draft.crtName }
  if (draft.keyName !== undefined) { const el = $('#cert-key-name'); if (el) el.textContent = draft.keyName }
}

async function loadCert() {
  if (!state.user?.isSystemAdmin) {
    state.cert = { loaded: false, loading: false, error: '', applying: false, data: null }
    renderKeepingCertDraft()
    return
  }
  state.cert.loading = true
  state.cert.error = ''
  renderKeepingCertDraft()
  try {
    state.cert.data = await withTimeout(getCert(), 20000, '读取证书状态')
    state.cert.loaded = true
  } catch (err) {
    if (isPasswordGateError(err)) { await handleGateError(err); return }
    log('warn', 'cert.load.fail', '读取证书状态失败', describeError(err))
    state.cert.error = friendlyError(err)
  } finally {
    state.cert.loading = false
  }
  renderKeepingCertDraft()
}

/** 在证书卡内就地显示错误（不重渲染，保住用户已经粘贴的文本） */
function showCertError(message) {
  const host = $('#cert-error')
  if (!host) {
    if (message) toast(message, 'warn')
    return
  }
  host.textContent = message || ''
  host.hidden = !message
}

/**
 * 选择证书/私钥文件后读成 PEM 文本。
 *
 * 用 FileReader 而不是 FormData：后端收的是 JSON + PEM 文本（证书契约：主理人下发），
 * 不是 multipart 上传。读完自动展开粘贴区，让用户能核对内容再提交。
 */
async function certFilePicked(kind, el) {
  const file = el?.files?.[0]
  if (!file) return                       // 只是点开了选择框，还没选文件
  if (typeof FileReader === 'undefined') {
    return showCertError('当前环境不支持读取本地文件，请展开「直接粘贴 PEM 文本」手工粘贴')
  }
  const text = await new Promise((resolve) => {
    try {
      const reader = new FileReader()
      reader.onload = () => resolve(String(reader.result || ''))
      reader.onerror = () => resolve(null)
      reader.readAsText(file)
    } catch {
      resolve(null)
    }
  })
  if (text == null) return showCertError(`读取 ${file.name} 失败，请改用「直接粘贴 PEM 文本」`)

  const target = $(kind === 'key' ? '#cert-key-pem' : '#cert-crt-pem')
  if (target) target.value = text
  const nameHost = $(kind === 'key' ? '#cert-key-name' : '#cert-crt-name')
  if (nameHost) nameHost.textContent = `${file.name}（${Math.max(1, Math.round(file.size / 1024))} KB）`
  const box = $('#cert-paste')
  if (box) box.hidden = false            // 展开让用户核对，避免"选错文件却不知道"
  showCertError('')
}

/**
 * 上传证书。
 *
 * 前端只做**轻量预校验**（早点报错），真正的校验证书与私钥是否配对、是否过期、
 * 私钥是否带口令全在服务端 —— 这三件事前端做不了，也不该假装能做。
 */
async function uploadCertFlow() {
  if (!(await guard())) return
  if (!state.user?.isSystemAdmin) return toast('只有系统管理员可以管理证书', 'warn')

  const certPem = ($('#cert-crt-pem')?.value || '').trim()
  const keyPem = ($('#cert-key-pem')?.value || '').trim()
  // ⚠️ 以下前置校验**不清空任何输入框**：这些分支返回时请求还没发出去，
  // 用户刚粘贴的内容还没离开过本机，清掉等于毁掉他正在改的东西（见清空策略表）。
  if (!certPem) return showCertError('请选择证书文件，或展开「直接粘贴 PEM 文本」粘贴证书内容')
  if (!keyPem) return showCertError('请选择私钥文件，或展开「直接粘贴 PEM 文本」粘贴私钥内容')
  if (!/-----BEGIN CERTIFICATE-----/.test(certPem)) {
    return showCertError('证书文本里找不到 -----BEGIN CERTIFICATE-----，请粘贴完整的 PEM（含 BEGIN/END 行）')
  }
  // 私钥可能是 PRIVATE KEY / RSA PRIVATE KEY / EC PRIVATE KEY
  if (!/-----BEGIN [A-Z ]*PRIVATE KEY-----/.test(keyPem)) {
    return showCertError('私钥文本里找不到 -----BEGIN ... PRIVATE KEY-----，请粘贴完整的 PEM（含 BEGIN/END 行）')
  }
  showCertError('')

  /**
   * 私钥的清空策略（三个时机分开判，不要一刀切）：
   *   提交成功 → 证书清、私钥清（都已入库，页面上不该再有）
   *   提交失败 → 证书**留**、私钥**清**（证书不是秘密，留着方便"只换私钥再试"；私钥是秘密）
   *   未提交（上面的前置校验）→ 都不清（用户还没把内容发出去）
   * 失败路径原来只 `return`、不清空，导致私钥原文一直留在 DOM 里直到手清或刷新 ——
   * 服务端此刻已经收到过这份私钥，页面再留着它没有任何好处。
   */
  let data
  try {
    // 私钥是明文，只在这一次请求体里出现：不写 state、不写 localStorage、不进 log()
    data = await withTimeout(uploadCert({ certPem, keyPem }), 60000, '上传证书')
  } catch (err) {
    // 先清私钥再分流：闸门跳转同样不能把私钥带过去
    clearKeyInput()
    if (isPasswordGateError(err)) { await handleGateError(err); return }
    log('error', 'cert.upload.fail', '上传证书失败', { certBytes: certPem.length, keyBytes: keyPem.length, ...describeError(err) })
    showCertError(friendlyError(err))
    return
  }

  clearKeyInput('提交成功后已清空')
  clearCertInput()
  log('info', 'cert.upload.ok', '证书已上传', { source: data?.source || null, pendingApply: !!data?.pendingApply })
  state.cert.data = data
  state.cert.loaded = true
  state.cert.error = ''
  render()
  toast('证书已保存')
  if (data?.pendingApply) await pollCertApply()
}

/** 清空私钥输入框（一次性敏感材料：提交过之后就不该继续留在 DOM 里） */
function clearKeyInput(reason = '提交失败后已清空') {
  const el = $('#cert-key-pem')
  if (el) el.value = ''
  // 文件名的标签也要跟着复位，否则会出现"显示已选某文件、内容却是空的"这种自相矛盾
  const nameEl = $('#cert-key-name')
  if (nameEl) nameEl.textContent = `未选择文件（${reason}，请重新选择或粘贴）`
}

/** 清空证书输入框 */
function clearCertInput() {
  const el = $('#cert-crt-pem')
  if (el) el.value = ''
  const nameEl = $('#cert-crt-name')
  if (nameEl) nameEl.textContent = '未选择文件'
}

/** 移除证书（回退到 HTTP 明文 / 运维自签名） */
async function deleteCertFlow() {
  if (!(await confirmAction('移除当前证书？移除后将回退到 HTTP 明文或运维自签名证书，浏览器会提示不安全，且后台需要重新加载监听。', { title: '移除证书', okText: '确认移除', danger: true }))) return
  if (!(await guard())) return
  if (!state.user?.isSystemAdmin) return toast('只有系统管理员可以管理证书', 'warn')

  let data
  try {
    data = await withTimeout(deleteCert(), 30000, '移除证书')
  } catch (err) {
    if (isPasswordGateError(err)) { await handleGateError(err); return }
    log('error', 'cert.delete.fail', '移除证书失败', describeError(err))
    toast(friendlyError(err), 'error')
    return
  }
  log('info', 'cert.delete.ok', '证书已移除', { pendingApply: !!data?.pendingApply })
  state.cert.data = data
  state.cert.loaded = true
  state.cert.error = ''
  render()
  toast('证书已移除')
  if (data?.pendingApply) await pollCertApply()
}

/**
 * 等后台应用新证书。
 *
 * 为什么要轮询：`POST/DELETE /api/admin/cert` 都是"写配置"，生效要等后台重新加载监听
 * （约 1 分钟）。不轮询的话用户会看着旧证书状态以为没生效，然后再传一遍。
 * 查满 CERT_POLL_TIMES 仍为 pendingApply 就如实说"没在预期时间内生效"，
 * 不要假装成功 —— 那种"看起来成功了其实没生效"最难排查。
 */
async function pollCertApply() {
  state.cert.applying = true
  render()
  for (let i = 0; i < CERT_POLL_TIMES; i += 1) {
    await sleep(CERT_POLL_INTERVAL_MS)
    try {
      const data = await getCert()
      state.cert.data = data
      state.cert.loaded = true
      if (!data?.pendingApply) {
        state.cert.applying = false
        render()
        toast('新证书已生效')
        return
      }
      render()   // 更新"正在应用…"，让用户看到还在进行
    } catch (err) {
      log('warn', 'cert.poll.fail', '轮询证书生效状态失败', describeError(err))
      break
    }
  }
  state.cert.applying = false
  render()
  toast('已保存，未在预期时间内生效，请检查后台服务', 'warn')
}

/* ---------------- 看板拖放 ---------------- */

let dragId = null

function bindBoardDnd() {
  document.addEventListener('dragstart', (e) => {
    const card = e.target.closest?.('.bcard')
    if (!card) return
    dragId = Number(card.dataset.id)
    card.classList.add('is-dragging')
  })
  document.addEventListener('dragend', (e) => {
    e.target.closest?.('.bcard')?.classList.remove('is-dragging')
    $$('.bcol.is-over').forEach((c) => c.classList.remove('is-over'))
  })
  document.addEventListener('dragover', (e) => {
    const col = e.target.closest?.('.bcol')
    if (!col) return
    e.preventDefault()
    $$('.bcol.is-over').forEach((c) => { if (c !== col) c.classList.remove('is-over') })
    col.classList.add('is-over')
  })
  document.addEventListener('drop', async (e) => {
    const col = e.target.closest?.('.bcol')
    if (!col || !dragId) return
    e.preventDefault()
    col.classList.remove('is-over')
    const status = col.dataset.status
    const swimlane = col.dataset.swimlane
    const id = dragId
    dragId = null
    if (!status) return
    // 走 runAction：拖放同样不经过 dispatch，抛错时原来没有人接住
    await runAction(async () => {
      if (!(await guard())) return
      const patch = { status, updated_at: isoNow() }
      if (swimlane) patch.swimlane = swimlane
      if (status === 'done') { patch.completed_at = isoNow(); patch.progress = 100 }
      const rows = await updateItem(id, patch)
      if (!assertAffected(rows)) return
      await refresh()
    }, { action: 'board-drop', id })
  })
}

/* ---------------- 全局搜索 ---------------- */

function renderSearchHtml() {
  const uid = state.user?.id
  const lk = makeLookups(state.data)
  const q = state.search.toLowerCase()
  const scopeItems = state.space === 'team' && state.activeTeamId
    ? (state.data.items || []).filter((i) => i.team_id === state.activeTeamId)
    : [...personalItems(state.data, uid), ...assignedToMe(state.data, uid)]
  const hit = scopeItems.filter((i) => `${i.title} ${i.notes || ''} ${i.last_update || ''}`.toLowerCase().includes(q))
  const hitProjects = (state.data.projects || [])
    .filter((p) => (state.space === 'team' && state.activeTeamId ? p.team_id === state.activeTeamId : true))
    .filter((p) => `${p.name} ${p.description || ''}`.toLowerCase().includes(q))

  return `<div class="searchpage">
  <h2 class="search-title">搜索「${escapeHtml(state.search)}」</h2>
  <p class="muted small">当前搜索范围：${state.space === 'team' ? '当前团队的工作项与项目' : '我的个人事项与被分配工作'}</p>
  ${card(`工作项（${hit.length}）`, hit.length
    ? hit.map((i) => itemRow(i, lk, { scope: state.space, showTeam: state.space === 'personal', showAssignee: state.space === 'team' })).join('')
    : emptyState('没有匹配的工作项'))}
  ${card(`项目（${hitProjects.length}）`, hitProjects.length
    ? hitProjects.map((p) => `<div class="irow" data-action="open-project" data-id="${escapeHtml(p.id)}">
        <div class="irow-main"><div class="irow-title">${escapeHtml(p.name)}</div>
        <div class="irow-meta">${chipInline(projStatusDefLabel(p.status))}</div></div></div>`).join('')
    : emptyState('没有匹配的项目'))}
</div>`
}

function chipInline(text) {
  return `<span class="chip" style="color:#475569;background:#f1f5f9">${escapeHtml(text)}</span>`
}

function projStatusDefLabel(status) {
  const map = { active: '进行中', waiting: '等待中', someday: '搁置', done: '已完成', archived: '已归档' }
  return map[status] || '未知'
}

/* ---------------- 全局异常记录 ---------------- */

function installGlobalLogger() {
  // 未捕获异常与未处理的 Promise 拒绝是最难复现的故障来源，必须留下凭证
  window.addEventListener('error', (e) => {
    log('error', 'app.window.error', e.message, {
      source: e.filename, line: e.lineno, column: e.colno,
      stack: String(e.error?.stack || '').slice(0, 800),
    })
  })
  window.addEventListener('unhandledrejection', (e) => {
    log('error', 'app.unhandled.rejection', '未处理的 Promise 拒绝', {
      reason: String(e.reason?.message || e.reason || '').slice(0, 500),
      stack: String(e.reason?.stack || '').slice(0, 800),
    })
  })
}

/* ---------------- 启动 ---------------- */

/**
 * 启动门禁：先确认本地服务在，再谈登录。
 *
 * 上一版这里检查的是"云服务 SDK 是否从 CDN 加载成功"—— 那是外部依赖。
 * 现在只探一次同源的 /api/health：本页面全部数据与认证都来自它，
 * 探不到就说明服务进程没起或端口不对，此时**必须给出可操作的指引**，
 * 而不是让用户面对一个空白页去猜（这类"界面打不开"的困惑最难自查）。
 */
async function ensureBackend() {
  try {
    const info = await health()
    console.log(`[boot] 本地服务就绪 v${info?.version || '?'}（存储 ${info?.storage || '?'}）`)
    return info
  } catch (err) {
    // 把真实原因也说出来：只写"未连接"会把"端口不对"和"服务崩了"混为一谈
    console.error('[boot] 本地服务探测失败：', err)
    const detail = err?.message || String(err)
    const hint = location.origin
    document.body.innerHTML = `
      <div style="padding:48px;font-family:system-ui,-apple-system,'Segoe UI',sans-serif;color:#1a1f2b;line-height:1.7;max-width:720px">
        <h2 style="margin:0 0 12px">未连接到本地服务</h2>
        <p style="margin:0 0 16px;color:#475569">
          这个页面需要同源的本机服务提供账号与数据。请确认服务已启动：
        </p>
        <pre style="background:#f1f5f9;border:1px solid #e2e8f0;border-radius:8px;padding:14px 16px;overflow:auto">python server.py</pre>
        <p style="margin:16px 0 8px;color:#475569">然后在浏览器打开启动时打印的地址，而不是直接双击 index.html —— 直接打开文件时浏览器没有服务端，接口全部不可用。</p>
        <p style="margin:0 0 8px;color:#b45309;font-size:13px">失败原因：${escapeHtml(detail)}</p>
        <p style="margin:0;color:#94a3b8;font-size:13px">当前地址：${escapeHtml(hint)}</p>
      </div>`
    return null
  }
}

async function main() {
  if (!(await ensureBackend())) return
  bindGlobal()
  installGlobalLogger()
  log('info', 'app.boot', '应用启动', { ua: navigator.userAgent.slice(0, 120), href: location.href })
  // 认证成功后主动收敛会话：不依赖事件推送、不等轮询，避免界面停在登录页被误判为卡死
  setOnAuthenticated(async (session) => { await bootstrap(session) })
  await bootstrap()
}

/* ---------------- 系统管理：集成域（REQ-055） ---------------- */

/**
 * 集成域端点的局部请求封装。
 * api.js 的 request() 是模块内私有函数，本轮改动范围不含 api.js，
 * 因此这里按同一套契约复刻：同源 Cookie、JSON body、超时、错误对象带 code/status，
 * 最终交给 friendlyError() 统一翻译 —— 不引入第二套错误文案。
 */
async function integrationRequest(path, { method = 'GET', body } = {}) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 20000)
  let response
  try {
    response = await fetch(path, {
      method,
      credentials: 'same-origin',
      signal: controller.signal,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (err) {
    if (err?.name === 'AbortError') {
      const e = new Error('请求超时，请稍后重试'); e.kind = 'timeout'; throw e
    }
    const e = new Error('无法连接本地服务，请确认服务进程仍在运行'); e.kind = 'network'; throw e
  } finally {
    clearTimeout(timer)
  }
  const text = await response.text()
  let payload = null
  if (text) { try { payload = JSON.parse(text) } catch { payload = null } }
  if (!response.ok) {
    const detail = payload?.error || {}
    const e = new Error(detail.message || `请求失败（HTTP ${response.status}）`)
    e.code = detail.code || null
    e.status = response.status
    if (response.status === 401) e.kind = 'unauthenticated'
    throw e
  }
  return payload
}

/** 就地显示内联错误，不重渲染整页（保住用户已填的备注与 scope 勾选） */
function showIntTokenError(message) {
  const host = $('#int-token-error')
  if (!host) { if (message) toast(message, 'warn'); return }
  host.textContent = message || ''
  host.hidden = !message
}

function showIntLogError(message) {
  const host = $('#int-log-error')
  if (!host) { if (message) toast(message, 'warn'); return }
  host.textContent = message || ''
  host.hidden = !message
}

/**
 * 读取令牌列表，并行读取实例运行配置（GET /api/admin/config）。
 * 明文从不经过这里 —— GET 只回 tokenHint（hash 前 8 位）。
 * 配置是只读按需数据：失败就保持 config=null，视图降级为"开关状态未知"，
 * 不抛错、也不妨碍令牌列表的加载与其自身的错误提示。
 */
async function loadIntegration() {
  if (!state.user?.isSystemAdmin) {
    state.integration = emptyIntegration()
    return
  }
  state.integration.loading = true
  state.integration.error = ''
  render()
  // 配置与令牌列表并行拉取；配置请求自带 catch，任何失败都只降级为 null。
  const configTask = integrationRequest('/api/admin/config')
    .then((cfg) => {
      state.integration.config = (cfg && typeof cfg === 'object') ? cfg : null
    })
    .catch((err) => {
      state.integration.config = null
      log('warn', 'integration.config.fail', '读取集成配置状态失败，降级为状态未知', describeError(err))
    })
  try {
    const data = await integrationRequest('/api/admin/api-tokens')
    state.integration.tokens = Array.isArray(data?.tokens) ? data.tokens : []
    state.integration.loaded = true
  } catch (err) {
    if (isPasswordGateError(err)) { await handleGateError(err); return }
    log('warn', 'integration.tokens.fail', '读取令牌列表失败', describeError(err))
    state.integration.error = friendlyError(err)
  } finally {
    state.integration.loading = false
  }
  await configTask
  render()
}

/** 签发令牌：scope 勾选与后端 VALID_SCOPES 逐字对齐，read 默认勾。 */
async function createTokenFlow() {
  if (!(await guard())) return
  if (!state.user?.isSystemAdmin) return toast('只有系统管理员可以签发访问令牌', 'warn')

  const label = ($('#int-token-label')?.value || '').trim().slice(0, 120)
  const days = Number($('#int-token-days')?.value || 90)
  const scopes = [...document.querySelectorAll('#main .int-issue input[data-scope]:checked')]
    .map((el) => el.dataset.scope).filter(Boolean)
  if (!scopes.length) return showIntTokenError('请至少勾选一个授权范围（只读 read 默认勾选）')
  if (!Number.isInteger(days) || days < 1 || days > 3650) {
    return showIntTokenError('有效期必须在 1~3650 天之间')
  }
  showIntTokenError('')

  let result
  try {
    result = await integrationRequest('/api/admin/api-tokens', {
      method: 'POST', body: { label, scopes, expiresInDays: days },
    })
  } catch (err) {
    if (isPasswordGateError(err)) { await handleGateError(err); return }
    log('warn', 'integration.token.create.fail', '签发令牌失败', describeError(err))
    return showIntTokenError(friendlyError(err))
  }
  if (!result?.token) {
    return showIntTokenError('签发成功但服务端未返回明文令牌，请撤销该令牌后重新签发')
  }
  // 明文只活到 openModal 这次调用：不进 state、不写 localStorage、不进 log()。
  await loadIntegration()
  openModal(tokenCreatedHtml(result))
}

async function rotateTokenFlow(tokenId) {
  if (!Number.isInteger(tokenId)) return toast('令牌标识无效，请刷新页面后重试', 'warn')
  if (!(await confirmAction(
    '旧令牌会立即失效。已配置好的脚本与 MCP 客户端会全部返回 401，需要重新粘贴新令牌。',
    { title: '轮换访问令牌？', okText: '确认轮换', danger: true }))) return
  if (!(await guard())) return

  let result
  try {
    result = await integrationRequest(`/api/admin/api-tokens/${tokenId}/rotate`, { method: 'POST', body: {} })
  } catch (err) {
    if (isPasswordGateError(err)) { await handleGateError(err); return }
    log('warn', 'integration.token.rotate.fail', '轮换令牌失败', describeError(err))
    return toast(friendlyError(err), 'error')
  }
  if (!result?.token) return toast('轮换成功但服务端未返回明文令牌，请撤销后重新签发', 'warn')
  await loadIntegration()
  openModal(tokenCreatedHtml(result, { rotated: true }))
}

async function revokeTokenFlow(tokenId) {
  if (!Number.isInteger(tokenId)) return toast('令牌标识无效，请刷新页面后重试', 'warn')
  if (!(await confirmAction(
    '撤销后使用该令牌的脚本与 MCP 客户端会立即返回 401，且无法恢复。',
    { title: '撤销访问令牌？', okText: '确认撤销', danger: true }))) return
  if (!(await guard())) return

  try {
    await integrationRequest(`/api/admin/api-tokens/${tokenId}`, { method: 'DELETE' })
  } catch (err) {
    if (isPasswordGateError(err)) { await handleGateError(err); return }
    log('warn', 'integration.token.revoke.fail', '撤销令牌失败', describeError(err))
    return toast(friendlyError(err), 'error')
  }
  log('info', 'integration.token.revoke.ok', '已撤销访问令牌', { tokenId })
  await loadIntegration()
  toast('令牌已撤销')
}

/** 按 request_id 查单条排障记录（P2 最小形态，不做列表/导出/全文搜索） */
async function findLogFlow() {
  const rid = ($('#int-log-rid')?.value || '').trim().toLowerCase()
  if (!/^[0-9a-f]{8}$/.test(rid)) {
    return showIntLogError('请填写服务端报错页面给出的 8 位 request_id（例如 a1b2c3d4）')
  }
  showIntLogError('')
  state.integration.logQuery = rid
  state.integration.logLoading = true
  state.integration.logError = ''
  state.integration.logResult = null
  state.integration.logDegraded = ''
  render()
  try {
    const data = await integrationRequest(`/api/logs?q=${encodeURIComponent(rid)}&limit=20`)
    const logs = Array.isArray(data?.logs) ? data.logs : []
    state.integration.logResult = logs.find((r) => String(r?.request_id || '').toLowerCase() === rid) || null
    if (data?.fileLogging === false) state.integration.logDegraded = data?.degradeReason || '日志未启用文件落盘'
  } catch (err) {
    if (isPasswordGateError(err)) { await handleGateError(err); return }
    log('warn', 'integration.log.find.fail', '按编号查询日志失败', describeError(err))
    state.integration.logError = friendlyError(err)
  } finally {
    state.integration.logLoading = false
  }
  render()
}
main().catch((err) => {
  console.error(err)
  toast(friendlyError(err), 'error')
})
