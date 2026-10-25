"""Harbour Memory Bounded Context Service Façade.

Consolidates the Unified Source Index (USI) adapter registry, FTS5 chunk query,
fallback structural census, and context manifest generation.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from src.unified_source_index_contract import (
    Classification,
    ContentPolicy,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
    ChunkRecord,
    TextRangeLocator,
    content_hash,
)
from src.unified_source_index_source_registry import SourceAdapterRegistry

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _sanitize_fts_query(query: str) -> str:
    """Safely format raw user query text into a valid SQLite FTS5 expression."""
    tokens = _TOKEN_RE.findall(query)
    if not tokens:
        clean = query.replace('"', '""')
        return f'"{clean}"'
    return " AND ".join(f'"{token}"*' for token in tokens)


def _extract_semantic_chunks(
    rel_path: str,
    text: str,
    *,
    max_chunk_chars: int = 1200,
    min_chunk_chars: int = 20,
) -> list[tuple[int, int, str]]:
    """Partition text into semantically coherent, bounded chunks with exact character spans."""
    clean = text.strip()
    if not clean:
        return []

    # 1. Specialized semantic chunking for roadmap & planning JSON
    if rel_path.endswith(".json"):
        try:
            data = json.loads(text)
            if isinstance(data, dict) and ("plan_id" in data or "slices" in data or "completed_features" in data or "features" in data):
                chunks: list[tuple[int, int, str]] = []
                plan_id = data.get("plan_id", "")
                title = data.get("title", "")
                goal = data.get("goal", "")
                status = data.get("status", "")
                overview_lines = [f"Plan: {plan_id}", f"Title: {title}"]
                if goal:
                    overview_lines.append(f"Goal: {goal}")
                if status:
                    overview_lines.append(f"Status: {status}")
                overview_text = "\n".join(overview_lines)
                chunks.append((0, len(overview_text), overview_text))

                raw_features = data.get("features")
                if not isinstance(raw_features, list) or not raw_features:
                    raw_features = data.get("completed_features")
                if not isinstance(raw_features, list) or not raw_features:
                    raw_features = data.get("slices")
                features = raw_features if isinstance(raw_features, list) else []

                offset = len(overview_text) + 2
                for item in features:
                    if isinstance(item, dict):
                        node_id = item.get("node_id") or item.get("slice_id") or item.get("id", "")
                        feat_status = item.get("status", "")
                        evidence = item.get("evidence", "") or item.get("description", "")
                        item_text = f"Feature: {node_id} | Status: {feat_status}\nDescription: {evidence}".strip()
                        if item_text:
                            chunks.append((offset, offset + len(item_text), item_text))
                            offset += len(item_text) + 2
                if chunks:
                    return chunks
        except Exception:
            pass

    # 2. General section & paragraph splitting for markdown and documents
    if re.search(r"(?:^|\n)#{1,4}\s", text):
        pattern = re.compile(r"(?:^|\n)(?=#{1,4}\s)")
    else:
        pattern = re.compile(r"(?:\n\s*\n)+")
    matches = list(pattern.finditer(text))

    spans: list[tuple[int, int]] = []
    prev = 0
    for m in matches:
        start = m.start()
        if start > prev:
            spans.append((prev, start))
        prev = m.end()
    if prev < len(text):
        spans.append((prev, len(text)))

    result: list[tuple[int, int, str]] = []
    for start, end in spans:
        raw_segment = text[start:end]
        stripped = raw_segment.strip()
        if not stripped or len(stripped) < min_chunk_chars:
            continue

        if len(stripped) <= max_chunk_chars:
            s_offset = text.find(stripped, start)
            if s_offset == -1:
                s_offset = start
            e_offset = s_offset + len(stripped)
            result.append((s_offset, e_offset, stripped))
        else:
            sub_start = start
            paragraphs = re.split(r"(?<=[.!?\n])\s+", raw_segment)
            current_sub: list[str] = []
            current_len = 0
            for p in paragraphs:
                p_str = p.strip()
                if not p_str:
                    continue
                if current_len + len(p_str) + 1 > max_chunk_chars and current_sub:
                    combined = " ".join(current_sub).strip()
                    if combined:
                        c_start = text.find(combined, sub_start)
                        if c_start == -1:
                            c_start = sub_start
                        c_end = c_start + len(combined)
                        result.append((c_start, c_end, combined))
                        sub_start = c_end
                    current_sub = [p_str]
                    current_len = len(p_str)
                else:
                    current_sub.append(p_str)
                    current_len += len(p_str) + 1
            if current_sub:
                combined = " ".join(current_sub).strip()
                if combined:
                    c_start = text.find(combined, sub_start)
                    if c_start == -1:
                        c_start = sub_start
                    c_end = c_start + len(combined)
                    result.append((c_start, c_end, combined))

    if not result and clean:
        t = clean[:max_chunk_chars]
        result.append((0, len(t), t))

    return result


class HarbourMemoryServiceError(ValueError):
    """Raised when an operation within HarbourMemoryService fails."""

    __slots__ = ("code", "detail")

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def default_source_adapter_registry() -> SourceAdapterRegistry:
    """Build the deterministic default SourceAdapterRegistry with 3 approved USI sources.

    Approved sources:
    - planning.approved_sources (planning composite)
    - memory.accepted (accepted personal memory)
    - knowledge.native (harbour knowledge)
    """
    from src.unified_source_index_sources.planning_composite import planning_composite_source_registration
    from src.unified_source_index_sources.memory import memory_source_registration
    from src.unified_source_index_sources.knowledge import knowledge_source_registration

    return SourceAdapterRegistry((
        planning_composite_source_registration(),
        memory_source_registration(),
        knowledge_source_registration(),
    ))


class HarbourMemoryService:
    """Façade for Harbour Memory Bounded Context and Unified Source Index."""

    __slots__ = ("_repo_root", "_registry", "_store")

    def __init__(
        self,
        repo_root: str | Path | None = None,
        store: Any = None,
        registry: SourceAdapterRegistry | None = None,
    ) -> None:
        self._repo_root = Path(repo_root).resolve() if repo_root else Path.cwd().resolve()
        self._registry = registry if registry is not None else default_source_adapter_registry()

        if store is None:
            db_path = self._repo_root / "data" / "usi.sqlite3"
            if db_path.is_file():
                try:
                    from src.unified_source_index_sqlite import SQLiteUnifiedSourceIndexStore
                    store = SQLiteUnifiedSourceIndexStore(db_path)
                except Exception:
                    store = None
        self._store = store

    @property
    def repo_root(self) -> Path:
        """Repository root directory."""
        return self._repo_root

    @property
    def registry(self) -> SourceAdapterRegistry:
        """The source adapter registry used by this service."""
        return self._registry

    @property
    def store(self) -> Any:
        """Active SQLiteUnifiedSourceIndexStore instance, or None if unavailable."""
        return self._store

    def _has_chunks(self) -> bool:
        """Check whether the active store contains at least one indexed chunk."""
        if self._store is None:
            return False
        if hasattr(self._store, "has_chunks") and callable(self._store.has_chunks):
            return bool(self._store.has_chunks())
        if hasattr(self._store, "_connect") and callable(self._store._connect):
            try:
                conn = self._store._connect()
                try:
                    row = conn.execute("SELECT 1 FROM usi_chunks LIMIT 1").fetchone()
                    return row is not None
                finally:
                    conn.close()
            except Exception:
                return False
        return True

    def query_usi(
        self,
        query: str,
        limit: int = 10,
        *,
        owner_scope: str = "owner:admin",
    ) -> dict[str, Any]:
        """Query Unified Source Index via FTS5 BM25 chunk search or structural census fallback.

        Validates query bounds and returns schema 'odysseus.harbour.memory_query_result.v1'.
        """
        if not isinstance(query, str) or not query.strip():
            raise HarbourMemoryServiceError("invalid_params", "query must be a non-empty string")
        if len(query) > 1000:
            raise HarbourMemoryServiceError("invalid_params", "query exceeds maximum length of 1000 characters")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 100:
            raise HarbourMemoryServiceError("invalid_params", "limit must be between 1 and 100")
        if not isinstance(owner_scope, str) or not owner_scope.strip():
            raise HarbourMemoryServiceError("invalid_params", "owner_scope must be a non-empty string")

        q = query.strip()

        # Primary path: active SQLite store with chunks
        if self._has_chunks():
            try:
                fts_query = _sanitize_fts_query(q)
                hits = self._store.search_chunks(owner_scope=owner_scope, query=fts_query, limit=limit)
                matches = []
                for hit in hits:
                    rec = hit.item.record
                    snippet = (rec.content or "")[:240]
                    matches.append({
                        "source_id": rec.source_id,
                        "chunk_id": rec.chunk_id,
                        "snippet": snippet,
                        "rank": float(hit.rank),
                    })
                return {
                    "schema": "odysseus.harbour.memory_query_result.v1",
                    "query": q,
                    "limit": limit,
                    "total_matches": len(matches),
                    "matches": matches,
                }
            except HarbourMemoryServiceError:
                raise
            except Exception as exc:
                raise HarbourMemoryServiceError("query_failed", f"USI chunk search failed: {exc}") from exc

        # Fallback path: deterministic structural census
        from src.planning_roadmap_migration import build_h1r03_structural_census

        try:
            census, _ = build_h1r03_structural_census(self._repo_root)
        except Exception:
            census = []
        q_lower = q.lower()
        matches = []
        for r in census:
            if (
                q_lower in r.get("path", "").lower()
                or q_lower in r.get("source_id", "").lower()
                or q_lower in r.get("kind", "").lower()
            ):
                matches.append({
                    "source_id": r.get("source_id"),
                    "path": r.get("path"),
                    "kind": r.get("kind"),
                    "structural_role": r.get("structural_role"),
                })
                if len(matches) >= limit:
                    break

        return {
            "schema": "odysseus.harbour.memory_query_result.v1",
            "query": q,
            "limit": limit,
            "total_matches": len(matches),
            "matches": matches,
        }

    def get_context_manifest(self, session_id: str | None = None) -> dict[str, Any]:
        """Produce the combined context manifest with registered adapter manifests."""
        if session_id is not None and not isinstance(session_id, str):
            raise HarbourMemoryServiceError("invalid_params", "session_id must be a string")
        if session_id is not None and len(session_id) > 256:
            raise HarbourMemoryServiceError("invalid_params", "session_id is too long")

        manifests = [m.to_dict() for m in self._registry.manifests()]
        return {
            "schema": "odysseus.harbour.context_manifest.v1",
            "manifest_version": "1.0.0",
            "session_id": session_id or "default-harbour-session",
            "memory_domain": {
                "usi_status": "ready",
                "active_sources": ["planning", "specs", "docs"],
            },
            "planning_domain": {
                "status": "synchronized",
                "scope": "mcp:harbour:context:read",
            },
            "bounded": True,
            "registered_adapters": manifests,
        }

    def get_runtime_status(self) -> dict[str, Any]:
        """Project current USI runtime status deterministically."""
        from src.unified_source_index_runtime import build_knowledge_runtime
        from src.unified_source_index_runtime_config import UnifiedSourceIndexRuntimeConfig
        from src.unified_source_index_job_runner import build_inert_cooperative_index_job_pump
        from src.unified_source_index_runtime_status import project_unified_source_index_runtime_status

        config = UnifiedSourceIndexRuntimeConfig.from_environment()
        kr = build_knowledge_runtime((config.mode.value, config.runtime_enabled))
        pump = build_inert_cooperative_index_job_pump(config)
        return project_unified_source_index_runtime_status(kr, pump)

    def _ensure_store(self) -> Any:
        """Ensure an active SQLiteUnifiedSourceIndexStore exists, creating data/usi.sqlite3 if needed."""
        if self._store is not None:
            return self._store
        db_path = self._repo_root / "data" / "usi.sqlite3"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            from src.unified_source_index_sqlite import SQLiteUnifiedSourceIndexStore
            self._store = SQLiteUnifiedSourceIndexStore(db_path)
            return self._store
        except Exception as exc:
            raise HarbourMemoryServiceError(
                "store_init_failed",
                f"Failed to initialize SQLiteUnifiedSourceIndexStore at {db_path}: {exc}",
            ) from exc

    def _commit_records(self, records: Sequence[Any]) -> None:
        """Atomically commit a batch of USI records to the active store."""
        if not records:
            return
        if hasattr(self._store, "store_chunks") and callable(self._store.store_chunks):
            self._store.store_chunks(records)
        elif hasattr(self._store, "insert_chunks") and callable(self._store.insert_chunks):
            self._store.insert_chunks(records)
        elif hasattr(self._store, "begin_write") and callable(self._store.begin_write):
            snapshot = self._store.current_snapshot()
            write = self._store.begin_write(snapshot)
            for r in records:
                if hasattr(write, "put_source") and isinstance(r, SourceRecord):
                    write.put_source(r)
                elif hasattr(write, "put_version") and isinstance(r, SourceVersionRecord):
                    write.put_version(r)
                elif hasattr(write, "put_chunk") and isinstance(r, ChunkRecord):
                    write.put_chunk(r)
                else:
                    write.put(r)
            write.commit()
        else:
            raise HarbourMemoryServiceError(
                "unsupported_store",
                f"Active store {type(self._store).__name__} does not support record writes",
            )

    def index_sources(
        self,
        sources: list[str] | None = None,
        *,
        owner_scope: str = "owner:admin",
        batch_size: int = 50,
    ) -> dict[str, Any]:
        """Index repository sources, planning documents, and roadmaps into SQLite FTS5 BM25 index."""
        if sources is not None:
            if not isinstance(sources, list) or any(not isinstance(s, str) or not s.strip() for s in sources):
                raise HarbourMemoryServiceError("invalid_params", "sources must be a list of non-empty strings")
        if not isinstance(owner_scope, str) or not owner_scope.strip():
            raise HarbourMemoryServiceError("invalid_params", "owner_scope must be a non-empty string")
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
            raise HarbourMemoryServiceError("invalid_params", "batch_size must be a positive integer")

        store = self._ensure_store()
        now_iso = datetime.now(timezone.utc).isoformat()

        # Determine target file inventory
        from src.planning_roadmap_migration import build_h1r03_structural_census
        try:
            census, _ = build_h1r03_structural_census(self._repo_root)
        except Exception:
            census = []
        census_by_path = {r["path"]: r for r in census}
        census_by_id = {r["source_id"]: r for r in census}

        targets: list[dict[str, str]] = []
        if sources is None:
            targets = list(census)
        else:
            for s in sources:
                clean_s = s.strip()
                if clean_s in census_by_path:
                    targets.append(census_by_path[clean_s])
                elif clean_s in census_by_id:
                    targets.append(census_by_id[clean_s])
                else:
                    rel_p = Path(clean_s).as_posix()
                    targets.append({
                        "path": rel_p,
                        "source_id": f"repo:{rel_p}",
                        "kind": "document",
                        "structural_role": "user_specified",
                    })

        # Deduplicate targets by relative path preserving order
        seen_paths: set[str] = set()
        deduped_targets: list[dict[str, str]] = []
        for t in targets:
            p = t.get("path", "")
            if p not in seen_paths:
                seen_paths.add(p)
                deduped_targets.append(t)
        targets = deduped_targets

        indexed_sources_count = 0
        indexed_chunks_count = 0
        indexed_paths: list[str] = []
        failed_sources: list[dict[str, str]] = []
        staged_batch: list[Any] = []

        for target in targets:
            rel_path = target.get("path", "")
            abs_path = (self._repo_root / rel_path).resolve()
            if not abs_path.is_file():
                failed_sources.append({"path": rel_path, "error": "file_not_found"})
                continue

            try:
                raw_bytes = abs_path.read_bytes()
                text = raw_bytes.decode("utf-8", errors="replace")
            except Exception as exc:
                failed_sources.append({"path": rel_path, "error": f"read_error: {exc}"})
                continue

            if not text.strip():
                continue

            ext = abs_path.suffix.lower()
            if ext == ".json" and "specs/roadmaps" in rel_path:
                source_kind = SourceKind.PLANNING
            elif ext in (".md", ".markdown"):
                source_kind = SourceKind.DOCUMENT
            else:
                source_kind = SourceKind.TEXT

            norm_rel_path = Path(rel_path).as_posix()
            source = SourceRecord(
                owner_scope=owner_scope,
                source_kind=source_kind,
                canonical_ref=f"repo:{norm_rel_path}",
                classification=Classification.PRIVATE,
                content_policy=ContentPolicy.INLINE_LOCAL,
                provider_ref="local-filesystem",
                first_seen_at=now_iso,
                source_modified_at=now_iso,
                valid_from=now_iso,
            )

            file_sha = hashlib.sha256(raw_bytes).hexdigest()
            version = SourceVersionRecord.create(
                source,
                revision_ref=f"sha256:{file_sha[:16]}",
                content_hash=content_hash(text),
                version_observed_at=now_iso,
                indexed_at=now_iso,
            )

            chunk_spans = _extract_semantic_chunks(norm_rel_path, text)
            chunks: list[ChunkRecord] = []
            for start, end, chunk_text in chunk_spans:
                if end <= start or not chunk_text:
                    continue
                chunks.append(
                    ChunkRecord.create(
                        version,
                        locator=TextRangeLocator(start, end),
                        extractor_profile_ref="semantic-v1",
                        content_hash=content_hash(chunk_text),
                        content=chunk_text,
                        indexed_at=now_iso,
                    )
                )

            staged_batch.append(source)
            staged_batch.append(version)
            staged_batch.extend(chunks)

            indexed_sources_count += 1
            indexed_chunks_count += len(chunks)
            indexed_paths.append(rel_path)

            if len(staged_batch) >= batch_size:
                self._commit_records(staged_batch)
                staged_batch.clear()

        if staged_batch:
            self._commit_records(staged_batch)
            staged_batch.clear()

        return {
            "schema": "odysseus.harbour.memory_index_report.v1",
            "status": "ok",
            "indexed_sources": indexed_sources_count,
            "indexed_chunks": indexed_chunks_count,
            "sources": indexed_paths,
            "failed_sources": failed_sources,
            "timestamp": now_iso,
        }


__all__ = [
    "HarbourMemoryService",
    "HarbourMemoryServiceError",
    "default_source_adapter_registry",
]
