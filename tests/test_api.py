"""端到端接口测试：真起一个服务进程外的实例，连真 SQLite 库。

为什么必须是"真服务 + 真库"而不是打桩：
本项目的核心风险不是某个函数算错，而是**边界是否真的成立** ——
越权能不能改到别人的数据、被权限拦下时前端能否看出"没改成"、
JSON 字段来回一趟是否还是对象、静态托管会不会把源码目录漏出去。
这些只有跑起来才看得见（云版本历史上正是靠真跑才发现"成员归属被写错"和
"静态托管把 .smoke/ 一起公开"）。

跑法：
    python tests/test_api.py            # 直接跑
    python -m unittest discover tests   # 或走 unittest

只用标准库：http.client + unittest + tempfile。
"""

import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
import http.client
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pyapp import db, security                     # noqa: E402
from pyapp.config import Config                    # noqa: E402
from pyapp.httpd import free_port, make_server     # noqa: E402
from pyapp.llm import strip_code_fence             # noqa: E402

_SERVER = None
_APP = None
_THREAD = None
_PORT = None
_DATA_DIR = None
# 模拟容器内的 /certs 挂载点。**测试绝不能写真实的 /certs** ——
# 那会污染线上挂载，而且 CI 上根本没有这个目录。
_CERT_DIR = None

# 实例管理员（.env 的 GTD_ADMIN_EMAILS 里那种账号）。用例里建一次复用。
# 注意：这个账号在库里**不是**系统管理员，它是"环境变量兜底"那条路径的样本；
# 库内系统管理员由 admin_client() 单独建（两者共存正是 Q-3 的 OR 兜底）。
ADMIN_EMAIL = 'instance-admin@example.com'
ADMIN_PASSWORD = 'test-pass-1234'
_ADMIN_CLIENT = None
_ENV_ADMIN_CLIENT = None


def setUpModule():
    global _SERVER, _APP, _THREAD, _PORT, _DATA_DIR, _CERT_DIR
    _DATA_DIR = tempfile.mkdtemp(prefix='gtd4teams-test-')
    _CERT_DIR = tempfile.mkdtemp(prefix='gtd4teams-certs-')
    _PORT = free_port()
    config = Config(host='127.0.0.1', port=_PORT, data_dir=_DATA_DIR,
                    admin_emails=[ADMIN_EMAIL], cert_dir=_CERT_DIR)
    _SERVER, _APP = make_server(config)
    _THREAD = threading.Thread(target=_SERVER.serve_forever, daemon=True)
    _THREAD.start()


def tearDownModule():
    if _SERVER:
        _SERVER.shutdown()
        _SERVER.server_close()


# --------------------------------------------------------------------------
# 最小 HTTP 客户端（带 Cookie 保持）
# --------------------------------------------------------------------------

class Response:
    def __init__(self, status, payload, raw, headers):
        self.status = status
        self.payload = payload
        self.raw = raw
        self.headers = headers

    @property
    def code(self):
        return ((self.payload or {}).get('error') or {}).get('code')

    @property
    def rows(self):
        return (self.payload or {}).get('rows')


class Client:
    def __init__(self, cookie=None):
        self.cookie = cookie

    def request(self, method, path, body=None, *, headers=None, raw_body=None, keep_cookie=True):
        conn = http.client.HTTPConnection('127.0.0.1', _PORT, timeout=20)
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
        status = resp.status
        content_type = resp.getheader('Content-Type') or ''
        set_cookie = resp.getheader('Set-Cookie')
        conn.close()

        if keep_cookie and set_cookie:
            token = set_cookie.split(';')[0]
            self.cookie = '' if 'gtd_session=;' in token + ';' else token

        parsed = None
        if 'application/json' in content_type and data:
            parsed = json.loads(data.decode('utf-8'))
        return Response(status, parsed, data, {'content-type': content_type,
                                               'set-cookie': set_cookie,
                                               'cache-control': resp.getheader('Cache-Control')})

    def get(self, path, **kwargs):
        return self.request('GET', path, **kwargs)

    def post(self, path, body=None, **kwargs):
        return self.request('POST', path, body if body is not None else {}, **kwargs)

    # -- 常用动作 ---------------------------------------------------------
    # 注意：没有 register()。自助注册已随 CR-2026-001 下线（端点 → 404），
    # 测试账号一律走 _create_user_directly() 直接落库 + login() 拿 Cookie。

    def login(self, email, password=ADMIN_PASSWORD):
        return self.post('/api/auth/login', {'email': email, 'password': password})

    def insert(self, table, payload):
        return self.post('/api/rows', {'table': table, 'op': 'insert', 'payload': payload})

    def update(self, table, row_id, payload):
        return self.post('/api/rows', {'table': table, 'op': 'update', 'id': row_id,
                                       'payload': payload})

    def delete(self, table, row_id):
        return self.post('/api/rows', {'table': table, 'op': 'delete', 'id': row_id})


def _create_user_directly(email, password=ADMIN_PASSWORD, *, is_system_admin=False,
                         must_change_password=False, data_dir=None, name=''):
    """直接落库建一个测试账号，返回 userId。

    为什么必须这样建号：自助注册端点已下线（CR-2026-001），
    而测试又需要账号。走"直接写库"而不是"通过某一个接口建号"，是为了让
    夹具与业务实现解耦 —— 否则建号接口一改，几十条无关用例会一起变红。
    """
    path = str(Path(data_dir or _DATA_DIR) / 'gtd4teams.db')
    db.init_db(path)
    conn = db.connect(path)
    try:
        user_id = security.new_user_id()
        now = db.now_iso()
        conn.execute(
            'INSERT INTO users (id, email, name, password_hash, created_at, updated_at, '
            'is_system_admin, must_change_password) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
            (user_id, email, name or email.split('@')[0], security.hash_password(password),
             now, now, 1 if is_system_admin else 0, 1 if must_change_password else 0),
        )
        conn.commit()
    finally:
        conn.close()
    return user_id


def fresh_user(email, password=ADMIN_PASSWORD, *, is_system_admin=False,
               must_change_password=False, data_dir=None):
    """建一个新账号并登录，返回 (client, userId)。

    每个用例用独立账号，避免互相污染。会话一定是走**真实登录接口**拿到的 ——
    手工造会话会让"登录路径本身坏了"这类故障测不出来。
    """
    user_id = _create_user_directly(email, password, is_system_admin=is_system_admin,
                                    must_change_password=must_change_password,
                                    data_dir=data_dir)
    client = Client()
    resp = client.login(email, password)
    assert resp.status == 200, f'登录失败: {resp.status} {resp.payload}'
    return client, user_id


def admin_client():
    """**库内**系统管理员会话（users.is_system_admin = 1）。

    这是建号 / 授权 / 改 AI 配置的主路径。环境变量兜底那条路径由
    env_admin_client() 单独覆盖（两者共存就是 Q-3 的 OR 兜底）。
    """
    global _ADMIN_CLIENT
    if _ADMIN_CLIENT is None:
        _ADMIN_CLIENT, _ = fresh_user('sysadmin@example.com', is_system_admin=True)
    return _ADMIN_CLIENT


def env_admin_client():
    """**仅在 GTD_ADMIN_EMAILS 白名单里**的账号（库内不是系统管理员）。

    它守着"升级后原白名单管理员不能突然失去 AI 配置权限"这条过渡期承诺。
    """
    global _ENV_ADMIN_CLIENT
    if _ENV_ADMIN_CLIENT is None:
        _ENV_ADMIN_CLIENT, _ = fresh_user(ADMIN_EMAIL)
    return _ENV_ADMIN_CLIENT


#: 证书用例共用的普通成员会话（建一次复用；重复建号会撞邮箱唯一索引）。
_CERT_MEMBER = None


def cert_member_client():
    global _CERT_MEMBER
    if _CERT_MEMBER is None:
        _CERT_MEMBER, _ = fresh_user('cert-member@example.com')
    return _CERT_MEMBER


def _drop_setting(key):
    """测试收尾用：直接把某条 settings 删掉，避免污染后续用例。"""
    conn = db.connect(db_path())
    try:
        conn.execute('DELETE FROM settings WHERE key = ?', (key,))
        conn.commit()
    finally:
        conn.close()


class CommitThenFailConnection:
    """把连接包一层：commit() 真实落库之后，紧接着抛一个写锁错误。

    这是最刁钻、也最真实的一类失败：提交**已经发生**，但调用方看到的是异常。
    用它来验证 httpd 的写锁重试不会把已经提交的写重放一遍。
    """

    def __init__(self, conn, calls):
        self._conn = conn
        self._calls = calls

    def commit(self):
        self._conn.commit()                      # 真实提交，数据已经在库里
        self._calls.append(True)
        raise sqlite3.OperationalError('database is locked')

    def __getattr__(self, name):                 # 其余一切都原样委托
        return getattr(self._conn, name)


def _read_one_response(sock):
    """从裸 socket 上读一份完整响应，返回 (状态行, 头, body)。

    这里必须自己解析：要验证"同一个连接上的第二个请求有没有被污染"、
    以及"响应体里有没有被塞进第二份 HTTP 响应"，只能看原始字节。
    """
    buf = b''
    while b'\r\n\r\n' not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise AssertionError(f'连接被提前关闭，已收到：{buf!r}')
        buf += chunk
    head, rest = buf.split(b'\r\n\r\n', 1)
    lines = head.split(b'\r\n')
    headers = {}
    for line in lines[1:]:
        key, _, value = line.partition(b':')
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get(b'content-length') or 0)
    while len(rest) < length:
        chunk = sock.recv(4096)
        if not chunk:
            break
        rest += chunk
    return lines[0], headers, rest


# --------------------------------------------------------------------------
# 用例
# --------------------------------------------------------------------------

class BackendTest(unittest.TestCase):
    def setUp(self):
        security.reset_limits()

    # ---- 基础 ----

    def test_01_health_reports_local_mode(self):
        resp = Client().get('/api/health')
        self.assertEqual(resp.status, 200)
        self.assertTrue(resp.payload['ok'])
        self.assertEqual(resp.payload['storage'], 'sqlite')
        self.assertTrue(resp.payload['local'])

    def test_02_auth_required_for_workspace(self):
        resp = Client().get('/api/workspace')
        self.assertEqual(resp.status, 401)
        self.assertEqual(resp.code, 'UNAUTHENTICATED')

    # ---- 登录 / 会话 ----

    def test_03_login_creates_session_and_profile(self):
        client, user_id = fresh_user('alpha@example.com')
        self.assertTrue(client.cookie.startswith('gtd_session='))
        session = client.get('/api/auth/session')
        self.assertEqual(session.payload['user']['id'], user_id)
        # 两个新字段是加法式扩展：session/profile/me 都要有（前端据此跳改密页）
        self.assertIs(session.payload['user']['mustChangePassword'], False)
        self.assertIs(session.payload['user']['isSystemAdmin'], False)
        profile = client.get('/api/auth/profile')
        self.assertEqual(profile.payload['email'], 'alpha@example.com')
        self.assertEqual(profile.payload['id'], user_id)
        self.assertIn('mustChangePassword', profile.payload)
        self.assertIn('isSystemAdmin', profile.payload)

    def test_04_self_service_registration_is_gone(self):
        """自助注册与其配套的邮箱探测接口都已从路由表移除（→ 404）。

        为什么是 404 而不是 403：403 表达"你暂时没被允许"，404 表达"没有这个能力"。
        保留路由 + 一个恒 403 的空壳，等于让"打开开关就恢复注册"这条路继续存在，
        而 `check-email` 在注册下线后只剩"匿名批量探测邮箱是否注册"这一个用途。
        """
        resp = Client().post('/api/auth/register',
                             {'email': 'nobody@example.com', 'password': 'test-pass-1234'})
        self.assertEqual(resp.status, 404)
        self.assertEqual(resp.code, 'NOT_FOUND')
        self.assertIsNone(resp.headers['set-cookie'], '已下线的注册接口仍然下发了会话 Cookie')

        check = Client().get('/api/auth/check-email?email=nobody@example.com')
        self.assertEqual(check.status, 404)
        self.assertEqual(check.code, 'NOT_FOUND')
        self.assertNotIn('isExistingUser', json.dumps(check.payload))

        # 端点不存在 ⇒ 账号也不该被创建出来
        conn = db.connect(db_path())
        try:
            self.assertIsNone(conn.execute(
                'SELECT 1 FROM users WHERE email = ?', ('nobody@example.com',)).fetchone())
        finally:
            conn.close()

    def test_05_login_validates_input(self):
        resp = Client().post('/api/auth/login', {})
        self.assertEqual(resp.status, 400)
        self.assertEqual(resp.code, 'MISSING_CREDENTIALS')

        # 弱口令由改密接口校验（注册接口已下线，口令强度规则仍在服务端）
        client, _ = fresh_user('weak@example.com')
        weak = client.post('/api/auth/password',
                           {'oldPassword': ADMIN_PASSWORD, 'newPassword': '123'})
        self.assertEqual(weak.status, 400)
        self.assertEqual(weak.code, 'WEAK_PASSWORD')

    def test_06_login_wrong_password_then_success(self):
        fresh_user('login@example.com')
        bad = Client().login('login@example.com', 'wrong-password')
        self.assertEqual(bad.status, 401)
        # 不区分"邮箱不存在"与"密码错误"，避免接口变成账号枚举器
        unknown = Client().login('nobody@example.com', 'whatever-1234')
        self.assertEqual(unknown.status, 401)
        self.assertEqual(bad.code, unknown.code)

        ok = Client().login('login@example.com')
        self.assertEqual(ok.status, 200)
        self.assertEqual(ok.payload['user']['email'], 'login@example.com')

    def test_07_logout_invalidates_session(self):
        client, _ = fresh_user('logout@example.com')
        self.assertEqual(client.post('/api/auth/logout').status, 200)
        self.assertIsNone(client.get('/api/auth/session').payload['user'])
        self.assertEqual(client.get('/api/workspace').status, 401)

    def test_08_login_is_rate_limited(self):
        fresh_user('throttle@example.com')
        codes = []
        for _ in range(12):
            codes.append(Client().login('throttle@example.com', 'nope-12345').status)
        self.assertIn(429, codes)
        security.reset_limits()

    def test_09_change_password_requires_old_and_invalidates_other_sessions(self):
        client, _ = fresh_user('chpw@example.com')
        other = Client()
        self.assertEqual(other.login('chpw@example.com').status, 200)

        wrong = client.post('/api/auth/password',
                            {'oldPassword': 'nope', 'newPassword': 'brand-new-1234'})
        self.assertEqual(wrong.status, 401)
        self.assertEqual(wrong.code, 'INVALID_OLD_PASSWORD')

        ok = client.post('/api/auth/password',
                         {'oldPassword': 'test-pass-1234', 'newPassword': 'brand-new-1234'})
        self.assertEqual(ok.status, 200)
        # 改密后其它会话必须失效，当前会话保留
        self.assertEqual(other.get('/api/workspace').status, 401)
        self.assertEqual(client.get('/api/workspace').status, 200)
        self.assertEqual(Client().login('chpw@example.com', 'brand-new-1234').status, 200)
        self.assertEqual(Client().login('chpw@example.com', 'test-pass-1234').status, 401)

    # ---- 团队生命周期 ----

    def test_10_create_team_registers_owner_atomically(self):
        client, user_id = fresh_user('owner@example.com')
        resp = client.post('/api/teams/create',
                           {'name': '运维一组', 'mission': '保障生产稳定', 'joinCode': 'TEAM01'})
        self.assertEqual(resp.status, 201)
        team = resp.payload['team']
        self.assertEqual(team['owner_id'], user_id)
        self.assertEqual(team['join_code'], 'TEAM01')

        workspace = client.get('/api/workspace').payload
        self.assertEqual([t['id'] for t in workspace['teams']], [team['id']])
        # 创建者必须同时拿到 owner 成员行，否则团队对他自己都不可见
        members = [m for m in workspace['members'] if m['team_id'] == team['id']]
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0]['role'], 'owner')
        self.assertEqual(members[0]['user_id'], user_id)

    def test_11_join_by_code_and_invalid_code(self):
        owner, _ = fresh_user('owner2@example.com')
        team = owner.post('/api/teams/create',
                          {'name': '网络组', 'joinCode': 'NET123'}).payload['team']

        joiner, joiner_id = fresh_user('joiner@example.com')
        bad = joiner.post('/api/teams/join', {'code': 'WRONG9'})
        self.assertEqual(bad.status, 404)
        self.assertEqual(bad.code, 'INVALID_JOIN_CODE')

        joined = joiner.post('/api/teams/join', {'code': 'net123'})   # 大小写不敏感
        self.assertEqual(joined.status, 200)
        self.assertEqual(joined.payload['teamId'], team['id'])

        # 重复加入幂等，不产生第二行成员记录
        again = joiner.post('/api/teams/join', {'code': 'NET123'})
        self.assertEqual(again.status, 200)
        members = [m for m in owner.get('/api/workspace').payload['members']
                   if m['team_id'] == team['id']]
        self.assertEqual(len(members), 2)
        self.assertEqual(len({m['user_id'] for m in members}), 2)
        self.assertEqual(members[1]['user_id'], joiner_id)

    def test_12_join_code_is_unique_per_team(self):
        owner, _ = fresh_user('owner3@example.com')
        self.assertEqual(
            owner.post('/api/teams/create', {'name': 'A', 'joinCode': 'SAME01'}).status, 201)
        clash = owner.post('/api/teams/create', {'name': 'B', 'joinCode': 'same01'})
        self.assertEqual(clash.status, 409)
        self.assertEqual(clash.code, 'JOIN_CODE_TAKEN')

    def test_13_archived_team_cannot_be_joined(self):
        owner, _ = fresh_user('owner4@example.com')
        team = owner.post('/api/teams/create',
                          {'name': '归档组', 'joinCode': 'ARC001'}).payload['team']
        owner.update('teams', team['id'], {'archived': True})
        other, _ = fresh_user('late@example.com')
        self.assertEqual(other.post('/api/teams/join', {'code': 'ARC001'}).status, 404)

    # ---- 权限边界（本项目的安全核心） ----

    def test_14_personal_items_are_isolated(self):
        alice, alice_id = fresh_user('alice@example.com')
        bob, bob_id = fresh_user('bob@example.com')

        created = alice.insert('work_items',
                               {'title': '给 Alice 的私事', 'team_id': None, 'status': 'intake'})
        self.assertEqual(created.status, 200)
        item = created.rows[0]
        self.assertEqual(item['owner_id'], alice_id)

        self.assertEqual([i['id'] for i in alice.get('/api/workspace').payload['items']],
                         [item['id']])
        self.assertEqual(bob.get('/api/workspace').payload['items'], [])
        # 别人的个人条目：改不动、删不掉，且返回 0 行（不是报错）
        self.assertEqual(bob.update('work_items', item['id'], {'title': '篡改'}).rows, [])
        self.assertEqual(bob.delete('work_items', item['id']).rows, [])
        self.assertEqual(alice.get('/api/workspace').payload['items'][0]['title'],
                         '给 Alice 的私事')

    def test_15_team_items_are_shared_but_not_public(self):
        owner, _ = fresh_user('team-owner@example.com')
        team = owner.post('/api/teams/create',
                          {'name': '共享组', 'joinCode': 'SHR001'}).payload['team']
        member, _ = fresh_user('team-member@example.com')
        member.post('/api/teams/join', {'code': 'SHR001'})
        outsider, _ = fresh_user('outsider@example.com')

        item = owner.insert('work_items', {
            'title': '生产库慢查询', 'team_id': team['id'],
            'item_type': 'incident', 'status': 'doing', 'priority': 'p1',
        }).rows[0]

        # 成员之间共享
        member_items = member.get('/api/workspace').payload['items']
        self.assertEqual([i['id'] for i in member_items], [item['id']])
        # 成员可以推进团队工作项
        advanced = member.update('work_items', item['id'], {'status': 'review'})
        self.assertEqual(len(advanced.rows), 1)
        self.assertEqual(advanced.rows[0]['status'], 'review')
        # 非成员完全看不见，也改不动
        self.assertEqual(outsider.get('/api/workspace').payload['items'], [])
        self.assertEqual(outsider.update('work_items', item['id'], {'status': 'done'}).rows, [])
        self.assertEqual(
            owner.get('/api/workspace').payload['items'][0]['status'], 'review')

    def test_16_insert_into_foreign_team_is_denied(self):
        owner, _ = fresh_user('owner5@example.com')
        team = owner.post('/api/teams/create',
                          {'name': '私有组', 'joinCode': 'PRV001'}).payload['team']
        outsider, _ = fresh_user('nosy@example.com')
        resp = outsider.insert('work_items', {'title': '硬插进去', 'team_id': team['id']})
        self.assertEqual(resp.status, 403)
        self.assertEqual(resp.code, 'PERMISSION_DENIED')

    def test_17_owner_id_cannot_be_forged(self):
        alice, alice_id = fresh_user('forge-a@example.com')
        bob, bob_id = fresh_user('forge-b@example.com')
        # 客户端塞进来的 owner_id 必须被丢弃，归属只能是操作者本人
        item = alice.insert('work_items',
                            {'title': '归属测试', 'owner_id': bob_id}).rows[0]
        self.assertEqual(item['owner_id'], alice_id)
        self.assertEqual(bob.get('/api/workspace').payload['items'], [])

    def test_18_member_cannot_self_promote(self):
        owner, _ = fresh_user('owner6@example.com')
        team = owner.post('/api/teams/create',
                          {'name': '提权组', 'joinCode': 'ESC001'}).payload['team']
        member, member_id = fresh_user('member2@example.com')
        member.post('/api/teams/join', {'code': 'ESC001'})

        row = [m for m in member.get('/api/workspace').payload['members']
               if m['team_id'] == team['id'] and m['user_id'] == member_id][0]
        # 可以更新自己那一行（例如改显示名），但改 role 会被 WITH CHECK 拦下
        self.assertEqual(len(member.update('team_members', row['id'], {'title': '值班'}).rows), 1)
        self.assertEqual(member.update('team_members', row['id'], {'role': 'owner'}).rows, [])
        roles = {m['user_id']: m['role'] for m in
                 owner.get('/api/workspace').payload['members'] if m['team_id'] == team['id']}
        self.assertEqual(roles[member_id], 'member')

    def test_19_admin_can_manage_members_but_owner_row_is_protected(self):
        owner, owner_id = fresh_user('owner7@example.com')
        team = owner.post('/api/teams/create',
                          {'name': '管理组', 'joinCode': 'ADM001'}).payload['team']
        member, member_id = fresh_user('member3@example.com')
        member.post('/api/teams/join', {'code': 'ADM001'})

        rows = owner.get('/api/workspace').payload['members']
        member_row = [m for m in rows if m['user_id'] == member_id][0]
        owner_row = [m for m in rows if m['user_id'] == owner_id][0]

        self.assertEqual(
            len(owner.update('team_members', member_row['id'],
                             {'role': 'admin', 'capacity': 4}).rows), 1)
        self.assertEqual(
            [m['role'] for m in owner.get('/api/workspace').payload['members']
             if m['id'] == member_row['id']], ['admin'])
        # 所有者不能被移出团队
        self.assertEqual(owner.delete('team_members', owner_row['id']).rows, [])

    def test_20_disabled_member_loses_access_and_cannot_rejoin(self):
        owner, _ = fresh_user('owner8@example.com')
        team = owner.post('/api/teams/create',
                          {'name': '停用组', 'joinCode': 'DIS001'}).payload['team']
        member, member_id = fresh_user('member4@example.com')
        member.post('/api/teams/join', {'code': 'DIS001'})
        self.assertEqual(len(member.get('/api/workspace').payload['teams']), 1)

        row = [m for m in owner.get('/api/workspace').payload['members']
               if m['user_id'] == member_id][0]
        owner.update('team_members', row['id'], {'status': 'inactive'})

        self.assertEqual(member.get('/api/workspace').payload['teams'], [])
        rejoin = member.post('/api/teams/join', {'code': 'DIS001'})
        self.assertEqual(rejoin.status, 403)
        self.assertEqual(rejoin.code, 'MEMBER_DISABLED')

    def test_21_progress_log_delete_requires_ownership_or_admin(self):
        owner, _ = fresh_user('owner9@example.com')
        team = owner.post('/api/teams/create',
                          {'name': '日志组', 'joinCode': 'LOG001'}).payload['team']
        member, member_id = fresh_user('member5@example.com')
        member.post('/api/teams/join', {'code': 'LOG001'})

        log = member.insert('progress_logs',
                            {'team_id': team['id'], 'kind': 'progress', 'note': '已修复'}).rows[0]
        owner_log = owner.insert('progress_logs',
                                 {'team_id': team['id'], 'kind': 'progress', 'note': '复核中'}).rows[0]

        self.assertEqual(member.delete('progress_logs', owner_log['id']).rows, [])
        self.assertEqual(len(owner.delete('progress_logs', log['id']).rows), 1)

    # ---- 字段与类型 ----

    def test_22_unknown_fields_are_dropped(self):
        client, _ = fresh_user('fields@example.com')
        item = client.insert('work_items', {
            'title': '字段白名单', 'nosuchcolumn': 'x', 'id': 999,
            'owner_id': 'somebody-else', 'created_at': '1999-01-01T00:00:00Z',
        }).rows[0]
        self.assertNotEqual(item['id'], 999)
        self.assertNotIn('nosuchcolumn', item)
        self.assertNotEqual(item['created_at'][:4], '1999')

    def test_23_json_and_scalar_types_round_trip(self):
        client, _ = fresh_user('types@example.com')
        item = client.insert('work_items', {
            'title': '类型往返', 'tags': ['生产', '数据库'],
            'recurrence': {'freq': 'weekly', 'interval': 2},
            'progress': '50', 'flagged': True, 'is_next': False, 'est_hours': '3.5',
            'due_date': '2026-10-01',
        }).rows[0]
        self.assertEqual(item['tags'], ['生产', '数据库'])
        self.assertEqual(item['recurrence'], {'freq': 'weekly', 'interval': 2})
        self.assertEqual(item['progress'], 50)
        self.assertIs(item['flagged'], True)
        self.assertIs(item['is_next'], False)
        self.assertAlmostEqual(item['est_hours'], 3.5)
        self.assertEqual(item['due_date'], '2026-10-01')

        # 从库里再读一次，确认不是只在响应对象里正确
        again = client.get('/api/workspace').payload['items'][0]
        self.assertEqual(again['tags'], ['生产', '数据库'])
        self.assertEqual(again['progress'], 50)
        self.assertIs(again['flagged'], True)

    def test_24_json_columns_survive_direct_db_read(self):
        """直接查库确认存的是 JSON 文本，不是 Python repr（否则前端 JSON.parse 会炸）。"""
        client, _ = fresh_user('jsoncol@example.com')
        team = client.post('/api/teams/create',
                           {'name': 'JSON 组', 'joinCode': 'JSN001'}).payload['team']
        client.update('teams', team['id'], {'board_config': {'doing': 5, 'triage': 2},
                                            'sla_config': {'p1': 4}})
        conn = db.connect(db_path())
        try:
            row = conn.execute(
                'SELECT board_config, sla_config, archived FROM teams WHERE id = ?',
                (team['id'],)
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(json.loads(row['board_config']), {'doing': 5, 'triage': 2})
        self.assertEqual(json.loads(row['sla_config']), {'p1': 4})
        self.assertEqual(row['archived'], 0)          # 布尔落成 0/1，不是 'false' 

    def test_25_team_board_config_round_trip(self):
        client, _ = fresh_user('board@example.com')
        team = client.post('/api/teams/create',
                           {'name': '看板组', 'joinCode': 'BRD001'}).payload['team']
        updated = client.update('teams', team['id'], {'board_config': {'doing': 5, 'triage': 2}})
        self.assertEqual(updated.rows[0]['board_config'], {'doing': 5, 'triage': 2})

    # ---- 复盘 ----

    def test_26_review_upsert_keeps_single_row_per_period(self):
        client, _ = fresh_user('review@example.com')
        first = client.post('/api/reviews/save', {'payload': {
            'team_id': None, 'kind': 'weekly', 'period_key': '2026-W38',
            'notes': '第一版', 'stats': {'done': 3},
        }})
        self.assertEqual(len(first.rows), 1)
        row_id = first.rows[0]['id']

        # 不传 existingId 也要落到同一行（前端 finishReview 依赖这个可观察结果）
        second = client.post('/api/reviews/save', {'payload': {
            'team_id': None, 'kind': 'weekly', 'period_key': '2026-W38',
            'notes': '第二版', 'stats': {'done': 5},
        }})
        self.assertEqual([r['id'] for r in second.rows], [row_id])
        self.assertEqual(second.rows[0]['notes'], '第二版')
        self.assertEqual(second.rows[0]['stats'], {'done': 5})

        reviews = client.get('/api/workspace').payload['reviews']
        self.assertEqual(len(reviews), 1)
        # 不同周期是另一条
        client.post('/api/reviews/save', {'payload': {
            'team_id': None, 'kind': 'weekly', 'period_key': '2026-W39'}})
        self.assertEqual(len(client.get('/api/workspace').payload['reviews']), 2)

    def test_27_review_of_others_is_not_updatable(self):
        alice, _ = fresh_user('rev-a@example.com')
        review = alice.post('/api/reviews/save', {'payload': {
            'team_id': None, 'kind': 'daily', 'period_key': '2026-09-17'}}).rows[0]
        bob, _ = fresh_user('rev-b@example.com')
        self.assertEqual(bob.update('reviews', review['id'], {'notes': '篡改'}).rows, [])

    # ---- 运行日志 ----

    def test_28_logs_accept_anonymous_but_only_owner_can_read(self):
        # 事件名带上前缀：整个测试进程共用一个库，别的用例也会写日志。
        anon = Client()
        resp = anon.post('/api/logs', {
            'level': 'error', 'scope': 'auth', 'event': 'test28.anon',
            'message': '登录失败', 'detail': {'password': 'secret123', 'errCode': 'X1'},
            'page_url': 'http://localhost/', 'user_agent': 'test',
        })
        self.assertEqual(resp.status, 200)

        client, _ = fresh_user('logs@example.com')
        client.post('/api/logs', {'level': 'info', 'event': 'test28.owner', 'message': '启动'})

        conn = db.connect(db_path())
        try:
            rows = conn.execute(
                "SELECT * FROM app_logs WHERE event IN ('test28.anon', 'test28.owner') "
                'ORDER BY id').fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rows), 2)
        # 未登录那条归属为空：谁都读不到，但排障时能直接在库里看到
        self.assertIsNone(rows[0]['owner_id'])
        self.assertEqual(json.loads(rows[0]['detail'])['errCode'], 'X1')
        self.assertEqual(rows[1]['event'], 'test28.owner')

    # ---- 静态托管 ----

    def test_29_static_serves_site_files(self):
        index = Client().get('/')
        self.assertEqual(index.status, 200)
        self.assertIn('text/html', index.headers['content-type'])
        self.assertNotIn(b'workbuddy-cloud-sdk', index.raw)   # 云 SDK 必须已经摘掉
        self.assertIn(b'js/app.js', index.raw)

        script = Client().get('/js/api.js')
        self.assertEqual(script.status, 200)
        self.assertIn('javascript', script.headers['content-type'])
        self.assertIn(b'/api/workspace', script.raw)

        css = Client().get('/css/app.css')
        self.assertEqual(css.status, 200)
        self.assertIn('text/css', css.headers['content-type'])

    def test_30_static_does_not_expose_non_site_paths(self):
        """白名单之外一律 404 —— 云版本正是把测试夹具与项目笔记整个公开过。"""
        from urllib.parse import quote

        for path in ('/pyapp/db.py', '/server.py', '/tests/test_api.py',
                     '/.smoke/render-test.mjs', '/.workbuddy/memory/MEMORY.md',
                     '/data/gtd4teams.db', '/README.md',
                     '/' + quote('docs/AI能力设计.md'),
                     # 目录穿越的各种写法：白名单第一段合法，但必须逃不出白名单子树
                     '/js/../server.py', '/css/../pyapp/db.py',
                     '/js/%2e%2e/server.py', '/js/..%5Cserver.py',
                     '/js/../tests/test_api.py', '/../server.py',
                     '//server.py', '/js//api.js'):
            resp = Client().get(path)
            self.assertEqual(resp.status, 404, f'{path} 不应可访问')
            self.assertNotIn(b'GtdServer', resp.raw)
            self.assertNotIn(b'CREATE TABLE', resp.raw)

    def test_31_static_has_no_directory_listing(self):
        resp = Client().get('/js/')
        self.assertEqual(resp.status, 404)
        self.assertNotIn(b'api.js', resp.raw)

    # ---- 协议边界 ----

    def test_32_write_requires_json_content_type(self):
        client, _ = fresh_user('proto@example.com')
        resp = client.request('POST', '/api/rows',
                              raw_body=b'table=work_items',
                              headers={'Content-Type': 'application/x-www-form-urlencoded'})
        self.assertEqual(resp.status, 415)

    def test_33_cross_origin_write_is_blocked(self):
        client, _ = fresh_user('csrf@example.com')
        resp = client.post('/api/rows', {'table': 'work_items', 'op': 'insert',
                                         'payload': {'title': 'x'}},
                           headers={'Origin': 'https://evil.example'})
        self.assertEqual(resp.status, 403)
        self.assertEqual(resp.code, 'CROSS_ORIGIN_BLOCKED')

    def test_34_unknown_api_and_method(self):
        self.assertEqual(Client().get('/api/nope').status, 404)
        self.assertEqual(Client().get('/api/rows').status, 404)      # 该接口只接受 POST

    def test_35_unsupported_table_is_rejected(self):
        client, _ = fresh_user('tables@example.com')
        denied = client.insert('users', {'email': 'x@y.com'})
        self.assertEqual(denied.status, 404)
        self.assertEqual(denied.code, 'UNKNOWN_TABLE')

    def test_36_no_cors_headers(self):
        resp = Client().get('/api/health', headers={'Origin': 'https://evil.example'})
        self.assertEqual(resp.status, 200)
        conn = http.client.HTTPConnection('127.0.0.1', _PORT, timeout=10)
        conn.request('GET', '/api/health')
        headers = conn.getresponse()
        self.assertIsNone(headers.getheader('Access-Control-Allow-Origin'))
        conn.close()

    # ---- 备份 / AI ----

    def test_37_export_requires_login(self):
        """导出接口的形状与门槛。

        注意这里**不再**断言"响应含 users 表"：
        修复前 HTTP 导出复用了 CLI 的整库导出，于是任何登录账号都能拿到
        全库 users（含 password_hash）与 settings（含 llm_api_key）。
        users 现在仍然出现在结果里，但只有当前账号自己那一行、且不含凭据列，
        详见 test_export_does_not_leak_other_users_or_api_key。
        """
        self.assertEqual(Client().get('/api/export').status, 401)
        client, _ = fresh_user('export@example.com')
        payload = client.get('/api/export').payload
        self.assertEqual(payload['format'], 'gtd4teams-backup')
        self.assertIn('teams', payload['tables'])
        self.assertIn('work_items', payload['tables'])

    def test_38_llm_reports_not_configured(self):
        client, _ = fresh_user('llm@example.com')
        status = client.get('/api/llm/config')
        self.assertEqual(status.status, 200)
        self.assertFalse(status.payload['configured'])
        chat = client.post('/api/llm/chat', {'messages': [{'role': 'user', 'content': 'hi'}]})
        self.assertEqual(chat.status, 400)
        self.assertEqual(chat.code, 'LLM_NOT_CONFIGURED')

    def test_39_llm_config_never_echoes_key(self):
        # 改 AI 配置要求实例管理员（见 test_llm_config_requires_admin）
        client = admin_client()
        saved = client.post('/api/llm/config', {
            'baseUrl': 'https://api.deepseek.com/v1', 'apiKey': 'sk-topsecret-value',
            'model': 'deepseek-chat',
        })
        self.assertEqual(saved.status, 200)
        self.assertTrue(saved.payload['configured'])
        self.assertNotIn('sk-topsecret-value', json.dumps(saved.payload))
        readback = client.get('/api/llm/config')
        self.assertNotIn('sk-topsecret-value', json.dumps(readback.payload))
        self.assertTrue(readback.payload['apiKeySet'])
        # 清掉，避免影响其它用例
        client.post('/api/llm/config', {'apiKey': ''})
        self.assertFalse(client.get('/api/llm/config').payload['configured'])


    def test_40_forged_x_forwarded_for_cannot_bypass_login_throttle(self):
        """未声明信任代理时，X-Forwarded-For 必须被忽略。

        这是"公网直连部署"的安全前提。若采信客户端自填的 XFF，
        攻击者每次请求换一个假 IP，限流键就每次都是新的 —— 登录爆破等于不限速。
        这条用例专门盯住这个退化：每次换一个伪造 IP，必须仍然被限流拦住。
        """
        security.reset_limits()
        try:
            client = Client()
            codes = []
            for i in range(security.MAX_ATTEMPTS + 3):
                resp = client.post(
                    '/api/auth/login',
                    {'email': 'throttle@example.com', 'password': 'wrong-pass-1234'},
                    headers={'X-Forwarded-For': f'10.9.{i // 250}.{i % 250}'},
                )
                codes.append(resp.status)
            self.assertIn(429, codes,
                          f'伪造 X-Forwarded-For 绕过了登录限流，状态码序列={codes}')
        finally:
            security.reset_limits()


    def test_export_does_not_leak_other_users_or_api_key(self):
        """GET /api/export 只能导"我看得见的东西"，不能变成整库拖库入口。

        修复前的实测后果：任何刚注册的账号 GET /api/export 能拿到
        别人的 email 与 password_hash、settings.llm_api_key 明文、
        以及别人的私有工作项 —— 因为 HTTP 侧直接复用了 CLI 的整库导出。
        这里同时守住两个方向：不该出现的绝不出现，自己的数据仍然导得出来
        （防止用"一律返回空"这种假修复把功能改死）。
        """
        alice, alice_id = fresh_user('export-a@example.com')
        bob, _ = fresh_user('export-b@example.com')
        team = bob.post('/api/teams/create',
                        {'name': 'B 的组', 'joinCode': 'EXP001'}).payload['team']
        private = bob.insert('work_items', {'title': 'B 的私事', 'team_id': None}).rows[0]
        shared = bob.insert('work_items',
                            {'title': 'B 的团队事项', 'team_id': team['id']}).rows[0]

        # 直接写库放一个密钥：排除规则不能依赖"密钥是经哪个接口进来的"
        conn = db.connect(db_path())
        try:
            conn.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) "
                         "VALUES ('llm_api_key', 'sk-export-secret', '2026-01-01T00:00:00Z')")
            conn.commit()
        finally:
            conn.close()
        self.addCleanup(_drop_setting, 'llm_api_key')

        mine = alice.insert('work_items', {'title': 'A 自己的事', 'team_id': None}).rows[0]

        resp = alice.get('/api/export')
        self.assertEqual(resp.status, 200)
        payload = resp.payload
        body = resp.raw.decode('utf-8')

        self.assertEqual(payload['format'], 'gtd4teams-backup')
        self.assertIn('tables', payload)

        # ① 别人的账号信息
        self.assertNotIn('export-b@example.com', body, '导出里出现了别人的邮箱')
        # ② 凭据：口令散列 / 密钥 / settings 表
        self.assertNotIn('password_hash', body, '导出里出现了口令散列')
        self.assertNotIn('llm_api_key', body, '导出里出现了 LLM 密钥的键名')
        self.assertNotIn('sk-export-secret', body, '导出里出现了 LLM 密钥明文')
        self.assertNotIn('settings', payload['tables'], 'HTTP 导出不应包含 settings 表')
        # ③ 别人的私有工作项 / 团队工作项 / 团队
        item_ids = [row['id'] for row in payload['tables']['work_items']]
        self.assertNotIn(private['id'], item_ids, '导出里出现了别人的私有工作项')
        self.assertNotIn(shared['id'], item_ids, '导出里出现了别人的团队工作项')
        self.assertEqual([t['id'] for t in payload['tables']['teams']], [],
                         '导出里出现了别人建的团队')
        # ④ 自己的数据必须仍然导出（否则就是"一刀切返回空"的假修复）
        self.assertIn(mine['id'], item_ids, '自己的数据也没导出，等于把导出功能改死')
        # ⑤ users 最多只有自己一行，且只给非敏感列
        users = payload['tables']['users']
        self.assertEqual([u['id'] for u in users], [alice_id],
                         f'users 应当只有当前账号一行，实际={users}')
        self.assertEqual(sorted(users[0].keys()),
                         ['created_at', 'email', 'id', 'name', 'updated_at'],
                         f'users 导出的列里有敏感字段：{sorted(users[0].keys())}')

    def test_export_does_not_leak_privilege_columns(self):
        """两个权限列绝不能进 HTTP 导出（`is_system_admin` / `must_change_password`）。

        今天 `export_visible` 用的是显式 5 列 SELECT，所以**目前不漏**；
        但这条保护没有测试守着 —— 哪天有人为了省事改成 `SELECT *`，
        两个权限列就会跟着进导出响应，而既有的 `password_hash` 断言仍然通过。
        权限拓扑信息属最小暴露范围：导出它是"我能不能给你提权"的地图。
        """
        admin = admin_client()
        body = admin.get('/api/export').raw.decode('utf-8')
        self.assertNotIn('is_system_admin', body, '导出里出现了系统管理员标记')
        self.assertNotIn('must_change_password', body, '导出里出现了强制改密标记')
        self.assertNotIn('password_hash', body, '导出里出现了口令散列')

    def test_export_user_columns_whitelist_is_exact(self):
        """导出列白名单必须**恰好**这 5 列 —— 以后加列时要显式决定"导不导"。"""
        from pyapp import store as store_module

        self.assertEqual(set(store_module.EXPORT_USER_COLUMNS),
                         {'id', 'email', 'name', 'created_at', 'updated_at'})
        for forbidden in ('password_hash', 'is_system_admin', 'must_change_password'):
            self.assertNotIn(forbidden, store_module.EXPORT_USER_COLUMNS)

    def test_write_retry_does_not_replay_committed_insert(self):
        """写锁重试不能重放"已经提交"的写。

        `_dispatch` 的重试是"整体重跑 handler"，它的安全前提是
        "抛异常时事务还没提交"。但代码里确实有"先提交、后做事"的路径
        （insert_row 提交后还要取回结果、register 提交建号后才写会话…）。
        这里把那个窗口原样造出来：commit() 真实落库之后立刻抛写锁错误。
        正确行为是"库里只有 1 行，客户端拿到错误"；
        错误行为是"重试把插入重做一遍，库里 N 行，而客户端看到的状态还说不清楚"。
        """
        client, _ = fresh_user('replay@example.com')
        original_connect = _APP.connect
        calls = []

        def connect_through_proxy():
            return CommitThenFailConnection(original_connect(), calls)

        _APP.connect = connect_through_proxy
        try:
            resp = client.insert('work_items', {'title': '只该有一行'})
        finally:
            _APP.connect = original_connect

        self.assertGreaterEqual(len(calls), 1, '测试前提不成立：commit() 根本没被执行到')
        self.assertEqual(resp.status, 500,
                         f'已提交的写没能以错误收场：{resp.status} {resp.payload}')

        conn = db.connect(db_path())
        try:
            rows = conn.execute(
                "SELECT id FROM work_items WHERE title = '只该有一行'").fetchall()
        finally:
            conn.close()
        self.assertEqual(len(rows), 1,
                         f'已提交的插入被重放，库里出现了 {len(rows)} 行')

    def test_llm_config_requires_admin(self):
        """改 AI 配置是**实例级**操作，普通注册账号必须被拒。

        这条接口能改 baseUrl，而 baseUrl 决定服务端把真实密钥发到哪台主机 ——
        不加门槛就等于"任何注册用户都能把厂商密钥转发给自己"。
        """
        user, _ = fresh_user('llm-nonadmin@example.com')

        # 取"动手之前"的快照再比对。不能直接断言 apiKeySet 为 False ——
        # 同一个测试进程里前面的用例（test_llm_config_allows_public_base_url）
        # 合法地写过密钥，整库是共享的，那会把"本用例没写进去"误判成"有人写了"。
        before = user.get('/api/llm/config').payload

        resp = user.post('/api/llm/config',
                         {'baseUrl': 'https://attacker.example/v1',
                          'apiKey': 'sk-stolen-by-nonadmin'})
        self.assertEqual(resp.status, 403,
                         f'普通账号改动了实例级 AI 配置：{resp.status} {resp.payload}')
        self.assertEqual(resp.code, 'ADMIN_ONLY')
        self.assertIn('仅实例管理员', resp.payload['error']['message'])

        after = user.get('/api/llm/config').payload
        self.assertEqual(after['apiKeySet'], before['apiKeySet'],
                         '普通账号把密钥写进库里了')
        self.assertEqual(after['baseUrl'], before['baseUrl'],
                         '普通账号把 baseUrl 改掉了')
        self.assertNotEqual(after['baseUrl'], 'https://attacker.example/v1',
                            '普通账号把 baseUrl 改掉了')
        # GET 不设门槛（它本来就不回显密钥）：普通账号读配置仍然可用
        self.assertEqual(user.get('/api/llm/config').status, 200)

    def test_llm_config_rejects_private_base_url(self):
        """baseUrl 写入前必须过协议 + 主机校验（SSRF / 凭据外带）。

        169.254.169.254 是云元数据地址；127.0.0.1 / 私网段可能是内部管理面；
        file:// 直接换掉协议。这些一律在写入时拒掉，而不是等到发请求时才失败。
        """
        admin = admin_client()
        bad_urls = (
            'http://169.254.169.254/latest',
            'http://127.0.0.1:11434/v1',
            'file:///etc/passwd',
            'http://10.0.0.5/v1',
            'http://192.168.1.10/v1',
            'http://172.16.3.9/v1',
            'http://localhost:8000/v1',
            'http://[::1]:8000/v1',
            'http://user:pw@api.example.com/v1',
            'gopher://api.example.com/v1',
            'http://metadata.internal/v1',
        )
        for url in bad_urls:
            resp = admin.post('/api/llm/config', {'baseUrl': url})
            self.assertEqual(resp.status, 400,
                             f'{url} 未被拦下：{resp.status} {resp.payload}')
            self.assertEqual(resp.code, 'INVALID_BASE_URL',
                             f'{url} 的错误码不对：{resp.payload}')
            self.assertIn('内网/回环', resp.payload['error']['message'])
        self.assertNotIn('169.254.169.254',
                         json.dumps(admin.get('/api/llm/config').payload),
                         '被拒的地址仍然落库了')

    def test_llm_config_allows_public_base_url(self):
        """防止"一刀切改死"：合法的公网端点必须放行。

        自建 vLLM / Ollama 是既有产品能力，所以这里只挡内网/回环/元数据，
        不挡自定义公网 host。
        """
        admin = admin_client()
        try:
            saved = admin.post('/api/llm/config',
                               {'baseUrl': 'https://api.deepseek.com/v1',
                                'model': 'deepseek-chat'})
            self.assertEqual(saved.status, 200, f'合法公网地址被拦：{saved.payload}')
            self.assertEqual(saved.payload['baseUrl'], 'https://api.deepseek.com/v1')
            self.assertEqual(admin.get('/api/llm/config').payload['baseUrl'],
                             'https://api.deepseek.com/v1')
        finally:
            admin.post('/api/llm/config', {'baseUrl': 'https://api.openai.com/v1'})

    def test_admin_cannot_promote_self_or_demote_owner(self):
        """团队管理员的权限上限：不能造出第二个 owner，也不能把 owner 拉下来。

        `delete` 早就有"所有者不能被移出"的护栏，`update` 却对 admin 无条件放行 ——
        结果是 admin 可以自提 owner、并把原 owner 降成 member，团队所有权被悄悄夺走。
        注意拦下时返回的仍是**空数组**而不是 4xx：这是与 RLS 一致的既有约定
        （前端 assertAffected 靠空数组提示"可能不在你可见的范围内"）。
        """
        owner, owner_id = fresh_user('guard-owner@example.com')
        team = owner.post('/api/teams/create',
                          {'name': '护栏组', 'joinCode': 'GRD001'}).payload['team']
        member, member_id = fresh_user('guard-admin@example.com')
        member.post('/api/teams/join', {'code': 'GRD001'})

        rows = owner.get('/api/workspace').payload['members']
        member_row = [m for m in rows if m['user_id'] == member_id][0]
        owner_row = [m for m in rows if m['user_id'] == owner_id][0]

        # 前提：owner 先把 member 提成 admin（这一步必须允许，否则管不了人）
        self.assertEqual(
            len(owner.update('team_members', member_row['id'], {'role': 'admin'}).rows), 1,
            '所有者提升管理员失败了')

        # ① admin 自提 owner —— 必须被拦
        self.assertEqual(
            member.update('team_members', member_row['id'], {'role': 'owner'}).rows, [],
            'admin 把自己提升成了 owner')
        # ② admin 把原 owner 降成 member —— 必须被拦
        self.assertEqual(
            member.update('team_members', owner_row['id'], {'role': 'member'}).rows, [],
            'admin 把原 owner 降级了')

        roles = {m['user_id']: m['role']
                 for m in owner.get('/api/workspace').payload['members']
                 if m['team_id'] == team['id']}
        self.assertEqual(roles[owner_id], 'owner', f'所有者角色被改了：{roles}')
        self.assertEqual(roles[member_id], 'admin', f'角色分布被改了：{roles}')

        # ③ 所有者本人也不能把 owner 这个角色改掉（转让要走专门路径，本期不做）
        self.assertEqual(
            owner.update('team_members', owner_row['id'], {'role': 'member'}).rows, [],
            'owner 把自己的角色改成了 member，团队会失去所有者')
        # ④ 但 admin 改别人的非角色字段仍然可以（别把功能改死）
        self.assertEqual(
            len(member.update('team_members', member_row['id'], {'title': '值班长'}).rows), 1,
            '管理员改自己的非角色字段也被拦了，权限收得太紧')

    # ---- E. HTTP 层加固 ----

    def test_check_email_is_rate_limited_per_ip(self):
        """原先 check-email 的限速用例 —— 端点已下线，改为"下线后没有匿名枚举口"。

        这条接口当年必须限速的理由是：它免鉴权、且会明确告诉调用方
        "这个邮箱是否已注册"。现在它整个不存在了，攻击面净减少一个；
        这条用例的价值变成**防止它被顺手加回来**。
        """
        client = Client()
        codes = [client.get('/api/auth/check-email?email=probe@example.com').status
                 for _ in range(5)]
        self.assertEqual(set(codes), {404},
                         f'check-email 又活过来了：状态码序列={codes}')

    def test_origin_null_is_rejected(self):
        """`Origin: null` 来自 sandbox iframe / file:// / data: 这类不透明来源，直接拒。

        但不能把"没有 Origin 头"也一起拒掉 —— 非浏览器客户端不发这个头，
        既有的 curl / 测试 / 脚本全都依赖这一点。
        """
        client, _ = fresh_user('origin-null@example.com')
        resp = client.post('/api/rows',
                           {'table': 'work_items', 'op': 'insert',
                            'payload': {'title': '跨站写'}},
                           headers={'Origin': 'null'})
        self.assertEqual(resp.status, 403, f'Origin: null 被放行了：{resp.payload}')
        self.assertEqual(resp.code, 'CROSS_ORIGIN_BLOCKED')
        # 没有 Origin 头的请求照旧放行
        self.assertEqual(client.insert('work_items', {'title': 'no-origin'}).status, 200,
                         '没有 Origin 头的请求被拦了，非浏览器客户端会全部失效')

    def test_error_response_does_not_leak_internal_detail(self):
        """500 只能回固定文案 + 一个编号，不能把 SQL / 表名 / 绝对路径交出去。

        内部细节对攻击者是免费情报：表名告诉他数据结构，绝对路径告诉他
        部署目录（进而能猜备份文件位置）。完整异常与栈只留在服务端日志里。
        """
        from pyapp import store as store_module

        client, _ = fresh_user('leak@example.com')
        original = store_module.select_table

        def boom(conn, ctx, table, **kwargs):
            raise RuntimeError(
                'sqlite3.OperationalError: no such table: work_items '
                '(db=D:\\secret\\gtd4teams.db)')

        store_module.select_table = boom
        try:
            resp = client.get('/api/workspace')
        finally:
            store_module.select_table = original

        self.assertEqual(resp.status, 500)
        body = resp.raw.decode('utf-8')
        self.assertEqual(resp.code, 'INTERNAL_ERROR')
        self.assertNotIn('sqlite', body.lower(), f'响应里泄漏了内部错误细节：{body}')
        self.assertNotIn('work_items', body, f'响应里泄漏了表名：{body}')
        self.assertNotIn('secret', body, f'响应里泄漏了部署路径：{body}')
        self.assertNotIn('gtd4teams.db', body, f'响应里泄漏了库文件名：{body}')
        self.assertRegex(body, r'[0-9a-f]{8}',
                         f'响应里没有可用于对账的 requestId：{body}')

    def test_sse_stream_does_not_emit_second_http_response(self):
        """SSE 已经写出 200 + 事件流头之后，错误分支不能再补一份 HTTP 响应。

        否则客户端先收到 200，body 里又跟着 `HTTP/1.1 400 ...` 和一整套响应头 ——
        浏览器侧表现为"看起来成功，但内容解析不了"，比直接失败更难查。
        """
        # 先把 AI 配置清空。整个测试进程共用一个库，前面的用例会把 baseUrl 设成
        # 真实厂商地址（如 https://api.deepseek.com/v1）；不清掉的话这条用例会真的
        # 去连外网 —— 在能立刻失败的环境里看不出问题，在连不通的环境里会一直挂到超时。
        # 这类"依赖共享状态 + 依赖外网"的用例是典型的假绿/假红来源。
        admin = admin_client()
        admin.post('/api/llm/config', {'apiKey': '', 'model': ''})
        self.assertFalse(admin.get('/api/llm/config').payload['configured'],
                         '前置条件不成立：AI 仍是已配置状态，本用例会去连真实上游')

        client, _ = fresh_user('sse@example.com')
        # 未配置 LLM：stream_chat 在第一次迭代就抛 LlmError，
        # 而此时 200 + SSE 响应头已经发出去了。
        resp = client.post('/api/llm/chat',
                           {'messages': [{'role': 'user', 'content': 'hi'}],
                            'stream': True})
        body = resp.raw
        self.assertNotIn(b'HTTP/1.', body,
                         f'SSE 之后又写了一份 HTTP 响应：{body[:300]!r}')
        self.assertNotIn(b'Content-Length', body,
                         f'SSE 之后又写了一份响应头：{body[:300]!r}')

    def test_get_with_unread_body_does_not_poison_keepalive(self):
        """GET/HEAD 带 body 时，多出来的字节不能被当成下一个请求行。

        keep-alive 连接上，没读掉的 body 会被 BaseHTTPRequestHandler 当成
        下一个请求的请求行，于是同一个连接上的下一个正常 GET 变成 501。
        """
        sock = socket.create_connection(('127.0.0.1', _PORT), timeout=10)
        try:
            sock.sendall(b'GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\n'
                         b'Content-Length: 5\r\n\r\nhello')
            first, _, _ = _read_one_response(sock)
            sock.sendall(b'GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n')
            second, _, second_body = _read_one_response(sock)
        finally:
            sock.close()
        self.assertIn(b'200', first, f'第一个请求就失败了：{first!r}')
        self.assertIn(b'200', second,
                      f'带 body 的 GET 污染了 keep-alive 连接，第二个请求得到：{second!r}')
        self.assertIn(b'ok', second_body)

    def test_resource_url_scheme_is_whitelisted(self):
        """resources.url 只接受 http / https / mailto。

        前端那层 HTML 转义只保证"不破坏 HTML 结构"，不保证协议安全：
        `javascript:` 完全可以穿过转义进入 href，点一下就在同源执行脚本。
        实测过四种写法（含大小写混淆与前导空格）曾经全部原样落库并在工作区回显，
        所以白名单必须落在服务端 —— 前端兜底只是纵深防御，不是修复。
        """
        user, _ = fresh_user('url-scheme@example.com')
        # resources 是**团队域**资源（policy 里 insert 要求是团队成员），
        # 所以先建一个团队；用个人身份插会被 403 挡住，测不到协议校验那一层。
        team = user.post('/api/teams/create',
                         {'name': '链接校验组', 'joinCode': 'URL001'}).payload['team']
        tid = team['id']

        bad_urls = (
            'javascript:alert(document.cookie)',
            'data:text/html,<script>alert(1)</script>',
            'vbscript:msgbox(1)',
            '  JaVaScRiPt:alert(1)',          # 前导空格 + 大小写混淆
            '//evil.example/x',              # 协议相对，没有 scheme
        )
        for bad in bad_urls:
            resp = user.insert('resources', {'title': '坏链', 'url': bad, 'team_id': tid})
            self.assertEqual(resp.status, 400, f'{bad!r} 居然通过了：{resp.status} {resp.payload}')
            self.assertEqual(resp.code, 'INVALID_URL')

        for good in ('https://example.com/a', 'http://example.com',
                     'mailto:ops@example.com'):
            resp = user.insert('resources', {'title': '好链', 'url': good, 'team_id': tid})
            self.assertEqual(resp.status, 200, f'{good!r} 被误拦：{resp.status} {resp.payload}')
            self.assertEqual(resp.rows[0]['url'], good.strip())

        # 空值不算非法：表单允许先占位、之后再补链接
        resp = user.insert('resources', {'title': '无链接', 'team_id': tid})
        self.assertEqual(resp.status, 200)

        # update 路径与 insert 走同一道闸，不能只堵一半
        row_id = user.insert('resources',
                             {'title': '先合法', 'url': 'https://ok.example',
                              'team_id': tid}).rows[0]['id']
        resp = user.update('resources', row_id, {'url': 'javascript:alert(1)'})
        self.assertEqual(resp.status, 400, f'update 路径漏了协议校验：{resp.status}')
        self.assertEqual(resp.code, 'INVALID_URL')


class StorageSpecTest(unittest.TestCase):
    """存储规格与真实表结构必须严格一致 —— 防止手写 DDL 与规格表悄悄漂移。"""

    def test_columns_spec_matches_real_schema(self):
        path = Path(_DATA_DIR) / 'spec-check.db'
        db.init_db(path)
        conn = db.connect(path)
        try:
            for table, spec in db.COLUMNS.items():
                real = {r['name'] for r in
                        conn.execute(f'PRAGMA table_info({table})').fetchall()}
                self.assertEqual(set(spec.keys()), real,
                                 f'{table} 的字段规格与实际列不一致')
        finally:
            conn.close()

    def test_expected_tables_are_all_created(self):
        """EXPECTED_TABLES 是"结构是否已建全"的判断依据。

        它决定 init_db 走"直接返回"还是"重跑 DDL"，而后者在并发启动时会撞写锁
        （蓝绿发布时新槽位正是要在旧槽位还在写的时候启动）。所以这个常量漏一项、
        或者表建出来了却没登记进来，都属于会让发布变得脆弱的真实缺陷。
        """
        path = Path(_DATA_DIR) / 'expected-tables.db'
        db.init_db(path)
        conn = db.connect(path)
        try:
            real = {r['name'] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
            missing = set(db.EXPECTED_TABLES) - real
            self.assertEqual(missing, set(), '这些表没有被建出来：%s' % sorted(missing))

            extra = {name for name in real if not name.startswith('sqlite_')} \
                - set(db.EXPECTED_TABLES)
            self.assertEqual(extra, set(), '这些表建出来了但没登记进 EXPECTED_TABLES：%s'
                             % sorted(extra))

            self.assertTrue(db.schema_present(conn), '建库之后 schema_present 应为 True')
        finally:
            conn.close()

    def test_schema_present_is_false_on_empty_db(self):
        """空库必须被判为"结构未建全"，否则新版本新增的表永远不会被建出来。"""
        path = Path(_DATA_DIR) / 'empty-schema.db'
        conn = db.connect(path)
        try:
            self.assertFalse(db.schema_present(conn))
        finally:
            conn.close()

    def test_init_db_is_idempotent(self):
        """重复建库不应改动任何已有数据（发布时会反复启动新槽位）。"""
        path = Path(_DATA_DIR) / 'idempotent.db'
        db.init_db(path)
        conn = db.connect(path)
        try:
            conn.execute("INSERT INTO users (id, email, name, password_hash, created_at, updated_at) "
                         "VALUES ('u_keep', 'keep@example.com', 'keep', 'x', '2026-01-01T00:00:00Z', "
                         "'2026-01-01T00:00:00Z')")
            conn.commit()
        finally:
            conn.close()

        db.init_db(path)
        conn = db.connect(path)
        try:
            row = conn.execute("SELECT email FROM users WHERE id = 'u_keep'").fetchone()
            self.assertIsNotNone(row, '重复建库把已有数据弄丢了')
            self.assertEqual(row['email'], 'keep@example.com')
        finally:
            conn.close()

    def test_users_table_spec_matches_real_schema(self):
        """users 是唯一没有 COLUMNS 规格的敏感表，用 SYSTEM_COLUMN_SPEC 单独防漂移。"""
        path = Path(_DATA_DIR) / 'users-spec.db'
        db.init_db(path)
        conn = db.connect(path)
        try:
            real = {r['name'] for r in conn.execute('PRAGMA table_info(users)').fetchall()}
            self.assertEqual(set(db.SYSTEM_COLUMN_SPEC['users'].keys()), real,
                             'users 的列清单与 SYSTEM_COLUMN_SPEC 不一致')
            self.assertEqual(_column_info(conn, 'users', 'is_system_admin'),
                             ('INTEGER', 1, '0'))
            self.assertEqual(_column_info(conn, 'users', 'must_change_password'),
                             ('INTEGER', 1, '0'))
        finally:
            conn.close()

    def test_users_table_is_not_client_writable(self):
        """护栏：users 绝不能进 COLUMNS —— 那是通用 CRUD 的可写白名单。

        一旦登记进去，`POST /api/rows` 就能改写账号表（含 password_hash、
        is_system_admin），等于开了一条"任何登录用户都能给自己提权"的通道。
        这条断言存在的意义就是：以后有人看到 users 没规格、顺手"补全"时会立刻变红。
        """
        from pyapp import store
        from pyapp.policy import load_context

        self.assertNotIn('users', db.COLUMNS)
        self.assertNotIn('users', db.WRITABLE_TABLES)
        self.assertNotIn('settings', db.COLUMNS)
        self.assertNotIn('sessions', db.COLUMNS)

        path = Path(_DATA_DIR) / 'users-not-writable.db'
        db.init_db(path)
        conn = db.connect(path)
        try:
            ctx = load_context(conn, 'u_tester')
            with self.assertRaises(store.StoreError) as caught:
                store.insert_row(conn, ctx, 'users', {'email': 'x@y.com'})
            self.assertEqual(caught.exception.code, 'UNKNOWN_TABLE')
            with self.assertRaises(store.StoreError) as caught2:
                store.insert_row(conn, ctx, 'users', {'is_system_admin': 1})
            self.assertEqual(caught2.exception.code, 'UNKNOWN_TABLE')
        finally:
            conn.close()

    def test_import_reports_schema_mismatch_clearly(self):
        """备份比库新时，必须给出"先升级结构再导入"的可执行提示。

        旧行为：`no such column` 冒到 CLI 兜底，运维看到的是一个 SQLite 报错
        （或 HTTP 侧一个 requestId），根本想不到是"备份版本比库新"。
        这里**不做列裁剪**（裁剪会让新备份导新库丢掉 is_system_admin，
        灾备后没人能建团队管理员），只把错误翻译成人能照做的一句话。
        """
        from pyapp import store

        path = Path(_DATA_DIR) / 'import-mismatch.db'
        db.init_db(path)
        conn = db.connect(path)
        try:
            payload = {'format': 'gtd4teams-backup', 'version': 1, 'tables': {'users': [{
                'id': 'u_future', 'email': 'future@example.com', 'name': 'future',
                'password_hash': 'x', 'created_at': db.now_iso(),
                'updated_at': db.now_iso(),
                'some_future_column': 1,        # 本代代码不认识这一列
            }]}}
            with self.assertRaises(store.StoreError) as caught:
                store.import_all(conn, payload)
            self.assertEqual(caught.exception.code, 'IMPORT_SCHEMA_MISMATCH')
            self.assertEqual(caught.exception.status, 400)
            self.assertIn('--check', caught.exception.message)
            # 失败必须整条回滚，不能留下半份数据
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM users WHERE id = 'u_future'").fetchone())
        finally:
            conn.close()

    def test_backup_round_trip(self):
        path = Path(_DATA_DIR) / 'roundtrip.db'
        db.init_db(path)
        conn = db.connect(path)
        try:
            from pyapp import store
            from pyapp.policy import load_context

            ctx = load_context(conn, 'u_tester')
            team = store.create_team_with_owner(conn, ctx, name='备份组', join_code='BAK001')
            ctx = load_context(conn, 'u_tester')      # 建团队后成员关系才存在
            store.insert_row(conn, ctx, 'work_items',
                             {'title': '备份项', 'team_id': team['id'], 'tags': ['a']})
            snapshot = store.export_all(conn)
        finally:
            conn.close()

        path2 = Path(_DATA_DIR) / 'roundtrip2.db'
        db.init_db(path2)
        conn = db.connect(path2)
        try:
            store.import_all(conn, snapshot)
            rows = conn.execute('SELECT * FROM teams').fetchall()
            self.assertEqual(len(rows), 1)
            items = conn.execute('SELECT tags FROM work_items').fetchall()
            self.assertEqual(json.loads(items[0]['tags']), ['a'])
        finally:
            conn.close()


# --------------------------------------------------------------------------
# 迁移层（CR-2026-001）
# --------------------------------------------------------------------------

#: v1.0 的 users 建表语句（6 列、无 is_system_admin / must_change_password）。
#: 老库升级用例必须用**旧 DDL** 建库，否则测不到迁移那一步。
OLD_USER_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id            TEXT PRIMARY KEY,
  email         TEXT NOT NULL,
  name          TEXT NOT NULL DEFAULT '',
  password_hash TEXT NOT NULL,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS users_email_uniq ON users (lower(email));
"""


def _make_legacy_db(path):
    """按 v1.0 结构建库：14 张表、users 6 列、**没有** schema_version。

    只替换 users 的 DDL，其余表结构本 CR 未改动，直接复用模块常量 ——
    这样"旧库"就是真实的旧库，而不是另写一份可能已经漂移的 DDL。
    """
    conn = db.connect(path)
    try:
        conn.executescript(OLD_USER_SCHEMA + db.SESSION_SCHEMA
                           + db.SETTINGS_SCHEMA + db.DATA_SCHEMA)
        conn.commit()
    finally:
        conn.close()


def _schema_fingerprint(conn):
    """列名/类型/非空/默认值/主键 + 索引名 —— 与 DDL 顺序无关的稳定指纹。"""
    tables = sorted(r['name'] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
    fingerprint = {}
    for table in tables:
        fingerprint[table] = [tuple(r) for r in conn.execute(f'PRAGMA table_info({table})')]
        fingerprint[table + '.__idx__'] = sorted(
            r['name'] for r in conn.execute(f'PRAGMA index_list({table})'))
    return fingerprint


def _column_info(conn, table, column):
    """(类型, notnull, 默认值) —— 直接用来断言"两列的类型/非空/默认值"。"""
    for row in conn.execute(f'PRAGMA table_info({table})').fetchall():
        if row['name'] == column:
            return (row['type'], row['notnull'], row['dflt_value'])
    return None


class MigrationTest(unittest.TestCase):
    """版本化迁移层：老库能升级、新库直达最新、重复执行零写入。

    这一组用例守的是"升级会不会长出新列"这个第 1 期实测出来的硬缺口：
    `init_db` 原来只要 `schema_present()` 为真就直接 return，
    于是给已存在的表加列后，只有全新库才有新列，升级库永远没有。
    """

    def setUp(self):
        self._dir = tempfile.mkdtemp(prefix='gtd-migration-')

    def tearDown(self):
        import shutil
        shutil.rmtree(self._dir, ignore_errors=True)

    def _path(self, name):
        return str(Path(self._dir) / name)

    def test_legacy_db_upgrade_adds_account_columns(self):
        path = self._path('legacy.db')
        _make_legacy_db(path)
        conn = db.connect(path)
        try:
            self.assertIsNone(_column_info(conn, 'users', 'is_system_admin'),
                              '前置条件不成立：旧库不应该已经有新列')
        finally:
            conn.close()

        db.init_db(path)

        conn = db.connect(path)
        try:
            self.assertEqual(_column_info(conn, 'users', 'is_system_admin'),
                             ('INTEGER', 1, '0'))
            self.assertEqual(_column_info(conn, 'users', 'must_change_password'),
                             ('INTEGER', 1, '0'))
        finally:
            conn.close()

    def test_legacy_db_upgrade_keeps_existing_rows(self):
        path = self._path('legacy-rows.db')
        _make_legacy_db(path)
        conn = db.connect(path)
        try:
            conn.execute(
                "INSERT INTO users (id, email, name, password_hash, created_at, updated_at) "
                "VALUES ('u_old', 'old@example.com', 'old', 'h', '2026-01-01T00:00:00Z', "
                "'2026-01-01T00:00:00Z')")
            conn.execute(
                "INSERT INTO teams (id, owner_id, name, created_at, updated_at) "
                "VALUES (1, 'u_old', '旧团队', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')")
            conn.execute(
                "INSERT INTO work_items (id, owner_id, title, created_at, updated_at) "
                "VALUES (1, 'u_old', '旧事项', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')")
            conn.commit()
        finally:
            conn.close()

        db.init_db(path)

        conn = db.connect(path)
        try:
            self.assertEqual(conn.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n'], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) AS n FROM teams').fetchone()['n'], 1)
            self.assertEqual(
                conn.execute('SELECT COUNT(*) AS n FROM work_items').fetchone()['n'], 1)
            row = conn.execute(
                'SELECT email, is_system_admin, must_change_password FROM users '
                "WHERE id = 'u_old'").fetchone()
            self.assertEqual(row['email'], 'old@example.com')
            # 存量账号既不是系统管理员、也不该被强制改密（默认 1 会是事故级变更）
            self.assertEqual((row['is_system_admin'], row['must_change_password']), (0, 0))
        finally:
            conn.close()

    def test_init_db_is_idempotent_and_writes_nothing_second_time(self):
        """已是最新版本时，第二次启动必须**零写入**（蓝绿切换的安全前提）。

        "不报错"不够：还要证明它没有偷偷重跑 DDL / 重写版本号。
        用 set_trace_callback 抓 SQL 语句，只允许出现读语句。
        """
        path = self._path('idempotent-migration.db')
        db.init_db(path)

        seen = []
        conn = db.connect(path)
        conn.set_trace_callback(seen.append)
        try:
            db.run_migrations(conn)
        finally:
            conn.set_trace_callback(None)
            conn.close()

        writes = [sql for sql in seen
                  if sql.lstrip().upper().startswith(('ALTER', 'CREATE', 'INSERT', 'UPDATE',
                                                      'DELETE', 'DROP'))]
        self.assertEqual(writes, [], f'第二次迁移出现了写语句：{writes}')

        conn = db.connect(path)
        try:
            self.assertEqual(
                conn.execute('SELECT COUNT(*) AS n FROM schema_version').fetchone()['n'],
                len(db._MIGRATIONS))
        finally:
            conn.close()

    def test_fresh_db_already_has_account_columns(self):
        """新建库直接就是最新结构 —— 不依赖"迁移必须成功"。"""
        path = self._path('fresh.db')
        db.init_db(path)
        conn = db.connect(path)
        try:
            self.assertEqual(_column_info(conn, 'users', 'is_system_admin'),
                             ('INTEGER', 1, '0'),
                             '新库应当由 SCHEMA_SQL 直接建出新列，而不是靠迁移补')
            self.assertEqual(_column_info(conn, 'users', 'must_change_password'),
                             ('INTEGER', 1, '0'))
            versions = {r['version'] for r in
                        conn.execute('SELECT version FROM schema_version').fetchall()}
            self.assertEqual(versions, {m[0] for m in db._MIGRATIONS})
        finally:
            conn.close()

    def test_new_and_legacy_paths_converge_to_same_schema(self):
        """收敛总闸：老库升级后的结构指纹必须等于全新库。

        这一条同时覆盖"迁移漏了列""迁移多加了一列""新库 DDL 与迁移默认值不一致"。
        """
        legacy = self._path('converge-legacy.db')
        _make_legacy_db(legacy)
        db.init_db(legacy)

        fresh = self._path('converge-fresh.db')
        db.init_db(fresh)

        conn_legacy = db.connect(legacy)
        conn_fresh = db.connect(fresh)
        try:
            self.assertEqual(_schema_fingerprint(conn_legacy), _schema_fingerprint(conn_fresh))
        finally:
            conn_legacy.close()
            conn_fresh.close()

    def test_schema_version_records_every_migration(self):
        path = self._path('version-log.db')
        db.init_db(path)
        conn = db.connect(path)
        try:
            rows = conn.execute(
                'SELECT version, applied_at, description FROM schema_version ORDER BY version'
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual({r['version'] for r in rows}, {m[0] for m in db._MIGRATIONS})
        for row in rows:
            self.assertIsNotNone(db.parse_iso(row['applied_at']),
                                 f'版本 {row["version"]} 的 applied_at 不可解析')
            self.assertTrue((row['description'] or '').strip())

    def test_migration_versions_are_unique_and_ascending(self):
        versions = [m[0] for m in db._MIGRATIONS]
        self.assertEqual(versions, sorted(set(versions)), '迁移版本号必须唯一且严格递增')

    def test_failed_migration_rolls_back_and_does_not_stamp_version(self):
        """迁移失败 → 该条整体回滚（含 DDL）→ 版本号不记账 → 向上抛。

        这是"账实一致"的唯一保障：`ALTER` 在 Python sqlite3 的默认隔离级别下
        会自提交，不显式 BEGIN IMMEDIATE 就做不到回滚。
        """
        from unittest import mock

        path = self._path('boom.db')
        _make_legacy_db(path)

        def boom(conn):
            raise RuntimeError('迁移故意失败')

        fake = ((1, 'users.is_system_admin', db._migration_0001_users_is_system_admin),
                (2, 'boom', boom))
        with mock.patch.object(db, '_MIGRATIONS', fake):
            with self.assertRaises(RuntimeError):
                db.init_db(path)

        conn = db.connect(path)
        try:
            versions = {r['version'] for r in
                        conn.execute('SELECT version FROM schema_version').fetchall()}
            self.assertEqual(versions, {1}, '失败的那一步不该被记账')
            self.assertIsNotNone(_column_info(conn, 'users', 'is_system_admin'),
                                 '已成功的第一步应当保留')
        finally:
            conn.close()

    def test_two_connections_migrate_concurrently(self):
        """两个进程同时启动（蓝绿）时，同一旧库的迁移必须串行化，不出现重复 ALTER。"""
        path = self._path('concurrent.db')
        _make_legacy_db(path)

        errors = []
        lock = threading.Lock()

        def worker():
            try:
                db.init_db(path)
            except Exception as exc:                       # noqa: BLE001
                with lock:
                    errors.append(repr(exc))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        self.assertEqual(errors, [], f'并发迁移报错：{errors}')
        conn = db.connect(path)
        try:
            versions = {r['version'] for r in
                        conn.execute('SELECT version FROM schema_version').fetchall()}
            self.assertEqual(versions, {m[0] for m in db._MIGRATIONS})
            self.assertIsNotNone(_column_info(conn, 'users', 'must_change_password'))
        finally:
            conn.close()


# --------------------------------------------------------------------------
# 强制改密闸门（CR-2026-001 §10.5）
# --------------------------------------------------------------------------

#: 不经 `_require_user` 因而**天然放行**的路径（闸门里不需要写例外）。
GATE_NATURAL_EXEMPT = {
    ('GET', '/api/auth/session'),
    ('POST', '/api/auth/login'),
    ('POST', '/api/auth/logout'),
    ('GET', '/api/health'),
    ('POST', '/api/logs'),
}


def _new_team(client, name='账号组'):
    code = 'T' + security.new_user_id()[2:8].upper()
    resp = client.post('/api/teams/create', {'name': name, 'joinCode': code})
    assert resp.status == 201, resp.payload
    return resp.payload['team']['id'], code


def _member_row(client, team_id, user_id):
    rows = client.get('/api/workspace').payload['members']
    return [row for row in rows if row['team_id'] == team_id and row['user_id'] == user_id][0]


class PasswordChangeGateTest(unittest.TestCase):
    """`must_change_password=1` 的会话只能改密 / 看自己的身份 / 登出。"""

    def setUp(self):
        security.reset_limits()

    def test_password_change_gate_blocks_all_business_endpoints(self):
        """遍历 ROUTES：除 3 条白名单 + 天然放行外，一律 403 PASSWORD_CHANGE_REQUIRED。

        这条不是"建议"，它是"将来新增的端点忘了过闸门"的唯一自动化保障。
        闸门放在 _require_user 里，所以任何按既有写法调它的新端点自动被覆盖；
        这条用例把这个不变量钉死。
        """
        from pyapp.httpd import PASSWORD_CHANGE_EXEMPT, ROUTES

        client, _ = fresh_user('gate@example.com', must_change_password=True)

        checked = 0
        for method, path in sorted(ROUTES):
            if path in PASSWORD_CHANGE_EXEMPT:
                continue
            if (method, path) in GATE_NATURAL_EXEMPT:
                continue
            resp = client.request(method, path, {} if method == 'POST' else None)
            self.assertEqual(resp.status, 403,
                             f'{method} {path} 未被强制改密闸门拦住：{resp.status} {resp.payload}')
            self.assertEqual(resp.code, 'PASSWORD_CHANGE_REQUIRED',
                             f'{method} {path} 的错误码不对：{resp.payload}')
            checked += 1
        self.assertGreaterEqual(checked, 10, '闸门遍历覆盖的端点太少，用例失效了')

    def test_gate_whitelist_and_natural_exemptions_still_work(self):
        """白名单 6 条必须真的可用，天然放行的（如 /api/logs）必须仍然 200。

        反向断言很重要：只测"拦住"会把"全部 403"误判成通过，
        而"改密接口本身 403"正是本期最致命的单点漏项（账号永久锁死）。
        """
        client, user_id = fresh_user('gate-allow@example.com', must_change_password=True)

        profile = client.get('/api/auth/profile')
        self.assertEqual(profile.status, 200, profile.payload)
        self.assertEqual(profile.payload['id'], user_id)
        self.assertIs(profile.payload['mustChangePassword'], True)

        me = client.get('/api/auth/me')
        self.assertEqual(me.status, 200, me.payload)

        session = client.get('/api/auth/session')
        self.assertEqual(session.status, 200)
        self.assertIs(session.payload['user']['mustChangePassword'], True)

        self.assertEqual(client.get('/api/health').status, 200)
        self.assertEqual(client.post('/api/logs', {'event': 'gate.probe'}).status, 200)

        # 改密接口本身必须放行（否则账号永久锁死）：
        # 这里故意给错原密码，期望 401 而不是 403 —— 证明请求进到了 handler 内部。
        wrong = client.post('/api/auth/password',
                            {'oldPassword': 'wrong-old', 'newPassword': 'new-pass-1234'})
        self.assertEqual(wrong.status, 401, wrong.payload)
        self.assertEqual(wrong.code, 'INVALID_OLD_PASSWORD')

        # 登出必须可用（被强制改密的人要能换账号）
        self.assertEqual(client.post('/api/auth/logout').status, 200)

    def test_password_change_exempt_whitelist_is_exact(self):
        """白名单必须**恰好**是这 6 条路径，而且每条都得是真实路由。

        - 多一条 = 越权（放行了业务端点）；
        - 少一条 = 可能把账号永久锁死（尤其 password）；
        - 路径抄错（例如漏了 `/auth` 段）= 该条永远匹配不上，白名单静默失效。
        """
        from pyapp.httpd import PASSWORD_CHANGE_EXEMPT, ROUTES

        expected = {
            '/api/auth/password',    # 漏了这条 = 账号永久锁死
            '/api/auth/profile',
            '/api/auth/me',
            '/api/auth/session',     # 前端启动门禁靠它判断该渲染哪个页面
            '/api/auth/logout',
            '/api/health',
        }
        self.assertEqual(set(PASSWORD_CHANGE_EXEMPT), expected,
                         '强制改密白名单与定稿不一致（多/少都不行）')

        routes = {path for _method, path in ROUTES}
        for path in expected:
            self.assertIn(path, routes,
                          f'白名单里的 {path} 不在 ROUTES 中（路径写错了，永远匹配不上）')

    def test_change_password_clears_must_change_flag(self):
        """改密成功后：标记清零 + 同一会话立刻能进业务接口。

        防的是"改密成功但永远用不了"：只写 password_hash、忘了清零，
        每次都会被闸门 403，而且再改一次也没用（同一段代码）。
        """
        client, user_id = fresh_user('mustchg@example.com', must_change_password=True)
        self.assertEqual(client.get('/api/workspace').status, 403)

        # 原密码错：不得清零（否则一次错误尝试就把用户放行出去了）
        bad = client.post('/api/auth/password',
                          {'oldPassword': 'not-the-old-one', 'newPassword': 'new-pass-1234'})
        self.assertEqual(bad.status, 401)
        self.assertEqual(client.get('/api/workspace').status, 403, '错误尝试把标记清零了')

        ok = client.post('/api/auth/password',
                         {'oldPassword': ADMIN_PASSWORD, 'newPassword': 'new-pass-1234'})
        self.assertEqual(ok.status, 200, ok.payload)
        self.assertEqual(client.get('/api/workspace').status, 200,
                         '改密成功后仍然被闸门拦住 —— 标记没有清零')
        self.assertIs(client.get('/api/auth/session').payload['user']['mustChangePassword'],
                      False)

        conn = db.connect(db_path())
        try:
            row = conn.execute('SELECT must_change_password, password_hash FROM users '
                               'WHERE id = ?', (user_id,)).fetchone()
        finally:
            conn.close()
        self.assertEqual(row['must_change_password'], 0)
        self.assertTrue(security.verify_password('new-pass-1234', row['password_hash']))

    def test_trailing_slash_password_endpoint_still_works(self):
        """`POST /api/auth/password/` 必须与不带斜杠的等价（防 self._path 那个坑）。

        白名单比对的是**归一化后**的路径。若误用 `self.path`，
        带尾斜杠的改密请求会落不进白名单 → 403 → 账号永久锁死。
        而尾斜杠是既有行为（`/api/health/` 与 `/api/health` 等价），真实会发生。
        """
        client, _ = fresh_user('slash@example.com', must_change_password=True)
        resp = client.post('/api/auth/password/',
                           {'oldPassword': ADMIN_PASSWORD, 'newPassword': 'slash-pass-1234'})
        self.assertEqual(resp.status, 200, resp.payload)
        self.assertEqual(client.get('/api/workspace').status, 200)


# --------------------------------------------------------------------------
# 三级建号的权限矩阵（06 §10.3.3 的 18 行）
# --------------------------------------------------------------------------

class AccountAdminTest(unittest.TestCase):
    def setUp(self):
        security.reset_limits()

    # -- 创建：响应契约 ------------------------------------------------

    def test_create_account_returns_one_time_password(self):
        admin = admin_client()
        email = 'created-once@example.com'
        resp = admin.post('/api/admin/accounts', {'email': email, 'name': '甲'})
        self.assertEqual(resp.status, 201, resp.payload)
        body = resp.payload

        self.assertEqual(set(body.keys()), {'user', 'teamId', 'role', 'password'})
        self.assertEqual(body['user']['email'], email)
        self.assertEqual(body['user']['name'], '甲')
        self.assertIs(body['user']['isSystemAdmin'], False)
        self.assertIs(body['user']['mustChangePassword'], True)
        self.assertIsNone(body['teamId'])
        self.assertIsNone(body['role'])
        self.assertEqual(len(body['password']), 16)
        self.assertEqual(security.validate_password(body['password']), '')
        # 视觉混淆字符（0/O/1/l/I）不在字符集里
        self.assertFalse(set('0O1lI') & set(body['password']), body['password'])

        # 含明文口令的响应不能被任何中间层缓存
        self.assertEqual(resp.headers.get('cache-control'), 'no-store')
        # 建号不切换调用者的会话
        self.assertIsNone(resp.headers.get('set-cookie'))

        # 该口令真的能用，且登录后立刻被闸门拦住
        newcomer = Client()
        self.assertEqual(newcomer.login(email, body['password']).status, 200)
        self.assertEqual(newcomer.get('/api/workspace').status, 403)

        # 「只出现一次」：其它读接口与审计里都不再出现这串明文
        password = body['password']
        self.assertNotIn(password, json.dumps(admin.get('/api/admin/accounts').payload))
        self.assertNotIn(password, admin.get('/api/export').raw.decode('utf-8'))
        conn = db.connect(db_path())
        try:
            logs = conn.execute('SELECT event, message, detail FROM app_logs '
                                "WHERE event = 'account.created'").fetchall()
            stored = conn.execute('SELECT password_hash FROM users WHERE email = ?',
                                  (email,)).fetchone()
        finally:
            conn.close()
        self.assertTrue(logs, '建号没有留下审计')
        self.assertNotIn(password, json.dumps([dict(r) for r in logs], ensure_ascii=False))
        self.assertFalse(stored['password_hash'].startswith(password))

    def test_create_account_ignores_privilege_and_identity_fields(self):
        """请求体里的 isSystemAdmin / mustChangePassword / password / id 一律不读。"""
        admin = admin_client()
        email = 'ignored-fields@example.com'
        resp = admin.post('/api/admin/accounts', {
            'email': email,
            'isSystemAdmin': True,
            'mustChangePassword': False,
            'password': 'attacker-chosen-1234',
            'passwordHash': 'x',
            'id': 'u_attacker',
            'owner_id': 'u_attacker',
        })
        self.assertEqual(resp.status, 201, resp.payload)
        self.assertNotEqual(resp.payload['user']['id'], 'u_attacker')

        conn = db.connect(db_path())
        try:
            row = conn.execute(
                'SELECT id, is_system_admin, must_change_password, password_hash '
                'FROM users WHERE email = ?', (email,)).fetchone()
        finally:
            conn.close()
        self.assertEqual(row['is_system_admin'], 0, 'HTTP 路径建出了系统管理员')
        self.assertEqual(row['must_change_password'], 1)
        self.assertFalse(security.verify_password('attacker-chosen-1234', row['password_hash']))

    def test_create_account_validates_email_and_duplicates(self):
        admin = admin_client()
        bad = admin.post('/api/admin/accounts', {'email': 'not-an-email'})
        self.assertEqual(bad.status, 400)
        self.assertEqual(bad.code, 'INVALID_EMAIL')

        email = 'dup-account@example.com'
        self.assertEqual(admin.post('/api/admin/accounts', {'email': email}).status, 201)
        dup = admin.post('/api/admin/accounts', {'email': email.upper()})
        self.assertEqual(dup.status, 409)
        self.assertEqual(dup.code, 'EMAIL_EXISTS')
        self.assertIn('邀请码', dup.payload['error']['message'],
                      '错误文案必须指向正确出路（既有账号走邀请码加入团队）')

    # -- 创建：18 行权限矩阵 -------------------------------------------

    def test_matrix_01_unauthenticated_is_rejected(self):
        resp = Client().post('/api/admin/accounts', {'email': 'anon@example.com'})
        self.assertEqual(resp.status, 401)
        self.assertEqual(resp.code, 'UNAUTHENTICATED')

    def test_matrix_02_must_change_password_blocks_account_creation(self):
        client, _ = fresh_user('matrix-gate@example.com', must_change_password=True)
        resp = client.post('/api/admin/accounts', {'email': 'blocked@example.com'})
        self.assertEqual(resp.status, 403)
        self.assertEqual(resp.code, 'PASSWORD_CHANGE_REQUIRED')

    def test_matrix_03_sysadmin_without_team_creates_account_only(self):
        admin = admin_client()
        email = 'matrix-03@example.com'
        resp = admin.post('/api/admin/accounts', {'email': email})
        self.assertEqual(resp.status, 201, resp.payload)
        self.assertIsNone(resp.payload['teamId'])
        self.assertIsNone(resp.payload['role'])
        conn = db.connect(db_path())
        try:
            user_id = conn.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()['id']
            members = conn.execute('SELECT COUNT(*) AS n FROM team_members WHERE user_id = ?',
                                   (user_id,)).fetchone()['n']
        finally:
            conn.close()
        self.assertEqual(members, 0)

    def test_matrix_04_sysadmin_without_team_cannot_grant_admin(self):
        admin = admin_client()
        resp = admin.post('/api/admin/accounts',
                          {'email': 'matrix-04@example.com', 'role': 'admin'})
        self.assertEqual(resp.status, 400)
        self.assertEqual(resp.code, 'ROLE_NOT_ALLOWED')

    def test_matrix_05_06_sysadmin_can_add_admin_but_not_member_to_team(self):
        admin = admin_client()
        team_id, _ = _new_team(admin, '矩阵五六组')

        # 矩阵 #5：系统管理员 + 有 teamId + role 省略 → 默认 admin，201
        omitted_email = 'matrix-05@example.com'
        omitted = admin.post('/api/admin/accounts',
                             {'email': omitted_email, 'teamId': team_id})
        self.assertEqual(omitted.status, 201, omitted.payload)
        self.assertEqual((omitted.payload['teamId'], omitted.payload['role']),
                         (team_id, 'admin'),
                         '系统管理员建号缺省 role 应为 admin（不是 member）')

        # 矩阵 #6：系统管理员 + 有 teamId + role=admin → 201
        admin_email = 'matrix-06@example.com'
        team_admin = admin.post('/api/admin/accounts',
                                {'email': admin_email, 'teamId': team_id, 'role': 'admin'})
        self.assertEqual(team_admin.status, 201, team_admin.payload)
        self.assertEqual((team_admin.payload['teamId'], team_admin.payload['role']),
                         (team_id, 'admin'))

        # 裁决收紧：系统管理员 + 有 teamId + role=member → 400 ROLE_NOT_ALLOWED
        member_email = 'matrix-05-member@example.com'
        member = admin.post('/api/admin/accounts',
                            {'email': member_email, 'teamId': team_id, 'role': 'member'})
        self.assertEqual(member.status, 400, member.payload)
        self.assertEqual(member.code, 'ROLE_NOT_ALLOWED',
                         '系统管理员不能创建 member（裁决：只能建团队管理员）')

        conn = db.connect(db_path())
        try:
            roles = {r['role'] for r in conn.execute(
                'SELECT role FROM team_members WHERE team_id = ?', (team_id,)).fetchall()}
        finally:
            conn.close()
        self.assertEqual(roles, {'owner', 'admin'})

    def test_matrix_07_owner_role_is_never_grantable(self):
        admin = admin_client()
        team_id, _ = _new_team(admin, '矩阵七组')
        resp = admin.post('/api/admin/accounts',
                          {'email': 'matrix-07@example.com', 'teamId': team_id, 'role': 'owner'})
        self.assertEqual(resp.status, 400)
        self.assertEqual(resp.code, 'ROLE_NOT_ALLOWED')

    def test_matrix_08_09_unknown_or_archived_team_is_404(self):
        admin = admin_client()
        missing = admin.post('/api/admin/accounts',
                             {'email': 'matrix-08@example.com', 'teamId': 999999})
        self.assertEqual(missing.status, 404)
        self.assertEqual(missing.code, 'TEAM_NOT_FOUND')

        team_id, _ = _new_team(admin, '矩阵九组')
        admin.update('teams', team_id, {'archived': True})
        archived = admin.post('/api/admin/accounts',
                              {'email': 'matrix-09@example.com', 'teamId': team_id})
        self.assertEqual(archived.status, 404)
        self.assertEqual(archived.code, 'TEAM_NOT_FOUND')

    def test_matrix_10_sysadmin_need_not_be_team_member(self):
        """系统管理员可对任意团队授 admin（已知取舍，见 06 §10.3.3 第 10 行）。"""
        admin = admin_client()
        outsider_owner, _ = fresh_user('matrix-10-owner@example.com')
        team_id, _ = _new_team(outsider_owner, '矩阵十组')

        self.assertEqual([
            m['user_id'] for m in admin.get('/api/workspace').payload['members']
            if m['team_id'] == team_id], [], '前提不成立：系统管理员成了该团队成员')

        resp = admin.post('/api/admin/accounts',
                          {'email': 'matrix-10@example.com', 'teamId': team_id, 'role': 'admin'})
        self.assertEqual(resp.status, 201, resp.payload)
        self.assertEqual(resp.payload['role'], 'admin')

    def test_matrix_11_12_team_admin_creates_member_but_not_admin(self):
        owner, owner_id = fresh_user('matrix-11-owner@example.com')
        team_id, code = _new_team(owner, '矩阵十一组')
        team_admin, team_admin_id = fresh_user('matrix-11-admin@example.com')
        team_admin.post('/api/teams/join', {'code': code})
        owner.update('team_members', _member_row(owner, team_id, team_admin_id)['id'],
                     {'role': 'admin'})

        ok = team_admin.post('/api/admin/accounts',
                             {'email': 'matrix-11@example.com', 'teamId': team_id})
        self.assertEqual(ok.status, 201, ok.payload)
        self.assertEqual(ok.payload['role'], 'member')

        denied = team_admin.post('/api/admin/accounts',
                                 {'email': 'matrix-12@example.com', 'teamId': team_id,
                                  'role': 'admin'})
        self.assertEqual(denied.status, 403)
        self.assertEqual(denied.code, 'ROLE_NOT_ALLOWED')

    def test_matrix_13_team_admin_cannot_manage_other_team(self):
        owner, _ = fresh_user('matrix-13-owner@example.com')
        _my_team, code = _new_team(owner, '矩阵十三甲组')
        other_owner, _ = fresh_user('matrix-13-other@example.com')
        other_team_id, _ = _new_team(other_owner, '矩阵十三乙组')

        team_admin, team_admin_id = fresh_user('matrix-13-admin@example.com')
        team_admin.post('/api/teams/join', {'code': code})
        owner.update('team_members', _member_row(owner, _my_team, team_admin_id)['id'],
                     {'role': 'admin'})

        resp = team_admin.post('/api/admin/accounts',
                               {'email': 'matrix-13@example.com', 'teamId': other_team_id})
        self.assertEqual(resp.status, 403)
        self.assertEqual(resp.code, 'NOT_TEAM_ADMIN')

    def test_matrix_14_team_admin_must_name_a_team(self):
        owner, _ = fresh_user('matrix-14-owner@example.com')
        _new_team(owner, '矩阵十四组')
        resp = owner.post('/api/admin/accounts', {'email': 'matrix-14@example.com'})
        self.assertEqual(resp.status, 400)
        self.assertEqual(resp.code, 'TEAM_REQUIRED')

    def test_matrix_15_16_17_no_team_authority_is_forbidden(self):
        # 15：普通成员
        owner, _ = fresh_user('matrix-15-owner@example.com')
        team_id, code = _new_team(owner, '矩阵十五组')
        member, _ = fresh_user('matrix-15-member@example.com')
        member.post('/api/teams/join', {'code': code})
        resp = member.post('/api/admin/accounts',
                           {'email': 'matrix-15@example.com', 'teamId': team_id})
        self.assertEqual(resp.status, 403)
        self.assertEqual(resp.code, 'FORBIDDEN')

        # 16：被停用的前管理员（停用即失去管理权）
        stale, stale_id = fresh_user('matrix-16-admin@example.com')
        stale.post('/api/teams/join', {'code': code})
        owner.update('team_members', _member_row(owner, team_id, stale_id)['id'],
                     {'role': 'admin'})
        self.assertEqual(stale.post('/api/admin/accounts',
                                    {'email': 'matrix-16a@example.com',
                                     'teamId': team_id}).status, 201)
        owner.update('team_members', _member_row(owner, team_id, stale_id)['id'],
                     {'status': 'inactive'})
        after = stale.post('/api/admin/accounts',
                           {'email': 'matrix-16b@example.com', 'teamId': team_id})
        self.assertEqual(after.status, 403)
        self.assertEqual(after.code, 'FORBIDDEN')

        # 17：不属于任何团队
        loner, _ = fresh_user('matrix-17-loner@example.com')
        alone = loner.post('/api/admin/accounts', {'email': 'matrix-17@example.com'})
        self.assertEqual(alone.status, 403)
        self.assertEqual(alone.code, 'FORBIDDEN')

    def test_matrix_18_sysadmin_wins_over_team_admin(self):
        """同时是系统管理员 + 该团队管理员 → 按系统管理员分支（可授 admin）。"""
        owner, _ = fresh_user('matrix-18-owner@example.com')
        team_id, code = _new_team(owner, '矩阵十八组')

        both, both_id = fresh_user('matrix-18-both@example.com', is_system_admin=True)
        both.post('/api/teams/join', {'code': code})
        owner.update('team_members', _member_row(owner, team_id, both_id)['id'],
                     {'role': 'admin'})

        resp = both.post('/api/admin/accounts',
                         {'email': 'matrix-18@example.com', 'teamId': team_id, 'role': 'admin'})
        self.assertEqual(resp.status, 201, resp.payload)
        self.assertEqual(resp.payload['role'], 'admin')

    def test_promoted_admin_must_change_password_before_working(self):
        """提权即"降权"：被授予系统管理员后必须先改密，才能继续干活。

        判据不是"有没有生成新口令"，而是"口令这一环是否经过运维"。
        一个 must_change_password=0 的老账号被提权后若仍免改密，
        就留下"永久免改密 + 口令可能已被共享"的状态 —— 正是本次要消灭的。
        """
        from pyapp import store as store_module

        client, _ = fresh_user('promote-http@example.com')
        self.assertEqual(client.get('/api/workspace').status, 200)

        conn = db.connect(db_path())
        try:
            result = store_module.grant_system_admin(conn, 'promote-http@example.com')
        finally:
            conn.close()
        self.assertEqual(result['changed'], True)
        self.assertIs(result['user']['isSystemAdmin'], True)
        self.assertIs(result['user']['mustChangePassword'], True)

        blocked = client.get('/api/workspace')
        self.assertEqual(blocked.status, 403, '提权后没有强制改密')
        self.assertEqual(blocked.code, 'PASSWORD_CHANGE_REQUIRED')

        # 改密后恢复使用，且仍是系统管理员
        ok = client.post('/api/auth/password',
                         {'oldPassword': ADMIN_PASSWORD, 'newPassword': 'promoted-1234'})
        self.assertEqual(ok.status, 200, ok.payload)
        self.assertEqual(client.get('/api/workspace').status, 200)
        self.assertIs(client.get('/api/auth/session').payload['user']['isSystemAdmin'], True)

    def test_account_creation_is_audited_without_secrets(self):
        admin = admin_client()
        resp = admin.post('/api/admin/accounts', {'email': 'audited@example.com'})
        self.assertEqual(resp.status, 201)
        conn = db.connect(db_path())
        try:
            row = conn.execute(
                "SELECT owner_id, level, scope, event, message, detail FROM app_logs "
                "WHERE event = 'account.created' ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            conn.close()
        self.assertEqual(row['scope'], 'account')
        self.assertEqual(row['level'], 'info')
        detail = json.loads(row['detail'])
        self.assertIs(detail['bySystemAdmin'], True)
        self.assertEqual(detail['via'], 'http')
        self.assertEqual(detail['targetUserId'], resp.payload['user']['id'])
        self.assertNotIn('password', json.dumps(detail).lower())

    # -- 列表 ----------------------------------------------------------

    def test_list_accounts_scope_rules(self):
        admin = admin_client()

        # 系统管理员省略 teamId → 实例级
        instance = admin.get('/api/admin/accounts')
        self.assertEqual(instance.status, 200, instance.payload)
        self.assertEqual(instance.payload['scope'], 'instance')
        self.assertGreaterEqual(len(instance.payload['accounts']), 1)

        owner, owner_id = fresh_user('list-owner@example.com')
        team_id, code = _new_team(owner, '列表组')
        member, member_id = fresh_user('list-member@example.com')
        member.post('/api/teams/join', {'code': code})

        # 团队管理员给 teamId → 团队级
        team_view = owner.get(f'/api/admin/accounts?teamId={team_id}')
        self.assertEqual(team_view.status, 200, team_view.payload)
        self.assertEqual(team_view.payload['scope'], 'team')
        ids = [a['id'] for a in team_view.payload['accounts']]
        self.assertEqual(set(ids), {owner_id, member_id})
        for account in team_view.payload['accounts']:
            self.assertEqual(len(account['teams']), 1)
            self.assertEqual(account['teams'][0]['teamId'], team_id)

        # 团队管理员省略 teamId → 400 TEAM_REQUIRED（不给"全量"选项）
        missing = owner.get('/api/admin/accounts')
        self.assertEqual(missing.status, 400)
        self.assertEqual(missing.code, 'TEAM_REQUIRED')

        # 普通成员 → 403 FORBIDDEN
        denied = member.get(f'/api/admin/accounts?teamId={team_id}')
        self.assertEqual(denied.status, 403)
        self.assertEqual(denied.code, 'FORBIDDEN')

        # 他人的团队 → 403 NOT_TEAM_ADMIN
        other_owner, _ = fresh_user('list-other@example.com')
        other_team_id, _ = _new_team(other_owner, '别人的组')
        cross = owner.get(f'/api/admin/accounts?teamId={other_team_id}')
        self.assertEqual(cross.status, 403)
        self.assertEqual(cross.code, 'NOT_TEAM_ADMIN')

    def test_list_accounts_never_exposes_credentials(self):
        admin = admin_client()
        body = admin.get('/api/admin/accounts').raw.decode('utf-8')
        self.assertNotIn('password_hash', body)
        self.assertNotIn('pbkdf2_sha256', body)
        self.assertNotIn('sessions', body)
        self.assertNotIn('llm_api_key', body)


# --------------------------------------------------------------------------
# CLI：--create-admin / --grant-admin / --set-password
# --------------------------------------------------------------------------

class CliAccountTest(unittest.TestCase):
    """三个 CLI 命令必须落在同一条写入路径（store）上，且都置 must_change_password=1。"""

    def setUp(self):
        import shutil
        import server
        self._server = server
        self._dir = tempfile.mkdtemp(prefix='gtd-cli-')
        self.cfg = Config(data_dir=self._dir)
        self.addCleanup(shutil.rmtree, self._dir, True)

    def _run(self, func, *args):
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = func(self.cfg, *args)
        return code, buffer.getvalue()

    def _user(self, email):
        path = str(Path(self._dir) / 'gtd4teams.db')
        db.init_db(path)
        conn = db.connect(path)
        try:
            return conn.execute(
                'SELECT id, email, is_system_admin, must_change_password, password_hash '
                'FROM users WHERE lower(email) = lower(?)', (email,)).fetchone()
        finally:
            conn.close()

    def test_create_admin_creates_and_is_idempotent(self):
        import re

        code, output = self._run(self._server.cmd_create_admin, 'ops@example.com')
        self.assertEqual(code, 0, output)
        self.assertIn('已创建系统管理员', output)
        match = re.search(r'初始密码（只显示这一次，请立即保存）：(\S+)', output)
        self.assertIsNotNone(match, output)
        password = match.group(1)

        row = self._user('ops@example.com')
        self.assertEqual((row['is_system_admin'], row['must_change_password']), (1, 1))
        self.assertTrue(security.verify_password(password, row['password_hash']))

        # 第二次执行：只授权，**不动口令**（否则会把刚发出去的口令改掉）
        code2, output2 = self._run(self._server.cmd_create_admin, 'ops@example.com')
        self.assertEqual(code2, 0, output2)
        self.assertIn('已是系统管理员', output2)
        self.assertNotIn(password, output2)
        row2 = self._user('ops@example.com')
        self.assertEqual(row2['password_hash'], row['password_hash'])

    def test_create_admin_grant_existing_account_keeps_password(self):
        _create_user_directly('existing@example.com', data_dir=self._dir)
        before = self._user('existing@example.com')

        code, output = self._run(self._server.cmd_create_admin, 'existing@example.com')
        self.assertEqual(code, 0, output)
        self.assertIn('已授予系统管理员', output)
        row = self._user('existing@example.com')
        self.assertEqual(row['is_system_admin'], 1)
        # 裁决 Q-2：凡口令经运维之手（--create-admin / --grant-admin / --set-password）
        # 一律置 must_change_password=1。判据是"口令这一环是否经运维"，不是"有没有生成新口令"。
        # 一个免改密的老账号被提权后若仍免改密，就留下"永久免改密 + 口令可能已被分发"的状态。
        self.assertEqual(row['must_change_password'], 1, '授权后没有强制改密')
        # 口令本身保持不动（只授权、不重置口令）
        self.assertEqual(row['password_hash'], before['password_hash'])

    def test_create_admin_rejects_invalid_email(self):
        code, output = self._run(self._server.cmd_create_admin, 'not-an-email')
        self.assertEqual(code, 2, output)
        self.assertIsNone(self._user('not-an-email'))

    def test_grant_admin_does_not_create_missing_account(self):
        code, output = self._run(self._server.cmd_grant_admin, 'ghost@example.com')
        self.assertEqual(code, 2, output)
        self.assertIn('不存在', output)
        self.assertIsNone(self._user('ghost@example.com'), '--grant-admin 建号了')

    def test_grant_admin_grants_existing(self):
        _create_user_directly('grantee@example.com', data_dir=self._dir)
        code, output = self._run(self._server.cmd_grant_admin, 'grantee@example.com')
        self.assertEqual(code, 0, output)
        self.assertEqual(self._user('grantee@example.com')['is_system_admin'], 1)
        # 提权是"口令经运维之手"的操作，必须同时强制改密：
        # 否则一个 must_change_password=0 的老账号被提权后仍永久免改密。
        self.assertEqual(self._user('grantee@example.com')['must_change_password'], 1,
                         '--grant-admin 提权后没有强制改密')

        code2, output2 = self._run(self._server.cmd_grant_admin, 'grantee@example.com')
        self.assertEqual(code2, 0, output2)
        self.assertIn('无改动', output2)

    def test_grant_admin_forces_password_change(self):
        """`--grant-admin` 也必须置 must_change_password=1（三个 CLI 一致）。

        这一条同时证明"提权即降权"：提权之后反而不能直接干活，必须先改密。
        """
        _create_user_directly('promote@example.com', data_dir=self._dir,
                              must_change_password=False)
        self.assertEqual(self._user('promote@example.com')['must_change_password'], 0)

        code, output = self._run(self._server.cmd_grant_admin, 'promote@example.com')
        self.assertEqual(code, 0, output)
        row = self._user('promote@example.com')
        self.assertEqual(row['is_system_admin'], 1)
        self.assertEqual(row['must_change_password'], 1,
                         '提权后没有强制改密（会出现"永久免改密 + 口令经运维之手"）')

    def test_grant_admin_only_stamps_flag_when_already_admin(self):
        """已是管理员但免改密 → 只补 flag，且必须如实报告"有改动"。"""
        _create_user_directly('already-admin@example.com', data_dir=self._dir,
                              is_system_admin=True, must_change_password=False)
        code, output = self._run(self._server.cmd_grant_admin, 'already-admin@example.com')
        self.assertEqual(code, 0, output)
        row = self._user('already-admin@example.com')
        self.assertEqual(row['must_change_password'], 1)
        self.assertNotIn('无改动', output, 'CLI 说"无改动"但库里被改了')

    def test_set_password_sets_must_change_flag_and_kicks_sessions(self):
        from unittest import mock

        _create_user_directly('reset@example.com', data_dir=self._dir)
        path = str(Path(self._dir) / 'gtd4teams.db')
        conn = db.connect(path)
        try:
            user_id = conn.execute("SELECT id FROM users WHERE email = 'reset@example.com'"
                                   ).fetchone()['id']
            conn.execute(
                'INSERT INTO sessions (token_hash, user_id, created_at, expires_at, '
                'last_seen_at) VALUES (?, ?, ?, ?, ?)',
                ('hash', user_id, db.now_iso(), '2099-01-01T00:00:00Z', db.now_iso()))
            conn.commit()
        finally:
            conn.close()

        with mock.patch('getpass.getpass', side_effect=['cli-pass-1234', 'cli-pass-1234']):
            code, output = self._run(self._server.cmd_set_password, 'reset@example.com')
        self.assertEqual(code, 0, output)

        row = self._user('reset@example.com')
        self.assertEqual(row['must_change_password'], 1, 'CLI 设的口令没有强制改密')
        self.assertTrue(security.verify_password('cli-pass-1234', row['password_hash']))

        conn = db.connect(path)
        try:
            self.assertEqual(
                conn.execute('SELECT COUNT(*) AS n FROM sessions WHERE user_id = ?',
                             (user_id,)).fetchone()['n'], 0, '重置密码没有踢掉旧会话')
        finally:
            conn.close()

    def test_set_password_creates_non_admin_account(self):
        from unittest import mock

        with mock.patch('getpass.getpass', side_effect=['cli-new-1234', 'cli-new-1234']):
            code, output = self._run(self._server.cmd_set_password, 'fresh@example.com')
        self.assertEqual(code, 0, output)
        row = self._user('fresh@example.com')
        self.assertIsNotNone(row, '账号没有被创建出来')
        self.assertEqual(row['is_system_admin'], 0, 'CLI 建号顺手授予了系统管理员')
        self.assertEqual(row['must_change_password'], 1)

    def test_check_reports_admin_counts(self):
        import contextlib
        import io

        _create_user_directly('counted@example.com', data_dir=self._dir,
                              is_system_admin=True, must_change_password=True)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            self._server.cmd_check(self.cfg)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload['counts']['systemAdmins'], 1)
        self.assertEqual(payload['counts']['pendingPasswordChanges'], 1)
        self.assertIn('schema_version', payload['tables'])


# --------------------------------------------------------------------------
# 安全复审计缺陷（SEC-01 ~ SEC-04）
# --------------------------------------------------------------------------

class SecurityAuditTest(unittest.TestCase):
    """安全复审计挖出的四条缺陷，每条一个红线用例组。

    这四条的共同点：**都不会让既有用例变红**（所以能活到复审计），
    因此必须有专门的断言把它们钉死，否则修好之后还会被"顺手改回去"。
    """

    def setUp(self):
        security.reset_limits()

    # ---- SEC-01：403 / 415 早退必须丢弃请求体（否则 keep-alive 连接失步）----

    def test_origin_403_discards_body_keepalive_stays_clean(self):
        """同一连接上先被 403 拦下的写请求，不能把请求体留给下一个请求。

        不丢弃的后果是实测出来的：请求体字节被 BaseHTTPRequestHandler 当成
        **下一个请求的请求行**，紧接着的正常登录变成
        `501 Unsupported method ('{}POST')`。经反向代理的连接池还能跨连接带过去，
        于是"1 个请求换回 2 份响应"成为走私原语。
        """
        _create_user_directly('keepalive-403@example.com')

        conn = http.client.HTTPConnection('127.0.0.1', _PORT, timeout=20)
        try:
            conn.request('POST', '/api/rows',
                         body=json.dumps({'table': 'work_items', 'op': 'insert',
                                          'payload': {'title': '跨站写'}}).encode('utf-8'),
                         headers={'Content-Type': 'application/json',
                                  'Origin': 'http://evil.test'})
            first = conn.getresponse()
            first.read()
            self.assertEqual(first.status, 403, '前置条件不成立：请求没被跨站校验拦下')

            conn.request('POST', '/api/auth/login',
                         body=json.dumps({'email': 'keepalive-403@example.com',
                                          'password': ADMIN_PASSWORD}).encode('utf-8'),
                         headers={'Content-Type': 'application/json'})
            second = conn.getresponse()
            body = second.read()
            self.assertEqual(second.status, 200,
                             f'同一连接上的下一个请求被污染了：{second.status} {body[:200]!r}')
            self.assertIn('gtd_session', second.getheader('Set-Cookie') or '',
                          '登录没有下发会话 Cookie（响应被污染或请求没到 handler）')
        finally:
            conn.close()

    def test_unsupported_media_415_discards_body(self):
        """415 早退同样必须丢弃请求体。"""
        conn = http.client.HTTPConnection('127.0.0.1', _PORT, timeout=20)
        try:
            conn.request('POST', '/api/rows',
                         body=b'table=work_items&op=insert&payload[title]=x',
                         headers={'Content-Type': 'application/x-www-form-urlencoded'})
            first = conn.getresponse()
            first.read()
            self.assertEqual(first.status, 415)

            conn.request('GET', '/api/health')
            second = conn.getresponse()
            body = second.read()
            self.assertEqual(second.status, 200,
                             f'415 之后同一连接的下一个请求失败了：{second.status} {body[:200]!r}')
            self.assertIn(b'"ok"', body)
        finally:
            conn.close()

    def test_unknown_api_post_discards_body(self):
        """404 早退是同一类缺陷（这里一并守住）。"""
        conn = http.client.HTTPConnection('127.0.0.1', _PORT, timeout=20)
        try:
            conn.request('POST', '/api/definitely-not-a-route',
                         body=b'{"a": 1}', headers={'Content-Type': 'application/json'})
            first = conn.getresponse()
            first.read()
            self.assertEqual(first.status, 404)

            conn.request('GET', '/api/health')
            second = conn.getresponse()
            second.read()
            self.assertEqual(second.status, 200)
        finally:
            conn.close()

    # ---- SEC-02：owner 行的 status 与 role 同样不可改 ----

    def test_team_admin_cannot_deactivate_owner(self):
        """admin 用"停用 owner"代替"降级 owner"，效果完全等价 —— 必须拦住。

        原来的护栏只锁了 role（`old_role == 'owner' and new_role != 'owner'`），
        把 owner 的 status 改成 inactive 完全放行 → owner 失权、团队被夺。
        """
        owner, owner_id = fresh_user('sec02-owner@example.com')
        team_id, code = _new_team(owner, 'SEC-02 组')
        admin, admin_id = fresh_user('sec02-admin@example.com')
        admin.post('/api/teams/join', {'code': code})
        owner.update('team_members', _member_row(owner, team_id, admin_id)['id'],
                     {'role': 'admin'})

        owner_row = _member_row(owner, team_id, owner_id)
        deactivate = admin.update('team_members', owner_row['id'], {'status': 'inactive'})
        self.assertEqual(deactivate.rows, [],
                         f'团队管理员把 owner 停用了：{deactivate.payload}')

        conn = db.connect(db_path())
        try:
            row = conn.execute(
                'SELECT role, status FROM team_members WHERE team_id = ? AND user_id = ?',
                (team_id, owner_id)).fetchone()
        finally:
            conn.close()
        self.assertEqual((row['role'], row['status']), ('owner', 'active'),
                         'owner 行的状态被改掉了')
        # 反向：owner 自己改自己的非状态字段仍然可以（别把功能改死）
        self.assertEqual(len(owner.update('team_members', owner_row['id'],
                                          {'title': '值班长'}).rows), 1)

    # ---- SEC-03：insert 路径不得产生第二个 owner ----

    def test_team_admin_cannot_insert_second_owner(self):
        owner, owner_id = fresh_user('sec03-owner@example.com')
        team_id, code = _new_team(owner, 'SEC-03 组')
        admin, admin_id = fresh_user('sec03-admin@example.com')
        admin.post('/api/teams/join', {'code': code})
        owner.update('team_members', _member_row(owner, team_id, admin_id)['id'],
                     {'role': 'admin'})

        intruder, intruder_id = fresh_user('sec03-intruder@example.com')
        resp = admin.insert('team_members', {
            'team_id': team_id, 'user_id': intruder_id, 'email': 'sec03-intruder@example.com',
            'display_name': '篡位者', 'role': 'owner', 'status': 'active',
        })
        # insert 被策略拒时是 403（与 test_16 "往他人团队硬插" 同一语义：
        # 插入路径的拒绝会抛 Denied，update/delete 才是静默返回空数组）
        self.assertEqual(resp.status, 403,
                         f'团队管理员插入 owner 行没有被拒：{resp.status} {resp.payload}')
        self.assertEqual(resp.code, 'PERMISSION_DENIED')

        conn = db.connect(db_path())
        try:
            owners = conn.execute(
                "SELECT user_id FROM team_members WHERE team_id = ? AND role = 'owner'",
                (team_id,)).fetchall()
        finally:
            conn.close()
        self.assertEqual([row['user_id'] for row in owners], [owner_id],
                         '团队出现了第二个 owner')

        # 反向：admin 插入普通成员仍然可以（别把功能改死）
        ok = admin.insert('team_members', {
            'team_id': team_id, 'user_id': 'u_plain_member', 'email': 'plain@example.com',
            'display_name': '普通成员', 'role': 'member', 'status': 'active',
        })
        self.assertEqual(len(ok.rows), 1, f'普通成员插入被误拦：{ok.payload}')

    # ---- SEC-04：私网判定不能被 IP 写法变体绕过 ----

    def test_base_url_rejects_ip_variants(self):
        """十进制 / 八进制 / 十六进制 / 短式 IP 都不能绕过"内网回环"判定。

        `ipaddress.ip_address('2852039166')` 抛 ValueError，而 socket 层能解析 ——
        旧实现把它当成"不是字面 IP"直接放行，于是
        `baseUrl=http://2852039166/v1`（=169.254.169.254）能保存成功，
        服务端随后把 `Authorization: Bearer sk-...` 发到宿主的监听器上。
        """
        admin = admin_client()
        variants = (
            'http://2852039166/v1',      # 169.254.169.254 —— 云元数据
            'http://2130706433/v1',      # 127.0.0.1
            'http://0177.0.0.1/v1',      # 八进制回环
            'http://0x7f000001/v1',      # 十六进制回环
            'http://127.1/v1',           # 短式回环
        )
        for url in variants:
            resp = admin.post('/api/llm/config', {'baseUrl': url})
            self.assertEqual(resp.status, 400, f'{url} 未被拦下：{resp.status} {resp.payload}')
            self.assertEqual(resp.code, 'INVALID_BASE_URL', f'{url} 的错误码不对：{resp.payload}')
        self.assertNotIn('2852039166', json.dumps(admin.get('/api/llm/config').payload))

    def test_base_url_allows_normal_domain(self):
        """防"一刀切"：正常公网域名必须仍然放行（自建模型服务是既有能力）。"""
        admin = admin_client()
        try:
            resp = admin.post('/api/llm/config', {'baseUrl': 'https://api.deepseek.com/v1'})
            self.assertEqual(resp.status, 200, f'正常域名被误拦：{resp.payload}')
            self.assertEqual(resp.code if resp.status >= 400 else None, None)
        finally:
            admin.post('/api/llm/config', {'baseUrl': 'https://api.openai.com/v1'})


# --------------------------------------------------------------------------
# 证书管理（GET/POST/DELETE /api/admin/cert）
# --------------------------------------------------------------------------

OPENSSL = shutil.which('openssl')
_CERT_FIXTURE_DIR = None
_CERT_FIXTURES = {}


def _run_openssl(args, directory):
    result = subprocess.run([OPENSSL, *args], cwd=str(directory),
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise unittest.SkipTest(f'openssl 生成测试证书失败：{result.stderr[:300]}')


def cert_fixture(kind):
    """生成/复用一对测试证书，返回 (cert_pem, key_pem)。

    为什么在测试里真生成而不是预置固定 PEM：私钥一旦进版本库，
    任何密钥扫描器都会报警，而它其实只是测试数据 —— 造出来的更干净。
    只在 openssl 不可用时 skip（CI 镜像一般都有）。
    """
    global _CERT_FIXTURE_DIR
    if kind in _CERT_FIXTURES:
        return _CERT_FIXTURES[kind]
    if _CERT_FIXTURE_DIR is None:
        _CERT_FIXTURE_DIR = tempfile.mkdtemp(prefix='gtd-cert-fixtures-')
    directory = Path(_CERT_FIXTURE_DIR)
    cert = directory / f'{kind}.crt'
    key = directory / f'{kind}.key'

    if kind == 'encrypted':
        _run_openssl(['genrsa', '-aes256', '-passout', 'pass:test-secret',
                      '-out', str(key), '2048'], directory)
        _run_openssl(['req', '-x509', '-new', '-key', str(key), '-passin', 'pass:test-secret',
                      '-out', str(cert), '-subj', '/CN=encrypted.example', '-days', '365'],
                     directory)
    else:
        common_name = 'localhost' if kind == 'valid' else f'{kind}.example'
        args = ['req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                '-keyout', str(key), '-out', str(cert), '-subj', f'/CN={common_name}',
                '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1']
        if kind == 'expired':
            # openssl 3.x 支持显式指定有效期，用来造"已过期"证书
            args += ['-not_before', '20200101000000Z', '-not_after', '20210101000000Z']
        else:
            args += ['-days', '825']
        _run_openssl(args, directory)

    pair = (cert.read_text('ascii'), key.read_text('ascii'))
    _CERT_FIXTURES[kind] = pair
    return pair


def _key_body_snippet(key_pem, length=32):
    """从私钥 PEM 里取一段 base64 正文，用来断言"它没出现在任何响应里"。"""
    body = [line for line in key_pem.splitlines()
            if line and not line.startswith('-----') and ':' not in line]
    return ''.join(body)[:length]


@unittest.skipUnless(OPENSSL, '需要 openssl 生成测试证书')
class CertAdminTest(unittest.TestCase):
    """证书管理三端点：鉴权、校验顺序、落盘权限、私钥不外泄。"""

    def setUp(self):
        security.reset_limits()
        self.admin = admin_client()
        self.member = cert_member_client()
        self._clear_cert_files()
        self.addCleanup(self._clear_cert_files)

    @staticmethod
    def _clear_cert_files():
        for name in ('tls.crt', 'tls.key', '.pending-apply'):
            path = Path(_CERT_DIR) / name
            if path.exists():
                path.unlink()
        # 运维侧生成的副本（tls-<SHA16>.crt/.key）与它自己的标记：用例里也要清掉，
        # 但它们**不属于后端管理范围**（见 test_cert_ignores_generator_copies）。
        for path in Path(_CERT_DIR).glob('tls-*'):
            path.unlink()
        for path in Path(_CERT_DIR).glob('.tmp-*'):
            path.unlink()
        # 元数据在 settings 表里（整库共享），不清会串到下一条用例：
        # 文件没了但 meta 还在 → GET 会把运维脚本生成的证书认成 uploaded。
        conn = db.connect(db_path())
        try:
            conn.execute("DELETE FROM settings WHERE key = 'tls_cert_meta'")
            conn.commit()
        finally:
            conn.close()

    def _upload(self, cert_pem, key_pem, client=None):
        return (client or self.admin).post(
            '/api/admin/cert', {'certPem': cert_pem, 'keyPem': key_pem})

    # -- 鉴权 ---------------------------------------------------------

    def test_cert_get_requires_admin(self):
        anon = Client().get('/api/admin/cert')
        self.assertEqual(anon.status, 401)
        self.assertEqual(anon.code, 'UNAUTHENTICATED')

        member = self.member.get('/api/admin/cert')
        self.assertEqual(member.status, 403)
        self.assertEqual(member.code, 'ADMIN_ONLY')

        # 三个端点的门槛必须一致（写和删比读更危险）
        cert_pem, key_pem = cert_fixture('valid')
        self.assertEqual(self._upload(cert_pem, key_pem, client=self.member).status, 403)
        self.assertEqual(self.member.request('DELETE', '/api/admin/cert').status, 403)
        self.assertFalse((Path(_CERT_DIR) / 'tls.crt').exists(),
                         '非管理员居然把证书写进磁盘了')

    def test_cert_unconfigured_shape(self):
        resp = self.admin.get('/api/admin/cert')
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.payload, {'configured': False, 'source': 'none',
                                        'pendingApply': False})

    # -- 校验顺序：格式 → 加密 → 配对 → 有效期 -------------------------

    def test_cert_upload_rejects_bad_pem(self):
        resp = self._upload('这不是证书', '也不是私钥')
        self.assertEqual(resp.status, 400)
        self.assertEqual(resp.code, 'CERT_INVALID_PEM')
        self.assertFalse((Path(_CERT_DIR) / 'tls.crt').exists())

    def test_cert_upload_rejects_encrypted_key(self):
        valid_cert, _ = cert_fixture('valid')
        _, encrypted_key = cert_fixture('encrypted')
        resp = self._upload(valid_cert, encrypted_key)
        self.assertEqual(resp.status, 400)
        self.assertEqual(resp.code, 'CERT_KEY_ENCRYPTED')
        self.assertNotIn('test-secret', json.dumps(resp.payload),
                         '错误响应里带出了私钥口令')

    def test_cert_upload_rejects_mismatched_pair(self):
        cert_pem, _ = cert_fixture('valid')
        _, other_key = cert_fixture('other')
        resp = self._upload(cert_pem, other_key)
        self.assertEqual(resp.status, 400)
        self.assertEqual(resp.code, 'CERT_KEY_MISMATCH')
        self.assertFalse((Path(_CERT_DIR) / 'tls.crt').exists())

    def test_cert_upload_rejects_expired(self):
        cert_pem, key_pem = cert_fixture('expired')
        resp = self._upload(cert_pem, key_pem)
        self.assertEqual(resp.status, 400)
        self.assertEqual(resp.code, 'CERT_EXPIRED')

    # -- 正常路径 -----------------------------------------------------

    def test_cert_upload_accepts_valid_pair(self):
        cert_pem, key_pem = cert_fixture('valid')
        resp = self._upload(cert_pem, key_pem)
        self.assertEqual(resp.status, 200, resp.payload)
        body = resp.payload

        self.assertIs(body['configured'], True)
        self.assertEqual(body['source'], 'uploaded')
        self.assertEqual(body['subject'], 'CN=localhost')
        self.assertEqual(body['issuer'], 'CN=localhost')
        self.assertIs(body['selfSigned'], True)
        self.assertIn('DNS:localhost', body['sans'])
        self.assertIn('IP Address:127.0.0.1', body['sans'])
        self.assertTrue(body['notBefore'].endswith('Z'), body['notBefore'])
        self.assertTrue(body['notAfter'].endswith('Z'), body['notAfter'])
        self.assertGreater(body['daysRemaining'], 100)
        self.assertRegex(body['fingerprintSha256'], r'^([0-9A-F]{2}:){31}[0-9A-F]{2}$')
        self.assertEqual(body['uploadedBy'], 'sysadmin@example.com')
        self.assertIs(body['pendingApply'], True)

        cert_path = Path(_CERT_DIR) / 'tls.crt'
        key_path = Path(_CERT_DIR) / 'tls.key'
        self.assertTrue(cert_path.is_file())
        self.assertTrue(key_path.is_file())
        self.assertEqual(cert_path.read_text('ascii').strip(), cert_pem.strip())
        self.assertEqual(key_path.read_text('ascii').strip(), key_pem.strip())
        if os.name == 'posix':
            # 权限只在 POSIX 上可断言（Windows 的 chmod 只映射只读位）
            self.assertEqual(os.stat(key_path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(cert_path).st_mode & 0o777, 0o644)
        self.assertTrue((Path(_CERT_DIR) / '.pending-apply').is_file(),
                        '没有创建待应用标记，Caddy 不会重新加载')
        # 临时文件必须清理干净：残留会让运维以为目录里多了神秘文件
        leftovers = [p.name for p in Path(_CERT_DIR).iterdir() if p.name.startswith('.tmp-')]
        self.assertEqual(leftovers, [], f'写盘后留下了临时文件：{leftovers}')

        # 再读一次：source/详情必须一致（元数据来自 settings，不是猜的）
        again = self.admin.get('/api/admin/cert').payload
        self.assertEqual(again['source'], 'uploaded')
        self.assertEqual(again['fingerprintSha256'], body['fingerprintSha256'])
        self.assertEqual(again['subject'], 'CN=localhost')

    def test_cert_upload_does_not_leak_key(self):
        cert_pem, key_pem = cert_fixture('valid')
        snippet = _key_body_snippet(key_pem)
        self.assertGreaterEqual(len(snippet), 20, '前置条件不成立：没取到私钥正文')

        upload = self._upload(cert_pem, key_pem)
        self.assertEqual(upload.status, 200)
        self.assertNotIn(snippet, upload.raw.decode('utf-8'), '上传响应里出现了私钥正文')
        self.assertNotIn('PRIVATE KEY', upload.raw.decode('utf-8'))

        read = self.admin.get('/api/admin/cert')
        self.assertNotIn(snippet, read.raw.decode('utf-8'), 'GET 响应里出现了私钥正文')
        self.assertNotIn('PRIVATE KEY', read.raw.decode('utf-8'))

        exported = self.admin.get('/api/export').raw.decode('utf-8')
        self.assertNotIn(snippet, exported, 'HTTP 导出里出现了私钥正文')
        self.assertNotIn('PRIVATE KEY', exported)
        self.assertNotIn('tls_cert_meta', exported, '导出里出现了证书元数据的键名')

        # 审计/运行日志里也不该有
        conn = db.connect(db_path())
        try:
            logs = conn.execute('SELECT message, detail FROM app_logs').fetchall()
        finally:
            conn.close()
        blob = json.dumps([dict(row) for row in logs], ensure_ascii=False)
        self.assertNotIn(snippet, blob, '日志里出现了私钥正文')

    def test_cert_source_is_selfsigned_when_no_meta(self):
        """运维脚本直接生成的证书（有文件、无上传记录）也要能正常显示。"""
        cert_pem, key_pem = cert_fixture('valid')
        (Path(_CERT_DIR) / 'tls.crt').write_text(cert_pem, encoding='ascii')
        (Path(_CERT_DIR) / 'tls.key').write_text(key_pem, encoding='ascii')

        body = self.admin.get('/api/admin/cert').payload
        self.assertIs(body['configured'], True)
        self.assertEqual(body['source'], 'selfsigned')
        self.assertEqual(body['subject'], 'CN=localhost')
        self.assertIsNone(body['uploadedBy'])
        self.assertIsNone(body['uploadedAt'])

    # -- 删除 ---------------------------------------------------------

    def test_cert_ignores_generator_copies(self):
        """运维生成器会在 /certs 里放自己的副本，后端必须**只认自己的两个文件**。

        `deploy/lib.sh` 换证书后会把证书复制成 `tls-<SHA256前16位>.crt/.key`
        并在 Caddyfile 里引用那个名字（否则"配置没变"会让 `caddy reload`
        判定 `config is unchanged` 而静默跳过，内存里还是旧证书）。
        因此 /certs 里会长期存在这些文件：
          · `tls.crt` / `tls.key`      ← 后端写的原件，**后端负责**
          · `tls-63AD...crt/.key`      ← 生成器副本，**不属于后端**
          · `.selfsigned`              ← gen-selfsigned.sh 的标记

        这条用例守住三件事：
          1. GET 只按固定文件名读，**不扫目录**（否则会把副本的指纹当成本证书）；
          2. DELETE 只删自己那两个文件，**绝不删运维的副本**（删了等于毁掉当前生效的那一对）；
          3. 多余的未知文件不会让状态判断出错。
        """
        cert_pem, key_pem = cert_fixture('valid')
        self.assertEqual(self._upload(cert_pem, key_pem).status, 200)

        # 造出"运维那一侧"的文件
        (Path(_CERT_DIR) / 'tls-63AD300720F933DC.crt').write_text(cert_pem, encoding='ascii')
        (Path(_CERT_DIR) / 'tls-63AD300720F933DC.key').write_text(key_pem, encoding='ascii')
        (Path(_CERT_DIR) / '.selfsigned').write_text('', encoding='ascii')

        body = self.admin.get('/api/admin/cert').payload
        self.assertIs(body['configured'], True)
        self.assertEqual(body['source'], 'uploaded')
        self.assertEqual(body['subject'], 'CN=localhost')

        self.assertEqual(self.admin.request('DELETE', '/api/admin/cert').status, 200)
        self.assertFalse((Path(_CERT_DIR) / 'tls.crt').exists())
        self.assertFalse((Path(_CERT_DIR) / 'tls.key').exists())
        self.assertTrue((Path(_CERT_DIR) / 'tls-63AD300720F933DC.crt').exists(),
                        'DELETE 把运维生成器那一侧的副本删了')
        self.assertTrue((Path(_CERT_DIR) / 'tls-63AD300720F933DC.key').exists())
        self.assertTrue((Path(_CERT_DIR) / '.selfsigned').exists(),
                        'DELETE 动了 gen-selfsigned.sh 的标记文件')

    def test_cert_get_keeps_upload_meta_when_fingerprint_matches(self):
        """没人在 API 之外换证书时，上传记录必须照常显示（别把元数据一律弃用）。"""
        cert_pem, key_pem = cert_fixture('valid')
        uploaded = self._upload(cert_pem, key_pem)
        self.assertEqual(uploaded.status, 200)

        body = self.admin.get('/api/admin/cert').payload
        self.assertIs(body['configured'], True)
        self.assertEqual(body['source'], 'uploaded')
        self.assertEqual(body['uploadedBy'], 'sysadmin@example.com')
        self.assertIsNotNone(body['uploadedAt'])
        self.assertEqual(body['fingerprintSha256'], uploaded.payload['fingerprintSha256'])

    def test_cert_get_prefers_file_over_stale_meta(self):
        """文件是唯一事实来源：API 之外被换掉的证书，不能继续显示旧的元数据。

        实测过的真实故障：API 上传 A（settings 记下 A 的指纹与 30 天有效期），
        运维脚本 `gen-selfsigned.sh --force` 覆盖成 3 年有效的自签名证书 B
        （只改文件、不改 settings）→ GET 仍在报"A，30 天后到期"。
        用户会被提示去续期一张**已经不在使用**的证书，而真正的证书 3 年内都有效。

        正确语义：
          · 详情一律现场解析 `/certs/tls.crt`；
          · `settings` 只补 `uploadedAt`/`uploadedBy`，**且必须指纹一致**才采信；
          · 指纹不一致 → `source=selfsigned`、上传人/时间置 null（那张不是 API 传的）。
        """
        a_cert, a_key = cert_fixture('valid')
        uploaded = self._upload(a_cert, a_key)
        self.assertEqual(uploaded.status, 200)
        self.assertEqual(uploaded.payload['subject'], 'CN=localhost')
        a_fingerprint = uploaded.payload['fingerprintSha256']
        self.assertIsNotNone(uploaded.payload['uploadedAt'])

        # 模拟"运维脚本 / 手工替换"：只改文件，settings 原封不动
        b_cert, b_key = cert_fixture('other')
        (Path(_CERT_DIR) / 'tls.crt').write_text(b_cert, encoding='ascii')
        (Path(_CERT_DIR) / 'tls.key').write_text(b_key, encoding='ascii')

        body = self.admin.get('/api/admin/cert').payload
        self.assertEqual(body['subject'], 'CN=other.example',
                         'GET 仍在报旧证书的信息（元数据压过了文件）')
        self.assertNotEqual(body['fingerprintSha256'], a_fingerprint,
                            '指纹还是旧证书的，说明没重新解析文件')
        self.assertEqual(body['source'], 'selfsigned',
                         '当前这张不是通过 API 传的，source 不能报 uploaded')
        self.assertIsNone(body['uploadedAt'], '旧证书的上传时间被算到了新证书头上')
        self.assertIsNone(body['uploadedBy'], '旧证书的上传人被告成了新证书的上传者')

        # 元数据本身不该被删（删了会丢"曾经传过什么"的痕迹，且 GET 只是读）
        conn = db.connect(db_path())
        try:
            row = conn.execute("SELECT value FROM settings WHERE key = 'tls_cert_meta'"
                               ).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row, 'GET 把元数据删掉了')
        self.assertEqual(json.loads(row['value'])['fingerprintSha256'], a_fingerprint)

    def test_cert_delete_is_idempotent(self):
        cert_pem, key_pem = cert_fixture('valid')
        self.assertEqual(self._upload(cert_pem, key_pem).status, 200)

        first = self.admin.request('DELETE', '/api/admin/cert')
        self.assertEqual(first.status, 200, first.payload)
        self.assertEqual(first.payload, {'configured': False, 'source': 'none',
                                         'pendingApply': True})
        self.assertFalse((Path(_CERT_DIR) / 'tls.crt').exists())
        self.assertFalse((Path(_CERT_DIR) / 'tls.key').exists())
        self.assertTrue((Path(_CERT_DIR) / '.pending-apply').is_file(),
                        '删除后没有创建待应用标记（Caddy 不会回到无证书状态）')

        second = self.admin.request('DELETE', '/api/admin/cert')
        self.assertEqual(second.status, 200, second.payload)
        self.assertEqual(second.payload['configured'], False)


@unittest.skipUnless(OPENSSL, '需要 openssl 生成测试证书')
class CertDirMissingTest(unittest.TestCase):
    """证书目录不存在 ⇒ 500 + 明确指向"部署问题"，且**不静默创建目录**。

    为什么不能自动 mkdir：挂载缺失时在容器只读层里建一个目录，会掩盖部署错误 ——
    证书写进去了、Caddy 看不到、重启即丢，排障方向会被彻底带偏。
    """

    @classmethod
    def setUpClass(cls):
        cls._data_dir = tempfile.mkdtemp(prefix='gtd4teams-certdir-')
        cls._cert_dir = str(Path(cls._data_dir) / 'no-such-certs')
        cls._port = free_port()
        config = Config(host='127.0.0.1', port=cls._port, data_dir=cls._data_dir,
                        cert_dir=cls._cert_dir)
        cls._server, _ = make_server(config)
        cls._thread = threading.Thread(target=cls._server.serve_forever, daemon=True)
        cls._thread.start()
        _create_user_directly('certdir-admin@example.com', data_dir=cls._data_dir,
                              is_system_admin=True)

    @classmethod
    def tearDownClass(cls):
        cls._server.shutdown()
        cls._server.server_close()

    def _call(self, method, path, payload=None, cookie=None):
        conn = http.client.HTTPConnection('127.0.0.1', self._port, timeout=20)
        headers = {}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        if cookie:
            headers['Cookie'] = cookie
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            status = resp.status
            set_cookie = resp.getheader('Set-Cookie') or ''
        finally:
            conn.close()
        parsed = json.loads(raw.decode('utf-8')) if raw.strip()[:1] == b'{' else None
        return status, parsed, set_cookie

    def test_missing_cert_dir_reports_deployment_problem(self):
        status, payload, set_cookie = self._call(
            'POST', '/api/auth/login',
            {'email': 'certdir-admin@example.com', 'password': ADMIN_PASSWORD})
        self.assertEqual(status, 200, payload)
        cookie = set_cookie.split(';')[0]

        cert_pem, key_pem = cert_fixture('valid')
        status, payload, _ = self._call('POST', '/api/admin/cert',
                                        {'certPem': cert_pem, 'keyPem': key_pem},
                                        cookie=cookie)
        self.assertEqual(status, 500, payload)
        self.assertEqual(payload['error']['code'], 'CERT_DIR_MISSING')
        message = payload['error']['message']
        self.assertIn('/certs', message)
        self.assertTrue('部署' in message or '挂载' in message,
                        f'文案没有指向部署问题：{message}')
        self.assertFalse(os.path.exists(self._cert_dir), '后端静默创建了证书目录')

        # GET 不因此 500：没目录就等于"没有证书"，页面要能正常打开
        status, payload, _ = self._call('GET', '/api/admin/cert', cookie=cookie)
        self.assertEqual(status, 200, payload)
        self.assertIs(payload['configured'], False)


class AccessLogRedactionTest(unittest.TestCase):
    """访问日志的脱敏与防注入（G-08）。

    `self.path` 是**客户端可控的原始 URL**，直接拼进日志有两个后果：
      1. 查询串里的 PII（邮箱、令牌）被落盘 —— 安全审计实测到
         `! GET /api/auth/check-email?email=x@y.test -> 404`；
      2. 换行/控制字符能把一行日志**伪造**成两行（日志投毒）：
         `GET /api/x%0d%0aFAKE` 之类。只抹值不管换行，这条日志仍可被投毒。
    """

    def test_redacts_query_values(self):
        from pyapp.httpd import Handler

        cases = {
            '/api/auth/check-email?email=secret@x.test': '/api/auth/check-email?email=***',
            '/api/x?email=a@b&token=abc':
                '/api/x?email=***&token=***',
            '/api/x?a=1&b=2&c=3': '/api/x?a=***&b=***&c=***',
            # 路径部分必须原样保留（排障要知道打的是哪个端点）
            '/api/workspace': '/api/workspace',
            '/api/workspace?teamId=12': '/api/workspace?teamId=***',
            # 裸参数不含值，保留原样
            '/api/x?debug': '/api/x?debug',
            # 空 query 保持原形态
            '/api/x?': '/api/x?',
        }
        for raw, expected in cases.items():
            self.assertEqual(Handler._redact_path(raw), expected, f'{raw} 脱敏结果不对')

    def test_redact_path_strips_control_chars_and_crlf(self):
        from pyapp.httpd import Handler

        # 日志注入：换行必须在**压平**后才落盘，否则能伪造出一条"看起来合法"的日志行
        injected = '/api/x\r\nFAKE_LOG_LINE admin login ok'
        cleaned = Handler._redact_path(injected)
        self.assertNotIn('\r', cleaned)
        self.assertNotIn('\n', cleaned)
        self.assertIn('FAKE_LOG_LINE', cleaned,
                      '路径部分的内容应当被压平到同一行（而不是整段丢弃，丢掉就看不见有人在注入）')
        self.assertEqual(len(cleaned.splitlines()), 1)

        # 写在 query 值里的注入尝试更彻底：值本来就会被抹成 ***，
        # 于是攻击载荷连内容都不会落盘（这是脱敏顺带得到的好处）。
        self.assertNotIn('FAKE', Handler._redact_path('/api/x?a=1\r\nFAKE_LOG_LINE'))

        # 其它控制字符（NUL / ESC / 制表 / DEL）同理
        for char in ('\t', '\x00', '\x1b', '\x7f'):
            self.assertNotIn(char, Handler._redact_path(f'/api/x{char}y'))

    def test_redact_path_truncates_long_urls(self):
        from pyapp.httpd import Handler

        cleaned = Handler._redact_path('/api/' + 'a' * 500)
        self.assertLessEqual(len(cleaned), 200)
        self.assertTrue(cleaned.endswith('…'), cleaned[-10:])
        # 超长 query 已经被值掩码吃掉长度，两条路径都要有界
        self.assertLessEqual(len(Handler._redact_path('/api/x?q=' + 'a' * 500)), 200)

    def test_access_log_output_is_redacted(self):
        """集成断言：真正**落盘**的那一行不含 PII，且保留键名。

        这条用例原来捕获 stdout（旧实现是 `print`）。改成写文件之后，
        断言也必须打在文件上 —— 否则它证明的只是"某处打印了什么"，
        而真正会被采集、被 grep 的是文件里的那一行。
        """
        resp = Client().get('/api/auth/check-email?email=secret@x.test&token=tok-123')
        self.assertEqual(resp.status, 404)

        entry = _wait_log(lambda es: es[-1] if es and 'check-email' in es[-1]['path'] else None)
        self.assertIsNotNone(entry, '这条请求没有留下日志')
        written = json.dumps(entry, ensure_ascii=False)
        self.assertIn('email=***', entry['path'], f'日志没有脱敏：{written}')
        self.assertNotIn('secret@x.test', written, f'日志里出现了邮箱：{written}')
        self.assertNotIn('tok-123', written, f'日志里出现了令牌：{written}')
        self.assertIn('/api/auth/check-email', entry['path'],
                      '路径被抹掉了，排障时看不出打的是哪个端点')


def _log_file_path():
    """访问日志的真实文件位置（取自运行中的实例，不硬编码 data_dir/logs）。"""
    return Path(_APP.access_log.path)


def _log_entries():
    """把日志文件解析成 list[dict] —— 顺便验证了"每一行都是合法 JSON"。"""
    path = _log_file_path()
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding='utf-8').splitlines():
        text = line.strip()
        if text:
            entries.append(json.loads(text))
    return entries


def _wait_log(predicate, timeout=5.0):
    """轮询直到 `predicate(entries)` 返回真值，返回该值（超时返回 None）。

    为什么必须轮询，不能直接读"最后一行"：日志是在响应**写完之后**才写的
    （`_send` 先把 body 写出去，再调 `_log_access`），而客户端一读到完整 body
    就返回了 —— 两者之间有微秒级竞态。直接断言最后一行会偶发读到上一条
    （首轮实测就踩到过：9 条用例里偶发 1 条失败）。
    """
    deadline = time.time() + timeout
    while True:
        entries = _log_entries()
        hit = predicate(entries)
        if hit:
            return hit
        if time.time() > deadline:
            return None
        time.sleep(0.01)


def _wait_logged_path(fragment, timeout=5.0):
    """等某条"path 含 fragment"的记录出现并返回它。"""
    return _wait_log(lambda es: next(
        (e for e in reversed(es) if fragment in e['path']), None), timeout=timeout)


def _trigger_internal_error(client):
    """制造一次真正的 500（不是伪造响应），返回该次响应。"""
    from pyapp import store as store_module

    original = store_module.select_table

    def boom(conn, ctx, table, **kwargs):
        raise RuntimeError('boom: no such table: work_items')

    store_module.select_table = boom
    try:
        return client.get('/api/workspace')
    finally:
        store_module.select_table = original


class AccessLogTest(unittest.TestCase):
    """结构化访问日志（JSONL）：每请求一条、成功也记、脱敏、编号对账、健康检查排除。

    这一组守的是"监控有没有数据源"这件事本身。在此之前，成功的请求完全不留痕，
    于是 M07~M13 那些指标全部无源可查 —— 而告警是建立在数据源之上的。
    """

    def test_success_requests_are_logged(self):
        """本次变更的核心：**成功的请求也要有日志**。"""
        client, user_id = fresh_user('log200@example.com')

        resp = client.get('/api/workspace')
        self.assertEqual(resp.status, 200)

        entry = _wait_log(lambda es: es[-1] if es and es[-1]['path'] == '/api/workspace' else None)
        self.assertIsNotNone(entry, '200 请求没有留下日志（核心变化未生效）')
        self.assertEqual(entry['status'], 200)
        self.assertEqual(entry['method'], 'GET')
        self.assertEqual(entry['uid'], user_id, '已登录请求必须能按 uid 聚合')

    def test_access_log_is_json_line(self):
        """每条记录都是单行 JSON，且 8 个字段齐全（字段缺失会让下游指标静默算错）。"""
        client, _ = fresh_user('logjson@example.com')
        client.get('/api/workspace')
        self.assertIsNotNone(_wait_logged_path('/api/workspace'))

        path = _log_file_path()
        last_line = [line for line in path.read_text(encoding='utf-8').splitlines()
                     if line.strip()][-1]
        # 单行 = 这条记录自己就能被 json.loads；多行会把 JSONL 变成不可解析的文本
        self.assertNotIn('\n', last_line)
        entry = json.loads(last_line)

        for field in ('ts', 'request_id', 'uid', 'ip', 'method', 'path',
                      'status', 'errcode', 'duration_ms'):
            self.assertIn(field, entry, f'缺少字段 {field}：{entry}')
        self.assertIsInstance(entry['status'], int)
        self.assertIsInstance(entry['duration_ms'], (int, float))
        self.assertIsNone(entry['errcode'], f'200 请求的 errcode 必须是 null：{entry}')
        self.assertRegex(entry['ts'], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$',
                         f'ts 不是 ISO8601(ms, UTC)：{entry["ts"]}')
        self.assertRegex(entry['request_id'], r'^[0-9a-f]{8}$',
                         f'request_id 不是 8 位十六进制：{entry["request_id"]}')

        # 未登录的请求必须是 JSON null，不能是 'anonymous'/''（否则聚合会多出一个假用户）
        Client().get('/api/auth/session')
        anon = _wait_log(lambda es: next(
            (e for e in reversed(es) if e['path'] == '/api/auth/session'), None))
        self.assertIsNotNone(anon, '/api/auth/session 没有留下日志')
        self.assertIsNone(anon['uid'], '匿名请求的 uid 必须是 null')

    def test_access_log_redacts_query(self):
        """记录量放大几十倍之后，脱敏不能有任何旁路。"""
        Client().get('/api/x?email=secret@test.com&token=abc123')

        entry = _wait_logged_path('/api/x?')
        self.assertIsNotNone(entry, '这条请求没有留下日志')
        self.assertEqual(entry['path'], '/api/x?email=***&token=***')
        raw_file = _log_file_path().read_text(encoding='utf-8')
        self.assertNotIn('secret@test.com', raw_file, '明文邮箱落盘了')
        self.assertIn('email=***', entry['path'], '键名要保留，否则排障看不出传了哪些参数')

    def test_access_log_request_id_matches_error_response(self):
        """客户端拿到的编号必须等于日志里的 request_id —— 否则报错无从对账。

        旧实现里 500 的编号是 `_internal_error` 自己现生成的，而正常请求
        根本没有编号；`_internal_error` 复用 `_dispatch` 的编号之后，
        用户的"请将编号 xxx 提供给管理员"才真的能查到那一行。
        """
        client, _ = fresh_user('log500@example.com')
        resp = _trigger_internal_error(client)
        self.assertEqual(resp.status, 500)

        body = resp.raw.decode('utf-8')
        match = re.search(r'[0-9a-f]{8}', body)
        self.assertIsNotNone(match, f'响应里没有可对账的编号：{body}')
        reported = match.group(0)

        entry = _wait_log(lambda es: next(
            (e for e in reversed(es) if e['request_id'] == reported), None))
        self.assertIsNotNone(entry, f'日志里查不到客户端拿到的编号 {reported}')
        self.assertEqual(entry['status'], 500)
        self.assertEqual(entry['path'], '/api/workspace')

    def test_access_log_carries_errcode(self):
        """`errcode` 让"这个 500 是不是内部错误"变成结构化查询，而不是 grep traceback。

        指标 A4（写锁 / 内部错误关键字）以前只能靠 `grep -iE 'database i...'`
        去匹配异常文本 —— 文本一改、语言一换就失效，而且 500 之外的失败
        （403 ADMIN_ONLY 这种）根本无从区分。
        """
        client, _ = fresh_user('errcode@example.com')

        # ① 正常请求：null（不是 ''/'OK'，聚合时 null 才是"没有错误码"）
        client.get('/api/workspace')
        entry = _wait_log(lambda es: es[-1] if es and es[-1]['path'] == '/api/workspace'
                          and es[-1]['status'] == 200 else None)
        self.assertIsNotNone(entry)
        self.assertIsNone(entry['errcode'])

        # ② 业务失败：记 ApiError 的错误码，且与响应体里的 code 一致
        resp = client.get('/api/logs')
        self.assertEqual(resp.status, 403)
        entry = _wait_log(lambda es: es[-1] if es and es[-1]['path'] == '/api/logs'
                          else None)
        self.assertIsNotNone(entry)
        self.assertEqual(entry['errcode'], 'ADMIN_ONLY')
        self.assertEqual(resp.code, entry['errcode'])

        # ③ 兜底 500：记 INTERNAL_ERROR（与响应体一致，客户端能对上）
        resp = _trigger_internal_error(client)
        self.assertEqual(resp.status, 500)
        entry = _wait_log(lambda es: es[-1] if es and es[-1]['status'] == 500 else None)
        self.assertIsNotNone(entry)
        self.assertEqual(entry['errcode'], 'INTERNAL_ERROR')

    def test_health_not_logged(self):
        """Caddy 每 3 秒一次的健康检查不进日志（否则真正的记录会被挤掉）。"""
        time.sleep(0.1)                  # 等前面用例的日志落定，避免误判为"新增"
        before = len(_log_entries())
        for _ in range(5):
            self.assertEqual(Client().get('/api/health').status, 200)
        time.sleep(0.1)
        self.assertEqual(len(_log_entries()), before,
                         '/api/health 被写进了访问日志：一天 28800 条会把轮转窗口挤满')

    def test_protocol_level_error_is_logged(self):
        """协议层错误（501 不支持的方法）也要留痕。

        这些响应由 BaseHTTPRequestHandler 在**解析阶段**直接发出，不经过 _dispatch，
        是"有没有人在扫我们"的最早信号。旧实现下它们一条日志都没有。
        """
        sock = socket.create_connection(('127.0.0.1', _PORT), timeout=20)
        try:
            sock.sendall(b'BOGUS /api/probe-501 HTTP/1.1\r\nHost: test\r\n\r\n')
            first = sock.recv(200)
        finally:
            sock.close()
        self.assertIn(b'501', first.split(b'\r\n')[0])

        entry = _wait_logged_path('/api/probe-501')
        self.assertIsNotNone(entry, '协议层 501 没有留下日志')
        self.assertEqual(entry['status'], 501)
        self.assertEqual(entry['method'], 'BOGUS')

    def test_one_line_per_request(self):
        """每请求**恰好一条**：既不能漏（500 没日志），也不能重（QPS 翻倍）。"""
        time.sleep(0.1)
        before = len(_log_entries())
        Client().get('/api/auth/session')
        self.assertIsNotNone(_wait_log(lambda es: len(es) > before))
        time.sleep(0.1)
        self.assertEqual(len(_log_entries()) - before, 1,
                         '一个请求写了不止一条（或一条都没写）')

    def test_get_logs_requires_admin(self):
        """成员 403 / 匿名 401 / 管理员 200 —— 日志含全站 IP 与身份，只给实例管理员。"""
        self.assertEqual(Client().get('/api/logs').status, 401, '匿名居然能读日志')

        member, _ = fresh_user('logreader-member@example.com')
        resp = member.get('/api/logs')
        self.assertEqual(resp.status, 403)
        self.assertEqual(resp.code, 'ADMIN_ONLY')

        admin = admin_client()
        resp = admin.get('/api/logs')
        self.assertEqual(resp.status, 200, resp.payload)
        payload = resp.payload
        self.assertEqual(set(payload) - {'logs', 'total', 'truncated',
                                        'fileLogging', 'degradeReason'}, set(),
                         f'返回结构多余字段：{sorted(payload)}')
        self.assertIsInstance(payload['logs'], list)
        self.assertIsInstance(payload['total'], int)
        self.assertIsInstance(payload['truncated'], bool)
        # 空结果必须无歧义："没请求" 还是 "日志没写进去"
        self.assertIs(payload['fileLogging'], True)
        self.assertIsNone(payload['degradeReason'])
        self.assertGreater(payload['total'], 0, 'total 应当反映文件实际行数')
        self.assertTrue(payload['logs'], '管理员读到了空日志（但文件里有记录）')
        for entry in payload['logs']:
            self.assertIn('request_id', entry)
        # 这条接口自身也该被记下来（运维读日志的动作同样要留痕）
        self.assertIsNotNone(_wait_logged_path('/api/logs'))

    def test_logs_endpoint_filters(self):
        """status / path / q 三个过滤都要真的生效，且 limit 有上限。"""
        client, _ = fresh_user('logfilter@example.com')
        token = f'logfilter-{uuid.uuid4().hex[:8]}'
        client.get(f'/api/{token}')                 # 404，路径唯一
        self.assertIsNotNone(_wait_logged_path(token))
        _trigger_internal_error(client)             # 500
        self.assertIsNotNone(_wait_log(lambda es: next(
            (e for e in reversed(es) if e['status'] == 500), None)))

        admin = admin_client()
        unique_path = f'/api/{token}'
        resp = admin.get(f'/api/logs?path={unique_path}')
        self.assertEqual(resp.status, 200)
        self.assertTrue(resp.payload['logs'], 'path 过滤没有命中刚刚那一条')
        for entry in resp.payload['logs']:
            self.assertIn(token, entry['path'])

        resp = admin.get('/api/logs?status=500&limit=10')
        self.assertEqual(resp.status, 200)
        self.assertTrue(resp.payload['logs'], 'status=500 过滤没有命中刚制造的 500')
        for entry in resp.payload['logs']:
            self.assertEqual(entry['status'], 500)
        self.assertLessEqual(len(resp.payload['logs']), 10)

        # q 是全文子串：可以用来按 request_id 定位那一条
        wanted = resp.payload['logs'][-1]['request_id']
        resp = admin.get(f'/api/logs?q={wanted}')
        self.assertEqual(resp.status, 200)
        self.assertTrue(any(e['request_id'] == wanted for e in resp.payload['logs']),
                        f'q={wanted} 没有搜到那条记录')

        # limit 上限硬性生效（防"一次把 300MB 读进内存"）
        resp = admin.get('/api/logs?limit=100000')
        self.assertEqual(resp.status, 200)
        self.assertLessEqual(len(resp.payload['logs']), 1000)

        # 非法参数要明确报错，而不是静默忽略（否则运维会以为过滤生效了）
        self.assertEqual(admin.get('/api/logs?limit=abc').status, 400)
        self.assertEqual(admin.get('/api/logs?status=9999').status, 400)


class AccessLogDegradedTest(unittest.TestCase):
    """日志目录不可用 ⇒ 降级为只写 stderr，**服务照常工作**。

    这是"日志是辅助不是依赖"的硬约束：因为日志写不了而拒绝服务，
    等于把可用性押在最不重要的组件上。用"在 data_dir 下放一个同名文件"
    来稳定制造 mkdir 失败（跨平台、不依赖 chmod 的语义差异）。
    """

    @classmethod
    def setUpClass(cls):
        cls._data_dir = tempfile.mkdtemp(prefix='gtd4teams-nolog-')
        # data_dir/logs 是个**文件**：mkdir 必然失败（FileExistsError）
        Path(cls._data_dir, 'logs').write_text('not a directory', encoding='utf-8')
        cls._port = free_port()
        cls._server, cls._app = make_server(
            Config(host='127.0.0.1', port=cls._port, data_dir=cls._data_dir))
        cls._thread = threading.Thread(target=cls._server.serve_forever, daemon=True)
        cls._thread.start()
        # 一个系统管理员：用来验证"日志写不进去"时 /api/logs 不会假装"没有请求"
        _create_user_directly('nolog-admin@example.com', data_dir=cls._data_dir,
                              is_system_admin=True)

    @classmethod
    def tearDownClass(cls):
        cls._server.shutdown()
        cls._server.server_close()

    def test_degraded_to_stderr_but_service_works(self):
        self.assertEqual(self._get('/api/health'), 200, '日志写不了居然影响了服务')
        self.assertTrue(self._app.access_log.degraded, '没有报出降级状态')
        self.assertIsNotNone(self._app.access_log.problem, '降级时必须说明原因')
        self.assertFalse(self._app.access_log.status()['fileLogging'])

        # 没有日志文件被创建（因为那个路径上是个文件）
        self.assertTrue(Path(self._data_dir, 'logs').is_file())
        self.assertFalse(Path(self._data_dir, 'logs', 'app.jsonl').exists())

        # 业务请求照常（这条会走 _log_access，写 stderr 而不是文件）
        self.assertEqual(self._get('/api/auth/session'), 200)

    def test_logs_endpoint_does_not_fake_empty_result(self):
        """日志写不进去时，`/api/logs` 必须**明说**，不能回一个空数组。

        `logs: []` 有两种含义："真的没有请求" 与 "日志没写进去"。
        不区分就等于让"空"看起来正常 —— 那正是本项目反复踩过的病
        （`probe.sh` 空档 exit 0、`config is unchanged` 仍打印重载成功）。
        """
        status, payload, cookie = self._request(
            'POST', '/api/auth/login',
            {'email': 'nolog-admin@example.com', 'password': ADMIN_PASSWORD})
        self.assertEqual(status, 200, payload)

        status, payload, _ = self._request('GET', '/api/logs', cookie=cookie)
        self.assertEqual(status, 200, payload)
        self.assertIs(payload['fileLogging'], False, '降级了却报 fileLogging=true')
        self.assertIsNotNone(payload['degradeReason'], '降级了却没给原因')
        self.assertIn('不可', payload['degradeReason'])
        self.assertEqual(payload['total'], 0)
        self.assertEqual(payload['logs'], [])
        self.assertIs(payload['truncated'], False)
        # 内部标记（读文件失败的原始 errno 文案）绝不能泄进响应
        self.assertEqual(set(payload), {'logs', 'total', 'truncated',
                                        'fileLogging', 'degradeReason'},
                         f'响应里出现了多余字段：{sorted(payload)}')

    def _request(self, method, path, payload=None, cookie=None):
        conn = http.client.HTTPConnection('127.0.0.1', self._port, timeout=20)
        headers = {}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        if cookie:
            headers['Cookie'] = cookie
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            status = resp.status
            set_cookie = resp.getheader('Set-Cookie') or ''
        finally:
            conn.close()
        parsed = json.loads(raw.decode('utf-8')) if raw.strip()[:1] in (b'{', b'[') else None
        return status, parsed, set_cookie.split(';')[0]

    def _get(self, path):
        conn = http.client.HTTPConnection('127.0.0.1', self._port, timeout=20)
        try:
            conn.request('GET', path)
            resp = conn.getresponse()
            resp.read()
            return resp.status
        finally:
            conn.close()


class AppLogReaderTest(unittest.TestCase):
    """`applog.read_recent` 的反向分块读取（不经过 HTTP，直接打函数）。

    HTTP 层的用例只能覆盖"默认块大小"那一档，而反向读的坑几乎全在**块边界**上：
    一行被切成两半时，少拼一段就会静默丢记录或读出半条 JSON。
    所以这里用 64 字节的小块把边界踩密。
    """

    def setUp(self):
        from pyapp import applog
        self.applog = applog
        self._dir = tempfile.mkdtemp(prefix='gtd4teams-logread-')
        self.path = str(Path(self._dir) / 'app.jsonl')
        with open(self.path, 'w', encoding='utf-8') as handle:
            for index in range(500):
                handle.write(json.dumps({
                    'n': index,
                    'status': 500 if index % 100 == 0 else 200,
                    'path': f'/api/x{index}',
                }) + '\n')

    def _read(self, **kwargs):
        kwargs.setdefault('block_size', 64)      # 小块：把块边界踩密
        return self.applog.read_recent(self.path, **kwargs)

    def test_returns_tail_in_chronological_order(self):
        result = self._read(limit=3)
        self.assertEqual([e['n'] for e in result['logs']], [497, 498, 499],
                         '返回的必须是最近 3 条，且按时间正序（tail 语义）')
        self.assertEqual(result['total'], 500)
        self.assertTrue(result['truncated'], '还有更早的记录没返回，必须报 truncated')

    def test_reads_whole_file_when_limit_exceeds_records(self):
        result = self._read(limit=1000)
        self.assertEqual(len(result['logs']), 500, '块边界拼接丢了记录')
        self.assertEqual(result['logs'][0]['n'], 0)
        self.assertEqual(result['logs'][-1]['n'], 499)
        self.assertFalse(result['truncated'])

    def test_filters_are_applied_while_reading(self):
        # 命中数达到 limit 就停：结果必须是**最近**的 3 条 500，不是最早那 3 条。
        recent_three = self._read(limit=3, status=500)
        self.assertEqual([e['n'] for e in recent_three['logs']], [200, 300, 400])
        self.assertTrue(recent_three['truncated'], '还有更早的命中没返回，必须报 truncated')
        # 恰好读完全部命中时要如实报"没有更多"（不能一律报 True）
        five = self._read(limit=5, status=500)
        self.assertEqual([e['n'] for e in five['logs']], [0, 100, 200, 300, 400])
        self.assertFalse(five['truncated'], '已经读到文件头了，不该再报"还有更早的"')
        # 命中数不足 limit 时必须读到文件头，并如实报"没有更多"
        all_of_them = self._read(limit=100, status=500)
        self.assertEqual(len(all_of_them['logs']), 5)
        self.assertFalse(all_of_them['truncated'])

    def test_tolerates_half_written_last_line(self):
        with open(self.path, 'ab') as handle:
            handle.write(b'{"n":9999,"stat')      # 进程被 kill 时留下的残行
        result = self._read(limit=1)
        self.assertEqual(result['logs'][-1]['n'], 499, '残行被当成了记录')

    def test_unreadable_path_is_not_an_error(self):
        """父路径是普通文件 / 文件是目录 ⇒ 空结果，**绝不抛异常**。

        这条守的是跨平台差异（实测过，两边异常类型不一样）：

        | 场景                | Windows                  | Linux                     |
        |---------------------|--------------------------|---------------------------|
        | `<logs>` 是普通文件 | `FileNotFoundError` (2)  | `NotADirectoryError` (20) |
        | 目标路径是目录      | `PermissionError` (13)   | `IsADirectoryError` (21)  |

        旧实现只接了 `OSError` → 抛 `LogReadError` → 接口 500。
        Windows 上侥幸走成"文件不存在 → 空结果"，Linux 上直接红 ——
        L0 门禁就是这样把它拦下来的。
        """
        from pyapp import applog

        # ① 父路径是普通文件：两边都必须安静地返回空结果
        broken = Path(self._dir) / 'logs'
        broken.write_text('not a directory', encoding='utf-8')
        result = applog.read_recent(str(broken / 'app.jsonl'))
        self.assertEqual(result['logs'], [])
        self.assertEqual(result['total'], 0)
        self.assertFalse(result['truncated'])

        # ② 目标路径是目录：两边都会进"读不动"分支（标记用于告知调用方原因）
        as_dir = Path(self._dir) / 'isadir.jsonl'
        as_dir.mkdir()
        result = applog.read_recent(str(as_dir))
        self.assertEqual(result['logs'], [])
        self.assertIn(applog.READ_UNAVAILABLE, result,
                      '读不动时必须带标记，否则调用方分不清"没日志"与"读不了"')

        # ③ 文件不存在 ⇒ 真·空结果（"一条请求都没来过"），不带标记
        result = applog.read_recent(str(Path(self._dir) / 'nope.jsonl'))
        self.assertEqual(result['logs'], [])
        self.assertNotIn(applog.READ_UNAVAILABLE, result,
                         '文件不存在不该被当成"读不动"（那是正常状态）')


class LlmHelperTest(unittest.TestCase):
    def test_strip_code_fence(self):
        self.assertEqual(strip_code_fence('```json\n{"a":1}\n```'), '{"a":1}')
        self.assertEqual(strip_code_fence('```\n{"a":1}\n```'), '{"a":1}')
        self.assertEqual(strip_code_fence('{"a":1}'), '{"a":1}')
        self.assertEqual(strip_code_fence(''), '')


class AdminConfigTest(unittest.TestCase):
    """GTD_ADMIN_EMAILS 的解析规则。"""

    def test_defaults_to_nobody(self):
        """没配 = 谁都不能经 HTTP 改 AI 配置。安全默认应当是关，而不是开。"""
        previous = os.environ.pop('GTD_ADMIN_EMAILS', None)
        try:
            cfg = Config()
        finally:
            if previous is not None:
                os.environ['GTD_ADMIN_EMAILS'] = previous
        self.assertEqual(cfg.admin_emails, frozenset())
        self.assertFalse(cfg.is_admin_email('anyone@example.com'))
        self.assertFalse(cfg.is_admin_email(''))
        self.assertFalse(cfg.is_admin_email(None))

    def test_env_parsing_tolerates_spaces_and_case(self):
        """多打的空格、大小写不一致都不该让配置静默失效。"""
        previous = os.environ.get('GTD_ADMIN_EMAILS')
        os.environ['GTD_ADMIN_EMAILS'] = ' Boss@Example.com , ops@example.com ,, '
        try:
            cfg = Config()
        finally:
            if previous is None:
                os.environ.pop('GTD_ADMIN_EMAILS', None)
            else:
                os.environ['GTD_ADMIN_EMAILS'] = previous
        self.assertEqual(cfg.admin_emails,
                         frozenset({'boss@example.com', 'ops@example.com'}))
        self.assertTrue(cfg.is_admin_email('BOSS@EXAMPLE.COM'))
        self.assertTrue(cfg.is_admin_email('  ops@example.com  '))
        self.assertFalse(cfg.is_admin_email('intern@example.com'))


class NoAdminConfiguredTest(unittest.TestCase):
    """GTD_ADMIN_EMAILS 为空时，HTTP 侧一律 403，并明确告知怎么配。

    这条守着"安全默认"本身：默认关闭比默认人人可写重要得多，
    没有管理员的部署不该因为"没人配"就退化成"谁都能改"。
    """

    @classmethod
    def setUpClass(cls):
        cls._data_dir = tempfile.mkdtemp(prefix='gtd4teams-noadmin-')
        cls._port = free_port()
        config = Config(host='127.0.0.1', port=cls._port, data_dir=cls._data_dir,
                        admin_emails=[])
        cls._server, _ = make_server(config)
        cls._thread = threading.Thread(target=cls._server.serve_forever, daemon=True)
        cls._thread.start()

    @classmethod
    def tearDownClass(cls):
        cls._server.shutdown()
        cls._server.server_close()

    def _call(self, method, path, payload=None, cookie=None):
        conn = http.client.HTTPConnection('127.0.0.1', self._port, timeout=20)
        headers = {}
        body = None
        if payload is not None:
            body = json.dumps(payload).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        if cookie:
            headers['Cookie'] = cookie
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            status = resp.status
            set_cookie = resp.getheader('Set-Cookie') or ''
        finally:
            conn.close()
        parsed = json.loads(raw.decode('utf-8')) if raw.strip()[:1] in (b'{', b'[') else None
        return status, parsed, set_cookie

    def test_account_creation_without_any_system_admin_explains_bootstrap(self):
        """零系统管理员时，建号失败要说清"怎么引导"，而不是只说"你没权限"。

        这条只是**错误文案**的改善，不是全局闸门：零管理员时既有用户的业务功能
        必须照常工作（否则一次"忘了引导"会把整站锁死）。
        """
        _create_user_directly('bootstrap@example.com', data_dir=self._data_dir)
        status, payload, set_cookie = self._call(
            'POST', '/api/auth/login',
            {'email': 'bootstrap@example.com', 'password': ADMIN_PASSWORD})
        self.assertEqual(status, 200, payload)
        cookie = set_cookie.split(';')[0]

        status, payload, _ = self._call(
            'POST', '/api/admin/accounts', {'email': 'want-account@example.com'}, cookie=cookie)
        self.assertEqual(status, 403, payload)
        self.assertEqual(payload['error']['code'], 'NO_SYSTEM_ADMIN')
        self.assertIn('--create-admin', payload['error']['message'],
                      '应当直接给出引导命令')

        # 同一账号的业务功能不受影响（这不是全局闸门）
        status, workspace, _ = self._call('GET', '/api/workspace', cookie=cookie)
        self.assertEqual(status, 200, workspace)

    def test_empty_admin_list_blocks_everyone(self):
        _create_user_directly('noadmin@example.com', data_dir=self._data_dir)
        status, payload, set_cookie = self._call(
            'POST', '/api/auth/login',
            {'email': 'noadmin@example.com', 'password': ADMIN_PASSWORD})
        self.assertEqual(status, 200, payload)
        cookie = set_cookie.split(';')[0]

        status, payload, _ = self._call(
            'POST', '/api/llm/config',
            {'apiKey': 'sk-should-never-be-saved'}, cookie=cookie)
        self.assertEqual(status, 403, f'没有管理员却能改配置：{payload}')
        self.assertEqual((payload or {}).get('error', {}).get('code'), 'ADMIN_ONLY')
        message = payload['error']['message']
        self.assertIn('--grant-admin', message,
                      '未配置实例管理员时应当明确告知怎么授予（CLI 是唯一入口）')

        status, payload, _ = self._call('GET', '/api/llm/config', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertFalse(payload['apiKeySet'], '没有管理员却能写密钥')


class TrustProxyTest(unittest.TestCase):
    """GTD_TRUST_PROXY=1 时（跑在反向代理之后的部署形态）才采信 X-Forwarded-*。

    因为信任开关是进程级配置，这里单独起一个服务实例。
    这组用例守的是 Caddy / Nginx 后面那条部署路径：
    限流要按真实客户端 IP 分开，会话 Cookie 要带上 Secure。
    """

    @classmethod
    def setUpClass(cls):
        cls._data_dir = tempfile.mkdtemp(prefix='gtd4teams-trust-')
        cls._port = free_port()
        config = Config(host='127.0.0.1', port=cls._port, data_dir=cls._data_dir)
        config.trust_proxy = True
        cls._server, _ = make_server(config)
        cls._thread = threading.Thread(target=cls._server.serve_forever, daemon=True)
        cls._thread.start()
        # Cookie 是**登录路径**发的，所以断言必须打在 login 的 Set-Cookie 上。
        # （原来打在 register 上，端点下线后一并迁到 login，语义反而更准。）
        _create_user_directly('securecookie@example.com', data_dir=cls._data_dir)
        _create_user_directly('plainhttp@example.com', data_dir=cls._data_dir)

    @classmethod
    def tearDownClass(cls):
        cls._server.shutdown()
        cls._server.server_close()

    def _post(self, path, payload, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self._port, timeout=20)
        sent = {'Content-Type': 'application/json'}
        sent.update(headers or {})
        conn.request('POST', path,
                     body=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                     headers=sent)
        resp = conn.getresponse()
        resp.read()
        status, set_cookie = resp.status, resp.getheader('Set-Cookie')
        conn.close()
        return status, set_cookie

    def test_forwarded_for_is_honoured_when_proxy_trusted(self):
        """限流按 XFF 里的客户端 IP 分开计数：每个 IP 各失败一次，不该触发限流。"""
        security.reset_limits()
        try:
            for i in range(security.MAX_ATTEMPTS + 3):
                status, _ = self._post(
                    '/api/auth/login',
                    {'email': 'trusted@example.com', 'password': 'wrong-pass-1234'},
                    headers={'X-Forwarded-For': f'10.8.{i // 250}.{i % 250}'},
                )
                self.assertNotEqual(status, 429,
                                    'XFF 未被采信，限流仍按 TCP 来源 IP 聚合')
        finally:
            security.reset_limits()

    def test_secure_cookie_behind_https_proxy(self):
        """HTTPS 反代之后，会话 Cookie 必须带 Secure 标志。"""
        status, set_cookie = self._post(
            '/api/auth/login',
            {'email': 'securecookie@example.com', 'password': ADMIN_PASSWORD},
            headers={'X-Forwarded-For': '10.7.0.1', 'X-Forwarded-Proto': 'https'},
        )
        self.assertEqual(status, 200, set_cookie)
        self.assertIn('Secure', set_cookie or '', f'Set-Cookie 缺少 Secure：{set_cookie}')

    def test_no_secure_cookie_on_plain_http(self):
        """明文 HTTP 下不能加 Secure —— 否则浏览器不回传 Cookie，登录会直接失效。"""
        status, set_cookie = self._post(
            '/api/auth/login',
            {'email': 'plainhttp@example.com', 'password': ADMIN_PASSWORD},
            headers={'X-Forwarded-For': '10.6.0.1'},
        )
        self.assertEqual(status, 200, set_cookie)
        self.assertNotIn('Secure', set_cookie or '')


def db_path():
    """主库路径。用函数而不是模块常量：`_DATA_DIR` 是 setUpModule 之后才确定的。"""
    return str(Path(_DATA_DIR) / 'gtd4teams.db')


if __name__ == '__main__':
    unittest.main(verbosity=2)
