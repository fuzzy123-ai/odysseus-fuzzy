"""Repo-only planning model for server-side Odysseus project execution."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.claim_evidence_gate import (
    AgentMaintenanceCompletionEvidence,
    evaluate_agent_maintenance_completion,
)
from src.live_quality_gate_command_runner import quality_gate_command_text_is_allowed

_ALLOWED_REMOTES = ("fuzzy",)
_DECISIONS = ("blocked", "plan_ready", "ready_for_operator_go", "hold")
_OPERATOR_DECISIONS = ("go", "hold", "no_go", "missing")
_PROJECT_MODES = ("backend_logic", "full_stack_after_ui_go")
_DEPLOY_TARGETS = ("homeserver_podman", "dry_run_only")
_PROJECT_TYPES = ("software", "website", "app", "game", "research", "generic")
_SECRET_RE = re.compile(
    r"(?i)\b(token|secret|password|passwd|api[_-]?key|chat[_-]?id|bearer)\b\s*[:=]?\s*\S*"
)
_PATH_RE = re.compile(r"(?:[A-Za-z]:\\[^\s`]+|(?<![A-Za-z0-9._-])/(?:[^\s/`]+/)*[^\s`]+)")
_SLUG_RE = re.compile(r"[^a-z0-9._-]+")


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _normalize_choice(value: Any, *, field_name: str, choices: tuple[str, ...]) -> str:
    text = _normalize_text(value, field_name=field_name).lower().replace("-", "_")
    if text not in choices:
        raise ValueError(f"unsupported {field_name}: {value!r}")
    return text


def _dedupe(values: Iterable[Any], *, field_name: str, allow_empty: bool = True) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        item = _normalize_text(value, field_name=field_name, allow_empty=True)
        if item and item not in result:
            result.append(item)
    if not result and not allow_empty:
        raise ValueError(f"{field_name} must not be empty")
    return tuple(result)


def _redact(value: Any) -> str:
    text = _normalize_text(value, field_name="redacted_text", allow_empty=True)
    text = _SECRET_RE.sub("[redacted-secret]", text)
    text = _PATH_RE.sub("[redacted-path]", text)
    return text


def _slugify(value: Any) -> str:
    text = _normalize_text(value, field_name="project_title").lower()
    text = _SLUG_RE.sub("-", text)
    text = "-".join(part for part in text.strip("-._").split("-") if part)
    if not text:
        raise ValueError("project_title must produce a non-empty slug")
    return text[:80]


@dataclass(frozen=True, slots=True)
class UniversalProjectSpec:
    project_title: str
    project_slug: str
    project_type: str
    repo_name: str
    workspace_root: str
    chat_scope: str
    default_branch: str
    cloudflare_tunnel_requested: bool
    cloudflare_tunnel_gate: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_title": self.project_title,
            "project_slug": self.project_slug,
            "project_type": self.project_type,
            "repo_name": self.repo_name,
            "workspace_root": self.workspace_root,
            "chat_scope": self.chat_scope,
            "default_branch": self.default_branch,
            "cloudflare_tunnel_requested": self.cloudflare_tunnel_requested,
            "cloudflare_tunnel_gate": self.cloudflare_tunnel_gate,
        }


@dataclass(frozen=True, slots=True)
class ServerProjectRunnerPlan:
    project_spec: UniversalProjectSpec
    project_id: str
    mode: str
    deploy_target: str
    push_remote: str
    base_branch: str
    worker_branch: str
    quality_gate_commands: tuple[str, ...]
    backup_evidence_green: bool
    smoke_target: str
    rollback_plan: str
    operator_decision: str
    live_go: bool
    decision: str
    blockers: tuple[str, ...]
    planned_steps: tuple[Mapping[str, Any], ...]
    operator_gate: Mapping[str, Any]
    evidence_summary: Mapping[str, Any]
    next_human_decision: str

    @property
    def operator_live_go_ready(self) -> bool:
        return self.decision == "ready_for_operator_go" and self.live_go and self.operator_decision == "go"

    @property
    def live_execution_allowed(self) -> bool:
        """Legacy compatibility property; plans never grant execution authority."""

        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_spec": self.project_spec.to_dict(),
            "project_id": self.project_id,
            "mode": self.mode,
            "deploy_target": self.deploy_target,
            "push_remote": self.push_remote,
            "base_branch": self.base_branch,
            "worker_branch": self.worker_branch,
            "quality_gate_commands": list(self.quality_gate_commands),
            "backup_evidence_green": self.backup_evidence_green,
            "smoke_target": self.smoke_target,
            "rollback_plan": self.rollback_plan,
            "operator_decision": self.operator_decision,
            "live_go": self.live_go,
            "operator_live_go_ready": self.operator_live_go_ready,
            "live_execution_allowed": False,
            "decision": self.decision,
            "blockers": list(self.blockers),
            "planned_steps": [dict(step) for step in self.planned_steps],
            "operator_gate": dict(self.operator_gate),
            "evidence_summary": dict(self.evidence_summary),
            "next_human_decision": self.next_human_decision,
            "raw_content_visible": False,
        }

    def to_markdown(self) -> str:
        lines = [
            "# Server Project Runner Plan",
            "",
            f"- Project: `{self.project_id}`",
            f"- Repo: `{self.project_spec.repo_name}`",
            f"- Chat scope: `{self.project_spec.chat_scope}`",
            f"- Decision: `{self.decision}`",
            f"- Remote: `{self.push_remote}`",
            f"- Operator live Go ready: `{str(self.operator_live_go_ready).lower()}`",
            "- Live execution allowed: `false` (requires separate execution decision)",
        ]
        if self.blockers:
            lines.extend(["", "## Blockers"])
            lines.extend(f"- {blocker}" for blocker in self.blockers)
        lines.extend(["", "## Planned Steps"])
        for step in self.planned_steps:
            lines.append(f"- `{step['step_id']}`: {step['summary']}")
        lines.extend(["", "## Recommended Next Human Decision", self.next_human_decision])
        return "\n".join(lines).rstrip()


@dataclass(frozen=True, slots=True)
class ServerProjectExecutionAuthority:
    action: str
    plan_digest: str
    granted: bool


@dataclass(frozen=True, slots=True)
class ServerProjectExecutionDecision:
    allowed: bool
    completion_verified: bool
    action_authorized: bool
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "odysseus.server_project_execution_gate.v1",
            "allowed": self.allowed,
            "completion_verified": self.completion_verified,
            "action_authorized": self.action_authorized,
            "blockers": list(self.blockers),
        }


def build_server_project_execution_authority(
    plan: ServerProjectRunnerPlan,
    *,
    granted: bool,
) -> ServerProjectExecutionAuthority:
    if type(granted) is not bool:
        raise ValueError("granted must be a boolean")
    if not _server_project_plan_is_valid(plan):
        raise ValueError("server project plan is not a canonical builder-valid plan")
    return ServerProjectExecutionAuthority(
        action="server_project_execution",
        plan_digest=_server_project_plan_digest(plan),
        granted=granted,
    )


def evaluate_server_project_execution(
    plan: ServerProjectRunnerPlan,
    *,
    repo_root: str | Path,
    completion_evidence: AgentMaintenanceCompletionEvidence | None,
    authority: ServerProjectExecutionAuthority | None,
) -> ServerProjectExecutionDecision:
    if not isinstance(plan, ServerProjectRunnerPlan):
        raise ValueError("plan must be a ServerProjectRunnerPlan")
    plan_valid = _server_project_plan_is_valid(plan)
    completion_verified = evaluate_agent_maintenance_completion(
        completion_evidence,
        repo_root=Path(repo_root).resolve(),
    ).completed
    action_authorized = (
        isinstance(authority, ServerProjectExecutionAuthority)
        and authority.action == "server_project_execution"
        and plan_valid
        and authority.plan_digest == _server_project_plan_digest(plan)
        and authority.granted is True
    )
    blockers: list[str] = []
    if not plan_valid:
        blockers.append("server project plan is not canonical or builder-valid")
    if not plan.operator_live_go_ready:
        blockers.append("server project plan lacks explicit operator live Go")
    if not completion_verified:
        blockers.append("current claims and machine verification receipt are required")
    if not action_authorized:
        blockers.append("typed server project execution authority is required")
    unique = tuple(dict.fromkeys(blockers))
    return ServerProjectExecutionDecision(
        allowed=not unique,
        completion_verified=completion_verified,
        action_authorized=action_authorized,
        blockers=unique,
    )


def _server_project_plan_material(plan: ServerProjectRunnerPlan) -> dict[str, Any]:
    return {
        "project_spec": plan.project_spec.to_dict(),
        "project_id": plan.project_id,
        "mode": plan.mode,
        "deploy_target": plan.deploy_target,
        "push_remote": plan.push_remote,
        "base_branch": plan.base_branch,
        "worker_branch": plan.worker_branch,
        "quality_gate_commands": list(plan.quality_gate_commands),
        "backup_evidence_green": plan.backup_evidence_green,
        "smoke_target": plan.smoke_target,
        "rollback_plan": plan.rollback_plan,
        "operator_decision": plan.operator_decision,
        "live_go": plan.live_go,
        "decision": plan.decision,
        "blockers": list(plan.blockers),
        "planned_steps": [dict(step) for step in plan.planned_steps],
        "operator_gate": dict(plan.operator_gate),
        "evidence_summary": dict(plan.evidence_summary),
        "next_human_decision": plan.next_human_decision,
    }


def _server_project_plan_digest(plan: ServerProjectRunnerPlan) -> str:
    encoded = json.dumps(
        _server_project_plan_material(plan),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _server_project_plan_is_valid(plan: Any) -> bool:
    if not isinstance(plan, ServerProjectRunnerPlan):
        return False
    try:
        ui_scope_requested = any(
            "UI scope is excluded" in blocker for blocker in plan.blockers
        )
        rebuilt = build_server_project_runner_plan(
            project_title=plan.project_spec.project_title,
            project_type=plan.project_spec.project_type,
            repo_name=plan.project_spec.repo_name,
            workspace_root=plan.project_spec.workspace_root,
            chat_scope=plan.project_spec.chat_scope,
            cloudflare_tunnel_requested=plan.project_spec.cloudflare_tunnel_requested,
            project_id=plan.project_id,
            mode=plan.mode,
            deploy_target=plan.deploy_target,
            push_remote=plan.push_remote,
            base_branch=plan.base_branch,
            worker_branch=plan.worker_branch,
            quality_gate_commands=plan.quality_gate_commands,
            backup_evidence_green=plan.backup_evidence_green,
            smoke_target=plan.smoke_target,
            rollback_plan=plan.rollback_plan,
            operator_decision=plan.operator_decision,
            live_go=plan.live_go,
            ui_scope_requested=ui_scope_requested,
        )
        return _server_project_plan_material(rebuilt) == _server_project_plan_material(plan)
    except (AttributeError, TypeError, ValueError):
        return False


def _build_steps(
    *,
    project_spec: UniversalProjectSpec,
    push_remote: str,
    base_branch: str,
    worker_branch: str,
    quality_gate_commands: tuple[str, ...],
    deploy_target: str,
    smoke_target: str,
) -> tuple[Mapping[str, Any], ...]:
    steps: list[Mapping[str, Any]] = [
        {
            "step_id": "project_intake",
            "summary": f"create or load universal project record for {project_spec.project_slug}",
            "executes": False,
        },
        {
            "step_id": "project_chat_scope",
            "summary": f"bind AI chat context to project scope {project_spec.chat_scope}",
            "executes": False,
        },
        {
            "step_id": "repo_creation_plan",
            "summary": f"plan repo creation or attachment for {project_spec.repo_name}",
            "executes": False,
        },
        {
            "step_id": "workspace_preflight",
            "summary": f"verify clean worktree, allowed paths, and workspace {project_spec.workspace_root}",
            "executes": False,
        },
        {
            "step_id": "git_remote_gate",
            "summary": f"require push remote {push_remote} and base branch {base_branch}",
            "executes": False,
        },
        {
            "step_id": "branch_plan",
            "summary": f"prepare isolated worker branch {worker_branch} from {push_remote}/{base_branch}",
            "executes": False,
        },
    ]
    for index, command in enumerate(quality_gate_commands, start=1):
        steps.append(
            {
                "step_id": f"quality_gate_{index}",
                "summary": f"review focused quality gate command: {_redact(command)}",
                "executes": False,
            }
        )
    steps.extend(
        (
            {
                "step_id": "backup_gate",
                "summary": "require pre-update snapshot, repository check, and restore smoke evidence",
                "executes": False,
            },
            {
                "step_id": "deploy_handoff",
                "summary": f"handoff deployment to operator-gated {deploy_target} flow",
                "executes": False,
            },
            {
                "step_id": "smoke_gate",
                "summary": f"require bounded smoke target {_redact(smoke_target)} after operator deploy",
                "executes": False,
            },
            {
                "step_id": "cloudflare_tunnel_gate",
                "summary": project_spec.cloudflare_tunnel_gate,
                "executes": False,
            },
            {
                "step_id": "rollback_or_hold",
                "summary": "record rollback or hold decision if smoke or health evidence is not green",
                "executes": False,
            },
        )
    )
    return tuple(steps)


def build_server_project_runner_plan(
    *,
    project_title: Any = "Odysseus Server Project Runner",
    project_type: Any = "generic",
    repo_name: Any | None = None,
    workspace_root: Any | None = None,
    chat_scope: Any | None = None,
    cloudflare_tunnel_requested: bool = False,
    project_id: Any = "odysseus-server-project-runner",
    mode: Any = "backend_logic",
    deploy_target: Any = "homeserver_podman",
    push_remote: Any = "fuzzy",
    base_branch: Any = "dev",
    worker_branch: Any = "codex/server-project-runner",
    quality_gate_commands: Iterable[Any] = ("python -m pytest tests/test_server_project_runner.py -q",),
    backup_evidence_green: bool = False,
    smoke_target: Any = "",
    rollback_plan: Any = "",
    operator_decision: Any = "missing",
    live_go: bool = False,
    ui_scope_requested: bool = False,
) -> ServerProjectRunnerPlan:
    cloudflare_requested_value = (
        cloudflare_tunnel_requested if type(cloudflare_tunnel_requested) is bool else False
    )
    backup_green_value = backup_evidence_green if type(backup_evidence_green) is bool else False
    live_go_value = live_go if type(live_go) is bool else False
    ui_scope_value = ui_scope_requested if type(ui_scope_requested) is bool else False
    slug = _slugify(project_title)
    normalized_project_type = _normalize_choice(project_type, field_name="project_type", choices=_PROJECT_TYPES)
    normalized_repo_name = _redact(repo_name if repo_name is not None else slug)
    normalized_workspace_root = _redact(workspace_root if workspace_root is not None else f"projects/{slug}")
    normalized_chat_scope = _redact(chat_scope if chat_scope is not None else f"project:{slug}")
    cloudflare_gate = (
        "Cloudflare Tunnel requested; require separate domain, route, token, healthcheck, and operator Go before exposure"
        if cloudflare_requested_value
        else "Cloudflare Tunnel not requested; keep deployment internal until exposure gate is opened"
    )
    project_spec = UniversalProjectSpec(
        project_title=_redact(project_title),
        project_slug=slug,
        project_type=normalized_project_type,
        repo_name=normalized_repo_name,
        workspace_root=normalized_workspace_root,
        chat_scope=normalized_chat_scope,
        default_branch=_redact(base_branch),
        cloudflare_tunnel_requested=cloudflare_requested_value,
        cloudflare_tunnel_gate=cloudflare_gate,
    )
    normalized_project = _redact(project_id)
    normalized_mode = _normalize_choice(mode, field_name="mode", choices=_PROJECT_MODES)
    normalized_target = _normalize_choice(deploy_target, field_name="deploy_target", choices=_DEPLOY_TARGETS)
    normalized_remote = _normalize_text(push_remote, field_name="push_remote")
    normalized_base = _normalize_text(base_branch, field_name="base_branch")
    normalized_worker = _normalize_text(worker_branch, field_name="worker_branch")
    raw_commands = _dedupe(quality_gate_commands, field_name="quality_gate_command", allow_empty=False)
    normalized_commands = tuple(_redact(command) for command in raw_commands)
    normalized_smoke = _redact(smoke_target)
    normalized_rollback = _redact(rollback_plan)
    normalized_operator = _normalize_choice(
        operator_decision,
        field_name="operator_decision",
        choices=_OPERATOR_DECISIONS,
    )

    blockers: list[str] = []
    for field_name, value in (
        ("cloudflare_tunnel_requested", cloudflare_tunnel_requested),
        ("backup_evidence_green", backup_evidence_green),
        ("live_go", live_go),
        ("ui_scope_requested", ui_scope_requested),
    ):
        if type(value) is not bool:
            blockers.append(f"{field_name} must be a boolean")
    if normalized_remote not in _ALLOWED_REMOTES:
        blockers.append("push remote must be fuzzy; origin is not an allowed deployment remote")
    if not normalized_worker.startswith(("codex/", "project/", "odysseus/")):
        blockers.append("worker branch must use codex/, project/, or odysseus/ prefix")
    if not project_spec.workspace_root.startswith("projects/"):
        blockers.append("workspace root must stay below projects/<project-slug>")
    if not project_spec.chat_scope.startswith("project:"):
        blockers.append("chat scope must be project:<project-slug>")
    if project_spec.repo_name in {"odysseus", "odysseus-fuzzy"}:
        blockers.append("universal project runner must not default to the Odysseus repository")
    if ui_scope_value or normalized_mode != "backend_logic":
        blockers.append("UI scope is excluded until the separate UI redesign gate is opened")
    if any(not quality_gate_command_text_is_allowed(command) for command in raw_commands):
        blockers.append("quality gate commands include blocked, unsupported, or unsafe argv")
    if not backup_green_value:
        blockers.append("backup evidence is not green")
    if not normalized_smoke:
        blockers.append("smoke target is missing")
    if not normalized_rollback:
        blockers.append("rollback or hold plan is missing")
    if live_go_value and normalized_operator != "go":
        blockers.append("live_go requires operator_decision=go")

    blocked_command_requested = any(
        not quality_gate_command_text_is_allowed(command) for command in raw_commands
    )
    if blockers:
        decision = "blocked" if normalized_remote not in _ALLOWED_REMOTES or blocked_command_requested else "hold"
    elif live_go_value and normalized_operator == "go":
        decision = "ready_for_operator_go"
    else:
        decision = "plan_ready"

    next_human_decision = (
        "Decide whether to open P2 for Odysseus-only workspaces or a general server projects workspace schema."
    )
    if decision == "ready_for_operator_go":
        next_human_decision = "Operator may make a separate live Go/No-Go decision for the bounded deployment handoff."
    elif blockers:
        next_human_decision = "Clear the listed gates before any active server-side project execution."

    planned_steps = _build_steps(
        project_spec=project_spec,
        push_remote=normalized_remote,
        base_branch=normalized_base,
        worker_branch=normalized_worker,
        quality_gate_commands=normalized_commands,
        deploy_target=normalized_target,
        smoke_target=normalized_smoke,
    )

    return ServerProjectRunnerPlan(
        project_spec=project_spec,
        project_id=normalized_project,
        mode=normalized_mode,
        deploy_target=normalized_target,
        push_remote=normalized_remote,
        base_branch=normalized_base,
        worker_branch=normalized_worker,
        quality_gate_commands=normalized_commands,
        backup_evidence_green=backup_green_value,
        smoke_target=normalized_smoke,
        rollback_plan=normalized_rollback,
        operator_decision=normalized_operator,
        live_go=live_go_value,
        decision=_normalize_choice(decision, field_name="decision", choices=_DECISIONS),
        blockers=tuple(blockers),
        planned_steps=planned_steps,
        operator_gate={
            "live_go": live_go_value,
            "operator_decision": normalized_operator,
            "push_remote": normalized_remote,
            "deploy_target": normalized_target,
            "commit_push_deploy_requires_gate": True,
            "cloudflare_tunnel_requested": cloudflare_requested_value,
            "operator_live_go_ready": decision == "ready_for_operator_go",
            "mutation_allowed": False,
            "raw_content_visible": False,
        },
        evidence_summary={
            "backup_evidence_green": backup_green_value,
            "smoke_target_present": bool(normalized_smoke),
            "rollback_plan_present": bool(normalized_rollback),
            "quality_gate_count": len(normalized_commands),
            "planned_step_count": len(planned_steps),
            "raw_content_visible": False,
        },
        next_human_decision=next_human_decision,
    )
