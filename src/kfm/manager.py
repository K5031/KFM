"""The fleet manager: it runs every unit's process and carries everything between them.
A unit's report, question or failure reaches its supervisor as a message; what the lead
reports or asks reaches the person. Units never talk to each other directly."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from kayden.api import (
    AgentState,
    ConversationUpdated,
    EventEnvelope,
    Failure,
    HelpNeeded,
    MessageCommitted,
    PermissionRequested,
    Role,
    RuntimeStateError,
    StateChanged,
    Text,
    ToolCall,
    UsageUpdated,
)
from kayden.frontends.remote import Channel

from kfm import config as settings
from kfm import units
from kfm.config import FleetConfig
from kfm.fleet import Fleet, FleetError, Unit
from kfm.tools import ToolServer

logger = logging.getLogger(__name__)

STOP_GRACE_S = 10.0  # after a unit's stdin closes, before it is killed
_ACTIVE = (AgentState.THINKING, AgentState.RUNNING_TOOLS, AgentState.AWAITING_PERMISSION)
_OVER = ("done", "idle", "failed", "stopped")


# ── What the manager tells whoever shows the fleet to the person ─────────


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
class Notice:
    text: str


type FleetEvent = Changed | Said | Delivered | Approval | Notice


class Manager:
    def __init__(self, config: FleetConfig, folder: Path, work: Path, *,
                 command: Sequence[str] = (sys.executable, "-m", "kayden"),
                 on_event: Callable[[FleetEvent], None] = lambda event: None,
                 fleet: Fleet | None = None) -> None:
        self.config, self.folder, self.work = config, folder, work
        self._resuming = fleet is not None
        self.fleet = fleet or Fleet(config.top_rank, config.max_units)
        self.fleet.work, self.fleet.settings = str(work), settings.record(config)
        self.finished = asyncio.Event()  # the lead reported, or the budget ran out
        self._command, self._on_event = tuple(command), on_event
        self._tools = ToolServer(self)
        self._channels: dict[str, Channel] = {}
        self._agent: dict[str, AgentState] = {}
        self._has_duty: dict[str, bool] = {}
        # Units told more after their goal ended, and what each wrote last since: with no
        # goal left to finish, that is its report.
        self._answering: set[str] = set()
        self._last_said: dict[str, str] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._over_budget = False
        self._saving = asyncio.Lock()

    @classmethod
    def resumed(cls, folder: Path, *,
                command: Sequence[str] = (sys.executable, "-m", "kayden"),
                on_event: Callable[[FleetEvent], None] = lambda event: None,
                **overrides: object) -> Manager:
        """A manager for the stopped fleet in `folder`, with the settings it ran with and
        `overrides` that are not None. `start` then starts every unit that was at work.
        FleetError when the fleet is running or has spent its limit. Blocking."""
        fleet = Fleet.load(folder)
        if fleet.running:
            raise FleetError(f"The fleet in {folder} is running (process "
                             f"{fleet.manager_pid}).")
        config = settings.from_record(fleet.settings, **overrides)
        if fleet.spent_usd >= config.budget_usd:
            raise FleetError(f"The fleet has spent ${fleet.spent_usd:.2f} of its "
                             f"${config.budget_usd:.2f}: give it more with --budget.")
        return cls(config, folder, Path(fleet.work), command=command, on_event=on_event,
                   fleet=fleet)

    async def start(self) -> None:
        await asyncio.to_thread(self.folder.mkdir, parents=True, exist_ok=True)
        await self._tools.start()
        self.fleet.manager_pid = os.getpid()
        await self._save()
        if self._resuming:
            for unit in list(self.fleet.units.values()):
                if unit.active:
                    await self._carry_on(unit)

    async def aclose(self) -> None:
        """Stop the fleet's processes. The record keeps every unit's state, so that
        `resumed` knows which were at work."""
        closing = [self._close(unit) for unit in self.fleet.units.values()]
        await asyncio.gather(*closing, return_exceptions=True)
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._tools.aclose()
        self.fleet.manager_pid = None
        await self._save()

    # ── What the person does ─────────────────────────────────────────────

    async def lead(self, task: str, *, ongoing: bool = False) -> Unit:
        """Start the fleet: its lead, on the person's task."""
        unit = self.fleet.add("Lead", task, None, ongoing=ongoing,
                              budget_usd=self.config.unit_budget_usd)
        await self._start(unit)
        return unit

    async def say(self, text: str) -> None:
        """The person's message, to the lead."""
        if (lead := self.fleet.lead) is None:
            raise FleetError("The fleet has not started.")
        await self.tell(lead, text)

    async def approve(self, unit: Unit, request_id: str, allow: bool) -> None:
        if (channel := self._channels.get(unit.id)) is not None:
            await channel.request("resolve_permission", request_id=request_id,
                                  decision="allow_once" if allow else "deny")

    # ── What units do, through the fleet tools ───────────────────────────

    async def delegate(self, supervisor: Unit, name: str, task: str, *,
                       team: str | None = None, ongoing: bool = False,
                       budget_usd: float | None = None) -> Unit:
        if self._over_budget:
            raise FleetError("The fleet has reached its spending limit.")
        budget = min(budget_usd or self.config.unit_budget_usd, self.config.unit_budget_usd)
        unit = self.fleet.add(name, task, supervisor, team=team, ongoing=ongoing,
                              budget_usd=budget)
        await self._start(unit)
        return unit

    async def tell(self, unit: Unit, text: str) -> None:
        """Put a message into the unit's conversation, starting it again if it is not
        running: it then works on what the message says."""
        if unit.conversation_id is None:
            raise FleetError(f"{unit.label} never started.")
        if self._over_budget:
            raise FleetError("The fleet has reached its spending limit.")
        channel = self._channels.get(unit.id)
        if channel is None:
            channel = await self._launch(unit, resume=True)
        if unit.state in _OVER:
            unit.report = unit.failure = None
            self._set(unit, "working")
            if not self._has_duty.get(unit.id, False):
                self._answering.add(unit.id)
                self._last_said.pop(unit.id, None)
                text = f"{text}\n\n{units.answer_note(self.fleet.supervisor_of(unit))}"
        await channel.request("send", text=text)

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
        self._emit(Changed(unit, unit.state))
        try:
            channel = await self._launch(unit, resume=False)
            info = await channel.request("start")
            unit.conversation_id = info["id"]
            low, high = self.config.check_in_s
            await channel.request("set_duty", duty={
                "instruction": units.brief(unit, self.fleet.supervisor_of(unit)),
                "kind": "duty" if unit.ongoing else "goal",
                "min_interval_s": low, "max_interval_s": high})
        except (OSError, RuntimeError, RuntimeStateError, ValueError) as e:
            unit.failure = self._why(unit, e)
            self._set(unit, "failed")
            await self._close(unit)
            return
        if unit.state == "starting":
            self._set(unit, "working")

    async def _carry_on(self, unit: Unit) -> None:
        """Start a unit that was at work when the fleet stopped, on its conversation: its
        Kayden still has its duty and schedules the next check-in."""
        if unit.conversation_id is None:  # stopped before it had its task
            await self._start(unit)
            return
        self._has_duty[unit.id] = True
        unit.question = None  # its Kayden held it in memory only
        self._emit(Changed(unit, unit.state))
        try:
            channel = await self._launch(unit, resume=True)
            self._set(unit, "working")
            # A Kayden that opens a conversation with a duty waits for a message.
            await channel.request("send", text=units.RESUMED)
        except (OSError, RuntimeError, RuntimeStateError, ValueError) as e:
            unit.failure = self._why(unit, e)
            self._set(unit, "failed")
            await self._close(unit)

    def _unit_folder(self, unit: Unit) -> Path:
        return self.folder / "units" / unit.id

    async def _launch(self, unit: Unit, *, resume: bool) -> Channel:
        # Written at every start: the fleet tools' address changes with the manager.
        await asyncio.to_thread(units.prepare, unit, self._unit_folder(unit), self.config,
                                self.work, self._tools.url)
        channel = await units.launch(self._command, self._unit_folder(unit), self.work,
                                     unit.conversation_id if resume else None)
        channel.listeners.append(lambda envelope: self._event(unit, channel, envelope))
        self._channels[unit.id] = channel
        self._agent[unit.id] = AgentState.IDLE
        return channel

    async def _close(self, unit: Unit) -> None:
        if (channel := self._channels.pop(unit.id, None)) is not None:
            await channel.close(STOP_GRACE_S)

    def _why(self, unit: Unit, error: Exception | None) -> str:
        channel = self._channels.get(unit.id)
        said = " ".join(channel.stderr) if channel is not None else ""
        return " ".join(part for part in (str(error) if error else "", said[-400:]) if part) \
            or "Its process ended."

    # ── What a unit's Kayden says ────────────────────────────────────────

    def _event(self, unit: Unit, channel: Channel, envelope: EventEnvelope | None) -> None:
        if envelope is None:  # its process ended
            if self._channels.get(unit.id) is channel:
                del self._channels[unit.id]
                if unit.active:  # by itself
                    unit.failure = self._why(unit, None)
                    self._set(unit, "failed")
                    self._turn_ended(unit)
            return
        match envelope.event:
            case MessageCommitted(message=message) if message.role == Role.ASSISTANT:
                for block in message.blocks:
                    if isinstance(block, ToolCall) and block.name == "finish_goal":
                        if self._has_duty.get(unit.id, False):  # else Kayden refuses it
                            unit.report = block.arguments.get("report") or "Done."
                    elif isinstance(block, Text) and block.text.strip():
                        self._last_said[unit.id] = block.text.strip()
                        self._emit(Said(unit, block.text.strip()))
            case HelpNeeded(question=question):
                unit.question = question
            case ConversationUpdated(info=info) if info.id == unit.conversation_id:
                self._has_duty[unit.id] = info.duty is not None
            case UsageUpdated(snapshot=snapshot):
                unit.spent_usd = snapshot.usage.cost_usd or 0.0
                self._spawn(self._save())
                self._check_budget()
            case Failure(message=message):
                unit.failure = message
            case PermissionRequested(request=request):
                if request.call.name.startswith(units.PREFIX):  # the fleet's own tools
                    self._spawn(channel.request("resolve_permission", request_id=request.id,
                                                decision="allow_conversation"))
                else:
                    self._emit(Approval(unit, request.id, request.call.name,
                                        str(request.call.arguments)))
            case StateChanged(state=state):
                was_active = self._agent.get(unit.id) in _ACTIVE
                self._agent[unit.id] = state
                if was_active and state not in _ACTIVE and unit.id in self._answering:
                    self._answering.discard(unit.id)
                    if unit.report is None and not self._has_duty.get(unit.id, False):
                        unit.report = self._last_said.get(unit.id)
                self._set(unit, self._state_after(unit, state))
                if was_active and state not in _ACTIVE and unit.state != "working":
                    self._turn_ended(unit)

    def _state_after(self, unit: Unit, state: AgentState) -> str:
        if unit.state == "stopped":
            return "stopped"
        if state in _ACTIVE:
            return "working"
        if state == AgentState.FAILED:
            return "failed"
        if unit.state == "starting":
            return "starting"  # idle until it has its task
        if state in (AgentState.IDLE, AgentState.STOPPED):
            has_duty = self._has_duty.get(unit.id, False)
            if unit.question is not None and has_duty:
                return "waiting"
            if has_duty:
                return "working"  # between check-ins
            if unit.id in self._answering:
                return "working"  # started again; its turn has yet to begin
            return "done" if unit.report is not None else "idle"
        return unit.state

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
                    f"{units.PREFIX}message(agent=\"{unit.id}\", text=...).",
                    unit.question or "")
            case "failed":
                why = unit.failure or "It stopped with an error."
                self._deliver(
                    unit, "failure",
                    f"[{unit.label} failed] {why}\nTell it to carry on with "
                    f"{units.PREFIX}message, hand the task to another agent, or ask your "
                    "own supervisor.", why)
            case "idle":
                self._deliver(
                    unit, "stopped",
                    f"[{unit.label} stopped without a report] Ask it what it did with "
                    f"{units.PREFIX}message, or hand the task to another agent.",
                    "It stopped without a report.")

    def _deliver(self, unit: Unit, kind: str, message: str, text: str) -> None:
        supervisor = self.fleet.supervisor_of(unit)
        self._emit(Delivered(unit, supervisor, kind, text))
        if supervisor is not None:
            self._spawn(self._tell_quietly(supervisor, message))

    async def _tell_quietly(self, unit: Unit, text: str) -> None:
        try:
            await self.tell(unit, text)
        except (FleetError, RuntimeError, RuntimeStateError, OSError) as e:
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
        try:
            self._on_event(event)
        except Exception:
            logger.exception("showing a fleet event failed")

    def _spawn(self, work: object) -> None:
        task: asyncio.Task[None] = asyncio.ensure_future(work)  # type: ignore[arg-type]
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
