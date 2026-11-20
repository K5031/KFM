"""A second harness, written against kfm.api alone: units that follow a script inside the
test's own process. Nothing of Kadence is used: a fleet that runs on this runs on the
contract.

A unit's script is a list of steps, one per turn: {"when": text, "do": [actions]}. A turn
is the unit's instruction or a message sent to it; it takes the next step if `when` (default
"") is in that text, else it says "ok". Actions: ("say", text), ("tool", name, arguments)
for a fleet tool, ("report", text), ("ask", question), ("approval", id, tool, arguments), which
waits for the answer."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from mcp import Client

from kfm.api import (
    ApprovalNeeded,
    Asked,
    Doing,
    Harness,
    Instruction,
    Reported,
    Rested,
    Said,
    Session,
    Spent,
    Thought,
    UnitError,
    UnitEvent,
    UnitSpec,
    Vocabulary,
    Working,
    Wrote,
)

WORDS = Vocabulary(finish="done", ask="ask_boss", tools="fleet.")


class FakeHarness(Harness):
    def __init__(self, scripts: dict[str, list[dict[str, Any]]]) -> None:
        self.scripts = {name: list(steps) for name, steps in scripts.items()}
        self.heard: dict[str, list[str]] = {}  # every turn's text, by unit name
        self.standing: dict[str, bool] = {}  # by session: whether its instruction stands
        self.opened: list[tuple[str, str | None]] = []  # (unit name, session)
        self.models: list[tuple[str, str | None]] = []  # (unit name, model), as opened
        self.approved: list[tuple[str, bool]] = []  # (request id, allow)
        self.conversations: list[str] = []  # units begun on a conversation, not a goal
        self.permissions: list[tuple[str, str]] = []  # (unit name, permissions), as opened
        self.offered = [("big-1", "Big One", "$5 in, $25 out per million tokens"),
                        ("small-1", "Small One", "")]  # its models
        self.open_s = 0.0  # how long a unit's process takes to come up
        self.broken = False  # no process comes up
        self.results: list[tuple[str, str, str]] = []  # (unit name, tool, what it answered)
        self.always: list[str] = []  # request ids allowed for the rest of the unit's work
        self.files: list[tuple[str, list[str], bool]] = []  # (unit name, files, spoken)
        self.instructed: list[tuple[str, str]] = []  # (unit name, its new instruction)

    @property
    def vocabulary(self) -> Vocabulary:
        return WORDS

    async def model_choices(self, folder: Path) -> Sequence[tuple[str, str, str]]:
        return self.offered

    async def open(self, spec: UnitSpec, on_event: Callable[[UnitEvent], None],
                   session: str | None = None) -> Session:
        await asyncio.sleep(self.open_s)
        if self.broken:
            raise UnitError("It could not be started.")
        self.opened.append((spec.name, session))
        self.models.append((spec.name, spec.model))
        self.permissions.append((spec.name, spec.permissions))
        return FakeSession(self, spec, on_event, session)


class FakeSession(Session):
    def __init__(self, harness: FakeHarness, spec: UnitSpec,
                 on_event: Callable[[UnitEvent], None], session: str | None) -> None:
        self.harness, self.spec, self.emit, self.session = harness, spec, on_event, session
        self.turns: set[asyncio.Task[None]] = set()
        self.answers: dict[str, asyncio.Event] = {}

    async def begin(self, instruction: Instruction) -> str:
        self.session = f"session-{self.spec.id}"
        self.harness.standing[self.session] = not instruction.conversation
        if instruction.conversation:
            self.harness.conversations.append(self.spec.name)
        self._turn(instruction.text)
        return self.session

    async def instruct(self, instruction: Instruction) -> None:
        assert self.session is not None
        self.harness.instructed.append((self.spec.name, instruction.text))
        self.harness.standing[self.session] = not instruction.conversation

    async def send(self, text: str, files: Sequence[str] = (), *,
                   spoken: bool = False) -> None:
        if files or spoken:
            self.harness.files.append((self.spec.name, list(files), spoken))
        self._turn(text)

    async def approve(self, request_id: str, allow: bool, *, always: bool = False) -> None:
        self.harness.approved.append((request_id, allow))
        if always:
            self.harness.always.append(request_id)
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
                case ("think", thought):
                    self.emit(Thought(thought))
                case ("write", written):
                    self.emit(Wrote(written))
                case ("doing", what):
                    self.emit(Doing(what))
                case ("wait", seconds):
                    await asyncio.sleep(seconds)
                case ("tool", name, arguments):
                    async with Client(self.spec.tools_url) as client:
                        result = await client.call_tool(name, arguments)
                    self.harness.results.append((self.spec.name, name, " ".join(
                        getattr(part, "text", "") for part in result.content)))
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
