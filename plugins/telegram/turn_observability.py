"""Pure, content-free Telegram turn observability projection."""

from __future__ import annotations

import json
import re


TURN_OBSERVABILITY_SCHEMA = "odysseus.telegram.turn_observability.v1"
MAX_TURN_OBSERVABILITY_BYTES = 1024

_INPUT_FIELDS = (
    "correlation_id",
    "intake_path",
    "last_observed_phase",
    "outcome",
    "received_at",
    "session_bound_at",
    "agent_started_at",
    "tool_selection_ms",
    "first_delta_at",
    "first_draft_sent_at",
    "final_sent_at",
)
_TIMESTAMP_FIELDS = (
    "received_at",
    "session_bound_at",
    "agent_started_at",
    "first_delta_at",
    "first_draft_sent_at",
    "final_sent_at",
)
_INTAKE_PATHS = frozenset(("webhook", "polling"))
_PHASES = (
    "received",
    "session_bound",
    "agent_started",
    "first_delta",
    "first_draft_sent",
    "final_sent",
)
_OUTCOMES = frozenset(("in_progress", "sent", "failed", "blocked", "skipped", "reconciliation_required"))
_MAX_TIMESTAMP = 253402300799999
_MAX_TOOL_SELECTION_MS = 3600000
_CORRELATION_ID = re.compile(r"telegram:[0-9a-f]{16}\Z")


class TelegramTurnObservabilityError(ValueError):
    """A fixed, content-free rejection of an observability snapshot."""


def _reject(code: str) -> None:
    raise TelegramTurnObservabilityError(code) from None


def _is_timestamp(value: object) -> bool:
    return value is None or (type(value) is int and 0 <= value <= _MAX_TIMESTAMP)


def _is_tool_selection_ms(value: object) -> bool:
    return value is None or (type(value) is int and 0 <= value <= _MAX_TOOL_SELECTION_MS)


def _validate(snapshot: dict[str, object]) -> None:
    if len(snapshot) != len(_INPUT_FIELDS):
        _reject("invalid_snapshot")
    for field in _INPUT_FIELDS:
        if field not in snapshot:
            _reject("invalid_snapshot")

    correlation_id = snapshot["correlation_id"]
    intake_path = snapshot["intake_path"]
    phase = snapshot["last_observed_phase"]
    outcome = snapshot["outcome"]
    if type(correlation_id) is not str or _CORRELATION_ID.fullmatch(correlation_id) is None:
        _reject("invalid_snapshot")
    if type(intake_path) is not str or intake_path not in _INTAKE_PATHS:
        _reject("invalid_snapshot")
    if type(phase) is not str or phase not in _PHASES:
        _reject("invalid_timing")
    if type(outcome) is not str or outcome not in _OUTCOMES:
        _reject("invalid_timing")
    if any(not _is_timestamp(snapshot[field]) for field in _TIMESTAMP_FIELDS):
        _reject("invalid_snapshot")
    if not _is_tool_selection_ms(snapshot["tool_selection_ms"]):
        _reject("invalid_snapshot")

    phase_index = _PHASES.index(phase)
    timestamps = tuple(snapshot[field] for field in _TIMESTAMP_FIELDS)
    if timestamps[0] is None or timestamps[phase_index] is None:
        _reject("invalid_timing")
    if any(value is not None for value in timestamps[phase_index + 1:]):
        _reject("invalid_timing")
    present_timestamps = tuple(value for value in timestamps[:phase_index + 1] if value is not None)
    if any(later < earlier for earlier, later in zip(present_timestamps, present_timestamps[1:])):
        _reject("invalid_timing")
    if snapshot["tool_selection_ms"] is not None and phase_index < 2:
        _reject("invalid_timing")
    if (outcome == "sent") != (phase == "final_sent"):
        _reject("invalid_timing")


def project_telegram_turn_observability(snapshot: object, /) -> dict[str, object]:
    """Return a detached, fixed-shape projection without any side effects."""
    if type(snapshot) is not dict:
        _reject("invalid_snapshot")
    try:
        captured = snapshot.copy()
        _validate(captured)
        projection: dict[str, object] = {
            "schema": TURN_OBSERVABILITY_SCHEMA,
            **{field: captured[field] for field in _INPUT_FIELDS},
            "snapshot_atomic": False,
            "persistence_authorized": False,
            "lifecycle_transition_authorized": False,
            "live_action_authorized": False,
            "raw_content_visible": False,
            "raw_identifiers_visible": False,
        }
        canonical = json.dumps(projection, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(canonical) > MAX_TURN_OBSERVABILITY_BYTES:
            _reject("projection_failed")
        return projection
    except TelegramTurnObservabilityError:
        raise
    except Exception:
        _reject("projection_failed")
