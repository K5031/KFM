"""The fleet as data: its units, who supervises whom, and the rules of the hierarchy
(PHILOSOPHY.md). No processes here; the manager runs them."""

from __future__ import annotations

import json
import os
import secrets
import shutil
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

FORMAT = 5  # of fleet.json; a file of another format is refused
RECORD = "fleet.json"

# planned: placed by the person in a fleet that is not running; it starts with the fleet.
# starting: its process is coming up.  working: on its task, between check-ins included.
# waiting: it asked its supervisor and waits for the answer.  done: it reported.
# idle: it ended a turn with nothing left to do and no report.  failed, stopped.
ACTIVE = ("starting", "working", "waiting")


def delete(folder: Path) -> None:
    """Remove a fleet's folder: its record, its log and what its units kept. The folder it
    worked in is not part of it. Blocking."""
    if not (folder / RECORD).is_file():
        raise ValueError(f"{folder} is not a fleet")
    shutil.rmtree(folder)


class FleetError(ValueError):
    """A request the hierarchy does not allow; its message is for the unit that asked."""


@dataclass
class Unit:
    id: str
    name: str
    rank: int  # 5 is K5
    task: str
    supervisor: str | None  # a unit's id; None: the person the fleet works for
    team: str | None = None
    ongoing: bool = False  # standing work that never ends, instead of a goal
    budget_usd: float = 0.0  # the most this unit may spend
    model: str | None = None  # the person's choice for it; None: its rank's or team's
    token: str = field(default_factory=lambda: secrets.token_urlsafe(16))  # for the fleet tools
    state: str = "starting"
    session: str | None = None  # what its harness opens it on again (api.Session.begin)
    report: str | None = None
    question: str | None = None
    failure: str | None = None
    spent_usd: float = 0.0

    @property
    def label(self) -> str:
        return f"{self.name} (K{self.rank}, {self.id})"

    @property
    def active(self) -> bool:
        return self.state in ACTIVE


class Fleet:
    """Units by id, in the order they were added."""

    def __init__(self, top_rank: int = 5, max_units: int = 12, name: str = "") -> None:
        if not 1 <= top_rank <= 5:
            raise ValueError("the top rank is between 1 and 5")
        self.top_rank, self.max_units = top_rank, max_units
        self.name = name  # what a person calls it: "marketing"
        self.units: dict[str, Unit] = {}
        self._next = 1
        # What starting the fleet again needs (the manager fills these in).
        self.work = ""  # the folder the fleet works in
        self.settings: dict[str, Any] = {}  # config.record of what it runs with
        self.manager_pid: int | None = None  # the process that runs it now

    @property
    def running(self) -> bool:
        """Whether the process that ran the fleet last is still there."""
        if self.manager_pid is None:
            return False
        try:
            os.kill(self.manager_pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    @property
    def lead(self) -> Unit | None:
        """The unit the person talks to."""
        return next((u for u in self.units.values() if u.supervisor is None), None)

    @property
    def spent_usd(self) -> float:
        return sum(u.spent_usd for u in self.units.values())

    def add(self, name: str, task: str, supervisor: Unit | None, *, team: str | None = None,
            ongoing: bool = False, budget_usd: float = 0.0) -> Unit:
        """A new unit one rank below its supervisor, or the lead at the top rank. With no
        name it is called by its number; with no task it waits for one: the first message
        it gets."""
        name, task = name.strip() or f"Agent {self._next}", task.strip()
        if supervisor is None:
            if self.lead is not None:
                raise FleetError("The fleet already has a lead.")
            rank = self.top_rank
        else:
            if supervisor.rank <= 1:
                raise FleetError("A K1 agent is the lowest rank and cannot delegate: do the "
                                 "work yourself.")
            rank = supervisor.rank - 1
        if len(self.units) >= self.max_units:
            raise FleetError(f"The fleet is at its limit of {self.max_units} agents. Reuse an "
                             "agent that has finished (message it a new task) or do the work "
                             "yourself.")
        unit = Unit(f"u{self._next}", name, rank, task, supervisor.id if supervisor else None,
                    team=team or (supervisor.team if supervisor else None), ongoing=ongoing,
                    budget_usd=budget_usd)
        self._next += 1
        self.units[unit.id] = unit
        return unit

    def by_token(self, token: str) -> Unit:
        for unit in self.units.values():
            if secrets.compare_digest(unit.token, token):
                return unit
        raise FleetError("Unknown agent.")

    def subordinate(self, supervisor: Unit, unit_id: str) -> Unit:
        """The unit `supervisor` directly supervises."""
        unit = self.units.get(unit_id.strip())
        if unit is None or unit.supervisor != supervisor.id:
            own = ", ".join(u.id for u in self.subordinates(supervisor)) or "none"
            raise FleetError(f"You have no agent {unit_id!r}. Your agents: {own}.")
        return unit

    def subordinates(self, supervisor: Unit) -> list[Unit]:
        return [u for u in self.units.values() if u.supervisor == supervisor.id]

    def subtree(self, unit: Unit) -> Iterator[Unit]:
        """The unit, then everything below it."""
        yield unit
        for below in self.subordinates(unit):
            yield from self.subtree(below)

    def supervisor_of(self, unit: Unit) -> Unit | None:
        return self.units.get(unit.supervisor) if unit.supervisor is not None else None

    # ── fleet.json: for people, `kfm status` and `kfm resume` ────────────

    def save(self, folder: Path) -> None:
        data = {"format": FORMAT, "name": self.name, "top_rank": self.top_rank,
                "max_units": self.max_units,
                "work": self.work, "settings": self.settings,
                "manager_pid": self.manager_pid,
                "units": [asdict(u) for u in self.units.values()]}
        target = folder / RECORD
        partial = target.with_suffix(".tmp")
        partial.write_text(json.dumps(data, indent=2))
        partial.replace(target)

    @classmethod
    def load(cls, folder: Path) -> Fleet:
        data = json.loads((folder / RECORD).read_text())
        if data.get("format") != FORMAT:
            raise ValueError(f"{folder / RECORD} is of format {data.get('format')}, not {FORMAT}")
        fleet = cls(data["top_rank"], data["max_units"], data["name"])
        fleet.work, fleet.settings = data["work"], data["settings"]
        fleet.manager_pid = data["manager_pid"]
        for record in data["units"]:
            unit = Unit(**record)
            fleet.units[unit.id] = unit
            fleet._next = max(fleet._next, int(unit.id[1:]) + 1)
        return fleet
