"""Kaden as a fleet's unit: an unchanged Kaden, run headless in the unit's data folder.
This module writes that folder's configuration, starts the process, and turns what
Kaden's headless channel says into the unit contract's events (kfm.api)."""

from __future__ import annotations

import asyncio
import json
import sys
import tomllib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import tomli_w
from kaden.api import (
    DESCRIPTION_ARG,
    AgentState,
    ConversationUpdated,
    EventEnvelope,
    Failure,
    HelpNeeded,
    MessageCommitted,
    ModelRetry,
    Prompt,
    PromptClosed,
    PromptKind,
    PromptOpened,
    ReasoningDelta,
    Role,
    RuntimeSnapshot,
    RuntimeStateError,
    StateChanged,
    Text,
    TextDelta,
    ToolCall,
    ToolFinished,
    ToolStarted,
    UsageUpdated,
)
from kaden.frontends.remote import Channel
from kaden.frontends.wire import from_json

from kfm.api import (
    ApprovalNeeded,
    Asked,
    Discarded,
    Doing,
    Exited,
    Failed,
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

TOOLS = "fleet"  # the MCP server's name in a unit: its tools are mcp__fleet__<tool>
WORDS = Vocabulary(finish="finish_goal", ask="ask_for_help", tools=f"mcp__{TOOLS}__")
CARD = "wrap_up"  # Kaden's tool that ends a turn with a recap, a question and its answers
UNIT_RECORD = "unit.json"
_LINE_LIMIT = 16 * 1024 * 1024
DOING_CHARS = 160  # of a tool call, as it is shown while it runs
MODELS_S = 30.0  # for a Kaden asked which models it offers to answer
MODELS_GRACE_S = 5.0  # for it to end afterwards
_ACTIVE = (AgentState.THINKING, AgentState.RUNNING_TOOLS, AgentState.AWAITING_PERMISSION,
           AgentState.FINISHING)  # a turn is over when what follows the answer is too


def factory(options: dict[str, Any]) -> KadenHarness:
    """Options: `config`, the Kaden configuration every unit starts from: its provider,
    key and options (default ~/.kaden/config.toml); `command`, how Kaden is started
    (default: this Python's `-m kaden`)."""
    options = dict(options)
    config = Path(options.pop("config", Path.home() / ".kaden" / "config.toml")).expanduser()
    command = tuple(options.pop("command", (sys.executable, "-m", "kaden")))
    if options:
        raise TypeError(f"unknown option {', '.join(sorted(options))}")
    return KadenHarness(config, command)


class KadenHarness(Harness):
    def __init__(self, config: Path, command: Sequence[str]) -> None:
        self.config, self.command = config, tuple(command)

    @property
    def vocabulary(self) -> Vocabulary:
        return WORDS

    async def open(self, spec: UnitSpec, on_event: Callable[[UnitEvent], None],
                   session: str | None = None) -> Session:
        args = [*self.command, "--headless", "--data-dir", str(spec.folder)]
        if session is not None:
            args += ["--conversation", session]
        try:
            await asyncio.to_thread(self.prepare, spec)
            process = await asyncio.create_subprocess_exec(
                *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, cwd=spec.work, limit=_LINE_LIMIT,
                # Ctrl+C in the terminal is for the manager, which closes its units.
                start_new_session=True)
        except (OSError, ValueError) as e:
            raise UnitError(str(e)) from e
        opened = _Session(Channel(process), on_event, session)
        if session is not None:
            await opened.attach()
        return opened

    async def model_choices(self, folder: Path) -> Sequence[tuple[str, str, str]]:
        """What the configured provider lists: asked of a Kaden started for that alone,
        which makes no request to a model. UnitError when it cannot be asked."""
        def configure() -> None:
            folder.mkdir(parents=True, exist_ok=True)
            base = tomllib.loads(self.config.read_text()) if self.config.exists() else {}
            base.pop("frontend", None)
            (folder / "config.toml").write_text(tomli_w.dumps(base))
            (folder / UNIT_RECORD).write_text(json.dumps({"id": "", "name": "", "rank": 0}))

        try:
            await asyncio.to_thread(configure)
            process = await asyncio.create_subprocess_exec(
                *self.command, "--headless", "--data-dir", str(folder),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, limit=_LINE_LIMIT, start_new_session=True)
        except (OSError, ValueError) as e:
            raise UnitError(str(e)) from e
        channel = Channel(process)
        try:
            listed = await asyncio.wait_for(channel.request("list_models"), MODELS_S)
        except (RuntimeStateError, RuntimeError, ValueError, KeyError, OSError,
                TimeoutError) as e:
            raise UnitError(f"Kaden could not list its models: {e}") from e
        finally:
            await channel.close(MODELS_GRACE_S)
        return [(each["id"], each.get("name") or each["id"], _price(each)) for each in listed]

    def prepare(self, spec: UnitSpec) -> None:
        """The unit's Kaden data folder: the base configuration with this unit's model,
        limit and permissions, the fleet tools, and the folder the fleet works in.
        Blocking."""
        spec.folder.mkdir(parents=True, exist_ok=True)
        base: dict[str, Any] = {}
        if self.config.exists():
            base = tomllib.loads(self.config.read_text())
        base.pop("frontend", None)
        runtime = base.setdefault("runtime", {})
        if spec.model:
            runtime["model"] = spec.model
        runtime["permissions"] = spec.permissions
        runtime["budget"] = {"max_cost_usd": spec.budget_usd}
        (spec.folder / "config.toml").write_text(tomli_w.dumps(base))
        settings = {"work_folder": str(spec.work),
                    "mcp_servers": [{"name": TOOLS, "url": spec.tools_url}]}
        (spec.folder / "settings.toml").write_text(tomli_w.dumps(settings))
        (spec.folder / UNIT_RECORD).write_text(json.dumps(
            {"id": spec.id, "name": spec.name, "rank": spec.rank}))


class _Session(Session):
    def __init__(self, channel: Channel, on_event: Callable[[UnitEvent], None],
                 conversation: str | None) -> None:
        self._channel, self._emit, self._conversation = channel, on_event, conversation
        self._active = False
        self._questioned: str | None = None  # the prompt that waits for the next send
        self._has_duty = False
        self._failure: str | None = None  # what Kaden said before its state became failed
        self._closed = False
        self._tools: dict[str, str] = {}  # the tool calls under way, in words, by call id
        self._answers: set[asyncio.Task[Any]] = set()
        channel.listeners.append(self._event)

    async def attach(self) -> None:
        """Opened on a conversation: learn whether its duty still stands."""
        snapshot = from_json(await self._request("snapshot"), RuntimeSnapshot)
        self._has_duty = (snapshot.conversation is not None
                          and snapshot.conversation.duty is not None)

    async def begin(self, instruction: Instruction) -> str:
        info = await self._request("start")
        self._conversation = info["id"]
        if instruction.conversation:  # said once, as what the conversation opens with
            await self.send(instruction.text)
        else:
            await self.instruct(instruction)
        return info["id"]

    async def instruct(self, instruction: Instruction) -> None:
        if instruction.conversation:
            return
        low, high = instruction.check_in_s
        await self._request("set_duty", duty={
            "instruction": instruction.text,
            "kind": "duty" if instruction.ongoing else "goal",
            "min_interval_s": low, "max_interval_s": high})

    async def send(self, text: str, files: Sequence[str] = (), *,
                   spoken: bool = False) -> None:
        await self._request("send", text=text, files=list(files), spoken=spoken)

    async def approve(self, request_id: str, allow: bool, *, always: bool = False) -> None:
        decision = "deny" if not allow else "allow_conversation" if always else "allow_once"
        await self._request("respond", prompt_id=request_id, reply=decision)

    async def close(self, grace_s: float) -> None:
        self._closed = True
        await self._channel.close(grace_s)

    # Not of the contract: Kaden's own channel, for a window that shows the unit's
    # conversation as Kaden does (Manager.follow, Manager.ask).

    @property
    def listeners(self) -> list[Callable[[EventEnvelope | None], None]]:
        """Called with each event of the unit's Kaden, and None when it ended."""
        return self._channel.listeners

    async def ask(self, method: str, params: dict[str, Any]) -> Any:
        """A request of Kaden's headless channel, in its JSON."""
        return await self._request(method, **params)

    async def _request(self, method: str, **params: Any) -> Any:
        try:
            return await self._channel.request(method, **params)
        except (RuntimeStateError, RuntimeError, ValueError, KeyError, NotImplementedError,
                OSError) as e:
            raise UnitError(" ".join(part for part in (str(e), self._said()) if part)) from e

    def _said(self) -> str:
        """The end of what the process wrote to stderr."""
        return " ".join(self._channel.stderr)[-400:]

    def _event(self, envelope: EventEnvelope | None) -> None:
        if envelope is None:  # its process ended
            if not self._closed:
                self._emit(Exited(self._said() or "Its process ended."))
            return
        match envelope.event:
            case MessageCommitted(message=message) if message.role == Role.ASSISTANT:
                for block in message.blocks:
                    if isinstance(block, ToolCall) and block.name == WORDS.finish:
                        if self._has_duty:  # else Kaden refuses the call
                            self._emit(Reported(block.arguments.get("report") or "Done."))
                    elif isinstance(block, ToolCall) and block.name == CARD:
                        self._emit(Said(_card(block.arguments)))
                    elif isinstance(block, Text) and block.text.strip():
                        self._emit(Said(block.text.strip()))
            case ReasoningDelta(text=text):
                self._emit(Thought(text))
            case TextDelta(text=text):
                self._emit(Wrote(text))
            case ModelRetry():
                self._emit(Discarded())
            case ToolStarted(call=call):
                self._tools[call.id] = _in_words(call)
                self._emit(Doing(self._tools[call.id]))
            case ToolFinished(result=result):
                self._tools.pop(result.call_id, None)
                self._emit(Doing(next(reversed(self._tools.values()), None)))
            case PromptOpened(prompt=prompt):
                self._prompted(prompt)
            case PromptClosed(prompt_id=prompt_id) if prompt_id == self._questioned:
                self._questioned = None
            case HelpNeeded(question=question):
                self._emit(Asked(question))
            case ConversationUpdated(info=info) if self._conversation in (None, info.id):
                self._has_duty = info.duty is not None
            case UsageUpdated(snapshot=snapshot):
                self._emit(Spent(snapshot.usage.cost_usd or 0.0))
            case Failure(message=message):
                self._failure = message
            case StateChanged(state=state):
                active = state in _ACTIVE and self._questioned is None
                if state == AgentState.FAILED:
                    self._emit(Failed(self._failure or "It stopped with an error."))
                elif active and not self._active:
                    self._emit(Working())
                elif self._active and not active:
                    self._emit(Rested(self._has_duty))
                self._active = active


    def _prompted(self, prompt: Prompt) -> None:
        """What a unit's Kaden waits on, as the fleet handles it: a permission goes up as
        an approval (the fleet's own tools are allowed at once); a plan is said and
        approved, since nobody approves plans in a fleet; anything a message answers (a
        question) goes up as the unit asking, and its turn rests until the next send. A
        proposal stays open: a unit's ideas wait for nobody."""
        call = prompt.call
        if prompt.kind == PromptKind.PERMISSION and call is not None:
            if call.name.startswith(WORDS.tools):
                self._answer("respond", prompt_id=prompt.id, reply="allow_conversation")
            else:
                self._emit(ApprovalNeeded(prompt.id, call.name, str(call.arguments)))
        elif prompt.kind == PromptKind.PLAN:
            self._emit(Said(str(prompt.details.get("plan", "")).strip()))
            self._answer("respond", prompt_id=prompt.id, reply={"approve": True})
        elif prompt.words:
            questions = prompt.details.get("questions")
            self._emit(Asked(_questions(questions) if questions else prompt.text, waits=True))
            if self._active:  # it carries on once answered, with or without a duty
                self._emit(Rested(True))
            self._active, self._questioned = False, prompt.id

    def _answer(self, method: str, **params: Any) -> None:
        """Answer what Kaden waits on, without waiting for its reply."""
        answer = asyncio.ensure_future(self._channel.request(method, **params))
        self._answers.add(answer)
        answer.add_done_callback(self._answers.discard)


def _price(model: Mapping[str, Any]) -> str:
    """What a model costs, as Kaden knows it; "" when it does not."""
    paid, made = model.get("input_usd_per_mtok"), model.get("output_usd_per_mtok")
    if paid is None or made is None:
        return ""
    return f"${paid:g} in, ${made:g} out per million tokens"


def _card(arguments: dict[str, Any]) -> str:
    """The card a turn ended with, as what the unit said: its recap, its question, and
    the answers it offers."""
    options = [f"- {option['label']}" for option in arguments.get("options") or ()
               if isinstance(option, dict) and option.get("label")]
    question = "\n".join([str(arguments.get("question") or "").strip(), *options]).strip()
    return "\n\n".join(part for part in (str(arguments.get("recap") or "").strip(), question)
                       if part)


def _questions(questions: Sequence[Any]) -> str:
    """An AskUserQuestion's questions, as the unit asks them: each with its options."""
    parts = []
    for question in questions:
        if not isinstance(question, dict):
            continue
        options = [f"- {o['label']}" + (f": {o['description']}" if o.get("description") else "")
                   for o in question.get("options") or ()
                   if isinstance(o, dict) and o.get("label")]
        parts.append("\n".join([str(question.get("question") or "").strip(), *options]))
    return "\n\n".join(p for p in parts if p.strip())


def _in_words(call: ToolCall) -> str:
    """A tool call for a person to read: what the agent said it is for, then the call."""
    name = call.name.removeprefix(WORDS.tools)
    rest = {key: value for key, value in call.arguments.items() if key != DESCRIPTION_ARG}
    only = next(iter(rest.values())) if len(rest) == 1 else None
    detail = only if isinstance(only, str) else json.dumps(rest, ensure_ascii=False)
    detail = " ".join(f"{name} {detail}".split())
    if len(detail) > DOING_CHARS:
        detail = detail[:DOING_CHARS] + "…"
    said = call.arguments.get(DESCRIPTION_ARG)
    return f"{said.strip()}: {detail}" if isinstance(said, str) and said.strip() else detail
