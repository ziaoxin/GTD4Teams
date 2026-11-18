"""HTTP 层：REST 路由 + 静态资源托管。

它同时取代了云版本的两条通道：
  · 云平台的**托管身份**（注册/登录/改密/会话）→ 本项目自带账号体系；
  · 云数据库的 **REST 数据面**（/api/workspace、/api/rows）→ 本项目直连 SQLite。

安全上的几个刻意选择
--------------------
1. **不开 CORS**。接口同源使用，不需要 `Access-Control-Allow-Origin`；
   一旦加上，任何网页都能拿着用户的 Cookie 调这套接口。同源是默认也是唯一的工作方式。
2. **Cookie 只放随机会话令牌**，配 `HttpOnly` + `SameSite=Lax`：
   · HttpOnly 让脚本读不到它（XSS 也无法直接窃取登录态）；
   · Lax 让浏览器在跨站请求里不带它，天然挡掉 CSRF。
   另外对写操作额外校验 `Origin` 与 `Content-Type`，作为纵深防御。
3. **静态资源白名单托管**。只有 `index.html` / `js` / `css` / `assets` / `img` 会被暴露，
   源码目录、测试夹具、数据目录、备份文件都在白名单之外，
   且解析后必须落在项目目录内（防目录穿越）。
4. **没有目录列表**。这是云版本踩过的坑：静态服务默认开目录列表，
   于是 `.smoke/`、`.workbuddy/` 被整个公开，且上传是累加的、删不掉。
5. **请求体有上限**（默认 8MB），因为 OCR 场景会把截图以 data URL 内联提交。
"""

import ipaddress
import json
import re
import secrets
import socket
import sqlite3
import sys
import threading
import time
import traceback
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import APP_VERSION, applog, db, llm, security, store, tls_cert
from .config import MIME_TYPES, PUBLIC_ENTRIES
from .policy import Denied, load_context
from .store import StoreError

EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]{2,}$')

#: 访问日志里需要压平的控制字符（含 CR/LF/TAB）。
#: 日志是行导向的：一个换行就能伪造出一条"看起来合法"的日志行。
_CONTROL_CHARS_RE = re.compile(r'[\x00-\x1f\x7f]')

#: 不进访问日志的路径（**只有健康检查**）。
#:
#: 为什么必须排除：Caddy 的 active health check 每 3 秒打一次 /api/health，
#: 一天 28800 条。照单全收的后果不只是"日志变多"，而是它会把 50MB 的轮转窗口
#: 迅速挤满、把真正的业务与故障记录顶掉 —— 出事时最想看的那些行最先消失。
#:
#: 为什么静态资源**不**排除：/ 、/js/*.js、/css/*.css 是真实用户行为
#: （首屏渲染、缓存命中与否），不是噪音。把它们排除掉，PV 就没有数据源了。
ACCESS_LOG_EXCLUDED = frozenset({'/api/health'})

#: 请求结束但**从未发出任何响应**时记的状态码。
#:
#: 499 是 nginx 的约定（客户端提前断开）。不用 500：499 与 500 混在一起会直接
#: 污染"服务端错误率"这个指标 —— 用户关掉页面不该触发告警。
CLIENT_CLOSED_STATUS = 499


# 强制改密闸门的放行白名单（**实现上需要显式登记的只有这 3 条**）。
#
# 为什么只有 3 条：闸门在 _require_user 里，而下面这些路径压根不经过它 ——
#   GET  /api/auth/session  用 _current_user（前端启动门禁的唯一来源）
#   POST /api/auth/logout   不鉴权（被强制改密的人必须能退出换账号）
#   GET  /api/health        不鉴权（发布/容器探针不能因为有人没改密码而失败）
#   POST /api/logs          用 _current_user（"改密失败"正是最需要留证的场景）
#   GET  /api/logs          走 _require_instance_admin → **受闸门约束**（运维接口，
#                           没改初始口令的管理员先改密码；不在白名单里）
#   静态资源                 _dispatch 前置分支（否则改密页本身都打不开）
# 判据：白名单里只允许出现"改密必须走的"(password) 与"渲染改密页必须读的"
# (profile/me，其中 me 是 profile 的别名，必须同放行) 两类。
# 任何"业务上方便"的放行（例如"让他先看一眼工作区"）都不允许加。
PASSWORD_CHANGE_EXEMPT = frozenset({
    '/api/auth/password',    # 漏了这条 = 账号永久锁死
    '/api/auth/profile',
    '/api/auth/me',
    '/api/auth/session',     # 前端启动门禁靠它判断"该渲染哪个页面"
    '/api/auth/logout',
    '/api/health',           # 健康检查永远不能因账号状态而失败
})

#: 视为"写操作"的方法：都要过 Origin 校验；POST/PUT/PATCH 还要求 JSON 请求体。
#: DELETE 不要求 Content-Type —— 它通常没有 body，硬性要求会让删除必然 415。
WRITE_METHODS = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})

# AI 配置权限的环境变量兜底是否已经提醒过（用于"每进程只警告一次"）
_ENV_ADMIN_FALLBACK_WARNED = False


class ApiError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


# --------------------------------------------------------------------------
# 应用容器
# --------------------------------------------------------------------------

class Application:
    """把配置与数据库初始化收拢在一个对象里，便于测试直接复用。"""

    def __init__(self, config):
        self.config = config
        self._init_lock = threading.Lock()
        self._initialized = False
        # 装配访问日志（幂等）：建 <data_dir>/logs、挂 stderr + 轮转文件两个 handler。
        # 放在这里而不是 server.py，是为了让"用 make_server 起服务"的所有入口
        # （含测试）都自动获得同样的行为 —— 只有一个装配点，才不会出现
        # "测试里没日志、线上有日志"这种不对称。
        # 写不了文件时它只 warn 一行并降级为 stderr，**不会**让服务起不来。
        # 挂在 Application 上而不是模块全局：同一进程可能有多个实例（不同 data_dir），
        # 共用一份 handler 会把 A 实例的请求记进 B 实例的日志文件。
        self.access_log = applog.configure(config)

    def ensure_ready(self):
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            db.init_db(self.config.db_path)
            self._initialized = True

    def connect(self):
        self.ensure_ready()
        # 请求级连接包一层：让 _dispatch 能判断"本次请求有没有提交过"，
        # 见 db.TrackedConnection 与 _can_replay()。
        conn = db.TrackedConnection(db.connect(self.config.db_path))
        # 库里的 LLM 设置优先于环境变量（界面里填一次即可）
        stored = {
            'llm_base_url': store.get_setting(conn, 'llm_base_url'),
            'llm_api_key': store.get_setting(conn, 'llm_api_key'),
            'llm_model': store.get_setting(conn, 'llm_model'),
        }
        self.config.apply_llm_settings(stored)
        return conn


# --------------------------------------------------------------------------
# 请求处理
# --------------------------------------------------------------------------

# 判定一个 sqlite3.OperationalError 是不是"写锁争用"。
#
# 为什么要把 SQLITE_READONLY 也算进来 —— 这一点很反直觉，但实测如此：
#   · Linux 上两个进程争用写锁，报的是 SQLITE_BUSY（"database is locked"），
#     而 sqlite3_busy_timeout 会替我们等待并自动重试，应用层什么都感觉不到；
#   · Windows 上同样的争用会被报成 SQLITE_READONLY（"attempt to write a readonly
#     database"），而 busy_timeout **不处理这个错误码** ——
#     结果就是并发写入直接丢 500，日志里看起来还像是"库变成只读了"，非常误导。
# 既然蓝绿发布要求"两个进程可以共用同一个库文件"这件事成立，
# 就不能把它寄托在操作系统的差异上。
_CONTENTION_HINTS = (
    'database is locked',
    'database table is locked',
    'attempt to write a readonly database',
)


def _is_write_contention(error) -> bool:
    if not isinstance(error, sqlite3.OperationalError):
        return False
    text = str(error).lower()
    return any(hint in text for hint in _CONTENTION_HINTS)


def _match_route_pattern(pattern, path):
    """把 '/v1/work-items/{id}/archive' 这样的模板匹配到真实路径。

    只支持整段占位符，不做前缀/正则 —— 路由表是白名单，越简单越不会
    出现"以为只匹配一条、实际上放行了另一条"的意外。
    """
    expected = [part for part in pattern.split('/') if part]
    actual = [part for part in path.split('/') if part]
    if len(expected) != len(actual):
        return None
    params = {}
    for want, got in zip(expected, actual):
        if want.startswith('{') and want.endswith('}') and len(want) > 2:
            name = want[1:-1]
            if not got or name in params:
                return None
            params[name] = got
        elif want != got:
            return None
    return params


class Handler(BaseHTTPRequestHandler):
    server_version = 'GTD4Teams'
    sys_version = ''
    protocol_version = 'HTTP/1.1'

    # 写锁争用时的重试次数（配合 _dispatch 里的指数退避）。
    # 见 _is_write_contention() 与 _dispatch 中"为什么要重试"的说明。
    WRITE_RETRY_ATTEMPTS = 6

    app: Application = None          # 由 make_server 注入

    # ── 集成域的四处最小侵入改造（TDS §16.2）─────────────────────────────
    # 目的只有一个：让 IntegrationHandler 完整继承 _dispatch 的全部保证。
    # ROUTES_TABLE 在模块末尾（ROUTES 定义之后）注入到 Handler 上。
    ROUTES_TABLE = {}
    # 形如 {('DELETE', '/api/admin/api-tokens/{id}'): handler}；{name} 段按路径切片匹配。
    PARAM_ROUTES_TABLE = {}
    SERVE_STATIC = True              # 集成域不托管静态资源
    REJECT_ANY_ORIGIN = False        # 集成域改成"出现 Origin 就拒"
    _token_id = None                 # 访问日志里的令牌 ID（**不是**令牌本体）
    _token_label = ''                # 令牌标签（只进审计，不进访问日志）
    _path_params = {}                # 路径参数，供 handler 读取（不改 handler 签名）
    _api_user = None                 # 集成域鉴权后的账号
    _api_scopes = ()                 # 集成域鉴权后的 scopes

    # 本次请求的**归一化**路径（去尾斜杠）。由 _dispatch 赋值。
    # 类属性默认空串：任何绕过 _dispatch 的调用都拿不到豁免（fail-closed）。
    _path = ''

    # 每请求的访问日志状态。类属性给默认值，是为了让绕过 _dispatch 的调用
    # （`do_OPTIONS`、协议层 `send_error`）不会抛 AttributeError ——
    # 日志相关代码永久性地不允许成为 500 的来源。
    _request_id = ''
    _uid = None
    _status = None
    _started_at = None
    _access_logged = False
    _errcode = None

    def _reset_access_state(self):
        """每个请求的访问状态复位：编号、身份、计时、幂等标记、状态码、错误码。"""
        self._request_id = secrets.token_hex(4)
        self._uid = None
        self._started_at = time.monotonic()
        self._status = None
        self._errcode = None
        self._access_logged = False
        self._path = ''
        self._token_id = None
        self._token_label = ''
        self._path_params = {}
        self._api_user = None
        self._api_scopes = ()

    def _ensure_request_id(self):
        """拿到（必要时生成）本次请求的编号。

        正常路径由 `_dispatch` 生成。少数入口绕过了它（`do_OPTIONS`、
        协议层 `send_error`），这里兜底生成 —— 保证"每条日志都有一个可检索的编号"，
        否则那些行的 `request_id` 是空串，查起来毫无线索。
        """
        request_id = getattr(self, '_request_id', '')
        if not request_id:
            request_id = secrets.token_hex(4)
            self._request_id = request_id
        return request_id

    def send_error(self, code, message=None, explain=None):
        """协议层错误（501 不支持的方法、400 请求行不合法）也要留一条日志。

        这些路径由 `BaseHTTPRequestHandler` 在**解析阶段**直接发出，
        压根不经过 `_dispatch` —— 于是扫描器、协议探测、请求走私尝试
        全部零留痕。而它们恰恰是"有没有人在打我们"的最早信号。

        这里必须显式复位访问状态：keep-alive 连接上，上一个请求留下的
        `_access_logged=True` 会把这条日志静默吞掉（看起来像"没人在探测"）。
        """
        self._reset_access_state()
        self._status = code
        if not getattr(self, 'command', ''):
            # 请求行压根没解析出来：`self.path` 还是**上一个请求**留下的值，
            # 照着记会让这条记录指向一个完全无关的端点（排障时被彻底带偏）。
            self.path = ''
        try:
            super().send_error(code, message, explain)
        finally:
            self._log_access(code)

    def send_header(self, keyword, value):
        """记录"响应已经开始写了"。

        请求处理器一旦发出了响应头，就绝不能再被重试 —— 否则会往同一个连接上
        写出第二份响应。这条判断必须挂在 send_header 上，而不是挂在统一的
        _json 出口上：AI 对话接口是 SSE 直通（自己调 send_header + 写 wfile），
        压根不经过 _json，只盯出口会漏掉它。
        """
        self._responded = True
        super().send_header(keyword, value)

    # ---------------- 基础设施 ----------------

    def log_message(self, fmt, *args):
        pass                          # 访问日志由 _log_access 统一输出

    @staticmethod
    def _redact_path(raw, limit=200):
        """把客户端可控的 URL 变成可安全落盘的一行文本。

        两个必须同时解决的问题（只做一个，日志仍然不可信）：

        1. **PII 落盘**：`self.path` 是含查询串的原始 URL。安全审计实测到
           `! GET /api/auth/check-email?email=x@y.test -> 404` ——
           匿名请求一个 404 就把邮箱写进了日志文件。
           这里把 **所有 query 的值** 换成 `***`，键名保留（否则排障时看不出
           调用方传了哪些参数，而这通常正是关键线索）。
           裸参数（`?debug`，无 `=`）不含值，原样保留。

        2. **日志注入**：`self.path` 来自客户端，换行/制表/控制字符能把一行日志
           伪造出一个"看起来合法"的第二行（`GET /api/x%0d%0aFAKE` 之类）。
           **脱敏如果只管抹值不管抹换行，这条日志仍然可被投毒**，
           所以控制字符一并压平成 `?`（保留内容，便于识别有人在尝试注入）。

        另外做长度截断：超长 URL 会把日志撑爆（也是一个廉价的放大攻击面）。
        调用点**只有一处**：`_log_access`。它每个请求都会执行（成功也记），
        因此这里的成本要保持在"一次正则 + 一次切分"的量级上。
        """
        text = _CONTROL_CHARS_RE.sub('?', str(raw or ''))
        path, separator, query = text.partition('?')
        if separator:
            items = []
            for item in query.split('&'):
                if not item:
                    continue
                key, equals, _value = item.partition('=')
                items.append(f'{key}=***' if equals else key)
            text = f'{path}?{"&".join(items)}'
        if len(text) > limit:
            text = text[:max(limit - 1, 0)] + '…'
        return text

    def _log_access(self, status):
        """每个请求**恰好一条**结构化访问日志（JSONL）。

        与旧实现的差别：以前只有 `status >= 400` 才 `print` 一行纯文本，
        成功的请求完全不留痕 —— 于是 QPS、成功率、P95 延迟都没有数据源。

        三条不可动摇的规则：

        1. **每请求恰好一条**：靠 `_access_logged` 幂等闸门保证。
           `_send` 会调一次，`_dispatch` 的 finally 兜底再调一次（早退、BrokenPipe、
           未发出响应就被中断等路径），两者只能生效一个。重复计数会让
           成功率与 QPS 一起失真，那比没有日志更糟。
        2. **path 必须脱敏**：脱敏在这里**固定**执行，不接受调用方传入
           "已经处理好的 path"。一旦把它变成调用方的责任，就一定会有人忘 ——
           而"每请求都记"把记录量放大了几十倍，
           `?email=secret@test.com` 会以 JSON 形式落盘，看起来更"规范"、
           更像可信数据。所以这里只认 `self.path` + `_redact_path`，没有旁路。
        3. **日志失败绝不影响响应**：整段包在 try 里。日志是辅助，不是依赖。

        `uid` 见 `_uid`：认证成功时由 `_current_user` 写入，认证前就失败的请求
        （401/403/404）记 `null` —— 必须是 JSON null 而不是 `anonymous`/空串，
        否则按 uid 聚合时会多出一个虚构的"匿名用户"分组。

        `errcode` 见 `_errcode`：由 `_error()` 统一写入（它**唯一**掌握
        "这次失败的业务错误码"）。存在的理由是监控指标 A4
        （写锁 / 内部错误关键字）此前只能 `grep -iE 'database i...'` 去匹配
        **traceback 文本** —— 文本一变、语言一换就失效。有 `errcode` 之后，
        "这个 500 是不是 INTERNAL_ERROR"是一条结构化查询，而不是正则赌博。
        正常请求记 `null`（不是 `''`/`'OK'`：聚合时 `null` 才是"没有错误码"）。
        """
        if getattr(self, '_access_logged', False):
            return
        self._access_logged = True
        # 日志实例取自**当前这个 Application**，不是模块全局：
        # 同一进程可能有多个实例（不同 data_dir），串了就会记进别人的文件。
        access = getattr(self.app, 'access_log', None) or applog.default()
        # 健康检查只留一条 DEBUG 心跳：默认级别下不输出（见 ACCESS_LOG_EXCLUDED）。
        if self._path in ACCESS_LOG_EXCLUDED:
            access.heartbeat(f'{self.command} {self._path}')
            return
        try:
            started = getattr(self, '_started_at', None)
            duration = (time.monotonic() - started) * 1000 if started else 0.0
            try:
                ip = self._client_ip()
            except Exception:                # noqa: BLE001
                # 请求行就没解析出来时 self.headers 还不存在。宁可 ip 记空串，
                # 也不要因为取 IP 失败丢掉整条日志。
                ip = ''
            access.write({
                'ts': applog.timestamp(),
                'request_id': self._ensure_request_id(),
                'uid': getattr(self, '_uid', None),
                # 集成域令牌只落 token_id（整数主键），绝不落令牌本体或 Authorization 头。
                # 主入口恒为 null —— 字段存在但为空，便于日志文件保持同构。
                'token_id': getattr(self, '_token_id', None),
                'ip': ip,
                'method': getattr(self, 'command', '') or '',
                'path': self._redact_path(getattr(self, 'path', '')),
                'status': status,
                'errcode': getattr(self, '_errcode', None),
                'duration_ms': round(duration, 1),
            })
        except Exception:                    # noqa: BLE001 - 日志不能影响响应
            pass

    @property
    def cfg(self):
        return self.app.config

    def _read_body(self):
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > self.cfg.max_body_bytes:
            raise ApiError(413, 'PAYLOAD_TOO_LARGE',
                           f'请求体过大（上限 {self.cfg.max_body_bytes // 1048576} MB）')
        raw = self.rfile.read(length)
        # 先标记"已消费"，再解析 JSON：解析失败会抛 BAD_JSON，
        # 那时字节已经从流里读走了，若标记为"未消费"，_discard_body 会再读一次
        # （读不到 → 卡 2 秒超时 → 关连接）。标记的语义是"流已经前进过了"，
        # 不是"解析成功了"。
        self._body_consumed = True
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            raise ApiError(400, 'BAD_JSON', '请求体不是合法 JSON')
        if not isinstance(parsed, dict):
            raise ApiError(400, 'BAD_JSON', '请求体必须是 JSON 对象')
        return parsed

    def _discard_body(self):
        """把请求体读掉并丢弃。

        不读会怎样：那段字节留在 socket 缓冲区里，HTTP/1.1 的 keep-alive 会把它当成
        **下一个请求的请求行** —— 实测同一个连接上，先发一个带 body 的 GET，
        后一个正常的 GET 直接变成 501（BaseHTTPRequestHandler 解析不出方法）。
        同样的失步也发生在"POST 被 403/415/404 早退"的路径上：实测
        `POST /api/rows`（Origin: evil）得 403 后，紧接着的正常登录变成
        `501 Unsupported method ('{}POST')` —— 经反向代理的连接池还会跨连接传染，
        构成"1 个请求换回 2 份响应"的走私原语。

        因此本函数必须在**任何早退响应之前**调用（见 _dispatch 的各个 except 分支）。

        若请求体已经被读过（`_body_consumed`），直接返回：再读一次会阻塞到
        `settimeout(2)` 然后被当成超时关连接 —— 把"已读"当"未读"是另一个坑。
        """
        if getattr(self, '_body_consumed', False):
            return
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return
        if length > self.cfg.max_body_bytes:
            # 超大 body 不读进内存（等于送对方一个放大器），直接关连接
            self.close_connection = True
            return
        # 万一对方声明了长度却没打算发（或只发了一半），读操作会一直等。
        # 给一个很短的超时兜底，别让它占住一个处理线程。
        try:
            self.connection.settimeout(2)
            self.rfile.read(length)
            self._body_consumed = True
        except (OSError, ValueError):
            self.close_connection = True
        finally:
            try:
                self.connection.settimeout(None)
            except OSError:
                pass

    def _check_origin(self):
        """写操作的纵深防御：Origin 与 Host 不一致即拒绝（跨站伪造请求）。"""
        origin = self.headers.get('Origin')
        if not origin:
            return
        # `Origin: null` 来自 sandbox iframe、file:// 、data: 这类**不透明来源** ——
        # 浏览器把它统一写成字面量 "null"，我们无从判断它到底是谁。
        # 下面那条 netloc 比较对它恒不成立（urlparse('null').netloc 是空串），
        # 于是这一层纵深防御被整个架空。实测过：带 Origin: null 的写请求返回 200 并落库。
        # 正确的语义是"来源不明 = 拒绝"，而不是"比较不了 = 放行"。
        if origin.strip().lower() == 'null':
            raise ApiError(403, 'CROSS_ORIGIN_BLOCKED', '跨站请求已被拒绝')
        host = self.headers.get('Host') or ''
        parsed = urlparse(origin)
        if parsed.netloc and parsed.netloc != host:
            raise ApiError(403, 'CROSS_ORIGIN_BLOCKED', '跨站请求已被拒绝')

    def _cookies(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get('Cookie') or '')
        except Exception:
            return {}
        return {key: morsel.value for key, morsel in cookie.items()}

    def _client_ip(self):
        """请求来源 IP，用于限流与审计。

        X-Forwarded-For 是客户端**可以随意伪造**的请求头。无条件采信它，
        攻击者每次请求换一个假 IP 就能把限流键换掉，登录爆破等于不限速。
        因此只在显式声明"我在可信反向代理之后"（GTD_TRUST_PROXY=1）时才读它。
        """
        if self.cfg.trust_proxy:
            forwarded = self.headers.get('X-Forwarded-For')
            if forwarded:
                return forwarded.split(',')[0].strip()
        return self.client_address[0] if self.client_address else ''

    def _send(self, status, body=b'', content_type='application/json; charset=utf-8',
              extra_headers=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        # 与安全相关的默认响应头
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != 'HEAD' and body:
            self.wfile.write(body)
        # 记下状态码：万一这条响应之后又出了异常，_dispatch 的 finally 兜底
        # 才知道该记哪个状态（而不是统一记成 500，制造假的错误率）。
        self._status = status
        self._log_access(status)

    def _json(self, payload, status=200, extra_headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self._send(status, body, extra_headers=extra_headers)

    def _error(self, error):
        if isinstance(error, ApiError):
            status, code, message = error.status, error.code, error.message
        elif isinstance(error, StoreError):
            status, code, message = error.status, error.code, error.message
        elif isinstance(error, llm.LlmError):
            status, code, message = error.status, error.code, error.message
        else:
            status, code, message = 500, 'INTERNAL_ERROR', str(error) or '服务端内部错误'
        # 错误码落到 self 上供访问日志使用：这里是**唯一**知道本次失败错误码的地方
        # （ApiError / StoreError / LlmError / 兜底 500 全收在这一个函数里），
        # 所以只在这里写一次。写在这里而不是各 handler 里，是为了让
        # "忘了传错误码" 这类漏项不可能发生。
        self._errcode = code
        payload = {'error': {'code': code, 'message': message}}
        # 版本冲突要回"当前快照"让客户端能重读后再提交（TDS §16.6.1）。
        # 只有 store.update_versioned 抛出的那一种错误带 current，其余请求不受影响。
        current = getattr(error, 'current', None)
        if current is not None:
            payload['current'] = current
        etag = getattr(error, 'etag', None)
        headers = {'ETag': etag} if etag else None
        self._json(payload, status=status, extra_headers=headers)

    # ---------------- 会话 ----------------

    # 会话活跃时间的刷新节流窗口（秒）。
    #
    # 为什么必须节流：这个 UPDATE 原本发生在**每一个已认证请求**上 ——
    # 也就是所有"读"操作都会顺手去抢一次 SQLite 的写锁（读路径上带写）。
    # 单进程时只是慢一点；而蓝绿切换期间新旧两个进程共用同一个库文件，
    # 读路径带写会把纯读取的流量也卷进写冲突里（实测：并发下直接丢 500）。
    # 活跃时间本来就是记账信息，精确到分钟足够。
    SESSION_TOUCH_SECONDS = 300

    def _current_user(self, conn):
        token = self._cookies().get(security.SESSION_COOKIE)
        if not token:
            return None
        row = conn.execute(
            'SELECT s.token_hash, s.expires_at, s.last_seen_at, u.id, u.email, u.name, '
            'u.is_system_admin, u.must_change_password '
            'FROM sessions s JOIN users u ON u.id = s.user_id '
            'WHERE s.token_hash = ?',
            (security.token_fingerprint(token),),
        ).fetchone()
        if row is None:
            return None

        now = db.now_iso()
        if str(row['expires_at']) <= now:
            # 顺手清理过期会话。清不掉也无所谓：这个会话本来就已经无效了，
            # 而它是写操作 —— 不该因为一次写冲突把"登录已失效"变成"500"。
            try:
                conn.execute('DELETE FROM sessions WHERE token_hash = ?', (row['token_hash'],))
                db.bookkeeping_commit(conn)
            except sqlite3.OperationalError:
                pass
            return None

        # 两个实例级标记随会话一起取出：零额外查询，且"谁都要用到它们"
        # （改密闸门 + 前端的"建号/改密"入口）。用 dict(row).get 兜底，
        # 任何一处 SELECT 漏写列都不会在渲染路径上抛 KeyError。
        fields = {key: row[key] for key in row.keys()}
        user = {
            'id': row['id'], 'email': row['email'], 'name': row['name'] or '',
            'isSystemAdmin': bool(fields.get('is_system_admin')),
            'mustChangePassword': bool(fields.get('must_change_password')),
        }

        # 滑动续期：过期时间固定（不无限延长），只刷新"最近活跃"。
        if db.seconds_since(row['last_seen_at']) >= self.SESSION_TOUCH_SECONDS:
            try:
                conn.execute('UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?',
                             (now, row['token_hash']))
                # 记账提交：它幂等，且绝不能因此判定"本次请求已提交、不可重放" ——
                # 否则一次单纯的时间戳刷新就会让后面的业务写入失去写锁重试。
                db.bookkeeping_commit(conn)
            except sqlite3.OperationalError:
                # 记账失败不是业务失败：本次请求照常继续。
                # 宁可丢一次"活跃时间"，也不要给用户一个 500。
                try:
                    conn.rollback()
                except sqlite3.Error:
                    pass
        # 身份落到 self 上供访问日志使用：日志在响应之后才写，那时 user 早已
        # 出了作用域（它只存在于某个 handler 的局部变量里）。这是"每个请求
        # 一条日志"能带上 uid 的唯一稳妥位置 —— 所有认证路径都必经这里。
        # 认证之前就失败的请求（401/403）保持 None，日志里记 null。
        self._uid = user['id']
        return user

    def _require_user(self, conn):
        user = self._current_user(conn)
        if not user:
            raise ApiError(401, 'UNAUTHENTICATED', '登录状态已失效，请重新登录')
        # 强制改密闸门：放在这里而不是 _dispatch，因为它**唯一必经**且天然 fail-closed ——
        # 以后新增的端点只要按既有写法调 _require_user 就自动被覆盖，
        # 不需要任何人记得去改一个"黑名单"。优先级：401 > 403 闸门 > 端点自身授权错误
        # （不能让人用"我还在改密阶段"来解释"我为什么没有建号权"）。
        if user.get('mustChangePassword') and not self._password_change_exempt():
            raise ApiError(403, 'PASSWORD_CHANGE_REQUIRED',
                           '初始密码尚未修改，请先设置新密码后再继续（当前操作已锁定）')
        return user

    def _api_identity(self, conn):
        """Bearer 令牌鉴权（集成域专用，主入口不调用）。

        延迟导入 integration 以避免循环依赖；这样权限判定、令牌查询与
        last_used_at 记账只有一份实现（TDS §16.3.4）。
        """
        from .integration import authenticate_token       # noqa: PLC0415
        return authenticate_token(self, conn)

    def _before_handler(self, conn, handler):
        """路由命中后、handler 执行前的钩子；默认是空操作。

        放在重试循环内部：每次重跑都重新鉴权（撤销即时生效），
        并确保鉴权发生在任何业务写入之前。集成域覆写它做 scope 检查。
        """
        return None

    def _password_change_exempt(self):
        """当前请求是否在闸门白名单内。

        必须比对**归一化后**的路径（`_dispatch` 里的局部变量 path，带尾斜杠会被 rstrip）。
        用 `self.path` 会让 `POST /api/auth/password/` 落不进白名单 →
        账号永远改不了密码（这是本变更最致命的单点漏项）。
        """
        return self._path in PASSWORD_CHANGE_EXEMPT

    def _begin_session(self, conn, user_id):
        """写入一行会话，**不提交**。

        提交由调用方连同业务写入一起做（注册：用户行+会话行；登录：会话行+口令重算）。
        这样"提交"在整个请求里只发生一次，且提交之后只剩发响应 ——
        把"已提交但后续失败、不可重放"的窗口压到最小（见 db.TrackedConnection）。
        """
        token = security.new_token()
        created, expires = security.session_expiry()
        conn.execute(
            'INSERT INTO sessions (token_hash, user_id, created_at, expires_at, last_seen_at, user_agent) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (security.token_fingerprint(token), user_id, created, expires, created,
             (self.headers.get('User-Agent') or '')[:300]),
        )
        return token, expires

    def _session_cookie(self, token, max_age=security.SESSION_TTL_SECONDS):
        parts = [
            f'{security.SESSION_COOKIE}={token}',
            'Path=/',
            'HttpOnly',
            'SameSite=Lax',
            f'Max-Age={max_age}',
        ]
        # 走 HTTPS 反向代理时才加 Secure，否则明文 HTTP 下浏览器不会回传 Cookie。
        # 同样以 trust_proxy 为前提 —— 不信任代理时，这个头一样是客户端说了算的。
        if (self.cfg.trust_proxy
                and self.headers.get('X-Forwarded-Proto', '').lower() == 'https'):
            parts.append('Secure')
        return '; '.join(parts)

    # ---------------- 路由 ----------------

    def do_GET(self):
        self._dispatch('GET')

    def do_HEAD(self):
        self._dispatch('GET')

    def do_POST(self):
        self._dispatch('POST')

    def do_PATCH(self):
        # 集成域的条件更新用 PATCH；主入口没有该路由，会统一走 404。
        self._dispatch('PATCH')

    def do_PUT(self):
        self._dispatch('PUT')

    def do_DELETE(self):
        # DELETE 过去被当成 POST 派发，于是 `DELETE /api/rows` 会真的执行
        # POST /api/rows 那个 handler（方法混淆）。证书管理需要真正的 DELETE，
        # 顺手把这个别名关系改正：每种方法只匹配自己的路由 key。
        self._dispatch('DELETE')

    def do_OPTIONS(self):
        # 不开 CORS：预检请求直接拒绝，避免任何站点拿到这套接口的跨域授权
        self._error(ApiError(405, 'METHOD_NOT_ALLOWED', '不支持的方法'))

    def _dispatch(self, kind):
        """每个请求的外层：分配 request_id、开始计时、保证**恰好一条**访问日志。

        request_id 在**这里**生成（而不是只在 500 时生成）：它是"把客户端看到的
        报错与服务端日志对上"的唯一凭据。以前只有 500 才有编号，
        于是正常请求出了问题（比如前端说"我点了没反应"）根本没有可查的锚点。
        8 位十六进制沿用既有编号格式，`_internal_error` 直接复用它。

        finally 里的兜底是给"没走到 _send"的路径准备的：
        `BrokenPipeError` 直接 return、SSE 已发头后再出错、`_abort_if_responded`
        只断连接 —— 这些路径以前**一条日志都没有**。而 500 恰恰走的就是
        这类早退分支，等于"最需要日志的时候没有日志"。
        `_log_access` 自身幂等，所以正常路径不会因此写两条。
        """
        self._reset_access_state()
        try:
            self._handle(kind)
        finally:
            # 从未发出过响应 = 客户端提前断开（499），不是服务端错误。
            self._log_access(self._status if self._status is not None
                             else CLIENT_CLOSED_STATUS)

    def _handle(self, kind):
        # 连接是 keep-alive 复用的，请求体"已消费"标记必须每个请求重置，
        # 否则上一个请求的状态会串到下一个请求上。
        self._body_consumed = False
        parsed = urlparse(self.path)
        path = parsed.path.rstrip('/') or '/'
        # 归一化后的路径必须留一份在 self 上：强制改密闸门（_require_user）要按它
        # 比对白名单。不要在两处各自 rstrip 一遍 —— 算法一旦分叉，
        # `POST /api/auth/password/` 就会落不进白名单，账号永久锁死。
        self._path = path
        query = parse_qs(parsed.query)
        # 非写方法的请求体没人消费 —— 必须主动读掉丢弃，否则它会污染同一条
        # keep-alive 连接上的下一个请求（详见 _discard_body 的注释）。
        if kind not in WRITE_METHODS:
            self._discard_body()
        self._responded = False          # 每个请求重置：连接是 keep-alive 复用的

        # 集成域反向的 Origin 策略：**出现就拒**，不是校验是否同源。
        # 浏览器的 fetch 必带 Origin，脚本不带 —— 这一条挡住"任意网页
        # 触发本机写操作"（TDS §16.4.2）。必须在路由查找前执行。
        if self.REJECT_ANY_ORIGIN and 'Origin' in self.headers:
            self._discard_body()
            self._error(ApiError(403, 'CROSS_ORIGIN_BLOCKED', '跨站请求已被拒绝'))
            return

        # 集成域不托管静态资源：所有路径都进路由表，/ 直接 404。
        is_static = (self.SERVE_STATIC
                     and not path.startswith(('/api/', '/v1/', '/mcp')))
        if is_static:
            if kind != 'GET':
                self._discard_body()     # 405 早退前也要把 body 读掉
                self._error(ApiError(405, 'METHOD_NOT_ALLOWED', '静态资源只支持 GET'))
                return
            self._serve_static(parsed.path)
            return

        handler = self.ROUTES_TABLE.get((kind, path))
        if handler is None:
            for (method, pattern), target in self.PARAM_ROUTES_TABLE.items():
                if method != kind:
                    continue
                params = _match_route_pattern(pattern, path)
                if params is not None:
                    handler = target
                    self._path_params = params
                    break
        if handler is None:
            # 404 早退同样会失步：POST /api/<不存在的路径> 带 body 时，
            # 那段字节会变成下一个请求的请求行。
            self._discard_body()
            self._error(ApiError(404, 'NOT_FOUND', f'接口 {path} 不存在'))
            return

        # 写方法三关：Origin（跨站伪造）→ Content-Type（只接受 JSON）→ 读请求体。
        #
        # DELETE / PUT 不要求 JSON 请求体：浏览器与 curl 的 DELETE 默认不带
        # Content-Type，硬性要求会让"删除证书"这种无 body 的操作必然 415。
        # 但它们仍然是写操作，Origin 校验必须照做（CSRF 防护不能漏）。
        body = {}
        if kind in WRITE_METHODS:
            try:
                self._check_origin()
                if kind in ('POST', 'PUT', 'PATCH'):
                    content_type = (self.headers.get('Content-Type') or '').split(';')[0].strip()
                    if content_type != 'application/json':
                        raise ApiError(415, 'UNSUPPORTED_MEDIA_TYPE',
                                       '写操作必须使用 application/json')
                    body = self._read_body()
                elif kind == 'DELETE':
                    # 集成域的软删除要传 expectedVersion：允许带 JSON body。
                    # 没有 body 时照旧直接丢弃（证书删除等既有路径不受影响）。
                    raw_length = self.headers.get('Content-Length') or '0'
                    if str(raw_length).strip() not in ('0', ''):
                        body = self._read_body()
                    else:
                        self._discard_body()
                else:
                    self._discard_body()
            except (ApiError, StoreError, llm.LlmError) as err:
                # 早退之前必须把请求体丢掉：403（跨站）/415（媒体类型）/400（坏 JSON）
                # 若把 body 留在 socket 里，同一 keep-alive 连接上的下一个请求会被
                # 当成以 body 字节开头的请求行 → 501，且可被构造成请求走私。
                # 已消费过时 _discard_body 会立刻返回，不会重复读。
                self._discard_body()
                self._error(err)
                return
            except Exception as err:                   # noqa: BLE001
                self._discard_body()
                self._internal_error(err)
                return

        # ---- 执行处理器；遇到写锁争用就重试 ----
        #
        # 重试 = 把整个 handler 再跑一遍。它安全的**唯一**前提是：
        # 抛异常时这次请求什么都没提交过 —— 每个请求都新建连接，未提交的事务随
        # 连接关闭被丢弃，所以重跑不会留下"写了一半"的痕迹。
        #
        # 三个条件必须同时成立才允许重试：
        #   1. 响应还没开始写（否则会写出第二份响应）—— 见 send_header 的覆盖；
        #   2. 本次请求从未尝试提交（见 db.TrackedConnection）——
        #      曾经"整体重跑"忽略了这一条，结果"commit 成功后紧接着抛锁错"的路径
        #      被重放：实测插入 1 条却落库 6 条；
        #   3. 异常确实是写锁争用（真正的只读挂载、磁盘满之类，重试没有意义）。
        for attempt in range(self.WRITE_RETRY_ATTEMPTS):
            conn = None
            try:
                conn = self.app.connect()
                self._before_handler(conn, handler)
                handler(self, conn, body, query)
                return
            except (ApiError, StoreError, llm.LlmError) as err:
                if self._abort_if_responded():
                    return
                self._error(err)
                return
            except Denied as err:
                if self._abort_if_responded():
                    return
                self._error(ApiError(403, 'PERMISSION_DENIED', str(err)))
                return
            except BrokenPipeError:
                return
            except sqlite3.OperationalError as err:
                if (_is_write_contention(err)
                        and not self._responded
                        and self._can_replay(conn)
                        and attempt + 1 < self.WRITE_RETRY_ATTEMPTS):
                    # 指数退避，上限 0.4 秒；累计约 1 秒，不会把请求拖成超时
                    time.sleep(min(0.05 * (2 ** attempt), 0.4))
                    continue
                self._internal_error(err, conn)
                return
            except Exception as err:                   # noqa: BLE001 - 兜底并留下证据
                self._internal_error(err, conn)
                return
            finally:
                if conn is not None:
                    conn.close()

    @staticmethod
    def _can_replay(conn):
        """本次请求是否可以整体重跑：只要尝试过提交就不行。

        提交点之后还可能失败（取回结果、写会话、改内存配置…），
        那种情况下重放会把已经生效的写再做一遍。宁可返回 500 让人看见，
        也不要"看起来成功、库里两行"。
        """
        if conn is None:
            return True                        # 连连接都没建起来，重试无害
        return not getattr(conn, 'commit_attempted', False)

    def _abort_if_responded(self):
        """响应已经开始写了就只断开连接，不再补一份响应。

        SSE 直通是这条规则的现实来源：200 + 事件流头已经发出去了，
        若这时再走一遍 _error，客户端会先收到 200，body 里又跟一份
        `HTTP/1.1 400 ...` 与整套响应头。那比"连接断了"更难排查。
        """
        if not self._responded:
            return False
        self.close_connection = True
        return True

    def _internal_error(self, error, conn=None):
        """兜底错误：客户端只拿到固定文案 + 一个可对账的编号。

        以前这里把 `str(error)` 直接回给浏览器，于是 SQL 语句、表名、
        绝对路径（以及运行环境细节）都跟着交出去 —— 对攻击者是免费的情报。
        完整异常与栈一律只打到服务端 stderr，并带上同一个编号便于对应。

        `request_id` **复用本次请求的编号**（`_dispatch` 在开头生成的那一个），
        不再自己生成一个新的：客户端拿到的编号必须能在访问日志里检索到。
        自己生成一份的后果是"两边各说一个号"，对账时永远查不上。
        """
        request_id = self._ensure_request_id()
        traceback.print_exc()
        replay_note = '（已提交但后续失败，不可重放）' \
            if getattr(conn, 'commit_attempted', False) else ''
        print(f'  !! [req {request_id}] 内部错误{replay_note}: {error!r}',
              file=sys.stderr, flush=True)
        if self._abort_if_responded():
            return
        self._error(ApiError(500, 'INTERNAL_ERROR',
                             f'服务端内部错误，请将编号 {request_id} 提供给管理员'))

    # ---------------- 静态资源 ----------------

    def _serve_static(self, raw_path):
        """白名单静态托管。

        三道关卡，缺一不可 —— 第一版只做了第一道，结果被自己的测试抓到漏网：
        `/js/../server.py` 的第一段是 `js`（在白名单里），解析后也确实还落在项目目录内，
        于是服务端把**自己的源码**读出去返回了 200。第二道关卡就是为了堵它。
        """
        from urllib.parse import unquote

        try:
            decoded = unquote(raw_path)
        except (UnicodeDecodeError, ValueError):
            self._not_found()
            return

        relative = decoded.lstrip('/')
        if relative == '':
            relative = 'index.html'

        parts = relative.split('/')
        # 关①：逐段校验。空段直接拒（目录列表、'//'、结尾斜杠一并挡掉）；
        #       '.' / '..' / 反斜杠 / NUL 一律拒 —— 它们都用于逃出白名单子树。
        if any(part in ('', '.', '..') or '\\' in part or '\x00' in part for part in parts):
            self._not_found()
            return
        # 关②：第一段必须在白名单里。目录列表也因此不可能出现。
        if parts[0] not in PUBLIC_ENTRIES:
            self._not_found()
            return

        root = self.cfg.root_dir
        try:
            target = (root / relative).resolve()
            base = (root / parts[0]).resolve()
        except (OSError, ValueError):
            self._not_found()
            return

        # 关③：解析之后必须仍在白名单条目之下（挡住符号链接指向外部的情况）
        if target != base and base not in target.parents:
            self._not_found()
            return
        if not target.is_file():
            self._not_found()
            return

        try:
            data = target.read_bytes()
        except OSError:
            self._not_found()
            return

        content_type = MIME_TYPES.get(target.suffix.lower(), 'application/octet-stream')
        # 站点本体不做长期缓存：静态资源靠 index.html / 模块里的 ?v= 串失效，
        # 若这里再叠一层强缓存，改了代码而忘记递增版本串时会看到旧行为，
        # 那种"改了没生效"的故障极难排查（本项目历史上被它误导过）。
        cache = 'no-store' if target.suffix == '.html' else 'no-cache'
        headers = {'Cache-Control': cache}
        if target.suffix == '.html':
            headers['Content-Security-Policy'] = (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: blob:; connect-src 'self'; base-uri 'none'; "
                "form-action 'self'; object-src 'none'; frame-ancestors 'none'"
            )
        self._send(200, data, content_type, headers)

    def _not_found(self):
        self._send(404, b'not found', 'text/plain; charset=utf-8')


# --------------------------------------------------------------------------
# 接口实现
# --------------------------------------------------------------------------

def _user_public(user):
    """账号的对外形状（session / profile / me 共用）。

    两个新字段是**加法式**扩展：`isSystemAdmin`（是否显示建号入口）与
    `mustChangePassword`（前端据此跳到强制改密页）。用 `.get()` 兜底 ——
    任何漏改的 dict 都不该把 500 抛在渲染路径上。
    """
    return {
        'id': user['id'],
        'email': user['email'],
        'name': user.get('name') or '',
        'isSystemAdmin': bool(user.get('isSystemAdmin')),
        'mustChangePassword': bool(user.get('mustChangePassword')),
    }


def h_health(handler, conn, body, query):
    """健康检查（免鉴权）。

    除了"进程还活着"，这里还回传 `version` 与 `build`。
    零停机发布脚本靠它们核对**起来的确实是我刚构建的那一版** ——
    容器健康不代表内容正确，命中旧容器时端口一样是通的。
    这两个字段本身就是公开信息（构建时间戳 / 短版本号），不构成敏感数据泄漏。
    """
    cfg = handler.cfg
    handler._json({
        'ok': True,
        'app': 'GTD4Teams',
        'version': APP_VERSION,
        'build': cfg.build,
        'storage': 'sqlite',
        'local': True,
        'llmConfigured': cfg.llm_configured,
    })


def h_session(handler, conn, body, query):
    user = handler._current_user(conn)
    handler._json({'user': _user_public(user) if user else None})


def h_profile(handler, conn, body, query):
    user = handler._require_user(conn)
    handler._json({'user': _user_public(user), **_user_public(user)})


def h_login(handler, conn, body, query):
    email = str(body.get('email') or '').strip()
    password = str(body.get('password') or '')
    if not email or not password:
        raise ApiError(400, 'MISSING_CREDENTIALS', '请输入邮箱与密码')

    throttle_key = f'{email.lower()}|{handler._client_ip()}'
    if security.is_rate_limited(throttle_key):
        raise ApiError(429, 'RATE_LIMITED', '尝试次数过多，请 15 分钟后再试')

    row = conn.execute(
        'SELECT * FROM users WHERE lower(email) = lower(?)', (email,)
    ).fetchone()
    # 邮箱不存在与口令错误返回同一个错误码，避免变成账号枚举接口
    if row is None or not security.verify_password(password, row['password_hash']):
        security.register_failure(throttle_key)
        raise ApiError(401, 'INVALID_CREDENTIALS', '邮箱或密码不正确')

    security.clear_failures(throttle_key)
    # 登录这条路径不经过 _current_user（它直接查 users），所以 uid 要显式补上。
    # 审计价值很高：`POST /api/auth/login` 的日志能回答"谁在什么时候从哪里登进来"，
    # 而这条记录此前完全不存在。
    handler._uid = row['id']
    # 会话行与"顺手升级口令散列"写在同一个事务里，只提交一次。
    # 提交之后只剩下拼响应 —— 不给重放留下任何可乘之机。
    token, expires = handler._begin_session(conn, row['id'])
    if security.needs_rehash(row['password_hash']):
        conn.execute('UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?',
                     (security.hash_password(password), db.now_iso(), row['id']))
    conn.commit()
    fields = {key: row[key] for key in row.keys()}
    user = {'id': row['id'], 'email': row['email'], 'name': row['name'] or '',
            # 首登就要能强制跳转改密页，所以这里也要带上（否则要等下一次 session）
            'isSystemAdmin': bool(fields.get('is_system_admin')),
            'mustChangePassword': bool(fields.get('must_change_password'))}
    handler._json({'user': user},
                  extra_headers={'Set-Cookie': handler._session_cookie(token)})


def h_logout(handler, conn, body, query):
    token = handler._cookies().get(security.SESSION_COOKIE)
    if token:
        conn.execute('DELETE FROM sessions WHERE token_hash = ?',
                     (security.token_fingerprint(token),))
        conn.commit()
    handler._json({'ok': True},
                  extra_headers={'Set-Cookie': handler._session_cookie('', max_age=0)})


def h_change_password(handler, conn, body, query):
    """改密：**必须与"清零强制改密标记"同一条 UPDATE、同一次 commit**。

    漏了 `must_change_password = 0` 的后果是"改密成功但永远用不了"：
    每次都会被闸门 403，而且再改一次也一样（走的是同一段代码）。
    清零必须发生在原密码校验通过之后 —— 否则一次错误尝试就把用户放行出去了。
    """
    user = handler._require_user(conn)
    old_password = str(body.get('oldPassword') or '')
    new_password = str(body.get('newPassword') or '')

    problem = security.validate_password(new_password)
    if problem:
        raise ApiError(400, 'WEAK_PASSWORD', problem)

    row = conn.execute('SELECT password_hash FROM users WHERE id = ?', (user['id'],)).fetchone()
    if row is None or not security.verify_password(old_password, row['password_hash']):
        raise ApiError(401, 'INVALID_OLD_PASSWORD', '原密码不正确')

    conn.execute(
        'UPDATE users SET password_hash = ?, must_change_password = 0, updated_at = ? '
        'WHERE id = ?',
        (security.hash_password(new_password), db.now_iso(), user['id']))
    # 改密后踢掉其它会话，只保留当前这一个
    token = handler._cookies().get(security.SESSION_COOKIE) or ''
    conn.execute('DELETE FROM sessions WHERE user_id = ? AND token_hash <> ?',
                 (user['id'], security.token_fingerprint(token)))
    conn.commit()
    handler._json({'ok': True})


def _account_actor(conn, user):
    """从**会话**组装 store 层的 actor —— 绝不读请求体里的身份字段。

    `ctx.memberships` 只含 `status='active'` 的行（见 policy.load_context），
    所以"被停用的前管理员"自动不在 admin_team_ids 里，不需要额外判断。
    """
    ctx = load_context(conn, user['id'])
    return {
        'uid': user['id'],
        'is_system_admin': bool(user.get('isSystemAdmin')),
        'admin_team_ids': tuple(team_id for team_id, role in ctx.memberships.items()
                                if role in ('owner', 'admin')),
        'via': 'http',
    }


def h_admin_accounts_create(handler, conn, body, query):
    """建号（三级：系统管理员 / 团队管理员）。

    ⚠️ `/api/admin/*` 这个前缀表示"**账号管理**"，不表示"仅系统管理员"。
    不要给这个前缀加统一的"必须是系统管理员"门槛 —— 那会把团队管理员
    这条主路径直接堵死。具体权限在 store.create_account 内按身份分流。

    请求体里的 `isSystemAdmin` / `mustChangePassword` / `password` 一律**不读**：
    前者唯一的创建入口是 CLI，中者恒为 1，后者由服务端生成。
    """
    user = handler._require_user(conn)            # 认证 + 强制改密闸门
    actor = _account_actor(conn, user)

    password = security.generate_temp_password()  # 明文只存活在本次请求的局部变量里
    result = store.create_account(
        conn, actor,
        email=body.get('email'),
        name=body.get('name'),
        team_id=body.get('teamId'),
        role=body.get('role'),
        password_hash=security.hash_password(password),
    )
    result['password'] = password                 # 只塞进响应：不落库、不打日志
    handler._json(result, status=201,
                  # 响应体含明文口令，不能被任何中间层缓存
                  extra_headers={'Cache-Control': 'no-store'})


def h_admin_accounts_list(handler, conn, body, query):
    """账号列表（按身份收敛范围）。

    - 系统管理员省略 teamId → 实例级（最近创建的 200 个账号）
    - 团队管理员省略 teamId → 400 TEAM_REQUIRED（不给"全量"这个选项）
    - 成员 / 非成员 → 403 FORBIDDEN
    """
    user = handler._require_user(conn)
    actor = _account_actor(conn, user)

    raw = (query.get('teamId') or [None])[0]
    team_id = None if raw in (None, '', 'null', 'None') else raw
    handler._json(store.list_accounts(conn, actor, team_id))


def h_admin_cert_get(handler, conn, body, query):
    """读取当前 TLS 证书状态（**绝不返回私钥，也不返回证书全文**）。

    三种状态都要能显示：
      · 未配置（`configured: false` / `source: none`）
      · 用户上传的（`source: uploaded`，元数据在 settings.tls_cert_meta）
      · 运维脚本生成的（`source: selfsigned`：有 tls.crt 但没有上传记录）
    """
    _require_instance_admin(handler, conn, what='管理 TLS 证书')
    handler._json(tls_cert.status(conn, handler.cfg.cert_dir))


def h_admin_cert_upload(handler, conn, body, query):
    """上传证书与私钥（用 JSON 传 PEM 文本，不做 multipart）。

    为什么不用 multipart：证书和私钥都是几 KB 文本，JSON 完全够用；
    而手写 multipart 边界解析是 bug 温床，本项目也没有任何现成的解析器。

    校验顺序由 tls_cert.validate_pair 保证（格式 → 加密 → 配对 → 有效期），
    每类失败给不同的错误码 —— 用户拿到的是"该换什么"而不是笼统的"上传失败"。

    响应头 `Cache-Control: no-store`：这里虽然不回私钥，但回的是实例的
    安全配置详情，没有理由让它进任何中间层缓存。
    """
    user = _require_instance_admin(handler, conn, what='管理 TLS 证书')
    try:
        payload = tls_cert.save(
            conn, handler.cfg.cert_dir,
            body.get('certPem'), body.get('keyPem'),
            uploaded_by=user.get('email'),
        )
    except tls_cert.CertError as err:
        raise ApiError(err.status, err.code, err.message) from err
    handler._json(payload, extra_headers={'Cache-Control': 'no-store'})


def h_admin_cert_delete(handler, conn, body, query):
    """删除证书（幂等）并打"待应用"标记 —— Caddy 需要回到无证书状态。"""
    _require_instance_admin(handler, conn, what='管理 TLS 证书')
    try:
        payload = tls_cert.delete(conn, handler.cfg.cert_dir)
    except tls_cert.CertError as err:
        raise ApiError(err.status, err.code, err.message) from err
    handler._json(payload, extra_headers={'Cache-Control': 'no-store'})


def h_admin_config(handler, conn, body, query):
    """GET /api/admin/config —— 回传集成域的开关与端口（**只读**）。

    前端系统管理页靠这四个字段把"状态未知 + .env 指引"替换成真实状态；
    消费端按 `typeof enabled === 'boolean'` 判断，所以这里必须是真正的
    bool / int，不能回字符串或 null。

    权限：`_require_instance_admin`（与 AI 配置、证书管理同一个门槛）。
    未登录 → 401 UNAUTHENTICATED；普通成员 → 403 ADMIN_ONLY。

    **刻意不提供写端点**：这四个值只能由 .env 的 GTD_API_ENABLED /
    GTD_MCP_ENABLED / GTD_API_PORT / GTD_MCP_PORT 配置并重启进程生效。
    改开关意味着要重建监听器（bind 新端口 / 关闭已监听端口），是另一类
    运维操作，本批不做。若做成可热改的写接口，会给出"改了立即生效"的
    错误预期，因此宁可不提供。

    返回体**只有**下面四个键，与 `config.describe()` 同源取值：不回 LLM
    密钥、管理员邮箱、数据库路径等任何其它实例信息（§16.8.4 "initialize
    不泄露实例信息" 的同源纪律）。非 GET 请求体由 `_handle` 统一
    `_discard_body`，无需在此重复。
    """
    _require_instance_admin(handler, conn, what='查看集成域配置')
    cfg = handler.cfg
    handler._json({
        'apiEnabled': bool(cfg.api_enabled),
        'apiPort': int(cfg.api_port),
        'mcpEnabled': bool(cfg.mcp_enabled),
        'mcpPort': int(cfg.mcp_port),
    }, extra_headers={'Cache-Control': 'no-store'})


def h_workspace(handler, conn, body, query):
    user = handler._require_user(conn)
    ctx = load_context(conn, user['id'])
    handler._json(store.load_workspace(conn, ctx))


def h_rows(handler, conn, body, query):
    """通用行级读写：等价于云版本的 database.from(table).insert/update/delete。

    响应统一是 `{rows: [...]}`。被权限拦下时 rows 为空数组 —— 前端据此提示
    "可能不存在或不在你可见的范围内"，不会假装成功。
    """
    user = handler._require_user(conn)
    ctx = load_context(conn, user['id'])

    table = str(body.get('table') or '')
    op = str(body.get('op') or 'insert')
    payload = body.get('payload') or {}
    if not isinstance(payload, dict):
        raise ApiError(400, 'BAD_PAYLOAD', 'payload 必须是 JSON 对象')

    if op == 'insert':
        rows = store.insert_row(conn, ctx, table, payload)
    elif op == 'update':
        rows = store.update_row(conn, ctx, table, body.get('id'), payload)
    elif op == 'delete':
        rows = store.delete_row(conn, ctx, table, body.get('id'))
    else:
        raise ApiError(400, 'BAD_OP', f'不支持的操作 {op}')
    handler._json({'rows': rows})


def h_team_create(handler, conn, body, query):
    user = handler._require_user(conn)
    ctx = load_context(conn, user['id'])
    team = store.create_team_with_owner(
        conn, ctx,
        name=body.get('name'),
        description=body.get('description'),
        mission=body.get('mission'),
        join_code=body.get('joinCode'),
        # 邮箱与显示名以服务端会话为准，不采信客户端传来的身份（避免伪造归属文案）
        owner_email=user['email'],
        owner_name=user.get('name') or body.get('ownerName'),
    )
    handler._json({'team': team}, status=201)


def h_team_join(handler, conn, body, query):
    user = handler._require_user(conn)
    ctx = load_context(conn, user['id'])
    team_id = store.join_team_by_code(
        conn, ctx, body.get('code'),
        user_email=user['email'], user_name=user.get('name'),
    )
    # 前端用的是 `state.activeTeamId = Number(teamId)`，这里直接回 id 而不是对象
    handler._json({'teamId': team_id})


def h_review_save(handler, conn, body, query):
    user = handler._require_user(conn)
    ctx = load_context(conn, user['id'])
    payload = body.get('payload') or {}
    if not isinstance(payload, dict):
        raise ApiError(400, 'BAD_PAYLOAD', 'payload 必须是 JSON 对象')
    rows = store.save_review(conn, ctx, payload, body.get('existingId'))
    handler._json({'rows': rows})


def h_log(handler, conn, body, query):
    """运行日志上报。

    允许未登录写入：登录失败、注册失败这类故障正是最需要留证的场景。
    代价是匿名调用者可写库，因此限流 + 逐字段截断（见 store.write_app_log）。
    """
    key = f'log|{handler._client_ip()}'
    if security.is_rate_limited(key):
        raise ApiError(429, 'RATE_LIMITED', '日志上报过于频繁')
    security.bump_counter(key)
    user = handler._current_user(conn)
    ctx = load_context(conn, user['id']) if user else load_context(conn, '')
    handler._json({'rows': store.write_app_log(conn, ctx, body)})


def h_access_logs(handler, conn, body, query):
    """GET /api/logs —— 读服务端自己的结构化访问日志（**仅实例管理员**）。

    为什么需要它：日志文件在容器里（`<data_dir>/logs/app.jsonl`），
    但运维最常问的三个问题——"刚才那个 500 的编号对应哪条请求"、
    "现在有没有人在被限流"、"服务到底有没有在收流量"——
    不该逼人去宿主机 `docker exec` + `tail`。有了这个接口，
    排障可以完全在浏览器里完成。

    权限：复用 `_require_instance_admin`（与 AI 配置、证书管理同一个门槛）。
    未登录 → 401；成员 → 403 ADMIN_ONLY。
    日志里有全部用户的 IP、路径与身份，是**实例级**的运维数据，
    团队级的 owner/admin 也不该看到（他们能看到的只是自己团队的数据）。

    参数：
      · limit  默认 100，上限 1000（超出按 1000 处理）
      · status 按状态码精确过滤（如 500）
      · path   按日志里的 path 子串过滤（如 /api/workspace）
      · q      全文子串（大小写不敏感），可用来查 request_id / uid / ip

    返回 `{"logs": [...], "total": <当前文件行数>, "truncated": <是否还有更早的匹配>,
    "fileLogging": <日志有没有在写>, "degradeReason": <写不了的原因|null>}`。

    `logs` 是**时间正序**（旧 → 新），等价于 `tail -n`。"最新在最上"是渲染偏好，
    交给前端反转 —— 数据层与数据源同序，才不会把一次视觉决策固化进接口契约
    （否则以后想"按时间正序看完整链路"还得再开一个参数）。
    `truncated` 为真表示"更早的位置可能还有匹配记录没返回"，因为本接口只给最近 N 条、不做翻页。

    `fileLogging` / `degradeReason` 的存在是为了让**空结果没有歧义**：
    `logs: []` 有两种完全不同的含义 —— "真的没有请求" 与 "日志压根没写进去"。
    不区分就等于让"空"看起来正常，而这正是本项目反复踩过的病
    （`probe.sh` 空档 exit 0、`config is unchanged` 仍打印重载成功）。
    `fileLogging=false` 时 `degradeReason` 给出具体原因（权限/挂载）。

    只读文件、不碰数据库：日志不是业务数据，不进库（进库会让每次请求都抢写锁，
    把日志本身变成故障源）。
    """
    _require_instance_admin(handler, conn, what='查看运行日志')

    # 先看日志到底有没有在写：降级状态下**根本不碰文件**。
    #
    # 为什么不碰：日志不可用时（`<data_dir>/logs` 是个普通文件、或目录只读），
    # 打开文件**必然失败**，而"打开失败"会被翻译成 500 LOG_UNREADABLE ——
    # 于是「已经知道自己写不进去」的情况下，还要去读它、再把"降级"报成"服务端错误"。
    # 实测就是这条把 L0 门禁拦下来的（Linux 抛 Errno 20 NotADirectoryError，
    # Windows 抛 WinError 183，两边异常类型还不一样）。
    # 这个判断不依赖任何 errno 语义，跨平台行为一致，而且语义正确：
    # 日志不可用时该说"日志不可用"，不是"读取失败"。
    logging_status = (getattr(handler.app, 'access_log', None) or applog.default()).status()
    if not logging_status.get('fileLogging'):
        handler._json({
            'logs': [], 'total': 0, 'truncated': False,
            'fileLogging': False,
            'degradeReason': logging_status.get('problem') or '日志未启用文件落盘',
        }, extra_headers={'Cache-Control': 'no-store'})
        return

    def first(name):
        values = query.get(name) or []
        return values[0] if values else None

    limit = applog.DEFAULT_LIMIT
    raw_limit = first('limit')
    if raw_limit not in (None, ''):
        try:
            limit = int(str(raw_limit).strip())
        except (TypeError, ValueError):
            raise ApiError(400, 'INVALID_LIMIT', 'limit 必须是整数') from None
        if limit < 1:
            raise ApiError(400, 'INVALID_LIMIT', 'limit 必须大于 0')
        # 上限是硬性的：一次请求把 300MB 读进内存，等于给自己造一个 OOM 入口。
        limit = min(limit, applog.MAX_LIMIT)

    status = None
    raw_status = first('status')
    if raw_status not in (None, ''):
        try:
            status = int(str(raw_status).strip())
        except (TypeError, ValueError):
            raise ApiError(400, 'INVALID_STATUS', 'status 必须是 HTTP 状态码') from None
        if not 100 <= status <= 599:
            raise ApiError(400, 'INVALID_STATUS', 'status 必须是 100~599 之间的整数')

    # 子串条件截断到 100 字符：它会被逐行比对，超长输入纯粹是浪费 CPU。
    path_contains = (first('path') or '').strip()[:100] or None
    contains = (first('q') or '').strip()[:100] or None

    try:
        payload = applog.read_recent(
            handler.cfg.log_file,
            limit=limit, status=status,
            path_contains=path_contains, contains=contains,
        )
    except applog.LogReadError as err:
        # 走到这里说明文件**存在且可打开**，但真的 I/O 出错了（EIO 之类）。
        # 这种值得让人看见，不要伪装成"没有日志"。
        raise ApiError(500, 'LOG_UNREADABLE',
                       f'日志文件读取失败（请检查数据目录权限与挂载）：{err}') from err
    # 文件存在但读不动（权限/路径不是目录）：`read_recent` 不抛异常，而是把原因
    # 放在内部标记里回传。这里翻译成"日志不可用"，并且**不把标记泄进响应**。
    unreadable = payload.pop(applog.READ_UNAVAILABLE, None)
    if unreadable is not None:
        payload['fileLogging'] = False
        payload['degradeReason'] = f'日志文件读取失败：{unreadable}'
    else:
        # 落盘状态随结果一起回：`logs: []` 到底是"没流量"还是"写不进去"，
        # 必须让调用方一眼能分辨 —— `fileLogging` / `degradeReason` 就是那对答案。
        payload['fileLogging'] = bool(logging_status.get('fileLogging'))
        payload['degradeReason'] = logging_status.get('problem')
    handler._json(payload, extra_headers={'Cache-Control': 'no-store'})


def h_export(handler, conn, body, query):
    """HTTP 导出：**只导出当前账号可见的行**。

    这里曾经直接调用 store.export_all()（CLI 整库备份那条路径），后果是
    任何刚注册的账号一次 GET 就能拿到 users.password_hash、
    settings.llm_api_key 明文、以及别人的私有工作项 —— 接口成了拖库入口。
    现在改走 export_visible()：逐表过 policy 的 select 策略，
    settings 整表不出现，users 只有自己那一行且不含凭据列。
    CLI 的 --export 不受影响（灾备仍然需要整库）。
    """
    user = handler._require_user(conn)
    ctx = load_context(conn, user['id'])
    handler._json(store.export_visible(conn, ctx, user))


def h_llm_config_get(handler, conn, body, query):
    handler._require_user(conn)
    cfg = handler.cfg
    handler._json({
        'configured': cfg.llm_configured,
        'baseUrl': cfg.llm_base_url,
        'model': cfg.llm_model,
        # 绝不回显密钥，连掩码都不给：掩码容易被人当凭据复制走
        'apiKeySet': bool(cfg.llm_api_key),
    })


# 拒绝的 host 后缀：*.localhost / *.internal 属于"只在本机/内网有意义"的名字，
# 放在公网服务上指向它们，等价于让服务端替调用方访问内网。
_BLOCKED_HOST_SUFFIXES = ('.localhost', '.internal')


def _is_private_host(hostname) -> bool:
    """回环 / 私网 / 链路本地 / 元数据 / 未指定地址 → True。

    用 `ipaddress` 判断字面 IP，不手写正则拼网段：网段清单是会写漏的，
    而 `ip_address()` 的 is_private / is_loopback / is_link_local 是标准库
    按 RFC 维护的判定。169.254.169.254（云元数据）由 is_link_local 覆盖，
    `0.0.0.0` 由 is_unspecified 覆盖。

    只判断字面 IP，不做 DNS 解析：本项目的定位是"允许接自建 vLLM/Ollama"，
    配置写入时去解析域名会让离线环境/内网 DNS 把保存动作卡住或误拒。
    域名解析到内网（DNS rebinding）属于更外层（网络出口策略）该管的事。
    """
    host = (hostname or '').strip().strip('.').lower()
    if not host:
        return True
    if host == 'localhost' or any(host.endswith(suffix) for suffix in _BLOCKED_HOST_SUFFIXES):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # 十进制 / 八进制 / 十六进制 / 短式写法（`2852039166`、`0177.0.0.1`、
        # `0x7f000001`、`127.1`）：`ip_address()` 直接拒，但 **socket 层能解析**。
        # 不归一化就等于白名单可绕 —— 实测 `baseUrl=http://2852039166/v1`
        # （=169.254.169.254 云元数据）曾被保存成功，随后宿主监听器收到了
        # `Authorization: Bearer sk-...`。所以这里必须用 inet_aton 再判一次。
        try:
            address = ipaddress.ip_address(socket.inet_aton(host))
        except (OSError, ValueError):
            return False               # 确实不是 IP 字面量：交给域名白名单
    return (address.is_loopback or address.is_private or address.is_link_local
            or address.is_unspecified or address.is_reserved or address.is_multicast)


def _validate_base_url(raw, cfg):
    """写入前校验 AI 服务地址。返回规范化后的地址。

    这道校验挡的不是"填错地址"，而是**凭据外带**：baseUrl 决定服务端把
    `Authorization: Bearer <真实密钥>` 发到哪台主机。既然允许界面配置它，
    就必须先挡住"内网 / 回环 / 元数据"这类目标（SSRF），否则一次配置就能把
    密钥送到攻击者手里，或让服务端替他读 169.254.169.254 上的云凭据。

    允许自定义公网 host 是既有产品能力（接自建模型服务），所以这里
    只做这一层拦截；需要更严时由部署方设置 GTD_LLM_ALLOWED_HOSTS 收紧。
    """
    text = str(raw or '').strip()
    if not text:
        return ''                      # 允许清空：清空后回落到环境变量/默认值
    parsed = urlparse(text)
    if (parsed.scheme or '').lower() not in ('http', 'https'):
        raise ApiError(400, 'INVALID_BASE_URL',
                       '服务地址只支持 http/https 协议，且不允许指向内网/回环地址')
    if not parsed.hostname:
        raise ApiError(400, 'INVALID_BASE_URL',
                       '服务地址缺少主机名，且不允许指向内网/回环地址')
    if parsed.username or parsed.password:
        raise ApiError(400, 'INVALID_BASE_URL',
                       '服务地址里不允许包含用户名或密码，且不允许指向内网/回环地址')
    if _is_private_host(parsed.hostname):
        raise ApiError(400, 'INVALID_BASE_URL',
                       '服务地址不允许指向内网/回环地址（自建模型服务请由部署方通过 '
                       'GTD_LLM_BASE_URL 指定）')
    if cfg.llm_allowed_hosts and parsed.hostname.lower() not in cfg.llm_allowed_hosts:
        raise ApiError(400, 'INVALID_BASE_URL',
                       '该主机不在 GTD_LLM_ALLOWED_HOSTS 白名单内')
    return text.rstrip('/')


def _warn_env_admin_fallback(conn):
    """环境变量兜底生效时打一行弃用提示（每个进程只打一次）。

    本期采用"OR 兜底"：`is_system_admin` **或** `GTD_ADMIN_EMAILS` 白名单都能改 AI 配置。
    纯切 DB 而不兜底，升级后原白名单里的邮箱会突然失去权限
    （"我配了管理员却没权限"）。但兜底必须**可见** —— 否则 v1.2 删掉环境变量方案时，
    没人知道还有部署在依赖它。
    """
    global _ENV_ADMIN_FALLBACK_WARNED
    if _ENV_ADMIN_FALLBACK_WARNED:
        return
    _ENV_ADMIN_FALLBACK_WARNED = True
    print('  ! 弃用提示：AI 配置权限正在从 GTD_ADMIN_EMAILS 迁移到 users.is_system_admin。'
          '本次通过是**环境变量白名单**兜底的，请对该账号执行 '
          '`python server.py --grant-admin <邮箱>`（v1.2 将移除环境变量方案）。',
          file=sys.stderr, flush=True)


def _require_instance_admin(handler, conn, *, what='该操作'):
    """实例级管理员门槛（AI 配置 / 证书管理共用；**唯一**实现）。

    判据顺序写死：库内 `is_system_admin` 优先 → `GTD_ADMIN_EMAILS` 兜底 → 否则 403。
    兜底时必须打一行弃用提示（见 _warn_env_admin_fallback）：
    不然 v1.2 删掉环境变量方案时，没人知道还有部署在依赖它。

    注意：这是**实例级**门槛，与团队级（owner/admin）无关。
    `what` 只影响文案，不影响判定。
    """
    user = handler._require_user(conn)
    cfg = handler.cfg
    if user.get('isSystemAdmin'):
        return user                       # 库内身份优先，不读环境变量
    if cfg.is_admin_email(user.get('email')):
        _warn_env_admin_fallback(conn)    # 过渡期兜底 + 留痕
        return user
    raise ApiError(403, 'ADMIN_ONLY',
                   f'仅实例管理员可{what}：请由系统管理员操作，'
                   '或用 `python server.py --grant-admin <邮箱>` 授予实例管理权限'
                   '（旧的 GTD_ADMIN_EMAILS 环境变量方案已进入弃用期）')


def h_llm_config_set(handler, conn, body, query):
    """修改 AI 配置（实例级）。

    两道门，缺一不可：
      1. **谁能改**：`_require_instance_admin`（系统管理员；过渡期 env 兜底）。
         这条接口能改 baseUrl，而 baseUrl 决定"服务端把密钥发到哪"；
         对任何账号开放等于任何账号都能把厂商密钥转发给自己。
      2. **能改成什么**：baseUrl 必须过协议 + 主机校验（见 _validate_base_url）。

    写入合并成**一次提交**：先把三项要写的数据备齐，最后一次事务写完，
    提交成功之后才改内存里的 cfg。这样"提交成功但随后抛异常"的窗口消失，
    也不会出现"库没写进去、进程却已按新值工作"的错位。
    """
    _require_instance_admin(handler, conn, what='修改 AI 配置')
    cfg = handler.cfg

    updates = {}          # settings 表键值；None 表示删除
    pending = []          # (cfg 属性, 新值)：提交成功之后才生效

    if 'baseUrl' in body:
        value = _validate_base_url(body.get('baseUrl'), cfg)
        updates['llm_base_url'] = value
        if value:
            pending.append(('llm_base_url', value))
    if 'model' in body:
        value = str(body.get('model') or '').strip()
        updates['llm_model'] = value
        if value:
            pending.append(('llm_model', value))
    if 'apiKey' in body:
        value = str(body.get('apiKey') or '').strip()
        updates['llm_api_key'] = value or None
        pending.append(('llm_api_key', value))

    store.apply_settings(conn, updates)          # 单次事务、单次提交
    for attribute, value in pending:
        setattr(cfg, attribute, value)

    handler._json({'configured': cfg.llm_configured,
                   'baseUrl': cfg.llm_base_url,
                   'model': cfg.llm_model})


def h_llm_models(handler, conn, body, query):
    handler._require_user(conn)
    handler._json({'models': llm.list_models(handler.cfg)})


def h_llm_chat(handler, conn, body, query):
    """AI 转发。

    浏览器永远拿不到厂商密钥：它在服务端，只在这里被换成上游调用。
    支持 `stream: true` 时以 SSE 直通，方便前端边生成边渲染。
    """
    handler._require_user(conn)
    messages = body.get('messages')
    if not isinstance(messages, list) or not messages:
        raise ApiError(400, 'BAD_PAYLOAD', 'messages 必须是非空数组')

    options = {
        'messages': messages,
        'model': body.get('model') or None,
        'temperature': body.get('temperature'),
        'max_tokens': body.get('max_tokens'),
        'response_format': body.get('response_format'),
    }

    if not body.get('stream'):
        result = llm.chat(handler.cfg, **options)
        handler._json(result)
        return

    # 流式：逐块写 SSE。此时不能再用 _json（它要算 Content-Length），手动发头。
    handler.send_response(200)
    handler.send_header('Content-Type', 'text/event-stream; charset=utf-8')
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Accel-Buffering', 'no')
    handler.send_header('Connection', 'close')
    handler.end_headers()
    try:
        for event in llm.stream_chat(handler.cfg, **options):
            payload = json.dumps(event, ensure_ascii=False)
            handler.wfile.write(f'data: {payload}\n\n'.encode('utf-8'))
            handler.wfile.flush()
        handler.wfile.write(b'data: [DONE]\n\n')
        handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError):
        pass
    except (llm.LlmError, ApiError) as err:
        # 响应头（200 + 事件流）已经发出去了，这里**不能再补一份 HTTP 响应** ——
        # 那会让客户端收到两个状态行，body 里跟着一整套响应头，比直接失败更难查。
        # 改成一条 SSE 错误事件：前端照常按事件流解析，就能显示出错原因。
        try:
            code = getattr(err, 'code', None) or 'LLM_ERROR'
            frame = json.dumps({'error': {'code': code, 'message': str(err)}},
                               ensure_ascii=False)
            handler.wfile.write(f'event: error\ndata: {frame}\n\n'.encode('utf-8'))
            handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
    finally:
        # 无论正常结束还是出错，都必须断开：SSE 响应没有 Content-Length，
        # 客户端只能靠 EOF 判断"流结束了"，不收尾就会一直等下去。
        handler.close_connection = True
        # SSE 自己发头、自己写 wfile，压根不经过 _send —— 不在这里补一条，
        # AI 对话（最贵、最容易出问题的一条接口）就完全没有访问日志。
        handler._log_access(200)


def _deferred_integration(name):
    """令牌管理端点在浏览器面，但实现放在 integration.py（避免模块级循环导入）。"""
    def proxy(handler, conn, body, query):
        from . import integration                        # noqa: PLC0415
        return getattr(integration, name)(handler, conn, body, query)
    proxy.__name__ = name
    return proxy


ROUTES = {
    ('GET', '/api/health'): h_health,
    ('GET', '/api/auth/session'): h_session,
    ('GET', '/api/auth/profile'): h_profile,
    ('GET', '/api/auth/me'): h_profile,
    ('POST', '/api/auth/login'): h_login,
    ('POST', '/api/auth/logout'): h_logout,
    ('POST', '/api/auth/password'): h_change_password,
    # 账号管理：权限在 handler 内按身份分流（系统管理员 / 团队管理员），
    # 不要给 /api/admin/* 加统一门槛。
    ('POST', '/api/admin/accounts'): h_admin_accounts_create,
    ('GET', '/api/admin/accounts'): h_admin_accounts_list,
    # 证书管理（实例管理员）：运维的 HTTPS 部署从这里拿证书
    ('GET', '/api/admin/cert'): h_admin_cert_get,
    ('POST', '/api/admin/cert'): h_admin_cert_upload,
    ('DELETE', '/api/admin/cert'): h_admin_cert_delete,
    # 本地 API 令牌管理（实例管理员或本人）。令牌明文只在这里回一次。
    ('GET', '/api/admin/api-tokens'): _deferred_integration('admin_tokens_list'),
    ('POST', '/api/admin/api-tokens'): _deferred_integration('admin_tokens_create'),
    # 集成域开关与端口（实例管理员，只读）：只能由 .env 配置并重启，无写端点。
    ('GET', '/api/admin/config'): h_admin_config,
    ('GET', '/api/workspace'): h_workspace,
    ('POST', '/api/rows'): h_rows,
    ('POST', '/api/teams/create'): h_team_create,
    ('POST', '/api/teams/join'): h_team_join,
    ('POST', '/api/reviews/save'): h_review_save,
    ('POST', '/api/logs'): h_log,
    # 读服务端访问日志（实例管理员）。与上面那条**方法不同、语义不同**：
    # POST /api/logs 是前端把运行日志上报进库；GET /api/logs 是运维读服务端自己的日志文件。
    ('GET', '/api/logs'): h_access_logs,
    ('GET', '/api/export'): h_export,
    ('GET', '/api/llm/config'): h_llm_config_get,
    ('POST', '/api/llm/config'): h_llm_config_set,
    ('GET', '/api/llm/models'): h_llm_models,
    ('POST', '/api/llm/chat'): h_llm_chat,
}

#: 带路径参数的路由。`_handle` 在精确匹配失败后按注册顺序匹配。
PARAM_ROUTES = {
    ('DELETE', '/api/admin/api-tokens/{id}'):
        _deferred_integration('admin_tokens_revoke'),
    ('POST', '/api/admin/api-tokens/{id}/rotate'):
        _deferred_integration('admin_tokens_rotate'),
}

# ROUTES 定义在 Handler 之后，这里完成"类属性指向唯一路由表"的注入。
Handler.ROUTES_TABLE = ROUTES
Handler.PARAM_ROUTES_TABLE = PARAM_ROUTES


# --------------------------------------------------------------------------
# 服务器
# --------------------------------------------------------------------------

def make_server(config):
    """创建（但不启动）HTTP 服务。返回 (server, application)。"""
    application = Application(config)
    application.ensure_ready()

    handler_class = type('BoundHandler', (Handler,), {'app': application})

    class BoundServer(ThreadingHTTPServer):
        daemon_threads = True          # 主线程退出即不再等待挂在连接上的线程
        allow_reuse_address = True     # 重启服务不会被 TIME_WAIT 卡住端口

        def __init__(self, address):
            super().__init__(address, handler_class)

    try:
        server = BoundServer((config.host, config.port))
    except OSError as err:
        raise SystemExit(
            f'无法监听 {config.host}:{config.port} —— {err}\n'
            f'换一个端口：python server.py --port 8788'
        ) from err
    return server, application


def make_integration_servers(config, application):
    """按开关创建第二/第三监听器；**关闭时不创建任何 socket**。

    这是 TDS §16.1 不变式 3 的落地：开关默认关闭，且关闭时连 bind 都不做 ——
    不是"绑了但拒绝请求"。地址硬编码 127.0.0.1，没有任何配置项能改。

    返回 list[ThreadingHTTPServer]；调用方负责启动线程与 server_close()。
    任一端口占用时先关掉已创建的那一个，再给明确错误（不是笼统"启动失败"）。
    """
    specs = []
    if getattr(config, 'api_enabled', False):
        specs.append(('api', config.api_port))
    if getattr(config, 'mcp_enabled', False):
        specs.append(('mcp', config.mcp_port))

    servers = []
    for label, port in specs:
        if label == 'api':
            from .integration import IntegrationHandler as base        # noqa: PLC0415
        else:
            from .mcp import McpHandler as base                        # noqa: PLC0415

        handler_class = type('Bound' + label.title() + 'Handler', (base,),
                             {'app': application})

        class BoundIntegrationServer(ThreadingHTTPServer):
            daemon_threads = True
            allow_reuse_address = True

            def __init__(self, address):
                super().__init__(address, handler_class)

        try:
            server = BoundIntegrationServer(('127.0.0.1', int(port)))
        except (OSError, ValueError) as err:
            for opened in servers:
                try:
                    opened.server_close()
                except OSError:
                    pass
            code = 'api_port_in_use' if label == 'api' else 'mcp_port_in_use'
            raise SystemExit(
                f'[{code}] 无法监听 127.0.0.1:{port} —— {err}\n'
                f'请换一个端口：GTD_{"API" if label == "api" else "MCP"}_PORT=<未占用端口>'
            ) from err
        servers.append(server)
    return servers


def free_port():
    """给测试用：要一个当前空闲的端口。"""
    import socket
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]
