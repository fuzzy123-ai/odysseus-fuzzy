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
    "create_document": "document_write",
    "update_document": "document_write",
    "edit_document": "document_write",
    "manage_personal_docs": "document_source_control",
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


def effectful_tool_names() -> tuple[str, ...]:
    return tuple(sorted(EFFECTFUL_TOOL_MATRIX))


def tool_effect_category(tool: Any) -> str:
    return EFFECTFUL_TOOL_MATRIX.get(str(tool or "").strip(), "")


def build_effectful_action_snapshot(tool_events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    events = tuple(event for event in tool_events if isinstance(event, Mapping))
    transactions = tuple(tx.to_dict() for tx in transactions_from_tool_events(events, surface="agent"))
    categories = tuple(
        sorted(
            {
                category
                for event in events
                for category in (tool_effect_category(event.get("tool")),)
                if category
            }
        )
    )
    return {
        "schema": EFFECTFUL_TOOL_MATRIX_SCHEMA,
        "effectful_count": sum(1 for event in events if tool_effect_category(event.get("tool"))),
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
