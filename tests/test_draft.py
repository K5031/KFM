"""Drafting a project, on the second harness."""

from __future__ import annotations

from pathlib import Path

import pytest

from kfm.config import FleetConfig
from kfm.draft import DraftError, draft
from tests.fakeharness import FakeHarness

TEAMS = [{"name": "dev", "does": "Build the game.", "ongoing": True},
         {"name": "trailer", "does": "Cut a trailer.", "ongoing": False}]


async def test_an_agent_proposes_the_teams_and_ends(tmp_path: Path):
    harness = FakeHarness({"Planner": [
        {"do": [("tool", "propose", {"teams": TEAMS}), ("report", "Proposed.")]}]})
    made = await draft("Run my game studio", config=FleetConfig(), harness=harness,
                       folder=tmp_path / "planner", work=tmp_path)
    assert [(t.name, t.does, t.ongoing) for t in made.teams] == [
        ("dev", "Build the game.", True), ("trailer", "Cut a trailer.", False)]
    [told] = harness.heard["Planner"]
    assert "Run my game studio" in told and str(tmp_path) in told
    assert "fleet.propose" in told and "done" in told  # in the harness's own words
    assert harness.opened == [("Planner", None)]


@pytest.mark.parametrize(("steps", "why"), [
    ([("report", "Here is my plan: ...")], "without proposing"),
    ([("ask", "What is the game?")], "What is the game?"),
])
async def test_no_proposal_is_an_error(tmp_path: Path, steps, why):
    harness = FakeHarness({"Planner": [{"do": steps}]})
    with pytest.raises(DraftError, match=why):
        await draft("Run my game studio", config=FleetConfig(), harness=harness,
                    folder=tmp_path / "planner", work=tmp_path)


async def test_a_real_kadence_unit_drafts_on_a_scripted_model(tmp_path: Path, monkeypatch):
    import tomli_w

    from kfm import harnesses

    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1]))
    base = tmp_path / "kadence.toml"
    base.write_text(tomli_w.dumps({
        "core": {"factory": "tests.fakecore:factory", "options": {"scripts": {"Planner": [
            {"calls": [{"name": "mcp__fleet__propose", "arguments": {"teams": TEAMS}}]}]}}},
        "runtime": {"model": "fake-1"}}))
    (tmp_path / "work").mkdir()
    made = await draft("Run my game studio", config=FleetConfig(),
                       harness=harnesses.load("kadence", {"config": str(base)}),
                       folder=tmp_path / "planner", work=tmp_path / "work")
    assert [team.name for team in made.teams] == ["dev", "trailer"]  # no approval was asked
