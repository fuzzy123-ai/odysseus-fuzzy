import json

from src.bigdata_ledger_contract import (
    AppendOnlyBigDataLedger,
    BigDataLedgerItem,
    BigDataLedgerRecord,
)
from src.nextcloud_software_archives import (
    build_nextcloud_software_archive_plans,
    plan_nextcloud_software_archive_metadata,
)


def _item(path: str, *, size: int = 1024, source_id: str = "nextcloud-main") -> BigDataLedgerItem:
    return BigDataLedgerItem(
        provider="nextcloud",
        source_id=source_id,
        relative_path=path,
        size_bytes=size,
        mtime="2026-06-22T10:00:00Z",
    )


def _inventory(path: str, *, size: int = 1024, source_id: str = "nextcloud-main") -> BigDataLedgerRecord:
    return BigDataLedgerRecord.create(
        _item(path, size=size, source_id=source_id),
        stage="inventory",
        status="completed",
        metadata={"scanner": "test"},
    )


def test_builds_review_gated_archive_plan_for_toolchain_bundle():
    records = [
        _inventory("Daten/Referendariat/NWT/MSP430/msp430G2/energia/hardware/tools/msp430/bin/gcc.exe"),
        _inventory("Daten/Referendariat/NWT/MSP430/msp430G2/energia/hardware/tools/msp430/bin/ld.exe"),
        _inventory("Daten/Referendariat/NWT/MSP430/msp430G2/energia/hardware/tools/msp430/bin/runtime.dll"),
        _inventory("Daten/Referendariat/NWT/MSP430/msp430G2/energia/readme.txt"),
        _inventory("Daten/Referendariat/NWT/MSP430/msp430G2/energia/examples/blink.ino"),
        _inventory("Daten/Referendariat/NWT/notes/lesson.md"),
    ]

    plans = build_nextcloud_software_archive_plans(
        records,
        source_id="nextcloud-main",
        min_executable_files=2,
        min_executable_ratio=0.2,
    )

    assert len(plans) == 1
    plan = plans[0]
    payload = plan.to_dict()
    assert plan.dry_run is True
    assert plan.writes_performed is False
    assert plan.delete_original is False
    assert plan.execution_allowed is False
    assert "create_zip" in plan.actions
    assert "write_sidecar" in plan.actions
    assert payload["profile"]["bundle_kind"] == "toolchain_bundle"
    assert payload["profile"]["executable_count"] == 3
    assert payload["profile"]["review_required"] is True
    assert "binary_files_excluded_from_memory" in payload["profile"]["reason_codes"]
    assert plan.archive_path.startswith("Software Archives/")
    assert plan.sidecar_path.endswith(".zip.odysseus.json")
    assert "lesson.md" not in json.dumps(payload, sort_keys=True)


def test_detects_node_modules_as_single_archive_candidate():
    records = [
        _inventory("Python/game/node_modules/@esbuild/win32-x64/esbuild.exe", size=9_000_000),
        _inventory("Python/game/node_modules/@esbuild/win32-x64/package.json"),
        _inventory("Python/game/package.json"),
        _inventory("Python/game/src/main.js"),
    ]

    plans = build_nextcloud_software_archive_plans(
        records,
        source_id="nextcloud-main",
        min_executable_files=1,
    )

    assert len(plans) == 1
    assert plans[0].profile.bundle_kind == "node_dependency_bundle"
    assert plans[0].profile.folder_path == "Python/game/node_modules"
    assert plans[0].profile.executable_suffix_counts == {".exe": 1}


def test_appends_metadata_only_ledger_records_and_resumes(tmp_path):
    ledger_path = tmp_path / "events.jsonl"
    ledger = AppendOnlyBigDataLedger(ledger_path)
    for record in [
        _inventory("Software/Tool/bin/app.exe", size=5),
        _inventory("Software/Tool/bin/helper.dll", size=7),
        _inventory("Software/Tool/docs/manual.pdf", size=11),
    ]:
        ledger.append_record(record)

    first = plan_nextcloud_software_archive_metadata(
        ledger_path=str(ledger_path),
        source_id="nextcloud-main",
        min_executable_files=2,
    )
    second = plan_nextcloud_software_archive_metadata(
        ledger_path=str(ledger_path),
        source_id="nextcloud-main",
        min_executable_files=2,
    )
    latest = AppendOnlyBigDataLedger(ledger_path).latest_state()
    analysis_records = [
        record
        for record in latest.values()
        if record.stage == "analysis"
        and record.metadata.get("planner") == "nextcloud_software_archive"
    ]
    encoded = ledger_path.read_text(encoding="utf-8")

    assert first.planned == 1
    assert first.plans[0].profile.file_count == 3
    assert second.planned == 0
    assert second.skipped_existing == 1
    assert len(analysis_records) == 1
    assert analysis_records[0].status == "needs_review"
    assert analysis_records[0].metadata["delete_original"] is False
    assert analysis_records[0].metadata["overwrite_existing"] is False
    assert "raw private body" not in encoded
    assert "create_zip" in encoded


def test_ignores_low_density_document_folder_with_single_binary():
    records = [
        _inventory("Docs/manual.pdf"),
        _inventory("Docs/notes.md"),
        _inventory("Docs/images/photo.jpg"),
        _inventory("Docs/setup.exe"),
    ]

    plans = build_nextcloud_software_archive_plans(
        records,
        source_id="nextcloud-main",
        min_executable_files=2,
        min_executable_ratio=0.5,
    )

    assert plans == ()
