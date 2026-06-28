"""Deterministic remote and branch policy for registered repos."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

from src.repo_registry import RepoRecord, RepoRemote


_ACTIONS = ("push", "force_push", "delete_branch", "publish_tag", "delete_tag")
_DESTRUCTIVE_ACTIONS = ("force_push", "delete_branch", "publish_tag", "delete_tag")
_DECISIONS = ("allowed", "hold", "blocked")
_SAFE_BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,159}$")
_SECRET_RE = re.compile(r"(?i)\b(token|secret|password|passwd|api[_-]?key|bearer)\b\s*[:=]\s*\S+")
_DEFAULT_PROTECTED_BRANCHES = ("main", "master", "dev", "stable", "prod", "production")


class RepoRemotePolicyError(ValueError):
    """Raised when repo remote policy inputs are invalid."""


@dataclass(frozen=True, slots=True)
class RepoRemotePolicyDecision:
    repo_id: str
    remote_name: str
    branch_name: str
    action: str
    decision: str
    reason: str
    next_safe_action: str
    remote_push_policy: str
    remote_purpose: str
    protected_branch: bool

    @property
    def allowed(self) -> bool:
        return self.decision == "allowed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "remote_name": self.remote_name,
            "branch_name": self.branch_name,
            "action": self.action,
            "decision": self.decision,
            "allowed": self.allowed,
            "reason": self.reason,
            "next_safe_action": self.next_safe_action,
            "remote_push_policy": self.remote_push_policy,
            "remote_purpose": self.remote_purpose,
            "protected_branch": self.protected_branch,
        }


def evaluate_remote_branch_policy(
    *,
    record: RepoRecord,
    remote_name: Any,
    branch_name: Any,
    action: Any = "push",
    protected_branches: Iterable[Any] = _DEFAULT_PROTECTED_BRANCHES,
) -> RepoRemotePolicyDecision:
    """Evaluate whether a repo action is allowed by registry policy."""

    if not isinstance(record, RepoRecord):
        raise RepoRemotePolicyError("record must be a RepoRecord")
    normalized_action = _normalize_action(action)
    normalized_remote = _normalize_remote_name(remote_name)
    normalized_branch = normalize_branch_name(branch_name, repo_id=record.repo_id)
    remote = _find_remote(record, normalized_remote)
    protected = _is_protected_branch(normalized_branch, protected_branches)

    if normalized_action in _DESTRUCTIVE_ACTIONS:
        return _decision(
            record=record,
            remote=remote,
            remote_name=normalized_remote,
            branch_name=normalized_branch,
            action=normalized_action,
            decision="blocked",
            protected_branch=protected,
            reason=f"{normalized_action} is blocked; destructive Git operations need a separate live gate.",
            next_safe_action=_safe_branch_action(record, normalized_remote),
        )

    if remote is None:
        return _decision(
            record=record,
            remote=None,
            remote_name=normalized_remote,
            branch_name=normalized_branch,
            action=normalized_action,
            decision="blocked",
            protected_branch=protected,
            reason=f"remote `{normalized_remote}` is not registered for repo `{record.repo_id}`.",
            next_safe_action=_suggest_remote(record),
        )

    if "push" not in record.allowed_actions:
        return _decision(
            record=record,
            remote=remote,
            remote_name=normalized_remote,
            branch_name=normalized_branch,
            action=normalized_action,
            decision="blocked",
            protected_branch=protected,
            reason="repo allowed_actions does not include push.",
            next_safe_action="Use manage_repos update_policy with confirmed=true after reviewing the repo scope.",
        )

    if remote.push_policy != "push_allowed":
        return _decision(
            record=record,
            remote=remote,
            remote_name=normalized_remote,
            branch_name=normalized_branch,
            action=normalized_action,
            decision="blocked",
            protected_branch=protected,
            reason=f"remote `{normalized_remote}` push_policy is `{remote.push_policy}`.",
            next_safe_action=_suggest_remote(record),
        )

    if protected:
        return _decision(
            record=record,
            remote=remote,
            remote_name=normalized_remote,
            branch_name=normalized_branch,
            action=normalized_action,
            decision="hold",
            protected_branch=True,
            reason=f"branch `{normalized_branch}` is protected; protected branch writes require a separate live gate.",
            next_safe_action=_safe_branch_action(record, normalized_remote),
        )

    return _decision(
        record=record,
        remote=remote,
        remote_name=normalized_remote,
        branch_name=normalized_branch,
        action=normalized_action,
        decision="allowed",
        protected_branch=False,
        reason=f"push to `{normalized_remote}/{normalized_branch}` is allowed by repo policy.",
        next_safe_action="Proceed only through the confirmed push runner; deploy remains a separate gate.",
    )


def assert_remote_branch_allowed(**kwargs: Any) -> RepoRemotePolicyDecision:
    decision = evaluate_remote_branch_policy(**kwargs)
    if not decision.allowed:
        raise RepoRemotePolicyError(f"{decision.reason} Next safe action: {decision.next_safe_action}")
    return decision


def choose_push_remote(record: RepoRecord, *, preferred_remote: Any | None = None) -> str:
    if not isinstance(record, RepoRecord):
        raise RepoRemotePolicyError("record must be a RepoRecord")
    if preferred_remote:
        name = _normalize_remote_name(preferred_remote)
        remote = _find_remote(record, name)
        if remote is None:
            raise RepoRemotePolicyError(f"remote `{name}` is not registered. {_suggest_remote(record)}")
        if remote.push_policy != "push_allowed":
            raise RepoRemotePolicyError(f"remote `{name}` push_policy is `{remote.push_policy}`. {_suggest_remote(record)}")
        return name
    for remote in record.remotes:
        if remote.push_policy == "push_allowed":
            return remote.name
    raise RepoRemotePolicyError(_suggest_remote(record))


def normalize_branch_name(value: Any, *, repo_id: str = "repo") -> str:
    branch = _normalize_text(value, field_name="branch_name", max_len=160)
    lowered = branch.lower()
    if (
        not _SAFE_BRANCH_RE.fullmatch(branch)
        or branch.startswith(("-", "/", "."))
        or branch.endswith("/")
        or lowered.endswith(".lock")
        or ".." in branch
        or "//" in branch
        or "@{" in branch
    ):
        raise RepoRemotePolicyError(
            f"branch_name is not safe; use a worker branch such as `{_worker_branch(repo_id)}`."
        )
    return branch


def _decision(
    *,
    record: RepoRecord,
    remote: RepoRemote | None,
    remote_name: str,
    branch_name: str,
    action: str,
    decision: str,
    reason: str,
    next_safe_action: str,
    protected_branch: bool,
) -> RepoRemotePolicyDecision:
    if decision not in _DECISIONS:
        raise RepoRemotePolicyError(f"unsupported decision: {decision}")
    return RepoRemotePolicyDecision(
        repo_id=record.repo_id,
        remote_name=remote_name,
        branch_name=branch_name,
        action=action,
        decision=decision,
        reason=reason,
        next_safe_action=next_safe_action,
        remote_push_policy=remote.push_policy if remote else "missing",
        remote_purpose=remote.purpose if remote else "missing",
        protected_branch=protected_branch,
    )


def _normalize_text(value: Any, *, field_name: str, max_len: int = 120) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise RepoRemotePolicyError(f"{field_name} must not be empty")
    if len(text) > max_len:
        raise RepoRemotePolicyError(f"{field_name} exceeds max length {max_len}")
    if _SECRET_RE.search(text):
        raise RepoRemotePolicyError(f"{field_name} appears to contain secret material")
    return text


def _normalize_action(value: Any) -> str:
    action = _normalize_text(value, field_name="action", max_len=40).lower().replace("-", "_")
    aliases = {
        "force": "force_push",
        "forcepush": "force_push",
        "tag_publish": "publish_tag",
        "publish_tags": "publish_tag",
        "delete_remote_branch": "delete_branch",
    }
    action = aliases.get(action, action)
    if action not in _ACTIONS:
        raise RepoRemotePolicyError(f"unsupported action: {value!r}; use push for the safe path.")
    return action


def _normalize_remote_name(value: Any) -> str:
    remote = _normalize_text(value, field_name="remote_name", max_len=80)
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", remote) or remote.startswith("-"):
        raise RepoRemotePolicyError("remote_name contains unsupported characters")
    return remote


def _find_remote(record: RepoRecord, remote_name: str) -> RepoRemote | None:
    for remote in record.remotes:
        if remote.name == remote_name:
            return remote
    return None


def _is_protected_branch(branch: str, protected_branches: Iterable[Any]) -> bool:
    normalized = {_normalize_text(item, field_name="protected_branch", max_len=120) for item in protected_branches}
    return branch in normalized


def _suggest_remote(record: RepoRecord) -> str:
    push_remote = next((remote.name for remote in record.remotes if remote.push_policy == "push_allowed"), "")
    if push_remote:
        return f"Use push_allowed remote `{push_remote}` or update policy with confirmed=true."
    return "Use manage_repos update_policy to add a push_allowed remote such as `fuzzy` after review."


def _safe_branch_action(record: RepoRecord, remote_name: str) -> str:
    return f"Push a worker branch such as `{remote_name}/{_worker_branch(record.repo_id)}` instead."


def _worker_branch(repo_id: str) -> str:
    safe_repo = re.sub(r"[^a-z0-9._-]+", "-", str(repo_id).lower()).strip("-._") or "repo"
    return f"codex/{safe_repo}/work"
