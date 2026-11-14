"""`kfm serve`: the service of this process (kfm.local) answering `kfm.service` on a Unix
socket, for any number of frontends. One JSON object a line, as Kadence's headless channel
speaks: the first line names the contract's version; a request {"id", "method", "args",
"kwargs"} is answered by {"id", "result"} or {"id", "error": {"type", "message"}}; what
happens arrives as lines with "update" and no id. It ends when no fleet runs and no
frontend is attached. The first line also carries the service's process id, for a
frontend of another version to name."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from kfm import config as settings
from kfm import log
from kfm.api import UnitError
from kfm.draft import DraftError
from kfm.fleet import FleetError
from kfm.local import LocalService
from kfm.service import VERSION, Happened, Listed, Update, Updated

logger = logging.getLogger(__name__)

SOCKET = "service.sock"  # in KFM's home
IDLE_S = 5.0  # with no fleet running and nobody attached, before it ends
SPENDING_S = 1.0  # how often a running fleet's spending is passed on
LINE_LIMIT = 16 * 1024 * 1024  # bytes: a fleet's history is one line
CALLS = frozenset({
    "projects", "create_project", "rename_project", "delete_project", "draft", "cancel_draft",
    "fleets", "add_fleet", "watch", "history", "start", "stop", "rename_fleet", "delete_fleet",
    "add_agent", "remove_agent", "rename_agent", "set_task", "set_model", "say", "approve",
    "settings", "save_settings"})
_REFUSALS = (FleetError, UnitError, DraftError, ValueError, OSError, TypeError)


class Server:
    def __init__(self, service: LocalService, path: Path, *, idle_s: float = IDLE_S) -> None:
        self.service, self.path, self._idle_s = service, path, idle_s
        self._writers: set[asyncio.StreamWriter] = set()
        self._calls: set[asyncio.Task[None]] = set()
        self._spent: dict[str, float] = {}

    async def run(self) -> None:
        """Serve until idle. RuntimeError when a service already answers on the socket."""
        if await alive(self.path):
            raise RuntimeError(f"A KFM service already runs on {self.path}.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.unlink(missing_ok=True)  # left by one that died
        server = await asyncio.start_unix_server(self._attached, path=str(self.path),
                                                 limit=LINE_LIMIT)
        self.path.chmod(0o600)
        stop_listening = self.service.subscribe(self._update)
        try:
            await self._until_idle()
        finally:
            stop_listening()
            server.close()
            for call in list(self._calls):
                call.cancel()
            await asyncio.gather(*self._calls, return_exceptions=True)
            for writer in list(self._writers):
                writer.close()
            await self.service.aclose()
            self.path.unlink(missing_ok=True)

    async def _until_idle(self) -> None:
        idle_since: float | None = None
        while True:
            await asyncio.sleep(min(SPENDING_S, self._idle_s / 2))
            for fleet_id in self.service.watched():
                fleet = self.service.fleet(fleet_id)
                spent = fleet.spent_usd if fleet is not None else 0.0
                if self._spent.get(fleet_id) != spent:
                    self._spent[fleet_id] = spent
                    self._send_all(self._record(fleet_id))
            if self._writers or self.service.running():
                idle_since = None
            elif idle_since is None:
                idle_since = time.monotonic()
            elif time.monotonic() - idle_since >= self._idle_s:
                return

    # ── What happens, to every frontend ──────────────────────────────────

    def _update(self, update: Update) -> None:
        match update:
            case Happened(fleet=fleet_id, event=event):
                self._send_all(self._record(fleet_id))  # the units it speaks of, first
                self._send_all({"update": "happened", "fleet": fleet_id,
                                "event": log.encode(event, time.time())})
            case Updated(fleet=fleet_id):
                self._send_all(self._record(fleet_id))
            case Listed():
                self._send_all({"update": "listed"})

    def _record(self, fleet_id: str) -> dict[str, Any]:
        fleet = self.service.fleet(fleet_id)
        return {"update": "updated", "fleet": fleet_id,
                "record": fleet.record() if fleet is not None else None,
                "state": asdict(self.service.state(fleet_id))}

    def _send_all(self, message: dict[str, Any]) -> None:
        line = json.dumps(message, ensure_ascii=False).encode() + b"\n"
        for writer in list(self._writers):
            if not writer.is_closing():
                writer.write(line)

    # ── One frontend ─────────────────────────────────────────────────────

    async def _attached(self, reader: asyncio.StreamReader,
                        writer: asyncio.StreamWriter) -> None:
        mine: set[asyncio.Task[None]] = set()
        writer.write(json.dumps({"kfm": VERSION, "pid": os.getpid()}).encode() + b"\n")
        self._writers.add(writer)
        try:
            while line := await reader.readline():
                call = asyncio.ensure_future(self._answer(writer, line))
                for calls in (mine, self._calls):
                    calls.add(call)
                    call.add_done_callback(calls.discard)
        except (ConnectionError, ValueError):
            pass
        finally:
            self._writers.discard(writer)
            for call in list(mine):  # what it was waiting for is nobody's now
                call.cancel()
            writer.close()

    async def _answer(self, writer: asyncio.StreamWriter, line: bytes) -> None:
        request_id = None
        try:
            request = json.loads(line)
            request_id = request["id"]
            method = request["method"]
            if method not in CALLS:
                raise ValueError(f"There is no {method!r} in KFM's service.")
            answer: dict[str, Any] = {"id": request_id, "result": await self._call(
                method, request.get("args", []), request.get("kwargs", {}))}
        except _REFUSALS + (KeyError,) as e:
            answer = {"id": request_id,
                      "error": {"type": type(e).__name__, "message": str(e)}}
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("a call failed")
            answer = {"id": request_id,
                      "error": {"type": "Error", "message": f"{type(e).__name__}: {e}"}}
        if not writer.is_closing():
            writer.write(json.dumps(answer, ensure_ascii=False).encode() + b"\n")
            with contextlib.suppress(ConnectionError):
                await writer.drain()

    async def _call(self, method: str, args: list[Any], kwargs: dict[str, Any]) -> Any:
        result = await getattr(self.service, method)(*args, **kwargs)
        match method:
            case "projects":
                return [asdict(each) for each in result]
            case "create_project":
                return asdict(result)
            case "draft":
                return {"teams": [team.model_dump() for team in result.teams],
                        "spent_usd": result.spent_usd}
            case "watch":
                return self._record(args[0])
            case "history":
                return [log.encode(event, 0.0) for event in result]
            case "settings" | "save_settings":
                return settings.record(result)
        return result


async def alive(path: Path) -> bool:
    """Whether a service answers on the socket."""
    try:
        _, writer = await asyncio.open_unix_connection(str(path))
    except (OSError, ValueError):
        return False
    writer.close()
    return True
