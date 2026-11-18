/**
 * 视图层：纯渲染函数与数据选择器。
 *
 * 约定（与 app.js 的分工）：
 *   - 本文件**只产出 HTML 字符串**，不绑定任何事件、不读写全局状态；
 *   - 一切交互通过 `data-action` / `data-*` 属性表达，由 app.js 统一事件委托处理；
 *   - 新增交互不要在这里内联 onclick。
 *
 * 双域结构：
 *   个人空间（GTD 五步）与团队空间（ITIL 轻量化 + Kanban）复用同一份 work_items 数据，
 *   差别只在筛选口径与状态标注（见 config.js 的 ITEM_STATUS.personal）。
 */
import {
  ITEM_TYPES, ITEM_STATUS, PRIORITY, ROLES, RESOURCE_KINDS, PROJECT_KINDS, PROJECT_STATUS,
  SHIFT_SLOTS, BOARD_COLUMNS, PALETTE, DEFAULT_WIP,
  MIN_PASSWORD_LENGTH, MAX_PASSWORD_LENGTH,
} from './config.mjs'
import {
  escapeHtml, safeText, describeDate, describeSla, todayStr, addDays, diffDays,
  startOfWeek, formatDateTime, formatDateCN, relativeTime, pct, WEEK_LABELS,
  shortName, initials, hashIndex, clamp, emailPrefix, isPlaceholderIdentity, urlSafety,
} from './util.mjs'

/* ============================================================
 * 0. 枚举取用与容错
 * ============================================================ */

const FALLBACK = { label: '未知', short: '—', color: '#64748b', soft: '#f1f5f9', icon: '•' }

const typeDef = (t) => ITEM_TYPES[t] || FALLBACK
const statusDef = (s) => ITEM_STATUS[s] || FALLBACK
const priorityDef = (p) => PRIORITY[p] || FALLBACK
const roleDef = (r) => ROLES[r] || FALLBACK
const resKindDef = (k) => RESOURCE_KINDS[k] || FALLBACK
const projKindDef = (k) => PROJECT_KINDS[k] || FALLBACK
const projStatusDef = (s) => PROJECT_STATUS[s] || FALLBACK
const slotDef = (s) => SHIFT_SLOTS[s] || FALLBACK

/** 状态在两种作用域下的不同叫法：团队看流程，个人看清单 */
const statusLabel = (status, scope) => {
  const def = statusDef(status)
  return scope === 'personal' ? (def.personal || def.label) : def.label
}

export const ACTIVE_STATUSES = ['intake', 'triage', 'ready', 'doing', 'review', 'blocked', 'waiting', 'someday']

/* ============================================================
 * 1. 查表器（避免视图里反复写 find）
 * ============================================================ */

export function makeLookups(data) {
  const teamById = new Map((data.teams || []).map((t) => [t.id, t]))
  const memberById = new Map((data.members || []).map((m) => [m.id, m]))
  const membersByTeam = new Map()
  for (const m of data.members || []) {
    if (!membersByTeam.has(m.team_id)) membersByTeam.set(m.team_id, [])
    membersByTeam.get(m.team_id).push(m)
  }
  const projectById = new Map((data.projects || []).map((p) => [p.id, p]))
  const areaById = new Map((data.areas || []).map((a) => [a.id, a]))
  const contextById = new Map((data.contexts || []).map((c) => [c.id, c]))
  const userByUid = new Map()
  for (const m of data.members || []) {
    if (m.user_id && !userByUid.has(m.user_id)) userByUid.set(m.user_id, m)
  }
  return {
    teamById,
    memberById,
    membersByTeam,
    projectById,
    areaById,
    contextById,
    userByUid,
    team: (id) => teamById.get(id) || null,
    project: (id) => projectById.get(id) || null,
    area: (id) => areaById.get(id) || null,
    context: (id) => contextById.get(id) || null,
    memberOf: (teamId, uid) => (membersByTeam.get(teamId) || []).find((m) => m.user_id === uid) || null,
    members: (teamId) => membersByTeam.get(teamId) || [],
    nameOfUser: (uid) => {
      if (!uid) return ''
      const m = userByUid.get(uid)
      return m ? (m.display_name || shortName(m.email) || '成员') : ''
    },
    nameOfMemberId: (memberId) => {
      const m = memberById.get(memberId)
      return m ? (m.display_name || shortName(m.email) || '成员') : ''
    },
  }
}

/* ============================================================
 * 2. 数据选择器
 * ============================================================ */

/** 个人自建事项（不属于任何团队） */
export const personalItems = (data, uid) =>
  (data.items || []).filter((i) => i.team_id == null && i.owner_id === uid)

/** 团队分配给我的事项 —— 个人空间与团队空间之间的桥梁 */
export const assignedToMe = (data, uid) =>
  (data.items || []).filter((i) => i.team_id != null && i.assignee_id === uid)

/** 我的全部事项：自建 + 被分配 */
export const myItems = (data, uid) => [...personalItems(data, uid), ...assignedToMe(data, uid)]

export const teamItems = (data, teamId) => (data.items || []).filter((i) => i.team_id === teamId)

export const isOpen = (i) => i.status !== 'done' && i.status !== 'archived'

/** 逾期：有截止日或 SLA 时限且已过期，且未完成 */
export function isOverdue(item) {
  if (!isOpen(item)) return false
  const due = item.due_date || item.sla_due_at
  if (!due) return false
  return String(due).slice(0, 10) < todayStr()
}

/** SLA 风险：已承诺时限，剩余不足 1 小时或已超时 */
export function slaRisk(item) {
  if (!item.sla_due_at || !isOpen(item)) return null
  const due = new Date(item.sla_due_at).getTime()
  if (!Number.isFinite(due)) return null
  const remain = due - Date.now()
  if (remain < 0) return 'breach'
  if (remain < 3600000) return 'risk'
  return null
}

export const todayFocus = (list) => {
  const today = todayStr()
  return list.filter((i) => isOpen(i) && (i.focus_date === today || (!i.focus_date && i.due_date === today)))
}

/* ============================================================
 * 3. 指标（报表与健康度）
 * ============================================================ */

const RUN_TYPES = ['routine', 'inspection', 'request', 'incident', 'problem', 'change']

/**
 * 团队度量。
 * 设计依据：运维团队价值常被低估，根因是"工作没有被数据化"——
 * 因此这里每一项输出都对应一个可以对外沟通的事实，而不是内部自嗨的计数。
 */
export function computeTeamMetrics(data, teamId, lk) {
  const items = teamItems(data, teamId)
  const lkSafe = lk || makeLookups(data)
  const active = items.filter(isOpen)
  const done = items.filter((i) => i.status === 'done')

  const countTypes = {}
  for (const key of Object.keys(ITEM_TYPES)) countTypes[key] = 0
  for (const i of items) countTypes[i.item_type] = (countTypes[i.item_type] || 0) + 1

  const countStatus = {}
  for (const i of items) countStatus[i.status] = (countStatus[i.status] || 0) + 1

  const runCount = items.filter((i) => RUN_TYPES.includes(i.item_type)).length
  const buildCount = items.filter((i) => i.item_type === 'project_task').length
  const unplanned = items.filter((i) => i.swimlane === 'unplanned').length

  // SLA 达成：仅统计"承诺了时限且已关闭"的事项，避免用未承诺项冲淡指标
  const slaScoped = done.filter((i) => i.sla_due_at && i.completed_at)
  const slaOk = slaScoped.filter(
    (i) => new Date(i.completed_at).getTime() <= new Date(i.sla_due_at).getTime(),
  ).length
  const slaRate = slaScoped.length ? pct(slaOk, slaScoped.length) : null

  const overdue = active.filter(isOverdue)
  const overdueRate = active.length ? pct(overdue.length, active.length) : 0

  const now = Date.now()
  const doneLast = (days) =>
    done.filter((i) => i.completed_at && now - new Date(i.completed_at).getTime() <= days * 86400000).length
  const throughput7 = doneLast(7)
  const throughput30 = doneLast(30)

  // 流转速率：已完成占"已完成 + 积压"的比例，反映能否消化存量
  const flowScore = active.length + throughput30 === 0
    ? 100
    : clamp(pct(throughput30, active.length + throughput30), 0, 100)

  // 健康度 = SLA 达成 40% + 逾期控制 30% + 流转速率 30%
  // 未定义 SLA 的团队该项取满分，不对"尚未建立 SLA"这件事做惩罚
  const health = Math.round((slaRate ?? 100) * 0.4 + (100 - overdueRate) * 0.3 + flowScore * 0.3)

  const members = lkSafe.members(teamId).filter((m) => m.status === 'active')
  const load = members.map((m) => {
    const mine = active.filter((i) => i.assignee_id === m.user_id)
    const capacity = Number(m.capacity) || 6
    return {
      member: m,
      open: mine.length,
      doing: mine.filter((i) => i.status === 'doing').length,
      blocked: mine.filter((i) => i.status === 'blocked').length,
      overdue: mine.filter(isOverdue).length,
      capacity,
      over: mine.length > capacity,
    }
  }).sort((a, b) => b.open - a.open)

  const unassigned = active.filter((i) => !i.assignee_id)

  return {
    items, active, done,
    countTypes, countStatus, runCount, buildCount, unplanned,
    runRatio: items.length ? pct(runCount, items.length) : 0,
    buildRatio: items.length ? pct(buildCount, items.length) : 0,
    interruptRate: items.length ? pct(unplanned, items.length) : 0,
    slaRate, slaScoped: slaScoped.length, slaOk,
    overdue, overdueRate,
    throughput7, throughput30, flowScore, health,
    load, unassigned,
    wip: BOARD_COLUMNS.reduce((acc, s) => {
      acc[s] = active.filter((i) => i.status === s).length
      return acc
    }, {}),
  }
}

/** 个人 GTD 度量：收集箱是否清空、等待项是否跟进、在办项目是否都有下一步 */
export function computePersonalMetrics(data, uid) {
  const mine = myItems(data, uid)
  const open = mine.filter(isOpen)
  const inbox = open.filter((i) => i.status === 'intake')
  const triage = open.filter((i) => i.status === 'triage')
  const ready = open.filter((i) => i.status === 'ready')
  const waiting = open.filter((i) => i.status === 'waiting')
  const someday = open.filter((i) => i.status === 'someday')
  const overdue = open.filter(isOverdue)
  const focus = todayFocus(open)

  const teamIds = [...new Set(mine.filter((i) => i.team_id != null).map((i) => i.team_id))]
  const projects = (data.projects || []).filter((p) => teamIds.includes(p.team_id) && p.status === 'active')
  const stalled = projects.filter((p) => !mine.some((i) => i.project_id === p.id && i.status === 'ready'))

  return {
    mine, open, inbox, triage, ready, waiting, someday, overdue, focus,
    assignedCount: assignedToMe(data, uid).length,
    personalCount: personalItems(data, uid).length,
    stalledProjects: stalled,
  }
}

/** 团队一周的班次排布（值班视图用） */
export function onCallMatrix(data, teamId, anchor) {
  const start = startOfWeek(anchor || todayStr())
  const days = Array.from({ length: 7 }, (_, i) => addDays(start, i))
  const rows = (data.onCall || []).filter((r) => r.team_id === teamId)
  return days.map((d) => ({
    date: d,
    shifts: ['day', 'night'].map((slot) => ({
      slot,
      row: rows.find((r) => r.shift_date === d && r.slot === slot) || null,
    })),
  }))
}

/* ============================================================
 * 4. 基础视觉组件
 * ============================================================ */

const esc = (v) => escapeHtml(v ?? '')

export function pageHeader(title, subtitle, actions = '', tabs = '') {
  return `<header class="ph">
  <div class="ph-text">
    <h1 class="ph-title">${esc(title)}</h1>
    ${subtitle ? `<p class="ph-sub">${esc(subtitle)}</p>` : ''}
  </div>
  ${actions ? `<div class="ph-actions">${actions}</div>` : ''}
  ${tabs ? `<div class="ph-tabs">${tabs}</div>` : ''}
</header>`
}

export function card(title, body, opts = {}) {
  const cls = ['card', opts.class || ''].filter(Boolean).join(' ')
  const head = title
    ? `<div class="card-head">
        <h2 class="card-title">${esc(title)}</h2>
        ${opts.aside ? `<div class="card-aside">${opts.aside}</div>` : ''}
      </div>`
    : ''
  return `<section class="${cls}">${head}<div class="card-body">${body}</div></section>`
}

export function chip(text, color = '#475569', soft = '#f1f5f9', extra = '') {
  return `<span class="chip ${extra}" style="color:${color};background:${soft}">${esc(text)}</span>`
}

export const typeChip = (item) => {
  const d = typeDef(item.item_type)
  return `<span class="chip chip-type" style="color:${d.color};background:${d.soft}" title="${esc(d.label)}">${d.icon} ${esc(d.short)}</span>`
}

export const priorityChip = (item) => {
  const d = priorityDef(item.priority)
  return `<span class="chip" style="color:${d.color};background:${d.soft}" title="${esc(`${d.label} ${d.name}·目标 ${d.slaHours}h`)}">${esc(d.label)}</span>`
}

export const statusChip = (status, scope) => {
  const d = statusDef(status)
  return `<span class="chip" style="color:${d.color};background:${d.soft}">${esc(statusLabel(status, scope))}</span>`
}

/** 截止/SLA 提示：把"还有多久"讲清楚，而不是丢一个日期 */
export function dueChip(item) {
  const out = []
  if (item.sla_due_at && isOpen(item)) {
    const risk = slaRisk(item)
    const color = risk === 'breach' ? '#b91c1c' : risk === 'risk' ? '#b45309' : '#475569'
    const soft = risk === 'breach' ? '#fdeaea' : risk === 'risk' ? '#fdf3e3' : '#f1f5f9'
    out.push(chip(`SLA ${describeSla(item.sla_due_at)}`, color, soft))
  }
  if (item.due_date) {
    const late = isOverdue(item)
    out.push(chip(
      late ? `逾期 ${Math.abs(diffDays(todayStr(), item.due_date))} 天` : `截止 ${describeDate(item.due_date)}`,
      late ? '#b91c1c' : '#475569',
      late ? '#fdeaea' : '#f1f5f9',
    ))
  }
  return out.join('')
}

export function statTile(label, value, hint = '', tone = '') {
  return `<div class="stat ${tone}">
  <div class="stat-value">${esc(value)}</div>
  <div class="stat-label">${esc(label)}</div>
  ${hint ? `<div class="stat-hint">${esc(hint)}</div>` : ''}
</div>`
}

export function emptyState(title, hint = '', action = '') {
  return `<div class="empty">
  <div class="empty-title">${esc(title)}</div>
  ${hint ? `<div class="empty-hint">${esc(hint)}</div>` : ''}
  ${action ? `<div class="empty-action">${action}</div>` : ''}
</div>`
}

export function avatar(name, seed) {
  const label = initials((name || '?').includes('@') ? name : `${name}@x`)
  const tone = PALETTE[hashIndex(String(seed || name || '?'), PALETTE.length)]
  return `<span class="avatar av-${tone}" title="${esc(name || '')}">${esc(label)}</span>`
}

/** 横向占比条：报表主力组件 */
export function barRow(label, value, total, color = '#0f766e', extra = '') {
  const p = total ? pct(value, total, 0) : 0
  return `<div class="bar-row">
  <div class="bar-label" title="${esc(label)}">${esc(label)}</div>
  <div class="bar-track"><div class="bar-fill" style="width:${clamp(p, 0, 100)}%;background:${color}"></div></div>
  <div class="bar-value">${esc(value)}${extra}</div>
</div>`
}

export function progressBar(value) {
  const v = clamp(Number(value) || 0, 0, 100)
  const color = v >= 100 ? '#15803d' : v >= 60 ? '#0f766e' : v >= 30 ? '#0369a1' : '#b45309'
  return `<div class="pbar" title="完成度 ${v}%"><div class="pbar-fill" style="width:${v}%;background:${color}"></div></div>`
}

export function scoreRing(score) {
  const s = clamp(Number(score) || 0, 0, 100)
  const tone = s >= 85 ? '#15803d' : s >= 70 ? '#0f766e' : s >= 50 ? '#b45309' : '#b91c1c'
  return `<div class="ring" style="--val:${s};--tone:${tone}">
  <div class="ring-inner"><span class="ring-num">${s}</span><span class="ring-unit">分</span></div>
</div>`
}

/* ============================================================
 * 5. 事项条目与卡片
 * ============================================================ */

const DONE_MARK = '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M3 8.5l3.2 3.2L13 5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'

/**
 * 事项行 —— 个人空间与团队列表共用。
 * opts.scope: 'personal' | 'team'，决定状态文案与显示哪些附属信息
 * opts.showTeam: 是否显示所属团队徽标（跨团队的个人视图需要）
 * opts.showAssignee: 是否显示负责人（团队视图需要）
 */
export function itemRow(item, lk, opts = {}) {
  const scope = opts.scope || 'team'
  const done = item.status === 'done'
  const overdue = isOverdue(item)
  const team = item.team_id != null ? lk.team(item.team_id) : null
  const project = item.project_id != null ? lk.project(item.project_id) : null
  const context = item.context_id != null ? lk.context(item.context_id) : null
  const assigneeName = item.assignee_name || (item.assignee_id ? lk.nameOfUser(item.assignee_id) : '')

  const meta = [
    priorityChip(item),
    statusChip(item.status, scope),
    dueChip(item),
    opts.showTeam && team ? chip(team.name, '#0f766e', '#e6f4f1') : '',
    project ? chip(`#${project.name}`, '#4f46e5', '#ecebfd') : '',
    context ? chip(`@${context.name}`, '#0369a1', '#e5f0fb') : '',
    item.waiting_for ? chip(`等：${item.waiting_for}`, '#b45309', '#fdf3e3') : '',
    item.blocked_reason ? chip(`受阻：${item.blocked_reason}`, '#b91c1c', '#fdeaea') : '',
    item.flagged ? '<span class="flag" title="已标记">★</span>' : '',
  ].filter(Boolean).join('')

  return `<div class="irow ${done ? 'is-done' : ''} ${overdue ? 'is-overdue' : ''}" data-action="open-item" data-id="${esc(item.id)}">
  <button class="irow-check" type="button" data-action="quick-done" data-id="${esc(item.id)}" title="${done ? '恢复为待办' : '标记完成'}">${done ? DONE_MARK : ''}</button>
  <div class="irow-main">
    <div class="irow-title">${typeChip(item)} ${esc(item.title)}</div>
    <div class="irow-meta">${meta}</div>
  </div>
  <div class="irow-side">
    ${opts.showAssignee && assigneeName ? `<span class="irow-assignee">${avatar(assigneeName, item.assignee_id)}<span>${esc(shortName(assigneeName))}</span></span>` : ''}
    ${Number(item.progress) > 0 && !done ? `<span class="irow-progress">${esc(String(item.progress))}%</span>` : ''}
  </div>
</div>`
}

/** 看板卡片：信息密度比列表行更高，但不放操作按钮，靠拖拽与详情抽屉 */
export function itemCard(item, lk) {
  const overdue = isOverdue(item)
  const risk = slaRisk(item)
  const project = item.project_id != null ? lk.project(item.project_id) : null
  const assigneeName = item.assignee_name || (item.assignee_id ? lk.nameOfUser(item.assignee_id) : '')
  return `<article class="bcard ${overdue ? 'is-overdue' : ''} ${risk === 'breach' ? 'is-breach' : ''}"
  draggable="true" data-action="open-item" data-id="${esc(item.id)}" data-status="${esc(item.status)}">
  <div class="bcard-top">
    ${typeChip(item)}
    ${priorityChip(item)}
    ${risk ? `<span class="bcard-sla ${risk}">${esc(risk === 'breach' ? 'SLA 超时' : 'SLA 临期')}</span>` : ''}
  </div>
  <div class="bcard-title">${esc(item.title)}</div>
  ${project ? `<div class="bcard-project">#${esc(project.name)}</div>` : ''}
  <div class="bcard-foot">
    <span class="bcard-assignee">${assigneeName ? `${avatar(assigneeName, item.assignee_id)}<span>${esc(shortName(assigneeName))}</span>` : '<span class="muted">未分派</span>'}</span>
    ${item.due_date ? `<span class="bcard-due ${overdue ? 'late' : ''}">${esc(overdue ? `逾期 ${Math.abs(diffDays(todayStr(), item.due_date))}天` : describeDate(item.due_date))}</span>` : ''}
  </div>
  ${Number(item.progress) > 0 ? `<div class="bcard-progress">${progressBar(item.progress)}</div>` : ''}
</article>`
}

/** 进度时间线：把"谁在什么时候推进到哪一步"显式化，替代口头同步 */
export function timeline(logs, lk, limit = 30) {
  const list = (logs || []).slice(0, limit)
  if (!list.length) return emptyState('尚无进度记录', '在事项详情里更新进度，团队就能看到变化的过程。')
  const kindLabel = { progress: '进度更新', comment: '评论', blocked: '受阻', handover: '交接' }
  return `<ol class="timeline">${list.map((g) => `<li class="tl-item">
    <span class="tl-dot tl-${esc(g.kind || 'progress')}"></span>
    <div class="tl-body">
      <div class="tl-head">
        <strong>${esc(g.author_name || lk.nameOfUser(g.owner_id) || '成员')}</strong>
        <span class="muted">${esc(relativeTime(g.created_at))}</span>
        <span class="tl-kind">${esc(kindLabel[g.kind] || '记录')}</span>
        ${g.progress != null ? `<span class="tl-pct">${esc(String(g.progress))}%</span>` : ''}
      </div>
      ${g.item_title && !g.item_id ? '' : ''}
      <div class="tl-note">${esc(g.note || '')}</div>
    </div>
  </li>`).join('')}</ol>`
}

/* ============================================================
 * 6. 个人空间视图（GTD 域）
 * ============================================================ */

function rowsSection(title, list, lk, opts = {}, emptyHint = '') {
  const body = list.length
    ? list.map((i) => itemRow(i, lk, opts)).join('')
    : emptyState(emptyHint || '这里暂时是空的', opts.emptyHint || '')
  return card(title, body, { aside: `<span class="muted">${list.length} 项</span>`, class: opts.class || '' })
}

/** 收集箱：GTD 第一站。强调"先记下来，不判断"，降低捕获摩擦 */
function viewInbox(state, lk) {
  const m = computePersonalMetrics(state.data, state.user?.id)
  const rows = m.inbox
  const guide = `<div class="guide">
  <div class="guide-step"><span class="guide-num">1</span><div><strong>收集不判断</strong><p>任何念头先扔进来。这一步只要求"记下来"。</p></div></div>
  <div class="guide-step"><span class="guide-num">2</span><div><strong>两分钟原则</strong><p>能两分钟做完的，当场做掉，不进入系统。</p></div></div>
  <div class="guide-step"><span class="guide-num">3</span><div><strong>要有人做就委派</strong><p>团队事项指派给成员，个人事项转"等待他人"。</p></div></div>
  <div class="guide-step"><span class="guide-num">4</span><div><strong>其余变成下一步行动</strong><p>必须落到一个具体动作，而不是一个愿望。</p></div></div>
</div>`

  return pageHeader(
    '收集箱',
    '一切未厘清的输入都先落在这里，大脑只负责产生想法，不负责记住想法。',
    `<button class="btn btn-primary" data-action="new-item" data-scope="personal" data-status="intake">＋ 记录一件事</button>`,
  ) + `
  <div class="grid-2">
    ${card('待厘清的输入', rows.length
      ? rows.map((i) => itemRow(i, lk, { scope: 'personal', showTeam: true })).join('')
      : emptyState('收集箱已清空', '这是 GTD 状态最好的时候。继续保持。'))}
    ${card('厘清动作怎么选', guide)}
  </div>
  ${rowsSection('我的团队事项概览', m.open.filter((i) => i.team_id != null).slice(0, 6), lk, { scope: 'personal', showTeam: true, showAssignee: false }, '暂无团队分配的事项')}`
}

/** 待厘清：给每个输入一个明确的去处，避免"收集箱永远在长草" */
function clarifyRow(item, lk) {
  return `<div class="crow" data-action="open-item" data-id="${esc(item.id)}">
  <div class="crow-main">
    <div class="crow-title">${typeChip(item)} ${esc(item.title)}</div>
    <div class="crow-meta">${item.team_id != null ? chip(lk.team(item.team_id)?.name || '团队', '#0f766e', '#e6f4f1') : chip('个人事项', '#475569', '#f1f5f9')}
      ${item.notes ? chip('有备注', '#475569', '#f1f5f9') : ''}</div>
  </div>
  <div class="crow-actions">
    <button class="btn btn-sm btn-primary" data-action="set-status" data-id="${esc(item.id)}" data-status="ready">下一步</button>
    <button class="btn btn-sm" data-action="set-status" data-id="${esc(item.id)}" data-status="waiting">等他人</button>
    <button class="btn btn-sm" data-action="set-status" data-id="${esc(item.id)}" data-status="someday">将来</button>
    <button class="btn btn-sm btn-ghost" data-action="set-status" data-id="${esc(item.id)}" data-status="archived">丢弃</button>
  </div>
</div>`
}

function viewClarify(state, lk) {
  const m = computePersonalMetrics(state.data, state.user?.id)
  const rows = m.triage
  return pageHeader(
    '厘清',
    '逐个问三个问题：这是什么？需要行动吗？下一步具体动作是什么？',
    '',
  ) + card(
    `待厘清（${rows.length}）`,
    rows.length
      ? `<p class="hint">选定去向就退出厘清流程。选不出下一步，往往说明这件事本身还需要先沟通。</p>${rows.map((i) => clarifyRow(i, lk)).join('')}`
      : emptyState('没有待厘清的条目', '收集箱里的每一条都已经有了归属。'),
  )
}

/** 今日执行：把"现在能做什么"收敛到一个可承受的量 */
function viewFocus(state, lk) {
  const m = computePersonalMetrics(state.data, state.user?.id)
  const focus = m.focus
  const over = focus.length > 5
  const advice = over
    ? `<div class="callout warn">今日焦点已有 ${focus.length} 项，超过建议上限 5 项。多项并行是运维最常见的隐性成本，建议把次要项移到"下一步行动"。 </div>`
    : `<div class="callout ok">今日焦点 ${focus.length} 项，处于可完成的量级。</div>`

  const quick = m.ready.filter((i) => Number(i.est_hours) > 0 && Number(i.est_hours) <= 0.5)

  return pageHeader(
    '今日执行',
    `按当前情境选择可执行动作，而不是按清单顺序。今天 ${todayStr()}`,
    `<button class="btn btn-primary" data-action="new-item" data-scope="personal" data-status="ready">＋ 新增下一步行动</button>`,
  ) + advice + `
  <div class="grid-3">
    ${statTile('今日焦点', focus.length, '已排入今天', focus.length > 5 ? 'is-warn' : '')}
    ${statTile('逾期未决', m.overdue.length, '需要重新承诺或授权', m.overdue.length ? 'is-danger' : '')}
    ${statTile('待办池', m.ready.length, '已明确下一步')}
  </div>
  ${rowsSection('今日焦点', focus, lk, { scope: 'personal', showTeam: true }, '今天还没有排定焦点，从"下一步行动"里挑 3 件最重要的。')}
  ${rowsSection('逾期未决', m.overdue, lk, { scope: 'personal', showTeam: true }, '没有逾期事项，节奏可控。')}
  ${rowsSection('半小时内可顺手完成', quick, lk, { scope: 'personal', showTeam: true }, '没有短平快的任务，说明剩下的都需要整块时间。')}`
}

/** 下一步行动：按情境分组，这是 GTD "情境驱动"最直接的落地 */
function viewNext(state, lk) {
  const m = computePersonalMetrics(state.data, state.user?.id)
  const filter = state.filters?.context || 'all'
  const all = m.ready
  const contexts = (state.data.contexts || []).slice().sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0))

  const tabs = [`<button class="tab ${filter === 'all' ? 'is-on' : ''}" data-action="filter" data-key="context" data-value="all">全部 ${all.length}</button>`]
    .concat(contexts.map((c) => {
      const n = all.filter((i) => i.context_id === c.id).length
      return `<button class="tab ${String(filter) === String(c.id) ? 'is-on' : ''}" data-action="filter" data-key="context" data-value="${esc(c.id)}">@${esc(c.name)} ${n}</button>`
    }))
    .concat([`<button class="tab ${filter === 'none' ? 'is-on' : ''}" data-action="filter" data-key="context" data-value="none">未分类 ${all.filter((i) => !i.context_id).length}</button>`])
    .join('')

  const list = filter === 'all'
    ? all
    : filter === 'none'
      ? all.filter((i) => !i.context_id)
      : all.filter((i) => String(i.context_id) === String(filter))

  const grouped = filter === 'all'
    ? contexts.map((c) => ({ title: `@${c.name}`, list: list.filter((i) => i.context_id === c.id) }))
      .concat([{ title: '未标注情境', list: list.filter((i) => !i.context_id) }])
      .filter((g) => g.list.length)
    : [{ title: '筛选结果', list }]

  return pageHeader('下一步行动', '每一个条目都应该是一个可以立刻动手的具体动作。', '', `<div class="tabs">${tabs}</div>`)
    + (grouped.length
      ? grouped.map((g, idx) => rowsSection(`${g.title}`, g.list, lk, { scope: 'personal', showTeam: true, class: idx === 0 ? 'first' : '' })).join('')
      : card('下一步行动', emptyState('没有可执行的下一步', '先清空收集箱，再把待厘清项转成具体动作。')))
}

/** 等待与委派：GTD 里最容易失效的清单，因此把"该跟进谁"直接写在标题上 */
function viewWaiting(state, lk) {
  const m = computePersonalMetrics(state.data, state.user?.id)
  const stale = m.waiting.filter((i) => {
    const ref = i.updated_at || i.created_at
    return ref && Date.now() - new Date(ref).getTime() > 7 * 86400000
  })
  return pageHeader('等待他人', '委派出去的事不会自己回来。这里的每一项都需要一次明确的跟进。') + `
  <div class="grid-3">
    ${statTile('等待中', m.waiting.length, '已委派或依赖外部')}
    ${statTile('超过一周未动', stale.length, '建议今天追问一次', stale.length ? 'is-warn' : '')}
    ${statTile('我发起未分派', m.open.filter((i) => i.team_id != null && !i.assignee_id).length, '团队事项需要指定负责人')}
  </div>
  ${rowsSection('等待清单', m.waiting, lk, { scope: 'personal', showTeam: true, showAssignee: true }, '没有等待事项。')}`
}

/** 日程：只看"有明确时间约束"的事，避免日程被愿望塞满 */
function viewCalendar(state, lk) {
  const anchor = state.weekAnchor || todayStr()
  const start = startOfWeek(anchor)
  const days = Array.from({ length: 7 }, (_, i) => addDays(start, i))
  const mine = myItems(state.data, state.user?.id).filter(isOpen)
  const weekItems = mine.filter((i) => {
    const ref = i.due_date || i.start_date
    return ref && ref >= start && ref <= days[6]
  })
  const noDate = mine.filter((i) => !i.due_date && !i.start_date && i.status === 'ready')

  const cols = days.map((d, idx) => {
    const dayList = mine.filter((i) => (i.due_date || i.start_date) === d)
    const isToday = d === todayStr()
    return `<div class="cal-col ${isToday ? 'is-today' : ''}">
    <div class="cal-head"><span>${WEEK_LABELS[idx]}</span><span class="muted">${esc(d.slice(5))}</span></div>
    <div class="cal-body">${dayList.length ? dayList.map((i) => `<div class="cal-item" data-action="open-item" data-id="${esc(i.id)}">
      ${typeChip(i)}<span class="cal-title">${esc(i.title)}</span></div>`).join('') : '<div class="cal-empty">—</div>'}</div>
  </div>`
  }).join('')

  return pageHeader(
    '日程',
    '只有不可移动的时间约束才放这里。其余都属于"下一步行动"。',
    `<button class="btn" data-action="week" data-delta="-7">← 上一周</button>
     <button class="btn" data-action="week" data-delta="0">本周</button>
     <button class="btn" data-action="week" data-delta="7">下一周 →</button>`,
  ) + card(`本周（${weekItems.length} 项）`, `<div class="cal-grid">${cols}</div>`)
    + rowsSection('有下一步但没有日期', noDate, lk, { scope: 'personal', showTeam: true }, '都排好日期了。')
}

function viewSomeday(state, lk) {
  const m = computePersonalMetrics(state.data, state.user?.id)
  return pageHeader('将来 / 也许', '不承诺、但不想丢的想法。每次复盘重新评估：启动、还是删掉。') +
    rowsSection('清单', m.someday, lk, { scope: 'personal', showTeam: true }, '还没有沉淀下来的想法。')
}

/** 团队分配给我的 —— 个人空间与团队空间的桥。个人在这里"跟进"，团队在那里"管理" */
function viewAssigned(state, lk) {
  const list = assignedToMe(state.data, state.user?.id)
  const open = list.filter(isOpen)
  const doing = open.filter((i) => i.status === 'doing')
  const overdue = open.filter(isOverdue)
  const needsUpdate = doing.filter((i) => {
    const ref = i.updated_at || i.created_at
    return ref && Date.now() - new Date(ref).getTime() > 3 * 86400000
  })
  const unclarified = open.filter((i) => i.status === 'intake' || i.status === 'triage')

  return pageHeader(
    '团队分配给我的',
    '团队按流程管理这些工作，你按 GTD 管理自己的注意力。两边通过进度更新保持同步。',
    `<button class="btn btn-primary" data-action="nav" data-view="focus">进入今日执行</button>`,
  ) + `
  <div class="grid-4">
    ${statTile('在办', open.length, `${list.length} 项累计`)}
    ${statTile('处理中', doing.length, '占用并行带宽')}
    ${statTile('逾期', overdue.length, '需要重新排期或升级', overdue.length ? 'is-danger' : '')}
    ${statTile('待厘清', unclarified.length, '团队已分派但未规划', unclarified.length ? 'is-warn' : '')}
  </div>
  ${needsUpdate.length ? `<div class="callout warn">有 ${needsUpdate.length} 项处理中工作超过 3 天没有更新进度。团队空间看到的是"静默"，容易被误判为停滞。</div>` : ''}
  ${rowsSection('在办事项', open, lk, { scope: 'personal', showTeam: true, showAssignee: false }, '当前没有分配给你的团队工作。')}
  ${rowsSection('已完成（近 20 条）', list.filter((i) => i.status === 'done').slice(0, 20), lk, { scope: 'personal', showTeam: true }, '')}`
}

/** 我的项目：个人项目 + 我所在团队的项目，标出"没有下一步"的停滞项 */
function viewMyProjects(state, lk) {
  const uid = state.user?.id
  const myTeamIds = [...new Set(myItems(state.data, uid).filter((i) => i.team_id != null).map((i) => i.team_id))]
  const projects = (state.data.projects || []).filter((p) => myTeamIds.includes(p.team_id))
  const active = projects.filter((p) => p.status === 'active')

  const cardHtml = (p) => {
    const team = lk.team(p.team_id)
    const pItems = (state.data.items || []).filter((i) => i.project_id === p.id)
    const doneN = pItems.filter((i) => i.status === 'done').length
    const nextN = pItems.filter((i) => i.status === 'ready').length
    const sd = projStatusDef(p.status)
    return `<div class="pcard" data-action="open-project" data-id="${esc(p.id)}">
    <div class="pcard-head">
      <span class="pcard-dot" style="background:${esc(p.color || '#0f766e')}"></span>
      <span class="pcard-name">${esc(p.name)}</span>
      <span class="chip" style="color:${sd.color};background:#f1f5f9">${esc(sd.label)}</span>
    </div>
    <div class="pcard-meta">
      ${chip(team?.name || '团队', '#0f766e', '#e6f4f1')}
      ${chip(projKindDef(p.kind).label, '#0369a1', '#e5f0fb')}
      ${p.due_date ? chip(`目标 ${describeDate(p.due_date)}`, '#475569', '#f1f5f9') : ''}
    </div>
    ${progressBar(p.progress)}
    <div class="pcard-foot">
      <span class="muted">行动 ${pItems.length} · 完成 ${doneN}</span>
      ${nextN === 0 ? '<span class="warn-inline">缺少下一步行动</span>' : `<span class="muted">待动 ${nextN}</span>`}
    </div>
  </div>`
  }

  return pageHeader('我的项目', '项目 = 需要多个动作才能达成的结果。每个在办项目都必须至少有一个明确的下一步。', '')
    + (active.length ? `<div class="pgrid">${active.map(cardHtml).join('')}</div>` : card('在办项目', emptyState('暂无在办项目', '运维工作中"项目少"是常态，但要避免把改善类工作永远留在待办里。')))
    + rowsSection('我参与项目的可执行行动', myItems(state.data, uid).filter((i) => isOpen(i) && i.project_id != null && i.status === 'ready'), lk, { scope: 'personal', showTeam: true }, '没有与项目关联的待办行动。')
}

/** 情境管理：运维情境 = 工作模式与工具，而不是地点 */
function viewContexts(state, lk) {
  const contexts = (state.data.contexts || []).slice().sort((a, b) => (a.sort_order || 0) - (b.sort_order || 0))
  const mine = myItems(state.data, state.user?.id).filter(isOpen)
  const body = contexts.length
    ? `<div class="ctxgrid">${contexts.map((c) => {
      const n = mine.filter((i) => i.context_id === c.id).length
      return `<div class="ctxcard" data-action="filter" data-key="context" data-value="${esc(c.id)}" data-goto="next">
      <div class="ctxcard-top"><span class="ctx-name">@${esc(c.name)}</span><span class="ctx-count">${n}</span></div>
      <div class="muted small">${esc(c.team_id == null ? '个人情境' : (lk.team(c.team_id)?.name || '团队情境'))}</div>
      <div class="ctxcard-actions">
        <button class="btn btn-sm btn-ghost" data-action="edit-context" data-id="${esc(c.id)}">编辑</button>
        <button class="btn btn-sm btn-ghost" data-action="delete-context" data-id="${esc(c.id)}">删除</button>
      </div>
    </div>`
    }).join('')}</div>`
    : emptyState('还没有情境', '情境是 GTD 的情境驱动基础。运维场景建议按工作模式划分。')

  return pageHeader(
    '情境',
    '运维的情境不是"在哪里"，而是"此刻能做什么"：值守、巡检、变更窗口、深度专注。',
    `<button class="btn btn-primary" data-action="new-context">＋ 新建情境</button>`,
  ) + card('情境列表', body)
    + card('为什么运维要按模式分情境', `<p class="prose">运维的工作被打断不是因为懒，而是因为注意力被反复切换。<br>把任务按"需要哪种注意力"归类后，你能成批处理同类工作：值守时段处理可中断的请求，变更窗口处理需要停机与回滚预案的操作，深度专注时段处理根因分析。<br>这也是团队空间里"泳道"设计的前提：中断型工作（事件、请求）与计划内工作分列管理，才能看清究竟是哪一类在吃掉产能。</p>`)
}

/** 复盘：GTD 的引擎。日复盘轻、周复盘重 */
function viewReviewPersonal(state, lk) {
  const m = computePersonalMetrics(state.data, state.user?.id)
  const weekKey = (() => {
    const d = new Date()
    const target = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()))
    const dayNum = (target.getUTCDay() + 6) % 7
    target.setUTCDate(target.getUTCDate() - dayNum + 3)
    const firstThursday = new Date(Date.UTC(target.getUTCFullYear(), 0, 4))
    const firstDayNum = (firstThursday.getUTCDay() + 6) % 7
    firstThursday.setUTCDate(firstThursday.getUTCDate() - firstDayNum + 3)
    const week = 1 + Math.round((target - firstThursday) / (7 * 86400000))
    return `${target.getUTCFullYear()}-W${String(week).padStart(2, '0')}`
  })()
  const doneReview = (state.data.reviews || []).find((r) => r.kind === 'weekly' && r.period_key === weekKey && r.team_id == null)
  const todayReview = (state.data.reviews || []).find((r) => r.kind === 'daily' && r.period_key === todayStr() && r.team_id == null)

  const steps = [
    { key: 'inbox', label: '清空收集箱', n: m.inbox.length, hint: '每一条都要有去处' },
    { key: 'triage', label: '处理待厘清', n: m.triage.length, hint: '转成具体下一步' },
    { key: 'waiting', label: '跟进等待清单', n: m.waiting.length, hint: '委派的事需要主动追问' },
    { key: 'projects', label: '检查项目是否有下一步', n: m.stalledProjects.length, hint: '停滞项目必须补一个动作' },
    { key: 'overdue', label: '重新承诺逾期项', n: m.overdue.length, hint: '删掉、改期、或降级' },
    { key: 'someday', label: '审视将来/也许', n: m.someday.length, hint: '三个月不动就删掉' },
  ]

  const body = `<div class="rev-steps">${steps.map((s) => `<div class="rev-step">
    <span class="rev-dot ${s.n === 0 ? 'is-ok' : 'is-todo'}">${s.n === 0 ? '✓' : s.n}</span>
    <div class="rev-text"><strong>${esc(s.label)}</strong><span class="muted small">${esc(s.hint)}</span></div>
  </div>`).join('')}</div>`

  return pageHeader('复盘', '没有复盘的 GTD 系统会在两周内失效。周复盘是维持系统可信度的唯一手段。') + `
  <div class="grid-3">
    ${statTile('本周状态', doneReview ? '已完成' : '待复盘', doneReview ? `完成于 ${formatDateTime(doneReview.completed_at)}` : weekKey, doneReview ? '' : 'is-warn')}
    ${statTile('收集箱', m.inboxClean ? '已清空' : `${m.inbox.length} 项待处理`, '每日至少清空一次', m.inboxClean ? '' : 'is-warn')}
    ${statTile('今日回顾', todayReview ? '已记录' : '未记录', todayReview ? formatDateTime(todayReview.completed_at) : '3 分钟就可以完成')}
  </div>
  ${card('周复盘检查清单', body)}
  <div class="grid-2">
    ${card('今日回顾（2 分钟）', `<p class="hint">只回答一个问题：明天第一件事是什么？</p>
      <label class="field"><span>明天最重要的一件事</span><textarea id="daily-note" rows="3" placeholder="例如：先处理积压的账号权限请求，再开变更评审"></textarea></label>
      <button class="btn btn-primary" data-action="finish-review" data-kind="daily" data-key="${esc(todayStr())}">记录今日回顾</button>`)}
    ${card(`本周复盘（${weekKey}）`, `<p class="hint">清空收集箱 → 检查项目 → 跟进等待 → 重排逾期 → 审视将来。</p>
      <label class="field"><span>本周结论与下周重点</span><textarea id="weekly-note" rows="3" placeholder="例如：中断型请求占比过高，下周与业务方约一次请求分级约定"></textarea></label>
      <button class="btn btn-primary" data-action="finish-review" data-kind="weekly" data-key="${esc(weekKey)}">完成周复盘</button>`)}
  </div>`
}

function viewDone(state, lk) {
  const list = myItems(state.data, state.user?.id).filter((i) => i.status === 'done')
  const recent = list.slice().sort((a, b) => String(b.completed_at || '').localeCompare(String(a.completed_at || '')))
  return pageHeader('已完成', '回看完成记录是维持系统信任感的方式之一。', '')
    + rowsSection('近期完成', recent.slice(0, 60), lk, { scope: 'personal', showTeam: true }, '还没有完成记录。')
}

/* ============================================================
 * 7. 团队空间视图
 * ============================================================ */

function noTeam() {
  return card('还没有团队', emptyState(
    '先创建一个团队，或凭邀请码加入',
    '团队空间承载日常工作、服务台、看板、项目、值班、资源与报表；个人空间负责你自己的跟进节奏。',
    `<div class="empty-btns">
      <button class="btn btn-primary" data-action="new-team">＋ 创建团队</button>
      <button class="btn" data-action="join-team">凭邀请码加入</button>
    </div>`,
  ))
}

/** 团队页头：把"当前在哪个团队"始终写清楚，避免多团队串味 */
function teamHeader(state, lk, title, subtitle, actions = '', tabs = '') {
  const team = lk.team(state.activeTeamId)
  const role = lk.memberOf(state.activeTeamId, state.user?.id)
  const roleTag = role ? chip(roleDef(role.role).label, roleDef(role.role).color, roleDef(role.role).soft) : ''
  const sub = `${subtitle}${subtitle ? ' · ' : ''}团队：${team?.name || '未选择'}`
  return pageHeader(title, sub, `${roleTag}${actions}`, tabs)
}

function teamFilterTabs(state, key, pairs, current) {
  return `<div class="tabs">${pairs.map(([value, label, n]) => `<button class="tab ${String(current) === String(value) ? 'is-on' : ''}" data-action="filter" data-key="${esc(key)}" data-value="${esc(value)}">${esc(label)}${n == null ? '' : ` ${n}`}</button>`).join('')}</div>`
}

/** 团队总览：把"运维在忙什么"变成一眼能看懂的事实 */
function viewTeamOverview(state, lk) {
  if (!state.activeTeamId) return noTeam()
  const team = lk.team(state.activeTeamId)
  const m = computeTeamMetrics(state.data, state.activeTeamId, lk)
  const uid = state.user?.id
  const mine = m.active.filter((i) => i.assignee_id === uid)
  const healthTone = m.health >= 85 ? '状态良好' : m.health >= 70 ? '基本可控' : m.health >= 50 ? '存在压力' : '风险偏高'

  const alerts = []
  if (m.unassigned.length) alerts.push(`${m.unassigned.length} 项在办工作没有负责人`)
  if (m.wip.triage > 2) alerts.push(`待分类积压 ${m.wip.triage} 项，分类不及时会拖慢整体响应`)
  if (m.overdue.length) alerts.push(`${m.overdue.length} 项已逾期`)
  if (m.load.some((l) => l.over)) alerts.push(`${m.load.filter((l) => l.over).length} 名成员在办数超过自报容量`)
  if (m.slaRate !== null && m.slaRate < 80) alerts.push(`SLA 达成率 ${m.slaRate}%，低于 80% 警戒线`)

  return teamHeader(state, lk, '团队总览', team?.mission || '共享空间的全部工作在办情况', '', '')
    + `<div class="grid-2">
      ${card('团队健康度', `<div class="health">
        ${scoreRing(m.health)}
        <div class="health-text">
          <div class="health-title">${esc(healthTone)}</div>
          <div class="muted small">由 SLA 达成（40%）、逾期控制（30%）、流转速率（30%）加权得出</div>
          <div class="health-facts">
            <span>在办 <strong>${m.active.length}</strong></span>
            <span>本周完成 <strong>${m.throughput7}</strong></span>
            <span>SLA 达成 <strong>${m.slaRate === null ? '未建立' : `${m.slaRate}%`}</strong></span>
          </div>
        </div>
      </div>`)}
      ${card('需要关注的信号', alerts.length
        ? `<ul class="alerts">${alerts.map((a) => `<li>${esc(a)}</li>`).join('')}</ul>`
        : emptyState('没有异常信号', '在办工作在负责人、时限与容量上都处于可控状态。'))}
    </div>
    <div class="grid-4">
      ${statTile('待受理', m.wip.intake || 0, '尚未进入流程', (m.wip.intake || 0) > 5 ? 'is-warn' : '')}
      ${statTile('处理中', m.wip.doing || 0, `WIP 上限 ${DEFAULT_WIP.doing}`, (m.wip.doing || 0) > DEFAULT_WIP.doing ? 'is-warn' : '')}
      ${statTile('受阻', m.wip.blocked || 0, '需要外部推动', (m.wip.blocked || 0) > 0 ? 'is-danger' : '')}
      ${statTile('中断型占比', `${m.interruptRate}%`, '事件与请求的产能挤压', m.interruptRate > 50 ? 'is-warn' : '')}
    </div>
    ${rowsSection('分配给我在办的', mine, lk, { scope: 'team', showAssignee: false }, '当前没有分配给你的事项。')}
    ${rowsSection('最近需要推进', m.active.filter((i) => isOverdue(i) || slaRisk(i)).slice(0, 8), lk, { scope: 'team', showAssignee: true }, '没有逾期或临期的推进项。')}`
}

/** 日常工作与巡检 */
function viewTeamRoutine(state, lk) {
  if (!state.activeTeamId) return noTeam()
  const m = computeTeamMetrics(state.data, state.activeTeamId, lk)
  const filter = state.filters?.itemType || 'all'
  const base = m.items.filter((i) => i.item_type === 'routine' || i.item_type === 'inspection')
  const list = filter === 'all' ? base.filter(isOpen) : base.filter((i) => i.item_type === filter && isOpen(i))
  const recurring = base.filter((i) => i.recurrence && isOpen(i))
  const todayIns = base.filter((i) => i.item_type === 'inspection' && isOpen(i) && i.due_date === todayStr())

  return teamHeader(
    state, lk, '日常工作', '例行维护与周期巡检：这类工作没有天然截止日，因此必须显式排期，否则永远排在故障后面。',
    `<button class="btn btn-primary" data-action="new-item" data-scope="team" data-type="routine" data-status="ready">＋ 例行工作</button>
     <button class="btn" data-action="new-item" data-scope="team" data-type="inspection" data-status="ready">＋ 周期巡检</button>`,
    teamFilterTabs(state, 'itemType', [['all', '全部', base.filter(isOpen).length], ['routine', '例行维护', base.filter((i) => i.item_type === 'routine' && isOpen(i)).length], ['inspection', '周期巡检', base.filter((i) => i.item_type === 'inspection' && isOpen(i)).length]], filter),
  ) + `
  <div class="grid-3">
    ${statTile('今日巡检', todayIns.length, todayIns.length ? '待执行' : '今日无到期巡检', todayIns.length ? 'is-warn' : '')}
    ${statTile('周期任务', recurring.length, '已设置重复规则')}
    ${statTile('例行在办', base.filter(isOpen).length, `${base.length} 项累计`)}
  </div>
  ${rowsSection('在办日常工作', list, lk, { scope: 'team', showAssignee: true }, '没有在办的例行工作。')}
  ${card('为什么例行工作要显式排期', `<p class="prose">例行工作的共同特征：不做也不会立刻出事，但积累到某个点就是事故。<br>把巡检做成带重复规则的正式事项后，它才会进入报表——「本月完成巡检 22 次，发现隐患 3 项」比「我们一直在巡检」有说服力得多。</p>`)}`
}

/** 服务台：事件 / 请求 / 变更 / 问题的统一队列 */
function viewTeamDesk(state, lk) {
  if (!state.activeTeamId) return noTeam()
  const m = computeTeamMetrics(state.data, state.activeTeamId, lk)
  const deskTypes = ['incident', 'request', 'change', 'problem', 'event']
  const filter = state.filters?.itemType || 'all'
  const base = m.items.filter((i) => deskTypes.includes(i.item_type))
  const list = (filter === 'all' ? base : base.filter((i) => i.item_type === filter)).filter(isOpen)
  const sorted = list.slice().sort((a, b) => {
    const pr = (priorityDef(a.priority).rank || 9) - (priorityDef(b.priority).rank || 9)
    if (pr !== 0) return pr
    return String(a.sla_due_at || '9999').localeCompare(String(b.sla_due_at || '9999'))
  })

  const counts = {}
  for (const t of deskTypes) counts[t] = base.filter((i) => i.item_type === t && isOpen(i)).length

  return teamHeader(
    state, lk, '服务台', '事件是"坏了"，请求是"要东西"，变更是"要动系统"，问题是"为什么反复坏"。四类工作混在一起处理，是运维失控的开始。',
    `<button class="btn btn-primary" data-action="new-item" data-scope="team" data-type="incident" data-status="intake">＋ 登记事项</button>`,
    teamFilterTabs(state, 'itemType',
      [['all', '全部', list.length]].concat(deskTypes.filter((t) => ITEM_TYPES[t]).map((t) => [t, ITEM_TYPES[t].label, counts[t]])), filter),
  ) + `
  <div class="grid-4">
    ${statTile('P1/P2 在办', sorted.filter((i) => i.priority === 'p1' || i.priority === 'p2').length, '高优先需要先响应', '')}
    ${statTile('SLA 临期或超时', sorted.filter((i) => slaRisk(i)).length, '按剩余时间排序', sorted.filter((i) => slaRisk(i)).length ? 'is-danger' : '')}
    ${statTile('等待外部', sorted.filter((i) => i.status === 'waiting').length, '已委派或依赖供应商')}
    ${statTile('受阻', sorted.filter((i) => i.status === 'blocked').length, '需要推动解除', sorted.some((i) => i.status === 'blocked') ? 'is-warn' : '')}
  </div>
  ${rowsSection('按优先级与时限排序的队列', sorted, lk, { scope: 'team', showAssignee: true }, '队列已清空。')}`
}

/** 看板：显式流程 + 泳道 + WIP 限制 */
function viewTeamBoard(state, lk) {
  if (!state.activeTeamId) return noTeam()
  const team = lk.team(state.activeTeamId)
  const m = computeTeamMetrics(state.data, state.activeTeamId, lk)
  const cfg = { ...DEFAULT_WIP, ...(team?.board_config || {}) }
  const swimFilter = state.filters?.swimlane || 'all'
  const lanes = swimFilter === 'all' ? ['planned', 'unplanned'] : [swimFilter]
  const laneName = { planned: '计划内工作', unplanned: '中断型工作（事件 / 请求）' }

  const laneHtml = lanes.map((lane) => {
    const laneItems = m.active.filter((i) => (i.swimlane || 'planned') === lane)
    const cols = BOARD_COLUMNS.map((col) => {
      const list = laneItems.filter((i) => i.status === col)
      const limit = cfg[col] ?? 0
      const over = limit > 0 && list.length > limit
      const head = col === 'done'
        ? ''
        : `<span class="bcol-wip ${over ? 'over' : ''}">${list.length}${limit > 0 ? ` / ${limit}` : ''}</span>`
      return `<div class="bcol ${over ? 'is-over' : ''}" data-status="${esc(col)}" data-swimlane="${esc(lane)}">
      <div class="bcol-head"><span class="bcol-name">${esc(statusLabel(col, 'team'))}</span>${head}</div>
      <div class="bcol-body">${list.map((i) => itemCard(i, lk)).join('') || '<div class="bcol-empty">—</div>'}</div>
    </div>`
    }).join('')

    return `<section class="lane">
      <div class="lane-head">
        <h3 class="lane-title">${esc(laneName[lane])}</h3>
        <span class="muted small">${laneItems.length} 项在办</span>
      </div>
      <div class="board">${cols}</div>
    </section>`
  }).join('')

  return teamHeader(
    state, lk, '看板', '列即流程。每列的数字是并行任务数，超过上限不是勤奋，而是风险。',
    `<button class="btn btn-primary" data-action="new-item" data-scope="team" data-type="routine" data-status="ready">＋ 新建工作项</button>`,
    teamFilterTabs(state, 'swimlane', [['all', '全部泳道', m.active.length], ['planned', '计划内', m.active.filter((i) => (i.swimlane || 'planned') === 'planned').length], ['unplanned', '中断型', m.active.filter((i) => i.swimlane === 'unplanned').length]], swimFilter),
  ) + (m.active.length ? laneHtml : card('看板', emptyState('看板是空的', '从服务台或日常工作里登记一条工作项，它会出现在"待受理"列。', `<button class="btn btn-primary" data-action="new-item" data-scope="team" data-type="incident" data-status="intake">＋ 登记工作项</button>`)))
   + card('泳道与上限的用法', `<p class="prose">横向两条泳道区分「计划内」与「中断型」。中断型工作（事件、请求）天生会抢占产能，把它单独成道，才能看清它究竟吃掉了多少。<br>列上限建议从保守值开始：处理中同时不超过 5 项、待验证不超过 3 项。触顶时正确做法是"先完成、再拉新"，而不是把卡片硬塞进去。</p>`)
}

/** 项目 */
function viewTeamProjects(state, lk) {
  if (!state.activeTeamId) return noTeam()
  const teamId = state.activeTeamId
  const projects = (state.data.projects || []).filter((p) => p.team_id === teamId)
  const filter = state.filters?.projectStatus || 'active'
  const list = filter === 'all' ? projects : projects.filter((p) => p.status === filter)

  const pcard = (p) => {
    const items = (state.data.items || []).filter((i) => i.project_id === p.id)
    const open = items.filter(isOpen)
    const nextN = open.filter((i) => i.status === 'ready').length
    const doingN = open.filter((i) => i.status === 'doing').length
    const sd = projStatusDef(p.status)
    return `<div class="pcard" data-action="open-project" data-id="${esc(p.id)}">
    <div class="pcard-head">
      <span class="pcard-dot" style="background:${esc(p.color || '#0f766e')}"></span>
      <span class="pcard-name">${esc(p.name)}</span>
      <span class="chip" style="color:${sd.color};background:#f1f5f9">${esc(sd.label)}</span>
    </div>
    <div class="pcard-meta">
      ${chip(projKindDef(p.kind).label, '#0369a1', '#e5f0fb')}
      ${p.lead_name ? chip(`负责人 ${p.lead_name}`, '#4f46e5', '#ecebfd') : ''}
      ${p.due_date ? chip(`目标 ${describeDate(p.due_date)}`, '#475569', '#f1f5f9') : ''}
    </div>
    <p class="pcard-desc">${esc(p.description || '暂无描述')}</p>
    ${progressBar(p.progress)}
    <div class="pcard-foot">
      <span class="muted">行动 ${items.length} · 在办 ${open.length} · 处理中 ${doingN}</span>
      ${nextN === 0 && p.status === 'active' ? '<span class="warn-inline">缺少下一步</span>' : `<span class="muted">待动 ${nextN}</span>`}
    </div>
  </div>`
  }

  return teamHeader(
    state, lk, '项目', '运维团队项目少是常态。真正的难点是：改善类工作永远排在故障之后。',
    `<button class="btn btn-primary" data-action="new-project">＋ 新建项目</button>`,
    teamFilterTabs(state, 'projectStatus', [['active', '进行中', projects.filter((p) => p.status === 'active').length], ['waiting', '等待中', projects.filter((p) => p.status === 'waiting').length], ['done', '已完成', projects.filter((p) => p.status === 'done').length], ['all', '全部', projects.length]], filter),
  ) + (list.length ? `<div class="pgrid">${list.map(pcard).join('')}</div>` : card('项目', emptyState('没有符合条件的项目', '把"监控完善""自动化降本""隐患治理"这类改善工作立成项目，它们才有机会被排进产能。')))
}

/** 值班与交接 */
function viewTeamOnCall(state, lk) {
  if (!state.activeTeamId) return noTeam()
  const teamId = state.activeTeamId
  const anchor = state.weekAnchor || todayStr()
  const matrix = onCallMatrix(state.data, teamId, anchor)
  const rows = (state.data.onCall || []).filter((r) => r.team_id === teamId)

  const cols = matrix.map((d) => `<div class="cal-col ${d.date === todayStr() ? 'is-today' : ''}">
    <div class="cal-head"><span>${esc(d.date.slice(5))}</span></div>
    <div class="cal-body">${d.shifts.map((s) => {
      const r = s.row
      return `<div class="shift ${r ? '' : 'is-empty'}" ${r ? `data-action="open-oncall" data-id="${esc(r.id)}"` : `data-action="new-oncall" data-date="${esc(d.date)}" data-slot="${esc(s.slot)}"`}>
        <div class="shift-slot">${esc(slotDef(s.slot).short)}</div>
        ${r ? `<div class="shift-primary">${esc(r.primary_name || '未指定')}</div>
               ${r.backup_name ? `<div class="shift-backup">备 ${esc(r.backup_name)}</div>` : ''}
               ${r.handover_notes ? '<div class="shift-note">有交接记录</div>' : ''}`
            : '<div class="shift-add">＋ 排班</div>'}
      </div>`
    }).join('')}</div>
  </div>`).join('')

  const recent = rows.slice().sort((a, b) => String(b.shift_date).localeCompare(String(a.shift_date))).slice(0, 10)

  return teamHeader(
    state, lk, '值班与交接', '值班的失效点从来不是技术，而是交接信息不完整：漏掉一条未关闭告警，下个班次要重新排查一遍。',
    `<button class="btn btn-primary" data-action="new-oncall" data-date="${esc(todayStr())}" data-slot="day">＋ 排班</button>
     <button class="btn" data-action="week" data-delta="-7">←</button>
     <button class="btn" data-action="week" data-delta="0">本周</button>
     <button class="btn" data-action="week" data-delta="7">→</button>`,
  ) + card('值班表', `<div class="cal-grid cal-grid-7">${cols}</div>`)
    + card('交接纪律', `<div class="grid-2">
        <div><h4>交班方必须交代</h4><ul class="ticks">
          <li>未关闭告警与正在处理的工单</li>
          <li>本班次执行的变更与操作记录</li>
          <li>受阻与等待外部的事项</li>
          <li>下个班次需要重点盯防的风险点</li>
        </ul></div>
        <div><h4>接班方必须确认</h4><ul class="ticks">
          <li>核心服务状态已核查</li>
          <li>未决事项已接手并明确下一步</li>
          <li>交接记录已写入系统，而不是只在群里说</li>
        </ul></div>
      </div>`)
    + rowsSection('近期排班', recent.map((r) => ({
      id: r.id, title: `${r.shift_date} ${slotDef(r.slot).short} 主班 ${r.primary_name || '未指定'}`,
      item_type: 'routine', status: 'ready', priority: 'p3', progress: 0,
    })), lk, { scope: 'team' }, '还没有排班记录。')
}

/**
 * 资源链接的渲染。
 *
 * 历史实现直接把成员填写的 url 写进 href，只做了 HTML 转义 ——
 * 那对 `javascript:` / `data:` 这类伪协议完全无效。这里改用白名单：
 * 不合规的链接**不渲染成 <a>**（不能点、不新开窗口），只用 aria-disabled 标注，
 * 让用户知道"这条记录有链接但不可打开"，而不是静默消失。
 */
function resourceLink(raw) {
  const u = urlSafety(raw)
  if (u.safe) {
    // 只有 http/https 才需要新窗口；mailto 加 target=_blank 会在部分浏览器留下一个空白标签页
    const newTab = (u.scheme === 'http' || u.scheme === 'https')
      ? ' target="_blank" rel="noopener noreferrer"'
      : ''
    return `<a class="rcard-link" href="${esc(u.href)}"${newTab} data-action="noop">打开链接 →</a>`
  }
  return `<span class="rcard-link is-disabled" aria-disabled="true" title="链接协议不被支持，已禁用">链接不可用 →</span>`
}

/** 资源与知识库 */
function viewTeamResources(state, lk) {
  if (!state.activeTeamId) return noTeam()
  const teamId = state.activeTeamId
  const kinds = ['runbook', 'doc', 'link', 'credential']
  const all = (state.data.resources || []).filter((r) => r.team_id === teamId && !r.archived)
  const filter = state.filters?.resKind || 'all'
  const list = filter === 'all' ? all : all.filter((r) => r.kind === filter)

  const rcard = (r) => `<div class="rcard" data-action="open-resource" data-id="${esc(r.id)}">
    <div class="rcard-top">
      <span class="rcard-icon">${resKindDef(r.kind).icon}</span>
      <span class="rcard-title">${esc(r.title)}</span>
      ${r.pinned ? '<span class="flag">★</span>' : ''}
    </div>
    <div class="rcard-meta">
      ${r.category ? chip(r.category, '#475569', '#f1f5f9') : ''}
      ${r.custodian_name ? chip(`维护 ${r.custodian_name}`, '#4f46e5', '#ecebfd') : ''}
      ${r.review_due ? chip(`复核 ${describeDate(r.review_due)}`, '#b45309', '#fdf3e3') : ''}
    </div>
    <p class="rcard-desc">${esc(r.description || '暂无说明')}</p>
    ${r.url ? resourceLink(r.url) : ''}
  </div>`

  const overdueReview = all.filter((r) => r.review_due && r.review_due < todayStr())

  return teamHeader(
    state, lk, '资源与知识库', '每一次重复解决同一个问题，都是在浪费本该投入改善的时间。把解法沉淀成资源，是团队产能的复利。',
    `<button class="btn btn-primary" data-action="new-resource" data-kind="runbook">＋ 新增资源</button>`,
    teamFilterTabs(state, 'resKind', [['all', '全部', all.length]].concat(kinds.map((k) => [k, RESOURCE_KINDS[k].label, all.filter((r) => r.kind === k).length])), filter),
  ) + `
  <div class="grid-3">
    ${statTile('资源条目', all.length, '手册 / 文档 / 链接 / 凭据位置')}
    ${statTile('待复核', overdueReview.length, '超过复核日期', overdueReview.length ? 'is-warn' : '')}
    ${statTile('手册覆盖', all.filter((r) => r.kind === 'runbook').length, '可复用的操作步骤')}
  </div>
  ${list.length ? `<div class="rgrid">${list.map(rcard).join('')}</div>` : card('资源', emptyState('还没有资源条目', '从最常被问到的那一个问题开始写：新员工如何申请权限？'))}`
}

/** 工具台账 */
function viewTeamTools(state, lk) {
  if (!state.activeTeamId) return noTeam()
  const teamId = state.activeTeamId
  const tools = (state.data.resources || []).filter((r) => r.team_id === teamId && (r.kind === 'tool' || r.kind === 'asset') && !r.archived)
  const filter = state.filters?.toolKind || 'all'
  const list = filter === 'all' ? tools : tools.filter((r) => r.kind === filter)
  const renewSoon = tools.filter((t) => {
    const d = t.meta?.renewal_date
    if (!d) return false
    return d <= addDays(todayStr(), 60)
  })

  const trow = (t) => `<div class="trow" data-action="open-resource" data-id="${esc(t.id)}">
    <span class="rcard-icon">${resKindDef(t.kind).icon}</span>
    <div class="trow-main">
      <div class="trow-title">${esc(t.title)}</div>
      <div class="trow-meta">
        ${t.category ? chip(t.category, '#0369a1', '#e5f0fb') : ''}
        ${t.meta?.vendor ? chip(t.meta.vendor, '#475569', '#f1f5f9') : ''}
        ${t.criticality === 'high' ? chip('关键依赖', '#b91c1c', '#fdeaea') : ''}
        ${t.meta?.renewal_date ? chip(`续期 ${t.meta.renewal_date}`, '#b45309', '#fdf3e3') : ''}
      </div>
      <div class="muted small">${esc(t.description || '')}</div>
    </div>
    <div class="trow-side">${t.custodian_name ? `<span class="muted small">负责人 ${esc(t.custodian_name)}</span>` : '<span class="muted small">未指定负责人</span>'}</div>
  </div>`

  return teamHeader(
    state, lk, '工具台账', '工具的价值来自"有人负责、有人复核"。无人负责的工具迟早变成无人知晓的隐患。',
    `<button class="btn btn-primary" data-action="new-resource" data-kind="tool">＋ 登记工具</button>`,
    teamFilterTabs(state, 'toolKind', [['all', '全部', tools.length], ['tool', '软件工具', tools.filter((t) => t.kind === 'tool').length], ['asset', '硬件资产', tools.filter((t) => t.kind === 'asset').length]], filter),
  ) + `
  <div class="grid-3">
    ${statTile('登记在册', tools.length, '工具与资产')}
    ${statTile('60 天内续期', renewSoon.length, '许可证 / 维保', renewSoon.length ? 'is-warn' : '')}
    ${statTile('无负责人', tools.filter((t) => !t.custodian_name).length, '需要指定归属', tools.some((t) => !t.custodian_name) ? 'is-warn' : '')}
  </div>
  ${list.length ? card('工具清单', list.map(trow).join('')) : card('工具', emptyState('还没有登记工具', '先登记监控、备份、远程接入这三类最核心的工具，标出负责人与续期时间。'))}`
}

/** 报表：把运维工作数据化，用于对外沟通而不是自我表扬 */
function viewTeamReports(state, lk) {
  if (!state.activeTeamId) return noTeam()
  const m = computeTeamMetrics(state.data, state.activeTeamId, lk)
  const total = m.items.length || 1

  const typeBars = Object.keys(ITEM_TYPES)
    .map((t) => barRow(ITEM_TYPES[t].label, m.countTypes[t] || 0, total, ITEM_TYPES[t].color))
    .join('')

  const statusBars = BOARD_COLUMNS
    .map((s) => barRow(statusLabel(s, 'team'), m.wip[s] || 0, m.active.length || 1, statusDef(s).color))
    .join('')

  const loadBars = m.load.length
    ? m.load.map((l) => barRow(
      l.member.display_name || shortName(l.member.email) || '成员',
      l.open, Math.max(...m.load.map((x) => x.open), 1),
      l.over ? '#b91c1c' : l.blocked ? '#b45309' : '#0f766e',
      ` / 容量 ${l.capacity}`,
    )).join('')
    : emptyState('还没有成员负荷数据')

  return teamHeader(state, lk, '报表', '运维团队被误解的根因不是做得不好，而是没有把做过的事变成数据。') + `
  <div class="grid-4">
    ${statTile('累计工作项', m.items.length, `在办 ${m.active.length} / 完成 ${m.done.length}`)}
    ${statTile('近 7 天完成', m.throughput7, `近 30 天 ${m.throughput30}`)}
    ${statTile('SLA 达成率', m.slaRate === null ? '未建立' : `${m.slaRate}%`, m.slaScoped ? `${m.slaOk}/${m.slaScoped} 项承诺时限` : '尚无带时限的关闭项')}
    ${statTile('中断型占比', `${m.interruptRate}%`, '事件与请求占用', m.interruptRate > 50 ? 'is-warn' : '')}
  </div>
  <div class="grid-2">
    ${card('Run / Build 结构', `
      <p class="hint">业界经验值：IT 团队约 70% 产能被 Run（维持运转）占用，仅 30% 可用于 Build（改善与建设）。运维任务多、项目任务少正是这个结构性比例的体现，而不是团队不努力。</p>
      <div class="ratio">
        <div class="ratio-bar">
          <div class="ratio-run" style="width:${clamp(m.runRatio, 0, 100)}%">Run ${m.runRatio}%</div>
          <div class="ratio-build" style="width:${clamp(m.buildRatio, 0, 100)}%">Build ${m.buildRatio}%</div>
        </div>
      </div>
      <div class="ratio-legend">
        <span>Run ${m.runCount} 项（日常 / 巡检 / 事件 / 请求 / 变更 / 问题）</span>
        <span>Build ${m.buildCount} 项（项目任务）</span>
      </div>
      ${m.buildRatio < 15 && m.items.length > 10 ? '<div class="callout warn">Build 占比低于 15%。如果长期如此，技术债会持续累积，改善类工作需要有明确的产能配额（例如每周固定半天）。</div>' : ''}
    `)}
    ${card('健康度拆解', `<div class="health">
      ${scoreRing(m.health)}
      <div class="health-text">
        <div class="health-facts">
          <span>逾期率 <strong>${m.overdueRate}%</strong></span>
          <span>流转速率 <strong>${m.flowScore}%</strong></span>
          <span>SLA <strong>${m.slaRate === null ? '未建立' : `${m.slaRate}%`}</strong></span>
        </div>
        <div class="muted small">健康度是一个沟通工具，不是考核工具。它的作用是让"运维是不是在忙"变成"哪些环节在拖后腿"。</div>
      </div>
    </div>`)}
  </div>
  <div class="grid-2">
    ${card('工作类型分布', typeBars)}
    ${card('在办状态分布（WIP）', statusBars)}
  </div>
  <div class="grid-2">
    ${card('成员负荷', loadBars)}
    ${card('一页纸对外汇报（可直接复制）', `<textarea class="report-text" rows="12" readonly>${esc(buildOnePager(state, lk, m))}</textarea>`)}
  </div>
  ${card('未分派的在办工作', m.unassigned.length
    ? m.unassigned.map((i) => itemRow(i, lk, { scope: 'team' })).join('')
    : emptyState('没有未分派的工作', '每一项在办工作都有明确的负责人。'))}`
}

/** 生成"给管理层看的一页纸"——运维团队最缺的沟通物料 */
function buildOnePager(state, lk, m) {
  const team = lk.team(state.activeTeamId)
  const lines = [
    `【${team?.name || '团队'} · 运维工作简报】`,
    `统计时间：${todayStr()}`,
    '',
    `一、工作量`,
    `· 累计登记工作项 ${m.items.length} 项，其中在办 ${m.active.length} 项，已完成 ${m.done.length} 项。`,
    `· 近 7 天完成 ${m.throughput7} 项，近 30 天完成 ${m.throughput30} 项。`,
    `· 工作结构：Run 类 ${m.runRatio}%，Build 类 ${m.buildRatio}%。`,
    `· 中断型工作（事件与请求）占 ${m.interruptRate}%，这是计划内工作被推迟的主要原因。`,
    '',
    `二、服务质量`,
    `· SLA 达成率：${m.slaRate === null ? '尚未建立承诺时限' : `${m.slaRate}%（${m.slaOk}/${m.slaScoped} 项按期关闭）`}。`,
    `· 逾期在办 ${m.overdue.length} 项，占比 ${m.overdueRate}%。`,
    `· 受阻 ${m.wip.blocked || 0} 项，等待外部 ${m.active.filter((i) => i.status === 'waiting').length} 项。`,
    '',
    `三、产能与风险`,
    `· 处理中并行 ${m.wip.doing || 0} 项（建议上限 5 项）。`,
    `· 成员负荷：${m.load.length ? m.load.map((l) => `${l.member.display_name || shortName(l.member.email)} ${l.open} 项`).join('、') : '暂无成员数据'}。`,
    `· 未分派在办工作 ${m.unassigned.length} 项。`,
    '',
    `四、需要支持`,
    m.interruptRate > 50 ? '· 中断型工作占比过高，建议与业务方约定请求分级与响应时段。' : '· 中断型工作占比处于可控范围。',
    m.buildRatio < 15 ? '· Build 类工作占比偏低，建议为改善类工作预留固定产能。' : '· 改善类工作已获得基本产能。',
    m.overdue.length ? `· 存在 ${m.overdue.length} 项逾期工作，需要重新确认优先级或补充资源。` : '· 无逾期工作。',
  ]
  return lines.join('\n')
}

/** 成员与角色 */
function viewTeamMembers(state, lk) {
  if (!state.activeTeamId) return noTeam()
  const teamId = state.activeTeamId
  const members = lk.members(teamId)
  const me = members.find((m) => m.user_id === state.user?.id)
  const canManage = me && (me.role === 'owner' || me.role === 'admin')
  const m = computeTeamMetrics(state.data, teamId, lk)
  const capacityTotal = members.filter((mm) => mm.status === 'active').reduce((a, b) => a + (Number(b.capacity) || 6), 0)

  const row = (mem) => {
    const l = m.load.find((x) => x.member.id === mem.id) || { open: 0, doing: 0, blocked: 0, overdue: 0, capacity: 6, over: false }
    const rd = roleDef(mem.role)
    const loadPct = clamp(pct(l.open, l.capacity, 0), 0, 150)
    return `<div class="mrow">
      ${avatar(mem.display_name || mem.email, mem.user_id)}
      <div class="mrow-main">
        <div class="mrow-title">${esc(mem.display_name || shortName(mem.email) || '未命名')}
          <span class="chip" style="color:${rd.color};background:${rd.soft}">${esc(rd.label)}</span>
          ${mem.status !== 'active' ? chip('已停用', '#737373', '#f4f4f5') : ''}
        </div>
        <div class="mrow-meta">${esc(mem.email || '未登记邮箱')}${mem.title ? ` · ${esc(mem.title)}` : ''}</div>
      </div>
      <div class="mrow-load">
        <div class="load-text ${l.over ? 'over' : ''}">在办 ${l.open} / 容量 ${l.capacity}</div>
        <div class="load-track"><div class="load-fill ${l.over ? 'over' : ''}" style="width:${clamp(loadPct, 0, 100)}%"></div></div>
        <div class="muted small">处理中 ${l.doing} · 受阻 ${l.blocked} · 逾期 ${l.overdue}</div>
      </div>
      ${canManage && mem.role !== 'owner' ? `<div class="mrow-actions">
        <button class="btn btn-sm" data-action="edit-member" data-id="${esc(mem.id)}">调整</button>
        <button class="btn btn-sm btn-ghost" data-action="remove-member" data-id="${esc(mem.id)}">移出</button>
      </div>` : ''}
    </div>`
  }

  // 账号只能由管理员创建（自助注册已下线）：两个入口用同一套建号组件，可见性按身份收窄。
  // 系统管理员即便不是本团队成员也要能看到入口（引导流程第 4 步就在他手上）。
  const isSystemAdmin = !!state.user?.isSystemAdmin
  const actions = [
    canManage ? '<button class="btn" data-action="new-member-account">＋ 创建成员账号</button>' : '',
    isSystemAdmin ? '<button class="btn" data-action="new-admin-account">＋ 创建团队管理员</button>' : '',
    canManage ? '<button class="btn btn-primary" data-action="invite-member">🔗 邀请成员加入</button>' : '',
  ].filter(Boolean).join('')

  return teamHeader(
    state, lk, '成员与角色', '所有者负责空间治理与资源分配。小团队里一个人可以兼多个角色，但每一件事都必须有唯一负责人。',
    actions,
  ) + `
  <div class="grid-4">
    ${statTile('成员', members.filter((x) => x.status === 'active').length, `共 ${members.length} 条记录`)}
    ${statTile('所有者/管理员', members.filter((x) => x.role === 'owner' || x.role === 'admin').length, '可治理团队空间')}
    ${statTile('团队容量', capacityTotal, '成员自报并行上限之和')}
    ${statTile('平均在办', m.load.length ? Math.round(m.load.reduce((a, b) => a + b.open, 0) / m.load.length) : 0, '每人在办事项数')}
  </div>
  ${card('成员名单', members.length ? members.map(row).join('') : emptyState('还没有成员'))}
  ${card('角色权限约定', `<div class="rolegrid">${Object.keys(ROLES).map((r) => `<div class="rolecard">
    <div class="rolecard-name" style="color:${ROLES[r].color}">${esc(ROLES[r].label)}</div>
    <div class="muted small">${esc(ROLES[r].desc)}</div>
  </div>`).join('')}</div>
  <p class="prose">所有权限最终由数据库行级安全策略强制：非团队成员无法看到团队任何数据；成员无法自行提升角色；只有所有者与管理员可以添加或移除成员。
  <br>账号不由成员自助注册：团队管理员只能为<strong>自己管理的团队</strong>创建「成员」账号；创建「团队管理员」需要系统管理员。</p>`)}`
}

/** 团队复盘：团队层面的运行节奏，与个人 GTD 周复盘是一对 */
function viewTeamReview(state, lk) {
  if (!state.activeTeamId) return noTeam()
  const m = computeTeamMetrics(state.data, state.activeTeamId, lk)
  const weekKey = isoKey(new Date())
  const doneR = (state.data.reviews || []).find((r) => r.team_id === state.activeTeamId && r.kind === 'weekly' && r.period_key === weekKey)

  const agenda = [
    { label: '服务健康度', hint: '哪些是绿的、哪些是黄的，为什么', n: m.wip.blocked || 0 },
    { label: '事件复盘', hint: '有意义的事件与纠正动作，不做追责', n: m.countTypes.incident || 0 },
    { label: '变更计划', hint: '下周要做什么变更，需要什么批准', n: m.countTypes.change || 0 },
    { label: '安全与备份', hint: '补丁、漏洞、恢复演练', n: m.countTypes.inspection || 0 },
    { label: '阻碍与决策', hint: '需要向管理层要的资源或授权', n: m.unassigned.length },
  ]

  return teamHeader(state, lk, '团队复盘', '这是团队的运行节奏：固定的议程、固定的时长，比临时会议更能稳定输出。') + `
  <div class="grid-3">
    ${statTile('本周团队复盘', doneR ? '已完成' : '待进行', doneR ? formatDateTime(doneR.completed_at) : weekKey, doneR ? '' : 'is-warn')}
    ${statTile('建议议程', `${agenda.length} 项`, '30 分钟即可走完')}
    ${statTile('健康度', `${m.health} 分`, m.health >= 70 ? '基本可控' : '需要关注', m.health >= 70 ? '' : 'is-warn')}
  </div>
  ${card('标准议程（借鉴 IT 运营周会的固定结构）', `<div class="rev-steps">${agenda.map((a) => `<div class="rev-step">
    <span class="rev-dot ${a.n === 0 ? 'is-ok' : 'is-todo'}">${a.n === 0 ? '✓' : a.n}</span>
    <div class="rev-text"><strong>${esc(a.label)}</strong><span class="muted small">${esc(a.hint)}</span></div>
  </div>`).join('')}</div>
  <p class="prose">固定议程的价值在于"同一结构反复出现"。当成员每周都看到相同的五段结构，他们才敢把问题摆上桌，而不是留到出事之后。</p>`)}
  ${card('本周复盘记录', `<label class="field"><span>服务健康、事件、变更、下一步重点</span><textarea id="team-weekly-note" rows="5" placeholder="例如：本周事件 3 起，其中 2 起源于同一台存储设备，已立项更换；下周变更窗口做数据库小版本升级"></textarea></label>
    <button class="btn btn-primary" data-action="finish-review" data-kind="weekly" data-key="${esc(weekKey)}" data-team="${esc(state.activeTeamId)}">提交团队复盘</button>`)}`
}

/** 团队设置：所有者与管理员专用 */
function viewTeamSettings(state, lk) {
  if (!state.activeTeamId) return noTeam()
  const team = lk.team(state.activeTeamId)
  const me = lk.memberOf(state.activeTeamId, state.user?.id)
  const canManage = me && (me.role === 'owner' || me.role === 'admin')
  const cfg = { ...DEFAULT_WIP, ...(team?.board_config || {}) }

  if (!canManage) {
    return teamHeader(state, lk, '团队设置', '该页面仅对所有者与管理员开放。') +
      card('权限不足', emptyState('你不是该团队的所有者或管理员', '如需调整团队设置，请联系所有者。'))
  }

  const wipFields = BOARD_COLUMNS.filter((c) => c !== 'done')
    .map((c) => `<label class="field field-inline"><span>${esc(statusLabel(c, 'team'))}</span>
      <input id="wip-${esc(c)}" type="number" min="0" max="99" value="${esc(cfg[c] ?? 0)}" /></label>`).join('')

  return teamHeader(state, lk, '团队设置', '所有者负责空间治理：成员、流程上限、资源归属与项目汇总。') + `
  <div class="grid-2">
    ${card('基本信息', `
      <label class="field"><span>团队名称</span><input id="team-name" type="text" value="${esc(team?.name || '')}" /></label>
      <label class="field"><span>一句话定位</span><input id="team-desc" type="text" value="${esc(team?.description || '')}" placeholder="例如：负责总部与门店的基础设施运维" /></label>
      <label class="field"><span>使命（显示在总览页）</span><textarea id="team-mission" rows="2" placeholder="例如：让业务系统稳定到可以被忽略">${esc(team?.mission || '')}</textarea></label>
      <label class="field"><span>邀请码（成员凭此加入）</span>
        <div class="inline-row"><input id="team-code" type="text" value="${esc(team?.join_code || '')}" />
        <button class="btn btn-sm" data-action="regen-code">重新生成</button></div>
        <span class="muted small">邀请码由服务端校验后才允许加入团队，成员无法凭团队编号自行挂靠。</span>
      </label>
      <button class="btn btn-primary" data-action="save-team">保存团队信息</button>`)}
    ${card('看板 WIP 上限', `
      <p class="hint">触顶时应"先完成再拉新"。上限过松等于没有上限。</p>
      <div class="wip-fields">${wipFields}</div>
      <button class="btn btn-primary" data-action="save-wip">保存上限设置</button>`)}
  </div>
  ${card('危险操作', `<div class="danger-zone">
    <div>
      <strong>归档团队</strong>
      <p class="muted small">归档后团队不再出现在导航中，数据保留。仅所有者可操作。</p>
    </div>
    <button class="btn btn-danger" data-action="archive-team" data-id="${esc(team?.id)}">归档团队</button>
  </div>`)}`
}

function isoKey(date) {
  const target = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()))
  const dayNum = (target.getUTCDay() + 6) % 7
  target.setUTCDate(target.getUTCDate() - dayNum + 3)
  const firstThursday = new Date(Date.UTC(target.getUTCFullYear(), 0, 4))
  const firstDayNum = (firstThursday.getUTCDay() + 6) % 7
  firstThursday.setUTCDate(firstThursday.getUTCDate() - firstDayNum + 3)
  const week = 1 + Math.round((target - firstThursday) / (7 * 86400000))
  return `${target.getUTCFullYear()}-W${String(week).padStart(2, '0')}`
}

/* ============================================================
 * 8. 事项详情抽屉（团队与个人共用的编辑器）
 * ============================================================ */

const options = (pairs, current) => pairs
  .map(([v, l]) => `<option value="${esc(v)}" ${String(current) === String(v) ? 'selected' : ''}>${esc(l)}</option>`)
  .join('')

const NONE = [['', '— 无 —']]

export function itemDrawerHtml(item, state, lk) {
  const scope = item.team_id == null ? 'personal' : 'team'
  const teamId = item.team_id
  const members = teamId ? lk.members(teamId).filter((m) => m.status === 'active') : []
  const projects = teamId ? (state.data.projects || []).filter((p) => p.team_id === teamId) : []
  const areas = teamId ? (state.data.areas || []).filter((a) => a.team_id === teamId && !a.archived) : []
  const contexts = (state.data.contexts || []).filter((c) => (c.team_id == null) === (teamId == null))
  const logs = (state.data.logs || []).filter((g) => g.item_id === item.id)
  const rec = item.recurrence || {}

  const teamFields = scope === 'team' ? `
    <div class="frow">
      <label class="field"><span>负责人</span>
        <select id="f-assignee">${options([[ '', '— 未分派 —' ]].concat(members.map((m) => [m.user_id, m.display_name || shortName(m.email) || '成员'])), item.assignee_id || '')}</select>
      </label>
      <label class="field"><span>所属项目</span>
        <select id="f-project">${options(NONE.concat(projects.map((p) => [p.id, p.name])), item.project_id || '')}</select>
      </label>
    </div>
    <div class="frow">
      <label class="field"><span>责任领域</span>
        <select id="f-area">${options(NONE.concat(areas.map((a) => [a.id, a.name])), item.area_id || '')}</select>
      </label>
      <label class="field"><span>来源 / 报障人</span>
        <input id="f-source" type="text" value="${esc(item.source || '')}" placeholder="例如：客服转来 / 监控告警" />
      </label>
    </div>` : ''

  const slaField = scope === 'team' ? `
    <div class="frow">
      <label class="field"><span>承诺时限（SLA）</span>
        <input id="f-sla" type="datetime-local" value="${esc(toLocalInput(item.sla_due_at))}" />
        <span class="muted small">留空表示不承诺时限。按优先级目标：P1 ${PRIORITY.p1.slaHours}h / P2 ${PRIORITY.p2.slaHours}h / P3 ${PRIORITY.p3.slaHours}h / P4 ${PRIORITY.p4.slaHours}h</span>
      </label>
      <label class="field"><span>泳道</span>
        <select id="f-swimlane">${options([['planned', '计划内'], ['unplanned', '中断型']], item.swimlane || 'planned')}</select>
      </label>
    </div>` : ''

  return `
  <div class="drawer-head">
    <div>
      <div class="drawer-kicker">${esc(scope === 'team' ? (lk.team(teamId)?.name || '团队事项') : '个人事项')}</div>
      <h2 class="drawer-title">${esc(item.title)}</h2>
    </div>
    <button class="icon-btn" data-action="close-drawer" title="关闭">✕</button>
  </div>
  <div class="drawer-body">
    <div class="dsec">
      <label class="field"><span>标题</span><input id="f-title" type="text" value="${esc(item.title)}" /></label>
      <div class="frow">
        <label class="field"><span>类型</span>
          <select id="f-type">${options(Object.keys(ITEM_TYPES).map((t) => [t, `${ITEM_TYPES[t].icon} ${ITEM_TYPES[t].label}`]), item.item_type)}</select>
        </label>
        <label class="field"><span>优先级</span>
          <select id="f-priority">${options(Object.keys(PRIORITY).map((p) => [p, `${PRIORITY[p].label} ${PRIORITY[p].name}`]), item.priority)}</select>
        </label>
      </div>
      <div class="frow">
        <label class="field"><span>状态</span>
          <select id="f-status">${options(Object.keys(ITEM_STATUS).map((s) => [s, statusLabel(s, scope)]), item.status)}</select>
        </label>
        <label class="field"><span>情境</span>
          <select id="f-context">${options(NONE.concat(contexts.map((c) => [c.id, `@${c.name}`])), item.context_id || '')}</select>
        </label>
      </div>
      ${teamFields}
      ${slaField}
    </div>

    <div class="dsec">
      <div class="frow">
        <label class="field"><span>开始日期</span><input id="f-start" type="date" value="${esc(item.start_date || '')}" /></label>
        <label class="field"><span>截止日期</span><input id="f-due" type="date" value="${esc(item.due_date || '')}" /></label>
      </div>
      <div class="frow">
        <label class="field"><span>预估工时（小时）</span><input id="f-est" type="number" step="0.5" min="0" value="${esc(item.est_hours ?? '')}" /></label>
        <label class="field"><span>今日焦点日期</span><input id="f-focus" type="date" value="${esc(item.focus_date || '')}" />
          <span class="muted small">填今天即出现在"今日执行"。</span></label>
      </div>
      <label class="field"><span>完成度 <b id="f-progress-val">${esc(String(item.progress || 0))}%</b></span>
        <input id="f-progress" type="range" min="0" max="100" step="5" value="${esc(String(item.progress || 0))}" />
      </label>
    </div>

    <div class="dsec">
      <div class="frow">
        <label class="field"><span>等待谁 / 等什么</span><input id="f-waiting" type="text" value="${esc(item.waiting_for || '')}" placeholder="例如：等待机房确认电力改造窗口" /></label>
        <label class="field"><span>受阻原因</span><input id="f-blocked" type="text" value="${esc(item.blocked_reason || '')}" placeholder="例如：供应商备件未到" /></label>
      </div>
      <div class="frow frow-check">
        <label class="check"><input id="f-flagged" type="checkbox" ${item.flagged ? 'checked' : ''} /><span>标记为重点</span></label>
        <label class="check"><input id="f-next" type="checkbox" ${item.is_next ? 'checked' : ''} /><span>确认为下一步行动</span></label>
      </div>
    </div>

    <div class="dsec">
      <h3 class="dsec-title">周期规则</h3>
      <p class="muted small">例行维护与巡检建议设置重复规则，否则它们永远排在故障之后。</p>
      <div class="frow frow-3">
        <label class="field"><span>模式</span>
          <select id="f-rec-mode">${options([['', '不重复'], ['fixed', '固定周期'], ['after', '完成后顺延']], rec.mode || '')}</select>
        </label>
        <label class="field"><span>间隔</span><input id="f-rec-interval" type="number" min="1" value="${esc(rec.interval ?? 1)}" /></label>
        <label class="field"><span>单位</span>
          <select id="f-rec-unit">${options([['day', '天'], ['week', '周'], ['month', '月']], rec.unit || 'day')}</select>
        </label>
      </div>
    </div>

    <div class="dsec">
      <label class="field"><span>我的备注（GTD 笔记）</span><textarea id="f-gtd-note" rows="2" placeholder="为什么现在不做 / 做到什么程度算完成">${esc(item.gtd_note || '')}</textarea></label>
      <label class="field"><span>处理记录与说明</span><textarea id="f-notes" rows="4">${esc(item.notes || '')}</textarea></label>
    </div>

    <div class="dsec">
      <h3 class="dsec-title">更新进度</h3>
      <div class="frow frow-3">
        <label class="field"><span>进度</span><input id="p-progress" type="number" min="0" max="100" step="5" value="${esc(String(item.progress || 0))}" /></label>
        <label class="field"><span>记录类型</span>
          <select id="p-kind">${options([['progress', '进度更新'], ['comment', '评论'], ['blocked', '受阻说明'], ['handover', '交接记录']], 'progress')}</select>
        </label>
        <label class="field"><span> </span><button class="btn" data-action="add-progress" data-id="${esc(item.id)}">写入记录</button></label>
      </div>
      <label class="field"><span>说明</span><textarea id="p-note" rows="2" placeholder="例如：已定位到为存储多路径配置问题，明天变更窗口修复"></textarea></label>
    </div>

    <div class="dsec">
      <h3 class="dsec-title">进度时间线</h3>
      ${timeline(logs, lk, 20)}
    </div>
  </div>
  <div class="drawer-foot">
    <button class="btn btn-ghost danger-text" data-action="delete-item" data-id="${esc(item.id)}">删除</button>
    <div class="drawer-foot-right">
      <button class="btn" data-action="close-drawer">取消</button>
      <button class="btn btn-primary" data-action="save-item" data-id="${esc(item.id)}">保存</button>
    </div>
  </div>`
}

function toLocalInput(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (!Number.isFinite(d.getTime())) return ''
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`
}

/* ============================================================
 * 9. 各类表单
 * ============================================================ */

/** 新建事项：只问必要的几件事，其余留到详情里补——降低捕获摩擦 */
export function newItemFormHtml(scope, itemType, status, state, lk) {
  const teamId = scope === 'team' ? state.activeTeamId : null
  const members = teamId ? lk.members(teamId).filter((m) => m.status === 'active') : []
  const projects = teamId ? (state.data.projects || []).filter((p) => p.team_id === teamId && p.status === 'active') : []
  const teamSelect = scope === 'team' ? `
    <div class="frow">
      <label class="field"><span>负责人</span>
        <select id="q-assignee">${options([[ '', '— 稍后分派 —' ]].concat(members.map((m) => [m.user_id, m.display_name || shortName(m.email) || '成员'])), state.user?.id || '')}</select>
      </label>
      <label class="field"><span>所属项目</span>
        <select id="q-project">${options(NONE.concat(projects.map((p) => [p.id, p.name])), '')}</select>
      </label>
    </div>` : ''
  return `
  <div class="modal-head"><h2>${esc(scope === 'team' ? '登记团队工作项' : '记录一件事')}</h2><button class="icon-btn" data-action="close-modal">✕</button></div>
  <div class="modal-body">
    <label class="field"><span>标题</span><input id="q-title" type="text" placeholder="${esc(scope === 'team' ? '例如：三楼打印机无法联机' : '例如：整理桌面布线方案')}" /></label>
    <div class="frow">
      <label class="field"><span>类型</span>
        <select id="q-type">${options(Object.keys(ITEM_TYPES).map((t) => [t, `${ITEM_TYPES[t].icon} ${ITEM_TYPES[t].label}`]), itemType)}</select>
      </label>
      <label class="field"><span>优先级</span>
        <select id="q-priority">${options(Object.keys(PRIORITY).map((p) => [p, `${PRIORITY[p].label} ${PRIORITY[p].name}`]), 'p3')}</select>
      </label>
    </div>
    ${teamSelect}
    <div class="frow">
      <label class="field"><span>截止日期</span><input id="q-due" type="date" value="${esc(status === 'ready' ? todayStr() : '')}" /></label>
      <label class="field"><span>进入状态</span>
        <select id="q-status">${options(Object.keys(ITEM_STATUS).map((s) => [s, statusLabel(s, scope)]), status)}</select>
      </label>
    </div>
  </div>
  <div class="modal-foot">
    <button class="btn" data-action="close-modal">取消</button>
    <button class="btn btn-primary" data-action="save-new-item" data-scope="${esc(scope)}">创建</button>
  </div>`
}

/**
 * 危险操作的产品内二次确认模态。
 * 完全复用现有的 .modal / .modal-head / .modal-foot 基础设施与 openModal()，
 * 按钮文案与危险色均可定制；两个按钮都有 data-action，
 * 由 app.js 的事件委托统一处理并结算 Promise。
 */
export function confirmDialogHtml({ title = '请确认', message = '', okText = '确定', danger = false } = {}) {
  return `
  <div class="modal-head"><h2>${esc(title)}</h2><button class="icon-btn" data-action="dialog-cancel" aria-label="关闭">✕</button></div>
  <div class="modal-body">
    <p class="confirm-dialog-msg">${esc(message)}</p>
  </div>
  <div class="modal-foot">
    <button class="btn" data-action="dialog-cancel">取消</button>
    <button class="btn ${danger ? 'btn-danger' : 'btn-primary'}" data-action="dialog-confirm">${esc(okText)}</button>
  </div>`
}

export function teamFormHtml() {
  return `
  <div class="modal-head"><h2>创建团队</h2><button class="icon-btn" data-action="close-modal">✕</button></div>
  <div class="modal-body">
    <p class="hint">创建后你将成为该团队的所有者，负责成员、任务与资源分配。</p>
    <label class="field"><span>团队名称</span><input id="t-name" type="text" placeholder="例如：基础设施运维组" /></label>
    <label class="field"><span>一句话定位</span><input id="t-desc" type="text" placeholder="例如：负责总部与门店的 IT 基础设施" /></label>
    <label class="field"><span>使命</span><textarea id="t-mission" rows="2" placeholder="例如：让业务系统稳定到可以被忽略"></textarea></label>
  </div>
  <div class="modal-foot">
    <button class="btn" data-action="close-modal">取消</button>
    <button class="btn btn-primary" data-action="save-team-new">创建团队</button>
  </div>`
}

export function joinTeamFormHtml() {
  return `
  <div class="modal-head"><h2>凭邀请码加入团队</h2><button class="icon-btn" data-action="close-modal">✕</button></div>
  <div class="modal-body">
    <p class="hint">邀请码由团队所有者提供。加入后你可以看到该团队的共享空间，并承接分配给你的工作。</p>
    <label class="field"><span>邀请码</span><input id="j-code" type="text" placeholder="例如：K7M2QP" style="text-transform:uppercase" /></label>
  </div>
  <div class="modal-foot">
    <button class="btn" data-action="close-modal">取消</button>
    <button class="btn btn-primary" data-action="save-join">加入团队</button>
  </div>`
}

export function projectFormHtml(project, state, lk) {
  const teamId = project?.team_id || state.activeTeamId
  const members = lk.members(teamId).filter((m) => m.status === 'active')
  const areas = (state.data.areas || []).filter((a) => a.team_id === teamId && !a.archived)
  return `
  <div class="modal-head"><h2>${project ? '编辑项目' : '新建项目'}</h2><button class="icon-btn" data-action="close-modal">✕</button></div>
  <div class="modal-body">
    <label class="field"><span>项目名称</span><input id="p-name" type="text" value="${esc(project?.name || '')}" placeholder="例如：核心交换机双机切换改造" /></label>
    <label class="field"><span>目标说明</span><textarea id="p-desc" rows="3" placeholder="达成什么结果算完成">${esc(project?.description || '')}</textarea></label>
    <div class="frow">
      <label class="field"><span>性质</span>
        <select id="p-kind">${options(Object.keys(PROJECT_KINDS).map((k) => [k, PROJECT_KINDS[k].label]), project?.kind || 'build')}</select>
      </label>
      <label class="field"><span>状态</span>
        <select id="p-status">${options(Object.keys(PROJECT_STATUS).map((s) => [s, PROJECT_STATUS[s].label]), project?.status || 'active')}</select>
      </label>
    </div>
    <div class="frow">
      <label class="field"><span>项目负责人</span>
        <select id="p-lead">${options([[ '', '— 未指定 —' ]].concat(members.map((m) => [m.user_id, m.display_name || shortName(m.email) || '成员'])), project?.lead_id || '')}</select>
      </label>
      <label class="field"><span>责任领域</span>
        <select id="p-area">${options(NONE.concat(areas.map((a) => [a.id, a.name])), project?.area_id || '')}</select>
      </label>
    </div>
    <div class="frow">
      <label class="field"><span>目标日期</span><input id="p-due" type="date" value="${esc(project?.due_date || '')}" /></label>
      <label class="field"><span>完成度 <b id="p-progress-val">${esc(String(project?.progress || 0))}%</b></span>
        <input id="p-progress" type="range" min="0" max="100" step="5" value="${esc(String(project?.progress || 0))}" /></label>
    </div>
  </div>
  <div class="modal-foot">
    <button class="btn btn-ghost danger-text" data-action="delete-project" data-id="${esc(project?.id || '')}" ${project ? '' : 'hidden'}>删除</button>
    <div class="drawer-foot-right">
      <button class="btn" data-action="close-modal">取消</button>
      <button class="btn btn-primary" data-action="save-project" data-id="${esc(project?.id || '')}">保存</button>
    </div>
  </div>`
}

export function contextFormHtml(context, state) {
  const isTeam = context ? context.team_id != null : false
  return `
  <div class="modal-head"><h2>${context ? '编辑情境' : '新建情境'}</h2><button class="icon-btn" data-action="close-modal">✕</button></div>
  <div class="modal-body">
    <p class="hint">情境回答"此刻能做什么"。运维建议按工作模式划分：值守、巡检、变更窗口、深度专注。</p>
    <label class="field"><span>名称</span><input id="c-name" type="text" value="${esc(context?.name || '')}" placeholder="例如：变更窗口" /></label>
    <label class="field"><span>适用范围</span>
      <select id="c-scope">
        ${options([['personal', '个人情境'], ['team', '团队情境']], isTeam ? 'team' : 'personal')}
      </select>
      <span class="muted small">团队情境对该团队所有成员可见，适合统一"值守/巡检/变更窗口"这类工作模式。</span>
    </label>
  </div>
  <div class="modal-foot">
    <button class="btn btn-ghost danger-text" data-action="delete-context" data-id="${esc(context?.id || '')}" ${context ? '' : 'hidden'}>删除</button>
    <div class="drawer-foot-right">
      <button class="btn" data-action="close-modal">取消</button>
      <button class="btn btn-primary" data-action="save-context" data-id="${esc(context?.id || '')}">保存</button>
    </div>
  </div>`
}

export function resourceFormHtml(resource, state, lk) {
  const teamId = state.activeTeamId
  const members = lk.members(teamId).filter((m) => m.status === 'active')
  const kinds = Object.keys(RESOURCE_KINDS)
  return `
  <div class="modal-head"><h2>${resource ? '编辑条目' : '新增资源 / 工具'}</h2><button class="icon-btn" data-action="close-modal">✕</button></div>
  <div class="modal-body">
    <div class="frow">
      <label class="field"><span>类型</span>
        <select id="r-kind">${options(kinds.map((k) => [k, `${RESOURCE_KINDS[k].icon} ${RESOURCE_KINDS[k].label}`]), resource?.kind || 'runbook')}</select>
      </label>
      <label class="field"><span>分类</span><input id="r-category" type="text" value="${esc(resource?.category || '')}" placeholder="例如：网络 / 备份 / 监控" /></label>
    </div>
    <label class="field"><span>名称</span><input id="r-title" type="text" value="${esc(resource?.title || '')}" placeholder="例如：核心交换机割接操作手册" /></label>
    <label class="field"><span>链接地址</span><input id="r-url" type="url" value="${esc(resource?.url || '')}" placeholder="https://" /></label>
    <label class="field"><span>说明</span><textarea id="r-desc" rows="3" placeholder="什么情况下用它 / 关键注意事项">${esc(resource?.description || '')}</textarea></label>
    <div class="frow">
      <label class="field"><span>维护负责人</span>
        <select id="r-custodian">${options([[ '', '— 未指定 —' ]].concat(members.map((m) => [m.user_id, m.display_name || shortName(m.email) || '成员'])), resource?.custodian_id || '')}</select>
      </label>
      <label class="field"><span>复核日期</span><input id="r-review" type="date" value="${esc(resource?.review_due || '')}" />
        <span class="muted small">到期未复核会在列表里标黄。</span></label>
    </div>
    <div class="frow">
      <label class="field"><span>关键程度</span>
        <select id="r-critical">${options([['normal', '一般'], ['high', '关键依赖']], resource?.criticality || 'normal')}</select>
      </label>
      <label class="field"><span>供应商 / 厂商</span><input id="r-vendor" type="text" value="${esc(resource?.meta?.vendor || '')}" /></label>
    </div>
    <div class="frow">
      <label class="field"><span>续期日期</span><input id="r-renewal" type="date" value="${esc(resource?.meta?.renewal_date || '')}" /></label>
      <label class="check"><input id="r-pinned" type="checkbox" ${resource?.pinned ? 'checked' : ''} /><span>置顶显示</span></label>
    </div>
  </div>
  <div class="modal-foot">
    <button class="btn btn-ghost danger-text" data-action="delete-resource" data-id="${esc(resource?.id || '')}" ${resource ? '' : 'hidden'}>删除</button>
    <div class="drawer-foot-right">
      <button class="btn" data-action="close-modal">取消</button>
      <button class="btn btn-primary" data-action="save-resource" data-id="${esc(resource?.id || '')}">保存</button>
    </div>
  </div>`
}

/**
 * 成员调整表单。
 *
 * 只用于**调整已有成员**（角色 / 岗位 / 并行容量 / 状态）。
 * 不提供"输入邮箱直接添加成员"：本平台的身份由邮箱验证码注册产生，
 * 而 team_members.user_id 的列默认值是 auth.uid()，
 * 由他人代填邮箱写行会把成员记录错误绑定到填写者的账号上，
 * 进而导致任务分派归属错乱。成员关系只能由本人凭邀请码建立。
 */
export function memberFormHtml(member, state, lk) {
  const isOwnerRow = member?.role === 'owner'
  if (!member) return invitePanelHtml(state, lk)
  return `
  <div class="modal-head"><h2>调整成员</h2><button class="icon-btn" data-action="close-modal">✕</button></div>
  <div class="modal-body">
    <p class="hint">成员身份由账号本身决定，不可由他人代改邮箱。若要调整可分配的角色与容量，在下方设置。</p>
    <label class="field"><span>成员邮箱（账号标识，不可修改）</span><input id="m-email" type="email" value="${esc(member.email || '')}" readonly /></label>
    <label class="field"><span>显示名称</span><input id="m-name" type="text" value="${esc(member.display_name || '')}" placeholder="例如：老王" /></label>
    <div class="frow">
      <label class="field"><span>角色</span>
        <select id="m-role" ${isOwnerRow ? 'disabled' : ''}>
          ${options(Object.keys(ROLES).map((r) => [r, ROLES[r].label]), member.role || 'member')}
        </select>
      </label>
      <label class="field"><span>岗位</span><input id="m-title" type="text" value="${esc(member.title || '')}" placeholder="例如：网络 / 服务器 / 桌面" /></label>
    </div>
    <div class="frow">
      <label class="field"><span>并行容量</span><input id="m-capacity" type="number" step="1" min="1" max="50" value="${esc(String(member.capacity ?? 6))}" />
        <span class="muted small">同时在办多少项算满载，用于负载预警。</span></label>
      <label class="field"><span>状态</span>
        <select id="m-status">${options([['active', '在用'], ['inactive', '停用']], member.status || 'active')}</select>
        <span class="muted small">停用后该成员立即失去团队数据的访问权限。</span>
      </label>
    </div>
  </div>
  <div class="modal-foot">
    <button class="btn" data-action="close-modal">取消</button>
    <button class="btn btn-primary" data-action="save-member" data-id="${esc(member.id)}">保存</button>
  </div>`
}

/**
 * 邀请面板（成员视图的"邀请成员加入"）。
 *
 * 说明为什么是这样：团队成员的建立只有两条合法路径 ——
 *   1. 创建团队时创建者自动成为所有者（auth.uid() 列默认值）；
 *   2. 本人登录后凭邀请码调用服务端函数加入（服务端写入自己的 uid）。
 * 因此邀请只提供"把邀请码交给对方"这一条路，而不代填邮箱建行。
 */
export function invitePanelHtml(state, lk) {
  const team = lk?.team ? lk.team(state.activeTeamId) : state.data.teams.find((t) => t.id === state.activeTeamId)
  if (!team) return `<div class="modal-head"><h2>邀请成员</h2><button class="icon-btn" data-action="close-modal">✕</button></div>
  <div class="modal-body">${emptyState('请先选择或创建一个团队')}</div>`
  const code = team.join_code || ''
  const members = lk?.members ? lk.members(team.id) : (state.data.members || []).filter((m) => m.team_id === team.id)
  return `
  <div class="modal-head"><h2>邀请成员加入「${esc(team.name)}」</h2><button class="icon-btn" data-action="close-modal">✕</button></div>
  <div class="modal-body">
    <p class="hint">成员关系只能由本人建立：对方先用邮箱注册登录，再在「加入团队」里输入下面的邀请码。
    系统不会代填邮箱创建成员记录——那会把成员的归属错误地绑到你的账号上，导致分派的工作对不上人。</p>
    <label class="field"><span>团队邀请码</span>
      <div class="inline-row">
        <input id="invite-code" type="text" value="${esc(code)}" readonly />
        <button class="btn btn-sm btn-primary" data-action="copy-invite" data-code="${esc(code)}" ${code ? '' : 'disabled'}>复制</button>
        <button class="btn btn-sm" data-action="rotate-invite" data-team="${esc(team.id)}">换一个</button>
      </div>
      <span class="muted small">换一个即时生效：旧邀请码立刻失效，已加入的成员不受影响。</span>
    </label>
    <div class="invite-steps">
      <div class="rev-step"><span class="rev-dot is-todo">1</span><div class="rev-text"><strong>对方注册账号</strong><span class="muted small">邮箱 + 验证码，设置密码</span></div></div>
      <div class="rev-step"><span class="rev-dot is-todo">2</span><div class="rev-text"><strong>输入邀请码加入</strong><span class="muted small">在团队空间的「加入团队」中粘贴</span></div></div>
      <div class="rev-step"><span class="rev-dot is-ok">3</span><div class="rev-text"><strong>你即可分派工作</strong><span class="muted small">对方出现在成员名单后，任务与资源都能指派给他</span></div></div>
    </div>
    <p class="muted small">当前成员 ${members.length} 人（在用 ${members.filter((m) => m.status === 'active').length} 人）。</p>
  </div>
  <div class="modal-foot">
    <button class="btn" data-action="close-modal">完成</button>
  </div>`
}

export function onCallFormHtml(row, state, lk, defaults = {}) {
  const teamId = state.activeTeamId
  const members = lk.members(teamId).filter((m) => m.status === 'active')
  const memberOpts = [[ '', '— 未指定 —' ]].concat(members.map((m) => [m.user_id, m.display_name || shortName(m.email) || '成员']))
  return `
  <div class="modal-head"><h2>${row ? '编辑排班' : '安排值班'}</h2><button class="icon-btn" data-action="close-modal">✕</button></div>
  <div class="modal-body">
    <div class="frow">
      <label class="field"><span>日期</span><input id="o-date" type="date" value="${esc(row?.shift_date || defaults.date || todayStr())}" /></label>
      <label class="field"><span>班次</span>
        <select id="o-slot">${options(Object.keys(SHIFT_SLOTS).map((s) => [s, SHIFT_SLOTS[s].label]), row?.slot || defaults.slot || 'day')}</select>
      </label>
    </div>
    <div class="frow">
      <label class="field"><span>主班</span><select id="o-primary">${options(memberOpts, row?.primary_id || '')}</select></label>
      <label class="field"><span>备班</span><select id="o-backup">${options(memberOpts, row?.backup_id || '')}</select></label>
    </div>
    <label class="field"><span>备注</span><textarea id="o-notes" rows="2" placeholder="例如：当晚有数据库变更，需重点关注">${esc(row?.notes || '')}</textarea></label>
    <label class="field"><span>交接记录</span><textarea id="o-handover" rows="5" placeholder="填写交班清单：未关闭告警 / 进行中工单 / 变更操作 / 受阻事项 / 下班次重点">${esc(row?.handover_notes || '')}</textarea></label>
  </div>
  <div class="modal-foot">
    <button class="btn btn-ghost danger-text" data-action="delete-oncall" data-id="${esc(row?.id || '')}" ${row ? '' : 'hidden'}>删除</button>
    <div class="drawer-foot-right">
      <button class="btn" data-action="close-modal">取消</button>
      <button class="btn btn-primary" data-action="save-oncall" data-id="${esc(row?.id || '')}">保存</button>
    </div>
  </div>`
}

export function projectDrawerHtml(project, state, lk) {
  const items = (state.data.items || []).filter((i) => i.project_id === project.id)
  const open = items.filter(isOpen)
  const byStatus = Object.keys(ITEM_STATUS).map((s) => ({ s, list: items.filter((i) => i.status === s) })).filter((g) => g.list.length)
  const team = lk.team(project.team_id)
  return `
  <div class="drawer-head">
    <div>
      <div class="drawer-kicker">${esc(team?.name || '团队')} · ${esc(projKindDef(project.kind).label)}项目</div>
      <h2 class="drawer-title">${esc(project.name)}</h2>
    </div>
    <button class="icon-btn" data-action="close-drawer">✕</button>
  </div>
  <div class="drawer-body">
    <div class="dsec">
      <p class="prose">${esc(project.description || '暂无目标说明')}</p>
      <div class="pcard-meta">
        ${chip(projStatusDef(project.status).label, projStatusDef(project.status).color, '#f1f5f9')}
        ${project.lead_name ? chip(`负责人 ${project.lead_name}`, '#4f46e5', '#ecebfd') : ''}
        ${project.due_date ? chip(`目标 ${describeDate(project.due_date)}`, '#475569', '#f1f5f9') : ''}
      </div>
      <div class="dsec-title">完成度 ${esc(String(project.progress || 0))}%</div>
      ${progressBar(project.progress)}
    </div>
    <div class="dsec">
      <h3 class="dsec-title">项目下的行动（${items.length}）</h3>
      ${byStatus.length
        ? byStatus.map((g) => `<div class="pgroup"><div class="pgroup-head">${statusChip(g.s, 'team')} ${g.list.length}</div>${g.list.map((i) => itemRow(i, lk, { scope: 'team', showAssignee: true })).join('')}</div>`).join('')
        : emptyState('项目还没有行动', '没有下一步行动的项目会停滞。从这里创建一个具体动作。')}
      <div class="inline-row">
        <input id="project-quick-add" type="text" placeholder="直接加一条属于本项目的行动…" />
        <button class="btn" data-action="add-to-project" data-id="${esc(project.id)}">添加</button>
      </div>
    </div>
  </div>
  <div class="drawer-foot">
    <button class="btn btn-ghost" data-action="edit-project" data-id="${esc(project.id)}">编辑项目</button>
    <div class="drawer-foot-right">
      <button class="btn" data-action="close-drawer">关闭</button>
    </div>
  </div>`
}

/**
 * 当前登录者的展示身份（纯读取，不发请求）。
 *
 * 数据来源优先级由 app.js 决定：state.identity 来自 auth.getUser()，
 * 拿不到时依次回退到 fallbackName（通常是团队内成员记录里的显示名）与邮箱前缀。
 * 任何情况下都不返回"已登录"这类占位文案 —— 拿不到就说拿不到。
 */
export function identityOf(state, fallbackName = '') {
  const it = state?.identity || {}
  const email = String(it.email || '').trim()
  const name = [it.name, fallbackName, emailPrefix(email)]
    .map((v) => String(v || '').trim())
    .find((v) => v && !isPlaceholderIdentity(v)) || '当前账号'
  return { id: it.id || state?.user?.id || '', name, email: isPlaceholderIdentity(email) ? '' : email }
}

/**
 * 修改密码表单。
 *
 * 为什么必须由视图层真的渲染出来：历史上这里只有一个
 * `data-action="change-password"` 的按钮，app.js 的对应分支做的是
 * `$('#pwd-form').hidden = ...`，而**这个元素从未被渲染过** ——
 * 于是用户点"修改密码"完全没反应（`if (form)` 直接跳过），
 * `api.changePassword()` 成了没有任何调用方的死接口，自助改密整体不可用。
 * 因此表单与按钮必须成对出现在同一个函数里，并由 .smoke/contract-test.mjs 守住。
 *
 * 口令规则与后端 `pyapp/security.py` 对齐（MIN=8 / MAX=200），
 * 常量来自 config.js，避免前端两处各写一遍字面量。
 */
function passwordFormHtml() {
  // 刻意用 <div> 而不是 <form>：表单里有 3 个 password 输入框且没有 type=submit 按钮，
  // 按 HTML 规范的隐式提交规则，用户在输入框里按回车会让浏览器提交表单 → 整页刷新，
  // 已填的口令与面板状态全部丢失。改用 div 后回车什么都不会发生，行为可控。
  return `<div class="upanel-pwd" id="pwd-form" hidden>
      <label class="field">
        <span class="field-label">当前密码</span>
        <input id="pwd-old" type="password" autocomplete="current-password" placeholder="输入当前密码" />
      </label>
      <label class="field">
        <span class="field-label">新密码</span>
        <input id="pwd-new" type="password" autocomplete="new-password"
               minlength="${MIN_PASSWORD_LENGTH}" maxlength="${MAX_PASSWORD_LENGTH}"
               placeholder="至少 ${MIN_PASSWORD_LENGTH} 位" />
      </label>
      <label class="field">
        <span class="field-label">确认新密码</span>
        <input id="pwd-confirm" type="password" autocomplete="new-password"
               minlength="${MIN_PASSWORD_LENGTH}" maxlength="${MAX_PASSWORD_LENGTH}"
               placeholder="再输入一次" />
      </label>
      <p class="upanel-pwd-error" id="pwd-error" role="alert" hidden></p>
      <button class="btn btn-primary btn-block btn-sm" type="button" data-action="submit-password">确认修改</button>
      <p class="muted small upanel-pwd-hint">修改成功后，其它设备上的登录会失效。</p>
    </div>`
}

/**
 * 账号下拉面板：把"我是谁、在哪个团队、什么角色"一次说清。
 * 历史版本这里只显示一行邮箱 + 字面量"已登录"，信息量为零。
 *
 * 系统管理员在这里多一个「创建团队管理员」入口：他可能一个团队都不属于，
 * 那样在「成员与角色」页根本进不去，而引导流程第 4 步（建团队管理员）就在他手上。
 * 入口可见性只是 UI，真正的授权在服务端（`FORBIDDEN` / `NOT_TEAM_ADMIN`）。
 */
export function userPanelHtml(ident, version) {
  const name = ident?.name || '当前账号'
  const email = ident?.email || ''
  const teamName = ident?.teamName || ''
  const roleLabel = ident?.roleLabel || ''
  const teamLine = teamName
    ? `团队：${esc(teamName)}${roleLabel ? ` · ${esc(roleLabel)}` : ''}`
    : (ident?.space === 'team' ? '尚未加入任何团队' : '个人空间')
  return `<div class="upanel">
    <div class="upanel-head">
      ${avatar(name, ident?.id || email || name)}
      <div class="upanel-id">
        <div class="upanel-name" title="${esc(name)}">${esc(name)}</div>
        ${email ? `<div class="upanel-mail" title="${esc(email)}">${esc(email)}</div>` : ''}
        <div class="muted small">${esc(teamLine)}${ident?.isSystemAdmin ? ' · 系统管理员' : ''}</div>
      </div>
    </div>
    <div class="upanel-body">
      <button class="upanel-item" data-action="change-password">修改密码</button>
      ${passwordFormHtml()}
      ${ident?.isSystemAdmin ? '<button class="upanel-item" data-action="new-admin-account">创建团队管理员账号</button>' : ''}
      <button class="upanel-item" data-action="nav" data-view="review">开始复盘</button>
      <button class="upanel-item danger-text" data-action="sign-out">退出登录</button>
    </div>
    <div class="upanel-foot muted small">版本 ${esc(version)}</div>
  </div>`
}

/* ============================================================
 * 10. 侧边导航与顶栏
 * ============================================================ */

const PERSONAL_NAV = [
  { view: 'inbox', label: '收集箱', icon: '📥', badge: 'inbox' },
  { view: 'clarify', label: '厘清', icon: '🧩', badge: 'triage' },
  { view: 'focus', label: '今日执行', icon: '🎯', badge: 'focus' },
  { view: 'next', label: '下一步行动', icon: '✅', badge: 'ready' },
  { view: 'waiting', label: '等待他人', icon: '⏳', badge: 'waiting' },
  { view: 'calendar', label: '日程', icon: '🗓️' },
  { view: 'someday', label: '将来 / 也许', icon: '💭', badge: 'someday' },
  { view: 'assigned', label: '团队分配给我的', icon: '🤝', badge: 'assigned' },
  { view: 'myProjects', label: '我的项目', icon: '📁' },
  { view: 'contexts', label: '情境', icon: '🏷️' },
  { view: 'review', label: '复盘', icon: '🔁', badge: 'overdue' },
  { view: 'done', label: '已完成', icon: '📦' },
]

const TEAM_NAV = [
  { view: 'overview', label: '团队总览', icon: '📊' },
  { view: 'routine', label: '日常工作', icon: '🔧' },
  { view: 'desk', label: '服务台', icon: '🎫', badge: 'teamOpen' },
  { view: 'board', label: '看板', icon: '🗂️' },
  { view: 'projects', label: '项目', icon: '🚀' },
  { view: 'oncall', label: '值班与交接', icon: '🌙' },
  { view: 'resources', label: '资源与知识库', icon: '📘' },
  { view: 'tools', label: '工具台账', icon: '🧰' },
  { view: 'reports', label: '报表', icon: '📈' },
  { view: 'members', label: '成员与角色', icon: '👥' },
  { view: 'teamReview', label: '团队复盘', icon: '🔁' },
  { view: 'teamSettings', label: '团队设置', icon: '⚙️' },
]

export function renderSpaceTabs(state) {
  const p = state.space === 'personal'
  return `<div class="spacetabs">
    <button class="spacetab ${p ? 'is-on' : ''}" data-action="switch-space" data-space="personal">个人空间</button>
    <button class="spacetab ${p ? '' : 'is-on'}" data-action="switch-space" data-space="team">团队空间</button>
  </div>`
}

export function renderTeamSelect(state, lk) {
  const teams = (state.data.teams || []).filter((t) => !t.archived)
  if (!teams.length) return '<span class="muted small">尚未加入团队</span>'
  const opts = teams.map((t) => `<option value="${esc(t.id)}" ${String(state.activeTeamId) === String(t.id) ? 'selected' : ''}>${esc(t.name)}</option>`).join('')
  return `<select id="team-select" class="team-select" title="切换团队">${opts}</select>`
}

export function renderSidebar(state) {
  const lk = makeLookups(state.data)
  const uid = state.user?.id
  const pm = computePersonalMetrics(state.data, uid)
  const team = state.activeTeamId ? lk.team(state.activeTeamId) : null
  const tm = state.activeTeamId ? computeTeamMetrics(state.data, state.activeTeamId, lk) : null

  const counts = {
    inbox: pm.inbox.length,
    triage: pm.triage.length,
    focus: pm.focus.length,
    ready: pm.ready.length,
    waiting: pm.waiting.length,
    someday: pm.someday.length,
    assigned: pm.assignedCount,
    overdue: pm.overdue.length,
    teamOpen: tm ? tm.active.length : 0,
  }
  const nav = state.space === 'personal' ? PERSONAL_NAV : TEAM_NAV

  const items = nav.map((n) => {
    const n0 = counts[n.badge] || 0
    return `<button class="nav-item ${state.view === n.view ? 'is-active' : ''}" data-action="nav" data-view="${esc(n.view)}">
      <span class="nav-icon">${n.icon}</span>
      <span class="nav-label">${esc(n.label)}</span>
      ${n0 ? `<span class="nav-badge ${n.badge === 'overdue' ? 'is-danger' : ''}">${n0}</span>` : ''}
    </button>`
  }).join('')

  const teamBlock = state.space === 'team'
    ? (team
      ? `<div class="side-team">
          <div class="side-team-name">${esc(team.name)}</div>
          <div class="muted small">${esc(team.mission || team.description || '共享空间')}</div>
        </div>`
      : `<div class="side-team is-empty">
          <div class="muted small">还没有团队</div>
          <button class="btn btn-sm btn-primary" data-action="new-team">创建团队</button>
          <button class="btn btn-sm" data-action="join-team">凭邀请码加入</button>
        </div>`)
    : ''

  // 左下角常驻身份：谁在登录 + 当前所在空间/团队（团队名比"团队空间"四个字更有信息量）
  const myMember = lk.memberOf(state.activeTeamId, state.user?.id)
  const ident = identityOf(state, myMember?.display_name)
  const spaceLabel = state.space === 'team'
    ? (team ? `团队 · ${team.name}` : '团队空间 · 尚未加入')
    : '个人空间'

  // 系统管理是实例级入口：只对系统管理员出现，且与"个人/团队空间"并列（换空间不该让它消失）
  const systemNav = state.user?.isSystemAdmin
    ? `<nav class="nav nav-system" aria-label="实例管理">
        <button class="nav-item ${state.view === 'system' ? 'is-active' : ''}" data-action="nav" data-view="system">
          <span class="nav-icon">🛡️</span>
          <span class="nav-label">系统管理</span>
        </button>
      </nav>`
    : ''

  return `${teamBlock}
  <nav class="nav" aria-label="主导航">${items}</nav>
  ${systemNav}
  <div class="side-foot" title="${esc([ident.name, ident.email].filter(Boolean).join('  ·  '))}">
    ${avatar(ident.name, ident.id || ident.email || ident.name)}
    <div class="side-foot-meta">
      <div class="side-foot-name">${esc(ident.name)}</div>
      ${ident.email ? `<div class="side-foot-mail" title="${esc(ident.email)}">${esc(ident.email)}</div>` : ''}
      <div class="side-foot-sub" title="${esc(spaceLabel)}">${esc(spaceLabel)}</div>
    </div>
  </div>`
}

/* ============================================================
 * 11. 视图分发
 * ============================================================ */

export function renderView(state) {
  const lk = makeLookups(state.data)
  const v = state.view
  // 系统管理是**实例级**页面，与"当前在哪个空间"无关，所以先于空间分支处理
  if (v === 'system') return viewSystem(state)
  if (state.space === 'team') {
    if (v === 'overview') return viewTeamOverview(state, lk)
    if (v === 'routine') return viewTeamRoutine(state, lk)
    if (v === 'desk') return viewTeamDesk(state, lk)
    if (v === 'board') return viewTeamBoard(state, lk)
    if (v === 'projects') return viewTeamProjects(state, lk)
    if (v === 'oncall') return viewTeamOnCall(state, lk)
    if (v === 'resources') return viewTeamResources(state, lk)
    if (v === 'tools') return viewTeamTools(state, lk)
    if (v === 'reports') return viewTeamReports(state, lk)
    if (v === 'members') return viewTeamMembers(state, lk)
    if (v === 'teamReview') return viewTeamReview(state, lk)
    if (v === 'teamSettings') return viewTeamSettings(state, lk)
    return viewTeamOverview(state, lk)
  }
  if (v === 'inbox') return viewInbox(state, lk)
  if (v === 'clarify') return viewClarify(state, lk)
  if (v === 'focus') return viewFocus(state, lk)
  if (v === 'next') return viewNext(state, lk)
  if (v === 'waiting') return viewWaiting(state, lk)
  if (v === 'calendar') return viewCalendar(state, lk)
  if (v === 'someday') return viewSomeday(state, lk)
  if (v === 'assigned') return viewAssigned(state, lk)
  if (v === 'myProjects') return viewMyProjects(state, lk)
  if (v === 'contexts') return viewContexts(state, lk)
  if (v === 'review') return viewReviewPersonal(state, lk)
  if (v === 'done') return viewDone(state, lk)
  return viewFocus(state, lk)
}

/** 空工作区（首次登录、尚无团队且无任何事项）的引导页 */
export function renderOnboarding(state) {
  return card('开始使用', `<p class="prose">这套工具把两个层次分开：
  <br>· <strong>团队空间</strong>解决"工作怎么流转"——日常工作、事件、请求、变更、项目、值班、资源与报表；
  <br>· <strong>个人空间</strong>解决"注意力怎么分配"——收集、厘清、组织、执行、复盘。
  <br>团队分配给你的每一项工作，都会出现在个人空间的"团队分配给我的"，你在那里跟进，团队在那里看到进度。
  </p>
  <div class="empty-btns">
    <button class="btn btn-primary" data-action="new-team">＋ 创建第一个团队</button>
    <button class="btn" data-action="join-team">凭邀请码加入团队</button>
  </div>`)
}

/* ============================================================
 * 12. 账号体系（CR-2026-001）
 *
 * 三块内容，都只产出 HTML 字符串（交互由 app.js / auth-ui.js 绑定）：
 *   · passwordGateHtml —— 强制改密页（`must_change_password=1` 时唯一能看的屏）；
 *   · accountFormHtml  —— 建号表单（团队管理员建成员 / 系统管理员建团队管理员，同一套组件）；
 *   · accountCreatedHtml —— 建号成功后的一次性初始口令展示。
 * ============================================================ */

/**
 * 强制改密页。
 *
 * 为什么必须是整页而不是弹窗：服务端此刻已经拒绝了一切业务请求，
 * 先渲染主界面再盖弹窗会让业务数据在被拦下之前闪一下。
 *
 * 为什么必须有"当前密码"一栏：`POST /api/auth/password` 要 `oldPassword`。
 * 首次登录场景下用户手里拿的就是创建者给的初始口令，所以这一栏填得出来 ——
 * 反过来，如果服务端改成"凭会话免旧密码重置"，这里就必须同步删掉，否则文案会骗人。
 */
export function passwordGateHtml({ email = '', version = '', alert = '' } = {}) {
  return `<div class="auth-wrap is-single">
  <section class="auth-card">
    <div class="auth-brand"><span class="brand-mark"></span> GTD 工作台</div>
    <h1 class="gate-title">请先设置新密码</h1>
    <p class="auth-lead">这个账号现在用的还是创建者设置的初始密码（首次登录，或口令被管理员重置过）。
      出于安全考虑，必须先换成只有你知道的新密码，之后才能进入工作台。</p>
    ${email ? `<p class="gate-account">当前账号：<b>${esc(email)}</b></p>` : ''}
    <div class="auth-alert" id="gate-error" role="alert" ${alert ? '' : 'hidden'}>${esc(alert)}</div>
    <form class="auth-form" id="gate-form" autocomplete="on">
      <label class="field">
        <span class="field-label">当前密码（创建者给你的初始密码）</span>
        <input id="gate-old" name="oldPassword" type="password" required
               autocomplete="current-password" placeholder="输入现在使用的密码" />
      </label>
      <label class="field">
        <span class="field-label">新密码</span>
        <input id="gate-new" name="newPassword" type="password" required
               minlength="${MIN_PASSWORD_LENGTH}" maxlength="${MAX_PASSWORD_LENGTH}"
               autocomplete="new-password" placeholder="至少 ${MIN_PASSWORD_LENGTH} 位" />
      </label>
      <label class="field">
        <span class="field-label">确认新密码</span>
        <input id="gate-confirm" name="confirmPassword" type="password" required
               minlength="${MIN_PASSWORD_LENGTH}" maxlength="${MAX_PASSWORD_LENGTH}"
               autocomplete="new-password" placeholder="再输入一次" />
      </label>
      <button class="btn btn-primary btn-block" id="gate-submit" type="submit">设置新密码并继续</button>
    </form>
    <p class="auth-hint">修改成功后，你在其它设备上的登录会失效；新密码只有你知道，管理员也看不到。</p>
    <div class="gate-foot">
      <button class="btn btn-ghost btn-sm" type="button" data-gate="signout">换一个账号登录</button>
    </div>
  </section>
  ${version ? `<div class="auth-version" title="构建版本，用于确认页面是否为最新">v${esc(version)} · 本地版</div>` : ''}
</div>`
}

/**
 * 建号表单：两个入口共用一套组件。
 *
 * · mode='member'（团队管理员）：团队锁死为当前团队，**不渲染角色选择器** ——
 *   团队管理员只能创建「成员」。UI 收窄不等于授权，服务端仍会拒绝越权请求；
 *   但界面上不该给出"可以选择"的错觉，否则用户会先选再被拒。
 * · mode='admin'（系统管理员）：只能创建「团队管理员」。
 *   产品裁决（D-1）：系统管理员**不能**建 member，所以这里不出现"成员"这个选项。
 *   角色由"是否选了团队"唯一决定，且 `role` 必须与 `teamId` **同现同缺**：
 *     选了团队 → role='admin'；不选团队 → role=null（"先建一个待分配的账号"）。
 *   把 role 做成可选项就会出现"默认回填 member 但没选团队"这种稳定 400 的组合，故不做选择器。
 *   `owner` 一律不提供：所有权只能由本人创建团队产生（`06` §10.3.3 禁止项 3）。
 *
 * 表单里**没有**口令输入框：初始口令由服务端 `secrets` 生成并只回传一次。
 */
export function accountFormHtml(state, lk, { mode = 'member' } = {}) {
  const isAdminMode = mode === 'admin'
  const teamId = state.activeTeamId
  const team = teamId
    ? (lk?.team ? lk.team(teamId) : (state.data.teams || []).find((t) => t.id === teamId))
    : null
  const teams = (state.data.teams || []).filter((t) => !t.archived)

  const teamFieldHtml = isAdminMode
    ? `<label class="field"><span>加入团队</span>
        <select id="a-team">
          ${options([['', '— 暂不加入（对方之后凭邀请码加入）—']]
            .concat(teams.map((t) => [t.id, t.name])), teamId || '')}
        </select>
        <span class="muted small">选了团队 → 该团队的管理员；不选 → 只建账号、角色留空，等对方登录后自行加入。</span>
      </label>`
    : `<label class="field"><span>加入团队（锁定为当前团队）</span>
        <input id="a-team" type="text" value="${esc(team?.name || '未选择团队')}" readonly />
        <span class="muted small">团队管理员只能为自己管理的团队创建账号。</span>
      </label>`

  // 角色不做选择器：系统管理员只能建 admin，团队管理员只能建 member —— 两种模式都没有"可选项"
  const roleFieldHtml = isAdminMode
    ? `<div class="callout">角色由<strong>是否选择团队</strong>决定，不单独选择：
        选了团队 → <strong>团队管理员</strong>（可管理成员与流程）；不选团队 → 角色留空（待分配）。
        系统管理员不能创建「成员」，也不能授予「所有者」。</div>`
    : `<div class="callout">账号创建后的角色固定为<strong>成员</strong>。如需调整角色，创建完成后再到本页成员名单里修改。</div>`

  return `<div class="modal-head"><h2>${isAdminMode ? '创建团队管理员账号' : '创建成员账号'}</h2>
    <button class="icon-btn" data-action="close-modal">✕</button></div>
  <div class="modal-body">
    <p class="hint">系统会生成一个<strong>一次性初始密码</strong>：只显示这一次，关闭后无法再次查看，请当场转交给本人。</p>
    <label class="field"><span>邮箱（登录名）</span>
      <input id="a-email" type="email" required placeholder="someone@example.com" autocomplete="off" />
      <span class="muted small">该邮箱不能是已有账号；已有账号请把邀请码发给对方，由对方登录后自行加入。</span>
    </label>
    <label class="field"><span>显示名称</span>
      <input id="a-name" type="text" maxlength="64" placeholder="留空则用邮箱前缀" autocomplete="off" />
    </label>
    ${teamFieldHtml}
    ${roleFieldHtml}
    <div class="auth-alert" id="a-error" role="alert" hidden></div>
  </div>
  <div class="modal-foot">
    <button class="btn" data-action="close-modal">取消</button>
    <button class="btn btn-primary" data-action="save-account" data-mode="${esc(mode)}">创建账号</button>
  </div>`
}

/**
 * 建号成功：展示一次性初始口令。
 *
 * 三条纪律（`06` §10.3.2 / §10.7 注 2）：
 *   1. 口令**只**在这里出现一次，不进 state、不写 localStorage、不进 log()；
 *   2. 关闭浮层后 180ms 内容即从 DOM 摘除，"无法再次查看"是字面意思；
 *   3. 页面必须写清"只显示一次"，否则管理员会习惯性地关掉去别处找。
 *
 * @param {{user:object, teamId:number|null, role:string|null, password:string}} result 建号响应原文
 * @param {{teamName?:string}} [opts] 团队名（响应里只有 teamId，名字由调用方从本地数据补）
 */
export function accountCreatedHtml(result = {}, { teamName = '' } = {}) {
  const user = result.user || {}
  const password = String(result.password || '')
  // 角色一律读响应，不要硬编码成「成员」：系统管理员建的是 admin（`06` v1.3）
  const roleLabel = result.role ? roleDef(result.role).label : ''
  const teamLabel = teamName || (result.teamId ? `团队 #${result.teamId}` : '')
  const teamLine = result.teamId
    ? `已加入${esc(teamLabel)}${roleLabel ? `，角色：${esc(roleLabel)}` : ''}`
    : '暂未加入任何团队、角色留空：对方首次登录后可凭团队邀请码加入（成为成员），或自己创建团队（成为所有者）'
  return `<div class="modal-head"><h2>账号已创建</h2>
    <button class="icon-btn" data-action="close-modal">✕</button></div>
  <div class="modal-body">
    <div class="callout warn"><strong>此密码只显示一次，关闭后无法再次查看。</strong>
      现在复制，并通过可信渠道转交给本人；一旦丢失，只能由运维重置。</div>
    <label class="field"><span>登录邮箱（账号标识）</span>
      <input type="text" value="${esc(user.email || '')}" readonly autocomplete="off" />
    </label>
    <label class="field"><span>初始密码（一次性）</span>
      <div class="inline-row">
        <input id="a-password" type="text" value="${esc(password)}" readonly
               autocomplete="off" spellcheck="false" />
        <button class="btn btn-sm btn-primary" type="button" data-action="copy-temp-password">复制</button>
      </div>
      <span class="muted small">对方首次登录后必须先设置新密码，之后才能使用工作台。</span>
    </label>
    <p class="muted small">${teamLine}</p>
    <div class="rev-steps">
      <div class="rev-step"><span class="rev-dot is-todo">1</span>
        <div class="rev-text"><strong>把邮箱与初始密码交给本人</strong>
          <span class="muted small">不要贴在群里或工单里长期留存</span></div></div>
      <div class="rev-step"><span class="rev-dot is-todo">2</span>
        <div class="rev-text"><strong>对方登录后会被要求改密</strong>
          <span class="muted small">改完才能进入工作台</span></div></div>
      <div class="rev-step"><span class="rev-dot is-ok">3</span>
        <div class="rev-text"><strong>在本页成员名单里确认已就位</strong>
          <span class="muted small">之后即可分派工作</span></div></div>
    </div>
  </div>
  <div class="modal-foot">
    <button class="btn btn-primary" data-action="close-modal">我已保存，关闭</button>
  </div>`
}

/* ============================================================
 * 13. 系统管理（实例级）：HTTPS 证书
 *
 * 与「团队设置」的区别必须一眼看出来：这一页改的是**整个实例**（所有团队、所有账号），
 * 不是某个团队的配置。所以用 pageHeader 而不是 teamHeader，副标题也直说。
 *
 * 渲染纪律：
 *   · 服务端返回的证书详情字段**可能是 null**（解析失败时降级），一律走 dash() 兜底，
 *     不要在模板里直接拼 `${data.subject}` —— 那会渲染出 "undefined"（render-test 会红）。
 *   · subject / issuer / 指纹 / 上传人都来自外部文件内容或账号，全部 esc()。
 * ============================================================ */

/** 值可能为 null / 空串 —— 统一显示成破折号，避免 undefined 泄漏到界面上 */
const dash = (v) => (v == null || v === '' ? '—' : esc(v))

function certSourceLabel(source) {
  const map = { uploaded: '已上传', selfsigned: '运维自签名', none: '未配置' }
  return map[source] || '未知来源'
}

/** 剩余有效期的醒目提示：≤7 天红、≤30 天黄（其余不提示，避免天天喊狼来了） */
function certExpiryNotice(days) {
  if (days == null) return ''
  if (days <= 7) {
    return `<div class="callout danger"><strong>证书只剩 ${esc(String(days))} 天有效期</strong>，
      到期后 HTTPS 会直接失败（浏览器显示证书错误）。请尽快更换，别等到当天。</div>`
  }
  if (days <= 30) {
    return `<div class="callout warn">证书还有 ${esc(String(days))} 天到期，建议安排续期。</div>`
  }
  return ''
}

export function viewSystem(state) {
  const head = pageHeader('系统管理', '实例级配置：影响所有团队与所有账号，不属于任何单个团队。')
  if (!state.user?.isSystemAdmin) {
    return head + card('权限不足', emptyState(
      '只有系统管理员可以访问本页',
      '这里的配置会影响整个实例（所有团队与所有账号）。如需调整，请联系系统管理员。',
    ))
  }
  const cert = state.cert || {}
  const data = cert.data || null

  // 集成域三区块与证书并列在同一页（O-19 裁决：不新建视图、不新增导航项）
  return head + certStatusCard(cert, data) + certUploadCard() + certDangerCard(cert, data)
    + integrationSection(state)
}

/** 证书状态卡：未配置 / 读取中 / 读取失败 / 已配置 四态 */
function certStatusCard(cert, data) {
  if (cert.loading && !cert.loaded) {
    return card('HTTPS 证书', '<div class="loading">正在读取证书状态…</div>')
  }
  if (cert.error) {
    return card('HTTPS 证书', `<div class="auth-alert" role="alert">${esc(cert.error)}</div>
      <button class="btn btn-sm" data-action="load-cert">重新读取</button>`)
  }
  if (!data || !data.configured) {
    return card('HTTPS 证书', `<div class="callout"><strong>当前没有配置自定义证书。</strong>
      访问方式取决于服务的启动参数：要么是 HTTP 明文，要么是运维生成的临时自签名证书。
      生产环境建议上传受信任的证书（下方可上传）。</div>
      ${emptyState('还没有配置证书', '支持 PEM 格式：证书（.crt/.pem/.cer）+ 未加密私钥（.key）。上传后由后台自动应用。')}`)
  }

  const days = Number.isFinite(data.daysRemaining) ? data.daysRemaining : null
  const applyLine = cert.applying
    ? '<div class="callout warn">正在应用新证书…请勿关闭页面（每 5 秒检查一次，通常 1 分钟内生效）。</div>'
    : (data.pendingApply
      ? '<div class="callout warn">已保存，正在应用…（后台自动应用，约 1 分钟内生效）</div>'
      : '')

  return card('HTTPS 证书', `
  ${applyLine}
  ${certExpiryNotice(days)}
  <div class="certgrid">
    <div class="cert-item"><div class="cert-item-label">来源</div>
      <div class="cert-item-value">${chip(certSourceLabel(data.source), '#475569', '#f1f5f9')}</div></div>
    <div class="cert-item"><div class="cert-item-label">剩余有效期</div>
      <div class="cert-item-value">${days == null ? chip('未知', '#737373', '#f4f4f5')
        : chip(`${days} 天`, days <= 7 ? 'var(--danger)' : (days <= 30 ? '#b45309' : '#15803d'),
          days <= 7 ? '#fdeaea' : (days <= 30 ? '#fdf3e3' : '#e8f5ec'))}</div></div>
    <div class="cert-item"><div class="cert-item-label">主题（subject）</div>
      <div class="cert-item-value">${dash(data.subject)}</div></div>
    <div class="cert-item"><div class="cert-item-label">签发者（issuer）</div>
      <div class="cert-item-value">${dash(data.issuer)}</div></div>
    <div class="cert-item"><div class="cert-item-label">生效时间</div>
      <div class="cert-item-value">${data.notBefore ? dash(formatDateTime(data.notBefore)) : '—'}</div></div>
    <div class="cert-item"><div class="cert-item-label">到期时间</div>
      <div class="cert-item-value">${data.notAfter ? dash(formatDateTime(data.notAfter)) : '—'}</div></div>
    <div class="cert-item cert-item-wide"><div class="cert-item-label">备用名称（SAN）</div>
      <div class="cert-item-value">${Array.isArray(data.sans) && data.sans.length ? esc(data.sans.join(' · ')) : '—'}</div></div>
    <div class="cert-item cert-item-wide"><div class="cert-item-label">SHA256 指纹</div>
      <div class="cert-item-value cert-fp" title="长按可选择复制">${dash(data.fingerprintSha256)}</div></div>
    <div class="cert-item"><div class="cert-item-label">上传时间</div>
      <div class="cert-item-value">${data.uploadedAt ? dash(formatDateTime(data.uploadedAt)) : '—'}</div></div>
    <div class="cert-item"><div class="cert-item-label">上传人</div>
      <div class="cert-item-value">${dash(data.uploadedBy)}</div></div>
  </div>
  ${data.selfSigned ? '<div class="callout">自签名证书，浏览器会显示安全警告（这是自签名证书的正常表现，不是配置出错）。</div>' : ''}
  <button class="btn btn-sm" data-action="load-cert">刷新状态</button>`)
}

/** 上传区：文件选择（读成文本）与直接粘贴两条路，后端都收 PEM 文本 */
function certUploadCard() {
  return card('上传证书', `
  <p class="hint">支持 PEM 文本：证书（.crt / .pem / .cer）+ <strong>未加密</strong>的私钥（.key / .pem）。
  提交后页面上的私钥输入会被立刻清空（无论成功或失败），证书文本在失败时保留以便重试。</p>
  <div class="grid-2">
    <label class="field"><span>证书文件</span>
      <input id="cert-crt-file" type="file" accept=".crt,.pem,.cer"
             data-action="cert-file-picked" data-kind="crt" />
      <span class="muted small" id="cert-crt-name">未选择文件</span>
    </label>
    <label class="field"><span>私钥文件</span>
      <input id="cert-key-file" type="file" accept=".key,.pem"
             data-action="cert-file-picked" data-kind="key" />
      <span class="muted small" id="cert-key-name">未选择文件</span>
    </label>
  </div>
  <button class="btn btn-sm" data-action="toggle-cert-paste">直接粘贴 PEM 文本</button>
  <div id="cert-paste" hidden>
    <label class="field"><span>证书 PEM</span>
      <textarea id="cert-crt-pem" rows="5" spellcheck="false"
                placeholder="-----BEGIN CERTIFICATE-----"></textarea>
    </label>
    <label class="field"><span>私钥 PEM（未加密）</span>
      <textarea id="cert-key-pem" rows="5" spellcheck="false"
                placeholder="-----BEGIN PRIVATE KEY-----"></textarea>
    </label>
  </div>
  <div class="auth-alert" id="cert-error" role="alert" hidden></div>
  <div class="inline-row">
    <button class="btn btn-primary" data-action="upload-cert">上传并应用</button>
    <span class="muted small">服务端会校验证书与私钥是否配对、证书是否过期、私钥是否带口令。</span>
  </div>`)
}

/** 移除证书：只有已配置时才出现（没有证书就没有"移除"这个动作） */
function certDangerCard(cert, data) {
  if (!data?.configured) return ''
  return card('危险操作', `<div class="danger-zone">
    <div>
      <strong>移除当前证书</strong>
      <p class="muted small">移除后回退到 HTTP 明文或运维生成的自签名证书，浏览器会提示不安全。
      后台会重新加载监听，期间可能有几秒连接中断。仅系统管理员可操作。</p>
    </div>
    <button class="btn btn-danger" data-action="delete-cert" ${cert.applying ? 'disabled' : ''}>移除证书</button>
  </div>`)
}


/* ============================================================
 * 14. 系统管理（实例级）：集成域（REQ-055）
 *
 * 落点（O-19 裁决）：与证书同页平铺三块卡片，不新建 settings 视图、不新增导航项。
 * 三条诚实反馈纪律：
 *   · 后端目前**没有**读取/写入 apiEnabled / mcpEnabled / 端口的 HTTP 端点，
 *     所以这里只做只读说明 + .env 指引，绝不画一个点了没反应的假开关；
 *   · 令牌明文只在签发/轮换响应里出现一次，只在 tokenCreatedHtml() 的局部模板里活一次，
 *     不进 state、不进 localStorage、不进 log()；
 *   · 区块标题写清"这块是干什么的"，不写裸功能名。
 * ============================================================ */

const INT_SCOPE_DEFS = [
  { key: 'read', label: '读取（read）', hint: '查看工作项、团队与成员等只读数据；默认勾选。' },
  { key: 'mcp', label: 'MCP 接入（mcp）', hint: '仅允许该令牌连接内置 MCP 服务，本身不含任何写权限。' },
  { key: 'write:edit', label: '编辑 / 完成 / 恢复（write:edit）', hint: '允许修改工作项内容与状态。' },
  { key: 'write:archive', label: '归档（write:archive）', hint: '允许归档工作项。' },
  { key: 'write:delete', label: '删除（write:delete）', hint: '允许删除工作项；一律软删除，可从数据侧恢复。' },
]

/** 服务端 config.py 的默认值：接口未回传时只当"默认值提示"，绝不当"当前值" */
const INT_DEFAULT_PORTS = { api: 8790, mcp: 8722 }

function intRuntime(cfg, kind) {
  if (!cfg) return { known: false, enabled: null, port: null }
  const enabled = cfg[kind === 'api' ? 'apiEnabled' : 'mcpEnabled']
  const port = cfg[kind === 'api' ? 'apiPort' : 'mcpPort']
  return { known: typeof enabled === 'boolean', enabled: !!enabled, port }
}

function intStatusChip(cfg, kind) {
  const r = intRuntime(cfg, kind)
  if (!r.known) return chip('开关状态未知', '#737373', '#f1f5f9')
  return r.enabled
    ? chip('运行中', '#15803d', '#e8f5ec')
    : chip('已停用', '#475569', '#f1f5f9')
}

function intEnvCallout(kind) {
  const vars = kind === 'api'
    ? 'GTD_API_ENABLED=1、GTD_API_PORT=8790'
    : 'GTD_MCP_ENABLED=1、GTD_MCP_PORT=8722'
  return `<div class="callout caution"><strong>当前接口不提供开关与端口的读写，这里只做说明。</strong>
    服务端尚未暴露配置端点，页面无法判断服务是否真的在监听，因此也不会画一个点了没反应的开关。
    如需开启，请在服务端 <code>.env</code> 设置 <code>${esc(vars)}</code>，然后重启服务。</div>`
}

function intRuntimeLine(cfg, kind) {
  const r = intRuntime(cfg, kind)
  if (!r.known) return '<p class="muted small">端口：当前接口未提供实时值（上方给出的是服务端默认值）。</p>'
  return `<div class="int-facts">
    <div><span class="int-fact-label">监听地址</span><span class="int-fact-value">127.0.0.1（只读，不对局域网与公网开放）</span></div>
    <div><span class="int-fact-label">端口</span><span class="int-fact-value">${esc(String(r.port ?? INT_DEFAULT_PORTS[kind]))}</span></div>
  </div>`
}

function intTokenStatus(t) {
  if (t.revoked) return chip('已撤销', '#b91c1c', '#fdeaea')
  if (t.expiresAt && Date.parse(t.expiresAt) < Date.now()) return chip('已过期', '#b45309', '#fdf3e3')
  return chip('有效', '#15803d', '#e8f5ec')
}

function intTokenRow(t) {
  const scopes = Array.isArray(t.scopes) ? t.scopes : []
  const scopeText = scopes.length ? scopes.join(' · ') : '—'
  return `<div class="int-token">
    <div class="int-token-head">
      <strong>${esc(t.label || '未命名令牌')}</strong>
      <code class="int-hint">${esc(t.tokenHint || '—')}</code>
      ${intTokenStatus(t)}
    </div>
    <div class="int-token-meta">
      <span>授权范围：${esc(scopeText)}</span>
      <span>最近使用：${esc(t.lastUsedAt ? formatDateTime(t.lastUsedAt) : '从未使用')}</span>
      <span>到期：${esc(t.expiresAt ? formatDateTime(t.expiresAt) : '永不过期')}</span>
    </div>
    <div class="int-token-actions">
      <button class="btn btn-sm" data-action="int-rotate-token" data-id="${esc(String(t.id))}" ${t.revoked ? 'disabled' : ''}>轮换</button>
      <button class="btn btn-sm btn-danger" data-action="int-revoke-token" data-id="${esc(String(t.id))}" ${t.revoked ? 'disabled' : ''}>撤销</button>
    </div>
  </div>`
}

function intTokenListHtml(intg) {
  if (intg.loading && !intg.loaded) return '<div class="loading">正在读取令牌列表…</div>'
  if (intg.error) return `<div class="auth-alert" role="alert">${esc(intg.error)}</div>
    <button class="btn btn-sm" data-action="int-load-tokens">重新读取</button>`
  const tokens = Array.isArray(intg.tokens) ? intg.tokens : []
  if (!tokens.length) return emptyState('尚未签发访问令牌',
    '没有令牌时，本地 API 与 MCP 一律返回 401：「令牌无效或已撤销」。可用下方表单签发。')
  return `<div class="int-tokens">${tokens.map(intTokenRow).join('')}</div>`
}

function intTokenFormHtml() {
  const scopes = INT_SCOPE_DEFS.map((s) => `<label class="int-scope">
    <input type="checkbox" data-scope="${esc(s.key)}" ${s.key === 'read' ? 'checked' : ''} />
    <span><strong>${esc(s.label)}</strong><span class="muted small">${esc(s.hint)}</span></span>
  </label>`).join('')
  return `<div class="int-issue">
    <div class="grid-2">
      <label class="field"><span>用途备注（可选）</span>
        <input id="int-token-label" type="text" maxlength="120" autocomplete="off" placeholder="例如：每日备份脚本" /></label>
      <label class="field"><span>有效期</span>
        <select id="int-token-days">
          <option value="90">90 天（默认）</option>
          <option value="30">30 天</option>
          <option value="365">365 天</option>
        </select></label>
    </div>
    <div class="int-scope-box">
      <div class="int-scope-title">授权范围（scope）</div>
      ${scopes}
    </div>
    <div class="callout"><code>mcp</code> 只代表"能连上 MCP"，**不含写权限**；
      要让 AI 客户端修改数据，必须同时勾选 <code>mcp</code> 与对应的 <code>write:*</code>。</div>
    <div class="auth-alert" id="int-token-error" role="alert" hidden></div>
    <div class="inline-row">
      <button class="btn btn-primary" data-action="int-create-token">签发令牌</button>
      <span class="muted small">每个账号最多 10 个未撤销令牌；明文只在签发响应里出现一次。</span>
    </div>
  </div>`
}

function intLogResultHtml(intg) {
  if (intg.logLoading) return '<div class="loading">正在查询该编号…</div>'
  if (intg.logError) return `<div class="auth-alert" role="alert">${esc(intg.logError)}</div>`
  if (!intg.logQuery) return emptyState('尚未查询',
    '把服务端 500 页面上给出的 8 位编号贴进来，只查这一条请求的排障记录。')
  if (!intg.logResult) return emptyState('没有找到该编号对应的记录',
    '编号可能来自另一个实例，或日志已轮转；请核对页面上的 8 位编号。')
  const r = intg.logResult
  return `<div class="int-log-result">
    <div class="int-facts">
      <div><span class="int-fact-label">编号</span><span class="int-fact-value"><code>${esc(r.request_id || intg.logQuery)}</code></span></div>
      <div><span class="int-fact-label">时间</span><span class="int-fact-value">${esc(r.ts || '—')}</span></div>
      <div><span class="int-fact-label">请求</span><span class="int-fact-value">${esc(r.method || '—')} ${esc(r.path || '—')}</span></div>
      <div><span class="int-fact-label">状态码</span><span class="int-fact-value">${esc(String(r.status ?? '—'))}</span></div>
      <div><span class="int-fact-label">错误码</span><span class="int-fact-value">${esc(r.errcode || '—')}</span></div>
      <div><span class="int-fact-label">耗时</span><span class="int-fact-value">${esc(String(r.duration_ms ?? '—'))} ms</span></div>
    </div>
    ${intg.logDegraded ? `<div class="callout caution">日志未在写入：${esc(intg.logDegraded)}</div>` : ''}
  </div>`
}

/** 三块集成卡片：只在此处组装，不改 renderSidebar / 不新增 view */
export function integrationSection(state) {
  const intg = state.integration || {}
  const cfg = intg.config || null
  const mcpPort = intRuntime(cfg, 'mcp').port ?? INT_DEFAULT_PORTS.mcp
  const apiCard = card('本地 API（供脚本/集成调用，默认关闭）', `
    ${intEnvCallout('api')}
    ${intRuntimeLine(cfg, 'api')}
    <h3 class="int-subtitle">访问令牌</h3>
    ${intTokenListHtml(intg)}
    <h3 class="int-subtitle">签发新令牌</h3>
    ${intTokenFormHtml()}
  `, { aside: intStatusChip(cfg, 'api'), class: 'card-integration' })

  const mcpCard = card('MCP 服务（供 AI 客户端调用，默认关闭且只读）', `
    ${intEnvCallout('mcp')}
    ${intRuntimeLine(cfg, 'mcp')}
    <div class="int-connection">
      <div class="int-facts">
        <div><span class="int-fact-label">传输方式</span><span class="int-fact-value">Streamable HTTP</span></div>
        <div><span class="int-fact-label">地址</span><span class="int-fact-value"><code>http://127.0.0.1:${esc(String(mcpPort))}/mcp</code></span></div>
      </div>
      <button class="btn btn-sm" data-action="int-copy-conn" data-port="${esc(String(mcpPort))}">复制连接配置</button>
    </div>
    <h3 class="int-subtitle">写权限分级（在签发令牌时按 scope 授予）</h3>
    <div class="int-scope-box">
      ${INT_SCOPE_DEFS.slice(2).map((s) => `<div class="int-scope"><span><strong>${esc(s.label)}</strong>
        <span class="muted small">${esc(s.hint)}</span></span></div>`).join('')}
    </div>
    <div class="callout caution">三类权限独立授予，没有联动；<code>mcp</code> 本身没有写权限。
      客户端只带 <code>mcp</code> 时看不到、也调不动任何写工具。</div>
    <div class="callout">不支持项：无 SSE、无 batch 请求、无会话保持；<code>initialize</code> 不回传实例信息。
      带 <code>Origin</code> 的请求一律 403，只监听 127.0.0.1。</div>
  `, { aside: intStatusChip(cfg, 'mcp'), class: 'card-integration' })

  const logCard = card('运行日志排障（按报错编号查单条，不提供日志浏览）', `
    <p class="muted small">服务端报 500 时会给出 8 位编号（request_id）。这里只按编号查这一条，
      不做列表、导出或全文搜索；批量排查请用服务端 <code>GET /api/logs</code>。</p>
    <label class="field"><span>报错编号（8 位十六进制）</span>
      <div class="inline-row">
        <input id="int-log-rid" type="text" maxlength="8" autocomplete="off" spellcheck="false" value="${esc(intg.logQuery || '')}" placeholder="a1b2c3d4" />
        <button class="btn btn-sm btn-primary" data-action="int-find-log">查询</button>
      </div>
    </label>
    <div class="auth-alert" id="int-log-error" role="alert" hidden></div>
    <div id="int-log-result">${intLogResultHtml(intg)}</div>
  `, { class: 'card-integration' })

  return apiCard + mcpCard + logCard
}

/**
 * 令牌明文的一次性展示卡（复用建号一次性口令模式）。
 * 明文参数只在本次调用栈里存在：调用方不写 state / localStorage / log()。
 */
export function tokenCreatedHtml(result = {}, { rotated = false } = {}) {
  const token = String(result.token || '')
  const scopes = Array.isArray(result.scopes) ? result.scopes : []
  return `<div class="modal-head"><h2>${rotated ? '令牌已轮换' : '令牌已签发'}</h2>
    <button class="icon-btn" data-action="close-modal">✕</button></div>
  <div class="modal-body">
    <div class="callout caution"><strong>令牌只显示这一次，关闭后无法再次查看。</strong>
      请立即复制并妥善保存；丢失只能撤销后重新签发。</div>
    <label class="field"><span>访问令牌（一次性明文）</span>
      <div class="inline-row">
        <input id="int-token-value" type="text" value="${esc(token)}" readonly autocomplete="off" spellcheck="false" />
        <button class="btn btn-sm btn-primary" data-action="int-copy-token">复制</button>
      </div>
    </label>
    <p class="muted small">${rotated
      ? '旧令牌已立即失效（没有宽限期），请先替换已配置脚本与 MCP 客户端。'
      : '复制后请通过可信渠道保存，不要贴在工单或聊天记录里。'}</p>
    <p class="muted small">标识：${esc(result.tokenHint || '—')}　授权范围：${esc(scopes.join(' · ') || '—')}</p>
  </div>
  <div class="modal-foot">
    <button class="btn btn-primary" data-action="close-modal">我已保存，关闭</button>
  </div>`
}
