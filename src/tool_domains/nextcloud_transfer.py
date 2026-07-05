"""Agent tool surface for review-gated Universal Inbox Nextcloud writes."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
from typing import Any, Dict, Optional

from src.live_affordance_readiness import build_live_affordance_readiness
from src.tool_domains.common import _parse_tool_args
from src.universal_inbox_nextcloud_transfer import (
    UniversalInboxNextcloudTransferRequest,
    execute_universal_inbox_nextcloud_transfer,
)


_DEFAULT_SMOKE_TARGET = "Odysseus/Test/odysseus-universal-inbox-smoke.txt"
_DEFAULT_SMOKE_SIDECAR = "Odysseus/Test/odysseus-universal-inbox-smoke.odysseus.json"


async def do_manage_nextcloud_transfer(content: str, owner: Optional[str] = None) -> Dict[str, Any]:
    """Plan or execute a bounded Universal Inbox copy-only Nextcloud transfer."""

    try:
        args = _parse_tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action") or "").strip().lower()
    if action not in {"readiness", "smoke_plan", "execute"}:
        return {"error": "action must be one of: readiness, smoke_plan, execute", "exit_code": 1}

    if action == "readiness":
        return _readiness(args)
    if action == "smoke_plan":
        return _smoke_plan(args, owner=owner)
    return _execute(args, owner=owner)


def _readiness(args: dict[str, Any]) -> dict[str, Any]:
    packet = build_live_affordance_readiness(env=os.environ)
    nextcloud = next(
        (item for item in packet.get("actions", []) if item.get("action_id") == "nextcloud_copy"),
        {},
    )
    bounded_request_ready = bool(str(args.get("target_path") or "").strip())
    gap_names = list(nextcloud.get("readiness_gap_names") or ())
    if bounded_request_ready:
        gap_names = [name for name in gap_names if name != "bounded_copy_request_required"]
    return {
        "status": "ready" if not gap_names else "blocked",
        "ready": not gap_names,
        "action_id": "nextcloud_copy",
        "readiness_gap_names": gap_names,
        "expected_env_names": [
            "UNIVERSAL_INBOX_NEXTCLOUD_LIVE_WRITE_ENABLED",
            "UNIVERSAL_INBOX_NEXTCLOUD_OPERATOR_LIVE_GO",
            "NEXTCLOUD_WEBDAV_BASE_URL",
            "NEXTCLOUD_WEBDAV_USERNAME",
            "NEXTCLOUD_WEBDAV_APP_PASSWORD",
            "NEXTCLOUD_WEBDAV_ROOT",
        ],
        "password_alias_accepted": False,
        "bounded_request_ready": bounded_request_ready,
        "network_probe_performed": False,
        "nextcloud_write_performed": False,
        "secret_values_visible": False,
        "host_paths_visible": False,
        "raw_content_visible": False,
        "exit_code": 0,
    }


def _smoke_plan(args: dict[str, Any], *, owner: Optional[str]) -> dict[str, Any]:
    target_path = str(args.get("target_path") or _DEFAULT_SMOKE_TARGET).strip()
    sidecar_path = str(args.get("sidecar_path") or _DEFAULT_SMOKE_SIDECAR).strip()
    with _temporary_smoke_file(str(args.get("smoke_text") or "Odysseus Universal Inbox Nextcloud smoke\n")) as source:
        request = UniversalInboxNextcloudTransferRequest(
            source_path=source,
            target_path=target_path,
            sidecar_path=sidecar_path,
            review_approved=True,
            operator_live_go=False,
            dry_run=True,
            actor=_actor(owner),
        )
        result = execute_universal_inbox_nextcloud_transfer(request)
    payload = result.to_dict()
    payload.update(
        {
            "status": "dry_run_ready" if result.status == "dry_run_ready" else result.status,
            "action": "smoke_plan",
            "nextcloud_write_performed": False,
            "network_probe_performed": False,
            "secret_values_visible": False,
            "host_paths_visible": False,
            "raw_content_visible": False,
            "exit_code": 0,
        }
    )
    return payload


def _execute(args: dict[str, Any], *, owner: Optional[str]) -> dict[str, Any]:
    target_path = str(args.get("target_path") or _DEFAULT_SMOKE_TARGET).strip()
    sidecar_path = str(args.get("sidecar_path") or _DEFAULT_SMOKE_SIDECAR).strip()
    dry_run = bool(args.get("dry_run", True))
    review_approved = bool(args.get("review_approved") or args.get("confirmed"))
    operator_live_go = bool(args.get("operator_live_go") or args.get("live_go"))

    if not dry_run and not _server_nextcloud_live_enabled():
        return {
            "status": "blocked",
            "reason": "nextcloud_live_flags_missing",
            "requires_live_go": True,
            "nextcloud_write_performed": False,
            "secret_values_visible": False,
            "host_paths_visible": False,
            "raw_content_visible": False,
            "exit_code": 0,
        }

    source_path = args.get("source_path")
    if source_path:
        return _execute_with_source(
            Path(str(source_path)),
            target_path=target_path,
            sidecar_path=sidecar_path,
            dry_run=dry_run,
            review_approved=review_approved,
            operator_live_go=operator_live_go,
            owner=owner,
        )

    with _temporary_smoke_file(str(args.get("smoke_text") or "Odysseus Universal Inbox Nextcloud smoke\n")) as source:
        return _execute_with_source(
            source,
            target_path=target_path,
            sidecar_path=sidecar_path,
            dry_run=dry_run,
            review_approved=review_approved,
            operator_live_go=operator_live_go,
            owner=owner,
        )


def _execute_with_source(
    source_path: Path,
    *,
    target_path: str,
    sidecar_path: str,
    dry_run: bool,
    review_approved: bool,
    operator_live_go: bool,
    owner: Optional[str],
) -> dict[str, Any]:
    request = UniversalInboxNextcloudTransferRequest(
        source_path=source_path,
        target_path=target_path,
        sidecar_path=sidecar_path,
        review_approved=review_approved,
        operator_live_go=operator_live_go,
        dry_run=dry_run,
        actor=_actor(owner),
    )
    client = None
    try:
        if not dry_run and review_approved and operator_live_go:
            try:
                from src.nextcloud_webdav_client import build_nextcloud_webdav_client_from_env

                client = build_nextcloud_webdav_client_from_env()
            except Exception:
                return {
                    "status": "blocked",
                    "reason": "nextcloud_server_config_missing",
                    "dry_run": True,
                    "writes_performed": False,
                    "nextcloud_write_performed": False,
                    "secret_values_visible": False,
                    "host_paths_visible": False,
                    "raw_content_visible": False,
                    "exit_code": 0,
                }
        result = execute_universal_inbox_nextcloud_transfer(request, client=client)
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    payload = result.to_dict()
    payload.update(
        {
            "action": "execute",
            "nextcloud_write_performed": bool(result.writes_performed),
            "network_probe_performed": bool(result.writes_performed),
            "secret_values_visible": False,
            "host_paths_visible": False,
            "raw_content_visible": False,
            "exit_code": 0,
        }
    )
    return payload


def _server_nextcloud_live_enabled() -> bool:
    return _truthy(os.getenv("UNIVERSAL_INBOX_NEXTCLOUD_LIVE_WRITE_ENABLED")) and _truthy(
        os.getenv("UNIVERSAL_INBOX_NEXTCLOUD_OPERATOR_LIVE_GO")
    )


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "y"}


def _actor(owner: Optional[str]) -> str:
    text = str(owner or "odysseus").strip().lower()
    safe = "".join(ch if ch.isalnum() or ch in "_.:-" else "." for ch in text)
    if not safe or not safe[0].isalpha():
        return "odysseus"
    return safe[:80]


class _temporary_smoke_file:
    def __init__(self, text: str) -> None:
        self.text = text
        self.path: Path | None = None

    def __enter__(self) -> Path:
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", delete=False, suffix=".txt")
        try:
            handle.write(self.text)
            self.path = Path(handle.name)
            return self.path
        finally:
            handle.close()

    def __exit__(self, *_exc: Any) -> None:
        if self.path:
            try:
                self.path.unlink()
            except OSError:
                pass
