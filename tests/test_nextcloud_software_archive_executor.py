import json
import zipfile

from src.bigdata_ledger_contract import BigDataLedgerItem, BigDataLedgerRecord
from src.nextcloud_software_archive_executor import (
    MANIFEST_NAME,
    NextcloudSoftwareArchiveExecutionRequest,
    execute_nextcloud_software_archive_plan,
)
from src.nextcloud_software_archives import build_nextcloud_software_archive_plans


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


def _plan():
    plans = build_nextcloud_software_archive_plans(
        [
            _inventory("Software/Tool/bin/app.exe", size=7),
            _inventory("Software/Tool/bin/helper.dll", size=11),
            _inventory("Software/Tool/readme.txt", size=13),
        ],
        source_id="nextcloud-main",
        min_executable_files=2,
    )
    assert len(plans) == 1
    return plans[0]


def _source_tree(tmp_path):
    root = tmp_path / "source"
    (root / "Software" / "Tool" / "bin").mkdir(parents=True)
    (root / "Software" / "Tool" / "bin" / "app.exe").write_text("private executable placeholder", encoding="utf-8")
    (root / "Software" / "Tool" / "bin" / "helper.dll").write_text("private dll placeholder", encoding="utf-8")
    (root / "Software" / "Tool" / "readme.txt").write_text("private readme body", encoding="utf-8")
    return root


def test_archive_executor_requires_review_before_any_write(tmp_path):
    plan = _plan()
    source_root = _source_tree(tmp_path)
    output_root = tmp_path / "out"

    result = execute_nextcloud_software_archive_plan(
        NextcloudSoftwareArchiveExecutionRequest(
            plan=plan,
            source_root=source_root,
            output_root=output_root,
            review_approved=False,
            operator_live_go=True,
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.reason == "review_approval_missing"
    assert result.writes_performed is False
    assert not output_root.exists()


def test_archive_executor_blocks_live_without_operator_go(tmp_path):
    plan = _plan()
    source_root = _source_tree(tmp_path)
    output_root = tmp_path / "out"

    result = execute_nextcloud_software_archive_plan(
        NextcloudSoftwareArchiveExecutionRequest(
            plan=plan,
            source_root=source_root,
            output_root=output_root,
            review_approved=True,
            operator_live_go=False,
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.reason == "operator_live_go_missing"
    assert result.writes_performed is False
    assert not output_root.exists()


def test_archive_executor_dry_run_counts_files_without_writes(tmp_path):
    plan = _plan()
    source_root = _source_tree(tmp_path)
    output_root = tmp_path / "out"

    result = execute_nextcloud_software_archive_plan(
        NextcloudSoftwareArchiveExecutionRequest(
            plan=plan,
            source_root=source_root,
            output_root=output_root,
            review_approved=True,
            operator_live_go=False,
            dry_run=True,
        )
    )

    assert result.status == "dry_run"
    assert result.reason == "review_confirmed_no_writes_performed"
    assert result.files_archived == 3
    assert result.writes_performed is False
    assert not output_root.exists()


def test_archive_executor_writes_zip_sidecar_and_manifest_after_go(tmp_path):
    plan = _plan()
    source_root = _source_tree(tmp_path)
    output_root = tmp_path / "out"

    result = execute_nextcloud_software_archive_plan(
        NextcloudSoftwareArchiveExecutionRequest(
            plan=plan,
            source_root=source_root,
            output_root=output_root,
            review_approved=True,
            operator_live_go=True,
            dry_run=False,
        )
    )
    archive_path = output_root / plan.archive_path
    sidecar_path = output_root / plan.sidecar_path
    sidecar_payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    encoded_sidecar = json.dumps(sidecar_payload, sort_keys=True)

    assert result.status == "completed"
    assert result.writes_performed is True
    assert result.source_files_deleted is False
    assert archive_path.exists()
    assert sidecar_path.exists()
    assert (source_root / "Software" / "Tool" / "readme.txt").exists()
    assert "private readme body" not in encoded_sidecar
    assert str(source_root) not in encoded_sidecar
    assert sidecar_payload["source_folder"] == plan.profile.folder_path
    assert sidecar_payload["deletion_performed"] is False
    assert sidecar_payload["overwrite_existing"] is False
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
        manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
    assert "Software/Tool/readme.txt" in names
    assert MANIFEST_NAME in names
    assert manifest["source_folder"] == plan.profile.folder_path
    assert "private readme body" not in json.dumps(manifest, sort_keys=True)


def test_archive_executor_blocks_existing_target_without_overwrite(tmp_path):
    plan = _plan()
    source_root = _source_tree(tmp_path)
    output_root = tmp_path / "out"
    archive_path = output_root / plan.archive_path
    archive_path.parent.mkdir(parents=True)
    archive_path.write_text("existing archive placeholder", encoding="utf-8")

    result = execute_nextcloud_software_archive_plan(
        NextcloudSoftwareArchiveExecutionRequest(
            plan=plan,
            source_root=source_root,
            output_root=output_root,
            review_approved=True,
            operator_live_go=True,
            dry_run=False,
        )
    )

    assert result.status == "blocked"
    assert result.reason == "archive_target_exists"
    assert archive_path.read_text(encoding="utf-8") == "existing archive placeholder"
