from pathlib import Path

import pytest

from kfm import config
from kfm.config import FleetConfig, load


def test_defaults_without_a_file(tmp_path: Path):
    assert load(tmp_path / "fleet.toml") == FleetConfig()


def test_a_teams_model_wins_over_its_ranks(tmp_path: Path):
    path = tmp_path / "fleet.toml"
    path.write_text('top_rank = 3\nbudget_usd = 4.5\n[models]\nK3 = "big"\ndefault = "small"\n'
                    '[teams]\nmarketing = "wordy"\n')
    config = load(path, unit_budget_usd=0.5, top_rank=None)
    assert (config.top_rank, config.budget_usd, config.unit_budget_usd) == (3, 4.5, 0.5)
    assert config.model_for(3, None) == "big"
    assert config.model_for(2, None) == "small"
    assert config.model_for(3, "marketing") == "wordy"
    assert FleetConfig().model_for(5, None) is None  # the base configuration's model


@pytest.mark.parametrize("text", ["nonsense = 1", "top_rank = 6", "budget_usd = 0",
                                  "check_in_s = [10, 5]"])
def test_a_wrong_setting_is_named(tmp_path: Path, text: str):
    path = tmp_path / "fleet.toml"
    path.write_text(text)
    with pytest.raises(ValueError):
        load(path)


def test_settings_are_saved_beside_what_the_file_already_says(tmp_path: Path):
    path = tmp_path / "fleet.toml"
    path.write_text('harness = "kadence"\n[models]\nK5 = "old"\nK1 = "small"\n')
    saved = config.save(path, budget_usd=25.0, max_units=6,
                        models={"K5": "big-1", "K1": "", "K3": "mid-1"})
    assert saved == config.load(path)
    assert (saved.budget_usd, saved.max_units, saved.harness) == (25.0, 6, "kadence")
    assert saved.models == {"K5": "big-1", "K3": "mid-1"}  # an empty one is taken out
    with pytest.raises(ValueError, match="above zero"):
        config.save(path, budget_usd=0)
    assert config.load(path).budget_usd == 25.0  # nothing was written


def test_real_world_actions_ask_unless_the_setting_grants_them(tmp_path: Path):
    path = tmp_path / "fleet.toml"
    assert load(path).real_world_approval is True
    path.write_text("real_world_approval = false\n")
    assert load(path).real_world_approval is False


@pytest.mark.parametrize("text", ['real_world_approval = "no"', "real_world_approval = 0"])
def test_a_real_world_setting_that_is_not_true_or_false_is_refused(tmp_path: Path, text: str):
    path = tmp_path / "fleet.toml"
    path.write_text(text)
    with pytest.raises(ValueError, match="real_world_approval"):
        load(path)
