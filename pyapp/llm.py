"""可选的自带密钥 AI 通道（OpenAI 兼容协议）。

它替代的是云平台那个"免密钥 LLM 网关"。替代方式不是找另一家云，而是
**把厂商选择权和密钥交给使用者自己**：任何暴露 `/chat/completions` 的服务都能接
（OpenAI、DeepSeek、通义、智谱、Kimi、本地 Ollama / vLLM / LM Studio…），
服务端只做协议转发，不绑定任何一家。

三条边界
--------
1. **密钥只留在服务端**。浏览器永远拿不到它，`GET /api/llm/config` 只回显
   "配没配 + 模型 + 端点"，绝不回显密钥本身（连掩码都不给，避免被误当凭据使用）。
2. **未配置时明确报错**，返回 `LLM_NOT_CONFIGURED` 与可操作的指引，
   而不是静默降级成空结果让用户以为"AI 没反应"。
3. **不日志密钥**。异常信息里若带上游响应片段，先做一次脱敏。

只用标准库 `urllib`，不引入 requests / httpx —— 零依赖是本项目的硬约束。
"""

import json
import urllib.error
import urllib.request


class LlmError(Exception):
    def __init__(self, code, message, status=502):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _headers(cfg):
    return {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {cfg.llm_api_key}',
        'Accept': 'application/json',
    }


def _require_config(cfg):
    if not cfg.llm_configured:
        raise LlmError(
            'LLM_NOT_CONFIGURED',
            '尚未配置 AI 服务：请在「账号 → AI 设置」里填入服务地址与密钥，'
            '或设置环境变量 GTD_LLM_API_KEY。',
            400,
        )


def _request(cfg, path, *, method='GET', body=None, stream=False, timeout=None):
    _require_config(cfg)
    url = f'{cfg.llm_base_url}{path}'
    data = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
    request = urllib.request.Request(url, data=data, headers=_headers(cfg), method=method)
    try:
        return urllib.request.urlopen(request, timeout=timeout or cfg.llm_timeout)
    except urllib.error.HTTPError as err:                       # 上游返回了错误码
        detail = ''
        try:
            detail = err.read().decode('utf-8', 'replace')
        except Exception:
            detail = ''
        raise LlmError(
            'LLM_UPSTREAM_ERROR',
            f'上游 AI 服务返回 {err.code}：{_scrub(detail, cfg)[:400]}',
            502,
        ) from err
    except urllib.error.URLError as err:                        # 连不上 / DNS / 超时
        raise LlmError('LLM_UNREACHABLE', f'无法连接 AI 服务（{err.reason}）', 502) from err
    except TimeoutError as err:
        raise LlmError('LLM_TIMEOUT', 'AI 服务响应超时', 504) from err


def _scrub(text, cfg):
    """把可能出现在上游回显里的密钥抹掉，再交给上层拼错误消息。"""
    if not text:
        return ''
    if cfg.llm_api_key:
        text = text.replace(cfg.llm_api_key, '***')
    return text


def list_models(cfg):
    with _request(cfg, '/models') as response:
        payload = json.loads(response.read().decode('utf-8'))
    models = payload.get('data') or []
    return [
        {'id': m.get('id'), 'ownedBy': m.get('owned_by')}
        for m in models if isinstance(m, dict) and m.get('id')
    ]


def chat(cfg, *, messages, model=None, temperature=None, max_tokens=None,
         response_format=None, timeout=None):
    """一次性对话（内部仍用非流式请求，返回完整文本）。

    与云版本网关的差别需要说清楚：云端那条通道**强制流式**，一次性任务也得自己
    在业务层把流拼起来；这里直接支持非流式，分类/抽取这类短任务不必再绕一圈。
    需要流式时用 `stream_chat()`。
    """
    body = {
        'model': model or cfg.llm_model,
        'messages': messages,
        'stream': False,
    }
    if temperature is not None:
        body['temperature'] = temperature
    if max_tokens is not None:
        body['max_tokens'] = max_tokens
    if response_format is not None:
        body['response_format'] = response_format

    with _request(cfg, '/chat/completions', method='POST', body=body, timeout=timeout) as response:
        payload = json.loads(response.read().decode('utf-8'))

    choice = (payload.get('choices') or [{}])[0]
    message = choice.get('message') or {}
    content = message.get('content')
    if isinstance(content, list):          # 少数端点按分片返回 content
        content = ''.join(part.get('text', '') for part in content if isinstance(part, dict))
    return {
        'text': content or '',
        'reasoning': message.get('reasoning_content') or '',
        'model': payload.get('model') or body['model'],
        'usage': payload.get('usage') or {},
        'finishReason': choice.get('finish_reason'),
    }


def stream_chat(cfg, *, messages, model=None, temperature=None, max_tokens=None,
                response_format=None, timeout=None):
    """流式对话：逐块产出 `{'type': 'delta'|'reasoning'|'done', ...}`。

    只做协议搬运，不解析业务语义。前端若要 SSE 直通，HTTP 层直接包一层即可。
    """
    body = {
        'model': model or cfg.llm_model,
        'messages': messages,
        'stream': True,
    }
    if temperature is not None:
        body['temperature'] = temperature
    if max_tokens is not None:
        body['max_tokens'] = max_tokens
    if response_format is not None:
        body['response_format'] = response_format

    with _request(cfg, '/chat/completions', method='POST', body=body,
                  stream=True, timeout=timeout) as response:
        for raw_line in response:
            line = raw_line.decode('utf-8', 'replace').strip()
            if not line or not line.startswith('data:'):
                continue
            chunk = line[5:].strip()
            if chunk == '[DONE]':
                break
            try:
                payload = json.loads(chunk)
            except ValueError:
                continue
            choice = (payload.get('choices') or [{}])[0]
            delta = choice.get('delta') or {}
            # reasoning_content 与 content 是两条独立通道，必须分开累积
            if delta.get('reasoning_content'):
                yield {'type': 'reasoning', 'text': delta['reasoning_content']}
            if delta.get('content'):
                yield {'type': 'delta', 'text': delta['content']}
            if choice.get('finish_reason'):
                yield {'type': 'done', 'finishReason': choice['finish_reason'],
                       'usage': payload.get('usage') or {}}


def strip_code_fence(text):
    """剥掉模型可能裹在 JSON 外面的 ``` 围栏。

    实测经验：即使指定了 `response_format: json_object`，模型仍会偶尔吐
    ```json ... ```；直接 `JSON.parse` 会抛错，所以解析前必须先剥一层。
    """
    if not text:
        return ''
    stripped = text.strip()
    if not stripped.startswith('```'):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith('```'):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith('```'):
        lines = lines[:-1]
    return '\n'.join(lines).strip()
