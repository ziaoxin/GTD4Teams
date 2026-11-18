#!/usr/bin/env bash
#
# GTD4Teams 第 0 期配置止血 —— 可执行验收
#
#   bash deploy/verify-phase0.sh
#
# 验收对象是 docs/process/01_现状评估与改善路线图.md §4「第 0 期」的五条动作。
# 之所以要写成脚本：这五条原本是"口头验收"，而其中 0-4 是整个安全论证的地基
# （所有"信任代理"的结论都建立在 Caddy 覆盖而非追加 XFF 之上），
# 靠人肉看是不长久的 —— 它必须每次部署后都能被重新证明一次。
#
# 与 smoke.sh 的分工：
#   smoke.sh         打**槽位端口**，验"这一版代码本身对不对"，发布门禁用
#   verify-phase0.sh 打**公网入口**，验"这一层壳配得对不对"，部署后 / 变更后跑
#
# 全部检查都不写库（0-4 会产生 10 次失败登录，用的是不存在的邮箱，
# 只会占用限速计数，15 分钟后自然过期）。
#
# 退出码：0 = 全部通过（SKIP 不算失败）；1 = 有项不通过；3 = 0-4 不通过（地基塌了）
#
set -Eeuo pipefail

_P0_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${_P0_DIR}/lib.sh"

cd "$ROOT_DIR"

PASS=0
FAIL=0
SKIP=0

check() {  # check <描述> <0=通过/1=失败> [附加信息]
  local desc="$1" result="$2" detail="${3:-}"
  if [[ "$result" == '0' ]]; then
    PASS=$(( PASS + 1 ))
    printf '    \033[32m✓\033[0m %s%s\n' "$desc" "${detail:+  —— $detail}"
  else
    FAIL=$(( FAIL + 1 ))
    printf '    \033[31m✗\033[0m %s%s\n' "$desc" "${detail:+  —— $detail}"
  fi
}

skip() {  # skip <描述> <原因>
  SKIP=$(( SKIP + 1 ))
  printf '    \033[33m–\033[0m %s  —— 跳过：%s\n' "$1" "$2"
}

note() {
  printf '      \033[2m%s\033[0m\n' "$*"
}

need_docker

DOMAIN="$(read_env GTD_DOMAIN '')"
ALLOW_SIGNUP="$(read_env GTD_ALLOW_SIGNUP 0)"
BASE="${1:-}"
if [[ -z "$BASE" ]]; then
  # 只要 origin，不要路径。别用 "${完全URL%/*}" 去砍 —— 它只砍掉最后一段，
  # "http://host:8080/api/health" 会变成 "http://host:8080/api"，于是下面每个
  # 请求都被拼成 /api/api/...（这个 bug 真的写出来过一次，且让 0-2 假通过）。
  if [[ -n "$DOMAIN" ]]; then
    BASE="https://${DOMAIN}"
  else
    BASE="http://127.0.0.1:$(read_env GTD_HTTP_PORT 80)"
  fi
fi
BASE="${BASE%/}"

# 有域名时走 HTTPS 并把它解析到回环：验收的是"这台机器上的 Caddy"，
# 不该受 DNS 与安全组影响（那属于上线预检，见 09 §2）。
probe_curl_args

p0_get() {  # p0_get <path> -> 状态码
  local code=''
  code="$(curl -s --noproxy '*' "${PROBE_ARGS[@]}" -o /dev/null -w '%{http_code}' \
          --max-time 8 "$BASE$1" 2>/dev/null)" || true
  printf '%s' "${code:-000}"
}

p0_post() {  # p0_post <path> <json> [额外 -H ...]
  local path="$1" body="$2"; shift 2
  local code=''
  code="$(curl -s --noproxy '*' "${PROBE_ARGS[@]}" "$@" -o /dev/null -w '%{http_code}' \
          --max-time 10 -X POST -H 'Content-Type: application/json' \
          --data "$body" "$BASE$path" 2>/dev/null)" || true
  printf '%s' "${code:-000}"
}

printf '\n  第 0 期验收目标：%s\n' "$BASE"
printf '  （域名：%s ｜ 自助注册：%s）\n' "${DOMAIN:-未配置（明文 HTTP）}" \
       "$([[ "$ALLOW_SIGNUP" == '1' ]] && echo 开放 || echo 关闭)"
printf '  ────────────────────────────────────────────────\n'

# ============================================================ 0-1 端点止血
printf '\n  [1/5] 0-1 越权端点（Caddy 层止血已于 2026-11-15 撤销，改验代码侧双门）\n'

# 2026-11-15 变更：Caddy 那两条 404 止血规则已删除（代码侧已有 export_visible 与
# h_llm_config_set 双重门）。判据因此从"404 = 被 Caddy 拦掉"改为
# "匿名 = 401（端点存在但无会话）"；同时必须**不是** 404（规则没撤干净）
# 也**不是** 200（越权面回来了）。
code="$(p0_get /api/export)"
r=0; [[ "$code" == '401' ]] || r=1
check 'GET  /api/export 匿名访问返回 401（非 404、非 200）' "$r" "实际 $code"
note '止血规则已撤；越权面由代码侧 export_visible 收口'

code="$(p0_post /api/llm/config '{"baseUrl":"http://169.254.169.254/latest"}')"
r=0; [[ "$code" == '401' ]] || r=1
check 'POST /api/llm/config 匿名访问返回 401（非 404、非 200）' "$r" "实际 $code"
note '止血规则已撤；写配置由代码侧 h_llm_config_set（ADMIN_ONLY）收口'

# 反向确认：只挡了该挡的，没把整个 LLM 面板一起挡死。
# 未登录访问应当是 401（端点存在、但没有会话），不是 404（被拦掉）。
code="$(p0_get /api/llm/config)"
r=0; [[ "$code" == '401' ]] || r=1
check 'GET  /api/llm/config 未被误伤（401 而非 404）' "$r" "实际 $code"

code="$(p0_get /api/llm/models)"
r=0; [[ "$code" == '401' ]] || r=1
check 'GET  /api/llm/models 未被误伤（401 而非 404）' "$r" "实际 $code"

# ============================================================ 0-2 注册策略
printf '\n  [2/5] 0-2 自助注册已下线\n'

# CR-2026-001（2026-11-14）把注册端点从 ROUTES **代码级移除**了，
# 所以正确结果不是 403 SIGNUP_DISABLED，而是 404 —— 端点根本不存在。
# 这两者的区别不是"数字不同"，而是**能不能被配置打开**：
#   403 = 端点存在、被策略拒绝 → 改 .env 就能重新打开
#   404 = 端点不存在 → 没有任何配置能把它打开（更强，也是现在的事实）
code="$(p0_post /api/auth/register \
  "{\"email\":\"phase0-probe-$$@invalid.test\",\"password\":\"Phase0Probe!1\",\"name\":\"probe\"}")"
r=0; [[ "$code" == '404' ]] || r=1
check 'POST /api/auth/register 返回 404（端点已从代码移除）' "$r" "实际 $code"

# 反向确认：check-email 这个免鉴权的账号枚举口也一并下线了。
code="$(p0_get '/api/auth/check-email?email=probe@invalid.test')"
r=0; [[ "$code" == '404' ]] || r=1
check 'GET  /api/auth/check-email 返回 404（枚举口已下线）' "$r" "实际 $code"

# 这条防两件事：① 有人以为改 GTD_ALLOW_SIGNUP=1 就能开注册；
#               ② 以后有人把端点加回来，但门禁仍然只说"不是 201"，于是没人发现。
if [[ "$ALLOW_SIGNUP" == '1' ]]; then
  note 'GTD_ALLOW_SIGNUP=1 但注册仍应为 404 —— 该配置项在 CR-2026-001 后已失效'
  code="$(p0_post /api/auth/register \
    "{\"email\":\"phase0-probe2-$$@invalid.test\",\"password\":\"Phase0Probe!1\",\"name\":\"probe\"}")"
  r=0; [[ "$code" == '404' ]] || r=1
  check 'GTD_ALLOW_SIGNUP=1 时注册仍为 404（该配置项已失效）' "$r" "实际 $code"
fi

# ============================================================ 0-3 HTTPS
printf '\n  [3/5] 0-3 HTTPS / HSTS\n'

CERTS_DIR="${ROOT_DIR}/deploy/run/certs"
HTTPS_PORT="$(read_env GTD_HTTPS_PORT 443)"

if [[ -f "${CERTS_DIR}/tls.crt" && -f "${CERTS_DIR}/tls.key" ]]; then
  # ---- 手动证书（自签名或运维上传）：真验 HTTPS，而不是跳过 ----
  # ⚠️ 本项通过 **不等于 G-03 通过**。G-03 的判据是「真实域名 + **受信任**证书 + HSTS」，
  #    而这里的典型形态是 localhost + 自签名 —— 浏览器会报警告，不满足"公网可信"。
  #    见本节末尾的提示行，别让 "0-3 ✓" 被当成公网 HTTPS 门禁已过。
  cert_subject="$(openssl x509 -in "${CERTS_DIR}/tls.crt" -noout -subject 2>/dev/null | sed 's/^subject=//')"
  cert_san="$(openssl x509 -in "${CERTS_DIR}/tls.crt" -noout -ext subjectAltName 2>/dev/null \
              | tail -n +2 | tr -d ' \n')"
  note "使用手动证书：${cert_subject:-未知}；SAN: ${cert_san:-未解析}"

  code="$(curl -s --noproxy '*' -k -o /dev/null -w '%{http_code}' --max-time 10 \
          "https://localhost:${HTTPS_PORT}/api/health" 2>/dev/null || printf '000')"
  r=0; [[ "$code" == '200' ]] || r=1
  check "HTTPS 入口可用（https://localhost:${HTTPS_PORT}/api/health）" "$r" "实际 $code"

  hsts="$(curl -s --noproxy '*' -k -D - -o /dev/null --max-time 10 \
          "https://localhost:${HTTPS_PORT}/api/health" 2>/dev/null \
          | tr -d '\r' | sed -n 's/^[Ss]trict-[Tt]ransport-[Ss]ecurity:[[:space:]]*//p' | head -1)"
  r=0; [[ -n "$hsts" ]] || r=1
  check 'HTTPS 响应带 Strict-Transport-Security' "$r" "${hsts:-缺失}"

  # SAN 必须覆盖访问用的主机名：现代浏览器/curl 只看 SAN，不看 CN
  r=0; [[ "$cert_san" == *localhost* ]] || r=1
  check '证书 SAN 含 localhost（现代浏览器不看 CN）' "$r" "${cert_san:-未解析}"

  # 明文入口必须仍然可用且**没有被跳转**（release.sh 探测/冒烟打的都是它）
  code="$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' --max-time 8 \
          "http://localhost:$(read_env GTD_HTTP_PORT 80)/api/health" 2>/dev/null || printf '000')"
  r=0; [[ "$code" == '200' ]] || r=1
  check '明文入口仍为 200（HTTP/HTTPS 并存，未被跳转破坏）' "$r" "实际 $code"

  note '⚠️ 本项通过只代表「HTTPS 已启用且证书可用」。**自签名证书不满足 G-03 的"受信任证书"要求**，'
  note '   真实上线仍需：真实域名 + A 记录指向本机 + Let us Encrypt（或同等级 CA）受信任证书，浏览器无告警。'
elif [[ -z "$DOMAIN" ]]; then
  skip 'HTTPS / HSTS' '未配置证书，也未配置 GTD_DOMAIN（明文 HTTP 场景）'
  note '公网实例必须配域名：A 记录指向本机 → .env 填 GTD_DOMAIN → 重载 Caddy。'
  note '未配域名/证书时登录口令与会话 Cookie 明文过网，这一条在公网不可豁免。'
else
  code="$(curl -s --noproxy '*' -k --resolve "${DOMAIN}:443:127.0.0.1" -o /dev/null \
          -w '%{http_code}' --max-time 10 "https://${DOMAIN}/api/health" 2>/dev/null || printf '000')"
  r=0; [[ "$code" == '200' ]] || r=1
  check 'HTTPS 入口可用（证书已签发）' "$r" "实际 $code"

  hsts="$(curl -s --noproxy '*' -k --resolve "${DOMAIN}:443:127.0.0.1" -D - -o /dev/null \
          --max-time 10 "https://${DOMAIN}/api/health" 2>/dev/null \
          | tr -d '\r' | sed -n 's/^[Ss]trict-[Tt]ransport-[Ss]ecurity:[[:space:]]*//p' | head -1)"
  r=0; [[ -n "$hsts" ]] || r=1
  check '响应带 Strict-Transport-Security' "$r" "${hsts:-缺失}"

  # 明文端口必须 308 到 HTTPS，否则用户第一次访问仍走明文。
  code="$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' --max-time 8 "http://${DOMAIN}/" 2>/dev/null || printf '000')"
  r=0; [[ "$code" == '308' || "$code" == '301' ]] || r=1
  check 'HTTP 端口自动跳转 HTTPS（308）' "$r" "实际 $code"
fi

# ============================================================ 0-4 XFF 覆盖语义
printf '\n  [4/5] 0-4 Caddy 的 X-Forwarded-For 是"覆盖"而非"追加"\n'
note '判据：同一个邮箱连打 11 次错误登录，每次换一个伪造 XFF。'
note '若 Caddy 覆盖 → 限流键恒为 (邮箱, 真实IP) → 第 11 次必须 429。'
note '若 Caddy 追加 → 伪造值排在前面 → 每次都是新键 → 全程 401，限流等于白做。'

P0_EMAIL="phase0-xff-$(date +%s)-$$@invalid.test"
codes=''
for i in $(seq 1 11); do
  c="$(p0_post /api/auth/login \
        "{\"email\":\"${P0_EMAIL}\",\"password\":\"definitely-wrong-${i}\"}" \
        -H "X-Forwarded-For: 10.0.0.${i}")"
  codes="${codes}${c} "
done
note "依次：${codes% }"

eleventh="$(printf '%s' "$codes" | awk '{print $11}')"
early429=0
idx=0
for c in $codes; do
  idx=$(( idx + 1 ))
  if [[ "$c" == '429' && "$idx" -lt 11 ]]; then early429=$idx; fi
done

r=0; [[ "$eleventh" == '429' ]] || r=1
check '第 11 次错误登录返回 429（覆盖语义成立）' "$r" "实际 ${eleventh}"

if [[ "$early429" != '0' ]]; then
  note "提示：第 ${early429} 次就已 429 —— 该邮箱此前已有失败记录，说明计数是累加的，不影响结论。"
fi

XFF_BROKEN=0
if [[ "$eleventh" != '429' ]]; then
  XFF_BROKEN=1
fi

# ============================================================ 0-5 密钥轮换
printf '\n  [5/5] 0-5 LLM 密钥轮换\n'
if [[ -n "$(read_env GTD_LLM_API_KEY '')" ]]; then
  skip 'LLM API Key 已轮换' '这是人工动作，脚本无法判定新旧关系'
  note '按"密钥已外带"处理：在服务商后台吊销旧 key、签发新 key，'
  note '再写入 .env 并重启两个槽位（界面里删除密钥不会清掉另一进程内存里的旧值）。'
else
  skip 'LLM API Key 轮换' '当前未配置任何 LLM 密钥，无密钥可轮换'
fi

# ============================================================ 结论
printf '\n  ────────────────────────────────────────────────\n'
if [[ "$XFF_BROKEN" == '1' ]]; then
  printf '  \033[31m地基不成立：Caddy 在"追加"XFF，不是你预期的"覆盖"。\033[0m\n'
  printf '  立即动作：把 .env 的 GTD_TRUST_PROXY 改回 0 并重载，然后修 Caddy 配置。\n'
  printf '  （改回 0 的代价：所有人被算成同一个 IP，登录限流会误伤，但不会被绕过。）\n\n'
  exit 3
fi

if [[ "$FAIL" -eq 0 ]]; then
  printf '  \033[32m第 0 期验收通过：%d 项通过，%d 项跳过\033[0m\n\n' "$PASS" "$SKIP"
  exit 0
fi
printf '  \033[31m第 0 期验收失败：%d 项通过，%d 项失败，%d 项跳过\033[0m\n\n' "$PASS" "$FAIL" "$SKIP"
exit 1
