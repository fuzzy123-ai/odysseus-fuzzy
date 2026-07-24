"""Plan-only rollout and independent live-gate packet for Telegram Todo truth."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping


TELEGRAM_TODO_ROLLOUT_PACKET_SCHEMA = "odysseus.telegram_todo_rollout_packet.v1"
TELEGRAM_TODO_LIVE_GATES = (
    "TTD-LIVE-DEPLOY",
    "TTD-LIVE-DATA-REPAIR",
    "TTD-LIVE-TELEGRAM-SMOKE",
    "TTD-LIVE-ROLLOVER-SMOKE",
)

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_EVIDENCE_REF_RE = re.compile(r"^[a-z][a-z0-9_-]{2,47}:v1:[0-9a-f]{12,64}$")
_ALLOWED_EVIDENCE_KEYS = {
    "backup_preupdate",
    "data_backup",
    "deploy_readback",
    "digest_schedule_contract",
    "focused_tests",
    "healthcheck_contract",
    "history_privacy_contract",
    "integration_tests",
    "repair_scope_review",
    "rollover_scope",
    "session_archive_contract",
    "test_channel",
    "todo_drift_preview",
    "todo_readback_contract",
}

_ACTION_SPECS = (
    {
        "gate_id": "TTD-LIVE-DEPLOY",
        "effect_class": "deployment",
        "required_evidence": (
            "focused_tests",
            "integration_tests",
            "healthcheck_contract",
            "backup_preupdate",
        ),
        "planned_steps": (
            "verify the exact build and rollback commits",
            "review the operator-controlled deployment target out of band",
            "deploy only after the exact action-specific go",
            "read back the redacted Telegram readiness and health contract",
        ),
        "abort_conditions": (
            "exact build or rollback commit differs from the reviewed packet",
            "backup evidence is missing or stale",
            "focused or integration tests are not green",
            "health readback is unavailable or degraded",
        ),
        "success_evidence": (
            "deployed build commit readback",
            "redacted health and Telegram readiness result",
            "operator deployment decision record",
        ),
        "rollback_kind": "code_only",
        "rollback_steps": (
            "hold traffic-affecting follow-up actions",
            "restore the reviewed rollback commit through the operator deploy path",
            "repeat redacted health and readiness readback",
        ),
    },
    {
        "gate_id": "TTD-LIVE-DATA-REPAIR",
        "effect_class": "todo_data_mutation",
        "required_evidence": (
            "deploy_readback",
            "todo_drift_preview",
            "data_backup",
            "repair_scope_review",
        ),
        "planned_steps": (
            "review the exact content-free drift preview and repair scope",
            "verify the independent data backup evidence",
            "apply only the operator-reviewed Todo repair after its separate go",
            "read back Notes state and digest membership",
        ),
        "abort_conditions": (
            "preview, scope or backup reference differs from operator review",
            "the repair would touch an unreviewed owner, list or item",
            "Notes or digest readback cannot verify the postcondition",
            "any delete or migration appears outside the reviewed repair scope",
        ),
        "success_evidence": (
            "content-free mutation receipt",
            "Notes postcondition readback",
            "digest include or exclude readback",
        ),
        "rollback_kind": "data_only",
        "rollback_steps": (
            "stop further Todo mutations",
            "restore only the reviewed data backup through an independently approved path",
            "repeat Notes and digest readback without changing code",
        ),
    },
    {
        "gate_id": "TTD-LIVE-TELEGRAM-SMOKE",
        "effect_class": "telegram_test_channel_send_and_synthetic_todo",
        "required_evidence": (
            "deploy_readback",
            "test_channel",
            "todo_readback_contract",
            "digest_schedule_contract",
        ),
        "planned_steps": (
            "create exactly one synthetic Todo in the reviewed test scope",
            "verify its canonical Notes and digest inclusion postconditions",
            "complete the same synthetic Todo and verify digest exclusion",
            "send only to the separately reviewed test channel",
        ),
        "abort_conditions": (
            "the channel reference differs from the reviewed test channel",
            "a canonical mutation or digest receipt is missing",
            "the flow addresses non-synthetic or pre-existing Todo data",
            "more than the single bounded smoke sequence would be sent",
        ),
        "success_evidence": (
            "redacted Telegram delivery receipt",
            "Todo create and complete receipts",
            "digest include and exclude receipts",
        ),
        "rollback_kind": "synthetic_scope_cleanup",
        "rollback_steps": (
            "stop further Telegram sends",
            "remove only the synthetic smoke Todo inside the same approved scope if still present",
            "verify no unrelated Todo or chat state changed",
        ),
    },
    {
        "gate_id": "TTD-LIVE-ROLLOVER-SMOKE",
        "effect_class": "controlled_session_rollover",
        "required_evidence": (
            "deploy_readback",
            "rollover_scope",
            "session_archive_contract",
            "history_privacy_contract",
            "todo_readback_contract",
        ),
        "planned_steps": (
            "review one internal redacted chat and scope reference",
            "perform exactly one controlled rollover after the separate go",
            "verify old-session readability, new binding and single-use continuity",
            "verify canonical Todo readback without Telegram send or session delete",
        ),
        "abort_conditions": (
            "the reviewed chat or scope reference differs",
            "an active turn or concurrent rollover is present",
            "the new binding is not durably visible before archive",
            "a Telegram send, session delete or Todo reconstruction from continuity is attempted",
        ),
        "success_evidence": (
            "redacted previous and new session references",
            "archive-after-bind result",
            "single-use continuity and canonical Todo readback result",
        ),
        "rollback_kind": "binding_only",
        "rollback_steps": (
            "stop further rollover attempts",
            "rebind the previous readable session only through an operator-reviewed recovery path",
            "preserve both session histories and perform no Todo or data rollback",
        ),
    },
)


class TelegramTodoRolloutPacketError(ValueError):
    """Raised when a plan-only rollout packet contains unsafe inputs."""


def build_telegram_todo_rollout_packet(
    *,
    build_commit: Any,
    rollback_commit: Any,
    environment_ref: Any,
    evidence_refs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    exact_build = _exact_commit(build_commit, field_name="build_commit")
    exact_rollback = _exact_commit(rollback_commit, field_name="rollback_commit")
    if exact_build == exact_rollback:
        raise TelegramTodoRolloutPacketError("build and rollback commits must differ")
    environment = _evidence_ref(environment_ref, field_name="environment_ref")
    evidence = _normalize_evidence_refs(evidence_refs or {})
    release_id = _release_id(exact_build, environment)

    actions = tuple(
        _build_action(spec, release_id=release_id, evidence=evidence)
        for spec in _ACTION_SPECS
    )
    return {
        "schema": TELEGRAM_TODO_ROLLOUT_PACKET_SCHEMA,
        "mode": "plan_only",
        "packet_status": "blocked_pending_action_specific_live_go",
        "release_id": release_id,
        "environment_ref": environment,
        "build": {
            "exact_commit": exact_build,
            "rollback_commit": exact_rollback,
            "commits_are_distinct": True,
        },
        "healthcheck": {
            "contract_ref": evidence.get("healthcheck_contract"),
            "required_after_deploy": True,
            "raw_host_output_visible": False,
        },
        "actions": actions,
        "gate_independence": {
            "all_gates_require_separate_go": True,
            "one_gate_implies_another": False,
            "code_rollback_applies_data_restore": False,
            "data_rollback_changes_code": False,
        },
        "authorization": {
            "accepted_live_go_count": 0,
            "live_go_ledger": (),
            "execution_supported": False,
        },
        "privacy": {
            "raw_content_visible": False,
            "raw_identifiers_visible": False,
            "secret_values_visible": False,
            "host_targets_visible": False,
            "private_todo_values_visible": False,
        },
    }


def _build_action(
    spec: Mapping[str, Any],
    *,
    release_id: str,
    evidence: Mapping[str, str],
) -> dict[str, Any]:
    required = tuple(str(item) for item in spec["required_evidence"])
    missing = tuple(item for item in required if item not in evidence)
    gate_id = str(spec["gate_id"])
    return {
        "gate_id": gate_id,
        "effect_class": str(spec["effect_class"]),
        "readiness": "ready_for_separate_go" if not missing else "blocked_missing_evidence",
        "authorization_state": "missing_action_specific_go",
        "execution_state": "blocked",
        "execution_supported": False,
        "required_exact_go_phrase": f"GO {gate_id} {release_id}",
        "required_evidence": required,
        "evidence_refs": {
            key: evidence[key] for key in required if key in evidence
        },
        "missing_evidence": missing,
        "planned_steps": tuple(spec["planned_steps"]),
        "abort_conditions": tuple(spec["abort_conditions"]),
        "success_evidence": tuple(spec["success_evidence"]),
        "implied_gate_ids": (),
        "rollback": {
            "kind": str(spec["rollback_kind"]),
            "automatic": False,
            "requires_operator_review": True,
            "steps": tuple(spec["rollback_steps"]),
        },
    }


def _exact_commit(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not _COMMIT_RE.fullmatch(text):
        raise TelegramTodoRolloutPacketError(
            f"{field_name} must be an exact 40-character lowercase commit id"
        )
    return text


def _evidence_ref(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip().lower()
    if not _EVIDENCE_REF_RE.fullmatch(text):
        raise TelegramTodoRolloutPacketError(
            f"{field_name} must be a content-free versioned evidence reference"
        )
    return text


def _normalize_evidence_refs(values: Mapping[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for raw_key, raw_value in values.items():
        key = str(raw_key or "").strip().lower().replace("-", "_")
        if key not in _ALLOWED_EVIDENCE_KEYS:
            raise TelegramTodoRolloutPacketError(f"unsupported evidence key: {raw_key!r}")
        normalized[key] = _evidence_ref(raw_value, field_name=key)
    return dict(sorted(normalized.items()))


def _release_id(build_commit: str, environment_ref: str) -> str:
    digest = hashlib.sha256(
        f"telegram-todo-rollout:v1\0{build_commit}\0{environment_ref}".encode("utf-8")
    ).hexdigest()[:24]
    return f"ttd-release:v1:{digest}"


__all__ = [
    "TELEGRAM_TODO_LIVE_GATES",
    "TELEGRAM_TODO_ROLLOUT_PACKET_SCHEMA",
    "TelegramTodoRolloutPacketError",
    "build_telegram_todo_rollout_packet",
]
