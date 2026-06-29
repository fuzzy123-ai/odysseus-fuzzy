"""Metadata-only dry-run reports for Nextcloud import preparation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.bigdata_ledger_contract import AppendOnlyBigDataLedger, BigDataLedgerRecord
from src.nextcloud_software_archives import build_nextcloud_software_archive_plans


REPORT_SCHEMA = "odysseus.nextcloud_import_dry_run_report.v1"
DOCUMENT_CATEGORIES = frozenset({"text_extractable", "document_extractable"})
REVIEW_CATEGORIES = frozenset(
    {
        "archive_review",
        "audio_transcribable_review",
        "dangerous_or_binary",
        "office_pending",
        "unsupported",
        "video_metadata",
    }
)
METADATA_ONLY_CATEGORIES = frozenset({"media_metadata", "video_metadata", "dangerous_or_binary", "archive_review"})
PRIVATE_PRIVACY_CLASSES = frozenset({"local_sensitive", "unknown_private"})


@dataclass(frozen=True, slots=True)
class NextcloudImportDryRunReport:
    source_id: str
    inventory_total: int
    by_file_category: Mapping[str, int]
    by_privacy_class: Mapping[str, int]
    long_path_count: int
    document_candidates: int
    metadata_only_candidates: int
    review_candidates: int
    software_archive_candidates: int
    software_archive_paths: tuple[str, ...]
    sample_review_paths: tuple[str, ...]
    private_content_visible: bool = False
    secret_values_visible: bool = False
    schema: str = REPORT_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "source_id": self.source_id,
            "inventory_total": self.inventory_total,
            "by_file_category": dict(self.by_file_category),
            "by_privacy_class": dict(self.by_privacy_class),
            "long_path_count": self.long_path_count,
            "document_candidates": self.document_candidates,
            "metadata_only_candidates": self.metadata_only_candidates,
            "review_candidates": self.review_candidates,
            "software_archive_candidates": self.software_archive_candidates,
            "software_archive_paths": self.software_archive_paths,
            "sample_review_paths": self.sample_review_paths,
            "private_content_visible": self.private_content_visible,
            "secret_values_visible": self.secret_values_visible,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Nextcloud Import Dry-run Report",
            "",
            f"- Source: `{self.source_id}`",
            f"- Inventory records: `{self.inventory_total}`",
            f"- Document candidates: `{self.document_candidates}`",
            f"- Metadata-only candidates: `{self.metadata_only_candidates}`",
            f"- Review candidates: `{self.review_candidates}`",
            f"- Long paths: `{self.long_path_count}`",
            f"- Software archive candidates: `{self.software_archive_candidates}`",
            "",
            "Private contents and secret values are intentionally not included.",
        ]
        return "\n".join(lines)


def build_nextcloud_import_dry_run_report(
    *,
    ledger_path: str | Path,
    source_id: str,
    max_samples: int = 10,
    software_archive_target_root: str = "Software Archives",
) -> NextcloudImportDryRunReport:
    """Build a compact report from completed metadata-only inventory records."""

    source = str(source_id or "").strip()
    if not source:
        raise ValueError("source_id must not be empty")
    if max_samples < 0:
        raise ValueError("max_samples must be non-negative")

    ledger = AppendOnlyBigDataLedger(ledger_path)
    inventory = tuple(_inventory_records(ledger.latest_state().values(), source_id=source))
    by_file_category: dict[str, int] = {}
    by_privacy_class: dict[str, int] = {}
    long_path_count = document_candidates = metadata_only_candidates = review_candidates = 0
    sample_review_paths: list[str] = []

    for record in inventory:
        category = str(record.metadata.get("file_category") or "unknown")
        privacy_class = _privacy_class(record)
        by_file_category[category] = by_file_category.get(category, 0) + 1
        by_privacy_class[privacy_class] = by_privacy_class.get(privacy_class, 0) + 1
        if bool(record.metadata.get("long_path")):
            long_path_count += 1
        if category in DOCUMENT_CATEGORIES and privacy_class not in PRIVATE_PRIVACY_CLASSES:
            document_candidates += 1
        if category in METADATA_ONLY_CATEGORIES or privacy_class in PRIVATE_PRIVACY_CLASSES:
            metadata_only_candidates += 1
        if _needs_review(category, privacy_class):
            review_candidates += 1
            if len(sample_review_paths) < max_samples:
                sample_review_paths.append(record.item.relative_path)

    software_plans = build_nextcloud_software_archive_plans(
        inventory,
        source_id=source,
        target_root=software_archive_target_root,
    )
    return NextcloudImportDryRunReport(
        source_id=source,
        inventory_total=len(inventory),
        by_file_category=dict(sorted(by_file_category.items())),
        by_privacy_class=dict(sorted(by_privacy_class.items())),
        long_path_count=long_path_count,
        document_candidates=document_candidates,
        metadata_only_candidates=metadata_only_candidates,
        review_candidates=review_candidates,
        software_archive_candidates=len(software_plans),
        software_archive_paths=tuple(plan.archive_path for plan in software_plans[:max_samples]),
        sample_review_paths=tuple(sample_review_paths),
    )


def _inventory_records(records: Iterable[BigDataLedgerRecord], *, source_id: str) -> Iterable[BigDataLedgerRecord]:
    for record in records:
        if record.stage != "inventory" or record.status != "completed":
            continue
        if record.item.source_id != source_id:
            continue
        yield record


def _privacy_class(record: BigDataLedgerRecord) -> str:
    privacy = record.metadata.get("privacy")
    if isinstance(privacy, Mapping):
        return str(privacy.get("privacy_class") or record.metadata.get("privacy_class") or "unknown")
    return str(record.metadata.get("privacy_class") or "unknown")


def _needs_review(category: str, privacy_class: str) -> bool:
    return category in REVIEW_CATEGORIES or privacy_class in PRIVATE_PRIVACY_CLASSES
