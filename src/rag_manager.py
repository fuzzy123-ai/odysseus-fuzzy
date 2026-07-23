"""
rag_manager.py

A thin wrapper around VectorRAG for backward compatibility and additional features.
"""

import logging
import math
import time
from typing import List, Dict, Any, Optional

from src.constants import CHROMA_DIR
from src.ai_lens_events import AiLensRedactionLevel, AiLensSourceKind, AiLensSourceRef
from src.ai_lens_service import opaque_ai_lens_ref
from src.rag_vector import VectorRAG

logger = logging.getLogger(__name__)
MAX_AI_LENS_CAPTURE_HITS = 32


def _bounded_capture_count(value: Any, maximum: int = 1_000_000) -> int:
    try:
        return max(0, min(int(value), maximum))
    except (TypeError, ValueError):
        return 0


def _normalized_capture_score(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(score) or score < 0.0 or score > 1.0:
        return None
    return round(score, 6)


def _capture_latency_ms(started: float) -> int:
    if started <= 0:
        return 0
    return max(0, min(int((time.perf_counter() - started) * 1000), 86_400_000))


def _rag_result_identity(row: Dict[str, Any]) -> str:
    direct = row.get("id") or row.get("source_ref")
    if direct not in (None, ""):
        return str(direct)
    metadata = row.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    source = metadata.get("source") or metadata.get("document_id")
    if source in (None, ""):
        return ""
    chunk = metadata.get("chunk_id")
    return f"{source}\x1f{chunk}" if chunk not in (None, "") else str(source)

class RAGManager:
    """
    A manager class that wraps VectorRAG for backward compatibility.
    Most methods delegate directly to VectorRAG.
    """
    
    def __init__(self, persist_directory: str = CHROMA_DIR, ai_lens_emitter=None):
        """Initialize the RAGManager with VectorRAG."""
        self.vector_rag = VectorRAG(persist_directory=persist_directory)
        self._ai_lens_emitter = ai_lens_emitter
        self._ai_lens_capture_errors = 0
        self._ai_lens_emitted_events = 0
        logger.info("RAGManager initialized as wrapper for VectorRAG")
    
    # Delegate all methods to VectorRAG
    def search(
        self,
        query: str,
        k: int = 5,
        owner: Optional[str] = None,
        *,
        ai_lens_emitter=None,
    ) -> List[Dict[str, Any]]:
        """Search for documents - delegates to VectorRAG."""
        emitter = ai_lens_emitter if ai_lens_emitter is not None else getattr(self, "_ai_lens_emitter", None)
        if emitter is None:
            return self.vector_rag.search(query, k, owner=owner)
        started = time.perf_counter()
        self._capture_ai_lens(
            emitter,
            event_type="rag_search_started",
            payload={"requested_count": _bounded_capture_count(k)},
            summary="RAG search started with bounded metadata.",
        )
        result = self.vector_rag.search(query, k, owner=owner)
        self._emit_rag_results(emitter, result, capture_started=started)
        return result

    def _emit_rag_results(self, emitter, rows, *, capture_started: float) -> None:
        if not isinstance(rows, (list, tuple)):
            self._reject_ai_lens_evidence(emitter, "invalid_result_shape")
            rows = ()
        refs = []
        if len(rows) > MAX_AI_LENS_CAPTURE_HITS:
            self._reject_ai_lens_evidence(emitter, "hit_event_budget")
        for rank, row in enumerate(rows[:MAX_AI_LENS_CAPTURE_HITS], start=1):
            if not isinstance(row, dict):
                self._reject_ai_lens_evidence(emitter, "invalid_result_row")
                continue
            identity = _rag_result_identity(row)
            if not identity:
                self._reject_ai_lens_evidence(emitter, "missing_source_identity")
                continue
            source_ref = AiLensSourceRef.create(
                source_id=opaque_ai_lens_ref("rag", identity),
                kind=AiLensSourceKind.RAG,
                redaction_level=AiLensRedactionLevel.REDACTED,
            )
            refs.append(source_ref)
            payload = {"rank": rank}
            score = _normalized_capture_score(
                row.get("similarity", row.get("score", row.get("vector_similarity")))
            )
            if score is None:
                self._reject_ai_lens_evidence(emitter, "invalid_retrieval_score")
            else:
                payload["score"] = score
            self._capture_ai_lens(
                emitter,
                event_type="rag_hit",
                source_refs=(source_ref,),
                payload=payload,
                summary="RAG hit emitted with opaque metadata.",
            )
        bounded_refs = tuple(refs[:8])
        self._capture_ai_lens(
            emitter,
            event_type="retrieval_ranking_summary",
            source_refs=bounded_refs,
            payload={
                "ranked_count": _bounded_capture_count(len(rows)),
                "returned_count": _bounded_capture_count(len(rows)),
            },
            summary="RAG ranking completed with bounded counts.",
            latency_ms=_capture_latency_ms(capture_started),
        )
        if refs:
            self._capture_ai_lens(
                emitter,
                event_type="source_coverage_summary",
                source_refs=bounded_refs,
                payload={
                    "source_type_counts": {"rag": len(refs)},
                    "independent_source_count": len({ref.source_id for ref in refs}),
                },
                summary="RAG source coverage contains opaque references only.",
            )

    def _capture_ai_lens(self, emitter, **event) -> None:
        try:
            target = getattr(emitter, "emit", None)
            result = target(**event) if callable(target) else emitter(**event)
        except Exception:
            self._ai_lens_capture_errors = getattr(self, "_ai_lens_capture_errors", 0) + 1
            return
        if result is False:
            self._ai_lens_capture_errors = getattr(self, "_ai_lens_capture_errors", 0) + 1
        else:
            self._ai_lens_emitted_events = getattr(self, "_ai_lens_emitted_events", 0) + 1

    def _reject_ai_lens_evidence(self, emitter, reason_code: str) -> None:
        self._ai_lens_capture_errors = getattr(self, "_ai_lens_capture_errors", 0) + 1
        try:
            callback = getattr(emitter, "record_rejection", None)
            if callable(callback):
                callback(reason_code)
        except Exception:
            pass

    def ai_lens_diagnostics(self) -> Dict[str, Any]:
        return {
            "schema": "odysseus.ai_lens.instrumentation_diagnostics.v1",
            "surface": "rag",
            "emitted_event_count": getattr(self, "_ai_lens_emitted_events", 0),
            "capture_error_count": getattr(self, "_ai_lens_capture_errors", 0),
            "raw_content_visible": False,
        }
    
    def index_personal_documents(
        self,
        directory: str,
        file_extensions: Optional[set] = None,
        owner: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Index documents - delegates to VectorRAG."""
        return self.vector_rag.index_personal_documents(
            directory,
            file_extensions=file_extensions,
            owner=owner,
        )
    
    def retrieve(self, query: str, k: int = 5) -> List[str]:
        """Retrieve relevant chunks - delegates to VectorRAG."""
        return self.vector_rag.retrieve(query, k)
    
    def rebuild_index(self) -> bool:
        """Rebuild index - delegates to VectorRAG."""
        return self.vector_rag.rebuild_index()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get stats - delegates to VectorRAG."""
        return self.vector_rag.get_stats()

    def owner_inventory(self, owner: Optional[str] = None) -> Dict[str, Any]:
        """Get redacted owner-scoped inventory - delegates to VectorRAG."""
        return self.vector_rag.owner_inventory(owner=owner)
    
    def add_document(self, text: str, metadata: Dict[str, Any]) -> bool:
        """Add single document - delegates to VectorRAG."""
        return self.vector_rag.add_document(text, metadata)
    
    def add_documents_batch(self, docs: List[tuple]) -> Dict[str, Any]:
        """Add documents in batch - delegates to VectorRAG."""
        return self.vector_rag.add_documents_batch(docs)
