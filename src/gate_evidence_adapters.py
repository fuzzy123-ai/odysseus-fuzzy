"""Adapters from existing gate/readiness payloads to Gate Evidence Core.

The adapters in this module are intentionally stdlib-only and side-effect free.
They accept mappings, dataclasses, or objects with ``to_dict()``, keep the
source payload untouched, and emit only summarized canonical evidence.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
import re
from typing import Any, Iterable, Mapping

from src.gate_evidence_core import (
    CanonicalGate,
    EvidenceItem,
    GateClass,
    GateEvidenceCoreError,
    GateFamily,
    GateStatus,
    LiveRequirement,
    NextAction,
    NextActionType,
    OperatorDecision,
    RedactionFlag,
    assert_redaction_safe,
)


_TRUE_VALUES = {"1", "true", "yes", "on", "y", "approved"}
_GO_STATUSES = {"go", "ok", "pass", "passed", "ready", "success", "complete", "backend_ready"}
_PARTIAL_STATUSES = {"warn", "warning", "partial", "degraded", "review"}
_DEFERRED_STATUSES = {"pending", "manual_pending", "needs_manual_evidence", "skip", "skipped"}
_NO_GO_STATUSES = {"fail", "failed", "no_go", "nogo", "no", "denied", "declined"}
_BLOCKED_STATUSES = {"block", "blocked", "not_ready", "required", "incomplete"}
_TOKEN_RE = re.compile(r"[^a-z0-9]+")


def adapt_release_readiness(payload: Any, *, gate_id: Any = "release-readiness") -> CanonicalGate:
    """Adapt a release/readiness payload into one canonical release gate."""

    data = _as_mapping(payload, field_name="release_payload")
    release = _as_mapping(data.get("release"), field_name="release", allow_empty=True)
    ready = _first_bool(
        data,
        release,
        keys=("external_release_ready", "external_release_allowed", "version_1_0_ready", "ready", "ok"),
    )
    decision_text = _first_text(data, keys=("decision", "status", "next_human_decision"))
    live_required = _release_live_required(data, release)
    operator_required = _truthy(data.get("operator_required")) or bool(data.get("next_human_decision"))
    blockers = _release_blockers(data, release)
    status = _status_from_payload(
        data.get("status") or data.get("decision"),
        ready=ready,
        live_required=live_required,
        operator_required=operator_required,
    )
    if _contains_no_go(decision_text):
        status = GateStatus.NO_GO
    elif ready is False and status == GateStatus.GO:
        status = GateStatus.BLOCKED
    if status in {GateStatus.BLOCKED, GateStatus.NO_GO} and not blockers:
        blockers = (decision_text or f"release readiness is {status.value}",)

    evidence = _evidence(
        evidence_id=f"{gate_id}-summary",
        summary=_release_summary(data, release, status),
        source=_source(data, fallback="release_readiness"),
    )
    return _checked_gate(
        gate_id=gate_id,
        family=GateFamily.RELEASE,
        gate_class=GateClass.READINESS,
        status=status,
        evidence=(evidence,),
        next_action=_next_action(status, live_required=live_required, operator_required=operator_required),
        live_requirement=LiveRequirement.REQUIRED if live_required else LiveRequirement.NOT_REQUIRED,
        operator_decision=OperatorDecision.REQUIRED if operator_required else OperatorDecision.NOT_REQUIRED,
        safe_actions=_safe_actions(data, blocked_actions=()),
        blockers=blockers,
    )


def adapt_live_affordance_readiness(payload: Any) -> tuple[CanonicalGate, ...]:
    """Adapt live affordance readiness actions into canonical live gates."""

    data = _as_mapping(payload, field_name="live_affordance_payload")
    actions = data.get("actions") or ()
    if isinstance(actions, Mapping):
        actions = actions.values()
    gates: list[CanonicalGate] = []
    for index, action_payload in enumerate(actions):
        action = _as_mapping(action_payload, field_name="live_affordance_action")
        action_id = action.get("action_id") or action.get("id") or f"live-action-{index + 1}"
        blocked_live_actions = _texts(action.get("blocked_live_actions"))
        live_required = (
            bool(blocked_live_actions)
            or _truthy(action.get("live_required"))
            or _truthy(action.get("live_go_required"))
            or _truthy(action.get("requires_live"))
        )
        operator_required = (
            _truthy(action.get("operator_required"))
            or _truthy(action.get("manual_review_required"))
            or _truthy(action.get("operator_live_go_required"))
        )
        ready = action.get("ready") if isinstance(action.get("ready"), bool) else None
        status = _status_from_payload(
            action.get("status"),
            ready=ready,
            live_required=live_required,
            operator_required=operator_required,
        )
        blockers = _live_blockers(action, blocked_live_actions, live_required, operator_required)
        safe_actions = _safe_actions(action, blocked_actions=blocked_live_actions)
        evidence = _evidence(
            evidence_id=f"{action_id}-readiness",
            summary=_live_summary(action, blocked_live_actions),
            source="live_affordance_readiness",
        )
        gates.append(
            _checked_gate(
                gate_id=f"live-{action_id}",
                family=GateFamily.LIVE,
                gate_class=GateClass.EXECUTION,
                status=status,
                evidence=(evidence,),
                next_action=_next_action(
                    status,
                    live_required=live_required,
                    operator_required=operator_required,
                ),
                live_requirement=LiveRequirement.REQUIRED if live_required else LiveRequirement.NOT_REQUIRED,
                operator_decision=OperatorDecision.REQUIRED if operator_required else OperatorDecision.NOT_REQUIRED,
                safe_actions=safe_actions,
                blockers=blockers,
            )
        )
    if not gates:
        raise GateEvidenceCoreError("live affordance payload must contain at least one action")
    return tuple(sorted(gates, key=lambda gate: gate.gate_id))


def adapt_plugin_release_gate(payload: Any, *, gate_id: Any = "plugin-release-gate") -> CanonicalGate:
    """Adapt a plugin release gate payload into canonical evidence."""

    data = _as_mapping(payload, field_name="plugin_release_gate")
    ok = data.get("ok") if isinstance(data.get("ok"), bool) else None
    errors = _texts(data.get("errors"))
    warnings = _texts(data.get("warnings"))
    status = _status_from_payload(data.get("status"), ready=ok, live_required=False, operator_required=False)
    if ok is True and warnings:
        status = GateStatus.PARTIAL
    if ok is False and status == GateStatus.GO:
        status = GateStatus.NO_GO
    blockers = () if status not in {GateStatus.BLOCKED, GateStatus.NO_GO} else (
        f"plugin release gate has {len(errors) or 1} blocking issue(s)",
    )
    evidence = _evidence(
        evidence_id=f"{gate_id}-summary",
        summary=(
            "Plugin release gate "
            f"ok={_display_bool(ok)}; registry_ok={_display_bool(data.get('registry_ok'))}; "
            f"local_plugins_ok={_display_bool(data.get('local_plugins_ok'))}; "
            f"errors={len(errors)}; warnings={len(warnings)}."
        ),
        source="plugin_release_gate",
    )
    return _checked_gate(
        gate_id=gate_id,
        family=GateFamily.RELEASE,
        gate_class=GateClass.POLICY,
        status=status,
        evidence=(evidence,),
        next_action=_next_action(status, live_required=False, operator_required=False),
        blockers=blockers or warnings,
    )


def adapt_quality_gate(payload: Any) -> CanonicalGate:
    """Adapt one quality/runtime gate payload into canonical evidence."""

    data = _as_mapping(payload, field_name="quality_gate")
    gate_id = data.get("gate_id") or data.get("id") or "quality-gate"
    gate_type = _token(data.get("gate_type") or data.get("type") or "quality")
    status = _status_from_payload(data.get("status"), ready=None, live_required=False, operator_required=False)
    evidence_count = len(_texts(data.get("evidence")))
    block_reason = _first_text(data, keys=("block_reason", "reason"))
    next_action = _first_text(data, keys=("next_action",))
    blockers = _quality_blockers(status, block_reason, next_action)
    evidence = _evidence(
        evidence_id=f"{gate_id}-summary",
        summary=(
            f"Quality gate {gate_type or 'quality'} reported status {status.value}; "
            f"summarized_evidence_items={evidence_count}."
        ),
        source=f"quality_gate:{gate_type or 'quality'}",
    )
    return _checked_gate(
        gate_id=gate_id,
        family=_quality_family(gate_type),
        gate_class=GateClass.PRECHECK,
        status=status,
        evidence=(evidence,),
        next_action=_next_action(status, summary=next_action),
        blockers=blockers,
    )


def adapt_quality_gate_result(payload: Any) -> tuple[CanonicalGate, ...]:
    """Adapt a quality/runtime gate result containing multiple gate payloads."""

    data = _as_mapping(payload, field_name="quality_gate_result")
    gates = data.get("gates") or ()
    if isinstance(gates, Mapping):
        gates = gates.values()
    adapted = tuple(adapt_quality_gate(gate) for gate in gates)
    if not adapted:
        raise GateEvidenceCoreError("quality gate result must contain at least one gate")
    return tuple(sorted(adapted, key=lambda gate: gate.gate_id))


def adapt_review_gate_status(payload: Any) -> tuple[CanonicalGate, ...]:
    """Adapt review/write gate status payloads without changing their route shape."""

    data = _as_mapping(payload, field_name="review_gate_status")
    gates = data.get("gates") or ()
    if isinstance(gates, Mapping):
        gates = gates.values()
    adapted: list[CanonicalGate] = []
    for gate_payload in gates:
        gate = _as_mapping(gate_payload, field_name="review_gate")
        gate_id = gate.get("id") or "review-gate"
        state = _token(gate.get("state"))
        review_required = _truthy(gate.get("review_required")) or bool(gate.get("approval_command"))
        live_required = "live" in _token(gate.get("approval_command"))
        status = _review_status(state, review_required=review_required)
        blockers = _review_blockers(gate, state, review_required, live_required)
        evidence = _evidence(
            evidence_id=f"{gate_id}-review",
            summary=(
                f"Review gate {gate.get('family') or 'review'} state {state or 'unknown'}; "
                f"review_required={str(review_required).lower()}."
            ),
            source="review_gate_routes",
        )
        adapted.append(
            _checked_gate(
                gate_id=f"review-{gate_id}",
                family=GateFamily.OPERATOR if review_required else GateFamily.EVIDENCE,
                gate_class=GateClass.POLICY,
                status=status,
                evidence=(evidence,),
                next_action=_next_action(
                    status,
                    summary=_first_text(gate, keys=("reason", "approval_command")),
                    live_required=live_required,
                    operator_required=review_required,
                ),
                live_requirement=LiveRequirement.REQUIRED if live_required else LiveRequirement.NOT_REQUIRED,
                operator_decision=OperatorDecision.REQUIRED if review_required else OperatorDecision.NOT_REQUIRED,
                blockers=blockers,
            )
        )
    if not adapted:
        raise GateEvidenceCoreError("review gate status must contain at least one gate")
    return tuple(sorted(adapted, key=lambda gate: gate.gate_id))


def _as_mapping(payload: Any, *, field_name: str, allow_empty: bool = False) -> dict[str, Any]:
    if payload is None:
        if allow_empty:
            return {}
        raise GateEvidenceCoreError(f"{field_name} must not be empty")
    if isinstance(payload, Mapping):
        return dict(payload)
    if hasattr(payload, "to_dict") and callable(payload.to_dict):
        value = payload.to_dict()
        if not isinstance(value, Mapping):
            raise GateEvidenceCoreError(f"{field_name}.to_dict() must return a mapping")
        return dict(value)
    if is_dataclass(payload) and not isinstance(payload, type):
        return {field.name: getattr(payload, field.name) for field in fields(payload)}
    raise GateEvidenceCoreError(f"{field_name} must be a mapping, dataclass, or to_dict object")


def _checked_gate(**kwargs: Any) -> CanonicalGate:
    gate = CanonicalGate.create(
        redaction_flags=(RedactionFlag.SUMMARY_ONLY, RedactionFlag.RAW_PROVIDER_OUTPUT_OMITTED),
        **kwargs,
    )
    assert_redaction_safe(gate.to_dict())
    return gate


def _evidence(*, evidence_id: Any, summary: Any, source: Any) -> EvidenceItem:
    return EvidenceItem.create(
        evidence_id=evidence_id,
        summary=summary,
        source=source,
        redaction_flags=(RedactionFlag.SUMMARY_ONLY, RedactionFlag.RAW_PROVIDER_OUTPUT_OMITTED),
    )


def _status_from_payload(
    value: Any,
    *,
    ready: bool | None,
    live_required: bool,
    operator_required: bool,
) -> GateStatus:
    normalized = _token(value)
    if normalized in _GO_STATUSES:
        return GateStatus.GO
    if normalized in _PARTIAL_STATUSES:
        return GateStatus.PARTIAL
    if normalized in _NO_GO_STATUSES:
        return GateStatus.NO_GO
    if normalized in _BLOCKED_STATUSES:
        return GateStatus.BLOCKED
    if normalized in _DEFERRED_STATUSES:
        if live_required or operator_required:
            return GateStatus.BLOCKED
        return GateStatus.DEFERRED
    if ready is True:
        return GateStatus.GO
    if ready is False:
        return GateStatus.BLOCKED if live_required or operator_required else GateStatus.NO_GO
    return GateStatus.DEFERRED


def _next_action(
    status: GateStatus,
    *,
    live_required: bool = False,
    operator_required: bool = False,
    summary: str = "",
) -> NextAction:
    if live_required:
        return NextAction.create(
            action_type=NextActionType.REQUEST_LIVE_GO,
            summary=summary or "request bounded live approval before execution",
        )
    if operator_required:
        return NextAction.create(
            action_type=NextActionType.REQUEST_OPERATOR_DECISION,
            summary=summary or "request operator decision before proceeding",
        )
    if summary:
        action_type = NextActionType.FIX_BLOCKER if status in {GateStatus.BLOCKED, GateStatus.NO_GO} else NextActionType.COLLECT_EVIDENCE
        return NextAction.create(action_type=action_type, summary=summary)
    if status == GateStatus.GO:
        return NextAction.create(action_type=NextActionType.PROCEED, summary="proceed with scoped non-live handoff")
    if status == GateStatus.PARTIAL:
        return NextAction.create(action_type=NextActionType.COLLECT_EVIDENCE, summary="collect remaining summarized evidence")
    if status == GateStatus.DEFERRED:
        return NextAction.create(action_type=NextActionType.DEFER, summary="defer until required evidence is available")
    return NextAction.create(action_type=NextActionType.FIX_BLOCKER, summary="resolve blocking gate before proceeding")


def _release_summary(data: Mapping[str, Any], release: Mapping[str, Any], status: GateStatus) -> str:
    blocking_count = len(_texts(data.get("blocking_gate_ids")))
    partial_count = len(_texts(data.get("partial_gate_ids")))
    next_actions = len(_texts(data.get("next_actions")))
    release_ready = _first_bool(
        data,
        release,
        keys=("external_release_ready", "external_release_allowed", "version_1_0_ready", "ready", "ok"),
    )
    return (
        f"Release readiness status {status.value}; "
        f"external_release_ready={_display_bool(release_ready)}; "
        f"blocking_gates={blocking_count}; partial_gates={partial_count}; next_actions={next_actions}."
    )


def _live_summary(action: Mapping[str, Any], blocked_live_actions: tuple[str, ...]) -> str:
    gate_count = len(tuple(action.get("gates") or ()))
    gap_count = len(_texts(action.get("readiness_gap_names")))
    label = _first_text(action, keys=("label", "summary", "action_id")) or "live affordance"
    return (
        f"{label} readiness summarized; gates={gate_count}; gaps={gap_count}; "
        f"blocked_live_actions={len(blocked_live_actions)}."
    )


def _release_blockers(data: Mapping[str, Any], release: Mapping[str, Any]) -> tuple[str, ...]:
    blockers = [
        *(f"blocking gate: {item}" for item in _texts(data.get("blocking_gate_ids"))),
        *_texts(data.get("missing_evidence")),
        *_texts(data.get("gaps")),
    ]
    if release and _first_bool(release, keys=("external_release_allowed", "tag_allowed", "deploy_allowed")) is False:
        blockers.append("release action is not currently allowed")
    decision = _first_text(data, keys=("next_human_decision", "decision"))
    if decision and blockers:
        blockers.append(decision)
    return _dedupe(blockers)


def _live_blockers(
    action: Mapping[str, Any],
    blocked_live_actions: tuple[str, ...],
    live_required: bool,
    operator_required: bool,
) -> tuple[str, ...]:
    blockers = [*(f"readiness gap: {item}" for item in _texts(action.get("readiness_gap_names")))]
    if live_required:
        blockers.append("bounded live approval is required")
    if operator_required:
        blockers.append("operator review is required")
    if blocked_live_actions:
        blockers.append(f"{len(blocked_live_actions)} live action(s) remain blocked")
    return _dedupe(blockers)


def _quality_blockers(status: GateStatus, block_reason: str, next_action: str) -> tuple[str, ...]:
    if status in {GateStatus.BLOCKED, GateStatus.NO_GO}:
        return (block_reason or next_action or f"quality gate status is {status.value}",)
    if status in {GateStatus.DEFERRED, GateStatus.PARTIAL} and (block_reason or next_action):
        return (block_reason or next_action,)
    return ()


def _review_status(state: str, *, review_required: bool) -> GateStatus:
    if state in {"no_pending", "done", "clear"}:
        return GateStatus.GO
    if state == "blocked":
        return GateStatus.BLOCKED
    if state in {"pending_review", "ready_to_write", "ready_to_execute"}:
        return GateStatus.BLOCKED if review_required else GateStatus.DEFERRED
    return GateStatus.DEFERRED


def _review_blockers(
    gate: Mapping[str, Any],
    state: str,
    review_required: bool,
    live_required: bool,
) -> tuple[str, ...]:
    blockers: list[str] = []
    reason = _first_text(gate, keys=("reason",))
    if reason and state not in {"no_pending", "done", "clear"}:
        blockers.append(reason)
    if review_required:
        blockers.append("operator review is required")
    if live_required:
        blockers.append("bounded live approval is required")
    return _dedupe(blockers)


def _safe_actions(data: Mapping[str, Any], *, blocked_actions: Iterable[str]) -> tuple[str, ...]:
    blocked = set(blocked_actions)
    explicit = _texts(data.get("safe_actions"))
    actions = [action for action in explicit if action not in blocked]
    for key in ("blocked_live_actions", "unsafe_actions"):
        blocked.update(_texts(data.get(key)))
    return tuple(action for action in actions if action not in blocked)


def _release_live_required(data: Mapping[str, Any], release: Mapping[str, Any]) -> bool:
    if _truthy(data.get("live_required")) or _truthy(data.get("live_go_required")):
        return True
    ui = _as_mapping(data.get("ui"), field_name="ui", allow_empty=True)
    if ui.get("required") is True and ui.get("live") is False:
        return True
    if release and release.get("deploy_allowed") is False:
        return True
    return False


def _quality_family(gate_type: str) -> GateFamily:
    if gate_type == "tests":
        return GateFamily.TESTS
    if gate_type in {"evidence", "manual"}:
        return GateFamily.EVIDENCE
    if gate_type in {"scope", "hot_file"}:
        return GateFamily.SCOPE
    return GateFamily.QUALITY


def _first_bool(*payloads: Mapping[str, Any], keys: Iterable[str]) -> bool | None:
    for payload in payloads:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, bool):
                return value
    return None


def _first_text(payload: Mapping[str, Any], *, keys: Iterable[str]) -> str:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        if isinstance(value, Mapping):
            value = value.get("summary") or value.get("status") or value.get("decision")
        text = " ".join(str(value or "").split())
        if text:
            assert_redaction_safe({"text": text})
            return text[:500]
    return ""


def _texts(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = (value,)
    elif isinstance(value, Mapping):
        items = value.values()
    elif isinstance(value, Iterable):
        items = value
    else:
        items = (value,)
    result: list[str] = []
    for item in items:
        text = " ".join(str(item or "").split())
        if not text:
            continue
        assert_redaction_safe({"text": text})
        result.append(text[:500])
    return _dedupe(result)


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = " ".join(str(value or "").split())
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return tuple(result)


def _source(data: Mapping[str, Any], *, fallback: str) -> str:
    schema = _first_text(data, keys=("schema", "source"))
    return schema or fallback


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return _token(value) in _TRUE_VALUES


def _contains_no_go(value: str) -> bool:
    normalized = _token(value)
    return "no_go" in normalized or normalized.startswith("external_no_go")


def _display_bool(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return "unknown"


def _token(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = _TOKEN_RE.sub("_", text).strip("_")
    return re.sub(r"_{2,}", "_", text)
