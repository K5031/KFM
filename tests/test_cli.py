"""The `kfm` command, run as a person would."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import tomli_w

REPO = Path(__file__).resolve().parents[1]


def kfm(*args: str, home: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(REPO), "HOME": str(home)}
    return subprocess.run([sys.executable, "-m", "kfm", *args], env=env, text=True,
                          capture_output=True, timeout=60, stdin=subprocess.DEVNULL)


def test_run_follows_the_fleet_to_its_report_and_status_shows_it(tmp_path: Path):
    base = tmp_path / "kayden.toml"
    base.write_text(tomli_w.dumps({
        "core": {"factory": "tests.fakecore:factory", "options": {"scripts": {
            "Lead": [{"calls": [{"name": "mcp__fleet__delegate",
                                 "arguments": {"name": "Scout", "task": "Look"}}]},
                     {"when": "[Report from Scout",
                      "calls": [{"name": "finish_goal", "arguments": {"report": "All found."}}]}],
            "Scout": [{"calls": [{"name": "finish_goal",
                                  "arguments": {"report": "Found it."}}]}]}}},
        "runtime": {"model": "fake-1"}}))
    config = tmp_path / "fleet.toml"
    config.write_text(tomli_w.dumps({"kayden_config": str(base), "check_in_s": [0.05, 60.0]}))
    work = tmp_path / "work"
    work.mkdir()
    fleet = tmp_path / "fleet"
    ran = kfm("run", "Find it", "--config", str(config), "--fleet", str(fleet),
              "--work", str(work), "--budget", "1", home=tmp_path)
    assert ran.returncode == 0, ran.stderr
    assert "[Scout K4 u2 → Lead] report: Found it." in ran.stdout
    assert "[Lead K5 u1 → you] report: All found." in ran.stdout
    assert "The fleet spent $" in ran.stdout
    shown = kfm("status", "--fleet", str(fleet), home=tmp_path)
    assert shown.returncode == 0, shown.stderr
    assert "Lead (K5, u1): done" in shown.stdout
    assert "  Scout (K4, u2): done" in shown.stdout


def test_a_wrong_setting_ends_with_one_line(tmp_path: Path):
    config = tmp_path / "fleet.toml"
    config.write_text("nonsense = 1\n")
    ran = kfm("run", "x", "--config", str(config), home=tmp_path)
    assert ran.returncode == 2
    assert ran.stderr.strip().startswith("kfm:") and "nonsense" in ran.stderr


def test_status_without_a_fleet_says_how_to_start_one(tmp_path: Path):
    shown = kfm("status", home=tmp_path)
    assert shown.returncode == 1 and "kfm run" in shown.stdout
