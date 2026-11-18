"""零停机发布的前提验证 —— 两个应用进程共用同一个 SQLite 文件。

蓝绿发布的整个方案都压在一个前提上：**新槽位起来之后、旧槽位停掉之前，
两个进程可以安全地读写同一个库文件**。这个前提如果不成立（比如并发写入直接抛
`database is locked`），那么"先起新槽位 → 确认健康 → 再切流"的做法就会在切换窗口里
随机失败 —— 而且只在生产上、只在恰好有并发写入的时候失败，最难查的一类问题。

所以这里不靠推理，直接把两个服务进程跑起来验证：

  1. 两个进程读写同一个库，并发写入零失败，且一边写的内容另一边立刻看得到；
  2. 在 A 上建立的会话在 B 上同样有效 —— 这是"用户不会在切换瞬间被登出"的依据；
  3. /api/health 的 build 字段能区分两个版本（否则发布脚本的新旧核对形同虚设）；
  4. deploy/smoke.sh 对正确的构建放行、对错误的构建拦截；
  5. deploy/probe.sh 报告的失败数为 0。

跑法：
    python tests/test_zerodowntime.py
"""

import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BUILD_A = 'A-20260928-000000'
BUILD_B = 'B-20260928-235959'
COOKIE_NAME = 'gtd_session'

DATA_DIR = ''
PORT_A = 0
PORT_B = 0
PROC_A = None
PROC_B = None
HANDLE_A = None
HANDLE_B = None
LOG_A = os.path.join(tempfile.gettempdir(), 'gtd-zerodowntime-a.log')
LOG_B = os.path.join(tempfile.gettempdir(), 'gtd-zerodowntime-b.log')
SESSION = ''
TEAM_ID = None
ZD_PASSWORD = 'zd-pass-1234'

BASH = shutil.which('bash')
CURL = shutil.which('curl')


# --------------------------------------------------------------------------
# 基础设施
# --------------------------------------------------------------------------

def create_user_directly(email, password, *, is_system_admin=False):
    """直接往库里写一个账号（不经 HTTP）。

    自助注册端点已下线，而本模块的每个服务进程都要能登录 ——
    所以夹具改成"直接落库 + 走真实登录接口拿 Cookie"。
    """
    sys.path.insert(0, str(ROOT))
    from pyapp import db, security

    db.init_db(os.path.join(DATA_DIR, 'gtd4teams.db'))
    conn = db.connect(os.path.join(DATA_DIR, 'gtd4teams.db'))
    try:
        now = db.now_iso()
        conn.execute(
            'INSERT OR IGNORE INTO users (id, email, name, password_hash, created_at, '
            'updated_at, is_system_admin, must_change_password) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, 0)',
            ('u_' + email.split('@')[0], email, email.split('@')[0],
             security.hash_password(password), now, now, 1 if is_system_admin else 0),
        )
        conn.commit()
    finally:
        conn.close()

def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def start_server(port, build, log_path):
    env = dict(os.environ)
    env.update({
        'GTD_BUILD': build,
        'GTD_TRUST_PROXY': '1',
    })
    env.pop('PORT', None)          # 别让环境里的 PORT 覆盖命令行端口
    env.pop('GTD_PORT', None)
    handle = open(log_path, 'w', encoding='utf-8')
    proc = subprocess.Popen(
        [sys.executable, 'server.py', '--port', str(port), '--data-dir', DATA_DIR],
        cwd=str(ROOT), env=env, stdout=handle, stderr=subprocess.STDOUT,
    )
    return proc, handle


def wait_ready(port, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, _, _ = request(port, 'GET', '/api/health')
            if status == 200:
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def request(port, method, path, payload=None, cookie=None, timeout=20):
    """返回 (status, parsed_json_or_text, set_cookie)。"""
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
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
        raw = resp.read().decode('utf-8', 'replace')
        status = resp.status
        set_cookie = resp.getheader('Set-Cookie') or ''
    finally:
        conn.close()
    try:
        data = json.loads(raw)
    except ValueError:
        data = raw
    return status, data, set_cookie


def run_script(script, *args, timeout=180):
    return subprocess.run(
        [BASH, str(ROOT / 'deploy' / script), *args],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding='utf-8', errors='replace', timeout=timeout,
    )


def read_log(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as handle:
            return handle.read()
    except OSError:
        return ''


# --------------------------------------------------------------------------
# 生命周期
# --------------------------------------------------------------------------

def setUpModule():
    global DATA_DIR, PORT_A, PORT_B, PROC_A, PROC_B, HANDLE_A, HANDLE_B
    DATA_DIR = tempfile.mkdtemp(prefix='gtd-zerodowntime-')
    PORT_A = free_port()
    PORT_B = free_port()
    PROC_A, HANDLE_A = start_server(PORT_A, BUILD_A, LOG_A)
    PROC_B, HANDLE_B = start_server(PORT_B, BUILD_B, LOG_B)
    if not (wait_ready(PORT_A) and wait_ready(PORT_B)):
        tearDownModule()
        raise RuntimeError('两个测试用服务未能就绪，检查 %s' % LOG_A)


def tearDownModule():
    for proc in (PROC_A, PROC_B):
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
    for handle in (HANDLE_A, HANDLE_B):
        try:
            if handle is not None:
                handle.close()
        except Exception:
            pass
    if DATA_DIR and os.path.isdir(DATA_DIR):
        shutil.rmtree(DATA_DIR, ignore_errors=True)


# --------------------------------------------------------------------------
# 用例
# --------------------------------------------------------------------------

class ZeroDowntimePremiseTest(unittest.TestCase):

    def test_01_两个进程都能读写同一个库(self):
        """蓝绿要成立，首先得有两个进程共用一个库还能正常干活。

        建号走**直接落库**而不是某个接口：自助注册已随 CR-2026-001 下线
        （这正是"两个槽位共用同一个库"之外的另一条前提 —— 夹具不该依赖业务入口）。
        """
        global SESSION
        create_user_directly('zd@example.com', ZD_PASSWORD)
        status, data, set_cookie = request(
            PORT_A, 'POST', '/api/auth/login',
            {'email': 'zd@example.com', 'password': ZD_PASSWORD})
        self.assertEqual(status, 200, data)
        self.assertIn(COOKIE_NAME, set_cookie, '登录没有下发会话 Cookie')
        SESSION = set_cookie.split(';')[0]

        # 同一个库，从另一个进程读也应当能读出这个账号（登录成功即已证明）
        status, data, _ = request(PORT_B, 'GET', '/api/auth/session', cookie=SESSION)
        self.assertEqual(status, 200, data)
        self.assertEqual(data['user']['email'], 'zd@example.com')

    def test_02_health_的_build_能区分两个版本(self):
        """发布脚本靠这个字段核对"起来的确实是我刚构建的那一版"。"""
        _, health_a, _ = request(PORT_A, 'GET', '/api/health')
        _, health_b, _ = request(PORT_B, 'GET', '/api/health')
        self.assertEqual(health_a.get('build'), BUILD_A, health_a)
        self.assertEqual(health_b.get('build'), BUILD_B, health_b)
        self.assertNotEqual(health_a.get('build'), health_b.get('build'))

    def test_03_会话在另一个进程上同样有效(self):
        """切流不能把用户登出 —— 会话记录在共享的库里，另一个进程必须认得它。"""
        status, data, _ = request(PORT_B, 'GET', '/api/workspace', cookie=SESSION)
        self.assertEqual(status, 200, data)
        self.assertIn('items', data)

    def test_04_并发写入零失败且互相可见(self):
        """两个进程同时对同一个库写入：不应出现 database is locked 之类的失败。"""
        global TEAM_ID
        status, data, _ = request(
            PORT_A, 'POST', '/api/teams/create',
            {'name': '零停机验证组', 'joinCode': 'ZD0001'}, cookie=SESSION)
        self.assertIn(status, (200, 201), data)
        TEAM_ID = data['team']['id']

        total_per_writer = 20
        results = []
        errors = []
        lock = threading.Lock()

        def writer(port, tag):
            for index in range(total_per_writer):
                try:
                    status, data, _ = request(
                        port, 'POST', '/api/rows',
                        {'table': 'work_items', 'op': 'insert', 'payload': {
                            'title': f'{tag}-{index}', 'team_id': TEAM_ID, 'status': 'doing'}},
                        cookie=SESSION)
                    with lock:
                        results.append(status)
                except Exception as exc:                      # noqa: BLE001
                    with lock:
                        errors.append(repr(exc))

        threads = [threading.Thread(target=writer, args=(PORT_A, 'A')),
                   threading.Thread(target=writer, args=(PORT_B, 'B'))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)

        self.assertEqual(errors, [], '并发写入期间出现异常')
        self.assertEqual(len(results), total_per_writer * 2)
        bad = [code for code in results if code not in (200, 201)]
        self.assertEqual(bad, [], '并发写入出现非成功状态：%s' % bad)

        # 两边都应该看到全部写入
        for port, label in ((PORT_A, 'A'), (PORT_B, 'B')):
            status, data, _ = request(port, 'GET', '/api/workspace', cookie=SESSION)
            self.assertEqual(status, 200, data)
            titles = {item['title'] for item in data['items']}
            for tag in ('A', 'B'):
                for index in range(total_per_writer):
                    self.assertIn(f'{tag}-{index}', titles,
                                  f'从进程 {label} 读不到 {tag}-{index}')

    def test_05_服务端日志没有锁冲突或内部错误(self):
        """把"没报错"这件事也断言下来 —— 不然上面的用例只证明了客户端看到的成功。"""
        for label, path in (('A', LOG_A), ('B', LOG_B)):
            text = read_log(path)
            self.assertNotIn('database is locked', text, f'进程 {label} 日志出现锁冲突')
            self.assertNotIn('Traceback', text, f'进程 {label} 日志出现异常栈')
            self.assertNotIn('sqlite3.OperationalError', text, f'进程 {label} 日志出现 SQLite 错误')

    @unittest.skipUnless(BASH and CURL, '需要 bash 与 curl 才能验证部署脚本')
    def test_06_冒烟脚本对正确的构建放行(self):
        result = run_script('smoke.sh', f'http://127.0.0.1:{PORT_A}', BUILD_A)
        self.assertEqual(result.returncode, 0,
                         '冒烟未放行：\n%s\n%s' % (result.stdout, result.stderr))
        self.assertIn('冒烟测试通过', result.stdout)

    @unittest.skipUnless(BASH and CURL, '需要 bash 与 curl 才能验证部署脚本')
    def test_07_冒烟脚本对错误的构建拦截(self):
        """切流前的新旧核对：构建标识对不上就必须拦住，不能"端口通了就放行"。"""
        result = run_script('smoke.sh', f'http://127.0.0.1:{PORT_A}', 'NOT-THIS-BUILD')
        self.assertNotEqual(result.returncode, 0, '构建标识不符却放行了')
        self.assertIn('构建标识与本次发布一致', result.stdout)
        self.assertIn('✗', result.stdout)

    @unittest.skipUnless(BASH and CURL, '需要 bash 与 curl 才能验证部署脚本')
    def test_08_连续性探测报告零失败(self):
        result = run_script('probe.sh', f'http://127.0.0.1:{PORT_A}', '4', '0.1')
        self.assertEqual(result.returncode, 0,
                         '探测报告了失败：\n%s\n%s' % (result.stdout, result.stderr))
        self.assertIn('未断线', result.stdout)

    @unittest.skipUnless(BASH and CURL, '需要 bash 与 curl 才能验证部署脚本')
    def test_09_冒烟脚本能抓到目录穿越(self):
        """curl 默认会规范化 URL 里的 ..，脚本必须显式关掉这个行为，
        否则"目录穿越被拦住"这条检查是假的：请求根本没带着 .. 发出去。"""
        result = run_script('smoke.sh', f'http://127.0.0.1:{PORT_A}', BUILD_A)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn('个受限路径全部 404', result.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
