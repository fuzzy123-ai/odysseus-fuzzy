"""Bridge website research packets into Memory and RaptorGraph candidates."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

from src.internal_references import build_internal_reference_dict
from src.web_research_memory_packet import WebResearchMemoryPacket


@dataclass(frozen=True, slots=True)
class WebResearchMemoryIntent:
    status: str
    reason: str
    memory_records: tuple[dict[str, Any], ...]
    raptorgraph_candidates: tuple[dict[str, Any], ...]
    raw_content_visible: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "odysseus.web_research.memory_intent.v1",
            "status": self.status,
            "reason": self.reason,
            "memory_records": self.memory_records,
            "raptorgraph_candidates": self.raptorgraph_candidates,
            "raw_content_visible": False,
        }


def build_web_research_memory_intent(
    *,
    packet: WebResearchMemoryPacket,
    author_model: str,
    reviewed: bool,
    dsgvo_mode: bool = False,
) -> WebResearchMemoryIntent:
    if not isinstance(packet, WebResearchMemoryPacket):
        raise ValueError("packet must be a WebResearchMemoryPacket")
    payload = packet.to_dict()
    memory_id = "web-" + _hash_text(payload["packet_hash"])[-16:]
    record = {
        "schema": "odysseus.web_research.memory_record.v1",
        "memory_id": memory_id,
        "internal_ref": build_internal_reference_dict("memory", memory_id, label="Memory oeffnen"),
        "source": "telegram_web_research",
        "text": payload["summary"],
        "metadata": {
            "topic": payload["topic"],
            "source_refs": payload["source_refs"],
            "confidence": payload["confidence"],
            "gaps": payload["gaps"],
            "author_stamp": {"model": str(author_model or "")[:120], "created_by": "web_research_memory_intent"},
            "dsgvo_mode": bool(dsgvo_mode),
            "raw_content_stored": False,
        },
    }
    raptor_id = "web-rg-" + _hash_text(memory_id + payload["topic"])[-16:]
    candidate = {
        "schema": "odysseus.web_research.raptorgraph_candidate.v1",
        "candidate_id": raptor_id,
        "internal_ref": build_internal_reference_dict("raptor_node", raptor_id, label="Raptor-Kandidat oeffnen"),
        "memory_internal_ref": record["internal_ref"],
        "topic": payload["topic"],
        "source_ref_count": len(payload["source_refs"]),
        "confidence": payload["confidence"],
        "truth_write_allowed": False,
        "raw_content_stored": False,
    }
    if not reviewed:
        return WebResearchMemoryIntent("review", "review_required_before_long_term_write", (), (candidate,))
    return WebResearchMemoryIntent("ready", "reviewed_abstract_research_ready", (record,), (candidate,))


def _hash_text(value: Any) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8", errors="replace")).hexdigest()
