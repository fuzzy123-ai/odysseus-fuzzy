"""Offline chunked extraction lane for private Nextcloud ingestion.

The lane accepts runtime-only text that was already extracted by a caller and
persists only chunk references, offsets, hashes, and retry state in the big-data
ledger. It never reads Nextcloud, opens provider connections, or stores raw
document text.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from src.bigdata_ledger_contract import (
    AppendOnlyBigDataLedger,
    BigDataLedgerItem,
    BigDataLedgerRecord,
)
from src.nextcloud_ingestion_integration import classify_nextcloud_ingestion_path
from src.token_budget import count_text_tokens


_SAFE_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,79}$")
CHUNK_REF_SPLITTER_VERSION = "nextcloud_chunk_refs_v2"


@dataclass(frozen=True, slots=True)
class ExtractionChunkRef:
    chunk_index: int
    start: int
    end: int
    chars: int
    budget_start_est: int
    budget_end_est: int
    digest: str
    splitter_version: str = CHUNK_REF_SPLITTER_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_index": self.chunk_index,
            "start": self.start,
            "end": self.end,
            "chars": self.chars,
            "budget_start_est": self.budget_start_est,
            "budget_end_est": self.budget_end_est,
            "digest": self.digest,
            "splitter_version": self.splitter_version,
        }


@dataclass(frozen=True, slots=True)
class RuntimeExtractionDocument:
    item: BigDataLedgerItem
    runtime_text: str = ""
    extractor: str = "universal_inbox"
    error: str = ""
    warning_codes: tuple[str, ...] = ()
    privacy_metadata: Mapping[str, Any] | None = None

    @classmethod
    def create(
        cls,
        item: BigDataLedgerItem | Mapping[str, Any],
        *,
        runtime_text: str = "",
        extractor: str = "universal_inbox",
        error: str = "",
        warning_codes: Iterable[str] = (),
        privacy_metadata: Mapping[str, Any] | None = None,
    ) -> "RuntimeExtractionDocument":
        parsed_item = item if isinstance(item, BigDataLedgerItem) else BigDataLedgerItem.from_mapping(item)
        return cls(
            item=parsed_item,
            runtime_text=str(runtime_text or ""),
            extractor=_normalize_label(extractor, field="extractor"),
            error=str(error or ""),
            warning_codes=tuple(_normalize_label(code, field="warning_code") for code in warning_codes),
            privacy_metadata=dict(privacy_metadata or {}),
        )


@dataclass(frozen=True, slots=True)
class ChunkedExtractionLaneResult:
    completed: int
    retryable: int
    needs_review: int
    skipped_existing: int
    skipped_missing_transfer: int
    interrupted: bool
    ledger_summary: dict[str, Any]

    @property
    def planned(self) -> int:
        return self.completed + self.retryable + self.needs_review

    def to_dict(self) -> dict[str, Any]:
        return {
            "planned": self.planned,
            "completed": self.completed,
            "retryable": self.retryable,
            "needs_review": self.needs_review,
            "skipped_existing": self.skipped_existing,
            "skipped_missing_transfer": self.skipped_missing_transfer,
            "interrupted": self.interrupted,
            "ledger_summary": dict(self.ledger_summary),
        }


def build_extraction_chunk_refs(
    runtime_text: str,
    *,
    max_chunk_chars: int = 4000,
) -> tuple[ExtractionChunkRef, ...]:
    """Build deterministic chunk references without returning chunk bodies."""

    text = str(runtime_text or "")
    if max_chunk_chars <= 0:
        raise ValueError("max_chunk_chars must be positive")
    if not text:
        return ()

    refs: list[ExtractionChunkRef] = []
    for start in range(0, len(text), max_chunk_chars):
        end = min(start + max_chunk_chars, len(text))
        chunk = text[start:end]
        budget_start = count_text_tokens(text[:start])
        refs.append(
            ExtractionChunkRef(
                chunk_index=len(refs),
                start=start,
                end=end,
                chars=len(chunk),
                budget_start_est=budget_start,
                budget_end_est=budget_start + count_text_tokens(chunk),
                digest=hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
            )
        )
    return tuple(refs)


def plan_nextcloud_chunked_extraction(
    *,
    ledger_path: str | Path,
    source_id: str,
    documents: Iterable[RuntimeExtractionDocument | Mapping[str, Any]],
    batch_limit: int | None = None,
    max_chunk_chars: int = 4000,
    max_chunks_per_item: int = 256,
    require_transfer_state: bool = True,
    sensitive_roots: Iterable[str] = (),
    default_unknown_private: bool = False,
) -> ChunkedExtractionLaneResult:
    """Persist offline extraction progress and retry states in the ledger."""

    source = str(source_id or "").strip()
    if not source:
        raise ValueError("source_id must not be empty")
    if batch_limit is not None and int(batch_limit) < 0:
        raise ValueError("batch_limit must be non-negative")
    if max_chunk_chars <= 0:
        raise ValueError("max_chunk_chars must be positive")
    if max_chunks_per_item <= 0:
        raise ValueError("max_chunks_per_item must be positive")

    normalized_documents = tuple(_normalize_document(document) for document in documents)
    selected_documents = tuple(document for document in normalized_documents if document.item.source_id == source)
    limit = int(batch_limit) if batch_limit is not None else None

    ledger = AppendOnlyBigDataLedger(ledger_path)
    completed = retryable = needs_review = skipped_existing = skipped_missing_transfer = processed = 0

    for document in sorted(selected_documents, key=lambda item: item.item.relative_path):
        if limit is not None and processed >= limit:
            break
        processed += 1

        latest = ledger.latest_state()
        existing_extraction = latest.get((document.item.item_id, "extraction"))
        if existing_extraction is not None and existing_extraction.status == "completed":
            skipped_existing += 1
            continue

        transfer_status = _transfer_status(latest, document.item)
        if require_transfer_state and transfer_status not in {"pending", "completed"}:
            skipped_missing_transfer += 1
            continue

        privacy_metadata = _privacy_metadata_for_document(
            document,
            latest,
            sensitive_roots=sensitive_roots,
            default_unknown_private=default_unknown_private,
        )
        prior_attempts = existing_extraction.attempt_count if existing_extraction is not None else 0
        record = _build_record(
            document,
            transfer_status=transfer_status,
            prior_attempts=prior_attempts,
            max_chunk_chars=max_chunk_chars,
            max_chunks_per_item=max_chunks_per_item,
            privacy_metadata=privacy_metadata,
        )
        ledger.append_record(record)
        if record.status == "completed":
            completed += 1
        elif record.status == "retryable":
            retryable += 1
        elif record.status == "needs_review":
            needs_review += 1

    return ChunkedExtractionLaneResult(
        completed=completed,
        retryable=retryable,
        needs_review=needs_review,
        skipped_existing=skipped_existing,
        skipped_missing_transfer=skipped_missing_transfer,
        interrupted=limit is not None and processed >= limit and processed < len(selected_documents),
        ledger_summary=ledger.summary(),
    )


def _build_record(
    document: RuntimeExtractionDocument,
    *,
    transfer_status: str,
    prior_attempts: int,
    max_chunk_chars: int,
    max_chunks_per_item: int,
    privacy_metadata: Mapping[str, Any],
) -> BigDataLedgerRecord:
    base_metadata: dict[str, Any] = {
        "lane": "nextcloud_chunked_extraction",
        "extractor": document.extractor,
        "runtime_only": True,
        "transfer_status": transfer_status or "unknown",
        "max_chunk_chars": max_chunk_chars,
        "warning_codes": tuple(document.warning_codes),
        **dict(privacy_metadata or {}),
    }
    privacy = base_metadata.get("privacy") if isinstance(base_metadata.get("privacy"), Mapping) else {}
    if privacy and not bool(privacy.get("memory_write_candidate", True)):
        return BigDataLedgerRecord.create(
            document.item,
            stage="extraction",
            status="needs_review",
            attempt_count=prior_attempts,
            metadata={**base_metadata, "reason_code": "privacy_review_required"},
        )

    if document.error:
        return BigDataLedgerRecord.create(
            document.item,
            stage="extraction",
            status="retryable",
            attempt_count=prior_attempts + 1,
            last_error=document.error,
            metadata={**base_metadata, "reason_code": "extractor_error"},
        )

    chunk_refs = build_extraction_chunk_refs(document.runtime_text, max_chunk_chars=max_chunk_chars)
    if not chunk_refs:
        return BigDataLedgerRecord.create(
            document.item,
            stage="extraction",
            status="needs_review",
            attempt_count=prior_attempts,
            metadata={**base_metadata, "reason_code": "empty_runtime_input", "chunk_count": 0, "total_chars": 0},
        )

    truncated = len(chunk_refs) > max_chunks_per_item
    persisted_refs = chunk_refs[:max_chunks_per_item]
    return BigDataLedgerRecord.create(
        document.item,
        stage="extraction",
        status="needs_review" if truncated else "completed",
        attempt_count=prior_attempts,
        metadata={
            **base_metadata,
            "reason_code": "chunk_limit_exceeded" if truncated else "chunk_refs_ready",
            "chunk_count": len(chunk_refs),
            "persisted_ref_count": len(persisted_refs),
            "total_chars": len(document.runtime_text),
            "total_budget_units_est": count_text_tokens(document.runtime_text),
            "source_hash": "sha256:" + hashlib.sha256(document.runtime_text.encode("utf-8")).hexdigest(),
            "splitter_version": CHUNK_REF_SPLITTER_VERSION,
            "truncated": truncated,
            "chunk_refs": tuple(ref.to_dict() for ref in persisted_refs),
        },
    )


def _transfer_status(latest: Mapping[tuple[str, str], BigDataLedgerRecord], item: BigDataLedgerItem) -> str:
    record = latest.get((item.item_id, "transfer"))
    return record.status if record is not None else ""


def _privacy_metadata_for_document(
    document: RuntimeExtractionDocument,
    latest: Mapping[tuple[str, str], BigDataLedgerRecord],
    *,
    sensitive_roots: Iterable[str],
    default_unknown_private: bool,
) -> dict[str, Any]:
    if isinstance(document.privacy_metadata, Mapping) and document.privacy_metadata:
        return dict(document.privacy_metadata)

    inventory = latest.get((document.item.item_id, "inventory"))
    if inventory is not None:
        privacy = inventory.metadata.get("privacy")
        if isinstance(privacy, Mapping) and privacy:
            return {
                "privacy": dict(privacy),
                "classification": _classification_for_privacy(privacy),
                "local_model_only": bool(privacy.get("local_model_only")),
                "required_model_scope": privacy.get("required_model_scope") or "",
                "memory_write_candidate": bool(privacy.get("memory_write_candidate", True)),
                "rag_index_candidate": bool(privacy.get("memory_write_candidate", True)),
                "privacy_policy": "nextcloud_privacy_partition:v1",
            }

    return classify_nextcloud_ingestion_path(
        document.item.relative_path,
        sensitive_roots=sensitive_roots,
        default_unknown_private=default_unknown_private,
    ).for_extraction_metadata()


def _classification_for_privacy(privacy: Mapping[str, Any]) -> str:
    if bool(privacy.get("local_model_only")):
        return "sensitive"
    privacy_class = str(privacy.get("privacy_class") or "").strip().lower()
    return "sensitive" if privacy_class in {"local_sensitive", "unknown_private"} else "private"


def _normalize_document(document: RuntimeExtractionDocument | Mapping[str, Any]) -> RuntimeExtractionDocument:
    if isinstance(document, RuntimeExtractionDocument):
        return document
    item = document.get("item", document)
    return RuntimeExtractionDocument.create(
        item,
        runtime_text=str(document.get("runtime_text") or ""),
        extractor=str(document.get("extractor") or "universal_inbox"),
        error=str(document.get("error") or ""),
        warning_codes=document.get("warning_codes") or (),
        privacy_metadata=document.get("privacy_metadata") or {},
    )


def _normalize_label(value: Any, *, field: str) -> str:
    label = str(value or "").strip().lower()
    if not _SAFE_LABEL_RE.fullmatch(label):
        raise ValueError(f"{field} must be a safe label")
    return label
