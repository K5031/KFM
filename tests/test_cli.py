"""The `kfm` command, run as a person would: each call is a frontend of the service, which
it starts when none runs."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import tomli_w

REPO = Path(__file__).resolve().parents[1]


def kfm(*args: str, home: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONPATH": str(REPO), "HOME": str(home)}
    return subprocess.run([sys.executable, "-m", "kfm", *args], env=env, text=True, check=False,
                          capture_output=True, timeout=60, stdin=subprocess.DEVNULL)


def settings(home: Path, scripts: dict) -> None:
    """KFM's settings in `home`, with units on the scripted model."""
    base = home / "kaden.toml"
    base.write_text(tomli_w.dumps({
        "core": {"factory": "tests.fakecore:factory", "options": {"scripts": scripts}},
        "runtime": {"model": "fake-1"}}))
    (home / ".kfm").mkdir(exist_ok=True)
    (home / ".kfm" / "fleet.toml").write_text(tomli_w.dumps(
        {"harness_options": {"config": str(base)}, "check_in_s": [0.05, 60.0]}))


def test_run_follows_a_fleet_and_the_other_commands_reach_it(tmp_path: Path):
    settings(tmp_path, {
        "K5001": [{"calls": [{"name": "mcp__fleet__delegate",
                              "arguments": {"name": "Scout", "task": "Look"}}]},
                  {"when": "[Report from Scout",
                   "calls": [{"name": "mcp__fleet__finish", "arguments": {"report": "All found."}}]}],
        "Scout": [{"calls": [{"name": "mcp__fleet__finish", "arguments": {"report": "Found it."}}]}]})
    work = tmp_path / "work"
    work.mkdir()
    ran = kfm("run", "Find it", "--work", str(work), "--budget", "1", home=tmp_path)
    assert ran.returncode == 0, ran.stderr
    assert "[Scout K4001 → K5001] report: Found it." in ran.stdout
    assert "[K5001 → you] report: All found." in ran.stdout  # a lead has no name
    assert "The fleet spent $" in ran.stdout
    assert "Error" not in ran.stderr  # nothing to type into (stdin is /dev/null) is no fault

    shown = kfm("status", home=tmp_path)  # the fleet is in a project, named after its folder
    assert shown.returncode == 0, shown.stderr
    lines = shown.stdout.splitlines()
    assert lines[0].startswith("work (") and str(work) in lines[0]
    assert "fleet 1 (" in lines[1] and "stopped, 2 agents" in lines[1] and "of $1.00" in lines[1]
    assert "agent K5001: done" in shown.stdout and "Scout (K4001): done" in shown.stdout
    again = kfm("resume", home=tmp_path)
    assert again.returncode == 1 and "nothing was at work" in again.stdout.lower()

    # What the window can do, a terminal can: a fleet built by hand, changed and deleted.
    assert "work: 1 fleet, 0 running" in kfm("projects", home=tmp_path).stdout
    made = kfm("fleet", "new", "work", "hand", home=tmp_path)
    assert made.returncode == 0 and "no agents" in made.stdout, made.stderr
    assert "Agent K5002." in kfm("agent", "add", "hand", "--name", "Chief", "--task",
                              "Run it", home=tmp_path).stdout
    assert kfm("agent", "add", "hand", "--under", "K5002", home=tmp_path).returncode == 0
    assert kfm("agent", "rename", "hand", "K5002", "Boss", home=tmp_path).returncode == 0
    assert kfm("agent", "model", "hand", "K4002", "small-1", home=tmp_path).returncode == 0
    assert kfm("agent", "task", "hand", "K4002", "Count the files", home=tmp_path).returncode == 0
    # A duty, and a task again: with no text, only its kind changes.
    assert kfm("agent", "continuous", "hand", "K4002", home=tmp_path).returncode == 0
    assert "Count the files" in kfm("status", "hand", home=tmp_path).stdout
    assert kfm("agent", "continuous", "hand", "K4002", "Watch the files", home=tmp_path).returncode == 0
    assert kfm("agent", "task", "hand", "K4002", "Count the files", home=tmp_path).returncode == 0
    built = kfm("status", "hand", home=tmp_path).stdout
    assert "hand (" in built and "Boss (K5002): planned" in built
    assert "agent K4002: planned" in built and "Count the files" in built
    assert kfm("agent", "remove", "hand", "K4002", home=tmp_path).returncode == 0
    assert kfm("fleet", "rename", "hand", "built", home=tmp_path).returncode == 0
    wrong = kfm("agent", "model", "built", "u9", "x", home=tmp_path)
    assert wrong.returncode == 2 and wrong.stderr.startswith("kfm:") and "u9" in wrong.stderr
    assert "deleted" in kfm("fleet", "delete", "built", home=tmp_path).stdout
    assert "A fleet may spend $3.00" in kfm("settings", "--budget", "3", "--model",
                                            "K5=big-1", home=tmp_path).stdout
    assert "K5: big-1" in kfm("settings", home=tmp_path).stdout
    gone = kfm("project", "delete", "work", home=tmp_path)
    assert gone.returncode == 0 and work.is_dir()
    assert kfm("status", home=tmp_path).returncode == 1


def test_a_fleet_keeps_running_after_the_command_that_started_it(tmp_path: Path):
    settings(tmp_path, {"K5001": [{"text": "On watch."},
                                  {"when": "anything new", "text": "All quiet."}]})
    work = tmp_path / "work"
    work.mkdir()
    ran = kfm("run", "Keep watch", "--continuous", "--detach", "--name", "watch", "--work",
              str(work), home=tmp_path)
    assert ran.returncode == 0 and "runs on its own" in ran.stdout, ran.stderr
    assert "watch (" in kfm("status", home=tmp_path).stdout
    assert "running, 1 agents" in kfm("status", "watch", home=tmp_path).stdout
    assert kfm("say", "watch", "anything new?", home=tmp_path).returncode == 0
    assert "is stopped" in kfm("stop", "watch", home=tmp_path).stdout
    assert "stopped, 1 agents" in kfm("status", "watch", home=tmp_path).stdout


def test_a_wrong_setting_ends_with_one_line(tmp_path: Path):
    (tmp_path / ".kfm").mkdir()
    (tmp_path / ".kfm" / "fleet.toml").write_text("nonsense = 1\n")
    ran = kfm("run", "x", "--work", str(tmp_path), home=tmp_path)
    assert ran.returncode == 2
    assert ran.stderr.strip().startswith("kfm:") and "nonsense" in ran.stderr


def test_status_without_a_fleet_says_how_to_start_one(tmp_path: Path):
    shown = kfm("status", home=tmp_path)
    assert shown.returncode == 1 and "kfm run" in shown.stdout


def test_the_service_is_shown_and_stopped_from_the_terminal(tmp_path: Path):
    settings(tmp_path, {"K5001": [{"text": "On watch."}]})
    work = tmp_path / "work"
    work.mkdir()
    assert "No KFM service runs" in kfm("service", home=tmp_path).stdout
    kfm("run", "Keep watch", "--continuous", "--detach", "--name", "watch", "--work", str(work),
        home=tmp_path)
    shown = kfm("service", home=tmp_path).stdout
    assert "runs since" in shown and "process" in shown
    stopped = kfm("service", "stop", home=tmp_path)
    assert stopped.returncode == 0 and "ended" in stopped.stdout, stopped.stderr
    assert "No KFM service runs" in kfm("service", home=tmp_path).stdout
    assert "stopped, 1 agents" in kfm("status", "watch", home=tmp_path).stdout  # resumable
    restarted = kfm("service", "restart", home=tmp_path)
    assert restarted.returncode == 0 and "runs since" in restarted.stdout, restarted.stderr
    kfm("service", "stop", home=tmp_path)


def test_a_project_from_the_terminal_gets_its_folder_in_the_projects_folder(tmp_path: Path):
    settings(tmp_path, {})
    base = tmp_path / "all"
    assert "Projects folder" not in kfm("settings", home=tmp_path).stdout
    set_ = kfm("settings", "--projects-folder", str(base), home=tmp_path)
    assert f"Projects folder: {base}" in set_.stdout, set_.stderr
    made = kfm("project", "new", "Sea Game", home=tmp_path)
    assert made.returncode == 0 and (base / "Sea-Game").is_dir(), made.stderr
    assert str(base / "Sea-Game") in kfm("projects", home=tmp_path).stdout
    (base / "Sea-Game" / "notes.txt").write_text("mine")  # in use: not taken unasked
    again = kfm("project", "new", "Sea Game", home=tmp_path)
    assert again.returncode != 0 and "already exists" in again.stderr and "--work" in again.stderr
    named = kfm("project", "new", "Sea Game", "--work", str(base / "Sea-Game"), home=tmp_path)
    assert named.returncode == 0, named.stderr
    kfm("service", "stop", home=tmp_path)


def test_how_models_are_chosen_is_set_from_the_terminal(tmp_path: Path):
    settings(tmp_path, {})
    assert "Models: manual" in kfm("settings", home=tmp_path).stdout
    refused = kfm("settings", "--model-mode", "auto", home=tmp_path)
    assert refused.returncode != 0 and "at least one model" in refused.stderr
    changed = kfm("settings", "--model-mode", "auto", "--auto-models", "small-1,big-1",
                  home=tmp_path)
    assert "Models: auto, from small-1, big-1" in changed.stdout, changed.stderr
    kfm("project", "new", "Studio", "--work", str(tmp_path), home=tmp_path)
    unnamed = kfm("fleet", "new", "Studio", "dev", "Build it.", home=tmp_path)
    assert unnamed.returncode != 0 and "one of: small-1, big-1" in unnamed.stderr
    named = kfm("fleet", "new", "Studio", "dev", "Build it.", "--model", "big-1",
                home=tmp_path)
    assert named.returncode == 0, named.stderr
    kfm("service", "stop", home=tmp_path)


def test_what_agents_may_do_is_set_from_the_terminal(tmp_path: Path):
    settings(tmp_path, {})
    assert "Permission: allow_workspace (work freely" in kfm("settings", home=tmp_path).stdout
    changed = kfm("settings", "--permissions", "auto", home=tmp_path)
    assert "Permission: auto (decide automatically" in changed.stdout, changed.stderr
    refused = kfm("settings", "--permissions", "anything", home=tmp_path)
    assert refused.returncode != 0
    kfm("service", "stop", home=tmp_path)
