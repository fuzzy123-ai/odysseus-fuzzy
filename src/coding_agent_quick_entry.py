"""Redacted coding-agent capability summaries for chat entry points."""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping


CODING_AGENT_QUICK_ENTRY_SCHEMA = "odysseus.coding_agent_quick_entry.v1"

READ_ONLY_PREVIEW = "read_only_preview"
GATED_MUTATION = "gated_mutation"
LIVE_ACTION = "live_action"

CODING_AGENT_ACTIONS: tuple[dict[str, Any], ...] = (
    {"id": "project_list", "label": "Project registry summary", "method": "GET", "path": "/api/projects", "action_class": READ_ONLY_PREVIEW, "surface": "projects"},
    {"id": "repo_snapshot", "label": "Repository snapshot", "method": "GET", "path": "/api/coding-agent/repos/{repo_id}/snapshot", "action_class": READ_ONLY_PREVIEW, "surface": "coding_agent"},
    {"id": "task_plan", "label": "Task plan preview", "method": "POST", "path": "/api/coding-agent/repos/{repo_id}/task-plan", "action_class": READ_ONLY_PREVIEW, "surface": "coding_agent"},
    {"id": "handoff_plan", "label": "Handoff plan preview", "method": "POST", "path": "/api/coding-agent/repos/{repo_id}/handoff-plan", "action_class": READ_ONLY_PREVIEW, "surface": "coding_agent"},
    {"id": "publish_plan", "label": "Publish plan preview", "method": "POST", "path": "/api/coding-agent/repos/{repo_id}/publish-plan", "action_class": READ_ONLY_PREVIEW, "surface": "coding_agent"},
    {"id": "subagents_plan", "label": "Subagent plan preview", "method": "POST", "path": "/api/coding-agent/repos/{repo_id}/subagents-plan", "action_class": READ_ONLY_PREVIEW, "surface": "coding_agent"},
    {"id": "sandbox_status", "label": "Sandbox job status", "method": "GET", "path": "/api/sandbox-worker/status/{job_id}", "action_class": READ_ONLY_PREVIEW, "surface": "sandbox_worker"},
    {"id": "sandbox_artifacts", "label": "Sandbox artifact metadata", "method": "GET", "path": "/api/sandbox-worker/artifacts/{job_id}", "action_class": READ_ONLY_PREVIEW, "surface": "sandbox_worker"},
    {"id": "create_project", "label": "Create project registry entry", "method": "POST", "path": "/api/projects", "action_class": GATED_MUTATION, "surface": "projects", "operator_decision_required": True},
    {"id": "intake_apply", "label": "Apply project intake proposal", "method": "POST", "path": "/api/projects/{project_slug}/intake/apply", "action_class": GATED_MUTATION, "surface": "projects", "operator_decision_required": True},
    {"id": "intake_merge", "label": "Merge intake ledger into project state", "method": "POST", "path": "/api/projects/{project_slug}/intake/merge", "action_class": GATED_MUTATION, "surface": "projects", "operator_decision_required": True},
    {"id": "chat_bind", "label": "Bind chat session to project", "method": "POST", "path": "/api/projects/{project_slug}/chat-bind", "action_class": GATED_MUTATION, "surface": "projects", "operator_decision_required": True},
    {"id": "worktree", "label": "Create coding worktree", "method": "POST", "path": "/api/coding-agent/repos/{repo_id}/worktree", "action_class": GATED_MUTATION, "surface": "coding_agent", "operator_decision_required": True, "repo_mutation_possible": True},
    {"id": "patch_set", "label": "Apply patch set", "method": "POST", "path": "/api/coding-agent/repos/{repo_id}/patch-set", "action_class": GATED_MUTATION, "surface": "coding_agent", "operator_decision_required": True, "repo_mutation_possible": True},
    {"id": "quality_gate", "label": "Evaluate quality gate", "method": "POST", "path": "/api/coding-agent/quality-gate", "action_class": GATED_MUTATION, "surface": "coding_agent", "operator_decision_required": True},
    {"id": "done_gate", "label": "Evaluate done gate", "method": "POST", "path": "/api/coding-agent/done-gate", "action_class": GATED_MUTATION, "surface": "coding_agent", "operator_decision_required": True},
    {"id": "project_workspace_provision", "label": "Provision project workspace", "method": "POST", "path": "/api/projects/{project_slug}/provision", "action_class": LIVE_ACTION, "surface": "projects", "live_go_required": True, "host_write_possible": True},
    {"id": "project_repo_provision", "label": "Provision local project repository", "method": "POST", "path": "/api/projects/{project_slug}/repo-provision", "action_class": LIVE_ACTION, "surface": "projects", "live_go_required": True, "host_write_possible": True, "repo_mutation_possible": True},
    {"id": "project_task_run", "label": "Run project task", "method": "POST", "path": "/api/projects/{project_slug}/task-run", "action_class": LIVE_ACTION, "surface": "projects", "live_go_required": True, "host_write_possible": True, "repo_mutation_possible": True},
    {"id": "project_planner_task_run", "label": "Run planner task", "method": "POST", "path": "/api/projects/{project_slug}/planner-task-run", "action_class": LIVE_ACTION, "surface": "projects", "live_go_required": True, "host_write_possible": True, "repo_mutation_possible": True},
    {"id": "project_commit_run", "label": "Commit project changes", "method": "POST", "path": "/api/projects/{project_slug}/commit-run", "action_class": LIVE_ACTION, "surface": "projects", "live_go_required": True, "repo_mutation_possible": True},
    {"id": "project_push_run", "label": "Push project changes", "method": "POST", "path": "/api/projects/{project_slug}/push-run", "action_class": LIVE_ACTION, "surface": "projects", "live_go_required": True, "network_mutation_possible": True, "repo_mutation_possible": True},
    {"id": "sandbox_checks", "label": "Dispatch coding checks to sandbox", "method": "POST", "path": "/api/coding-agent/repos/{repo_id}/sandbox-checks", "action_class": LIVE_ACTION, "surface": "coding_agent", "live_go_required": True, "sandbox_execution_possible": True},
    {"id": "sandbox_submit", "label": "Submit sandbox job", "method": "POST", "path": "/api/sandbox-worker/submit", "action_class": LIVE_ACTION, "surface": "sandbox_worker", "live_go_required": True, "sandbox_execution_possible": True},
    {"id": "sandbox_cancel", "label": "Cancel sandbox job", "method": "POST", "path": "/api/sandbox-worker/cancel/{job_id}", "action_class": GATED_MUTATION, "surface": "sandbox_worker", "operator_decision_required": True},
)


def build_coding_agent_quick_entry() -> dict[str, Any]:
    actions = [_safe_action(item) for item in CODING_AGENT_ACTIONS]
    counts = Counter(action["action_class"] for action in actions)
    return {
        "schema": CODING_AGENT_QUICK_ENTRY_SCHEMA,
        "status": "available",
        "summary": {
            "action_count": len(actions),
            "read_only_preview_count": counts[READ_ONLY_PREVIEW],
            "gated_mutation_count": counts[GATED_MUTATION],
            "live_action_count": counts[LIVE_ACTION],
            "default_recommended_action_class": READ_ONLY_PREVIEW,
        },
        "classes": {
            READ_ONLY_PREVIEW: {
                "can_render_in_chat": True,
                "requires_operator_decision": False,
                "requires_live_go": False,
                "execution_performed_by_quick_entry": False,
            },
            GATED_MUTATION: {
                "can_render_in_chat": True,
                "requires_operator_decision": True,
                "requires_live_go": False,
                "execution_performed_by_quick_entry": False,
            },
            LIVE_ACTION: {
                "can_render_in_chat": True,
                "requires_operator_decision": True,
                "requires_live_go": True,
                "execution_performed_by_quick_entry": False,
            },
        },
        "actions": actions,
        "safety": {
            "execution_performed": False,
            "sandbox_started": False,
            "repo_mutation_performed": False,
            "host_write_performed": False,
            "network_mutation_performed": False,
            "deploy_performed": False,
            "raw_content_visible": False,
            "host_paths_visible": False,
            "sensitive_values_visible": False,
            "chat_ids_visible": False,
            "sensitive_content_visible": False,
        },
    }


def _safe_action(action: Mapping[str, Any]) -> dict[str, Any]:
    action_class = _safe_class(action.get("action_class"))
    return {
        "id": _safe_token(action.get("id")),
        "label": _safe_label(action.get("label")),
        "method": _safe_method(action.get("method")),
        "path": _safe_path(action.get("path")),
        "surface": _safe_token(action.get("surface")),
        "action_class": action_class,
        "operator_decision_required": bool(action.get("operator_decision_required") or action_class == LIVE_ACTION),
        "live_go_required": bool(action.get("live_go_required") or action_class == LIVE_ACTION),
        "repo_mutation_possible": bool(action.get("repo_mutation_possible")),
        "host_write_possible": bool(action.get("host_write_possible")),
        "network_mutation_possible": bool(action.get("network_mutation_possible")),
        "sandbox_execution_possible": bool(action.get("sandbox_execution_possible")),
        "raw_values_visible": False,
    }


def _safe_class(value: Any) -> str:
    token = _safe_token(value)
    return token if token in {READ_ONLY_PREVIEW, GATED_MUTATION, LIVE_ACTION} else GATED_MUTATION


def _safe_method(value: Any) -> str:
    token = _safe_token(value).upper()
    return token if token in {"GET", "POST", "PUT", "PATCH", "DELETE"} else "POST"


def _safe_path(value: Any) -> str:
    path = str(value or "").strip()
    if not path.startswith("/api/"):
        return "/api/redacted"
    return "".join(ch for ch in path if ch.isalnum() or ch in "/{}_-")[:180]


def _safe_label(value: Any) -> str:
    return "".join(ch for ch in str(value or "").strip() if ch.isalnum() or ch in " -_/()")[:120]


def _safe_token(value: Any) -> str:
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum() or ch in "._:-")[:80]
