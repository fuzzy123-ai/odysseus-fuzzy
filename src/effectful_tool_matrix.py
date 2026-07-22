"""Central matrix of tool effects used by completion verification."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from src.tool_transaction_ledger import transactions_from_tool_events


EFFECTFUL_TOOL_MATRIX_SCHEMA = "odysseus.effectful_tool_matrix.v1"

EFFECTFUL_TOOL_MATRIX: dict[str, str] = {
    "bash": "local_command",
    "python": "local_command",
    "write_file": "filesystem_write",
    "publish_artifact": "artifact_publication",
    "verify_pygame_headless": "headless_validation",
    "commit_project": "project_versioning",
    "create_document": "document_write",
    "update_document": "document_write",
    "edit_document": "document_write",
    "manage_personal_docs": "document_source_control",
    "manage_todos": "todo_domain_transaction",
    "manage_embeddings": "embedding_runtime_control",
    "manage_plugins": "plugin_supply_chain_control",
    "manage_settings": "settings_control",
    "manage_tokens": "credential_control",
    "manage_repos": "repository_state_control",
    "manage_endpoints": "model_endpoint_control",
    "download_model": "model_runtime_control",
    "serve_model": "model_runtime_control",
    "serve_preset": "model_runtime_control",
    "stop_served_model": "model_runtime_control",
    "cancel_download": "model_runtime_control",
    "adopt_served_model": "model_runtime_control",
    "sandbox_submit": "sandbox_worker",
    "sandbox_status": "sandbox_worker",
    "dispatch_sandbox_checks": "sandbox_worker",
    "coding_agent_sandbox_bridge": "sandbox_worker",
    "telegram_reply": "telegram_outbound",
    "telegram_text_reply": "telegram_outbound",
    "telegram_document_reply": "telegram_outbound",
    "telegram_photo": "telegram_outbound",
    "send_telegram_photo": "telegram_outbound",
    "browser": "browser_automation",
    "browser_screenshot": "browser_automation",
    "playwright": "browser_automation",
    "playwright_screenshot": "browser_automation",
    "quality_gate": "completion_gate",
    "coding_agent_done": "completion_gate",
    "sandbox_checks": "completion_gate",
    "git_commit": "git_remote_state",
    "git_push": "git_remote_state",
}


EFFECTFUL_TOOL_ACTION_MATRIX: dict[str, dict[str, str]] = {
    "manage_plugins": {
        "list": "",
        "get": "",
        "status": "",
        "*": "plugin_supply_chain_control",
    },
    "manage_settings": {
        "list": "",
        "get": "",
        "status": "",
        "*": "settings_control",
    },
    "manage_tokens": {
        "list": "",
        "get": "",
        "status": "",
        "*": "credential_control",
    },
    "manage_repos": {
        "list": "",
        "get": "",
        "status": "",
        "log": "",
        "diff_stat": "",
        "changed_paths": "",
        "remotes": "",
        "commit_plan": "",
        "push_plan": "",
        "forge_plan": "",
        "changes": "",
        "change_history": "",
        "*": "repository_state_control",
    },
}

_MUTATION_SIGNAL_KEYS = {
    "transaction_id",
    "transaction_status",
    "mutation",
    "mutated",
    "write",
    "writes",
    "changed",
    "side_effect",
    "effectful",
    "committed",
    "pushed",
}


def effectful_tool_names() -> tuple[str, ...]:
    return tuple(sorted(EFFECTFUL_TOOL_MATRIX))


def tool_effect_category(tool: Any, action: Any = None) -> str:
    tool_name = str(tool or "").strip()
    action_map = EFFECTFUL_TOOL_ACTION_MATRIX.get(tool_name)
    if action_map is not None and action is not None:
        action_name = str(action or "").strip().lower()
        return action_map.get(action_name, action_map.get("*", ""))
    return EFFECTFUL_TOOL_MATRIX.get(tool_name, "")


def _event_effect_category(event: Mapping[str, Any]) -> str:
    tool = event.get("tool")
    action = event.get("action")
    if not isinstance(tool, str) or not isinstance(action, str):
        return EFFECTFUL_TOOL_MATRIX.get(str(tool or "").strip(), "")
    tool_name = tool.strip()
    action_name = action.strip().lower()
    action_map = EFFECTFUL_TOOL_ACTION_MATRIX.get(tool_name)
    if action_map is None:
        return EFFECTFUL_TOOL_MATRIX.get(tool_name, "")
    if action_name not in action_map or action_name == "*":
        return action_map.get("*", EFFECTFUL_TOOL_MATRIX.get(tool_name, ""))
    category = action_map[action_name]
    if category:
        return category
    if _read_only_event_has_conflicting_metadata(event):
        return action_map.get("*", EFFECTFUL_TOOL_MATRIX.get(tool_name, ""))
    return ""


def _read_only_event_has_conflicting_metadata(event: Mapping[str, Any]) -> bool:
    for key in _MUTATION_SIGNAL_KEYS:
        if key not in event:
            continue
        value = event.get(key)
        if value not in (None, False, "", (), [], {}):
            return True
    command = str(event.get("command") or "").strip().lower()
    if command and any(
        token in command
        for token in (
            " commit",
            " push",
            " delete",
            " remove",
            " update",
            " create",
            " write",
            " mutate",
        )
    ):
        return True
    return False


def build_effectful_action_snapshot(tool_events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    events = tuple(event for event in tool_events if isinstance(event, Mapping))
    transactions = tuple(tx.to_dict() for tx in transactions_from_tool_events(events, surface="agent"))
    categories = tuple(
        sorted(
            {
                category
                for event in events
                for category in (
                    _event_effect_category(event),
                )
                if category
            }
        )
    )
    return {
        "schema": EFFECTFUL_TOOL_MATRIX_SCHEMA,
        "effectful_count": sum(
            1
            for event in events
            if _event_effect_category(event)
        ),
        "categories": categories,
        "transactions": transactions,
        "transaction_status": tuple(
            {
                "transaction_id": tx["transaction_id"],
                "tool": tx["tool"],
                "claim_type": tx["claim_type"],
                "status": tx["status"],
                "verified_done": tx["verified_done"],
                "artifact_refs": tx["artifact_refs"],
                "evidence_refs": tx["evidence_refs"],
            }
            for tx in transactions
        ),
        "raw_content_visible": False,
    }
