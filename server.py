#!/usr/bin/env python3
"""GTD4Teams 本地版 —— 启动入口。

    python server.py                    # 默认监听 127.0.0.1:8787，数据落在 ./data/gtd4teams.db
    python server.py --port 9000        # 换端口
    python server.py --host 0.0.0.0     # 允许局域网内其它机器访问
    python server.py --open             # 启动后自动打开浏览器
    python server.py --check            # 自检：建库、打印配置、退出
    python server.py --export back.json # 整库导出（备份）
    python server.py --import back.json # 整库还原
    python server.py --create-admin me@example.com   # 引导系统管理员（创建 + 授权 + 一次性口令）
    python server.py --grant-admin me@example.com    # 只把已有账号授予系统管理员（不建号）
    python server.py --set-password me@example.com   # 重设某个账号的密码（忘密码时用）

不依赖任何第三方包，也不需要 Node.js / npm / 构建工具。
"""

import argparse
import json
import signal
import sys
import threading
import webbrowser
from pathlib import Path

if __package__ in (None, ''):                    # 支持 `python server.py` 直接运行
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from pyapp import APP_VERSION, db, security  # noqa: E402
from pyapp.config import Config              # noqa: E402
from pyapp.httpd import (Application, make_integration_servers,  # noqa: E402
                         make_server)
from pyapp import store                       # noqa: E402


def build_parser():
    parser = argparse.ArgumentParser(
        prog='server.py',
        description='GTD4Teams 本地版：自带账号体系与数据存储的团队 GTD 工作台',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('--host', default=None, help='监听地址（默认 127.0.0.1，仅本机可访问）')
    parser.add_argument('--port', type=int, default=None, help='监听端口（默认 8787）')
    parser.add_argument('--data-dir', default=None, help='数据目录（默认 ./data）')
    parser.add_argument('--open', action='store_true', help='启动后自动打开浏览器')
    parser.add_argument('--check', action='store_true', help='自检后退出')
    parser.add_argument('--export', metavar='FILE', help='把整库导出为 JSON 备份后退出')
    parser.add_argument('--import', dest='import_file', metavar='FILE',
                        help='从 JSON 备份还原整库后退出')
    parser.add_argument('--create-admin', metavar='EMAIL',
                        help='创建系统管理员（不存在则建号并打印一次性初始口令；'
                             '已存在则只授权，不改口令）')
    parser.add_argument('--grant-admin', metavar='EMAIL',
                        help='把已存在的账号授予系统管理员（账号不存在则报错退出，不建号）')
    parser.add_argument('--set-password', metavar='EMAIL',
                        help='重设指定邮箱账号的密码（交互式输入，忘密码时自救）')
    # 集成域：默认关闭；关闭时**不创建 socket**（只绑 127.0.0.1，无配置项可改）
    parser.add_argument('--api-enabled', action='store_true', default=None,
                        help='启用本地 REST API（默认关闭；等价于 GTD_API_ENABLED=1）')
    parser.add_argument('--mcp-enabled', action='store_true', default=None,
                        help='启用内置 MCP（默认关闭；等价于 GTD_MCP_ENABLED=1）')
    parser.add_argument('--api-port', type=int, default=None,
                        help='本地 API 端口（默认 8790，仅 127.0.0.1）')
    parser.add_argument('--mcp-port', type=int, default=None,
                        help='MCP 端口（默认 8722，仅 127.0.0.1）')
    return parser


def config_from_args(args):
    return Config(host=args.host, port=args.port, data_dir=args.data_dir,
                  api_enabled=args.api_enabled, mcp_enabled=args.mcp_enabled,
                  api_port=args.api_port, mcp_port=args.mcp_port)


def _warn_ignored_signup_env(cfg):
    """老 .env 里还留着已下线的注册开关时，明确说一句"它已经无效"。

    静默失效是最难发现的配置故障：运维看到 `.env` 里写着 `GTD_ALLOW_SIGNUP=1`，
    会一直以为"注册只是暂时没开"，而实际上端点已经不存在了。
    """
    if not getattr(cfg, 'ignored_signup_env', ()):
        return
    print('  ! 提示：' + '、'.join(cfg.ignored_signup_env)
          + ' 已随自助注册下线而失效（端点已移除），可以从 .env 里删掉。', flush=True)


def banner(cfg, host_for_url):
    lines = [
        '',
        f'  GTD4Teams v{cfg.version}',
        '  ─────────────────────────────────────────────',
        f'  访问地址   http://{host_for_url}:{cfg.port}/',
        f'  实际监听   {cfg.host}:{cfg.port}',
        f'  数据文件   {cfg.db_path}',
        f'  反向代理   {"信任 X-Forwarded-*（限流按真实客户端 IP）" if cfg.trust_proxy else "不信任（限流按 TCP 来源 IP）"}',
        f'  AI 通道    {"已配置（" + cfg.llm_model + "）" if cfg.llm_configured else "未配置，可在界面里填自己的密钥"}',
        # 诚实反馈：关闭就显示"未启用"，绝不显示假的"运行中"（§16.9 硬要求）。
        f'  本地 API   {"已启用 127.0.0.1:" + str(cfg.api_port) if cfg.api_enabled else "未启用"}',
        f'  内置 MCP   {"已启用 127.0.0.1:" + str(cfg.mcp_port) + "/mcp" if cfg.mcp_enabled else "未启用"}',
        '  ─────────────────────────────────────────────',
        '  按 Ctrl+C 停止服务。',
        '  首次使用请执行：python server.py --create-admin <邮箱>',
        '  （自助注册已下线：账号只能由系统管理员或团队管理员创建）',
        '',
    ]
    print('\n'.join(lines), flush=True)
    _warn_ignored_signup_env(cfg)


def cmd_check(cfg):
    db.init_db(cfg.db_path)
    conn = db.connect(cfg.db_path)
    try:
        tables = [r['name'] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()]
        users = conn.execute('SELECT COUNT(*) AS n FROM users').fetchone()['n']
        teams = conn.execute('SELECT COUNT(*) AS n FROM teams').fetchone()['n']
        items = conn.execute('SELECT COUNT(*) AS n FROM work_items').fetchone()['n']
        # 引导做了没有 / 还有谁没改初始口令 —— 这两个数字是唯一的自检依据。
        # 只报计数不报邮箱：--check 的输出常被贴进工单或聊天窗口。
        system_admins = conn.execute(
            'SELECT COUNT(*) AS n FROM users WHERE is_system_admin = 1').fetchone()['n']
        pending_changes = conn.execute(
            'SELECT COUNT(*) AS n FROM users WHERE must_change_password = 1').fetchone()['n']
    finally:
        conn.close()
    print(json.dumps({
        'ok': True,
        'version': cfg.version,
        'python': sys.version.split()[0],
        'dbPath': str(cfg.db_path),
        'tables': tables,
        'counts': {'users': users, 'teams': teams, 'workItems': items,
                   'systemAdmins': system_admins,
                   'pendingPasswordChanges': pending_changes},
        'config': cfg.describe(),
    }, ensure_ascii=False, indent=2))
    _warn_ignored_signup_env(cfg)


def cmd_export(cfg, target):
    db.init_db(cfg.db_path)
    conn = db.connect(cfg.db_path)
    try:
        payload = store.export_all(conn)
    finally:
        conn.close()
    with open(target, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    counts = {k: len(v) for k, v in payload['tables'].items() if v}
    print(f'已导出到 {target}')
    print('  含数据的表：' + ('，'.join(f'{k}={v}' for k, v in counts.items()) or '（空库）'))
    print('  注意：文件包含账号口令散列，属于敏感文件，请勿公开或提交到版本库。')


def cmd_import(cfg, source):
    if not Path(source).is_file():
        raise SystemExit(f'找不到备份文件：{source}')
    db.init_db(cfg.db_path)
    conn = db.connect(cfg.db_path)
    try:
        counts = store.import_all(conn, store.load_backup_file(source))
    finally:
        conn.close()
    print(f'已从 {source} 还原')
    print('  写入的表：' + ('，'.join(f'{k}={v}' for k, v in counts.items()) or '（无）'))


# CLI 侧的身份：不是从请求体来的，而是"能在服务器上执行命令的人"这一事实本身。
# store.create_account 里还有一道 `via == 'cli'` 的双重保险（HTTP 路径恒传 'http'）。
CLI_ACTOR = {'uid': 'cli', 'is_system_admin': True, 'admin_team_ids': (), 'via': 'cli'}


def _cli_error(message):
    print(f'错误：{message}', file=sys.stderr, flush=True)
    return 2


def cmd_create_admin(cfg, email):
    """引导系统管理员：不存在则建号 + 授权 + 随机口令；已存在则**只授权不动口令**。

    幂等性不是可选项：这条命令会被写进部署脚本与运维手册，脚本可能被重复执行。
    若第二次执行就重置了口令，运维会在"我以为它只是检查一下"的时候
    把刚发给用户的口令改掉。
    """
    email = (email or '').strip()
    problem = security.validate_email(email)
    if problem:
        return _cli_error(f'{problem}：{email!r}')

    db.init_db(cfg.db_path)
    conn = db.connect(cfg.db_path)
    try:
        existing = conn.execute(
            'SELECT id, is_system_admin FROM users WHERE lower(email) = lower(?)', (email,)
        ).fetchone()
        if existing is None:
            password = security.generate_temp_password()
            store.create_account(
                conn, CLI_ACTOR, email=email,
                password_hash=security.hash_password(password),
                is_system_admin=True, must_change_password=True,
            )
            print(f'已创建系统管理员：{email}')
            print(f'  初始密码（只显示这一次，请立即保存）：{password}')
            print('  首次登录后必须立即修改密码；该账号目前不属于任何团队。')
            return 0
        if existing['is_system_admin']:
            print(f'该账号已是系统管理员：{email}（幂等，无改动）')
            return 0
        store.grant_system_admin(conn, email)
        print(f'账号已存在：{email} → 已授予系统管理员')
        print(f'  口令未改动。如需重置：python server.py --set-password {email}')
        return 0
    finally:
        conn.close()


def cmd_grant_admin(cfg, email):
    """只授权，**不建号**。

    若这里顺手建号，一次邮箱打错（`admn@` vs `admin@`）就会静默创建一个
    带随机口令的管理员账号并打印口令 —— 而真正的那个人依然进不来。
    把"建号"与"授权"分开，这个错误才会表现为"命令报错"，
    而不是"线上多了一个管理员"。
    """
    email = (email or '').strip()
    problem = security.validate_email(email)
    if problem:
        return _cli_error(f'{problem}：{email!r}')

    db.init_db(cfg.db_path)
    conn = db.connect(cfg.db_path)
    try:
        result = store.grant_system_admin(conn, email)
    finally:
        conn.close()
    if result is None:
        return _cli_error(f'账号 {email} 不存在。请先用 '
                          f'python server.py --create-admin {email} 创建')
    print(f'已授予系统管理员：{email}' if result['changed']
          else f'该账号已是系统管理员：{email}（无改动）')
    return 0


def cmd_set_password(cfg, email):
    """重设密码：既是"忘密码自救"，也是"关闭注册后如何开账号"的答案。

    收敛到 store.reset_password()：本项目只允许**一处**建号逻辑，
    否则散列参数、`must_change_password` 置位、踢会话这三件事会各写一遍并漂移。
    """
    import getpass

    email = (email or '').strip()
    problem = security.validate_email(email)
    if problem:
        return _cli_error(f'{problem}：{email!r}')

    db.init_db(cfg.db_path)
    conn = db.connect(cfg.db_path)
    try:
        row = conn.execute('SELECT id FROM users WHERE lower(email) = lower(?)',
                           (email,)).fetchone()
        create = row is None
        if create:
            print(f'账号 {email} 不存在，将创建一个新账号（**不是**系统管理员）。')

        password = getpass.getpass('请输入新密码：')
        confirm = getpass.getpass('请再输入一次：')
        if password != confirm:
            return _cli_error('两次输入的密码不一致')
        problem = security.validate_password(password)
        if problem:
            return _cli_error(problem)

        # 口令由他人设定 → 置 must_change_password=1（用户应当自己再改一次）；
        # 重置分支同时踢掉该账号的全部会话（既有行为，保持不变）。
        store.reset_password(conn, email, security.hash_password(password),
                             create_if_missing=True, must_change_password=True)
    finally:
        conn.close()
    if create:
        print('账号已创建。首次登录后必须立即修改密码。')
    else:
        print('已更新密码（首次登录需修改密码），该账号的其它登录会话已失效。')
    return 0


def _install_signal_handlers(servers):
    """把 SIGTERM / SIGINT 接到优雅关闭上（主监听器 + 已启用的集成监听器）。

    容器编排（docker stop、compose down、k8s）下发的都是 SIGTERM，而 Python 的默认
    行为是**立刻终止进程** —— 正在处理的请求被拦腰截断，SQLite 的写事务与 WAL
    也来不及收尾。接上 server.shutdown() 之后，serve_forever 会正常返回，
    随后执行 server_close()，连接与数据文件都能干净收场。

    注意 shutdown() 必须由**另一个线程**调用：它在等 serve_forever 的循环退出，
    而那个循环正阻塞在主线程里，直接在信号处理函数里调用会死锁。
    """
    if not isinstance(servers, (list, tuple)):
        servers = [servers]

    def handle(signum, _frame):
        print(f'\n收到信号 {signum}，正在优雅停止…', flush=True)
        for instance in servers:
            threading.Thread(target=instance.shutdown, daemon=True).start()

    for name in ('SIGTERM', 'SIGINT'):
        sig = getattr(signal, name, None)
        if sig is None:              # 平台没有这个信号
            continue
        try:
            signal.signal(sig, handle)
        except (ValueError, OSError):
            pass                     # 不在主线程时无法注册，忽略即可


def main(argv=None):
    args = build_parser().parse_args(argv)
    cfg = config_from_args(args)

    if args.check:
        cmd_check(cfg)
        return 0
    if args.export:
        cmd_export(cfg, args.export)
        return 0
    if args.import_file:
        cmd_import(cfg, args.import_file)
        return 0
    if args.create_admin:
        return cmd_create_admin(cfg, args.create_admin)
    if args.grant_admin:
        return cmd_grant_admin(cfg, args.grant_admin)
    if args.set_password:
        return cmd_set_password(cfg, args.set_password)

    server, app = make_server(cfg)
    # 默认关闭：这两个函数在读开关为 False 时**连 bind 都不做**。
    integration_servers = make_integration_servers(cfg, app)
    _install_signal_handlers([server] + integration_servers)
    host_for_url = '127.0.0.1' if cfg.host in ('0.0.0.0', '::') else cfg.host
    banner(cfg, host_for_url)

    if args.open:
        threading.Timer(0.4, lambda: webbrowser.open(f'http://{host_for_url}:{cfg.port}/')).start()

    for instance in integration_servers:
        threading.Thread(target=instance.serve_forever, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n正在停止服务…', flush=True)
    finally:
        for instance in integration_servers:
            instance.shutdown()
            instance.server_close()
        server.server_close()
    print('已停止。', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
