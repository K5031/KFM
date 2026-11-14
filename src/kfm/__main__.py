"""`kfm`: KFM from a terminal. A frontend of the service (kfm.service), as the window is:
every command is a call on it, and the service that `kfm serve` runs is started when none
is there. `kfm run "<task>"` starts a fleet and follows it."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import signal
import stat
import sys
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path

from kfm.api import UnitError
from kfm.config import HOME
from kfm.draft import DraftError
from kfm.events import Approval, Changed, Delivered, Instructed, Notice, Said
from kfm.fleet import Unit
from kfm.remote import RemoteService, ServiceError
from kfm.service import Happened, ProjectInfo, Service, Update, Updated

LINE_CHARS = 300  # of what an agent below the lead says, in the log
NO_FLEET = 'There is no fleet yet. Start one with: kfm run "<task>"'


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kfm", description="Run fleets of Kadence agents on tasks and duties.")
    parser.add_argument("--home", type=Path, default=HOME,
                        help="where projects and settings are kept (default: ~/.kfm)")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="start a fleet on a task and follow it")
    run.add_argument("task", help="what the fleet should achieve")
    run.add_argument("--duty", action="store_true",
                     help="standing work that never ends, instead of a goal")
    run.add_argument("--work", type=Path, default=Path.cwd(),
                     help="the folder the agents work in (default: this one)")
    run.add_argument("--project", help="the project it belongs to (default: the one whose "
                                       "folder this is, made if there is none)")
    run.add_argument("--name", help="what the fleet is called")
    run.add_argument("--budget", type=float, dest="budget_usd",
                     help="the most the whole fleet may spend, in US dollars")
    run.add_argument("--detach", action="store_true",
                     help="start it and return: it keeps running")

    for name, text in (("watch", "follow a fleet and talk to its lead; Ctrl+C lets go of it"),
                       ("resume", "start a stopped fleet again and follow it"),
                       ("stop", "stop a fleet; it can be resumed")):
        command = commands.add_parser(name, help=text)
        command.add_argument("fleet", nargs="?", help="its name or id (default: the newest)")
        if name == "resume":
            command.add_argument("--budget", type=float, dest="budget_usd",
                                 help="a new limit for the whole fleet, in US dollars")
    status = commands.add_parser("status", help="show projects, fleets and their agents")
    status.add_argument("fleet", nargs="?", help="one fleet's name or id (default: all)")

    commands.add_parser("projects", help="list the projects")
    project = commands.add_parser("project", help="make, rename or delete a project")
    what = project.add_subparsers(dest="action", required=True)
    new = what.add_parser("new")
    new.add_argument("name")
    new.add_argument("--work", type=Path, default=Path.cwd(),
                     help="the folder its fleets work in (default: this one)")
    rename = what.add_parser("rename")
    rename.add_argument("project")
    rename.add_argument("name")
    what.add_parser("delete").add_argument("project")

    fleet = commands.add_parser("fleet", help="add, rename or delete a fleet")
    what = fleet.add_subparsers(dest="action", required=True)
    new = what.add_parser("new", help="with a task a lead builds it; with none it has no "
                                      "agents until you add some")
    new.add_argument("project")
    new.add_argument("name")
    new.add_argument("task", nargs="?", default="")
    new.add_argument("--goal", action="store_true",
                     help="a task that finishes, instead of standing work")
    new.add_argument("--work", type=Path, help="default: the project's folder")
    new.add_argument("--budget", type=float, dest="budget_usd")
    rename = what.add_parser("rename")
    rename.add_argument("fleet")
    rename.add_argument("name")
    what.add_parser("delete").add_argument("fleet")

    agent = commands.add_parser("agent", help="add, remove or change an agent")
    what = agent.add_subparsers(dest="action", required=True)
    add = what.add_parser("add", help="under another agent, or as the lead of a fleet "
                                      "that has none")
    add.add_argument("fleet")
    add.add_argument("--under", help="its supervisor's id, such as u1")
    add.add_argument("--name", default="")
    add.add_argument("--task", default="",
                     help="with none it waits for its first message")
    add.add_argument("--duty", action="store_true")
    add.add_argument("--model")
    for name, extra in (("remove", ()), ("rename", ("name",)), ("task", ("task",)),
                        ("model", ("model",))):
        change = what.add_parser(name)
        change.add_argument("fleet")
        change.add_argument("unit", help="the agent's id, such as u2")
        for argument in extra:
            if argument == "model":
                change.add_argument(argument, nargs="?", default="",
                                    help="with none, its rank's")
            else:
                change.add_argument(argument)

    say = commands.add_parser("say", help="a message to a fleet's lead, or to one agent")
    say.add_argument("fleet")
    say.add_argument("text")
    say.add_argument("--to", help="the agent's id (default: the lead)")
    say.add_argument("--file", action="append", default=[], help="a file to go with it")
    approve = commands.add_parser("approve", help="answer what an agent asked to do")
    approve.add_argument("fleet")
    approve.add_argument("unit")
    approve.add_argument("request")
    approve.add_argument("answer", choices=("yes", "no"))

    settings = commands.add_parser("settings", help="show or change what new fleets get")
    settings.add_argument("--budget", type=float, dest="budget_usd")
    settings.add_argument("--unit-budget", type=float, dest="unit_budget_usd")
    settings.add_argument("--max-agents", type=int, dest="max_units")
    settings.add_argument("--model", action="append", default=[], metavar="RANK=ID",
                          help="K5=claude-sonnet-5-5; RANK= takes it out")

    serve = commands.add_parser(
        "serve", help="run KFM's service, which owns the fleets, until none runs and no "
                      "frontend is attached; frontends start it by themselves")
    serve.add_argument("--socket", type=Path, help="default: service.sock in the home")

    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            return asyncio.run(_serve(args))
        return asyncio.run(_attached(args))
    except (ValueError, OSError, RuntimeError, UnitError, DraftError) as e:
        print(f"kfm: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


async def _serve(args: argparse.Namespace) -> int:
    from kfm.local import LocalService
    from kfm.server import SOCKET, Server

    await Server(LocalService(args.home), args.socket or args.home / SOCKET).run()
    return 0


async def _attached(args: argparse.Namespace) -> int:
    service = await RemoteService.connect(args.home)
    try:
        return await _COMMANDS[args.command](service, args)
    finally:
        await service.aclose()


# ── Commands ─────────────────────────────────────────────────────────────


async def _run(service: Service, args: argparse.Namespace) -> int:
    work = args.work.resolve()
    if not work.is_dir():
        raise ValueError(f"There is no folder {work}.")
    listed = await service.projects()
    if args.project is not None:
        owner = _one_project(listed, args.project)
    else:
        mine = [each for each in listed if Path(each.work) == work]
        owner = mine[0] if mine else await service.create_project(work.name, str(work))
    name = args.name or f"fleet {owner.fleets + 1}"
    fleet = await service.add_fleet(owner.id, name, args.task, ongoing=args.duty,
                                    work=str(work), budget_usd=args.budget_usd)
    print(f"Fleet {fleet} ({name}, in {owner.name}).")
    if args.detach:
        print(f"It runs on its own: kfm watch {fleet} follows it, kfm stop {fleet} stops it.")
        return 0
    return await _follow(service, fleet, ends=True)


async def _watch(service: Service, args: argparse.Namespace) -> int:
    fleet = await _one_fleet(service, args.fleet)
    return 1 if fleet is None else await _follow(service, fleet, ends=False)


async def _resume(service: Service, args: argparse.Namespace) -> int:
    fleet = await _one_fleet(service, args.fleet)
    if fleet is None:
        return 1
    record = service.fleet(fleet)
    assert record is not None
    if service.state(fleet).running:
        print(f"{fleet} is running: kfm watch follows it.")
        return 1
    if not any(unit.active or unit.state == "planned" for unit in record.units.values()):
        print(f"Nothing was at work in {fleet}: every agent had finished or was stopped.")
        return 1
    await service.start(fleet, budget_usd=args.budget_usd)
    return await _follow(service, fleet, ends=True)


async def _stop(service: Service, args: argparse.Namespace) -> int:
    fleet = await _one_fleet(service, args.fleet)
    if fleet is None:
        return 1
    await service.stop(fleet)
    print(f"{fleet} is stopped.")
    return 0


async def _status(service: Service, args: argparse.Namespace) -> int:
    if args.fleet is not None:
        fleet = await _one_fleet(service, args.fleet)
        if fleet is not None:
            _print_fleet(service, fleet, "")
        return 0 if fleet is not None else 1
    listed = await service.projects()
    if not any(each.fleets for each in listed):
        print(NO_FLEET)
        return 1
    for each in listed:
        print(f"{each.name} ({each.id}), in {each.work}")
        for fleet in await service.fleets(each.id):
            _print_fleet(service, fleet, "  ")
    return 0


async def _projects(service: Service, args: argparse.Namespace) -> int:
    for each in await service.projects():
        fleets = "1 fleet" if each.fleets == 1 else f"{each.fleets} fleets"
        print(f"{each.id}  {each.name}: {fleets}, {each.running} running, in {each.work}")
    return 0


async def _project(service: Service, args: argparse.Namespace) -> int:
    if args.action == "new":
        work = args.work.resolve()
        if not work.is_dir():
            raise ValueError(f"There is no folder {work}.")
        made = await service.create_project(args.name, str(work))
        print(f"Project {made.id} ({made.name}).")
        return 0
    owner = _one_project(await service.projects(), args.project)
    if args.action == "rename":
        await service.rename_project(owner.id, args.name)
    else:
        await service.delete_project(owner.id)
        print(f"{owner.name} is deleted; {owner.work} is as it was.")
    return 0


async def _fleet(service: Service, args: argparse.Namespace) -> int:
    if args.action == "new":
        owner = _one_project(await service.projects(), args.project)
        fleet = await service.add_fleet(
            owner.id, args.name, args.task, ongoing=not args.goal,
            work=str(args.work.resolve()) if args.work else None, budget_usd=args.budget_usd)
        print(f"Fleet {fleet} ({args.name})." + ("" if args.task else
              f" It has no agents: kfm agent add {fleet} gives it its lead."))
        return 0
    fleet = await _one_fleet(service, args.fleet)
    if fleet is None:
        return 1
    if args.action == "rename":
        await service.rename_fleet(fleet, args.name)
    else:
        await service.delete_fleet(fleet)
        print(f"{fleet} is deleted.")
    return 0


async def _agent(service: Service, args: argparse.Namespace) -> int:
    fleet = await _one_fleet(service, args.fleet)
    if fleet is None:
        return 1
    match args.action:
        case "add":
            unit = await service.add_agent(fleet, args.under, args.name, args.task,
                                           ongoing=args.duty, model=args.model)
            print(f"Agent {unit}.")
        case "remove":
            await service.remove_agent(fleet, args.unit)
        case "rename":
            await service.rename_agent(fleet, args.unit, args.name)
        case "task":
            await service.set_task(fleet, args.unit, args.task)
        case "model":
            await service.set_model(fleet, args.unit, args.model)
    return 0


async def _say(service: Service, args: argparse.Namespace) -> int:
    fleet = await _one_fleet(service, args.fleet)
    if fleet is None:
        return 1
    files = [str(Path(file).resolve()) for file in args.file]
    await service.say(fleet, args.text, args.to, files=files)
    return 0


async def _approve(service: Service, args: argparse.Namespace) -> int:
    fleet = await _one_fleet(service, args.fleet)
    if fleet is None:
        return 1
    await service.approve(fleet, args.unit, args.request, args.answer == "yes")
    return 0


async def _settings(service: Service, args: argparse.Namespace) -> int:
    changes = {key: value for key in ("budget_usd", "unit_budget_usd", "max_units")
               if (value := getattr(args, key)) is not None}
    if args.model:
        if not all("=" in each for each in args.model):
            raise ValueError("--model takes RANK=ID, such as K5=claude-sonnet-5-5")
        changes["models"] = dict(each.split("=", 1) for each in args.model)
    now = await service.save_settings(**changes) if changes else await service.settings()
    print(f"A fleet may spend ${now.budget_usd:.2f}, an agent ${now.unit_budget_usd:.2f}; "
          f"at most {now.max_units} agents in a fleet.")
    for rank, model in sorted(now.models.items(), reverse=True):
        print(f"{rank}: {model}")
    return 0


_COMMANDS: dict[str, Callable[[Service, argparse.Namespace], Awaitable[int]]] = {
    "run": _run, "watch": _watch, "resume": _resume, "stop": _stop, "status": _status,
    "projects": _projects, "project": _project, "fleet": _fleet, "agent": _agent,
    "say": _say, "approve": _approve, "settings": _settings}


# ── Finding what a name means ────────────────────────────────────────────


def _one_project(listed: list[ProjectInfo], text: str) -> ProjectInfo:
    found = [each for each in listed if text in (each.id, each.name)]
    if len(found) != 1:
        raise ValueError(f"There is no project {text!r}." if not found else
                         f"{text!r} is {len(found)} projects: use an id from kfm projects.")
    return found[0]


async def _one_fleet(service: Service, text: str | None) -> str | None:
    """The fleet a name or an id means, watched; with none, the newest. None, and a line
    that says so, when there is no fleet at all."""
    every: list[tuple[str, str, str]] = []  # id, its project's name, its name
    for each in await service.projects():
        for fleet in await service.fleets(each.id):
            record = service.fleet(fleet)
            every.append((fleet, each.name, record.name if record is not None else ""))
    if not every:
        print(NO_FLEET)
        return None
    if text is None:
        return max(every, key=lambda found: found[0].split("/")[1])[0]
    found = [fleet for fleet, owner, name in every
             if text in (fleet, name, f"{owner}/{name}")]
    if len(found) != 1:
        raise ValueError(f"There is no fleet {text!r}." if not found else
                         f"{text!r} is {len(found)} fleets: use an id from kfm status.")
    return found[0]


def _print_fleet(service: Service, fleet: str, indent: str) -> None:
    record = service.fleet(fleet)
    if record is None:
        return
    state = service.state(fleet)
    how = "running" if state.running else "stopping" if state.stopping else "stopped"
    print(f"{indent}{record.name} ({fleet}): {how}, {len(record.units)} agents, "
          f"${record.spent_usd:.2f} of ${state.budget_usd:.2f}")
    if (lead := record.lead) is not None:
        for unit in record.subtree(lead):
            depth = record.top_rank - unit.rank + 1
            print(f"{indent}{'  ' * depth}{unit.label}: {unit.state}, ${unit.spent_usd:.2f}. "
                  f"{_clip(unit.task, 100)}")


# ── Following a fleet ────────────────────────────────────────────────────


async def _follow(service: Service, fleet: str, *, ends: bool) -> int:
    """Show what happens in the fleet until it ends or the person interrupts. `ends`:
    the fleet is this command's, stopped when its task is done or on Ctrl+C; otherwise
    Ctrl+C only lets go of it."""
    console = Console(service, fleet)
    over = asyncio.Event()

    def heard(update: Update) -> None:
        match update:
            case Happened(fleet=which, event=event) if which == fleet:
                console.show(event)
            case Updated(fleet=which) if which == fleet:
                state = service.state(fleet)
                if (ends and state.finished) or not (state.running or state.stopping):
                    over.set()

    done = service.subscribe(heard)
    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError):
        loop.add_signal_handler(signal.SIGINT, over.set)
    print("Type a message for the lead; Ctrl+C " + (
        "stops the fleet, and kfm resume starts it again." if ends
        else "lets go of it, and it keeps running."))
    reader = asyncio.create_task(console.read())
    try:
        if not service.state(fleet).running:
            over.set()
        await over.wait()
    finally:
        done()
        reader.cancel()
        with contextlib.suppress(NotImplementedError):
            loop.remove_signal_handler(signal.SIGINT)
    if not ends:
        return 0
    await service.stop(fleet)
    record = service.fleet(fleet)
    if record is None:
        return 0
    print(f"The fleet spent ${record.spent_usd:.2f}.")
    return 1 if record.lead is not None and record.lead.state == "failed" else 0


class Console:
    """A fleet as lines in a terminal; what the person types goes to the lead, or answers
    the oldest open approval with y or n."""

    def __init__(self, service: Service, fleet: str) -> None:
        self.service, self.fleet = service, fleet
        self.approvals: deque[Approval] = deque()

    def show(self, event: object) -> None:
        record = self.service.fleet(self.fleet)
        units = record.units if record is not None else {}
        lead = record.lead if record is not None else None

        def tag(unit: str) -> str:
            return _tag(units[unit]) if unit in units else unit

        def called(unit: str) -> str:
            return units[unit].called if unit in units else unit

        match event:
            case Changed(unit=unit, state=state):
                print(f"[{tag(unit)}] {state}")
            case Said(unit=unit, text=text):
                whole = lead is not None and unit == lead.id
                print(f"[{tag(unit)}] {text if whole else _clip(text)}")
            case Delivered(unit=unit, to=to, kind=kind, text=text):
                target = called(to) if to is not None else "you"
                print(f"[{tag(unit)} → {target}] {kind}: {text}")
            case Instructed(unit=unit, by=by, kind=kind, text=text) if by is not None:
                print(f"[{tag(by)} → {called(unit)}] {kind}: {_clip(text)}")
            case Approval(unit=unit, tool=tool, arguments=arguments):
                self.approvals.append(event)
                print(f"[{tag(unit)}] wants to run {tool} {_clip(arguments)}. Allow? (y/n)")
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
        try:
            if self.approvals and text.lower() in ("y", "yes", "n", "no"):
                approval = self.approvals.popleft()
                await self.service.approve(self.fleet, approval.unit, approval.request_id,
                                           text.lower().startswith("y"))
            else:
                await self.service.say(self.fleet, text)
        except (ValueError, UnitError, ServiceError, OSError) as e:
            print(f"! {e}")


def _tag(unit: Unit) -> str:
    return f"{unit.name} K{unit.rank} {unit.id}" if unit.name else unit.called


def _clip(text: str, chars: int = LINE_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= chars else text[:chars] + "…"


if __name__ == "__main__":
    sys.exit(main())
