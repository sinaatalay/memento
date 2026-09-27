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


def render_memory(title: str, body: str, recipe: str, sources: list[str]) -> str:
    front = yaml.dump({"title": title, "sources": sources, "recipe": recipe}, Dumper=LiteralDumper, sort_keys=False, allow_unicode=True, width=78)
    return f"---\n{front}---\n\n{body.strip()}\n"


def parse_memory(markdown: str) -> dict:
    normalized = markdown.replace("\r\n", "\n")
    if not normalized.startswith("---\n"):
        return {"title": "Untitled memory", "body": normalized.strip(), "recipe": "", "sources": []}
    parts = normalized.split("\n---", 2)
    if len(parts) < 2:
        raise ValueError("memory front matter is missing its closing ---")
    metadata = yaml.safe_load(parts[0][4:]) or {}
    if not isinstance(metadata, dict):
        raise ValueError("memory front matter must be a mapping")
    recipe = metadata.get("recipe", "")
    if not isinstance(recipe, str):
        raise ValueError("recipe must be a Python source string")
    sources = metadata.get("sources", [])
    if isinstance(sources, str):
        sources = [sources]
    return {
        "title": str(metadata.get("title", "Untitled memory")),
        "body": "\n---".join(parts[1:]).strip(),
        "recipe": recipe,
        "sources": [str(x) for x in sources],
    }


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:24]


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:64] or "memory"
