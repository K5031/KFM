"""A unit is an unchanged Kayden, run headless in its own data folder. This module writes
that folder's configuration, words the unit's brief, and starts its process."""

from __future__ import annotations

import asyncio
import json
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import tomli_w
from kayden.frontends.remote import Channel

from kfm.config import FleetConfig
from kfm.fleet import Unit

TOOLS = "fleet"  # the MCP server's name in a unit: its tools are mcp__fleet__<tool>
PREFIX = f"mcp__{TOOLS}__"
UNIT_RECORD = "unit.json"
_LINE_LIMIT = 16 * 1024 * 1024


def prepare(unit: Unit, folder: Path, config: FleetConfig, work: Path, tools_url: str) -> None:
    """The unit's Kayden data folder: the base configuration with this unit's model, limit
    and permissions, the fleet tools, and the folder the fleet works in. Blocking."""
    folder.mkdir(parents=True, exist_ok=True)
    base: dict[str, Any] = {}
    if config.kayden_config.exists():
        base = tomllib.loads(config.kayden_config.read_text())
    base.pop("frontend", None)
    runtime = base.setdefault("runtime", {})
    if model := config.model_for(unit.rank, unit.team):
        runtime["model"] = model
    runtime["permissions"] = config.permissions
    runtime["budget"] = {"max_cost_usd": unit.budget_usd}
    (folder / "config.toml").write_text(tomli_w.dumps(base))
    settings = {"work_folder": str(work),
                "mcp_servers": [{"name": TOOLS, "url": f"{tools_url}?unit={unit.token}"}]}
    (folder / "settings.toml").write_text(tomli_w.dumps(settings))
    (folder / UNIT_RECORD).write_text(json.dumps({"id": unit.id, "name": unit.name,
                                                  "rank": unit.rank}))


async def launch(command: Sequence[str], folder: Path, work: Path,
                 conversation_id: str | None = None) -> Channel:
    """Start the unit's Kayden; with a conversation, on that one again."""
    args = [*command, "--headless", "--data-dir", str(folder)]
    if conversation_id is not None:
        args += ["--conversation", conversation_id]
    process = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, cwd=work, limit=_LINE_LIMIT,
        start_new_session=True)  # Ctrl+C in the terminal is for the manager, which closes it
    return Channel(process)


RESUMED = (
    "[The fleet was stopped and has now been started again. Anything you had running in "
    "the background (a watcher, a long command) ended with it: start it again if you still "
    "need it. Look at what changed while the fleet was down, then carry on with your work. "
    "If you were waiting for an answer that has not arrived, ask again.]")


def answer_note(supervisor: Unit | None) -> str:
    """What a unit is told with a message that reaches it after its goal ended."""
    reader = supervisor.name if supervisor is not None else "the person"
    return ("(Your task had ended, so there is no goal to finish now. Deal with this, then "
            f"end your turn: what you write last goes to {reader} as your report, so make it "
            f"complete, and do not also send it with {PREFIX}report.)")


def brief(unit: Unit, supervisor: Unit | None) -> str:
    """The unit's standing instruction: who it is in the fleet, its task, and how the fleet
    works from where it stands."""
    lines = [(f"You are {unit.name}, a K{unit.rank} agent in a fleet of AI agents that works "
              "without human supervision. A higher rank supervises the one below it.")]
    if unit.team:
        lines.append(f"Your team: {unit.team}.")
    lines += ["", f"Your task: {unit.task}", "", "How the fleet works:"]
    if supervisor is None:
        lines.append("- A person gave you this task and you are their point of contact. "
                     "They are not watching: decide what you can, and ask them only what "
                     "only they can decide.")
    else:
        lines.append(f"- Your supervisor is {supervisor.name} (K{supervisor.rank}). It gave "
                     "you this task, sees nothing of your conversation, and gets what you "
                     "report.")
    if unit.ongoing:
        lines.append(f"- This is standing work: keep at it. When there is something your "
                     f"supervisor should know, send it with {PREFIX}report.")
    else:
        lines.append("- When the task is done, call finish_goal with a report: what you did, "
                     "what you found, where the results are, and what is not done. The "
                     "report is all your supervisor gets, so make it complete.")
    lines.append("- When you are blocked on something only your supervisor can decide or "
                 "provide, call ask_for_help with one clear question; the answer arrives as "
                 "a message.")
    if unit.rank > 1:
        lines.append(
            f"- You can hand work to K{unit.rank - 1} agents with {PREFIX}delegate. Give each "
            "a complete, self-contained task: it sees nothing of your conversation. Their "
            f"reports and questions arrive as messages; answer with {PREFIX}message, and see "
            f"them all with {PREFIX}status. Delegate what is big enough to be worth another "
            "agent and do small things yourself. Your job is then to coordinate: plan, "
            "split the work, check what comes back, and keep everyone on the goal. If an "
            "agent fails or stalls, give it the task again, hand it to another, or ask your "
            "own supervisor.")
    else:
        lines.append("- You are the lowest rank: do the work yourself.")
    lines.append("- Every agent works in the same folder. Say in each task which files it "
                 "owns, and do not change files that belong to another agent's task.")
    return "\n".join(lines)
