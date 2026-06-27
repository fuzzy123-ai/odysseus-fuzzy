"""Dry-run Git and review planning for universal server projects."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

from src.server_project_registry import ServerProjectRecord


_REMOTE_ALLOWLIST = ("fuzzy",)
_REPO_ACTIONS = ("create_new", "attach_existing")
_OPERATOR_DECISIONS = ("go", "hold", "no_go", "missing")
_DECISIONS = ("plan_ready", "hold", "blocked")
_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer)\b\s*[:=]?\s*\S*")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")


class ServerProjectGitReviewError(ValueError):
    """Raised when a project Git/review plan is unsafe or invalid."""


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False, max_len: int = 220) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text and not allow_empty:
        raise ServerProjectGitReviewError(f"{field_name} must not be empty")
    if len(text) > max_len:
        raise ServerProjectGitReviewError(f"{field_name} exceeds max length {max_len}")
    if _SECRET_RE.search(text):
        raise ServerProjectGitReviewError(f"{field_name} appears to contain secret material")
    if re.search(r"[A-Za-z]:\\", text) or text.startswith("/"):
        raise ServerProjectGitReviewError(f"{field_name} must not contain host-local absolute paths")
    return text


def _normalize_choice(value: Any, *, field_name: str, choices: tuple[str, ...]) -> str:
    text = _normalize_text(value, field_name=field_name).lower().replace("-", "_")
    if text not in choices:
        raise ServerProjectGitReviewError(f"unsupported {field_name}: {value!r}")
    return text


def _normalize_branch(value: Any, *, field_name: str) -> str:
    branch = _normalize_text(value, field_name=field_name, max_len=120)
    lowered = branch.lower()
    if lowered.endswith(".lock") or ".." in branch or branch.startswith("-"):
        raise ServerProjectGitReviewError(f"{field_name} is not a safe branch name")
    if not branch.startswith(("project/", "codex/", "release/")):
        raise ServerProjectGitReviewError(f"{field_name} must use project/, codex/, or release/ prefix")
    return branch


def _normalize_repo_name(value: Any) -> str:
    repo_name = _normalize_text(value, field_name="repo_name", max_len=80).lower()
    if not _SLUG_RE.fullmatch(repo_name):
        raise ServerProjectGitReviewError("repo_name must be a slug")
    if repo_name in {"odysseus", "odysseus-fuzzy"}:
        raise ServerProjectGitReviewError("project repository must not be Odysseus")
    return repo_name


def _normalize_repo_path(value: Any) -> str:
    raw = _normalize_text(value, field_name="changed_path", max_len=180)
    if "\\" in raw:
        raise ServerProjectGitReviewError("changed_path must use forward slashes")
    parts = PurePosixPath(raw).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ServerProjectGitReviewError("changed_path must not contain traversal segments")
    return "/".join(parts)


def _dedupe_paths(values: Iterable[Any]) -> tuple[str, ...]:
    paths: list[str] = []
    for value in values:
        path = _normalize_repo_path(value)
        if path not in paths:
            paths.append(path)
    return tuple(paths)


@dataclass(frozen=True, slots=True)
class ProjectGitReviewPlan:
    project_slug: str
    repo_name: str
    repo_action: str
    remote_name: str
    base_branch: str
    worker_branch: str
    changed_paths: tuple[str, ...]
    commit_message: str
    operator_decision: str
    decision: str
    blockers: tuple[str, ...]
    planned_steps: tuple[Mapping[str, Any], ...]
    next_human_decision: str

    @property
    def push_allowed(self) -> bool:
        return self.decision == "plan_ready"

    @property
    def repo_creation_allowed(self) -> bool:
        return self.repo_action == "create_new" and self.operator_decision == "go" and self.decision == "plan_ready"

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_slug": self.project_slug,
            "repo_name": self.repo_name,
            "repo_action": self.repo_action,
            "remote_name": self.remote_name,
            "base_branch": self.base_branch,
            "worker_branch": self.worker_branch,
            "changed_paths": list(self.changed_paths),
            "commit_message": self.commit_message,
            "operator_decision": self.operator_decision,
            "decision": self.decision,
            "push_allowed": self.push_allowed,
            "repo_creation_allowed": self.repo_creation_allowed,
            "blockers": list(self.blockers),
            "planned_steps": [dict(step) for step in self.planned_steps],
            "next_human_decision": self.next_human_decision,
        }


def build_project_git_review_plan(
    *,
    record: ServerProjectRecord,
    repo_action: Any = "create_new",
    remote_name: Any = "fuzzy",
    base_branch: Any = "dev",
    worker_branch: Any | None = None,
    changed_paths: Iterable[Any] = (),
    commit_message: Any | None = None,
    operator_decision: Any = "missing",
) -> ProjectGitReviewPlan:
    if not isinstance(record, ServerProjectRecord):
        raise ServerProjectGitReviewError("record must be a ServerProjectRecord")
    normalized_action = _normalize_choice(repo_action, field_name="repo_action", choices=_REPO_ACTIONS)
    normalized_remote = _normalize_text(remote_name, field_name="remote_name", max_len=40)
    normalized_base = _normalize_text(base_branch, field_name="base_branch", max_len=80)
    normalized_worker = _normalize_branch(
        worker_branch if worker_branch is not None else f"project/{record.project_slug}/work",
        field_name="worker_branch",
    )
    normalized_paths = _dedupe_paths(changed_paths)
    normalized_commit = _normalize_text(
        commit_message if commit_message is not None else f"feat: initialize {record.project_slug}",
        field_name="commit_message",
        max_len=120,
    )
    normalized_operator = _normalize_choice(
        operator_decision,
        field_name="operator_decision",
        choices=_OPERATOR_DECISIONS,
    )
    repo_name = _normalize_repo_name(record.project_spec.repo_name)

    blockers: list[str] = []
    if normalized_remote not in _REMOTE_ALLOWLIST:
        blockers.append("push remote must be fuzzy; origin is blocked")
    if normalized_base.lower() == "origin/dev" or normalized_remote == "origin":
        blockers.append("origin is not an allowed project push target")
    if normalized_action == "create_new" and normalized_operator != "go":
        blockers.append("new repository creation requires operator_decision=go")
    if not normalized_paths:
        blockers.append("changed paths are required before commit or push review")
    if normalized_operator == "no_go":
        blockers.append("operator decision is no_go")

    if normalized_remote not in _REMOTE_ALLOWLIST or normalized_base.lower() == "origin/dev":
        decision = "blocked"
    elif blockers:
        decision = "hold"
    else:
        decision = "plan_ready"

    return ProjectGitReviewPlan(
        project_slug=record.project_slug,
        repo_name=repo_name,
        repo_action=normalized_action,
        remote_name=normalized_remote,
        base_branch=normalized_base,
        worker_branch=normalized_worker,
        changed_paths=normalized_paths,
        commit_message=normalized_commit,
        operator_decision=normalized_operator,
        decision=decision,
        blockers=tuple(blockers),
        planned_steps=_planned_steps(
            record=record,
            repo_action=normalized_action,
            remote_name=normalized_remote,
            base_branch=normalized_base,
            worker_branch=normalized_worker,
            changed_paths=normalized_paths,
            commit_message=normalized_commit,
        ),
        next_human_decision=_next_decision(decision, normalized_action),
    )


def _planned_steps(
    *,
    record: ServerProjectRecord,
    repo_action: str,
    remote_name: str,
    base_branch: str,
    worker_branch: str,
    changed_paths: tuple[str, ...],
    commit_message: str,
) -> tuple[Mapping[str, Any], ...]:
    return (
        {
            "step_id": "repo_review",
            "summary": f"review {repo_action} for {record.project_spec.repo_name}",
            "executes": False,
        },
        {
            "step_id": "branch_review",
            "summary": f"review worker branch {worker_branch} from {remote_name}/{base_branch}",
            "executes": False,
        },
        {
            "step_id": "change_set_review",
            "summary": f"review {len(changed_paths)} changed project path(s)",
            "executes": False,
        },
        {
            "step_id": "commit_review",
            "summary": f"review commit message: {commit_message}",
            "executes": False,
        },
        {
            "step_id": "push_review",
            "summary": f"review push target {remote_name}/{worker_branch}",
            "executes": False,
        },
    )


def _next_decision(decision: str, repo_action: str) -> str:
    if decision == "plan_ready":
        return "Review diff evidence and keep live git operations in the separate operator path."
    if repo_action == "create_new":
        return "Provide operator Go and changed paths before creating a new project repository."
    return "Clear Git/review blockers before project commit or push review."
