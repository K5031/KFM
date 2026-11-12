"""The `kfm` command: start a fleet on a task and follow it from the terminal."""

from __future__ import annotations

import argparse
import asyncio
import os
import stat
import sys
import time
from collections import deque
from pathlib import Path

from kfm.api import UnitError
from kfm.config import CONFIG_FILE, HOME, load
from kfm.events import Approval, Changed, Delivered, FleetEvent, Notice, Said
from kfm.fleet import Fleet, FleetError, Unit
from kfm.manager import Manager

LINE_CHARS = 300  # of what an agent below the lead says, in the log


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kfm", description="Run a fleet of Kayden agents on a task.")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="start a fleet on a task and follow it")
    run.add_argument("task", help="what the fleet should achieve")
    run.add_argument("--duty", action="store_true",
                     help="standing work that never ends, instead of a goal")
    run.add_argument("--work", type=Path, default=Path.cwd(),
                     help="the folder the agents work in (default: this one)")
    run.add_argument("--fleet", type=Path, help="where the fleet keeps its files "
                                                "(default: a new folder in ~/.kfm/fleets)")
    run.add_argument("--config", type=Path, default=HOME / CONFIG_FILE,
                     help="the fleet's settings (default: ~/.kfm/fleet.toml)")
    run.add_argument("--budget", type=float, dest="budget_usd",
                     help="the most the whole fleet may spend, in US dollars")
    run.add_argument("--unit-budget", type=float, dest="unit_budget_usd",
                     help="the most one agent may spend")
    run.add_argument("--top-rank", type=int, choices=range(1, 6),
                     help="the lead's rank: 5 allows four levels below it, 1 none")
    resume = commands.add_parser("resume", help="start a stopped fleet again and follow it")
    resume.add_argument("--fleet", type=Path, help="the fleet's folder (default: the newest)")
    resume.add_argument("--budget", type=float, dest="budget_usd",
                        help="a new limit for the whole fleet, in US dollars")
    resume.add_argument("--unit-budget", type=float, dest="unit_budget_usd",
                        help="a new limit for agents started from now on")
    status = commands.add_parser("status", help="show a fleet's agents")
    status.add_argument("--fleet", type=Path, help="the fleet's folder (default: the newest)")
    args = parser.parse_args(argv)
    try:
        if args.command == "status":
            return _status(args.fleet)
        if args.command == "resume":
            return asyncio.run(_resume(args))
        return asyncio.run(_run(args))
    except (ValueError, OSError) as e:
        print(f"kfm: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


async def _run(args: argparse.Namespace) -> int:
    config = load(args.config, budget_usd=args.budget_usd,
                  unit_budget_usd=args.unit_budget_usd, top_rank=args.top_rank)
    folder = args.fleet or HOME / "fleets" / time.strftime("%Y%m%d-%H%M%S")
    console = Console()
    manager = Manager(config, folder.resolve(), args.work.resolve(), on_event=console.show)
    return await _follow(manager, console, folder, task=args.task, ongoing=args.duty)


async def _resume(args: argparse.Namespace) -> int:
    folder = args.fleet or _newest()
    if folder is None:
        print("There is no fleet yet. Start one with: kfm run \"<task>\"")
        return 1
    fleet = await asyncio.to_thread(Fleet.load, folder)
    if not any(unit.active for unit in fleet.units.values()):
        print(f"Nothing was at work in the fleet in {folder}: every agent had finished or "
              "was stopped.")
        return 1
    console = Console()
    manager = await asyncio.to_thread(
        lambda: Manager.resumed(folder.resolve(), on_event=console.show,
                                budget_usd=args.budget_usd,
                                unit_budget_usd=args.unit_budget_usd))
    return await _follow(manager, console, folder)


async def _follow(manager: Manager, console: Console, folder: Path, *,
                  task: str | None = None, ongoing: bool = False) -> int:
    """Run the fleet until it ends or is interrupted; with `task`, start it on that."""
    console.manager = manager
    print(f"Fleet files: {folder}. Type a message for the lead; Ctrl+C stops the fleet, "
          "and kfm resume starts it again.")
    await manager.start()
    reader = asyncio.create_task(console.read())
    try:
        if task is not None:
            lead = await manager.lead(task, ongoing=ongoing)
            if lead.state == "failed":
                return 1
        await manager.finished.wait()
        return 0
    finally:
        reader.cancel()
        await manager.aclose()
        print(f"The fleet spent ${manager.fleet.spent_usd:.2f}.")


class Console:
    """The fleet as lines in a terminal; what the person types goes to the lead, or answers
    the oldest open approval with y or n."""

    def __init__(self) -> None:
        self.manager: Manager | None = None
        self.approvals: deque[Approval] = deque()

    def show(self, event: FleetEvent) -> None:
        match event:
            case Changed(unit=unit, state=state):
                print(f"[{_tag(unit)}] {state}")
            case Said(unit=unit, text=text):
                print(f"[{_tag(unit)}] {text if unit.supervisor is None else _clip(text)}")
            case Delivered(unit=unit, to=to, kind=kind, text=text):
                target = to.name if to is not None else "you"
                print(f"[{_tag(unit)} → {target}] {kind}: {text}")
            case Approval(unit=unit, tool=tool, arguments=arguments):
                self.approvals.append(event)
                print(f"[{_tag(unit)}] wants to run {tool} {_clip(arguments)}. Allow? (y/n)")
            case Notice(text=text):
                print(f"! {text}")
        sys.stdout.flush()

    async def read(self) -> None:
        try:
            mode = os.fstat(sys.stdin.fileno()).st_mode
        except (OSError, ValueError):
            return
        # A file or /dev/null cannot be waited on: the fleet then runs on its own.
        if not (sys.stdin.isatty() or stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)):
            return
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
        while line := await reader.readline():
            if text := line.decode(errors="replace").strip():
                await self._typed(text)

    async def _typed(self, text: str) -> None:
        assert self.manager is not None
        try:
            if self.approvals and text.lower() in ("y", "yes", "n", "no"):
                approval = self.approvals.popleft()
                await self.manager.approve(approval.unit, approval.request_id,
                                           text.lower().startswith("y"))
            else:
                await self.manager.say(text)
        except (FleetError, UnitError) as e:
            print(f"! {e}")


def _newest() -> Path | None:
    fleets = sorted((HOME / "fleets").glob("*/fleet.json"))
    return fleets[-1].parent if fleets else None


def _status(folder: Path | None) -> int:
    folder = folder or _newest()
    if folder is None:
        print("There is no fleet yet. Start one with: kfm run \"<task>\"")
        return 1
    fleet = Fleet.load(folder)
    state = "running" if fleet.running else "stopped"
    print(f"{folder}: {state}, {len(fleet.units)} agents, ${fleet.spent_usd:.2f} spent")
    if (lead := fleet.lead) is not None:
        for unit in fleet.subtree(lead):
            depth = fleet.top_rank - unit.rank
            print(f"{'  ' * depth}{unit.label}: {unit.state}, ${unit.spent_usd:.2f}. "
                  f"{_clip(unit.task, 100)}")
    return 0


def _tag(unit: Unit) -> str:
    return f"{unit.name} K{unit.rank} {unit.id}"


def _clip(text: str, chars: int = LINE_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= chars else text[:chars] + "…"


if __name__ == "__main__":
    sys.exit(main())
