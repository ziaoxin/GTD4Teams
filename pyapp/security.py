"""口令散列、会话令牌与登录限速。

与云版本的关键差异
------------------
云版本把口令交给平台身份源（服务端 bcrypt），本项目改为自持：

* **口令散列**：PBKDF2-HMAC-SHA256，迭代次数按机器算力可调，每个口令独立随机盐。
  存库格式 `pbkdf2_sha256$<迭代数>$<盐base64>$<散列base64>` —— 把参数写进存储串，
  以后提高迭代次数时旧口令仍能校验通过（登录成功时顺手升级）。
  选 PBKDF2 而不是 bcrypt/argon2 的唯一理由是**零依赖**：标准库 `hashlib.pbkdf2_hmac`
  在 FIPS 语境下也是被接受的 KDF；本项目不引入第三方编译依赖是硬约束。
* **会话**：随机 32 字节令牌放在 HttpOnly Cookie 里，库里只存它的 SHA-256。
  即使数据库文件被拷走，也无法用其中的内容冒用登录态。
* **限速**：按 (邮箱, 来源 IP) 计数，防止在线暴力破解。

本模块不碰 HTTP，只做纯函数与一个小状态机，便于单测。
"""

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time

PBKDF2_ITERATIONS = 240_000
SESSION_TTL_SECONDS = 30 * 24 * 3600          # 30 天不活跃即失效
SESSION_COOKIE = 'gtd_session'
MIN_PASSWORD_LENGTH = 8

# 邮箱正则只在这里定义一次：HTTP 建号接口与 CLI（--create-admin/--grant-admin/
# --set-password）必须用同一套规则，否则两处会漂移成"CLI 能建、接口不能建"。
EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]{2,}$')

# 随机初始口令的字符集：A-Z 去掉 I/O + a-z 去掉 l + 2-9（共 57 个）。
# 去掉 0/O、1/l/I 三对视觉混淆字符 —— 这类口令是要靠人手抄/口述传递的；
# 不含空格、引号、反斜杠、`$`，避免 shell 粘贴与存储串（PBKDF2 用 `$` 分隔）产生歧义。
TEMP_PASSWORD_UPPER = 'ABCDEFGHJKLMNPQRSTUVWXYZ'
TEMP_PASSWORD_LOWER = 'abcdefghijkmnopqrstuvwxyz'
TEMP_PASSWORD_DIGITS = '23456789'
TEMP_PASSWORD_ALPHABET = TEMP_PASSWORD_UPPER + TEMP_PASSWORD_LOWER + TEMP_PASSWORD_DIGITS
TEMP_PASSWORD_LENGTH = 16

# --------------------------------------------------------------------------
# 口令
# --------------------------------------------------------------------------


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
    return 'pbkdf2_sha256${}${}${}'.format(
        iterations,
        base64.b64encode(salt).decode('ascii'),
        base64.b64encode(digest).decode('ascii'),
    )


def verify_password(password: str, stored: str) -> bool:
    """校验口令。任何格式异常一律判失败，不抛异常（避免把存储损坏变成 500）。"""
    try:
        algo, iterations, salt_b64, digest_b64 = str(stored).split('$')
        if algo != 'pbkdf2_sha256':
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def needs_rehash(stored: str) -> bool:
    """存储串的迭代次数低于当前配置时，登录成功后可顺手升级。"""
    try:
        algo, iterations, _salt, _digest = str(stored).split('$')
    except ValueError:
        return True
    return algo != 'pbkdf2_sha256' or int(iterations) < PBKDF2_ITERATIONS


def validate_password(password: str) -> str:
    """返回错误文案；通过则返回空串。规则与前端表单保持一致（至少 8 位）。"""
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        return f'密码至少需要 {MIN_PASSWORD_LENGTH} 位'
    if len(password) > 200:
        return '密码过长'
    return ''


def validate_email(email) -> str:
    """返回错误文案；通过则返回空串。CLI 与 HTTP 共用同一规则。"""
    if not isinstance(email, str) or not email.strip():
        return '请输入有效的邮箱地址'
    text = email.strip()
    if len(text) > 254 or not EMAIL_RE.match(text):
        return '请输入有效的邮箱地址'
    return ''


def generate_temp_password(length: int = TEMP_PASSWORD_LENGTH) -> str:
    """生成一次性初始口令（**只在建号响应 / CLI stdout 出现一次**）。

    必须用 `secrets` 而不是 `random`：后者是 Mersenne Twister，
    不是密码学安全随机源，绝不可用于生成凭据。
    16 位 × log2(57) ≈ 93 bit 熵，远高于 8 位长度下限，又短到能一次复制。

    前三位分别固定取大写/小写/数字各一，其余取全集，最后整体打乱：
    保证任何"必须含大小写+数字"的密码策略都能过，也避免"全是字母"这种
    看起来不像口令的极端。
    """
    length = max(int(length or 0), 3)
    chars = [
        secrets.choice(TEMP_PASSWORD_UPPER),
        secrets.choice(TEMP_PASSWORD_LOWER),
        secrets.choice(TEMP_PASSWORD_DIGITS),
    ]
    chars.extend(secrets.choice(TEMP_PASSWORD_ALPHABET) for _ in range(length - 3))
    secrets.SystemRandom().shuffle(chars)
    return ''.join(chars)


# --------------------------------------------------------------------------
# 会话令牌
# --------------------------------------------------------------------------


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_fingerprint(token: str) -> str:
    """令牌的存储形态：SHA-256 十六进制。库被读走也无法反推令牌本身。"""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def new_user_id() -> str:
    """账号主键。TEXT 而非自增整数：云版本用的是 uuid 文本，
    而前端多处用 `owner_id === state.user.id` 做归属比较，保持文本形态语义一致。"""
    return 'u_' + secrets.token_hex(8)


def session_expiry(now=None):
    """返回 (创建时间, 过期时间)，均为 ISO8601 UTC 字符串。

    刻意不写 `-> tuple[str, str]`：那需要 Python 3.9+，
    而本项目的目标是"随便一台机器上的 python3 就能跑"，注解保持在 3.8 可用范围内。
    """
    from datetime import datetime, timezone
    now = now if now is not None else time.time()
    created = datetime.fromtimestamp(now, timezone.utc)
    expires = datetime.fromtimestamp(now + SESSION_TTL_SECONDS, timezone.utc)
    fmt = '%Y-%m-%dT%H:%M:%SZ'
    return created.strftime(fmt), expires.strftime(fmt)


# --------------------------------------------------------------------------
# 登录限速（进程内，够用且无需外部依赖）
# --------------------------------------------------------------------------

_LOCK = threading.Lock()
_ATTEMPTS = {}          # key -> [失败次数, 窗口起点]
WINDOW_SECONDS = 15 * 60
MAX_ATTEMPTS = 10


def register_failure(key: str) -> None:
    """记一次失败（或任意计数事件）。

    这个名字来自登录限速；`bump_counter` 是同一实现的通用别名，
    供日志上报这类"只计数、不区分成败"的场景使用，避免读代码时产生歧义。
    """
    with _LOCK:
        now = time.time()
        count, start = _ATTEMPTS.get(key, (0, now))
        if now - start > WINDOW_SECONDS:
            count, start = 0, now
        _ATTEMPTS[key] = (count + 1, start)


bump_counter = register_failure


def clear_failures(key: str) -> None:
    with _LOCK:
        _ATTEMPTS.pop(key, None)


def is_rate_limited(key: str) -> bool:
    with _LOCK:
        entry = _ATTEMPTS.get(key)
        if not entry:
            return False
        count, start = entry
        if time.time() - start > WINDOW_SECONDS:
            _ATTEMPTS.pop(key, None)
            return False
        return count >= MAX_ATTEMPTS


def reset_limits() -> None:
    """仅供测试使用：清空限速状态。"""
    with _LOCK:
        _ATTEMPTS.clear()
