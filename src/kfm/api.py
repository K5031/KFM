"""The unit contract: what KFM needs from the agent program its units run (a harness).

A harness opens units: agents that work by themselves on an instruction, in their own
data folder, and say what they do through events. The units are Kaden
(kfm.harnesses.kaden) and nothing else is offered: this is the line inside KFM between
what a fleet does and how one agent's process is run, and where a scripted unit stands in
for a test.

What a unit must do, beyond the methods below:

- **Take turns.** After `begin` and after each `send`, the agent works on what it was
  told by itself: it takes a turn, uses its tools, and ends the turn (`Rested`) when
  there is nothing to do now. It may wake itself for what it planned for later. Whether
  its work stands, ends or waits is the fleet's: the fleet wakes a unit whose work
  stands with a message, ends a goal when the agent calls the fleet tool `finish`, and
  holds a question the agent asked with the fleet tool `ask` until it is answered.
- **Answer in a conversation.** An instruction that is a `conversation` is what the
  agent is told once, when it begins. The agent takes a turn on it and on each `send`;
  it has no fleet work, and its turn ending is all that follows.
- **Say when it waits in a call.** An agent that waits inside a call for an answer only
  a person or its supervisor can give (a question of its own program, such as a form)
  says so (`Asked`) and ends its turn: it takes the next `send` as the answer whatever it
  says, so the fleet sends it nothing else until the answer is given.
- **Use the fleet tools.** `UnitSpec.tools_url` is an MCP server (streamable HTTP) with
  the tools an agent delegates, answers, asks, reports and finishes with. The agent has
  them, and they run without asking anyone.
- **Stay inside its limits.** The agent's other actions follow `UnitSpec.permissions`;
  one that needs approval is asked for with `ApprovalNeeded` and waits for `approve`. The
  unit stops working when it has spent `UnitSpec.budget_usd`, counted over every time it
  was opened.
- **Keep its session.** What `begin` returns names the unit's conversation in
  `UnitSpec.folder`. Opened again with it, the agent has that conversation, its
  instruction, and what it had spent. A unit opened on a session does
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

    id: str  # K5001, K4001, K4002, ...
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
    # A conversation and no fleet work: the text is said once, when the unit begins; it
    # answers each message and stops.
    conversation: bool = False


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """What the agent of this harness calls things, for the words KFM writes to it."""

    tools: str  # what stands before a fleet tool's name, such as "mcp__fleet__"

    def tool(self, name: str) -> str:
        return f"{self.tools}{name}"


# ── What a unit says ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Working:
    """The agent began a turn."""


@dataclass(frozen=True, slots=True)
class Rested:
    """The agent's turn ended."""


@dataclass(frozen=True, slots=True)
class Said:
    text: str  # what the agent wrote in its own conversation


@dataclass(frozen=True, slots=True)
class Asked:
    """The agent waits in a call for the answer, and takes the next send as it, whatever
    it says: the fleet sends nothing else until it has been answered. Before the Rested
    of that turn."""

    question: str


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


# What the agent is doing as it does it, for whoever watches. Optional: an agent program
# that cannot say gives none, and nothing else depends on it. None of it is the record.


@dataclass(frozen=True, slots=True)
class Thought:
    text: str  # more of its reasoning, in the request to its model it is on


@dataclass(frozen=True, slots=True)
class Wrote:
    text: str  # more of the message it is writing; the finished one comes as `Said`


@dataclass(frozen=True, slots=True)
class Doing:
    what: str | None  # the tool it is using, in words; None: none now


@dataclass(frozen=True, slots=True)
class Discarded:
    """What it thought and wrote in this request is void: the request is made again."""


type UnitEvent = (Working | Rested | Said | Asked | Spent | ApprovalNeeded | Failed | Exited
                  | Thought | Wrote | Doing | Discarded)


# ── The harness ───────────────────────────────────────────────────────────


class Session(abc.ABC):
    """An open unit."""

    @abc.abstractmethod
    async def begin(self, instruction: Instruction) -> str:
        """Start a new conversation on the instruction: the agent begins to work. Returns
        the session to open the unit on again."""

    @abc.abstractmethod
    async def instruct(self, instruction: Instruction) -> None:
        """The standing instruction of the conversation `begin` started, from now: another
        one, or the same again. The conversation carries on. A message is sent after it:
        the instruction alone need not start a turn."""

    @abc.abstractmethod
    async def send(self, text: str, files: Sequence[str] = (), *,
                   spoken: bool = False) -> None:
        """A message into the agent's conversation: it takes a turn on it, and an open
        question is answered. `files`: paths of files that go with it, which the agent
        reads as it can; `text` may be empty then. `spoken`: the person said it aloud
        and hears the reply read."""

    @abc.abstractmethod
    async def approve(self, request_id: str, allow: bool, *, always: bool = False) -> None:
        """Answer an ApprovalNeeded, for that one action. `always` with `allow`: the unit
        no longer asks for actions of that kind, for as long as its conversation lasts."""

    @abc.abstractmethod
    async def close(self, grace_s: float) -> None:
        """End the unit's process: its turn is interrupted, its session stays. Waits at
        most `grace_s` for it to end by itself. No event follows."""


class Harness(abc.ABC):
    @property
    @abc.abstractmethod
    def vocabulary(self) -> Vocabulary: ...

    async def model_choices(self, folder: Path) -> Sequence[tuple[str, str, str]]:
        """The models its agents can run on, for a person or a supervisor to choose from:
        each its id, its name, and what else is known of it in a few words (its price),
        or "". None when it cannot say. `folder` is its own to use for asking."""
        return ()

    @abc.abstractmethod
    async def open(self, spec: UnitSpec, on_event: Callable[[UnitEvent], None],
                   session: str | None = None) -> Session:
        """Start the unit's process: new (then `begin` gives it its instruction), or on
        the `session` an earlier `begin` returned."""
