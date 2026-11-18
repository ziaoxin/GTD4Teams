# GTD4Teams · 自托管多团队 GTD 工作台

[English](README_EN.md) · [环境重建指南](doc/REBUILD.md)

面向**小型 IT 运维团队**的多团队 GTD 工作台：团队共享空间负责派活、巡检、值班与资源台账；个人空间用 GTD 五步（收集 → 厘清 → 组织 → 执行 → 复盘）跟进本人工作。方法论融合 ITIL 轻量化票种（事件 / 请求 / 变更 / 问题）、Kanban（显式流程与 WIP 限制）和 GTD。

**零依赖、不依赖任何云平台**：账号、数据、行级权限、AI 通道全部自持。只要有 Python 3，复制到任意目录即可运行——不需要 `pip install`，不需要 Node，不需要构建步骤。

---

## 快速开始

```bash
python3 server.py
```

打开终端打印的地址（默认 <http://127.0.0.1:8787/>）即可使用。

> 必须通过 HTTP 访问，不要直接双击 `index.html`：直接打开文件没有服务端，所有接口不可用（页面会明确提示）。

| 前提 | 要求 |
|---|---|
| Python | 3.8+（仅标准库；开发测试在 3.13 完成） |
| 第三方包 | **无** |
| Node / 构建工具 | **无**（原生 ES 模块，由 Python 直接托管） |
| 数据库 | **无外部依赖**（SQLite 单文件） |
| 网络 | 可完全离线 |

---

## 常用命令

```bash
python3 server.py                        # 启动（127.0.0.1:8787，数据落在 ./data）
python3 server.py --port 9000            # 换端口
python3 server.py --host 0.0.0.0         # 允许局域网访问
python3 server.py --open                 # 启动后打开浏览器
python3 server.py --no-signup            # 关闭自助注册
python3 server.py --signup-code ops2026  # 注册口令
python3 server.py --check                # 自检
python3 server.py --export backup.json   # 整库导出（含口令散列，注意保管）
python3 server.py --import backup.json   # 整库还原
python3 server.py --set-password you@example.com  # 重设密码
```

数据目录可用 `--data-dir` 或 `GTD_DATA_DIR` 指向别处。

---

## 核心能力

- **双域共用一张 `work_items`**：`team_id IS NULL` 是个人 GTD 事项；非空是团队工作项。统一状态机 10 态，个人视角只换标注。
- **权限在服务端收敛**：`pyapp/policy.py` 逐表实现 select / insert / update.using / update.check，是唯一安全边界；前端不做归属过滤。
- **强制改密闸门**：初始口令账号首登只能改密码，业务界面一次都不渲染。
- **TLS 证书管理**：上传 / 查看 / 删除，异步应用 + 指纹断言；私钥只存服务端、明文不回显、不落日志。
- **结构化访问日志**：JSONL 9 字段，路径自动脱敏，`/api/health` 排除。
- **本地集成域（REST + MCP）**：默认关闭、只绑回环；令牌只存 SHA-256，scope 分数据位（`write:*`）与传输位（`mcp`）。

### 集成域示例

```bash
# 启用（写入 .env 后重启）
GTD_API_ENABLED=1
GTD_API_PORT=8790
GTD_MCP_ENABLED=1
GTD_MCP_PORT=8722

curl http://127.0.0.1:8790/v1/health
# {"ok":true,"version":"20261118b","build":"..."}
```

令牌在系统管理页签发；带 `Origin` 的请求一律 403，只认 Bearer 不认 Cookie。

---

## Docker 与蓝绿发布

```bash
cp deploy/env.example .env
sudo bash deploy/install.sh          # 首次安装（冒烟失败会中止）
sudo bash deploy/release.sh --status # 查看槽位
sudo bash deploy/release.sh          # 零停机发布
sudo bash deploy/rollback.sh         # 回滚
sudo bash deploy/backup.sh /var/backups/gtd4teams 14
```

拓扑：`app-blue` / `app-green` 双槽位 + Caddy 常驻入口。切流是重写 Caddy 上游顺序后 `caddy reload`，不重启容器；发布前核对构建标识，避免"端口通但跑着旧版"。

---

## 测试

```bash
# 后端 207 项
python3 -m unittest tests.test_api tests.test_integration \
  tests.test_security_integration tests.test_dependencies tests.test_markdown_tables

# 前端 440 项（需 Node.js）
for s in contract-test version-contract render-test auth-flow-test app-boot-test; do
  node .smoke/$s.mjs || break
done
```

`release.sh` 已把上述全部纳入发布门禁，任一红就中止，线上不动。

API / MCP 端到端演示（临时库，不碰线上数据）：

```bash
python3 .workbuddy/demo_integration.py
```

---

## 目录

| 路径 | 内容 |
|---|---|
| `server.py`、`pyapp/` | 后端（零依赖 Python 标准库） |
| `index.html`、`js/`、`css/` | 前端（原生 ESM，无构建链） |
| `tests/`、`.smoke/` | 后端与前端测试 |
| `deploy/` | Docker、Caddy、发布 / 回滚 / 备份脚本 |
| `doc/` | 需求、设计、测试、安全、运维全套过程文档 |
| `doc/REBUILD.md` | 换机器重建环境的步骤与注意事项 |

---

## 安全须知

- 本仓库**不含任何真实数据**：`data/`（SQLite 库、口令散列、令牌哈希）、`.env`、`deploy/run/` 均未提交。新环境从空库初始化。
- `deploy/*.sh` 必须是 **LF 行尾**。Windows 上编辑后若变成 CRLF，`set -Eeuo pipefail\r` 在 Linux 会报 `invalid option name`，而 `bash -n` 查不出来。
- 改前端代码后，版本串 8 个源码落点必须同步递增，并用 `node .smoke/version-contract.mjs` 校验。该测试保证"各处同值"，递增本身靠发布纪律。

---

## 文档

完整设计与验收依据见 [`doc/`](doc/)；入口是 [`doc/REBUILD.md`](doc/REBUILD.md) 与 [`doc/process/README.md`](doc/process/README.md)。
