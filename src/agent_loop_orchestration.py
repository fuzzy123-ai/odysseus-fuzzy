"""Verifier, plan-mode, context-provider and orchestrator helpers for ``src.agent_loop``."""

import json
import logging
import re
from typing import Dict, List, Optional

from src.effectful_tool_matrix import build_effectful_action_snapshot, effectful_tool_names

logger = logging.getLogger(__name__)

# ── Completion verifier ──
# Tools whose effects produce a checkable artifact. A turn that used one of
# these is "effectful" and worth an independent completion check; pure
# read-only / Q&A turns are not.
_VERIFIER_EFFECTFUL_TOOLS = set(effectful_tool_names())
_VERIFIER_MAX_ROUNDS = 2  # cap re-verify cycles per turn — never loop forever


def _build_actions_snapshot(tool_events: list, limit: int = 8000) -> str:
    """Compact record of what the agent actually did this turn, for the
    verifier to judge against. One block per tool execution: the command and
    a head of its output."""
    parts = []
    for ev in tool_events:
        tool = ev.get("tool", "?")
        cmd = (ev.get("command") or "").strip()
        out = (ev.get("output") or "").strip()
        rc = ev.get("exit_code")
        head = f"[{tool}] {cmd}" if cmd else f"[{tool}]"
        rc_s = f" (exit {rc})" if rc not in (None, 0) else ""
        body = (out[:1200] + " …") if len(out) > 1200 else (out or "(no output)")
        parts.append(f"{head}{rc_s}\n-> {body}")
    evidence_snapshot = build_effectful_action_snapshot(tool_events)
    if evidence_snapshot.get("transactions") or evidence_snapshot.get("categories"):
        parts.append(
            "[machine_evidence]\n-> "
            + json.dumps(evidence_snapshot, ensure_ascii=True, sort_keys=True)[:3000]
        )
    snap = "\n\n".join(parts)
    return snap[:limit] if len(snap) > limit else snap


async def _run_verifier_subagent(
    instruction: str, actions_snapshot: str,
    *, endpoint_url: str, model: str, headers: dict,
    owner: Optional[str] = None, session_id: Optional[str] = None,
) -> list:
    """Fresh-context completion verifier. A second model instance with NO
    shared history reads the user's request + a record of what the agent did
    and judges whether the task is genuinely complete. The independent context
    is the whole point: a model checking its own work rationalizes; one that
    didn't do the work reads it cold. Returns a list of failure reasons
    (empty = pass, or silently empty on any error so it can't block a valid
    completion)."""
    from src.llm_core import llm_call_async
    prompt = (
        "You are an independent verifier. Another assistant just claimed the "
        "following task is complete. Using ONLY the request and the record of "
        "what it actually did, decide whether that claim is correct. Be strict: "
        "only say SUCCESS if the work genuinely satisfies the request.\n\n"
        f"<user_request>\n{(instruction or '')[:4000]}\n</user_request>\n\n"
        f"<actions_taken>\n{actions_snapshot[:8000]}\n</actions_taken>\n\n"
        "<checklist>\n"
        "1. Every concrete deliverable the request asked for was actually produced\n"
        "2. Outputs/edits match what was asked — nothing missing, no extra or unrequested changes\n"
        "3. Tool results show success, not errors or empty output that got ignored\n"
        "4. Anything the request said to leave alone was left unchanged\n"
        "</checklist>\n\n"
        "Reason briefly (2-3 sentences max). Then output EXACTLY one of:\n"
        "  VERIFICATION: SUCCESS\n"
        "  VERIFICATION: FAIL: <one short sentence per issue, semicolon-separated>\n"
        "Output nothing after the VERIFICATION line."
    )
    try:
        raw = await llm_call_async(
            url=endpoint_url, model=model,
            messages=[{"role": "user", "content": prompt}],
            headers=headers, temperature=0.0, max_tokens=600, timeout=60,
            owner=owner,
            surface="agent",
            session_id=session_id,
            prompt_type="agent_verifier",
        )
    except Exception as e:
        logger.warning(f"[agent] verifier subagent failed: {e}")
        return []
    raw = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL | re.IGNORECASE)
    last_v = None
    for line in raw.splitlines():
        if "VERIFICATION:" in line:
            last_v = line.strip()
    if not last_v or "VERIFICATION: FAIL:" not in last_v:
        return []
    reasons = last_v.split("VERIFICATION: FAIL:", 1)[1].strip()
    return [r.strip() for r in reasons.split(";") if r.strip()]


def _empty_response_fallback(
    full_response: str,
    round_reasoning: str,
    tool_events: list,
) -> tuple:
    """Return (final_response, sse_chunk_or_none) for the end-of-loop empty-response guard.

    When a thinking model routes all tokens to reasoning_content (leaving
    content=""), full_response is empty but round_reasoning has content.
    The reasoning was already streamed as {thinking:true} chunks — do not
    re-emit it as a normal delta.  Just persist it and yield nothing.

    Returns:
        (final_response: str, chunk: str | None)
            chunk is the SSE string to yield, or None if nothing should be emitted.
    """
    if full_response.strip() or tool_events:
        return full_response, None
    if round_reasoning.strip():
        return round_reasoning, None
    _error_msg = "The model returned an empty response. Please try again or switch to a different model."
    return _error_msg, f'data: {json.dumps({"delta": _error_msg})}\n\n'


PLAN_MODE_DIRECTIVE = (
    "## PLAN MODE — OVERRIDES EVERYTHING ELSE BELOW\n"
    "You are in PLAN MODE. Your ONLY job this turn is to PROPOSE a plan. You have "
    "NOT done anything yet. Do NOT claim you created, wrote, ran, sent, or changed "
    "anything — that would be a lie.\n"
    "\n"
    "ABSOLUTE RULE — DO NOT MUTATE ANYTHING. Every write/state-changing tool, "
    "including the shell (`bash`/`python`), is disabled this turn and will be "
    "rejected — only read-only tools remain available. Use the read-only tools "
    "listed below (read files, search code, browse the project, web lookups) to "
    "ground the plan. If the task is 'write a file', your plan is to DESCRIBE "
    "writing it — you do NOT write it now.\n"
    "\n"
    "OUTPUT: present the plan as a GitHub-style checklist, one concrete step per line:\n"
    "- [ ] first action you will take once approved\n"
    "- [ ] next action\n"
    "Each item = one concrete action (file to create/edit, command to run, side "
    "effect). Do not execute. Do not end with 'Done' or anything implying the work "
    "is finished. End your turn with the checklist."
)

ORCHESTRATOR_MODE_DIRECTIVE = (
    "## ORCHESTRATOR MODE\n"
    "You are the master agent for this run. Break implementation work into "
    "small non-overlapping slices, keep durable progress in the Obsidian state "
    "doc when available, inspect only the context needed to route work, use "
    "`delegate` only for lightweight read-only analysis, and use the fake "
    "subagent runtime surface for durable worker runs. Assume another agent may "
    "already be working in the project: avoid assigning overlapping files or "
    "mutating state yourself until ownership is clear. Do not directly edit host "
    "files, run shell commands, call broad app APIs, or perform the worker's "
    "implementation work yourself. Summarize worker results, record slice "
    "ownership and risks, and choose the next orchestration step."
)


def build_active_plan_note(approved_plan: str) -> str:
    """System note that pins an approved plan during execution.

    Sent back by the frontend each turn so a long plan on a weak model survives
    history truncation — the agent can always re-read it. Returns "" for empty
    input.
    """
    if not approved_plan or not approved_plan.strip():
        return ""
    return (
        "## ACTIVE PLAN (approved — execute this)\n"
        "You are executing a plan the user already approved. THE FULL PLAN IS "
        "BELOW — it is always provided here every turn. Do NOT say you lost it, "
        "and do NOT look for it in tasks, notes, memory, files, or the API; just "
        "read it below. Work through it IN ORDER. After finishing each step, call "
        "the `update_plan` tool with the full checklist and that step marked "
        "`- [x]` so progress stays visible in the user's plan window. If the user "
        "asks to change the plan, call `update_plan` with the revised checklist. "
        "Do the next unchecked item until all are done. Do not skip, reorder, or "
        "invent steps; if a step is genuinely impossible, say so and stop.\n\n"
        "Current plan:\n"
        + approved_plan.strip()
    )


def _inject_context_provider_messages(
    messages: List[Dict],
    *,
    owner: Optional[str],
    query: str,
    context_length: int,
    enabled: bool = True,
) -> List[Dict]:
    """Insert generic plugin provider context after the primary system prompt."""
    if not enabled:
        return messages
    try:
        from src.settings import load_features
        from src.context_orchestrator import (
            preload_provider_context,
            provider_messages,
            provider_warning_messages,
            split_context_budget,
        )

        if not load_features().get("context_provider_preload", True):
            return messages
        budget = split_context_budget(context_length or 4000)
        payloads, warnings = preload_provider_context(
            owner=owner,
            query=query,
            budget_tokens=budget.providers,
            mode="agent",
        )
        injected = provider_messages(payloads) + provider_warning_messages(warnings)
        if not injected:
            for warning in warnings:
                logger.warning("[agent] Context provider warning: %s", warning)
            return messages
        for warning in warnings:
            logger.warning("[agent] Context provider warning: %s", warning)
        if messages and messages[0].get("role") == "system":
            return [messages[0]] + injected + messages[1:]
        return injected + messages
    except Exception as e:
        logger.warning("[agent] Context provider preload skipped: %s", e)
        return messages


def _detect_runaway_call(call_freq, threshold=15):
    """Tool name of a call signature repeated >= ``threshold`` times — a real
    runaway loop. Counts IDENTICAL repeated calls (same tool AND args), so a
    legitimate batch of distinct calls to one tool (e.g. creating 18 calendar
    events at once) is NOT flagged. Returns ``None`` when nothing is runaway.

    ``call_freq`` is a Counter keyed by ``"{tool_type}:{content[:120]}"``.
    """
    sig = next((s for s, n in call_freq.items() if n >= threshold), None)
    return sig.split(":", 1)[0] if sig else None


def _ensure_orchestrator_state_doc(*, owner: Optional[str], session_id: Optional[str], goal: str) -> None:
    try:
        from src.plugin_system import import_plugin_module

        state_doc = import_plugin_module("obsidian", "backend.state_doc")
        vault_service = import_plugin_module("obsidian", "backend.vault_service")
        vault_security = import_plugin_module("obsidian", "backend.vault_security")

        try:
            vault_dir = vault_service.unlocked_vault_path_for_owner(owner)
        except vault_security.VaultSecurityError:
            return
        if state_doc.read_state_doc(vault_dir) is None:
            state_doc.initialize_state_doc(
                vault_dir,
                owner=owner,
                session_id=session_id,
                goal=goal,
                checklist=[
                    "Identify active slice ownership and avoid overlapping files.",
                    "Delegate one focused slice.",
                    "Review worker result and decide next step.",
                ],
                open_questions=[
                    "Which files or slices are currently owned by another active agent?",
                ],
            )
        else:
            state_doc.append_step_entry(
                vault_dir,
                owner=owner,
                entry="Orchestrator run resumed.",
                status="active",
            )
    except Exception:
        logger.debug("[orchestrator] state-doc initialization skipped", exc_info=True)


def _orchestrator_actions_snapshot(tool_events: list, *, limit: int = 6000) -> str:
    if not tool_events:
        return "(no tool actions recorded yet)"
    parts = []
    for event in tool_events[-20:]:
        tool = event.get("tool", "?")
        cmd = (event.get("command") or "").strip()
        output = (event.get("output") or "").strip()
        exit_code = event.get("exit_code")
        head = f"round {event.get('round', '?')}: {tool}"
        if cmd:
            head += f" {cmd[:180]}"
        if exit_code not in (None, 0):
            head += f" (exit {exit_code})"
        parts.append(head + ("\n" + output[:800] if output else ""))
    snapshot = "\n\n".join(parts)
    return snapshot[:limit] if len(snapshot) > limit else snapshot


async def _run_orchestrator_reflector(
    *,
    owner: Optional[str],
    session_id: Optional[str],
    user_request: str,
    tool_events: list,
    trigger: str,
    round_num: Optional[int] = None,
) -> Optional[Dict]:
    try:
        from src.plugin_system import import_plugin_module
        from src.reflector_agent import run_reflector_assessment

        state_doc = import_plugin_module("obsidian", "backend.state_doc")
        vault_service = import_plugin_module("obsidian", "backend.vault_service")
        vault_security = import_plugin_module("obsidian", "backend.vault_security")

        try:
            vault_dir = vault_service.unlocked_vault_path_for_owner(owner)
        except vault_security.VaultSecurityError:
            return None
        doc = state_doc.read_state_doc(vault_dir)
        if doc is None:
            doc = state_doc.initialize_state_doc(
                vault_dir,
                owner=owner,
                session_id=session_id,
                goal=user_request,
                checklist=["Reflect on current progress.", "Choose next step."],
            )
        result = await run_reflector_assessment(
            owner=owner,
            user_request=user_request,
            state_doc_content=doc.content,
            actions_snapshot=_orchestrator_actions_snapshot(tool_events),
            trigger=trigger,
            round_num=round_num,
        )
        if not result:
            return None
        state_doc.append_reflection_entry(
            vault_dir,
            owner=owner,
            trigger=trigger,
            status=str(result.get("status") or "ok"),
            assessment=str(result.get("assessment") or ""),
            risks=result.get("risks") if isinstance(result.get("risks"), list) else [],
            next_step=str(result.get("next_step") or ""),
            note=str(result.get("state_doc_note") or ""),
            teacher_model=str(result.get("teacher_model") or ""),
        )
        return result
    except Exception:
        logger.debug("[orchestrator] reflector skipped", exc_info=True)
        return None

