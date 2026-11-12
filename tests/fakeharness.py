"""A second harness, written against kfm.api alone: units that follow a script inside the
test's own process. Nothing of Kayden is used: a fleet that runs on this runs on the
contract.

A unit's script is a list of steps, one per turn: {"when": text, "do": [actions]}. A turn
is the unit's instruction or a message sent to it; it takes the next step if `when` (default
"") is in that text, else it says "ok". Actions: ("say", text), ("tool", name, arguments)
for a fleet tool, ("report", text), ("ask", question), ("approval", id, tool, arguments), which
waits for the answer."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from mcp import Client

from kfm.api import (
    ApprovalNeeded,
    Asked,
    Harness,
    Instruction,
    Reported,
    Rested,
    Said,
    Session,
    Spent,
    UnitEvent,
    UnitSpec,
    Vocabulary,
    Working,
)

WORDS = Vocabulary(finish="done", ask="ask_boss", tools="fleet.")


class FakeHarness(Harness):
    def __init__(self, scripts: dict[str, list[dict[str, Any]]]) -> None:
        self.scripts = {name: list(steps) for name, steps in scripts.items()}
        self.heard: dict[str, list[str]] = {}  # every turn's text, by unit name
        self.standing: dict[str, bool] = {}  # by session: whether its instruction stands
        self.opened: list[tuple[str, str | None]] = []  # (unit name, session)
        self.approved: list[tuple[str, bool]] = []  # (request id, allow)

    @property
    def vocabulary(self) -> Vocabulary:
        return WORDS

    async def open(self, spec: UnitSpec, on_event: Callable[[UnitEvent], None],
                   session: str | None = None) -> Session:
        self.opened.append((spec.name, session))
        return FakeSession(self, spec, on_event, session)


class FakeSession(Session):
    def __init__(self, harness: FakeHarness, spec: UnitSpec,
                 on_event: Callable[[UnitEvent], None], session: str | None) -> None:
        self.harness, self.spec, self.emit, self.session = harness, spec, on_event, session
        self.turns: set[asyncio.Task[None]] = set()
        self.answers: dict[str, asyncio.Event] = {}

    async def begin(self, instruction: Instruction) -> str:
        self.session = f"session-{self.spec.id}"
        self.harness.standing[self.session] = True
        self._turn(instruction.text)
        return self.session

    async def send(self, text: str) -> None:
        self._turn(text)

    async def approve(self, request_id: str, allow: bool) -> None:
        self.harness.approved.append((request_id, allow))
        self.answers[request_id].set()

    async def close(self, grace_s: float) -> None:
        for turn in self.turns:
            turn.cancel()
        await asyncio.gather(*self.turns, return_exceptions=True)

    def _turn(self, text: str) -> None:
        turn = asyncio.create_task(self._work(text))
        self.turns.add(turn)
        turn.add_done_callback(self.turns.discard)

    async def _work(self, text: str) -> None:
        assert self.session is not None
        self.harness.heard.setdefault(self.spec.name, []).append(text)
        self.emit(Working())
        script = self.harness.scripts.get(self.spec.name, [])
        actions = [("say", "ok")]
        if script and script[0].get("when", "") in text:
            actions = script.pop(0)["do"]
        for action in actions:
            match action:
                case ("say", said):
                    self.emit(Said(said))
                case ("tool", name, arguments):
                    async with Client(self.spec.tools_url) as client:
                        await client.call_tool(name, arguments)
                case ("report", report):
                    self.emit(Reported(report))
                    self.harness.standing[self.session] = False
                case ("ask", question):
                    self.emit(Asked(question))
                case ("approval", request_id, tool, arguments):
                    self.answers[request_id] = asyncio.Event()
                    self.emit(ApprovalNeeded(request_id, tool, arguments))
                    await self.answers[request_id].wait()
        self.emit(Spent(0.01 * len(self.harness.heard[self.spec.name])))
        self.emit(Rested(self.harness.standing.get(self.session, False)))
