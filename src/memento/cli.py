"""memento: proactive memory for GBrain.

memento run                 watch the brain and run every recipe
memento ls                  proactive memories and what they wait for
memento show SLUG           a memory's recipe
memento check FILE          load a recipe (a page .md, a .py, or -) and list its triggers
memento write SLUG          have River write (or rewrite) a page's recipe now
memento time [+3h|-1d|"mon 7:45"|reset]   show or move the clock
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.markup import escape
from rich.syntax import Syntax

from . import state
from .api import Page, RecipeError
from .gbrain import Brain, parse
from .recipe import load
from .runtime import Runtime, describe, next_fire, watching

out = Console(highlight=False)


def _runtime(models: bool = False) -> Runtime:
    """A runtime over the configured brain with every recipe loaded; Jev and River if `models`."""
    jev = river = None
    if models:
        from .jev import Jev
        from .river import River

        jev, river = Jev(), River()
    runtime = Runtime(Brain(), jev, river)
    for slug in runtime.brain.slugs():
        if stored := runtime.brain.read(slug):
            runtime.register(stored)
    return runtime


def cmd_run(args) -> None:
    runtime = _runtime(models=True)
    try:
        asyncio.run(runtime.run())
    except KeyboardInterrupt:
        runtime.state.save()


def cmd_ls(args) -> None:
    runtime = _runtime()
    now = runtime.now()
    if not runtime.memories:
        out.print("[dim]No proactive memories yet.[/]")
    for slug, memory in sorted(runtime.memories.items(), key=lambda kv: kv[1].page.title.lower()):
        out.print(f"[bold]{escape(memory.page.title)}[/]  [dim]{escape(slug)}[/]")
        if memory.error:
            out.print(f"  [red]error[/]  {escape(memory.error)}")
            continue
        for key, trigger in memory.recipe.triggers.items():
            if trigger.kind == "at":
                record = runtime.state.timers.get(key, {"since": now.isoformat(), "fired": None})
                moment = next_fire(trigger, record, now)
                status = (
                    f"[green]next {moment:%a %b %-d %-I:%M %p}[/]" if moment else "[dim]done[/]"
                )
            else:
                status = "[green]watching[/]" if watching(trigger, now) else "[dim]stopped[/]"
            out.print(f"  {status}  [dim]{escape(describe(trigger))}[/]")
    out.print(f"\n[dim]now {now:%A %B %-d, %-I:%M %p}[/]")


def cmd_show(args) -> None:
    """A memory's file, as GBrain stores it. Without a slug: the newest memory with a recipe."""
    brain = Brain()
    slugs = brain.slugs()
    if args.slug:
        slug = args.slug
    else:
        with_recipe = [s for s in slugs if (st := brain.read(s)) and st.recipe.strip()]
        if not with_recipe:
            sys.exit("no memory has a recipe yet")
        slug = max(with_recipe, key=lambda s: slugs[s].stat().st_mtime)
    path = brain.root / f"{slug}.md"
    if not path.exists():
        sys.exit(f"no page {slug!r} in {brain.root}")
    out.print(f"[dim]{escape(str(path))}[/]\n")
    out.print(Syntax(path.read_text().rstrip(), "markdown", theme="ansi_dark", background_color="default"))
    if args.open:
        import subprocess

        subprocess.run(["open", str(path)])


def cmd_check(args) -> None:
    text = sys.stdin.read() if args.file == "-" else Path(args.file).read_text()
    if args.file.endswith(".py"):
        source, page = text, Page(slug="recipe", title="recipe", text="")
    else:
        stored = parse(Path(args.file).stem, text)
        source, page = stored.recipe, stored.page
    if not source.strip():
        sys.exit("no recipe: the page has no `recipe:` in its frontmatter")
    try:
        recipe = load(source, page, state.now())
    except RecipeError as e:
        out.print(f"[red]✗[/] {escape(str(e))}")
        sys.exit(1)
    out.print(f"[green]✓[/] {len(recipe.triggers)} trigger(s)")
    for trigger in recipe.triggers.values():
        out.print(f"  {escape(describe(trigger))}")


def cmd_write(args) -> None:
    runtime = _runtime(models=True)
    stored = runtime.brain.read(args.slug)
    if not stored:
        sys.exit(f"no page {args.slug!r}")

    async def go():
        runtime.state.pages.setdefault(args.slug, state.PageState())
        await runtime.write_recipe(stored, current=stored.recipe or None)

    asyncio.run(go())
    runtime.state.save()


def cmd_time(args) -> None:
    if args.spec:
        try:
            state.travel(" ".join(args.spec))
        except ValueError as e:
            sys.exit(str(e))
    shift = state.offset()
    note = (
        f"  [dim](real time {'+' if shift.total_seconds() >= 0 else '-'}{abs(shift)})[/]"
        if shift
        else ""
    )
    out.print(f"{state.now():%A %B %-d %Y, %-I:%M %p}{note}")


def main() -> None:
    state.load_env()
    parser = argparse.ArgumentParser(prog="memento", description="Proactive memory for GBrain.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="watch the brain and run every recipe").set_defaults(fn=cmd_run)
    sub.add_parser("ls", help="proactive memories and what they wait for").set_defaults(fn=cmd_ls)
    p = sub.add_parser("show", help="a memory's file (default: the newest memory with a recipe)")
    p.add_argument("slug", nargs="?")
    p.add_argument("--open", action="store_true", help="also open the file in its default app")
    p.set_defaults(fn=cmd_show)
    p = sub.add_parser("check", help="load a recipe and list its triggers")
    p.add_argument("file", help="a page (.md), a recipe (.py), or - for a page on stdin")
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("write", help="have River write (or rewrite) a page's recipe now")
    p.add_argument("slug")
    p.set_defaults(fn=cmd_write)
    p = sub.add_parser("time", help="show or move the clock (for demos)")
    p.add_argument(
        "spec", nargs="*", help='+3h, -1d, +1d2h, "mon 7:45", reset, or "YYYY-MM-DD HH:MM"'
    )
    p.set_defaults(fn=cmd_time)
    args = parser.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
