"""The window, offscreen, on the second harness (tests/fakeharness.py)."""

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

from kfm import project
from kfm.config import FleetConfig
from kfm.desktop import (
    FLEETS_ROLE,
    ApprovalCard,
    DeliveryCard,
    FleetView,
    FleetWindow,
    YouEntry,
)
from kfm.fleet import Fleet
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


async def start(window: FleetWindow, tmp_path: Path, task: str, *, name: str = "dev",
                duty: bool = False, project: str = "Acme") -> FleetView:
    """Add a fleet through the forms, making the project first when there is none."""
    if window.project is None:
        window.project_form.name.setText(project)
        window.project_form.set_folder(tmp_path / "work")
        window.project_form.create.click()
        await until(lambda: window.chart.isVisibleTo(window))
    if not window.adding:
        window.chart.add_button.click()
    form = window.start_form
    await until(lambda: form.isVisibleTo(window))
    before = set(window.views)
    form.name.setText(name)
    form.task.setPlainText(task)
    (form.duty if duty else form.task_kind).setChecked(True)
    form.budget.setValue(3.0)
    form.start.click()
    await until(lambda: set(window.views) - before and window.view is not None)
    return window.view


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


def cards(view: FleetView) -> list[tuple[str, str]]:
    return [(c.header.text(), c.body.toPlainText()) for c in view.entries
            if isinstance(c, DeliveryCard) and not c.isHidden()]


def chart(window: FleetWindow) -> dict[str, list[str]]:
    """Each fleet on the chart, with its agents as "name rank"."""
    shown = window.chart
    return {head.name.text(): sorted(f"{node.name.text()} {node.rank.text()}"
                                     for (fleet, _), node in shown.nodes.items() if fleet == key)
            for key, head in shown.heads.items()}


async def test_a_project_and_its_first_fleet_are_made(window, tmp_path: Path):
    shown, _ = window(TEAM)
    assert shown.project_form.isVisibleTo(shown) and not shown.title.isVisibleTo(shown)
    assert not shown.agents_section.isVisibleTo(shown)
    shown.project_form.name.setText("Acme")
    shown.project_form.set_folder(tmp_path / "work")
    shown.project_form.create.click()
    form = shown.start_form
    await until(lambda: shown.chart.isVisibleTo(shown))  # the project's chart, empty so far
    assert not shown.chart.nodes and not shown.back_button.isVisibleTo(shown)
    shown.chart.add_button.click()
    assert form.isVisibleTo(shown) and shown.back_button.isVisibleTo(shown)
    assert shown.title.text() == "Acme" and shown.projects.item(0).text() == "Acme"
    assert form.folder == tmp_path / "work"  # the project's, unless another is chosen
    assert form.heading.text() == "New fleet in Acme"  # the project is named, this is not
    assert form.name.placeholderText() == "Fleet name"
    assert shown.project_form.name.placeholderText() == "Project name"
    assert form.duty.isChecked() and "until you stop" in form.hint.text()
    form.task_kind.setChecked(True)
    assert "Ends" in form.hint.text()
    form.task.setPlainText("Tidy up")
    form.start.click()  # not without a name
    await asyncio.sleep(0.05)
    assert not shown.views
    view = await start(shown, tmp_path, "Tidy up", name="dev")
    await until(lambda: view.manager is not None and view.manager.finished.is_set())
    await until(lambda: len(cards(view)) == 3)
    assert view.isVisibleTo(shown) and shown.agents_section.isVisibleTo(shown)
    assert agents(shown) == [(0, "Lead  K5", "done"), (1, "Scout  K4", "done")]
    assert cards(view) == [("Scout → Lead · question", "Which folder?"),
                           ("Scout → Lead · report", "Tidied docs/"),
                           ("Lead → you · report", "Docs are tidy.")]
    assert shown.title.text() == "dev"
    await until(lambda: "finished" in shown.status.text())
    assert "of $3.00" in shown.status.text()
    assert view.manager.fleet.work == str(tmp_path / "work")
    assert view.folder.parent == shown.project.folder / "fleets"


async def test_the_person_answers_approves_stops_and_resumes(window, tmp_path: Path):
    shown, harness = window({"Lead": [
        {"do": [("approval", "r1", "write_file", "{'path': '/etc/motd'}"), ("say", "Wrote it.")]},
        {"when": "hello", "do": [("say", "Hi there.")]},
        {"when": "started again", "do": [("say", "Back on watch.")]},
        {"when": "still there", "do": [("say", "Yes.")]}]})
    view = await start(shown, tmp_path, "Keep watch", duty=True)
    await until(lambda: any(isinstance(e, ApprovalCard) for e in view.entries))
    [card] = [e for e in view.entries if isinstance(e, ApprovalCard)]
    assert "write_file" in card.question.text() and "/etc/motd" in card.question.text()
    card.allow.click()
    await until(lambda: harness.approved == [("r1", True)])
    await until(lambda: not card.allow.isVisibleTo(shown) and "Allowed" in card.question.text())

    view.input.setPlainText("hello")
    view.send_button.click()
    await until(lambda: any("hello" in heard for heard in harness.heard["Lead"]))
    assert view.input.toPlainText() == ""
    await until(lambda: [e.text.text() for e in view.entries if isinstance(e, YouEntry)]
                == ["hello"])

    shown.stop_button.click()
    await until(lambda: "stopped" in shown.status.text())
    assert shown.resume_button.isVisibleTo(shown)
    shown.resume_button.click()
    await until(lambda: ("Lead", "session-u1") in harness.opened)
    await until(lambda: "running" in shown.status.text() and view.input.isEnabled())

    shown.stop_button.click()  # a message to a stopped fleet starts it again
    await until(lambda: "stopped" in shown.status.text() and view.input.isEnabled())
    view.input.setPlainText("still there?")
    view.send_button.click()
    await until(lambda: any("still there?" in heard for heard in harness.heard["Lead"]))
    assert view.running and "running" in shown.status.text()


async def test_the_window_opens_on_the_chart_of_the_newest_project(window, tmp_path: Path):
    first, _ = window(TEAM)
    view = await start(first, tmp_path, "Tidy up", name="dev")
    await until(lambda: view.manager.finished.is_set() and len(cards(view)) == 3)
    await first.shut()

    again, _ = window({})
    await until(lambda: chart(again) == {"dev": ["Lead K5", "Scout K4"]})
    assert again.chart.isVisibleTo(again) and again.title.text() == "Acme"
    assert "0 running" in again.status.text()
    assert again.projects.item(0).data(PREVIEW_ROLE) == "1 fleet"
    [head] = again.chart.heads.values()
    assert "stopped" in head.status.text()
    lead, scout = (again.chart.nodes[key] for key in sorted(again.chart.nodes))
    assert lead.y() < scout.y()  # the K5 on top
    scout.clicked.emit()  # an agent on the chart opens its fleet, on what concerns it
    opened = again.view
    assert opened is not None and opened.isVisibleTo(again) and again.title.text() == "dev"
    assert [header for header, _ in cards(opened)] == ["Scout → Lead · question",
                                                       "Scout → Lead · report"]
    assert agents(again) == [(0, "Lead  K5", "done"), (1, "Scout  K4", "done")]
    assert not again.resume_button.isVisibleTo(again)  # nothing was at work
    again.show_all.click()
    assert len(cards(opened)) == 3 and not again.show_all.isVisibleTo(again)
    again.back_button.click()
    assert again.chart.isVisibleTo(again) and again.view is None
    head.clicked.emit()  # a fleet's name opens it whole
    assert again.view is opened and len(cards(opened)) == 3


async def test_fleets_run_side_by_side_and_projects_are_switched(window, tmp_path: Path):
    shown, _ = window({"Lead": [{"do": [("say", "On watch.")]}]})
    dev = await start(shown, tmp_path, "Build it", name="dev", duty=True)
    shown.back_button.click()
    art = await start(shown, tmp_path, "Draw it", name="art", duty=True)
    await until(lambda: dev.running and art.running)
    shown.back_button.click()
    await until(lambda: chart(shown) == {"dev": ["Lead K5"], "art": ["Lead K5"]})
    assert "2 running" in shown.status.text()
    dev_head, art_head = shown.chart.heads.values()
    assert dev_head.x() < art_head.x() and dev_head.y() == art_head.y()
    dev_head.clicked.emit()
    shown.stop_button.click()
    await until(lambda: "stopped" in shown.status.text())
    assert art.running  # the other fleet is left alone
    shown.back_button.click()
    assert "1 running" in shown.status.text()

    shown.new_button.click()
    assert shown.project_form.isVisibleTo(shown) and art.running
    await start(shown, tmp_path, "Hire", name="HR", duty=True, project="Other")
    shown.back_button.click()
    await until(lambda: chart(shown) == {"HR": ["Lead K5"]})  # only this project's fleets
    await until(lambda: shown.projects.count() == 2)
    acme = next(shown.projects.item(i) for i in range(2)
                if shown.projects.item(i).text() == "Acme")
    assert acme.data(PREVIEW_ROLE) == "2 fleets · 1 running"
    shown.projects.itemClicked.emit(acme)
    await until(lambda: set(chart(shown)) == {"dev", "art"})
    assert shown.title.text() == "Acme"


async def test_a_described_project_is_drafted_and_started_by_the_person(window,
                                                                          tmp_path: Path):
    teams = [{"name": "dev", "does": "Build the game.", "ongoing": True},
             {"name": "trailer", "does": "Cut a trailer.", "ongoing": False},
             {"name": "legal", "does": "Read contracts.", "ongoing": True}]
    shown, harness = window({
        "Planner": [{"do": [("tool", "propose", {"teams": teams}), ("report", "Proposed.")]}],
        "Lead": [{"do": [("say", "On it.")]}, {"do": [("say", "On it.")]}]})
    form = shown.project_form
    form.name.setText("Studio")
    form.does.setPlainText("Run my game studio")
    form.set_folder(tmp_path / "work")
    form.create.click()
    await until(lambda: shown.drafts)
    [page] = shown.drafts.values()
    await until(lambda: not page.busy)
    assert page.isVisibleTo(shown) and shown.title.text() == "Studio"
    assert "Run my game studio" in harness.heard["Planner"][0]
    assert [(r.name.text(), r.does.toPlainText(), r.kind.duty.isChecked())
            for r in page.rows] == [("dev", "Build the game.", True),
                                    ("trailer", "Cut a trailer.", False),
                                    ("legal", "Read contracts.", True)]
    assert not shown.views and "Lead" not in harness.heard  # nothing runs yet
    page.rows[2].remove.click()  # the person changes the draft
    page.rows[1].name.setText("video")
    page.rows[0].name.clear()
    page.start.click()
    await asyncio.sleep(0.05)
    assert not shown.views and "needs a name" in page.note.text()
    page.rows[0].name.setText("dev")
    page.budget.setValue(4.0)
    page.start.click()
    await until(lambda: chart(shown) == {"dev": ["Lead K5"], "video": ["Lead K5"]})
    assert not shown.drafts and shown.chart.isVisibleTo(shown)
    await until(lambda: len(harness.heard.get("Lead", [])) == 2)
    assert any("Build the game." in told for told in harness.heard["Lead"])
    dev, video = shown.project_views()
    assert dev.fleet.lead.ongoing and not video.fleet.lead.ongoing
    assert "of $4.00" in dev.status


async def test_a_draft_that_fails_or_is_discarded_leaves_the_project(window, tmp_path: Path):
    shown, _ = window({"Planner": [{"do": [("report", "A plan in words.")]}]})
    form = shown.project_form
    form.name.setText("Studio")
    form.does.setPlainText("Run my game studio")
    form.set_folder(tmp_path / "work")
    form.create.click()
    await until(lambda: shown.drafts and not next(iter(shown.drafts.values())).busy)
    [page] = shown.drafts.values()
    assert "No draft" in page.note.text() and page.rows == []
    page.add_button.click()  # by hand, then
    assert len(page.rows) == 1
    page.discard.click()
    await until(lambda: shown.chart.isVisibleTo(shown))  # the project's chart, empty
    assert not shown.drafts and shown.project.name == "Studio"


async def test_the_person_adds_an_agent_and_sets_a_model_on_the_chart(window, tmp_path: Path):
    shown, harness = window({"Lead": [{"do": [("say", "On watch.")]}],
                             "Tester": [{"do": [("say", "Testing.")]}]})
    view = await start(shown, tmp_path, "Keep it healthy", name="dev", duty=True)
    shown.back_button.click()
    await until(lambda: chart(shown) == {"dev": ["Lead K5"]})
    [lead] = shown.chart.nodes.values()
    assert lead.add_action.isEnabled() and lead.model_action.isEnabled()
    lead.add_action.trigger()
    dialog = shown.dialog
    assert dialog.isVisible() and "under Lead" in dialog.windowTitle()
    dialog.name.setText("Tester")
    dialog.task.setPlainText("Run the tests")
    dialog.model.setText("small-1")
    dialog.add.click()
    await until(lambda: chart(shown) == {"dev": ["Lead K5", "Tester K4"]})
    assert not dialog.isVisible() and ("Tester", "small-1") in harness.models
    assert not view.fleet.units["u2"].ongoing  # a task, unless Duty is chosen
    tester = shown.chart.nodes[(str(view.folder), "u2")]
    assert "small-1" in tester.toolTip()

    lead.model_action.trigger()
    shown.dialog.model.setText("big-1")
    shown.dialog.accept()
    await until(lambda: ("Lead", "big-1") in harness.models)
    await view.stop()


async def test_a_fleet_is_built_by_hand_and_then_started(window, tmp_path: Path):
    shown, harness = window({"Chief": [{"do": [("say", "On watch.")]}],
                             "Writer": [{"do": [("say", "Writing.")]}],
                             "Editor": [{"do": [("say", "Editing.")]}]})
    shown.project_form.name.setText("Acme")
    shown.project_form.set_folder(tmp_path / "work")
    shown.project_form.create.click()
    await until(lambda: shown.chart.isVisibleTo(shown))
    shown.chart.add_button.click()
    shown.start_form.name.setText("content")
    shown.start_form.start.click()  # nothing it should do: no lead builds it
    await until(lambda: chart(shown) == {"content": []})
    [view] = shown.views.values()
    key = str(view.folder)
    head, first = shown.chart.heads[key], shown.chart.firsts[key]
    assert "not started" in head.status.text() and not head.start.isVisibleTo(shown)

    first.click()  # the lead
    assert "lead" in shown.dialog.windowTitle()
    shown.dialog.name.setText("Chief")
    shown.dialog.task.setPlainText("Run content")
    shown.dialog.kind.duty.setChecked(True)
    shown.dialog.add.click()
    await until(lambda: chart(shown) == {"content": ["Chief K5"]})
    chief = shown.chart.nodes[(key, "u1")]
    assert chief.state.text() == "planned" and key not in shown.chart.firsts
    chief.add_action.trigger()
    shown.dialog.name.setText("Writer")
    shown.dialog.task.setPlainText("Write the posts")
    shown.dialog.model.setText("small-1")
    shown.dialog.add.click()
    await until(lambda: chart(shown) == {"content": ["Chief K5", "Writer K4"]})
    chief.model_action.trigger()
    shown.dialog.model.setText("big-1")
    shown.dialog.accept()
    await until(lambda: view.fleet.units["u1"].model == "big-1")
    assert not harness.models and not view.running  # nothing has run

    assert head.start.isVisibleTo(shown)
    head.start.click()
    await until(lambda: harness.models == [("Chief", "big-1"), ("Writer", "small-1")])
    await until(lambda: view.running and not head.start.isVisibleTo(shown))
    assert "Writer (K4, u2)" in harness.heard["Chief"][0]
    await until(lambda: chief.state.text() == "working")

    await view.stop()  # a stopped fleet is built on the same way
    await until(lambda: head.start.isVisibleTo(shown))
    chief.add_action.trigger()
    shown.dialog.name.setText("Editor")
    shown.dialog.task.setPlainText("Edit the posts")
    shown.dialog.add.click()
    await until(lambda: (key, "u3") in shown.chart.nodes)
    assert shown.chart.nodes[(key, "u3")].state.text() == "planned"
    head.start.click()
    await until(lambda: ("Editor", None) in harness.models)
    await view.stop()
    await until(lambda: head.start.isVisibleTo(shown))
    chief.add_action.trigger()  # nothing said about it: it is named by its number
    shown.dialog.add.click()
    await until(lambda: (key, "u4") in shown.chart.nodes)
    assert shown.chart.nodes[(key, "u4")].name.text() == "Agent 4"


async def test_a_project_is_deleted_with_its_fleets_and_not_its_work(window, tmp_path: Path):
    shown, _ = window({"Lead": [{"do": [("say", "On watch.")]}]})
    view = await start(shown, tmp_path, "Keep watch", duty=True)
    await until(lambda: shown.projects.count() == 1
                and shown.projects.item(0).data(FLEETS_ROLE) == 1)
    folder = shown.project.folder
    (tmp_path / "work" / "kept.txt").write_text("the agents' work")
    shown.ask_delete(shown.projects.item(0))  # what a right-click on it offers
    assert "Acme" in shown.dialog.question.text() and "1 fleet" in shown.dialog.question.text()
    shown.dialog.reject()
    await asyncio.sleep(0.05)
    assert folder.exists() and view.running
    shown.ask_delete(shown.projects.item(0))
    shown.dialog.delete.click()
    await until(lambda: not folder.exists())
    await until(lambda: shown.projects.count() == 0 and shown.project_form.isVisibleTo(shown))
    assert not shown.views and shown.project is None and not view.running
    assert (tmp_path / "work" / "kept.txt").exists()


async def test_a_fleet_is_deleted_from_the_chart(window, tmp_path: Path):
    shown, _ = window({"Lead": [{"do": [("say", "On watch.")]}]})
    first = await start(shown, tmp_path, "Keep watch", name="dev", duty=True)
    shown.back_button.click()
    second = await start(shown, tmp_path, "Keep watch", name="art", duty=True)
    shown.back_button.click()
    await until(lambda: chart(shown) == {"dev": ["Lead K5"], "art": ["Lead K5"]})
    shown.chart.heads[str(first.folder)].delete_action.trigger()
    assert "dev" in shown.dialog.question.text()
    shown.dialog.reject()
    await asyncio.sleep(0.05)
    assert first.folder.exists() and first.running
    shown.chart.heads[str(first.folder)].delete_action.trigger()
    shown.dialog.delete.click()
    await until(lambda: not first.folder.exists())
    await until(lambda: chart(shown) == {"art": ["Lead K5"]})
    assert list(shown.views.values()) == [second] and second.running and not first.running
    await until(lambda: shown.projects.item(0).data(FLEETS_ROLE) == 1)
    await second.stop()


async def test_an_agent_a_fleet_and_a_project_are_renamed(window, tmp_path: Path):
    shown, _ = window(TEAM)
    view = await start(shown, tmp_path, "Tidy up", name="dev")
    shown.back_button.click()
    await until(lambda: chart(shown) == {"dev": ["Lead K5", "Scout K4"]})
    key = str(view.folder)

    def rename(action, to: str) -> None:
        action.trigger()
        shown.dialog.line.setText(to)
        shown.dialog.accept()

    rename(shown.chart.nodes[(key, "u2")].rename_action, "Tidier")  # while it runs
    await until(lambda: chart(shown) == {"dev": ["Lead K5", "Tidier K4"]})
    rename(shown.chart.heads[key].rename_action, "docs")
    await until(lambda: chart(shown) == {"docs": ["Lead K5", "Tidier K4"]})
    rename(shown.chart.heads[key].rename_action, "  ")  # no name: as it was
    await asyncio.sleep(0.05)
    assert view.name == "docs"
    await view.stop()
    rename(shown.chart.nodes[(key, "u1")].rename_action, "Chief")  # and while it does not
    await until(lambda: chart(shown) == {"docs": ["Chief K5", "Tidier K4"]})
    saved = Fleet.load(view.folder)
    assert (saved.name, [u.name for u in saved.units.values()]) == ("docs", ["Chief", "Tidier"])

    shown.ask_rename(shown.projects.item(0))  # what a right-click on it offers
    assert shown.dialog.line.text() == "Acme"
    shown.dialog.line.setText("Globex")
    shown.dialog.accept()
    await until(lambda: shown.projects.item(0).text() == "Globex")
    assert shown.title.text() == "Globex" and shown.project.name == "Globex"
    assert project.load(shown.project.folder).name == "Globex"


async def test_a_project_needs_a_folder_that_is_there(window, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "me")
    (tmp_path / "me").mkdir()
    shown, _ = window({})
    form = shown.project_form
    assert form.folder is None and form.folder_button.text().strip() == "Choose a folder"
    form.name.setText("Acme")
    for folder, problem in ((None, "Choose a folder first."), (tmp_path / "me", "home folder"),
                            (tmp_path / "gone", "no folder")):
        form.set_folder(folder)
        form.create.click()
        await asyncio.sleep(0.05)
        assert shown.project is None and problem in form.warning.text()
    assert not (tmp_path / "gone").exists()  # nothing is made that nobody asked for
    assert not (tmp_path / "home" / "projects").exists()
    form.set_folder(tmp_path / "work")
    assert not form.warning.isVisibleTo(shown) and form.folder_button.text().strip() == "work"
    form.create.click()
    await until(lambda: shown.project is not None)
    assert shown.project.work == tmp_path / "work" and form.folder is None


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


async def test_the_window_has_rounded_corners_unless_maximized(window, qapp: QApplication):
    shown, _ = window({})
    shown.resize(900, 600)
    shown.show()
    qapp.processEvents()
    image = shown.grab().toImage()
    for x, y in ((0, 0), (899, 0), (0, 599), (899, 599)):
        assert image.pixelColor(x, y).alpha() == 0  # the desktop shows through
    assert image.pixelColor(450, 300).alpha() == 255
    assert image.pixelColor(880, 592).alpha() == 255  # the window's own background
    shown.showMaximized()
    qapp.processEvents()
    assert shown.grab().toImage().pixelColor(0, 0).alpha() == 255
