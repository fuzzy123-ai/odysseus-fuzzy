"""MVP Roadmap 3 private data / Nextcloud ingestion progress model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_GATE_STATUSES = ("go", "repo_open", "needs_live_go", "needs_design", "blocked", "deferred")
_SLICE_CLASSES = ("safe_offline", "repo_only", "needs_live_go", "needs_design", "blocked")


def _normalize_text(value: Any, *, field_name: str) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_status(value: Any) -> str:
    text = _normalize_text(value, field_name="status").strip().lower()
    if text not in _GATE_STATUSES:
        raise ValueError("unsupported private data closure gate status")
    return text


def _normalize_slice_class(value: Any) -> str:
    text = _normalize_text(value, field_name="slice_class").strip().lower()
    if text not in _SLICE_CLASSES:
        raise ValueError("unsupported private data closure slice class")
    return text


@dataclass(frozen=True, slots=True)
class PrivateDataIngestionGate:
    gate_id: str
    title: str
    status: str
    slice_class: str
    reason: str

    @classmethod
    def create(
        cls,
        *,
        gate_id: Any,
        title: Any,
        status: Any,
        slice_class: Any,
        reason: Any,
    ) -> "PrivateDataIngestionGate":
        return cls(
            gate_id=_normalize_text(gate_id, field_name="gate_id").strip().lower(),
            title=_normalize_text(title, field_name="title"),
            status=_normalize_status(status),
            slice_class=_normalize_slice_class(slice_class),
            reason=_normalize_text(reason, field_name="reason"),
        )

    @property
    def complete(self) -> bool:
        return self.status == "go"

    def to_dict(self) -> dict[str, str]:
        return {
            "gate_id": self.gate_id,
            "title": self.title,
            "status": self.status,
            "slice_class": self.slice_class,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class PrivateDataIngestionReport:
    roadmap_id: str
    title: str
    gates: tuple[PrivateDataIngestionGate, ...]
    percent_complete: int
    why_not_100: str
    recommended_next_human_decision: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "roadmap_id": self.roadmap_id,
            "title": self.title,
            "percent_complete": self.percent_complete,
            "why_not_100": self.why_not_100,
            "recommended_next_human_decision": self.recommended_next_human_decision,
            "gates": tuple(gate.to_dict() for gate in self.gates),
        }

    def to_markdown_row(self) -> str:
        reason = "-" if self.percent_complete == 100 else self.why_not_100
        return f"| 3 | {self.title} | {self.percent_complete} | {reason} |"


def _percent(gates: Iterable[PrivateDataIngestionGate]) -> int:
    items = tuple(gates)
    if not items:
        return 0
    complete = sum(1 for gate in items if gate.complete)
    return round((complete / len(items)) * 100)


def _first_incomplete(gates: Iterable[PrivateDataIngestionGate]) -> PrivateDataIngestionGate | None:
    for gate in gates:
        if not gate.complete:
            return gate
    return None


def build_private_data_ingestion_report(
    *,
    planning_sources_inventory_go: bool = True,
    planning_sources_ingest_go: bool = True,
    bigdata_ledger_contract_go: bool = True,
    nextcloud_transfer_readiness_go: bool = False,
    resumable_transfer_tooling_go: bool = True,
    resumable_scanner_dry_run_go: bool = True,
    live_small_batch_transfer_go: bool = False,
    chunked_extraction_lanes_go: bool = True,
    memory_abstraction_ingest_live_go: bool = False,
    full_transfer_live_go: bool = False,
    full_corpus_analysis_live_go: bool = False,
    ingestion_dashboard_live_go: bool = False,
) -> PrivateDataIngestionReport:
    gates = (
        PrivateDataIngestionGate.create(
            gate_id="planning_sources_inventory",
            title="Planning sources memory inventory",
            status="go" if planning_sources_inventory_go else "blocked",
            slice_class="repo_only",
            reason=(
                "planning source inventory is bounded, read-only, and redacted"
                if planning_sources_inventory_go
                else "planning source inventory is missing or blocked"
            ),
        ),
        PrivateDataIngestionGate.create(
            gate_id="planning_sources_ingest",
            title="Planning sources memory ingest live",
            status="go" if planning_sources_ingest_go else "blocked",
            slice_class="repo_only",
            reason=(
                "planning documents can be ingested as bounded memory capsules"
                if planning_sources_ingest_go
                else "planning source memory ingest is missing or blocked"
            ),
        ),
        PrivateDataIngestionGate.create(
            gate_id="bigdata_ledger_contract",
            title="Big Data ledger contract",
            status="go" if bigdata_ledger_contract_go else "blocked",
            slice_class="repo_only",
            reason=(
                "append-only metadata ledger contract exists without raw content"
                if bigdata_ledger_contract_go
                else "big data ledger contract is missing or blocked"
            ),
        ),
        PrivateDataIngestionGate.create(
            gate_id="nextcloud_transfer_readiness",
            title="Nextcloud transfer readiness",
            status="go" if nextcloud_transfer_readiness_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "operator inputs, source/target paths, disk budget, and no-delete dry-run are validated"
                if nextcloud_transfer_readiness_go
                else "needs operator transfer inputs and no-delete dry-run readiness model"
            ),
        ),
        PrivateDataIngestionGate.create(
            gate_id="resumable_transfer_tooling",
            title="Resumable transfer tooling",
            status="go" if resumable_transfer_tooling_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "copy-only transfer wrapper records resumable ledger progress"
                if resumable_transfer_tooling_go
                else "needs copy-only resumable transfer tooling with redacted ledger progress"
            ),
        ),
        PrivateDataIngestionGate.create(
            gate_id="resumable_scanner_dry_run",
            title="Resumable scanner dry-run",
            status="go" if resumable_scanner_dry_run_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "scanner dry-run handles large trees in pages with recoverable ledger states"
                if resumable_scanner_dry_run_go
                else "needs synthetic large-tree scanner dry-run and recoverable ledger states"
            ),
        ),
        PrivateDataIngestionGate.create(
            gate_id="live_small_batch_transfer",
            title="Live small-batch transfer",
            status="go" if live_small_batch_transfer_go else "needs_live_go",
            slice_class="needs_live_go",
            reason=(
                "representative live subset transferred and verified with redacted evidence"
                if live_small_batch_transfer_go
                else "needs explicit operator Go for small live Nextcloud transfer"
            ),
        ),
        PrivateDataIngestionGate.create(
            gate_id="chunked_extraction_lanes",
            title="Chunked extraction lanes",
            status="go" if chunked_extraction_lanes_go else "repo_open",
            slice_class="repo_only",
            reason=(
                "chunked extraction lanes process documents with retryable ledger states"
                if chunked_extraction_lanes_go
                else "needs offline chunked extraction lanes and retry/error modeling"
            ),
        ),
        PrivateDataIngestionGate.create(
            gate_id="memory_abstraction_ingest_live",
            title="Memory abstraction ingest live",
            status="go" if memory_abstraction_ingest_live_go else "needs_live_go",
            slice_class="needs_live_go",
            reason=(
                "memory ingest path has live evidence without raw-content leaks"
                if memory_abstraction_ingest_live_go
                else "needs operator-approved ingest evidence after dry-run lanes are safe"
            ),
        ),
        PrivateDataIngestionGate.create(
            gate_id="full_transfer_live",
            title="Full 100GB+ transfer live",
            status="go" if full_transfer_live_go else "needs_live_go",
            slice_class="needs_live_go",
            reason=(
                "full corpus transfer completed or has a verified retry plan"
                if full_transfer_live_go
                else "full 100GB+ transfer is a high-impact operator-gated live action"
            ),
        ),
        PrivateDataIngestionGate.create(
            gate_id="full_corpus_analysis_live",
            title="Full corpus analysis live",
            status="go" if full_corpus_analysis_live_go else "needs_live_go",
            slice_class="needs_live_go",
            reason=(
                "full corpus analysis completed with redacted provenance evidence"
                if full_corpus_analysis_live_go
                else "needs successful transfer plus explicit Go before full corpus analysis"
            ),
        ),
        PrivateDataIngestionGate.create(
            gate_id="ingestion_dashboard_live",
            title="Ingestion dashboard live",
            status="go" if ingestion_dashboard_live_go else "needs_design",
            slice_class="needs_design",
            reason=(
                "operator can inspect transfer, scan, extraction, retry, and throughput state"
                if ingestion_dashboard_live_go
                else "dashboard is deferred until backend ingestion states are stable and UI is redesigned"
            ),
        ),
    )
    percent_complete = _percent(gates)
    first_incomplete = _first_incomplete(gates)
    if first_incomplete is None:
        why_not_100 = "-"
        next_decision = "Roadmap 3 is complete; continue to System Health Checker Host-Agent."
    else:
        why_not_100 = f"{first_incomplete.title}: {first_incomplete.reason}"
        if first_incomplete.status == "repo_open":
            next_decision = "Continue backend-safe private-data ingestion work, starting with Nextcloud transfer readiness."
        elif first_incomplete.slice_class == "needs_live_go":
            next_decision = "Grant or defer the next private-data live gate before claiming live ingestion closure."
        else:
            next_decision = f"Resolve {first_incomplete.title} before private-data ingestion closure."
    return PrivateDataIngestionReport(
        roadmap_id="private_data_nextcloud_memory_ingestion",
        title="Private Data / Nextcloud Memory Ingestion",
        gates=gates,
        percent_complete=percent_complete,
        why_not_100=why_not_100,
        recommended_next_human_decision=next_decision,
    )
