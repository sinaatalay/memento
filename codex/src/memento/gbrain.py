"""Serialized, source-aware access to an unmodified local GBrain CLI.

PGLite has a single owner. Never run ``gbrain serve`` against this installation
while Memento owns it. The file lock also serializes separate Memento processes.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import fcntl
import json
import os
from pathlib import Path
from typing import Any, AsyncIterator, Sequence
from uuid import uuid4


class GBrainError(RuntimeError):
    """A safe diagnostic that does not echo page bodies or credential output."""

    def __init__(
        self,
        operation: str,
        code: str,
        *,
        receipt: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        self.operation = operation
        self.code = code
        self.receipt = receipt
        self.request_id = request_id
        suffix = f" (request {request_id})" if request_id else ""
        super().__init__(f"GBrain {operation}: {code}{suffix}")


class GBrain:
    def __init__(
        self,
        command: Sequence[str],
        home: str | Path,
        source: str = "default",
        timeout: float = 120,
        *,
        google_source: str = "google",
        page_size: int = 500,
    ) -> None:
        if isinstance(command, str) or not command:
            raise ValueError("command must be a nonempty argument list")
        if timeout <= 0 or page_size <= 0:
            raise ValueError("timeout and page_size must be positive")
        self.command = tuple(command)
        self.home = Path(home).expanduser().resolve()
        self.source = source
        self.google_source = google_source
        self.timeout = timeout
        self.page_size = page_size
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def _serialized(self) -> AsyncIterator[None]:
        """Keep a paginated snapshot stable against other Memento CLI work."""
        self.home.mkdir(parents=True, exist_ok=True)
        async with self._lock:
            lockfile = self.home / ".memento-cli.lock"
            with lockfile.open("a") as handle:
                deadline = asyncio.get_running_loop().time() + self.timeout
                while True:
                    try:
                        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if asyncio.get_running_loop().time() >= deadline:
                            raise GBrainError("lock", "timeout")
                        await asyncio.sleep(0.05)
                try:
                    yield
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    async def _execute(
        self,
        args: Sequence[str],
        *,
        stdin: str | None = None,
        request_id: str | None = None,
    ) -> Any:
        """Called with the serialization lock held; content travels on stdin."""
        # An inherited DATABASE_URL overrides even a PGLite config upstream.
        # This adapter owns a local isolated brain; never retarget it through
        # unrelated shell settings or give its optional enrichment an API key.
        env = {
            key: value for key, value in os.environ.items()
            if not key.startswith("GBRAIN_")
            and not key.endswith("_API_KEY")
            and key != "DATABASE_URL"
        }
        env["GBRAIN_HOME"] = str(self.home)
        try:
            process = await asyncio.create_subprocess_exec(
                *self.command,
                *args,
                cwd=self.home,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise GBrainError(args[0], "executable_unavailable") from error
        try:
            stdout, _stderr = await asyncio.wait_for(
                process.communicate(None if stdin is None else stdin.encode()),
                timeout=self.timeout,
            )
        except (TimeoutError, asyncio.CancelledError) as error:
            # Do not release the PGLite ownership lock while a child is alive.
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.communicate(), timeout=5)
                except TimeoutError:
                    process.kill()
                    await process.communicate()
            if isinstance(error, asyncio.CancelledError):
                raise
            raise GBrainError(
                args[0], "timeout_outcome_unknown", request_id=request_id
            ) from error
        try:
            result = json.loads(stdout)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise GBrainError(
                args[0],
                f"invalid_json_exit_{process.returncode}",
                request_id=request_id,
            ) from error
        if process.returncode or isinstance(result, dict) and result.get("error"):
            code = result.get("error", f"exit_{process.returncode}") if isinstance(result, dict) else f"exit_{process.returncode}"
            raise GBrainError(
                args[0], str(code),
                receipt=result.get("write_request") if isinstance(result, dict) else None,
                request_id=request_id,
            )
        return result

    async def sync(self, source: str | None = None) -> dict[str, Any]:
        """Sync one explicit source; never embed or invoke GBrain enrichment."""
        async with self._serialized():
            return await self._execute([
                "sync", "--source", source or self.google_source,
                "--no-embed", "--no-extract", "--no-pull", "--json",
            ])

    async def list_pages(
        self,
        updated_after: str | None = None,
        source: str = "__all__",
        include_deleted: bool = True,
    ) -> list[dict[str, Any]]:
        """Enumerate all pages, retaining tombstones and equal-time rows.

        GBrain's timestamp comparison is strict ``>``. The runtime should overlap
        its cursor and deduplicate by (source_id, slug, updated_at), so a write at
        the cursor timestamp is not lost. Pagination keeps one fixed filter.
        """
        pages: list[dict[str, Any]] = []
        async with self._serialized():
            while True:
                params: dict[str, Any] = {
                    "source_id": source,
                    "include_deleted": include_deleted,
                    "sort": "updated_asc",
                    "limit": self.page_size,
                    "offset": len(pages),
                }
                if updated_after is not None:
                    params["updated_after"] = updated_after
                # Upstream `list --json` currently emits TSV. `call` emits JSON.
                batch = await self._execute([
                    "call", "list_pages", json.dumps(params),
                ])
                if not isinstance(batch, list):
                    raise GBrainError("list_pages", "unexpected_response")
                pages.extend(batch)
                if len(batch) < self.page_size:
                    return pages

    async def get_page(
        self, slug: str, source: str | None = None,
    ) -> dict[str, Any]:
        async with self._serialized():
            return await self._execute([
                "get", slug, "--source-id", source or self.source,
                "--include-content", "true", "--include-deleted", "true",
                "--json",
            ])

    async def put_page(
        self,
        slug: str,
        markdown: str,
        source: str | None = None,
        expected_revision: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """Create or conditionally replace canonical Markdown.

        Persist ``request_id`` with the intent before calling this method. After
        an uncertain result, replay the identical arguments and UUID. Omitting
        expected_revision is create-only; this adapter never force-overwrites.
        A queued/pending receipt is returned honestly, never as committed.
        """
        request_id = request_id or str(uuid4())
        args = [
            "put", slug, "--source-id", source or self.source,
            "--request-id", request_id, "--json",
        ]
        if expected_revision is not None:
            args.extend(["--expected-revision", expected_revision])
        async with self._serialized():
            return await self._execute(args, stdin=markdown, request_id=request_id)
