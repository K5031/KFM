"""A proposal of teams for a project, as its secretary puts it to the person. Nothing of it
exists until the person starts it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, Field


class Team(BaseModel):
    name: Annotated[str, Field(min_length=1, description="Short: marketing, dev.")]
    does: Annotated[str, Field(min_length=1, description=(
        "What it is there to do, written to its lead, who knows nothing else."))]
    ongoing: Annotated[bool, Field(description=(
        "True for standing work that never ends; false for a task that finishes."))] = False
    folder: Annotated[str | None, Field(description=(
        "A folder of its own to work in, inside the project's; none: the project's."))] = None
    model: Annotated[str | None, Field(description=(
        "The model its lead runs on, where that is yours to name."))] = None


@dataclass(frozen=True, slots=True)
class Draft:
    teams: tuple[Team, ...]
    spent_usd: float
