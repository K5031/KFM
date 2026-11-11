"""What a person sets for a fleet: its size, its spending, its models."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

HOME = Path.home() / ".kfm"
CONFIG_FILE = "fleet.toml"


@dataclass(frozen=True, slots=True)
class FleetConfig:
    """`models` maps a rank ("K5") or "default" to a model id, `teams` a team's name to one;
    a team's model wins over its rank's. A unit with no model here uses the one in
    `kayden_config`, the Kayden configuration every unit starts from (its provider, key
    and options)."""

    top_rank: int = 5
    max_units: int = 12
    budget_usd: float = 10.0  # the whole fleet; reaching it stops every unit
    unit_budget_usd: float = 2.0  # each unit's own limit, unless its supervisor sets less
    permissions: str = "allow_workspace"  # Kayden's level for every unit
    models: Mapping[str, str] = field(default_factory=dict)
    teams: Mapping[str, str] = field(default_factory=dict)
    kayden_config: Path = Path.home() / ".kayden" / "config.toml"
    # A unit's duty intervals: it carries on soon after a turn, and checks in this often
    # when nothing wakes it.
    check_in_s: tuple[float, float] = (5.0, 300.0)

    def model_for(self, rank: int, team: str | None) -> str | None:
        return (self.teams.get(team or "") or self.models.get(f"K{rank}")
                or self.models.get("default"))


def load(path: Path, **overrides: Any) -> FleetConfig:
    """The fleet.toml at `path` (defaults when there is none), then `overrides` that are not
    None. ValueError names a setting that does not exist or does not fit."""
    data: dict[str, Any] = tomllib.loads(path.read_text()) if path.exists() else {}
    data.update({k: v for k, v in overrides.items() if v is not None})
    known = {f.name for f in fields(FleetConfig)}
    if unknown := sorted(set(data) - known):
        raise ValueError(f"{path}: unknown setting {', '.join(unknown)}")
    if "kayden_config" in data:
        data["kayden_config"] = Path(data["kayden_config"]).expanduser()
    if "check_in_s" in data:
        data["check_in_s"] = tuple(data["check_in_s"])
    config = FleetConfig(**data)
    if not 1 <= config.top_rank <= 5:
        raise ValueError("top_rank is between 1 and 5")
    if config.max_units < 1 or config.budget_usd <= 0 or config.unit_budget_usd <= 0:
        raise ValueError("max_units, budget_usd and unit_budget_usd are above zero")
    low, high = config.check_in_s
    if not 0 < low <= high:
        raise ValueError("check_in_s is [shortest, longest] in seconds")
    return config
