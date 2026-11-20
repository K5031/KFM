"""A project's secretary and the ways between its teams, on the second harness."""

from __future__ import annotations

from pathlib import Path

from kfm.config import FleetConfig
from kfm.events import Across, Told
from kfm.local import LocalService
from kfm.service import Happened, Proposed, Update
from tests.fakeharness import FakeHarness
from tests.test_service import until

TEAMS = [{"name": "dev", "does": "Build the game.", "ongoing": True},
         {"name": "trailer", "does": "Cut a trailer.", "ongoing": False}]


def local(tmp_path: Path, scripts) -> tuple[LocalService, FakeHarness, list[Update]]:
    harness = FakeHarness(scripts)
    (tmp_path / "work").mkdir(exist_ok=True)
    service = LocalService(tmp_path / "home", harness=harness, config=FleetConfig())
    seen: list[Update] = []
    service.subscribe(seen.append)
    return service, harness, seen


def across(service: LocalService, seen: list[Update], fleet: str) -> list[tuple[str, str, bool]]:
    return [(u.event.team, u.event.text, u.event.sent) for u in seen
            if isinstance(u, Happened) and u.fleet == fleet and isinstance(u.event, Across)]


async def test_a_described_project_gets_its_teams_from_its_secretary(tmp_path: Path):
    service, harness, seen = local(tmp_path, {"Secretary": [
        {"do": [("tool", "propose", {"teams": TEAMS}), ("say", "Three teams, as proposed.")]}]})
    made = await service.create_project("Studio", str(tmp_path / "work"), "Run my game studio")
    assert made.secretary == f"{made.id}/secretary" and (made.fleets, made.running) == (0, 0)
    await until(lambda: Proposed(made.id) in seen)
    proposed = await service.proposal(made.id)
    assert [(t.name, t.does, t.ongoing) for t in proposed.teams] == [
        ("dev", "Build the game.", True), ("trailer", "Cut a trailer.", False)]
    assert await service.fleets(made.id) == []  # nothing exists of it: she is no team
    [told] = harness.heard["Secretary"]
    assert "the secretary of Studio" in told and "Run my game studio" in told
    assert "fleet.propose" in told and "fleet.teams" in told and "fleet.message" in told
    # She is told what is so; how to be a secretary is her own. That she does none of
    # the work is what she is able to do: she reads, and nothing else is allowed.
    for instruction in ("you must", "you should", "you do not", "as few", "make sure",
                        "your job", "always", "never"):
        assert instruction not in told.lower(), instruction
    assert harness.permissions == [("Secretary", "allow_read_only")]
    # She is a conversation: she answers and stops. Nothing is finished or reported, so
    # what she wrote is the one answer the person reads.
    await until(lambda: service.fleet(made.secretary).lead.state == "done")
    assert "ask_boss" not in told and harness.conversations == ["Secretary"]
    history, _ = await service.history(made.secretary)
    assert [type(e).__name__ for e in history if type(e).__name__ in ("Said", "Delivered")] \
        == ["Said"]
    await service.settle_proposal(made.id)
    assert await service.proposal(made.id) is None
    [listed] = await service.projects()
    assert (listed.fleets, listed.running, listed.secretary) == (0, 0, made.secretary)
    await service.aclose()


async def test_a_secretary_sleeps_until_she_is_written_to(tmp_path: Path):
    service, harness, seen = local(tmp_path, {
        "Secretary": [{"when": "How is it going", "do": [("tool", "teams", {}),
                                                         ("say", "All well.")]},
                      {"when": "Tell dev", "do": [
                          ("tool", "message", {"team": "dev", "text": "Ship on Friday."}),
                          ("tool", "message", {"team": "art", "text": "Hello."}),
                          ("say", "Told.")]}],
        "K5 u1": [{"do": [("report", "The build is green.")]},
                  {"when": "Ship on Friday", "do": [("say", "Noted.")]}]})
    made = await service.create_project("Studio", str(tmp_path / "work"))
    assert not harness.opened  # nothing was said to her: nothing runs
    dev = await service.add_fleet(made.id, "dev", "Keep the build green", ongoing=False)
    await until(lambda: service.state(dev).finished)
    await service.say(dev, "Well done")
    await until(lambda: any("Well done" in heard for heard in harness.heard["K5 u1"]))

    await service.say(made.secretary, "How is it going?")
    await until(lambda: [r for r in harness.results if r[1] == "teams"])
    [(_, _, standing)] = [r for r in harness.results if r[1] == "teams"]
    assert "dev:" in standing and "Keep the build green" in standing
    assert "to the person, report: The build is green." in standing
    assert "The person to K5 agent u1: Well done" in standing  # what was said without her
    await until(lambda: service.fleet(made.secretary).lead.state == "done")

    await service.say(made.secretary, "Tell dev we ship on Friday")
    await until(lambda: any("Ship on Friday" in heard for heard in harness.heard["K5 u1"]))
    heard = next(h for h in harness.heard["K5 u1"] if "Ship on Friday" in h)
    assert heard == "[Message from the project's secretary] Ship on Friday."
    await until(lambda: len([r for r in harness.results if r[1] == "message"]) == 2)
    sent, refused = [r[2] for r in harness.results if r[1] == "message"]
    assert sent == "Sent to dev." and "no team" in refused and "dev" in refused
    assert across(service, seen, made.secretary) == [("dev", "Ship on Friday.", True)]
    assert across(service, seen, dev) == [("Secretary", "Ship on Friday.", False)]
    history, _ = await service.history(made.secretary)
    assert [e.text for e in history if isinstance(e, Told)] == [
        "How is it going?", "Tell dev we ship on Friday"]
    await service.aclose()


def test_a_proposed_team_is_a_task_unless_she_says_it_is_standing_work():
    from kfm.draft import Team

    assert not Team(name="dev", does="Build it.").ongoing
    assert Team(name="support", does="Answer mail.", ongoing=True).ongoing


async def test_the_secretarys_message_starts_a_team_that_is_stopped(tmp_path: Path):
    service, harness, _ = local(tmp_path, {
        "Secretary": [{"do": [("tool", "message", {"team": "dev", "text": "Carry on."}),
                              ("say", "It is back.")]}],
        "K5 u1": [{"do": [("say", "Building.")]},
                  {"when": "Carry on", "do": [("say", "On it.")]}]})
    made = await service.create_project("Studio", str(tmp_path / "work"))
    dev = await service.add_fleet(made.id, "dev", "Build it")
    await until(lambda: "K5 u1" in harness.heard)
    await service.stop(dev)
    assert not service.state(dev).running

    await service.say(made.secretary, "Get dev going again")
    await until(lambda: [r for r in harness.results if r[1] == "message"])
    assert harness.results[-1][2] == "Sent to dev."
    assert service.state(dev).running
    await until(lambda: "[Message from the project's secretary] Carry on."
                in harness.heard["K5 u1"])
    await service.aclose()


async def test_leads_reach_each_other_and_the_secretary(tmp_path: Path):
    service, harness, seen = local(tmp_path, {
        "K5 u1": [{"when": "Build it", "do": [
                      ("tool", "status", {}),
                      ("tool", "message", {"agent": "art", "text": "Which palette?"}),
                      ("tool", "message", {"agent": "secretary", "text": "We disagree."}),
                      ("tool", "message", {"agent": "legal", "text": "Anyone?"})]},
                  {"when": "Draw it", "do": [("say", "Drawing.")]},
                  {"when": "Which palette", "do": [("say", "Blue.")]}],
        "Secretary": [{"do": [("say", "I will tell the person.")]}]})
    made = await service.create_project("Studio", str(tmp_path / "work"))
    art = await service.add_fleet(made.id, "art", "Draw it")
    await until(lambda: any("Draw it" in h for h in harness.heard.get("K5 u1", [])))
    dev = await service.add_fleet(made.id, "dev", "Build it")
    await until(lambda: len([r for r in harness.results if r[1] == "message"]) == 3)
    brief = next(h for h in harness.heard["K5 u1"] if "Build it" in h)
    assert "one team of a project" in brief and "fleet.message" in brief
    [status] = [r[2] for r in harness.results if r[1] == "status"]
    assert "The project's other teams:" in status and "- art (running): Draw it" in status
    to_art, to_secretary, to_nobody = [r[2] for r in harness.results if r[1] == "message"]
    assert (to_art, to_secretary) == ("Sent to art.", "Sent to Secretary.")
    assert "no team" in to_nobody and "art" in to_nobody
    assert any(h == "[Message from the lead of the team dev] Which palette?"
               for h in harness.heard["K5 u1"])
    assert across(service, seen, art) == [("dev", "Which palette?", False)]
    assert across(service, seen, dev) == [("art", "Which palette?", True),
                                         ("Secretary", "We disagree.", True)]
    # It woke the secretary, who had not begun: what a lead says is what she starts on.
    await until(lambda: "Secretary" in harness.heard)
    assert "[Message from the lead of the team dev] We disagree." in harness.heard["Secretary"][0]

    await service.stop(art)  # a team that does not run is started by a message
    assert not service.state(art).running
    harness.scripts["K5 u1"] = [{"when": "Try again", "do": [
        ("tool", "message", {"agent": "art", "text": "Still there?"})]}]
    await service.say(dev, "Try again")
    await until(lambda: len([r for r in harness.results if r[1] == "message"]) == 4)
    assert harness.results[-1][2] == "Sent to art."
    assert service.state(art).running
    await until(lambda: any(h == "[Message from the lead of the team dev] Still there?"
                            for h in harness.heard["K5 u1"]))
    await service.aclose()


async def test_a_real_kadence_unit_is_the_secretary_on_a_scripted_model(tmp_path: Path,
                                                                        monkeypatch):
    import tomli_w

    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    base = tmp_path / "kadence.toml"
    base.write_text(tomli_w.dumps({
        "core": {"factory": "tests.fakecore:factory", "options": {"scripts": {"Secretary": [
            {"calls": [{"name": "mcp__fleet__propose", "arguments": {"teams": TEAMS}}]}]}}},
        "runtime": {"model": "fake-1"}}))
    (tmp_path / "work").mkdir()
    service = LocalService(tmp_path / "home", config=FleetConfig(
        harness_options={"config": str(base)}))
    seen: list[Update] = []
    service.subscribe(seen.append)
    made = await service.create_project("Studio", str(tmp_path / "work"), "Run my game studio")
    await until(lambda: Proposed(made.id) in seen, timeout=30)
    assert [team.name for team in (await service.proposal(made.id)).teams] == ["dev", "trailer"]
    # What a Kadence agent does is seen as it does it: the tool it used, in words.
    from kfm.service import Live
    await until(lambda: any(isinstance(u, Live) and u.doing.startswith("propose ")
                            for u in seen), timeout=30)
    # Her turn ends as a conversation's does: nothing to finish, and no report.
    await until(lambda: service.fleet(made.secretary).lead.state == "done", timeout=30)
    history, _ = await service.history(made.secretary)
    assert "Delivered" not in [type(event).__name__ for event in history]
    await service.aclose()


async def test_a_proposal_reaches_a_frontend_over_the_socket(tmp_path: Path):
    import asyncio

    from kfm.remote import RemoteService
    from kfm.server import Server

    harness = FakeHarness({"Secretary": [
        {"do": [("tool", "propose", {"teams": TEAMS}), ("say", "Three teams, as proposed.")]}]})
    (tmp_path / "work").mkdir()
    home, socket = tmp_path / "home", tmp_path / "s"
    server = Server(LocalService(home, harness=harness, config=FleetConfig()), socket,
                    idle_s=0.3)
    serving = asyncio.ensure_future(server.run())
    await until(socket.exists)
    remote = await RemoteService.connect(home, socket=socket, start=False)
    seen: list[Update] = []
    remote.subscribe(seen.append)
    made = await remote.create_project("Studio", str(tmp_path / "work"), "Run my game studio")
    assert made.secretary == f"{made.id}/secretary"
    await until(lambda: Proposed(made.id) in seen)
    proposed = await remote.proposal(made.id)
    assert [(t.name, t.ongoing, t.folder) for t in proposed.teams] == [
        ("dev", True, None), ("trailer", False, None)]
    await remote.watch(made.secretary)
    await until(lambda: remote.fleet(made.secretary).lead.state == "done")
    await remote.settle_proposal(made.id)
    assert await remote.proposal(made.id) is None
    await remote.aclose()
    await asyncio.wait_for(serving, 10)


async def test_a_team_with_a_folder_of_its_own_finds_it_there(tmp_path: Path):
    """The secretary may propose a folder inside the project's that does not exist yet:
    it is made with the team. A folder elsewhere has to be there."""
    import pytest

    service, _, _ = local(tmp_path, {"K5 u1": [{"do": [("say", "Drawing.")]}]})
    work = tmp_path / "work"
    made = await service.create_project("Studio", str(work))
    fleet = await service.add_fleet(made.id, "art", "Draw it", work=str(work / "assets" / "art"))
    assert (work / "assets" / "art").is_dir()
    assert service.fleet(fleet).work == str(work / "assets" / "art")
    with pytest.raises(ValueError, match="There is no folder"):
        await service.add_fleet(made.id, "elsewhere", "Go", work=str(tmp_path / "nowhere"))
    assert not (tmp_path / "nowhere").exists()
    await service.aclose()


async def test_a_secretary_runs_on_the_model_set_for_secretaries(tmp_path: Path):
    """Else on the top rank's, as she is of that rank."""
    for models, expected in (({"secretary": "small-1", "K5": "big-1"}, "small-1"),
                             ({"K5": "big-1"}, "big-1")):
        harness = FakeHarness({"Secretary": [{"do": [("say", "Hello.")]}]})
        home = tmp_path / expected
        (home / "work").mkdir(parents=True)
        service = LocalService(home, harness=harness, config=FleetConfig(models=models))
        await service.create_project("Studio", str(home / "work"), "Run my studio")
        await until(lambda harness=harness: harness.models)
        assert harness.models == [("Secretary", expected)]
        await service.aclose()


async def test_in_auto_the_secretary_names_the_model_of_each_teams_lead(tmp_path: Path):
    """She starts the teams, through what she proposes, so their leads' models are hers
    to name; her own is the person's, since nobody starts her."""
    plain = [{"name": "dev", "does": "Build the game."}]
    named = [{"name": "dev", "does": "Build the game.", "model": "big-1"},
             {"name": "art", "does": "Draw it.", "model": "small-1"}]
    harness = FakeHarness({"Secretary": [{"do": [
        ("tool", "propose", {"teams": plain}), ("tool", "propose", {"teams": named})]}]})
    (tmp_path / "work").mkdir()
    service = LocalService(tmp_path / "home", harness=harness, config=FleetConfig(
        model_mode="auto", auto_models=("small-1", "big-1"), models={"secretary": "small-1"}))
    seen: list[Update] = []
    service.subscribe(seen.append)
    made = await service.create_project("Studio", str(tmp_path / "work"), "Run my studio")
    await until(lambda: Proposed(made.id) in seen)
    assert harness.models == [("Secretary", "small-1")]
    await until(lambda: len([r for r in harness.results if r[1] == "propose"]) == 2)
    refused, shown = [r[2] for r in harness.results if r[1] == "propose"]
    assert "dev: Name the model it runs on, one of: small-1, big-1" in refused
    assert "Shown to the person" in shown
    proposed = await service.proposal(made.id)
    assert [(team.name, team.model) for team in proposed.teams] == [
        ("dev", "big-1"), ("art", "small-1")]
    await service.aclose()
