"""Kayden as a fleet's unit: an unchanged Kayden, run headless in the unit's data folder.
This module writes that folder's configuration, starts the process, and turns what
Kayden's headless channel says into the unit contract's events (kfm.api)."""

from __future__ import annotations

import asyncio
import json
import sys
import tomllib
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import tomli_w
from kayden.api import (
    AgentState,
    ConversationUpdated,
    EventEnvelope,
    Failure,
    HelpNeeded,
    MessageCommitted,
    PermissionRequested,
    Role,
    RuntimeSnapshot,
    RuntimeStateError,
    StateChanged,
    Text,
    ToolCall,
    UsageUpdated,
)
from kayden.frontends.remote import Channel
from kayden.frontends.wire import from_json

from kfm.api import (
    ApprovalNeeded,
    Asked,
    Exited,
    Failed,
    Harness,
    Instruction,
    Reported,
    Rested,
    Said,
    Session,
    Spent,
    UnitError,
    UnitEvent,
    UnitSpec,
    Vocabulary,
    Working,
)

TOOLS = "fleet"  # the MCP server's name in a unit: its tools are mcp__fleet__<tool>
WORDS = Vocabulary(finish="finish_goal", ask="ask_for_help", tools=f"mcp__{TOOLS}__")
UNIT_RECORD = "unit.json"
_LINE_LIMIT = 16 * 1024 * 1024
_ACTIVE = (AgentState.THINKING, AgentState.RUNNING_TOOLS, AgentState.AWAITING_PERMISSION)


def factory(options: dict[str, Any]) -> KaydenHarness:
    """Options: `config`, the Kayden configuration every unit starts from: its provider,
    key and options (default ~/.kayden/config.toml); `command`, how Kayden is started
    (default: this Python's `-m kayden`)."""
    options = dict(options)
    config = Path(options.pop("config", Path.home() / ".kayden" / "config.toml")).expanduser()
    command = tuple(options.pop("command", (sys.executable, "-m", "kayden")))
    if options:
        raise TypeError(f"unknown option {', '.join(sorted(options))}")
    return KaydenHarness(config, command)


class KaydenHarness(Harness):
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

    def prepare(self, spec: UnitSpec) -> None:
        """The unit's Kayden data folder: the base configuration with this unit's model,
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
        self._has_duty = False
        self._failure: str | None = None  # what Kayden said before its state became failed
        self._closed = False
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
        low, high = instruction.check_in_s
        await self._request("set_duty", duty={
            "instruction": instruction.text,
            "kind": "duty" if instruction.ongoing else "goal",
            "min_interval_s": low, "max_interval_s": high})
        return info["id"]

    async def send(self, text: str) -> None:
        await self._request("send", text=text)

    async def approve(self, request_id: str, allow: bool) -> None:
        await self._request("resolve_permission", request_id=request_id,
                            decision="allow_once" if allow else "deny")

    async def close(self, grace_s: float) -> None:
        self._closed = True
        await self._channel.close(grace_s)

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
                        if self._has_duty:  # else Kayden refuses the call
                            self._emit(Reported(block.arguments.get("report") or "Done."))
                    elif isinstance(block, Text) and block.text.strip():
                        self._emit(Said(block.text.strip()))
            case HelpNeeded(question=question):
                self._emit(Asked(question))
            case ConversationUpdated(info=info) if self._conversation in (None, info.id):
                self._has_duty = info.duty is not None
            case UsageUpdated(snapshot=snapshot):
                self._emit(Spent(snapshot.usage.cost_usd or 0.0))
            case Failure(message=message):
                self._failure = message
            case PermissionRequested(request=request):
                if request.call.name.startswith(WORDS.tools):  # the fleet's own tools
                    answer = asyncio.ensure_future(self._channel.request(
                        "resolve_permission", request_id=request.id,
                        decision="allow_conversation"))
                    self._answers.add(answer)
                    answer.add_done_callback(self._answers.discard)
                else:
                    self._emit(ApprovalNeeded(request.id, request.call.name,
                                              str(request.call.arguments)))
            case StateChanged(state=state):
                active = state in _ACTIVE
                if state == AgentState.FAILED:
                    self._emit(Failed(self._failure or "It stopped with an error."))
                elif active and not self._active:
                    self._emit(Working())
                elif self._active and not active:
                    self._emit(Rested(self._has_duty))
                self._active = active
