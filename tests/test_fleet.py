from pathlib import Path

import pytest

from kfm.fleet import Fleet, FleetError


def test_the_lead_has_the_top_rank_and_each_agent_one_below_its_supervisor():
    fleet = Fleet(top_rank=3)
    lead = fleet.add("Lead", "Ship it", None)
    scout = fleet.add("Scout", "Look around", lead)
    helper = fleet.add("Helper", "Carry things", scout)
    assert (lead.rank, scout.rank, helper.rank) == (3, 2, 1)
    assert fleet.lead is lead
    assert fleet.supervisor_of(helper) is scout
    assert [u.id for u in fleet.subtree(lead)] == ["u1", "u2", "u3"]


def test_the_lowest_rank_cannot_delegate():
    fleet = Fleet(top_rank=1)
    lead = fleet.add("Lead", "Do it", None)
    with pytest.raises(FleetError, match="lowest rank"):
        fleet.add("Helper", "Help", lead)


def test_a_fleet_has_one_lead_and_a_limit():
    fleet = Fleet(max_units=2)
    lead = fleet.add("Lead", "Do it", None)
    with pytest.raises(FleetError, match="already has a lead"):
        fleet.add("Other", "Also", None)
    fleet.add("A", "a", lead)
    with pytest.raises(FleetError, match="limit of 2"):
        fleet.add("B", "b", lead)


def test_an_agent_reaches_only_those_it_supervises():
    fleet = Fleet()
    lead = fleet.add("Lead", "Do it", None)
    a = fleet.add("A", "a", lead)
    below = fleet.add("Below", "b", a)
    assert fleet.subordinate(lead, " u2 ") is a
    with pytest.raises(FleetError, match="Your agents: u2"):
        fleet.subordinate(lead, below.id)  # its agent's agent, not its own
    assert fleet.by_token(a.token) is a
    with pytest.raises(FleetError):
        fleet.by_token("nope")


def test_a_team_is_inherited_and_spending_adds_up():
    fleet = Fleet()
    lead = fleet.add("Lead", "Do it", None)
    writer = fleet.add("Writer", "Write", lead, team="marketing")
    editor = fleet.add("Editor", "Edit", writer)
    assert (lead.team, writer.team, editor.team) == (None, "marketing", "marketing")
    writer.spent_usd, editor.spent_usd = 0.5, 0.25
    assert fleet.spent_usd == 0.75


def test_the_record_round_trips_and_another_format_is_refused(tmp_path: Path):
    fleet = Fleet(top_rank=4, max_units=7)
    lead = fleet.add("Lead", "Do it", None)
    fleet.add("A", "a", lead).state = "done"
    fleet.save(tmp_path)
    again = Fleet.load(tmp_path)
    assert (again.top_rank, again.max_units) == (4, 7)
    assert [(u.id, u.name, u.state) for u in again.units.values()] == [
        ("u1", "Lead", "planned"), ("u2", "A", "done")]
    assert again.add("B", "b", again.lead).id == "u3"
    (tmp_path / "fleet.json").write_text('{"format": 99}')
    with pytest.raises(ValueError, match="format 99"):
        Fleet.load(tmp_path)
