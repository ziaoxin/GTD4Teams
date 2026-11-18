#!/usr/bin/env bash
#
# GTD4Teams 零停机发布 —— 在**服务器上**运行。
#
#   sudo bash deploy/release.sh                 # 发布（自动构建 + 全部门禁 + 切流）
#   sudo bash deploy/release.sh --status        # 只看当前状态，什么都不改
#   sudo bash deploy/release.sh --drain 60      # 引流窗口给到 60 秒
#
# 为什么这样就不停机
# ──────────────────
# 双槽位（蓝 / 绿）轮流承载流量，Caddy 是唯一入口也是切流开关：
#
#   1. 新槽位用新镜像起来            ← 旧槽位仍在服务，用户无感
#   2. 新槽位健康 + 构建标识核对 + 冒烟 ← 任何一项不过就原地放弃，线上完全没动过
#   3. 重写 Caddy 上游顺序并热加载     ← 平滑重载，不断连接
#   4. 旧槽位再活一段（引流窗口）      ← 在途请求由它收尾；新槽位若立刻挂了还能退回去
#   5. 停掉旧槽位
#
# 任何一步失败都停在那个位置：**要么线上完全没变，要么还能退回去**，不存在"改了一半"。
#
# 唯一的例外是 --stop-first：库结构发生不兼容变更时，必须先停旧槽位（有短暂中断）。
# 见 docs/零停机发布方案.md 的「库结构变更的兼容纪律」。
#
set -Eeuo pipefail

_RELEASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${_RELEASE_DIR}/lib.sh"

# ------------------------------------------------------------------ 参数
TAG_OPT=''
DRAIN=20
DO_BACKUP=1
DO_PROBE=1
STOP_FIRST=0
SKIP_TESTS=0
STRICT_TESTS=0
KEEP_IMAGES=5
MODE='release'

usage() {
  cat <<'EOF'
用法：bash deploy/release.sh [选项]

  --status           只打印当前槽位/镜像/健康状态，不做任何改动
  --tag TAG          指定镜像标签（默认 gtd4teams:<时间戳>[-<短提交>]）
  --drain SECONDS    切流后旧槽位继续保留的秒数（默认 20）
  --keep N           保留最近 N 个历史镜像（默认 5，最小 2，回滚需要）
  --no-backup        跳过发布前的自动备份（不推荐）
  --no-probe         跳过切流期间的连续性探测
  --stop-first       先停旧槽位再起新槽位（有短暂中断；仅用于不兼容的库结构变更）
  --skip-tests       跳过本机测试门禁（不推荐）
  --strict-tests     本机没有 python3/node 无法跑测试时，直接拒绝发布
  -h, --help         显示本帮助
EOF
}

while (( $# > 0 )); do
  case "$1" in
    --status)       MODE='status' ;;
    --tag)          TAG_OPT="${2:-}"; shift ;;
    --drain)        DRAIN="${2:-20}"; shift ;;
    --keep)         KEEP_IMAGES="${2:-5}"; shift ;;
    --no-backup)    DO_BACKUP=0 ;;
    --no-probe)     DO_PROBE=0 ;;
    --stop-first)   STOP_FIRST=1 ;;
    --skip-tests)   SKIP_TESTS=1 ;;
    --strict-tests) STRICT_TESTS=1 ;;
    -h|--help)      usage; exit 0 ;;
    *)              printf '未知参数：%s\n\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

# ------------------------------------------------------------------ 状态展示
describe_slot() {
  local color="$1" svc port health image
  svc="$(color_service "$color")"
  port="$(color_port "$color")"
  health="$(svc_health "$svc")"
  image="$(svc_image "$svc")"
  printf '    %-6s %-11s %-30s 127.0.0.1:%s\n' \
         "$color" "$health" "${image:-（未运行）}" "$port"
}

print_status() {
  load_state
  printf '\n  GTD4Teams 部署状态\n'
  printf '  ────────────────────────────────────────────────\n'
  printf '  槽位        健康        镜像                            槽位端口\n'
  describe_slot blue
  describe_slot green
  printf '\n'
  if [[ -n "${ACTIVE_COLOR}" ]]; then
    printf '  活跃槽位    %s（%s）\n' "$ACTIVE_COLOR" "${ACTIVE_IMAGE:-未知镜像}"
  else
    printf '  活跃槽位    未知（deploy/run/state.env 不存在）\n'
  fi
  if [[ -n "${PREVIOUS_COLOR}" ]]; then
    printf '  上一版      %s（%s）—— 可执行 rollback.sh 退回\n' \
           "$PREVIOUS_COLOR" "${PREVIOUS_IMAGE:-未知镜像}"
  fi
  printf '  最近发布    %s\n' "${LAST_RELEASE_AT:-—}"
  printf '  公网入口    %s\n' "$(probe_url)"
  printf '  ────────────────────────────────────────────────\n'

  if svc_running app-blue && svc_running app-green; then
    warn '两个槽位都在运行。数据不会坏（WAL + 单写者排队），但请保持「同一时刻只有一个活跃槽位」'
    warn '这个不变量 —— 否则两条写入路径会各自持有连接，行为不再可预期。'
  fi
  printf '\n'
}

if [[ "$MODE" == 'status' ]]; then
  # print_status -> svc_cid 用 "${COMPOSE[@]}" ps 查容器，COMPOSE 必须由 need_docker 初始化。
  # 不先调用它，${COMPOSE[@]} 会塌成空并退化成系统 `ps -q`，两个槽位永远显示 absent（实测）。
  # need_docker 只做只读校验（command -v / docker info / docker compose version），不改任何状态，
  # 且能正确区分 docker compose(v2) 与 docker-compose(v1) —— 比写死 COMPOSE=(docker compose) 稳。
  need_docker
  print_status
  exit 0
fi

# ------------------------------------------------------------------ 测试门禁
run_test_gate() {
  if (( SKIP_TESTS )); then
    warn '已跳过测试门禁（--skip-tests）—— 只保留了镜像构建自检与新槽位冒烟'
    return 0
  fi

  local ran=0 log_file="${RUN_DIR}/tests.log"
  mkdir -p "$RUN_DIR"

  # ---- L0：后端接口测试（真起服务 + 真库）----
  if command -v python3 >/dev/null 2>&1 && [[ -f "${ROOT_DIR}/tests/test_api.py" ]]; then
    log '测试门禁 L0 —— 后端接口测试（真起服务 + 真库，约 1 分钟）'
    if ! python3 "${ROOT_DIR}/tests/test_api.py" >"$log_file" 2>&1; then
      printf '\n' >&2
      tail -40 "$log_file" >&2 || true
      die '后端测试未通过，已中止发布。线上服务未受任何影响。'
    fi
    ok '后端测试通过'
    ran=$(( ran + 1 ))
  else
    warn '本机缺少 python3 或 tests/test_api.py，跳过后端测试'
  fi

  # ---- L0b：新增测试套件（集成域 / 安全 / 零依赖 / 文档表格）----
  # 2026-11-18：集成域落地后，test_api.py 已覆盖不到 /v1/*、MCP、令牌scope、
  # 幂等/乐观锁、令牌明文泄露等新代码。缺了这一层，"冒烟全绿"会让
  # 未通过的集成/安全测试照样发版 —— 门禁空转比没有门禁更危险。
  local suite
  for suite in test_integration test_security_integration test_dependencies test_markdown_tables; do
    if [[ ! -f "${ROOT_DIR}/tests/${suite}.py" ]]; then
      warn "  缺少 tests/${suite}.py，跳过"
      continue
    fi
    if ! python3 -m unittest "tests.${suite}" >"${log_file}.${suite}" 2>&1; then
      printf '\n' >&2
      tail -40 "${log_file}.${suite}" >&2 || true
      die "测试 ${suite} 未通过，已中止发布。线上服务未受任何影响。"
    fi
    ok "  ${suite} 通过"
  done

  # ---- L1：前端冒烟 ----
  if command -v node >/dev/null 2>&1 && [[ -d "${ROOT_DIR}/.smoke" ]]; then
    log '测试门禁 L1 —— 前端冒烟（渲染 / 认证流程 / 真实 DOM 启动）'
    local suite
    # contract-test 与 version-contract 放在最前面：两者都是纯静态读文件、秒级完成，
    # 而且它们抓的正是"测试跑绿了但界面对不上/缓存对不上"那类问题 ——
    # contract-test 防"按钮渲染了却没有 dispatch 分支"（改密断链那次事故），
    # version-contract 防"漏改一处 ?v= 导致用户拿到旧模块"。
    # 失败时它们给出的定位（文件:值）比冒烟测试精确得多，所以先跑、快失败。
    for suite in contract-test version-contract render-test auth-flow-test app-boot-test; do
      if [[ ! -f "${ROOT_DIR}/.smoke/${suite}.mjs" ]]; then
        warn "  缺少 .smoke/${suite}.mjs，跳过"
        continue
      fi
      if ! node "${ROOT_DIR}/.smoke/${suite}.mjs" >"${log_file}.${suite}" 2>&1; then
        printf '\n' >&2
        tail -30 "${log_file}.${suite}" >&2 || true
        die "前端冒烟 ${suite} 未通过，已中止发布。线上服务未受任何影响。"
      fi
      ok "  ${suite} 通过"
    done
    ran=$(( ran + 1 ))
  else
    warn '本机缺少 node 或 .smoke/，跳过前端冒烟'
  fi

  if (( ran == 0 )); then
    warn '这台机器上没能跑成任何测试（缺 python3 与 node 两者）。'
    warn '发布仍会经过「镜像构建自检」与「新槽位冒烟」两道门禁，'
    warn '但完整测试应当在你上传代码的那台构建机上先跑过一遍。'
    if (( STRICT_TESTS )); then
      die '--strict-tests 已开启：拒绝在一台没跑过测试的机器上发布。'
    fi
  fi
}

# ------------------------------------------------------------------ 镜像清理
prune_images() {
  local i=0 tag
  local -a tags=()
  while IFS= read -r tag; do
    [[ -n "$tag" ]] && tags+=("$tag")
  done < <(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null \
             | grep '^gtd4teams:' | grep -v ':dev$' | sort -r || true)

  (( KEEP_IMAGES < 2 )) && KEEP_IMAGES=2      # 至少要留着上一版给回滚用

  for tag in "${tags[@]}"; do
    i=$(( i + 1 ))
    if (( i > KEEP_IMAGES )); then
      # 正在被容器使用的镜像 docker 会拒绝删除，这里的失败是预期内的，忽略即可
      docker rmi "$tag" >/dev/null 2>&1 || true
    fi
  done
}

# ================================================================== 主流程
cd "$ROOT_DIR"

need_docker
require_env_file
compose_config_check
mkdir -p "$RUN_DIR"

# ---- 并发锁：同一时刻只允许有一次发布在跑 ----
# 为什么必须有（2026-11-15 实测踩到）：两次 release.sh 并行时，
#   · 各自算出不同的 TAG，但共用"当前活跃槽位"这一个状态；
#   · 后一次会把前一次刚起的新槽位**重建**成自己的镜像；
#   · 于是前一次的冒烟核对拿到的是别人的构建标识 →
#     "构建标识与本次发布一致" 失败，发布被中止（看起来像代码坏了，其实是撞车）。
# 实测时间线：12:40:30 与 12:40:42 两个 tag、12:41:48 与 12:41:55 两个 tag，
# 每次"第二个 tag"都是另一次发布建的。
# 用 flock（与 watchdog.sh 同一套做法），拿不到锁就直说，不要硬上。
LOCK_FILE="${RUN_DIR}/release.lock"
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  flock -n 9 || die '另一发布正在进行（deploy/run/release.lock 被占用）。
     并发发布会让两次构建与切流互相踩：后一次会把前一次刚起的槽位重建成自己的镜像，
     前一次的构建标识核对随即失败（看着像代码问题，实际是撞车）。等它结束再跑；
     若确认没有发布在跑，用 fuser deploy/run/release.lock 查占用者。'
else
  # O-15（TDS §14）：没有 flock 时绝不静默跳过互斥 —— 那正是本项目反复吃亏的模式。
  warn '=========================================================================='
  warn '  未找到 flock，本次发布无法加锁（并发互斥已失效）！'
  warn '  并发发布会互相踩：后一次会把前一次刚起的槽位重建成它自己的镜像，'
  warn '  前一次的「构建标识与本次发布一致」核对随即失败（看着像代码坏了，其实是撞车）。'
  warn '  默认中止发布。若你能确定此刻没有其它发布在跑，可显式设置：'
  warn '      GTD_ALLOW_NO_FLOCK=1 sudo bash deploy/release.sh'
  warn '  以放弃互斥保护强行继续（风险自负）。'
  warn '=========================================================================='
  [[ "${GTD_ALLOW_NO_FLOCK:-}" == '1' ]] || die '缺少 flock 且未显式设置 GTD_ALLOW_NO_FLOCK=1，已中止发布（线上未受任何影响）。'
  warn '已按 GTD_ALLOW_NO_FLOCK=1 继续：互斥锁已失效，请确保没有其它发布在跑。'
fi

load_state

# ---- 0. 确定活跃槽位 ----
if [[ -z "${ACTIVE_COLOR}" ]]; then
  if svc_running app-blue; then
    ACTIVE_COLOR='blue'
  elif svc_running app-green; then
    ACTIVE_COLOR='green'
  else
    die '没有任何槽位在运行。首次部署请先执行：sudo bash deploy/install.sh'
  fi
  ACTIVE_IMAGE="$(svc_image "$(color_service "$ACTIVE_COLOR")")"
  warn "状态文件缺失，按实际运行情况推断活跃槽位：${ACTIVE_COLOR}"
fi

TARGET="$(other_color "$ACTIVE_COLOR")"

printf '\n  GTD4Teams 零停机发布\n'
printf '  ────────────────────────────────────────────────\n'
printf '  当前活跃   %s（%s）\n' "$ACTIVE_COLOR" "${ACTIVE_IMAGE:-未知}"
printf '  本次目标   %s\n' "$TARGET"
if (( STOP_FIRST )); then
  printf '  \033[33m模式       --stop-first：会先停旧槽位，存在一次短暂中断\033[0m\n'
fi
printf '  ────────────────────────────────────────────────\n\n'

# ---- 1. 测试门禁（在动线上之前）----
run_test_gate

# ---- 2. 发布前备份（给回滚留一条退路）----
if (( DO_BACKUP )); then
  log '发布前备份（失败则中止发布）'
  if ! bash "${ROOT_DIR}/deploy/backup.sh"; then
    die '备份失败，已中止发布。修好备份再发 —— 没有退路的发布不值得冒险。'
  fi
else
  warn '已跳过发布前备份（--no-backup）'
fi

# ---- 3. 构建镜像 ----
TAG="${TAG_OPT:-$(new_tag)}"
BUILD_ID="${TAG#gtd4teams:}"          # 构建标识与镜像标签严格一一对应，避免对不上号
log "构建镜像 ${TAG}（构建标识 ${BUILD_ID}）"

FREE_MB="$(free_mb "$ROOT_DIR")"
if [[ "$FREE_MB" =~ ^[0-9]+$ ]] && (( FREE_MB < 2048 )); then
  warn "磁盘可用空间只有 ${FREE_MB} MB，构建可能失败"
fi

if ! docker build --build-arg "GTD_BUILD=${BUILD_ID}" -t "$TAG" "$ROOT_DIR"; then
  warn '镜像构建失败。构建过程本身包含一次「构建自检」（import 全部模块 + 建库自检），'
  warn '所以这一步失败往往就是代码问题，而不是网络问题。'
  die '已中止发布。线上服务未受任何影响。'
fi
ok "镜像已构建：${TAG}"

# ---- 4. 可选：先停旧槽位（仅用于不兼容的库结构变更）----
if (( STOP_FIRST )); then
  warn "先停旧槽位 ${ACTIVE_COLOR}：现在开始有短暂中断，直到新槽位健康"
  "${COMPOSE[@]}" stop "$(color_service "$ACTIVE_COLOR")" >/dev/null || true
fi

# ---- 5. 起新槽位（旧槽位仍在服务）----
export GTD_IMAGE="$TAG"
log "启动新槽位 ${TARGET}（镜像 ${TAG}）"
"${COMPOSE[@]}" up -d --no-build "$(color_service "$TARGET")" >/dev/null

log "等待新槽位 ${TARGET} 就绪"
if ! wait_healthy "$TARGET" 120; then
  printf '\n' >&2
  "${COMPOSE[@]}" logs --tail 60 "$(color_service "$TARGET")" >&2 || true
  if (( STOP_FIRST )); then
    warn "旧槽位已被 --stop-first 停掉，需要手动拉起：docker compose up -d $(color_service "$ACTIVE_COLOR")"
  fi
  die "新槽位未能在 120 秒内变为 healthy —— 未做任何切流，线上仍是旧版本。"
fi
ok "新槽位 ${TARGET} 已 healthy"

# ---- 6. 产物核对 + 冒烟（任何一项不过都不切流）----
SLOT_URL="http://127.0.0.1:$(color_port "$TARGET")"
log "冒烟测试新槽位（直连 ${SLOT_URL}，绕开 Caddy 以便定位问题）"
if ! bash "${ROOT_DIR}/deploy/smoke.sh" "$SLOT_URL" "$BUILD_ID"; then
  die '新槽位冒烟测试未通过 —— 未做任何切流，线上仍是旧版本。'
fi

# ---- 7. 切流前开始打流（覆盖整个切换窗口）----
PROBE_FILE=''
PROBE_PID=''
if (( DO_PROBE )); then
  PROBE_FILE="$(mktemp)"
  bash "${ROOT_DIR}/deploy/probe.sh" "" "$(( DRAIN + 15 ))" >"$PROBE_FILE" 2>&1 &
  PROBE_PID=$!
  log "已开始在公网入口打流（证明不断线），持续 $(( DRAIN + 15 )) 秒"
  sleep 1
fi

# ---- 8. 切流 ----
log "生成 Caddy 配置并把上游切到 ${TARGET}"
caddyfile_generate "$TARGET"
caddy_reload

# ---- 9. 引流窗口：旧槽位继续活着，收尾在途请求 ----
log "引流 ${DRAIN} 秒（旧槽位 ${ACTIVE_COLOR} 继续服务，新槽位若立刻挂掉还能退回去）"
sleep "$DRAIN"

# ---- 9b. 先把旧槽位从上游列表里摘掉，再停容器 ----
# 顺序反了会真的丢请求。旧槽位此时还挂在 Caddy 的上游列表里当兜底，
# 一旦新槽位那一刻有健康检查超时 / 连接被重置，Caddy 就会把请求转给
# 一台**正在 shutdown 的容器**，客户端拿到 502。
# 实测（2026-11-13，WSL 的 Ubuntu 26.04 + Docker 29）：先停容器的话，
# 停容器那两秒里丢了 2 个 502，其中一次 dial 卡了 1.4 秒。
# 先摘再停，Caddy 手里就没有这台机器可连了。
log "把旧槽位 ${ACTIVE_COLOR} 从上游摘除（先摘再停）"
caddyfile_generate "$TARGET" solo
caddy_reload
sleep 3   # 给在途请求收尾、也给健康检查一个刷新的窗口

# ---- 10. 停旧槽位 ----
log "停止旧槽位 ${ACTIVE_COLOR}"
"${COMPOSE[@]}" stop "$(color_service "$ACTIVE_COLOR")" >/dev/null || true

# ---- 11. 汇总连续性探测 ----
# 保留探测的**具体退出码**：1 = 出现失败请求；3 = 没有失败请求但切换窗口内有秒级空档。
# 两者都说明"这次不是零停机"，但排查方向完全不同（丢请求 → 看 Caddy/旧槽位；
# 只丢空档 → 看切换动作本身的停顿），混成一句话会把人引向错误的方向。
# 注意：此时切流与停旧槽位都已完成，探测在这里只是**收口报告**，
# 所以任何非零结果都只告警 + 本次 release 以非零码结束，不回头中止发布
# （此刻回滚的代价比让人看着结论决定更高）。
PROBE_RC=0
if (( DO_PROBE )); then
  wait "$PROBE_PID" || PROBE_RC=$?
  printf '\n  切流窗口的连续性探测结果\n'
  printf '  ────────────────────────────────────────────────\n'
  cat "$PROBE_FILE"
  # 先归档、再删临时文件：这份报告是「这次发布有没有断线」的唯一物证，
  # 归档失败不影响发布（不参与下面任何判定）。
  archive_probe_report "$PROBE_FILE" "$BUILD_ID" || true
  rm -f "$PROBE_FILE"
fi

# ---- 12. 保存状态 ----
PREVIOUS_COLOR="$ACTIVE_COLOR"
PREVIOUS_IMAGE="$ACTIVE_IMAGE"
ACTIVE_COLOR="$TARGET"
ACTIVE_IMAGE="$TAG"
save_state

# ---- 13. 清理历史镜像 ----
prune_images

# ---- 14. 结果 ----
printf '  ────────────────────────────────────────────────\n'
printf '  发布完成\n'
printf '  ────────────────────────────────────────────────\n'
printf '  活跃槽位   %s\n' "$ACTIVE_COLOR"
printf '  镜像       %s\n' "$TAG"
printf '  构建标识   %s\n' "$BUILD_ID"
printf '  回滚方式   sudo bash deploy/rollback.sh\n'
printf '  公网入口   %s\n' "$(probe_url)"
printf '  ────────────────────────────────────────────────\n\n'

if (( DO_PROBE )) && (( PROBE_RC != 0 )); then
  case "$PROBE_RC" in
    3)
      warn '连续性探测：没有失败请求，但切换窗口内出现过秒级空档 —— 这次切换不是零停机。'
      warn '（空档期内的用户请求是失败的，只是恰好没被探测打到。）'
      ;;
    *)
      warn '连续性探测报告了失败请求 —— 这次切换并非零停机。'
      ;;
  esac
  warn '若线上表现仍有问题，可立即回滚：sudo bash deploy/rollback.sh'
  exit 3
fi
if (( STOP_FIRST )); then
  warn '本次使用了 --stop-first，切换期间存在设计中允许的短暂中断。'
fi
