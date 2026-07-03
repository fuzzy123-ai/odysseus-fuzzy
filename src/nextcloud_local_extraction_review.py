"""Local-only Nextcloud extraction review executor.

This module is the narrow bridge after metadata-only pilot planning. It may
read local synced files at runtime when explicitly allowed, but it persists only
redacted extraction/review metadata and never writes Memory or RaptorGraph.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Mapping

from src.bigdata_ledger_contract import (
    AppendOnlyBigDataLedger,
    BigDataLedgerItem,
    BigDataLedgerRecord,
)
from src.nextcloud_document_pilot_import import LOCAL_EXTRACTION_SUPPORTED_EXTENSIONS
from src.nextcloud_import_report import DOCUMENT_CATEGORIES
from src.universal_inbox_analysis import build_universal_inbox_file_analysis_packet
from src.universal_inbox_extraction import (
    UniversalInboxExtractionError,
    extract_universal_inbox_content,
)


LOCAL_EXTRACTION_REVIEW_RUN_SCHEMA = "odysseus.nextcloud.local_only_extraction_review_run.v1"
LOCAL_EXTRACTION_REVIEW_PLANNER = "nextcloud_local_only_extraction_review_executor"


@dataclass(frozen=True, slots=True)
class NextcloudLocalExtractionReviewItem:
    source_ref: str
    suffix: str
    extraction_status: str
    analysis_status: str
    classification: str
    document_type: str
    char_count: int
    line_count: int
    warning_codes: tuple[str, ...]
    memory_write_permitted: bool = False
    raptor_write_permitted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_ref": self.source_ref,
            "suffix": self.suffix,
            "extraction_status": self.extraction_status,
            "analysis_status": self.analysis_status,
            "classification": self.classification,
            "document_type": self.document_type,
            "char_count": self.char_count,
            "line_count": self.line_count,
            "warning_codes": self.warning_codes,
            "memory_write_permitted": False,
            "raptor_write_permitted": False,
        }


@dataclass(frozen=True, slots=True)
class NextcloudLocalExtractionReviewRun:
    source_id: str
    status: str
    scanned_candidates: int
    processed_count: int
    appended_count: int
    skipped_count: int
    failed_count: int
    review_required_count: int
    items: tuple[NextcloudLocalExtractionReviewItem, ...]
    reasons: tuple[str, ...] = ()
    schema: str = LOCAL_EXTRACTION_REVIEW_RUN_SCHEMA
    dry_run: bool = True
    writes_performed: bool = False
    selected_items_redacted: bool = True
    private_path_material_required_at_runtime: bool = True
    raw_content_visible: bool = False
    raw_content_persisted: bool = False
    memory_writes_permitted: bool = False
    raptor_writes_permitted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "source_id": self.source_id,
            "status": self.status,
            "dry_run": self.dry_run,
            "writes_performed": self.writes_performed,
            "selected_items_redacted": True,
            "private_path_material_required_at_runtime": True,
            "raw_content_visible": False,
            "raw_content_persisted": False,
            "memory_writes_permitted": False,
            "raptor_writes_permitted": False,
            "scanned_candidates": self.scanned_candidates,
            "processed_count": self.processed_count,
            "appended_count": self.appended_count,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
            "review_required_count": self.review_required_count,
            "reasons": self.reasons,
            "items": tuple(item.to_dict() for item in self.items),
        }


def run_nextcloud_local_only_extraction_review(
    *,
    root: str | Path,
    ledger_path: str | Path,
    source_id: str,
    batch_limit: int = 25,
    max_extract_bytes: int = 2 * 1024 * 1024,
    operator_local_extraction_go: bool = False,
) -> NextcloudLocalExtractionReviewRun:
    """Extract local-only pilot candidates into redacted review records."""

    if not operator_local_extraction_go:
        return NextcloudLocalExtractionReviewRun(
            source_id=str(source_id or "").strip(),
            status="blocked",
            scanned_candidates=0,
            processed_count=0,
            appended_count=0,
            skipped_count=0,
            failed_count=0,
            review_required_count=0,
            items=(),
            reasons=("operator_local_extraction_go_required",),
        )
    limit = _positive_int(batch_limit, field="batch_limit")
    root_path = Path(root)
    ledger = AppendOnlyBigDataLedger(ledger_path)
    records = tuple(_candidate_records(ledger.latest_state().values(), source_id=source_id))
    items: list[NextcloudLocalExtractionReviewItem] = []
    appended = failed = review_required = 0
    for record in records[:limit]:
        source_ref = _source_ref(record)
        synthetic_path = f"Local Extraction Review/{source_ref}.json"
        try:
            extraction = extract_universal_inbox_content(
                {"relative_path": record.item.relative_path},
                root=root_path,
                relative_path=record.item.relative_path,
                max_extract_bytes=max_extract_bytes,
            )
            warning_codes = tuple(getattr(warning, "code", "warning") for warning in extraction.warnings)
            analysis = build_universal_inbox_file_analysis_packet(
                {
                    "source_channel": "nextcloud_local_sync",
                    "classification": "sensitive",
                    "document_type": "reference",
                    "extraction_status": extraction.status,
                    "extractor": str(extraction.metadata.get("extractor") or ""),
                    "source_labels": ("nextcloud", "local_only"),
                },
                text_sample=extraction.raw_text,
            ).to_dict()
            policy = dict(analysis.get("policy") or {})
            item = NextcloudLocalExtractionReviewItem(
                source_ref=source_ref,
                suffix=_extension(record),
                extraction_status=str(extraction.status or "unknown"),
                analysis_status=str(analysis.get("status") or policy.get("status") or "unknown"),
                classification=str(policy.get("classification") or "unknown"),
                document_type=str(analysis.get("document_type") or "reference"),
                char_count=int(extraction.metadata.get("char_count") or 0),
                line_count=int(extraction.metadata.get("line_count") or 0),
                warning_codes=warning_codes,
            )
            status = "needs_review" if item.analysis_status != "go" or warning_codes else "completed"
            if status == "needs_review":
                review_required += 1
            ledger.append_record(
                BigDataLedgerRecord.create(
                    BigDataLedgerItem(
                        provider="nextcloud",
                        source_id=str(source_id),
                        relative_path=synthetic_path,
                        size_bytes=0,
                        mtime=_now_iso(),
                        content_hash="sha256:" + _review_digest(item),
                    ),
                    stage="extraction",
                    status=status,
                    metadata={
                        "planner": LOCAL_EXTRACTION_REVIEW_PLANNER,
                        "source_ref": source_ref,
                        "suffix": item.suffix,
                        "extraction_status": item.extraction_status,
                        "analysis_status": item.analysis_status,
                        "classification": item.classification,
                        "document_type": item.document_type,
                        "char_count": item.char_count,
                        "line_count": item.line_count,
                        "warning_codes": item.warning_codes,
                        "extraction_runtime_only": True,
                        "derived_material_persisted": False,
                        "memory_writes_permitted": False,
                        "raptor_writes_permitted": False,
                        "selected_item_redacted": True,
                    },
                )
            )
            appended += 1
            items.append(item)
        except (OSError, UniversalInboxExtractionError, ValueError) as exc:
            failed += 1
            ledger.append_record(
                BigDataLedgerRecord.create(
                    BigDataLedgerItem(
                        provider="nextcloud",
                        source_id=str(source_id),
                        relative_path=synthetic_path,
                        size_bytes=0,
                        mtime=_now_iso(),
                        content_hash="sha256:" + _hash_text(f"{source_ref}|failed|{type(exc).__name__}"),
                    ),
                    stage="extraction",
                    status="failed",
                    metadata={
                        "planner": LOCAL_EXTRACTION_REVIEW_PLANNER,
                        "source_ref": source_ref,
                        "suffix": _extension(record),
                        "error_class": type(exc).__name__,
                        "extraction_runtime_only": True,
                        "derived_material_persisted": False,
                        "memory_writes_permitted": False,
                        "raptor_writes_permitted": False,
                        "selected_item_redacted": True,
                    },
                )
            )

    processed = len(items) + failed
    skipped = max(0, len(records) - processed)
    status = "completed" if processed and not failed else ("partial" if processed else "empty")
    return NextcloudLocalExtractionReviewRun(
        source_id=str(source_id),
        status=status,
        scanned_candidates=len(records),
        processed_count=processed,
        appended_count=appended,
        skipped_count=skipped,
        failed_count=failed,
        review_required_count=review_required,
        items=tuple(items),
    )


def _candidate_records(records: tuple[BigDataLedgerRecord, ...], *, source_id: str) -> tuple[BigDataLedgerRecord, ...]:
    selected: list[BigDataLedgerRecord] = []
    for record in records:
        if record.stage != "inventory" or record.status != "completed" or record.item.source_id != source_id:
            continue
        if str(record.metadata.get("file_category") or "") not in DOCUMENT_CATEGORIES:
            continue
        privacy = record.metadata.get("privacy") if isinstance(record.metadata.get("privacy"), Mapping) else {}
        if not bool(privacy.get("local_model_only")):
            continue
        if _extension(record) not in LOCAL_EXTRACTION_SUPPORTED_EXTENSIONS:
            continue
        selected.append(record)
    return tuple(sorted(selected, key=lambda item: item.item.item_id))


def _source_ref(record: BigDataLedgerRecord) -> str:
    return _hash_text(f"{record.item.source_id}|{record.item.item_id}|{record.item.version_digest()}")[-24:]


def _review_digest(item: NextcloudLocalExtractionReviewItem) -> str:
    return _hash_text(
        f"{item.source_ref}|{item.suffix}|{item.extraction_status}|{item.char_count}|{item.line_count}"
    )


def _extension(record: BigDataLedgerRecord) -> str:
    value = str(record.metadata.get("extension") or "").strip().casefold()
    if value and not value.startswith("."):
        value = "." + value
    return value or Path(record.item.relative_path).suffix.casefold() or "(none)"


def _positive_int(value: Any, *, field: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be positive") from None
    if number <= 0:
        raise ValueError(f"{field} must be positive")
    return number


def _hash_text(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", errors="replace")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
