#!/usr/bin/env python3
"""Emit a fixed, presence-only projection of homeserver runtime credentials.

The host process never requests Podman's Config.Env metadata. A fixed Python
program runs inside the selected container, converts expected credentials to
booleans, and emits a bounded schema. The host validates and reserializes only
that schema; raw stdout, stderr, values, unknown fields, and exception text are
never forwarded.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from typing import Any, Callable, Sequence


HOST_SCHEMA_ID = "odysseus.homeserver.redacted_runtime_probe.v1"
CONTAINER_SCHEMA_ID = "odysseus.homeserver.credential_presence.v1"
DEFAULT_CONTAINER = "odysseus_odysseus_1"
DEFAULT_TIMEOUT_SECONDS = 15
MAX_ENVIRONMENT_ENTRIES = 4096
CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

EXPECTED_CREDENTIAL_KEYS = (
    "DATA_BRAVE_API_KEY",
    "EMBEDDING_API_KEY",
    "GH_TOKEN",
    "GITHUB_TOKEN",
    "GOOGLE_API_KEY",
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
    "NEXTCLOUD_WEBDAV_APP_PASSWORD",
    "ODYSSEUS_ADMIN_PASSWORD",
    "ODYSSEUS_INTERNAL_TOKEN",
    "OPENAI_API_KEY",
    "SERPER_API_KEY",
    "TAVILY_API_KEY",
    "TELEGRAM_BOT_TOKEN",
)

SENSITIVE_KEY_MARKERS = (
    "API_KEY",
    "AUTHORIZATION",
    "CHAT_ID",
    "CREDENTIAL",
    "PASSWORD",
    "PRIVATE_KEY",
    "SECRET",
    "TOKEN",
)

_TELEGRAM_READINESS_KEYS = (
    "opaque_target_configured",
    "agent_reply_enabled",
    "send_ready",
    "raw_target_visible",
    "secret_values_visible",
)

_TELEGRAM_READINESS_ENV_KEYS = (
    "TELEGRAM_NOTIFICATION_CHAT_ID",
    "TELEGRAM_ALLOWED_CHAT_IDS",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_AGENT_REPLY_ENABLED",
)

_CONTAINER_PROGRAM = """\
import json
import os

expected = %r
markers = %r
readiness_keys = %r
keys = tuple(os.environ)
known = set(expected) | set(readiness_keys)
notification_target = (os.environ.get("TELEGRAM_NOTIFICATION_CHAT_ID") or "").strip()
allowed_targets = (os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS") or "").strip()
fallback_target = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
target_configured = bool(
    notification_target
    if notification_target
    else allowed_targets.split(",", 1)[0].strip()
    if allowed_targets
    else fallback_target
)
agent_reply_enabled = os.environ.get("TELEGRAM_AGENT_REPLY_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
bot_token_configured = bool((os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip())
payload = {
    "schema_id": %r,
    "environment_entry_count": len(keys),
    "credential_presence": {name: bool(os.environ.get(name)) for name in expected},
    "unknown_sensitive_key_count": sum(
        1
        for name in keys
        if name not in known and any(marker in name.upper() for marker in markers)
    ),
    "telegram_delivery_readiness": {
        "opaque_target_configured": target_configured,
        "agent_reply_enabled": agent_reply_enabled,
        "send_ready": target_configured and agent_reply_enabled and bot_token_configured,
        "raw_target_visible": False,
        "secret_values_visible": False,
    },
}
print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
""" % (EXPECTED_CREDENTIAL_KEYS, SENSITIVE_KEY_MARKERS, _TELEGRAM_READINESS_ENV_KEYS, CONTAINER_SCHEMA_ID)


def _blocked(error_code: str) -> dict[str, Any]:
    return {
        "schema_id": HOST_SCHEMA_ID,
        "status": "blocked",
        "error_code": error_code,
        "raw_environment_visible": False,
        "secret_values_visible": False,
        "telegram_delivery_readiness": _blocked_telegram_readiness(),
    }


def _blocked_telegram_readiness() -> dict[str, bool]:
    return {
        "opaque_target_configured": False,
        "agent_reply_enabled": False,
        "send_ready": False,
        "raw_target_visible": False,
        "secret_values_visible": False,
    }


def build_probe_command(container: str) -> list[str]:
    if not CONTAINER_NAME_RE.fullmatch(container):
        raise ValueError("invalid_container_name")
    return ["podman", "exec", container, "python", "-I", "-c", _CONTAINER_PROGRAM]


def _bounded_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("invalid_count")
    if not 0 <= value <= MAX_ENVIRONMENT_ENTRIES:
        raise ValueError("invalid_count")
    return value


def parse_container_projection(raw: str, *, container: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid_probe_payload") from exc
    if (
        not isinstance(payload, dict)
        or set(payload)
        != {
            "schema_id",
            "environment_entry_count",
            "credential_presence",
            "unknown_sensitive_key_count",
            "telegram_delivery_readiness",
        }
        or payload.get("schema_id") != CONTAINER_SCHEMA_ID
    ):
        raise ValueError("invalid_probe_payload")

    raw_presence = payload.get("credential_presence")
    if not isinstance(raw_presence, dict) or set(raw_presence) != set(
        EXPECTED_CREDENTIAL_KEYS
    ):
        raise ValueError("invalid_probe_payload")
    if any(type(raw_presence[name]) is not bool for name in EXPECTED_CREDENTIAL_KEYS):
        raise ValueError("invalid_probe_payload")
    readiness = _telegram_readiness(
        payload.get("telegram_delivery_readiness"),
        bot_token_configured=raw_presence["TELEGRAM_BOT_TOKEN"],
    )

    return {
        "schema_id": HOST_SCHEMA_ID,
        "status": "ok",
        "container": container,
        "container_running": True,
        "environment_entry_count": _bounded_int(
            payload.get("environment_entry_count")
        ),
        "credential_presence": {
            name: raw_presence[name] for name in EXPECTED_CREDENTIAL_KEYS
        },
        "unknown_sensitive_key_count": _bounded_int(
            payload.get("unknown_sensitive_key_count")
        ),
        "raw_environment_visible": False,
        "secret_values_visible": False,
        "telegram_delivery_readiness": readiness,
    }


def _telegram_readiness(
    value: Any,
    *,
    bot_token_configured: bool,
) -> dict[str, bool]:
    if (
        not isinstance(value, dict)
        or set(value) != set(_TELEGRAM_READINESS_KEYS)
        or type(bot_token_configured) is not bool
    ):
        raise ValueError("invalid_probe_payload")
    if any(type(value[key]) is not bool for key in _TELEGRAM_READINESS_KEYS):
        raise ValueError("invalid_probe_payload")
    if value["raw_target_visible"] is not False or value["secret_values_visible"] is not False:
        raise ValueError("invalid_probe_payload")
    if value["send_ready"] != (
        value["opaque_target_configured"]
        and value["agent_reply_enabled"]
        and bot_token_configured
    ):
        raise ValueError("invalid_probe_payload")
    return {key: value[key] for key in _TELEGRAM_READINESS_KEYS}


def collect_runtime_projection(
    *,
    container: str = DEFAULT_CONTAINER,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    try:
        command = build_probe_command(container)
    except ValueError:
        return _blocked("invalid_container_name")

    try:
        result = runner(
            command,
            capture_output=True,
            text=True,
            timeout=DEFAULT_TIMEOUT_SECONDS,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return _blocked("podman_unavailable")
    except subprocess.TimeoutExpired:
        return _blocked("container_probe_timeout")
    except Exception:
        return _blocked("container_probe_internal_error")

    if result.returncode != 0:
        return _blocked("container_probe_failed")
    try:
        return parse_container_projection(result.stdout, container=container)
    except ValueError:
        return _blocked("invalid_probe_payload")


def _self_check() -> dict[str, Any]:
    presence = {name: False for name in EXPECTED_CREDENTIAL_KEYS}
    projection = parse_container_projection(
        json.dumps(
            {
                "schema_id": CONTAINER_SCHEMA_ID,
                "environment_entry_count": len(presence),
                "credential_presence": presence,
                "unknown_sensitive_key_count": 0,
                "telegram_delivery_readiness": _blocked_telegram_readiness(),
            }
        ),
        container=DEFAULT_CONTAINER,
    )
    return {
        "schema_id": HOST_SCHEMA_ID,
        "status": "passed"
        if projection["status"] == "ok"
        and projection["secret_values_visible"] is False
        and projection["raw_environment_visible"] is False
        and projection["telegram_delivery_readiness"] == _blocked_telegram_readiness()
        else "failed",
        "secret_values_visible": False,
        "raw_environment_visible": False,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Emit presence-only Debian runtime credential readiness."
    )
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--self-check", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    projection = _self_check() if args.self_check else collect_runtime_projection(
        container=args.container
    )
    print(json.dumps(projection, sort_keys=True, separators=(",", ":")))
    return 0 if projection.get("status") in {"ok", "passed"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
