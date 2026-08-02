"""Build Memory/RaptorGraph write intents from coding-agent evidence."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

from src.coding_context_envelope import CodingContextCheckpoint, CodingContextEnvelope
from src.coding_project_scope import CodingPlanningBinding, CodingProjectScopeError
from src.memory_candidate_schema import build_memory_candidates_from_synthesis
from src.memory_write_policy import decide_memory_write_policy
from src.raptorgraph_candidate_mapping import map_memory_candidates_to_raptorgraph
from src.runtime_event_envelope import stable_payload_hash


MEMORY_CHECKPOINT_RECEIPT_SCHEMA = "odysseus.coding_agent.memory_checkpoint_receipt.v1"
_CHECKPOINT_NAMES = {
    CodingContextCheckpoint.PRE_PLAN: "planning_intake",
    CodingContextCheckpoint.PRE_SLICE: "pre_edit",
    CodingContextCheckpoint.FAILURE_RETRIEVAL: "failure_retrieval",
    CodingContextCheckpoint.POST_ACCEPTANCE_WRITEBACK: "post_acceptance_writeback",
}
_RETRIEVAL_STATUSES = {
    "available",
    "unavailable",
    "stale",
    "conflicting",
    "low_confidence",
}
_USER_ACCEPTANCE_STATES = {"not_applicable", "accepted", "waiting", "rejected"}


class CodingAgentMemoryBridgeError(ValueError):
    """Raised when coding-agent evidence is unsafe for memory candidates."""


def build_coding_agent_memory_checkpoint_receipt(
    *,
    envelope: CodingContextEnvelope,
    planning_binding: CodingPlanningBinding | Mapping[str, Any],
    retrieval_status: Any,
    candidate_reference_ids: tuple[str, ...] = (),
    unresolved_reference_count: Any = 0,
    dirty_diff_digest: Any = "",
    failed_lane_receipt_ids: tuple[str, ...] = (),
    rejected_patch_digest_or_none: Any = "none",
    required_machine_auto_receipt_ids: tuple[str, ...] = (),
    independent_agent_auto_receipt_id: Any = "",
    applicable_user_acceptance_state: Any = "not_applicable",
) -> dict[str, Any]:
    """Project a CAO-08B envelope into an advisory RAPTOR/GraphRAG receipt.

    The accepted CAO envelope remains the canonical graph boundary.  This
    adapter creates no graph write, lifecycle transition, gate closure, or
    scope authority; it only records bounded reference identifiers and the
    observable retrieval limitation for one of the four checkpoints.
    """

    if not isinstance(envelope, CodingContextEnvelope):
        raise CodingAgentMemoryBridgeError("checkpoint envelope must be typed")
    try:
        binding = CodingPlanningBinding.from_value(planning_binding)
    except CodingProjectScopeError as exc:
        raise CodingAgentMemoryBridgeError(f"Planning binding is invalid: {exc}") from exc
    if (
        envelope.planning_item_id != binding.planning_item_id
        or envelope.planning_revision != binding.canonical_plan_revision
    ):
        raise CodingAgentMemoryBridgeError("checkpoint envelope does not match validated Planning revision")
    if envelope.acceptance_criteria_id != binding.acceptance_contract:
        raise CodingAgentMemoryBridgeError("checkpoint envelope does not match Planning acceptance contract")
    scope_key = "normalized_claim_scope" if envelope.claim_id else "normalized_allowed_scope"
    expected_scope_digest = stable_payload_hash({scope_key: binding.allowed_paths})
    if envelope.scope_digest != expected_scope_digest:
        raise CodingAgentMemoryBridgeError("checkpoint envelope does not match Planning allowed scope")
    checkpoint = _CHECKPOINT_NAMES.get(envelope.checkpoint)
    if checkpoint is None:
        raise CodingAgentMemoryBridgeError("checkpoint is not supported")
    receipt_scope_key = (
        "normalized_allowed_scope"
        if checkpoint == "planning_intake"
        else "normalized_claim_scope"
    )
    receipt_scope_digest = stable_payload_hash(
        {receipt_scope_key: binding.allowed_paths}
    )
    status = _retrieval_status(retrieval_status)
    refs = _bounded_reference_ids(candidate_reference_ids, field="candidate_reference_ids")
    failed_ids = _bounded_reference_ids(failed_lane_receipt_ids, field="failed_lane_receipt_ids")
    unresolved = _bounded_count(unresolved_reference_count, field="unresolved_reference_count")
    user_state = _user_acceptance_state(applicable_user_acceptance_state)
    if checkpoint != "failure_retrieval" and (dirty_diff_digest or failed_ids or rejected_patch_digest_or_none != "none"):
        raise CodingAgentMemoryBridgeError("failure receipt fields are only valid at failure_retrieval")
    if checkpoint != "post_acceptance_writeback" and (
        required_machine_auto_receipt_ids or independent_agent_auto_receipt_id or user_state != "not_applicable"
    ):
        raise CodingAgentMemoryBridgeError("post-acceptance fields are only valid at post_acceptance_writeback")

    payload: dict[str, Any] = {
        "schema": MEMORY_CHECKPOINT_RECEIPT_SCHEMA,
        "checkpoint": checkpoint,
        "planning": {
            "planning_item_id": binding.planning_item_id,
            "canonical_plan_revision": binding.canonical_plan_revision,
            "binding_digest": binding.binding_digest,
            "acceptance_contract": binding.acceptance_contract,
            "allowed_paths_digest": stable_payload_hash(binding.allowed_paths),
            "gate_requirements": binding.gate_requirements,
        },
        "revision_binding": envelope.authority_digest,
        "scope_digest": receipt_scope_digest,
        "envelope_scope_digest": envelope.scope_digest,
        "memory_query_class": f"raptor_graphrag_{checkpoint}",
        "bounded_reference_ids": refs,
        "retrieval_status": status,
        "envelope_id": envelope.envelope_id,
        "envelope_disposition": envelope.disposition.value,
        "advisory_only": True,
        "authority_effect": "none",
        "gate_effect": "none",
        "execution_allowed": False,
        "write_allowed": False,
        "dispatch_allowed": False,
        "live_effect_allowed": False,
        "raw_content_visible": False,
    }
    if checkpoint == "planning_intake":
        payload.update(
            candidate_reference_ids=refs,
            planning_context_complete=envelope.disposition.value == "ready",
        )
    elif checkpoint == "pre_edit":
        payload.update(
            allowed_path_count=len(binding.allowed_paths),
            candidate_reference_ids=refs,
            unresolved_reference_count=unresolved,
            exact_read_required=True,
        )
    elif checkpoint == "failure_retrieval":
        payload.update(
            active_planning_envelope_digest=_canonical_digest(
                envelope.parent_envelope_id, field="active_planning_envelope_digest"
            ),
            dirty_diff_digest=_canonical_digest(dirty_diff_digest, field="dirty_diff_digest"),
            failed_lane_receipt_ids=failed_ids,
            rejected_patch_digest_or_none=_digest_or_none(rejected_patch_digest_or_none),
            bounded_failure_reference_ids=refs,
        )
    else:
        machine_ids = _bounded_reference_ids(
            required_machine_auto_receipt_ids, field="required_machine_auto_receipt_ids"
        )
        independent_id = _safe_label_or_empty(
            independent_agent_auto_receipt_id, field="independent_agent_auto_receipt_id"
        )
        intent_ids = {
            item.target_graph.value: item.intent_ref for item in envelope.post_acceptance_intents
        }
        payload.update(
            required_machine_auto_receipt_ids=machine_ids,
            independent_agent_auto_receipt_id=independent_id,
            applicable_user_acceptance_state=user_state,
            code_write_intent_id=intent_ids.get("code", "none"),
            causal_write_intent_id=intent_ids.get("causal", "none"),
            memory_write_intent_id=intent_ids.get("memory", "none"),
            provenance_reference_ids=refs,
            write_intent_ready=(
                envelope.disposition.value == "ready"
                and bool(machine_ids)
                and bool(independent_id)
                and user_state in {"not_applicable", "accepted"}
            ),
        )
    payload["receipt_id"] = stable_payload_hash(payload)
    _reject_unsafe(payload)
    return payload


def build_coding_agent_memory_write_intent(
    evidence: Mapping[str, Any],
    *,
    model: str,
    dsgvo_mode: bool = False,
    operator_auto_write_enabled: bool = False,
) -> dict[str, Any]:
    if not isinstance(evidence, Mapping):
        raise CodingAgentMemoryBridgeError("evidence must be a mapping")
    _reject_unsafe(evidence)
    title = _safe_text(evidence.get("title") or "Coding task result", field="title", max_len=120)
    summary = _safe_text(evidence.get("summary") or "Coding task completed with sandbox evidence.", field="summary", max_len=500)
    source_refs = _source_refs_from_evidence(evidence)
    synthesis = {
        "source_refs": source_refs,
        "confidence": evidence.get("confidence", 0.82),
        "topics": [{"name": title, "summary": summary}],
    }
    candidates = tuple(
        candidate.to_dict()
        for candidate in build_memory_candidates_from_synthesis(
            synthesis,
            model=model,
            created_by="coding_agent_sandbox_bridge",
            sensitivity=_safe_label(evidence.get("sensitivity") or "project", field="sensitivity"),
            recheck_hint="on_next_task",
        )
    )
    policy = decide_memory_write_policy(
        candidates,
        dsgvo_mode=dsgvo_mode,
        model_route="local_only" if dsgvo_mode else "api_or_local",
        operator_auto_write_enabled=operator_auto_write_enabled,
    )
    mapping = map_memory_candidates_to_raptorgraph(candidates, topic_namespace="coding_agent")
    return {
        "schema": "odysseus.coding_agent.memory_write_intent.v1",
        "candidates": candidates,
        "raptorgraph_mapping": mapping.to_dict(),
        "policy": policy.to_dict(),
        "raw_content_visible": False,
    }


def build_coding_agent_capability_memory_write_intent(
    knowledge: Mapping[str, Any],
    *,
    model: str,
    dsgvo_mode: bool = False,
    operator_auto_write_enabled: bool = False,
) -> dict[str, Any]:
    """Build memory intent for system capability knowledge, never private content."""

    from src.tool_capability_knowledge import coding_agent_capability_evidence

    evidence = coding_agent_capability_evidence(knowledge)
    return build_coding_agent_memory_write_intent(
        evidence,
        model=model,
        dsgvo_mode=dsgvo_mode,
        operator_auto_write_enabled=operator_auto_write_enabled,
    )


def _source_refs_from_evidence(evidence: Mapping[str, Any]) -> tuple[str, ...]:
    refs: list[str] = []
    for key in ("content_hash", "payload_hash", "evidence_hash"):
        value = str(evidence.get(key) or "").strip().lower()
        if re.fullmatch(r"sha256:[a-f0-9]{16,64}", value):
            refs.append(value)
    for artifact in evidence.get("artifacts") or ():
        if not isinstance(artifact, Mapping):
            continue
        value = str(artifact.get("content_hash") or "").strip().lower()
        if re.fullmatch(r"sha256:[a-f0-9]{16,64}", value):
            refs.append(value)
    if not refs:
        digest = hashlib.sha256(repr(sorted(evidence.items())).encode("utf-8", errors="replace")).hexdigest()
        refs.append("sha256:" + digest)
    return tuple(dict.fromkeys(refs))


def _retrieval_status(value: Any) -> str:
    status = str(value or "").strip().lower().replace("-", "_")
    if status not in _RETRIEVAL_STATUSES:
        raise CodingAgentMemoryBridgeError("retrieval_status is unsupported")
    return status


def _user_acceptance_state(value: Any) -> str:
    state = str(value or "").strip().lower().replace("-", "_")
    if state not in _USER_ACCEPTANCE_STATES:
        raise CodingAgentMemoryBridgeError("applicable_user_acceptance_state is unsupported")
    return state


def _bounded_reference_ids(values: tuple[str, ...], *, field: str) -> tuple[str, ...]:
    if not isinstance(values, tuple) or len(values) > 32:
        raise CodingAgentMemoryBridgeError(f"{field} must be a bounded tuple")
    normalized = tuple(sorted(dict.fromkeys(_safe_label(value, field=field) for value in values)))
    if len(normalized) != len(values):
        raise CodingAgentMemoryBridgeError(f"{field} contains duplicates")
    return normalized


def _safe_label_or_empty(value: Any, *, field: str) -> str:
    if value is None or str(value).strip() == "":
        return ""
    return _safe_label(value, field=field)


def _bounded_count(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise CodingAgentMemoryBridgeError(f"{field} must be an integer")
    try:
        count = int(value)
    except (TypeError, ValueError) as exc:
        raise CodingAgentMemoryBridgeError(f"{field} must be an integer") from exc
    if not 0 <= count <= 32:
        raise CodingAgentMemoryBridgeError(f"{field} exceeds bounds")
    return count


def _canonical_digest(value: Any, *, field: str) -> str:
    text = str(value or "").strip().lower()
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", text):
        raise CodingAgentMemoryBridgeError(f"{field} must be a canonical SHA-256 digest")
    return text


def _digest_or_none(value: Any) -> str:
    text = str(value or "none").strip().lower()
    if text == "none":
        return text
    return _canonical_digest(text, field="rejected_patch_digest_or_none")


def _safe_label(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:@/-]{1,80}", text):
        raise CodingAgentMemoryBridgeError(f"{field} is unsafe")
    return text


def _safe_text(value: Any, *, field: str, max_len: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        raise CodingAgentMemoryBridgeError(f"{field} must not be empty")
    lowered = text.lower()
    if any(marker in lowered for marker in ("authorization", "bearer ", "api_key", "password", "cookie", "private raw text")):
        raise CodingAgentMemoryBridgeError(f"{field} contains forbidden marker")
    if re.search(r"(^|['\"\\s])([a-z]:[\\/]|/home/|/opt/|/users/|~[\\/])", lowered):
        raise CodingAgentMemoryBridgeError(f"{field} contains host path")
    return text[:max_len]


def _reject_unsafe(value: Any) -> None:
    encoded = repr(value).lower()
    if any(marker in encoded for marker in ("authorization", "bearer ", "api_key", "password", "cookie", "private raw text")):
        raise CodingAgentMemoryBridgeError("evidence contains forbidden marker")
    if re.search(r"(^|['\"\\s])([a-z]:[\\/]|/home/|/opt/|/users/|~[\\/])", encoded):
        raise CodingAgentMemoryBridgeError("evidence contains host path")
    if "raw_content_visible': true" in encoded or '"raw_content_visible": true' in encoded:
        raise CodingAgentMemoryBridgeError("evidence exposes raw content")
