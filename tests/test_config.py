from pathlib import Path

import pytest

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
