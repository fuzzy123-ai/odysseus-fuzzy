"""Strict process startup modes shared by import-time and lazy components."""

from __future__ import annotations

import os


STARTUP_OFFLINE_ENV = "ODYSSEUS_STARTUP_OFFLINE"


def startup_offline_enabled() -> bool:
    """Return whether optional network-backed startup services are disabled.

    The setting deliberately accepts only the two lowercase literals. A typo
    must not silently re-enable network access during an isolated startup.
    """

    value = os.environ.get(STARTUP_OFFLINE_ENV)
    if value is None:
        return False
    if value not in {"true", "false"}:
        raise RuntimeError("invalid_startup_mode_configuration")
    return value == "true"
