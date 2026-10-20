"""Bounded Chat Completions adapter; no tools, writes, retries or raw provider errors."""
import asyncio
from collections import defaultdict, deque
import json
from threading import Lock
import time

import httpx

from app.errors import APIError
from app.observability import increment

_lock = Lock()
_calls = defaultdict(deque)
_active = set()


async def completion(options, key, messages, subject, *, test=False, scope='design'):
    now = time.monotonic()
    with _lock:
        history = _calls[subject]
        while history and now - history[0] >= 3600:
            history.popleft()
        if subject in _active or len(_active) >= 2:
            raise APIError(429, 'AI_BUSY', '已有模型请求进行中，请完成后再试。')
        if len(history) >= options['calls_per_hour']:
            raise APIError(429, 'AI_BUDGET_EXCEEDED', '当前操作者已达到本进程每小时模型调用上限。')
        history.append(now)
        _active.add(subject)
    increment(scope + '_llm_calls')
    try:
        payload = {'model': options['model'], 'messages': messages, 'stream': False,
                   options['token_parameter']: 32 if test else options['max_tokens']}
        if options['json_mode']:
            payload['response_format'] = {'type': 'json_object'}
        async with asyncio.timeout(options['timeout_seconds']):
            async with httpx.AsyncClient(timeout=options['timeout_seconds'], trust_env=False, follow_redirects=False) as client:
                async with client.stream('POST', options['base_url'] + '/chat/completions',
                        headers={'Authorization': 'Bearer ' + key.get_secret_value()}, json=payload) as response:
                    if response.status_code != 200:
                        increment(scope + '_llm_failures')
                        code = response.status_code
                        raise APIError(502, 'AI_PROVIDER_REJECTED',
                            '模型服务拒绝请求，请检查密钥或权限。' if code in (401, 403) else
                            '模型服务余额不足。' if code == 402 else
                            '模型服务限流，请稍后重试。' if code == 429 else
                            '模型服务返回错误，请检查模型名、接口地址和兼容参数。')
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 1024 * 1024:
                            raise APIError(502, 'AI_OUTPUT_LIMIT', '模型响应超过 1 MiB，已停止读取。')
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get('choices'), list):
            raise ValueError()
        choice = data['choices'][0]
        if not isinstance(choice, dict) or not isinstance(choice.get('message'), dict):
            raise ValueError()
        if choice.get('finish_reason') != 'stop' or choice['message'].get('tool_calls'):
            raise APIError(502, 'AI_OUTPUT_INCOMPLETE', '模型输出不完整或包含工具调用，请调整生成长度后重试。')
        content = choice['message']['content']
        if not isinstance(content, str) or len(content.encode()) > 128 * 1024:
            raise APIError(502, 'AI_OUTPUT_LIMIT', '模型正文无效或超过提案限制。')
        usage = data.get('usage') or {}
        if not isinstance(usage, dict):
            raise ValueError()
        safe_usage = {name: value for name, value in usage.items()
                      if name in ('prompt_tokens', 'completion_tokens', 'total_tokens') and type(value) is int and 0 <= value <= 10**8}
        for name, value in safe_usage.items():
            increment(scope + '_llm_' + name, value)
        increment(scope + '_llm_successes')
        return content, safe_usage
    except (TimeoutError, httpx.TimeoutException):
        increment(scope + '_llm_timeouts')
        raise APIError(504, 'AI_TIMEOUT', '模型调用超时，原草稿未修改；不会自动重试。') from None
    except httpx.HTTPError:
        increment(scope + '_llm_failures')
        raise APIError(502, 'AI_CONNECTION_FAILED', '无法连接模型服务，请检查网络、API 地址和证书。') from None
    except (ValueError, KeyError, IndexError, TypeError):
        increment(scope + '_llm_failures')
        raise APIError(502, 'AI_RESPONSE_INVALID', '模型服务响应格式无效。') from None
    finally:
        with _lock:
            _active.discard(subject)
