#!/usr/bin/env python3
"""Generate/check the content-free TTD-00 static domain-truth contract."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from typing import Any

SCHEMA = "odysseus.telegram_todo_domain_truth.v1"
ROLES = (
    "Notes", "Digest", "Memory", "ScheduledTask", "Chat History", "Telegram", "claim/evidence",
)
ROLE_DEFINITIONS = (
    ("Notes", "canonical_todo_truth", "current_notes_checklist", "core/database.py", ("Note",)),
    ("Digest", "notes_read_only_projection", "current_todo_digest", "src/builtin_actions.py", ("_todo_digest_from_notes", "action_todo_digest")),
    ("Memory", "todo_writes_forbidden", "current_enforcement_absent", "src/agent_loop_prompts.py", ("manage_notes", "manage_memory")),
    ("ScheduledTask", "schedule_and_delivery_only", "current_scheduled_task", "src/calendar_capability_service.py", ("build_todo_digest_schedule_plan", "write_todo_digest_schedule")),
    ("Chat History", "untrusted_context_only", "current_privacy_boundary", "src/telegram_truth_runtime.py", ("raw_content_visible",)),
    ("Telegram", "transport_and_redacted_audit_only", "current_control_bridge", "plugins/telegram/control_service.py", ("parse_todo_digest_tail", "handle_calendar_control_command")),
    ("claim/evidence", "semantic_receipt_authority", "current_todo_receipts_absent", "src/telegram_truth_gate.py", ("gate_telegram_reply_text",)),
)
EXTRA_SOURCES = {
    "core/database.py": ("Note", "ScheduledTask"),
    "plugins/telegram/parsing.py": ("calendar_todo_digest_create",),
    "plugins/telegram/plugin.py": ("write_todo_digest_schedule",),
    "src/claim_evidence_gate.py": ("evaluate_response_claims",),
    "src/tool_domains/personal_workspace.py": ("do_manage_notes", "toggle_item"),
    "src/request_models.py": ("MemoryAddRequest", "MemoryUpdateRequest", "task"),
    "src/memory.py": ("add_entry", "category"),
    "src/task_scheduler.py": ("TaskScheduler", "_looks_like_todo_digest"),
    "plugins/telegram/stores.py": ("TelegramInboxStore", "history"),
    "routes/note_routes.py": ("Note",), "src/ai_interaction.py": ("do_manage_memory",),
    "src/task_scheduler_delivery.py": ("_is_todo_digest_task",), "src/tool_transaction_ledger.py": ("ToolTransaction",),
}
ABSENT_TODO_CLAIMS = (b"todo_item_created", b"todo_item_completed", b"todo_item_reopened", b"todo_item_removed", b"todo_list_read", b"todo_digest_postcondition")
LITERAL_MARKERS = {"Note", "manage_notes", "manage_memory", "raw_content_visible", "calendar_todo_digest_create", "write_todo_digest_schedule", "toggle_item", "task", "category", "history"}
FORBIDDEN = ("telegram_chat_id", "api_token", "raw_note", "raw_chat", "private_text")
SEMANTIC_MARKERS = {
    "src/tool_domains/personal_workspace.py": (b"items[index]",),
    "src/request_models.py": (b"'task'",), "src/memory.py": (b"def add_entry", b"category"),
    "src/task_scheduler.py": (b"todo_digest",), "plugins/telegram/plugin.py": (b"gate_telegram_reply_text(text, repo_root=Path.cwd())",),
}
HOTFILE_DEFINITIONS = {
    "core/database.py": ("notes_data_model", "notes_read_write_model", "defer", "TTD-01"),
    "routes/note_routes.py": ("notes_route_writer", "notes_read_write_route", "defer", "TTD-01"),
    "src/tool_domains/personal_workspace.py": ("notes_agent_writer", "index_toggle_mutation", "defer", "TTD-01"),
    "src/builtin_actions.py": ("digest_projection", "notes_read_only_projection", "defer", "TTD-05"),
    "src/agent_loop_prompts.py": ("prompt_advisory", "advisory_no_enforcement", "defer", "TTD-02"),
    "src/request_models.py": ("memory_task_category", "request_category_writer", "defer", "TTD-02"),
    "src/memory.py": ("memory_writer", "arbitrary_category_persist", "defer", "TTD-02"),
    "src/ai_interaction.py": ("memory_agent_writer", "memory_mutation", "defer", "TTD-02"),
    "src/calendar_capability_service.py": ("digest_schedule", "schedule_write", "defer", "TTD-05"),
    "src/task_scheduler.py": ("digest_executor", "dispatch_and_delivery", "defer", "TTD-05"),
    "src/task_scheduler_delivery.py": ("task_delivery", "delivery_writer", "defer", "TTD-05"),
    "plugins/telegram/control_service.py": ("telegram_control", "intent_to_schedule_write", "defer", "TTD-04"),
    "plugins/telegram/parsing.py": ("telegram_intent", "command_parse", "defer", "TTD-04"),
    "plugins/telegram/plugin.py": ("telegram_pre_send", "gated_transport", "defer", "TTD-04"),
    "plugins/telegram/stores.py": ("telegram_history", "history_read_write", "defer", "TTD-07/08"),
    "src/telegram_truth_runtime.py": ("chat_boundary", "redacted_audit", "defer", "TTD-08"),
    "src/telegram_truth_gate.py": ("claim_gate", "semantic_claim_gate", "defer", "TTD-04"),
    "src/claim_evidence_gate.py": ("evidence_gate", "semantic_evidence_gate", "defer", "TTD-03"),
    "src/tool_transaction_ledger.py": ("transaction_gate", "transaction_receipt_boundary", "defer", "TTD-03"),
}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _symbols(source: bytes, path: str) -> set[str]:
    tree = ast.parse(source, filename=path)
    return {node.name for node in ast.walk(tree) if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))}


def build_contract(root: Path) -> dict[str, Any]:
    root = root.resolve()
    requirements = {path: set(markers) for _, _, _, path, markers in ROLE_DEFINITIONS}
    for path, markers in EXTRA_SOURCES.items():
        requirements.setdefault(path, set()).update(markers)
    sources = []
    for path in sorted(requirements):
        data = (root / path).read_bytes()
        if any(marker not in data for marker in SEMANTIC_MARKERS.get(path, ())):
            raise ValueError(f"decision-required semantic drift in {path}")
        if path in {"src/claim_evidence_gate.py", "src/tool_transaction_ledger.py", "src/telegram_truth_gate.py"} and any(marker in data for marker in ABSENT_TODO_CLAIMS):
            raise ValueError(f"decision-required todo-claim drift in {path}")
        symbols = _symbols(data, path)
        markers = sorted(requirements[path])
        missing = [marker for marker in markers if (marker not in symbols if marker not in LITERAL_MARKERS else marker.encode() not in data)]
        if missing:
            raise ValueError(f"static symbol drift in {path}: {','.join(missing)}")
        sources.append({"path": path, "sha256": _sha(data), "symbols": markers})
    roles = []
    for name, role, current_state, path, markers in ROLE_DEFINITIONS:
        roles.append({"domain": name, "role": role, "current_state": current_state, "source_path": path, "symbols": list(markers)})
    return {
        "schema": SCHEMA,
        "roles": roles,
        "sources": sources,
        "identity_contract": {
            "owner_ref": "opaque_future_owner_ref", "current_owner_scope": "Note.owner nullable_raw", "list_ref": "opaque_future_list_ref", "item_ref": "opaque_future_item_ref",
            "idempotency_key": "opaque_future_idempotency_key", "current_note_identity": "stable Note.id",
            "current_item_identity": "index_based_legacy_no_stable_item_id", "current_mutation": "toggle_by_index",
        },
        "gaps": ["memory_task_shaped_write_risk", "plugin_pre_send_omits_tool_events", "todo_claim_types_and_postconditions_absent"],
        "receipt_schema": ["list_ref", "item_ref", "operation", "previous_state", "current_state", "open_count", "transaction_status", "verified", "evidence_refs_redacted"],
        "operations": ["list", "add", "complete", "reopen", "remove"],
        "compatibility": {"backward_read": "legacy_notes_readable_without_mutation", "migration_write": False, "rollback": "contract_artifact_only_no_notes_rewrite"},
        "hotfiles": [{"path": path, "concern": concern, "access_mode": mode, "decision": decision, "owner_track": track, "active_claim_state": "none_at_capture"} for path, (concern, mode, decision, track) in sorted(HOTFILE_DEFINITIONS.items())],
        "capabilities": {key: False for key in ("atomic_mutation", "stable_item_id", "owner_safe_item_mutation", "todo_receipt", "digest_postcondition", "telegram_tool_event_propagation", "memory_todo_enforcement", "runtime", "provider_access", "production_data_read", "live_telegram", "environment_read")},
    }


def _render(value: dict[str, Any]) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n"


def validate_contract(value: dict[str, Any], root: Path | None = None) -> list[str]:
    errors: list[str] = []
    expected = build_contract(root or Path(__file__).resolve().parents[1])
    if value.get("schema") != SCHEMA or set(value) != set(expected):
        errors.append("unknown or missing top-level field")
    roles = value.get("roles")
    if roles != expected["roles"]:
        errors.append("roles must be present exactly once in canonical order")
    if value.get("identity_contract") != expected["identity_contract"] or value.get("gaps") != expected["gaps"] or value.get("receipt_schema") != expected["receipt_schema"] or value.get("operations") != expected["operations"] or value.get("compatibility") != expected["compatibility"]:
        errors.append("contradictory identity, gap, receipt, or compatibility claim")
    if value.get("sources") != expected["sources"] or value.get("hotfiles") != expected["hotfiles"]:
        errors.append("source hash, symbol, or hotfile drift")
    if value.get("capabilities") != expected["capabilities"]:
        errors.append("capabilities must remain false")
    serialized = _render(value).lower()
    if any(token in serialized for token in FORBIDDEN) or "\\\\" in serialized or "://" in serialized or "\\\\users\\" in serialized or '"/home/' in serialized or '"/etc/' in serialized:
        errors.append("unsafe private or host-shaped content")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--print", action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    value = build_contract(args.root)
    errors = validate_contract(value, args.root)
    if errors:
        raise SystemExit("; ".join(errors))
    rendered = _render(value)
    target = args.root / "docs/plans/telegram-todo-domain-truth-contract.json"
    if args.check:
        try:
            committed = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(f"telegram todo domain truth contract is unreadable: {exc}") from exc
        committed_errors = validate_contract(committed, args.root)
        if committed_errors or target.read_text(encoding="utf-8") != rendered:
            raise SystemExit("telegram todo domain truth contract is missing, invalid, or stale")
    if args.print or not args.check:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
