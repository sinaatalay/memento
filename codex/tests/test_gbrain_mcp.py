"""Shared-owner MCP adapter: wire protocol, provenance, CAS, and safe failures."""

from contextlib import asynccontextmanager
import base64
import json
import time
from urllib.parse import parse_qs

import httpx2
from mcp.types import CallToolResult, TextContent
import pytest

from memento.gbrain import GBrainError
from memento.gbrain_mcp import GBrainMCP, _tool_value
import memento.gbrain_mcp as adapter


def brain(tmp_path, **kwargs):
    return GBrainMCP(
        ["gbrain"], tmp_path / "brain",
        endpoint="http://127.0.0.1:3131/mcp", token="synthetic-token", **kwargs,
    )


def tool_result(value, *, error=False):
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(value))],
        is_error=error,
    )


async def test_actual_sdk_initializes_and_decodes_sse(tmp_path, monkeypatch):
    """Exercise the installed SDK with an HTTP mock, not a mocked session."""
    requests = []

    async def respond(request):
        assert request.headers["authorization"] == "Bearer synthetic-token"
        payload = json.loads(request.content)
        requests.append(payload)
        if payload["method"] == "notifications/initialized":
            return httpx2.Response(202)
        if payload["method"] == "initialize":
            result = {
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "gbrain", "version": "0.59.0.0"},
            }
        elif payload["method"] == "tools/list":
            result = {"tools": [{
                "name": "get_page", "inputSchema": {"type": "object"},
            }]}
        else:
            assert payload["method"] == "tools/call", payload
            assert payload["params"]["name"] == "get_page"
            assert payload["params"]["arguments"] == {
                "slug": "events/flight", "source_id": "default",
                "include_content": True, "include_deleted": True,
            }
            result = {
                "content": [{"type": "text", "text": json.dumps({
                    "content": "---\ntitle: Flight\n---\nFlight body",
                    "revision": "r1", "source_id": "default",
                })}],
            }
        data = json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": result})
        return httpx2.Response(
            200, headers={"Content-Type": "text/event-stream"},
            content=f"event: message\ndata: {data}\n\n".encode(),
        )

    real_client = httpx2.AsyncClient
    monkeypatch.setattr(adapter.httpx2, "AsyncClient", lambda **kwargs: real_client(
        **kwargs, transport=httpx2.MockTransport(respond),
    ))
    result = await brain(tmp_path).get_page("events/flight")
    assert result["revision"] == "r1"
    assert requests[0]["method"] == "initialize"
    assert any(request["method"] == "tools/call" for request in requests)


async def test_pagination_uses_http_cap_and_retains_tombstones(tmp_path, monkeypatch):
    instance = brain(tmp_path, page_size=500)
    rows = [{"slug": f"notes/{i}", "source_id": "default"} for i in range(101)]
    rows[-1]["deleted_at"] = "2026-09-27T12:00:00Z"
    seen = []

    async def call(name, arguments):
        seen.append(arguments)
        assert name == "list_pages"
        assert arguments["limit"] == 100
        assert arguments["updated_after"] == "2026-09-27T11:59:59Z"
        assert arguments["source_id"] == "__all__"
        assert arguments["include_deleted"] is True
        offset = arguments["offset"]
        return rows[offset:offset + arguments["limit"]]

    monkeypatch.setattr(instance, "_call_tool", call)
    assert await instance.list_pages("2026-09-27T11:59:59Z") == rows
    assert [p["offset"] for p in seen] == [0, 100]


async def test_put_preserves_exact_cas_and_request_id(tmp_path, monkeypatch):
    instance = brain(tmp_path)
    calls = []

    async def call(name, arguments, *, request_id=None):
        calls.append((name, arguments, request_id))
        return {"state": "pending", "request_id": request_id}

    monkeypatch.setattr(instance, "_call_tool", call)
    result = await instance.put_page(
        "events/flight", "PRIVATE BODY", expected_revision="prior",
        request_id="fixed-uuid",
    )
    assert result["state"] == "pending"
    assert calls == [("put_page", {
        "slug": "events/flight", "source_id": "default", "content": "PRIVATE BODY",
        "expected_revision": "prior", "request_id": "fixed-uuid",
    }, "fixed-uuid")]
    await instance.put_page("events/new", "new", request_id="create-id")
    assert "expected_revision" not in calls[-1][1]
    assert "force" not in calls[-1][1]


def test_tool_error_retains_receipt_but_redacts_message():
    receipt = {"state": "conflict", "request_id": "fixed"}
    with pytest.raises(GBrainError) as caught:
        _tool_value("put_page", tool_result({
            "error": "revision_conflict", "message": "PRIVATE BODY",
            "write_request": receipt,
        }, error=True), "fixed")
    assert caught.value.receipt == receipt
    assert caught.value.request_id == "fixed"
    assert caught.value.code == "revision_conflict"
    assert "PRIVATE BODY" not in str(caught.value)


def test_unexpected_error_text_never_becomes_a_diagnostic():
    with pytest.raises(GBrainError, match="tool_error") as caught:
        _tool_value("get_page", tool_result({"error": "PRIVATE BODY"}, error=True))
    assert "PRIVATE BODY" not in str(caught.value)


def test_structured_result_and_advisory_text_are_supported():
    assert _tool_value("get_page", CallToolResult(
        content=[], structured_content={"revision": "r1"},
    )) == {"revision": "r1"}
    result = tool_result([{"slug": "a"}])
    result.content.append(TextContent(type="text", text="Advisory: not operation JSON"))
    assert _tool_value("list_pages", result) == [{"slug": "a"}]


async def test_transport_failure_keeps_mutation_identity(tmp_path, monkeypatch):
    @asynccontextmanager
    async def failure(*args, **kwargs):
        raise RuntimeError("PRIVATE BODY and synthetic-token")
        yield  # pragma: no cover

    monkeypatch.setattr(adapter, "streamable_http_client", failure)
    with pytest.raises(GBrainError) as caught:
        await brain(tmp_path).put_page("notes/a", "PRIVATE BODY", request_id="replay-id")
    assert caught.value.request_id == "replay-id"
    assert caught.value.code == "transport_error_outcome_unknown"
    assert "PRIVATE BODY" not in str(caught.value)
    assert "synthetic-token" not in str(caught.value)


@pytest.mark.parametrize("value", [{"pages": []}, ["not a page"]])
async def test_invalid_list_shape_fails_loudly(tmp_path, monkeypatch, value):
    instance = brain(tmp_path)

    async def call(*args, **kwargs):
        return value

    monkeypatch.setattr(instance, "_call_tool", call)
    with pytest.raises(GBrainError, match="unexpected_response"):
        await instance.list_pages()


def credentials(tmp_path, **overrides):
    path = tmp_path / "handoff.json"
    value = {
        "version": 1, "mcp_url": "http://127.0.0.1:3131/mcp",
        "issuer_url": "http://127.0.0.1:3131",
        "client_id": "id:with space", "client_secret": "secret&value",
        "access_token": "expired-token", "expires_at": time.time() - 10,
        "profile": "memory-writer", "source_id": "default",
        "shared_skills": {"follow": False, "source_ids": ["default", "google"]},
        **overrides,
    }
    path.write_text(json.dumps(value))
    return path, value


@pytest.mark.parametrize("method", ["client_secret_post", "client_secret_basic"])
async def test_credentials_refresh_atomically_preserves_grants(tmp_path, monkeypatch, method):
    path, original = credentials(tmp_path, token_endpoint_auth_method=method)
    seen = []

    async def respond(request):
        seen.append(request)
        assert str(request.url) == "http://127.0.0.1:3131/token"
        form = parse_qs(request.content.decode())
        assert form["grant_type"] == ["client_credentials"]
        if method == "client_secret_post":
            assert form["client_id"] == [original["client_id"]]
            assert form["client_secret"] == [original["client_secret"]]
            assert "authorization" not in request.headers
        else:
            assert set(form) == {"grant_type"}
            basic = request.headers["authorization"].split(" ")[1]
            assert base64.b64decode(basic).decode() == "id%3Awith+space:secret%26value"
        return httpx2.Response(200, json={"access_token": "renewed-token", "expires_in": 3600})

    real_client = httpx2.AsyncClient
    monkeypatch.setattr(adapter.httpx2, "AsyncClient", lambda **kwargs: real_client(
        **kwargs, transport=httpx2.MockTransport(respond),
    ))
    instance = brain(tmp_path, credentials_file=path)
    assert await instance._access_token() == "renewed-token"
    assert await instance._access_token() == "renewed-token"
    assert len(seen) == 1
    updated = json.loads(path.read_text())
    assert updated["shared_skills"] == original["shared_skills"]
    assert updated["client_secret"] == original["client_secret"]
    assert updated["expires_at"] > time.time() + 3500
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".memento-oauth-*"))


async def test_credentials_issuer_mismatch_never_sends_secret(tmp_path, monkeypatch):
    path, _ = credentials(tmp_path, issuer_url="https://other.example")
    with pytest.raises(GBrainError, match="invalid_credentials_file"):
        await brain(tmp_path, credentials_file=path)._access_token()


async def test_refresh_redirect_is_not_followed_and_failure_preserves_file(tmp_path, monkeypatch):
    path, _ = credentials(tmp_path)
    original = path.read_bytes()
    seen = []

    async def respond(request):
        seen.append(request)
        return httpx2.Response(307, headers={"Location": "https://other.example/token"})

    real_client = httpx2.AsyncClient
    monkeypatch.setattr(adapter.httpx2, "AsyncClient", lambda **kwargs: real_client(
        **kwargs, transport=httpx2.MockTransport(respond),
    ))
    with pytest.raises(GBrainError, match="token_refresh_failed") as caught:
        await brain(tmp_path, credentials_file=path)._access_token()
    assert len(seen) == 1
    assert path.read_bytes() == original
    assert "secret" not in str(caught.value)


async def test_valid_handoff_token_does_not_need_renewal(tmp_path):
    path, _ = credentials(tmp_path, access_token="current-token", expires_at=time.time() + 3600)
    assert await brain(tmp_path, credentials_file=path)._access_token() == "current-token"
