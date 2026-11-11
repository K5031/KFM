"""A scripted model for the units of a test fleet. The base Kayden configuration names it
as the Core and carries one script per unit name: a list of steps, each {text} or
{calls: [{name, arguments}]}. Every request of a turn takes the unit's next step; when
the script is used up the model answers "ok".

A step with `when` is taken only once the newest user message contains that text; until
then the model answers "ok" and keeps the step. A step with `report_last_result` finishes
the goal with the newest tool result as its report, so a test can read what a tool said."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any

from kayden.api import (
    Completed,
    Core,
    CoreEvent,
    InferenceRequest,
    InstancePaths,
    Message,
    ModelInfo,
    Role,
    StopReason,
    Text,
    TextDelta,
    ToolCall,
    ToolCallDelta,
    ToolResult,
    Usage,
)

MODELS = tuple(ModelInfo(name, name, context_window=100_000, max_output_tokens=1_000,
                         input_usd_per_mtok=1.0, output_usd_per_mtok=2.0)
               for name in ("fake-1", "fake-2"))


def factory(options: dict[str, Any], paths: InstancePaths) -> ScriptedCore:
    name = json.loads((paths.data_dir / "unit.json").read_text())["name"]
    return ScriptedCore(options.get("scripts", {}).get(name, []),
                        Usage(*options.get("usage", (100, 10))))


class ScriptedCore(Core):
    def __init__(self, script: Sequence[dict[str, Any]], usage: Usage) -> None:
        self._script, self._usage = list(script), usage

    async def list_models(self) -> Sequence[ModelInfo]:
        return MODELS

    async def stream(self, request: InferenceRequest) -> AsyncIterator[CoreEvent]:
        step: dict[str, Any] = {"text": ""}
        if not request.maintenance:
            step = {"text": "ok"}
            if self._script and self._script[0].get("when", "") in _last_user(request):
                step = self._script.pop(0)
            if step.get("report_last_result"):
                step = {"calls": [{"name": "finish_goal",
                                   "arguments": {"report": _last_result(request)}}]}
        blocks: list[Any] = []
        if text := step.get("text"):
            yield TextDelta(text)
            blocks.append(Text(text))
        for call in step.get("calls", ()):
            block = ToolCall(f"call_{uuid.uuid4().hex[:8]}", call["name"],
                             call.get("arguments", {}))
            yield ToolCallDelta(block.id, block.name, json.dumps(block.arguments))
            blocks.append(block)
        stop = StopReason.TOOL_USE if step.get("calls") else StopReason.END_TURN
        yield Completed(Message(uuid.uuid4().hex, Role.ASSISTANT, tuple(blocks), 0.0),
                        self._usage, stop)


def _last_user(request: InferenceRequest) -> str:
    for message in reversed(request.messages):
        if message.role == Role.USER:
            return "".join(b.text for b in message.blocks if isinstance(b, Text))
    return ""


def _last_result(request: InferenceRequest) -> str:
    for message in reversed(request.messages):
        for block in reversed(message.blocks):
            if isinstance(block, ToolResult):
                return block.content
    return ""
