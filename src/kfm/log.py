"""The fleet's record of what happened: every fleet event, as a line of JSON in
`log.jsonl` in the fleet's folder. A frontend that opens a fleet later shows its history
from it."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from kfm import events

FILE = "log.jsonl"
_KINDS = {kind.__name__: kind for kind in (events.Changed, events.Said, events.Delivered,
                                           events.Instructed,
                                           events.Approval, events.Answered, events.Told,
                                           events.Notice)}


def encode(event: events.FleetEvent, at: float) -> dict[str, Any]:
    """The event as plain data: its kind, the time, and its fields."""
    return {"t": round(at, 3), "type": type(event).__name__, **asdict(event)}


def decode(data: dict[str, Any]) -> tuple[float, events.FleetEvent]:
    """What `encode` made. ValueError, KeyError or TypeError for what is not an event."""
    data = dict(data)
    kind = _KINDS[data.pop("type")]
    at = data.pop("t")
    if "files" in data:
        data["files"] = tuple(data["files"])
    return at, kind(**data)


def line(event: events.FleetEvent, at: float) -> str:
    """The event as its line in the record."""
    return json.dumps(encode(event, at), ensure_ascii=False)


def count(folder: Path) -> int:
    """How many events the fleet's record holds: an event's number is its line's.
    Blocking."""
    try:
        with (folder / FILE).open("rb") as record:
            return sum(1 for _ in record)
    except FileNotFoundError:
        return 0


def read(folder: Path, upto: int | None = None) -> list[tuple[float, events.FleetEvent]]:
    """The fleet's events with their times, oldest first, as far as number `upto`; none
    when it has no record. Lines that cannot be read (cut off by a crash) are left out.
    Blocking."""
    found: list[tuple[float, events.FleetEvent]] = []
    with contextlib.suppress(FileNotFoundError):
        for text in (folder / FILE).read_text().splitlines()[:upto]:
            try:
                found.append(decode(json.loads(text)))
            except (ValueError, KeyError, TypeError):
                continue
    return found


class Log:
    """Appends events to the record, in order, off the event loop."""

    def __init__(self, folder: Path) -> None:
        self._path = folder / FILE
        # A line to write, an event to set once everything before it is written, or
        # None: the close.
        self._lines: asyncio.Queue[str | asyncio.Event | None] = asyncio.Queue()
        self._writer: asyncio.Task[None] | None = None

    def add(self, event: events.FleetEvent) -> None:
        self._lines.put_nowait(line(event, time.time()))
        if self._writer is None:
            self._writer = asyncio.create_task(self._write())

    async def flush(self) -> None:
        """Return once every event added so far is in the record."""
        if self._writer is not None:
            written = asyncio.Event()
            self._lines.put_nowait(written)
            await written.wait()

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
            if lines := [each for each in batch if isinstance(each, str)]:
                await asyncio.to_thread(self._append, lines)
            for each in batch:
                if isinstance(each, asyncio.Event):
                    each.set()

    def _append(self, batch: list[str]) -> None:
        with contextlib.suppress(OSError), self._path.open("a") as record:
            record.write("".join(f"{text}\n" for text in batch))
