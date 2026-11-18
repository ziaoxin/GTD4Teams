"""内置 MCP 服务：JSON-RPC 2.0 / 单次 POST /mcp（无 SSE、无 batch、无会话）。

削减项必须显式声明（TDS §16.8.1）：
  * `GET /mcp` → 405，不实现 SSE 流；
  * 不支持 JSON-RPC batch（请求体必须是单个对象）；
  * 不实现 Mcp-Session-Id；
  * initialize 的 capabilities.gtd.unsupported 明示 views / availability / 提醒 / 日历 / 附件
    —— 这些数据模型在本项目里根本不存在，硬造工具等于返回编造的数据。

鉴权、权限、审计全部复用 integration / store / policy，没有第二套实现。
"""

import json

from . import APP_VERSION, db, store
from .httpd import ApiError
from .integration import IntegrationHandler, IntegrationError, _audit_context, _ctx, _err
from .policy import can_select

# JSON-RPC 标准错误码
RPC_INVALID_REQUEST = -32600
RPC_METHOD_NOT_FOUND = -32601
RPC_INVALID_PARAMS = -32602
RPC_APP_ERROR = -32001

SUPPORTED_TOOLS = {
    'list_work_items': 'read',
    'get_work_item': 'read',
    'list_projects': 'read',
    'get_project': 'read',
    'list_areas': 'read',
    'list_contexts': 'read',
    'list_members': 'read',
    'list_progress_logs': 'read',
    'list_reviews': 'read',
    'search': 'read',
    'update_work_item': 'write:edit',
    'complete_work_item': 'write:edit',
    'restore_work_item': 'write:edit',
    'archive_work_item': 'write:archive',
    'delete_work_item': 'write:delete',
}


def _rpc_result(request_id, result):
    return {'jsonrpc': '2.0', 'id': request_id, 'result': result}


def _rpc_error(request_id, code, message, gtd_code=None):
    error = {'code': code, 'message': message}
    if gtd_code:
        error['data'] = {'gtdCode': gtd_code}
    return {'jsonrpc': '2.0', 'id': request_id, 'error': error}


def _tool_result(data):
    """MCP 工具的标准返回：文本块里放 JSON，便于任何客户端直接展示。"""
    return {'content': [{'type': 'text',
                         'text': json.dumps(data, ensure_ascii=False)}],
            'isError': False}


def _limit(arguments, default=100, high=500):
    raw = arguments.get('limit', default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise _err(400,'BAD_ARGUMENT', 'limit 必须是整数', )
    return max(1, min(value, high))


def _team_filter(arguments, rows):
    raw = arguments.get('teamId')
    if raw in (None, ''):
        return rows
    try:
        team_id = int(raw)
    except (TypeError, ValueError):
        raise _err(400,'BAD_ARGUMENT', 'teamId 必须是整数')
    return [row for row in rows if int(row.get('team_id') or 0) == team_id]


def _visible_row(handler, conn, table, row_id, *, include_archived=False):
    try:
        value = int(row_id)
    except (TypeError, ValueError):
        raise _err(400,'BAD_ARGUMENT', 'id 必须是整数')
    row = store._fetch(conn, table, value)
    if row is None or not can_select(table, row, _ctx(handler, conn)):
        raise _err(404, 'not_found', '记录不存在或不可见')
    # 与 REST 单条 / 列表同一口径：软删行一律当作不存在（TC-SEC-INT-21）。
    # 写工具（含 restore_work_item）不走这里，而是直接进 store.*_versioned，
    # 所以归档行依然可以被恢复或再次软删。
    if not include_archived and row.get('archived_at'):
        raise _err(404, 'not_found', '记录不存在或不可见')
    return row


# --------------------------------------------------------------------------
# 10 个只读工具
# --------------------------------------------------------------------------

def _list_items(handler, conn, args):
    rows = store.select_table(conn, _ctx(handler, conn), 'work_items')
    rows = [row for row in rows if not row.get('archived_at')]
    rows = _team_filter(args, rows)
    if args.get('status'):
        rows = [row for row in rows if str(row.get('status') or '') == str(args['status'])]
    if args.get('assignee'):
        who = str(args['assignee'])
        rows = [row for row in rows
                if str(row.get('assignee_id') or '') == who
                or str(row.get('assignee_name') or '') == who]
    return rows[:_limit(args)]


def _get_item(handler, conn, args):
    if 'id' not in args:
        raise _err(400,'BAD_ARGUMENT', '缺少 id')
    return _visible_row(handler, conn, 'work_items', args['id'])


def _list_projects(handler, conn, args):
    return _team_filter(args, store.select_table(conn, _ctx(handler, conn), 'projects'))


def _get_project(handler, conn, args):
    if 'id' not in args:
        raise _err(400,'BAD_ARGUMENT', '缺少 id')
    return _visible_row(handler, conn, 'projects', args['id'])


def _list_areas(handler, conn, args):
    return _team_filter(args, store.select_table(conn, _ctx(handler, conn), 'areas'))


def _list_contexts(handler, conn, args):
    return _team_filter(args, store.select_table(conn, _ctx(handler, conn), 'contexts'))


def _list_members(handler, conn, args):
    rows = _team_filter(args, store.select_table(conn, _ctx(handler, conn), 'team_members'))
    # 显式投影：不因表结构将来加列而把敏感字段带出去。
    return [{key: row.get(key) for key in ('id', 'email', 'display_name', 'role', 'status')}
            for row in rows]


def _list_progress_logs(handler, conn, args):
    rows = store.select_table(conn, _ctx(handler, conn), 'progress_logs')
    item_id = args.get('itemId')
    if item_id not in (None, ''):
        try:
            value = int(item_id)
        except (TypeError, ValueError):
            raise _err(400,'BAD_ARGUMENT', 'itemId 必须是整数')
        rows = [row for row in rows if int(row.get('item_id') or 0) == value]
    return rows[:_limit(args)]


def _list_reviews(handler, conn, args):
    rows = _team_filter(args, store.select_table(conn, _ctx(handler, conn), 'reviews'))
    if args.get('kind'):
        rows = [row for row in rows if str(row.get('kind') or '') == str(args['kind'])]
    return rows[:_limit(args)]


def _search(handler, conn, args):
    term = str(args.get('q') or '').strip().lower()
    if not term:
        raise _err(400,'BAD_ARGUMENT', '缺少 q')
    rows = store.select_table(conn, _ctx(handler, conn), 'work_items')
    # 与列表/单条一致：软删行不进搜索结果。
    rows = [row for row in rows if not row.get('archived_at')]
    hits = [row for row in rows
            if term in str(row.get('title') or '').lower()
            or term in str(row.get('notes') or '').lower()]
    return hits[:_limit(args, default=50, high=200)]


READ_TOOLS = {
    'list_work_items': _list_items,
    'get_work_item': _get_item,
    'list_projects': _list_projects,
    'get_project': _get_project,
    'list_areas': _list_areas,
    'list_contexts': _list_contexts,
    'list_members': _list_members,
    'list_progress_logs': _list_progress_logs,
    'list_reviews': _list_reviews,
    'search': _search,
}

# --------------------------------------------------------------------------
# 写工具：全部走 store.*_versioned，必须 expectedVersion
# --------------------------------------------------------------------------

def _write_tool(handler, conn, args, *, server_values=None, require_delete=False):
    if 'id' not in args:
        raise _err(400,'BAD_ARGUMENT', '缺少 id')
    if 'expectedVersion' not in args:
        raise _err(400,'PATCH_PRECONDITION_REQUIRED',
                               'MCP 写工具必须提供 expectedVersion')
    if require_delete and args.get('confirm') is not True:
        raise _err(400,'CONFIRM_REQUIRED',
                               '删除必须显式传 confirm: true')
    try:
        row_id = int(args['id'])
    except (TypeError, ValueError):
        raise _err(400,'BAD_ARGUMENT', 'id 必须是整数')
    audit = _audit_context(handler)
    audit.update({'method': 'tools/call', 'path': '/mcp'})
    result = store.update_versioned(
        conn, _ctx(handler, conn), 'work_items', row_id,
        {key: value for key, value in args.items()
         if key not in ('id', 'expectedVersion', 'confirm')},
        expected_version=args['expectedVersion'],
        server_values=server_values, audit=audit, require_delete=require_delete,
    )
    return result['data']


def _update_item(handler, conn, args):
    return _write_tool(handler, conn, args)


def _complete_item(handler, conn, args):
    return _write_tool(handler, conn, args,
                       server_values={'status': 'done', 'completed_at': db.now_iso()})


def _restore_item(handler, conn, args):
    return _write_tool(handler, conn, args, server_values={'archived_at': None})


def _archive_item(handler, conn, args):
    return _write_tool(handler, conn, args, server_values={'archived_at': db.now_iso()})


def _delete_item(handler, conn, args):
    return _write_tool(
        handler, conn, args, require_delete=True,
        server_values={'archived_at': db.now_iso(), 'status': 'archived'},
    )


WRITE_TOOLS = {
    'update_work_item': _update_item,
    'complete_work_item': _complete_item,
    'restore_work_item': _restore_item,
    'archive_work_item': _archive_item,
    'delete_work_item': _delete_item,
}

ALL_TOOLS = dict(READ_TOOLS, **WRITE_TOOLS)


# --------------------------------------------------------------------------
# JSON-RPC 方法
# --------------------------------------------------------------------------

def _tool_descriptions(handler):
    """按令牌 scope 裁剪工具列表：只读令牌看不到必然失败的写工具。"""
    scopes = set(getattr(handler, '_api_scopes', ()) or ())
    tools = []
    for name, required in SUPPORTED_TOOLS.items():
        if required not in scopes:
            continue
        tools.append({
            'name': name,
            'description': 'GTD 工作项：' + ('读取' if required == 'read' else f'写入（{required}）'),
            'inputSchema': {'type': 'object', 'properties': {}},
        })
    return tools


def _rpc_call(handler, conn, request):
    request_id = request.get('id')
    method = request.get('method')
    params = request.get('params') or {}

    if method == 'initialize':
        # **不泄露实例信息**：没有 dbPath / llmConfigured / 管理员数量。
        return _rpc_result(request_id, {
            'protocolVersion': '2024-11-05',
            'capabilities': {
                'tools': {'listChanged': False},
                'gtd': {'unsupported': [
                    'views', 'availability', 'notifications', 'calendar', 'attachments',
                ]},
            },
            'serverInfo': {'name': 'gtd4teams', 'version': APP_VERSION},
        })
    if method in ('ping', 'notifications/initialized'):
        return _rpc_result(request_id, {})
    if method == 'tools/list':
        return _rpc_result(request_id, {'tools': _tool_descriptions(handler)})
    if method == 'tools/call':
        name = params.get('name')
        arguments = params.get('arguments') or {}
        if name not in ALL_TOOLS:
            return _rpc_error(request_id, RPC_METHOD_NOT_FOUND,
                              f'未知工具：{name}')
        if not isinstance(arguments, dict):
            return _rpc_error(request_id, RPC_INVALID_PARAMS, 'arguments 必须是对象')
        required = SUPPORTED_TOOLS[name]
        if required not in (getattr(handler, '_api_scopes', ()) or ()):
            return _rpc_error(request_id, RPC_APP_ERROR,
                              '该令牌缺少工具所需权限', gtd_code='INSUFFICIENT_SCOPE')
        try:
            data = ALL_TOOLS[name](handler, conn, arguments)
            return _rpc_result(request_id, _tool_result(data))
        except IntegrationError as err:
            return _rpc_error(request_id, RPC_APP_ERROR, err.message, gtd_code=err.code)
        except store.StoreError as err:
            return _rpc_error(request_id, RPC_APP_ERROR, err.message, gtd_code=err.code)
    return _rpc_error(request_id, RPC_METHOD_NOT_FOUND, f'未知方法：{method}')


def h_mcp(handler, conn, body, query):
    """POST /mcp：单请求单响应。batch 数组由 _read_body 直接判 BAD_JSON。"""
    request_id = body.get('id')
    if body.get('jsonrpc') != '2.0':
        handler._json(_rpc_error(request_id, RPC_INVALID_REQUEST,
                                 'jsonrpc 必须是 2.0'), status=400)
        return
    if not isinstance(body.get('method'), str) or not body['method']:
        handler._json(_rpc_error(request_id, RPC_INVALID_REQUEST,
                                 'method 必须是非空字符串'), status=400)
        return
    handler._json(_rpc_call(handler, conn, body))


h_mcp.gtd_scope = 'mcp'


def h_mcp_method_not_allowed(handler, conn, body, query):
    """GET /mcp → 405：明确告知不支持 SSE，而不是含糊的 404。"""
    raise ApiError(405, 'METHOD_NOT_ALLOWED',
                   'MCP 只支持 POST /mcp 的单次 JSON-RPC 请求；不实现 SSE 流')


MCP_ROUTES = {
    ('POST', '/mcp'): h_mcp,
    ('GET', '/mcp'): h_mcp_method_not_allowed,
}


class McpHandler(IntegrationHandler):
    """MCP 监听器：与 API 监听器共用鉴权/日志/重试，只有路由表不同。"""

    ROUTES_TABLE = MCP_ROUTES
    PARAM_ROUTES_TABLE = {}
