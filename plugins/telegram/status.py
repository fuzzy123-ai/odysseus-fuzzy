"""Shared Telegram readiness gate objects."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Sequence


_TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class TelegramGateStatus:
    """A redacted, uniform status record for a Telegram readiness gate."""

    name: str
    enabled: bool
    env_names: tuple[str, ...]
    required_for: tuple[str, ...]
    live_action: bool = False
    value_visible: bool = False

    @property
    def state(self) -> str:
        return "enabled" if self.enabled else "disabled"

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "state": self.state,
            "enabled": self.enabled,
            "env_names": list(self.env_names),
            "required_for": list(self.required_for),
            "live_action": self.live_action,
            "value_visible": self.value_visible,
        }


def _bool_env(env: Mapping[str, str], name: str) -> bool:
    return (env.get(name) or "").strip().lower() in _TRUE_VALUES


def _present_env(env: Mapping[str, str], names: Sequence[str]) -> bool:
    return any(bool(env.get(name)) for name in names)


def build_telegram_gate_statuses(env: Mapping[str, str] | None = None) -> dict[str, TelegramGateStatus]:
    """Build redacted Telegram gate statuses from environment markers."""

    source = os.environ if env is None else env
    voice_stt_enabled = _bool_env(source, "TELEGRAM_VOICE_STT_ENABLED") or _bool_env(source, "TELEGRAM_STT_ENABLED")
    return {
        "token": TelegramGateStatus(
            name="token",
            enabled=_present_env(source, ("TELEGRAM_BOT_TOKEN",)),
            env_names=("TELEGRAM_BOT_TOKEN",),
            required_for=("intake", "reply", "polling", "voice_download"),
        ),
        "allowed_chat": TelegramGateStatus(
            name="allowed_chat",
            enabled=_present_env(source, ("TELEGRAM_CHAT_ID", "TELEGRAM_ALLOWED_CHAT_IDS")),
            env_names=("TELEGRAM_CHAT_ID", "TELEGRAM_ALLOWED_CHAT_IDS"),
            required_for=("reply", "chat_allowlist"),
        ),
        "agent_chat": TelegramGateStatus(
            name="agent_chat",
            enabled=_bool_env(source, "TELEGRAM_AGENT_CHAT_ENABLED"),
            env_names=("TELEGRAM_AGENT_CHAT_ENABLED",),
            required_for=("intake", "agent_bridge"),
        ),
        "reply_gate": TelegramGateStatus(
            name="reply_gate",
            enabled=_bool_env(source, "TELEGRAM_AGENT_REPLY_ENABLED"),
            env_names=("TELEGRAM_AGENT_REPLY_ENABLED",),
            required_for=("reply", "document_reply"),
            live_action=True,
        ),
        "polling": TelegramGateStatus(
            name="polling",
            enabled=_bool_env(source, "TELEGRAM_POLLING_ENABLED"),
            env_names=("TELEGRAM_POLLING_ENABLED",),
            required_for=("polling",),
            live_action=True,
        ),
        "rich_messages": TelegramGateStatus(
            name="rich_messages",
            enabled=_bool_env(source, "TELEGRAM_RICH_MESSAGES_ENABLED"),
            env_names=("TELEGRAM_RICH_MESSAGES_ENABLED",),
            required_for=("rich_reply",),
            live_action=True,
        ),
        "rich_drafts": TelegramGateStatus(
            name="rich_drafts",
            enabled=_bool_env(source, "TELEGRAM_RICH_DRAFTS_ENABLED"),
            env_names=("TELEGRAM_RICH_DRAFTS_ENABLED",),
            required_for=("rich_draft",),
            live_action=True,
        ),
        "voice_download": TelegramGateStatus(
            name="voice_download",
            enabled=_bool_env(source, "TELEGRAM_VOICE_DOWNLOAD_ENABLED"),
            env_names=("TELEGRAM_VOICE_DOWNLOAD_ENABLED",),
            required_for=("voice_pipeline",),
            live_action=True,
        ),
        "voice_stt": TelegramGateStatus(
            name="voice_stt",
            enabled=voice_stt_enabled,
            env_names=("TELEGRAM_VOICE_STT_ENABLED", "TELEGRAM_STT_ENABLED"),
            required_for=("voice_pipeline",),
        ),
        "image_actions": TelegramGateStatus(
            name="image_actions",
            enabled=_bool_env(source, "TELEGRAM_IMAGE_ACTIONS_ENABLED"),
            env_names=("TELEGRAM_IMAGE_ACTIONS_ENABLED",),
            required_for=("image_action",),
        ),
        "privacy_pin": TelegramGateStatus(
            name="privacy_pin",
            enabled=not _bool_env(source, "TELEGRAM_PRIVACY_PIN_DISABLED"),
            env_names=("TELEGRAM_PRIVACY_PIN_DISABLED",),
            required_for=("privacy_status_feedback",),
        ),
        "nextcloud_live_write": TelegramGateStatus(
            name="nextcloud_live_write",
            enabled=_bool_env(source, "TELEGRAM_NEXTCLOUD_LIVE_WRITE_ENABLED"),
            env_names=("TELEGRAM_NEXTCLOUD_LIVE_WRITE_ENABLED",),
            required_for=("universal_inbox_nextcloud_transfer",),
            live_action=True,
        ),
        "memory_auto_write": TelegramGateStatus(
            name="memory_auto_write",
            enabled=_bool_env(source, "TELEGRAM_MEMORY_AUTO_WRITE_ENABLED"),
            env_names=("TELEGRAM_MEMORY_AUTO_WRITE_ENABLED",),
            required_for=("universal_inbox_memory_write",),
        ),
    }


def telegram_gate_statuses_to_dict(gates: Mapping[str, TelegramGateStatus]) -> dict[str, dict[str, object]]:
    return {name: gate.to_dict() for name, gate in gates.items()}
