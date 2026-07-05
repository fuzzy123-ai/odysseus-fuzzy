import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import HTTPException

from src.endpoint_resolver import (
    resolve_chat_fallback_candidates,
    resolve_endpoint,
    resolve_endpoint_by_id,
)
from src.llm_core import llm_call_async
from src.model_episode_store import append_model_episode
from src.model_context import DEFAULT_CONTEXT, get_context_length, is_local_endpoint
from src.model_reward_contract import ModelEpisode, ModelEpisodeAction, ModelEpisodeOutcome, ModelEpisodeState
from src.model_reward_scorer import score_episode
from src.model_routing_policy import ModelRoutingCandidate, recommend_model_routing
from src.settings import get_user_setting

logger = logging.getLogger(__name__)

MEMORY_ROLE_SETTINGS: Dict[str, str] = {
    "memory.router": "memory.router_model",
    "memory.answer": "memory.answer_model",
    "memory.summarize": "memory.summarize_model",
    "memory.graph_label": "memory.graph_extract_model",
    "memory.review": "memory.global_synthesis_model",
    "memory.embed": "memory.embedding_model",
}

MEMORY_FALLBACK_SETTINGS = {
    "memory.answer": "memory.answer_fallback_models",
}

_SECRET_PATTERNS: Sequence[str] = (
    "sk-",
    "Bearer ",
    "Authorization",
    "api_key",
    "token",
    "secret",
)


@dataclass(frozen=True)
class _Candidate:
    mode: str
    url: str
    model: str
    headers: Dict[str, str]
    endpoint_id: str
    provider: str
    context_tokens: int
    warnings: Tuple[str, ...]
    source: str


def _normalize_mode(value: str) -> str:
    mode = str(value or "auto").strip().lower()
    if mode not in {"auto", "cloud", "local", "extractive"}:
        return "auto"
    return mode


def _provider_name(url: str) -> str:
    host = str(url or "").strip().lower()
    if not host:
        return ""
    if "deepseek" in host:
        return "DeepSeek"
    if "openrouter" in host:
        return "OpenRouter"
    if "openai" in host:
        return "OpenAI"
    if "anthropic" in host:
        return "Anthropic"
    if "ollama" in host:
        return "Ollama"
    if "localhost" in host or "127.0.0.1" in host:
        return "Local"
    return "OpenAI-compatible"


def _warning_from_context_tokens(tokens: int) -> List[str]:
    warnings: List[str] = []
    if int(tokens or 0) <= 0 or int(tokens) == DEFAULT_CONTEXT:
        warnings.append("model_context_tokens_unverified")
    elif int(tokens) < 16000:
        warnings.append("model_context_below_recommended_16k")
    return warnings


def _mask_value(value: Any) -> Any:
    text = str(value or "")
    lowered = text.lower()
    if any(pattern.lower() in lowered for pattern in _SECRET_PATTERNS):
        return "[redacted]"
    if len(text) > 20 and any(ch.isdigit() for ch in text) and any(ch.isalpha() for ch in text):
        return "[redacted]"
    return value


def _sanitize_warning_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    masked = _mask_value(text)
    return str(masked)


def _infer_endpoint_id(url: str, model: str, owner: Optional[str]) -> str:
    for setting_prefix in ("default", "utility", "research", "task"):
        resolved_url, resolved_model, _headers = resolve_endpoint(setting_prefix, owner=owner)
        if resolved_url == url and resolved_model == model:
            endpoint_id = str(get_user_setting(f"{setting_prefix}_endpoint_id", owner or "", "") or "").strip()
            if endpoint_id:
                return endpoint_id
    return ""


def _candidate_from_resolved(
    *,
    url: str,
    model: str,
    headers: Optional[Dict[str, str]],
    owner: Optional[str],
    source: str,
    endpoint_id: str = "",
) -> Optional[_Candidate]:
    if not url or not model:
        return None
    context_tokens = int(get_context_length(url, model) or 0)
    mode = "local" if is_local_endpoint(url) else "cloud"
    warnings = tuple(_warning_from_context_tokens(context_tokens))
    endpoint_id = endpoint_id or _infer_endpoint_id(url, model, owner)
    return _Candidate(
        mode=mode,
        url=url,
        model=model,
        headers=dict(headers or {}),
        endpoint_id=endpoint_id,
        provider=_provider_name(url),
        context_tokens=context_tokens,
        warnings=warnings,
        source=source,
    )


def _configured_fallback_entries(owner: Optional[str]) -> List[Any]:
    raw = get_user_setting("memory.answer_fallback_models", owner or "", []) or []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return [raw]
        return parsed if isinstance(parsed, list) else [parsed]
    if isinstance(raw, list):
        return list(raw)
    return [raw]


def _append_candidate_once(candidates: List[_Candidate], candidate: Optional[_Candidate]) -> None:
    if candidate is None:
        return
    key = (candidate.mode, candidate.url, candidate.model, candidate.endpoint_id)
    for existing in candidates:
        if (existing.mode, existing.url, existing.model, existing.endpoint_id) == key:
            return
    candidates.append(candidate)


def _fallback_candidates(owner: Optional[str]) -> List[_Candidate]:
    candidates: List[_Candidate] = []
    configured = _configured_fallback_entries(owner)
    if configured:
        for entry in configured:
            if isinstance(entry, str):
                alias = entry.strip().lower()
                if alias in {"extractive", ""}:
                    continue
                if alias == "default":
                    for resolved_url, resolved_model, headers in resolve_chat_fallback_candidates(owner=owner):
                        _append_candidate_once(
                            candidates,
                            _candidate_from_resolved(
                                url=resolved_url,
                                model=resolved_model,
                                headers=headers,
                                owner=owner,
                                source="memory.answer_fallback_models",
                            ),
                        )
                    continue
            if isinstance(entry, dict):
                alias = str(entry.get("alias") or "").strip().lower()
                if alias == "extractive":
                    continue
                resolved = resolve_endpoint_by_id(
                    str(entry.get("endpoint_id", "") or "").strip(),
                    str(entry.get("model", "") or "").strip(),
                    owner=owner,
                )
                if resolved:
                    _append_candidate_once(
                        candidates,
                        _candidate_from_resolved(
                            url=resolved[0],
                            model=resolved[1],
                            headers=resolved[2],
                            owner=owner,
                            source="memory.answer_fallback_models",
                            endpoint_id=str(entry.get("endpoint_id", "") or "").strip(),
                        ),
                    )
        return candidates

    for resolved_url, resolved_model, headers in resolve_chat_fallback_candidates(owner=owner):
        _append_candidate_once(
            candidates,
            _candidate_from_resolved(
                url=resolved_url,
                model=resolved_model,
                headers=headers,
                owner=owner,
                source="default_model_fallbacks",
            ),
        )
    return candidates


def _primary_answer_candidate(owner: Optional[str]) -> Optional[_Candidate]:
    configured = str(get_user_setting("memory.answer_model", owner or "", "default") or "default").strip().lower()
    if configured == "extractive":
        return None
    if configured == "default":
        url, model, headers = resolve_endpoint("default", owner=owner)
        return _candidate_from_resolved(
            url=url or "",
            model=model or "",
            headers=headers or {},
            owner=owner,
            source="memory.answer_model",
        )
    if configured in {"local", "cloud"}:
        url, model, headers = resolve_endpoint("default", owner=owner)
        candidate = _candidate_from_resolved(
            url=url or "",
            model=model or "",
            headers=headers or {},
            owner=owner,
            source="memory.answer_model",
        )
        if candidate and candidate.mode == configured:
            return candidate
        return None
    url, model, headers = resolve_endpoint("default", owner=owner)
    return _candidate_from_resolved(
        url=url or "",
        model=model or "",
        headers=headers or {},
        owner=owner,
        source="memory.answer_model",
    )


def _ordered_answer_candidates(owner: Optional[str], requested_mode: str) -> List[_Candidate]:
    candidates: List[_Candidate] = []
    _append_candidate_once(candidates, _primary_answer_candidate(owner))
    for fallback in _fallback_candidates(owner):
        _append_candidate_once(candidates, fallback)
    mode = _normalize_mode(requested_mode)
    if mode == "cloud":
        preferred = [candidate for candidate in candidates if candidate.mode == "cloud"]
        secondary = [candidate for candidate in candidates if candidate.mode == "local"]
        return preferred + secondary
    if mode == "local":
        preferred = [candidate for candidate in candidates if candidate.mode == "local"]
        secondary = [candidate for candidate in candidates if candidate.mode == "cloud"]
        return preferred + secondary
    if mode == "auto":
        preferred = [candidate for candidate in candidates if candidate.mode == "cloud"]
        secondary = [candidate for candidate in candidates if candidate.mode == "local"]
        return preferred + secondary
    return []


def _rl_policy_mode(owner: Optional[str]) -> str:
    mode = str(get_user_setting("model_rl_policy_mode", owner or "", "off") or "off").strip().lower()
    return mode if mode in {"off", "shadow", "active"} else "off"


def _rl_policy_shadow_summary(
    *,
    owner: Optional[str],
    candidates: Sequence[_Candidate],
    citation_required: bool = False,
    local_only_required: bool = False,
) -> Dict[str, Any]:
    if _rl_policy_mode(owner) != "shadow" or not candidates:
        return {}
    routing_candidates = [
        ModelRoutingCandidate.create(
            candidate_id=_candidate_policy_id(candidate),
            provider=candidate.provider,
            model=candidate.model,
            answer_mode=candidate.mode,
        )
        for candidate in candidates
    ]
    decision = recommend_model_routing(
        state=ModelEpisodeState.create(
            surface="memory.answer",
            task_type="evidence_summary",
            owner_label="owner",
            citation_required=citation_required,
            local_only_required=local_only_required,
        ),
        candidates=routing_candidates,
        reward_history=(),
    )
    return decision.audit_summary()


def _candidate_policy_id(candidate: _Candidate) -> str:
    return candidate.endpoint_id or f"{candidate.mode}:{candidate.provider}:{candidate.model}"


def _record_memory_episode_safely(
    *,
    owner: Optional[str],
    candidate: Optional[_Candidate],
    status: str,
    citation_count: int,
    confidence: str,
    fallback_reason: str = "",
    warning_codes: Sequence[str] = (),
    duration_ms: int = 0,
    requested_mode: str = "auto",
) -> None:
    try:
        action = ModelEpisodeAction.create(
            answer_mode=candidate.mode if candidate else "extractive",
            provider=candidate.provider if candidate else "extractive",
            model=candidate.model if candidate else "extractive",
            endpoint_ref=_candidate_policy_id(candidate) if candidate else "extractive",
            prompt_template_id="memory-answer-v1",
            retrieval_depth=citation_count,
            max_tokens=500 if candidate else 0,
        )
        outcome = ModelEpisodeOutcome.create(
            status=status,
            citation_count=citation_count,
            confidence=_confidence_ratio(confidence),
            fallback_reason=fallback_reason,
            warning_codes=tuple(warning_codes),
            duration_ms=duration_ms,
        )
        episode = ModelEpisode.create(
            state=ModelEpisodeState.create(
                surface="memory.answer",
                task_type="evidence_summary",
                owner_label="owner" if owner else "unknown",
                retrieval_doc_count=citation_count,
                citation_required=bool(citation_count),
                local_only_required=_normalize_mode(requested_mode) == "local",
                context_budget_tokens=candidate.context_tokens if candidate else 0,
            ),
            action=action,
            outcome=outcome,
        )
        append_model_episode(score_episode(episode))
    except Exception as exc:  # pragma: no cover - defensive, no answer-path breakage
        logger.warning("Failed to record redacted memory model episode: %s", _sanitize_warning_text(exc))


def _confidence_ratio(confidence: str) -> float:
    return {"high": 0.9, "medium": 0.65, "low": 0.25}.get(str(confidence or "").lower(), 0.0)


def resolve_memory_role_status(owner: Optional[str] = None) -> Dict[str, Any]:
    roles: Dict[str, Any] = {}
    configured_warnings: List[str] = []
    for role, setting_key in MEMORY_ROLE_SETTINGS.items():
        configured = get_user_setting(setting_key, owner or "", "")
        role_payload: Dict[str, Any] = {
            "configured_value": configured,
            "selected_model": "",
            "selected_endpoint_id": "",
            "provider": "",
            "mode": "",
            "model_context_tokens": 0,
            "model_capability_warnings": [],
        }
        if role == "memory.router":
            value = str(configured or "heuristic").strip() or "heuristic"
            role_payload["configured_value"] = value
            role_payload["selected_model"] = value
            role_payload["mode"] = "heuristic" if value == "heuristic" else "local"
            roles[role] = role_payload
            continue
        if role == "memory.answer":
            candidate = _primary_answer_candidate(owner)
        else:
            setting_prefix = "default"
            if role in {"memory.graph_label", "memory.review"}:
                setting_prefix = "utility"
            url, model, headers = resolve_endpoint(setting_prefix, owner=owner)
            candidate = _candidate_from_resolved(
                url=url or "",
                model=model or "",
                headers=headers or {},
                owner=owner,
                source=setting_key,
            )
        if candidate:
            role_payload.update(
                {
                    "selected_model": candidate.model,
                    "selected_endpoint_id": candidate.endpoint_id,
                    "provider": candidate.provider,
                    "mode": candidate.mode,
                    "model_context_tokens": candidate.context_tokens,
                    "model_capability_warnings": list(candidate.warnings),
                }
            )
        else:
            role_payload["model_capability_warnings"] = ["no_model_available"]
            configured_warnings.append(f"{role}:no_model_available")
        roles[role] = role_payload

    fallbacks = _ordered_answer_candidates(owner, "auto")[1:]
    all_answer_candidates = _ordered_answer_candidates(owner, "auto")
    return {
        "roles": roles,
        "answer_fallback_chain": [
            {
                "mode": candidate.mode,
                "selected_model": candidate.model,
                "selected_endpoint_id": candidate.endpoint_id,
                "provider": candidate.provider,
                "model_context_tokens": candidate.context_tokens,
                "model_capability_warnings": list(candidate.warnings),
            }
            for candidate in fallbacks
        ],
        "rl_policy_shadow": _rl_policy_shadow_summary(
            owner=owner,
            candidates=all_answer_candidates,
            citation_required=True,
        ),
        "warnings": configured_warnings,
    }


def _fallback_reason_from_exception(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        if exc.status_code == 429:
            return "provider_rate_limited"
        if exc.status_code in {408, 504}:
            return "provider_timeout"
        if exc.status_code >= 500:
            return "provider_error"
        return "provider_rejected_request"
    text = str(exc or "").lower()
    if "timeout" in text:
        return "provider_timeout"
    return "provider_error"


def _build_synthesis_messages(query: str, citations: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    context_lines = []
    for index, item in enumerate(citations[:6], start=1):
        snippets = list(item.get("snippets") or [])
        snippet_text = "\n".join(f"- {snippet}" for snippet in snippets[:2] if str(snippet or "").strip())
        context_lines.append(
            f"[{index}] {item.get('title') or item.get('path')}\n"
            f"path: {item.get('path')}\n"
            f"score: {item.get('score')}\n"
            f"{snippet_text}".strip()
        )
    context_block = "\n\n".join(line for line in context_lines if line.strip())
    return [
        {
            "role": "system",
            "content": (
                "You answer only from the supplied retrieved citations. "
                "Be concise, grounded, and do not invent facts outside the provided snippets."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Question: {query.strip()}\n\n"
                "Retrieved citations:\n"
                f"{context_block}\n\n"
                "Write a short grounded answer using only this evidence."
            ).strip(),
        },
    ]


async def synthesize_answer(
    *,
    owner: Optional[str],
    query: str,
    citations: List[Dict[str, Any]],
    requested_mode: str,
    confidence: str,
) -> Dict[str, Any]:
    mode = _normalize_mode(requested_mode)
    if mode == "extractive":
        _record_memory_episode_safely(
            owner=owner,
            candidate=None,
            status="fallback",
            citation_count=len(citations),
            confidence=confidence,
            fallback_reason="requested_extractive",
            requested_mode=requested_mode,
        )
        return {
            "answer_mode": "extractive",
            "provider": "",
            "selected_role": "memory.answer",
            "selected_model": "extractive",
            "selected_endpoint_id": "",
            "fallback_reason": "",
            "model_context_tokens": 0,
            "model_capability_warnings": [],
            "warnings": [],
            "answer": "",
        }
    if not citations:
        _record_memory_episode_safely(
            owner=owner,
            candidate=None,
            status="fallback",
            citation_count=0,
            confidence=confidence,
            fallback_reason="no_citations",
            warning_codes=("no_citations",),
            requested_mode=requested_mode,
        )
        return {
            "answer_mode": "extractive",
            "provider": "",
            "selected_role": "memory.answer",
            "selected_model": "extractive",
            "selected_endpoint_id": "",
            "fallback_reason": "no_citations",
            "model_context_tokens": 0,
            "model_capability_warnings": ["no_citations"],
            "warnings": ["no_citations"],
            "answer": "",
        }
    if mode == "auto" and confidence == "low":
        _record_memory_episode_safely(
            owner=owner,
            candidate=None,
            status="fallback",
            citation_count=len(citations),
            confidence=confidence,
            fallback_reason="low_confidence_retrieval",
            warning_codes=("low_confidence_retrieval",),
            requested_mode=requested_mode,
        )
        return {
            "answer_mode": "extractive",
            "provider": "",
            "selected_role": "memory.answer",
            "selected_model": "extractive",
            "selected_endpoint_id": "",
            "fallback_reason": "low_confidence_retrieval",
            "model_context_tokens": 0,
            "model_capability_warnings": ["low_confidence_retrieval"],
            "warnings": ["low_confidence_retrieval"],
            "answer": "",
        }

    candidates = _ordered_answer_candidates(owner, mode)
    rl_policy_shadow = _rl_policy_shadow_summary(
        owner=owner,
        candidates=candidates,
        citation_required=True,
        local_only_required=mode == "local",
    )
    if not candidates:
        _record_memory_episode_safely(
            owner=owner,
            candidate=None,
            status="fallback",
            citation_count=len(citations),
            confidence=confidence,
            fallback_reason="no_model_available",
            warning_codes=("no_model_available",),
            requested_mode=requested_mode,
        )
        return {
            "answer_mode": "extractive",
            "provider": "",
            "selected_role": "memory.answer",
            "selected_model": "extractive",
            "selected_endpoint_id": "",
            "fallback_reason": "no_model_available",
            "model_context_tokens": 0,
            "model_capability_warnings": ["no_model_available"],
            "warnings": ["no_model_available"],
            "rl_policy_shadow": rl_policy_shadow,
            "answer": "",
        }

    messages = _build_synthesis_messages(query, citations)
    last_reason = ""
    warnings: List[str] = []
    for candidate in candidates:
        try:
            answer = await llm_call_async(
                candidate.url,
                candidate.model,
                messages,
                headers=candidate.headers,
                max_tokens=500,
                temperature=0.2,
                timeout=45,
                owner=owner,
                surface="legacy_vault",
                prompt_type="legacy_vault_memory_answer",
            )
            _record_memory_episode_safely(
                owner=owner,
                candidate=candidate,
                status="success",
                citation_count=len(citations),
                confidence=confidence,
                fallback_reason=last_reason,
                warning_codes=tuple(warnings + list(candidate.warnings)),
                requested_mode=requested_mode,
            )
            return {
                "answer_mode": candidate.mode,
                "provider": candidate.provider,
                "selected_role": "memory.answer",
                "selected_model": candidate.model,
                "selected_endpoint_id": candidate.endpoint_id,
                "fallback_reason": last_reason,
                "model_context_tokens": candidate.context_tokens,
                "model_capability_warnings": list(candidate.warnings),
                "warnings": warnings + list(candidate.warnings),
                "rl_policy_shadow": rl_policy_shadow,
                "answer": str(answer or "").strip(),
            }
        except Exception as exc:  # pragma: no cover - exercised via tests
            last_reason = _fallback_reason_from_exception(exc)
            warnings.append(last_reason)
            logger.warning("Memory answer candidate failed: %s", _sanitize_warning_text(exc))

    _record_memory_episode_safely(
        owner=owner,
        candidate=None,
        status="fallback",
        citation_count=len(citations),
        confidence=confidence,
        fallback_reason=last_reason or "all_candidates_failed",
        warning_codes=tuple(warnings or ["all_candidates_failed"]),
        requested_mode=requested_mode,
    )
    return {
        "answer_mode": "extractive",
        "provider": "",
        "selected_role": "memory.answer",
        "selected_model": "extractive",
        "selected_endpoint_id": "",
        "fallback_reason": last_reason or "all_candidates_failed",
        "model_context_tokens": 0,
        "model_capability_warnings": warnings or ["all_candidates_failed"],
        "warnings": warnings or ["all_candidates_failed"],
        "rl_policy_shadow": rl_policy_shadow,
        "answer": "",
    }
