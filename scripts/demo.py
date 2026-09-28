"""Drive a Memento demo: a fresh brain, an ordinary AI chat on GBrain, email arriving.

    uv run scripts/demo.py stage            # fresh brain + the two memories from weeks ago
    uv run scripts/demo.py reset            # fresh, empty demo brain + memento state
    uv run scripts/demo.py chat             # chat with Claude; GBrain is its memory
    uv run scripts/demo.py chat "message"   # one message, for scripting
    uv run scripts/demo.py email "United Airlines" "Schedule change: UA123" "body…"
    uv run scripts/demo.py play             # the whole story, for rehearsal

Run `uv run memento run` in another terminal to watch it happen. The chat is
Claude Code with GBrain as an MCP server, told only that GBrain is its memory;
it knows nothing about Memento. Uses GBRAIN_HOME and MEMENTO_GBRAIN from
~/.memento/.env, like memento itself.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path

from memento import state

state.load_env()
HOME = state.HOME
GBRAIN = shlex.split(os.environ.get("MEMENTO_GBRAIN", "gbrain"))
BRAIN_HOME = Path(os.environ.get("GBRAIN_HOME", "")).expanduser()

MEMORY_PROMPT = (
    "Your long-term memory is GBrain, through the gbrain MCP tools. Whenever the user tells you "
    "something worth remembering (plans, bookings, promises, people, news), save it right away "
    "without asking: use the `capture` tool with a markdown page (a clear `# Title` heading, then "
    "the facts, one topic per page), or update the relevant existing page with get_page + put_page. "
    "Never use local files for memory. Keep replies short and friendly."
)

ISSUE = "sinaatalay/memento#1"  # a public issue you control: close it on stage

# Two memories from "weeks ago", already carrying the recipes River wrote.
SOC2 = """\
from memento import when, notify, update

@when(
    "our final SOC 2 report was delivered or issued",
    unless="the audit is only scheduled, in progress, or a draft",
)
def soc2_ready(news):
    notify("SOC 2 is in. Send it to Dan at Acme today: it unblocks the $18k pilot.")
    update(news)
"""

TERM_SHEET = """\
from memento import at, when, notify, update, this

friday = at("{friday} 17:00")

@when("Maya at Northwind sent the term sheet", until=friday)
def arrived(news):
    notify("Northwind's term sheet is in. Read it tonight.")
    update(news)

@at(friday)
def silence():
    if not this.says("the Northwind term sheet arrived"):
        notify("Friday 5pm and no term sheet from Northwind. Call Maya.")
"""

STORY = [
    ("chat", f"We can't ship the iOS release until {ISSUE} is fixed (Safari checkout crash). "
             "Ship the moment it's fixed."),
    ("say", f"now close {ISSUE} as completed, then: memento time +6h"),
    ("time", "+6h"),
    ("email", "Jen Alvarez (Prescient Assurance)", "Your final SOC 2 Type I report",
     "Hi! Attached is your final SOC 2 Type I report, signed and issued today. Congrats!"),
    ("time", "fri 17:05"),
]  # fmt: skip


def gbrain(*args: str, stdin: str | None = None) -> str:
    done = subprocess.run(
        [*GBRAIN, *args], input=stdin, capture_output=True, text=True, cwd=BRAIN_HOME or None
    )
    if done.returncode:
        sys.exit(f"gbrain {' '.join(args)} failed:\n{done.stderr[-800:]}")
    return done.stdout


def reset() -> None:
    """Wipe and re-create the demo brain. Refuses anything but a dedicated GBRAIN_HOME."""
    if (
        not BRAIN_HOME.is_absolute()
        or BRAIN_HOME == Path.home()
        or not str(BRAIN_HOME).startswith(str(HOME))
    ):
        sys.exit(
            f"GBRAIN_HOME must be a dedicated demo brain under {HOME}, not {BRAIN_HOME or 'unset'}"
        )
    shutil.rmtree(BRAIN_HOME / ".gbrain", ignore_errors=True)
    for name in ("state.json", "clock.json"):
        (HOME / name).unlink(missing_ok=True)
    BRAIN_HOME.mkdir(parents=True, exist_ok=True)
    gbrain("init", "--pglite", "--no-embedding", "--non-interactive", "--json")
    print(f"fresh brain in {BRAIN_HOME}; start `uv run memento run` now")


def page(title: str, body: str, recipe: str, kind: str = "note") -> str:
    block = "".join(f"  {line}\n" if line else "\n" for line in recipe.splitlines())
    return f"---\ntype: {kind}\ntitle: {json.dumps(title)}\nrecipe: |\n{block}---\n\n# {title}\n\n{body}\n"


def stage() -> None:
    """A fresh brain holding the two memories from weeks ago. Start `memento run` after."""
    reset()
    today = state.now()
    friday = (today + timedelta(days=(4 - today.weekday()) % 7 or 7)).strftime("%Y-%m-%d")
    gbrain("put", "projects/acme-pilot", "--json", stdin=page(
        "Acme pilot",
        "Told Dan Kim (Acme security) he gets our SOC 2 Type I report the day our auditor "
        "delivers it. Acme's $18k pilot is blocked on it.",
        SOC2, "project"))  # fmt: skip
    gbrain("put", "deals/northwind", "--json", stdin=page(
        "Northwind term sheet",
        f"Maya at Northwind said their term sheet comes by Friday ({friday}).",
        TERM_SHEET.format(friday=friday), "deal"))  # fmt: skip
    print(f"staged: Acme pilot (SOC 2 promise), Northwind (term sheet by Fri {friday})")
    print(f"live issue: https://github.com/{ISSUE.replace('#', '/issues/')}  (reopen it if closed)")


def mcp_config() -> Path:
    path = HOME / "mcp.json"
    server = {
        "command": GBRAIN[0],
        "args": [*GBRAIN[1:], "serve"],
        "env": {"GBRAIN_HOME": str(BRAIN_HOME)},
    }
    path.write_text(json.dumps({"mcpServers": {"gbrain": server}}, indent=2))
    return path


def chat(message: str | None = None) -> None:
    workdir = HOME / "chat"  # one Claude Code project for every demo chat
    workdir.mkdir(parents=True, exist_ok=True)
    args = [
        "claude",
        "--mcp-config", str(mcp_config()),
        "--strict-mcp-config",
        "--allowedTools", "mcp__gbrain__*",
        "--disallowedTools", "Write Edit Read Bash Glob Grep",
        "--append-system-prompt", MEMORY_PROMPT,
    ]  # fmt: skip
    if message is None:
        os.chdir(workdir)
        os.execvp("claude", args)
    done = subprocess.run([*args, "-p", message, "--model", "sonnet"], cwd=workdir, stdin=subprocess.DEVNULL,
                          capture_output=True, text=True)  # fmt: skip
    print(f"you     {message}\nclaude  {done.stdout.strip()}")


def email(sender: str, subject: str, body: str) -> None:
    """An email page, as GBrain's Gmail connector would write it."""
    slug = (
        "emails/"
        + "-".join("".join(c if c.isalnum() else " " for c in subject.lower()).split())[:60]
    )
    page = f"---\ntype: email\ntitle: {json.dumps(subject)}\nfrom: {json.dumps(sender)}\n---\n\n{body}\n"
    gbrain("put", slug, "--json", stdin=page)
    print(f"email   {sender}: {subject}")


def play() -> None:
    for step in STORY:
        kind, *args = step
        if kind == "chat":
            chat(args[0])
        elif kind == "email":
            email(*args)
        elif kind == "time":
            print(f"time    {state.travel(args[0]):%a %b %-d, %-I:%M %p}")
        elif kind == "say":
            input(f"\n>>> {args[0]}  [enter] ")
            continue
        time.sleep(12)


if __name__ == "__main__":
    command, *rest = sys.argv[1:] or ["help"]
    if command == "reset":
        reset()
    elif command == "stage":
        stage()
    elif command == "chat":
        chat(" ".join(rest) or None)
    elif command == "email" and len(rest) == 3:
        email(*rest)
    elif command == "play":
        play()
    else:
        print(__doc__)
