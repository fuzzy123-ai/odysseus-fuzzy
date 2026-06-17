"""Stable digest helpers for release morning payloads."""
from __future__ import annotations

import hashlib

from src.release_morning_payload import ReleaseMorningPayload, build_current_release_morning_payload
from src.release_morning_payload_json import render_release_morning_payload_json


def release_morning_payload_digest(payload: ReleaseMorningPayload) -> str:
    rendered = render_release_morning_payload_json(payload)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def current_release_morning_payload_digest() -> str:
    return release_morning_payload_digest(build_current_release_morning_payload())
