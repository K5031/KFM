"""Desktop notifications: libnotify's notify-send, run without blocking."""

from __future__ import annotations

import asyncio
from pathlib import Path

from kfm.notify import Notifier


def program(tmp_path: Path, script: str) -> str:
    path = tmp_path / "notify-send"
    path.write_text(f"#!/bin/sh\n{script}\n")
    path.chmod(0o755)
    return str(path)


async def test_a_notification_is_sent_and_a_click_on_it_is_heard(tmp_path: Path):
    said = tmp_path / "said"
    notifier = Notifier(program(tmp_path, f'printf "%s\\n" "$@" > {said}\necho default'))
    clicked: list[bool] = []
    notifier.send("dev › Lead", "wants to run Bash", lambda: clicked.append(True))
    async with asyncio.timeout(5):
        while not clicked:
            await asyncio.sleep(0.01)
    assert said.read_text().splitlines()[-2:] == ["dev › Lead", "wants to run Bash"]
    await notifier.aclose()


async def test_one_that_is_closed_without_a_click_is_not_a_click(tmp_path: Path):
    notifier = Notifier(program(tmp_path, "exit 0"))
    clicked: list[bool] = []
    notifier.send("dev", "asks", lambda: clicked.append(True))
    await notifier.aclose(wait=True)
    assert not clicked


async def test_nothing_is_sent_where_the_program_is_not_installed():
    notifier = Notifier(None)
    notifier.send("dev", "asks", lambda: None)  # nothing, and no error
    await notifier.aclose()


async def test_one_still_shown_ends_with_the_notifier(tmp_path: Path):
    notifier = Notifier(program(tmp_path, "sleep 60"))
    notifier.send("dev", "asks", lambda: None)
    await asyncio.sleep(0.05)
    async with asyncio.timeout(5):
        await notifier.aclose()
