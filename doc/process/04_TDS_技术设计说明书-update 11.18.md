# 04 · TDS 技术设计说明书

> GTD4Teams 治理体系重建 · 架构师（高见远）
> **版本 v2.1 ｜ 日期 2026-11-18**（v2.0：2026-11-18；上一版 v1.3 ｜ 2026-11-14）
> **v2.1 做两件事**：**（一）**把主理人 2026-11-18 的三项裁决写进正文 —— ① O-14 日志入口定为 **P2 且限定范围**（不是 P0，见 §9.8/§11/§14）；② O-19 界面归属**统一为「系统管理页」分区块**（§16.9）；③ `archived_at` 的静默丢弃记为**集成域 B3 硬前置**（§17.3，不在本批开 CR）；另把 D-1 的"性质不同"说明补进 §17.5。**（二）**按 `02` **v1.3 定稿**对齐追溯口径 —— REQ 总数 **55 → 58**（新增 REQ-056~058 三条"已实现能力补登记"），回填 §17.1 的需求归属并新增 §17.1.1（BACKFILL 三条标 **✅ 已实现**，不是 `【设计·未实现】`）；同时按类精确重算测试计数（`AccessLogTest` 8→**10**、`StorageSpecTest` 4→**8**，合计仍 **156**）。
> 执行模式：**严格模式**（命中 `HARD_GATES.md` 的「登录认证 / 权限系统 RBAC / 数据库变更 / 生产部署回滚 / 证书与密钥」五类高风险场景）
> 本文只描述**存量代码的真实架构**（反向补课），不提出推翻硬约束的重构；所有结论标注证据与验证状态。
> 上游依据：`00_开发流程总纲.md`（G0~G6、编号规则、追溯链）、`02_需求调研与PRD-update 11.18.md`（**v1.3：REQ-001~058**，其中 REQ-056~058 为"已实现能力补登记"、REQ-028 已作废但占用编号）；同级文档 `03_FDS`、`05_数据模型`、`06_接口契约`。

---

## v2.0 相对 v1.3 新增了什么（先读这一段）

v1.3 之后（2026-11-16 ~ 11-18）代码库落地了六项变更，v1.3 完全没写进去，**旧版的 §9 日志章节已整体失效**（它描述的 `print` 实现已不存在）。本版做的是"把已发生的实现补进设计"，外加一个**尚未实现**领域的设计。

| # | 本轮新增的实现 | 代码落点 | 本版落点 |
|---|---|---|---|
| 1 | 结构化访问日志（JSONL，双 handler、轮转、降级） | `pyapp/applog.py`（397 行，新模块）；`httpd.py` 的 `_reset_access_state` / `_log_access` / `send_error` | **§9 整章重写**；ADR-15 |
| 2 | `GET /api/logs`（读服务端自己的日志，仅实例管理员） | `httpd.py:1223-1332`（`h_access_logs`）；`applog.read_recent`（`applog.py:280-357`） | §9.4（新增接口章节）；ADR-16 |
| 3 | HTTPS 证书管理三端点 + 证书生命周期 | `pyapp/tls_cert.py`（561 行，新模块）；`httpd.py:1093-1136`；`views.js:2360-2470`（系统管理页） | **§6.7 新增**；ADR-17/18 |
| 4 | 账号体系（CR-2026-001）在代码中收口 | `store.create_account`（`store.py:389`）/ `grant_system_admin`（`:530`）/ `reset_password`（`:581`）/ `list_accounts`（`:637`）；`users.is_system_admin`、`users.must_change_password` | §4.8、§5.7、§5.8（旧 §4.6/4.7/5.5/5.6 保留并校正） |
| 5 | 发布锁（flock）+ 单文件 bind mount 的 inode 修复 | `deploy/release.sh:215-224`、`deploy/rollback.sh:77-81`、`deploy/watchdog.sh:44-50`；`deploy/lib.sh:343-350`（`cat >` 原地覆盖） | §3 ADR-18/19；§12.3 |
| 6 | 请求体消费一致性（早退响应必须先读掉 body） | `httpd.py` 的 `_body_consumed`（`:378,405,422`）、`_discard_body`（`:389-429`）、`_handle` 的三处早退分支（`:695,705,731,735`） | §8.5；ADR-20 |

另有**一个整域是纯设计、一行代码都没有**：

| 域 | 需求 | 状态 | 本版落点 |
|---|---|---|---|
| 集成域（本地 REST API + 内置 MCP） | **REQ-045 ~ REQ-055（11 条）** | **【设计·未实现】** —— 仓内 `pyapp/`、`js/` 无任何 `MCP` / `api_tokens` 命中 | **§16**；ADR-21 |

三句话总结本版口径：

1. **§9 是重写，不是增补**：旧文写"访问日志仅在 `status>=400` 时 `print` 一行"，该实现已被删除。
2. **§6.7 与 §16 是"设计与实现"的分界**：证书管理**已实现**（有 19 个专项测试），集成域**未实现**（连表都没有），两者混写等于骗人。
3. **行号全部重测**：旧版 §0.4 记录的基准是 `httpd.py` 1105 行，当前 1650 行；照旧行号跳转会落到无关代码上（见 §0.4）。

---

## 0. 文档控制

| 项 | 内容 |
|---|---|
| 文档编号 | `04_TDS_技术设计说明书.md`（总纲 §5.1 编号 04，责任角色：架构师，更新触发：架构 / 环境 / 分支 / 认证权限变更） |
| 版本 | **v2.1** ｜ 日期：2026-11-18（v2.1：同日按主理人三项裁决修订；v2.0：2026-11-18；v1.3：2026-11-14；v1.2/v1.1：2026-11-14；v1.0：2026-11-13） |
| 本版范围 | ① 保留 v1.3 全部有效内容（§1、§2、§4.1~4.7、§5.1~5.6、§6.1~6.5、§7、§8.1~8.4、§10~§15）；② 补写本轮 6 项已实现能力（§6.6/§6.7/§8.5/§9/§12/§13/§14）；③ 新增集成域设计（§16，标注未实现）；④ ADR 从 14 条扩到 21 条（ADR-15~21，编号连续）；⑤ 行号基准整体重测 |
| 适用代码版本 | `APP_VERSION = '20261114f'`（`pyapp/__init__.py:20`、`js/config.js:185`）。**注意**：这是"前端/服务端源码版本串"，不是发布产物标识 |
| 发布产物标识 | `GTD_BUILD`：构建时 `--build-arg GTD_BUILD=<标识>` 注入（`Dockerfile:42-47`），运行期经 `GET /api/health` 的 `build` 字段回传（`config.py:71`、`httpd.py:937`）；源码直跑默认 `'dev'`（`config.py:71`） |
| 二者语义 | `APP_VERSION` 回答"页面代码是不是最新"；`GTD_BUILD` 回答"起来的容器是不是我刚构建的这一版"。总纲 §5.2 把两者分开登记、不可互相替代；切流前核对的是 `build` 而非 `version` |
| 读者 | 用户（架构/运维终裁，G1/G6 放行权）、后端/前端工程师、数据建模师、QA、安全工程师、运维工程师 |
| 追溯链落点 | `REQ-xxx → BR/EX → 03 FDS → 本文（模块·ADR）→ 06 接口契约 / 05 数据模型 → TC-xxx → ACC-Gn-xxx → GTD_BUILD` |
| 门禁归属 | 本文是 **G1（设计）** 的入参之一；判定标准见总纲 §4 与 `07` |
| 对应 Skill | `architecture.architecture_blueprint`、`auth_architecture`、`permission_model`、`environment_strategy`、`api_standard`、`error_handling_standard`、`logging_monitoring_plan`、`security_architecture`、`cache_strategy`（本项目系统性不使用缓存，见 ADR-16）、`api.endpoint_contract_design`、`code_quality.git_branch_commit`；门禁 `HARD_GATES.md` |

### 0.1 术语表（与 `00` 总纲 §5.2 逐字对齐）

| 前缀/术语 | 含义 | 本文中的用法 |
|---|---|---|
| TDS | 技术设计说明书（本文） | 模块、ADR、环境、认证权限、横切方案 |
| FDS | 功能设计说明书（`03`） | 页面/流程与本文模块一一对应（G1 判定项） |
| REQ- | 需求编号 | 追溯链起点，由 `02` 定义（**总数 58、有效 57**：`02` v1.3 补登记 REQ-056~058，REQ-028 作废但占号不复用）。本版涉及：REQ-040~044（账号体系）、REQ-045~055（集成域，**设计未实现**）、REQ-056~058（已实现能力补登记） |
| BR- | 业务规则 | `02`/`03`；本文 §5 权限矩阵对应"数据范围规则" |
| EX- | 异常场景 | `02`/`03`/前端文案；本文 §8 与 `06` 错误码表对应 |
| TC- | 测试用例（如 `TC-AUTH-013`） | `07`；本文 §12.2 与 `06` §6 引用 |
| ACC-Gn- | 阶段验收单（如 `ACC-G4-20261113`） | 总纲 §4 每道门禁一张 |
| BUG- | 缺陷 | `07` |
| CR- | 需求变更单 | 总纲 §7.1：任何改动先开 `CR-`；本版涉及 `CR-2026-001`（账号体系） |
| `APP_VERSION` | 源码版本串（四处同步） | §7.4（本版实测 `20261114f`） |
| `GTD_BUILD` | 构建标识（镜像 tag / health / 发布结论表） | §3 ADR-09/18/19、§12.3 |
| PT（Personal Token） | 集成域的长效机器令牌 | **仅出现在 §16（设计未实现）**，勿与 `gtd_session` 会话令牌混用 |
| `.pending-apply` | 证书"待应用"标记文件 | §6.7（后端只放标记，宿主侧 watchdog 消费） |

### 0.2 证据与验证状态标记（全文统一）

> **重要声明**：本次工作是**纯文档撰写，未修改任何业务代码，也未重新执行任何测试**。凡标 `【测试·有名】` 者，指"仓库中存在该用例（给出类名/用例名）"；「全绿」是**上游上下文转述，非本次复跑结果**（见 §13 U-10）。凡需 Docker / 服务器 / 真实上游才能确认的，一律标 `【未验证】`，汇总到 §13。

| 标记 | 含义 |
|---|---|
| `【代码·已核】` | 本次逐行核对了对应 `文件:行号`（行号基准见 §0.4），是对现状的事实陈述 |
| `【文档·原文】` | 引自 `README.md` / 既有 `docs/*.md` 原句，注明出处行 |
| `【测试·有名】` | 仓库中存在对应测试类/用例名（本版列出类名），**本次未复跑** |
| `【推断】` | 由代码/文档推导，但无专门断言直接覆盖；已写明推导依据 |
| `【未验证】` | 需服务器 / Docker / 真机浏览器 / 真实 LLM 上游，或本次根本未执行 |
| **`【设计·未实现】`** | **v2.0 新增**：只是设计，**代码不存在**。凡出现此标记的章节（§16 全篇、ADR-21）**不得**被引用为"已实现能力"，也不得作为验收证据 |

### 0.3 结论先行

| 项 | 判断 |
|---|---|
| 架构现状 | **单进程 Python 标准库单体 + SQLite 单文件 + 原生 ESM 前端**，可选 Docker 双槽位蓝绿。层次清晰、边界收敛，所有安全边界都在服务端（`policy.py` + `httpd.py`）【代码·已核：`pyapp/policy.py:267-310`、`pyapp/httpd.py:654-833`】 |
| v2.0 的架构增量 | 从"只有 HTTP 层"变成"**HTTP 层 + 三个横切模块**"：`applog`（日志）、`tls_cert`（证书）、`deploy/lib.sh` 的生成/断言函数（发布）。三者共同点：**都遵循"失败只降级、不阻断主流程"**（ADR-15/17/18） |
| 最大架构风险 | **仍是文档与代码漂移**，且本版实测出 5 处新的硬漂移：`APP_VERSION` 早已是 `20261114f`（v1.3 写 `20260917a`）；`test_api.py` 156 个用例（v1.3 写 49）；`ROUTES` 24 条（v1.3 写 20）；`js/api.js` 30 个导出（v1.3 写 27）；`GTD_ALLOW_SIGNUP` 已无任何行为（v1.3 仍列为有效开关）。详见 §0.5 与 §7.4 |
| 次大架构风险 | ① 蓝绿期间登录限速状态不共享（进程内字典，`security.py:162-199`）；② **集成域一旦打开，Bearer 令牌就是"等同于账号的长期凭据"**，其权限面等于账号全量（REQ-047 复用 `policy` 的必然后果）→ 因此设计上"默认关闭 + 只绑 `127.0.0.1` + 作用域"三条缺一不可（§16.3） |
| 是否阻塞下一步 | 不阻塞 G1 之外的门禁。**集成域 11 条（REQ-045~055）连 BR/EX/AC 正文条目都还没有**，按 `02` §4.3 注 3「须在同一变更单中补齐，否则本域不得进入 G0」—— 本版只交付技术设计，BR/EX 补齐是产品侧的活 |
| 本次交付边界 | 只写一个新文件 `04_TDS_技术设计说明书-update 11.18.md`；**不改 `pyapp/`、`js/`、`deploy/`、`tests/` 任何代码，不改旧版文档，不改其它文档** |

### 0.4 行号基准（v2.0 重测，旧版行号已整体失效）

> **必读**：v1.3 之后 `httpd.py` 增长约 545 行、`store.py` 增长约 436 行，v1.x 全文行号**普遍比当前文件小 40~550 行**。照旧行号跳转会落到无关代码上（例：v1.3 说 `ROUTES` 在 `httpd.py:815-836`，实际在 `:1584-1614`；说 `policy.POLICIES` 在 `:224-267`，实际在 `:267-310`）。

| 文件 | v2.0 实测行数 | v1.3 记录 | 偏差 |
|---|---|---|---|
| `pyapp/httpd.py` | **1650** | 1105 | +545 |
| `pyapp/db.py` | **831** | 667 | +164 |
| `pyapp/store.py` | **919** | 483 | +436 |
| `pyapp/policy.py` | **366** | 350 | +16 |
| `pyapp/security.py` | **205** | 159 | +46 |
| `pyapp/config.py` | **210** | 176 | +34 |
| `pyapp/applog.py` | **397** | 不存在 | 新模块 |
| `pyapp/tls_cert.py` | **561** | 不存在 | 新模块 |
| `pyapp/llm.py` | **189** | 190 | -1 |
| `server.py` | **14796 字节**（未逐行核对） | 238 行 | 见锚点 |
| `js/api.js` | **486** | 385 | +101 |
| `js/views.js` | **2468** | 未记录 | — |
| `js/app.js` | **2224** | 未记录 | — |
| `js/util.js` | **365** | 不存在 | 新模块 |
| `js/auth-ui.js` / `js/config.js` | 317 / 185 | 未记录 | — |
| `deploy/lib.sh` | **598** | — | — |
| `deploy/release.sh` / `rollback.sh` / `watchdog.sh` | 401 / 209 / 253 | — | — |
| `tests/test_api.py` | **3857 行 / 156 个 `test_` 方法** | 49 项 | +107 项 |

- **约定**：v2.0 新增/改写的章节（§2.5、§2.6、§4.8、§5.7、§5.8、§6.6、§6.7、§8.5、§8.6、§9 全章、§12~§14、§16、§17）**全部使用本次实测行号**；§1、§2.1~2.4、§4.1~4.7、§5.1~5.6、§6.1~6.5、§7、§8.1~8.4、§10~§11 中的行号也已按实测校正（不校正就是把已知错误留在文档里）。
- 不确定的行号一律改用**内容锚点**（函数名/常量名），这是我给自己定的纪律：宁可写"内容锚点：`h_access_logs`"，也不写一个会漂的 `:1234`。

### 0.5 v2.0 变更清单（章节级）

| 章节 | 处理 | 说明 |
|---|---|---|
| v2.0 摘要块 / §0 | **改写** | 新增 §0.5 变更清单、§0.6 章节映射、`【设计·未实现】`标记、行号基准重测 |
| §1 | **保留 + 扩充** | C-1~C-10 原文保留；新增 C-11~C-15（日志零依赖 / 证书零依赖 / 发布互斥 / 请求体一致性 / 集成域进程内） |
| §2 | **保留 + 扩充** | 2.1 分层图加 `applog`/`tls_cert`；2.2 模块表加两行与 `js/util.js`；2.3 写链路的日志/追踪说明校正；**新增 2.5（请求体）与 2.6（监听拓扑）** |
| §3 | **保留 + 追加** | ADR-01~14 原文保留（含 ADR-04 的 v1.1 修订）；**新增 ADR-15~21** |
| §4 | **保留 + 扩充** | 4.1~4.7 原文保留并校正行号；**新增 4.8**（会话令牌 vs 机器令牌的边界） |
| §5 | **保留 + 扩充** | 5.1~5.6 原文保留并重测行号；**新增 5.7**（`_require_instance_admin` 唯一的实例级门槛）、**5.8**（三处"不可顺手优化"的代码再确认） |
| §6 | **保留 + 扩充** | 6.1~6.5 原文保留；**新增 6.6**（本轮新增配置项）、**6.7**（TLS 与证书生命周期，含 6.7.1~6.7.6） |
| §7 | **保留 + 校正** | 7.4 版本串更新为 `20261114f` 并重算落点；**新增 7.6**（日志/证书/契约的登记纪律） |
| §8 | **保留 + 扩充** | 8.1~8.4 原文保留；**新增 8.5**（早退响应与请求体消费）、**8.6**（本轮新增错误码） |
| §9 | **整章重写** | 旧文描述的是已被删除的 `print` 实现；新文按 `applog.py` 重写，并新增 9.4 接口设计、9.5 轮转容量、9.8 缺口 |
| §10 | **保留 + 扩充** | 新增 10.5（日志容量与证书有效期的非功能边界） |
| §11 | **保留 + 扩充** | 新增/调整优先级条目（集成域、日志集中化、证书轮换） |
| §12 | **扩充** | 12.1 风险表新增 R-16~R-22；12.2 测试资产按实测更新；12.3 回滚补证书/日志/发布锁 |
| §13 | **扩充** | 新增 U-11~U-15 |
| §14 | **扩充** | 新增 O-14~O-19（集成域与文档漂移相关） |
| §15 | **保留 + 更新** | 追溯接口表补 §16/§17 行 |
| **§16** | **全新** | 集成域（API / MCP）技术设计 —— **【设计·未实现】** |
| **§17** | **全新** | 本轮新增能力的追溯与验收（REQ/BR/AC 映射 + 门禁影响） |

### 0.6 章节映射（旧 § → 新 §，供 `06`/`07`/`09` 的既有引用核对）

> 本版**刻意不重排既有章节号**：`06`、`07`、`09` 里存在"见 `04` §5.2 / §7.4 / §9.3"这类引用，重排会让它们静默指错（这正是本项目反复吃亏的漂移形态）。下表用于确认"旧编号还在原位"，新增内容一律用新编号。

| 旧编号 | v2.0 位置 | 是否变动 |
|---|---|---|
| §0~§15 的章标题与主编号 | 完全一致 | 不变 |
| §9.1~§9.3（旧日志三小节） | 由新 §9.1~§9.8 取代 | **内容重写，编号复用** |
| §4.6 / §4.7 / §5.5 / §5.6（v1.1 新增） | 原位保留 | 不变（仅校正行号） |
| §12.1 / §12.2 / §12.3 | 原位扩充 | 表格增行，不改标题 |
| §14 O-1~O-13 | 原位保留 | 不变（O-9~O-13 仍是"已裁决留档"） |
| §2.5 / §2.6 / §4.8 / §5.7 / §5.8 / §6.6 / §6.7 / §7.6 / §8.5 / §8.6 / §9.4~§9.8 / §10.5 / §16 / §17 | 新增 | 全文其它文档**尚未**引用这些编号 |

---

## 1. 设计目标与约束（可验证条目）

硬约束来自任务上下文与 `README.md`，逐条给出**验证方式**（能在本机判定的标已核，需环境的标未验证）。

| 编号 | 约束（可验证陈述） | 证据 | 验证方式 | 状态 |
|---|---|---|---|---|
| C-1 | 后端零第三方依赖：仅用标准库 `http.server`/`sqlite3`/`hashlib`/`secrets`/`urllib`/`argparse`/`logging`/`ssl` 等 | `pyapp/*.py` 全部 import、`Dockerfile:3-7`（无 `requirements.txt`、无 `pip install`） | `grep -rn "^import\|^from" pyapp/ server.py` 人工核对；`python -c "import pyapp.config, ..."` 构建自检（`Dockerfile:37-40`） | 【代码·已核】；构建自检 【未验证·需 Docker】 |
| C-2 | 前端原生 ESM、无构建链、无 CDN 运行时依赖 | `README.md:29-31`、`index.html:64`、`js/*.js` 全为 `import`/`export` | 检索 `js/` 中无 `require(`/`define(`/`from 'http`；`index.html` 无外部 `<script src="http...">` | 【代码·已核】 |
| C-3 | 随处可运行：任意装有 Python 3.8+ 的目录即可启动，首次运行自建库建表 | `README.md:8,15,27`、`httpd.make_server` → `Application.ensure_ready` → `db.init_db` | `python server.py --check` 打印配置与统计后退出 | 【代码·已核】；实跑 【未验证·本次未执行】 |
| C-4 | 单服务器 / 单机形态，业务数据全在 `data/gtd4teams.db` 一个文件 | `README.md:56-59`、`config.py:74-76`、`db.connect` | `python server.py --check` 的 `dbPath`；`ls data/` | 【代码·已核】 |
| C-5 | 数据可携带：整库可导出/还原为纯 JSON，且可跨机器迁移 | `server.py` 的 `--export/--import`、`store.export_all`（`store.py:810`）、`store.import_all`（`:863`） | `test_backup_round_trip`（`tests/test_api.py`）；`--export`/`--import` 往返 | 【测试·有名】 |
| C-6 | 前后端唯一耦合点是 `js/api.js`，导出逐名登记（v1.1 起不再要求"与云版本逐名一致"） | `js/api.js:1-30`（30 个 `export`）、`README.md:209`、ADR-04 的 v1.1 修订 | 逐名比对 `api.js` 导出与 `app.js`/`auth-ui.js` 的 import；登记处 `06` §10.7 | 【代码·已核】 |
| C-7 | 权限在服务端判定，前端不做数据归属判断 | `policy.py:1-22`、`js/api.js:13-16` | 越权用例 `test_14`~`test_21`（`tests/test_api.py`） | 【代码·已核】+【测试·有名】 |
| C-8 | 版本串四处同步（详见 §7.4），当前实测同为 `20261114f` | 总纲 §4 G2、`README.md:264-265` | 见 §7.4 的检索命令 | 【代码·已核】 |
| C-9 | 发布产物以 `GTD_BUILD` 为准，切流前必须核对 | 总纲 §7.5、`Dockerfile:42-47`、`deploy/smoke.sh` | `test_02`/`test_07`（`tests/test_zerodowntime.py`） | 【代码·已核】+【测试·有名】 |
| C-10 | 离线可用、不开 CORS、静态资源白名单 | `README.md:31,96-101`、`config.py:22`、`httpd.py:834-901` | `test_29`~`test_31`、`test_36`、`test_09`（冒烟抓目录穿越） | 【代码·已核】+【测试·有名】 |
| **C-11** | **日志是辅助不是依赖**：日志目录只读/建不出来/文件打不开时，**服务必须照常启动与响应**，只降级为 stderr 并告警一行 | `applog.py:18-29,113-143`（`degraded`/`problem`）、`applog.py:146-152`（`write` 吞异常）、`httpd.py:356-357`（`_log_access` 整体 try） | `AccessLogDegradedTest`（`tests/test_api.py:3451`）的 `test_degraded_to_stderr_but_service_works`、`test_logs_endpoint_does_not_fake_empty_result` | 【代码·已核】+【测试·有名】 |
| **C-12** | **访问日志每请求恰好一条**，且 `path` 必须脱敏（query 值 `***`、控制字符压平、超长截断） | `httpd.py:296-357`（`_access_logged` 幂等闸门 + `_redact_path` 固定调用）、`httpd.py:56`（健康检查排除） | `AccessLogTest`（`:3225`）8 项含 `test_one_line_per_request`、`test_access_log_redacts_query`；`AccessLogRedactionTest`（`:3088`）4 项 | 【代码·已核】+【测试·有名】 |
| **C-13** | **证书管理零依赖**：不引入 `cryptography`/`pyOpenSSL`；配对校验只能用 `ssl.SSLContext.load_cert_chain` | `tls_cert.py:20-27`（"零依赖怎么做到"）、`:239-261`（`_load_chain`）、`:173-188`（`_test_decode_cert` 降级） | `CertAdminTest`（`tests/test_api.py:2726`）13 项、`CertDirMissingTest`（`:3018`） | 【代码·已核】+【测试·有名】 |
| **C-14** | **发布/回滚/看门狗互斥**：同一时刻只允许一个发布在跑，拿不到锁必须**直说并退出**，不得"硬上" | `deploy/release.sh:215-224`、`rollback.sh:77-81`、`watchdog.sh:44-50`（全部 `flock -n 9`，同一把 `deploy/run/*.lock`） | 服务器上并发执行两次 `release.sh`，第二次应立即 `die` 并打印占用者排查提示 | 【代码·已核】；并发实测 【未验证】 |
| **C-15** | **集成域必须是进程内标准库**（REQ-054）：不得引入 Node/npm/Bun/npx、不得作为独立进程、不得提供隧道/公网暴露 | 现状 `pyapp/`、`js/` 无 `MCP`/`api_tokens` 命中；约束来源 `README.md:7-8,25-31`、`pyapp/__init__.py:17`、REQ-033 | §16.2 的形态选择；验收时 `grep -rn "mcp\|npm\|npx" pyapp/` 只允许出现自研模块名 | **【设计·未实现】** |

> 约束的**代价**不做粉饰：零依赖 ⇒ 无 ORM/迁移框架/连接池/异步、证书只能靠 `ssl._ssl._test_decode_cert` 这种 CPython 内部函数；SQLite 单文件 ⇒ 单写、无跨进程限速共享、全量读取；无构建链 ⇒ 无 tree-shaking、无类型检查、版本串靠人工同步。见 §11、§12。

---

## 2. 总体架构

### 2.1 分层图

```mermaid
flowchart TB
  subgraph B[浏览器 · 原生 ESM 无构建链]
    IH[index.html]
    CFG[js/config.js APP_VERSION + 状态枚举]
    API[js/api.js —— 唯一后端接缝 30 个导出]
    APP[js/app.js —— 状态机 space/activeTeamId/view + dispatch]
    VW[js/views.js 纯渲染 HTML 字符串]
    UT[js/util.js 纯函数工具]
    AU[js/auth-ui.js]
    IH --> CFG & API & APP & VW & UT & AU
  end

  subgraph E[接入层 · 仅 Docker 形态]
    CAD[Caddy 唯一公网入口<br/>lb_policy first + health_uri<br/>覆盖 XFF / flush_interval -1<br/>tls 指向 tls-&lt;指纹&gt;.crt（版本化命名）]
  end

  subgraph P[Python 进程 · 标准库单体]
    SRV[server.py CLI/信号/优雅关闭]
    HTTPD[pyapp/httpd.py<br/>Handler._dispatch + ROUTES(24) + 静态三关白名单<br/>每个请求恰好一条 JSONL 访问日志]
    LOG[pyapp/applog.py ★新<br/>AccessLog：stderr + RotatingFileHandler<br/>读最近 N 条（尾部反向读）]
    TLS[pyapp/tls_cert.py ★新<br/>PEM 解析/配对校验/原子落盘/元数据]
    SEC[pyapp/security.py<br/>PBKDF2 / 令牌 / 限速状态机]
    POL[pyapp/policy.py<br/>POLICIES 行级策略 = 云版 RLS 复刻]
    STO[pyapp/store.py<br/>工作区 / CRUD / 团队 / 账号 / 复盘 / 备份]
    DB[pyapp/db.py<br/>COLUMNS 唯一权威 / 归一化 / WAL 连接 / _MIGRATIONS]
    LLM[pyapp/llm.py<br/>可选 OpenAI 兼容转发 urllib]
    CFG2[pyapp/config.py<br/>配置优先级 / log_file / cert_dir / 静态白名单]
    SRV --> HTTPD
    HTTPD --> LOG & TLS & SEC & POL & STO & CFG2 & LLM
    STO --> POL
    STO --> DB
    HTTPD --> DB
  end

  subgraph D[存储]
    SQLITE[(data/gtd4teams.db<br/>单文件 · WAL · busy_timeout=15s)]
    LOGFILE[data/logs/app.jsonl<br/>50MB × 5 份轮转]
    CERTS[deploy/run/certs/<br/>tls.crt / tls.key / .pending-apply / tls-&lt;fp&gt;.crt]
  end

  subgraph OPS[部署侧 · 仅服务器]
    DC[docker-compose.yml 双槽位 app-blue/app-green]
    SH[deploy/*.sh 发布/回滚/冒烟/探测/看门狗<br/>flock 互斥]
    GEN[lib.sh caddyfile_generate<br/>按指纹复制证书 + cat&gt; 原地覆盖 Caddyfile]
    ST[deploy/run/state.env 活跃槽位唯一权威]
    DC -.维护不变量.-> SH
    SH --> GEN --> ST
  end

  API -- "同源 /api/* fetch" --> CAD
  CAD -->|"反向代理 :8787"| HTTPD
  CAD -. "读 tls-&lt;fp&gt;.crt/.key" .-> CERTS
  API -. "本地直跑时直连" .-> HTTPD
  DB --> SQLITE
  LOG --> LOGFILE
  TLS -->|"写 tls.crt/tls.key + .pending-apply"| CERTS
  OPS -. "watchdog 消费 .pending-apply → 重生成+reload" .-> CERTS
  LLM -. "密钥只在服务端" .-> UP[OpenAI 兼容上游]
  OPS -. 编排 .-> P
```

- **本地直跑 / WorkBuddy sites 形态**：无 `Caddy`、无 `OPS` 两框，浏览器直连 `httpd`；`PORT` 被注入时自动绑 `0.0.0.0` 【代码·已核：`config.py:85-88`；文档·原文：`README.md:61-65`】。
- **Docker 形态**：`Caddy` 常驻唯一公网入口，两个槽位同时只跑一个，共用一个 `gtd-data` 卷，并**同时**把 `./deploy/run/certs` 挂给 app（`:rw`，`docker-compose.yml:109`）与 caddy（`:ro`，`:179`）【代码·已核】。
- **v2.0 新增的两条边**（图中虚线）：`httpd → applog → data/logs/app.jsonl` 与 `tls_cert → deploy/run/certs`，后者的"应用"动作**不在 Python 进程内发生**，由宿主 watchdog 消费 `.pending-apply` 完成（见 §6.7、ADR-17/18）。

### 2.2 模块职责表

| 层 | 模块 | 职责 | 不做什么（边界） | 证据 |
|---|---|---|---|---|
| 入口 | `server.py` | CLI 解析、`Config` 组装、`--check/--export/--import/--set-password/--create-admin/--grant-admin`、SIGTERM 优雅关闭 | 不含任何 HTTP 路由/业务规则 | `server.py`（内容锚点：`main` / `_install_signal_handlers`） |
| 配置 | `pyapp/config.py` | 运行配置、优先级、`PUBLIC_ENTRIES` 静态白名单、`MIME_TYPES`、`log_dir`/`log_file`/`cert_dir`、LLM 设置覆盖 | 不读数据库（由 `httpd.Application.connect` 代劳） | `config.py:22,24-40,60-210` |
| HTTP | `pyapp/httpd.py` | 路由表 `ROUTES`（24 条）、`_dispatch` 分发与写锁重试、会话与安全响应头、静态三关白名单、SSE、**每请求一条访问日志**、证书三端点、`GET /api/logs` | 不直接写表结构、不实现业务规则、不自己拼 SQL | `httpd.py:175-901,908-1614` |
| **日志** | **`pyapp/applog.py`（新）** | **`AccessLog` 装配（stderr + 轮转文件双 handler）、写一条 JSONL、健康检查心跳、`status()`、`read_recent` 尾部反向读、`timestamp()`** | **不脱敏**（脱敏固定在 `httpd._redact_path`，不提供绕过口）；**不抛异常到请求路径**；不写数据库 | `applog.py:93-174,177-198,209-241,248-397` |
| **证书** | **`pyapp/tls_cert.py`（新）** | **PEM 解析、四段校验（格式→加密→配对→有效期）、原子落盘（同目录 `.tmp-` + `os.replace`）、元数据（只存展示字段）、`status()` 以文件为唯一事实来源、`.pending-apply` 标记** | **绝不返回私钥/证书全文**；**绝不写日志**；不做 `mkdir`（目录缺失按部署问题 500）；**从不删除** `.pending-apply`（那是外部观察者的职责） | `tls_cert.py:10-27,69-80,264-294,357-394,439-561` |
| 安全 | `pyapp/security.py` | 口令散列/校验/升级、令牌生成与指纹、进程内限速状态机、临时口令生成 | 不碰 HTTP（`httpd` 负责取 IP 与 Cookie） | `security.py:50-205` |
| 权限 | `pyapp/policy.py` | `POLICIES` 逐表策略、`AuthzContext`、`load_context`、`Denied` | 不执行 SQL、不下沉到前端、**不含实例级概念** | `policy.py:36-67,267-366` |
| 业务 | `pyapp/store.py` | 工作区读取、通用 CRUD、团队生命周期、**账号三级建号/授权/重置**、复盘 upsert、运行日志、备份还原 | 不做认证（信任上游传入的 `ctx`）；不含明文口令 | `store.py:98-202,208-296,320-705,710-760,810-919` |
| 存储规格 | `pyapp/db.py` | DDL、`COLUMNS` 唯一权威、类型归一化/JSON 编解码、连接与建库幂等、**`_MIGRATIONS` 版本化迁移层（v1/v2）** | 不做权限判定 | `db.py:19-21,408,425,468-560,571-731` |
| 可选 AI | `pyapp/llm.py` | OpenAI 兼容转发、非流式/流式、错误脱敏 | 不向浏览器下发密钥 | `llm.py:24-189` |
| 前端接缝 | `js/api.js` | 同源 REST 客户端、30s 超时、错误结构化、`friendlyError`、日志缓冲脱敏、证书三方法 | 不含业务判断 | `api.js:19-486` |
| 前端状态 | `js/app.js` | `space/activeTeamId/view` 三层状态机 + 单一 `dispatch` 事件委托 | 不产出 HTML | `app.js`（内容锚点：`dispatch`）＋总纲 §8 |
| 前端渲染 | `js/views.js` | 纯函数产出 HTML 字符串；系统管理页含证书状态/上传/危险区 | 不持有状态、不发请求 | `views.js:2337-2470` |
| **前端工具** | **`js/util.js`（v1.x 未登记）** | **`escapeHtml`/`formatDateTime`/`toast`/`withTimeout` 等纯函数** | 不含状态与请求 | `util.js`（内容锚点：`escapeHtml`） |
| 部署 | `deploy/*.sh` + compose + Caddyfile | 槽位状态、健康检查、切流、**运行时上游断言**、**证书按指纹版本化 + reload 断言**、冒烟、连续性探测、备份、自愈、**flock 互斥** | 不引入 python3/node 依赖 | `lib.sh:208-530`、`release.sh:215-224`、`watchdog.sh:44-170` |

### 2.3 数据流：一次写请求的完整链路（以 `POST /api/rows` 的 `op=insert` 为例）

链路逐跳给出落点，供联调（G3）与排障按图索骥：

1. **浏览器**：`api.js` 的 `mutate()` 组装 `{table, op, id, payload}`，`request()` 发 `fetch('/api/rows', {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json'}})`，带 30s `AbortController` 超时 【代码·已核：`js/api.js:40-57,152-158`（内容锚点：`request`/`mutate`）】。
2. **接入层（Docker 形态）**：Caddy `lb_policy first` 把请求给上游列表第一个可用槽位；`header_up X-Forwarded-For {http.request.remote.host}` **覆盖**客户端伪造值；`flush_interval -1` 保证 SSE 不被缓冲 【代码·已核：`deploy/Caddyfile.template:41-61`】。
3. **请求级状态复位与编号**：`do_POST → _dispatch('POST')` → **`_reset_access_state()`** 生成 `request_id = secrets.token_hex(4)`、复位 `_uid/_status/_errcode/_access_logged/_path`、记录 `_started_at`（`time.monotonic()`）【代码·已核：`httpd.py:200-208,654-674`】。
4. **分发**：`_handle` 里重置 `_body_consumed=False`，`path` 去尾斜杠后写回 `self._path`（**强制改密闸门按它比对白名单**），查 `ROUTES.get(('POST','/api/rows'))` 命中 `h_rows`；非 `/api/` 前缀且非 GET → 先 `_discard_body()` 再 405 【代码·已核：`httpd.py:676-707`】。
5. **写前三关**：`_check_origin()`（`Origin` 与 `Host` 比对，`Origin: null` 也拒，403 `CROSS_ORIGIN_BLOCKED`）→ `Content-Type` 必须 `application/json`（415）→ `_read_body()` 一次性读取、`max_body_bytes` 8MB 上限（413）、非合法 JSON 对象 → 400 `BAD_JSON`；**读成功后立刻置 `_body_consumed=True`**（语义是"流已前进"，不是"解析成功"）【代码·已核：`httpd.py:363-387,431-446,714-737`】。
6. **写锁重试循环**：`for attempt in range(WRITE_RETRY_ATTEMPTS=6)`；每轮 `app.connect()` 新建连接（`WAL` + `synchronous=NORMAL` + `busy_timeout=15000`），并把库内 `settings` 的 LLM 三项覆盖到 `cfg` 【代码·已核：`httpd.py:751-784`、`db.connect`（`db.py:468`）、`config.apply_llm_settings`（`config.py:165`）】。
7. **认证 + 闸门**：`h_rows → _require_user(conn)` → `_current_user` 从 Cookie 取 `gtd_session` → `token_fingerprint` → 查 `sessions JOIN users`（**多取 `is_system_admin`/`must_change_password` 两列**）；失效 401；随后 **`must_change_password` 闸门**（不在白名单即 403 `PASSWORD_CHANGE_REQUIRED`）；顺带做过期会话清理与 `last_seen_at` 300s 节流刷新 【代码·已核：`httpd.py:519-596`】。
8. **权限上下文**：`load_context(conn, uid)` 读 `team_members`，仅纳入 `status=='active'` 的行，归并为 `{team_id: role}`；此读取**绕过 `team_members` 自身策略**（对应云版 `SECURITY DEFINER`）【代码·已核：`policy.py:351-366`】。
9. **字段归一化**：`db.split_payload(table, payload, for_insert=True)` 丢弃 `pk`/`owner`/`stamp`/未知字段，其余按 `COLUMNS` 的 kind 归一化（`int/real/bool/json/jsonarr/text/date/ts`）【代码·已核：`db.split_payload`（`db.py:805`）】。
10. **服务端注入归属**：若表含 `owner_id` 列，`values['owner_id'] = ctx.uid`；同时写 `created_at`/`updated_at` 【代码·已核：`store.py:148-154`】。
11. **权限判定**：`can_insert(table, prospective, ctx)` 为假 → `raise Denied` → `_dispatch` 转 403 `PERMISSION_DENIED`；为真才 `INSERT`（参数绑定，表名过 `_assert_table`）→ `_fetch` 取回 → **最后** `conn.commit()` 【代码·已核：`store.py:140-167`；`policy.py:334-342`；`httpd.py:762-766`】。
12. **响应**：`_json({'rows': rows})` → `_send` 附加 `X-Content-Type-Options: nosniff` / `Referrer-Policy: no-referrer` / `X-Frame-Options: DENY`，并在写完之后**立刻写一条访问日志**（`_status` 记下真实状态码供 finally 兜底复用）【代码·已核：`httpd.py:469-490`】。
13. **异常与收尾**：`ApiError/StoreError/LlmError` → `_error()` 统一错误体**并写 `self._errcode`**（唯一掌握业务错误码的位置，供日志结构化查询）；写锁争用（`_is_write_contention`，含 Windows 上报 `SQLITE_READONLY` 的情形）→ 指数退避重试，**且仅当响应未开始写 + 本次从未尝试提交**（`send_header` 覆写置 `_responded`；`db.TrackedConnection.commit_attempted`）；`finally: conn.close()` 丢弃未提交事务，保证"要么整体生效、要么完全没发生"【代码·已核：`httpd.py:168-172,244-253,492-506,769-833`、`db.py:482-560`】。
14. **日志兜底**：`_dispatch` 的 `finally` 再调一次 `_log_access`；从未发出响应则记 `499`（`CLIENT_CLOSED_STATUS`，nginx 约定，**不记 500** —— 否则用户关页面会污染服务端错误率）【代码·已核：`httpd.py:58-62,668-674`】。

> **`update`/`delete` 的差异**：权限不通过时**返回 `[]` 而非抛异常**（`store.py:188-189,199-200`），且行不存在也返回 `[]`；`update` 在**归一化后没有任何可写字段**时直接返回 `[current]` 且不 `commit`（`store.py:184-185`）—— 这条既有语义正好是 §16.6 "未变更 PATCH 不推进版本"的实现基础。这是刻意保留，详见 §5.3 与 ADR-08。

### 2.4 一次读请求的差异（补充）

`GET /api/workspace` 走 `h_workspace → load_context → store.load_workspace`，后者对 10 张表逐行在 Python 内跑 `can_select` 过滤后一次性返回（`teams/members/items/projects/areas/contexts/resources/reviews/onCall/logs`，`logs` 限 `LOG_LIMIT=400`）【代码·已核：`store.py:116-137`】。**读路径不带写**（会话刷新被 300s 节流），这是 §3 ADR-09 蓝绿共库成立的纪律之一。

v2.0 补充两条读路径的特例：

- **`GET /api/logs` 完全不碰数据库**：只读 `data/logs/app.jsonl` 文件（`applog.read_recent`），因此它不参与写锁竞争，也不进入 `policy` 的 `app_logs` 策略（它的鉴权是 `_require_instance_admin`）【代码·已核：`httpd.py:1257-1259,1260`】。
- **`GET /api/admin/cert` 读库但只读 `settings` 一个键**：`store.get_setting(conn,'tls_cert_meta')`，且**只有当元数据指纹等于文件指纹时才采信**（§6.7.4）【代码·已核：`tls_cert.py:301-309,474-493`】。

### 2.5 早退响应的请求体一致性（v2.0 新增，实现见 §8.5）

一次写请求在**发出响应之前**可能走 5 条早退分支（静态资源 405、路由 404、跨站 403、媒体类型 415、坏 JSON 400）。它们**全部**必须先消费掉请求体，否则残留字节会成为同一条 keep-alive 连接上"下一个请求的请求行"【代码·已核：`httpd.py:689-690,695,705,731,735`】。这条纪律的完整论证、实测现象与请求走私风险见 **§8.5**，架构上的结论只有一句：

> **"响应早退"与"请求体已消费"是两件必须解耦的事**：`_body_consumed` 表达的是"流已前进"，不是"处理成功"。任何新增的早退分支都必须先 `_discard_body()`。

### 2.6 进程内监听拓扑（v2.0 新增，为 §16 铺垫）

| 监听器 | 绑定 | 是否已实现 | 职责 |
|---|---|---|---|
| 主 HTTP | `config.host:config.port`（默认 `127.0.0.1:8787`；Docker 槽内 `0.0.0.0:8787`，宿主只绑 `127.0.0.1:8788/8789`） | ✅ | 全部 `/api/*` + 静态资源（`httpd.make_server`） |
| 集成域 API | `127.0.0.1:<GTD_API_PORT>`（**建议默认 8790**，避开槽位 8788/8789） | **❌【设计·未实现】** | `/v1/*` 机器接口，Bearer 令牌 |
| 内置 MCP | `127.0.0.1:<GTD_MCP_PORT>`（默认 **8722**，可配 1024~65535，PRD REQ-050） | **❌【设计·未实现】** | `/mcp` JSON-RPC，Bearer 令牌 |

三条不变式（设计约束，v2.0 拍板）：

1. **三个监听器是同一进程内的三个 `ThreadingHTTPServer` 实例**，共用 `Application`（同 `db_path`、同 `access_log`）；不引入第二个进程 —— 否则又会出现"跨进程不共享限速/令牌状态"（§12.1 R-5 的同类问题）。
2. **集成域的两个监听器只能绑 `127.0.0.1`**，且**没有配置项**可以让它们绑 `0.0.0.0`（不是"默认关"，而是"根本没有这个开关"）。理由：它们提供的是"等同于账号全权限"的长期凭据入口（REQ-047），一旦可达公网就等于把账号密码贴在公网上。
3. **默认关闭**：`GTD_API_ENABLED=0` / `GTD_MCP_ENABLED=0`，关闭时**不创建 socket**（不是创建后拒服务）—— 关闭状态下 `netstat` 里看不到任何端口，这是"默认关闭"唯一可验证的语义。

---

## 3. ADR 架构决策记录（21 条）

> 每条含「决策 / 备选方案 / 选择理由 / 代价 / 何时重新评估」。
> **状态标注**：ADR-01~12 为 **Accepted（存量，反向补录）**；ADR-13/14 为 v1.1 新增 **Accepted（CR-2026-001 拍板）**；**ADR-15~21 为 v2.0 新增** —— 其中 **ADR-15~20 状态 Accepted（存量实现，反向补录）**，**ADR-21 状态 Proposed（设计未实现，待 G1 裁决）**。
> 编号规则：ADR-NNN 严格连续、永不重用。本版从 ADR-15 续起。

### ADR-01 后端用 Python 标准库，而非 Web 框架
- **决策**：`http.server.ThreadingHTTPServer` + `BaseHTTPRequestHandler` 手写路由，零第三方依赖 【代码·已核：`httpd.py:34,1621-1643`】。
- **备选**：FastAPI/Flask + uvicorn/gunicorn；引入 ORM 与迁移框架。
- **理由**：硬约束 C-1/C-3；`README.md:7-8,27-31`「拷到一个装有 Python 3 的目录里就能跑」；`Dockerfile:3-7` 不联网装包即可构建。
- **代价**：无自动 OpenAPI、无依赖注入、无中间件生态；路由/校验/错误映射全部手写且需靠 `06` 固化；无异步 IO，LLM 长连接与 SSE 各占一个线程。
- **重新评估**：当并发需要异步/多 worker 池、或需要生态中间件（限流/追踪/指标）且愿意放弃零依赖时。

### ADR-02 用 SQLite 单文件，而非 PostgreSQL
- **决策**：`data/gtd4teams.db` 单文件；`WAL + busy_timeout=15000 + synchronous=NORMAL` 【代码·已核：`db.connect`（`db.py:468-480`）；`config.py:74-76`】。
- **备选**：PG（云版本即 PG + RLS）；MySQL；外部数据库服务。
- **理由**：数据可携带（C-4/C-5）、备份即拷文件、无需独立服务进程、字段名与云版本逐字一致以便前端零改动（`db.py:5-8`）。
- **代价**：单写（写写排队）、无数据库级 RLS（故有 ADR-06）、无在线 DDL、`--import` 为 `INSERT OR REPLACE` 不清空；跨进程不共享内存状态（§12.1 R-5）。
- **重新评估**：多写并发成为瓶颈、需要跨机多活、或需要数据库级行级安全与审计时。

### ADR-03 前端原生 ESM、无构建链
- **决策**：`<script type="module">` + 相对路径 `import ... ?v=20261114f`，Python 直接托管静态文件 【代码·已核：`index.html:64`；`js/*.js`】。
- **备选**：React/Vue + Vite/webpack；CDN SDK。
- **理由**：C-2/C-10 离线可用、无 Node 依赖；镜像白名单 COPY 只需 `index.html`/`js`/`css`（`Dockerfile:26-30`）。
- **代价**：无类型检查与打包优化；缓存失效完全依赖人工递增版本串（§7.4）；无目录列表、白名单外文件 404（`httpd.py:834-901`）。
- **重新评估**：需要组件化复用/类型安全，且能引入构建链与 Node 运行时时。

### ADR-04 `js/api.js` 是与后端的唯一接缝（v1.1 已修订一次）
- **决策**：仅 `api.js` 内含 `/api/*` 路径与错误码，`app.js` 业务逻辑不发生网络调用 【文档·原文：`README.md:209`】【代码·已核：`js/api.js:19-30`】。
- **备选**：让各视图直接 fetch；或引入统一 SDK。
- **理由**：可验证的替换边界；后端换栈时前端业务层零改动；契约变更集中在 `06` §5 管理。
- **代价**：任何接口变更都必须同步 `api.js` 与 `06`；`api.js` 承担传输、错误映射、日志三重职责（较胖，486 行）。
- **重新评估**：接缝内职责多到难以维护、或需要为不同后端提供多套 adapter 时。
- **⚠️ v1.1（CR-2026-001）修订：「导出签名逐名对齐云版本」这条约束作废。** 原因：云版本已不在维护，把本地导出签名钉在一个不再演进的实现上，只会让"下线一个已死的端点"变成需要绕路的事。约束的**目的**（接缝单一、变更可追溯）与**手段**（逐名一致）是两件事，现改为**本地实现为准 + 每次增删逐名登记**（登记处：`06` §10.7）。不变的部分：`api.js` 仍是唯一接缝。代价：失去"和云版对照"这条外部校验线，补偿手段是 `06` §10.7 登记表 + `.smoke/auth-flow-test.mjs` 的表驱动断言。
- **v2.0 追加**：导出数已从 27 增至 **30**（新增 `getCert`/`uploadCert`/`deleteCert`，`js/api.js:395-411`）—— 再次印证"导出总数不是硬指标，登记才是"。

### ADR-05 `db.COLUMNS` 是字段规格的唯一权威
- **决策**：可写字段白名单、类型归一化、JSON 编解码全由 `COLUMNS` 驱动；键集合必须等于 `PRAGMA table_info` 【代码·已核：`db.py:408,734-800,805`】。
- **备选**：手写每条 SQL 的字段清单；从 `PRAGMA` 运行时反射生成。
- **理由**：防"手写 DDL 与规格表悄悄漂移"；`split_payload` 借它丢弃未知字段与 `owner_id`（安全相关）；有防漂移断言 `test_columns_spec_matches_real_schema`（`tests/test_api.py:1275`）【测试·有名】。
- **代价**：新增/改列必须同改 DDL + `COLUMNS` + `EXPECTED_TABLES` 等（§7.6 登记纪律）；无向下迁移框架，兼容性靠人工（§12）。
- **重新评估**：引入迁移框架、或列数多到规格表难以人工维护时（但会破坏零依赖）。

### ADR-06 `policy.py` 逐条复刻云版 RLS 行级策略
- **决策**：`Policy(select, insert, update.using, update.check, delete)` + `PolicyUpdate(using, check, keep)` + `AuthzContext`，11 张表策略表 `POLICIES` 【代码·已核：`policy.py:26-34,267-310`】。
- **备选**：把可见性判断放前端；在 `store.py` 里硬编码 if/else；下沉到 SQL `WHERE`。
- **理由**：安全边界不能下沉到前端（`policy.py:5-7`）；与云版 RLS 清单一一对应、便于逐条对照；`select_table` 明确选择「Python 逐行判策略」而非编译成 SQL，理由是数据体量小且前端本就一次性全量加载（`store.py:98-115`）。
- **代价**：全表读入内存再过滤，性能随数据量线性增长（§10.1）；策略与 DDL 双份维护，靠 G1 评审对齐（§5.4）。
- **重新评估**：单表行数过万导致 `loadWorkspace` 明显变慢、或未来回到带 RLS 的数据库时。
- **v2.0 追加**：`PolicyUpdate` 多了第三段 `keep(old, new, ctx)`（`policy.py:27-34`）。这是本地版对 RLS 的**必要补充**——"某列不许被改"这类规则必须同时看到改动前后两行才能判（`team_members` 的 owner 保护就是靠它）。它不破坏"逐条复刻"，因为 `keep` 只在 `using` 通过后才跑（`policy.can_update`，`:338-344`）。

### ADR-07 双域共用一张 `work_items`
- **决策**：`team_id IS NULL` = 个人事项（仅本人可见），非空 = 团队事项（成员共享）；两者共用同一状态机与字段 【代码·已核：`policy.py:11-12,83-89`、`db.py` 的 `work_items` DDL】。
- **备选**：拆 `personal_items` / `team_items` 两表。
- **理由**：方法论底座要求"两层共用同一张 `work_items` 与同一套状态机"；云版本字段名逐字一致（`db.py:5-8`）。
- **代价**：所有查询都需带 `team_id` 语义；权限谓词必须区分 `_personal_or_member`；误写 `team_id` 会造成跨域数据错配，靠 `split_payload` 归一化 + `can_*` 兜底。
- **重新评估**：个人域与团队域字段/流程差异大到共用表成为负担时（需评估迁移成本）。
- **v2.0 影响提示**：§16.6 要加的 `version` / `archived_at` 两列落在**这一张表**上，因此个人域与团队域同时获得版本控制与软删除 —— 这是"共用表"的又一次连带收益/代价。

### ADR-08 更新/删除被权限拦下时返回空数组，而非抛错
- **决策**：`update_row`/`delete_row` 在行不存在或权限不通过时 `return []`；`h_rows` 统一响应 `{rows: [...]}` 【代码·已核：`store.py:169-202`、`httpd.py:1145-1168`、`policy.py:317-328`】。
- **备选**：抛 403/404 异常。
- **理由**：与云版 RLS「命中 0 行」语义一致；前端 `assertAffected()` 依赖"空数组 = 没改到"如实提示"可能不存在或不在你可见的范围内"，**不假装成功**，也不用"不存在"与"无权限"两个响应泄露数据存在性。
- **代价**：调用方必须检查返回值长度；调试时"静默无操作"不直观。
- **重新评估**：若前端改用乐观锁/需要区分"不存在"与"无权限"时——但会改变产品语义与既有测试（`test_14/15/20` 断言 `rows == []`），属破坏性变更，须走 `CR-`。
- **v2.0 与 §16 的张力（必须提前说清）**：机器接口需要**可区分的错误**（`404 not_found` vs `403 forbidden`），而 ADR-08 的"空数组"对脚本毫无信息量。§16.4 的裁决是：**REST 面在 `policy` 之后自己再判一次"行是否可见"**（先 `can_select` 判可见性 → 不可见返回 404 `not_found`，与"不存在"合并为同一响应，仍不泄露存在性），而不改 `store` 的返回值语义。**绝不允许**为了给 API 报错而把空数组改成抛错（那会同时破坏前端与 `test_14/15/20`）。

### ADR-09 双槽位蓝绿共用同一个 SQLite 库文件
- **决策**：`app-blue`(127.0.0.1:8788)/`app-green`(127.0.0.1:8789) 共挂 `gtd-data` 卷，同时只跑一个；切流 = 重写 Caddy 上游顺序 + `caddy reload` 【代码·已核：`docker-compose.yml:8-18,86-87,161-164`、`lib.sh:208-357,462-510`】。
- **备选**：每槽位独立库 + 数据同步；停服发布；共享 PG。
- **理由**：零停机且无需数据同步；会话存在共享库中，切流不登出；`caddy reload` 平滑重载不断连接。
- **代价**：**三处代码纪律必须同时成立**：① 结构建全时跳过 DDL（`db.schema_present` + `init_db`）；② 会话刷新 300s 节流避免读路径带写；③ 写锁争用退避重试且响应未开始写。另要求新旧两版表结构兼容（总纲 §7.3 expand/contract）。**v2.0 追加第四条**：④ 迁移层必须`BEGIN IMMEDIATE` + 抢锁后二次判重，否则两槽位同时启动会真的并发 `ALTER`（`db.run_migrations`）。
- **验证状态**：前提由 `tests/test_zerodowntime.py` 9 项覆盖（含并发写入零失败、会话跨进程有效、build 可区分、日志无锁冲突）【测试·有名】；**但开发机无 Docker，Linux 双进程场景总纲 §1 明示"首次上服务器前必须跑"→【未验证】**。
- **重新评估**：若并发写入在生产出现锁冲突、或需要跨机多活时（此时应换 ADR-02）。

### ADR-10 可选 LLM 走服务端转发、密钥只在服务端
- **决策**：`/api/llm/*` 由 `pyapp/llm.py` 用标准库 `urllib` 转发 OpenAI 兼容端点；`GET /api/llm/config` 只回 `{configured, baseUrl, model, apiKeySet}` 不回密钥（连掩码都不给）【代码·已核：`llm.py:8-16,32-80`、`httpd.py:1350-1520`】。
- **备选**：浏览器直连厂商（密钥下发）；内置某一家 SDK（破坏零依赖）；强制流式。
- **理由**：密钥不出服务端；支持非流式；`stream:true` 时 SSE 直通；未配置返回 `LLM_NOT_CONFIGURED` 给可操作指引。
- **代价**：长连接占线程；上游错误码需脱敏（`llm._scrub`）；SSE 中断处理见 `06` §7.2；密钥存在库 `settings` 表且 `--export` 会带出。
- **重新评估**：需要并发流式连接数较高、或需要统一 AI 网关/计费审计时。

### ADR-11 `GTD_TRUST_PROXY` 默认关，且必须与绑定形态配对
- **决策**：`trust_proxy` 默认 `False`；仅在显式声明在可信反代之后才采信 `X-Forwarded-For/Proto`，且 Caddy 必须**覆盖**而非追加 XFF 【代码·已核：`config.py:107`、`httpd.py:456-467,615-628`、`Caddyfile.template:50-56`】。
- **备选**：无条件采信 XFF（云版直觉做法）；或永远不采信。
- **理由**：伪造 XFF 可换掉登录限流键使爆破不限速（`config.py:102-107`）；`test_40_forged_x_forwarded_for_cannot_bypass_login_throttle` 直接盯此退化【测试·有名】。
- **代价**：反代后若忘记置 1，所有请求算成同一 IP、限流误伤且 Cookie 缺 `Secure`；反过来直连公网误置 1 则安全失守（配对见 §6.4）。
- **重新评估**：部署拓扑变化（槽位端口改为对外暴露）时必须重新判定，见 §6.4 配对表与 G5 门禁。

### ADR-12 会话令牌只存 SHA-256、口令用 PBKDF2、限速为进程内状态机
- **决策**：`secrets.token_urlsafe(32)` 存 Cookie，库里只存 `sha256(token)`；口令 `pbkdf2_sha256$240000$独立盐$摘要`，登录成功顺手 rehash；限速 `(email.lower, IP)` 15 分钟 10 次，进程内 `dict` + `threading.Lock` 【代码·已核：`security.py:27-29,50-80,129-135,162-199`、`httpd.py:954-990`】。
- **备选**：bcrypt/argon2（需编译依赖）；JWT 无状态令牌；Redis 集中限速。
- **理由**：零依赖硬约束（`security.py:10-11` 明确"选 PBKDF2 的唯一理由是零依赖"）；库被拷走也无法冒用登录态（`security.py:12-13`）。
- **代价**：无服务端主动吊销粒度之外的能力（改密踢会话靠删行）；**进程内限速状态机不跨进程共享** → 蓝绿双进程期间窗口被稀释（§12.1 R-5，【推断】）；PBKDF2 240k 迭代使登录有 CPU 开销。
- **重新评估**：需要多实例共享限速/会话吊销、或有 FIPS 之外的口令散列合规要求时。
- **v2.0 追加**：§16 的机器令牌**复用同一套散列纪律**（只存 `sha256`）与**同一套限速思路**（进程内键 `token|<token_id>`），但**不复用会话表**（理由见 ADR-21）。

### ADR-13 系统管理员用库内标记（`users.is_system_admin`），不再用环境变量白名单
> 状态：**Accepted（v1.1，CR-2026-001 拍板；v2.0 已由代码兑现）**。本条**取代** v1.0 `config.py` 注释里"管理员不是业务概念、放环境变量最省事"的判断。

- **决策**：实例级管理员身份落在 `users.is_system_admin`（INTEGER 0/1，由版本化迁移层的 `_MIGRATIONS` **v1** 新增；`must_change_password` 是 **v2**，终值 `schema_version = 2`，见 `05` §13）；首次引导由 CLI `--create-admin` 产生；`GTD_ADMIN_EMAILS`（`config.admin_emails`）在过渡期作为"OR 兜底"存在，v1.2 删除（`06` §10.11 Q-3）。
- **备选**：
  - A 环境变量白名单（旧实现，`config.is_admin_email` 仍作为兜底保留，`config.py:181-185`）；
  - B 独立 `instance_admins(user_id)` 表（多一表、多一条策略，可加审计列）；
  - C 在 `users` 上加 `role TEXT`（`'user'|'admin'`），与 `team_members.role` 同名不同域 —— **不采纳**：两个 `role` 会长期被混淆，而 `team_members.role` 已被 `policy.py` 深度依赖（`AuthzContext.is_admin`）。
- **理由（白名单的三条硬伤，都已在本变更中变成现实问题）**：
  1. **空实例无法引导**。白名单只能"给已存在的账号授权"，而"存在"本身需要一个免鉴权的建号入口。本期下线自助注册后，纯白名单方案下**第一个账号建不出来**，于是"谁能改 AI 配置"变成无人可答。库内标记 + CLI 引导把"第一个账号"恢复成一次显式的运维操作。
  2. **授权与发布耦合**。`admin_emails` 在 `Config.__init__` 里读一次，改一次要改 `.env` 并重启进程；而"提一个管理员"本应是即时、可追溯的业务动作。库内标记即时生效，能被审计（`app_logs` 的 `account.created`），能随备份迁移。
  3. **权限挂在可变标识上**。`is_admin_email()` 按 **email 字符串**匹配，账号换邮箱/改名后权限跟着漂移或凭空消失；`users.id` 是 TEXT 主键、恒定（`security.new_user_id`），挂在它上面才稳定。
- **代价（必须记住，否则会以"缺陷"的形式复发）**：
  1. **必须有引导流程**：新人接手/新环境部署的第一条命令不再是"打开浏览器注册"，而是 `--create-admin`。
  2. **必须做数据库迁移**。本项目 v1.0 时没有版本化迁移层，本变更成为迁移层的**首个真实用例**；现已有 `_MIGRATIONS` v1/v2（`db.py:674-677`）。
  3. **权限来源从"部署配置"变成"库内数据"**：库被拷走即带走"谁是实例管理员"。威胁模型未实质变化（口令散列、LLM 密钥本来就在库里），但**备份文件敏感性上升了一档**，`09` 的备份说明要跟着改。
  4. **`--export`/`--import` 会带上该标记**（`store.export_all` 是 `SELECT *`，`BACKUP_TABLES` 含 `users`，`store.py:803`）→ 从生产备份还原到测试环境后，测试环境里也有同一批实例管理员。这是期望行为（否则灾备后没人能管），但**必须写进 `09`**。
- **何时重新评估**：需要"实例管理员"与"运维账号"分离（"能建号"与"能改配置"拆成两个权限）、或需要多租户/多实例管理面时 → 那时应改成独立表 + 审计表（备选 B）。
- **v2.0 落地确认**：`_require_instance_admin`（`httpd.py:1452-1473`）是本决策的**唯一实现点**，已被 AI 配置、证书管理、`GET /api/logs` 三处复用（§5.7）。

### ADR-14 账号创建收敛为「三级建号 + 强制改密闸门」，且闸门默认拒绝
> 状态：**Accepted（v1.1，CR-2026-001 拍板；v2.0 已由代码兑现）**。

- **决策**：
  1. **三级建号**：系统管理员 → 可建任意团队的 **`admin`（仅 `admin`，v1.3 收紧）**；团队管理员（该团队 `owner`/`admin`）→ 只能为本团队建 `member`；其他任何人 → 不能建号。`owner` 一律不可经接口授予。
  2. **口令由系统生成、一次性返回**（`secrets`，16 位，去掉 `0/O/1/l/I` 三对视觉混淆字符），账号带 `must_change_password=1`。
  3. **闸门默认拒绝**：`must_change_password=1` 时，除白名单外一律 `403 PASSWORD_CHANGE_REQUIRED`。闸门实现在 **`_require_user` 内部**（`httpd.py:576-587`）。
  4. **唯一写入路径**：`store.create_account(conn, actor, ...)`（`store.py:389`）。`httpd` 不拼 SQL，也不用 `policy.can_insert` 近似。
- **备选**：
  - ① 回归"自助注册 + 邀请制"——产品已明确否决；
  - ② 让创建者自己填写初始口令 —— **更差**：口令由"创建者"与"使用者"两人知道，且人会选弱口令；
  - ③ 邮件邀请链接 —— 需要邮件服务，破坏零依赖（C-1）；
  - ④ 闸门放 `_dispatch` 的某一步 —— 那里没有 user 对象，要么二次查会话、要么把路径白名单散到路由层。
- **理由**：
  1. **闸门放在 `_require_user` 是唯一"新增端点自动被覆盖"的位置**（fail-closed）。
  2. **`_require_user` 天然不误伤匿名端点**：`h_session`/`h_log`/`h_access_logs` 走 `_current_user`；`h_health`/`h_logout` 不鉴权；静态资源在前置分支。因此闸门里**只需要 6 条路径的显式登记**，可逐条审计。
  3. **口令必须一次性**：初始口令是"传递中的凭据"，必然经过聊天窗口/便签/口头；`must_change_password` 把这个窗口压到"首次登录后立刻关闭"。
- **代价**：
  1. **必须保留 break-glass**：唯一系统管理员丢了口令 → 只能上服务器 `--set-password`；若连 CLI 都进不去，就只能改库文件。`09` 必须写清。
  2. **引导漏做 = 建号能力缺失（不锁站）**：迁移把存量账号置 0，因此既有业务功能照常。
  3. **前端多一个必过页面**（强制改密），且旧标签页会撞 403。
  4. **测试夹具大面积改造**：`Client.register` 已不再走自助注册端点。
- **何时重新评估**：接入 SSO/OIDC（那时整套可撤），或需要"批量导入账号"（走 CLI 批量而非开放接口）。
- **v2.0 落地确认**：`store.create_account`（`:389-527`）、`grant_system_admin`（`:530-578`）、`reset_password`（`:581-631`）、`list_accounts`（`:637-704`）四函数已就位；`PASSWORD_CHANGE_EXEMPT` 常量在 `httpd.py:78-85`；闸门行为由 `PasswordChangeGateTest`（`tests/test_api.py:1755`）覆盖【测试·有名】。

### ADR-15 访问日志：JSONL + stderr/文件双写 + 轮转变上限 + 失败只降级
> 状态：**Accepted（v2.0 反向补录，实现已存在）**。

- **决策**：
  1. **每请求一条 JSON 行**（JSONL），字段固定 9 个（§9.3），落 `<data_dir>/logs/app.jsonl`；
  2. **双 handler**：`StreamHandler(sys.stderr)` + `RotatingFileHandler`，**同一条记录写两处**；
  3. **轮转有上限**：50MB × 5 份 ≈ 300MB 封顶；
  4. **健康检查不进日志**（只留 `logger.debug` 心跳），排除清单由调用方判定（`ACCESS_LOG_EXCLUDED`）；
  5. **日志失败只降级**：目录建不出/文件打不开 → `degraded=True` + `problem` 原因，只写 stderr，**服务照常**。
- **备选**：
  - A **只写文件**：`docker logs` 里什么都没有，容器排障时"日志在哪"变成必须 exec 进去才知道的问题；容器日志驱动的轮转与我们的轮转还会打架（两份都在转）。
  - B **只写 stderr**：宿主上直接跑（形态 A）时没有日志驱动接管，关掉终端就没了；且无法提供 `GET /api/logs` 的读取源。
  - C **写数据库**（像 `app_logs` 那样）：**否决**——每个请求一次 INSERT 会让**读路径带写**，直接违反 ADR-09 纪律②，把日志本身变成写锁争用的源头。这一条是本 ADR 与 §9.4"只读文件不碰库"的共同理由。
  - D 引入第三方日志库（structlog/loguru）：破坏 C-1。
- **为什么"双写"值得**（这是本条最容易被问的点）：两处的**故障模式互补**，而不是冗余：
  - 宿主机磁盘满/只读挂载 → 文件 handler 挂，**stderr 仍在**（容器日志驱动通常落在另一个磁盘/分区）；
  - 容器被 `docker rm` / 日志驱动未配置 → stderr 丢，**文件仍在 `gtd-data` 卷里**（卷才是运维真正备份的东西）；
  - 采集侧要聚合（`jq`/`grep`/时间窗统计）→ 要文件；人要"现在发生了什么"→ `docker logs -f` 更直接。
- **代价**：
  1. **写放大 2 倍**（每条记录两次 IO），QPS 高时 stderr 的 `flush` 会成为最慢的一环；
  2. 双份日志的**顺序不保证一致**（两个 handler 各自加锁）；
  3. 300MB 上限意味着**高峰期会丢历史**（这是刻意的：宁可丢历史，也不要写满宿主磁盘把 SQLite 一起带走）；
  4. 降级状态下监控没有数据源 —— 这是**已知且不可消除**的缺口，只能用 `GET /api/logs` 的 `fileLogging=false` 让它**可见**（§9.4）。
- **重新评估**：当日志量超过 300MB/天（需要外部采集），或需要跨请求追踪（引入 trace id 体系）时。

### ADR-16 `GET /api/logs` 只读文件、尾部反向读、不做分页
> 状态：**Accepted（v2.0 反向补录）**。

- **决策**：
  1. 从**文件尾部反向**按 64KB 块读，凑够 `limit` 条立即停（`applog.read_recent`）；
  2. 过滤条件（`status`/`path`/`q`）**在读取过程中**应用，不是读完再过滤；
  3. 返回**时间正序**（等价 `tail -n`），"最新在最上"交给前端反转；
  4. **不做翻页**，只给 `truncated` 保守标记；
  5. **权限复用 `_require_instance_admin`**，日志是**实例级**运维数据，团队 owner/admin 也看不到；
  6. **降级时根本不打开文件**（先看 `status().fileLogging`），返回 `logs:[] + fileLogging:false + degradeReason`。
- **备选**：
  - A 全文件读入再过滤 → `?status=500` 恰恰在日志最大时被使用，一次请求就能把 300MB 读进内存（OOM 入口）。
  - B 真正的分页（offset/total）→ 需要稳定游标 + 精确总数，而要精确就得反复扫全文件；结论只是"有没有第 101 条"。**用保守 `truncated` 换掉一个昂贵且没人验的精度**。
  - C 让日志进库再 `SELECT ... ORDER BY id DESC LIMIT` → 见 ADR-15 备选 C。
- **理由**：排障的三个高频问题（"刚才那个 500 的编号对应哪条请求"、"现在有没有人在被限流"、"服务到底有没有在收流量"）应当**在浏览器里回答完**，而不是逼运维 `docker exec` + `tail`。而这三个问题都只需要"最近的 N 条"。
- **代价**：
  1. `total` 是"当前文件总行数"（真去数，1MB 一块），不是"匹配总数" —— 容易误解，已在 §9.4 的字段说明里写死；
  2. 无翻页 ⇒ 看不了"上周三的错误"，只能 `docker exec`/卷内检索；
  3. 反向读 + 半行容忍 ⇒ 极端情况下（轮转交接瞬间）可能少给一条；
  4. **`fileLogging=false` 与 `truncated=true` 都是"保守判断"**，宁可多说一句"可能还有"，不为了精确再扫一遍。
- **重新评估**：日志需要长期检索（那时应上外部采集，而不是给这个接口加分页）。

### ADR-17 证书按 SHA-256 指纹版本化命名，用 `.pending-apply` 标记驱动异步应用
> 状态：**Accepted（v2.0 反向补录）**。

- **决策**：
  1. `caddyfile_generate` 把 `tls.crt/tls.key` **复制成 `tls-<指纹前16位>.crt/.key`**，Caddyfile 里 `tls` 指令引用**副本**，并清理旧版本副本（`lib.sh:235-270`）；
  2. 后端**只写文件 + 打 `.pending-apply` 标记**，从不重载 Caddy、从不删除标记（`tls_cert.py:380-394,509-540`）；
  3. 应用动作由宿主 `watchdog.sh`（cron 每分钟）消费标记：重生成配置 → `caddy reload` → **断言运行时证书指纹 == 文件指纹** → 成功才删标记（`watchdog.sh:98-170`）；
  4. 发布脚本 `caddy_reload` 亦顺手消费该标记（若本次 reload 已让运行时证书对上）（`lib.sh:491-503`）。
- **备选**：
  - A **继续引用固定路径 `tls.crt`**：`caddy reload` 在"配置文本没变"时跳过整个加载（日志只有 `config is unchanged`），内存里仍是旧证书，而脚本打印"应用成功"。**实测踩到**：对外 CN 还是旧值。注释里写指纹也没用 —— Caddyfile 适配成 JSON 时注释会被丢弃。
  - B 让后端直接调用 `caddy reload`：后端容器没有 docker socket（**也不该有**），且会破坏"容器只读、cap_drop ALL"的容器安全基线。
  - C 每次换证书都重启 caddy 容器：会有可见断流（与零停机目标冲突）。
- **理由**：**路径变化 = 语义变化**，这是唯一能让 `caddy reload` 真的重新加载证书、且不依赖"改配置文本"的手段。"标记 + 轮询"把"需要宿主机权限的动作"收回宿主机 —— 后端唯一能做、也唯一该做的动作就是"放一个标记"。
- **代价**：
  1. **生效有延迟**（最多一个 cron 周期 ≈ 60s），前端只能显示 `pendingApply: true`，不能承诺"已生效"；
  2. 依赖 cron 装了 `watchdog.sh`；没有 watchdog 的部署（纯本地直跑）根本不涉及此路径（无 Caddy）；
  3. 磁盘上会短暂存在多份私钥副本（旧的会被清理，同一时刻最多两份）；
  4. 缺 `openssl` 时退回直接引用原文件并 `warn` 说"换证书需重建 caddy"（`lib.sh:263-268`）—— **降级是显式的，不是静默的**。
- **重新评估**：Caddy 支持"证书热加载 API"或我们改用文件监听（Caddy 的 `tls` 已能自动重载同路径文件的变化）时，可以去掉版本化命名。

### ADR-18 单文件 bind mount 绑 inode：配置必须原地覆盖，切流必须以运行时状态断言
> 状态：**Accepted（v2.0 反向补录）**。

- **决策**：
  1. `Caddyfile` 的最终写入**禁止用 `mv`**，必须 `cat "${CONFIG}.new" > "${CONFIG}"` 原地覆盖（`lib.sh:343-350`）；
  2. `caddy_reload` 在 `reload` 返回 0 之后**必须**用 `caddy_assert_upstreams` 校验运行时的上游列表（`lib.sh:462-489`）；
  3. 断言**顺序敏感**（`lb_policy first` 只走第一个可用上游），不是集合比较。
- **备选**：`mv` 原子替换（教科书做法）；只信 `caddy validate` + `reload` 退出码。
- **理由（这是一次真实事故的复盘）**：docker 的单文件 bind mount 绑的是 **inode**，不是路径。`mv` 之后宿主机路径指向新 inode，而容器内 `/etc/caddy/Caddyfile` 仍指向旧 inode（旧内容）→ `caddy validate`/`reload` 读到的永远是**旧配置**，`reload` 返回 0 且日志写 `config is unchanged` —— **切流静默失效，而脚本还会打印"已平滑重载上游"**。该缺陷从 2026-11-13 存在到 2026-11-15，表现为每次发布丢 1~4 个 502。
- **与 GATE-01/02、ADR-15 是同一个病**：**判定器只看退出码，不看运行时状态**。三条修正的共性都是"把判据换成可观测的运行时事实"（上游列表、证书指纹、`fileLogging`）。
- **代价**：
  1. 原地覆盖**不是原子的**（写入过程中容器可能读到半个文件）—— 用"先写全 `.new` 再一次性 `cat`"把窗口压到一次 IO，且 `reload` 前有 `validate` 兜底；
  2. 断言多等最多 1 秒（10 × 100ms），发布总时长略增；
  3. 断言失败时 `die`，发布中止 —— 这是**刻意的**（"切流失败应该是什么都没发生"）。
- **重新评估**：把 `Caddyfile` 改成目录挂载（而不是单文件）时，`mv` 可以恢复；但断言仍应保留。

### ADR-19 发布 / 回滚 / 看门狗共用一把 `flock`
> 状态：**Accepted（v2.0 反向补录）**。

- **决策**：`release.sh`、`rollback.sh`、`watchdog.sh` 三者各自对 `deploy/run/*.lock` 做 `flock -n 9`；拿不到锁**立即退出并说明原因**，绝不排队等（`release.sh:215-224`、`rollback.sh:77-81`、`watchdog.sh:44-50`）。
- **备选**：`set -e` + PID 文件；`mkdir` 锁目录；排队等待。
- **理由（实测踩到）**：两次 `release.sh` 并行时——各自算出不同 TAG 但共用"当前活跃槽位"这一个状态；后一次会把前一次刚起的新槽位**重建**成自己的镜像；于是前一次的冒烟核对拿到别人的 `GTD_BUILD` → "构建标识与本次发布一致"失败（**看起来像代码坏了，其实是撞车**）。实测时间线：`12:40:30`/`12:40:42`、`12:41:48`/`12:41:55` 两组 tag，每次"第二个 tag"都是另一次发布建的。
- **为什么选 `flock` 而不是 PID 文件**：PID 文件在进程被 `kill -9` 后成僵尸（"锁还在但没人持有"），而 `flock` 随文件描述符关闭自动释放 —— **不依赖清理逻辑**。项目也不需要装别的东西（`flock` 是 util-linux 自带）。
- **代价**：
  1. `flock` 缺失时退化为**无锁**（脚本用 `command -v` 判断后静默跳过）→ 在极简镜像/宿主机上保护失效。**这是已知缺口**（§12.1 R-19）；
  2. 看门狗拿不到锁就 `exit 0`（"这一轮让路"），意味着**证书应用可能被推迟一轮**（≤ 1 分钟）；
  3. 锁的粒度是"整个发布"，无法并行发布两个不同环境。
- **重新评估**：需要并行发布多环境时，锁文件必须按环境区分。

### ADR-20 早退响应必须先消费请求体；`_body_consumed` 的语义是"流已前进"
> 状态：**Accepted（v2.0 反向补录）**。

- **决策**：
  1. `_discard_body()` 必须在**任何早退响应之前**调用（静态 405、路由 404、跨站 403、媒体类型 415、坏 JSON 400 五条分支）；
  2. `_read_body()` 在**读到字节之后、解析 JSON 之前**就置 `_body_consumed = True`（`httpd.py:378`）；
  3. `_body_consumed` **每个请求重置**（`_handle` 开头，`httpd.py:679`）；
  4. 超大 body（> `max_body_bytes`）不读进内存，直接 `close_connection = True`。
- **备选**：只依赖 `Connection: close`；让 `BaseHTTPRequestHandler` 自己处理。
- **理由（实测现象）**：不消费 body 时，残留字节会成为同一条 keep-alive 连接上**下一个请求的请求行** —— 实测同一个连接上先发带 body 的 GET，后一个正常 GET 直接变 `501`（`Unsupported method ('{}POST')`）；`POST /api/rows`（`Origin: evil`）得 403 后，紧接着的正常登录变成 501。经反向代理的连接池还会**跨连接传染**，构成"1 个请求换回 2 份响应"的**请求走私原语**。
- **为什么标记语义是"流已前进"而不是"解析成功"**：解析失败（`BAD_JSON`）时字节**已经**被读走了；若标记为"未消费"，`_discard_body` 会再读一次 → 读不到 → 卡 2 秒超时 → 关连接。**把"已读"当"未读"是另一个坑**。
- **代价**：
  1. 每个写请求多一次 `rfile.read` 的簿记（可忽略）；
  2. `_discard_body` 的读有 2 秒 `settimeout` 兜底，恶意"声明长度但不发"会**占住一个处理线程 2 秒**（有界，不会无限占）；
  3. 任何新增的早退分支都可能漏掉 `_discard_body()` —— 目前靠 3 个专项测试兜（§8.5）。
- **重新评估**：改成长连接 = `close`（`Connection: close` 逐请求）时，风险面消失，但会牺牲性能。

### ADR-21 集成域用**独立 Bearer 令牌**，不复用会话 Cookie；进程内第二监听器
> 状态：**Proposed（v2.0 新增，设计未实现；待 G1 裁决）** —— 全文见 §16。

- **决策**：
  1. 机器访问用**独立令牌**（`api_tokens` 表，只存 `sha256`），**不复用** `gtd_session` Cookie；
  2. 令牌**绑定既有 `users.id`**，逐请求 `load_context` + 复用 `store`/`policy`，**权限面等于该账号本人**（不放大、不缩小）；
  3. 集成域是**同一进程内的第二个/第三个监听器**，只绑 `127.0.0.1`、默认关闭；
  4. 令牌带**作用域**（`read` / `write:edit` / `write:archive` / `write:delete` / `mcp`），默认只读。
- **备选**：
  - A **复用会话 Cookie**：脚本没有 Cookie jar 也没有 `Origin` 头 → 必须放宽 `SameSite=Lax` 与 `Origin` 校验，**直接削弱浏览器侧的 CSRF 防线**；且 Cookie 的 30 天滑动续期与"机器凭据应有独立吊销面"相冲突。
  - B **JWT 自签名令牌**：无状态、无需查表，但**无法即时撤销**（REQ-052 要求"撤销即时生效"），且要用 HMAC 手搓签名/校验（自研密码学 = 缺陷温床）。
  - C **独立小进程 / 独立端口服务（另一个 python 进程）**：又回到"跨进程不共享状态"（限速、令牌缓存），且要额外管一份 PID/日志/健康检查 —— 与 C-15 的"进程内"要求冲突。
  - D **照搬参照实现的"一个全局令牌全量读写"**：本系统是多团队 + 行级权限，全量令牌等于"一个令牌打穿所有团队边界"。
- **理由**：
  1. **威胁模型不同**：Cookie 是"浏览器自动附带"的凭据，防的是 CSRF；Bearer 是"客户端显式附带"的凭据，防的是泄露后的横向移动。两者用同一套机制会让两边都变弱。
  2. **作用域是 REQ-052 的硬需求**（三类写权限独立授予），Cookie 没有作用域这个概念。
  3. **可撤销性**：令牌有独立行、独立 `revoked_at`，撤销即时生效；会话只有"删行"一种手段，且与浏览器登录态耦合。
- **代价**：
  1. **多一张表 + 多两条迁移**（v5/v6），且**不进 `COLUMNS`/`WRITABLE_TABLES`**（否则客户端可自助发令牌 → 提权）；
  2. **令牌就是"等同于账号的长期凭据"**：任何拿到令牌的人拥有该账号在 API 面的全部权限。这是 REQ-047"复用 `policy`"的必然结果，**不可通过作用域之外的手段消除**（§16.3 的"默认只读 + 独立吊销 + 不入日志"是仅有的缓解）。
  3. **前端多一个设置页**（REQ-055"界面可达"），且令牌明文只在创建响应里出现一次。
- **重新评估**：需要 OAuth2 / 第三方应用授权（那时应引入标准的 authorization code 流程），或需要"跨实例"的令牌（那时应上真正的密钥管理）。

---

## 4. 认证与会话架构

> 严格模式：本节对应 `HARD_GATES.md`「登录认证、Token/Session、密码、账号安全」。

### 4.1 时序（建号 / 登录 / 改密 / 登出）

| 动作 | 时序 | 证据 |
|---|---|---|
| ~~注册~~ **建号（v1.1 替代注册）** | 自助注册端点已从 `ROUTES` 移除（`POST /api/auth/register` → 404）。建号走 `POST /api/admin/accounts`（三级建号）→ `_require_user`（含闸门）→ `_account_actor` 从会话组装 actor → `store.create_account` 单事务写 `users` + 可选 `team_members` + 审计 `app_logs` → 201（**不建会话、不设 Cookie**）→ 一次性返回初始口令 | `httpd.py:1050-1075`；`store.py:389-527`；`06` §10.3 |
| 登录 | `POST /api/auth/login` → 组 `throttle_key=email.lower()|client_ip` → `is_rate_limited` → 查用户（邮箱不存在与口令错**同码** `INVALID_CREDENTIALS`，防账号枚举）→ `clear_failures` → `_begin_session` → `needs_rehash` 则升级散列 → **一次 `commit()`** → 200 + Set-Cookie；同时显式写 `handler._uid`（这条路径不经过 `_current_user`，不补就丢审计） | `httpd.py:954-990`；`test_06`【测试·有名】 |
| 改密 | `POST /api/auth/password` → `_require_user` → 新密码强度（`WEAK_PASSWORD`）→ 校验 `oldPassword`（`INVALID_OLD_PASSWORD`）→ `UPDATE password_hash + must_change_password=0 + updated_at` **同一条 SQL** → **删除该用户除当前令牌外的所有 sessions** → 一次 `commit()` → 200 | `httpd.py:1003-1031`；`test_09`【测试·有名】 |
| 登出 | `POST /api/auth/logout` → 按指纹删当前 session → 200 + `Set-Cookie` `Max-Age=0`；前端 `signOut()` 静默吞异常保证能回登录页 | `httpd.py:993-1000`；`test_07`【测试·有名】 |
| 会话探测 | `GET /api/auth/session` 返回 `{user: null}` 或 `{user}`；`getSession()` 捕获任何异常返回 `null`，作为门禁判断（不抛错） | `httpd.py:944-946`；`api.js`（内容锚点：`getSession`） |

> `--set-password <email>` 是「忘密码自救 + 关闭注册后开账号」的 CLI 通道：账号不存在则新建，存在则改密并清空该用户所有会话 —— **现已收敛到 `store.reset_password`**（`store.py:581-631`），不再由 `server.py` 自己拼 `INSERT INTO users`。
> `--create-admin` / `--grant-admin` / `--set-password` 三者正交，且**一律把 `must_change_password` 置 1**（判据是"口令这一环是否经过运维之手"）。
> **登录语义**：`POST /api/auth/login` 的响应 `user` 含 `isSystemAdmin` / `mustChangePassword` 两个布尔字段（加法式），使前端登录后即可判定是否需要跳强制改密页；服务端闸门**不依赖**前端是否读了它。

### 4.2 会话令牌生命周期

| 阶段 | 行为 | 证据 |
|---|---|---|
| 生成 | `new_token()` = `secrets.token_urlsafe(32)`；`new_user_id()` = `'u_' + token_hex(8)`（TEXT 主键，与云版 uuid 文本语义一致） | `security.py:129-141` |
| 存储 | `sessions` 表仅存 `token_hash = sha256(token)`、`user_id`、`created_at`、`expires_at`、`last_seen_at`、`user_agent`；不存明文令牌 | `httpd._begin_session`（`httpd.py:598-613`）；`db.py` 的 `sessions` DDL |
| 有效期 | `SESSION_TTL_SECONDS = 30*24*3600`（30 天）；**`expires_at` 固定不滑动延长**，只刷新 `last_seen_at` | `security.py:28,144-155`；`httpd.py:554-568` |
| 活跃刷新 | 仅当 `seconds_since(last_seen_at) >= SESSION_TOUCH_SECONDS(300)` 才 UPDATE，且走 `db.bookkeeping_commit`（幂等，不参与"是否可重放"判定） | `httpd.py:510-574`；`db.py:545-556` |
| 过期清理 | 读到过期会话时顺手 `DELETE`，删除失败也不影响返回 `None`（不把"登录已失效"变成 500） | `httpd.py:534-542` |
| 主动失效 | 登出（删当前）、改密（删其它）、`--set-password`（删全部） | `httpd.py:993-1000,1026-1029`；`store.py:622` |

### 4.3 Cookie 属性矩阵

`_session_cookie()` 输出固定 `gtd_session=<token>; Path=/; HttpOnly; SameSite=Lax; Max-Age=<ttl>` 【代码·已核：`httpd.py:615-628`】。

| 属性 | 取值 | 条件 | 作用 / 证据 |
|---|---|---|---|
| `HttpOnly` | 恒有 | — | 脚本读不到令牌，XSS 无法直接窃取登录态（`httpd.py:11-13`） |
| `SameSite=Lax` | 恒有 | — | 跨站请求不带 Cookie，天然挡 CSRF；是第三层防线 |
| `Path=/` | 恒有 | — | 全站同源携带（`api.js` 的 `credentials:'same-origin'`） |
| `Max-Age` | `2592000`（30d） | 登录；登出为 `0` | `security.py:28`；`httpd.py:615` |
| `Secure` | **条件附加** | 仅当 `trust_proxy==1` **且** `X-Forwarded-Proto == 'https'` | 明文 HTTP 下浏览器不会回传带 Secure 的 Cookie；该头同样以信任代理为前提（`httpd.py:623-628`）。`test_secure_cookie_behind_https_proxy`（`tests/test_api.py:3830`） |
| 纵深防御 | 写操作额外校验 | `Content-Type: application/json` + `Origin`/`Host` 一致 + **`Origin: null` 直接拒** | `httpd.py:431-446,714-737`；`test_32/33`、`test_origin_403_discards_body_keepalive_stays_clean`（`:2486`） |
| CORS | 不开 | `do_OPTIONS` 直接 405；无 `Access-Control-Allow-*` | `httpd.py:650-652`；`test_36`（`:614` 附近）【测试·有名】 |

### 4.4 限流键与 `GTD_TRUST_PROXY` 的关系

- 键：`f'{email.lower()}|{handler._client_ip()}'`（登录，`httpd.py:960`）；日志上报键：`f'log|{client_ip}'`（`httpd.py:1214`）。
- `_client_ip()`：`trust_proxy=1` 时取 `X-Forwarded-For` **第一个值**，否则取 TCP 连接地址 `client_address[0]`（`httpd.py:456-467`）。
- 计数：`register_failure`/`bump_counter` 同一实现，`WINDOW_SECONDS=900`、`MAX_ATTEMPTS=10`，窗口过期自动清零，`clear_failures` 在登录成功后清空（`security.py:162-199`）。
- **因果链**：`GTD_TRUST_PROXY=0` 时伪造 XFF 无效（`test_40`）；置 1 后必须同时满足「槽位端口只绑回环」+「Caddy 覆盖 XFF」，否则客户端伪造值会排到真实 IP 前面被 `_client_ip` 取到（`Caddyfile.template:50-56`）。详见 §6.4 配对表。

### 4.5 改密踢出其它会话

`UPDATE users SET password_hash = ?, must_change_password = 0, updated_at = ?` 与 `DELETE FROM sessions WHERE user_id = ? AND token_hash <> ?` 在**同一 `conn.commit()`** 内完成（`httpd.py:1022-1030`）；当前令牌从 Cookie 取指纹保留。`test_09` 断言：另一会话随后访问 `/api/workspace` 得 401，当前会话仍 200，旧密码不能登录、新密码可以【测试·有名】。

**`must_change_password = 0` 必须与 `password_hash` 同一条 UPDATE**：漏掉的后果是**改密成功但永远用不了**（每次都被闸门 403，且再改一次也一样）—— 这是 CR-2026-001 最容易被漏测的故障。清零必须发生在**原密码校验通过之后**，否则一次错误尝试就把用户放行出去了。

### 4.6 三级建号（v1.1 新增，替代自助注册）

```
调用者身份            → 允许创建的账号                      → 依据
─────────────────────────────────────────────────────────────────────────
系统管理员            → 任意团队的 admin（**只有 admin**）    → 06 §10.3.3 #3-#10
（users.is_system_admin=1）  可省略 teamId（建号但暂不入队）   （可对任意 teamId 授 admin；不能建 member）
团队 owner / admin    → 仅本团队的 member                    → 06 §10.3.3 #11-#14
（team_members.role）        必须显式给 teamId
member / 非成员 / 未登录 → 不能建号                          → 06 §10.3.3 #1、#15-#17
```

**裁决 2（主理人 2026-11-14，v1.3）：系统管理员只能建 `admin`，不能建 `member`。**

- `role` 与 `teamId` **同现同缺**：省略 `teamId` 时 `role` 必须也省略（显式传任何角色 → 400 `ROLE_NOT_ALLOWED`）；给了 `teamId` 而省略 `role` 时**默认 `admin`**。
- 理由：需求原句是"系统管理员创建**团队管理员**"；给 `member` 会架空"团队管理员管成员"这条委派链，并让系统管理员获得"往任何团队塞人"的能力。
- 我原先的反对意见（"否则新团队产生不了第一个管理员"）**不成立**：系统管理员建**无团队账号** → 对方自己登录、改密、**自己建团队成为 owner**（`store.create_team_with_owner`，`store.py:208-250`）→ 再由他建成员。这条路径不碰 `member`。
- 副产品：团队成员的来源收敛成"团队管理员创建"与"本人建团队"两条可审计路径（审计事件只有 `account.created`）。
- **代码确认【代码·已核】**：`SYSADMIN_CREATABLE_ROLES = ('admin',)`（`store.py:321`），分支判定在 `store.py:436-450`；团队管理员分支强制 `role == 'member'`（`:461-463`）。`_normalize_account_inputs` 的 `default_role` **由调用方按身份传入**，不在函数里写死 `'member'`（`store.py:335-353`）。

**裁决 1（主理人 2026-11-14，写死不可再议）：「团队管理员」= `owner` + `admin`，沿用 `ctx.is_admin()` 的合并语义，`policy.py` 零改动。**

- 校验代码**必须**是 `ctx.is_admin(teamId)`（`policy.py:60-61`，判定 `role in ('owner','admin')`），**不得**写成 `ctx.is_owner(teamId)`。
- 与前端既有判断一致：`js/views.js` 用 `role === 'owner' || role === 'admin'` 判"能不能管成员"。
- 与 `policy` 的 `team_members.insert`（`is_admin(tid) or is_owner(tid)`，`policy.py:161-172`）语义对齐 —— 注意 `is_admin` 已包含 owner，那行里的 `or is_owner` 是冗余但无害的历史写法；**新增代码不要照抄这种冗余**。
- CR-2026-001 的硬约束是 **`policy.py` 零改动**，因此这一层判定只能出现在 `store.create_account` 的显式分流里（§5.6）。
- **授权判定在 `store.create_account` 内显式分流**，不复用 `policy.can_insert`：`policy` 的 `team_members.insert` 只表达"团队管理员"这一层；系统管理员若不是该团队成员会**误拒**，若是成员又会被当成普通管理员**误放**。更根本的是 `AuthzContext` 里没有"系统管理员"这个概念（`policy.py:50-67`），硬塞进去会让策略层承担实例级概念。详见 §5.6。
- **`owner` 不可经接口授予**：与 `policy._team_members_policy.update_keep` ①「owner 行的 role 不可被修改」同源（`policy.py:187-191`）；`team_members.insert` 也直接拒绝 `role='owner'`（`policy.py:161-172`）。
- **既有账号加入团队不走本接口**：`EMAIL_EXISTS` 的错误文案直接指向"把邀请码发给对方，由对方自行加入"（`store.py:477-484`）。这条分工让"建号"永远不触碰已有账号 —— 从根上消掉"用建号接口改别人账号/口令"的越权面。
- **建号不建会话、不设 Cookie**：创建者的会话不能被切换。

### 4.7 强制改密闸门（v1.1 新增）

| 项 | 内容 |
|---|---|
| 触发 | 会话有效 **且** `users.must_change_password = 1` **且** 路径不在白名单 |
| 响应 | `403 PASSWORD_CHANGE_REQUIRED`，`message` 即最终展示文案（「初始密码尚未修改，请先设置新密码后再继续（当前操作已锁定）」） |
| 优先级 | `401 UNAUTHENTICATED` > `403 PASSWORD_CHANGE_REQUIRED` > 端点自身授权错误（`FORBIDDEN`/`ADMIN_ONLY` 等） |
| 位置 | **`_require_user`（`httpd.py:576-587`）内部**，`_current_user` 之后、`return user` 之前 —— 仍在 `_dispatch` 的重试循环内，继承单连接/单事务/可重放判定 |
| 白名单（**5 逻辑项 / 6 条路径，统一登记进常量**） | `GET /api/health`、`GET /api/auth/session`、`GET /api/auth/profile`、`GET /api/auth/me`、`POST /api/auth/password`、`POST /api/auth/logout`（`PASSWORD_CHANGE_EXEMPT`，`httpd.py:78-85`） |
| 不参与判定、也不登记 | `POST /api/logs`（走 `_current_user`，天然放行，且"改密失败"需要留证）、全部静态资源（`_dispatch` 前置分支，连 `/api/` 前缀都不进） |
| **v2.0 新增的一处例外说明** | `GET /api/logs` **受闸门约束**（它走 `_require_instance_admin` → `_require_user`），因为它是运维接口："没改初始口令的管理员先改密码"。这是刻意的：日志里含全员 IP 与路径，不能给一个"尚未完成账号交接"的会话开着 |
| 清零 | 改密时与 `password_hash` **同事务、同一次提交**（§4.5） |
| 实现陷阱 | 白名单必须比对**归一化后**的路径（`self._path`，在 `_handle` 里赋值，`httpd.py:685`）；用 `self.path` 会让 `POST /api/auth/password/` 落不进白名单 → 账号永久锁死。判门读 `_password_change_exempt()`（`:589-596`） |

**为什么放 `_require_user` 而不是 `_dispatch`**：四条理由见 ADR-14；核心是"新增端点自动继承"（fail-closed）与"匿名端点不需要在闸门里写例外"。白名单虽然只有 6 条路径，但**必须显式登记**（含当前天然放行的 health/session/logout）—— 多几行常量的成本可忽略，收益是将来有人把 `h_session` 重构成 `_require_user` 时不会把会话探测打死。**读也拦**：`/api/workspace` 一次返回全部可见数据，只拦写等于没拦。完整契约（白名单表、前端字段消费、7 条断言其中 3 条必做）在 `06` §10.5。

### 4.8 会话令牌与机器令牌的边界（v2.0 新增，§16 的前置说明）

| 维度 | 会话令牌（`gtd_session`，**已实现**） | 机器令牌（PT，**§16 设计未实现**） |
|---|---|---|
| 载体 | Cookie（浏览器自动附带） | `Authorization: Bearer`（客户端显式附带） |
| 主要威胁 | CSRF、XSS 窃取 | 令牌泄露后的横向移动、误授全量权限 |
| 作用域 | 无（登录即全权限） | **有**（`read` / `write:edit` / `write:archive` / `write:delete` / `mcp`） |
| 撤销粒度 | 删行（登出/改密/重置口令） | 删行 + `revoked_at`（**即时生效**，REQ-052） |
| 有效期 | 30 天固定 | 独立（建议默认 90 天 + 可设永不过期需显式勾选） |
| 存储形态 | `sha256(token)` | `sha256(token)`（同一纪律） |
| 关联表 | `sessions` | `api_tokens`（新表，**不进 `COLUMNS`**） |
| 能否互相兑换 | **不能**（设计上禁止：Cookie → 令牌会绕过"令牌是显式授权"的语义） | **不能** |
| 是否受改密闸门约束 | 是 | **是**（重要：否则令牌成了闸门的后门） |

**三条写死的边界**：

1. **不许把 Cookie 校验逻辑套到 Bearer 上**：`_current_user` 只认 Cookie（`httpd.py:519-574`）。集成域要有自己的 `_api_user(conn, token)`，两者的成功路径都汇入同一个 `load_context`。
2. **不许给 `/api/*` 主入口开 Bearer**：那会让"浏览器接口"与"机器接口"共用一张鉴权表，`Origin`/`SameSite` 这层防护立刻变成可选（一旦有 Bearer 通道，攻击者只要能发请求就不需要 Cookie）。
3. **不许让令牌绕过 `must_change_password`**：见上表最后一行。这一条如果漏了，§4.7 的整个闸门就有一个不花钱的旁路。

---

## 5. 权限模型（从 `policy.py` 逐表还原）

> 严格模式：本节对应 `HARD_GATES.md`「权限系统 / RBAC / 数据权限 / 越权用例」与总纲 §4 G1「权限矩阵与 `policy.py` 逐条对齐」。

### 5.1 模型定义

- 角色来自 `team_members.role`，`AuthzContext` 提供 `is_member / is_admin(owner|admin) / is_owner / role_of`（`policy.py:50-67`）。
- 成员有效性：`load_context` 只纳入 `status=='active'` 的行（`policy.py:351-366`）——**刻意收紧**，见 §5.3 K-5。
- 策略结构：`Policy(select, insert, update, delete)`，`update` 为 `PolicyUpdate(using, check, keep)`，分别对应 SQL 的 `USING`（这一行当前能否被我改）、`WITH CHECK`（改完之后的行是否仍合法）、`keep`（必须同时看到改动前后两行才能判的规则，本地版补充）（`policy.py:26-34`）。
- 判定入口：`can_select/can_insert/can_update/can_delete`（`policy.py:330-348`）；`can_update` 要求 `using(old) AND keep(old,new) AND check(new)` 同时为真。
- 团队键映射 `TEAM_KEY`：`teams` 用自身 `id`，其余 9 表 + `app_logs` 用 `team_id`（`policy.py:36-47`）。
- `Denied` 只用于「整个操作在权限上根本不可能成立」（如往非成员团队插数据），**不用于**「看不见/改不到」（那返回空结果）（`policy.py:317-328`）。

谓词语义（供下表阅读）：

| 谓词 | 语义 | 证据 |
|---|---|---|
| `_personal_or_member(t)` | `team_id IS NULL` → `owner_id==uid`；否则 `is_member(team_id)` | `policy.py:83-89` |
| `_member_only(t)` | 必须 `is_member(team_id)` | `policy.py:92-95` |
| `_own_writable(t)` | `owner_id==uid` 且（`team_id IS NULL` 或 `is_member(team_id)`） | `policy.py:110-122` |
| `_admin_or_row_owner(t)` | `is_admin(team_id) OR owner_id==uid` | `policy.py:98-103` |
| `_member_update(t)` | `using = check = _member_only(t)` | `policy.py:132-133` |
| `_own_only_update(t)` | `using = _personal_or_member`、`check = _own_writable`（**含 owner 限制**） | `policy.py:125-129` |

### 5.2 逐表策略矩阵（`POLICIES`，`policy.py:267-310`）

| 表 | select | insert | update.using | update.check | delete | 行号 |
|---|---|---|---|---|---|---|
| `teams` | `owner_id==uid OR is_member(id)` | `owner_id==uid` | `owner_id==uid OR is_admin(id)` | 同 using | `owner_id==uid` | 140-154 / 268 |
| `team_members` | `user_id==uid OR is_member(team_id)` | `is_admin(tid) OR is_owner(tid)`，**且 `role!='owner'`** | `is_admin(tid) OR user_id==uid` | `is_admin(tid)` 或（`user_id==uid AND role 未变`） | `role!='owner' AND (is_admin OR user_id==uid)` | 157-221 / 269 |
| `work_items` | `_personal_or_member` | `_own_writable` | `_personal_or_member` **（不含 owner 限制）** | `_personal_or_member`（与 using 同一表达式） | `_personal_or_member` | 270-279 |
| `projects` | `_member_only` | `_member_only` | `_member_only` | `_member_only` | `_admin_or_row_owner` | 280-283 |
| `areas` | `_member_only` | `_member_only` | `_member_only` | `_member_only` | `is_admin(team_id)` | 284-288 |
| `contexts` | `_personal_or_member` | `_own_writable` | `_personal_or_member` | **`_own_writable`（含 owner 限制，云版遗留）** | `_personal_or_member` | 289-297 |
| `resources` | `_member_only` | `_member_only` | `_member_only` | `_member_only` | `_admin_or_row_owner` | 298-301 |
| `progress_logs` | `_personal_or_member` | `_own_writable` | `owner_id==uid` | `owner_id==uid` | `owner_id==uid OR is_admin(team_id)` | 224-236 / 302 |
| `reviews` | `_personal_or_member` | `_own_writable` | `owner_id==uid` | `owner_id==uid` | `owner_id==uid` | 239-248 / 303 |
| `on_call` | `_member_only` | `_member_only` | `_member_only` | `_member_only` | `is_admin(team_id)` | 304-308 |
| `app_logs` | `owner_id==uid` | `owner_id in (uid, None, '')`（允许登录前留证，不允许替他人写） | `deny` | `deny` | `deny`（日志不许改删，避免掩盖故障证据） | 251-264 / 309 |

- 系统表 `users/sessions/settings/schema_version` **不在** `WRITABLE_TABLES`，永不对通用 CRUD 开放（`db.py:408,425`）。
- `load_context` 对 `team_members` 的读取是「受信任的内部读取」，绕过该表自身策略以避自引用（对应云版 `SECURITY DEFINER`，`policy.py:351-356`）。
- 越权覆盖用例：`test_14`~`test_21`（个人隔离、团队共享但不公开、跨团队插入被拒、伪造 owner、禁止自我提权、owner 行保护、停用成员失权、日志删除需归属/管理员）【测试·有名】；v2.0 新增两条同族用例：`test_team_admin_cannot_deactivate_owner`（`tests/test_api.py:2559`）、`test_team_admin_cannot_insert_second_owner`（`:2592`）。
- **v2.0 注意**：`app_logs` 的 `select` 仍是"只能看自己的"，因此 §16.7 的"开放接口审计"要以**运维可见**的形式落地时，只能走 `GET /api/logs`（文件）或另开实例级查询接口 —— **不许**为了让它可见而放宽 `app_logs.select`（那会让每个用户看到别人的日志）。这是 §16.7 的一条硬约束。

### 5.3 刻意保留的设计取舍（**改动前必须先读，勿当缺陷「顺手修好」**）

| # | 取舍 | 代码 | 文档原句 | 为什么保留 |
|---|---|---|---|---|
| K-1 | `work_items` 的 update **不含** `owner_id` 必须是我 | `policy.py:270-279` | `README.md:88`「团队成员必须能推进别人建的工单，否则看板流转当场失效」 | 云版 RLS 的 USING/CHECK 本就是同一条表达式；改了会破坏看板流转 |
| K-2 | `contexts` 的 update **含** owner 限制（非创建者改不动团队情境） | `policy.py:289-297` | `README.md:89`「云版本原有的形状，如实保留，未顺手『修好』」 | 差异本身是需要被记录的事实 |
| K-3 | 被权限拦下**返回空数组而非抛错** | `store.py:169-202`；`policy.py:317-328` | `README.md:87` | 前端 `assertAffected` 依赖它如实提示；不泄露数据存在性 |
| K-4 | 客户端提交的 `owner_id` **一律丢弃**，归属服务端注入 | `db.split_payload`（`db.py:805`）；`store.py:148-150` | `README.md:86` | 云版靠列默认值 `auth.uid()`，本地等价防线即"给什么都不采纳"；`test_17` 断言 |
| K-5 | `team_members.status != 'active'` **视为非成员**（有意收紧，与云版不同） | `policy.py:361-365`；`load_context` | `README.md:90`「与『停用即失去访问权限』的产品语义相矛盾」 | 前端 `saveMember` 确认弹窗就是这么承诺的；`test_20` 断言停用后无法凭邀请码自行恢复 |
| K-6 | 双域共用一张 `work_items`（`team_id IS NULL` = 个人） | `db.py` 的 `work_items` DDL；`policy.py:11-12` | `README.md:85` | 见 ADR-07 |

> 总纲 §2 把 K-1/K-2/K-3/K-4/K-5 列为「决策→实现断链」的实例，要求显式写进 `04` 与 `05` 并标注「刻意保留，改动前必须先读」。任何针对 K-1~K-6 的「修复」都必须先开 `CR-`，并同步 `03/05/06/07` 与回归范围。

### 5.4 G1 门禁评审要点（本文提交给 G1 的判定项）

1. **逐条对齐**：`02` 的角色权限矩阵必须与 §5.2 的 11 行 × 5 列一致；不一致处必须显式标为「刻意取舍」（K-1~K-6）或记为缺陷（`BUG-`）。
2. **新增可写表的连锁登记**（漏一处即 G1 不通过）：`db.COLUMNS`、`POLICIES`、`EXPECTED_TABLES`、`TEAM_KEY`（若按团队隔离）、`BACKUP_TABLES`、`store.load_workspace`（若要进工作区）、`js/api.js` 导出、`06` 接口契约、`05` 迁移 SQL + 回滚 SQL。**v2.0 追加第 9 处**：`_MIGRATIONS`（见 §7.6）。
3. **后端校验点**：每个受保护接口 `_require_user`；每个数据读写过 `can_*`；前端隐藏按钮不等于后端授权；令牌/密码/密钥不进日志。
4. **越权用例结果**：`test_14`~`test_21` + `test_40` + v2.0 的 owner 保护两条全过；空数组语义未被改成抛错。
5. **追溯链**：每条 REQ 至少追到"一条 BR + 一处实现 + 一条 TC + 一张 ACC-G1"（总纲 §5.3 最低要求）。

### 5.5 身份层级图（v1.1 新增）——**系统管理员 ≠ 所有团队的管理员**

```
┌─ 实例级（users 表，跨团队） ───────────────────────────────────────────────┐
│  L0  系统管理员   users.is_system_admin = 1（迁移 v1 新增列；CLI 引导产生）  │
│      能做什么：创建/授任账号（任意团队的 admin 或 member）、改 AI 配置、      │
│                管 TLS 证书、读服务端访问日志（后两项 v2.0 新增）              │
│      不能做什么：读写任何团队的业务数据（除非他本人是该团队成员）              │
└──────────────────────────────────────────────────────────────────────────┘
┌─ 团队级（team_members 表，一团队一行） ────────────────────────────────────┐
│  L1  团队 owner   role='owner'   团队所有权；可授权 admin、可转让所有权(本期无)│
│  L2  团队 admin   role='admin'   管成员、管团队配置、可建 member 账号         │
│  L3  成员         role='member'  读写本团队业务数据；不能建号                 │
└──────────────────────────────────────────────────────────────────────────┘
```

| 层级 | 存储位置 | 判定入口 | 能否建号 | 能否读写业务数据 | 能否管证书/读服务端日志 |
|---|---|---|---|---|---|
| **L0 系统管理员** | `users.is_system_admin`（`_MIGRATIONS` v1） | `_current_user` 的 SELECT（`httpd.py:523-529`，多取两列）+ `_require_instance_admin`（`:1452-1473`） | **能**（**仅 `admin`**，任意团队） | **只有在其本人是 `team_members` 成员时才能**（`policy.load_context` 只读 `team_members`） | **能**（v2.0） |
| L1 团队 owner | `team_members.role='owner'` | `AuthzContext.is_owner`（`policy.py:66-67`） | 能（本团队 `member`） | 能（本团队） | **不能**（实例级操作与团队级无关） |
| L2 团队 admin | `team_members.role='admin'` | `AuthzContext.is_admin`（`policy.py:60-61`） | 能（本团队 `member`） | 能（本团队） | **不能** |
| L3 成员 | `team_members.role='member'` | `AuthzContext.is_member`（`policy.py:57-58`） | **不能** | 能（本团队） | **不能** |
| （非成员） | — | `memberships` 为空 | 不能 | 不能 | 不能 |

**四条必须写死的推论**（它们决定了本期的实现位置）：

1. **系统管理员在 `policy.py` 里没有对应物，也不应该有**。`AuthzContext` 只由 `team_members` 构造（`policy.py:351-366`），`POLICIES` 的 11 张表全部按 `team_id` 判。给 `AuthzContext` 加一个 `is_system_admin` 字段，会让"实例级"概念渗透进每一个行级谓词，而这些谓词是需要与云版 RLS 逐条对照的（ADR-06）。**结论：系统管理员这一层只活在 `store` 的受控函数（§5.6）与 httpd 的 handler / `_require_instance_admin`（§5.7）里，不进 `policy`。**

   **三条写死的禁止**（主理人 2026-11-14 确认）：
   - `AuthzContext.is_admin()`（`policy.py:60-61`）**不得**改成 `... or self.is_system_admin` —— 一旦这么写，系统管理员会立刻变成"所有团队的管理员"，团队边界当场消失。
   - **`policy.py` 在本 CR 内零改动**（diff 必须为空）。
   - **`ROLES` 枚举不新增系统管理员**（`js/config.js:76` 附近）。这不只是"概念归属"问题：`js/views.js` 用 `Object.keys(ROLES)` 渲染成员角色下拉框 —— 加进去会直接让"系统管理员"成为可授予给团队成员的选项，而 `team_members.role` 里根本没有这个值。
2. **"系统管理员"不等于"所有团队的管理员"**（最小权限）。他对业务数据的可见性**完全等于**他作为普通成员的可见性；不在任何团队里 → `load_context` 返回空 `memberships` → 所有业务表 `can_select` 全 False → 他看到的是空工作区。这是**刻意**的：把系统管理员的能力**限定在"建号与授权"**——"能造账号"与"能读数据"是两件事。
3. **反过来也成立**：一个团队的 owner **不是**系统管理员，看不到别的团队、不能建系统管理员、也不能改 AI 配置/管证书/读服务端日志。
4. **建号接口的授权不能落到 `policy` 上**（否则第 1 条被破坏），只能显式分流；而显式分流必须收敛在**一个**有名字的函数里，否则 httpd / CLI 两处会漂移 → §5.6。

### 5.6 系统管理员层的写入路径：`store.create_account()`（唯一受控入口）

**硬规则**：`POST /api/admin/accounts` 必须调用 `store.create_account(...)`。

- **不允许**在 `httpd.py` 里直接拼 `INSERT INTO users / team_members`：那会把"邮箱唯一性、角色枚举、团队存在性、审计同事务、单次提交"这五件事分散到 handler，并把重放安全性（`db.TrackedConnection`）的正确性再赌一次。
- **不允许**用 `policy.can_insert` 近似（理由见 §5.5 第 1 条）。
- **也不允许**在 CLI 里另写一份：`--set-password` 曾自己拼 `INSERT INTO users`，现已收敛到 `store.reset_password`。

**函数签名与职责边界**（完整版见 `06` §10.3.4；此处只固化架构约束）：

```python
store.create_account(conn, actor, *, email, name=None, team_id=None, role=None,
                     password_hash, is_system_admin=False, must_change_password=True)
store.grant_system_admin(conn, email)                    # --grant-admin；不存在返回 None，不建号
store.reset_password(conn, email, password_hash, *,
                     create_if_missing=True, must_change_password=True)   # --set-password
store.list_accounts(conn, actor, team_id=None)           # GET /api/admin/accounts
```

| 架构约束 | 内容 |
|---|---|
| `actor` 来源 | **只能**由 httpd 从会话组装（`_account_actor`，`httpd.py:1034-1047`）/ CLI 从命令行组装；**绝不**读请求体里的任何身份字段 |
| 明文口令 | **不进 store**。散列在 httpd/CLI 侧完成（`security.hash_password`），明文只存在于 httpd 的局部变量与响应体（`httpd.py:1063,1072`） |
| `is_system_admin` | HTTP 路径**恒不传**；store 内再做一次 `via == 'cli'` 双重保险（`store.py:489`）。**唯一的系统管理员创建入口是 CLI** |
| 校验顺序 | 先授权（`FORBIDDEN`/`NOT_TEAM_ADMIN`/`TEAM_REQUIRED`/`ROLE_NOT_ALLOWED`/`NO_SYSTEM_ADMIN`）→ 再参数（`INVALID_EMAIL`）→ 再唯一性（`EMAIL_EXISTS`）。**先授权不是为了好看**：反过来会向无权建号的人暴露"某邮箱是否已注册"（账号枚举） |
| 事务纪律 | `users` + 可选 `team_members` + 审计 `app_logs` **同一事务、一次 `commit()`**；取回新行**在提交之前**；提交之后只剩 `return` |
| 并发兜底 | `except sqlite3.IntegrityError` → `rollback()` → 命中 `users_email_uniq` 映射 `EMAIL_EXISTS` 409，其它映射 `ACCOUNT_CREATE_FAILED` 500 |
| 审计 | 与建号同事务写 `app_logs`（`event='account.created'`，`scope='account'`），**不含明文口令与散列**（`store._insert_account_audit`，`:364-387`）。不能调 `store.write_app_log` —— 它内部会 `conn.commit()`，破坏单次提交 |
| **新列不可被客户端篡改** | **结构性保障**，不是运行时校验：`users` 不在 `db.COLUMNS` → `WRITABLE_TABLES` 不含它 → `/api/rows` 的 `table` 过 `_assert_table()` 直接 404 `UNKNOWN_TABLE`，且 `test_35_unsupported_table_is_rejected` 已把 `insert('users')→404` 钉死。**客户端根本没有一条能触达 `is_system_admin`/`must_change_password` 的写入路径** |
| **CLI 三命令** | `--create-admin` / `--grant-admin` / `--set-password` **一律把 `must_change_password` 置 1**。`--set-password` 的踢会话与置 1 在**同一次 `commit()`** 里 —— "踢旧会话 + 强制改密"是同一件事的两半 |
| **口令重置的三分支**（`grant_system_admin`） | 提权是"口令这一环经运维之手"的操作，必须同时强制改密。三分支按 `(是否已是管理员, 是否已要求改密)` 穷举，**没有第四个分支**（`store.py:556-570`） |

> 为什么"审计同事务"值得单独一条：分开写会产生两种错账 ——「号建了但没审计」（可掩盖）与「审计了但号没建」（可诬告）。而"哪个账号是谁建的"是本期唯一能追责的线索（没有独立审计表，`app_logs` 是唯一结构化落点）。

### 5.7 实例级门槛：`_require_instance_admin`（v2.0 新增，唯一实现）

v2.0 之后，"实例级操作"从 1 处（AI 配置）扩到 3 处（AI 配置 / TLS 证书 / 读服务端日志）。如果每处各写一遍判定，漂移只是时间问题 —— 因此收敛成**唯一实现**：

```python
# httpd.py:1452-1473
def _require_instance_admin(handler, conn, *, what='该操作'):
    user = handler._require_user(conn)          # 认证 + 强制改密闸门
    if user.get('isSystemAdmin'):
        return user                              # 库内身份优先，不读环境变量
    if handler.cfg.is_admin_email(user.get('email')):
        _warn_env_admin_fallback(conn)           # 过渡期兜底 + 每进程只告警一次
        return user
    raise ApiError(403, 'ADMIN_ONLY', f'仅实例管理员可{what}：...')
```

| 项 | 内容 |
|---|---|
| 判据顺序 | **库内 `is_system_admin` 优先** → `GTD_ADMIN_EMAILS` 兜底（打弃用提示，`_warn_env_admin_fallback`，`httpd.py:1434-1450`）→ 否则 `403 ADMIN_ONLY` |
| 为什么还保留环境变量兜底 | 纯切 DB 而不兜底，升级后原白名单里的邮箱会**突然**失去权限（"我配了管理员却没权限"）。但兜底必须**可见**，否则 v1.2 删 env 方案时没人知道还有部署在依赖它。判据（何时可以删 env）= 告警日志归零 |
| 与团队级的关系 | **完全无关**。`what` 只影响文案，不影响判定；`is_admin(tid)` 与它没有任何组合关系 |
| 当前使用者（3 处，**缺一即不一致**） | ① `h_llm_config_set`（`httpd.py:1475`）② 证书三端点 `h_admin_cert_get/upload/delete`（`:1093-1136`）③ `h_access_logs`（`:1260`） |
| **显式不属于它的** | `/api/admin/accounts*` —— **`/api/admin/` 前缀表示"账号管理"，不表示"仅系统管理员"**。给这个前缀加统一门槛会把团队管理员这条主路径直接堵死（`httpd.py:1053-1055` 的注释就是为此写的） |
| 未登录 / 成员 / 其他成员账号 | 401 / 403 `ADMIN_ONLY` / 403 `ADMIN_ONLY`（`CertAdminTest.test_cert_get_requires_admin`、`AccessLogTest.test_get_logs_requires_admin` 各钉一条）【测试·有名】 |

**新增实例级接口时的登记纪律**：任何"只有实例管理员能做"的新端点，第一个动作必须是 `_require_instance_admin(handler, conn, what='...')`，**不得**自己在 handler 里写 `if not user.get('isSystemAdmin'): raise`。理由与 §4.7 的闸门相同：**单点实现才能单点审计**。

### 5.8 三处"不可顺手优化"的代码再确认（v2.0 新增）

v1.x 用 K-1/K-2/K-5 表达过这三条，但分散在表格里。v2.0 把它们提到显式小节，因为**它们是最容易被"优化"掉的三处，且每一处都不是笔误**：

| # | 代码事实 | 常见误判 | 为什么不能改 |
|---|---|---|---|
| **①** | `work_items` 的 `update.using` 与 `update.check` 都是 `_personal_or_member`，**不含 `owner_id == uid`**（`policy.py:270-279`） | "别人建的工单我能改，这是越权漏洞" | 云版 RLS 的 `USING`/`WITH CHECK` 本来就是同一条表达式。加上 owner 限制会**当场打断团队看板流转**：任何一个成员都无法推进别人建的任务卡（`README.md:88`）。要改必须先开 `CR-`，并同步 `07` 的看板用例 |
| **②** | `contexts` 的 `update.check` 是 `_own_writable`，**含 `owner_id == uid`**（`policy.py:289-297`）；而 `work_items` 没有 | "这两张表写法不一致，统一一下" | 这是**云版本的原始形状**，差异本身是要被记录的事实（`README.md:89`）。"统一成 work_items 那样"会让非创建者能改团队情境；"统一成 contexts 那样"会打断看板。**两边都不许动** |
| **③** | `load_context` 对 `team_members.status != 'active'` 的行**直接跳过** → 视为非成员（`policy.py:351-366`） | "云版只判行是否存在，这里判 status 是多写的分支，删掉更贴近 RLS" | 这是**有意的收紧**：与"停用即失去访问权限"的产品语义一致，前端 `saveMember` 的确认弹窗就是这么向用户承诺的（`README.md:90`）。放宽回去等于让被停用的人继续读全部团队数据。`test_20` 钉住了这一点 |

> 这三条的共同点：**它们看起来像"两处不一致/一条多余分支"，实际每一条都对应一个明确的业务承诺**。把它们"写整齐"的代价，大于任何可读性收益。任何改动都必须走 `CR-` + `08` 安全复核 + `07` 回归。

---

## 6. 环境与配置

> 严格模式：本节对应 `HARD_GATES.md`「生产部署、配置、密钥、安全边界」。

### 6.1 三种环境差异

| 环境 | 启动方式 | 监听 | 数据目录 | Caddy | `GTD_TRUST_PROXY` | 零停机 | 证据 |
|---|---|---|---|---|---|---|---|
| **A. 本地直跑** | `python server.py` | 默认 `127.0.0.1:8787`；`--host/--port` 可改 | `./data`（或 `--data-dir`/`GTD_DATA_DIR`） | 无 | 默认 `0` | 无 | `README.md:15,38,61-65`；`config.py:85-88` |
| **B. WorkBuddy sites / PaaS** | `python3 server.py`（平台注入 `PORT`） | 检测到 `PORT` 自动绑 `0.0.0.0` | 平台目录；**发布前须把 `data/` 移出项目目录**，本地库含口令散列不该进包 | 平台反代 | 【推断】应为 `0`，需确认 | 无 | `config.py:85-88`；`README.md:63-64` |
| **C. Docker 双槽位蓝绿** | `deploy/install.sh` / `release.sh` | 槽内 `0.0.0.0:8787`；宿主端口只绑 `127.0.0.1:8788/8789` | `gtd-data` 卷 `/data` | 有（唯一公网入口，常驻） | `1`（compose 默认） | 有 | `README.md:108-152`；`docker-compose.yml` |

- 环境 B 的 `GTD_TRUST_PROXY` 取值在本机**无法验证**（沙箱反代行为不可见），标【推断】，列入 §14 O-4。
- 镜像构建不联网装包，仅白名单 COPY（`Dockerfile:23-30`）；`read_only:true` + `cap_drop:ALL` + `no-new-privileges` + 非 root uid 10001（`docker-compose.yml:89-96`、`Dockerfile:49-57`）。
- **v2.0 追加**：环境 C 多挂了两个目录 —— `./deploy/run/certs:/certs`（app 可写，`docker-compose.yml:109`）与 `:ro`（caddy 只读，`:179`）。`data/logs/` 随 `gtd-data` 卷一起持久化，**不需要**额外挂载（这是 ADR-15 把日志放数据目录的原因之一）。

### 6.2 变量表（按用途分组，含默认值与来源）

| 变量 | 默认 | 作用 | 代码落点 |
|---|---|---|---|
| `PORT` / `GTD_PORT` | `8787` | 监听端口；检测到 `PORT` 时默认绑 `0.0.0.0` | `config.py:85-86` |
| `HOST` / `GTD_HOST` | `127.0.0.1`（有 `PORT` 时 `0.0.0.0`） | 监听地址；另有 CLI `--host` | `config.py:87-88` |
| `GTD_DATA_DIR` | `<root>/data` | 数据目录，决定 `db_path` / `log_dir` / `log_file` | `config.py:74-76,161-162` |
| `GTD_TRUST_PROXY` | `False`（compose 里显式给 1） | 是否采信 XFF/XFP | `config.py:107` |
| `GTD_BUILD` | `dev` | 构建标识，health 回传 | `config.py:71` |
| `GTD_LLM_BASE_URL` | `https://api.openai.com/v1` | 上游端点 | `config.py:131-132` |
| `GTD_LLM_API_KEY` | 空 | 上游密钥（也可存库 `settings`） | `config.py:133` |
| `GTD_LLM_MODEL` | `gpt-4o-mini` | 模型 | `config.py:134` |
| `GTD_LLM_TIMEOUT` | `120` | 上游超时（秒） | `config.py:135` |
| `GTD_LLM_ALLOWED_HOSTS` | 空 | 非空时 baseUrl 的 host 只能从白名单挑（收紧口） | `config.py:127` |
| `GTD_ADMIN_EMAILS` | 空 | 实例管理员环境变量白名单（**弃用期兜底**，见 §5.7） | `config.py:121-123` |
| **`GTD_CERT_DIR`** | **`/certs`** | **证书目录（容器内挂载点）；测试用临时目录** | **`config.py:149`** |
| `GTD_MAX_BODY_BYTES` | `8*1024*1024` | 请求体上限（OCR data URL 场景） | `config.py:152` |
| `GTD_HTTP_PORT`/`GTD_HTTPS_PORT` | `80`/`443` | Caddy 宿主端口（**不是**容器内端口） | `docker-compose.yml:163-164`；`env.example:17` |
| `GTD_BLUE_PORT`/`GTD_GREEN_PORT` | `8788`/`8789` | 槽位回环端口 | `env.example:27-28` |
| `GTD_DOMAIN` | 空 | 有值则 Caddy 自动 HTTPS + HSTS（ACME） | `env.example:18-20`；`lib.sh:215-217` |
| `GTD_PROBE_PATH` | `/api/health` | 连续性探测路径 | `env.example:58` |
| `GTD_WATCHDOG_FAILOVER` | `0` | 看门狗自动降级开关（默认关） | `env.example:60-65`；`watchdog.sh:32` |
| `GTD_IMAGE` | `gtd4teams:dev` | 槽位镜像 tag（发布脚本注入） | `docker-compose.yml:42` |
| ~~`GTD_ALLOW_SIGNUP`~~ | — | **已无任何行为**（CR-2026-001 永久下线自助注册）。仅被 `config.ignored_signup_env` 检测并在 `--check`/启动横幅打一行提醒 | `config.py:90-100,209` |
| ~~`GTD_SIGNUP_CODE`~~ | — | 同上 | 同上 |

> **v2.0 修正一处硬漂移**：v1.3 的变量表把 `GTD_ALLOW_SIGNUP`（默认 `True`）与 `GTD_SIGNUP_CODE` 当作有效开关。当前代码里它们**不参与任何判定**，只用于"提醒运维清理老 `.env`"（`config.py:94-100`）。把已死开关继续写成活开关，会让运维以为"注册还开着只是没人用" —— 那是最难发现的一类配置失效（`config.py:95-97` 的注释就是为此写的）。

### 6.3 配置优先级

- `pyapp/config.py:3` 头注释写的是四层：**命令行参数 > 环境变量 > 数据目录里的 settings 表 > 内置默认值**。
- 精确化（避免编造）：**通用配置（host/port/data_dir/trust_proxy/超时/体上限/cert_dir）只有三层「CLI > 环境变量 > 默认」**；`settings` 表覆盖**仅作用于 LLM 三项**，由 `Application.connect()` 在每个请求前调用 `apply_llm_settings(stored)` 实现，"只覆盖非空值，环境变量仍有兜底作用"（`config.py:165-175`、`httpd.py:132-144`）。
- **v2.0 追加**：`log_dir`/`log_file` **不进 settings 表、也没有 CLI 参数**，只由 `data_dir` 派生 —— 日志位置必须与数据目录同生命周期，否则"备份了数据却没备份日志"或"恢复了数据但日志指向上一个实例"都会发生。
- `cert_dir` 有优先级但**刻意不做 `mkdir`**：目录不存在时 POST 返回 500 并说明是部署问题（`tls_cert._require_dir`，`:337-347`）。理由见 `config.py:143-148` 的注释。

### 6.4 绑定地址与 `GTD_TRUST_PROXY` 的配对表（含一处历史文档漂移）

> **必须先如实说明一个漂移**：`docs/Docker部署方案.md` 与 `.workbuddy/memory/MEMORY.md` 把绑定地址变量写作 **`GTD_BIND`**，而**当前代码里不存在 `GTD_BIND` 这个环境变量**（全仓检索：仅出现在上述文档/memory；代码实际读取的是 `--host` / `HOST` / `GTD_HOST`，`config.py:87-88`）。因此下表用**语义配对**表述，"`GTD_BIND`"列标注为"历史文档名"。Docker 形态下"绑定回环"实际由 compose 的 `ports: "127.0.0.1:${GTD_BLUE_PORT}:8787"` 实现，而非应用读取某个 bind 变量。见 §14 O-1。

| 部署形态 | 绑定地址（历史文档名 `GTD_BIND` / **代码实际手段**） | `GTD_TRUST_PROXY` | Caddy XFF | 后果（搞反会怎样） | 依据 |
|---|---|---|---|---|---|
| 公网 IP 直连（A/B 无反代信任） | `0.0.0.0`（`--host 0.0.0.0` 或 `HOST`/`GTD_HOST`；或 `PORT` 触发） | **0** | 不适用 | 误置 1 → 伪造 XFF 换掉限流键，爆破不限速；Cookie 被加上 Secure 导致明文 HTTP 掉线 | `config.py:102-107`；`README.md:102-104,142-145` |
| 反代之后（C 蓝绿） | 槽内 `0.0.0.0:8787` + **宿主端口只绑 `127.0.0.1`** | **1** | **覆盖** `X-Forwarded-For {http.request.remote.host}` | 误置 0 → 所有请求算成 Caddy 容器 IP，限流误伤且 Cookie 缺 Secure；Caddy 若改成**追加**而非覆盖 → 伪造值排在真实 IP 前 | `docker-compose.yml:124,131`；`Caddyfile.template:50-56` |
| 反代之后但槽位端口改绑 `0.0.0.0` | `0.0.0.0`（对外） | **必须改回 0** | — | 客户端可绕过 Caddy 直连槽位并伪造 XFF | `README.md:145`；总纲 §4 G5 |

- 总纲 §4 **G5** 明确要求「`GTD_TRUST_PROXY` 与槽位绑定配对正确」——本表即该门禁的判定依据。
- `install.sh` 有守卫：`GTD_TRUST_PROXY` 被改成非 1 时告警「所有请求会被算成 Caddy 容器的 IP」【代码·已核：`deploy/install.sh`（内容锚点：`GTD_TRUST_PROXY` 检查）】。
- **v2.0 追加一行（新增风险面）**：§16 的集成域监听器**只绑 `127.0.0.1`，且没有配置项能改**。因此它**不受**本表的配对规则约束，也**不得**被纳入"绑定形态"讨论 —— 它的安全前提是"不可达"，不是"配对正确"。

### 6.5 密钥管理

| 密钥 | 存放 | 保护 | 风险/纪律 |
|---|---|---|---|
| 用户口令 | `users.password_hash`，PBKDF2 独立盐 | 不明文；`verify_password` 用 `hmac.compare_digest` | `security.py:50-71` |
| 会话令牌 | 仅 Cookie；库里只存 SHA-256 | 库被拷走也无法冒用 | `security.py:133-135` |
| LLM `apiKey` | 环境变量 或 库 `settings` 表 | `GET /api/llm/config` 只回 `apiKeySet` 布尔，连掩码都不给；`llm._scrub` 抹掉上游回显中的密钥 | `httpd.py:1350-1359`；`llm.py` 的 `_scrub`；`test_39`【测试·有名】 |
| **TLS 私钥** | **`<cert_dir>/tls.key`（mode 0600）；不经 settings、不进日志、不出现在任何响应** | **临时文件建在同目录（`.tmp-`）、`os.replace` 原子替换、用完即删；`ssl.SSLContext.load_cert_chain` 只做配对校验** | **`tls_cert.py:10-18,48-49,357-377,509-540`；`CertAdminTest.test_cert_upload_does_not_leak_key`** |
| **访问日志** | **`data/logs/app.jsonl`** | **落盘前必须脱敏：query 值全换 `***`、控制字符压平、超长截断** | **`httpd.py:260-294,351`（唯一调用点；不提供绕过口）** |
| 备份文件 | `--export` 产物 | **含口令散列、LLM 密钥、`is_system_admin` 标记**，明确"不要放进版本库或公开目录" | `store.export_all`（`store.py:810-827`） |
| `.env` 权限 | 项目根 | `.env` 被 `.gitignore` + `.dockerignore` 排除，不进镜像与版本库 | `env.example:9-10`；`README.md:147-148` |

> 前端日志脱敏同样属于密钥纪律：`SENSITIVE_KEYS` 精确匹配（`password|token|apikey|signupcode|...`），`errCode` 等排障字段不被误伤，长字符串截断 500（`js/api.js` 的 `flushLogs`/`log` 一带）；`.smoke/auth-flow-test.mjs` 有断言【测试·有名】。
> **v2.0 追加**：`tls_cert.py` 自己**没有任何 `print`**，且**绝不把异常原文拼进对外文案**（`CertError` 的 `message` 是可展示文案，`tls_cert.py:69-80`）。这是"私钥模块不许有日志"的落地方式 —— 不靠 review，靠"这个文件里根本没有输出语句"。

### 6.6 v2.0 新增配置项（本轮实现的横切配置）

| 项 | 值来源 | 默认 | 变更后是否需要重启 | 说明 |
|---|---|---|---|---|
| `config.log_dir` | `data_dir / 'logs'` | 随数据目录 | 否 | **目录不在 `Config.__init__` 创建**（构造器可能在只读环境/CLI 子命令里跑）；创建与降级都在 `applog.configure()` |
| `config.log_file` | `log_dir / 'app.jsonl'` | 同上 | 否 | `--check` 的 `logFile` 字段回显它（`config.describe`，`:207`）——这是"日志到底写去哪了"的第一现场 |
| `config.cert_dir` | `GTD_CERT_DIR` / 构造参数 | `/certs` | 是（改挂载即重启） | 缺失时按**部署问题** 500，不 `mkdir` |
| `applog.MAX_BYTES` | 常量 | `50MB` | 是（改常量即改代码） | 单文件上限 |
| `applog.BACKUP_COUNT` | 常量 | `5` | 是 | 保留份数 → 磁盘上限 ≈ 300MB |
| `applog.MAX_LIMIT` / `DEFAULT_LIMIT` | 常量 | `1000` / `100` | 是 | `GET /api/logs` 的 `limit` 硬上限，挡住"一次读 300MB" |
| `Handler.SESSION_TOUCH_SECONDS` | 常量 | `300` | 是 | 会话活跃刷新节流（旧有，本版重述） |
| `Handler.WRITE_RETRY_ATTEMPTS` | 常量 | `6` | 是 | 写锁重试次数（旧有，本版重述） |

**为什么这些是常量而不是环境变量**：它们都是**安全/容量的下限或上限**（日志轮转上限、单次读取上限、重试次数）。做成环境变量意味着"部署方可以把它调大到危险值"，而调大之后的故障（磁盘写满、OOM、请求拖成超时）不会指向那个变量。**能改的东西越多，能解释的故障越少。**

### 6.7 TLS 与证书生命周期（v2.0 新增）

> 严格模式：证书属"密钥"类，命中 `HARD_GATES.md`。本节只写**代码事实 + 设计取舍**；运维操作 SOP 交 `09`。

#### 6.7.1 为什么需要它，以及它刻意不做什么

| 做 | 不做（**写死，不要"顺手补上"**） |
|---|---|
| 上传/替换 `tls.crt` + `tls.key`（一次性替换成对文件） | ❌ **不签发**证书（不内置 CA，不生成 CSR） |
| 解析并显示 subject/issuer/SAN/有效期/剩余天数/SHA-256 指纹 | ❌ **不返回私钥**（连掩码都不给），**不返回证书全文** |
| 四段校验：PEM 格式 → 私钥是否加密 → 是否配对 → 是否已过期 | ❌ **不做链完整性校验**（不验证 CA 根、不验 OCSP/CRL）—— 那需要第三方库或完整 X.509 路径验证，零依赖下做不到 |
| 原子落盘 + 元数据 + `.pending-apply` 标记 | ❌ **不重载 Caddy**、**不删除标记**（那是宿主的职责） |
| 删除证书（幂等） | ❌ 不 `mkdir` 证书目录（掩盖部署错误是最坏结果） |

#### 6.7.2 三个端点（接口契约见 `06`，此处只定架构约束）

| 方法/路径 | handler | 权限 | 事务/落盘顺序 |
|---|---|---|---|
| `GET /api/admin/cert` | `h_admin_cert_get`（`httpd.py:1093-1102`） | `_require_instance_admin` | 只读：现场解析文件 + 只在指纹一致时采信元数据 |
| `POST /api/admin/cert` | `h_admin_cert_upload`（`:1105-1126`） | 同上 | 校验全部在**写盘之前** → 两个临时文件都写好 → 两次 `os.replace` → 写元数据 → 打标记 |
| `DELETE /api/admin/cert` | `h_admin_cert_delete`（`:1129-1136`） | 同上 | 删文件（不存在不报错）→ 删元数据 → 打标记 |

三条共性约束：

1. **全部响应带 `Cache-Control: no-store`**（`httpd.py:1126,1136`）：回的是实例的安全配置详情，没有理由进任何中间层缓存。
2. **全部不回显私钥**；`POST` 的响应就是 `status()` 的结果（`save` 最后一行 `return status(...)`）。
3. **全部用 JSON 传 PEM 文本，不用 multipart**（`httpd.py:1106-1110`）：证书与私钥都是几 KB 文本；手写 multipart 边界解析是 bug 温床，且本项目没有任何现成解析器。

#### 6.7.3 落盘纪律（原子性 + 权限）

```
<cert_dir>/
├── tls.crt                 # 0644 证书（公开信息，Caddy 要能读）
├── tls.key                 # 0600 私钥（只能属主可读）
├── .pending-apply          # 空文件；"存在性"就是信号（幂等、写不坏）
├── .tmp-XXXXXX             # 临时文件（同目录，用完即删/被 replace 消费）
├── tls-<fp16>.crt / .key   # ★ lib.sh 生成的"按指纹版本化"副本（Caddy 实际引用这两个）
└── .selfsigned             # ★ gen-selfsigned.sh 留下的"这是脚本自签的"标记（后端不读它）
```

| 纪律 | 做法 | 证据 |
|---|---|---|
| 原子替换 | 临时文件建在**同目录**（跨设备的 `os.replace` 会退化成"复制+删除"，就又回到"能被读到半截"） | `tls_cert._stage_file`（`:357-377`） |
| 半对证书的窗口 | **两个临时文件都写好之后才开始替换**，把窗口压到两次 `rename` 之间（微秒级） | `tls_cert.save`（`:527-536`） |
| 元数据顺序 | **文件写完再写元数据**：元数据失败时文件仍可用（GET 会归为 `selfsigned`）；反过来会让页面显示"已配置"但 Caddy 读不到 | `save` 的 ④ |
| 标记最后 | `.pending-apply` 是最后一步 → 外部 watchdog 永远读不到半对证书 | `save` 的 ⑤ |
| 从不删标记 | 删了就等于声称"已生效"，而我们无法知道 Caddy 有没有真的加载 | `_touch_pending`（`:380-394`） |
| 权限 | `chmod` 失败**不阻断**（Windows 上 chmod 语义有限） | `_stage_file`（`:370-373`） |

> **`.selfsigned` 标记的一处不对称（如实记录）**：`gen-selfsigned.sh` 会在产物旁留 `.selfsigned`（用于"不覆盖别人上传的真实证书"），而 `tls_cert.status()` **完全不读它** —— 它只按"元数据指纹是否等于文件指纹"判断 `source: uploaded | selfsigned`（`:474-493`）。这不是缺陷，是两条独立判据：脚本用它保护文件不被误覆盖，后端用它判断"这张证书是不是经 API 传的"。**不要在 `status()` 里加 `.selfsigned` 判断**，那会让"脚本自签后又经 API 上传同一张证书"这种情况显示成 `selfsigned`。

#### 6.7.4 文件是唯一事实来源（这条救过一次线上故障）

`status()` 的取值顺序**固定**为（`tls_cert.py:439-495`）：

1. 始终现场解析 `<cert_dir>/tls.crt`（不存在才 `configured: false`）；
2. `settings.tls_cert_meta` **只补** `uploadedAt` / `uploadedBy`；
3. **仅当元数据指纹 == 文件指纹**才采信它 —— 否则 `source` 报 `selfsigned`、上传人/时间置 `null`，并打一行漂移告警（每组合只打一次）。

**实测现场（为什么必须这样）**：API 上传证书 A（30 天）→ 运维 `gen-selfsigned.sh --force` 换成证书 B（825 天，不经 API、只改文件）→ `settings` 仍记着 A → 于是 GET 报"A，30 天后到期"，而 HTTPS 实际提供的是 3 年后才过期的 B。**用户被提示去续期一张已经不在使用的证书。假信息比没有信息更危险。**

两条衍生纪律：

- **不写"修数据"的脚本**去清 `settings.tls_cert_meta`：脚本在宿主上跑、没有数据库访问能力（数据在容器内 `/data`），且"文件被谁改"**无法枚举**（今天想到 `gen-selfsigned.sh`，明天有人 `cp` 一张进去）。只有"以文件为准 + 指纹比对"能覆盖所有路径。
- **解析失败必须降级**：连 DER 都转不出来时，返回"有证书但读不出"（字段全 `null` + 指纹尽力算），**不让页面打不开**（`:466-471`）。

#### 6.7.5 从"上传成功"到"真的生效"：三段式 + 两道断言

```
① 后端（容器内，uid 10001）          ② 宿主 watchdog（cron 每分钟）           ③ Caddy
   POST /api/admin/cert                见 .pending-apply
   ├ 四段校验                           ├ caddyfile_generate（复制成 tls-<fp>.crt）
   ├ os.replace tls.crt / tls.key       ├ caddy_reload（validate → reload）
   ├ 写 settings.tls_cert_meta          ├ 断言 ① 运行时上游列表 == 期望
   └ touch .pending-apply  ──────────▶  ├ 断言 ② 运行时证书指纹 == 文件指纹
                                        └ 成功才 rm .pending-apply / 清重试计数
```

| 断言 | 校验什么 | 为什么必须有 |
|---|---|---|
| `caddy_assert_upstreams`（`lib.sh:430-460`） | 运行时上游列表（**顺序敏感**）== 本次生成的期望 | `caddy reload` 对无变化配置也返回 0；inode 事故期间每次"重载成功"而配置一个字节没变（ADR-18） |
| 证书指纹比对（`lib.sh:491-503`、`watchdog.sh:118-145`） | `openssl s_client` 从宿主回环抓到的证书指纹 == `tls.crt` 指纹 | "换了证书文件但配置文本没变 → Caddy 跳过加载 → 仍是旧证书"会被当成成功（实测：日志只有 `config is unchanged`，对外 CN 还是旧值） |

**失败语义（三条，都是踩过的坑换来的，`watchdog.sh:62-96`）**：

1. **成功才删标记** —— 失败就删会让用户以为生效了，而实际线上还是旧证书；
2. **失败必须留痕** —— 写进 `deploy/run/cert-apply.log`，否则"没生效"是完全无声的；
3. **不能影响看门狗本职** —— 生成/重载放在**子 shell** 里跑，`caddy_reload` 的 `die` 不能把本轮的巡检一起带走。连续失败 3 轮起日志明确写"已连续失败、需人工介入"（`cert_record_retry`，`:82-96`）。

**前端的诚实反馈**：`pendingApply: true` 时页面显示"待应用"而非"已生效"（`views.js:2385-2390`）。**没有"应用成功"的即时承诺**，因为后端根本不知道 Caddy 加载了没有。

#### 6.7.6 证书相关的运维约束（交 `09` 展开）

| 约束 | 原因 |
|---|---|
| 宿主 `deploy/run/certs` 必须属主为 uid 10001 且可写 | 容器以非 root 运行、根文件系统只读；不 chown 就会 500 `CERT_DIR_MISSING` |
| 手工替换 `tls.crt` 后**必须**同时 `touch` `.pending-apply` | 否则文件换了、线上仍是旧证书，而 mtime/md5 全对、脚本也不报错（`gen-selfsigned.sh` 头注释已写死） |
| 自签名证书**一定会**触发浏览器警告，本项目不处理 | 不导出 CA、不装信任库；它只用于验证 HTTPS 链路本身 |
| 证书必须带 SAN，不能只有 CN | 现代浏览器/curl 只认 `subjectAltName`，少 SAN 不是"警告"而是直接拒绝 |
| 私钥必须未加密 | `_is_encrypted_key` 会把 PKCS#8 加密与传统 OpenSSL 加密都识别出来，给 `CERT_KEY_ENCRYPTED` 而不是误报"不配对" |

---

## 7. 开发工程规范

### 7.1 目录结构（存量项目，不套空壳）

> 硬规则：真实业务代码只写入业务项目目录。本项目**已是存量布局**，不套 `project/backend/` + `project/frontend/` 空壳。`README.md:196-234` 是目录权威清单，本文 §2.2 是职责视图。文档统一落 `docs/process/`，SQL/迁移落 `05` 与 `sql/`（由数据建模师维护）。

```
GTD4Teams/
├── server.py                # 启动入口 + CLI
├── pyapp/                   # 后端包（10 个模块：config/db/security/policy/store/llm/httpd + applog/tls_cert/__init__）
├── js/ css/ index.html      # 前端（原生 ESM，无构建链；js 含 util.js）
├── data/                    # 运行时生成：gtd4teams.db + logs/app.jsonl（.gitignore）
├── tests/                   # test_api.py(156) / test_zerodowntime.py(9)
├── .smoke/                  # 前端冒烟 render/auth-flow/app-boot
├── docs/process/            # 00~10 + templates/   ← 本次治理文档
├── docs/                    # 专题技术文档（含历史漂移，见 §6.4）
├── deploy/                  # 部署脚本 + Caddyfile.template + env.example + gen-selfsigned.sh
│   └── run/                 # 运行时：state.env / Caddyfile / certs/ / *.lock / cert-apply.log
├── Dockerfile / docker-compose.yml / .dockerignore / .gitignore
└── .workbuddy/memory/       # 项目笔记（不作为配置权威）
```

### 7.2 Git 分支与提交规范

对应 `code_quality.git_branch_commit`。**以下为面向"1 人 + AI 协作"的轻量约定，标注哪些是现状、哪些是建议**：

- 现状【代码·已核】：`deploy/lib.sh` 的 `new_tag()` 用 `git rev-parse --short HEAD` 生成镜像 tag `<时间戳>-<短提交>`；`rollback.sh` 依赖"标签字典序 = 时间序"逆序取上一版。
- 分支约定（**O-6 已裁决搁置，2026-11-18**：项目当前非 git 仓库，本段仅为将来纳入版本控制时的条件性参考，**不是本期待办**）：届时 `main` 为可发布主干；改动在 `cr/<CR-编号>-<简述>` 短分支进行；禁止直推 `main`；每次发布对应一个 `GTD_BUILD` / 一个 tag。
- commit 模板（建议）：`[<类型>] <CR-编号|REQ-编号> <一句话>`，类型取 `feat|fix|sec|docs|ops|refactor|test`；正文注明"影响的文档 / 代码模块 / 测试 / 是否涉及数据库 / 是否涉及安全边界 / 回归范围"。
- 合并流程（建议）：G2 自审（含版本串同步证据）→ G3 联调单 → G4 测试报告 → G5 安全放行 → G6 上线；任一门禁未过不合并到可发布主干。
- 回滚策略：`deploy/rollback.sh`（零停机，状态互换，可"回滚掉这次回滚"）；数据库按 expand/contract；**v2.0 追加**：迁移层是**向前 only**（`_MIGRATIONS` 无 down），回滚 = 恢复备份（§12.3）。

### 7.3 代码审查清单（提交前自检，G2 入参）

- [ ] 是否引入任何第三方 import / CDN / 构建步骤（违反 C-1/C-2）
- [ ] 新增字段是否同步 DDL + `db.COLUMNS` + `EXPECTED_TABLES` + 策略表（违反 ADR-05，`test_columns_spec_matches_real_schema` 会挂）
- [ ] **新增表是否登记 `db.EXPECTED_TABLES`**（v2.0 加粗：`test_expected_tables_are_all_created` 会因"多出来的表"直接失败）
- [ ] **新增列是否同时加一条 `_MIGRATIONS`**（v2.0 加粗：不做迁移，生产库不会长出新列）
- [ ] 新增 `/api/*` 是否同步 `ROUTES` + `js/api.js` + `06` + 测试
- [ ] 新增视图/交互是否完成三处登记（§7.5）
- [ ] 版本串是否同步（§7.4）
- [ ] `owner_id` 是否仍由服务端注入、客户端值是否仍被丢弃（K-4）
- [ ] 权限被拦是否仍返回空数组而非抛错（K-3）
- [ ] 是否触碰 K-1/K-2/K-5/K-6 的「刻意保留」语义（触碰须开 `CR-`）
- [ ] 是否有敏感值进入日志/响应（口令、令牌、LLM key、**TLS 私钥**）
- [ ] **新增的早退响应是否先调用了 `_discard_body()`**（v2.0 加粗，见 §8.5）
- [ ] **新增的实例级端点是否用 `_require_instance_admin` 而不是自己判 `isSystemAdmin`**（v2.0 加粗，见 §5.7）
- [ ] 写操作是否仍走 `_check_origin` + `Content-Type` + 参数绑定
- [ ] LLM 上游错误是否经 `_scrub` 脱敏

### 7.4 版本串同步纪律（v2.0 重测）

总纲 §4 G2 与 `README.md:264-265` 把它定为硬门禁。**实测落点（本次 grep 全仓结果，值均为 `20261114f`）**：

| 处 | 位置 | 当前值 |
|---|---|---|
| ① `APP_VERSION` 常量 | `js/config.js:185` | `20261114f` |
| ② 各模块 import 的 `?v=` | `js/api.js:22`；`js/app.js:16,17,25,30,34`；`js/auth-ui.js:26,27,28,29`；`js/views.js:17,22`（共 **12 处**） | `?v=20261114f` |
| ③ `index.html` 的 css/js 引用 | `index.html:9`（css）、`index.html:64`（app.js） | `?v=20261114f` |
| ④ 服务端版本串 | `pyapp/__init__.py:20` → 被 `config.py:14,65`、`httpd.py` 的 `h_health`、`server.py` 消费 | `20261114f` |
| （另）`.smoke/config.mjs` | 是 `js/config.js` 的测试镜像副本 | 需同步（O-2） |

> **v2.0 修正**：v1.3 表格写的是 `20260917a`、`js/views.js:16,21`、`js/config.js:170` 等行号，**全部已漂移**。这正是"文档记死行号"的固有代价 —— 所以本表的行号只作为"当前定位提示"，判据仍是**字面值相等**。
> 建议做成自动化断言但**不引入构建链**：在现有纯标准库测试里加一条用例，读取四处的字面值并断言相等、且 `?v=` 数量与预期一致。**状态：建议，未实现【推断】**。（该行是 v2.0 当时状态；见下方 v2.2 更新，现已实现。）
> 静态资源不做长期缓存：`.html` 为 `no-store`、其余 `no-cache`，正是为了让 `?v=` 成为唯一失效手段（`httpd.py:886-891`）。
>
> **v2.2 更新（2026-11-18，随 §14 O-2 裁决回写）**：上表的 `20261114f` 是 v2.0 当时取值，**现值已递增为 `20261118a`**（P0 修复 REQ-039 时同步递增 8 个落点）【代码·已核：`js/config.js:185`、`pyapp/__init__.py:20`、`index.html:9,64`、`.smoke/config.mjs:185` 逐文件实读均为 `20261118a`】。自动化断言**已实现**：`.smoke/version-contract.mjs` 实跑 **18 文件 / 13 断言**全绿；第 5 类落点 `.smoke/config.mjs` **已接入扫描**（`scanTargets` = js/ + `index.html` + pyapp/ + `.smoke/config.mjs`，新增 `[6]` 镜像副本断言），O-2 裁决已落地。破坏性验证：故意把镜像值改成 `20261118b` → 2 项红、退出码 1，还原后 13 项全绿。`version-contract` 只保证"各处同值"，**不保证"值已递增"** —— 递增需发布纪律另行保证。

### 7.5 新增视图 / 交互的三处登记纪律

| # | 登记点 | 说明 | 证据 |
|---|---|---|---|
| 1 | `js/app.js` 的 `dispatch` 事件委托 | 新交互必须有对应分支；`app-boot-test.mjs` 是唯一能发现"视图未分发"的测试 | 总纲 §4 G3；`app.js`（内容锚点：`dispatch` 的 `case` 表） |
| 2 | `js/views.js` 的 `render()` / 视图函数 | 纯渲染，只产 HTML 字符串；新增视图需挂进渲染分发 | `README.md:212,264`；`render-test.mjs` |
| 3 | `index.html` 的挂载点 / 引用 | 改 `index.html` 或 `render()`/`dispatch` 后**必须重跑** `app-boot-test.mjs`（真实 DOM） | `README.md:260-265` |

> **v2.0 实例**：证书管理的三处登记都已做完 —— `views.js:2360-2470`（视图）、`app.js` 的 `load-cert`/`upload-cert`/`delete-cert`/`cert-file-picked` 四个分支（`:1094-1104`）、`api.js` 的 `getCert`/`uploadCert`/`deleteCert`（`:395-411`）。这是"新增交互三处登记"的完整样例。

### 7.6 v2.0 新增：三类横切改动的登记纪律

本轮新增的三个能力，各自都有一份"漏一处就失效"的清单。写在这里，是为了下次改动时**不用重新推导**：

| 改动类型 | 必须同时改的地方 | 漏掉的后果 |
|---|---|---|
| **加一张表**（如 §16 的 `api_tokens`） | ① DDL（`db.py` 的 `DATA_SCHEMA` 或迁移函数）② `_MIGRATIONS` 加一条 ③ **`EXPECTED_TABLES`** ④ `store.BACKUP_TABLES`（若要备份）⑤ 若要被通用 CRUD 写：`COLUMNS` + `POLICIES` + `TEAM_KEY` + `load_workspace` | ③ 漏 → `schema_present()` 用子集判断仍返回 True → **旧库永远不会建这张表**，而 `test_expected_tables_are_all_created` 会在测试里报"多出来的表"（生产上则是 `no such table`） |
| **加一列** | ① DDL ② `COLUMNS` ③ `_MIGRATIONS`（**生产库靠它长出新列**）④ `05` 迁移 SQL + 回滚 SQL ⑤ 若列含敏感语义，确认 `split_payload` 丢弃客户端值 | ③ 漏 → 代码里 `SELECT` 它就直接 `no such column`，而且**只在升级后的库上炸**，本机新库完全正常 |
| **加一个 `/api/*` 端点** | ① `ROUTES` ② handler 首行 `_require_user` **或** `_require_instance_admin` ③ `js/api.js` 导出（若前端要用）④ `06` 契约 + 错误码 ⑤ `tests/test_api.py` 用例 ⑥ 若写数据：过 `can_*` ⑦ 若早退：`_discard_body()` | ② 漏 → 端点裸奔（比"权限写错"更常见，因为它是"忘了"不是"写错"）；⑦ 漏 → keep-alive 失步（§8.5） |

---

## 8. 异常处理与错误码规范

> 严格模式：对应 `architecture.error_handling_standard`。完整错误码常量表、HTTP 映射、前端文案映射在 **`06_接口契约与错误码.md` §2**，此处只定规范。

### 8.1 错误分类

| 类 | 抛出方 | HTTP 形态 | 证据 |
|---|---|---|---|
| 传输/协议错误 | `httpd.py` 的 `ApiError` | 400/401/403/404/405/409/413/415/429/500 | `httpd.py:95-100,714-737` |
| 业务错误 | `store.StoreError(code, message, status)` | 默认 400，个别 403/404/409/500 | `store.py:23-31` 及各处 `raise` |
| **证书错误（v2.0）** | `tls_cert.CertError(code, message, status)` | 400（格式/加密/不配对/过期）/ 500（目录缺失） | `tls_cert.py:69-80`；`httpd.py:1124-1125,1134-1135` |
| 上游 AI 错误 | `llm.LlmError(code, message, status)` | 400/502/504 | `llm.py:24-29,40-71` |
| 权限拒绝（整体性） | `policy.Denied` | 由 `_dispatch` 转 403 `PERMISSION_DENIED` | `httpd.py:762-766`；`policy.py:317-328` |
| 权限拒绝（行级） | 不抛异常 | **200 + `{rows: []}`** | `store.py:188-189,199-200` |
| 写锁争用 | `sqlite3.OperationalError` | 内部重试；耗尽则 500 | `httpd.py:168-172,751-784` |
| 兜底 | `except Exception` | 500 `INTERNAL_ERROR` + `traceback.print_exc()` 留证据 + **复用同一个 `request_id`** | `httpd.py:810-830` |
| **日志读取 I/O 错误（v2.0）** | `applog.LogReadError` | 500 `LOG_UNREADABLE`（**只有真 I/O 错误**；"日志不可用"走降级分支） | `applog.py:76-78,323-325`；`httpd.py:1316-1320` |

### 8.2 错误码命名与统一返回体

- 命名：全大写下划线（`UNAUTHENTICATED`、`PASSWORD_CHANGE_REQUIRED`、`LLM_NOT_CONFIGURED`…），机读 `code` + 可展示 `message` 分离。
- 错误体固定：`{"error": {"code": "...", "message": "中文可直接展示文案"}}`（`httpd.py:506`）。
- 成功体不统一包装：各端点返回自己的结构（`{user}`、`{rows}`、`{team}`、`{teamId}`、`{ok}`、`{models}`、`{logs,total,truncated,fileLogging,degradeReason}` 等），由 `06` 逐条固化。
- 前端 `request()` 把非 2xx 转成带 `code/status/kind` 的 `Error`；`kind` 含 `timeout`/`network`/`unauthenticated`；`friendlyError()` 用 `MAP` 把 `code` 映射为可读文案，未知码回落到服务端 `message`（`api.js` 的 `request`/`friendlyError`）。
- **v2.0 追加**：错误码必须同时写入访问日志的 `errcode` 字段（`_error()` 是**唯一**写 `self._errcode` 的地方，`httpd.py:501-505`）。这样"这个 500 是不是 `INTERNAL_ERROR`"变成一次结构化查询，而不是对 traceback 文本做正则赌博。

### 8.3 前端提示映射原则

1. 服务端已给可直接展示的中文文案，前端只做少量必要归一。
2. `kind` 优先于 `code`（超时/网络故障先判）。
3. 未知码不得显示 `[object Object]` 或空白——`readMessage` 只接受字符串来源。
4. `.smoke/auth-flow-test.mjs` 的表驱动断言锁定了 7 个码的文案片段（`UNAUTHENTICATED`/`INVALID_CREDENTIALS`/`EMAIL_EXISTS`/`INVALID_JOIN_CODE`/`PERMISSION_DENIED`/`RATE_LIMITED`/`UNKNOWN_TABLE`）与 2 个 kind【测试·有名】。**改文案必须同步改这张表**。

### 8.4 被权限拦下返回空数组的约定与前端如实提示义务

- 约定：见 K-3 / ADR-08；`h_rows` 文档字符串明确"不会假装成功"（`httpd.py:1145-1150`）。
- 义务：前端 `assertAffected()` 必须据"空数组 = 没改到"提示"可能不存在或不在你可见的范围内"，**不得**当作成功刷新界面、也不得谎称"服务器错误"。
- 边界：这与 403 `PERMISSION_DENIED` 并存且语义不同——`Denied` 是"整个操作在权限上不可能成立"（如往非成员团队 insert），空数组是"这一行你看不见/改不到"。

### 8.5 早退响应与请求体消费（v2.0 新增）

这是本轮修的**协议级缺陷**，不是风格问题。它同时是"数据错乱"和"请求走私"两个风险的共同根源，所以单独成节。

#### 8.5.1 现象（实测）

| 场景 | 观测结果 |
|---|---|
| 同一 keep-alive 连接：先发一个带 body 的 GET，再发正常 GET | 第二个请求变 `501 Unsupported method ('{}POST')`（`BaseHTTPRequestHandler` 把残留字节当成请求行） |
| `POST /api/rows`（`Origin: evil`）→ 403，紧接着正常登录 | 登录变 `501`；经反向代理的连接池会**跨连接传染** |
| `POST /api/<不存在的路径>` 带 body | 同上 |
| `POST /api/rows` 带坏 JSON → 400 | 若此时标记"未消费"，`_discard_body` 再读一次 → 读不到 → **卡 2 秒超时** → 关连接 |

**风险定性**：残留 body 会被解析成下一个请求的请求行，意味着"**1 个请求换回 2 份响应**" —— 这是标准的请求走私（request smuggling）原语。在反代之后，它还能污染**同一条上游连接上的其他用户请求**。

#### 8.5.2 机制

| 机制 | 实现 | 关键点 |
|---|---|---|
| 消费函数 | `_discard_body()`（`httpd.py:389-429`） | 幂等（已消费则立即返回）；`length<=0` 直接返回；`length > max_body_bytes` **不读进内存**、直接 `close_connection=True`；读操作带 2 秒 `settimeout` 兜底 |
| 消费标记 | `_body_consumed` | 由 `_read_body()` 在**读到字节之后、解析 JSON 之前**置位（`:378`）；`_discard_body()` 成功读完置位（`:422`）；**每请求在 `_handle` 开头重置**（`:679`） |
| 非写方法的 body | `if kind not in WRITE_METHODS: self._discard_body()`（`:689-690`） | GET/DELETE 也可能带 body（客户端不守规矩），一样要读掉 |
| 写方法的早退 | 5 条分支全部先 `_discard_body()` | 静态 405（`:695`）、路由 404（`:705`）、`ApiError/StoreError/LlmError`（`:731`）、其它异常（`:735`）、`DELETE` 无 Content-Type 分支（`:725`） |

#### 8.5.3 三条写死的纪律

1. **`_body_consumed` 的语义是"流已经前进过了"，不是"解析成功了"。** 把"已读"当"未读"会导致二次读取卡死（见 8.5.1 第 4 行）；把"未读"当"已读"会导致失步。两者都错，所以只有一种正确语义。
2. **任何新增的早退分支都必须先 `_discard_body()`。** 这是 §7.6 表格里的第 ⑦ 项。
3. **`_body_consumed` 必须每请求重置。** 连接是 keep-alive 复用的，上一个请求的状态串到下一个请求上，会让"本该读掉的 body"被跳过。

#### 8.5.4 现有覆盖与缺口

- 已钉住的用例【测试·有名】：`test_origin_403_discards_body_keepalive_stays_clean`（`tests/test_api.py:2486`）、`test_unsupported_media_415_discards_body`（`:2520`）、`test_unknown_api_post_discards_body`（`:2540`）。
- **缺口**：① 静态资源 405 分支（非 `/api/` + 非 GET）没有专项用例；② 坏 JSON 400 之后的连接复用没有专项用例；③ 超大 body（> 8MB）走 `close_connection=True` 的分支没有用例。三条列入 §13 U-13，交 QA。

### 8.6 v2.0 新增错误码（清单，完整表在 `06` §2）

| 错误码 | HTTP | 来源 | 何时出现 |
|---|---|---|---|
| `ADMIN_ONLY` | 403 | `_require_instance_admin` | 非实例管理员访问 AI 配置 / 证书 / `GET /api/logs` |
| `CERT_INVALID_PEM` | 400 | `tls_cert._cert_der` / `_check_key_pem` | 证书或私钥不是合法 PEM（含截断、粘贴错、正文非 base64） |
| `CERT_KEY_ENCRYPTED` | 400 | `tls_cert._is_encrypted_key` | 私钥带口令（PKCS#8 加密 或 `Proc-Type: 4,ENCRYPTED`） |
| `CERT_KEY_MISMATCH` | 400 | `tls_cert._load_chain` | 证书与私钥不是同一对（用 `ssl` 真校验，不是比字符串） |
| `CERT_EXPIRED` | 400 | `tls_cert.validate_pair` ④ | 证书已过期（文案带到期时间） |
| `CERT_DIR_MISSING` | 500 | `tls_cert._require_dir` / `_touch_pending` / `delete` | 证书目录不存在或不可写 —— **部署问题**，文案指向挂载与属主 |
| `INVALID_LIMIT` | 400 | `h_access_logs` | `limit` 非整数或 ≤ 0 |
| `INVALID_STATUS` | 400 | `h_access_logs` | `status` 非整数或不在 100~599 |
| `LOG_UNREADABLE` | 500 | `h_access_logs` | 日志文件**可打开但 I/O 出错**（EIO 等）—— 注意"日志不可用"**不走**这个码，走降级响应体 |
| `NO_SYSTEM_ADMIN` | 403 | `store.create_account` | 零系统管理员且调用者无建号权 —— 文案给引导命令，不是"你没权限" |
| `PASSWORD_CHANGE_REQUIRED` | 403 | `_require_user` 闸门 | 初始口令未改 |
| `WEAK_PASSWORD` / `INVALID_OLD_PASSWORD` | 400 / 401 | `h_change_password` | 改密失败两类 |

> **一个刻意的"不新增"**：`CERT_DIR_MISSING` 用 **500 而不是 400**。它不是输入问题，是部署问题；用 400 会让运维去检查"我上传的文件对不对"，而真正要检查的是"挂载有没有生效、属主对不对"。错误码的语义价值就在于**把排查方向指对**。

---

## 9. 日志与监控设计（v2.0 整章重写）

> 严格模式：对应 `architecture.logging_monitoring_plan`（日志分类 / 字段规范 / 监控指标 / 告警建议）。
>
> **本章为什么是重写而不是增补**：v1.x 的 §9.1 写的是"访问日志（异常）→ 进程 stdout，`>=400` 才打印 `! METHOD path -> status`，证据 `httpd.py:135-141`"。那个实现**已被删除**，`httpd.py` 里现在没有那行 `print`；`log_message` 被整体置空（`:257-258`）。继续沿用旧描述会让任何按文档排障的人**找不到日志**。

### 9.1 从 `print` 到 JSONL：动机与边界

| 维度 | 旧实现（v1.x 描述，现已删除） | 现实现（`applog.py` + `_log_access`） |
|---|---|---|
| 记录条件 | 仅 `status >= 400` | **每个请求一条**（健康检查除外） |
| 格式 | 纯文本 `! GET /path -> 500` | **一行一个 JSON 对象**（JSONL） |
| 落点 | `stdout` | **`stderr` + `data/logs/app.jsonl`**（双 handler） |
| 身份字段 | 无 | `uid`（认证成功才有，否则 JSON `null`） |
| 耗时 | 无 | `duration_ms`（`time.monotonic()` 差值，1 位小数） |
| 错误码 | 无 | `errcode`（由 `_error()` 唯一写入） |
| 可检索性 | 要写正则 | `jq` / `grep` / 按字段聚合 |
| 磁盘约束 | 无（由容器日志驱动管） | 50MB × 5 份 ≈ 300MB 封顶 + `GET /api/logs` 可读 |

**旧实现的三条具体后果**（不是"少了一点方便"，而是监控**没有数据源**）：

1. **成功的请求完全不留痕** → 算不出 QPS、成功率、P95 延迟；
2. **只有"出错就算一次"的计数，且没有身份字段** → 无法区分"一个人反复失败"（撞库）与"很多人各失败一次"（系统故障）；
3. **print 到 stdout 的纯文本机器不可解析** → 任何指标都要先写正则，而正则一改、语言一换就失效。

**边界（这一章不承诺什么）**：

- **不引入指标系统**：没有 Prometheus / statsd / tracing。指标是"从 JSONL 里聚合出来的"，不是服务端维护的。
- **不做日志聚合**：单机单文件，跨机看日志要靠外部采集（未实现，见 §11 P1）。
- **不做审计与日志的合并**：`app_logs`（数据库，业务/审计）与 `app.jsonl`（文件，访问）是**两条独立的流**，见 §9.7。

### 9.2 `applog.py` 的结构（397 行，唯一日志模块）

```
applog
├── 常量：ACCESS_LOGGER_NAME='gtd.access'、MAX_BYTES=50MB、BACKUP_COUNT=5、
│        MAX_LIMIT=1000、DEFAULT_LIMIT=100、READ_BLOCK=64KB、READ_UNAVAILABLE
├── _Formatter            # 只输出消息本身（JSONL 每行必须恰好是一个 JSON 对象）
├── class AccessLog       # 一个数据目录对应一份日志
│   ├── __init__(path, stream)   # 子 logger 名 = gtd.access.<path指纹8位>；propagate=False
│   ├── _install(stream)         # 清旧 handler → 装 stderr(INFO) → 装文件(INFO, 轮转)
│   ├── write(record)            # 吞掉一切异常
│   ├── heartbeat(message)       # DEBUG 级（健康检查专用，默认不输出）
│   └── status()                 # {path, fileLogging, degraded, problem, maxBytes, backupCount}
├── _open_file_handler(target)   # 返回 (handler|None, 失败原因|None)
├── configure(config, stream)    # 幂等；按 log_file 路径做 key，同路径只装配一次
├── default()                    # 兜底实例（只写 stderr，不碰磁盘）
├── timestamp()                  # ISO8601 毫秒 UTC，Z 结尾
├── count_lines(path)            # 1MB 一块统计当前文件行数
├── read_recent(path, ...)       # 尾部反向读 + 读取中过滤
└── _parse / _matches            # 残行容忍 / 过滤谓词
```

#### 9.2.1 四个刻意的设计（每一处都对应一个具体故障）

| # | 设计 | 不这么做会怎样 |
|---|---|---|
| 1 | **日志是辅助，不是依赖**：目录建不出/文件打不开 → `degraded=True` + `problem` 原因 + warn 一行，服务照常 | 一个因为日志写不了而拒绝服务的进程，等于把可用性押在最不重要的组件上 |
| 2 | **健康检查不进日志**：`ACCESS_LOG_EXCLUDED = {'/api/health'}`（`httpd.py:56`），只留 `logger.debug` 心跳 | Caddy 每 3 秒打一次 ≈ **28800 条/天**，会把 50MB 轮转窗口迅速挤满、把真正的故障记录顶掉 —— 出事时最想看的那些行最先消失 |
| 3 | **轮转有上限**：50MB × 5 | 没有上限的日志迟早写满宿主磁盘，而那时的表现是"SQLite 写不进去 → 全站 500"，排查方向被彻底带偏 |
| 4 | **按路径区分实例**：子 logger 名带 `sha1(路径)[:8]`，`propagate=False` | 同一进程可能有多个 `Application`（测试里就有三个：主实例、证书目录缺失实例、反向代理实例）；共用全局 handler 会"把 A 实例的请求记进 B 实例的日志文件"，排查时得出完全错误的结论 |

**为什么用 `logging` 而不是自己写文件**：`RotatingFileHandler` 的轮转与并发写加锁是标准库已经做对的事；零依赖约束下它是唯一"不用自己写"的正确选择（C-1 允许标准库，只禁止第三方）。

**为什么 `_Formatter` 只输出 `record.getMessage()`**：JSONL 的每一行必须**恰好**是一个 JSON 对象。一旦前缀了 `2026-11-17 08:00:00 INFO `，整份文件就不再可解析，采集侧要么解析失败、要么为"两种行格式"写分支。时间戳在 JSON 里（`ts`），所以不需要 logging 自己的时间前缀。

#### 9.2.2 降级路径的完整语义

| 失败点 | 表现 | 服务影响 | 可观测性 |
|---|---|---|---|
| `<data_dir>/logs` 建不出来（只读挂载/权限） | `_open_file_handler` 返回 `目录不可创建: ...` | **无** | `logger.warning`（走 stderr handler，所以一定看得见）+ `status().problem` |
| 目录在但文件写不了（只读文件系统/同名目录占位） | 返回 `文件不可写: ...` | **无** | 同上 |
| `write()` 内部任何异常 | 吞掉 | **无** | 无声（刻意：日志不能成为 500 来源） |
| 读取时路径不是目录 / 权限不足 | `read_recent` 返回空 + `READ_UNAVAILABLE` 标记，**不抛异常** | 无 | `GET /api/logs` 的 `fileLogging:false` + `degradeReason` |
| 读取时真 I/O 错误（EIO） | 抛 `LogReadError` → 500 `LOG_UNREADABLE` | 无 | 有明确错误码 |

> **跨平台坑（实测）**：同一个"父路径是个普通文件"的故障，**Windows 抛 WinError 183（`FileExistsError` 系），Linux 抛 `Errno 20 NotADirectoryError`**。只接一种就是"Windows 绿、Linux 红"——本项目的 L0 门禁正是被这一条拦住的。所以 `read_recent` **必须同时**接住 `NotADirectoryError`/`IsADirectoryError`/`PermissionError`（`applog.py:309-322`）。

### 9.3 访问日志字段规范（对外契约，`06` 需同步）

| 字段 | 类型 | 取值 | 说明 |
|---|---|---|---|
| `ts` | string | `2026-11-18T06:12:33.123Z` | ISO8601 毫秒、**强制 UTC**。容器时区五花八门，跨机排障时"本地时间"是陷阱：两台机器差 8 小时会让人以为是延迟 |
| `request_id` | string | 8 位十六进制 | 每请求生成（`_reset_access_state`），**与 500 响应体里给客户的编号是同一个**（`_internal_error` 复用，不自己再生成一个） |
| `uid` | string \| **null** | `u_xxxxxxxx` 或 `null` | 认证成功才有。**必须是 JSON `null`**，不能是 `anonymous`/空串 —— 否则按 uid 聚合时会多出一个虚构的"匿名用户"分组 |
| `ip` | string | `127.0.0.1` / XFF 首值 | `trust_proxy=0` 时是 TCP 地址；取不到记空串（不因取 IP 失败丢整条日志） |
| `method` | string | `GET`/`POST`/… | 请求行没解析出来时为空串 |
| `path` | string | `/api/rows?teamId=***` | **脱敏后**：query **值**全换 `***`、键名保留；控制字符压平为 `?`；超 200 字符截断加 `…` |
| `status` | int | 200/403/499/500… | 未发出响应时记 **499**（客户端提前断开），**不记 500** |
| `errcode` | string \| **null** | `INTERNAL_ERROR` / `PERMISSION_DENIED` / null | 正常请求记 `null`（不是 `''`/`'OK'`：聚合时 `null` 才是"没有错误码"） |
| `duration_ms` | number | `12.3` | 1 位小数；`_started_at` 缺失时为 `0.0` |

**字段级别的四条纪律**：

1. **`path` 脱敏在 `_log_access` 内部固定执行**，不接受调用方传入"已经处理好的 path"（`httpd.py:308-312`）。一旦把它变成调用方的责任，就一定会有人忘 —— 而"每请求都记"把记录量放大几十倍，`?email=secret@test.com` 会以 JSON 形式落盘，看起来更"规范"、更像可信数据。
2. **脱敏必须同时管"值"和"换行"**：只管抹值不管抹 CR/LF，日志仍可被投毒（`GET /api/x%0d%0aFAKE` 能伪造出第二行）。
3. **`_redact_path` 只有一个调用点**，且成本必须保持在"一次正则 + 一次切分"的量级（它每个请求都跑一次）。
4. **`uid`/`errcode` 只由认证路径与 `_error()` 写**，不在各 handler 里散写 —— 那样必然漏。

**健康检查的唯一痕迹**：`access.heartbeat(f'{command} {path}')`（`httpd.py:333-335`）→ `logger.debug` → 默认级别不输出。需要临时看它时**调 handler 级别**，但**不要**把它提成 INFO。

### 9.4 `GET /api/logs` 接口设计（v2.0 新增）

> 后端已实现（`httpd.py:1223-1332` + `applog.read_recent`），**前端无入口**（见 §9.8 缺口 G-1）。接口契约以 `06` 为准，此处定架构。

#### 9.4.1 为什么需要它

日志文件在容器里（`<data_dir>/logs/app.jsonl`），但运维最常问的三个问题——**"刚才那个 500 的编号对应哪条请求"**、**"现在有没有人在被限流"**、**"服务到底有没有在收流量"**——不该逼人去宿主机 `docker exec` + `tail`。有了这个接口，排障可以完全在浏览器里完成。

#### 9.4.2 契约

| 项 | 内容 |
|---|---|
| 方法与路径 | `GET /api/logs`（注意与 `POST /api/logs` **方法不同、语义不同**：后者是前端把运行日志上报进库） |
| 权限 | `_require_instance_admin`（与 AI 配置、证书管理同一门槛）。未登录 401；成员 403 `ADMIN_ONLY` |
| 为什么不是团队管理员可见 | 日志里有**全部用户**的 IP、路径与身份，是**实例级**运维数据。团队 owner/admin 能看到的应当只是自己团队的数据 |
| 参数 | `limit`（默认 100，上限 1000，超限按 1000 处理）、`status`（100~599 精确匹配）、`path`（子串）、`q`（全文子串，大小写不敏感，可查 `request_id`/`uid`/`ip`） |
| 参数校验 | `limit` 非整数或 ≤0 → 400 `INVALID_LIMIT`；`status` 非整数或越界 → 400 `INVALID_STATUS`；`path`/`q` 截断到 100 字符（逐行比对，超长纯浪费 CPU） |
| 响应 | `{logs:[...], total:<当前文件行数>, truncated:<bool>, fileLogging:<bool>, degradeReason:<str|null>}`，`Cache-Control: no-store` |
| `logs` 顺序 | **时间正序**（旧 → 新），等价 `tail -n`。"最新在最上"是渲染偏好，交给前端反转 —— 数据层与数据源同序，才不会把一次视觉决策固化进接口契约 |

#### 9.4.3 四个刻意的设计

1. **不碰数据库**（`httpd.py:1257-1259`）：日志不是业务数据，进库会让**每个请求都抢写锁**，把日志本身变成故障源（ADR-15 备选 C）。
2. **尾部反向读**（`applog.read_recent`）：按 64KB 块从文件尾往回读，凑够 `limit` 立即停，过滤条件**在读取过程中**应用。理由：日志可能到 300MB，而 `?status=500` 恰恰是在日志最多的时候才被用 —— 那时"读完再过滤"一次请求就能把进程撑爆。
3. **降级时根本不碰文件**（`httpd.py:1271-1278`）：日志不可用时（`<data_dir>/logs` 是个普通文件、或目录只读），打开文件**必然失败**，而"打开失败"会被翻译成 500 —— 于是「已经知道自己写不进去」的情况下还要去读它、再把"降级"报成"服务端错误"。**实测就是这条把 L0 门禁拦下来的。** 现在的判断不依赖任何 errno 语义，跨平台一致。
4. **`fileLogging` / `degradeReason` 让空结果没有歧义**：`logs: []` 有两种完全不同的含义——"真的没有请求"与"日志压根没写进去"。不区分就等于让"空"看起来正常，而这正是本项目反复踩过的病（`probe.sh` 空档 `exit 0`、`config is unchanged` 仍打印重载成功）。

#### 9.4.4 字段语义的坑（写进 `06`，避免误读）

| 字段 | 容易误解为 | 实际含义 |
|---|---|---|
| `total` | 匹配到的总条数 | **当前文件的总行数**（真去数，1MB 一块）。用途是回答"日志是不是被轮转掉了/是不是根本没写" |
| `truncated` | 精确的"还有 N 条" | **保守判断**：只有当匹配数达到 `limit` 且文件还没读到头时才为 true。达到 limit 就停，不为了精确再扫一遍全文件 |
| `logs: []` + `fileLogging: true` | 出错了 | 真的没有（匹配的）请求 |
| `logs: []` + `fileLogging: false` | 出错了 | 日志没写进去，看 `degradeReason` |

### 9.5 轮转、容量与保留

| 项 | 值 | 依据 |
|---|---|---|
| 单文件上限 | 50MB | `applog.MAX_BYTES` |
| 保留份数 | 5（`app.jsonl` + 5 个 `.1`~`.5`） | `applog.BACKUP_COUNT` |
| 磁盘上限 | ≈ 300MB | 50 × (1+5) |
| 轮转时机 | 写入使文件超过上限时（`RotatingFileHandler` 语义） | 标准库 |
| 备份文件命名 | `app.jsonl.1` … `.5`（越旧数字越大） | 标准库 |
| 是否压缩 | **否** | 压缩要第三方库或自写（破坏零依赖）；300MB 在宿主上可接受 |
| 是否跨机聚合 | **否**（未实现） | §11 P1 |

**为什么上限是"文件级"而不是"天数级"**：按天轮转需要一个定时器（线程/进程内 sleep），而按大小轮转只需要在写入时判断。前者会引入"日志线程挂了但没人知道"的新故障面；后者与写入同生共死。

### 9.6 关键链路监控点（零依赖约束下的采集）

> 本表在 v1.x 版本上更新了两处（访问日志与写锁冲突）、新增了四行（日志降级、证书待应用、发布互斥、`GET /api/logs` 自证）。

| 监控点 | 采集方式 | 判据/阈值 | 证据 |
|---|---|---|---|
| 进程存活 + 版本正确 | `GET /api/health`（免鉴权）回 `{ok,version,build,storage,local,llmConfigured}` | `ok==true` 且 `build == GTD_BUILD` | `httpd.py:924-941`；`deploy/smoke.sh` |
| 容器健康 | Dockerfile `HEALTHCHECK` / compose `healthcheck`（15s/5s/10s/3 次），用标准库 urllib 不装 curl | `healthy` | `Dockerfile:63-66`；`docker-compose.yml:98-106` |
| 上游自动退回 | Caddy `health_uri /api/health`、`health_interval 3s`、`health_timeout 2s` + `lb_policy first` | 首上游失败自动到备用 | `Caddyfile.template:41-48` |
| 切流不断线 | `probe.sh` 在切流窗口持续打**公网入口** | 失败数 0 且最大间隔 ≤ **3000ms** | `deploy/probe.sh`；总纲 §4 G6 |
| **切流真的生效（v2.0）** | `caddy_assert_upstreams` 比对**运行时**上游列表（顺序敏感） | 完全相等，最多等 1 秒 | `lib.sh:430-460` |
| **证书真的生效（v2.0）** | 宿主 `openssl s_client` 抓证书指纹 vs `tls.crt` 指纹 | 指纹相等；不等则重试并在第 3 轮起报"需人工介入" | `lib.sh:395-402`；`watchdog.sh:118-145` |
| **日志在某处真的落地（v2.0）** | `AccessLog.status()` / `GET /api/logs` 的 `fileLogging` | `fileLogging==true`；false 时必须能读到 `degradeReason` | `applog.py:166-174`；`httpd.py:1271-1278` |
| 登录爆破 | 进程内限速计数；`GET /api/logs?status=429` 可直接看到 | 429 出现即应告警（**目前无自动告警**）【推断】 | `security.py:162-199` |
| **写锁冲突（v2.0 改口径）** | **不再**对 traceback 文本写正则，改为 `GET /api/logs?status=500` + 按 `errcode` 聚合 | `errcode == 'INTERNAL_ERROR'` 的条数应为 0 | `httpd.py:501-505,353`；`test_05` 断言两进程日志中不出现 `database is locked`/`Traceback`【测试·有名】 |
| **发布互斥生效（v2.0）** | `deploy/run/release.lock` 被 `flock` 占用 | 并发发布时第二个进程立即 `die` | `release.sh:215-224` |
| 看门狗自愈 | `watchdog.sh` 连续 8 次不健康；默认只告警（`GTD_WATCHDOG_FAILOVER=0`） | 自动降级默认关闭，避免"没人说得清线上是哪版" | `env.example:57-62` |
| 磁盘 | `lib.sh:free_mb()`（部署脚本内） | 发布前检查；**v2.0 追加**：日志 300MB 上限要计入容量规划 | `lib.sh`（内容锚点：`free_mb`） |

- **零依赖约束下的采集方式**：不引入 Prometheus/agent；只用「标准库 HTTP 探针 + 容器 healthcheck + Caddy 主动健康检查 + 脚本 stdout + **JSONL 文件聚合** + 库内 `app_logs` + docker json-file 日志轮转」。
- **v2.0 的净收益**：写锁冲突与 500 的判定从"正则赌博"变成"结构化查询"（`errcode` 字段）。这是本轮实现里**对监控价值最大的一个字段**，成本只有一次赋值。

### 9.7 `app_logs` 表（数据库侧，与 §9.1~§9.6 是两条独立的流）

| 项 | 内容 |
|---|---|
| 写入路径 | `POST /api/logs` → `h_log` → `store.write_app_log`（`store.py:742-760`）；**允许匿名写入**（登录失败这类故障最需留证），限流键 `log\|<ip>` |
| 字段与截断 | `owner_id`（未登录 `null`）、`level`≤16、`scope`≤64、`event`≤120、`message`≤1000、`detail`（JSON）、`page_url`≤300、`user_agent`≤300、`created_at` |
| 权限 | insert 要求 `owner_id in (uid, None, '')`；**select 只能看自己的**；update/delete 一律 `deny`（避免掩盖故障证据）（`policy.py:251-264`） |
| 前端 | `log()` 缓冲 8 条或 1.2s 批量 flush，失败静默；敏感键精确脱敏（`api.js` 的 `log`/`flushLogs`） |
| 审计复用 | `create_account` 与建号**同事务**写一条 `event='account.created'`（§5.6）；§16.7 的开放接口审计也拟复用这张表 |
| **已知增长风险【推断】** | 只有 `created_at` 索引，**无自动清理/归档**；`LOG_LIMIT=400` 只限制 `load_workspace` 返回的 `progress_logs`，不限制 `app_logs` 的库内增长 → 交 `09` 定期清理 |

**为什么审计与访问日志必须是两条流**（这是最容易被合并错的地方）：

| | `app.jsonl`（访问日志） | `app_logs`（数据库） |
|---|---|---|
| 写者 | 服务端自己（每个请求） | 前端上报 + 服务端审计事件 |
| 频率 | 与请求数同阶（可达每秒数十条） | 低频（用户操作/建号/审计） |
| 是否需要事务 | **否** | **是**（审计必须与业务同事务，否则产生错账） |
| 是否可被用户读 | 仅实例管理员（文件） | 只能读自己的（策略） |
| 合并的后果 | 每次请求一次 INSERT → 读路径带写 → 违反 ADR-09 纪律② | 审计的低频写入（用户操作/建号/审计）被"与请求数同阶（每秒数十条）"的高频写拖累，且失去"**与业务同事务，否则产生错账**"的事务边界（见本节「是否需要事务」行与 §5.6 审计复用） |

### 9.8 v2.0 已知缺口（诚实清单）

| ID | 缺口 | 影响 | 归属 |
|---|---|---|---|
| G-1 | **`GET /api/logs` 前端无入口**：`js/api.js` 里没有对应导出，`views.js` 无日志视图 | 运维只能用 `curl` 直接打开。**已裁决（O-14）：补，但 P2** —— 它不是 bug（有替代路径 `docker logs` / `curl`），P0 要留给"错误信息"（如 REQ-039 的假空） | 交前端 + `06`（范围见 §14 O-14：只读 100 条 + status/path 过滤，**不做**实时刷新/导出/图表） |
| G-2 | 无自动告警：429/500/`fileLogging=false` 都只有数据源，没有通知 | 出事靠人看 | `09`（§14 O-7） |
| G-3 | 无跨机/长期检索：本地 300MB 上限，无外部采集 | 无法回答"上周三的错误" | §11 P1 |
| G-4 | `duration_ms` 只覆盖"处理时间"，不含排队/网络；无分位数计算 | 不能直接当 P95 用 | 需外部聚合 |
| G-5 | 访问日志不含 `user_agent`、不含 `referer`、不含响应体大小 | 判断"是脚本还是浏览器"要靠 `uid`+`path` 间接推断 | 刻意（字段越多越容易漏脱敏） |
| G-6 | `app_logs` 无清理策略 | 库内无限增长 | `09` |

---

## 10. 非功能设计

### 10.1 性能与容量边界

| 项 | 现状 | 风险 | 证据 |
|---|---|---|---|
| 写并发 | SQLite 单写；`busy_timeout=15000` 排队 + 应用层 6 次退避重试（累计约 1s） | 高并发写入会排队；重试耗尽返 500；Windows 争用上报 `SQLITE_READONLY` 已特判 | `db.connect`（`db.py:468`）；`httpd.py:151-172,751-784` |
| 读放大 | `load_workspace` 一次性全表读入内存 + Python 逐行 `can_select` 过滤 | 随数据量线性增长（ADR-06 代价）；`test_zerodowntime.py` 只验证 40 条并发写入场景 | `store.py:116-137` |
| 日志上限 | `progress_logs` 工作区只回 `LOG_LIMIT=400` 条；`app_logs` 无库内清理 | 见 §9.7 | `db.py` 的 `app_logs` DDL |
| 请求体 | 后端 8MB；Caddy `request_body 16MB`（先不掐断，让后端自己判） | OCR data URL 内联；超限 413 | `config.py:152`；`Caddyfile.template:32-36` |
| 长连接 | LLM 默认 120s 超时，前端 `llmChat` 180s；SSE 直通占线程 | 线程占用、上游挂起 | `config.py:135`；`api.js` 的 `llmChat`；`httpd.py:1523-1581` |
| 口令散列 | PBKDF2 240k 迭代 | 登录有 CPU 开销（安全/性能取舍） | `security.py:27` |
| **访问日志写放大（v2.0）** | 每请求 2 次写（stderr + 文件），JSON 序列化 1 次 | QPS 高时 stderr 的 flush 是最慢一环；`read_recent` 与写入争同一文件的读/追加锁（不同描述符，不阻塞） | `applog.py:121-135,146-152` |
| **日志读放大（v2.0）** | `read_recent` 按 64KB 往回读，最坏读满整个文件（当 `limit` 大于文件行数） | `+ total` 还要再全文件扫一遍数换行（1MB 一块，无内存放大） | `applog.py:258-277,280-357` |
| **证书校验开销（v2.0）** | 每次 `save` 会：2 次临时文件写 + 1 次 `load_cert_chain` + 1 次 `_test_decode_cert`（又一次临时文件）+ 2 次 `os.replace` | 低频操作（人手点一次），可忽略；**不要在轮询接口里调 `describe`** | `tls_cert.py:173-188,239-261,357-377,509-540` |
| **证书状态轮询（v2.0）** | 每次 `GET /api/admin/cert` 都**现场解析文件**（不缓存） | 这是刻意的：缓存会让"文件被改过"这件事看不见（§6.7.4）。代价是每次约 1 次 DER 转换 + 1 次 `_test_decode_cert` | `tls_cert.status`（`:439-495`） |

### 10.2 可用性

- 健康检查见 §9.6；`caddy reload` 校验不通过则**原地放弃**，线上仍是旧配置，"切流失败应该是什么都没发生，而不是服务挂了"（`lib.sh:462-489`）【代码·已核】。
- 自动退回：Caddy 主动健康 + 备用上游；`watchdog.sh` 可选自愈但默认只告警。
- 优雅关闭：SIGTERM/SIGINT → 另起线程 `server.shutdown()` → `serve_forever` 正常返回 → `server_close()`，让在途请求与 WAL 收尾；`shutdown()` 必须由另一线程调用否则死锁【代码·已核：`server.py`（内容锚点：`_install_signal_handlers`）】。
- 会话存共享库 ⇒ 切流不登出【测试·有名】。
- **v2.0 追加三条**：
  1. **日志不可写不影响可用性**（C-11，`AccessLogDegradedTest` 钉住）；
  2. **证书目录缺失不影响服务启动**（只影响证书三端点，返回 500 `CERT_DIR_MISSING`；`CertDirMissingTest` 钉住）；
  3. **证书应用失败不影响看门狗的本职巡检**（子 shell 隔离 + 重试计数，`watchdog.sh:62-96,118`）。

### 10.3 安全边界（摘要，详表交 `08`）

| 边界 | 手段 | 证据 |
|---|---|---|
| 认证 | HttpOnly+SameSite=Lax Cookie、令牌只存散列、30 天固定过期、改密踢会话 | §4 |
| **强制改密闸门** | `_require_user` 内、默认拒绝、6 条路径显式白名单 | §4.7 |
| **实例级门槛** | `_require_instance_admin` 唯一实现（AI 配置/证书/日志三处复用） | §5.7 |
| 授权 | `policy.py` 服务端逐行判定；前端不做归属判断 | §5 |
| CSRF | SameSite=Lax + 写操作 `Origin`/`Host` 校验（`Origin: null` 也拒）+ `Content-Type` 校验 | `httpd.py:9-14,431-446` |
| 跨站 | 不开 CORS；`do_OPTIONS` 405 | `httpd.py:650-652` |
| 静态暴露 | 三道关卡（逐段校验 / 首段白名单 / resolve 后仍在白名单子树）+ 无目录列表 + CSP（仅 html） | `httpd.py:834-901`；`config.py:22` |
| 注入 | 表名来自 `WRITABLE_TABLES` 白名单，值一律 `?` 占位绑定 | `store.py:33-41`、`store._insert_raw` |
| **协议走私（v2.0）** | 早退响应前必须消费 body；`_body_consumed` 每请求重置 | §8.5 |
| **日志注入（v2.0）** | `path` 控制字符压平 + query 值脱敏 + 长度截断；脱敏只在一个调用点 | §9.3 |
| **私钥暴露（v2.0）** | 私钥不进 settings / 不进日志 / 不出现在响应；模块内无 `print`；临时文件同目录 + `os.replace` + 用完即删 | §6.5、§6.7.3 |
| 密钥 | 不回显、脱敏、`.env` 排除 | §6.5 |
| 容器 | 非 root、只读根文件系统、cap_drop ALL、no-new-privileges | `Dockerfile:49-57`；`docker-compose.yml:89-96` |
| 响应头 | nosniff / no-referrer / X-Frame-Options DENY；HTTPS 场景 HSTS | `httpd.py:469-490`；`Caddyfile.template:63-71` |

### 10.4 可移植性 / 备份恢复在架构中的位置

- 可移植性：C-1~C-5；切换部署形态只改环境变量/启动方式，不改代码（`config.py:85-88`）。
- 备份恢复分两条：① **逻辑备份** `server.py --export/--import`（纯 JSON，跨机器/跨版本）+ `deploy/backup.sh`（cron，自动识别活跃槽位）；② **物理备份** 直接复制 `data/gtd4teams.db`（建议先停服务或用 `--export`，因有 `-wal`）。
- 约束：`--import` 是 `INSERT OR REPLACE`，**既不清空也不去重**，灾难恢复必须先删库文件再导入；`sessions` 不导出（迁移后重新登录）；备份文件含口令散列与 `is_system_admin` 标记，属敏感文件。
- **结构不匹配必须显式失败，不得静默降级**。`import_all` **不做任何列裁剪**，但必须捕获 `no such column` / `no such table`，翻译成 `StoreError('IMPORT_SCHEMA_MISMATCH', '备份文件的结构与当前库不一致：请先运行 --check 让数据库升级到最新结构，再导入', 400)`；其余 `OperationalError` 原样上抛。
  - **决策依据（留档）**：曾考虑给 `users` 加导入列白名单，被否决 —— 白名单会让"新备份 → 旧结构库"**静默成功**：`is_system_admin` 被悄悄丢掉，还原后实例**零管理员**、没人能建号，而操作者看到的是"导入成功"。**一个明确的失败 + 一句"先 `--check`"，比一次成功的静默降级安全得多。**
- 回滚在架构中的位置：`deploy/rollback.sh`（零停机，状态互换）+ 数据库 expand/contract（本文不重复 SQL）。
- **v2.0 追加（备份内容的三处变化）**：
  1. **`--export` 现在会带出 `is_system_admin` 与 `must_change_password` 两列**（`BACKUP_TABLES` 含 `users`，`store.py:803`）。从生产还原到测试后，测试环境也有同一批实例管理员 —— 期望行为，但**必须写进 `09`**。
  2. **`data/logs/app.jsonl` 不在逻辑备份里**（它不是业务数据，且含全员 IP）。要么随卷备份，要么明确"不备份"。**必须二选一写清**，否则灾难恢复后"日志从某个时间点消失"会让人以为丢数据。
  3. **`<cert_dir>` 下的 `tls.crt`/`tls.key` 不在数据库备份里**。灾备必须同时备份 `deploy/run/certs/`（否则还原后 HTTPS 起不来）。**这是 v2.0 新增的一条灾备必做项**。

### 10.5 v2.0 新增：三处"降级而非失败"的设计及其代价

本轮三项能力都采用"失败只降级"的模式。这是一种**刻意的可用性优先**选择，但每种降级都必须有"可观测的出口"，否则会退化成"静默失效"——本项目已经吃过三次这个亏（`probe.sh` 空档 exit 0、`config is unchanged` 仍打印成功、GATE-01/02 只看退出码）。

| 能力 | 降级行为 | 可观测出口 | 若没有出口会怎样 |
|---|---|---|---|
| 访问日志 | 只写 stderr | `status().degraded/problem`；`GET /api/logs` 的 `fileLogging=false` + `degradeReason` | "监控没数据"会被当成"没有流量" |
| 证书上传 | 不 `mkdir`，直接 500 `CERT_DIR_MISSING` | 错误文案直接指向挂载与属主 | 证书写进容器只读层、Caddy 看不到、重启即丢，排查方向被彻底带偏 |
| 证书应用（watchdog） | 失败保留标记、下一轮重试 | `deploy/run/cert-apply.log` + 第 3 轮起写明"需人工介入"；前端 `pendingApply:true` | 用户以为生效了，而线上还是旧证书 |
| 发布切流 | 断言失败即 `die`，旧配置仍生效 | `die` 的输出含"期望/实际（去重后）/实际（原始）"+ 最常见原因 | `reload` 返回 0 被当成成功（inode 事故的原始形态） |

**判据（写给未来的自己和 reviewer）**：任何"失败但不报错"的分支，都必须回答两个问题——**谁会看到它？在哪里看到？** 答不上来的，就不许降级，宁可失败。

---

## 11. 演进与重构路线

> 每条标注「是否破坏零依赖 / 无构建链约束」。**P0 = 治理期即可做（不改运行时行为）；P1 = 需评估；P2 = 需重新裁决硬约束。**

| 优先级 | 建议 | 破坏零依赖？ | 破坏无构建链？ | 说明 |
|---|---|---|---|---|
| P0 | 把「版本串同步」做成纯标准库/`node` 冒烟断言（§7.4） | 否 | 否 | 加测试用例，不引入工具链 |
| P0 | 修正 `GTD_BIND` 文档漂移（§6.4 / O-1） | 否 | 否 | 纯文档一致性 |
| P0 | 把 K-1~K-6 写进 `05` 与 `06` 的「刻意保留」清单 | 否 | 否 | 反向补课闭环 |
| P0 | 补 `app_logs` 的定期清理/归档 SOP（交 `09`） | 否 | 否 | 运维脚本/文档，不动运行时 |
| **P2（v2.1 裁决）** | **给 `GET /api/logs` 补前端入口（G-1）** | 否 | 否 | **原拟 P0，主理人裁定降为 P2**：它不是 bug（有替代路径 `docker logs` / `curl`），P0 应留给"错误信息"（如 REQ-039 的假空）。范围**限定**为：系统管理页只读最近 100 条 + `status`/`path` 过滤；**不做**实时刷新、导出、图表。位置与证书管理并列（O-19 已统一为系统管理页分区） |
| **P0（v2.0）** | **在 `09` 写清"备份三件套"：库 + `data/logs` + `deploy/run/certs`（§10.4）** | 否 | 否 | 否则灾备后 HTTPS 起不来、日志断档 |
| **P0（v2.0）** | **补 §8.5.4 的三条协议级用例** | 否 | 否 | 纯测试 |
| P1 | 集中限速/会话吊销（跨进程共享），例如把限速计数落库 | 否（可用现有 sqlite） | 否 | 修 R-5；但会给读路径引入写，与 ADR-09 纪律②冲突，须评估 |
| P1 | 给 `429`/`500`/`fileLogging=false` 加轻量告警（脚本轮询 health/JSONL） | 否 | 否 | 零依赖脚本；交 `09` |
| P1 | `load_workspace` 增量化 / 分页 / 按 team 过滤 | 否 | 否 | 缓解 §10.1 读放大；改 `api.js` 契约，需走 `CR-` |
| P1 | 日志集中化（外部采集/轮转天数策略） | 否 | 否 | 打破"单机单文件"，需评估部署复杂度 |
| **P1（v2.0）** | **证书到期告警/自动续期** | 否 | 否 | `describe` 已给 `daysRemaining`，缺的是"定时检查 + 通知"；ACME 路径由 Caddy 自带 |
| **P1（v2.0）** | **集成域落地（§16，REQ-045~055）** | 否（纯标准库） | 否 | 工作量最大的一块；建议按 §16.11 的批次推进 |
| P1 | 真机浏览器冒烟（Playwright 等） | 否（仅开发/测试期） | 否（不进运行时） | 本机无此能力，【未验证】 |
| P2 | 引入 FastAPI/ORM/迁移框架 | **是** | 否 | 推翻 ADR-01/05，需用户重新裁决硬约束 |
| P2 | 换 PostgreSQL + 原生 RLS | **是**（需驱动） | 否 | 推翻 ADR-02/06，代价最大 |
| P2 | 引入 Vite/前端框架/组件库 | 否 | **是** | 推翻 ADR-03 |
| P2 | 拆分 `personal_items`/`team_items` 两表 | 否 | 否 | 推翻 ADR-07 + K-6，属破坏性数据库变更（expand/contract × 3 次发布） |
| **P2（v2.0）** | **访问日志改用 `WAL` 之外的结构化存储（如 SQLite 只写表）** | 否 | 否 | 会违反"读路径不带写"（ADR-15 备选 C），除非改为异步批量落库 —— 那又引入"日志线程挂了没人知道" |

### 11.1 明确「不建议做」的改造

1. **把 K-1 `work_items` update 补上 owner 限制** —— 会当场打断团队看板流转（`policy.py:270-279`、`README.md:88`）。
2. **把 K-2 `contexts` update 的 owner 限制"修好"** —— 云版遗留形状，差异本身是要记录的事实（`README.md:89`）。
3. **把空数组改成抛 403/404** —— 破坏 `assertAffected` 语义与 `test_14/15/20`，且泄露数据存在性（ADR-08）。若机器接口需要可区分错误，按 §16.4 在 API 层再判一次，**不动 store 语义**。
4. **采信客户端 `owner_id`** —— 直接制造越权（K-4、`test_17`）。
5. **把 `team_members.status != 'active'` 放宽回"行存在即成员"** —— 违背"停用即失去访问权"产品承诺（K-5、`test_20`）。
6. **无条件信任 XFF / 直接 `docker compose up -d` 拉起两个槽位** —— 前者使爆破不限速，后者破坏"同时只有一个活跃槽位"不变量。
7. **给静态目录开目录列表 / 加 CORS / 给 `.html` 加强缓存** —— 分别对应云版踩过的公开源码坑、Cookie 被任意站点调用、"改了没生效"的难排查故障。
8. **为「方便」在 `store.py` 里绕过 `policy.can_*` 直接写** —— 权限边界必须单点。
9. **把访问日志改成"只在出错时记"**（回到旧实现）—— 立刻失去 QPS/成功率/耗时的全部数据源（§9.1）。
10. **把 `GET /api/logs` 的权限从"实例管理员"放宽到"团队管理员"** —— 日志含**全部用户**的 IP 与路径，团队管理员没有这个数据范围的合法理由。
11. **给 `.pending-apply` 加上"应用成功"的含义，或让后端删它** —— 后端无法知道 Caddy 加载了没有；删标记等于声称已生效（ADR-17）。
12. **为了让证书"立即生效"而让后端直连 docker socket** —— 破坏容器安全基线（无 socket、只读、cap_drop ALL）。
13. **把 `Caddyfile` 的写入改回 `mv`** —— inode 不跟随，切流静默失效（ADR-18）。

---

## 12. 严格模式六项必填

> 依据 `HARD_GATES.md`「严格模式必须输出」与总纲 §9 索引。风险清单的**工程/架构侧**落本文；安全侧落 `08`、运维侧落 `09`、需求侧落 `02`。

### 12.1 风险清单（影响范围 / 触发条件 / 严重程度）

| ID | 风险 | 影响范围 | 触发条件 | 严重度 | 证据/状态 |
|---|---|---|---|---|---|
| R-1 | 双进程共库写锁冲突导致 500 | 全部写接口、切流窗口 | 蓝绿切换期间新旧进程并发写 | 高 | 三处代码纪律 + 6 次退避缓解；`test_zerodowntime.py`【测试·有名】，但 **Linux 生产未实测【未验证】** |
| R-2 | 构建标识核对失效，切流到旧容器 | 发布正确性 | 旧容器端口也通；`build` 未核对 | 高 | `smoke.sh` 有核对；`test_02/07`【测试·有名】 |
| R-3 | `GTD_TRUST_PROXY` 与绑定配对搞反 | 登录限速 / Cookie | 槽位端口改绑 `0.0.0.0` 却仍置 1，或 Caddy 改「追加」XFF | 高 | `README.md:142-145`；总纲 G5 |
| R-4 | ~~自助注册在公网默认开放~~ | 账号体系 | — | **已消除**（CR-2026-001 永久下线注册；`GTD_ALLOW_SIGNUP` 已无行为） | `config.py:90-100` |
| R-5 | **蓝绿期间登录限速被稀释** | 认证 | 两个进程各自持有 `_ATTEMPTS` 字典 | 中 | 【推断】：`security.py:162-199` 为进程内状态；`test_08` 仅单进程验证。§14 O-5 |
| R-6 | 全量 `load_workspace` 读放大 | 响应时间 | 单表行数持续增长 | 中 | 【推断】：`store.py:116-137` 全表读入；无分页 |
| R-7 | `app_logs` 无限增长 / 匿名可写 | 磁盘、日志可信度 | 长期运行、匿名高频上报 | 中 | 【推断】：无清理策略，仅 `log\|ip` 限速 |
| R-8 | `--import` 叠加残留数据 | 灾难恢复正确性 | 向非空库导入备份 | 中 | `store.import_all`；已文档化，仍需操作纪律 |
| R-9 | `GTD_BIND` 文档漂移误导配置 | 部署配置 | 运维照旧文档设置不存在的变量 | 中 | §6.4、O-1【代码·已核：变量不存在】 |
| R-10 | LLM 密钥随 `--export` 外泄 | 凭据 | 备份文件保管不当 | 中 | `store.export_all` 已注明敏感 |
| R-11 | SSE 流中断 / 上游挂起 | AI 功能可用性 | 上游断连、客户端断开 | 低中 | `httpd.py:1560-1581` 已捕获 `BrokenPipeError`/`ConnectionResetError` |
| R-12 | **初始口令在传递途中泄露** | 新建账号的登录凭据 | 管理员把口令发给使用者，任何中间环节被旁观 | **高** | 缓解：口令系统生成只回一次 + `no-store` + 不写日志 + 强制首登改密（§4.7）。**残余**：改密前被旁观等同泄露 |
| R-13 | **引导流程漏做导致无人能建号**（不是锁站） | 账号管理能力 | 迁移后没有任何 `is_system_admin=1` 的账号；或唯一系统管理员忘口令 | 中高 | 存量账号 `must_change_password=0` → **不会被锁站**。break-glass = `--set-password`/`--grant-admin`。必须写进 `09` |
| R-14 | **将来新增的端点漏过闸门** | 强制改密约束 | 新 handler 不调 `_require_user` | 中高 | 结构上已收敛；**兜底必须自动化**：遍历 `ROUTES`、以 `must_change_password=1` 的会话断言除白名单外全部 403，且有成对的反向断言 |
| R-15 | **CLI 与 HTTP 的建号逻辑漂移** | 账号安全 | 两处各写一份 `INSERT users` | 中 | **已收敛**（`store.create_account`/`reset_password`）→ 残余为"将来有人再写一份"，靠 §7.6 登记纪律 |
| **R-16** | **日志静默降级被当成"没有流量"** | 监控/告警 | 数据目录只读、`logs` 被占用为文件、磁盘满 | **中高** | 缓解：`degraded/problem` + `GET /api/logs` 的 `fileLogging=false`（§9.4.3 第 4 条）。**残余**：没人看这个接口时仍然静默 → 需告警（§11 P1） |
| **R-17** | **日志文件被写满导致 SQLite 一起失败** | 全站可用性 | 轮转失效（权限/挂载异常）或 300MB 上限被打满且宿主无空间 | **高** | 缓解：50MB×5 硬上限 + 健康检查不入日志 + 降级。**残余**：300MB 仍可能压垮小磁盘 → 容量规划交 `09` |
| **R-18** | **访问日志泄露 PII / 被注入伪造行** | 隐私、日志可信度 | 有人用含邮箱/令牌的 query 打接口；或尝试 CRLF 注入 | **高** | 缓解：`_redact_path` 固定调用、query 值全 `***`、控制字符压平、200 字符截断（§9.3）。**残余**：`path` 段本身不脱敏（如 `/api/user/alice@x` 这类路径设计**禁止新增**） |
| **R-19** | **`flock` 缺失导致发布互斥失效** | 发布正确性 | 极简宿主机/镜像无 `flock`（util-linux） | **中高** | 现状：`command -v flock` 判断后**静默跳过**（`release.sh:217`）。**这是已知缺口**，建议改为"缺 flock 时 warn + 要求人工确认"（§14 O-15） |
| **R-20** | **证书换新后未真正生效，而界面显示已上传** | HTTPS 正确性 | 手工替换文件未 `touch .pending-apply`；或 watchdog 未装 cron；或 Caddy 因配置未变跳过加载 | **高** | 缓解：指纹版本化命名 + 双重断言 + `pendingApply` 诚实显示（§6.7.5）。**残余**：无 cron 的部署下"标记永远存在"，只能靠人看 `cert-apply.log` |
| **R-21** | **私钥通过临时文件/日志/响应外泄** | 最高敏感数据 | 临时文件落在 `/certs` 且未删；异常文案拼接了路径；响应误带 `keyPem` | **高** | 缓解：临时文件同目录 `.tmp-` 前缀 + `finally` 删除；`CertError` 不拼原文；模块内零 `print`；`test_cert_upload_does_not_leak_key` 钉住 |
| **R-22** | **集成域一旦启用，令牌 = 账号全权限的长期凭据** | 全站数据 | 令牌泄露；或令牌被授予了过宽的作用域 | **高（设计已缓解，实现未落地）** | 设计缓解：默认关闭 + 只绑 `127.0.0.1` + 作用域 + 独立撤销 + 不入日志 + 拒绝带 `Origin` 的请求（§16.3/§16.4）。**【设计·未实现】** |

### 12.2 测试用例清单（索引到 `07`，不重复定义）

| 组 | 现有资产（v2.0 实测） | 用例名/编号 | 覆盖 |
|---|---|---|---|
| L0 后端 | `tests/test_api.py` **3857 行 / 156 个 `test_` 方法 / 20 个测试类** | `BackendTest`(54，含 `test_01`~`test_40`) + `StorageSpecTest`(8) + `MigrationTest`(9) + `PasswordChangeGateTest`(5) + `AccountAdminTest`(20) + `CliAccountTest`(10) + `SecurityAuditTest`(7) + **`CertAdminTest`(13)** + **`CertDirMissingTest`(1)** + **`AccessLogRedactionTest`(4)** + **`AccessLogTest`(10)** + **`AccessLogDegradedTest`(2)** + **`AppLogReaderTest`(5)** + `LlmHelperTest`(1) + `AdminConfigTest`(2) + `NoAdminConfiguredTest`(2) + `TrustProxyTest`(3) | 认证/权限/存储/代理信任/防漂移/**证书/日志**（计数为 v2.1 按类精确重算，合计 156） |
| L0b 零停机前提 | `tests/test_zerodowntime.py` 9 项 | `test_01`~`test_09` | 双进程共库、会话跨进程、build 区分、并发写零失败、日志无锁冲突、smoke/probe 行为 |
| L1 前端 | `.smoke/render-test.mjs` / `auth-flow-test.mjs` / `app-boot-test.mjs` | 表驱动错误码映射、真实 index.html 启动 | 渲染、认证流程、挂载点/视图分发 |
| 发布期 | `deploy/smoke.sh` / `probe.sh` | 构建标识核对、暴露面、鉴权边界、≤3000ms | 总纲 G6 |

**v2.0 建议补充的用例**（交 QA，编号沿用 `TC-<分组>-<序号>`）：

| ID | 用例 | 为什么需要 |
|---|---|---|
| TC-LOG-01 | 日志目录只读时 `GET /api/logs` 返回 `fileLogging:false` 而**不是** 500，且服务其他接口正常 | 已有 `test_logs_endpoint_does_not_fake_empty_result`，但未覆盖"目录只读"这一变体（现有覆盖的是"路径是文件"） |
| TC-LOG-02 | `?limit=99999` 被夹到 1000，且 `total` 不等于返回条数时不会 500 | 上限与 total 语义易误读 |
| TC-LOG-03 | 有 `Origin` 头的 `GET /api/logs` 不返回 CORS 头（浏览器跨域读不到） | 防"任何网页都能读日志" |
| TC-CERT-01 | 证书上传成功但 watchdog 未运行 → `pendingApply` 持续 true，**不得**变成 false | 防"后端假装已生效" |
| TC-CERT-02 | 元数据指纹与文件指纹不一致时 `source=selfsigned` 且 `uploadedBy=null`，且**不修改**元数据 | 已有 `test_cert_get_prefers_file_over_stale_meta` 覆盖前半，后半（不改元数据）无断言 |
| TC-BODY-01 | 静态资源分支：`POST /` → 405 后同连接正常请求不受影响 | §8.5.4 缺口 1 |
| TC-BODY-02 | 坏 JSON → 400 后同连接正常请求不受影响 | §8.5.4 缺口 2 |
| TC-BODY-03 | 声明 9MB body → 连接被关闭且服务不为它分配大内存 | §8.5.4 缺口 3 |
| TC-DEP-01 | 并发两次 `release.sh`，第二个立即失败并提示锁占用 | C-14，需 Docker 环境【未验证】 |
| TC-INT-01~ | 集成域用例 | §16.11（实现后） |

### 12.3 回滚方案（架构层，运维 SOP 在 `09`、迁移回滚 SQL 在 `05`）

| 层 | 手段 | 依据 |
|---|---|---|
| 应用 | `deploy/rollback.sh`：状态互换回上一版镜像，同样零停机，可"回滚掉这次回滚" | `README.md:117`；`rollback.sh` 依赖 tag 字典序=时间序 |
| 切流 | `caddy_reload()` 校验/断言失败即原地放弃，线上仍是旧配置 | `lib.sh:462-489` |
| 配置 | `.env` 与 `deploy/run/state.env` 由脚本维护；`Caddyfile` **原地覆盖**（不是 `mv`） | `lib.sh:343-350` |
| 数据库 | expand/contract：一次发布只做加法；**`_MIGRATIONS` 是向前 only（无 down）** → 回滚 = 恢复备份 + 用旧镜像启动（旧镜像的 `init_db` 因 `schema_present()` 判 True 而不会重跑 DDL，所以"多出来的列"必须被旧代码容忍） | `db.run_migrations`；§5.4 |
| 数据 | 逻辑备份（`--export`/`backup.sh`）+ 物理备份；`--import` 前须先删库文件 | §10.4 |
| **证书（v2.0）** | 回滚不改证书文件；若新证书有问题 → 重新上传旧证书（`POST /api/admin/cert` 成对上传）或 `DELETE` 回到无证书状态。**不要**手工 `git checkout` 证书（它不在版本库） | §6.7 |
| **日志（v2.0）** | 回滚不影响日志文件；旧版本的 `_log_access` 是 `print` 实现，回滚后 `app.jsonl` 会**停止增长**（不是损坏）。**排查时不要以为日志坏了** | §9.1 |
| 版本串 | 回滚后 `APP_VERSION` 与 `?v=` 必须一并回退，否则浏览器缓存与服务端版本不一致 | 总纲 §7.2 |

### 12.4 验收标准（可勾选，G1 侧）

- [ ] §5.2 的 11 行 × 5 列与 `policy.py:267-310` 逐条一致，且 K-1~K-6 已标「刻意保留」
- [ ] §5.8 的三处"不可顺手优化"已被 reviewer 明确确认（不是"看起来多余就删"）
- [ ] §1 的 C-1~C-15 每条有证据标记与验证方式，无 `【未验证】` 被写成「已验证」
- [ ] `03 FDS` 的页面/流程能映射到本文 §2.2 的模块，无孤儿页面
- [ ] `06` 的端点清单与 `ROUTES`（`httpd.py:1584-1614`，**24 条**）逐条一致，含错误码
- [ ] `05` 的表/字段与 `db.COLUMNS` + `EXPECTED_TABLES` 一致，任何变更含迁移 SQL + 回滚 SQL + 兼容性分析
- [ ] §9 的日志字段规范与 `06` 的 `GET /api/logs` 契约一致（9 个字段 + 4 个响应顶层字段）
- [ ] §6.7 的证书三段式与 `09` 的运维 SOP 一致（尤其"手工替换必须 touch 标记"）
- [ ] §16 的集成域**通篇标注「设计未实现」**，未与已实现章节混淆
- [ ] 严格模式六项（§12.1~§12.6）齐备；`【未验证】` 项已汇总到 §13 并指派角色
- [ ] 涉及登录/权限/证书的改动已由安全角色在 `08` 复核并进入严格模式

### 12.5 日志与监控点

见 §9.6 表（health/build、容器 healthcheck、Caddy 主动健康、probe ≤3000ms、**运行时上游断言**、**证书指纹断言**、**日志落地自证**、限速 429、写锁冲突 `errcode` 聚合、**发布互斥**、看门狗、磁盘）。补一句判据：**切流前必须核对新槽位 `/api/health` 回传的 `build` 与本次 `GTD_BUILD` 一致**（`smoke.sh`）；「端口通 ≠ 版本对」。

### 12.6 失败处理方案（超时 / 失败 / 重复 / 部分成功 / 补偿）

| 场景 | 现状处理 | 证据 | 残余 |
|---|---|---|---|
| 请求超时 | 前端 `AbortController` 30s（LLM 180s）；上游 `GTD_LLM_TIMEOUT=120s`；Caddy 健康 `health_timeout 2s`；`_discard_body` 2s 读超时 | `api.js`；`config.py:135`；`httpd.py:419-429` | 服务端无主动取消 LLM 上游请求的机制【推断】 |
| 上游失败 | `LlmError` 分类：`LLM_UNREACHABLE`(502)/`LLM_UPSTREAM_ERROR`(502,已脱敏)/`LLM_TIMEOUT`(504)；未配置 `LLM_NOT_CONFIGURED`(400) | `llm.py:40-71` | — |
| 写失败 / 部分成功 | 每请求单连接 + 单事务 `commit`；异常时 `finally: conn.close()` 丢弃未提交事务 ⇒ **不会留"写了一半"**；重试不会重复写入（`commit_attempted`） | `httpd.py:751-784`；`db.py:482-560` | — |
| 写锁争用 | 6 次指数退避（0.05→0.4s，累计约 1s）；仅当响应未开始写**且**从未尝试提交才重试；SSE 路径由 `send_header` 置 `_responded` 覆盖 | `httpd.py:244-253,769-778` | 耗尽则 500 `INTERNAL_ERROR` + traceback |
| **请求体残留（v2.0）** | 早退前 `_discard_body()`；已消费则立即返回；超大 body 直接关连接 | §8.5 | 静态 405/坏 JSON/超大 body 三条无用例（U-13） |
| **日志写失败（v2.0）** | 降级为 stderr + warn 一行；`write()` 吞异常 | §9.2.2 | 无人看 `fileLogging` 时仍静默（R-16） |
| **证书上传失败（v2.0）** | 四段校验在**写盘之前**完成；临时文件 `finally` 清理；错误码区分四类 | `tls_cert.validate_pair`/`save` | — |
| **证书应用失败（v2.0）** | 保留标记、写 `cert-apply.log`、下一轮重试、第 3 轮起写明需人工介入；子 shell 隔离不影响巡检 | `watchdog.sh:62-96,118-145` | 无 cron 时标记永远存在（R-20） |
| 重复请求 | 建号重复邮箱 → 409 `EMAIL_EXISTS`；邀请码唯一索引 → 409 `JOIN_CODE_TAKEN`；重复加入幂等返回同 `teamId`；复盘同周期 upsert 只留一行；**`update_row` 无可写字段时直接返回当前行不提交** | `store.py:184-185,487-492`；`test_26` | 通用 `insert` **无幂等键**，重复提交会产生两行【推断】 → §16.6 为机器接口补幂等键 |
| 会话记账失败 | `last_seen_at` 刷新失败 → 回滚并继续；过期会话清理失败 → 忽略 | `httpd.py:534-568` | 宁可丢一次活跃时间也不给用户 500 |
| 切流失败 | Caddy 配置校验不过 / reload 失败 / **运行时断言不符** → 放弃，旧配置仍生效 | `lib.sh:462-489` | 「什么都没发生」 |
| **发布并发（v2.0）** | `flock -n` 拿不到即 `die` | `release.sh:215-224` | 无 `flock` 时静默无保护（R-19） |
| 探测失败 | `release.sh` 以非 0 结束（总纲 §4 G6） | 总纲 §4 | — |
| 补偿 | 无消息队列/事务补偿（单机单事务已覆盖主要路径）；灾难恢复靠备份 + 先删库再导入 | §10.4 | 跨系统一致性不适用 |

---

## 13. 未验证项（本机无法验证，需在服务器执行）

> 用户明确要求区分「已验证/推断/未验证」。以下逐条给出**命令**与**期望判据**，并标注责任角色；本次工作**一律未实际执行**。

| ID | 未验证项 | 需执行的环境 | 命令 | 期望判据 | 责任 |
|---|---|---|---|---|---|
| U-1 | 双进程共库在 Linux 生产成立 | 服务器（有 Docker/双进程） | `python3 tests/test_zerodowntime.py` | 9 项全绿，尤其并发写零失败、日志无 `database is locked`/`Traceback` | 运维/QA（总纲 §1 唯一阻塞项） |
| U-2 | compose 编排 YAML 锚点语义正确 | 服务器 | `docker compose config -q` | 退出 0；两槽位配置除名字/端口外一致 | 运维 |
| U-3 | 镜像构建与构建自检 | 服务器 | `docker build --build-arg GTD_BUILD=<tag> -t gtd4teams:<tag> .` | `Dockerfile:37-40` 的 import 自检与 `--check` 通过 | 运维 |
| U-4 | Caddy 配置生成、热加载与**上游断言** | 服务器 | `deploy/install.sh` / `release.sh`，或 `caddy validate --config deploy/run/Caddyfile --adapter caddyfile` | 校验通过；`reload` 返回 0；`caddy_assert_upstreams` 打印 `[ ok ]` | 运维 |
| U-5 | 真实切流连续性 | 服务器 | `bash deploy/probe.sh <公网入口> ...` | 失败数 0、最大间隔 ≤ 3000ms | 运维 |
| U-6 | HTTPS 自动证书 / HSTS / Secure Cookie | 有域名的服务器 | 配置 `GTD_DOMAIN` 后部署并浏览器检查 Cookie | `Set-Cookie` 含 `Secure`；HSTS 头存在 | 运维/安全 |
| U-7 | 真实 LLM 上游（非流式 + SSE） | 配密钥的环境 | `POST /api/llm/chat`（`stream:true/false`） | 非流式返回 `{text,...}`；SSE 逐块 `data:` 并以 `[DONE]` 收尾 | 后端/QA |
| U-8 | 真机浏览器冒烟 | 有浏览器的环境 | 参见 `07`/`08` | 与 `.smoke` 三套断言一致 | QA |
| U-9 | WorkBuddy sites 形态的 `GTD_TRUST_PROXY` 取值 | 沙箱 | 比对平台反代是否覆盖 XFF | 若不能保证覆盖 → 保持 0 | 主理人（O-4） |
| U-10 | 本次文档声称的「全绿」 | 本机 | `python tests/test_api.py && python tests/test_zerodowntime.py && node .smoke/*.mjs` | 全部退出 0 | **本次未复跑**；全绿为上下文转述 |
| **U-11** | **证书上传 → watchdog 应用 → 运行时指纹一致的完整闭环** | 服务器（Docker + cron + openssl） | ① `POST /api/admin/cert` 上传新证书 ② 等 ≤ 2 分钟 ③ `bash deploy/watchdog.sh` 看日志 ④ `openssl s_client -connect 127.0.0.1:443 </dev/null \| openssl x509 -noout -fingerprint -sha256` | `cert-apply.log` 出现 `=== 开始应用证书 ===` 且标记被删除；抓到的指纹 == `tls.crt` 指纹 | 运维（R-20） |
| **U-12** | **证书按指纹版本化命名真的让 reload 重新加载** | 服务器 | 换证书 → `caddyfile_generate` → 检查 `deploy/run/certs/` 下出现 `tls-<新fp16>.crt` 且旧副本被清 | 文件名变化；`caddy reload` 日志**不再**是 `config is unchanged` | 运维（ADR-17） |
| **U-13** | **§8.5.4 的三条协议级用例**（静态 405 / 坏 JSON 400 / 超大 body） | 本机即可 | 新增用例后 `python tests/test_api.py` | 三条全绿，且同连接后续请求正常 | QA（补用例） |
| **U-14** | **并发发布互斥** | 服务器 | 两个终端同时 `bash deploy/release.sh` | 第二个立即 `die` 并打印"另一发布正在进行" | 运维（C-14） |
| **U-15** | **日志降级在生产文件系统上的真实表现** | 服务器（只读挂载） | 把 `/data` 挂成只读后启动，看 stderr 与 `GET /api/logs` | 服务正常起；stderr 有一行 warn；`fileLogging:false` + 有 `degradeReason` | 运维/QA（R-16） |

---

## 14. 待确认 / 开放问题（提交主理人裁决）

> **说明**：O-9~O-13 五项已由主理人 **2026-11-14 裁决定稿**，不再是开放问题 —— 保留行是为了留档"为什么这样定"，请勿重新讨论。**O-14 / O-19 已于 2026-11-18 裁决定稿（v2.1 已按裁决改写正文）**。**O-1~O-8、O-15~O-18 亦已于 2026-11-18 由主理人逐条裁决定稿，本版按裁决回写、保留原行留档 —— 至此 §14 全部 19 项均已裁决，无待裁决项**（裁决明细见各行"建议 / 已裁决"列，标 ✅）。**O-14~O-19 为 v2.0 新增**。

| ID | 问题 | 性质 | 建议 / 已裁决 |
|---|---|---|---|
| **O-1** | **`GTD_BIND` 文档漂移**：专题文档与 memory 把它当配置变量，但代码全仓无此变量（实际是 `--host`/`HOST`/`GTD_HOST` + compose `127.0.0.1:` 端口映射） | 文档↔代码不一致 | ✅ **已裁决（2026-11-18）：采纳方案 ① —— 统一改用真实变量名并修订专题文档；不新增 `GTD_BIND` 别名、不改代码**。**已修订（复核证据）**：`GTD_BIND` 全仓核实仅存于文档/memory，代码零命中【代码·已核：`pyapp/config.py:87-88` 读 `--host`/`HOST`/`GTD_HOST`；`docker-compose.yml:146,153` 绑定前缀 `127.0.0.1:${GTD_BLUE_PORT:-8788}:8787` / `127.0.0.1:${GTD_GREEN_PORT:-8789}:8787` 写死】；历史文档 6 处已按本文档 §7.4/§6.4 口径修正（`docs/Docker部署方案.md:405` 已标注"代码中不存在 GTD_BIND 变量"；`README.md:93`、`.workbuddy/memory/MEMORY.md:26`、`01` §9.1 D-1 均已改为纠正说明）。TDS 内 §6.4/§7.4/§11 P0/§12 R-9 与本节均只作"历史文档名/漂移留档"，**无一处把 `GTD_BIND` 当真变量**，无需再改 |
| **O-2** | 版本串"四处"口径：实测还有 `.smoke/config.mjs` 镜像副本共 5 类落点 | 口径 | ✅ **已裁决（2026-11-18）：把 `.smoke/config.mjs` 的版本串同步纳入 G2 自动化断言**。**5 类落点实测**：`js/config.js:185` 的 `APP_VERSION`、各模块 `?v=`、`index.html:9,64` 的 css/js、`pyapp/__init__.py:20`、`.smoke/config.mjs:185` 镜像副本，现值均 `20261118a`【代码·已核：逐文件实读】。`version-contract.mjs` 实跑扫描 **17 个文件**（js/ 6 + `index.html` + pyapp/ 10）、**13 项断言全绿**【实测：node .smoke/version-contract.mjs → 11 项通过】；**注意缺口**：该脚本 `scanTargets` 不含 `.smoke/config.mjs`，第五类落点目前靠 `02` NFR-10 人工核对，**本次裁决即要求把它接入 G2 自动断言**（交 `07`），在此之前不得声称"5 类落点全自动覆盖" |
| **O-3** | 端点计数口径（**v2.0 重算**）：`ROUTES` 共 **24 条 `(method, path)` 条目 / 19 条唯一路径**（旧文档此处及 §17.4 D-5 误记为 22，见本行裁决的复核更正）—— 重复路径**四处**：`/api/admin/accounts`（POST+GET）、`/api/admin/cert`（GET+POST+DELETE）、`/api/logs`（POST 上报 + GET 读取）、`/api/llm/config`（GET + POST）；`/api/auth/me` 是 `/api/auth/profile` 的**别名**（不同路径、同一 handler `h_profile`） | 口径 | ✅ **已裁决（2026-11-18）：知会，按实测口径登记，`06` 据实更新**。**复核更正【代码·已核：`pyapp/httpd.py:1584-1610` 字典定义 + `python -c "from pyapp.httpd import ROUTES"` 运行时导入统计】**：`ROUTES` = **24 条 `(method, path)` / 19 条唯一路径**，不是旧文档写的 22 —— 重复的是 4 处（`admin/accounts` 2、`admin/cert` 3、`logs` 2、`llm/config` 2），旧口径只数了 `logs`/`llm/config` 两处、漏掉两条 admin 路径。与 `02` v1.3「唯一路径 **19** 不变」一致。**知会而非改代码**：`/api/auth/me` 保留（闸门白名单路径之一）；本行与 §17.4 D-5 的「22」同步更正为 19 |
| **O-4** | WorkBuddy sites 形态的 `GTD_TRUST_PROXY` 应取 0 还是 1 | 安全 | ✅ **已裁决（2026-11-18）：保持 `GTD_TRUST_PROXY=0`**。理由：sites 形态无法保证平台反代**覆盖** XFF，取 1 会让客户端伪造 `X-Forwarded-For` 换掉限流键、并给明文 HTTP 加 `Secure` 导致掉线（后果见 §6.4 配对表）。代码默认已是 0【代码·已核：`pyapp/config.py:107` `_env_bool('GTD_TRUST_PROXY', False)`；`httpd.py:461-463,625` 仅 `trust_proxy` 为真才读 XFF】。**仅 C 蓝绿反代形态**按 §6.4 取 1；sites 本机/A 直连维持 0，不因本次裁决改代码或编排 |
| **O-5** | R-5 双进程限速稀释是否需要在本期修（P1 建议把计数落库，但会与"读路径不带写"纪律冲突） | 安全/架构 | ✅ **已裁决（2026-11-18）：本期不修，接受风险并记入 `01` 风险清单（已由本版写入 `01` §6「已接受风险」小节）**。理由：计数落库会与"读路径不带写"纪律冲突，且实测确认稀释客观存在【代码·已核：`pyapp/security.py:163` `_ATTEMPTS={}` 为进程内字典、`:165 MAX_ATTEMPTS=10`；`tests/test_zerodowntime.py` 未覆盖跨进程限速】。蓝绿重叠期任一进程各计一半失败 → 门限被稀释，属**已知残差非新发现**；本期不引入共享计数，后续如要修须走 CR 并解决写放大 |
| **O-6** | Git 分支/提交规范（§7.2）目前是**建议** | 工程规范 | ✅ **已裁决（2026-11-18）：搁置，不写入总纲/任何文档，也不列为待办**。理由：本项目当前**不是 git 仓库**【实测：`git rev-parse --is-inside-work-tree` 返回 `fatal: not a git repository`】，§7.2 的分支/提交规范无适用对象；待将来纳入版本控制再另立规范。§7.2 现有"建议分支"一段保留为**条件性参考**（仅当届时启用 git 才生效），不作为本期欠账 |
| **O-7** | `app_logs` 清理周期与保留天数、`429/500/日志降级` 告警阈值 | 运维 | ✅ **已裁决（2026-11-18）：移交 `09` 定义指标与阈值表；TDS 仅留交接指向，不在此定额**。交接点见 §15（`§9.6 监控点 → 09 运维手册`、`§12.1 风险 → 09`）；`GET /api/logs` 的字段契约仍以 §9 为准，`app_logs` 数据源与降级事实见 §9/§12。具体保留天数、429/500 阈值以 `09` 为准，本文不重复定义以免再漂移 |
| **O-8** | `.smoke/` 云端专用脚本是否清理、是否要真机浏览器测试 | 沿用总纲 §12 | ✅ **已裁决（2026-11-18）：移交 `01` 统一裁决，TDS 不再单列**。裁决要点由 `01` §7「未验证清单」（真机浏览器/新机首部署无环境）与 §6 P3 承接；本版 `01` 已按"保留脚本 + 真机测试列为环境依赖阻塞"口径登记。TDS 侧仅保留本行作为裁决去向留档 |
| **O-9**（v1.1） | `GTD_ADMIN_EMAILS` 是否随 ADR-13 一并切到 `users.is_system_admin` | 架构/运维 | ✅ **已裁决：本期 OR 兜底** + **必须打弃用告警**；v1.2 删 env，删除判据 = 告警日志归零。**现状**：兜底与告警均已实现（`_warn_env_admin_fallback`，`httpd.py:1434-1450`），env 方案尚未删除 |
| **O-10**（v1.1） | 三个 CLI 是否强制 `must_change_password=1` | 架构/运维 | ✅ **已裁决：三个全置 1**；`--set-password` 踢会话保留，与置 1 同一次 commit。**现状**：已实现（`store.reset_password` 默认 `must_change_password=True`） |
| **O-11**（v1.1） | `GET /api/admin/accounts` 是否本期实现 | 排期 | ✅ **已裁决：本期做，P1**。**现状**：已实现（`h_admin_accounts_list`） |
| **O-12**（v1.1） | 旧页面命中已下线端点，是否保留 `410 Gone` + `SIGNUP_REMOVED` | 产品/契约 | ✅ **已裁决：不保留，纯 404，不新增码**。理由：410 是**永久性承诺**，发出去以后想复用该路径就成了破坏性变更 |
| **O-13**（v1.2） | 临时口令是否设 72h 有效期 | 数据/契约 | ✅ **已裁决：不做有效期、不加第三列**。`_MIGRATIONS` 只有 v1/v2（`schema_version`=2）；时效由"首登必须改密"闸门保证。**另注**：用 `users.updated_at` 推算不可行 —— 登录 rehash 会刷新它，期限会自我续期 |
| **O-14**（v2.0） | **`GET /api/logs` 前端无入口（G-1）**：需不需要在本期补？补的话放在哪个页面 | 产品/前端 | ✅ **已裁决（2026-11-18）：要补，但 P2 不是 P0**。理由：**REQ-055 的原意是"面向终端用户的功能必须可达"，而"可达"的手段取决于用户是谁** —— 对运维 `curl` 本就是可达到的方式，但系统管理员同时是终端用户，要他 SSH 进容器 curl 属"可达但很难受"，所以补。**但 P0 要留给错误信息**（REQ-039 的假空是**真 bug**：用户会以为数据丢了；而日志入口不是 bug，有替代路径）。范围**限定**：系统管理页只读区块（与证书管理并列）+ 只读最近 100 条 + `status`/`path` 子串过滤；**不做**实时刷新、导出、图表。**注意**：日志含"全部用户"的 IP 与路径，属实例级，入口只在系统管理员可见区域 |
| **O-15**（v2.0） | **`flock` 缺失时是否允许静默降级**（R-19） | 运维/架构 | ✅ **已裁决（2026-11-18）：不得静默降级，改为显式 warn + 要求人工确认**（典型实现：无 flock 时打印"未获得互斥保护，5 秒后继续"并等待确认后再走）。现状【代码·已核：`deploy/release.sh:217-223` —— `command -v flock` 为真时用 `flock -n 9 || die` 阻止并发，**但 flock 不存在时整个守卫被 `if` 跳过、无任何提示直接发布**，即 R-19 的静默缺口】。裁决理由：静默降级是本项目反复吃亏的模式；宁可显式告警并要求人确认，也不要无声地并发两次。落地交 `09` 改 `release.sh`，需补 G6 用例验证"无 flock 必告警" |
| **O-16**（v2.0） | **证书到期是否需要告警/自动续期** | 运维/安全 | ✅ **已裁决（2026-11-18）：至少做阈值告警（<30 天）；自动续期只对 ACME 路径有意义，手动上传证书不做自动续**。现状【代码·已核：`pyapp/tls_cert.py:211,225,498` 的 `describe()` 已返回 `daysRemaining`，`GET /api/admin/cert` 现场解析；前端 `render-test` `system:cert-constraints` 已有 30/7 天阈值展示】。缺的仅是"服务端定时检查 + 通知"：交 `09` 加 cron 检查 + `<30 天` 告警；ACME 路径由 Caddy 自带续期，手动证书到期只告警、到点提示运维重新上传，不伪造自动续期承诺 |
| **O-17**（v2.0） | **`data/logs/` 是否纳入 `deploy/backup.sh`** | 运维 | ✅ **已裁决（2026-11-18）：纳入 `deploy/backup.sh`，备份分级提一档**。理由：不纳入则灾难恢复后日志断档、事故无法复盘。现状【代码·已核：`deploy/backup.sh` 仅 `server.py --export` 整库 JSON 并 `chmod 700` 目录/`600` 文件，**未包含 `data/logs/` 目录**】。**敏感须知**：访问日志含全员 IP 与路径，属敏感数据 —— 备份分级随整库提一档（加密/留存期/访问控制与整库一致，目录 700 + 文件 600 已有，另需在 `09` 明确留存天数与授权查看人）。落地交 `09` 改 `backup.sh` 与备份台账 |
| **O-18**（v2.0） | **集成域（§16）的推进批次与 G1 时点** | 排期/架构 | ✅ **已裁决（2026-11-18）：按 §16.11.2 分三批推进** —— ① 只读 REST + 令牌（REQ-045/046/047/049/054/055）② MCP 只读（REQ-050/051）③ 写能力（REQ-048/052/053；B3 硬前置见 §16.11.2）。**开工前置依赖（本裁决的关键约束）**：先补齐 PRD 的 **BR-037~047 / EX-038~048**（及配套 AC）正文条目 —— `02` §4.3 注 3 明确"否则本域不得进入 G0"。该正文由**产品经理并行补写**（任务 #22，只改 `02`），本 TDS **只声明依赖、不代写正文**。依赖闭合前，§16 集成域保持 **【设计·未实现】**（代码 0 行 / 测试 0 条），不得写成已实现、不得作为任何门禁验收证据；批次①的 G1 时点以 PRD 正文补齐为前提另定 |
| **O-19**（v2.0） | **集成域与运行日志的界面归属**：放"设置 → 高级"、"设置 → 集成"，还是别处？ | 产品/UI | ✅ **已裁决（2026-11-18）：统一挂「系统管理页」，用分区块（证书管理 / 本地 API / MCP / 运行日志），不新建 settings 视图、不新增导航项**。三条理由：① 同属"实例级配置"；② 权限边界天然一致（都是 `_require_instance_admin`）；③ 少一个导航项就少一处"找不到"。PRD 的 REQ-045「设置→高级」与 REQ-050「设置→集成」两处表述已交产品经理按过期表述改掉 |

---

## 15. 与其他文档的追溯接口

| 本文档章节 | 交接到 | 内容 |
|---|---|---|
| §2.2 模块职责 / §2.3 写链路 | `03 FDS`、`06` | 页面/流程 ↔ 模块 ↔ 端点一一对应（G1） |
| §5.2 策略矩阵 / §5.3 K-1~K-6 / §5.8 | `02` 权限矩阵、`05` 数据模型、`08` 越权用例 | G0/G1/G5 判定依据 |
| §7.4 版本串 / §7.5 三处登记 / **§7.6 三类登记纪律** | `07` G2/G3 验收标准 | 自动化断言建议 + 改动清单 |
| §8 错误分类、`06` §2 错误码表 | `07` 测试用例、`js/api.js` `friendlyError` | 文案映射表驱动 |
| **§9 日志全章（字段规范 + `GET /api/logs` 契约）** | **`06`（新增接口章）、`09`（采集与告警）、`07`（TC-LOG-*）** | **v2.0 新增交接** |
| **§6.7 证书生命周期** | **`09`（部署 SOP、cron、属主、备份）、`08`（密钥泄露用例）、`07`（TC-CERT-*）** | **v2.0 新增交接** |
| §9.6 监控点 / §12.3 回滚 | `09` 运维手册、`07` G6 | 阈值、SOP、发布结论表 |
| §12.1 风险清单 | `01` 现状评估（工程风险）、`08`（安全）、`09`（运维） | 不越界重复定义 |
| §13 未验证项 / §14 开放问题 | `01` 第 6 节、主理人 | 需裁决/需服务器执行 |
| §4.6 / §4.7 / §5.5 / §5.6 | `06` §10（端点契约、18 行权限矩阵、闸门白名单、CLI 契约、`api.js` 增删）、`02` 附录 D、`10_CR-2026-001_账号体系变更.md`（REQ-040~044 / EX-026~035）、`05` §13（`_MIGRATIONS` v1/v2） | **CR-2026-001 的实现依据** |
| **§16 集成域（设计未实现）** | **`02`（补 BR-037~047 / EX-038~048）、`06`（新接口契约 + 错误码）、`05`（v3~v6 迁移）、`08`（令牌泄露与作用域越权用例）、`07`（TC-INT-*）** | **v2.0 新增；实现前不得作为验收证据** |
| **§17 追溯与验收** | **`07`、`01`** | **v2.0 新增** |

---

## 16. 集成域（本地 REST API + 内置 MCP）技术设计

> # ⚠️ 本章通篇状态：【设计·未实现】
>
> **仓内现状（2026-11-18 实测）**：`pyapp/`、`js/` 无 `api_tokens` / `mcp` / `ApiHandler` 任何命中；`ROUTES` 里没有 `/v1/*`；`db._MIGRATIONS` 只有 v1/v2。**本章的每一行都是待实现的设计，不得作为"已实现能力"引用，也不得作为任何门禁的验收证据。**
>
> 对应需求：**REQ-045 ~ REQ-055（11 条）**，见 `02` §4.3。
> 对应 ADR：**ADR-21（Proposed）**。
> **前置条件（不做则本域不得进入 G0）**：`02` §4.3 注 3 要求"BR-037~047 / EX-038~048 / AC-F-47~55 须在同一变更单中补齐"。**本文只交付技术设计，不代产品补 BR/EX**（§17.2 给出待补清单）。

### 16.1 范围与硬约束

| 维度 | 内容 |
|---|---|
| 做 | 本地 REST API（读 + 安全写）、内置 MCP（只读工具集 + 分级写）、令牌管理、审计 |
| **不做（W 档，写死）** | ❌ 独立 npm/Bun/npx MCP helper（REQ-054）❌ Node/第三方包作为运行前置 ❌ 远程隧道 / 公网暴露 / Cloud 模式 ❌ 独立的第二个 Python 进程 ❌ OAuth2/第三方应用授权（§14 O-18 重新评估条件） |
| 硬约束 1 | **进程内、纯标准库**（C-15）：只用 `http.server` / `json` / `hashlib` / `secrets` / `sqlite3` |
| 硬约束 2 | **只绑 `127.0.0.1`，且没有配置项能改**（§2.6 不变式 2） |
| 硬约束 3 | **默认关闭**，关闭时不创建 socket（§2.6 不变式 3） |
| 硬约束 4 | **权限逐行复用 `policy.py`**（REQ-047）：不得出现第二条权限实现 |
| 硬约束 5 | **令牌绑定既有 `users.id`**，不新建账号体系 |
| 硬约束 6 | **删除一律软删除**（REQ-052），不做物理 `DELETE` |

### 16.2 总体形态：同进程内的第二/第三监听器

```
┌──────────────────────── 同一个 Python 进程 ────────────────────────┐
│                                                                    │
│  ThreadingHTTPServer #1  (config.host:config.port)                 │
│    └─ Handler          → ROUTES      /api/*  + 静态资源   【已实现】│
│                                                                    │
│  ThreadingHTTPServer #2  (127.0.0.1:GTD_API_PORT=8790)             │
│    └─ IntegrationHandler(Handler) → API_ROUTES  /v1/*     【未实现】│
│                                                                    │
│  ThreadingHTTPServer #3  (127.0.0.1:GTD_MCP_PORT=8722)             │
│    └─ IntegrationHandler(Handler) → API_ROUTES  /mcp      【未实现】│
│                                                                    │
│  共享：同一个 Application（db_path / access_log / config）          │
└────────────────────────────────────────────────────────────────────┘
```

**为什么是"同进程三个 server"**（而不是"一个 server 加前缀"或"另一个进程"）：

| 方案 | 判定 | 理由 |
|---|---|---|
| 同进程 + 独立监听器（**选定**） | ✅ | 共享 `Application`（同一 `db_path`、同一 `access_log`）；"不可达"由**绑定地址**保证，而不是由路由判断保证；关闭时 socket 不存在 |
| 同进程 + 同一监听器 + `/v1` 路径前缀 | ❌ | 集成域会**暴露在公网入口**（Caddy 转发所有路径）。靠 Caddy 配置去屏蔽 `/v1` 是"配置正确性依赖"，一旦有人改了 Caddyfile 就全泄 |
| 另一个 Python 进程 | ❌ | 回到"跨进程不共享状态"（限速、配置）；多一套 PID/日志/健康检查；违反 C-15 的"进程内" |
| 独立线程 + 自己解析 socket | ❌ | 手写 HTTP 解析器 = 无谓的缺陷面；`ThreadingHTTPServer` 已够用 |

**最小侵入改造（4 处，都在 `Handler` 上）**：

| # | 改造 | 目的 | 影响面 |
|---|---|---|---|
| 1 | 新增类属性 `ROUTES_TABLE = ROUTES`，`_handle` 里把 `ROUTES.get(...)` 改为 `self.ROUTES_TABLE.get(...)` | 子类可换一张路由表 | **1 行**，主入口行为不变 |
| 2 | 新增类属性 `SERVE_STATIC = True`；`_handle` 的静态分支加 `if self.SERVE_STATIC:` | 集成域不托管静态资源（对 `/` 直接 404） | **1 行** |
| 3 | 新增 `_api_identity(conn)`（见 §16.3.4），子类在 `_handle` 的写前三关之后调用 | Bearer 鉴权 | 新增方法，主入口不调用 |
| 4 | `_log_access` 的记录里**追加**一个可选字段 `token_id`（仅当 `self._token_id` 非空时输出） | 审计可追溯"哪个令牌"，但不落令牌本体 | 1 行 dict 字面量 |

> 改造 1/2 是**为了让 `_dispatch` 的全部保证被继承**（request_id、每请求一条日志、写锁重试、`_body_consumed`、`_aborted_if_responded`、`_internal_error`）。**绝不允许**为集成域另写一套 `_dispatch` —— 那会立刻出现"主接口有重试、机器接口没有"这类不对称。

### 16.3 令牌模型（REQ-045 / REQ-047）

#### 16.3.1 令牌形态与存储

| 项 | 设计 |
|---|---|
| 形态 | `gtd_pat_` + `secrets.token_urlsafe(32)`（前缀让令牌**可被 grep 识别**，也便于将来上 secret scanner 时被认出来） |
| 存储 | **只存 `sha256(整个令牌字符串)`**，复用 `security.token_fingerprint()`（`security.py:133-135`）—— 与会话令牌同一纪律，库被拷走也无法反推 |
| 明文 | **只在创建响应里出现一次**；响应带 `Cache-Control: no-store`；**不写任何日志**（`_redact_path` 只管 query，所以纪律必须是"不打印 Authorization 头"，且 `_log_access` 记录里根本没有这个字段） |
| 绑定 | `user_id TEXT NOT NULL`（`users.id`）。**不建新账号体系**，令牌只是"以某人身份发请求的凭据" |
| 作用域 | `scopes TEXT`（JSON 数组）：**唯一一套五个值** `read`、`write:edit`、`write:archive`、`write:delete`、`mcp`（裁决 B3-D-01：废除 `mcp:write:*` 三串，全文不得再出现；详见下方勾选表与 §16.12）。**默认只读**：`["read"]` |
| 有效期 | `expires_at TEXT NULL`（`NULL` = 不过期，需创建时显式勾选）。**默认 90 天** |
| 撤销 | `revoked_at TEXT NULL` + `revoked_by TEXT`。**校验查询必须带 `revoked_at IS NULL`** → 撤销即时生效（每次请求读库，不做缓存） |
| 轮换 | `POST /api/admin/api-tokens/{id}/rotate`：**同一事务内**插入新行 + 把旧行 `revoked_at` 置为 now。**不给重叠宽限期**（简化语义；代价是调用方必须原子替换令牌，见 16.3.5） |
| 标签 | `label TEXT`（人读，如 "opswork 笔记本"），用于审计里区分"哪个令牌干了什么" |
| 最大数量 | 每账号 **10 个**（防"令牌海洋"导致撤销面不可控）；超限 409 `TOKEN_LIMIT_REACHED` |
| 与环境变量 | **不提供**"从环境变量读令牌"的入口（那会让令牌出现在 `docker inspect` / 进程环境里） |

**创建令牌时 UI 的 scope 勾选项（B3-D-01 裁决落地，详见 §16.12）**：

| 勾选项（写入 `scopes` 的值） | 语义 | 默认 | 说明 |
|---|---|---|---|
| `read` | 读权限 | ✅ 勾选 | REST 读端点 + MCP 只读工具（§16.8.2）的最低门槛 |
| `mcp` | MCP 传输**附加能力位** | ☐ 不勾 | **本身不含任何写权限**。MCP 写工具必须"`mcp` + 对应 `write:*`"同时具备才放行（两级门槛）；REST 端点不校验此位 |
| `write:edit` | 增 / 改 / 完成 / 恢复 | ☐ 不勾 | REST 与 MCP **共用同名 scope**；对应 `POST`/`PATCH /v1/work-items`、`complete`、`restore` 与 `update_*`/`complete_*`/`restore_*` |
| `write:archive` | 归档 | ☐ 不勾 | 对应 `POST /v1/work-items/{id}/archive` 与 `archive_work_item` |
| `write:delete` | 软删除 | ☐ 不勾 | 对应 `DELETE /v1/work-items/{id}` 与 `delete_work_item`（MCP 另需 `confirm: true`） |

> **默认组合 = `["read"]`**（不是 `["read","mcp"]`）：开箱只给 REST 与 MCP 的只读能力，`mcp` 传输位与三类 `write:*` 一律要显式勾选。理由：① 与 §16.3.1 作用域行、DDL 默认（`:1886`）、代码 `DEFAULT_SCOPES`（`pyapp/integration.py:31`）三处一致；② 满足 `08` TC-SEC-INT-24（同一套 scope 名、缺一不可），并彻底堵死"`mcp` 单 scope 解锁写"的越权路径（`08` :742）。

#### 16.3.2 表结构（§16.10 的迁移 v5）

```sql
CREATE TABLE IF NOT EXISTS api_tokens (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  token_hash   TEXT    NOT NULL UNIQUE,       -- sha256(明文)，唯一索引即"查令牌"的唯一入口
  user_id      TEXT    NOT NULL,
  label        TEXT    NOT NULL DEFAULT '',
  scopes       TEXT    NOT NULL DEFAULT '["read"]',
  created_at   TEXT    NOT NULL,
  created_by   TEXT,                          -- 谁创建的（可能与 user_id 不同：管理员代建）
  last_used_at TEXT,                          -- 300s 节流刷新，见 16.3.4
  expires_at   TEXT,                          -- NULL = 不过期
  revoked_at   TEXT,                          -- NULL = 有效
  revoked_by   TEXT
);
CREATE INDEX IF NOT EXISTS api_tokens_user_idx ON api_tokens (user_id);
```

**四条结构性纪律**：

1. **不进 `db.COLUMNS`** → 自动不进 `WRITABLE_TABLES` → `/api/rows` 的 `table` 参数触达不到它（与 `users`/`sessions` 同一手法，`db.py:40-48,408`）。
2. **必须进 `db.EXPECTED_TABLES`**（`db.py:425`）。漏了会怎样：`schema_present()` 用**子集**判断（`db.py:568`）→ 旧库仍被判"结构已建全"→ `init_db` 直接返回 → **这张表永远不会被建出来**，而测试的"多出来的表"断言会报错。这是 §7.6 表格的第一行。
3. **`token_hash` 唯一索引**：它是"用令牌查行"的唯一入口，也防止同一令牌被插两次。
4. **不进 `BACKUP_TABLES`**（`store.py:803`）：令牌是短暂凭据（同 `sessions` 的处理），迁移后重新签发即可；进备份会让"备份文件泄露 = 全部令牌泄露"。

#### 16.3.3 生命周期

```
创建（浏览器，实例管理员或本人）        使用（脚本）                  撤销/轮换
POST /api/admin/api-tokens            GET /v1/work-items            DELETE /api/admin/api-tokens/{id}
 ├ 校验收紧：只能给自己建                │ Authorization: Bearer gtd_pat_xxx     ├ revoked_at = now
 │   （实例管理员可代建，须记 created_by） │                                      └ 立即生效（无缓存）
 ├ 生成明文 + 只存 sha256              ├ sha256 → 查 api_tokens
 ├ 返回 {token: 'gtd_pat_...', ...}    │   WHERE hash=? AND revoked_at IS NULL
 └ Cache-Control: no-store            │   AND (expires_at IS NULL OR expires_at > now)
                                       ├ 取 user（含 is_system_admin / must_change_password）
                                       ├ must_change_password=1 → 403 PASSWORD_CHANGE_REQUIRED
                                       ├ scopes 校验（写操作必须有对应 scope）
                                       └ last_used_at 节流刷新（300s，bookkeeping_commit）
```

#### 16.3.4 鉴权函数 `_api_identity(conn)` 的设计

```python
# 设计稿（未实现）—— 与 _current_user 并列，汇入同一个 load_context
def _api_identity(self, conn):
    header = self.headers.get('Authorization') or ''
    if not header.lower().startswith('bearer '):
        raise ApiError(401, 'UNAUTHORIZED', '缺少 Bearer 令牌')       # 不区分"没给"与"给错"
    token = header[7:].strip()
    row = conn.execute(
        'SELECT t.id AS token_id, t.scopes, t.expires_at, t.revoked_at, t.last_used_at, '
        '       u.id, u.email, u.name, u.is_system_admin, u.must_change_password '
        'FROM api_tokens t JOIN users u ON u.id = t.user_id '
        'WHERE t.token_hash = ?', (security.token_fingerprint(token),)).fetchone()
    if row is None or row['revoked_at'] or (row['expires_at'] or '') <= db.now_iso():
        raise ApiError(401, 'INVALID_TOKEN', '令牌无效或已撤销')       # 三种失败同一个码
    user = {...}                                    # 与 _current_user 同构
    if user['mustChangePassword']:
        raise ApiError(403, 'PASSWORD_CHANGE_REQUIRED', '...')        # ★ 不许绕过闸门
    self._uid = user['id']                          # 让访问日志带上 uid
    self._token_id = row['token_id']                # 让访问日志带上 token_id（不是令牌本体）
    if db.seconds_since(row['last_used_at']) >= self.SESSION_TOUCH_SECONDS:
        try:
            conn.execute('UPDATE api_tokens SET last_used_at = ? WHERE id = ?', (db.now_iso(), row['token_id']))
            db.bookkeeping_commit(conn)             # 记账提交：不影响重放判定
        except sqlite3.OperationalError:
            pass                                    # 记账失败不影响请求
    return user, json.loads(row['scopes'] or '[]')
```

**六条设计要点**：

| # | 要点 | 理由 |
|---|---|---|
| 1 | 所有失败（无头/无效/过期/撤销）分别用 `UNAUTHORIZED` / `INVALID_TOKEN`，**但"无效/过期/撤销"三者同码** | 区分它们等于告诉攻击者"这个令牌曾经有效"；区分"没给"与"给错"有助于客户端排障，且不泄露秘密 |
| 2 | **必须查 `must_change_password` 并拦** | 否则令牌是 §4.7 闸门的免费旁路（§4.8 边界 3） |
| 3 | `last_used_at` **300s 节流** | 每个请求一次 UPDATE 会让**读接口也抢写锁**（与 ADR-09 纪律②同一问题）。复用 `SESSION_TOUCH_SECONDS` 这个既有常量，语义一致 |
| 4 | `bookkeeping_commit` 而不是 `commit` | 记账写入不能影响 `commit_attempted`，否则会白丢写锁重试机会（`db.py:545-556`） |
| 5 | `self._uid` / `self._token_id` 落到 handler 上 | `_log_access` 在响应之后才执行，那时局部变量已出作用域；这是"日志能带上身份"的唯一稳妥位置 |
| 6 | **不解码/不信任令牌里的任何自描述信息**（无 JWT） | 令牌是不透明随机串，权限全在库里 → 撤销即时生效（ADR-21） |

#### 16.3.5 令牌的残余风险（必须写清，不能靠设计消除）

| 风险 | 说明 | 缓解 |
|---|---|---|
| **令牌 = 账号在 API 面的全部权限** | REQ-047 要求"复用 `policy`"，所以令牌没有独立于账号的权限模型 —— 拿到 `read` 令牌 = 看到该账号能看到的**全部**团队数据（含个人域） | ① 默认只读 ② 默认 90 天过期 ③ 每账号 10 个上限 ④ 独立撤销 ⑤ `last_used_at` 可查"最近有没有被用" |
| **持有者可能是人，也可能是脚本配置文件** | 令牌常被写进 CI 变量、`.env`、脚本历史 | ① 前缀 `gtd_pat_` 便于扫描 ② 不写日志 ③ 文档要求"只放本机，不进版本库" |
| **轮换无宽限期** | 旧令牌立刻失效 → 可能造成调用方短暂中断 | 刻意的取舍：**"安全即时"优先于"零中断"**。需要零中断的调用方应自己先部署新令牌、再撤销旧的（两步操作，不是一次 rotate） |
| **无法限制"从哪个进程/机器"** | 本地接口没有客户端证书概念 | 只绑 `127.0.0.1` —— 攻击者必须先有本机执行权限，那时令牌已经不是主要问题了 |

### 16.4 路由规划与 `_dispatch` 复用（REQ-046）

#### 16.4.1 路由表（`API_ROUTES`，与 `ROUTES` 完全分离）

| 方法 | 路径 | 作用 | 最小 scope | 权限（在 `policy` 之后） |
|---|---|---|---|---|
| GET | `/v1/health` | 存活 + `version`/`build` + `capabilities` | **无（免令牌）** | — （只回 `{ok,version,build}`，**不回** `llmConfigured`/`storage` 等实例信息） |
| GET | `/v1/me` | 当前令牌对应的账号与 scopes | `read` | — |
| GET | `/v1/teams` | 可见团队 | `read` | `can_select('teams')` 逐行 |
| GET | `/v1/work-items` | 列表 + 筛选（`teamId`/`status`/`assignee`/`projectId`/`areaId`/`contextId`/`updatedSince`/`limit`） | `read` | `can_select('work_items')` 逐行 |
| GET | `/v1/work-items/{id}` | 单条（带 `ETag`） | `read` | `can_select` 单行；不可见 → 404 |
| POST | `/v1/work-items` | 创建（支持 `Idempotency-Key`） | `write:edit` | `can_insert`；`owner_id` 由服务端注入 |
| PATCH | `/v1/work-items/{id}` | 条件更新（**必须**带 `If-Match` 或 `expectedVersion`） | `write:edit` | `can_update`（含 `keep`） |
| POST | `/v1/work-items/{id}/complete` | 完成（等价 PATCH `status='done'` + `completed_at`，同一事务） | `write:edit` | `can_update` |
| POST | `/v1/work-items/{id}/archive` | 归档（写 `archived_at`） | `write:archive` | `can_update` |
| POST | `/v1/work-items/{id}/restore` | 从归档恢复 | `write:edit` | `can_update` |
| DELETE | `/v1/work-items/{id}` | **软删除**（写 `archived_at` + `status='archived'`） | `write:delete` | `can_update`，**且** `can_delete` 也须为真 |
| GET | `/v1/projects` / `/v1/areas` / `/v1/contexts` / `/v1/members` | 四类基础数据（只读） | `read` | 各自 `can_select` |
| GET | `/v1/search?q=` | 统一搜索（在**已可见**的行里做子串匹配） | `read` | 先 `can_select` 再匹配 |
| GET | `/v1/openapi.json` | 机器可读契约（由 `API_ROUTES` 生成） | `read` | — |

**令牌管理不吃 `/v1`**：签发/轮换/撤销放**浏览器面** `/api/admin/api-tokens`（`GET`/`POST`/`DELETE`），走 `_require_instance_admin` 或"本人"。理由：令牌明文要显示一次给人复制，那是**浏览器**的活；机器接口不应能签发新凭据（否则一个泄漏的令牌可以无限续期自己）。

#### 16.4.2 与 `_dispatch` 的关系（**不许另写一套**）

继承 `_dispatch` 得到的 7 项保证：

| 保证 | 来自 | 对机器接口的意义 |
|---|---|---|
| `request_id` 生成 + 与错误响应同号 | `_reset_access_state` / `_ensure_request_id` | 脚本报错时能把编号贴给运维 |
| **每请求恰好一条访问日志** | `_access_logged` 幂等闸门 | 机器流量同样可被统计（且**不会**把令牌写进日志） |
| 写锁 6 次退避重试 | `_dispatch` 的 for 循环 | 蓝绿切换期间机器写入也不会丢 500 |
| `_can_replay` 判定 | `db.TrackedConnection` | 不会重复创建工单 |
| **早退前消费 body** | `_discard_body` | 不会制造走私原语（对脚本同样重要） |
| `_abort_if_responded` | `send_header` 覆写 | 半截响应不会再补一份 |
| `_internal_error` 统一脱敏 | `_internal_error` | 机器接口同样不回 `str(error)` |

`IntegrationHandler._handle` 的差异只有三处（其余全部继承）：

```
① 不做静态资源分支（SERVE_STATIC=False）
② 写方法三关之后、调用 handler 之前：user, scopes = self._api_identity(conn)
③ 强制 Origin 检查：任何带 Origin 头的请求 → 403 CROSS_ORIGIN_BLOCKED
```

**③ 是本设计里最容易被漏掉的一条**：localhost HTTP 接口是**浏览器可达**的。任何网页都可以向 `http://127.0.0.1:8790/v1/work-items` 发 `fetch`（跨域读不到响应，但**请求已经发出去了** —— 如果服务端不做防护，这就是一个"任意网页可触发本机写操作"的缺口）。`fetch` 一定会带 `Origin`，而脚本不会。所以：

> **带 `Origin` 的请求一律拒绝**（不是"校验 Origin 与 Host 相同"，而是"出现就拒"）。这是与主入口**相反**的策略 —— 主入口的客户端是浏览器，集成域的客户端不是。

### 16.5 资源映射：复用 `policy` + `store`（REQ-047）

#### 16.5.1 三层映射（一层都不能少）

```
HTTP 请求  →  资源模型（本域）  →  store 函数（既有）  →  policy 谓词（既有）
/v1/work-items   work_items 表        select_table            can_select
                                      insert_row              can_insert
                                      update_row ★             can_update(using/keep/check)
                                      delete_row ★             can_delete
```

- **读接口**：`ctx = load_context(conn, user['id'])` → `store.select_table(conn, ctx, 'work_items')` → 在 Python 里应用筛选参数（与 `load_workspace` 同一手法）→ 分页截断。**不写 SQL**。
- **写接口**：**只允许**走 §16.6 新增的 `store.*_versioned` 函数（它们在内部调用 `can_*`，并把"版本校验 + version 自增 + 审计"放进同一次提交）。**不允许**在 handler 里拼 `UPDATE work_items` —— 那会绕开 `policy` 与 `split_payload`。
- **字段白名单/归一化**：必然复用 `db.split_payload`（`db.py:805-831`）→ 客户端传 `owner_id`/`created_at`/`version` 一律被丢弃（§16.6.3 会解释为什么这是**安全必需**，不只是"清理垃圾"）。

#### 16.5.2 与 ADR-08 的冲突及裁决

`store.update_row` 在"看不见/改不到"时返回 `[]`。**机器接口需要可区分的错误**，但**不许**改 `store` 的语义（会同时破坏前端 `assertAffected` 与 `test_14/15/20`）。裁决：

```
① 先判可见性：current = _fetch(...)；can_select('work_items', current, ctx) 为假 → 404 not_found
   （与"行不存在"同一响应 —— 不泄露存在性，也不给"无权限"这个可枚举信号）
② 可见但无写权：can_update(...) 为假 → 403 forbidden
③ 版本不符 → 412 task_version_conflict
④ 其余 → 200 / 201
```

**"可见性用 404、可写性用 403"** 这条划分是有意的：可见性属于"数据存在性"，泄露它有价值；可写性属于"角色能力"，告诉对方"你没这个权限"不泄露任何数据。

### 16.6 幂等键与版本冲突（REQ-048 / REQ-053）

#### 16.6.1 版本列（乐观锁）

| 项 | 设计 |
|---|---|
| 列 | `work_items.version INTEGER NOT NULL DEFAULT 1`（迁移 **v3**） |
| 语义 | **每次实际发生变更的更新 +1**；创建即为 1；软删除/归档 +1；恢复 +1 |
| 暴露 | `GET` 返回 `version` 字段 + 响应头 `ETag: W/"<id>-<version>"` |
| 校验 | 写请求必须带 `If-Match: W/"<id>-<version>"` **或** body 里的 `expectedVersion`；两者都给且冲突 → 400 `PRECONDITION_CONFLICT` |
| 缺失前置条件 | **428 `PATCH_PRECONDITION_REQUIRED`** —— 这是 REQ-053"**不得静默降级为无保护写入**"的落地方式。**不是默认放行**：宁可让脚本第一次调不通（然后去读文档加 `If-Match`），也不要让它在生产里默默丢更新 |
| 冲突 | **412 `task_version_conflict`**，响应体带 `{current: {...}}`（当前行 + 当前 version），**且不落库** |
| 快照过期 | `If-Match: *` 不支持（那是"不管版本"的语义，与本约束冲突）→ 400 |

**为什么不用 `updated_at` 当版本**：① 同一秒内的两次更新无法区分（`now_iso` 秒级，`db.py:438`）；② `updated_at` 会被"顺手刷新"的操作改写，语义不纯；③ 字符串比较的边界（`Z` 结尾、时区）容易写错。用整数版本列是最小面。

**跨域连带影响（ADR-07 的又一次体现）**：`work_items` 是双域共用表，所以个人事项与团队事项**同时**获得 version。这是好事（个人域也能安全写入），代价是 `load_workspace` 返回的行会多一个字段 —— 前端忽略即可（加法式变更）。

#### 16.6.2 `COLUMNS` 的扩展：新增 kind `'lock'`

版本列必须"可读、不可由客户端写"。但现有的 `split_payload` 会把 `COLUMNS` 里已知的字段**当作可写**（只有 `pk`/`owner`/`stamp` 会被丢弃）。而 `decode_row` 对未知列是**原样透传**（`db.py:734-752`），看起来"不登记也能读" —— 但 **`test_columns_spec_matches_real_schema` 要求 `COLUMNS` 的键集合等于真实列**（`tests/test_api.py:1275`），所以必须登记。

因此设计一个**最小扩展**：

```python
# COLUMNS['work_items'] 增加两行
'version':     'lock',     # 服务端维护：split_payload 丢弃、decode_row 原样返回
'archived_at': 'lock',     # B3-D-02 裁决：同 version，服务端维护，客户端不可写（见下方决定表）

# db.split_payload 的丢弃分支增加一个 kind（1 行改动）
if kind in ('owner', 'stamp', 'lock'):
    dropped.append(key); continue
```

| 决定 | 理由 |
|---|---|
| `version` 用新 kind `'lock'` | 若登记成 `'int'`，客户端就能在 `/api/rows` 里**直接写 version** → 可以人为制造 412、也可以把 version 改成任意值来绕过机器接口的乐观锁。**乐观锁的版本号必须只有服务端能改。** |
| **`archived_at` = `'lock'`（B3-D-02 最终裁决，取代本章先前两处互斥表述）** | 归档/软删除是**有权限语义的操作**（要过 `write:archive`/`write:delete` scope + `policy.can_update`），不能经通用 CRUD 触达；否则 `/api/rows` 的普通 update 就多一条绕过 scope 的归档路径，直接违反 §16.1 硬约束 4"权限逐行复用 `policy`、不许第二条路径"。**代码已按 `'lock'` 落地**（`pyapp/db.py:411` 登记 `'archived_at': 'lock'`；`:915` 丢弃分支含 `lock`；`store.py:351-352` 建行时服务端置 `NULL`）—— 裁决 `'ts'` 反而会与现有代码冲突。**前端实读核对**：`js/` 全目录无 `archived_at` 写入（见 §16.12），故本次裁决**不造成既有前端归档功能失效**；原"必须开 `CR-`"的判断前提（前端有走通用 CRUD 的归档按钮）经代码核对不成立，CR 的定性见 §16.12 与 §17.3。 |

#### 16.6.3 幂等键

```sql
CREATE TABLE IF NOT EXISTS api_idempotency (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     TEXT    NOT NULL,
  idem_key    TEXT    NOT NULL,          -- 客户端给的 UUID/字符串（≤128）
  method      TEXT    NOT NULL,
  path        TEXT    NOT NULL,
  request_hash TEXT   NOT NULL,          -- sha256(规范化 body)，判"同键不同请求"
  status      INTEGER NOT NULL,          -- 首次响应的状态码
  response    TEXT    NOT NULL,          -- 首次响应体（JSON 原文，≤64KB）
  created_at  TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS api_idempotency_uniq
  ON api_idempotency (user_id, idem_key);
```

| 场景 | 行为 |
|---|---|
| 首次带 `Idempotency-Key: K` 创建 | 正常创建 → **同一事务内**写入 `api_idempotency` → 201 |
| 重复带同一 `K`，**请求体相同** | 返回**存下来的首个响应**（含 201 状态码），不重复创建（`X-Gtd-Idempotent-Replay: 1`） |
| 重复带同一 `K`，**请求体不同** | **409 `idempotency_conflict`**（REQ-048 原码），不落库 |
| 无 `Idempotency-Key` | 正常创建（**不强制**幂等键 —— 强制会让所有简单脚本都要生成 UUID；但文档建议写操作都带） |
| 键作用域 | `(user_id, idem_key)`：**跨账号不共享**（否则 A 的键能读到 B 的响应体） |
| 保留期 | 24 小时；清理策略：**只在插入时顺手删过期行**（`DELETE ... WHERE created_at < now-24h LIMIT 100`），**不在读路径做清理** |
| 响应体大小 | 超过 64KB 不落幂等表（只回 `X-Gtd-Idempotent-Replay: 0` 并 warn）—— 避免幂等表成为第二个大对象存储 |

**`request_hash` 用规范化后的 body**（键序无关）：`json.dumps(payload, sort_keys=True, separators=(',',':'))` 再 sha256。理由：同一份 JSON 不同键序不应被当成"不同请求"。

#### 16.6.4 "未变更 PATCH 不推进版本"（REQ-048 第三句）

实现方式**复用既有语义**（`store.update_row` 在无可写字段时直接返回当前行且不提交，`store.py:184-185`）：

```
PATCH /v1/work-items/{id}  (If-Match: W/"7-3")
① 版本校验通过
② split_payload 归一化 → 丢弃未知/归属/时间戳/version/archived_at
③ 逐字段与当前行比较（按 COLUMNS 的 kind 归一后比较，避免 "1" vs 1 的假变更）
④ 全等 → 200 + 当前行 + version 仍为 3 + 不 commit + 不写审计（`X-Gtd-Unchanged: 1`）
⑤ 有差异 → UPDATE（含 version=version+1）+ 审计，同一 commit → 200
```

**第 ③ 步的比较必须按 kind 归一后做**：`encode_value` 会把 `"1"` 变成 `1`、`true` 变成 `1`；如果直接比原始值，`{"flagged": "true"}` 会被误判为"有变更"从而白白推进版本。

### 16.7 审计（REQ-049）

| 项 | 设计 |
|---|---|
| 落点 | `app_logs`（既有表），`scope='api'` / `scope='mcp'` |
| 事件 | `api.write`（每次成功的写，一条）；`api.denied`（权限/前置条件失败，一条）；`api.token.created` / `api.token.revoked`（令牌生命周期） |
| 字段 | `owner_id` = 令牌所属账号；`message` = `"{method} {path} → {table}#{id}"`（≤1000）；`detail` = `{tokenId, tokenLabel, method, path, table, rowId, versionBefore, versionAfter, result, scopesUsed}` |
| **绝不写入** | 令牌明文、`Authorization` 头、任何请求头、私钥、口令 |
| 事务 | **成功路径的 `api.write` 与业务写入同一事务、同一次 commit**（复用 `_insert_raw`，**不调** `store.write_app_log` —— 它内部自己 commit） |
| 失败路径 | 权限/版本失败时业务没写，审计是唯一写入 → 单独 commit（可接受：这里没有"错账"风险，因为业务确实没发生） |
| 谁能看 | **不能**通过 `app_logs` 的普通查询看（`select` 只给自己）；运维看 `GET /api/logs`（访问日志）或将来的实例级查询接口。**不许**为了"审计可见"放宽 `app_logs.select` |
| 与访问日志的分工 | 访问日志回答"**谁在什么时候请求了什么**"（含 401/403/404）；`app_logs` 审计回答"**这次写改了什么**"（含 version 前后值）。两者用 `request_id` 关联（审计 `detail` 里也写 `requestId`） |

**审计与业务同事务的账务意义**（与 §5.6 建号审计同一条理由）：分开写会产生两种错账 ——「改了但没审计」（可掩盖）与「审计了但没改」（可诬告）。而"哪个令牌改了哪一行"是本域唯一能追责的线索。

### 16.8 MCP 服务与工具集（REQ-050 / REQ-051 / REQ-052）

#### 16.8.1 传输与协议（**明确标注削减的部分**）

| 项 | 设计 | 状态 |
|---|---|---|
| 传输 | Streamable HTTP，**只支持 `POST /mcp` 的单次请求-响应（`application/json`）** | 设计 |
| JSON-RPC | 2.0，单请求单响应（**不支持 batch**） | 设计 |
| 会话 | **无状态**（不实现 `Mcp-Session-Id`） | 设计 |
| SSE 流 | **不实现**（`GET /mcp` 返回 405） | **削减，必须写进 `06`/`09`** |
| 鉴权 | 同一个 `Authorization: Bearer`（作用域需含 `mcp`） | 设计 |
| 端口 | 默认 `8722`，可配 1024~65535，**只绑 `127.0.0.1`** | 设计 |
| 生命周期 | 开关关闭 / 进程退出即停服（无独立进程可残留） | 设计 |
| 错误 | JSON-RPC `error.code` + `error.data.gtdCode`（复用既有错误码字符串） | 设计 |

**为什么削减 SSE 与 batch**：① 零依赖下手写 SSE + 会话管理是显著缺陷面；② 本域的工具都是"秒级返回"的本地 SQLite 查询，不需要服务端推送；③ 削减必须在契约里写明——**不写明的"不支持"会被当成 bug**。

#### 16.8.2 只读工具集（10 个，REQ-051）

| 工具名 | 参数 | 复用 | 说明 |
|---|---|---|---|
| `list_work_items` | `teamId?`, `status?`, `assignee?`, `limit?` | `select_table` | 只返回该账号可见的行 |
| `get_work_item` | `id` | `_fetch` + `can_select` | 带 `version` |
| `list_projects` | `teamId?` | `select_table` | |
| `get_project` | `id` | 同上 | |
| `list_areas` | `teamId?` | `select_table` | |
| `list_contexts` | `teamId?` | `select_table` | |
| `list_members` | `teamId?` | `select_table('team_members')` | 只回 `id/email/display_name/role/status`，**不回** `password_hash` 等（`team_members` 本来也没有） |
| `list_progress_logs` | `itemId?`, `limit?` | `select_table` | |
| `list_reviews` | `teamId?`, `kind?` | `select_table` | |
| `search` | `q`, `limit?` | 同 `/v1/search` | 先可见性再匹配 |

**明确"不支持"的能力（REQ-051 要求显式声明，不得伪造字段）**：

| 参照实现有、本项目没有 | 处理 |
|---|---|
| GTD「视图分区（view/availability）」类工具 | **不提供**，并在 `initialize` 的 `result.capabilities.gtd.unsupported = ["views","availability"]` 中声明。理由：视图分区是**前端渲染概念**（`js/views.js` 的 `render()` 分发），数据库里没有对应实体。硬造一个"views"工具等于返回编造的数据 |
| 提醒/通知、日历、附件 | 同上（无数据模型） |
| 多人协作的"分配给我"语义 | 用 `assignee_id == me` 表达，**不新增字段** |

#### 16.8.3 写工具与分级授权（REQ-052）

| 工具名 | 需要 scope（B3-D-01 裁决后） | 语义 | 前置条件 |
|---|---|---|---|
| `update_work_item` | `mcp` **且** `write:edit` | 字段更新（白名单同 PATCH） | 必须 `expectedVersion` |
| `complete_work_item` | `mcp` **且** `write:edit` | `status='done'` + `completed_at=now` | 必须 `expectedVersion` |
| `restore_work_item` | `mcp` **且** `write:edit` | `archived_at=NULL`（若原状态为归档） | 必须 `expectedVersion` |
| `archive_work_item` | `mcp` **且** `write:archive` | `archived_at=now` | 必须 `expectedVersion` |
| `delete_work_item` | `mcp` **且** `write:delete` | **软删除**（`archived_at=now` + `status='archived'`） | 必须 `expectedVersion` + 二次确认字段 `confirm: true` |

> **B3-D-01 裁决（scope 命名统一）**：旧表里的 `mcp:write:edit` / `mcp:write:archive` / `mcp:write:delete` 三串**作废** —— 它们不在 §16.3.1 的 scope 枚举里、全文无任何映射，照字面实现会让写工具永远 403。MCP 写工具改为**复用 REST 同一套 `write:*` scope**；`mcp` 降为"是否允许 MCP 传输"的附加能力位，**须与对应 `write:*` 同时具备**才放行；`mcp` 单独不授予任何写权限（堵死"默认只读令牌意外获得写权限"）。REST 写端点（§16.4.1）只校验 `write:*`，不吃 `mcp` 位。**同一令牌的 `write:edit` 在 REST 与 MCP 判定结果一致**，满足 `08` TC-SEC-INT-24（:822）与 V2 门禁（:841，全文无 `mcp:write:*`）。

**分级的三条纪律**：

1. **写工具默认全部拒绝**：令牌默认 `scopes=["read"]`（B3-D-01 统一：与 §16.3.1、DDL `:1886`、代码 `DEFAULT_SCOPES` 一致，旧表述 `["read","mcp"]` 作废），**不含 `mcp` 位、也不含任何 `write:*`**。要经 MCP 写须同时显式勾选 `mcp` 位与对应 `write:*`；走 REST 写只须 `write:*`。
2. **三类权限独立**：`edit` / `archive` / `delete` 分开授予 —— "能改标题"不等于"能删除"。**删除还额外要求 `confirm: true`**（防 LLM 幻觉式批量删除）。
3. **撤销即时生效**：scope 存在 `api_tokens.scopes`，每次请求读库 → 改 scope 或撤销令牌**无需重启**（REQ-052 最后一句）。

**没有"物理删除"**：`delete_work_item` 只写时间戳与状态。理由：机器写入的错误率高于人手点按钮，而物理删除在单机无审计表的架构里**不可恢复**。

#### 16.8.4 MCP 的安全约束（与 REST 同源）

- 只绑 `127.0.0.1`；带 `Origin` 的请求一律 403；无 CORS 头。
- `initialize` 响应**不泄露**实例信息（不回 `dbPath`/`llmConfigured`/管理员邮箱数量）。
- 工具列表按令牌 scope **裁剪**（只读令牌看不到写工具）—— 这是"最小惊讶"：客户端不会看到一个必然失败的写工具。
- 错误信息不回 `str(error)`（复用 `_internal_error` 的脱敏）。

### 16.9 界面可达性（REQ-055）

> **位置已裁决（O-19，2026-11-18）**：**统一挂「系统管理页」，用分区块呈现 —— 证书管理 / 本地 API / MCP / 运行日志**。**不新建 settings 视图、不新增导航项**。三条理由：① 同属"实例级配置"；② 权限边界天然一致（四个区块的门槛都是 `_require_instance_admin`）；③ 少一个导航项就少一处"找不到"。
> **PRD 的两处旧表述作废**：REQ-045 写的「设置 → 高级」与 REQ-050 写的「设置 → 集成」都过期了（已交产品经理按过期表述改掉）。

| 需要暴露的 | 位置（系统管理页 · 分区块） | 权限 | 说明 |
|---|---|---|---|
| API 启用开关 + 端口 | **本地 API 区块** | 仅实例管理员可见 | 端口占用 → 明确错误（`api_port_in_use`），**不是**"启动失败" |
| 令牌列表（label / 作用域 / 最近使用 / 过期时间 / 状态） | 本地 API 区块 | 仅实例管理员；本人可见自己的 | **不回显明文**，只显示前 8 位 + `…` |
| 创建令牌（一次性显示明文 + 复制按钮） | 本地 API 区块 | 同上 | 关闭弹窗后不可再取（只存 hash） |
| 轮换 / 撤销 | 本地 API 区块 | 同上 | 撤销需二次确认 |
| MCP 启用开关 + 端口 + **连接信息可复制** | **MCP 区块** | 仅实例管理员 | 连接信息 = `http://127.0.0.1:8722/mcp` + "在客户端填 Bearer 令牌" |
| 写权限三个开关 | MCP 区块 | 同上 | 分别对应 `write:edit`/`write:archive`/`write:delete` |
| **运行日志只读区块**（`GET /api/logs`） | **运行日志区块**（与证书管理并列） | 仅实例管理员 | **P2，不是 P0**（O-14 裁决）。范围：最近 100 条 + `status`/`path` 子串过滤；**不做**实时刷新、导出、图表 |
| **诚实反馈（硬要求）** | — | — | ① 端口被占用 → "端口 8790 已被占用，请换一个"（不是"启动失败"）② 令牌失效 → 401 文案"令牌无效或已撤销"③ 关闭状态 → 页面显示"未启用"，且**不显示**假的"运行中" |

**REQ-055 的验收判据（写进 `07`）**：任一开关/令牌操作，都必须能**从界面完成**，且失败时有明确原因。**但"可达"的手段取决于用户是谁** —— 对运维，`curl` 是可达到的方式；对"同时是终端用户"的系统管理员，界面才是（O-14 的裁决理由）。这条区分决定了"补什么、排多高优先级"，不要把它简化成"有接口就必须有页面"。

### 16.10 迁移与表结构（v3 ~ v6）

> 遵循 `_MIGRATIONS` 的既有纪律：版本号严格递增、全局唯一、永不重用、一个版本 = 一个意图 = 一个事务（`db.py:674-682`），执行外壳是 `BEGIN IMMEDIATE` + 二次判重 + 迁移 + 写版本号 + commit（`db.run_migrations`）。**没有 down 迁移**（`05` 的回滚 = 恢复备份）。

| 版本 | 意图 | 内容 | 幂等保护 | 兼容性 |
|---|---|---|---|---|
| **v3** | 工单乐观锁 | `ALTER TABLE work_items ADD COLUMN version INTEGER NOT NULL DEFAULT 1` | `_add_column_if_missing` | 加法式；旧代码 `SELECT *` 后 `decode_row` 原样透传（`db.py:734-752`）→ 不炸 |
| **v4** | 工单软删除 | `ALTER TABLE work_items ADD COLUMN archived_at TEXT` | 同上 | 同上；旧代码会把它当未知列透传 |
| **v5** | 令牌表 | `CREATE TABLE api_tokens` + 唯一索引 + user 索引 | `CREATE TABLE IF NOT EXISTS` | **必须同步 `EXPECTED_TABLES`**（否则旧库永远不建它，见 §7.6） |
| **v6** | 幂等表 | `CREATE TABLE api_idempotency` + 唯一索引 `(user_id, idem_key)` | 同上 | 同上 |

**四条登记清单（漏一条即失效）**：

1. `db.COLUMNS['work_items']` 增加 `'version': 'lock'`、`'archived_at': 'lock'`（§16.6.2）。
2. `db.split_payload` 的丢弃分支加 `'lock'`（1 行）。
3. `db.EXPECTED_TABLES` 增加 `'api_tokens'`、`'api_idempotency'`（4 个既有系统表之后）。
4. `store.BACKUP_TABLES` **不加**这两张表（同 `sessions`，`store.py:803`）。

**蓝绿发布顺序（expand/contract）**：

```
① 先发"只加列/只建表"的版本（v3~v6）→ 两槽位都能跑
② 再发"使用新列/新表"的版本 → 此时旧槽位仍在，但它不认识中文列也不影响（只是不写 version）
③ 回滚安全：旧镜像的 init_db 因 schema_present() 判 True 而不会重跑 DDL；多出来的列/表被旧代码忽略
```

> **一个必须承认的代价**：v3 之后新旧代码**同时**写 `work_items` 时，旧代码不会递增 `version` → 可能出现"version 没变但内容变了"。缓解：① expand/contract 间隔尽量短；② 机器接口的 `If-Match` 冲突本身就是保护（客户端会重读）；③ 文档写明"启用集成域前先把两个槽位都升到 v3+"。

### 16.11 未实现声明与验收批次

#### 16.11.1 声明（这一段是本章的唯一法律效力）

| 项 | 状态 |
|---|---|
| 代码 | **0 行**。`pyapp/` 无 `api_tokens` / `ApiHandler` / `mcp`；`ROUTES` 无 `/v1/*`；`_MIGRATIONS` 只有 v1/v2 |
| 测试 | **0 条**。本文 §12.2 的 `TC-INT-*` 全部待写 |
| 迁移 | **未写**。v3~v6 只是设计 |
| 界面 | **未写**。§16.9 是设计 |
| 可否作为验收证据 | **不可以**。任何"集成域已支持"的说法在实现前都是错误陈述 |
| 前置依赖 | `02` 的 BR-037~047 / EX-038~048 正文条目（`02` §4.3 注 3） |

#### 16.11.2 建议分批（与 §14 O-18 一致）

| 批次 | 需求 | 内容 | 门槛 |
|---|---|---|---|
| **B1 只读** | REQ-045 / 046（读部分）/ 047 / 049（读侧）/ 054 / 055 | 令牌表 + 鉴权 + `/v1/health`/`/v1/me`/列表/单条 + 审计骨架 + **系统管理页分区**（本地 API 区块：开关、端口、令牌管理；运行日志区块 P2） | B1 完成后可交付"脚本能读自己的数据" |
| **B2 MCP 只读** | REQ-050 / 051 | `/mcp` JSON-RPC + 10 个读工具 + capabilities 声明"不支持项" | 依赖 B1 的令牌与 `load_context` 装配 |
| **B3 写** | REQ-046（写部分）/ 048 / 049（写侧）/ 052 / 053 | `version`/`archived_at` 列 + `*_versioned` 函数 + ETag/If-Match/412 + 幂等表 + 写工具 + 三类 scope | 需 G5 安全复核（令牌泄露 + 作用域越权用例） |

**B3 之前的硬前置**：`08` 必须先给出"令牌泄露后的爆炸半径"结论与"作用域越权用例"清单 —— 因为 B3 一落地，"一个泄漏的令牌"就从"能读"变成"能改/能删"。

#### 16.11.3 B3 的验收断言（先写断言，再写实现）

| ID | 断言 |
|---|---|
| TC-INT-01 | 无 `Authorization` → 401 `UNAUTHORIZED`；错令牌 → 401 `INVALID_TOKEN`（两者响应体除 `code` 外不含其他可区分信息） |
| TC-INT-02 | 已撤销令牌 → 401；撤销**立即**生效（同一秒内的下一次请求即失败） |
| TC-INT-03 | `must_change_password=1` 的账号令牌 → 403 `PASSWORD_CHANGE_REQUIRED`（**闸门不许被绕过**） |
| TC-INT-04 | 只读令牌 `POST /v1/work-items` → 403 `INSUFFICIENT_SCOPE`，且**未落库** |
| TC-INT-05 | A 账号令牌读 B 团队数据 → 该行不出现在列表里；`GET /v1/work-items/{B的id}` → 404（不是 403） |
| TC-INT-06 | 带 `Origin: https://evil.test` 的请求 → 403 `CROSS_ORIGIN_BLOCKED`（即使令牌正确） |
| TC-INT-07 | `PATCH` 无 `If-Match`/`expectedVersion` → 428；带旧版本 → 412 且**库内未变** |
| TC-INT-08 | 同一 `Idempotency-Key` + 同 body 两次 → 只建一行，第二次返回首个响应；同 key 不同 body → 409 |
| TC-INT-09 | 未变更 PATCH → 200 且 `version` 不变、`X-Gtd-Unchanged: 1` |
| TC-INT-10 | 写成功后 `app_logs` 有且仅有一条 `event='api.write'`，`detail` 含 `tokenId` 与 `versionBefore/After`；**全表 grep 不到令牌明文与 `Authorization`** |
| TC-INT-11 | 访问日志里 `token_id` 存在而令牌明文不存在；`uid` 等于令牌所属账号 |
| TC-INT-12 | 关闭开关后 `netstat` 无 8790/8722 监听；进程内 `Application` 仍只有主 server |
| TC-INT-13 | `delete_work_item` 之后该行仍在库里（`archived_at` 非空），`GET /v1/work-items` 默认不含它 |
| TC-INT-14 | 集成域监听器不接受 Cookie 鉴权（带 `gtd_session` 但无 Bearer → 401） |

### 16.12 B3 设计缺陷裁决记录（B3-D-01 / B3-D-02 开工前；B3-D-03 / B3-D-04 开发期内同期）

> **性质**：`08_安全审计与上线门禁.md` §15.6（:829）给 B3 的"有条件放行"要求先由架构师裁决这两条 P1 并回写 `04`/`06`/`02`。本节是架构侧裁决的**唯一结论源**，覆盖本文件此前所有互斥表述。裁决人：架构师（高见远）；日期：2026-11-18；范围：16.12.1/16.12.2 两条 P1 为开工前裁决；B3-D-03/B3-D-04 两条 P2 属 B3 开发期内同期裁决，已在 16.12.3/16.12.4 于代码落地后补裁（原注"不在此处"随之作废）。**所有前端行为断言均来自实读代码，未改任何 JS。**

#### 16.12.1 B3-D-01：scope 命名空间分裂 —— 裁决：**否决 `mcp:write:*`，MCP 写复用 `write:*`，`mcp` 降为附加能力位**

**结论原文**：令牌 scope 只保留一套五个值 `read` / `write:edit` / `write:archive` / `write:delete` / `mcp`；`mcp:write:edit`/`mcp:write:archive`/`mcp:write:delete` 三串**全文作废**。MCP 写工具的准入改为"`mcp`（传输能力位）**且**对应 `write:*`（数据写权限）同时具备"；REST 写端点只校验 `write:*`。**默认组合 `["read"]`**（UI 只默认勾 `read`；`mcp` 与三类 `write:*` 默认不勾，须显式勾选）。

**理由（采纳主理人倾向，并核对 §16.8.4 后确认）**：
1. **字面实现会死锁**：`mcp:write:*` 三串不在 §16.3.1 枚举（:1870 本版）、`VALID_SCOPES`（`pyapp/integration.py:30`）与 DDL 默认（`:1886`）里，全文无映射；照 §16.8.3 旧表实现 → 合法写令牌仍永远 403。二选一的另一头（把 `mcp` 当万能写）会让默认只读令牌意外获得写权限 —— 两条路都不可接受，必须引入第二维。
2. **§16.8.4 已隐含两维**：该节"工具列表按令牌 scope 裁剪（只读令牌看不到写工具）"（:2225 本版）说明 scope 同时承担"工具可见性"与"操作准入"；把 `mcp` 定位成传输能力位、把 `write:*` 定位成数据权限位，正好让"能否连 MCP"与"能否写"正交，比再造三个复合 scope 的面更小。
3. **与既有契约/代码一致，改动最小**：§16.4.1 路由表（:1981-1986 本版）与 §16.9 UI 三开关（:2226 本版）本就用 `write:edit`/`write:archive`/`write:delete`；代码 `integration.py:679-694` 写路由也用同名 scope。统一到 `write:*` 只有 §16.8.3 一处需要改；若反过来统一到 `mcp:write:*`，要改路由表+UI+代码+`08` 的 TC-SEC-INT-24/V2 断言，代价与风险都更大。
4. **满足 G5 断言**：`08` TC-SEC-INT-24（:822）明确"同一令牌 REST 与 MCP 按同一套 scope 名判定、结果一致；不得 `mcp` 单 scope 解锁全部写"；V2 门禁（:841）要求全文无 `mcp:write:*` 混用。本裁决两条都满足：同一 `write:edit` 在两处同名，`mcp` 单给无任何写。

**对实现的影响**：① 创建令牌 UI 勾选项 = 上表五项，默认 `["read"]`；② MCP 写工具判定 = `('mcp' in scopes) and ('write:xxx' in scopes)`，缺一即拒（工具列表裁剪同样按此二维判据）；③ REST 写端点判定 = `('write:xxx' in scopes)`，不读 `mcp`；④ 校验 `VALID_SCOPES` 不放行 `mcp:write:*`（400 `INVALID_SCOPE`）。

#### 16.12.2 B3-D-02：`archived_at` 的 kind 同节矛盾 —— 裁决：**`'lock'`（客户端不可写）**

**结论原文**：`COLUMNS['work_items']['archived_at'] = 'lock'`，与 `version` 同级；客户端不得经 `/api/rows` 直接写 `archived_at`。归档/软删除唯一合法路径 = `store.*_versioned`（REST `/v1/work-items/{id}/archive`、`DELETE /v1/work-items/{id}`，或 MCP `archive_work_item`/`delete_work_item`）。同节旧代码块的 `'ts'` 与"用 `'ts'`（可写）"一行**作废**。

**理由（采纳主理人倾向，两条核对均已完成）**：
1. **决定表理由成立**：归档是有权限语义的操作，要过 `write:archive`/`write:delete` + `policy.can_update`；登记成 `'ts'` 等于开第二条路径，违反 §16.1 硬约束 4"权限逐行复用 `policy`、不许第二条路径"（:1818 本版）。`store.split_payload` 对 `lock` 一律丢弃（`pyapp/db.py:915-920`），是唯一能堵住旁路的手法。
2. **代码已经是 `'lock'`，裁决 `'ts'` 才会制造新缺陷**：`pyapp/db.py:411` 登记 `'version': 'lock', 'archived_at': 'lock'`；`db.py:409-410` 注释已写明"归档时间戳是有权限语义的操作，不能经通用 CRUD 触达（§16.6.2）"；`store.py:351-352` 建行时把 `archived_at` 置 `NULL`。§16.10 登记清单（:2245 本版）也写 `'archived_at': 'lock'`。即实现已按裁决落地，本裁决只是让文档追平代码。
3. **前端核对①——现有归档走什么路径（实读 `js/`，未改）**：
   - 工单列表的"丢弃"按钮 `data-status="archived"`（`js/views.js:510`）走 `case 'set-status'`（`js/app.js:927-933`）→ `updateItem(id, {status:'archived', updated_at})` → `updateRow('work_items', ...)`（`js/api.js:168`）→ `POST /api/rows`（`js/api.js:155-159`）。它**只写 `status`，不写 `archived_at`**。
   - 另有 `case 'quick-done'`（:906-925）、看板拖放（:2161-2164）同样只写 `status`/`completed_at`，不写 `archived_at`。
   - 名字含"归档"的 `archiveTeam`（`js/app.js:1468-1479`，由 `case 'archive-team'` :1021 触发）写的是 **`teams.archived` 布尔列**（`updateRow('teams', id, { archived: true, ... })` :1472），**不是** `work_items.archived_at`，与本裁决无关。
   - `js/` 全目录 grep `archived_at`：**零命中**（`updateItem`/`updateRow` 的 payload 由调用点决定，无一处带该字段）。
4. **前端核对②——定为 `'lock'` 后前端归档是否失效、走哪个端点（实读结论）**：**不失效**。因为现有前端从未写过 `work_items.archived_at`，把它从"可写"改成"丢弃"没有任何前端调用方受影响；`status='archived'` 的"丢弃"语义走 `/api/rows` 照常有效。§16.6.2 旧表与 §17.3 :2362 所称"前端归档按钮会被静默丢弃、必须开 CR"的**前提（前端有走通用 CRUD 写 `archived_at` 的按钮）经代码核对不成立**。将来若要把"丢弃"升级为真正的工单归档（写 `archived_at`），按 TDS 应走专用端点 `POST /v1/work-items/{id}/archive`（§16.4.1 :1984，scope `write:archive`，带 `expectedVersion`）或 MCP `archive_work_item`（§16.8.3），**不得**经 `/api/rows`。**CR 定性**：保留 `CR-` 登记（满足 `08` :829 放行条件），但内容从"修复既有前端破坏"更正为"前端归档能力增强（把 `status` 式丢弃升级为写 `archived_at` 的真归档），非阻塞项"；是否本期做由主理人/PM 定，见 §17.3。

**对实现的影响**：① 零代码改动（代码已是 `'lock'`）；② B3 实现者不得把 `archived_at` 登记为 `'ts'` 或加通用 CRUD 写路径；③ 验收直接复用 `08` TC-SEC-INT-17（:815，对 `/api/rows` 写 `archived_at` 必须被丢弃、库值不变）。

**尚未闭合的交接（架构侧不改这些文件）**：`06` 接口契约需按 B3-D-01 写清 MCP 写工具的 `mcp` + `write:*` 双条件与默认 `["read"]`；`02` PRD 需在 REQ-052 体现同一套 scope 名与"MCP 写须 transport 位 + 数据位"。详见本报告"需其它文档同步"清单，交主理人派单。

#### 16.12.3 B3-D-03：代建令牌的审计归属 —— 裁决：**追责链已闭合、`TC-SEC-INT-16` 判据满足，缺陷关闭；采纳 G5 建议第②条，零代码改动**

> 本条承接 `08` §15.4 B3-D-03（`08` :788）与 §15.5 `TC-SEC-INT-16`（:814）。G5 原判"审计 `detail` 没有 `created_by`，会追不到签发者"。裁决人：高见远；日期 2026-11-18；证据为 B3 代码落地后实读 `pyapp/` 与实测（非静态推断），行号以 2026-11-18 13:35 版 `pyapp/integration.py` 为准。

**结论原文**：管理员代建的令牌（`created_by` ≠ `user_id`）在审计里**可以追到签发者**，追责键是 `tokenId` —— `api.token.created` 记"谁签发"（`createdBy`=代建管理员、`userId`=被代建 owner、`tokenId`），`api.write` 记"谁执行"（`owner_id`=owner、`detail.tokenId`=同一令牌 id），两者以 `tokenId` 关联即还原"创建者=管理员、行动身份=owner"的完整链。`api.write.detail` **不改**、不加 `created_by`。G5 三条修复建议中**采纳第②条**（"代建动作写 `api.token.created` 并记操作者"）——该条已由实现满足；第①条（每条 `api.write.detail` 增 `created_by`）与第③条（禁止代建 `write:*`）**不采纳**。缺陷**关闭**，**零代码改动**。

**理由与代码证据（所有断言实读代码 + 实测）**：
1. **签发事实记在生命周期事件上，三条签发路径都记 `createdBy`**：普通创建 `api.token.created` 的 `detail={'tokenId','userId','createdBy': created_by, ...}`（`pyapp/integration.py:241-246`，`createdBy` 在 :244）；轮换新建 `detail` 含 `createdBy`: actor_uid 与 `rotatedFrom`（`pyapp/integration.py:298-303`）；代建入口 `admin_tokens_create` 把 `created_by=user['id']`（当前管理员 id）传入 `_create_token`（`pyapp/integration.py:358-359`）。即 G5 建议第②条**已落地**。
2. **写审计带关联键、不带签发者，是刻意分工**：`api.write` 的 `detail` 公共部分由 `_audit_context` 提供 `tokenId`（`pyapp/integration.py:79-87`，`tokenId` 在 :82），`store._audit_write` 只补 `method/path/table/rowId/versionBefore/versionAfter/result`（`pyapp/store.py:239-252`）；其 `owner_id=ctx.uid`（`pyapp/store.py:248`），`ctx.uid` 恰是令牌所属账号（`pyapp/integration.py:70-72` 的 `_ctx` 用 `handler._api_user['id']`，而 `_api_user['id']` 在鉴权时取自令牌所属用户 `pyapp/integration.py:165-169`）。所以"谁执行"=owner 无误，"谁签发"走 `tokenId` 关联查询，两者不混。
3. **轮换不丢签发链**：轮换新令牌的 `createdBy` 记为执行轮换者、`rotatedFrom` 指向旧令牌（`pyapp/integration.py:301-302`），旧令牌另有 `api.token.revoked`（:304-307）。沿 `tokenId`/`rotatedFrom` 可逐跳回溯签发历史。
4. **判据已实测通过**：`TC-SEC-INT-16`（`tests/test_security_integration.py:704-729`）实测：代建产生**恰一条** `api.token.created` 且其 `createdBy`=管理员、`userId`=owner（:712-716）；随后用该令牌写，`api.write.owner_id`=owner、`detail.tokenId` 与创建记录一致（:725-727）；并显式断言 `api.write.detail` 的 `createdBy` **不等于**管理员（:728），证明两类角色可区分。本人 2026-11-18 实跑该用例 **OK**（1 test）。
5. **不采纳第①条（api.write 加 created_by）的理由**：`created_by` 是令牌的**静态签发属性**，不是"这一次写"的执行者；复制进每条写审计会把"签发者"与"执行者"挤进同一语义位，且轮换后 `createdBy` 会变为轮换者（`pyapp/integration.py:302`），同一令牌在轮换前后会在 `api.write` 里显示两个签发者，反而制造歧义。记在令牌生命周期事件上、以 `tokenId` 关联，既唯一又精确，且无需改审计热路径（`_audit_context`/`_audit_write`）。
6. **不采纳第③条（禁代建 write:*）的理由**：追责链已闭合，无需用功能阉割替代审计；§16.3.3 生命周期明确"实例管理员可代建"，禁写会与该设计冲突，也削弱合法运维能力。

**对实现的影响**：① **零代码改动**，14/14 集成测试与 25 条 `TC-SEC-INT-*` 结论不变；② `06` 契约无需为"追责"加字段（`api.write` 字段表 `04` :2151 保持不变）；③ 追责查询口径（交 ops/G5）：`app_logs` 中按 `event='api.write'` 取目标行的 `detail.tokenId`，再查同 `tokenId` 的 `event='api.token.created'` 行，其 `detail.createdBy` 即签发者、`detail.userId` 即被代建 owner；轮换令牌沿 `detail.rotatedFrom` 上溯。

#### 16.12.4 B3-D-04：令牌 10 个上限的计数口径 —— 裁决：**口径 = "未撤销且未过期"的令牌数，满额轮换排除自身；判据满足，缺陷关闭，零代码改动**

> 本条承接 `08` §15.4 B3-D-04（`08` :789）与 §15.5 `TC-SEC-INT-25`（:823）。裁决人：高见远；日期 2026-11-18；证据为实读 `pyapp/integration.py`（2026-11-18 13:35 版）+ 实测。

**结论原文**：每账号 10 个上限的计数口径写死为 **`revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now)` 的行数**，即"当前真正可用的令牌数"；撤销与过期**都**释放名额（绝不采用"历史总数"口径，否则应急撤销/轮换会自锁）；轮换时**排除被替换的旧令牌自身**计数，并把"插新+撤旧"放在同一事务最后统一 commit，因此满额轮换既不假超限、也无"瞬时 11 个"中间态（G5 建议措辞为"先撤后插"，实现取等价的"插新撤旧同事务 + 统一 commit"，判据"不瞬时超限"同样成立，无需强行改序）。超限仍回 409 `TOKEN_LIMIT_REACHED`。此口径与 G5 修复建议一致，缺陷**关闭**，**零代码改动**。

**理由与代码证据**：
1. **口径已由代码写死**：`MAX_TOKENS_PER_USER = 10`（`pyapp/integration.py:33`）；计数集中在 `_active_token_count`（:209-223），SQL 为 `SELECT COUNT(*) FROM api_tokens WHERE user_id=? AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > ?)`，第二参数 `db.now_iso()`（:216-219）——撤销与过期双双排除。创建（:227）与轮换（:277）共用该函数，口径唯一。
2. **轮换排除自身，消除满额假超限**：`_active_token_count(..., exclude_id=token_id)` 追加 `AND id <> ?`（`pyapp/integration.py:220-222`、:277）；注释明写"满 10 个时待替换令牌若计入就会轮换必然假超限"（:213-214）。这正是 G5 担心的"应急令牌自锁死"的反向防线。
3. **轮换原子性**：插入新令牌（:288-294）与撤销旧令牌（:296-297）在同一事务，最后 `conn.commit()`（:309），不存在外部可见的 11 个有效令牌中间态；轮换审计同时写 `api.token.created`（带 `rotatedFrom`，:298-303）与 `api.token.revoked`（:304-307）。
4. **失败行为明确**：超限 409 `TOKEN_LIMIT_REACHED`（:228-230、:278-280），文案"每个账号最多 10 个有效令牌，请先撤销一个"，不含任何令牌明文。
5. **判据已实测通过**：`TC-SEC-INT-25`（`tests/test_security_integration.py:935-985`）实测：连签 10 个后第 11 个 409 且码为 `TOKEN_LIMIT_REACHED`（:941-949）、撤销后释放名额（:951-959）、满 10 个轮换成功（:961-970）、把所有未撤销令牌的 `expires_at` 改为过期后新签成功（:972-985）。本人 2026-11-18 实跑该用例 **OK**（1 test）。
6. **口径演进留痕**：B3 开工时 `04` §16.3.1（:1875）只写"每账号 10 个"，未定义计数维度，故 G5 登记 P2。早期实现版本仅有 `revoked_at IS NULL`（无过期过滤、轮换未排除自身）；与本节裁决对齐的**完整口径系 be-integration 于 2026-11-18 13:35 更新，本裁决核对时发现已就位**。本裁决以**当前实读代码**为准，并确认其正确。

**对实现的影响**：① **零代码改动**——当前实现已是裁决口径；② `06` 无需改错误码（409/`TOKEN_LIMIT_REACHED` 不变）；③ §16.3.1 的"10 个"按本节口径理解，不另改正文（留痕）；④ 运维口径：用户/管理员遇到 409 时，撤销或等待过期任一令牌即释放名额；轮换不受满额阻塞。

---

## 17. 本轮新增能力的追溯与验收

### 17.1 六项已实现能力的追溯落点

> 说明：**`02` v1.3（2026-11-18）补登记了三条「有代码、有测试、需求基线零记载」的能力**（REQ-056 TLS 证书管理 / REQ-057 版本化迁移层 / REQ-058 结构化访问日志 + 可查询接口），本节的"需求归属"已按 v1.3 回填。
> **仍未归位的两条**（v1.3 未登记，属同一类问题）：**发布锁 + inode 修复**、**请求体消费一致性** —— 已报主理人（见 §17.2 末尾"仍未归位"注）。

| # | 能力 | 本文落点 | ADR | 风险 | 测试 | 需求归属 |
|---|---|---|---|---|---|---|
| 1 | 结构化访问日志 | §9.1~§9.3、§9.5、§9.6 | ADR-15 | R-16/R-17/R-18 | `AccessLogTest`(10)、`AccessLogRedactionTest`(4)、`AccessLogDegradedTest`(2)、`AppLogReaderTest`(5) | **REQ-058**（v1.3 补登记，实现早于本版 TDS） |
| 2 | `GET /api/logs` | §9.4 | ADR-16 | R-16/R-18 | `test_get_logs_requires_admin`、`test_logs_endpoint_filters`、`test_logs_endpoint_does_not_fake_empty_result`（均属 `AccessLogTest`） | **REQ-058**（同一需求的第二个能力面） |
| 3 | HTTPS 证书管理 | §6.7、§6.5、§6.6 | ADR-17/18 | R-20/R-21 | `CertAdminTest`(13)、`CertDirMissingTest`(1) | **REQ-056**（v1.3 补登记） |
| 4 | 账号体系（CR-2026-001） | §4.6/§4.7/§5.5/§5.6/§5.7 | ADR-13/14 | R-12~R-15 | `PasswordChangeGateTest`(5)、`AccountAdminTest`(20)、`CliAccountTest`(10)、`NoAdminConfiguredTest`(2) | **REQ-040~044**（`10_CR-2026-001` 附录 D.1-#6） |
| 5 | 发布锁 + inode 修复 | §3 ADR-18/19、§12.3 | ADR-18/19 | R-19 | `test_zerodowntime.py`(9)【未验证·需服务器】 | ⏳ **仍待 `02` 归位**（v1.3 未登记；建议补一条 BACKFILL REQ） |
| 6 | 请求体消费一致性 | §8.5、§2.5 | ADR-20 | 协议走私 | `test_origin_403_discards_body_keepalive_stays_clean`、`test_unsupported_media_415_discards_body`、`test_unknown_api_post_discards_body` | ⏳ **仍待 `02` 归位**（v1.3 未登记；建议补一条 BACKFILL REQ） |

#### 17.1.1 BACKFILL 三条（REQ-056~058）的落点与状态

> **状态口径（重要）**：这三条是**已实现能力的补登记**，**不是本版设计的东西**。所以它们的状态是 **✅ 已实现**，而不是 §16 那种 `【设计·未实现】`。把两者写混会让读者以为"证书管理还没做"。

| REQ | 能力 | 本文落点 | 状态 | 关键事实 | 备注 |
|---|---|---|---|---|---|
| **REQ-056** | TLS 证书管理（上传/查看/删除 + 异步应用 + 私钥永不回显/落日志 + 到期阈值提示） | §6.7 全节、§6.5（密钥表）、§6.6 | **✅ 已实现**（14 条后端测试 + 前端 `render-test` 13 条 + `contract-test` 5 条） | 三端点 `GET/POST/DELETE /api/admin/cert`；`tls_cert.py` 四段校验；指纹版本化 + `.pending-apply` | 与 §2.6/§16.9 的"系统管理页分区块"一致 |
| **REQ-057** | 版本化迁移层（`schema_version` + 顺序迁移 + 失败回滚不 stamp + 两进程并发安全） | §3 ADR-09 追加条④、§3 ADR-13 代价 2、**§7.6 登记纪律**、§17.3 第 2/3 点 | **✅ 已实现**（`MigrationTest` 9 条） | `_MIGRATIONS` v1/v2；`BEGIN IMMEDIATE` + 抢锁后二次判重；`schema_present` 子集判断 | **§16.10 的 v3~v6 就是加在这个层上**（设计，未实现）—— 两条不冲突：层已实现，新迁移未写 |
| **REQ-058** | 结构化访问日志 + `GET /api/logs`（JSONL 9 字段 / 健康检查排除 / 脱敏 / 仅实例管理员可查 / 降级不挂） | §9 全章、§9.4、§9.8、§6.2 的 `log_file` | **✅ 已实现**（21 条日志测试） | `applog.py` 397 行；`fileLogging`/`degradeReason` 让空结果无歧义 | **REQ-049（开放接口审计，未实现）依赖 REQ-058**（`02` 注 4）：本版 §16.7 的审计设计正是建在它之上（`request_id` 串联） |
|  | **一处口径要对齐（提请注意）** | §9.8 G-1 / §14 O-14 | — | `02` v1.3 的 REQ-058 把"`GET /api/logs` 无 UI"登记为**「刻意无 UI」**（BR-026 允许的形态）；而主理人 2026-11-18 的 **O-14 裁决是"补，但 P2"** | 两者**不冲突**（"刻意无 UI"是当期形态，"P2 补"是后续计划），但**建议 `02` 那句加一个指向 O-14 的括注**，否则并列阅读会以为"永远不做 UI" |

### 17.2 集成域：需求 → 本文落点 → 待产品补的条目（REQ-045~055）

> `02` §4.3 注 3 明确：BR-037~047 / EX-038~048 / AC-F-47~55 / AC-A-47~52 / AC-R-12 为**预分配编号，正文尚未建立条目**，"须在同一变更单中补齐，否则本域不得进入 G0"。下表逐条给出**技术侧已就位的位置**与**还缺什么**。

| REQ | 技术设计落点 | 本文已给出的可验收事实 | 还缺（产品侧 / 其它文档） |
|---|---|---|---|
| REQ-045 本地 REST API 服务（默认关、只绑回环、令牌） | §16.2、§16.3、§16.4 | 三个监听器拓扑、默认关闭的"无 socket"语义、令牌生成/存储/轮换/撤销、`_api_identity` 设计 | BR-037、EX-038、AC-F-47、AC-A-47 正文；`06` 的 `/api/admin/api-tokens` 契约 |
| REQ-046 资源端点（work-items/projects/areas/contexts/members/search…） | §16.4.1、§16.5 | 16 条路由 + 权限映射表 + "读用 select_table、写用 `*_versioned`"的分层 | BR-038、EX-039、AC-F-48、AC-A-48；`06` 的 `/v1/*` 逐条契约（含查询参数与分页口径） |
| REQ-047 令牌绑定既有账号、逐行复用 `policy` | §16.3.1、§16.5、ADR-21 | "令牌 = 该账号本人权限面"的明确定义 + 三层映射 + 禁止第二套权限实现 | BR-039、EX-040、AC-F-49、AC-A-49；`08` 的"令牌爆炸半径"结论 |
| REQ-048 API 安全写入（ETag/412/409/未变更不推进版本） | §16.6 全节 | version 列、`'lock'` kind、If-Match/`expectedVersion`、428/412/409 三码、逐字段归一化比较 | BR-040、EX-041、AC-F-50；`06` 的请求/响应头清单（`ETag`/`If-Match`/`X-Gtd-*`） |
| REQ-049 开放接口审计（入 `app_logs`，令牌不明文落日志） | §16.7、§9.3 | 事件/字段/同事务纪律/"绝不写入"清单/与访问日志的分工 | BR-041、EX-042、AC-A-50；`09` 的审计查看方式（**注意：目前只能靠 `GET /api/logs` 或直连库**） |
| REQ-050 内置 MCP（默认关只读、8722、Bearer、可复制连接信息） | §16.8.1、§16.9 | 传输削减清单（无 SSE/无 batch/无 session）、端口规则、界面要素 | BR-042、EX-043、AC-F-51、AC-A-51；`06` 的 JSON-RPC 方法清单（`initialize`/`tools/list`/`tools/call`） |
| REQ-051 MCP 只读工具集（10 个，不支持项须声明） | §16.8.2 | 10 个工具 + **显式"不支持"清单**（views/availability 等） | BR-043、EX-044、AC-F-52；`06` 的工具 schema |
| REQ-052 MCP 写工具 + 分级授权（默认拒、三类独立、软删、撤销即时） | §16.8.3 | 5 个写工具 + 三类 scope + `confirm:true` + "无物理删除" | BR-044、EX-045、AC-F-53、AC-A-52；`08` 的作用域越权用例 |
| REQ-053 MCP 安全写入（`task_version_conflict` / `idempotency_conflict`，不得静默降级） | §16.6、§16.8.3 | 428（禁止无保护写入）+ 412 + 409 的完整语义 | BR-045、EX-046、AC-F-54 |
| REQ-054 零依赖/离线/本地自持不回退 | §16.1、§16.2、C-15 | "同进程三 listener + 纯标准库"的形态选择与四方案对比 | BR-046、EX-047、**AC-R-12**；验收时 `grep` 证明无第三方 import |
| REQ-055 配置可达 + 反馈诚实 | §16.9 | 六类界面要素 + 三条诚实反馈判据 | BR-047、EX-048、AC-F-55；`03 FDS` 的页面设计（**位置已裁决：系统管理页分区块，O-19**） |

> **仍未归位的两条已实现能力（提请注意，v1.3 未登记）**：本节表格覆盖的是集成域 11 条 + §17.1.1 的 3 条补登记。但实现层面还有两项能力**在 `02` 里没有任何 REQ**：
>
> | 能力 | 本文落点 | 测试 | 为什么建议补 |
> |---|---|---|---|
> | **发布锁 + inode 修复**（`flock` 互斥、`cat >` 原地覆盖、运行时上游断言） | §3 ADR-18/19、§12.3 | `test_zerodowntime.py`(9)【未验证·需服务器】 | 它是"切流从没生效过"那次事故的修正，属**发布正确性**；没有 REQ 意味着它是"谁都没要求的能力"，下一轮容易被当成"可选优化"删掉 |
> | **请求体消费一致性**（早退前 `_discard_body`、`_body_consumed` 语义） | §8.5、§2.5 | 3 条专项 | 它是**协议走私**的修正面，属安全加固；同样没有 REQ |
>
> 这两条与 REQ-056~058 属**同一类问题**（有代码、有测试、基线零记载），建议照 REQ-056~058 的做法补两条 BACKFILL。**我只报不代填**（PRD 属产品侧）。

### 17.3 本设计对既有代码的**破坏性影响**（三点，其中第 1 点已定为 B3 硬前置）

| # | 影响 | 为什么是破坏性的 | 处置（含已裁决） |
|---|---|---|---|
| **1** | `archived_at` 定为 `'lock'` → 前端**不能**再经 `/api/rows` 归档工单 | 现有前端若有"归档"按钮走通用 CRUD，会突然被**静默丢弃**（`split_payload` 丢未知字段**不报错**，`store.py:143`）。**"静默失效"比"报错"危险得多**：前端以为自己归档成功了、界面刷新后卡片还在，用户会**重复操作**。而且这条影响是**为了修另一个问题**（乐观锁不能客户端可写）而引入的，属典型"修 A 引入 B" | ✅ **已裁决（2026-11-18）：开 CR，但不在这一批；记为集成域 B3 的硬前置**（写进 `01` 待办：「集成域 B3 落地前必须先解决」）。CR 里必须给两个选项：**(a)** 归档走专用端点 `POST /api/items/{id}/archive`（推荐：与 `write:archive` scope 语义对齐）；**(b)** 允许前端经 `/api/rows` 归档，但加 scope 校验。**注意**：B3 落地前不许先把 `archived_at` 改成 `'lock'`。**【B3-D-02 裁决追记，见 §16.12.2】**：实读 `js/` 证实前端**没有任何**写 `work_items.archived_at` 的按钮（"丢弃"只写 `status`、`archiveTeam` 写的是 `teams.archived`），故上面"若前端有归档按钮会静默失效"的前提不成立、`'lock'` **不造成既有前端破坏**（代码也早已是 `'lock'`）。CR 保留登记以满足 `08` 放行条件，但**定性更正为"前端归档能力增强、非阻塞项"**，不再是 B3 开工的破坏性前置。若将来做真归档，走选项 (a) 专用端点 |
| 2 | `COLUMNS` 新增 kind `'lock'` | `db.split_payload` 的行为变化（多丢弃一类字段）会影响所有表的通用 CRUD 语义 | 属加法式（只对登记为 `'lock'` 的列生效），但仍需 `05` 记录 |
| 3 | `work_items` 加两列 → `load_workspace` 返回多两个字段 | JS 无类型检查，不会报错；但 `.smoke/render-test.mjs` 的夹具可能需要补字段 | 跑一遍三套 `.smoke` 即知；**不要**为了测试通过而在 `decode_row` 里过滤新列 |

**这三点说明**：集成域不是"加几个接口"，它**动了核心表与通用 CRUD 的语义边界**。这也是为什么 §16 全篇标"未实现"——先把影响面写清，再决定做不做。第 1 点更是"设计先行"的价值示例：**如果先写代码后写设计，这个静默丢弃会在上线后以"用户说归档没反应"的形式出现，而排查方向会指向前端。**

### 17.4 门禁影响

| 门禁 | 影响 | 说明 |
|---|---|---|
| **G0（需求）** | ⚠️ **阻塞** | 集成域缺 40 条正文规格（BR-037~047 共 11、EX-038~048 共 11、AC-F-47~55 共 9、AC-A-47~52 共 6、AC-R-12 共 1）。`02` §4.3 注 3 自己写着"否则本域不得进入 G0"，所以这不是"文档待完善"，是**开工前置条件**。本域当前状态：**设计齐备、规格不全、不得开工**。<br>**另一处欠账（性质不同，别混）**：REQ-056~058（补登记）也**没有** BR/EX/AC 正文（`02` 自述"在此之前本节三条不满足 G0"），但它们**已经实现并跑在生产代码里** —— 这属**需求侧欠账**，不是"功能未交付"。两者都不能作为"本期没做完"的证据，但都必须在 G0 前补齐 |
| **G1（设计）** | ✅ 本文即入参 | §16 的 ADR-21 状态为 **Proposed**，需拍板后才能进 B1 实现 |
| **G2（工程）** | ✅ 不受影响 | 无构建链变化；版本串已校正为 `20261114f` |
| **G3（联调）** | ✅ 不受影响 | 六项已实现能力的接口都在既有 `/api/*` 内 |
| **G4（测试）** | ⚠️ **有条件** | ① R-14 的"闸门遍历断言"仍未实现（v1.3 即列为必做）；② §8.5.4 三条协议级用例未写；③ §16.11.3 的 14 条 TC-INT-* 在实现前不存在 |
| **G5（安全）** | ⚠️ **有条件** | ① 证书/私钥路径需 `08` 复核（本文已给 R-20/R-21）；② 集成域 B3（写能力）落地前必须有"令牌爆炸半径"结论 |
| **G6（上线）** | ⚠️ **需补运维件** | ① `09` 缺"证书替换→touch 标记→验证指纹"的 SOP；② `09` 缺"备份三件套（库 + logs + certs）"；③ `deploy/run/certs` 属主要求（uid 10001）未写进 `09` |

### 17.5 v1.3 → v2.0 修正的「文档与代码不一致」清单（9 处）

> 这一节是本版最有实用价值的部分之一：**旧 TDS 里有 9 处说法已经与代码不符**。逐条给出"旧说法 → 实际"，避免有人照着旧文档排障。

| # | 旧 v1.3 的说法 | 代码实际 | 影响 |
|---|---|---|---|
| D-1 | §9.1：访问日志"`>=400` 才打印 `! METHOD path -> status`"，证据 `httpd.py:135-141` | 该实现**已删除**；现在是每请求一条 JSONL（`applog.py` + `_log_access`）；`log_message` 被整体置空 | **最严重**：按旧文档排障会找不到任何日志 |
| D-2 | §0 表：`APP_VERSION = '20260917a'` | `20261114f`（`pyapp/__init__.py:20`、`js/config.js:185`） | G2 版本串核对会对着错的值比 |
| D-3 | §0.4：行号基准 `httpd.py` 1105 / `store.py` 483 / `db.py` 667 | 1650 / 919 / 831 | 所有 `文件:行号` 引用都会落到无关代码上 |
| D-4 | §12.2：`tests/test_api.py` **49 项** | **156 个 `test_` 方法 / 20 个测试类** | 测试覆盖率被低估 3 倍；新增的证书/日志测试完全没被登记 |
| D-5 | §14 O-3：`ROUTES` **20 条 / 19 条唯一路径** | **24 条 / 19 条唯一路径**（v2.2 复核更正：旧表写「22 条唯一路径」是漏算 `admin/accounts`、`admin/cert` 两处重复；运行时导入 `ROUTES` 实测 19 条唯一路径，与 `02` v1.3 一致，见 §14 O-3）。当前比 v1.3 多出的条目：`/api/admin/accounts`（POST/GET）、`/api/admin/cert`（GET/POST/DELETE）、`GET /api/logs` 共 6 条；旧值 20 的构成已无法逐条复现（当时 `ROUTES` 里还有现在已删除的 `POST /api/auth/register`），故**以当前实测 24 条 / 19 条唯一路径为准** | `06` 的端点清单若照旧值写会漏条 |
| D-6 | §6.2：`GTD_ALLOW_SIGNUP`（默认 `True`）、`GTD_SIGNUP_CODE` 是有效开关 | **无任何行为**；只被 `ignored_signup_env` 检测并提醒（`config.py:90-100`） | 运维会以为"注册还开着只是没人用" |
| D-7 | §4.5/§4.6：改密与建号"由 `httpd.py` 的 `h_change_password` / `server.py:160-170` 拼 SQL" | 建号/重置已全部收敛到 `store.create_account`/`reset_password`；`server.py` 不再自己拼 `INSERT INTO users` | §5.6 的"唯一写入路径"现在是真的 |
| D-8 | §7.4：`?v=` 落点与 12 处计数（`js/views.js:16,21`、`js/api.js:19`…） | 落点已变（`js/api.js:22`、`js/app.js:16,17,25,30,34`…），仍是 12 处 js + 2 处 html，值 `20261114f` | 自动化断言若照旧行号写会瞎 |
| D-9 | §2.2 模块表只列 8 个后端模块 + 5 个前端模块 | 后端 **10 个**（+`applog.py`、`tls_cert.py`）；前端 **6 个**（+`js/util.js`） | `03 FDS` 的模块映射会缺两个模块 |

> **D-1 与其余 8 条不是同一类问题，必须单独记住**：
>
> - 其余 8 条是**行号漂移、数字过期**。读者跳到错位置会**自己发现**"这里不对"，然后重新找 —— 代价是时间。
> - **D-1 描述的机制根本不存在**。后果不是"找不到"，而是**"照着它排障会得出错误结论"**：
>
> ```
> 旧文档说：>=400 才打印 "! METHOD path -> status"
> 运维照做：docker logs gtd4teams-app-blue | grep '  ! '   → 空
> 运维结论：没有任何错误 → 服务没问题
> 真实情况：每条请求都记了（JSONL），只是格式变了；错误就在 app.jsonl 里
> ```
>
> 这与 `09` 把 `die` 写成 `warn` 是同一类缺陷：**文档描述的行为与代码相反，且读者无从察觉**。区别只在于方向 —— 这次是"漏看"（以为没日志），不是"误信"（以为已生效）。
>
> **防复发的一句话**：凡改动"输出通道"（日志去向、格式、级别、是否入库），必须同批更新 `04` §9 与 `09` 的检索示例；否则下一份文档会以一个**看起来合理但不存在**的机制，把排障引向反方向。

> 另有 3 处**不是漂移但需要更新**：① `js/api.js` 导出 27 → **30**；② `PolicyUpdate` 多了第三段 `keep`（v1.3 只写了 `using/check`）；③ `EXPECTED_TABLES` 的构成与"多表即失败"的测试断言（`tests/test_api.py:1289-1311`）在 v1.3 里没有登记。

### 17.6 本版交付边界（自我约束声明）

| 项 | 事实 |
|---|---|
| 新增文件 | **1 个**：`docs/process/04_TDS_技术设计说明书-update 11.18.md`（本文件） |
| 修改的既有文件 | **1 个，只加 1 行**：`01_现状评估与改善路线图-update 11.16.md` 的 P2 待办表新增第 11 行（`archived_at` 静默丢弃 = 集成域 B3 硬前置），依据是主理人 2026-11-18 的裁定"写进 `01` 的待办"。旧版 `04_TDS`、代码、其它文档**均未改** |
| 代码引用方式 | 逐条 `文件:行号`；不确定处用**内容锚点**（函数名/常量名），**没有编造行号** |
| 测试声明 | 所有 `【测试·有名】` 均给出类名或用例名；**本次一条都没跑**（U-10） |
| 未实现标注 | §16 全章 + ADR-21 + C-15 + R-22 + §17.2 相关行，**统一标 `【设计·未实现】`** |
| 行尾 | LF |

---

> **本文档状态**：**v2.1 定稿（内容层面已按主理人 2026-11-18 三项裁决定稿）**，等待 G1 评审（用户拍板）。
> **v2.1 做五件事（不改任何技术决策）**：① **O-14**：`GET /api/logs` 前端入口定为 **P2 并限定范围**（只读 100 条 + `status`/`path` 过滤，不做实时刷新/导出/图表），§9.8 G-1、§11、§14 三处同步；理由记档："REQ-055 的'可达'取决于用户是谁，P0 要留给错误信息"。② **O-19**：界面归属**统一为「系统管理页」分区块**（证书管理 / 本地 API / MCP / 运行日志），不新建 settings 视图、不新增导航项，§16.9 重写 + §16.11.2 B1 + §17.2 同步。③ **§17.3 第 1 点**：`archived_at` 的静默丢弃记为**集成域 B3 硬前置**（不在本批开 CR，CR 时给 (a) 专用端点 / (b) scope 校验两个选项），并写明"B3 落地前不许先把 `archived_at` 改成 `'lock'`"。④ **§17.5 补 D-1 的"性质不同"说明**：其余 8 条是漂移，D-1 描述的机制**根本不存在**，照它排障会得出**错误结论**（附可复现的错误推理链）。⑤ **按 `02` v1.3 对齐追溯口径**：REQ 55→**58**（REQ-056~058 为已实现能力补登记，标 ✅ 而非设计态），回填 §17.1 需求归属、新增 §17.1.1；并按类重算测试计数（`AccessLogTest` 8→10、`StorageSpecTest` 4→8，合计仍 156）。
> **v2.0 做了五件事**：① 补写本轮 6 项已实现能力的完整设计（§6.6/§6.7/§8.5/§8.6/**§9 整章重写**/§2.5/§2.6）；② 交付集成域 11 条需求的技术设计并**全程标注未实现**（§16，含令牌模型、路由规划、`policy` 复用、幂等与版本冲突、审计、MCP 工具集与分级授权、迁移 v3~v6、14 条验收断言）；③ ADR 从 14 条扩到 **21 条**（ADR-15~20 反向补录已实现决策，ADR-21 为 Proposed 的集成域决策，编号连续）；④ 全量重测行号并**修正 9 处文档与代码不一致**（§17.5）；⑤ 新增 §5.7/§5.8/§7.6/§10.5 四节"给未来的自己看的纪律"（实例级门槛单点、三处不可顺手优化、三类横切登记清单、降级必须有可观测出口）。
> **保留的部分**：§1 的 C-1~C-10、§2.1~§2.4、§3 的 ADR-01~14（含 ADR-04 的 v1.1 修订）、§4.1~§4.7、§5.1~§5.6、§6.1~§6.5、§7.1~§7.5、§8.1~§8.4、§10.1~§10.4、§11、§12.1~§12.6、§13、§14（O-1~O-13）、§15 的**全部有效内容**——只校正了行号与已失效的事实，未改动任何决策。
> **一句提醒**：本文件里的每一个"为什么不能改"（K-1~K-6、§5.8 三处、§11.1 十三条）都是**踩过之后写下来的**。要改它们，先开 `CR-`，再读一遍那一条的原文。
