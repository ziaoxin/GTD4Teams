# GTD4Teams · 环境重建指南

> 本包用于在一台新机器上从零重建 GTD4Teams 自托管环境。
> 生成时间：2026-11-18 · 版本串 `20261118b` · 线上构建标识 `20261118-140444`

---

## 0. 先读这段（避免踩坑）

1. **本包不含任何真实数据**：`data/`（SQLite 库，含口令散列、令牌哈希、会话）按安全纪律被排除。新环境从空库初始化，**不要试图从别处拷贝 `.db` 文件**——那会带入他人凭据。
2. **零依赖是硬约束**：后端只用 Python 标准库，前端原生 ESM。不要 `pip install` 任何包，不要引入构建链。
3. **三个路径是每台机器自己的状态**，绝不能从别处继承：`data/`、`deploy/run/`、`.env`。它们被 `.gitignore` 和本包同时排除。
4. **shell 脚本必须 LF 行尾**。在 Windows 上编辑过 `deploy/*.sh` 后，务必确认没有 CRLF——`set -Eeuo pipefail\r` 在 Linux 上会直接报 `invalid option name`，而 `bash -n` 查不出来（本项目真实踩过，导致一次发版在备份环节被拦）。

---

## 1. 环境要求

| 项 | 要求 |
|---|---|
| Python | 3.10+（实测 3.13），仅标准库 |
| 前端 | 现代浏览器；测试需 Node.js 18+（实测 22.22.1），仅测试用，运行不需要 |
| 部署机 | Linux（实测 Ubuntu 26.04 on WSL2）+ Docker 29.x + docker compose v2 |
| 网络 | 本地访问 `127.0.0.1:8787`；公网入口走 Caddy，只暴露 8080/8443 |

---

## 2. 本地开发（最快路径，不需要 Docker）

```bash
cd <解压目录>
python3 server.py --data-dir ./data --port 8787
# 浏览器打开 http://127.0.0.1:8787
```

创建第一个账号（自助注册默认关闭）：

```bash
python3 server.py --data-dir ./data --create-admin
```

或设置环境变量 `GTD_ADMIN_EMAILS=你的邮箱` 后，用该邮箱登录实例管理员能力。

**校验环境是否干净**：

```bash
python3 server.py --check
```

---

## 3. 生产部署（Docker + 蓝绿零停机）

```bash
cd <解压目录>
cp deploy/env.example .env
# 编辑 .env：设置 GTD_HTTP_PORT/GTD_HTTPS_PORT、GTD_ADMIN_EMAILS、
# GTD_SIGNUP_CODE（如开放注册）、GTD_TRUST_PROXY（反代后=1，公网直连=0）
sudo bash deploy/install.sh
```

`install.sh` 会做：生成自签名证书 → 创建具名卷 → 起 Caddy + 一个应用槽位 → 跑冒烟。**冒烟失败会中止**，不会打印"部署完成"。

日常发布 / 回滚：

```bash
sudo bash deploy/release.sh --status     # 查看槽位（需 docker 可用）
sudo bash deploy/release.sh              # 蓝绿发布：先备份 → 测试门禁 → 构建 → 切流
sudo bash deploy/rollback.sh             # 回滚到上一版镜像
sudo bash deploy/backup.sh /var/backups/gtd4teams 14
```

**发布门禁**：`release.sh` 会跑 L0 后端（156+14+25+6+6 项）与 L1 前端（39+13+103+107+233 项），任一红就中止，线上不动。

---

## 4. 测试

```bash
# 后端全量
python3 -m unittest tests.test_api tests.test_integration \
  tests.test_security_integration tests.test_dependencies tests.test_markdown_tables

# 前端全量（需要 Node.js）
for s in contract-test version-contract render-test auth-flow-test app-boot-test; do
  node .smoke/$s.mjs || break
done
```

当前基线：后端 **207 项**、前端 **440 项**，全部通过。

API / MCP 实跑演示（临时库，不碰线上数据）：

```bash
python3 .workbuddy/demo_integration.py
```

---

## 5. 版本串同步（改代码后必须做）

改任何前端代码后，以下 **8 个源码落点**必须同步递增到同一个值（形如 `20261118b`）：

```
js/config.js:185            APP_VERSION
pyapp/__init__.py:20        APP_VERSION
index.html                  css/js 的 ?v=（2 处）
js/api.js / app.js / auth-ui.js / views.js   import 的 ?v=
.smoke/config.mjs           测试夹具镜像副本
```

校验：

```bash
node .smoke/version-contract.mjs   # 13 项断言，18 个文件
```

注意：它只保证"各处同值"，**不保证"值已递增"**——递增靠发布纪律。本项目曾因内容改了但 `?v=` 没改，导致用户浏览器命中旧缓存，修复等于没上线。

---

## 6. 集成域（本地 REST + MCP）

默认**关闭**，且关闭时不创建 socket。启用方式：

```bash
# .env 中加入（只绑回环，没有配置项能改成 0.0.0.0）
GTD_API_ENABLED=1
GTD_API_PORT=8790
GTD_MCP_ENABLED=1
GTD_MCP_PORT=8722
```

重启后访问：

- REST: `http://127.0.0.1:8790/v1/health`（免令牌，只回 ok/version/build）
- MCP: `POST http://127.0.0.1:8722/mcp`（JSON-RPC 2.0，单次请求）

令牌管理在**浏览器面**（系统管理页），机器接口不能签发新凭据。scope 有两维：

- `read` / `write:edit` / `write:archive` / `write:delete` —— 数据权限
- `mcp` —— 传输能力位。MCP 写工具需 **`mcp` + 对应 `write:*` 同时具备**；REST 写只需 `write:*`

默认 `["read"]`，只读。

**安全要点**：令牌只存 SHA-256、明文只在创建响应出现一次、不进日志/备份；带 `Origin` 头的请求一律 403；只接受 Bearer，不认 Cookie。

---

## 7. 目录说明

| 路径 | 内容 |
|---|---|
| `server.py`、`pyapp/` | 后端，零依赖 Python 标准库 |
| `index.html`、`js/`、`css/` | 前端，原生 ESM，无构建链 |
| `tests/` | 后端测试（含安全与集成域） |
| `.smoke/` | 前端测试与版本契约 |
| `deploy/` | Docker、Caddy、蓝绿发布/回滚/备份脚本 |
| `docs/` | 需求、设计、测试、安全、运维全套过程文档 |
| `README.md` | 产品与操作说明 |

---

## 8. 已知非阻塞事项

1. `06 接口契约` / `02 PRD` 未逐字写入 scope 双维裁决文案（`04` §16.12.1 是唯一结论源）；代码、测试、全仓文本均无 `mcp:write:*` 残留，无口径冲突。
2. G-03 公网域名与 A 记录是环境依赖，不在代码包内。
3. `archived_at` 已登记为 `'lock'`，前端不能经通用 CRUD 写该列；将来页面要直接归档须走 `/v1/work-items/{id}/archive` 并开变更单。
