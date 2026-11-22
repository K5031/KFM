"""What runs a fleet's units: Kaden (kfm.harnesses.kaden), behind kfm.api."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from kfm.api import Harness
from kfm.harnesses import kaden


def load(options: Mapping[str, Any]) -> Harness:
    """Kaden, made with the settings' options. ValueError when it does not take them."""
    try:
        return kaden.factory(dict(options))
    except TypeError as e:
        raise ValueError(f"harness_options: {e}") from e
