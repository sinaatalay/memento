"""Page access through the same local GBrain owner used by regular assistants.

The official MCP SDK handles initialization and Streamable HTTP/SSE. The inherited
CLI is used only for connector helpers and ``sync``, which delegates to the live
serve owner upstream. Do not run other DB-opening CLI commands alongside serve.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Sequence
from urllib.parse import quote_plus, urlsplit
from uuid import uuid4

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult

from .gbrain import GBrain, GBrainError


def _safe_code(value: Any, fallback: str) -> str:
    return value if isinstance(value, str) and re.fullmatch(
        r"[a-z][a-z0-9_\-]{0,79}", value
    ) else fallback


def _tool_value(
    operation: str, result: Any, request_id: str | None = None,
) -> Any:
    """Decode GBrain's JSON text block without exposing free-text diagnostics."""
    if not isinstance(result, CallToolResult):
        raise GBrainError(operation, "unexpected_response", request_id=request_id)
    value = result.structured_content
    if value is None:
        blocks = [block for block in result.content if block.type == "text"]
        if not blocks:
            raise GBrainError(operation, "unexpected_response", request_id=request_id)
        # Upstream returns its operation envelope first; advisory blocks may follow.
        try:
            value = json.loads(blocks[0].text)
        except (TypeError, json.JSONDecodeError):
            raise GBrainError(operation, "invalid_json", request_id=request_id) from None
    if result.is_error or isinstance(value, dict) and value.get("error"):
        code = _safe_code(value.get("error"), "tool_error") if isinstance(value, dict) else "tool_error"
        receipt = value.get("write_request") if isinstance(value, dict) else None
        raise GBrainError(
            operation, code, request_id=request_id,
            receipt=receipt if isinstance(receipt, dict) else None,
        )
    return value


class GBrainMCP(GBrain):
    """GBrain-compatible adapter sharing one HTTP serve process.

    The token must grant the configured write source and every explicit read
    source (normally ``default`` + ``google``). ``__all__`` means granted sources,
    not an auth bypass. Each operation opens/closes its own SDK session, avoiding
    cross-task AnyIO cancel-scope ownership and stale sessions after restart.
    """

    def __init__(
        self,
        command: Sequence[str],
        home: str | Path,
        *,
        endpoint: str,
        token: str | None = None,
        credentials_file: str | Path | None = None,
        source: str = "default",
        google_source: str = "google",
        timeout: float = 120,
        page_size: int = 100,
    ) -> None:
        parsed = urlsplit(endpoint)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("MCP endpoint must be an HTTP(S) URL without credentials")
        if not (token and token.strip()) and credentials_file is None:
            raise ValueError("MCP bearer token or credentials_file is required")
        super().__init__(
            command, home, source, timeout, google_source=google_source,
            page_size=min(page_size, 100),
        )
        self.endpoint = endpoint
        self._token = token.strip() if token else None
        self.credentials_file = Path(credentials_file).expanduser().resolve() if credentials_file else None

    async def _access_token(self) -> str:
        """Renew a private GBrain machine handoff, keeping all grant metadata."""
        if self.credentials_file is None:
            assert self._token is not None
            return self._token
        try:
            credentials = json.loads(self.credentials_file.read_text())
            if not isinstance(credentials, dict) or credentials.get("version") != 1:
                raise ValueError("invalid handoff")
            if credentials.get("mcp_url", "").rstrip("/") != self.endpoint.rstrip("/"):
                raise ValueError("endpoint mismatch")
            issuer = credentials.get("issuer_url")
            if not isinstance(issuer, str):
                raise ValueError("missing issuer")
            endpoint_parts, issuer_parts = urlsplit(self.endpoint), urlsplit(issuer)
            if (
                (endpoint_parts.scheme, endpoint_parts.hostname, endpoint_parts.port)
                != (issuer_parts.scheme, issuer_parts.hostname, issuer_parts.port)
                or issuer_parts.username or issuer_parts.password
                or issuer_parts.query or issuer_parts.fragment
            ):
                raise ValueError("issuer origin mismatch")
            expires = credentials.get("expires_at")
            if expires is not None and (
                not isinstance(expires, (int, float)) or not math.isfinite(expires)
            ):
                raise ValueError("invalid expiration")
            token = credentials.get("access_token")
            if isinstance(token, str) and token and (expires is None or expires > time.time() + 30):
                return token
            client_id, secret = credentials.get("client_id"), credentials.get("client_secret")
            if not isinstance(client_id, str) or not client_id or not isinstance(secret, str) or not secret:
                raise ValueError("renewable credentials missing")
            method = credentials.get("token_endpoint_auth_method", "client_secret_post")
            if method not in {"client_secret_post", "client_secret_basic"}:
                raise ValueError("unsupported authentication method")
        except (OSError, ValueError, TypeError, AttributeError):
            raise GBrainError("oauth", "invalid_credentials_file") from None

        form = {"grant_type": "client_credentials"}
        auth = None
        if method == "client_secret_basic":
            auth = httpx2.BasicAuth(quote_plus(client_id), quote_plus(secret))
        else:
            form.update(client_id=client_id, client_secret=secret)
        try:
            async with httpx2.AsyncClient(
                timeout=self.timeout, trust_env=False, follow_redirects=False,
            ) as client:
                response = await client.post(
                    issuer.rstrip("/") + "/token", data=form, auth=auth,
                )
                response.raise_for_status()
                renewed = response.json()
            token = renewed.get("access_token")
            lifetime = renewed.get("expires_in")
            if (
                not isinstance(token, str) or not token
                or not isinstance(lifetime, (int, float))
                or not math.isfinite(lifetime) or lifetime <= 0
            ):
                raise ValueError("invalid token response")
        except Exception:
            raise GBrainError("oauth", "token_refresh_failed") from None
        credentials.update(access_token=token, expires_at=time.time() + lifetime)
        temporary: str | None = None
        try:
            descriptor, temporary = tempfile.mkstemp(
                prefix=".memento-oauth-", dir=self.credentials_file.parent,
            )
            with os.fdopen(descriptor, "w") as handle:
                os.fchmod(handle.fileno(), 0o600)
                json.dump(credentials, handle)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.credentials_file)
        except OSError:
            raise GBrainError("oauth", "credentials_write_failed") from None
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
        return token

    async def _call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        request_id: str | None = None,
    ) -> Any:
        try:
            async with asyncio.timeout(self.timeout):
                token = await self._access_token()
                async with httpx2.AsyncClient(
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=self.timeout, trust_env=False,
                ) as client:
                    async with streamable_http_client(
                        self.endpoint, http_client=client,
                    ) as (read, write):
                        async with ClientSession(
                            read, write, read_timeout_seconds=self.timeout,
                        ) as session:
                            await session.initialize()
                            result = await session.call_tool(name, arguments)
            return _tool_value(name, result, request_id)
        except GBrainError:
            raise
        except TimeoutError:
            raise GBrainError(
                name, "timeout_outcome_unknown", request_id=request_id,
            ) from None
        except Exception:
            # SDK exceptions can contain protocol/server free text. A failed
            # mutation's request UUID stays available for identical replay.
            raise GBrainError(
                name, "transport_error_outcome_unknown", request_id=request_id,
            ) from None

    async def list_pages(
        self,
        updated_after: str | None = None,
        source: str = "__all__",
        include_deleted: bool = True,
    ) -> list[dict[str, Any]]:
        pages: list[dict[str, Any]] = []
        async with self._serialized():
            while True:
                arguments: dict[str, Any] = {
                    "source_id": source, "include_deleted": include_deleted,
                    "sort": "updated_asc", "limit": self.page_size,
                    "offset": len(pages),
                }
                if updated_after is not None:
                    arguments["updated_after"] = updated_after
                batch = await self._call_tool("list_pages", arguments)
                if not isinstance(batch, list) or any(not isinstance(row, dict) for row in batch):
                    raise GBrainError("list_pages", "unexpected_response")
                pages.extend(batch)
                if len(batch) < self.page_size:
                    return pages

    async def get_page(
        self, slug: str, source: str | None = None,
    ) -> dict[str, Any]:
        async with self._serialized():
            result = await self._call_tool("get_page", {
                "slug": slug, "source_id": source or self.source,
                "include_content": True, "include_deleted": True,
            })
            if not isinstance(result, dict):
                raise GBrainError("get_page", "unexpected_response")
            return result

    async def put_page(
        self,
        slug: str,
        markdown: str,
        source: str | None = None,
        expected_revision: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        request_id = request_id or str(uuid4())
        arguments: dict[str, Any] = {
            "slug": slug, "source_id": source or self.source,
            "content": markdown, "request_id": request_id,
        }
        if expected_revision is not None:
            arguments["expected_revision"] = expected_revision
        async with self._serialized():
            result = await self._call_tool("put_page", arguments, request_id=request_id)
            if not isinstance(result, dict):
                raise GBrainError("put_page", "unexpected_response", request_id=request_id)
            return result
