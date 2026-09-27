"""Typed shapes shared by recipes (the DSL) and the runtime."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class Source(str, Enum):
    email = "email"
    calendar = "calendar"
    chat = "chat"


# The name each source's item has inside Jev's state. Questions must point at it.
STATE_KEY = {Source.email: "email", Source.calendar: "event", Source.chat: "message"}


class Noul(BaseModel):
    kind: Literal["noul"] = "noul"
    question: str
    yes: str | None = None
    no: str | None = None


class Choice(BaseModel):
    kind: Literal["choice"] = "choice"
    question: str
    options: dict[str, str]
    fire_on: str


class Score(BaseModel):
    kind: Literal["score"] = "score"
    question: str
    levels: list[str]
    at_least: int


Question = Annotated[Noul | Choice | Score, Field(discriminator="kind")]


class Alert(BaseModel):
    kind: Literal["alert"] = "alert"
    text: str


class Surface(BaseModel):
    kind: Literal["surface"] = "surface"


class Rewrite(BaseModel):
    kind: Literal["rewrite"] = "rewrite"


Action = Annotated[Alert | Surface | Rewrite, Field(discriminator="kind")]


class TimeTrigger(BaseModel):
    kind: Literal["time"] = "time"
    id: str = ""
    fire_at: datetime
    action: Alert


class EventTrigger(BaseModel):
    kind: Literal["event"] = "event"
    id: str = ""
    source: Source
    when: Question
    actions: list[Action]
    threshold: float = Field(0.7, ge=0.0, le=1.0)


Trigger = Annotated[TimeTrigger | EventTrigger, Field(discriminator="kind")]


class Recipe(BaseModel):
    triggers: list[Trigger] = []
    expires: datetime | None = None
