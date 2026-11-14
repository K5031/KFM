"""The service that `kfm serve` runs, as a frontend holds it: `kfm.service` over the
socket (kfm.server). It keeps a copy of every fleet it watches, made current by what the
service sends, so that a frontend reads a `Fleet` and gets the manager's own events as it
would in the service's process. Letting go of it leaves the fleets running."""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from kfm import config as settings
from kfm import log
from kfm.api import UnitError
from kfm.config import HOME, FleetConfig
from kfm.draft import Draft, DraftError, Team
from kfm.events import FleetEvent
from kfm.fleet import Fleet, FleetError
from kfm.server import IDLE_S, LINE_LIMIT, SOCKET
from kfm.service import (
    VERSION,
    FleetState,
    Happened,
    Listed,
    ProjectInfo,
    Service,
    Update,
    Updated,
)

START_S = 15.0  # for a service that was started to answer
_ERRORS: dict[str, type[Exception]] = {
    "FleetError": FleetError, "UnitError": UnitError, "DraftError": DraftError,
    "ValueError": ValueError, "KeyError": ValueError, "TypeError": ValueError}


class ServiceError(RuntimeError):
    """The service failed at something, or cannot be reached; the text is for the person."""


class RemoteService(Service):
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader, self._writer = reader, writer
        self._ids = itertools.count(1)
        self._waiting: dict[int, asyncio.Future[Any]] = {}
        self._fleets: dict[str, Fleet] = {}
        self._states: dict[str, FleetState] = {}
        self._listeners: list[Callable[[Update], None]] = []
        self._reading = asyncio.ensure_future(self._read())

    @classmethod
    async def connect(cls, home: Path = HOME, *, socket: Path | None = None,
                      start: bool = True, wait_s: float = IDLE_S + 4.0) -> RemoteService:
        """The service of `home`, started if none runs (`start`). A service of another
        version of the contract is given `wait_s` to end, which one with nothing to do
        does by itself, and the service is then looked for again. ServiceError when it
        cannot be reached, or one of another version stays."""
        path = socket or home / SOCKET
        try:
            reader, writer = await asyncio.open_unix_connection(str(path), limit=LINE_LIMIT)
        except (OSError, ValueError):
            if not start:
                raise ServiceError("No KFM service runs.") from None
            reader, writer = await _started(home, path)
        try:
            hello = json.loads(await asyncio.wait_for(reader.readline(), START_S))
        except (TimeoutError, ValueError) as e:
            raise ServiceError(f"The KFM service did not answer: {e}") from None
        if hello.get("kfm") != VERSION:
            writer.close()
            if await _gone(path, wait_s):
                return await cls.connect(home, socket=socket, start=start, wait_s=0.0)
            end = f"kill {hello['pid']}" if "pid" in hello else "pkill -f 'kfm.*serve'"
            raise ServiceError(
                f"A KFM service of another version is running (it speaks version "
                f"{hello.get('kfm')}, this program version {VERSION}) and is not ending, so "
                f"it still runs fleets or another KFM window is open. Close the other "
                f"windows, or end it with: {end}\nIts fleets stop and can be resumed.")
        return cls(reader, writer)

    # ── Projects ─────────────────────────────────────────────────────────

    async def projects(self) -> list[ProjectInfo]:
        return [ProjectInfo(**each) for each in await self._request("projects")]

    async def create_project(self, name: str, work: str) -> ProjectInfo:
        return ProjectInfo(**await self._request("create_project", name, work))

    async def rename_project(self, project: str, name: str) -> None:
        await self._request("rename_project", project, name)

    async def delete_project(self, project: str) -> None:
        await self._request("delete_project", project)
        for fleet_id in [each for each in self._fleets if each.startswith(f"{project}/")]:
            self._drop(fleet_id)

    async def draft(self, project: str, description: str) -> Draft:
        made = await self._request("draft", project, description)
        return Draft(tuple(Team(**team) for team in made["teams"]), made["spent_usd"])

    async def cancel_draft(self, project: str) -> None:
        await self._request("cancel_draft", project)

    # ── Fleets ───────────────────────────────────────────────────────────

    async def fleets(self, project: str) -> list[str]:
        found = await self._request("fleets", project)
        for fleet_id in found:
            await self.watch(fleet_id)
        return found

    async def add_fleet(self, project: str, name: str, task: str, *, ongoing: bool = True,
                        work: str | None = None, budget_usd: float | None = None) -> str:
        fleet_id = await self._request("add_fleet", project, name, task, ongoing=ongoing,
                                       work=work, budget_usd=budget_usd)
        await self.watch(fleet_id)
        return fleet_id

    async def watch(self, fleet: str) -> None:
        if fleet not in self._fleets:
            self._keep(await self._request("watch", fleet))

    def fleet(self, fleet: str) -> Fleet | None:
        return self._fleets.get(fleet)

    def state(self, fleet: str) -> FleetState:
        return self._states.get(fleet, FleetState())

    async def history(self, fleet: str) -> list[tuple[float, FleetEvent]]:
        record = self._fleets.get(fleet)
        if record is None:
            raise ValueError(f"The fleet {fleet} is not being watched.")
        found = []
        for data in await self._request("history", fleet):
            with contextlib.suppress(ValueError, KeyError, TypeError):  # a unit since removed
                found.append(log.decode(data, record))
        return found

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

    async def approve(self, fleet: str, unit: str, request_id: str, allow: bool) -> None:
        await self._request("approve", fleet, unit, request_id, allow)

    # ── Settings, and what happens ───────────────────────────────────────

    async def settings(self) -> FleetConfig:
        return settings.from_record(await self._request("settings"))

    async def save_settings(self, **changes: Any) -> FleetConfig:
        return settings.from_record(await self._request("save_settings", **changes))

    def subscribe(self, listener: Callable[[Update], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

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
            case "updated" if fleet_id in self._fleets:
                if message["record"] is None:
                    self._drop(fleet_id)
                else:
                    self._keep(message)
                self._tell(Updated(fleet_id))
            case "happened" if fleet_id in self._fleets:
                with contextlib.suppress(ValueError, KeyError, TypeError):
                    self._tell(Happened(fleet_id, log.decode(
                        message["event"], self._fleets[fleet_id])[1]))

    def _keep(self, message: dict[str, Any]) -> None:
        fleet_id = message["fleet"]
        if message["record"] is None:
            raise ValueError(f"There is no fleet {fleet_id}.")
        if (kept := self._fleets.get(fleet_id)) is None:
            self._fleets[fleet_id] = Fleet.of(message["record"])
        else:
            kept.update(message["record"])
        self._states[fleet_id] = FleetState(**message["state"])

    def _drop(self, fleet_id: str) -> None:
        self._fleets.pop(fleet_id, None)
        self._states.pop(fleet_id, None)

    def _tell(self, update: Update) -> None:
        for listener in list(self._listeners):
            listener(update)


async def _gone(path: Path, wait_s: float) -> bool:
    """Whether the service on the socket ends within `wait_s`: it takes its socket with
    it. Looking does not attach to it, which would keep it alive."""
    waited = 0.0
    while path.exists():
        if waited >= wait_s:
            return False
        await asyncio.sleep(0.2)
        waited += 0.2
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
