"""In-memory history helpers for release morning snapshot envelopes."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.release_morning_snapshot_envelope_contract import validate_release_morning_snapshot_envelope_contract
from src.release_morning_snapshot_envelope_diff import ReleaseMorningSnapshotEnvelopeDiff, diff_release_morning_snapshot_envelopes


@dataclass(frozen=True)
class ReleaseMorningSnapshotHistory:
    envelopes: tuple[Mapping[str, Any], ...]

    @property
    def latest(self) -> Mapping[str, Any] | None:
        return self.envelopes[-1] if self.envelopes else None

    @property
    def previous(self) -> Mapping[str, Any] | None:
        return self.envelopes[-2] if len(self.envelopes) >= 2 else None

    def latest_diff(self) -> ReleaseMorningSnapshotEnvelopeDiff | None:
        if self.previous is None or self.latest is None:
            return None
        return diff_release_morning_snapshot_envelopes(self.previous, self.latest)

    def to_dict(self) -> dict[str, Any]:
        latest = self.latest
        previous = self.previous
        latest_diff = self.latest_diff()
        return {
            "count": len(self.envelopes),
            "latest_digest": latest.get("digest") if latest else None,
            "previous_digest": previous.get("digest") if previous else None,
            "latest_diff": latest_diff.to_dict() if latest_diff else None,
        }


def build_release_morning_snapshot_history(
    envelopes: tuple[Mapping[str, Any], ...],
) -> ReleaseMorningSnapshotHistory:
    for index, envelope in enumerate(envelopes):
        report = validate_release_morning_snapshot_envelope_contract(envelope)
        if not report.ok:
            joined = ",".join(report.errors)
            raise ValueError(f"invalid envelope at index {index}: {joined}")
    return ReleaseMorningSnapshotHistory(envelopes=envelopes)
