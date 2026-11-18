#!/usr/bin/env bash
#
# GTD4Teams 看门狗 —— 单服务器上的兜底自愈。**可选**，交给 cron 每分钟跑一次。
#
#   bash deploy/watchdog.sh
#
#   crontab -e
#   * * * * * cd /opt/gtd4teams && bash deploy/watchdog.sh >> /var/log/gtd4teams-watchdog.log 2>&1
#
# 它补的是 restart: unless-stopped 覆盖不到的那一类故障：
#   · 进程活着但健康检查持续失败（端口在听、请求全 500）；
#   · 有人手工把容器停了忘了起回来；
#   · Docker 守护进程重启后容器没回来。
#
# 处置是**逐级**的，不会一上来就大动作：
#   连续 3 次不健康  → 重启该槽位
#   连续 8 次不健康  → 若 GTD_WATCHDOG_FAILOVER=1，则切回上一版；否则只告警
# 默认不自动切版本：自动降级若悄悄发生，第二天没人说得清线上跑的是哪一版。
#
set -Eeuo pipefail

_WATCHDOG_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${_WATCHDOG_DIR}/lib.sh"

STREAK_FILE="${RUN_DIR}/watchdog-streak"
# 走 read_env 读 .env（lib.sh 已在上方 source）。原来写的是
# ${GTD_WATCHDOG_FAILOVER:-0}，只读进程环境 —— 在 .env 里写 1 是**静默无效**的，
# 结果就是"以为开了自动降级，其实永远只告警"。cron 里也没人 export 它。
# fallback 仍取同名环境变量：优先级 = .env > 进程环境 > 0，
# 保证既有 crontab 里 `GTD_WATCHDOG_FAILOVER=1 bash deploy/watchdog.sh` 的写法继续有效。
FAILOVER="$(read_env GTD_WATCHDOG_FAILOVER "${GTD_WATCHDOG_FAILOVER:-0}")"
if [[ -n "${GTD_WATCHDOG_FAILOVER:-}" && "${GTD_WATCHDOG_FAILOVER}" != "$FAILOVER" ]]; then
  printf '[%s] 注意：GTD_WATCHDOG_FAILOVER 冲突 —— .env 里是 %s，进程环境里是 %s，按 .env 为准。\n' \
         "$(date '+%F %T')" "$FAILOVER" "$GTD_WATCHDOG_FAILOVER"
fi
RESTART_AT=3
FAILOVER_AT=8

mkdir -p "$RUN_DIR"

# 防止 cron 上一次还没跑完就又起一个（等待健康最多 90 秒 > 1 分钟）
LOCK_FILE="${RUN_DIR}/watchdog.lock"
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    exit 0            # 上一轮还在跑，这一轮直接让路
  fi
fi

streak_read() {
  if [[ -f "$STREAK_FILE" ]]; then
    tr -dc '0-9' < "$STREAK_FILE" || printf '0'
  else
    printf '0'
  fi
}
streak_write() { printf '%s' "$1" > "$STREAK_FILE"; }

# ---------------------------------------------------------------- 证书待应用
# 后台上传证书后会在 deploy/run/certs/ 里留下 .pending-apply（容器内是 /certs/.pending-apply）。
# 为什么用"标记文件 + 看门狗轮询"而不是让后端直接重载 Caddy：
#   后端容器没有 docker socket（也不该有），它唯一能做、也唯一该做的动作就是"放一个标记"；
#   真正的"重生成配置 + 热加载"必须发生在宿主机上，由有权限的脚本做。
#
# 三条语义（都是踩过的坑换来的）：
#   1. 成功才删标记 —— 失败就删会让用户以为生效了，而实际线上还是旧证书；
#   2. 失败必须留痕 —— 写进 deploy/run/cert-apply.log，否则"没生效"是完全无声的；
#   3. 不能影响看门狗本职 —— 生成/重载放在子 shell 里跑，caddy_reload 的 die 不能把
#      本轮的巡检一起带走（否则证书问题会连带把自愈能力关掉）。
CERT_PENDING="${RUN_DIR}/certs/.pending-apply"
CERT_APPLY_LOG="${RUN_DIR}/cert-apply.log"
# 连续失败轮次。**刻意放在 RUN_DIR 而不是 /certs**：/certs 是给后端看的约定目录，
# 里面多出别的文件会让"这个目录里有什么"变得含糊；失败计数是纯运维侧的账。
CERT_RETRY_FILE="${RUN_DIR}/cert-apply-retries"

# 记一次失败并决定日志的口气：
#   · 前 2 轮 → 普通告警（大多数失败是暂时的：模板一时改坏、Caddy 刚重启）
#   · 第 3 轮起 → 明确写"已连续失败、需人工介入"，因为 cron 每分钟一次，
#     连续三轮说明不是抖动。后端的 `pendingApply` 会一直是 true（它看不到我的日志），
#     所以**这一行是唯一能让人知道"卡住了"的地方**。
cert_record_retry() {
  local reason="$1" n stamp
  n="$(cat "$CERT_RETRY_FILE" 2>/dev/null || echo 0)"
  [[ "$n" =~ ^[0-9]+$ ]] || n=0
  n=$(( n + 1 ))
  printf '%s\n' "$n" > "$CERT_RETRY_FILE" 2>/dev/null || true
  stamp="$(date '+%F %T')"
  if (( n <= 2 )); then
    printf '[%s] 证书应用失败（第 %s 轮）：%s。**标记保留**，下一轮重试；详见 %s。\n' \
           "$stamp" "$n" "$reason" "$CERT_APPLY_LOG" | tee -a "$CERT_APPLY_LOG"
  else
    printf '[%s] 证书应用失败（第 %s 轮，**已连续失败、需人工介入**）：%s。**标记保留中**；排查 %s。\n' \
           "$stamp" "$n" "$reason" "$CERT_APPLY_LOG" | tee -a "$CERT_APPLY_LOG"
  fi
}

cert_apply_if_pending() {
  [[ -f "$CERT_PENDING" ]] || return 0

  local other_mode='' rc=0 stamp
  stamp="$(date '+%F %T')"
  if svc_running "$(color_service "$(other_color "$ACTIVE_COLOR")")"; then
    other_mode=''          # 另一个槽位活着 → 双上游（与发布脚本的拓扑一致）
  else
    other_mode='solo'
  fi

  local -a gen_args=("$ACTIVE_COLOR")
  [[ "$other_mode" == 'solo' ]] && gen_args+=('solo')

  printf '[%s] 检测到证书待应用标记（%s），开始重生成配置并热加载（%s 上游）。\n' \
         "$stamp" "$CERT_PENDING" "${other_mode:-dual}"
  printf '[%s] === 开始应用证书（active=%s mode=%s）===\n' \
         "$stamp" "$ACTIVE_COLOR" "${other_mode:-dual}" >> "$CERT_APPLY_LOG"

  # 子 shell：隔离 caddy_reload 的 die，别让它中断本轮的巡检
  if ( caddyfile_generate "${gen_args[@]}" && caddy_reload ) >> "$CERT_APPLY_LOG" 2>&1; then
    # 断言：Caddy **内存里当前提供的**证书，必须就是我们刚写进配置的那张。
    # 少了这一步，"换了证书文件但配置文本没变 → Caddy 跳过加载 → 仍是旧证书"会被当成成功。
    # （实测踩到：日志只有 config is unchanged，对外 CN 还是旧的，而脚本打印"应用成功"。）
    local want got
    want="$(cert_fingerprint_file "${RUN_DIR}/certs/tls.crt")"
    got="$(caddy_served_cert_fingerprint)"
    if [[ -z "$want" ]]; then
      cert_record_retry '读不到 /certs/tls.crt 的证书指纹'
      return 0
    fi
    if [[ -z "$got" ]]; then
      cert_record_retry '读不到 Caddy 实际提供的证书（HTTPS 未监听？）'
      return 0
    fi
    if [[ "$want" != "$got" ]]; then
      printf '[%s] 证书指纹不符：期望 %s / 实际 %s（Caddy 仍在用旧证书）\n' \
             "$(date '+%F %T')" "$want" "$got" >> "$CERT_APPLY_LOG"
      cert_record_retry "Caddy 仍在提供旧证书（指纹不符 ${want:0:16}… vs ${got:0:16}…）"
      return 0
    fi
    rm -f "$CERT_PENDING"
    rm -f "$CERT_RETRY_FILE"      # 成功就清掉失败计数，下次从第 1 轮算起
    stamp="$(date '+%F %T')"
    printf '[%s] 证书已应用并热加载成功（指纹 %s 已核对，标记已删除）。\n' "$stamp" "${want:0:16}…" | tee -a "$CERT_APPLY_LOG"
  else
    rc=$?
    cert_record_retry "reload/校验失败（rc=${rc}，真因见日志尾部）"
  fi

  # 日志轮转：只保留最近 200 行。
  # 只涨不减的日志最后总是以两种方式收场：没人看，或者把磁盘写满。
  if [[ -f "$CERT_APPLY_LOG" ]] && (( $(wc -l < "$CERT_APPLY_LOG" 2>/dev/null || echo 0) > 200 )); then
    tail -n 200 "$CERT_APPLY_LOG" > "${CERT_APPLY_LOG}.tmp" 2>/dev/null \
      && mv -f "${CERT_APPLY_LOG}.tmp" "$CERT_APPLY_LOG" 2>/dev/null || true
  fi
  return 0
}

need_docker
load_state

if [[ -z "$ACTIVE_COLOR" ]]; then
  printf '[%s] 尚未发布过（没有活跃槽位记录），无事可做。\n' "$(date '+%F %T')"
  exit 0
fi

# 证书待应用检查放在健康判定**之前**：它是独立的一条工作线，
# 健康与否都要把用户刚上传的证书应用掉（失败也照样继续往下做巡检）。
cert_apply_if_pending

SVC="$(color_service "$ACTIVE_COLOR")"
HEALTH="$(svc_health "$SVC")"
STREAK="$(streak_read)"

case "$HEALTH" in
  healthy)
    if [[ "$STREAK" != '0' ]]; then
      printf '[%s] 槽位 %s 已恢复健康（此前连续 %s 次异常）。\n' \
             "$(date '+%F %T')" "$ACTIVE_COLOR" "$STREAK"
    fi
    streak_write 0
    exit 0
    ;;
  starting)
    # 刚起来或在 --stop-first 的切换窗口内，属于正常过程，不计入异常。
    printf '[%s] 槽位 %s 正在启动中，继续观察。\n' "$(date '+%F %T')" "$ACTIVE_COLOR"
    exit 0
    ;;
esac

STREAK=$(( STREAK + 1 ))
streak_write "$STREAK"
printf '[%s] 槽位 %s 状态异常：%s（连续第 %s 次）。\n' \
       "$(date '+%F %T')" "$ACTIVE_COLOR" "$HEALTH" "$STREAK"

# ---- 第一级：重启 ----
if (( STREAK == RESTART_AT )); then
  printf '[%s] 连续 %s 次异常，尝试重启槽位 %s。\n' \
         "$(date '+%F %T')" "$STREAK" "$ACTIVE_COLOR"
  "${COMPOSE[@]}" restart "$SVC" >/dev/null 2>&1 || true
  if wait_healthy "$ACTIVE_COLOR" 90; then
    printf '[%s] 重启后恢复正常。\n' "$(date '+%F %T')"
    streak_write 0
  else
    printf '[%s] 重启无效，问题可能不在进程本身。\n' "$(date '+%F %T')"
  fi
  exit 0
fi

# ---- 第二级：切回上一版（默认关闭）----
if (( STREAK >= FAILOVER_AT )); then
  if [[ "$FAILOVER" != '1' ]]; then
    printf '[%s] 已连续 %s 次异常。未开启自动降级（GTD_WATCHDOG_FAILOVER=1 才启用）。\n' \
           "$(date '+%F %T')" "$STREAK"
    printf '[%s] 人工处置：bash deploy/release.sh --status 看状态；\n' "$(date '+%F %T')"
    printf '[%s]            bash deploy/rollback.sh 退回上一版。\n' "$(date '+%F %T')"
    exit 0
  fi

  if [[ -z "$PREVIOUS_IMAGE" ]] || ! docker image inspect "$PREVIOUS_IMAGE" >/dev/null 2>&1; then
    printf '[%s] 想自动降级，但上一版镜像不可用（%s）。需要人工介入。\n' \
           "$(date '+%F %T')" "${PREVIOUS_IMAGE:-未记录}"
    exit 1
  fi

  printf '[%s] 连续 %s 次异常，自动切回上一版：%s（%s）。\n' \
         "$(date '+%F %T')" "$STREAK" "$PREVIOUS_COLOR" "$PREVIOUS_IMAGE"

  export GTD_IMAGE="$PREVIOUS_IMAGE"
  "${COMPOSE[@]}" up -d --no-build "$(color_service "$PREVIOUS_COLOR")" >/dev/null 2>&1 || true

  if wait_healthy "$PREVIOUS_COLOR" 90; then
    caddyfile_generate "$PREVIOUS_COLOR"
    if caddy_reload; then
      # 两个版本互换。注意必须先把旧值存下来再改写，否则会自己覆盖自己
      # （这个顺序错了不会报错，只会把"上一版"记成一个不存在的版本）。
      old_active_color="$ACTIVE_COLOR"
      old_active_image="$ACTIVE_IMAGE"
      ACTIVE_COLOR="$PREVIOUS_COLOR"
      ACTIVE_IMAGE="$PREVIOUS_IMAGE"
      PREVIOUS_COLOR="$old_active_color"
      PREVIOUS_IMAGE="$old_active_image"
      save_state
      streak_write 0
      printf '[%s] 已自动切回上一版并记录状态。出问题的版本仍在 %s 槽位，可用 --status 查看。\n' \
             "$(date '+%F %T')" "$PREVIOUS_COLOR"
      exit 0
    fi
  fi

  printf '[%s] 自动降级失败，需要人工介入。\n' "$(date '+%F %T')"
  exit 1
fi

exit 0
