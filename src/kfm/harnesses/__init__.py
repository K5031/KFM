"""What runs a fleet's units: Kadence (kfm.harnesses.kadence), behind kfm.api."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from kfm.api import Harness
from kfm.harnesses import kadence


def load(options: Mapping[str, Any]) -> Harness:
    """Kadence, made with the settings' options. ValueError when it does not take them."""
    try:
        return kadence.factory(dict(options))
    except TypeError as e:
        raise ValueError(f"harness_options: {e}") from e
