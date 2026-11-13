"""The fleet's record of what happened: every fleet event, as a line of JSON in
`log.jsonl` in the fleet's folder. A frontend that opens a fleet later shows its history
from it."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import fields
from pathlib import Path
from typing import Any

from kfm import events
from kfm.fleet import Fleet, Unit

FILE = "log.jsonl"
_KINDS = {kind.__name__: kind for kind in (events.Changed, events.Said, events.Delivered,
                                           events.Instructed,
                                           events.Approval, events.Answered, events.Told,
                                           events.Notice)}


def line(event: events.FleetEvent, at: float) -> str:
    """The event as its record: its kind, the time, and its fields, units by their ids."""
    data: dict[str, Any] = {"t": round(at, 3), "type": type(event).__name__}
    for field in fields(event):
        value = getattr(event, field.name)
        data[field.name] = value.id if isinstance(value, Unit) else value
    return json.dumps(data, ensure_ascii=False)


def read(folder: Path, fleet: Fleet) -> list[tuple[float, events.FleetEvent]]:
    """The fleet's events with their times, oldest first; none when it has no record.
    Lines that cannot be read (cut off by a crash) are left out. Blocking."""
    found: list[tuple[float, events.FleetEvent]] = []
    with contextlib.suppress(FileNotFoundError):
        for text in (folder / FILE).read_text().splitlines():
            try:
                data = json.loads(text)
                kind = _KINDS[data.pop("type")]
                at = data.pop("t")
                for name in ("unit", "to", "by"):
                    if data.get(name) is not None:
                        data[name] = fleet.units[data[name]]
                if "files" in data:
                    data["files"] = tuple(data["files"])
                found.append((at, kind(**data)))
            except (ValueError, KeyError, TypeError):
                continue
    return found


class Log:
    """Appends events to the record, in order, off the event loop."""

    def __init__(self, folder: Path) -> None:
        self._path = folder / FILE
        self._lines: asyncio.Queue[str | None] = asyncio.Queue()
        self._writer: asyncio.Task[None] | None = None

    def add(self, event: events.FleetEvent) -> None:
        self._lines.put_nowait(line(event, time.time()))
        if self._writer is None:
            self._writer = asyncio.create_task(self._write())

    async def aclose(self) -> None:
        if self._writer is not None:
            self._lines.put_nowait(None)
            await self._writer
            self._writer = None

    async def _write(self) -> None:
        while (text := await self._lines.get()) is not None:
            batch = [text]
            while not self._lines.empty() and (more := self._lines.get_nowait()) is not None:
                batch.append(more)
            await asyncio.to_thread(self._append, batch)

    def _append(self, batch: list[str]) -> None:
        with contextlib.suppress(OSError), self._path.open("a") as record:
            record.write("".join(f"{text}\n" for text in batch))
