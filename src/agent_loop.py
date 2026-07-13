"""
agent_loop.py

Streaming agent loop for odysseus-ui.
Wraps stream_llm() with multi-round tool execution.
The LLM decides when to use tools by writing fenced code blocks.
"""

import asyncio
import collections
import json
import re
import time
import logging
from pathlib import Path
from typing import AsyncGenerator, List, Dict, Optional, Set

from src.llm_core import stream_llm, stream_llm_with_fallback, _is_ollama_native_url
from src.model_context import estimate_tokens
from src.settings import get_setting
from src.claim_evidence_gate import build_claim_evidence_correction, evaluate_response_claims
from src.tool_transaction_ledger import transactions_from_tool_events
from src.tool_security import (
    PUBLIC_MCP_SERVER_ALLOWLIST,
    blocked_tools_for_owner,
    orchestrator_mode_disabled_tools,
    plan_mode_disabled_tools,
)
from src.tool_policy import GUIDE_ONLY_DIRECTIVE, ToolPolicy
from src.tool_utils import _truncate, get_mcp_manager
from src.agent_tools import (
    strip_tool_blocks,
    execute_tool_block,
    format_tool_result,
    set_active_document,
    set_active_model,
    FUNCTION_TOOL_SCHEMAS,
    TOOL_TAGS,
    ToolBlock,
    MAX_AGENT_ROUNDS,
)
from src.agent_loop_tool_mechanics import (
    _append_tool_results,
    _compute_final_metrics,
    _resolve_tool_blocks,
)
from src.agent_loop_orchestration import (
    ORCHESTRATOR_MODE_DIRECTIVE,
    PLAN_MODE_DIRECTIVE,
    _VERIFIER_EFFECTFUL_TOOLS,
    _VERIFIER_MAX_ROUNDS,
    _build_actions_snapshot,
    _detect_runaway_call,
    _empty_response_fallback,
    _ensure_orchestrator_state_doc,
    _inject_context_provider_messages,
    _orchestrator_actions_snapshot,
    _run_orchestrator_reflector,
    _run_verifier_subagent,
    build_active_plan_note,
)
from src.tool_schemas import get_function_tool_schemas

logger = logging.getLogger(__name__)


def _load_mcp_disabled_map() -> Dict[str, set]:
    """Load per-server disabled tool sets from the database."""
    from core.database import McpServer, SessionLocal
    disabled_map: Dict[str, set] = {}
    db = SessionLocal()
    try:
        for srv in db.query(McpServer).all():
            if srv.disabled_tools:
                try:
                    names = json.loads(srv.disabled_tools)
                    if names:
                        disabled_map[srv.id] = set(names)
                except (json.JSONDecodeError, TypeError):
                    pass
    finally:
        db.close()
    return disabled_map

from src.agent_loop_prompts import (
    AGENT_SYSTEM_PROMPT,
    TOOL_SECTIONS,
    _AGENT_PREAMBLE,
    _AGENT_RULES,
    _API_AGENT_RULES,
    _DOMAIN_RULES,
    _DOMAIN_TOOL_MAP,
    _LINK_RULES,
    _all_tool_sections,
    _assemble_prompt,
    _domain_rules_for_tools,
    _loaded_plugins_prompt,
    _section_text,
    get_builtin_overrides,
)
from src import agent_loop_system_prompt as _agent_loop_system_prompt
from src.agent_loop_system_prompt import _ADMIN_TOOLS

_BUILD_BASE_PROMPT_IMPL = _agent_loop_system_prompt._build_base_prompt
_cached_base_prompt = None
_cached_base_prompt_key = None

from src.agent_loop_intent import (
    _ADMIN_KEYWORDS,
    _ADMIN_SCHEMA_NAMES,
    _API_HOSTS,
    _MCP_KEYWORDS,
    _TOOL_SELECTION_TIMEOUT_SECONDS,
    _assistant_requested_followup,
    _classify_agent_request,
    _detect_admin_intent,
    _endpoint_lookup_keys,
    _extract_last_user_message,
    _is_explicit_continuation,
    _is_ollama_openai_compat_url,
    _recent_context_for_retrieval,
)
def _build_system_prompt(
    messages: List[Dict],
    model: str,
    active_document,
    mcp_mgr,
    disabled_tools: Optional[Set[str]] = None,
    needs_admin: bool = False,
    relevant_tools: Optional[Set[str]] = None,
    mcp_disabled_map: Optional[Dict[str, set]] = None,
    compact: bool = False,
    owner: Optional[str] = None,
    suppress_local_context: bool = False,
    active_email: Optional[Dict[str, str]] = None,
    workflow_required_skill_names: Optional[tuple[str, ...]] = None,
) -> List[Dict]:
    """Compatibility wrapper for the extracted system prompt builder."""
    global _cached_base_prompt, _cached_base_prompt_key
    _agent_loop_system_prompt._cached_base_prompt = _cached_base_prompt
    _agent_loop_system_prompt._cached_base_prompt_key = _cached_base_prompt_key

    _base_hook = globals().get("_build_base_prompt")
    if _base_hook is _BUILD_BASE_PROMPT_WRAPPER:
        _agent_loop_system_prompt._build_base_prompt = _BUILD_BASE_PROMPT_IMPL
    else:
        _agent_loop_system_prompt._build_base_prompt = _base_hook
    _agent_loop_system_prompt.set_active_model = globals().get("set_active_model", set_active_model)
    _agent_loop_system_prompt.set_active_document = globals().get("set_active_document", set_active_document)
    _agent_loop_system_prompt.get_builtin_overrides = globals().get("get_builtin_overrides", get_builtin_overrides)

    try:
        result = _agent_loop_system_prompt._build_system_prompt(
            messages=messages,
            model=model,
            active_document=active_document,
            mcp_mgr=mcp_mgr,
            disabled_tools=disabled_tools,
            needs_admin=needs_admin,
            relevant_tools=relevant_tools,
            mcp_disabled_map=mcp_disabled_map,
            compact=compact,
            owner=owner,
            suppress_local_context=suppress_local_context,
            active_email=active_email,
            workflow_required_skill_names=workflow_required_skill_names,
        )
        _cached_base_prompt = _agent_loop_system_prompt._cached_base_prompt
        _cached_base_prompt_key = _agent_loop_system_prompt._cached_base_prompt_key
        return result
    finally:
        _agent_loop_system_prompt._build_base_prompt = _BUILD_BASE_PROMPT_IMPL
        _agent_loop_system_prompt.set_active_model = set_active_model
        _agent_loop_system_prompt.set_active_document = set_active_document
        _agent_loop_system_prompt.get_builtin_overrides = get_builtin_overrides


def _build_base_prompt(
    disabled_tools,
    mcp_mgr,
    needs_admin,
    relevant_tools=None,
    mcp_disabled_map=None,
    compact: bool = False,
    owner: Optional[str] = None,
    suppress_local_context: bool = False,
):
    """Compatibility wrapper for the extracted base prompt builder."""
    return _BUILD_BASE_PROMPT_IMPL(
        disabled_tools,
        mcp_mgr,
        needs_admin,
        relevant_tools=relevant_tools,
        mcp_disabled_map=mcp_disabled_map,
        compact=compact,
        owner=owner,
        suppress_local_context=suppress_local_context,
    )


_BUILD_BASE_PROMPT_WRAPPER = _build_base_prompt



async def stream_agent_loop(
    endpoint_url: str,
    model: str,
    messages: List[Dict],
    headers: Optional[Dict] = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    prompt_type: Optional[str] = None,
    max_rounds: int = MAX_AGENT_ROUNDS,
    max_tool_calls: int = 0,
    context_length: int = 0,
    active_document=None,
    active_email: Optional[Dict[str, str]] = None,
    session_id: Optional[str] = None,
    disabled_tools: Optional[Set[str]] = None,
    owner: Optional[str] = None,
    relevant_tools: Optional[Set[str]] = None,
    fallbacks: Optional[List[tuple]] = None,
    plan_mode: bool = False,
    orchestrator_mode: bool = False,
    context_was_compacted: bool = False,
    approved_plan: Optional[str] = None,
    tool_policy: Optional[ToolPolicy] = None,
    workspace: Optional[str] = None,
    workflow_skill_resolution: Optional[Dict] = None,
    _is_teacher_run: bool = False,
    audit_surface: Optional[str] = None,
    audit_correlation_id: Optional[str] = None,
    audit_task_id: Optional[str] = None,
    audit_doc_id: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """Streaming agent loop generator.

    Yields SSE events:
      - data: {"delta": "text"}                             (text chunks)
      - data: {"type": "tool_start", "tool": "...", ...}    (before execution)
      - data: {"type": "tool_output", "tool": "...", ...}   (after execution)
      - data: {"type": "agent_step", "round": N}            (next round)
      - data: {"type": "metrics", "data": {...}}            (final metrics)
      - data: [DONE]                                        (end)
    """

    mcp_mgr = get_mcp_manager()
    prep_timings: Dict[str, float] = {}
    disabled_tools = set(disabled_tools or [])
    workflow_skill_resolution = workflow_skill_resolution if isinstance(workflow_skill_resolution, dict) else {}
    workflow_required_skill_names = tuple(
        str(name or "").strip()
        for name in (workflow_skill_resolution.get("required_skill_names") or ())
        if str(name or "").strip()
    )
    workflow_requested_toolsets = {
        str(name or "").strip()
        for name in (workflow_skill_resolution.get("requested_toolsets") or ())
        if str(name or "").strip()
    }
    if tool_policy:
        disabled_tools.update(tool_policy.all_disabled_names())
        if tool_policy.disable_mcp:
            mcp_mgr = None
    guide_only = bool(tool_policy and tool_policy.mode == "guide_only")
    public_blocked_tools = blocked_tools_for_owner(owner)
    public_mcp_allowlist = None
    if public_blocked_tools:
        disabled_tools.update(public_blocked_tools)
        # MCP tools are namespaced dynamically. Public users may use only the
        # built-in vault MCP, which is owner-scoped below at execution time.
        public_mcp_allowlist = PUBLIC_MCP_SERVER_ALLOWLIST

    if plan_mode:
        # Plan mode: investigate read-only, propose a plan, don't execute. The
        # route also unions the read-only-disabled set, but enforce here too so
        # the loop is safe regardless of caller. MCP stays available but is
        # filtered to read-only tools below (after the disabled map is loaded).
        disabled_tools.update(plan_mode_disabled_tools())
    if orchestrator_mode:
        disabled_tools.update(orchestrator_mode_disabled_tools())
        mcp_mgr = None

    _t0 = time.time()
    _needs_admin = _detect_admin_intent(messages)
    _last_user = _extract_last_user_message(messages)
    _interactive_deliverable_decision = None
    try:
        from src.interactive_deliverable_policy import (
            InteractiveDeliverableTarget,
            decide_interactive_deliverable,
        )

        _candidate_deliverable = decide_interactive_deliverable(_last_user)
        if _candidate_deliverable.target != InteractiveDeliverableTarget.NOT_APPLICABLE:
            _interactive_deliverable_decision = _candidate_deliverable
    except (ValueError, TypeError):
        _interactive_deliverable_decision = None
    if orchestrator_mode:
        _ensure_orchestrator_state_doc(owner=owner, session_id=session_id, goal=_last_user)
    _intent = _classify_agent_request(messages, _last_user)
    # Tool retrieval uses the latest message by default. It may inherit recent
    # user turns only for explicit continuations ("yes", "do it", "1").
    _retrieval_query = str(_intent.get("retrieval_query") or _last_user)
    logger.info(
        "[agent-intent] latest_len=%s continuation=%s low_signal=%s domains=%s retrieval_query_len=%s",
        len(_last_user),
        bool(_intent.get("continuation")),
        bool(_intent.get("low_signal")),
        sorted(_intent.get("domains") or []),
        len(_retrieval_query),
    )
    _mcp_disabled_map = _load_mcp_disabled_map() if mcp_mgr else {}
    if mcp_mgr and public_mcp_allowlist is not None:
        _mcp_tools_by_server: Dict[str, set] = {}
        for _tool in mcp_mgr.get_all_tools({}):
            _mcp_tools_by_server.setdefault(_tool["server_id"], set()).add(_tool["name"])
        for _sid, _names in _mcp_tools_by_server.items():
            if _sid not in public_mcp_allowlist:
                _mcp_disabled_map.setdefault(_sid, set()).update(_names)
    if plan_mode and mcp_mgr:
        # Allow read-only MCP tools to investigate, block write/unknown ones:
        # hide them from the schemas AND reject them at runtime by qualified name.
        _mcp_block_map, _mcp_block_q = mcp_mgr.plan_mode_blocked_mcp()
        for _sid, _names in _mcp_block_map.items():
            _mcp_disabled_map.setdefault(_sid, set()).update(_names)
        disabled_tools.update(_mcp_block_q)
    prep_timings["request_setup"] = time.time() - _t0

    # RAG-based tool selection: retrieve relevant tools for this query.
    # If caller provided a pre-computed set (e.g. task_scheduler), use that.
    _relevant_tools = set() if guide_only else relevant_tools
    _t1 = time.time()
    if _relevant_tools:
        logger.info(f"[tool-rag] Using caller-provided relevant_tools ({len(_relevant_tools)} tools)")
    if not guide_only and not _relevant_tools and bool(_intent.get("low_signal")):
        from src.tool_index import ALWAYS_AVAILABLE
        if workspace:
            # An active workspace IS the file-work signal: a vague "look at the
            # project" means explore this folder. Surface only the READ-ONLY file
            # tools (intersection with the plan-mode read-only allowlist) so the
            # agent can investigate; write/shell tools stay out until the request
            # actually calls for them (RAG retrieval adds those on a real ask).
            _relevant_tools = set(ALWAYS_AVAILABLE)
            from src.tool_security import PLAN_MODE_READONLY_TOOLS
            _relevant_tools |= (_DOMAIN_TOOL_MAP["files"] & PLAN_MODE_READONLY_TOOLS)
            logger.info("[tool-rag] Low-signal but workspace active; including read-only file tools")
        else:
            # Don't short-circuit: fall through to RAG retrieval below.
            # Non-English queries are flagged low_signal by the English-only
            # intent classifier, but fastembed retrieval works across languages.
            logger.info("[tool-rag] Low-signal query; will run RAG retrieval")
    if not guide_only and not _relevant_tools:
        try:
            from src.tool_index import get_tool_index, ALWAYS_AVAILABLE
            tool_idx = get_tool_index()
            if tool_idx:
                if mcp_mgr:
                    try:
                        await asyncio.wait_for(
                            asyncio.to_thread(tool_idx.index_mcp_tools, mcp_mgr, _mcp_disabled_map),
                            timeout=_TOOL_SELECTION_TIMEOUT_SECONDS,
                        )
                    except asyncio.TimeoutError:
                        logger.warning(
                            "[tool-rag] MCP tool indexing exceeded %.1fs; continuing without reindex",
                            _TOOL_SELECTION_TIMEOUT_SECONDS,
                        )
                if _retrieval_query:
                    try:
                        _relevant_tools = await asyncio.wait_for(
                            asyncio.to_thread(tool_idx.get_tools_for_query, _retrieval_query, 8),
                            timeout=_TOOL_SELECTION_TIMEOUT_SECONDS,
                        )
                        logger.info(f"[tool-rag] Retrieved tools for query: {sorted(_relevant_tools - ALWAYS_AVAILABLE)}")
                    except asyncio.TimeoutError:
                        logger.warning(
                            "[tool-rag] Retrieval exceeded %.1fs; falling back to always-available tools",
                            _TOOL_SELECTION_TIMEOUT_SECONDS,
                        )
                        _relevant_tools = set(ALWAYS_AVAILABLE)
        except Exception as e:
            logger.warning(f"[tool-rag] Retrieval failed, using keyword fallback: {e}")
            _relevant_tools = None

    # Fallback: if RAG unavailable, use keyword-based tool selection
    # instead of sending ALL tools (which overwhelms the model).
    if not guide_only and not _relevant_tools and _retrieval_query:
        from src.tool_index import ALWAYS_AVAILABLE, ToolIndex
        _relevant_tools = set(ALWAYS_AVAILABLE)
        ql = _retrieval_query.lower()
        for keywords, tools in ToolIndex._KEYWORD_HINTS.items():
            if any(kw in ql for kw in keywords):
                _relevant_tools.update(tools)
        logger.info(f"[tool-rag] Keyword fallback selected: {sorted(_relevant_tools - ALWAYS_AVAILABLE)}")

    # If deterministic domain detection fired, seed the corresponding domain
    # tools into the selected tool set. This is not direct prompt-pack
    # injection: `_assemble_prompt()` still derives domain rules from the final
    # tool names. It prevents obvious requests like "last 5 emails" from
    # collapsing to only ask_user/manage_memory when vector retrieval misses or
    # times out.
    if not guide_only and _relevant_tools is not None:
        for _domain in (_intent.get("domains") or set()):
            _relevant_tools.update(_DOMAIN_TOOL_MAP.get(str(_domain), set()))
        if "cookbook" in (_intent.get("domains") or set()):
            _relevant_tools.update({
                "list_served_models",
                "list_downloads",
                "list_cached_models",
                "list_cookbook_servers",
                "list_serve_presets",
            })
        if "email" in (_intent.get("domains") or set()):
            _relevant_tools.add("ui_control")
        if "web" in (_intent.get("domains") or set()):
            _relevant_tools.update({"web_search", "web_fetch"})
        if "ui" in (_intent.get("domains") or set()):
            _relevant_tools.add("ui_control")
        if _interactive_deliverable_decision is not None:
            _target = _interactive_deliverable_decision.target.value
            if _target in ("native_download", "dual"):
                _relevant_tools.update({
                    "write_file",
                    "verify_pygame_headless",
                    "publish_artifact",
                })
            if _target in ("browser_preview", "dual"):
                _relevant_tools.add("create_document")

    # If a document is open the model needs the editing tools available
    # regardless of which selection path (RAG, keyword, caller-provided) ran
    # or what keywords were in the latest user message.
    if _relevant_tools is not None and active_document is not None:
        _relevant_tools.update({"edit_document", "update_document", "suggest_document"})

    # The skill index injected by _build_system_prompt tells the model to
    # call `manage_skills action=view`, and Jaccard-matched skills are pasted
    # into the prompt as procedures to follow — but neither path goes through
    # tool selection, so the model can be handed a procedure naming tools
    # (grep, read_file, ...) that aren't in its schema list. Keep the schemas
    # in lockstep: manage_skills is callable whenever any skill is indexed,
    # and a matched skill's declared requires_toolsets ride along with it.
    if not guide_only and _relevant_tools is not None:
        try:
            from services.memory.skills import SkillsManager
            from src.constants import DATA_DIR
            _skills_on = True
            try:
                from routes.prefs_routes import _load_for_user as _load_prefs
                _skills_on = (_load_prefs(owner) or {}).get("skills_enabled", True)
            except Exception:
                pass
            _sm = SkillsManager(DATA_DIR)
            _owner_skills = _sm.load(owner=owner) if _skills_on else []
            if _owner_skills:
                _relevant_tools.add("manage_skills")
                from src.tool_policy import known_tool_names
                _known = known_tool_names()
                _relevant_tools.update(t for t in workflow_requested_toolsets if t in _known)
                if _retrieval_query:
                    # Validate against every known executable tool, not just
                    # TOOL_SECTIONS — code-nav tools (grep/glob/ls) ship as
                    # schemas without a prompt-prose section.
                    for _sk in _sm.get_relevant_skills(
                        _retrieval_query, skills=_owner_skills,
                        threshold=0.25, max_items=3,
                    ):
                        _relevant_tools.update(
                            t for t in (_sk.get("requires_toolsets") or [])
                            if t in _known
                        )
        except Exception as _e:
            logger.debug(f"[tool-rag] skill-aware tool include skipped: {_e}")

    if _relevant_tools is not None:
        logger.info("[agent-intent] selected_tools=%s", sorted(_relevant_tools)[:50])

    prep_timings["tool_selection"] = time.time() - _t1

    _t2 = time.time()
    # Hosted-API match by URL, OR the model name looks like a recent model
    # known to follow OpenAI-style function calling (DeepSeek, GPT*, Claude,
    # Gemini, Qwen3+, Mixtral, Llama 3.1+). Caught the DeepSeek-via-local-
    # vLLM case where endpoint_url doesn't include a vendor host.
    _model_lc = (model or "").lower()
    # Step 1: per-endpoint override (set at registration time from the
    # serve command — `--enable-auto-tool-choice` flips it on. UI can
    # also toggle per endpoint). NULL = unknown; for local Ollama /v1 we
    # default to fenced tools, otherwise fall through to keyword + host checks.
    _endpoint_supports: Optional[bool] = None
    try:
        from core.database import SessionLocal as _SL, ModelEndpoint as _ME
        _db = _SL()
        try:
            _ep = None
            for _key in _endpoint_lookup_keys(endpoint_url):
                _ep = _db.query(_ME).filter(_ME.base_url == _key).first()
                if _ep is not None:
                    break
            if _ep is not None:
                _endpoint_supports = _ep.supports_tools
        finally:
            _db.close()
    except Exception as _e:
        logger.debug(f"endpoint supports_tools lookup failed: {_e}")
    _model_supports_tools = any(kw in _model_lc for kw in (
        "gpt-4", "gpt-5", "gpt-o", "claude", "gemini", "gemma",
        "qwen3", "qwen2.5", "mixtral", "mistral", "llama-3.1", "llama-3.2",
        "llama-3.3", "llama-4",
        # Local-served models that follow OpenAI-style function calling
        # via vLLM's `--enable-auto-tool-choice`. Belt-and-suspenders
        # with the per-endpoint flag above.
        "minimax", "kimi", "yi-", "phi-3", "phi-4", "command-r",
        "glm-4", "internlm", "hermes",
        # deepseek-v2/v3/chat support tools via the cloud API; deepseek-r1
        # (reasoning model) does not — handled by the blocklist below.
        "deepseek-v", "deepseek-chat",
    ))
    # Models known to reject tool schemas at the Ollama/local level even when
    # the endpoint URL would otherwise enable native function calling.
    # The per-endpoint supports_tools flag (True/False) always takes priority
    # and can override this list for users who know their setup.
    _model_no_tools = any(kw in _model_lc for kw in (
        "deepseek-r1",
        # Open-weight GPT-OSS models are commonly served through llama.cpp /
        # llama-cpp-python. Their names contain "gpt-o", but they do not use
        # OpenAI's native tool-call channel unless the endpoint opts in.
        "gpt-oss",
    ))
    # Native Ollama endpoints (/api/chat) handle tool schemas differently from
    # the OpenAI-compat path. Models like gemma4, qwen3.5, ministral respond to
    # tool schemas by emitting a single native tool_call token then stopping,
    # rather than writing a fenced block — the agent loop sees 1 token and no
    # recognised tool, so the round terminates immediately (issue #1567).
    # Unless the endpoint is explicitly marked supports_tools=True by the user
    # (via the endpoint settings toggle), treat Ollama-native as text-only so
    # the fenced-block path is used instead of native function calling.
    _is_ollama_native = _is_ollama_native_url(endpoint_url or "")
    _ollama_openai_compat = _is_ollama_openai_compat_url(endpoint_url or "")
    if _endpoint_supports is True:
        _is_api_model = True
    elif (
        _endpoint_supports is False
        or _model_no_tools
        or _is_ollama_native
        or _ollama_openai_compat
    ):
        _is_api_model = False
    else:
        _is_api_model = any(h in endpoint_url for h in _API_HOSTS) or _model_supports_tools
    messages, mcp_schemas = _build_system_prompt(
        messages, model, active_document, mcp_mgr, disabled_tools,
        needs_admin=_needs_admin, relevant_tools=_relevant_tools,
        mcp_disabled_map=_mcp_disabled_map,
        compact=_is_api_model,
        owner=owner,
        suppress_local_context=guide_only,
        active_email=active_email,
        workflow_required_skill_names=workflow_required_skill_names,
    )
    messages = _inject_context_provider_messages(
        messages,
        owner=owner,
        query=_retrieval_query or _last_user or "",
        context_length=context_length,
        enabled=not guide_only,
    )
    if _interactive_deliverable_decision is not None and not guide_only:
        _target = _interactive_deliverable_decision.target.value
        _deliverable_note = (
            "## INTERACTIVE DELIVERABLE POLICY — RUNTIME ENFORCED\n"
            f"Resolved target: {_target}. Follow this exact deliverable contract.\n"
            "native_download: create a workspace .py with write_file, call "
            "verify_pygame_headless, then call publish_artifact for the .py. "
            "Publish the captured PNG with inspect_image=true before any visual claim.\n"
            "browser_preview: create one self-contained HTML/canvas document with inline "
            "CSS/JS for the editor Run/Preview surface.\n"
            "dual: complete both paths. A dummy-SDL run is headless_tested, never "
            "interactive_preview_ready. A filesystem path is not a download link; only "
            "publish_artifact proves download_ready. Never say visually inspected unless "
            "artifact_evidence.visual_inspected.status is verified."
        )
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = _deliverable_note + "\n\n" + (messages[0].get("content") or "")
        else:
            messages.insert(0, {"role": "system", "content": _deliverable_note})
    if workspace and not guide_only:
        # PREPEND (not append) so it dominates the large base prompt — appended
        # at the end, small models ignored it and asked the user for code. The
        # folder IS the project; the agent must explore it, not ask.
        _ws_note = (
            f"## ACTIVE WORKSPACE — READ FIRST\n"
            f"The user is working in this folder: {workspace}\n"
            f"It IS the project. bash/python run with cwd set here and "
            f"read_file/write_file are confined to it (paths outside are rejected).\n"
            f"When the user says \"the code\" / \"this project\" / \"the workspace\" "
            f"or asks to review/find/edit something WITHOUT a path, they mean THIS "
            f"folder. Do NOT ask the user for code or a path, and do NOT read a file "
            f"literally named \"workspace\". ALWAYS start by exploring it yourself: "
            f"run `bash` → `git ls-files` (or `ls -R`) to see the files, then "
            f"read_file the relevant ones by path RELATIVE to the workspace."
        )
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = _ws_note + "\n\n" + (messages[0].get("content") or "")
        else:
            messages.insert(0, {"role": "system", "content": _ws_note})
        logger.info("[workspace] active for this turn: %s", workspace)
    if plan_mode and not guide_only:
        # Steer the model to investigate-then-propose. Hard tool gating handles
        # every write path except shell; this directive is what keeps the
        # intentionally-allowed bash/python read-only, so it must DOMINATE. Put
        # it at the very TOP of the system prompt (the base prompt is large and
        # action-oriented — appending buried it, and small models ignored it).
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = PLAN_MODE_DIRECTIVE + "\n\n" + (messages[0].get("content") or "")
        else:
            messages.insert(0, {"role": "system", "content": PLAN_MODE_DIRECTIVE})
    elif orchestrator_mode and not guide_only:
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = ORCHESTRATOR_MODE_DIRECTIVE + "\n\n" + (messages[0].get("content") or "")
        else:
            messages.insert(0, {"role": "system", "content": ORCHESTRATOR_MODE_DIRECTIVE})
    elif approved_plan and approved_plan.strip() and not guide_only:
        # EXECUTING an approved plan. Pin the checklist as a top-of-context
        # system note so a long plan on a weak model survives history
        # truncation — the agent can always re-read the plan instead of losing
        # the thread. (The first system message is kept by the context trimmer.)
        _plan_note = build_active_plan_note(approved_plan)
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = _plan_note + "\n\n" + (messages[0].get("content") or "")
        else:
            messages.insert(0, {"role": "system", "content": _plan_note})
        logger.info("[plan] pinned approved plan (%d chars) for execution turn", len(approved_plan))
    if guide_only:
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = GUIDE_ONLY_DIRECTIVE + "\n\n" + (messages[0].get("content") or "")
        else:
            messages.insert(0, {"role": "system", "content": GUIDE_ONLY_DIRECTIVE})
    prep_timings["prompt_build"] = time.time() - _t2

    _t3 = time.time()
    try:
        from src.context_compactor import latest_dialog_pair_preserved, trim_for_context
        from src.context_budget import DEFAULT_HARD_MAX, DEFAULT_BUDGET, budget_is_explicit as _budget_is_explicit, resolve_input_token_budget
        from src.model_context import budget_context_for_model

        soft_budget = int(get_setting("agent_input_token_budget", DEFAULT_BUDGET) or 0)
        if soft_budget > 0:
            before_trim_tokens = estimate_tokens(messages)
            reserve_tokens = min(max(max_tokens or 1024, 512), 2048)
            # Ceiling for the auto-derived budget (no effect on an explicit budget;
            # see #1230). Falls back to DEFAULT_HARD_MAX on missing/malformed values
            # so misconfig can't zero the budget.
            try:
                hard_max = int(get_setting("agent_input_token_hard_max", DEFAULT_HARD_MAX) or DEFAULT_HARD_MAX)
            except (TypeError, ValueError):
                hard_max = DEFAULT_HARD_MAX
            if hard_max <= 0:
                hard_max = DEFAULT_HARD_MAX
            # Default value = auto sentinel (scale to the window); any other value =
            # explicit cap. Value-based, not presence-based, because the save path
            # materializes defaults so a persisted default must still read as auto (#4121).
            budget_is_explicit = _budget_is_explicit(soft_budget)
            # Scale only off a window we actually discovered, bound to the value it
            # proves (else 0) — not the passed-in context_length, which can be stale
            # or unset for some callers (#4122 review).
            ctx_for_budget = budget_context_for_model(endpoint_url, model, fallback=context_length)
            budget_decision = resolve_input_token_budget(
                soft_budget,
                ctx_for_budget,
                budget_is_explicit,
                hard_max=hard_max,
                endpoint_url=endpoint_url,
                model=model,
                overrides=get_setting(
                    "agent_input_token_budget_overrides",
                    {"providers": {}, "models": {}},
                ),
                output_reserve=reserve_tokens,
            )
            trimmed_messages = trim_for_context(
                messages,
                budget_decision.input_budget,
                # The output reserve was already applied once against the real
                # model window while resolving input_budget.
                reserve_tokens=0,
            )
            after_trim_tokens = estimate_tokens(trimmed_messages)
            latest_pair_preserved = latest_dialog_pair_preserved(messages, trimmed_messages)
            logger.info(
                "[agent-context-budget] model=%s provider=%s source=%s input_budget=%s "
                "output_reserve=%s before=%s after=%s latest_pair_preserved=%s",
                model,
                budget_decision.provider,
                budget_decision.source,
                budget_decision.input_budget,
                budget_decision.output_reserve,
                before_trim_tokens,
                after_trim_tokens,
                latest_pair_preserved,
            )
            if after_trim_tokens < before_trim_tokens:
                logger.info(
                    "[agent] soft-trimmed context: %s -> %s tokens (budget=%s, reserve=%s)",
                    before_trim_tokens,
                    after_trim_tokens,
                    budget_decision.input_budget,
                    0,
                )
            messages = trimmed_messages
    except Exception as e:
        logger.warning("[agent] Soft context trim skipped: %s", e)
    prep_timings["context_trim"] = time.time() - _t3

    # Strip internal metadata keys before sending to the LLM API
    messages = [{k: v for k, v in msg.items() if k != "_protected"} for msg in messages]

    yield f"data: {json.dumps({'type': 'agent_prep', 'data': {k: round(v, 3) for k, v in prep_timings.items()}})}\n\n"

    full_response = ""
    total_start = time.time()
    time_to_first_token = None
    first_token_received = False
    tool_events = []   # Persist tool executions for history reload
    assistant_attachments = []  # Public, owner-verified generated downloads
    _assistant_attachment_ids = set()
    round_texts = []   # Cleaned text per round for history reload
    # Completion-verifier state (mechanism 3a). _effectful_used flips on when
    # a tool that produces a checkable artifact runs; the verifier only fires
    # on such turns and at most _VERIFIER_MAX_ROUNDS times.
    _effectful_used = False
    _verifier_rounds = 0
    _verifier_instruction = _extract_last_user_message(messages)
    real_input_tokens = 0   # Accumulated real usage from API
    real_output_tokens = 0
    last_round_input_tokens = 0  # Last round's input tokens (for context % peak)
    has_real_usage = False
    backend_gen_tps = 0      # backend-reported true gen speed (llama.cpp timings)
    backend_prefill_tps = 0  # backend-reported prefill speed
    requested_model = model
    actual_model = model
    total_tool_calls = 0  # for budget enforcement
    if orchestrator_mode and context_was_compacted and not _is_teacher_run:
        await _run_orchestrator_reflector(
            owner=owner,
            session_id=session_id,
            user_request=_last_user,
            tool_events=tool_events,
            trigger="context_compacted",
            round_num=0,
        )

    # Loop-breaker state. Small models (e.g. deepseek-v4-flash) can get
    # stuck firing the same tool call over and over with no text — burns
    # all 20 rounds, looks like the chat "died". Track recent call
    # signatures + consecutive no-text tool rounds to bail early.
    _recent_call_sigs = collections.deque(maxlen=6)
    _stuck_rounds = 0
    # Frequency of each exact call signature (tool + args), for the runaway
    # backstop. Counting identical repeats — not distinct same-tool calls —
    # lets a legit batch (e.g. 18 calendar events at once) through.
    _call_freq: collections.Counter = collections.Counter()
    _THINK_RE = re.compile(r'<think>.*?</think>', re.DOTALL | re.IGNORECASE)
    _force_answer = False  # set by loop-breaker → next round runs with NO tools
    # Supervisor: how many times we've nudged the model after it announced
    # an action without emitting the tool call. Capped to prevent a model
    # that *can't* call the tool from looping forever.
    _intent_nudge_count = 0
    _MAX_INTENT_NUDGES = 2

    # "I said I would, then didn't" detector. The pattern that breaks debug
    # loops on weak models (deepseek-v4-flash mid-2026): the model writes
    # "Let me tail the output to see the error" and then ends the turn with
    # no tool_calls. The intent is sincere but the function call gets dropped.
    # Match the common phrasings + an action verb that maps to an available
    # tool, so we don't nudge on harmless transitional text like "let me
    # know what you think".
    _INTENT_RE = re.compile(
        r"(?:^|\n)\s*(?:let me|i'?ll|i will|i need to|we need to|need to|"
        r"i should|we should|i must|we must|going to|let's)\s+"
        r"(?:tail|check|investigate|look at|see|tail|read|fetch|inspect|"
        r"verify|diagnose|examine|debug|capture|grab|pull|view|run|call|"
        r"trigger|launch|start|kick off|stop|kill|restart|adopt|serve|"
        r"register|adopt|list|search|find|query|hit|ping|test|use|perform|do)"
        r"\b[^.\n]{0,140}",
        re.IGNORECASE,
    )
    _awaiting_user = False  # set by ask_user → end the turn and wait for a choice

    # Document streaming state (persists across rounds)
    _doc_acc = ""          # accumulated tool-call JSON arguments
    _doc_opened = False    # whether doc_stream_open was sent
    _doc_last_len = 0      # last content length sent

    # Set when the loop runs out of rounds while the agent was still actively
    # using tools — i.e. it was cut off, not finished. Drives a "Continue" event
    # so the user can resume instead of the turn silently stalling.
    _exhausted_rounds = False

    for round_num in range(1, max_rounds + 1):
        round_response = ""
        round_reasoning = ""  # reasoning_content deltas (DeepSeek-thinking, vLLM --reasoning-parser)
        native_tool_calls = []  # populated if model uses function calling
        # Reset doc streaming state per round
        _doc_acc = ""
        _doc_opened = False
        _doc_last_len = 0
        _doc_fence_offset = 0  # offset into round_response for text-fence content
        # Cursor for the multi-block scanner — when a `create_document`
        # fenced block closes we advance this so the next iteration can
        # detect a SUBSEQUENT block in the same round.
        _doc_scan_from = 0

        # Merge native tool schemas with MCP tool schemas, filtering out
        # Only send function schemas for API models (OpenAI, Anthropic, etc.).
        # Local models use fenced code blocks or <tool_code> — schemas add overhead.
        if _force_answer:
            # Loop-breaker decided the model has enough info but keeps
            # calling tools. Send NO tools this round so it's forced to
            # write the answer instead of flailing further.
            all_tool_schemas = []
        elif _is_api_model:
            function_tool_schemas = get_function_tool_schemas()
            # Filter schemas by RAG-selected tools (if available)
            if _relevant_tools:
                # _build_base_prompt unions _ADMIN_TOOLS into the prompt
                # sections when admin intent fires — the schema list must
                # offer the same names, or the model reads prose describing
                # tools it cannot call and substitutes the nearest schema
                # it does have (e.g. manage_memory for manage_skills).
                _schema_names = set(_relevant_tools)
                if _needs_admin:
                    _schema_names |= _ADMIN_TOOLS
                base_schemas = [
                    s for s in function_tool_schemas
                    if s.get("function", {}).get("name") in _schema_names
                ]
                _mcp_filtered = [
                    s for s in mcp_schemas
                    if s.get("function", {}).get("name") in _relevant_tools
                ]
                all_tool_schemas = base_schemas + _mcp_filtered
            else:
                base_schemas = function_tool_schemas if _needs_admin else [
                    s for s in function_tool_schemas
                    if s.get("function", {}).get("name") not in _ADMIN_SCHEMA_NAMES
                ]
                all_tool_schemas = base_schemas + mcp_schemas
            if disabled_tools:
                all_tool_schemas = [
                    t for t in all_tool_schemas
                    if t.get("function", {}).get("name") not in disabled_tools
                    and t.get("name") not in disabled_tools
                ]
        else:
            # Local: only MCP schemas when message suggests MCP tool usage
            _last_content = _last_user.lower()
            _wants_mcp = any(kw in _last_content for kw in _MCP_KEYWORDS)
            all_tool_schemas = mcp_schemas if (_wants_mcp and mcp_schemas) else []
        agent_stream_timeout = int(get_setting("agent_stream_timeout_seconds", 300) or 300)

        _tool_names_sent = [t.get("function", {}).get("name") for t in (all_tool_schemas or []) if t.get("function")]
        logger.info(f"[agent-debug] round={round_num} model={model} _is_api_model={_is_api_model} tools_sent={len(_tool_names_sent)} tool_names={_tool_names_sent[:15]} relevant_tools={sorted(_relevant_tools)[:15] if _relevant_tools else 'ALL'}")

        # Primary target + any configured fallback models. stream_llm_with_fallback
        # only switches on a pre-content failure, so streamed output is never
        # duplicated; the dead-host cooldown keeps repeat primary attempts cheap.
        _candidates = [(endpoint_url, model, headers)] + list(fallbacks or [])
        # stream_llm enforces a per-read INACTIVITY timeout (httpx read=timeout),
        # which kills a wedged/silent endpoint. This wall-clock deadline is the
        # complementary cap for the rare stream that trickles bytes forever and
        # so never trips the inactivity timeout. Generous — only catches runaway.
        _round_deadline = time.time() + max(agent_stream_timeout * 4, 1200)
        async for chunk in stream_llm_with_fallback(
            _candidates,
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            prompt_type=prompt_type if round_num == 1 else None,
            tools=all_tool_schemas if all_tool_schemas else None,
            timeout=agent_stream_timeout,
            session_id=session_id,
            owner=owner,
            surface=audit_surface or "agent",
            correlation_id=audit_correlation_id,
            task_id=audit_task_id,
            doc_id=audit_doc_id,
        ):
            if time.time() > _round_deadline:
                logger.warning(f"[agent] round {round_num} stream exceeded wall-clock deadline; cutting off")
                break
            # Forward error events from stream_llm to the frontend
            if chunk.startswith("event: error"):
                yield chunk
                continue
            if chunk.startswith("data: ") and not chunk.startswith("data: [DONE]"):
                try:
                    data = json.loads(chunk[6:])
                    # IMPORTANT: check type-based events BEFORE "delta" key,
                    # because tool_call_delta also has an "arg_delta" field.
                    if data.get("type") == "tool_call_delta":
                        if tool_policy and tool_policy.blocks(data.get("name")):
                            continue
                        # Stream document content to frontend as AI generates it
                        logger.debug(f"tool_call_delta: name={data.get('name')}, len(arg_delta)={len(data.get('arg_delta', ''))}")
                        _doc_acc += data.get("arg_delta", "")
                        if not _doc_opened:
                            tm = re.search(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"', _doc_acc)
                            if tm:
                                _doc_opened = True
                                try:
                                    title = json.loads('"' + tm.group(1) + '"')
                                except Exception:
                                    title = tm.group(1)
                                lm = re.search(r'"language"\s*:\s*"((?:[^"\\]|\\.)*)"', _doc_acc)
                                lang = ""
                                if lm:
                                    try:
                                        lang = json.loads('"' + lm.group(1) + '"')
                                    except Exception:
                                        lang = lm.group(1)
                                logger.info(f"Doc streaming: open title={title!r} lang={lang!r}")
                                yield f'data: {json.dumps({"type": "doc_stream_open", "title": title, "language": lang})}\n\n'
                        if _doc_opened:
                            cm = re.search(r'"content"\s*:\s*"', _doc_acc)
                            if cm:
                                raw = _doc_acc[cm.end():]
                                raw = re.sub(r'"\s*\}\s*$', '', raw)
                                try:
                                    decoded = json.loads('"' + raw + '"')
                                except Exception:
                                    try:
                                        decoded = json.loads('"' + raw.rstrip('\\') + '"')
                                    except Exception:
                                        decoded = raw.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"').replace('\\\\', '\\')
                                if len(decoded) > _doc_last_len:
                                    _doc_last_len = len(decoded)
                                    yield f'data: {json.dumps({"type": "doc_stream_delta", "content": decoded})}\n\n'
                    elif data.get("type") == "tool_calls":
                        native_tool_calls = data.get("calls", [])
                        logger.info(f"Agent round {round_num}: received {len(native_tool_calls)} native tool call(s)")
                    elif data.get("type") == "usage":
                        u = data.get("data", {})
                        actual_model = u.get("model") or actual_model
                        round_input = u.get("input_tokens", 0)
                        real_input_tokens += round_input
                        real_output_tokens += u.get("output_tokens", 0)
                        last_round_input_tokens = round_input
                        has_real_usage = True
                        # Backend-reported TRUE generation speed (llama.cpp
                        # timings.predicted_per_second) — pure decode, excludes
                        # prefill/network. Preferred over tokens/wall-clock, which
                        # reads low. Keep the last round's value (the gen phase).
                        if u.get("gen_tps"):
                            backend_gen_tps = u["gen_tps"]
                        if u.get("prefill_tps"):
                            backend_prefill_tps = u["prefill_tps"]
                    elif data.get("type") == "fallback":
                        # The selected model failed and another answered; surface
                        # the notice so a misconfigured provider isn't masked.
                        actual_model = data.get("answered_by") or actual_model
                        logger.warning(f"[agent] round {round_num} fell back: "
                                       f"{data.get('selected_model')} -> {data.get('answered_by')}")
                        yield chunk
                    elif data.get("type") == "model_actual":
                        actual_model = data.get("model") or actual_model
                        data["requested_model"] = requested_model
                        yield f"data: {json.dumps(data)}\n\n"
                    elif "delta" in data:
                        if not first_token_received:
                            time_to_first_token = time.time() - total_start
                            first_token_received = True
                        # Keep reasoning deltas in a separate accumulator so
                        # we can echo them back via `reasoning_content` on the
                        # next request (DeepSeek requires this; harmless for
                        # other vendors). Regular content still flows into
                        # round_response unchanged.
                        if data.get("thinking"):
                            round_reasoning += data["delta"]
                        else:
                            round_response += data["delta"]
                            full_response += data["delta"]
                        yield chunk  # Stream all rounds
                        # Detect text-fence doc streaming for rounds 2+
                        # (round 1 is handled by frontend fence detection + server fenced block path)
                        if (
                            round_num > 1
                            and not _doc_acc
                            and not (tool_policy and tool_policy.blocks("create_document"))
                        ):
                            _fence_marker = '```create_document\n'
                            # Open a new block if we're not currently inside one
                            # and there's an unstreamed marker in the response.
                            # The marker search starts at the byte after the
                            # last block's closing fence so the SECOND
                            # `create_document` block in the same round gets
                            # detected (previously only the first one was
                            # streamed and the rest were silently dropped).
                            if not _doc_opened and _fence_marker in round_response[_doc_scan_from:]:
                                _fi = round_response.index(_fence_marker, _doc_scan_from)
                                _fa = round_response[_fi + len(_fence_marker):]
                                _fl = _fa.split('\n')
                                if _fl and _fl[0].strip():
                                    _doc_opened = True
                                    _ft = _fl[0].strip()
                                    _kl = {'python','py','javascript','js','typescript','ts','html','css','json','yaml','bash','sql','rust','go','java','c','cpp','markdown','text'}
                                    _flang = _fl[1].strip() if len(_fl) > 1 and _fl[1].strip().lower() in _kl else ''
                                    _doc_fence_offset = _fi + len(_fence_marker) + len(_fl[0]) + 1
                                    if _flang:
                                        _doc_fence_offset += len(_fl[1]) + 1
                                    _doc_last_len = 0
                                    yield f'data: {json.dumps({"type": "doc_stream_open", "title": _ft, "language": _flang})}\n\n'
                            if _doc_opened:
                                _rc = round_response[_doc_fence_offset:]
                                _ci = _rc.find('\n```')
                                if _ci >= 0:
                                    _rc = _rc[:_ci]
                                if len(_rc) > _doc_last_len:
                                    _doc_last_len = len(_rc)
                                    yield f'data: {json.dumps({"type": "doc_stream_delta", "content": _rc})}\n\n'
                                # If the closing fence has arrived, finalise
                                # this block and arm detection of the NEXT
                                # one. The model can emit multiple
                                # `create_document` blocks in a single round.
                                if _ci >= 0:
                                    _doc_opened = False
                                    _doc_scan_from = _doc_fence_offset + _ci + len('\n```')
                                    _doc_fence_offset = 0
                                    _doc_last_len = 0
                    elif data.get("error"):
                        err_msg = data.get("error", "unknown")
                        logger.error(f"Agent round {round_num}: stream error: {err_msg}")
                        yield f'data: {json.dumps({"delta": chr(10) + chr(10) + "*[Stream error: " + str(err_msg) + "]*"})}\n\n'
                except json.JSONDecodeError:
                    if round_num == 1:
                        yield chunk
            elif chunk.startswith("event: "):
                # Forward error events to frontend as visible text
                yield chunk
            # Intercept [DONE] — don't forward until all rounds finish

        tool_blocks, used_native = _resolve_tool_blocks(round_response, native_tool_calls, round_num, is_api_model=_is_api_model)

        # Force-answer round: we told the model to STOP calling tools and
        # answer. If it ignored that and emitted a (possibly DSML) tool
        # call anyway, discard it — don't execute, don't re-loop. Keep
        # only the prose; if there's none, emit a graceful fallback.
        if _force_answer:
            if tool_blocks:
                logger.info(f"[agent] force-answer round {round_num}: discarding {len(tool_blocks)} ignored tool call(s)")
            tool_blocks = []
            if not _THINK_RE.sub("", strip_tool_blocks(round_response)).strip():
                # The model burned its budget gathering data but never wrote a
                # final answer (common with weaker models on multi-source
                # briefings). Salvage it: one blunt non-streaming synthesis call
                # over the full conversation (which already holds every tool
                # result) before falling back to the canned apology.
                _synth = ""
                try:
                    from src.llm_core import llm_call_async
                    _synth_messages = list(messages) + [{
                        "role": "user",
                        "content": (
                            "Using ONLY the information already gathered above, write "
                            "the final answer for the user now. Do NOT call any tools, "
                            "do NOT explain your reasoning — output the finished response "
                            "directly. If some data couldn't be fetched, just work with "
                            "what you have and note what's missing in one short line."
                        ),
                    }]
                    _raw = await llm_call_async(
                        url=endpoint_url, model=model, messages=_synth_messages,
                        headers=headers, temperature=0.3, max_tokens=max_tokens, timeout=60,
                        session_id=session_id, owner=owner,
                        surface=audit_surface or "agent",
                        correlation_id=audit_correlation_id,
                        task_id=audit_task_id,
                        doc_id=audit_doc_id,
                        prompt_type="agent_grace_synthesis",
                    )
                    _synth = _THINK_RE.sub("", strip_tool_blocks(_raw or "")).strip()
                except Exception as _e:
                    logger.warning(f"[agent] grace synthesis failed: {_e}")
                if _synth:
                    yield f'data: {json.dumps({"delta": _synth})}\n\n'
                    full_response += _synth
                else:
                    _fb = ("I gathered some search results but couldn't pull a clean "
                           "answer together. Want me to try a more specific question, "
                           "or summarize what I did find?")
                    yield f'data: {json.dumps({"delta": _fb})}\n\n'
                    full_response += _fb

        # ── Fallback: auto-create document if model dumped large code in chat ──
        # If no create_document tool was used, check for big code blocks in text
        has_doc_tool = any(
            b.tool_type in ("create_document", "update_document")
            for b in tool_blocks
        ) or any(
            tc.get("name") in ("create_document", "update_document")
            for tc in native_tool_calls
        )
        if not has_doc_tool and session_id and "create_document" not in (disabled_tools or set()):
            _code_block_re = re.compile(r'```(\w*)\n([\s\S]*?)```')
            for m in _code_block_re.finditer(round_response):
                lang_tag = m.group(1).lower()
                code_body = m.group(2).strip()
                # Skip small blocks and known tool tags
                if code_body.count('\n') < 30:
                    continue
                if lang_tag in TOOL_TAGS:
                    continue  # already handled as a tool execution
                # Auto-create a document from this code block
                lang_map = {"py": "python", "js": "javascript", "ts": "typescript", "": "text"}
                doc_lang = lang_map.get(lang_tag, lang_tag or "text")
                doc_title = f"Code ({doc_lang})"
                tb = ToolBlock("create_document", f"{doc_title}\n{doc_lang}\n{code_body}")
                tool_blocks.append(tb)
                # Stream the document open event
                yield f'data: {json.dumps({"type": "doc_stream_open", "title": doc_title, "language": doc_lang})}\n\n'
                yield f'data: {json.dumps({"type": "doc_stream_delta", "content": code_body})}\n\n'
                logger.info(f"Auto-created document from {lang_tag} code block ({code_body.count(chr(10))+1} lines)")
                break  # only auto-create one document per round

        # Save cleaned round text for history persistence
        # Keep <think> blocks so they render in the thinking section on reload
        # Mirror the same fenced-pattern gate used to resolve tool_blocks above:
        # an illustrative fence that wasn't executed (because this is a native
        # model with no real native_tool_calls) must not be stripped from the
        # persisted text either — otherwise it streams once and then disappears
        # on reload (#3222 follow-up).
        cleaned_round = strip_tool_blocks(round_response, skip_fenced=(_is_api_model and not used_native)).strip()
        round_texts.append(cleaned_round)

        if not tool_blocks:
            # ── Completion verifier (mechanism 3a) ────────────────────
            # The model is finishing. If this was an effectful agentic turn,
            # have a fresh-context verifier independently check the work
            # before we accept "done". On FAIL, surface the issues and let
            # the model fix them (capped, and it must do new effectful work
            # to re-trigger). Skipped on force-answer rounds (no tools to
            # fix with), pure Q&A, and when the toggle is off.
            _claimed_done = bool(_THINK_RE.sub("", cleaned_round).strip())
            if (_effectful_used and not _force_answer
                    and _claimed_done
                    and _verifier_rounds < _VERIFIER_MAX_ROUNDS
                    # Default OFF: on weak local models the verifier can't judge
                    # from the action-snapshot (no doc body), so it false-rejects
                    # ("content not shown") and forces a costly extra round every
                    # effectful turn. Opt-in via setting for strong models.
                    and get_setting("agent_verifier_subagent", False)):
                # Brief "working" indicator while the verifier runs.
                yield f'data: {json.dumps({"type": "agent_step", "round": round_num})}\n\n'
                _vfail = await _run_verifier_subagent(
                    _verifier_instruction,
                    _build_actions_snapshot(tool_events),
                    endpoint_url=endpoint_url, model=model, headers=headers,
                    owner=owner, session_id=session_id,
                )
                if _vfail:
                    _verifier_rounds += 1
                    logger.info(f"[agent] verifier flagged {len(_vfail)} issue(s) on round {round_num}: {_vfail}")
                    _note = "\n\n_Double-checked the work and found something to fix._\n\n"
                    yield f'data: {json.dumps({"delta": _note})}\n\n'
                    full_response += _note
                    messages.append({
                        "role": "system",
                        "content": (
                            "An independent verifier reviewed your work against the "
                            "original request and found issues that must be fixed before "
                            "this is actually done:\n- " + "\n- ".join(_vfail) +
                            "\n\nFix these now using tools, then finish."
                        ),
                    })
                    # Require fresh effectful work before verifying again, so we
                    # never re-verify an unchanged state in a loop.
                    _effectful_used = False
                    continue
            # ── Intent-without-action supervisor ─────────────────────
            # Catch "Let me tail the output" / "I'll check the logs" /
            # "Let me investigate" patterns where the model announces an
            # action but emits no tool_call. The bug shows up most on
            # smaller models trained to verbalize plans before acting.
            # We inject one sharp nudge ("you said you would X — call the
            # actual tool now") and loop again. Capped at
            # _MAX_INTENT_NUDGES so a model that genuinely cannot use the
            # tool doesn't pin us in a forever loop.
            _intent_text = _THINK_RE.sub("", cleaned_round).strip()
            _intent_match = _INTENT_RE.search(_intent_text) if _intent_text else None
            # Only nudge when the round REALLY looks like an unfinished
            # promise: short response (<400 chars), no fenced code/answer,
            # and an action-intent phrase was matched. Long answers that
            # happen to contain "let me know" are not stalls.
            _looks_like_promise = (
                not guide_only
                and _intent_match is not None
                and len(_intent_text) < 400
                and "```" not in _intent_text
                and _intent_nudge_count < _MAX_INTENT_NUDGES
            )
            if _looks_like_promise:
                _intent_nudge_count += 1
                _matched_phrase = _intent_match.group(0).strip()
                logger.info(f"[agent] intent-without-action nudge #{_intent_nudge_count} on round {round_num}: {_matched_phrase!r}")
                messages.append({
                    "role": "system",
                    "content": (
                        f"You just wrote: \"{_matched_phrase}\" — but ended the "
                        "turn without making the actual tool call. The user can "
                        "see you announced the action but didn't run it, which "
                        "is the most frustrating thing you can do. "
                        "DO IT NOW: emit the actual function call this turn. "
                        "If you decided not to do it after all, say so plainly in "
                        "one sentence instead of restating the plan."
                    ),
                })
                # Visible signal in the stream so the user knows we caught it.
                yield f'data: {json.dumps({"type": "agent_step", "round": round_num + 1})}\n\n'
                continue
            break  # no tools — done

        # ── Loop-breaker (Terminus-style stall detector) ──────────────
        # Stall detector for repeated no-progress tool loops.
        # A round is "useless" ONLY when it re-issues a recent tool call AND
        # writes no answer text — i.e. the model is going in circles.
        # Genuine exploration (new, distinct calls) is never useless, so
        # multi-step work (file hunts, multi-host ssh, build→test→fix) rides
        # all the way to a real answer. We bail only on a streak of useless
        # rounds, or a single tool fired an absurd number of times (hard
        # runaway backstop). On bail we don't give up — we force one
        # tool-free round so the model declares done or declares blocked,
        # mirroring Terminus's explicit-completion handshake.
        _sig = "|".join(sorted(f"{b.tool_type}:{(b.content or '').strip()[:120]}" for b in tool_blocks))
        _is_repeat = _sig in _recent_call_sigs
        _recent_call_sigs.append(_sig)
        for _b in tool_blocks:
            _call_freq[f"{_b.tool_type}:{(_b.content or '').strip()[:120]}"] += 1
        # "Real" answer text = round text minus <think> blocks. Empty-think
        # rounds (just "<think>\n\n</think>" + a tool call) must not read as
        # progress, so strip think before checking.
        _real_text = _THINK_RE.sub("", cleaned_round).strip()
        # Circling = repeating a recent call with nothing written. Any
        # progress (a NEW distinct call, or actual answer text) resets it.
        if _is_repeat and not _real_text:
            _stuck_rounds += 1
        else:
            _stuck_rounds = 0
        # Runaway = the SAME exact call repeated an absurd number of times.
        # Distinct calls to one tool (a real batch) are legitimate work, so we
        # count identical call signatures, not raw per-tool-type totals.
        _runaway = _detect_runaway_call(_call_freq)
        if _stuck_rounds >= 4 or _runaway:
            reason = (f"calling {_runaway} with identical arguments over and over" if _runaway
                      else "repeating the same tool calls without new progress")
            logger.warning(f"[agent] loop-breaker tripped on round {round_num} ({reason}); sig={_sig[:80]!r}")
            if orchestrator_mode and not _is_teacher_run:
                await _run_orchestrator_reflector(
                    owner=owner,
                    session_id=session_id,
                    user_request=_last_user,
                    tool_events=tool_events,
                    trigger="runaway:" + reason,
                    round_num=round_num,
                )
            # The model has been executing tools, so its results are already
            # in context. Force ONE tool-free round to converge: write the
            # answer from what it has, or state plainly what's blocking it.
            # The force-answer handler above salvages (grace synthesis) or
            # apologizes honestly if it still writes nothing.
            _off = [t for t in ("web_search", "bash")
                    if disabled_tools and t in disabled_tools]
            _off_note = (f" ({', '.join(_off)} is currently disabled — say so if "
                         f"you needed it.)" if _off else "")
            _force_answer = True
            messages.append({
                "role": "system",
                "content": (
                    "You're repeating tool calls without converging. STOP calling "
                    "tools and end the turn one of two ways: (a) write your best "
                    "final answer NOW from the information already gathered, or "
                    "(b) if you're genuinely blocked, say plainly what's blocking "
                    "you in a sentence or two." + _off_note
                ),
            })
            full_response += "\n\n"
            yield f'data: {json.dumps({"type": "agent_step", "round": round_num + 1})}\n\n'
            continue

        # Pre-stream document content for fenced tool blocks (non-native path)
        # Native path already streamed via tool_call_delta above
        # For round 1 fenced blocks, frontend fence detection already handled streaming
        if not _doc_opened and round_num == 1:
            for block in tool_blocks:
                if tool_policy and tool_policy.blocks(block.tool_type):
                    continue
                if block.tool_type == "create_document":
                    _doc_opened = True
                    break

        if not _doc_opened:
            for block in tool_blocks:
                if tool_policy and tool_policy.blocks(block.tool_type):
                    continue
                if block.tool_type == "create_document":
                    lines = block.content.strip().split("\n")
                    title = lines[0].strip() if lines else "Untitled"
                    lang = ""
                    content_start = 1
                    if len(lines) > 1 and len(lines[1].strip()) < 20 and lines[1].strip().isalpha():
                        lang = lines[1].strip()
                        content_start = 2
                    content = "\n".join(lines[content_start:]) if len(lines) > content_start else ""
                    yield f'data: {json.dumps({"type": "doc_stream_open", "title": title, "language": lang})}\n\n'
                    if content:
                        yield f'data: {json.dumps({"type": "doc_stream_delta", "content": content})}\n\n'
                    break
                elif block.tool_type == "update_document":
                    # Pre-stream the full replacement content so user sees it immediately
                    content = block.content.strip()
                    yield f'data: {json.dumps({"type": "doc_stream_open", "title": "", "language": ""})}\n\n'
                    yield f'data: {json.dumps({"type": "doc_stream_delta", "content": content})}\n\n'
                    break

        # Execute each tool block
        tool_results = []
        tool_result_texts = []  # plain text for native tool role messages
        budget_hit = False
        for i, block in enumerate(tool_blocks):
            # --- Tool budget check ---
            if max_tool_calls > 0 and total_tool_calls >= max_tool_calls:
                yield f'data: {json.dumps({"type": "budget_exceeded", "limit": max_tool_calls, "used": total_tool_calls})}\n\n'
                budget_hit = True
                break

            total_tool_calls += 1
            # Build a short display string for the frontend tool bubble.
            # Document tools show a brief summary instead of dumping full content.
            is_doc_tool = block.tool_type in ("create_document", "update_document", "edit_document", "suggest_document")
            if is_doc_tool:
                cmd_display = block.content.split("\n")[0].strip()[:80]
            else:
                cmd_display = block.content.strip()

            if tool_policy and tool_policy.blocks(block.tool_type):
                desc = f"{block.tool_type}: BLOCKED"
                result = {
                    "error": tool_policy.reason_for(block.tool_type),
                    "exit_code": 1,
                    "blocked": True,
                }
                logger.info("Tool blocked before start by policy: %s", block.tool_type)
            else:
                yield (
                    f'data: {json.dumps({"type": "tool_start", "tool": block.tool_type, "command": cmd_display, "round": round_num})}\n\n'
                )

                # Streaming progress for long-running tools (bash, python).
                # The bash/python branches inside _direct_fallback emit
                # periodic {elapsed_s, tail} payloads via this callback;
                # we forward each one as a `tool_progress` SSE event so
                # the UI can render live elapsed-time + tail-of-output.
                _progress_q: asyncio.Queue = asyncio.Queue()
                async def _push_progress(payload):
                    await _progress_q.put(payload)

                async def _run_tool():
                    try:
                        return await execute_tool_block(
                            block,
                            session_id=session_id,
                            disabled_tools=disabled_tools,
                            tool_policy=tool_policy,
                            owner=owner,
                            progress_cb=_push_progress,
                            workspace=workspace,
                            endpoint_url=endpoint_url,
                            model=model,
                            headers=headers or {},
                            context_length=context_length,
                        )
                    finally:
                        # Sentinel so the drainer knows to stop.
                        await _progress_q.put(None)

                _tool_task = asyncio.create_task(_run_tool())
                # Drain progress events as they arrive — block until the
                # next event OR the tool finishes (sentinel = None).
                while True:
                    evt = await _progress_q.get()
                    if evt is None:
                        break
                    yield (
                        f'data: {json.dumps({"type": "tool_progress", "tool": block.tool_type, "round": round_num, **evt})}\n\n'
                    )
                desc, result = await _tool_task

            # Only the trusted publisher tool may create assistant attachment
            # metadata. Re-resolve the ID with the current owner before it can
            # enter SSE metrics or persisted chat history.
            if block.tool_type == "publish_artifact" and not result.get("error") and isinstance(result.get("attachment"), dict):
                try:
                    from src.generated_artifact_publication import (
                        get_generated_artifact_upload_handler,
                        project_generated_attachment,
                    )

                    _candidate_id = str(result["attachment"].get("id") or "")
                    _artifact_handler = get_generated_artifact_upload_handler()
                    _resolved_artifact = _artifact_handler.resolve_upload(
                        _candidate_id,
                        owner=owner,
                        allow_admin=False,
                    )
                    if _resolved_artifact:
                        _public_artifact = project_generated_attachment(_resolved_artifact)
                        # Vision model is safe display metadata, but only accept
                        # it from the publisher's trusted result.
                        _vision_model = result["attachment"].get("vision_model")
                        if isinstance(_vision_model, str) and _vision_model.strip():
                            _public_artifact["vision_model"] = _vision_model.strip()[:160]
                        result["attachment"] = _public_artifact
                        if _candidate_id not in _assistant_attachment_ids:
                            _assistant_attachment_ids.add(_candidate_id)
                            assistant_attachments.append(_public_artifact)
                    else:
                        result = {
                            "error": "Published artifact failed the owner-scoped readback check.",
                            "exit_code": 1,
                        }
                except Exception:
                    result = {
                        "error": "Published artifact failed the metadata safety check.",
                        "exit_code": 1,
                    }

            # A skill the model just loaded can prescribe tools that weren't
            # RAG-selected this turn (declared via requires_toolsets in its
            # frontmatter). Union them into the selection so the NEXT round's
            # schema list includes them — otherwise the model reads "use
            # grep" from the skill it fetched but has no grep schema to call.
            if (
                block.tool_type == "manage_skills"
                and _relevant_tools is not None
                and not result.get("error")
            ):
                _ms_args = {}
                _ms_raw = (block.content or "").strip()
                if _ms_raw.startswith("{"):
                    try:
                        _ms_args = json.loads(_ms_raw)
                    except json.JSONDecodeError:
                        _ms_args = {}
                _ms_name = str(_ms_args.get("name", "") or "").strip()
                if _ms_name and _ms_args.get("action") in ("view", "view_ref"):
                    try:
                        from services.memory.skills import SkillsManager as _SkM
                        from src.constants import DATA_DIR as _DD
                        from src.tool_policy import known_tool_names as _ktn
                        _known = _ktn()
                        for _sk in _SkM(_DD).load(owner=owner):
                            if _sk.get("name") == _ms_name:
                                _new = {
                                    t for t in (_sk.get("requires_toolsets") or [])
                                    if t in _known and t not in _relevant_tools
                                }
                                if _new:
                                    _relevant_tools.update(_new)
                                    logger.info(
                                        "[tool-rag] skill '%s' unlocked tools for next round: %s",
                                        _ms_name, sorted(_new),
                                    )
                                break
                    except Exception as _e:
                        logger.debug(f"skill requires_toolsets unlock skipped: {_e}")

            # Extract structured web sources from web_search tool output.
            # web_search returns {"output": ..., "exit_code": 0}; check "output"
            # first so the <!-- SOURCES:…--> marker is found and stripped even
            # when the result doesn't carry a "results" or "stdout" key.
            _src_text = result.get("output") or result.get("results") or result.get("stdout") or ""
            if block.tool_type == "web_search" and _src_text:
                _src_marker = "<!-- SOURCES:"
                _src_idx = _src_text.find(_src_marker)
                if _src_idx >= 0:
                    _src_end = _src_text.find(" -->", _src_idx)
                    if _src_end >= 0:
                        try:
                            _extracted_sources = json.loads(_src_text[_src_idx + len(_src_marker):_src_end])
                            yield f'data: {json.dumps({"type": "web_sources", "data": _extracted_sources})}\n\n'
                            # Strip the marker from the result so it doesn't show in chat
                            _clean = _src_text[:_src_idx].rstrip()
                            if "output" in result:
                                result["output"] = _clean
                            elif "results" in result:
                                result["results"] = _clean
                            elif "stdout" in result:
                                result["stdout"] = _clean
                        except (json.JSONDecodeError, Exception):
                            pass

            # Emit doc-specific event for document tools — the frontend
            # document panel handles this; no need to show content in chat.
            if is_doc_tool and "action" in result:
                if result["action"] == "suggest":
                    yield (
                        f'data: {json.dumps({"type": "doc_suggestions", "doc_id": result["doc_id"], "suggestions": result["suggestions"]})}\n\n'
                    )
                else:
                    yield (
                        f'data: {json.dumps({"type": "doc_update", "doc_id": result["doc_id"], "content": result["content"], "version": result["version"], "title": result.get("title", ""), "language": result.get("language")})}\n\n'
                    )

            # Emit ui_control event for frontend to apply UI changes
            if "ui_event" in result:
                yield (
                    f'data: {json.dumps({"type": "ui_control", "data": result})}\n\n'
                )

            _pending_ask_user_event = None
            # ask_user: the agent posed a multiple-choice question. Emit it so the
            # frontend renders clickable options, then end the turn (below) and
            # wait — the user's pick becomes the next message.
            if "ask_user" in result:
                # The question lives in the tool args. ChatMessage.to_dict()
                # replays only role+content to the model next turn — tool_event
                # metadata is dropped — so if the question is never in the saved
                # assistant text, the model can't see it already asked and will
                # loop and re-ask after the user answers. Stream it as assistant
                # text (once) so it persists and is replayed. The card shows the
                # options only, so this is the single visible copy of the question.
                _auq = result["ask_user"]
                _auq_q = (_auq.get("question") or "").strip()
                if _auq_q and _auq_q not in full_response:
                    _auq_delta = ("\n\n" if full_response.strip() else "") + _auq_q
                    full_response += _auq_delta
                    yield 'data: ' + json.dumps({"delta": _auq_delta}) + '\n\n'
                _pending_ask_user_event = _auq
                _awaiting_user = True

            # update_plan: agent wrote back to the plan (ticked a step / revised).
            # Push it to the frontend so the stored plan + docked window update
            # live. Does NOT end the turn — the agent keeps working.
            if "plan_update" in result:
                yield (
                    f'data: {json.dumps({"type": "plan_update", "data": result["plan_update"]})}\n\n'
                )

            # Build output for frontend tool bubble.
            # Document tools get a short summary — content goes to the editor panel.
            output_text = ""
            if is_doc_tool and "action" in result:
                action = result["action"]
                title = result.get("title", "")
                ver = result.get("version", "?")
                if action == "create":
                    output_text = f'Document created: "{title}" (v{ver})'
                elif action == "edit":
                    output_text = f'Document edited: "{title}" (v{ver}, {result.get("applied", 0)} edit(s))'
                elif action == "update":
                    output_text = f'Document updated: "{title}" (v{ver})'
            elif "stdout" in result:
                # On a bash/python timeout the result carries error + (often
                # empty) stdout/stderr; fall back to the error so the "timed
                # out" reason reaches the UI instead of a blank result.
                raw = result["stdout"] or result["stderr"] or result.get("error", "")
                output_text = _truncate(raw)
            elif "output" in result:
                # bash / python canonical result: {"output": ..., "exit_code": ...}
                raw = result["output"] or ""
                output_text = _truncate(raw)
            elif "response" in result:
                # AI interaction tools (chat_with_model, send_to_session)
                label = result.get("model", result.get("session_name", "AI"))
                output_text = _truncate(f"{label}: {result['response']}")
            elif "content" in result:
                output_text = _truncate(result["content"])
            elif "results" in result:
                output_text = _truncate(result["results"])
            elif "session_id" in result and "name" in result:
                output_text = f"Session created: {result['name']} (id: {result['session_id']})"
            elif "success" in result:
                output_text = (
                    f"Written: {result.get('path', '')}"
                    if result["success"]
                    else f"Error: {result.get('error', '')}"
                )
            elif "error" in result:
                output_text = _truncate(result["error"])

            # Emit tool_output (include ui_event data if present)
            tool_output_data = {"type": "tool_output", "tool": block.tool_type, "command": cmd_display, "output": output_text, "exit_code": result.get("exit_code")}
            if _pending_ask_user_event:
                tool_output_data["ask_user"] = _pending_ask_user_event
            if "ui_event" in result:
                tool_output_data["ui_event"] = result["ui_event"]
                for k in (
                    "toggle_name", "state", "mode", "model", "endpoint_url",
                    "theme_name", "colors",
                    # ui_control open_email_reply payload — without these the
                    # frontend openReplyDraft bails on undefined uid and the
                    # reply window silently never opens.
                    "uid", "folder", "account_id",
                    # Optional pre-filled body for open_email_reply so the
                    # agent can compose-and-open in one tool call.
                    "body",
                    # ui_control open_panel payload
                    "panel",
                ):
                    if k in result:
                        tool_output_data[k] = result[k]
            # Forward image data from generate_image tool
            for k in ("image_url", "image_prompt", "image_model", "image_size", "image_quality"):
                if k in result:
                    tool_output_data[k] = result[k]
            for k in (
                "attachment", "artifact_evidence", "interactive_runtime",
                "headless_evidence", "pygame_headless_plan", "screenshot_ref",
            ):
                if k in result:
                    tool_output_data[k] = result[k]
            # Forward screenshots from browser tools (base64 images)
            if result.get("images"):
                img = result["images"][0]
                tool_output_data["screenshot"] = f"data:{img['mimeType']};base64,{img['data']}"
            # Forward a file-write diff for inline before/after rendering
            if "diff" in result:
                tool_output_data["diff"] = result["diff"]
            yield f'data: {json.dumps(tool_output_data)}\n\n'
            if _pending_ask_user_event:
                yield (
                    f'data: {json.dumps({"type": "ask_user", "data": _pending_ask_user_event})}\n\n'
                )

            # Native document tools open in the editor + carry the REAL doc id.
            # Emit a doc_update so the frontend opens/activates it and sends it
            # back as active_doc_id next turn (otherwise the agent can't "see"
            # the document it just created on the follow-up message).
            if block.tool_type in ("create_document", "update_document", "edit_document") and result.get("doc_id"):
                yield (
                    'data: ' + json.dumps({
                        "type": "doc_update",
                        "doc_id": result["doc_id"],
                        "title": result.get("title", ""),
                        "language": result.get("language", ""),
                        "content": result.get("content", ""),
                        "version": result.get("version", 1),
                    }) + '\n\n'
                )

            # Inline research: emit the open-link as part of the assistant's
            # actual response text — a `#research-<id>` anchor that chatRenderer
            # turns into a regular clickable link. Saved with the message, so it
            # PERSISTS across refresh (unlike the old ephemeral injected chip).
            _rsid = result.get("research_session_id")
            if _rsid:
                _anchor = f"\n\n[Open in Deep Research](#research-{_rsid})\n"
                yield 'data: ' + json.dumps({"delta": _anchor}) + '\n\n'

            # Same pattern for notes: when manage_notes creates a note
            # and returns note_id, drop a `[View note](#note-<id>)` link
            # into the stream so chatRenderer's click handler routes to
            # the new openNote() in notes.js — opens the notes panel and
            # scrolls/flashes the matching card. Without this, the agent
            # would write "View note" as a phrase with no target.
            _nid = result.get("note_id")
            if _nid and block.tool_type == "manage_notes":
                _title = (result.get("note_title") or "").strip()
                _label = f"View note: {_title}" if _title else "View note"
                _anchor = f"\n\n[{_label}](#note-{_nid})\n"
                yield 'data: ' + json.dumps({"delta": _anchor}) + '\n\n'

            # Save for history persistence
            tool_event = {
                "round": round_num,
                "tool": block.tool_type,
                "command": cmd_display,
                "output": output_text,
                "exit_code": result.get("exit_code"),
            }
            if result.get("image_url"):
                for ik in ("image_url", "image_prompt", "image_model", "image_size", "image_quality"):
                    if result.get(ik):
                        tool_event[ik] = result[ik]
            if result.get("doc_id"):
                tool_event["doc_id"] = result["doc_id"]
                tool_event["doc_title"] = result.get("title", "")
            # Persist the file-write/edit diff so it re-renders on reload — without
            # this the diff shows live but vanishes from saved history.
            if result.get("diff"):
                tool_event["diff"] = result["diff"]
            for key in (
                "attachment", "artifact_evidence", "interactive_runtime",
                "headless_evidence", "pygame_headless_plan", "screenshot_ref",
            ):
                if result.get(key):
                    tool_event[key] = result[key]
            if _pending_ask_user_event:
                tool_event["ask_user"] = _pending_ask_user_event
            tool_events.append(tool_event)
            if block.tool_type in _VERIFIER_EFFECTFUL_TOOLS:
                _effectful_used = True

            formatted = format_tool_result(desc, result)
            tool_results.append(formatted)
            tool_result_texts.append(formatted)

        # If budget was hit, stop the loop
        if budget_hit:
            break

        # ask_user posed a question — stop here and wait for the user's choice.
        # Don't feed tool results back or advance a round; the user's selection
        # arrives as the next message and the agent resumes from there. The
        # question text is already in the streamed response, so it persists.
        if _awaiting_user:
            break

        # Feed results back to LLM for next round
        _append_tool_results(messages, round_response, native_tool_calls,
                             tool_results, tool_result_texts, used_native, round_num,
                             round_reasoning=round_reasoning)

        if orchestrator_mode and not _is_teacher_run and round_num % 3 == 0:
            await _run_orchestrator_reflector(
                owner=owner,
                session_id=session_id,
                user_request=_last_user,
                tool_events=tool_events,
                trigger="periodic",
                round_num=round_num,
            )

        # Emit agent_step event
        yield (
            f'data: {json.dumps({"type": "agent_step", "round": round_num + 1})}\n\n'
        )

        # Separator in accumulated response
        full_response += "\n\n"
    else:
        # The for-loop completed every allowed round WITHOUT an early `break`
        # (a `break` fires on "done", budget, or error). Reaching this `else`
        # means the agent kept working until it ran out of rounds — so offer
        # Continue instead of stopping silently. This catches ALL exhaustion
        # paths, including a verifier `continue` on the final round (the old
        # bottom-of-loop flag missed those).
        _exhausted_rounds = True

    # If the loop hit the round cap while still working, tell the client so it
    # can show a "Continue" affordance instead of the turn just stopping.
    if _exhausted_rounds:
        logger.info("[agent] round cap (%d) reached mid-task — emitting rounds_exhausted", max_rounds)
        yield f'data: {json.dumps({"type": "rounds_exhausted", "rounds": max_rounds})}\n\n'

    # If the response is completely empty and no tools were executed,
    # yield a fallback message so the user is not left hanging.
    full_response, _fallback_chunk = _empty_response_fallback(
        full_response, round_reasoning, tool_events
    )
    if _fallback_chunk:
        yield _fallback_chunk

    claim_evidence_gate = {}
    tool_transactions = tuple(item.to_dict() for item in transactions_from_tool_events(tool_events, surface="agent"))
    try:
        _claim_report = evaluate_response_claims(
            full_response,
            tool_events,
            repo_root=Path.cwd(),
            tool_transactions=tool_transactions,
        )
        claim_evidence_gate = _claim_report.to_dict()
        _claim_correction = build_claim_evidence_correction(_claim_report)
        if _claim_correction:
            full_response += _claim_correction
            yield f'data: {json.dumps({"delta": _claim_correction})}\n\n'
    except Exception as _claim_err:
        logger.warning("[agent] claim evidence gate skipped: %s", _claim_err, exc_info=True)
        claim_evidence_gate = {"ok": False, "error": "claim_evidence_gate_skipped"}

    # --- Final metrics ---
    total_duration = time.time() - total_start
    metrics = _compute_final_metrics(
        messages, full_response, total_duration, time_to_first_token,
        context_length, real_input_tokens, real_output_tokens,
        has_real_usage, tool_events, round_texts, model=actual_model,
        last_round_input_tokens=last_round_input_tokens,
        prep_timings=prep_timings,
        backend_gen_tps=backend_gen_tps,
        backend_prefill_tps=backend_prefill_tps,
    )
    metrics["requested_model"] = requested_model
    metrics["claim_evidence_gate"] = claim_evidence_gate
    if _interactive_deliverable_decision is not None:
        metrics["interactive_deliverable_policy"] = _interactive_deliverable_decision.audit_summary()
    if assistant_attachments:
        metrics["attachments"] = assistant_attachments
    if tool_transactions:
        metrics["tool_transactions"] = tool_transactions
    yield f"data: {json.dumps({'type': 'metrics', 'data': metrics})}\n\n"

    # Teacher-escalation: inline takeover visible in the chat stream.
    # The student just finished; if Tier 1 flags failure, the teacher
    # gets a turn (with its own tool calls forwarded to the user) and
    # a skill is saved ONLY if the teacher actually succeeds. Skipped
    # when we ARE the teacher to avoid recursion.
    if not _is_teacher_run and not guide_only:
        try:
            from src.teacher_escalation import run_teacher_inline
            async for evt in run_teacher_inline(
                student_endpoint_url=endpoint_url,
                student_messages=messages,
                student_tool_events=tool_events,
                student_reply=full_response,
                owner=owner,
            ):
                yield evt
        except Exception as _esc_err:
            logger.warning(f"teacher escalation hook failed: {_esc_err}", exc_info=True)

    yield "data: [DONE]\n\n"
