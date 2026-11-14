"""The window, offscreen, on the second harness (tests/fakeharness.py)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import asyncio
import getpass
from collections.abc import Callable
from pathlib import Path
from typing import Any

import kadence.frontends.desktop  # noqa: F401 - before any Qt import
import pytest
from kadence.frontends.desktop import theme
from kadence.frontends.desktop.window import PREVIEW_ROLE
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent, QImage
from PySide6.QtWidgets import QApplication

from kfm import project
from kfm.config import FleetConfig, load
from kfm.desktop import (
    FLEETS_ROLE,
    ApprovalCard,
    DeliveryCard,
    FleetView,
    FleetWindow,
    SaidEntry,
    YouEntry,
)
from kfm.fleet import Fleet
from kfm.local import LocalService
from kfm.remote import RemoteService
from kfm.server import Server
from tests.fakeharness import FakeHarness

TEAM = {
    "K5 u1": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Tidy the docs"})]},
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
    return {head.name.text(): sorted(f"{node.name.text()} {node.rank.text()}".strip()
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
    assert "(optional)" in shown.project_form.does.placeholderText()  # both may stay empty
    assert "(optional)" in form.task.placeholderText()
    assert form.duty.isChecked() and "until you stop" in form.hint.text()
    form.task_kind.setChecked(True)
    assert "Ends" in form.hint.text()
    form.task.setPlainText("Tidy up")
    form.start.click()  # not without a name
    await asyncio.sleep(0.05)
    assert not shown.views
    view = await start(shown, tmp_path, "Tidy up", name="dev")
    await until(lambda: shown.service.state(view.id).finished)
    await until(lambda: len(cards(view)) == 3)
    assert view.isVisibleTo(shown) and shown.agents_section.isVisibleTo(shown)
    assert agents(shown) == [(0, "K5", "done"), (1, "Scout  K4", "done")]
    assert cards(view) == [("Scout → K5 u1 · question", "Which folder?"),
                           ("Scout → K5 u1 · report", "Tidied docs/"),
                           ("K5 u1 → you · report", "Docs are tidy.")]
    assert shown.title.text() == "dev"
    await until(lambda: "finished" in shown.status.text())
    assert "of $3.00" in shown.status.text()
    assert view.fleet.work == str(tmp_path / "work")
    assert view.folder.parent == shown.project.folder / "fleets"


async def test_the_person_answers_approves_stops_and_resumes(window, tmp_path: Path):
    shown, harness = window({"K5 u1": [
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
    await until(lambda: any("hello" in heard for heard in harness.heard["K5 u1"]))
    assert view.input.toPlainText() == ""
    await until(lambda: [e.text.text() for e in view.entries if isinstance(e, YouEntry)]
                == ["Keep watch", "hello"])  # the task is the first thing you said

    shown.stop_button.click()
    await until(lambda: "stopped" in shown.status.text())
    assert shown.resume_button.isVisibleTo(shown)
    shown.resume_button.click()
    await until(lambda: ("K5 u1", "session-u1") in harness.opened)
    await until(lambda: "running" in shown.status.text() and view.input.isEnabled())

    shown.stop_button.click()  # a message to a stopped fleet starts it again
    await until(lambda: "stopped" in shown.status.text() and view.input.isEnabled())
    view.input.setPlainText("still there?")
    view.send_button.click()
    await until(lambda: any("still there?" in heard for heard in harness.heard["K5 u1"]))
    assert view.running and "running" in shown.status.text()


async def test_the_window_opens_on_the_chart_of_the_newest_project(window, tmp_path: Path):
    first, _ = window(TEAM)
    view = await start(first, tmp_path, "Tidy up", name="dev")
    await until(lambda: first.service.state(view.id).finished and len(cards(view)) == 3)
    await first.shut()

    again, _ = window({})
    await until(lambda: chart(again) == {"dev": ["K5", "Scout K4"]})
    assert again.chart.isVisibleTo(again) and again.title.text() == "Acme"
    assert "0 running" in again.status.text()
    assert again.projects.item(0).data(PREVIEW_ROLE) == "1 fleet"
    [head] = again.chart.heads.values()
    assert "stopped" in head.status.text()
    lead, scout = (again.chart.nodes[key] for key in sorted(again.chart.nodes))
    assert lead.y() < scout.y()  # the K5 on top
    scout.clicked.emit()  # an agent on the chart opens its fleet, on what concerns it
    opened = again.view
    assert opened is not None and opened.isVisibleTo(again)
    assert again.title.text() == "dev › Scout"
    assert [header for header, _ in cards(opened)] == ["Scout → K5 u1 · question",
                                                       "Scout → K5 u1 · report"]
    assert agents(again) == [(0, "K5", "done"), (1, "Scout  K4", "done")]
    assert not again.resume_button.isVisibleTo(again)  # nothing was at work
    again.show_all.click()
    assert len(cards(opened)) == 3 and not again.show_all.isVisibleTo(again)
    again.back_button.click()
    assert again.chart.isVisibleTo(again) and again.view is None
    head.clicked.emit()  # a fleet's name opens it whole
    assert again.view is opened and len(cards(opened)) == 3


async def test_fleets_run_side_by_side_and_projects_are_switched(window, tmp_path: Path):
    shown, _ = window({"K5 u1": [{"do": [("say", "On watch.")]}]})
    dev = await start(shown, tmp_path, "Build it", name="dev", duty=True)
    shown.back_button.click()
    art = await start(shown, tmp_path, "Draw it", name="art", duty=True)
    await until(lambda: dev.running and art.running)
    shown.back_button.click()
    await until(lambda: chart(shown) == {"dev": ["K5"], "art": ["K5"]})
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
    await until(lambda: chart(shown) == {"HR": ["K5"]})  # only this project's fleets
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
        "K5 u1": [{"do": [("say", "On it.")]}, {"do": [("say", "On it.")]}]})
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
    assert not shown.views and "K5 u1" not in harness.heard  # nothing runs yet
    page.rows[2].remove.click()  # the person changes the draft
    page.rows[1].name.setText("video")
    page.rows[0].name.clear()
    page.start.click()
    await asyncio.sleep(0.05)
    assert not shown.views and "needs a name" in page.note.text()
    page.rows[0].name.setText("dev")
    page.budget.setValue(4.0)
    page.start.click()
    await until(lambda: chart(shown) == {"dev": ["K5"], "video": ["K5"]})
    assert not shown.drafts and shown.chart.isVisibleTo(shown)
    await until(lambda: len(harness.heard.get("K5 u1", [])) == 2)
    assert any("Build the game." in told for told in harness.heard["K5 u1"])
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
    shown, harness = window({"K5 u1": [{"do": [("say", "On watch.")]}],
                             "Tester": [{"do": [("say", "Testing.")]}]})
    view = await start(shown, tmp_path, "Keep it healthy", name="dev", duty=True)
    shown.back_button.click()
    await until(lambda: chart(shown) == {"dev": ["K5"]})
    [lead] = shown.chart.nodes.values()
    assert lead.add_action.isEnabled() and lead.model_action.isEnabled()
    lead.add_action.trigger()
    dialog = shown.dialog
    assert dialog.isVisible() and "under K5 u1" in dialog.windowTitle()
    dialog.name.setText("Tester")
    dialog.task.setPlainText("Run the tests")
    dialog.model.setText("small-1")
    dialog.add.click()
    await until(lambda: chart(shown) == {"dev": ["K5", "Tester K4"]})
    await until(lambda: not dialog.isVisible())  # once its process is up
    assert ("Tester", "small-1") in harness.models
    assert not view.fleet.units["u2"].ongoing  # a task, unless Duty is chosen
    tester = shown.chart.nodes[(str(view.folder), "u2")]
    assert "small-1" in tester.toolTip()

    lead.model_action.trigger()
    shown.dialog.model.setText("big-1")
    shown.dialog.accept()
    await until(lambda: ("K5 u1", "big-1") in harness.models)
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
    chief.add_action.trigger()  # nothing said about it: it has no name, only its rank
    shown.dialog.add.click()
    await until(lambda: (key, "u4") in shown.chart.nodes)
    nameless = shown.chart.nodes[(key, "u4")]
    assert (nameless.name.text(), nameless.rank.text()) == ("K4", "")


async def test_a_project_is_deleted_with_its_fleets_and_not_its_work(window, tmp_path: Path):
    shown, _ = window({"K5 u1": [{"do": [("say", "On watch.")]}]})
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
    shown, _ = window({"K5 u1": [{"do": [("say", "On watch.")]}]})
    first = await start(shown, tmp_path, "Keep watch", name="dev", duty=True)
    shown.back_button.click()
    second = await start(shown, tmp_path, "Keep watch", name="art", duty=True)
    shown.back_button.click()
    await until(lambda: chart(shown) == {"dev": ["K5"], "art": ["K5"]})
    shown.chart.heads[str(first.folder)].delete_action.trigger()
    assert "dev" in shown.dialog.question.text()
    shown.dialog.reject()
    await asyncio.sleep(0.05)
    assert first.folder.exists() and first.running
    shown.chart.heads[str(first.folder)].delete_action.trigger()
    shown.dialog.delete.click()
    await until(lambda: not first.folder.exists())
    await until(lambda: chart(shown) == {"art": ["K5"]})
    assert list(shown.views.values()) == [second] and second.running and not first.running
    await until(lambda: shown.projects.item(0).data(FLEETS_ROLE) == 1)
    await second.stop()


async def test_an_agent_a_fleet_and_a_project_are_renamed(window, tmp_path: Path):
    shown, _ = window(TEAM)
    view = await start(shown, tmp_path, "Tidy up", name="dev")
    shown.back_button.click()
    await until(lambda: chart(shown) == {"dev": ["K5", "Scout K4"]})
    key = str(view.folder)

    def rename(action, to: str) -> None:
        action.trigger()
        shown.dialog.line.setText(to)
        shown.dialog.accept()

    rename(shown.chart.nodes[(key, "u2")].rename_action, "Tidier")  # while it runs
    await until(lambda: chart(shown) == {"dev": ["K5", "Tidier K4"]})
    rename(shown.chart.heads[key].rename_action, "docs")
    await until(lambda: chart(shown) == {"docs": ["K5", "Tidier K4"]})
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


async def test_the_chosen_agent_is_the_one_messaged(window, tmp_path: Path):
    shown, harness = window({
        "K5 u1": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Map the docs"})]}],
        "Scout": [{"do": [("say", "Mapping.")]},
                  {"when": "Skip the drafts", "do": [("say", "Skipping them.")]}]})
    view = await start(shown, tmp_path, "Tidy up", duty=True)
    await until(lambda: len(agents(shown)) == 2)
    assert view.input.placeholderText() == "Message the lead"
    shown.agents.setCurrentItem(shown.agents.topLevelItem(0).child(0))
    assert view.input.placeholderText() == "Message Scout"
    # Its page is a conversation with it: its task from its lead, then what it wrote,
    # without its name on every line.
    seen = [e for e in view.entries if e.isVisibleTo(shown)]
    assert [type(e).__name__ for e in seen] == ["InstructionCard", "SaidEntry"]
    assert seen[0].header.text() == "K5 u1 → Scout · task"
    assert seen[0].body.toPlainText() == "Map the docs" and not seen[1].header.isVisibleTo(shown)
    assert shown.title.text() == "dev › Scout" and "working" in shown.status.text()
    view.input.setPlainText("Skip the drafts")
    view.send_button.click()
    await until(lambda: any("Skip the drafts" in heard for heard in harness.heard["Scout"]))
    await until(lambda: [e.text.text() for e in view.entries
                         if isinstance(e, YouEntry) and e.isVisibleTo(shown)]
                == ["Skip the drafts"])
    shown.show_all.click()
    assert view.input.placeholderText() == "Message the lead" and shown.title.text() == "dev"
    assert all(e.header.isVisibleTo(shown) for e in view.entries if isinstance(e, SaidEntry))
    shown.agents.setCurrentItem(shown.agents.topLevelItem(0))  # the lead: nothing to add
    assert view.input.placeholderText() == "Message the lead"
    assert [e.text.text() for e in view.entries
            if isinstance(e, YouEntry) and e.isVisibleTo(shown)] == ["Tidy up"]


async def test_files_are_attached_to_a_message(window, tmp_path: Path):
    shown, harness = window({"K5 u1": [{"do": [("say", "On watch.")]}]})
    view = await start(shown, tmp_path, "Keep watch", duty=True)
    await until(lambda: harness.heard.get("K5 u1"))
    note = tmp_path / "note.txt"
    note.write_text("read me")
    assert not view.attachments.isVisibleTo(shown)
    view.input.attached.emit([str(note)])  # dropped or pasted into the box
    assert view.attachments.isVisibleTo(shown) and view.attachments.paths() == [str(note)]
    view.send_button.click()  # files alone are a message
    await until(lambda: harness.files == [("K5 u1", [str(note)], False)])
    assert view.attachments.paths() == [] and not view.attachments.isVisibleTo(shown)
    await until(lambda: [e.files for e in view.entries if isinstance(e, YouEntry)]
                == [[], ["note.txt"]])
    assert view.attach_button.toolTip() == "Add files"

    picture = QImage(4, 4, QImage.Format.Format_RGB32)  # a picture copied, then pasted
    picture.fill(0)
    view.input.pasted.emit(picture)
    [pasted] = view.attachments.paths()
    assert Path(pasted).name == "pasted-1.png" and not QImage(pasted).isNull()
    view.attachments.clear()
    dropped = QMimeData()  # a file dropped anywhere on the fleet's page
    dropped.setUrls([QUrl.fromLocalFile(str(note))])
    view.dropEvent(QDropEvent(QPointF(5, 5), Qt.DropAction.CopyAction, dropped,
                              Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    assert view.attachments.paths() == [str(note)]


async def test_settings_are_reached_from_the_profile_corner(tmp_path: Path, qapp: QApplication):
    shown = FleetWindow(tmp_path / "home", harness=FakeHarness({}))  # settings from its home
    shown.show()
    try:
        assert shown.profile_button.text().strip() == getpass.getuser()
        assert [a.text() for a in shown.profile_button.menu().actions() if a.text()][1:] == [
            "Settings", "Add to App Menu", "Quit"]
        shown.gear_button.click()
        await until(lambda: shown.dialog is not None and shown.dialog.isVisible())
        dialog = shown.dialog
        assert dialog.budget.value() == 10.0
        dialog.models["K5"].setText("big-1")
        dialog.budget.setValue(25.0)
        dialog.agents.setValue(0)  # no fleet has no agents
        dialog.save.click()
        await until(lambda: "above zero" in dialog.warning.text())
        assert dialog.isVisible()
        dialog.agents.setValue(6)
        dialog.save.click()
        await until(lambda: not dialog.isVisible())
        saved = load(tmp_path / "home" / "fleet.toml")
        assert (saved.models, saved.budget_usd, saved.max_units) == ({"K5": "big-1"}, 25.0, 6)
        await until(lambda: shown.start_form.budget.value() == 25.0)  # what a new fleet gets
        shown.gear_button.click()
        await until(lambda: shown.dialog is not dialog and shown.dialog.isVisible())
        assert shown.dialog.models["K5"].text() == "big-1"
        shown.dialog.reject()
    finally:
        await shown.shut()


async def test_an_agents_task_is_changed_and_an_agent_is_removed_on_the_chart(
        window, tmp_path: Path):
    shown, harness = window({
        "K5 u1": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Map the docs"})]}],
        "Scout": [{"do": [("say", "Mapping.")]}]})
    view = await start(shown, tmp_path, "Tidy up", duty=True)
    shown.back_button.click()
    await until(lambda: chart(shown) == {"dev": ["K5", "Scout K4"]})
    key = str(view.folder)
    scout = shown.chart.nodes[(key, "u2")]
    scout.task_action.trigger()
    assert shown.dialog.task.toPlainText() == "Map the docs"
    shown.dialog.task.setPlainText("Map the code")
    shown.dialog.save.click()
    await until(lambda: harness.instructed and "Map the code" in harness.instructed[0][1])
    assert "Map the code" in scout.toolTip()

    scout.remove_action.trigger()
    assert "Scout" in shown.dialog.question.text()
    shown.dialog.delete.click()
    await until(lambda: chart(shown) == {"dev": ["K5"]})
    assert list(view.fleet.units) == ["u1"]
    shown.chart.heads[key].clicked.emit()  # its row in the agents list went with it
    await until(lambda: agents(shown) == [(0, "K5", "working")])
    shown.back_button.click()

    await view.stop()  # in a stopped fleet too: it works on it when the fleet starts
    lead = shown.chart.nodes[(key, "u1")]
    await until(lambda: lead.task_action.isEnabled() and lead.remove_action.isEnabled())
    lead.task_action.trigger()
    shown.dialog.task.setPlainText("Tidy the code")
    shown.dialog.save.click()
    await until(lambda: view.fleet.units["u1"].task == "Tidy the code")
    assert not view.running and len(harness.instructed) == 1  # nobody was started for it
    lead.remove_action.trigger()
    shown.dialog.delete.click()
    await until(lambda: chart(shown) == {"dev": []} and key in shown.chart.firsts)
    assert not Fleet.load(view.folder).units


async def test_a_fleet_outlives_the_window_that_started_it(tmp_path: Path, qapp: QApplication):
    """The window on the service `kfm serve` runs: closing it lets go of the fleet, which
    keeps running, and another window finds it with what happened meanwhile."""
    harness = FakeHarness({"K5 u1": [{"do": [("say", "On watch.")]},
                                     {"when": "anything new", "do": [("say", "All quiet.")]}]})
    (tmp_path / "work").mkdir()
    home, socket = tmp_path / "home", tmp_path / "s"
    owner = LocalService(home, harness=harness, config=FleetConfig())
    serving = asyncio.ensure_future(Server(owner, socket, idle_s=0.3).run())
    await until(socket.exists)

    first = FleetWindow(home, service=await RemoteService.connect(home, socket=socket,
                                                                 start=False))
    first.show()
    view = await start(first, tmp_path, "Keep watch", duty=True)
    await until(lambda: "running" in first.status.text())
    await until(lambda: any(isinstance(e, SaidEntry) for e in view.entries))
    await first.shut()
    assert owner.state(view.id).running  # nobody is watching, and it runs

    again = FleetWindow(home, service=await RemoteService.connect(home, socket=socket,
                                                                 start=False))
    again.show()
    try:
        await until(lambda: chart(again) == {"dev": ["K5"]})
        [head] = again.chart.heads.values()
        assert "running" in head.status.text() and not head.start.isVisibleTo(again)
        head.clicked.emit()
        opened = again.view
        await until(lambda: [e.body.toPlainText() for e in opened.entries
                             if isinstance(e, SaidEntry)] == ["On watch."])
        opened.input.setPlainText("anything new?")
        opened.send_button.click()
        await until(lambda: [e.body.toPlainText() for e in opened.entries
                             if isinstance(e, SaidEntry)] == ["On watch.", "All quiet."])
        again.stop_button.click()
        await until(lambda: "stopped" in again.status.text())
    finally:
        await again.shut()
    await asyncio.wait_for(serving, 10)


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
    await until(lambda: shown.app_menu_action.isVisible())  # in the profile's menu
    shown.app_menu_action.trigger()
    await until(lambda: not shown.app_menu_action.isVisible())
    entry = (tmp_path / "share/applications/io.github.k5031.kfm.desktop").read_text()
    assert "Name=KFM" in entry and "-m kfm.desktop" in entry and "Terminal=false" in entry
    [icon] = (tmp_path / "share/icons/hicolor/512x512/apps").glob("io.github.k5031.kfm-*.png")
    assert f"Icon={icon.stem}" in entry
    again, _ = window({})
    await asyncio.sleep(0.2)
    assert not again.app_menu_action.isVisible()  # it is there already


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
