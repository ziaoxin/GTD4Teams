/**
 * 渲染冒烟测试（双域版）
 *
 * 目的：用真实形态的数据把个人空间与团队空间的**全部视图与表单**跑一遍，
 * 检查输出中的 undefined / NaN / [object Object] / 标签不闭合等模板缺陷，
 * 并捕获运行时异常（漏 import 的符号会在此暴露为 ReferenceError）。
 */
import fs from 'fs'
import path from 'path'
import { fileURLToPath } from 'url'

const here = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(here, '..')

/* 1. 把源码复制成 .mjs（Node 需要显式 ESM 扩展名），并剥掉 CDN 缓存版本号 */
for (const f of ['config.js', 'util.js', 'views.js']) {
  const src = fs.readFileSync(path.join(root, 'js', f), 'utf8')
    .replace(/from '\.\/([A-Za-z0-9_-]+)\.js(\?[^']*)?'/g, "from './$1.mjs'")
  fs.writeFileSync(path.join(here, f.replace(/\.js$/, '.mjs')), src, 'utf8')
}

const V = await import('./views.mjs')

/* ---------------- 夹具数据 ---------------- */

const pad = (n) => String(n).padStart(2, '0')
const ds = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
const now = new Date()
const iso = now.toISOString()
const day = (n) => ds(new Date(now.getTime() + n * 86400000))
const hours = (n) => new Date(now.getTime() + n * 3600000).toISOString()

const TEAM = 1
const UID_OWNER = 'uid-owner-0001'
const UID_MEMBER = 'uid-member-0002'
const UID_ORPHAN = 'uid-orphan-0003'

const data = {
  teams: [
    { id: TEAM, name: '基础设施运维组', mission: '保障核心系统 7×24 稳定运行', description: '6 人小组', code: 'OPS-4F2K', owner_id: UID_OWNER, archived: false, board_config: null },
    { id: 2, name: '应用支持组', mission: '业务系统二线支持', description: null, code: 'APP-9QX1', owner_id: UID_MEMBER, archived: false, board_config: { doing: 3 } },
  ],
  members: [
    { id: 11, team_id: TEAM, user_id: UID_OWNER, email: 'owner@example.com', display_name: '李沛', role: 'owner', title: '运维负责人', capacity: 6, status: 'active' },
    { id: 12, team_id: TEAM, user_id: UID_MEMBER, email: 'member@example.com', display_name: '王敏', role: 'member', title: '系统工程师', capacity: 5, status: 'active' },
    { id: 13, team_id: TEAM, user_id: UID_ORPHAN, email: 'zhang@example.com', display_name: '张涛', role: 'member', title: '网络工程师', capacity: 4, status: 'active' },
    { id: 14, team_id: TEAM, user_id: null, email: 'lin@example.com', display_name: null, role: 'member', title: null, capacity: 6, status: 'invited' },
    { id: 21, team_id: 2, user_id: UID_MEMBER, email: 'member@example.com', display_name: '王敏', role: 'owner', title: null, capacity: 6, status: 'active' },
  ],
  items: [
    // —— 团队工作项 ——
    { id: 101, team_id: TEAM, owner_id: UID_OWNER, title: '每日机房巡检', notes: '按清单执行', item_type: 'inspection', status: 'doing', priority: 'p3', swimlane: 'planned', assignee_id: UID_MEMBER, assignee_name: '王敏', project_id: null, area_id: 1, context_id: 1, sla_due_at: hours(3), due_date: day(0), focus_date: day(0), gtd_note: '先跑监控快照', recurrence: { mode: 'after', interval: 1, unit: 'day' }, progress: 40, flags: null, flagged: true, is_next: false, waiting_for: null, blocked_reason: null, created_at: iso, updated_at: iso, completed_at: null, kanban_order: 1, est_hours: 1, actual_hours: 0.5, tags: ['巡检'], last_update: '水位正常' },
    { id: 102, team_id: TEAM, owner_id: UID_OWNER, title: '核心交换机端口告警', notes: 'Gi1/0/24 CRC 错误激增', item_type: 'incident', status: 'doing', priority: 'p1', swimlane: 'unplanned', assignee_id: UID_OWNER, assignee_name: '李沛', project_id: null, area_id: 1, context_id: 2, sla_due_at: hours(-1), due_date: day(0), focus_date: day(0), gtd_note: null, recurrence: null, progress: 65, flagged: true, is_next: true, waiting_for: null, blocked_reason: null, created_at: iso, updated_at: iso, completed_at: null, kanban_order: 2, tags: ['网络'], last_update: '已更换光纤跳线' },
    { id: 103, team_id: TEAM, owner_id: UID_OWNER, title: '新同事账号与权限开通', notes: null, item_type: 'request', status: 'ready', priority: 'p4', swimlane: 'unplanned', assignee_id: null, assignee_name: null, project_id: null, area_id: 5, context_id: 5, sla_due_at: hours(20), due_date: day(2), focus_date: null, gtd_note: null, recurrence: null, progress: 0, flagged: false, is_next: false, waiting_for: null, blocked_reason: null, created_at: iso, updated_at: iso, completed_at: null, kanban_order: 3, tags: null },
    { id: 104, team_id: TEAM, owner_id: UID_OWNER, title: '反复出现的磁盘写满问题', notes: '本月第 3 次', item_type: 'problem', status: 'triage', priority: 'p2', swimlane: 'planned', assignee_id: UID_MEMBER, assignee_name: '王敏', project_id: null, area_id: 3, context_id: 4, sla_due_at: null, due_date: day(5), focus_date: null, gtd_note: '需定位日志轮转策略', recurrence: null, progress: 10, flagged: false, is_next: false, waiting_for: null, blocked_reason: null, created_at: iso, updated_at: iso, completed_at: null, kanban_order: 4, tags: ['根因'] },
    { id: 105, team_id: TEAM, owner_id: UID_OWNER, title: '备份系统版本升级', notes: '需变更窗口', item_type: 'change', status: 'blocked', priority: 'p2', swimlane: 'planned', assignee_id: UID_ORPHAN, assignee_name: '张涛', project_id: 201, area_id: 6, context_id: 3, sla_due_at: null, due_date: day(-2), focus_date: null, gtd_note: null, recurrence: null, progress: 30, flagged: false, is_next: false, waiting_for: '厂商支持', blocked_reason: '等待厂商提供兼容性说明', created_at: iso, updated_at: iso, completed_at: null, kanban_order: 5 },
    { id: 106, team_id: TEAM, owner_id: UID_OWNER, title: '监控指标采集改造', notes: null, item_type: 'project_task', status: 'ready', priority: 'p3', swimlane: 'planned', assignee_id: UID_MEMBER, assignee_name: '王敏', project_id: 201, area_id: 3, context_id: 4, sla_due_at: null, due_date: day(7), focus_date: day(1), gtd_note: '先接 Prometheus', recurrence: null, progress: 0, flagged: false, is_next: true, created_at: iso, updated_at: iso, completed_at: null, kanban_order: 6 },
    { id: 107, team_id: TEAM, owner_id: UID_OWNER, title: '月度安全基线核查', notes: null, item_type: 'routine', status: 'intake', priority: 'p3', swimlane: 'planned', assignee_id: null, assignee_name: null, project_id: null, area_id: 7, context_id: 1, sla_due_at: null, due_date: null, focus_date: null, recurrence: { mode: 'after', interval: 1, unit: 'month' }, progress: 0, flagged: false, is_next: false, created_at: iso, updated_at: iso, completed_at: null, kanban_order: 7 },
    { id: 108, team_id: TEAM, owner_id: UID_OWNER, title: '上周合同续签跟进', notes: null, item_type: 'request', status: 'waiting', priority: 'p4', swimlane: 'unplanned', assignee_id: UID_OWNER, assignee_name: '李沛', project_id: null, area_id: null, context_id: 5, due_date: day(-1), waiting_for: '采购部审批', progress: 50, flagged: false, is_next: false, created_at: iso, updated_at: iso, completed_at: null, kanban_order: 8 },
    { id: 109, team_id: TEAM, owner_id: UID_OWNER, title: '交换机固件升级', notes: null, item_type: 'change', status: 'done', priority: 'p2', swimlane: 'planned', assignee_id: UID_MEMBER, assignee_name: '王敏', project_id: null, area_id: 1, context_id: 3, sla_due_at: hours(-48), due_date: day(-3), progress: 100, flagged: false, created_at: iso, updated_at: iso, completed_at: hours(-50), kanban_order: 9 },
    { id: 110, team_id: TEAM, owner_id: UID_OWNER, title: '临时协助搬机柜', notes: null, item_type: 'routine', status: 'done', priority: 'p4', swimlane: 'unplanned', assignee_id: UID_ORPHAN, assignee_name: '张涛', project_id: null, area_id: 2, context_id: 2, sla_due_at: hours(-600), due_date: day(-20), progress: 100, created_at: iso, updated_at: iso, completed_at: hours(-601), kanban_order: 10 },
    { id: 111, team_id: TEAM, owner_id: UID_OWNER, title: '巡检异常项待复核', notes: null, item_type: 'inspection', status: 'review', priority: 'p3', swimlane: 'planned', assignee_id: UID_MEMBER, assignee_name: '王敏', project_id: null, area_id: 2, context_id: 1, due_date: day(0), progress: 90, flagged: false, created_at: iso, updated_at: iso, completed_at: null, kanban_order: 11 },
    { id: 112, team_id: TEAM, owner_id: UID_OWNER, title: '暂缓：运维大屏建设', notes: null, item_type: 'project_task', status: 'someday', priority: 'p4', swimlane: 'planned', assignee_id: null, area_id: 3, progress: 0, created_at: iso, updated_at: iso, kanban_order: 12 },
    // —— 个人事项（team_id 为空）——
    { id: 201, team_id: null, owner_id: UID_OWNER, title: '读《凤凰项目》第三章', notes: null, item_type: 'routine', status: 'intake', priority: 'p4', swimlane: 'planned', assignee_id: null, project_id: null, area_id: null, context_id: 4, progress: 0, flagged: false, is_next: false, created_at: iso, updated_at: iso, focus_date: null, gtd_note: null },
    { id: 202, team_id: null, owner_id: UID_OWNER, title: '整理年度运维总结素材', notes: null, item_type: 'routine', status: 'triage', priority: 'p3', swimlane: 'planned', context_id: 4, area_id: null, project_id: null, progress: 0, flagged: false, is_next: false, created_at: iso, updated_at: iso, gtd_note: '何时做：春节前' },
    { id: 203, team_id: null, owner_id: UID_OWNER, title: '研究 Ansible 自动化改造', notes: null, item_type: 'routine', status: 'ready', priority: 'p3', swimlane: 'planned', context_id: 4, area_id: null, project_id: null, due_date: day(0), focus_date: day(0), progress: 0, flagged: true, is_next: true, created_at: iso, updated_at: iso },
    { id: 204, team_id: null, owner_id: UID_OWNER, title: '等财务回复报销单', notes: null, item_type: 'routine', status: 'waiting', priority: 'p4', swimlane: 'planned', context_id: null, area_id: null, project_id: null, waiting_for: '财务-陈', due_date: day(-4), progress: 0, created_at: iso, updated_at: iso },
    { id: 205, team_id: null, owner_id: UID_OWNER, title: '学 Kubernetes 运维', notes: null, item_type: 'routine', status: 'someday', priority: 'p4', swimlane: 'planned', context_id: 4, area_id: null, project_id: null, progress: 0, created_at: iso, updated_at: iso },
    { id: 206, team_id: null, owner_id: UID_OWNER, title: '提交月度工时统计', notes: null, item_type: 'routine', status: 'done', priority: 'p3', swimlane: 'planned', context_id: 1, area_id: null, project_id: null, progress: 100, completed_at: hours(-20), created_at: iso, updated_at: iso },
    { id: 207, team_id: null, owner_id: UID_OWNER, title: '归档 2025 年运维记录', notes: null, item_type: 'routine', status: 'archived', priority: 'p4', swimlane: 'planned', progress: 100, completed_at: hours(-400), created_at: iso, updated_at: iso },
    { id: 208, team_id: null, owner_id: UID_MEMBER, title: '别人的个人事项（不应出现）', item_type: 'routine', status: 'ready', priority: 'p3', swimlane: 'planned', owner_id: UID_MEMBER, progress: 0, created_at: iso, updated_at: iso },
  ],
  projects: [
    { id: 201, team_id: TEAM, owner_id: UID_OWNER, name: '监控与告警平台升级', description: '替换旧 Zabbix，接入统一告警', status: 'active', kind: 'improve', lead_id: UID_MEMBER, lead_name: '王敏', area_id: 3, due_date: day(20), color: '#14867a', progress: 35, sort_order: 0, created_at: iso, updated_at: iso },
    { id: 202, team_id: TEAM, owner_id: UID_OWNER, name: '机房搬迁', description: null, status: 'waiting', kind: 'build', lead_id: UID_OWNER, lead_name: '李沛', area_id: 2, due_date: null, color: '#4f46e5', progress: 10, sort_order: 1, created_at: iso, updated_at: iso },
    { id: 203, team_id: TEAM, owner_id: UID_OWNER, name: '已完成项目', description: null, status: 'done', kind: 'improve', lead_id: null, area_id: null, due_date: day(-30), progress: 100, sort_order: 2, created_at: iso, updated_at: iso },
    { id: 204, team_id: TEAM, owner_id: UID_OWNER, name: '搁置项目', description: null, status: 'someday', kind: 'build', area_id: null, progress: 0, sort_order: 3, created_at: iso, updated_at: iso },
    { id: 221, team_id: 2, owner_id: UID_MEMBER, name: 'CRM 二线支持优化', status: 'active', kind: 'improve', area_id: null, progress: 20, sort_order: 0, created_at: iso, updated_at: iso },
  ],
  areas: [
    { id: 1, team_id: TEAM, owner_id: UID_OWNER, name: '网络与链路', criticality: 'high', description: '路由交换、专线、VPN、DNS', archived: false, sort_order: 0, created_at: iso, updated_at: iso },
    { id: 2, team_id: TEAM, owner_id: UID_OWNER, name: '服务器与虚拟化', criticality: 'high', description: '物理机、虚机、存储', archived: false, sort_order: 1, created_at: iso, updated_at: iso },
    { id: 3, team_id: TEAM, owner_id: UID_OWNER, name: '应用与中间件', criticality: 'normal', description: null, archived: false, sort_order: 2, created_at: iso, updated_at: iso },
    { id: 4, team_id: TEAM, owner_id: UID_OWNER, name: '已归档领域', criticality: 'normal', archived: true, sort_order: 9, created_at: iso, updated_at: iso },
  ],
  contexts: [
    { id: 1, team_id: TEAM, owner_id: UID_OWNER, name: '值守台', kind: 'mode', color: '#0f766e', sort_order: 0, created_at: iso, updated_at: iso },
    { id: 2, team_id: TEAM, owner_id: UID_OWNER, name: '现场/机房', kind: 'place', color: '#0369a1', sort_order: 1, created_at: iso, updated_at: iso },
    { id: 3, team_id: TEAM, owner_id: UID_OWNER, name: '变更窗口', kind: 'mode', color: '#b45309', sort_order: 2, created_at: iso, updated_at: iso },
    { id: 4, team_id: null, owner_id: UID_OWNER, name: '深度专注', kind: 'energy', color: '#4f46e5', sort_order: 3, created_at: iso, updated_at: iso },
    { id: 5, team_id: null, owner_id: UID_OWNER, name: '沟通协调', kind: 'tool', color: '#7c3aed', sort_order: 4, created_at: iso, updated_at: iso },
  ],
  resources: [
    { id: 301, team_id: TEAM, owner_id: UID_OWNER, kind: 'runbook', title: '核心交换机应急处置手册', category: '网络', url: 'https://example.com/rb', description: '含回滚步骤', tags: ['网络', '应急'], custodian_id: UID_MEMBER, custodian_name: '王敏', criticality: 'high', review_due: day(-5), meta: null, pinned: true, archived: false, created_at: iso, updated_at: iso },
    { id: 302, team_id: TEAM, owner_id: UID_OWNER, kind: 'tool', title: 'Zabbix 监控平台', category: '监控', url: 'https://zabbix.example.com', description: null, tags: null, custodian_id: UID_MEMBER, custodian_name: '王敏', criticality: 'high', review_due: day(3), meta: { renewal_date: day(30), license: 'GPL', vendor: '开源' }, pinned: true, archived: false, created_at: iso, updated_at: iso },
    { id: 303, team_id: TEAM, owner_id: UID_OWNER, kind: 'asset', title: '机房 A 列机柜 U12 交换机', category: '硬件', url: null, description: 'SN: CN12345', tags: ['资产'], custodian_id: UID_ORPHAN, custodian_name: '张涛', criticality: 'normal', review_due: null, meta: { renewal_date: day(10), warranty_end: day(200) }, pinned: false, archived: false, created_at: iso, updated_at: iso },
    { id: 304, team_id: TEAM, owner_id: UID_OWNER, kind: 'credential', title: '堡垒机管理账号位置', category: '安全', url: null, description: '存放于密码管理器分组 infra', tags: null, custodian_id: UID_OWNER, custodian_name: '李沛', criticality: 'high', review_due: null, meta: null, pinned: false, archived: false, created_at: iso, updated_at: iso },
    { id: 305, team_id: TEAM, owner_id: UID_OWNER, kind: 'doc', title: '网络拓扑图 v3', category: '网络', url: 'https://example.com/topo', description: null, tags: null, custodian_id: null, custodian_name: null, criticality: 'normal', review_due: day(0), meta: null, pinned: false, archived: false, created_at: iso, updated_at: iso },
    { id: 306, team_id: TEAM, owner_id: UID_OWNER, kind: 'link', title: '供应商工单入口', category: '外联', url: 'https://vendor.example.com', custodian_id: null, criticality: 'low', pinned: false, archived: false, created_at: iso, updated_at: iso },
    { id: 307, team_id: TEAM, owner_id: UID_OWNER, kind: 'runbook', title: '已归档手册', archived: true, pinned: false, criticality: 'normal', created_at: iso, updated_at: iso },
  ],
  reviews: [
    { id: 401, team_id: TEAM, owner_id: UID_OWNER, kind: 'weekly', period_key: '2026-W37', notes: '本周事件 3 起，均在 SLA 内关闭', stats: { health: 88 }, completed_at: iso, created_at: iso, updated_at: iso },
    { id: 402, team_id: null, owner_id: UID_OWNER, kind: 'daily', period_key: ds(now), notes: '收集箱已清空', stats: {}, completed_at: iso, created_at: iso, updated_at: iso },
    { id: 403, team_id: null, owner_id: UID_OWNER, kind: 'weekly', period_key: '2026-W36', notes: '下周重点是自动化', stats: {}, completed_at: iso, created_at: iso, updated_at: iso },
  ],
  onCall: (() => {
    const start = V.onCallMatrix({ onCall: [] }, TEAM, ds(now))
    return [
      { id: 501, team_id: TEAM, owner_id: UID_OWNER, shift_date: start[0].date, slot: 'day', primary_id: UID_MEMBER, primary_name: '王敏', backup_id: UID_ORPHAN, backup_name: '张涛', notes: '重点关注网络告警', handover_notes: null, status: 'planned', created_at: iso, updated_at: iso },
      { id: 502, team_id: TEAM, owner_id: UID_OWNER, shift_date: start[0].date, slot: 'night', primary_id: UID_OWNER, primary_name: '李沛', backup_id: null, backup_name: null, notes: null, handover_notes: '交班：未关闭告警 2 条', status: 'handed_over', created_at: iso, updated_at: iso },
      { id: 503, team_id: TEAM, owner_id: UID_OWNER, shift_date: start[2].date, slot: 'day', primary_id: UID_ORPHAN, primary_name: '张涛', backup_id: UID_MEMBER, backup_name: '王敏', status: 'planned', created_at: iso, updated_at: iso },
    ]
  })(),
  logs: [
    { id: 601, team_id: TEAM, item_id: 102, user_id: UID_OWNER, actor_email: 'owner@example.com', kind: 'progress', note: '已更换光纤跳线，观察中', progress: 65, created_at: iso },
    { id: 602, team_id: TEAM, item_id: 102, user_id: UID_MEMBER, actor_email: 'member@example.com', kind: 'comment', note: '已通知网络厂商', created_at: iso },
    { id: 603, team_id: TEAM, item_id: 101, user_id: UID_MEMBER, kind: 'handover', note: '夜间告警已清理', created_at: iso },
  ],
}

const UID = UID_OWNER

/* ---------------- 断言 ---------------- */

const base = {
  view: 'focus',
  space: 'personal',
  data,
  search: '',
  filters: {},
  weekAnchor: ds(now),
  activeTeamId: TEAM,
  // 真实 SDK 的 getSession() 只给 { id, isAnonymous, raw } —— 没有 email；
  // 展示用的身份由 app.js 调 auth.getUser() 后写入 state.identity。
  user: { id: UID, isAnonymous: false, raw: {} },
  identity: { id: UID, name: '李沛', email: 'owner@example.com' },
}

const balance = (html, tag) => {
  const open = (html.match(new RegExp('<' + tag + '[ >]', 'g')) || []).length
  const close = (html.match(new RegExp('</' + tag + '>', 'g')) || []).length
  return { open, close, ok: open === close }
}

let failures = 0
let passes = 0

const check = (name, fn) => {
  let html
  try {
    html = typeof fn === 'function' ? fn() : fn
  } catch (err) {
    console.log(`THROW ${name.padEnd(28)} -> ${err.constructor.name}: ${err.message}`)
    failures += 1
    return
  }
  if (typeof html !== 'string') {
    console.log(`BAD   ${name.padEnd(28)} -> 非字符串输出 (${typeof html})`)
    failures += 1
    return
  }
  const problems = []
  if (/undefined/.test(html)) problems.push('undefined')
  if (/NaN/.test(html)) problems.push('NaN')
  if (/\[object Object\]/.test(html)) problems.push('[object Object]')
  if (/\$\{/.test(html)) problems.push('未插值 ${')
  if (/&lt;div/.test(html) && /<div/.test(html) === false) problems.push('HTML 被转义')
  for (const tag of ['div', 'section', 'article', 'header']) {
    const b = balance(html, tag)
    if (!b.ok) problems.push(`${tag} ${b.open}/${b.close}`)
  }
  if (problems.length) {
    console.log(`BAD   ${name.padEnd(28)} -> ${problems.join(', ')}`)
    failures += 1
  } else {
    passes += 1
    console.log(`OK    ${name.padEnd(28)} ${String(html.length).padStart(6)} chars`)
  }
}

const lk = V.makeLookups(data)
const byId = (id) => data.items.find((i) => i.id === id)
const S = (over = {}) => ({ ...base, ...over })

/* 外壳 */
check('shell:spaceTabs', () => V.renderSpaceTabs(S()))
check('shell:spaceTabs(team)', () => V.renderSpaceTabs(S({ space: 'team' })))
check('shell:teamSelect', () => V.renderTeamSelect(S(), lk))
check('shell:teamSelect(no-team)', () => V.renderTeamSelect(S({ data: { ...data, teams: [] }, activeTeamId: null }), lk))
check('shell:sidebar(personal)', () => V.renderSidebar(S()))
check('shell:sidebar(team)', () => V.renderSidebar(S({ space: 'team' })))
check('shell:sidebar(team-no-active)', () => V.renderSidebar(S({ space: 'team', activeTeamId: null })))
check('shell:onboarding', () => V.renderOnboarding(S({ data: { teams: [], members: [], items: [], projects: [], areas: [], contexts: [], resources: [], reviews: [], onCall: [], logs: [] } })))

/* 个人空间视图 */
for (const v of ['inbox', 'clarify', 'focus', 'next', 'waiting', 'calendar', 'someday', 'assigned', 'myProjects', 'contexts', 'review', 'done']) {
  check(`personal:${v}`, () => V.renderView(S({ view: v, space: 'personal', search: '' })))
}

/* 团队空间视图 */
for (const v of ['overview', 'routine', 'desk', 'board', 'projects', 'oncall', 'resources', 'tools', 'reports', 'members', 'teamReview', 'teamSettings']) {
  check(`team:${v}`, () => V.renderView(S({ view: v, space: 'team' })))
}

/* 空数据兜底：每个视图在无数据时也不能崩 */
const EMPTY = { teams: [], members: [], items: [], projects: [], areas: [], contexts: [], resources: [], reviews: [], onCall: [], logs: [] }
check('empty:personal:focus', () => V.renderView(S({ view: 'focus', space: 'personal', data: EMPTY, user: { id: 'x', email: 'x@y.z' } })))
check('empty:team:overview', () => V.renderView(S({ view: 'overview', space: 'team', data: EMPTY, activeTeamId: null })))
check('empty:team:board', () => V.renderView(S({ view: 'board', space: 'team', data: EMPTY, activeTeamId: null })))
check('empty:team:reports', () => V.renderView(S({ view: 'reports', space: 'team', data: EMPTY, activeTeamId: null })))

/* 筛选分支 */
check('filter:next-context', () => V.renderView(S({ view: 'next', space: 'personal', filters: { context: 4 } })))
check('filter:routine-type', () => V.renderView(S({ view: 'routine', space: 'team', filters: { itemType: 'inspection' } })))
check('filter:desk-type', () => V.renderView(S({ view: 'desk', space: 'team', filters: { itemType: 'incident' } })))
check('filter:board-unplanned', () => V.renderView(S({ view: 'board', space: 'team', filters: { swimlane: 'unplanned' } })))
check('filter:projects-status', () => V.renderView(S({ view: 'projects', space: 'team', filters: { projectStatus: 'all' } })))
check('filter:resources-kind', () => V.renderView(S({ view: 'resources', space: 'team', filters: { resKind: 'tool' } })))
check('filter:tools-kind', () => V.renderView(S({ view: 'tools', space: 'team', filters: { toolKind: 'asset' } })))
check('filter:calendar-week', () => V.renderView(S({ view: 'calendar', space: 'personal', weekAnchor: day(7) })))

/* 抽屉 */
check('drawer:item-team', () => V.itemDrawerHtml(data.items.find((i) => i.id === 102), S({ space: 'team' }), lk))
check('drawer:item-personal', () => V.itemDrawerHtml(data.items.find((i) => i.id === 203), S(), lk))
check('drawer:item-minimal', () => V.itemDrawerHtml(data.items.find((i) => i.id === 112), S({ space: 'team' }), lk))
check('drawer:project', () => V.projectDrawerHtml(data.projects[0], S({ space: 'team' }), lk))
check('drawer:project-minimal', () => V.projectDrawerHtml(data.projects[2], S({ space: 'team' }), lk))

/* 表单 */
check('form:newItem-team', () => V.newItemFormHtml('team', 'incident', 'ready', S({ space: 'team' }), lk))
check('form:newItem-personal', () => V.newItemFormHtml('personal', 'routine', 'intake', S(), lk))
check('form:team', () => V.teamFormHtml())
check('form:joinTeam', () => V.joinTeamFormHtml())
check('form:project-edit', () => V.projectFormHtml(data.projects[0], S({ space: 'team' }), lk))
check('form:project-new', () => V.projectFormHtml(null, S({ space: 'team' }), lk))
check('form:context-edit', () => V.contextFormHtml(data.contexts[0], S()))
check('form:context-new', () => V.contextFormHtml(null, S()))
check('form:resource-edit', () => V.resourceFormHtml(data.resources[1], S({ space: 'team' }), lk))
check('form:resource-new', () => V.resourceFormHtml(null, S({ space: 'team' }), lk))
check('form:member-edit', () => V.memberFormHtml(data.members[0], S({ space: 'team' }), lk))
check('form:member-edit-plain', () => V.memberFormHtml(data.members[2], S({ space: 'team' }), lk))
check('form:member-no-real-user', () => V.memberFormHtml(data.members[3], S({ space: 'team' }), lk))
check('form:invite-panel', () => V.invitePanelHtml(S({ space: 'team' }), lk))
check('form:invite-panel-no-team', () => V.invitePanelHtml(S({ space: 'team', activeTeamId: null, data: { ...data, teams: [] } }), V.makeLookups({ ...data, teams: [] })))
check('form:onCall-edit', () => V.onCallFormHtml(data.onCall[0], S({ space: 'team' }), lk))
check('form:onCall-new', () => V.onCallFormHtml(null, S({ space: 'team' }), lk))

/* 账号体系变更（CR-2026-001）：强制改密页 + 建号模态 + 一次性口令 */
check('gate:password', () => V.passwordGateHtml({ email: 'bob@example.com', version: 'v1' }))
check('gate:password-alert', () => V.passwordGateHtml({ email: 'bob@example.com', alert: '原密码不正确。', version: 'v1' }))
check('gate:password-no-email', () => V.passwordGateHtml({}))
check('form:account-member', () => V.accountFormHtml(S({ space: 'team' }), lk, { mode: 'member' }))
check('form:account-admin', () => V.accountFormHtml(S({ space: 'team' }), lk, { mode: 'admin' }))
check('form:account-admin-no-team', () => V.accountFormHtml(S({ space: 'team', activeTeamId: null }), lk, { mode: 'admin' }))
check('form:account-created', () => V.accountCreatedHtml({
  user: { id: 'u_9f3a1c7d2e4b8a60', email: 'bob@example.com', name: 'Bob', isSystemAdmin: false, mustChangePassword: true },
  teamId: TEAM, role: 'member', password: 'Xk7pQ2mRt9vWb4Ns',
}))
check('form:account-created-no-team', () => V.accountCreatedHtml({
  user: { id: 'u_1', email: 'bob@example.com', name: 'Bob' }, teamId: null, role: null, password: 'Xk7pQ2mRt9vWb4Ns',
}))

/* 系统管理（实例级）：HTTPS 证书卡片 */
const CERT_NONE = { configured: false, source: 'none', pendingApply: false }
const CERT_FULL = {
  configured: true, source: 'uploaded', subject: 'CN=localhost', issuer: "CN=Let's Encrypt R3",
  sans: ['DNS:localhost', 'IP Address:127.0.0.1'], notBefore: iso, notAfter: hours(24 * 825),
  daysRemaining: 825, fingerprintSha256: 'AB:CD:EF:01:23:45:67:89', selfSigned: false,
  uploadedAt: iso, uploadedBy: 'admin@nnit.com', pendingApply: false,
}
const SYS = (over = {}) => S({ view: 'system', user: { id: UID_OWNER, isSystemAdmin: true, mustChangePassword: false }, ...over })
const certState = (data, over = {}) => ({ loaded: true, loading: false, error: '', applying: false, data, ...over })

check('system:no-permission', () => V.renderView(S({ view: 'system' })))
check('system:cert-loading', () => V.renderView(SYS({ cert: { loaded: false, loading: true, error: '', applying: false, data: null } })))
check('system:cert-error', () => V.renderView(SYS({ cert: { loaded: false, loading: false, error: '只有系统管理员可以创建账号', applying: false, data: null } })))
check('system:cert-none', () => V.renderView(SYS({ cert: certState(CERT_NONE) })))
check('system:cert-ok', () => V.renderView(SYS({ cert: certState(CERT_FULL) })))
check('system:cert-expiring-warn', () => V.renderView(SYS({ cert: certState({ ...CERT_FULL, daysRemaining: 21 }) })))
check('system:cert-expiring-danger', () => V.renderView(SYS({ cert: certState({ ...CERT_FULL, daysRemaining: 3 }) })))
check('system:cert-selfsigned', () => V.renderView(SYS({ cert: certState({ ...CERT_FULL, source: 'selfsigned', selfSigned: true }) })))
check('system:cert-pending', () => V.renderView(SYS({ cert: certState({ ...CERT_FULL, pendingApply: true }) })))
check('system:cert-applying', () => V.renderView(SYS({ cert: certState(CERT_FULL, { applying: true }) })))
check('system:cert-null-fields', () => V.renderView(SYS({
  cert: certState({
    configured: true, source: 'uploaded', subject: null, issuer: null, sans: null,
    notBefore: null, notAfter: null, daysRemaining: null, fingerprintSha256: null,
    selfSigned: null, uploadedAt: null, uploadedBy: null, pendingApply: false,
  }),
})))

/* 组件与面板 */
check('panel:user', () => V.userPanelHtml({ name: '李沛', email: 'owner@example.com', teamName: '基础设施运维组', roleLabel: '所有者' }, '20260916b'))
check('panel:user-bare', () => V.userPanelHtml({ name: '当前账号' }, '20260916b'))
// 系统管理员即便不属于任何团队，也必须有一个"建团队管理员"的入口（引导流程第 4 步）
check('panel:user-system-admin', () => V.userPanelHtml({ name: 'root', email: 'root@example.com', isSystemAdmin: true }, 'v'))
check('cmp:timeline', () => V.timeline(data.logs, lk))
check('cmp:timeline-empty', () => V.timeline([], lk))
check('cmp:itemRow', () => V.itemRow(byId(101), lk, { scope: 'team', showAssignee: true, showTeam: true }))
check('cmp:itemRow-personal', () => V.itemRow(byId(203), lk, { scope: 'personal' }))
check('cmp:itemRow-done', () => V.itemRow(byId(109), lk, { scope: 'team' }))
check('cmp:itemCard', () => V.itemCard(byId(102), lk))
check('cmp:itemCard-sla-risk', () => V.itemCard(byId(103), lk))

/* 选择器一致性：个人空间不得出现他人事项，团队分配项与个人项不得混淆 */
try {
  const mine = V.myItems(data, UID)
  const personal = V.personalItems(data, UID)
  const assigned = V.assignedToMe(data, UID)
  const expectAssigned = data.items.filter((i) => i.team_id != null && i.assignee_id === UID)
  const expectPersonal = data.items.filter((i) => i.team_id == null && i.owner_id === UID)
  const problems = []
  if (mine.some((i) => i.id === 208)) problems.push('他人个人事项泄漏进我的清单')
  if (personal.some((i) => i.team_id != null)) problems.push('personalItems 混入团队项')
  if (assigned.some((i) => i.team_id == null)) problems.push('assignedToMe 混入个人项')
  if (personal.length !== expectPersonal.length) problems.push(`personalItems=${personal.length} 期望 ${expectPersonal.length}`)
  if (assigned.length !== expectAssigned.length) problems.push(`assignedToMe=${assigned.length} 期望 ${expectAssigned.length}`)
  if (mine.length !== personal.length + assigned.length) problems.push('myItems 不等于 personal + assigned')
  if (V.teamItems(data, TEAM).some((i) => i.team_id !== TEAM)) problems.push('teamItems 跨团队泄漏')
  if (V.teamItems(data, TEAM).some((i) => i.team_id == null)) problems.push('teamItems 混入个人项')
  if (problems.length) {
    console.log(`BAD   ${'selectors:isolation'.padEnd(28)} -> ${problems.join(', ')}`)
    failures += 1
  } else {
    passes += 1
    console.log(`OK    ${'selectors:isolation'.padEnd(28)} 个人=${personal.length} 分配=${assigned.length} 合并=${mine.length} 团队=${V.teamItems(data, TEAM).length}`)
  }
} catch (err) {
  console.log(`THROW selectors:isolation -> ${err.message}`)
  failures += 1
}

/* 登录者身份：本轮缺陷（身份显示退化为字面量"已登录"）的回归防线 */
try {
  const U = await import('./util.mjs')
  const problems = []
  const eq = (label, got, want) => {
    if (String(got) !== String(want)) problems.push(`${label}=${JSON.stringify(got)} 期望 ${JSON.stringify(want)}`)
  }

  // 只有会话（真实形状：无 email）时，不得凭空编造身份
  const bare = U.describeIdentity({ id: 'u1', isAnonymous: false, raw: {} }, null)
  eq('裸会话.id', bare.id, 'u1')
  eq('裸会话.email', bare.email, '')
  eq('裸会话.name', bare.name, '')
  if (/已登录/.test(JSON.stringify(bare))) problems.push('裸会话仍产出占位文案')

  // 有 auth.getUser() 资料时，邮箱与用户名必须拿到
  const full = U.describeIdentity({ id: 'u1', raw: {} }, { id: 'u1', email: 'lpnl@nnit.com', raw: { name: 'lpnl' } })
  eq('资料.email', full.email, 'lpnl@nnit.com')
  eq('资料.name', full.name, 'lpnl')

  // 兼容探测：身份字段可能藏在 raw.user_metadata 下
  const meta = U.describeIdentity({ id: 'u1', raw: { user_metadata: { email: 'x@y.com', name: 'X' } } }, null)
  eq('元数据.email', meta.email, 'x@y.com')
  eq('元数据.name', meta.name, 'X')

  // 历史脏数据必须被判为"缺失"，否则会一直被当身份用
  if (!U.isPlaceholderIdentity('已登录')) problems.push('isPlaceholderIdentity 未识别"已登录"')
  if (U.isPlaceholderIdentity('lpnl@nnit.com')) problems.push('isPlaceholderIdentity 误伤正常邮箱')
  eq('脏数据.email', U.describeIdentity({ id: 'u1', raw: { email: '已登录' } }, { email: '已登录' }).email, '')

  // 左下角：必须同时出现登录用户名与团队名称
  const sidebarTeam = V.renderSidebar(S({ space: 'team' }))
  if (!sidebarTeam.includes('李沛')) problems.push('侧栏未显示登录用户名')
  if (!sidebarTeam.includes('基础设施运维组')) problems.push('侧栏未显示团队名称')
  const sidebarBare = V.renderSidebar(S({ space: 'team', identity: {}, user: { id: 'u9', isAnonymous: false, raw: {} } }))
  if (/已登录/.test(sidebarBare)) problems.push('侧栏在无身份时仍泄漏占位文案"已登录"')

  // 账号面板：用户名 + 邮箱 + 团队 + 角色
  const panel = V.userPanelHtml({ name: '李沛', email: 'owner@example.com', teamName: '基础设施运维组', roleLabel: '所有者' }, 'v')
  for (const t of ['李沛', 'owner@example.com', '基础设施运维组', '所有者']) {
    if (!panel.includes(t)) problems.push(`账号面板缺少 ${t}`)
  }

  if (problems.length) {
    console.log(`BAD   ${'identity:display'.padEnd(28)} -> ${problems.join(', ')}`)
    failures += 1
  } else {
    passes += 1
    console.log(`OK    ${'identity:display'.padEnd(28)} 侧栏=${V.identityOf(S({ space: 'team' })).name} · 团队名已就位`)
  }
} catch (err) {
  console.log(`THROW identity:display -> ${err.message}`)
  failures += 1
}

/* 资源链接协议白名单：只放行 http / https / mailto，其余在渲染层降级为不可点 */
try {
  const U = await import('./util.mjs')
  const problems = []
  const eq = (label, got, want) => {
    if (String(got) !== String(want)) problems.push(`${label}=${JSON.stringify(got)} 期望 ${JSON.stringify(want)}`)
  }

  // 工具函数层
  eq('safeUrl(javascript)', U.safeUrl('javascript:alert(1)'), '#')
  eq('safeUrl(data)', U.safeUrl('data:text/html,x'), '#')
  eq('safeUrl(大小写+前后空格)', U.safeUrl('  JaVaScRiPt:alert(1)'), '#')
  eq('safeUrl(vbscript)', U.safeUrl('vbscript:msgbox(1)'), '#')
  eq('safeUrl(空)', U.safeUrl(''), '#')
  eq('safeUrl(null)', U.safeUrl(null), '#')
  eq('safeUrl(无协议)', U.safeUrl('example.com/rb'), '#')
  eq('safeUrl(换行绕过)', U.safeUrl('java\nscript:alert(1)'), '#')
  eq('safeUrl(https)', U.safeUrl('https://example.com'), 'https://example.com')
  eq('safeUrl(大写 HTTPS)', U.safeUrl('  HTTPS://Example.com  '), 'HTTPS://Example.com')
  eq('safeUrl(mailto)', U.safeUrl('mailto:ops@example.com'), 'mailto:ops@example.com')
  if (typeof U.urlSafety !== 'function') problems.push('缺少 urlSafety()：渲染层无法判断"是否被降级"')
  else {
    const bad = U.urlSafety('javascript:alert(1)')
    if (bad.safe !== false || bad.href !== '#') problems.push(`urlSafety(javascript)=${JSON.stringify(bad)}`)
    const good = U.urlSafety('https://example.com')
    if (good.safe !== true || good.href !== 'https://example.com') problems.push(`urlSafety(https)=${JSON.stringify(good)}`)
  }

  // 渲染层：三种恶意 schema 都不得出现在 href 里
  const dirty = {
    ...data,
    resources: [
      { id: 901, team_id: TEAM, owner_id: UID_OWNER, kind: 'link', title: 'XSS-js', url: 'javascript:alert(1)', archived: false, pinned: false, criticality: 'normal' },
      { id: 902, team_id: TEAM, owner_id: UID_OWNER, kind: 'link', title: 'XSS-data', url: 'data:text/html,x', archived: false, pinned: false, criticality: 'normal' },
      { id: 903, team_id: TEAM, owner_id: UID_OWNER, kind: 'link', title: 'XSS-混写', url: '  JaVaScRiPt:alert(1)', archived: false, pinned: false, criticality: 'normal' },
      { id: 904, team_id: TEAM, owner_id: UID_OWNER, kind: 'link', title: '正常链接', url: 'https://example.com/ok', archived: false, pinned: false, criticality: 'normal' },
    ],
  }
  const html = V.renderView(S({ space: 'team', view: 'resources', data: dirty }))
  if (/href="javascript:/i.test(html)) problems.push('渲染结果里出现了 href="javascript:')
  if (/href="data:/i.test(html)) problems.push('渲染结果里出现了 href="data:')
  if (/href="vbscript:/i.test(html)) problems.push('渲染结果里出现了 href="vbscript:')
  if (!/href="https:\/\/example\.com\/ok"/.test(html)) problems.push('合法 https 链接被误伤')
  if (/javascript:alert/i.test(html)) problems.push('javascript: 仍然出现在 HTML 里（可能落在 title/aria 属性中）')
  // 被降级的链接不得可点、不得新开窗口：只有合法链接能带 target="_blank"
  const blank = (html.match(/target="_blank"/g) || []).length
  if (blank !== 1) problems.push(`target="_blank" 出现 ${blank} 次，期望 1 次（仅合法链接）`)
  const degraded = (html.match(/aria-disabled="true"/g) || []).length
  if (degraded !== 3) problems.push(`被降级标记 aria-disabled 出现 ${degraded} 次，期望 3 次`)

  if (problems.length) {
    console.log(`BAD   ${'render-safe-url'.padEnd(28)} -> ${problems.join(', ')}`)
    failures += 1
  } else {
    passes += 1
    console.log(`OK    ${'render-safe-url'.padEnd(28)} http/https/mailto 放行，其余降级为 #`)
  }
} catch (err) {
  console.log(`THROW render-safe-url -> ${err.message}`)
  failures += 1
}

/* 账号体系变更（CR-2026-001）：页面结构与安全约束 */
try {
  const problems = []

  // --- 强制改密页 ---
  const gate = V.passwordGateHtml({ email: 'bob@example.com', version: 'v1' })
  const autoOf = (id) => (gate.match(new RegExp(`id="${id}"[^>]*`)) || [''])[0]
  if (!/id="gate-old"[^>]*type="password"/.test(gate)) problems.push('gate-old 不是 password 输入框')
  if (!/autocomplete="current-password"/.test(autoOf('gate-old'))) problems.push('gate-old 缺少 autocomplete=current-password')
  if (!/autocomplete="new-password"/.test(autoOf('gate-new'))) problems.push('gate-new 缺少 autocomplete=new-password')
  if (!/autocomplete="new-password"/.test(autoOf('gate-confirm'))) problems.push('gate-confirm 缺少 autocomplete=new-password')
  if (!/id="gate-new"[^>]*minlength="8"/.test(gate)) problems.push('新密码缺少 minlength=8（应与 security.py 对齐）')
  if (!/id="gate-new"[^>]*maxlength="200"/.test(gate)) problems.push('新密码缺少 maxlength=200')
  if (!/id="gate-confirm"/.test(gate)) problems.push('缺少确认新密码字段（两次输入必须一致性校验）')
  if (!/首次登录/.test(gate) || !/重置/.test(gate)) problems.push('未说明"为什么被拦在这里"（首次登录 / 口令被重置）')
  if (!/bob@example\.com/.test(gate)) problems.push('未显示当前账号邮箱（用户要知道自己在改哪个账号）')
  if (/注册|signup/i.test(gate)) problems.push('强制改密页出现了注册相关文案（注册已下线）')
  if (/type="submit"/.test(gate) === false) problems.push('缺少提交按钮')

  // --- 建号表单：团队管理员只能建成员 ---
  const memberForm = V.accountFormHtml(S({ space: 'team' }), lk, { mode: 'member' })
  if (/id="a-role"/.test(memberForm)) problems.push('团队管理员入口不应渲染角色选择器（只能创建成员）')
  if (!/readonly/.test(memberForm)) problems.push('团队管理员入口应把团队锁死（readonly）')
  if (!/data-mode="member"/.test(memberForm)) problems.push('建号按钮缺少 data-mode="member"')
  if (!/id="a-email"/.test(memberForm) || !/id="a-name"/.test(memberForm)) problems.push('建号表单缺邮箱/显示名字段')
  if (/type="password"/.test(memberForm)) problems.push('建号表单不应有口令输入框（初始口令由服务端生成）')
  if (!/基础设施运维组/.test(memberForm)) problems.push('未显示锁定的团队名称')

  // --- 建号表单：系统管理员只能建 admin（`06` v1.3 D-1） ---
  const adminForm = V.accountFormHtml(S({ space: 'team' }), lk, { mode: 'admin' })
  if (!/<select id="a-team"/.test(adminForm)) problems.push('系统管理员入口应可选团队')
  if (/id="a-role"/.test(adminForm)) problems.push('系统管理员入口不应有角色选择器（只能建 admin；role 与 teamId 同现同缺）')
  if (/value="member"/.test(adminForm) || /成员（承接任务/.test(adminForm)) {
    problems.push('系统管理员入口不得出现「成员」选项（产品裁决 D-1，服务端会 400）')
  }
  if (!/角色由/.test(adminForm)) problems.push('系统管理员入口应说明"角色由是否选择团队决定"')
  if (/value="owner"/.test(adminForm) || />\s*所有者\s*</.test(adminForm)) problems.push('角色不得提供 owner（所有权只能由本人建团队产生）')
  if (!/data-mode="admin"/.test(adminForm)) problems.push('建号按钮缺少 data-mode="admin"')
  const adminNoTeam = V.accountFormHtml(S({ space: 'team', activeTeamId: null }), lk, { mode: 'admin' })
  if (/selected[^>]*>基础设施运维组/.test(adminNoTeam)) problems.push('无当前团队时不应预选某个团队')

  // --- 一次性口令展示 ---
  const created = V.accountCreatedHtml({
    user: { id: 'u_9f3a1c', email: 'bob@example.com', name: 'Bob', isSystemAdmin: false, mustChangePassword: true },
    teamId: TEAM, role: 'member', password: 'Xk7pQ2mRt9vWb4Ns',
  })
  const hits = (created.match(/Xk7pQ2mRt9vWb4Ns/g) || []).length
  if (hits !== 1) problems.push(`一次性口令在展示页出现 ${hits} 次，期望恰好 1 次`)
  if (!/只显示一次/.test(created)) problems.push('未强调"此密码只显示一次"')
  if (!/关闭后无法再次查看/.test(created)) problems.push('未说明"关闭后无法再次查看"')
  if (!/data-action="copy-temp-password"/.test(created)) problems.push('缺少复制按钮（data-action=copy-temp-password）')
  if (!/bob@example\.com/.test(created)) problems.push('未展示账号邮箱，管理员无法核对要转交谁')
  if (!/必须/.test(created)) problems.push('未说明对方首次登录必须先改密')

  // --- 入口可见性：只有系统管理员能看到"创建团队管理员" ---
  const panelAdmin = V.userPanelHtml({ name: 'root', email: 'root@example.com', isSystemAdmin: true }, 'v')
  const panelNormal = V.userPanelHtml({ name: '李沛', email: 'owner@example.com' }, 'v')
  if (!/data-action="new-admin-account"/.test(panelAdmin)) problems.push('系统管理员的账号面板缺少"创建团队管理员"入口')
  if (/data-action="new-admin-account"/.test(panelNormal)) problems.push('普通账号的面板泄漏了系统管理员入口')

  if (problems.length) {
    console.log(`BAD   ${'account:ui-constraints'.padEnd(28)} -> ${problems.join(', ')}`)
    failures += 1
  } else {
    passes += 1
    console.log(`OK    ${'account:ui-constraints'.padEnd(28)} 强制改密页 3 字段 · 建号按身份收窄 · 口令仅一次`)
  }
} catch (err) {
  console.log(`THROW account:ui-constraints -> ${err.message}`)
  failures += 1
}

/* 系统管理：证书卡片的显示规则（阈值/四态/转义） */
try {
  const problems = []
  const sys = (data, over = {}) => V.renderView(SYS({ cert: certState(data, over) }))

  const none = sys(CERT_NONE)
  if (!/还没有配置证书/.test(none)) problems.push('未配置时应给出空状态')
  if (!/data-action="upload-cert"/.test(none)) problems.push('未配置时应引导上传（有上传按钮）')
  if (/data-action="delete-cert"/.test(none)) problems.push('未配置时不应出现「移除证书」')

  const ok = sys(CERT_FULL)
  for (const t of ['CN=localhost', "CN=Let&#39;s Encrypt R3", 'DNS:localhost', 'IP Address:127.0.0.1',
    '825', 'AB:CD:EF:01:23:45:67:89', 'admin@nnit.com', '已上传']) {
    if (!ok.includes(t)) problems.push(`已配置视图缺少 ${t}`)
  }
  if (!/class="cert-item-value cert-fp"/.test(ok)) problems.push('指纹没有用等宽字体样式（cert-fp）')
  if (/callout warn/.test(ok) || /callout danger/.test(ok)) problems.push('825 天时不应出现到期告警')
  if (/自签名证书，浏览器会显示安全警告/.test(ok)) problems.push('非自签名证书不应出现自签名提示')
  if (!/data-action="delete-cert"/.test(ok)) problems.push('已配置时应提供「移除证书」')

  // 阈值：≤30 黄、≤7 红
  const warn = sys({ ...CERT_FULL, daysRemaining: 21 })
  if (!/callout warn/.test(warn) || !/21 天到期/.test(warn)) problems.push('21 天时应是黄色到期提醒')
  if (/callout danger/.test(warn)) problems.push('21 天时不应是红色告警')
  const danger = sys({ ...CERT_FULL, daysRemaining: 3 })
  if (!/callout danger/.test(danger) || !/只剩 3 天/.test(danger)) problems.push('3 天时应是红色告警')
  if (/callout warn/.test(danger)) problems.push('3 天时不应同时出现黄色提醒')

  // 自签名 / 待生效 / 应用中
  if (!/自签名证书，浏览器会显示安全警告/.test(sys({ ...CERT_FULL, source: 'selfsigned', selfSigned: true }))) {
    problems.push('selfSigned=true 时应提示自签名证书会触发浏览器警告')
  }
  if (!/正在应用/.test(sys({ ...CERT_FULL, pendingApply: true }))) problems.push('pendingApply=true 时应显示"正在应用"')
  if (!/正在应用新证书/.test(sys(CERT_FULL, { applying: true }))) problems.push('轮询期间应显示"正在应用新证书"')

  // 字段为 null：不得渲染 undefined / NaN（check() 已覆盖，这里再断言占位符）
  const nulls = sys({
    configured: true, source: 'uploaded', subject: null, issuer: null, sans: null,
    notBefore: null, notAfter: null, daysRemaining: null, fingerprintSha256: null,
    selfSigned: null, uploadedAt: null, uploadedBy: null, pendingApply: false,
  })
  if (/undefined|NaN|null/.test(nulls)) problems.push('字段为 null 时泄漏了 undefined/NaN/null')
  if (!/—/.test(nulls)) problems.push('字段为 null 时应显示破折号占位')

  // 转义：证书主题来自外部文件，必须当纯文本
  const dirty = sys({ ...CERT_FULL, subject: 'CN=<img src=x onerror=alert(1)>', uploadedBy: '"><script>alert(1)</script>' })
  if (/<img src=x/.test(dirty) || /<script>alert\(1\)/.test(dirty)) problems.push('证书字段未转义（XSS 风险）')

  // 非系统管理员：给权限提示，且不出现任何证书操作入口
  const denied = V.renderView(S({ view: 'system' }))
  if (!/只有系统管理员可以访问本页/.test(denied)) problems.push('非系统管理员应看到权限提示（不是空白页）')
  for (const act of ['load-cert', 'upload-cert', 'delete-cert', 'cert-file-picked']) {
    if (denied.includes(`data-action="${act}"`)) problems.push(`非系统管理员页面出现了 ${act} 入口`)
  }
  if (/type="file"/.test(denied)) problems.push('非系统管理员页面出现了文件选择框')

  // 侧栏入口：只对系统管理员出现（实例级入口不该出现在普通账号的导航里）
  if (!/data-view="system"/.test(V.renderSidebar(SYS()))) problems.push('系统管理员的侧栏缺少「系统管理」入口')
  if (/data-view="system"/.test(V.renderSidebar(S({ space: 'personal' })))) {
    problems.push('非系统管理员的侧栏出现了「系统管理」入口')
  }

  // 上传区：两个文件选择 + 粘贴备选 + 真不出现口令字段
  const upload = ok
  if (!/id="cert-crt-file"[^>]*accept="\.crt,\.pem,\.cer"/.test(upload)) problems.push('证书文件框的 accept 不对')
  if (!/id="cert-key-file"[^>]*accept="\.key,\.pem"/.test(upload)) problems.push('私钥文件框的 accept 不对')
  if (!/data-action="toggle-cert-paste"/.test(upload)) problems.push('缺少「直接粘贴 PEM」切换')
  if (!/id="cert-crt-pem"/.test(upload) || !/id="cert-key-pem"/.test(upload)) problems.push('缺少粘贴用的 textarea')
  if (/type="password"/.test(upload)) problems.push('证书页不应出现 password 输入框（PEM 是文本，不是口令）')

  if (problems.length) {
    console.log(`BAD   ${'system:cert-constraints'.padEnd(28)} -> ${problems.join(', ')}`)
    failures += 1
  } else {
    passes += 1
    console.log(`OK    ${'system:cert-constraints'.padEnd(28)} 四态 · 30/7 天阈值 · 空值占位 · 转义 · 权限收窄`)
  }
} catch (err) {
  console.log(`THROW system:cert-constraints -> ${err.message}`)
  failures += 1
}

/* 指标计算 */
check('metrics:team', () => JSON.stringify(V.computeTeamMetrics(data, TEAM, lk)))
check('metrics:personal', () => JSON.stringify(V.computePersonalMetrics(data, UID)))
check('metrics:onCallMatrix', () => JSON.stringify(V.onCallMatrix(data, TEAM, ds(now))))

console.log(failures ? `\n>>> FAILURES: ${failures} (pass ${passes})` : `\n>>> ALL ${passes} CHECKS CLEAN`)
process.exit(failures ? 1 : 0)
