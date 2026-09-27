"""What memento remembers about the brain between runs, and its clock.

All of it lives in MEMENTO_HOME (default ~/.memento): `state.json` for what
has been seen, reviewed and fired; `clock.json` for the demo clock offset,
which `memento time` moves and a running `memento run` picks up.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .api import TZ

HOME = Path(os.environ.get("MEMENTO_HOME", "~/.memento")).expanduser()


def load_env() -> None:
    """Fill the environment from MEMENTO_HOME/.env (keys stay out of the repo)."""
    path = HOME / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and not key.startswith("#"):
                os.environ.setdefault(key.strip(), value.strip().strip("'\""))


# ---- clock -----------------------------------------------------------------


def _clock_file() -> Path:
    return HOME / "clock.json"


def offset() -> timedelta:
    try:
        return timedelta(seconds=json.loads(_clock_file().read_text())["offset"])
    except (FileNotFoundError, KeyError, ValueError):
        return timedelta()


def now() -> datetime:
    return datetime.now(TZ).replace(microsecond=0) + offset()


def set_offset(value: timedelta) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    _clock_file().write_text(json.dumps({"offset": int(value.total_seconds())}))


_SPAN = re.compile(r"(\d+(?:\.\d+)?)(min|m|h|d|w)")
_UNITS = {"m": "minutes", "min": "minutes", "h": "hours", "d": "days", "w": "weeks"}
_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_WEEKDAY = re.compile(r"(mon|tue|wed|thu|fri|sat|sun)[a-z]*\s+(\d{1,2}):(\d{2})")


def travel(spec: str) -> datetime:
    """`+3h`, `-1d`, `+1d2h`, `mon 7:45`, `reset`, or "YYYY-MM-DD HH:MM". Returns the new now."""
    spec = spec.strip().lower()
    if spec in ("reset", "now"):
        set_offset(timedelta())
    elif spec[:1] in ("+", "-"):
        body = spec[1:].replace(" ", "")
        parts = _SPAN.findall(body)
        if not parts or "".join(n + u for n, u in parts) != body:
            raise ValueError(f"can't read {spec!r}; try +3h, -1d, +1d2h, +30m or reset")
        span = sum((timedelta(**{_UNITS[u]: float(n)}) for n, u in parts), timedelta())
        set_offset(offset() + (-span if spec[0] == "-" else span))
    elif m := _WEEKDAY.fullmatch(spec):
        # "mon 7:45": the next Monday at 7:45 (today, if that's still ahead)
        day = [d[:3] for d in _DAYS].index(m.group(1)[:3])
        current = now()
        target = current.replace(hour=int(m.group(2)), minute=int(m.group(3)), second=0)
        target += timedelta(days=(day - current.weekday()) % 7)
        if target <= current:
            target += timedelta(weeks=1)
        set_offset(target - datetime.now(TZ).replace(microsecond=0))
    else:
        target = datetime.fromisoformat(spec)
        target = target if target.tzinfo else target.replace(tzinfo=TZ)
        set_offset(target - datetime.now(TZ).replace(microsecond=0))
    return now()


# ---- state -----------------------------------------------------------------


@dataclass
class PageState:
    file: str = ""  # digest of the whole file
    body: str = ""  # digest of the body
    recipe: str = ""  # the recipe source, as last seen
    reviewed: str = ""  # body digest River last looked at


@dataclass
class State:
    pages: dict[str, PageState] = field(default_factory=dict)
    own: dict[str, str] = field(default_factory=dict)  # file digests memento wrote itself
    timers: dict[str, dict] = field(default_factory=dict)  # key -> {"since": iso, "fired": iso | None}

    @classmethod
    def load(cls) -> State:
        try:
            raw = json.loads((HOME / "state.json").read_text())
        except (FileNotFoundError, ValueError):
            return cls()
        return cls(
            pages={slug: PageState(**p) for slug, p in raw.get("pages", {}).items()},
            own=raw.get("own", {}),
            timers=raw.get("timers", {}),
        )

    def save(self) -> None:
        HOME.mkdir(parents=True, exist_ok=True)
        tmp = HOME / "state.json.tmp"
        tmp.write_text(json.dumps(asdict(self), indent=1))
        tmp.replace(HOME / "state.json")
