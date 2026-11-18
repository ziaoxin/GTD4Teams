#!/usr/bin/env bash
#
# GTD4Teams 自签名证书生成 —— 给"没有域名、只想在本机/内网用 HTTPS"的场景。
#
#   bash deploy/gen-selfsigned.sh            # 生成（已存在且未快过期则跳过）
#   bash deploy/gen-selfsigned.sh --force    # 强制重生成
#
# 产物：deploy/run/certs/tls.crt、deploy/run/certs/tls.key
#
# 为什么需要它
# ────────────
# Caddy 只有在 `tls` 指令拿到证书、或站点是域名（走 ACME）时才提供 HTTPS。
# 本机没有域名、也申请不了证书，于是 :443 起不来 —— 想在 https://localhost:8443
# 上验证 Cookie 的 Secure 属性、HSTS、混合内容之类，就必须有一张自签名证书。
#
# 关于浏览器警告
# ──────────────
# 自签名证书**一定会**触发浏览器警告（"您的连接不是私密连接"）。本项目**不处理**它：
# 不导出 CA、不装信任库。它只用于"验证 HTTPS 链路本身是通的"，不是给最终用户用的。
# 要对公网用户提供服务，必须换成真实域名 + ACME（见文档）。
#
# 关于 SAN（最容易漏的一点）
# ──────────────────────────
# 现代浏览器/curl 只认 subjectAltName，**不看 CN**。少了 SAN 的证书不是"警告"，是直接拒绝。
# 所以必须带 `-addext "subjectAltName=DNS:localhost,IP:127.0.0.1"`。
#
# 关于"换了证书为什么没生效"（重要）
# ────────────────────────
# Caddy 只在「配置发生变化」时才重新加载证书，而 tls 指令引用的是**按指纹命名的副本**
# （`deploy/run/certs/tls-<sha256前16位>.crt`，由 caddyfile_generate 生成）。
# 所以**手工替换** `deploy/run/certs/tls.crt` 之后，必须同时创建
# `deploy/run/certs/.pending-apply`（容器内 `/certs/.pending-apply`），
# 让看门狗在下一分钟重生成配置并热加载 —— 否则文件换了、线上仍是旧证书，
# 而 mtime/md5 全对、脚本也不报错。后端上传接口已按这个约定实现（写完文件再建标记）。
#
# 关于"不覆盖别人的证书"
# ────────────────────────
# 本脚本生成的证书旁边会留一个标记文件 `.selfsigned`。
# 若目录里有 tls.crt 但**没有**标记，说明那是别人上传的真实证书 —— 拒绝覆盖，
# 除非显式 --force。这样"运维上传了正式证书，结果某次跑脚本把它冲掉"不会发生。
#
set -Eeuo pipefail

_SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${_SELF_DIR}/.." && pwd)"
CERT_DIR="${ROOT_DIR}/deploy/run/certs"
CRT="${CERT_DIR}/tls.crt"
KEY="${CERT_DIR}/tls.key"
MARKER="${CERT_DIR}/.selfsigned"

# 证书/私钥的属主：后端容器以 uid 10001 运行（rootfs 只读，/certs 是唯一可写的挂载点之一）。
# 权限要求：目录 700、私钥 600、证书 644。
# 注意：Caddy 以 root 运行，700 的目录它照样能读；所以收紧权限不会把 Caddy 挡在外面。
CERT_UID=10001
CERT_GID=10001

DAYS=825          # 825 天是浏览器对证书有效期的宽容上限附近的值
REMAIN_OK=$(( 30 * 86400 ))   # 剩余不足 30 天就重生成（避免"还能用但快过期"的状态）

FORCE=0
while (( $# > 0 )); do
  case "$1" in
    --force) FORCE=1 ;;
    -h|--help)
      printf '用法：bash deploy/gen-selfsigned.sh [--force]\n\n'
      printf '  生成 localhost 自签名证书到 deploy/run/certs/（SAN: DNS:localhost,IP:127.0.0.1）\n'
      printf '  --force  即使已存在也重新生成\n'
      exit 0 ;;
    *) printf '未知参数：%s\n\n' "$1" >&2; exit 2 ;;
  esac
  shift
done

command -v openssl >/dev/null 2>&1 || {
  printf '\033[31m[  x ]\033[0m 未找到 openssl。Ubuntu/Debian：apt-get install -y openssl\n' >&2
  exit 1
}

printf '\n  GTD4Teams 自签名证书\n'
printf '  ────────────────────────────────────────────────\n'
printf '  输出目录  %s\n' "$CERT_DIR"

mkdir -p "$CERT_DIR"

# ---- 已有证书的处理 ----
# 标记文件里记录**生成时的证书指纹**，用来判断"现在的 tls.crt 还是不是我生成的那一张"。
# 为什么需要指纹而不只是"标记文件存在"：用户上传真实证书后会覆盖 tls.crt，但 `.selfsigned`
# 还在 —— 只看标记存在就会误判成"这是我生成的、可以覆盖"，把用户刚上传的证书冲掉。
cert_fp() { openssl x509 -in "$1" -noout -fingerprint -sha256 2>/dev/null | sed 's/.*=//' | tr -d ':' | tr 'a-f' 'A-F'; }

if [[ -f "$CRT" && -f "$KEY" && $FORCE -eq 0 ]]; then
  if [[ ! -f "$MARKER" ]]; then
    printf '\n\033[31m[  x ]\033[0m %s 已存在，但它不是本脚本生成的（缺少标记文件 .selfsigned）。\n' "$CRT"
    printf '        看起来这是**运维上传的真实证书**。为避免把正式证书冲掉，已中止。\n'
    printf '        确实要覆盖：bash deploy/gen-selfsigned.sh --force\n'
    exit 1
  fi
  recorded_fp="$(sed -n 's/^fingerprint=//p' "$MARKER" | head -1)"
  current_fp="$(cert_fp "$CRT")"
  if [[ -n "$recorded_fp" && -n "$current_fp" && "$recorded_fp" != "$current_fp" ]]; then
    printf '\n\033[31m[  x ]\033[0m 现有证书与标记里记录的不是同一张（说明它已被替换/上传）。\n'
    printf '        标记记录: %s\n' "${recorded_fp:0:32}…"
    printf '        当前文件: %s\n' "${current_fp:0:32}…"
    printf '        为避免把上传的证书冲掉，已中止。确实要覆盖：bash deploy/gen-selfsigned.sh --force\n'
    exit 1
  fi
  if openssl x509 -in "$CRT" -noout -checkend "$REMAIN_OK" >/dev/null 2>&1; then
    # 顺手补齐指纹记录（老版本的标记里没有这一行）
    if [[ -z "$recorded_fp" && -n "$current_fp" ]]; then
      printf 'fingerprint=%s\n' "$current_fp" >> "$MARKER" 2>/dev/null || true
    fi
    printf '\n\033[32m[ ok ]\033[0m 已有自签名证书且剩余有效期 > 30 天，跳过生成。\n'
    printf '        需要重生成：bash deploy/gen-selfsigned.sh --force\n'
    openssl x509 -in "$CRT" -noout -subject -enddate | sed 's/^/        /'
    exit 0
  fi
  printf '\n\033[33m[  ! ]\033[0m 已有证书即将过期（剩余不足 30 天），重新生成。\n'
fi

if [[ -f "$CRT" && -f "$KEY" && ! -f "$MARKER" && $FORCE -eq 1 ]]; then
  printf '\n\033[33m[  ! ]\033[0m --force：将覆盖一份**可能是真实证书**的文件（%s）。\n' "$CRT"
fi

# ---- 生成 ----
# 不用交互：-subj 一次给全 DN，-nodes 不加密私钥（否则 Caddy 每次启动要口令）。
umask 077
TMPDIR_GEN="$(mktemp -d "${CERT_DIR}/.gen.XXXXXX")"
trap 'rm -rf "$TMPDIR_GEN"' EXIT

if ! openssl req -x509 -newkey rsa:2048 -nodes -days "$DAYS" \
      -keyout "${TMPDIR_GEN}/tls.key" -out "${TMPDIR_GEN}/tls.crt" \
      -subj "/CN=localhost" \
      -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" \
      -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
      -addext "extendedKeyUsage=serverAuth" \
      -addext "basicConstraints=critical,CA:FALSE" >/dev/null 2>&1; then
  printf '\n\033[31m[  x ]\033[0m openssl 生成失败。\n' >&2
  exit 1
fi

# 覆盖前再确认一次：先写临时目录、校验通过后才落位，避免生成失败留下半张证书
openssl x509 -in "${TMPDIR_GEN}/tls.crt" -noout >/dev/null 2>&1 || {
  printf '\n\033[31m[  x ]\033[0m 生成的证书无法解析，未落位。\n' >&2
  exit 1
}

install -m 600 "${TMPDIR_GEN}/tls.key" "$KEY"
install -m 644 "${TMPDIR_GEN}/tls.crt" "$CRT"
{
  printf 'fingerprint=%s\n' "$(cert_fp "$CRT")"
  printf 'generated by deploy/gen-selfsigned.sh at %s (SAN: DNS:localhost,IP:127.0.0.1)\n' "$(date '+%F %T')"
} > "$MARKER"
chmod 600 "$MARKER"

# ---- 权限与属主 ----
chmod 700 "$CERT_DIR"
if (( EUID == 0 )); then
  chown -R "${CERT_UID}:${CERT_GID}" "$CERT_DIR" 2>/dev/null || {
    printf '\033[33m[  ! ]\033[0m 无法把 %s 的属主改为 %s:%s（可能宿主 uid 不存在）。\n' \
           "$CERT_DIR" "$CERT_UID" "$CERT_GID" >&2
    printf '        后端容器以 uid %s 运行，上传证书会写不进来。手工修：\n' "$CERT_UID" >&2
    printf '        sudo chown -R %s:%s %s\n' "$CERT_UID" "$CERT_GID" "$CERT_DIR" >&2
  }
else
  printf '\033[33m[  ! ]\033[0m 非 root：跳过 chown。若后端要写这个目录，需手工：\n' >&2
  printf '        sudo chown -R %s:%s %s\n' "$CERT_UID" "$CERT_GID" "$CERT_DIR" >&2
fi

printf '\n\033[32m[ ok ]\033[0m 已生成自签名证书（%s 天）\n' "$DAYS"
openssl x509 -in "$CRT" -noout -subject -dates -ext subjectAltName 2>/dev/null | sed 's/^/        /'
printf '\n  下一步：让 Caddy 用上它（重新生成配置 + 热加载，不必重建容器）\n'
printf '    bash -c "source deploy/lib.sh; need_docker; caddyfile_generate <槽位>; caddy_reload"\n'
printf '  或直接走一次发布：sudo bash deploy/release.sh\n'
printf '  验证：curl -k https://localhost:8443/api/health\n\n'
