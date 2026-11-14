"""Approval for actions with real-world effect: a unit asks the person through
request_approval and waits, without the fleet waiting with it. Runs on the fake harness."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from kfm.config import FleetConfig
from kfm.events import Answered, Approval, FleetEvent
from kfm.manager import Manager
from tests.fakeharness import FakeHarness


async def until(predicate: Callable[[], Any], timeout: float = 30.0) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.02)

    await asyncio.wait_for(poll(), timeout)


def ask(action: str, details: str = "") -> dict[str, Any]:
    """A script step whose one action asks for approval through the fleet tool."""
    return {"do": [("tool", "request_approval", {"action": action, "details": details})]}


@pytest.fixture
async def fleet(tmp_path: Path):
    managers: list[Manager] = []
    harnesses: list[FakeHarness] = []

    async def make(scripts: dict[str, list[dict[str, Any]]], **settings: Any):
        events: list[FleetEvent] = []
        work = tmp_path / "work"
        work.mkdir(exist_ok=True)
        harness = FakeHarness(scripts)
        cfg = replace(FleetConfig(), check_in_s=(0.05, 60.0), **settings)
        manager = Manager(cfg, tmp_path / "fleet", work, on_event=events.append,
                          harness=harness)
        managers.append(manager)
        harnesses.append(harness)
        await manager.start()
        return manager, events, harness

    yield make
    for manager in managers:
        await manager.aclose()


def approvals(events: list[FleetEvent]) -> list[Approval]:
    return [e for e in events if isinstance(e, Approval)]


async def test_a_real_world_action_waits_for_the_person(fleet):
    manager, events, harness = await fleet({
        "Lead": [ask("Publish the post", "On the blog, at noon")]})
    lead = await manager.lead("Publish the post", name="Lead")
    await until(lambda: approvals(events))
    [asked] = approvals(events)
    assert asked.unit is lead and asked.tool == "request_approval"
    assert "Publish the post" in asked.arguments and "On the blog" in asked.arguments
    assert "Publish" not in "".join(harness.results.get("Lead", []))  # no answer yet
    await manager.approve(lead, asked.request_id, True)
    await until(lambda: harness.results.get("Lead"))
    assert harness.results["Lead"] == ["approved"]
    assert [e.allow for e in events if isinstance(e, Answered)] == [True]


async def test_a_denial_comes_back_to_the_unit_with_the_reason(fleet):
    manager, events, harness = await fleet({"Lead": [ask("Send the newsletter")]})
    lead = await manager.lead("Send it", name="Lead")
    await until(lambda: approvals(events))
    await manager.approve(lead, approvals(events)[0].request_id, False)
    await until(lambda: harness.results.get("Lead"))
    assert harness.results["Lead"] == ["denied: the person did not allow it"]


async def test_the_setting_grants_real_world_actions_without_asking(fleet):
    manager, events, harness = await fleet({"Lead": [ask("Send the newsletter")]},
                                           real_world_approval=False)
    await manager.lead("Send it", name="Lead")
    await until(lambda: harness.results.get("Lead"))
    assert approvals(events) == [] and harness.results["Lead"] == ["approved"]


async def test_stopping_the_fleet_denies_what_waits(fleet):
    manager, events, _harness = await fleet({"Lead": [ask("Pay the invoice")]})
    lead = await manager.lead("Pay it", name="Lead")
    await until(lambda: approvals(events))
    await manager.stop(lead)
    assert manager._pending == {}


async def test_stopping_one_agent_leaves_what_another_asked(fleet):
    manager, events, _harness = await fleet({
        "Lead": [{"do": [("tool", "delegate", {"name": "Clerk", "task": "File it"}),
                         *ask("Pay the invoice")["do"]]}],
        "Clerk": [ask("Send the receipt")]})
    await manager.lead("Pay it", name="Lead")
    await until(lambda: len(approvals(events)) == 2)
    clerk = manager.fleet.units["u2"]
    await manager.stop(clerk)
    [(who, waiting)] = manager._pending.values()  # the lead's still waits for the person
    assert who == "u1" and not waiting.done()
