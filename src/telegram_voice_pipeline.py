"""Offline Telegram voice pipeline gates.

This module models the download/STT/agent-turn boundary without calling
Telegram, network services, or an STT provider. Runtime adapters can wire these
decisions later.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Callable, Mapping


@dataclass(frozen=True)
class VoiceDownloadDecision:
    allowed: bool
    status: str
    reason: str
    file_handle: str = ""
    max_bytes: int = 0
    raw_identifiers_visible: bool = False


@dataclass(frozen=True)
class VoiceSttDecision:
    allowed: bool
    status: str
    reason: str
    transcript: str = ""
    raw_identifiers_visible: bool = False


@dataclass(frozen=True)
class VoiceAgentTurn:
    ready_for_agent: bool
    prompt: str
    status: str
    reason: str
    raw_identifiers_visible: bool = False


@dataclass(frozen=True)
class VoiceLocalFileRef:
    ready: bool
    status: str
    reason: str
    local_file_ref: str = ""
    raw_identifiers_visible: bool = False


@dataclass(frozen=True)
class VoiceReplyDecision:
    reply_allowed: bool
    status: str
    reason: str
    reply_text_present: bool = False
    raw_identifiers_visible: bool = False


_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|passwd|authorization|chat[_-]?id|file[_-]?id)\b\s*[:=]\s*[^\s,;]+"
)
_HANDLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def plan_voice_download(
    message: Mapping[str, object],
    *,
    download_enabled: bool = False,
    max_bytes: int = 10_000_000,
) -> VoiceDownloadDecision:
    """Plan a voice download from redacted metadata only."""

    media = message.get("media") if isinstance(message.get("media"), Mapping) else {}
    file_handle = str(media.get("file_handle") or "") if isinstance(media, Mapping) else ""
    file_size = media.get("file_size") if isinstance(media, Mapping) else None
    if not file_handle:
        return VoiceDownloadDecision(False, "download_blocked", "missing_redacted_file_handle")
    if not download_enabled:
        return VoiceDownloadDecision(False, "download_blocked", "download_gate_disabled", file_handle=file_handle)
    if isinstance(file_size, int) and file_size > max_bytes:
        return VoiceDownloadDecision(False, "download_blocked", "voice_file_too_large", file_handle=file_handle, max_bytes=max_bytes)
    return VoiceDownloadDecision(True, "pending_download", "download_planned", file_handle=file_handle, max_bytes=max_bytes)


def build_voice_local_file_ref(
    decision: VoiceDownloadDecision,
    *,
    mime_type: str = "audio/ogg",
) -> VoiceLocalFileRef:
    """Create a deterministic local cache reference without downloading bytes."""

    if not decision.allowed:
        return VoiceLocalFileRef(False, decision.status, decision.reason)
    if not decision.file_handle or not _HANDLE_RE.fullmatch(decision.file_handle):
        return VoiceLocalFileRef(False, "download_blocked", "invalid_redacted_file_handle")
    extension = _extension_for_mime(mime_type)
    digest = hashlib.sha256(f"telegram-voice:{decision.file_handle}".encode("utf-8")).hexdigest()
    return VoiceLocalFileRef(
        True,
        "local_ref_ready",
        "download_target_planned",
        local_file_ref=f"telegram_voice_cache/{digest[:32]}{extension}",
    )


def run_fakeable_stt(
    *,
    local_file_ref: str,
    stt_enabled: bool = False,
    stt_provider: Callable[[str], str] | None = None,
) -> VoiceSttDecision:
    """Run a gated fakeable STT boundary using a local safe file reference."""

    safe_ref = str(local_file_ref or "")
    if not stt_enabled:
        return VoiceSttDecision(False, "pending_stt", "stt_gate_disabled")
    if not safe_ref or safe_ref.startswith(("http://", "https://")):
        return VoiceSttDecision(False, "failed", "invalid_local_file_ref")
    if stt_provider is None:
        return VoiceSttDecision(False, "pending_stt", "stt_provider_missing")
    try:
        transcript = str(stt_provider(safe_ref) or "").strip()
    except Exception:
        return VoiceSttDecision(False, "failed", "stt_provider_failed")
    if not transcript:
        return VoiceSttDecision(False, "failed", "empty_transcript")
    transcript = _redact_transcript(transcript)
    if not transcript:
        return VoiceSttDecision(False, "failed", "empty_transcript")
    return VoiceSttDecision(True, "transcribed", "stt_completed", transcript=transcript)


def build_voice_agent_turn(
    stt: VoiceSttDecision,
    *,
    chat_handle: str,
) -> VoiceAgentTurn:
    """Create an agent prompt only after the transcript boundary succeeds."""

    if not stt.allowed or not stt.transcript:
        return VoiceAgentTurn(False, "", stt.status, stt.reason)
    return VoiceAgentTurn(
        True,
        f"[Telegram voice transcript from {chat_handle}]\n{stt.transcript}",
        "agent_ready",
        "transcript_ready",
    )


def plan_voice_reply(
    turn: VoiceAgentTurn,
    *,
    reply_enabled: bool = False,
    reply_text: str = "",
) -> VoiceReplyDecision:
    """Plan a Telegram text reply after the agent turn without sending it."""

    if not turn.ready_for_agent:
        return VoiceReplyDecision(False, turn.status, turn.reason)
    if not reply_enabled:
        return VoiceReplyDecision(False, "reply_blocked", "reply_gate_disabled")
    if not str(reply_text or "").strip():
        return VoiceReplyDecision(False, "reply_blocked", "reply_text_missing")
    return VoiceReplyDecision(True, "reply_ready", "reply_planned", reply_text_present=True)


def _extension_for_mime(mime_type: str) -> str:
    normalized = str(mime_type or "").strip().lower()
    if normalized == "audio/ogg":
        return ".ogg"
    if normalized in {"audio/mpeg", "audio/mp3"}:
        return ".mp3"
    if normalized == "audio/wav":
        return ".wav"
    return ".bin"


def _redact_transcript(value: str) -> str:
    text = _SECRET_RE.sub("[redacted]", str(value or ""))
    return " ".join(text.split())
