"""SQLite 存储层：表结构、字段规格、JSON 编解码与类型归一化。

三条设计原则
------------
1. **字段名与云版本（PostgreSQL）完全一致**。
   `js/views.js` 与 `js/app.js` 是逐字段消费数据的，任何改名都会让界面直接空掉。
   因此这里的列名逐字照抄原库（见 .workbuddy/memory 里的表结构取证），
   只把 PG 专有类型按下面的规则落成 SQLite 类型。

2. **类型映射**：
   bigint → INTEGER PRIMARY KEY AUTOINCREMENT / INTEGER
   text   → TEXT          boolean → INTEGER(0/1)
   numeric→ REAL          date → TEXT('YYYY-MM-DD')
   timestamp with time zone → TEXT(ISO8601)
   jsonb  → TEXT(JSON)    ARRAY → TEXT(JSON 数组)
   PG 里 `owner_id` 的默认值是 `auth.uid()` —— SQLite 没有这个概念，
   归属一律由 store 层显式注入（绝不由客户端提供，见 policy.py）。

3. **字段规格（COLUMNS）是业务表的唯一权威**：允许写入哪些字段、按什么类型归一化，
   全看它。tests/test_api.py 里有一条断言：COLUMNS 的键集合必须与
   PRAGMA table_info 的实际列集合严格相等 —— 这样手写 DDL 与规格表不可能悄悄漂移。
   **系统表（users / sessions / settings）刻意不在 COLUMNS 里**：COLUMNS 同时是
   通用 CRUD 的可写白名单，把 users 登记进去等于开放账号表写入（见 USER_SCHEMA 上方）。
"""

import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------
# 表结构
# --------------------------------------------------------------------------

# 账号表：本地自持身份源，取代云平台托管身份
#
# ⚠️ 这张表的两列（is_system_admin / must_change_password）**绝不进 COLUMNS**。
# COLUMNS 不只是"字段规格表"，它同时是通用 CRUD 的可写白名单
# （下面 WRITABLE_TABLES = tuple(COLUMNS.keys())）。把 users 登记进去，
# 等价于让 POST /api/rows 能改账号表，`password_hash` 与 `is_system_admin`
# 会一起进入可写字段集 —— 那是一条"任何登录用户都能给自己提权"的通道。
# 防漂移靠 SYSTEM_COLUMN_SPEC + tests/test_api.py 里的护栏用例，
# **不要**为了"给这两列建规格"而把 users 加进 COLUMNS（见 05 §13.2.3）。
USER_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id                   TEXT PRIMARY KEY,
  email                TEXT NOT NULL,
  name                 TEXT NOT NULL DEFAULT '',
  password_hash        TEXT NOT NULL,
  created_at           TEXT NOT NULL,
  updated_at           TEXT NOT NULL,
  is_system_admin      INTEGER NOT NULL DEFAULT 0,
  must_change_password INTEGER NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS users_email_uniq ON users (lower(email));
"""

# 会话表：只存令牌的散列，库被读走也无法直接冒用登录态
SESSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  token_hash   TEXT PRIMARY KEY,
  user_id      TEXT NOT NULL,
  created_at   TEXT NOT NULL,
  expires_at   TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  user_agent   TEXT
);
CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions (user_id);
"""

# 键值配置表：LLM 凭据等运行期设置，避免让用户去改环境变量
SETTINGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""

# 版本账：一行一条已应用的迁移。
#
# 归属判定（05 §13.1.1 逐项给过理由）：
#   · 必须进 SCHEMA_SQL：新建库靠 executescript 一次性建表，若不在这里，
#     新库就没有这张表 → schema_present() 恒 False → 每次启动都重跑全文 DDL；
#   · 必须进 EXPECTED_TABLES：schema_present() 用子集判断，且有一条测试要求
#     "建出来了但没登记"必须为零；
#   · 绝不进 COLUMNS / WRITABLE_TABLES：否则客户端可改写迁移版本号；
#   · 不进 BACKUP_TABLES：版本号必须由"库的实际结构"重新推导，不能由备份携带。
SCHEMA_VERSION_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
  version     INTEGER PRIMARY KEY,
  applied_at  TEXT NOT NULL,
  description TEXT
);
"""

# 业务表
DATA_SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_id     TEXT NOT NULL,
  owner_name   TEXT,
  name         TEXT NOT NULL DEFAULT '',
  description  TEXT,
  mission      TEXT,
  join_code    TEXT,
  board_config TEXT,
  sla_config   TEXT,
  archived     INTEGER NOT NULL DEFAULT 0,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
-- 邀请码大小写不敏感唯一：防止两个团队共用一个码（云版本同名索引）
CREATE UNIQUE INDEX IF NOT EXISTS teams_join_code_uniq
  ON teams (upper(join_code)) WHERE join_code IS NOT NULL AND join_code <> '';
CREATE INDEX IF NOT EXISTS teams_owner_idx ON teams (owner_id);

CREATE TABLE IF NOT EXISTS team_members (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  team_id      INTEGER NOT NULL,
  user_id      TEXT NOT NULL,
  email        TEXT,
  display_name TEXT,
  role         TEXT NOT NULL DEFAULT 'member',
  title        TEXT,
  capacity     REAL NOT NULL DEFAULT 6,
  status       TEXT NOT NULL DEFAULT 'active',
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS team_members_team_user_uniq ON team_members (team_id, user_id);
CREATE INDEX IF NOT EXISTS team_members_user_idx ON team_members (user_id);

CREATE TABLE IF NOT EXISTS work_items (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  team_id        INTEGER,
  owner_id       TEXT NOT NULL,
  title          TEXT NOT NULL DEFAULT '',
  notes          TEXT,
  item_type      TEXT NOT NULL DEFAULT 'routine',
  status         TEXT NOT NULL DEFAULT 'intake',
  priority       TEXT NOT NULL DEFAULT 'p3',
  swimlane       TEXT NOT NULL DEFAULT 'planned',
  assignee_id    TEXT,
  assignee_name  TEXT,
  reviewer_id    TEXT,
  project_id     INTEGER,
  area_id        INTEGER,
  context_id     INTEGER,
  source         TEXT,
  impact         TEXT,
  urgency        TEXT,
  sla_due_at     TEXT,
  responded_at   TEXT,
  start_date     TEXT,
  due_date       TEXT,
  recurrence     TEXT,
  est_hours      REAL,
  actual_hours   REAL,
  progress       INTEGER NOT NULL DEFAULT 0,
  last_update    TEXT,
  blocked_reason TEXT,
  waiting_for    TEXT,
  flagged        INTEGER NOT NULL DEFAULT 0,
  is_next        INTEGER NOT NULL DEFAULT 0,
  kanban_order   REAL NOT NULL DEFAULT 0,
  tags           TEXT,
  completed_at   TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  focus_date     TEXT,
  gtd_note       TEXT,
  reported_by    TEXT,
  -- ↓ 集成域（§16.10 v3/v4）：刻意放在**最后**，让全新库与"旧库迁移加列"的
  --   列序一致（_schema_fingerprint 会逐列比对顺序）。
  version        INTEGER NOT NULL DEFAULT 1,
  archived_at    TEXT
);
CREATE INDEX IF NOT EXISTS work_items_team_idx ON work_items (team_id);
CREATE INDEX IF NOT EXISTS work_items_owner_idx ON work_items (owner_id);
CREATE INDEX IF NOT EXISTS work_items_status_idx ON work_items (status);

CREATE TABLE IF NOT EXISTS projects (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  team_id     INTEGER NOT NULL,
  owner_id    TEXT NOT NULL,
  name        TEXT NOT NULL DEFAULT '',
  description TEXT,
  status      TEXT NOT NULL DEFAULT 'active',
  kind        TEXT NOT NULL DEFAULT 'build',
  lead_id     TEXT,
  lead_name   TEXT,
  area_id     INTEGER,
  due_date    TEXT,
  color       TEXT DEFAULT '#14867a',
  progress    INTEGER NOT NULL DEFAULT 0,
  sort_order  INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS projects_team_idx ON projects (team_id);

CREATE TABLE IF NOT EXISTS areas (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  team_id       INTEGER NOT NULL,
  owner_id      TEXT NOT NULL,
  name          TEXT NOT NULL DEFAULT '',
  description   TEXT,
  criticality   TEXT DEFAULT 'normal',
  steward_id    TEXT,
  steward_name  TEXT,
  archived      INTEGER NOT NULL DEFAULT 0,
  sort_order    INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS areas_team_idx ON areas (team_id);

CREATE TABLE IF NOT EXISTS contexts (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  team_id    INTEGER,
  owner_id   TEXT NOT NULL,
  name       TEXT NOT NULL DEFAULT '',
  kind       TEXT DEFAULT 'mode',
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS contexts_owner_idx ON contexts (owner_id);

CREATE TABLE IF NOT EXISTS resources (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  team_id        INTEGER NOT NULL,
  owner_id       TEXT NOT NULL,
  kind           TEXT NOT NULL DEFAULT 'doc',
  title          TEXT NOT NULL DEFAULT '',
  category       TEXT,
  url            TEXT,
  description    TEXT,
  tags           TEXT,
  custodian_id   TEXT,
  custodian_name TEXT,
  criticality    TEXT DEFAULT 'normal',
  review_due     TEXT,
  meta           TEXT,
  pinned         INTEGER NOT NULL DEFAULT 0,
  archived       INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS resources_team_idx ON resources (team_id);

CREATE TABLE IF NOT EXISTS progress_logs (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  team_id     INTEGER,
  owner_id    TEXT NOT NULL,
  author_name TEXT,
  item_id     INTEGER,
  item_title  TEXT,
  kind        TEXT NOT NULL DEFAULT 'progress',
  progress    INTEGER,
  note        TEXT,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS progress_logs_team_idx ON progress_logs (team_id);
CREATE INDEX IF NOT EXISTS progress_logs_item_idx ON progress_logs (item_id);
CREATE INDEX IF NOT EXISTS progress_logs_created_idx ON progress_logs (created_at);

CREATE TABLE IF NOT EXISTS reviews (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  team_id      INTEGER,
  owner_id     TEXT NOT NULL,
  kind         TEXT NOT NULL DEFAULT 'weekly',
  period_key   TEXT NOT NULL DEFAULT '',
  notes        TEXT,
  stats        TEXT,
  completed_at TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT NOT NULL
);
-- 同一 (账号, 团队, 类型, 周期) 只保留一条：云版本靠表达式唯一索引 + 先查后写实现，
-- 这里用同样的表达式唯一索引在库层面兜底并发。
CREATE UNIQUE INDEX IF NOT EXISTS reviews_uniq_idx
  ON reviews (owner_id, COALESCE(team_id, 0), kind, period_key);

CREATE TABLE IF NOT EXISTS on_call (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  team_id        INTEGER NOT NULL,
  owner_id       TEXT NOT NULL,
  shift_date     TEXT NOT NULL,
  slot           TEXT NOT NULL DEFAULT 'day',
  primary_id     TEXT,
  primary_name   TEXT,
  backup_id      TEXT,
  backup_name    TEXT,
  notes          TEXT,
  handover_notes TEXT,
  status         TEXT NOT NULL DEFAULT 'planned',
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS on_call_team_idx ON on_call (team_id);

CREATE TABLE IF NOT EXISTS app_logs (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  owner_id   TEXT,
  level      TEXT,
  scope      TEXT,
  event      TEXT,
  message    TEXT,
  detail     TEXT,
  page_url   TEXT,
  user_agent TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS app_logs_created_idx ON app_logs (created_at);
"""

# 集成域的两张系统表（§16.3.2 / §16.6.3）。
#
# 四条纪律（漏一条就会在蓝绿发布时露出真实缺口）：
#   1. **不进 COLUMNS** → 自动不进 WRITABLE_TABLES → `/api/rows` 触达不到它们；
#   2. **必须进 EXPECTED_TABLES**（见下方）—— schema_present() 是**子集**判断，
#      漏登记会让旧库永远停在这张表"不存在"的状态，迁移也补不回来；
#   3. token_hash / (user_id, idem_key) 的唯一索引是"查令牌 / 判幂等"的唯一入口；
#   4. **不进 store.BACKUP_TABLES** —— 备份一旦携带令牌散列，等于把凭据随备份分发。
# 每条语句单独成项：迁移运行在 `BEGIN IMMEDIATE` 事务里，而 Python sqlite3 的
# `executescript()` 会**隐式提交**之前的事务 —— 用它建表会让"DDL 与版本号
# 同一事务"这条纪律失效（失败时列建了、账没记）。迁移里必须用 conn.execute()
# 逐条执行，因此这里保留语句列表；SCHEMA_SQL 只需拼接后交给 executescript。
API_TOKEN_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS api_tokens (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  token_hash   TEXT    NOT NULL UNIQUE,
  user_id      TEXT    NOT NULL,
  label        TEXT    NOT NULL DEFAULT '',
  scopes       TEXT    NOT NULL DEFAULT '["read"]',
  created_at   TEXT    NOT NULL,
  created_by   TEXT,
  last_used_at TEXT,
  expires_at   TEXT,
  revoked_at   TEXT,
  revoked_by   TEXT
)""",
    'CREATE INDEX IF NOT EXISTS api_tokens_user_idx ON api_tokens (user_id)',
)

API_IDEMPOTENCY_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS api_idempotency (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id      TEXT    NOT NULL,
  idem_key     TEXT    NOT NULL,
  method       TEXT    NOT NULL,
  path         TEXT    NOT NULL,
  request_hash TEXT    NOT NULL,
  status       INTEGER NOT NULL,
  response     TEXT    NOT NULL,
  created_at   TEXT    NOT NULL
)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS api_idempotency_uniq
  ON api_idempotency (user_id, idem_key)""",
)

INTEGRATION_SCHEMA = '\n'.join(
    statement + ';' for statement in API_TOKEN_SCHEMA + API_IDEMPOTENCY_SCHEMA)

SCHEMA_SQL = (USER_SCHEMA + SESSION_SCHEMA + SETTINGS_SCHEMA
              + SCHEMA_VERSION_SQL + DATA_SCHEMA + INTEGRATION_SCHEMA)

# --------------------------------------------------------------------------
# 字段规格：允许写入的字段 + 类型归一化（键集合必须等于真实列集合）
# --------------------------------------------------------------------------
# kind 取值：
#   pk      自增主键，不接受客户端写入
#   owner   归属列，写库时由服务端强制注入当前账号（客户端提供的值一律丢弃）
#   int     整数           real  浮点        bool  0/1
#   text    文本           date  'YYYY-MM-DD'  ts    ISO8601 文本
#   json    对象（存 JSON 文本）   jsonarr 数组（存 JSON 文本）
#   stamp   时间戳，创建/更新时由服务端维护
#   lock    服务端独占列：decode_row 原样透传，split_payload 一律丢弃（§16.6.2）

COLUMNS = {
    'teams': {
        'id': 'pk', 'owner_id': 'owner', 'owner_name': 'text', 'name': 'text',
        'description': 'text', 'mission': 'text', 'join_code': 'text',
        'board_config': 'json', 'sla_config': 'json', 'archived': 'bool',
        'created_at': 'stamp', 'updated_at': 'stamp',
    },
    'team_members': {
        'id': 'pk', 'team_id': 'int', 'user_id': 'text', 'email': 'text',
        'display_name': 'text', 'role': 'text', 'title': 'text',
        'capacity': 'real', 'status': 'text',
        'created_at': 'stamp', 'updated_at': 'stamp',
    },
    'work_items': {
        'id': 'pk', 'team_id': 'int', 'owner_id': 'owner', 'title': 'text',
        'notes': 'text', 'item_type': 'text', 'status': 'text', 'priority': 'text',
        'swimlane': 'text', 'assignee_id': 'text', 'assignee_name': 'text',
        'reviewer_id': 'text', 'project_id': 'int', 'area_id': 'int',
        'context_id': 'int', 'source': 'text', 'impact': 'text', 'urgency': 'text',
        'sla_due_at': 'ts', 'responded_at': 'ts', 'start_date': 'date',
        'due_date': 'date', 'recurrence': 'json', 'est_hours': 'real',
        'actual_hours': 'real', 'progress': 'int', 'last_update': 'text',
        'blocked_reason': 'text', 'waiting_for': 'text', 'flagged': 'bool',
        'is_next': 'bool', 'kanban_order': 'real', 'tags': 'jsonarr',
        'completed_at': 'ts', 'created_at': 'stamp', 'updated_at': 'stamp',
        'focus_date': 'date', 'gtd_note': 'text', 'reported_by': 'text',
        # 'lock' = 可读但客户端不可写；乐观锁的版本号必须只有服务端能改，
        # 归档时间戳是"有权限语义的操作"，不能经通用 CRUD 触达（§16.6.2）。
        'version': 'lock', 'archived_at': 'lock',
    },
    'projects': {
        'id': 'pk', 'team_id': 'int', 'owner_id': 'owner', 'name': 'text',
        'description': 'text', 'status': 'text', 'kind': 'text', 'lead_id': 'text',
        'lead_name': 'text', 'area_id': 'int', 'due_date': 'date', 'color': 'text',
        'progress': 'int', 'sort_order': 'int',
        'created_at': 'stamp', 'updated_at': 'stamp',
    },
    'areas': {
        'id': 'pk', 'team_id': 'int', 'owner_id': 'owner', 'name': 'text',
        'description': 'text', 'criticality': 'text', 'steward_id': 'text',
        'steward_name': 'text', 'archived': 'bool', 'sort_order': 'int',
        'created_at': 'stamp', 'updated_at': 'stamp',
    },
    'contexts': {
        'id': 'pk', 'team_id': 'int', 'owner_id': 'owner', 'name': 'text',
        'kind': 'text', 'sort_order': 'int',
        'created_at': 'stamp', 'updated_at': 'stamp',
    },
    'resources': {
        'id': 'pk', 'team_id': 'int', 'owner_id': 'owner', 'kind': 'text',
        'title': 'text', 'category': 'text', 'url': 'text', 'description': 'text',
        'tags': 'jsonarr', 'custodian_id': 'text', 'custodian_name': 'text',
        'criticality': 'text', 'review_due': 'date', 'meta': 'json',
        'pinned': 'bool', 'archived': 'bool',
        'created_at': 'stamp', 'updated_at': 'stamp',
    },
    'progress_logs': {
        'id': 'pk', 'team_id': 'int', 'owner_id': 'owner', 'author_name': 'text',
        'item_id': 'int', 'item_title': 'text', 'kind': 'text', 'progress': 'int',
        'note': 'text', 'created_at': 'stamp',
    },
    'reviews': {
        'id': 'pk', 'team_id': 'int', 'owner_id': 'owner', 'kind': 'text',
        'period_key': 'text', 'notes': 'text', 'stats': 'json',
        'completed_at': 'ts', 'created_at': 'stamp', 'updated_at': 'stamp',
    },
    'on_call': {
        'id': 'pk', 'team_id': 'int', 'owner_id': 'owner', 'shift_date': 'date',
        'slot': 'text', 'primary_id': 'text', 'primary_name': 'text',
        'backup_id': 'text', 'backup_name': 'text', 'notes': 'text',
        'handover_notes': 'text', 'status': 'text',
        'created_at': 'stamp', 'updated_at': 'stamp',
    },
    'app_logs': {
        'id': 'pk', 'owner_id': 'owner', 'level': 'text', 'scope': 'text',
        'event': 'text', 'message': 'text', 'detail': 'json', 'page_url': 'text',
        'user_agent': 'text', 'created_at': 'stamp',
    },
}

# 客户端可以走通用 CRUD 的业务表（users/sessions/settings 永远不开放）
WRITABLE_TABLES = tuple(COLUMNS.keys())

# 系统表（不开放 CRUD）的列清单：**只给测试断言用**，不参与任何写入路径。
#
# ⚠️ 禁令：严禁把这张表的内容合并进 COLUMNS（见 USER_SCHEMA 上方的说明与 05 §13.2.3）。
# 它的存在只是为了给 users 这种"没有 COLUMNS 规格"的敏感表提供防漂移断言 ——
# 否则手写 DDL 加了一列，没有任何测试会发现。
SYSTEM_COLUMN_SPEC = {
    'users': {
        'id': 'text', 'email': 'text', 'name': 'text', 'password_hash': 'text',
        'created_at': 'ts', 'updated_at': 'ts',
        'is_system_admin': 'bool', 'must_change_password': 'bool',
    },
}

# 建库之后应当存在的全部表。用途见 schema_present()：
# 结构已经建好的库，启动时不该再重跑一遍 DDL。
EXPECTED_TABLES = ('users', 'sessions', 'settings', 'schema_version',
                   'api_tokens', 'api_idempotency') + WRITABLE_TABLES

# 列出整表时按 id 升序（与云版本 selectAll 的 order('id') 一致）
DEFAULT_ORDER = {'progress_logs': ('created_at', 'DESC')}
LOG_LIMIT = 400

JSON_KINDS = ('json', 'jsonarr')


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------

def now_iso() -> str:
    """当前时间（UTC，秒精度，带 Z 后缀）。

    前端用 `new Date(iso)` 解析，必须带时区标记，否则会被当成浏览器本地时间。
    """
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_iso(text):
    """把 now_iso() 产出的时间串解析回 datetime（UTC）。解析不了返回 None。"""
    if not text:
        return None
    try:
        return datetime.strptime(str(text), '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def seconds_since(text, now=None) -> float:
    """`text` 时刻距现在多少秒。

    解析不出来时返回 inf（当作"很久没更新过"）—— 这个方向是安全的一侧：
    宁可多写一次，也不要因为一个格式不对的时间串而**再也不刷新**。
    """
    moment = parse_iso(text)
    if moment is None:
        return float('inf')
    return ((now or datetime.now(timezone.utc)) - moment).total_seconds()


def connect(db_path) -> sqlite3.Connection:
    """开一个连接。

    每次请求一个连接（连接本身很廉价），这样多线程处理请求时不必共享游标；
    WAL 让"读"不会被"写"阻塞。
    """
    conn = sqlite3.connect(str(db_path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode = WAL')
    conn.execute('PRAGMA synchronous = NORMAL')
    conn.execute('PRAGMA busy_timeout = 15000')
    return conn


class TrackedConnection:
    """请求级连接包装：记录"本次请求有没有尝试提交过"。

    为什么需要它
    ------------
    httpd._dispatch 遇到写锁争用时会**整体重跑 handler**。这件事的安全前提是
    "抛异常时事务还没提交，重跑不会留下痕迹"。但真实代码里存在
    "先 commit、后做事"的路径（提交之后还要取回结果 / 写会话 / 更新内存配置），
    一旦提交之后的某一步抛了写锁错误，重试就会把已经落库的写再做一遍 ——
    实测：客户端看到 1 行、库里 2 行；注册建号成功却返回 409。

    所以判定改成"只要尝试过提交，就不可重放"。`commit_attempted` 刻意在
    **调用真实 commit 之前**置位：事后无法区分"提交成功但随即抛异常"与
    "提交失败"，那就一律按最保守的一侧处理（宁可在极小概率下返回 500，
    也不要静默写入两行）。

    只做包装、不改 connect() 的语义：init_db / CLI / 测试都用裸连接，
    它们不需要（也不该）被这套判定影响。
    """

    def __init__(self, conn):
        self._conn = conn
        self.commit_attempted = False
        self.commit_count = 0

    # -- 委托给真实连接 -----------------------------------------------------
    def execute(self, *args, **kwargs):
        return self._conn.execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        return self._conn.executemany(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        self.commit_attempted = True
        return self._conn.executescript(*args, **kwargs)

    def cursor(self, *args, **kwargs):
        return self._conn.cursor(*args, **kwargs)

    def commit(self):
        self.commit_attempted = True          # 先置位，再提交（见类注释）
        self._conn.commit()
        self.commit_count += 1

    def commit_bookkeeping(self):
        """记账类提交（会话活跃时间这类幂等写入）。

        它**不影响** commit_attempted：这类写入再执行一次没有副作用，
        若因为它把整个请求标成"已提交"，后续真正的业务写入就会失去锁争用重试，
        一次单纯的时间戳刷新就能让用户看到 500。
        """
        self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()

    def __getattr__(self, name):             # pragma: no cover - 纯委托
        return getattr(self._conn, name)


def bookkeeping_commit(conn) -> None:
    """提交一次"记账型"写入。

    对 TrackedConnection 走专用入口（不参与重放判定）；对裸连接就是普通 commit，
    这样同一个函数在测试、CLI 与请求路径上都能用。
    """
    inner = getattr(conn, 'commit_bookkeeping', None)
    if callable(inner):
        inner()
    else:
        conn.commit()


def schema_present(conn) -> bool:
    """这套表是否已经建全。**只读查询，不产生任何写入。**

    这个判断是零停机发布能成立的关键之一：DDL 需要写锁，而两个进程同时写同一个
    SQLite 文件时，`CREATE TABLE IF NOT EXISTS` 这类语句在争用下会失败
    （实测：新进程启动时 `executescript` 直接抛 `attempt to write a readonly database`）。
    蓝绿发布的场景恰好就是"旧槽位正在服务的同时，新槽位启动"——
    第二个进程若是每次都重跑一遍 DDL，就等于每次发布都在赌这一次没有写冲突。
    """
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return set(EXPECTED_TABLES).issubset({row['name'] for row in rows})


def init_db(db_path, attempts: int = 6) -> None:
    """建库建表 + 补齐迁移（幂等）。可重复执行，不影响已有数据。

    三条与并发有关的纪律：

    1. **结构已建全就不再执行 DDL**。
       绝大多数启动都是这种情况（尤其蓝绿切换时的第二个进程），
       于是完全绕开了"两个进程同时持有写锁"这件事。
    2. **但版本检查不能被"结构已建全"短路**。
       这是第 1 期实测出来的硬缺口：`schema_present()` 只判**表名**，
       与"列是否齐全"无关；一旦它命中就 return，给已存在的表加列后
       升级库永远不会长出新列 → 运行时 `no such column`，而新建库的测试全绿
       （"本地全绿、上线才炸"）。所以 `run_migrations()` 无条件执行。
    3. 真正需要建表时**带退避重试**；迁移失败（非锁争用）**向上抛**，
       让新槽位起不来 —— 宁可发布中止、线上未动，也不要带着半套结构对外服务。
    """
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    error = None
    for attempt in range(attempts):
        conn = None
        try:
            conn = connect(db_path)
            # ── ① 首次建库：新建库直接就是**最新结构**（SCHEMA_SQL 已含两列）
            if not schema_present(conn):
                conn.executescript(SCHEMA_SQL)
                conn.commit()
            # ── ② 无论 schema_present 真假，都要补齐版本
            run_migrations(conn)
            return
        except sqlite3.OperationalError as err:
            error = err
            if attempt + 1 >= attempts:
                break
            # 线性退避：0.25s、0.5s、0.75s…总计约 5 秒，
            # 比 busy_timeout 的 15 秒短，不会把启动拖成"假死"。
            time.sleep(0.25 * (attempt + 1))
        finally:
            if conn is not None:
                conn.close()

    raise error


# --------------------------------------------------------------------------
# 版本化迁移层（CR-2026-001）
# --------------------------------------------------------------------------
#
# 为什么必须有这一层：`CREATE TABLE IF NOT EXISTS` 对"已存在的表加列"完全无效，
# 而 `init_db` 原来在 `schema_present()` 命中时整体跳过 DDL ——
# 于是只有全新库能拿到新列。迁移层是"升级库也能拿到新列"的唯一通道。

def _table_exists(conn, name: str) -> bool:
    """只读判断，不取写锁（并发下可安全调用）。"""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _applied_versions(conn) -> set:
    """已应用的迁移版本号；版本表还不存在时返回空集合（只读）。"""
    if not _table_exists(conn, 'schema_version'):
        return set()
    return {int(row['version']) for row in
            conn.execute('SELECT version FROM schema_version').fetchall()}


def _add_column_if_missing(conn, table: str, column: str, ddl: str) -> bool:
    """列已存在就跳过并返回 False —— 这就是"已应用"的判据，**不是异常**。

    为什么不能写成 `try: ALTER ... except: pass`：SQLite 的
    `ALTER TABLE ... ADD COLUMN` 没有 `IF NOT EXISTS`，吞异常会把
    「列已存在」与「表不存在 / 语法错 / 磁盘满 / 数据库被锁」归为同一类。
    后几种情况下接着写版本号 → 账上记着"已应用"、实际结构没改 →
    升级后写入立刻 `no such column`，而下次启动因为版本号已达标**永远不会再试**。
    那正是本 CR 要根治的"半套结构上线"。`PRAGMA table_info` 是只读查询，
    不取写锁，并发下安全。
    """
    existing = {row['name'] for row in
                conn.execute(f'PRAGMA table_info({table})').fetchall()}
    if column in existing:
        return False
    conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}')
    return True


def _migration_0001_users_is_system_admin(conn) -> None:
    """v1：系统管理员标记。系统管理员负责创建团队管理员账号。"""
    _add_column_if_missing(conn, 'users', 'is_system_admin',
                           'INTEGER NOT NULL DEFAULT 0')


def _migration_0002_users_must_change_password(conn) -> None:
    """v2：强制改密标记。新建账号置 1，改密成功后清零。"""
    _add_column_if_missing(conn, 'users', 'must_change_password',
                           'INTEGER NOT NULL DEFAULT 0')


def _migration_0003_work_items_version(conn) -> None:
    """v3：工单乐观锁列。每次"实际发生变更"的写 +1，只有服务端能改。"""
    _add_column_if_missing(conn, 'work_items', 'version',
                           'INTEGER NOT NULL DEFAULT 1')


def _migration_0004_work_items_archived_at(conn) -> None:
    """v4：工单软删除/归档时间。NULL = 未归档，非 NULL = 已从默认列表隐藏。"""
    _add_column_if_missing(conn, 'work_items', 'archived_at', 'TEXT')


def _migration_0005_api_tokens(conn) -> None:
    """v5：集成域令牌表。只存 sha256，明文一次性返回后即丢弃。

    `CREATE TABLE IF NOT EXISTS` 已足够做幂等；这里**仅**建表与索引，
    不碰任何数据、不写日志 —— 一条迁移一个意图。
    逐条 conn.execute() 而不是 executescript()，否则会隐式提交外层事务。
    """
    for statement in API_TOKEN_SCHEMA:
        conn.execute(statement)


def _migration_0006_api_idempotency(conn) -> None:
    """v6：幂等表。v5 的脚本已经建过它，这里保留独立版本意味着
    "幂等能力"有独立的到账记录；`IF NOT EXISTS` 让重复执行成为零操作。

    拆成两条而不是把两张表塞进 v5：中途失败时第 5 条不会被回滚，
    下次启动只补第 6 条（与 v1/v2 的拆分纪律一致）。
    """
    for statement in API_IDEMPOTENCY_SCHEMA:
        conn.execute(statement)


# 一条迁移 = 一个意图（排障时的最小定位粒度）。
# 拆成两条而不是合并：中途失败时已完成的一步不回滚（流水账如实记录到第几步），
# 下次启动只重试剩下的那条；幂等判重让拆分的代价为零。
# 版本号**严格递增、全局唯一、永不重用、永不修改**。
_MIGRATIONS = (
    (1, 'users.is_system_admin', _migration_0001_users_is_system_admin),
    (2, 'users.must_change_password', _migration_0002_users_must_change_password),
    (3, 'work_items.version', _migration_0003_work_items_version),
    (4, 'work_items.archived_at', _migration_0004_work_items_archived_at),
    (5, 'api_tokens', _migration_0005_api_tokens),
    (6, 'api_idempotency', _migration_0006_api_idempotency),
)

# 模块级自检：版本号写错（重复/乱序）在 import 时就炸，而不是等到某个库升级时
_versions = [item[0] for item in _MIGRATIONS]
if _versions != sorted(set(_versions)):
    raise RuntimeError(f'_MIGRATIONS 的版本号必须唯一且严格递增：{_versions}')


def run_migrations(conn) -> list:
    """按版本升序补齐未应用的迁移。已是最新版本时**全程只读、零写入**。

    每一条迁移的执行外壳是：`BEGIN IMMEDIATE` 抢写锁 → 二次判重 → 迁移
    → 写版本号 → commit；任何一步出错就整条 rollback 并**向上抛**。

    为什么要显式 `BEGIN IMMEDIATE`（两个都是实测结论，不是理论）：
      1. Python `sqlite3` 默认隔离级别下 **DDL 走 autocommit** —— 不显式开启事务，
         `ALTER` 语句自身就提交了，事务里只剩"版本号没写"，
         于是失败后表现为"列已加、账没记"，正是要避免的账实不一致；
      2. 不加锁保护时，两个槽位同时启动会**真的并发 ALTER**
         （`duplicate column name` / 版本表 UNIQUE 冲突）。
         第二层判重必须发生在抢到锁之后，否则读到的还是旧快照。
    """
    if conn.in_transaction:                 # 防御：进入时不得有未提交事务
        conn.rollback()
    if not _table_exists(conn, 'schema_version'):
        conn.executescript(SCHEMA_VERSION_SQL)
        conn.commit()

    applied = _applied_versions(conn)
    done = []
    for version, description, migrate in sorted(_MIGRATIONS, key=lambda item: item[0]):
        if version in applied:
            continue
        conn.execute('BEGIN IMMEDIATE')     # 抢 RESERVED 写锁：两槽位串行化
        try:
            if version in _applied_versions(conn):
                # 抢到锁后二次判重：另一个进程可能刚做完这一条
                conn.rollback()
                continue
            migrate(conn)                   # 内部 PRAGMA 判重；出错就抛，不吞
            conn.execute(
                'INSERT INTO schema_version (version, applied_at, description) '
                'VALUES (?, ?, ?)',
                (version, now_iso(), description))
            conn.commit()                   # 一个版本 = 一个事务
        except BaseException:
            conn.rollback()                 # 该条整体回滚（DDL 也一起回滚）
            raise                           # 向上抛 → init_db 不吞 → 启动失败
        applied.add(version)
        done.append(version)
    return done


# --------------------------------------------------------------------------
# JSON 编解码
# --------------------------------------------------------------------------

def decode_row(table: str, row) -> dict:
    """把库里的行转成前端可直接消费的 dict：JSON 列解析成对象/数组，布尔还原。"""
    spec = COLUMNS[table]
    out = {}
    for key in row.keys():
        value = row[key]
        kind = spec.get(key)
        if kind in JSON_KINDS:
            if value is None or value == '':
                out[key] = [] if kind == 'jsonarr' else None
            else:
                try:
                    out[key] = json.loads(value)
                except (TypeError, ValueError):
                    out[key] = [] if kind == 'jsonarr' else None
        elif kind == 'bool':
            out[key] = bool(value)
        else:
            out[key] = value
    return out


def encode_value(kind: str, value):
    """按字段规格归一化一个待写入的值。

    归一化而不是直接落库的原因：SQLite 是动态类型，如果 `progress` 被写成字符串
    '50'，`WHERE progress > 10` 这类比较会静默给出错误结果（类型亲和性只在
    部分场景生效）。前端会传 `Number(...)` 也可能传 `null`，所以统一在这里收敛。
    """
    if value is None:
        return None
    if kind == 'jsonarr':
        if isinstance(value, (list, tuple)):
            return json.dumps(list(value), ensure_ascii=False)
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except ValueError:
                parsed = [value]
            return json.dumps(parsed if isinstance(parsed, list) else [parsed], ensure_ascii=False)
        return json.dumps([value], ensure_ascii=False)
    if kind == 'json':
        if isinstance(value, str):
            try:
                json.loads(value)
                return value
            except ValueError:
                return json.dumps(value, ensure_ascii=False)
        return json.dumps(value, ensure_ascii=False)
    if kind == 'bool':
        if isinstance(value, str):
            return 1 if value.strip().lower() in ('1', 'true', 'yes', 'on') else 0
        return 1 if value else 0
    if kind == 'int':
        if isinstance(value, bool):
            return int(value)
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None
    if kind == 'real':
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    # text / date / ts
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return str(value)


def split_payload(table: str, payload: dict, *, for_insert: bool):
    """把客户端提交的字段拆成 (可写字段, 被丢弃字段)。

    丢弃规则（安全相关，不是清理垃圾）：
      · `id`        自增主键与创建时间不可由客户端指定；
      · `owner_id`  归属只能由服务端注入 —— 云版本靠列默认值 `auth.uid()` 防止伪造，
                    本地版没有列默认值，等价防线就是"客户端给什么都不采纳"；
      · 未知字段    写进去会污染库，也没有任何视图消费它。
    """
    spec = COLUMNS[table]
    writable, dropped = {}, []
    for key, value in (payload or {}).items():
        kind = spec.get(key)
        if kind is None or kind == 'pk':
            dropped.append(key)
            continue
        if kind in ('owner', 'stamp', 'lock'):
            # owner 由服务端注入；stamp 由服务端维护；
            # lock（version / archived_at）只能经 store.*_versioned 在权限判定之后
            # 显式写入，通用 CRUD 绝不可触达（§16.6.2）。
            dropped.append(key)
            continue
        if for_insert and key in ('id',):
            dropped.append(key)
            continue
        writable[key] = encode_value(kind, value)
    return writable, dropped
