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
    "manage_plugins": "plugin_state",
    "manage_settings": "settings_write",
    "manage_tokens": "token_state",
    "manage_repos": "repo_registry_write",
    "download_model": "model_runtime",
    "serve_model": "model_runtime",
    "serve_preset": "model_runtime",
    "stop_served_model": "model_runtime",
    "cancel_download": "model_runtime",
    "adopt_served_model": "model_runtime",
    "manage_todos": "todo_state",
}


# Mixed read/mutation tools remain conservatively effectful when the action is
# absent or unknown, but explicit inspection actions do not create a false
# mutation receipt.  Callers should pass a normalized, content-free action
# field rather than parsing or retaining raw tool arguments here.
EFFECTFUL_TOOL_ACTION_MATRIX: dict[str, dict[str, str]] = {
    "manage_plugins": {
        "list": "",
        "get": "",
        "status": "",
        "*": "plugin_state",
    },
    "manage_settings": {
        "list": "",
        "get": "",
        "status": "",
        "*": "settings_write",
    },
    "manage_tokens": {
        "list": "",
        "get": "",
        "status": "",
        "*": "token_state",
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
        "*": "repo_registry_write",
    },
    "manage_todos": {
        "list": "",
        "*": "todo_state",
    },
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


def build_effectful_action_snapshot(tool_events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    events = tuple(event for event in tool_events if isinstance(event, Mapping))
    transactions = tuple(tx.to_dict() for tx in transactions_from_tool_events(events, surface="agent"))
    categories = tuple(
        sorted(
            {
                category
                for event in events
                for category in (
                    tool_effect_category(event.get("tool"), event.get("action")),
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
            if tool_effect_category(event.get("tool"), event.get("action"))
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
