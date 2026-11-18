#!/usr/bin/env bash
#
# GTD4Teams 冒烟测试 —— 对着一个**已经跑起来**的 HTTP 端点做体检。
#
#   bash deploy/smoke.sh http://127.0.0.1:8788 [期望的构建标识]
#
# 它在发布流程里是"新槽位就绪"与"切流"之间的那道闸门：
# 只有全部通过，才允许把流量切过去。全部检查都不写库，可以随便重复跑。
#
# 检查三件事：
#   1. 服务真的能干活   —— 健康检查 + 前端模块图（index.html 引到的每个模块都要 200）
#   2. 起来的确实是我要发布的那一版 —— 构建标识核对 + 版本串一致
#   3. 没有多余的东西对外暴露 —— 源码/测试/数据/备份一律 404，目录穿越也不行
#
# 需要认证的用例是可选的：给 SMOKE_EMAIL / SMOKE_PASSWORD 才会跑。
# 建议专门开一个只用于冒烟的账号，避免用真人账号的凭据。
#
set -Eeuo pipefail

_SMOKE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${_SMOKE_DIR}/lib.sh"

BASE="${1:-}"
EXPECT_BUILD="${2:-}"
if [[ -z "$BASE" ]]; then
  printf '用法：bash deploy/smoke.sh <base-url> [期望的构建标识]\n' >&2
  exit 2
fi
BASE="${BASE%/}"

PASS=0
FAIL=0

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

# curl 默认会**规范化 URL 路径里的 ..**，那样目录穿越检查就成了假测试
# （请求根本没带着 .. 发出去，当然会 404）。必须加 --path-as-is。
http_code_raw() {
  local code=''
  code="$(curl -s --noproxy '*' --path-as-is -o /dev/null -w '%{http_code}' --max-time 6 "$1" 2>/dev/null)" || true
  printf '%s' "${code:-000}"
}

printf '\n  冒烟测试目标：%s\n' "$BASE"
printf '  ────────────────────────────────────────────────\n'

# ============================================================ 1. 健康检查
printf '\n  [1/5] 服务健康\n'
code="$(http_code "$BASE/api/health")"
r=0; [[ "$code" == '200' ]] || r=1
check 'GET /api/health 返回 200' "$r" "实际 $code"

health=''
if [[ "$code" == '200' ]]; then
  health="$(http_body "$BASE/api/health")"
fi
storage="$(json_str "$health" 'storage')"
build="$(json_str "$health" 'build')"

r=0; [[ "$storage" == 'sqlite' ]] || r=1
check '存储后端为 sqlite' "$r" "实际 ${storage:-（空）}"

r=0; [[ -n "$build" ]] || r=1
check '回传了构建标识' "$r" "build=${build:-（空）}"

if [[ -n "$EXPECT_BUILD" ]]; then
  r=0; [[ "$build" == "$EXPECT_BUILD" ]] || r=1
  check '构建标识与本次发布一致' "$r" "期望 $EXPECT_BUILD / 实际 ${build:-（空）}"
fi

# ============================================================ 2. 前端可加载
printf '\n  [2/5] 前端资源与模块图\n'
html="$(http_body "$BASE/")"
r=0; [[ -n "$html" ]] || r=1
check 'GET / 返回内容' "$r" "$(printf '%s' "$html" | wc -c | tr -d ' ') 字节"

# 注意结尾的 `|| true`：grep 在没有匹配时返回 1，叠加 set -o pipefail 会让整条
# 命令替换失败，进而被 set -e 当成致命错误 —— 于是"恰好没有外部脚本"这种
# **期望的结果**反而会让脚本自己崩掉，而且崩得毫无提示。
# 这类地方一律显式兜底。
external="$(printf '%s' "$html" | grep -oE '<script[^>]+src="https?://[^"]+"' | wc -l | tr -d ' ' || true)"
r=0; [[ "$external" == '0' ]] || r=1
check 'index.html 不引用任何外部脚本（可离线运行）' "$r" "外部脚本 $external 个"

version_hits="$(printf '%s' "$html" | grep -oE '\?v=[A-Za-z0-9]+' | wc -l | tr -d ' ' || true)"
r=0; [[ "$version_hits" != '0' ]] || r=1
check 'index.html 带缓存版本串（?v=）' "$r" "$version_hits 处"

# 逐模块抓取：从 index.html 播种，顺着 import 语句把整张模块图走完。
# 这一条能抓到"新代码引用了没打进镜像的模块"这类只在运行时才炸的问题。
declare -A _SEEN=()
declare -a _PENDING=()

_push() {
  local key="$1|$2"
  if [[ -z "${_SEEN[$key]:-}" ]]; then
    _SEEN[$key]=1
    _PENDING+=("$key")
  fi
}

while IFS= read -r hit; do
  [[ -n "$hit" ]] || continue
  seed_path="${hit%%\?v=}"; seed_path="/${seed_path#./}"
  _push "$seed_path" "${hit##*\?v=}"
done < <(printf '%s' "$html" \
           | grep -oE '\./js/[A-Za-z0-9._-]+\.js\?v=[A-Za-z0-9]+' | sort -u)

declare -a _BROKEN=()
declare -A _VERSIONS=()
_modules=0

while (( ${#_PENDING[@]} > 0 )); do
  key="${_PENDING[0]}"
  _PENDING=("${_PENDING[@]:1}")
  mpath="${key%%|*}"; mver="${key##*|}"
  _VERSIONS[$mver]=1
  _modules=$(( _modules + 1 ))

  code="$(http_code "$BASE${mpath}?v=${mver}")"
  if [[ "$code" != '200' ]]; then
    _BROKEN+=("${mpath} -> ${code}")
    continue
  fi

  body="$(http_body "$BASE${mpath}?v=${mver}")"
  while IFS= read -r dep; do
    [[ -n "$dep" ]] || continue
    # 模块里的 import 写作 './api.js?v=xxx'（相对 /js/ 目录），
    # 必须先把 './' 去掉再拼绝对路径，否则会请求到 /js/./api.js —— 服务端按设计拒绝 '.' 段，
    # 于是这条检查会以"模块缺失"的假象失败。
    dpath="${dep%%\?v=}"
    _push "/js/${dpath#./}" "${dep##*\?v=}"
  done < <(printf '%s' "$body" \
             | grep -oE '\./[A-Za-z0-9._-]+\.js\?v=[A-Za-z0-9]+' | sort -u)
done

r=0; [[ "$_modules" -gt 0 ]] || r=1
check '模块图非空（确实解析到了前端模块）' "$r" "共 ${_modules} 个"

r=0; [[ ${#_BROKEN[@]} -eq 0 ]] || r=1
check '模块图中每个模块都返回 200' "$r" \
      "$( [[ ${#_BROKEN[@]} -eq 0 ]] && printf '无缺失' || printf '%s' "${_BROKEN[*]}" )"

r=0; [[ ${#_VERSIONS[@]} -eq 1 ]] || r=1
check '所有模块的 ?v= 版本串一致' "$r" "${!_VERSIONS[*]}"

# ============================================================ 3. 暴露面
printf '\n  [3/5] 暴露面（这些路径必须全部 404）\n'
declare -a _LEAK_PATHS=(
  # 服务端源码
  '/server.py' '/pyapp/db.py' '/pyapp/policy.py' '/pyapp/config.py' '/pyapp/httpd.py'
  # 测试、文档、部署配置
  '/tests/test_api.py' '/README.md' '/Dockerfile' '/docker-compose.yml'
  # 密钥与数据
  '/.env' '/data/gtd4teams.db' '/deploy/run/state.env'
  # 目录穿越的几种写法（白名单第一段合法，但不能逃出白名单子树）
  '/js/../server.py' '/js/%2e%2e/server.py' '/js/..%2Fserver.py'
  '/js/../pyapp/db.py' '/css/../pyapp/db.py' '//server.py' '/js//api.js'
)
_leaked=''
for path in "${_LEAK_PATHS[@]}"; do
  code="$(http_code_raw "$BASE$path")"
  if [[ "$code" != '404' ]]; then
    _leaked="${_leaked}${path}=${code} "
  fi
done
r=0; [[ -z "$_leaked" ]] || r=1
check "${#_LEAK_PATHS[@]} 个受限路径全部 404" "$r" "${_leaked:-无泄漏}"

# 反向确认白名单没有收得过紧：站点本身必须能取到
code="$(http_code "$BASE/js/app.js?v=$(printf '%s' "${!_VERSIONS[*]}" | awk '{print $1}')")"
r=0; [[ "$code" == '200' ]] || r=1
check '站点资源本身可正常获取（白名单没误伤）' "$r" "app.js -> $code"

# ============================================================ 4. 鉴权边界
printf '\n  [4/5] 鉴权与写入口\n'
code="$(http_code "$BASE/api/workspace")"
r=0; [[ "$code" == '401' ]] || r=1
check '未登录读工作区被拒（401）' "$r" "实际 $code"

code="$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' --max-time 6 \
        -X POST -H 'Content-Type: application/json' \
        -d '{"table":"work_items","op":"insert","payload":{"title":"smoke"}}' \
        "$BASE/api/rows" 2>/dev/null || printf '000')"
r=0; [[ "$code" == '401' ]] || r=1
check '未登录写数据被拒（401）' "$r" "实际 $code"

# 跨站写：带一个第三方 Origin 的写请求在任何情况下都不该成功。
code="$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' --max-time 6 \
        -X POST -H 'Content-Type: application/json' -H 'Origin: https://evil.example' \
        -d '{"table":"work_items","op":"insert","payload":{"title":"smoke"}}' \
        "$BASE/api/rows" 2>/dev/null || printf '000')"
r=0; [[ "$code" =~ ^(4|5) ]] || r=1
check '跨站写请求被拒（4xx/5xx）' "$r" "实际 $code"

# ============================================================ 5. 带认证的端到端（可选）
printf '\n  [5/5] 带认证的端到端（可选）\n'
if [[ -z "${SMOKE_EMAIL:-}" || -z "${SMOKE_PASSWORD:-}" ]]; then
  printf '    \033[33m·\033[0m 未提供 SMOKE_EMAIL / SMOKE_PASSWORD，跳过。\n'
  printf '      设上之后这里会真的登录一次并读一遍工作区，是最强的一道检查。\n'
else
  _jar="$(mktemp)"
  code="$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' --max-time 10 \
          -c "$_jar" -X POST -H 'Content-Type: application/json' \
          -d "{\"email\":\"${SMOKE_EMAIL}\",\"password\":\"${SMOKE_PASSWORD}\"}" \
          "$BASE/api/auth/login" 2>/dev/null || printf '000')"
  r=0; [[ "$code" == '200' ]] || r=1
  check '冒烟账号可以登录' "$r" "实际 $code"

  if [[ "$code" == '200' ]]; then
    code="$(curl -s --noproxy '*' -o /dev/null -w '%{http_code}' --max-time 10 -b "$_jar" \
            "$BASE/api/workspace" 2>/dev/null || printf '000')"
    r=0; [[ "$code" == '200' ]] || r=1
    check '登录后可读工作区（200）' "$r" "实际 $code"

    ws="$(curl -s --noproxy '*' --max-time 10 -b "$_jar" "$BASE/api/workspace" 2>/dev/null || true)"
    r=0; [[ "$ws" == *'"items"'* ]] || r=1
    check '工作区响应结构完整（含 items）' "$r"
  fi
  rm -f "$_jar"
fi

# ============================================================ 结论
printf '\n  ────────────────────────────────────────────────\n'
if [[ "$FAIL" -eq 0 ]]; then
  printf '  \033[32m冒烟测试通过：%d 项全部通过\033[0m\n\n' "$PASS"
  exit 0
fi
printf '  \033[31m冒烟测试失败：%d 项通过，%d 项失败\033[0m\n\n' "$PASS" "$FAIL"
exit 1
