"""Artifact classification policy for sandbox outputs."""

from __future__ import annotations

import re
from typing import Any, Mapping


class SandboxArtifactPolicyError(ValueError):
    """Raised when an artifact policy record is unsafe."""


def classify_sandbox_artifact(*, artifact_ref: Any, kind: Any, size_bytes: Any = 0) -> dict[str, Any]:
    safe_kind = _kind(kind)
    size = _size(size_bytes)
    retention = "short"
    if safe_kind in {"report", "screenshot"}:
        retention = "medium"
    if safe_kind == "generated_file":
        retention = "review_required"
    payload = {
        "schema": "odysseus.sandbox_artifact_policy.v1",
        "artifact_ref": _artifact_ref(artifact_ref),
        "kind": safe_kind,
        "size_bytes": size,
        "retention": retention,
        "redaction_required": safe_kind in {"log", "report", "generated_file"},
        "raw_content_visible": False,
    }
    _reject_unsafe_payload(payload)
    return payload


def _kind(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text not in {"log", "screenshot", "report", "generated_file"}:
        raise SandboxArtifactPolicyError("unsupported artifact kind")
    return text


def _artifact_ref(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,180}", text) or ".." in text.split("/") or text.startswith("/"):
        raise SandboxArtifactPolicyError("artifact ref is unsafe")
    return text


def _size(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        parsed = 0
    return max(0, min(parsed, 100_000_000))


def _reject_unsafe_payload(payload: Mapping[str, Any]) -> None:
    encoded = repr(payload).lower()
    if any(marker in encoded for marker in ("authorization", "bearer ", "api_key", "password", "cookie", "private raw text")):
        raise SandboxArtifactPolicyError("payload contains forbidden marker")
