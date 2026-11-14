import json

from kfm import events
from kfm.fleet import Fleet
from kfm.report import build, render


def _fleet() -> Fleet:
    fleet = Fleet(top_rank=5, name="marketing")
    fleet.work = "/tmp/work"
    fleet.settings = {}
    lead = fleet.add("Lead", "Run the campaign for the spring launch and report back", None)
    lead.state, lead.spent_usd = "done", 0.40
    scout = fleet.add("Scout", "Find the three competitors", lead)
    scout.state, scout.spent_usd = "done", 0.60
    writer = fleet.add("", "Write the launch post", lead)
    writer.state, writer.spent_usd = "waiting", 0.10
    return fleet


def _events(fleet: Fleet) -> list[tuple[float, events.FleetEvent]]:
    lead, scout, writer = fleet.units["u1"], fleet.units["u2"], fleet.units["u3"]
    return [
        (100.0, events.Instructed(lead, None, "task", "Run the campaign")),
        (110.0, events.Changed(scout, "working")),
        (120.0, events.Delivered(writer, lead, "question", "Which tone should I use?")),
        (130.0, events.Approval(scout, "req-1", "Bash", "curl https://example.com")),
        (131.0, events.Answered(scout, "req-1", False)),
        (140.0, events.Delivered(scout, lead, "report", "Found three competitors.")),
        (150.0, events.Told("Use a friendly tone.", to=writer)),
        (160.0, events.Notice("the fleet reached its limit")),
        (170.0, events.Delivered(lead, None, "report", "Campaign drafted.")),
    ]


def test_the_header_says_what_the_fleet_is_and_how_it_went():
    fleet = _fleet()
    text = render(
        fleet, _events(fleet), state="stopped", budget_usd=5.0, started=100.0, ended=170.0
    )
    assert text.startswith("# Report: marketing")
    assert "Status: stopped" in text
    assert "Duration: 1 min 10 s" in text
    assert "Spent: $1.10 of $5.00" in text


def test_the_agent_table_lists_each_agent_with_its_state_spending_and_clipped_task():
    fleet = _fleet()
    text = render(fleet, _events(fleet), state="stopped", budget_usd=5.0)
    assert "| Agent | Rank | Name | State | Spent | Task |" in text
    assert "| u1 | K5 | Lead | done | $0.40 |" in text
    assert "| u2 | K4 | Scout | done | $0.60 |" in text
    assert "| u3 | K4 | (none) | waiting | $0.10 | Write the launch post |" in text


def test_long_tasks_are_clipped_in_the_table():
    fleet = Fleet(name="long")
    fleet.add("Lead", "word " * 80, None)
    text = render(fleet, [], state="stopped", budget_usd=1.0)
    row = next(line for line in text.splitlines() if line.startswith("| u1 |"))
    assert "…" in row and len(row) < 300


def test_questions_approvals_and_instructions_to_the_person_are_listed():
    fleet = _fleet()
    text = render(fleet, _events(fleet), state="stopped", budget_usd=5.0)
    assert "## For you" in text
    assert "Which tone should I use?" in text  # a question from K4 u3 to the lead
    assert "Bash" in text and "denied" in text  # an approval asked and answered
    assert "Use a friendly tone." in text  # what the person told an agent


def test_reports_and_failures_are_in_time_order_with_who_to_whom():
    fleet = _fleet()
    text = render(fleet, _events(fleet), state="stopped", budget_usd=5.0)
    section = text.split("## Reports and failures", 1)[1].split("\n## ", 1)[0]
    assert "Scout (K4, u2) → Lead (K5, u1): report: Found three competitors." in section
    assert "Lead (K5, u1) → you: report: Campaign drafted." in section
    assert section.index("Found three competitors") < section.index("Campaign drafted")


def test_failures_are_listed_with_reports():
    fleet = _fleet()
    evs = [
        (
            1.0,
            events.Delivered(fleet.units["u2"], fleet.units["u1"], "failure", "The site is down."),
        )
    ]
    text = render(fleet, evs, state="stopped", budget_usd=5.0)
    assert "Scout (K4, u2) → Lead (K5, u1): failure: The site is down." in text


def test_spending_per_agent_adds_up_to_the_total():
    fleet = _fleet()
    data = build(fleet, _events(fleet), state="stopped", budget_usd=5.0)
    assert data["spending"] == {"u1": 0.40, "u2": 0.60, "u3": 0.10}
    assert data["spent_usd"] == 1.10


def test_json_is_structured_and_serialisable():
    fleet = _fleet()
    data = build(fleet, _events(fleet), state="stopped", budget_usd=5.0, started=100.0, ended=170.0)
    assert data["fleet"] == "marketing"
    assert data["status"] == "stopped"
    assert data["duration_s"] == 70.0
    assert data["budget_usd"] == 5.0
    assert [a["id"] for a in data["agents"]] == ["u1", "u2", "u3"]
    assert data["agents"][1] == {
        "id": "u2",
        "rank": 4,
        "name": "Scout",
        "state": "done",
        "spent_usd": 0.6,
        "task": "Find the three competitors",
    }
    kinds = [e["kind"] for e in data["timeline"]]
    assert kinds[0] == "task" and "report" in kinds
    json.dumps(data)  # must not raise


def test_since_leaves_out_what_came_before_the_time():
    fleet = _fleet()
    text = render(fleet, _events(fleet), state="stopped", budget_usd=5.0, since=145.0)
    assert "Campaign drafted." in text
    assert "Found three competitors." not in text


def test_a_fleet_with_nothing_yet_says_so():
    fleet = Fleet(name="new")
    text = render(fleet, [], state="stopped", budget_usd=1.0)
    assert "# Report: new" in text
    assert "Nothing has happened" in text


def test_what_the_lead_told_the_person_is_whole_and_what_agents_told_each_other_is_short():
    fleet = Fleet(name="dev")
    lead = fleet.add("", "Improve it", None)
    worker = fleet.add("Builder", "Build it", lead)
    long = "word " * 200
    history = [(1.0, events.Delivered(worker, lead, "report", long)),
               (2.0, events.Delivered(lead, None, "update", "Round one is done. " + long)),
               (3.0, events.Delivered(worker, lead, "update", "half way"))]
    text = render(fleet, history, state="stopped", budget_usd=3.0)
    you, between = text.split("## Reports and failures")
    assert "K5 u1 → you: update: Round one is done." in you and long.strip() in you
    assert "half way" not in text  # an update between agents is not the person's to read
    assert "Builder (K4, u2) → K5 u1: report: word" in between and "…" in between
    assert long.strip() not in between
