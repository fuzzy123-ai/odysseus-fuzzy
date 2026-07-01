"""Gemma4 local maintenance path for Telegram voice and attachment follow-ups.

The Telegram plugin owns live polling/webhook behavior. This module is a
side-effect-free contract that turns trusted Telegram runtime metadata into a
bounded local Gemma task without persisting voice transcripts, chat IDs, host
paths, attachment bodies, or raw tool output.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import hashlib
import re
from typing import Any, Mapping

from src.gemma4_maintenance_router import (
    GemmaMaintenanceSurface,
    plan_gemma4_maintenance_route,
)
from src.maintenance_model_policy import MaintenanceWorkload


TELEGRAM_LOCAL_PATH_SCHEMA = "odysseus.gemma4_telegram_local_path.v1"
RUNTIME_PACKET_SCHEMA = "odysseus.gemma4_telegram_runtime_packet.v1"

_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9_.:@/-]{1,160}$")
_SECRET_RE = re.compile(
    r"(bearer\s+[a-z0-9._-]{12,}|api[_-]?key|password\s*[:=]|-----BEGIN [A-Z ]*PRIVATE KEY-----)",
    re.IGNORECASE,
)


class Gemma4TelegramLocalPathError(ValueError):
    """Raised when a Telegram local maintenance plan would be unsafe."""


class TelegramLocalMaintenanceKind(StrEnum):
    VOICE_TRANSCRIPT = "voice_transcript"
    RECENT_ATTACHMENT_FOLLOWUP = "recent_attachment_followup"


@dataclass(frozen=True, slots=True)
class TelegramLocalRuntimePacket:
    kind: TelegramLocalMaintenanceKind
    source_ref: str
    bounded_excerpt: str
    recent_attachment: Mapping[str, Any]
    maintenance_route: Mapping[str, Any]
    schema: str = RUNTIME_PACKET_SCHEMA

    def to_runtime_dict(self) -> dict[str, Any]:
        """Runtime-only packet. Callers must not persist this output."""

        return {
            "schema": self.schema,
            "kind": self.kind.value,
            "source_ref": self.source_ref,
            "bounded_excerpt": self.bounded_excerpt,
            "recent_attachment": dict(self.recent_attachment),
            "maintenance_route": dict(self.maintenance_route),
            "raw_content_persistence_allowed": False,
        }


@dataclass(frozen=True, slots=True)
class TelegramLocalMaintenancePlan:
    kind: TelegramLocalMaintenanceKind
    source_ref: str
    transcript_hash: str
    excerpt_hash: str
    input_chars: int
    recent_attachment: Mapping[str, Any]
    maintenance_route: Mapping[str, Any]
    runtime_packet: TelegramLocalRuntimePacket
    schema: str = TELEGRAM_LOCAL_PATH_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "kind": self.kind.value,
            "source_ref": self.source_ref,
            "transcript_hash": self.transcript_hash,
            "excerpt_hash": self.excerpt_hash,
            "input_chars": self.input_chars,
            "recent_attachment": dict(self.recent_attachment),
            "maintenance_route": dict(self.maintenance_route),
            "raw_content_visible": False,
            "raw_content_persisted": False,
            "transcript_visible": False,
            "external_model_may_see_raw": False,
        }


def plan_telegram_gemma4_local_path(
    *,
    kind: TelegramLocalMaintenanceKind | str,
    source_ref: str,
    transcript: str = "",
    followup_text: str = "",
    recent_attachment_context: Mapping[str, Any] | None = None,
    dsgvo_mode: bool = False,
    classification: str = "private",
) -> TelegramLocalMaintenancePlan:
    normalized_kind = _normalize_kind(kind)
    safe_source_ref = _safe_ref(source_ref, field="source_ref")
    recent_attachment = _safe_recent_attachment(recent_attachment_context or {})
    local_only = bool(dsgvo_mode or classification in {"sensitive", "secret"} or recent_attachment.get("local_only_required"))
    bounded_excerpt = _bounded_excerpt(transcript or followup_text)
    workload = (
        MaintenanceWorkload.VOICE_TRANSCRIPT
        if normalized_kind is TelegramLocalMaintenanceKind.VOICE_TRANSCRIPT
        else MaintenanceWorkload.INBOX_TRIAGE
    )
    surface = GemmaMaintenanceSurface.VOICE if normalized_kind is TelegramLocalMaintenanceKind.VOICE_TRANSCRIPT else GemmaMaintenanceSurface.TELEGRAM
    route_plan = plan_gemma4_maintenance_route(
        surface=surface,
        workload=workload,
        classification=classification,
        dsgvo_mode=bool(dsgvo_mode or local_only),
        input_chars=len(bounded_excerpt),
        source_refs=(safe_source_ref, recent_attachment.get("source_ref") or ""),
        excerpt=bounded_excerpt,
        api_escalation_allowed=not local_only,
    )
    route_report = route_plan.flat_route_report()
    runtime_packet = TelegramLocalRuntimePacket(
        kind=normalized_kind,
        source_ref=safe_source_ref,
        bounded_excerpt=bounded_excerpt,
        recent_attachment=recent_attachment,
        maintenance_route=route_report,
    )
    return TelegramLocalMaintenancePlan(
        kind=normalized_kind,
        source_ref=safe_source_ref,
        transcript_hash=_hash_text(transcript),
        excerpt_hash=_hash_text(bounded_excerpt),
        input_chars=len(bounded_excerpt),
        recent_attachment=recent_attachment,
        maintenance_route=route_report,
        runtime_packet=runtime_packet,
    )


def _normalize_kind(kind: TelegramLocalMaintenanceKind | str) -> TelegramLocalMaintenanceKind:
    if isinstance(kind, TelegramLocalMaintenanceKind):
        return kind
    token = str(kind or "").strip().lower().replace("-", "_").replace(" ", "_")
    try:
        return TelegramLocalMaintenanceKind(token)
    except ValueError as exc:
        raise Gemma4TelegramLocalPathError("unsupported kind") from exc


def _safe_recent_attachment(context: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(context, Mapping):
        raise Gemma4TelegramLocalPathError("recent_attachment_context must be a mapping")
    raw_source = context.get("source_ref") or context.get("spool_key") or context.get("source_hash") or ""
    source_ref = _safe_ref(raw_source, field="recent_attachment.source_ref", allow_empty=True)
    return {
        "present": bool(context.get("present", bool(source_ref))),
        "source_ref": source_ref,
        "family": _safe_token(context.get("family") or context.get("attachment_family") or ""),
        "suffix": _safe_suffix(context.get("suffix") or ""),
        "universal_inbox_status": _safe_token(context.get("universal_inbox_status") or context.get("status") or ""),
        "memory_write_intent_status": _safe_token(context.get("memory_write_intent_status") or ""),
        "local_only_required": bool(context.get("local_only_required")),
        "raw_content_visible": False,
    }


def _bounded_excerpt(value: Any, *, limit: int = 1200) -> str:
    text = " ".join(str(value or "").split())
    if _SECRET_RE.search(text):
        raise Gemma4TelegramLocalPathError("excerpt contains secret marker")
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _safe_ref(value: Any, *, field: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        if allow_empty:
            return ""
        raise Gemma4TelegramLocalPathError(f"{field} is required")
    if any(marker in text.lower() for marker in ("token", "password", "api_key", "chat_id", "secret")):
        raise Gemma4TelegramLocalPathError(f"{field} contains forbidden marker")
    if re.search(r"^[A-Za-z]:[\\/]|^/home/|^/Users/|^~[\\/]", text):
        raise Gemma4TelegramLocalPathError(f"{field} must not be a host path")
    if not _SAFE_REF_RE.fullmatch(text):
        return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
    return text[:160]


def _safe_token(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return text if re.fullmatch(r"[a-z0-9_]{0,64}", text) else ""


def _safe_suffix(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if re.fullmatch(r"\.[a-z0-9]{1,12}", text) else ""


def _hash_text(value: Any) -> str:
    return "sha256:" + hashlib.sha256(str(value or "").encode("utf-8", errors="replace")).hexdigest()
