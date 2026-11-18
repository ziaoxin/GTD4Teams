"""业务数据访问层：工作区读取、通用行级 CRUD、团队生命周期、复盘。

它取代了原先"前端直接调云数据库 REST 接口"的那条路径。所有写操作都遵循同一条纪律：

    先取原行 → 过权限策略（policy.py） → 执行 → 返回**受影响的行**
    被策略拦下时返回空列表，绝不抛异常，也绝不假装成功。

前端 `js/app.js` 的 `assertAffected()` 正是靠"空数组 = 没改到"来向用户如实提示的，
这条约定不能改。

路径参数一律做成占位符绑定，不做字符串拼接 —— 本项目没有动态表名之外的 SQL 拼接，
动态的部分只有表名，且表名来自 `db.WRITABLE_TABLES` 白名单。
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from . import db, security
from .db import COLUMNS, DEFAULT_ORDER, LOG_LIMIT, WRITABLE_TABLES
from .policy import Denied, can_delete, can_insert, can_select, can_update


class StoreError(Exception):
    """带机读错误码的业务异常，由 HTTP 层翻译成响应。"""

    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _assert_table(table):
    if table not in WRITABLE_TABLES:
        raise StoreError('UNKNOWN_TABLE', f'数据表 {table} 不存在或不可写', 404)


#: url 字段允许的协议。
#: 不含 `javascript` / `data` / `vbscript` —— 这三个是"点一下就在同源执行脚本"的入口。
URL_SCHEMES = ('http', 'https', 'mailto')


def _normalize_urls(table, values):
    """归一化 url 字段并校验协议，只放行 http / https / mailto。

    为什么必须做在服务端：前端那层 HTML 转义只保证"不破坏 HTML 结构"，
    不保证"这个 URL 的协议是安全的"。`javascript:alert(document.cookie)`
    完全可以通过转义进入 href 属性，点击即执行。
    实测过四种写法（`javascript:` / `data:text/html,...` / `vbscript:` /
    前导空格 + 大小写混淆的 `  JaVaScRiPt:`）全部原样落库并在工作区回显 ——
    前端兜底不算修复，服务端白名单才是。

    归一化只做去前后空白（并写回），不做编码或补全协议：猜协议容易猜错，
    不如让用户写清楚，报错文案里也说了支持哪些。
    """
    if 'url' not in COLUMNS[table]:
        return
    raw = values.get('url')
    if raw is None:
        return
    text = str(raw).strip()
    if not text:
        values['url'] = ''
        return
    # 只在第一个冒号处切：`mailto:a@b` 的冒号后面还有内容，但它不是协议分隔。
    scheme = text.split(':', 1)[0].lower() if ':' in text else ''
    if scheme not in URL_SCHEMES:
        raise StoreError('INVALID_URL', '链接只支持 http / https / mailto', 400)
    values['url'] = text


def _fetch(conn, table, row_id):
    row = conn.execute(f'SELECT * FROM {table} WHERE id = ?', (row_id,)).fetchone()
    return db.decode_row(table, row) if row else None


def _fetch_all(conn, table):
    return [db.decode_row(table, r) for r in conn.execute(f'SELECT * FROM {table}').fetchall()]


def _insert_raw(conn, table, values):
    """底层插入。values 已经过类型归一化，键全部来自字段规格。"""
    cols = ', '.join(values.keys())
    marks = ', '.join('?' * len(values))
    cur = conn.execute(f'INSERT INTO {table} ({cols}) VALUES ({marks})', list(values.values()))
    return cur.lastrowid


def _update_raw(conn, table, row_id, values):
    assignments = ', '.join(f'{col} = ?' for col in values)
    conn.execute(f'UPDATE {table} SET {assignments} WHERE id = ?', [*values.values(), row_id])


# --------------------------------------------------------------------------
# 读取
# --------------------------------------------------------------------------

def select_table(conn, ctx, table, *, limit=None):
    """按权限策略筛选可见行。

    这里在 Python 里逐行判策略而不是把策略编译成 SQL：策略与 RLS 表达式是一一对应的
    可读代码，编译成 SQL 会让两边难以对照；而本应用的数据体量是"一个小团队的工单"，
    并且前端本来就是一次性把整个工作区拿进内存（loadWorkspace），没有额外开销。
    """
    _assert_table(table)
    rows = _fetch_all(conn, table)
    visible = [r for r in rows if can_select(table, r, ctx)]
    order_col, direction = DEFAULT_ORDER.get(table, ('id', 'ASC'))
    reverse = direction.upper() == 'DESC'
    visible.sort(key=lambda r: (r.get(order_col) is None, r.get(order_col)), reverse=reverse)
    if limit is not None:
        visible = visible[:limit]
    return visible


def load_workspace(conn, ctx):
    """一次性拉取当前账号可见的全部工作区数据。

    键名与云版本 `api.loadWorkspace()` 的返回结构逐字一致，
    因为 `js/app.js` 的 `state.data` 直接消费它。
    """
    return {
        'teams': select_table(conn, ctx, 'teams'),
        'members': select_table(conn, ctx, 'team_members'),
        'items': select_table(conn, ctx, 'work_items'),
        'projects': select_table(conn, ctx, 'projects'),
        'areas': select_table(conn, ctx, 'areas'),
        'contexts': select_table(conn, ctx, 'contexts'),
        'resources': select_table(conn, ctx, 'resources'),
        'reviews': select_table(conn, ctx, 'reviews'),
        'onCall': select_table(conn, ctx, 'on_call'),
        'logs': select_table(conn, ctx, 'progress_logs', limit=LOG_LIMIT),
    }


# --------------------------------------------------------------------------
# 通用写：与云版本 database.from(table).insert/update/delete 等价
# --------------------------------------------------------------------------

def insert_row(conn, ctx, table, payload):
    _assert_table(table)
    values, _dropped = db.split_payload(table, payload, for_insert=True)
    _normalize_urls(table, values)      # 协议白名单：在写入前拦，不依赖前端

    spec = COLUMNS[table]
    now = db.now_iso()

    # 归属：列默认值 auth.uid() 的本地等价物 —— 由服务端写入，客户端提供的一律忽略
    if 'owner_id' in spec:
        values['owner_id'] = ctx.uid
    if 'created_at' in spec:
        values['created_at'] = now
    if 'updated_at' in spec:
        values['updated_at'] = now

    prospective = dict(values)
    if not can_insert(table, prospective, ctx):
        raise Denied(table, 'insert', '没有权限在该范围内新建数据')

    row_id = _insert_raw(conn, table, values)
    # 先取回受影响的行，**最后**才提交：
    # 提交之后不再有任何可能抛异常的动作，也就没有"已提交但不可重放"的窗口
    # （见 db.TrackedConnection 与 httpd._can_replay）。
    row = _fetch(conn, table, row_id)
    conn.commit()
    return [row]


def update_row(conn, ctx, table, row_id, payload):
    _assert_table(table)
    current = _fetch(conn, table, row_id)
    if current is None:
        return []

    values, _dropped = db.split_payload(table, payload, for_insert=False)
    _normalize_urls(table, values)      # 协议白名单：与 insert 同一道闸
    if 'updated_at' in COLUMNS[table]:
        values['updated_at'] = db.now_iso()

    prospective = {**current, **values}
    if not can_update(table, current, prospective, ctx):
        return []                      # 静默拦下：与 RLS 的 0 行语义一致

    if not values:
        return [current]
    _update_raw(conn, table, row_id, values)
    row = _fetch(conn, table, row_id)  # 取回放在提交之前，理由同 insert_row
    conn.commit()
    return [row]


def delete_row(conn, ctx, table, row_id):
    _assert_table(table)
    current = _fetch(conn, table, row_id)
    if current is None:
        return []
    if not can_delete(table, current, ctx):
        return []
    conn.execute(f'DELETE FROM {table} WHERE id = ?', (row_id,))
    conn.commit()
    return [{'id': row_id}]            # 与云版本 .select('id') 的形状一致


# --------------------------------------------------------------------------
# 集成域专用写入：版本校验 + 审计 + 幂等，同一次提交（TDS §16.6 / §16.7）
# --------------------------------------------------------------------------
#
# 为什么单独一套而不是改 insert_row/update_row：
#   · 那两个函数被前端 assertAffected 与旧测试依赖，"看不见/改不到"返回 [] 是契约；
#   · 机器接口需要可区分的 404 / 403 / 412，且要把"版本 +1 / 审计 / 幂等回执"
#     塞进**同一次**提交。两者语义不同，强行共用只会把双方都弄坏。
#
# 三条硬纪律：
#   1. 权限判定仍然逐行调用 policy 的 can_*，这里没有第二条权限实现；
#   2. 客户端字段一律经 db.split_payload —— owner_id / version / archived_at
#      都会在那里被丢弃（归属由服务端注入，版本号只有服务端能改）；
#   3. 服务端独占字段只从 server_values 传入，且只由 handler 里的常量构造，
#      **绝不**把客户端 JSON 直接塞进它。

IDEMPOTENCY_TTL_HOURS = 24
IDEMPOTENCY_BATCH = 100
MAX_IDEMPOTENCY_RESPONSE_BYTES = 64 * 1024


def _audit_insert(conn, *, owner_id, scope, event, message, detail):
    """只 INSERT 不提交 —— 提交时机由调用方决定（成功要和业务写同一事务）。"""
    return _insert_raw(conn, 'app_logs', {
        'owner_id': owner_id or None,
        'level': 'info',
        'scope': scope[:32],
        'event': event[:120],
        'message': message[:1000],
        'detail': db.encode_value('json', detail if isinstance(detail, dict) else {}),
        'created_at': db.now_iso(),
    })


def _audit_write(conn, ctx, audit, *, method, path, table, row_id,
                 before, after, result):
    """成功写审计：调用方保证随后 conn.commit() 与业务写同一次提交。"""
    detail = dict(audit or {})
    detail.update({
        'method': method, 'path': path, 'table': table, 'rowId': row_id,
        'versionBefore': before, 'versionAfter': after, 'result': result,
    })
    _audit_insert(
        conn, owner_id=ctx.uid, scope=(audit or {}).get('scope', 'api'),
        event='api.write',
        message=f'{method} {path} → {table}#{row_id}',
        detail=detail,
    )


def _audit_denied(conn, ctx, audit, *, method, path, code):
    """失败路径：业务没写，审计是唯一写入 → 用 bookkeeping 提交单独落账。

    用 bookkeeping_commit 而不是 commit：不污染 commit_attempted，
    否则一次 403 会让本次请求失去后续写锁重试的机会（db.TrackedConnection）。
    """
    try:
        detail = dict(audit or {})
        detail.update({'method': method, 'path': path, 'result': 'denied',
                       'gtdCode': code})
        _audit_insert(
            conn, owner_id=ctx.uid, scope=(audit or {}).get('scope', 'api'),
            event='api.denied', message=f'{method} {path} → denied ({code})',
            detail=detail,
        )
        db.bookkeeping_commit(conn)
    except sqlite3.Error:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass


def _idem_lookup(conn, ctx, idempotency):
    """命中历史回执 → 返回 dict；键冲突 → 409；未命中 → None。"""
    key = (idempotency or {}).get('key')
    if not key:
        return None
    row = conn.execute(
        'SELECT status, response, request_hash, method, path '
        'FROM api_idempotency WHERE user_id = ? AND idem_key = ?',
        (ctx.uid, key),
    ).fetchone()
    if row is None:
        return None
    if (row['request_hash'] != idempotency['request_hash']
            or row['method'] != idempotency['method']
            or row['path'] != idempotency['path']):
        raise StoreError('idempotency_conflict', '相同幂等键对应的请求体不一致', 409)
    data = json.loads(row['response'])
    return {'status': row['status'], 'data': data, 'replay': True,
            'unchanged': False,
            'etag': 'W/"{}-{}"'.format(data.get('id'), data.get('version') or 1)}


def _idem_store(conn, ctx, idempotency, status, data):
    """存首份响应。超过 64KB 不存（不把幂等表变成第二个大对象存储）。"""
    key = (idempotency or {}).get('key')
    if not key:
        return False
    response = json.dumps(data, ensure_ascii=False)
    if len(response.encode('utf-8')) > MAX_IDEMPOTENCY_RESPONSE_BYTES:
        return False
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(hours=IDEMPOTENCY_TTL_HOURS)).strftime('%Y-%m-%dT%H:%M:%SZ')
    # 只用"插入时顺手清理"，绝不在读路径做 DML（读接口不该抢写锁）。
    # 不用 DELETE ... LIMIT：该语法依赖编译选项，用子查询更可移植。
    conn.execute(
        'DELETE FROM api_idempotency WHERE id IN '
        '(SELECT id FROM api_idempotency WHERE created_at < ? ORDER BY id LIMIT ?)',
        (cutoff, IDEMPOTENCY_BATCH),
    )
    conn.execute(
        'INSERT INTO api_idempotency '
        '(user_id, idem_key, method, path, request_hash, status, response, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (ctx.uid, key, idempotency['method'], idempotency['path'],
         idempotency['request_hash'], status, response, db.now_iso()),
    )
    return True


def _version_error(current, table):
    err = StoreError('task_version_conflict', '记录已被他人修改，请重读后再提交', 412)
    err.current = current                       # _error() 会把它放进响应体
    err.etag = f'W/"{current["id"]}-{current.get("version") or 1}"'
    return err


def insert_versioned(conn, ctx, table, payload, *, audit=None, idempotency=None):
    """集成域创建。审计 + 幂等回执与业务插入同一次提交。"""
    _assert_table(table)
    replay = _idem_lookup(conn, ctx, idempotency)
    if replay:
        return replay

    values, _dropped = db.split_payload(table, payload, for_insert=True)
    _normalize_urls(table, values)
    now = db.now_iso()
    spec = COLUMNS[table]
    if 'owner_id' in spec:
        values['owner_id'] = ctx.uid
    if 'created_at' in spec:
        values['created_at'] = now
    if 'updated_at' in spec:
        values['updated_at'] = now
    if 'version' in spec:                       # 'lock' 已被 split_payload 丢弃
        values['version'] = 1
    if 'archived_at' in spec:
        values['archived_at'] = None

    if not can_insert(table, dict(values), ctx):
        _audit_denied(conn, ctx, audit,
                      method=(audit or {}).get('method', 'POST'),
                      path=(audit or {}).get('path', ''), code='permission_denied')
        raise StoreError('permission_denied', '没有权限在该范围内新建数据', 403)

    row_id = _insert_raw(conn, table, values)
    row = _fetch(conn, table, row_id)           # 取回必须在 commit 之前
    _audit_write(conn, ctx, audit,
                 method=(audit or {}).get('method', 'POST'),
                 path=(audit or {}).get('path', ''),
                 table=table, row_id=row_id, before=None,
                 after=row.get('version'), result='created')
    data = dict(row)
    _idem_store(conn, ctx, idempotency, 201, data)
    conn.commit()
    return {'status': 201, 'data': data, 'replay': False, 'unchanged': False,
            'etag': f'W/"{row_id}-{row.get("version") or 1}"'}


def update_versioned(conn, ctx, table, row_id, payload, *, expected_version,
                     server_values=None, audit=None, idempotency=None,
                     require_delete=False):
    """集成域条件更新。

    expected_version 缺失 → 428；不符 → 412（且不落库）；
    可见但无写权 → 403；不可见/不存在 → 404（不泄露存在性）；
    逐字段归一后全等 → 200 + X-Gtd-Unchanged，不 commit、不推进版本、不写审计。
    """
    _assert_table(table)
    replay = _idem_lookup(conn, ctx, idempotency)
    if replay:
        return replay

    current = _fetch(conn, table, row_id)
    if current is None or not can_select(table, current, ctx):
        raise StoreError('not_found', '记录不存在或不可见', 404)

    method = (audit or {}).get('method', 'PATCH')
    path = (audit or {}).get('path', '')
    if require_delete and not can_delete(table, current, ctx):
        _audit_denied(conn, ctx, audit, method=method, path=path, code='forbidden')
        raise StoreError('forbidden', '没有删除权限', 403)

    values, _dropped = db.split_payload(table, payload, for_insert=False)
    _normalize_urls(table, values)
    server_values = dict(server_values or {})   # 服务端独占字段，非客户端输入
    prospective = {**current, **values, **server_values}
    if not can_update(table, current, prospective, ctx):
        _audit_denied(conn, ctx, audit, method=method, path=path, code='forbidden')
        raise StoreError('forbidden', '没有修改权限', 403)

    before = int(current.get('version') or 1)
    if expected_version is None:
        raise StoreError('PATCH_PRECONDITION_REQUIRED',
                         '缺少 If-Match 或 expectedVersion，拒绝无保护写入', 428)
    try:
        expected = int(expected_version)
    except (TypeError, ValueError):
        raise StoreError('BAD_PRECONDITION', 'expectedVersion 必须是整数', 400)
    if expected != before:
        err = _version_error(current, table)
        _audit_denied(conn, ctx, audit, method=method, path=path,
                      code='task_version_conflict')
        raise err

    # 按 kind 归一后比较：{"flagged": "true"} 不能算成一次真实变更。
    changed = {}
    spec = COLUMNS[table]
    for key, value in {**values, **server_values}.items():
        kind = spec.get(key)
        normalized = value if kind == 'lock' else db.encode_value(kind, value)
        if current.get(key) != normalized:
            changed[key] = value
    if not changed:
        return {'status': 200, 'data': dict(current), 'replay': False,
                'unchanged': True,
                'etag': f'W/"{row_id}-{before}"'}

    changed['updated_at'] = db.now_iso()
    if 'version' in spec:
        changed['version'] = before + 1
    _update_raw(conn, table, row_id, changed)
    row = _fetch(conn, table, row_id)           # 取回放在 commit 之前
    _audit_write(conn, ctx, audit, method=method, path=path,
                 table=table, row_id=row_id, before=before,
                 after=row.get('version'), result='updated')
    data = dict(row)
    _idem_store(conn, ctx, idempotency, 200, data)
    conn.commit()
    return {'status': 200, 'data': data, 'replay': False, 'unchanged': False,
            'etag': f'W/"{row_id}-{row.get("version") or before + 1}"'}


#: 供 integration 在 scope 闸门早退时记账（业务写尚未发生，单独记账提交）。
audit_denied = _audit_denied


def audit_event(conn, *, owner_id, scope, event, message, detail):
    """记一条令牌生命周期审计；**不提交**，由调用方与业务写同事务提交。"""
    return _audit_insert(conn, owner_id=owner_id, scope=scope, event=event,
                         message=message, detail=detail)


# --------------------------------------------------------------------------
# 团队生命周期
# --------------------------------------------------------------------------

def create_team_with_owner(conn, ctx, *, name, description=None, mission=None,
                           join_code=None, owner_email=None, owner_name=None):
    """建团队 + 把创建者登记为 owner，两步在同一事务里。

    必须是事务：只有 team_members 里存在成员行，该团队的数据才对创建者可见
    （可见性依赖 is_team_member）。若第二步失败而第一步留下了一条团队记录，
    用户会得到一个谁也进不去的"孤儿空间"——云版本正是这么失败过的。

    另外：邮箱/显示名取不到时写 null 而不是占位文案。历史上库里的
    "已登录" 就是这么被写进去的，之后成员列表、责任人下拉框全都显示这串脏数据。
    """
    name = (name or '').strip()
    if not name:
        raise StoreError('TEAM_NAME_REQUIRED', '请输入团队名称')

    email = (owner_email or '').strip() or None
    display = (owner_name or '').strip() or (email.split('@')[0] if email else None)
    now = db.now_iso()

    try:
        team_id = _insert_raw(conn, 'teams', {
            'owner_id': ctx.uid, 'owner_name': display, 'name': name,
            'description': description or None, 'mission': mission or None,
            'join_code': join_code or None, 'created_at': now, 'updated_at': now,
        })
        _insert_raw(conn, 'team_members', {
            'team_id': team_id, 'user_id': ctx.uid, 'email': email,
            'display_name': display, 'role': 'owner', 'status': 'active',
            'capacity': 6, 'created_at': now, 'updated_at': now,
        })
    except sqlite3.IntegrityError as err:
        conn.rollback()
        if 'join_code' in str(err):
            raise StoreError('JOIN_CODE_TAKEN', '该邀请码已被占用，请重新生成', 409) from err
        raise StoreError('TEAM_CREATE_FAILED', f'团队创建失败：{err}', 500) from err
    except Exception:
        conn.rollback()
        raise
    # 取回放在提交之前：提交之后不再做任何可能抛异常的事（理由见 insert_row）
    team = _fetch(conn, 'teams', team_id)
    conn.commit()
    return team


def join_team_by_code(conn, ctx, code, *, user_email=None, user_name=None):
    """凭邀请码加入团队，返回团队 id。

    为什么必须由服务端函数完成（云版本用 SECURITY DEFINER 的 join_team_by_code）：
    成员行的插入权限只对"该团队的管理员"开放，而凭码加入的人此刻还不是成员 ——
    这是唯一一条合法的自助加入路径，不能让客户端自己去插 team_members。
    """
    code = (code or '').strip().upper()
    if not code:
        raise StoreError('INVALID_JOIN_CODE', '请输入邀请码', 400)

    team = conn.execute(
        'SELECT * FROM teams WHERE upper(join_code) = ? AND archived = 0', (code,)
    ).fetchone()
    if team is None:
        raise StoreError('INVALID_JOIN_CODE', '邀请码无效，请向团队所有者确认', 404)

    team_id = int(team['id'])
    existing = conn.execute(
        'SELECT id, status FROM team_members WHERE team_id = ? AND user_id = ?',
        (team_id, ctx.uid),
    ).fetchone()

    if existing is not None:
        status = (existing['status'] or 'active').strip().lower()
        if status != 'active':
            # 被所有者停用后凭公开的邀请码自行恢复，会架空"停用即失去访问权"
            raise StoreError(
                'MEMBER_DISABLED',
                '你已被移出该团队，请联系团队所有者重新启用',
                403,
            )
        return team_id                  # 重复加入按幂等处理，直接切过去

    now = db.now_iso()
    _insert_raw(conn, 'team_members', {
        'team_id': team_id, 'user_id': ctx.uid,
        'email': (user_email or '').strip() or None,
        'display_name': (user_name or '').strip() or None,
        'role': 'member', 'status': 'active', 'capacity': 6,
        'created_at': now, 'updated_at': now,
    })
    conn.commit()
    return team_id


# --------------------------------------------------------------------------
# 账号管理：三级建号（唯一受控写入路径）
# --------------------------------------------------------------------------
#
# 为什么这些函数必须存在（不能散在 httpd / CLI 里）
# -------------------------------------------------
# 1. `team_members` 的插入策略是 `is_admin(tid) or is_owner(tid)`（policy.py），
#    它只表达"团队管理员"这一层。系统管理员若不是该团队成员会被**误拒**，
#    若是成员又会被当成普通管理员**误放** —— 所以不能用 `policy.can_insert` 近似。
# 2. 更根本的是：`AuthzContext` 里没有"系统管理员"这个概念，也不应该有。
#    给它加一个实例级字段，会让实例级概念渗透进每一个需要与云版 RLS 逐条对照的
#    行级谓词。系统管理员这一层只活在下面这几个受控函数里。
# 3. 建号涉及"邮箱唯一性 / 角色枚举 / 团队存在性 / 审计同事务 / 单次提交"五件事，
#    分散到 handler 就会各写一遍并漂移，还会把重放安全性再赌一次。

#: 可经接口授予的团队角色。`owner` 一律拒绝：转让所有权需要二次确认 + 审计，
#: 本期不做，所以先把这条口子关死（与 policy 对"自提 owner"的封堵同源）。
#:
#: 两个调用者能授的角色**不同**（裁决：系统管理员只能建 admin，团队管理员只能建 member）：
#:   · 系统管理员（sysadmin）→ 只允许 'admin'
#:   · 团队管理员（owner/admin）→ 只允许 'member'
#: 因此不要用一份共享常量去校验两边；分别在各自分支里判定（见 create_account）。
ACCOUNT_ROLES = ('admin', 'member')
SYSADMIN_CREATABLE_ROLES = ('admin',)


def _account_public(row) -> dict:
    """账号的对外形状。**绝不包含 password_hash**。"""
    return {
        'id': row['id'],
        'email': row['email'],
        'name': row['name'] or '',
        'isSystemAdmin': bool(row['is_system_admin']),
        'mustChangePassword': bool(row['must_change_password']),
    }


def _normalize_account_inputs(email, name, role, *, default_role):
    """归一化入参：去空白、name 缺省取邮箱前缀并截断 64、role 缺省取调用者默认值。

    `default_role` 由调用方按身份传入（系统管理员→'admin'，团队管理员→'member'），
    **不在这里写死 'member'**：缺省值随调用者不同而不同，
    写死会导致"系统管理员漏传 role 时被默认成 member"（与裁决相反）。
    """
    email = str(email or '').strip()
    display = str(name or '').strip()
    if not display:
        display = email.split('@')[0] if email else ''
    display = display[:64]

    if role is None:
        role = default_role
    else:
        role = str(role).strip().lower() or default_role
    return email, display, role


def _team_row(conn, team_id):
    """取团队（含归档标记）。team_id 不可转成整数时返回 None。"""
    try:
        tid = int(team_id)
    except (TypeError, ValueError):
        return None
    return conn.execute('SELECT id, name, archived FROM teams WHERE id = ?', (tid,)).fetchone()


def _insert_account_audit(conn, *, actor_uid, target_user_id, email, team_id, role,
                          by_system_admin, via):
    """建号审计：与建号**同一事务**、随同一次 commit。

    不能调 store.write_app_log() —— 它内部自己 commit，会变成第二次提交，
    破坏"提交只发生一次"的纪律（见 db.TrackedConnection）。
    **绝不含明文口令与散列。**
    """
    _insert_raw(conn, 'app_logs', {
        'owner_id': actor_uid or None,
        'level': 'info',
        'scope': 'account',
        'event': 'account.created',
        'message': f'创建账号 {email} → team={team_id} role={role}'[:1000],
        'detail': db.encode_value('json', {
            'targetUserId': target_user_id,
            'teamId': team_id,
            'role': role,
            'bySystemAdmin': bool(by_system_admin),
            'via': via,
        }),
        'created_at': db.now_iso(),
    })


def create_account(conn, actor, *, email, name=None, team_id=None, role=None,
                   password_hash, is_system_admin=False, must_change_password=True):
    """三级建号的唯一写入路径。返回 `{'user': {...}, 'teamId': int|None, 'role': str|None}`。

    `actor` —— 由调用方从**会话 / CLI** 组装，**绝不来自请求体**：
        {'uid', 'is_system_admin', 'admin_team_ids', 'via'}

    `password_hash` —— 已散列的口令。**明文永不进入 store**
    （明文只存在于 httpd 的局部变量与响应体）。

    `is_system_admin` —— **仅 CLI 传 True**；HTTP 路径恒定不传。
    本项目里创建系统管理员的入口只有 CLI。

    校验顺序是安全设计的一部分：**先授权 → 再参数 → 再唯一性**。
    反过来会把"某邮箱是否已注册"暴露给一个本来无权建号的人（账号枚举）。
    """
    actor = actor or {}
    caller_is_sysadmin = bool(actor.get('is_system_admin'))
    admin_team_ids = tuple(int(t) for t in (actor.get('admin_team_ids') or ()))
    via = actor.get('via') or 'http'

    # 显式 role 的判定必须在归一化之前：归一化会补默认值，之后就分不清
    # "调用方传了 role"还是"没传"了。而"未指定 teamId 却传了 role"是要拒的。
    role_explicitly_given = role is not None

    # 缺省 role 随调用者身份：系统管理员默认 admin，团队管理员默认 member。
    # 不能用同一个字面量再分支覆盖（以后改默认值时容易误伤另一条路）。
    default_role = 'admin' if caller_is_sysadmin else 'member'
    email, display, role = _normalize_account_inputs(email, name, role, default_role=default_role)

    # ── ① 授权闸：有无建号权 ────────────────────────────────────────────
    if not caller_is_sysadmin and not admin_team_ids:
        # 零系统管理员的实例还处在"未引导"状态，这时最该告诉对方的不是
        # "你没权限"，而是"这个实例还没有管理员，怎么引导"。
        # 只在**失败路径**上多一次只读查询：成功建号不付这个成本。
        # 注意这不是全局闸门：零管理员时既有用户的业务功能、团队管理员建成员
        # 都必须照常工作（否则一次"忘了引导"会把整站锁死）。
        has_admin = conn.execute(
            'SELECT 1 FROM users WHERE is_system_admin = 1 LIMIT 1').fetchone()
        if has_admin is None:
            raise StoreError(
                'NO_SYSTEM_ADMIN',
                '本实例还没有系统管理员。请先在服务器执行 '
                'python server.py --create-admin <邮箱> 完成引导', 403)
        raise StoreError('FORBIDDEN', '只有系统管理员或团队管理员可以创建账号', 403)

    target_team = None
    if caller_is_sysadmin:
        # 系统管理员身份**优先生效**，不再叠加团队约束（矩阵 #18）
        # 裁决：系统管理员只能创建"团队管理员(admin)"，不能创建 member ——
        # 给 member 会架空委派链，且让实例管理员变成"万能加号入口"。
        if role not in SYSADMIN_CREATABLE_ROLES:
            raise StoreError('ROLE_NOT_ALLOWED',
                             '系统管理员只能创建团队管理员（admin）', 400)
        if team_id is not None:
            target_team = _team_row(conn, team_id)
            if target_team is None or int(target_team['archived'] or 0) != 0:
                raise StoreError('TEAM_NOT_FOUND', '团队不存在或已归档', 404)
        elif role_explicitly_given:
            # 未指定团队却显式传了 role：没有团队就没有团队角色，传了就是错的。
            raise StoreError('ROLE_NOT_ALLOWED',
                             '未指定团队时不能授予团队角色', 400)
    else:
        if team_id is None:
            raise StoreError('TEAM_REQUIRED', '请先选择要加入的团队', 400)
        try:
            requested_team_id = int(team_id)
        except (TypeError, ValueError):
            raise StoreError('TEAM_REQUIRED', '请先选择要加入的团队', 400)
        # 先判越权、再判 role：否则会向越权调用者透露"role 该填什么"
        if requested_team_id not in admin_team_ids:
            raise StoreError('NOT_TEAM_ADMIN', '你不是该团队的管理员', 403)
        if role != 'member':
            raise StoreError('ROLE_NOT_ALLOWED',
                             '你无权授予该角色：团队管理员只能创建成员', 403)
        target_team = _team_row(conn, requested_team_id)
        if target_team is None or int(target_team['archived'] or 0) != 0:
            raise StoreError('TEAM_NOT_FOUND', '团队不存在或已归档', 404)

    # ── ② 参数闸 ───────────────────────────────────────────────────────
    problem = security.validate_email(email)
    if problem:
        raise StoreError('INVALID_EMAIL', problem, 400)

    # ── ③ 唯一性 ───────────────────────────────────────────────────────
    if conn.execute('SELECT 1 FROM users WHERE lower(email) = lower(?)',
                    (email,)).fetchone():
        # 文案必须指向**正确的责任人**：管理员没有"把人加入团队"的能力
        # （join_team_by_code 用的是当前会话身份），邀请码只能由对方本人使用。
        raise StoreError(
            'EMAIL_EXISTS',
            '该邮箱已有账号。请把本团队邀请码发给对方，由对方登录后自行加入；'
            '若对方无法登录，请联系运维重置密码。', 409)

    # ── ④ 写入：users + 可选 team_members + 审计，同一事务、一次提交 ────
    now = db.now_iso()
    user_id = security.new_user_id()
    target_team_id = int(target_team['id']) if target_team is not None else None
    # 双重保险：即使有人给 HTTP 路径传了 is_system_admin=True，
    # via 不是 'cli' 也会被抹成 0。系统管理员的唯一创建入口是 CLI。
    flag_sysadmin = 1 if (is_system_admin and via == 'cli') else 0

    try:
        conn.execute(
            'INSERT INTO users (id, email, name, password_hash, created_at, updated_at, '
            'is_system_admin, must_change_password) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (user_id, email, display, password_hash, now, now, flag_sysadmin,
             1 if must_change_password else 0),
        )
        if target_team_id is not None:
            _insert_raw(conn, 'team_members', {
                'team_id': target_team_id, 'user_id': user_id, 'email': email,
                'display_name': display, 'role': role, 'status': 'active',
                'capacity': 6, 'created_at': now, 'updated_at': now,
            })
        _insert_account_audit(conn, actor_uid=actor.get('uid'), target_user_id=user_id,
                             email=email, team_id=target_team_id, role=role,
                             by_system_admin=caller_is_sysadmin, via=via)
    except sqlite3.IntegrityError as err:
        conn.rollback()
        if 'email' in str(err).lower():
            # 步骤 ③ 与 INSERT 之间有并发窗口，这条是兜底（文案与 ③ 保持一致）
            raise StoreError(
                'EMAIL_EXISTS',
                '该邮箱已有账号。请把本团队邀请码发给对方，由对方登录后自行加入；'
                '若对方无法登录，请联系运维重置密码。', 409) from err
        raise StoreError('ACCOUNT_CREATE_FAILED', '账号创建失败，请重试', 500) from err
    except Exception:
        conn.rollback()
        raise

    # 取回在**提交之前**：提交之后只剩 return，不给重放留窗口
    row = conn.execute(
        'SELECT id, email, name, is_system_admin, must_change_password '
        'FROM users WHERE id = ?', (user_id,)).fetchone()
    user = _account_public(row)
    conn.commit()
    return {'user': user, 'teamId': target_team_id,
            'role': role if target_team_id is not None else None}


def grant_system_admin(conn, email):
    """把**已存在**的账号授予系统管理员。账号不存在返回 None（**不建号**）。

    不建号是刻意的：这条命令的用途是"给一个已知存在的人授权"。
    若它顺手建号，一次邮箱打错就会静默创建一个带随机口令的管理员账号。
    """
    email = str(email or '').strip()
    problem = security.validate_email(email)
    if problem:
        raise StoreError('INVALID_EMAIL', problem, 400)

    row = conn.execute(
        'SELECT id, is_system_admin, must_change_password FROM users WHERE lower(email) = lower(?)',
        (email,)
    ).fetchone()
    if row is None:
        return None

    is_admin = int(row['is_system_admin'] or 0)
    must_change = int(row['must_change_password'] or 0)
    changed = False

    # 三分支：提权是"口令这一环经运维之手"的操作，必须同时强制改密。
    # 判据不是"有没有生成新口令"，而是"口令是否由他人设定/可能已被共享" ——
    # 一个 must_change_password=0 的老账号被提权后若仍免改密，就会留下
    # "永久免改密 + 口令可能已被分发"的状态，正是本次变更要消灭的。
    if not is_admin and must_change:
        # 非管理员 + 已要求改密 → 只提权
        conn.execute('UPDATE users SET is_system_admin = 1, updated_at = ? WHERE id = ?',
                     (db.now_iso(), row['id']))
        changed = True
    elif not is_admin and not must_change:
        # 非管理员 + 免改密 → 提权 + 强制改密
        conn.execute('UPDATE users SET is_system_admin = 1, must_change_password = 1, '
                     'updated_at = ? WHERE id = ?', (db.now_iso(), row['id']))
        changed = True
    elif is_admin and not must_change:
        # 已是管理员但免改密 → 只补 flag（最容易漏的一支：用户以为"没变化"，其实状态变了）
        conn.execute('UPDATE users SET must_change_password = 1, updated_at = ? WHERE id = ?',
                     (db.now_iso(), row['id']))
        changed = True
    # else: 已是管理员且已要求改密 → 真的无改动

    fresh = conn.execute(
        'SELECT id, email, name, is_system_admin, must_change_password '
        'FROM users WHERE id = ?', (row['id'],)).fetchone()
    user = _account_public(fresh)
    conn.commit()
    return {'user': user, 'changed': changed}


def reset_password(conn, email, password_hash, *, create_if_missing=True,
                   must_change_password=True):
    """设定口令（`--set-password`）。返回 `{'created': bool, 'user': {...}}`。

    收敛这份逻辑的原因：`server.py` 原来自己拼 `INSERT INTO users`，
    与建号接口形成两处独立实现 —— 散列参数、`must_change_password` 置位、
    踢会话这三件事只要有一处漏改，就会出现"CLI 开的账号不强制改密"这类裂缝。

    口令由**他人**设定（运维 / 管理员），所以默认置 `must_change_password=1`：
    用户应当自己再改一次。重置分支同时踢掉该账号的全部会话（既有行为）。
    """
    email = str(email or '').strip()
    problem = security.validate_email(email)
    if problem:
        raise StoreError('INVALID_EMAIL', problem, 400)

    row = conn.execute('SELECT id FROM users WHERE lower(email) = lower(?)',
                       (email,)).fetchone()
    now = db.now_iso()
    created = row is None
    if created:
        if not create_if_missing:
            return None
        try:
            conn.execute(
                'INSERT INTO users (id, email, name, password_hash, created_at, updated_at, '
                'is_system_admin, must_change_password) VALUES (?, ?, ?, ?, ?, ?, 0, ?)',
                (security.new_user_id(), email, email.split('@')[0], password_hash,
                 now, now, 1 if must_change_password else 0),
            )
        except sqlite3.IntegrityError as err:
            conn.rollback()
            raise StoreError('ACCOUNT_CREATE_FAILED', '账号创建失败，请重试', 500) from err
        except Exception:
            conn.rollback()
            raise
    else:
        conn.execute(
            'UPDATE users SET password_hash = ?, must_change_password = ?, updated_at = ? '
            'WHERE id = ?',
            (password_hash, 1 if must_change_password else 0, now, row['id']))
        conn.execute('DELETE FROM sessions WHERE user_id = ?', (row['id'],))

    fresh = conn.execute(
        'SELECT id, email, name, is_system_admin, must_change_password '
        'FROM users WHERE lower(email) = lower(?)', (email,)).fetchone()
    user = _account_public(fresh)
    conn.commit()
    return {'created': created, 'user': user}


#: 账号列表的上限。本应用体量是"一个小团队"，不做分页；
#: 超出部分不返回 —— 这是**已知限制**，写在这里而不是留给人猜。
ACCOUNT_LIST_LIMIT = 200


def list_accounts(conn, actor, team_id=None):
    """按调用者身份收敛范围的账号列表。返回 `{'scope', 'accounts': [...]}`。

    **绝不返回** `password_hash`、`sessions` 任何字段、`settings` 任何字段
    （参照 EXPORT_USER_COLUMNS 的取舍）。
    """
    actor = actor or {}
    caller_is_sysadmin = bool(actor.get('is_system_admin'))
    admin_team_ids = tuple(int(t) for t in (actor.get('admin_team_ids') or ()))

    if not caller_is_sysadmin:
        if not admin_team_ids:
            # 成员 / 非成员：没有实例级视野，也没有团队管理权
            raise StoreError('FORBIDDEN', '只有系统管理员或团队管理员可以查看账号列表', 403)
        if team_id is None:
            # 团队管理员没有"全量"这个选项：让他省略就等于允许他试探
            raise StoreError('TEAM_REQUIRED', '请先选择要查看的团队', 400)

    accounts = {}
    if team_id is None:
        rows = conn.execute(
            'SELECT id, email, name, is_system_admin, must_change_password, created_at '
            'FROM users ORDER BY created_at DESC, id DESC LIMIT ?',
            (ACCOUNT_LIST_LIMIT,)).fetchall()
        for row in rows:
            account = _account_public(row)
            account['createdAt'] = row['created_at']
            account['teams'] = []
            accounts[row['id']] = account
        if accounts:
            marks = ', '.join('?' * len(accounts))
            member_rows = conn.execute(
                f'SELECT user_id, team_id, role, status FROM team_members '
                f'WHERE user_id IN ({marks}) ORDER BY team_id', tuple(accounts.keys())).fetchall()
            for member_row in member_rows:
                accounts[member_row['user_id']]['teams'].append({
                    'teamId': int(member_row['team_id']),
                    'role': member_row['role'],
                    'status': member_row['status'],
                })
        scope = 'instance'
    else:
        try:
            requested_team_id = int(team_id)
        except (TypeError, ValueError):
            raise StoreError('TEAM_NOT_FOUND', '团队不存在或已归档', 404)
        if not caller_is_sysadmin and requested_team_id not in admin_team_ids:
            raise StoreError('NOT_TEAM_ADMIN', '你不是该团队的管理员', 403)
        team = _team_row(conn, requested_team_id)
        if team is None:
            raise StoreError('TEAM_NOT_FOUND', '团队不存在或已归档', 404)
        rows = conn.execute(
            'SELECT u.id, u.email, u.name, u.is_system_admin, u.must_change_password, '
            'u.created_at, m.role, m.status FROM team_members m '
            'JOIN users u ON u.id = m.user_id WHERE m.team_id = ? '
            'ORDER BY u.created_at DESC, u.id DESC LIMIT ?',
            (requested_team_id, ACCOUNT_LIST_LIMIT)).fetchall()
        for row in rows:
            account = _account_public(row)
            account['createdAt'] = row['created_at']
            # 团队级查询只回该团队那一条成员关系
            account['teams'] = [{'teamId': requested_team_id, 'role': row['role'],
                                 'status': row['status']}]
            accounts[row['id']] = account
        scope = 'team'

    return {'scope': scope, 'accounts': list(accounts.values())}


# --------------------------------------------------------------------------
# 复盘
# --------------------------------------------------------------------------

def save_review(conn, ctx, payload, existing_id=None):
    """同一 (账号, 团队, 类型, 周期) 只保留一条，重复提交即更新。

    云版本的实现是"先查后写"：表上是**表达式唯一索引**
    (owner_id, COALESCE(team_id,0), kind, period_key)，PostgREST 的 on_conflict
    只接受列名列表，命中不了表达式索引，服务端会以 42P10 拒掉整条请求。
    本地版没有这个限制，但仍然保留"先查后写"的语义，并且允许调用方不传 existing_id ——
    前端 `finishReview` 依赖"重复复盘不产生第二行"这个可观察结果。
    """
    payload = dict(payload or {})
    team_id = payload.get('team_id')
    kind = payload.get('kind') or 'weekly'
    period_key = payload.get('period_key') or ''

    target_id = existing_id
    if not target_id:
        found = conn.execute(
            'SELECT id FROM reviews WHERE owner_id = ? AND COALESCE(team_id, 0) = ? '
            'AND kind = ? AND period_key = ?',
            (ctx.uid, team_id or 0, kind, period_key),
        ).fetchone()
        target_id = found['id'] if found else None

    if target_id:
        return update_row(conn, ctx, 'reviews', target_id, payload)
    return insert_row(conn, ctx, 'reviews', payload)


# --------------------------------------------------------------------------
# 运行日志
# --------------------------------------------------------------------------

def write_app_log(conn, ctx, payload):
    """写入一条运行日志。字段一律截断，避免前端把异常堆栈整段灌进库。"""
    detail = payload.get('detail')
    values = {
        'owner_id': ctx.uid or None,
        'level': str(payload.get('level') or 'info')[:16],
        'scope': str(payload.get('scope') or 'app')[:64],
        'event': str(payload.get('event') or '')[:120],
        'message': str(payload.get('message') or '')[:1000],
        'detail': db.encode_value('json', detail if isinstance(detail, dict) else {}),
        'page_url': (str(payload.get('page_url'))[:300] if payload.get('page_url') else None),
        'user_agent': (str(payload.get('user_agent'))[:300] if payload.get('user_agent') else None),
        'created_at': db.now_iso(),
    }
    row_id = _insert_raw(conn, 'app_logs', values)
    conn.commit()
    return [{'id': row_id}]


# --------------------------------------------------------------------------
# 键值设置（LLM 凭据等）
# --------------------------------------------------------------------------

def get_setting(conn, key, default=None):
    row = conn.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    return row['value'] if row else default


def set_setting(conn, key, value):
    apply_settings(conn, {key: '' if value is None else str(value)})


def delete_setting(conn, key):
    apply_settings(conn, {key: None})


def apply_settings(conn, updates):
    """一次事务写完多条设置，只提交一次。

    与"逐条 set_setting"的差别只在提交时机：把要改的几项放进同一个事务、
    在最后统一提交，"提交"这件事在整个请求里就只发生一次 ——
    提交点越少越靠后，httpd 那边"已提交但后续失败、不可重放"的窗口就越小
    （见 db.TrackedConnection）。value 为 None 表示删除该键。
    """
    now = db.now_iso()
    for key, value in (updates or {}).items():
        if value is None:
            conn.execute('DELETE FROM settings WHERE key = ?', (key,))
            continue
        conn.execute(
            'INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) '
            'ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at',
            (key, str(value), now),
        )
    conn.commit()


# --------------------------------------------------------------------------
# 整库备份 / 还原（供 CLI 使用）
# --------------------------------------------------------------------------

BACKUP_TABLES = tuple(COLUMNS.keys()) + ('users', 'settings')

# HTTP 导出里 users 只给这些非敏感列。
# 口令散列、以及任何以后加进来的凭据类字段，都绝不能出现在这个列表里。
EXPORT_USER_COLUMNS = ('id', 'email', 'name', 'created_at', 'updated_at')


def export_all(conn):
    """整库导出为纯 JSON。

    备份本可以直接拷 data/*.db 文件，导出成 JSON 的意义是：
    跨机器/跨版本迁移时不必依赖 SQLite 二进制格式，也便于人工检查内容。
    **注意：其中含口令散列与会话，属于敏感文件，不要放进版本库或公开目录。**

    会话表不导出（令牌是短暂凭据，迁移后重新登录即可）。

    这条路径**只给 CLI 的 --export 用**。HTTP 侧用 export_visible(),
    两者不能合并：这个函数是"整库"，而任何登录用户都能调 HTTP 那个。
    """
    payload = {'format': 'gtd4teams-backup', 'version': 1, 'exported_at': db.now_iso(), 'tables': {}}
    for table in BACKUP_TABLES:
        rows = conn.execute(f'SELECT * FROM {table}').fetchall()
        payload['tables'][table] = [dict(r) for r in rows]
    return payload


def export_visible(conn, ctx, user):
    """导出"当前账号可见"的数据，供 `GET /api/export` 使用。

    与 export_all() 的分工（刻意分成两个函数，不是重复实现）：
      · export_all()     → CLI `--export`，整库逻辑备份，灾备依赖它，行为不变；
      · export_visible() → HTTP 接口，逐表过 policy 的 select 策略。

    为什么必须分开：修复前 HTTP 侧直接调用了 export_all()，于是任何刚注册的账号
    都能一次拿到 `users.password_hash`、`settings.llm_api_key` 明文，
    以及**别人的**私有工作项。接口导出是本机使用者的自助备份，
    它只需要"我看得见的那部分"，从来没有过"整库"的正当用途。

    三处刻意的收口：
      1. 业务表逐表走 select_table()（复用 policy，不另写一套过滤）；
      2. `settings` 整表不出现 —— 里面是 LLM 凭据；
      3. `users` 最多只有当前账号自己那一行，且只取 EXPORT_USER_COLUMNS。
    """
    payload = {'format': 'gtd4teams-backup', 'version': 1,
               'exported_at': db.now_iso(), 'tables': {}}

    for table in WRITABLE_TABLES:
        payload['tables'][table] = select_table(conn, ctx, table)

    owner_row = None
    if user and user.get('id'):
        row = conn.execute(
            'SELECT {} FROM users WHERE id = ?'.format(', '.join(EXPORT_USER_COLUMNS)),
            (user['id'],),
        ).fetchone()
        owner_row = dict(row) if row else None
    payload['tables']['users'] = [owner_row] if owner_row else []
    return payload


def import_all(conn, payload):
    """从导出的 JSON 还原。按主键覆盖（INSERT OR REPLACE），不做增量合并。

    **刻意不做列裁剪**（曾经做过，已撤销）：裁剪会让"新备份导回新库"把
    `is_system_admin` / `must_change_password` 静默抹成 0 —— 灾备还原后
    实例里没人能建团队管理员，恰好在最需要保真的场景失真。
    而且备份里本来就有 `password_hash`：能改写备份的人早已能伪造登录态，
    多一个 `is_system_admin` 并不是这堆秘密里权限最大的东西。

    旧备份导入新库本来就不会报错（旧备份不含新列 → 缺的列走 `DEFAULT 0`）。
    真正会炸的是**反向**：新代备份导入旧结构库，报 `no such column`。
    那是"运维动作顺序"问题，正确处置是把结构升到最新再导入 ——
    所以这里只做一件事：把这类结构不匹配翻译成**可执行**的错误文案，
    否则运维看到的是兜底 500 + 一个 requestId，根本想不到是"备份比库新"。
    """
    tables = (payload or {}).get('tables')
    if not isinstance(tables, dict):
        raise StoreError('BAD_BACKUP', '备份文件格式不正确：缺少 tables 字段')
    counts = {}
    try:
        for table, rows in tables.items():
            if table not in BACKUP_TABLES or not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict) or not row:
                    continue
                cols = [c for c in row.keys() if c in COLUMNS.get(table, {}) or
                        (table in ('users', 'settings'))]
                if not cols:
                    continue
                marks = ', '.join('?' * len(cols))
                conn.execute(
                    f'INSERT OR REPLACE INTO {table} ({", ".join(cols)}) VALUES ({marks})',
                    [row[c] for c in cols],
                )
            counts[table] = len(rows)
    except sqlite3.OperationalError as err:
        conn.rollback()
        message = str(err).lower()
        # 结构不匹配的三种实际措辞（都要覆盖，漏一个就等于没翻译）：
        #   · `table users has no column named is_system_admin`（INSERT/UPDATE 时的真实措辞）
        #   · `no such column: is_system_admin`（SELECT 侧）
        #   · `no such table: xxx`
        if ('has no column named' in message or 'no such column' in message
                or 'no such table' in message):
            raise StoreError(
                'IMPORT_SCHEMA_MISMATCH',
                '备份文件的结构与当前库不一致：请先运行 --check 让数据库升级到最新结构，再导入',
                400) from err
        raise                            # 其它 OperationalError 原样上抛，不吞
    conn.commit()
    return counts


def load_backup_file(path):
    with open(path, 'r', encoding='utf-8') as handle:
        return json.load(handle)
