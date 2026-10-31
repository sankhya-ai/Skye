"""Provider compatibility against a local mock: the host's LLM proxy path, no paid calls."""

from __future__ import annotations

import asyncio

import httpx
from mock_provider import MockProvider

from tura_code.llm_proxy import LLMProxy


class FakeRequest:
    def __init__(self, token: str, body: bytes):
        self.headers = {"authorization": f"Bearer {token}", "content-type": "application/json"}
        self.method = "POST"
        self.query_params = {}
        self._body = body

    async def body(self):
        return self._body


def test_proxy_swaps_token_for_key_and_streams_tool_calls():
    async def go(mock):
        proxy = LLMProxy()
        proxy.base = "http://host"
        base, token = proxy.issue("t", "s", lambda: (mock.base_url, "sk-real"))
        body = b'{"model":"m","stream":true,"messages":[{"role":"assistant","tool_calls":[{"id":"1"}],"content":""}]}'
        response = await proxy.forward(FakeRequest(token, body), "v1/chat/completions")
        data = b"".join([chunk async for chunk in response.body_iterator])
        await proxy.close()
        return data

    with MockProvider([{"tool_calls": [{"name": "find_tools", "arguments": {"query": "read a pdf"}}]}]) as mock:
        data = asyncio.run(go(mock))
    assert mock.auth == ["Bearer sk-real"]  # the runtime's token never reaches the provider
    assert b"find_tools" in data and b"[DONE]" in data
    assert mock.requests[0]["messages"][0]["reasoning_content"] == ""  # echoed for non-OpenRouter upstreams


def test_proxy_passes_provider_errors_through():
    async def go(mock):
        proxy = LLMProxy()
        proxy.base = "http://host"
        _, token = proxy.issue("t", "s", lambda: (mock.base_url, "k"))
        response = await proxy.forward(FakeRequest(token, b'{"model":"m"}'), "v1/chat/completions")
        await proxy.close()
        return response.status_code

    with MockProvider([{"status": 429}]) as mock:
        assert asyncio.run(go(mock)) == 429


def test_mock_non_streaming_shape_is_openai_compatible():
    with MockProvider([{"text": "hi"}]) as mock:
        r = httpx.post(f"{mock.base_url}/chat/completions", json={"model": "m", "messages": []})
    assert r.json()["choices"][0]["message"]["content"] == "hi" and r.json()["usage"]["total_tokens"] == 15


def test_proxy_counts_provider_usage_and_refuses_past_the_cap():
    async def go(mock):
        proxy = LLMProxy()
        proxy.base = "http://host"
        _, token = proxy.issue("t", "s1", lambda: (mock.base_url, "k"))
        proxy.budget("s1", 25)  # each mock reply reports 15 tokens
        codes = []
        for _ in range(3):
            response = await proxy.forward(FakeRequest(token, b'{"model":"m","stream":true}'), "v1/chat/completions")
            if hasattr(response, "body_iterator"):
                [c async for c in response.body_iterator]
            codes.append(response.status_code)
        used = proxy.used("s1")
        await proxy.close()
        return codes, used

    with MockProvider([{"text": "a"}, {"text": "b"}, {"text": "c"}]) as mock:
        codes, used = asyncio.run(go(mock))
    assert codes == [200, 200, 402] and used == 30  # one call may overshoot the cap; the next is refused
    assert len(mock.requests) == 2
