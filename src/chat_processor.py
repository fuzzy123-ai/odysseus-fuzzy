# src/chat_processor.py
import hashlib
import logging
import math
import re
import time
from collections import Counter
from typing import List, Dict, Any, Optional, Tuple
from src.chat_helpers import extract_urls
from src.youtube_handler import is_youtube_url
from src.search import comprehensive_web_search, fetch_webpage_content
from src.context_orchestrator import (
    preload_provider_context,
    provider_messages,
    provider_warning_messages,
    split_context_budget,
)
from src.prompt_security import UNTRUSTED_CONTEXT_POLICY, untrusted_context_message
from src.settings import load_features
from src.chat_security_state import SecurityMode
from src.data_classification import DataClassification
from src.privacy_runtime import EXTERNAL_IO_BLOCK_MESSAGE, create_runtime_security_state, runtime_allows_external_io
from src.secure_model_routing import ModelCandidate, ModelUse, decide_model_route
from src.secure_provider_runtime import provider_scope_for_base_url
from src.sensitive_retrieval_guard import decide_retrieval_access
from src.self_control_runtime import build_self_control_context_message
from src.token_budget import CHARS_PER_TOKEN_ESTIMATE, count_text_tokens

logger = logging.getLogger(__name__)
DEFAULT_RAG_CONTEXT_BUDGET_UNITS = 1200

# ── Stopwords & tokenizer ──

_STOPWORDS = frozenset(
    "a an the is am are was were be been being have has had do does did "
    "will would shall should can could may might must need ought dare "
    "i me my mine we us our ours you your yours he him his she her hers "
    "it its they them their theirs this that these those "
    "and but or nor not no so if then else than too also very "
    "in on at to for of by with from up out about into over after "
    "what when where which who whom how why all each every some any "
    "just very really actually like well also still already even "
    "oh ok okay yes yeah hey hi hello thanks thank please sorry "
    "much more most own other another such only same here there "
    "because while during before until since through between both "
    "few many several some none nothing something anything everything "
    "get got make made go going went been come came take took "
    "know think want let say tell give see look find way thing "
    "don doesn didn won wouldn couldn shouldn wasn weren isn aren haven hasn "
    "don't doesn't didn't won't wouldn't couldn't shouldn't "
    "it's i'm i've i'll i'd you're you've you'll he's she's we're we've they're they've "
    "that's there's here's what's who's how's let's can't".split()
)

def _content_tokens(text: str) -> list:
    """Extract meaningful content words: no stopwords, min 3 chars, lowercase."""
    words = re.findall(r'[a-z0-9]+(?:[-_][a-z0-9]+)*', text.lower())
    return [w for w in words if len(w) >= 3 and w not in _STOPWORDS]


def rag_context_budget_units(context_budget_tokens: Optional[int]) -> int:
    if not context_budget_tokens:
        return DEFAULT_RAG_CONTEXT_BUDGET_UNITS
    return max(256, min(4000, int(context_budget_tokens * 0.18)))


def build_budgeted_rag_context(
    relevant: List[Dict[str, Any]],
    *,
    budget_units: int,
) -> tuple[str, List[Dict[str, Any]], int]:
    """Build RAG context within a metadata-estimated budget."""

    prefix = "Relevant documents:"
    remaining = max(0, int(budget_units or 0) - count_text_tokens(prefix))
    sources: List[Dict[str, Any]] = []
    entries: List[str] = []
    truncated = 0
    for result in relevant:
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        filename = metadata.get("filename", metadata.get("source", "unknown"))
        document = str(result.get("document") or "")
        header = f"[{filename}]\n"
        header_units = count_text_tokens(header)
        if remaining <= header_units:
            truncated += 1
            continue
        document_budget = remaining - header_units
        trimmed_document, was_truncated = _trim_text_to_budget(document, document_budget)
        if not trimmed_document:
            truncated += 1
            continue
        entry = header + trimmed_document
        entry_units = count_text_tokens(entry)
        entries.append(entry)
        sources.append(
            {
                "filename": filename,
                "snippet": trimmed_document[:200],
                "similarity": round(result.get("similarity", 0), 3),
                "splitter_version": metadata.get("splitter_version", ""),
                "char_start": metadata.get("char_start"),
                "char_end": metadata.get("char_end"),
                "budget_units_est": entry_units,
                "truncated": was_truncated,
            }
        )
        remaining -= entry_units + count_text_tokens("\n\n---\n\n")
        if was_truncated:
            truncated += 1
            break
    if not entries:
        return "", [], truncated
    return prefix + "\n\n" + "\n\n---\n\n".join(entries), sources, truncated


def _trim_text_to_budget(text: str, budget_units: int) -> tuple[str, bool]:
    if budget_units <= 0 or not text:
        return "", bool(text)
    if count_text_tokens(text) <= budget_units:
        return text, False
    char_limit = max(1, int(budget_units * CHARS_PER_TOKEN_ESTIMATE))
    return text[:char_limit].rstrip() + "\n[Truncated]", True


class ChatProcessor:
    def __init__(self, memory_manager, personal_docs_manager, memory_vector=None, skills_manager=None):
        self.memory_manager = memory_manager
        self.personal_docs_manager = personal_docs_manager
        self.memory_vector = memory_vector
        self.skills_manager = skills_manager

    # Minimum similarity score for RAG results to be injected
    RAG_SIMILARITY_THRESHOLD = 0.35

    @staticmethod
    def _retrieval_source_id(surface: str, idx: int, raw_id: Any) -> str:
        digest = hashlib.sha256(str(raw_id or idx).encode("utf-8", errors="ignore")).hexdigest()[:16]
        return f"{surface}-{idx}-{digest}"

    @staticmethod
    def _classification_for_mapping(item: Dict[str, Any]) -> DataClassification:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        raw = (
            item.get("classification")
            or item.get("ai_classification")
            or metadata.get("classification")
            or metadata.get("ai_classification")
        )
        if str(raw or "").strip().lower() in {"local_sensitive", "sensitive_root_runtime_match"}:
            return DataClassification.SENSITIVE
        if metadata.get("local_model_only") is True or item.get("local_model_only") is True:
            return DataClassification.SENSITIVE
        if str(raw or "").strip().lower() in {item.value for item in DataClassification}:
            return DataClassification(str(raw).strip().lower())
        return DataClassification.PRIVATE

    def _retrieval_security_context(self, session: Any, owner: Optional[str]):
        state = create_runtime_security_state(
            chat_id=getattr(session, "id", None) or "pending-chat",
            thread_id=getattr(session, "id", None) or "pending-chat",
            security_mode=getattr(session, "security_mode", None),
            requested_by=owner or "chat-processor",
        )
        if state.security_mode != SecurityMode.SECURE:
            return state, None

        endpoint_url = getattr(session, "endpoint_url", "") if session is not None else ""
        model_id = getattr(session, "model", "") if session is not None else ""
        provider_scope = provider_scope_for_base_url(endpoint_url)
        route = decide_model_route(
            state=state,
            primary=ModelCandidate.create(
                model_id=model_id or "pending-model",
                provider_id="session-provider",
                provider_scope=provider_scope,
                use=ModelUse.CHAT,
            ),
        )
        return state, route

    def _filter_retrieval_context(
        self,
        items: List[Dict[str, Any]],
        *,
        surface: str,
        session: Any,
        owner: Optional[str],
        id_getter,
    ) -> List[Dict[str, Any]]:
        if not items:
            return []

        state, model_route = self._retrieval_security_context(session, owner)
        source_refs = []
        by_ref_id: Dict[str, Dict[str, Any]] = {}
        for idx, item in enumerate(items):
            source_id = self._retrieval_source_id(surface, idx, id_getter(item, idx))
            source_refs.append((source_id, self._classification_for_mapping(item)))
            by_ref_id[source_id] = item

        try:
            decision = decide_retrieval_access(
                state=state,
                surface=surface,
                sources=source_refs,
                model_route=model_route,
            )
        except Exception as exc:
            logger.warning("%s retrieval guard failed closed: %s", surface, exc)
            return []

        if not decision.allowed:
            logger.warning(
                "%s retrieval context blocked: %s next_action=%s",
                surface,
                decision.block_reason,
                decision.next_action,
            )
            return []

        allowed_ids = set(decision.context_ref_ids)
        return [by_ref_id[source_id] for source_id, _classification in source_refs if source_id in allowed_ids]

    def _hybrid_retrieve(self, message: str, mem_entries: list, k: int = 5) -> list:
        """Retrieve memories relevant to the message.

        Uses BM25-style keyword scoring + optional vector similarity.
        Recency is a tiebreaker only, never the primary signal.
        """
        if not mem_entries or not message.strip():
            return []

        now = time.time()
        query_tokens = _content_tokens(message)

        # If the query has no meaningful tokens, skip keyword retrieval entirely
        if not query_tokens:
            # Fall back to vector-only if available
            if not (self.memory_vector and self.memory_vector.healthy):
                return []

        # ── Build IDF from the memory corpus ──
        N = len(mem_entries)
        doc_freq = Counter()  # token -> how many memories contain it
        mem_token_cache = {}  # mem_id -> set of content tokens
        for mem in mem_entries:
            toks = set(_content_tokens(mem["text"]))
            mem_token_cache[mem["id"]] = toks
            for t in toks:
                doc_freq[t] += 1

        def _bm25_score(query_toks, mem_id):
            """BM25-inspired score between query and a memory."""
            mem_toks = mem_token_cache.get(mem_id, set())
            if not mem_toks or not query_toks:
                return 0.0
            score = 0.0
            mem_len = len(mem_toks)
            avg_len = max(sum(len(v) for v in mem_token_cache.values()) / N, 1)
            k1, b = 1.5, 0.75
            for qt in query_toks:
                if qt not in mem_toks:
                    continue
                df = doc_freq.get(qt, 0)
                idf = math.log((N - df + 0.5) / (df + 0.5) + 1)
                tf = 1  # binary presence (memory entries are short)
                tf_norm = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * mem_len / avg_len))
                score += idf * tf_norm
            return score

        # ── Score all candidates ──
        has_vector = self.memory_vector and self.memory_vector.healthy
        vector_scores = {}

        if has_vector:
            results = self.memory_vector.search(message, k=min(k * 3, 20))
            mem_by_id = {m["id"]: m for m in mem_entries}
            for r in results:
                if r["memory_id"] in mem_by_id:
                    vector_scores[r["memory_id"]] = max(r["score"], 0.0)

        scored = []
        for mem in mem_entries:
            mid = mem["id"]
            vs = vector_scores.get(mid, 0.0)
            kw = _bm25_score(query_tokens, mid)

            # Normalize BM25 to roughly 0-1 range (cap at a reasonable max)
            kw_norm = min(kw / 6.0, 1.0) if kw > 0 else 0.0

            # Category-aware boost for identity/contact queries
            category = mem.get("category", "fact")
            msg_lower = message.lower()
            mem_lower = mem["text"].lower()
            cat_boost = 1.0
            if any(w in msg_lower for w in ["name", "who am i", "my name"]):
                if category == "identity" or any(w in mem_lower for w in ["name is", "i am", "called"]):
                    cat_boost = 1.4
            elif any(w in msg_lower for w in ["phone", "email", "address", "contact"]):
                if category == "contact" or "@" in mem_lower:
                    cat_boost = 1.3
            elif any(w in msg_lower for w in ["like", "prefer", "favorite"]):
                if category == "preference":
                    cat_boost = 1.2

            kw_norm = min(kw_norm * cat_boost, 1.0)

            # Recency — tiebreaker only (max 5% contribution)
            ts = mem.get("timestamp", 0)
            days_old = max((now - ts) / 86400, 0)
            recency = 1.0 / (1.0 + days_old * 0.05)

            # Gate: need real relevance, not just recency
            if has_vector:
                if vs < 0.20 and kw_norm < 0.08:
                    continue
                final = (0.55 * vs) + (0.40 * kw_norm) + (0.05 * recency)
            else:
                if kw_norm < 0.08:
                    continue
                final = (0.95 * kw_norm) + (0.05 * recency)

            if final > 0.12:
                scored.append((final, mem))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [mem for _, mem in scored[:k]]

    def build_context_preface(
        self,
        message: str,
        session: Any,
        use_web: bool = False,
        use_rag: bool = True,
        use_memory: bool = True,
        time_filter: Optional[str] = None,
        preset_system_prompt: Optional[str] = None,
        owner: Optional[str] = None,
        character_name: Optional[str] = None,
        agent_mode: bool = False,
        incognito: bool = False,
        use_skills: bool = True,
        use_context_providers: bool = True,
        context_budget_tokens: Optional[int] = None,
    ) -> Tuple[List[Dict[str, str]], List[Dict[str, Any]], List[Dict[str, str]]]:
        """Build the context preface for LLM calls.

        Returns:
            Tuple of (preface messages, rag_sources list)

        Note on KV-cache friendliness: the ``system``-role messages assembled
        here are later concatenated into a single system message and sent as
        the very first thing in the payload (see ``llm_core``'s "consolidate
        system messages" step). Local OpenAI-compatible backends (llama.cpp /
        LM Studio) key their KV cache off the byte-identical token prefix, so
        *anything* that changes turn-to-turn — timestamps, retrieved snippets,
        per-turn counts — must NOT be folded into a system message here. Such
        content belongs in a separate ``user``/context message appended near
        the end of the array (see ``current_datetime_context_message`` and
        ``untrusted_context_message`` callers in ``build_chat_context``),
        which keeps the static system prefix byte-identical across turns of
        the same session and lets the backend reuse its cached prefix.
        """
        preface = []
        rag_sources = []
        self._last_context_provider_payloads = []
        self._last_context_provider_warnings = []

        # Add preset system prompt if specified
        if preset_system_prompt:
            preface.append({
                "role": "system",
                "content": preset_system_prompt
            })
        preface.append({
            "role": "system",
            "content": UNTRUSTED_CONTEXT_POLICY,
        })
        try:
            preface.append(build_self_control_context_message(
                session=session,
                owner=owner,
                message=message,
            ))
        except Exception:
            logger.debug("Failed to add self-control runtime context", exc_info=True)
        if use_context_providers and not incognito and load_features().get("context_provider_preload", True):
            try:
                budget = split_context_budget(context_budget_tokens or 4000)
                provider_payloads, provider_warnings = preload_provider_context(
                    owner=owner,
                    query=message,
                    budget_tokens=budget.providers,
                    mode="agent" if agent_mode else "chat",
                )
                preface.extend(provider_messages(provider_payloads))
                preface.extend(provider_warning_messages(provider_warnings))
                self._last_context_provider_payloads = provider_payloads
                self._last_context_provider_warnings = provider_warnings
                for warning in provider_warnings:
                    logger.warning("Context provider warning: %s", warning)
            except Exception as e:
                logger.warning("Context provider preload failed: %s", e)

        # Memory: pinned (always included) + extended (RAG-retrieved when relevant)
        self._last_used_memories = []  # track what was injected
        if use_memory:
            mem_entries = self.memory_manager.load(owner=owner)

            pinned = [m for m in mem_entries if m.get("pinned")]
            extended = [m for m in mem_entries if not m.get("pinned")]

            _used_ids: list = []
            if pinned:
                pinned = self._filter_retrieval_context(
                    pinned,
                    surface="memory",
                    session=session,
                    owner=owner,
                    id_getter=lambda item, _idx: item.get("id") or item.get("text", ""),
                )
            if pinned:
                pinned_text = "\n- ".join([m["text"] for m in pinned])
                preface.append(untrusted_context_message(
                    "saved memory: pinned user facts",
                    f"Core facts about the user:\n- {pinned_text}",
                ))
                for m in pinned:
                    self._last_used_memories.append({"text": m["text"], "category": m.get("category", "fact"), "type": "pinned"})
                    if m.get("id"):
                        _used_ids.append(m["id"])

            if extended:
                relevant = self._hybrid_retrieve(message, extended, k=3)
                relevant = self._filter_retrieval_context(
                    relevant,
                    surface="memory",
                    session=session,
                    owner=owner,
                    id_getter=lambda item, _idx: item.get("id") or item.get("text", ""),
                )
                if relevant:
                    ext_text = "\n".join([f"- {m['text']}" for m in relevant])
                    preface.append(untrusted_context_message(
                        "saved memory: retrieved context",
                        (
                            "Memory context. Do not reference unless the user asks "
                            f"about these topics.\n{ext_text}"
                        ),
                    ))
                    for m in relevant:
                        self._last_used_memories.append({"text": m["text"], "category": m.get("category", "fact"), "type": "recalled"})
                        if m.get("id"):
                            _used_ids.append(m["id"])

            # Bump usage counters for the memories that were actually injected.
            if _used_ids and hasattr(self.memory_manager, "increment_uses"):
                try:
                    self.memory_manager.increment_uses(_used_ids)
                except Exception as _e:
                    logger.warning("Failed to increment memory uses: %s", _e)

            # (skills index injection moved out — see below; only fires in
            # agent mode so chat mode and incognito stay clean.)

        # RAG: search if enabled and rag_manager available, inject only above threshold
        if use_rag:
            try:
                rag_manager = getattr(self.personal_docs_manager, 'rag_manager', None)
                if rag_manager:
                    results = rag_manager.search(message, k=5, owner=owner)
                    # Filter by similarity threshold
                    relevant = [r for r in results if r.get("similarity", 0) >= self.RAG_SIMILARITY_THRESHOLD]
                    relevant = self._filter_retrieval_context(
                        relevant,
                        surface="rag",
                        session=session,
                        owner=owner,
                        id_getter=lambda item, idx: (
                            item.get("id")
                            or item.get("metadata", {}).get("source")
                            or item.get("metadata", {}).get("filename")
                            or idx
                        ),
                    )
                    if relevant:
                        logger.info(f"RAG: {len(relevant)}/{len(results)} results above threshold {self.RAG_SIMILARITY_THRESHOLD}")
                        rag_content, rag_sources, truncated_count = build_budgeted_rag_context(
                            relevant,
                            budget_units=rag_context_budget_units(context_budget_tokens),
                        )
                        if rag_content:
                            if truncated_count:
                                logger.info("RAG context budget truncated %s result(s)", truncated_count)
                            preface.append(untrusted_context_message("retrieved documents", rag_content))
            except Exception as e:
                logger.warning(f"RAG retrieval failed: {e}")

        # Add web search if enabled
        web_sources = []
        external_io_allowed = runtime_allows_external_io()
        if use_web:
            if not external_io_allowed:
                logger.info("Web search skipped by DSGVO runtime policy")
                preface.append(untrusted_context_message("web access policy", EXTERNAL_IO_BLOCK_MESSAGE))
            else:
                try:
                    web_context, web_sources = comprehensive_web_search(
                        message, time_filter=time_filter, return_sources=True
                    )
                    preface.append(untrusted_context_message("web search results", web_context))
                except Exception as e:
                    logger.error(f"Web search failed: {e}")
                    preface.append({"role": "system", "content": "Web search encountered an error and could not retrieve results."})

        # Process non-YouTube URLs in message (YouTube handled by preprocess_message)
        # Skip auto-fetch for long pastes (the user already pasted the content —
        # fetching every embedded link buries the actual question under
        # hundreds of KB of duplicate page HTML and confuses the model) or for
        # link-heavy pastes (>3 URLs typically means it's a boilerplate-laden
        # blog post, not a "summarize this URL" request).
        urls = extract_urls(message)
        non_yt_urls = [u for u in urls if not is_youtube_url(u)]
        skip_url_fetch = len(message) > 2000 or len(non_yt_urls) > 3
        if not skip_url_fetch:
            if non_yt_urls and not external_io_allowed:
                logger.info("URL auto-fetch skipped by DSGVO runtime policy")
                preface.append(untrusted_context_message("web page auto-fetch policy", EXTERNAL_IO_BLOCK_MESSAGE))
            else:
                for url in non_yt_urls:
                    result = fetch_webpage_content(url)
                    if result.get('success'):
                        content = result.get('content', '')[:10000]
                        preface.append(untrusted_context_message(
                            f"web page: {url}",
                            f"Content from {url}:\n\n{content}",
                        ))

        # Skills index — progressive disclosure. Only injected when the
        # model has the `manage_skills` tool available (agent_mode), and
        # never in incognito mode (the user has explicitly opted out of
        # context retention this turn). In plain chat mode the model can't
        # call the tool anyway, so the index would be noise.
        if agent_mode and not incognito and use_skills and self.skills_manager:
            try:
                idx = self.skills_manager.index_for(owner=owner)
            except Exception as e:
                logger.debug(f"Skills index unavailable: {e}")
                idx = []
            if idx:
                by_cat: Dict[str, list] = {}
                for s in idx:
                    by_cat.setdefault(s.get("category") or "general", []).append(s)
                lines = ["[Available skills — call manage_skills(action='view', name='...') to load one when relevant]"]
                for cat in sorted(by_cat):
                    lines.append(f"  {cat}:")
                    for s in sorted(by_cat[cat], key=lambda x: x["name"]):
                        desc = s.get("description") or ""
                        lines.append(f"    - {s['name']}: {desc}" if desc else f"    - {s['name']}")
                preface.append(untrusted_context_message("available skills index", "\n".join(lines)))

        return preface, rag_sources, web_sources
