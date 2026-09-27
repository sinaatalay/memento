"""The runtime: keeps every recipe in the brain loaded, and runs them.

Once a second it looks at the brain:
- A page that changed is re-read and its recipe (re)loaded.
- A change memento didn't make itself is news. One Jev request checks it
  against every other memory's @when claims. A page without a recipe is also
  asked whether it deserves one; if so, River writes it into the page.
- @at timers that came due fire.
Handlers run in a worker thread and only record effects; `notify` and
`update` are carried out here.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta

from rich.console import Console
from rich.markup import escape

from . import deliver, state
from .api import Effect, Page, RecipeError, Trigger
from .gbrain import Brain, GBrainError, Stored
from .jev import FIRES, WORTH_A_RECIPE, Jev
from .recipe import Recipe, load, run
from .river import River

WORTH = 0.5  # P(worth a recipe) above which River is asked; River may still say NONE
LATE = timedelta(
    hours=1
)  # a reminder later than this (runtime was off, clock jumped) is stale: skip it


@dataclass
class Memory:
    """A page with a recipe."""

    stored: Stored
    recipe: Recipe | None = None
    error: str | None = None

    @property
    def page(self) -> Page:
        return self.stored.page


def next_fire(trigger: Trigger, record: dict, now: datetime) -> datetime | None:
    """When an @at trigger fires next, given its record {"since", "fired"}; None if never."""
    since = datetime.fromisoformat(record["since"])
    if trigger.every is None:
        return trigger.at if record["fired"] is None and trigger.at > since else None
    after = datetime.fromisoformat(record["fired"]) if record["fired"] else since
    if after < trigger.at:
        return trigger.at
    steps = int((after - trigger.at) / trigger.every) + 1
    moment = trigger.at + steps * trigger.every
    return None if trigger.until and moment > trigger.until else moment


def watching(trigger: Trigger, now: datetime) -> bool:
    return trigger.until is None or now < trigger.until


class Runtime:
    def __init__(self, brain: Brain, jev: Jev | None = None, river: River | None = None) -> None:
        self.brain = brain
        self.jev = jev
        self.river = river
        self.state = state.State.load()
        self.memories: dict[str, Memory] = {}
        self.mtimes: dict[str, float] = {}
        self.writing: set[str] = set()
        self.locks: dict[str, asyncio.Lock] = {}
        self.tasks: set[asyncio.Task] = set()
        self.out = Console(highlight=False)

    def now(self) -> datetime:
        return state.now()

    # ---- the loop ----

    async def run(self) -> None:
        if self.jev:
            self.jev.loop = asyncio.get_running_loop()
        first_run = not self.state.pages
        self.scan(baseline=first_run)
        self.state.save()
        self.header()
        while True:
            self.scan()
            self.check_timers()
            self.state.save()
            await asyncio.sleep(1)

    def spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    # ---- pages ----

    def scan(self, baseline: bool = False) -> None:
        """Re-read changed pages. On the first run ever, what's there is history, not news."""
        files = self.brain.slugs()
        for slug in set(self.mtimes) - set(files):
            self.mtimes.pop(slug, None)
            if self.memories.pop(slug, None):
                self.say("page", f"[dim]- {escape(slug)} (deleted)[/]")
        for slug, path in files.items():
            if slug in self.writing:
                continue
            try:
                mtime = path.stat().st_mtime
            except FileNotFoundError:
                continue
            if self.mtimes.get(slug) == mtime:
                continue
            self.mtimes[slug] = mtime
            if stored := self.brain.read(slug):
                self.observe(stored, baseline)

    def observe(self, stored: Stored, baseline: bool) -> None:
        slug = stored.page.slug
        before = self.state.pages.get(slug)
        self.register(stored)
        if before and before.file == stored.digest:
            return
        body = stored.body_digest
        after = state.PageState(
            stored.digest, body, stored.recipe, before.reviewed if before else ""
        )
        self.state.pages[slug] = after
        if baseline or self.state.own.get(slug) == stored.digest:
            after.reviewed = body
            return
        body_changed = before is None or before.body != body
        recipe_changed = before is None or before.recipe != stored.recipe
        if not body_changed:
            return  # only the recipe or metadata moved: not news
        verb = "+" if before is None else "~"
        self.say("page", f"{verb} [bold]{escape(stored.page.title)}[/]  [dim]{escape(slug)}[/]")
        gate = not stored.recipe.strip() and after.reviewed != body
        rewrite = bool(stored.recipe.strip()) and not recipe_changed and after.reviewed != body
        if stored.recipe.strip() and recipe_changed:
            after.reviewed = body  # someone wrote this recipe on purpose: trust it
        self.spawn(self.news(stored, gate=gate, rewrite=rewrite))

    def register(self, stored: Stored) -> None:
        """Keep the page's recipe loaded (or forget it, if it has none)."""
        slug = stored.page.slug
        if not stored.recipe.strip():
            self.memories.pop(slug, None)
            return
        known = self.memories.get(slug)
        if known and known.stored.recipe == stored.recipe:
            known.stored = stored
            return
        now = self.now()
        try:
            recipe = load(stored.recipe, stored.page, now)
        except RecipeError as e:
            self.memories[slug] = Memory(stored, None, str(e))
            self.say("error", f"{escape(stored.page.title)}: recipe doesn't load: {escape(str(e))}")
            return
        self.memories[slug] = Memory(stored, recipe)
        for key in recipe.timers:
            self.state.timers.setdefault(key, {"since": now.isoformat(), "fired": None})

    # ---- news ----

    async def news(self, stored: Stored, gate: bool, rewrite: bool) -> None:
        page, now = stored.page, self.now()
        claims = {
            key: (memory, trigger)
            for slug, memory in self.memories.items()
            if slug != page.slug and memory.recipe
            for key, trigger in memory.recipe.watches.items()
            if watching(trigger, now)
        }
        questions = {key: trigger.question for key, (_, trigger) in claims.items()}
        if gate:
            questions |= WORTH_A_RECIPE
        if questions and self.jev:
            asked = await self.jev.ask(page, now, questions)
            ranked = sorted(claims, key=lambda k: -float(asked.values[k]))
            fired = [k for k in ranked if float(asked.values[k]) >= FIRES]
            parts = [f"{asked.ms} ms", f"{len(claims)} claim{'s' * (len(claims) != 1)}"]
            worth = max((float(asked.values[k]) for k in WORTH_A_RECIPE), default=0.0)
            if gate:
                parts.append(f"worth a recipe {worth:.2f}")
            if fired:
                parts += [
                    f"[bold green]✓ {escape(claims[k][0].page.title)} {float(asked.values[k]):.2f}[/]"
                    for k in fired
                ]
            elif ranked:
                best = ranked[0]
                parts.append(
                    f"[dim]nothing fires (best: {escape(claims[best][0].page.title)} {float(asked.values[best]):.2f})[/]"
                )
            self.say("jev", " · ".join(parts))
            for key in fired:
                memory, trigger = claims[key]
                self.spawn(self.fire(memory, trigger, news=page))
            if gate:
                if fired:
                    self.state.pages[
                        page.slug
                    ].reviewed = stored.body_digest  # news for a memory, not a new one
                elif worth >= WORTH:
                    await self.write_recipe(stored)
                else:
                    self.state.pages[page.slug].reviewed = stored.body_digest
        if rewrite:
            await self.write_recipe(stored, current=stored.recipe)

    # ---- time ----

    def check_timers(self) -> None:
        now = self.now()
        for memory in list(self.memories.values()):
            if not memory.recipe:
                continue
            for key, trigger in memory.recipe.timers.items():
                record = self.state.timers.setdefault(
                    key, {"since": now.isoformat(), "fired": None}
                )
                moment = next_fire(trigger, record, now)
                if moment is not None and moment <= now:
                    record["fired"] = now.isoformat()
                    if now - moment > LATE:
                        late = f"{(now - moment).total_seconds() / 3600:.0f}h"
                        self.say(
                            "fire",
                            f"[dim]{escape(memory.page.title)} › {trigger.name}() skipped, {late} late[/]",
                        )
                    else:
                        self.spawn(self.fire(memory, trigger))

    # ---- handlers and effects ----

    async def fire(self, memory: Memory, trigger: Trigger, news: Page | None = None) -> None:
        now = self.now()
        why = (
            f"@when {trigger.claim}" if trigger.kind == "when" else f"@at {trigger.at:%a %-I:%M %p}"
        )
        self.say(
            "fire",
            f"[bold]{escape(memory.page.title)}[/] › {escape(trigger.name)}()  [dim]{escape(why)}[/]",
        )

        def ask(page: Page, question):
            assert self.jev is not None
            return self.jev.answer(page, now, question)

        try:
            done = await asyncio.wait_for(
                asyncio.to_thread(run, trigger, memory.page, now, ask, news), 90
            )
        except Exception as e:
            self.say(
                "error",
                f"{escape(memory.page.title)} › {trigger.name}(): {escape(str(e) or type(e).__name__)}",
            )
            return
        for page, question, answer in done.asked:
            text = getattr(question, "claim", None) or getattr(question, "question", "")
            self.say("jev", f"[dim]{escape(page.title)}: {escape(text)} → {answer}[/]")
        for effect in done.effects:
            try:
                await self.apply(memory, effect)
            except Exception as e:
                self.say(
                    "error", f"{escape(memory.page.title)}: {effect.kind} failed: {escape(str(e))}"
                )

    async def apply(self, memory: Memory, effect: Effect) -> None:
        if effect.kind == "notify":
            self.say("notify", f"[bold]{escape(memory.page.title)}[/]: {escape(effect.text)}")
            await deliver.send(memory.page.title, effect.text)
        elif effect.kind == "update":
            await self.update(memory.page.slug, effect.news)

    # ---- River ----

    def lock(self, slug: str) -> asyncio.Lock:
        return self.locks.setdefault(slug, asyncio.Lock())

    async def write_recipe(self, stored: Stored, current: str | None = None) -> None:
        if not self.river:
            return
        slug, now = stored.page.slug, self.now()
        async with self.lock(slug):
            self.say("river", f"[dim]reading {escape(stored.page.title)}…[/]")
            try:
                draft = await self.river.awrite(stored.page, now, current, self.others(slug))
            except Exception as e:
                self.say(
                    "error",
                    f"River couldn't write a recipe for {escape(stored.page.title)}: {escape(str(e))}",
                )
                return
            self.state.pages[slug].reviewed = stored.body_digest
            if draft.recipe.strip() == (current or "").strip():
                verdict = "keeps its recipe" if current else "needs no recipe"
                self.say(
                    "river",
                    f"{escape(stored.page.title)} {verdict} [dim]({draft.seconds:.1f} s)[/]",
                )
                return
            await self.write(slug, self.brain.set_recipe(slug, draft.recipe))
            verb = "rewrote" if current else "wrote"
            self.say(
                "river",
                f"{verb} a recipe for [bold]{escape(stored.page.title)}[/] [dim]({draft.seconds:.1f} s)[/]",
            )
            self.show_triggers(slug)

    def others(self, slug: str) -> list[Page]:
        return [m.page for s, m in self.memories.items() if s != slug and m.recipe]

    async def update(self, slug: str, news: Page | None) -> None:
        if not self.river:
            return
        async with self.lock(slug):
            memory = self.memories.get(slug)
            if memory is None:
                return
            now = self.now()
            self.say("river", f"[dim]updating {escape(memory.page.title)}…[/]")
            try:
                draft = await self.river.arevise(memory.page, memory.stored.recipe, news, now)
            except Exception as e:
                self.say(
                    "error", f"River couldn't update {escape(memory.page.title)}: {escape(str(e))}"
                )
                return

            async def both():
                await self.brain.add_timeline(
                    slug, f"{now:%Y-%m-%d}", draft.note or "Updated.", news and news.slug
                )
                await self.brain.set_recipe(slug, draft.recipe)

            await self.write(slug, both())
            self.say(
                "river",
                f"updated [bold]{escape(memory.page.title)}[/]: {escape(draft.note or '')} [dim]({draft.seconds:.1f} s)[/]",
            )
            self.show_triggers(slug)

    async def write(self, slug: str, operation) -> None:
        """Write to GBrain; the change that comes back from disk is ours, not news."""
        self.writing.add(slug)
        try:
            await operation
        except GBrainError as e:
            self.say("error", escape(str(e)))
        finally:
            if stored := self.brain.read(slug):
                self.state.own[slug] = stored.digest
                self.state.pages[slug] = state.PageState(
                    stored.digest, stored.body_digest, stored.recipe, stored.body_digest
                )
                self.register(stored)
                path = self.brain.root / f"{slug}.md"
                self.mtimes[slug] = path.stat().st_mtime if path.exists() else 0
            self.writing.discard(slug)
            self.state.save()

    # ---- output ----

    STYLES = {
        "page": "cyan",
        "jev": "magenta",
        "river": "blue",
        "fire": "yellow",
        "notify": "bold green",
        "error": "red",
    }

    def say(self, kind: str, text: str) -> None:
        label = "🔔" if kind == "notify" else kind
        self.out.print(
            f"[dim]{self.now():%a %H:%M}[/]  [{self.STYLES[kind]}]{label:<6}[/] {text}",
            soft_wrap=True,
        )

    def show_triggers(self, slug: str) -> None:
        memory = self.memories.get(slug)
        if not memory or not memory.recipe:
            return
        for trigger in memory.recipe.triggers.values():
            self.out.print(f"                 [dim]{escape(describe(trigger))}[/]")

    def header(self) -> None:
        active = sum(1 for m in self.memories.values() if m.recipe)
        self.out.print(
            f"[bold]memento[/] watching [cyan]{escape(str(self.brain.root))}[/]\n"
            f"[dim]{len(self.mtimes)} pages, {active} proactive · "
            f"now {self.now():%A %B %-d, %-I:%M %p}[/]\n"
        )


def span(delta) -> str:
    """7 days -> "week", 2 hours -> "2 hours"."""
    seconds = int(delta.total_seconds())
    for size, unit in ((604800, "week"), (86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds % size == 0:
            n = seconds // size
            return unit if n == 1 else f"{n} {unit}s"
    return str(delta)


def describe(trigger: Trigger) -> str:
    if trigger.kind == "at":
        spec = f"@at {trigger.at:%a %b %-d %-I:%M %p}"
        if trigger.every:
            spec += f", every {span(trigger.every)}"
    else:
        spec = f'@when "{trigger.claim}"'
    if trigger.until:
        spec += f", until {trigger.until:%a %b %-d %-I:%M %p}"
    return f"{spec} → {trigger.name}()"
