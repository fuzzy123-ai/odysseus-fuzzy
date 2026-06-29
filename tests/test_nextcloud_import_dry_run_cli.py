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
        "max_samples": 10,
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
