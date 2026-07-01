"""Metadata-only pilot batch planning for Nextcloud document imports.

The planner selects a bounded set of safe document inventory records and stores
one reviewable analysis record. It does not read document contents, call
providers, mutate Nextcloud, or write memory/RAG entries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.bigdata_ledger_contract import (
    AppendOnlyBigDataLedger,
    BigDataLedgerItem,
    BigDataLedgerRecord,
)
from src.nextcloud_import_report import DOCUMENT_CATEGORIES, PRIVATE_PRIVACY_CLASSES


PILOT_PLAN_SCHEMA = "odysseus.nextcloud.document_pilot_plan.v1"
PILOT_PLANNER = "nextcloud_document_pilot_import"
DEFAULT_PILOT_ACTIONS = (
    "copy_to_staging",
    "extract_runtime_only",
    "persist_chunk_refs",
    "review_memory_write_intent",
)


@dataclass(frozen=True, slots=True)
class NextcloudDocumentPilotItem:
    relative_path: str
    item_id: str
    file_category: str
    extension: str
    privacy_class: str
    local_model_only: bool
    required_model_scope: str
    rag_index_candidate: bool
    memory_write_candidate: bool
    review_required: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "item_id": self.item_id,
            "file_category": self.file_category,
            "extension": self.extension,
            "privacy_class": self.privacy_class,
            "local_model_only": self.local_model_only,
            "required_model_scope": self.required_model_scope,
            "rag_index_candidate": self.rag_index_candidate,
            "memory_write_candidate": self.memory_write_candidate,
            "review_required": self.review_required,
            "reason_codes": self.reason_codes,
        }


@dataclass(frozen=True, slots=True)
class NextcloudDocumentPilotPlan:
    pilot_id: str
    source_id: str
    selected_items: tuple[NextcloudDocumentPilotItem, ...]
    candidate_count: int
    skipped_private: int
    skipped_non_document: int
    skipped_existing: int
    interrupted: bool
    actions: tuple[str, ...] = DEFAULT_PILOT_ACTIONS
    dry_run: bool = True
    writes_performed: bool = False
    schema: str = PILOT_PLAN_SCHEMA

    @property
    def selected_count(self) -> int:
        return len(self.selected_items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "pilot_id": self.pilot_id,
            "source_id": self.source_id,
            "selected_count": self.selected_count,
            "candidate_count": self.candidate_count,
            "skipped_private": self.skipped_private,
            "skipped_non_document": self.skipped_non_document,
            "skipped_existing": self.skipped_existing,
            "interrupted": self.interrupted,
            "actions": self.actions,
            "dry_run": self.dry_run,
            "writes_performed": self.writes_performed,
            "selected_items": tuple(item.to_dict() for item in self.selected_items),
            "private_content_visible": False,
            "secret_values_visible": False,
        }


@dataclass(frozen=True, slots=True)
class NextcloudDocumentPilotPlanningResult:
    plan: NextcloudDocumentPilotPlan
    appended: bool
    ledger_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "appended": self.appended,
            "ledger_summary": dict(self.ledger_summary),
        }


def build_nextcloud_document_pilot_plan(
    records: Iterable[BigDataLedgerRecord | Mapping[str, Any]],
    *,
    source_id: str,
    pilot_id: str = "pilot-documents",
    batch_limit: int = 100,
    include_private: bool = False,
    existing_item_ids: Iterable[str] = (),
) -> NextcloudDocumentPilotPlan:
    """Build a bounded document pilot plan from inventory metadata only."""

    source = _safe_label(source_id, field="source_id")
    pilot = _safe_label(pilot_id, field="pilot_id")
    limit = _positive_int(batch_limit, field="batch_limit")
    existing = frozenset(str(value or "").strip() for value in existing_item_ids if str(value or "").strip())
    inventory = tuple(_inventory_records(records, source_id=source))

    selected: list[NextcloudDocumentPilotItem] = []
    skipped_private = skipped_non_document = skipped_existing = 0
    candidate_count = 0
    for record in sorted(inventory, key=lambda item: item.item.relative_path):
        category = str(record.metadata.get("file_category") or "unknown")
        privacy = _privacy_payload(record)
        privacy_class = str(privacy.get("privacy_class") or record.metadata.get("privacy_class") or "unknown")
        if category not in DOCUMENT_CATEGORIES:
            skipped_non_document += 1
            continue
        if privacy_class in PRIVATE_PRIVACY_CLASSES and not include_private:
            skipped_private += 1
            continue
        candidate_count += 1
        if record.item.item_id in existing:
            skipped_existing += 1
            continue
        if len(selected) >= limit:
            continue
        selected.append(_pilot_item(record, privacy=privacy, privacy_class=privacy_class))

    return NextcloudDocumentPilotPlan(
        pilot_id=pilot,
        source_id=source,
        selected_items=tuple(selected),
        candidate_count=candidate_count,
        skipped_private=skipped_private,
        skipped_non_document=skipped_non_document,
        skipped_existing=skipped_existing,
        interrupted=(candidate_count - skipped_existing) > len(selected),
    )


def append_nextcloud_document_pilot_plan(
    *,
    ledger_path: str | Path,
    source_id: str,
    pilot_id: str = "pilot-documents",
    batch_limit: int = 100,
    include_private: bool = False,
) -> NextcloudDocumentPilotPlanningResult:
    """Append one metadata-only pilot plan record to the ledger."""

    ledger = AppendOnlyBigDataLedger(ledger_path)
    latest = ledger.latest_state()
    existing_item_ids = _existing_pilot_item_ids(ledger.events, source_id=source_id, pilot_id=pilot_id)
    plan = build_nextcloud_document_pilot_plan(
        latest.values(),
        source_id=source_id,
        pilot_id=pilot_id,
        batch_limit=batch_limit,
        include_private=include_private,
        existing_item_ids=existing_item_ids,
    )
    if plan.selected_count <= 0:
        return NextcloudDocumentPilotPlanningResult(plan=plan, appended=False, ledger_summary=ledger.summary())

    record = BigDataLedgerRecord.create(
        BigDataLedgerItem(
            provider="nextcloud",
            source_id=plan.source_id,
            relative_path=f"Pilot Plans/{plan.pilot_id}.json",
            size_bytes=0,
            mtime=_now_iso(),
            content_hash="sha256:" + _plan_digest(plan),
        ),
        stage="analysis",
        status="needs_review",
        metadata={
            "planner": PILOT_PLANNER,
            "dry_run": True,
            "review_required": True,
            "pilot_id": plan.pilot_id,
            "selected_count": plan.selected_count,
            "candidate_count": plan.candidate_count,
            "skipped_private": plan.skipped_private,
            "skipped_non_document": plan.skipped_non_document,
            "skipped_existing": plan.skipped_existing,
            "interrupted": plan.interrupted,
            "actions": plan.actions,
            "selected_items": tuple(item.to_dict() for item in plan.selected_items),
        },
    )
    ledger.append_record(record)
    return NextcloudDocumentPilotPlanningResult(plan=plan, appended=True, ledger_summary=ledger.summary())


def _inventory_records(
    records: Iterable[BigDataLedgerRecord | Mapping[str, Any]],
    *,
    source_id: str,
) -> Iterable[BigDataLedgerRecord]:
    for record in records:
        parsed = record if isinstance(record, BigDataLedgerRecord) else BigDataLedgerRecord.from_mapping(record)
        if parsed.stage != "inventory" or parsed.status != "completed":
            continue
        if parsed.item.source_id != source_id:
            continue
        yield parsed


def _pilot_item(
    record: BigDataLedgerRecord,
    *,
    privacy: Mapping[str, Any],
    privacy_class: str,
) -> NextcloudDocumentPilotItem:
    review_required = bool(
        privacy_class in PRIVATE_PRIVACY_CLASSES
        or record.metadata.get("extraction_status") in {"partial", "failed", "metadata_only"}
        or record.metadata.get("partial") is True
    )
    reasons = ["document_pilot_candidate"]
    if review_required:
        reasons.append("review_required_before_memory_write")
    return NextcloudDocumentPilotItem(
        relative_path=record.item.relative_path,
        item_id=record.item.item_id,
        file_category=str(record.metadata.get("file_category") or "unknown"),
        extension=str(record.metadata.get("extension") or ""),
        privacy_class=privacy_class,
        local_model_only=bool(privacy.get("local_model_only", privacy_class in PRIVATE_PRIVACY_CLASSES)),
        required_model_scope=str(privacy.get("required_model_scope") or "policy_selected"),
        rag_index_candidate=bool(privacy.get("memory_write_candidate", True)),
        memory_write_candidate=bool(privacy.get("memory_write_candidate", True)),
        review_required=review_required,
        reason_codes=tuple(reasons),
    )


def _privacy_payload(record: BigDataLedgerRecord) -> Mapping[str, Any]:
    privacy = record.metadata.get("privacy")
    if isinstance(privacy, Mapping):
        return privacy
    return {}


def _existing_pilot_item_ids(events: Iterable[Any], *, source_id: str, pilot_id: str) -> frozenset[str]:
    source = str(source_id or "").strip()
    pilot = str(pilot_id or "").strip()
    ids: set[str] = set()
    for event in events:
        record = getattr(event, "record", None)
        if record is None or record.stage != "analysis" or record.item.source_id != source:
            continue
        if record.metadata.get("planner") != PILOT_PLANNER or record.metadata.get("pilot_id") != pilot:
            continue
        for item in record.metadata.get("selected_items") or ():
            if isinstance(item, Mapping):
                item_id = str(item.get("item_id") or "").strip()
                if item_id:
                    ids.add(item_id)
    return frozenset(ids)


def _plan_digest(plan: NextcloudDocumentPilotPlan) -> str:
    payload = "|".join(item.item_id for item in plan.selected_items)
    return hashlib.sha256(f"{plan.source_id}|{plan.pilot_id}|{payload}".encode("utf-8")).hexdigest()


def _safe_label(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} must not be empty")
    if any(ord(ch) < 32 for ch in text):
        raise ValueError(f"{field} contains control characters")
    return text


def _positive_int(value: Any, *, field: str) -> int:
    number = int(value)
    if number <= 0:
        raise ValueError(f"{field} must be positive")
    return number


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
