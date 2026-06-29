import json
from pathlib import Path

import pytest

from src.project_intake import apply_project_intake_proposal, build_project_intake_preview
from src.server_project_intake_state import (
    ServerProjectIntakeStateError,
    load_project_intake_state,
    merge_project_intake_ledger,
)
from src.server_project_registry import ServerProjectRegistry


def _registry() -> ServerProjectRegistry:
    registry = ServerProjectRegistry()
    registry.create_project(
        project_title="Kundenportal MVP",
        project_type="app",
        created_at="2026-06-29T10:00:00Z",
    )
    return registry


def _write_ledger(tmp_path: Path, registry: ServerProjectRegistry, text: str) -> Path:
    proposal = build_project_intake_preview(
        registry=registry,
        text=text,
        source_channel="telegram",
    ).to_dict()
    ledger_path = tmp_path / "project_intake_ledger.json"
    apply_project_intake_proposal(
        registry=registry,
        project_slug="kundenportal-mvp",
        proposal=proposal,
        ledger_path=ledger_path,
        applied_at="2026-06-29T12:00:00Z",
        applied_by="telegram",
        review_confirmed=True,
    )
    return ledger_path


def test_merge_project_intake_ledger_writes_deduped_project_state(tmp_path: Path):
    registry = _registry()
    record = registry.get("kundenportal-mvp")
    ledger_path = _write_ledger(
        tmp_path,
        registry,
        (
            "#project:kundenportal-mvp\n"
            "TODO: Login als MVP Slice aufnehmen.\n"
            "Roadmap: Release Smoke unterwegs pruefbar machen.\n"
            "Risiko: DSGVO Review vor Livegang."
        ),
    )
    state_path = tmp_path / "project_state.json"

    first = merge_project_intake_ledger(
        record=record,
        ledger_path=ledger_path,
        state_path=state_path,
        merged_at="2026-06-29T12:05:00Z",
    )
    second = merge_project_intake_ledger(
        record=record,
        ledger_path=ledger_path,
        state_path=state_path,
        merged_at="2026-06-29T12:06:00Z",
    )

    assert first.to_dict()["status"] == "merged"
    assert first.added_task_count == 2
    assert first.added_risk_count == 1
    assert first.added_roadmap_update_count == 1
    assert second.added_task_count == 0
    assert second.processed_event_count == 0
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["schema"] == "odysseus.project_intake.state.v1"
    assert [task["title"] for task in state["tasks"]] == [
        "Login als MVP Slice aufnehmen.",
        "Release Smoke unterwegs pruefbar machen.",
    ]
    assert state["tasks"][0]["status"] == "planned"
    assert state["risks"][0]["text"] == "DSGVO Review vor Livegang."
    assert state["raw_content_persisted"] is False
    assert str(tmp_path) not in json.dumps(first.to_dict())


def test_merge_project_intake_ledger_can_merge_single_event(tmp_path: Path):
    registry = _registry()
    record = registry.get("kundenportal-mvp")
    ledger_path = _write_ledger(tmp_path, registry, "#project:kundenportal-mvp TODO: Login bauen.")
    event_id = json.loads(ledger_path.read_text(encoding="utf-8"))["events"][0]["event_id"]

    report = merge_project_intake_ledger(
        record=record,
        ledger_path=ledger_path,
        state_path=tmp_path / "project_state.json",
        merged_at="2026-06-29T12:05:00Z",
        source_event_id=event_id,
    )

    assert report.merged is True
    assert report.processed_event_count == 1
    state = load_project_intake_state(record=record, state_path=tmp_path / "project_state.json")
    assert state["processed_event_ids"] == [event_id]


def test_merge_project_intake_ledger_blocks_missing_or_wrong_event(tmp_path: Path):
    registry = _registry()
    record = registry.get("kundenportal-mvp")
    ledger_path = _write_ledger(tmp_path, registry, "#project:kundenportal-mvp TODO: Login bauen.")

    report = merge_project_intake_ledger(
        record=record,
        ledger_path=ledger_path,
        state_path=tmp_path / "project_state.json",
        merged_at="2026-06-29T12:05:00Z",
        source_event_id="missing-event",
    ).to_dict()

    assert report["merged"] is False
    assert "source_event_id_not_found" in report["blockers"]


def test_merge_project_intake_state_rejects_secret_like_existing_state(tmp_path: Path):
    registry = _registry()
    record = registry.get("kundenportal-mvp")
    ledger_path = _write_ledger(tmp_path, registry, "#project:kundenportal-mvp TODO: Login bauen.")
    state_path = tmp_path / "project_state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema": "odysseus.project_intake.state.v1",
                "project_slug": "kundenportal-mvp",
                "project_title": "Kundenportal MVP",
                "tasks": [{"title": "token=abc123", "kind": "task"}],
                "decisions": [],
                "risks": [],
                "roadmap_updates": [],
                "processed_event_ids": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ServerProjectIntakeStateError, match="task.title.*secret material"):
        merge_project_intake_ledger(
            record=record,
            ledger_path=ledger_path,
            state_path=state_path,
            merged_at="2026-06-29T12:05:00Z",
        )
