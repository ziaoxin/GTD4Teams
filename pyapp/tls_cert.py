"""TLS 证书的解析、校验与落盘（**零第三方依赖**）。

为什么单独一个模块
------------------
证书管理有三件互相独立的事：**解析**（拿 subject/SAN/有效期/指纹）、
**校验**（PEM 格式、私钥是否配对、是否过期）、**落盘**（原子替换 + 权限）。
把它们放在 httpd 里会变成 100 行手写逻辑混在路由代码中间，
而这段逻辑里只要有一处漏了 try/except，表现就是"上传合法证书却 500"。

敏感数据纪律（本模块唯一的红线）
--------------------------------
私钥是本项目里敏感度最高的数据之一：
  · **绝不进 settings**（元数据只存 subject/issuer/SAN/有效期/指纹/上传人）；
  · **绝不进日志**（本模块没有任何 print，也不把异常原文拼进对外文案）；
  · **绝不出现在任何响应里**（对外只有 GET 的那几个字段）；
  · 只在"写 /certs/tls.key"的那一刻存在于内存与临时文件里，
    临时文件建在系统临时目录（**不是 /certs**，避免污染挂载目录），用完立即删除。
  所以：任何把 `key_pem` 或 `err` 文本拼进 message 的改动都是缺陷。

零依赖怎么做到
--------------
· 配对校验：`ssl.SSLContext.load_cert_chain(certfile, keyfile)` —— 标准库唯一可靠的做法。
  自己比 RSA 模数要手写 ASN.1，那是几百行且必然出错的路。
· 证书详情：`ssl._ssl._test_decode_cert(path)`。它是 CPython 内部函数，
  零依赖下没有更好的选择 —— 因此**必须**用 try/except 包住并降级：
  解析不到就只给指纹，其余字段 null，绝不因为"读不出 CN"让整个上传失败。
"""

import base64
import binascii
import hashlib
import json
import os
import re
import ssl
import tempfile
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from . import store

CERT_FILE = 'tls.crt'
KEY_FILE = 'tls.key'
PENDING_FLAG = '.pending-apply'
META_KEY = 'tls_cert_meta'

CERT_MODE = 0o644          # 证书是公开信息，Caddy 进程要能读
KEY_MODE = 0o600           # 私钥只能属主可读

# PEM 块匹配：证书用固定头；私钥兼容 PKCS#8 / PKCS#1 / EC 三种头
_PEM_CERT_RE = re.compile(
    r'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', re.S)
_PEM_KEY_RE = re.compile(
    r'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z]+ )?PRIVATE KEY-----', re.S)
_PEM_B64_RE = re.compile(r'^[A-Za-z0-9+/=\s]+$')

_SHORT_NAMES = {
    'commonName': 'CN',
    'countryName': 'C',
    'stateOrProvinceName': 'ST',
    'localityName': 'L',
    'organizationName': 'O',
    'organizationalUnitName': 'OU',
    'emailAddress': 'emailAddress',
}


class CertError(Exception):
    """带机读错误码的证书异常，由 HTTP 层翻译成响应。

    `message` 是**可展示文案**：绝不拼接 SQLite/ssl 的异常原文 ——
    那里面可能带出路径甚至密钥内容。
    """

    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# --------------------------------------------------------------------------
# PEM 解析
# --------------------------------------------------------------------------

def _as_text(value) -> str:
    if isinstance(value, bytes):
        try:
            return value.decode('ascii')
        except UnicodeDecodeError:
            raise CertError('CERT_INVALID_PEM', '证书内容必须是 ASCII 文本（PEM 格式）')
    return str(value or '')


def _cert_der(cert_pem: str) -> bytes:
    """把 PEM 证书转成 DER。非法 PEM 抛 CERT_INVALID_PEM（不吞）。"""
    text = _as_text(cert_pem)
    if '-----BEGIN CERTIFICATE-----' not in text:
        raise CertError('CERT_INVALID_PEM',
                        '证书不是合法的 PEM：缺少 -----BEGIN CERTIFICATE----- 块')
    try:
        return ssl.PEM_cert_to_DER_cert(text)
    except (ValueError, TypeError):
        raise CertError('CERT_INVALID_PEM',
                        '证书不是合法的 PEM：内容无法被解析（请检查是否被截断或粘贴错）')


def _check_key_pem(key_pem: str) -> str:
    """只做"格式像不像私钥"的检查（配对校验在 _load_chain 里）。"""
    text = _as_text(key_pem)
    match = _PEM_KEY_RE.search(text)
    if not match:
        raise CertError('CERT_INVALID_PEM',
                        '私钥不是合法的 PEM：缺少 PRIVATE KEY 块')
    body = ''.join(line for line in match.group(0).splitlines()
                   if line and not line.startswith('-----') and ':' not in line)
    if not body or not _PEM_B64_RE.match(body):
        raise CertError('CERT_INVALID_PEM', '私钥不是合法的 PEM：正文不是 base64')
    try:
        base64.b64decode(body, validate=True)
    except (binascii.Error, ValueError):
        raise CertError('CERT_INVALID_PEM', '私钥不是合法的 PEM：正文 base64 解码失败')
    return text


def _is_encrypted_key(key_pem: str) -> bool:
    """带口令的私钥。

    两种形态都要认：
      · PKCS#8 加密：`-----BEGIN ENCRYPTED PRIVATE KEY-----`
      · 传统 OpenSSL 加密：头部下有 `Proc-Type: 4,ENCRYPTED`
    必须与"坏格式"区分开 —— 用户拿到的是完全不同的处置建议。
    """
    text = key_pem or ''
    return ('ENCRYPTED PRIVATE KEY' in text) or ('Proc-Type: 4,ENCRYPTED' in text)


def normalize_pem(text: str) -> str:
    """统一成"末尾恰好一个换行"的形态（Caddy / OpenSSL 都接受这种）。"""
    body = _as_text(text).strip()
    return body + '\n' if body else ''


# --------------------------------------------------------------------------
# 证书详情（解析失败只降级，不失败）
# --------------------------------------------------------------------------

def _format_name(name) -> str:
    """`((('commonName','localhost'),),)` → `CN=localhost`。"""
    parts = []
    for rdn in name or ():
        for key, value in rdn:
            parts.append(f'{_SHORT_NAMES.get(key, key)}={value}')
    return ', '.join(parts) or None


def _format_asn1_time(text):
    """`Nov 15 05:00:00 2026 GMT` → `2026-11-15T05:00:00Z`。"""
    if not text:
        return None
    try:
        moment = parsedate_to_datetime(str(text))
    except (TypeError, ValueError):
        return None
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _decode_via_tempfile(cert_pem: str):
    """用 `_test_decode_cert` 读证书字段。失败返回 None（**降级，不抛**）。"""
    path = None
    try:
        handle, path = tempfile.mkstemp(suffix='.crt')
        with os.fdopen(handle, 'w', encoding='ascii') as stream:
            stream.write(cert_pem)
        return ssl._ssl._test_decode_cert(path)      # noqa: SLF001 - 零依赖唯一选择
    except Exception:                                # noqa: BLE001 - 内部 API，任何异常都降级
        return None
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass


def fingerprint_sha256(cert_pem: str) -> str:
    """证书 DER 的 SHA-256，形如 `AB:CD:...`（全大写，OpenSSL 习惯）。"""
    digest = hashlib.sha256(_cert_der(cert_pem)).hexdigest().upper()
    return ':'.join(digest[index:index + 2] for index in range(0, len(digest), 2))


def describe(cert_pem: str) -> dict:
    """证书详情。**任何字段解析不到都给 null，绝不因此让调用方失败。**

    只有指纹是强依赖（它决定"是不是同一张证书"），所以它单独 try：
    连指纹都算不出来说明证书本身不可用，那时抛 CERT_INVALID_PEM 是对的
    （调用方在格式校验阶段就该拦下）。
    """
    text = _as_text(cert_pem)
    result = {
        'subject': None,
        'issuer': None,
        'sans': [],
        'notBefore': None,
        'notAfter': None,
        'daysRemaining': None,
        'selfSigned': None,
    }
    info = _decode_via_tempfile(text)
    if info:
        result['subject'] = _format_name(info.get('subject'))
        result['issuer'] = _format_name(info.get('issuer'))
        result['sans'] = [f'{kind}:{value}' for kind, value in info.get('subjectAltName', ())]
        result['notBefore'] = _format_asn1_time(info.get('notBefore'))
        result['notAfter'] = _format_asn1_time(info.get('notAfter'))
        if result['notAfter']:
            try:
                expiry = datetime.strptime(result['notAfter'], '%Y-%m-%dT%H:%M:%SZ').replace(
                    tzinfo=timezone.utc)
                result['daysRemaining'] = int(
                    (expiry - datetime.now(timezone.utc)).total_seconds() // 86400)
            except (TypeError, ValueError):
                result['daysRemaining'] = None
        if result['subject'] and result['issuer']:
            result['selfSigned'] = result['subject'] == result['issuer']
    result['fingerprintSha256'] = fingerprint_sha256(text)
    return result


# --------------------------------------------------------------------------
# 校验（顺序即安全设计：格式 → 加密 → 配对 → 有效期）
# --------------------------------------------------------------------------

def _load_chain(cert_pem: str, key_pem: str) -> None:
    """用标准库验证"证书与私钥是否配对"。不配对抛 CERT_KEY_MISMATCH。"""
    cert_path = key_path = None
    try:
        cert_fd, cert_path = tempfile.mkstemp(suffix='.crt')
        with os.fdopen(cert_fd, 'w', encoding='ascii') as stream:
            stream.write(cert_pem)
        key_fd, key_path = tempfile.mkstemp(suffix='.key')
        with os.fdopen(key_fd, 'w', encoding='ascii') as stream:
            stream.write(key_pem)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_path, key_path)
    except (ssl.SSLError, OSError, ValueError):
        # 不把异常原文拼进文案：它虽不含密钥，但会带出临时文件路径，
        # 而路径是给攻击者的免费情报（部署目录结构）。
        raise CertError('CERT_KEY_MISMATCH', '证书与私钥不匹配（两者不是同一对）')
    finally:
        for path in (cert_path, key_path):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def validate_pair(cert_pem, key_pem, *, now=None) -> dict:
    """完整校验并返回证书详情。失败抛 CertError（四类错误码各不同）。"""
    cert_text = _as_text(cert_pem)
    key_text = _as_text(key_pem)

    # ① 格式（证书与私钥都要像 PEM）
    _cert_der(cert_text)
    key_text = _check_key_pem(key_text)

    # ② 私钥带口令 —— 必须在"配对"之前判：加密私钥 load_cert_chain 也会抛，
    #    但那是 SSLError，会被误判成"不配对"，用户会去换密钥而不是去掉口令。
    if _is_encrypted_key(key_text):
        raise CertError('CERT_KEY_ENCRYPTED',
                        '私钥带有口令保护，无法使用：请上传未加密的私钥（去掉 passphrase）')

    # ③ 配对
    _load_chain(cert_text, key_text)

    # ④ 有效期
    details = describe(cert_text)
    expiry = details.get('notAfter')
    if expiry:
        try:
            expire_at = datetime.strptime(expiry, '%Y-%m-%dT%H:%M:%SZ').replace(
                tzinfo=timezone.utc)
        except (TypeError, ValueError):
            expire_at = None
        if expire_at is not None and expire_at <= (now or datetime.now(timezone.utc)):
            raise CertError('CERT_EXPIRED',
                            f'证书已过期（{expiry}），请上传新证书')
    return details


# --------------------------------------------------------------------------
# 元数据（settings.tls_cert_meta；**只存展示字段，不含私钥/证书全文**）
# --------------------------------------------------------------------------

def read_meta(conn):
    raw = store.get_setting(conn, META_KEY)
    if not raw:
        return None
    try:
        meta = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return meta if isinstance(meta, dict) else None


def _write_meta(conn, details, uploaded_by) -> None:
    meta = {
        'subject': details.get('subject'),
        'issuer': details.get('issuer'),
        'sans': details.get('sans') or [],
        'notBefore': details.get('notBefore'),
        'notAfter': details.get('notAfter'),
        'fingerprintSha256': details.get('fingerprintSha256'),
        'selfSigned': details.get('selfSigned'),
        'uploadedAt': _now_iso(),
        'uploadedBy': (uploaded_by or None),
    }
    # 注意：这里写入的是 JSON 文本，里面**没有**私钥、也没有证书全文。
    store.set_setting(conn, META_KEY, json.dumps(meta, ensure_ascii=False))


def _now_iso() -> str:
    from . import db
    return db.now_iso()


# --------------------------------------------------------------------------
# 落盘 / 读取 / 删除
# --------------------------------------------------------------------------

def _require_dir(cert_dir) -> Path:
    directory = Path(cert_dir)
    if not directory.is_dir():
        raise CertError(
            'CERT_DIR_MISSING',
            f'证书目录 {directory} 不存在：这是**部署问题**，不是输入问题。'
            '请确认容器已把宿主 deploy/run/certs 挂载到 /certs 且可写'
            '（docker-compose 的 certs 卷 / GTD_CERT_DIR 配置），'
            '然后重启服务再试。',
            500)
    return directory


def _unlink_quietly(path) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _stage_file(directory: Path, content: str, mode: int) -> str:
    """把内容写进**同目录**的隐藏临时文件并设好权限，返回临时路径。

    同目录而不是系统临时目录：`os.replace` 只有在同一文件系统内才是原子的，
    跨设备会退化成"复制 + 删除"，那就又回到"能被读到半截"的世界了。
    文件名以 `.tmp-` 开头（点开头），watchdog 只认 tls.crt / tls.key，
    不会被误读；万一进程被杀留下残骸，也是一眼能认出来的垃圾文件。
    """
    text = normalize_pem(content)
    handle, temp_path = tempfile.mkstemp(prefix='.tmp-', dir=str(directory))
    try:
        with os.fdopen(handle, 'wb') as stream:
            stream.write(text.encode('ascii'))
        try:
            os.chmod(temp_path, mode)
        except OSError:
            pass                      # Windows 上 chmod 语义有限，不因此失败
    except BaseException:
        _unlink_quietly(temp_path)
        raise
    return temp_path


def _touch_pending(directory: Path) -> None:
    """创建"待应用"标记：外部的 watchdog 据此重新生成 Caddyfile 并 reload。

    空文件即可 —— 内容没有语义，**存在性**就是信号（幂等、不会被写坏）。
    这个标记由外部删除（reload 成功后），我们从不主动删它：
    删了就等于声称"已经生效"，而我们无法知道 Caddy 到底有没有加载。
    """
    flag = directory / PENDING_FLAG
    try:
        with open(flag, 'w', encoding='ascii'):
            pass
    except OSError:
        raise CertError('CERT_DIR_MISSING',
                        f'证书目录 {directory} 不可写：这是**部署问题**，'
                        '请检查挂载权限后重启服务再试。', 500)


def pending_apply(cert_dir) -> bool:
    return (Path(cert_dir) / PENDING_FLAG).is_file()


#: 已经警告过的"元数据漂移"指纹对（每个组合只打一行，避免前端轮询把日志刷爆）
_DRIFT_WARNED = set()


def _empty_details() -> dict:
    return {'subject': None, 'issuer': None, 'sans': [], 'notBefore': None,
            'notAfter': None, 'daysRemaining': None, 'fingerprintSha256': None,
            'selfSigned': None}


def _short_fingerprint(value) -> str:
    """指纹前 16 个字符（≈5 字节），够运维肉眼比对，又不至于刷屏。"""
    return (value or 'unknown')[:16]


def _warn_meta_drift(meta_fingerprint, file_fingerprint) -> None:
    """元数据与文件指纹不一致时留一行警告（同一种漂移只报一次）。

    为什么不让运维脚本去清 `settings.tls_cert_meta`：
      · 脚本在**宿主**上跑，没有数据库访问能力（数据在容器内的 /data），
        给它开一条改库的口子不值得；
      · 更根本的是：**"文件被谁改"无法枚举**。今天能想到 `gen-selfsigned.sh`，
        明天就有人 `cp` 一张证书进去、或从另一个槽位写入。
        只有"以文件为准 + 指纹比对"能覆盖所有路径，而不是逐个堵。
    """
    key = (_short_fingerprint(meta_fingerprint), _short_fingerprint(file_fingerprint))
    if key in _DRIFT_WARNED:
        return
    if len(_DRIFT_WARNED) > 32:          # 防御：别让某个反复换证书的脚本把集合撑大
        _DRIFT_WARNED.clear()
    _DRIFT_WARNED.add(key)
    print('  ! 证书元数据与实际文件不一致：settings.tls_cert_meta 记录的是 '
          f'{key[0]}…，/certs/tls.crt 实际是 {key[1]}…'
          '（已按文件为准、source 标为 selfsigned）。'
          '**若最近用脚本/自签生成过证书（gen-selfsigned.sh 或手工替换），这是正常现象、可忽略**；'
          '否则说明证书在 API 之外被改动过。', flush=True)


def status(conn, cert_dir) -> dict:
    """GET 的响应体。**文件是唯一事实来源，元数据只是附加信息。**

    为什么不能"有元数据就用元数据"（曾经这么写，线上踩过）：
    实测现场 —— API 上传证书 A（30 天）→ 运维 `gen-selfsigned.sh --force`
    换成证书 B（825 天，不经 API、只改文件）→ `settings` 仍记着 A，
    于是 GET 报"A，30 天后到期"，而 HTTPS 实际提供的是 3 年后才过期的 B。
    用户被提示去续期一张**已经不在使用**的证书。假信息比没有信息更危险。

    所以取值顺序固定为：
      1. 始终现场解析 `/certs/tls.crt`（不存在才 `configured: false`）；
      2. `settings.tls_cert_meta` 只补 `uploadedAt` / `uploadedBy`；
      3. **仅当元数据指纹 == 文件指纹**才采信它 —— 否则说明文件被 API 之外
         的东西换过，`source` 报 `selfsigned`、上传人/时间置 null。
    """
    directory = Path(cert_dir)
    cert_path = directory / CERT_FILE
    pending = pending_apply(directory)

    if not cert_path.is_file():
        return {'configured': False, 'source': 'none', 'pendingApply': pending}

    # ── ① 文件：现场解析（解析失败也降级，不让页面打不开） ──────────────
    try:
        cert_pem = cert_path.read_text('ascii')
    except (OSError, UnicodeDecodeError):
        cert_pem = ''
    details = _empty_details()
    if cert_pem:
        try:
            details = describe(cert_pem)
        except CertError:
            details = _empty_details()      # 连 DER 都转不出来：只报"有证书但读不出"

    # ── ② 元数据：只用来补上传人/时间，且必须指纹一致 ────────────────────
    meta = read_meta(conn)
    file_fingerprint = details.get('fingerprintSha256')
    meta_matches = bool(meta) and bool(file_fingerprint) \
        and meta.get('fingerprintSha256') == file_fingerprint

    payload = {'configured': True,
               'source': 'uploaded' if meta_matches else 'selfsigned'}
    payload.update(details)

    if meta_matches:
        payload['uploadedAt'] = meta.get('uploadedAt')
        payload['uploadedBy'] = meta.get('uploadedBy')
    else:
        payload['uploadedAt'] = None
        payload['uploadedBy'] = None
        if meta:
            # 有记录但对不上 → 文件被其它途径换过（脚本/手工/另一槽位）。
            # 留一行警告便于排查"谁动了证书"，但**不修改、不删除**这条记录。
            _warn_meta_drift(meta.get('fingerprintSha256'), file_fingerprint)

    payload['pendingApply'] = pending
    return payload


def _days_remaining(not_after):
    if not not_after:
        return None
    try:
        expiry = datetime.strptime(str(not_after), '%Y-%m-%dT%H:%M:%SZ').replace(
            tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return int((expiry - datetime.now(timezone.utc)).total_seconds() // 86400)


def save(conn, cert_dir, cert_pem, key_pem, *, uploaded_by=None) -> dict:
    """校验 → 原子写 cert/key → 写元数据 → 打"待应用"标记 → 返回状态。

    顺序与失败语义：
      1. 目录不存在直接 500（不 mkdir，掩盖部署错误是最坏的结果）；
      2. 校验全部在**写盘之前**完成 —— 半份合法证书比没有证书更难排查；
      3. **两个临时文件都写好之后才开始替换**：把"证书已是新的、私钥还是旧的"
         这个窗口压到两次 rename 之间（微秒级），而不是跨两次"写 + 替换"。
         外部 watchdog 只在 `.pending-apply` 出现后才读文件，而标记是最后一步，
         所以它永远读不到半对证书；
      4. 文件写完再写元数据：元数据写失败时文件仍可用（GET 会归为 selfsigned），
         反过来（有元数据没文件）会让页面显示"已配置"但 Caddy 读不到；
      5. 标记最后创建。**从不在这里删除标记** —— 删了就等于声称"已生效"，
         而我们无法知道 Caddy 有没有真的加载（那是外部观察者的职责）。
    """
    directory = _require_dir(cert_dir)
    details = validate_pair(cert_pem, key_pem)

    staged = []
    try:
        staged.append((_stage_file(directory, cert_pem, CERT_MODE), CERT_FILE))
        staged.append((_stage_file(directory, key_pem, KEY_MODE), KEY_FILE))
        for temp_path, name in staged:
            os.replace(temp_path, str(directory / name))
        staged = []                  # 全部替换完成；临时文件已不存在
    finally:
        for temp_path, _name in staged:
            _unlink_quietly(temp_path)

    _write_meta(conn, details, uploaded_by)
    _touch_pending(directory)
    return status(conn, directory)


def delete(conn, cert_dir) -> dict:
    """删除证书与元数据，并打"待应用"标记（Caddy 要回到无证书状态）。

    文件不存在不报错（幂等）：重复删除、部分文件缺失都是正常场景 ——
    用户点两次删除不该看到错误。
    """
    directory = _require_dir(cert_dir)
    for name in (CERT_FILE, KEY_FILE):
        try:
            os.unlink(str(directory / name))
        except FileNotFoundError:
            pass
        except OSError:
            raise CertError('CERT_DIR_MISSING',
                            f'证书文件无法删除（{directory}）：这是部署问题，'
                            '请检查挂载权限。', 500)
    store.delete_setting(conn, META_KEY)
    _touch_pending(directory)
    return {'configured': False, 'source': 'none', 'pendingApply': pending_apply(directory)}
