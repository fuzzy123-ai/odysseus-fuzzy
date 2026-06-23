"""Offline resumable transfer planner for private Nextcloud ingestion."""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Any

from src.bigdata_ledger_contract import (
    AppendOnlyBigDataLedger,
    BigDataLedgerRecord,
)
from src.nextcloud_privacy_partition import privacy_metadata_allows_archive


_SAFE_LABEL_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,79}$")


@dataclass(frozen=True, slots=True)
class ResumableTransferPlanResult:
    planned: int
    skipped_existing: int
    unavailable_inventory: int
    interrupted: bool
    ledger_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "planned": self.planned,
            "skipped_existing": self.skipped_existing,
            "unavailable_inventory": self.unavailable_inventory,
            "interrupted": self.interrupted,
            "ledger_summary": dict(self.ledger_summary),
        }


def plan_nextcloud_resumable_transfer(
    *,
    ledger_path: str | Path,
    source_id: str,
    target_label: str,
    batch_limit: int | None = None,
) -> ResumableTransferPlanResult:
    """Append copy-only transfer plan records from completed inventory records.

    This function does not copy files, touch Nextcloud, inspect target paths, or
    execute shell commands. It only advances the metadata ledger from inventory
    into a resumable pending-transfer plan.
    """

    if not _SAFE_LABEL_RE.fullmatch(str(target_label or "").strip().lower()):
        raise ValueError("target_label must be a safe redacted label")
    source = str(source_id or "").strip()
    if not source:
        raise ValueError("source_id must not be empty")

    ledger = AppendOnlyBigDataLedger(ledger_path)
    latest = ledger.latest_state()
    completed_inventory = [
        record for record in latest.values()
        if record.stage == "inventory"
        and record.status == "completed"
        and record.item.source_id == source
        and privacy_metadata_allows_archive(record.metadata.get("privacy") or {})
    ]
    existing_transfer_ids = {
        record.item.item_id for record in latest.values()
        if record.stage == "transfer" and record.item.source_id == source
    }
    limit = int(batch_limit) if batch_limit is not None else None
    planned = skipped = unavailable = scanned = 0

    for inventory in sorted(completed_inventory, key=lambda item: item.item.relative_path):
        if limit is not None and scanned >= limit:
            break
        scanned += 1
        if inventory.item.item_id in existing_transfer_ids:
            skipped += 1
            continue
        if inventory.item.size_bytes < 0:
            unavailable += 1
            continue
        record = BigDataLedgerRecord.create(
            inventory.item,
            stage="transfer",
            status="pending",
            metadata={
                "planner": "nextcloud_resumable_transfer",
                "copy_only": True,
                "target_label": str(target_label).strip().lower(),
                "live_action": False,
            },
        )
        ledger.append_record(record)
        existing_transfer_ids.add(inventory.item.item_id)
        planned += 1

    return ResumableTransferPlanResult(
        planned=planned,
        skipped_existing=skipped,
        unavailable_inventory=unavailable,
        interrupted=limit is not None and scanned >= limit and scanned < len(completed_inventory),
        ledger_summary=ledger.summary(),
    )
