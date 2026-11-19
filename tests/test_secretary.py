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
        {"do": [("tool", "propose", {"teams": TEAMS}), ("report", "Proposed.")]}]})
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
    await until(lambda: service.fleet(made.secretary).lead.state == "done")
    await service.settle_proposal(made.id)
    assert await service.proposal(made.id) is None
    [listed] = await service.projects()
    assert (listed.fleets, listed.running, listed.secretary) == (0, 0, made.secretary)
    await service.aclose()


async def test_a_secretary_sleeps_until_she_is_written_to(tmp_path: Path):
    service, harness, seen = local(tmp_path, {
        "Secretary": [{"when": "How is it going", "do": [("tool", "teams", {}),
                                                         ("report", "All well.")]},
                      {"when": "Tell dev", "do": [
                          ("tool", "message", {"team": "dev", "text": "Ship on Friday."}),
                          ("tool", "message", {"team": "art", "text": "Hello."}),
                          ("report", "Told.")]}],
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


async def test_leads_reach_each_other_and_the_secretary(tmp_path: Path):
    service, harness, seen = local(tmp_path, {
        "K5 u1": [{"when": "Build it", "do": [
                      ("tool", "status", {}),
                      ("tool", "message", {"agent": "art", "text": "Which palette?"}),
                      ("tool", "message", {"agent": "secretary", "text": "We disagree."}),
                      ("tool", "message", {"agent": "legal", "text": "Anyone?"})]},
                  {"when": "Draw it", "do": [("say", "Drawing.")]},
                  {"when": "Which palette", "do": [("say", "Blue.")]}],
        "Secretary": [{"do": [("report", "I will tell the person.")]}]})
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

    await service.stop(art)  # a team that does not run is not started by a message
    harness.scripts["K5 u1"] = [{"when": "Try again", "do": [
        ("tool", "message", {"agent": "art", "text": "Still there?"})]}]
    await service.say(dev, "Try again")
    await until(lambda: len([r for r in harness.results if r[1] == "message"]) == 4)
    assert "not running" in harness.results[-1][2]
    assert not service.state(art).running
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
    await service.aclose()


async def test_a_proposal_reaches_a_frontend_over_the_socket(tmp_path: Path):
    import asyncio

    from kfm.remote import RemoteService
    from kfm.server import Server

    harness = FakeHarness({"Secretary": [
        {"do": [("tool", "propose", {"teams": TEAMS}), ("report", "Proposed.")]}]})
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
