"""Focused capability knowledge packets for Odysseus self-reporting."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Mapping


CODING_AGENT_CAPABILITY_SCHEMA = "odysseus.coding_agent_capability_knowledge.v1"

_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer|chat[_-]?id)\b\s*[:=]?\s*\S*")
_HOST_PATH_RE = re.compile(r"(?i)([a-z]:[\\/]|/home/|/opt/|/users/|~[\\/])")


class ToolCapabilityKnowledgeError(ValueError):
    """Raised when capability knowledge would expose unsafe data."""


def build_coding_agent_capability_knowledge(*, commit: Any = "", generated_at: Any = "") -> dict[str, Any]:
    """Return a metadata-only packet describing current autonomous coding abilities."""

    capabilities = (
        {
            "id": "project_scope_resolution",
            "status": "available",
            "summary": "Resolves project names into repo_id, allowed_paths, checks, branch policy and sandbox policy.",
            "surfaces": ("project_ui", "api", "telegram_monitoring"),
            "live_gate_required": False,
        },
        {
            "id": "sandbox_check_evidence",
            "status": "available",
            "summary": "Dispatches allowlisted checks to dry-run or approved Podman sandbox mode and stores redacted evidence.",
            "surfaces": ("coding_runner", "api"),
            "live_gate_required": True,
        },
        {
            "id": "telegram_runner_controls",
            "status": "available",
            "summary": "Consumes metadata-only Telegram pause, resume and cancel controls for selected coding tasks.",
            "surfaces": ("telegram", "coding_runner"),
            "live_gate_required": False,
        },
        {
            "id": "publish_deploy_operator_gates",
            "status": "available",
            "summary": "Generates commit, push, deploy and Cloudflare handoff plans with explicit operator gates.",
            "surfaces": ("project_runner", "api"),
            "live_gate_required": True,
        },
    )
    live_gates = (
        "autonomous-coding-live-remote-control-go",
        "deploy-live-go",
        "mcp-service-availability",
    )
    packet = {
        "schema": CODING_AGENT_CAPABILITY_SCHEMA,
        "generated_at": _timestamp(generated_at),
        "commit": _safe_label(commit, allow_empty=True),
        "domain": "autonomous_coding_agent",
        "capabilities": capabilities,
        "live_gates": live_gates,
        "summary": (
            "Odysseus can plan workstation-first coding tasks, resolve bounded project scopes, run gated sandbox checks, consume Telegram controls and prepare publish/deploy gates.",
            "Live Telegram smokes, real deployment and Cloudflare exposure still require explicit operator Go.",
        ),
        "raw_content_visible": False,
    }
    packet["fingerprint"] = _fingerprint(packet)
    _assert_safe_packet(packet)
    return packet


def coding_agent_capability_evidence(packet: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a capability packet into safe evidence for memory/RaptorGraph intent."""

    if not isinstance(packet, Mapping):
        raise ToolCapabilityKnowledgeError("packet must be a mapping")
    _assert_safe_packet(packet)
    summaries = tuple(str(item) for item in packet.get("summary") or ())
    evidence = {
        "title": "Autonomous coding capabilities",
        "summary": " ".join(summaries)[:500],
        "content_hash": str(packet.get("fingerprint") or ""),
        "confidence": 0.9,
        "sensitivity": "system",
    }
    _assert_safe_packet(evidence)
    return evidence


def _timestamp(value: Any) -> str:
    text = str(value or "").strip()
    if text and re.fullmatch(r"\d{4}-\d{2}-\d{2}T[0-9:.+-]+Z?", text):
        return text
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_label(value: Any, *, allow_empty: bool = False) -> str:
    text = str(value or "").strip()
    if not text and allow_empty:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_.:@/-]{1,120}", text):
        raise ToolCapabilityKnowledgeError("label is unsafe")
    return text


def _fingerprint(packet: Mapping[str, Any]) -> str:
    encoded = json.dumps({k: v for k, v in packet.items() if k != "fingerprint"}, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8", errors="replace")).hexdigest()


def _assert_safe_packet(packet: Mapping[str, Any]) -> None:
    encoded = json.dumps(packet, ensure_ascii=False, sort_keys=True, default=str)
    if _SECRET_RE.search(encoded):
        raise ToolCapabilityKnowledgeError("capability packet contains forbidden secret marker")
    if _HOST_PATH_RE.search(encoded):
        raise ToolCapabilityKnowledgeError("capability packet contains host path")
    if '"raw_content_visible": true' in encoded.lower():
        raise ToolCapabilityKnowledgeError("capability packet exposes raw content")
