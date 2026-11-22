"""What a frontend can do with KFM, and what it is told: the contract between KFM and
whatever shows it to a person (the window, the command line). Normative for frontends, as
`kfm.api` is for agent programs. A frontend takes a `Service` and nothing else of KFM's
workings: `kfm.local` is the service in this process, `kfm.remote` the one that
`kfm serve` runs, reached over a socket.

Ids are text: a project's is its folder's name, a fleet's is "<project>/<its folder's
name>", a unit's is the one in the fleet's record ("K4001").

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
from kfm.events import Approval, FleetEvent
from kfm.fleet import Fleet

VERSION = 9  # of this contract; a service and a client of different versions do not talk


@dataclass(frozen=True, slots=True)
class ProjectInfo:
    id: str
    name: str
    work: str  # the folder its fleets work in, unless one is given its own
    fleets: int  # its teams; its secretary is not one
    running: int  # of its teams
    secretary: str = ""  # the id of its secretary, a fleet of one agent
    waiting: int = 0  # approvals and leads' questions that wait for the person, in the
    # fleets of it that the service has read


@dataclass(frozen=True, slots=True)
class FleetState:
    """How a fleet stands, worked out once, for every frontend to show."""

    running: bool = False
    stopping: bool = False  # its processes are ending
    finished: bool = False  # its lead reported, or its money ran out, when it last ran
    budget_usd: float = 0.0  # the most it may spend
    # In a word: running, stopping, stopped, "not started" (no agent has begun), or
    # finished (it runs, its lead has reported and nothing is at work).
    status: str = "not started"
    can_start: bool = False  # it is stopped, with agents that would carry on or begin
    has_run: bool = False  # one of its agents has begun
    # What its agents ask the person's approval for and wait on, oldest first.
    approvals: tuple[Approval, ...] = ()


@dataclass(frozen=True, slots=True)
class Happened:
    """Something happened in a fleet. The fleet's record already says who the units it
    names are: an `Updated` comes first when it changed."""

    fleet: str
    event: FleetEvent
    number: int  # its place in the fleet's history, from 1


@dataclass(frozen=True, slots=True)
class Updated:
    """A fleet's record or state changed: `Service.fleet` and `Service.state` answer with
    what they are now."""

    fleet: str


@dataclass(frozen=True, slots=True)
class Listed:
    """A project or a fleet was added, renamed or removed."""


@dataclass(frozen=True, slots=True)
class Proposed:
    """A project's secretary put teams to the person: `Service.proposal` has them."""

    project: str


@dataclass(frozen=True, slots=True)
class Live:
    """What an agent is doing as it does it: what it has thought and written so far in
    the request to its model it is on, and the tool it is using; each "" for nothing. A
    few a second at most, and not in the fleet's history: for watching."""

    fleet: str
    unit: str
    thinking: str
    writing: str
    doing: str


@dataclass(frozen=True, slots=True)
class Within:
    """Something happened in an agent's own Kaden conversation, for whoever follows it
    (Service.follow): an event of Kaden's headless channel as that carries it
    ({"seq": ..., "event": ...}), or None when the agent's process ended. Not in the
    fleet's history: the agent's Kaden keeps its conversation."""

    fleet: str
    unit: str
    event: dict[str, Any] | None


type Update = Happened | Updated | Listed | Proposed | Live | Within


class Service(abc.ABC):
    # It was started before the code this frontend runs was installed, and so runs what
    # was there before: only a service in another process can be.
    stale: bool = False
    # The fleets that ran in a service of another version, which was ended for this one
    # when the frontend attached: they are stopped, and can be resumed.
    interrupted: Sequence[str] = ()

    # ── Projects ─────────────────────────────────────────────────────────

    @abc.abstractmethod
    async def projects(self) -> list[ProjectInfo]:
        """Every project, newest first."""

    @abc.abstractmethod
    async def unreadable(self) -> list[tuple[str, str]]:
        """The projects another version of KFM made, which this one cannot read: each
        its id and its name. `delete_project` is all that can be done with one."""

    @abc.abstractmethod
    async def create_project(self, name: str, work: str,
                             description: str = "") -> ProjectInfo:
        """A project with its secretary and no teams. `description`: what the project
        should do, said to the secretary, which starts her on it."""

    @abc.abstractmethod
    async def rename_project(self, project: str, name: str) -> None: ...

    @abc.abstractmethod
    async def delete_project(self, project: str) -> None:
        """Stop its fleets and remove its records; the folder they worked in stays."""

    @abc.abstractmethod
    async def proposal(self, project: str) -> Draft | None:
        """The teams the project's secretary last put to the person, until settled."""

    @abc.abstractmethod
    async def settle_proposal(self, project: str) -> None:
        """The person started what they wanted of the proposal, or dropped it."""

    # ── Fleets ───────────────────────────────────────────────────────────

    @abc.abstractmethod
    async def fleets(self, project: str) -> list[str]:
        """The project's fleets that can be read, oldest first. Each is being watched."""

    @abc.abstractmethod
    async def add_fleet(self, project: str, name: str, task: str, *, ongoing: bool = False,
                        work: str | None = None, budget_usd: float | None = None,
                        model: str | None = None) -> str:
        """A new fleet. With a task, a lead starts on it at once; with none the fleet has
        no agents and runs nothing until it is given some and started. `work`: the
        project's, unless given. `budget_usd`: the settings', unless given. `model`: the
        lead's, which whoever starts it names where the settings say so (ValueError when
        it is missing or not allowed there, or given where models go by rank)."""

    @abc.abstractmethod
    async def watch(self, fleet: str) -> None:
        """Keep the fleet's record and state current here: `fleet` and `state` answer for
        it from now on, and `Updated` says when they changed."""

    @abc.abstractmethod
    def fleet(self, fleet: str) -> Fleet | None:
        """A watched fleet's record as it is now; None for one that is not watched. It is
        read, never changed or kept: changes go through this contract, and after an
        `Updated` the record is asked for again."""

    @abc.abstractmethod
    def state(self, fleet: str) -> FleetState: ...

    @abc.abstractmethod
    async def history(self, fleet: str) -> tuple[list[FleetEvent], int]:
        """What happened in a watched fleet so far, oldest first, without what was about
        an agent since removed; and the number of the last event it covers: a `Happened`
        with a higher one came after it."""

    @abc.abstractmethod
    async def start(self, fleet: str, *, budget_usd: float | None = None) -> None:
        """Start a fleet that is not running: every agent that was at work carries on,
        and every agent that has a task and has not begun begins. `budget_usd`: a new
        limit for it, which one that stopped at its limit needs."""

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
        """An agent under `supervisor`, or the lead of a fleet that has none. With a task
        it begins when the fleet runs; with none, on the first message it gets. Returns
        its id."""

    @abc.abstractmethod
    async def remove_agent(self, fleet: str, unit: str) -> None:
        """Take the agent and everything under it out of the fleet."""

    @abc.abstractmethod
    async def rename_agent(self, fleet: str, unit: str, name: str) -> None: ...

    @abc.abstractmethod
    async def set_task(self, fleet: str, unit: str, task: str, *,
                       ongoing: bool | None = None) -> None:
        """Another task for the agent, in the conversation it has; `ongoing` makes it a
        duty or a task, None leaves it what it is. In a fleet that is not running it
        works on it when the fleet starts."""

    @abc.abstractmethod
    async def set_model(self, fleet: str, unit: str, model: str) -> None:
        """The model the agent runs on; "": its rank's."""

    @abc.abstractmethod
    async def say(self, fleet: str, text: str, unit: str | None = None, *,
                  files: Sequence[str] = (), spoken: bool = False) -> None:
        """The person's message to the agent, or to the lead, with files. A fleet that is
        not running is started for it."""

    @abc.abstractmethod
    async def approve(self, fleet: str, unit: str, request_id: str, allow: bool, *,
                      always: bool = False) -> None:
        """Answer an `events.Approval`. `always` with `allow`: that agent no longer asks
        for actions of that kind."""

    # ── An agent's own conversation ──────────────────────────────────────

    @abc.abstractmethod
    async def follow(self, fleet: str, unit: str) -> bool:
        """Have what happens in an agent's own Kaden conversation sent as `Within`,
        and its process kept up meanwhile; it is started on its conversation if it is
        closed, which asks its model nothing. After a `Within` with no event, follow
        again. False when there is nothing to follow: the fleet does not run, or the
        agent has not begun."""

    @abc.abstractmethod
    async def unfollow(self, fleet: str, unit: str) -> None:
        """Stop that. Nothing when it was not followed."""

    @abc.abstractmethod
    async def ask(self, fleet: str, unit: str, method: str, params: dict[str, Any]) -> Any:
        """A request to a followed agent's own Kaden, of its headless channel and in
        its JSON, and the answer: how a frontend reads its conversation. UnitError when
        its process is not up or refuses."""

    # ── Settings, and what happens ───────────────────────────────────────

    @abc.abstractmethod
    async def models(self) -> list[tuple[str, str, str]]:
        """The models the agent program offers, for a person to choose from: each its id,
        its name, and what else is known of it (its price) or ""; none when it cannot
        say."""

    @abc.abstractmethod
    async def settings(self) -> FleetConfig:
        """What a new fleet gets."""

    @abc.abstractmethod
    async def save_settings(self, **changes: Any) -> FleetConfig:
        """`config.save` on KFM's settings. Every fleet there is then follows them, but
        for its own limit: an agent whose model, permissions or limit changed and whose
        process is up is started again on them, in its own conversation."""

    @abc.abstractmethod
    def subscribe(self, listener: Callable[[Update], None]) -> Callable[[], None]:
        """Call `listener` with everything that happens from now on, in order. Returns
        what ends it."""

    @abc.abstractmethod
    async def end(self) -> None:
        """End the service itself, for everyone attached to it: its fleets stop and can
        be resumed. Returns once it is gone; whoever attaches next starts another, on
        the code that is then installed."""

    @abc.abstractmethod
    async def aclose(self) -> None:
        """Let go of the service. The one in this process stops its fleets; a client of
        `kfm serve` leaves them running."""
