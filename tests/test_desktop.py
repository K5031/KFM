"""The window, offscreen, on the second harness (tests/fakeharness.py)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import asyncio
import getpass
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import kadence.frontends.desktop  # noqa: F401 - before any Qt import
import pytest
import tomli_w
from kadence.frontends.desktop import theme
from kadence.frontends.desktop.window import PREVIEW_ROLE, AssistantBubble, WrapUpCard
from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl
from PySide6.QtGui import QDropEvent, QImage
from PySide6.QtWidgets import QApplication, QPushButton

from kfm import project
from kfm.config import FleetConfig, load
from kfm.desktop import (
    FLEETS_ROLE,
    UNREADABLE_ROLE,
    ApprovalCard,
    DeliveryCard,
    FleetView,
    FleetWindow,
    SaidEntry,
    YouEntry,
    saved_theme,
)
from kfm.events import Said, Told
from kfm.fleet import Fleet
from kfm.local import LocalService
from kfm.remote import RemoteService
from kfm.server import Server
from kfm.service import FleetState
from tests.fakeharness import FakeHarness

TEAM = {
    "K5001": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Tidy the docs"})]},
             {"when": "[Question from Scout",
              "do": [("tool", "message", {"agent": "K4001", "text": "Use docs/"})]},
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
    """Qt's own events (timers, layout, what is shown later) are run as well: the tests'
    loop is asyncio's, which does not run them as the app's loop does."""
    async def poll() -> None:
        while not predicate():
            QApplication.processEvents()
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


@pytest.fixture
async def window(tmp_path: Path, qapp: QApplication):
    made: list[FleetWindow] = []

    def make(scripts: dict[str, list[dict[str, Any]]]) -> tuple[FleetWindow, FakeHarness]:
        harness = FakeHarness(scripts)
        (tmp_path / "work").mkdir(exist_ok=True)
        shown = FleetWindow(LocalService(tmp_path / "home", harness=harness,
                                         config=FleetConfig()))
        shown.resize(1100, 720)
        shown.show()
        made.append(shown)
        return shown, harness

    yield make
    for shown in made:
        await shown.shut()


def folder(tmp_path: Path, fleet_id: str) -> Path:
    """Where the service keeps what has this id: a project's, or a fleet's."""
    owner, _, fleet = fleet_id.partition("/")
    return tmp_path / "home" / "projects" / owner / ("fleets" if fleet else "") / fleet


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


def teams(window: FleetWindow) -> dict[str, FleetView]:
    """The pages of the fleets that are teams: a project's secretary is not one."""
    return {key: view for key, view in window.views.items() if not view.secretary}


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
    assert form.task_kind.isChecked() and "Ends" in form.hint.text()  # a task unless said
    form.duty.setChecked(True)
    assert "until you stop" in form.hint.text()
    form.task_kind.setChecked(True)
    form.task.setPlainText("Tidy up")
    form.start.click()  # not without a name
    await asyncio.sleep(0.05)
    assert not teams(shown)
    view = await start(shown, tmp_path, "Tidy up", name="dev")
    await until(lambda: shown.service.state(view.id).finished)
    await until(lambda: len(cards(view)) == 3)
    assert view.isVisibleTo(shown) and shown.agents_section.isVisibleTo(shown)
    assert agents(shown) == [(0, "K5001", "done"), (1, "Scout  K4001", "done")]
    assert cards(view) == [("Scout → K5001 · question", "Which folder?"),
                           ("Scout → K5001 · report", "Tidied docs/"),
                           ("K5001 → you · report", "Docs are tidy.")]
    assert shown.title.text() == "dev"
    await until(lambda: "finished" in shown.status.text())
    assert "of $3.00" in shown.status.text()
    assert view.fleet.work == str(tmp_path / "work")
    assert view.id.startswith(f"{shown.project.id}/") and teams(shown) == {view.id: view}


async def test_the_person_answers_approves_stops_and_resumes(window, tmp_path: Path):
    shown, harness = window({"K5001": [
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
    await until(lambda: any("hello" in heard for heard in harness.heard["K5001"]))
    assert view.input.toPlainText() == ""
    await until(lambda: [e.text.text() for e in view.entries if isinstance(e, YouEntry)]
                == ["Keep watch", "hello"])  # the task is the first thing you said

    shown.stop_button.click()
    await until(lambda: "stopped" in shown.status.text())
    assert shown.resume_button.isVisibleTo(shown)
    shown.resume_button.click()
    await until(lambda: ("K5001", "session-K5001") in harness.opened)
    await until(lambda: "running" in shown.status.text() and view.input.isEnabled())

    # A stopped fleet shows its limit beside Resume; a new amount there is its new limit,
    # which a fleet that stopped at its limit needs.
    shown.stop_button.click()
    await until(lambda: "stopped" in shown.status.text())
    assert shown.limit.isVisibleTo(shown) and shown.limit.value() == 3.0
    shown.limit.setValue(7.5)
    shown.resume_button.click()
    await until(lambda: "running" in shown.status.text() and "of $7.50" in shown.status.text())
    assert not shown.limit.isVisibleTo(shown)

    shown.stop_button.click()  # a message to a stopped fleet starts it again
    await until(lambda: "stopped" in shown.status.text() and view.input.isEnabled())
    view.input.setPlainText("still there?")
    view.send_button.click()
    await until(lambda: any("still there?" in heard for heard in harness.heard["K5001"]))
    assert view.running and "running" in shown.status.text()


class Notified:
    """A notifier that keeps what it was given."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str, Any]] = []

    def send(self, title: str, body: str, clicked: Any) -> None:
        self.sent.append((title, body, clicked))

    async def aclose(self) -> None:
        pass


async def test_what_waits_for_the_person_is_notified_while_they_are_elsewhere(
        window, tmp_path: Path):
    shown, harness = window({"K5001": [
        {"do": [("approval", "r1", "write_file", "{'path': '/etc/motd'}"), ("say", "Wrote.")]},
        {"when": "again", "do": [("approval", "r2", "shell", "{'command': 'ls'}")]}]})
    notified = shown.notifier = Notified()
    shown.away = lambda: False  # looking at the window: it shows what waits
    view = await start(shown, tmp_path, "Keep watch", duty=True)
    await until(lambda: any(isinstance(e, ApprovalCard) for e in view.entries))
    [card] = [e for e in view.entries if isinstance(e, ApprovalCard)]
    shown.away = lambda: True
    shown._refresh()
    assert not notified.sent  # it was seen, and is not news later
    card.allow.click()
    await until(lambda: harness.approved == [("r1", True)])

    shown.show_chart()
    view.input.setPlainText("again")
    view.send_button.click()
    await until(lambda: len(notified.sent) == 1)
    title, body, clicked = notified.sent[0]
    assert title == "dev › K5001" and "shell" in body and "ls" in body
    shown._refresh()
    assert len(notified.sent) == 1  # once
    clicked()  # a click on it opens the fleet it is about
    assert shown.view is view


async def test_an_approval_can_be_given_for_the_rest_of_an_agents_work(window, tmp_path: Path):
    shown, harness = window({"K5001": [
        {"do": [("approval", "r1", "shell", "{'command': 'uv run pytest'}")]}]})
    view = await start(shown, tmp_path, "Keep watch", duty=True)
    await until(lambda: any(isinstance(e, ApprovalCard) for e in view.entries))
    [card] = [e for e in view.entries if isinstance(e, ApprovalCard)]
    assert [b.text() for b in (card.allow, card.always, card.deny)] == [
        "Allow", "Always allow", "Deny"]
    card.always.click()
    await until(lambda: harness.approved == [("r1", True)] and harness.always == ["r1"])
    await until(lambda: not card.always.isVisibleTo(shown)
                and card.question.text().startswith("Always allowed"))


async def test_the_window_opens_on_the_chart_of_the_newest_project(window, tmp_path: Path):
    first, _ = window(TEAM)
    view = await start(first, tmp_path, "Tidy up", name="dev")
    await until(lambda: first.service.state(view.id).finished and len(cards(view)) == 3)
    await first.shut()

    again, _ = window({})
    await until(lambda: chart(again) == {"dev": ["K5001", "Scout K4001"]})
    assert again.chart.isVisibleTo(again) and again.title.text() == "Acme"
    assert "0 running" in again.status.text()
    assert again.projects.item(0).data(PREVIEW_ROLE) == "1 fleet"
    [head] = again.chart.heads.values()
    assert "stopped" in head.status.text()
    scout, lead = (again.chart.nodes[key] for key in sorted(again.chart.nodes))
    assert lead.y() < scout.y()  # the K5 on top
    scout.clicked.emit()  # an agent on the chart opens its fleet, on what concerns it
    opened = again.view
    assert opened is not None and opened.isVisibleTo(again)
    assert again.title.text() == "dev › Scout"
    assert [header for header, _ in cards(opened)] == ["Scout → K5001 · question",
                                                       "Scout → K5001 · report"]
    assert agents(again) == [(0, "K5001", "done"), (1, "Scout  K4001", "done")]
    assert not again.resume_button.isVisibleTo(again)  # nothing was at work
    again.show_all.click()
    assert len(cards(opened)) == 3 and not again.show_all.isVisibleTo(again)
    again.back_button.click()
    assert again.chart.isVisibleTo(again) and again.view is None
    head.clicked.emit()  # a fleet's name opens it whole
    assert again.view is opened and len(cards(opened)) == 3


async def test_fleets_run_side_by_side_and_projects_are_switched(window, tmp_path: Path):
    shown, _ = window({"K5001": [{"do": [("say", "On watch.")]}]})
    dev = await start(shown, tmp_path, "Build it", name="dev", duty=True)
    shown.back_button.click()
    art = await start(shown, tmp_path, "Draw it", name="art", duty=True)
    await until(lambda: dev.running and art.running)
    shown.back_button.click()
    await until(lambda: chart(shown) == {"dev": ["K5001"], "art": ["K5002"]})
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
    await until(lambda: chart(shown) == {"HR": ["K5001"]})  # only this project's fleets
    await until(lambda: shown.projects.count() == 2)
    acme = next(shown.projects.item(i) for i in range(2)
                if shown.projects.item(i).text() == "Acme")
    assert acme.data(PREVIEW_ROLE) == "2 fleets · 1 running"
    shown.projects.itemClicked.emit(acme)
    await until(lambda: set(chart(shown)) == {"dev", "art"})
    assert shown.title.text() == "Acme"


async def test_a_described_project_is_drafted_and_started_by_the_person(window,
                                                                          tmp_path: Path):
    proposed = [{"name": "dev", "does": "Build the game.", "ongoing": True},
             {"name": "trailer", "does": "Cut a trailer.", "ongoing": False},
             {"name": "legal", "does": "Read contracts.", "ongoing": True}]
    shown, harness = window({
        "Secretary": [{"do": [("tool", "propose", {"teams": proposed}),
                              ("say", "Proposed.")]},
                      {"when": "Add a legal team", "do": [
                          ("tool", "propose", {"teams": [proposed[2]]}), ("say", "Proposed.")]}],
        "K5001": [{"do": [("say", "On it.")]}], "K5002": [{"do": [("say", "On it.")]}]})
    form = shown.project_form
    form.name.setText("Studio")
    form.does.setPlainText("Run my game studio")
    form.set_folder(tmp_path / "work")
    form.create.click()
    await until(lambda: shown.drafts)
    [page] = shown.drafts.values()
    await until(lambda: not page.busy)
    assert page.isVisibleTo(shown) and shown.title.text() == "Studio"
    assert "Run my game studio" in harness.heard["Secretary"][0]
    assert [(r.name.text(), r.does.toPlainText(), r.kind.duty.isChecked())
            for r in page.rows] == [("dev", "Build the game.", True),
                                    ("trailer", "Cut a trailer.", False),
                                    ("legal", "Read contracts.", True)]
    assert not teams(shown) and "K5001" not in harness.heard  # nothing runs yet
    page.rows[2].remove.click()  # the person changes the draft
    page.rows[1].name.setText("video")
    page.rows[0].name.clear()
    page.start.click()
    await asyncio.sleep(0.05)
    assert not teams(shown) and "needs a name" in page.note.text()
    page.rows[0].name.setText("dev")
    page.budget.setValue(4.0)
    page.start.click()
    await until(lambda: chart(shown) == {"dev": ["K5001"], "video": ["K5002"]})
    assert not shown.drafts and shown.chart.isVisibleTo(shown)
    await until(lambda: "K5001" in harness.heard and "K5002" in harness.heard)
    assert any("Build the game." in told for told in harness.heard["K5001"])
    dev, video = shown.project_views()
    assert dev.fleet.lead.ongoing and not video.fleet.lead.ongoing
    assert "of $4.00" in dev.status

    # The secretary stays: she is opened from the chart and is not one of its teams. What
    # she proposes later comes back as the same page.
    assert shown.secretary_button.isVisibleTo(shown)
    shown.secretary_button.click()
    view = shown.view
    assert view.id == shown.project.secretary and shown.title.text() == "Secretary"
    assert view.input.placeholderText() == "Message the secretary"
    assert not shown.secretary_button.isVisibleTo(shown)
    assert not shown.agents_section.isVisibleTo(shown)  # she is one agent: no list of them
    view.input.setPlainText("Add a legal team")
    view.send_button.click()
    await until(lambda: shown.drafts and not next(iter(shown.drafts.values())).busy)
    [page] = shown.drafts.values()
    assert page.isVisibleTo(shown) and [r.name.text() for r in page.rows] == ["legal"]
    page.discard.click()
    await until(lambda: chart(shown) == {"dev": ["K5001"], "video": ["K5002"]})
    assert not shown.drafts


async def test_waiting_for_a_proposal_shows_what_she_writes_and_can_be_cancelled(
        window, tmp_path: Path):
    shown, _ = window({"Secretary": [
        {"do": [("say", "Looking at the folder."), ("approval", "r1", "shell", "ls")]}]})
    form = shown.project_form
    form.name.setText("Studio")
    form.does.setPlainText("Run my game studio")
    form.set_folder(tmp_path / "work")
    form.create.click()
    await until(lambda: shown.drafts)
    [page] = shown.drafts.values()
    await until(lambda: page.waiting.isVisibleTo(shown))
    assert page.busy
    assert not page.start.isVisibleTo(shown) and page.stop.text() == "Cancel"
    await until(lambda: page.saying.toPlainText() == "Looking at the folder.")
    secretary = shown.project.secretary
    await until(lambda: shown.service.state(secretary).running)
    page.stop.click()  # she is stopped, and the project stays, with its chart
    await until(lambda: not shown.service.state(secretary).running)
    assert not shown.drafts and shown.chart.isVisibleTo(shown)


async def test_in_auto_a_proposal_names_each_leads_model_for_the_person_to_change(
        window, tmp_path: Path):
    shown, _ = window({
        "Secretary": [{"do": [("tool", "propose", {"teams": [
            {"name": "dev", "does": "Build it.", "model": "big-1"},
            {"name": "art", "does": "Draw it.", "model": "small-1"}]}), ("say", "Two.")]}],
        "K5001": [{"do": [("say", "On it.")]}] * 2})
    shown.service._config = FleetConfig(model_mode="auto", auto_models=("small-1", "big-1"),
                                        models={"secretary": "small-1"})
    form = shown.project_form
    form.name.setText("Studio")
    form.does.setPlainText("Run my studio")
    form.set_folder(tmp_path / "work")
    form.create.click()
    await until(lambda: shown.drafts and not next(iter(shown.drafts.values())).busy)
    [page] = shown.drafts.values()
    dev, art = page.rows
    assert [row.lead_model.currentText() for row in page.rows] == ["big-1", "small-1"]
    assert not dev.lead_model.isHidden() and dev.lead_model.count() == 2
    art.lead_model.setCurrentText("big-1")  # the person's to change before it starts
    page.start.click()
    await until(lambda: len(teams(shown)) == 2 and all(
        view.fleet is not None and view.fleet.lead is not None
        for view in teams(shown).values()))
    assert {view.name: view.fleet.lead.model for view in teams(shown).values()} == {
        "dev": "big-1", "art": "big-1"}


async def test_a_draft_that_fails_or_is_discarded_leaves_the_project(window, tmp_path: Path):
    shown, _ = window({"Secretary": [{"do": [("say", "A plan in words.")]}]})
    form = shown.project_form
    form.name.setText("Studio")
    form.does.setPlainText("Run my game studio")
    form.set_folder(tmp_path / "work")
    form.create.click()
    await until(lambda: shown.drafts and not next(iter(shown.drafts.values())).busy)
    [page] = shown.drafts.values()
    assert "No proposal" in page.note.text() and page.rows == []
    page.add_button.click()  # by hand, then
    assert len(page.rows) == 1
    page.discard.click()
    await until(lambda: shown.chart.isVisibleTo(shown))  # the project's chart, empty
    assert not shown.drafts and shown.project.name == "Studio"


async def test_the_person_adds_an_agent_and_sets_a_model_on_the_chart(window, tmp_path: Path):
    shown, harness = window({"K5001": [{"do": [("say", "On watch.")]}],
                             "Tester": [{"do": [("say", "Testing.")]}]})
    view = await start(shown, tmp_path, "Keep it healthy", name="dev", duty=True)
    shown.back_button.click()
    await until(lambda: chart(shown) == {"dev": ["K5001"]})
    [lead] = shown.chart.nodes.values()
    assert lead.add_action.isEnabled() and lead.model_action.isEnabled()
    lead.add_action.trigger()
    dialog = shown.dialog
    assert dialog.isVisible() and "under K5001" in dialog.windowTitle()
    dialog.name.setText("Tester")
    dialog.task.setPlainText("Run the tests")
    dialog.model.setText("small-1")
    dialog.add.click()
    await until(lambda: chart(shown) == {"dev": ["K5001", "Tester K4001"]})
    await until(lambda: not dialog.isVisible())  # once its process is up
    assert ("Tester", "small-1") in harness.models
    assert not view.fleet.units["K4001"].ongoing  # a task, unless Duty is chosen
    tester = shown.chart.nodes[(view.id, "K4001")]
    assert "small-1" in tester.toolTip()

    lead.model_action.trigger()
    shown.dialog.model.setText("big-1")
    shown.dialog.accept()
    await until(lambda: ("K5001", "big-1") in harness.models)
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
    [view] = teams(shown).values()
    key = view.id
    head, first = shown.chart.heads[key], shown.chart.firsts[key]
    assert "not started" in head.status.text() and not head.start.isVisibleTo(shown)

    first.click()  # the lead
    assert "lead" in shown.dialog.windowTitle()
    shown.dialog.name.setText("Chief")
    shown.dialog.task.setPlainText("Run content")
    shown.dialog.kind.duty.setChecked(True)
    shown.dialog.add.click()
    await until(lambda: chart(shown) == {"content": ["Chief K5001"]})
    chief = shown.chart.nodes[(key, "K5001")]
    assert chief.state.text() == "planned" and key not in shown.chart.firsts
    chief.add_action.trigger()
    shown.dialog.name.setText("Writer")
    shown.dialog.task.setPlainText("Write the posts")
    # The models the agent program offers are there to pick; another can be typed.
    offered = shown.dialog.model
    await until(lambda: offered.count() == 3)
    assert [offered.itemText(i) for i in range(3)] == ["", "big-1", "small-1"]
    offered.setText("small-1")
    shown.dialog.add.click()
    await until(lambda: chart(shown) == {"content": ["Chief K5001", "Writer K4001"]})
    chief.model_action.trigger()
    offered = shown.dialog.model
    await until(lambda: offered.count() == 3)
    assert offered.itemData(1, Qt.ItemDataRole.ToolTipRole) == (
        "Big One, $5 in, $25 out per million tokens")
    assert offered.text() == ""  # none of its own, and the list did not choose one
    offered.setCurrentIndex(1)
    assert offered.text() == "big-1"
    shown.dialog.accept()
    await until(lambda: view.fleet.units["K5001"].model == "big-1")
    assert not harness.models and not view.running  # nothing has run

    assert head.start.isVisibleTo(shown)
    head.start.click()
    await until(lambda: harness.models == [("Chief", "big-1"), ("Writer", "small-1")])
    await until(lambda: view.running and not head.start.isVisibleTo(shown))
    assert "Writer (K4001)" in harness.heard["Chief"][0]
    await until(lambda: chief.state.text() == "working")

    await view.stop()  # a stopped fleet is built on the same way
    await until(lambda: head.start.isVisibleTo(shown))
    chief.add_action.trigger()
    shown.dialog.name.setText("Editor")
    shown.dialog.task.setPlainText("Edit the posts")
    shown.dialog.add.click()
    await until(lambda: (key, "K4002") in shown.chart.nodes)
    assert shown.chart.nodes[(key, "K4002")].state.text() == "planned"
    head.start.click()
    await until(lambda: ("Editor", None) in harness.models)
    await view.stop()
    await until(lambda: head.start.isVisibleTo(shown))
    chief.add_action.trigger()  # nothing said about it: it has no name, only its rank
    shown.dialog.add.click()
    await until(lambda: (key, "K4003") in shown.chart.nodes)
    nameless = shown.chart.nodes[(key, "K4003")]
    assert (nameless.name.text(), nameless.rank.text()) == ("K4003", "")


async def test_a_project_is_deleted_with_its_fleets_and_not_its_work(window, tmp_path: Path):
    shown, _ = window({"K5001": [{"do": [("say", "On watch.")]}]})
    view = await start(shown, tmp_path, "Keep watch", duty=True)
    await until(lambda: shown.projects.count() == 1
                and shown.projects.item(0).data(FLEETS_ROLE) == 1)
    kept = folder(tmp_path, shown.project.id)
    (tmp_path / "work" / "kept.txt").write_text("the agents' work")
    shown.ask_delete(shown.projects.item(0))  # what a right-click on it offers
    assert "Acme" in shown.dialog.question.text() and "1 fleet" in shown.dialog.question.text()
    shown.dialog.reject()
    await asyncio.sleep(0.05)
    assert kept.exists() and view.running
    shown.ask_delete(shown.projects.item(0))
    shown.dialog.delete.click()
    await until(lambda: not kept.exists())
    await until(lambda: shown.projects.count() == 0 and shown.project_form.isVisibleTo(shown))
    assert not teams(shown) and shown.project is None and not view.running
    assert (tmp_path / "work" / "kept.txt").exists()


async def test_a_project_of_another_version_is_listed_to_be_deleted(window, tmp_path: Path):
    """It cannot be opened, and would stay on the disk for good if it were not shown."""
    old = tmp_path / "home" / "projects" / "20250101-000000"
    (old / "fleets").mkdir(parents=True)
    (old / "project.json").write_text('{"format": 1, "name": "Old one"}')
    shown, _ = window({})
    await until(lambda: shown.projects.count() == 1)
    row = shown.projects.item(0)
    assert row.text() == "Old one" and row.data(UNREADABLE_ROLE)
    assert "older" in row.data(PREVIEW_ROLE)
    assert not row.flags() & Qt.ItemFlag.ItemIsEnabled  # a click opens nothing
    assert [a.text() for a in shown.project_actions(row).actions()] == ["Delete…"]
    shown.ask_delete(row)
    assert "Old one" in shown.dialog.question.text()
    shown.dialog.delete.click()
    await until(lambda: not old.exists() and shown.projects.count() == 0)


async def test_a_fleet_is_deleted_from_the_chart(window, tmp_path: Path):
    shown, _ = window({"K5001": [{"do": [("say", "On watch.")]}]})
    first = await start(shown, tmp_path, "Keep watch", name="dev", duty=True)
    shown.back_button.click()
    second = await start(shown, tmp_path, "Keep watch", name="art", duty=True)
    shown.back_button.click()
    await until(lambda: chart(shown) == {"dev": ["K5001"], "art": ["K5002"]})
    shown.chart.heads[first.id].delete_action.trigger()
    assert "dev" in shown.dialog.question.text()
    shown.dialog.reject()
    await asyncio.sleep(0.05)
    assert folder(tmp_path, first.id).exists() and first.running
    shown.chart.heads[first.id].delete_action.trigger()
    shown.dialog.delete.click()
    await until(lambda: not folder(tmp_path, first.id).exists())
    await until(lambda: chart(shown) == {"art": ["K5002"]})
    assert list(teams(shown).values()) == [second] and second.running and not first.running
    await until(lambda: shown.projects.item(0).data(FLEETS_ROLE) == 1)
    await second.stop()


async def test_an_agent_a_fleet_and_a_project_are_renamed(window, tmp_path: Path):
    shown, _ = window(TEAM)
    view = await start(shown, tmp_path, "Tidy up", name="dev")
    shown.back_button.click()
    await until(lambda: chart(shown) == {"dev": ["K5001", "Scout K4001"]})
    key = view.id

    def rename(action, to: str) -> None:
        action.trigger()
        shown.dialog.line.setText(to)
        shown.dialog.accept()

    rename(shown.chart.nodes[(key, "K4001")].rename_action, "Tidier")  # while it runs
    await until(lambda: chart(shown) == {"dev": ["K5001", "Tidier K4001"]})
    rename(shown.chart.heads[key].rename_action, "docs")
    await until(lambda: chart(shown) == {"docs": ["K5001", "Tidier K4001"]})
    rename(shown.chart.heads[key].rename_action, "  ")  # no name: as it was
    await asyncio.sleep(0.05)
    assert view.name == "docs"
    await view.stop()
    rename(shown.chart.nodes[(key, "K5001")].rename_action, "Chief")  # and while it does not
    await until(lambda: chart(shown) == {"docs": ["Chief K5001", "Tidier K4001"]})
    saved = Fleet.load(folder(tmp_path, view.id))
    assert (saved.name, [u.name for u in saved.units.values()]) == ("docs", ["Chief", "Tidier"])

    shown.ask_rename(shown.projects.item(0))  # what a right-click on it offers
    assert shown.dialog.line.text() == "Acme"
    shown.dialog.line.setText("Globex")
    shown.dialog.accept()
    await until(lambda: shown.projects.item(0).text() == "Globex")
    assert shown.title.text() == "Globex" and shown.project.name == "Globex"
    assert project.load(folder(tmp_path, shown.project.id)).name == "Globex"


async def test_the_chosen_agent_is_the_one_messaged(window, tmp_path: Path):
    shown, harness = window({
        "K5001": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Map the docs"})]}],
        "Scout": [{"do": [("say", "Mapping.")]},
                  {"when": "Skip the drafts", "do": [("say", "Skipping them.")]}]})
    view = await start(shown, tmp_path, "Tidy up", duty=True)
    await until(lambda: len(agents(shown)) == 2)
    assert view.input.placeholderText() == "Message the lead"
    shown.agents.setCurrentItem(shown.agents.topLevelItem(0).child(0))
    assert view.input.placeholderText() == "Message Scout"
    # Its page is a conversation with it: its task from its lead, then what it wrote,
    # without its name on every line.
    await until(lambda: [type(e).__name__ for e in view.entries if e.isVisibleTo(shown)]
                == ["InstructionCard", "SaidEntry"])
    seen = [e for e in view.entries if e.isVisibleTo(shown)]
    assert seen[0].header.text() == "K5001 → Scout · task"
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
    shown, harness = window({"K5001": [{"do": [("say", "On watch.")]}]})
    view = await start(shown, tmp_path, "Keep watch", duty=True)
    await until(lambda: harness.heard.get("K5001"))
    note = tmp_path / "note.txt"
    note.write_text("read me")
    assert not view.attachments.isVisibleTo(shown)
    view.input.attached.emit([str(note)])  # dropped or pasted into the box
    assert view.attachments.isVisibleTo(shown) and view.attachments.paths() == [str(note)]
    view.send_button.click()  # files alone are a message
    await until(lambda: harness.files == [("K5001", [str(note)], False)])
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


async def test_a_project_makes_its_folder_in_the_projects_folder(tmp_path: Path,
                                                               qapp: QApplication):
    """With a projects folder set, a project needs no folder chosen: one named after it
    is made there. One that is chosen is used as before."""
    shown = FleetWindow(LocalService(tmp_path / "home", harness=FakeHarness({})))
    shown.show()
    try:
        form = shown.project_form
        form.name.setText("Sea Game")
        form.create.click()
        assert "Choose a folder" in form.warning.text()  # none is set: as it was
        shown.gear_button.click()
        await until(lambda: shown.dialog is not None and shown.dialog.isVisible())
        shown.dialog.browse.click()  # a folder is picked, or its path typed
        shown.dialog.picker.fileSelected.emit(str(tmp_path / "all"))
        shown.dialog.picker.reject()
        assert shown.dialog.projects.text() == str(tmp_path / "all")
        shown.dialog.save.click()
        await until(lambda: form.base == tmp_path / "all")
        again = FleetWindow(shown.service)  # a window that opens later knows it at once
        await until(lambda: again.project_form.base == tmp_path / "all")
        again.deleteLater()
        assert form.folder_button.text().strip() == "all/Sea-Game"
        form.name.setText("Sea Game 2")
        assert form.folder_button.text().strip() == "all/Sea-Game-2"
        form.create.click()
        await until(lambda: shown.project is not None)
        assert shown.project.work == str(tmp_path / "all" / "Sea-Game-2")
        assert (tmp_path / "all" / "Sea-Game-2").is_dir()

        # A folder of that name with files in it is not taken without asking.
        shown.new_project()
        await until(lambda: form.base == tmp_path / "all")
        (tmp_path / "all" / "Sea-Game-2" / "notes.txt").write_text("mine")
        form.name.setText("Sea Game 2")
        form.create.click()
        asked = shown.dialog
        assert "already exists" in asked.text() and shown.project is None
        asked.button(asked.StandardButton.Cancel).click()
        await asyncio.sleep(0.05)
        assert shown.project is None
        (tmp_path / "all" / "Empty").mkdir()  # an empty one is as good as new
        form.name.setText("Sea Game 2")
        form.create.click()
        shown.dialog.button(shown.dialog.StandardButton.Yes).click()
        await until(lambda: shown.project is not None)
        assert shown.project.work == str(tmp_path / "all" / "Sea-Game-2")

        shown.new_project()
        await until(lambda: form.base == tmp_path / "all")
        form.name.setText("Empty")
        form.create.click()
        await until(lambda: shown.project is not None and shown.project.name == "Empty")
        assert shown.project.work == str(tmp_path / "all" / "Empty")

        shown.new_project()
        await until(lambda: form.base == tmp_path / "all")
        form.name.setText("Elsewhere")
        (tmp_path / "own").mkdir()
        form.set_folder(tmp_path / "own")  # a folder that is chosen wins
        assert form.folder_button.text().strip() == "own"
        form.create.click()
        await until(lambda: shown.project is not None and shown.project.name == "Elsewhere")
        assert shown.project.work == str(tmp_path / "own")
        assert not (tmp_path / "all" / "Elsewhere").exists()
    finally:
        await shown.shut()


async def test_settings_are_reached_from_the_profile_corner(tmp_path: Path, qapp: QApplication):
    shown = FleetWindow(LocalService(tmp_path / "home", harness=FakeHarness({})))  # its home's
    shown.show()
    try:
        assert shown.profile_button.text().strip() == getpass.getuser()
        assert [a.text() for a in shown.profile_button.menu().actions() if a.text()][1:] == [
            "Settings", "Add to App Menu", "Quit"]
        # The menu at the top is about KFM as it runs.
        assert [a.text() for a in shown.menu_button.menu().actions() if a.text()] == [
            "Theme", "Restart KFM", "Stop KFM"]
        shown.gear_button.click()
        await until(lambda: shown.dialog is not None and shown.dialog.isVisible())
        dialog = shown.dialog
        assert dialog.budget.value() == 10.0
        # Its own header in place of the desktop's title bar, with a button that closes it.
        assert dialog.windowFlags() & Qt.WindowType.FramelessWindowHint
        assert dialog.close_button.isVisibleTo(dialog)
        # The models the agent program offers are there to pick; another can be typed.
        field = dialog.models["K5"]
        await until(lambda: field.count() == 3)
        assert [field.itemText(i) for i in range(3)] == ["", "big-1", "small-1"]
        assert field.itemData(1, Qt.ItemDataRole.ToolTipRole) == (
            "Big One, $5 in, $25 out per million tokens")
        # Manual, as it starts: a model for each rank, and no list of allowed ones.
        assert dialog.mode.currentText() == "Manual"
        assert dialog.models["K3"].isVisibleTo(dialog) and not dialog.auto.isVisibleTo(dialog)
        assert field.currentText() == "" and field.lineEdit().placeholderText() == "Default"
        # The model of every rank that has none of its own: each such rank says which.
        every = dialog.models["default"]
        assert list(dialog.models) == ["secretary", "default", "K5", "K4", "K3", "K2", "K1"]
        assert dialog.models["secretary"].lineEdit().placeholderText() == "As K5"
        assert every.lineEdit().placeholderText() == "Kadence's model"
        every.setCurrentText("small-1")
        assert field.lineEdit().placeholderText() == "Default: small-1"
        every.setCurrentText("")
        assert field.lineEdit().placeholderText() == "Default"
        every.setCurrentIndex(2)
        field.setCurrentIndex(1)
        dialog.models["K4"].setCurrentText("my-own-model")
        dialog.budget.setValue(25.0)
        dialog.agents.setValue(0)  # no fleet has no agents
        dialog.save.click()
        await until(lambda: "above zero" in dialog.warning.text())
        assert dialog.isVisible()
        dialog.agents.setValue(6)
        dialog.save.click()
        await until(lambda: not dialog.isVisible())
        saved = load(tmp_path / "home" / "fleet.toml")
        assert (saved.models, saved.budget_usd, saved.max_units) == (
            {"default": "small-1", "K5": "big-1", "K4": "my-own-model"}, 25.0, 6)
        assert (saved.model_mode, saved.auto_models) == ("manual", ())
        assert saved.permissions == "allow_workspace"  # as it was: left alone
        await until(lambda: shown.start_form.budget.value() == 25.0)  # what a new fleet gets
        shown.gear_button.click()
        await until(lambda: shown.dialog is not dialog and shown.dialog.isVisible())
        assert shown.dialog.models["K5"].currentText() == "big-1"
        assert shown.dialog.models["K4"].currentText() == "my-own-model"
        assert shown.dialog.models["default"].currentText() == "small-1"
        assert shown.dialog.models["K3"].lineEdit().placeholderText() == "Default: small-1"
        # What agents may do without asking: Kadence's levels, by Kadence's words.
        levels = shown.dialog.permissions
        assert levels.currentData() == "allow_workspace"
        assert [levels.itemData(i) for i in range(levels.count())] == [
            "auto", "allow_workspace", "allow_read_only", "ask", "deny"]
        assert [levels.itemText(i) for i in range(levels.count())] == [
            "Auto", "Work folder", "Read only", "Ask first", "No tools"]
        assert levels.itemData(0, Qt.ItemDataRole.ToolTipRole) == (
            "Decide automatically, ask me only when unsure")
        levels.setCurrentIndex(0)
        again = shown.dialog
        # Auto: only the models allowed and the secretary's are there to set, and the
        # ranks are gone; whoever starts an agent names its model.
        await until(lambda: again.models["K5"].count() == 3)
        again.mode.setCurrentIndex(again.mode.findText("Auto"))
        assert again.auto.isVisibleTo(again) and again.models["secretary"].isVisibleTo(again)
        assert not any(again.models[key].isVisibleTo(again)
                       for key in ("default", "K5", "K4", "K3", "K2", "K1"))
        assert again.auto.text() == "None"
        again.save.click()  # nothing is allowed yet
        await until(lambda: "at least one model" in again.warning.text())
        ticks = {a.text(): a for a in again.auto.menu().actions()}
        assert list(ticks) == ["big-1", "small-1"]
        ticks["small-1"].setChecked(True)
        ticks["big-1"].setChecked(True)
        assert again.auto.text() == "small-1, big-1"
        again.models["secretary"].setCurrentText("big-1")
        again.save.click()
        await until(lambda: not again.isVisible())
        kept = load(tmp_path / "home" / "fleet.toml")
        assert kept.permissions == "auto"
        assert (kept.model_mode, kept.auto_models) == ("auto", ("small-1", "big-1"))
        assert kept.models["secretary"] == "big-1" and kept.models["K5"] == "big-1"
        # The form for a fleet added by hand has its lead's model to choose, the
        # secretary's to begin with.
        await until(lambda: shown.start_form.lead_model.count() == 2)
        assert not shown.start_form.lead_model.isHidden()
        assert shown.start_form.lead_model.currentText() == "big-1"
    finally:
        await shown.shut()


async def test_an_agents_task_is_changed_and_an_agent_is_removed_on_the_chart(
        window, tmp_path: Path):
    shown, harness = window({
        "K5001": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Map the docs"})]}],
        "Scout": [{"do": [("say", "Mapping.")]}]})
    view = await start(shown, tmp_path, "Tidy up", duty=True)
    shown.back_button.click()
    await until(lambda: chart(shown) == {"dev": ["K5001", "Scout K4001"]})
    key = view.id
    scout = shown.chart.nodes[(key, "K4001")]
    scout.task_action.trigger()
    assert shown.dialog.task.toPlainText() == "Map the docs"
    assert shown.dialog.kind.task_kind.isChecked()  # what it is now
    shown.dialog.task.setPlainText("Map the code")
    shown.dialog.kind.duty.setChecked(True)
    shown.dialog.save.click()
    await until(lambda: harness.instructed and "Map the code" in harness.instructed[0][1])
    assert harness.kinds == [("Scout", True)] and view.fleet.units["K4001"].ongoing
    assert "Map the code" in scout.toolTip()

    assert any("K4001" in entry.concerns for entry in view.entries)
    scout.remove_action.trigger()
    assert "Scout" in shown.dialog.question.text()
    shown.dialog.delete.click()
    await until(lambda: chart(shown) == {"dev": ["K5001"]})
    assert list(view.fleet.units) == ["K5001"]
    assert not any("K4001" in entry.concerns for entry in view.entries)  # what named it went
    shown.chart.heads[key].clicked.emit()  # its row in the agents list went with it
    await until(lambda: agents(shown) == [(0, "K5001", "working")])
    shown.back_button.click()

    await view.stop()  # in a stopped fleet too: it works on it when the fleet starts
    lead = shown.chart.nodes[(key, "K5001")]
    await until(lambda: lead.task_action.isEnabled() and lead.remove_action.isEnabled())
    lead.task_action.trigger()
    shown.dialog.task.setPlainText("Tidy the code")
    shown.dialog.save.click()
    await until(lambda: view.fleet.units["K5001"].task == "Tidy the code")
    assert not view.running and len(harness.instructed) == 1  # nobody was started for it
    lead.remove_action.trigger()
    shown.dialog.delete.click()
    await until(lambda: chart(shown) == {"dev": []} and key in shown.chart.firsts)
    assert not Fleet.load(folder(tmp_path, view.id)).units


async def test_a_page_shows_each_event_once_where_its_history_and_what_happens_meet(
        qapp: QApplication):
    fleet = Fleet()
    fleet.add("Lead", "Keep watch", None)
    fetched = asyncio.Event()

    class Stub:  # of the service, what a fleet's page asks of it
        def fleet(self, fleet_id: str) -> Fleet:
            return fleet

        def state(self, fleet_id: str) -> FleetState:
            return FleetState(running=True)

        async def watch(self, fleet_id: str) -> None:
            pass

        async def history(self, fleet_id: str) -> tuple[list[Any], int]:
            await fetched.wait()
            return [Said("K5001", "one"), Said("K5001", "same")], 2

    view = FleetView(Stub(), "p/f")
    loading = view.load()
    view.happened(Said("K5001", "same"), 2)  # while the history is fetched, and in it
    view.happened(Said("K5001", "same"), 3)  # said again: the history lacks it
    fetched.set()
    await loading
    assert view.load() is loading and view.loaded
    view.happened(Said("K5001", "later"), 4)
    assert [entry.body.toPlainText() for entry in view.entries] == [
        "one", "same", "same", "later"]
    view.deleteLater()


async def test_a_fleet_outlives_the_window_that_started_it(tmp_path: Path, qapp: QApplication):
    """The window on the service `kfm serve` runs: closing it lets go of the fleet, which
    keeps running, and another window finds it with what happened meanwhile."""
    harness = FakeHarness({"K5001": [{"do": [("say", "On watch.")]},
                                     {"when": "anything new", "do": [("say", "All quiet.")]}]})
    (tmp_path / "work").mkdir()
    home, socket = tmp_path / "home", tmp_path / "s"
    owner = LocalService(home, harness=harness, config=FleetConfig())
    serving = asyncio.ensure_future(Server(owner, socket, idle_s=0.3).run())
    await until(socket.exists)

    first = FleetWindow(await RemoteService.connect(home, socket=socket, start=False))
    first.show()
    view = await start(first, tmp_path, "Keep watch", duty=True)
    await until(lambda: "running" in first.status.text())
    await until(lambda: any(isinstance(e, SaidEntry) for e in view.entries))
    await first.shut()
    assert owner.state(view.id).running  # nobody is watching, and it runs

    again = FleetWindow(await RemoteService.connect(home, socket=socket, start=False))
    again.show()
    try:
        await until(lambda: chart(again) == {"dev": ["K5001"]})
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
    assert shown.project.work == str(tmp_path / "work") and form.folder is None


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


async def test_kfm_is_restarted_from_the_menu(window, tmp_path: Path):
    """The service that holds the fleets is ended, and the window started again on a new
    one: what an update needs, since the service outlives the window."""
    shown, _ = window({"K5001": [{"do": [("say", "On watch.")]}]})
    view = await start(shown, tmp_path, "Keep watch", duty=True)
    await until(lambda: view.running)
    again: list[bool] = []
    shown.relaunch = lambda: again.append(True)
    [restart] = [a for a in shown.menu_button.menu().actions() if a.text() == "Restart KFM"]
    restart.trigger()
    assert "stop" in shown.dialog.text() and "resumed" in shown.dialog.text()
    shown.dialog.button(shown.dialog.StandardButton.Yes).click()
    await until(lambda: again == [True])
    assert not shown.service.state(view.id).running


async def test_the_theme_is_chosen_in_the_menu_and_kept(tmp_path: Path, qapp: QApplication):
    """Light, dark or the desktop's: kept in a file, and the window starts again in it
    (the service and its fleets are not touched)."""
    look = tmp_path / "window.json"
    assert saved_theme(look) == "system"  # none kept: the desktop's
    shown = FleetWindow(LocalService(tmp_path / "home", harness=FakeHarness({})), look=look)
    shown.show()
    try:
        again: list[bool] = []
        shown.relaunch = lambda: again.append(True)
        [themes] = [a.menu() for a in shown.menu_button.menu().actions() if a.text() == "Theme"]
        assert [(a.text(), a.isChecked()) for a in themes.actions()] == [
            ("System", True), ("Light", False), ("Dark", False)]
        themes.actions()[1].trigger()
        await until(lambda: again == [True])
        assert saved_theme(look) == "light"
        look.write_text("not json")
        assert saved_theme(look) == "system"
    finally:
        await shown.shut()


async def test_kfm_is_stopped_from_the_menu(window, tmp_path: Path):
    """The service is ended and the window closed: closing the window alone leaves the
    fleets running."""
    shown, _ = window({"K5001": [{"do": [("say", "On watch.")]}]})
    view = await start(shown, tmp_path, "Keep watch", duty=True)
    await until(lambda: view.running)
    service = shown.service
    [stop] = [a for a in shown.menu_button.menu().actions() if a.text() == "Stop KFM"]
    stop.trigger()
    assert "stop" in shown.dialog.text() and "resumed" in shown.dialog.text()
    shown.dialog.button(shown.dialog.StandardButton.Yes).click()
    await until(shown.closed.is_set)
    assert not service.state(view.id).running


async def test_a_window_on_an_older_service_offers_to_restart(window, tmp_path: Path):
    shown, _ = window({})
    shown.offer_restart()
    assert shown.dialog is None  # the service is this code: nothing to say
    shown.service.stale = True
    again: list[bool] = []
    shown.relaunch = lambda: again.append(True)
    shown.offer_restart()
    assert "updated" in shown.dialog.text() and "resumed" in shown.dialog.text()
    shown.dialog.button(shown.dialog.StandardButton.Yes).click()
    await until(lambda: again == [True])


async def test_the_agents_list_follows_the_fleet_and_keeps_the_one_chosen(window,
                                                                         tmp_path: Path):
    """It is built anew when agents come or go and when another fleet is opened, and the
    agent that was chosen stays chosen while it is there."""
    shown, _ = window({
        **{lead: [{"when": task, "do": [
            ("tool", "delegate", {"name": "Scout", "task": "Look"}),
            ("tool", "delegate", {"name": "Scribe", "task": "Write"})]}]
           for lead, task in (("K5001", "Build it"), ("K5002", "Draw it"))},
        "Scout": [{"do": [("tool", "delegate", {"name": "Runner", "task": "Run"})]}] * 2,
        "Scribe": [{"do": [("say", "Writing.")]}] * 2,
        "Runner": [{"do": [("say", "Running.")]}] * 2})
    dev = await start(shown, tmp_path, "Build it", name="dev", duty=True)
    await until(lambda: [name for _, name, _ in agents(shown)] == [
        "K5001", "Scout  K4001", "Runner  K3001", "Scribe  K4002"])
    scribe = next(u.id for u in dev.fleet.units.values() if u.name == "Scribe")
    scout = next(u.id for u in dev.fleet.units.values() if u.name == "Scout")
    shown.agents.setCurrentItem(shown._agent_row(scribe))
    assert dev.shown == scribe
    await shown.service.remove_agent(dev.id, scout)  # with the one under it
    await until(lambda: [name for _, name, _ in agents(shown)] == ["K5001", "Scribe  K4002"])
    assert dev.shown == scribe and shown.agents.currentItem().text(0) == "Scribe  K4002"
    await shown.service.remove_agent(dev.id, scribe)  # the one chosen goes: all is shown
    await until(lambda: [name for _, name, _ in agents(shown)] == ["K5001"])
    assert dev.shown is None

    shown.back_button.click()
    art = await start(shown, tmp_path, "Draw it", name="art", duty=True)
    await until(lambda: len(agents(shown)) == 4)  # the other fleet's, built the same way
    for _ in range(5):  # back and forth: the list is cleared and built each time
        shown.open_fleet(dev)
        assert [name for _, name, _ in agents(shown)] == ["K5001"]
        shown.open_fleet(art)
        assert len(agents(shown)) == 4


async def test_what_an_agent_does_shows_under_the_feed_as_it_does_it(window, tmp_path: Path):
    shown, _ = window({
        "K5001": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Look"}),
                          ("think", "Who should look? "), ("think", "Scout."),
                          ("doing", "Run the tests: shell uv run pytest"),
                          ("write", "Tests "), ("write", "pass."), ("wait", 0.6),
                          ("say", "Tests pass."), ("wait", 0.4)]}],
        "Scout": [{"do": [("think", "Looking around."), ("wait", 1.2)]}]})
    view = await start(shown, tmp_path, "Build it", name="dev", duty=True)
    await until(lambda: "K5001" in view.lives and view.lives["K5001"].writing.text() == "Tests pass.")
    lead = view.lives["K5001"]
    assert lead.isVisibleTo(shown) and lead.thinking.text() == "Who should look? Scout."
    assert lead.doing.text() == "Run the tests: shell uv run pytest"
    assert lead not in view.entries  # no part of the feed
    await until(lambda: "K4001" in view.lives and view.lives["K4001"].thinking.text())
    said = [e for e in view.entries if isinstance(e, SaidEntry)]
    await until(lambda: [e for e in view.entries if isinstance(e, SaidEntry)] != said)
    # The message is whole and in the feed: what was being written is gone from under it,
    # and what is new in the feed stands above what agents are doing now.
    await until(lambda: lead.writing.text() == "")
    layout = lead.parentWidget().layout()
    assert layout.indexOf(view.entries[-1]) < layout.indexOf(view.lives["K4001"])
    scout = next(u.id for u in view.fleet.units.values() if u.name == "Scout")
    shown.agents.setCurrentItem(shown._agent_row(scout))  # an agent's page: its own only
    assert view.lives[scout].isVisibleTo(shown) and not view.lives[scout].header.isVisibleTo(shown)
    assert not lead.isVisibleTo(shown)
    await until(lambda: lead.empty and view.lives[scout].empty, timeout=5)  # their turns end


async def test_drafting_shows_what_the_secretary_does_and_thinks(window, tmp_path: Path):
    shown, _ = window({"Secretary": [{"do": [
        ("think", "A game needs code and art. "),
        ("doing", "See what is there: list_files"), ("wait", 0.5), ("doing", None),
        ("think", "The folder is empty."), ("wait", 0.5), ("approval", "r1", "shell", "ls")]}]})
    form = shown.project_form
    form.name.setText("Studio")
    form.does.setPlainText("Make a game")
    form.set_folder(tmp_path / "work")
    form.create.click()
    await until(lambda: shown.drafts)
    [page] = shown.drafts.values()
    await until(lambda: "See what is there: list_files" in page.saying.toPlainText())
    await until(lambda: page.saying.toPlainText().endswith("The folder is empty."))
    assert "A game needs code and art." in page.saying.toPlainText()
    page.stop.click()


async def test_what_waits_for_the_person_shows_wherever_they_are(window, tmp_path: Path):
    """An approval asked for in one fleet is answered from the chart, or from another
    fleet's page; a lead's question leads to its fleet."""
    shown, harness = window({
        "K5001": [{"when": "Build it", "do": [
                      ("approval", "r1", "shell", "uv run pytest"), ("say", "Ran them.")]}],
        "K5002": [{"when": "Draw it", "do": [("ask", "Which palette?")]}]})
    dev = await start(shown, tmp_path, "Build it", name="dev", duty=True)
    shown.back_button.click()
    await until(lambda: shown.attention.isVisibleTo(shown) and len(shown.attention.rows) == 1)
    [row] = shown.attention.rows
    assert row.text.text() == "dev › K5001 wants to run shell uv run pytest"
    assert [b.text() for b in row.buttons] == ["Allow", "Always allow", "Deny"]
    assert chart(shown) == {"dev": ["K5001"]} and shown.chart.isVisibleTo(shown)
    node = shown.chart.nodes[(dev.id, "K5001")]
    assert node.state.text() == "asking"
    await until(lambda: "1 waiting for you" in shown.projects.item(0).data(PREVIEW_ROLE))

    art = await start(shown, tmp_path, "Draw it", name="art", duty=True)  # another's page
    await until(lambda: art.fleet.lead.state == "waiting")
    assert shown.view is art and len(shown.attention.rows) == 1  # its own question is here
    shown.attention.rows[0].buttons[1].click()  # Always allow
    await until(lambda: harness.approved == [("r1", True)] and harness.always == ["r1"])
    await until(lambda: not shown.attention.isVisibleTo(shown))
    shown.back_button.click()
    await until(lambda: len(shown.attention.rows) == 1)  # on the chart: art's lead asks
    [row] = shown.attention.rows
    assert row.text.text() == "art › K5002 asks: Which palette?"
    row.buttons[0].click()
    assert shown.view is art


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


async def test_the_window_has_rounded_corners_and_a_shadow_unless_maximized(
        window, qapp: QApplication):
    from kadence.frontends.desktop.window import RoundedCorners

    shown, _ = window({})
    shown.resize(1100, 720)
    shown.show()
    qapp.processEvents()
    s = RoundedCorners.SHADOW  # the margin around the frame, where the shadow is drawn
    assert shown.contentsRect().getRect() == (s, s, 1100 - 2 * s, 720 - 2 * s)
    image = shown.grab().toImage()
    for x, y in ((0, 0), (1099, 0), (0, 719), (1099, 719)):
        assert image.pixelColor(x, y).alpha() == 0  # the desktop shows through
    assert 0 < image.pixelColor(550, 721 - s).alpha() < 255  # the shadow under the frame
    assert image.pixelColor(550, 360).alpha() == 255
    assert image.pixelColor(1080 - s, 712 - s).alpha() == 255  # the window's own background
    shown.showMaximized()
    qapp.processEvents()
    assert shown.grab().toImage().pixelColor(0, 0).alpha() == 255
    assert shown.contentsRect() == shown.rect()


async def test_a_fleets_feed_is_at_its_end_when_opened_and_when_an_agent_is_chosen(
        window, tmp_path: Path):
    long = "\n\n".join(f"Line {n} of what was found." for n in range(12))
    shown, _ = window({"K5001": [{"do": [("say", long)] * 8}]})
    view = await start(shown, tmp_path, "Look at the art", duty=True)
    await until(lambda: sum(isinstance(e, SaidEntry) for e in view.entries) == 8)
    bar = view.scroll.verticalScrollBar()
    await until(lambda: bar.maximum() > 0 and bar.value() >= bar.maximum() - 4)
    bar.setValue(0)  # reading the beginning: it is left there as nothing new comes
    await until(lambda: not view._at_end)
    shown.agents.setCurrentItem(shown._agent_row(view.fleet.lead.id))
    await until(lambda: bar.value() >= bar.maximum() - 4)  # one agent's: from its latest
    bar.setValue(0)
    shown.back_button.click()
    await until(lambda: shown.chart.isVisibleTo(shown))
    shown.open_fleet(view)
    await until(lambda: bar.maximum() > 0 and bar.value() >= bar.maximum() - 4)


async def test_fleets_an_update_stopped_are_resumed_from_the_strip(window, tmp_path: Path):
    shown, harness = window({"K5001": [{"do": [("say", "On watch.")]}] * 2})
    view = await start(shown, tmp_path, "Keep watch", duty=True)
    await until(lambda: "K5001" in harness.heard)
    await view.stop()
    await until(lambda: not view.running)
    shown._interrupted = [view.id]  # as a service that replaced an older one says
    shown.back_button.click()
    await until(lambda: len(shown.attention.rows) == 1)
    [row] = shown.attention.rows
    assert row.text.text() == "KFM was updated · 1 fleet stopped"
    assert [b.text() for b in row.buttons] == ["Resume", "Dismiss"]
    row.buttons[0].click()
    await until(lambda: view.running and not shown.attention.rows)


@pytest.fixture
async def kadence_window(tmp_path: Path, qapp: QApplication, monkeypatch: pytest.MonkeyPatch):
    """make(scripts) -> a window on a service whose agents are real Kadence processes
    with a scripted model (tests/fakecore.py)."""
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    made: list[FleetWindow] = []

    def make(scripts: dict[str, list[dict[str, Any]]]) -> FleetWindow:
        base = tmp_path / "kadence.toml"
        base.write_text(tomli_w.dumps({
            "core": {"factory": "tests.fakecore:factory", "options": {"scripts": scripts}},
            "runtime": {"model": "fake-1"}}))
        (tmp_path / "work").mkdir(exist_ok=True)
        config = replace(FleetConfig(), harness_options={"config": str(base)},
                         check_in_s=(0.05, 60.0))
        shown = FleetWindow(LocalService(tmp_path / "home", config=config))
        shown.resize(1100, 720)
        shown.show()
        made.append(shown)
        return shown

    yield make
    for shown in made:
        await shown.shut()


async def test_an_agents_page_is_its_kadence_conversation(kadence_window, tmp_path: Path):
    """Its turns as Kadence draws them, and a card it ends a turn with, whose buttons
    answer it through KFM."""
    shown = kadence_window({"K5001": [
        {"calls": [{"name": "wrap_up", "arguments": {
            "recap": "The art is listed.", "question": "Order more art?",
            "options": [{"label": "Yes, order it"}, {"label": "Not now"}]}}]},
        {"when": "Yes, order it", "text": "Ordered."}]})
    view = await start(shown, tmp_path, "Look at the art", duty=True)
    await until(lambda: view.fleet is not None and view.fleet.lead is not None
                and view.fleet.lead.session, timeout=30)
    assert view.conversation is None and view.pages.currentWidget() is view.scroll
    shown.agents.setCurrentItem(shown._agent_row(view.fleet.lead.id))
    await until(lambda: view.conversation is not None, timeout=30)
    page = view.conversation
    assert view.pages.currentWidget() is page.view
    await until(lambda: page.model.card is not None, timeout=30)
    def shown_card() -> WrapUpCard | None:
        item = page.view.prompts.itemAt(0)
        card = item.widget() if item is not None else None
        return card if isinstance(card, WrapUpCard) and card.isVisibleTo(shown) else None

    await until(shown_card)
    card = shown_card()
    yes = next(b for b in card.findChildren(QPushButton) if b.text() == "Yes, order it")
    yes.click()
    await until(lambda: any(isinstance(w, AssistantBubble) and "Ordered." in w.item.text
                            for w in page.view._widgets.values()), timeout=30)
    history, _ = await shown.service.history(view.id)
    assert "Yes, order it" in [e.text for e in history if isinstance(e, Told)]  # through KFM

    shown.show_all.click()  # back to the whole fleet: its feed
    await until(lambda: view.conversation is None)
    assert view.pages.currentWidget() is view.scroll


async def test_an_agents_page_opens_at_the_end_of_its_conversation(kadence_window,
                                                                  tmp_path: Path):
    long = "\n\n".join(f"Line {n} of what was found." for n in range(12))
    shown = kadence_window({"K5001": [{"text": long}] * 6})
    view = await start(shown, tmp_path, "Look at the art", duty=True)
    for n in range(1, 6):  # more than a page of it
        await until(lambda n=n: sum(isinstance(e, SaidEntry) for e in view.entries) == n,
                    timeout=30)
        await shown.service.say(view.id, f"More {n}")
    await until(lambda: sum(isinstance(e, SaidEntry) for e in view.entries) == 6, timeout=30)
    shown.agents.setCurrentItem(shown._agent_row(view.fleet.lead.id))
    await until(lambda: view.conversation is not None, timeout=30)
    bar = view.conversation.view.verticalScrollBar()
    await until(lambda: bar.maximum() > 0, timeout=30)
    await asyncio.sleep(0.3)
    assert bar.value() >= bar.maximum() - 4


async def test_a_conversation_that_grew_off_screen_is_at_its_end_when_opened(kadence_window,
                                                                            tmp_path: Path):
    long = "\n\n".join(f"Line {n} of what was found." for n in range(12))
    shown = kadence_window({"K5001": [{"text": long}] * 8})
    view = await start(shown, tmp_path, "Look at the art", duty=True)
    await until(lambda: any(isinstance(e, SaidEntry) for e in view.entries), timeout=30)
    shown.agents.setCurrentItem(shown._agent_row(view.fleet.lead.id))
    await until(lambda: view.conversation is not None, timeout=30)
    page = view.conversation
    shown.back_button.click()  # to the chart: the page is not on screen
    await until(lambda: shown.chart.isVisibleTo(shown))
    for n in range(1, 7):
        before = len(page.model.items)
        await shown.service.say(view.id, f"More {n}", view.fleet.lead.id)
        await until(lambda before=before: len(page.model.items) >= before + 2
                    and not page.model.busy, timeout=30)
    shown.open_fleet(view, view.fleet.lead.id)
    await until(lambda: view.conversation is not None and view.conversation.view.isVisibleTo(shown))
    bar = view.conversation.view.verticalScrollBar()
    await until(lambda: bar.maximum() > 0, timeout=30)
    for _ in range(20):
        QApplication.processEvents()
        await asyncio.sleep(0.02)
    assert bar.value() >= bar.maximum() - 4
