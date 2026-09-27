"""GBrain, used as it is.

Every GBrain write goes through to a markdown file under the brain's content
root, so memento reads pages from disk: no database lock, no matter who owns
the brain. It writes with GBrain's own operations, `put_page` (with the page's
revision, so it never clobbers a concurrent edit) and `add_timeline_entry`.
Both reach a running `gbrain serve` if one owns the brain.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
import uuid
from dataclasses import dataclass
from pathlib import Path

import yaml

from .api import Page

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.S)
IGNORED = ("skills/", ".git/", ".obsidian/")  # GBrain's own skill pages are not memories


@dataclass
class Stored:
    """A page as GBrain stored it."""

    page: Page
    frontmatter: dict
    recipe: str  # "" if the page has none
    digest: str  # of the whole file: changes whenever the page does

    @property
    def body_digest(self) -> str:
        return hashlib.sha1(self.page.text.encode()).hexdigest()


def parse(slug: str, text: str, source: str = "default") -> Stored:
    match = _FRONTMATTER.match(text)
    frontmatter, body = {}, text
    if match:
        try:
            frontmatter = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            frontmatter = {}
        body = match.group(2)
    if not isinstance(frontmatter, dict):
        frontmatter = {}
    title = str(frontmatter.get("title") or "")
    if not title:
        heading = re.search(r"^#\s+(.+)$", body, re.M)
        title = heading.group(1).strip() if heading else slug.rsplit("/", 1)[-1]
    recipe = frontmatter.get("recipe") or ""
    page = Page(
        slug=slug,
        title=title,
        text=body.strip(),
        type=str(frontmatter.get("type") or ""),
        source=source,
    )
    return Stored(page, frontmatter, str(recipe), hashlib.sha1(text.encode()).hexdigest())


def with_recipe(markdown: str, recipe: str) -> str:
    """The same page with its `recipe:` replaced (or removed, if empty). Nothing else moves."""
    match = _FRONTMATTER.match(markdown)
    head, body = (match.group(1), match.group(2)) if match else ("", markdown)
    lines, kept, skipping = head.splitlines(), [], False
    for line in lines:
        if skipping and (not line.strip() or line[:1].isspace()):
            continue
        skipping = line.startswith("recipe:")
        if not skipping:
            kept.append(line)
    recipe = "\n".join(line.rstrip() for line in recipe.strip("\n").splitlines())
    if recipe.strip():
        kept += ["recipe: |"] + [f"  {line}" if line else "" for line in recipe.splitlines()]
    return "---\n" + "\n".join(kept) + "\n---\n" + body


def content_root() -> Path:
    if explicit := os.environ.get("MEMENTO_BRAIN_DIR"):
        return Path(explicit).expanduser()
    home = Path(os.environ.get("GBRAIN_HOME") or Path.home()).expanduser()
    roots = sorted((home / ".gbrain" / "content").glob("*/default"))
    if not roots:
        raise RuntimeError(
            f"no GBrain content root under {home}/.gbrain; set GBRAIN_HOME or MEMENTO_BRAIN_DIR"
        )
    return roots[0]


class GBrainError(RuntimeError):
    pass


class Brain:
    def __init__(self, root: Path | None = None, command: list[str] | None = None) -> None:
        self.root = root or content_root()
        self.command = command or shlex.split(os.environ.get("MEMENTO_GBRAIN", "gbrain"))
        self._lock = asyncio.Lock()

    # ---- reading, from disk ----

    def slugs(self) -> dict[str, Path]:
        pages = {}
        for path in self.root.rglob("*.md"):
            rel = path.relative_to(self.root).as_posix()
            if rel.startswith(IGNORED) or rel == "README.md":
                continue
            pages[rel[:-3]] = path
        return pages

    def read(self, slug: str) -> Stored | None:
        try:
            return parse(slug, (self.root / f"{slug}.md").read_text(errors="replace"))
        except FileNotFoundError:
            return None

    # ---- writing, through GBrain ----

    async def call(self, operation: str, params: dict) -> dict:
        async with self._lock:
            proc = await asyncio.create_subprocess_exec(
                *self.command,
                "call",
                operation,
                json.dumps(params),
                cwd=self.root,  # Bun reads a .env from the working directory; don't let it
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(), 120)
        try:
            result = json.loads(out)
        except json.JSONDecodeError:
            raise GBrainError(
                f"gbrain {operation}: {(err or out).decode(errors='replace')[-400:]}"
            ) from None
        if proc.returncode or (isinstance(result, dict) and result.get("error")):
            raise GBrainError(f"gbrain {operation}: {result.get('error', result)}")
        return result

    async def set_recipe(self, slug: str, recipe: str) -> None:
        """Replace one page's recipe, against the revision just read."""
        page = await self.call("get_page", {"slug": slug, "include_content": True})
        await self.call(
            "put_page",
            {
                "slug": slug,
                "content": with_recipe(page["content"], recipe),
                "expected_revision": page["revision"],
                "request_id": str(uuid.uuid4()),
            },
        )

    async def add_timeline(self, slug: str, date: str, summary: str, source: str | None) -> None:
        params = {"slug": slug, "date": date, "summary": summary, "request_id": str(uuid.uuid4())}
        if source:
            params["source"] = source
        await self.call("add_timeline_entry", params)
