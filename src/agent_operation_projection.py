"""Safe, deterministic product projection for the Agent operations surface.

The functions in this module are intentionally pure.  They accept already
persisted product facts and emit the bounded public contract; they never read
Temporal, chat state, localStorage, or the filesystem.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Iterable, Mapping, Sequence


AGENT_OPERATION_PROJECTION_SCHEMA_ID = "odysseus.agent.operation_projection.v1"
MAX_HISTORY_PAGE_SIZE = 200
MAX_RUN_PAGE_SIZE = 100
HEARTBEAT_LATE_MULTIPLIER = 1
HEARTBEAT_STALE_MULTIPLIER = 2

_ACTIVE_ACTIVITY_STATES = frozenset({"scheduled", "running", "retry_wait"})
_TERMINAL_RUN_STATES = frozenset(
    {"cancelled", "completed", "failed", "timed_out", "terminated"}
)
_COMMAND_STATES = {
    "pause": frozenset({"running", "waiting_gate", "waiting_signal"}),
    "resume": frozenset({"paused"}),
    "cancel": frozenset({"running", "waiting_gate", "waiting_signal", "paused"}),
    "retry_activity": frozenset({"running"}),
    "decide_gate": frozenset({"waiting_gate"}),
    "steer_run": frozenset({"running", "waiting_signal", "paused"}),
}
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$")
_CURSOR_RE = re.compile(r"^h([0-9]{1,9}):([0-9]{1,20})$")
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[/\\]")
_EMBEDDED_WINDOWS_PATH_RE = re.compile(r"(?:^|[\s(\[{\"'])(?:[A-Za-z]:[/\\]|\\\\)[^\s]+")
_EMBEDDED_UNIX_PATH_RE = re.compile(
    r"(?:^|[\s(\[{\"'])/(?:Users|etc|home|mnt|opt|private|root|srv|tmp|var|Volumes)(?:/|\\)[^\s]+",
    re.IGNORECASE,
)
_SECRET_VALUE_RES = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"https?://[^\s/:]+:[^\s/@]+@", re.IGNORECASE),
)
_SENSITIVE_PARTS = (
    "access_token",
    "api_key",
    "credential",
    "password",
    "private_key",
    "provider_payload",
    "raw_command",
    "raw_history",
    "raw_output",
    "secret",
)


class AgentOperationProjectionError(ValueError):
    """Projection input is unsafe or violates the public product contract."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def allowed_commands_for(
    run_state: str,
    *,
    slice_states: Mapping[str, str] | None = None,
    gate_states: Mapping[str, str] | None = None,
) -> list[str]:
    """Return the workflow-equivalent command set for persisted server state."""

    state = _identifier(run_state, "run_state")
    slices = dict(slice_states or {})
    gates = dict(gate_states or {})
    allowed: list[str] = []
    for command, states in _COMMAND_STATES.items():
        if state not in states:
            continue
        if command == "retry_activity" and "retry_wait" not in slices.values():
            continue
        if command == "decide_gate" and "pending" not in gates.values():
            continue
        allowed.append(command)
    return allowed


def derive_heartbeat_health(
    *,
    activity_state: str,
    last_heartbeat_at: str | None,
    heartbeat_timeout_seconds: int | None,
    observed_at: str | datetime,
    started_at: str | None = None,
) -> str:
    """Classify freshness from server time and the persisted Activity policy."""

    if activity_state not in _ACTIVE_ACTIVITY_STATES or not heartbeat_timeout_seconds:
        return "not_expected"
    if isinstance(heartbeat_timeout_seconds, bool) or heartbeat_timeout_seconds <= 0:
        raise AgentOperationProjectionError(
            "invalid_activity", "heartbeat timeout must be a positive integer"
        )
    observed = _datetime(observed_at, "observed_at")
    anchor_value = last_heartbeat_at or started_at
    if anchor_value is None:
        return "stale"
    age = max(0.0, (observed - _datetime(anchor_value, "heartbeat_anchor")).total_seconds())
    if age <= heartbeat_timeout_seconds * HEARTBEAT_LATE_MULTIPLIER:
        return "healthy"
    if age <= heartbeat_timeout_seconds * HEARTBEAT_STALE_MULTIPLIER:
        return "late"
    return "stale"


def build_agent_operation_projection(
    *,
    plan_ref: Mapping[str, Any],
    run: Mapping[str, Any],
    activities: Sequence[Mapping[str, Any]] = (),
    claims: Sequence[Mapping[str, Any]] = (),
    gates: Sequence[Mapping[str, Any]] = (),
    evidence: Sequence[Mapping[str, Any]] = (),
    observed_at: str | datetime,
) -> dict[str, Any]:
    """Build the complete, allowlisted Agent read model from persisted facts."""

    observed = _timestamp(observed_at, "observed_at")
    normalized_plan = _plan_reference(plan_ref)
    slice_states = _string_mapping(run.get("slice_states", {}), "slice_states")
    gate_states = _string_mapping(run.get("gate_states", {}), "gate_states")
    state = _identifier(run.get("run_state", run.get("state")), "run_state")
    current_nodes = sorted(
        node_id
        for node_id, node_state in slice_states.items()
        if node_state in {"claiming", "activity_scheduled", "activity_running", "retry_wait"}
    )
    projected_activities = [
        _activity(item, observed_at=observed) for item in activities
    ]
    projection = {
        "schema_id": AGENT_OPERATION_PROJECTION_SCHEMA_ID,
        "observed_at": observed,
        "run": {
            "agent_run_id": _identifier(run.get("agent_run_id"), "agent_run_id"),
            "workflow_id": _identifier(run.get("workflow_id"), "workflow_id"),
            "workflow_run_id": _identifier(
                run.get("workflow_run_id"), "workflow_run_id"
            ),
            "history_segment": _non_negative_int(
                run.get("history_segment", 0), "history_segment"
            ),
            "plan_ref": normalized_plan,
            "state": state,
            "version": _non_negative_int(
                run.get("run_version", run.get("version", 0)), "run_version"
            ),
            "started_at": _optional_timestamp(run.get("started_at"), "started_at"),
            "updated_at": _optional_timestamp(run.get("updated_at"), "updated_at"),
            "completed_at": _optional_timestamp(
                run.get("completed_at"), "completed_at"
            ),
            "deadline_at": _optional_timestamp(run.get("deadline_at"), "deadline_at"),
            "current_node_ids": current_nodes,
            "waiting_reason": _optional_safe_text(
                run.get("waiting_reason"), "waiting_reason", maximum=512
            ),
            "allowed_commands": allowed_commands_for(
                state,
                slice_states=slice_states,
                gate_states=gate_states,
            ),
        },
        "activities": projected_activities,
        "claims": [_claim(item) for item in claims],
        "gates": [_bounded_public_object(item, "gate") for item in gates],
        "evidence": [_bounded_public_object(item, "evidence") for item in evidence],
    }
    _assert_public_safe(projection)
    return projection


def project_history(
    events: Iterable[Mapping[str, Any]],
    *,
    after: str = "",
    limit: int = 50,
) -> dict[str, Any]:
    """Return a stable, bounded history page strictly after ``after``."""

    page_limit = _bounded_limit(limit, MAX_HISTORY_PAGE_SIZE)
    after_key = decode_history_cursor(after)
    normalized = sorted((_history_event(item) for item in events), key=_event_key)
    remaining = [item for item in normalized if _event_key(item) > after_key]
    selected = remaining[:page_limit]
    cursor = encode_history_cursor(*after_key) if after_key != (-1, -1) else ""
    next_cursor = cursor
    if selected:
        next_cursor = selected[-1]["event_id"]
    return {
        "cursor": cursor,
        "next_cursor": next_cursor,
        "has_more": len(remaining) > page_limit,
        "events": [
            {
                "event_id": item["event_id"],
                "event_type": item["event_type"],
                "occurred_at": item["occurred_at"],
                "node_id": item["node_id"],
                "activity_id": item["activity_id"],
                "summary": item["summary"],
                "ref_ids": item["ref_ids"],
            }
            for item in selected
        ],
    }


def encode_history_cursor(history_segment: int, event_id: int) -> str:
    return f"h{_non_negative_int(history_segment, 'history_segment')}:{_non_negative_int(event_id, 'event_id')}"


def decode_history_cursor(cursor: str | None) -> tuple[int, int]:
    text = str(cursor or "")
    if not text:
        return (-1, -1)
    match = _CURSOR_RE.fullmatch(text)
    if match is None:
        raise AgentOperationProjectionError("invalid_cursor", "history cursor is invalid")
    return (int(match.group(1)), int(match.group(2)))


def _activity(value: Mapping[str, Any], *, observed_at: str) -> dict[str, Any]:
    state = _identifier(value.get("state"), "activity.state")
    timeout = value.get("heartbeat_timeout_seconds")
    if timeout is not None:
        timeout = _positive_int(timeout, "heartbeat_timeout_seconds")
    last_heartbeat = _optional_timestamp(
        value.get("last_heartbeat_at"), "last_heartbeat_at"
    )
    started_at = _optional_timestamp(value.get("started_at"), "activity.started_at")
    return {
        "activity_id": _identifier(value.get("activity_id"), "activity_id"),
        "node_id": _identifier(value.get("node_id"), "activity.node_id"),
        "type": _identifier(value.get("type"), "activity.type"),
        "state": state,
        "attempt": _positive_int(value.get("attempt", 1), "activity.attempt"),
        "max_attempts": _positive_int(
            value.get("max_attempts", 1), "activity.max_attempts"
        ),
        "retryable": _boolean(value.get("retryable", False), "activity.retryable"),
        "next_retry_at": _optional_timestamp(
            value.get("next_retry_at"), "activity.next_retry_at"
        ),
        "started_at": started_at,
        "updated_at": _optional_timestamp(
            value.get("updated_at"), "activity.updated_at"
        ),
        "completed_at": _optional_timestamp(
            value.get("completed_at"), "activity.completed_at"
        ),
        "last_heartbeat_at": last_heartbeat,
        "heartbeat_timeout_seconds": timeout,
        "heartbeat_health": derive_heartbeat_health(
            activity_state=state,
            last_heartbeat_at=last_heartbeat,
            heartbeat_timeout_seconds=timeout,
            observed_at=observed_at,
            started_at=started_at,
        ),
        "error_code": _optional_identifier(value.get("error_code"), "error_code"),
    }


def _claim(value: Mapping[str, Any]) -> dict[str, Any]:
    paths = value.get("repo_relative_paths", [])
    hotfiles = value.get("hotfiles", [])
    if not isinstance(paths, Sequence) or isinstance(paths, (str, bytes)):
        raise AgentOperationProjectionError("invalid_claim", "claim paths must be an array")
    if not isinstance(hotfiles, Sequence) or isinstance(hotfiles, (str, bytes)):
        raise AgentOperationProjectionError("invalid_claim", "claim hotfiles must be an array")
    return {
        "claim_id": _identifier(value.get("claim_id"), "claim_id"),
        "node_id": _identifier(value.get("node_id"), "claim.node_id"),
        "repo_id": _identifier(value.get("repo_id"), "repo_id"),
        "repo_relative_paths": [_repo_relative_path(item) for item in paths],
        "hotfiles": [_repo_relative_path(item) for item in hotfiles],
        "state": _identifier(value.get("state"), "claim.state"),
        "lease_revision": _non_negative_int(
            value.get("lease_revision", 0), "lease_revision"
        ),
        "lease_expires_at": _optional_timestamp(
            value.get("lease_expires_at"), "lease_expires_at"
        ),
    }


def _history_event(value: Mapping[str, Any]) -> dict[str, Any]:
    segment = _non_negative_int(value.get("history_segment", 0), "history_segment")
    event_number = _positive_int(value.get("event_id"), "event_id")
    ref_ids = value.get("ref_ids", [])
    if not isinstance(ref_ids, Sequence) or isinstance(ref_ids, (str, bytes)):
        raise AgentOperationProjectionError("invalid_event", "ref_ids must be an array")
    return {
        "event_id": encode_history_cursor(segment, event_number),
        "event_type": _identifier(value.get("event_type"), "event_type"),
        "occurred_at": _timestamp(value.get("occurred_at"), "occurred_at"),
        "node_id": _optional_identifier(value.get("node_id"), "event.node_id"),
        "activity_id": _optional_identifier(
            value.get("activity_id"), "event.activity_id"
        ),
        "summary": _optional_safe_text(value.get("summary"), "summary", maximum=512),
        "ref_ids": [_identifier(item, "ref_id") for item in ref_ids[:32]],
        "_key": (segment, event_number),
    }


def _event_key(value: Mapping[str, Any]) -> tuple[int, int]:
    key = value.get("_key")
    if isinstance(key, tuple):
        return key
    return decode_history_cursor(str(value["event_id"]))


def _plan_reference(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AgentOperationProjectionError("invalid_plan_ref", "plan_ref must be an object")
    content_hash = str(value.get("content_hash") or "")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", content_hash):
        raise AgentOperationProjectionError("invalid_plan_ref", "content_hash is invalid")
    revision = value.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, (int, str)):
        raise AgentOperationProjectionError("invalid_plan_ref", "revision is invalid")
    return {
        "project_id": _identifier(value.get("project_id"), "project_id"),
        "roadmap_id": _identifier(value.get("roadmap_id"), "roadmap_id"),
        "revision": revision,
        "content_hash": content_hash,
    }


def _bounded_public_object(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or len(value) > 32:
        raise AgentOperationProjectionError("invalid_projection", f"{label} is not bounded")
    result = {str(key): item for key, item in value.items()}
    _assert_public_safe(result)
    return result


def _assert_public_safe(value: Any, *, depth: int = 0) -> None:
    if depth > 7:
        raise AgentOperationProjectionError("unsafe_projection", "projection is too deep")
    if isinstance(value, Mapping):
        if len(value) > 256:
            raise AgentOperationProjectionError("unsafe_projection", "object is too large")
        for key, item in value.items():
            lowered = str(key).lower()
            if any(part in lowered for part in _SENSITIVE_PARTS):
                raise AgentOperationProjectionError(
                    "unsafe_projection", f"sensitive field {key!r} is forbidden"
                )
            _assert_public_safe(item, depth=depth + 1)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > 2_000:
            raise AgentOperationProjectionError("unsafe_projection", "array is too large")
        for item in value:
            _assert_public_safe(item, depth=depth + 1)
        return
    if isinstance(value, str):
        if len(value) > 16_384:
            raise AgentOperationProjectionError("unsafe_projection", "string is too large")
        if _contains_absolute_path(value):
            raise AgentOperationProjectionError(
                "unsafe_projection", "absolute paths are not public"
            )
        if any(pattern.search(value) for pattern in _SECRET_VALUE_RES):
            raise AgentOperationProjectionError(
                "unsafe_projection", "credential-shaped values are not public"
            )
        return
    if value is None or type(value) in (bool, int):
        return
    raise AgentOperationProjectionError("unsafe_projection", "non-JSON value is forbidden")


def _repo_relative_path(value: Any) -> str:
    text = str(value or "").replace("\\", "/")
    if not text or _is_absolute_path(text) or ".." in text.split("/"):
        raise AgentOperationProjectionError("invalid_claim", "path must be repository-relative")
    return text


def _is_absolute_path(value: str) -> bool:
    return value.startswith(("/", "\\\\")) or bool(_WINDOWS_ABSOLUTE_RE.match(value))


def _contains_absolute_path(value: str) -> bool:
    return (
        _is_absolute_path(value)
        or bool(_EMBEDDED_WINDOWS_PATH_RE.search(value))
        or bool(_EMBEDDED_UNIX_PATH_RE.search(value))
    )


def _string_mapping(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or len(value) > 2_000:
        raise AgentOperationProjectionError("invalid_projection", f"{label} is invalid")
    return {
        _identifier(key, f"{label}.key"): _identifier(item, f"{label}.value")
        for key, item in value.items()
    }


def _identifier(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not _ID_RE.fullmatch(text) or ".." in text or _is_absolute_path(text):
        raise AgentOperationProjectionError("invalid_identifier", label)
    return text


def _optional_identifier(value: Any, label: str) -> str | None:
    return None if value in (None, "") else _identifier(value, label)


def _optional_safe_text(value: Any, label: str, *, maximum: int) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    if len(text) > maximum or _is_absolute_path(text):
        raise AgentOperationProjectionError("unsafe_projection", label)
    return text


def _boolean(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise AgentOperationProjectionError("invalid_projection", f"{label} must be boolean")
    return value


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AgentOperationProjectionError("invalid_projection", f"{label} must be positive")
    return value


def _non_negative_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AgentOperationProjectionError(
            "invalid_projection", f"{label} must be non-negative"
        )
    return value


def _bounded_limit(value: Any, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
        raise AgentOperationProjectionError(
            "invalid_limit", f"limit must be between 1 and {maximum}"
        )
    return value


def _optional_timestamp(value: Any, label: str) -> str | None:
    return None if value in (None, "") else _timestamp(value, label)


def _timestamp(value: Any, label: str) -> str:
    return _datetime(value, label).isoformat().replace("+00:00", "Z")


def _datetime(value: Any, label: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and len(value) <= 64:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise AgentOperationProjectionError("invalid_timestamp", label) from exc
    else:
        raise AgentOperationProjectionError("invalid_timestamp", label)
    if parsed.tzinfo is None:
        raise AgentOperationProjectionError("invalid_timestamp", label)
    return parsed.astimezone(timezone.utc)
