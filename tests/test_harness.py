"""A fleet on a harness that is not Kadence (tests/fakeharness.py): what the manager needs
of a unit is in kfm.api and nowhere else."""

from __future__ import annotations

import asyncio
import itertools
from pathlib import Path

import pytest

from kfm import harnesses, log
from kfm.config import FleetConfig
from kfm.events import Delivered, FleetEvent, Instructed, Notice, Told
from kfm.fleet import Fleet, FleetError, Unit
from kfm.harnesses.kadence import KadenceHarness
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
    lead = await manager.lead("Tidy up", name="Lead")
    await asyncio.wait_for(manager.finished.wait(), 10)
    assert delivered(events) == [("Scout", "Lead", "question", "Which folder?"),
                                 ("Scout", "Lead", "report", "Tidied docs/"),
                                 ("Lead", None, "report", "Docs are tidy.")]
    assert lead.session == "session-u1" and lead.spent_usd > 0
    # The words KFM wrote to the units name this harness's tools, not Kadence's.
    brief, question = harness.heard["Lead"][0], harness.heard["Lead"][1]
    assert "fleet.delegate" in brief and "call done with a report" in brief
    assert "fleet.message(agent=\"u2\"" in question
    assert not any("mcp__" in text or "finish_goal" in text
                   for heard in harness.heard.values() for text in heard)
    await manager.aclose()


async def test_a_fleet_on_another_harness_is_resumed_on_its_sessions(tmp_path: Path):
    harness = FakeHarness({"Lead": [{"do": [("say", "On watch.")]}]})
    manager, _ = await run(tmp_path, harness)
    await manager.lead("Keep watch", ongoing=True, name="Lead")
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
    await manager.lead("Tidy up", name="Lead")
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
    await manager.lead("Tidy up", name="Lead")
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
    by_name = harnesses.load("kadence", {"config": str(tmp_path / "kadence.toml")})
    assert isinstance(by_name, KadenceHarness) and by_name.config == tmp_path / "kadence.toml"
    by_path = harnesses.load("tests.fakeharness:FakeHarness", {})
    assert isinstance(by_path, FakeHarness)
    with pytest.raises(ValueError, match="no harness 'nope'.*kadence"):
        harnesses.load("nope", {})
    with pytest.raises(ValueError, match="could not be loaded"):
        harnesses.load("kadence", {"wrong": 1})


async def test_the_person_adds_an_agent_and_sets_models(tmp_path: Path):
    harness = FakeHarness({
        "Lead": [{"do": [("say", "On watch.")]}],
        "Tester": [{"do": [("say", "Testing.")]},
                   {"when": "model was changed", "do": [("say", "Carrying on.")]}]})
    manager, _ = await run(tmp_path, harness)
    lead = await manager.lead("Keep the project healthy", ongoing=True, name="Lead")
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
    lead = await manager.lead("Keep watch", ongoing=True, name="Lead")
    scout = await manager.hire(lead, "Scout", "")
    assert (scout.state, scout.session) == ("idle", None)  # nothing started, nothing spent
    await until(lambda: delivered(events) == [("Scout", "Lead", "report", "Mapped.")])
    told = harness.heard["Lead"][1]
    assert "no task yet" in told and 'fleet.message(agent="u2"' in told
    assert "Your task: Map the docs" in harness.heard["Scout"][0]
    assert scout.task == "Map the docs" and "[Message from" not in scout.task
    await manager.aclose()

    # Built by hand with no names and no tasks: nothing runs until the person says what to
    # do, and the agents are known by rank and id.
    harness = FakeHarness({"K5 u1": [{"do": [("say", "On it.")]}]})
    folder = tmp_path / "fleet2"
    fleet = lay_out(FleetConfig(), folder, tmp_path / "work", "content")
    chief = place(fleet, folder, None, " ", "")
    worker = place(fleet, folder, chief, "", "")
    assert (chief.name, chief.called, worker.label) == ("", "K5 u1", "K4 agent u2")
    manager = Manager.resumed(folder, harness=harness)
    await manager.start()
    assert not harness.models
    assert [u.state for u in manager.fleet.units.values()] == ["idle", "idle"]
    await manager.say("Run content")
    await until(lambda: harness.heard.get("K5 u1"))
    brief = harness.heard["K5 u1"][0]
    assert brief.startswith("You are a K5 agent in a fleet")
    assert "Your task: Run content" in brief and "K4 agent u2: no task yet" in brief
    assert harness.models == [("K5 u1", None)]
    await manager.aclose()


async def test_a_renamed_agent_is_known_by_its_new_name(tmp_path: Path):
    harness = FakeHarness({
        "Lead": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Map the docs"})]}],
        "Scout": [{"do": [("say", "Mapping.")]},
                  {"when": "go on", "do": [("report", "Mapped.")]}]})
    manager, events = await run(tmp_path, harness)
    await manager.lead("Tidy up", ongoing=True, name="Lead")
    await until(lambda: "u2" in manager.fleet.units)
    scout = manager.fleet.units["u2"]
    await manager.rename("Mapper", scout)
    await manager.rename("docs")
    await manager.tell(scout, "go on")
    await until(lambda: delivered(events) == [("Mapper", "Lead", "report", "Mapped.")])
    await until(lambda: any("[Report from Mapper (K4, u2)]" in heard
                            for heard in harness.heard["Lead"]))
    await manager.aclose()
    saved = Fleet.load(tmp_path / "fleet")
    assert saved.name == "docs" and saved.units["u2"].name == "Mapper"


async def test_the_person_messages_one_agent_and_its_supervisor_is_told(tmp_path: Path):
    harness = FakeHarness({
        "Lead": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Map the docs"})]},
                 {"when": "The person told Scout", "do": [("say", "Noted.")]}],
        "Scout": [{"do": [("say", "Mapping.")]},
                  {"when": "Skip the drafts", "do": [("say", "Skipping them.")]}]})
    manager, events = await run(tmp_path, harness)
    lead = await manager.lead("Tidy up", ongoing=True, name="Lead")
    await until(lambda: harness.heard.get("Scout"))
    scout = manager.fleet.units["u2"]
    await manager.say("Skip the drafts", scout)
    await until(lambda: len(harness.heard["Scout"]) == 2)
    heard = harness.heard["Scout"][1]  # it knows who spoke, and that it outranks its supervisor
    assert "Skip the drafts" in heard and "the person" in heard and "supervisor" in heard
    await until(lambda: any("The person told Scout (K4, u2)" in told and "Skip the drafts" in told
                            for told in harness.heard["Lead"]))
    await manager.say("Thanks")  # with nobody named: the lead
    await until(lambda: "Thanks" in harness.heard["Lead"][-1])
    assert [(e.text, e.to) for e in events if isinstance(e, Told)] == [
        ("Skip the drafts", scout), ("Thanks", lead)]
    await manager.aclose()
    kept = log.read(tmp_path / "fleet", Fleet.load(tmp_path / "fleet"))
    assert [e.to.id for _, e in kept if isinstance(e, Told)] == ["u2", "u1"]


async def test_what_goes_down_the_chain_is_shown_too(tmp_path: Path):
    harness = FakeHarness({
        "Lead": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Map the docs"})]},
                 {"when": "[Question from Scout",
                  "do": [("tool", "message", {"agent": "u2", "text": "Use docs/"})]}],
        "Scout": [{"do": [("ask", "Which folder?")]},
                  {"when": "Use docs/", "do": [("say", "On it.")]}]})
    manager, events = await run(tmp_path, harness)
    lead = await manager.lead("Tidy up", ongoing=True, name="Lead")
    await until(lambda: len(harness.heard.get("Scout", [])) == 2)
    scout = manager.fleet.units["u2"]
    down = [(e.by, e.unit, e.kind, e.text) for e in events if isinstance(e, Instructed)]
    assert down == [(None, lead, "task", "Tidy up"),  # None: the person
                    (lead, scout, "task", "Map the docs"),
                    (lead, scout, "message", "Use docs/")]
    await manager.hire(scout, "", "Check the links")  # the person's, though under Scout
    await until(lambda: "u3" in manager.fleet.units)
    assert [(e.by, e.unit.id) for e in events if isinstance(e, Instructed)][-1] == (None, "u3")
    await manager.aclose()
    kept = [e for _, e in log.read(tmp_path / "fleet", Fleet.load(tmp_path / "fleet"))
            if isinstance(e, Instructed)]
    assert [(e.by.id if e.by else None, e.unit.id) for e in kept] == [
        (None, "u1"), ("u1", "u2"), ("u1", "u2"), (None, "u3")]


async def test_files_go_with_a_message_to_the_agent_it_is_for(tmp_path: Path):
    harness = FakeHarness({
        "Lead": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Map the docs"})]}],
        "Scout": [{"do": [("say", "Mapping.")]}]})
    manager, events = await run(tmp_path, harness)
    await manager.lead("Tidy up", ongoing=True, name="Lead")
    await until(lambda: harness.heard.get("Scout"))
    scout = manager.fleet.units["u2"]
    await manager.say("Start from this", files=["/tmp/brief.pdf"])
    await manager.say("And this", scout, files=["/tmp/map.png"], spoken=True)
    assert harness.files == [("Lead", ["/tmp/brief.pdf"], False),
                             ("Scout", ["/tmp/map.png"], True)]
    await until(lambda: any("map.png" in heard for heard in harness.heard["Lead"]))  # told of it
    assert [(e.text, e.files) for e in events if isinstance(e, Told)] == [
        ("Start from this", ("/tmp/brief.pdf",)), ("And this", ("/tmp/map.png",))]
    # A first message that is an agent's task brings its files after the task.
    idle = await manager.hire(scout, "", "")
    await manager.say("Check the links", idle, files=["/tmp/links.txt"])
    await until(lambda: harness.files[-1] == ("K3 u3", ["/tmp/links.txt"], False))
    await manager.aclose()


async def test_an_agent_is_given_another_task_and_an_agent_is_removed(tmp_path: Path):
    harness = FakeHarness({
        "Lead": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Map the docs"})]}],
        "Scout": [{"do": [("tool", "delegate", {"name": "Helper", "task": "Count pages"})]},
                  {"when": "task was changed", "do": [("say", "Mapping the code now.")]}],
        "Helper": [{"do": [("say", "Counting.")]}]})
    manager, events = await run(tmp_path, harness)
    lead = await manager.lead("Tidy up", ongoing=True, name="Lead")
    await until(lambda: harness.heard.get("Helper"))
    scout, helper = manager.fleet.units["u2"], manager.fleet.units["u3"]

    await manager.retask(scout, "Map the code")
    await until(lambda: any("task was changed" in heard for heard in harness.heard["Scout"]))
    [(name, instruction)] = harness.instructed  # its standing instruction, not only a message
    assert name == "Scout" and "Your task: Map the code" in instruction
    assert scout.task == "Map the code" and scout.session == "session-u2"  # same conversation
    assert [(e.by, e.unit, e.text) for e in events if isinstance(e, Instructed)][-1] == (
        None, scout, "Map the code")
    await until(lambda: any("changed the task of Scout (K4, u2)" in heard and "Map the code" in heard
                            for heard in harness.heard["Lead"]))
    with pytest.raises(FleetError, match="empty"):
        await manager.retask(scout, "  ")
    idle = await manager.hire(lead, "", "")  # one with no task starts on the one it is given
    await manager.retask(idle, "Check the links")
    await until(lambda: any("Your task: Check the links" in heard
                            for heard in harness.heard.get("K4 u4", [])))

    (tmp_path / "fleet" / "units" / "u3").mkdir(parents=True, exist_ok=True)
    await until(lambda: scout.spent_usd > 0 and helper.spent_usd > 0)
    spent = manager.fleet.spent_usd
    await manager.remove(scout)  # with everything under it
    assert list(manager.fleet.units) == ["u1", "u4"]
    # What they spent still counts against the fleet's limit.
    assert manager.fleet.spent_usd == pytest.approx(spent)
    assert manager.fleet.spent_usd > sum(u.spent_usd for u in manager.fleet.units.values())
    assert not (tmp_path / "fleet" / "units" / "u3").exists()
    await until(lambda: any("removed Scout (K4, u2)" in heard for heard in harness.heard["Lead"]))
    with pytest.raises(FleetError):
        await manager.tell(helper, "still there?")
    again = await manager.hire(lead, "Scout", "Map the docs again")
    assert again.id == "u5"  # a number is not given twice
    await manager.aclose()
    kept = Fleet.load(tmp_path / "fleet")
    assert list(kept.units) == ["u1", "u4", "u5"]
    assert kept.spent_usd > sum(u.spent_usd for u in kept.units.values())  # and is kept


async def test_a_failure_in_the_managers_own_work_is_seen(tmp_path: Path, caplog):
    harness = FakeHarness({"Lead": [{"do": [("say", "On watch.")]}]})
    manager, events = await run(tmp_path, harness)
    await manager.lead("Keep watch", ongoing=True, name="Lead")

    async def broken() -> None:
        raise RuntimeError("disk on fire")

    manager._spawn(broken())
    await until(lambda: any(isinstance(e, Notice) and "disk on fire" in e.text for e in events))
    assert "disk on fire" in caplog.text  # with where it happened, for whoever reads the log
    await manager.aclose()


async def until(predicate, timeout: float = 10.0) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)
