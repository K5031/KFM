"""What the manager tells whoever shows the fleet to the person: a frontend gets these as
they happen (Manager's on_event), and the fleet's record keeps them (log.py)."""

from __future__ import annotations

from dataclasses import dataclass

from kfm.fleet import Unit


@dataclass(frozen=True, slots=True)
class Changed:
    unit: Unit
    state: str  # what it became


@dataclass(frozen=True, slots=True)
class Said:
    unit: Unit
    text: str  # what the agent wrote in its own conversation


@dataclass(frozen=True, slots=True)
class Delivered:
    """A report, question, failure or update on its way up: to the unit's supervisor, or
    with `to` None to the person."""

    unit: Unit
    to: Unit | None
    kind: str  # report, question, failure, update, stopped
    text: str


@dataclass(frozen=True, slots=True)
class Approval:
    """A unit wants to do something its permissions do not cover. Answer with
    Manager.approve."""

    unit: Unit
    request_id: str
    tool: str
    arguments: str


@dataclass(frozen=True, slots=True)
class Answered:
    """The person answered an Approval."""

    unit: Unit
    request_id: str
    allow: bool


@dataclass(frozen=True, slots=True)
class Told:
    """What the person said to a unit."""

    text: str
    to: Unit | None = None


@dataclass(frozen=True, slots=True)
class Notice:
    text: str


type FleetEvent = Changed | Said | Delivered | Approval | Answered | Told | Notice
