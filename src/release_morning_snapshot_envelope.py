"""Envelope for storing or handing off release morning payload snapshots."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from src.release_morning_payload import ReleaseMorningPayload, build_current_release_morning_payload
from src.release_morning_payload_digest import release_morning_payload_digest
from src.release_morning_payload_json import render_release_morning_payload_json


@dataclass(frozen=True)
class ReleaseMorningSnapshotEnvelope:
    digest: str
    payload: ReleaseMorningPayload
    payload_json: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "payload": json.loads(self.payload_json),
            "payload_json": self.payload_json,
        }


def build_release_morning_snapshot_envelope(payload: ReleaseMorningPayload) -> ReleaseMorningSnapshotEnvelope:
    return ReleaseMorningSnapshotEnvelope(
        digest=release_morning_payload_digest(payload),
        payload=payload,
        payload_json=render_release_morning_payload_json(payload),
    )


def build_current_release_morning_snapshot_envelope() -> ReleaseMorningSnapshotEnvelope:
    return build_release_morning_snapshot_envelope(build_current_release_morning_payload())
