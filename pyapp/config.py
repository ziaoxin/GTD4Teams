"""运行配置。

优先级：命令行参数 > 环境变量 > 数据目录里的 settings 表 > 内置默认值。

为什么把 LLM 凭据也放在库里（settings 表）：本应用的定位是"随处可运行的单机应用"，
让使用者去改环境变量、重启服务、再记住变量名，是不必要的摩擦。放进库里之后，
在界面里填一次即可；同时环境变量仍然可用，适合容器/服务化部署。
密钥只经由 /api/llm/config 写入，**任何读取接口都不会把它回显出来**（见 httpd.py）。
"""

import os
from pathlib import Path

from . import APP_VERSION

# 项目根目录：本包（pyapp/）的上一级
ROOT_DIR = Path(__file__).resolve().parent.parent

# 站点资源的白名单：只有这些顶层条目会被 HTTP 托管出去。
# 采用白名单而不是黑名单，是因为黑名单一旦漏项就会把源码、测试夹具、
# 数据目录甚至备份文件直接公开（云版本正是踩过"静态托管把 .smoke/ 一起公开"的坑）。
PUBLIC_ENTRIES = {'index.html', 'js', 'css', 'assets', 'img', 'favicon.ico'}

MIME_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.mjs': 'text/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.json': 'application/json; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.png': 'image/png',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.gif': 'image/gif',
    '.webp': 'image/webp',
    '.ico': 'image/x-icon',
    '.woff': 'font/woff',
    '.woff2': 'font/woff2',
    '.txt': 'text/plain; charset=utf-8',
}


def _env_bool(name, default):
    raw = os.environ.get(name)
    if raw is None or raw == '':
        return default
    return raw.strip().lower() in ('1', 'true', 'yes', 'on')


def _env_port(name, default, *, low=1024, high=65535):
    """集成域端口：非法值直接报错，不静默回退到默认端口。

    静默回退会让运维以为服务在 8790 上，实际却绑到了别的端口 ——
    这类"配了没生效"的故障在排障时极其昂贵。
    """
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == '':
        return default
    try:
        value = int(str(raw).strip())
    except ValueError as err:
        raise ValueError(f'{name} 必须是 {low}~{high} 的整数，收到 {raw!r}') from err
    if not low <= value <= high:
        raise ValueError(f'{name} 必须是 {low}~{high} 的整数，收到 {value}')
    return value


def _env_list(name):
    """解析"逗号分隔"的环境变量：去空白、丢掉空项、统一小写。

    邮箱与主机名都是大小写不敏感的，界面上多打一个空格也不该让配置失效 ——
    这类"配了但没生效"的故障排起来非常费时间。
    """
    raw = os.environ.get(name) or ''
    return [item.strip().lower() for item in raw.split(',') if item.strip()]


class Config:
    """一次进程运行期间不变的配置。"""

    def __init__(self, *, host='127.0.0.1', port=8787, data_dir=None,
                 root_dir=None, admin_emails=None, cert_dir=None,
                 api_enabled=None, mcp_enabled=None, api_port=None, mcp_port=None):
        self.version = APP_VERSION

        # 构建标识：由镜像构建时注入（Dockerfile 的 ARG GTD_BUILD），源码直跑时为 'dev'。
        # 它是**零停机发布的产物核对依据**：蓝绿切换前必须确认新槽位回传的 build
        # 与本次构建的标识一致。只看端口通不通是不够的 —— 旧容器也会让端口是通的，
        # 于是"发布成功"之后线上跑的还是上一版，这类故障极难发现。
        self.build = (os.environ.get('GTD_BUILD') or 'dev').strip() or 'dev'

        self.root_dir = Path(root_dir or ROOT_DIR)
        self.data_dir = Path(data_dir or os.environ.get('GTD_DATA_DIR')
                             or self.root_dir / 'data')
        self.db_path = self.data_dir / 'gtd4teams.db'

        # 监听地址与端口。优先级：命令行 > 环境变量 > 默认值。
        # 除自有变量外还认标准的 PORT / HOST —— 托管平台（容器、PaaS、反向代理）
        # 就是靠注入这两个变量来指定"唯一对外端口"的，只认自己的变量名会导致
        # 部署上去后服务根本不监听平台期待的那个端口，表现为"发布成功但打不开"。
        #
        # 另一个坑：检测到 PORT 时默认绑 0.0.0.0。平台的反向代理从容器外部访问，
        # 只绑 127.0.0.1 连不上；反之在纯本地场景（无 PORT）仍绑回环，不开局域网口子。
        env_port = os.environ.get('PORT') or os.environ.get('GTD_PORT')
        self.port = int(port or env_port or 8787)
        self.host = (host or os.environ.get('HOST') or os.environ.get('GTD_HOST')
                     or ('0.0.0.0' if env_port else '127.0.0.1'))

        # 自助注册已**永久下线**（CR-2026-001）：账号只能由系统管理员 / 团队管理员创建。
        # 端点已从 ROUTES 移除（`POST /api/auth/register` → 404），
        # 因此 allow_signup / signup_code 这两个配置项不再有任何行为。
        #
        # 但这里**保留对旧环境变量的检测**（不保留开关本身）：
        # 老 .env 里的 `GTD_ALLOW_SIGNUP=1` 如果被无声忽略，运维会长期以为
        # "注册还开着，只是没人用" —— 那是最难发现的一类配置失效。
        # 检测结果只用于在启动横幅 / --check 输出里打一行提醒，不参与任何判定。
        self.ignored_signup_env = tuple(
            name for name in ('GTD_ALLOW_SIGNUP', 'GTD_SIGNUP_CODE')
            if (os.environ.get(name) or '').strip())

        # 反向代理信任开关（默认关，这一点很重要）。
        # X-Forwarded-For / X-Forwarded-Proto 是**客户端可以随意伪造**的请求头。
        # 直连公网时无条件采信 XFF，攻击者每次请求换一个假 IP 就能把登录限流键换掉，
        # 等于限流形同虚设。只有服务确实跑在可信反向代理之后、
        # 且自身端口不对外暴露时，才把它置 1。
        self.trust_proxy = _env_bool('GTD_TRUST_PROXY', False)

        # ------------------------------------------------------------------
        # 实例管理员白名单（默认空 = **没有任何账号**可以经 HTTP 修改 AI 配置）
        # ------------------------------------------------------------------
        # 为什么需要它：`POST /api/llm/config` 能改 baseUrl，而 baseUrl 决定
        # "服务端把真实的厂商密钥发到哪台主机"。如果任何注册用户都能改，
        # 那么任何注册用户都能把密钥转发给自己（SSRF + 凭据外带）。
        #
        # 为什么用环境变量而不是库里的角色列：管理员本来就不是业务概念
        # （团队里的 owner/admin 是团队级的，管不到实例级配置），
        # 由部署方在 .env 里指定最省事，也不必引入新表、新列与迁移。
        #
        # 空集合时的语义是"谁都不能改"，不是"谁都能改" —— 安全默认应当关闭。
        if admin_emails is None:
            admin_emails = _env_list('GTD_ADMIN_EMAILS')
        self.admin_emails = frozenset(admin_emails)

        # 可选的收紧口：非空时，AI 服务地址的 host 只能从这里挑。
        # 不设置时只挡"内网/回环/元数据"这一类（见 httpd._validate_base_url）。
        self.llm_allowed_hosts = frozenset(_env_list('GTD_LLM_ALLOWED_HOSTS'))

        # LLM：默认指向 OpenAI 兼容端点，但**不预置任何密钥**。
        # 没配密钥时 AI 相关接口返回明确的 LLM_NOT_CONFIGURED，而不是静默失败。
        self.llm_base_url = (os.environ.get('GTD_LLM_BASE_URL')
                             or 'https://api.openai.com/v1').rstrip('/')
        self.llm_api_key = os.environ.get('GTD_LLM_API_KEY') or ''
        self.llm_model = os.environ.get('GTD_LLM_MODEL') or 'gpt-4o-mini'
        self.llm_timeout = int(os.environ.get('GTD_LLM_TIMEOUT') or 120)

        # ------------------------------------------------------------------
        # TLS 证书目录（证书管理接口的落盘位置）
        # ------------------------------------------------------------------
        # 默认 /certs 是**容器内的挂载点**：运维把宿主的 deploy/run/certs 挂进来，
        # Caddy 从同一个目录读 tls.crt / tls.key。
        #
        # 为什么不做成"不存在就 mkdir"：挂载缺失时在容器只读层里建目录会把部署错误
        # 掩盖掉 —— 证书写进去了、Caddy 看不到、容器重启即丢，
        # 排障方向会被彻底带偏。所以目录缺失时 POST 直接 500 并说明是部署问题。
        #
        # 可覆盖（GTD_CERT_DIR / 构造参数）是为了让测试用临时目录，
        # 绝不能在测试里真写 /certs（会污染线上挂载，CI 上也没有这个目录）。
        self.cert_dir = Path(cert_dir or os.environ.get('GTD_CERT_DIR') or '/certs')

        # 请求体上限：截图 OCR 会把图片以 data URL 内联提交，故不能太小
        self.max_body_bytes = int(os.environ.get('GTD_MAX_BODY_BYTES') or 8 * 1024 * 1024)

        # ------------------------------------------------------------------
        # 集成域（本地 REST API + 内置 MCP）—— **默认关闭**
        # ------------------------------------------------------------------
        # 为什么默认关：这两个监听器只在 127.0.0.1 上，但"能在本机发请求"
        # 不等于"应该一直开着"。默认关闭且关闭时**不创建 socket**，
        # 是 TDS §2.6 不变式 3 的硬要求（TC-INT-12 会验证端口无监听）。
        #
        # 不提供"从环境变量读令牌"的入口 —— 令牌只经浏览器面签发，
        # 否则会出现在 docker inspect / 进程环境里（§16.3.1）。
        self.api_enabled = _env_bool('GTD_API_ENABLED', False) \
            if api_enabled is None else bool(api_enabled)
        self.mcp_enabled = _env_bool('GTD_MCP_ENABLED', False) \
            if mcp_enabled is None else bool(mcp_enabled)
        self.api_port = _env_port('GTD_API_PORT', 8790) \
            if api_port is None else int(api_port)
        self.mcp_port = _env_port('GTD_MCP_PORT', 8722) \
            if mcp_port is None else int(mcp_port)
        for name, value in (('GTD_API_PORT', self.api_port),
                            ('GTD_MCP_PORT', self.mcp_port)):
            if not 1024 <= value <= 65535:
                raise ValueError(f'{name} 必须是 1024~65535 的整数，收到 {value}')

        # ------------------------------------------------------------------
        # 结构化访问日志（JSONL）
        # ------------------------------------------------------------------
        # 落在数据目录里而不是 /var/log：本项目是"随处可运行的单机应用"，
        # 数据目录已经是唯一需要备份/挂载的位置，日志跟着它走不需要额外配置。
        # 目录**不在构造时创建**（构造器可能在只读环境/CLI 子命令里跑）：
        # 创建与降级都在 applog.configure() 里处理，日志写不了也不能让服务起不来。
        self.log_dir = self.data_dir / 'logs'
        self.log_file = self.log_dir / 'app.jsonl'

    # -- 运行期可变的 LLM 配置（从 settings 表覆盖） -------------------------
    def apply_llm_settings(self, settings):
        """用库里的设置覆盖 LLM 三项（只覆盖非空值，环境变量仍有兜底作用）。"""
        base = (settings or {}).get('llm_base_url')
        key = (settings or {}).get('llm_api_key')
        model = (settings or {}).get('llm_model')
        if base:
            self.llm_base_url = str(base).rstrip('/')
        if key:
            self.llm_api_key = str(key)
        if model:
            self.llm_model = str(model)

    @property
    def llm_configured(self):
        return bool(self.llm_api_key)

    def is_admin_email(self, email) -> bool:
        """当前账号是否是实例管理员（白名单为空时恒为 False）。"""
        if not email:
            return False
        return str(email).strip().lower() in self.admin_emails

    def describe(self):
        return {
            'version': self.version,
            'build': self.build,
            'host': self.host,
            'port': self.port,
            'dataDir': str(self.data_dir),
            'dbPath': str(self.db_path),
            'trustProxy': self.trust_proxy,
            'llmConfigured': self.llm_configured,
            'llmModel': self.llm_model,
            'llmBaseUrl': self.llm_base_url,
            # 只报数量，不报邮箱：--check 的输出常被贴进工单或聊天窗口。
            # 这个数字是"我在 .env 里配的管理员到底生效了没有"的唯一自检依据。
            'adminEmailCount': len(self.admin_emails),
            'llmAllowedHosts': sorted(self.llm_allowed_hosts),
            # 证书目录只报路径（这是排障时最需要确认的一条：挂载到底有没有生效）
            'certDir': str(self.cert_dir),
            # 访问日志落盘位置：`--check` 的输出要能回答"日志到底写去哪了"，
            # 这也是"监控为什么没数据"的第一现场。
            'logFile': str(self.log_file),
            # 已下线的注册开关：老 .env 里若还留着，这里如实报出来提醒清理。
            'ignoredSignupEnv': list(self.ignored_signup_env),
            # 集成域：只报开关与端口，不报任何令牌信息。
            'apiEnabled': self.api_enabled, 'apiPort': self.api_port,
            'mcpEnabled': self.mcp_enabled, 'mcpPort': self.mcp_port,
        }
