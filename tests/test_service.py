"""The service a frontend talks to (kfm.service), in this process and over its socket."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kfm.config import FleetConfig
from kfm.events import Delivered, Said, Told, units
from kfm.fleet import Fleet, FleetError
from kfm.local import LocalService
from kfm.remote import RemoteService, ServiceError
from kfm.server import Server
from kfm.service import Happened, Listed, Live, Service, Update, Updated
from tests.fakeharness import FakeHarness

TEAM = {
    "K5 u1": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Tidy the docs"})]},
              {"when": "[Report from Scout", "do": [("report", "Docs are tidy.")]}],
    "Scout": [{"do": [("report", "Tidied docs/")]}]}


async def until(predicate, timeout: float = 10.0) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), timeout)


def local(tmp_path: Path, scripts) -> tuple[LocalService, FakeHarness, list[Update]]:
    harness = FakeHarness(scripts)
    (tmp_path / "work").mkdir(exist_ok=True)
    service = LocalService(tmp_path / "home", harness=harness, config=FleetConfig())
    seen: list[Update] = []
    service.subscribe(seen.append)
    return service, harness, seen


def reports(service: Service, seen: list[Update]) -> list[tuple[str, str]]:
    """What was delivered, with the names the fleets' records give the units."""
    return [(service.fleet(u.fleet).units[u.event.unit].called, u.event.text) for u in seen
            if isinstance(u, Happened) and isinstance(u.event, Delivered)]


async def test_a_project_and_a_fleet_through_the_service(tmp_path: Path):
    service, harness, seen = local(tmp_path, TEAM)
    assert await service.projects() == []
    made = await service.create_project("Acme", str(tmp_path / "work"))
    assert (made.name, made.fleets, made.running) == ("Acme", 0, 0) and Listed() in seen
    fleet = await service.add_fleet(made.id, "dev", "Tidy up", ongoing=False, budget_usd=3.0)
    assert fleet.startswith(f"{made.id}/") and await service.fleets(made.id) == [fleet]
    await until(lambda: reports(service, seen) == [("Scout", "Tidied docs/"),
                                                   ("K5 u1", "Docs are tidy.")])
    await until(lambda: service.state(fleet).finished)
    record = service.fleet(fleet)
    assert [(u.called, u.state) for u in record.units.values()] == [("K5 u1", "done"),
                                                                     ("Scout", "done")]
    assert service.state(fleet).running and service.state(fleet).budget_usd == 3.0
    assert service.state(fleet).status == "finished"  # its lead reported, nothing is at work
    assert Updated(fleet) in seen
    [listed] = await service.projects()
    assert (listed.fleets, listed.running) == (1, 1)

    await service.say(fleet, "Thanks")  # to the lead
    await until(lambda: any("Thanks" in heard for heard in harness.heard["K5 u1"]))
    await service.stop(fleet)
    assert not service.state(fleet).running and (await service.projects())[0].running == 0
    stopped = service.state(fleet)  # the lead, told more, was at work again
    assert (stopped.status, stopped.can_start, stopped.has_run) == ("stopped", True, True)
    history, upto = await service.history(fleet)
    kinds = [type(event) for event in history]
    assert Told in kinds and Delivered in kinds
    happened = [u for u in seen if isinstance(u, Happened)]
    assert [u.number for u in happened] == list(range(1, upto + 1))  # each has its place
    assert history == [u.event for u in happened]

    await service.rename_project(made.id, "Globex")
    await service.rename_fleet(fleet, "docs")
    assert (await service.projects())[0].name == "Globex" and service.fleet(fleet).name == "docs"
    with pytest.raises(ValueError):
        await service.watch("../../etc")
    await service.delete_fleet(fleet)
    assert await service.fleets(made.id) == [] and service.fleet(fleet) is None
    await service.delete_project(made.id)
    assert await service.projects() == [] and (tmp_path / "work").is_dir()
    await service.aclose()


async def test_a_fleet_is_built_and_changed_through_the_service(tmp_path: Path):
    service, harness, _ = local(tmp_path, {
        "Chief": [{"do": [("say", "On watch.")]}],
        "Writer": [{"do": [("say", "Writing.")]},
                   {"when": "task was changed", "do": [("say", "Editing now.")]}]})
    made = await service.create_project("Acme", str(tmp_path / "work"))
    fleet = await service.add_fleet(made.id, "content", "")  # nothing it should do: by hand
    assert not service.state(fleet).running and not service.fleet(fleet).units
    empty = service.state(fleet)
    assert (empty.status, empty.can_start, empty.has_run) == ("not started", False, False)
    chief = await service.add_agent(fleet, None, "Chief", "Run content", ongoing=True)
    assert service.state(fleet).can_start and service.state(fleet).status == "not started"
    writer = await service.add_agent(fleet, chief, "Writer", "Write the posts")
    await service.set_model(fleet, chief, "big-1")
    await service.rename_agent(fleet, writer, "Author")
    await service.rename_agent(fleet, writer, "Writer")
    await service.set_task(fleet, writer, "Write the long posts")
    assert not harness.models  # nothing has run
    await service.start(fleet)
    await until(lambda: harness.models == [("Chief", "big-1"), ("Writer", None)])
    running = service.state(fleet)
    assert (running.status, running.can_start, running.has_run) == ("running", False, True)
    await until(lambda: harness.heard.get("Writer"))
    assert "Your task: Write the long posts" in harness.heard["Writer"][0]

    await service.set_task(fleet, writer, "Edit the posts")  # while it runs: it is told
    await until(lambda: any("task was changed" in heard for heard in harness.heard["Writer"]))
    with pytest.raises(FleetError, match="no agent"):
        await service.set_model(fleet, "u9", "x")
    await service.say(fleet, "Shorter ones", writer)
    await until(lambda: any("Shorter ones" in heard for heard in harness.heard["Writer"]))
    assert any(units(event) == {writer} for event in (await service.history(fleet))[0])
    await service.remove_agent(fleet, writer)
    assert list(service.fleet(fleet).units) == [chief]
    history, upto = await service.history(fleet)  # what was about it went with it
    assert history and not any(writer in units(event) for event in history)
    assert upto > len(history)  # the events keep their numbers
    await service.stop(fleet)
    assert service.state(fleet).status == "stopped" and service.state(fleet).can_start
    await service.set_task(fleet, chief, "Run the blog")  # one that has run, while stopped
    assert service.fleet(fleet).units[chief].task == "Run the blog" and not harness.instructed[1:]
    await service.say(fleet, "Still there?")  # a message starts a stopped fleet
    await until(lambda: service.state(fleet).running)
    assert any(name == "Chief" and "Your task: Run the blog" in instruction
               for name, instruction in harness.instructed[1:])  # as it was started
    await service.aclose()
    assert list(Fleet.load(tmp_path / "home" / "projects" / made.id / "fleets"
                           / fleet.split("/")[1]).units) == [chief]


async def test_settings_and_a_draft_through_the_service(tmp_path: Path):
    harness = FakeHarness({"Secretary": [{"do": [("tool", "propose", {"teams": [
        {"name": "dev", "does": "Build the game"}, {"name": "art", "does": "Draw it"}]})]}]})
    (tmp_path / "work").mkdir()
    service = LocalService(tmp_path / "home", harness=harness)  # settings from its home
    assert (await service.settings()).budget_usd == 10.0
    saved = await service.save_settings(budget_usd=25.0, models={"K5": "big-1"})
    assert saved == await service.settings() and saved.models == {"K5": "big-1"}
    made = await service.create_project("Studio", str(tmp_path / "work"), "Run my game studio")
    await until(lambda: made.id in service._proposals)
    proposed = await service.proposal(made.id)
    assert [(team.name, team.does) for team in proposed.teams] == [
        ("dev", "Build the game"), ("art", "Draw it")]
    assert await service.fleets(made.id) == []  # nothing was started
    await service.aclose()


async def test_two_frontends_on_one_service_over_its_socket(tmp_path: Path):
    harness = FakeHarness({
        "K5 u1": [{"do": [("tool", "delegate", {"name": "Scout", "task": "Tidy the docs"})]},
                  {"when": "hello", "do": [("say", "Hi there.")]}],
        "Scout": [{"do": [("say", "Tidying.")]}]})
    (tmp_path / "work").mkdir()
    home, socket = tmp_path / "home", tmp_path / "s"
    server = Server(LocalService(home, harness=harness, config=FleetConfig()), socket,
                    idle_s=0.3)
    serving = asyncio.ensure_future(server.run())
    await until(socket.exists)
    with pytest.raises(RuntimeError, match="already runs"):
        await Server(LocalService(home), socket).run()

    first = await RemoteService.connect(home, socket=socket, start=False)
    seen: list[Update] = []
    strangers: list[Update] = []  # events that name a unit the record does not have yet
    first.subscribe(seen.append)
    first.subscribe(lambda u: isinstance(u, Happened) and not units(u.event) <= first.fleet(
        u.fleet).units.keys() and strangers.append(u))
    made = await first.create_project("Acme", str(tmp_path / "work"))
    fleet = await first.add_fleet(made.id, "dev", "Tidy up", budget_usd=3.0)
    await until(lambda: first.fleet(fleet) is not None and len(first.fleet(fleet).units) == 2)
    await until(lambda: any(isinstance(u, Happened) and isinstance(u.event, Said)
                            and u.event.unit == "u2" for u in seen))
    assert first.state(fleet).running and first.state(fleet).budget_usd == 3.0
    assert first.state(fleet).status == "running" and first.state(fleet).has_run
    assert Listed() in seen and Updated(fleet) in seen

    # A second frontend finds the fleet, with what happened before it came.
    second = await RemoteService.connect(home, socket=socket, start=False)
    later: list[Update] = []
    second.subscribe(later.append)
    [listed] = await second.projects()
    assert (listed.name, listed.fleets, listed.running) == ("Acme", 1, 1)
    assert await second.fleets(made.id) == [fleet]
    assert [u.called for u in second.fleet(fleet).units.values()] == ["K5 u1", "Scout"]
    history, upto = await second.history(fleet)
    assert "Instructed" in [type(event).__name__ for event in history] and upto >= len(history)

    await second.say(fleet, "hello")  # what one says, both see
    for watching in (seen, later):
        await until(lambda w=watching: any(
            isinstance(u, Happened) and isinstance(u.event, Said)
            and u.event.text == "Hi there." for u in w))
    assert sum(1 for u in seen if isinstance(u, Happened) and isinstance(u.event, Said)
               and u.event.text == "Hi there.") == 1  # once
    assert all(u.number > upto for u in later if isinstance(u, Happened))  # after the history
    assert not strangers  # a frontend's record had every unit an event named
    assert first.fleet(fleet).spent_usd > 0  # spending is a change like any other
    await second.rename_agent(fleet, "u2", "Tidier")
    await until(lambda: second.fleet(fleet).units["u2"].name == "Tidier"
                and first.fleet(fleet).units["u2"].name == "Tidier")
    with pytest.raises(FleetError, match="no agent"):
        await first.set_model(fleet, "u9", "x")
    assert (await first.settings()).budget_usd == 10.0

    # Letting go of a frontend leaves the fleet running; the service ends when none does.
    await first.aclose()
    await asyncio.sleep(0.5)
    assert not serving.done() and second.state(fleet).running
    await second.stop(fleet)
    await until(lambda: not second.state(fleet).running)
    await second.aclose()
    await asyncio.wait_for(serving, 10)
    assert not socket.exists()
    assert Fleet.load(home / "projects" / made.id / "fleets" / fleet.split("/")[1]).name == "dev"


async def test_a_service_of_another_version_is_refused_and_named(tmp_path: Path):
    socket = tmp_path / "s"

    async def old(reader, writer):  # speaks a version this program does not
        writer.write(b'{"kfm": 0, "pid": 4242}\n')
        await reader.read()
        writer.close()

    server = await asyncio.start_unix_server(old, path=str(socket))
    started = asyncio.get_running_loop().time()
    with pytest.raises(ServiceError, match="kill 4242") as refused:
        await RemoteService.connect(tmp_path, socket=socket, start=False)
    assert asyncio.get_running_loop().time() - started < 1.0  # at once: it is not waited out
    assert "version 0" in str(refused.value) and "can be resumed" in str(refused.value)
    server.close()


async def test_the_service_is_ended_by_a_frontend(tmp_path: Path):
    """Its fleets stop and can be resumed; the next frontend starts another."""
    harness = FakeHarness({"K5 u1": [{"do": [("say", "On watch.")]}]})
    (tmp_path / "work").mkdir()
    home, socket = tmp_path / "home", tmp_path / "s"
    server = Server(LocalService(home, harness=harness, config=FleetConfig()), socket,
                    idle_s=60)
    serving = asyncio.ensure_future(server.run())
    await until(socket.exists)
    remote = await RemoteService.connect(home, socket=socket, start=False)
    assert remote.pid > 0 and remote.since > 0
    made = await remote.create_project("Acme", str(tmp_path / "work"))
    fleet = await remote.add_fleet(made.id, "watch", "Keep watch")
    await until(lambda: remote.state(fleet).running)
    await remote.end()  # returns once its fleets are stopped and it has let go
    await asyncio.wait_for(serving, 10)
    assert not socket.exists()
    assert not Fleet.load(home / "projects" / made.id / "fleets"
                          / fleet.partition("/")[2]).running
    with pytest.raises(ServiceError, match="No KFM service runs"):
        await RemoteService.connect(home, socket=socket, start=False)


async def test_a_frontend_knows_a_service_started_before_the_code_it_runs(tmp_path: Path):
    """The service outlives the window: after an update it runs what it was started on."""
    from kfm.remote import installed_at

    home, socket = tmp_path / "home", tmp_path / "s"
    for since, stale in ((installed_at() - 60, True), (installed_at() + 60, False)):
        server = Server(LocalService(home, harness=FakeHarness({}), config=FleetConfig()),
                        socket, idle_s=0.2)
        server._since = since
        serving = asyncio.ensure_future(server.run())
        await until(socket.exists)
        remote = await RemoteService.connect(home, socket=socket, start=False)
        assert remote.stale is stale
        await remote.aclose()
        await asyncio.wait_for(serving, 10)
    assert LocalService(home).stale is False  # the one in this process is this code


async def test_what_the_person_says_is_shown_before_the_agent_is_woken(tmp_path: Path):
    """An agent that has finished has no process: starting one takes a moment, and the
    person's message is in the feed at once, not after it."""
    service, harness, seen = local(tmp_path, {"K5 u1": [
        {"do": [("report", "Done.")]},
        {"when": "One more thing", "do": [("report", "That too.")]}]})
    made = await service.create_project("Acme", str(tmp_path / "work"))
    fleet = await service.add_fleet(made.id, "dev", "Tidy up", ongoing=False)
    await until(lambda: service.state(fleet).finished)

    def told() -> list[str]:
        return [u.event.text for u in seen
                if isinstance(u, Happened) and isinstance(u.event, Told)]

    harness.open_s = 0.5
    saying = asyncio.ensure_future(service.say(fleet, "One more thing"))
    await until(lambda: told() == ["One more thing"], timeout=0.3)  # while it comes up
    assert not saying.done()
    await saying
    await until(lambda: any("One more thing" in heard for heard in harness.heard["K5 u1"]))

    await until(lambda: service.fleet(fleet).lead.report == "That too.")
    await until(lambda: service.fleet(fleet).lead.state == "done")
    harness.open_s, harness.broken = 0.0, True  # one that cannot be delivered says so
    with pytest.raises(Exception, match="could not be started"):
        await service.say(fleet, "Hello?")
    notices = [u.event.text for u in seen
               if isinstance(u, Happened) and type(u.event).__name__ == "Notice"]
    assert told()[-1] == "Hello?" and "did not reach K5 u1" in notices[-1]
    await service.aclose()


async def test_what_an_agent_does_is_seen_as_it_does_it(tmp_path: Path):
    """Its reasoning, the message it is writing and the tool it uses reach a frontend
    while they happen, a few times a second at most, and are no part of the record."""
    service, _, seen = local(tmp_path, {"K5 u1": [{"do": [
        ("think", "Which file "), ("think", "first?"),
        ("doing", "Read the notes: read_file notes.md"), ("wait", 0.3),
        ("doing", None), *[("write", word) for word in ["It", "says", "to", "ship", "on", "Friday", "."]],
        ("wait", 0.3), ("say", "It says to ship on Friday."), ("think", "Done."),
        ("wait", 0.3)]}]})
    made = await service.create_project("Acme", str(tmp_path / "work"))
    fleet = await service.add_fleet(made.id, "dev", "Read the notes")

    def live() -> list[tuple[str, str, str]]:
        return [(u.thinking, u.writing, u.doing) for u in seen
                if isinstance(u, Live) and u.fleet == fleet and u.unit == "u1"]

    await until(lambda: ("Which file first?", "", "Read the notes: read_file notes.md")
                in live())
    await until(lambda: ("Which file first?", "ItsaystoshiponFriday.", "") in live())
    await until(lambda: ("Done.", "", "") in live())  # the message is whole: a new thought
    await until(lambda: live()[-1] == ("", "", ""))  # its turn is over
    assert len(live()) <= 8  # not one for every word
    history, _ = await service.history(fleet)
    assert {type(event).__name__ for event in history} <= {
        "Instructed", "Changed", "Said", "Delivered", "Told", "Notice"}
    await service.aclose()


async def test_what_waits_for_the_person_is_known_without_opening_a_fleet(tmp_path: Path):
    """An agent that asks for approval stands still: its state, its fleet's state and its
    project's count say so, here and over the socket."""
    harness = FakeHarness({"K5 u1": [
        {"do": [("approval", "r1", "shell", "uv run pytest"), ("say", "Ran them.")]}]})
    (tmp_path / "work").mkdir()
    home, socket = tmp_path / "home", tmp_path / "s"
    server = Server(LocalService(home, harness=harness, config=FleetConfig()), socket,
                    idle_s=0.3)
    serving = asyncio.ensure_future(server.run())
    await until(socket.exists)
    remote = await RemoteService.connect(home, socket=socket, start=False)
    made = await remote.create_project("Acme", str(tmp_path / "work"))
    fleet = await remote.add_fleet(made.id, "dev", "Run the tests")
    await until(lambda: remote.state(fleet).approvals)
    [asked] = remote.state(fleet).approvals
    assert (asked.unit, asked.request_id, asked.tool, asked.arguments) == (
        "u1", "r1", "shell", "uv run pytest")
    assert remote.fleet(fleet).units["u1"].state == "asking"
    assert (await remote.projects())[0].waiting == 1
    await remote.approve(fleet, "u1", "r1", True)
    await until(lambda: not remote.state(fleet).approvals)
    await until(lambda: remote.fleet(fleet).units["u1"].state == "working")
    assert (await remote.projects())[0].waiting == 0
    await remote.stop(fleet)
    await remote.aclose()
    await asyncio.wait_for(serving, 10)


async def test_the_models_an_agent_program_offers_are_listed(tmp_path: Path):
    """For a frontend to choose from; here and over the socket, and asked for once."""
    harness = FakeHarness({})
    home, socket = tmp_path / "home", tmp_path / "s"
    server = Server(LocalService(home, harness=harness, config=FleetConfig()), socket,
                    idle_s=0.2)
    serving = asyncio.ensure_future(server.run())
    await until(socket.exists)
    remote = await RemoteService.connect(home, socket=socket, start=False)
    assert await remote.models() == [("big-1", "Big One"), ("small-1", "Small One")]
    harness.offered = []
    assert len(await remote.models()) == 2  # kept: the program is not asked each time
    await remote.aclose()
    await asyncio.wait_for(serving, 10)
