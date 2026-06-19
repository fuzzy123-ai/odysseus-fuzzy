"""Release evidence snapshot helpers.

The 1.0 release process distinguishes "automated checks are green" from
"external release is approved". This module keeps that distinction explicit and
machine-readable without executing tests, providers, imports, or host commands.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


AUTOMATED = "automated"
MANUAL = "manual"
PASS = "pass"
WARN = "warn"
PENDING = "pending"
BLOCKED = "blocked"
NOT_APPLICABLE = "not_applicable"

VALID_KINDS = frozenset({AUTOMATED, MANUAL})
VALID_STATUSES = frozenset({PASS, WARN, PENDING, BLOCKED, NOT_APPLICABLE})


@dataclass(frozen=True)
class ReleaseGate:
    gate_id: str
    label: str
    kind: str
    status: str
    evidence_refs: tuple[str, ...] = ()
    risk: str = ""
    required_for_external_release: bool = True

    def __post_init__(self) -> None:
        if not self.gate_id.strip():
            raise ValueError("gate_id is required")
        if self.kind not in VALID_KINDS:
            raise ValueError("kind must be automated or manual")
        if self.status not in VALID_STATUSES:
            raise ValueError("invalid release gate status")
        if self.status == PASS and self.required_for_external_release and not self.evidence_refs:
            raise ValueError("passing required gates need evidence_refs")


@dataclass(frozen=True)
class ReleaseEvidenceSnapshot:
    status: str
    external_release_go: bool
    automated_ok: bool
    manual_ok: bool
    blocking_gate_ids: tuple[str, ...] = ()
    pending_manual_gate_ids: tuple[str, ...] = ()
    warning_gate_ids: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    gates: tuple[ReleaseGate, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "external_release_go": self.external_release_go,
            "automated_ok": self.automated_ok,
            "manual_ok": self.manual_ok,
            "blocking_gate_ids": self.blocking_gate_ids,
            "pending_manual_gate_ids": self.pending_manual_gate_ids,
            "warning_gate_ids": self.warning_gate_ids,
            "evidence_refs": self.evidence_refs,
            "gate_count": len(self.gates),
        }


def build_release_evidence_snapshot(gates: Iterable[ReleaseGate]) -> ReleaseEvidenceSnapshot:
    normalized = tuple(gates)
    blocking = tuple(gate.gate_id for gate in normalized if gate.status == BLOCKED)
    pending_manual = tuple(
        gate.gate_id
        for gate in normalized
        if gate.kind == MANUAL and gate.required_for_external_release and gate.status == PENDING
    )
    warnings = tuple(gate.gate_id for gate in normalized if gate.status == WARN)
    evidence_refs = tuple(
        dict.fromkeys(ref for gate in normalized for ref in gate.evidence_refs if ref.strip())
    )

    automated_ok = all(
        gate.status not in {PENDING, BLOCKED}
        for gate in normalized
        if gate.kind == AUTOMATED and gate.required_for_external_release
    )
    manual_ok = all(
        gate.status not in {PENDING, BLOCKED}
        for gate in normalized
        if gate.kind == MANUAL and gate.required_for_external_release
    )
    external_go = automated_ok and manual_ok and not blocking and not pending_manual

    if blocking:
        status = "blocked"
    elif pending_manual:
        status = "manual_pending"
    elif warnings:
        status = "go_with_warnings" if external_go else "pending_with_warnings"
    else:
        status = "go" if external_go else "pending"

    return ReleaseEvidenceSnapshot(
        status=status,
        external_release_go=external_go,
        automated_ok=automated_ok,
        manual_ok=manual_ok,
        blocking_gate_ids=blocking,
        pending_manual_gate_ids=pending_manual,
        warning_gate_ids=warnings,
        evidence_refs=evidence_refs,
        gates=normalized,
    )


def default_1_0_release_gates() -> tuple[ReleaseGate, ...]:
    """Current 1.0 gate names from the active release checklist.

    Automated gates can be populated with fresh evidence after a run. Manual
    gates intentionally default to pending so external 1.0 cannot be claimed
    from unit-test success alone.
    """
    return (
        ReleaseGate(
            gate_id="memory-obsidian-external-proof",
            label="Memory / Obsidian / External Proof",
            kind=AUTOMATED,
            status=PASS,
            evidence_refs=("REL1 automated gate run",),
        ),
        ReleaseGate(
            gate_id="static-context-ui-safety-smoke",
            label="Static / Context / UI Safety Smoke",
            kind=AUTOMATED,
            status=PASS,
            evidence_refs=("REL1 automated gate run",),
        ),
        ReleaseGate(
            gate_id="scale-lightweight-maintenance",
            label="Scale / Lightweight Maintenance",
            kind=AUTOMATED,
            status=PASS,
            evidence_refs=("REL1 automated gate run",),
        ),
        ReleaseGate("fresh-install", "Fresh install", MANUAL, PASS, ("manual evidence log",)),
        ReleaseGate("upgrade-path", "Upgrade path", MANUAL, PASS, ("manual evidence log",)),
        ReleaseGate("provider-proof", "Provider proof", MANUAL, PENDING, risk="provider fallback not yet evidenced"),
        ReleaseGate(
            "export-import-rebuild",
            "Export/Import/Rebuild",
            MANUAL,
            PASS,
            ("P1 isolated test-vault evidence run-7dyxtze_",),
        ),
        ReleaseGate("known-limits-review", "Known Limits Review", MANUAL, PASS, ("manual evidence log",)),
    )
