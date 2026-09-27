"""Deliver a notification: a macOS banner, and a phone push if MEMENTO_NTFY is set.

MEMENTO_NTFY is an ntfy topic URL (https://ntfy.sh/<topic>, or a self-hosted
server). Install the ntfy app and subscribe to the topic to get the pushes.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.parse
import urllib.request


def _applescript(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)  # a JSON string is a valid AppleScript literal


async def banner(title: str, text: str) -> None:
    if sys.platform != "darwin":
        return
    script = (
        f"display notification {_applescript(text)} with title \"memento\" "
        f"subtitle {_applescript(title)} sound name \"Glass\""
    )
    proc = await asyncio.create_subprocess_exec(
        "osascript", "-e", script, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    await asyncio.wait_for(proc.wait(), 10)


def push_request(url: str, title: str, text: str) -> urllib.request.Request:
    """An ntfy JSON publish (UTF-8 safe) for the topic URL https://host/<topic>."""
    parts = urllib.parse.urlsplit(url)
    body = {"topic": parts.path.strip("/"), "title": title, "message": text, "tags": ["brain"]}
    return urllib.request.Request(
        f"{parts.scheme}://{parts.netloc}/",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )


def _push(url: str, title: str, text: str) -> None:
    urllib.request.urlopen(push_request(url, title, text), timeout=10).read()


async def send(title: str, text: str) -> None:
    jobs = [banner(title, text)]
    if url := os.environ.get("MEMENTO_NTFY"):
        jobs.append(asyncio.to_thread(_push, url, title, text))
    await asyncio.gather(*jobs, return_exceptions=True)
