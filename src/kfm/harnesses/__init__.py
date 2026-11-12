"""Harnesses: the agent programs a fleet's units can run (kfm.api). `load` finds one by
the name the fleet's settings give it."""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from importlib.metadata import entry_points
from typing import Any

from kfm.api import Harness

GROUP = "kfm.harnesses"


def load(name: str, options: Mapping[str, Any]) -> Harness:
    """The harness `name`: an entry point of the group "kfm.harnesses", or
    "package.module:factory". ValueError when there is none or it does not take the
    options."""
    try:
        if ":" in name:
            module, _, attribute = name.partition(":")
            factory = getattr(importlib.import_module(module), attribute)
        else:
            found = entry_points(group=GROUP, name=name)
            if not found:
                known = ", ".join(sorted(e.name for e in entry_points(group=GROUP)))
                raise ValueError(f"there is no harness {name!r}; installed: {known or 'none'}")
            factory = next(iter(found)).load()
        return factory(dict(options))
    except (ImportError, AttributeError, TypeError) as e:
        raise ValueError(f"the harness {name!r} could not be loaded: {e}") from e
