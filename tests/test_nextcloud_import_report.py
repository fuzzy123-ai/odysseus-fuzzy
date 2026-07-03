import json

from src.bigdata_ledger_contract import AppendOnlyBigDataLedger, BigDataLedgerItem, BigDataLedgerRecord
from src.nextcloud_import_report import build_nextcloud_import_dry_run_report


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
    file_category: str,
    privacy_class: str = "archive_candidate",
    long_path: bool = False,
    size: int = 64,
) -> BigDataLedgerRecord:
    return BigDataLedgerRecord.create(
        _item(path, size=size),
        stage="inventory",
        status="completed",
        metadata={
            "scanner": "test",
            "file_category": file_category,
            "long_path": long_path,
            "privacy": {
                "privacy_class": privacy_class,
                "archive_allowed": privacy_class == "archive_candidate",
                "mirror_to_new_nextcloud": privacy_class == "archive_candidate",
                "memory_write_candidate": True,
                "local_model_only": privacy_class != "archive_candidate",
                "required_model_scope": "local_only" if privacy_class != "archive_candidate" else "policy_selected",
            },
        },
    )


def test_import_report_summarizes_candidates_without_private_content(tmp_path):
    ledger_path = tmp_path / "events.jsonl"
    ledger = AppendOnlyBigDataLedger(ledger_path)
    for record in [
        _inventory("Daten/notes.md", file_category="text_extractable"),
        _inventory("Daten/report.pdf", file_category="document_extractable", long_path=True),
        _inventory("Daten/photo.jpg", file_category="media_metadata"),
        _inventory("Privat/local.docx", file_category="document_extractable", privacy_class="local_sensitive"),
        _inventory("Daten/archive.zip", file_category="archive_review"),
        _inventory("Daten/tool/bin/app.exe", file_category="dangerous_or_binary", size=10),
        _inventory("Daten/tool/bin/helper.dll", file_category="dangerous_or_binary", size=10),
    ]:
        ledger.append_record(record)

    report = build_nextcloud_import_dry_run_report(ledger_path=ledger_path, source_id="nextcloud-main")
    payload = report.to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["inventory_total"] == 7
    assert payload["document_candidates"] == 2
    assert payload["document_candidate_profile"] == {
        "total_document_inventory": 3,
        "safe_candidates": 2,
        "private_review_candidates": 1,
        "by_extension": {
            ".docx": {"total": 1, "safe": 0, "private_review": 1},
            ".md": {"total": 1, "safe": 1, "private_review": 0},
            ".pdf": {"total": 1, "safe": 1, "private_review": 0},
        },
    }
    assert payload["metadata_only_candidates"] == 5
    assert payload["review_candidates"] == 4
    assert payload["long_path_count"] == 1
    assert payload["software_archive_candidates"] == 1
    assert payload["software_archive_paths"] == ("Software Archives/daten-tool.zip",)
    assert payload["by_privacy_class"]["local_sensitive"] == 1
    assert payload["private_content_visible"] is False
    assert payload["secret_values_visible"] is False
    assert "local.docx" in encoded
    assert "raw private body" not in encoded


def test_import_report_ignores_other_sources_and_non_inventory(tmp_path):
    ledger_path = tmp_path / "events.jsonl"
    ledger = AppendOnlyBigDataLedger(ledger_path)
    ledger.append_record(_inventory("Daten/a.md", file_category="text_extractable"))
    ledger.append_record(
        BigDataLedgerRecord.create(
            _item("Daten/other.md", source_id="other-source"),
            stage="inventory",
            status="completed",
            metadata={"file_category": "text_extractable"},
        )
    )
    ledger.append_record(
        BigDataLedgerRecord.create(
            _item("Daten/a.md"),
            stage="analysis",
            status="needs_review",
            metadata={"planner": "test"},
        )
    )

    report = build_nextcloud_import_dry_run_report(ledger_path=ledger_path, source_id="nextcloud-main")

    assert report.inventory_total == 1
    assert report.document_candidates == 1
    assert report.document_candidate_profile["safe_candidates"] == 1
