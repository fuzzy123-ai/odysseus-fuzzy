"""Safe request contract for Odysseus user notifications.

The contract deliberately keeps delivery secrets and channel targets outside
agent/MCP inputs. Callers may request a notification; Odysseus decides whether
and how to dispatch it from server-side configuration.
"""

from __future__ import annotations

import json
import hashlib
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from src.todo_digest_formatting import format_todo_digest_notification_body


ALLOWED_SEVERITIES = {"info", "success", "warning", "error"}
ALLOWED_CHANNELS = {"auto", "telegram"}
FORBIDDEN_KEY_TOKENS = {
    "apikey",
    "authorization",
    "bearer",
    "botsecret",
    "bottoken",
    "chatid",
    "credential",
    "credentials",
    "destination",
    "password",
    "recipient",
    "secret",
    "target",
    "telegramchat",
    "telegramchatid",
    "telegramtoken",
    "token",
}
MAX_MESSAGE_CHARS = 1200
MAX_EVENT_CHARS = 80
MAX_METADATA_ITEMS = 12

PLANNING_NOTIFICATION_SCHEMA = "odysseus.planning.definition_notification_candidate.v2"
PLANNING_NOTIFICATION_EVENTS = {
    "project_created",
    "project_deleted",
    "roadmap_created",
    "roadmap_deleted",
    "roadmap_revision_approved",
    "roadmap_revision_conflict",
    "undo_available_after_structural_delete",
}
PLANNING_REVISION_NOTIFICATION_EVENTS = {
    "roadmap_revision_approved",
    "roadmap_revision_conflict",
}
EXECUTION_NOTIFICATION_EVENTS = {
    "activity_completed",
    "activity_failed",
    "activity_started",
    "agent_run_completed",
    "agent_run_failed",
    "agent_run_started",
    "claim_expired",
    "gate_blocked",
    "gate_unblocked_when_it_changes_available_work",
    "heartbeat_late",
    "heartbeat_recovered",
    "human_decision_required",
    "workflow_cancelled",
    "workflow_paused",
    "workflow_resumed",
}
PLANNING_SILENT_EVENTS = {
    "roadmap_progress_updated",
    "todo_completed",
    "context_pack_read",
    "raptor_memory_processed",
    "summary_refreshed",
    "agent_checkpoint_written",
    "planning_read",
    "planning_search",
    "planning_validate",
    "planning_draft_created",
    "planning_patch_proposed",
    "planning_context_pack_read",
    "planning_section_context_pack_read",
    "planning_progress_updated",
    "planning_todo_completed",
    "planning_derived_memory_lifecycle_planned",
    "planning_agent_checkpoint_written",
}
PLANNING_UI_HIGHLIGHT_KINDS = {"project", "roadmap"}
PLANNING_UI_HIGHLIGHT_MODES = {"focus", "expand_summary"}
PLANNING_DOCUMENT_INTENTS = {"none", "open_roadmap_document"}
MAX_PLANNING_REASON_CHARS = 240

_PLANNING_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_PLANNING_CANDIDATE_FIELDS = {
    "event_type", "project_id", "roadmap_id", "revision", "content_hash", "severity", "reason", "created_at", "ui_target",
}
_PLANNING_UI_TARGET_FIELDS = {
    "workspace", "view", "highlight_kind", "highlight_mode", "document_view_intent",
}
_PRIVATE_PATH_RE = re.compile(r"(?i)(?:[a-z]:[\\/]|/(?:home|users|var/lib|mnt|srv)/|\\\\[^\s]+)")
_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:bearer\s+[a-z0-9._~+/=-]{8,}|(?:api[_ -]?key|access[_ -]?token|password|secret)\s*[:=]\s*\S+|\bsk-[a-z0-9_-]{12,})"
)
_URL_RE = re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://")


class NotificationContractError(ValueError):
    """Raised when a notification request violates the safe boundary."""

    def __init__(self, message: str, *, code: str = "notification_contract_error") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class UserNotificationRequest:
    event: str
    message: str
    severity: str = "info"
    channel: str = "auto"
    dry_run: bool = True
    render_mode: str = "standard"
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class UserNotificationDecision:
    status: str
    dispatch_allowed: bool
    reason: str
    request: UserNotificationRequest
    resolved_channel: str
    rendered_text: str

    def as_public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["token_value_visible"] = False
        payload["chat_target_value_visible"] = False
        return payload


@dataclass(frozen=True)
class PlanningNotificationUiTarget:
    workspace: str
    view: str
    project_id: str
    roadmap_id: str | None
    revision: int | None
    highlight_kind: str
    highlight_id: str
    highlight_mode: str
    document_view_intent: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class PlanningNotificationCandidate:
    event_type: str
    project_id: str
    roadmap_id: str | None
    revision: int | None
    content_hash: str
    severity: str
    reason: str
    created_at: str
    ui_target: PlanningNotificationUiTarget
    dedupe_key: str
    schema: str = PLANNING_NOTIFICATION_SCHEMA
    classification: str = "notification_candidate"
    delivery_authorized: bool = False
    live_delivery_performed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "classification": self.classification,
            "event_type": self.event_type,
            "project_id": self.project_id,
            "roadmap_id": self.roadmap_id,
            "revision": self.revision,
            "content_hash": self.content_hash,
            "severity": self.severity,
            "reason": self.reason,
            "created_at": self.created_at,
            "ui_target": self.ui_target.to_dict(),
            "dedupe_key": self.dedupe_key,
            "delivery_authorized": False,
            "live_delivery_performed": False,
        }


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _assert_no_forbidden_keys(value: Any, *, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = _normalized_key(key)
            if normalized in FORBIDDEN_KEY_TOKENS:
                raise NotificationContractError(f"Forbidden notification key at {path}.{key}")
            _assert_no_forbidden_keys(nested, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_no_forbidden_keys(nested, path=f"{path}[{index}]")


def _clean_text(value: Any, *, fallback: str = "", max_chars: int = MAX_MESSAGE_CHARS) -> str:
    text = " ".join(str(value if value is not None else fallback).split())
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "..."
    return text


def _clean_message_text(value: Any, *, fallback: str = "", max_chars: int = MAX_MESSAGE_CHARS) -> str:
    raw = str(value if value is not None else fallback).replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.split()) for line in raw.split("\n")]
    # Preserve intentional paragraph/list structure without letting arbitrary
    # whitespace create huge Telegram bodies.
    compacted: list[str] = []
    previous_blank = False
    for line in lines:
        if not line:
            if not previous_blank:
                compacted.append("")
            previous_blank = True
            continue
        compacted.append(line)
        previous_blank = False
    text = "\n".join(compacted).strip()
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "..."
    return text


def _clean_slug(value: Any, *, fallback: str = "odysseus_notification") -> str:
    raw = _clean_text(value, fallback=fallback, max_chars=MAX_EVENT_CHARS).lower()
    slug = re.sub(r"[^a-z0-9_.-]+", "_", raw).strip("._-")
    return slug or fallback


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _metadata_to_public_strings(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, str] = {}
    for key, item in list(value.items())[:MAX_METADATA_ITEMS]:
        clean_key = _clean_slug(key, fallback="metadata")[:40]
        if isinstance(item, (dict, list, tuple)):
            rendered = json.dumps(item, ensure_ascii=False, sort_keys=True)
        else:
            rendered = str(item)
        out[clean_key] = _clean_text(rendered, max_chars=180)
    return out


def build_user_notification_request(payload: Mapping[str, Any] | str) -> UserNotificationRequest:
    if isinstance(payload, str):
        payload = {"message": payload}
    if not isinstance(payload, Mapping):
        raise NotificationContractError("Notification payload must be an object or text")
    _assert_no_forbidden_keys(payload)

    event = _clean_slug(
        payload.get("event")
        or payload.get("event_type")
        or payload.get("run_label")
        or "odysseus_notification"
    )
    message = _clean_message_text(payload.get("message") or payload.get("summary") or payload.get("text") or "")
    if not message:
        raise NotificationContractError("Notification message is required")

    severity = _clean_slug(payload.get("severity") or "info", fallback="info")
    if severity not in ALLOWED_SEVERITIES:
        severity = "info"

    channel = _clean_slug(
        payload.get("channel") or payload.get("requested_channel_class") or "auto",
        fallback="auto",
    )
    if channel in {"completion_notice", "completion"}:
        channel = "auto"
    if channel not in ALLOWED_CHANNELS:
        channel = "auto"

    render_mode = _clean_slug(payload.get("render_mode") or "standard", fallback="standard")
    if render_mode not in {"standard", "plain"}:
        render_mode = "standard"

    return UserNotificationRequest(
        event=event,
        message=message,
        severity=severity,
        channel=channel,
        dry_run=_coerce_bool(payload.get("dry_run"), default=True),
        render_mode=render_mode,
        metadata=_metadata_to_public_strings(payload.get("metadata") or {}),
    )


def render_user_notification_text(request: UserNotificationRequest) -> str:
    normalized_message = " ".join(request.message.lower().split())
    is_todo_digest = request.event in {"todo_digest", "scheduled_task"} and (
        normalized_message.startswith("todo digest")
        or "todo_digest: todo digest" in normalized_message
        or "scheduled_task: todo digest" in normalized_message
    )
    if is_todo_digest:
        return format_todo_digest_notification_body(request.message)
    if request.render_mode == "plain":
        return request.message
    prefix = f"[Odysseus][{request.severity}] {request.event}"
    if not request.metadata:
        return f"{prefix}: {request.message}"
    metadata = " ".join(f"{key}={value}" for key, value in sorted(request.metadata.items()))
    return f"{prefix}: {request.message}\n{metadata}"


def build_user_notification_decision(
    payload: Mapping[str, Any] | str,
    *,
    configured_channels: Sequence[str] = ("telegram",),
    live_dispatch_enabled: bool = False,
    target_configured: bool = False,
) -> UserNotificationDecision:
    request = build_user_notification_request(payload)
    configured = tuple(channel for channel in configured_channels if channel in ALLOWED_CHANNELS - {"auto"})
    resolved_channel = configured[0] if request.channel == "auto" and configured else request.channel
    rendered_text = render_user_notification_text(request)

    if request.dry_run:
        return UserNotificationDecision(
            status="dry_run",
            dispatch_allowed=False,
            reason="dry_run_requested",
            request=request,
            resolved_channel=resolved_channel,
            rendered_text=rendered_text,
        )
    if resolved_channel not in configured:
        return UserNotificationDecision(
            status="blocked",
            dispatch_allowed=False,
            reason="channel_not_configured",
            request=request,
            resolved_channel=resolved_channel,
            rendered_text=rendered_text,
        )
    if not live_dispatch_enabled:
        return UserNotificationDecision(
            status="blocked",
            dispatch_allowed=False,
            reason="live_dispatch_disabled",
            request=request,
            resolved_channel=resolved_channel,
            rendered_text=rendered_text,
        )
    if not target_configured:
        return UserNotificationDecision(
            status="blocked",
            dispatch_allowed=False,
            reason="notification_target_missing",
            request=request,
            resolved_channel=resolved_channel,
            rendered_text=rendered_text,
        )
    return UserNotificationDecision(
        status="accepted",
        dispatch_allowed=True,
        reason="ready_for_server_side_dispatch",
        request=request,
        resolved_channel=resolved_channel,
        rendered_text=rendered_text,
    )


def classify_planning_notification_event(event_type: Any) -> str:
    workspace = notification_workspace_for_event(event_type)
    if workspace == "agent":
        raise NotificationContractError(
            "Execution notifications are Agent-only and cannot target Planning",
            code="execution_event_agent_only",
        )
    if workspace == "silent":
        return "silent"
    return "notification_candidate"


def notification_workspace_for_event(event_type: Any) -> str:
    """Return the only workspace allowed to receive one product event."""

    if not isinstance(event_type, str):
        raise NotificationContractError(
            "Notification event_type must be text",
            code="invalid_notification_event",
        )
    if event_type in PLANNING_NOTIFICATION_EVENTS:
        return "planning"
    if event_type in EXECUTION_NOTIFICATION_EVENTS:
        return "agent"
    if event_type in PLANNING_SILENT_EVENTS:
        return "silent"
    raise NotificationContractError(
        "Notification event_type is invalid",
        code="invalid_notification_event",
    )


def build_planning_notification_candidate(
    payload: Mapping[str, Any],
) -> PlanningNotificationCandidate | None:
    """Build one sparse logical Planning notification candidate.

    The result is navigation metadata only.  It never authorizes or performs
    Telegram, network, provider, or any other live delivery.
    """

    if not isinstance(payload, Mapping):
        raise NotificationContractError("Planning notification payload must be an object")
    if set(payload) - _PLANNING_CANDIDATE_FIELDS:
        raise NotificationContractError("Planning notification payload contains unsupported fields")
    _assert_no_forbidden_keys(payload)
    _assert_planning_safe_values(payload)

    event_type = _planning_event_type(payload.get("event_type"))
    project_id = _planning_id(payload.get("project_id"), field_name="project_id")
    if event_type in PLANNING_SILENT_EVENTS:
        return None

    roadmap_id = _optional_planning_id(payload.get("roadmap_id"), field_name="roadmap_id")
    if event_type not in {"project_created", "project_deleted"} and roadmap_id is None:
        raise NotificationContractError("Roadmap structural events require roadmap_id")

    if event_type in PLANNING_REVISION_NOTIFICATION_EVENTS:
        revision = _planning_revision(payload.get("revision"))
        content_hash = _planning_content_hash(payload.get("content_hash"))
    else:
        if payload.get("revision") is not None or payload.get("content_hash") not in {None, ""}:
            raise NotificationContractError(
                "Revision metadata is accepted only for definition revision events",
                code="unexpected_definition_revision",
            )
        revision = None
        content_hash = ""

    severity = payload.get("severity") or _default_planning_severity(event_type)
    if severity not in ALLOWED_SEVERITIES:
        raise NotificationContractError("Planning notification severity is invalid")
    reason = _planning_reason(payload.get("reason"))
    created_at = _planning_timestamp(payload.get("created_at"))
    ui_target = _planning_ui_target(
        payload.get("ui_target"),
        project_id=project_id,
        roadmap_id=roadmap_id,
        revision=revision,
    )
    dedupe_key = _planning_dedupe_key(
        event_type=event_type,
        project_id=project_id,
        roadmap_id=roadmap_id,
        revision=revision,
        content_hash=content_hash,
        reason=reason,
        document_view_intent=ui_target.document_view_intent,
    )
    return PlanningNotificationCandidate(
        event_type=event_type,
        project_id=project_id,
        roadmap_id=roadmap_id,
        revision=revision,
        content_hash=content_hash,
        severity=severity,
        reason=reason,
        created_at=created_at,
        ui_target=ui_target,
        dedupe_key=dedupe_key,
    )


def _planning_event_type(value: Any) -> str:
    workspace = notification_workspace_for_event(value)
    if workspace == "agent":
        raise NotificationContractError(
            "Execution notifications are Agent-only and cannot target Planning",
            code="execution_event_agent_only",
        )
    return value


def _planning_id(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not _PLANNING_ID_RE.fullmatch(value):
        raise NotificationContractError(f"Planning {field_name} is invalid")
    return value


def _optional_planning_id(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _planning_id(value, field_name=field_name)


def _planning_revision(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise NotificationContractError(
            "Definition notification requires an exact positive revision",
            code="invalid_definition_revision",
        )
    return value


def _planning_content_hash(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise NotificationContractError(
            "Definition notification requires an exact content hash",
            code="invalid_definition_content_hash",
        )
    return value


def _planning_reason(value: Any) -> str:
    if not isinstance(value, str):
        raise NotificationContractError("Planning notification reason is required")
    text = " ".join(value.split())
    if not text:
        raise NotificationContractError("Planning notification reason is required")
    if len(text) > MAX_PLANNING_REASON_CHARS:
        raise NotificationContractError("Planning notification reason exceeds its budget")
    _assert_planning_safe_values(text)
    return text


def _planning_timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise NotificationContractError("Planning notification created_at is required")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise NotificationContractError("Planning notification created_at is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise NotificationContractError("Planning notification created_at must be UTC")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _planning_ui_target(
    value: Any,
    *,
    project_id: str,
    roadmap_id: str | None,
    revision: int | None,
) -> PlanningNotificationUiTarget:
    if value is None:
        target: Mapping[str, Any] = {}
    elif isinstance(value, Mapping):
        target = value
    else:
        raise NotificationContractError("Planning ui_target must be an object")
    if set(target) - _PLANNING_UI_TARGET_FIELDS:
        raise NotificationContractError("Planning ui_target contains unsupported fields")
    _assert_no_forbidden_keys(target)
    _assert_planning_safe_values(target)
    workspace = target.get("workspace", "planning")
    view = target.get("view", "overview")
    if workspace != "planning" or view != "overview":
        raise NotificationContractError("Planning ui_target workspace/view is invalid")
    default_kind = "roadmap" if roadmap_id else "project"
    highlight_kind = target.get("highlight_kind", default_kind)
    if highlight_kind not in PLANNING_UI_HIGHLIGHT_KINDS:
        raise NotificationContractError("Planning ui_target highlight_kind is invalid")
    refs = {"project": project_id, "roadmap": roadmap_id}
    highlight_id = refs[highlight_kind]
    if highlight_id is None:
        raise NotificationContractError("Planning ui_target highlight ref is missing")
    default_mode = "expand_summary" if highlight_kind == "roadmap" else "focus"
    highlight_mode = target.get("highlight_mode", default_mode)
    if highlight_mode not in PLANNING_UI_HIGHLIGHT_MODES:
        raise NotificationContractError("Planning ui_target highlight_mode is invalid")
    document_intent = target.get("document_view_intent", "none")
    if document_intent not in PLANNING_DOCUMENT_INTENTS:
        raise NotificationContractError("Planning ui_target document_view_intent is invalid")
    if document_intent == "open_roadmap_document" and roadmap_id is None:
        raise NotificationContractError("Roadmap document intent requires roadmap_id")
    return PlanningNotificationUiTarget(
        workspace="planning",
        view="overview",
        project_id=project_id,
        roadmap_id=roadmap_id,
        revision=revision,
        highlight_kind=highlight_kind,
        highlight_id=highlight_id,
        highlight_mode=highlight_mode,
        document_view_intent=document_intent,
    )


def _default_planning_severity(event_type: str) -> str:
    if event_type in {"project_created", "roadmap_created", "roadmap_revision_approved"}:
        return "success"
    if event_type in {"project_deleted", "roadmap_deleted", "roadmap_revision_conflict"}:
        return "warning"
    return "info"


def _planning_dedupe_key(
    *,
    event_type: str,
    project_id: str,
    roadmap_id: str | None,
    revision: int | None,
    content_hash: str,
    reason: str,
    document_view_intent: str,
) -> str:
    canonical = json.dumps(
        {
            "event_type": event_type,
            "project_id": project_id,
            "roadmap_id": roadmap_id,
            "revision": revision,
            "content_hash": content_hash,
            "reason": reason,
            "document_view_intent": document_view_intent,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _assert_planning_safe_values(value: Any) -> None:
    if isinstance(value, Mapping):
        for nested in value.values():
            _assert_planning_safe_values(nested)
        return
    if isinstance(value, (list, tuple)):
        for nested in value:
            _assert_planning_safe_values(nested)
        return
    if not isinstance(value, str):
        return
    if _PRIVATE_PATH_RE.search(value) or _SECRET_VALUE_RE.search(value) or _URL_RE.search(value):
        raise NotificationContractError("Planning notification contains forbidden private or delivery material")
