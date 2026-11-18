#!/usr/bin/env bash
#
# GTD4Teams 回滚 —— 退回到上一个已知可用的版本，同样是零停机切换。
#
#   sudo bash deploy/rollback.sh              # 退回上一版
#   sudo bash deploy/rollback.sh --list       # 看看本地还留着哪些镜像
#   sudo bash deploy/rollback.sh --no-backup  # 跳过回滚前的备份
#
# 它做的事和 release.sh 的后半段完全一样（起旧槽位 → 冒烟 → 切流 → 引流 → 停新槽位），
# 唯一的区别是**不重新构建**：直接拿上一次保留下来的镜像标签。
# 所以回滚通常只要十几秒，比修 bug 再发一版快得多。
#
# 前提：目标镜像还在本地。release.sh 默认保留最近 5 个镜像（且最少保留 2 个 =
# 当前版 + 上一版），正是为了这一刻。
#
set -Eeuo pipefail

_ROLLBACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${_ROLLBACK_DIR}/lib.sh"

DRAIN=15
DO_BACKUP=1
DO_PROBE=1
MODE='rollback'
TO_COLOR=''

usage() {
  cat <<'EOF'
用法：bash deploy/rollback.sh [选项]

  --list             列出本地保留的 gtd4teams 镜像后退出
  --to blue|green    指定用哪个槽位承载回滚（默认沿用上一版所在的槽位）
  --drain SECONDS    切流后新槽位（被回滚掉的那个）继续保留的秒数（默认 15）
  --no-backup        跳过回滚前的自动备份
  --no-probe         跳过切流期间的连续性探测
  -h, --help         显示本帮助
EOF
}

while (( $# > 0 )); do
  case "$1" in
    --list)       MODE='list' ;;
    --to)         TO_COLOR="${2:-}"; shift ;;
    --drain)      DRAIN="${2:-15}"; shift ;;
    --no-backup)  DO_BACKUP=0 ;;
    --no-probe)   DO_PROBE=0 ;;
    -h|--help)    usage; exit 0 ;;
    *)            printf '未知参数：%s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

cd "$ROOT_DIR"
need_docker

if [[ "$MODE" == 'list' ]]; then
  printf '\n  本地保留的 gtd4teams 镜像（标签按时间倒序，第一个是最新的）\n'
  printf '  ────────────────────────────────────────────────\n'
  # 标签形如 20260928-143000-ab12cd3，字典序恰好等于时间序，直接逆序即最新在前
  docker images --format '{{.Repository}}:{{.Tag}}\t{{.CreatedAt}}\t{{.Size}}' 2>/dev/null \
    | grep '^gtd4teams:' | grep -v ':dev' | sort -r \
    | awk -F'\t' '{ printf "    %-42s %s  %s\n", $1, $2, $3 }' || true
  printf '\n  （<none> 之类的悬空镜像不在这里列出。正在使用的镜像 docker 不允许删除，属正常。）\n\n'
  exit 0
fi

require_env_file
mkdir -p "$RUN_DIR"

# ---- 并发锁：与 release.sh **共用同一把锁** ----
# 为什么必须共用而不是各开一把：回滚与发布并行比两次发布并行更隐蔽 ——
#   发布把 active 从 A 切到 B，回滚又按它读到的**旧状态**切回 A，
#   两边各自都"成功"，最终 state.env 与实际运行的镜像对不上，而两份日志都理直气壮。
# 这种失败比"构建撞车"难查得多（构建撞车至少会在冒烟阶段当场失败）。
LOCK_FILE="${RUN_DIR}/release.lock"
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  flock -n 9 || die '另一发布/回滚正在进行（deploy/run/release.lock 被占用）。
     两者并行的后果不是"慢一点"，而是**状态被互换两次**：发布把 active 从 A 切到 B，
     回滚又按旧状态切回 A，最终 deploy/run/state.env 与实际运行的镜像不一致，
     而两边日志都显示成功。等对方结束再跑；若确认没有任务在跑，用 fuser deploy/run/release.lock 查占用者。'
fi

load_state

if [[ -z "${PREVIOUS_COLOR}" || -z "${PREVIOUS_IMAGE}" ]]; then
  die '没有可回滚的版本。deploy/run/state.env 里没有 PREVIOUS_* 记录 ——
     说明这台机器上成功发布过的次数还不到两次。'
fi

TARGET="${TO_COLOR:-$PREVIOUS_COLOR}"
BACK_IMAGE="$PREVIOUS_IMAGE"
BACK_BUILD="${BACK_IMAGE#gtd4teams:}"
CURRENT_COLOR="$ACTIVE_COLOR"
CURRENT_IMAGE="$ACTIVE_IMAGE"

if ! docker image inspect "$BACK_IMAGE" >/dev/null 2>&1; then
  die "回滚目标镜像 ${BACK_IMAGE} 不在本地了（可能被镜像清理删掉）。
     可用的镜像：docker images gtd4teams
     或者干脆前滚：修好代码后重新执行 release.sh"
fi

printf '\n  GTD4Teams 回滚\n'
printf '  ────────────────────────────────────────────────\n'
printf '  当前版本   %s（%s）\n' "$CURRENT_COLOR" "${CURRENT_IMAGE:-未知}"
printf '  回滚到     %s（%s）\n' "$TARGET" "$BACK_IMAGE"
printf '  ────────────────────────────────────────────────\n\n'

if (( DO_BACKUP )); then
  log '回滚前备份（失败则中止）'
  if ! bash "${ROOT_DIR}/deploy/backup.sh"; then
    die '备份失败，已中止回滚。'
  fi
else
  warn '已跳过回滚前备份（--no-backup）'
fi

# ---- 1. 用旧镜像把目标槽位起起来（当前槽位仍在服务）----
export GTD_IMAGE="$BACK_IMAGE"
log "启动槽位 ${TARGET}（镜像 ${BACK_IMAGE}）"
"${COMPOSE[@]}" up -d --no-build "$(color_service "$TARGET")" >/dev/null

log "等待槽位 ${TARGET} 就绪"
if ! wait_healthy "$TARGET" 120; then
  printf '\n' >&2
  "${COMPOSE[@]}" logs --tail 60 "$(color_service "$TARGET")" >&2 || true
  die "回滚目标槽位未能在 120 秒内变为 healthy —— 未做任何切流，线上仍是当前版本。"
fi
ok "槽位 ${TARGET} 已 healthy"

# ---- 2. 冒烟（核对回滚到的确实是那个旧构建）----
SLOT_URL="http://127.0.0.1:$(color_port "$TARGET")"
log "冒烟测试回滚目标（${SLOT_URL}，期望构建标识 ${BACK_BUILD}）"
if ! bash "${ROOT_DIR}/deploy/smoke.sh" "$SLOT_URL" "$BACK_BUILD"; then
  die '回滚目标的冒烟测试未通过 —— 未做任何切流，线上仍是当前版本。'
fi

# ---- 3. 打流 + 切流 ----
PROBE_FILE=''
PROBE_PID=''
if (( DO_PROBE )); then
  PROBE_FILE="$(mktemp)"
  bash "${ROOT_DIR}/deploy/probe.sh" "" "$(( DRAIN + 15 ))" >"$PROBE_FILE" 2>&1 &
  PROBE_PID=$!
  log "已开始在公网入口打流，持续 $(( DRAIN + 15 )) 秒"
  sleep 1
fi

log "生成 Caddy 配置并把上游切到 ${TARGET}"
caddyfile_generate "$TARGET"
caddy_reload

log "引流 ${DRAIN} 秒"
sleep "$DRAIN"

if [[ -n "$CURRENT_COLOR" && "$CURRENT_COLOR" != "$TARGET" ]] && svc_running "$(color_service "$CURRENT_COLOR")"; then
  # 先摘上游、再停容器：容器 shutdown 期间仍挂在 Caddy 上游列表里时，
  # 一旦目标槽位那一刻连接被重置，Caddy 会把请求转给它，客户端看到 502
  # （release.sh 里实测踩过，这里是同一处陷阱的对称面）。
  log "把槽位 ${CURRENT_COLOR} 从上游摘除（先摘再停）"
  caddyfile_generate "$TARGET" solo
  caddy_reload
  sleep 3

  log "停止槽位 ${CURRENT_COLOR}（被回滚掉的那个版本）"
  "${COMPOSE[@]}" stop "$(color_service "$CURRENT_COLOR")" >/dev/null || true
fi

# 保留探测的具体退出码：1 = 有请求失败，3 = 没丢请求但切换期间有秒级空档。
# 与 release.sh 同一套判读，别在这里把它压回一个笼统的"失败"。
PROBE_RC=0
if (( DO_PROBE )); then
  wait "$PROBE_PID" || PROBE_RC=$?
  printf '\n  切流窗口的连续性探测结果\n'
  printf '  ────────────────────────────────────────────────\n'
  cat "$PROBE_FILE"
  # 与 release.sh 同一处理：先归档再删，回滚同样是"有没有断线"的一等证据。
  archive_probe_report "$PROBE_FILE" "$BACK_BUILD" || true
  rm -f "$PROBE_FILE"
fi

# ---- 4. 更新状态：两个版本互换，方便"回滚错了再滚回来" ----
PREVIOUS_COLOR="$CURRENT_COLOR"
PREVIOUS_IMAGE="$CURRENT_IMAGE"
ACTIVE_COLOR="$TARGET"
ACTIVE_IMAGE="$BACK_IMAGE"
save_state

printf '  ────────────────────────────────────────────────\n'
printf '  回滚完成\n'
printf '  ────────────────────────────────────────────────\n'
printf '  活跃槽位   %s\n' "$ACTIVE_COLOR"
printf '  镜像       %s\n' "$BACK_IMAGE"
printf '  构建标识   %s\n' "$BACK_BUILD"
printf '  说明       状态已互换：如需「回滚掉这次回滚」，再执行一次 rollback.sh 即可。\n'
printf '  ────────────────────────────────────────────────\n\n'

if (( DO_PROBE )) && (( PROBE_RC != 0 )); then
  case "$PROBE_RC" in
    3)
      warn '连续性探测：没有失败请求，但切换窗口内出现过秒级空档 —— 这次回滚不是零停机。'
      ;;
    *)
      warn '连续性探测报告了失败请求 —— 这次回滚并非零停机。'
      ;;
  esac
  exit 3
fi
