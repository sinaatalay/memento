"""Drive a Memento demo: a fresh brain, an ordinary AI chat on GBrain, email arriving.

    uv run scripts/demo.py reset            # fresh demo brain + memento state
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

STORY = [
    ("chat", "Booked my flight to New York for Monday: UA123 out of SFO at 8:05am, seat 14C."),
    ("chat", "Priya Raman is leaving Northwind to start a company and needs a founding product "
             "designer. I told her I'd keep an eye out."),
    ("chat", "I promised Priya our seed deck by Wednesday end of day."),
    ("chat", "Had coffee with Alex Chen today. Six years as a senior product designer at Figma, "
             "leaving next month, wants to be a founding designer somewhere."),
    ("email", "United Airlines", "Schedule change: UA 123 on Mon, Sep 28",
     "Your flight UA 123 from San Francisco (SFO) to New York (JFK) on Monday, September 28 now "
     "departs at 10:40 AM instead of 8:05 AM and arrives at 7:16 PM. Seat 14C is unchanged. "
     "Confirmation K7Q2LM."),
    ("time", "sun 22:45"),
    ("time", "mon 7:45"),
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
    if not BRAIN_HOME.is_absolute() or BRAIN_HOME == Path.home() or not str(BRAIN_HOME).startswith(str(HOME)):
        sys.exit(f"GBRAIN_HOME must be a dedicated demo brain under {HOME}, not {BRAIN_HOME or 'unset'}")
    shutil.rmtree(BRAIN_HOME / ".gbrain", ignore_errors=True)
    for name in ("state.json", "clock.json"):
        (HOME / name).unlink(missing_ok=True)
    BRAIN_HOME.mkdir(parents=True, exist_ok=True)
    gbrain("init", "--pglite", "--no-embedding", "--non-interactive", "--json")
    print(f"fresh brain in {BRAIN_HOME}; start `uv run memento run` now")


def mcp_config() -> Path:
    path = HOME / "mcp.json"
    server = {"command": GBRAIN[0], "args": [*GBRAIN[1:], "serve"], "env": {"GBRAIN_HOME": str(BRAIN_HOME)}}
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
    slug = "emails/" + "-".join("".join(c if c.isalnum() else " " for c in subject.lower()).split())[:60]
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
        time.sleep(12)


if __name__ == "__main__":
    command, *rest = sys.argv[1:] or ["help"]
    if command == "reset":
        reset()
    elif command == "chat":
        chat(" ".join(rest) or None)
    elif command == "email" and len(rest) == 3:
        email(*rest)
    elif command == "play":
        play()
    else:
        print(__doc__)
