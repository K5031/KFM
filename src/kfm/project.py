"""A project: an organisation of named fleets, kept in a folder of its own. It is how
fleets are grouped for a person; a fleet's manager knows nothing of it."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from kfm.fleet import RECORD

PROJECT = "project.json"
FORMAT = 1


@dataclass(frozen=True, slots=True)
class Project:
    folder: Path  # <home>/projects/<when it was made>
    name: str
    work: Path  # where its fleets work, unless one is given its own

    def fleets(self) -> list[Path]:
        """The folders of its fleets, oldest first. Blocking."""
        return sorted(record.parent for record in (self.folder / "fleets").glob(f"*/{RECORD}"))

    def new_fleet(self) -> Path:
        """A folder for one more fleet; the fleet's manager makes it. Blocking."""
        return _unused(self.folder / "fleets")


def create(home: Path, name: str, work: Path) -> Project:
    """Blocking."""
    made = Project(_unused(home / "projects"), name.strip(), work)
    made.folder.mkdir(parents=True)
    (made.folder / PROJECT).write_text(json.dumps(
        {"format": FORMAT, "name": made.name, "work": str(work)}, indent=2))
    return made


def load(folder: Path) -> Project:
    """ValueError for a record of another format. Blocking."""
    data = json.loads((folder / PROJECT).read_text())
    if data.get("format") != FORMAT:
        raise ValueError(f"{folder / PROJECT} is of format {data.get('format')}, not {FORMAT}")
    return Project(folder, data["name"], Path(data["work"]))


def every(home: Path) -> list[Project]:
    """Every project under `home` that can be read, newest first. Blocking."""
    found = []
    for record in sorted((home / "projects").glob(f"*/{PROJECT}"), reverse=True):
        try:
            found.append(load(record.parent))
        except (ValueError, KeyError, TypeError, OSError):
            continue
    return found


def _unused(parent: Path) -> Path:
    """A folder under `parent` named after now, with a number when that is taken."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder, n = parent / stamp, 1
    while folder.exists():
        n += 1
        folder = parent / f"{stamp}-{n}"
    return folder
