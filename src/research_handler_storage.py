"""Storage and report helpers for ``src.research_handler``.

This mixin keeps the public ``ResearchHandler`` surface stable while moving
owner-scoped saved-report IO out of the orchestration file.
"""
import json
import logging
import time
from typing import Optional

from src.research_utils import is_low_quality, strip_thinking

logger = logging.getLogger(__name__)


class ResearchStorageMixin:
    def get_status(self, session_id: str) -> Optional[dict]:
        """Get current research status for a session."""
        if session_id in self._active_tasks:
            entry = self._active_tasks[session_id]
            result = {
                "status": entry["status"],
                "progress": entry["progress"],
                "query": entry["query"],
                "started_at": entry["started_at"],
            }
            if "_avg_duration" not in entry:
                entry["_avg_duration"] = self.get_avg_duration()
            avg = entry["_avg_duration"]
            if avg is not None:
                result["avg_duration"] = round(avg, 1)
            return result

        path = self._research_json_path(session_id)
        if path is None:
            return None
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("consumed"):
                    return None
                return {
                    "status": data.get("status", "done"),
                    "progress": {},
                    "query": data.get("query", ""),
                    "started_at": data.get("started_at", 0),
                }
            except Exception:
                pass
        return None

    def cancel_research(self, session_id: str) -> bool:
        """Cancel running research for a session."""
        if session_id not in self._active_tasks:
            return False
        entry = self._active_tasks[session_id]
        if entry["status"] != "running":
            return False
        researcher = entry.get("researcher")
        if researcher:
            researcher.cancel()
        task = entry.get("task")
        if task and not task.done():
            task.cancel()
        entry["status"] = "cancelled"
        return True

    def get_result(self, session_id: str) -> Optional[str]:
        """Get the completed research result."""
        if session_id in self._active_tasks:
            entry = self._active_tasks[session_id]
            if entry["status"] in ("done", "error", "cancelled"):
                return entry.get("result")
        path = self._research_json_path(session_id)
        if path is None:
            return None
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("consumed"):
                    return None
                return data.get("result")
            except Exception:
                pass
        return None

    def get_sources(self, session_id: str) -> Optional[list]:
        """Get deduplicated source list from research findings."""
        if session_id in self._active_tasks:
            entry = self._active_tasks[session_id]
            if entry.get("sources"):
                return entry["sources"]
            researcher = entry.get("researcher")
            if researcher and researcher.findings:
                return self._extract_sources(researcher.findings)
        path = self._research_json_path(session_id)
        if path is None:
            return None
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data.get("sources")
            except Exception:
                pass
        return None

    def get_raw_findings(self, session_id: str) -> Optional[list]:
        """Get raw per-source findings for display."""
        if session_id in self._active_tasks:
            entry = self._active_tasks[session_id]
            researcher = entry.get("researcher")
            if researcher and researcher.findings:
                return self._extract_raw_findings(researcher.findings)
        path = self._research_json_path(session_id)
        if path is None:
            return None
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return data.get("raw_findings")
            except Exception as e:
                logger.warning(f"Failed to read raw findings for {session_id}: {e}")
        return None

    @staticmethod
    def _extract_sources(findings: list) -> list:
        """Extract deduplicated [{url, title}] from findings, filtering junk."""
        seen = set()
        sources = []
        for f in findings:
            if not isinstance(f, dict):
                continue
            url = f.get("url", "")
            title = f.get("title", "") or url
            summary = f.get("summary", "") or f.get("evidence", "")
            if url and url not in seen and not is_low_quality(summary):
                seen.add(url)
                entry = {"url": url, "title": title}
                og_img = f.get("og_image", "")
                if og_img:
                    entry["image"] = og_img
                sources.append(entry)
        return sources

    @staticmethod
    def _extract_raw_findings(findings: list) -> list:
        """Extract [{url, title, summary}] per-source display findings."""
        try:
            items = []
            for f in findings:
                if not isinstance(f, dict):
                    continue
                url = f.get("url", "")
                title = f.get("title", "") or "Untitled"
                summary = f.get("summary", "")
                evidence = f.get("evidence", "")
                content = summary if summary else (evidence[:2000] if evidence else "")
                if url and content and not is_low_quality(content):
                    items.append({"url": url, "title": title, "summary": content})
            return items
        except Exception as e:
            logger.warning(f"Failed to extract raw findings: {e}")
            return []

    def get_avg_duration(self) -> Optional[float]:
        """Compute average research duration from completed results on disk."""
        durations = []
        try:
            for p in self._research_data_dir().glob("*.json"):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if data.get("status") == "done":
                        started = data.get("started_at", 0)
                        completed = data.get("completed_at", 0)
                        if started and completed and completed > started:
                            durations.append(completed - started)
                except Exception:
                    continue
        except Exception:
            pass
        if durations:
            return sum(durations) / len(durations)
        return None

    def clear_result(self, session_id: str):
        """Mark result as consumed so it won't be re-rendered on refresh."""
        self._active_tasks.pop(session_id, None)
        path = self._research_json_path(session_id)
        if path is None:
            return
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                data["consumed"] = True
                path.write_text(json.dumps(data), encoding="utf-8")
            except Exception:
                pass

    def _save_result(self, session_id: str, entry: dict):
        """Persist completed research result to disk."""
        try:
            path = self._research_json_path(session_id)
            if path is None:
                logger.error("Refusing to save research result for invalid session_id: %r", session_id)
                return
            sources = []
            raw_findings = []
            researcher = entry.get("researcher")
            if researcher and researcher.findings:
                sources = self._extract_sources(researcher.findings)
                raw_findings = self._extract_raw_findings(researcher.findings)
            entry["sources"] = sources

            data = {
                "query": entry["query"],
                "status": entry["status"],
                "result": entry["result"],
                "raw_report": entry.get("raw_report", ""),
                "sources": sources,
                "raw_findings": raw_findings,
                "stats": entry.get("stats"),
                "category": entry.get("category"),
                "started_at": entry["started_at"],
                "completed_at": time.time(),
                "owner": entry.get("owner", ""),
            }
            path.write_text(json.dumps(data), encoding="utf-8")
            logger.info(f"Research result saved to {path}")
            try:
                from src.event_bus import fire_event
                fire_event("research_completed", entry.get("owner") or None)
            except Exception:
                logger.debug("research_completed event dispatch failed", exc_info=True)
        except Exception as e:
            logger.error(f"Failed to save research result: {e}")

    def _get_session_json(self, session_id: str) -> Optional[dict]:
        """Load the saved research JSON for a session, if it exists."""
        path = self._research_json_path(session_id)
        if path is None:
            return None
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return None

    def get_report_html(self, session_id: str) -> Optional[str]:
        """Generate the visual HTML report for a session from saved JSON."""
        json_path = self._research_json_path(session_id)
        if json_path is None:
            return None
        if not json_path.exists():
            logger.warning(f"No JSON found for visual report: {json_path}")
            return None

        try:
            from src.visual_report import generate_visual_report

            data = json.loads(json_path.read_text(encoding="utf-8"))
            report_md = data.get("raw_report") or data.get("result", "")
            html_content = generate_visual_report(
                question=data.get("query", ""),
                report_markdown=report_md,
                sources=data.get("sources"),
                stats=data.get("stats"),
                category=data.get("category"),
                session_id=session_id,
                hidden_images=data.get("hidden_images") or [],
            )
            logger.info(f"Visual report generated for {session_id}")
            return html_content
        except Exception as e:
            logger.error(f"Failed to generate visual report: {e}")
            return None

    def hide_image(self, session_id: str, image_url: str) -> bool:
        """Add image_url to the persisted hidden_images list for a research."""
        path = self._research_json_path(session_id)
        if path is None:
            return False
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            hidden = data.get("hidden_images") or []
            if image_url not in hidden:
                hidden.append(image_url)
                data["hidden_images"] = hidden
                path.write_text(json.dumps(data), encoding="utf-8")
                logger.info(f"Hid image {image_url[:80]} for research {session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to hide image: {e}")
            return False

    def unhide_all_images(self, session_id: str) -> bool:
        """Clear the hidden_images list for a research."""
        path = self._research_json_path(session_id)
        if path is None:
            return False
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["hidden_images"] = []
            path.write_text(json.dumps(data), encoding="utf-8")
            logger.info(f"Cleared hidden_images for research {session_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to unhide images: {e}")
            return False

    @staticmethod
    def _format_research_report(query: str, full_report: str, stats: dict, elapsed: float) -> str:
        """Format research report markdown; sources/findings stay separate."""
        full_report = strip_thinking(full_report)
        summary_lines = [
            f"**Duration:** {elapsed:.1f}s",
            f"**Rounds:** {stats.get('Rounds', stats.get('Findings', '?'))}",
            f"**Queries:** {stats.get('Queries', stats.get('Searches', '?'))}",
            f"**URLs Analyzed:** {stats.get('URLs', '?')}",
        ]
        summary_text = " | ".join(summary_lines)

        return f"""---

## Research Summary

{summary_text}

---

{full_report}
"""

    @staticmethod
    def _format_error_response(error_msg: str, query: str) -> str:
        """Format an unavailable-engine response in a user-friendly way."""
        return f"""## Research Engine Unavailable

**Query:** {query}

**Error:** {error_msg}

**Please check:**
1. LLM endpoint is reachable
2. SearXNG is running at the configured instance
3. Application logs for detailed error information

**Troubleshooting:**
- Test basic search: Try the web search toggle first
- Check search config: `/api/search/config`
- Review logs for initialization errors
"""

    @staticmethod
    def _handle_research_failure(query: str, error: str) -> str:
        """Handle research failure with fallback to basic web search."""
        try:
            logger.info("Attempting fallback to basic web search...")
            from src.search import comprehensive_web_search

            search_result = comprehensive_web_search(query)

            return f"""## Research Failed - Basic Search Fallback

**Query:** {query}

**Error:** {error}

**Note:** The deep research engine encountered an error. Here are basic search results instead:

---

### Basic Web Search Results

{search_result}

---

**To fix deep research:**
1. Check that your LLM endpoint and search provider are properly configured
2. Verify network connectivity
3. Review application logs for detailed error information

Try the web search toggle for simpler queries, or fix the research engine for comprehensive analysis.
"""

        except Exception as e2:
            logger.error(f"Fallback search also failed: {e2}", exc_info=True)
            return f"""## Complete Research Failure

**Primary Error:** {error}
**Fallback Error:** {str(e2)}

**Please check:**
1. Search provider configuration in Settings -> Search Settings
2. Network connectivity to search APIs
3. Application logs for detailed error information
4. That SearXNG is running (if using SearXNG)

**Debug Info:**
- Search config endpoint: `/api/search/config`
- Test basic search toggle with a simple query first
"""
