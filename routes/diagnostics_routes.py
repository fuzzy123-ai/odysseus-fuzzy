"""Diagnostics routes — /api/db/stats, /api/rag/stats, /api/test/youtube, /api/test-research."""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, Any

from fastapi import APIRouter, HTTPException, Form, Query, Request, Response

from services.youtube.youtube_handler import extract_youtube_id, extract_transcript_async
from core.constants import DEFAULT_HOST, DATA_DIR
from core.middleware import require_admin
from src.auth_helpers import require_api_token_exact_scope
from src.secret_safe_diagnostics import DiagnosticContract, project_diagnostic

logger = logging.getLogger(__name__)

_LOG_SUMMARY_CONTRACT = DiagnosticContract(
    source_id="diagnostic_log_summary",
    presence_fields=("log_file_present",),
    count_fields=("sampled_line_count",),
    state_values={"status": ("available", "missing", "failed")},
    max_count=1000,
)
_YOUTUBE_PROBE_CONTRACT = DiagnosticContract(
    source_id="youtube_probe",
    presence_fields=("transcript_present",),
    count_fields=("transcript_char_count",),
    state_values={"status": ("available", "invalid", "failed")},
)
_RESEARCH_PROBE_CONTRACT = DiagnosticContract(
    source_id="research_probe",
    presence_fields=("response_present",),
    count_fields=("response_char_count",),
    state_values={"status": ("available", "failed")},
)


def _project_log_summary(
    *,
    status: str,
    log_file_present: bool | None = None,
    sampled_line_count: int | None = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"status": status}
    if log_file_present is not None:
        payload["log_file_present"] = log_file_present
    if sampled_line_count is not None:
        payload["sampled_line_count"] = sampled_line_count
    return project_diagnostic(_LOG_SUMMARY_CONTRACT, payload).to_dict()


def _project_youtube_probe(
    *,
    status: str,
    transcript_present: bool = False,
    transcript_char_count: int = 0,
) -> Dict[str, Any]:
    return project_diagnostic(
        _YOUTUBE_PROBE_CONTRACT,
        {
            "status": status,
            "transcript_present": transcript_present,
            "transcript_char_count": min(max(transcript_char_count, 0), 1_000_000),
        },
    ).to_dict()


def _project_research_probe(
    *,
    status: str,
    response_present: bool = False,
    response_char_count: int = 0,
) -> Dict[str, Any]:
    return project_diagnostic(
        _RESEARCH_PROBE_CONTRACT,
        {
            "status": status,
            "response_present": response_present,
            "response_char_count": min(max(response_char_count, 0), 1_000_000),
        },
    ).to_dict()


def setup_diagnostics_routes(
    rag_manager,
    rag_available: bool,
    research_handler,
    memory_vector=None,
    tool_usage_analytics=None,
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
        """Return bounded log availability evidence without raw log text."""

        require_admin(request)
        limit = max(1, min(limit, 1000))
        try:
            log_file = os.path.join(DATA_DIR, "logs", "app.log")
            if not os.path.exists(log_file):
                return _project_log_summary(
                    status="missing",
                    log_file_present=False,
                    sampled_line_count=0,
                )

            sampled_line_count = 0
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                for sampled_line_count, _line in enumerate(f, start=1):
                    if sampled_line_count >= limit:
                        break
            return _project_log_summary(
                status="available",
                log_file_present=True,
                sampled_line_count=sampled_line_count,
            )
        except Exception:
            logger.error("Diagnostics log summary retrieval failed")
            raise HTTPException(500, "Failed to retrieve diagnostics log summary")

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

    @router.get("/api/diagnostics/quick-summary")
    async def get_diagnostics_quick_summary(
        request: Request,
        day: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    ) -> Dict[str, Any]:
        """Return compact redacted diagnostics for chat surfaces."""
        require_admin(request)
        try:
            from src.ai_activity_ledger import read_ai_activity
            from src.diagnostics_quick_summary import build_diagnostics_quick_summary
            from src.memory_provenance_ledger import read_memory_provenance
            from src.tool_capability_maintenance import read_tool_capability_diagnostics

            return build_diagnostics_quick_summary(
                ai_activity=read_ai_activity(day=day, limit=100),
                memory_provenance=read_memory_provenance(day=day, limit=100),
                tool_capabilities=read_tool_capability_diagnostics(),
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.error(f"Diagnostics quick summary retrieval error: {e}")
            raise HTTPException(500, "Failed to retrieve diagnostics quick summary")

    @router.get("/api/diagnostics/runtime-metrics")
    async def get_runtime_metrics(request: Request) -> Response:
        """Render only process-local content-free registries for Prometheus."""

        token_request = require_api_token_exact_scope(request, "observability:read")
        if not token_request:
            require_admin(request)
        try:
            from src.observability_metrics import render_process_runtime_metrics

            return Response(
                render_process_runtime_metrics(),
                media_type="text/plain; version=0.0.4; charset=utf-8",
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.error(f"Runtime metrics retrieval error: {e}")
            raise HTTPException(500, "Failed to retrieve runtime metrics")

    @router.get("/api/diagnostics/observability-bridge")
    async def get_observability_bridge(
        request: Request,
        question: str = Query("", max_length=500),
        day: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    ) -> Dict[str, Any]:
        """Return a bounded diagnostic packet for an operational question."""
        require_admin(request)
        try:
            from src.ai_activity_ledger import read_ai_activity
            from src.diagnostics_quick_summary import build_diagnostics_quick_summary
            from src.memory_provenance_ledger import read_memory_provenance
            from src.observability_diagnostics_bridge import build_observability_diagnostic_packet
            from src.observability_metrics import build_runtime_metrics_from_diagnostics
            from src.tool_capability_maintenance import read_tool_capability_diagnostics

            ai_activity = read_ai_activity(day=day, limit=1000)
            memory_provenance = read_memory_provenance(day=day, limit=1000)
            quick_summary = build_diagnostics_quick_summary(
                ai_activity=ai_activity,
                memory_provenance=memory_provenance,
                tool_capabilities=read_tool_capability_diagnostics(),
            )
            metrics = build_runtime_metrics_from_diagnostics(
                ai_activity=ai_activity,
                memory_provenance=memory_provenance,
            )
            return build_observability_diagnostic_packet(
                question=question,
                metrics_snapshot=metrics,
                quick_summary=quick_summary,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.error(f"Observability bridge retrieval error: {e}")
            raise HTTPException(500, "Failed to retrieve observability bridge diagnostics")

    @router.get("/api/diagnostics/open-work")
    async def get_open_work_status(request: Request) -> Dict[str, Any]:
        """Return the consolidated open-work roadmap status and gate packets."""
        require_admin(request)
        try:
            from src.open_work_status import build_open_work_completion_status

            return build_open_work_completion_status()
        except Exception as e:
            logger.error(f"Open-work diagnostics retrieval error: {e}")
            raise HTTPException(500, "Failed to retrieve open-work diagnostics")

    @router.get("/api/diagnostics/tool-usage")
    async def get_tool_usage_analytics(
        request: Request,
        start_day: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        end_day: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        tool: str | None = Query(None, max_length=120),
        family: str | None = Query(None, max_length=80),
        source: str | None = Query(None, max_length=40),
        surface: str | None = Query(None, max_length=40),
        status: str | None = Query(None, max_length=40),
        limit: int = Query(100, ge=1, le=250),
    ) -> Dict[str, Any]:
        """Return bounded aggregate-only tool usage diagnostics for admins."""

        # Authenticate before resolving or opening any analytics store.
        require_admin(request)
        current_day = datetime.now(timezone.utc).date()
        resolved_end = end_day or current_day.isoformat()
        resolved_start = start_day or (
            current_day - timedelta(days=6)
        ).isoformat()
        service = tool_usage_analytics
        owned_store = None
        try:
            if service is None:
                from core.database import DATABASE_URL
                from src.tool_usage_analytics import ToolUsageAnalyticsService
                from src.tool_usage_store import ToolUsageStore

                if not str(DATABASE_URL).startswith("sqlite:///"):
                    raise RuntimeError("tool usage analytics store is unavailable")
                database_path = str(DATABASE_URL).replace("sqlite:///", "", 1)
                owned_store = ToolUsageStore(database_path)
                service = ToolUsageAnalyticsService(owned_store)
            report = service.summarize(
                resolved_start,
                resolved_end,
                tool_analytics_id=tool,
                tool_family=family,
                tool_source=source,
                surface=surface,
                status=status,
                row_limit=limit,
                max_span_days=90,
            )
            return _project_tool_usage_report(report)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        except Exception:
            logger.error("Tool usage aggregate diagnostics retrieval failed", exc_info=True)
            raise HTTPException(503, "Tool usage aggregate diagnostics unavailable")
        finally:
            if owned_store is not None:
                owned_store.close()

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
        """Return fixed-key transcript availability evidence only."""

        require_admin(request)
        try:
            video_id = extract_youtube_id(url)
            if not video_id:
                return _project_youtube_probe(status="invalid")

            data = await extract_transcript_async(url, video_id)
            transcript = data.get("transcript", "") if isinstance(data, dict) else ""
            success = bool(data.get("success")) if isinstance(data, dict) else False
            if not success or type(transcript) is not str:
                return _project_youtube_probe(status="failed")
            return _project_youtube_probe(
                status="available",
                transcript_present=bool(transcript),
                transcript_char_count=len(transcript),
            )
        except Exception:
            logger.error("YouTube diagnostic probe failed")
            return _project_youtube_probe(status="failed")

    @router.post("/api/test-research")
    async def test_research(request: Request, query: str = Form("What is machine learning?")) -> Dict[str, Any]:
        """Return fixed-key research availability evidence only."""

        require_admin(request)
        try:
            endpoint = f"http://{DEFAULT_HOST}:8000/v1/chat/completions"
            model = "gpt-oss-120b"
            result = await research_handler.call_research_service(query, endpoint, model)
            if type(result) is not str:
                return _project_research_probe(status="failed")
            return _project_research_probe(
                status="available",
                response_present=bool(result),
                response_char_count=len(result),
            )
        except Exception:
            logger.error("Research diagnostic probe failed")
            return _project_research_probe(status="failed")

    return router


def _project_tool_usage_report(report: Dict[str, Any]) -> Dict[str, Any]:
    """Allowlist the aggregate API response against future internal fields."""

    quality = report.get("quality") if isinstance(report.get("quality"), dict) else {}
    rows = report.get("rows") if isinstance(report.get("rows"), (list, tuple)) else ()
    row_fields = (
        "day",
        "tool_analytics_id",
        "tool_family",
        "tool_source",
        "surface",
        "status",
        "invocation_count",
        "duration_count",
        "duration_total_ms",
        "distinct_owner_count",
        "distinct_session_count",
        "retry_count",
        "unknown_identity_count",
    )
    projected_rows = tuple(
        {field: row.get(field) for field in row_fields}
        for row in rows
        if isinstance(row, dict)
    )
    quality_fields = (
        "invocation_count",
        "started_count",
        "terminal_count",
        "complete_count",
        "incomplete_count",
        "distinct_owner_count",
        "distinct_session_count",
        "unknown_identity_count",
        "duplicates_rejected",
        "writer_failures",
        "scope",
        "aggregation_complete_day_count",
        "warning_codes",
    )
    top_fields = (
        "schema",
        "start_day",
        "end_day",
        "filters",
        "calls",
        "active_days",
        "duration_count",
        "duration_total_ms",
        "duration_mean_ms",
        "duration_p50_ms",
        "duration_p95_ms",
        "duration_overflow_count",
        "retry_count",
        "status_counts",
        "status_rates",
        "daily_distinct_owner_total",
        "daily_distinct_session_total",
        "filtered_group_distinct_owner_total",
        "filtered_group_distinct_session_total",
        "coverage",
        "row_count",
        "rows_truncated",
    )
    return {
        **{field: report.get(field) for field in top_fields},
        "quality": {
            **{field: quality.get(field) for field in quality_fields},
            "raw_content_visible": False,
        },
        "rows": projected_rows,
        "raw_content_visible": False,
    }
