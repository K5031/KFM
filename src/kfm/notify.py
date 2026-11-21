"""Desktop notifications, through libnotify's `notify-send`: the desktop's own way to say
something while the person looks elsewhere. Nothing where it is not installed."""

from __future__ import annotations

import asyncio
import contextlib
import shutil
from collections.abc import Callable

APP = "KFM"
OPEN = "default"  # the action a click on the notification itself is


class Notifier:
    def __init__(self, program: str | None = shutil.which("notify-send")) -> None:
        self._program = program
        self._shown: set[asyncio.Task[None]] = set()

    def send(self, title: str, body: str, clicked: Callable[[], None]) -> None:
        """Show a notification; `clicked` when the person clicks it."""
        if self._program is None:
            return
        task = asyncio.ensure_future(self._show(title, body, clicked))
        self._shown.add(task)
        task.add_done_callback(self._shown.discard)

    async def _show(self, title: str, body: str, clicked: Callable[[], None]) -> None:
        """Runs until the notification is gone: notify-send waits to say what was chosen."""
        assert self._program is not None
        try:
            process = await asyncio.create_subprocess_exec(
                self._program, f"--app-name={APP}", f"--action={OPEN}=Open", "--", title, body,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        except OSError:
            return
        try:
            chosen, _ = await process.communicate()
        finally:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        if chosen.decode(errors="replace").strip() == OPEN:
            clicked()

    async def aclose(self, *, wait: bool = False) -> None:
        """End the notifications still shown; `wait`: let them end by themselves."""
        shown = list(self._shown)
        if not wait:
            for task in shown:
                task.cancel()
        await asyncio.gather(*shown, return_exceptions=True)
