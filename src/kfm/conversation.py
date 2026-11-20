"""An agent's own Kadence conversation in KFM's window, drawn as Kadence draws it: its
view and its model, fed through KFM's service (Service.follow, Service.ask). What the
person writes and decides still goes through KFM, which tells the agent's supervisor and
keeps the fleet's record."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import Any

import kadence.frontends.desktop  # noqa: F401 - before any Qt import
from kadence.api import Decision, EventEnvelope, Refusal, RuntimeStateError
from kadence.frontends.desktop.viewmodel import (
    Controller,
    ConversationListChanged,
    ConversationModel,
    MemoryListChanged,
    Reset,
    StatusChanged,
)
from kadence.frontends.desktop.window import ConversationView
from kadence.frontends.remote import ChannelClosed, RemoteRuntime
from kadence.frontends.wire import from_json, to_json
from PySide6.QtWidgets import QWidget

from kfm.api import UnitError
from kfm.fleet import FleetError
from kfm.service import Service

_REFUSALS = (FleetError, UnitError, ValueError, OSError, RuntimeError)


class UnitChannel:
    """Kadence's channel to an agent's process, as its RemoteRuntime uses one, carried by
    KFM's service."""

    def __init__(self, service: Service, fleet_id: str, unit_id: str) -> None:
        self._service, self._fleet, self._unit = service, fleet_id, unit_id
        self.listeners: list[Callable[[EventEnvelope | None], None]] = []
        self.ended = asyncio.Event()

    async def request(self, method: str, **params: Any) -> Any:
        if self.ended.is_set():
            raise ChannelClosed
        try:
            return await self._service.ask(self._fleet, self._unit, method, to_json(params))
        except _REFUSALS as e:
            raise RuntimeStateError(str(e), Refusal.UNAVAILABLE) from e

    def within(self, event: dict[str, Any] | None) -> None:
        """What the service says happened in the agent's conversation (service.Within)."""
        if event is None:
            self.ended.set()
        envelope = (EventEnvelope(event["seq"], from_json(event["event"]))
                    if event is not None else None)
        for listener in list(self.listeners):
            listener(envelope)  # type: ignore[arg-type]


class AgentController(Controller):
    """Kadence's controller, for an agent of a fleet: a message and an answer to what it
    asks permission for go through KFM."""

    def __init__(self, runtime: RemoteRuntime, model: ConversationModel,
                 tell: Callable[[str, list[str]], None],
                 approve: Callable[[str, bool, bool], None]) -> None:
        super().__init__(runtime, model)  # type: ignore[arg-type]
        self._tell, self._approve = tell, approve

    def send(self, text: str, files: Sequence[str] = (), *, spoken: bool = False) -> None:
        self._tell(text, list(files))

    def resolve(self, request_id: str, decision: Decision) -> None:
        self._approve(request_id, decision is not Decision.DENY,
                      decision is Decision.ALLOW_CONVERSATION)


class AgentPage:
    """One agent's conversation: `view` shows it and `prompts` holds what waits for an
    answer, for the page that hosts it. `begin` first; `ended` when its process is gone
    and the page has to be made anew."""

    def __init__(self, service: Service, fleet_id: str, unit_id: str, *,
                 tell: Callable[[str, list[str]], None],
                 approve: Callable[[str, bool, bool], None],
                 chose: Callable[[], None], ended: Callable[[], None]) -> None:
        self._service, self.fleet, self.unit = service, fleet_id, unit_id
        self._tell, self._approve, self._ended = tell, approve, ended
        self.channel = UnitChannel(service, fleet_id, unit_id)
        self.model = ConversationModel(self._changed)
        self.controller: AgentController | None = None
        self.view = ConversationView(lambda: self.controller,  # type: ignore[arg-type,return-value]
                                     QWidget(), lambda link: None)
        self.view.chose.connect(chose)
        self.prompts = QWidget()
        self.prompts.setLayout(self.view.prompts)
        self._running: asyncio.Task[None] | None = None
        self._closed = False

    async def begin(self) -> bool:
        """Follow the agent and show its conversation. False when there is none to show:
        its fleet does not run, it has not begun, or its process did not answer."""
        try:
            if not await self._service.follow(self.fleet, self.unit):
                return False
            runtime = await RemoteRuntime(self.channel).attach()  # type: ignore[arg-type]
        except (*_REFUSALS, RuntimeStateError):
            return False
        self.controller = AgentController(runtime, self.model, self._tell, self._approve)
        self.channel.listeners.append(self._heard)
        self._running = asyncio.ensure_future(self.controller.run())
        return True

    def _heard(self, envelope: EventEnvelope | None) -> None:
        if envelope is None and not self._closed:
            self._ended()

    def _changed(self, change: object) -> None:
        if self.controller is None:
            return
        match change:
            case Reset():
                self.view.rebuild()
                self.view.refresh()
            case StatusChanged():
                self.view.refresh()
            case ConversationListChanged() | MemoryListChanged():
                pass
            case _:
                self.view.changed(change)  # type: ignore[arg-type]
        # A card made after its holder was shown is not shown with it.
        for index in range(self.view.prompts.count()):
            if (card := self.view.prompts.itemAt(index).widget()) is not None:
                card.show()

    async def close(self) -> None:
        """Let go of the agent and of the widgets."""
        self._closed = True
        self.model.on_change = lambda change: None
        if self._running is not None:
            self._running.cancel()
            await asyncio.gather(self._running, return_exceptions=True)
        try:
            await self._service.unfollow(self.fleet, self.unit)
        except _REFUSALS:
            pass
        for widget in (self.view, self.prompts):
            widget.setParent(None)
            widget.deleteLater()
