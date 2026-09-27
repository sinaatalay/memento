"""Push a notification to the user: native macOS notifications."""

from __future__ import annotations

import asyncio
import json


def _quote(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)  # a valid AppleScript string literal


async def notify(title: str, text: str) -> None:
    script = f"display notification {_quote(text)} with title \"memento\" subtitle {_quote(title)} sound name \"Glass\""
    try:
        proc = await asyncio.create_subprocess_exec(
            "osascript", "-e", script, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await asyncio.wait_for(proc.wait(), 5)
    except (OSError, TimeoutError):
        pass
