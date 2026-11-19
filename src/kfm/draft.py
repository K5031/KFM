"""Drafting a project from a description: one agent proposes which fleets it needs, and
ends. It is no lead: nothing starts until the person says so."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from kfm import api
from kfm.config import FleetConfig
from kfm.tools import ToolServer

STOP_GRACE_S = 5.0
READ_ONLY = "allow_read_only"  # the drafting agent looks and proposes; it does none of the work


class DraftError(Exception):
    """The agent ended without a proposal; the message is for a person."""


class Team(BaseModel):
    name: Annotated[str, Field(min_length=1, description="Short: marketing, dev.")]
    does: Annotated[str, Field(min_length=1, description=(
        "What it is there to do, written to its lead, who knows nothing else."))]
    ongoing: Annotated[bool, Field(description=(
        "True for standing work that never ends; false for a task that finishes."))] = True


@dataclass(frozen=True, slots=True)
class Draft:
    teams: tuple[Team, ...]
    spent_usd: float


def brief(description: str, work: Path, words: api.Vocabulary) -> str:
    """What the drafting agent is told: what is so, and what its tools do."""
    return (
        "A person is setting up an organisation of AI agents and has described what it "
        f"should do:\n\n{description.strip()}\n\n"
        "Your task: propose the teams this organisation is made of.\n\n"
        "Each team has a lead agent that takes on what the team is there to do and hires "
        "agents under it as the work needs, so a team's size is its lead's to decide. A "
        "team's lead runs, and is paid for, for as long as the team does. The teams work "
        f"side by side in the folder {work}, do not talk to each other, and each reports "
        "to the person.\n\n"
        f"{words.tool('propose')} shows the person your proposal, which they read and may "
        f"change before anything starts. {words.finish} ends your task. You can read the "
        "folder; you are not able to change it or to run commands.")


async def draft(description: str, *, config: FleetConfig, harness: api.Harness,
                folder: Path, work: Path) -> Draft:
    """Have one agent propose the teams for `description`. `folder` is the agent's own.
    DraftError when it ends without a proposal; UnitError when it could not run."""
    proposed: list[Team] = []
    over = asyncio.Event()
    problem = ""
    spent = 0.0
    tools = MCPServer("fleet")

    @tools.tool(description="Propose the organisation's teams to the person.")
    async def propose(teams: Annotated[list[Team], Field(min_length=1)]) -> str:
        if len({team.name.strip().lower() for team in teams}) < len(teams):
            raise ToolError("Two teams have the same name.")
        proposed[:] = teams
        over.set()
        return "Shown to the person."

    def on_event(event: api.UnitEvent) -> None:
        nonlocal problem, spent
        match event:
            case api.Spent(usd=usd):
                spent = usd
                return
            case api.Asked(question=question):
                problem = f"It asked instead of proposing: {question}"
            case api.Failed(message=message) | api.Exited(why=message):
                problem = message
            case api.ApprovalNeeded(request_id=request_id):
                refusing.append(asyncio.ensure_future(session.approve(request_id, False)))
                return
            case api.Rested(carries_on=False):
                problem = problem or "It ended without proposing anything."
            case _:
                return
        over.set()

    refusing: list[asyncio.Future[None]] = []
    server = ToolServer(tools)
    await server.start()
    try:
        await asyncio.to_thread(folder.mkdir, parents=True, exist_ok=True)
        session = await harness.open(api.UnitSpec(
            "u1", "Planner", config.top_rank, folder, work,
            config.model_for(config.top_rank, None), READ_ONLY,
            config.unit_budget_usd, server.url), on_event)
        try:
            await session.begin(api.Instruction(
                brief(description, work, harness.vocabulary), False, config.check_in_s))
            await over.wait()
        finally:
            await asyncio.gather(*refusing, return_exceptions=True)
            await session.close(STOP_GRACE_S)
    finally:
        await server.aclose()
    if not proposed:
        raise DraftError(problem or "It ended without proposing anything.")
    return Draft(tuple(proposed), spent)
