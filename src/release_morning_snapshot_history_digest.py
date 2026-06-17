"""Stable digest helpers for release morning snapshot history."""
from __future__ import annotations

import hashlib

from src.release_morning_snapshot_history import ReleaseMorningSnapshotHistory
from src.release_morning_snapshot_history_json import render_release_morning_snapshot_history_json


def release_morning_snapshot_history_digest(history: ReleaseMorningSnapshotHistory) -> str:
    rendered = render_release_morning_snapshot_history_json(history)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()
