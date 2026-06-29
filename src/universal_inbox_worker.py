"""Dry-run worker orchestration for Universal Inbox live-readiness.

The worker reads local synced inbox files for discovery/extraction only. It
does not copy, move, delete, write sidecars, call Nextcloud, or write GraphRaptor.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from src.universal_inbox_discovery import (
    UniversalInboxDiscoveryReport,
    discover_universal_inbox_local,
)
from src.universal_inbox_extraction import (
    UniversalInboxExtractionPacket as LocalExtractionPacket,
    extract_universal_inbox_content,
)
from src.universal_inbox_memory import UniversalInboxMemoryAbstraction
from src.universal_inbox_pipeline import build_universal_inbox_pipeline_run
from src.universal_inbox_placement import build_universal_inbox_placement_plan
from src.universal_inbox_routing import (
    UniversalInboxRoutingRules,
    load_universal_inbox_routing_rules,
    plan_universal_inbox_route,
)


WORKER_SCHEMA = "odysseus.universal_inbox.worker_dry_run_report.v1"
_INVOICE_HINTS = ("invoice", "rechnung", "bill")
_CONTRACT_HINTS = ("contract", "vertrag", "agreement")
_PROJECT_HINTS = ("project", "projekt", "spec", "planung")


@dataclass(frozen=True)
class UniversalInboxWorkerConfig:
    domain: str = "private"
    default_document_type: str = "reference"
    default_confidence: float = 0.86
    review_confidence: float = 0.62
    incoming_prefix: str = "AI Inbox/Incoming"
    max_file_size_bytes: int | None = None
    max_extract_bytes: int = 2 * 1024 * 1024


@dataclass(frozen=True)
class UniversalInboxWorkerItemReport:
    relative_path: str
    filename: str
    source_hash: str
    extraction_status: str
    routing_decision: Mapping[str, Any]
    placement_plan: Mapping[str, Any]
    pipeline_report: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "relative_path": self.relative_path,
            "filename": self.filename,
            "source_hash": self.source_hash,
            "extraction_status": self.extraction_status,
            "routing_decision": dict(self.routing_decision),
            "placement_plan": dict(self.placement_plan),
            "pipeline_report": dict(self.pipeline_report),
        }


@dataclass(frozen=True)
class UniversalInboxWorkerDryRunReport:
    status: str
    items: tuple[UniversalInboxWorkerItemReport, ...]
    discovery: Mapping[str, Any]
    review_reasons: tuple[str, ...]
    no_go_reasons: tuple[str, ...]
    schema: str = WORKER_SCHEMA
    dry_run: bool = True
    writes_performed: bool = False
    host_paths_visible: bool = False
    raw_content_visible: bool = False

    @property
    def item_count(self) -> int:
        return len(self.items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "dry_run": self.dry_run,
            "writes_performed": self.writes_performed,
            "host_paths_visible": self.host_paths_visible,
            "raw_content_visible": self.raw_content_visible,
            "item_count": self.item_count,
            "discovery": dict(self.discovery),
            "review_reasons": self.review_reasons,
            "no_go_reasons": self.no_go_reasons,
            "items": tuple(item.to_dict() for item in self.items),
        }


def run_universal_inbox_dry_run(
    inbox_path: str | Path,
    *,
    config: UniversalInboxWorkerConfig | None = None,
    rules: UniversalInboxRoutingRules | Mapping[str, Any] | None = None,
) -> UniversalInboxWorkerDryRunReport:
    """Run the local-sync Universal Inbox pipeline in mutation-free dry-run mode."""

    worker_config = config or UniversalInboxWorkerConfig()
    routing_rules = _coerce_rules(rules)
    discovery = discover_universal_inbox_local(
        inbox_path,
        max_file_size_bytes=worker_config.max_file_size_bytes,
    )

    item_reports: list[UniversalInboxWorkerItemReport] = []
    review_reasons: list[str] = []
    no_go_reasons: list[str] = []
    for item in discovery.items:
        extraction = extract_universal_inbox_content(
            item.to_dict(),
            root=inbox_path,
            max_extract_bytes=worker_config.max_extract_bytes,
        )
        routing_decision = plan_universal_inbox_route(
            _routing_item(item.to_dict(), extraction, worker_config),
            rules=routing_rules,
        )
        memory = UniversalInboxMemoryAbstraction.from_routing_decision(
            routing_decision,
            abstract=_safe_abstract(item.to_dict(), extraction),
            tags=_safe_tags(routing_decision.domain, routing_decision.document_type),
        )
        placement = build_universal_inbox_placement_plan(routing_decision)
        pipeline = build_universal_inbox_pipeline_run(
            run_id=f"uix-dry-run-{item.sha256[:12]}",
            discovery={"status": "completed", "metadata": {"adapter": "local_read_only"}},
            ledger={"status": "pending", "metadata": {"provider": "nextcloud_inbox"}},
            extraction_packet={
                "status": extraction.status,
                "abstract": _safe_abstract(item.to_dict(), extraction),
                "raw_packet": extraction.to_dict(),
            },
            analysis={"status": "completed", "metadata": {"mode": "deterministic_stub"}},
            routing_decision=routing_decision,
            memory_abstraction=memory,
        )

        placement_report = placement.to_dict()
        pipeline_report = pipeline.to_dict()
        review_reasons.extend(placement.review_reasons)
        review_reasons.extend(pipeline_report.get("review_reasons", ()))
        no_go_reasons.extend(placement.no_go_reasons)
        no_go_reasons.extend(pipeline_report.get("no_go_reasons", ()))
        item_reports.append(
            UniversalInboxWorkerItemReport(
                relative_path=item.relative_path,
                filename=item.filename,
                source_hash=item.sha256,
                extraction_status=extraction.status,
                routing_decision=routing_decision.to_dict(),
                placement_plan=placement_report,
                pipeline_report=pipeline_report,
            )
        )

    if no_go_reasons:
        status = "no_go"
    elif review_reasons or discovery.warnings:
        status = "partial"
    else:
        status = "go"
    return UniversalInboxWorkerDryRunReport(
        status=status,
        items=tuple(item_reports),
        discovery=discovery.to_dict(),
        review_reasons=tuple(dict.fromkeys(review_reasons)),
        no_go_reasons=tuple(dict.fromkeys(no_go_reasons)),
    )


def _coerce_rules(
    rules: UniversalInboxRoutingRules | Mapping[str, Any] | None,
) -> UniversalInboxRoutingRules:
    if rules is None:
        return load_universal_inbox_routing_rules()
    if isinstance(rules, UniversalInboxRoutingRules):
        return rules
    return UniversalInboxRoutingRules.from_dict(rules)


def _routing_item(
    item: Mapping[str, Any],
    extraction: LocalExtractionPacket,
    config: UniversalInboxWorkerConfig,
) -> dict[str, Any]:
    document_type = _infer_document_type(item["filename"], config.default_document_type)
    partial_extraction = extraction.status in {"partial", "metadata_only", "unsupported", "failed", "blocked"}
    confidence = config.review_confidence if partial_extraction else config.default_confidence
    return {
        "original_path": f"{config.incoming_prefix}/{item['relative_path']}",
        "filename": item["filename"],
        "domain": config.domain,
        "document_type": document_type,
        "title": Path(item["filename"]).stem,
        "confidence": confidence,
        "source_hash": item["sha256"],
        "mtime": item["mtime"],
        "partial_extraction": partial_extraction,
    }


def _infer_document_type(filename: str, fallback: str) -> str:
    lowered = filename.lower()
    if any(hint in lowered for hint in _INVOICE_HINTS):
        return "invoice"
    if any(hint in lowered for hint in _CONTRACT_HINTS):
        return "contract"
    if any(hint in lowered for hint in _PROJECT_HINTS):
        return "project"
    return fallback


def _safe_abstract(
    item: Mapping[str, Any],
    extraction: LocalExtractionPacket,
) -> dict[str, Any]:
    return {
        "summary": _summary_for_status(extraction.status),
        "source_suffix": item.get("suffix") or extraction.suffix,
        "char_count": extraction.metadata.get("char_count", 0),
        "line_count": extraction.metadata.get("line_count", 0),
        "extractor": extraction.metadata.get("extractor", ""),
        "warnings": tuple(warning.code for warning in extraction.warnings),
    }


def _summary_for_status(status: str) -> str:
    if status == "completed":
        return "local file text extracted and summarized as metadata profile"
    if status == "metadata_only":
        return "local file metadata captured without text extraction"
    if status == "unsupported":
        return "local file type requires review before extraction"
    if status == "blocked":
        return "local file type is blocked before extraction"
    return "local file extraction requires review"


def _safe_tags(domain: str, document_type: str) -> tuple[str, ...]:
    return tuple(tag for tag in (domain, document_type) if tag and tag != "unknown")
