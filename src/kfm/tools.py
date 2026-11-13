"""The fleet tools: an MCP server every unit connects to, which is how an agent delegates,
answers its agents and reports. The unit is known by the token in its URL; what it may do
is the manager's to decide."""

from __future__ import annotations

import asyncio
import contextlib
import socket
from collections.abc import Iterator
from typing import TYPE_CHECKING, Annotated

import uvicorn
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from kfm.api import UnitError
from kfm.fleet import FleetError, Unit

if TYPE_CHECKING:
    from kfm.manager import Manager

TASK_CHARS = 200  # of a task or a report in the status list


class ToolServer:
    """An MCP server on a local port of its own, for as long as it is needed."""

    def __init__(self, tools: MCPServer) -> None:
        self._tools = tools
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task[None] | None = None
        self.url = ""

    async def start(self) -> None:
        app = self._tools.streamable_http_app(stateless_http=True, json_response=True)
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        self.url = f"http://127.0.0.1:{sock.getsockname()[1]}/mcp"
        self._server = uvicorn.Server(uvicorn.Config(app, log_level="warning",
                                                     lifespan="on"))
        self._task = asyncio.create_task(self._server.serve(sockets=[sock]))
        while not self._server.started:
            if self._task.done():
                await self._task  # raises why it did not start
            await asyncio.sleep(0.01)

    async def aclose(self) -> None:
        if self._server is not None and self._task is not None:
            self._server.should_exit = True
            await asyncio.gather(self._task, return_exceptions=True)


@contextlib.contextmanager
def _refusals() -> Iterator[None]:
    """What the fleet refuses reaches the agent in the fleet's own words."""
    try:
        yield
    except (FleetError, UnitError) as e:
        raise ToolError(str(e)) from None


def fleet_tools(manager: Manager) -> MCPServer:
    server = MCPServer("fleet")

    def caller(ctx: Context) -> Unit:
        token = ctx.request_context.request.query_params.get("unit", "")
        return manager.fleet.by_token(token)

    @server.tool(description=(
        "Hand a task to a new agent one rank below you. It works on its own, in the same "
        "folder, and sees nothing of your conversation: write the task so it stands alone "
        "(what to do, which files are its to change, what done looks like). Returns at "
        "once; its report, or a question, arrives later as a message."))
    async def delegate(
        name: Annotated[str, Field(description="A short name for the agent, by its role: "
                                               "Researcher, Tester.")],
        task: Annotated[str, Field(description="The complete task.")],
        ctx: Context,
        team: Annotated[str | None, Field(description="Its team, if it differs from "
                                                      "yours.")] = None,
        ongoing: Annotated[bool, Field(description="True for standing work that never "
                                                   "ends; false for a task that finishes "
                                                   "with a report.")] = False,
        budget_usd: Annotated[float | None, Field(
            gt=0, description="The most it may spend, in US dollars; at most the fleet's "
                              "limit per agent, which is the default.")] = None,
    ) -> str:
        with _refusals():
            unit = await manager.delegate(caller(ctx), name, task, team=team,
                                          ongoing=ongoing, budget_usd=budget_usd)
        if unit.state == "failed":
            raise ToolError(f"{unit.label} could not start: {unit.failure}")
        return (f"Started {unit.label}. Its report or question will arrive as a message; "
                "you can end your turn meanwhile.")

    @server.tool(description=(
        "Send a message to one of your agents: the answer to its question, a correction, "
        "or a new task for one that has finished or failed (it starts working again). "
        "Its reply arrives later as a message."))
    async def message(
        agent: Annotated[str, Field(description="The agent's id, such as u3.")],
        text: Annotated[str, Field(min_length=1)],
        ctx: Context,
    ) -> str:
        with _refusals():
            me = caller(ctx)
            unit = manager.fleet.subordinate(me, agent)
            await manager.tell(unit, f"[Message from {me.label}, your supervisor] {text}")
        return f"Sent to {unit.label}."

    @server.tool(description="Your agents: what each is doing, what it reported or asked, "
                             "and what it has spent.")
    async def status(ctx: Context) -> str:
        with _refusals():
            me = caller(ctx)
        lines = [_line(unit) for unit in manager.fleet.subordinates(me)]
        left = manager.config.budget_usd - manager.fleet.spent_usd
        return "\n".join(lines or ["You have no agents."]) + (
            f"\nThe fleet has ${left:.2f} of its ${manager.config.budget_usd:.2f} left.")

    @server.tool(description="Stop one of your agents, and every agent under it, when its "
                             "work is no longer needed.")
    async def stop(agent: Annotated[str, Field(description="The agent's id.")],
                   ctx: Context) -> str:
        with _refusals():
            unit = manager.fleet.subordinate(caller(ctx), agent)
            await manager.stop(unit)
        return f"Stopped {unit.label}."

    @server.tool(description=(
        "Send your supervisor an update without ending your work: progress worth knowing, "
        "a result, a warning. To finish a task use finish_goal; when blocked, "
        "ask_for_help."))
    async def report(text: Annotated[str, Field(min_length=1)], ctx: Context) -> str:
        with _refusals():
            await manager.update(caller(ctx), text)
        return "Sent."

    return server


def _line(unit: Unit) -> str:
    line = f"{unit.label}: {unit.state}, spent ${unit.spent_usd:.2f}. Task: {_clip(unit.task)}"
    if unit.state == "done" and unit.report:
        line += f" Report: {_clip(unit.report)}"
    elif unit.state == "waiting" and unit.question:
        line += f" Asks: {_clip(unit.question)}"
    elif unit.state == "failed" and unit.failure:
        line += f" Failure: {_clip(unit.failure)}"
    return line


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= TASK_CHARS else text[:TASK_CHARS] + "…"
