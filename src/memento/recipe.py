"""Load a recipe into triggers, and run a trigger's handler into effects.

Recipes are written by models from untrusted input (a forwarded email can say
anything), so they run with a small allow-list: only `from memento import`,
no private names or attributes, no loops without an end, restricted builtins.
Every error is a RecipeError whose message is meant for the recipe's author.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from . import api
from .api import Context, Effect, Page, Question, RecipeError, Trigger

MAX_RANGE = 10_000
MAX_STEPS = 200_000  # lines a recipe may execute per load or handler call

_BANNED_NAMES = {
    "eval", "exec", "compile", "open", "input", "globals", "locals", "vars", "getattr",
    "setattr", "delattr", "breakpoint", "help", "memoryview", "exit", "quit", "type",
    "object", "super", "classmethod", "staticmethod", "property",
}  # fmt: skip
_BANNED_NODES = {
    ast.While: "while loops",
    ast.ClassDef: "classes",
    ast.AsyncFunctionDef: "async functions",
    ast.Await: "await",
    ast.Yield: "generators",
    ast.YieldFrom: "generators",
    ast.Global: "global",
    ast.Nonlocal: "nonlocal",
}
_BANNED_ATTRIBUTES = {"format", "format_map", "mro"}


def _range(*args: int) -> range:
    r = range(*args)
    if len(r) > MAX_RANGE:
        raise RecipeError(f"range() is limited to {MAX_RANGE} steps in a recipe")
    return r


# What `from memento import ...` sees inside a recipe: the language, nothing else.
_LANGUAGE = SimpleNamespace(__all__=list(api.__all__), **{n: getattr(api, n) for n in api.__all__})


# Stdlib internals import these lazily (e.g. strftime imports `time`); lint()
# already stops a recipe from writing any import but `from memento import`.
_INTERNAL_IMPORTS = {"time", "_strptime", "datetime", "calendar", "locale", "zoneinfo", "encodings"}


def _import(name, globals=None, locals=None, fromlist=(), level=0):
    if name in _INTERNAL_IMPORTS and not level:
        return builtins.__import__(name, globals, locals, fromlist, level)
    if name != "memento" or level:
        raise RecipeError(f"only `from memento import ...` is allowed, not {name!r}")
    missing = [n for n in fromlist or () if n != "*" and n not in api.__all__]
    if missing:
        raise RecipeError(f"memento has no {missing[0]!r}; it offers: {', '.join(api.__all__)}")
    return _LANGUAGE


_SAFE_BUILTINS: dict[str, Any] = {
    name: getattr(builtins, name)
    for name in (
        "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "int",
        "isinstance", "len", "list", "map", "max", "min", "reversed", "round", "set",
        "sorted", "str", "sum", "tuple", "zip", "True", "False", "None",
        "Exception", "ValueError",
    )
}  # fmt: skip
_SAFE_BUILTINS |= {"range": _range, "print": lambda *a, **k: None, "__import__": _import}


def _is_fetch(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "fetch"


def _literal_url(call: ast.Call) -> bool:
    arg = call.args[0] if len(call.args) == 1 and not call.keywords else None
    return (
        isinstance(arg, ast.Constant)
        and isinstance(arg.value, str)
        and arg.value.startswith(("https://", "http://"))
    )


def lint(source: str) -> list[str]:
    """Static problems, as `line N: ...` strings. Empty means the recipe may run."""
    if "\t" in source:
        return ["use spaces, not tabs"]
    try:
        tree = ast.parse(source, "recipe.py")
    except SyntaxError as e:
        return [f"line {e.lineno}: syntax error: {e.msg}"]
    problems = []
    bound = set(_SAFE_BUILTINS)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.Lambda)):
            bound.update(a.arg for a in ast.walk(node.args) if isinstance(a, ast.arg))
            bound.add(getattr(node, "name", ""))
        elif isinstance(node, ast.ImportFrom):
            bound.update((a.asname or a.name) for a in node.names)
            if any(a.name == "*" for a in node.names):
                bound.update(api.__all__)
        elif isinstance(node, ast.MatchAs | ast.MatchStar) and node.name:
            bound.add(node.name)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id not in bound:
            hint = " (import it from memento)" if node.id in api.__all__ else ""
            problems.append(f"line {node.lineno}: `{node.id}` is not defined{hint}")
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import) or (
            isinstance(node, ast.ImportFrom) and node.module != "memento"
        ):
            problems.append(f"line {line}: only `from memento import ...` is allowed")
        elif type(node) in _BANNED_NODES:
            problems.append(f"line {line}: {_BANNED_NODES[type(node)]} are not allowed in a recipe")
        elif isinstance(node, ast.Attribute) and (
            node.attr.startswith("_") or node.attr in _BANNED_ATTRIBUTES
        ):
            problems.append(f"line {line}: `.{node.attr}` is not allowed in a recipe")
        elif _is_fetch(node) and not _literal_url(node):
            problems.append(
                f'line {line}: fetch() takes a literal "https://..." URL, never one built from data'
            )
        elif isinstance(node, ast.Name) and (node.id.startswith("_") or node.id in _BANNED_NAMES):
            problems.append(f"line {line}: `{node.id}` is not allowed in a recipe")
    return problems


class _Budget:
    """Counts the lines a recipe executes, and stops it past MAX_STEPS."""

    def __init__(self) -> None:
        self.steps = 0
        self.previous = None

    def __enter__(self) -> None:
        self.previous = sys.gettrace()
        sys.settrace(self._call)

    def __exit__(self, *exc) -> None:
        sys.settrace(self.previous)

    def _call(self, frame, event, arg):
        return self._line if frame.f_code.co_filename == "recipe.py" else None

    def _line(self, frame, event, arg):
        if event == "line":
            self.steps += 1
            if self.steps > MAX_STEPS:
                raise RecipeError(f"the recipe ran more than {MAX_STEPS:,} steps")
        return self._line


@dataclass
class Recipe:
    """A loaded recipe: its triggers, each with a stable key."""

    source: str
    triggers: dict[str, Trigger]

    @property
    def watches(self) -> dict[str, Trigger]:
        return {k: t for k, t in self.triggers.items() if t.kind == "when"}

    @property
    def timers(self) -> dict[str, Trigger]:
        return {k: t for k, t in self.triggers.items() if t.kind == "at"}


def _where(error: BaseException) -> str:
    tb, line = error.__traceback__, None
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == "recipe.py":
            line = tb.tb_lineno
        tb = tb.tb_next
    return f"line {line}: " if line else ""


def load(source: str, memory: Page, now: datetime) -> Recipe:
    """Run the recipe's top level to collect its triggers."""
    problems = lint(source)
    if problems:
        raise RecipeError("; ".join(problems))
    ctx = Context(this=memory, now=now, triggers=[])
    token = api._current.set(ctx)
    try:
        with _Budget():
            exec(
                compile(source, "recipe.py", "exec"),
                {"__name__": "recipe", "__builtins__": _SAFE_BUILTINS},
            )
    except RecipeError as e:
        raise RecipeError(f"{_where(e)}{e}") from None
    except Exception as e:
        raise RecipeError(f"{_where(e)}{type(e).__name__}: {e}") from None
    finally:
        api._current.reset(token)
    triggers: dict[str, Trigger] = {}
    for trigger in ctx.triggers or []:
        # Content-derived keys: an unchanged trigger keeps its history when the
        # recipe is rewritten around it; a changed one starts fresh.
        digest = hashlib.sha1(trigger.signature().encode()).hexdigest()[:10]
        triggers[f"{memory.slug}#{trigger.name}-{digest}"] = trigger
    return Recipe(source=source, triggers=triggers)


@dataclass
class Run:
    effects: list[Effect]
    asked: list[tuple[Page, Question, Any]]


def run(
    trigger: Trigger,
    memory: Page,
    now: datetime,
    ask: Callable[[Page, Question], Any],
    news: Page | None = None,
    fetch: Callable[[str], Page] | None = None,
) -> Run:
    """Call a trigger's handler. Returns what it asked Jev and the effects it recorded."""
    ctx = Context(this=memory, now=now, effects=[], ask=ask, fetch=fetch)
    token = api._current.set(ctx)
    try:
        takes_news = trigger.fn.__code__.co_argcount > 0
        with _Budget():
            trigger.fn(news) if takes_news else trigger.fn()
    except RecipeError as e:
        raise RecipeError(f"{_where(e)}{e}") from None
    except Exception as e:
        raise RecipeError(f"{_where(e)}{type(e).__name__}: {e}") from None
    finally:
        api._current.reset(token)
    return Run(effects=ctx.effects or [], asked=ctx.asked)
