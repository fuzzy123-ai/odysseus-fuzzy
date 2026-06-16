"""Manual 1.0 release evidence model.

Manual gates are intentionally separate from automated test gates. A partial
provider proof or a read-only rebuild probe must not accidentally become an
external release "go" just because the surrounding unit tests are green.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


GO = "go"
PARTIAL = "partial"
NO_GO = "no_go"
PENDING = "pending"

VALID_RESULTS = frozenset({GO, PARTIAL, NO_GO, PENDING})
REQUIRED_GATE_IDS = frozenset(
    {
        "fresh-install",
        "upgrade-path",
        "provider-proof",
        "export-import-rebuild",
        "known-limits-review",
    }
)


@dataclass(frozen=True)
class ManualEvidenceEntry:
    gate_id: str
    label: str
    result: str
    commit: str
    evidence_ref: str
    blocker: str = ""
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.gate_id.strip():
            raise ValueError("gate_id is required")
        if self.result not in VALID_RESULTS:
            raise ValueError("invalid manual evidence result")
        if self.result == GO and (not self.commit.strip() or not self.evidence_ref.strip()):
            raise ValueError("go evidence requires commit and evidence_ref")
        if self.result in {PARTIAL, NO_GO} and not self.blocker.strip():
            raise ValueError("partial/no_go evidence requires a blocker")

    @property
    def external_go(self) -> bool:
        return self.result == GO


@dataclass(frozen=True)
class ManualEvidenceSummary:
    external_go: bool
    status: str
    missing_gate_ids: tuple[str, ...]
    pending_gate_ids: tuple[str, ...]
    partial_gate_ids: tuple[str, ...]
    no_go_gate_ids: tuple[str, ...]
    evidence_refs: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "external_go": self.external_go,
            "status": self.status,
            "missing_gate_ids": self.missing_gate_ids,
            "pending_gate_ids": self.pending_gate_ids,
            "partial_gate_ids": self.partial_gate_ids,
            "no_go_gate_ids": self.no_go_gate_ids,
            "evidence_refs": self.evidence_refs,
        }


def summarize_manual_evidence(
    entries: Iterable[ManualEvidenceEntry],
    *,
    required_gate_ids: Iterable[str] = REQUIRED_GATE_IDS,
) -> ManualEvidenceSummary:
    normalized = tuple(entries)
    required = tuple(required_gate_ids)
    by_gate = {entry.gate_id: entry for entry in normalized}

    missing = tuple(gate_id for gate_id in required if gate_id not in by_gate)
    pending = tuple(entry.gate_id for entry in normalized if entry.result == PENDING)
    partial = tuple(entry.gate_id for entry in normalized if entry.result == PARTIAL)
    no_go = tuple(entry.gate_id for entry in normalized if entry.result == NO_GO)
    evidence_refs = tuple(dict.fromkeys(entry.evidence_ref for entry in normalized if entry.evidence_ref.strip()))

    external_go = not missing and not pending and not partial and not no_go
    if no_go:
        status = "no_go"
    elif partial:
        status = "partial_no_go"
    elif pending or missing:
        status = "pending"
    else:
        status = "go"

    return ManualEvidenceSummary(
        external_go=external_go,
        status=status,
        missing_gate_ids=missing,
        pending_gate_ids=pending,
        partial_gate_ids=partial,
        no_go_gate_ids=no_go,
        evidence_refs=evidence_refs,
    )


def current_manual_evidence_entries() -> tuple[ManualEvidenceEntry, ...]:
    """Manual evidence currently documented in the active 1.0 evidence log."""
    return (
        ManualEvidenceEntry(
            "fresh-install",
            "Fresh Install",
            GO,
            "3c2cdab0",
            r"C:\tmp\odysseus-rel3-fresh-2cea25f",
        ),
        ManualEvidenceEntry(
            "upgrade-path",
            "Upgrade Path",
            GO,
            "3c2cdab0",
            r"C:\tmp\odysseus-rel3-upgrade-proof",
        ),
        ManualEvidenceEntry(
            "provider-proof",
            "Provider Proof",
            PARTIAL,
            "3c2cdab0",
            "authenticated browser read-only run",
            blocker="query layer not ready for model-backed answer",
        ),
        ManualEvidenceEntry(
            "export-import-rebuild",
            "Export / Import / Rebuild Proof",
            PARTIAL,
            "3c2cdab0",
            "authenticated read-only proof plus REL1 tests",
            blocker="controlled write run with small test vault is still open",
        ),
        ManualEvidenceEntry(
            "known-limits-review",
            "Known Limits Review",
            GO,
            "91f2f737",
            "docs/plans/1.0-evidence-release-checklist.md",
        ),
    )
