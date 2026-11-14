"""The fleet manager: it holds a fleet, runs every unit's process and carries everything
between them.
A unit's report, question or failure reaches its supervisor as a message; what the lead
reports or asks reaches the person. Units never talk to each other directly."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from kfm import api, harnesses, log, units
from kfm import config as settings
from kfm.api import (
    Harness,
    Instruction,
    Session,
    UnitError,
    UnitEvent,
    UnitSpec,
    Vocabulary,
)
from kfm.config import FleetConfig
from kfm.events import (
    Answered,
    Approval,
    Changed,
    Delivered,
    FleetEvent,
    Instructed,
    Notice,
    Said,
    Told,
)
from kfm.fleet import Fleet, FleetError, Unit
from kfm.tools import ToolServer, fleet_tools

logger = logging.getLogger(__name__)

STOP_GRACE_S = 10.0  # for a unit to end by itself when it is closed
_OVER = ("done", "idle", "failed", "stopped")


def _forget(folder: Path, gone: Sequence[Unit]) -> None:
    """Delete what units taken out of the fleet in `folder` kept. Blocking."""
    for unit in gone:
        shutil.rmtree(folder / "units" / unit.id, ignore_errors=True)


class Manager:
    """A fleet, whether or not it runs: its record and its rules always, its units'
    processes only between `start` and `stop`. Whatever changes the fleet goes through
    here, so each change is written once."""

    def __init__(self, folder: Path, fleet: Fleet, *, harness: Harness | None = None,
                 on_event: Callable[[FleetEvent], None] = lambda event: None,
                 on_change: Callable[[], None] = lambda: None) -> None:
        """`harness`: the agent program the units run; None: the one the fleet's settings
        name, loaded when it starts. `on_change`: called when the record, or whether the
        fleet runs, changed."""
        self.folder, self.fleet, self.work = folder, fleet, Path(fleet.work)
        self.config = settings.from_record(fleet.settings)
        self.running = False  # its processes are up, or ending
        self.stopping = False  # they are ending: nothing more is started
        self.finished = asyncio.Event()  # the lead reported, or the budget ran out
        self._own_harness, self._harness = harness, harness
        self._on_event, self._on_change = on_event, on_change
        self._log = log.Log(folder)  # every event, for whoever opens the fleet later
        self._tools: ToolServer  # the fleet tools, made each time it starts
        self._switch = asyncio.Lock()  # one start or stop at a time
        self._sessions: dict[str, Session] = {}
        self._opening: dict[str, asyncio.Lock] = {}  # one start of a unit's process at a time
        # Whether a unit's instruction stood when its last turn ended.
        self._carries_on: dict[str, bool] = {}
        # Units told more after their goal ended, and what each wrote last since: with no
        # goal left to finish, that is its report.
        self._answering: set[str] = set()
        self._last_said: dict[str, str] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._saving = asyncio.Lock()

    @classmethod
    def new(cls, config: FleetConfig, folder: Path, work: Path, name: str = "",
            **how: Any) -> Manager:
        """A new fleet in `folder`, with no agents and nothing running. Blocking."""
        fleet = Fleet(config.top_rank, config.max_units, name)
        fleet.work, fleet.settings = str(work), settings.record(config)
        folder.mkdir(parents=True, exist_ok=True)
        fleet.save(folder)
        return cls(folder, fleet, **how)

    @classmethod
    def load(cls, folder: Path, **how: Any) -> Manager:
        """The fleet kept in `folder`, not running. ValueError when its record cannot be
        read. Blocking."""
        return cls(folder, Fleet.load(folder), **how)

    @property
    def up(self) -> bool:
        """Whether units' processes can be started now."""
        return self.running and not self.stopping

    @property
    def _words(self) -> Vocabulary:
        assert self._harness is not None  # words are written to units, which run
        return self._harness.vocabulary

    async def start(self, **overrides: object) -> None:
        """Start the fleet's processes, with `overrides` of its settings that are not
        None: every unit that was at work carries on, and every unit that has a task and
        has not begun begins. Nothing when it runs. FleetError when another process runs
        it or it has spent its limit; ValueError for a harness that cannot be loaded."""
        async with self._switch:
            if self.running:
                return
            if self.fleet.running:
                raise FleetError(f"The fleet in {self.folder} is running (process "
                                 f"{self.fleet.manager_pid}).")
            config = settings.from_record(self.fleet.settings, **overrides)
            if self.fleet.spent_usd >= config.budget_usd:
                raise FleetError(f"The fleet has spent ${self.fleet.spent_usd:.2f} of its "
                                 f"${config.budget_usd:.2f}: give it more with --budget.")
            self._harness = self._own_harness or await asyncio.to_thread(
                harnesses.load, config.harness, config.harness_options)
            self.config, self.fleet.settings = config, settings.record(config)
            self._tools = ToolServer(fleet_tools(self))
            await self._tools.start()
            self.fleet.manager_pid = os.getpid()
            self.running = True
            self.finished.clear()
            await self._save()
            everyone = list(self.fleet.units.values())
            for unit in everyone:
                if unit.active and unit.session is None:  # stopped while its process came up
                    unit.state = "planned"
            # Every unit is open before any is told: a supervisor that acts at once finds
            # its agents there.
            back = [unit for unit in everyone if unit.active and await self._reopen(unit)]
            for unit in back:
                await self._carry_on(unit)
            await self._launch(everyone)

    async def stop(self) -> None:
        """End the fleet's processes. The record keeps every unit's state, so that
        `start` knows which were at work."""
        async with self._switch:
            if not self.running:
                return
            self.stopping = True
            self._on_change()
            try:
                await self._close_all()
                mine = [task for task in self._tasks if task is not asyncio.current_task()]
                for task in mine:
                    task.cancel()
                await asyncio.gather(*mine, return_exceptions=True)
                await self._tools.aclose()
                await self._close_all()  # what a tool call under way opened meanwhile
            finally:
                self.running = self.stopping = False
                self.fleet.manager_pid = None
                await self._save()

    async def flush(self) -> None:
        """Return once everything that happened so far is in the fleet's record."""
        await self._log.flush()

    async def aclose(self) -> None:
        """Stop the fleet and let go of it."""
        await self.stop()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._log.aclose()

    # ── What the person does ─────────────────────────────────────────────

    async def add(self, supervisor: Unit | None, name: str, task: str, *,
                  ongoing: bool = False, model: str | None = None) -> Unit:
        """The person puts an agent into the fleet: under `supervisor`, or as its lead,
        on a model of its own if one is named. With a task it begins when the fleet
        runs; with none, on the first message it gets. A supervisor that has begun is
        told it has it. FleetError when the hierarchy or the fleet's limits do not allow
        it."""
        unit = await self._add(supervisor, name, task, ongoing=ongoing, model=model)
        if supervisor is not None:
            self._emit(Notice(f"You added {unit.called} under {supervisor.called}."))
            if self.up and supervisor.session is not None:
                self._spawn(self._tell_quietly(supervisor,
                                               units.hired_note(unit, self._words)))
        return unit

    async def say(self, text: str, unit: Unit | None = None, *, files: Sequence[str] = (),
                  spoken: bool = False) -> None:
        """The person's message, to the lead or to `unit`, with the files attached to it;
        `spoken` in a voice conversation. A fleet that is not running is started for it.
        A unit under a supervisor is told who speaks, and its supervisor, if it is at
        work, what was said."""
        unit = unit or self.fleet.lead
        if unit is None:
            raise FleetError("The fleet has no lead.")
        await self.start()
        supervisor = self.fleet.supervisor_of(unit)
        if unit.state == "planned":  # it has no task yet: this is it
            await self._assign(unit, text)
            if files:  # a task carries none
                await self.tell(unit, "", files=files, spoken=spoken)
        else:
            await self.tell(unit, text if supervisor is None else units.from_person(text),
                            files=files, spoken=spoken)
        if supervisor is not None and supervisor.active:  # one that has ended is not woken
            self._spawn(self._tell_quietly(supervisor, units.overheard(unit, text, files)))
        self._emit(Told(text, unit, tuple(files)))

    async def set_model(self, unit: Unit, model: str | None) -> None:
        """The model the unit runs on from now; None or "": its rank's or team's. A unit
        at work is started again on it, in its own conversation."""
        unit.model = model or None
        await self._save()
        if unit.id not in self._sessions:
            return  # its process starts on it the next time
        await self._close(unit)
        if unit.active and self.up:
            try:
                await (await self._session(unit)).send(units.MODEL_CHANGED)
            except UnitError as e:
                await self._could_not_start(unit, e)

    async def retask(self, unit: Unit, task: str) -> None:
        """Another task for the unit. One that has not begun begins on it when the fleet
        runs. One that has keeps its conversation: its standing instruction is replaced
        and it is told, at once in a fleet that runs, else when the fleet starts. Its
        supervisor, if it is at work, is told. FleetError or UnitError when it cannot
        be."""
        if not task.strip():
            raise FleetError("A task cannot be empty.")
        self._emit(Instructed(unit, None, "task", task.strip()))
        await self._assign(unit, task)
        supervisor = self.fleet.supervisor_of(unit)
        if self.up and supervisor is not None and supervisor.active:
            self._spawn(self._tell_quietly(supervisor, units.retasked_note(unit)))

    async def remove(self, unit: Unit) -> None:
        """Take the unit and everything under it out of the fleet: their processes end
        and what they kept is deleted. Its supervisor, if it is at work, is told."""
        supervisor = self.fleet.supervisor_of(unit)
        await self.stop_unit(unit)
        gone = self.fleet.remove(unit)
        for each in gone:
            for kept in (self._carries_on, self._last_said, self._opening):
                kept.pop(each.id, None)
            self._answering.discard(each.id)
        await self._save()
        await asyncio.to_thread(_forget, self.folder, gone)
        self._emit(Notice(f"You removed {unit.called}"
                          + (f" and the {len(gone) - 1} under it." if len(gone) > 1 else ".")))
        if self.up and supervisor is not None and supervisor.active:
            self._spawn(self._tell_quietly(supervisor,
                                           units.removed_note(unit, len(gone) - 1)))

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
                       budget_usd: float | None = None) -> Unit:
        """A supervisor starts an agent of its own on a task."""
        return await self._add(supervisor, name, task, by=supervisor, team=team,
                               ongoing=ongoing, budget_usd=budget_usd)

    async def tell(self, unit: Unit, text: str, *, files: Sequence[str] = (),
                   spoken: bool = False) -> None:
        """Put a message into the conversation of a unit that has begun, starting its
        process if it is not up: it then works on what the message says. `files` and
        `spoken` are the session's (api.Session.send). FleetError or UnitError when it
        cannot be."""
        if self.fleet.units.get(unit.id) is not unit:
            raise FleetError(f"{unit.label} is not in the fleet.")
        if not self.up:
            raise FleetError("The fleet is not running.")
        if unit.session is None:
            raise FleetError(f"{unit.label} has not begun.")
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
        await session.send(text, files, spoken=spoken)

    async def message(self, supervisor: Unit, unit: Unit, text: str) -> None:
        """A supervisor's message to one of its agents."""
        if unit.state == "planned":  # it has no task yet: this is it
            await self._assign(unit, text)
        else:
            await self.tell(unit, f"[Message from {supervisor.label}, your supervisor] {text}")
        self._emit(Instructed(unit, supervisor, "message", text))

    async def update(self, unit: Unit, text: str) -> None:
        """A unit's update for its supervisor, without ending its work."""
        self._deliver(unit, "update", f"[Update from {unit.label}] {text}", text)

    async def stop_unit(self, unit: Unit) -> None:
        """Stop the unit and everything under it."""
        for each in self.fleet.subtree(unit):
            if each.active:
                self._set(each, "stopped")
            await self._close(each)

    # ── Units ────────────────────────────────────────────────────────────

    async def _add(self, supervisor: Unit | None, name: str, task: str, *,
                   by: Unit | None = None, team: str | None = None, ongoing: bool = False,
                   budget_usd: float | None = None, model: str | None = None) -> Unit:
        """A new unit in the fleet, given its task by `by` (None: the person)."""
        budget = min(budget_usd or self.config.unit_budget_usd, self.config.unit_budget_usd)
        unit = self.fleet.add(name, task, supervisor, team=team, ongoing=ongoing,
                              budget_usd=budget)
        unit.model = model or None
        if unit.task:
            self._emit(Instructed(unit, by, "task", unit.task))
        await self._launch([unit])
        await self._save()
        return unit

    async def _assign(self, unit: Unit, task: str) -> None:
        """Give the unit a task. One that has not begun begins on it when the fleet runs;
        one that has is at work on it from now, in the conversation it has."""
        if not (task := task.strip()):
            raise FleetError("A task cannot be empty.")
        unit.task = task
        if unit.session is None:
            await self._launch([unit])
        else:
            unit.report = unit.failure = unit.question = None
            self._carries_on[unit.id] = True
            self._answering.discard(unit.id)
            self._set(unit, "working")
            if self.up:  # else `start` opens it on its instruction as it then is
                session = await self._session(unit)
                await session.instruct(self._instruction(unit))
                await session.send(units.RETASKED)
        await self._save()

    async def _launch(self, among: Sequence[Unit]) -> None:
        """Begin the units among these that have a task and have not begun, if the fleet
        runs: every process first, then each its task, so that a supervisor that acts at
        once finds its agents there."""
        if self.up:
            for unit in [unit for unit in among if await self._come_up(unit)]:
                await self._begin(unit)

    async def _come_up(self, unit: Unit) -> bool:
        """Start the process of a unit that has a task and has not begun. False: it is
        not one, or it could not start."""
        if unit.state != "planned" or not unit.task:
            return False
        unit.state = "starting"
        self._emit(Changed(unit, unit.state))
        try:
            await self._open(unit, resume=False)
        except UnitError as e:
            await self._could_not_start(unit, e)
            return False
        except Exception as e:  # a harness's own bug must not leave the unit starting
            logger.exception("opening %s failed", unit.label)
            await self._could_not_start(unit, UnitError(f"{type(e).__name__}: {e}"))
            return False
        return True

    def _instruction(self, unit: Unit) -> Instruction:
        """The unit's standing instruction, from the fleet as it is now."""
        brief = units.brief(unit, self.fleet.supervisor_of(unit),
                            self.fleet.subordinates(unit), self._words)
        return Instruction(brief, unit.ongoing, self.config.check_in_s)

    async def _begin(self, unit: Unit) -> None:
        """Give the unit its task."""
        try:
            if (session := self._sessions.get(unit.id)) is None:
                raise UnitError("Its process ended.")
            unit.session = await session.begin(self._instruction(unit))
        except UnitError as e:
            await self._could_not_start(unit, e)
            return
        self._carries_on[unit.id] = True
        if unit.state == "starting":
            self._set(unit, "working")

    async def _reopen(self, unit: Unit) -> bool:
        """Open a unit that was at work when the fleet stopped, on its session, with its
        instruction as the fleet now has it. False: it could not be."""
        self._carries_on[unit.id] = True
        unit.question = None  # an open question does not outlive the unit's process
        self._emit(Changed(unit, unit.state))
        try:
            await (await self._session(unit)).instruct(self._instruction(unit))
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

    async def _close_all(self) -> None:
        closing = [self._close(unit) for unit in list(self.fleet.units.values())]
        await asyncio.gather(*closing, return_exceptions=True)

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
                    self._on_change()
            case "waiting":
                self._deliver(
                    unit, "question",
                    f"[Question from {unit.label}] {unit.question}\n"
                    f"{self._words.tool('message')}(agent=\"{unit.id}\", text=...) "
                    "answers it.",
                    unit.question or "")
            case "failed":
                why = unit.failure or "It stopped with an error."
                self._deliver(
                    unit, "failure",
                    f"[{unit.label} failed] {why}\n"
                    f"{self._words.tool('message')}(agent=\"{unit.id}\", text=...) starts "
                    "it again.", why)
            case "idle":
                self._deliver(
                    unit, "stopped",
                    f"[{unit.label} stopped without a report]\n"
                    f"{self._words.tool('message')}(agent=\"{unit.id}\", text=...) starts "
                    "it again.",
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
        """Out of money: the fleet stops. Its units stay as they were in the record, to
        carry on when it is started with more."""
        if not self.up or self.fleet.spent_usd < self.config.budget_usd:
            return
        self.stopping = True
        self._emit(Notice(f"The fleet reached its spending limit of "
                          f"${self.config.budget_usd:.2f}; every agent is stopped."))
        self.finished.set()
        self._spawn(self.stop())

    # ── Bookkeeping ──────────────────────────────────────────────────────

    def _set(self, unit: Unit, state: str) -> None:
        if unit.state != state:
            unit.state = state
            self._emit(Changed(unit, unit.state))
            self._spawn(self._save())

    async def _save(self) -> None:
        """The record changed: whoever shows the fleet is told, and it is written."""
        self._on_change()
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
        task.add_done_callback(self._spawned)

    def _spawned(self, task: asyncio.Task[None]) -> None:
        """Work the manager started by itself ended: a failure nobody awaits is said."""
        self._tasks.discard(task)
        if not task.cancelled() and (error := task.exception()) is not None:
            logger.error("the fleet's own work failed", exc_info=error)
            self._emit(Notice(f"Something the fleet does by itself failed: "
                              f"{type(error).__name__}: {error}"))
