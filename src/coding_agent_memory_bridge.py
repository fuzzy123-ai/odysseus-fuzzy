"""Build Memory/RaptorGraph write intents from coding-agent evidence."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Mapping

from src.memory_candidate_schema import build_memory_candidates_from_synthesis
from src.memory_write_policy import decide_memory_write_policy
from src.raptorgraph_candidate_mapping import map_memory_candidates_to_raptorgraph


class CodingAgentMemoryBridgeError(ValueError):
    """Raised when coding-agent evidence is unsafe for memory candidates."""


def build_coding_agent_memory_write_intent(
    evidence: Mapping[str, Any],
    *,
    model: str,
    dsgvo_mode: bool = False,
    operator_auto_write_enabled: bool = False,
) -> dict[str, Any]:
    if not isinstance(evidence, Mapping):
        raise CodingAgentMemoryBridgeError("evidence must be a mapping")
    _reject_unsafe(evidence)
    title = _safe_text(evidence.get("title") or "Coding task result", field="title", max_len=120)
    summary = _safe_text(evidence.get("summary") or "Coding task completed with sandbox evidence.", field="summary", max_len=500)
    source_refs = _source_refs_from_evidence(evidence)
    synthesis = {
        "source_refs": source_refs,
        "confidence": evidence.get("confidence", 0.82),
        "topics": [{"name": title, "summary": summary}],
    }
    candidates = tuple(
        candidate.to_dict()
        for candidate in build_memory_candidates_from_synthesis(
            synthesis,
            model=model,
            created_by="coding_agent_sandbox_bridge",
            sensitivity=_safe_label(evidence.get("sensitivity") or "project", field="sensitivity"),
            recheck_hint="on_next_task",
        )
    )
    policy = decide_memory_write_policy(
        candidates,
        dsgvo_mode=dsgvo_mode,
        model_route="local_only" if dsgvo_mode else "api_or_local",
        operator_auto_write_enabled=operator_auto_write_enabled,
    )
    mapping = map_memory_candidates_to_raptorgraph(candidates, topic_namespace="coding_agent")
    return {
        "schema": "odysseus.coding_agent.memory_write_intent.v1",
        "candidates": candidates,
        "raptorgraph_mapping": mapping.to_dict(),
        "policy": policy.to_dict(),
        "raw_content_visible": False,
    }


def _source_refs_from_evidence(evidence: Mapping[str, Any]) -> tuple[str, ...]:
    refs: list[str] = []
    for key in ("content_hash", "payload_hash", "evidence_hash"):
        value = str(evidence.get(key) or "").strip().lower()
        if re.fullmatch(r"sha256:[a-f0-9]{16,64}", value):
            refs.append(value)
    for artifact in evidence.get("artifacts") or ():
        if not isinstance(artifact, Mapping):
            continue
        value = str(artifact.get("content_hash") or "").strip().lower()
        if re.fullmatch(r"sha256:[a-f0-9]{16,64}", value):
            refs.append(value)
    if not refs:
        digest = hashlib.sha256(repr(sorted(evidence.items())).encode("utf-8", errors="replace")).hexdigest()
        refs.append("sha256:" + digest)
    return tuple(dict.fromkeys(refs))


def _safe_label(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.:@/-]{1,80}", text):
        raise CodingAgentMemoryBridgeError(f"{field} is unsafe")
    return text


def _safe_text(value: Any, *, field: str, max_len: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        raise CodingAgentMemoryBridgeError(f"{field} must not be empty")
    lowered = text.lower()
    if any(marker in lowered for marker in ("authorization", "bearer ", "api_key", "password", "cookie", "private raw text")):
        raise CodingAgentMemoryBridgeError(f"{field} contains forbidden marker")
    if re.search(r"(^|['\"\\s])([a-z]:[\\/]|/home/|/opt/|/users/|~[\\/])", lowered):
        raise CodingAgentMemoryBridgeError(f"{field} contains host path")
    return text[:max_len]


def _reject_unsafe(value: Any) -> None:
    encoded = repr(value).lower()
    if any(marker in encoded for marker in ("authorization", "bearer ", "api_key", "password", "cookie", "private raw text")):
        raise CodingAgentMemoryBridgeError("evidence contains forbidden marker")
    if re.search(r"(^|['\"\\s])([a-z]:[\\/]|/home/|/opt/|/users/|~[\\/])", encoded):
        raise CodingAgentMemoryBridgeError("evidence contains host path")
    if "raw_content_visible': true" in encoded or '"raw_content_visible": true' in encoded:
        raise CodingAgentMemoryBridgeError("evidence exposes raw content")
