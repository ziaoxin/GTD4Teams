/**
 * 构建与部署说明。
 *
 * 本版本**不再有任何云平台配置**：没有 endpoint、没有 publishableKey，
 * 也不再从 CDN 加载云服务 SDK。前端只访问同源的 `/api/*`，
 * 背后的服务端是仓库里的 `server.py`（自带账号体系 + SQLite 存储）。
 * 因此同一份代码拷到任何装有 Python 3 的机器上都能直接跑。
 */

/* ============================================================
 * 方法论底座
 * 团队层：ITIL 轻量化（事件/请求/变更/问题）+ Kanban（显式流程、WIP 限制、泳道）
 * 个人层：GTD 五步（收集 → 厘清 → 组织 → 执行 → 复盘）
 * 两层共用同一张 work_items 与同一套状态机，靠 team_id 是否为空区分作用域。
 * ============================================================ */

/**
 * 工作项类型 —— 取自 ITIL 轻量化票种，并补入运维团队最重的一类：日常工作。
 * lane 表示默认泳道：planned（计划内）/ unplanned（中断型，需抢占 WIP）
 */
export const ITEM_TYPES = {
  routine: { label: '日常工作', short: '日常', color: '#0f766e', soft: '#e6f4f1', lane: 'planned', icon: '🔧' },
  inspection: { label: '周期巡检', short: '巡检', color: '#0369a1', soft: '#e5f0fb', lane: 'planned', icon: '🧭' },
  request: { label: '服务请求', short: '请求', color: '#4f46e5', soft: '#ecebfd', lane: 'unplanned', icon: '📨' },
  incident: { label: '事件故障', short: '事件', color: '#b91c1c', soft: '#fdeaea', lane: 'unplanned', icon: '🚨' },
  problem: { label: '问题根因', short: '问题', color: '#7c2d12', soft: '#fbeee7', lane: 'planned', icon: '🔍' },
  change: { label: '变更发布', short: '变更', color: '#b45309', soft: '#fdf3e3', lane: 'planned', icon: '⚙️' },
  project_task: { label: '项目任务', short: '项目', color: '#15803d', soft: '#e8f5ec', lane: 'planned', icon: '🚀' },
}

/**
 * 统一状态机。
 * 团队视角 = Kanban 流水列（受理 → 分类 → 待办 → 处理中 → 待验证 → 受阻 → 完成）
 * 个人视角 = GTD 清单（收集箱 → 厘清 → 下一步行动 → 执行 → 等待 → 将来/也许）
 * 两套只是同一枚举的不同标注，见 STATUS_LABEL_PERSONAL。
 */
export const ITEM_STATUS = {
  intake: { label: '待受理', personal: '收集箱', color: '#64748b', soft: '#f1f5f9' },
  triage: { label: '待分类', personal: '待厘清', color: '#7c3aed', soft: '#f2ecfd' },
  ready: { label: '待办', personal: '下一步行动', color: '#0f766e', soft: '#e6f4f1' },
  doing: { label: '处理中', personal: '执行中', color: '#0369a1', soft: '#e5f0fb' },
  review: { label: '待验证', personal: '待复核', color: '#0891b2', soft: '#e4f6fa' },
  blocked: { label: '受阻', personal: '受阻', color: '#b91c1c', soft: '#fdeaea' },
  waiting: { label: '等待外部', personal: '等待他人', color: '#b45309', soft: '#fdf3e3' },
  someday: { label: '暂缓', personal: '将来/也许', color: '#6d28d9', soft: '#f2ecfd' },
  done: { label: '已完成', personal: '已完成', color: '#15803d', soft: '#e8f5ec' },
  archived: { label: '已归档', personal: '已归档', color: '#737373', soft: '#f4f4f5' },
}

/** 看板列（团队视角的显式流程），顺序即流转方向 */
export const BOARD_COLUMNS = ['intake', 'triage', 'ready', 'doing', 'review', 'blocked', 'done']

/** 默认 WIP 上限：照搬运维 Kanban 实践，分类与处理中列最紧，防止并行过载 */
export const DEFAULT_WIP = {
  intake: 99,
  triage: 2,
  ready: 8,
  doing: 5,
  review: 3,
  blocked: 5,
  done: 0,
}

/**
 * 优先级 = 影响 × 紧急度 的落地简化（ITIL 优先级矩阵）。
 * 小团队直接在四个等级上做取舍，slaHours 为期望解决时限（小时）。
 */
export const PRIORITY = {
  p1: { label: 'P1', name: '紧急', color: '#b91c1c', soft: '#fdeaea', slaHours: 4, rank: 1 },
  p2: { label: 'P2', name: '重要', color: '#b45309', soft: '#fdf3e3', slaHours: 8, rank: 2 },
  p3: { label: 'P3', name: '普通', color: '#0369a1', soft: '#e5f0fb', slaHours: 24, rank: 3 },
  p4: { label: 'P4', name: '一般', color: '#475569', soft: '#f1f5f9', slaHours: 72, rank: 4 },
}

/** 团队角色：所有者 1~2 人负责空间治理、任务与资源分配、项目汇总 */
export const ROLES = {
  owner: { label: '所有者', color: '#b45309', soft: '#fdf3e3', desc: '团队空间管理者：成员、资源、项目汇总' },
  admin: { label: '管理员', color: '#4f46e5', soft: '#ecebfd', desc: '可代所有者分派任务与维护资源' },
  member: { label: '成员', color: '#475569', soft: '#f1f5f9', desc: '承接任务、更新进度、参与复盘' },
}

/** 资源与工具台账的类型 */
export const RESOURCE_KINDS = {
  runbook: { label: '操作手册', icon: '📘', color: '#0f766e' },
  doc: { label: '文档资料', icon: '📄', color: '#4f46e5' },
  link: { label: '外部链接', icon: '🔗', color: '#0369a1' },
  tool: { label: '工具', icon: '🧰', color: '#b45309' },
  asset: { label: '资产', icon: '🖥️', color: '#7c2d12' },
  credential: { label: '凭据位置', icon: '🔑', color: '#6d28d9' },
}

/** 项目性质：Run（改善运维）与 Build（新建交付）—— 运维团队项目少，需区分 */
export const PROJECT_KINDS = {
  build: { label: '建设型', desc: '新建或替换：系统上线、迁移、平台搭建', color: '#15803d' },
  improve: { label: '改善型', desc: '降本提效：自动化、监控完善、隐患治理', color: '#0369a1' },
}

export const PROJECT_STATUS = {
  active: { label: '进行中', color: '#0f766e' },
  waiting: { label: '等待中', color: '#b45309' },
  someday: { label: '搁置', color: '#6d28d9' },
  done: { label: '已完成', color: '#15803d' },
  archived: { label: '已归档', color: '#737373' },
}

/** 值班班次 */
export const SHIFT_SLOTS = {
  day: { label: '白班 08:00-18:00', short: '白班' },
  night: { label: '夜班 18:00-08:00', short: '夜班' },
  weekend: { label: '周末全天', short: '周末' },
  holiday: { label: '节假日值守', short: '节假日' },
}

/**
 * 运维情境 —— GTD 的「情境」在运维场景不是地点，而是「可用的工作模式与工具」。
 * 没有专用工具时，人只能靠记忆切换模式，这正是运维打断频率高的根因之一。
 */
export const DEFAULT_CONTEXTS = [
  { name: '值守台', kind: 'mode' },
  { name: '现场/机房', kind: 'place' },
  { name: '变更窗口', kind: 'mode' },
  { name: '深度专注', kind: 'energy' },
  { name: '沟通协调', kind: 'tool' },
  { name: '远程支持', kind: 'tool' },
  { name: '低精力杂务', kind: 'energy' },
]

/** 默认责任领域（服务域），用于把工作挂到稳定的责任面上，而不是逐个打标签 */
export const DEFAULT_AREAS = [
  { name: '网络与链路', criticality: 'high', description: '路由交换、专线、VPN、DNS' },
  { name: '服务器与虚拟化', criticality: 'high', description: '物理机、虚机、容器、存储' },
  { name: '应用与中间件', criticality: 'high', description: '业务系统、数据库、消息队列' },
  { name: '终端与桌面', criticality: 'normal', description: '办公终端、打印机、外设' },
  { name: '账号与权限', criticality: 'normal', description: 'AD、SSO、应用账号、离入职' },
  { name: '备份与恢复', criticality: 'high', description: '备份策略、恢复演练' },
  { name: '安全与合规', criticality: 'high', description: '补丁、漏洞、基线、审计' },
]

/** 巡检任务的标准清单骨架，新建巡检时自动带入 notes */
export const INSPECTION_TEMPLATE = [
  '【巡检清单】',
  '1. 核心服务状态（nginx / 数据库 / 中间件）',
  '2. 资源水位：CPU / 内存 / 磁盘（<80% / <85% / <80%）',
  '3. 备份完成情况与失败项',
  '4. 夜间告警汇总与遗留项',
  '5. 安全事件与异常登录',
  '',
  '【结论】异常项：__ ；已处理：__ ；转工单：__',
].join('\n')

/** 值班交接单的标准清单骨架 */
export const HANDOVER_TEMPLATE = [
  '【交班清单】',
  '· 未关闭告警：__ 条，重点：__',
  '· 进行中工单：__ 条，需接手：__',
  '· 本班次变更操作：__',
  '· 受阻/等待外部事项：__',
  '· 下个班次重点盯防：__',
].join('\n')

export const PALETTE = ['teal', 'indigo', 'amber', 'rose', 'violet', 'sky', 'emerald', 'slate']

/**
 * 口令长度规则（前端唯一来源）。
 *
 * 必须与服务端 `pyapp/security.py` 的 `MIN_PASSWORD_LENGTH` / `validate_password()`
 * 逐字对齐：服务端是 `len < 8` 拒绝、`len > 200` 拒绝。
 * 前端只做"提前告知"，强制仍在服务端；两侧口径不一致时用户会看到
 * "我明明填了 8 位，服务端却说不行"这种自相矛盾的提示（历史缺陷：前端只校验下限）。
 *
 * 注意：这套规则**只用于设置新密码**（注册、修改密码）。
 * 登录只需要非空 —— 历史账号可能存在短密码，前端若按新规则拦截，
 * 那些账号会在根本打不到服务端的情况下被永久锁在门外。
 */
export const MIN_PASSWORD_LENGTH = 8
export const MAX_PASSWORD_LENGTH = 200

/**
 * 前端构建版本号。
 * - 用于登录页右下角展示，便于确认当前页面是否为最新代码。
 * - 每次改动都要与此文件及各模块 import 中的 ?v= 版本串同步递增。
 *   （服务端版本号在 pyapp/__init__.py 的 APP_VERSION，发布时一并递增，
 *     两边不一致时说明浏览器命中/服务端未重启，排障第一眼就能看出来。）
 */
export const APP_VERSION = '20261118b'
