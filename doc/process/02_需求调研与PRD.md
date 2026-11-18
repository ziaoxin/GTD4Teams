# 02 · 需求调研与 PRD

> GTD4Teams 治理体系重建 · 产品经理（许清楚）交付
> 版本：v1.2（草案） ｜ 日期：2026-11-18 ｜ 执行模式：**严格模式**（命中登录认证 / RBAC 权限 / 数据库变更 / 生产部署回滚）
> v1.2 增量（本次）：新增 §4.3「集成域（API / MCP）」REQ-045~REQ-055，并在 §4.2 登记范围与「明确不做」。依据为 Mindwtr 官方 `power-users/local-api` 与 `power-users/mcp` 两页（本次已实读）；**CR-2026-001 预留的 REQ-040~044 仍以附录 D 为准，本次不占用**。本次只改需求表，不改业务代码，也不回填 CR 的其它编号。
> v1.1 增量：整合前端 / 后端工程师实测转交的 **4 条已验证差距** 与 **6 个产品语义待裁决项**（均已逐条抽验 `文件:行号`），并补「已有功能必须可达且诚实」的 REQ / BR / EX / 验收编号。v1.0 全部内容保留。
> 上游依据：`docs/process/00_开发流程总纲.md` §4（G0 门禁）、§5.2（统一编号规则）、§8（反向补课）
> 本次性质：**存量项目的需求侧反向补课 + 治理规格**。只做评估与文档，不修改任何业务代码。
> 文档路径：`docs/process/02_需求调研与PRD.md` ｜ 责任角色：产品经理 ｜ 更新触发：新增/变更需求（总纲 §5.1）

---

## 0. 本文口径与证据标注约定

用户（李鹏 / Leo）明确要求「重视实测数据与证据，不接受未经验证的断言」。本文所有「现状差距」「代码事实」「产品语义存疑」均按下列三档标注，**不做无证据断言**。

| 标注 | 定义 | 允许的证据形式 | 本文中的典型例子 |
|---|---|---|---|
| **【已验证】** | 本次会话通过 `Read` / `Grep` 直接读到具体 `文件:行号`，或项目文档写出可定位的原句 / 测试名 | `pyapp/policy.py:230-236`、`README.md:88`、`tests/test_api.py::test_20_...`、Grep 全量命中清单 | 「`work_items` 更新策略不含 owner 限制」 |
| **【推断】** | 由两条及以上【已验证】事实推出的结论，但没有一条文档或代码直接写出该结论 | 必须写明「由 X + Y 推出」 | 「ITIL 票种与 GTD 五步的映射只在注释里，未形成可交付规格」 |
| **【未验证】** | 本次未实际执行、无直接证据，或仅来自上游任务上下文 / 文档转述 | 明确写出「本次未重跑」「开发机无 Docker」「需上服务器实测」 | 「49 项测试本机全绿」「`docker build` 成功」「Linux 写锁行为」 |

**本项目既有的验证纪律可直接复用**：`docs/零停机发布方案.md:464-487` 的 §11「已验证 / 尚未验证（如实清单）」已经把「开发机可复跑」与「第一次上服务器请留意」分开列。本文沿用同一口径，不另造一套。

| 本次实读 / 检索的材料（附录 A 给出逐项索引） | 方式 |
|---|---|
| `README.md`（全文 266 行，含「与云版本的差别」「安全说明」「测试」「项目结构」） | Read 全文 |
| `js/config.js`（全文 171 行） | Read 全文 |
| `pyapp/policy.py`（全文 320 行） | Read 全文 |
| `pyapp/db.py`（全文 592 行） | Read 全文 |
| `pyapp/config.py`（全文 137 行）、`pyapp/__init__.py`、`index.html`（全文 67 行） | Read 全文 |
| `js/views.js`（`renderView` / `viewTeamBoard` / `PERSONAL_NAV` / `TEAM_NAV` / `renderSidebar` / 全量导出函数清单） | Read 片段 + Grep |
| `js/app.js`（`dispatch` 全量 case、`set-status`、`quick-done`、`saveWip`） | Read 片段 + Grep |
| `js/api.js`（全量导出、`friendlyError` 错误码映射表） | Read 片段 + Grep |
| `pyapp/httpd.py`（`ROUTES` 共 20 条 (method,path) / 19 条唯一路径）、`pyapp/store.py`（函数清单） | Grep |
| `tests/test_api.py`（49 个 `def test_` 名称全量） | Grep |
| `docs/零停机发布方案.md` §11 | Read 片段 |
| 全项目 `WIP` / `sla` / `threading` 关键字 | Grep 全量命中 |
| `docs/process/00_开发流程总纲.md`（全文 217 行） | Read 全文 |
| **v1.1 补读**：`js/app.js`（`:347` `assertAffected` 全量 24 行 Grep、`:388-395`、`:500-518`、`:597-609`、`:659-676`、`:860-873`、`:1030-1051`、`:1435-1451`）、`js/views.js:1895`、`js/api.js:347-385` | Read 片段 + Grep |
| **v1.1 补读**：`pyapp/httpd.py`（`:255,268-270,524-540,592-595,648-671,724-728,815-835`）、`pyapp/store.py:336-352`、`pyapp/security.py:27,100-142`、`pyapp/config.py:80-85` | Read 片段 + Grep |
| **v1.1 全量检索**：`js/` 下 `pwd-form\|pwd-old\|pwd-new\|submit-password`（6 处命中、零处渲染）、`js/app.js` 下 `assertAffected`（定义 1 + 调用 23）、`pyapp/` 下 `last_seen_at\|UPDATE sessions\|expires_at` | Grep 全量命中 |

> **重要边界**：本文是「需求侧规格」，不替代 `03 FDS`（页面/交互/视觉，UI/UX 设计师）、`04 TDS`（架构/权限实现，架构师）、`05 数据模型`（数据建模师）、`06 接口契约`（架构师）、`07 测试体系`（质量保障）、`08 安全审计`（安全工程师）、`09 运维手册`（运维工程师）。文中出现的代码行号是**取证**，不是替下游做技术设计。

---

## 1. 需求调研方案

本项目为**存量系统反向补课**（总纲 §8），因此调研方案有两个用途：① 给出一套「若现在要新增/变更需求」可直接执行的调研 SOP；② 说明本次 PRD 的「已兑现需求」是从代码反推、而非来自真实访谈，并诚实标出这一步的证据缺口（见 §2.3、§8 OPEN-08）。

### 1.1 调研目标

| 目标 ID | 目标 | 衡量口径（可判定） | 证据来源 | 关联差距 |
|---|---|---|---|---|
| RG-01 | 还原「小型 IT 运维团队」的真实工作链路 | 产出 ≥ 8 个端到端场景，覆盖值班交接 / 告警转工单 / 变更窗口 / 巡检 / 跨团队协作 | 访谈记录 + 场景采集表 | GAP-01 / GAP-05 |
| RG-02 | 把代码里已实现的功能反推成带 ID 的需求基线 | 每条 REQ 有唯一 ID、优先级、验收标准、至少一处代码证据 | 本文 §4.3 / §5 | GAP-02 |
| RG-03 | 把只存在于注释 / README 一句话的「刻意取舍」显式化 | 逐条列出并标注「刻意保留 / 产品语义存疑」，不擅自修正 | `README.md:83-90`、`pyapp/policy.py` 注释 | GAP-03 / GAP-09 |
| RG-04 | 建立需求 → 规则 → 实现 → 测试 → 验收的可追溯链 | 每条 REQ 至少能追到 1 条 BR/EX + 1 处实现 + 1 条测试用例 + 1 条验收项 | 总纲 §5.3、本文 §1.7 | GAP-02 |
| RG-05 | 判定 MVP 与范围边界，防止小团队过度设计 | 输出「必须做 / 可延后 / 明确不做」三档且每档有理由 | 本文 §4.2 | GAP-06 |
| RG-06 | 明确非功能约束的可判定指标 | 离线 / 零依赖 / 单服务器 / 数据可携带各有「通过 / 不通过」判据 | 本文 §4.9 / §5 | GAP-12 |

### 1.2 访谈对象与角色

角色取自 `js/config.js:76-80` 的 `ROLES`（owner / admin / member），并按运维实际补出「值班工程师」这一**班次视角**（它不是独立角色，而是 member/owner 在 `on_call` 上的临时职责），以及「管理员」在 README 语境中含「实例级账号管理员」与「团队 admin」两种含义，需区分（见 §8 OPEN-09）。

| 对象 ID | 角色 | 典型职责（取证） | 访谈关注点 | 建议样本 | 招募方式 |
|---|---|---|---|---|---|
| IT-01 | 团队 Owner（owner） | 空间治理、成员/资源/项目汇总（`js/config.js:77`）；创建团队时自动登记为 owner（`tests/test_api.py::test_10_create_team_registers_owner_atomically`） | 团队划分口径、邀请码管理、成员停用、WIP 与 SLA 阈值谁定、归档后数据如何处理 | 1~2 人 / 团队 | 现有账号 `users` 中 `teams.owner_id` 对应者 |
| IT-02 | 值班工程师（on-call，班次视角） | 值班排班与交接（`pyapp/db.py:254-270` 的 `on_call`、`js/config.js:152-159` 的 `HANDOVER_TEMPLATE`） | 交接单字段够不够、未关闭告警如何转成工单、夜班/节假日班次、离线时怎么记 | 2~3 人 / 团队 | `on_call.primary_id` / `backup_id` 近期有值者 |
| IT-03 | 普通成员（member） | 承接任务、更新进度、参与复盘（`js/config.js:79`） | 看板流转是否被 WIP 挡住、个人 GTD 五步是否真用、团队分配项在哪跟进、`quick-capture` 是否够快 | 3~5 人 / 团队 | `team_members.role='member'` 且 `status='active'` |
| IT-04 | 管理员（admin，两层含义） | 团队层：代 owner 分派任务与维护资源（`js/config.js:78`）；实例层：关闭注册后用 `--set-password` 开账号（`README.md:42,148`） | 与 owner 的权限差异在哪里、能否删他人进度日志、停用成员的操作路径 | 1 人 / 团队 + 1 实例管理员 | `team_members.role='admin'` + 部署负责人 |
| IT-05 | 跨团队协作方（外部/兄弟团队） | 被 `waiting` / 跨团队依赖间接涉及 | 跨团队事项当前靠什么传递、是否需要只读视图 | 1~2 人 | 由 IT-01 指认 |

> **【已验证】** 角色枚举确为 3 个（`js/config.js:76-80`），`policy.py:56-63` 的 `is_admin` 把 `owner` 与 `admin` **合并**为同一判定，代码层未区分二者；`is_owner` 单独存在但仅用于 `team_members` 插入策略（`policy.py:160`）。这是 §3.5 的存疑点之一。

### 1.3 访谈提纲（20 题，按运维场景分组）

题目要求：贴合值班交接、告警转工单、变更窗口、SLA、跨团队协作、离线可用性。每题都写出「期望产出的需求线索」，避免空泛提问。

| 编号 | 主题 | 主问对象 | 具体问题 | 期望产出的需求线索 |
|---|---|---|---|---|
| Q01 | 值班交接 | IT-02 | 交班时你最怕漏掉哪一类信息？现在 `HANDOVER_TEMPLATE` 的 5 条（未关闭告警 / 进行中工单 / 本班变更 / 受阻等待 / 下班次盯防，`js/config.js:152-159`）够不够？ | 交接单是否需要结构化字段而非纯文本；是否需要「未确认接班」状态 |
| Q02 | 值班交接 | IT-01/02 | 未关闭告警现在是手工抄进交接单，还是直接登记成 `incident` 工单？如果转工单，谁建、`reported_by` 填什么？ | 告警→工单的自动/半自动转换需求；`work_items.source` / `reported_by` 字段（`pyapp/db.py:122,145`）是否被真正使用 |
| Q03 | 值班交接 | IT-02 | 接班人没确认时，责任算谁的？系统要不要留「已交接 / 未交接」的痕迹？ | `on_call.status` 取值是否需要扩展（当前默认 `'planned'`，`pyapp/db.py:266`） |
| Q04 | 告警转工单 | IT-02/03 | 一条告警从出现到进入 `intake`，中间经过几步？会不会出现「电话里处理完了、系统里没有」？ | 收集入口（`index.html:31-34` 的 quick-capture）是否覆盖告警场景；补录规则 |
| Q05 | 告警转工单 | IT-01 | `incident` 与 `request` 都落在 `unplanned` 泳道（`js/config.js:25,24`），你们实际怎么区分？区分错了要不要能改票种？ | 票种可变更规则；泳道自动/手工归属（BR-014） |
| Q06 | 变更窗口 | IT-01/02 | 变更发布（`change`，`js/config.js:27`）有没有固定窗口？窗口外能不能建、能不能推进到 `doing`？ | 变更窗口是否需要时间约束与越窗提示；是否纳入 NFR/业务规则 |
| Q07 | 变更窗口 | IT-02 | 变更失败回滚时，工单状态怎么回写？现在 `set-status` 可以任意跳转（`js/app.js:699-705`），有没有出现过误回退？ | 状态机是否需要合法转换约束（见 §8 OPEN-03、BR-011） |
| Q08 | SLA | IT-01 | `P1/P2/P3/P4` 对应 4/8/24/72 小时（`js/config.js:68-73`）是你们真实承诺的时限吗？超时后谁被通知、要不要自动升级？ | SLA 是否需要自动计算 `sla_due_at` / 超时升级 / 提醒（当前无后端逻辑，见 GAP-07） |
| Q09 | SLA | IT-03 | `sla_due_at` / `responded_at`（`pyapp/db.py:125-126`）你在界面上见过吗？是手填还是系统算的？ | 字段是否实际被消费；前端 `slaRisk` / `isOverdue`（`js/views.js:111,119`）是否满足 |
| Q10 | 看板与 WIP | IT-01/03 | 当「处理中」超过 5 项时，系统现在只把列标红（`js/views.js:932-936`），你希望它**阻止**继续拉入，还是**只提醒**？ | WIP 是硬约束还是软提示的产品裁决（§8 OPEN-01，GAP-04） |
| Q11 | 看板与 WIP | IT-01 | 团队的 WIP 上限由谁改、改完要不要通知成员？`board_config` 存的是纯数字（`js/app.js:1191-1205`），是否要保留修改历史？ | 配置变更是否需要审计（`app_logs`）与通知 |
| Q12 | 巡检 | IT-02 | `INSPECTION_TEMPLATE` 的 5 项清单（`js/config.js:140-149`）你们照着用吗？异常项「转工单」是手工建还是一键建？ | 巡检模板结构化、异常项自动建单需求 |
| Q13 | 巡检 | IT-03 | 周期巡检靠 `spawnNextOccurrence` 自动派生下一次（`js/app.js:691-694`），周期变更/跳过某一期怎么处理？ | `work_items.recurrence`（`pyapp/db.py:129`）的规则是否需要文档化 |
| Q14 | 跨团队协作 | IT-05/01 | 依赖外部团队的事项现在放在 `waiting` / `waiting_for`（`pyapp/db.py:135`），对方能看到吗？靠什么同步？ | 是否需要跨团队只读视图或导出；当前权限不允许（`policy.py` 仅本团队成员） |
| Q15 | 跨团队协作 | IT-01 | 一个人同时属于多个团队时（`team_members` 允许同一 `user_id` 多行，`pyapp/db.py:103` 唯一键是 `(team_id,user_id)`），切换成本如何？有没有「所有团队待办」的汇总视图？ | 跨团队汇总需求；现有只有单团队 `renderTeamSelect`（`js/views.js:1945-1950`） |
| Q16 | 离线可用性 | IT-02/03 | README 写「可完全离线」（`README.md:31`），但机房/现场可能断网。断网时你能做哪些操作？重新连上后数据怎么合？ | 离线边界的产品定义（核心功能离线 vs AI 需联网，GAP-11 / §8 OPEN-05）；是否需要冲突处理 |
| Q17 | 离线可用性 | IT-01 | 单服务器 + SQLite 单文件（`README.md:30,56`），如果同时有 2 个人改同一张卡片，你们接受「后写覆盖」吗？ | 并发编辑的产品容忍度；当前无乐观锁字段（`work_items` 无 `version`，`pyapp/db.py:106-146`） |
| Q18 | 权限与信任 | IT-01/04 | 成员被停用后立刻看不到团队数据（`README.md:90`），这个「立刻」符合预期吗？需不需要一个「交接期只读」？ | `team_members.status` 语义（§3.5 存疑点、BR-007） |
| Q19 | 权限与信任 | IT-01 | 团队情境 `contexts` 只有创建者能改（`README.md:89`），但工作项 `work_items` 谁都能推进（`README.md:88`）——这两条不一致你们遇到过吗？是有意为之吗？ | 云版遗留形状是否保留的产品裁决（§3.5、§8 OPEN-04） |
| Q20 | 数据与交接 | IT-01 | 整库导出 `--export` 含口令散列（`README.md:45,56`）、`--import` 是 INSERT OR REPLACE（总纲 §7.4），团队移交/人员离职时你们怎么做？ | 数据可携带与灾难恢复 SOP；是否需要按团队导出 |

### 1.4 场景采集表

场景编号 `SC-`，供 FDS（任务 #2）直接取用；「现有替代」一列如实记录**系统在代码里是否已经承接**。

| 场景 ID | 场景名 | 触发条件 | 主角色 | 主要步骤 | 期望结果 | 当前系统承接情况（证据） | 频次估计 |
|---|---|---|---|---|---|---|---|
| SC-01 | 告警转事件工单 | 监控告警 / 电话报障 | IT-02 | 登记 `incident` → `intake` → 分类 → 派 `assignee_id` → 处理 → `done` | 全程可追溯、SLA 可见 | 部分承接：票种与状态齐备（`js/config.js:25,38-47`），但 `reported_by`/`source` 无录入规则文档（GAP-13） | 高 |
| SC-02 | 值班交接 | 班次结束 | IT-02 | 打开值班页 → 填 `handover_notes` → 下班次确认 | 未关闭告警 / 在办工单不丢 | 部分承接：`on_call.handover_notes` + 静态模板（`pyapp/db.py:265`、`js/config.js:152-159`），无确认状态（Q03） | 高 |
| SC-03 | 计划内巡检 | 周期到点 | IT-03 | 系统派生下次巡检 → 按清单核对 → 异常项转工单 | 周期不遗漏、异常有单 | 已承接：`spawnNextOccurrence`（`js/app.js:691-694`）+ `INSPECTION_TEMPLATE`；模板为纯文本（Q12） | 中 |
| SC-04 | 变更窗口内发布 | 变更审批通过 | IT-01 | 建 `change` → 排期 → 窗口内 `doing` → 验证 `review` → `done` | 变更可回溯、失败可回写 | 部分承接：票种/状态齐备，无窗口约束与状态转换约束（Q06/Q07、GAP-08） | 中 |
| SC-05 | 跨团队等待 | 依赖外部团队 | IT-03 | 置 `waiting` + 填 `waiting_for` → 定期跟进 → 恢复 | 责任不丢、有提醒 | 部分承接：字段存在（`pyapp/db.py:44,135`），无提醒/外部可见（Q14） | 中 |
| SC-06 | 看板拉入受阻 | 某列已达 WIP 上限 | IT-01/03 | 拖卡 / `set-status` 到该列 | 期望：或被阻止，或明确告警 | **软提示**：仅 `is-over` 标红 + 文案，不拦截（`js/views.js:932-936,956`、`js/app.js:699-705`）；见 GAP-04 | 高 |
| SC-07 | 个人 GTD 五步 | 想到一件待办 | IT-03 | 快速收集 → 厘清 → 组织 → 今日执行 → 复盘 | 注意力不丢 | 已承接：12 个个人页（`js/views.js:1907-1920`）+ `quick-capture`（`index.html:31-34`） | 高 |
| SC-08 | 团队派活到个人 | owner 分派 | IT-01 | 建团队工作项并设 `assignee_id` | 出现在个人「团队分配给我的」 | 已承接：`assignedToMe`（`js/views.js:100`）、`viewAssigned`（`:643`）、README:2055 的说明 | 高 |
| SC-09 | 周/月复盘 | 周期结束 | IT-01/03 | 生成复盘 → 填 notes/stats → 完成 | 同周期只留一条 | 已承接：`reviews` 唯一索引（`pyapp/db.py:251-252`）、`test_26_review_upsert_keeps_single_row_per_period` | 中 |
| SC-10 | 断网现场作业 | 机房无网络 | IT-02 | 本地记录 → 恢复后继续 | 核心功能不中断 | 已承接：零外部资源（`README.md:31,81`）；但 AI 通道需联网，且「离线」未定义边界（GAP-11） | 低 |
| SC-11 | 成员停用 | 人员离岗 | IT-01/04 | 置 `status != 'active'` | 立即失去访问权，且不可自行重新加入 | 已承接：有意收紧（`policy.py:314-319`、`README.md:90`、`test_20_disabled_member_loses_access_and_cannot_rejoin`） | 低 |
| SC-12 | 整库迁移/备份 | 换机 / 上服务器前 | IT-01 | `--export` → 复制 → `--import` | 数据可携带、可还原 | 已承接：`test_backup_round_trip`、`README.md:45-46`；`--import` 语义需警惕（总纲 §7.4、EX-021） | 低 |

### 1.5 调研记录模板

> `docs/process/templates/` 目前为空（本次会话 `ls` 已确认），4 个模板由质量保障（任务 #5）产出。本节只定义**字段语义**，不创建模板文件，避免与他人的产出冲突。字段命名与总纲 §5.2 的 ID 规则一致。

| 字段 | 类型 | 必填 | 说明 | 校验规则 |
|---|---|---|---|---|
| `record_id` | ID | 是 | `IR-2026-NNN`（Interview Record） | 全局唯一、不复用 |
| `date` / `duration_min` | 日期/整数 | 是 | 访谈日期与时长 | 日期不得晚于当日 |
| `interviewee_id` | 枚举 | 是 | 取 §1.2 的 IT-01~IT-05 | 必须在角色表内 |
| `team_id` / `anon` | 文本/布尔 | 是 | 所属团队；是否匿名 | 匿名时不得记录可识别信息 |
| `question_ids` | ID 列表 | 是 | 本次覆盖的 Q 编号 | 至少 3 个 Q |
| `verbatim` | 文本 | 是 | **原话**，不做归纳 | 不得出现「应该 / 可能需要」等代拟措辞 |
| `scene_id` | ID | 否 | 关联 SC- | 若填，须在场景表内 |
| `pain_point` | 文本 | 是 | 痛点（由原话提炼，需标「提炼」） | 与 `verbatim` 分列存放 |
| `current_workaround` | 文本 | 是 | 现在怎么绕过去（Excel / 群聊 / 记忆…） | 不得空 |
| `frequency` | 枚举 | 是 | 每日 / 每周 / 每月 / 偶发 | 与 §1.4 频次口径一致 |
| `candidate_req` | 文本 | 否 | 候选需求描述（**此时不分配 REQ-ID**） | 需求 ID 在评审通过后由 §1.7 分配 |
| `evidence_type` | 枚举 | 是 | 原话 / 观察 / 系统日志 / 推测 | 「推测」不计入需求依据 |
| `priority_hint` | 枚举 | 是 | MoSCoW 初判（§1.6） | 仅为初判，终裁在评审 |
| `conflict_with` | ID 列表 | 否 | 与哪些记录/需求冲突 | 用于 §7.6 失败处理 |

可复制空白记录：

```
IR-2026-___ ｜ 日期：____ ｜ 时长：__ 分钟 ｜ 对象：IT-0_ ｜ 团队：____ ｜ 匿名：是/否
覆盖问题：Q__ / Q__ / Q__
原话：「……」（逐字，不改写）
关联场景：SC-__  ｜ 痛点（提炼）：…… ｜ 现有替代：…… ｜ 频次：每日/每周/每月/偶发
候选需求（不编号）：…… ｜ 证据类型：原话/观察/系统日志/推测 ｜ 优先级初判：M/S/C/W
冲突记录：IR-2026-___ ｜ 记录人：____
```

### 1.6 需求优先级判定口径（MoSCoW + KANO，可执行）

采用**两级判定**：先用 KANO 判断「做了会不会提升满意度 / 不做会不会不满」，再用 MoSCoW 结合「运维风险 + 实现成本」定档。评分必须能复算，不允许拍脑袋。

| 步骤 | 判定项 | 取值 | 计分 | 依据 |
|---|---|---|---|---|
| K1 | 不做该需求是否引发明确不满（基本型） | 是 / 否 | 是 = +3 | 运维事故、数据丢失、越权、无法交接 |
| K2 | 做了是否线性提升满意度（期望型） | 是 / 否 | 是 = +2 | 效率、可见性、减少手工 |
| K3 | 做了是否带来惊喜（兴奋型） | 是 / 否 | 是 = +1 | AI 辅助、自动化建议 |
| K4 | 是否无差异（做了没感觉） | 是 / 否 | 是 = -2 | 需实测确认，不能凭想象 |
| R1 | 触及安全 / 权限 / 数据完整性 | 是 / 否 | 是 = +3 | 总纲 §4 G5 独立阻塞权 |
| R2 | 触及核心闭环（收集→执行→复盘 / 派活→流转） | 是 / 否 | 是 = +2 | 本文 §4.5 主流程 |
| C1 | 实现成本（人日，本项目为 1 人 + AI） | ≤0.5 / 0.5~2 / >2 | 分别 +2 / 0 / -2 | 总纲 §1 复杂度原则 |
| C2 | 是否破坏「零依赖 / 单服务器 / 离线」约束 | 是 / 否 | 是 = -3 | 本文 §4.9 NFR |

**总分 = K1+K2+K3+K4+R1+R2+C1+C2**，按下表定档：

| 总分 | MoSCoW 档 | 含义 | 处置 |
|---|---|---|---|
| ≥ 8 | **M（必须做）** | 本期必须兑现，缺失即阻塞 G0 | 必须配 BR/EX/验收标准/测试用例 |
| 5 ~ 7 | **S（应该做）** | 本期尽量做，可延后一个迭代 | 需登记到「可延后」并注明目标版本 |
| 2 ~ 4 | **C（可以做）** | 有余力才做 | 不占用本期门禁 |
| ≤ 1 | **W（本次不做）** | 明确不做，写入 §4.2 防止范围蔓延 | 需写明「不做的理由」，避免日后反复讨论 |

| 复算示例（取自本文真实需求） | 计算 | 结果 |
|---|---|---|
| 行级权限与双域隔离 REQ-026 | K1(+3) + R1(+3) + R2(+2) + C1(+2) + C2(0) = 10 | **M** |
| WIP 硬约束（候选，见 OPEN-01） | K2(+2) + R2(+2) + C1(0) + C2(0) + K1(0) = 4 | **C**（故不擅自加入 MVP，先裁决语义） |
| AI 辅助生成巡检结论（候选） | K3(+1) + C1(0) + C2(-3，需联网) = -2 | **W** |

> 该口径的作用是**让「要不要做 WIP 硬约束 / SLA 自动化」这类争议可复算**，而不是靠「感觉挺重要」。所有候选需求在 §4.2 归档时必须带总分。

### 1.7 追溯矩阵 ID 规则

严格遵循总纲 §5.2，不另造前缀。

| 前缀 | 含义 | 本文使用范围 | 编号格式 | 示例 |
|---|---|---|---|---|
| `REQ-` | 需求 | §4.3 功能清单（需求基线） | `REQ-NNN`，3 位数字，按域分段连续编号，**不复用、不回收** | `REQ-026` |
| `BR-` | 业务规则 | §4.7 | `BR-NNN` | `BR-005` |
| `EX-` | 异常场景 | §4.8 | `EX-NNN` | `EX-016` |
| `TC-` | 测试用例 | §7.2 清单（正式台账归 `07`） | `TC-<域>-NNN` | `TC-AUTH-013` |
| `ACC-Gn-` | 阶段验收单 | §5 引用，不自建文件 | `ACC-G0-20261113` | 由 QA 模板生成 |
| `CR-` | 需求变更单 | §6 | `CR-2026-NNNN` | `CR-2026-0007` |
| `SC-` / `Q-` / `IR-` / `GAP-` / `DD-` / `NFR-` / `OPEN-` | 场景 / 访谈问题 / 调研记录 / 差距 / 已兑现需求 / 非功能 / 开放问题 | **本文内部编号** | 各自连续 | `GAP-04` |

**REQ 状态机**（需求从提出到作废的生命周期，供 §6.5 回退规则使用）：

| 状态 | 含义 | 允许的下一状态 | 谁可变更 |
|---|---|---|---|
| `draft` | 调研记录产生候选需求，未评审 | `approved` / `rejected` | 产品经理 |
| `approved` | 评审通过、已分配 REQ-ID 与验收标准 | `baselined` / `deprecated` | 用户（G0 放行权，总纲 §4） |
| `baselined` | 已进入需求基线、有 BR/EX/测试/验收链 | `changed`（走 CR）/ `deprecated` | 用户 + 变更单 |
| `changed` | 已被 CR 修改，旧版本保留可读 | `baselined` / `deprecated` | 用户 |
| `deprecated` | 已作废（**不删除、不复用编号**） | 终态 | 用户 |
| `rejected` | 评审未通过 | 终态 | 产品经理 |

**追溯链最低要求**（总纲 §5.3）：`REQ → (BR|EX) → 实现(文件:行号) → 测试用例 → 验收项`。本文 §4.3 每行都给出「实现证据」，§5 每条验收项都回指 REQ-ID；追不到链的需求在 §2.2 记为 GAP-02。

| 追溯矩阵示例（完整矩阵见 §4.3 + §5） | REQ | BR / EX | 实现证据 | 测试用例 | 验收项 |
|---|---|---|---|---|---|
| 示例 1 | `REQ-026` 行级权限与双域隔离 | `BR-001` / `BR-003` / `EX-009` | `pyapp/policy.py:79-85,224-267` | `test_14` / `test_15` / `test_16` / `test_17` | `AC-F-26` / `AC-A-14` |
| 示例 2 | `REQ-013` 团队复盘 | `BR-009` / `EX-019` | `pyapp/db.py:251-252`、`pyapp/store.py:258` | `test_26` / `test_27` | `AC-F-13` / `AC-A-26` |
| 示例 3 | `REQ-006` 看板与 WIP | `BR-012` / `EX-016` | `js/views.js:918-957`、`js/app.js:1191-1205` | `test_25_team_board_config_round_trip` | `AC-F-06` |

---

## 2. 现状与需求侧差距分析

### 2.1 方法：把「代码里实现的功能」反推成「已兑现的需求」

存量项目没有需求文档，唯一可靠的基线是**代码 + 测试 + README**。下表先把已兑现的能力固化下来（这是 §4.3 REQ 清单的来源），再在 §2.2 找差距。

| DD-ID | 已兑现的能力（反推） | 直接证据 | 标注 |
|---|---|---|---|
| DD-01 | 自带账号体系：注册/登录/登出/改密、PBKDF2、会话散列存储、登录限速、改密踢其它会话 | `README.md:75-76,102,105`；`test_03`~`test_09` | 【已验证】 |
| DD-02 | 多团队：创建时原子登记 owner、邀请码大小写不敏感唯一、归档团队不可加入 | `pyapp/db.py:86-87`；`pyapp/store.py:166,208`；`test_10`~`test_13` | 【已验证】 |
| DD-03 | 团队角色 3 类 + 成员停用即失权 + 防自行提权 + owner 行受保护 | `js/config.js:76-80`；`pyapp/policy.py:162-176,314-319`；`test_18`~`test_20` | 【已验证】 |
| DD-04 | 双域共用 `work_items`：`team_id IS NULL` 为个人、非空为团队共享；归属由服务端注入 | `README.md:85-86`；`pyapp/policy.py:79-85`；`pyapp/db.py:565-591`；`test_14`~`test_17` | 【已验证】 |
| DD-05 | 7 类 ITIL 轻量票种 + 11 态统一状态机 + 团队/个人双标注 | `js/config.js:21-29,37-48` | 【已验证】 |
| DD-06 | 看板 7 列 + 双泳道 + 可配置 WIP 上限（`board_config`） | `js/config.js:51,54-62`；`js/views.js:918-957`；`test_25` | 【已验证】 |
| DD-07 | 项目 / 责任领域 / 情境 / 资源台账 / 工具台账 / 值班排班与交接 | `pyapp/db.py:151-218,254-270`；`js/views.js:960-1130,1232-1356` | 【已验证】 |
| DD-08 | 个人 GTD 五步 12 个视图 + 快速收集 + 团队分配给我的 | `js/views.js:1907-1920`；`index.html:31-34`；`js/views.js:100` | 【已验证】 |
| DD-09 | 复盘同周期唯一 + 进度日志 + 运行日志（只写不改删） | `pyapp/db.py:221-252`；`pyapp/policy.py:208-221`；`test_26`~`test_28` | 【已验证】 |
| DD-10 | 写操作双重校验、不开 CORS、静态白名单、目录穿越拦截、字段白名单与类型归一化 | `README.md:96-100`；`pyapp/config.py:22`；`test_22`~`test_24`,`test_29`~`test_36` | 【已验证】 |
| DD-11 | 整库导出/导入、数据目录可外挂、SQLite WAL、幂等建库 | `README.md:45-46,56-59`；`pyapp/db.py:425-487`；`test_backup_round_trip`、`test_init_db_is_idempotent` | 【已验证】 |
| DD-12 | 可选 OpenAI 兼容 AI 通道，密钥只在服务端、不回显、未配置有明确错误码 | `README.md:175-192`；`pyapp/config.py:94-120`；`test_38`~`test_39` | 【已验证】 |
| DD-13 | 双槽位蓝绿零停机发布 + 构建标识核对 + 冒烟/探测门禁 | `README.md:108-152`；`docs/零停机发布方案.md:464-487`；`pyapp/httpd.py:815-835` | 【已验证】（文档与脚本存在性） |
| DD-14 | 前端 25 个页面/视图由 `renderView` 分发，约 50 个 `data-action` 由 `dispatch` 统一委托 | `js/views.js:2017-2048`；`js/app.js:597-869` | 【已验证】 |

### 2.2 差距与风险清单（20 条，逐条带证据 + 标注）

| GAP-ID | 差距 / 风险描述 | 证据（文件:行号 / 测试名 / README 原句） | 标注 | 影响 | 建议归属 |
|---|---|---|---|---|---|
| **GAP-01** | **从无需求调研记录**：`docs/` 原有 4 份全是专题技术文档，无访谈、无场景、无原始诉求 | `docs/` 目录清单（AI能力设计.md / Docker部署方案.md / 桌面客户端设计.md / 零停机发布方案.md）；总纲 §2「需求 → 代码断链」；`README.md:232` 把 docs 描述为「设计文档」 | 【已验证】 | 无法回答「为什么做这个功能」，改一处不敢判影响面 | 本文 §1 补齐 SOP；真实访谈是否补做待裁决（OPEN-08） |
| **GAP-02** | **无需求编号 / 无追溯矩阵**：功能只散落在枚举、视图函数、README 描述里，任何线上行为都追不到「哪条需求 / 哪条规则 / 哪条验收」 | 总纲 §1「不可追溯」、`README.md:3-5` 仅一句话定位；`js/config.js` 无 REQ 注释；`tests/test_api.py` 测试名无需求 ID 前缀 | 【已验证】 | 回归范围靠记忆、变更无法评估、新人接不住 | 本文 §1.7 / §4.3 / §5 建立基线 |
| **GAP-03** | **关键设计取舍只写在代码注释 / README 一句话**，且明确警告「改之前请先读」，但没有规格文档承载 | `README.md:83` 原句「权限语义刻意保持一致的几处（都在代码注释里写明了原因，改之前请先读）」；`pyapp/policy.py:230-233,249-251` 注释 | 【已验证】 | 后来者极易把「刻意保留」误判成 bug 顺手修好（总纲 §2 已记录历史上因此丢过改动） | 本文 §3.5 如实登记，规格落 `04`/`05` |
| **GAP-04** | **WIP「限制」名不副实**：README 与 config.js 宣称 Kanban「WIP 限制」，但代码只做视觉告警，不阻止超额拉入 | `README.md:5`「Kanban（显式流程、WIP 限制）」；`js/config.js:12,54-62` 常量；`js/views.js:932-936` 仅 `over ? 'is-over'` 标红、`:956` 文案「触顶时正确做法是…而不是把卡片硬塞进去」；`js/app.js:699-705` `set-status` 直接改状态无任何校验；`js/app.js:1191-1205` `saveWip` 仅写 `board_config`；全项目 `WIP` Grep 在 `pyapp/` **零命中** | 【已验证】 | 方法论承诺与实际行为不符；用户预期可能被误导 | **需产品裁决**（OPEN-01），本文不擅自改代码 |
| **GAP-05** | **ITIL 票种 × GTD 五步的映射未文档化**：只在 config.js 一段注释里说明「两套只是同一枚举的不同标注」 | `js/config.js:10-15` 注释、`js/config.js:31-36` 注释、`js/config.js:37-48` 的 `personal` 字段；无独立映射表/规则文档 | 【已验证】 | 新人无法理解团队看板与个人清单为何共用一套状态；票种→GTD 步骤的对应关系无定义 | 本文 §4.6 补映射表（推断部分已标注） |
| **GAP-06** | **无 MVP 范围定义、无「明确不做」清单**，边界靠 README 散述 | 全项目 Grep 未见 MoSCoW / MVP 文档；`README.md:177` 仅对 AI 写「可选」 | 【已验证】 | 范围易蔓延；候选需求（WIP 硬约束、SLA 自动化）无归档处 | 本文 §4.2 + §1.6 |
| **GAP-07** | **SLA 只有数据位和前端显示，无后端自动化**：定义了 4/8/24/72 小时与 `sla_due_at`/`responded_at` 字段，但没有自动计算、超时判断、升级或通知 | `js/config.js:68-73`（`slaHours`）；`pyapp/db.py:80,125-126,304,319`（`sla_config`/`sla_due_at`/`responded_at` 仅字段与 COLUMNS 声明）；`pyapp/` 全量 Grep `sla|cron|Timer|schedule` 仅命中字段与 `threading.Lock`（`pyapp/httpd.py:26,58` / `pyapp/security.py:23,116`），无任何定时任务或 SLA 逻辑；前端 `js/views.js:111,119,307` 的 `isOverdue`/`slaRisk`/`dueChip` 为纯展示 | 【已验证】（无后端逻辑）／【推断】（因此 SLA 保障未真正兑现） | 「P1 4 小时」目前是标签，不是承诺 | 需裁决（OPEN-02） |
| **GAP-08** | **状态机缺业务规则说明**：11 个状态、7 个看板列只有枚举，没有「哪些转换合法 / 谁能转 / 终态能否回退」；`set-status` 可把任意项直接改成任意状态 | `js/config.js:37-51` 只有枚举与列顺序；`js/app.js:699-705` `set-status` 直接 `updateItem(id,{status: ds.status})`；`js/app.js:684-689` `quick-done` 可在 `done` 与 `ready` 间来回切；无状态转换表文档 | 【已验证】 | 变更失败回滚、误操作、审计口径无依据（Q07） | 本文 §4.6 补规则表；是否加硬约束待裁决（OPEN-03） |
| **GAP-09** | **权限矩阵未在文档层成型，且存在两处刻意不一致**：`work_items` 更新不含 owner 限制、`contexts` 更新含 owner 限制（云版遗留），`team_members.status` 有意收紧 | `README.md:88` 原句「`work_items` 的更新策略**不含**"owner_id 必须是我"：团队成员必须能推进别人建的工单，否则看板流转当场失效」；`README.md:89` 原句「`contexts` 的更新策略**含**该条件…这是云版本原有的形状，如实保留，未顺手"修好"」；`README.md:90`「一处**有意收紧**」；`pyapp/policy.py:227-236` vs `:246-254` vs `:314-319` | 【已验证】 | 产品语义不自洽；若无文档，极易被「统一」掉而破坏看板流转 | 本文 §3.3 逐条还原、§3.5 如实标「存疑」，**不修正** |
| **GAP-10** | **owner 与 admin 在代码里被合并**：`is_admin` 同时接受 `owner`/`admin`，产品描述却说 admin「可代所有者分派任务与维护资源」，两者差异未定义 | `pyapp/policy.py:56-57` `is_admin` 返回 `role in ('owner','admin')`；`pyapp/policy.py:62-63` `is_owner` 仅用于 `team_members` 插入（`:160`）；`js/config.js:77-79` 三者 desc 不同 | 【已验证】 | 权限说明与实际不符；访谈 IT-04 会问到（Q19） | 本文 §3.5 / OPEN-09 |
| **GAP-11** | **「离线可用」边界未定义**：README 承诺「可完全离线」，但 AI 通道需访问外部 LLM；且断网期间的操作、恢复后的数据合并未说明 | `README.md:31`「网络：**可完全离线**。不再加载任何 CDN 资源」；`README.md:80,179-183` 自带密钥 OpenAI 兼容转发、`https://api.openai.com/v1` 默认端点（`pyapp/config.py:97`）；`js/api.js:353-355` 有 `backend-unavailable` 分支 | 【已验证】（两处事实）／【推断】（措辞需收窄为「核心功能离线」） | 用户对「离线」的预期可能被 AI 功能打破 | 本文 §4.9 NFR-01 / OPEN-05 |
| **GAP-12** | **无阶段验收标准文档**：测试极扎实但「什么才算可以上线」没有可勾选、可签字的判定表；§11 是散文式清单 | `README.md:238-253` 只列测试命令与覆盖点；`docs/零停机发布方案.md:464-487` §11 标题即「如实清单」，形式为要点列表；总纲 §2「测试 → 验收断链」 | 【已验证】 | 「全绿」只说明脚本跑了，不说明达到了什么标准 | 本文 §5 三层验收；门禁表归 `07` |
| **GAP-13** | **若干业务字段有存储但无使用规则**：`reported_by` / `source` / `impact` / `urgency` / `sla_due_at` / `responded_at` / `waiting_for` 等出现在表结构，但录入与消费规则未文档化 | `pyapp/db.py:122-126,135,145`；`pyapp/db.py:313-326` COLUMNS 允许写入；前端 `js/views.js:1378-1518` 的 `itemDrawerHtml` 未见统一规则说明（Grep 未发现字段级规则文档） | 【已验证】（字段存在）／【推断】（规则缺失，需 FDS 确认表单是否暴露） | 字段可能被静默忽略，告警来源/影响度无法统计（Q02/Q05/Q14） | 交 `03 FDS` / `05 数据模型` 逐字段定规则 |
| **GAP-14** | **版本串口径不一致**：README 说「三者必须同版本」+ 服务端单列，总纲 §7.2 说「四处同步」；实际存在 4 处物理位置 | `README.md:264-265` 原句「`js/config.js` 的 `APP_VERSION` + 各模块 import 里的 `?v=` + `index.html` 的 css/js 引用，三者必须同版本；服务端版本号在 `pyapp/__init__.py`」；`js/config.js:170`、`pyapp/__init__.py:20` 均为 `20260917a`；`index.html:9,64` 与 `js/app.js:15,16,24,29,33` 的 `?v=20260917a`；总纲 §7.2「四处同步」 | 【已验证】 | 变更单与 G2 门禁引用哪一口径会打架 | 本文 §6.4 统一为「前端 3 处 + 服务端 1 处 = 4 处」，建议总纲/README 对齐（OPEN-07） |
| **GAP-15** | **`--import` 语义危险且未在需求侧声明**：为 INSERT OR REPLACE，不清空不去重，灾难恢复需先删库文件 | 总纲 §7.4 原句「`--import` 是 INSERT OR REPLACE，既不清空也不去重：灾难恢复必须先删库文件再导入」；`README.md:46` 仅写「整库还原」；`test_backup_round_trip` 只验证往返 | 【已验证】 | 误操作导致残留行与新数据叠加，数据可携带承诺被破坏 | 本文 §4.9 NFR-04 / §4.8 EX-021 / `09 运维手册` |
| **GAP-16** | **并发编辑无冲突策略**：`work_items` 无版本号/乐观锁字段，两人同改一张卡片的产品容忍度未定义 | `pyapp/db.py:106-146` 全部列清单（无 `version` / `updated_at` 作为并发校验的文档说明，`updated_at` 为 `stamp` 由服务端维护）；`js/app.js:701` 直接提交 `status` + `updated_at` | 【已验证】（无版本字段）／【推断】（冲突策略未定义，Q17 待访谈） | 后写覆盖前写，运维现场可能丢信息 | 本文 §4.9 NFR-05 / §7.2 并发用例 |
| **GAP-17** | **自助改密整条链路「接口已实现但用户不可达」**：后端端点、API 客户端、提交函数、dispatch 分支齐备，但前端**从未渲染 `#pwd-form`**，用户点「修改密码」按钮无任何反应；46 项集成测试全绿仍漏掉 | `pyapp/httpd.py:824`（`POST /api/auth/password`）；`js/api.js:139` `changePassword()`；`js/app.js:1439-1451` `submitPassword()` 读 `$('#pwd-old')` / `$('#pwd-new')`；`js/app.js:868` `case 'submit-password'`；`js/views.js:1895` 「修改密码」按钮；`js/app.js:863-866` `case 'change-password'` 仅 `if (form) form.hidden = !form.hidden`，`form` 为 null 时静默跳过；**全 `js/` Grep `pwd-form\|pwd-old\|pwd-new\|submit-password` 仅 6 处命中且全为读取/分支，零处渲染** | 【已验证】 | 用户无法自助改密，只能靠 `--set-password` CLI（README.md:47）；「接口测过 ≠ 功能可达」，且直接暴露验收缺口 | 本文 §4.3 REQ-036 / §4.7 BR-026 / §4.8 EX-022 / §5.1 AC-F-36；回归范围须由 `07` 补 |
| **GAP-18** | **写表单无防重复提交**：全局 click 委托为 async 且无 busy 锁；`save-new-item` 先 `closeModal()` 再 `await doCreateItem()`，而 `closeModal()` 180ms 后才清空 DOM，按钮在此期间仍可点 | `js/app.js:503-518` `bindGlobal` 的 `document.addEventListener('click', async ...)` 直接 `await dispatch(...)`、无任何锁；`js/app.js:670-674` `closeModal()` 位于 `await doCreateItem()` 之前；`js/app.js:391-395` `closeModal` 用 `setTimeout(..., 180)` 才清空 `innerHTML`；`css/app.css:775-783` 未设 `pointer-events:none`（此条 CSS 行号引自前端实测转交，本次未逐行复核） | 【已验证】（前三处本次 Read 核实） | 连点可重复插入 `work_items` / 重复建团队；是「无验收标准」的直接后果 | §4.3 REQ-037 / BR-027 / EX-023 / AC-F-37 |
| **GAP-19** | **「不能假装成功」纪律有一处缺口**：`addProgress` 更新工作项那一步未校验受影响行数即提示成功，与 README 明确要求相悖 | `js/app.js:1030-1051` `addProgress`：新增进度日志有 `if (!assertAffected(rows,'进度记录')) return`（`:1038`），但 `:1046` `await updateItem(id, patch)` **无校验**，紧接 `:1047` `toast('进度已写入')`；`js/app.js:347` `assertAffected` 定义，全量 Grep 共 **定义 1 处 + 调用 23 处**（`:690`~`:1490`），仅此处漏检；`README.md:87` 原句「更新/删除被权限拦下时返回空数组而不是报错 —— 前端靠'空数组 = 没改到'如实提示用户」 | 【已验证】 | 权限拦下时用户看到「进度已写入」但数据未变，直接违背产品纪律 | §4.3 REQ-038 / BR-028 / EX-024 / AC-F-38 |
| **GAP-20** | **跨视图筛选串味**：`case 'nav'` 只清 `state.search` 不清 `state.filters`，多个视图共用扁平 `filters.itemType`，团队空间点过「周期巡检」后切到「服务台」列表假空并显示「队列已清空」 | `js/app.js:603-609` `case 'nav'`：仅 `state.view = ds.view` + `state.search = ''`，**无 `state.filters` 重置**；`js/views.js:812` `teamFilterTabs` 与多视图共用 `state.filters` 键（「6 个视图共用 `itemType`」计数引自前端实测转交） | 【已验证】（`case 'nav'` 本次 Read 核实） | 用户误判「数据丢了」，是最容易被当成功能故障的交互缺陷 | §4.3 REQ-039 / BR-029 / EX-025 / AC-F-39 |

> **v1.1 新增证据来源与抽验结论**：GAP-17~20 来自前端工程师端木灵的实测转交，本文已逐条 `Read` / `Grep` 抽验行号；§8.1 的 6 个产品语义问题来自后端工程师寇豆码实测转交，同样已逐条抽验。**一处数字按实测修正**：转交称 `assertAffected` 共 21 处调用，本次全量 Grep 实为「定义 1 处 + 调用 23 处」，但「仅 addProgress 一处漏检」的结论仍成立。凡「计数 / CSS 行号」类转交而本次未复核者，已在该行证据中显式标注来源，不冒充本次实读。GAP-17~20 共同支撑一条元结论：**没有 PRD / 没有验收标准，功能完整性与可达性无人把关**（接口测试与 46 项集成测试全绿仍漏掉改密断链）。

### 2.3 证据边界声明（明确「本次未验证」的部分）

| 项 | 状态 | 说明 |
|---|---|---|
| 49 项接口测试 / 9 项零停机 / 80+65+46 前端冒烟「本机全绿」 | **【未验证】本次未重跑** | 测试**存在性与数量**已由 `README.md:241-245`、`docs/零停机发布方案.md:468`、49 个 `def test_` 名称（Grep）验证；但「本机全绿」来自上游任务上下文与文档转述，本次会话未执行任何测试命令。本文引用测试名作为**验收依据的锚点**，不宣称已跑通 |
| `docker build` 是否成功、Caddy 指令实跑、Linux 写锁行为 | 【未验证】 | `docs/零停机发布方案.md:475-487` 已如实列为「尚未验证」；总纲 §1 把 `test_zerodowntime.py` 列为唯一阻塞项 |
| 真实用户访谈 / 场景频次 / 痛点强度 | 【未验证】 | 本次为反向补课，未接触真实运维团队。§1 是**可执行的调研方案**，§1.4 频次列为「估计」，不作为需求依据（§1.5 规定「推测」不计入依据） |
| WIP 硬约束 / SLA 自动化 / 状态转换约束「是否有意不做」 | 【推断】 | 代码事实【已验证】，但「这是产品决策还是遗漏」无文档证据，故一律进 §8 开放问题交主理人裁决，**不擅自补需求、不改代码** |
| `03`~`09` 文档内容 | 【未验证】 | 由其他成员并行产出，本文只引用总纲已定规则，不预设其内容 |

---

## 3. 角色与权限矩阵

> 本节从 `pyapp/policy.py` **逐条还原**，满足总纲 §4 G0 门禁「权限矩阵与 `policy.py` 逐条对齐」。所有「代码如此但产品语义存疑」的点集中在 §3.5 如实呈现，**不擅自修正**。前端的显示层判断不构成边界（`pyapp/policy.py:5-7` 明确「不能下沉到前端」）。

### 3.1 角色定义

| 角色键 | 名称 | 产品描述（`js/config.js:76-80`） | 代码判定 | 数据范围 |
|---|---|---|---|---|
| `owner` | 所有者 | 团队空间管理者：成员、资源、项目汇总 | `is_owner()`（`policy.py:62-63`）、`is_admin()` 亦为真 | 本团队成员范围 + 自己是 owner 的团队 |
| `admin` | 管理员 | 可代所有者分派任务与维护资源 | `is_admin()` 与 owner **合并**（`policy.py:56-57`） | 同上（代码未区分） |
| `member` | 成员 | 承接任务、更新进度、参与复盘 | `is_member()`（`policy.py:53-54`） | 本团队成员范围 |
| （非成员） | — | — | `is_member` 为假 → 团队数据不可见/不可写 | 仅本人个人数据 |
| （停用成员） | — | 「停用即失去访问权限」 | `status != 'active'` 跳过，`load_context`（`policy.py:314-319`） | 视为非成员（有意收紧） |
| （未登录） | — | — | 仅可写 `app_logs`（`policy.py:212-215`） | 匿名日志，仅本人可读 |

> 另有「实例级管理员」概念：`--no-signup` / `--set-password`（`README.md:42,47,148`），属运行配置而非 `ROLES` 枚举，本文不作为团队角色列入（见 OPEN-09）。

### 3.2 资源清单与作用域

`TEAM_KEY`（`pyapp/policy.py:32-43`）定义了 11 张业务表的团队键；`teams` 用自身 `id`，其余用 `team_id`。`WRITABLE_TABLES`（`pyapp/db.py:378`）为这 11 张；`users` / `sessions` / `settings` **永不开放通用 CRUD**（`pyapp/db.py:377`）。

| 资源（表） | 团队键 | 可含个人数据（`team_id IS NULL`） | 说明 | 证据 |
|---|---|---|---|---|
| `teams` | `id` | 否 | 团队本体 | `policy.py:33`、`db.py:71-88` |
| `team_members` | `team_id` | 否 | 成员关系 | `policy.py:34`、`db.py:90-104` |
| `work_items` | `team_id` | **是**（双域共用） | 工作项主表 | `policy.py:35`、`db.py:106-149`、`README.md:85` |
| `projects` | `team_id` | 否 | 项目 | `db.py:151-169` |
| `areas` | `team_id` | 否 | 责任领域 | `db.py:171-185` |
| `contexts` | `team_id` | **是** | 情境 | `db.py:187-197` |
| `resources` | `team_id` | 否 | 资源/知识库/工具台账 | `db.py:199-219` |
| `progress_logs` | `team_id` | **是** | 进度日志 | `db.py:221-235` |
| `reviews` | `team_id` | **是** | 复盘 | `db.py:237-252` |
| `on_call` | `team_id` | 否 | 值班排班/交接 | `db.py:254-270` |
| `app_logs` | （`owner_id`） | — | 运行日志，只写不改删 | `db.py:272-284`、`policy.py:208-221` |

### 3.3 权限矩阵（角色 × 资源 × 增 / 查 / 改 / 删）

图例：`✓` 允许；`✓*` 允许但带附加条件（见「条件」列）；`—` 禁止；`本人` 仅 `owner_id == uid` 的行；`成员` = 该 `team_id` 的 active 成员；`管理` = `is_admin`（owner 或 admin）；`O` = 团队 owner。

| 资源 | 操作 | 非成员 | member | admin | owner | 条件 / 谓词原件 | 证据 |
|---|---|---|---|---|---|---|---|
| `teams` | 查 | — | ✓ | ✓ | ✓ | `owner_id == uid` **或** `is_member(id)` | `policy.py:138-139` |
| `teams` | 增 | ✓* | ✓* | ✓* | ✓* | `owner_id == uid`（创建者归属必须本人；`create_team_with_owner` 原子登记 owner） | `policy.py:141-142`、`store.py:166`、`test_10` |
| `teams` | 改 | — | — | ✓ | ✓ | `owner_id == uid` **或** `is_admin(id)` | `policy.py:144-145` |
| `teams` | 删 | — | — | — | ✓* | **仅** `owner_id == uid`（注意：非 admin 语义，是「这行归属我」） | `policy.py:147-148` |
| `team_members` | 查 | 本人 | ✓ | ✓ | ✓ | `user_id == uid` **或** `is_member(team_id)` | `policy.py:154-155` |
| `team_members` | 增 | — | — | ✓ | ✓ | `is_admin` **或** `is_owner` | `policy.py:157-160` |
| `team_members` | 改 | — | 本人* | ✓ | ✓ | using：`is_admin` 或本人行；check：非管理**只能改自己那一行且不得改变自己的角色**（防提权） | `policy.py:162-170`、`test_18_member_cannot_self_promote` |
| `team_members` | 删 | — | 本人* | ✓* | ✓* | `role != 'owner'`（**owner 行不可移出**）且（`is_admin` 或本人行） | `policy.py:172-176`、`test_19_admin_can_manage_members_but_owner_row_is_protected` |
| `work_items` | 查 | 本人 | 成员 | 成员 | 成员 | `team_id IS NULL → owner_id == uid`；非空 → `is_member` | `policy.py:79-85,228` |
| `work_items` | 增 | 本人* | ✓* | ✓* | ✓* | `owner_id == uid` 且（`team_id IS NULL` 或 `is_member`）；`owner_id` 客户端值一律丢弃 | `policy.py:106-118,229`、`db.py:581-582`、`test_17_owner_id_cannot_be_forged` |
| `work_items` | 改 | 本人 | **成员（不限创建者）** | 成员 | 成员 | using/check 均为 `_personal_or_member`，**不含 owner 限制**（刻意，为看板流转） | `policy.py:230-236`、`README.md:88` |
| `work_items` | 删 | 本人 | 成员 | 成员 | 成员 | `_personal_or_member`（成员可删他人建的团队工单） | `policy.py:235` |
| `projects` | 查/增/改 | — | 成员 | 成员 | 成员 | `_member_only` / `_member_update` | `policy.py:237-240` |
| `projects` | 删 | — | 本人* | ✓* | ✓* | `is_admin` **或** `owner_id == uid` | `policy.py:94-99,239` |
| `areas` | 查/增/改 | — | 成员 | 成员 | 成员 | `_member_only` / `_member_update` | `policy.py:241-245` |
| `areas` | 删 | — | — | ✓ | ✓ | **仅** `is_admin` | `policy.py:244` |
| `contexts` | 查 | 本人 | 成员 | 成员 | 成员 | `_personal_or_member` | `policy.py:247` |
| `contexts` | 增 | 本人* | ✓* | ✓* | ✓* | `_own_writable` | `policy.py:248` |
| `contexts` | 改 | 本人 | **成员但 check 限创建者** | 同左 | 同左 | using=`_personal_or_member`，check=`_own_writable`（**含 owner 限制，云版遗留**） | `policy.py:249-252`、`README.md:89` |
| `contexts` | 删 | 本人 | 成员 | 成员 | 成员 | `_personal_or_member`（与改不一致） | `policy.py:253` |
| `resources` | 查/增/改 | — | 成员 | 成员 | 成员 | `_member_only` / `_member_update` | `policy.py:255-258` |
| `resources` | 删 | — | 本人* | ✓* | ✓* | `is_admin` 或 `owner_id == uid` | `policy.py:257` |
| `progress_logs` | 查 | 本人 | 成员 | 成员 | 成员 | `_personal_or_member` | `policy.py:189` |
| `progress_logs` | 增 | 本人* | ✓* | ✓* | ✓* | `_own_writable` | `policy.py:190` |
| `progress_logs` | 改 | 本人 | 本人 | 本人 | 本人 | using/check 均 `owner_id == uid`（**管理员也不能改他人日志**） | `policy.py:182-183,191` |
| `progress_logs` | 删 | 本人 | 本人* | ✓* | ✓* | `owner_id == uid` **或** `is_admin` | `policy.py:185-186`、`test_21_progress_log_delete_requires_ownership_or_admin` |
| `reviews` | 查/增 | 本人 / 成员 | 成员 | 成员 | 成员 | `_personal_or_member` / `_own_writable` | `policy.py:200-202` |
| `reviews` | 改/删 | 本人 | 本人 | 本人 | 本人 | **仅** `owner_id == uid`（他人复盘不可改） | `policy.py:196-205`、`test_27_review_of_others_is_not_updatable` |
| `on_call` | 查/增/改 | — | 成员 | 成员 | 成员 | `_member_only` / `_member_update` | `policy.py:261-264` |
| `on_call` | 删 | — | — | ✓ | ✓ | **仅** `is_admin` | `policy.py:264` |
| `app_logs` | 查 | 本人 | 本人 | 本人 | 本人 | `owner_id == uid`（含未登录写入的空归属？见注） | `policy.py:209-210` |
| `app_logs` | 增 | ✓*（未登录亦可） | ✓* | ✓* | ✓* | `owner_id in (uid, None, '')`，不允许替他人写 | `policy.py:212-215`、`test_28_logs_accept_anonymous_but_only_owner_can_read` |
| `app_logs` | 改/删 | **—** | **—** | **—** | **—** | 全 `deny`（避免掩盖故障证据） | `policy.py:217-221` |

> 注：`app_logs` 的「未登录可写」是刻意设计（`policy.py:213` 注释「让登录前就失败的场景也能留下日志」），但查策略只看 `owner_id == uid`，因此**匿名写入的日志没有任何登录账号能读到**——这是一个需要产品确认的可观测性缺口（见 §7.5 监控点、OPEN-10）。
> **统一语义**：查询/更新/删除「看不见」**不抛异常**、返回空结果（`policy.py:13-14,274-284` 的 `Denied` 仅用于「整个操作根本不可能成立」，如往非成员团队插数据）；前端靠 `assertAffected` 把「空数组」翻译成「可能不存在或不在你可见的范围内」（`README.md:87`）。

### 3.4 数据可见范围（横向 / 纵向）

| 维度 | 规则 | 证据 |
|---|---|---|
| 纵向（角色） | 后端 `can_select/can_insert/can_update/can_delete` 逐行判定，前端不设边界 | `policy.py:5-7,287-301`、`README.md:78` |
| 横向（数据归属） | 个人数据 `team_id IS NULL` 仅 `owner_id == uid`；团队数据仅 active 成员 | `policy.py:79-85`、`README.md:85,90` |
| ID 枚举防护 | 客户端提交 `owner_id` / `id` / `created_at` / 未知字段一律丢弃 | `db.py:565-591`、`test_17`、`test_22_unknown_fields_are_dropped` |
| 停用成员 | `status != 'active'` 在 `load_context` 阶段即被剔除，且 `test_20` 验证「不可重新加入」 | `policy.py:314-319`、`README.md:90` |
| 归档团队 | `archived` 团队不可被加入 | `test_13_archived_team_cannot_be_joined`、`js/app.js:1207-1218` `archiveTeam` |
| 系统表 | `users` / `sessions` / `settings` 永不开放通用 CRUD | `db.py:377-378` |
| 跨团队 | 一个用户可属多个团队，但**每次请求只按当前 `team_id` 判定**，无跨团队聚合权限 | `policy.py:32-43`、`js/views.js:1945-1950` |

### 3.5 「代码如此但产品语义存疑」如实登记表

> 总纲 §2 / §8 要求把这些「刻意保留」显式化。下表**只呈现，不修正**；是否变更一律走 §6 变更单与 §8 开放问题。

| 编号 | 代码事实 | 证据 | README / 注释的口径（原文） | 产品语义存疑点 | 处置建议 |
|---|---|---|---|---|---|
| S-01 | `work_items` 更新/删除**不含** `owner_id == uid` 限制，任何成员可推进/删除他人建的团队工单 | `pyapp/policy.py:230-236` | `README.md:88`「团队成员必须能推进别人建的工单，否则看板流转当场失效」；`policy.py:230-233` 注释「这里照抄不改」 | 与「我的数据我负责」直觉冲突；成员可删他人工作项是否过宽？ | **保留**，理由已充分；在 `04 TDS` 标注「改动前必读」 |
| S-02 | `contexts` 更新 **check 含** `owner_id == uid`（非创建者改不动团队情境），但 **delete 不含**（成员可删） | `pyapp/policy.py:249-253` | `README.md:89`「这是云版本原有的形状，如实保留，未顺手"修好"」；`policy.py:249-251` 注释「差异本身就是需要被记录的事实」 | 同一张表「改」比「删」更严，逻辑不自洽；且团队情境本应共享 | **如实保留**；是否在下一版统一交用户裁决（OPEN-04） |
| S-03 | `work_items` 与 `contexts` 同为双域表，更新策略形状却不同 | `policy.py:227-254` 对照 | `README.md:88-89` 并列陈述 | 无文档时最容易被「顺手统一」而破坏 S-01 | 本文登记 + `05` 数据模型注明 |
| S-04 | `team_members.status != 'active'` 视为非成员（与云版不同） | `pyapp/policy.py:17-21,314-319` | `README.md:90`「一处**有意收紧**…与'停用即失去访问权限'的产品语义相矛盾。这里按后者执行」 | 与「云版语义对齐」的总体原则相悖，属明确例外 | **保留**；`04` 列为有意差异清单 |
| S-05 | `is_admin` 把 `owner` 与 `admin` 合并 | `pyapp/policy.py:56-57` | `js/config.js:77-78` 两者 desc 明显不同 | 「管理员可代所有者」在代码里没有可区分的能力边界 | 交用户裁决是否细分（OPEN-09）；本文不擅自定义新权限 |
| S-06 | 被权限拦下返回**空数组**而非报错 | `pyapp/policy.py:13-14,274-284` | `README.md:87`「前端靠'空数组 = 没改到'如实提示」 | 「无权限」与「不存在」对用户界面表现相同，排障困难 | **保留**（安全上避免信息泄露）；在 `06 接口契约` 明确该语义 |
| S-07 | `app_logs` 匿名可写但无人可读 | `pyapp/policy.py:209-215` | `policy.py:213`「让'登录前就失败'的场景也能留下日志」 | 登录失败排障证据落库却查不到 | 建议产品确认是否需要实例管理员只读视图（OPEN-10） |
| S-08 | `reviews` 改/删仅创建者，`progress_logs` 改仅创建者但删允许 admin | `pyapp/policy.py:182-205` 对照 | `test_21` / `test_27` | 同为「他人记录」，删除口径不一致 | 登记；`04` 说明是否为刻意 |
| S-09 | 客户端提交的 `owner_id` **一律丢弃**，归属由服务端注入 | `pyapp/db.py:565-591` `split_payload`；`pyapp/policy.py:15,106-118`；`test_17_owner_id_cannot_be_forged` | `README.md:86` 原句「写入**永不**携带 `owner_id`：客户端提交的 `owner_id` 一律丢弃，归属由服务端注入」 | 无（安全边界，语义明确） | **保留**；属 README:83 所列「刻意保持一致的几处」之一 |

#### README.md:87-90 五处刻意取舍 · 对照表（v1.1 补，逐字对应，不建议修正）

> 主理人要求「如实呈现，不要建议修正」。下表把 `README.md:83-90` 列明的 5 处权限语义逐一落到代码与本文登记点，**全部标注为刻意保留**；如需变更，一律走 §6 变更单 + §8 裁决，不在本文或下游文档里「顺手修好」。

| # | README 原句（位置） | 代码事实 | 本文登记点 | 状态 |
|---|---|---|---|---|
| 1 | 「`work_items` 的更新策略**不含**"owner_id 必须是我"：团队成员必须能推进别人建的工单，否则看板流转当场失效」（`README.md:88`） | `pyapp/policy.py:230-236` using/check 均为 `_personal_or_member`，注释 `:230-233`「这里照抄不改」 | S-01 / BR-005 | **刻意保留** |
| 2 | 「`contexts` 的更新策略**含**该条件（非创建者改不动团队情境）—— 这是云版本原有的形状，如实保留，未顺手"修好"」（`README.md:89`） | `pyapp/policy.py:249-252` using=`_personal_or_member`、check=`_own_writable`；注释 `:249-251`「差异本身就是需要被记录的事实」 | S-02 / S-03 / BR-006 | **刻意保留**（是否统一见 OPEN-04，本文不建议修正） |
| 3 | 「更新/删除被权限拦下时**返回空数组而不是报错**」（`README.md:87`） | `pyapp/policy.py:13-14,274-284`；`pyapp/httpd.py:651-652`「被权限拦下时 rows 为空数组…不会假装成功」 | S-06 / BR-004 / EX-016 | **刻意保留**；注意 GAP-19 是这条纪律在前端的一处漏检，属实现缺陷而非语义变更 |
| 4 | 「写入**永不**携带 `owner_id`：客户端提交的 `owner_id` 一律丢弃」（`README.md:86`） | `pyapp/db.py:581-582`；`pyapp/policy.py:15,106-118` | S-09 / BR-002 | **刻意保留** |
| 5 | 「一处**有意收紧**：`team_members.status != 'active'` 视为非成员」（`README.md:90`） | `pyapp/policy.py:17-21,314-319`；`test_20` | S-04 / BR-007 | **刻意保留**（与「云版语义对齐」总体原则的明确例外） |

> 另：`README.md:85` 的「双域共用一张 `work_items`：`team_id IS NULL` 是个人数据」亦为刻意设计，已落在 §3.4 / BR-001，一并保留。

### 3.6 权限风险点（交 `08 安全审计` 复核）

| 风险 | 触发条件 | 现有防线 | 证据 |
|---|---|---|---|
| 前端误当边界 | 只在视图里隐藏按钮 | `policy.py` 后端逐行校验 | `policy.py:5-7` |
| 归属伪造 | 客户端提交 `owner_id` | `split_payload` 丢弃 + `_own_writable` | `db.py:581-582`、`test_17` |
| 自行提权 | 成员改自己 `role` | `update_check` 校验 `role == ctx.role_of(tid)` | `policy.py:165-170`、`test_18` |
| 停用成员残留访问 | 旧会话仍持有成员关系 | `load_context` 每次请求重读 active | `policy.py:304-319`、`test_20` |
| 跨站写 / CORS | 第三方页面带 Cookie 调接口 | 不开 CORS + `Content-Type` + `Origin` + `SameSite=Lax` 三重 | `README.md:96-97`、`test_32`~`test_36` |
| XFF 伪造绕过登录限流 | 直连公网且误开 `GTD_TRUST_PROXY` | 默认关闭该开关（`pyapp/config.py:92`） | `README.md:102-104,135-145`、`test_40` |

---

## 4. PRD 主体

### 4.1 项目背景、目标用户与核心价值

| 项 | 内容 | 证据 |
|---|---|---|
| 项目定位 | 面向**小型 IT 运维团队**的多团队 GTD 工作台；团队共享空间派活/巡检/值班/资源台账，个人空间以 GTD 五步跟进本人工作 | `README.md:3-4`、`index.html:7` |
| 方法论 | 三层融合：ITIL 轻量票种（事件/请求/变更/问题）+ Kanban（显式流程、WIP）+ GTD 五步 | `README.md:5`、`js/config.js:10-15` |
| 目标用户 | 1~2 名 owner、若干值班/普通成员的小型运维团队（非大组织、非 SaaS 多租户） | `js/config.js:75`「所有者 1~2 人」 |
| 核心价值 | 把「工作怎么流转」与「注意力怎么分配」放进同一份数据，团队派活自动出现在个人清单 | `js/views.js:2052-2056` `renderOnboarding` 原文 |
| 部署形态 | Python 3 标准库单体（零第三方依赖）+ 原生 ESM 前端 + SQLite 单文件；可选 OpenAI 兼容 LLM | `README.md:7-8,25-31` |
| 本次文档性质 | 存量反向补课：把代码已实现功能反推为需求基线 | 总纲 §8 |

### 4.2 MVP 范围（三档，每条带 §1.6 复算总分）

| 档 | 范围 | 理由 / 复算 | 证据 |
|---|---|---|---|
| **必须做（M）** | REQ-001~035 中标记 M 的「已兑现」基线：账号与权限、双域工作项、看板、GTD 五步、值班/巡检/复盘、备份、发布门禁 | 这些是**已经上线运行的能力**，本期不是重新实现，而是补规格与验收；缺任何一条即 G0 不通过 | §2.1 DD-01~DD-14 |
| **必须做（M，文档层）** | 需求编号体系、角色权限矩阵、BR/EX 表、三层验收标准、变更管控 | 总纲 §4 G0 门禁的必交产出 | 总纲 §4 G0 行 |
| **可延后（S）** | WIP 硬约束、SLA 自动计算与超时升级、状态转换约束、变更窗口约束、交接单结构化、跨团队汇总视图 | 均需产品裁决语义后才能定规格；按 §1.6 复算多为 4~7 分（S/C），且会改动前端 `dispatch` 与可能的后端逻辑 | GAP-04/07/08、Q06/Q07/Q10/Q12/Q15 |
| **明确不做（W）** | 多租户 SaaS 化、外部 CDN/构建链、微服务、强制流式 AI、邮件验证码注册、目录列表、CORS 开放、真机浏览器自动化（本期） | 均破坏「零依赖 / 单服务器 / 离线 / 本地自持」约束，§1.6 C2 记 -3；或 README 已明确否决 | `README.md:7-8,73-81,96,101`、`docs/零停机发布方案.md:475-487` |
| **本次新增（v1.2，M）** | API / MCP 开放能力的安全底座：本地 REST API 服务（REQ-045~047）、开放接口审计（REQ-049）、内置 MCP 服务与只读工具集（REQ-050~051）、零依赖约束不回退（REQ-054）、配置可达性（REQ-055） | 主理人明确要求新增；§1.6 复算 K1(+3，明确指令+安全边界) + K2(+2) + K3(+1) + R1(+3) + R2(+2) + C1(0，复用现有 handler) + C2(0，进程内标准库) = **11 → M** | 本次用户指令；Mindwtr Local API / MCP 官方文档 |
| **本次新增（v1.2，S）** | API 安全写入（REQ-048）、MCP 写工具集与分级授权（REQ-052）、MCP 安全写入（REQ-053） | 参照实现以只读为默认、写能力需显式授权；§1.6 复算 K2(+2) + K3(+1) + R1(+3) + R2(+2) + C1(-2，>2 人日) + C2(0) = **6 → S** | Mindwtr MCP §Permissions and port / §Read Tools |
| **本次新增（明确不做 W）** | 独立 npm/Bun/npx MCP helper、Mindwtr Cloud 后端模式、远程隧道 / 公网暴露本地 API 或 MCP | 引入 Node 运行时或第三方包破坏「零依赖 / 离线 / 本地自持」（§1.6 C2 记 -3）；公网暴露使令牌与团队数据面临越权风险，与 REQ-047 直接冲突 | `README.md:7-8,25-31`、REQ-033、REQ-054 |

> 复算示例见 §1.6。**任何把「可延后」提升为「必须做」的动作，必须走 §6 变更单**，避免范围蔓延。

### 4.3 功能清单（需求基线，按域分组，带 REQ-ID）

优先级列按 §1.6 判定；「实现证据」列满足总纲 §5.3「一处实现」；「验收」列指向 §5。

**团队域（TEAM）**

| REQ-ID | 功能 | 优先级 | 关联页面 | 实现证据 | 关联 BR / EX | 验收 |
|---|---|---|---|---|---|---|
| REQ-001 | 创建团队并原子登记创建者为 owner | M | 团队引导 / 新建团队表单 | `pyapp/store.py:166`、`js/app.js:783,785`、`test_10` | BR-002 / EX-009 | AC-F-01 |
| REQ-002 | 凭邀请码加入团队（大小写不敏感唯一、归档团队不可加入） | M | 加入团队表单 | `pyapp/store.py:208`、`pyapp/db.py:86-87`、`test_11`~`test_13` | BR-015 / EX-010 | AC-F-02 |
| REQ-003 | 成员与角色管理（增删改、防自提权、owner 行受保护） | M | `members` | `pyapp/policy.py:153-178`、`js/app.js:796-818`、`test_18`~`test_19` | BR-008 / EX-009 | AC-F-03 |
| REQ-004 | 成员停用即失去访问权且不可自行重新加入 | M | `members` | `pyapp/policy.py:314-319`、`README.md:90`、`test_20` | BR-007 / EX-011 | AC-F-04 |
| REQ-005 | 登记团队工作项（7 类票种、优先级、泳道、指派、责任人） | M | `desk` / `routine` / 多入口新建 | `pyapp/db.py:106-149`、`js/config.js:21-29`、`js/app.js:654-677` | BR-014 / EX-012 | AC-F-05 |
| REQ-006 | 看板：7 列显式流程 + 双泳道 + WIP 上限显示与配置 | M | `board` / `teamSettings` | `js/views.js:918-957,1315-1356`、`js/app.js:1191-1205`、`test_25` | BR-011 / BR-012 / EX-016 | AC-F-06 |
| REQ-007 | 服务台 / 日常工作视图 | M | `desk` / `routine` | `js/views.js:861-917` | BR-014 | AC-F-07 |
| REQ-008 | 团队总览与指标告警（积压、受阻、WIP 触顶提示） | M | `overview` | `js/views.js:145-221,817-860` | BR-012 | AC-F-08 |
| REQ-009 | 项目管理（建设型/改善型、负责人、进度、截止） | M | `projects` / `myProjects` | `pyapp/db.py:151-169`、`js/views.js:960-1000` | BR-024 | AC-F-09 |
| REQ-010 | 值班排班与交接（4 班次、主/备、交接单模板） | M | `oncall` | `pyapp/db.py:254-270`、`js/config.js:107-112,152-159`、`js/views.js:1001-1050` | BR-024 | AC-F-10 |
| REQ-011 | 资源与知识库 / 工具台账（6 类、复核到期、置顶、归档） | M | `resources` / `tools` | `pyapp/db.py:199-219`、`js/config.js:83-90`、`js/views.js:1051-1130` | BR-024 | AC-F-11 |
| REQ-012 | 报表与一页纸汇总 | M | `reports` | `js/views.js:1131-1231` | — | AC-F-12 |
| REQ-013 | 团队复盘（同周期唯一、他人不可改） | M | `teamReview` | `pyapp/db.py:237-252`、`pyapp/store.py:258`、`test_26`~`test_27` | BR-009 / EX-019 | AC-F-13 |
| REQ-014 | 团队设置（WIP 上限、邀请码轮换、团队归档） | M | `teamSettings` | `js/app.js:788-793,1207-1218`、`js/views.js:1315-1356` | BR-012 / BR-015 | AC-F-14 |
| REQ-015 | 责任领域与情境（默认 7 领域 / 7 情境） | M | `contexts` / 团队设置 | `js/config.js:118-137`、`pyapp/db.py:171-197` | BR-006 / S-02 | AC-F-15 |
| REQ-016 | 周期巡检清单模板与自动派生下一次 | M | `routine` / 工作项抽屉 | `js/config.js:140-149`、`js/app.js:691-694` | BR-016 / EX-019 | AC-F-16 |

**个人域（PERSONAL）**

| REQ-ID | 功能 | 优先级 | 关联页面 | 实现证据 | 关联 BR / EX | 验收 |
|---|---|---|---|---|---|---|
| REQ-017 | 快速收集（顶栏输入框，回车入收集箱） | M | 全局顶栏 | `index.html:31-34`、`js/app.js`（quick capture） | BR-011 | AC-F-17 |
| REQ-018 | GTD 五步 12 个清单视图（收集/厘清/执行/下一步/等待/日程/将来也许/已完成…） | M | 个人空间 12 页 | `js/views.js:1907-1920`、`js/views.js:2017-2048` | BR-011 / BR-001 | AC-F-18 |
| REQ-019 | 「团队分配给我的」：按 `assignee_id` 聚合团队工作项 | M | `assigned` | `js/views.js:100,643-670`、`README.md:2055`（renderOnboarding 说明） | BR-001 | AC-F-19 |
| REQ-020 | 个人项目与个人情境维护 | M | `myProjects` / `contexts` | `js/views.js:671-733` | BR-006 | AC-F-20 |
| REQ-021 | 个人复盘 | M | `review` | `js/views.js:734-780`、`pyapp/db.py:237-252` | BR-009 | AC-F-21 |
| REQ-022 | 工作项抽屉编辑 + 进度日志（新增/删除受权限约束） | M | 抽屉 / 时间线 | `js/views.js:1378-1518,442-464`、`js/app.js:720`、`test_21` | BR-010 / EX-016 | AC-F-22 |
| REQ-023 | 到期 / SLA 风险的前端提示 | M | 多处 chip | `js/views.js:111-128,307-325` | BR-013 | AC-F-23 |
| REQ-024 | 完成工作项并自动派生下一次周期任务 | M | 列表 / 看板 | `js/app.js:678-696` | BR-016 | AC-F-24 |

**系统域（SYSTEM）**

| REQ-ID | 功能 | 优先级 | 关联页面 | 实现证据 | 关联 BR / EX | 验收 |
|---|---|---|---|---|---|---|
| REQ-025 | 本地账号：注册 / 登录 / 登出 / 改密（PBKDF2、会话散列、限速、改密踢会话） | M | 认证页 | `README.md:75-76,102,105`、`test_03`~`test_09` | BR-017 / BR-018 / EX-003~008 | AC-F-25 / AC-A-01 |
| REQ-026 | 行级权限与双域隔离（后端逐行判定） | M | 全局 | `pyapp/policy.py:79-85,224-267`、`test_14`~`test_17` | BR-001 / BR-003 / EX-009 | AC-F-26 / AC-A-14 |
| REQ-027 | 归属不可伪造 + 字段白名单 + 类型归一化 | M | 全局 | `pyapp/db.py:565-591`、`test_17`,`test_22`~`test_24` | BR-002 / BR-020 | AC-A-17 / AC-A-22 |
| REQ-028 | 自助注册开关与注册口令 | M | 认证页 / CLI | `pyapp/config.py:80-85`、`README.md:42-43,147-148` | EX-014 | AC-F-28 |
| REQ-029 | 写操作双重校验 + 不开 CORS + 静态白名单 + 目录穿越拦截 | M | 全局 | `README.md:96-100`、`pyapp/config.py:22`、`test_29`~`test_36` | BR-019 / EX-013 / EX-020 | AC-A-32 / AC-A-33 |
| REQ-030 | 运行日志 `app_logs`（只写不改删、匿名可写、仅本人可读） | M | 全局 | `pyapp/policy.py:208-221`、`pyapp/db.py:272-284`、`test_28` | BR-010 / EX-018 | AC-A-28 |
| REQ-031 | 整库导出 / 导入、数据目录可外挂、幂等建库 | M | CLI / `/api/export` | `README.md:45-46,56-59`、`pyapp/db.py:425-487`、`test_backup_round_trip` | BR-022 / EX-021 | AC-F-31 / AC-A-37 |
| REQ-032 | 可选 AI 通道（配置/模型列表/对话，密钥只服务端、不回显、未配置有明确错误码） | S | 设置 / AI 入口 | `README.md:175-192`、`pyapp/config.py:94-120`、`test_38`~`test_39` | BR-023 / EX-015 | AC-A-38 |
| REQ-033 | 离线可用、零第三方依赖、Python 标准库单体 | M | 全局 | `README.md:7-8,25-31,81`、`pyapp/__init__.py:17` | BR-021(见注) | AC-R-01 |
| REQ-034 | 蓝绿零停机发布 + 构建标识核对 + 冒烟/探测门禁 | M | 部署 | `README.md:108-152`、`docs/零停机发布方案.md:464-487`、`test_zerodowntime.py` 9 项 | BR-025 | AC-R-02~06 |
| REQ-035 | 版本串四处同步 | M | 全局 | `README.md:264-265`、`js/config.js:170`、`pyapp/__init__.py:20`、`index.html:9,64`、`js/app.js:15,16,24,29,33` | BR-025 | AC-R-07 |
| **REQ-036** | **已实现功能必须用户可达**：凡后端已提供端点 + 前端已提供 API 客户端与 dispatch 分支的能力，界面必须存在可触发的入口与表单（当前反例：自助改密） | **M** | 认证页 / 用户面板 | `pyapp/httpd.py:824`、`js/api.js:139`、`js/app.js:868,1439-1451`、`js/views.js:1895`（入口在、表单缺） | BR-026 / EX-022 | AC-F-36 |
| **REQ-037** | **写操作必须防重复提交**：一次用户意图只产生一次写入；等待期间界面不得接受第二次同类提交 | **M** | 全部模态表单 | `js/app.js:503-518,670-674,391-395` | BR-027 / EX-023 | AC-F-37 |
| **REQ-038** | **写结果必须如实反馈**：任何 update/delete 必须校验受影响行数，未改到不得提示成功 | **M** | 全部写操作 | `js/app.js:347,1038,1046-1047`、`README.md:87` | BR-028 / EX-024 | AC-F-38 / AC-A-19 |
| **REQ-039** | **视图切换必须隔离筛选状态**：切换视图/空间时不得把上一视图的筛选条件带入下一视图造成假空 | **M** | 全局导航 | `js/app.js:603-609`、`js/views.js:812` | BR-029 / EX-025 | AC-F-39 |

> 注 1：BR 编号以 §4.7 表为准；REQ-033 关联 NFR 而非 BR。
> 注 2（v1.1）：REQ-036~039 是**元需求**，不新增功能，只要求「已实现的能力必须可达且诚实」——这正是总纲 §8「反向补课」的核心，比新增功能更贴合本次目标。它们由前端实测的 4 条已验证差距（GAP-17~20）反推，每条都配了可判定的验收标准（AC-F-36~39），并须纳入 `07` 的回归范围。**本文只定义规格与验收，不修改业务代码。**

**集成域（INTEGRATION · API / MCP，v1.2 新增）**

> **编号边界**：REQ-040~044 已由 `10_CR-2026-001` 预留（附录 D.1-#6），本域从 **REQ-045** 起编号，不占用、不复用。BR 从 **BR-037**、EX 从 **EX-038**、AC-F/AC-A 从 **AC-F-47 / AC-A-47** 起（BR-030~036 / EX-026~037 / AC-F-40~46 / AC-A-41~46 已被 CR 占用）。
> **证据口径**：本域 11 条均为**新增需求、尚未实现**，「实现证据」列填写**参照依据 + 必须复用的现有边界**，不冒充已落地代码。参照文档 `https://docs.mindwtr.app/power-users/local-api`、`https://docs.mindwtr.app/power-users/mcp` 本次已实读，标【已验证·外部文档】；本系统现状「无本地 REST API / 无 MCP」由 `pyapp/httpd.py:1584-1613` 当前 24 条路由（本次计数）与业务代码 `pyapp/`、`js/` 无 `MCP` 命中【已验证】。
> **参照 ≠ 照搬**：Mindwtr 是单机单用户应用，本系统是**多团队 + 行级权限 + 本地账号**体系。参照实现中「设置里生成一个 Bearer 令牌即可全量读写」在本项目**必须改为令牌绑定既有账号并复用 `policy.py`**（REQ-047）；其 npm/Bun helper、Cloud 模式、远程隧道一律不做（§4.2 W 档）。

| REQ-ID | 功能 | 优先级 | 关联页面 | 实现证据（参照依据 / 必须复用的边界） | 关联 BR / EX | 验收 |
|---|---|---|---|---|---|---|
| REQ-045 | 本地 REST API 服务：默认关闭、显式启用；仅绑定 `127.0.0.1`、端口可配置；每请求校验 Bearer 令牌，令牌服务端生成/轮换且不明文回显前端 | M | 设置 → 高级（新增开关）/ 启动参数 | 待实现；参照 Local API §Desktop Toggle / §Authentication / §Security Notes【已验证·外部文档】；现状仅前端 API 路由 `pyapp/httpd.py:1584-1613` | BR-037 / EX-038 | AC-F-47 / AC-A-47 |
| REQ-046 | API 资源端点：health、工作项（列表/筛选/单条/创建/更新/软删/恢复/完成/归档）、项目、责任领域、情境、人员、统一搜索；路径/参数/错误码在 `06 接口契约` 定义 | M | 无独立页面（脚本 / 集成调用） | 待实现；参照 Local API §Endpoints（30 条 method+path）【已验证·外部文档】 | BR-038 / EX-039 | AC-F-48 / AC-A-48 |
| REQ-047 | 令牌必须绑定既有本地账号，逐行复用 `policy.py` 双域/团队判定与 `db.py` 字段白名单、类型归一化；禁止因走 API 绕过行级权限 | M | 全局 | 待实现；必须复用 `pyapp/policy.py:79-85,224-267`、`pyapp/db.py:565-591`【已验证】 | BR-039 / EX-040 | AC-F-49 / AC-A-49 |
| REQ-048 | API 安全写入：条件更新带版本/ETag，冲突返回 `412 task_version_conflict` 且不落库；幂等创建键冲突返回 `409 idempotency_conflict`；未变更 PATCH 不推进版本；能力在 health 声明 | S | API 客户端 | 待实现；参照 Local API §Safe task writes (v1.3.8)【已验证·外部文档】 | BR-040 / EX-041 | AC-F-50 |
| REQ-049 | 开放接口审计：经 API/MCP 的每次写操作入 `app_logs`（令牌标识、账号、动作、目标、结果）；令牌与请求头不得明文落日志 | M | 日志 / 运维 | 待实现；复用 `pyapp/applog.py`、`pyapp/policy.py:208-221`【已验证】 | BR-041 / EX-042 | AC-A-50 |
| REQ-050 | 内置 MCP 服务：默认关闭且只读；Streamable HTTP 仅绑 `127.0.0.1`（默认端口 8722，可配 1024~65535）；Bearer 令牌；连接信息可复制；关闭/退出即停服 | M | 设置 → 集成（新增） | 待实现；参照 MCP §Built-in desktop MCP / §Permissions and port【已验证·外部文档】 | BR-042 / EX-043 | AC-F-51 / AC-A-51 |
| REQ-051 | MCP 只读工具集：list/get 覆盖工作项、项目、责任领域、情境、人员、视图分区；本系统无对应数据模型的能力（如 GTD `view` 可用性）须显式声明不支持，不得伪造字段 | M | MCP 客户端 | 待实现；参照 MCP §Available Tools 的 10 个读工具【已验证·外部文档】 | BR-043 / EX-044 | AC-F-52 |
| REQ-052 | MCP 写工具集与分级授权：写工具默认拒绝；「编辑/完成/恢复」「归档」「删除」三类权限独立授予；删除一律软删除；授权与令牌绑定，撤销即时生效 | S | MCP 客户端 / 设置 | 待实现；参照 MCP §Write Tools（19 个）/ §Permission Matrix【已验证·外部文档】 | BR-044 / EX-045 | AC-F-53 / AC-A-52 |
| REQ-053 | MCP 安全写入：`expectedVersion` 冲突返回 `task_version_conflict`，`idempotencyKey` 冲突返回 `idempotency_conflict`；冲突时不得自动重试，也不得静默降级为无保护写入 | S | MCP 客户端 | 待实现；参照 MCP §Safe task writes (v1.3.8)【已验证·外部文档】 | BR-045 / EX-046 | AC-F-54 |
| REQ-054 | 零依赖 / 离线 / 本地自持不回退：API 与 MCP 必须是进程内 Python 标准库模块；禁止 Node/npm/Bun/npx 或第三方包作为运行前置；不提供远程隧道与公网暴露 | M | 全局 | 待实现；约束来源 `README.md:7-8,25-31,81`、`pyapp/__init__.py:17`、REQ-033【已验证】 | BR-046 / EX-047 | AC-R-12 |
| REQ-055 | 配置必须用户可达且反馈诚实：启用开关、端口、令牌复制/轮换、写权限开关在设置页有可触发入口；端口占用与令牌失效有明确错误提示，禁止「接口已实现但界面不可达」 | M | 设置 → 高级 / 集成 | 待实现；约束来源 REQ-036 / GAP-17 / BR-026【已验证】 | BR-047 / EX-048 | AC-F-55 |

> 注 3：本域 BR-037~047、EX-038~048、AC-F-47~55、AC-A-47~52、AC-R-12 为**预分配编号**，正文 §4.7 / §4.8 / §5 尚未建条目；按 §1.7 追溯链要求，这些条目必须在同一变更单中补齐，否则本域不得进入 G0。

### 4.3.1 功能清单统计（v1.2）

| 域 | REQ 范围 | 条数 | 备注 |
|---|---|---|---|
| 团队域 TEAM | REQ-001 ~ REQ-016 | 16 | 已兑现能力反推 |
| 个人域 PERSONAL | REQ-017 ~ REQ-024 | 8 | 已兑现能力反推 |
| 系统域 SYSTEM | REQ-025 ~ REQ-035 | 11 | 已兑现能力反推 |
| 系统域 · 治理元需求 | REQ-036 ~ REQ-039 | 4 | v1.1 新增，由 GAP-17~20 反推 |
| 系统域 · CR-2026-001 预留 | REQ-040 ~ REQ-044 | 5 | 由 CR-2026-001 新增（附录 D.1-#6），正文待回填，本次不占用 |
| 集成域 API / MCP | REQ-045 ~ REQ-055 | 11 | v1.2 新增，尚未实现，参照 Mindwtr 官方文档 |
| **合计** | **REQ-001 ~ REQ-055** | **55** | 编号连续、不复用（含 CR 预留 5 条） |

### 4.4 页面功能说明（给 `03 FDS` / ui-designer 的页面清单）

> 表格只写「做什么、什么规则」；**布局、视觉、交互状态、微文案由 UI/UX 设计师承接**。页面清单【已验证】来自 `PERSONAL_NAV` / `TEAM_NAV` / `renderView` / `dispatch`。

| 域 | 页面 / 视图键 | 入口 | 主要元素 | 主要操作（`data-action`） | 跳转 / 状态 | 证据 |
|---|---|---|---|---|---|---|
| 全局 | 认证页 | 未登录 | 登录/注册/口令/改密 | `login` / `register` / `change-password` | 成功 → 应用外壳 | `js/auth-ui.js`、`js/api.js:117-144` |
| 全局 | 应用外壳 | 登录后 | 顶栏（空间切换 / 团队选择 / 快速收集 / 搜索 / 用户面板）、侧栏、主区、抽屉、模态、toast | `switch-space` / `nav` / `close-drawer` / `close-modal` / `sign-out` | `state.space` / `state.view` | `index.html:14-62`、`js/app.js:121,597-625` |
| 全局 | 空工作区引导 | 首次登录且无团队无事项 | 双域说明 | `new-team` / `join-team` | → 团队空间 | `js/views.js:2051-2057` |
| 全局 | 搜索 | 顶栏搜索框 | 事项/项目/资源结果 | — | `state.view === 'search'` | `js/app.js:121` |
| 个人 | `inbox` 收集箱 | 个人导航 | 待受理项 | `new-item` / `open-item` / `set-status` / `quick-done` | → `clarify` | `views.js:1908,473-497` |
| 个人 | `clarify` 厘清 | 个人导航 | 待厘清行 + 组织动作 | `save-item` / `set-status` | → `next` / `someday` / `waiting` | `views.js:1909,498-529` |
| 个人 | `focus` 今日执行 | 个人导航（默认页） | 今日聚焦项 | `quick-done` / `open-item` | 默认回退视图 `views.js:2047` | `views.js:1910,530-555` |
| 个人 | `next` 下一步行动 | 个人导航 | `is_next` / `ready` 清单 | `set-status` | → `doing` | `views.js:1911,556-588` |
| 个人 | `waiting` 等待他人 | 个人导航 | `waiting` + `waiting_for` | `save-item` | → `ready` | `views.js:1912,589-604` |
| 个人 | `calendar` 日程 | 个人导航 | 日期维度 | `week`（周切换） | — | `views.js:1913,605-635`、`app.js:636` |
| 个人 | `someday` 将来/也许 | 个人导航 | `someday` 清单 | `set-status` | → `ready` | `views.js:1914,636-642` |
| 个人 | `assigned` 团队分配给我的 | 个人导航 | `assignee_id == uid` 的团队项 | `open-item` / `add-progress` | → 抽屉 | `views.js:1915,643-670` |
| 个人 | `myProjects` 我的项目 | 个人导航 | 本人相关项目 | `open-project` | → 项目抽屉 | `views.js:1916,671-707` |
| 个人 | `contexts` 情境 | 个人导航 | 情境列表 | `new-context` / `edit-context` / `save-context` / `delete-context` | 模态表单 | `views.js:1917,708-733`、`app.js:764-771` |
| 个人 | `review` 复盘 | 个人导航 | 周/月复盘卡 | `finish-review` | 同周期唯一 | `views.js:1918,734-780`、`app.js:859` |
| 个人 | `done` 已完成 | 个人导航 | `done` 清单 | `quick-done`（可回退） | — | `views.js:1919,781-791` |
| 团队 | `overview` 团队总览 | 团队导航 | 指标卡 + 告警 | `nav` / `new-item` | — | `views.js:1923,817-860` |
| 团队 | `routine` 日常工作 | 团队导航 | 日常工作/巡检 | `new-item` / `open-item` | — | `views.js:1924,861-885` |
| 团队 | `desk` 服务台 | 团队导航 | 事件/请求/待受理 | `new-item` / `set-status` | → 看板 | `views.js:1925,886-917` |
| 团队 | `board` 看板 | 团队导航 | 7 列 × 2 泳道、WIP 计数 | `set-status` / `filter`（泳道）/ `open-item` | WIP 触顶 `is-over` | `views.js:1926,918-957` |
| 团队 | `projects` 项目 | 团队导航 | 项目列表 | `new-project` / `edit-project` / `delete-project` / `open-project` | 模态 / 抽屉 | `views.js:1927,960-1000`、`app.js:742-759` |
| 团队 | `oncall` 值班与交接 | 团队导航 | 班次矩阵、主/备、交接单 | `new-oncall` / `open-oncall` / `save-oncall` / `delete-oncall` | 模态 | `views.js:1928,1001-1050`、`app.js:840-847` |
| 团队 | `resources` 资源与知识库 | 团队导航 | 6 类资源 | `new-resource` / `open-resource` / `save-resource` / `delete-resource` | 模态 | `views.js:1929,1051-1089`、`app.js:821-828` |
| 团队 | `tools` 工具台账 | 团队导航 | 工具/资产/凭据位置 | 同 `resources` | — | `views.js:1930,1090-1130` |
| 团队 | `reports` 报表 | 团队导航 | 状态分布 / 一页纸 | — | — | `views.js:1931,1131-1231` |
| 团队 | `members` 成员与角色 | 团队导航 | 成员表、角色、容量、状态 | `invite-member` / `copy-invite` / `rotate-invite` / `edit-member` / `save-member` / `remove-member` | 停用需确认弹窗 | `views.js:1932,1232-1284`、`app.js:796-818` |
| 团队 | `teamReview` 团队复盘 | 团队导航 | 团队复盘卡 | `finish-review` | 同周期唯一 | `views.js:1933,1285-1314` |
| 团队 | `teamSettings` 团队设置 | 团队导航 | WIP 上限输入、邀请码、归档 | `save-wip` / `regen-code` / `archive-team` / `save-team` | 归档后跳回个人空间 | `views.js:1934,1315-1356`、`app.js:787-793,1207-1218` |
| 浮层 | 工作项抽屉 / 新建表单 | `open-item` / `new-item` | 全字段编辑 | `save-item` / `delete-item` / `add-progress` / `add-to-project` | 抽屉 | `views.js:1378-1570`、`app.js:644-677,707-734` |
| 浮层 | 项目抽屉 / 各模态表单 | 各 `new-*` | 表单 | 各 `save-*` | 模态 | `views.js:1571-1813` |

> **给 ui-designer 的硬约束**：新增视图必须同时改三处 —— `js/views.js` 的 `renderView` 分发（`:2017-2048`）、`PERSONAL_NAV`/`TEAM_NAV`（`:1907-1935`）、以及 `js/app.js` 的 `dispatch` case（`:597-869`）；否则 `app-boot-test.mjs` 会抓到「视图未分发」（`README.md:260-262`、总纲 §4 G3）。

### 4.5 业务流程

**主流程（团队派活 → 个人执行 → 复盘）**

| 步 | 角色 | 动作 | 系统状态变化 | 证据 |
|---|---|---|---|---|
| 1 | owner/member | 登记团队工作项（票种、优先级、泳道、指派） | `work_items.team_id` 非空、`status=intake`、`owner_id` 服务端注入 | `db.py:106-149`、`policy.py:106-118` |
| 2 | 成员 | 服务台分类 → `set-status` 推进 | `intake → triage → ready → doing → review → done` | `config.js:51`、`app.js:699-705` |
| 3 | 系统 | 看板按列/泳道聚合，WIP 计数与触顶提示 | 仅展示，不拦截 | `views.js:918-957` |
| 4 | 成员（个人空间） | 「团队分配给我的」看到 `assignee_id` 项，用 GTD 五步跟进 | 同一行 `work_items`，个人视角换状态标注 | `views.js:100,643-670`、`config.js:37-48` |
| 5 | 成员 | 填进度日志 / 完成 | `progress_logs` 追加；`quick-done` 置 `done`+`completed_at`+`progress=100`，并派生下一次周期任务 | `app.js:678-696`、`db.py:221-235` |
| 6 | owner/成员 | 周期复盘 | `reviews` 同 `(owner_id, team_id, kind, period_key)` 唯一 | `db.py:251-252`、`store.py:258` |
| 7 | 值班工程师 | 交接班填 `handover_notes` | `on_call` 行更新 | `db.py:254-270`、`config.js:152-159` |

**分支流程**

| 分支 | 触发 | 处理 | 证据 |
|---|---|---|---|
| B1 受阻 / 等待外部 | 依赖未就绪 | `status=blocked` + `blocked_reason`；或 `waiting` + `waiting_for` | `db.py:44,134-135`、`config.js:43-44` |
| B2 中断型抢占 | `request` / `incident` 进入 | 落 `unplanned` 泳道，与计划内分开呈现 | `config.js:24-25,19`、`views.js:923-925` |
| B3 完成回退 | 误点完成 | `quick-done` 可从 `done` 切回 `ready`（并保留 `progress`） | `app.js:683-689` |
| B4 周期巡检 | 完成带 `recurrence` 的项 | `spawnNextOccurrence` 自动派生下一次 | `app.js:691-694`、`db.py:129` |
| B5 成员停用 | owner 置 `status != active` | 下一次请求起视为非成员，且不可自行重新加入 | `policy.py:314-319`、`test_20` |
| B6 团队归档 | owner 归档 | 团队不再出现在导航，数据保留，成员不可见 | `app.js:1207-1218`、`test_13` |
| B7 团队切换 / 多团队 | 用户属多个团队 | `renderTeamSelect` 切 `activeTeamId`，权限按当前 `team_id` 重算 | `views.js:1945-1950`、`policy.py:32-43` |

**异常流程**（详见 §4.8 EX 表）

| 异常 | 系统行为 | 证据 |
|---|---|---|
| 权限拦下（查/改/删） | 返回空数组，不报错；前端 `assertAffected` 提示「可能不存在或不在你可见的范围内」 | `README.md:87`、`policy.py:13-14,274-284` |
| 往非成员团队插入 | 抛 `Denied` → 前端 `PERMISSION_DENIED` | `policy.py:274-284`、`api.js:366` |
| 服务未启动 / 断网 | `health()` 探测失败 → `backend-unavailable` 提示 | `api.js:314-317,353-355`、`README.md:20-21` |
| 并发建表 / 写锁争用 | `init_db` 带 6 次线性退避重试；`schema_present` 已建全则直接返回不跑 DDL | `db.py:439-487` |
| 备份还原误用 | `--import` 为 INSERT OR REPLACE，需先删库文件 | 总纲 §7.4 |

### 4.6 状态机、枚举与 ITIL × GTD 映射

**统一状态机（11 态）**【已验证：`js/config.js:37-48`】

| 状态键 | 团队标注（Kanban 列） | 个人标注（GTD） | 是否在 `BOARD_COLUMNS` | 是否 `ACTIVE_STATUSES` |
|---|---|---|---|---|
| `intake` | 待受理 | 收集箱 | 是 | 是 |
| `triage` | 待分类 | 待厘清 | 是 | 是 |
| `ready` | 待办 | 下一步行动 | 是 | 是 |
| `doing` | 处理中 | 执行中 | 是 | 是 |
| `review` | 待验证 | 待复核 | 是 | 是 |
| `blocked` | 受阻 | 受阻 | 是 | 是 |
| `waiting` | 等待外部 | 等待他人 | **否** | 是 |
| `someday` | 暂缓 | 将来/也许 | **否** | 是 |
| `done` | 已完成 | 已完成 | 是 | **否**（`isOpen` 排除） |
| `archived` | 已归档 | 已归档 | **否** | **否** |

`BOARD_COLUMNS = ['intake','triage','ready','doing','review','blocked','done']`（`config.js:51`）；`ACTIVE_STATUSES` 为 8 个（`views.js:44`）；`isOpen = status !== 'done' && !== 'archived'`（`views.js:108`）。

**票种（7 类）与默认泳道**【已验证：`js/config.js:21-29`】

| 票种键 | 名称 | 默认泳道 | ITIL 归类 |
|---|---|---|---|
| `routine` | 日常工作 | `planned` | 运维补充（非标准 ITIL） |
| `inspection` | 周期巡检 | `planned` | 运维补充 |
| `request` | 服务请求 | `unplanned` | 请求 |
| `incident` | 事件故障 | `unplanned` | 事件 |
| `problem` | 问题根因 | `planned` | 问题 |
| `change` | 变更发布 | `planned` | 变更 |
| `project_task` | 项目任务 | `planned` | 运维补充 |

**ITIL 票种 × GTD 五步映射（本文补齐，解决 GAP-05）**

| GTD 五步 | 对应系统动作 | 主要状态 | 票种侧重 | 标注 |
|---|---|---|---|---|
| ① 收集 | 顶栏快速收集 / 服务台登记 | `intake` | 全部（尤其 `incident` / `request`） | 【已验证】`index.html:31-34`、`config.js:38` |
| ② 厘清 | 判定是否可执行、定票种/优先级/泳道 | `triage` → `ready` / `someday` / `waiting` | 分类 | 【推断】由 `clarify` 视图（`views.js:514-529`）与状态枚举推出，无文档直接定义 |
| ③ 组织 | 挂项目/责任领域/情境、指派、标 `is_next` | `ready` / `someday` / `waiting` | 全部 | 【推断】由 `work_items` 的 `project_id`/`area_id`/`context_id`/`is_next`（`db.py:119-121,137`）推出 |
| ④ 执行 | 看板推进 + 今日执行 + 进度日志 | `doing` / `review` / `blocked` | 全部 | 【已验证】`app.js:699-705`、`views.js:530-555` |
| ⑤ 复盘 | 周/月复盘 + 完成/归档 | `done` / `archived` | — | 【已验证】`db.py:237-252`、`views.js:734-780,1285-1314` |

> 上表是**从代码反推的映射**，其中 3 条标【推断】。若产品认为某一步应有不同对应（例如「组织」应独立于 `triage`），走 §6 变更单修订。

**优先级 / SLA**【已验证：`js/config.js:68-73`】

| 优先级 | 名称 | `slaHours` | `rank` | 后端自动化 |
|---|---|---|---|---|
| `p1` | 紧急 | 4 | 1 | **无**（GAP-07） |
| `p2` | 重要 | 8 | 2 | 无 |
| `p3` | 普通 | 24 | 3 | 无（也是 `work_items.priority` 默认值，`db.py:114`） |
| `p4` | 一般 | 72 | 4 | 无 |

**默认 WIP 上限**【已验证：`js/config.js:54-62`】：`intake 99 / triage 2 / ready 8 / doing 5 / review 3 / blocked 5 / done 0`；可由 `teams.board_config` 覆盖（`views.js:922`、`app.js:1191-1205`）。**当前为软提示，不拦截流转**（GAP-04 / BR-012）。

**状态转换规则（当前代码行为）**：`set-status` 接受任意目标状态（`app.js:699-705`），无合法转换表、无角色限制、无终态保护；`quick-done` 额外允许 `done ↔ ready`（`app.js:683-689`）。这是 §8 OPEN-03 的裁决对象，本文**不擅自增加约束**。

### 4.7 业务规则表（BR-001 ~ BR-029）

| BR-ID | 规则名称 | 触发条件 | 处理逻辑 | 例外 / 提示 | 证据 | 关联 REQ |
|---|---|---|---|---|---|---|
| BR-001 | 双域隔离 | 任意 `work_items`/`contexts`/`progress_logs`/`reviews` 读写 | `team_id IS NULL` → 仅 `owner_id == uid`；非空 → active 成员共享 | 被拦返回空结果不报错 | `policy.py:79-85`、`README.md:85` | REQ-026 |
| BR-002 | 归属服务端注入 | 任意 insert | 客户端 `owner_id` 一律丢弃，由 store 注入当前账号 | `id`/`created_at`/未知字段同样丢弃 | `db.py:565-591`、`README.md:86`、`test_17` | REQ-027 |
| BR-003 | 插入合法性 | insert 到任意业务表 | `owner_id == uid` **且**（`team_id IS NULL` 或 `is_member`） | 不成立 → `Denied` | `policy.py:106-118,274-284` | REQ-026 |
| BR-004 | 权限拦下返回空数组 | update/delete/select 命中 0 行 | 返回 `[]`，前端 `assertAffected` 提示「可能不存在或不在你可见的范围内」 | 不抛异常、不假装成功 | `README.md:87`、`policy.py:13-14` | REQ-026 |
| BR-005 | 看板流转优先 | 成员更新他人建的团队工作项 | **允许**（更新策略不含 owner 限制） | 刻意保留，改动前必读 | `policy.py:230-236`、`README.md:88` | REQ-006 |
| BR-006 | 团队情境仅创建者可改 | 非创建者更新 `contexts` | using 通过、check 拒绝（`_own_writable`），操作无效果 | 云版遗留形状，如实保留；delete 不受此限 | `policy.py:249-253`、`README.md:89` | REQ-015 |
| BR-007 | 停用即失权 | `team_members.status != 'active'` | `load_context` 剔除该成员关系，后续请求视为非成员 | 与云版语义的**有意差异** | `policy.py:314-319`、`README.md:90`、`test_20` | REQ-004 |
| BR-008 | 成员不可自提权 / owner 行受保护 | 成员改自己 `role`；删除 owner 行 | check 要求 `role == ctx.role_of(tid)`；`role == 'owner'` 的行不可删 | 管理员可改他人角色 | `policy.py:165-176`、`test_18`~`test_19` | REQ-003 |
| BR-009 | 复盘同周期唯一 | 保存复盘 | 唯一索引 `(owner_id, COALESCE(team_id,0), kind, period_key)` 兜底并发，先查后写 upsert | 他人复盘不可改/删 | `db.py:249-252`、`store.py:258`、`test_26`~`test_27` | REQ-013 / REQ-021 |
| BR-010 | 运行日志只写不改删 | 任意对 `app_logs` 的 update/delete | 全 `deny`；insert 允许归属本人或空（未登录） | 避免掩盖故障证据 | `policy.py:208-221`、`test_28` | REQ-030 |
| BR-011 | 统一状态机 + 双标注 | 展示工作项状态 | 团队用 `label`、个人用 `personal`；`set-status` 直接赋值目标状态 | 无合法转换约束（OPEN-03） | `config.js:37-51`、`views.js:39-42`、`app.js:699-705` | REQ-006 / REQ-018 |
| BR-012 | WIP 上限 | 看板渲染 / 保存团队设置 | 按 `{...DEFAULT_WIP, ...board_config}` 计数，`limit>0 && 超限` 标 `is-over`；`save-wip` 把配置写入 `teams.board_config` | **仅视觉告警 + 建议文案，不阻止流转**；`clamp(0,99)` | `config.js:54-62`、`views.js:922,931-936,956,1320-1346`、`app.js:1191-1205`、`test_25` | REQ-006 / REQ-014 |
| BR-013 | 优先级与 SLA 时限 | 选择优先级 | `P1/P2/P3/P4 → 4/8/24/72h`；前端 `slaRisk`/`isOverdue`/`dueChip` 显示 | **无后端自动计算 `sla_due_at`、无超时升级/通知**（GAP-07） | `config.js:68-73`、`db.py:125-126`、`views.js:111-128,307-325` | REQ-023 |
| BR-014 | 票种默认泳道 | 新建工作项 | `request`/`incident` → `unplanned`；其余 → `planned`；看板按泳道分道展示 | 泳道可在视图筛选，字段存 `work_items.swimlane` | `config.js:21-29`、`db.py:115`、`views.js:923-925` | REQ-005 / REQ-007 |
| BR-015 | 邀请码唯一与轮换 | 创建/轮换/加入团队 | `upper(join_code)` 唯一索引（非空非空串）；归档团队不可加入；`regen-code` 可轮换 | 大小写不敏感 | `db.py:85-87`、`app.js:789`、`test_12`~`test_13` | REQ-002 / REQ-014 |
| BR-016 | 完成派生下一次 | 完成带 `recurrence` 的项 | `spawnNextOccurrence(item)` 新建下一次周期任务 | 巡检清单由 `INSPECTION_TEMPLATE` 自动带入 notes | `app.js:691-694`、`config.js:140-149`、`db.py:129` | REQ-016 / REQ-024 |
| BR-017 | 登录限速 | 同一 (邮箱, 来源 IP) 15 分钟内失败 10 次 | 拒绝登录；来源 IP 默认取 TCP 地址，仅 `GTD_TRUST_PROXY=1` 时采信 XFF | 防伪造 XFF 换限流键 | `README.md:102-104,135-145`、`pyapp/config.py:87-92`、`test_08`,`test_40`,`test_forwarded_for_is_honoured_when_proxy_trusted` | REQ-025 |
| BR-018 | 改密踢出其它会话 | 改密成功 | 只保留当前会话 | 需提供原密码 | `README.md:105`、`test_09` | REQ-025 |
| BR-019 | 写操作三重校验 + 暴露面 | 任意写请求 / 静态请求 | `Content-Type: application/json` + `Origin == Host` + `SameSite=Lax`；不开 CORS；静态仅白名单且不得逃出子树 | 目录穿越 `/js/../server.py` 被拒 | `README.md:96-101`、`pyapp/config.py:19-22`、`test_29`~`test_36` | REQ-029 |
| BR-020 | 字段白名单与类型归一化 | 任意 CRUD payload | `COLUMNS` 为唯一权威；`split_payload` 丢弃 pk/owner/stamp/未知字段；`encode_value` 按 kind 归一化 | COLUMNS 键集合必须等于 `PRAGMA table_info`（防漂移断言） | `db.py:19-21,300-375,516-591`、`test_columns_spec_matches_real_schema`、`test_22`~`test_24` | REQ-027 |
| BR-021 | 离线与零依赖 | 任意运行 | 仅 Python 标准库；前端原生 ESM 无 CDN；SQLite 单文件随目录走 | AI 通道需联网（GAP-11） | `README.md:7-8,25-31,81`、`pyapp/__init__.py:17` | REQ-033 |
| BR-022 | 备份与还原语义 | `--export` / `--import` / `/api/export` | 导出含口令散列需妥善保管；**`--import` 为 INSERT OR REPLACE，不清空不去重** | 灾难恢复必须先删库文件 | `README.md:45-46,56-57`、总纲 §7.4、`test_backup_round_trip` | REQ-031 |
| BR-023 | AI 可选与密钥保护 | 未配置 / 配置 / 调用 | 未配置返回 `LLM_NOT_CONFIGURED` 并给指引；`GET /api/llm/config` 不回显密钥（连掩码都不给） | 密钥只在服务端 | `README.md:106,177-192`、`pyapp/config.py:8,94-120`、`test_38`~`test_39` | REQ-032 |
| BR-024 | 团队资源删除权限 | 删除 `areas`/`on_call`/`projects`/`resources`/`team_members` 等 | `areas`/`on_call` 仅 `is_admin`；`projects`/`resources` 为 `is_admin` 或行 owner；`teams` 仅 `owner_id == uid` | 口径不统一，见 S-08 | `policy.py:148,239,244,257,264` | REQ-009~011 |
| BR-025 | 版本串四处同步 + 构建标识 | 前端/服务端任一改动 | `js/config.js` 的 `APP_VERSION` + 各模块 `?v=` + `index.html` 引用 + `pyapp/__init__.py` 四处同版本；发布以 `GTD_BUILD` 核对新槽位 `/api/health` | 不同步 → G2 不通过 | `README.md:264-265`、总纲 §7.2/§7.5、`config.py:57-61`、`index.html:9,64`、`js/app.js:15,16,24,29,33` | REQ-034 / REQ-035 |
| **BR-026** | **功能可达性**：端点存在即须有可触发 UI | 新增/变更任一端点或 dispatch 分支 | 验收必须包含「从真实 `index.html` 出发点击到该端点」的端到端路径；仅有接口测试不算通过 | 允许「刻意无 UI」的能力（如 CLI 专用），但须在 PRD 显式登记 | `pyapp/httpd.py:824`、`js/app.js:863-868,1439-1451`、`js/views.js:1895`；GAP-17；`README.md:260-262`（`app-boot-test.mjs` 的定位） | REQ-036 / AC-F-36 |
| **BR-027** | **写操作幂等防护（一次意图一次写入）** | 任意创建/提交类动作 | 提交后至响应返回前，入口须禁用或加 busy 锁；关闭浮层不得早于写入完成；等待期不得接受第二次同类提交 | 只读动作不受限 | `js/app.js:503-518,670-674,391-395`；GAP-18 | REQ-037 / AC-F-37 |
| **BR-028** | **结果诚实（受影响行数必校验）** | 任意 update/delete | 一律 `assertAffected(rows)`：空数组 → 按 README.md:87 提示「可能不存在或不在你可见的范围内」，**不得提示成功** | 无例外；`app_logs` 只写不改删不适用 | `js/app.js:347`（定义）、`:1038`（有校验）、`:1046-1047`（漏检）、`README.md:87`、`pyapp/httpd.py:651-652`；GAP-19 | REQ-038 / AC-F-38 / AC-A-19 |
| **BR-029** | **视图/空间切换重置筛选状态** | `case 'nav'` / `case 'switch-space'` | 切换时至少清 `state.search` 与 `state.filters`（或按视图隔离筛选命名空间）；不得让上一视图的条件造成下一视图假空 | 若产品决定「筛选跨视图保留」须显式定义并在 UI 标明 | `js/app.js:603-609`、`js/views.js:812`；GAP-20 | REQ-039 / AC-F-39 |

> BR-026~029 为 v1.1 新增的**治理型业务规则**：不新增功能，只把「已有功能必须可达且诚实」变成可验收的规则。四条均对应一条已验证差距（GAP-17~20）与一条 REQ（036~039）。

### 4.8 异常场景表（EX-001 ~ EX-025）

> 「前端提示文案」**逐字取自** `js/api.js:347-385` 的 `friendlyError` MAP（【已验证】），不自行编造；「后端处理」取自 `policy.py` / `httpd.py` / `db.py` 与 README 安全说明。

| EX-ID | 场景 | 触发条件 | 前端提示文案（原文） | 后端处理 | 错误码 / 证据 | 关联 REQ |
|---|---|---|---|---|---|---|
| EX-001 | 服务未启动 / 直接双击 `index.html` | `health()` 失败或 `backend-unavailable` | 「无法连接本地服务，请确认服务进程仍在运行（python server.py）。」 | 无服务端；页面明确告知接口不可用 | `api.js:353-355`、`README.md:20-21` | REQ-033 |
| EX-002 | 请求超时 | `kind === 'timeout'` | 「请求超时，请稍后重试」 | 客户端超时（health 6000ms / llmChat 180000ms） | `api.js:352,316,334` | REQ-032 |
| EX-003 | 登录态失效 | 会话过期/被踢 | 「登录状态已失效，请重新登录。」 | `401`，会话表只存令牌散列 | `api.js:359`、`db.py:48-57`、`test_07` | REQ-025 |
| EX-004 | 邮箱或密码错误 | 登录失败 | 「邮箱或密码不正确。」 | 计入限流计数 | `api.js:360`、`test_06`,`test_08` | REQ-025 |
| EX-005 | 原密码不正确 | 改密 | 「原密码不正确。」 | 拒绝改密 | `api.js:361`、`test_09` | REQ-025 |
| EX-006 | 邮箱已注册 | 注册重名 | 「该邮箱已注册，请直接登录。」 | `lower(email)` 唯一索引 | `api.js:362`、`db.py:44`、`test_04` | REQ-025 |
| EX-007 | 邮箱格式 / 弱密码 | 注册校验 | 「请输入有效的邮箱地址。」/「密码强度不足。」 | `400` 校验 | `api.js:363-364`、`test_05` | REQ-025 |
| EX-008 | 登录被限速 | 15 分钟失败 10 次 | 「尝试次数过多，请稍后再试。」 | 拒绝，防 XFF 伪造 | `api.js:365`、`README.md:102-104`、BR-017 | REQ-025 |
| EX-009 | 越权 / 非成员写入 | 往非成员团队插入、或权限拦下 | 「没有权限执行该操作（可能你不是该团队成员）。」；空数组时「可能不存在或不在你可见的范围内」 | `Denied` 或返回 `[]` | `api.js:366`、`policy.py:274-284`、`README.md:87`、`test_16` | REQ-026 |
| EX-010 | 邀请码无效 / 已被占用 / 团队归档 | 加入或轮换邀请码 | 「邀请码无效，请向团队所有者确认。」/「该邀请码已被占用，请重新生成。」 | `upper(join_code)` 唯一约束；归档团队拒绝加入 | `api.js:367-368`、`db.py:86-87`、`test_11`~`test_13` | REQ-002 |
| EX-011 | 成员被停用 | `status != active` 后访问 | 「你已被移出该团队，请联系团队所有者。」 | `load_context` 剔除成员关系 | `api.js:369`、`policy.py:314-319`、`test_20` | REQ-004 |
| EX-012 | 未知表 / 非法数据 / 过大 | 通用 CRUD | 「数据表不存在或不可写。」/「提交的数据格式不正确。」/「提交的内容过大。」 | `WRITABLE_TABLES` 校验；`max_body_bytes` 默认 8 MiB | `api.js:370-373`、`db.py:378,103`、`config.py:103`、`test_35` | REQ-027 / REQ-029 |
| EX-013 | 跨站写操作 | `Origin != Host` 或 `Content-Type` 非 json | 「请求被安全策略拒绝（跨站来源）。」 | 拒绝写；不开 CORS | `api.js:374`、`README.md:96-97`、`test_32`~`test_33`,`test_36` | REQ-029 |
| EX-014 | 注册关闭 / 口令错误 | `--no-signup` / `--signup-code` | 「本实例已关闭自助注册。」/「注册口令不正确。」 | `allow_signup` / `signup_code` 校验 | `api.js:375-376`、`config.py:80-85`、`README.md:42-43,147-148` | REQ-028 |
| EX-015 | AI 四类异常 | 未配置 / 不可达 / 上游错 / 超时 | 「尚未配置 AI 服务。」/「无法连接 AI 服务。」/「AI 服务返回错误。」/「AI 服务响应超时。」 | 明确错误码，不静默失败 | `api.js:377-380`、`README.md:192`、`test_38` | REQ-032 |
| EX-016 | 权限拦下（非错误路径） | update/delete 命中 0 行 | 「可能不存在或不在你可见的范围内」（`assertAffected`） | 返回 `[]`，**不是异常** | `README.md:87`、`policy.py:13-14,277-279` | REQ-006 / REQ-022 |
| EX-017 | 并发建表 / 写锁争用 | 蓝绿切换时两个进程共用同一 SQLite | （无用户提示，启动期自愈） | `schema_present` 已建全则不跑 DDL；否则 6 次线性退避重试（约 5 秒） | `db.py:439-487`、`docs/零停机发布方案.md:485-487` | REQ-034 |
| EX-018 | 断网 / 离线 | 网络中断 | 「无法连接本地服务…」（仅当本地服务也不可达） | 核心功能本地自持；AI 不可用 | `api.js:353-355`、`README.md:31,81`、GAP-11 | REQ-033 |
| EX-019 | 重复提交 / 复盘并发 | 同周期复盘重复保存 | （upsert 无错） | 唯一索引兜底并发，只留一条 | `db.py:249-252`、`test_26` | REQ-013 |
| EX-020 | 目录穿越 | `/js/../server.py` | （静态请求被拒） | 白名单 + 解析后不得逃出子树；无目录列表 | `README.md:98-101`、`config.py:19-22`、`test_30`,`test_31` | REQ-029 |
| EX-021 | 备份还原残留叠加 | 未删库直接 `--import` | （无专门提示，属操作风险） | INSERT OR REPLACE，不清空不去重 → 残留行叠加 | 总纲 §7.4、`README.md:46` | REQ-031 |
| **EX-022** | **点「修改密码」无任何反应** | 用户面板点击 `data-action="change-password"` | **当前无任何提示（缺陷）**：`form` 为 null，分支静默 `break` | 后端 `POST /api/auth/password` 正常、无需改动；缺口在前端无表单渲染 | `js/views.js:1895`、`js/app.js:863-868,1439-1451`、`pyapp/httpd.py:824`；GAP-17 | REQ-036 / BR-026 / AC-F-36 |
| **EX-023** | **连点提交产生重复数据** | 网络较慢时连续点击「保存」/「创建」 | **当前无提示（缺陷）**：浮层已关但请求未回，第二次点击仍被受理 | 后端无幂等键，重复写入成立 | `js/app.js:503-518,670-674,391-395`；GAP-18 | REQ-037 / BR-027 / AC-F-37 |
| **EX-024** | **「假装成功」：提示进度已写入但数据未变** | `addProgress` 的 `updateItem` 被权限拦下（返回空数组） | 当前错误地 `toast('进度已写入')`；**应改为** README.md:87 的「可能不存在或不在你可见的范围内」 | `h_rows` 返回 `{rows: []}`，不抛错 | `js/app.js:1046-1047`（对比 `:1038` 正确写法）、`README.md:87`、`pyapp/httpd.py:651-652`；GAP-19 | REQ-038 / BR-028 / AC-F-38 |
| **EX-025** | **跨视图筛选串味导致列表假空** | 团队空间设置过「周期巡检」筛选后切到「服务台」 | 当前显示「队列已清空」（误导用户以为数据丢失）；**应**在切换时重置筛选或标明当前筛选 | 无后端参与，纯前端状态残留 | `js/app.js:603-609`、`js/views.js:812`；GAP-20 | REQ-039 / BR-029 / AC-F-39 |

**异常覆盖自检（v1.1 更新）**：网络（EX-001/002/017/018）、权限（EX-009/011/016/**024**）、数据（EX-006/007/012/019/021/**023**）、第三方（EX-015）、并发（EX-017/019/**023**）、安全（EX-013/020）、**可达性与状态一致性（EX-022/025）** 均已覆盖，符合 `exception_scenario_mining` Skill 的检查清单。
> EX-022~025 与 EX-001~021 性质不同：前 21 条是「系统已定义的异常路径 + 既定提示文案」，后 4 条是**当前缺失正确处理的缺陷场景**，提示文案列标明「当前无提示（缺陷）」并给出**应达到的口径**（不设计新功能，只定义可判定验收）。

### 4.9 非功能需求（可判定指标）

| NFR-ID | 需求 | 可判定指标（通过 / 不通过） | 证据 | 标注 |
|---|---|---|---|---|
| NFR-01 | 离线可用 | **核心功能**（账号/工作项/看板/GTD/复盘/导出）在完全无外网时可用 → 通过；任一核心功能加载 CDN/外部 SDK → 不通过。AI 通道明确列为「可选且需联网」 | `README.md:31,81`、`config.py:97`、`index.html`（无外链） | 【已验证】（现状）／【推断】（措辞需收窄，OPEN-05） |
| NFR-02 | 零第三方依赖 | `python server.py` 在纯净 Python 3.8+ 环境启动成功、无需 `pip install`、无需 Node/构建工具 → 通过 | `README.md:25-31`、`pyapp/__init__.py:17`、`Dockerfile`（不 pip install） | 【已验证】（文档与 Dockerfile）／【未验证】（本次未实跑纯净环境启动） |
| NFR-03 | 单服务器 / 单文件数据库 | 全部数据落在 `data/gtd4teams.db`（+ WAL），`--data-dir` / `GTD_DATA_DIR` 可外挂 → 通过 | `README.md:30,56-59`、`config.py:63-66`、`db.py:425-436` | 【已验证】 |
| NFR-04 | 数据可携带 | `--export` / `--import` 往返一致（含字段规格断言）→ 通过；必须文档化 `--import` 的 INSERT OR REPLACE 语义与「先删库」前置条件，否则判不通过 | `test_backup_round_trip`、总纲 §7.4、EX-021 | 【已验证】（往返测试存在）／【未验证】（本次未重跑） |
| NFR-05 | 并发一致性 | 两进程共用一个 SQLite 文件：并发写零失败、数据互见、会话跨进程有效 → 通过（**Linux 上须实测**） | `tests/test_zerodowntime.py` 9 项、`docs/零停机发布方案.md:468-469,485-487`、总纲 §1 唯一阻塞项 | 【未验证】（本次未重跑；Linux 行为列为未验证） |
| NFR-06 | 并发编辑冲突策略 | 两人同改一张卡片的行为需**有明确定义**（后写覆盖 / 拒绝 / 合并）；当前无定义 → 判不通过，登记为 OPEN 项 | `db.py:106-146` 无版本字段、`app.js:701` 直接提交 | 【推断】（待访谈 Q17 确认容忍度） |
| NFR-07 | 安全基线 | 不开 CORS、写操作双重校验、静态白名单、登录限速、密钥不回显、改密踢会话 → 逐条有测试即通过 | `README.md:94-106`、`test_08`,`test_29`~`test_36`,`test_38`~`test_40` | 【已验证】（测试名存在）／【未验证】（本次未重跑） |
| NFR-08 | 可观测性 | `/api/health` 回传 `version` + `build`；`app_logs` 可写不可改删；构建标识切流前核对 → 通过 | `config.py:55-61,122-136`、`httpd.py:816`、`policy.py:208-221`、总纲 §7.5 | 【已验证】（端点与字段存在）／S-07 匿名日志不可读为缺口 |
| NFR-09 | 发布零停机 | `smoke.sh` 全过、`probe.sh` 零失败且最大间隔 ≤ 3000ms、构建标识一致 → 通过 | 总纲 §4 G6、`docs/零停机发布方案.md:470` | 【未验证】（发布实跑未做，§11 已列） |
| NFR-10 | 版本一致性 | `APP_VERSION` / 所有 `?v=` / `index.html` / `pyapp/__init__.py` **四处**字符串相等 → 通过 | `config.js:170`、`__init__.py:20`、`index.html:9,64`、`app.js:15,16,24,29,33`（当前均 `20260917a`） | 【已验证】（本次逐文件比对一致） |

### 4.10 数据与接口影响（交 `05` / `06` 承接）

| 类别 | 事实 | 证据 | 下游 |
|---|---|---|---|
| 数据表 | 14 张：`users`/`sessions`/`settings`（系统，不开放 CRUD）+ 11 张业务表 | `db.py:35-285,300-382` | `05 数据模型` |
| 字段规格 | `COLUMNS` 为唯一权威，键集合必须等于 `PRAGMA table_info` | `db.py:19-21,300-375`、`test_columns_spec_matches_real_schema` | `05` |
| 迁移纪律 | 幂等建库 + `schema_present` 短路；expand/contract；迁移 SQL 必须配回滚 SQL | `db.py:439-487`、总纲 §7.3 | `05` |
| 接口 | `ROUTES` 20 条 (method,path) / 19 条唯一路径：`/api/health`、`/api/auth/*`(8)、`/api/workspace`、`/api/rows`、`/api/teams/*`(2)、`/api/reviews/save`、`/api/logs`、`/api/export`、`/api/llm/*`(4) | `pyapp/httpd.py:815-835` | `06 接口契约` |
| 通用 CRUD | `mutate(table, 'insert'/'update'/'delete')`，`/api/rows` 承载全部 11 张业务表 | `js/api.js:160-162`、`httpd.py:826` | `06` |
| 错误码 | 22 个业务错误码 + 3 类网络异常，前端 `friendlyError` 统一映射 | `js/api.js:347-385` | `06`（错误码契约） |
| 安全响应头 | 会话 Cookie HttpOnly + SameSite=Lax，HTTPS 代理下加 Secure | `README.md:76`、`test_secure_cookie_behind_https_proxy`、`test_no_secure_cookie_on_plain_http` | `04` / `08` |

---

## 5. 验收标准（三层，逐条可判定，回指 REQ-ID）

> 判定口径：每条必须能回答「通过 / 不通过」，禁止「基本完成」「看起来没问题」（总纲 §3）。格式为 Given/When/Then 或勾选清单。
> **本次未实际执行任何测试命令**（§2.3）。下表「现有测试锚点」是**应当运行的验证命令**，不是「已通过」的结论；实际执行与签字由 `07` 测试体系与 `ACC-Gn` 验收单承接。

### 5.1 功能验收（AC-F）

| AC-ID | Given / When / Then | 关联 REQ | 现有测试锚点 | 通过判定 |
|---|---|---|---|---|
| AC-F-01 | Given 已登录用户，When 创建团队，Then `teams` 新增 1 行且 `team_members` 同步新增 1 行 `role='owner'`，两步原子 | REQ-001 | `test_10_create_team_registers_owner_atomically` | 两行同时存在，无中间态 |
| AC-F-02 | Given 有效邀请码，When 加入，Then 成为 active 成员；Given 归档团队/错误码，When 加入，Then 被拒且无成员行 | REQ-002 | `test_11`~`test_13` | 4 个子用例全过 |
| AC-F-03 | Given member，When 改自己 `role='owner'`，Then 被拒；When admin 删 owner 行，Then 被拒 | REQ-003 | `test_18`,`test_19` | 越权写返回空/拒绝，角色未变 |
| AC-F-04 | Given 成员被置 `status='disabled'`，When 用原会话访问团队数据，Then 立刻不可见且不能重新加入 | REQ-004 | `test_20_disabled_member_loses_access_and_cannot_rejoin` | 访问被拒 + 重入被拒 |
| AC-F-05 | Given 团队成员，When 建 7 类票种各 1 条，Then `item_type` 正确、`swimlane` 符合 BR-014 默认泳道 | REQ-005 | `config.js:21-29`（枚举）、`db.py:112-115` | 7 条均可建且泳道正确 |
| AC-F-06 | Given `doing` 列已有 5 项且 `board_config.doing=5`，When 再 `set-status` 到 `doing`，Then **列标 `is-over`（当前行为）**；Given 保存新上限，Then `board_config` 往返一致 | REQ-006 | `test_25_team_board_config_round_trip`、`views.js:931-936` | 按当前「软提示」语义判定；若裁决改硬约束则本条随 CR 更新（OPEN-01） |
| AC-F-13 | Given 同一 `(owner, team, kind, period)`，When 连续保存 2 次复盘，Then 表中仅 1 行；When 他人改该行，Then 无效果 | REQ-013 | `test_26`,`test_27` | 行数=1 且越权改无效 |
| AC-F-17 | Given 顶栏快速收集框，When 输入并回车，Then 生成 `status='intake'` 的个人工作项 | REQ-017 | `index.html:31-34`、`config.js:38` | 新项出现在 `inbox` |
| AC-F-18 | Given 个人空间，When 逐个访问 12 个导航视图，Then 每个都由 `renderView` 正确分发、无空白/异常 | REQ-018 | `.smoke/render-test.mjs`（80 项）、`.smoke/app-boot-test.mjs`（46 项） | 12/12 渲染通过 |
| AC-F-19 | Given owner 把团队工作项 `assignee_id` 设为某成员，When 该成员进个人空间，Then 该项出现在「团队分配给我的」 | REQ-019 | `views.js:100,643-670` | 1 条可见且归属正确 |
| AC-F-25 | Given 未注册邮箱，When 注册→登录→改密→登出，Then 每步符合预期且改密后其它会话失效 | REQ-025 | `test_03`~`test_09` | 7 项全过 |
| AC-F-26 | Given 用户 A 的个人项与团队 T 的项，When A 读工作区，Then 只见本人个人项 + 其 active 成员团队项；When A 往非成员团队插数据，Then 拒绝 | REQ-026 | `test_14`~`test_16` | 可见集合精确匹配 + 越权插入被拒 |
| AC-F-28 | Given `--no-signup` 或错误口令，When 注册，Then 返回 `SIGNUP_DISABLED` / `INVALID_SIGNUP_CODE` | REQ-028 | `api.js:375-376`、`config.py:80-85` | 两个错误码正确 |
| AC-F-31 | Given 运行中的库，When `--export` 到文件→删库→`--import`，Then 数据完整还原 | REQ-031 | `test_backup_round_trip` | 往返一致（注意 BR-022 前置条件） |
| AC-F-35 | Given 任一次前端改动，When 检查四处版本串，Then 全部相等 | REQ-035 | 无自动化断言（建议纳入 L1，总纲 §10） | 4 处字符串相等 |
| **AC-F-36** | Given 已登录用户，When 在用户面板点击「修改密码」，Then **出现可填写的原密码 / 新密码表单**，且提交后 `POST /api/auth/password` 被调用、密码确已变更 | REQ-036 / BR-026 / EX-022 | **现有 46 项 `app-boot-test.mjs` 未覆盖此路径**（GAP-17 实测：全绿仍漏掉）；需新增端到端用例 | 表单可见 **且** 端到端改密成功；缺任一即不通过（仅接口测试通过不算） |
| **AC-F-37** | Given 任一创建类表单，When 在响应返回前连续点击 2 次「保存 / 创建」，Then **只产生 1 条新记录** | REQ-037 / BR-027 / EX-023 | 现有测试无防重断言（GAP-18）；需新增连点用例 | 记录数恰好 = 1；第 2 次点击被禁用或被忽略 |
| **AC-F-38** | Given 一个会被权限拦下的 update（后端返回 `rows: []`），When 前端收到结果，Then **不出现「成功」类提示**，并给出 `README.md:87` 的「可能不存在或不在你可见的范围内」 | REQ-038 / BR-028 / EX-024 | `js/app.js:1046-1047` 当前违反；对照 `:1038` 的正确写法；`pyapp/httpd.py:651-652` | 全量 Grep：所有 update/delete 调用点均有 `assertAffected`；无「空数组 + 成功提示」组合 |
| **AC-F-39** | Given 团队空间已设置某筛选（如「周期巡检」），When 切换到「服务台」，Then 列表不假空（筛选被重置，或 UI 明确标示当前生效的筛选） | REQ-039 / BR-029 / EX-025 | 现有测试未覆盖（GAP-20）；需新增跨视图切换用例 | 切换后不出现「队列已清空」这类误导性空态 |

> 其余 REQ（007~012、014~016、020~024、027、029、030、032、033、034）的功能验收项与 §5.2 / §5.3 的接口/上线验收合并判定，避免重复列表；**每条 REQ 在 §4.3 都有实现证据，且在下表能找到至少一条对应验收项**，满足 G0「每条需求有可判定的验收标准」。
>
> **v1.1 回归范围补充（重要）**：GAP-17 证明「46 项 `app-boot-test.mjs` 全绿」并不保证已实现功能**用户可达**——该文件用真实 `index.html` 建 DOM，但只覆盖已渲染的路径，`#pwd-form` 从未被渲染故根本测不到。因此 AC-F-36~39 必须作为**新增回归用例**纳入 `07` 的 L1，并在 CR 的 `impact_tests` 中强制勾选；这也印证了总纲 §2「测试 → 验收断链」：全绿只说明脚本跑了，不说明「达到了什么标准才算可用」。

### 5.2 接口验收（AC-A）

| AC-ID | 判定项（勾选式） | 关联 REQ | 验证命令 / 测试锚点 |
|---|---|---|---|
| AC-A-01 | `GET /api/health` 返回本地模式与 `version`/`build` | REQ-025 / REQ-034 | `test_01_health_reports_local_mode` |
| AC-A-02 | 未登录访问 `/api/workspace` 被拒 | REQ-025 / REQ-026 | `test_02_auth_required_for_workspace` |
| AC-A-14 | 个人项隔离、团队项共享但不公开 | REQ-026 | `test_14`,`test_15` |
| AC-A-16 | 往外部团队插入被拒 | REQ-026 | `test_16_insert_into_foreign_team_is_denied` |
| AC-A-17 | 客户端提交的 `owner_id` 被丢弃 | REQ-027 | `test_17_owner_id_cannot_be_forged` |
| **AC-A-19** | **被权限拦下的 update/delete 一律返回 `{rows: []}` 而非报错；且前端不得据此提示成功**（`README.md:87` 结果诚实纪律的端到端校验） | REQ-038 / BR-028 / EX-024 | `pyapp/httpd.py:648-671`、`README.md:87`；前端侧对照 `js/app.js:1038`（正确）与 `:1046-1047`（漏检，GAP-19）；需新增契约用例 |
| AC-A-22 | 未知字段被丢弃；JSON/标量类型往返一致；JSON 列直读库仍正确 | REQ-027 | `test_22`,`test_23`,`test_24` |
| AC-A-25 | `board_config` 往返一致 | REQ-006 | `test_25` |
| AC-A-26 | 复盘 upsert 同周期仅 1 行 / 他人复盘不可改 | REQ-013 | `test_26`,`test_27` |
| AC-A-28 | 日志匿名可写、仅本人可读 | REQ-030 | `test_28_logs_accept_anonymous_but_only_owner_can_read` |
| AC-A-29~31 | 静态仅托管白名单；不暴露非站点路径；无目录列表 | REQ-029 | `test_29`,`test_30`,`test_31` |
| AC-A-32~36 | 写需 JSON Content-Type；跨站写被拦；未知 API/方法/表有确定响应；无 CORS 头 | REQ-029 | `test_32`,`test_33`,`test_34`,`test_35`,`test_36` |
| AC-A-37 | 导出需登录 | REQ-031 | `test_37_export_requires_login` |
| AC-A-38~39 | 未配置返回 `LLM_NOT_CONFIGURED`；配置接口永不回显密钥 | REQ-032 | `test_38`,`test_39` |
| AC-A-40 | 伪造 XFF 不能绕过登录限流；开启信任代理后确实被采信且加 Secure Cookie | REQ-025 / BR-017 | `test_40`,`test_forwarded_for_is_honoured_when_proxy_trusted`,`test_secure_cookie_behind_https_proxy`,`test_no_secure_cookie_on_plain_http` |
| AC-A-SCHEMA | `COLUMNS` 键集合 = `PRAGMA table_info`；14 张表齐全；空库 `schema_present` 为假；`init_db` 幂等不丢数据 | REQ-027 / REQ-031 | `test_columns_spec_matches_real_schema`,`test_expected_tables_are_all_created`,`test_schema_present_is_false_on_empty_db`,`test_init_db_is_idempotent` |

**建议运行的验证命令**（本次未执行，标注「未实际验证」）：

```bash
python tests/test_api.py                 # 49 项：接口 / 权限 / 存储 / 代理信任
python tests/test_zerodowntime.py        # 9 项：零停机前提（首次上服务器前必跑）
node .smoke/render-test.mjs              # 80 项
node .smoke/auth-flow-test.mjs           # 65 项
node .smoke/app-boot-test.mjs            # 46 项（改 index.html / render / dispatch 后必跑）
python server.py --check                 # 自检：建库、打印配置与统计
```

### 5.3 上线验收（AC-R）

| AC-ID | 判定项（可勾选、可签字） | 关联 REQ | 证据 / 出处 | 当前状态 |
|---|---|---|---|---|
| AC-R-01 | 零依赖、离线、单文件数据库、数据目录可外挂四项全部核对 | REQ-033 | `README.md:25-31,56-59`、NFR-01~03 | 【已验证】（文档与代码） |
| AC-R-02 | `python3 tests/test_zerodowntime.py` 在**目标服务器**全绿（并发写零失败、会话跨进程有效、构建标识可区分） | REQ-034 | 总纲 §1「唯一阻塞项」、`docs/零停机发布方案.md:468-469,485-487` | **【未验证】必须在服务器上执行** |
| AC-R-03 | `docker build` 成功 + `docker compose config` 由 compose 自身校验通过 | REQ-034 | `docs/零停机发布方案.md:477-481` | 【未验证】开发机无 Docker |
| AC-R-04 | `smoke.sh` 五组检查全过；`probe.sh` 零失败且最大间隔 ≤ 3000ms | REQ-034 | 总纲 §4 G6、`docs/零停机发布方案.md:470` | 【未验证】（脚本存在，未实跑发布） |
| AC-R-05 | 切流前新槽位 `/api/health` 回传 `build` 与本次 `GTD_BUILD` 一致 | REQ-034 | 总纲 §7.5、`config.py:57-61` | 【未验证】 |
| AC-R-06 | 备份可还原、`rollback.sh` 可用、Caddy 指令经 `caddy validate` | REQ-034 | `docs/零停机发布方案.md:483-484`、总纲 §4 G6 | 【未验证】 |
| AC-R-07 | 四处版本串相等；`GTD_TRUST_PROXY=1` 与槽位端口只绑 `127.0.0.1` 配对正确；公网实例已收紧自助注册 | REQ-035 / REQ-029 | NFR-10、`README.md:135-148`、总纲 §4 G2/G5 | 【已验证】（版本串本次比对一致；后两项待发布核对） |
| AC-R-08 | G4/G5/G6 三张验收单已填（`ACC-G4-`/`ACC-G5-`/`ACC-G6-`） | 全部 | 总纲 §8 第二步 | 待执行 |

### 5.4 G0 门禁自检（本文交付是否满足总纲 §4）

| G0 判定标准 | 本文落点 | 结论 |
|---|---|---|
| 每条需求有唯一 ID | §4.3 `REQ-001`~`REQ-035`，§1.7 状态机规定不复用不回收 | 满足 |
| 每条需求有可判定的验收标准 | §5.1/§5.2/§5.3，每条回指 REQ-ID | 满足（合并判定项已说明） |
| 每条需求有优先级 | §4.3 优先级列（M/S），§1.6 复算口径 | 满足 |
| 权限矩阵与 `policy.py` 逐条对齐 | §3.3 逐表逐操作 + `policy.py:行号` | 满足 |
| 权限矩阵与代码不一致处已标注为「刻意取舍」 | §3.5 S-01~S-08 | 满足 |
| 调研记录齐全（访谈对象/场景/原始诉求） | §1.2/§1.3/§1.4；**原始诉求为反向补课，真实访谈待裁决** | **部分**（见 §2.3、OPEN-08，需主理人确认是否阻塞） |
| **v1.1 新增需求的验收闭合** | REQ-036~039 各有 AC-F-36~39（+ AC-A-19）与 TC 用例；BR-026~029 / EX-022~025 均已登记 | **满足**（4 条均有唯一 ID、优先级 M、可判定验收标准、实现证据） |

---

## 6. 需求变更管控

### 6.1 变更单字段（`CR-2026-NNNN`）

> `docs/process/templates/需求变更单模板.md` 由质量保障（任务 #5）产出；本节定义字段语义，字段命名与总纲 §5.2/§7.1 一致，避免与模板冲突。

| 字段 | 必填 | 说明 | 校验 |
|---|---|---|---|
| `cr_id` | 是 | `CR-2026-NNNN`，全局唯一不复用 | 格式校验 |
| `title` / `requester` / `date` | 是 | 标题、提出人、提出日期 | — |
| `reason` | 是 | 变更动因（必须能追到 `IR-` 调研记录或线上事件/BUG 编号） | 不得写「优化体验」这类无来源描述 |
| `req_ids` | 是 | 受影响 REQ-ID 列表（新增/修改/作废） | 必须存在或标为「新增待分配」 |
| `change_type` | 是 | `new` / `modify` / `deprecate` | 与 §1.7 状态机对应 |
| `before` / `after` | 是 | 变更前后规格原文（含 BR/EX 编号） | 逐字，不得概述 |
| `impact_docs` | 是 | 受影响的 `02`~`09` 文档清单 | 至少列出 `02` |
| `impact_code` | 是 | 受影响模块 + `文件:行号` | 必须给行号 |
| `impact_tests` | 是 | 需新增/修改的测试用例与回归范围 | 关联 `TC-` / 现有测试名 |
| `db_change` | 是 | 是/否；若是必须附迁移 SQL + 回滚 SQL + 兼容性分析 + 验证 SQL | 总纲 §7.3 |
| `security_change` | 是 | 是/否；若是必须过 G5 独立阻塞权 | 总纲 §4 G5 |
| `version_bump` | 是 | 是否需要递增 `APP_VERSION` 及四处同步 | §6.4 |
| `priority_recalc` | 是 | 按 §1.6 复算总分与 MoSCoW 档 | 分数可复算 |
| `risk` / `rollback` | 是 | 风险 + 回退方案（§7.3） | — |
| `approver` / `decision` | 是 | 审批人与结论（通过/驳回/挂起） | 见 §6.3 |

### 6.2 影响分析维度（逐维必答）

| 维度 | 必答问题 | 关联门禁 |
|---|---|---|
| 需求 | 哪些 REQ 被改/新增/作废？追溯链是否仍闭合？ | G0 |
| 规则 | 哪些 BR / EX 需要增删改？提示文案是否同步（§4.8）？ | G0 |
| 页面 | 哪些视图受影响？是否需改 `renderView` + `NAV` + `dispatch` 三处？ | G1 / G3 |
| 接口 | `/api/*` 是否变化？错误码映射（`friendlyError`）是否需增项？ | G1（06） |
| 数据 | 表/字段/索引是否变化？迁移与回滚 SQL？`COLUMNS` 是否同步？ | G1（05） |
| 权限 | `policy.py` 策略是否变化？是否触及 §3.5 的「刻意取舍」？ | G5（08） |
| 测试 | 新增哪些 `TC-`？回归范围是 L0/L0b/L1 哪几层？`app-boot-test` 是否必跑？ | G4（07） |
| 部署 | 是否影响蓝绿前提 / 构建标识 / 版本串？ | G6（09） |
| 非功能 | 是否破坏离线/零依赖/单服务器/数据可携带（§4.9）？ | G0 |

### 6.3 审批与流程（RACI，极小团队版）

| 步骤 | 执行 | 终责(A) | 依据 |
|---|---|---|---|
| 1 提交 CR | 提出人（用户或 AI 协作体） | 用户 | 总纲 §6「需求调研与优先级 A/R」 |
| 2 影响分析 | 产品经理 + 相关工程角色 | 产品经理 | §6.2 |
| 3 优先级复算 | 产品经理 | 用户 | §1.6 |
| 4 审批（G0 放行权） | — | **用户（李鹏 / Leo）** | 总纲 §4「G0/G1/G6 由用户拍板」 |
| 5 若触及安全 | — | **安全工程师（G5 独立阻塞权，不可被赶进度覆盖）** | 总纲 §4/§6 |
| 6 落地与版本串同步 | 开发 | 开发自审（G2） | §6.4 |
| 7 验收单签字 | 测试 / 安全 / 运维 | 各门禁责任人 | 总纲 §4 |

### 6.4 与版本串纪律挂钩（严格对应 GAP-14 / BR-025）

**物理位置共 4 处**（README 称「三者」是指前端 3 处，服务端单列；总纲 §7.2 统一称「四处同步」。本文采用「前端 3 处 + 服务端 1 处 = 4 处」的表述，两者不矛盾，建议主理人在总纲/README 对齐措辞 —— OPEN-07）：

| # | 位置 | 当前值 | 证据 |
|---|---|---|---|
| 1 | `js/config.js` 的 `APP_VERSION` | `20260917a` | `js/config.js:170` |
| 2 | `js/app.js` 各模块 import 的 `?v=`（5 处） | `20260917a` | `js/app.js:15,16,24,29,33`（另 `js/views.js:16,21` 同版本） |
| 3 | `index.html` 的 css/js 引用 | `20260917a` | `index.html:9,64` |
| 4 | `pyapp/__init__.py` 的 `APP_VERSION`（服务端） | `20260917a` | `pyapp/__init__.py:20`；`pyapp/config.py:14,55` 引用它 |

| 挂钩规则 | 内容 |
|---|---|
| 触发 | 任何前端改动（`js/` / `css/` / `index.html`）必须在 CR 中把 `version_bump` 置为「是」并递增四处 |
| 门禁 | 四处不相等 → **G2 不通过**（总纲 §4 G2 阻塞条件、§7.2） |
| 构建标识 | 发布另以 `GTD_BUILD` 注入镜像，切流前核对 `/api/health` 回传值；端口通 ≠ 版本对（总纲 §7.5、`config.py:57-61`） |
| 建议 | 把「四处字符串相等」做成 L1 自动化断言（总纲 §10 增强版，本期不做） |
| 文档版本 | 本文自身版本用 `v1.0`（文首），每次 CR 落地后按 `v1.1 / v2.0` 递增并在 §9 变更记录登记 |

### 6.5 需求作废 / 回退规则（如何作废一条需求）

| 规则 | 内容 |
|---|---|
| R1 不删除 | 需求条目**永不物理删除**，只把状态置 `deprecated`（§1.7）；保留原文与作废理由 |
| R2 不复用编号 | `REQ-NNN` 一旦分配，作废后也不得再分配给其它需求 |
| R3 必走 CR | 作废必须开 `CR-`，填写 `before` 全文、`reason`、`impact_*`，经用户审批 |
| R4 连带清理 | 同步标注其 BR / EX / 验收项 / 测试用例为「失效」，但同样不删；在 §5 对应行打「已作废（CR-xxxx）」 |
| R5 追溯链保留 | 历史实现若仍在代码中，需在 §2.1 / §3.5 注明「需求已作废但实现仍在」，防止文档与代码漂移 |
| R6 文档版本 | 作废后本文版本递增，§9 变更记录写明 `REQ-xxx deprecated by CR-xxxx` |
| R7 代码回退 | 若作废伴随代码回退，版本串四处仍须同步递增（回退也是改动），并重新走 G2~G6 |

---

## 7. 严格模式六项必填（单独成节）

> 依据 `HARD_GATES` 与所加载 7 个 Skill 的「高风险强制门禁」。总纲 §9 把六项分散到各文档：本文承担**需求侧**部分，安全/运维/测试的完整台账分别归 `08`/`09`/`07`，本文不越界重复，只给索引与需求视角。

### 7.1 风险清单（需求侧）

| RISK-ID | 风险 | 影响范围 | 触发条件 | 严重程度 | 缓解 / 处置 | 证据 |
|---|---|---|---|---|---|---|
| RSK-01 | 文档与代码的「刻意取舍」被误改 | 看板流转、权限语义 | 后来者把 S-01/S-02/S-04 当 bug 顺手修 | **高** | §3.5 登记 + `04`/`05` 标注「改动前必读」 + CR 强制影响分析 | 总纲 §2、GAP-03/09 |
| RSK-02 | WIP / SLA / 状态机的「名不副实」被当作已兑现能力对外承诺 | 用户预期、验收结论 | 未按 OPEN-01/02/03 裁决就写进验收 | **中高** | 本文标「软提示 / 无后端逻辑」，验收按当前行为判定；改语义须走 CR | GAP-04/07/08 |
| RSK-03 | 无真实调研记录导致需求基线只是「代码的镜像」 | 需求完整性 | 把反向补课当成已完成的调研 | **中** | §2.3 明确标注、OPEN-08 交裁决；§1 给出可执行 SOP | GAP-01 |
| RSK-04 | 「离线可用」措辞过宽 | 用户预期 | AI 功能被理解为离线可用 | **中** | NFR-01 收窄为「核心功能离线」、OPEN-05 | GAP-11 |
| RSK-05 | `--import` 语义误用导致数据叠加 | 数据完整性 | 灾难恢复未先删库 | **高** | NFR-04 / EX-021 / `09 运维手册` SOP | 总纲 §7.4、GAP-15 |
| RSK-06 | 并发编辑无冲突策略 | 数据完整性 | 两人同改一张卡片 | **中** | NFR-06 判不通过、Q17 访谈、OPEN-06 | GAP-16 |
| RSK-07 | owner/admin 权限边界含糊 | 权限治理 | 按产品描述推断出代码没有的能力 | **中** | §3.5 S-05、OPEN-09 | GAP-10 |
| RSK-08 | 匿名 `app_logs` 不可读导致登录失败排障无证据 | 可观测性 | 登录前失败 | **中** | S-07、§7.5 监控点、OPEN-10 | `policy.py:209-215` |
| RSK-09 | 版本串口径「三处 vs 四处」 | 变更门禁 | CR 引用错口径 | **低** | §6.4 统一、OPEN-07 | GAP-14 |
| RSK-10 | 首次上服务器前未跑零停机前提测试 | 发布可用性 | Linux 写锁行为未实测 | **高** | 总纲 §1 唯一阻塞项、AC-R-02 | `docs/零停机发布方案.md:485-487` |

### 7.2 测试用例清单（需求视角，正式台账归 `07`）

| 类别 | TC-ID | 用例要点 | 关联 REQ / BR / EX | 现有锚点 |
|---|---|---|---|---|
| 正常 | TC-FLOW-001 | 建团队→建工单→推进 6 列→完成→复盘，链路贯通 | REQ-001/005/006/013 | `test_10`,`test_26` |
| 正常 | TC-FLOW-002 | 快速收集→厘清→下一步→今日执行→完成 | REQ-017/018/024 | `.smoke/app-boot-test.mjs` |
| 正常 | TC-FLOW-003 | 派活到成员→个人「团队分配给我的」可见→回填进度 | REQ-019/022 | `views.js:100,643-670` |
| 边界 | TC-BND-001 | WIP 上限设为 0 / 99 的展示与保存（`clamp(0,99)`） | BR-012 | `app.js:1199`、`test_25` |
| 边界 | TC-BND-002 | 11 个状态逐一渲染；`done`/`archived` 不计入 `ACTIVE_STATUSES` | BR-011 | `config.js:37-48`、`views.js:44,108` |
| 边界 | TC-BND-003 | 7 类票种逐一建立；`unplanned` 泳道仅 request/incident | BR-014 | `config.js:21-29` |
| 边界 | TC-BND-004 | 请求体恰好 8 MiB / 超过 8 MiB | EX-012 | `config.py:103` |
| 边界 | TC-BND-005 | 同周期复盘重复保存 2 次 | BR-009 / EX-019 | `test_26` |
| 异常 | TC-EXC-001 | 服务未启动、请求超时、断网 | EX-001/002/018 | `api.js:352-355` |
| 异常 | TC-EXC-002 | 22 个错误码逐一触发并核对 `friendlyError` 文案 | EX-003~015 | `api.js:347-385` |
| 异常 | TC-EXC-003 | 权限拦下返回空数组（非异常）与 `assertAffected` 提示 | EX-016 / BR-004 | `README.md:87` |
| 异常 | TC-EXC-004 | 目录穿越 `/js/../server.py` 被拒；无目录列表 | EX-020 | `test_30`,`test_31` |
| 异常 | TC-EXC-005 | 未删库直接 `--import` 的残留叠加（应被 SOP 拦住） | EX-021 / BR-022 | 总纲 §7.4 |
| 权限 | TC-AUTH-001 | 越权读/写/插入非成员团队 | REQ-026 / EX-009 | `test_14`~`test_16` |
| 权限 | TC-AUTH-002 | 伪造 `owner_id` / 未知字段 / 自行提权 | REQ-027 / BR-002/008 | `test_17`,`test_18`,`test_22` |
| 权限 | TC-AUTH-003 | 停用成员立即失权且不可重入 | REQ-004 / BR-007 | `test_20` |
| 权限 | TC-AUTH-004 | 他人复盘不可改、进度日志删除需 owner 或 admin | BR-009 / BR-010 | `test_21`,`test_27` |
| 权限 | TC-AUTH-005 | 跨站写 / 非 JSON Content-Type / 无 CORS 头 | REQ-029 | `test_32`~`test_36` |
| 权限 | TC-AUTH-006 | 伪造 XFF 绕过限流（默认关代理信任） | BR-017 | `test_40`,`test_forwarded_for_is_honoured_when_proxy_trusted` |
| 并发 | TC-CNC-001 | 两进程共用一个 SQLite：并发写零失败、数据互见、会话跨进程有效 | REQ-034 / NFR-05 | `tests/test_zerodowntime.py` 9 项 |
| 并发 | TC-CNC-002 | 蓝绿切换时第二进程启动不重跑 DDL | EX-017 / BR-020 | `db.py:439-487`、`test_init_db_is_idempotent` |
| 并发 | TC-CNC-003 | 两人同改一张卡片的后写覆盖行为（**需先定义期望**） | NFR-06 / GAP-16 | 暂无（OPEN-06） |
| **异常** | **TC-EXC-006** | 点击「修改密码」→ 表单可见 → 端到端改密成功（**用户可达性**） | REQ-036 / BR-026 / EX-022 / AC-F-36 | 现有 46 项 `app-boot-test.mjs` 未覆盖（GAP-17） |
| **边界** | **TC-BND-006** | 网络延迟下连点「保存 / 创建」2 次，只产生 1 条记录 | REQ-037 / BR-027 / EX-023 / AC-F-37 | 暂无（GAP-18） |
| **权限** | **TC-AUTH-007** | 被权限拦下的 update 返回 `rows: []` 时，前端不得提示成功 | REQ-038 / BR-028 / EX-024 / AC-A-19 | `js/app.js:1046-1047` 当前违反（GAP-19） |
| **正常** | **TC-FLOW-004** | 设置筛选后跨视图切换，不出现「假空 / 队列已清空」 | REQ-039 / BR-029 / EX-025 / AC-F-39 | 暂无（GAP-20） |
| **权限（待裁决）** | **TC-AUTH-008** | 任意登录用户调用 `GET /api/export` 的实际返回范围（含 `users` / `settings`）——**只记录现状，不断言通过/失败** | PQ-02 / REQ-031 | `pyapp/httpd.py:726-728`、`pyapp/store.py:336,339-352`、`test_37` |
| **边界（待裁决）** | **TC-BND-007** | 持续活跃用户在第 31 天是否仍保持登录（绝对 30 天 vs 不活跃 30 天）——**只记录行为，不断言** | PQ-04 / REQ-025 | `pyapp/security.py:27,107`、`pyapp/httpd.py:255,268-270` |

> 末两条标注「待裁决」：按主理人要求，**产品语义未拍板前不得写成「已验证缺陷」**，测试用例只负责把行为观测出来，判定口径由 §8.1 裁决后补。

### 7.3 回滚方案（需求变更的回退）

> 与 `09` 的「发布回滚」不同，本节指**需求/文档层**的回退：版本号、文档版本、如何作废一条需求。

| 场景 | 回退动作 | 版本号规则 | 作废规则 | 证据 / 依据 |
|---|---|---|---|---|
| 新增需求未通过评审 | 状态 `draft → rejected`，条目保留 | 文档版本**不递增**（未进基线） | 不分配 REQ-ID | §1.7 状态机 |
| 已基线需求需回退到旧规格 | 开 `CR-`，`before` 填旧规格全文，状态 `baselined → changed → baselined` | 本文 `v1.0 → v1.1`；§9 登记 | 旧版本保留可读，不删除 | §6.1 / §6.5 R1 |
| 彻底作废一条需求 | 状态 → `deprecated`，连带 BR/EX/验收/用例标「失效」 | 本文版本递增 | **编号不复用**、条目不删除 | §6.5 R1/R2/R4 |
| 作废但实现仍在代码里 | 在 §2.1 / §3.5 注明「需求已作废但实现仍在」 | 同上 | 防止文档与代码漂移 | §6.5 R5 |
| 变更伴随代码回退 | 代码回退仍是改动：**四处版本串同步递增**，重走 G2~G6 | `APP_VERSION` 递增（**不回退版本号**） | 遵循 BR-025 | §6.5 R7、总纲 §7.2 |
| 数据库变更回退 | 每次迁移必须配回滚 SQL + 兼容性分析 + 验证 SQL；破坏性变更拆三次发布 | — | — | 总纲 §7.3、`05` |
| 发布级回退 | `deploy/rollback.sh`（零停机，状态互换，可「回滚掉这次回滚」） | `GTD_BUILD` 指向上一版 | — | `README.md:117,221`、`docs/零停机发布方案.md` |

**文档版本规则**：本文 `vMAJOR.MINOR`。新增/修改需求 → `+0.1`；编号规则或章节结构变更 → `+1.0`；纯错字修正 → 不递增但在 §9 记一行。每次递增必须同步更新文首「版本 / 日期」与 §9 变更记录。

### 7.4 验收标准

| 层级 | 落点 | 判定 |
|---|---|---|
| 功能 / 接口 / 上线三层 | 本文 §5.1 / §5.2 / §5.3 | 每条可判通过/不通过，回指 REQ-ID |
| G0 门禁自检 | 本文 §5.4 | 6 项判定标准逐条核对（其中「原始诉求」部分满足，见 OPEN-08） |
| G1~G6 完整判定 | `07 测试体系与各阶段验收标准.md` + `templates/阶段验收单模板.md` | 由质量保障承接，本文不重复 |
| **本文自身交付验收** | 下列勾选清单 | 全部勾选才算本文交付完成 |

- [x] 每节都有可落地表格（§1~§8 共 40+ 张表）
- [x] ≥ 15 个访谈问题（§1.3 共 20 题）且覆盖值班交接/告警转工单/变更窗口/SLA/跨团队协作/离线
- [x] ≥ 10 条带证据的差距（§2.2 共 **20 条**，逐条标注三档；v1.1 新增 GAP-17~20 均已抽验行号）
- [x] 角色 × 资源 × CRUD 矩阵从 `policy.py` 逐条还原并带行号（§3.3）
- [x] `README.md:87-90` **五处刻意取舍**如实呈现、未修正、未建议修正（§3.5 对照表）
- [x] 功能清单按团队域/个人域/系统域分组且带 REQ-ID（§4.3 共 **39 条**，含 4 条治理元需求 REQ-036~039 + 统计表）
- [x] BR / EX 表带触发条件、处理逻辑、提示文案原文（§4.7 **29 条 BR** / §4.8 **25 条 EX**）
- [x] 三层验收标准与 REQ-ID 对应（§5，含 AC-F-36~39、AC-A-19 与 46 项测试漏检的回归说明）
- [x] 变更单字段、影响维度、审批、版本串挂钩、作废规则（§6）
- [x] 严格模式六项必填单独成节（§7）
- [x] **4 条前端已验证差距**与 **6 个后端产品语义待裁决项**已整合，后者全部标「待确认」而非「已验证缺陷」（§2.2 / §8.1）
- [x] 未实际验证的部分明确标注（§2.3 / §5 各处；转交而本次未复核的 CSS 行号与计数亦已标注来源）
- [x] 未修改任何业务代码，未写他人产出文件

### 7.5 日志与监控点（需求视角，完整台账归 `04`/`09`）

| 监控点 | 现有机制 | 需求侧要求 | 证据 |
|---|---|---|---|
| 运行日志 | `app_logs` 表，前端 `log()` 缓冲 8 条或 1200ms 刷入；只写不改删 | 需明确「匿名写入的日志谁可读」（S-07 / OPEN-10） | `js/api.js:265-285`、`pyapp/policy.py:208-221`、`db.py:272-284` |
| 服务健康 | `GET /api/health` 回传 `version` + `build` | 切流前核对 `build`；前端 `health()` 作启动门禁 | `httpd.py:816`、`config.py:55-61,122-136`、`api.js:314-317` |
| 请求日志 | `httpd.py:139` 记录 `METHOD path -> status` | 需求侧不新增指标，交 `09` | `pyapp/httpd.py:139` |
| 登录限速 | 同一 (邮箱, IP) 15 分钟 10 次 | 监控 `RATE_LIMITED` 触发量（EX-008） | `README.md:102-104`、`pyapp/security.py:116` |
| 权限拒绝 | 返回空数组（**不产生错误日志**） | 「无权限」与「不存在」在日志层不可区分，排障需知悉 | `README.md:87`、`policy.py:13-14` |
| 发布连续性 | `probe.sh` 持续打流、`watchdog.sh` 自愈 | `probe` 零失败、最大间隔 ≤ 3000ms | 总纲 §4 G6、`README.md:223-225` |
| 版本一致性 | 登录页右下角展示 `APP_VERSION` | 四处相等是 G2 硬门禁；建议做自动化断言 | `config.js:163-170`、总纲 §10 |
| 需求侧监控 | 文档与代码一致性季度巡检 | 漂移记入 `01 现状评估`（总纲 §8 第二步） | 总纲 §8 |

### 7.6 失败处理方案

| 失败场景 | 判定信号 | 处理动作 | 谁负责 | 依据 |
|---|---|---|---|---|
| 调研样本不足 / 无法接触真实运维团队 | `IR-` 记录数 < 8 或对象未覆盖 IT-01~IT-04 | 不把推测当需求依据；在 §2.3 标注；把「反向补课」结论定为**临时基线**，OPEN-08 交裁决 | 产品经理 | §1.5「推测不计入依据」 |
| 访谈结论互相冲突 | `IR.conflict_with` 非空 | 并列保留两条原话，不合并；提交用户按 §1.6 复算裁决；结论写入 CR | 用户（A/R） | §1.5 / 总纲 §6 |
| 代码与文档不一致 | 季度巡检发现 §3.3 与 `policy.py` 不符 | 先判定是「代码漂移」还是「刻意取舍被改」：后者按 RSK-01 立即回滚代码并复核 §3.5 | 产品经理 + 安全 | 总纲 §8、RSK-01 |
| 需求无验收标准 | §5.4 自检出现空白 | **阻塞 G0**，不得进入 G1 | 用户（G0 放行权） | 总纲 §4 G0 阻塞条件 |
| 权限矩阵与代码不一致且未标注刻意取舍 | §3.3 核对失败 | **阻塞 G0**；补 §3.5 登记或修正文档 | 用户 | 总纲 §4 G0 |
| 证据不足（无法给出 `文件:行号`） | 任一 GAP 条缺证据列 | 降级为【推断】或【未验证】，不得写成【已验证】 | 产品经理 | §0 口径约定 |
| 测试未跑 / 未全绿 | AC-A / AC-R 无命令输出 | 不得宣称通过；按总纲 §4 G4「任一断言失败」阻塞；首次上服务器先跑 `test_zerodowntime.py`，不绿用 `release.sh --stop-first` | 测试 / 运维 | 总纲 §1/§4/§12 |
| 需求变更影响面评估不出来 | CR 的 `impact_code` 无行号 | 驳回 CR，补齐后重提；禁止「先改再说」 | 产品经理 | §6.1 校验 |
| 需求作废后实现仍在 | §6.5 R5 未登记 | 补登记，防止文档与代码漂移 | 产品经理 | §6.5 |
| 发布失败 | `probe.sh` 失败 / 构建标识不符 | `release.sh` 以 exit 3 结束；`rollback.sh` 零停机回退；需求侧不开新 CR 直到复盘完成 | 运维 | 总纲 §4 G6、`README.md:117,221` |

---

## 8. 待确认项与假设（需主理人裁决）

> 以下均为本文**不擅自决定**的事项。裁决结论应写入 CR 并回改本文对应章节。总纲 §12.3 已列出的 3 个开放问题（自助注册是否收紧、`.smoke/` 云端脚本清理、真机浏览器测试）属工程侧，本文不重复，仅交叉引用。

### 8.1 产品语义待裁决清单（v1.1 · 后端实测转交 · 全部「待确认」）

> 来源：后端工程师寇豆码实测转交（主理人中转）。**这 6 项一律标注「待确认」，不是「已验证缺陷」**：代码行为本文已逐条抽验（`文件:行号` 见下表），但「这是不是产品本意」因项目从无 PRD、无需求编号而**无人能拍板**——这本身就是 GAP-02「不可追溯」最直接的证据。裁决权归用户（总纲 §4：G0/G1/G6 由用户拍板），涉及权限 / 数据导出的须经安全工程师 G5 独立阻塞权。
> **本文不给出倾向性结论、不建议修正**，只把备选口径与影响面摆清，供主理人在最终交付时提交用户拍板。

| 编号 | 问题 | 代码现状与证据（已抽验） | 备选口径 | 影响的需求 ID | 裁决人 | 状态 |
|---|---|---|---|---|---|---|
| **PQ-01** | team `admin` 能否任意改 `team_members.role`？实测可把自己从 member 提为 owner 并降级原 owner——这是否为云版刻意语义？ | `pyapp/policy.py:162-170`：`update_using` 允许 `is_admin`，`update_check` 在 `ctx.is_admin(tid)` 时直接 `return True`（`:167-168`），**不校验角色变更后的 owner 数量**；`pyapp/httpd.py:648-671` `h_rows` 仅原样返回 `{'rows': rows}`，不校验角色语义；对照 `pyapp/policy.py:172-176` 仅 delete 拒绝 `role=='owner'` 行。「admin 可自提并降级原 owner」为后端实测结论 | A 云版刻意语义（admin 全权管成员）→ 保留并写入 `04 TDS「改动前必读」`；B 需约束「每团队至少 1 名 owner」「仅 owner 可改角色」→ 改 `policy.py` + 加后端校验 + 补越权用例 | REQ-003 / REQ-026 / BR-008 / S-05 / TC-AUTH-002 | 用户 + 安全工程师（G5） | **待确认** |
| **PQ-02** | `GET /api/export` 是否本就打算让**任意登录用户**整库备份？当前导出含 `settings.llm_api_key` 明文与 `users.password_hash` | `pyapp/httpd.py:726-728`：`h_export` 仅 `handler._require_user(conn)`，**无 admin/owner 判定**，直接 `store.export_all(conn)`；`pyapp/store.py:336` `BACKUP_TABLES = tuple(COLUMNS.keys()) + ('users','settings')`；`:339-352` 逐表 `SELECT *`；`README.md:45` 原句「整库导出（备份，含口令散列，注意保管）」；`test_37_export_requires_login` 只验证「需登录」未验证「谁能导」 | A 任意登录用户可整库导出（单机自持、拿到账号即信任）→ 保留，但须在 PRD/接口契约写明边界；B 管理员专用；C 仅导出本人可见范围（按 `policy.py` 过滤）。**若选 B/C，须补 BR-030「导出权限边界」并改 `06` 接口契约** | REQ-031 / BR-022 / EX-021 / NFR-04 / TC-AUTH-008 | 用户 + 安全工程师（G5） | **待确认** |
| **PQ-03** | `GET /api/auth/check-email` 是否接受「邮箱可枚举」？它免鉴权、无限速，与 `h_login` 刻意的防枚举设计矛盾 | `pyapp/httpd.py:528-540`：`h_check_email` **无 `_require_user`**，返回 `isExistingUser` 布尔 + `signupAllowed` + `signupCodeRequired`；`:820` 路由注册；`pyapp/security.py:116-119` 登录限速（`WINDOW_SECONDS`/`MAX_ATTEMPTS`）只作用于登录路径。「`httpd.py:592-595` 为 `h_login` 防枚举设计」引自后端实测转交，本次未逐行复核该行号 | A 接受枚举（注册体验优先，本地部署风险低）；B 加限速 / 统一响应消除枚举；C 移除该端点并让注册流程直接返回冲突 | REQ-025 / REQ-028 / BR-017 / EX-008 | 用户 + 安全工程师（G5） | **待确认** |
| **PQ-04** | 会话有效期：注释写「30 天不活跃失效」，实现是**绝对 30 天**，哪个是产品意图？ | `pyapp/security.py:27` `SESSION_TTL_SECONDS = 30 * 24 * 3600  # 30 天不活跃即失效`；`:107` `expires = datetime.fromtimestamp(now + SESSION_TTL_SECONDS, ...)` 创建时一次算定；`pyapp/httpd.py:255` 按 `expires_at <= now` 判过期；`:268-270` 节流 300s 只 `UPDATE sessions SET last_seen_at`，**不续 `expires_at`**；`pyapp/db.py:52-54` 会话表字段 | A 绝对 30 天（定期重登，安全优先）→ 仅修正注释措辞；B 不活跃 30 天（滑动续期）→ 改实现，须评估会话表与兼容性 | REQ-025 / BR-018 / NFR-07 / TC-BND-007 | 用户 + 安全工程师（G5） | **待确认** |
| **PQ-05** | 近期是否有**新增列 / 新增表**计划？当前 `schema_present()` 会整体跳过 DDL，加列不生效 | `pyapp/db.py:439-449` `schema_present` 用 `set(EXPECTED_TABLES).issubset(...)` **只判表是否存在**；`:452-487` `init_db` 一旦结构建全即 `return`，不执行任何 DDL；`:380-382` `EXPECTED_TABLES`；总纲 §7.3 expand/contract 纪律 | A 本期无结构变更 → 只需把该约束写进 `05 数据模型`；B 有变更计划 → 必须先备迁移 SQL + 回滚 SQL + 验证 SQL（总纲 §7.3、G1 阻塞条件），并评估 `schema_present` 短路对蓝绿发布的影响 | REQ-027 / REQ-031 / REQ-034 / BR-020，及全部涉及数据模型的 REQ | 用户（排期）+ 数据建模师 / 架构师（技术约束） | **待确认**（技术约束已验证，产品排期未定） |
| **PQ-06** | 公网实例是否**默认关闭自助注册**或强制 `GTD_SIGNUP_CODE`？当前默认 `allowSignup=True`，拿到链接的人都能建号 | `pyapp/config.py:80-85`：`allow_signup = _env_bool('GTD_ALLOW_SIGNUP', True)`（`:83`）、`signup_code` 默认空串；`README.md:42-43` `--no-signup` / `--signup-code`；`README.md:147-148` 原句「公网实例请务必设置 `GTD_SIGNUP_CODE`，或 `GTD_ALLOW_SIGNUP=0` 关闭自助注册」；`pyapp/httpd.py:528-540` check-email 回传 `signupAllowed` | A 保持默认开放（本地/内网优先），靠部署文档提醒；B 改为默认关闭；C 检测到公网绑定（`0.0.0.0` / `PORT`）时强制要求口令 | REQ-028 / REQ-029 / EX-014 / BR-019 | 用户 + 安全工程师（G5） | **待确认** |

**裁决后的回改路径（统一，避免只改一处）**：结论写入 `CR-2026-xxxx` → 回改 §4.3 对应 REQ / §4.7 BR / §5 验收项 / §8.2 相关 OPEN → 同步 `04 TDS`（刻意取舍清单）、`06 接口契约`（PQ-02/03）、`08 安全门禁`（PQ-01/02/03/04/06）、`05 数据模型`（PQ-05）→ 涉及权限或数据导出的重跑越权用例并过 G5。

**两点交叉引用**：① PQ-06 与总纲 §12.3 已列的工程侧问题「自助注册是否收紧」是同一议题的**需求侧口径**，请主理人汇总时合并为一个结论，避免两处打架；② PQ-02 与 GAP-15 / BR-022 / EX-021 同属「数据可携带」主题，但前者问「谁能导」、后者问「怎么还原」，不要混为一谈。

**§8.1 与 §8.2 的分工**：§8.1 是后端实测提出的**产品语义**（代码如此，本意不明）；§8.2 是需求侧整理出的**范围 / 措辞 / 调研口径**开放问题。两者都需用户拍板，但证据性质不同。

### 8.2 需求侧开放问题（OPEN-01~12）

| OPEN-ID | 开放问题 | 两种选项 | 影响面 | 建议 | 关联 |
|---|---|---|---|---|---|
| OPEN-01 | **WIP 是硬约束还是软提示？** README 说「WIP 限制」，代码只标红不拦截 | A 保留软提示并**改 README 措辞**；B 加硬约束（改 `set-status`/`dispatch`，可能需后端） | 文档 / 前端 / 验收 AC-F-06 | 先裁决语义；若选 B 按 §1.6 复算后再定档 | GAP-04 / BR-012 / Q10 |
| OPEN-02 | **SLA 是否需要自动化**（自动算 `sla_due_at`、超时升级、通知） | A 明确「仅为标签」并收窄措辞；B 补后端定时任务/计算 | 后端 / NFR / 运维（定时） | 注意「单服务器 + 零依赖」约束下定时任务的成本 | GAP-07 / BR-013 / Q08/Q09 |
| OPEN-03 | **状态机是否定义合法转换**（谁能转、终态能否回退） | A 保持任意转换；B 定义转换矩阵并加校验 | 前端 dispatch / 业务规则 / 变更失败回滚场景 | 与 Q07 强相关 | GAP-08 / BR-011 |
| OPEN-04 | **`contexts` 更新含 owner 限制（云版遗留）是否保留？**（且与 delete 不一致） | A 如实保留（当前）；B 统一为与 `work_items` 一致 | `policy.py` / §3.3 / `04` / `05` | 选 B 必须走 CR 并评估对「团队情境共享」的影响 | S-02 / S-03 / Q19 |
| OPEN-05 | **「可完全离线」措辞是否收窄为「核心功能离线，AI 可选联网」** | A 收窄；B 保持 | README / NFR-01 / 用户预期 | 建议 A（GAP-11） | GAP-11 / Q16 |
| OPEN-06 | **并发编辑冲突策略**（两人同改一张卡片） | A 明确接受后写覆盖并文档化；B 加版本号/乐观锁 | 数据模型 / 前端 / NFR-06 | 需 Q17 访谈结论 | GAP-16 / TC-CNC-003 |
| OPEN-07 | **版本串口径统一**：README「三者 + 服务端」vs 总纲「四处同步」 | A 统一表述为「前端 3 处 + 服务端 1 处 = 4 处」；B 其它 | README / 总纲 / §6.4 / G2 | 建议 A，本文已按 A 写 | GAP-14 / BR-025 |
| OPEN-08 | **本次反向补课是否需要补做真实访谈？** 本文需求基线全部来自代码反推，无 `IR-` 原始诉求 | A 认可「代码反推即基线」，G0 视为满足；B 要求补访谈后才放行 G0 | **G0 门禁是否阻塞**（§5.4 唯一「部分满足」项） | 请主理人明确；若选 B，§1 的 SOP 需实际执行 | §2.3 / GAP-01 / 总纲 §8 |
| OPEN-09 | **owner 与 admin 是否需要可区分的能力边界？**（代码 `is_admin` 合并） | A 接受合并并改产品描述；B 细分权限 | `policy.py` / §3.1 / 安全 | 另需区分「团队 admin」与「实例级账号管理员」两个概念 | GAP-10 / S-05 / Q19 |
| OPEN-10 | **匿名 `app_logs` 是否需实例管理员只读视图**（否则登录前失败证据无人可读） | A 增加只读通道；B 明确「仅落库不查询」 | 权限 / 可观测性 / `08` | 与 §7.5 监控点相关 | S-07 / RSK-08 |
| OPEN-11 | **`reported_by` / `source` / `impact` / `urgency` / `waiting_for` 等字段是否暴露到表单并定规则** | A 逐字段定规则（交 `03`/`05`）；B 明确「预留不使用」 | FDS / 数据模型 / 报表 | 影响 Q02/Q05/Q14 的告警来源统计 | GAP-13 |
| OPEN-12 | **值班交接单 / 巡检模板是否需要结构化字段与「已交接」状态** | A 保持纯文本模板；B 结构化 | `on_call` / 表单 / 数据模型 | Q01/Q03/Q12 | BR-016 / SC-02 |

**本文采用的假设（若被推翻需回改）**

| 假设 | 内容 | 依据 |
|---|---|---|
| A-01 | 「小型 IT 运维团队」规模为 1 个团队 1~2 名 owner + 若干成员，非多租户 SaaS | `js/config.js:75`「所有者 1~2 人」、`README.md:7-8` 单机定位 |
| A-02 | 存量功能即需求基线，本期不新增功能、不改代码 | 任务性质（治理体系重建）+ 总纲 §8 |
| A-03 | 测试「存在且数量明确」可作验收锚点，但「全绿」需由 `07` 实际执行确认 | §2.3 |
| A-04 | 编号规则完全沿用总纲 §5.2，不新增前缀（本文内部 `GAP-`/`SC-` 等仅用于本文） | 总纲 §5.2 |
| A-05 | 「离线」指核心功能，AI 属可选联网能力（待 OPEN-05 确认） | `README.md:31` 与 `:179-183` 并存 |

---

## 9. 变更记录

| 版本 | 日期 | 变更内容 | 关联 CR |
|---|---|---|---|
| v1.0 | 2026-11-13 | 首次交付：调研方案、16 条带证据差距、角色权限矩阵、PRD 主体（35 条 REQ / 25 条 BR / 21 条 EX / 10 条 NFR）、三层验收、变更管控、严格模式六项、12 个开放问题 | — |
| v1.1 | 2026-11-13 | 增量整合（未重做已有内容）：① 新增 GAP-17~20（前端实测 4 条已验证差距，逐条抽验行号）；② 新增 REQ-036~039 治理元需求 + BR-026~029 + EX-022~025 + AC-F-36~39 / AC-A-19 + 7 条 TC；③ 新增 §8.1 产品语义待裁决清单 PQ-01~06（全部「待确认」）；④ 新增 §3.5「README:87-90 五处刻意取舍对照表」与 S-09；⑤ 补充 46 项集成测试漏检改密断链的回归说明、数字按实测修正（`assertAffected` 实为 23 处调用）；⑥ 更新 §4.3.1 统计表与 §7.4 自检清单。业务代码零改动 | 前端 / 后端工程师实测转交（主理人中转） |

---

## 附录 A · 证据索引（本次实读 / 检索）

| 文件 | 关键行号 / 内容 | 用于本文 |
|---|---|---|
| `README.md` | `:3-5` 定位与方法论；`:7-8,25-31` 零依赖；`:20-21` 必须 HTTP；`:42-48` CLI；`:56-59` 数据位置；`:69-90` 与云版本差别 + 6 条权限语义（`:85,86,87,88,89,90`）；`:94-106` 安全说明；`:108-152` 蓝绿部署；`:135-148` 两个安全开关；`:175-192` AI；`:196-234` 项目结构；`:238-265` 测试与版本串纪律 | 全文各节 |
| `js/config.js` | `:10-15` 方法论注释；`:21-29` 票种；`:37-48` 状态机；`:51` 看板列；`:54-62` DEFAULT_WIP；`:68-73` 优先级/SLA；`:76-80` 角色；`:83-90` 资源类型；`:98-104` 项目状态；`:107-112` 班次；`:118-137` 情境/领域；`:140-159` 巡检/交接模板；`:163-170` APP_VERSION | §3.1 / §4.6 / §6.4 |
| `pyapp/policy.py` | `:1-22` 文档字符串（三点语义 + 一处有意差异）；`:32-43` TEAM_KEY；`:46-63` AuthzContext；`:79-129` 谓词原件；`:136-221` 各表策略函数；`:224-267` POLICIES；`:274-319` Denied / 对外接口 / load_context | §3 全节 / BR-001~010 |
| `pyapp/db.py` | `:35-67` 系统表；`:70-285` 11 张业务表 DDL；`:300-375` COLUMNS；`:377-386` WRITABLE/EXPECTED；`:425-487` connect/schema_present/init_db；`:494-591` decode/encode/split_payload | §3.2 / §4.10 / BR-002/009/020/022 |
| `pyapp/config.py` | `:19-22` PUBLIC_ENTRIES；`:55-61` version/build；`:68-85` host/port/signup；`:87-92` trust_proxy；`:94-120` LLM；`:122-136` describe() | §4.9 / §6.4 / §7.5 |
| `pyapp/__init__.py` | `:17` 零依赖声明；`:20` `APP_VERSION='20260917a'` | §6.4 / NFR-10 |
| `pyapp/httpd.py` | `:139` 请求日志；`:317-350` do_* / 路由分发；`:815-835` ROUTES 20 条路由条目 / 19 条唯一路径 | §4.10 / AC-A / §7.5 |
| `pyapp/store.py` | `:64` select_table、`:82` load_workspace、`:106/130/150` CRUD、`:166` create_team_with_owner、`:208` join_team_by_code、`:258` save_review、`:290` write_app_log、`:339/355` export/import | §2.1 / §4.3 |
| `js/views.js` | `:44` ACTIVE_STATUSES；`:100` assignedToMe；`:108` isOpen；`:111,119` isOverdue/slaRisk；`:145,222` metrics；`:918-957` viewTeamBoard；`:1907-1935` PERSONAL_NAV/TEAM_NAV；`:1937-2011` renderSpaceTabs/TeamSelect/Sidebar；`:2017-2048` renderView；`:2051-2057` renderOnboarding；全量导出函数清单 | §4.4 / §4.6 / GAP-04 |
| `js/app.js` | `:88` normalizeView；`:121` render 分发；`:597-869` dispatch 全量 case；`:678-705` quick-done / set-status；`:1191-1205` saveWip；`:1207-1218` archiveTeam；`:15,16,24,29,33` `?v=` | §4.4 / §4.5 / GAP-04/08 / §6.4 |
| `js/api.js` | `:83-166` 认证与 CRUD 导出；`:175-209` 团队/复盘/日志；`:265-285` log 缓冲；`:288-296` describeError；`:314-317` health；`:321-343` LLM；`:347-385` friendlyError 22 错误码 | §4.8 / §4.10 / EX 全表 |
| `index.html` | `:9,64` `?v=20260917a`；`:31-34` quick-capture；`:14-62` 挂载点 | §4.4 / §6.4 / NFR-10 |
| `tests/test_api.py` | 49 个 `def test_` 名称（`:151`~`:855`），含 `test_columns_spec_matches_real_schema`、`test_init_db_is_idempotent`、`test_backup_round_trip` 等 | §5.2 AC-A 全表 |
| `docs/零停机发布方案.md` | `:464-487` §11 已验证 / 尚未验证（如实清单） | §0 / §2.3 / AC-R |
| `docs/process/00_开发流程总纲.md` | §4 G0 门禁、§5.2 编号规则、§7 变更与版本纪律、§8 反向补课、§9 严格模式六项、§12 开放问题 | 全文对齐依据 |
| `css/app.css` | `:532,535` `.bcol.is-over` / `.bcol-wip`；`:729` `.wip-fields` | GAP-04 佐证（WIP 仅视觉） |
| 全项目 Grep | `WIP` 在 `pyapp/` 零命中；`sla|cron|Timer|schedule` 在 `pyapp/` 仅字段 + `threading.Lock` | GAP-04 / GAP-07 |
| **v1.1** `js/app.js` | `:503-518` async click 委托无锁；`:670-674` 先关浮层后写；`:391-395` 180ms 延迟清空；`:603-609` nav 只清 `search`；`:863-868` change-password 分支；`:1046-1047` updateItem 无校验即成功提示；`:1439-1451` submitPassword | GAP-17~20 / BR-026~029 / EX-022~025 |
| **v1.1** `js/views.js` / `js/api.js` | `views.js:1895` 修改密码按钮（全 `js/` 零处渲染 `#pwd-form`）；`api.js:139` changePassword、`:347-385` friendlyError | GAP-17 / EX-022 |
| **v1.1** `pyapp/httpd.py` | `:255,268-270` 会话绝对过期；`:528-540` check-email 免鉴权可枚举；`:648-671` h_rows 不校验角色语义；`:726-728` h_export 仅 `_require_user`；`:824` 改密端点 | PQ-01 / PQ-02 / PQ-03 / PQ-04 / GAP-17 |
| **v1.1** `pyapp/store.py` / `security.py` / `config.py` | `store.py:336,339-352` BACKUP_TABLES 含 users/settings；`security.py:27,107` TTL 注释与实现；`config.py:83` 默认 allowSignup=True | PQ-02 / PQ-04 / PQ-06 |

---

## 附录 B · 给下游成员的输入摘要（索引）

| 下游 | 直接取用章节 | 关键输入 |
|---|---|---|
| **UI/UX 设计师（任务 #2，FDS）** | §4.4 页面清单（25+ 视图）、§4.5 主/分支/异常流程、§4.6 状态机与枚举、§4.7 BR 表、§4.8 EX 表（提示文案原文）、§4.2 MVP 三档、**§3.5 五处刻意取舍对照表** | 页面键、入口、元素、操作、跳转、状态；**新增视图必须改三处**（`renderView` / NAV / `dispatch`）；**BR-001~029、EX-001~025**；v1.1 新增 4 条界面级缺口（改密表单缺失 / 提交防重 / 成功提示诚实 / 筛选状态隔离）须在 FDS 中作为「待补交互状态」登记，见下方补充 |
| **架构师（任务 #3/#6，TDS / 接口契约）** | §3.3 权限矩阵、§3.5 刻意取舍（必须写进「改动前必读」）、§4.10 数据与接口影响、§4.9 NFR、§6.4 版本串四处 | `policy.py:行号`、20 条路由条目 / 19 条唯一路径、22 个前端映射 code、云版 RLS 对齐语义与 4 处有意差异 |
| **数据建模师（任务 #4）** | §3.2 资源清单、§4.10、BR-009/020/022、EX-017/019/021 | 14 张表、`COLUMNS` 唯一权威、唯一索引、幂等建库、`--import` 语义、expand/contract 纪律 |
| **质量保障（任务 #5）** | §5 三层验收、§7.2 测试用例清单、§7.4 验收标准、§1.5 模板字段、§6.1 变更单字段 | REQ-ID ↔ 测试名映射、49+9+80+65+46 锚点、`ACC-Gn` 验收单、G0~G6 判定 |
| **安全工程师（任务 #8）** | §3.6 权限风险点、§3.5 S-01~S-08、EX-009/011/013/020、BR-017/019/023 | 越权用例、`GTD_TRUST_PROXY` 配对、静态白名单、密钥不回显 |
| **运维工程师（任务 #9）** | §5.3 AC-R、§7.5 监控点、§7.6 失败处理、NFR-03/04/05/09 | 首次上服务器必跑 `test_zerodowntime.py`、`--import` 先删库、构建标识核对、`probe` 阈值 |
| **主理人（任务 #10）** | §0 口径、§2 差距、§5.4 G0 自检、§8 开放问题、§9 变更记录 | 需裁决 **§8.1 的 6 个 PQ 项 + §8.2 的 12 个 OPEN 项**；其中 **OPEN-08 决定是否阻塞 G0**；PQ-06 须与总纲 §12.3 工程侧问题合并结论 |

#### v1.1 给 ui-designer 的补充输入（4 条界面级缺口）

| # | 缺口 | FDS 中应登记的交互状态 / 页面 | 关联编号 | 不要做什么 |
|---|---|---|---|---|
| 1 | 用户面板「修改密码」按钮点击无反应（表单未渲染） | 用户面板需定义「改密表单展开态」：原密码 / 新密码 / 确认、校验中、成功、失败四态 | REQ-036 / BR-026 / EX-022 / AC-F-36 | 不新增功能，只补已实现端点的可达路径 |
| 2 | 写表单连点可重复提交 | 所有创建类模态需定义「提交中（禁用）」态与按钮禁用样式 | REQ-037 / BR-027 / EX-023 / AC-F-37 | 不引入新交互范式，只补状态 |
| 3 | 权限拦下时仍提示「进度已写入」 | 进度/编辑类操作的空态文案须统一为 `README.md:87` 的「可能不存在或不在你可见的范围内」 | REQ-038 / BR-028 / EX-024 / AC-F-38 | 不改写其它 22 个既有错误码文案（逐字取自 `friendlyError`） |
| 4 | 跨视图筛选串味导致列表假空 | 导航切换需定义筛选重置或「当前筛选」可见标识 | REQ-039 / BR-029 / EX-025 / AC-F-39 | 不擅自决定「筛选是否跨视图保留」（属产品语义，见 OPEN/CR） |

> 另请 FDS 注意：§3.5 对照表的 5 处权限语义是**刻意保留**，交互稿不要把它们表现成「可随手修改」的普通配置；WIP 软提示 / SLA 无自动化 / 状态机无转换约束 / `contexts` 权限不一致这 4 点须标「候选 / 待裁决」，不可画成既有能力。

---

## 附录 C · 需求评审自检清单

- [x] 是否区分核心 / 次要 / 后续（§4.2 三档 + §1.6 复算）
- [x] 是否列出关键问题（§1.3 共 20 题、§8 共 12 个开放问题）
- [x] 是否包含横向 / 纵向权限且后端校验（§3.3 / §3.4 / §3.6）
- [x] 是否覆盖开始 / 结束状态与失败分支（§4.5 主/分支/异常流程）
- [x] 是否覆盖网络 / 权限 / 数据 / 第三方失败（§4.8 覆盖自检）
- [x] 是否页面级明确、按钮 / 弹窗 / 异常可开发（§4.4 页面功能说明）
- [x] 验收是否可验证、有通过 / 失败标准（§5，Given/When/Then + 勾选）
- [x] 是否标注假设与待确认（§0 三档、§2.3 边界、§8 假设与开放问题）
- [x] 是否避免带病进入下一阶段（§5.4 G0 自检、§7.6 阻塞规则）
- [x] 是否覆盖产品 / 技术 / 部署 / 用户使用并支持后期维护（附录 B 下游索引）
- [x] 是否控制复杂度、适合 1 人 + AI 协作（§1.6 成本项、§4.2 明确不做）
- [x] 是否给出建议验证命令 / 明确「未实际验证」（§5.2 命令块、§2.3）

---

> **本文结束（v1.1）。** 本文为需求侧规格，不替代 `03`~`09`；`README.md:87-90` 五处刻意取舍已如实登记且**未修正、未建议修正**；所有「代码如此但产品语义存疑」的点（§3.5 S-01~S-09、§8.1 PQ-01~06）一律标「待确认」而非「已验证缺陷」；所有未实际验证的结论已明确标注。需主理人裁决事项见 §8（§8.1 六个 PQ 项 + §8.2 十二个 OPEN 项），其中 **OPEN-08 关系到 G0 是否放行**、**PQ-06 须与总纲 §12.3 合并结论**。业务代码零改动。

---

## 附录 D · CR-2026-001 影响修订

> 本附录**只登记"哪些原有表述已被取代"**，不重写正文。正文（§3.1 / §3.3 / §4.3 / §4.4 / §4.7 / §4.8 / §5.1 / §5.3 / §8）保留为 **v1.1 快照**，用于追溯"变更前是什么"。
> 变更依据：`docs/process/10_CR-2026-001_账号体系变更.md`（2026-11-14，用户已拍板）。
> 阅读顺序：**先读本附录，再读正文对应节**；两者冲突时**以 CR-2026-001 为准**，直到正文按 CR 完成修订。

### D.1 被取代的表述清单（逐条对照）

| # | 原表述位置 | 原表述（摘要，非逐字全文） | 被 CR-2026-001 取代为 | 取代生效时点 |
|---|---|---|---|---|
| 1 | **§3.1 角色定义** | 表内 6 行：`owner` / `admin` / `member` / 非成员 / 停用成员 / 未登录；表下注「另有『实例级管理员』概念：`--no-signup` / `--set-password`，属运行配置而非 `ROLES` 枚举，**本文不作为团队角色列入**（见 OPEN-09）」 | ① 新增**系统管理员**（实例级，`users.is_system_admin`，**不进 `ROLES`**）；② 明确「团队管理员」= `owner` + `admin`（沿用 `is_admin` 合并语义，见 CR §3、O-2）；③ 停用成员补注「**团队级**：不影响登录、不影响其建自己的团队、不影响加入其它团队」；④ 原「实例级管理员」注脚改为指向 CR 的库内身份（不再只是运行配置） | CR 批准即生效（规格层）；代码生效待发布 |
| 2 | **§3.3 权限矩阵** | 6 列角色 × 11 张资源 × 增/查/改/删 —— 这份矩阵是**团队角色 × 资源**的代码还原，且**不含**"建账号 / 改 AI 配置 / 导出 / 凭邀请码加入"这些**动作维度** | **不修订本表**（它仍是团队资源判定的有效快照）。新增的 6 身份 × 8 动作矩阵见 **CR §4.1**；两者互补：本表回答"角色能对**数据行**做什么"，CR §4 回答"身份能对**账号与实例配置**做什么" | CR 批准即生效 |
| 3 | **§4.3 功能清单 · 团队域** | `REQ-003｜成员与角色管理（增删改、防自提权、owner 行受保护）` | **修订**：追加"团队管理员可为**本人管理的团队**创建**成员账号**，角色强制 `member`"（CR §5.2 BR-030） | CR 批准即生效 |
| 4 | **§4.3 功能清单 · 系统域** | `REQ-025｜本地账号：**注册** / 登录 / 登出 / 改密（PBKDF2、会话散列、限速、改密踢会话）` | **修订**：删「注册」，改为「**由管理员建号**（随机初始密码一次性交付）+ 登录 / 登出 / **强制首登改密**」（CR §7） | CR 批准即生效 |
| 5 | **§4.3 功能清单 · 系统域** | `REQ-028｜自助注册开关与注册口令`（优先级 M） | **作废**（含 `GET /api/auth/check-email` 一并下线）。优先级重算为"不适用" | CR 批准即生效 |
| 6 | **§4.3 功能清单 · 系统域** | REQ-040 起为未分配编号（上一号为 REQ-039） | **新增 REQ-040 ~ REQ-044**：系统管理员引导 / 建团队管理员 / 团队管理员建成员 / 一次性密码 + 强制改密闸门 / 注册代码级下线 | CR 批准即生效；正文表待回填 |
| 7 | **§4.4 页面功能说明** | 「认证页｜未登录｜**登录/注册/口令/改密**｜`login` / **`register`** / `change-password`」（证据列 `js/auth-ui.js`、`js/api.js:117-144`） | 认证页**删除注册**（含"还没有账号？去注册"与两步式邮箱探测）；新增「**创建账号**」模态（系统管理员 / 团队管理员两种视图）与「**强制改密页**」；`members` 页在既有"邀请成员加入"（`views.js:1289`）之外新增"创建成员账号" | CR 批准即生效；布局与微文案交 ui-designer |
| 8 | **§4.7 业务规则 · BR-017** | 「登录限速」——同 (邮箱, 来源 IP) 15 分钟失败 10 次 | **保留登录段**；**删除注册段**（注册路径已不存在），并新增「建号接口按 (uid, IP) 限速」→ 归入 **BR-030 ④** | CR 批准即生效 |
| 9 | **§4.7 业务规则 · BR-018** | 「改密踢出其它会话」——改密成功只保留当前会话 | **保留不变**，但补一句"改密同时清除 `must_change_password`"（CR BR-032 ③） | CR 批准即生效 |
| 10 | **§4.7 业务规则 · BR-001~029** | 全部 29 条 | **不废止任何一条**（`work_items` 看板流转、`contexts` 云版形状、空数组不报错等刻意取舍全部保留）。新增 **BR-030 ~ BR-036**（建号分级 / 一次性密码 / 强制改密闸门 / 身份来源与引导态 / 最小权限 + 邀请码保留 / 停用≠封禁 / 建号结果诚实） | CR 批准即生效 |
| 11 | **§4.8 异常场景 · EX-014** | 「注册关闭 / 口令错误 →『本实例已关闭自助注册。』/『注册口令不正确。』」（关联 REQ-028） | **作废**；由 **EX-034**（旧客户端/书签调用已下线端点 → `404 NOT_FOUND`）取代。`js/api.js:375-376` 的两条映射随端点下线一并删除 | CR 批准即生效 |
| 12 | **§4.8 异常场景 · EX-006** | 「邮箱已注册 →『该邮箱已注册，请直接登录。』」（**注册**重名场景） | 注册场景随 REQ-028 作废；**同一错误码 `EMAIL_EXISTS` 在新场景下复用为 EX-026**（建号重名），文案改为「该邮箱已有账号，无法重复创建。如需加入本团队，请使用邀请码。」 | CR 批准即生效 |
| 13 | **§4.8 异常场景 · EX-001~025** | 全部 25 条 | 除 EX-014 作废、EX-006 文案改口径外，**其余 23 条全部保留**。新增 **EX-026 ~ EX-035**（邮箱已存在 / 团队管理员建管理员 / 跨团队建号 / 临时密码过期与多次登录 / 改密旧密码错 / 系统管理员被取消身份 / 引导态 / 临时密码丢失 / 旧客户端调注册 / 未指定或归档团队） | CR 批准即生效 |
| 14 | **§5.1 功能验收 · AC-F-25** | 「Given 未注册邮箱，When **注册**→登录→改密→登出，Then 每步符合预期且改密后其它会话失效」（关联 REQ-025） | **改写为**：Given 管理员建号返回的一次性初始密码，When 用它登录 → 被闸门拦到改密页 → 改密 → 登出 → 用新密码登录，Then 全链路符合预期且其它会话失效 | CR 批准即生效 |
| 15 | **§5.1 功能验收 · AC-F-28** | 「Given `--no-signup` 或错误口令，When 注册，Then 返回 `SIGNUP_DISABLED` / `INVALID_SIGNUP_CODE`」（关联 REQ-028） | **作废**；由 **AC-F-46**（`POST /api/auth/register` → `404 NOT_FOUND`，且登录页无注册入口）取代 | CR 批准即生效 |
| 16 | **§5.1 功能验收 · AC-F-03** | 「Given member，When 改自己 `role='owner'`，Then 被拒；When admin 删 owner 行，Then 被拒」（关联 REQ-003） | **保留不变**；**追加** AC-F-42 / AC-F-43 / AC-F-44（建号的三个越权边界：建管理员、跨团队、成员建号） | CR 批准即生效 |
| 17 | **§5.2 接口验收** | 无注册相关断言（现有 49 项中注册用例在 `test_03`~`test_05`） | **新增 AC-A-41 ~ AC-A-46**（一次性密码不落库/不落日志、`users` 列不可经通用 CRUD 篡改、旧槽位写入兼容、AI 配置权威来源切换、引导态错误码可区分、建号限速） | CR 批准即生效 |
| 18 | **§5.3 上线验收 · AC-R-07** | 「……公网实例**已收紧自助注册**」（REQ-035 / REQ-029） | 末句**改为**「自助注册已**代码级下线**（端点移除/`404` + 前端无入口）」；并新增 **AC-R-09 / AC-R-10 / AC-R-11**（注册下线实测、引导 SOP 全链路实测、回滚后临时密码处置演练） | CR 批准即生效 |
| 19 | **§8 待确认项** | §8.1 PQ-01~06、§8.2 OPEN-01~12 均未涉及"账号从哪来" | **追加 O-1 ~ O-7**（详见 CR §15）。其中 **O-2（"团队管理员"= owner+admin 还是仅 owner）必须在 G1 前裁决**；原 OPEN-09（是否细分 owner/admin）与本 CR 的 O-2 **强相关**，建议合并裁决 | CR 批准即生效 |
| 20 | **§2.2 差距清单 / §3.5 S-01~S-09** | 逐条登记"代码如此但产品语义存疑" | **全部保留、不改**。本 CR **不触碰** `work_items` / `contexts` 的刻意取舍（S-01/S-02/S-03）、不改 `is_admin` 的合并语义（S-05，仅在其上追加"团队管理员"这一**称谓**） | — |

### D.2 一条必须说明的边界（防止下游误读）

本 CR **没有**改变"团队内谁能对数据行做什么"的任何一条策略：
`policy.py` 的策略表（`POLICIES` 对 11 张业务表的 select/insert/update/delete）**零改动**。
它改变的是**三件"账号层面"的事**：

1. **账号从哪来**（谁有权产账号）；
2. **账号第一次怎么进系统**（一次性密码 + 强制改密闸门）；
3. **实例级身份怎么判定**（从环境变量白名单 → 库内 `is_system_admin`）。

因此：§3.3 的 11 张资源矩阵仍然是**有效且未被取代**的；若下游把 CR §4.1 的动作矩阵误当作"§3.3 的替代品"，会丢掉全部行级判定细节。

### D.3 本附录的边界声明

- 本附录**只做登记**，不修改 §1~§9 与附录 A~C 的任何原文；原文与 CR 冲突时以 CR 为准。
- 本附录**未逐条重写**受影响的 REQ/BR/EX/AC 表格 —— 这些回填工作分别在 `03`（页面）、`06`（接口）、`07`（用例）与 `02` 的下一版本（v1.2）中完成，**责任人与门禁见 CR §12**。
- 本次**未执行任何测试命令**，本附录不含任何"已通过"结论。

### D.4 契约对齐补充登记（2026-11-14，与 `06` §10 会签后）

> 本节只登记**被契约设计改动或细化**的表述，不改 D.1 的结论；冲突时以 `06` + `10_CR-2026-001` §17 为准。

| # | 事项 | 原登记 | 对齐后 | 影响本文章节 |
|---|---|---|---|---|
| D.4-1 | 注册端点形态 | 「REQ-028 作废，保留一版 `410` 语义」 | **直接移除，命中 `404 NOT_FOUND`**（`06` §10.2.2）。理由：留 403/410 就必须留 `ALLOW_SIGNUP` 开关 → "一行配置复活自助注册"的路径会长期存在 | §4.3 REQ-028 行、§5.3 AC-R-07 |
| D.4-2 | 新增页面 | 只写"新增建号模态 + 强制改密页" | 细化：**建号页两身份共用**；团队管理员**不渲染角色选择器**、团队选择器**锁死本团队**；强制改密页是 `must_change_password=1` 时**唯一**可用页面（服务端已拦，前端只是不让人白撞） | §4.4 认证页与 `members` 页两行 |
| D.4-3 | 建号失败的异常粒度 | 原按"无权限"合并描述 | **四个码分别成场景**：`FORBIDDEN`（完全无建号权）/ `NOT_TEAM_ADMIN`（定向越权）/ `TEAM_REQUIRED`（团队管理员漏带 `teamId`）/ `ROLE_NOT_ALLOWED`（试图授 `admin`/`owner`）。另 `TEAM_NOT_FOUND`（团队不存在或已归档，两者合并不区分） | §4.8 追加 EX-026~036 |
| D.4-4 | 引导态错误码 | `403 NO_SYSTEM_ADMIN` | **已落地（`06` v1.3）**：403，触发点在「调用者无任何建号权」分支内、`FORBIDDEN` 之前；**不是全局闸门**（零管理员只表示没人能建新账号，存量账号与团队管理员建成员不受影响）。与"你不是管理员"的 `FORBIDDEN`、以及 `/api/llm/config` 的 `ADMIN_ONLY` 三者保持独立 | §4.8 EX-032、§5.1 AC-F-40、§5.2 AC-A-45 |
| D.4-5 | 「邀请码加入」的用户故事 | 已存在于 REQ-002 / AC-F-02 | **需在建号失败的出口处被引用**：`EMAIL_EXISTS` 的文案必须写成"把邀请码发给对方，由**对方登录后自行加入**"（管理员无法代替对方加入，`store.py:252-295` 用的是当前会话身份） | §4.3 REQ-002（不改内容，仅补交叉引用）、§4.8 EX-026 |
| D.4-6 | "三个团队角色"的表述 | §3.1「另有实例级管理员，本文不作为团队角色列入」 | 统一为：**三级委派链 = 四层身份**（系统管理员 + `owner`/`admin`/`member`）；`ROLES` 仍只有 3 个团队角色，**不新增** `system_admin` | §3.1 |
| D.4-7 | 强制改密放行的验收口径 | 未区分"放行几条" | **可观测 8 条 / 显式登记 3 条**：`health`、`session`、`profile`、`me`、`password`、`logout`、`logs`、静态资源；实现只写 3 条常量（`password`/`profile`/`me`），其余 5 类走 `_current_user` 或不鉴权。**AC-F-45 按 8 条逐条断言** | §5.1 AC-F-45 |
| D.4-8 | 临时口令有效期 | CR 曾提 72 小时 | **本期不做有效期、不新增第三列**；寿命由"首登必须改密"闸门保证。不能用 `users.updated_at` 推算（`h_login` 的 rehash 会刷新它，`httpd.py:716-718` → 活跃用户永不过期）。将来要做须走 v3 迁移 + 独立 CR | §4.8 EX-029 |
| D.4-9 | 系统管理员的建号能力边界 | 原写「职责：创建团队管理员账号」（来自用户拍板），未细化到参数 | **已裁决（2026-11-14）：只可建团队管理员**。精确口径：`teamId` 省略 ⇒ `role` 必须省略；`teamId` 存在 ⇒ `role` 只能是 `admin`（省略即默认 `admin`）；传 `member`/`owner` ⇒ `400 ROLE_NOT_ALLOWED`。理由：给 member 会架空「团队管理员」这条委派链，且等于改动他人团队的成员关系 | §3.1 系统管理员行、§4.3 REQ-041、§4.8 EX-027 |
| D.4-10 | 建号时 `role` 与 `teamId` 的联动 | 未规定 | **对系统管理员：两者必须同现同缺**。无 `teamId` 传任何 `role`（含 `member`）→ `400 ROLE_NOT_ALLOWED`；有 `teamId` 时 `role` 省略即默认 **`admin`**（不是 member），传 `member`/`owner` → `400`。UI 必须联动：不选团队则角色控件禁用/隐藏 | §4.8 新增 EX-037 |


