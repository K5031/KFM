"""What a person sets for a fleet: its size, its spending, its models."""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import tomli_w

HOME = Path.home() / ".kfm"
CONFIG_FILE = "fleet.toml"


@dataclass(frozen=True, slots=True)
class FleetConfig:
    """`models` maps a rank ("K5") or "default" to a model id, `teams` a team's name to one;
    a team's model wins over its rank's; a unit with no model here uses its harness's own.
    `harness` is the agent program every unit runs (kfm.harnesses.load) and
    `harness_options` what it is made with."""

    top_rank: int = 5
    max_units: int = 12
    budget_usd: float = 10.0  # the whole fleet; reaching it stops every unit
    unit_budget_usd: float = 2.0  # each unit's own limit, unless its supervisor sets less
    permissions: str = "allow_workspace"  # Kayden's level for every unit
    models: Mapping[str, str] = field(default_factory=dict)
    teams: Mapping[str, str] = field(default_factory=dict)
    harness: str = "kayden"
    harness_options: Mapping[str, Any] = field(default_factory=dict)
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
    return _config(data, str(path), overrides)


def save(path: Path, **changes: Any) -> FleetConfig:
    """Write `changes` into the fleet.toml at `path`, beside what it already says, and
    return the settings it then holds. In `models` an empty id takes the rank's entry
    out. ValueError, and nothing written, for a setting that does not fit. Blocking."""
    data: dict[str, Any] = tomllib.loads(path.read_text()) if path.exists() else {}
    models = {**data.get("models", {}), **changes.pop("models", {})}
    data.update(changes, models={rank: model for rank, model in models.items() if model})
    config = _config(dict(data), str(path), {})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(data))
    return config


def record(config: FleetConfig) -> dict[str, Any]:
    """The settings as fleet.json keeps them, for `from_record`."""
    return {**asdict(config), "check_in_s": list(config.check_in_s)}


def from_record(data: Mapping[str, Any], **overrides: Any) -> FleetConfig:
    """The settings a fleet ran with, then `overrides` that are not None."""
    return _config(dict(data), "the fleet's record", overrides)


def _config(data: dict[str, Any], source: str, overrides: Mapping[str, Any]) -> FleetConfig:
    data.update({k: v for k, v in overrides.items() if v is not None})
    known = {f.name for f in fields(FleetConfig)}
    if unknown := sorted(set(data) - known):
        raise ValueError(f"{source}: unknown setting {', '.join(unknown)}")
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
