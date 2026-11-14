"""What the manager tells whoever shows the fleet to the person: a frontend gets these as
they happen (Manager's on_event), and the fleet's record keeps them (log.py). They are
plain data: a unit is named by its id, and the fleet's record says who that is."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Changed:
    unit: str
    state: str  # what it became


@dataclass(frozen=True, slots=True)
class Said:
    unit: str
    text: str  # what the agent wrote in its own conversation


@dataclass(frozen=True, slots=True)
class Delivered:
    """A report, question, failure or update on its way up: to the unit's supervisor, or
    with `to` None to the person."""

    unit: str
    to: str | None
    kind: str  # report, question, failure, update, stopped
    text: str


@dataclass(frozen=True, slots=True)
class Instructed:
    """What a unit is given from above: its task, or a message from its supervisor. `by`
    None: the person gave it."""

    unit: str
    by: str | None
    kind: str  # task, message
    text: str


@dataclass(frozen=True, slots=True)
class Approval:
    """A unit wants to do something its permissions do not cover. Answer with
    Manager.approve."""

    unit: str
    request_id: str
    tool: str
    arguments: str


@dataclass(frozen=True, slots=True)
class Answered:
    """The person answered an Approval."""

    unit: str
    request_id: str
    allow: bool


@dataclass(frozen=True, slots=True)
class Told:
    """What the person said to a unit."""

    text: str
    to: str | None = None
    files: tuple[str, ...] = ()  # paths of what went with it


@dataclass(frozen=True, slots=True)
class Notice:
    text: str


type FleetEvent = (Changed | Said | Delivered | Instructed | Approval | Answered | Told
                   | Notice)


def units(event: FleetEvent) -> set[str]:
    """The ids of the units the event names."""
    named = (getattr(event, name, None) for name in ("unit", "to", "by"))
    return {unit for unit in named if unit is not None}
