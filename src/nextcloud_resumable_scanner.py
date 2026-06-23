"""Offline resumable scanner for private-data ingestion dry-runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
from typing import Any

from src.bigdata_ledger_contract import (
    AppendOnlyBigDataLedger,
    BigDataLedgerItem,
    BigDataLedgerRecord,
)


@dataclass(frozen=True, slots=True)
class ScannerDryRunResult:
    scanned: int
    committed: int
    skipped_existing: int
    failed: int
    interrupted: bool
    ledger_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "committed": self.committed,
            "skipped_existing": self.skipped_existing,
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
) -> ScannerDryRunResult:
    """Scan file metadata into the append-only ledger without reading contents."""

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
    scanned = committed = skipped_existing = failed = 0
    limit = int(batch_limit) if batch_limit is not None else None

    for path in _iter_files(root_path):
        if limit is not None and scanned >= limit:
            break
        scanned += 1
        relative = path.relative_to(root_path).as_posix()
        try:
            stat = path.stat()
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
                metadata={"scanner": "nextcloud_resumable_scanner", "dry_run": True},
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
        failed=failed,
        interrupted=limit is not None and scanned >= limit,
        ledger_summary=ledger.summary(),
    )


def _iter_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if not _unsafe_segment(name))
        for filename in sorted(filenames):
            if _unsafe_segment(filename):
                continue
            yield Path(dirpath) / filename


def _unsafe_segment(value: str) -> bool:
    return value in {"", ".", ".."} or any(ord(ch) < 32 for ch in value)


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
