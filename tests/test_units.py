import tomllib
from pathlib import Path

from kfm.api import UnitSpec, Vocabulary
from kfm.fleet import Fleet
from kfm.harnesses import kadence
from kfm.units import brief


def spec(tmp_path: Path, unit_id: str, **fields) -> UnitSpec:
    return UnitSpec(**{"id": unit_id, "name": "Scout", "rank": 4,
                       "folder": tmp_path / unit_id, "work": tmp_path / "work",
                       "model": None, "permissions": "allow_workspace", "budget_usd": 2.0,
                       "tools_url": "http://127.0.0.1:1/mcp?unit=abc", **fields})


def test_a_kadence_units_folder_holds_the_base_configuration_with_its_own_limits(
        tmp_path: Path):
    base = tmp_path / "kadence.toml"
    base.write_text('[core]\nfactory = "x:y"\n[runtime]\nmodel = "base"\n'
                    'maintenance_model = "light"\n[frontend]\nfactory = "a:b"\n')
    harness = kadence.factory({"config": str(base)})
    harness.prepare(spec(tmp_path, "u1"))
    harness.prepare(spec(tmp_path, "u2", model="mid", budget_usd=0.5))
    lead_config = tomllib.loads((tmp_path / "u1" / "config.toml").read_text())
    scout_config = tomllib.loads((tmp_path / "u2" / "config.toml").read_text())
    assert lead_config["core"] == {"factory": "x:y"} and "frontend" not in lead_config
    assert lead_config["runtime"] == {"model": "base", "maintenance_model": "light",
                                      "permissions": "allow_workspace",
                                      "budget": {"max_cost_usd": 2.0}}
    assert scout_config["runtime"]["model"] == "mid"
    assert scout_config["runtime"]["budget"] == {"max_cost_usd": 0.5}
    settings = tomllib.loads((tmp_path / "u2" / "settings.toml").read_text())
    assert settings["work_folder"] == str(tmp_path / "work")
    assert settings["mcp_servers"] == [
        {"name": "fleet", "url": "http://127.0.0.1:1/mcp?unit=abc"}]


def test_the_brief_says_where_the_unit_stands_in_its_harnesss_words():
    fleet = Fleet(top_rank=2)
    lead = fleet.add("Lead", "Ship the site", None)
    worker = fleet.add("Builder", "Build the page", lead, team="web")
    watcher = fleet.add("Watcher", "Watch the logs", lead, ongoing=True)
    words = kadence.WORDS
    top, low, standing = (brief(lead, None, [], words), brief(worker, lead, [], words),
                          brief(watcher, lead, [], words))
    assert "You are Lead, a K2 agent" in top and "Your task: Ship the site" in top
    assert "A person gave you this task" in top and "mcp__fleet__delegate" in top
    assert "Your supervisor is Lead (K2)" in low and "Your team: web." in low
    assert "lowest rank" in low and "mcp__fleet__delegate" not in low
    # A brief says what is so and what each tool does; how to work is the agent's own.
    for told in (top, low, standing):
        for instruction in ("you must", "you should", "do not", "make sure", "your job",
                            "only where", "keep at it", "make it complete"):
            assert instruction not in told.lower(), instruction
    assert "mcp__fleet__delegate starts a K1 agent on a task you give it" in top
    assert "finish_goal" in low and "finish_goal" not in standing
    assert "mcp__fleet__report" in standing
    other = brief(worker, lead, [], Vocabulary(finish="done", ask="ask_boss", tools="fleet."))
    assert "done with a report ends the task" in other
    assert "ask_boss puts a question to it" in other
    assert "finish_goal" not in other and "mcp__" not in other
