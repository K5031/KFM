"""The service that `kfm serve` runs, as a frontend holds it: `kfm.service` over the
socket (kfm.server). It keeps the record of every fleet it watches as the service last
sent it, so that a frontend reads a `Fleet` and gets the manager's own events as it would
in the service's process. Letting go of it leaves the fleets running."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import os
import signal
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from kfm import config as settings
from kfm import log
from kfm.api import UnitError
from kfm.config import HOME, FleetConfig
from kfm.draft import Draft, Team
from kfm.events import Approval, FleetEvent
from kfm.fleet import Fleet, FleetError
from kfm.project import SECRETARY
from kfm.server import LINE_LIMIT, SOCKET
from kfm.service import (
    VERSION,
    FleetState,
    Happened,
    Listed,
    Live,
    ProjectInfo,
    Proposed,
    Service,
    Update,
    Updated,
    Within,
)

START_S = 15.0  # for a service that was started to answer
ANSWER_S = 5.0  # for one of another version to say it ends
END_S = 30.0  # for one that was asked to end to be gone: it stops its fleets first
_ERRORS: dict[str, type[Exception]] = {
    "FleetError": FleetError, "UnitError": UnitError,
    "ValueError": ValueError, "KeyError": ValueError, "TypeError": ValueError}


def installed_at() -> float:
    """When the code this program runs was last changed: the newest of KFM's own files and
    of Kadence's, which its agents run. Blocking."""
    import kadence

    import kfm

    return max((path.stat().st_mtime
                for package in (kfm, kadence) if package.__file__ is not None
                for path in Path(package.__file__).parent.rglob("*.py")), default=0.0)


class ServiceError(RuntimeError):
    """The service failed at something, or cannot be reached; the text is for the person."""


class RemoteService(Service):
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *,
                 path: Path | None = None, pid: int = 0, since: float = 0.0) -> None:
        """`path`: its socket. `pid` and `since`: the service's process, and when it
        started, which is the code it runs."""
        self._reader, self._writer = reader, writer
        self._path, self.pid, self.since = path, pid, since
        self.stale = False
        self.interrupted = []
        self._ids = itertools.count(1)
        self._waiting: dict[int, asyncio.Future[Any]] = {}
        self._fleets: dict[str, Fleet] = {}
        self._states: dict[str, FleetState] = {}
        self._listeners: list[Callable[[Update], None]] = []
        self._reading = asyncio.ensure_future(self._read())

    @classmethod
    async def connect(cls, home: Path = HOME, *, socket: Path | None = None,
                      start: bool = True) -> RemoteService:
        """The service of `home`, started if none runs (`start`). One that speaks another
        version of the contract, left running by the program from before an update, is
        ended first: its fleets stop, and `interrupted` names those that ran. ServiceError
        when none can be reached."""
        path = socket or home / SOCKET
        interrupted: list[str] = []
        for replacing in (True, False):
            try:
                reader, writer = await asyncio.open_unix_connection(str(path),
                                                                    limit=LINE_LIMIT)
            except (OSError, ValueError):
                if not start:
                    raise ServiceError("No KFM service runs.") from None
                reader, writer = await _started(home, path)
            try:
                hello = json.loads(await asyncio.wait_for(reader.readline(), START_S))
            except (TimeoutError, ValueError) as e:
                raise ServiceError(f"The KFM service did not answer: {e}") from None
            if hello.get("kfm") == VERSION:
                break
            if not replacing:
                writer.close()
                raise ServiceError(
                    f"The KFM service speaks version {hello.get('kfm')} of its contract and "
                    f"this program version {VERSION}: they are not of one installation.")
            interrupted = await asyncio.to_thread(_ran_under, home, hello.get("pid"))
            await _ended(reader, writer, path, hello.get("pid"))
        service = cls(reader, writer, path=path, pid=hello.get("pid", 0),
                      since=hello.get("since", 0.0))
        service.stale = service.since < await asyncio.to_thread(installed_at)
        service.interrupted = interrupted
        return service

    # ── Projects ─────────────────────────────────────────────────────────

    async def projects(self) -> list[ProjectInfo]:
        return [ProjectInfo(**each) for each in await self._request("projects")]

    async def create_project(self, name: str, work: str,
                             description: str = "") -> ProjectInfo:
        return ProjectInfo(**await self._request("create_project", name, work, description))

    async def rename_project(self, project: str, name: str) -> None:
        await self._request("rename_project", project, name)

    async def delete_project(self, project: str) -> None:
        await self._request("delete_project", project)
        for fleet_id in [each for each in self._fleets if each.startswith(f"{project}/")]:
            self._drop(fleet_id)

    async def models(self) -> list[tuple[str, str, str]]:
        return [(each[0], each[1], each[2]) for each in await self._request("models")]

    async def proposal(self, project: str) -> Draft | None:
        made = await self._request("proposal", project)
        return made and Draft(tuple(Team(**team) for team in made["teams"]),
                              made["spent_usd"])

    async def settle_proposal(self, project: str) -> None:
        await self._request("settle_proposal", project)

    # ── Fleets ───────────────────────────────────────────────────────────

    async def fleets(self, project: str) -> list[str]:
        found = await self._request("fleets", project)
        for fleet_id in found:
            await self.watch(fleet_id)
        return found

    async def add_fleet(self, project: str, name: str, task: str, *, ongoing: bool = False,
                        work: str | None = None, budget_usd: float | None = None,
                        model: str | None = None) -> str:
        fleet_id = await self._request("add_fleet", project, name, task, ongoing=ongoing,
                                       work=work, budget_usd=budget_usd, model=model)
        await self.watch(fleet_id)
        return fleet_id

    async def watch(self, fleet: str) -> None:
        if fleet not in self._fleets:
            self._keep(await self._request("watch", fleet))

    def fleet(self, fleet: str) -> Fleet | None:
        return self._fleets.get(fleet)

    def state(self, fleet: str) -> FleetState:
        return self._states.get(fleet, FleetState())

    async def history(self, fleet: str) -> tuple[list[FleetEvent], int]:
        kept = await self._request("history", fleet)
        return [log.decode(data)[1] for data in kept["events"]], kept["upto"]

    async def start(self, fleet: str, *, budget_usd: float | None = None) -> None:
        await self._request("start", fleet, budget_usd=budget_usd)

    async def stop(self, fleet: str) -> None:
        await self._request("stop", fleet)

    async def rename_fleet(self, fleet: str, name: str) -> None:
        await self._request("rename_fleet", fleet, name)

    async def delete_fleet(self, fleet: str) -> None:
        await self._request("delete_fleet", fleet)
        self._drop(fleet)

    # ── Agents ───────────────────────────────────────────────────────────

    async def add_agent(self, fleet: str, supervisor: str | None, name: str, task: str, *,
                        ongoing: bool = False, model: str | None = None) -> str:
        return await self._request("add_agent", fleet, supervisor, name, task,
                                   ongoing=ongoing, model=model)

    async def remove_agent(self, fleet: str, unit: str) -> None:
        await self._request("remove_agent", fleet, unit)

    async def rename_agent(self, fleet: str, unit: str, name: str) -> None:
        await self._request("rename_agent", fleet, unit, name)

    async def set_task(self, fleet: str, unit: str, task: str) -> None:
        await self._request("set_task", fleet, unit, task)

    async def set_model(self, fleet: str, unit: str, model: str) -> None:
        await self._request("set_model", fleet, unit, model)

    async def say(self, fleet: str, text: str, unit: str | None = None, *,
                  files: Sequence[str] = (), spoken: bool = False) -> None:
        await self._request("say", fleet, text, unit, files=list(files), spoken=spoken)

    async def approve(self, fleet: str, unit: str, request_id: str, allow: bool, *,
                      always: bool = False) -> None:
        await self._request("approve", fleet, unit, request_id, allow, always=always)

    # ── An agent's own conversation ──────────────────────────────────────

    async def follow(self, fleet: str, unit: str) -> bool:
        return await self._request("follow", fleet, unit)

    async def unfollow(self, fleet: str, unit: str) -> None:
        await self._request("unfollow", fleet, unit)

    async def ask(self, fleet: str, unit: str, method: str, params: dict[str, Any]) -> Any:
        return await self._request("ask", fleet, unit, method, params)

    # ── Settings, and what happens ───────────────────────────────────────

    async def settings(self) -> FleetConfig:
        return settings.from_record(await self._request("settings"))

    async def save_settings(self, **changes: Any) -> FleetConfig:
        return settings.from_record(await self._request("save_settings", **changes))

    def subscribe(self, listener: Callable[[Update], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    async def end(self) -> None:
        try:
            await self._request("end")
        except ServiceError:
            pass  # it went before it answered
        except ValueError:  # one from before it could be asked: its process is ended
            if self.pid:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(self.pid, signal.SIGTERM)
        try:  # it lets go of those attached once its fleets are stopped
            await asyncio.wait_for(asyncio.shield(self._reading), END_S)
        except TimeoutError:
            raise ServiceError(
                f"The KFM service did not end within {END_S:.0f} seconds.") from None
        finally:
            await self.aclose()

    async def aclose(self) -> None:
        self._reading.cancel()
        self._writer.close()
        await asyncio.gather(self._reading, return_exceptions=True)

    # ── The socket ───────────────────────────────────────────────────────

    async def _request(self, method: str, *args: Any, **kwargs: Any) -> Any:
        if self._writer.is_closing():
            raise ServiceError("The KFM service is gone.")
        request_id = next(self._ids)
        answer = self._waiting[request_id] = asyncio.get_running_loop().create_future()
        self._writer.write(json.dumps(
            {"id": request_id, "method": method, "args": args, "kwargs": kwargs},
            ensure_ascii=False).encode() + b"\n")
        try:
            return await answer
        finally:
            self._waiting.pop(request_id, None)

    async def _read(self) -> None:
        try:
            while line := await self._reader.readline():
                message = json.loads(line)
                if "update" in message:
                    self._updated(message)
                elif (answer := self._waiting.get(message.get("id"))) is not None:
                    if answer.done():
                        continue
                    if (error := message.get("error")) is not None:
                        kind = _ERRORS.get(error["type"], ServiceError)
                        answer.set_exception(kind(error["message"]))
                    else:
                        answer.set_result(message.get("result"))
        except (ConnectionError, ValueError):
            pass
        finally:
            self._writer.close()
            for answer in self._waiting.values():
                if not answer.done():
                    answer.set_exception(ServiceError("The KFM service is gone."))

    def _updated(self, message: dict[str, Any]) -> None:
        fleet_id = message.get("fleet", "")
        match message["update"]:
            case "listed":
                self._tell(Listed())
            case "proposed":
                self._tell(Proposed(message["project"]))
            case "within":  # of an agent someone here follows
                self._tell(Within(fleet_id, message["unit"], message["event"]))
            case "live":  # of any fleet: it is asked for nowhere, and a page comes later
                self._tell(Live(fleet_id, message["unit"], message["thinking"],
                                message["writing"], message["doing"]))
            case "updated" if fleet_id in self._fleets:
                if message["record"] is None:
                    self._drop(fleet_id)
                else:
                    self._keep(message)
                self._tell(Updated(fleet_id))
            case "happened" if fleet_id in self._fleets:
                self._tell(Happened(fleet_id, log.decode(message["event"])[1],
                                    message["number"]))

    def _keep(self, message: dict[str, Any]) -> None:
        fleet_id = message["fleet"]
        if message["record"] is None:
            raise ValueError(f"There is no fleet {fleet_id}.")
        self._fleets[fleet_id] = Fleet.of(message["record"])
        state = message["state"]
        self._states[fleet_id] = FleetState(**{**state, "approvals": tuple(
            Approval(**asked) for asked in state.get("approvals", ()))})

    def _drop(self, fleet_id: str) -> None:
        self._fleets.pop(fleet_id, None)
        self._states.pop(fleet_id, None)

    def _tell(self, update: Update) -> None:
        for listener in list(self._listeners):
            listener(update)


def _ran_under(home: Path, pid: int | None) -> list[str]:
    """The ids of the fleets whose records say that process runs them. Blocking."""
    found = []
    for record in sorted((home / "projects").glob("*/fleets/*/fleet.json")):
        try:
            runs = json.loads(record.read_text()).get("manager_pid")
        except (OSError, ValueError, AttributeError):
            continue
        if pid is not None and runs == pid and record.parent.name != SECRETARY:
            found.append(f"{record.parent.parent.parent.name}/{record.parent.name}")
    return found


async def _ended(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, path: Path,
                 pid: int | None) -> None:
    """End a service this program cannot talk to: asked to, as a frontend asks (that much
    every version understands), and its process ended if it does not go. ServiceError
    when it is still there."""
    writer.write(json.dumps({"id": 0, "method": "end", "args": [], "kwargs": {}}).encode()
                 + b"\n")
    asked, line = False, b"-"
    try:
        async with asyncio.timeout(ANSWER_S):
            while not asked and (line := await reader.readline()):
                with contextlib.suppress(ValueError, AttributeError):
                    answer = json.loads(line)
                    asked = answer.get("id") == 0 and "error" not in answer
            asked = asked or not line  # it closed: gone already
    except (TimeoutError, ConnectionError, OSError):
        pass
    if not asked and pid:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGTERM)
    try:
        async with asyncio.timeout(END_S):  # it stops its fleets first
            while await reader.read(65536):
                pass
    except (TimeoutError, ConnectionError, OSError):
        pass
    writer.close()
    waited = 0.0
    while _alive(pid) if pid else path.exists():
        if waited >= END_S:
            raise ServiceError(
                "The KFM service from before the update did not end"
                + (f": end it with kill {pid}." if pid else "."))
        await asyncio.sleep(0.1)
        waited += 0.1


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _started(home: Path, path: Path) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Start a service for `home`, on its own, and attach to it."""
    home.mkdir(parents=True, exist_ok=True)
    with (home / "service.log").open("ab") as out:
        subprocess.Popen(  # noqa: ASYNC220 - it outlives this process; nothing is awaited
            [sys.executable, "-m", "kfm", "--home", str(home), "serve", "--socket", str(path)],
            stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)
    waited = 0.0
    while True:
        try:
            return await asyncio.open_unix_connection(str(path), limit=LINE_LIMIT)
        except (OSError, ValueError):
            if waited >= START_S:
                raise ServiceError(
                    f"The KFM service did not start: see {home / 'service.log'}.") from None
            await asyncio.sleep(0.1)
            waited += 0.1
