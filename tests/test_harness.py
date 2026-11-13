"""A fleet on a harness that is not Kayden (tests/fakeharness.py): what the manager needs
of a unit is in kfm.api and nowhere else."""

from __future__ import annotations

import asyncio
import itertools
from pathlib import Path

import pytest

from kfm import harnesses, log
from kfm.config import FleetConfig
from kfm.events import Delivered, FleetEvent, Told
from kfm.fleet import Fleet, FleetError, Unit
from kfm.harnesses.kayden import KaydenHarness
from kfm.manager import Manager, lay_out, place
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


async def test_what_happens_in_a_fleet_is_kept_in_its_record(tmp_path: Path):
    harness = FakeHarness({
        "Lead": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Tidy the docs"})]},
                 {"when": "[Report from Scout", "do": [("say", "Good."),
                                                       ("report", "Docs are tidy.")]}],
        "Scout": [{"do": [("report", "Tidied docs/")]}]})
    manager, events = await run(tmp_path, harness)
    await manager.lead("Tidy up")
    await asyncio.wait_for(manager.finished.wait(), 10)
    await manager.say("Thanks")
    await manager.aclose()
    fleet = Fleet.load(tmp_path / "fleet")
    kept = log.read(tmp_path / "fleet", fleet)
    assert [type(event).__name__ for _, event in kept] == [
        type(event).__name__ for event in events]
    assert all(earlier <= later for (earlier, _), (later, _) in itertools.pairwise(kept))
    reports = [(e.unit.name, e.to.name if e.to else None, e.text)
               for _, e in kept if isinstance(e, Delivered)]
    assert reports[:2] == [("Scout", "Lead", "Tidied docs/"), ("Lead", None, "Docs are tidy.")]
    assert [e.text for _, e in kept if isinstance(e, Told)] == ["Thanks"]
    assert kept[0][1].unit is fleet.units["u1"]  # the units of the fleet it was read with
    (tmp_path / "fleet" / log.FILE).write_text("{cut off")
    assert log.read(tmp_path / "fleet", fleet) == []


def test_a_harness_is_found_by_its_name_or_its_import_path(tmp_path: Path):
    by_name = harnesses.load("kayden", {"config": str(tmp_path / "kayden.toml")})
    assert isinstance(by_name, KaydenHarness) and by_name.config == tmp_path / "kayden.toml"
    by_path = harnesses.load("tests.fakeharness:FakeHarness", {})
    assert isinstance(by_path, FakeHarness)
    with pytest.raises(ValueError, match="no harness 'nope'.*kayden"):
        harnesses.load("nope", {})
    with pytest.raises(ValueError, match="could not be loaded"):
        harnesses.load("kayden", {"wrong": 1})


async def test_the_person_adds_an_agent_and_sets_models(tmp_path: Path):
    harness = FakeHarness({
        "Lead": [{"do": [("say", "On watch.")]}],
        "Tester": [{"do": [("say", "Testing.")]},
                   {"when": "model was changed", "do": [("say", "Carrying on.")]}]})
    manager, _ = await run(tmp_path, harness)
    lead = await manager.lead("Keep the project healthy", ongoing=True)
    tester = await manager.hire(lead, "Tester", "Run the tests", ongoing=True, model="small-1")
    assert (tester.supervisor, tester.rank, tester.model) == (lead.id, 4, "small-1")
    assert harness.models == [("Lead", None), ("Tester", "small-1")]
    await until(lambda: len(harness.heard.get("Lead", [])) == 2)
    told = harness.heard["Lead"][1]  # its supervisor knows it has it
    assert "Tester (K4, u2)" in told and "Run the tests" in told and "fleet.message" in told
    assert "Your task: Run the tests" in harness.heard["Tester"][0]
    await until(lambda: tester.state == "working" and len(harness.heard["Tester"]) == 1)

    await manager.set_model(tester, "big-1")  # at once: its process starts again
    await until(lambda: ("Tester", "big-1") in harness.models)
    assert harness.opened[-1] == ("Tester", "session-u2")  # on its own conversation
    await until(lambda: any("model was changed" in heard for heard in harness.heard["Tester"]))
    await manager.set_model(lead, "")  # none of its own: its rank's again
    await until(lambda: harness.models[-1] == ("Lead", None))
    await manager.aclose()

    assert Fleet.load(tmp_path / "fleet").units["u2"].model == "big-1"
    with pytest.raises(FleetError, match="has not started"):
        other = Manager(FleetConfig(), tmp_path / "other", tmp_path / "work", harness=harness)
        await other.hire(lead, "X", "y")


async def test_a_fleet_laid_out_by_hand_starts_as_it_was_built(tmp_path: Path):
    harness = FakeHarness({
        "Chief": [{"do": [("say", "On watch.")]}],
        "Writer": [{"do": [("report", "Written.")]}],
        "Checker": [{"do": [("say", "Checking.")]}]})
    folder = tmp_path / "fleet"
    (tmp_path / "work").mkdir()
    fleet = lay_out(FleetConfig(), folder, tmp_path / "work", "content")
    assert Fleet.load(folder).name == "content" and not Fleet.load(folder).units
    with pytest.raises(FleetError, match="lead first"):
        place(fleet, folder, Unit("u9", "X", 4, "y", None), "Writer", "Write")
    chief = place(fleet, folder, None, "Chief", "Run content", ongoing=True, model="big-1")
    writer = place(fleet, folder, chief, "Writer", "Write the posts")
    place(fleet, folder, writer, "Checker", "Check the facts", ongoing=True)
    with pytest.raises(FleetError, match="already has a lead"):
        place(fleet, folder, None, "Other", "y")
    saved = Fleet.load(folder)
    assert [(u.name, u.rank, u.state, u.model) for u in saved.units.values()] == [
        ("Chief", 5, "planned", "big-1"), ("Writer", 4, "planned", None),
        ("Checker", 3, "planned", None)]
    assert not harness.models  # nothing runs

    manager, events = await run(tmp_path, harness, resume=True)
    await until(lambda: delivered(events) == [("Writer", "Chief", "report", "Written.")])
    assert harness.models == [("Chief", "big-1"), ("Writer", None), ("Checker", None)]
    brief = harness.heard["Chief"][0]  # it knows whom it was given
    assert "Your task: Run content" in brief and "Writer (K4, u2): Write the posts" in brief
    assert "Checker" not in brief and "Checker (K3, u3)" in harness.heard["Writer"][0]
    units = manager.fleet.units
    assert units["u1"].state == units["u3"].state == "working" and units["u2"].state == "done"

    # In a fleet that has run, an agent placed while it is stopped starts with it.
    await manager.aclose()
    stopped = Fleet.load(folder)
    place(stopped, folder, stopped.units["u1"], "Editor", "Edit the posts")
    harness.scripts["Editor"] = [{"do": [("say", "Editing.")]}]
    manager, _ = await run(tmp_path, harness, resume=True)
    await until(lambda: any("Editor (K4, u4)" in heard for heard in harness.heard["Chief"][1:]))
    await until(lambda: manager.fleet.units["u4"].state == "working")
    await manager.aclose()


async def test_an_agent_with_no_task_takes_its_first_message_as_its_task(tmp_path: Path):
    harness = FakeHarness({
        "Lead": [{"do": [("say", "On watch.")]},
                 {"when": "added Scout",
                  "do": [("tool", "message", {"agent": "u2", "text": "Map the docs"})]}],
        "Scout": [{"do": [("report", "Mapped.")]}]})
    manager, events = await run(tmp_path, harness)
    lead = await manager.lead("Keep watch", ongoing=True)
    scout = await manager.hire(lead, "Scout", "")
    assert (scout.state, scout.session) == ("idle", None)  # nothing started, nothing spent
    await until(lambda: delivered(events) == [("Scout", "Lead", "report", "Mapped.")])
    told = harness.heard["Lead"][1]
    assert "no task yet" in told and 'fleet.message(agent="u2"' in told
    assert "Your task: Map the docs" in harness.heard["Scout"][0]
    assert scout.task == "Map the docs" and "[Message from" not in scout.task
    await manager.aclose()

    # Built by hand with no tasks at all: nothing runs until the person says what to do.
    harness = FakeHarness({"Chief": [{"do": [("say", "On it.")]}]})
    folder = tmp_path / "fleet2"
    fleet = lay_out(FleetConfig(), folder, tmp_path / "work", "content")
    chief = place(fleet, folder, None, "Chief", "")
    place(fleet, folder, chief, "Writer", "")
    manager = Manager.resumed(folder, harness=harness)
    await manager.start()
    assert not harness.models
    assert [u.state for u in manager.fleet.units.values()] == ["idle", "idle"]
    await manager.say("Run content")
    await until(lambda: harness.heard.get("Chief"))
    brief = harness.heard["Chief"][0]
    assert "Your task: Run content" in brief and "Writer (K4, u2): no task yet" in brief
    assert harness.models == [("Chief", None)]
    await manager.aclose()


async def until(predicate, timeout: float = 10.0) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)
