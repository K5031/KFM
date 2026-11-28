"""A fleet on a harness that is not Kaden (tests/fakeharness.py): what the manager needs
of a unit is in kfm.api and nowhere else."""

from __future__ import annotations

import asyncio
import itertools
from pathlib import Path

import pytest

from kfm import api, harnesses, log, units
from kfm.config import FleetConfig
from kfm.events import Delivered, Dismissed, FleetEvent, Instructed, Notice, Told
from kfm.fleet import Fleet, FleetError
from kfm.harnesses.kaden import KadenHarness
from kfm.manager import Manager
from tests.fakeharness import FakeHarness


def delivered(manager: Manager, events: list[FleetEvent]) -> list[tuple[str, str | None, str, str]]:
    """Who sent what to whom, by the units' names."""
    def name(unit: str | None) -> str | None:
        return manager.fleet.units[unit].name if unit is not None else None

    return [(name(e.unit), name(e.to), e.kind, e.text) for e in events if isinstance(e, Delivered)]


async def run(tmp_path: Path, harness: FakeHarness, **how) -> tuple[Manager, list[FleetEvent]]:
    events: list[FleetEvent] = []
    (tmp_path / "work").mkdir(exist_ok=True)
    if how.pop("resume", False):
        manager = Manager.load(tmp_path / "fleet", harness=harness, on_event=events.append)
    else:
        manager = Manager.new(how.pop("config", FleetConfig()), tmp_path / "fleet",
                              tmp_path / "work", harness=harness, on_event=events.append)
    await manager.start()
    return manager, events


async def test_a_fleet_runs_on_another_harness(tmp_path: Path):
    harness = FakeHarness({
        "Lead": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Tidy the docs"})]},
                 {"when": "[Question from Scout",
                  "do": [("tool", "message", {"agent": "K4001", "text": "Use docs/"})]},
                 {"when": "[Report from Scout", "do": [("report", "Docs are tidy.")]}],
        "Scout": [{"do": [("ask", "Which folder?")]},
                  {"when": "Use docs/", "do": [("say", "On it."), ("report", "Tidied docs/")]}]})
    manager, events = await run(tmp_path, harness)
    lead = await manager.add(None, "Lead", "Tidy up")
    await asyncio.wait_for(manager.finished.wait(), 10)
    assert delivered(manager, events) == [("Scout", "Lead", "question", "Which folder?"),
                                 ("Scout", "Lead", "report", "Tidied docs/"),
                                 ("Lead", None, "report", "Docs are tidy.")]
    assert lead.session == "session-K5001" and lead.spent_usd > 0
    # The words KFM wrote to the units name this harness's tools, not Kaden's.
    brief, question = harness.heard["Lead"][0], harness.heard["Lead"][1]
    assert "fleet.delegate" in brief and "fleet.finish with a report ends the task" in brief
    assert "fleet.message(agent=\"K4001\"" in question
    assert not any("mcp__" in text
                   for heard in harness.heard.values() for text in heard)
    await manager.aclose()


async def test_the_fleet_wakes_an_agent_whose_work_stands_until_it_finishes(tmp_path: Path):
    """A unit only takes turns; the fleet sends one whose task stands a check-in once it
    has rested for the longest time, and none once it has finished."""
    harness = FakeHarness({"Lead": [{"do": [("say", "Waiting for the build.")]},
                                    {"when": units.CHECK_IN, "do": [("report", "Built.")]}]})
    manager, _ = await run(tmp_path, harness, config=FleetConfig(check_in_s=(0.01, 0.1)))
    lead = await manager.add(None, "Lead", "Build it")
    await asyncio.wait_for(manager.finished.wait(), 10)
    assert harness.heard["Lead"][1] == units.CHECK_IN and lead.state == "done"
    await asyncio.sleep(0.3)
    assert len(harness.heard["Lead"]) == 2  # finished: nobody wakes it
    await manager.aclose()


async def test_a_waking_check_in_waits_for_the_answer_to_a_question(tmp_path: Path):
    harness = FakeHarness({"Lead": [{"do": [("ask", "Blue or green?")]}]})
    manager, _ = await run(tmp_path, harness, config=FleetConfig(check_in_s=(0.01, 0.05)))
    lead = await manager.add(None, "Lead", "Paint it")
    while lead.state != "waiting":
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.2)
    assert len(harness.heard["Lead"]) == 1  # nothing arrived, and it is not woken
    await manager.aclose()


async def test_standing_work_cannot_be_finished(tmp_path: Path):
    harness = FakeHarness({"Lead": [{"do": [("report", "All done.")]}]})
    manager, events = await run(tmp_path, harness)
    lead = await manager.add(None, "Lead", "Keep watch", ongoing=True)
    while not harness.results:
        await asyncio.sleep(0.01)
    [(_, tool, answer)] = harness.results
    assert tool == "finish" and "standing work" in answer.lower()
    assert lead.report is None and lead.state == "working"
    assert not [e for e in events if isinstance(e, Delivered)]
    await manager.aclose()


async def test_a_fleet_on_another_harness_is_resumed_on_its_sessions(tmp_path: Path):
    harness = FakeHarness({"Lead": [{"do": [("say", "On watch.")]}]})
    manager, _ = await run(tmp_path, harness)
    await manager.add(None, "Lead", "Keep watch", ongoing=True)
    while "Lead" not in harness.heard:
        await asyncio.sleep(0.01)
    await manager.aclose()
    assert Fleet.load(tmp_path / "fleet").units["K5001"].session == "session-K5001"

    harness.scripts["Lead"] = [{"when": "started again", "do": [("say", "Back on watch.")]}]
    manager, events = await run(tmp_path, harness, resume=True)
    while len(harness.heard["Lead"]) < 2:
        await asyncio.sleep(0.01)
    assert harness.opened == [("Lead", None), ("Lead", "session-K5001")]
    assert "started again" in harness.heard["Lead"][1]
    assert Notice("The fleet was started again.") in events  # and the feed says so
    await manager.aclose()


async def test_a_supervisor_that_acts_at_once_after_a_resume_finds_its_agents_open(
        tmp_path: Path):
    harness = FakeHarness({
        "Lead": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Tidy the docs"})]}],
        "Scout": [{"do": [("ask", "Which folder?")]}]})
    manager, events = await run(tmp_path, harness)
    await manager.add(None, "Lead", "Tidy up")
    while ("Scout", "Lead", "question", "Which folder?") not in delivered(manager, events):
        await asyncio.sleep(0.01)
    await manager.aclose()

    harness.opened.clear()
    harness.scripts.update({
        "Lead": [{"when": "started again",
                  "do": [("tool", "message", {"agent": "K4001", "text": "Use docs/"})]},
                 {"when": "[Report from Scout", "do": [("report", "Docs are tidy.")]}],
        "Scout": [{"when": "Use docs/", "do": [("report", "Tidied docs/")]}]})
    manager, events = await run(tmp_path, harness, resume=True)
    await asyncio.wait_for(manager.finished.wait(), 10)
    assert sorted(harness.opened) == [("Lead", "session-K5001"), ("Scout", "session-K4001")]
    assert delivered(manager, events)[-1] == ("Lead", None, "report", "Docs are tidy.")
    await manager.aclose()


async def test_what_happens_in_a_fleet_is_kept_in_its_record(tmp_path: Path):
    harness = FakeHarness({
        "Lead": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Tidy the docs"})]},
                 {"when": "[Report from Scout", "do": [("say", "Good."),
                                                       ("report", "Docs are tidy.")]}],
        "Scout": [{"do": [("report", "Tidied docs/")]}]})
    manager, events = await run(tmp_path, harness)
    await manager.add(None, "Lead", "Tidy up")
    await asyncio.wait_for(manager.finished.wait(), 10)
    await manager.say("Thanks")
    await manager.aclose()
    kept = log.read(tmp_path / "fleet")
    assert [type(event).__name__ for _, event in kept] == [
        type(event).__name__ for event in events]
    assert all(earlier <= later for (earlier, _), (later, _) in itertools.pairwise(kept))
    assert [event for _, event in kept] == events  # units by their ids, as they happened
    reports = [(e.unit, e.to, e.text) for _, e in kept if isinstance(e, Delivered)]
    assert reports[:2] == [("K4001", "K5001", "Tidied docs/"), ("K5001", None, "Docs are tidy.")]
    assert [e.text for _, e in kept if isinstance(e, Told)] == ["Thanks"]
    assert log.count(tmp_path / "fleet") == len(events) == manager.count
    assert [event for _, event in log.read(tmp_path / "fleet", 3)] == events[:3]
    (tmp_path / "fleet" / log.FILE).write_text("{cut off")
    assert log.read(tmp_path / "fleet") == []


def test_units_run_on_kaden_made_with_the_settings_options(tmp_path: Path):
    made = harnesses.load({"config": str(tmp_path / "kaden.toml")})
    assert isinstance(made, KadenHarness) and made.config == tmp_path / "kaden.toml"
    with pytest.raises(ValueError, match="harness_options"):
        harnesses.load({"wrong": 1})


async def test_the_person_adds_an_agent_and_sets_models(tmp_path: Path):
    harness = FakeHarness({
        "Lead": [{"do": [("say", "On watch.")]}],
        "Tester": [{"do": [("say", "Testing.")]},
                   {"when": "model was changed", "do": [("say", "Carrying on.")]}]})
    manager, events = await run(tmp_path, harness)
    lead = await manager.add(None, "Lead", "Keep the project healthy", ongoing=True)
    tester = await manager.add(lead, "Tester", "Run the tests", ongoing=True, model="small-1")
    assert (tester.supervisor, tester.rank, tester.model) == (lead.id, 4, "small-1")
    assert harness.models == [("Lead", None), ("Tester", "small-1")]
    await until(lambda: len(harness.heard.get("Lead", [])) == 2)
    told = harness.heard["Lead"][1]  # its supervisor knows it has it
    assert "Tester (K4001)" in told and "Run the tests" in told and "fleet.message" in told
    assert "Your task: Run the tests" in harness.heard["Tester"][0]
    await until(lambda: tester.state == "working" and len(harness.heard["Tester"]) == 1)

    await manager.set_model(tester, "big-1")  # at once: its process starts again
    await until(lambda: ("Tester", "big-1") in harness.models)
    assert harness.opened[-1] == ("Tester", "session-K4001")  # on its own conversation
    await until(lambda: any("model was changed" in heard for heard in harness.heard["Tester"]))
    assert Notice("Tester was started again on big-1.") in events
    await manager.set_model(lead, "")  # none of its own: its rank's again
    await until(lambda: harness.models[-1] == ("Lead", None))
    await manager.aclose()

    assert Fleet.load(tmp_path / "fleet").units["K4001"].model == "big-1"


async def test_a_fleet_built_while_it_does_not_run_starts_as_it_was_built(tmp_path: Path):
    harness = FakeHarness({
        "Chief": [{"do": [("say", "On watch.")]}],
        "Writer": [{"do": [("report", "Written.")]}],
        "Checker": [{"do": [("say", "Checking.")]}]})
    folder = tmp_path / "fleet"
    (tmp_path / "work").mkdir()
    events: list[FleetEvent] = []
    manager = Manager.new(FleetConfig(), folder, tmp_path / "work", "content",
                          harness=harness, on_event=events.append)
    assert Fleet.load(folder).name == "content" and not Fleet.load(folder).units
    chief = await manager.add(None, "Chief", "Run content", ongoing=True, model="big-1")
    writer = await manager.add(chief, "Writer", "Write the posts")
    await manager.add(writer, "Checker", "Check the facts", ongoing=True)
    with pytest.raises(FleetError, match="already has a lead"):
        await manager.add(None, "Other", "y")
    saved = Fleet.load(folder)
    assert [(u.name, u.rank, u.state, u.model) for u in saved.units.values()] == [
        ("Chief", 5, "planned", "big-1"), ("Writer", 4, "planned", None),
        ("Checker", 3, "planned", None)]
    assert not harness.models and not manager.running  # nothing runs

    await manager.start()
    await until(lambda: delivered(manager, events) == [("Writer", "Chief", "report", "Written.")])
    assert harness.models == [("Chief", "big-1"), ("Writer", None), ("Checker", None)]
    brief = harness.heard["Chief"][0]  # it knows whom it was given
    assert "Your task: Run content" in brief and "Writer (K4001): Write the posts" in brief
    assert "Checker" not in brief and "Checker (K3001)" in harness.heard["Writer"][0]
    units = manager.fleet.units
    assert units["K5001"].state == units["K3001"].state == "working" and units["K4001"].state == "done"

    # The same manager holds the fleet while it is stopped: an agent added then begins
    # when it starts, and its supervisor's instruction names it.
    await manager.stop()
    assert not manager.running and Fleet.load(folder).units["K5001"].state == "working"
    editor = await manager.add(chief, "Editor", "Edit the posts")
    assert editor.state == "planned" and Fleet.load(folder).units["K4002"].state == "planned"
    harness.scripts["Editor"] = [{"do": [("say", "Editing.")]}]
    await manager.start()
    await until(lambda: manager.fleet.units["K4002"].state == "working")
    assert any(name == "Chief" and "Editor (K4002): Edit the posts" in instruction
               for name, instruction in harness.instructed)
    await until(lambda: "started again" in harness.heard["Chief"][-1])
    await manager.aclose()

    again = Manager.load(folder, harness=harness)  # and so does one that read its record
    assert [u.name for u in again.fleet.units.values()] == ["Chief", "Writer", "Checker",
                                                            "Editor"]
    await again.aclose()


async def test_a_fleet_that_runs_with_no_agents_is_given_its_lead(tmp_path: Path):
    harness = FakeHarness({"Lead": [{"do": [("say", "On watch.")]}]})
    manager, _ = await run(tmp_path, harness)
    lead = await manager.add(None, "Lead", "Keep watch", ongoing=True)
    await until(lambda: lead.state == "working" and harness.heard.get("Lead"))
    await manager.aclose()


async def test_an_agent_that_has_run_is_given_another_task_while_its_fleet_is_stopped(
        tmp_path: Path):
    harness = FakeHarness({"Lead": [{"do": [("report", "Watched.")]}]})
    manager, events = await run(tmp_path, harness)
    lead = await manager.add(None, "Lead", "Keep watch")
    await asyncio.wait_for(manager.finished.wait(), 10)
    await manager.stop()
    assert lead.state == "done"

    await manager.retask(lead, "Count the files")  # no process is there to be told
    assert (lead.task, lead.state, lead.report) == ("Count the files", "working", None)
    assert Fleet.load(tmp_path / "fleet").units["K5001"].task == "Count the files"
    assert not harness.instructed and len(harness.heard["Lead"]) == 1
    harness.scripts["Lead"] = [{"when": "started again", "do": [("report", "3 files.")]}]
    await manager.start()  # it is opened on its conversation, with the task it now has
    await until(lambda: delivered(manager, events)[-1] == ("Lead", None, "report", "3 files."))
    [(name, instruction)] = harness.instructed
    assert name == "Lead" and "Your task: Count the files" in instruction
    assert harness.opened == [("Lead", None), ("Lead", "session-K5001")]
    await manager.aclose()


async def test_an_agent_with_no_task_takes_its_first_message_as_its_task(tmp_path: Path):
    harness = FakeHarness({
        "Lead": [{"do": [("say", "On watch.")]},
                 {"when": "added Scout",
                  "do": [("tool", "message", {"agent": "K4001", "text": "Map the docs"})]}],
        "Scout": [{"do": [("report", "Mapped.")]}]})
    manager, events = await run(tmp_path, harness)
    lead = await manager.add(None, "Lead", "Keep watch", ongoing=True)
    scout = await manager.add(lead, "Scout", "")
    assert (scout.state, scout.session) == ("planned", None)  # nothing started, nothing spent
    await until(lambda: delivered(manager, events) == [("Scout", "Lead", "report", "Mapped.")])
    told = harness.heard["Lead"][1]
    assert "no task yet" in told and 'fleet.message(agent="K4001"' in told
    assert "Your task: Map the docs" in harness.heard["Scout"][0]
    assert scout.task == "Map the docs" and "[Message from" not in scout.task
    assert len(harness.heard["Scout"]) == 1  # the message was its task, and nothing more
    await manager.aclose()

    # Built by hand with no names and no tasks: nothing runs until the person says what to
    # do, and the agents are known by rank and id.
    harness = FakeHarness({"K5001": [{"do": [("say", "On it.")]}]})
    folder = tmp_path / "fleet2"
    manager = Manager.new(FleetConfig(), folder, tmp_path / "work", "content", harness=harness)
    chief = await manager.add(None, " ", "")
    worker = await manager.add(chief, "", "")
    assert (chief.name, chief.called, worker.label) == ("", "K5001", "agent K4001")
    await manager.start()
    assert not harness.models
    assert [u.state for u in manager.fleet.units.values()] == ["planned", "planned"]
    with pytest.raises(FleetError, match="empty"):  # files alone are no task
        await manager.say("", files=["/tmp/brief.pdf"])
    await manager.say("Run content")
    await until(lambda: harness.heard.get("K5001"))
    brief = harness.heard["K5001"][0]
    assert brief.startswith("You are a K5 agent in a fleet")
    assert "Your task: Run content" in brief and "agent K4001: no task yet" in brief
    assert harness.models == [("K5001", None)]
    await manager.aclose()


async def test_a_finished_agent_that_is_told_more_has_its_goal_again(tmp_path: Path):
    harness = FakeHarness({"Lead": [
        {"do": [("report", "3 files")]},
        {"when": "Count again", "do": [("say", "Counting."), ("report", "Still 3.")]},
        {"when": "And the folders", "do": [("say", "Two folders.")]}]})
    manager, events = await run(tmp_path, harness)
    lead = await manager.add(None, "Lead", "Count the files")
    await until(lambda: lead.state == "done")
    await manager.say("Count again")
    assert not harness.instructed  # its task stands again, as it was told it
    await until(lambda: delivered(manager, events) == [("Lead", None, "report", "3 files"),
                                              ("Lead", None, "report", "Still 3.")])
    assert harness.heard["Lead"][1] == "Count again"  # the message, and no note with it
    await until(lambda: lead.state == "done")

    await manager.say("And the folders")  # it answers and does not finish
    await until(lambda: len(harness.heard["Lead"]) == 3 and lead.spent_usd == pytest.approx(0.03))
    assert len(delivered(manager, events)) == 2 and lead.report is None  # its words are no report
    assert lead.state == "working"  # its goal stands
    await manager.aclose()


async def test_a_renamed_agent_is_known_by_its_new_name(tmp_path: Path):
    harness = FakeHarness({
        "Lead": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Map the docs"})]}],
        "Scout": [{"do": [("say", "Mapping.")]},
                  {"when": "go on", "do": [("report", "Mapped.")]}]})
    manager, events = await run(tmp_path, harness)
    await manager.add(None, "Lead", "Tidy up", ongoing=True)
    await until(lambda: "K4001" in manager.fleet.units)
    scout = manager.fleet.units["K4001"]
    await manager.rename("Mapper", scout)
    await manager.rename("docs")
    await manager.tell(scout, "go on")
    await until(lambda: delivered(manager, events) == [("Mapper", "Lead", "report", "Mapped.")])
    await until(lambda: any("[Report from Mapper (K4001)]" in heard
                            for heard in harness.heard["Lead"]))
    await manager.aclose()
    saved = Fleet.load(tmp_path / "fleet")
    assert saved.name == "docs" and saved.units["K4001"].name == "Mapper"


async def test_the_person_messages_one_agent_and_its_supervisor_is_told(tmp_path: Path):
    harness = FakeHarness({
        "Lead": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Map the docs"})]},
                 {"when": "The person told Scout", "do": [("say", "Noted.")]}],
        "Scout": [{"do": [("say", "Mapping.")]},
                  {"when": "Skip the drafts", "do": [("say", "Skipping them.")]}]})
    manager, events = await run(tmp_path, harness)
    lead = await manager.add(None, "Lead", "Tidy up", ongoing=True)
    await until(lambda: harness.heard.get("Scout"))
    scout = manager.fleet.units["K4001"]
    await manager.say("Skip the drafts", scout)
    await until(lambda: len(harness.heard["Scout"]) == 2)
    heard = harness.heard["Scout"][1]  # it knows who spoke, and that it outranks its supervisor
    assert "Skip the drafts" in heard and "the person" in heard and "supervisor" in heard
    await until(lambda: any("The person told Scout (K4001)" in told and "Skip the drafts" in told
                            for told in harness.heard["Lead"]))
    await manager.say("Thanks")  # with nobody named: the lead
    await until(lambda: "Thanks" in harness.heard["Lead"][-1])
    assert [(e.text, e.to) for e in events if isinstance(e, Told)] == [
        ("Skip the drafts", scout.id), ("Thanks", lead.id)]
    await manager.aclose()
    kept = log.read(tmp_path / "fleet")
    assert [e.to for _, e in kept if isinstance(e, Told)] == ["K4001", "K5001"]


async def test_what_goes_down_the_chain_is_shown_too(tmp_path: Path):
    harness = FakeHarness({
        "Lead": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Map the docs"})]},
                 {"when": "[Question from Scout",
                  "do": [("tool", "message", {"agent": "K4001", "text": "Use docs/"})]}],
        "Scout": [{"do": [("ask", "Which folder?")]},
                  {"when": "Use docs/", "do": [("say", "On it.")]}]})
    manager, events = await run(tmp_path, harness)
    lead = await manager.add(None, "Lead", "Tidy up", ongoing=True)
    await until(lambda: len(harness.heard.get("Scout", [])) == 2)
    scout = manager.fleet.units["K4001"]
    down = [(e.by, e.unit, e.kind, e.text) for e in events if isinstance(e, Instructed)]
    assert down == [(None, lead.id, "task", "Tidy up"),  # None: the person
                    (lead.id, scout.id, "task", "Map the docs"),
                    (lead.id, scout.id, "message", "Use docs/")]
    await manager.add(scout, "", "Check the links")  # the person's, though under Scout
    await until(lambda: "K3001" in manager.fleet.units)
    assert [(e.by, e.unit) for e in events if isinstance(e, Instructed)][-1] == (None, "K3001")
    await manager.aclose()
    kept = [e for _, e in log.read(tmp_path / "fleet") if isinstance(e, Instructed)]
    assert [(e.by, e.unit) for e in kept] == [
        (None, "K5001"), ("K5001", "K4001"), ("K5001", "K4001"), (None, "K3001")]


async def test_files_go_with_a_message_to_the_agent_it_is_for(tmp_path: Path):
    harness = FakeHarness({
        "Lead": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Map the docs"})]}],
        "Scout": [{"do": [("say", "Mapping.")]}]})
    manager, events = await run(tmp_path, harness)
    await manager.add(None, "Lead", "Tidy up", ongoing=True)
    await until(lambda: harness.heard.get("Scout"))
    scout = manager.fleet.units["K4001"]
    await manager.say("Start from this", files=["/tmp/brief.pdf"])
    await manager.say("And this", scout, files=["/tmp/map.png"], spoken=True)
    assert harness.files == [("Lead", ["/tmp/brief.pdf"], False),
                             ("Scout", ["/tmp/map.png"], True)]
    await until(lambda: any("map.png" in heard for heard in harness.heard["Lead"]))  # told of it
    assert [(e.text, e.files) for e in events if isinstance(e, Told)] == [
        ("Start from this", ("/tmp/brief.pdf",)), ("And this", ("/tmp/map.png",))]
    # A first message that is an agent's task brings its files after the task.
    waiting = await manager.add(scout, "", "")
    await manager.say("Check the links", waiting, files=["/tmp/links.txt"])
    await until(lambda: harness.files[-1] == ("K3001", ["/tmp/links.txt"], False))
    await manager.aclose()


async def test_a_supervisor_dismisses_an_agent_it_will_not_use_again(tmp_path: Path):
    """The agent and those under it leave the fleet; what they spent stays counted, and
    the record still says who they were."""
    harness = FakeHarness({
        "Lead": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Map the docs"})]},
                 {"when": "[Report from Scout",
                  "do": [("tool", "dismiss", {"agent": "K4001"}), ("say", "Scout is let go.")]}],
        "Scout": [{"do": [("tool", "delegate", {"name": "Helper", "task": "Count pages"})]},
                  {"when": "[Report from Helper", "do": [("report", "Mapped.")]}],
        "Helper": [{"do": [("report", "12 pages.")]}]})
    manager, events = await run(tmp_path, harness)
    await manager.add(None, "Lead", "Tidy up", ongoing=True)
    await until(lambda: any(name == "dismiss" for _, name, _ in harness.results))
    assert ("Lead", "dismiss", "Dismissed Scout (K4001) and the 1 under it.") in harness.results
    assert list(manager.fleet.units) == ["K5001"]
    [gone] = [e for e in events if isinstance(e, Dismissed)]
    assert (gone.unit, gone.by, gone.gone) == ("K4001", "K5001", {"K4001": "Scout",
                                                                 "K3001": "Helper"})
    spent = manager.fleet.spent_usd
    assert spent > manager.fleet.units["K5001"].spent_usd  # theirs is still in the total
    assert not (manager.folder / "units" / "K4001").exists()
    # Nobody tells the lead what it did itself, and a second agent is a new one.
    assert not any("removed" in heard for heard in harness.heard["Lead"])
    with pytest.raises(FleetError):
        manager.fleet.subordinate(manager.fleet.units["K5001"], "K4001")
    await manager.aclose()
    assert Fleet.load(tmp_path / "fleet").spent_usd == spent


async def test_only_its_own_agents_are_a_supervisors_to_dismiss(tmp_path: Path):
    harness = FakeHarness({
        "Lead": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Map the docs"})]}],
        "Scout": [{"do": [("tool", "dismiss", {"agent": "K5001"}), ("say", "Tried.")]}]})
    manager, _ = await run(tmp_path, harness)
    await manager.add(None, "Lead", "Tidy up", ongoing=True)
    await until(lambda: any(name == "dismiss" for _, name, _ in harness.results))
    [(_, _, said)] = [r for r in harness.results if r[1] == "dismiss"]
    assert "You have no agent" in said and list(manager.fleet.units) == ["K5001", "K4001"]
    await manager.aclose()


async def test_an_agent_is_given_another_task_and_an_agent_is_removed(tmp_path: Path):
    harness = FakeHarness({
        "Lead": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Map the docs"})]}],
        "Scout": [{"do": [("tool", "delegate", {"name": "Helper", "task": "Count pages"})]},
                  {"when": "task was changed", "do": [("say", "Mapping the code now.")]}],
        "Helper": [{"do": [("say", "Counting.")]}]})
    manager, events = await run(tmp_path, harness)
    lead = await manager.add(None, "Lead", "Tidy up", ongoing=True)
    await until(lambda: harness.heard.get("Helper"))
    scout, helper = manager.fleet.units["K4001"], manager.fleet.units["K3001"]

    await manager.retask(scout, "Map the code")
    await until(lambda: any("task was changed" in heard for heard in harness.heard["Scout"]))
    [(name, instruction)] = harness.instructed  # its standing instruction, not only a message
    assert name == "Scout" and "Your task: Map the code" in instruction
    assert scout.task == "Map the code" and scout.session == "session-K4001"  # same conversation
    assert [(e.by, e.unit, e.text) for e in events if isinstance(e, Instructed)][-1] == (
        None, scout.id, "Map the code")
    await until(lambda: any("changed the task of Scout (K4001)" in heard and "Map the code" in heard
                            for heard in harness.heard["Lead"]))
    with pytest.raises(FleetError, match="empty"):
        await manager.retask(scout, "  ")
    # A task becomes a duty, or a duty a task, with the task: its instruction says which.
    assert not scout.ongoing and "standing work" not in harness.instructed[-1][1]
    await manager.retask(scout, "Keep the map current", ongoing=True)
    assert scout.ongoing and "standing work" in harness.instructed[-1][1]
    await manager.retask(scout, "Keep the map current")  # unsaid: as it is
    assert scout.ongoing and "standing work" in harness.instructed[-1][1]
    waiting = await manager.add(lead, "", "")  # with no task: it begins on the one given
    await manager.retask(waiting, "Check the links")
    await until(lambda: any("Your task: Check the links" in heard
                            for heard in harness.heard.get("K4002", [])))

    await until(lambda: any("changed the task of agent K4002" in heard
                            for heard in harness.heard["Lead"]))  # its supervisor knows

    (tmp_path / "fleet" / "units" / "K3001").mkdir(parents=True, exist_ok=True)
    await until(lambda: all(  # every turn has ended
        unit.spent_usd == pytest.approx(0.01 * len(harness.heard.get(unit.called, [])))
        for unit in manager.fleet.units.values()))
    spent = manager.fleet.spent_usd
    await manager.remove(scout)  # with everything under it
    assert list(manager.fleet.units) == ["K5001", "K4002"]
    # What they spent still counts against the fleet's limit.
    assert manager.fleet.spent_usd == pytest.approx(spent)
    assert manager.fleet.spent_usd > sum(u.spent_usd for u in manager.fleet.units.values())
    assert not (tmp_path / "fleet" / "units" / "K3001").exists()
    await until(lambda: any("removed Scout (K4001)" in heard for heard in harness.heard["Lead"]))
    with pytest.raises(FleetError):
        await manager.tell(helper, "still there?")
    again = await manager.add(lead, "Scout", "Map the docs again")
    assert again.id == "K4003"  # a number is not given twice
    await manager.aclose()
    kept = Fleet.load(tmp_path / "fleet")
    assert list(kept.units) == ["K5001", "K4002", "K4003"]
    assert kept.spent_usd > sum(u.spent_usd for u in kept.units.values())  # and is kept


async def test_what_a_unit_says_while_it_is_closed_changes_nothing_but_its_spending(
        tmp_path: Path):
    class Lingering(FakeHarness):  # keeps what its unit speaks through
        async def open(self, spec, on_event, session=None):
            self.emit = on_event
            return await super().open(spec, on_event, session)

    harness = Lingering({"Lead": [{"do": [("say", "On watch.")]}]})
    manager, events = await run(tmp_path, harness)
    lead = await manager.add(None, "Lead", "Keep watch", ongoing=True)
    await until(lambda: len(events) > 3 and lead.spent_usd > 0)
    await manager.stop_unit(lead)
    before = list(events)
    for late in (api.Working(), api.Said("Still here."),
                 api.Rested(), api.Failed("Broke."), api.Exited("Gone.")):
        harness.emit(late)
    assert (lead.state, lead.report, lead.failure) == ("stopped", None, None)
    assert events == before
    harness.emit(api.Spent(0.5))  # what it spent is still what it spent
    assert lead.spent_usd == 0.5
    await manager.aclose()


async def test_a_failure_in_the_managers_own_work_is_seen(tmp_path: Path, caplog):
    harness = FakeHarness({"Lead": [{"do": [("say", "On watch.")]}]})
    manager, events = await run(tmp_path, harness)
    await manager.add(None, "Lead", "Keep watch", ongoing=True)

    async def broken() -> None:
        raise RuntimeError("disk on fire")

    manager._spawn(broken())
    await until(lambda: any(isinstance(e, Notice) and "disk on fire" in e.text for e in events))
    assert "disk on fire" in caplog.text  # with where it happened, for whoever reads the log

    # A record that cannot be written is such a failure, not a silence.
    (tmp_path / "fleet" / "fleet.tmp").mkdir()  # in the way of the next write
    manager._changed()
    await until(lambda: any(isinstance(e, Notice) and "IsADirectoryError" in e.text
                            for e in events))
    with pytest.raises(OSError):  # and whoever asked for the change is told
        await manager.rename("watch")
    (tmp_path / "fleet" / "fleet.tmp").rmdir()
    await manager.aclose()
    assert Fleet.load(tmp_path / "fleet").name == "watch"


async def until(predicate, timeout: float = 10.0) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


async def test_kaden_says_which_models_it_offers(tmp_path: Path, monkeypatch):
    """Asked of a Kaden started for that alone, with the configuration units get."""
    import tomli_w

    from kfm import harnesses

    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    base = tmp_path / "kaden.toml"
    base.write_text(tomli_w.dumps({
        "core": {"factory": "tests.fakecore:factory", "options": {"scripts": {}}},
        "runtime": {"model": "fake-1"}}))
    harness = harnesses.load({"config": str(base)})
    assert await harness.model_choices(tmp_path / "models") == [
        ("fake-1", "fake-1", "$1 in, $2 out per million tokens"),
        ("fake-2", "fake-2", "$1 in, $2 out per million tokens")]
