"""Diff helpers for release morning snapshot envelopes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.release_morning_payload_diff import ReleaseMorningPayloadDiff, diff_release_morning_payloads
from src.release_morning_snapshot_envelope_contract import validate_release_morning_snapshot_envelope_contract


@dataclass(frozen=True)
class ReleaseMorningSnapshotEnvelopeDiff:
    ok: bool
    digest_changed: bool
    payload_diff: ReleaseMorningPayloadDiff | None = None
    errors: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return self.digest_changed or bool(self.payload_diff and self.payload_diff.changed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "changed": self.changed,
            "digest_changed": self.digest_changed,
            "payload_diff": self.payload_diff.to_dict() if self.payload_diff else None,
            "errors": self.errors,
        }


def diff_release_morning_snapshot_envelopes(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> ReleaseMorningSnapshotEnvelopeDiff:
    before_report = validate_release_morning_snapshot_envelope_contract(before)
    after_report = validate_release_morning_snapshot_envelope_contract(after)
    errors = tuple(f"before:{error}" for error in before_report.errors) + tuple(
        f"after:{error}" for error in after_report.errors
    )
    if errors:
        return ReleaseMorningSnapshotEnvelopeDiff(ok=False, digest_changed=False, errors=errors)

    digest_changed = before["digest"] != after["digest"]
    if not digest_changed:
        return ReleaseMorningSnapshotEnvelopeDiff(ok=True, digest_changed=False)

    payload_diff = diff_release_morning_payloads(before["payload"], after["payload"])
    return ReleaseMorningSnapshotEnvelopeDiff(
        ok=payload_diff.ok,
        digest_changed=True,
        payload_diff=payload_diff,
        errors=payload_diff.errors,
    )
