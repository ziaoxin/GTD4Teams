"""结构化访问日志（JSONL）：写入、轮转与"读最近 N 条"。

为什么要有这个模块
------------------
在本模块之前，服务端**没有任何日志框架**（全仓 `import logging` 命中 0），
访问日志只有 httpd._log_access 在 `status >= 400` 时 `print` 一行。
后果不是"少了一点方便"，而是监控与告警**没有数据源**：

  · 成功的请求完全不留痕 → 算不出 QPS、P95 延迟、成功率；
  · 只有"出错就算一次"的计数，且没有身份字段 → 无法区分
    "一个人反复失败"（撞库）与"很多人各失败一次"（系统故障）；
  · print 到 stdout 的纯文本，机器不可解析 → 任何指标都要先写正则。

所以这里把访问日志做成**每请求一条 JSON 行**（一行一个对象，即 JSONL）：
它对 `jq` / `grep` 友好，可以直接被采集侧按字段聚合。
落盘在 `<data_dir>/logs/app.jsonl`，同时镜像一份到 stderr 供 `docker logs` 查看。

三个刻意的设计
--------------
1. **日志是辅助，不是依赖**。目录建不出来、磁盘只读、文件打不开 ——
   一律降级成"只写 stderr"并 warn 一行，**绝不让服务因此起不来**。
   一个因为日志写不了而拒绝服务的进程，等于把可用性押在了最不重要的组件上。
2. **健康检查不进日志**。Caddy 的 active health check 每 3 秒打一次
   `/api/health`（≈28800 条/天），照单全收不只是"日志变多"：
   它会把 50MB 的轮转窗口迅速挤满、把真正的故障记录顶掉。
   这条规则由调用方（httpd.ACCESS_LOG_EXCLUDED）判定，这里只提供
   DEBUG 级的心跳出口。
3. **轮转有上限**。50MB × 5 份 ≈ 最多 300MB。没有上限的日志迟早把宿主磁盘写满，
   而那时的表现是"SQLite 写不进去 → 全站 500"，排查方向会被彻底带偏。

安全前提：**落盘的 path 必须已脱敏**。本模块不负责脱敏，
调用方（httpd.Handler._log_access）固定调用 `_redact_path`，且不提供绕过口。

为什么是"每个数据目录一个 AccessLog 实例"而不是一个模块级 logger
-----------------------------------------------------------------
同一个进程里可能同时存在多个 Application（测试里就有三个：主实例、
证书目录缺失实例、反向代理实例）。它们各自有不同的 data_dir。
若共用一个全局 handler，"最后装配的那个"会把其它实例的日志全写到自己文件里 ——
表现是"某台服务器的日志文件里出现了别的实例的请求"，
排查时会得出完全错误的结论。所以 handler 挂在**按路径区分**的子 logger 上
（名字仍是 `gtd.access.<hash>`，采集侧按 `gtd.access` 前缀过滤照旧生效）。
"""

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

#: 访问日志的 logger 命名空间。独立（不挂 root），便于采集侧按名过滤。
ACCESS_LOGGER_NAME = 'gtd.access'

#: 轮转策略：单文件 50MB，保留 5 份备份 → 磁盘占用上限约 300MB。
MAX_BYTES = 50 * 1024 * 1024
BACKUP_COUNT = 5

#: `GET /api/logs` 的 limit 上限。挡住"一次把 300MB 读进内存"的请求。
MAX_LIMIT = 1000
DEFAULT_LIMIT = 100

#: 反向读文件时的块大小。64KB 足以覆盖几百行，又不会一次占住内存。
READ_BLOCK = 64 * 1024

#: `read_recent` 的内部标记键：文件存在但**读不动**（权限 / 路径不是目录）时，
#: 返回空结果并把原因塞在这个键里，由调用方（`GET /api/logs`）翻译成
#: `fileLogging=false` + `degradeReason`。**不会**出现在接口响应里。
#:
#: 为什么不用异常：这三类失败都不是"服务端错误"，只是"日志不可用"。
#: 回 500 会把"日志降级"伪装成"服务炸了"——而这两件事的处置方式完全不同。
READ_UNAVAILABLE = '_unreadable'


class LogReadError(Exception):
    """日志文件存在但读不了（权限/IO）。由调用方翻成 500 并说明是部署问题。"""


class _Formatter(logging.Formatter):
    """只输出消息本身。

    JSONL 的每一行必须**恰好**是一个 JSON 对象：一旦前缀了
    `2026-11-17 08:00:00 INFO `，整份文件就不再是可解析的 JSONL，
    采集侧要么解析失败、要么得为"两种行格式"写分支 —— 那正是要避免的。
    时间戳在 JSON 里（字段 ts），所以这里不需要 logging 自己的时间前缀。
    """

    def format(self, record):
        return record.getMessage()


class AccessLog:
    """一个数据目录对应的一份访问日志（stderr + 轮转文件）。"""

    def __init__(self, path, stream=None):
        # path=None 表示"只写 stderr"（还没配置数据目录时的兜底实例）。
        self.path = Path(path) if path else None
        self.degraded = False
        self.problem = None
        # 子 logger：名字带路径指纹，保证不同数据目录的记录不会串到同一个文件。
        self.logger = logging.getLogger(
            f'{ACCESS_LOGGER_NAME}.{_fingerprint(self.path)}')
        # logger 自身放到 DEBUG，级别交给 handler 决定：
        # 访问记录走 INFO（两个 handler 都收），健康检查心跳走 DEBUG（默认不输出）。
        self.logger.setLevel(logging.DEBUG)
        # 不向父 logger 传播：否则"同一条日志被两个实例各写一遍"，
        # 或者将来任何一处 logging.basicConfig() 让访问日志重复出现。
        self.logger.propagate = False
        self._install(stream)

    # -- 装配 -------------------------------------------------------------
    def _install(self, stream):
        for handler in list(self.logger.handlers):
            self.logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:                # noqa: BLE001 - 关不掉也要继续装配
                pass

        # stderr：容器里 `docker logs` 直接能看到，且不受磁盘状态影响。
        if stream is None:
            import sys
            stream = sys.stderr
        stream_handler = logging.StreamHandler(stream)
        stream_handler.setLevel(logging.INFO)
        stream_handler.setFormatter(_Formatter())
        self.logger.addHandler(stream_handler)

        if self.path is None:                # 兜底实例：只写 stderr，不碰磁盘
            return

        file_handler, problem = _open_file_handler(self.path)
        if file_handler is not None:
            self.logger.addHandler(file_handler)
        self.degraded = file_handler is None
        self.problem = problem
        if problem:
            # 用 warning 而不是 print：这行和访问日志走同一条通道，不会被吞掉。
            self.logger.warning(
                '访问日志无法写入 %s（%s），已降级为仅输出到 stderr。'
                '服务不受影响，但监控/告警将缺少数据源，请检查数据目录权限与挂载。',
                self.path, problem)

    # -- 写入 -------------------------------------------------------------
    def write(self, record):
        """写一条访问记录。任何异常都必须被吞掉 —— 日志不能影响响应。"""
        try:
            self.logger.info(json.dumps(record, ensure_ascii=False,
                                        separators=(',', ':')))
        except Exception:                    # noqa: BLE001
            pass

    def heartbeat(self, message='health check'):
        """健康检查这类"每 3 秒一次"的请求只留 DEBUG 心跳，默认级别不输出。

        需要临时看它时直接调 handler 级别即可，但**不要**把它提成 INFO：
        28800 条/天的心跳会把真正的问题挤出轮转窗口。
        """
        try:
            self.logger.debug(message)
        except Exception:                    # noqa: BLE001
            pass

    # -- 状态 -------------------------------------------------------------
    def status(self):
        return {
            'path': str(self.path) if self.path else None,
            'fileLogging': self.path is not None and not self.degraded,
            'degraded': bool(self.degraded),
            'problem': self.problem,
            'maxBytes': MAX_BYTES,
            'backupCount': BACKUP_COUNT,
        }


def _fingerprint(path):
    """路径 → 8 位十六进制。用作 logger 名后缀（只允许字母数字，避免怪字符）。"""
    return hashlib.sha1(str(path or '').encode('utf-8')).hexdigest()[:8]


def _open_file_handler(target):
    """尝试打开轮转文件。返回 (handler|None, 失败原因|None)。"""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as err:
        # 目录建不出来（只读挂载、上级目录不存在、权限不足）
        return None, f'目录不可创建: {err}'
    try:
        handler = RotatingFileHandler(
            str(target), maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding='utf-8',
        )
    except OSError as err:
        # 目录在但文件写不了（只读文件系统、目录只读、同名目录占位）
        return None, f'文件不可写: {err}'
    handler.setLevel(logging.INFO)
    handler.setFormatter(_Formatter())
    return handler, None


# --------------------------------------------------------------------------
# 装配入口
# --------------------------------------------------------------------------

_configure_lock = threading.Lock()
_instances = {}


def configure(config, stream=None):
    """按配置装配（幂等）。返回该数据目录对应的 AccessLog。

    幂等的意义：`make_server` 可能被重复调用（测试、脚本里起多个实例），
    重复装配会让同一条日志写两遍 —— 那会让 QPS 直接翻倍，且很难发现。
    """
    path = Path(getattr(config, 'log_file', None)
                or (Path(config.data_dir) / 'logs' / 'app.jsonl'))
    key = str(path)
    with _configure_lock:
        instance = _instances.get(key)
        if instance is None:
            instance = AccessLog(path, stream=stream)
            _instances[key] = instance
        return instance


_default_lock = threading.Lock()
_default_instance = None


def default():
    """尚未 configure 时的兜底实例（只写 stderr，不碰磁盘）。

    存在的理由：`_log_access` 会在每个请求上被调用，而 `Handler.app` 理论上
    可以是 None（类属性默认值）。此时宁可只写 stderr，也不要抛 AttributeError
    把一条本该 200 的请求变成 500 —— 日志不是依赖。
    """
    global _default_instance
    with _default_lock:
        if _default_instance is None:
            _default_instance = AccessLog(None)
        return _default_instance


# --------------------------------------------------------------------------
# 时间与读取
# --------------------------------------------------------------------------

def timestamp():
    """ISO8601 带毫秒、UTC、Z 结尾。

    固定 UTC 的理由：容器时区五花八门，跨机排障时"本地时间"是个陷阱 ——
    两台机器的时间戳差 8 小时，会让人以为是延迟而不是时区。
    """
    now = datetime.now(timezone.utc)
    return now.strftime('%Y-%m-%dT%H:%M:%S.') + f'{now.microsecond // 1000:03d}Z'


def count_lines(path):
    """文件总行数。

    为什么真的去数：`total` 的用途是回答"日志是不是被轮转掉了 / 是不是根本没写"，
    所以它必须是**当前文件**的行数，不能是"本次返回的条数"。
    分块统计（1MB 一块），不会把 300MB 读进内存。
    """
    total = 0
    try:
        with open(path, 'rb') as handle:
            while True:
                block = handle.read(1024 * 1024)
                if not block:
                    break
                total += block.count(b'\n')
    except FileNotFoundError:
        return 0
    except OSError as err:
        raise LogReadError(str(err)) from err
    return total


def read_recent(path, *, limit=DEFAULT_LIMIT, status=None, path_contains=None,
                contains=None, block_size=READ_BLOCK):
    """从文件尾部反向读，凑够 limit 条就停。

    为什么不能"读完再过滤"：日志文件可能到 300MB，而 `?status=500` 这种过滤
    恰恰是在日志很多的时候才被用 —— 那时把整个文件读进内存既慢又危险
    （一次请求就能把进程撑爆）。所以过滤条件在**读取过程中**应用，
    命中数够了立刻返回。

    返回的 `logs` 是**时间正序**（旧 → 新），等价于 `tail -n` 的输出顺序，
    前端按顺序渲染即可。

    `truncated` 表示"更早的位置可能还有匹配记录没有返回"：
    只有当匹配数达到 limit、而文件还没读到头时才为 True。
    这是**保守判断** —— 达到 limit 就停，不为了精确再扫一遍全文件
    （真要精确就得把 300MB 再读一次，而结论只是"有没有第 101 条"）。

    文件存在但读不动（权限 / 路径不是目录）时返回空结果 + `READ_UNAVAILABLE` 标记，
    不抛异常、不回 500 —— 那不是服务端故障，是"日志不可用"。
    """
    limit = max(1, min(int(limit), MAX_LIMIT))
    entries = []
    truncated = False

    try:
        handle = open(path, 'rb')
    except FileNotFoundError:
        # 文件还没创建：这是**正常状态**（一条请求都没来过），不是故障。
        return {'logs': [], 'total': 0, 'truncated': False}
    except (NotADirectoryError, IsADirectoryError, PermissionError) as err:
        # 「日志不可用」而不是「服务端读取失败」。三类来源：
        #   · NotADirectoryError：`<data_dir>/logs` 是个**普通文件** —— 降级场景，
        #     写入侧（RotatingFileHandler）启动时就已经报过并降级了，读取侧必然也打不开；
        #   · IsADirectoryError ：`app.jsonl` 是个目录（人为/挂载事故）；
        #   · PermissionError   ：只读挂载、属主不对。
        #
        # ⚠️ 这里必须**同时**接住 NotADirectoryError 与 PermissionError：
        # 同一个"父路径是文件"的故障，Windows 抛 WinError 183（FileExistsError 系），
        # Linux 抛 Errno 20 NotADirectoryError；只接一种就是"Windows 绿、Linux 红"。
        # 实测过：本项目的 L0 门禁正是被这一条拦住的。
        #
        # 也不回 500：把"日志降级"报成"服务端错误"，会让运维去查一个根本不存在的故障。
        return {'logs': [], 'total': 0, 'truncated': False, READ_UNAVAILABLE: str(err)}
    except OSError as err:
        # 剩下的才是真的 I/O 异常（EIO、设备消失…）：这种值得让人看见。
        raise LogReadError(str(err)) from err

    with handle:
        position = handle.seek(0, os.SEEK_END)
        carry = b''
        while position > 0 and len(entries) < limit:
            size = min(block_size, position)
            position -= size
            handle.seek(position)
            chunk = handle.read(size) + carry
            pieces = chunk.split(b'\n')
            # 第一段可能是上一块的一部分（反向读），留到下一轮再拼。
            carry = pieces[0]
            for index in range(len(pieces) - 1, 0, -1):
                entry = _parse(pieces[index])
                if not _matches(entry, pieces[index], status, path_contains, contains):
                    continue
                entries.append(entry)
                if len(entries) >= limit:
                    # 更早的原始行还没看：本块剩下的、以及文件更早的部分
                    # 都可能还有匹配项 —— 保守地报 truncated=True。
                    truncated = bool(position > 0 or any(
                        _matches(_parse(raw), raw, status, path_contains, contains)
                        for raw in pieces[1:index]))
                    break
        # 文件开头那一段没有前导换行，循环结束后单独处理一次。
        if len(entries) < limit and carry:
            entry = _parse(carry)
            if _matches(entry, carry, status, path_contains, contains):
                entries.append(entry)

    entries.reverse()
    return {'logs': entries, 'total': count_lines(path), 'truncated': truncated}


def _parse(raw):
    """一行 → dict。半行（崩溃时写了开头）、非本格式的行一律跳过。

    跳过而不是抛错：轮转交接的瞬间、进程被 kill 的瞬间都可能留下残行，
    一条残行不该让整个"看日志"的接口 500 —— 那等于最需要日志的时候看不了日志。
    """
    text = raw.strip()
    if not text:
        return None
    try:
        entry = json.loads(text.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return None
    return entry if isinstance(entry, dict) else None


def _matches(entry, raw, status, path_contains, contains):
    """一条记录是否满足过滤条件（在读取时应用）。"""
    if entry is None:
        return False
    if status is not None:
        try:
            if int(entry.get('status')) != status:
                return False
        except (TypeError, ValueError):
            return False
    if path_contains:
        if path_contains not in str(entry.get('path') or ''):
            return False
    if contains:
        # 全文子串：查 request_id / uid / ip 都靠它，所以匹配**原始行**而不是某个字段。
        if isinstance(raw, bytes):
            text = raw.decode('utf-8', 'replace')
        else:
            text = str(raw)
        if contains.lower() not in text.lower():
            return False
    return True
