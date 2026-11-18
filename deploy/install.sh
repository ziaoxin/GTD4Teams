#!/usr/bin/env bash
#
# GTD4Teams 首次部署 —— 在**服务器上**运行。
#
#   sudo bash deploy/install.sh
#
# 只做四件事：校验环境 → 准备 .env → 构建镜像 → 拉起蓝槽位与 Caddy 并验证。
# 幂等：重复执行会识别出"已经部署过"，把该起的服务起回来，不会动数据、不会覆盖 .env。
#
# 之后的每次发布请用 deploy/release.sh —— 它走双槽位切换，发布期间不停机。
#
set -Eeuo pipefail

_INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${_INSTALL_DIR}/lib.sh"

cd "$ROOT_DIR"

# ------------------------------------------------------------ 1. 环境校验
log '校验运行环境'
need_docker
log "编排命令：${COMPOSE[*]}"

# ------------------------------------------------------------ 2. 准备 .env
if [[ -f "${ROOT_DIR}/.env" ]]; then
  log '.env 已存在，沿用现有配置（要改就直接编辑它）'
else
  cp "${ROOT_DIR}/deploy/env.example" "${ROOT_DIR}/.env"
  log '已从 deploy/env.example 生成 .env'

  # 自助注册已随 CR-2026-001 **从代码里移除**（POST /api/auth/register 返回 404），
  # 所以这里不再生成"注册口令"：那个口令现在没有任何地方会消费它。
  # 打印出来只会让人以为"填了口令就能注册"，然后去改一个无效配置。
  # 账号只能由管理员创建 —— 首任系统管理员用 --create-admin，其余由团队管理员在界面开通。
  log '账号由管理员创建（自助注册已下线）：首任管理员请用 --create-admin <邮箱>'
fi
require_env_file
compose_config_check

BIND_HINT="$(read_env GTD_HTTP_PORT 80)"
DOMAIN="$(read_env GTD_DOMAIN '')"
TRUST_PROXY="$(read_env GTD_TRUST_PROXY 1)"
# 这里**不再读取 GTD_ALLOW_SIGNUP / GTD_SIGNUP_CODE**：自助注册已下线（CR-2026-001），
# 读取它们只会在老 .env 上制造假警报（"任何人都能注册"这种已经不可能发生的事）。

# ------------------------------------------------------------ 3. 生成 Caddy 配置
load_state
TARGET_COLOR="${ACTIVE_COLOR:-blue}"
log "生成 Caddy 配置（当前指向槽位 ${TARGET_COLOR}）"
caddyfile_generate "$TARGET_COLOR"
ok "已生成 ${CADDY_CONFIG}"

# 首次部署时 Caddy 还没起来，没法 exec 进去校验，但也不能就这么把一份可能有错的
# 配置交给它 —— 那样 Caddy 启动失败，而报错信息指向的是容器日志，排查会绕远路。
if ! svc_running caddy; then
  log '校验 Caddy 配置（用一次性容器）'
  if ! caddy_validate_standalone; then
    warn '生成的配置未通过校验，具体错误如下：'
    docker run --rm \
      -v "${CADDY_CONFIG}:/etc/caddy/Caddyfile:ro" \
      caddy:2-alpine caddy validate \
      --config /etc/caddy/Caddyfile --adapter caddyfile >&2 || true
    die "配置有问题，已中止部署。产物在 ${CADDY_CONFIG}，可对照 deploy/Caddyfile.template 排查。"
  fi
  ok 'Caddy 配置校验通过'
fi

# ------------------------------------------------------------ 4. 已经部署过 → 只把服务起回来
if [[ -n "${ACTIVE_COLOR}" ]]; then
  log "检测到这台机器已经部署过（活跃槽位 ${ACTIVE_COLOR}，镜像 ${ACTIVE_IMAGE:-未记录}）"

  IMAGE_TO_USE="${ACTIVE_IMAGE}"
  if [[ -z "$IMAGE_TO_USE" ]] || ! docker image inspect "$IMAGE_TO_USE" >/dev/null 2>&1; then
    warn "记录的镜像不在本地了，改为构建一个新版本"
    IMAGE_TO_USE="$(new_tag)"
    BUILD_ID="${IMAGE_TO_USE#gtd4teams:}"
    docker build --build-arg "GTD_BUILD=${BUILD_ID}" -t "$IMAGE_TO_USE" "$ROOT_DIR" || die '镜像构建失败'
    ACTIVE_IMAGE="$IMAGE_TO_USE"
    save_state
  fi

  export GTD_IMAGE="$IMAGE_TO_USE"
  log "启动槽位 ${ACTIVE_COLOR} 与入口 Caddy"
  "${COMPOSE[@]}" up -d --no-build "$(color_service "$ACTIVE_COLOR")" caddy >/dev/null

  if ! wait_healthy "$ACTIVE_COLOR" 120; then
    printf '\n' >&2
    "${COMPOSE[@]}" logs --tail 60 "$(color_service "$ACTIVE_COLOR")" >&2 || true
    die '服务未能在 120 秒内变为 healthy'
  fi
  ok '服务已就绪'

  printf '\n  ────────────────────────────────────────────────\n'
  printf '  服务已启动（本次未改动版本）\n'
  printf '  ────────────────────────────────────────────────\n'
  printf '  当前版本   %s\n' "$ACTIVE_IMAGE"
  printf '  公网入口   %s\n' "$(probe_url)"
  printf '  发布新版   sudo bash deploy/release.sh   ← 零停机\n'
  printf '  查看状态   sudo bash deploy/release.sh --status\n'
  printf '  ────────────────────────────────────────────────\n\n'
  exit 0
fi

# ------------------------------------------------------------ 5. 全新部署
TAG="$(new_tag)"
BUILD_ID="${TAG#gtd4teams:}"
log "构建镜像 ${TAG}（构建标识 ${BUILD_ID}）"
log '（构建过程自带一次自检：import 全部模块 + 建库检查，所以这一步失败通常是代码问题）'

if ! docker build --build-arg "GTD_BUILD=${BUILD_ID}" -t "$TAG" "$ROOT_DIR"; then
  warn '镜像构建失败。若提示拉不到 python:3.13-slim，多半是网络问题，可配置镜像加速后重试，'
  warn '例如写入 /etc/docker/daemon.json：'
  warn '  {"registry-mirrors": ["https://docker.m.daocloud.io"]}'
  warn '然后 systemctl restart docker，再重跑本脚本。'
  exit 1
fi
ok "镜像已构建：${TAG}"

export GTD_IMAGE="$TAG"
log '启动蓝槽位与入口 Caddy'
"${COMPOSE[@]}" up -d --no-build app-blue caddy >/dev/null

log '等待服务就绪（最多 120 秒）'
if ! wait_healthy 'blue' 120; then
  printf '\n' >&2
  "${COMPOSE[@]}" logs --tail 60 app-blue >&2 || true
  die '服务未能在 120 秒内变为 healthy'
fi
ok '服务已就绪（健康检查通过）'

# ------------------------------------------------------------ 6. 冒烟验证
SLOT_URL="http://127.0.0.1:$(color_port blue)"
log '冒烟验证'
if ! bash "${ROOT_DIR}/deploy/smoke.sh" "$SLOT_URL" "$BUILD_ID"; then
  # 这里**不能**只 warn 然后继续打印"部署完成"。
  # 那样一道门禁在失败时会被静默放行，而"部署完成"这四个字会让人直接跳过上面那些 ✗
  # —— 本项目实测踩过：构建标识核对 ✗ 的同时，末尾照样打印了"部署完成"。
  # 服务保持运行不动（可能本来就是既有环境），但退出码必须非零、横幅必须不打。
  warn '冒烟测试未通过：对外暴露面或接口行为有问题。'
  warn '服务仍在运行、数据未被改动 —— 但那不等于可以对外公布地址。'
  warn '修好后重跑本脚本即可（幂等，不会动数据）。'
  die '未通过冒烟门禁，部署中止。'
fi

# ------------------------------------------------------------ 7. 记录状态
ACTIVE_COLOR='blue'
ACTIVE_IMAGE="$TAG"
PREVIOUS_COLOR=''
PREVIOUS_IMAGE=''
save_state

# ------------------------------------------------------------ 8. 结果提示
PUBLIC_IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)"

printf '\n  ────────────────────────────────────────────────\n'
printf '  部署完成\n'
printf '  ────────────────────────────────────────────────\n'

if [[ -n "$DOMAIN" ]]; then
  printf ' 访问入口   https://%s/   （证书由 Caddy 自动申请与续期）\n' "$DOMAIN"
elif [[ -n "$PUBLIC_IP" ]]; then
  printf ' 访问入口   http://%s:%s/\n' "$PUBLIC_IP" "$BIND_HINT"
else
  printf ' 访问入口   http://<本机公网IP>:%s/\n' "$BIND_HINT"
fi

printf ' 开通账号   首次引导 %s exec -T app-blue python server.py --create-admin <邮箱>\n' "${COMPOSE[*]}"
# 后续账号的正规路径是界面委派（账号自带团队归属与角色）。
# CLI 侧 --set-password 也能建号（store.reset_password 的 create_if_missing），
# 但建出来的是 is_system_admin=0、无团队归属的裸账号 —— 适合"管理员忘密码"这类兜底，
# 不适合当建号主路径。详见 docs/process/09 §7.4。
printf '            后续账号 由上级管理员在界面创建：系统管理员建「团队管理员」，团队管理员建「成员」\n'

printf ' 槽位端口   blue=127.0.0.1:%s（仅回环，不对外）\n' "$(color_port blue)"
printf ' 数据卷     gtd4teams_gtd-data\n'
printf ' 发布新版   sudo bash deploy/release.sh     ← 双槽位切换，不停机\n'
printf ' 查看状态   sudo bash deploy/release.sh --status\n'
printf ' 立即备份   bash deploy/backup.sh\n'
printf ' 跟踪日志   %s logs -f app-blue caddy\n' "${COMPOSE[*]}"
printf '  ────────────────────────────────────────────────\n\n'

# ------------------------------------------------------------ 9. 安全与环境提示
if [[ -z "$DOMAIN" ]]; then
  warn '未配置域名：当前是 HTTP 明文访问，登录口令会以明文经过网络。'
  warn '配好 GTD_DOMAIN（A 记录指向本机）后有两条命令要一起做：'
  warn '  1) .env 里填 GTD_DOMAIN=你的域名'
  warn '  2) bash deploy/release.sh --status 确认状态后，任选一次 release.sh 或'
  warn '     docker compose up -d caddy 让 Caddy 重新读取配置'
fi
if [[ "$TRUST_PROXY" != '1' ]]; then
  warn 'GTD_TRUST_PROXY 被改成了非 1：这样所有请求会被算成 Caddy 容器的 IP，'
  warn '登录限流将按"同一个来源"计数，正常用户也可能被误伤。除非你清楚原因，否则改回 1。'
fi
warn '别忘了在云厂商控制台的安全组里放行 80 与 443，否则外面访问不到。'
warn '槽位端口（8788/8789）只绑在回环上，无需、也不应该对公网放行。'
