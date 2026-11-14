"""What a frontend can do with KFM, and what it is told: the contract between KFM and
whatever shows it to a person (the window, the command line). Normative for frontends, as
`kfm.api` is for agent programs. A frontend takes a `Service` and nothing else of KFM's
workings: `kfm.local` is the service in this process, `kfm.remote` the one that
`kfm serve` runs, reached over a socket.

Ids are text: a project's is its folder's name, a fleet's is "<project>/<its folder's
name>", a unit's is the one in the fleet's record ("u2").

Errors: `FleetError` for what the fleet's rules do not allow, `UnitError` for an agent that
could not be reached, `ValueError` for a name, a setting or an id that does not fit,
`OSError` for a folder or a file. Their text is for the person."""

from __future__ import annotations

import abc
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from kfm.config import FleetConfig
from kfm.draft import Draft
from kfm.events import FleetEvent
from kfm.fleet import Fleet

VERSION = 2  # of this contract; a service and a client of different versions do not talk


@dataclass(frozen=True, slots=True)
class ProjectInfo:
    id: str
    name: str
    work: str  # the folder its fleets work in, unless one is given its own
    fleets: int
    running: int  # of its fleets


@dataclass(frozen=True, slots=True)
class FleetState:
    """How a fleet stands apart from its record."""

    running: bool = False
    stopping: bool = False  # its processes are ending
    finished: bool = False  # its lead reported, or its money ran out, while it ran
    budget_usd: float = 0.0  # the most it may spend


@dataclass(frozen=True, slots=True)
class Happened:
    """Something happened in a fleet."""

    fleet: str
    event: FleetEvent


@dataclass(frozen=True, slots=True)
class Updated:
    """A fleet's record or state changed: `Service.fleet` and `Service.state` are current."""

    fleet: str


@dataclass(frozen=True, slots=True)
class Listed:
    """A project or a fleet was added, renamed or removed."""


type Update = Happened | Updated | Listed


class Service(abc.ABC):
    # ── Projects ─────────────────────────────────────────────────────────

    @abc.abstractmethod
    async def projects(self) -> list[ProjectInfo]:
        """Every project, newest first."""

    @abc.abstractmethod
    async def create_project(self, name: str, work: str) -> ProjectInfo: ...

    @abc.abstractmethod
    async def rename_project(self, project: str, name: str) -> None: ...

    @abc.abstractmethod
    async def delete_project(self, project: str) -> None:
        """Stop its fleets and remove its records; the folder they worked in stays."""

    @abc.abstractmethod
    async def draft(self, project: str, description: str) -> Draft:
        """Have one agent propose the project's fleets from what it should do. Nothing is
        started. `draft.DraftError` when there is no proposal, or it was dropped."""

    @abc.abstractmethod
    async def cancel_draft(self, project: str) -> None:
        """Drop the draft under way for the project, if there is one."""

    # ── Fleets ───────────────────────────────────────────────────────────

    @abc.abstractmethod
    async def fleets(self, project: str) -> list[str]:
        """The project's fleets that can be read, oldest first. Each is being watched."""

    @abc.abstractmethod
    async def add_fleet(self, project: str, name: str, task: str, *, ongoing: bool = True,
                        work: str | None = None, budget_usd: float | None = None) -> str:
        """A new fleet. With a task, a lead starts on it at once; with none the fleet has
        no agents and runs nothing until it is given some and started. `work`: the
        project's, unless given. `budget_usd`: the settings', unless given."""

    @abc.abstractmethod
    async def watch(self, fleet: str) -> None:
        """Keep the fleet's record and state current here: `fleet` and `state` answer for
        it from now on, and `Updated` says when they changed."""

    @abc.abstractmethod
    def fleet(self, fleet: str) -> Fleet | None:
        """A watched fleet's record, kept current; None for one that is not watched. It
        is read, never changed: changes go through this contract."""

    @abc.abstractmethod
    def state(self, fleet: str) -> FleetState: ...

    @abc.abstractmethod
    async def history(self, fleet: str) -> list[tuple[float, FleetEvent]]:
        """What happened in a watched fleet so far, oldest first, each with when it did
        (seconds since the epoch)."""

    @abc.abstractmethod
    async def start(self, fleet: str, *, budget_usd: float | None = None) -> None:
        """Start a fleet that is not running: every agent that was at work carries on,
        and every agent placed meanwhile starts. `budget_usd`: a new limit for it, which
        one that stopped at its limit needs."""

    @abc.abstractmethod
    async def stop(self, fleet: str) -> None:
        """End the fleet's processes; it can be started again."""

    @abc.abstractmethod
    async def rename_fleet(self, fleet: str, name: str) -> None: ...

    @abc.abstractmethod
    async def delete_fleet(self, fleet: str) -> None:
        """Stop it and remove its record; the folder it worked in stays."""

    # ── Agents ───────────────────────────────────────────────────────────

    @abc.abstractmethod
    async def add_agent(self, fleet: str, supervisor: str | None, name: str, task: str, *,
                        ongoing: bool = False, model: str | None = None) -> str:
        """An agent under `supervisor`, or the lead of a fleet that has none. In a fleet
        that runs it starts at once; otherwise with the fleet. Returns its id."""

    @abc.abstractmethod
    async def remove_agent(self, fleet: str, unit: str) -> None:
        """Take the agent and everything under it out of the fleet."""

    @abc.abstractmethod
    async def rename_agent(self, fleet: str, unit: str, name: str) -> None: ...

    @abc.abstractmethod
    async def set_task(self, fleet: str, unit: str, task: str) -> None:
        """Another task for the agent, in the conversation it has. One that has run can
        only be given one while its fleet runs."""

    @abc.abstractmethod
    async def set_model(self, fleet: str, unit: str, model: str) -> None:
        """The model the agent runs on; "": its rank's."""

    @abc.abstractmethod
    async def say(self, fleet: str, text: str, unit: str | None = None, *,
                  files: Sequence[str] = (), spoken: bool = False) -> None:
        """The person's message to the agent, or to the lead, with files. A fleet that is
        not running is started for it."""

    @abc.abstractmethod
    async def approve(self, fleet: str, unit: str, request_id: str, allow: bool) -> None:
        """Answer an `events.Approval`."""

    # ── Settings, and what happens ───────────────────────────────────────

    @abc.abstractmethod
    async def settings(self) -> FleetConfig:
        """What a new fleet gets."""

    @abc.abstractmethod
    async def save_settings(self, **changes: Any) -> FleetConfig:
        """`config.save` on KFM's settings."""

    @abc.abstractmethod
    def subscribe(self, listener: Callable[[Update], None]) -> Callable[[], None]:
        """Call `listener` with everything that happens from now on, in order. Returns
        what ends it."""

    @abc.abstractmethod
    async def aclose(self) -> None:
        """Let go of the service. The one in this process stops its fleets; a client of
        `kfm serve` leaves them running."""
