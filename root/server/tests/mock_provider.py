"""A local OpenAI-compatible chat-completions server that replays scripted replies.

For testing mechanics (tool discovery, scope, retries, proxy, usage) without a paid model.
It proves nothing about coding quality or token savings: replies are synthetic and fixed.
Each reply is ``{"text": ...}``, ``{"tool_calls": [{"name":..., "arguments": {...}}]}`` or
``{"status": 429|500}``. Requests are recorded in ``.requests``.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MockProvider:
    def __init__(self, replies: list[dict]):
        self.replies = list(replies)
        self.requests: list[dict] = []
        self.auth: list[str] = []
        self.paths: list[str] = []
        provider = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence
                pass

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
                provider.paths.append(self.path)
                if not self.path.endswith("chat/completions"):  # e.g. a director's /alpha/decisions: not scripted
                    self.send_response(404)
                    self.send_header("content-type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":{"message":"not found"}}')
                    return
                provider.requests.append(body)
                provider.auth.append(self.headers.get("authorization", ""))
                reply = provider.replies.pop(0) if provider.replies else {"text": "done"}
                if reply.get("delay"):
                    time.sleep(reply["delay"])
                if reply.get("status"):
                    self.send_response(reply["status"])
                    self.send_header("content-type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": {"message": "scripted failure"}}).encode())
                    return
                chunks = []
                if reply.get("tool_calls"):
                    calls = [
                        {"index": i, "id": f"call_{len(provider.requests)}_{i}", "type": "function",
                         "function": {"name": c["name"], "arguments": json.dumps(c.get("arguments", {}))}}
                        for i, c in enumerate(reply["tool_calls"])
                    ]
                    chunks.append({"delta": {"role": "assistant", "content": None, "tool_calls": calls}, "finish_reason": None})
                    finish = "tool_calls"
                else:
                    chunks.append({"delta": {"role": "assistant", "content": reply.get("text", "")}, "finish_reason": None})
                    finish = "stop"
                chunks.append({"delta": {}, "finish_reason": finish})
                usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, **({"cost": reply["cost"]} if "cost" in reply else {})}
                if body.get("stream"):
                    self.send_response(200)
                    self.send_header("content-type", "text/event-stream")
                    self.end_headers()
                    for c in chunks:
                        self.wfile.write(b"data: " + json.dumps({"id": "mock", "object": "chat.completion.chunk", "model": body.get("model", "mock"), "choices": [{"index": 0, **c}]}).encode() + b"\n\n")
                    self.wfile.write(b"data: " + json.dumps({"id": "mock", "object": "chat.completion.chunk", "choices": [], "usage": usage}).encode() + b"\n\ndata: [DONE]\n\n")
                else:
                    message = {"role": "assistant", "content": chunks[0]["delta"].get("content"), **({"tool_calls": [{k: v for k, v in c.items() if k != "index"} for c in chunks[0]["delta"]["tool_calls"]]} if reply.get("tool_calls") else {})}
                    payload = {"id": "mock", "object": "chat.completion", "model": body.get("model", "mock"), "choices": [{"index": 0, "message": message, "finish_reason": finish}], "usage": usage}
                    data = json.dumps(payload).encode()
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
