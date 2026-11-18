# 团队 GTD 工作台 · Docker 部署与调度方案

> 版本：v1（2026-09-28）
> 适用：把 GTD4Teams 部署到一台自有 Linux 服务器（阿里云 ECS / 其它 VPS）长期运行
> 本文中的「调度」包含两层：**服务调度**（编排、自愈、开机自启）与**定时任务调度**（备份）

---

## 一分钟上手

服务器上已装好 Docker 的前提下：

```bash
# 1) 把代码放到服务器（任选一种）
#    a. 本机上传：scp -r GTD4Teams user@服务器:/opt/gtd4teams
#    b. 或服务器上 git clone

# 2) 一键部署
cd /opt/gtd4teams
sudo bash deploy/install.sh
```

脚本会校验环境、生成 `.env`（含随机注册口令）、构建镜像、启动容器，
并等到健康检查通过才返回。**幂等**，重复执行不会覆盖配置、不会动数据。

---

## 一、整体设计

### 1.1 为什么是「单容器」

这不是偷懒，而是这个应用的形态决定的：

| 组件 | 是否需要独立容器 | 理由 |
|---|---|---|
| 应用进程 | — | 纯 Python 标准库，零第三方依赖，没有 requirements.txt |
| 数据库 | **否** | SQLite 单文件，不是需要独立进程的 DBMS |
| 缓存 | **否** | 没有 Redis 类需求 |
| 反向代理 | **可选** | 有域名才需要（Caddy，见第八节） |

好处是运维面极小：**升级 = 重建一个容器，备份 = 导出一个 JSON，迁移 = 搬一个卷**。

**什么时候该换架构**（诚实说明边界）：需要多实例水平扩展、或并发写入者很多时，
SQLite 的单写入者模型会成为瓶颈，那时应换成 PostgreSQL 之类的独立数据库。
就"小型 IT 运维团队"这个使用场景（QPS 极低、写入稀疏）而言，远未到那个量级。

### 1.2 运行时结构

```
              公网
               │
      ┌────────┴─────────┐
      │ 云安全组放行 443  │   ← 必须手动做，否则外面访问不到
      └────────┬─────────┘
               │
        ┌──────▼───────┐        可选：Caddy（--profile tls）
        │    Caddy      │       自动申请/续期 HTTPS 证书
        └──────┬───────┘
               │ 容器网络内 app:8787
    ┌──────────▼───────────┐
    │   gtd4teams 容器      │  非 root(uid 10001)
    │   python server.py    │  只读根文件系统 / 无 capability
    └──────────┬───────────┘
               │
        ┌──────▼────────┐
        │ 卷 gtd-data   │  /data/gtd4teams.db（唯一有状态的东西）
        └───────────────┘
```

**关键点：唯一有状态的东西是那个卷。** 容器本身随时可以删掉重建。

### 1.3 镜像里有什么、没有什么

| 有 | 没有 |
|---|---|
| `server.py` + `pyapp/`（后端） | `data/`（本机数据库，含口令散列） |
| `index.html` + `js/` + `css/`（前端） | `tests/`、`.smoke/`（测试与冒烟脚本） |
| 非 root 用户、健康检查 | `docs/`、`.workbuddy/`（文档与项目笔记） |
| | `deploy/`、`.env`（部署脚本与密钥） |

采用**白名单式 COPY**：只有上面「有」那一列会进镜像。
镜像里不存在的东西，就不存在被静态服务误暴露的可能 —— 这也是本项目的既有安全纪律。

---

## 二、前置条件

| 项 | 要求 | 检查命令 |
|---|---|---|
| 操作系统 | Ubuntu 20.04+ / Debian 11+ | `lsb_release -a` |
| Docker | 20.10+ | `docker --version` |
| Compose | v2（`docker compose`） | `docker compose version` |
| 磁盘 | ≥ 2 GB 可用 | `df -h /` |
| 内存 | ≥ 512 MB 可用 | `free -m` |

若尚未安装 Docker：

```bash
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker
```

---

## 三、部署

### 3.1 上传代码

推荐用 tar 管道，顺带把本机数据与开发用目录排除掉：

```bash
cd "D:/WorkBuddy/工作空间/代码开发/GTD4Teams"
tar --exclude=./data --exclude=./.workbuddy --exclude=./.smoke \
    --exclude=./__pycache__ --exclude='*.pyc' \
    -czf - . \
  | ssh user@服务器 'mkdir -p /opt/gtd4teams && tar -xzf - -C /opt/gtd4teams'
```

这条命令是幂等的：重复执行只覆盖同名文件，服务器上已生成的 `.env` 不受影响。

若习惯用 scp，注意目录语义 —— 目标目录已存在时，`scp -r` 会在其下**再建一层同名目录**，
所以要用 `/.` 的形式把内容摊平：

```bash
ssh user@服务器 'mkdir -p /opt/gtd4teams'
scp -r "D:/WorkBuddy/工作空间/代码开发/GTD4Teams/." user@服务器:/opt/gtd4teams/
```

> **不要把本机的 `data/` 传上去。** 那是你本地的库，含账号口令散列；
> 服务器实例应当从空库自行初始化（首次启动会自动建表建目录）。

### 3.2 一键部署（推荐）

```bash
cd /opt/gtd4teams
sudo bash deploy/install.sh
```

脚本按顺序做这些事：

1. 校验 docker / compose / 守护进程权限
2. 生成 `.env`（不存在时），并用 `openssl` 生成随机注册口令
3. `docker compose build` —— 零依赖，不联网装包
4. `docker compose up -d`
5. 轮询 `docker inspect` 的 healthcheck 状态，最多等 60 秒
6. 打印访问地址、注册口令、常用运维命令，以及安全提醒

### 3.3 分步执行（想看清每一步时）

```bash
cd /opt/gtd4teams
cp deploy/env.example .env
vim .env                              # 按需修改，至少要设 GTD_SIGNUP_CODE
docker compose build
docker compose up -d
docker compose ps                     # STATUS 一列应显示 Up (healthy)
```

### 3.4 验证部署

```bash
# 服务自身健康检查（免鉴权端点）
curl -s http://127.0.0.1:8787/api/health
# {"ok": true, "app": "GTD4Teams", "version": "...", "storage": "sqlite", "local": true, ...}

# 容器健康状态
docker inspect --format '{{.State.Health.Status}}' gtd4teams     # healthy

# 确认源码没有被托管出去（应全部 404）
for p in /server.py /pyapp/db.py /README.md; do
  printf '%-16s -> %s\n' "$p" "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8787$p)"
done
```

### 3.5 首次使用：注册账号

打开浏览器访问部署脚本打印的地址，点注册。

- `.env` 里设了 `GTD_SIGNUP_CODE` → 注册时要填对口令
- 关闭了自助注册（`GTD_ALLOW_SIGNUP=0`）→ 由运维开通：

```bash
docker compose exec app python server.py --set-password 某人@example.com
```

该命令交互式输入新密码；**邮箱不存在时会直接创建账号**，所以它同时也是
"关闭注册后如何开账号"的标准答案。

---

## 四、配置项全表

配置一律走项目根目录的 `.env`（从 `deploy/env.example` 复制而来）。
`.env` 已被 `.gitignore` 与 `.dockerignore` 排除 —— 不会进版本库，也不会被打进镜像。

| 变量 | 默认 | 说明 |
|---|---|---|
| `GTD_HTTP_PORT` | `8787` | 宿主机暴露端口。注意与容器内固定监听的 8787 是两回事 |
| `HOST` / `GTD_HOST` | `127.0.0.1` | 应用监听地址（`pyapp/config.py:77`）；检测到 `PORT` 时自动绑 `0.0.0.0`。**容器对外绑定由 compose 的 `127.0.0.1:${GTD_*_PORT}:8787` 前缀写死，不是这个变量** |
| `GTD_ALLOW_SIGNUP` | `1` | 是否允许自助注册。`0` = 关闭 |
| `GTD_SIGNUP_CODE` | 空 | 注册口令。留空 = 任何拿到地址的人都能建号 |
| `GTD_TRUST_PROXY` | `0` | **安全开关**，见下 |
| `GTD_DOMAIN` | — | 仅 tls profile 需要，如 `gtd.example.com` |
| `GTD_LLM_API_KEY` | 空 | AI 能力密钥（可选；也可登录后在界面里填） |
| `GTD_LLM_BASE_URL` | OpenAI 官方 | 任何兼容 `/chat/completions` 的端点 |
| `GTD_LLM_MODEL` | `gpt-4o-mini` | 模型名 |

### 关于 `GTD_TRUST_PROXY`（重要）

它决定服务是否采信 `X-Forwarded-For` / `X-Forwarded-Proto` 这两个请求头。

它们是**客户端可以随意伪造**的。如果服务直连公网却信任它们，
攻击者每次请求换一个假 IP，就能把登录限流键换掉 —— 限流形同虚设。

| 部署形态 | 实际绑定手段 | `GTD_TRUST_PROXY` |
|---|---|---|
| 公网 IP 直连 | compose ports 不加 `127.0.0.1:` 前缀 | `0` |
| Caddy / Nginx 反代之后 | compose ports 加 `127.0.0.1:` 前缀 | `1` |

代码里已按"不信任"作为默认值。开启后限流才能按真实客户端 IP 计数，
会话 Cookie 也才会带上 `Secure` 标志。

---

## 五、调度与自愈

### 5.1 进程级：优雅关闭

`server.py` 已把 `SIGTERM` / `SIGINT` 接到 `server.shutdown()` 上。
`docker stop` 发的是 SIGTERM，进程会正常退出而不是被腰斩 ——
在途请求得以完成，SQLite 的写事务与 WAL 有机会收尾。

`docker stop` 默认给 10 秒宽限期，这个应用远用不到。

### 5.2 容器级：崩溃自愈

```yaml
restart: unless-stopped
```

覆盖三种情形：进程崩溃、容器被 OOM 杀掉、宿主机重启后 Docker 重新拉起。
选 `unless-stopped` 而不是 `always`，是因为手动 `docker compose stop` 之后
不该被自动唤醒 —— 那会让人以为"停不掉"。

### 5.3 健康检查：判断"真的能干活的"而不是"进程还在"

```yaml
healthcheck:
  test: ["CMD", "python", "-c", "...urlopen('http://127.0.0.1:8787/api/health')..."]
  interval: 30s
  timeout: 5s
  start_period: 10s
  retries: 3
```

用 Python 标准库发真实 HTTP 请求，**不装 curl**：少一层依赖，也少一个攻击面。
连续 3 次失败后容器状态变为 `unhealthy`，便于外部监控（如 Uptime Kuma、
阿里云云监控的自定义脚本）据此告警。

### 5.4 宿主机级：开机自启

两件事共同保证"服务器重启后服务自动回来"：

```bash
sudo systemctl enable docker     # Docker 守护进程开机自启
```

加上 compose 里的 `restart: unless-stopped`，容器随之拉起。
部署完成后建议**重启一次服务器验证**，而不是假设它一定生效。

### 5.5 定时任务调度：备份

`deploy/backup.sh` 用应用自带的 `--export` 做**逻辑备份**，
产出一个完整 JSON，并自动保留最近 N 份。

```bash
# 手动备份一次
bash deploy/backup.sh

# 每天 03:30 自动备份（保留 14 份）
sudo crontab -e
30 3 * * * cd /opt/gtd4teams && bash deploy/backup.sh >> /var/log/gtd4teams-backup.log 2>&1
```

为什么用 `--export` 而不是直接 `cp` 那个 `.db` 文件：
导出走的是正常读连接，拿到的一定是一致性快照；
直接拷文件可能拷到写入中途的状态（尤其 WAL 模式下）。

**备份文件含账号口令散列，属敏感文件**，脚本已将其权限设为 `600`。
异地容灾请自行同步到对象存储。

### 5.6 日志

| 来源 | 位置 | 说明 |
|---|---|---|
| 应用访问日志 | SQLite 的 `app_logs` 表 | 结构化的，可在应用内查看 |
| 进程输出 | `docker compose logs app` | 启动横幅、异常栈 |
| Docker 日志文件 | 宿主机 `/var/lib/docker/containers/` | 已限制 `10MB × 3`，防写满磁盘 |
| Caddy 访问日志 | `caddy-data` 卷内 `access.log` | 仅在启用 tls profile 时 |

---

## 六、备份与恢复

### 恢复

先说清楚 `--import` 的语义，这决定了你该用哪种恢复方式：

> `--import` 是 **按主键覆盖（INSERT OR REPLACE）**，既不删旧数据、也不做增量比对。

| 恢复到 | 结果 |
|---|---|
| **空库** | 备份内容被精确还原 |
| **非空库** | 与现有数据按 id 覆盖合并 —— 备份里没有的行会**继续留在库里** |

所以"灾难恢复"要用先清空的方式，把库精确拉回备份那一刻的状态：

```bash
cd /opt/gtd4teams

# 1) 停服务：避免恢复过程中仍有写入
docker compose down

# 2) 用一个临时容器清空旧库并把备份灌回去
#    （run 不发布端口，不会和别的容器抢端口；备份文件以只读方式挂进容器）
docker compose run --rm \
  -v "$PWD/gtd4teams-20260928-033000.json:/tmp/restore.json:ro" \
  app sh -c 'rm -f /data/gtd4teams.db* && python server.py --import /tmp/restore.json'

# 3) 起服务
docker compose up -d
docker compose ps          # 确认 healthy
```

如果只是想**把某几个团队/事项补进现有库**（而不是整体回滚），那就别清空，
用合并导入即可 —— 但务必清楚它是覆盖而不是合并去重：

```bash
docker compose cp gtd4teams-20260928-033000.json app:/data/restore.json
docker compose exec app python server.py --import /data/restore.json
docker compose exec app rm -f /data/restore.json
```

> 恢复完成后建议抽查一下：登录后确认团队数、事项数是否符合预期。
> 会话表不在备份范围内（令牌是短暂凭据），所有人需要重新登录，这是正常的。

### 迁移到另一台服务器

```bash
# 老机器
bash deploy/backup.sh /tmp
# 新机器
#   1) 上传代码 + 备份文件
#   2) 部署（deploy/install.sh）
#   3) 恢复备份
```

因为存储是单文件、格式是纯 JSON，跨机器迁移不涉及任何数据库版本兼容问题。

---

## 七、更新与回滚

### 更新

```bash
cd /opt/gtd4teams
# 1) 拉取或上传新代码
# 2) 重建并重启
docker compose up -d --build
```

有短暂中断（重建容器期间）。团队规模下完全可接受；
若要求零中断，需要引入双实例 + 反向代理切换，本方案不提供。

### 回滚

镜像带 `local` 标签，不自动保留历史版本。要保留回滚能力，建议每次更新前打标签：

```bash
docker tag gtd4teams:local gtd4teams:20260928      # 更新前
# 出问题时回滚
docker compose down
docker tag gtd4teams:20260928 gtd4teams:local
docker compose up -d
```

**数据兼容性**：`--export` / `--import` 是逻辑备份，字段名与表结构在
本项目内保持稳定；但跨版本回滚前建议先做一次备份。

---

## 八、HTTPS（有域名时强烈建议）

默认的 HTTP 直连意味着**登录口令以明文经过网络**。有域名就应该上 HTTPS。

### 步骤

1. 域名 A 记录指向服务器公网 IP
2. 云安全组放行 **80 与 443**
3. 编辑 `.env`：

```ini
GTD_DOMAIN=gtd.example.com
# 槽位端口的 127.0.0.1: 前缀已在 docker-compose.yml 固化；代码中不存在 GTD_BIND 变量
GTD_TRUST_PROXY=1         # 信任 Caddy 递来的 X-Forwarded-*
```

4. 启动带 Caddy 的编排：

```bash
docker compose --profile tls up -d
```

Caddy 会自动申请 Let's Encrypt 证书并到期续期，证书存在 `caddy-data` 卷里。

### 没有域名怎么办

三种选择，按推荐度排序：

1. **申请一个域名**（最省事，几十元/年）
2. 用 `sslip.io` 之类的通配解析：把 `GTD_DOMAIN` 设成 `1-2-3-4.sslip.io`
   （把 IP 的点换成横线），同样能拿到合法证书
3. 继续用 `IP:端口` 直连 —— 可用，但**必须清楚口令是明文传输的**，
   并且应把安全组来源限制为自己的办公网出口 IP

---

## 九、安全基线（部署后逐条勾）

- [ ] `GTD_SIGNUP_CODE` 已设置（或 `GTD_ALLOW_SIGNUP=0`）
- [ ] 云安全组只放行必要端口；数据库端口没有对外开放
- [ ] 有域名时已启用 HTTPS，且 compose 的 `127.0.0.1:` 端口前缀 + `GTD_TRUST_PROXY=1`
- [ ] 无域名时清楚明文风险，安全组来源已做限制
- [ ] `cron` 备份已配置，并**实际验证过一次恢复流程**
- [ ] 已重启服务器一次，确认服务自动恢复
- [ ] `.env` 权限为 `600`，未被提交到版本库

容器层面的加固已经内置在 `docker-compose.yml` 里，无需额外操作：
非 root 运行、只读根文件系统、`cap_drop: ALL`、`no-new-privileges`、
`/tmp` 走 tmpfs、日志大小上限。

---

## 十、运维速查

```bash
docker compose ps                      # 状态与健康
docker compose logs -f app             # 跟踪日志
docker compose restart app             # 重启
docker compose down                    # 停止（数据保留）
docker compose down -v                 # 停止并删除数据（危险）
docker compose up -d --build           # 重建并启动

docker stats gtd4teams                 # 资源占用
docker exec -it gtd4teams sh           # 进容器（alpine 无此问题，debian slim 有 sh）

docker volume inspect gtd4teams_gtd-data   # 数据卷位置
docker compose exec app python server.py --check            # 自检
docker compose exec app python server.py --set-password x@y.z  # 开账号/改密
bash deploy/backup.sh                  # 备份
```

---

## 十一、故障排查

| 现象 | 定位 | 处理 |
|---|---|---|
| 容器起来又退出 | `docker compose logs app` | 多为端口被占用或数据卷权限问题 |
| 状态一直 `starting` | 同上 | healthcheck 未通过；容器内 `curl`/python 探测 8787 |
| 宿主机能访问、外网不能 | 安全组 | 阿里云控制台放行端口 |
| 登录后立刻掉线 | `GTD_TRUST_PROXY` | 开着反代但没设 1 → Cookie 缺 `Secure`；或反之 |
| 所有请求都算同一个 IP | `GTD_TRUST_PROXY=0` 在反代之后 | 改成 1，限流才按真实 IP |
| `build` 拉不到基础镜像 | 网络 | 配 `registry-mirrors` 后 `systemctl restart docker` |
| 数据卷权限拒绝 | bind mount 场景 | `chown -R 10001:10001 /宿主机/目录`（容器内 uid 固定 10001） |
| 磁盘写满 | `docker system df` | 日志已限大小；清 `docker image prune` |

### 查容器为什么起不来

```bash
docker compose logs --tail 100 app
docker inspect gtd4teams --format '{{json .State}}' | python3 -m json.tool
```

---

## 十二、资源占用与容量

| 项 | 量级 |
|---|---|
| 镜像体积 | ~150 MB（`python:3.13-slim` 占绝大部分） |
| 运行时内存 | 约 40–80 MB（Python 进程 + SQLite 缓存） |
| CPU | 常态接近 0；只有 PBKDF2 登录校验是 CPU 密集（毫秒级） |
| 磁盘增长 | 主要是 SQLite 库与 `app_logs` 表，小团队年增量在数十 MB 量级 |

1 核 1 GB 的入门级 ECS 足够。

---

## 附：给我服务器信息后，我会做什么

你提供以下信息后，我可以直接在服务器上把部署跑完并验证：

| 需要 | 说明 |
|---|---|
| 公网 IP | 或域名 |
| SSH 端口、用户名 | 如 `22` / `root` |
| 认证方式 | **推荐 SSH 密钥**（可以把公钥给我，或确认已配好免密）；密码方式无法非交互自动化 |
| 代码放置路径 | 默认建议 `/opt/gtd4teams` |
| 对外端口 | 默认 `8787`；是否配了域名决定要不要上 HTTPS |

我会执行：上传代码 → `deploy/install.sh` → 健康检查 → 暴露面探测
（确认 `/server.py`、`/pyapp/*` 等一律 404）→ 端到端业务验证
（注册 → 建团队 → 写事项 → 读工作区）→ 配置备份 cron。
