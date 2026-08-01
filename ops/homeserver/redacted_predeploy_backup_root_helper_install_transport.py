#!/usr/bin/env python3
"""Pinned, inert preparation transport for the root-helper installation.

This module can obtain only the two exact published blobs.  It deliberately
has no remote-execution path: a future host installation requires its own
action-specific live-go and rollback packet.
"""
from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from typing import Any, Callable, Mapping


SCHEMA_ID = "odysseus.predeploy_backup_root_helper_install_transport.v1"
PUBLISHED_REF = "refs/remotes/fuzzy/dev"
INSTALLER_PATH = "ops/homeserver/redacted_predeploy_backup_root_helper_install.py"
HELPER_PATH = "ops/homeserver/redacted_predeploy_backup_root_helper.py"
READBACK_PATH = "ops/homeserver/redacted_predeploy_backup_root_helper_readback.py"
PUBLISHED_INSTALLER_SHA256 = "e8c631ec14879bf8328983e7e06d91000eb47bdf34bb940b6e218932f58f4e00"
PUBLISHED_HELPER_SHA256 = "dbcbac4c5a4b65edcc4d4facd9204674a8e9114179f406c88d067c7f96185a97"
PUBLISHED_READBACK_SHA256 = "8201653d392d1556a81f6ca236e9f5dd94b6d425dc03ae699f74280b4ae9b722"
_HEX = __import__("re").compile(r"^[0-9a-f]{64}$")
_KEYS = frozenset({"schema_id", "status", "error_code", "installation_invoked", "retry_permitted", "raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible", "evidence_sha256"})


def _digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps({k: v for k, v in value.items() if k != "evidence_sha256"}, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("ascii")).hexdigest()


def blocked(code: str) -> dict[str, Any]:
    value = {"schema_id": SCHEMA_ID, "status": "blocked", "error_code": code if code in {"invalid_invocation", "published_blob_mismatch", "live_go_required"} else "published_blob_mismatch", "installation_invoked": False, "retry_permitted": False, "raw_output_visible": False, "environment_visible": False, "paths_visible": False, "secret_values_visible": False}
    value["evidence_sha256"] = _digest(value)
    return value


def validate_envelope(value: Any) -> bool:
    return bool(type(value) is dict and set(value) == _KEYS and value.get("schema_id") == SCHEMA_ID and value.get("status") == "blocked" and value.get("error_code") in {"invalid_invocation", "published_blob_mismatch", "live_go_required"} and value.get("installation_invoked") is False and value.get("retry_permitted") is False and all(value.get(item) is False for item in ("raw_output_visible", "environment_visible", "paths_visible", "secret_values_visible")) and isinstance(value.get("evidence_sha256"), str) and value["evidence_sha256"] == _digest(value))


def _published_blob(path: str, digest: str, runner: Callable[..., Any]) -> bytes | None:
    if not _HEX.fullmatch(digest): return None
    try:
        result = runner(["git", "cat-file", "blob", f"{PUBLISHED_REF}:{path}"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=False, timeout=5, check=False, shell=False)
        data = getattr(result, "stdout", None)
        return data if getattr(result, "returncode", None) == 0 and type(data) is bytes and 0 < len(data) <= 400000 and hashlib.sha256(data).hexdigest() == digest else None
    except Exception: return None


def prepare_published_install_bundle(*, runner: Callable[..., Any] = subprocess.run) -> dict[str, str] | None:
    """Return only base64 blobs and their predeclared hashes; never execute them."""
    installer = _published_blob(INSTALLER_PATH, PUBLISHED_INSTALLER_SHA256, runner)
    helper = _published_blob(HELPER_PATH, PUBLISHED_HELPER_SHA256, runner)
    readback = _published_blob(READBACK_PATH, PUBLISHED_READBACK_SHA256, runner)
    if installer is None or helper is None or readback is None: return None
    return {"installer_sha256": PUBLISHED_INSTALLER_SHA256, "installer_source": base64.b64encode(installer).decode("ascii"), "helper_sha256": PUBLISHED_HELPER_SHA256, "helper_source": base64.b64encode(helper).decode("ascii"), "readback_sha256": PUBLISHED_READBACK_SHA256, "readback_source": base64.b64encode(readback).decode("ascii")}


def request_installation(*, execute: bool = False, runner: Callable[..., Any] = subprocess.run) -> dict[str, Any]:
    # Deliberately no effectful branch: this release only prepares a pinned
    # package and leaves host installation behind an independent live packet.
    if execute is not True: return blocked("invalid_invocation")
    return blocked("live_go_required") if prepare_published_install_bundle(runner=runner) is not None else blocked("published_blob_mismatch")


def main() -> int:
    print(json.dumps(blocked("invalid_invocation"), ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 1


if __name__ == "__main__": raise SystemExit(main())
