"""集成域的 HTTP 面：Bearer 令牌、令牌管理端点与本地 REST API（/v1/*）。

对应 TDS §16.3 ~ §16.7、§16.11。几条贯穿全文件的纪律：

* **纯标准库**，进程内运行；只绑 127.0.0.1（绑定发生在 httpd.make_integration_servers）。
* **默认无令牌 = 401**；令牌只存 sha256 到 api_tokens，明文只在创建/轮换响应里出现一次，
  不进访问日志、不进 app_logs、不进备份（那张表不在 store.BACKUP_TABLES 里）。
* **权限只有一套**：令牌身份解析后统一 policy.load_context，逐行 can_select/can_insert/
  can_update/can_delete；本文件不写任何第二套权限谓词。
* **继承 Handler._dispatch**：request_id、每请求一条日志、写锁 6 次退避、body 消费、
  _abort_if_responded、_internal_error 全部沿用，绝不另写一套分发循环。
"""

import json
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from . import APP_VERSION, db, security, store
from .httpd import ApiError, Handler, _require_instance_admin
from .policy import can_select, load_context

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------

TOKEN_PREFIX = 'gtd_pat_'
VALID_SCOPES = ('read', 'write:edit', 'write:archive', 'write:delete', 'mcp')
DEFAULT_SCOPES = ('read',)
DEFAULT_TOKEN_DAYS = 90
MAX_TOKENS_PER_USER = 10
MAX_TOKEN_LABEL = 120
MAX_IDEM_KEY = 128
_MAX_LIMIT = 1000
_DEFAULT_LIMIT = 200

_ETAG_RE = re.compile(r'^W/"(\d+)-(\d+)"$')

# TC-INT-01 要求"没给令牌"与"给了错令牌"两种 401 的响应体**除 code 外完全一致**。
# 故 message 也必须共用同一句 —— 不可用"缺少 Bearer 令牌"这类更友好的文案区分。
AUTH_FAILURE_MESSAGE = '令牌无效或已撤销'


class IntegrationError(store.StoreError):
    """集成域的业务错误。继承 StoreError 是为了直接复用 _dispatch / _error 的翻译。"""


def _err(status, code, message):
    return IntegrationError(code, message, status)


def _param(query, name):
    values = query.get(name) if query else None
    return values[0] if values else None


def _int_param(query, name, default, *, low=0, high=_MAX_LIMIT):
    raw = _param(query, name)
    if raw is None or str(raw).strip() == '':
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        raise _err(400, 'BAD_QUERY', f'{name} 必须是整数')
    return max(low, min(value, high))


def _ctx(handler, conn):
    """从令牌身份装配权限上下文（与浏览器面走的是同一个 load_context）。"""
    return load_context(conn, handler._api_user['id'])


def _etag(row):
    return 'W/"{}-{}"'.format(row['id'], row.get('version') or 1)


def _audit_context(handler):
    """审计 detail 的公共部分。**只含 tokenId/label，绝不含令牌本体或请求头。**"""
    return {
        'tokenId': getattr(handler, '_token_id', None),
        'tokenLabel': getattr(handler, '_token_label', '') or '',
        'requestId': getattr(handler, '_request_id', '') or '',
        'scopesUsed': list(getattr(handler, '_api_scopes', ()) or ()),
        'scope': 'mcp' if getattr(handler, '_path', '') == '/mcp' else 'api',
    }


# --------------------------------------------------------------------------
# 令牌：生成 / 校验 / 生命周期
# --------------------------------------------------------------------------

def generate_token() -> str:
    """`gtd_pat_` 前缀让令牌可被 grep/secret scanner 识别。"""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def normalize_scopes(raw):
    if raw is None:
        return list(DEFAULT_SCOPES)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            raise _err(400, 'INVALID_SCOPE', 'scopes 必须是 JSON 数组')
    if not isinstance(raw, (list, tuple)):
        raise _err(400, 'INVALID_SCOPE', 'scopes 必须是数组')
    unknown = [item for item in raw if item not in VALID_SCOPES]
    if unknown:
        raise _err(400, 'INVALID_SCOPE', '未知的 scope：' + ', '.join(map(str, unknown)))
    seen, result = set(), []
    for item in raw:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _expiry_from_body(body):
    """默认 90 天；`noExpiry: true` 才显式给 NULL（永不过期）。"""
    if bool(body.get('noExpiry')):
        return None
    days = body.get('expiresInDays', DEFAULT_TOKEN_DAYS)
    if days is None or days == '':
        days = DEFAULT_TOKEN_DAYS
    try:
        days = int(days)
    except (TypeError, ValueError):
        raise _err(400, 'BAD_PAYLOAD', 'expiresInDays 必须是整数')
    if not 1 <= days <= 3650:
        raise _err(400, 'BAD_PAYLOAD', 'expiresInDays 必须在 1~3650 之间')
    when = datetime.now(timezone.utc) + timedelta(days=days)
    return when.strftime('%Y-%m-%dT%H:%M:%SZ')


def authenticate_token(handler, conn):
    """Bearer 鉴权。失败码分两档：没给 = UNAUTHORIZED；给错/过期/撤销 = INVALID_TOKEN。

    不解码令牌里的任何自描述信息（无 JWT）：权限全在库里，撤销才可能即时生效。
    每次请求都读库，不做任何缓存。Cookie 在这里**完全不被读取**。
    """
    header = handler.headers.get('Authorization') or ''
    if not header.lower().startswith('bearer '):
        raise _err(401, 'UNAUTHORIZED', AUTH_FAILURE_MESSAGE)
    token = header[7:].strip()
    if not token:
        raise _err(401, 'UNAUTHORIZED', AUTH_FAILURE_MESSAGE)

    row = conn.execute(
        'SELECT t.id AS token_id, t.label, t.scopes, t.expires_at, t.revoked_at, '
        '       t.last_used_at, u.id, u.email, u.name, '
        '       u.is_system_admin, u.must_change_password '
        'FROM api_tokens t JOIN users u ON u.id = t.user_id '
        'WHERE t.token_hash = ?',
        (security.token_fingerprint(token),),
    ).fetchone()
    # 三种失败同一个码：区分它们等于告诉攻击者"这个令牌曾经有效"。
    if row is None or row['revoked_at']:
        raise _err(401, 'INVALID_TOKEN', AUTH_FAILURE_MESSAGE)
    now = db.now_iso()
    if row['expires_at'] and str(row['expires_at']) <= now:
        raise _err(401, 'INVALID_TOKEN', AUTH_FAILURE_MESSAGE)

    user = {
        'id': row['id'], 'email': row['email'], 'name': row['name'] or '',
        'isSystemAdmin': bool(row['is_system_admin']),
        'mustChangePassword': bool(row['must_change_password']),
    }
    # 强制改密闸门不许被令牌旁路（§4.8 边界 3）。
    if user['mustChangePassword']:
        raise _err(403, 'PASSWORD_CHANGE_REQUIRED',
                   '初始密码尚未修改，请先设置新密码后再继续（当前操作已锁定）')

    # 身份落到 handler：访问日志在响应之后才写，那时局部变量已出作用域。
    handler._uid = user['id']
    handler._token_id = row['token_id']
    handler._token_label = row['label'] or ''

    # last_used_at 节流刷新（复用会话的 300s 常量）：每个请求都 UPDATE 会让
    # 读接口也抢写锁。走 bookkeeping_commit，不污染 commit_attempted。
    if db.seconds_since(row['last_used_at']) >= handler.SESSION_TOUCH_SECONDS:
        try:
            conn.execute('UPDATE api_tokens SET last_used_at = ? WHERE id = ?',
                         (now, row['token_id']))
            db.bookkeeping_commit(conn)
        except sqlite3.OperationalError:
            try:
                conn.rollback()
            except sqlite3.Error:
                pass

    try:
        scopes = json.loads(row['scopes'] or '[]')
    except (TypeError, ValueError):
        scopes = []
    if not isinstance(scopes, list):
        scopes = []
    return user, tuple(scopes)


def _token_row(conn, token_id):
    return conn.execute(
        'SELECT id, user_id, label, scopes, expires_at, revoked_at '
        'FROM api_tokens WHERE id = ?', (token_id,)
    ).fetchone()


def _active_token_count(conn, user_id, *, exclude_id=None):
    """只数"当前真的能用"的令牌：未撤销**且未过期**。

    TC-SEC-INT-25：撤销与过期都必须释放名额 —— 已过期的令牌早就不能用了，
    继续占着名额只会让新签凭空 409。轮换还要排除自身：满 10 个时，
    待替换的那把如果也计入，就会出现"轮换必然假超限"的死锁。
    """
    sql = ('SELECT COUNT(*) AS n FROM api_tokens '
           'WHERE user_id = ? AND revoked_at IS NULL '
           'AND (expires_at IS NULL OR expires_at > ?)')
    params = [user_id, db.now_iso()]
    if exclude_id is not None:
        sql += ' AND id <> ?'
        params.append(exclude_id)
    return conn.execute(sql, params).fetchone()['n']


def _create_token(conn, *, user_id, created_by, label, scopes, expires_at):
    count = _active_token_count(conn, user_id)
    if count >= MAX_TOKENS_PER_USER:
        raise _err(409, 'TOKEN_LIMIT_REACHED',
                   f'每个账号最多 {MAX_TOKENS_PER_USER} 个有效令牌，请先撤销一个')
    plaintext = generate_token()
    created_at = db.now_iso()
    conn.execute(
        'INSERT INTO api_tokens '
        '(token_hash, user_id, label, scopes, created_at, created_by, expires_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        (security.token_fingerprint(plaintext), user_id, label,
         json.dumps(scopes, ensure_ascii=False), created_at, created_by, expires_at),
    )
    token_id = conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
    store.audit_event(
        conn, owner_id=user_id, scope='api', event='api.token.created',
        message=f'创建令牌 #{token_id}（{label or "无标签"}）',
        detail={'tokenId': token_id, 'userId': user_id, 'createdBy': created_by,
                'scopes': scopes, 'expiresAt': expires_at},
    )
    conn.commit()
    return plaintext, token_id


def _revoke_token(conn, token_id, actor_uid):
    row = _token_row(conn, token_id)
    if row is None:
        raise _err(404, 'NOT_FOUND', '令牌不存在')
    if not row['revoked_at']:
        now = db.now_iso()
        conn.execute('UPDATE api_tokens SET revoked_at = ?, revoked_by = ? WHERE id = ?',
                     (now, actor_uid, token_id))
        store.audit_event(
            conn, owner_id=row['user_id'], scope='api', event='api.token.revoked',
            message=f'撤销令牌 #{token_id}',
            detail={'tokenId': token_id, 'userId': row['user_id'],
                    'revokedBy': actor_uid},
        )
        conn.commit()
    return row


def _rotate_token(conn, token_id, actor_uid):
    old = _token_row(conn, token_id)
    if old is None:
        raise _err(404, 'NOT_FOUND', '令牌不存在')
    if old['revoked_at']:
        raise _err(409, 'TOKEN_REVOKED', '已撤销的令牌不能轮换')
    # 排除自身：轮换是"换一把"，不是"在满额基础上再加一把"。
    # 插入新行与撤销旧行仍在同一事务里，最后统一 commit，不引入中间态。
    count = _active_token_count(conn, old['user_id'], exclude_id=token_id)
    if count >= MAX_TOKENS_PER_USER:
        raise _err(409, 'TOKEN_LIMIT_REACHED',
                   f'每个账号最多 {MAX_TOKENS_PER_USER} 个有效令牌，请先撤销一个')
    plaintext = generate_token()
    now = db.now_iso()
    try:
        scopes = json.loads(old['scopes'] or '[]')
    except (TypeError, ValueError):
        scopes = list(DEFAULT_SCOPES)
    # 同一事务：插新 + 撤旧，不给重叠宽限期（调用方须原子替换）。
    conn.execute(
        'INSERT INTO api_tokens '
        '(token_hash, user_id, label, scopes, created_at, created_by, expires_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        (security.token_fingerprint(plaintext), old['user_id'], old['label'],
         json.dumps(scopes, ensure_ascii=False), now, actor_uid, old['expires_at']),
    )
    new_id = conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
    conn.execute('UPDATE api_tokens SET revoked_at = ?, revoked_by = ? WHERE id = ?',
                 (now, actor_uid, token_id))
    store.audit_event(
        conn, owner_id=old['user_id'], scope='api', event='api.token.created',
        message=f'轮换令牌 #{token_id} → #{new_id}',
        detail={'tokenId': new_id, 'rotatedFrom': token_id,
                'userId': old['user_id'], 'createdBy': actor_uid, 'scopes': scopes},
    )
    store.audit_event(
        conn, owner_id=old['user_id'], scope='api', event='api.token.revoked',
        message=f'轮换后撤销旧令牌 #{token_id}',
        detail={'tokenId': token_id, 'userId': old['user_id'], 'revokedBy': actor_uid},
    )
    conn.commit()
    return plaintext, new_id


# --------------------------------------------------------------------------
# 令牌管理端点（浏览器面 /api/admin/api-tokens，不走 /v1）
# --------------------------------------------------------------------------

def _manage_target(handler, conn, user, target_uid, what):
    """本人可直接操作；操作他人必须复用唯一的实例管理员门槛。"""
    target = str(target_uid or user['id']).strip()
    if target != user['id']:
        _require_instance_admin(handler, conn, what=what)
    return target


def admin_tokens_list(handler, conn, body, query):
    user = handler._require_user(conn)
    target = _manage_target(handler, conn, user, _param(query, 'userId'), '查看他人令牌')
    rows = conn.execute(
        'SELECT id, token_hash, user_id, label, scopes, created_at, created_by, '
        '       last_used_at, expires_at, revoked_at, revoked_by '
        'FROM api_tokens WHERE user_id = ? ORDER BY id DESC', (target,),
    ).fetchall()
    tokens = []
    for row in rows:
        try:
            scopes = json.loads(row['scopes'] or '[]')
        except (TypeError, ValueError):
            scopes = []
        tokens.append({
            'id': row['id'], 'userId': row['user_id'], 'label': row['label'] or '',
            # hash 前 8 位只是"哪一把令牌"的线索，不是明文前缀，泄露不了令牌。
            'tokenHint': (row['token_hash'] or '')[:8] + '…',
            'scopes': scopes if isinstance(scopes, list) else [],
            'createdAt': row['created_at'], 'createdBy': row['created_by'],
            'lastUsedAt': row['last_used_at'], 'expiresAt': row['expires_at'],
            'revokedAt': row['revoked_at'], 'revokedBy': row['revoked_by'],
            'revoked': bool(row['revoked_at']),
        })
    handler._json({'tokens': tokens, 'total': len(tokens)})


def admin_tokens_create(handler, conn, body, query):
    user = handler._require_user(conn)
    target = _manage_target(handler, conn, user, body.get('userId'), '代他人创建令牌')
    label = str(body.get('label') or '').strip()[:MAX_TOKEN_LABEL]
    scopes = normalize_scopes(body.get('scopes'))
    expires_at = _expiry_from_body(body)
    plaintext, token_id = _create_token(
        conn, user_id=target, created_by=user['id'], label=label,
        scopes=scopes, expires_at=expires_at,
    )
    # 明文**只在这一次响应里出现**：不写日志、不写审计、不落库。
    handler._json({
        'token': plaintext, 'id': token_id,
        'tokenHint': plaintext[:8] + '…',
        'userId': target, 'label': label, 'scopes': scopes, 'expiresAt': expires_at,
        'warning': '令牌只显示这一次，请立即复制保存；关闭后无法再次查看。',
    }, status=201, extra_headers={'Cache-Control': 'no-store'})


def admin_tokens_revoke(handler, conn, body, query):
    user = handler._require_user(conn)
    try:
        token_id = int(handler._path_params['id'])
    except (KeyError, TypeError, ValueError):
        raise _err(400, 'BAD_PATH', '令牌 id 必须是整数')
    row = _token_row(conn, token_id)
    if row is None:
        raise _err(404, 'NOT_FOUND', '令牌不存在')
    _manage_target(handler, conn, user, row['user_id'], '撤销他人令牌')
    _revoke_token(conn, token_id, user['id'])
    handler._json({'id': token_id, 'revoked': True})


def admin_tokens_rotate(handler, conn, body, query):
    user = handler._require_user(conn)
    try:
        token_id = int(handler._path_params['id'])
    except (KeyError, TypeError, ValueError):
        raise _err(400, 'BAD_PATH', '令牌 id 必须是整数')
    row = _token_row(conn, token_id)
    if row is None:
        raise _err(404, 'NOT_FOUND', '令牌不存在')
    _manage_target(handler, conn, user, row['user_id'], '轮换他人令牌')
    plaintext, new_id = _rotate_token(conn, token_id, user['id'])
    handler._json({
        'token': plaintext, 'id': new_id, 'tokenHint': plaintext[:8] + '…',
        'rotatedFrom': token_id, 'revokedImmediately': True,
        'warning': '旧令牌已立即失效，且没有宽限期；请先替换调用方配置。',
    }, extra_headers={'Cache-Control': 'no-store'})


# --------------------------------------------------------------------------
# 只读 REST 能力（B1）
# --------------------------------------------------------------------------

def h_health(handler, conn, body, query):
    """只回存活与版本，**不回** llmConfigured / storage / 管理员数量等实例信息。"""
    handler._json({'ok': True, 'version': APP_VERSION, 'build': handler.cfg.build},
                  extra_headers={'Cache-Control': 'no-store'})


h_health.gtd_no_auth = True


def h_me(handler, conn, body, query):
    user = handler._api_user
    handler._json({
        'id': user['id'], 'email': user['email'], 'name': user['name'],
        'isSystemAdmin': user['isSystemAdmin'],
        'scopes': list(handler._api_scopes),
        'tokenId': handler._token_id,
        'tokenLabel': handler._token_label,
    })


def h_teams(handler, conn, body, query):
    rows = store.select_table(conn, _ctx(handler, conn), 'teams')
    handler._json({'teams': rows, 'total': len(rows)})


def _visible_work_items(handler, conn, query, *, include_archived=False):
    rows = store.select_table(conn, _ctx(handler, conn), 'work_items')

    team_raw = _param(query, 'teamId')
    if team_raw not in (None, ''):
        try:
            team_id = int(team_raw)
        except (TypeError, ValueError):
            raise _err(400, 'BAD_QUERY', 'teamId 必须是整数')
        rows = [r for r in rows if int(r.get('team_id') or 0) == team_id]
    status = _param(query, 'status')
    if status:
        rows = [r for r in rows if str(r.get('status') or '') == status]
    assignee = _param(query, 'assignee')
    if assignee:
        rows = [r for r in rows
                if str(r.get('assignee_id') or '') == assignee
                or str(r.get('assignee_name') or '') == assignee]
    for key, column in (('projectId', 'project_id'),
                        ('areaId', 'area_id'), ('contextId', 'context_id')):
        raw = _param(query, key)
        if raw not in (None, ''):
            try:
                value = int(raw)
            except (TypeError, ValueError):
                raise _err(400, 'BAD_QUERY', f'{key} 必须是整数')
            rows = [r for r in rows if int(r.get(column) or 0) == value]
    since = _param(query, 'updatedSince')
    if since:
        rows = [r for r in rows if str(r.get('updated_at') or '') >= str(since)]
    if not include_archived:
        rows = [r for r in rows if not r.get('archived_at')]
    limit = _int_param(query, 'limit', _DEFAULT_LIMIT, low=1, high=_MAX_LIMIT)
    return rows[:limit]


def h_work_items(handler, conn, body, query):
    rows = _visible_work_items(handler, conn, query)
    handler._json({'items': rows, 'total': len(rows)})


def h_work_item(handler, conn, body, query):
    try:
        row_id = int(handler._path_params['id'])
    except (KeyError, TypeError, ValueError):
        raise _err(400, 'BAD_PATH', '工单 id 必须是整数')
    row = store._fetch(conn, 'work_items', row_id)
    if row is None or not can_select('work_items', row, _ctx(handler, conn)):
        raise _err(404, 'not_found', '记录不存在或不可见')
    # 软删除后单条必须和列表一致地"不存在"（TC-SEC-INT-21）：否则一条已经
    # 从列表隐去的记录仍能被猜 id 读回全文，等于泄露存在性与全部字段。
    # restore 不走这里：它经 _conditional_update → store.update_versioned 直接取行，
    # 因此归档行仍可被恢复，不受这道闸门影响。
    if row.get('archived_at'):
        raise _err(404, 'not_found', '记录不存在或不可见')
    handler._json(row, extra_headers={'ETag': _etag(row)})


def _make_basic_handler(table, key):
    def handler_fn(handler, conn, body, query):
        rows = store.select_table(conn, _ctx(handler, conn), table)
        team_raw = _param(query, 'teamId')
        if team_raw not in (None, ''):
            try:
                team_id = int(team_raw)
            except (TypeError, ValueError):
                raise _err(400, 'BAD_QUERY', 'teamId 必须是整数')
            rows = [r for r in rows if int(r.get('team_id') or 0) == team_id]
        handler._json({key: rows, 'total': len(rows)})
    handler_fn.gtd_scope = 'read'
    return handler_fn


def h_members(handler, conn, body, query):
    rows = store.select_table(conn, _ctx(handler, conn), 'team_members')
    team_raw = _param(query, 'teamId')
    if team_raw not in (None, ''):
        try:
            team_id = int(team_raw)
        except (TypeError, ValueError):
            raise _err(400, 'BAD_QUERY', 'teamId 必须是整数')
        rows = [r for r in rows if int(r.get('team_id') or 0) == team_id]
    # 显式投影：即便将来 team_members 加了敏感列也不会顺着接口漏出去。
    members = [{key: row.get(key) for key in
                ('id', 'email', 'display_name', 'role', 'status')} for row in rows]
    handler._json({'members': members, 'total': len(members)})


def h_search(handler, conn, body, query):
    term = (_param(query, 'q') or '').strip().lower()
    if not term:
        raise _err(400, 'BAD_QUERY', '缺少搜索参数 q')
    limit = _int_param(query, 'limit', 50, low=1, high=200)
    ctx = _ctx(handler, conn)
    results = []
    for table, label, fields in (
        ('work_items', 'work_item', ('title', 'notes')),
        ('projects', 'project', ('name', 'description')),
        ('areas', 'area', ('name', 'description')),
        ('contexts', 'context', ('name',)),
        ('team_members', 'member', ('email', 'display_name')),
    ):
        # 先过可见性，再做子串匹配 —— 绝不能"先搜再过滤"把别人的内容当命中。
        for row in store.select_table(conn, ctx, table):
            # 与列表/单条同一口径：软删行不参与搜索，否则又是一条存在性泄露通道。
            if row.get('archived_at'):
                continue
            if any(term in str(row.get(field) or '').lower() for field in fields):
                results.append({'type': label, 'id': row.get('id'), 'row': row})
                if len(results) >= limit:
                    break
        if len(results) >= limit:
            break
    handler._json({'results': results, 'total': len(results)})


def h_openapi(handler, conn, body, query):
    paths = {}
    for table in (API_ROUTES, API_PARAM_ROUTES):
        for (method, path), fn in table.items():
            entry = paths.setdefault(path, {})
            entry[method.lower()] = {
                'summary': (fn.__doc__ or f'{method} {path}').strip().split('\n')[0],
                'security': ([] if getattr(fn, 'gtd_no_auth', False)
                             else [{'bearerAuth': [getattr(fn, 'gtd_scope', 'read')]}]),
                'responses': {'200': {'description': 'OK'}},
            }
    handler._json({
        'openapi': '3.0.3',
        'info': {'title': 'GTD4Teams 本地 API', 'version': APP_VERSION,
                 'description': '只绑定 127.0.0.1；带 Origin 头的请求一律拒绝。'},
        'paths': paths,
        'components': {'securitySchemes': {
            'bearerAuth': {'type': 'http', 'scheme': 'bearer',
                           'description': 'gtd_pat_ 前缀的本地个人访问令牌'}}},
    })


# --------------------------------------------------------------------------
# 写能力（B3）：全部走 store.*_versioned
# --------------------------------------------------------------------------

def _idem_from_headers(handler, body, method, path):
    key = (handler.headers.get('Idempotency-Key') or '').strip()[:MAX_IDEM_KEY]
    if not key:
        return None
    canonical = json.dumps(body, sort_keys=True, separators=(',', ':'),
                           ensure_ascii=False)
    return {'key': key, 'method': method, 'path': path,
            'request_hash': security.token_fingerprint(canonical)}


def _expected_version(handler, body, row_id):
    """If-Match 与 expectedVersion 二选一；都给且不一致 → 400。"""
    header = (handler.headers.get('If-Match') or '').strip()
    from_header = None
    if header:
        if header == '*':
            raise _err(400, 'BAD_PRECONDITION', '不支持 If-Match: *（那等于放弃版本保护）')
        match = _ETAG_RE.match(header)
        if not match:
            raise _err(400, 'BAD_PRECONDITION', 'If-Match 格式应为 W/"<id>-<version>"')
        if int(match.group(1)) != row_id:
            raise _err(400, 'PRECONDITION_CONFLICT', 'If-Match 中的 id 与路径不一致')
        from_header = int(match.group(2))

    raw = body.get('expectedVersion')
    from_body = None
    if raw is not None and raw != '':
        try:
            from_body = int(raw)
        except (TypeError, ValueError):
            raise _err(400, 'BAD_PRECONDITION', 'expectedVersion 必须是整数')
    if from_header is not None and from_body is not None and from_header != from_body:
        raise _err(400, 'PRECONDITION_CONFLICT', 'If-Match 与 expectedVersion 不一致')
    return from_header if from_header is not None else from_body


def _send_write_result(handler, result):
    headers = {'ETag': result['etag']}
    if result.get('replay'):
        headers['X-Gtd-Idempotent-Replay'] = '1'
    if result.get('unchanged'):
        headers['X-Gtd-Unchanged'] = '1'
    handler._json(result['data'], status=result['status'], extra_headers=headers)


def _audit_context_method(handler, method):
    audit = _audit_context(handler)
    audit['method'] = method
    audit['path'] = handler._path
    return audit


def h_work_item_create(handler, conn, body, query):
    """创建工单：`owner_id` 一律由服务端注入，客户端提交的版本/归档字段被丢弃。"""
    ctx = _ctx(handler, conn)
    result = store.insert_versioned(
        conn, ctx, 'work_items', body,
        audit=_audit_context_method(handler, 'POST'),
        idempotency=_idem_from_headers(handler, body, 'POST', handler._path),
    )
    _send_write_result(handler, result)


def _conditional_update(handler, conn, body, *, server_values=None,
                        require_delete=False, method='PATCH'):
    try:
        row_id = int(handler._path_params['id'])
    except (KeyError, TypeError, ValueError):
        raise _err(400, 'BAD_PATH', '工单 id 必须是整数')
    expected = _expected_version(handler, body, row_id)
    result = store.update_versioned(
        conn, _ctx(handler, conn), 'work_items', row_id, body,
        expected_version=expected, server_values=server_values,
        audit=_audit_context_method(handler, method),
        idempotency=_idem_from_headers(handler, body, method, handler._path),
        require_delete=require_delete,
    )
    _send_write_result(handler, result)


def h_work_item_patch(handler, conn, body, query):
    """条件更新：必须带 If-Match 或 expectedVersion；未变更不推进版本。"""
    _conditional_update(handler, conn, body, method='PATCH')


def h_work_item_complete(handler, conn, body, query):
    _conditional_update(
        handler, conn, body, method='POST',
        server_values={'status': 'done', 'completed_at': db.now_iso()},
    )


def h_work_item_archive(handler, conn, body, query):
    _conditional_update(handler, conn, body, method='POST',
                        server_values={'archived_at': db.now_iso()})


def h_work_item_restore(handler, conn, body, query):
    _conditional_update(handler, conn, body, method='POST',
                        server_values={'archived_at': None})


def h_work_item_delete(handler, conn, body, query):
    """软删除：写 archived_at + status='archived'，绝不物理 DELETE。"""
    _conditional_update(
        handler, conn, body, method='DELETE', require_delete=True,
        server_values={'archived_at': db.now_iso(), 'status': 'archived'},
    )


# --------------------------------------------------------------------------
# 路由表（与浏览器面的 ROUTES 完全分离）
# --------------------------------------------------------------------------

def _read(name):
    fn = globals()[name]
    fn.gtd_scope = 'read'
    return fn


def _write(name, scope):
    fn = globals()[name]
    fn.gtd_scope = scope
    return fn


API_ROUTES = {
    ('GET', '/v1/health'): h_health,
    ('GET', '/v1/me'): _read('h_me'),
    ('GET', '/v1/teams'): _read('h_teams'),
    ('GET', '/v1/work-items'): _read('h_work_items'),
    ('POST', '/v1/work-items'): _write('h_work_item_create', 'write:edit'),
    ('GET', '/v1/projects'): _make_basic_handler('projects', 'projects'),
    ('GET', '/v1/areas'): _make_basic_handler('areas', 'areas'),
    ('GET', '/v1/contexts'): _make_basic_handler('contexts', 'contexts'),
    ('GET', '/v1/members'): h_members,
    ('GET', '/v1/search'): _read('h_search'),
    ('GET', '/v1/openapi.json'): _read('h_openapi'),
}

API_PARAM_ROUTES = {
    ('GET', '/v1/work-items/{id}'): _read('h_work_item'),
    ('PATCH', '/v1/work-items/{id}'): _write('h_work_item_patch', 'write:edit'),
    ('POST', '/v1/work-items/{id}/complete'): _write('h_work_item_complete', 'write:edit'),
    ('POST', '/v1/work-items/{id}/archive'): _write('h_work_item_archive', 'write:archive'),
    ('POST', '/v1/work-items/{id}/restore'): _write('h_work_item_restore', 'write:edit'),
    ('DELETE', '/v1/work-items/{id}'): _write('h_work_item_delete', 'write:delete'),
}


class IntegrationHandler(Handler):
    """本地 API 监听器使用的 Handler：路由表不同，其余全部继承。

    与 Handler 的差异只有三处：
      ① 不托管静态资源（SERVE_STATIC=False）；
      ② 任何带 Origin 的请求一律 403（REJECT_ANY_ORIGIN=True）；
      ③ 路由命中后先做 Bearer 鉴权与 scope 闸门（_before_handler）。
    """

    ROUTES_TABLE = API_ROUTES
    PARAM_ROUTES_TABLE = API_PARAM_ROUTES
    SERVE_STATIC = False
    REJECT_ANY_ORIGIN = True

    def _before_handler(self, conn, handler):
        if getattr(handler, 'gtd_no_auth', False):
            return
        required = getattr(handler, 'gtd_scope', 'read')
        user, scopes = self._api_identity(conn)
        self._api_user = user
        self._api_scopes = tuple(scopes)
        if required not in scopes:
            store.audit_denied(
                conn, SimpleNamespace(uid=user['id']), _audit_context(self),
                method=self.command, path=self._path, code='INSUFFICIENT_SCOPE',
            )
            raise _err(403, 'INSUFFICIENT_SCOPE',
                       f'该令牌缺少 {required} 权限（未落库）')
