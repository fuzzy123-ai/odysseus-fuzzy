"""Bundle release morning snapshot history with validation and renderers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.release_morning_snapshot_history import (
    ReleaseMorningSnapshotHistory,
    build_release_morning_snapshot_history,
)
from src.release_morning_snapshot_history_contract import ReleaseMorningSnapshotHistoryContractReport
from src.release_morning_snapshot_history_contract import validate_release_morning_snapshot_history_contract
from src.release_morning_snapshot_history_json import render_release_morning_snapshot_history_json
from src.release_morning_snapshot_history_markdown import render_release_morning_snapshot_history_markdown


@dataclass(frozen=True)
class ReleaseMorningSnapshotHistoryBundle:
    history: ReleaseMorningSnapshotHistory
    contract_report: ReleaseMorningSnapshotHistoryContractReport
    markdown: str
    json_payload: str

    @property
    def ok(self) -> bool:
        return self.contract_report.ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "history": self.history.to_dict(),
            "contract_report": self.contract_report.to_dict(),
            "markdown": self.markdown,
            "json_payload": self.json_payload,
        }


def build_release_morning_snapshot_history_bundle(
    envelopes: tuple[Mapping[str, Any], ...],
) -> ReleaseMorningSnapshotHistoryBundle:
    history = build_release_morning_snapshot_history(envelopes)
    contract_report = validate_release_morning_snapshot_history_contract(history.to_dict())
    return ReleaseMorningSnapshotHistoryBundle(
        history=history,
        contract_report=contract_report,
        markdown=render_release_morning_snapshot_history_markdown(history),
        json_payload=render_release_morning_snapshot_history_json(history),
    )
