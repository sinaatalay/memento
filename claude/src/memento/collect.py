"""Check a recipe and run it to collect its triggers.

A recipe runs in a subprocess with a timeout, restricted builtins, and only
`memento` importable. Every problem comes back as a RecipeError whose message
is meant to be read by the LLM that wrote the recipe.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

from .dsl import RecipeError
from .model import Recipe

# GBrain re-serializes frontmatter with js-yaml: a multi-line string stays a
# literal `|` block (clean Python on disk) only while every line is <= 78 chars.
# Longer lines still round-trip exactly, just folded on disk, so fmt() shortens
# what it can and lint() doesn't reject the rest.
MAX_COLS = 78
_STRING_LINE = re.compile(r'^(\s*)"([^"\\]*)"(,?)$')

_FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "open", "input", "globals", "locals", "vars", "getattr",
    "setattr", "delattr", "breakpoint", "help", "memoryview", "exit", "quit",
}  # fmt: skip

_RUNNER = r"""
import builtins, json, sys, traceback

SAFE = {n: getattr(builtins, n) for n in (
    "abs", "all", "any", "bool", "dict", "enumerate", "float", "int", "isinstance",
    "len", "list", "max", "min", "range", "round", "sorted", "str", "sum", "tuple",
    "zip", "print", "True", "False", "None", "ValueError", "Exception",
)}

def _import(name, globals=None, locals=None, fromlist=(), level=0):
    if name != "memento":
        raise ImportError(f"only `from memento import ...` is allowed, not {name!r}")
    return __import__(name, globals, locals, fromlist, level)

SAFE["__import__"] = _import
src = sys.stdin.read()
try:
    exec(compile(src, "recipe.py", "exec"), {"__name__": "recipe", "__builtins__": SAFE})
    from memento import dsl
    print(json.dumps({"ok": dsl._recipe.model_dump(mode="json")}))
except BaseException as e:
    frames = [f for f in traceback.extract_tb(e.__traceback__) if f.filename == "recipe.py"]
    where = f"line {frames[-1].lineno}: " if frames else ""
    print(json.dumps({"error": f"{where}{type(e).__name__}: {e}"}))
"""


def lint(source: str) -> list[str]:
    """Static problems: tabs, and anything besides the memento language."""
    problems = []
    if "\t" in source:
        problems.append("use spaces, not tabs")
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return [*problems, f"line {e.lineno}: syntax error: {e.msg}"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) or (isinstance(node, ast.ImportFrom) and node.module != "memento"):
            problems.append(f"line {node.lineno}: only `from memento import ...` is allowed")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            problems.append(f"line {node.lineno}: private attributes ({node.attr}) are not allowed")
        elif isinstance(node, ast.Name) and (node.id.startswith("__") or node.id in _FORBIDDEN_NAMES):
            problems.append(f"line {node.lineno}: `{node.id}` is not allowed in a recipe")
    return problems


def collect(source: str, *, memory: str, timeout: float = 10.0) -> Recipe:
    """Validate and run `source`; return its triggers, with ids `<memory>#<n>-<content hash>`."""
    problems = lint(source)
    if problems:
        raise RecipeError("; ".join(problems))
    try:
        done = subprocess.run(
            [sys.executable, "-c", _RUNNER], input=source, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as e:
        raise RecipeError(f"the recipe ran longer than {timeout:.0f}s") from e
    results = [line for line in done.stdout.splitlines() if line.startswith("{")]
    if not results:
        raise RecipeError(f"the recipe crashed: {done.stderr.strip()[-400:]}")
    result = json.loads(results[-1])
    if "error" in result:
        raise RecipeError(result["error"])
    recipe = Recipe.model_validate(result["ok"])
    if not recipe.triggers:
        raise RecipeError("the recipe declares no triggers; add remind(...) or on(...)")
    for n, trigger in enumerate(recipe.triggers):
        # Content-derived, so a rewritten trigger is a new trigger (its own fired state),
        # while an unchanged one keeps its identity across re-registration.
        digest = hashlib.sha1(trigger.model_dump_json(exclude={"id"}).encode()).hexdigest()[:6]
        trigger.id = f"{memory}#{n}-{digest}"
    return recipe


def _split_long_strings(source: str) -> str:
    """Split a long string literal that sits alone on its line into adjacent literals."""
    lines = []
    for line in source.splitlines():
        match = _STRING_LINE.match(line)
        if len(line) <= MAX_COLS or not match:
            lines.append(line)
            continue
        indent, text, comma = match.groups()
        width = MAX_COLS - len(indent) - 3
        chunks, current = [], ""
        for word in text.split(" "):
            if current and len(current) + 1 + len(word) + 1 > width:
                chunks.append(current + " ")
                current = word
            else:
                current = f"{current} {word}" if current else word
        chunks.append(current)
        lines += [f'{indent}"{c}"' for c in chunks[:-1]] + [f'{indent}"{chunks[-1]}"{comma}']
    return "\n".join(lines) + ("\n" if source.endswith("\n") else "")


def fmt(source: str) -> str:
    """Format with ruff at MAX_COLS so the recipe stays clean Python inside GBrain."""
    ruff = Path(sys.executable).parent / "ruff"
    try:
        done = subprocess.run(
            [str(ruff), "format", f"--line-length={MAX_COLS}", "-"],
            input=source,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return source
    formatted = done.stdout if done.returncode == 0 and done.stdout.strip() else source
    return _split_long_strings(_pack_imports(formatted))


def _pack_imports(source: str) -> str:
    """Replace `from memento import (...)` blocks with as few <= MAX_COLS lines as fit."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return source
    imports = [n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "memento"]
    if not imports or any(a.asname for n in imports for a in n.names):
        return source
    names = list(dict.fromkeys(a.name for n in imports for a in n.names))
    packed, line = [], "from memento import "
    for name in names:
        piece = name if line.endswith("import ") else f", {name}"
        if len(line) + len(piece) > MAX_COLS:
            packed.append(line)
            line, piece = "from memento import ", name
        line += piece
    packed.append(line)
    lines = source.splitlines()
    drop = {i for n in imports for i in range(n.lineno - 1, n.end_lineno)}
    first = imports[0].lineno - 1
    out = [x for i, x in enumerate(lines[:first]) if i not in drop] + packed
    out += [x for i, x in enumerate(lines[first:], first) if i not in drop]
    return "\n".join(out) + ("\n" if source.endswith("\n") else "")
