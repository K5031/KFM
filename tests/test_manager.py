"""A fleet end to end: real Kayden units, run headless, with a scripted model
(tests/fakecore.py)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import tomli_w

from kfm.config import FleetConfig
from kfm.fleet import Fleet
from kfm.manager import Approval, Changed, Delivered, FleetEvent, Manager, Notice

REPO = Path(__file__).resolve().parents[1]


def call(tool: str, /, **arguments: Any) -> dict[str, Any]:
    return {"calls": [{"name": tool, "arguments": arguments}]}


def finish(report: str, when: str = "") -> dict[str, Any]:
    return {"when": when, **call("finish_goal", report=report)}


async def until(predicate: Callable[[], Any], timeout: float = 30.0) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.02)

    await asyncio.wait_for(poll(), timeout)


@pytest.fixture
async def fleet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """make(scripts, **settings) -> (manager, events): a started manager whose units run
    the scripted model."""
    monkeypatch.setenv("PYTHONPATH", str(REPO))
    managers: list[Manager] = []

    async def make(scripts: dict[str, list[dict[str, Any]]], **settings: Any
                   ) -> tuple[Manager, list[FleetEvent]]:
        base = tmp_path / "kayden.toml"
        base.write_text(tomli_w.dumps({
            "core": {"factory": "tests.fakecore:factory", "options": {"scripts": scripts}},
            "runtime": {"model": "fake-1"}}))
        work = tmp_path / "work"
        work.mkdir(exist_ok=True)
        config = replace(FleetConfig(), kayden_config=base,
                         **{"check_in_s": (0.05, 60.0), **settings})
        events: list[FleetEvent] = []
        manager = Manager(config, tmp_path / "fleet", work, on_event=events.append)
        managers.append(manager)
        await manager.start()
        return manager, events

    yield make
    for manager in managers:
        await manager.aclose()


def delivered(events: list[FleetEvent]) -> list[tuple[str, str | None, str, str]]:
    return [(e.unit.name, e.to.name if e.to else None, e.kind, e.text)
            for e in events if isinstance(e, Delivered)]


async def test_a_task_is_delegated_and_the_reports_travel_up(fleet, tmp_path: Path):
    manager, events = await fleet({
        "Lead": [call("mcp__fleet__delegate", name="Scout", task="Count the files"),
                 finish("Scout counted 3 files.", when="[Report from Scout (K4, u2)] 3")],
        "Scout": [finish("3 files")]})
    lead = await manager.lead("Find out how many files there are")
    await asyncio.wait_for(manager.finished.wait(), 30)
    scout = manager.fleet.units["u2"]
    assert (lead.rank, scout.rank, scout.supervisor) == (5, 4, "u1")
    assert [e.state for e in events if isinstance(e, Changed) and e.unit is lead] == [
        "starting", "working", "done"]
    assert delivered(events) == [("Scout", "Lead", "report", "3 files"),
                                 ("Lead", None, "report", "Scout counted 3 files.")]
    assert (lead.state, scout.state) == ("done", "done")
    assert lead.spent_usd > 0 and manager.fleet.spent_usd > lead.spent_usd
    await manager.aclose()
    saved = Fleet.load(tmp_path / "fleet")
    assert [(u.name, u.state) for u in saved.units.values()] == [("Lead", "done"),
                                                                 ("Scout", "done")]


async def test_a_question_goes_to_the_supervisor_and_its_answer_back(fleet):
    manager, events = await fleet({
        "Lead": [call("mcp__fleet__delegate", name="Scout", task="Tidy the docs"),
                 {"when": "[Question from Scout", **call("mcp__fleet__message", agent="u2",
                                                         text="Use docs/")},
                 finish("Docs are tidy.", when="[Report from Scout")],
        "Scout": [call("ask_for_help", question="Which folder?"),
                  finish("Tidied docs/", when="your supervisor] Use docs/")]})
    await manager.lead("Tidy up")
    await asyncio.wait_for(manager.finished.wait(), 30)
    assert delivered(events) == [("Scout", "Lead", "question", "Which folder?"),
                                 ("Scout", "Lead", "report", "Tidied docs/"),
                                 ("Lead", None, "report", "Docs are tidy.")]
    states = [e.state for e in events if isinstance(e, Changed) and e.unit.name == "Scout"]
    assert "waiting" in states


async def test_the_lowest_rank_is_told_it_cannot_delegate(fleet):
    manager, events = await fleet({
        "Lead": [call("mcp__fleet__delegate", name="Helper", task="Help"),
                 {"report_last_result": True}]}, top_rank=1)
    await manager.lead("Do it")
    await asyncio.wait_for(manager.finished.wait(), 30)
    assert len(manager.fleet.units) == 1
    assert "lowest rank" in delivered(events)[0][3]


async def test_the_lead_asks_the_person_and_carries_on_with_the_answer(fleet):
    manager, events = await fleet({
        "Lead": [call("ask_for_help", question="Blue or green?"),
                 finish("Painted it green.", when="green")]})
    lead = await manager.lead("Paint it")
    await until(lambda: lead.state == "waiting")
    assert delivered(events) == [("Lead", None, "question", "Blue or green?")]
    await manager.say("green")
    await asyncio.wait_for(manager.finished.wait(), 30)
    assert delivered(events)[-1] == ("Lead", None, "report", "Painted it green.")


async def test_the_person_approves_what_permissions_do_not_cover(fleet, tmp_path: Path):
    outside = tmp_path / "outside.txt"  # not in the fleet's work folder
    manager, events = await fleet({
        "Lead": [call("write_file", path=str(outside), content="hello"),
                 {"report_last_result": True}]})
    await manager.lead("Leave a note outside")
    await until(lambda: any(isinstance(e, Approval) for e in events))
    approval = next(e for e in events if isinstance(e, Approval))
    assert approval.tool == "write_file" and str(outside) in approval.arguments
    assert not outside.exists()
    await manager.approve(approval.unit, approval.request_id, True)
    await asyncio.wait_for(manager.finished.wait(), 30)
    assert outside.read_text() == "hello"


async def test_stopping_an_agent_stops_those_under_it(fleet):
    manager, _ = await fleet({
        "Lead": [call("mcp__fleet__delegate", name="Mid", task="Run the team",
                      ongoing=True)],
        "Mid": [call("mcp__fleet__delegate", name="Low", task="Keep watch", ongoing=True)]},
        top_rank=3)
    lead = await manager.lead("Run things", ongoing=True)
    await until(lambda: len(manager.fleet.units) == 3
                and manager.fleet.units["u3"].state == "working")
    mid, low = manager.fleet.units["u2"], manager.fleet.units["u3"]
    assert (mid.ongoing, low.rank, low.supervisor) == (True, 1, "u2")
    await manager.stop(mid)
    assert (lead.state, mid.state, low.state) == ("working", "stopped", "stopped")
    assert set(manager._channels) == {"u1"}


async def test_the_fleets_spending_limit_stops_everyone(fleet):
    manager, events = await fleet({}, budget_usd=0.0003, check_in_s=(0.05, 0.05))
    lead = await manager.lead("Keep going", ongoing=True)
    await asyncio.wait_for(manager.finished.wait(), 30)
    await until(lambda: lead.state == "stopped")
    assert any(isinstance(e, Notice) and "spending limit" in e.text for e in events)
    assert manager.fleet.spent_usd >= 0.0003


async def test_a_unit_that_cannot_start_has_failed(fleet, tmp_path: Path):
    manager, _ = await fleet({})
    manager._command = ("false",)
    lead = await manager.lead("Do it")
    assert lead.state == "failed" and lead.failure
