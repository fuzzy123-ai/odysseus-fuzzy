"""Deterministic JSON rendering for release morning payloads."""
from __future__ import annotations

import json

from src.release_morning_payload import ReleaseMorningPayload, build_current_release_morning_payload


def render_release_morning_payload_json(payload: ReleaseMorningPayload) -> str:
    return json.dumps(payload.to_dict(), indent=2, sort_keys=True)


def render_current_release_morning_payload_json() -> str:
    return render_release_morning_payload_json(build_current_release_morning_payload())
