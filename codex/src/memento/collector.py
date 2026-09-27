"""Collect declarative recipes in an AST-restricted, time-limited child process.

This is deliberately *not* advertised as an operating-system security sandbox.
The accepted Python subset excludes general computation and IO. An application
accepting code from adversarial tenants should add an OS/container boundary.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

from .dsl import RECIPE_EXPORTS, Recipe, _capture


MAX_RECIPE_BYTES = 16_384
MAX_AST_NODES = 2_000
MAX_MARKDOWN_BYTES = 1_000_000


class RecipeError(ValueError):
    """A recipe could not be safely collected or did not satisfy the DSL."""


_FUNCTION_RESULTS = {
    "at": "datetime",
    "hours": "duration",
    "minutes": "duration",
    "days": "duration",
    "noul": "question",
    "choice": "question",
    "score": "question",
    "alert": "action",
    "surface": "action",
    "rewrite": "action",
    "remind": "none",
    "on": "none",
    "expires": "none",
}


class _Validator:
    def __init__(self) -> None:
        self.imports: dict[str, str] = {}
        self.modules: set[str] = set()
        self.variables: dict[str, str] = {}

    @staticmethod
    def fail(node: ast.AST, message: str) -> None:
        raise RecipeError(f"line {getattr(node, 'lineno', 1)}: {message}")

    def add_name(self, name: str, node: ast.AST) -> None:
        if name.startswith("_"):
            self.fail(node, "private names are not permitted")
        if name in self.imports or name in self.modules or name in self.variables:
            self.fail(node, f"name {name!r} is already defined")

    def module(self, tree: ast.Module) -> None:
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                if node.module != "memento" or node.level:
                    self.fail(node, "only imports from memento are allowed")
                for alias in node.names:
                    if alias.name not in RECIPE_EXPORTS:
                        self.fail(node, f"unsupported memento import {alias.name!r}")
                    name = alias.asname or alias.name
                    self.add_name(name, node)
                    self.imports[name] = alias.name
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name != "memento":
                        self.fail(node, "only imports from memento are allowed")
                    name = alias.asname or alias.name
                    self.add_name(name, node)
                    self.modules.add(name)
            elif isinstance(node, ast.Assign):
                if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                    self.fail(node, "use a single variable name per assignment")
                name = node.targets[0].id
                self.add_name(name, node)
                result_type = self.expression(node.value)
                if result_type.startswith("function:") or result_type == "module":
                    self.fail(node, "assign recipe values, not imported functions")
                self.variables[name] = result_type
            elif isinstance(node, ast.Expr):
                self.expression(node.value)
            else:
                self.fail(node, f"{type(node).__name__} is not allowed in recipes")

    def expression(self, node: ast.AST, depth: int = 0) -> str:
        if depth > 32:
            self.fail(node, "recipe expressions are nested too deeply")
        recurse = lambda child: self.expression(child, depth + 1)
        if isinstance(node, ast.Constant):
            if node.value is None or isinstance(node.value, (str, int, float, bool)):
                return "literal"
        elif isinstance(node, ast.Name):
            if node.id in self.variables:
                return self.variables[node.id]
            if node.id in self.modules:
                return "module"
            if node.id in self.imports:
                name = self.imports[node.id]
                return f"function:{name}" if name in _FUNCTION_RESULTS else "symbol"
            self.fail(node, f"unknown name {node.id!r}")
        elif isinstance(node, ast.Attribute):
            if (isinstance(node.value, ast.Name)
                    and node.value.id in self.modules
                    and node.attr in RECIPE_EXPORTS):
                name = node.attr
                return f"function:{name}" if name in _FUNCTION_RESULTS else "symbol"
            self.fail(node, "only public memento.<name> access is allowed")
        elif isinstance(node, ast.Call):
            function = recurse(node.func)
            if not function.startswith("function:"):
                self.fail(node, "only declared memento functions may be called")
            for arg in node.args:
                recurse(arg)
            for keyword in node.keywords:
                if keyword.arg is None:
                    self.fail(node, "expanded keyword arguments are not allowed")
                recurse(keyword.value)
            return _FUNCTION_RESULTS[function.split(":", 1)[1]]
        elif isinstance(node, (ast.List, ast.Tuple)):
            for item in node.elts:
                recurse(item)
            return "sequence"
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if key is None:
                    self.fail(node, "expanded dictionaries are not allowed")
                recurse(key)
                recurse(value)
            return "mapping"
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            if isinstance(node.operand, ast.Constant) and isinstance(
                node.operand.value, (int, float)
            ):
                return "literal"
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            left, right = recurse(node.left), recurse(node.right)
            if left == "datetime" and right == "duration":
                return "datetime"
            if left == right == "duration":
                return "duration"
            self.fail(node, "arithmetic is limited to times and durations")
        self.fail(node, f"{type(node).__name__} expressions are not allowed")
        raise AssertionError("unreachable")


def validate_source(source: str) -> ast.Module:
    """Validate syntax without executing user code."""
    if not isinstance(source, str):
        raise RecipeError("recipe must be a Python source string")
    if len(source.encode("utf-8")) > MAX_RECIPE_BYTES:
        raise RecipeError(f"recipe exceeds {MAX_RECIPE_BYTES} bytes")
    for number, line in enumerate(source.splitlines(), 1):
        if "\t" in line:
            raise RecipeError(f"line {number}: tabs are not allowed")
    try:
        tree = ast.parse(source, filename="<memento-recipe>", mode="exec")
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise RecipeError(f"invalid Python recipe: {exc}") from exc
    if sum(1 for _ in ast.walk(tree)) > MAX_AST_NODES:
        raise RecipeError("recipe contains too many expressions")
    _Validator().module(tree)
    return tree


def _execute(source: str) -> Recipe:
    """Only called by the isolated collector child (and internal tests)."""
    tree = validate_source(source)
    public_module = ModuleType("memento")
    public_module.__dict__.update(RECIPE_EXPORTS)

    def restricted_import(
        name: str,
        globals: Any = None,
        locals: Any = None,
        fromlist: Any = (),
        level: int = 0,
    ) -> ModuleType:
        if name != "memento" or level:
            raise RecipeError("only memento may be imported")
        return public_module

    namespace = {"__builtins__": {"__import__": restricted_import}}
    with _capture() as registration:
        exec(compile(tree, "<memento-recipe>", "exec"), namespace, namespace)
        return Recipe(
            reminders=registration.reminders,
            triggers=registration.triggers,
            expires_at=registration.expires_at,
        )


def _child() -> None:
    try:
        payload = json.load(sys.stdin)
        recipe = _execute(payload["source"])
        result = {"ok": True, "recipe": recipe.model_dump(mode="json")}
    except Exception as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    sys.stdout.write(json.dumps(result))


def collect(source: str, *, timeout: float = 3.0) -> Recipe:
    """Return typed registrations without executing the recipe in this process.

    ``timeout`` limits the whole child process, including import startup time.
    Errors are suitable for feeding back to a writer model as validation errors.
    """
    validate_source(source)
    if not 0 < timeout <= 30:
        raise RecipeError("collector timeout must be greater than 0 and <= 30")
    source_root = str(Path(__file__).resolve().parents[1])
    bootstrap = (
        f"import sys; sys.path.insert(0, {source_root!r}); "
        "from memento.collector import _child; _child()"
    )
    try:
        process = subprocess.run(
            [sys.executable, "-I", "-c", bootstrap],
            input=json.dumps({"source": source}),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RecipeError(f"recipe collection timed out after {timeout:g}s") from exc
    except OSError as exc:
        raise RecipeError(f"could not start recipe collector: {exc}") from exc
    if process.returncode:
        detail = process.stderr.strip()[-2000:]
        raise RecipeError(f"recipe collector failed ({process.returncode}): {detail}")
    try:
        result = json.loads(process.stdout)
    except (ValueError, TypeError) as exc:
        raise RecipeError("recipe collector returned invalid JSON") from exc
    if not result.get("ok"):
        raise RecipeError(result.get("error", "recipe collection failed"))
    return Recipe.model_validate(result["recipe"])


def extract_recipe(markdown: str) -> str | None:
    """Read ``recipe: |`` from YAML front matter; passive memories return None."""
    if len(markdown.encode("utf-8")) > MAX_MARKDOWN_BYTES:
        raise RecipeError("memory Markdown exceeds 1 MB")
    lines = markdown.lstrip("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    end = next(
        (index for index, line in enumerate(lines[1:], 1)
         if line.strip() in {"---", "..."}),
        None,
    )
    if end is None:
        raise RecipeError("memory has unclosed YAML front matter")
    try:
        metadata = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError as exc:
        raise RecipeError(f"invalid YAML front matter: {exc}") from exc
    if not isinstance(metadata, dict):
        raise RecipeError("YAML front matter must be a mapping")
    recipe = metadata.get("recipe")
    if recipe is None:
        return None
    if not isinstance(recipe, str):
        raise RecipeError("front matter recipe must be a Python source string")
    return recipe


def collect_markdown(markdown: str, *, timeout: float = 3.0) -> Recipe:
    source = extract_recipe(markdown)
    return collect(source, timeout=timeout) if source is not None else Recipe()
