#!/usr/bin/env bash
#
# GTD4Teams 连续性探测 —— 用"真的打流"来证明发布没有断线。
#
#   bash deploy/probe.sh [目标URL] [持续秒数] [间隔秒] [--allow-gap]
#   例：bash deploy/probe.sh                       # 默认打公网入口 30 秒
#       bash deploy/probe.sh http://127.0.0.1:8788 20
#       bash deploy/probe.sh "" 35 --allow-gap     # 明确允许"丢空档"通过
#
# 输出三件事：总请求数、失败数、**连续成功之间的最大间隔**（毫秒）。
# 最后那个数字才是"是否断线"的真正度量：
#   · 失败数为 0，最大间隔也在正常请求耗时量级 → 确实没有断线；
#   · 出现失败，或最大间隔达到秒级 → 切换窗口内有请求被丢弃。
#
# 退出码（上层脚本靠它区分两类失败，别把它们合成一个）：
#   0  未断线；或带了 --allow-gap 且真的没有失败请求（放行是有记录的）
#   1  出现失败请求，或一次都没打成 —— 确实断线 / 目标不可达
#   2  参数错误
#   3  没有失败请求，但空档超过阈值 —— 连续性不达标
#
# 为什么"只丢空档"也要判不通过（exit 3，与 exit 1 分开）：
# 空档意味着那段时间用户的请求是失败的，只是恰好没被探测打到。
# 若此时仍 exit 0，这条门禁在"探测间隔 > 中断时长"的场景下永远说通过 ——
# 判定器被自己的采样率软化了。要放行必须显式加 --allow-gap，
# 让"我确实接受这次不是零停机"变成一条可检索的命令行，而不是一声沉默。
#
# 发布脚本会在切流前后并发跑它，所以这里要把结果打得足够清楚，
# 免得"切流成功"只是一句自我感觉良好的话。
#
set -Eeuo pipefail

_PROBE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${_PROBE_DIR}/lib.sh"

# ---------------------------------------------------------------- 参数
# 空档阈值：连续两次成功之间超过这个毫秒数，就认为那段窗口里用户是被断掉的。
GAP_THRESHOLD_MS=3000

usage() {
  cat <<'EOF'
用法：bash deploy/probe.sh [目标URL] [持续秒数] [间隔秒] [--allow-gap]

  目标URL      留空则按 .env 自动拼（GTD_DOMAIN / GTD_HTTP_PORT / GTD_PROBE_PATH）
  持续秒数     默认 30
  间隔秒       默认 0.2
  --allow-gap  没有失败请求、但出现超阈值空档时，降级为警告并返回 0（默认不降级）
  -h, --help   显示本帮助

退出码：0 未断线（或已按 --allow-gap 放行） / 1 有失败请求或一次都没打成 /
        2 参数错误 / 3 无失败请求但空档超阈值（连续性不达标）
EOF
}

# 显式开关解析。位置参数保持旧版顺序与语义（release.sh 是按位置传参的），
# 所以不用 getopts —— 它会把"空字符串 URL"当成缺参，而空串正是"用 .env 自己拼"的写法。
ALLOW_GAP=0
declare -a _PROBE_POS=()
while (( $# > 0 )); do
  case "$1" in
    --allow-gap) ALLOW_GAP=1 ;;
    -h|--help)   usage; exit 0 ;;
    --)          shift; _PROBE_POS+=("$@"); break ;;
    -*)          printf '未知参数：%s\n\n' "$1" >&2; usage >&2; exit 2 ;;
    *)           _PROBE_POS+=("$1") ;;
  esac
  shift
done

URL=''
DURATION='30'
INTERVAL='0.2'
if (( ${#_PROBE_POS[@]} > 0 )); then URL="${_PROBE_POS[0]}"; fi
if (( ${#_PROBE_POS[@]} > 1 )); then DURATION="${_PROBE_POS[1]}"; fi
if (( ${#_PROBE_POS[@]} > 2 )); then INTERVAL="${_PROBE_POS[2]}"; fi

# 参数校验先做：下面会直接把 DURATION 塞进算术表达式，
# 非法值会以一句看不出原因的 `syntax error` 死掉。
if ! [[ "$DURATION" =~ ^[0-9]+$ ]] || (( 10#$DURATION <= 0 )); then
  printf '持续秒数必须是正整数：%s\n\n' "$DURATION" >&2; usage >&2; exit 2
fi
if ! [[ "$INTERVAL" =~ ^[0-9]*\.?[0-9]+$ ]]; then
  printf '间隔必须是数字（秒，可带小数）：%s\n\n' "$INTERVAL" >&2; usage >&2; exit 2
fi
DURATION=$(( 10#$DURATION ))

[[ -n "$URL" ]] || URL="$(probe_url)"
probe_curl_args

# 毫秒时间戳。**不要写成 `date '+%s%3N'`** —— 那个写法只在 Git Bash(MSYS) 上得到 3 位毫秒；
# GNU coreutils 的 date 会忽略 %N 的宽度修饰，直接吐出 9 位纳秒，于是"毫秒"实际是纳秒，
# 单位差 1000 倍：DURATION*1000 的比较立刻成立，打流循环**一次都不跑**，
# 报告成"总请求 0 次"。这个 bug 在 Windows 开发机上永远看不见，一上 Linux 就现形（实测）。
# EPOCHREALTIME 是 bash 5 内置变量（秒.微秒），两边都可靠，且不用 fork 子进程。
now_ms() {
  local t=''
  if [[ -n "${EPOCHREALTIME:-}" ]]; then
    t="${EPOCHREALTIME//[.,]/}"          # 去掉小数点（某些 locale 用逗号）
    printf '%s' "$(( 10#$t / 1000 ))"    # 微秒 -> 毫秒
    return 0
  fi
  t="$(date +%s%N 2>/dev/null || true)"
  if [[ "$t" =~ ^[0-9]+$ ]]; then
    printf '%s' "$(( 10#$t / 1000000 ))"
  else
    printf '%s' "$(( $(date +%s) * 1000 ))"   # 最后兜底：精度降到秒
  fi
}

START_MS="$(now_ms)"
END_MS=$(( START_MS + DURATION * 1000 ))

TOTAL=0
FAILED=0
LAST_OK_MS=0
MAX_GAP_MS=0
declare -a _FAIL_SAMPLES=()

printf '  打流目标：%s\n' "$URL"
printf '  持续时间：%s 秒，间隔 %s 秒\n' "$DURATION" "$INTERVAL"

while :; do
  TICK_MS="$(now_ms)"
  if (( TICK_MS >= END_MS )); then
    break
  fi

  code=''
  # --noproxy：探测目标恒为本机（回环端口，或解析到回环的域名 443）。
  # 若环境里存在 HTTP_PROXY，curl 会把请求交给代理，拿到的是代理的结果而不是本机服务的 ——
  # 那样的"失败"会把人引向完全错误的方向。
  code="$(curl --noproxy '*' "${PROBE_ARGS[@]}" -s -o /dev/null -w '%{http_code}' --max-time 5 "$URL" 2>/dev/null)" || true
  code="${code:-000}"

  TOTAL=$(( TOTAL + 1 ))
  NOW_MS="$(now_ms)"

  if [[ "$code" == '200' ]]; then
    if (( LAST_OK_MS > 0 )); then
      GAP_MS=$(( NOW_MS - LAST_OK_MS ))
      if (( GAP_MS > MAX_GAP_MS )); then
        MAX_GAP_MS=$GAP_MS
      fi
    fi
    LAST_OK_MS="$NOW_MS"
  else
    FAILED=$(( FAILED + 1 ))
    if (( ${#_FAIL_SAMPLES[@]} < 5 )); then
      _FAIL_SAMPLES+=("${code}@$(date '+%H:%M:%S')")
    fi
  fi

  sleep "$INTERVAL"
done

ELAPSED_MS=$(( $(now_ms) - START_MS ))
SUCCEEDED=$(( TOTAL - FAILED ))

printf '\n  ────────────────────────────────────────────────\n'
printf '  总请求 %d 次  ·  成功 %d  ·  失败 %d  ·  用时 %.1f 秒\n' \
       "$TOTAL" "$SUCCEEDED" "$FAILED" "$(awk "BEGIN{printf \"%.1f\", $ELAPSED_MS/1000}")"
printf '  连续成功之间的最大间隔：%d 毫秒\n' "$MAX_GAP_MS"

if (( FAILED > 0 )); then
  printf '  失败样本：%s\n' "${_FAIL_SAMPLES[*]}"
fi

printf '  ────────────────────────────────────────────────\n'

if (( TOTAL == 0 )); then
  printf '  \033[33m[!] 一次都没打成，检查目标地址与网络。\033[0m\n'
  exit 1
fi
if (( FAILED > 0 )); then
  printf '  \033[31m[x] 切换窗口内出现失败请求：确实断线了。\033[0m\n'
  printf '      排查方向：Caddy 是否真的热加载成功、旧槽位是否被提前停掉、\n'
  printf '                新槽位是否在做耗时的初始化。必要时执行 rollback.sh。\n'
  exit 1
fi
if (( MAX_GAP_MS > GAP_THRESHOLD_MS )); then
  # 这里**不能**只打印一句"值得看一眼"然后 exit 0：
  # 空档说明那几秒里用户的请求是失败的（只是没被探测打到）。
  # 用 exit 3 与"有失败请求"（exit 1）区分开，上层才能说清是哪一种不达标；
  # 要放行必须显式 --allow-gap，让"接受这次不是零停机"留下痕迹。
  if (( ALLOW_GAP )); then
    printf '  \033[33m[ ! ] 全程无失败请求，但出现 %d 毫秒空档（阈值 %d 毫秒）：连续性不达标。\033[0m\n' \
           "$MAX_GAP_MS" "$GAP_THRESHOLD_MS"
    printf '  \033[33m      已按 --allow-gap 放行（本结论不构成零停机证明，请在发布记录里注明）。\033[0m\n'
    exit 0
  fi
  printf '  \033[31m[ !! ] 全程无失败请求，但出现 %d 毫秒空档（阈值 %d 毫秒）：连续性不达标。\033[0m\n' \
         "$MAX_GAP_MS" "$GAP_THRESHOLD_MS"
  printf '      空档意味着那段时间用户的请求是失败的，只是恰好没被探测打到（采样率掩盖了中断）。\n'
  printf '      排查方向：切换动作本身是否有停顿（Caddy 重载 / 旧槽位提前摘除）、宿主是否在做重活。\n'
  printf '      确认要接受这次不达标，重跑时加 --allow-gap。\n'
  exit 3
fi

printf '  \033[32m[ ok ] 全程 %d 次请求零失败，最大空档 %d 毫秒：未断线。\033[0m\n' "$TOTAL" "$MAX_GAP_MS"
exit 0
