"""The report of a fleet: what it did and why, for a person who was not watching. Pure: it
takes the fleet's record and its events (from `Service.fleet` and `Service.history`) and
returns Markdown or plain data. `kfm report` is its frontend."""

from __future__ import annotations

from typing import Any

from kfm import events
from kfm.fleet import Fleet, Unit

TASK_CHARS = 100  # of a task in the agent table
REPORT_CHARS = 400  # of what one agent sent another; what reached the person is whole


def build(
    fleet: Fleet,
    history: list[tuple[float, events.FleetEvent]],
    *,
    state: str,
    budget_usd: float,
    started: float | None = None,
    ended: float | None = None,
    since: float | None = None,
) -> dict[str, Any]:
    """The report as structured data. `since`: only events at or after that log time.
    `started` and `ended` bound the duration; both None when unknown."""
    shown = [(at, event) for at, event in history if since is None or at >= since]
    duration = (ended - started) if started is not None and ended is not None else None
    spending = {unit.id: round(unit.spent_usd, 2) for unit in fleet.units.values()}
    return {
        "fleet": fleet.name,
        "status": state,
        "duration_s": round(duration, 1) if duration is not None else None,
        "budget_usd": budget_usd,
        "spent_usd": round(fleet.spent_usd, 2),
        "agents": [_agent(fleet, unit) for unit in fleet.units.values()],
        "spending": spending,
        "timeline": [entry for at, event in shown
                     if (entry := _entry(at, event)) is not None],
    }


def render(
    fleet: Fleet,
    history: list[tuple[float, events.FleetEvent]],
    *,
    state: str,
    budget_usd: float,
    started: float | None = None,
    ended: float | None = None,
    since: float | None = None,
) -> str:
    """The report as Markdown."""
    data = build(
        fleet,
        history,
        state=state,
        budget_usd=budget_usd,
        started=started,
        ended=ended,
        since=since,
    )
    lines = [f"# Report: {fleet.name or 'fleet'}", ""]
    lines.append(f"Status: {data['status']}")
    if data["duration_s"] is not None:
        lines.append(f"Duration: {_duration(data['duration_s'])}")
    lines.append(f"Spent: ${data['spent_usd']:.2f} of ${budget_usd:.2f}")
    lines += [
        "",
        "## Agents",
        "",
        "| Agent | Rank | Name | State | Spent | Task |",
        "|---|---|---|---|---|---|",
    ]
    for agent in data["agents"]:
        lines.append(
            f"| {agent['id']} | K{agent['rank']} | {_cell(agent['name']) or '(none)'} | "
            f"{agent['state']} | ${agent['spent_usd']:.2f} | {_cell(agent['task'])} |"
        )
    entries = data["timeline"]
    if not entries:
        lines += ["", "Nothing has happened yet."]
    asked = [e for e in entries
             if e["kind"] in ("question", "approval", "answer", "told", "update")]
    lines += ["", "## For you", ""]
    lines += [_line(fleet, e) for e in asked] or ["Nothing was asked of you or told to an agent."]
    reports = [e for e in entries if e["kind"] in ("report", "failure")]
    lines += ["", "## Reports and failures", ""]
    lines += [_line(fleet, e) for e in reports] or ["No reports or failures."]
    lines += ["", "## Spending", ""]
    for agent in data["agents"]:
        lines.append(f"- {_label(fleet, agent['id'])}: ${agent['spent_usd']:.2f}")
    return "\n".join(lines) + "\n"


def _agent(fleet: Fleet, unit: Unit) -> dict[str, Any]:
    return {
        "id": unit.id,
        "rank": unit.rank,
        "name": unit.name,
        "state": unit.state,
        "spent_usd": round(unit.spent_usd, 2),
        "task": _clip(unit.task, TASK_CHARS),
    }


def _entry(at: float, event: events.FleetEvent) -> dict[str, Any] | None:
    """One row of the timeline, or None for what a report does not show."""
    match event:
        case events.Instructed(unit=unit, by=by, kind="task", text=text):
            return {  # from who gave it (None: the person) to the unit, as the others run
                "at": at,
                "kind": "task",
                "unit": by.id if by is not None else None,
                "to": unit.id,
                "text": _clip(text, TASK_CHARS),
            }
        case events.Delivered(unit=unit, to=to, kind=kind, text=text):
            if kind in ("report", "failure", "question") or (kind == "update" and to is None):
                return {
                    "at": at,
                    "kind": kind,
                    "unit": unit.id,
                    "to": to.id if to is not None else "you",
                    "text": text,
                }
            return None
        case events.Approval(unit=unit, tool=tool, arguments=arguments):
            return {
                "at": at,
                "kind": "approval",
                "unit": unit.id,
                "to": None,
                "text": f"wants to run {tool} {_clip(arguments, TASK_CHARS)}",
            }
        case events.Answered(unit=unit, allow=allow):
            return {
                "at": at,
                "kind": "answer",
                "unit": unit.id,
                "to": None,
                "text": "allowed" if allow else "denied",
            }
        case events.Told(text=text, to=to):
            return {
                "at": at,
                "kind": "told",
                "unit": None,
                "to": to.id if to is not None else "lead",
                "text": text,
            }
        case events.Notice(text=text):
            return {"at": at, "kind": "notice", "unit": None, "to": None, "text": text}
    return None


def _line(fleet: Fleet, entry: dict[str, Any]) -> str:
    kind, text = entry["kind"], entry["text"]
    unit = _label(fleet, entry["unit"]) if entry["unit"] else "fleet"
    to = _label(fleet, entry["to"]) if entry["to"] else "fleet"
    match kind:
        case "report" | "failure" | "question" | "update":
            if entry["to"] != "you":
                text = _clip(text, REPORT_CHARS)
            return f"- {unit} → {to}: {kind}: {text}"
        case "approval":
            return f"- {unit} {text}."
        case "answer":
            return f"- {unit} approval: {text}"
        case "told":
            return f"- you → {to}: {text}"
    return f"- {text}"


def _label(fleet: Fleet, ref: str) -> str:
    """What people read for a unit by its id, or the ref itself when it means the person."""
    unit = fleet.units.get(ref)
    if unit is None:
        return ref
    return f"{unit.name} (K{unit.rank}, {unit.id})" if unit.name else f"K{unit.rank} {unit.id}"


def _cell(text: str) -> str:
    """Text that stays inside one cell of a Markdown table."""
    return text.replace("|", "\\|")


def _duration(seconds: float) -> str:
    total = round(seconds)
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {minutes} min"
    if minutes:
        return f"{minutes} min {secs} s"
    return f"{secs} s"


def _clip(text: str, chars: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= chars else text[:chars] + "…"
