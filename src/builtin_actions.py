"""
builtin_actions.py

Registry of built-in automation actions that can be executed by the task
scheduler without needing an LLM call.
"""

import logging
import os
from datetime import datetime
from typing import Tuple

from src.auth_helpers import owner_filter
from core.platform_compat import IS_WINDOWS, find_bash
from core.constants import internal_api_base
from src.constants import DATA_DIR, DEEP_RESEARCH_DIR, TIDY_CALENDAR_STATE_FILE, COOKBOOK_STATE_FILE
from src.builtin_action_types import TaskDeferred, TaskNoop
from src.todo_digest_formatting import collapse_repeated_open_item_list_prefixes

logger = logging.getLogger(__name__)


async def action_tidy_sessions(owner: str, **kwargs) -> Tuple[str, bool]:
    """Delete empty sessions for the owner. Pure heuristic â€”
    the LLM folder-sort phase is skipped (user opted to keep this task
    LLM-free; sorting can be triggered manually via the Chats UI)."""
    try:
        import asyncio
        from src.session_actions import run_auto_sort
        result = await asyncio.wait_for(
            run_auto_sort(owner, skip_llm=True, delete_throwaway=False),
            timeout=60,
        )
        return result, True
    except asyncio.TimeoutError:
        logger.error("tidy_sessions action timed out")
        return "Chat session tidy timed out", False
    except Exception as e:
        logger.error(f"tidy_sessions action failed: {e}")
        return str(e), False


async def action_tidy_documents(owner: str, **kwargs) -> Tuple[str, bool]:
    """Run tidy on documents for the owner."""
    try:
        from src.document_actions import run_document_tidy
        result = await run_document_tidy(owner)
        return result, True
    except Exception as e:
        logger.error(f"tidy_documents action failed: {e}")
        return str(e), False


async def action_consolidate_memory(owner: str, **kwargs) -> Tuple[str, bool]:
    """Consolidate/deduplicate memories for the owner."""
    try:
        import json
        import re
        from src.constants import DATA_DIR
        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import llm_call_async
        from src.memory import MemoryManager

        manager = MemoryManager(DATA_DIR)
        all_memories = manager.load_all()

        _owner_clean = (owner or "").strip()
        text_limit = 2000

        def _memory_owner(mem: dict) -> str:
            return (mem.get("owner") or "").strip()

        # Built-in housekeeping can run without an owner. In that case scan all
        # memories, but keep every AI prompt/apply step owner-local.
        if _owner_clean:
            memory_groups = {
                _owner_clean: [m for m in all_memories if _memory_owner(m) == _owner_clean]
            }
        else:
            memory_groups = {}
            for mem in all_memories:
                memory_groups.setdefault(_memory_owner(mem), []).append(mem)

        memory_groups = {group_owner: group for group_owner, group in memory_groups.items() if group}
        if not memory_groups:
            raise TaskNoop("no memories to consolidate")

        total_removed = 0
        total_cleaned = 0
        total_scanned = 0
        removed_examples = []
        ai_reasons = []
        ai_used = False

        async def _try_ai_tidy_group(group_owner: str, group_memories: list) -> bool:
            nonlocal all_memories, total_removed, total_cleaned, total_scanned, ai_used
            if len(group_memories) < 2:
                return False

            url, model, headers = resolve_endpoint("utility", owner=group_owner or None)
            if not url or not model:
                url, model, headers = resolve_endpoint("default", owner=group_owner or None)
            if not url or not model:
                return False

            try:
                items = [
                    {
                        "id": m.get("id"),
                        "category": m.get("category", "fact"),
                        "text": (m.get("text") or "").strip()[:text_limit],
                        "truncated": len((m.get("text") or "").strip()) > text_limit,
                    }
                    for m in group_memories
                    if m.get("id") and (m.get("text") or "").strip()
                ]
                if len(items) < 2:
                    return False
                truncated_ids = {item["id"] for item in items if item.get("truncated")}
                prompt = (
                    "You are tidying a user's saved personal memories. Return ONLY raw JSON, no markdown.\n"
                    "Remove memories that are empty, broken, trivial conversation filler, duplicates, or obsolete "
                    "because a clearer newer memory replaces them. Preserve useful personal facts, preferences, "
                    "contacts, project context, and instructions. If memories conflict, keep the clearest/latest "
                    "one and drop the obsolete one.\n\n"
                    "JSON shape:\n"
                    "{\"keep\":[{\"id\":\"existing id\",\"text\":\"cleaned text\",\"category\":\"fact|preference|identity|event|contact|project|instruction\"}],"
                    "\"drop\":[{\"id\":\"existing id\",\"reason\":\"short reason\"}]}\n\n"
                    f"MEMORIES:\n{json.dumps(items, ensure_ascii=False)}"
                )
                raw = await llm_call_async(
                    url=url,
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=4096,
                    headers=headers,
                    timeout=120,
                    owner=owner,
                    surface="memory",
                    prompt_type="memory_consolidate",
                )
                from src.text_helpers import strip_think

                raw = strip_think(raw or "", prose=False, prompt_echo=False).strip()
                raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
                start = raw.find("{")
                end = raw.rfind("}")
                if start != -1 and end != -1 and end > start:
                    decision = json.loads(raw[start:end + 1])
                    keep_items = decision.get("keep") if isinstance(decision, dict) else None
                    drop_items = decision.get("drop") if isinstance(decision, dict) else None
                    if isinstance(keep_items, list) and isinstance(drop_items, list):
                        by_id = {m.get("id"): m for m in group_memories if m.get("id")}
                        cleaned_by_id = {}
                        for item in keep_items:
                            if not isinstance(item, dict):
                                continue
                            mid = item.get("id")
                            if mid not in by_id:
                                continue
                            text = (item.get("text") or "").strip()
                            if not text:
                                continue
                            cleaned = {
                                "category": (item.get("category") or by_id[mid].get("category") or "fact").strip(),
                            }
                            original_text = (by_id[mid].get("text") or "").strip()
                            if len(original_text) <= text_limit:
                                cleaned["text"] = text
                            cleaned_by_id[mid] = cleaned

                        # Delete only memories the model EXPLICITLY dropped, never
                        # ones it merely omitted from `keep`. Treating the
                        # complement of `keep` as deletions meant a model that
                        # forgot to re-list an id (common) silently destroyed that
                        # memory. Honor the explicit `drop` set instead.
                        drop_ids = {
                            d.get("id")
                            for d in drop_items
                            if isinstance(d, dict) and d.get("id") in by_id
                        }
                        # Never delete a memory the model only saw truncated.
                        drop_ids -= truncated_ids

                        if drop_ids or cleaned_by_id:
                            changed_text = 0
                            group_ref_ids = {id(m) for m in group_memories}
                            kept_all = []
                            for mem in all_memories:
                                if id(mem) not in group_ref_ids:
                                    kept_all.append(mem)
                                    continue
                                mid = mem.get("id")
                                if mid in drop_ids:
                                    continue
                                cleaned = cleaned_by_id.get(mid) or {}
                                if mid in truncated_ids:
                                    cleaned.pop("text", None)
                                if cleaned.get("text") and cleaned["text"] != mem.get("text"):
                                    mem["text"] = cleaned["text"]
                                    changed_text += 1
                                if cleaned.get("category"):
                                    mem["category"] = cleaned["category"]
                                kept_all.append(mem)

                            removed = sum(1 for m in group_memories if m.get("id") in drop_ids)
                            total_scanned += len(group_memories)
                            if removed or changed_text:
                                all_memories = kept_all
                                total_removed += removed
                                total_cleaned += changed_text
                                ai_used = True
                                ai_reasons.extend([
                                    (d.get("reason") or "").strip()
                                    for d in drop_items
                                    if isinstance(d, dict) and (d.get("reason") or "").strip()
                                ])
                            return True
            except Exception as ai_err:
                logger.warning("AI memory tidy failed; falling back to duplicate cleanup: %s", ai_err)
            return False

        for group_owner, group_memories in memory_groups.items():
            if await _try_ai_tidy_group(group_owner, group_memories):
                continue

            seen = {}
            keep_refs = set()
            total_scanned += len(group_memories)
            for mem in group_memories:
                text = (mem.get("text") or "").strip()
                key = " ".join(text.lower().split())
                if not key:
                    if len(removed_examples) < 3:
                        removed_examples.append("(empty)")
                    continue
                if key in seen:
                    if len(removed_examples) < 3:
                        removed_examples.append(text[:60] + ("..." if len(text) > 60 else ""))
                    continue
                seen[key] = mem
                keep_refs.add(id(mem))

            group_removed = len(group_memories) - len(keep_refs)
            if group_removed == 0:
                continue

            group_ref_ids = {id(m) for m in group_memories}
            all_memories = [
                m for m in all_memories
                if id(m) not in group_ref_ids or id(m) in keep_refs
            ]
            total_removed += group_removed

        if total_removed or total_cleaned:
            manager.save(all_memories)
            if ai_used:
                reasons = ai_reasons[:3]
                reason_text = f": {'; '.join(reasons)}" if reasons else ""
                return (
                    f"AI tidied {total_scanned} memories: "
                    f"removed {total_removed}, cleaned {total_cleaned}{reason_text}",
                    True,
                )
            preview = "; ".join(removed_examples)
            extra = f" (+{total_removed - len(removed_examples)} more)" if total_removed > len(removed_examples) else ""
            return f"Removed {total_removed} duplicate(s) of {total_scanned}: {preview}{extra}", True

        raise TaskNoop(f"scanned {total_scanned} memories, no duplicates")
    except Exception as e:
        logger.error(f"consolidate_memory action failed: {e}")
        return str(e), False


# Registry: action name -> async function(owner, **kwargs) -> (result_str, success_bool)


async def _run_subprocess(argv, *, shell: bool = False, timeout: int = 120, label: str = "Command") -> Tuple[str, bool]:
    """Shared subprocess runner. Wraps the blocking subprocess.run in
    asyncio.to_thread so the event loop stays responsive."""
    import asyncio
    import subprocess
    try:
        result = await asyncio.to_thread(
            subprocess.run, argv, shell=shell, capture_output=True, text=True, timeout=timeout,
        )
        output = (result.stdout or "").strip()
        if result.returncode != 0 and result.stderr:
            output += "\nSTDERR: " + result.stderr.strip()
        return output or "(no output)", result.returncode == 0
    except subprocess.TimeoutExpired:
        return f"{label} timed out ({timeout}s)", False
    except Exception as e:
        return str(e), False


async def action_ssh_command(owner: str, command: str = "", host: str = "localhost", **kwargs) -> Tuple[str, bool]:
    """Run a shell command locally or on a remote host via SSH."""
    if not command:
        return "No command specified", False
    if host in ("localhost", "127.0.0.1", "local"):
        if IS_WINDOWS:
            bash = find_bash()
            if bash:
                return await _run_subprocess([bash, "-c", command], timeout=120, label="Command")
            return await _run_subprocess(command, shell=True, timeout=120, label="Command")
        return await _run_subprocess(["bash", "-c", command], timeout=120, label="Command")
    return await _run_subprocess(
        ["ssh", "-o", "ConnectTimeout=10", host, command], timeout=120, label="Command",
    )


async def action_run_script(owner: str, script: str = "", host: str = "", **kwargs) -> Tuple[str, bool]:
    """Run a script locally, or via SSH when a host is configured."""
    if not script:
        return "No script specified", False
    target_host = (host or os.getenv("ODYSSEUS_SCRIPT_HOST", "localhost")).strip()
    if target_host in ("", "localhost", "127.0.0.1", "local"):
        if IS_WINDOWS and find_bash():
            return await _run_subprocess([find_bash(), "-c", script], timeout=300, label="Script")
        return await _run_subprocess(script, shell=True, timeout=300, label="Script")
    return await _run_subprocess(["ssh", target_host, script], timeout=300, label="Script")


async def action_run_local(owner: str, script: str = "", **kwargs) -> Tuple[str, bool]:
    """Run a script locally (no SSH)."""
    if not script:
        return "No script specified", False
    if IS_WINDOWS and find_bash():
        return await _run_subprocess([find_bash(), "-c", script], timeout=300, label="Script")
    return await _run_subprocess(script, shell=True, timeout=300, label="Script")


async def action_tidy_research(owner: str, **kwargs) -> Tuple[str, bool]:
    """Remove only broken research files (empty or unparseable JSON).

    Research history lives entirely in data/deep_research/<id>.json and is NOT
    backed by chat-session rows â€” so a file must never be deleted just because
    no chat session matches its id. Only prune files that fail to load."""
    try:
        from pathlib import Path
        import json as _json
        research_dir = Path(DEEP_RESEARCH_DIR)
        if not research_dir.exists():
            raise TaskNoop("no research directory")
        files = list(research_dir.glob("*.json"))
        removed = []
        for p in files:
            try:
                txt = p.read_text(encoding="utf-8").strip()
                if not txt:
                    raise ValueError("empty file")
                _json.loads(txt)  # valid JSON â†’ keep
            except Exception:
                p.unlink(missing_ok=True)
                removed.append(p.stem[:8])
        if not removed:
            raise TaskNoop(f"scanned {len(files)} research file(s), none broken")
        return f"Removed {len(removed)} broken research file(s) of {len(files)}", True
    except Exception as e:
        logger.error(f"tidy_research action failed: {e}")
        return str(e), False


async def action_tidy_calendar(owner: str, **kwargs) -> Tuple[str, bool]:
    """Find duplicate calendar events (same title + start time) and DELETE the dups,
    keeping the oldest (first-seen) instance.

    Incremental: remembers the newest `created_at` already scanned in
    data/tidy_calendar_state.json. If no events have been added since then,
    short-circuits. Otherwise only events newer than the watermark are candidates
    for deletion, but they're checked against the FULL existing set so a new
    duplicate of an old event still gets caught.
    """
    try:
        import json
        from pathlib import Path
        from core.database import SessionLocal, CalendarEvent
        from sqlalchemy import func

        STATE_FILE = Path(TIDY_CALENDAR_STATE_FILE)
        last_watermark = None
        try:
            if STATE_FILE.exists():
                saved = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                if saved.get("last_created_at"):
                    last_watermark = datetime.fromisoformat(saved["last_created_at"])
        except Exception:
            last_watermark = None

        db = SessionLocal()
        try:
            newest = db.query(func.max(CalendarEvent.created_at)).scalar()
            db.query(CalendarEvent).count()

            # Short-circuit: nothing new since last run
            if last_watermark is not None and newest is not None and newest <= last_watermark:
                raise TaskNoop(f"no new events since watermark {last_watermark.strftime('%Y-%m-%d %H:%M')}")

            events = db.query(CalendarEvent).order_by(CalendarEvent.dtstart).all()
            # Build full seen-set from events at or before the watermark (known-clean).
            # Events after the watermark are candidates for deletion.
            seen = {}
            candidates = []
            no_title = 0
            for e in events:
                title = (e.summary or "").strip()
                if not title:
                    no_title += 1
                    continue
                if last_watermark is None or (e.created_at and e.created_at <= last_watermark):
                    # Known-clean region: first occurrence wins
                    key = (title.lower(), e.dtstart)
                    if key not in seen:
                        seen[key] = e
                    # If a dup exists in the known-clean region (first run, or imported later
                    # with the same created_at), still remove it â€” fall through to candidate check.
                    else:
                        candidates.append(e)
                else:
                    candidates.append(e)

            removed = []
            for e in candidates:
                title = (e.summary or "").strip()
                key = (title.lower(), e.dtstart)
                if key in seen:
                    when = e.dtstart.strftime('%Y-%m-%d %H:%M') if e.dtstart else '?'
                    removed.append(f"{title} @ {when}")
                    db.delete(e)
                else:
                    seen[key] = e

            if removed:
                db.commit()

            # Persist the new watermark (newest created_at among events that survive)
            try:
                STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                if newest is not None:
                    STATE_FILE.write_text(json.dumps({
                        "last_created_at": newest.isoformat(),
                        "last_run_at": datetime.utcnow().isoformat(),
                        "scanned": len(events),
                        "removed": len(removed),
                    }, indent=2), encoding="utf-8")
            except Exception as se:
                logger.warning(f"tidy_calendar watermark save failed: {se}")

            new_since = len(candidates)
            parts = [f"Scanned {len(events)} event(s), {new_since} new since last run"]
            if removed:
                preview = "; ".join(removed[:5])
                if len(removed) > 5:
                    preview += f" (+{len(removed) - 5} more)"
                parts.append(f"removed {len(removed)} duplicate(s): {preview}")
            if no_title:
                parts.append(f"{no_title} untitled (kept)")
            if not removed and not no_title:
                parts.append("no duplicates")
            return " Â· ".join(parts), True
        finally:
            db.close()
    except Exception as e:
        logger.error(f"tidy_calendar action failed: {e}")
        return str(e), False


def _result_has_work(result: str | None) -> bool:
    """Heuristic: did the email pass actually process anything?

    `_run_auto_summarize_once` returns strings like 'Processed 0 emails',
    'No new emails to summarize', 'Tagged 0 / Moved 0', etc. when nothing
    was done. Used to decide whether to record the run or noop it.
    """
    if not isinstance(result, str) or not result:
        return False
    low = result.lower()
    if "processed 0" in low or "no new" in low or "nothing to" in low:
        return False
    # "Tagged 0 / Moved 0" or similar zero-count summaries
    if low.count(" 0") >= 2 and ("tagged" in low or "moved" in low or "drafted" in low):
        return False
    return True


async def action_summarize_emails(owner: str, **kwargs) -> Tuple[str, bool]:
    """Run one pass of email summary background processing."""
    try:
        from routes.email_pollers import _run_auto_summarize_once
        result = await _run_auto_summarize_once(do_summary=True, do_reply=False)
        if not _result_has_work(result):
            raise TaskNoop(f"summarize: {result or 'no new emails'}")
        return result, True
    except Exception as e:
        logger.error(f"summarize_emails action failed: {e}")
        return str(e), False


async def action_draft_email_replies(owner: str, **kwargs) -> Tuple[str, bool]:
    """Run one pass of AI reply drafting."""
    try:
        from routes.email_pollers import _run_auto_summarize_once
        result = await _run_auto_summarize_once(
            do_summary=False,
            do_reply=True,
            days_back=7,
            progress_cb=kwargs.get("progress_cb"),
        )
        if not _result_has_work(result):
            raise TaskNoop(f"draft replies: {result or 'no new emails'}")
        return result, True
    except Exception as e:
        logger.error(f"draft_email_replies action failed: {e}")
        return str(e), False


_TYPE_COLORS = {
    "work":     "#5b8abf",  # blue
    "personal": "#a07ae0",  # purple
    "health":   "#e06c75",  # red
    "travel":   "#e5a33a",  # orange
    "meal":     "#d8b974",  # tan
    "social":   "#82c882",  # green
    "admin":    "#888888",  # gray
    "other":    "#6b9cb5",  # default
}

_HEURISTIC_TYPES = {
    "health":  ["doctor", "dentist", "clinic", "hospital", "appointment", "checkup", "therapy",
                "physio", "chiropract", "vaccine", "blood test", "xray", "scan", "surgery"],
    "travel":  ["flight", "airport", "train", "shinkansen", "boarding", "uber", "taxi", "trip",
                "hotel", "airbnb", "depart", "arrival", "check-in", "checkout"],
    "meal":    ["lunch", "dinner", "breakfast", "brunch", "coffee", "drinks", "restaurant",
                "reservation", "bar", "cafe"],
    "social":  ["birthday", "party", "hangout", "wedding", "date with", "drinks with",
                "anniversary", "baby shower", "graduation", "picnic", "bbq"],
    "admin":   ["bill", "renewal", "tax", "deadline", "filing", "submit", "due date",
                "registration", "license", "passport", "visa", "form"],
    "work":    ["meeting", "standup", "sync", "1:1", "1on1", "review", "interview",
                "demo", "presentation", "kickoff", "retro", "all-hands", "town hall",
                "call with", "client", "deck"],
}

_HEURISTIC_HIGH = ["flight", "interview", "wedding", "surgery", "exam", "deadline",
                   "court", "presentation", "demo", "kickoff", "launch"]
_HEURISTIC_CRITICAL = ["surgery", "court", "wedding day", "funeral", "delivery date"]


def _classify_event_heuristic(summary: str) -> tuple:
    """Quick heuristic classification â€” returns (event_type, importance) or (None, None) if unclear."""
    s = (summary if isinstance(summary, str) else "").lower()
    etype = None
    for t, kws in _HEURISTIC_TYPES.items():
        if any(k in s for k in kws):
            etype = t
            break
    if any(k in s for k in _HEURISTIC_CRITICAL):
        return etype, "critical"
    if any(k in s for k in _HEURISTIC_HIGH):
        return etype, "high"
    return etype, None


def _memory_context_lines(mems, limit: int = 40) -> list:
    """Render Memory rows into short personal-context bullets for event classify.

    Reads the Memory ORM `text` column. The previous inline code read a
    non-existent `content` attribute, so it raised AttributeError on the first
    row, the surrounding except swallowed it, and the classifier ran with no
    personal context at all. getattr keeps it robust to future schema drift.
    """
    lines: list = []
    for m in mems:
        c = (getattr(m, "text", "") or "").strip()
        if c:
            lines.append(f"- {c[:200]}")
        if len(lines) >= limit:
            break
    return lines


async def action_classify_events(owner: str, **kwargs) -> Tuple[str, bool]:
    """Hybrid classification of upcoming calendar events: fast heuristic for
    obvious cases, LLM fallback for ambiguous ones. Assigns event_type +
    importance + color. Re-classifies anything not already set."""
    try:
        from datetime import timedelta
        from core.database import SessionLocal, CalendarEvent
        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import llm_call_async
        import re as _re, json as _json

        db = SessionLocal()
        try:
            now = datetime.utcnow()
            horizon = now + timedelta(days=30)
            events = db.query(CalendarEvent).filter(
                CalendarEvent.dtstart >= now,
                CalendarEvent.dtstart <= horizon,
                CalendarEvent.status != "cancelled",
            ).all()
            if not events:
                return "No upcoming events to classify", True

            llm_url, llm_model, llm_headers = resolve_endpoint("utility", owner=owner)
            if not llm_url:
                llm_url, llm_model, llm_headers = resolve_endpoint("default", owner=owner)
            llm_available = bool(llm_url and llm_model)

            # Pull user memories so the LLM has personal context (relationships,
            # job, hobbies). Helps it know e.g. "<name> is your spouse" so their
            # events are personal/social, not work.
            _memory_context = ""
            try:
                from core.database import Memory as _Mem
                _mems = db.query(_Mem).filter(_Mem.owner == owner).limit(60).all() if owner else []
                _lines = _memory_context_lines(_mems)
                if _lines:
                    _memory_context = "USER CONTEXT (relationships, work, life):\n" + "\n".join(_lines) + "\n\n"
            except Exception as _me:
                logger.warning(f"Could not load memory for classify: {_me}")

            classified_h = 0
            classified_llm = 0
            failed = 0
            unchanged = 0
            # Pass 1: heuristic for obvious cases, collect ambiguous for LLM batch
            llm_queue = []  # list of CalendarEvent objects needing LLM
            for ev in events:
                if ev.event_type and ev.importance and ev.importance != "normal":
                    unchanged += 1
                    continue
                etype, importance = _classify_event_heuristic(ev.summary or "")
                if etype and importance:
                    ev.event_type = etype
                    ev.color = _TYPE_COLORS.get(etype)
                    ev.importance = importance
                    classified_h += 1
                    continue
                # Apply partial heuristic; queue for LLM to fill missing
                if etype:
                    ev.event_type = etype
                    ev.color = _TYPE_COLORS.get(etype)
                if llm_available:
                    llm_queue.append(ev)
                elif etype:
                    classified_h += 1
            # Persist heuristic results before LLM pass (in case LLM is slow/unavailable)
            try:
                db.commit()
            except Exception:
                pass

            # Pass 2: batch LLM classification (10 events per call)
            BATCH = 10
            for i in range(0, len(llm_queue), BATCH):
                batch = llm_queue[i:i+BATCH]
                items = [
                    {"i": idx, "title": (ev.summary or "")[:120],
                     "when": ev.dtstart.isoformat() if ev.dtstart else "",
                     "loc": (ev.location or "")[:80]}
                    for idx, ev in enumerate(batch)
                ]
                prompt = (
                    _memory_context +
                    "Classify these calendar events using the USER CONTEXT above (people they know, "
                    "their job, hobbies). Return ONLY a raw JSON array, no prose, no markdown.\n"
                    "Each item: {\"i\": <index>, \"type\": \"work|personal|health|travel|meal|social|admin|other\", "
                    "\"importance\": \"low|normal|high|critical\"}\n\n"
                    "Type guidance:\n"
                    "- personal = family, partner, kids, pets, errands, home stuff\n"
                    "- social = friends, parties, birthdays, hangouts\n"
                    "- work = the user's own job/career commitments only (not their partner's)\n"
                    "- health = doctor, gym, therapy\n"
                    "- travel = flights, trips, hotels\n"
                    "- meal = lunch/dinner/coffee specifically\n"
                    "- admin = bills, taxes, paperwork\n"
                    "- other = anything else\n\n"
                    "Importance guide: critical = surgery/court/wedding day; high = flight/interview/big presentation/exam; "
                    "normal = regular meetings/appointments; low = recurring routine.\n\n"
                    f"EVENTS: {_json.dumps(items)}"
                )
                try:
                    raw = await llm_call_async(
                        url=llm_url, model=llm_model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.1, max_tokens=16384,
                        headers=llm_headers, timeout=180,
                        owner=owner,
                        surface="calendar",
                        prompt_type="calendar_classify_events",
                    )
                    from src.text_helpers import strip_think as _st
                    raw = _st(raw or "", prose=False, prompt_echo=False)
                    raw = _re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=_re.MULTILINE).strip()
                    m = _re.search(r"\[.*\]", raw, _re.DOTALL)
                    if not m:
                        logger.warning(f"[classify-llm] no JSON array in response: {raw[:300]!r}")
                        failed += len(batch)
                        continue
                    arr = _json.loads(m.group())
                    by_idx = {x.get("i"): x for x in arr if isinstance(x, dict)}
                    for idx, ev in enumerate(batch):
                        x = by_idx.get(idx)
                        if not x:
                            failed += 1
                            continue
                        t = (x.get("type") or "other").lower()
                        imp = (x.get("importance") or "normal").lower()
                        if t in _TYPE_COLORS:
                            ev.event_type = t
                            ev.color = _TYPE_COLORS[t]
                        if imp in ("low", "normal", "high", "critical"):
                            ev.importance = imp
                        classified_llm += 1
                        logger.info(f"[classify-llm] '{ev.summary}' â†’ type={t} importance={imp}")
                except Exception as e:
                    logger.warning(f"[classify-llm] batch failed: {e}")
                    failed += len(batch)
                # Commit after each batch so partial progress persists
                try:
                    db.commit()
                except Exception as ce:
                    logger.warning(f"[classify-llm] commit failed: {ce}")
            # Final commit covers heuristic-only updates from pass 1
            db.commit()
            parts = [f"Scanned {len(events)} upcoming event(s)"]
            if classified_h:
                parts.append(f"{classified_h} via heuristic")
            if classified_llm:
                parts.append(f"{classified_llm} via LLM")
            if unchanged:
                parts.append(f"{unchanged} already set (skipped)")
            if failed:
                parts.append(f"{failed} LLM failed")
            return " Â· ".join(parts), True
        finally:
            db.close()
    except Exception as e:
        logger.error(f"classify_events action failed: {e}")
        return str(e), False


async def action_ping_events(owner: str, **kwargs) -> Tuple[str, bool]:
    """Calendar event reminders are now dispatched by Notes."""
    raise TaskNoop("calendar event reminders are handled by Notes")


async def action_extract_email_events(owner: str, **kwargs) -> Tuple[str, bool]:
    """Scan recent emails for booking confirmations / meetings / events
    and auto-add them to the calendar."""
    import asyncio as _aio
    try:
        from routes.email_pollers import _run_auto_summarize_once
        try:
            # Hard wall-clock budget: 5 min total. Per-LLM call already has its own timeout.
            result = await _aio.wait_for(
                _run_auto_summarize_once(
                    do_summary=False, do_reply=False, do_calendar=True, days_back=3,
                ),
                timeout=300,
            )
            if not _result_has_work(result):
                raise TaskNoop(f"emailâ†’calendar: {result or 'no new emails'}")
            return f"{result} (3d window)", True
        except _aio.TimeoutError:
            return "Emailâ†’calendar pass exceeded 5 min budget â€” try fewer emails or a faster model", False
    except Exception as e:
        logger.error(f"extract_email_events action failed: {e}")
        return str(e), False



# Sender local-parts (matched exactly or by prefix) whose mail never carries a
# personal signature worth learning. These compare against the local-part
# (before "@"), so role names must NOT include a trailing "@" â€” "support@" etc.
# could never match a local-part of "support" and were silently dead.
_SIG_SKIP_PREFIXES = (
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "mailer-daemon", "notifications", "notification", "bounce",
    "newsletter", "support", "info", "admin",
)


async def action_learn_sender_signatures(owner: str, **kwargs) -> Tuple[str, bool]:
    """For each sender with â‰¥3 recent inbox emails, ask the LLM to extract
    the common signature block across their messages. The cached sig is
    served on the `/read` endpoint so the renderer can fold signatures
    consistently from that address (no more heuristic regex juggling).
    Caps at 20 senders per pass; re-runs after 30 days per sender."""
    try:
        import sqlite3 as _sql3
        import re as _re
        import email as _email_mod
        import asyncio as _aio
        from datetime import datetime as _dt, timedelta as _td
        from routes.email_helpers import _email_cache_owner_clause, _imap_connect, SCHEDULED_DB
        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import llm_call_async

        # 1. Pull recent UIDs + From headers cheaply (header-only fetch).
        def _pull_headers():
            results = []
            conn = _imap_connect(None, owner=owner)
            try:
                conn.select("INBOX", readonly=True)
                status, data = conn.search(None, "ALL")
                if status != "OK" or not data or not data[0]:
                    return results
                uids = data[0].split()[-300:][::-1]  # newest 300
                for uid in uids:
                    try:
                        st, msg_data = conn.fetch(
                            uid, "(BODY.PEEK[HEADER.FIELDS (FROM)])"
                        )
                        if st != "OK" or not msg_data or not msg_data[0]:
                            continue
                        raw = msg_data[0][1] if isinstance(msg_data[0], tuple) else None
                        if not raw:
                            continue
                        msg = _email_mod.message_from_bytes(raw)
                        from_raw = msg.get("From", "")
                        from_addr = _email_mod.utils.parseaddr(from_raw)[1].lower().strip()
                        if not from_addr or "@" not in from_addr:
                            continue
                        results.append({
                            "uid": uid.decode() if isinstance(uid, bytes) else str(uid),
                            "from_address": from_addr,
                        })
                    except Exception:
                        continue
            finally:
                try: conn.logout()
                except Exception: pass
            return results

        mails = await _aio.to_thread(_pull_headers)
        if not mails:
            return "No emails to scan", True

        # 2. Group by sender; drop addresses that don't carry useful sigs.
        by_sender: dict[str, list[dict]] = {}
        for m in mails:
            addr = m["from_address"]
            local = addr.split("@", 1)[0]
            if any(local == p or local.startswith(p) for p in _SIG_SKIP_PREFIXES):
                continue
            # Skip plus-aliases / list-style addresses too.
            if "+" in local or "-noreply" in addr or "-bounces" in addr:
                continue
            by_sender.setdefault(addr, []).append(m)

        # 3. Eligibility: â‰¥3 emails AND (no cache OR cache > 30 days old).
        try:
            conn = _sql3.connect(SCHEDULED_DB)
            owner_clause, owner_params = _email_cache_owner_clause(owner)
            cached = {
                r[0]: r[1] for r in conn.execute(
                    f"SELECT from_address, last_built_at FROM sender_signatures WHERE {owner_clause}",
                    owner_params,
                ).fetchall()
            }
            conn.close()
        except Exception:
            cached = {}

        cutoff_iso = (_dt.utcnow() - _td(days=30)).isoformat()
        eligible: list[tuple[str, list[dict]]] = []
        for addr, msgs in by_sender.items():
            if len(msgs) < 3:
                continue
            if cached.get(addr, "") > cutoff_iso:
                continue
            eligible.append((addr, msgs[:5]))  # use up to last 5 emails

        if not eligible:
            return "All sender sigs already cached (or no eligible senders)", True

        url, model, headers = resolve_endpoint("utility", owner=owner)
        if not url or not model:
            url, model, headers = resolve_endpoint("default", owner=owner)
        if not url or not model:
            return "No LLM endpoint available", False

        analyzed = 0
        no_sig = 0
        for addr, msgs in eligible[:20]:  # cost cap per run

            def _fetch_bodies(_msgs):
                bodies = []
                conn2 = _imap_connect(None, owner=owner)
                try:
                    conn2.select("INBOX", readonly=True)
                    for mm in _msgs:
                        try:
                            st, data = conn2.fetch(mm["uid"], "(BODY.PEEK[TEXT])")
                            if st != "OK" or not data or not data[0]:
                                continue
                            raw = data[0][1] if isinstance(data[0], tuple) else None
                            if not raw:
                                continue
                            text = raw.decode("utf-8", errors="replace")
                            bodies.append(text[:4000])
                        except Exception:
                            continue
                finally:
                    try: conn2.logout()
                    except Exception: pass
                return bodies

            try:
                bodies = await _aio.to_thread(_fetch_bodies, msgs)
            except Exception as e:
                logger.warning(f"sig learner: fetch bodies failed for {addr}: {e}")
                continue
            if len(bodies) < 2:
                continue

            joined = "\n\n---NEXT EMAIL---\n\n".join(bodies[:5])
            prompt = (
                "You are extracting the literal common SIGNATURE block that "
                "appears at the END of multiple emails from the same sender.\n\n"
                "Return ONLY the exact signature text, verbatim, with original "
                "line breaks preserved. If there is no clear common signature "
                "block across these emails, respond with the single token: "
                "NONE\n\n"
                "INCLUDE: title, company, address, phone, email/url lines, "
                "legal disclaimer block.\n"
                "EXCLUDE: greetings ('Hi', 'Dear'), closing phrases on their "
                "own ('Best regards'), the sender's name on its own line, the "
                "body content, quoted/forwarded threads (lines starting with "
                "'>' or 'On ... wrote:' or 'From: ... Sent:').\n\n"
                f"EMAILS FROM {addr}:\n{joined}"
            )

            try:
                raw = await llm_call_async(
                    url=url, model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0, max_tokens=600,
                    headers=headers, timeout=60,
                    owner=owner,
                    surface="email",
                    prompt_type="email_signature_extract",
                )
                from src.text_helpers import strip_think as _st
                sig = _st(raw or "", prose=False, prompt_echo=False).strip()
                # Strip surrounding code fences if the LLM added them.
                sig = _re.sub(r"^```[\w]*\n?", "", sig)
                sig = _re.sub(r"\n?```\s*$", "", sig)
                sig = sig.strip()
            except Exception as e:
                logger.warning(f"sig LLM call failed for {addr}: {e}")
                continue

            # NONE sentinel or out-of-bounds â†’ cache a NULL row so we don't
            # re-try for 30 days, then move on.
            if (
                not sig
                or sig.upper().strip().strip(".") == "NONE"
                or len(sig) < 15
                or len(sig) > 3000
            ):
                cached_sig: str | None = None
                no_sig += 1
            else:
                cached_sig = sig

            try:
                conn = _sql3.connect(SCHEDULED_DB)
                owner_value = (owner or "").strip()
                conn.execute(
                    "INSERT OR REPLACE INTO sender_signatures "
                    "(from_address, owner, signature_text, sample_count, last_built_at, model_used, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (addr, owner_value, cached_sig, len(bodies), _dt.utcnow().isoformat(), model, "llm"),
                )
                conn.commit()
                conn.close()
                analyzed += 1
            except Exception as e:
                logger.warning(f"sig cache write failed for {addr}: {e}")

        return f"Learned sigs: {analyzed - no_sig} found, {no_sig} no-sig, of {len(eligible)} eligible", True
    except Exception as e:
        logger.error(f"learn_sender_signatures failed: {e}")
        return str(e), False


async def action_daily_brief(owner: str, **kwargs) -> Tuple[str, bool]:
    """Build a short morning digest: today's calendar events, unread email count
    + top-N senders/subjects, active todos."""
    try:
        from datetime import datetime as _dt, timedelta as _td
        import json as _json

        from core.database import SessionLocal, CalendarEvent, CalendarCal, Note
        from routes.email_helpers import _imap_connect, _decode_header

        # ----- Calendar: today's events -----
        today = _dt.now().replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow = today + _td(days=1)
        # v2 review HIGH-12: gate the OR-null branch on single-user
        # (unconfigured) deploys only. In a multi-user deploy, one
        # user's daily brief must not include another user's notes or
        # events that happen to be stored with owner=None.
        try:
            from core.auth import AuthManager
            _allow_null = not AuthManager().is_configured
        except Exception:
            _allow_null = False
        db = SessionLocal()
        try:
            ev_q = db.query(CalendarEvent).join(CalendarCal).filter(
                CalendarEvent.dtstart < tomorrow,
                CalendarEvent.dtend > today,
                CalendarEvent.status != "cancelled",
            )
            if owner:
                ev_q = owner_filter(ev_q, CalendarCal, owner, include_shared=_allow_null)
            events = ev_q.order_by(CalendarEvent.dtstart).all()
            # ----- Notes: pinned + non-archived todos with at least one undone item -----
            n_q = db.query(Note).filter(Note.archived == False)  # noqa: E712
            if owner:
                n_q = owner_filter(n_q, Note, owner, include_shared=_allow_null)
            notes = n_q.all()
        finally:
            db.close()

        # ----- Email: unread count + top 5 inbox subjects (best-effort) -----
        # Direct IMAP: cheaper than the full _list_emails_sync helper and
        # avoids the module/import coupling that broke this once already.
        unread_count = 0
        recent_subjects: list[tuple[str, str]] = []
        try:
            import email as _email
            conn = _imap_connect(None)
            try:
                conn.select("INBOX", readonly=True)
                status, data = conn.search(None, "UNSEEN")
                uids = (data[0].split() if status == "OK" and data and data[0] else [])
                unread_count = len(uids)
                # Grab headers for the most recent 5 unread (UIDs increase with arrival)
                for uid in uids[-5:][::-1]:
                    try:
                        _, msg_data = conn.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT)])")
                        if not msg_data or not msg_data[0]:
                            continue
                        hdr = msg_data[0][1] if isinstance(msg_data[0], tuple) else msg_data[0]
                        parsed = _email.message_from_bytes(hdr)
                        subject = _decode_header(parsed.get("Subject") or "") or "(no subject)"
                        from_raw = _decode_header(parsed.get("From") or "") or "?"
                        # Extract just the display name if "Name <addr>" form
                        if "<" in from_raw:
                            name = from_raw.split("<", 1)[0].strip().strip('"') or from_raw
                        else:
                            name = from_raw
                        recent_subjects.append((name, subject))
                    except Exception as fe:
                        logger.debug(f"daily_brief: header fetch for uid {uid} failed: {fe}")
            finally:
                try: conn.logout()
                except Exception: pass
        except Exception as ee:
            logger.debug(f"daily_brief: email fetch failed: {ee}")

        # Pull active todo items from notes
        todo_lines: list[str] = []
        for n in notes:
            if n.note_type == "checklist" and n.items:
                try:
                    items = _json.loads(n.items)
                    pending = [it.get("text", "") for it in items if not it.get("done")]
                    for t in pending[:3]:
                        if t:
                            todo_lines.append(f"{n.title or 'Checklist'}: {t}")
                except Exception:
                    continue
            elif n.pinned and n.title:
                todo_lines.append(n.title)

        # ----- Compose -----
        # %-d is GNU-only; format the day with str() so the brief works on
        # Windows / non-glibc Python builds too.
        date_label = today.strftime(f"%A, %B {today.day}, %Y")

        plain = [f"Daily brief â€” {date_label}", ""]
        if events:
            plain.append("Calendar:")
            for e in events:
                t = e.dtstart.strftime("%H:%M") if not e.all_day else "all day"
                loc = f" @ {e.location}" if e.location else ""
                plain.append(f"  {t}  {e.summary}{loc}")
            plain.append("")
        else:
            plain.append("Calendar: nothing scheduled.")
            plain.append("")

        plain.append(f"Email: {unread_count} unread")
        for sender, subj in recent_subjects:
            plain.append(f"  Â· {sender} â€” {subj}")
        plain.append("")

        if todo_lines:
            plain.append("Todos:")
            for t in todo_lines[:10]:
                plain.append(f"  Â· {t}")
        else:
            plain.append("Todos: none active.")

        plain_body = "\n".join(plain)

        return plain_body, True
    except Exception as e:
        logger.error(f"daily_brief action failed: {e}")
        return str(e), False


def _todo_digest_from_notes(
    notes,
    *,
    label: str | None = None,
    list_filter: str | None = None,
    limit: int = 20,
    owner: str | None = None,
    projection: dict | None = None,
) -> str:
    import hashlib as _hashlib
    import json as _json
    from datetime import datetime as _dt

    from src.todo_domain_service import make_list_ref

    label = (label or "").strip().lower()
    list_filter = (list_filter or "").strip().lower()
    today = _dt.now().date()
    overdue: list[str] = []
    due_today: list[str] = []
    pinned: list[str] = []
    open_items: list[tuple[str, str | None]] = []
    item_states: dict[str, dict[str, object]] = {}

    def _due_bucket(raw: str | None) -> str:
        if not raw:
            return ""
        try:
            due = _dt.fromisoformat(str(raw).replace("Z", "+00:00")).date()
        except Exception:
            return ""
        if due < today:
            return "overdue"
        if due == today:
            return "today"
        return ""

    for note in notes:
        if getattr(note, "archived", False):
            continue
        if label and (getattr(note, "label", "") or "").strip().lower() != label:
            continue
        title = (getattr(note, "title", "") or "Checklist").strip() or "Checklist"
        if list_filter and list_filter not in title.lower():
            continue
        if getattr(note, "pinned", False):
            pinned.append(title)
        bucket = _due_bucket(getattr(note, "due_date", None))
        if bucket == "overdue":
            overdue.append(title)
        elif bucket == "today":
            due_today.append(title)
        if getattr(note, "note_type", "") == "checklist" and getattr(note, "items", None):
            note_id = str(getattr(note, "id", "") or "")
            note_list_ref = make_list_ref(owner, note_id) if owner and note_id else None
            try:
                items = _json.loads(note.items or "[]")
            except Exception:
                items = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id") or "").strip()
                item_ref = f"todo-item:v1:{item_id}" if item_id else None
                done = bool(item.get("done"))
                if item_ref and note_list_ref:
                    item_states[item_ref] = {
                        "list_ref": note_list_ref,
                        "exists": True,
                        "done": done,
                    }
                if done:
                    continue
                text = " ".join(str(item.get("text") or "").split())
                if text:
                    open_items.append((f"{title}: {text}", item_ref))
        elif getattr(note, "pinned", False) and title:
            open_items.append((title, None))

    lines = ["Todo digest"]
    if overdue:
        lines.append("")
        lines.append("Overdue:")
        lines.extend(f"- {item}" for item in overdue[:limit])
    if due_today:
        lines.append("")
        lines.append("Due today:")
        lines.extend(f"- {item}" for item in due_today[:limit])
    if pinned:
        lines.append("")
        lines.append("Pinned:")
        lines.extend(f"- {item}" for item in pinned[:limit])
    lines.append("")
    lines.append("Open items:")
    if open_items:
        lines.extend(f"- {item[0]}" for item in open_items[:limit])
    else:
        lines.append("- none")
    if projection is not None:
        included_item_refs = [
            item_ref for _text, item_ref in open_items[:limit] if item_ref
        ]
        redacted_manifest = {
            "included_item_refs": included_item_refs,
            "item_states": item_states,
            "label_filter_active": bool(label),
            "list_filter_active": bool(list_filter),
            "limit": limit,
        }
        digest = _hashlib.sha256(
            _json.dumps(
                redacted_manifest,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:32]
        projection.update(
            schema="odysseus.todo_digest_projection.v1",
            included_item_refs=included_item_refs,
            item_states=item_states,
            projection_ref=f"todo-digest-projection:v1:{digest}",
            evidence_refs=[f"notes-digest-readback:v1:{digest}"],
            owner_scoped=bool(owner),
            raw_content_visible=False,
        )
    return collapse_repeated_open_item_list_prefixes("\n".join(lines))


def build_todo_digest_item_postcondition(
    *,
    owner: str,
    list_ref: str,
    item_ref: str,
    current_state: dict,
    label: str | None = None,
    list_filter: str | None = None,
    limit: int = 20,
    session_factory=None,
) -> dict:
    """Read Notes and return a content-free digest membership postcondition."""
    from core.database import Note, SessionLocal

    if not isinstance(owner, str) or not owner.strip():
        raise ValueError("owner is required for a Todo digest postcondition")
    db = (session_factory or SessionLocal)()
    try:
        notes = (
            db.query(Note)
            .filter(Note.owner == owner, Note.archived.is_(False))
            .order_by(Note.pinned.desc(), Note.updated_at.desc())
            .all()
        )
    finally:
        db.close()

    projection: dict = {}
    _todo_digest_from_notes(
        notes,
        label=label,
        list_filter=list_filter,
        limit=limit,
        owner=owner,
        projection=projection,
    )
    included = item_ref in projection.get("included_item_refs", ())
    observed = projection.get("item_states", {}).get(item_ref)
    expected_exists = current_state.get("exists") if isinstance(current_state, dict) else None
    expected_done = current_state.get("done") if isinstance(current_state, dict) else None
    if expected_exists is False:
        state_matches = observed is None and expected_done is None
        claim_type = "todo_digest_excludes"
        semantic_match = not included
    elif expected_exists is True and expected_done is True:
        state_matches = bool(
            isinstance(observed, dict)
            and observed.get("list_ref") == list_ref
            and observed.get("exists") is True
            and observed.get("done") is True
        )
        claim_type = "todo_digest_excludes"
        semantic_match = not included
    elif expected_exists is True and expected_done is False:
        state_matches = bool(
            isinstance(observed, dict)
            and observed.get("list_ref") == list_ref
            and observed.get("exists") is True
            and observed.get("done") is False
        )
        claim_type = "todo_digest_contains"
        semantic_match = included
    else:
        state_matches = False
        claim_type = "todo_digest_excludes"
        semantic_match = False
    return {
        "schema": "odysseus.todo_digest_postcondition.v1",
        "claim_type": claim_type,
        "list_ref": list_ref,
        "item_ref": item_ref,
        "included": included,
        "current_state": {
            "exists": expected_exists,
            "done": expected_done,
        },
        "projection_ref": projection.get("projection_ref"),
        "transaction_status": "projected",
        "verified": bool(
            projection.get("owner_scoped")
            and state_matches
            and semantic_match
        ),
        "evidence_refs": list(projection.get("evidence_refs") or ()),
        "raw_content_visible": False,
    }


async def action_todo_digest(owner: str, **kwargs) -> Tuple[str, bool]:
    """Build a todo-only digest without calendar/email noise."""
    try:
        from core.database import Note, SessionLocal
        from src.auth_helpers import owner_filter

        db = SessionLocal()
        try:
            query = db.query(Note).filter(Note.archived == False)  # noqa: E712
            if owner:
                query = owner_filter(query, Note, owner, include_shared=False)
            notes = query.order_by(Note.pinned.desc(), Note.updated_at.desc()).all()
        finally:
            db.close()
        digest = _todo_digest_from_notes(
            notes,
            label=kwargs.get("label"),
            list_filter=kwargs.get("list") or kwargs.get("list_filter"),
            limit=int(kwargs.get("limit") or 20),
            owner=owner,
        )
        return digest, True
    except Exception as e:
        logger.error(f"todo_digest action failed: {e}")
        return str(e), False


async def action_local_maintenance_dry_run(owner: str, **kwargs) -> Tuple[str, bool]:
    """Prepare local maintenance work without calling a model or writing truth."""
    try:
        import json as _json
        from src.gemma4_maintenance_router import plan_gemma4_maintenance_route

        plan = plan_gemma4_maintenance_route(
            surface=kwargs.get("surface") or "memory",
            workload=kwargs.get("workload"),
            classification=kwargs.get("classification") or "private",
            dsgvo_mode=bool(kwargs.get("dsgvo_mode", False)),
            input_chars=int(kwargs.get("input_chars") or 0),
            chunk_count=int(kwargs.get("chunk_count") or 1),
            source_refs=tuple(kwargs.get("source_refs") or (f"owner:{owner or 'default'}",)),
            excerpt="",
        )
        payload = plan.to_dict()
        payload["dry_run"] = True
        payload["truth_write_allowed"] = False
        payload["model_called"] = False
        return _json.dumps(payload, ensure_ascii=False, sort_keys=True), True
    except Exception as e:
        logger.error(f"local_maintenance_dry_run action failed: {e}")
        return str(e), False


async def action_test_skills(owner: str, **kwargs) -> Tuple[str, bool]:
    """Run the per-skill Test on every skill: agent runs the procedure in a
    sandbox, LLM judges the transcript, verdict is recorded on the skill.
    ADVISORY ONLY â€” only writes set_audit (never rewrites SKILL.md, never
    demotes status, never overrides confidence)."""
    try:
        from services.memory.skills import SkillsManager
        from src.constants import DATA_DIR
        from routes.skills_routes import _run_skill_test_once, _skill_test_task
        from src.endpoint_resolver import resolve_endpoint

        # #3 SCOPE GUARD: refuse to run on a None/empty owner â€” otherwise
        # `sm.load(owner=None)` returns every user's skills and we'd cross-
        # test (and write audit verdicts to) other users' data in a
        # multi-user deployment.
        if not owner:
            return "test_skills requires an owner on the task â€” refusing to run without scope.", False

        sm = SkillsManager(DATA_DIR)
        skills = sm.load(owner=owner)
        names = [s.get("name") for s in skills if s.get("name")]
        if not names:
            raise TaskNoop("no skills to test")

        url, model, headers = resolve_endpoint("default", owner=owner)
        if not url or not model:
            return "No Default/Utility model configured â€” set one in Settings.", False

        # #2 NO SILENT MODEL SWAP: if the configured model isn't served by the
        # endpoint, try a basename match â€” but fail loudly instead of grabbing
        # `avail[0]` which could be an embedding-only model and produce 36
        # garbage transcripts â†’ 36 'unknown' verdicts with no hint why.
        try:
            from src.llm_core import list_model_ids
            avail = list_model_ids(url, headers=headers)
            if avail and model not in avail:
                import os as _os
                base = _os.path.basename((model or "").rstrip("/"))
                m = next((a for a in avail if _os.path.basename(a.rstrip("/")) == base), None)
                if m:
                    model = m
                else:
                    return (f"Default model '{model}' not served by endpoint {url}. "
                            f"Available: {', '.join(avail[:8])}{'â€¦' if len(avail) > 8 else ''}. "
                            "Set a valid Default model in Settings."), False
        except Exception as _e:
            logger.warning(f"test_skills model resolve check failed (continuing): {_e}")

        logger.info(f"test_skills: starting on {len(names)} skills, model={model}, owner={owner!r}")

        from collections import Counter
        tally = Counter()
        per_skill_log = []
        for skill in skills:
            name = skill.get("name")
            if not name:
                continue
            md = sm.read_skill_md(name, owner=owner) or ""
            if not md:
                tally["skipped"] += 1
                per_skill_log.append(f"{name}: skipped (no SKILL.md)")
                continue
            task = _skill_test_task(skill)
            try:
                transcript, verdict = await _run_skill_test_once(md, task, url, model, headers, owner)
                v = (verdict or {}).get("verdict") or "unknown"
                tally[v] += 1
                summary = (verdict or {}).get("summary") or ""
                tlen = len(transcript or "")
                detail = ""
                if v in ("unknown", "inconclusive", "fail", "needs_work"):
                    bits = []
                    if summary: bits.append(summary[:160])
                    if tlen < 200: bits.append(f"transcript {tlen}b")
                    if bits: detail = " â€” " + "; ".join(bits)
                per_skill_log.append(f"{name}: {v}{detail}")
                # #4 + #8 + #12: ONLY persist a real verdict (pass / needs_work /
                # fail / inconclusive). Skip 'unknown' â€” that's the judge's
                # "couldn't parse" sentinel, not a real result, and persisting
                # it pollutes the verified-badge UI. Also skip the confidence
                # rewrite entirely â€” update_skill() re-serialises SKILL.md
                # (contradicts "advisory only" docstring) and overwriting a
                # user-set value (e.g. 1.0 â†’ 0.95) is destructive.
                if v in ("pass", "needs_work", "fail", "inconclusive"):
                    try:
                        sm.set_audit(name, v, by_teacher=False, worker_model=model, owner=owner)
                    except Exception as _e:
                        logger.warning(f"test_skills set_audit({name}) failed: {_e}")
                if v == "unknown":
                    logger.warning(f"test_skills: {name} â†’ unknown â€” {summary[:200]}; transcript_len={tlen}")
            except Exception as e:
                logger.exception(f"test_skills: {name} errored")
                tally["error"] += 1
                per_skill_log.append(f"{name}: error â€” {str(e)[:200]}")

        parts = []
        for k in ("pass", "needs_work", "fail", "inconclusive", "unknown", "skipped", "error"):
            if tally.get(k):
                parts.append(f"{tally[k]} {k}")
        header = f"Tested {len(names)} skill(s): " + (" Â· ".join(parts) or "0")
        # Multi-line result: summary first, then per-skill detail. The Tasks
        # Activity feed renders this verbatim, so the user can see per-skill
        # outcomes + the judge's "why" without checking uvicorn stdout.
        body = "\n".join(per_skill_log)
        return f"{header}\nmodel={model}\n\n{body}", True
    except TaskNoop:
        raise
    except Exception as e:
        logger.error(f"test_skills action failed: {e}")
        return str(e), False


async def action_audit_skills(owner: str, **kwargs) -> Tuple[str, bool]:
    """Run the real skills audit pipeline for skills that have not been audited.

    Unlike test_skills, this uses the same audit logic as the UI Audit all flow:
    metadata narrowing, self-edit/retry, optional teacher rewrite, necessity
    tagging, and publish/draft finalization from the user's confidence threshold.
    """
    try:
        from services.memory.skills import SkillsManager
        from src.constants import DATA_DIR
        from routes.skills_routes import (
            _resolve_audit_models, _run_audit_all_job, _skill_audit_jobs,
        )

        if not owner:
            return "audit_skills requires an owner â€” refusing to run without scope.", False

        key = (owner or "",)
        existing = _skill_audit_jobs.get(key)
        if existing and existing.get("status") == "running":
            raise TaskNoop("skill audit already running")

        sm = SkillsManager(DATA_DIR)
        skills = sm.load(owner=owner)
        names = [
            s.get("name") for s in skills
            if s.get("name") and not s.get("audit_verdict")
        ]
        if not names:
            raise TaskNoop("no unaudited skills")

        url, model, headers, teacher = _resolve_audit_models()
        try:
            from src.llm_core import seconds_since_model_activity
            recent = seconds_since_model_activity(url, model)
        except Exception:
            recent = None
        if recent is not None and recent < (20 * 60):
            raise TaskDeferred(
                f"audit model {model} was used {int(recent)}s ago; waiting for quiet window",
                delay_seconds=20 * 60,
            )

        import time as _time
        _skill_audit_jobs[key] = {
            "status": "running", "scope": "scheduled-unchecked", "model": model,
            "teacher": teacher[1] if teacher else None,
            "total": len(names), "done": 0, "current": None,
            "results": [], "log": [
                f"Scheduled audit of {len(names)} unaudited skill(s) with {model}"
                + (f"; teacher {teacher[1]}" if teacher else "")
            ],
            "started": _time.time(), "cancel": False,
        }
        await _run_audit_all_job(key, sm, names, url, model, headers, teacher, owner)
        job = _skill_audit_jobs.get(key, {})
        counts = {}
        for r in job.get("results", []):
            k = r.get("result") or "unknown"
            counts[k] = counts.get(k, 0) + 1
        summary = " Â· ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "0 results"
        return f"Audited {job.get('done', 0)}/{len(names)} unaudited skill(s): {summary}", True
    except TaskNoop:
        raise
    except Exception as e:
        logger.error(f"audit_skills action failed: {e}")
        return str(e), False


async def action_ping_notes(owner: str, **kwargs) -> Tuple[str, bool]:
    """Background note-due scanner. Fires a reminder for any note whose
    `due_date` falls in the current Â±5-minute window and hasn't been pinged
    within the last 25 minutes. Mirrors `action_ping_events` for calendar.

    State (`data/note_pings.json`): {note_id: iso_ts_of_last_ping}. Pruned
    on each run by dropping entries for notes that are gone/archived/replied.
    """
    try:
        import json as _json
        import time as _time
        from datetime import datetime as _dt, timezone as _tz, timedelta as _td
        from pathlib import Path as _P
        from core.database import SessionLocal as _SL, Note as _N

        # Per-owner state file so cache-pruning doesn't cross-delete other
        # users' entries (review C4). Legacy path kept as fallback so a
        # single-user install (empty owner) doesn't lose its history.
        _owner_slug = "".join(c if (c.isalnum() or c in "-_.@") else "_" for c in (owner or "default"))
        STATE = _P(DATA_DIR) / f"note_pings_{_owner_slug}.json"
        STATE.parent.mkdir(parents=True, exist_ok=True)
        # One-time migration: if legacy global file exists and per-owner file
        # doesn't, seed from global (entries for OTHER owners still get pruned
        # on their first run â€” acceptable, prevents silent loss).
        _legacy = _P(DATA_DIR) / "note_pings.json"
        if _legacy.exists() and not STATE.exists():
            try:
                STATE.write_text(_legacy.read_text(encoding="utf-8"), encoding="utf-8")
            except Exception:
                pass
        # Scanner ticks every 60s in _note_pings_loop. 90s window guarantees
        # every note's due time lands inside at least one tick's window.
        WINDOW_SEC = 90
        REPING_MIN = 25     # don't re-ping same note more often than this

        def _parse_due(s: str):
            """Accept '2026-05-29T16:31' (local) or '...Z' (UTC). Returns UTC datetime."""
            if not s:
                return None
            try:
                # Handle the JS-style 'Z' suffix.
                if s.endswith("Z"):
                    return _dt.fromisoformat(s[:-1]).replace(tzinfo=_tz.utc)
                # Naive â†’ assume local server time.
                d = _dt.fromisoformat(s)
                if d.tzinfo is None:
                    d = d.astimezone().astimezone(_tz.utc)
                return d.astimezone(_tz.utc)
            except Exception:
                return None

        try:
            cache = _json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
        except Exception:
            cache = {}

        db = _SL()
        try:
            q = db.query(_N).filter(_N.archived == False)  # noqa: E712
            q = q.filter(_N.due_date.isnot(None), _N.due_date != "")
            if owner:
                # Match owner OR legacy null-owner notes (single-user installs).
                q = owner_filter(q, _N, owner)
            notes = q.all()
            if not notes:
                raise TaskNoop("no notes with due dates")

            now = _dt.now(_tz.utc)
            window = _td(seconds=WINDOW_SEC)
            reping_cutoff = now - _td(minutes=REPING_MIN)
            seen_ids = set()
            sent = []

            for n in notes:
                seen_ids.add(n.id)
                due = _parse_due(n.due_date)
                if not due:
                    continue
                # Inside the Â±5min window?
                if abs((due - now).total_seconds()) > window.total_seconds():
                    continue
                # Recently pinged? Skip.
                last = cache.get(n.id)
                if last:
                    try:
                        if isinstance(last, dict):
                            last = last.get("at")
                        last_dt = _dt.fromisoformat(str(last))
                        if last_dt.tzinfo is None:
                            last_dt = last_dt.replace(tzinfo=_tz.utc)
                        if last_dt >= reping_cutoff:
                            continue
                    except Exception:
                        pass
                # Compose + dispatch.
                title = (n.title or "Reminder").strip() or "Reminder"
                body_parts = []
                if n.content:
                    body_parts.append(n.content[:400])
                # Items: list pending checklist entries inline.
                if n.items:
                    try:
                        items = _json.loads(n.items)
                        pending = [
                            it.get("text", "")
                            for it in items
                            if not it.get("done") and not it.get("checked")
                        ]
                        if pending:
                            body_parts.append("Pending:\n" + "\n".join(f"- {t}" for t in pending[:8]))
                    except Exception:
                        pass
                body = "\n\n".join(p for p in body_parts if p) or title
                try:
                    from routes.note_routes import dispatch_reminder
                    await dispatch_reminder(
                        title=title, note_body=body, note_id=n.id,
                        owner=n.owner or owner or "",
                    )
                    cache[n.id] = now.isoformat()
                    sent.append(title)
                except Exception as e:
                    logger.warning(f"ping_notes: dispatch failed for {n.id}: {e}")

            # Prune cache entries for notes that no longer exist.
            for stale in [k for k in cache if k not in seen_ids]:
                cache.pop(stale, None)

            try:
                STATE.write_text(_json.dumps(cache), encoding="utf-8")
            except Exception as e:
                logger.warning(f"ping_notes: cache write failed: {e}")

            if not sent:
                raise TaskNoop(f"scanned {len(notes)} note(s), none due in Â±{WINDOW_SEC}s")
            preview = "; ".join(sent[:3])
            extra = f" (+{len(sent) - 3} more)" if len(sent) > 3 else ""
            return f"Pinged {len(sent)} note(s): {preview}{extra}", True
        finally:
            db.close()
    except TaskNoop:
        raise
    except Exception as e:
        logger.exception("ping_notes action failed")
        return str(e), False


from src.builtin_action_email_urgency import action_check_email_urgency


async def action_cookbook_serve(
    owner: str,
    task_name: str = "",
    progress_cb=None,
    command: str = "",
    **kwargs,
) -> Tuple[str, bool]:
    """Launch a Cookbook model serve as a scheduled task.

    `command` is the JSON config string the task carries in `prompt`,
    of shape: {"preset": "name"} OR {"repo_id": "...", "cmd": "...", "host": "..."}.
    Optional `end_after_min: N` schedules a hard-stop N minutes after launch
    (handled by cookbook_serve_lifecycle_loop in src/cookbook_serve_lifecycle.py).
    """
    import json
    import time as _time
    import httpx
    from pathlib import Path
    from core.middleware import INTERNAL_TOOL_HEADER, INTERNAL_TOOL_TOKEN
    from core.atomic_io import atomic_write_json

    headers = {INTERNAL_TOOL_HEADER: INTERNAL_TOOL_TOKEN}
    try:
        cfg = json.loads(command or "{}")
    except Exception:
        return f"Invalid JSON config: {command!r}", False
    if not isinstance(cfg, dict):
        return "Config must be a JSON object", False

    # Resolve the preset (if named) OR fall through with explicit fields.
    preset_name = (cfg.get("preset") or "").strip()
    repo_id = (cfg.get("repo_id") or "").strip()
    cmd = (cfg.get("cmd") or "").strip()
    host = (cfg.get("host") or cfg.get("remote_host") or "").strip()
    try:
        end_after_min = int(cfg.get("end_after_min") or 0)
    except Exception:
        end_after_min = 0

    state_path = Path(COOKBOOK_STATE_FILE)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    except Exception:
        state = {}

    # Preset lookup. Try three matching strategies in order so the
    # schedule still works even when the user's preset is named
    # differently from the model's short name:
    #
    #   1. Exact preset.name == preset_name (case-insensitive)
    #   2. preset.model / preset.modelId == repo_id  (caller knows the repo)
    #   3. preset.model's short name (after final /) == preset_name
    #
    # Without #2 and #3, scheduling "Qwen3.5-397B-A17B-AWQ" failed when
    # the saved preset was named "vllm-qwen-397b" or had the model field
    # populated with the full HF repo path. Either should resolve.
    def _short(name: str) -> str:
        return (name or "").rsplit("/", 1)[-1].lower()

    if not cmd or not repo_id:
        presets = state.get("presets") or []
        chosen = None
        # Strategy 1: exact name match.
        if preset_name:
            chosen = next(
                (p for p in presets if isinstance(p, dict)
                 and (p.get("name") or "").lower() == preset_name.lower()),
                None,
            )
        # Strategy 2: repo_id matches the preset's model field.
        if chosen is None and repo_id:
            chosen = next(
                (p for p in presets if isinstance(p, dict)
                 and (p.get("model") or p.get("modelId") or "").lower() == repo_id.lower()),
                None,
            )
        # Strategy 3: model's short name matches the preset_name.
        if chosen is None and preset_name:
            chosen = next(
                (p for p in presets if isinstance(p, dict)
                 and _short(p.get("model") or p.get("modelId") or "") == preset_name.lower()),
                None,
            )
        if chosen is not None:
            repo_id = repo_id or chosen.get("model") or chosen.get("modelId") or ""
            cmd = cmd or (chosen.get("cmd") or "").strip()
            host = host or chosen.get("host") or chosen.get("remoteHost") or ""
    if not repo_id or not cmd or cmd.startswith("(adopted"):
        # Surface what we tried so the user can name their preset to match.
        preset_names = [(p.get("name") or "") for p in (state.get("presets") or []) if isinstance(p, dict)]
        hint = f" Saved presets: {preset_names!r}" if preset_names else ""
        return (f"No launchable config for {preset_name!r} (repo_id={repo_id!r}). "
                f"Check Cookbook â†’ Presets has a real cmd, not 'adopted'.{hint}", False)

    # Resolve env_prefix etc. from the host's saved cookbook server entry,
    # matching the chat agent's serve_model path.
    body = {"repo_id": repo_id, "cmd": cmd}
    if host:
        body["remote_host"] = host
    env = (state.get("env") or {})
    srv = next(
        (s for s in (env.get("servers") or [])
         if isinstance(s, dict) and (s.get("host") == host or s.get("name") == host)),
        {},
    )
    if srv.get("env") == "venv" and srv.get("envPath"):
        body["env_prefix"] = f"source {srv['envPath']}/bin/activate"
    elif srv.get("env") == "conda" and srv.get("envPath"):
        body["env_prefix"] = f"conda activate {srv['envPath']}"
    if srv.get("hfToken"): body["hf_token"] = srv["hfToken"]
    if srv.get("port"): body["ssh_port"] = str(srv["port"])
    if srv.get("platform"): body["platform"] = srv["platform"]

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{internal_api_base()}/api/model/serve",
                                  json=body, headers=headers)
            data = r.json() if r.content else {}
    except Exception as e:
        return f"Launch HTTP failed: {e}", False
    if not data.get("ok"):
        return f"Launch rejected: {data.get('error') or data.get('detail') or 'unknown'}", False

    sid = data.get("session_id") or ""
    # Register the new task in cookbook_state.json + stamp it with our
    # scheduler-owner markers. /api/model/serve spawns the tmux session
    # but leaves the state-write to the UI â€” when a scheduled action
    # launches a serve from server-side, NOBODY writes the task into
    # state, so the Cookbook tab never shows it. We do the write here.
    if sid:
        try:
            # Re-read fresh (the route may have updated state already).
            try:
                fresh = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                fresh = {}
            if not isinstance(fresh, dict):
                fresh = {}
            tasks = fresh.get("tasks") if isinstance(fresh.get("tasks"), list) else []
            existing = next(
                (t for t in tasks if isinstance(t, dict) and t.get("sessionId") == sid),
                None,
            )
            if existing is None:
                display_name = repo_id.split("/")[-1] if "/" in repo_id else repo_id
                placeholder = (
                    f"Launched by scheduled task {task_name!r} â€” waiting for tmux outputâ€¦\n"
                    f"  session: {sid}\n"
                    f"  target:  {host or 'local'}\n"
                    f"  cmd:     {cmd[:200]}{'â€¦' if len(cmd) > 200 else ''}"
                )
                existing = {
                    "id": sid,
                    "sessionId": sid,
                    "name": display_name,
                    "modelId": repo_id,
                    "type": "serve",
                    "status": "running",
                    "output": placeholder,
                    "ts": int(_time.time() * 1000),
                    "payload": {"repo_id": repo_id, "remote_host": host or "", "_cmd": cmd},
                    "remoteHost": host or "",
                    "sshPort": "",
                    "platform": "linux",
                    "_serveReady": False,
                    "_endpointAdded": False,
                }
                tasks.append(existing)
            # Stamp ownership + end-at on the task entry.
            existing["_scheduledByTask"] = task_name or ""
            existing["_scheduledByOwner"] = owner or ""
            if end_after_min > 0:
                existing["_scheduledStopAtMs"] = int(_time.time() * 1000) + end_after_min * 60 * 1000
            fresh["tasks"] = tasks
            atomic_write_json(state_path, fresh)
        except Exception as e:
            logger.warning(f"cookbook_serve: state register/stamp failed: {e}")
    # Don't try to render absolute clock time in the message â€” the
    # server runs in UTC (Docker default), the user reads it as local,
    # and the offset depends on the user's TZ which the action doesn't
    # have a reliable handle on. The Tasks UI already shows the RUN
    # timestamp in the user's local time right above this message, so
    # "stops 8 min after that" gives the user everything they need.
    if end_after_min:
        return (
            f"Launched {repo_id} (session {sid}); stops {end_after_min} min after this ran",
            True,
        )
    return f"Launched {repo_id} (session {sid})", True


BUILTIN_ACTIONS = {
    "tidy_sessions": action_tidy_sessions,
    "tidy_documents": action_tidy_documents,
    "consolidate_memory": action_consolidate_memory,
    "tidy_research": action_tidy_research,
    "summarize_emails": action_summarize_emails,
    "draft_email_replies": action_draft_email_replies,
    "extract_email_events": action_extract_email_events,
    "classify_events": action_classify_events,
    # ping_events removed from the user-facing registry. Calendar reminders
    # are represented as Notes, so note pings are the single dispatch path.
    "daily_brief": action_daily_brief,
    "todo_digest": action_todo_digest,
    "local_maintenance_dry_run": action_local_maintenance_dry_run,
    "learn_sender_signatures": action_learn_sender_signatures,
    "ssh_command": action_ssh_command,
    "run_script": action_run_script,
    "run_local": action_run_local,
    "test_skills": action_test_skills,
    "audit_skills": action_audit_skills,
    "check_email_urgency": action_check_email_urgency,
    "cookbook_serve": action_cookbook_serve,
    # ping_notes removed from the registry â€” runs only inside `_note_pings_loop`.
}

# Descriptions for the UI/API
BUILTIN_ACTION_INFO = {
    "tidy_sessions": "Clean up empty chat sessions and auto-sort into folders",
    "tidy_documents": "Remove junk/empty documents",
    "consolidate_memory": "Remove duplicate memories",
    "tidy_research": "Remove orphaned research files (sessions that were deleted)",
    "summarize_emails": "Pre-generate AI summaries for new inbox emails",
    "draft_email_replies": "Pre-draft AI reply suggestions for new inbox emails",
    "extract_email_events": "Scan emails for booking/meeting confirmations and auto-add to calendar",
    "classify_events": "Tag upcoming events with importance (low/normal/high/critical) and type (work/health/travel/etc.); colors them too",
    "daily_brief": "Build a morning digest: today's calendar, unread email count + top senders, active todos",
    "todo_digest": "Build a todo-only digest with open checklist items, overdue items, due-today items, and pinned lists",
    "local_maintenance_dry_run": "Prepare review-only local maintenance model work without live LLM calls or truth writes",
    "learn_sender_signatures": "LLM learns each sender's signature from 3+ of their recent emails; cached per address so future renders fold sigs reliably without heuristics",
    "ssh_command": "Run a shell command on a local or remote host",
    "run_script": "Run a script locally or on ODYSSEUS_SCRIPT_HOST",
    "test_skills": "Run the per-skill Test on every skill: agent run + LLM judge â†’ records verdict on the skill (pass/needs_work/fail/inconclusive). Advisory only â€” never rewrites or demotes anything.",
    "audit_skills": "Audit unaudited skills after enough new skills are added: test, narrow metadata, self-edit/retry, optional teacher rewrite, tag duplicates/trivial skills, and publish/draft using the auto-approve threshold.",
    "check_email_urgency": "Scan unread emails hourly, tag urgent/reply-soon/newsletter/marketing/spam, and send a reminder when a new email needs a fast reply.",
}
