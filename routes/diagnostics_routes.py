"""Diagnostics routes — /api/db/stats, /api/rag/stats, /api/test/youtube, /api/test-research."""

import logging
import os
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, Form, Query, Request

from services.youtube.youtube_handler import extract_youtube_id, extract_transcript_async
from core.constants import DEFAULT_HOST, DATA_DIR
from core.middleware import require_admin

logger = logging.getLogger(__name__)


def setup_diagnostics_routes(
    rag_manager,
    rag_available: bool,
    research_handler,
    memory_vector=None,
) -> APIRouter:
    router = APIRouter(tags=["diagnostics"])

    @router.get("/api/diagnostics/services")
    async def get_service_health(request: Request) -> Dict[str, Any]:
        """Consolidated degraded-state report for ChromaDB, SearXNG, email,
        ntfy, and provider endpoints. Non-intrusive probes — safe to poll."""
        require_admin(request)
        from src.service_health import collect_service_health
        return await collect_service_health(rag_manager, memory_vector)

    @router.get("/api/diagnostics/logs")
    async def get_diagnostics_logs(request: Request, limit: int = 200) -> Dict[str, Any]:
        require_admin(request)
        limit = max(1, min(limit, 1000))
        try:
            log_file = os.path.join(DATA_DIR, "logs", "app.log")
            if not os.path.exists(log_file):
                return {"status": "success", "logs": []}

            # Safe tail read of the log file (max 5MB via rotation)
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()

            tail_lines = lines[-limit:] if len(lines) > limit else lines
            tail_lines = [line.rstrip('\r\n') for line in tail_lines]

            return {
                "status": "success",
                "logs": tail_lines
            }
        except Exception as e:
            logger.error(f"Diagnostics logs retrieval error: {e}")
            raise HTTPException(500, f"Failed to retrieve logs: {str(e)}")

    @router.get("/api/diagnostics/ai-activity")
    async def get_ai_activity(
        request: Request,
        day: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        limit: int = Query(100, ge=1, le=1000),
        owner: str | None = None,
        surface: str | None = None,
        status: str | None = None,
    ) -> Dict[str, Any]:
        """Return redacted AI activity records for admin diagnostics.

        The underlying ledger stores metadata and hashes only. This route does
        not read chat history, prompts, documents, e-mail bodies, image data, or
        provider headers.
        """
        require_admin(request)
        try:
            from src.ai_activity_ledger import read_ai_activity

            return read_ai_activity(
                day=day,
                limit=limit,
                owner=owner,
                surface=surface,
                status=status,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.error(f"AI activity diagnostics retrieval error: {e}")
            raise HTTPException(500, "Failed to retrieve AI activity diagnostics")

    @router.get("/api/diagnostics/memory-provenance")
    async def get_memory_provenance(
        request: Request,
        day: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        limit: int = Query(100, ge=1, le=1000),
        event_type: str | None = None,
        owner: str | None = None,
        status: str | None = None,
    ) -> Dict[str, Any]:
        """Return redacted memory/RaptorGraph provenance for admin diagnostics."""
        require_admin(request)
        try:
            from src.memory_provenance_ledger import read_memory_provenance

            return read_memory_provenance(
                day=day,
                limit=limit,
                event_type=event_type,
                owner=owner,
                status=status,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.error(f"Memory provenance diagnostics retrieval error: {e}")
            raise HTTPException(500, "Failed to retrieve memory provenance diagnostics")

    @router.get("/api/diagnostics/tool-capabilities")
    async def get_tool_capabilities(request: Request) -> Dict[str, Any]:
        """Return redacted tool capability freshness diagnostics."""
        require_admin(request)
        try:
            from src.tool_capability_maintenance import read_tool_capability_diagnostics

            return read_tool_capability_diagnostics()
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.error(f"Tool capability diagnostics retrieval error: {e}")
            raise HTTPException(500, "Failed to retrieve tool capability diagnostics")

    @router.get("/api/db/stats")
    async def get_database_stats(request: Request) -> Dict[str, Any]:
        require_admin(request)
        try:
            from core.database import get_detailed_stats
            return get_detailed_stats()
        except Exception as e:
            logger.error(f"DB stats error: {e}")
            raise HTTPException(500, "Failed to retrieve database statistics")

    @router.get("/api/rag/stats")
    async def get_rag_stats(request: Request) -> Dict[str, Any]:
        require_admin(request)
        if rag_available and rag_manager:
            return rag_manager.get_stats()
        return {"error": "RAG system not available"}

    @router.get("/api/test/youtube")
    async def test_youtube(request: Request, url: str) -> Dict[str, Any]:
        require_admin(request)
        try:
            video_id = extract_youtube_id(url)
            if not video_id:
                return {"error": "Invalid YouTube URL"}

            data = await extract_transcript_async(url, video_id)
            return {
                "video_id": video_id,
                "transcript_success": data.get("success", False),
                "transcript_length": len(data.get("transcript", "")) if data.get("success") else 0,
                "transcript_preview": (data.get("transcript", "")[:500] + "...")
                    if data.get("success") and len(data.get("transcript", "")) > 500
                    else data.get("transcript", ""),
                "error": data.get("error") if not data.get("success") else None,
            }
        except Exception as e:
            return {"error": str(e)}

    @router.post("/api/test-research")
    async def test_research(request: Request, query: str = Form("What is machine learning?")) -> Dict[str, Any]:
        require_admin(request)
        try:
            endpoint = f"http://{DEFAULT_HOST}:8000/v1/chat/completions"
            model = "gpt-oss-120b"
            result = await research_handler.call_research_service(query, endpoint, model)
            return {
                "status": "success",
                "query": query,
                "result_preview": result[:200] + "..." if len(result) > 200 else result,
                "result_length": len(result),
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "query": query}

    return router
