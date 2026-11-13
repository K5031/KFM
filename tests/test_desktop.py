"""The fleet window, offscreen, on the second harness (tests/fakeharness.py)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import kayden.frontends.desktop  # noqa: F401 - before any Qt import
import pytest
from kayden.frontends.desktop import theme
from kayden.frontends.desktop.window import PREVIEW_ROLE
from PySide6.QtWidgets import QApplication

from kfm.config import FleetConfig
from kfm.desktop import ApprovalCard, DeliveryCard, FleetWindow, YouEntry
from tests.fakeharness import FakeHarness

TEAM = {
    "Lead": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Tidy the docs"})]},
             {"when": "[Question from Scout",
              "do": [("tool", "message", {"agent": "u2", "text": "Use docs/"})]},
             {"when": "[Report from Scout",
              "do": [("say", "All good."), ("report", "Docs are **tidy**.")]}],
    "Scout": [{"do": [("ask", "Which folder?")]},
              {"when": "Use docs/", "do": [("report", "Tidied docs/")]}]}


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    theme.apply(app, dark=False)
    return app


async def until(predicate: Callable[[], Any], timeout: float = 10.0) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


@pytest.fixture
async def window(tmp_path: Path, qapp: QApplication):
    made: list[FleetWindow] = []

    def make(scripts: dict[str, list[dict[str, Any]]]) -> tuple[FleetWindow, FakeHarness]:
        harness = FakeHarness(scripts)
        (tmp_path / "work").mkdir(exist_ok=True)
        shown = FleetWindow(tmp_path / "home", harness=harness, config=FleetConfig())
        shown.resize(1100, 720)
        shown.show()
        made.append(shown)
        return shown, harness

    yield make
    for shown in made:
        await shown.shut()


def start(window: FleetWindow, tmp_path: Path, task: str, *, duty: bool = False) -> None:
    form = window.start_form
    form.task.setPlainText(task)
    form.duty.setChecked(duty)
    form.set_folder(tmp_path / "work")
    form.budget.setValue(3.0)
    form.start.click()


def agents(window: FleetWindow) -> list[tuple[int, str, str]]:
    """(depth, name and rank, state) of each row of the agents tree."""
    rows = []

    def walk(item: Any, depth: int) -> None:
        rows.append((depth, item.text(0), item.text(1)))
        for i in range(item.childCount()):
            walk(item.child(i), depth + 1)

    for i in range(window.agents.topLevelItemCount()):
        walk(window.agents.topLevelItem(i), 0)
    return rows


def cards(window: FleetWindow) -> list[tuple[str, str]]:
    return [(c.header.text(), c.body.toPlainText()) for c in window.entries
            if isinstance(c, DeliveryCard) and c.isVisibleTo(window)]


async def test_a_fleet_is_started_and_followed_in_the_window(window, tmp_path: Path):
    shown, _ = window(TEAM)
    form = shown.start_form
    assert form.isVisibleTo(shown) and not shown.composer.isVisibleTo(shown)
    assert not shown.title.isVisibleTo(shown)  # nothing to name yet
    assert not shown.agents_section.isVisibleTo(shown)
    assert "Ends" in form.hint.text() and not form.duty.isChecked()  # a task, by default
    form.duty.setChecked(True)
    assert "until you stop" in form.hint.text() and not form.task_kind.isChecked()
    form.task_kind.setChecked(True)
    start(shown, tmp_path, "Tidy up")
    await until(lambda: shown.manager is not None and shown.manager.finished.is_set())
    await until(lambda: len(cards(shown)) == 3)
    assert not shown.start_form.isVisibleTo(shown) and shown.agents_section.isVisibleTo(shown)
    assert agents(shown) == [(0, "Lead  K5", "done"), (1, "Scout  K4", "done")]
    assert cards(shown) == [("Scout → Lead · question", "Which folder?"),
                            ("Scout → Lead · report", "Tidied docs/"),
                            ("Lead → you · report", "Docs are tidy.")]
    assert "Tidy up" in shown.title.text()
    await until(lambda: "finished" in shown.status.text())
    assert "of $3.00" in shown.status.text()
    assert shown.manager.fleet.work == str(tmp_path / "work")


async def test_the_person_answers_approves_stops_and_resumes(window, tmp_path: Path):
    shown, harness = window({"Lead": [
        {"do": [("approval", "r1", "write_file", "{'path': '/etc/motd'}"), ("say", "Wrote it.")]},
        {"when": "hello", "do": [("say", "Hi there.")]},
        {"when": "started again", "do": [("say", "Back on watch.")]}]})
    start(shown, tmp_path, "Keep watch", duty=True)
    await until(lambda: any(isinstance(e, ApprovalCard) for e in shown.entries))
    [card] = [e for e in shown.entries if isinstance(e, ApprovalCard)]
    assert "write_file" in card.question.text() and "/etc/motd" in card.question.text()
    card.allow.click()
    await until(lambda: harness.approved == [("r1", True)])
    await until(lambda: not card.allow.isVisibleTo(shown) and "Allowed" in card.question.text())

    shown.input.setPlainText("hello")
    shown.send_button.click()
    await until(lambda: any("hello" in heard for heard in harness.heard["Lead"]))
    assert shown.input.toPlainText() == ""
    await until(lambda: [e.text.text() for e in shown.entries if isinstance(e, YouEntry)]
                == ["hello"])

    shown.stop_button.click()
    await until(lambda: "stopped" in shown.status.text())
    assert shown.resume_button.isVisibleTo(shown) and not shown.input.isEnabled()
    shown.resume_button.click()
    await until(lambda: ("Lead", "session-u1") in harness.opened)
    await until(lambda: "running" in shown.status.text() and shown.input.isEnabled())


async def test_a_stopped_fleet_is_opened_with_its_history(window, tmp_path: Path):
    first, _ = window(TEAM)
    start(first, tmp_path, "Tidy up")
    await until(lambda: first.manager is not None and first.manager.finished.is_set())
    await until(lambda: len(cards(first)) == 3)
    folder = first.manager.folder
    await first.shut()

    again, _ = window({})
    await until(lambda: again.fleets.count() == 1)  # the sidebar lists earlier fleets
    row = again.fleets.item(0)
    assert row.text() == "Tidy up" and "2 agents" in row.data(PREVIEW_ROLE)
    assert [f.folder for f in again.earlier_fleets()] == [folder]
    again.fleets.itemClicked.emit(row)
    await until(lambda: again.fleet is not None)
    assert [header for header, _ in cards(again)] == [
        "Scout → Lead · question", "Scout → Lead · report", "Lead → you · report"]
    assert agents(again) == [(0, "Lead  K5", "done"), (1, "Scout  K4", "done")]
    assert "stopped" in again.status.text()
    assert not again.resume_button.isVisibleTo(again)  # nothing was at work


async def test_choosing_an_agent_shows_what_concerns_it(window, tmp_path: Path):
    shown, _ = window(TEAM)
    start(shown, tmp_path, "Tidy up")
    await until(lambda: len(cards(shown)) == 3)
    lead = shown.agents.topLevelItem(0)
    shown.agents.setCurrentItem(lead.child(0))  # Scout
    assert [header for header, _ in cards(shown)] == ["Scout → Lead · question",
                                                      "Scout → Lead · report"]
    assert shown.show_all.isVisibleTo(shown)
    shown.show_all.click()
    assert len(cards(shown)) == 3 and not shown.show_all.isVisibleTo(shown)


async def test_the_window_adds_itself_to_the_app_menu(window, tmp_path: Path, monkeypatch):
    from kfm import app_menu

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setattr(app_menu, "_reload", lambda: None)
    shown, _ = window({})
    await until(lambda: shown.app_menu_button.isVisibleTo(shown))
    shown.app_menu_button.click()
    await until(lambda: not shown.app_menu_button.isVisibleTo(shown))
    entry = (tmp_path / "share/applications/io.github.k5031.kfm.desktop").read_text()
    assert "Name=KFM" in entry and "-m kfm.desktop" in entry and "Terminal=false" in entry
    [icon] = (tmp_path / "share/icons/hicolor/512x512/apps").glob("io.github.k5031.kfm-*.png")
    assert f"Icon={icon.stem}" in entry
    again, _ = window({})
    await asyncio.sleep(0.2)
    assert not again.app_menu_button.isVisibleTo(again)  # it is there already


async def test_the_window_has_no_title_bar_and_its_own_controls(window, qapp: QApplication):
    from PySide6.QtCore import Qt

    shown, _ = window({})
    assert shown.windowFlags() & Qt.WindowType.FramelessWindowHint
    assert [b.toolTip() for b in shown.window_buttons.values()] == [
        "Minimize", "Maximize", "Close"]
    assert shown.bar.isAncestorOf(shown.title)  # the bar is where the window is grabbed
    shown.window_buttons["maximize"].click()
    qapp.processEvents()
    assert shown.isMaximized() and shown.window_buttons["maximize"].toolTip() == "Restore"
    shown.window_buttons["maximize"].click()
    qapp.processEvents()
    assert not shown.isMaximized()
    shown.window_buttons["close"].click()
    await asyncio.wait_for(shown.closed.wait(), 5)


async def test_the_folder_is_chosen_for_each_fleet(window, tmp_path: Path, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "me")
    (tmp_path / "me").mkdir()
    shown, harness = window({"Lead": [{"do": [("report", "Done.")]}]})
    form = shown.start_form
    assert form.folder is None and form.folder_button.text().strip() == "Choose a folder"
    form.task.setPlainText("Tidy up")
    QTest.keyClick(form.task, Qt.Key.Key_Return)  # Enter starts, but not without a folder
    await asyncio.sleep(0.1)
    assert shown.fleet is None and form.warning.text() == "Choose a folder first."
    form.set_folder(tmp_path / "me")
    QTest.keyClick(form.task, Qt.Key.Key_Return)
    await asyncio.sleep(0.1)
    assert shown.fleet is None and "home folder" in form.warning.text()
    form.set_folder(tmp_path / "gone")
    QTest.keyClick(form.task, Qt.Key.Key_Return)
    await asyncio.sleep(0.1)
    assert shown.fleet is None and "no folder" in form.warning.text()
    assert not (tmp_path / "gone").exists()  # nothing is made that nobody asked for
    form.set_folder(tmp_path / "work")
    assert not form.warning.isVisibleTo(shown) and form.folder_button.text().strip() == "work"
    QTest.keyClick(form.task, Qt.Key.Key_Return)
    await until(lambda: shown.manager is not None and shown.manager.finished.is_set())
    assert harness.heard["Lead"]
    shown.new_button.click()
    await until(lambda: shown.fleet is None)
    assert form.folder is None  # the next fleet's folder is chosen again


async def test_a_running_fleet_keeps_the_others_closed(window, tmp_path: Path):
    first, _ = window({"Lead": [{"do": [("report", "Done.")]}]})
    start(first, tmp_path, "One")
    await until(lambda: first.manager is not None and first.manager.finished.is_set())
    await first.shut()
    shown, _ = window({"Lead": [{"do": [("say", "On watch.")]}]})
    await until(lambda: shown.fleets.count() == 1)
    start(shown, tmp_path, "Two", duty=True)
    await until(lambda: shown.fleets.count() == 2)
    assert not shown.fleets.isEnabled() and shown.fleets_hint.isVisibleTo(shown)
    assert shown.fleets.currentItem().text() == "Two"  # the open one is marked
    shown.stop_button.click()
    await until(lambda: shown.fleets.isEnabled())
