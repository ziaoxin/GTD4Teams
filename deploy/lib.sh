#!/usr/bin/env bash
# ============================================================================
# GTD4Teams 部署脚本公共库
#
# 被 install.sh / release.sh / rollback.sh / backup.sh / smoke.sh / watchdog.sh
# 共同 source。**不要直接执行本文件。**
#
# 集中在这里的是三件各脚本都做、但很容易各写一版写歪的事：
#   1. 认命令   —— docker compose 还是 docker-compose，Docker 是否可用
#   2. 认槽位   —— 现在哪个颜色是活跃的、它跑的镜像是哪个（状态落在 deploy/run/）
#   3. 切流     —— 生成 Caddy 上游配置并**平滑重载**（而不是重启容器）
#
# 设计约束：只依赖 bash + docker + 常见 GNU 工具（sed/awk/curl/tr）。
# 不依赖 python3 或 node —— 部署机不一定装，测试门禁另有地方跑（见 release.sh）。
# ============================================================================

set -Eeuo pipefail

# ---------------------------------------------------------------- 路径
_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${_LIB_DIR}/.." && pwd)"
RUN_DIR="${ROOT_DIR}/deploy/run"          # 运行期生成物：Caddy 配置、槽位状态
STATE_FILE="${RUN_DIR}/state.env"
CADDY_TEMPLATE="${ROOT_DIR}/deploy/Caddyfile.template"
CADDY_CONFIG="${RUN_DIR}/Caddyfile"

# ---------------------------------------------------------------- 输出
log()  { printf '\033[36m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
ok()   { printf '\033[32m[ ok ]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[  ! ]\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[  x ]\033[0m %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 环境校验
need_docker() {
  command -v docker >/dev/null 2>&1 \
    || die '未找到 docker。安装：curl -fsSL https://get.docker.com | sh'

  docker info >/dev/null 2>&1 \
    || die 'Docker 守护进程未运行，或当前用户无权访问（试试 sudo，或把用户加入 docker 组）'

  if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
  elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE=(docker-compose)
  else
    die '未找到 docker compose。Ubuntu/Debian：apt-get install -y docker-compose-plugin'
  fi
}

require_env_file() {
  [[ -f "${ROOT_DIR}/.env" ]] \
    || die "缺少 ${ROOT_DIR}/.env。首次部署请先执行：sudo bash deploy/install.sh"
}

# 让 compose 自己校验编排文件。
#
# 为什么需要它：docker-compose.yml 里用了 YAML 锚点（`<<: *app`）让两个槽位的配置
# 完全一致。锚点写错、变量没有默认值、缩进错位，都是**语法能过、语义不对**的问题 ——
# 表现可能是"两个槽位配置不一样"，而那种不一致在切换那一刻才会暴露。
# 本机无法验证（这台开发机没有 docker），所以把权威校验放到部署机的预检里：
# 它跑的就是最终会用来启动服务的那套实现。
compose_config_check() {
  if ! "${COMPOSE[@]}" config -q; then
    die '编排文件 docker-compose.yml 校验未通过，已中止。'
  fi
}

# 读 .env 里的一个键。没有该键时返回 fallback。
# 顺带容忍两种常见污染：CRLF 行尾（在 Windows 上编辑过 .env 就会这样）与加引号的值。
read_env() {
  local key="$1" fallback="${2:-}" value=''
  if [[ -f "${ROOT_DIR}/.env" ]]; then
    value="$(sed -n "s/^${key}=//p" "${ROOT_DIR}/.env" | tail -1)"
  fi
  value="${value%$'\r'}"
  value="${value%\"}"; value="${value#\"}"
  value="${value%\'}"; value="${value#\'}"
  value="$(printf '%s' "$value" | tr -d '[:space:]')"
  printf '%s' "${value:-$fallback}"
}

# ---------------------------------------------------------------- 槽位
color_service() { printf 'app-%s' "$1"; }

color_port() {
  case "$1" in
    blue)  read_env GTD_BLUE_PORT  8788 ;;
    green) read_env GTD_GREEN_PORT 8789 ;;
    *) die "未知槽位：$1（只接受 blue / green）" ;;
  esac
}

other_color() {
  if [[ "$1" == 'blue' ]]; then printf 'green'; else printf 'blue'; fi
}

# ---------------------------------------------------------------- 状态
# 状态文件是"当前哪个槽位活跃"的唯一权威。发布脚本靠它算出目标槽位，
# 回滚脚本靠它找上一版镜像 —— 所以每次成功切换后都必须写回去。
load_state() {
  ACTIVE_COLOR=''; ACTIVE_IMAGE=''
  PREVIOUS_COLOR=''; PREVIOUS_IMAGE=''; LAST_RELEASE_AT=''
  if [[ -f "$STATE_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$STATE_FILE"
  fi
}

save_state() {
  mkdir -p "$RUN_DIR"
  {
    printf '# 由 deploy/lib.sh 维护，请勿手工编辑（发布/回滚脚本会读写它）。\n'
    printf "ACTIVE_COLOR='%s'\n"   "${ACTIVE_COLOR}"
    printf "ACTIVE_IMAGE='%s'\n"   "${ACTIVE_IMAGE}"
    printf "PREVIOUS_COLOR='%s'\n" "${PREVIOUS_COLOR}"
    printf "PREVIOUS_IMAGE='%s'\n" "${PREVIOUS_IMAGE}"
    printf "LAST_RELEASE_AT='%s'\n" "$(date '+%F %T')"
  } > "${STATE_FILE}.tmp"
  mv "${STATE_FILE}.tmp" "$STATE_FILE"
}

# ---------------------------------------------------------------- 容器查询
svc_cid() {
  local svc="$1"
  "${COMPOSE[@]}" ps -q "$svc" 2>/dev/null | head -1 || true
}

svc_running() {
  local cid; cid="$(svc_cid "$1")"
  [[ -n "$cid" ]] || return 1
  [[ "$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null || echo false)" == 'true' ]]
}

# healthy / unhealthy / starting / none（没有 healthcheck）/ absent（容器不存在）
svc_health() {
  local cid; cid="$(svc_cid "$1")"
  if [[ -z "$cid" ]]; then
    printf 'absent'
    return 0
  fi
  docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null \
    || printf 'unknown'
}

svc_image() {
  local cid; cid="$(svc_cid "$1")"
  [[ -n "$cid" ]] || return 0
  docker inspect -f '{{.Config.Image}}' "$cid" 2>/dev/null || true
}

# 等待某个槽位变为 healthy。返回 0 = 就绪，1 = 超时。
# 刚 `up -d` 之后容器可能还没注册进 compose，所以 absent 要给一段宽限期再判定为失败。
wait_healthy() {
  local color="$1" timeout="${2:-120}" waited=0 status='' grace=0
  local svc; svc="$(color_service "$color")"
  while (( waited < timeout )); do
    status="$(svc_health "$svc")"
    case "$status" in
      healthy) printf '\r\033[K'; return 0 ;;
      absent)
        grace=$(( grace + 1 ))
        if (( grace > 10 )); then
          printf '\r\033[K'
          warn "槽位 ${color} 的容器始终没有出现（等了 $(( grace * 2 )) 秒）"
          return 1
        fi
        ;;
      *) grace=0 ;;
    esac
    printf '\r\033[K        槽位 %s 状态：%s（已等 %ss）' "$color" "$status" "$waited"
    sleep 2
    waited=$(( waited + 2 ))
  done
  printf '\r\033[K'
  return 1
}

# ---------------------------------------------------------------- HTTP 探针
# 一律加 --noproxy：我们探的目标永远是本机（回环端口，或把域名解析到回环的 443），
# 一旦环境里存在 HTTP_PROXY，curl 会把请求交给代理，代理再去解析那个域名 ——
# 拿回来的就完全不是本机服务的结果（实测过一次：代理返回 502，
# 于是"服务全挂"的假象掩盖了真正的问题）。
http_code() {
  local code=''
  code="$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' --max-time 6 "$1" 2>/dev/null)" || true
  printf '%s' "${code:-000}"
}

http_body() {
  curl -s --noproxy '*' --max-time 8 "$1" 2>/dev/null || true
}

# 从扁平 JSON 里取一个字符串字段。故意不依赖 jq / python3：
# 这两个在干净的部署机上都不一定有，而我们要取的就是 health 里的一两个字段。
json_str() {
  printf '%s' "$1" | sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p"
}

# ---------------------------------------------------------------- Caddy 切流
# 生成 Caddy 配置：把活跃槽位放在上游列表第一位（lb_policy first 只走第一个可用的），
# 备用槽位放第二位兜底。
#
# 对外暴露的方式有三种形态（决定写在模板内容**之前**的站点块）：
#   · 有手动证书（deploy/run/certs/tls.crt + tls.key）→ :80 与 :443 **并存**，不跳转
#   · 有 GTD_DOMAIN、无证书                          → <域名>（ACME 自动证书 + 80→443 跳转）
#   · 两者都没有                                     → :80（明文，与引入 HTTPS 之前完全一致）
# 手动证书优先于 ACME：证书是运维明确放进来的，不该被自动申请悄悄取代。
caddyfile_generate() {
  local active="$1" mode="${2:-}" other='' domain='' upstreams='' line='' trimmed=''
  local certs_dir cert crt key manual=0 hsts=''
  [[ -f "$CADDY_TEMPLATE" ]] || die "缺少配置模板：${CADDY_TEMPLATE}"
  other="$(other_color "$active")"
  domain="$(read_env GTD_DOMAIN '')"

  certs_dir="${ROOT_DIR}/deploy/run/certs"
  crt="${certs_dir}/tls.crt"
  key="${certs_dir}/tls.key"
  if [[ -f "$crt" && -f "$key" ]]; then
    manual=1
  else
    # 半个证书比没有更糟：Caddy 会在 :443 上起不来，而错误信息指不到"少了一个文件"。
    if [[ -f "$crt" || -f "$key" ]]; then
      warn "证书不完整：${crt} 与 ${key} 必须同时存在。本次按'无手动证书'处理。"
      warn "  补齐方式：bash deploy/gen-selfsigned.sh   或上传成对的 tls.crt/tls.key"
    fi
  fi
  hsts='Strict-Transport-Security "max-age=31536000; includeSubDomains"'

  # ---- 证书指纹 + 版本化文件名（这是"换证书真的生效"的关键）----
  # 坑：`tls /certs/tls.crt /certs/tls.key` 这个路径在换证书前后**完全一样**，
  # 于是 caddy reload 判定 "config is unchanged" 并跳过整个加载 ——
  # 内存里仍是旧证书，而脚本打印"应用成功"（实测：对外 CN 还是旧值）。
  # 注释里写指纹也没用：Caddyfile 适配成 JSON 时注释会被丢掉，配置仍然相同。
  # 解法：把证书复制成 **带指纹的文件名** 并在配置里引用它 —— 路径变了就是语义变化，
  # reload 才会真的重新加载证书。`cp` 走的是目录挂载，不存在 inode 问题。
  cert_ref_crt=''
  cert_ref_key=''
  cert_base=''
  if (( manual )); then
    local fp='' short=''
    fp="$(cert_fingerprint_file "$crt")"
    if [[ -n "$fp" ]]; then
      short="${fp:0:16}"
      cert_base="tls-${short}"
      cert_ref_crt="${certs_dir}/${cert_base}.crt"
      cert_ref_key="${certs_dir}/${cert_base}.key"
      cp -f "$crt" "$cert_ref_crt" 2>/dev/null || die "复制证书失败：${cert_ref_crt}"
      cp -f "$key" "$cert_ref_key" 2>/dev/null || die "复制私钥失败：${cert_ref_key}"
      chmod 644 "$cert_ref_crt" 2>/dev/null || true
      chmod 600 "$cert_ref_key" 2>/dev/null || true
      # 清掉旧版本（保留当前这一对）；`tls-*` 不会匹配到 tls.crt/tls.key 本身
      local old
      for old in "${certs_dir}"/tls-*.crt "${certs_dir}"/tls-*.key; do
        [[ -e "$old" ]] || continue
        [[ "$old" == "$cert_ref_crt" || "$old" == "$cert_ref_key" ]] && continue
        rm -f "$old" 2>/dev/null || true
      done
    else
      # 没有 openssl 之类的情况：退回直接用原文件（换证书不会生效，但至少能跑起来）
      warn '取不到证书指纹（缺 openssl？）：tls 将直接引用 tls.crt/tls.key，换证书后需重建 caddy 才生效。'
      cert_base='tls'
      cert_ref_crt="$crt"
      cert_ref_key="$key"
    fi
  else
    # 证书被删除/未配置 → HTTPS 关闭。清掉之前按指纹留下的副本：
    # 那些私钥副本已经没有任何配置引用，留着只是把私钥摊在磁盘上。
    # （`tls-*` 不会匹配 tls.crt / tls.key 本身，不会误删后端要写的原文件。）
    local stale
    for stale in "${certs_dir}"/tls-*.crt "${certs_dir}"/tls-*.key; do
      [[ -e "$stale" ]] || continue
      rm -f "$stale" 2>/dev/null || true
    done
  fi

  if (( manual )) && [[ -n "$domain" ]]; then
    warn "同时存在 GTD_DOMAIN=${domain} 与手动证书：按手动证书提供服务。"
    warn "  若这张证书不是给 ${domain} 签的，浏览器会报证书不匹配 —— 请确认或清掉 deploy/run/certs/。"
  fi

  # 两个上游必须**写在同一行**（见模板顶部说明）：Caddyfile 里换行会终结指令，
  # 参数表跨行会被解析成两条指令，直接报语法错。
  #
  # mode=solo：只留活跃槽位。用在"引流结束、准备停掉旧槽位"那一步 ——
  # 只要列表里还有那台正在关机的容器，Caddy 就有机会把请求转给它并丢一个 502。
  if [[ "$mode" == 'solo' ]]; then
    upstreams="app-${active}:8787"
  else
    upstreams="app-${active}:8787 app-${other}:8787"
  fi

  mkdir -p "$RUN_DIR"

  # 经典陷阱：compose 里把"宿主机上还不存在的文件"挂进容器时，docker 会**建一个同名目录**。
  # 之后 Caddy 会因为挂载点是个目录而启动失败，报错信息完全指不到真正的原因。
  if [[ -d "$CADDY_CONFIG" ]]; then
    die "${CADDY_CONFIG} 是一个目录（多半是首次 up 时该文件还不存在，被 docker 建成了目录）。
     删掉它再重试：rm -rf '${CADDY_CONFIG}'"
  fi

  {
    # ---- 模板主体：`(gtd) { … }` snippet 定义 ----
    # ⚠️ 必须写在站点块**之前**。Caddy 的 import 是解析期解析、自上而下的：
    #    先出现 `import gtd` 而 snippet 还没定义，会直接报
    #    `File to import not found: gtd, at /etc/caddy/Caddyfile:2`（实测踩过）。
    while IFS= read -r line || [[ -n "$line" ]]; do
      # 注释行原样输出，不做占位符替换。
      # 少了这个判断，模板顶部那段"占位符说明"会被一起替换掉：
      # 轻则注释变得莫名其妙，重则替换进来的多行内容把注释块撑破，
      # 在配置里留下一行裸的上游地址 —— 那就是一个真正的语法错误。
      trimmed="${line#"${line%%[![:space:]]*}"}"
      if [[ "$trimmed" == '#'* ]]; then
        printf '%s\n' "$line"
        continue
      fi
      line="${line//__UPSTREAMS__/$upstreams}"
      printf '%s\n' "$line"
    done < "$CADDY_TEMPLATE"

    # ---- 站点块（决定对外暴露方式）----
    if (( manual )); then
      # HTTP 与 HTTPS 并存，**不做 80→443 跳转**：跳转会让 http://127.0.0.1:8080/api/health
      # 变成 301，release.sh 的探测、smoke.sh、verify-phase0.sh 会全线失败。
      printf ':80 {\n\timport gtd\n}\n\n'
      printf ':443 {\n'
      # 引用**带指纹的文件名**：证书换了 → 文件名变了 → 配置语义变了 → reload 真的重新加载。
      # （若引固定路径 tls.crt，Caddy 会判定 config is unchanged 而跳过加载，内存里仍是旧证书。）
      printf '\ttls /certs/%s.crt /certs/%s.key\n' "$cert_base" "$cert_base"
      # HSTS 只在这里发：明文口发 HSTS 没有意义，且浏览器会记住，之后回退到 HTTP 就回不去了
      printf '\theader {\n\t\t%s\n\t}\n' "$hsts"
      printf '\timport gtd\n}\n\n'
    elif [[ -n "$domain" ]]; then
      # 有域名、无手动证书 → Caddy 走 ACME（自动申请/续期 + 80→443 跳转）。与引入 HTTPS 之前一致。
      printf '%s {\n' "$domain"
      printf '\theader {\n\t\t%s\n\t}\n' "$hsts"
      printf '\timport gtd\n}\n\n'
    else
      # 无证书、无域名 → 纯明文，与引入 HTTPS 之前**完全一致**
      printf ':80 {\n\timport gtd\n}\n\n'
    fi
  } > "${CADDY_CONFIG}.new"

  # 最后一步**不能**用 mv。docker 的单文件 bind mount 绑的是 **inode**，不是路径：
  # mv 之后宿主机路径指向新 inode，而容器内 /etc/caddy/Caddyfile 仍指向旧 inode（旧内容），
  # 于是 caddy validate / caddy reload 读到的永远是**旧配置**，reload 返回 0 且日志写
  # "config is unchanged" —— 切流静默失效，而脚本还会打印"已平滑重载上游"。
  # 这个缺陷从 2026-11-13 一直存在到 2026-11-15（表现为每次发布丢 1~4 个 502）。
  # 原地覆盖（cat >）保持 inode，容器内内容才会跟着变。中间文件仍保留：先写全，再覆盖。
  cat "${CADDY_CONFIG}.new" > "$CADDY_CONFIG" || die "写入 ${CADDY_CONFIG} 失败"
  rm -f "${CADDY_CONFIG}.new"

  # 记下"本次生成**应该**让运行时看到什么"，供 caddy_reload 断言用。
  # 顺序有意义：lb_policy first 只走第一个可用上游，所以"活跃槽位在前"就是切流本身。
  CADDY_EXPECTED_UPSTREAMS="$upstreams"
  # 顺带记下本次是否启用了 HTTPS，供上层打印/断言参考
  CADDY_HTTPS_ENABLED="$manual"
}

# 用一次性容器校验配置。用在 Caddy 还没起来的时候（首次部署），
# 此时没法 exec 进容器去校验，但也不能就这么把一份错误配置交给它启动。
caddy_validate_standalone() {
  local certs_dir="${ROOT_DIR}/deploy/run/certs"
  local -a cert_mount=()
  # 配置里若引用了 /certs/tls.crt，把这个目录也挂进去，校验才与真实运行环境一致。
  [[ -d "$certs_dir" ]] && cert_mount=(-v "${certs_dir}:/certs:ro")
  docker run --rm \
    -v "${CADDY_CONFIG}:/etc/caddy/Caddyfile:ro" \
    "${cert_mount[@]}" \
    caddy:2-alpine caddy validate \
    --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1
}

# ---------------------------------------------------------------- 切流断言
# 由 caddyfile_generate() 写入：本次生成的配置**应该**让运行时看到的 upstream 列表。
# 顺序有意义（lb_policy first 只走第一个可用的），所以断言是顺序敏感的。
CADDY_EXPECTED_UPSTREAMS=''
# 由 caddyfile_generate() 写入：本次生成是否启用了 HTTPS（0/1）。供上层打印或断言参考。
CADDY_HTTPS_ENABLED=0

# 证书文件的 SHA-256 指纹（大写、去冒号）。取不到就返回空串。
cert_fingerprint_file() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  command -v openssl >/dev/null 2>&1 || return 0
  openssl x509 -in "$f" -noout -fingerprint -sha256 2>/dev/null \
    | sed 's/.*=//' | tr -d ':' | tr 'a-f' 'A-F'
}

# Caddy **当前实际对外提供**的证书指纹（从宿主回环 HTTPS 端口抓）。
#
# 为什么需要它：`caddy reload` 在"配置文本没变"时会跳过整个加载（日志只写
# `config is unchanged`），**内存里的旧证书不会更新**。也就是说"换了证书文件，
# 但 tls 指令的路径没变"这种情况，reload 会返回 0 却什么都没做 ——
# 与 SOP-11 那次 inode 事故是同一个病：判定器只看退出码，不看运行时状态。
caddy_served_cert_fingerprint() {
  local port; port="$(read_env GTD_HTTPS_PORT 443)"
  command -v openssl >/dev/null 2>&1 || return 0
  openssl s_client -connect "127.0.0.1:${port}" -servername localhost </dev/null 2>/dev/null \
    | openssl x509 -noout -fingerprint -sha256 2>/dev/null \
    | sed 's/.*=//' | tr -d ':' | tr 'a-f' 'A-F'
}

# 从 admin API 读运行时真正在用的上游列表。
caddy_runtime_upstreams_raw() {
  # 防御：COMPOSE 未初始化时，"${COMPOSE[@]}" 依赖式展开会塌成空，
  # 于是 `exec -T caddy ...` 会被当成 bash 的 exec 内建去执行（报 "up/exec: command not found"），
  # 错误信息完全指不到真因。这里先把话说清楚。
  [[ -n "${COMPOSE[*]:-}" ]] || die '内部错误：COMPOSE 未初始化（调用前需先 need_docker）'
  "${COMPOSE[@]}" exec -T caddy sh -c 'wget -qO- http://127.0.0.1:2019/config/' 2>/dev/null \
    | grep -o '"dial":"[^"]*"' | sed 's/"dial":"//;s/"$//'
}

caddy_runtime_upstreams() {
  # 去重但**保留首次出现的顺序**（awk 的 !seen[$0]++ 正是这个语义）。
  #
  # 去重的前提：配置里 `:80` 与 `:443`（或域名站点块）**都 import 同一份 `(gtd)` snippet**，
  # 因此两边上游必然完全相同 —— 去重只是把"同一份配置被两个站点各写了一遍"折叠回一份。
  # ⚠️ 一旦有人让两个站点块用**不同**上游（例如灰度期 HTTPS 走新槽位、HTTP 还留旧槽位），
  #    去重会把真实差异抹平，断言随即失明。所以断言失败时会**同时打印原始未去重值**
  #    （见 caddy_assert_upstreams），排障时能看出到底是哪一块的配置不对。
  caddy_runtime_upstreams_raw | awk '!seen[$0]++' | tr '\n' ' ' | sed 's/ *$//'
}

# 断言"切流真的生效了"。
#
# 为什么必须有这道：caddy reload 对**没有任何变化**的配置也返回 0，日志只写一句
# `config is unchanged`。所以在单文件 bind mount 的 inode 被换掉的那段时间里，
# 每次发布都"重载成功"、实际一个字节都没变 —— 判定器自己被软化，和 GATE-01/GATE-02 同一类病。
# 唯一可信的判据是**运行时的上游列表**，不是 reload 的退出码。
caddy_assert_upstreams() {
  local expect="${1:-$CADDY_EXPECTED_UPSTREAMS}" actual='' raw='' tries=0
  [[ -n "$expect" ]] || return 0

  # reload 是异步的，配置生效有短暂延迟；最多等 1 秒（再久就不是延迟而是没生效了）。
  while (( tries < 10 )); do
    actual="$(caddy_runtime_upstreams)"
    if [[ "$actual" == "$expect" ]]; then
      printf '  \033[32m[ ok ]\033[0m 运行时上游已确认：%s\n' "$actual"
      return 0
    fi
    sleep 0.1
    tries=$(( tries + 1 ))
  done

  # 失败时把**原始未去重值**也打出来：去重是为了容忍"两个站点块各写一遍同一份上游"，
  # 但它会掩盖"两个站点块真的不一样"这种差异。只给去重后的结果，排障时看不出是哪一块错。
  raw="$(caddy_runtime_upstreams_raw 2>/dev/null | tr '\n' ' ' | sed 's/ *$//')"

  die "Caddy 运行时上游与期望不一致
     · 期望            : ${expect}
     · 实际（去重后）  : ${actual:-<空>}
     · 实际（原始）    : ${raw:-<空>}      ← 若这里出现「两个站点块各不相同」，说明 :80 与 :443 没共用同一份上游
     ▶ 最常见原因：单文件 bind mount 的 inode 没更新 —— caddyfile_generate 若用 mv 就会这样，
       容器内 /etc/caddy/Caddyfile 永远是旧 inode，caddy reload 读到旧配置并返回 config is unchanged。
       修法：确认写入已改为原地覆盖；若刚改完代码，需重建一次 caddy 容器让挂载重新绑定：
         docker compose up -d --force-recreate caddy      # 会有一次短暂断流
     ▶ 其次：caddy reload 静默失败，或 admin API(:2019) 不可达（在容器内应为 127.0.0.1:2019）。"
}

# 平滑重载。校验不通过就原地放弃 —— 线上仍是旧配置，服务完全不受影响。
# 这一点很重要：切流失败应该是"什么都没发生"，而不是"服务挂了"。
caddy_reload() {
  local svc='caddy'
  if ! svc_running "$svc"; then
    log 'Caddy 未在运行，直接启动'
    "${COMPOSE[@]}" up -d "$svc"
    sleep 2
    # 新起的容器绑的是当前 inode，理应一致；不一致就是别处出了问题（写死在第一分钟）。
    caddy_assert_upstreams
    return 0
  fi

  if ! "${COMPOSE[@]}" exec -T "$svc" caddy validate \
         --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
    warn '生成的配置未通过校验，具体错误：'
    "${COMPOSE[@]}" exec -T "$svc" caddy validate \
      --config /etc/caddy/Caddyfile --adapter caddyfile >&2 || true
    die '已放弃切流。线上仍是旧配置，服务未受影响。'
  fi

  "${COMPOSE[@]}" exec -T "$svc" caddy reload \
      --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 \
    || die 'Caddy 热加载失败（旧配置仍在生效，服务未受影响）'

  # reload 返回 0 ≠ 生效。必须以运行时上游为准，否则 no-op 会被当成成功。
  caddy_assert_upstreams

  # 若存在"证书待应用"标记，而本次 reload 之后运行时证书已经等于文件里的证书，
  # 那这个标记的意图已经达成 —— 清掉它，否则看门狗一分钟后会再做一次无用重载。
  # （发布脚本本身也会重生成配置并 reload，等于顺手把证书应用了。）
  local pending="${RUN_DIR}/certs/.pending-apply"
  if [[ -f "$pending" ]] && (( ${CADDY_HTTPS_ENABLED:-0} )); then
    local want_fp got_fp
    want_fp="$(cert_fingerprint_file "${RUN_DIR}/certs/tls.crt")"
    got_fp="$(caddy_served_cert_fingerprint)"
    if [[ -n "$want_fp" && "$want_fp" == "$got_fp" ]]; then
      rm -f "$pending"
      printf '  \033[32m[ ok ]\033[0m 证书已在本次重载中生效，已清除待应用标记\n'
    fi
  fi

  ok 'Caddy 已平滑重载上游（运行时上游已核实）'
}

# ---------------------------------------------------------------- 公网入口探测
# 探测必须打**公网入口**（也就是 Caddy），而不是槽位端口 ——
# 我们要证明的是"用户没被断线"，而用户走的是 Caddy 那条路。
# 只探槽位端口的话，切流那一刻丢掉的所有请求都看不见。
probe_url() {
  local domain path
  domain="$(read_env GTD_DOMAIN '')"
  # 必须走 read_env —— 这里原来写的是 ${GTD_PROBE_PATH:-/api/health}，
  # 那只读**进程环境**，脚本从不 source .env：操作者在 .env 里改了这个值
  # 完全不生效也没有任何提示（最坏的一种配置失效：静默）。
  #
  # fallback 仍取同名环境变量：优先级 = .env > 进程环境 > /api/health。
  # 这样既修好了 .env 不生效，也不会让既有部署里
  # `sudo GTD_PROBE_PATH=/ bash deploy/release.sh` 这种显式 export 的老写法突然失效。
  path="$(read_env GTD_PROBE_PATH "${GTD_PROBE_PATH:-/api/health}")"
  # 两边都设了却不一样时，别让落败的那个静默消失 —— 这正是本条缺陷的病根。
  # 必须写 stderr：probe_url 的输出会被 $(...) 原样当作 URL 用。
  if [[ -n "${GTD_PROBE_PATH:-}" && "${GTD_PROBE_PATH}" != "$path" ]]; then
    printf '\033[33m[  ! ] GTD_PROBE_PATH 冲突：.env 里是 %s，进程环境里是 %s —— 按 .env 为准。\033[0m\n' \
           "$path" "$GTD_PROBE_PATH" >&2
  fi
  if [[ -n "$domain" ]]; then
    printf 'https://%s%s' "$domain" "$path"
  else
    printf 'http://127.0.0.1:%s%s' "$(read_env GTD_HTTP_PORT 80)" "$path"
  fi
}

# 探测所需的额外 curl 参数，写入全局数组 PROBE_ARGS。
# 有域名时必须走 HTTPS：Caddy 在 80 端口会自动 308 重定向到 https，
# 直接探 http 只会拿到一个重定向，看不出上游是否健康。
# --resolve 把域名指到回环，探测不出机器，也不需要 DNS 参与。
probe_curl_args() {
  PROBE_ARGS=()
  local domain; domain="$(read_env GTD_DOMAIN '')"
  if [[ -n "$domain" ]]; then
    PROBE_ARGS=(-k --resolve "${domain}:443:127.0.0.1")
  fi
}

# ---------------------------------------------------------------- 证据归档
# 归档一次发布的连续性探测报告到 deploy/run/。
#
# 为什么必须归档：这份报告是「这次发布到底有没有断线」的**唯一物证**。
# 原来 release.sh / rollback.sh 打印完就 rm 掉临时文件 —— 事后复盘「上次是不是零停机」
# 只能靠人的记忆或聊天记录，出争议时没有物证；而"记得加 tee"这件事迟早会忘，
# 所以归档由脚本自己做。
#
# 命名与生命周期：probe-<构建标识>.log，只保留最近 keep 份（默认 10，按修改时间排）。
# deploy/run/ 会随每次发布长文件，没有保留策略就会无声地吃满磁盘。
# 注意：prune_images 只删 docker 镜像、不碰这里的文件，所以清理责任在本函数。
#
# **永远返回 0**：归档是旁路动作，绝不能因为它把一次成功的发布搞挂。
archive_probe_report() {
  local src="$1" label="$2" keep="${3:-10}" dest='' f=''
  [[ -n "${src:-}" && -f "$src" ]] || return 0

  [[ "$keep" =~ ^[0-9]+$ ]] || keep=10
  label="${label:-$(date '+%Y%m%d-%H%M%S')}"
  # 构建标识里可能带 : 或 /（本项目标签形如 gtd4teams:20261115-101010-abc1234），
  # 直接拿来当文件名会出问题。
  label="${label//[^A-Za-z0-9._-]/_}"
  dest="${RUN_DIR}/probe-${label}.log"

  mkdir -p "$RUN_DIR" 2>/dev/null || return 0
  cp -f "$src" "$dest" 2>/dev/null || return 0
  printf '  探测报告已归档：%s（保留最近 %s 份）\n' "$dest" "$keep"

  # 超出保留份数的按修改时间从旧到新删。
  local -a _stale=()
  while IFS= read -r f; do
    [[ -n "$f" ]] && _stale+=("$f")
  done < <(ls -1t "${RUN_DIR}"/probe-*.log 2>/dev/null | tail -n +$(( keep + 1 )) || true)
  for f in "${_stale[@]}"; do
    rm -f "$f" 2>/dev/null || true
  done
  return 0
}

# ---------------------------------------------------------------- 杂项
new_tag() {
  local stamp sha
  stamp="$(date '+%Y%m%d-%H%M%S')"
  sha="$(git -C "$ROOT_DIR" rev-parse --short HEAD 2>/dev/null || true)"
  if [[ -n "$sha" ]]; then
    printf 'gtd4teams:%s-%s' "$stamp" "$sha"
  else
    printf 'gtd4teams:%s' "$stamp"
  fi
}

free_mb() {
  df -Pm "$1" 2>/dev/null | awk 'NR==2 {print $4}' || printf '0'
}
