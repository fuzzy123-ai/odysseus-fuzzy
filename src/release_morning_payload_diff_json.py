"""Deterministic JSON rendering for release morning payload diffs."""
from __future__ import annotations

import json

from src.release_morning_payload_diff import ReleaseMorningPayloadDiff


def render_release_morning_payload_diff_json(diff: ReleaseMorningPayloadDiff) -> str:
    return json.dumps(diff.to_dict(), indent=2, sort_keys=True)
