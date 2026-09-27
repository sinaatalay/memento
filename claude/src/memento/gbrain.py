"""GBrain, used as-is.

A memory is any ordinary GBrain page. Memento adds one optional frontmatter
field, `recipe:`. Pages are read from the sources' markdown files (GBrain's
source of truth) and written back with `gbrain put`, which writes through to
the same files. Gmail threads and calendar events arrive as pages that GBrain's
Google connector writes to disk. Reading from disk means the runtime never needs
GBrain's single-writer database while a sync runs.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path("/Users/sina/memento-gbrain")
GB = ROOT / "gb"
GOOGLE_SOURCE = "gmail-sinajunks"
GOOGLE_DIR = ROOT / "home/.gbrain/clones/gmail-sinajunks-google"
MEMORY_SOURCE = "mem"  # where Memento writes the memories it creates from email
MEMORY_PREFIX = "memories/"


@dataclass(frozen=True)
class Source:
    id: str
    root: Path


def _default_root() -> Path:
    roots = sorted((ROOT / "home/.gbrain/content").glob("*/default"))
    return roots[0] if roots else ROOT / "home/.gbrain/content/default"


# Sources whose pages may carry recipes: ours, and GBrain's default brain,
# where agents connected over MCP write their pages.
SOURCES = [Source(MEMORY_SOURCE, ROOT / "memories"), Source("default", _default_root())]

_lock = asyncio.Lock()  # PGLite allows one writer at a time
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?(.*)\Z", re.S)


async def gb(*args: str, stdin: str | None = None, timeout: float = 300) -> tuple[int, str]:
    async with _lock:
        proc = await asyncio.create_subprocess_exec(
            str(GB),
            *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=ROOT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(stdin.encode() if stdin is not None else None), timeout)
        except TimeoutError:
            proc.kill()
            return 1, f"gbrain {' '.join(args)} timed out"
        return proc.returncode or 0, out.decode(errors="replace")


async def sync_google() -> tuple[int, str]:
    return await gb("sync", "--source", GOOGLE_SOURCE, timeout=240)


async def put_page(source: str, slug: str, markdown: str) -> None:
    """Write a whole page. Memento is the only writer of the fields it changes, so last write wins."""
    code, out = await gb("put", slug, "--source", source, "--force", stdin=markdown, timeout=60)
    if code != 0 or '"write_error"' in out:
        raise RuntimeError(f"gbrain put {slug} failed: {out[-800:]}")


def read_page(path: Path) -> tuple[dict, str]:
    text = path.read_text(errors="replace")
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text.strip()
    try:
        return yaml.safe_load(match.group(1)) or {}, match.group(2).strip()
    except yaml.YAMLError:
        return {}, match.group(2).strip()


def slug_of(path: Path, root: Path) -> str:
    return str(path.relative_to(root).with_suffix(""))


def page_markdown(frontmatter: dict, body: str) -> str:
    """Serialize a page, keeping every existing field and putting the recipe last as a literal block."""
    fields = {k: v for k, v in frontmatter.items() if k != "recipe" and v is not None}
    lines = ["---"]
    if fields:
        lines.append(yaml.safe_dump(fields, sort_keys=False, allow_unicode=True, width=10_000).rstrip())
    recipe = str(frontmatter.get("recipe") or "").rstrip()
    if recipe:
        lines.append("recipe: |")
        lines += [f"  {line}" if line.strip() else "" for line in recipe.splitlines()]
    lines += ["---", "", body.strip(), ""]
    return "\n".join(lines)


def memory_markdown(title: str, body: str, recipe: str, sources: list[str]) -> str:
    """A memory Memento writes from an email or event."""
    fm = {"type": "note", "title": title, "memento": True}
    if sources:
        fm["sources"] = sources
    fm["recipe"] = recipe
    return page_markdown(fm, f"# {title}\n\n{body.strip()}")


def slugify(title: str, run: str = "") -> str:
    words = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return MEMORY_PREFIX + run + (words[:60].rstrip("-") or "memory")
