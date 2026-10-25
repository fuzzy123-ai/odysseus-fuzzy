"""Harbour Memory Bounded Context Service Façade.

Consolidates the Unified Source Index (USI) adapter registry, FTS5 chunk query,
fallback structural census, and context manifest generation.
"""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Mapping

from src.unified_source_index_source_registry import SourceAdapterRegistry

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _sanitize_fts_query(query: str) -> str:
    """Safely format raw user query text into a valid SQLite FTS5 expression."""
    tokens = _TOKEN_RE.findall(query)
    if not tokens:
        clean = query.replace('"', '""')
        return f'"{clean}"'
    return " AND ".join(f'"{token}"*' for token in tokens)


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

        census, _ = build_h1r03_structural_census(self._repo_root)
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


__all__ = [
    "HarbourMemoryService",
    "HarbourMemoryServiceError",
    "default_source_adapter_registry",
]
