"""The unit contract: what KFM needs from the agent program its units run (a harness).

A harness opens units: agents that work by themselves on an instruction, in their own
data folder, and say what they do through events. Kadence is the built-in one
(kfm.harnesses.kadence); another is a `factory(options) -> Harness`, named in the fleet's
settings by its entry point in the group "kfm.harnesses" or as "package.module:factory".

What a unit must do, beyond the methods below:

- **Work by itself.** After `begin`, the agent carries its instruction on without being
  asked: it takes turns, uses its tools, and ends a turn when there is nothing to do
  now. While the instruction stands it wakes itself again, at least every
  `Instruction.check_in_s[1]` seconds.
- **End a goal with a report.** An instruction that is not `ongoing` is a goal: the agent
  declares it reached with a report (`Reported`), and the instruction then no longer
  stands. An `ongoing` one stands until the unit is closed.
- **Ask when blocked.** When only its supervisor can unblock it, the agent asks one
  question (`Asked`) and ends its turn. It does not wake itself while the question is
  open; the next `send` is the answer.
- **Use the fleet tools.** `UnitSpec.tools_url` is an MCP server (streamable HTTP) with
  the tools an agent delegates, answers and reports with. The agent has them, and they
  run without asking anyone.
- **Stay inside its limits.** The agent's other actions follow `UnitSpec.permissions`;
  one that needs approval is asked for with `ApprovalNeeded` and waits for `approve`. The
  unit stops working when it has spent `UnitSpec.budget_usd`, counted over every time it
  was opened.
- **Keep its session.** What `begin` returns names the unit's conversation in
  `UnitSpec.folder`. Opened again with it, the agent has that conversation, its
  instruction if it still stood, and what it had spent. A unit opened on a session does
  nothing until a message is sent to it.

Events of one unit arrive in order, on the event loop, through the callback given to
`open`. Methods raise UnitError for what the unit could not do; its message is for a
person.
"""

from __future__ import annotations

import abc
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path


class UnitError(Exception):
    """The unit could not do what was asked: it did not start, or its process is gone."""


@dataclass(frozen=True, slots=True)
class UnitSpec:
    """A unit as its harness needs to know it."""

    id: str  # u1, u2, ...
    name: str
    rank: int  # 5 is K5
    folder: Path  # the unit's own data folder; the harness keeps what it needs there
    work: Path  # the folder the fleet works in
    model: str | None  # None: the harness's own choice
    permissions: str  # FleetConfig.permissions
    budget_usd: float
    tools_url: str  # the fleet tools; the address changes each time the fleet starts


@dataclass(frozen=True, slots=True)
class Instruction:
    text: str  # the unit's brief: who it is in the fleet, its task, how the fleet works
    ongoing: bool  # standing work that never ends, instead of a goal
    check_in_s: tuple[float, float]  # the shortest and the longest time between two turns


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """What the agent of this harness calls things, for the words KFM writes to it."""

    finish: str  # the tool that declares a goal reached, with its report
    ask: str  # the tool that asks its supervisor
    tools: str  # what stands before a fleet tool's name, such as "mcp__fleet__"

    def tool(self, name: str) -> str:
        return f"{self.tools}{name}"


# ── What a unit says ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Working:
    """The agent began a turn."""


@dataclass(frozen=True, slots=True)
class Rested:
    """The agent's turn ended. `carries_on`: its instruction still stands, so it wakes
    itself again (or waits for the answer to what it asked)."""

    carries_on: bool


@dataclass(frozen=True, slots=True)
class Said:
    text: str  # what the agent wrote in its own conversation


@dataclass(frozen=True, slots=True)
class Reported:
    """The agent declared its goal reached. Before the Rested of that turn, and only while
    a goal stands."""

    text: str


@dataclass(frozen=True, slots=True)
class Asked:
    question: str  # before the Rested of that turn


@dataclass(frozen=True, slots=True)
class Spent:
    usd: float  # everything the unit has spent so far


@dataclass(frozen=True, slots=True)
class ApprovalNeeded:
    request_id: str
    tool: str
    arguments: str  # as a person can read them


@dataclass(frozen=True, slots=True)
class Failed:
    """The agent's turn ended in an error it does not recover from by itself."""

    message: str


@dataclass(frozen=True, slots=True)
class Exited:
    """The unit's process ended by itself. Never after `close`."""

    why: str


type UnitEvent = (Working | Rested | Said | Reported | Asked | Spent | ApprovalNeeded
                  | Failed | Exited)


# ── The harness ───────────────────────────────────────────────────────────


class Session(abc.ABC):
    """An open unit."""

    @abc.abstractmethod
    async def begin(self, instruction: Instruction) -> str:
        """Start a new conversation on the instruction: the agent begins to work. Returns
        the session to open the unit on again."""

    @abc.abstractmethod
    async def instruct(self, instruction: Instruction) -> None:
        """Replace the standing instruction of the conversation `begin` started, which
        carries on: the agent has another task from now, and takes no turn for it."""

    @abc.abstractmethod
    async def send(self, text: str, files: Sequence[str] = (), *,
                   spoken: bool = False) -> None:
        """A message into the agent's conversation: it takes a turn on it, and an open
        question is answered. `files`: paths of files that go with it, which the agent
        reads as it can; `text` may be empty then. `spoken`: the person said it aloud
        and hears the reply read."""

    @abc.abstractmethod
    async def approve(self, request_id: str, allow: bool) -> None:
        """Answer an ApprovalNeeded, for that one action."""

    @abc.abstractmethod
    async def close(self, grace_s: float) -> None:
        """End the unit's process: its turn is interrupted, its session stays. Waits at
        most `grace_s` for it to end by itself. No event follows."""


class Harness(abc.ABC):
    @property
    @abc.abstractmethod
    def vocabulary(self) -> Vocabulary: ...

    @abc.abstractmethod
    async def open(self, spec: UnitSpec, on_event: Callable[[UnitEvent], None],
                   session: str | None = None) -> Session:
        """Start the unit's process: new (then `begin` gives it its instruction), or on
        the `session` an earlier `begin` returned."""
