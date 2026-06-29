"""Offline resumable scanner for private-data ingestion dry-runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatch
import hashlib
import os
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

from src.bigdata_ledger_contract import (
    AppendOnlyBigDataLedger,
    BigDataLedgerItem,
    BigDataLedgerRecord,
)
from src.nextcloud_import_config import normalize_nextcloud_import_config
from src.nextcloud_privacy_partition import classify_nextcloud_relative_path


MEDIA_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".svg"})
AUDIO_EXTENSIONS = frozenset({".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma"})
VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".avi", ".mkv", ".webm", ".wmv", ".m4v"})
ARCHIVE_EXTENSIONS = frozenset({".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz"})
OFFICE_PENDING_EXTENSIONS = frozenset({".odt", ".rtf", ".pptx", ".xlsx", ".xls", ".epub"})
LONG_PATH_THRESHOLD = 240


@dataclass(frozen=True, slots=True)
class ScannerDryRunResult:
    scanned: int
    committed: int
    skipped_existing: int
    excluded: int
    failed: int
    interrupted: bool
    ledger_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "committed": self.committed,
            "skipped_existing": self.skipped_existing,
            "excluded": self.excluded,
            "failed": self.failed,
            "interrupted": self.interrupted,
            "ledger_summary": dict(self.ledger_summary),
        }


def run_nextcloud_scanner_dry_run(
    *,
    root: str | Path,
    ledger_path: str | Path,
    source_id: str,
    provider: str = "nextcloud",
    batch_limit: int | None = None,
    sensitive_roots: Iterable[str] = (),
    default_unknown_private: bool = False,
    config: Mapping[str, Any] | None = None,
    scan_profile: str = "full",
) -> ScannerDryRunResult:
    """Scan file metadata into the append-only ledger without reading contents."""

    scan_config = normalize_nextcloud_import_config(config or {})
    active_sensitive_roots = tuple(sensitive_roots) or tuple(scan_config["sensitive_roots"])
    active_default_unknown_private = (
        bool(default_unknown_private)
        if config is None
        else bool(default_unknown_private or scan_config["default_unknown_private"])
    )
    active_scan_profile = str(scan_profile or "full").strip() or "full"

    root_path = Path(root).resolve()
    if not root_path.exists() or not root_path.is_dir():
        raise ValueError("root must be an existing directory")
    if _is_within(Path(ledger_path).resolve(), root_path):
        raise ValueError("ledger_path must not live inside the scanned root")

    ledger = AppendOnlyBigDataLedger(ledger_path)
    existing_inventory = {
        record.item.item_id
        for record in ledger.latest_state().values()
        if record.stage == "inventory" and record.status == "completed"
    }
    scanned = committed = skipped_existing = excluded = failed = 0
    limit = int(batch_limit) if batch_limit is not None else None

    for path in _iter_files(root_path):
        if limit is not None and scanned >= limit:
            break
        scanned += 1
        relative = path.relative_to(root_path).as_posix()
        try:
            stat = path.stat()
            inventory = classify_nextcloud_inventory_path(
                relative,
                size_bytes=stat.st_size,
                config=scan_config,
                scan_profile=active_scan_profile,
            )
            if inventory["exclusion_status"] == "excluded":
                excluded += 1
                continue
            privacy = classify_nextcloud_relative_path(
                relative,
                sensitive_roots=active_sensitive_roots,
                default_unknown_private=active_default_unknown_private,
            )
            item = BigDataLedgerItem(
                provider=provider,
                source_id=source_id,
                relative_path=relative,
                size_bytes=stat.st_size,
                mtime=_mtime_iso(stat.st_mtime),
                content_hash=_metadata_fingerprint(relative, stat.st_size, stat.st_mtime),
            )
            if item.item_id in existing_inventory:
                skipped_existing += 1
                continue
            record = BigDataLedgerRecord.create(
                item,
                stage="inventory",
                status="completed",
                metadata={
                    "scanner": "nextcloud_resumable_scanner",
                    "dry_run": True,
                    "privacy": privacy.to_metadata(),
                    "extension": inventory["extension"],
                    "file_category": inventory["file_category"],
                    "privacy_class": privacy.privacy_class,
                    "exclusion_status": inventory["exclusion_status"],
                    "exclusion_reason": inventory["exclusion_reason"],
                    "long_path": inventory["long_path"],
                    "relative_path_length": inventory["relative_path_length"],
                    "top_level_root": inventory["top_level_root"],
                    "scan_profile": inventory["scan_profile"],
                },
            )
            ledger.append_record(record)
            existing_inventory.add(item.item_id)
            committed += 1
        except OSError as exc:
            failed += 1
            record = BigDataLedgerRecord.create(
                {
                    "provider": provider,
                    "source_id": source_id,
                    "relative_path": relative,
                    "size_bytes": 0,
                    "mtime": _now_iso(),
                },
                stage="inventory",
                status="retryable",
                last_error=str(exc),
                metadata={"scanner": "nextcloud_resumable_scanner", "dry_run": True},
            )
            ledger.append_record(record)

    return ScannerDryRunResult(
        scanned=scanned,
        committed=committed,
        skipped_existing=skipped_existing,
        excluded=excluded,
        failed=failed,
        interrupted=limit is not None and scanned >= limit,
        ledger_summary=ledger.summary(),
    )


def classify_nextcloud_inventory_path(
    relative_path: str,
    *,
    size_bytes: int,
    config: Mapping[str, Any] | None = None,
    scan_profile: str = "full",
) -> dict[str, Any]:
    """Classify a Nextcloud path using metadata only."""

    scan_config = normalize_nextcloud_import_config(config or {})
    relative = str(relative_path or "").replace("\\", "/").strip("/")
    name = PurePosixPath(relative).name
    extension = PurePosixPath(relative).suffix.casefold()
    excluded, reason = _exclusion_reason(relative, name, int(size_bytes), scan_config)
    return {
        "extension": extension,
        "file_category": "excluded" if excluded else _file_category(extension, int(size_bytes), scan_config),
        "exclusion_status": "excluded" if excluded else "included",
        "exclusion_reason": reason,
        "long_path": len(relative) > LONG_PATH_THRESHOLD,
        "relative_path_length": len(relative),
        "top_level_root": relative.split("/", 1)[0] if relative else "",
        "scan_profile": str(scan_profile or "full").strip() or "full",
    }


def _iter_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if not _unsafe_segment(name))
        for filename in sorted(filenames):
            if _unsafe_segment(filename):
                continue
            yield Path(dirpath) / filename


def _unsafe_segment(value: str) -> bool:
    return value in {"", ".", ".."} or any(ord(ch) < 32 for ch in value)


def _exclusion_reason(relative_path: str, filename: str, size_bytes: int, config: Mapping[str, Any]) -> tuple[bool, str]:
    if filename in set(config.get("exclude_names", ())):
        return True, "excluded_name"
    for pattern in config.get("exclude_globs", ()):
        if fnmatch(filename, pattern) or fnmatch(relative_path, pattern):
            return True, "excluded_glob"
    if size_bytes == 0 and not bool(config.get("include_zero_byte", False)):
        return True, "zero_byte"
    return False, ""


def _file_category(extension: str, size_bytes: int, config: Mapping[str, Any]) -> str:
    if size_bytes == 0:
        return "empty"
    if extension in set(config.get("binary_extensions", ())):
        return "dangerous_or_binary"
    if extension in {".pdf", ".docx"}:
        return "document_extractable"
    if extension in set(config.get("document_extensions_initial", ())):
        return "text_extractable"
    if extension in OFFICE_PENDING_EXTENSIONS:
        return "office_pending"
    if extension in MEDIA_EXTENSIONS:
        return "media_metadata"
    if extension in AUDIO_EXTENSIONS:
        return "audio_transcribable_review"
    if extension in VIDEO_EXTENSIONS:
        return "video_metadata"
    if extension in ARCHIVE_EXTENSIONS:
        return "archive_review"
    if not extension:
        return "unsupported"
    return "unsupported"


def _metadata_fingerprint(relative_path: str, size: int, mtime: float) -> str:
    payload = f"{relative_path}\0{size}\0{int(mtime)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _mtime_iso(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False
