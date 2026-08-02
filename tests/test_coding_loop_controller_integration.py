import ast
from pathlib import Path

from src.coding_loop_contracts import (
    CodingGateSubject,
    CodingLoopCommandKind,
    CodingLoopIntentKind,
    CodingLoopModelCommand,
)
from src.coding_loop_controller import (
    CodingLoopDisposition,
    apply_coding_loop_command,
    start_coding_loop_controller,
)
from src.coding_loop_model_adapter import adapt_scripted_coding_model

from test_coding_loop_controller import _advance, _context, _gate, _lifecycle


def test_fake_model_loop_reaches_review_ready_with_intents_but_no_execution():
    authority, envelope, capsules = _context()
    state = start_coding_loop_controller(
        lifecycle=_lifecycle("clarifying", authority),
        parent_envelope=envelope,
        capsules=capsules,
    )
    scripted = adapt_scripted_coding_model(
        (
            {"command_kind": "advance", "command_ref": "step-planning", "target_state": "planning"},
            {"command_kind": "advance", "command_ref": "step-ready-claim", "target_state": "ready_for_claim"},
            {"command_kind": "advance", "command_ref": "step-claimed", "target_state": "claimed"},
            {"command_kind": "advance", "command_ref": "step-context-building", "target_state": "context_building"},
            {"command_kind": "advance", "command_ref": "step-context-ready", "target_state": "context_ready"},
            {"command_kind": "advance", "command_ref": "step-worktree-ready", "target_state": "worktree_ready"},
            {"command_kind": "advance", "command_ref": "step-acting", "target_state": "acting"},
            {
                "command_kind": "mutation_intent",
                "command_ref": "step-patch-intent",
                "intent_kind": "propose_scoped_patch",
                "role": "implementer",
                "target_graph_ref": "code-ref-implementer-1",
                "exact_read_required_ref": "code-ref-implementer-1",
                "payload_digest": "sha256:" + "f" * 64,
            },
            {
                "command_kind": "check_intent",
                "command_ref": "step-check-intent",
                "intent_kind": "request_bounded_check",
                "role": "tester",
                "target_graph_ref": "code-ref-tester-2",
                "exact_read_required_ref": "code-ref-tester-2",
            },
            {
                "command_kind": "review",
                "command_ref": "step-independent-review",
                "target_state": "review_ready",
                "role": "reviewer",
                "evidence_ref": "evidence-cao08c-12",
            },
        )
    )

    for command in scripted:
        gate = None
        if command.command_kind is CodingLoopCommandKind.MUTATION_INTENT:
            gate = _gate(CodingGateSubject.ROUTINE_IMPLEMENTATION)
        elif command.command_kind is CodingLoopCommandKind.CHECK_INTENT:
            gate = _gate(CodingGateSubject.BOUNDED_VERIFICATION)
        elif command.command_kind is CodingLoopCommandKind.REVIEW:
            gate = _gate(CodingGateSubject.INDEPENDENT_REVIEW)
        state = apply_coding_loop_command(state, command=command, gate=gate)

    assert state.lifecycle.state == "review_ready"
    assert state.disposition is CodingLoopDisposition.REVIEW_READY
    assert len(state.intents) == 2
    assert tuple(item.intent_kind for item in state.intents) == (
        CodingLoopIntentKind.PROPOSE_SCOPED_PATCH,
        CodingLoopIntentKind.REQUEST_BOUNDED_CHECK,
    )
    payload = state.to_dict()
    assert payload["execution_allowed"] is False
    assert payload["edit_allowed"] is False
    assert payload["write_allowed"] is False
    assert payload["dispatch_allowed"] is False
    assert payload["gate_close_allowed"] is False
    assert payload["live_effect_allowed"] is False
    assert all(intent["execution_allowed"] is False for intent in payload["intents"])


def test_controller_never_allows_model_to_jump_directly_to_review_ready():
    authority, envelope, capsules = _context()
    state = start_coding_loop_controller(
        lifecycle=_lifecycle("clarifying", authority),
        parent_envelope=envelope,
        capsules=capsules,
    )
    command = CodingLoopModelCommand(
        command_kind=CodingLoopCommandKind.ADVANCE,
        command_ref="narrative-jump",
        target_state="review_ready",
    )
    try:
        apply_coding_loop_command(state, command=command)
    except Exception as exc:
        assert "independent review" in str(exc)
    else:
        raise AssertionError("narrative review_ready jump was accepted")


def test_new_controller_sources_have_no_effectful_or_generic_loop_imports():
    root = Path(__file__).resolve().parents[1]
    relative_paths = (
        "src/coding_loop_contracts.py",
        "src/coding_loop_controller.py",
        "src/coding_loop_model_adapter.py",
    )
    banned_modules = {
        "src.agent_loop",
        "src.agent_tools",
        "src.tool_execution",
        "src.tool_implementations",
        "src.subagent_runtime",
        "src.tool_domains.app_api",
        "subprocess",
    }
    banned_calls = {
        "execute_tool_block",
        "stream_agent_loop",
        "dispatch",
        "Popen",
        "run",
        "call",
        "check_call",
        "check_output",
    }
    for relative in relative_paths:
        source = (root / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert not imported & banned_modules
        assert not calls & banned_calls
