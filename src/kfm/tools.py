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
from kfm.draft import Team
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


def _models(manager: Manager) -> str:
    """What a tool that starts agents says of their model: whose it is to choose, and
    from which models."""
    config = manager.config
    if not config.auto:
        return "Its model is the person's to set."
    known = manager.models()
    choices = "; ".join(f"{each} ({known[each]})" if known.get(each) else each
                        for each in config.auto_models)
    return f"Whoever starts an agent names its model (`model`), one of: {choices}."


def fleet_tools(manager: Manager) -> MCPServer:
    server = MCPServer("fleet")

    def caller(ctx: Context) -> Unit:
        token = ctx.request_context.request.query_params.get("unit", "")
        return manager.fleet.by_token(token)

    @server.tool(description=(
        "Start a new agent one rank below you on a task. It works on its own, in the same "
        "folder, and sees nothing of your conversation, only the task. Returns at once; "
        "its report, or a question, arrives later as a message. " + _models(manager)))
    async def delegate(
        task: Annotated[str, Field(min_length=1, description="The task.")],
        ctx: Context,
        name: Annotated[str, Field(description="A name for the agent; it may have "
                                               "none.")] = "",
        team: Annotated[str | None, Field(description="Its team, if it differs from "
                                                      "yours.")] = None,
        ongoing: Annotated[bool, Field(description="True for standing work that never "
                                                   "ends; false for a task that finishes "
                                                   "with a report.")] = False,
        budget_usd: Annotated[float | None, Field(
            gt=0, description="The most it may spend, in US dollars; at most the fleet's "
                              "limit per agent, which is the default.")] = None,
        model: Annotated[str | None, Field(
            description="The model it runs on, where that is yours to name.")] = None,
    ) -> str:
        with _refusals():
            unit = await manager.delegate(caller(ctx), name, task, team=team,
                                          ongoing=ongoing, budget_usd=budget_usd, model=model)
        if unit.state == "failed":
            raise ToolError(f"{unit.label} could not start: {unit.failure}")
        return (f"Started {unit.label}. Its report or question will arrive as a message; "
                "you can end your turn meanwhile.")

    @server.tool(description=(
        "Send a message to one of your agents. One that has finished, failed or has no "
        "task yet starts working on it. Its reply arrives later as a message. The lead of "
        "a team in a project also reaches another team's lead by that team's name, and "
        "the project's secretary by \"secretary\"; a team that is not running, or whose "
        "lead finished or failed, is started by it."))
    async def message(
        agent: Annotated[str, Field(description="The agent's id, such as K4003.")],
        text: Annotated[str, Field(min_length=1)],
        ctx: Context,
    ) -> str:
        with _refusals():
            me = caller(ctx)
            try:
                unit = manager.fleet.subordinate(me, agent)
            except FleetError:
                if me.supervisor is not None or manager.project is None:
                    raise
                return f"Sent to {await manager.project.send(me, agent, text)}."
            await manager.message(me, unit, text)
        return f"Sent to {unit.label}."

    @server.tool(description="Your agents: what each is doing, what it reported or asked, "
                             "and what it has spent.")
    async def status(ctx: Context) -> str:
        with _refusals():
            me = caller(ctx)
        lines = [_line(unit) for unit in manager.fleet.subordinates(me)]
        left = manager.config.budget_usd - manager.fleet.spent_usd
        teams = (f"\nThe project's other teams, each with a limit of its own:\n"
                 f"{manager.project.listing()}"
                 if manager.project is not None and me.supervisor is None else "")
        return "\n".join(lines or ["You have no agents."]) + (
            f"\nYour fleet has ${left:.2f} of its ${manager.config.budget_usd:.2f} left."
            + teams)

    @server.tool(description="Stop one of your agents, and every agent under it.")
    async def stop(agent: Annotated[str, Field(description="The agent's id.")],
                   ctx: Context) -> str:
        with _refusals():
            unit = manager.fleet.subordinate(caller(ctx), agent)
            await manager.stop_unit(unit)
        return f"Stopped {unit.label}."

    @server.tool(description=(
        "Send your supervisor an update without ending your work."))
    async def report(text: Annotated[str, Field(min_length=1)], ctx: Context) -> str:
        with _refusals():
            await manager.update(caller(ctx), text)
        return "Sent."

    return server


def secretary_tools(manager: Manager) -> MCPServer:
    """What a project's secretary has in place of the fleet tools."""
    server = MCPServer("fleet")
    project = manager.project
    assert project is not None

    @server.tool(description=(
        "How each team of the project stands: its lead and what it is doing, what the "
        "team has spent, and what was lately reported, asked and said to it. With `team`, "
        "more of that team's record."))
    async def teams(
        team: Annotated[str | None, Field(description="A team's name.")] = None,
    ) -> str:
        with _refusals():
            return await project.standing(team)

    @server.tool(description=(
        "Write to a team's lead, who reads it as coming from the project's secretary. Its "
        "answer, if it writes one, arrives later as a message. A team that is not running, "
        "or whose lead failed, is started by it."))
    async def message(
        team: Annotated[str, Field(description="The team's name.")],
        text: Annotated[str, Field(min_length=1)],
    ) -> str:
        with _refusals():
            me = manager.fleet.lead
            assert me is not None
            return f"Sent to {await project.send(me, team, text)}."

    @server.tool(description=(
        "Put teams to the person: each with its name, what it is there to do, and "
        "whether that is standing work or a task that finishes. The person reads the "
        "proposal and may change it; a team exists once they start it. A team's lead is "
        "the agent a proposal starts. " + _models(manager)))
    async def propose(teams: Annotated[list[Team], Field(min_length=1)]) -> str:
        if len({team.name.strip().lower() for team in teams}) < len(teams):
            raise ToolError("Two teams have the same name.")
        for team in teams:
            try:
                manager.config.chosen(team.model)
            except ValueError as e:
                raise ToolError(f"{team.name}: {e}") from None
        project.propose(teams)
        return "Shown to the person. Nothing exists of it until they start it."

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
