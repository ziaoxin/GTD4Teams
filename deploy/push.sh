#!/usr/bin/env bash
#
# GTD4Teams 代码上传 —— 在**你的本机**执行，把项目推到服务器。
#
#   bash deploy/push.sh 用户名@服务器IP [远端目录]
#   例：bash deploy/push.sh root@47.80.12.34
#       bash deploy/push.sh deploy@47.80.12.34 /srv/gtd4teams
#
# 用 tar 走 ssh 管道，不依赖 rsync（服务器上不一定装）。
# 关键在于**排除清单**：本机的数据库绝不能传上去。
#   · data/          本机库，含账号口令散列，传上去就等于把本机用户和密码带到线上
#   · deploy/run/    运行期状态（活跃槽位、Caddy 配置）—— 线上要自己长出来，
#                    传上去会让服务器的"当前版本"记录与它实际跑的东西对不上
#
set -Eeuo pipefail

_PUSH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${_PUSH_DIR}/.." && pwd)"

HOST="${1:-}"
REMOTE="${2:-/opt/gtd4teams}"

if [[ -z "$HOST" ]]; then
  printf '用法：bash deploy/push.sh 用户名@服务器IP [远端目录]\n\n' >&2
  printf '  例：bash deploy/push.sh root@47.80.12.34\n' >&2
  printf '      bash deploy/push.sh deploy@47.80.12.34 /srv/gtd4teams\n\n' >&2
  exit 2
fi

command -v tar >/dev/null 2>&1 || { printf '未找到 tar\n' >&2; exit 1; }
command -v ssh >/dev/null 2>&1 || { printf '未找到 ssh\n' >&2; exit 1; }

[[ -f "${ROOT_DIR}/server.py" ]] || {
  printf '在 %s 没找到 server.py —— 请在项目根目录下运行本脚本。\n' "$ROOT_DIR" >&2
  exit 1
}

if [[ -d "${ROOT_DIR}/data" ]]; then
  printf '\033[36m[信息]\033[0m 本机 data/ 已存在，将被排除（不会上传）。\n'
fi

printf '\n  上传目标：%s:%s\n' "$HOST" "$REMOTE"
printf '  ────────────────────────────────────────────────\n'

cd "$ROOT_DIR"
tar \
  --exclude='./data' \
  --exclude='./data/*' \
  --exclude='./deploy/run' \
  --exclude='./deploy/run/*' \
  --exclude='./.git' \
  --exclude='./.workbuddy' \
  --exclude='./.wbapp_*.genie' \
  --exclude='./node_modules' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='*.db' \
  --exclude='*.db-wal' \
  --exclude='*.db-shm' \
  -czf - . \
| ssh -o ConnectTimeout=15 "$HOST" \
    "mkdir -p '${REMOTE}' && tar -xzf - -C '${REMOTE}' && echo '解包完成'"

printf '  ────────────────────────────────────────────────\n'
printf '  已上传。在服务器上继续：\n\n'
printf '    ssh %s\n' "$HOST"
printf '    cd %s\n' "$REMOTE"
printf '    sudo bash deploy/install.sh      # 首次部署\n'
printf '    sudo bash deploy/release.sh      # 之后的每次发布（零停机）\n\n'
