"""行级权限判定：把云版本的 RLS 策略逐条复刻成本地可执行的谓词。

为什么要有这一层
----------------
云版本把"谁能看见/改动哪一行"交给数据库的 RLS 策略（`is_team_member` /
`is_team_admin` / `auth.uid()`）。换成自带存储之后，这条安全边界必须原样搬过来，
**不能下沉到前端**——前端那些"这条是不是我的"的判断只是显示层，构不成边界。

判定语义与云版本保持一致的三点
------------------------------
1. **双域共用一张表**：`team_id IS NULL` 是个人数据（只看自己的），
   `team_id` 非空是团队数据（本团队成员共同可见）。
2. **被拦下不等于报错**：更新/删除命中 0 行就返回空数组，由应用层显式提示"可能不在
   你的可见范围内"，而不是抛异常或假装成功（前端 `assertAffected` 依赖这一点）。
3. **写入的归属不由客户端决定**：`owner_id` 一律由 store 层注入当前账号。

与云版本的一处有意差异
----------------------
`team_members.status` 为非 active（被所有者停用）时，**视为非成员**。
云版本只判断成员行是否存在，停用后仍能看见团队数据，这与"停用即失去访问权限"的
产品语义相矛盾（前端 `saveMember` 的确认弹窗就是这么向用户承诺的）。这里按后者执行。
"""

from collections import namedtuple

Policy = namedtuple('Policy', 'select insert update delete')
# update 拆两段，对应 SQL 的 USING 与 WITH CHECK：
#   using  判定"这一行当前是否允许被我改动"
#   check  判定"改动之后的行是否仍然合法"（例如把个人情境挪到一个自己不在的团队）
#
# 第三段 `keep` 是本地版的补充，SQL 的 RLS 里没有对应物：有些规则必须**同时**
# 看到改动前后的两行才能判（"这一列不许被改"就是典型 —— 只看新行无法知道改没改）。
# 不用它就得把状态藏进 ctx 或者把规则写进 store 层，前者不可读、后者会绕过策略表。
PolicyUpdate = namedtuple('PolicyUpdate', 'using check keep', defaults=(None,))

TEAM_KEY = {
    'teams': 'id',
    'team_members': 'team_id',
    'work_items': 'team_id',
    'projects': 'team_id',
    'areas': 'team_id',
    'contexts': 'team_id',
    'resources': 'team_id',
    'progress_logs': 'team_id',
    'reviews': 'team_id',
    'on_call': 'team_id',
}


class AuthzContext:
    """一次请求的权限上下文：当前账号 + 其有效团队成员关系。"""

    def __init__(self, uid, memberships=None):
        self.uid = uid or ''
        self.memberships = dict(memberships or {})     # {team_id(int): role(str)}

    def is_member(self, team_id) -> bool:
        return team_id is not None and int(team_id) in self.memberships

    def is_admin(self, team_id) -> bool:
        return self.is_member(team_id) and self.memberships[int(team_id)] in ('owner', 'admin')

    def role_of(self, team_id):
        return self.memberships.get(int(team_id)) if team_id is not None else None

    def is_owner(self, team_id) -> bool:
        return self.is_member(team_id) and self.memberships[int(team_id)] == 'owner'


# --------------------------------------------------------------------------
# 谓词原件
# --------------------------------------------------------------------------

def _team_of(row, table):
    key = TEAM_KEY.get(table)
    return row.get(key) if key else None


def _member(row, ctx, table):
    return ctx.is_member(_team_of(row, table))


def _personal_or_member(table):
    def predicate(row, ctx):
        tid = _team_of(row, table)
        if tid is None:
            return row.get('owner_id') == ctx.uid
        return ctx.is_member(tid)
    return predicate


def _member_only(table):
    def predicate(row, ctx):
        return ctx.is_member(_team_of(row, table))
    return predicate


def _admin_or_row_owner(table):
    """删除策略：团队管理员，或这行数据本人的记录。"""
    def predicate(row, ctx):
        tid = _team_of(row, table)
        return ctx.is_admin(tid) or row.get('owner_id') == ctx.uid
    return predicate


def _owner_only(row, ctx):
    return row.get('owner_id') == ctx.uid


def _own_writable(table):
    """插入策略：归属必须是本人，且（个人数据 或 本团队成员）。

    云版本写的是 `owner_id = auth.uid() AND (team_id IS NULL OR is_team_member(team_id))`。
    本地版 owner_id 由服务端注入，所以这条恒真的前半段仍然保留在表达式里 ——
    它同时是"插入路径不许伪造归属"这条规则的书面记录。
    """
    def predicate(row, ctx):
        if row.get('owner_id') != ctx.uid:
            return False
        tid = _team_of(row, table)
        return tid is None or ctx.is_member(tid)
    return predicate


def _own_only_update(table):
    return PolicyUpdate(
        using=_personal_or_member(table),
        check=_own_writable(table),
    )


def _member_update(table):
    return PolicyUpdate(using=_member_only(table), check=_member_only(table))


# --------------------------------------------------------------------------
# 策略表（顺序与云版本 RLS 清单一致，便于逐条对照）
# --------------------------------------------------------------------------

def _teams_policy():
    # teams 的团队键是自身 id，不是 team_id
    def select(row, ctx):
        return row.get('owner_id') == ctx.uid or ctx.is_member(row.get('id'))

    def insert(row, ctx):
        return row.get('owner_id') == ctx.uid

    def update_using(row, ctx):
        return row.get('owner_id') == ctx.uid or ctx.is_admin(row.get('id'))

    def delete(row, ctx):
        return row.get('owner_id') == ctx.uid

    return Policy(select, insert, PolicyUpdate(update_using, update_using), delete)


def _team_members_policy():
    def select(row, ctx):
        return row.get('user_id') == ctx.uid or ctx.is_member(row.get('team_id'))

    def insert(row, ctx):
        # 云版本：is_team_admin(team_id) OR 该团队 owner 是我
        #
        # 但**任何经 /api/rows 的插入都不得产生 owner 行**：
        # owner 只由"建团队"产生（store.create_team_with_owner 走 _insert_raw，
        # 不过 policy），而这里原来没有任何 role 判断 —— 实测一个团队 admin
        # 直接 insert `role='owner'` 就能让团队出现第二个 owner，
        # 与 update_keep ②「只有 owner 能把成员变成 owner」直接矛盾。
        tid = row.get('team_id')
        if row.get('role') == 'owner':
            return False
        return ctx.is_admin(tid) or ctx.is_owner(tid)

    def update_using(row, ctx):
        return ctx.is_admin(row.get('team_id')) or row.get('user_id') == ctx.uid

    def update_keep(old_row, new_row, ctx):
        """必须同时看"改之前"和"改之后"的两条护栏。

        原来这里对 admin 是 `return True` 无条件放行，而同一张表的 delete
        早就写着"所有者不能被移出"。结果 admin 可以自提 owner、并把原 owner
        降成 member —— 团队所有权被悄悄夺走，且全过程没有任何一条策略拦它。
        """
        tid = new_row.get('team_id')
        old_role = old_row.get('role')
        new_role = new_row.get('role')
        # ① owner 这一行的 role 不可被修改 —— 不论操作者是谁。
        #    转让所有权应当走一条专门的路径（需要二次确认、需要审计），
        #    本期不做，所以先把这条口子关死：谁都改不了，也就谁都夺不走。
        if old_role == 'owner' and new_role != 'owner':
            return False
        # ② 只有 owner 能把某个成员变成 owner（admin 不行）。
        #    注意判断的是"变成"，所以 admin 改 owner 行的 title 之类不受影响，
        #    不至于把管理员正常的成员管理能力一起收掉。
        if new_role == 'owner' and old_role != 'owner':
            return ctx.is_owner(tid)
        # ③ owner 这一行的 **status 同样不可改**。
        #    ① 只锁了 role，于是"把 owner 停用"成了等价的夺权路径：
        #    实测 admin 发 {status: 'inactive'} 得到 200 rows=1，落库
        #    (role='owner', status='inactive') —— owner 失权、团队失去所有者。
        #    停用一个 owner 在语义上就是"把所有者移出团队"，而 delete 早就禁止了
        #    这件事（下面 delete 里的 `role != 'owner'`）。
        if old_role == 'owner' and new_row.get('status') != old_row.get('status'):
            return False
        return True

    def update_check(row, ctx):
        tid = row.get('team_id')
        if not ctx.is_admin(tid):
            # 非管理员（即普通 member）只能改自己那一行，且不得改变自己的角色
            # （③ 普通成员不得修改任何人的 role —— 自改也包含在内）。
            return row.get('user_id') == ctx.uid and row.get('role') == ctx.role_of(tid)
        return True

    def delete(row, ctx):
        # 所有者不能被移出（与前端 removeMember 的护栏一致）
        return row.get('role') != 'owner' and (
            ctx.is_admin(row.get('team_id')) or row.get('user_id') == ctx.uid
        )

    return Policy(select, insert, PolicyUpdate(update_using, update_check, update_keep), delete)


def _progress_logs_policy():
    def update_using(row, ctx):
        return row.get('owner_id') == ctx.uid

    def delete(row, ctx):
        return row.get('owner_id') == ctx.uid or ctx.is_admin(row.get('team_id'))

    return Policy(
        _personal_or_member('progress_logs'),
        _own_writable('progress_logs'),
        PolicyUpdate(update_using, update_using),
        delete,
    )


def _reviews_policy():
    def update_using(row, ctx):
        return row.get('owner_id') == ctx.uid

    return Policy(
        _personal_or_member('reviews'),
        _own_writable('reviews'),
        PolicyUpdate(update_using, update_using),
        update_using,
    )


def _app_logs_policy():
    def select(row, ctx):
        return row.get('owner_id') == ctx.uid

    def insert(row, ctx):
        # 云版本策略恒真，用于让"登录前就失败"的场景也能留下日志。
        # 本地版额外要求归属要么是本人、要么为空（未登录），不允许替他人写日志。
        return row.get('owner_id') in (ctx.uid, None, '')

    def deny(row, ctx):
        return False

    # app_logs 是运行日志：只允许写，不允许通过接口改或删（避免掩盖故障证据）
    return Policy(select, insert, PolicyUpdate(deny, deny), deny)


POLICIES = {
    'teams': _teams_policy(),
    'team_members': _team_members_policy(),
    'work_items': Policy(
        _personal_or_member('work_items'),
        _own_writable('work_items'),
        # 注意：work_items_update 的 USING 与 WITH CHECK 在云版本里是**同一条**表达式
        # （(team_id IS NULL AND owner_id = auth.uid()) OR (team_id NOT NULL AND is_team_member)
        # ），**不含"owner_id 必须是我"这一条**。这点很关键：团队成员必须能推进别人建的工单，
        # 否则看板流转当场失效。这里照抄不改。
        PolicyUpdate(_personal_or_member('work_items'), _personal_or_member('work_items')),
        _personal_or_member('work_items'),
    ),
    'projects': Policy(
        _member_only('projects'), _member_only('projects'),
        _member_update('projects'), _admin_or_row_owner('projects'),
    ),
    'areas': Policy(
        _member_only('areas'), _member_only('areas'),
        _member_update('areas'),
        lambda row, ctx: ctx.is_admin(_team_of(row, 'areas')),
    ),
    'contexts': Policy(
        _personal_or_member('contexts'),
        _own_writable('contexts'),
        # contexts_update 的 WITH CHECK 与 work_items 不同，它**确实**带上了
        # owner_id = auth.uid()，即非创建者改不动团队情境。这是云版本原有策略的形状，
        # 这里如实保留（差异本身就是需要被记录的事实，而不是顺手"修好"）。
        PolicyUpdate(_personal_or_member('contexts'), _own_writable('contexts')),
        _personal_or_member('contexts'),
    ),
    'resources': Policy(
        _member_only('resources'), _member_only('resources'),
        _member_update('resources'), _admin_or_row_owner('resources'),
    ),
    'progress_logs': _progress_logs_policy(),
    'reviews': _reviews_policy(),
    'on_call': Policy(
        _member_only('on_call'), _member_only('on_call'),
        _member_update('on_call'),
        lambda row, ctx: ctx.is_admin(_team_of(row, 'on_call')),
    ),
    'app_logs': _app_logs_policy(),
}


# --------------------------------------------------------------------------
# 对外接口
# --------------------------------------------------------------------------

class Denied(Exception):
    """写入被权限策略拒绝。

    注意：**查询/更新/删除的"看不见"不抛这个异常**（那要返回空结果，与 RLS 一致）；
    它只用于"整个操作在权限上根本不可能成立"的情况，例如往一个自己不是成员的团队里插数据。
    """

    def __init__(self, table, action, message):
        super().__init__(message)
        self.table = table
        self.action = action


def can_select(table, row, ctx) -> bool:
    return bool(POLICIES[table].select(row, ctx))


def can_insert(table, row, ctx) -> bool:
    return bool(POLICIES[table].insert(row, ctx))


def can_update(table, old_row, new_row, ctx) -> bool:
    policy = POLICIES[table].update
    if not policy.using(old_row, ctx):
        return False
    if policy.keep is not None and not policy.keep(old_row, new_row, ctx):
        return False
    return bool(policy.check(new_row, ctx))


def can_delete(table, row, ctx) -> bool:
    return bool(POLICIES[table].delete(row, ctx))


def load_context(conn, uid) -> AuthzContext:
    """从 team_members 读出当前账号的有效成员关系。

    这一步是**受信任的内部读取**：它必须绕过 team_members 自身的策略，
    否则就成了策略自引用（云版本用 SECURITY DEFINER 函数解决同一问题）。
    """
    rows = conn.execute(
        "SELECT team_id, role, status FROM team_members WHERE user_id = ?", (uid,)
    ).fetchall()
    memberships = {}
    for row in rows:
        status = (row['status'] or 'active').strip().lower()
        if status != 'active':
            continue
        memberships[int(row['team_id'])] = (row['role'] or 'member')
    return AuthzContext(uid, memberships)
