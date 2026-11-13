"""The fleet manager: it runs every unit's process and carries everything between them.
A unit's report, question or failure reaches its supervisor as a message; what the lead
reports or asks reaches the person. Units never talk to each other directly."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Callable
from pathlib import Path

from kfm import api, harnesses, log, units
from kfm import config as settings
from kfm.api import Harness, Instruction, Session, UnitError, UnitEvent, UnitSpec
from kfm.config import FleetConfig
from kfm.events import (
    Answered,
    Approval,
    Changed,
    Delivered,
    FleetEvent,
    Notice,
    Said,
    Told,
)
from kfm.fleet import Fleet, FleetError, Unit
from kfm.tools import ToolServer, fleet_tools

logger = logging.getLogger(__name__)

STOP_GRACE_S = 10.0  # for a unit to end by itself when it is closed
_OVER = ("done", "idle", "failed", "stopped")


def lay_out(config: FleetConfig, folder: Path, work: Path, name: str) -> Fleet:
    """A fleet with no agents yet, for the person to build by hand: its record, and
    nothing running. `Manager.resumed` starts it. Blocking."""
    fleet = Fleet(config.top_rank, config.max_units, name)
    fleet.work, fleet.settings = str(work), settings.record(config)
    folder.mkdir(parents=True, exist_ok=True)
    fleet.save(folder)
    return fleet


def place(fleet: Fleet, folder: Path, supervisor: Unit | None, name: str, task: str, *,
          ongoing: bool = False, model: str | None = None) -> Unit:
    """The person puts an agent into a fleet that is not running: under `supervisor`, or
    as the lead. It starts when the fleet does. FleetError when the hierarchy or the
    fleet's limits do not allow it. Blocking."""
    if supervisor is not None and fleet.units.get(supervisor.id) is not supervisor:
        raise FleetError("Add the lead first.")
    unit = fleet.add(name, task, supervisor, ongoing=ongoing,
                     budget_usd=settings.from_record(fleet.settings).unit_budget_usd)
    unit.model, unit.state = model or None, "planned"
    fleet.save(folder)
    return unit


class Manager:
    def __init__(self, config: FleetConfig, folder: Path, work: Path, *,
                 harness: Harness | None = None,
                 on_event: Callable[[FleetEvent], None] = lambda event: None,
                 fleet: Fleet | None = None, name: str = "") -> None:
        """`name`: what a new fleet is called. `harness`: the agent program the units run; None loads the one the settings
        name (ValueError when it cannot be)."""
        self.config, self.folder, self.work = config, folder, work
        self._resuming = fleet is not None
        self.fleet = fleet or Fleet(config.top_rank, config.max_units, name)
        self.fleet.work, self.fleet.settings = str(work), settings.record(config)
        self.finished = asyncio.Event()  # the lead reported, or the budget ran out
        self._harness = harness or harnesses.load(config.harness, config.harness_options)
        self._words = self._harness.vocabulary
        self._on_event = on_event
        self._log = log.Log(folder)  # every event, for whoever opens the fleet later
        self._tools = ToolServer(fleet_tools(self))
        self._sessions: dict[str, Session] = {}
        self._opening: dict[str, asyncio.Lock] = {}  # one start of a unit's process at a time
        # Whether a unit's instruction stood when its last turn ended.
        self._carries_on: dict[str, bool] = {}
        # Units told more after their goal ended, and what each wrote last since: with no
        # goal left to finish, that is its report.
        self._answering: set[str] = set()
        self._last_said: dict[str, str] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._over_budget = False
        self._saving = asyncio.Lock()

    @classmethod
    def resumed(cls, folder: Path, *, harness: Harness | None = None,
                on_event: Callable[[FleetEvent], None] = lambda event: None,
                **overrides: object) -> Manager:
        """A manager for the stopped fleet in `folder`, with the settings it ran with and
        `overrides` that are not None. `start` then starts every unit that was at work
        and every unit placed while it was stopped.
        FleetError when the fleet is running or has spent its limit. Blocking."""
        fleet = Fleet.load(folder)
        if fleet.running:
            raise FleetError(f"The fleet in {folder} is running (process "
                             f"{fleet.manager_pid}).")
        config = settings.from_record(fleet.settings, **overrides)
        if fleet.spent_usd >= config.budget_usd:
            raise FleetError(f"The fleet has spent ${fleet.spent_usd:.2f} of its "
                             f"${config.budget_usd:.2f}: give it more with --budget.")
        return cls(config, folder, Path(fleet.work), harness=harness, on_event=on_event,
                   fleet=fleet)

    async def start(self) -> None:
        await asyncio.to_thread(self.folder.mkdir, parents=True, exist_ok=True)
        await self._tools.start()
        self.fleet.manager_pid = os.getpid()
        await self._save()
        if self._resuming:
            # Every unit is open before any is told: a supervisor that acts at once finds
            # its agents there.
            everyone = list(self.fleet.units.values())
            placed = [unit for unit in everyone if unit.state == "planned"]
            back = [unit for unit in everyone if unit.active and await self._reopen(unit)]
            ready = [unit for unit in placed if unit.task and await self._come_up(unit)]
            for unit in back:
                await self._carry_on(unit)
            for unit in placed:  # a supervisor before its agents
                supervisor = self.fleet.supervisor_of(unit)
                if unit in ready:
                    await self._begin(unit)
                elif not unit.task:
                    self._set(unit, "idle")  # until it is told what to do
                # A supervisor that begins now, or later, reads of its agents in its brief.
                if (supervisor is not None and supervisor not in placed
                        and supervisor.session is not None):
                    self._spawn(self._tell_quietly(
                        supervisor, units.hired_note(unit, self._words)))

    async def aclose(self) -> None:
        """Stop the fleet's processes. The record keeps every unit's state, so that
        `resumed` knows which were at work."""
        closing = [self._close(unit) for unit in self.fleet.units.values()]
        await asyncio.gather(*closing, return_exceptions=True)
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._tools.aclose()
        await self._log.aclose()
        self.fleet.manager_pid = None
        await self._save()

    # ── What the person does ─────────────────────────────────────────────

    async def lead(self, task: str, *, ongoing: bool = False, name: str = "") -> Unit:
        """Start the fleet: its lead, on the person's task."""
        unit = self.fleet.add(name, task, None, ongoing=ongoing,
                              budget_usd=self.config.unit_budget_usd)
        await self._start(unit)
        return unit

    async def say(self, text: str) -> None:
        """The person's message, to the lead."""
        if (lead := self.fleet.lead) is None:
            raise FleetError("The fleet has no lead.")
        await self.tell(lead, text)
        self._emit(Told(text))

    async def hire(self, supervisor: Unit, name: str, task: str, *, ongoing: bool = False,
                   model: str | None = None) -> Unit:
        """The person puts an agent under `supervisor`, on a model of its own if one is
        named; the supervisor is told it has it. FleetError when the hierarchy or the
        fleet's limits do not allow it."""
        if supervisor.id not in self.fleet.units:
            raise FleetError("The fleet has not started.")
        unit = await self.delegate(supervisor, name, task, ongoing=ongoing, model=model)
        self._emit(Notice(f"You added {unit.called} under {supervisor.called}."))
        if supervisor.session is not None:  # one that has not begun reads it in its brief
            self._spawn(self._tell_quietly(supervisor, units.hired_note(unit, self._words)))
        return unit

    async def set_model(self, unit: Unit, model: str | None) -> None:
        """The model the unit runs on from now; None or "": its rank's or team's. A unit
        at work is started again on it, in its own conversation."""
        unit.model = model or None
        await self._save()
        if unit.id not in self._sessions:
            return  # its process starts on it the next time
        await self._close(unit)
        if unit.active and not self._over_budget:
            try:
                await (await self._session(unit)).send(units.MODEL_CHANGED)
            except UnitError as e:
                await self._could_not_start(unit, e)

    async def rename(self, name: str, unit: Unit | None = None) -> None:
        """Another name for the unit, or for the fleet. Nobody is told: agents know each
        other by id, and what reaches a supervisor from now on carries the new name."""
        if unit is not None:
            unit.name = name
        else:
            self.fleet.name = name
        await self._save()

    async def approve(self, unit: Unit, request_id: str, allow: bool) -> None:
        if (session := self._sessions.get(unit.id)) is not None:
            await session.approve(request_id, allow)
            self._emit(Answered(unit, request_id, allow))

    # ── What units do, through the fleet tools ───────────────────────────

    async def delegate(self, supervisor: Unit, name: str, task: str, *,
                       team: str | None = None, ongoing: bool = False,
                       budget_usd: float | None = None, model: str | None = None) -> Unit:
        if self._over_budget:
            raise FleetError("The fleet has reached its spending limit.")
        budget = min(budget_usd or self.config.unit_budget_usd, self.config.unit_budget_usd)
        unit = self.fleet.add(name, task, supervisor, team=team, ongoing=ongoing,
                              budget_usd=budget)
        unit.model = model or None
        if unit.task:
            await self._start(unit)
        else:
            self._set(unit, "idle")  # until it is told what to do
        return unit

    async def tell(self, unit: Unit, text: str, *, task: str | None = None) -> None:
        """Put a message into the unit's conversation, starting it again if it is not
        running: it then works on what the message says. A unit that has no task yet
        starts on the message as its task (`task`: its words alone, when `text` wraps
        them). FleetError or UnitError when it cannot be."""
        if self._over_budget:
            raise FleetError("The fleet has reached its spending limit.")
        if unit.session is None:
            if unit.task or unit.state != "idle":
                raise FleetError(f"{unit.label} never started.")
            unit.task = task or text
            await self._start(unit)
            return
        session = await self._session(unit)
        unit.question = None  # this is its answer
        if unit.state in _OVER:
            ended = unit.state in ("done", "idle")  # its goal is reached or gone
            unit.report = unit.failure = None
            self._set(unit, "working")
            if ended and not self._carries_on.get(unit.id, False):
                self._answering.add(unit.id)
                self._last_said.pop(unit.id, None)
                note = units.answer_note(self.fleet.supervisor_of(unit), self._words)
                text = f"{text}\n\n{note}"
        await session.send(text)

    async def update(self, unit: Unit, text: str) -> None:
        """A unit's update for its supervisor, without ending its work."""
        self._deliver(unit, "update", f"[Update from {unit.label}] {text}", text)

    async def stop(self, unit: Unit) -> None:
        """Stop the unit and everything under it."""
        for each in self.fleet.subtree(unit):
            if each.active:
                self._set(each, "stopped")
            await self._close(each)

    # ── Units ────────────────────────────────────────────────────────────

    async def _start(self, unit: Unit) -> None:
        if await self._come_up(unit):
            await self._begin(unit)

    async def _come_up(self, unit: Unit) -> bool:
        """Start the unit's process, before it has its task. False: it could not."""
        unit.state = "starting"
        self._emit(Changed(unit, unit.state))
        try:
            await self._open(unit, resume=False)
        except UnitError as e:
            await self._could_not_start(unit, e)
            return False
        return True

    async def _begin(self, unit: Unit) -> None:
        """Give the unit its task. The agents it has before it begins are the person's,
        and are named in its brief."""
        under = self.fleet.subordinates(unit)
        brief = units.brief(unit, self.fleet.supervisor_of(unit), under, self._words)
        try:
            if (session := self._sessions.get(unit.id)) is None:
                raise UnitError("Its process ended.")
            unit.session = await session.begin(
                Instruction(brief, unit.ongoing, self.config.check_in_s))
        except UnitError as e:
            await self._could_not_start(unit, e)
            return
        self._carries_on[unit.id] = True
        if unit.state == "starting":
            self._set(unit, "working")

    async def _reopen(self, unit: Unit) -> bool:
        """Open a unit that was at work when the fleet stopped, on its session, where its
        instruction still stands. False: it starts from the beginning or not at all."""
        if unit.session is None:  # stopped before it had its task
            await self._start(unit)
            return False
        self._carries_on[unit.id] = True
        unit.question = None  # an open question does not outlive the unit's process
        self._emit(Changed(unit, unit.state))
        try:
            await self._session(unit)
        except UnitError as e:
            await self._could_not_start(unit, e)
            return False
        self._set(unit, "working")
        return True

    async def _carry_on(self, unit: Unit) -> None:
        """A unit opened on a session waits for a message: say what happened."""
        try:
            await (await self._session(unit)).send(units.RESUMED)
        except UnitError as e:
            await self._could_not_start(unit, e)

    async def _session(self, unit: Unit) -> Session:
        """The unit's open session; opened on its saved one when its process is not
        running."""
        async with self._opening.setdefault(unit.id, asyncio.Lock()):
            return self._sessions.get(unit.id) or await self._open(unit, resume=True)

    async def _could_not_start(self, unit: Unit, error: UnitError) -> None:
        unit.failure = str(error) or "Its process ended."
        self._set(unit, "failed")
        await self._close(unit)

    async def _open(self, unit: Unit, *, resume: bool) -> Session:
        spec = UnitSpec(
            unit.id, unit.called, unit.rank, self.folder / "units" / unit.id, self.work,
            unit.model or self.config.model_for(unit.rank, unit.team),
            self.config.permissions,
            unit.budget_usd, f"{self._tools.url}?unit={unit.token}")
        session = await self._harness.open(spec, lambda event: self._event(unit, event),
                                           unit.session if resume else None)
        self._sessions[unit.id] = session
        return session

    async def _close(self, unit: Unit) -> None:
        if (session := self._sessions.pop(unit.id, None)) is not None:
            await session.close(STOP_GRACE_S)

    # ── What a unit says ─────────────────────────────────────────────────

    def _event(self, unit: Unit, event: UnitEvent) -> None:
        match event:
            case api.Working():
                if unit.state != "stopped":
                    self._set(unit, "working")
            case api.Rested(carries_on=carries_on):
                self._carries_on[unit.id] = carries_on
                if unit.id in self._answering:
                    self._answering.discard(unit.id)
                    if unit.report is None and not carries_on:
                        unit.report = self._last_said.get(unit.id)
                if unit.state == "stopped":
                    return
                if carries_on:  # between check-ins, or until its question is answered
                    self._set(unit, "waiting" if unit.question is not None else "working")
                else:
                    self._set(unit, "done" if unit.report is not None else "idle")
                if unit.state != "working":
                    self._turn_ended(unit)
            case api.Said(text=text):
                self._last_said[unit.id] = text
                self._emit(Said(unit, text))
            case api.Reported(text=text):
                unit.report = text
            case api.Asked(question=question):
                unit.question = question
            case api.Spent(usd=usd):
                unit.spent_usd = usd
                self._spawn(self._save())
                self._check_budget()
            case api.ApprovalNeeded(request_id=request_id, tool=tool, arguments=arguments):
                self._emit(Approval(unit, request_id, tool, arguments))
            case api.Failed(message=message):
                unit.failure = message
                if unit.state != "stopped":
                    self._set(unit, "failed")
                    self._turn_ended(unit)
            case api.Exited(why=why):
                if self._sessions.pop(unit.id, None) is not None and unit.active:
                    unit.failure = why
                    self._set(unit, "failed")
                    self._turn_ended(unit)

    def _turn_ended(self, unit: Unit) -> None:
        """The unit stopped working: tell its supervisor, or the person, why."""
        match unit.state:
            case "done":
                self._deliver(unit, "report", f"[Report from {unit.label}] {unit.report}",
                              unit.report or "")
                self._spawn(self._close(unit))
                if unit.supervisor is None:
                    self.finished.set()
            case "waiting":
                self._deliver(
                    unit, "question",
                    f"[Question from {unit.label}] {unit.question}\nAnswer with "
                    f"{self._words.tool('message')}(agent=\"{unit.id}\", text=...).",
                    unit.question or "")
            case "failed":
                why = unit.failure or "It stopped with an error."
                self._deliver(
                    unit, "failure",
                    f"[{unit.label} failed] {why}\nTell it to carry on with "
                    f"{self._words.tool('message')}, hand the task to another agent, or ask your "
                    "own supervisor.", why)
            case "idle":
                self._deliver(
                    unit, "stopped",
                    f"[{unit.label} stopped without a report] Ask it what it did with "
                    f"{self._words.tool('message')}, or hand the task to another agent.",
                    "It stopped without a report.")

    def _deliver(self, unit: Unit, kind: str, message: str, text: str) -> None:
        supervisor = self.fleet.supervisor_of(unit)
        self._emit(Delivered(unit, supervisor, kind, text))
        if supervisor is not None:
            self._spawn(self._tell_quietly(supervisor, message))

    async def _tell_quietly(self, unit: Unit, text: str) -> None:
        try:
            await self.tell(unit, text)
        except (FleetError, UnitError) as e:
            self._emit(Notice(f"A message for {unit.label} could not be delivered: {e}"))

    def _check_budget(self) -> None:
        if self._over_budget or self.fleet.spent_usd < self.config.budget_usd:
            return
        self._over_budget = True
        self._emit(Notice(f"The fleet reached its spending limit of "
                          f"${self.config.budget_usd:.2f}; every agent is stopped."))
        self._spawn(self._stop_all())

    async def _stop_all(self) -> None:
        """Out of money: every process ends; the units stay as they were in the record, to
        carry on when the fleet is resumed with more."""
        closing = [self._close(unit) for unit in self.fleet.units.values()]
        await asyncio.gather(*closing, return_exceptions=True)
        self.finished.set()

    # ── Bookkeeping ──────────────────────────────────────────────────────

    def _set(self, unit: Unit, state: str) -> None:
        if unit.state != state:
            unit.state = state
            self._emit(Changed(unit, unit.state))
            self._spawn(self._save())

    async def _save(self) -> None:
        async with self._saving:
            with contextlib.suppress(OSError):
                await asyncio.to_thread(self.fleet.save, self.folder)

    def _emit(self, event: FleetEvent) -> None:
        self._log.add(event)
        try:
            self._on_event(event)
        except Exception:
            logger.exception("showing a fleet event failed")

    def _spawn(self, work: object) -> None:
        task: asyncio.Task[None] = asyncio.ensure_future(work)  # type: ignore[arg-type]
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
