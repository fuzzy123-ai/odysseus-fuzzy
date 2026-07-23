"""Read-only Harbor One workspace snapshot projection.

This module composes already-bounded status payloads into a frontend-safe
`odysseus.workspace_snapshot.v1` read model. It does not perform live probes,
run tools, inspect private content, or mutate state.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Mapping


WORKSPACE_SNAPSHOT_SCHEMA = "odysseus.workspace_snapshot.v1"

SECTION_IDS = (
    "operator",
    "projects",
    "clarification",
    "planning",
    "coding",
    "sandbox",
    "knowledge",
    "local_model",
    "inbox",
    "release",
)

SECTION_STATES = {"live", "partial", "stale", "unavailable", "fixture"}
DEGRADED_SECTION_STATES = {"partial", "stale", "unavailable", "fixture"}
DEGRADED_STATUSES = {"blocked", "pending", "warn", "unknown", "partial", "unavailable", "fixture"}
_SAFE_TOKEN_RE = re.compile(r"[^a-z0-9._:-]+")
_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer|chat[_-]?id)\b\s*[:=]?\s*\S*")
_PRIVATE_PATH_RE = re.compile(r"(?i)([a-z]:[\\/]|/home/|/users/|/opt/|\\\\)")
_SENSITIVE_KEY_PARTS = (
    "secret",
    "token",
    "password",
    "credential",
    "chat_id",
    "raw",
    "content",
    "body",
    "prompt",
    "reply",
    "path",
    "url",
    "webdav",
    "command",
    "env",
    "filename",
    "file_name",
)


def build_workspace_snapshot(
    *,
    operator_status: Mapping[str, Any] | None = None,
    projects_status: Mapping[str, Any] | None = None,
    clarification_status: Mapping[str, Any] | None = None,
    planning_status: Mapping[str, Any] | None = None,
    coding_status: Mapping[str, Any] | None = None,
    sandbox_status: Mapping[str, Any] | None = None,
    knowledge_status: Mapping[str, Any] | None = None,
    local_model_status: Mapping[str, Any] | None = None,
    inbox_status: Mapping[str, Any] | None = None,
    release_status: Mapping[str, Any] | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build one bounded frontend read model from optional section payloads."""

    section_inputs = {
        "operator": operator_status,
        "projects": projects_status,
        "clarification": clarification_status,
        "planning": planning_status,
        "coding": coding_status,
        "sandbox": sandbox_status,
        "knowledge": knowledge_status,
        "local_model": local_model_status,
        "inbox": inbox_status,
        "release": release_status,
    }
    sections = tuple(_section(section_id, _mapping(section_inputs.get(section_id))) for section_id in SECTION_IDS)
    counts = _counts(sections)
    status = _overall_status(sections)
    payload = {
        "schema": WORKSPACE_SNAPSHOT_SCHEMA,
        "snapshot_id": _hash({"sections": [(s["id"], s["state"], s["status"]) for s in sections], "counts": counts}),
        "generated_at": generated_at or _now_iso(),
        "status": status,
        "sections": sections,
        "counts": counts,
        "next_actions": _next_actions(sections),
        "controls": _controls(),
        "raw_content_visible": False,
        "private_content_visible": False,
        "path_values_visible": False,
        "url_values_visible": False,
        "command_values_visible": False,
        "env_values_visible": False,
        "token_value_visible": False,
        "chat_id_value_visible": False,
        "live_probe_performed": False,
        "live_mutation_performed": False,
        "write_action_available": False,
    }
    _reject_unsafe_payload(payload)
    return payload


def _section(section_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    state = _state(payload)
    status = _status(payload, state=state)
    degraded = _degraded(state=state, status=status)
    pending = _safe_count(payload.get("pending_count") or payload.get("unresolved_required_count"))
    blocked = _safe_count(payload.get("blocked_count") or payload.get("blocker_count"))
    item_count = _safe_count(payload.get("item_count") or payload.get("total_count") or payload.get("count"))
    if not item_count and isinstance(payload.get("items"), list):
        item_count = len(payload["items"])
    if not item_count and isinstance(payload.get("sections"), list):
        item_count = len(payload["sections"])
    return {
        "id": section_id,
        "schema": _safe_schema(payload.get("schema")),
        "state": state,
        "status": status,
        "available": state != "unavailable",
        "degraded": degraded,
        "degrade_reason": _degrade_reason(section_id, payload, state=state, status=status, degraded=degraded),
        "frontend_hint": _frontend_hint(state=state, status=status),
        "freshness": _freshness(payload, state),
        "updated_at": _safe_timestamp(payload.get("updated_at") or payload.get("last_updated_at") or payload.get("generated_at")),
        "source_ref": _source_ref(section_id, payload),
        "reason_unavailable": _safe_summary(payload.get("reason_unavailable") or payload.get("reason") or ""),
        "summary": _summary(section_id, payload, state=state, status=status),
        "item_count": item_count,
        "pending_count": pending,
        "blocked_count": blocked,
        "lifecycle_cards": _lifecycle_cards(section_id, payload, state=state, status=status),
        "status_details": _status_details(section_id, payload),
        "action": _action(section_id, state=state, status=status, pending=pending, blocked=blocked),
        "raw_content_visible": False,
        "private_content_visible": False,
        "path_values_visible": False,
        "url_values_visible": False,
        "command_values_visible": False,
        "token_value_visible": False,
        "chat_id_value_visible": False,
    }


def _state(payload: Mapping[str, Any]) -> str:
    raw = _safe_token(payload.get("state") or payload.get("snapshot_state") or payload.get("origin") or "")
    if raw in {"synthetic_fixture", "fallback_fixture", "demo_only", "fixture"}:
        return "fixture"
    if raw in SECTION_STATES:
        return raw
    if not payload:
        return "unavailable"
    if bool(payload.get("stale")):
        return "stale"
    if bool(payload.get("partial")) or bool(payload.get("provider_failed")):
        return "partial"
    return "live"


def _freshness(payload: Mapping[str, Any], state: str) -> str:
    explicit = _safe_token(payload.get("freshness") or payload.get("freshness_state") or "")
    if explicit in {"current", "partial", "stale", "missing", "synthetic", "unknown"}:
        return explicit
    if state == "fixture":
        return "synthetic"
    if state == "unavailable":
        return "missing"
    if state == "stale":
        return "stale"
    if state == "partial" or bool(payload.get("partial")):
        return "partial"
    return "current"


def _degraded(*, state: str, status: str) -> bool:
    return state in DEGRADED_SECTION_STATES or status in DEGRADED_STATUSES


def _degrade_reason(section_id: str, payload: Mapping[str, Any], *, state: str, status: str, degraded: bool) -> str:
    if not degraded:
        return ""
    explicit = _safe_summary(
        payload.get("degrade_reason")
        or payload.get("reason_unavailable")
        or payload.get("reason")
        or payload.get("freshness_reason")
        or payload.get("stale_reason")
        or ""
    )
    if explicit:
        return explicit
    if state == "fixture":
        return "synthetic fixture data; runtime source not connected"
    if state == "unavailable":
        return f"{section_id} snapshot source is unavailable"
    if state == "stale":
        return f"{section_id} snapshot is stale"
    if state == "partial":
        return f"{section_id} snapshot is partial"
    if status == "blocked":
        return f"{section_id} has blockers"
    return f"{section_id} needs review"


def _frontend_hint(*, state: str, status: str) -> str:
    if state == "fixture":
        return "render_fixture_fallback"
    if state == "unavailable":
        return "render_unavailable"
    if state == "stale":
        return "render_stale"
    if state == "partial" or status in {"pending", "warn", "unknown", "partial"}:
        return "render_attention"
    if status == "blocked":
        return "render_blocked"
    return "render_live"


def _status(payload: Mapping[str, Any], *, state: str) -> str:
    if state == "fixture":
        return "fixture"
    if state == "unavailable":
        return "unavailable"
    token = _safe_token(payload.get("status") or payload.get("plan_status") or payload.get("phase") or "")
    blocked = _safe_count(payload.get("blocked_count") or payload.get("blocker_count"))
    pending = _safe_count(payload.get("pending_count") or payload.get("unresolved_required_count"))
    if blocked:
        return "blocked"
    if pending:
        return "pending"
    if token in {"ok", "go", "ready", "clear", "healthy", "done", "available", "live"}:
        return "ok"
    if token in {"blocked", "failed", "error", "critical"}:
        return "blocked"
    if token in {"pending", "waiting", "review", "needs_review", "partial", "warn", "warning"}:
        return "pending" if token != "warn" else "warn"
    return token or ("partial" if state == "partial" else "unknown")


def _source_ref(section_id: str, payload: Mapping[str, Any]) -> str:
    explicit = _safe_ref(payload.get("source_ref"))
    if explicit:
        return explicit
    schema = _safe_schema(payload.get("schema"))
    if schema:
        return f"{section_id}:{schema}"
    return f"{section_id}:unavailable"


def _summary(section_id: str, payload: Mapping[str, Any], *, state: str, status: str) -> str:
    explicit = _safe_summary(payload.get("summary") or payload.get("title") or "")
    if explicit:
        return explicit
    if state == "fixture":
        return f"{section_id} is using synthetic fixture data"
    if state == "unavailable":
        return f"{section_id} unavailable"
    return f"{section_id} {status}"


def _action(section_id: str, *, state: str, status: str, pending: int, blocked: int) -> str:
    if state == "fixture":
        return "replace_fixture_with_snapshot"
    if state == "unavailable":
        return "connect_snapshot_source"
    if blocked:
        return f"inspect_{section_id}_blockers"
    if pending or status in {"pending", "warn", "unknown", "partial"}:
        return f"review_{section_id}"
    return "none"


def _lifecycle_cards(section_id: str, payload: Mapping[str, Any], *, state: str, status: str) -> tuple[dict[str, Any], ...]:
    if section_id != "coding":
        return ()
    runner_phase = _safe_token(payload.get("phase") or payload.get("runner_phase") or "planned") or "planned"
    gates_waiting = _safe_token_list(payload.get("gates_waiting") or payload.get("waiting_gates") or ())
    quality_gate = _mapping(payload.get("quality_gate"))
    done_gate = _mapping(payload.get("done_gate"))
    publish_gate = _mapping(payload.get("publish_gate"))
    checks = _mapping(payload.get("checks") or payload.get("check_gate"))
    sandbox_dispatch = _mapping(payload.get("sandbox_dispatch") or payload.get("sandbox"))
    clarification_gate = _mapping(payload.get("clarification_gate"))
    understanding_review = _mapping(payload.get("understanding_review"))
    project_scope = _mapping(payload.get("project_scope"))

    return (
        _card(
            "clarification_gate",
            "Clarification gate",
            _gate_status(
                clarification_gate.get("status"),
                pending=_safe_count(
                    clarification_gate.get("unresolved_required_count")
                    or payload.get("unresolved_required_count")
                    or payload.get("pending_clarification_count")
                ),
            ),
            clarification_gate.get("summary") or _clarification_summary(payload),
            evidence_ref=clarification_gate.get("source_ref") or "coding:clarification",
        ),
        _card(
            "understanding_review",
            "Understanding review",
            _gate_status(understanding_review.get("status") or payload.get("understanding_status")),
            understanding_review.get("summary") or "User-visible understanding must be confirmed before planning.",
            evidence_ref=understanding_review.get("source_ref") or "coding:understanding_review",
        ),
        _card(
            "project_scope",
            "Project scope",
            _project_scope_status(project_scope, payload),
            project_scope.get("summary") or _project_scope_summary(payload),
            evidence_ref=project_scope.get("source_ref") or "coding:project_scope",
        ),
        _card(
            "runner_phase",
            "Runner phase",
            _runner_phase_status(runner_phase, state=state, status=status),
            f"Runner phase: {runner_phase}",
            evidence_ref=payload.get("source_ref") or "coding:runner_state",
            progress_percent=_safe_percent(payload.get("progress_percent")),
        ),
        _card(
            "worktree_ref",
            "Worktree ref",
            _ref_status(payload.get("worktree_ref") or project_scope.get("worktree_ref")),
            _worktree_summary(payload, project_scope),
            evidence_ref=payload.get("worktree_ref") or project_scope.get("worktree_ref") or "coding:worktree_ref",
        ),
        _card(
            "checks",
            "Checks",
            _gate_status(checks.get("status") or payload.get("checks_status")),
            checks.get("summary") or _checks_summary(payload),
            evidence_ref=checks.get("source_ref") or "coding:checks",
        ),
        _card(
            "sandbox_dispatch",
            "Sandbox dispatch",
            _gate_status(sandbox_dispatch.get("status") or payload.get("sandbox_status")),
            sandbox_dispatch.get("summary") or "Sandbox execution remains policy gated and evidence based.",
            evidence_ref=sandbox_dispatch.get("source_ref") or "coding:sandbox_dispatch",
        ),
        _card(
            "quality_gate",
            "Quality gate",
            _quality_gate_status(quality_gate, payload),
            quality_gate.get("summary") or _quality_gate_summary(quality_gate),
            evidence_ref=quality_gate.get("source_ref") or "coding:quality_gate",
        ),
        _card(
            "done_gate",
            "Done gate",
            _done_gate_status(done_gate, payload),
            done_gate.get("summary") or "Completion requires review evidence and a satisfied done gate.",
            evidence_ref=done_gate.get("source_ref") or "coding:done_gate",
        ),
        _card(
            "publish_gate",
            "Publish gate",
            _publish_gate_status(publish_gate, gates_waiting),
            publish_gate.get("summary") or "Publish remains blocked until explicit operator and release gates pass.",
            evidence_ref=publish_gate.get("source_ref") or "coding:publish_gate",
            requires_operator_go=True,
        ),
    )


def _card(
    card_id: str,
    label: str,
    status: str,
    summary: Any,
    *,
    evidence_ref: Any,
    progress_percent: int | None = None,
    requires_operator_go: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": card_id,
        "label": label,
        "status": _safe_card_status(status),
        "summary": _safe_summary(summary),
        "evidence_ref": _safe_ref(evidence_ref),
        "requires_operator_go": bool(requires_operator_go),
        "write_action_enabled": False,
        "raw_content_visible": False,
    }
    if progress_percent is not None:
        payload["progress_percent"] = progress_percent
    return payload


def _safe_card_status(value: Any) -> str:
    token = _safe_token(value)
    if token in {"ok", "pending", "blocked", "warn", "unknown", "not_started", "running", "ready", "done"}:
        return token
    if token in {"failed", "error", "critical"}:
        return "blocked"
    if token in {"waiting", "review", "needs_review", "partial"}:
        return "pending"
    if token in {"clear", "healthy", "available", "verified", "satisfied"}:
        return "ok"
    return "unknown"


def _gate_status(value: Any, *, pending: int = 0) -> str:
    if pending:
        return "pending"
    return _safe_card_status(value or "unknown")


def _project_scope_status(project_scope: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    explicit = _safe_card_status(project_scope.get("status") or payload.get("scope_status"))
    if explicit != "unknown":
        return explicit
    if payload.get("repo_id") or project_scope.get("repo_id"):
        return "ok"
    return "pending"


def _runner_phase_status(phase: str, *, state: str, status: str) -> str:
    if state == "unavailable":
        return "unknown"
    if status == "blocked" or phase in {"blocked", "failed"}:
        return "blocked"
    if phase in {"done"}:
        return "done"
    if phase in {"checks_running"}:
        return "running"
    if phase in {"review_ready", "publish_ready"}:
        return "ready"
    if phase in {"planned", "scoped", "worktree_ready"}:
        return "pending"
    return "unknown"


def _ref_status(value: Any) -> str:
    return "ok" if _safe_ref(value) else "pending"


def _quality_gate_status(quality_gate: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    explicit = _safe_card_status(quality_gate.get("status") or payload.get("quality_status"))
    if explicit != "unknown":
        return explicit
    if bool(quality_gate.get("verified")):
        return "ok"
    if _safe_count(quality_gate.get("blocker_count")) or quality_gate.get("blockers"):
        return "blocked"
    return "pending"


def _done_gate_status(done_gate: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    explicit = _safe_card_status(done_gate.get("status") or payload.get("done_status"))
    if explicit != "unknown":
        return explicit
    return "done" if bool(done_gate.get("satisfied") or payload.get("done")) else "pending"


def _publish_gate_status(publish_gate: Mapping[str, Any], gates_waiting: tuple[str, ...]) -> str:
    explicit = _safe_card_status(publish_gate.get("status"))
    if explicit != "unknown":
        return explicit
    if any("publish" in gate or "operator" in gate or "release" in gate for gate in gates_waiting):
        return "pending"
    return "pending"


def _clarification_summary(payload: Mapping[str, Any]) -> str:
    pending = _safe_count(payload.get("unresolved_required_count") or payload.get("pending_clarification_count"))
    if pending:
        return f"{pending} required clarification answer(s) pending before planning."
    return "No required clarification blocker reported."


def _project_scope_summary(payload: Mapping[str, Any]) -> str:
    repo_id = _safe_ref(payload.get("repo_id"))
    task_id = _safe_ref(payload.get("task_id"))
    if repo_id and task_id:
        return f"Scoped task {task_id} for repo {repo_id}."
    if repo_id:
        return f"Repo scope available: {repo_id}."
    return "Project scope is not fully connected."


def _worktree_summary(payload: Mapping[str, Any], project_scope: Mapping[str, Any]) -> str:
    ref = _safe_ref(payload.get("worktree_ref") or project_scope.get("worktree_ref"))
    if ref:
        return f"Worktree reference available: {ref}."
    return "Worktree reference is not available in the snapshot."


def _checks_summary(payload: Mapping[str, Any]) -> str:
    count = _safe_count(payload.get("check_count") or payload.get("checks_count"))
    if count:
        return f"{count} check(s) reported for this coding task."
    return "No check evidence is attached yet."


def _quality_gate_summary(quality_gate: Mapping[str, Any]) -> str:
    if bool(quality_gate.get("verified")):
        return "Quality gate verified by bounded evidence."
    blockers = _safe_count(quality_gate.get("blocker_count"))
    if blockers:
        return f"{blockers} quality blocker(s) reported."
    return "Quality gate is waiting for evidence."


def _safe_token_list(value: Any) -> tuple[str, ...]:
    items = value if isinstance(value, (list, tuple, set)) else ()
    return tuple(token for token in (_safe_token(item) for item in items) if token)


def _safe_summary_list(value: Any) -> tuple[str, ...]:
    items = value if isinstance(value, (list, tuple, set)) else ()
    return tuple(summary for summary in (_safe_summary(item) for item in items) if summary)


def _safe_percent(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(parsed, 100))


def _safe_float(value: Any) -> float:
    try:
        return max(0.0, round(float(value or 0.0), 6))
    except (TypeError, ValueError):
        return 0.0


def _status_details(section_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if section_id == "knowledge":
        return _knowledge_status_details(payload)
    if section_id == "planning":
        return _planning_status_details(payload)
    if section_id != "local_model":
        return {}
    queue = _mapping(payload.get("queue"))
    foreground = _mapping(payload.get("foreground"))
    maintenance = _mapping(payload.get("maintenance_guard"))
    benchmark = _mapping(payload.get("benchmark_summary"))
    return {
        "required_model": _safe_summary(payload.get("required_model")),
        "warm_model_status": _safe_token(payload.get("warm_model_status")),
        "known_cpu_constraint": _safe_token(payload.get("known_cpu_constraint")),
        "queue": {
            "active": _safe_count(queue.get("active")),
            "active_foreground": _safe_count(queue.get("active_foreground")),
            "waiting_foreground": _safe_count(queue.get("waiting_foreground")),
            "max_concurrency": max(1, _safe_count(queue.get("max_concurrency"))),
        },
        "foreground": {
            "active": bool(foreground.get("active")),
            "model": _safe_summary(foreground.get("model")),
            "reason": _safe_token(foreground.get("reason")),
        },
        "maintenance_guard": {
            "preflight_status": _safe_token(maintenance.get("preflight_status") or "unknown"),
            "priority_class": _safe_token(maintenance.get("priority_class")),
            "required_model": _safe_summary(maintenance.get("required_model")),
            "wait_timeout_seconds": _safe_count(maintenance.get("wait_timeout_seconds")),
            "command_timeout_seconds": _safe_count(maintenance.get("command_timeout_seconds")),
            "failure_count": _safe_count(maintenance.get("failure_count")),
            "warning_count": _safe_count(maintenance.get("warning_count")),
            "executes": False,
        },
        "benchmark_summary": {
            "model": _safe_summary(benchmark.get("model")),
            "latency_seconds": _safe_float(benchmark.get("latency_seconds")),
            "tokens": _safe_count(benchmark.get("tokens")),
            "tokens_per_second": _safe_float(benchmark.get("tokens_per_second")),
            "result": _safe_token(benchmark.get("result")),
        },
        "raw_content_visible": False,
    }


def _knowledge_status_details(payload: Mapping[str, Any]) -> dict[str, Any]:
    memory_stats = _mapping(payload.get("memory_stats") or payload.get("stats"))
    graph = _mapping(payload.get("graph") or payload.get("graph_summary"))
    provenance = _mapping(payload.get("provenance") or payload.get("provenance_summary"))
    evidence_packets = _safe_evidence_packets(payload.get("evidence_packets") or payload.get("evidence") or ())
    return {
        "redaction_state": _safe_token(payload.get("redaction_state") or "redacted"),
        "memory_stats": {
            "personal_memory_entries": _safe_count(memory_stats.get("personal_memory_entries")),
            "vector_index_count": _safe_count(memory_stats.get("vector_index_count")),
            "vector_index_healthy": bool(memory_stats.get("vector_index_healthy")),
            "rag_document_count": _safe_count(memory_stats.get("rag_document_count")),
        },
        "graph": {
            "node_budget": _safe_count(graph.get("node_budget") or payload.get("graph_node_budget")),
            "node_count": _safe_count(graph.get("node_count") or payload.get("graph_node_count")),
            "edge_count": _safe_count(graph.get("edge_count") or payload.get("graph_edge_count")),
            "stale_count": _safe_count(graph.get("stale_count") or payload.get("stale_count")),
            "partial": bool(graph.get("partial") or payload.get("partial")),
        },
        "provenance": {
            "event_count": _safe_count(provenance.get("event_count") or provenance.get("count")),
            "latest_event_type": _safe_token(provenance.get("latest_event_type")),
            "source_ref": _safe_ref(provenance.get("source_ref")),
        },
        "evidence_packets": evidence_packets,
        "evidence_packet_count": len(evidence_packets),
        "raw_content_visible": False,
    }


def _planning_status_details(payload: Mapping[str, Any]) -> dict[str, Any]:
    roadmaps = _mapping(payload.get("roadmaps") or payload.get("roadmap_summary"))
    gates = _mapping(payload.get("gates") or payload.get("gate_summary"))
    proposals = _mapping(payload.get("proposals") or payload.get("proposal_summary"))
    context_pack = _mapping(payload.get("context_pack") or payload.get("context_pack_summary"))
    apply_gate = _mapping(payload.get("apply_gate") or payload.get("apply_gate_summary"))
    return {
        "roadmap_count": _safe_count(roadmaps.get("count") or payload.get("roadmap_count")),
        "roadmap_ids": _safe_ref_list(roadmaps.get("ids") or payload.get("roadmap_ids"), limit=8),
        "gate_count": _safe_count(gates.get("count") or payload.get("gate_count")),
        "open_gate_count": _safe_count(gates.get("open_count") or payload.get("open_gate_count")),
        "proposal_count": _safe_count(proposals.get("count") or payload.get("proposal_count")),
        "proposal_status": _safe_token(proposals.get("status") or payload.get("proposal_status")),
        "context_pack_available": bool(context_pack.get("available") or payload.get("context_pack_available")),
        "context_pack_source_ref": _safe_ref(context_pack.get("source_ref") or payload.get("context_pack_source_ref")),
        "apply_gate_status": _safe_token(apply_gate.get("status") or payload.get("apply_gate_status") or "policy_gated"),
        "current_source_ref": _safe_ref(payload.get("current_source_ref") or payload.get("source_ref")),
        "writes_supported": False,
        "raw_content_visible": False,
    }


def _safe_evidence_packets(value: Any) -> tuple[dict[str, str], ...]:
    items = value if isinstance(value, (list, tuple)) else ()
    packets: list[dict[str, str]] = []
    for item in items[:5]:
        packet = _mapping(item)
        packets.append(
            {
                "evidence_ref": _safe_ref(packet.get("evidence_ref") or packet.get("source_ref")),
                "status": _safe_token(packet.get("status")),
                "summary": _safe_summary(packet.get("summary")),
            }
        )
    return tuple(packets)


def _safe_ref_list(value: Any, *, limit: int) -> tuple[str, ...]:
    items = value if isinstance(value, (list, tuple, set)) else ()
    refs: list[str] = []
    for item in items:
        ref = _safe_ref(item)
        if ref:
            refs.append(ref)
        if len(refs) >= limit:
            break
    return tuple(refs)


def _counts(sections: tuple[dict[str, Any], ...]) -> dict[str, int]:
    return {
        "section_count": len(sections),
        "live_count": sum(1 for section in sections if section["state"] == "live"),
        "partial_count": sum(1 for section in sections if section["state"] == "partial"),
        "stale_count": sum(1 for section in sections if section["state"] == "stale"),
        "fixture_count": sum(1 for section in sections if section["state"] == "fixture"),
        "unavailable_count": sum(1 for section in sections if section["state"] == "unavailable"),
        "pending_count": sum(_safe_count(section.get("pending_count")) for section in sections),
        "blocked_count": sum(_safe_count(section.get("blocked_count")) for section in sections),
    }


def _overall_status(sections: tuple[dict[str, Any], ...]) -> str:
    if any(section["status"] == "blocked" for section in sections):
        return "blocked"
    if any(section["status"] in {"pending", "warn"} for section in sections):
        return "attention"
    if any(section["state"] in {"partial", "stale", "fixture", "unavailable"} for section in sections):
        return "partial"
    return "ok"


def _next_actions(sections: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    actions: list[dict[str, Any]] = []
    for section in sections:
        action = section.get("action")
        if not action or action == "none":
            continue
        actions.append(
            {
                "rank": len(actions) + 1,
                "section_id": section["id"],
                "action": action,
                "mode": "read_only",
                "requires_live_go": False,
                "write_action_enabled": False,
            }
        )
    return tuple(actions)


def _controls() -> dict[str, dict[str, str]]:
    return {
        "refresh": {"state": "read_only", "reason": "snapshot refresh does not mutate runtime state"},
        "plan": {"state": "policy_gated", "reason": "requires clarification plan gate"},
        "execute": {"state": "policy_gated", "reason": "requires bounded operator/live gate"},
        "publish": {"state": "policy_gated", "reason": "requires release and UI-live gates"},
    }


def _mapping(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _safe_token(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = _SAFE_TOKEN_RE.sub("_", text).strip("_")
    return text[:80]


def _safe_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _safe_timestamp(value: Any) -> str:
    text = str(value or "").strip()
    if not text or _SECRET_RE.search(text) or _PRIVATE_PATH_RE.search(text):
        return ""
    return text[:80]


def _safe_schema(value: Any) -> str:
    text = str(value or "").strip()
    return text[:120] if text.startswith("odysseus.") and not _unsafe_text(text) else ""


def _safe_ref(value: Any) -> str:
    text = str(value or "").strip()
    if not text or _unsafe_text(text) or _PRIVATE_PATH_RE.search(text):
        return ""
    return re.sub(r"[^A-Za-z0-9_.:@/-]+", "_", text)[:160]


def _safe_summary(value: Any) -> str:
    text = " ".join(str(value or "").strip().split())
    if _unsafe_text(text) or _PRIVATE_PATH_RE.search(text):
        return ""
    return text[:220]


def _unsafe_text(value: Any) -> bool:
    text = str(value or "")
    return bool(_SECRET_RE.search(text))


def _reject_unsafe_payload(payload: Mapping[str, Any]) -> None:
    encoded = repr(payload)
    if _SECRET_RE.search(encoded):
        raise ValueError("workspace snapshot contains secret material")
    if _PRIVATE_PATH_RE.search(encoded):
        raise ValueError("workspace snapshot contains private path material")


def _hash(value: Any) -> str:
    return hashlib.sha256(repr(value).encode("utf-8", errors="replace")).hexdigest()[:24]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
