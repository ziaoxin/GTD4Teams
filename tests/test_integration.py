"""集成域验收断言（TC-INT-01 ~ TC-INT-14）—— 先写红灯，再写实现。

判据来源（逐字读取，不得凭记忆改判据）：
  docs/process/04_TDS_技术设计说明书-update 11.18.md
    §16.11.3（TC-INT-01~14 全文）、§16.3（令牌）、§16.4（路由/Origin）、
    §16.5（policy 复用）、§16.6（ETag/幂等/未变更）、§16.7（审计）、§16.10（迁移）。

为什么要单独一个文件而不是加进 test_api.py：
  本项目踩过"共享状态导致假失败"的坑。本文件自己起独立的临时服务 + 临时库，
  每个测试方法独立 setUp/tearDown，与 test_api.py 的 156 项零共享。

红灯是预期结果：
  TDS §16.11.1 明确集成域当前"代码 0 行、测试 0 条"。实现未落地时本文件应全部
  报红；每条用例的 docstring 首行带 TC 编号与判据，unittest -v 会把它打印出来，
  因此"红在哪里、缺什么"是可读的。严禁 skip / 放宽断言来换绿。

范围与跳过条件：
  TC-INT-01~14 全部是 REST / DB / 访问日志层断言，Python 标准库即可覆盖，
  无浏览器 DOM 交互，故不建 .smoke/integration-test.mjs。
  MCP（REQ-050~053，B2）不在 §16.11.3 的 14 条内，本红灯集不覆盖，无 skip 项。

零依赖 / 离线：只用 unittest + http.client + sqlite3 + tempfile + threading。
setUp 里显式清空 LLM 配置，杜绝任何外网调用。
行尾：LF。
"""

import http.client
import json
import shutil
import socket
import sqlite3
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyapp import db, security                       # noqa: E402
from pyapp.config import Config                      # noqa: E402
from pyapp.httpd import free_port, make_server, make_integration_servers  # noqa: E402

PASSWORD = 'test-pass-1234'
ADMIN_EMAIL = 'int-admin@gtd.local'
A_EMAIL = 'int-a@gtd.local'
B_EMAIL = 'int-b@gtd.local'
GATE_EMAIL = 'int-gate@gtd.local'


# ---------------------------------------------------------------------------
# 最小 HTTP 客户端：带 Cookie 保持 + 任意请求头（Bearer / Origin / If-Match / 幂等键）
# ---------------------------------------------------------------------------

class Resp:
    def __init__(self, status, payload, raw, headers):
        self.status = status
        self.payload = payload
        self.raw = raw
        self.headers = {str(k).lower(): v for k, v in (headers or {}).items()}

    @property
    def code(self):
        return ((self.payload or {}).get('error') or {}).get('code')

    @property
    def rows(self):
        return (self.payload or {}).get('rows')

    def header(self, name):
        return self.headers.get(str(name).lower())


class Http:
    def __init__(self, port, cookie=None, api_port=None):
        self.port = port            # 主监听器：/api/*（含令牌签发端点、登录、/api/rows）
        self.api_port = api_port    # 集成监听器：/v1/*（TDS §16.2/§16.4 独立第二监听器）
        self.cookie = cookie

    def request(self, method, path, body=None, *, headers=None, raw_body=None):
        # /v1/* 只在独立集成监听器上；主入口 ROUTES 不含 /v1，打过去必然 404。
        target = self.api_port if (self.api_port and path.startswith('/v1/')) else self.port
        conn = http.client.HTTPConnection('127.0.0.1', target, timeout=20)
        sent = dict(headers or {})
        payload = None
        if raw_body is not None:
            payload = raw_body
        elif body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode('utf-8')
            sent.setdefault('Content-Type', 'application/json')
        if self.cookie and 'Cookie' not in sent:
            sent['Cookie'] = self.cookie
        conn.request(method, path, body=payload, headers=sent)
        resp = conn.getresponse()
        data = resp.read()
        status, hdrs = resp.status, dict(resp.getheaders())
        set_cookie = hdrs.get('Set-Cookie')
        conn.close()
        if set_cookie:
            kv = set_cookie.split(';')[0]
            self.cookie = '' if 'gtd_session=;' in kv + ';' else kv
        parsed = None
        if 'application/json' in (hdrs.get('Content-Type') or '') and data:
            try:
                parsed = json.loads(data.decode('utf-8'))
            except (ValueError, UnicodeDecodeError):
                parsed = None
        return Resp(status, parsed, data, hdrs)

    def get(self, path, **kw):
        return self.request('GET', path, **kw)

    def post(self, path, body=None, **kw):
        return self.request('POST', path, body if body is not None else {}, **kw)

    def patch(self, path, body=None, **kw):
        return self.request('PATCH', path, body if body is not None else {}, **kw)

    def insert(self, table, payload):
        return self.post('/api/rows', {'table': table, 'op': 'insert', 'payload': payload})


def bearer(token, extra=None):
    h = {'Authorization': 'Bearer ' + token}
    h.update(extra or {})
    return h


def strip_error_code(payload):
    """去掉 error.code 后的响应体——用于 TC-INT-01 的"除 code 外无区分信息"。"""
    p = json.loads(json.dumps(payload)) if payload is not None else None
    if isinstance(p, dict) and isinstance(p.get('error'), dict):
        p['error'].pop('code', None)
    return p


class IntegrationAcceptanceTest(unittest.TestCase):
    """14 条验收断言；每个测试方法独立起服务 + 独立临时库。"""

    # ---- 生命周期 --------------------------------------------------------

    def setUp(self):
        security.reset_limits()
        self.tmp = tempfile.mkdtemp(prefix='gtd4teams-int-')
        self.cert_dir = tempfile.mkdtemp(prefix='gtd4teams-int-cert-')
        self.port = free_port()
        # 显式开启集成域：把 /v1/* 挂到独立的第二监听器（默认端口 8790 在本机
        # 多半空闲，但用 free_port 规避与其他用例/进程争端口）。MCP 不在 14 条内，
        # 保持关闭，只起 API 监听器。
        self.api_port = free_port()
        self.config = Config(host='127.0.0.1', port=self.port, data_dir=self.tmp,
                             admin_emails=[ADMIN_EMAIL], cert_dir=self.cert_dir,
                             api_enabled=True, api_port=self.api_port, mcp_enabled=False)
        # 杜绝外网调用：本项目踩过 Linux 上 LLM 挂超时的坑
        self.config.llm_api_key = ''
        self.server, self.app = make_server(self.config)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.integration_servers = make_integration_servers(self.config, self.app)
        for s in self.integration_servers:
            threading.Thread(target=s.serve_forever, daemon=True).start()
        self.http = Http(self.port, api_port=self.api_port)
        self.db_path = str(Path(self.tmp) / 'gtd4teams.db')
        self._admin = None
        self._admin_uid = None

    def tearDown(self):
        try:
            for s in getattr(self, 'integration_servers', []):
                s.shutdown()
                s.server_close()
            self.server.shutdown()
            self.server.server_close()
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)
            shutil.rmtree(self.cert_dir, ignore_errors=True)

    # ---- 公共夹具 --------------------------------------------------------

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_user(self, email, *, must_change_password=False, is_system_admin=False):
        conn = self._connect()
        try:
            row = conn.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()
            if row:
                return row['id']
            uid, now = security.new_user_id(), db.now_iso()
            conn.execute(
                'INSERT INTO users (id,email,name,password_hash,created_at,updated_at,'
                'is_system_admin,must_change_password) VALUES (?,?,?,?,?,?,?,?)',
                (uid, email, email.split('@')[0], security.hash_password(PASSWORD),
                 now, now, 1 if is_system_admin else 0, 1 if must_change_password else 0),
            )
            conn.commit()
            return uid
        finally:
            conn.close()

    def _user_id_by_email(self, email):
        conn = self._connect()
        try:
            row = conn.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()
            return row['id'] if row else None
        finally:
            conn.close()

    def _session_for(self, email):
        self._ensure_user(email)
        h = Http(self.port)
        r = h.post('/api/auth/login', {'email': email, 'password': PASSWORD})
        self.assertEqual(r.status, 200,
                         '夹具失败：%s 登录应成功，实际 %s body=%s' % (email, r.status, r.payload))
        return h

    def _admin_http(self):
        if self._admin is None:
            self._admin = self._session_for(ADMIN_EMAIL)
            self._admin_uid = self._user_id_by_email(ADMIN_EMAIL)
        return self._admin

    def _mint(self, *, scopes=None, user_id=None, label='inttest'):
        """走真实浏览器面链路签发令牌，返回 (token, meta, admin_http)。

        请求体字段名按 §16.3 表结构推断（label/scopes/userId）；06 的精确契约若
        命名不同，只需改本函数一处。不给 200/201 或缺 token 时直接 fail——
        这是"前置缺失"的红灯，不跳过，也不伪造令牌绕过链路。
        """
        admin = self._admin_http()
        body = {'label': label, 'scopes': scopes if scopes is not None else ['read']}
        if user_id is not None:
            body['userId'] = user_id
        r = admin.post('/api/admin/api-tokens', body)
        if r.status not in (200, 201):
            self.fail('前置缺失：POST /api/admin/api-tokens 返回 %s body=%s。'
                      'B1 令牌签发端点尚未实现，本用例无法继续验收。' % (r.status, r.payload))
        p = r.payload or {}
        token = p.get('token')
        if not token:
            self.fail('令牌创建响应缺少 token 明文；实际 keys=%s' % list(p))
        meta = dict(p)
        meta.setdefault('userId', user_id if user_id is not None else self._admin_uid)
        return token, meta, admin

    def _row_count(self, table):
        conn = self._connect()
        try:
            return conn.execute('SELECT COUNT(*) FROM "%s"' % table).fetchone()[0]
        finally:
            conn.close()

    def _fetch_row(self, table, rid):
        conn = self._connect()
        try:
            row = conn.execute('SELECT * FROM "%s" WHERE id=?' % table, (rid,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _insert_item(self, session, title):
        r = session.insert('work_items', {'title': title})
        self.assertEqual(r.status, 200,
                         '夹具失败：插入 work_items 应 200，实际 %s body=%s' % (r.status, r.payload))
        return r.rows[0]['id']

    @staticmethod
    def _list_ids(payload):
        if not isinstance(payload, dict):
            return []
        for key in ('rows', 'items', 'data', 'workItems'):
            v = payload.get(key)
            if isinstance(v, list):
                return [x.get('id') for x in v if isinstance(x, dict)]
        return []

    def _read_access_log(self):
        path = Path(self.config.log_file)
        out = []
        if path.exists():
            for line in path.read_text('utf-8', errors='ignore').splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
        return out

    def _assert_no_token_anywhere(self, token):
        """全库所有列 + 日志文件 grep 不到令牌明文；日志不记录 Authorization 头。"""
        conn = self._connect()
        offenders = []
        try:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            for t in tables:
                cols = [c[1] for c in conn.execute('PRAGMA table_info("%s")' % t)]
                for c in cols:
                    try:
                        n = conn.execute(
                            'SELECT COUNT(*) FROM "%s" WHERE CAST("%s" AS TEXT) LIKE ?'
                            % (t, c), ('%' + token + '%',)).fetchone()[0]
                    except sqlite3.Error:
                        continue
                    if n:
                        offenders.append('%s.%s' % (t, c))
        finally:
            conn.close()
        self.assertFalse(offenders, '令牌明文不得出现在任何库表；发现：%s' % offenders)
        for p in (Path(self.config.log_file),):
            if p.exists():
                text = p.read_text('utf-8', errors='ignore')
                self.assertNotIn(token, text, '令牌明文不得出现在日志 %s' % p)
                self.assertNotIn('Authorization', text, '日志不得记录 Authorization 头')

    @staticmethod
    def _port_open(port):
        s = socket.socket()
        s.settimeout(0.2)
        try:
            s.connect(('127.0.0.1', port))
            return True
        except OSError:
            return False
        finally:
            s.close()

    # =====================================================================
    # 14 条验收断言（方法名顺序 = TC 编号顺序）
    # =====================================================================

    def test_TC_INT_01_missing_and_invalid_token_are_401_indistinguishable(self):
        """TC-INT-01 无 Authorization→401 UNAUTHORIZED；错令牌→401 INVALID_TOKEN；两者响应体除 code 外无可区分信息。"""
        path = '/v1/work-items'
        no_auth = self.http.get(path)
        self.assertEqual(no_auth.status, 401,
                         '无 Authorization 访问 %s 应 401，实际 %s body=%s。'
                         '检查 /v1 鉴权是否先于路由执行（路由缺失时也应 401 而非 404）。'
                         % (path, no_auth.status, no_auth.payload))
        self.assertEqual(no_auth.code, 'UNAUTHORIZED',
                         '无令牌的错误码应为 UNAUTHORIZED，实际 %s' % no_auth.code)

        bad = self.http.get(path, headers=bearer('gtd_pat_definitely_invalid'))
        self.assertEqual(bad.status, 401,
                         '错误令牌应 401，实际 %s body=%s' % (bad.status, bad.payload))
        self.assertEqual(bad.code, 'INVALID_TOKEN',
                         '错误令牌的错误码应为 INVALID_TOKEN，实际 %s' % bad.code)

        self.assertEqual(strip_error_code(no_auth.payload), strip_error_code(bad.payload),
                         '两种 401 的响应体除 error.code 外必须完全一致（含 message 与结构），'
                         '否则可区分"没给"与"给错"之外的信息。实际：%s vs %s'
                         % (no_auth.payload, bad.payload))

    def test_TC_INT_02_revocation_is_immediate(self):
        """TC-INT-02 已撤销令牌→401；撤销立即生效（同一秒内的下一次请求即失败）。"""
        token, meta, admin = self._mint(scopes=['read'])
        ok = self.http.get('/v1/me', headers=bearer(token))
        self.assertEqual(ok.status, 200,
                         '撤销前有效令牌应能访问 /v1/me，实际 %s body=%s' % (ok.status, ok.payload))

        tid = meta.get('id') or meta.get('tokenId')
        self.assertIsNotNone(tid,
                             '令牌创建响应需含 id/tokenId 供撤销；实际 keys=%s' % list(meta))
        rev = admin.request('DELETE', '/api/admin/api-tokens/%s' % tid)
        self.assertIn(rev.status, (200, 204),
                      '撤销端点 DELETE /api/admin/api-tokens/{id} 应成功，实际 %s body=%s'
                      % (rev.status, rev.payload))

        # 同一秒内紧接着的下一次请求——不允许任何缓存宽限期
        again = self.http.get('/v1/me', headers=bearer(token))
        self.assertEqual(again.status, 401,
                         '撤销后立即（同一秒）请求应 401，实际 %s body=%s。'
                         '撤销校验必须每次读库，不得缓存。' % (again.status, again.payload))
        self.assertEqual(again.code, 'INVALID_TOKEN',
                         '已撤销令牌应回 INVALID_TOKEN，实际 %s' % again.code)

    def test_TC_INT_03_password_change_gate_not_bypassable(self):
        """TC-INT-03 must_change_password=1 账号的令牌→403 PASSWORD_CHANGE_REQUIRED（闸门不许被绕过）。"""
        uid = self._ensure_user(GATE_EMAIL, must_change_password=True)
        token, meta, admin = self._mint(user_id=uid, scopes=['read'], label='gate-test')
        resp = self.http.get('/v1/me', headers=bearer(token))
        self.assertEqual(resp.status, 403,
                         'must_change_password=1 账号的令牌必须被改密闸门拦下（403），'
                         '实际 %s body=%s。令牌绝不能成为 §4.7 闸门的旁路。'
                         % (resp.status, resp.payload))
        self.assertEqual(resp.code, 'PASSWORD_CHANGE_REQUIRED',
                         '闸门错误码应为 PASSWORD_CHANGE_REQUIRED，实际 %s' % resp.code)

    def test_TC_INT_04_readonly_token_cannot_post_and_no_row_written(self):
        """TC-INT-04 只读令牌 POST /v1/work-items→403 INSUFFICIENT_SCOPE，且未落库。"""
        token, meta, admin = self._mint(scopes=['read'])
        before = self._row_count('work_items')
        resp = self.http.post('/v1/work-items', {'title': 'readonly-should-not-exist'},
                              headers=bearer(token))
        self.assertEqual(resp.status, 403,
                         '只读令牌写操作应 403，实际 %s body=%s' % (resp.status, resp.payload))
        self.assertEqual(resp.code, 'INSUFFICIENT_SCOPE',
                         '作用域不足的错误码应为 INSUFFICIENT_SCOPE，实际 %s' % resp.code)
        after = self._row_count('work_items')
        self.assertEqual(after, before,
                         '作用域被拒后不得落库：before=%d after=%d' % (before, after))

    def test_TC_INT_05_cross_account_rows_invisible_and_404_not_403(self):
        """TC-INT-05 A 令牌读 B 团队数据不可见；单条 GET /v1/work-items/{B id}→404（不是 403）。"""
        a_uid = self._ensure_user(A_EMAIL)
        self._ensure_user(B_EMAIL)
        a_session = self._session_for(A_EMAIL)
        b_session = self._session_for(B_EMAIL)
        b_id = self._insert_item(b_session, 'B-private-work-item')
        a_id = self._insert_item(a_session, 'A-own-work-item')

        token, meta, admin = self._mint(user_id=a_uid, scopes=['read'])
        listing = self.http.get('/v1/work-items', headers=bearer(token))
        self.assertEqual(listing.status, 200,
                         '列表请求应 200，实际 %s body=%s' % (listing.status, listing.payload))
        ids = self._list_ids(listing.payload)
        self.assertIn(a_id, ids,
                      'A 应能看到自己的行 id=%s；否则列表解析方式不符（防假绿）。ids=%s'
                      % (a_id, ids))
        self.assertNotIn(b_id, ids,
                         'A 令牌的列表不得包含 B 的行 id=%s；ids=%s' % (b_id, ids))

        single = self.http.get('/v1/work-items/%s' % b_id, headers=bearer(token))
        self.assertEqual(single.status, 404,
                         'GET B 的单条应为 404（不是 403，避免泄露行存在性），'
                         '实际 %s body=%s' % (single.status, single.payload))

    def test_TC_INT_06_origin_always_blocked_even_with_valid_token(self):
        """TC-INT-06 带 Origin: https://evil.test 的请求→403 CROSS_ORIGIN_BLOCKED（即使令牌正确）。"""
        token, meta, admin = self._mint(scopes=['read'])
        resp = self.http.get('/v1/me', headers=bearer(token, {'Origin': 'https://evil.test'}))
        self.assertEqual(resp.status, 403,
                         '带 Origin 的请求应一律 403（出现即拒），实际 %s body=%s。'
                         '主入口的"校验同源"策略不适用于集成域。'
                         % (resp.status, resp.payload))
        self.assertEqual(resp.code, 'CROSS_ORIGIN_BLOCKED',
                         '跨站错误码应为 CROSS_ORIGIN_BLOCKED，实际 %s' % resp.code)

    def test_TC_INT_07_patch_precondition_and_version_conflict(self):
        """TC-INT-07 PATCH 无 If-Match/expectedVersion→428；带旧版本→412 且库内未变。"""
        # 令牌必须与造行的会话同账号：个人 work_items 的 owner 由服务端注入为 A，
        # 若用默认 admin 令牌去改 A 的行，policy 正确返回 404（这是夹具错配，非实现缺陷）。
        token, meta, admin = self._mint(user_id=self._ensure_user(A_EMAIL),
                                        scopes=['read', 'write:edit'])
        session = self._session_for(A_EMAIL)
        rid = self._insert_item(session, 'patch-target')

        no_pre = self.http.patch('/v1/work-items/%s' % rid, {'title': 'changed'},
                                 headers=bearer(token))
        self.assertEqual(no_pre.status, 428,
                         '无 If-Match/expectedVersion 的 PATCH 应 428（不得静默降级为无保护写入），'
                         '实际 %s body=%s' % (no_pre.status, no_pre.payload))

        before = self._fetch_row('work_items', rid)
        cur_ver = before.get('version')
        self.assertIsNotNone(cur_ver,
                             'work_items 需有 version 列（v3 迁移）；实际列=%s' % list(before))
        stale_ver = max(int(cur_ver) - 1, 0)
        stale = self.http.patch(
            '/v1/work-items/%s' % rid, {'title': 'stale-write'},
            headers=bearer(token, {'If-Match': 'W/"%s-%d"' % (rid, stale_ver)}))
        self.assertEqual(stale.status, 412,
                         '带旧版本的 PATCH 应 412，实际 %s body=%s' % (stale.status, stale.payload))

        after = self._fetch_row('work_items', rid)
        self.assertEqual(after.get('title'), before.get('title'),
                         '412 后库内不得变化：before=%s after=%s'
                         % (before.get('title'), after.get('title')))

    def test_TC_INT_08_idempotency_key_semantics(self):
        """TC-INT-08 同 Idempotency-Key + 同 body 两次→只建一行、第二次返回首个响应；同 key 不同 body→409。"""
        token, meta, admin = self._mint(scopes=['read', 'write:edit'])
        before = self._row_count('work_items')
        key = 'int-idem-' + uuid.uuid4().hex
        body = {'title': 'idem-created-once'}
        hdrs = bearer(token, {'Idempotency-Key': key})

        r1 = self.http.post('/v1/work-items', body, headers=hdrs)
        self.assertIn(r1.status, (200, 201),
                      '首次带幂等键创建应成功（200/201），实际 %s body=%s'
                      % (r1.status, r1.payload))
        r2 = self.http.post('/v1/work-items', body, headers=hdrs)
        self.assertEqual(r2.status, r1.status,
                         '同 key 同 body 的第二次应返回首个响应状态码（含 201），'
                         '实际 r1=%s r2=%s' % (r1.status, r2.status))
        self.assertEqual(self._row_count('work_items'), before + 1,
                         '幂等重放只应建立一行：before=%d after=%d'
                         % (before, self._row_count('work_items')))

        r3 = self.http.post('/v1/work-items', {'title': 'DIFFERENT-body'}, headers=hdrs)
        self.assertEqual(r3.status, 409,
                         '同 key 不同 body 应 409 idempotency_conflict，实际 %s body=%s'
                         % (r3.status, r3.payload))

    def test_TC_INT_09_unchanged_patch_keeps_version(self):
        """TC-INT-09 未变更 PATCH→200 且 version 不变、X-Gtd-Unchanged: 1。"""
        # 令牌与造行会话同账号（个人行 owner=A），否则可见性判定必回 404。
        token, meta, admin = self._mint(user_id=self._ensure_user(A_EMAIL),
                                        scopes=['read', 'write:edit'])
        session = self._session_for(A_EMAIL)
        rid = self._insert_item(session, 'unchanged-target')
        cur = self._fetch_row('work_items', rid)
        ver = cur.get('version')
        self.assertIsNotNone(ver, 'work_items 需有 version 列（v3 迁移）；实际列=%s' % list(cur))

        resp = self.http.patch(
            '/v1/work-items/%s' % rid, {'title': 'unchanged-target'},
            headers=bearer(token, {'If-Match': 'W/"%s-%s"' % (rid, ver)}))
        self.assertEqual(resp.status, 200,
                         '未变更 PATCH 应 200，实际 %s body=%s' % (resp.status, resp.payload))
        self.assertEqual(resp.header('X-Gtd-Unchanged'), '1',
                         '未变更 PATCH 应回 X-Gtd-Unchanged: 1；实际 headers=%s' % resp.headers)
        after = self._fetch_row('work_items', rid)
        self.assertEqual(str(after.get('version')), str(ver),
                         '未变更 PATCH 不得推进 version：before=%s after=%s'
                         % (ver, after.get('version')))

    def test_TC_INT_10_audit_log_exactly_one_and_no_token_leak(self):
        """TC-INT-10 写成功后 app_logs 有且仅有一条 event='api.write'，detail 含 tokenId 与 versionBefore/After；全表 grep 不到令牌明文。"""
        token, meta, admin = self._mint(scopes=['read', 'write:edit'])
        conn = self._connect()
        try:
            conn.execute("DELETE FROM app_logs WHERE event='api.write'")
            conn.commit()
        finally:
            conn.close()

        r = self.http.post('/v1/work-items', {'title': 'audit-me'}, headers=bearer(token))
        self.assertIn(r.status, (200, 201),
                      '审计用例前置：写请求应成功，实际 %s body=%s' % (r.status, r.payload))

        conn = self._connect()
        try:
            rows = [dict(x) for x in conn.execute(
                "SELECT * FROM app_logs WHERE event='api.write'").fetchall()]
        finally:
            conn.close()
        self.assertEqual(len(rows), 1,
                         '一次写成功后 app_logs 应有且仅有一条 api.write，实际 %d 条' % len(rows))
        detail = rows[0].get('detail') or ''
        for field in ('tokenId', 'versionBefore', 'versionAfter'):
            self.assertIn(field, detail,
                          'api.write 的 detail 应含 %s；实际 detail=%s' % (field, detail))
        self.assertNotIn(token, detail, '审计 detail 不得含令牌明文')
        self._assert_no_token_anywhere(token)

    def test_TC_INT_11_access_log_has_token_id_not_plaintext(self):
        """TC-INT-11 访问日志里 token_id 存在而令牌明文不存在；uid 等于令牌所属账号。"""
        token, meta, admin = self._mint(scopes=['read'])
        self.http.get('/v1/me', headers=bearer(token))

        hits = [e for e in self._read_access_log()
                if str(e.get('path', '')).startswith('/v1/')]
        self.assertTrue(hits,
                        '访问日志应记录 /v1 请求（当前总日志 %d 条）' % len(self._read_access_log()))
        hit = hits[-1]
        self.assertIn('token_id', hit,
                      '访问日志应记录 token_id（不是令牌本体）；实际 keys=%s' % list(hit))
        self.assertNotIn(token, json.dumps(hit, ensure_ascii=False),
                         '访问日志不得含令牌明文')
        self.assertEqual(hit.get('uid'), meta.get('userId'),
                         '访问日志 uid 应等于令牌所属账号；actual=%s expected=%s'
                         % (hit.get('uid'), meta.get('userId')))

    def test_TC_INT_12_disabled_switch_leaves_no_integration_listener(self):
        """TC-INT-12 关闭开关后无 8790/8722 监听；进程内 Application 仍只有主 server。"""
        # 关键：必须另造一个"开关默认关闭"的 Config，不能复用 setUp 里 api_enabled=True
        # 的 self.config。关闭时 make_integration_servers 应返回空列表——连 bind 都不做
        # （§16.1 不变式 3），而不是绑了再拒。这才是本条断言的鉴别力所在。
        off_cfg = Config(host='127.0.0.1', port=free_port(), data_dir=self.tmp,
                         admin_emails=[ADMIN_EMAIL], cert_dir=self.cert_dir)
        self.assertFalse(off_cfg.api_enabled, '默认 Config 的 api_enabled 应为 False')
        self.assertFalse(off_cfg.mcp_enabled, '默认 Config 的 mcp_enabled 应为 False')
        # make_integration_servers 在开关关闭时直接返回 []，连 socket 都不 bind；
        # 它本身不起线程，传入 self.app 即可，绝不能再 make_server 后不 start 就 shutdown
        # （BaseServer.shutdown() 会等待一个从未启动的 serve_forever 循环 → 死锁）。
        self.assertEqual(make_integration_servers(off_cfg, self.app), [],
                         '开关关闭时不应创建任何集成监听器（返回空列表）')
        for port in (8790, 8722):
            self.assertFalse(self._port_open(port),
                             '集成域默认关闭时端口 %d 不应被监听' % port)

    def test_TC_INT_13_delete_is_soft_and_hidden_from_list(self):
        """TC-INT-13 delete_work_item 之后该行仍在库（archived_at 非空），GET /v1/work-items 默认不含它。"""
        # 令牌与造行会话同账号（个人行 owner=A），否则可见性判定必回 404。
        token, meta, admin = self._mint(user_id=self._ensure_user(A_EMAIL),
                                        scopes=['read', 'write:edit', 'write:delete'])
        session = self._session_for(A_EMAIL)
        rid = self._insert_item(session, 'to-soft-delete')
        # §16.6.1 全局硬约束：所有写请求必须带 If-Match/expectedVersion，缺失即 428。
        # 本条测的是"软删除语义"而非前置条件缺失（那属 TC-INT-07），故夹具须带当前版本。
        before = self._fetch_row('work_items', rid)
        ver = before.get('version')
        self.assertIsNotNone(ver, 'work_items 需有 version 列；实际列=%s' % list(before))

        resp = self.http.request('DELETE', '/v1/work-items/%s' % rid,
                                 headers=bearer(token, {'If-Match': 'W/"%s-%s"' % (rid, ver)}))
        self.assertIn(resp.status, (200, 204),
                      'DELETE /v1/work-items/{id}（带 If-Match）应成功，实际 %s body=%s'
                      % (resp.status, resp.payload))

        after = self._fetch_row('work_items', rid)
        self.assertIsNotNone(after, '软删后该行仍应在库中（无物理删除）')
        self.assertTrue(after.get('archived_at'),
                        '软删后 archived_at 应非空；实际 row=%s' % after)
        # §16.4.1：DELETE 是"写 archived_at + status='archived'"的一次软删除；
        # §16.6.1：软删除属于实际变更，version 应 +1。两者都是加严，不放松原判据。
        self.assertEqual(after.get('status'), 'archived',
                         '软删后 status 应为 archived；实际 row=%s' % after)
        self.assertEqual(int(after.get('version') or 0), int(ver) + 1,
                         '软删是一次实际变更，version 应由 %s 递增到 %s；实际 row=%s'
                         % (ver, int(ver) + 1, after))

        listing = self.http.get('/v1/work-items', headers=bearer(token))
        self.assertEqual(listing.status, 200,
                         '列表请求应 200，实际 %s body=%s' % (listing.status, listing.payload))
        self.assertNotIn(rid, self._list_ids(listing.payload),
                         '软删后列表默认不应包含该行 id=%s' % rid)

    def test_TC_INT_14_cookie_auth_rejected(self):
        """TC-INT-14 集成域监听器不接受 Cookie 鉴权（带 gtd_session 但无 Bearer→401）。"""
        session = self._session_for(A_EMAIL)
        self.assertTrue((session.cookie or '').startswith('gtd_session='),
                        '夹具失败：应持有 gtd_session Cookie；实际 %s' % session.cookie)
        resp = self.http.get('/v1/me', headers={'Cookie': session.cookie})
        self.assertEqual(resp.status, 401,
                         '集成域不接受 Cookie 鉴权：带 gtd_session 无 Bearer 应 401，'
                         '实际 %s body=%s' % (resp.status, resp.payload))


if __name__ == '__main__':
    unittest.main(verbosity=2)
