import json
from pathlib import Path

from scripts.nextcloud_import_dry_run import main, run_pipeline


def test_cli_pipeline_scans_plans_and_reports_without_raw_content(tmp_path, capsys):
    root = tmp_path / "source"
    root.mkdir()
    (root / "Daten").mkdir()
    (root / "Daten" / "notes.md").write_text("private note body", encoding="utf-8")
    (root / "Daten" / "tool").mkdir()
    (root / "Daten" / "tool" / "app.exe").write_bytes(b"MZ")
    (root / "Daten" / "tool" / "helper.dll").write_bytes(b"MZ")
    (root / "Desktop.ini").write_text("system", encoding="utf-8")
    ledger_path = tmp_path / "ledger" / "events.jsonl"
    config_path = _open_test_config(tmp_path)

    rc = main(
        [
            "--config",
            str(config_path),
            "--root",
            str(root),
            "--ledger-path",
            str(ledger_path),
            "--format",
            "json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    encoded = json.dumps(payload, sort_keys=True)

    assert rc == 0
    assert payload["dry_run"] is True
    assert payload["scan"]["scanned"] == 4
    assert payload["scan"]["excluded"] == 1
    assert payload["software_archives"]["planned"] == 1
    assert payload["document_pilot"]["plan"]["selected_count"] == 1
    assert payload["report"]["inventory_total"] == 3
    assert payload["report"]["document_candidates"] == 1
    assert payload["report"]["software_archive_candidates"] == 1
    assert payload["private_content_visible"] is False
    assert "private note body" not in encoded


def test_pipeline_can_report_existing_ledger_without_root(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "notes.md").write_text("runtime body", encoding="utf-8")
    ledger_path = tmp_path / "ledger.jsonl"
    first = run_pipeline(
        _args(
            root=str(root),
            ledger_path=str(ledger_path),
            skip_scan=False,
            skip_software_plan=True,
        )
    )

    second = run_pipeline(
        _args(
            root="",
            ledger_path=str(ledger_path),
            skip_scan=True,
            skip_software_plan=True,
        )
    )

    assert first["scan"]["committed"] == 1
    assert second["scan"] is None
    assert second["report"]["inventory_total"] == 1


def test_pipeline_requires_runtime_root_unless_scan_is_skipped(tmp_path):
    try:
        run_pipeline(
            _args(
                root="",
                ledger_path=str(tmp_path / "ledger.jsonl"),
                skip_scan=False,
                skip_software_plan=True,
            )
        )
    except SystemExit as exc:
        assert "source root is required" in str(exc)
    else:
        raise AssertionError("missing root should block scans")


def test_cli_ephemeral_ledger_deletes_metadata_after_report(tmp_path, capsys):
    root = tmp_path / "source"
    root.mkdir()
    (root / "notes.md").write_text("runtime body", encoding="utf-8")
    ledger_path = tmp_path / "ledger" / "events.jsonl"
    config_path = _open_test_config(tmp_path)

    rc = main(
        [
            "--config",
            str(config_path),
            "--root",
            str(root),
            "--ledger-path",
            str(ledger_path),
            "--skip-software-plan",
            "--skip-document-pilot",
            "--ephemeral-ledger",
            "--format",
            "json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload["report"]["inventory_total"] == 1
    assert payload["ephemeral_ledger"] == {
        "enabled": True,
        "deleted": True,
        "reason": "deleted_after_report",
    }
    assert not ledger_path.exists()


def test_cli_can_emit_local_only_document_pilot_profile(tmp_path, capsys):
    root = tmp_path / "source"
    root.mkdir()
    (root / "Privat").mkdir()
    (root / "Privat" / "private.docx").write_text("private body", encoding="utf-8")
    ledger_path = tmp_path / "ledger" / "events.jsonl"
    config_path = _open_test_config(tmp_path)

    rc = main(
        [
            "--config",
            str(config_path),
            "--root",
            str(root),
            "--ledger-path",
            str(ledger_path),
            "--skip-software-plan",
            "--local-only-document-pilot-profile",
            "--format",
            "json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    encoded = json.dumps(payload, sort_keys=True)

    assert rc == 0
    assert payload["document_pilot"]["plan"]["selected_count"] == 0
    assert payload["local_only_document_pilot"]["profile"]["selected_count"] == 1
    assert payload["local_only_document_pilot"]["profile"]["selected_items_redacted"] is True
    assert "private body" not in encoded
    assert "private.docx" not in encoded


def test_cli_can_emit_local_only_extraction_review_plan(tmp_path, capsys):
    root = tmp_path / "source"
    root.mkdir()
    (root / "Privat").mkdir()
    (root / "Privat" / "private.docx").write_text("private body", encoding="utf-8")
    ledger_path = tmp_path / "ledger" / "events.jsonl"
    config_path = _open_test_config(tmp_path)

    rc = main(
        [
            "--config",
            str(config_path),
            "--root",
            str(root),
            "--ledger-path",
            str(ledger_path),
            "--skip-software-plan",
            "--local-only-extraction-review-plan",
            "--format",
            "json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    encoded = json.dumps(payload, sort_keys=True)
    plan = payload["local_only_extraction_review"]["plan"]

    assert rc == 0
    assert payload["document_pilot"]["plan"]["selected_count"] == 0
    assert plan["selected_count"] == 1
    assert plan["extractable_now_count"] == 1
    assert plan["raw_content_persisted"] is False
    assert plan["memory_writes_permitted"] is False
    assert plan["raptor_writes_permitted"] is False
    assert "private body" not in encoded
    assert "private.docx" not in encoded


def test_cli_blocks_local_only_extraction_review_run_without_operator_go(tmp_path, capsys):
    root = tmp_path / "source"
    root.mkdir()
    (root / "Privat").mkdir()
    (root / "Privat" / "private.md").write_text("private body", encoding="utf-8")
    ledger_path = tmp_path / "ledger" / "events.jsonl"
    config_path = _open_test_config(tmp_path)

    rc = main(
        [
            "--config",
            str(config_path),
            "--root",
            str(root),
            "--ledger-path",
            str(ledger_path),
            "--skip-software-plan",
            "--local-only-extraction-review-run",
            "--format",
            "json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    encoded = json.dumps(payload, sort_keys=True)
    run = payload["local_only_extraction_review_run"]

    assert rc == 0
    assert run["status"] == "blocked"
    assert run["processed_count"] == 0
    assert run["reasons"] == ["operator_local_extraction_go_required"]
    assert "private body" not in encoded
    assert "private.md" not in encoded


def test_cli_can_run_local_only_extraction_review_with_operator_go(tmp_path, capsys):
    root = tmp_path / "source"
    root.mkdir()
    (root / "Privat").mkdir()
    (root / "Privat" / "private.md").write_text("private body", encoding="utf-8")
    ledger_path = tmp_path / "ledger" / "events.jsonl"
    config_path = _open_test_config(tmp_path)

    rc = main(
        [
            "--config",
            str(config_path),
            "--root",
            str(root),
            "--ledger-path",
            str(ledger_path),
            "--skip-software-plan",
            "--local-only-extraction-review-run",
            "--operator-local-extraction-go",
            "--format",
            "json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    encoded = json.dumps(payload, sort_keys=True)
    run = payload["local_only_extraction_review_run"]

    assert rc == 0
    assert run["processed_count"] == 1
    assert run["appended_count"] == 1
    assert run["memory_writes_permitted"] is False
    assert run["raptor_writes_permitted"] is False
    assert "private body" not in encoded
    assert "private.md" not in encoded


def test_ephemeral_ledger_is_blocked_for_skip_scan_reports(tmp_path):
    try:
        run_pipeline(
            _args(
                root="",
                ledger_path=str(tmp_path / "ledger.jsonl"),
                skip_scan=True,
                skip_software_plan=True,
                ephemeral_ledger=True,
            )
        )
    except SystemExit as exc:
        assert "--ephemeral-ledger cannot be used with --skip-scan" in str(exc)
    else:
        raise AssertionError("ephemeral skip-scan should be blocked")


def _args(**overrides):
    values = {
        "config": "config/nextcloud_import_config.json",
        "root": "",
        "ledger_path": "",
        "source_id": "",
        "batch_limit": None,
        "scan_profile": "full",
        "skip_scan": False,
        "skip_software_plan": False,
        "skip_document_pilot": False,
        "pilot_id": "pilot-documents",
        "pilot_batch_limit": 100,
        "include_private_pilot_documents": False,
        "local_only_document_pilot_profile": False,
        "local_only_extraction_review_plan": False,
        "local_only_extraction_review_run": False,
        "operator_local_extraction_go": False,
        "max_samples": 10,
        "ephemeral_ledger": False,
        "format": "json",
    }
    values.update(overrides)
    return type("Args", (), values)()


def _open_test_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "nextcloud_import_config.json"
    config_path.write_text(
        json.dumps(
            {
                "schema": "odysseus.nextcloud_import_config.v1",
                "source_id": "nextcloud-main",
                "source_root_env": "ODYSSEUS_NEXTCLOUD_IMPORT_ROOT",
                "mode": "dry_run",
                "default_unknown_private": False,
                "sensitive_roots": ["Privat"],
                "exclude_names": ["Desktop.ini"],
                "exclude_globs": [".sync_*.db", "~$*", "*.tmp"],
                "include_zero_byte": False,
                "binary_extensions": [".exe", ".dll"],
                "document_extensions_initial": [".md", ".pdf", ".docx"],
                "software_archives": {
                    "enabled": True,
                    "dry_run": True,
                    "target_root": "Software Archives",
                    "review_required": True,
                },
            }
        ),
        encoding="utf-8",
    )
    return config_path
