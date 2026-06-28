import json
from pathlib import Path

import pytest

from src.repo_registry import RepoRecord, RepoRemote
from src.repo_remote_policy import (
    RepoRemotePolicyError,
    assert_remote_branch_allowed,
    choose_push_remote,
    evaluate_remote_branch_policy,
    normalize_branch_name,
)


def _record(**overrides):
    values = {
        "repo_id": "demo",
        "title": "Demo Repo",
        "owner": "fuzzy123-ai",
        "workspace_root": "projects/demo",
        "project_root": "projects/demo/repo",
        "created_at": "2026-06-28T10:00:00Z",
        "allowed_actions": ["status", "push"],
        "remotes": [
            RepoRemote.create(
                name="origin",
                url="https://github.com/upstream/demo.git",
                purpose="origin",
                push_policy="read_only",
            ),
            RepoRemote.create(
                name="fuzzy",
                url="https://github.com/fuzzy123-ai/demo.git",
                purpose="fork",
                push_policy="push_allowed",
            ),
        ],
    }
    values.update(overrides)
    return RepoRecord.create(**values)


def test_fuzzy_push_allowed_for_worker_branch():
    decision = evaluate_remote_branch_policy(
        record=_record(),
        remote_name="fuzzy",
        branch_name="codex/demo/work",
    )

    assert decision.allowed is True
    assert decision.decision == "allowed"
    assert decision.remote_push_policy == "push_allowed"
    assert decision.protected_branch is False
    assert "fuzzy/codex/demo/work" in decision.reason
    assert "C:\\" not in json.dumps(decision.to_dict())


def test_origin_defaults_read_only_and_suggests_push_allowed_remote():
    decision = evaluate_remote_branch_policy(
        record=_record(),
        remote_name="origin",
        branch_name="codex/demo/work",
    )

    assert decision.allowed is False
    assert decision.decision == "blocked"
    assert decision.remote_push_policy == "read_only"
    assert "read_only" in decision.reason
    assert "fuzzy" in decision.next_safe_action


def test_origin_can_be_explicitly_push_allowlisted_for_worker_branch():
    record = _record(
        remotes=[
            RepoRemote.create(
                name="origin",
                url="https://github.com/fuzzy123-ai/demo.git",
                purpose="fork",
                push_policy="push_allowed",
            )
        ]
    )

    decision = evaluate_remote_branch_policy(
        record=record,
        remote_name="origin",
        branch_name="codex/demo/work",
    )

    assert decision.allowed is True
    assert decision.remote_name == "origin"


@pytest.mark.parametrize("action", ["force_push", "delete_branch", "publish_tag", "delete_tag", "tag_publish"])
def test_destructive_actions_are_blocked_even_on_push_allowed_remote(action):
    decision = evaluate_remote_branch_policy(
        record=_record(),
        remote_name="fuzzy",
        branch_name="codex/demo/work",
        action=action,
    )

    assert decision.allowed is False
    assert decision.decision == "blocked"
    assert "destructive" in decision.reason
    assert "codex/demo/work" in decision.next_safe_action


@pytest.mark.parametrize("branch", ["main", "dev", "production"])
def test_protected_branch_pushes_require_separate_gate(branch):
    decision = evaluate_remote_branch_policy(
        record=_record(),
        remote_name="fuzzy",
        branch_name=branch,
    )

    assert decision.allowed is False
    assert decision.decision == "hold"
    assert decision.protected_branch is True
    assert "protected" in decision.reason
    assert "codex/demo/work" in decision.next_safe_action


@pytest.mark.parametrize("branch", ["../main", "-bad", "main.lock", "feature//x", "feature@{1}"])
def test_unsafe_branch_names_are_rejected_with_safe_alternative(branch):
    with pytest.raises(RepoRemotePolicyError, match="codex/demo/work"):
        normalize_branch_name(branch, repo_id="demo")


def test_missing_remote_blocks_and_suggests_policy_update():
    decision = evaluate_remote_branch_policy(
        record=_record(),
        remote_name="missing",
        branch_name="codex/demo/work",
    )

    assert decision.allowed is False
    assert decision.remote_push_policy == "missing"
    assert "not registered" in decision.reason
    assert "fuzzy" in decision.next_safe_action


def test_repo_must_allow_push_action_even_when_remote_is_push_allowed():
    decision = evaluate_remote_branch_policy(
        record=_record(allowed_actions=["status"]),
        remote_name="fuzzy",
        branch_name="codex/demo/work",
    )

    assert decision.allowed is False
    assert "allowed_actions" in decision.reason
    assert "update_policy" in decision.next_safe_action


def test_assert_remote_branch_allowed_raises_with_next_safe_action():
    with pytest.raises(RepoRemotePolicyError, match="Next safe action"):
        assert_remote_branch_allowed(
            record=_record(),
            remote_name="origin",
            branch_name="codex/demo/work",
        )


def test_choose_push_remote_prefers_registered_push_allowed_remote():
    record = _record()

    assert choose_push_remote(record) == "fuzzy"
    assert choose_push_remote(record, preferred_remote="fuzzy") == "fuzzy"

    with pytest.raises(RepoRemotePolicyError, match="read_only"):
        choose_push_remote(record, preferred_remote="origin")
    with pytest.raises(RepoRemotePolicyError, match="not registered"):
        choose_push_remote(record, preferred_remote="missing")


def test_source_has_no_live_git_or_network_runtime():
    source = Path("src/repo_remote_policy.py").read_text(encoding="utf-8").lower()

    forbidden = ("subprocess", "requests", "httpx", "git push", "git reset", "force-push")
    for fragment in forbidden:
        assert fragment not in source
