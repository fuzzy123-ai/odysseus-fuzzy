"""Redacted Telegram persistence helpers.

This module owns local JSON state and stable redacted handles only. It must not
perform live Telegram API calls or persist raw chat ids, sender ids, file ids,
tokens, or private provider content.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import re
import tempfile
import time
from errno import EACCES, EINVAL, ENOTSUP, EOPNOTSUPP, EPERM
from pathlib import Path
import threading
from typing import Any, Callable, Mapping

from plugins.telegram.audit_store import TelegramAuditStore
from plugins.telegram.history_privacy import record_has_raw_content
from src.runtime_event_envelope import RuntimeEventEnvelopeError, build_runtime_event, stable_payload_hash
from src.telegram_session_rollover import (
    LedgerError,
    TelegramRolloverLedger,
    chat_handle_ref,
    owner_ref,
)

_HISTORY_FILE = "telegram_history.json"
_AUDIT_FILE = "telegram_audit.json"
_POLLING_FILE = "telegram_polling_state.json"
_SESSION_FILE = "telegram_session_bridge.json"
_PINNED_PRIVACY_FILE = "telegram_privacy_pin_state.json"
_SAFE_EVENT_VALUE_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:@/-")
_LEGACY_STABLE_CHAT_HANDLE_RE = re.compile(r"^chat_[0-9a-f]{12}$")
_LEGACY_RAW_CHAT_KEY_RE = re.compile(r"^-?[1-9][0-9]{0,19}$")


def _stable_handle(prefix: str, value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    digest = hashlib.sha256(f"{prefix}:{raw}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _message_correlation_id(*, chat_handle: str = "", update_id: Any = "", message_id: Any = "") -> str:
    seed = f"{chat_handle}:{update_id}:{message_id}"
    if not seed.strip(":"):
        return ""
    return "telegram:" + hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[:16]


def _runtime_status(status: Any) -> str:
    text = str(status or "").strip().lower()
    if not text:
        return "unknown"
    if "fail" in text or "error" in text:
        return "failed"
    if "block" in text or "denied" in text:
        return "blocked"
    if "pending" in text or "queued" in text or "retry" in text:
        return "queued"
    if text in {"sent", "processed", "transcribed", "bound", "handled", "accepted", "ready", "go", "ok", "poll_ok"}:
        return "success"
    if text in {"skipped", "duplicate_ignored"}:
        return "skipped"
    return "unknown"


def _safe_event_metadata(values: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key, value in values.items():
        if value is None or key in {"text", "reply_text", "chat_id", "token"}:
            continue
        if isinstance(value, bool) or isinstance(value, int):
            metadata[key] = value
            continue
        if isinstance(value, (tuple, list)):
            safe_items = []
            for item in value[:20]:
                text = str(item or "").strip()
                if len(text) <= 120 and all(ch in _SAFE_EVENT_VALUE_CHARS for ch in text):
                    safe_items.append(text)
                else:
                    safe_items.append(stable_payload_hash(text))
            metadata[key] = tuple(safe_items)
            continue
        text = str(value or "").strip()
        if not text:
            continue
        if len(text) <= 120 and all(ch in _SAFE_EVENT_VALUE_CHARS for ch in text):
            metadata[key] = text
        else:
            metadata[f"{key}_hash"] = stable_payload_hash(text)
    return metadata


def _build_telegram_runtime_event(
    *,
    kind: str,
    status: str,
    chat_handle: str = "",
    update_id: Any = "",
    message_id: Any = "",
    direction: str = "",
    component: str = "store",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    correlation_id = _message_correlation_id(
        chat_handle=chat_handle,
        update_id=update_id,
        message_id=message_id,
    )
    metadata = _safe_event_metadata({
        "telegram_status": status,
        "direction": direction,
        **(extra or {}),
    })
    try:
        return build_runtime_event(
            surface="telegram",
            component=component,
            event_type=str(kind or "event"),
            status=_runtime_status(status),
            severity="warn" if _runtime_status(status) in {"blocked", "failed"} else "info",
            owner_scope="telegram",
            correlation_id=correlation_id,
            privacy_level="private_metadata",
            message_ref=_message_correlation_id(
                chat_handle=chat_handle,
                update_id=update_id,
                message_id=message_id,
            ),
            metadata=metadata,
        )
    except RuntimeEventEnvelopeError:
        return {
            "schema": "odysseus.runtime_event.v1",
            "surface": "telegram",
            "component": "store",
            "event_type": "runtime_event_rejected",
            "status": "blocked",
            "severity": "warn",
            "correlation_id": correlation_id,
            "raw_content_visible": False,
        }


def _chat_handle(chat_id: Any) -> str:
    return _stable_handle("chat", chat_id)


def _sender_handle(sender_id: Any) -> str:
    return _stable_handle("sender", sender_id)


def _voice_file_handle(file_id: Any) -> str:
    return _stable_handle("voice_file", file_id)


def _voice_unique_handle(file_unique_id: Any) -> str:
    return _stable_handle("voice_unique", file_unique_id)


def _image_file_handle(file_id: Any) -> str:
    return _stable_handle("image_file", file_id)


def _image_unique_handle(file_unique_id: Any) -> str:
    return _stable_handle("image_unique", file_unique_id)


def _document_file_handle(file_id: Any) -> str:
    return _stable_handle("document_file", file_id)


def _document_unique_handle(file_unique_id: Any) -> str:
    return _stable_handle("document_unique", file_unique_id)


def build_telegram_draft_id(*, chat_id: str, source_message_id: Any = "") -> int:
    digest = hashlib.sha256(f"telegram-draft:{chat_id}:{source_message_id}".encode("utf-8")).hexdigest()
    return int(digest[:12], 16) or 1


def _sanitize_persisted_message(message: dict[str, Any]) -> dict[str, Any]:
    stored = dict(message)
    raw_chat_id = str(stored.pop("chat_id", "") or "")
    if raw_chat_id:
        stored["chat_handle"] = _chat_handle(raw_chat_id)
    sender = stored.get("sender")
    if isinstance(sender, dict):
        sanitized_sender = dict(sender)
        raw_sender_id = sanitized_sender.pop("id", "")
        if raw_sender_id:
            sanitized_sender["handle"] = _sender_handle(raw_sender_id)
        stored["sender"] = sanitized_sender
    media = stored.get("media")
    if isinstance(media, dict):
        sanitized_media = dict(media)
        raw_file_id = sanitized_media.pop("file_id", "")
        raw_unique_id = sanitized_media.pop("file_unique_id", "")
        raw_file_name = sanitized_media.pop("file_name", "")
        if raw_file_name:
            suffix = Path(str(raw_file_name)).suffix.lower()
            if suffix:
                sanitized_media["file_suffix"] = suffix[:16]
        media_type = str(sanitized_media.get("type") or "")
        if raw_file_id:
            if media_type == "image":
                sanitized_media["file_handle"] = _image_file_handle(raw_file_id)
            elif media_type == "document":
                sanitized_media["file_handle"] = _document_file_handle(raw_file_id)
            else:
                sanitized_media["file_handle"] = _voice_file_handle(raw_file_id)
        if raw_unique_id:
            if media_type == "image":
                sanitized_media["file_unique_handle"] = _image_unique_handle(raw_unique_id)
            elif media_type == "document":
                sanitized_media["file_unique_handle"] = _document_unique_handle(raw_unique_id)
            else:
                sanitized_media["file_unique_handle"] = _voice_unique_handle(raw_unique_id)
        stored["media"] = sanitized_media
    stored["chat_id_value_visible"] = False
    stored["sender_id_value_visible"] = False
    stored["voice_file_id_value_visible"] = False
    stored["voice_file_unique_id_value_visible"] = False
    stored["image_file_id_value_visible"] = False
    stored["image_file_unique_id_value_visible"] = False
    stored["document_file_id_value_visible"] = False
    stored["document_file_unique_id_value_visible"] = False
    return stored


class _LegacyTelegramInboxStore:
    """Small JSON store under the plugin data dir."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / _HISTORY_FILE
        self.audit_store = TelegramAuditStore(self.data_dir)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"messages": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"messages": []}
        if not isinstance(data, dict):
            return {"messages": []}
        messages = data.get("messages")
        if not isinstance(messages, list):
            data["messages"] = []
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _append_audit(self, record: Mapping[str, Any]) -> None:
        """Audit persistence is best-effort and never changes legacy-write success."""

        try:
            self.audit_store.append(record, scope_ref=str(record.get("chat_handle") or ""))
        except Exception:
            pass

    def append_event(self, *, kind: str, status: str, chat_id: str = "", **extra: Any) -> dict[str, Any]:
        data = self._read()
        chat_handle = _chat_handle(chat_id)
        event = {
            "direction": "system",
            "kind": kind,
            "status": status,
            "chat_handle": chat_handle,
            "stored_at": int(time.time()),
            "token_value_visible": False,
            "chat_id_value_visible": False,
        }
        event.update({key: value for key, value in extra.items() if value is not None})
        event["correlation_id"] = str(event.get("correlation_id") or _message_correlation_id(
            chat_handle=chat_handle,
            update_id=event.get("update_id"),
            message_id=event.get("message_id"),
        ))
        event["runtime_event"] = _build_telegram_runtime_event(
            kind=kind,
            status=status,
            chat_handle=chat_handle,
            update_id=event.get("update_id"),
            message_id=event.get("message_id"),
            direction="system",
            component="store",
            extra={key: value for key, value in event.items() if key not in {"runtime_event"}},
        )
        event["raw_content_visible"] = record_has_raw_content(event)
        event["raw_content_persisted"] = event["raw_content_visible"]
        event["raw_identifiers_visible"] = False
        data["messages"].append(event)
        self._write(data)
        self._append_audit(event)
        return event

    def append_inbound(self, message: dict[str, Any]) -> dict[str, Any]:
        data = self._read()
        messages = data["messages"]
        raw_chat_id = str(message.get("chat_id") or "")
        key = (message.get("direction"), message.get("update_id"), message.get("message_id"))
        for existing in messages:
            existing_key = (
                existing.get("direction"),
                existing.get("update_id"),
                existing.get("message_id"),
            )
            if existing_key == key:
                retry_pending_voice = (
                    existing.get("kind") == "voice"
                    and existing.get("transcript_status") == "pending_stt"
                    and message.get("kind") == "voice"
                )
                self.append_event(
                    kind="duplicate",
                    status="duplicate_pending_stt_retry" if retry_pending_voice else "duplicate_ignored",
                    chat_id=str(message.get("chat_id") or ""),
                    update_id=message.get("update_id"),
                    message_id=message.get("message_id"),
                )
                return {"stored": False, "message": existing, "retry_pending_voice": retry_pending_voice}

        stored = _sanitize_persisted_message(message)
        stored["stored_at"] = int(time.time())
        stored["correlation_id"] = _message_correlation_id(
            chat_handle=str(stored.get("chat_handle") or ""),
            update_id=stored.get("update_id"),
            message_id=stored.get("message_id"),
        )
        stored["runtime_event"] = _build_telegram_runtime_event(
            kind=str(stored.get("kind") or "inbound"),
            status=str(stored.get("intake_status") or "received"),
            chat_handle=str(stored.get("chat_handle") or ""),
            update_id=stored.get("update_id"),
            message_id=stored.get("message_id"),
            direction="inbound",
            component="inbound",
            extra={
                "kind": stored.get("kind"),
                "intake_status": stored.get("intake_status"),
                "transcript_status": stored.get("transcript_status"),
                "universal_inbox_status": stored.get("universal_inbox_status"),
            },
        )
        stored["raw_content_visible"] = record_has_raw_content(stored)
        stored["raw_content_persisted"] = stored["raw_content_visible"]
        stored["raw_identifiers_visible"] = False
        messages.append(stored)
        self._write(data)
        self._append_audit(stored)
        if stored.get("intake_status") == "blocked_chat":
            self.append_event(
                kind="blocked",
                status="chat_not_allowed",
                chat_id=raw_chat_id,
                update_id=stored.get("update_id"),
                message_id=stored.get("message_id"),
            )
        elif stored.get("kind") == "unsupported":
            self.append_event(
                kind="unsupported",
                status="unsupported_message",
                chat_id=raw_chat_id,
                update_id=stored.get("update_id"),
                message_id=stored.get("message_id"),
            )
        return {"stored": True, "message": stored, "retry_pending_voice": False}

    def update_inbound_status(
        self,
        message: dict[str, Any],
        *,
        transcript_status: str | None = None,
        voice_status: str | None = None,
        intake_status: str | None = None,
        universal_inbox_status: str | None = None,
    ) -> dict[str, Any] | None:
        data = self._read()
        key = (message.get("direction"), message.get("update_id"), message.get("message_id"))
        for existing in data["messages"]:
            existing_key = (
                existing.get("direction"),
                existing.get("update_id"),
                existing.get("message_id"),
            )
            if existing_key != key:
                continue
            if transcript_status is not None:
                existing["transcript_status"] = transcript_status
            if voice_status is not None:
                existing["voice_status"] = voice_status
            if intake_status is not None:
                existing["intake_status"] = intake_status
            if universal_inbox_status is not None:
                existing["universal_inbox_status"] = universal_inbox_status
            existing["updated_at"] = int(time.time())
            self._write(data)
            audit_record = dict(existing)
            audit_record["stored_at"] = existing["updated_at"]
            self._append_audit(audit_record)
            return dict(existing)
        return None

    def append_outbound(
        self,
        chat_id: str,
        text: str,
        *,
        source_message_id: int | None = None,
        delivery_status: str = "sent",
        failure_reason: str | None = None,
        delivery_mode: str = "",
        formatting_mode: str = "",
        truth_gate: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = self._read()
        message = {
            "direction": "outbound",
            "kind": "text",
            "chat_handle": _chat_handle(chat_id),
            "message_id": f"local-{int(time.time() * 1000)}",
            "source_message_id": source_message_id,
            "text": text,
            "delivery_status": delivery_status,
            "failure_reason": failure_reason or "",
            "delivery_mode": delivery_mode,
            "formatting_mode": formatting_mode,
            "stored_at": int(time.time()),
            "token_value_visible": False,
            "chat_id_value_visible": False,
            "raw_rich_payload_visible": False,
        }
        if truth_gate:
            message["truth_gate"] = dict(truth_gate)
        message["correlation_id"] = _message_correlation_id(
            chat_handle=str(message.get("chat_handle") or ""),
            message_id=message.get("source_message_id") or message.get("message_id"),
        )
        message["runtime_event"] = _build_telegram_runtime_event(
            kind="reply_delivery",
            status=delivery_status,
            chat_handle=str(message.get("chat_handle") or ""),
            message_id=message.get("source_message_id") or message.get("message_id"),
            direction="outbound",
            component="reply",
            extra={
                "delivery_status": delivery_status,
                "delivery_mode": delivery_mode,
                "formatting_mode": formatting_mode,
                "failure_reason": failure_reason or "",
                "truth_gate_status": str((truth_gate or {}).get("status") or ""),
                "truth_gate_changed": bool((truth_gate or {}).get("changed") or False),
            },
        )
        message["raw_content_visible"] = record_has_raw_content(message)
        message["raw_content_persisted"] = message["raw_content_visible"]
        message["raw_identifiers_visible"] = False
        data["messages"].append(message)
        self._write(data)
        self._append_audit(message)
        return message

    def history(self, *, chat_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Return internal mixed records for Telegram runtime consumers only."""

        limit = max(1, min(int(limit or 50), 200))
        messages = self._read()["messages"]
        if chat_id:
            chat_handle = _chat_handle(chat_id)
            messages = [
                m for m in messages
                if str(m.get("chat_handle") or "") == chat_handle or str(m.get("chat_id") or "") == str(chat_id)
            ]
        return list(reversed(messages[-limit:]))

    def audit_history(self, *, chat_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """Return closed content-free receipts for admin/diagnostic consumers."""

        scope_ref = _chat_handle(chat_id) if chat_id else None
        return self.audit_store.history(scope_ref=scope_ref, limit=limit)

    def counts(self) -> dict[str, int]:
        messages = self._read()["messages"]
        return {
            "total": len(messages),
            "inbound": sum(1 for m in messages if m.get("direction") == "inbound"),
            "outbound": sum(1 for m in messages if m.get("direction") == "outbound"),
            "voice": sum(1 for m in messages if m.get("kind") == "voice"),
            "image": sum(1 for m in messages if m.get("kind") == "image"),
            "document": sum(1 for m in messages if m.get("kind") == "document"),
            "blocked": sum(1 for m in messages if m.get("kind") == "blocked"),
            "duplicates": sum(1 for m in messages if m.get("kind") == "duplicate"),
            "pending_stt": sum(1 for m in messages if m.get("transcript_status") == "pending_stt"),
            "pending_image_action": sum(1 for m in messages if m.get("image_action_status") == "pending_image_action"),
            "pending_universal_inbox": sum(1 for m in messages if m.get("universal_inbox_status") == "pending_universal_inbox"),
        }

    def last_delivery_summary(self) -> dict[str, Any]:
        for message in reversed(self._read()["messages"]):
            if message.get("direction") == "outbound":
                return {
                    "last_delivery_mode": str(message.get("delivery_mode") or "classic"),
                    "last_delivery_status": str(message.get("delivery_status") or ""),
                    "formatting_mode": str(message.get("formatting_mode") or "plaintext"),
                    "raw_rich_payload_visible": False,
                }
        return {
            "last_delivery_mode": "",
            "last_delivery_status": "",
            "formatting_mode": "html",
            "raw_rich_payload_visible": False,
        }

    def latest_universal_inbox_review(self, *, chat_id: str | None = None) -> dict[str, Any] | None:
        chat_handle = _chat_handle(chat_id) if chat_id else ""
        for message in reversed(self._read()["messages"]):
            if message.get("kind") != "universal_inbox_attachment":
                continue
            if chat_handle and message.get("chat_handle") != chat_handle:
                continue
            status = str(message.get("status") or "")
            inbox_status = str(message.get("universal_inbox_status") or "")
            if status in {"processed", "blocked", "failed"} and inbox_status != "go":
                return dict(message)
        return None

    def latest_universal_inbox_attachment(self, *, chat_id: str | None = None, max_age_seconds: int | None = None) -> dict[str, Any] | None:
        chat_handle = _chat_handle(chat_id) if chat_id else ""
        now = int(time.time())
        max_age = int(max_age_seconds or 0)
        for message in reversed(self._read()["messages"]):
            if message.get("kind") != "universal_inbox_attachment":
                continue
            if chat_handle and message.get("chat_handle") != chat_handle:
                continue
            if max_age > 0 and now - int(message.get("stored_at") or 0) > max_age:
                continue
            return dict(message)
        return None

    def latest_universal_inbox_nextcloud_transfer(
        self,
        *,
        chat_id: str | None = None,
        source_message_id: Any | None = None,
    ) -> dict[str, Any] | None:
        chat_handle = _chat_handle(chat_id) if chat_id else ""
        for message in reversed(self._read()["messages"]):
            if message.get("kind") != "universal_inbox_nextcloud_transfer":
                continue
            if chat_handle and message.get("chat_handle") != chat_handle:
                continue
            if source_message_id is not None and message.get("source_message_id") != source_message_id:
                continue
            return dict(message)
        return None

    def latest_universal_inbox_memory_review(self, *, chat_id: str | None = None) -> dict[str, Any] | None:
        chat_handle = _chat_handle(chat_id) if chat_id else ""
        for message in reversed(self._read()["messages"]):
            if message.get("kind") != "universal_inbox_attachment":
                continue
            if chat_handle and message.get("chat_handle") != chat_handle:
                continue
            status = str(message.get("memory_write_intent_status") or "")
            if status in {"review", "ready"}:
                return dict(message)
        return None

    def latest_project_intake_review(self, *, chat_id: str | None = None) -> dict[str, Any] | None:
        chat_handle = _chat_handle(chat_id) if chat_id else ""
        for message in reversed(self._read()["messages"]):
            if message.get("kind") != "project_intake_review":
                continue
            if chat_handle and message.get("chat_handle") != chat_handle:
                continue
            status = str(message.get("status") or "")
            if status in {"confirmed", "held"}:
                return None
            if status in {"review", "blocked"}:
                return dict(message)
        return None


class TelegramRolloverBridgeError(ValueError):
    """Deterministic, content-free DB-aware bridge refusal."""


class DbAuthoritativeTelegramSessionBridge:
    """Explicit owner-injected adapter for the A3 DB-authoritative bridge.

    It is intentionally not wired into ``TelegramSessionBridgeStore`` or any
    productive call site.  Callers own the SQLAlchemy transaction and supply a
    stable chat handle for DB lookups/projection; HMAC references are never
    reversed into handles.
    """

    def __init__(
        self,
        *,
        database: Any,
        owner: str,
        reference_key: bytes,
        legacy_path: str | Path,
        rollover_local_day: str,
    ):
        try:
            self._ledger = TelegramRolloverLedger(database, reference_key)
            # Verify/initialize the key fingerprint before deriving *any* HMAC
            # reference from the supplied key.  A mismatched key must fail
            # without turning caller identity into a derived reference.
            self._ledger.verify_reference_key()
            self._owner_ref = owner_ref(reference_key, owner)
        except (LedgerError, ValueError) as error:
            raise TelegramRolloverBridgeError("invalid_bridge_identity") from error
        if not _is_iso_local_day(rollover_local_day):
            raise TelegramRolloverBridgeError("invalid_rollover_local_day")
        self._reference_key = reference_key
        self._legacy_path = Path(legacy_path)
        self._rollover_local_day = rollover_local_day

    def resolve(self, *, stable_chat_handle: str, scope: str) -> dict[str, Any] | None:
        """Read one DB binding; JSON is never consulted on this path."""

        _require_stable_chat_handle(stable_chat_handle)
        _require_scope(scope)
        try:
            binding = self._ledger.get_binding_for_identity(
                owner_reference=self._owner_ref,
                chat_reference=chat_handle_ref(self._reference_key, stable_chat_handle),
                scope=scope,
            )
        except LedgerError as error:
            raise TelegramRolloverBridgeError("db_binding_unavailable") from error
        if binding is None:
            return None
        return {
            "binding_id": binding.id,
            "scope": binding.scope,
            "active_session_id": binding.active_session_id,
            "generation": binding.generation,
            "source": "database",
            "raw_identity_visible": False,
        }

    def import_legacy_once(self) -> tuple[dict[str, Any], ...]:
        """Import each missing owner/chat/scope legacy slot exactly once.

        The entire source is parsed and Session ownership is prevalidated before
        a binding write, so malformed source cannot result in a partial import.
        """

        try:
            legacy = _read_legacy_bridge_strict(self._legacy_path)
        except TelegramRolloverBridgeError:
            # A durable binding is authoritative even if an old compatibility
            # file is no longer parseable.  There are no trustworthy legacy
            # candidates to fill, so leave the source untouched.
            try:
                if self._ledger.list_bindings_for_owner(owner_reference=self._owner_ref):
                    return ()
            except LedgerError as error:
                raise TelegramRolloverBridgeError("db_binding_unavailable") from error
            raise
        candidates = _legacy_slot_candidates(legacy)
        missing: list[tuple[str, str, str]] = []
        try:
            for stable_handle, scope, session_id in candidates:
                if self._ledger.get_binding_for_identity(
                    owner_reference=self._owner_ref,
                    chat_reference=chat_handle_ref(self._reference_key, stable_handle),
                    scope=scope,
                ) is None:
                    missing.append((stable_handle, scope, session_id))
        except LedgerError as error:
            raise TelegramRolloverBridgeError("db_binding_unavailable") from error
        _validate_legacy_sessions(
            self._ledger, self._owner_ref, tuple(missing)
        )
        imported: list[dict[str, Any]] = []
        try:
            # The ledger helper anchors a physical SQLite BEGIN before the
            # SAVEPOINT.  This keeps every newly-created slot rollbackable by
            # the caller if any later candidate is rejected.
            with self._ledger.begin_nested_transaction():
                for stable_handle, scope, session_id in missing:
                    chat_reference = chat_handle_ref(self._reference_key, stable_handle)
                    existing = self._ledger.get_binding_for_identity(
                        owner_reference=self._owner_ref,
                        chat_reference=chat_reference,
                        scope=scope,
                    )
                    if existing is not None:
                        continue
                    binding = self._ledger.get_or_create_binding(
                        owner_reference=self._owner_ref,
                        chat_reference=chat_reference,
                        scope=scope,
                        active_session_id=session_id,
                        active_rollover_local_day=self._rollover_local_day,
                    )
                    # Imported bindings have no durable compatibility
                    # projection until the atomic file projection succeeds.
                    binding = self._ledger.set_projection_status(
                        binding_id=binding.id,
                        expected_generation=binding.generation,
                        status="stale",
                    )
                    imported.append({
                        "binding_id": binding.id,
                        "stable_chat_handle": stable_handle,
                        "scope": scope,
                        "generation": binding.generation,
                        "raw_identity_visible": False,
                    })
        except LedgerError as error:
            raise TelegramRolloverBridgeError("legacy_import_blocked") from error
        return tuple(imported)

    def project_compatibility(self, *, stable_handle_by_binding: Mapping[str, str]) -> dict[str, Any]:
        """Atomically project explicit stable handles, never opaque HMAC refs."""

        if not isinstance(stable_handle_by_binding, Mapping):
            raise TelegramRolloverBridgeError("invalid_projection_mapping")
        try:
            self._ledger.begin_projection_transaction()
            bindings = self._ledger.list_bindings_for_owner(owner_reference=self._owner_ref)
        except LedgerError as error:
            raise TelegramRolloverBridgeError("projection_transaction_unavailable") from error
        handles: dict[str, str] = {}
        for binding in bindings:
            handle = stable_handle_by_binding.get(binding.id)
            if not isinstance(handle, str) or not _is_stable_chat_handle(handle):
                raise TelegramRolloverBridgeError("stable_handle_mapping_required")
            if chat_handle_ref(self._reference_key, handle) != binding.chat_handle_ref:
                raise TelegramRolloverBridgeError("stable_handle_mapping_mismatch")
            handles[binding.id] = handle
        if set(stable_handle_by_binding) != set(handles):
            raise TelegramRolloverBridgeError("stable_handle_mapping_required")
        conflicting_chat_references: set[str] = set()
        for binding in bindings:
            try:
                conflicts = self._ledger.list_bindings_for_chat_reference(
                    chat_reference=binding.chat_handle_ref
                )
            except LedgerError as error:
                raise TelegramRolloverBridgeError("db_binding_unavailable") from error
            if any(item.owner_ref != self._owner_ref for item in conflicts):
                conflicting_chat_references.add(binding.chat_handle_ref)
        if conflicting_chat_references:
            try:
                with self._ledger.begin_nested_transaction():
                    for owned in bindings:
                        if owned.chat_handle_ref not in conflicting_chat_references:
                            continue
                        self._ledger.set_projection_status(
                            binding_id=owned.id,
                            expected_generation=owned.generation,
                            status="blocked_multi_owner",
                        )
            except LedgerError as error:
                raise TelegramRolloverBridgeError("projection_blocked") from error
            return {"status": "blocked_multi_owner", "written": False, "raw_identity_visible": False}
        sessions: dict[str, dict[str, Any]] = {}
        for binding in bindings:
            handle = handles[binding.id]
            mapping = sessions.setdefault(handle, {
                "chat_handle": handle,
                "normal_session_id": "",
                "secure_session_id": "",
            })
            mapping[f"{binding.scope}_session_id"] = binding.active_session_id
        for mapping in sessions.values():
            selected = "normal" if mapping["normal_session_id"] else "secure"
            mapping["last_selected_scope"] = selected
            mapping["active_session_id"] = mapping[f"{selected}_session_id"]
            mapping["session_id"] = mapping["active_session_id"]
            mapping["session_slots"] = {
                "normal": bool(mapping["normal_session_id"]),
                "secure": bool(mapping["secure_session_id"]),
            }
        _atomic_write_json(self._legacy_path, {"sessions": sessions})
        try:
            with self._ledger.begin_nested_transaction():
                for binding in bindings:
                    self._ledger.set_projection_status(
                        binding_id=binding.id,
                        expected_generation=binding.generation,
                        status="current",
                    )
        except LedgerError as error:
            raise TelegramRolloverBridgeError("projection_status_unavailable") from error
        return {"status": "current", "written": True, "binding_count": len(bindings), "raw_identity_visible": False}


def _is_stable_chat_handle(value: Any) -> bool:
    return isinstance(value, str) and _LEGACY_STABLE_CHAT_HANDLE_RE.fullmatch(value) is not None


def _require_stable_chat_handle(value: Any) -> None:
    if not _is_stable_chat_handle(value):
        raise TelegramRolloverBridgeError("invalid_stable_chat_handle")


def _require_scope(value: Any) -> None:
    if value not in {"normal", "secure"}:
        raise TelegramRolloverBridgeError("invalid_scope")


def _is_iso_local_day(value: Any) -> bool:
    try:
        from datetime import date
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except (TypeError, ValueError):
        return False


def _read_legacy_bridge_strict(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        return {"sessions": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise TelegramRolloverBridgeError("legacy_json_invalid") from error
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), dict):
        raise TelegramRolloverBridgeError("legacy_json_invalid")
    return data


def _legacy_slot_candidates(legacy: Mapping[str, Any]) -> tuple[tuple[str, str, str], ...]:
    candidates: dict[tuple[str, str], str] = {}
    for raw_key, mapping in legacy["sessions"].items():
        if not isinstance(raw_key, str) or not isinstance(mapping, dict):
            raise TelegramRolloverBridgeError("legacy_mapping_invalid")
        supplied_handle = mapping.get("chat_handle")
        if supplied_handle is not None:
            if not _is_stable_chat_handle(supplied_handle):
                raise TelegramRolloverBridgeError("legacy_mapping_invalid")
            handle = supplied_handle
        elif _is_stable_chat_handle(raw_key):
            handle = raw_key
        elif _LEGACY_RAW_CHAT_KEY_RE.fullmatch(raw_key):
            handle = _chat_handle(raw_key)
        else:
            raise TelegramRolloverBridgeError("legacy_mapping_invalid")
        normal = mapping.get("normal_session_id")
        secure = mapping.get("secure_session_id")
        # Match TelegramSessionBridgeStore normalization: an old single
        # ``session_id`` remains the normal slot whenever both scoped fields
        # are absent *or normalize to empty strings*.
        if not str(normal or "").strip() and not str(secure or "").strip():
            normal = mapping.get("session_id")
        for scope, session_id in (("normal", normal), ("secure", secure)):
            if session_id is None or (isinstance(session_id, str) and not session_id.strip()):
                continue
            if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 512:
                raise TelegramRolloverBridgeError("legacy_mapping_invalid")
            identity = (handle, scope)
            normalized_session_id = session_id.strip()
            previous = candidates.get(identity)
            if previous is not None and previous != normalized_session_id:
                raise TelegramRolloverBridgeError("legacy_mapping_invalid")
            candidates[identity] = normalized_session_id
    return tuple((handle, scope, session_id) for (handle, scope), session_id in candidates.items())


def _validate_legacy_sessions(
    ledger: TelegramRolloverLedger,
    owner_reference: str,
    candidates: tuple[tuple[str, str, str], ...],
) -> None:
    for _handle, _scope, session_id in candidates:
        try:
            if not ledger.session_belongs_to_owner(
                session_id=session_id, owner_reference=owner_reference
            ):
                raise TelegramRolloverBridgeError("legacy_session_invalid")
        except LedgerError as error:
            raise TelegramRolloverBridgeError("legacy_session_invalid") from error


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    descriptor: int | None = None
    temporary: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        _fsync_parent_directory(path.parent)
    except Exception as error:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            if temporary is not None:
                os.unlink(temporary)
        except OSError:
            pass
        raise TelegramRolloverBridgeError("compatibility_projection_failed") from error


def _fsync_parent_directory(path: Path) -> None:
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY)
        os.fsync(descriptor)
    except OSError as error:
        if _directory_fsync_unsupported(error):
            return
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _directory_fsync_unsupported(error: OSError) -> bool:
    """Recognize only documented platform-level directory-fsync gaps."""

    if error.errno in {EINVAL, ENOTSUP, EOPNOTSUPP}:
        return True
    return os.name == "nt" and error.errno in {EACCES, EPERM}


class TelegramPollingStateStore:
    """Small JSON store for polling offsets and dry-run state."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / _POLLING_FILE

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"offset": 0, "last_status": "idle", "history": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"offset": 0, "last_status": "idle", "history": []}
        if not isinstance(data, dict):
            return {"offset": 0, "last_status": "idle", "history": []}
        if not isinstance(data.get("history"), list):
            data["history"] = []
        data["offset"] = int(data.get("offset") or 0)
        data["last_status"] = str(data.get("last_status") or "idle")
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_offset(self) -> int:
        return self._read()["offset"]

    def record(self, *, offset: int | None = None, status: str, **extra: Any) -> dict[str, Any]:
        data = self._read()
        if offset is not None:
            data["offset"] = int(offset)
        data["last_status"] = status
        event = {"status": status, "recorded_at": int(time.time())}
        event.update({key: value for key, value in extra.items() if value is not None})
        data["history"].append(event)
        self._write(data)
        return event


class TelegramPrivacyPinStore:
    """Persist DSGVO pinned-message state without raw Telegram chat ids."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / _PINNED_PRIVACY_FILE

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"pins": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {"pins": {}}
        if not isinstance(data, dict):
            return {"pins": {}}
        pins = data.get("pins")
        if not isinstance(pins, dict):
            data["pins"] = {}
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def get_pin(self, chat_id: str) -> dict[str, Any] | None:
        handle = _chat_handle(chat_id)
        if not handle:
            return None
        pin = self._read()["pins"].get(handle)
        return dict(pin) if isinstance(pin, dict) else None

    def set_pin(self, chat_id: str, message_id: int) -> dict[str, Any]:
        handle = _chat_handle(chat_id)
        if not handle:
            raise ValueError("telegram chat id is missing")
        if int(message_id or 0) <= 0:
            raise ValueError("telegram message id is missing")
        data = self._read()
        pin = {
            "chat_handle": handle,
            "message_id": int(message_id),
            "pinned_at": int(time.time()),
            "token_value_visible": False,
            "chat_id_value_visible": False,
            "raw_chat_id_value_visible": False,
        }
        data["pins"][handle] = pin
        self._write(data)
        return dict(pin)

    def clear_pin(self, chat_id: str) -> dict[str, Any] | None:
        handle = _chat_handle(chat_id)
        if not handle:
            return None
        data = self._read()
        pin = data["pins"].pop(handle, None)
        self._write(data)
        return dict(pin) if isinstance(pin, dict) else None

    def active_count(self) -> int:
        return len(self._read()["pins"])


class TelegramSessionBridgeStore:
    """Persist chat->session bindings without creating a second runtime."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / _SESSION_FILE
        self._lock = _session_bridge_lock(self.path)

    @contextmanager
    def exclusive(self):
        """Serialize a multi-step bridge transaction for this state file."""

        with self._lock:
            yield

    def _read(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return {"sessions": {}}
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                return {"sessions": {}}
            if not isinstance(data, dict):
                return {"sessions": {}}
            sessions = data.get("sessions")
            if not isinstance(sessions, dict):
                data["sessions"] = {}
            return data

    def _write(self, data: dict[str, Any]) -> None:
        with self._lock:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(
                f".{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            try:
                temporary.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                temporary.replace(self.path)
            finally:
                if temporary.exists():
                    temporary.unlink()

    def get(self, chat_id: str) -> dict[str, Any] | None:
        with self._lock:
            data = self._read()
            sessions = data["sessions"]
            mapping = sessions.get(_chat_handle(chat_id)) or sessions.get(str(chat_id))
            return self._normalize_mapping(mapping) if isinstance(mapping, dict) else None

    def replace_mapping(self, chat_id: str, mapping: Mapping[str, Any]) -> dict[str, Any]:
        """Atomically replace one redacted chat mapping."""

        with self._lock:
            data = self._read()
            handle = _chat_handle(chat_id)
            normalized = self._normalize_mapping(dict(mapping)) or {}
            normalized["chat_handle"] = handle
            normalized["session_alias"] = f"telegram:{handle}"
            data["sessions"].pop(str(chat_id), None)
            data["sessions"][handle] = normalized
            self._write(data)
            return dict(normalized)

    def _normalize_scope(self, scope: str | None) -> str:
        return "secure" if str(scope or "").strip().lower() == "secure" else "normal"

    def _normalize_mapping(self, mapping: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(mapping, dict):
            return None
        normalized = dict(mapping)
        legacy_session_id = str(normalized.get("session_id") or "").strip()
        normal_session_id = str(normalized.get("normal_session_id") or "").strip()
        secure_session_id = str(normalized.get("secure_session_id") or "").strip()
        active_session_id = str(normalized.get("active_session_id") or "").strip()
        selected_scope = self._normalize_scope(str(normalized.get("last_selected_scope") or ""))
        if legacy_session_id and not normal_session_id and not secure_session_id:
            normal_session_id = legacy_session_id
            selected_scope = "normal"
        if selected_scope == "secure" and secure_session_id:
            active_session_id = secure_session_id
        elif normal_session_id:
            selected_scope = "normal"
            active_session_id = normal_session_id
        elif secure_session_id:
            selected_scope = "secure"
            active_session_id = secure_session_id
        normalized["normal_session_id"] = normal_session_id
        normalized["secure_session_id"] = secure_session_id
        normalized["active_session_id"] = active_session_id
        normalized["session_id"] = active_session_id
        normalized["last_selected_scope"] = selected_scope
        normalized["session_slots"] = {
            "normal": bool(normal_session_id),
            "secure": bool(secure_session_id),
        }
        return normalized

    def bind_chat(
        self,
        *,
        chat_id: str,
        session_alias: str,
        recommended_session_name: str,
        scope: str = "normal",
        creator: Callable[..., Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            return self._bind_chat_locked(
                chat_id=chat_id,
                session_alias=session_alias,
                recommended_session_name=recommended_session_name,
                scope=scope,
                creator=creator,
            )

    def _bind_chat_locked(
        self,
        *,
        chat_id: str,
        session_alias: str,
        recommended_session_name: str,
        scope: str = "normal",
        creator: Callable[..., Any] | None = None,
    ) -> dict[str, Any]:
        data = self._read()
        sessions = data["sessions"]
        handle = _chat_handle(chat_id)
        safe_session_alias = f"telegram:{handle}" if handle else session_alias
        selected_scope = self._normalize_scope(scope)
        existing = self._normalize_mapping(sessions.get(handle) or sessions.get(str(chat_id)))
        slot_key = f"{selected_scope}_session_id"
        if isinstance(existing, dict) and existing.get(slot_key):
            existing["active_session_id"] = str(existing.get(slot_key) or "")
            existing["session_id"] = existing["active_session_id"]
            existing["last_selected_scope"] = selected_scope
            existing["session_alias"] = safe_session_alias
            existing["recommended_session_name"] = recommended_session_name
            if str(chat_id) in sessions and handle not in sessions:
                sessions[handle] = dict(existing)
                sessions.pop(str(chat_id), None)
            existing["last_seen_at"] = int(time.time())
            sessions[handle] = existing
            self._write(data)
            return {"session_id": existing["session_id"], "created": False, "mapping": dict(existing)}

        session_id = None
        if creator is not None:
            created = creator(
                chat_id=str(chat_id),
                session_alias=session_alias,
                recommended_session_name=recommended_session_name,
                session_scope=selected_scope,
                local_only_required=selected_scope == "secure",
            )
            if isinstance(created, dict):
                session_id = created.get("session_id") or created.get("id")
            else:
                session_id = created
        now = int(time.time())
        mapping = existing or {
            "chat_handle": handle,
            "session_alias": safe_session_alias,
            "recommended_session_name": recommended_session_name,
            "created_at": now,
        }
        mapping["chat_handle"] = handle
        mapping["session_alias"] = safe_session_alias
        mapping["recommended_session_name"] = recommended_session_name
        mapping[slot_key] = str(session_id or "")
        mapping["active_session_id"] = str(session_id or "")
        mapping["session_id"] = str(session_id or "")
        mapping["last_selected_scope"] = selected_scope
        mapping["last_seen_at"] = now
        mapping["session_slots"] = {
            "normal": bool(mapping.get("normal_session_id")),
            "secure": bool(mapping.get("secure_session_id")),
        }
        sessions.pop(str(chat_id), None)
        sessions[handle] = mapping
        self._write(data)
        return {"session_id": mapping["session_id"], "created": bool(session_id), "mapping": dict(mapping)}

    def rebind_chat(
        self,
        *,
        chat_id: str,
        session_alias: str,
        recommended_session_name: str,
        scope: str = "normal",
        creator: Callable[..., Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            return self._rebind_chat_locked(
                chat_id=chat_id,
                session_alias=session_alias,
                recommended_session_name=recommended_session_name,
                scope=scope,
                creator=creator,
            )

    def _rebind_chat_locked(
        self,
        *,
        chat_id: str,
        session_alias: str,
        recommended_session_name: str,
        scope: str = "normal",
        creator: Callable[..., Any] | None = None,
    ) -> dict[str, Any]:
        data = self._read()
        sessions = data["sessions"]
        handle = _chat_handle(chat_id)
        safe_session_alias = f"telegram:{handle}" if handle else session_alias
        selected_scope = self._normalize_scope(scope)
        slot_key = f"{selected_scope}_session_id"
        existing = self._normalize_mapping(sessions.get(handle) or sessions.get(str(chat_id))) or {}
        session_id = None
        if creator is not None:
            created = creator(
                chat_id=str(chat_id),
                session_alias=session_alias,
                recommended_session_name=recommended_session_name,
                session_scope=selected_scope,
                local_only_required=selected_scope == "secure",
            )
            if isinstance(created, dict):
                session_id = created.get("session_id") or created.get("id")
            else:
                session_id = created
        mapping = {
            **existing,
            "chat_handle": handle,
            "session_alias": safe_session_alias,
            "recommended_session_name": recommended_session_name,
            "last_seen_at": int(time.time()),
            "rebound_at": int(time.time()),
        }
        mapping.setdefault("created_at", int(time.time()))
        mapping[slot_key] = str(session_id or "")
        mapping["active_session_id"] = str(session_id or "")
        mapping["session_id"] = str(session_id or "")
        mapping["last_selected_scope"] = selected_scope
        mapping["session_slots"] = {
            "normal": bool(mapping.get("normal_session_id")),
            "secure": bool(mapping.get("secure_session_id")),
        }
        sessions.pop(str(chat_id), None)
        sessions[handle] = mapping
        self._write(data)
        return {"session_id": mapping["session_id"], "created": bool(session_id), "mapping": dict(mapping)}
