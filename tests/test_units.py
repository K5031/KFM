import tomllib
from dataclasses import replace
from pathlib import Path

from kfm.config import FleetConfig
from kfm.fleet import Fleet
from kfm.units import brief, prepare


def test_a_units_folder_holds_the_base_configuration_with_its_own_limits(tmp_path: Path):
    base = tmp_path / "kayden.toml"
    base.write_text('[core]\nfactory = "x:y"\n[runtime]\nmodel = "base"\n'
                    'maintenance_model = "light"\n[frontend]\nfactory = "a:b"\n')
    config = replace(FleetConfig(), kayden_config=base, models={"K4": "mid"})
    fleet = Fleet()
    lead = fleet.add("Lead", "Do it", None, budget_usd=2.0)
    scout = fleet.add("Scout", "Look", lead, budget_usd=0.5)
    for unit in (lead, scout):
        prepare(unit, tmp_path / unit.id, config, tmp_path / "work", "http://127.0.0.1:1/mcp")
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
        {"name": "fleet", "url": f"http://127.0.0.1:1/mcp?unit={scout.token}"}]


def test_the_brief_says_where_the_unit_stands():
    fleet = Fleet(top_rank=2)
    lead = fleet.add("Lead", "Ship the site", None)
    worker = fleet.add("Builder", "Build the page", lead, team="web")
    watcher = fleet.add("Watcher", "Watch the logs", lead, ongoing=True)
    top, low, standing = brief(lead, None), brief(worker, lead), brief(watcher, lead)
    assert "You are Lead, a K2 agent" in top and "Your task: Ship the site" in top
    assert "A person gave you this task" in top and "mcp__fleet__delegate" in top
    assert "Your supervisor is Lead (K2)" in low and "Your team: web." in low
    assert "lowest rank" in low and "mcp__fleet__delegate" not in low
    assert "finish_goal" in low and "finish_goal" not in standing
    assert "mcp__fleet__report" in standing
