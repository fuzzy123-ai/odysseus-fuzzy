"""Coding quality and sandbox alignment with Gate Evidence Core.

This CAO4 module is additive and side-effect free.  It turns existing
CodingQualityGateReport/CodingSandboxDispatch style payloads into canonical
gate evidence and reusable redacted result bundles without running checks,
dispatching sandbox jobs or exposing raw command output.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping

from src.agent_result_observer import ResultArtifact, ResultEvidenceBundle
from src.gate_evidence_core import (
    CanonicalGate,
    EvidenceItem,
    GateClass,
    GateFamily,
    GateStatus,
    LiveRequirement,
    NextAction,
    NextActionType,
    OperatorDecision,
    RedactionFlag,
    what_can_safely_happen_now,
)


CODING_QUALITY_ALIGNMENT_SCHEMA = "odysseus.coding_quality_alignment.v1"

_SAFE_TOKEN_RE = re.compile(r"[^a-z0-9]+")
_SECRET_RE = re.compile(
    r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer|chat[_-]?id|credential)\b\s*[:=]?\s*\S*"
)
_HOST_PATH_RE = re.compile(r"(^|[\s'\"=])([A-Za-z]:[\\/]|/(home|Users|var|opt|mnt|srv)/|~[\\/])", re.IGNORECASE)


class CodingQualityAlignmentError(ValueError):
    """Raised when coding quality evidence cannot be safely aligned."""


@dataclass(frozen=True, slots=True)
class CodingQualityAlignment:
    quality_gate: CanonicalGate
    sandbox_gate: CanonicalGate | None
    evidence_bundle: dict[str, Any]
    safe_now: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        gates = [self.quality_gate.to_dict()]
        if self.sandbox_gate is not None:
            gates.append(self.sandbox_gate.to_dict())
        return {
            "schema": CODING_QUALITY_ALIGNMENT_SCHEMA,
            "quality_gate": self.quality_gate.to_dict(),
            "sandbox_gate": self.sandbox_gate.to_dict() if self.sandbox_gate is not None else None,
            "gates": tuple(gates),
            "evidence_bundle": self.evidence_bundle,
            "safe_now": self.safe_now,
            "raw_content_visible": False,
        }


def adapt_coding_quality_gate(
    quality_gate: Any,
    *,
    gate_id: Any = "coding-quality",
) -> CanonicalGate:
    payload = _mapping_or_dict(quality_gate)
    status = _quality_status(payload)
    blockers = _texts(payload.get("blockers"))
    warnings = _texts(payload.get("warnings"))
    changed_paths = _iterable(payload.get("changed_paths"))
    check_results = _iterable(payload.get("check_results"))
    if status in {GateStatus.BLOCKED, GateStatus.NO_GO} and not blockers:
        blockers = ("coding quality gate is not verified",)
    next_action = _quality_next_action(status, warning_count=len(warnings))
    return CanonicalGate.create(
        gate_id=gate_id,
        family=GateFamily.QUALITY,
        gate_class=GateClass.PRECHECK,
        status=status,
        evidence=(
            EvidenceItem.create(
                evidence_id=f"{gate_id}-summary",
                summary=(
                    f"Coding quality gate status {status.value}; "
                    f"changed_paths={len(changed_paths)}; checks={len(check_results)}; "
                    f"warnings={len(warnings)}; blockers={len(blockers)}."
                ),
                source="coding_agent_quality_gate",
                redaction_flags=(RedactionFlag.SUMMARY_ONLY, RedactionFlag.RAW_PROVIDER_OUTPUT_OMITTED),
            ),
        ),
        redaction_flags=(RedactionFlag.SUMMARY_ONLY, RedactionFlag.RAW_PROVIDER_OUTPUT_OMITTED),
        next_action=next_action,
        live_requirement=LiveRequirement.NOT_REQUIRED,
        operator_decision=OperatorDecision.NOT_REQUIRED,
        safe_actions=("review redacted coding quality evidence",) if status in {GateStatus.GO, GateStatus.PARTIAL} else (),
        blockers=blockers if status in {GateStatus.BLOCKED, GateStatus.NO_GO, GateStatus.PARTIAL} else (),
    )


def build_coding_sandbox_evidence_bundle(dispatch: Any) -> dict[str, Any]:
    payload = _mapping_or_dict(dispatch)
    task_id = _safe_token(payload.get("task_id") or "coding-sandbox")
    statuses = _iterable(payload.get("statuses"))
    artifacts: list[ResultArtifact] = []
    for index, status_payload in enumerate(statuses, start=1):
        status = _mapping_or_dict(status_payload)
        job_id = _safe_token(status.get("job_id") or f"{task_id}-check-{index}")
        status_text = _safe_token(status.get("status") or "unknown")
        artifact_status = "ok" if status_text in {"succeeded", "dry_run"} else "failed"
        artifacts.append(
            ResultArtifact.create(
                kind="command_output",
                artifact_ref=f"reports/sandbox/{job_id}.log",
                summary=_sandbox_status_summary(status_text),
                status=artifact_status,
            )
        )
    if not artifacts:
        artifacts.append(
            ResultArtifact.create(
                kind="command_output",
                artifact_ref=f"reports/sandbox/{task_id}.log",
                summary="No sandbox status payloads were provided.",
                status="failed",
            )
        )
    return ResultEvidenceBundle.create(run_id=task_id, artifacts=artifacts).to_dict()


def adapt_coding_sandbox_dispatch(
    dispatch: Any,
    *,
    gate_id: Any = "coding-sandbox",
) -> CanonicalGate:
    payload = _mapping_or_dict(dispatch)
    quality = _mapping_or_dict(payload.get("quality_gate"))
    bundle = build_coding_sandbox_evidence_bundle(payload)
    statuses = tuple(_mapping_or_dict(status) for status in _iterable(payload.get("statuses")))
    status_values = tuple(_safe_token(status.get("status") or "unknown") for status in statuses)
    blockers = [*_texts(quality.get("blockers"))]
    if bundle.get("verdict") == "failed" and not blockers:
        blockers.append("one or more sandbox checks failed")
    verified = bool(quality.get("verified") or quality.get("status") == "verified")
    if verified and bundle.get("verdict") == "passed":
        gate_status = GateStatus.GO
    elif blockers:
        gate_status = GateStatus.BLOCKED
    elif "dry_run" in status_values:
        gate_status = GateStatus.PARTIAL
    else:
        gate_status = GateStatus.DEFERRED
    return CanonicalGate.create(
        gate_id=gate_id,
        family=GateFamily.TESTS,
        gate_class=GateClass.PRECHECK,
        status=gate_status,
        evidence=(
            EvidenceItem.create(
                evidence_id=f"{gate_id}-summary",
                summary=(
                    f"Coding sandbox dispatch status {gate_status.value}; "
                    f"jobs={len(_iterable(payload.get('jobs')))}; statuses={len(statuses)}; "
                    f"artifact_count={bundle.get('artifact_count', 0)}; verdict={bundle.get('verdict', 'unknown')}."
                ),
                source="coding_agent_sandbox_dispatch",
                redaction_flags=(RedactionFlag.SUMMARY_ONLY, RedactionFlag.RAW_PROVIDER_OUTPUT_OMITTED),
            ),
        ),
        redaction_flags=(RedactionFlag.SUMMARY_ONLY, RedactionFlag.RAW_PROVIDER_OUTPUT_OMITTED),
        next_action=_sandbox_next_action(gate_status),
        live_requirement=LiveRequirement.DRY_RUN_ONLY,
        operator_decision=OperatorDecision.NOT_REQUIRED,
        safe_actions=("review redacted sandbox evidence",) if gate_status in {GateStatus.GO, GateStatus.PARTIAL} else (),
        blockers=tuple(blockers) if gate_status in {GateStatus.BLOCKED, GateStatus.NO_GO, GateStatus.PARTIAL} else (),
    )


def build_coding_quality_alignment(
    *,
    quality_gate: Any,
    sandbox_dispatch: Any | None = None,
) -> CodingQualityAlignment:
    quality = adapt_coding_quality_gate(quality_gate)
    sandbox_gate = adapt_coding_sandbox_dispatch(sandbox_dispatch) if sandbox_dispatch is not None else None
    evidence_bundle = (
        build_coding_sandbox_evidence_bundle(sandbox_dispatch)
        if sandbox_dispatch is not None
        else _quality_evidence_bundle(quality)
    )
    gates = (quality, *(gate for gate in (sandbox_gate,) if gate is not None))
    return CodingQualityAlignment(
        quality_gate=quality,
        sandbox_gate=sandbox_gate,
        evidence_bundle=evidence_bundle,
        safe_now=what_can_safely_happen_now(gates),
    )


def _quality_status(payload: Mapping[str, Any]) -> GateStatus:
    status_text = _safe_token(payload.get("status") or "")
    blockers = _texts(payload.get("blockers"))
    warnings = _texts(payload.get("warnings"))
    verified = bool(payload.get("verified") or status_text == "verified")
    if blockers or status_text in {"blocked", "failed", "fail"} or payload.get("verified") is False:
        return GateStatus.BLOCKED
    if verified and warnings:
        return GateStatus.PARTIAL
    if verified:
        return GateStatus.GO
    return GateStatus.DEFERRED


def _quality_next_action(status: GateStatus, *, warning_count: int) -> NextAction:
    if status == GateStatus.GO:
        return NextAction.create(action_type=NextActionType.PROCEED, summary="continue with review gate")
    if status == GateStatus.PARTIAL:
        return NextAction.create(action_type=NextActionType.COLLECT_EVIDENCE, summary=f"review {warning_count} coding quality warning(s)")
    if status == GateStatus.DEFERRED:
        return NextAction.create(action_type=NextActionType.COLLECT_EVIDENCE, summary="collect coding quality evidence")
    return NextAction.create(action_type=NextActionType.FIX_BLOCKER, summary="fix coding quality blockers before review")


def _sandbox_next_action(status: GateStatus) -> NextAction:
    if status == GateStatus.GO:
        return NextAction.create(action_type=NextActionType.PROCEED, summary="continue with redacted sandbox evidence")
    if status == GateStatus.PARTIAL:
        return NextAction.create(action_type=NextActionType.COLLECT_EVIDENCE, summary="review sandbox dry-run evidence")
    if status == GateStatus.DEFERRED:
        return NextAction.create(action_type=NextActionType.COLLECT_EVIDENCE, summary="collect sandbox status evidence")
    return NextAction.create(action_type=NextActionType.FIX_BLOCKER, summary="fix sandbox check blockers")


def _sandbox_status_summary(status: str) -> str:
    if status == "dry_run":
        return "Sandbox check planned in dry-run mode; no live execution occurred."
    if status == "succeeded":
        return "Sandbox check completed successfully."
    if status == "failed":
        return "Sandbox check failed; raw output omitted."
    if status == "blocked":
        return "Sandbox check was blocked by sandbox policy."
    if status == "timed_out":
        return "Sandbox check timed out; raw output omitted."
    return f"Sandbox check status {status or 'unknown'}; raw output omitted."


def _quality_evidence_bundle(gate: CanonicalGate) -> dict[str, Any]:
    artifact = ResultArtifact.create(
        kind="command_output",
        artifact_ref=f"reports/coding-quality/{gate.gate_id}.log",
        summary=gate.evidence[0].summary,
        status="ok" if gate.status in {GateStatus.GO, GateStatus.PARTIAL} else "failed",
    )
    return ResultEvidenceBundle.create(run_id=gate.gate_id, artifacts=(artifact,)).to_dict()


def _mapping_or_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "to_dict"):
        raw = value.to_dict()
        if isinstance(raw, Mapping):
            return dict(raw)
    result: dict[str, Any] = {}
    for key in dir(value):
        if key.startswith("_"):
            continue
        try:
            item = getattr(value, key)
        except Exception:
            continue
        if callable(item):
            continue
        result[key] = item
    return result


def _iterable(value: Any) -> tuple[Any, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, (str, bytes)):
        return (value,)
    try:
        return tuple(value)
    except TypeError:
        return (value,)


def _texts(value: Any) -> tuple[str, ...]:
    result: list[str] = []
    for item in _iterable(value):
        text = " ".join(str(item or "").split())
        if not text:
            continue
        if _SECRET_RE.search(text) or _HOST_PATH_RE.search(text):
            raise CodingQualityAlignmentError("coding quality evidence contains unsafe private or secret material")
        result.append(text[:500])
    return tuple(dict.fromkeys(result))


def _safe_token(value: Any) -> str:
    text = str(value or "").strip().lower()
    if _SECRET_RE.search(text) or _HOST_PATH_RE.search(text):
        raise CodingQualityAlignmentError("coding quality identifier contains unsafe private or secret material")
    text = _SAFE_TOKEN_RE.sub("_", text).strip("_")
    text = re.sub(r"_{2,}", "_", text)
    if not text:
        return ""
    return text[:80]
