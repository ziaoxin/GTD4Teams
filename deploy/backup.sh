#!/usr/bin/env bash
#
# GTD4Teams 数据备份 —— 可手动执行，更建议交给 cron 调度。
#
#   bash deploy/backup.sh [备份目录] [保留份数]
#   默认：/var/backups/gtd4teams，保留最近 14 份
#
# cron 示例（每天 03:30 备份，输出另存日志）：
#   30 3 * * * cd /opt/gtd4teams && bash deploy/backup.sh >> /var/log/gtd4teams-backup.log 2>&1
#
# 为什么用应用自带的 --export，而不是直接 cp 那个 .db 文件：
# 导出走的是正常读连接，拿到的一定是一致性快照；直接拷文件可能拷到正在写入的中途状态
# （WAL 模式下尤其危险 —— 只拷 .db 而漏掉 -wal，拿到的会是旧数据）。
#
# 两个槽位共享同一个数据卷，所以从哪个槽位导出都一样；这里优先用当前活跃槽位。
#
set -Eeuo pipefail

_BACKUP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${_BACKUP_DIR}/lib.sh"

DEST="${1:-/var/backups/gtd4teams}"
KEEP="${2:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"
NAME="gtd4teams-${STAMP}.json"

cd "$ROOT_DIR"
need_docker
load_state

# ---- 选一个在跑的槽位 ----
SVC=''
for color in blue green; do
  if svc_running "$(color_service "$color")"; then
    SVC="$(color_service "$color")"
    break
  fi
done

# ---- 都不在跑：把当前版本拉起来再备份 ----
# 之所以不搞"离线直接从卷里掏文件"：那需要往容器里 bind mount 宿主机目录，
# 而容器以 uid 10001 运行、根文件系统只读，权限问题会让这条路比它看起来脆弱得多。
# 备份是运维动作，临时把服务拉起来是可接受的代价，而且服务本来就应该在跑。
if [[ -z "$SVC" ]]; then
  COLOR="${ACTIVE_COLOR:-blue}"
  warn '两个槽位都没有在运行。备份需要一个活着的服务，正在把当前版本拉起来…'

  if [[ -z "${ACTIVE_IMAGE}" ]] || ! docker image inspect "${ACTIVE_IMAGE}" >/dev/null 2>&1; then
    die "没有可用的镜像（记录为 ${ACTIVE_IMAGE:-空}），无法拉起服务。
     请先执行：sudo bash deploy/install.sh"
  fi

  export GTD_IMAGE="${ACTIVE_IMAGE}"
  "${COMPOSE[@]}" up -d --no-build "$(color_service "$COLOR")" >/dev/null
  if ! wait_healthy "$COLOR" 90; then
    die '服务未能在 90 秒内就绪，无法在线备份。请先排查服务本身。'
  fi
  SVC="$(color_service "$COLOR")"
  ok "已拉起槽位 ${COLOR}"
fi

log "从槽位 ${SVC} 在线导出"

mkdir -p "$DEST"
# 目录 700：**文件名本身也是信息** —— 目录可读（755）时，本机任何用户 `ls` 一下就知道
# "这台机器有没有备份、多久备一次、哪天缺了一份"。而这里装的是含口令散列的整库 JSON。
# 只 chmod 600 文件而把目录留在 755，等于把备份的存在与时间线公开。
# chmod 对已存在的旧目录同样生效（G-07 要求的就是"现在这一刻"目录是 700）。
chmod 700 "$DEST"

# 1) 在容器卷内导出（--export 把整库写成一个 JSON）
# 2) 拷到宿主机备份目录
# 3) 删掉卷里的临时文件，别让它把数据卷撑大
"${COMPOSE[@]}" exec -T "$SVC" mkdir -p /data/backups
"${COMPOSE[@]}" exec -T "$SVC" python server.py --export "/data/backups/${NAME}" >/dev/null
"${COMPOSE[@]}" cp "${SVC}:/data/backups/${NAME}" "${DEST}/${NAME}" >/dev/null
"${COMPOSE[@]}" exec -T "$SVC" rm -f "/data/backups/${NAME}"

# 备份文件包含账号口令散列，属敏感文件，收紧权限
chmod 600 "${DEST}/${NAME}"

# ---- O-17（TDS §14）：访问日志纳入备份 ----
# 路径辨析：日志写在容器内 <GTD_DATA_DIR>/logs/app.jsonl，也就是容器路径 /data/logs/。
# /data 是 compose 的具名卷 gtd-data，宿主机上没有可直接 cp 的 ./data/logs，
# 所以必须从容器里 docker compose cp 出来，不能拿宿主机路径当源。
# 含访问日志（全员 IP），属敏感备份，需按更高密级保管：目录 700、文件仅属主可读。
# 与 server.py --export 的整库 JSON 分开存放：JSON 是数据库灾备快照，日志是排障/审计材料。
LOG_ARCHIVE="${DEST}/logs-${STAMP}"
if "${COMPOSE[@]}" exec -T "$SVC" sh -c 'test -d /data/logs' 2>/dev/null; then
  log "备份访问日志（属敏感备份，需按更高密级保管）"
  "${COMPOSE[@]}" cp "${SVC}:/data/logs" "${LOG_ARCHIVE}" >/dev/null || die '访问日志备份失败 —— 已中止，避免「以为备了其实没备」的静默降级。'
  chmod 700 "${LOG_ARCHIVE}"
  chmod -R go-rwx "${LOG_ARCHIVE}"
  ok "访问日志已备份：${LOG_ARCHIVE}（敏感目录 700 / 仅属主可读）"
else
  warn '容器内 /data/logs 不存在（可能尚未产生访问日志），本次未备份访问日志。'
fi

# 只保留最近 KEEP 份
ls -1t "${DEST}"/gtd4teams-*.json 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
# 访问日志备份同样按 KEEP 份保留；|| true 兜住「无匹配目录」时 ls 的非零码（pipefail 下会中断脚本）
ls -1dt "${DEST}"/logs-* 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -rf || true

BYTES="$(wc -c < "${DEST}/${NAME}" | tr -d ' ')"
printf '[%s] 备份完成：%s/%s（%s 字节，目录内保留最近 %s 份）\n' \
       "$(date '+%F %T')" "$DEST" "$NAME" "$BYTES" "$KEEP"
if [[ -d "${LOG_ARCHIVE}" ]]; then
  printf '  访问日志：%s（含全员 IP，敏感备份，需按更高密级保管）\n' "${LOG_ARCHIVE}"
fi
