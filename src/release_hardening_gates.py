"""Read-only release hardening gate index for ABC3."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


_STATUSES = ("go", "partial", "no_go", "deferred")
_GATE_IDS = (
    "large_vault_performance",
    "graph_filter_state_isolation",
    "at_rest_security_disclosure",
    "project_apply_conflict_blocking",
    "repository_link_hygiene",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_gate_id(value: Any) -> str:
    gate_id = _normalize_text(value, field_name="gate_id").lower().replace("-", "_")
    if gate_id not in _GATE_IDS:
        raise ValueError(f"unsupported release hardening gate: {value!r}")
    return gate_id


def _normalize_status(value: Any) -> str:
    status = _normalize_text(value, field_name="status").lower().replace("-", "_")
    if status not in _STATUSES:
        raise ValueError(f"unsupported release hardening status: {value!r}")
    return status


def _dedupe(values: Iterable[Any], *, field_name: str) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        item = _normalize_text(value, field_name=field_name, allow_empty=True)
        if item and item not in result:
            result.append(item)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ReleaseHardeningGate:
    gate_id: str
    status: str
    summary: str
    evidence_refs: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    recommended_slices: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "gate_id", _normalize_gate_id(self.gate_id))
        object.__setattr__(self, "status", _normalize_status(self.status))
        object.__setattr__(self, "summary", _normalize_text(self.summary, field_name="summary"))
        object.__setattr__(self, "evidence_refs", _dedupe(self.evidence_refs, field_name="evidence_ref"))
        object.__setattr__(self, "missing_evidence", _dedupe(self.missing_evidence, field_name="missing_evidence"))
        object.__setattr__(self, "recommended_slices", _dedupe(self.recommended_slices, field_name="recommended_slice"))
        if not self.evidence_refs:
            raise ValueError("evidence_refs must not be empty")
        if self.status != "go" and not self.missing_evidence:
            raise ValueError("non-go hardening gates must name missing evidence")

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "status": self.status,
            "summary": self.summary,
            "evidence_refs": list(self.evidence_refs),
            "missing_evidence": list(self.missing_evidence),
            "recommended_slices": list(self.recommended_slices),
        }


@dataclass(frozen=True, slots=True)
class ReleaseHardeningIndex:
    gates: tuple[ReleaseHardeningGate, ...]
    external_release_ready: bool
    decision: str
    next_actions: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.gates:
            raise ValueError("gates must not be empty")
        object.__setattr__(self, "decision", _normalize_text(self.decision, field_name="decision"))
        object.__setattr__(self, "next_actions", _dedupe(self.next_actions, field_name="next_action"))

    @property
    def blocking_gate_ids(self) -> tuple[str, ...]:
        return tuple(gate.gate_id for gate in self.gates if gate.status == "no_go")

    @property
    def partial_gate_ids(self) -> tuple[str, ...]:
        return tuple(gate.gate_id for gate in self.gates if gate.status == "partial")

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "external_release_ready": self.external_release_ready,
            "blocking_gate_ids": list(self.blocking_gate_ids),
            "partial_gate_ids": list(self.partial_gate_ids),
            "next_actions": list(self.next_actions),
            "gates": [gate.to_dict() for gate in self.gates],
        }

    def to_markdown(self) -> str:
        lines = [
            "# Release Hardening Index",
            "",
            f"- Decision: `{self.decision}`",
            f"- External release ready: `{str(self.external_release_ready).lower()}`",
            "",
            "## Gates",
        ]
        for gate in self.gates:
            lines.append(f"- `{gate.gate_id}`: `{gate.status}` - {gate.summary}")
            if gate.recommended_slices:
                lines.append(f"  Recommended slices: {', '.join(gate.recommended_slices)}")
        if self.next_actions:
            lines.extend(["", "## Next Actions"])
            lines.extend(f"- {action}" for action in self.next_actions)
        return "\n".join(lines)


def build_release_hardening_index() -> ReleaseHardeningIndex:
    gates = (
        ReleaseHardeningGate(
            gate_id="large_vault_performance",
            status="no_go",
            summary="Large-vault claims are blocked until synthetic scale evidence names vault size, workload, and budgets.",
            evidence_refs=(
                "docs/plans/release-hardening-gates.md",
                "plugins/obsidian/tests/test_vault_performance_baseline.py",
                "plugins/obsidian/backend/performance_fixtures.py",
            ),
            missing_evidence=(
                "10k-file or 1GB synthetic-vault performance record",
                "p95 query/search/filter/graph latency budget",
                "rebuild or index maximum duration",
            ),
            recommended_slices=("ABC3A-performance-gate",),
        ),
        ReleaseHardeningGate(
            gate_id="graph_filter_state_isolation",
            status="partial",
            summary="Graph/filter state exists, but cross-project or scoped localStorage isolation is not yet evidenced.",
            evidence_refs=(
                "docs/plans/release-hardening-gates.md",
                "plugins/obsidian/frontend/main.js",
                "tests/test_obsidian_sidebar_static.py",
            ),
            missing_evidence=(
                "scoped graph filter storage key or reset rule",
                "project switch and reload smoke",
            ),
            recommended_slices=("ABC3B-graph-filter-state",),
        ),
        ReleaseHardeningGate(
            gate_id="at_rest_security_disclosure",
            status="partial",
            summary="Password-flow warning exists, but a persistent UI/status disclosure for indexes, caches, logs, and metadata is still needed.",
            evidence_refs=(
                "docs/plans/release-hardening-gates.md",
                "README.md",
                "plugins/obsidian/README.md",
                "plugins/obsidian/frontend/main.js",
                "tests/test_obsidian_sidebar_static.py",
            ),
            missing_evidence=(
                "persistent security status UI copy",
                "known-limits copy for derived indexes, caches, logs, and metadata",
            ),
            recommended_slices=("ABC3C-security-ui-docs",),
        ),
        ReleaseHardeningGate(
            gate_id="project_apply_conflict_blocking",
            status="partial",
            summary="Conflict blocking exists, but all route/tool/session overwrite paths need a strict-block matrix before broad release claims.",
            evidence_refs=(
                "docs/plans/release-hardening-gates.md",
                "plugins/obsidian/backend/project_planning.py",
                "plugins/obsidian/plugin.py",
                "plugins/obsidian/backend/routes.py",
                "plugins/obsidian/tests/test_project_planning_backend.py",
            ),
            missing_evidence=(
                "strict-block matrix for tool, route, session, selected apply, and explicit overwrite flows",
                "no-unselected-write assertion for conflict cases",
            ),
            recommended_slices=("ABC3D-strict-conflict-block-matrix",),
        ),
        ReleaseHardeningGate(
            gate_id="repository_link_hygiene",
            status="partial",
            summary="Canonical remotes are documented, but release-facing links still need an offline audit and intentional-retention map.",
            evidence_refs=(
                "docs/plans/origin-publish-hygiene.md",
                "docs/plans/abc-prioritized-execution-roadmap.md",
                "README.md",
                "plugins/obsidian/README.md",
                "package.json",
            ),
            missing_evidence=(
                "offline repository URL and typo snapshot",
                "canonical map for upstream, fork, plugin repo, and release branch",
            ),
            recommended_slices=("ABC3E-repo-link-audit",),
        ),
    )
    return ReleaseHardeningIndex(
        gates=tuple(sorted(gates, key=lambda gate: gate.gate_id)),
        external_release_ready=False,
        decision="external_no_go_until_hardening_and_manual_release_evidence_close",
        next_actions=(
            "Keep external 1.0 blocked while large-vault and manual release evidence are missing.",
            "Run ABC3A, ABC3C, and ABC3E in parallel before graph-state or conflict-semantics edits.",
            "Keep live provider, test-vault, host, Nextcloud, Telegram, backup, restore, and deploy actions gated.",
        ),
    )
