import json

from src.bigdata_ledger_contract import AppendOnlyBigDataLedger, BigDataLedgerItem, BigDataLedgerRecord
from src.nextcloud_document_pilot_import import (
    append_nextcloud_document_pilot_plan,
    build_nextcloud_document_pilot_plan,
)


def _item(path: str, *, source_id: str = "nextcloud-main", size: int = 64) -> BigDataLedgerItem:
    return BigDataLedgerItem(
        provider="nextcloud",
        source_id=source_id,
        relative_path=path,
        size_bytes=size,
        mtime="2026-06-29T10:00:00Z",
    )


def _inventory(
    path: str,
    *,
    file_category: str = "text_extractable",
    privacy_class: str = "archive_candidate",
    source_id: str = "nextcloud-main",
) -> BigDataLedgerRecord:
    return BigDataLedgerRecord.create(
        _item(path, source_id=source_id),
        stage="inventory",
        status="completed",
        metadata={
            "scanner": "test",
            "file_category": file_category,
            "extension": "." + path.rsplit(".", 1)[-1] if "." in path else "",
            "privacy": {
                "privacy_class": privacy_class,
                "archive_allowed": privacy_class == "archive_candidate",
                "mirror_to_new_nextcloud": privacy_class == "archive_candidate",
                "memory_write_candidate": privacy_class == "archive_candidate",
                "local_model_only": privacy_class != "archive_candidate",
                "required_model_scope": "local_only" if privacy_class != "archive_candidate" else "policy_selected",
            },
        },
    )


def test_document_pilot_plan_selects_bounded_safe_documents_only():
    plan = build_nextcloud_document_pilot_plan(
        [
            _inventory("Daten/a.md"),
            _inventory("Daten/b.pdf", file_category="document_extractable"),
            _inventory("Daten/photo.jpg", file_category="media_metadata"),
            _inventory("Privat/private.docx", file_category="document_extractable", privacy_class="local_sensitive"),
            _inventory("Other/source.md", source_id="other-source"),
        ],
        source_id="nextcloud-main",
        batch_limit=1,
    )
    payload = plan.to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert plan.selected_count == 1
    assert plan.candidate_count == 2
    assert plan.skipped_non_document == 1
    assert plan.skipped_private == 1
    assert plan.interrupted is True
    assert payload["selected_items"][0]["relative_path"] == "Daten/a.md"
    assert payload["selected_items"][0]["review_required"] is False
    assert "private body" not in encoded
    assert payload["private_content_visible"] is False
    assert payload["secret_values_visible"] is False


def test_document_pilot_plan_can_include_private_as_local_only_review():
    plan = build_nextcloud_document_pilot_plan(
        [
            _inventory("Privat/private.docx", file_category="document_extractable", privacy_class="local_sensitive"),
        ],
        source_id="nextcloud-main",
        batch_limit=10,
        include_private=True,
    )
    item = plan.selected_items[0].to_dict()

    assert plan.selected_count == 1
    assert item["privacy_class"] == "local_sensitive"
    assert item["local_model_only"] is True
    assert item["required_model_scope"] == "local_only"
    assert item["rag_index_candidate"] is False
    assert item["memory_write_candidate"] is False
    assert item["review_required"] is True


def test_append_document_pilot_plan_records_one_redacted_analysis_event_and_resumes(tmp_path):
    ledger_path = tmp_path / "events.jsonl"
    ledger = AppendOnlyBigDataLedger(ledger_path)
    for record in [
        _inventory("Daten/a.md"),
        _inventory("Daten/b.pdf", file_category="document_extractable"),
        _inventory("Daten/photo.jpg", file_category="media_metadata"),
    ]:
        ledger.append_record(record)

    first = append_nextcloud_document_pilot_plan(
        ledger_path=ledger_path,
        source_id="nextcloud-main",
        pilot_id="pilot-documents",
        batch_limit=1,
    )
    second = append_nextcloud_document_pilot_plan(
        ledger_path=ledger_path,
        source_id="nextcloud-main",
        pilot_id="pilot-documents",
        batch_limit=10,
    )
    latest = AppendOnlyBigDataLedger(ledger_path).latest_state()
    analysis = [
        record
        for record in latest.values()
        if record.stage == "analysis"
        and record.metadata.get("planner") == "nextcloud_document_pilot_import"
    ]
    encoded_ledger = ledger_path.read_text(encoding="utf-8")

    assert first.appended is True
    assert first.plan.selected_count == 1
    assert second.appended is True
    assert second.plan.selected_count == 1
    assert second.plan.skipped_existing == 1
    assert len(analysis) == 1
    assert analysis[0].status == "needs_review"
    assert analysis[0].metadata["dry_run"] is True
    assert analysis[0].metadata["review_required"] is True
    assert analysis[0].metadata["selected_count"] == 1
    assert "private document body" not in encoded_ledger
    assert "authorization=" not in encoded_ledger


def test_append_document_pilot_plan_noops_when_no_candidates(tmp_path):
    ledger_path = tmp_path / "events.jsonl"
    AppendOnlyBigDataLedger(ledger_path).append_record(
        _inventory("Daten/photo.jpg", file_category="media_metadata")
    )

    result = append_nextcloud_document_pilot_plan(
        ledger_path=ledger_path,
        source_id="nextcloud-main",
        batch_limit=10,
    )

    assert result.appended is False
    assert result.plan.selected_count == 0
    assert result.plan.skipped_non_document == 1
