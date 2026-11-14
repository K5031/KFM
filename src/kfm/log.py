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


def encode(event: events.FleetEvent, at: float) -> dict[str, Any]:
    """The event as plain data: its kind, the time, and its fields, units by their ids."""
    data: dict[str, Any] = {"t": round(at, 3), "type": type(event).__name__}
    for field in fields(event):
        value = getattr(event, field.name)
        data[field.name] = value.id if isinstance(value, Unit) else value
    return data


def decode(data: dict[str, Any], fleet: Fleet) -> tuple[float, events.FleetEvent]:
    """What `encode` made, with the units of `fleet`. ValueError, KeyError or TypeError
    for what is not an event of this fleet (a unit it no longer has, a kind unknown)."""
    data = dict(data)
    kind = _KINDS[data.pop("type")]
    at = data.pop("t")
    for name in ("unit", "to", "by"):
        if data.get(name) is not None:
            data[name] = fleet.units[data[name]]
    if "files" in data:
        data["files"] = tuple(data["files"])
    return at, kind(**data)


def line(event: events.FleetEvent, at: float) -> str:
    """The event as its line in the record."""
    return json.dumps(encode(event, at), ensure_ascii=False)


def read(folder: Path, fleet: Fleet) -> list[tuple[float, events.FleetEvent]]:
    """The fleet's events with their times, oldest first; none when it has no record.
    Lines that cannot be read (cut off by a crash) are left out. Blocking."""
    found: list[tuple[float, events.FleetEvent]] = []
    with contextlib.suppress(FileNotFoundError):
        for text in (folder / FILE).read_text().splitlines():
            try:
                found.append(decode(json.loads(text), fleet))
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
        closed = False
        while not closed:
            batch = [await self._lines.get()]
            while not self._lines.empty():
                batch.append(self._lines.get_nowait())
            if None in batch:  # the close: what came before it is still written
                closed, batch = True, batch[: batch.index(None)]
            if batch:
                await asyncio.to_thread(self._append, [line for line in batch if line])

    def _append(self, batch: list[str]) -> None:
        with contextlib.suppress(OSError), self._path.open("a") as record:
            record.write("".join(f"{text}\n" for text in batch))
