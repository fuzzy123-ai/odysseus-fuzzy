"""Read-only readiness for live delivery and converter affordances."""

from __future__ import annotations

import os
import shutil
from typing import Any, Callable, Mapping


LIVE_AFFORDANCE_READINESS_SCHEMA = "odysseus.live_affordance_readiness.v1"

_TRUE_VALUES = {"1", "true", "yes", "on", "y"}
_CONVERTER_TOOLS = ("libreoffice", "pandoc", "ffmpeg", "tesseract")


def build_live_affordance_readiness(
    *,
    env: Mapping[str, str] | None = None,
    tool_lookup: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    source_env = env if env is not None else os.environ
    lookup = tool_lookup or shutil.which
    actions = (
        _sandbox_execution(source_env, lookup),
        _telegram_delivery(source_env),
        _nextcloud_copy(source_env),
        _converter_execution(source_env, lookup),
    )
    return {
        "schema": LIVE_AFFORDANCE_READINESS_SCHEMA,
        "status": _overall_status(actions),
        "actions": actions,
        "live_execution_performed": False,
        "network_probe_performed": False,
        "telegram_send_performed": False,
        "nextcloud_write_performed": False,
        "sandbox_execution_performed": False,
        "converter_process_started": False,
        "tokens_visible": False,
        "chat_ids_visible": False,
        "host_paths_visible": False,
        "raw_content_visible": False,
    }


def _sandbox_execution(env: Mapping[str, str], lookup: Callable[[str], str | None]) -> dict[str, Any]:
    gates = (
        _gate("sandbox_worker_route_available", True, "Sandbox worker admin route is registered in the runtime"),
        _gate("podman_available", bool(lookup("podman")), "Podman is discoverable without starting a job"),
        _gate("sandbox_live_enabled_request_required", False, "A concrete request must set live_enabled=true"),
        _gate("operator_live_go_required", False, "A concrete bounded sandbox execution Go is still required"),
        _gate("bounded_sandbox_job_required", False, "A reviewed SandboxJobRequest with no secrets, scoped mounts and resource limits is still required"),
    )
    return _action(
        "sandbox_execution",
        "Sandbox execution",
        "run reviewed agent file, test and terminal jobs in a disposable Podman sandbox",
        gates,
        blocked_live_actions=("podman_pod_create", "podman_run", "sandbox_worker_submit_live"),
    )


def _telegram_delivery(env: Mapping[str, str]) -> dict[str, Any]:
    gates = (
        _gate("telegram_agent_reply_enabled", _truthy(env.get("TELEGRAM_AGENT_REPLY_ENABLED")), "Telegram replies are enabled server-side"),
        _gate("telegram_bot_token_present", _present(env.get("TELEGRAM_BOT_TOKEN")), "Telegram bot token is configured server-side"),
        _gate("telegram_allowed_chats_present", _present(env.get("TELEGRAM_ALLOWED_CHAT_IDS")), "Telegram allowed-chat policy is configured server-side"),
        _gate("operator_live_go_required", False, "A concrete bounded Telegram delivery Go is still required"),
    )
    return _action(
        "telegram_delivery",
        "Telegram file delivery",
        "send exported files back through the server-side Telegram boundary",
        gates,
        blocked_live_actions=("sendDocument", "sendPhoto", "sendAudio", "sendMessage"),
    )


def _nextcloud_copy(env: Mapping[str, str]) -> dict[str, Any]:
    gates = (
        _gate("nextcloud_live_write_enabled", _truthy(env.get("UNIVERSAL_INBOX_NEXTCLOUD_LIVE_WRITE_ENABLED")), "Nextcloud live writes are enabled server-side"),
        _gate("nextcloud_operator_live_go", _truthy(env.get("UNIVERSAL_INBOX_NEXTCLOUD_OPERATOR_LIVE_GO")), "Nextcloud operator live Go is enabled server-side"),
        _gate("nextcloud_webdav_base_present", _present(env.get("NEXTCLOUD_WEBDAV_BASE_URL")), "Nextcloud WebDAV base URL is configured server-side"),
        _gate("nextcloud_webdav_user_present", _present(env.get("NEXTCLOUD_WEBDAV_USERNAME")), "Nextcloud WebDAV user is configured server-side"),
        _gate("nextcloud_webdav_app_password_present", _present(env.get("NEXTCLOUD_WEBDAV_APP_PASSWORD")), "Nextcloud WebDAV app password is configured server-side"),
        _gate("bounded_copy_request_required", False, "A concrete bounded source/target copy request is still required"),
    )
    return _action(
        "nextcloud_copy",
        "Nextcloud copy",
        "copy reviewed export or Inbox artifacts to the configured Nextcloud target",
        gates,
        blocked_live_actions=("webdav_upload", "webdav_mkdir", "webdav_delete"),
    )


def _converter_execution(env: Mapping[str, str], lookup: Callable[[str], str | None]) -> dict[str, Any]:
    tool_gates = tuple(
        _gate(f"{tool}_available", bool(lookup(tool)), f"{tool} is discoverable without running it")
        for tool in _CONVERTER_TOOLS
    )
    gates = (
        _gate("universal_file_io_live_converter_enabled", _truthy(env.get("UNIVERSAL_FILE_IO_LIVE_CONVERTER_ENABLED")), "Universal File IO live converters are enabled server-side"),
        _gate("universal_file_io_operator_live_go", _truthy(env.get("UNIVERSAL_FILE_IO_OPERATOR_LIVE_GO")), "Universal File IO operator live Go is enabled server-side"),
        *tool_gates,
        _gate("bounded_conversion_request_required", False, "A concrete reviewed source and target format are still required"),
    )
    return _action(
        "converter_execution",
        "Converter execution",
        "run local converters for reviewed Universal File IO export plans",
        gates,
        blocked_live_actions=("libreoffice", "pandoc", "ffmpeg", "tesseract"),
    )


def _action(
    action_id: str,
    label: str,
    summary: str,
    gates: tuple[dict[str, Any], ...],
    *,
    blocked_live_actions: tuple[str, ...],
) -> dict[str, Any]:
    missing = tuple(gate["gate_id"] for gate in gates if gate["status"] != "go")
    return {
        "action_id": action_id,
        "label": label,
        "summary": summary,
        "status": "ready" if not missing else "blocked",
        "ready": not missing,
        "gates": gates,
        "readiness_gap_names": missing,
        "blocked_live_actions": blocked_live_actions if missing else (),
        "manual_review_required": True,
        "live_go_required": True,
        "values_visible": False,
    }


def _gate(gate_id: str, ok: bool, summary: str) -> dict[str, Any]:
    return {
        "gate_id": gate_id,
        "status": "go" if ok else "blocked",
        "summary": summary,
        "value_visible": False,
    }


def _overall_status(actions: tuple[dict[str, Any], ...]) -> str:
    if all(action.get("ready") for action in actions):
        return "ready"
    if any(action.get("ready") for action in actions):
        return "partial"
    return "blocked"


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in _TRUE_VALUES


def _present(value: Any) -> bool:
    return bool(str(value or "").strip())
