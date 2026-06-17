"""Deterministic JSON rendering for release morning snapshot envelope diffs."""
from __future__ import annotations

import json

from src.release_morning_snapshot_envelope_diff import ReleaseMorningSnapshotEnvelopeDiff


def render_release_morning_snapshot_envelope_diff_json(diff: ReleaseMorningSnapshotEnvelopeDiff) -> str:
    return json.dumps(diff.to_dict(), indent=2, sort_keys=True)
