"""Deterministic JSON rendering for release morning snapshot history."""
from __future__ import annotations

import json

from src.release_morning_snapshot_history import ReleaseMorningSnapshotHistory


def render_release_morning_snapshot_history_json(history: ReleaseMorningSnapshotHistory) -> str:
    return json.dumps(history.to_dict(), indent=2, sort_keys=True)
