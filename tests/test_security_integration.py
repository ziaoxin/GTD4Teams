"""G5 放行条件③/④：TC-SEC-INT-01~25 安全集成域用例实跑。

判据来源（逐字读取，未凭转述改判据）：
  docs/process/08_安全审计与上线门禁.md
    §15.5（TC-SEC-INT-01~25 全文）、§15.3（四个专项问答判据）、
    §15.4（B3-D-01~04）、§15.6（G5 放行条件③④）、§15.7（V1~V8）；
  docs/process/04_TDS_技术设计说明书-update 11.18.md
    §16.12.1（B3-D-01 裁决：mcp 是传输能力位、write:* 是数据权限位，默认 ["read"]）。

为什么单独一个文件：
  本项目踩过"共享状态导致假失败"的坑。本文件自带独立临时库 + 独立临时端口，
  每个测试方法独立 setUp/tearDown，与 test_api.py(156) / test_integration.py(14) 零共享。

纪律：
  * 零依赖：只用 unittest / http.client / sqlite3 / tempfile / threading / subprocess。
  * 离线：setUp 显式清空 llm_api_key；subprocess 只跑本地 -c 脚本。
  * 红灯就是红灯：不 skip、不放宽判据。若文档判据与裁决后实现冲突，如实报红。
  * 行尾 LF。
"""

import contextlib
import http.client
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyapp import db, security, store                          # noqa: E402
from pyapp.config import Config                                # noqa: E402
from pyapp.httpd import free_port, make_server, make_integration_servers  # noqa: E402

PASSWORD = 'test-pass-1234'
NEW_PASSWORD = 'test-pass-5678'
ADMIN_EMAIL = 'secint-admin@gtd.local'
A_EMAIL = 'secint-a@gtd.local'
B_EMAIL = 'secint-b@gtd.local'
OWNER_EMAIL = 'secint-owner@gtd.local'
MEMBER_EMAIL = 'secint-member@gtd.local'
GATE_EMAIL = 'secint-gate@gtd.local'
PLAINTEXT_MARKER = 'gtd_pat_'


# ---------------------------------------------------------------------------
# 最小 HTTP / JSON-RPC 客户端
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

    def has_acao(self):
        return any(k.startswith('access-control-allow-origin') for k in self.headers)


class Http:
    """可打主入口、/v1 监听器与 /mcp 监听器的最小客户端。"""

    def __init__(self, port, api_port=None, mcp_port=None, cookie=None):
        self.port = port
        self.api_port = api_port
        self.mcp_port = mcp_port
        self.cookie = cookie

    def request(self, method, path, body=None, *, headers=None, raw_body=None, port=None):
        if port is None:
            port = self.api_port if (self.api_port and path.startswith('/v1/')) else self.port
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=20)
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
        data, hdrs = resp.read(), dict(resp.getheaders())
        status = resp.status
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

    def rpc(self, method, params=None, rid=1, token=None, headers=None):
        """单次 JSON-RPC POST /mcp（MCP 是独立第三监听器）。"""
        body = {'jsonrpc': '2.0', 'id': rid, 'method': method}
        if params is not None:
            body['params'] = params
        sent = {}
        if token:
            sent['Authorization'] = 'Bearer ' + token
        sent.update(headers or {})
        return self.request('POST', '/mcp', body, headers=sent, port=self.mcp_port)


def bearer(token, extra=None):
    h = {'Authorization': 'Bearer ' + token}
    h.update(extra or {})
    return h


def strip_error_code(payload):
    """TC-SEC-INT-01：去掉 error.code 后的响应体（其余必须逐字相同）。"""
    p = json.loads(json.dumps(payload)) if payload is not None else None
    if isinstance(p, dict) and isinstance(p.get('error'), dict):
        p['error'].pop('code', None)
    return p


def rpc_gtd_code(resp):
    """JSON-RPC 应用层错误码在 error.data.gtdCode（HTTP 状态仍是 200）。"""
    err = (resp.payload or {}).get('error') or {}
    return (err.get('data') or {}).get('gtdCode')


def rpc_tool_names(resp):
    tools = (((resp.payload or {}).get('result') or {}).get('tools')) or []
    return [t.get('name') for t in tools if isinstance(t, dict)]


# ---------------------------------------------------------------------------
# 夹具：独立临时库 + 独立临时端口（api + mcp 两个集成监听器都开）
# ---------------------------------------------------------------------------

class SecurityIntegrationTest(unittest.TestCase):

    @staticmethod
    def _distinct_ports(count):
        """分配互不相同的空闲端口。

        Windows 上 allow_reuse_address=True 会让两个 server **静默绑到同一端口**
        （实测 bind 第二个不报错）。若 main/api 撞端口，/v1 请求会被主监听器
        接走并回 404「接口 /v1/... 不存在」——正是偶发红灯的症状。故必须去重。
        """
        ports = []
        while len(ports) < count:
            p = free_port()
            if p not in ports:
                ports.append(p)
        return ports

    def setUp(self):
        security.reset_limits()
        self.tmp = tempfile.mkdtemp(prefix='gtd4teams-secint-')
        self.cert_dir = tempfile.mkdtemp(prefix='gtd4teams-secint-cert-')
        self.port, self.api_port, self.mcp_port = self._distinct_ports(3)
        self.config = Config(host='127.0.0.1', port=self.port, data_dir=self.tmp,
                             admin_emails=[ADMIN_EMAIL], cert_dir=self.cert_dir,
                             api_enabled=True, api_port=self.api_port,
                             mcp_enabled=True, mcp_port=self.mcp_port)
        # 杜绝外网调用：本项目踩过 Linux 上 LLM 挂超时的坑
        self.config.llm_api_key = ''
        self.server, self.app = make_server(self.config)
        self.threads = []
        t = threading.Thread(target=self.server.serve_forever, daemon=True,
                             name='gtd-main-http')
        t.start()
        self.threads.append(t)
        # make_integration_servers 顺序恒为 [api, mcp]（开关开时）；它不起线程，
        # 必须自己 serve_forever 后再 shutdown —— 否则 shutdown() 永久死锁（qa-int 踩过）。
        self.integration_servers = make_integration_servers(self.config, self.app)
        self.assertEqual(len(self.integration_servers), 2,
                         '夹具失败：api+mcp 都开启时应有 2 个集成监听器')
        for label, s in zip(('gtd-api-http', 'gtd-mcp-http'), self.integration_servers):
            t = threading.Thread(target=s.serve_forever, daemon=True, name=label)
            t.start()
            self.threads.append(t)
        # 自检：三个监听器必须真的绑在互异且预期的端口上。Windows 的
        # SO_REUSEADDR 允许重复 bind 而不报错，不能只信 free_port 的返回值。
        bound = [self.server.server_address[1]] + [s.server_address[1]
                                                    for s in self.integration_servers]
        self.assertEqual(bound, [self.port, self.api_port, self.mcp_port],
                         '夹具失败：实际监听端口 %s 与分配端口 %s 不一致'
                         % (bound, [self.port, self.api_port, self.mcp_port]))
        self.http = Http(self.port, api_port=self.api_port, mcp_port=self.mcp_port)
        # 自检：/v1 必须真的由 API 监听器应答。撞端口时这条会立刻给出明确红灯，
        # 而不是让某个用例在几十行之后收到莫名其妙的 404。
        probe = self.http.get('/v1/health')
        self.assertEqual(probe.status, 200,
                         '夹具失败：/v1/health 应命中 API 监听器，实际 %s body=%s'
                         % (probe.status, probe.payload))
        self.db_path = str(Path(self.tmp) / 'gtd4teams.db')
        self._admin = None
        self._admin_uid = None

    def tearDown(self):
        servers = []
        try:
            servers.extend(getattr(self, 'integration_servers', []))
            if getattr(self, 'server', None) is not None:
                servers.append(self.server)
            # shutdown() 只让 serve_forever 退出循环，线程本身可能仍在收尾。
            # 必须逐个 join 后再 server_close，否则端口未必真正释放，下一个
            # 用例的 free_port() 可能撞上尚未关闭的监听器。
            for s in servers:
                s.shutdown()
            stuck = []
            for t in getattr(self, 'threads', []):
                t.join(timeout=5)
                if t.is_alive():
                    stuck.append(t.name)
            if stuck:
                self.fail('夹具清理失败：serve_forever 线程 5s 内未退出：%s' % stuck)
            for s in servers:
                s.server_close()
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)
            shutil.rmtree(self.cert_dir, ignore_errors=True)

    # ---- 通用夹具 --------------------------------------------------------

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

    def _mint(self, *, scopes=None, user_id=None, label='secint', expires_in_days=None):
        """走真实浏览器面链路签发令牌（不伪造），返回 (token, meta, admin_http)。"""
        admin = self._admin_http()
        body = {'label': label, 'scopes': scopes if scopes is not None else ['read']}
        if user_id is not None:
            body['userId'] = user_id
        if expires_in_days is not None:
            body['expiresInDays'] = expires_in_days
        r = admin.post('/api/admin/api-tokens', body)
        if r.status not in (200, 201):
            self.fail('前置缺失：POST /api/admin/api-tokens 返回 %s body=%s。'
                      'B1 令牌签发端点不可用时本用例无法验收。' % (r.status, r.payload))
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

    def _audit_rows(self, event):
        conn = self._connect()
        try:
            rows = conn.execute('SELECT * FROM app_logs WHERE event=?', (event,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def _audit_count(self, event):
        return len(self._audit_rows(event))

    def _insert_item(self, session, title):
        r = session.insert('work_items', {'title': title})
        self.assertEqual(r.status, 200,
                         '夹具失败：插入 work_items 应 200，实际 %s body=%s'
                         % (r.status, r.payload))
        return r.rows[0]['id']

    def _insert_item_direct(self, title, *, team_id=None, owner_id):
        """直接入库造 fixture（仅用于团队/成员可见性场景，不经 API）。"""
        now = db.now_iso()
        conn = self._connect()
        try:
            conn.execute(
                'INSERT INTO work_items (team_id,owner_id,title,version,created_at,updated_at) '
                'VALUES (?,?,?,?,?,?)', (team_id, owner_id, title, 1, now, now))
            conn.commit()
            return conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
        finally:
            conn.close()

    @staticmethod
    def _list_ids(payload):
        if not isinstance(payload, dict):
            return []
        for key in ('items', 'rows', 'data', 'workItems'):
            v = payload.get(key)
            if isinstance(v, list):
                return [x.get('id') for x in v if isinstance(x, dict)]
        return []

    def _read_access_log(self):
        out = []
        for p in sorted((Path(self.tmp) / 'logs').glob('*.jsonl')):
            for line in p.read_text('utf-8', errors='ignore').splitlines():
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
        return out

    def _column_hits(self, needle):
        """全库所有表所有文本列命中 needle 的 `表.列=次数` 列表。"""
        hits = []
        conn = self._connect()
        try:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            for t in tables:
                cols = [c[1] for c in conn.execute('PRAGMA table_info("%s")' % t)]
                for c in cols:
                    try:
                        n = conn.execute(
                            'SELECT COUNT(*) FROM "%s" WHERE CAST("%s" AS TEXT) LIKE ?'
                            % (t, c), ('%' + needle + '%',)).fetchone()[0]
                    except sqlite3.Error:
                        continue
                    if n:
                        hits.append('%s.%s=%d' % (t, c, n))
        finally:
            conn.close()
        return hits

    def _backup_text(self):
        """进程内等价 `python server.py --export`（不碰真实 data/）。"""
        conn = self._connect()
        try:
            return json.dumps(store.export_all(conn), ensure_ascii=False, default=str)
        finally:
            conn.close()

    def _assert_no_plaintext_anywhere(self, token=None):
        """G5 条件④：访问日志 / 备份导出 / app_logs 全表三处 grep 全 0。"""
        needles = [PLAINTEXT_MARKER] + ([token] if token and token != PLAINTEXT_MARKER else [])
        for needle in needles:
            access_hits = []
            for p in sorted((Path(self.tmp) / 'logs').glob('*.jsonl')):
                n = p.read_text('utf-8', errors='ignore').count(needle)
                if n:
                    access_hits.append('%s=%d' % (p.name, n))
            self.assertFalse(access_hits,
                             '访问日志出现令牌明文 %r：%s' % (needle, access_hits))
            db_hits = self._column_hits(needle)
            self.assertFalse(db_hits,
                             '库表（含 app_logs）出现令牌明文 %r：%s' % (needle, db_hits))
            backup_hits = self._backup_text().count(needle)
            self.assertEqual(backup_hits, 0,
                             '备份导出出现令牌明文 %r：%d 处' % (needle, backup_hits))

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
    # TC-SEC-INT-01 ~ 25（方法名后缀 = 用例编号）
    # =====================================================================

    def test_TC_SEC_INT_01_missing_and_invalid_token_indistinguishable(self):
        """TC-SEC-INT-01 无令牌→401 UNAUTHORIZED；错令牌→401 INVALID_TOKEN；响应体除 code 外无差异。"""
        path = '/v1/work-items'
        missing = self.http.get(path)
        self.assertEqual(missing.status, 401,
                         '无 Authorization 应 401，实际 %s body=%s' % (missing.status, missing.payload))
        self.assertEqual(missing.code, 'UNAUTHORIZED', '无令牌码应为 UNAUTHORIZED，实际 %s' % missing.code)

        wrong = self.http.get(path, headers=bearer('gtd_pat_wrong'))
        self.assertEqual(wrong.status, 401,
                         '错令牌应 401，实际 %s body=%s' % (wrong.status, wrong.payload))
        self.assertEqual(wrong.code, 'INVALID_TOKEN', '错令牌码应为 INVALID_TOKEN，实际 %s' % wrong.code)
        self.assertEqual(strip_error_code(missing.payload), strip_error_code(wrong.payload),
                         '两种 401 除 code 外必须无差异（不泄露令牌曾有效）：%s vs %s'
                         % (missing.payload, wrong.payload))

    def test_TC_SEC_INT_02_revocation_immediate_and_survives_restart(self):
        """TC-SEC-INT-02 撤销后同一秒下一次请求 401；重启进程后仍 401（查库不缓存）。"""
        token, meta, admin = self._mint(scopes=['read'])
        self.assertEqual(self.http.get('/v1/me', headers=bearer(token)).status, 200,
                         '撤销前令牌应有效')
        tid = meta.get('id') or meta.get('tokenId')
        self.assertIsNotNone(tid, '令牌响应需含 id/tokenId 供撤销；keys=%s' % list(meta))
        rev = admin.request('DELETE', '/api/admin/api-tokens/%s' % tid)
        self.assertIn(rev.status, (200, 204),
                      '撤销端点应成功，实际 %s body=%s' % (rev.status, rev.payload))
        again = self.http.get('/v1/me', headers=bearer(token))
        self.assertEqual(again.status, 401, '撤销后立即请求应 401，实际 %s' % again.status)
        self.assertEqual(again.code, 'INVALID_TOKEN', '撤销后应 INVALID_TOKEN，实际 %s' % again.code)

        # 真·新进程（sys.executable -c）只拿同一个临时库，验证没有任何进程内缓存。
        script = r'''
import os, sqlite3, sys
sys.path.insert(0, os.environ['GTD_ROOT'])
from pyapp.config import Config
from pyapp.integration import authenticate_token, IntegrationError
cfg = Config(data_dir=os.environ['GTD_TMP'], admin_emails=[], cert_dir=os.environ['GTD_CERT'])
conn = sqlite3.connect(cfg.db_path)
conn.row_factory = sqlite3.Row
class H:
    headers = {'Authorization': 'Bearer ' + os.environ['GTD_TOKEN']}
    SESSION_TOUCH_SECONDS = 300
    _uid = None
try:
    authenticate_token(H(), conn)
    print('ALLOWED')
except IntegrationError as exc:
    print(exc.code)
'''
        env = dict(os.environ, GTD_ROOT=str(ROOT), GTD_TMP=self.tmp,
                   GTD_CERT=self.cert_dir, GTD_TOKEN=token)
        proc = subprocess.run([sys.executable, '-c', script], capture_output=True,
                              text=True, env=env, timeout=60)
        self.assertEqual(proc.stdout.strip(), 'INVALID_TOKEN',
                         '新进程重放已撤销令牌必须仍 INVALID_TOKEN；stdout=%r stderr=%r'
                         % (proc.stdout, proc.stderr))

    def test_TC_SEC_INT_03_password_gate_not_bypassable_by_token(self):
        """TC-SEC-INT-03 must_change_password=1 令牌：GET 与写都 403 PASSWORD_CHANGE_REQUIRED，无副作用。"""
        uid = self._ensure_user(GATE_EMAIL, must_change_password=True)
        # 故意只给 read：若闸门排在 scope 之后，写请求会错回 INSUFFICIENT_SCOPE。
        token, meta, admin = self._mint(user_id=uid, scopes=['read'], label='gate-secint')
        before = self._row_count('work_items')
        get = self.http.get('/v1/work-items', headers=bearer(token))
        self.assertEqual(get.status, 403, 'GET 应被闸门拦下，实际 %s body=%s' % (get.status, get.payload))
        self.assertEqual(get.code, 'PASSWORD_CHANGE_REQUIRED',
                         '闸门码应为 PASSWORD_CHANGE_REQUIRED，实际 %s' % get.code)
        post = self.http.post('/v1/work-items', {'title': 'gate-bypass-should-not-exist'},
                              headers=bearer(token))
        self.assertEqual(post.status, 403,
                         '写代码也应 403（不能被闸门旁路），实际 %s body=%s'
                         % (post.status, post.payload))
        self.assertEqual(post.code, 'PASSWORD_CHANGE_REQUIRED',
                         '写代码时须回闸门码而非 INSUFFICIENT_SCOPE，实际 %s' % post.code)
        self.assertEqual(self._row_count('work_items'), before, '闸门拒绝不得有业务副作用')

    def test_TC_SEC_INT_04_readonly_token_cannot_write(self):
        """TC-SEC-INT-04 read 令牌 POST /v1/work-items→403 INSUFFICIENT_SCOPE 且行数不变。"""
        token, meta, admin = self._mint(scopes=['read'])
        before = self._row_count('work_items')
        r = self.http.post('/v1/work-items', {'title': 'readonly-no-write'},
                           headers=bearer(token))
        self.assertEqual(r.status, 403, '只读写应 403，实际 %s body=%s' % (r.status, r.payload))
        self.assertEqual(r.code, 'INSUFFICIENT_SCOPE', '应为 INSUFFICIENT_SCOPE，实际 %s' % r.code)
        self.assertEqual(self._row_count('work_items'), before, '被拒后行数不变')

    def test_TC_SEC_INT_05_cross_team_read_invisible_404_and_no_raw_sql(self):
        """TC-SEC-INT-05 A 令牌看不到 B 行；单条 GET B→404（非 403）；handler 无裸 SQL。"""
        a_uid = self._ensure_user(A_EMAIL)
        self._ensure_user(B_EMAIL)
        a_session, b_session = self._session_for(A_EMAIL), self._session_for(B_EMAIL)
        b_id = self._insert_item(b_session, 'B-private')
        a_id = self._insert_item(a_session, 'A-own')
        token, meta, admin = self._mint(user_id=a_uid, scopes=['read'])

        listing = self.http.get('/v1/work-items', headers=bearer(token))
        self.assertEqual(listing.status, 200, '列表应 200，实际 %s' % listing.status)
        ids = self._list_ids(listing.payload)
        self.assertIn(a_id, ids, 'A 应看见自己的行（防假绿）；ids=%s' % ids)
        self.assertNotIn(b_id, ids, 'A 不得看见 B 的行 id=%s；ids=%s' % (b_id, ids))
        single = self.http.get('/v1/work-items/%s' % b_id, headers=bearer(token))
        self.assertEqual(single.status, 404, '单条 B 行应 404 而非 403，实际 %s body=%s'
                         % (single.status, single.payload))

        # 静态证据：集成域 handler 不得自己拼 work_items 的 SQL（§15.3① 判据）
        src = (ROOT / 'pyapp' / 'integration.py').read_text('utf-8', errors='ignore')
        import re
        raw = re.findall(r'(?i)\b(?:select|update|delete)\b[^\n]{0,80}\bwork_items\b', src)
        self.assertFalse(raw, '集成域不得有裸 work_items SQL（须经 store/policy）：%s' % raw)

    def test_TC_SEC_INT_06_origin_present_blocked_on_write(self):
        """TC-SEC-INT-06 写请求带 Origin: https://evil.test→403 CROSS_ORIGIN_BLOCKED，无 ACAO。"""
        token, meta, admin = self._mint(scopes=['read', 'write:edit'])
        r = self.http.post('/v1/work-items', {'title': 'origin-blocked'},
                           headers=bearer(token, {'Origin': 'https://evil.test'}))
        self.assertEqual(r.status, 403, '带 Origin 的写应 403，实际 %s body=%s' % (r.status, r.payload))
        self.assertEqual(r.code, 'CROSS_ORIGIN_BLOCKED', '应为 CROSS_ORIGIN_BLOCKED，实际 %s' % r.code)
        self.assertFalse(r.has_acao(), '响应不得有 Access-Control-Allow-Origin：%s' % r.headers)

    def test_TC_SEC_INT_07_patch_precondition_and_version_conflict(self):
        """TC-SEC-INT-07 无 If-Match→428；旧版本→412 task_version_conflict；两者冲突→400。"""
        token, meta, admin = self._mint(user_id=self._ensure_user(A_EMAIL),
                                        scopes=['read', 'write:edit'])
        session = self._session_for(A_EMAIL)
        rid = self._insert_item(session, 'version-target')
        before = self._fetch_row('work_items', rid)
        ver = int(before.get('version') or 1)
        self.assertEqual(ver, 1, '夹具：新建行 version 应为 1，实际 %s' % ver)

        no_pre = self.http.patch('/v1/work-items/%s' % rid, {'title': 'changed'},
                                 headers=bearer(token))
        self.assertEqual(no_pre.status, 428, '无 If-Match 应 428，实际 %s body=%s'
                         % (no_pre.status, no_pre.payload))
        self.assertEqual(no_pre.code, 'PATCH_PRECONDITION_REQUIRED',
                         '应为 PATCH_PRECONDITION_REQUIRED，实际 %s' % no_pre.code)

        stale = self.http.patch('/v1/work-items/%s' % rid, {'title': 'stale'},
                                headers=bearer(token, {'If-Match': 'W/"%s-%d"' % (rid, ver - 1)}))
        self.assertEqual(stale.status, 412, '旧版本应 412，实际 %s body=%s'
                         % (stale.status, stale.payload))
        self.assertEqual(stale.code, 'task_version_conflict',
                         '应为 task_version_conflict，实际 %s' % stale.code)

        both = self.http.patch('/v1/work-items/%s' % rid,
                               {'title': 'both', 'expectedVersion': ver + 99},
                               headers=bearer(token, {'If-Match': 'W/"%s-%d"' % (rid, ver)}))
        self.assertEqual(both.status, 400, 'If-Match 与 expectedVersion 冲突应 400，实际 %s body=%s'
                         % (both.status, both.payload))
        after = self._fetch_row('work_items', rid)
        self.assertEqual(after.get('title'), before.get('title'), '412/400 后库内值不变')

    def test_TC_SEC_INT_08_idempotency_replay_and_body_conflict(self):
        """TC-SEC-INT-08 同 key 同 body 只建一行+重放头；同 key 不同 body→409。"""
        token, meta, admin = self._mint(scopes=['read', 'write:edit'])
        before = self._row_count('work_items')
        key = 'secint-idem-' + uuid.uuid4().hex
        body = {'title': 'idem-once'}
        hdrs = bearer(token, {'Idempotency-Key': key})
        r1 = self.http.post('/v1/work-items', body, headers=hdrs)
        self.assertIn(r1.status, (200, 201), '首次应成功，实际 %s body=%s' % (r1.status, r1.payload))
        r2 = self.http.post('/v1/work-items', body, headers=hdrs)
        self.assertEqual(r2.status, r1.status, '重放应回首个状态码，实际 %s vs %s'
                         % (r1.status, r2.status))
        self.assertEqual(r2.header('X-Gtd-Idempotent-Replay'), '1',
                         '重放须带 X-Gtd-Idempotent-Replay: 1；headers=%s' % r2.headers)
        self.assertEqual(self._row_count('work_items'), before + 1, '只应建一行')
        r3 = self.http.post('/v1/work-items', {'title': 'DIFFERENT'}, headers=hdrs)
        self.assertEqual(r3.status, 409, '同 key 不同 body 应 409，实际 %s body=%s'
                         % (r3.status, r3.payload))

    def test_TC_SEC_INT_09_unchanged_patch_has_no_audit(self):
        """TC-SEC-INT-09 全同值 PATCH→200、version 不变、X-Gtd-Unchanged: 1、无审计写入。"""
        token, meta, admin = self._mint(user_id=self._ensure_user(A_EMAIL),
                                        scopes=['read', 'write:edit'])
        session = self._session_for(A_EMAIL)
        rid = self._insert_item(session, 'unchanged-secint')
        cur = self._fetch_row('work_items', rid)
        ver = cur.get('version')
        self.assertIsNotNone(ver, 'work_items 需有 version 列；列=%s' % list(cur))
        audit_before = self._audit_count('api.write')
        r = self.http.patch('/v1/work-items/%s' % rid, {'title': 'unchanged-secint'},
                            headers=bearer(token, {'If-Match': 'W/"%s-%s"' % (rid, ver)}))
        self.assertEqual(r.status, 200, '未变更 PATCH 应 200，实际 %s body=%s' % (r.status, r.payload))
        self.assertEqual(r.header('X-Gtd-Unchanged'), '1',
                         '应回 X-Gtd-Unchanged: 1；headers=%s' % r.headers)
        self.assertEqual(str(self._fetch_row('work_items', rid).get('version')), str(ver),
                         'version 不得推进')
        self.assertEqual(self._audit_count('api.write'), audit_before,
                         '未变更不得写审计')

    def test_TC_SEC_INT_10_audit_has_fields_and_no_plaintext(self):
        """TC-SEC-INT-10 一次写→恰一条 api.write，detail 含 tokenId/versionBefore/After，全表无明文。"""
        token, meta, admin = self._mint(scopes=['read', 'write:edit'])
        r = self.http.post('/v1/work-items', {'title': 'audit-secint'}, headers=bearer(token))
        self.assertIn(r.status, (200, 201), '写应成功，实际 %s body=%s' % (r.status, r.payload))
        rows = self._audit_rows('api.write')
        self.assertEqual(len(rows), 1, '应恰一条 api.write，实际 %d' % len(rows))
        detail = rows[0].get('detail') or ''
        for field in ('tokenId', 'versionBefore', 'versionAfter'):
            self.assertIn(field, detail, 'detail 应含 %s；实际 %s' % (field, detail))
        self.assertNotIn(PLAINTEXT_MARKER, detail, '审计 detail 不得含令牌前缀')
        self._assert_no_plaintext_anywhere(token)

    def test_TC_SEC_INT_11_access_log_identity_and_request_id_link(self):
        """TC-SEC-INT-11 访问日志 token_id 为整数、uid=令牌属主、无明文，request_id 与审计可关联。"""
        token, meta, admin = self._mint(scopes=['read', 'write:edit'])
        self.http.post('/v1/work-items', {'title': 'access-log-secint'}, headers=bearer(token))
        hits = [e for e in self._read_access_log()
                if str(e.get('path', '')).startswith('/v1/') and e.get('token_id') is not None]
        self.assertTrue(hits, '访问日志应含带 token_id 的 /v1 记录；共 %d 条' % len(self._read_access_log()))
        hit = hits[-1]
        self.assertIsInstance(hit.get('token_id'), int, 'token_id 必须是整数；实际 %r'
                              % (hit.get('token_id'),))
        self.assertEqual(hit.get('uid'), meta.get('userId'),
                         'uid 应为令牌属主；actual=%s expected=%s' % (hit.get('uid'), meta.get('userId')))
        self.assertNotIn(token, json.dumps(hit, ensure_ascii=False), '访问日志不得含令牌明文')
        writes = self._audit_rows('api.write')
        self.assertTrue(writes, '应有一条 api.write 供关联')
        rid = json.loads(writes[-1]['detail']).get('requestId')
        self.assertEqual(rid, hit.get('request_id'),
                         '访问日志 request_id 须与审计 requestId 关联；实际 %s vs %s'
                         % (hit.get('request_id'), rid))

    def test_TC_SEC_INT_12_default_off_and_enabled_binds_loopback_only(self):
        """TC-SEC-INT-12 默认关闭无监听且 make_integration_servers→[]；开启后仅绑 127.0.0.1。"""
        off_cfg = Config(host='127.0.0.1', port=free_port(), data_dir=self.tmp,
                         admin_emails=[ADMIN_EMAIL], cert_dir=self.cert_dir)
        self.assertFalse(off_cfg.api_enabled, '默认 api_enabled 应为 False')
        self.assertFalse(off_cfg.mcp_enabled, '默认 mcp_enabled 应为 False')
        # 关键：make_integration_servers 关闭时返回 []，不另起 server，避免 qa-int 的死锁坑。
        self.assertEqual(make_integration_servers(off_cfg, self.app), [],
                         '关闭时不得创建任何监听器')
        for port in (8790, 8722):
            self.assertFalse(self._port_open(port), '默认关闭时端口 %d 不应被监听' % port)
        for s in self.integration_servers:
            addr = s.server_address[0]
            self.assertEqual(addr, '127.0.0.1', '集成监听器只绑回环，实际 %s' % addr)

    def test_TC_SEC_INT_13_delete_is_soft_and_list_excludes(self):
        """TC-SEC-INT-13 write:delete 软删：行仍在、archived_at 非空、status=archived、列表不含。"""
        uid = self._ensure_user(A_EMAIL)
        token, meta, admin = self._mint(user_id=uid, scopes=['read', 'write:edit', 'write:delete'])
        rid = self._insert_item(self._session_for(A_EMAIL), 'soft-delete-secint')
        cur = self._fetch_row('work_items', rid)
        ver = cur.get('version')
        r = self.http.request('DELETE', '/v1/work-items/%s' % rid,
                              headers=bearer(token, {'If-Match': 'W/"%s-%s"' % (rid, ver)}))
        self.assertIn(r.status, (200, 204), 'DELETE 应成功，实际 %s body=%s' % (r.status, r.payload))
        after = self._fetch_row('work_items', rid)
        self.assertIsNotNone(after, '软删后行仍在库')
        self.assertTrue(after.get('archived_at'), 'archived_at 应非空')
        self.assertEqual(after.get('status'), 'archived', 'status 应为 archived')
        self.assertEqual(int(after.get('version') or 0), int(ver) + 1, '软删推进 version')
        listing = self.http.get('/v1/work-items', headers=bearer(token))
        self.assertNotIn(rid, self._list_ids(listing.payload), '列表默认不含软删行')

    def test_TC_SEC_INT_14_cookie_not_accepted_reverse_bearer_works(self):
        """TC-SEC-INT-14 有效 Cookie 无 Bearer→401 UNAUTHORIZED；反向 Bearer 可用（不认 Cookie）。"""
        session = self._session_for(A_EMAIL)
        self.assertTrue((session.cookie or '').startswith('gtd_session='),
                        '夹具应有 gtd_session；实际 %s' % session.cookie)
        r = self.http.get('/v1/me', headers={'Cookie': session.cookie})
        self.assertEqual(r.status, 401, 'Cookie 鉴权应 401，实际 %s body=%s' % (r.status, r.payload))
        self.assertEqual(r.code, 'UNAUTHORIZED', '应为 UNAUTHORIZED，实际 %s' % r.code)
        token, meta, admin = self._mint(user_id=self._user_id_by_email(A_EMAIL), scopes=['read'])
        ok = self.http.get('/v1/me', headers=bearer(token))
        self.assertEqual(ok.status, 200, '反向：Bearer 无 Cookie 应可用，实际 %s' % ok.status)

    def test_TC_SEC_INT_15_bearer_rejected_on_browser_surface(self):
        """TC-SEC-INT-15 Bearer 打令牌管理/export/llm-config 全 401/404（主入口不认 Bearer）。"""
        token, meta, admin = self._mint(scopes=['read', 'write:edit', 'write:delete'])
        cases = [
            ('GET', '/api/admin/api-tokens'),
            ('POST', '/api/admin/api-tokens'),
            ('DELETE', '/api/admin/api-tokens/%s' % (meta.get('id') or meta.get('tokenId'))),
            ('GET', '/api/export'),
            ('GET', '/api/llm/config'),
            ('POST', '/api/llm/config'),
        ]
        for method, path in cases:
            # POST 必须带 JSON 体：否则 415 UNSUPPORTED_MEDIA_TYPE 在鉴权之前早退，
            # 测不到"主入口认不认 Bearer"。这不是放宽判据，是让请求走到鉴权层。
            body = {} if method == 'POST' else None
            r = self.http.request(method, path, body=body, headers=bearer(token))
            self.assertIn(r.status, (401, 404),
                          '%s %s 带 Bearer 应 401/404，实际 %s body=%s'
                          % (method, path, r.status, r.payload))

    def test_TC_SEC_INT_16_proxy_created_token_audit_distinguishes_actor(self):
        """TC-SEC-INT-16 管理员代建 write 令牌绑定 owner：审计须区分创建者与行动身份。"""
        admin = self._admin_http()
        owner_uid = self._ensure_user(OWNER_EMAIL)
        token, meta, _ = self._mint(user_id=owner_uid,
                                    scopes=['read', 'write:edit'], label='proxy-created')
        created = [r for r in self._audit_rows('api.token.created')
                   if json.loads(r['detail'] or '{}').get('tokenId') == (meta.get('id') or meta.get('tokenId'))]
        self.assertEqual(len(created), 1, '代建应恰有一条 api.token.created；实际 %d' % len(created))
        d = json.loads(created[0]['detail'])
        self.assertEqual(d.get('createdBy'), self._admin_uid,
                         '创建者应为管理员；实际 %s' % d.get('createdBy'))
        self.assertEqual(d.get('userId'), owner_uid, '行动身份应为 owner；实际 %s' % d.get('userId'))
        rid = self._insert_item(self._session_for(OWNER_EMAIL), 'proxy-edit-target')
        cur = self._fetch_row('work_items', rid)
        r = self.http.patch('/v1/work-items/%s' % rid, {'title': 'proxy-edited'},
                            headers=bearer(token, {'If-Match': 'W/"%s-%s"' % (rid, cur.get('version'))}))
        self.assertIn(r.status, (200, 201), '代建令牌写应成功，实际 %s body=%s' % (r.status, r.payload))
        writes = self._audit_rows('api.write')
        self.assertTrue(writes, '应有 api.write')
        wd = json.loads(writes[-1]['detail'])
        self.assertEqual(writes[-1]['owner_id'], owner_uid, '行动审计归属应为 owner')
        self.assertEqual(wd.get('tokenId'), meta.get('id') or meta.get('tokenId'),
                         '行动审计应带 tokenId')
        self.assertNotEqual(wd.get('createdBy'), self._admin_uid,
                            '行动审计不得把操作者写成管理员（须能区分）')

    def test_TC_SEC_INT_17_archived_at_not_writable_via_generic_crud(self):
        """TC-SEC-INT-17 /api/rows 写 archived_at 必须被 split_payload 丢弃、库值不变、version 不推进。"""
        session = self._session_for(A_EMAIL)
        rid = self._insert_item(session, 'lock-target')
        cur = self._fetch_row('work_items', rid)
        self.assertFalse(cur.get('archived_at'), '夹具：初始 archived_at 应为空')
        ver = cur.get('version')
        stamp = db.now_iso()
        r = session.post('/api/rows', {
            'table': 'work_items', 'op': 'update', 'id': rid,
            'payload': {'archived_at': stamp, 'title': 'lock-target-renamed'},
        })
        self.assertEqual(r.status, 200, '/api/rows 应 200，实际 %s body=%s' % (r.status, r.payload))
        after = self._fetch_row('work_items', rid)
        self.assertFalse(after.get('archived_at'),
                         'archived_at 经通用 CRUD 必须被丢弃；实际 %r' % after.get('archived_at'))
        self.assertEqual(str(after.get('version')), str(ver), '不得推进 version')
        self.assertEqual(after.get('title'), 'lock-target-renamed', '夹具：同请求里的普通字段应生效')

    def test_TC_SEC_INT_18_deactivated_member_token_loses_team_visibility(self):
        """TC-SEC-INT-18 成员停用后令牌即时失去团队可见性（令牌未撤销但权限已失）。"""
        owner_uid = self._ensure_user(OWNER_EMAIL)
        member_uid = self._ensure_user(MEMBER_EMAIL)
        now = db.now_iso()
        conn = self._connect()
        try:
            conn.execute('INSERT INTO teams (owner_id,owner_name,name,created_at,updated_at) '
                         'VALUES (?,?,?,?,?)', (owner_uid, 'owner', 'T1', now, now))
            tid = conn.execute('SELECT last_insert_rowid() AS id').fetchone()['id']
            conn.execute('INSERT INTO team_members '
                         '(team_id,user_id,email,display_name,role,status,created_at,updated_at) '
                         'VALUES (?,?,?,?,?,?,?,?)',
                         (tid, member_uid, MEMBER_EMAIL, 'member', 'member', 'active', now, now))
            conn.commit()
        finally:
            conn.close()
        item_id = self._insert_item_direct('T1-secret', team_id=tid, owner_id=owner_uid)
        token, meta, admin = self._mint(user_id=member_uid, scopes=['read'])
        before = self.http.get('/v1/work-items', headers=bearer(token))
        self.assertEqual(before.status, 200, '停用前应可见，实际 %s' % before.status)
        self.assertIn(item_id, self._list_ids(before.payload),
                      'active 成员应看得到 T1 行；ids=%s' % self._list_ids(before.payload))

        conn = self._connect()
        try:
            conn.execute("UPDATE team_members SET status='inactive', updated_at=? "
                         "WHERE team_id=? AND user_id=?", (db.now_iso(), tid, member_uid))
            conn.commit()
        finally:
            conn.close()
        after = self.http.get('/v1/work-items', headers=bearer(token))
        self.assertEqual(after.status, 200, '令牌未被撤销，请求本身仍应 200')
        self.assertNotIn(item_id, self._list_ids(after.payload),
                         '停用后不得再见到 T1 行；ids=%s' % self._list_ids(after.payload))
        single = self.http.get('/v1/work-items/%s' % item_id, headers=bearer(token))
        self.assertEqual(single.status, 404, '停用后单条应 404，实际 %s' % single.status)

    def test_TC_SEC_INT_19_account_password_change_and_deletion_invalidate_token(self):
        """TC-SEC-INT-19 属主改密不产生孤儿令牌；账号删除后令牌 JOIN users 失败→401。"""
        uid = self._ensure_user(A_EMAIL)
        token, meta, admin = self._mint(user_id=uid, scopes=['read'])
        session = self._session_for(A_EMAIL)
        changed = session.post('/api/auth/password',
                               {'oldPassword': PASSWORD, 'newPassword': NEW_PASSWORD})
        self.assertEqual(changed.status, 200,
                         '改密应成功，实际 %s body=%s' % (changed.status, changed.payload))
        still = self.http.get('/v1/me', headers=bearer(token))
        self.assertEqual(still.status, 200, '改密本身不应使令牌失效（无孤儿令牌），实际 %s'
                         % still.status)

        conn = self._connect()
        try:
            conn.execute('DELETE FROM users WHERE id=?', (uid,))
            conn.commit()
        finally:
            conn.close()
        gone = self.http.get('/v1/me', headers=bearer(token))
        self.assertEqual(gone.status, 401, '账号删除后令牌应 401，实际 %s body=%s'
                         % (gone.status, gone.payload))
        self.assertEqual(gone.code, 'INVALID_TOKEN', '应为 INVALID_TOKEN，实际 %s' % gone.code)

    def test_TC_SEC_INT_20_idempotency_key_isolated_per_account(self):
        """TC-SEC-INT-20 A/B 同 key 同 body：B 建自己的新行，不拿 A 的回执。"""
        a_uid, b_uid = self._ensure_user(A_EMAIL), self._ensure_user(B_EMAIL)
        ta, _, _ = self._mint(user_id=a_uid, scopes=['read', 'write:edit'], label='idem-A')
        tb, _, _ = self._mint(user_id=b_uid, scopes=['read', 'write:edit'], label='idem-B')
        key = 'secint-cross-' + uuid.uuid4().hex
        body = {'title': 'cross-account-idem'}
        ra = self.http.post('/v1/work-items', body, headers=bearer(ta, {'Idempotency-Key': key}))
        rb = self.http.post('/v1/work-items', body, headers=bearer(tb, {'Idempotency-Key': key}))
        self.assertIn(ra.status, (200, 201), 'A 首次应成功，实际 %s' % ra.status)
        self.assertIn(rb.status, (200, 201), 'B 同 key 应建自己的行，实际 %s body=%s'
                      % (rb.status, rb.payload))
        self.assertIsNone(rb.header('X-Gtd-Idempotent-Replay'),
                          'B 不应拿到 A 的幂等重放；headers=%s' % rb.headers)
        id_a = (ra.payload or {}).get('id')
        id_b = (rb.payload or {}).get('id')
        self.assertNotEqual(id_a, id_b, '两账号应各建一行：%s vs %s' % (id_a, id_b))
        self.assertEqual((self._fetch_row('work_items', id_b) or {}).get('owner_id'), b_uid,
                         'B 新行归属应为 B')

    def test_TC_SEC_INT_21_soft_deleted_single_get_is_404(self):
        """TC-SEC-INT-21 软删后单条 GET→404，与列表一致，不泄露存在性。"""
        uid = self._ensure_user(A_EMAIL)
        # 带 mcp 传输位：本条除 REST 外还要经 MCP search 复核"不泄露存在性"。
        token, meta, admin = self._mint(user_id=uid,
                                        scopes=['read', 'mcp', 'write:edit', 'write:delete'])
        title = 'soft-hide-secint'
        rid = self._insert_item(self._session_for(A_EMAIL), title)
        cur = self._fetch_row('work_items', rid)

        # 搜索也是一条可见性通道：先证明它本来能命中（防假绿）。
        before = self.http.get('/v1/search?q=%s' % title, headers=bearer(token))
        self.assertEqual(before.status, 200, '软删前搜索应 200，实际 %s' % before.status)
        self.assertIn(rid, [x.get('id') for x in (before.payload or {}).get('results', [])],
                      '夹具：软删前搜索应命中该行（否则后面的"不回显"是假绿）')

        r = self.http.request('DELETE', '/v1/work-items/%s' % rid,
                              headers=bearer(token, {'If-Match': 'W/"%s-%s"' % (rid, cur.get('version'))}))
        self.assertIn(r.status, (200, 204), '软删应成功，实际 %s' % r.status)
        single = self.http.get('/v1/work-items/%s' % rid, headers=bearer(token))
        self.assertEqual(single.status, 404, '软删后单条应 404，实际 %s body=%s'
                         % (single.status, single.payload))
        listing = self.http.get('/v1/work-items', headers=bearer(token))
        self.assertNotIn(rid, self._list_ids(listing.payload), '列表也应不含该行')

        # 单条 404 但搜索仍回显 = 换个门泄露存在性，与判据"不泄露存在性"冲突。
        after_search = self.http.get('/v1/search?q=%s' % title, headers=bearer(token))
        self.assertNotIn(rid, [x.get('id') for x in (after_search.payload or {}).get('results', [])],
                         '软删后 REST 搜索不得回显该行')
        mcp_search = self.http.rpc('tools/call',
                                   {'name': 'search', 'arguments': {'q': title}}, token=token)
        self.assertIsNone(mcp_search.payload.get('error'), 'MCP 搜索应成功；%s' % mcp_search.payload)
        text = mcp_search.payload['result']['content'][0]['text']
        self.assertNotIn(rid, [x.get('id') for x in json.loads(text)],
                         '软删后 MCP 搜索不得回显该行')

        # 归档闸门不能把恢复路径一起堵死：restore 走 store.update_versioned，
        # 不经过 h_work_item / _visible_row，软删行仍必须可恢复。
        deleted = self._fetch_row('work_items', rid)
        restored = self.http.post('/v1/work-items/%s/restore' % rid,
                                  headers=bearer(token, {'If-Match': 'W/"%s-%s"'
                                                         % (rid, deleted.get('version'))}))
        self.assertIn(restored.status, (200, 201), '恢复应成功，实际 %s body=%s'
                      % (restored.status, restored.payload))
        self.assertFalse(self._fetch_row('work_items', rid).get('archived_at'),
                         '恢复后 archived_at 应清空')
        self.assertEqual(self.http.get('/v1/work-items/%s' % rid, headers=bearer(token)).status,
                         200, '恢复后单条应重新可见')

    def test_TC_SEC_INT_22_backup_export_has_no_token_plaintext(self):
        """TC-SEC-INT-22 创建并使用令牌后：app_logs + app.jsonl + 备份 JSON 三处 gtd_pat_ 命中全 0。"""
        token, meta, admin = self._mint(scopes=['read', 'write:edit'])
        r = self.http.post('/v1/work-items', {'title': 'backup-grep-secint'},
                           headers=bearer(token))
        self.assertIn(r.status, (200, 201), '写应成功，实际 %s' % r.status)
        self._assert_no_plaintext_anywhere(token)
        # 额外点破 §15.3④ 的耦合：app_logs 在 BACKUP_TABLES，故审计写明文会污染备份。
        self.assertIn('app_logs', store.BACKUP_TABLES,
                      'app_logs 应在 BACKUP_TABLES —— 这正是必须实测备份的原因')
        self.assertNotIn('api_tokens', store.BACKUP_TABLES,
                         'api_tokens（含散列）不得进备份')

    def test_TC_SEC_INT_23_all_origin_variants_blocked_no_origin_works(self):
        """TC-SEC-INT-23 三种 Origin（含 null 与同源）全 403；无 Origin 正常；无 ACAO。"""
        token, meta, admin = self._mint(scopes=['read'])
        for origin in ('https://evil.test', 'http://127.0.0.1:8790', 'null',
                       'http://127.0.0.1:%d' % self.api_port):
            r = self.http.get('/v1/me', headers=bearer(token, {'Origin': origin}))
            self.assertEqual(r.status, 403,
                             'Origin=%r 应 403（出现即拒），实际 %s body=%s'
                             % (origin, r.status, r.payload))
            self.assertEqual(r.code, 'CROSS_ORIGIN_BLOCKED', 'Origin=%r 错误码实际 %s'
                             % (origin, r.code))
            self.assertFalse(r.has_acao(), 'Origin=%r 响应不得有 ACAO' % origin)
        ok = self.http.get('/v1/me', headers=bearer(token))
        self.assertEqual(ok.status, 200, '无 Origin 的脚本请求应正常，实际 %s body=%s'
                         % (ok.status, ok.payload))

    def test_TC_SEC_INT_24_scope_naming_consistent_across_rest_and_mcp(self):
        """TC-SEC-INT-24 同一套 scope 名：REST 只认 write:*；MCP 写=write:* 且 mcp（§16.12.1）。"""
        uid = self._ensure_user(A_EMAIL)
        session = self._session_for(A_EMAIL)

        def fresh_item():
            rid = self._insert_item(session, 'scope-target')
            return rid, self._fetch_row('work_items', rid).get('version')

        # 主用例：三档齐全，REST 与 MCP 都能写，按同一套 scope 名判定。
        full, meta, _ = self._mint(user_id=uid, scopes=['read', 'mcp', 'write:edit'])
        rid, ver = fresh_item()
        rest = self.http.patch('/v1/work-items/%s' % rid, {'title': 'rest-write'},
                               headers=bearer(full, {'If-Match': 'W/"%s-%s"' % (rid, ver)}))
        self.assertIn(rest.status, (200, 201), 'REST PATCH 应成功，实际 %s body=%s'
                      % (rest.status, rest.payload))
        ver2 = self._fetch_row('work_items', rid).get('version')
        mcp = self.http.rpc('tools/call',
                            {'name': 'update_work_item',
                             'arguments': {'id': rid, 'expectedVersion': ver2,
                                           'title': 'mcp-write'}},
                            token=full)
        self.assertEqual(mcp.status, 200, 'MCP 传输层应可达，实际 %s body=%s'
                         % (mcp.status, mcp.payload))
        self.assertIsNone(mcp.payload.get('error'), 'MCP 写应成功；实际 %s' % mcp.payload)

        # 反向 (a)：有 write:edit 无 mcp → REST 可写、MCP 传输层拒绝。
        no_mcp, _, _ = self._mint(user_id=uid, scopes=['read', 'write:edit'], label='no-mcp')
        rid2, ver3 = fresh_item()
        rest2 = self.http.patch('/v1/work-items/%s' % rid2, {'title': 'rest-only'},
                                headers=bearer(no_mcp, {'If-Match': 'W/"%s-%s"' % (rid2, ver3)}))
        self.assertIn(rest2.status, (200, 201), '无 mcp 时 REST 仍应可写，实际 %s' % rest2.status)
        mcp2 = self.http.rpc('tools/call',
                             {'name': 'update_work_item',
                              'arguments': {'id': rid2,
                                            'expectedVersion': self._fetch_row('work_items', rid2).get('version'),
                                            'title': 'should-be-blocked'}},
                             token=no_mcp)
        self.assertEqual(mcp2.status, 403, '无 mcp 传输位时 /mcp 应 403，实际 %s body=%s'
                         % (mcp2.status, mcp2.payload))

        # 反向 (b)：有 mcp 无 write:* → 写工具被裁剪，调用被拒（INSUFFICIENT_SCOPE）。
        no_write, _, _ = self._mint(user_id=uid, scopes=['read', 'mcp'], label='mcp-no-write')
        lst = self.http.rpc('tools/list', token=no_write)
        self.assertEqual(lst.status, 200, 'tools/list 应可达，实际 %s' % lst.status)
        names = rpc_tool_names(lst)
        for write_tool in ('update_work_item', 'complete_work_item', 'archive_work_item',
                           'delete_work_item', 'restore_work_item'):
            self.assertNotIn(write_tool, names, '无 write:* 时写工具须裁剪；names=%s' % names)
        self.assertIn('list_work_items', names, '只读工具仍应可见；names=%s' % names)
        rid3, _ = fresh_item()
        call = self.http.rpc('tools/call',
                             {'name': 'update_work_item',
                              'arguments': {'id': rid3, 'expectedVersion': 1, 'title': 'x'}},
                             token=no_write)
        self.assertEqual(rpc_gtd_code(call), 'INSUFFICIENT_SCOPE',
                         '无 write:* 的 MCP 写调用须被拒；resp=%s' % call.payload)

    def test_TC_SEC_INT_25_token_limit_counts_only_active_unexpired(self):
        """TC-SEC-INT-25 10 个上限按 revoked_at IS NULL AND (expires_at IS NULL OR >now) 计；撤销释放；轮换不超限。"""
        uid = self._ensure_user(A_EMAIL)
        limit = store.MAX_TOKENS_PER_USER if hasattr(store, 'MAX_TOKENS_PER_USER') else 10
        self.assertEqual(limit, 10, '上限应为 10；实际 %s' % limit)
        minted = []
        for i in range(10):
            token, meta, _ = self._mint(user_id=uid, scopes=['read'], label='limit-%d' % i)
            minted.append((token, meta))
        over = self._admin_http().post('/api/admin/api-tokens',
                                       {'userId': uid, 'label': 'over-limit', 'scopes': ['read']})
        self.assertEqual(over.status, 409, '第 11 个应 409，实际 %s body=%s'
                         % (over.status, over.payload))
        self.assertEqual((over.payload or {}).get('error', {}).get('code'), 'TOKEN_LIMIT_REACHED',
                         '应为 TOKEN_LIMIT_REACHED；实际 %s' % over.payload)

        # 撤销释放名额。
        tid = minted[0][1].get('id') or minted[0][1].get('tokenId')
        rev = self._admin_http().request('DELETE', '/api/admin/api-tokens/%s' % tid)
        self.assertIn(rev.status, (200, 204), '撤销应成功，实际 %s' % rev.status)
        after_revoke = self._admin_http().post('/api/admin/api-tokens',
                                               {'userId': uid, 'label': 'after-revoke',
                                                'scopes': ['read']})
        self.assertIn(after_revoke.status, (200, 201),
                      '撤销后应能新签，实际 %s body=%s' % (after_revoke.status, after_revoke.payload))

        # 轮换在满 10 个时不得触发瞬时超限（B3-D-04 判据）。
        active = self._admin_http().get('/api/admin/api-tokens?userId=%s' % uid)
        rows = (active.payload or {}).get('tokens', [])
        active_ids = [r['id'] for r in rows if not r.get('revoked')]
        self.assertEqual(len(active_ids), 10, '夹具：轮换前应有 10 个有效令牌；实际 %d'
                         % len(active_ids))
        rot = self._admin_http().post('/api/admin/api-tokens/%s/rotate' % active_ids[0], {})
        self.assertIn(rot.status, (200, 201),
                      '满额轮换应成功（先撤后插、不瞬时超限），实际 %s body=%s'
                      % (rot.status, rot.payload))

        # 过期但未撤销的令牌不应计入名额。
        conn = self._connect()
        try:
            conn.execute("UPDATE api_tokens SET expires_at='2000-01-01T00:00:00Z' "
                         "WHERE revoked_at IS NULL")
            conn.commit()
        finally:
            conn.close()
        expired_free = self._admin_http().post('/api/admin/api-tokens',
                                               {'userId': uid, 'label': 'after-expired',
                                                'scopes': ['read']})
        self.assertIn(expired_free.status, (200, 201),
                      '过期令牌不应占名额，新签应成功；实际 %s body=%s'
                      % (expired_free.status, expired_free.payload))


if __name__ == '__main__':
    unittest.main(verbosity=2)
