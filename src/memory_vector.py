"""
memory_vector.py

ChromaDB-backed vector store for memory entries.
Shares the EmbeddingClient with RAG to save memory.
Stores pre-computed embeddings (ChromaDB does not manage embedding).
"""

import logging
import math
import time
from typing import List, Dict, Optional

from src.ai_lens_events import AiLensRedactionLevel, AiLensSourceKind, AiLensSourceRef
from src.ai_lens_service import opaque_ai_lens_ref

from src.embedding_lanes import (
    LANE_CUSTOM,
    LANE_FASTEMBED,
    build_embedding_lanes,
    collection_name,
    dedupe_results,
    lane_count,
    migrate_legacy_collection,
)

logger = logging.getLogger(__name__)
MAX_AI_LENS_CAPTURE_HITS = 32


def _bounded_capture_count(value, maximum: int = 1_000_000) -> int:
    try:
        return max(0, min(int(value), maximum))
    except (TypeError, ValueError):
        return 0


def _normalized_capture_score(value) -> Optional[float]:
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


class MemoryVectorStore:
    """Vector index over memory entries for semantic retrieval."""

    COLLECTION_NAME = "odysseus_memories"

    def __init__(self, data_dir: str, embedding_model=None, ai_lens_emitter=None):
        self._model = embedding_model
        self._ai_lens_emitter = ai_lens_emitter
        self._ai_lens_capture_errors = 0
        self._ai_lens_emitted_events = 0
        self._collection = None
        self._lanes = []
        self._healthy = False

        self._initialize()

    def _initialize(self):
        try:
            self._lanes = build_embedding_lanes(self.COLLECTION_NAME)
            if not self._lanes:
                raise RuntimeError("No embedding lanes available")

            self._healthy = True
            self._collection = next(
                (lane.collection for lane in self._lanes if lane.name == LANE_FASTEMBED),
                self._lanes[0].collection,
            )
            migrate_legacy_collection(self.COLLECTION_NAME, self._lanes)
            logger.info(
                "MemoryVectorStore ready (lanes=%s entries=%s)",
                [lane.name for lane in self._lanes],
                self.count(),
            )

        except Exception as e:
            logger.error(f"MemoryVectorStore init failed: {e}")

    @property
    def healthy(self) -> bool:
        return self._healthy

    def _embed(self, texts: List[str]) -> List[List[float]]:
        if not self._lanes:
            return []
        return self._lanes[0].encode(texts)

    def count(self) -> int:
        """Return the number of stored vectors."""
        if not self._healthy:
            return 0
        return lane_count(self._lanes)

    def _collections_for_delete(self):
        collections = []
        seen = set()

        def add(collection) -> None:
            if collection is None:
                return
            key = getattr(collection, "name", None) or id(collection)
            if key in seen:
                return
            seen.add(key)
            collections.append(collection)

        for lane in self._lanes:
            add(lane.collection)

        try:
            from src.chroma_client import get_chroma_client

            client = get_chroma_client()
            for lane_name in (LANE_CUSTOM, LANE_FASTEMBED):
                try:
                    add(client.get_collection(collection_name(self.COLLECTION_NAME, lane_name)))
                except Exception:
                    pass
        except Exception:
            pass

        return collections

    def add(self, memory_id: str, text: str):
        """Add a single memory entry to the vector index."""
        if not self._healthy:
            return
        for lane in self._lanes:
            try:
                existing = lane.collection.get(ids=[memory_id])
                if existing["ids"]:
                    continue
                lane.collection.add(
                    ids=[memory_id],
                    embeddings=lane.encode([text]),
                    documents=[text],
                    metadatas=[{"source": "memory"}],
                )
            except Exception as e:
                logger.warning("memory add failed in %s lane for %s: %s", lane.name, memory_id, e)

    def remove(self, memory_id: str):
        """Remove a memory entry. O(1) — no rebuild needed."""
        if not self._healthy:
            return
        for collection in self._collections_for_delete():
            try:
                collection.delete(ids=[memory_id])
            except Exception as e:
                logger.warning(f"memory remove {memory_id}: {e}")

    def search(self, query: str, k: int = 8, *, ai_lens_emitter=None) -> List[Dict]:
        """Search for the most relevant memory IDs by semantic similarity.
        Returns list of {"memory_id": str, "score": float}.

        ChromaDB cosine distance = 1 - cosine_similarity.
        We convert back: similarity = 1.0 - distance.
        """
        emitter = ai_lens_emitter if ai_lens_emitter is not None else getattr(self, "_ai_lens_emitter", None)
        capture_started = time.perf_counter() if emitter is not None else 0.0
        if emitter is not None:
            self._capture_ai_lens(
                emitter,
                event_type="memory_search_started",
                payload={"requested_count": _bounded_capture_count(k)},
                summary="Memory search started with bounded metadata.",
            )
        if not self._healthy or self.count() == 0:
            if emitter is not None:
                self._emit_memory_results(emitter, (), capture_started=capture_started)
            return []

        out = []
        lane_priority = {LANE_CUSTOM: 0, LANE_FASTEMBED: 1}
        for lane in self._lanes:
            try:
                if lane.count() == 0:
                    continue
                results = lane.collection.query(
                    query_embeddings=lane.encode([query]),
                    n_results=min(k, lane.count()),
                    include=["distances"],
                )
                for idx, mid in enumerate(results["ids"][0]):
                    distance = results["distances"][0][idx]
                    out.append({
                        "memory_id": mid,
                        "score": round(1.0 - distance, 4),
                        "embedding_lane": lane.name,
                    })
            except Exception as e:
                logger.warning("memory search failed in %s lane: %s", lane.name, e)
        out.sort(key=lambda row: (-row["score"], lane_priority.get(row["embedding_lane"], 99)))
        selected = dedupe_results(out, id_key="memory_id", limit=k)
        if emitter is not None:
            self._emit_memory_results(emitter, selected, capture_started=capture_started)
        return selected

    def _emit_memory_results(self, emitter, rows, *, capture_started: float) -> None:
        refs = []
        if len(rows) > MAX_AI_LENS_CAPTURE_HITS:
            self._reject_ai_lens_evidence(emitter, "hit_event_budget")
        for rank, row in enumerate(rows[:MAX_AI_LENS_CAPTURE_HITS], start=1):
            identity = row.get("memory_id") if isinstance(row, dict) else None
            if identity in (None, ""):
                self._reject_ai_lens_evidence(emitter, "missing_source_identity")
                continue
            source_ref = AiLensSourceRef.create(
                source_id=opaque_ai_lens_ref("memory", identity),
                kind=AiLensSourceKind.MEMORY,
                redaction_level=AiLensRedactionLevel.REDACTED,
            )
            refs.append(source_ref)
            payload = {"rank": rank}
            score = _normalized_capture_score(row.get("score"))
            if score is None:
                self._reject_ai_lens_evidence(emitter, "invalid_retrieval_score")
            else:
                payload["score"] = score
            self._capture_ai_lens(
                emitter,
                event_type="memory_hit",
                source_refs=(source_ref,),
                payload=payload,
                summary="Memory hit emitted with opaque metadata.",
            )
        latency_ms = _capture_latency_ms(capture_started)
        bounded_refs = tuple(refs[:8])
        self._capture_ai_lens(
            emitter,
            event_type="retrieval_ranking_summary",
            source_refs=bounded_refs,
            payload={
                "ranked_count": _bounded_capture_count(len(rows)),
                "returned_count": _bounded_capture_count(len(rows)),
            },
            summary="Memory ranking completed with bounded counts.",
            latency_ms=latency_ms,
        )
        if refs:
            self._capture_ai_lens(
                emitter,
                event_type="source_coverage_summary",
                source_refs=bounded_refs,
                payload={
                    "source_type_counts": {"memory": len(refs)},
                    "independent_source_count": len({ref.source_id for ref in refs}),
                },
                summary="Memory source coverage contains opaque references only.",
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

    def ai_lens_diagnostics(self) -> Dict:
        return {
            "schema": "odysseus.ai_lens.instrumentation_diagnostics.v1",
            "surface": "memory",
            "emitted_event_count": getattr(self, "_ai_lens_emitted_events", 0),
            "capture_error_count": getattr(self, "_ai_lens_capture_errors", 0),
            "raw_content_visible": False,
        }

    def find_similar(self, text: str, threshold: float = 0.92) -> Optional[str]:
        """Check if a near-duplicate exists. Returns memory_id if found, else None."""
        if not self._healthy or self.count() == 0:
            return None

        for lane in self._lanes:
            try:
                if lane.count() == 0:
                    continue
                results = lane.collection.query(
                    query_embeddings=lane.encode([text]),
                    n_results=1,
                    include=["distances"],
                )
                if results["ids"][0]:
                    distance = results["distances"][0][0]
                    similarity = 1.0 - distance
                    if similarity >= threshold:
                        return results["ids"][0][0]
            except Exception as e:
                logger.warning("memory similarity search failed in %s lane: %s", lane.name, e)
        return None

    def rebuild(self, memories: List[Dict]):
        """Rebuild the entire index from a list of memory entries.
        Each entry must have 'id' and 'text' keys."""
        if not self._healthy:
            return

        from src.chroma_client import get_chroma_client

        client = get_chroma_client()
        lane_names = [
            self.COLLECTION_NAME,
            collection_name(self.COLLECTION_NAME, LANE_CUSTOM),
            collection_name(self.COLLECTION_NAME, LANE_FASTEMBED),
        ]
        for name in lane_names:
            try:
                client.delete_collection(name)
            except Exception:
                pass
        # Explicit rebuilds must start from the supplied memory list, so clear
        # legacy unsuffixed collections too.
        self._lanes = build_embedding_lanes(self.COLLECTION_NAME)
        self._collection = next(
            (lane.collection for lane in self._lanes if lane.name == LANE_FASTEMBED),
            self._lanes[0].collection if self._lanes else None,
        )

        texts = []
        ids = []
        for mem in memories:
            text = mem.get("text", "").strip()
            mid = mem.get("id", "")
            if text and mid:
                texts.append(text)
                ids.append(mid)

        if texts:
            # Batch in chunks of 100 to avoid oversized requests
            failed_lanes = set()
            for i in range(0, len(texts), 100):
                batch_texts = texts[i:i + 100]
                batch_ids = ids[i:i + 100]
                for lane in self._lanes:
                    if lane.name in failed_lanes:
                        continue
                    try:
                        lane.collection.add(
                            ids=batch_ids,
                            embeddings=lane.encode(batch_texts),
                            documents=batch_texts,
                            metadatas=[{"source": "memory"}] * len(batch_ids),
                        )
                    except Exception as e:
                        failed_lanes.add(lane.name)
                        logger.warning("memory rebuild failed in %s lane: %s", lane.name, e)

        logger.info(f"MemoryVectorStore rebuilt with {len(ids)} entries across {len(self._lanes)} lanes")

    def get_stats(self) -> Dict:
        return {
            "healthy": self.healthy,
            "count": self.count(),
            "lanes": [lane.stats() for lane in self._lanes],
        }
