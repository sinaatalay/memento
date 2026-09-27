"""River models for Pydantic AI.

River's inference is a gRPC call (`river_client.Client.chat_complete`). Pydantic
AI speaks OpenAI, so a tiny OpenAI-compatible endpoint runs in a daemon thread
and forwards each /v1/chat/completions request to River.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

FAST = "Qwen/Qwen3.6-35B-A3B-FP8"  # ~1.7 s, cheapest
MID = "deepseek-ai/DeepSeek-V4-Flash-0731"  # ~4 s, stronger

_local = threading.local()
_started = threading.Lock()
_server: ThreadingHTTPServer | None = None


def _client():
    if not hasattr(_local, "client"):
        import river_client

        _local.client = river_client.Client(api_key=os.environ["RIVER_API_KEY"])
    return _local.client


def _text(content) -> str:
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""


def complete(request: dict) -> dict:
    """One OpenAI-style chat completion served by River."""
    model = request["model"]
    messages = []
    for m in request["messages"]:
        m = dict(m)
        if m.get("role") == "developer":
            m["role"] = "system"
        if "content" in m and not m.get("tool_calls"):
            m["content"] = _text(m["content"])
        messages.append(m)
    kwargs = {
        "max_tokens": request.get("max_tokens") or request.get("max_completion_tokens") or 4096,
        "temperature": request.get("temperature", 0.0),
        "timeout": 120,
    }
    for key in ("tools", "tool_choice", "top_p", "stop", "response_format"):
        if request.get(key) is not None:
            kwargs[key] = request[key]
    if "Qwen" in model:
        kwargs["chat_template_kwargs"] = {"enable_thinking": False}
    last: Exception | None = None
    for attempt in range(3):
        try:
            response = _client().chat_complete(messages, base_model=model, **kwargs)
            body = response.response_json
            return json.loads(body) if isinstance(body, str) else body
        except Exception as e:  # queue timeouts and transient gRPC errors
            last = e
            time.sleep(1 + attempt)
    raise RuntimeError(f"River failed: {last}")


class _Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj: dict) -> None:
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        if not self.path.rstrip("/").endswith("/chat/completions"):
            return self._send(404, {"error": {"message": "not found"}})
        request = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        try:
            body = complete(request)
        except Exception as e:
            return self._send(502, {"error": {"message": str(e)}})
        body.setdefault("id", "chatcmpl-" + uuid.uuid4().hex[:12])
        body.setdefault("object", "chat.completion")
        body.setdefault("created", int(time.time()))
        body.setdefault("model", request["model"])
        self._send(200, body)

    def log_message(self, *args) -> None:
        pass


def ensure_endpoint() -> str:
    global _server
    with _started:
        if _server is None:
            _server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)  # any free port, per process
            threading.Thread(target=_server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{_server.server_address[1]}/v1"


def model(name: str = MID) -> OpenAIChatModel:
    """A Pydantic AI model backed by a River base model."""
    return OpenAIChatModel(name, provider=OpenAIProvider(base_url=ensure_endpoint(), api_key="river"))
