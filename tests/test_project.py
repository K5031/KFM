"""Projects on disk, and where a chart puts a fleet's agents."""

from __future__ import annotations

from pathlib import Path

import pytest

from kfm import project
from kfm.chart import slots
from kfm.fleet import Fleet


def test_a_project_is_kept_with_its_fleets(tmp_path: Path):
    home, work = tmp_path / "home", tmp_path / "work"
    made = project.create(home, " Acme ", work)
    assert made.name == "Acme" and made.folder.parent == home / "projects"
    assert project.load(made.folder) == made
    assert made.fleets() == []
    first = made.new_fleet()
    Fleet(name="dev").save(_made(first))
    second = made.new_fleet()  # within the same second: another folder
    assert second != first and second.parent == first.parent
    Fleet(name="art").save(_made(second))
    assert made.fleets() == [first, second]
    assert Fleet.load(second).name == "art"
    other = project.create(home, "Other", work)
    assert other.folder != made.folder and other.fleets() == []
    assert {p.name for p in project.every(home)} == {"Acme", "Other"}


def test_a_project_of_another_format_is_refused(tmp_path: Path):
    made = project.create(tmp_path, "Acme", tmp_path)
    (made.folder / project.PROJECT).write_text('{"format": 99}')
    with pytest.raises(ValueError, match="format 99"):
        project.load(made.folder)
    assert project.every(tmp_path) == []


def test_a_supervisor_stands_over_the_middle_of_its_agents():
    fleet = Fleet()
    lead = fleet.add("Lead", "Run it", None)
    a = fleet.add("A", "x", lead)
    b = fleet.add("B", "x", lead)
    fleet.add("A1", "x", a)
    fleet.add("A2", "x", a)
    fleet.add("B1", "x", b)
    at, columns = slots(fleet)
    assert columns == 3
    assert at == {"u1": (1.0, 0), "u2": (0.5, 1), "u3": (2.0, 1),
                  "u4": (0.0, 2), "u5": (1.0, 2), "u6": (2.0, 2)}
    assert slots(Fleet()) == ({}, 1.0)  # no lead yet


def _made(folder: Path) -> Path:
    folder.mkdir(parents=True)
    return folder
