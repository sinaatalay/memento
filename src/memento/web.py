"""fetch(): a recipe reads a web page or a JSON API, as a page Jev can judge.

Only public http(s) addresses; responses are capped, HTML becomes text, and
JSON keeps its fields but drops the URL noise APIs like GitHub's are full of.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import socket
import urllib.parse
import urllib.request

from .api import Page, RecipeError

MAX_BYTES = 3_000_000
MAX_TEXT = 12_000


def _public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    return all(ipaddress.ip_address(info[4][0]).is_global for info in infos)


def _json_text(value, depth: int = 0) -> str:
    """Readable JSON without *_url fields, ids and avatars."""
    pad = "  " * depth
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            if key.endswith("url") or key in ("node_id", "id", "reactions", "avatar"):
                continue
            if isinstance(item, (dict, list)):
                if item:
                    lines.append(f"{pad}{key}:\n{_json_text(item, depth + 1)}")
            elif item not in (None, "", False):
                lines.append(f"{pad}{key}: {item}")
        return "\n".join(lines)
    if isinstance(value, list):
        return "\n".join(_json_text(item, depth) for item in value[:30])
    return f"{pad}{value}"


def _html_text(html: str) -> tuple[str, str]:
    title = re.search(r"(?is)<title[^>]*>(.*?)</title>", html)
    html = re.sub(r"(?is)<(script|style|noscript|svg)\b.*?</\1>", " ", html)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;|&#160;", " ", text).replace("&amp;", "&")
    return (title.group(1).strip() if title else ""), re.sub(r"\s+", " ", text).strip()


def fetch(url: str) -> Page:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise RecipeError(f"fetch(): {url!r} is not an http(s) URL")
    if not _public(parts.hostname):
        raise RecipeError(f"fetch(): {parts.hostname} is not a public address")
    headers = {
        "User-Agent": "memento (+https://github.com/sinaatalay/memento)",
        "Cache-Control": "no-cache",  # a check right after a change must see the change
    }
    if parts.hostname == "api.github.com" and (token := os.environ.get("GITHUB_TOKEN")):
        headers["Authorization"] = f"Bearer {token}"  # fresher, and 5,000 checks an hour
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read(MAX_BYTES).decode(errors="replace")
            kind = response.headers.get("Content-Type", "")
    except Exception as e:
        raise RecipeError(f"fetch({url}): {e}") from None
    if "json" in kind or body.lstrip()[:1] in "[{":
        try:
            data = json.loads(body)
            title = str(data.get("title", "")) if isinstance(data, dict) else ""
            return Page(slug=url, title=title or url, text=_json_text(data)[:MAX_TEXT], type="web")
        except ValueError:
            pass
    title, text = _html_text(body)
    return Page(slug=url, title=title or url, text=text[:MAX_TEXT], type="web")
