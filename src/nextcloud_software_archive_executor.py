"""Review-gated ZIP execution for Nextcloud software archive plans.

This module turns a metadata-only software archive plan into a local ZIP,
sidecar JSON and optional ZIP manifest. It never deletes originals and only
writes after both review approval and explicit operator live-go are present.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping
import zipfile

from src.nextcloud_software_archives import NextcloudSoftwareArchivePlan


EXECUTION_SCHEMA = "odysseus.nextcloud.software_archive_execution.v1"
MANIFEST_NAME = "ODYSSEUS_MANIFEST.json"


class NextcloudSoftwareArchiveExecutionError(ValueError):
    """Raised when a software archive plan cannot be executed safely."""


@dataclass(frozen=True, slots=True)
class NextcloudSoftwareArchiveExecutionRequest:
    plan: NextcloudSoftwareArchivePlan
    source_root: str | Path
    output_root: str | Path
    review_approved: bool = False
    operator_live_go: bool = False
    dry_run: bool = True
    write_sidecar: bool = True
    write_manifest_inside_zip: bool = True
    overwrite_existing: bool = False


@dataclass(frozen=True, slots=True)
class NextcloudSoftwareArchiveExecutionResult:
    status: str
    reason: str
    archive_path: str
    sidecar_path: str
    dry_run: bool
    writes_performed: bool = False
    source_files_deleted: bool = False
    overwrite_existing: bool = False
    manifest_written: bool = False
    sidecar_written: bool = False
    files_archived: int = 0
    bytes_archived: int = 0
    schema: str = EXECUTION_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "reason": self.reason,
            "archive_path": self.archive_path,
            "sidecar_path": self.sidecar_path,
            "dry_run": self.dry_run,
            "writes_performed": self.writes_performed,
            "source_files_deleted": self.source_files_deleted,
            "overwrite_existing": self.overwrite_existing,
            "manifest_written": self.manifest_written,
            "sidecar_written": self.sidecar_written,
            "files_archived": self.files_archived,
            "bytes_archived": self.bytes_archived,
        }


def execute_nextcloud_software_archive_plan(
    request: NextcloudSoftwareArchiveExecutionRequest,
) -> NextcloudSoftwareArchiveExecutionResult:
    """Execute a review-gated software archive plan or return a safe block."""

    if not isinstance(request, NextcloudSoftwareArchiveExecutionRequest):
        raise TypeError("request must be NextcloudSoftwareArchiveExecutionRequest")

    plan = request.plan
    if not request.review_approved:
        return _blocked(request, "review_approval_missing")
    if not request.dry_run and not request.operator_live_go:
        return _blocked(request, "operator_live_go_missing")
    if request.overwrite_existing:
        return _blocked(request, "overwrite_existing_not_allowed")

    source_root = Path(request.source_root).resolve()
    output_root = Path(request.output_root).resolve()
    source_path = _safe_join(source_root, plan.profile.folder_path)
    archive_path = _safe_join(output_root, plan.archive_path)
    sidecar_path = _safe_join(output_root, plan.sidecar_path)

    if not source_path.exists():
        return _blocked(request, "source_missing")
    if not source_path.is_dir() and not source_path.is_file():
        return _blocked(request, "source_not_file_or_directory")
    if archive_path.exists():
        return _blocked(request, "archive_target_exists")
    if sidecar_path.exists():
        return _blocked(request, "sidecar_target_exists")

    planned_files = _planned_files(source_path, source_root=source_root)
    planned_bytes = sum(path.stat().st_size for path, _arcname in planned_files)
    if request.dry_run:
        return NextcloudSoftwareArchiveExecutionResult(
            status="dry_run",
            reason="review_confirmed_no_writes_performed",
            archive_path=plan.archive_path,
            sidecar_path=plan.sidecar_path,
            dry_run=True,
            files_archived=len(planned_files),
            bytes_archived=planned_bytes,
        )

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if request.write_sidecar:
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)

    manifest_payload = _execution_manifest(plan, planned_files=planned_files, planned_bytes=planned_bytes)
    with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path, arcname in planned_files:
            archive.write(file_path, arcname)
        if request.write_manifest_inside_zip:
            archive.writestr(MANIFEST_NAME, json.dumps(manifest_payload, ensure_ascii=False, indent=2, sort_keys=True))

    sidecar_written = False
    if request.write_sidecar:
        sidecar_path.write_text(
            json.dumps(manifest_payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        sidecar_written = True

    return NextcloudSoftwareArchiveExecutionResult(
        status="completed",
        reason="review_confirmed_and_archive_written",
        archive_path=plan.archive_path,
        sidecar_path=plan.sidecar_path,
        dry_run=False,
        writes_performed=True,
        overwrite_existing=False,
        manifest_written=bool(request.write_manifest_inside_zip),
        sidecar_written=sidecar_written,
        files_archived=len(planned_files),
        bytes_archived=planned_bytes,
    )


def _blocked(
    request: NextcloudSoftwareArchiveExecutionRequest,
    reason: str,
) -> NextcloudSoftwareArchiveExecutionResult:
    return NextcloudSoftwareArchiveExecutionResult(
        status="blocked",
        reason=reason,
        archive_path=request.plan.archive_path,
        sidecar_path=request.plan.sidecar_path,
        dry_run=request.dry_run,
        overwrite_existing=bool(request.overwrite_existing),
    )


def _planned_files(source_path: Path, *, source_root: Path) -> list[tuple[Path, str]]:
    if source_path.is_file():
        return [(source_path, _relative_archive_name(source_path, source_root=source_root))]
    files: list[tuple[Path, str]] = []
    for path in sorted(item for item in source_path.rglob("*") if item.is_file()):
        files.append((path, _relative_archive_name(path, source_root=source_root)))
    return files


def _relative_archive_name(path: Path, *, source_root: Path) -> str:
    relative = path.resolve().relative_to(source_root).as_posix()
    if not relative or relative.startswith("../") or "/../" in relative:
        raise NextcloudSoftwareArchiveExecutionError("archive entry must stay within source root")
    return relative


def _execution_manifest(
    plan: NextcloudSoftwareArchivePlan,
    *,
    planned_files: list[tuple[Path, str]],
    planned_bytes: int,
) -> dict[str, Any]:
    profile = plan.profile.to_dict()
    return {
        "schema": EXECUTION_SCHEMA,
        "created_at": _now_iso(),
        "source_folder": plan.profile.folder_path,
        "archive_path": plan.archive_path,
        "sidecar_path": plan.sidecar_path,
        "file_count": len(planned_files),
        "byte_size": planned_bytes,
        "profile": profile,
        "sample_paths": profile.get("sample_paths", ()),
        "original_files_retained": True,
        "deletion_performed": False,
        "overwrite_existing": False,
        "operator_approval": {
            "review_approved": True,
            "operator_live_go": True,
        },
    }


def _safe_join(root: Path, relative_path: str) -> Path:
    normalized = _normalize_relative_path(relative_path)
    candidate = (root / Path(*normalized.split("/"))).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise NextcloudSoftwareArchiveExecutionError("path escapes runtime root") from exc
    return candidate


def _normalize_relative_path(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        raise NextcloudSoftwareArchiveExecutionError("relative path is required")
    if raw.startswith(("/", "~")) or (len(raw) >= 3 and raw[1:3] == ":/"):
        raise NextcloudSoftwareArchiveExecutionError("relative path must not be absolute")
    parts = tuple(part.strip() for part in raw.split("/") if part.strip() and part.strip() != ".")
    if not parts or any(part == ".." for part in parts):
        raise NextcloudSoftwareArchiveExecutionError("relative path must not contain traversal")
    if any(any(ord(ch) < 32 for ch in part) for part in parts):
        raise NextcloudSoftwareArchiveExecutionError("relative path contains control characters")
    return "/".join(parts)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
