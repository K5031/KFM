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

FORMAT = 7  # of fleet.json; a file of another format is refused
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
    name: str  # "": it has none, and is known by its rank and id
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
    def called(self) -> str:
        """What people read for it: its name, or its rank and id."""
        return self.name or f"K{self.rank} {self.id}"

    @property
    def label(self) -> str:
        """What agents read for it, with the id they address it by."""
        return (f"{self.name} (K{self.rank}, {self.id})" if self.name
                else f"K{self.rank} agent {self.id}")

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
        self.removed_usd = 0.0  # what units taken out of the fleet had spent
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
        """What the fleet has spent, by the units it has and by those taken out of it."""
        return self.removed_usd + sum(u.spent_usd for u in self.units.values())

    def add(self, name: str, task: str, supervisor: Unit | None, *, team: str | None = None,
            ongoing: bool = False, budget_usd: float = 0.0) -> Unit:
        """A new unit one rank below its supervisor, or the lead at the top rank. A name
        is optional; with no task it waits for one: the first message it gets."""
        name, task = name.strip(), task.strip()
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

    def remove(self, unit: Unit) -> list[Unit]:
        """Take the unit and everything under it out of the fleet; returns them. Their
        ids are not given again."""
        gone = list(self.subtree(unit))
        for each in gone:
            self.removed_usd += each.spent_usd  # still spent: it counts against the limit
            del self.units[each.id]
        return gone

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

    def record(self) -> dict[str, Any]:
        """The fleet as fleet.json holds it."""
        return {"format": FORMAT, "name": self.name, "top_rank": self.top_rank,
                "max_units": self.max_units, "next": self._next,
                "removed_usd": self.removed_usd,
                "work": self.work, "settings": self.settings,
                "manager_pid": self.manager_pid,
                "units": [asdict(u) for u in self.units.values()]}

    def update(self, data: dict[str, Any]) -> None:
        """Make this fleet what the record says, in place: a unit it already has stays
        the same object. ValueError for a record of another format."""
        if data.get("format") != FORMAT:
            raise ValueError(f"the record is of format {data.get('format')}, not {FORMAT}")
        self.top_rank, self.max_units, self.name = (data["top_rank"], data["max_units"],
                                                    data["name"])
        self.work, self.settings = data["work"], data["settings"]
        self.manager_pid = data["manager_pid"]
        self._next, self.removed_usd = data["next"], data["removed_usd"]
        units: dict[str, Unit] = {}
        for record in data["units"]:
            unit = self.units.get(record["id"])
            if unit is None:
                unit = Unit(**record)
            else:
                for key, value in record.items():
                    setattr(unit, key, value)
            units[unit.id] = unit
        self.units = units

    @classmethod
    def of(cls, data: dict[str, Any]) -> Fleet:
        """The fleet a record describes."""
        fleet = cls(data.get("top_rank", 5))
        fleet.update(data)
        return fleet

    def save(self, folder: Path) -> None:
        target = folder / RECORD
        partial = target.with_suffix(".tmp")
        partial.write_text(json.dumps(self.record(), indent=2))
        partial.replace(target)

    @classmethod
    def load(cls, folder: Path) -> Fleet:
        try:
            return cls.of(json.loads((folder / RECORD).read_text()))
        except ValueError as e:
            raise ValueError(f"{folder / RECORD}: {e}") from None
