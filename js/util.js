/* 通用工具：DOM、日期、提示、转义 */

export const $ = (sel, root = document) => root.querySelector(sel)
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel))

export function escapeHtml(value) {
  if (value === null || value === undefined) return ''
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/** 把可能含 HTML 的文本安全地渲染，支持换行转空格 */
export function safeText(value) {
  return escapeHtml(String(value ?? '').replace(/\s+/g, ' ').trim())
}

/* ---------------- 外链安全 ---------------- */

/**
 * 渲染与保存外链时的唯一放行口。
 *
 * 为什么必须有这一层：`escapeHtml` 只处理 `& < > " '`，对伪协议毫无作用 ——
 * `javascript:alert(1)` 原样写进 href 就是可执行脚本；而 `<input type="url">`
 * 只校验"像不像 URL"，**不限制 scheme**，`data:` / `vbscript:` 都能填进去。
 * 资源台账的链接由团队成员手工填写、全员共享，等于把 XSS 入口开放给同事，
 * 所以服务端（写入时）与前端（渲染 + 保存前）各做一次白名单，任一侧失守都兜得住。
 *
 * 规则：先 trim，再要求**开头就是 scheme**，只放行 http / https / mailto（大小写不敏感）。
 *   · `java\nscript:` 这类插入控制字符的写法因为 scheme 匹配不到而被拒绝；
 *   · 没有 scheme 的相对路径也拒绝 —— 资源台账里的链接一律是绝对地址，
 *     放行相对路径只会让"打开链接"跳到本应用的某个页面，语义是错的。
 */
const SAFE_URL_SCHEMES = ['http', 'https', 'mailto']

/**
 * @returns {{ href: string, safe: boolean, empty: boolean, scheme: string }}
 *   `safe=false` 时 href 恒为 '#'，渲染层据此降级为不可点元素（并用 aria-disabled 标注）。
 */
export function urlSafety(raw) {
  const s = String(raw ?? '').trim()
  if (!s) return { href: '#', safe: false, empty: true, scheme: '' }
  const m = /^([a-z][a-z0-9+.-]*):/i.exec(s)
  if (!m) return { href: '#', safe: false, empty: false, scheme: '' }
  const scheme = m[1].toLowerCase()
  if (SAFE_URL_SCHEMES.includes(scheme)) return { href: s, safe: true, empty: false, scheme }
  return { href: '#', safe: false, empty: false, scheme }
}

/** 只要 href：不安全一律返回 '#' */
export const safeUrl = (raw) => urlSafety(raw).href

/** 是否被降级（渲染时要据此去掉 target 并加 aria-disabled） */
export const isSafeUrl = (raw) => urlSafety(raw).safe

/* ---------------- 日期工具（全部按本地时区，DATE 列为 'YYYY-MM-DD'） ---------------- */

const pad = (n) => String(n).padStart(2, '0')

export function toDateStr(date) {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

export const todayStr = () => toDateStr(new Date())

export function parseDate(str) {
  if (!str) return null
  return new Date(`${str}T00:00:00`)
}

export function addDays(dateStr, n) {
  const d = parseDate(dateStr) || new Date()
  d.setDate(d.getDate() + n)
  return toDateStr(d)
}

export function addMonths(dateStr, n) {
  const d = parseDate(dateStr) || new Date()
  d.setMonth(d.getMonth() + n)
  return toDateStr(d)
}

/** 周一为一周之始 */
export function startOfWeek(dateStr = todayStr()) {
  const d = parseDate(dateStr)
  const day = (d.getDay() + 6) % 7
  d.setDate(d.getDate() - day)
  return toDateStr(d)
}

export function endOfWeek(dateStr = todayStr()) {
  return addDays(startOfWeek(dateStr), 6)
}

/** 相差天数：b - a */
export function diffDays(a, b) {
  const ms = parseDate(b).getTime() - parseDate(a).getTime()
  return Math.round(ms / 86400000)
}

export const WEEK_LABELS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

/** ISO 周键，例如 2026-W38 */
export function isoWeekKey(dateStr = todayStr()) {
  const d = parseDate(dateStr)
  const target = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()))
  const dayNum = (target.getUTCDay() + 6) % 7
  target.setUTCDate(target.getUTCDate() - dayNum + 3)
  const firstThursday = new Date(Date.UTC(target.getUTCFullYear(), 0, 4))
  const firstDayNum = (firstThursday.getUTCDay() + 6) % 7
  firstThursday.setUTCDate(firstThursday.getUTCDate() - firstDayNum + 3)
  const week = 1 + Math.round((target - firstThursday) / (7 * 86400000))
  return `${target.getUTCFullYear()}-W${pad(week)}`
}

/** 人类友好的日期描述 */
export function describeDate(dateStr) {
  if (!dateStr) return ''
  const today = todayStr()
  const delta = diffDays(today, dateStr)
  if (delta === 0) return '今天'
  if (delta === 1) return '明天'
  if (delta === 2) return '后天'
  if (delta === -1) return '昨天'
  if (delta < 0) return `逾期 ${-delta} 天`
  if (delta <= 7) return `${delta} 天后`
  const d = parseDate(dateStr)
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

export function formatDateCN(dateStr) {
  if (!dateStr) return ''
  const d = parseDate(dateStr)
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`
}

export function formatDateTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/* ---------------- 提示条 ---------------- */

let toastTimer = null
export function toast(message, kind = 'info') {
  const root = $('#toast-root')
  if (!root) return
  root.innerHTML = `<div class="toast toast-${kind}">${escapeHtml(message)}</div>`
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { root.innerHTML = '' }, 3200)
}

/* ---------------- 其它 ---------------- */

export function debounce(fn, wait = 260) {
  let t
  return (...args) => {
    clearTimeout(t)
    t = setTimeout(() => fn(...args), wait)
  }
}

export function initials(email) {
  const name = String(email || '').split('@')[0] || '?'
  return name.slice(0, 2).toUpperCase()
}

export function isValidEmail(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(String(email || '').trim())
}

/**
 * 历史版本在取不到邮箱时写进界面的字面量占位符。
 * 这些值一旦落库就是脏数据（成员邮箱、事项责任人、进度作者），必须能被识别为"缺失"并覆盖。
 */
const IDENTITY_PLACEHOLDER = /^(已登录|已登录用户|未登录|用户|成员|unknown|null|undefined)$/i

export function isPlaceholderIdentity(value) {
  const s = String(value ?? '').trim()
  return !s || IDENTITY_PLACEHOLDER.test(s)
}

/** 邮箱前缀，用作显示名的最后一级回退（邮箱前缀为空时不返回占位文案）。 */
export function emailPrefix(email) {
  const s = String(email || '').trim()
  if (!s) return ''
  return s.includes('@') ? s.split('@')[0] : s
}

/**
 * 归一化登录者身份 → { id, name, email }。
 *
 * 为什么需要这一层（本次线上缺陷的根因）：
 *   SDK 的 getSession() 返回的 user 由内部的 userFromSessionPayload 构造，
 *   形状是 { id, isAnonymous, raw }，**不含 email**；
 *   真正带 email 的是 auth.getUser()（GET /v1/user/me → parseUser → { id, email, phone, raw }）。
 *   历史代码假定 session.user.email 存在，于是整站身份显示退化为字面量"已登录"，
 *   更严重的是把该字面量当作邮箱写进了 team_members.email / teams.owner_name。
 *   这里把两条来源统一，并对 raw 里的 user_metadata 做兼容探测。
 *
 * @param user    会话里的 user（可能只有 id）
 * @param profile auth.getUser() 的结果（可能为 null，例如该请求失败）
 */
export function describeIdentity(user, profile) {
  const u = user || {}
  const p = profile || {}
  const ur = u.raw || {}
  const pr = p.raw || {}
  const meta = {
    ...(ur.user_metadata || {}),
    ...(ur.metadata || {}),
    ...(pr.user_metadata || {}),
    ...(pr.metadata || {}),
  }
  const first = (...vals) => {
    for (const v of vals) {
      const s = String(v ?? '').trim()
      if (s && !IDENTITY_PLACEHOLDER.test(s)) return s
    }
    return ''
  }
  const email = first(p.email, pr.email, ur.email, meta.email)
  const name = first(
    p.name, pr.name, pr.display_name, pr.nickname,
    meta.name, meta.display_name, meta.nickname,
    ur.name, ur.display_name,
    emailPrefix(email),
  )
  return {
    id: first(p.id, u.id, pr.sub, pr.uid, ur.sub, ur.uid),
    name,
    email,
  }
}

/**
 * 给任何 Promise 加硬超时。
 * 认证/数据请求一旦在网络层挂起而不 settle，界面就会永久停在「处理中」，
 * 因此所有面向用户的异步动作都必须套上它，保证 finally 一定能恢复 UI。
 */
export function withTimeout(promise, ms = 30000, label = '请求') {
  let timer
  const guard = new Promise((_, reject) => {
    timer = setTimeout(() => {
      const err = new Error(`${label}超时（${Math.round(ms / 1000)} 秒无响应），请检查网络后重试`)
      err.kind = 'timeout'
      reject(err)
    }, ms)
  })
  return Promise.race([promise, guard]).finally(() => clearTimeout(timer))
}

/** 清洗服务端泄漏的 Go fmt 片段，例如 "无效的验证码%!(EXTRA string=rpc error: ...)" */
export function cleanMessage(msg) {
  return String(msg || '')
    .replace(/%!\(EXTRA[^)]*\)/g, '')
    .replace(/%!\w+\([^)]*\)/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

/* ---------------- 集合与统计 ---------------- */

export const clamp = (n, min, max) => Math.min(max, Math.max(min, n))

export function uniqBy(list, keyFn) {
  const seen = new Set()
  const out = []
  for (const it of list || []) {
    const k = keyFn(it)
    if (seen.has(k)) continue
    seen.add(k)
    out.push(it)
  }
  return out
}

export function groupBy(list, keyFn) {
  const out = new Map()
  for (const it of list || []) {
    const k = keyFn(it)
    if (!out.has(k)) out.set(k, [])
    out.get(k).push(it)
  }
  return out
}

export const sumBy = (list, fn) => (list || []).reduce((acc, it) => acc + (Number(fn(it)) || 0), 0)

/** 百分比，分母为 0 时返回 0，避免 NaN 渲染到界面 */
export function pct(part, total, digits = 0) {
  if (!total) return 0
  const v = (Number(part) / Number(total)) * 100
  if (!Number.isFinite(v)) return 0
  return Number(v.toFixed(digits))
}

/* ---------------- 时间补充 ---------------- */

export const isoNow = () => new Date().toISOString()

/** 距今剩余小时数（可为负），用于 SLA 倒计时 */
export function hoursUntil(iso) {
  if (!iso) return null
  const t = new Date(iso).getTime()
  if (!Number.isFinite(t)) return null
  return (t - Date.now()) / 3600000
}

/** 相对时间描述，用于进度日志 */
export function relativeTime(iso) {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (!Number.isFinite(t)) return ''
  const mins = Math.round((Date.now() - t) / 60000)
  if (mins < 1) return '刚刚'
  if (mins < 60) return `${mins} 分钟前`
  const hours = Math.round(mins / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.round(hours / 24)
  if (days < 30) return `${days} 天前`
  return formatDateCN(toDateStr(new Date(t)))
}

/** SLA 剩余时间的可读表达 */
export function describeSla(iso) {
  const h = hoursUntil(iso)
  if (h === null) return ''
  if (h < 0) return `已超时 ${Math.abs(Math.round(h))} 小时`
  if (h < 1) return `剩余 ${Math.round(h * 60)} 分钟`
  if (h < 24) return `剩余 ${Math.round(h)} 小时`
  return `剩余 ${Math.round(h / 24)} 天`
}

/** 从邮箱/昵称中取一个可读短名 */
export function shortName(value) {
  const s = String(value || '').trim()
  if (!s) return '未指派'
  const base = s.includes('@') ? s.split('@')[0] : s
  return base.length > 12 ? `${base.slice(0, 12)}…` : base
}

/** 生成团队邀请码：去掉易混淆字符，便于口播与手抄 */
export function makeJoinCode() {
  const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
  let out = ''
  for (let i = 0; i < 6; i += 1) {
    out += alphabet[Math.floor(Math.random() * alphabet.length)]
  }
  return out
}

/** 稳定地从字符串派生一个索引，用于配色稳定的头像 */
export function hashIndex(str, mod) {
  let h = 0
  const s = String(str || '')
  for (let i = 0; i < s.length; i += 1) {
    h = (h * 31 + s.charCodeAt(i)) % 100000
  }
  return mod ? h % mod : h
}
