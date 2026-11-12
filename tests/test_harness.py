"""A fleet on a harness that is not Kayden (tests/fakeharness.py): what the manager needs
of a unit is in kfm.api and nowhere else."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kfm import harnesses
from kfm.config import FleetConfig
from kfm.fleet import Fleet
from kfm.harnesses.kayden import KaydenHarness
from kfm.manager import Delivered, FleetEvent, Manager
from tests.fakeharness import FakeHarness


def delivered(events: list[FleetEvent]) -> list[tuple[str, str | None, str, str]]:
    return [(e.unit.name, e.to.name if e.to else None, e.kind, e.text)
            for e in events if isinstance(e, Delivered)]


async def run(tmp_path: Path, harness: FakeHarness, **how) -> tuple[Manager, list[FleetEvent]]:
    events: list[FleetEvent] = []
    (tmp_path / "work").mkdir(exist_ok=True)
    if how.pop("resume", False):
        manager = Manager.resumed(tmp_path / "fleet", harness=harness, on_event=events.append)
    else:
        manager = Manager(FleetConfig(), tmp_path / "fleet", tmp_path / "work",
                          harness=harness, on_event=events.append)
    await manager.start()
    return manager, events


async def test_a_fleet_runs_on_another_harness(tmp_path: Path):
    harness = FakeHarness({
        "Lead": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Tidy the docs"})]},
                 {"when": "[Question from Scout",
                  "do": [("tool", "message", {"agent": "u2", "text": "Use docs/"})]},
                 {"when": "[Report from Scout", "do": [("report", "Docs are tidy.")]}],
        "Scout": [{"do": [("ask", "Which folder?")]},
                  {"when": "Use docs/", "do": [("say", "On it."), ("report", "Tidied docs/")]}]})
    manager, events = await run(tmp_path, harness)
    lead = await manager.lead("Tidy up")
    await asyncio.wait_for(manager.finished.wait(), 10)
    assert delivered(events) == [("Scout", "Lead", "question", "Which folder?"),
                                 ("Scout", "Lead", "report", "Tidied docs/"),
                                 ("Lead", None, "report", "Docs are tidy.")]
    assert lead.session == "session-u1" and lead.spent_usd > 0
    # The words KFM wrote to the units name this harness's tools, not Kayden's.
    brief, question = harness.heard["Lead"][0], harness.heard["Lead"][1]
    assert "fleet.delegate" in brief and "call done with a report" in brief
    assert "fleet.message(agent=\"u2\"" in question
    assert not any("mcp__" in text or "finish_goal" in text
                   for heard in harness.heard.values() for text in heard)
    await manager.aclose()


async def test_a_fleet_on_another_harness_is_resumed_on_its_sessions(tmp_path: Path):
    harness = FakeHarness({"Lead": [{"do": [("say", "On watch.")]}]})
    manager, _ = await run(tmp_path, harness)
    await manager.lead("Keep watch", ongoing=True)
    while "Lead" not in harness.heard:
        await asyncio.sleep(0.01)
    await manager.aclose()
    assert Fleet.load(tmp_path / "fleet").units["u1"].session == "session-u1"

    harness.scripts["Lead"] = [{"when": "started again", "do": [("say", "Back on watch.")]}]
    manager, _ = await run(tmp_path, harness, resume=True)
    while len(harness.heard["Lead"]) < 2:
        await asyncio.sleep(0.01)
    assert harness.opened == [("Lead", None), ("Lead", "session-u1")]
    assert "started again" in harness.heard["Lead"][1]
    await manager.aclose()


async def test_a_supervisor_that_acts_at_once_after_a_resume_finds_its_agents_open(
        tmp_path: Path):
    harness = FakeHarness({
        "Lead": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Tidy the docs"})]}],
        "Scout": [{"do": [("ask", "Which folder?")]}]})
    manager, events = await run(tmp_path, harness)
    await manager.lead("Tidy up")
    while ("Scout", "Lead", "question", "Which folder?") not in delivered(events):
        await asyncio.sleep(0.01)
    await manager.aclose()

    harness.opened.clear()
    harness.scripts.update({
        "Lead": [{"when": "started again",
                  "do": [("tool", "message", {"agent": "u2", "text": "Use docs/"})]},
                 {"when": "[Report from Scout", "do": [("report", "Docs are tidy.")]}],
        "Scout": [{"when": "Use docs/", "do": [("report", "Tidied docs/")]}]})
    manager, events = await run(tmp_path, harness, resume=True)
    await asyncio.wait_for(manager.finished.wait(), 10)
    assert sorted(harness.opened) == [("Lead", "session-u1"), ("Scout", "session-u2")]
    assert delivered(events)[-1] == ("Lead", None, "report", "Docs are tidy.")
    await manager.aclose()


def test_a_harness_is_found_by_its_name_or_its_import_path(tmp_path: Path):
    by_name = harnesses.load("kayden", {"config": str(tmp_path / "kayden.toml")})
    assert isinstance(by_name, KaydenHarness) and by_name.config == tmp_path / "kayden.toml"
    by_path = harnesses.load("tests.fakeharness:FakeHarness", {})
    assert isinstance(by_path, FakeHarness)
    with pytest.raises(ValueError, match="no harness 'nope'.*kayden"):
        harnesses.load("nope", {})
    with pytest.raises(ValueError, match="could not be loaded"):
        harnesses.load("kayden", {"wrong": 1})
