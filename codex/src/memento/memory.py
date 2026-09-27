"""Round-trip a memory's readable body and executable recipe together."""
from __future__ import annotations

import hashlib
import re
import yaml


class LiteralDumper(yaml.SafeDumper):
    pass


def _string(dumper, value):
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|" if "\n" in value else None)


LiteralDumper.add_representer(str, _string)


def render_memory(title: str, body: str, recipe: str, sources: list[str], *, original_markdown: str | None = None) -> str:
    # A recipe extends an ordinary page. Updating it must not discard tags,
    # type, ownership, or other metadata maintained by another agent.
    metadata, _ = split_markdown(original_markdown) if original_markdown else ({}, "")
    metadata.update({"title": title, "sources": sources, "recipe": recipe})
    front = yaml.dump(metadata, Dumper=LiteralDumper, sort_keys=False, allow_unicode=True, width=78)
    return f"---\n{front}---\n\n{body.strip()}\n"


def split_markdown(markdown: str) -> tuple[dict, str]:
    normalized = markdown.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return {}, normalized.strip()
    match = re.fullmatch(r"---\n(.*?)\n---[ \t]*(?:\n|$)(.*)", normalized, re.DOTALL)
    if not match:
        raise ValueError("memory front matter is missing its closing ---")
    metadata = yaml.safe_load(match.group(1)) or {}
    if not isinstance(metadata, dict):
        raise ValueError("memory front matter must be a mapping")
    return metadata, match.group(2).strip()


def parse_memory(markdown: str) -> dict:
    metadata, body = split_markdown(markdown)
    recipe = metadata.get("recipe", "")
    if recipe is None:
        recipe = ""
    if not isinstance(recipe, str):
        raise ValueError("recipe must be a Python source string")
    sources = metadata.get("sources") or []
    if isinstance(sources, str):
        sources = [sources]
    if not isinstance(sources, list):
        raise ValueError("sources must be a list of source IDs")
    heading = re.search(r"^#\s+(.+?)\s*#*\s*$", body, re.MULTILINE)
    return {
        "title": str(metadata.get("title") or (heading.group(1) if heading else "Untitled memory")),
        "body": body,
        "recipe": recipe,
        "sources": [str(x) for x in sources],
    }


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:64] or "memory"
