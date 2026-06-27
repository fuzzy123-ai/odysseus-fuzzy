from pathlib import Path

import pytest

from src.server_project_git_review import ServerProjectGitReviewError, build_project_git_review_plan
from src.server_project_registry import ServerProjectRegistry


def _record():
    registry = ServerProjectRegistry()
    return registry.create_project(
        project_title="Kundenportal MVP",
        project_type="app",
        created_at="2026-06-27T10:00:00Z",
    )


def test_create_new_repo_holds_without_operator_go():
    plan = build_project_git_review_plan(
        record=_record(),
        changed_paths=("src/app.py",),
        operator_decision="hold",
    )

    assert plan.decision == "hold"
    assert plan.push_allowed is False
    assert plan.repo_creation_allowed is False
    assert "new repository creation requires operator_decision=go" in plan.blockers


def test_create_new_repo_plan_ready_with_operator_go_and_changed_paths():
    plan = build_project_git_review_plan(
        record=_record(),
        changed_paths=("src/app.py", "tests/test_app.py"),
        operator_decision="go",
    )

    assert plan.decision == "plan_ready"
    assert plan.push_allowed is True
    assert plan.repo_creation_allowed is True
    assert plan.remote_name == "fuzzy"
    assert plan.worker_branch == "project/kundenportal-mvp/work"
    assert all(step["executes"] is False for step in plan.planned_steps)


def test_attach_existing_repo_can_be_plan_ready_without_repo_creation_go():
    plan = build_project_git_review_plan(
        record=_record(),
        repo_action="attach_existing",
        changed_paths=("README.md",),
        operator_decision="hold",
    )

    assert plan.decision == "plan_ready"
    assert plan.repo_creation_allowed is False


def test_origin_remote_is_blocked():
    plan = build_project_git_review_plan(
        record=_record(),
        remote_name="origin",
        repo_action="attach_existing",
        changed_paths=("README.md",),
    )

    assert plan.decision == "blocked"
    assert any("origin" in blocker for blocker in plan.blockers)


def test_changed_paths_are_required_for_commit_review():
    plan = build_project_git_review_plan(
        record=_record(),
        repo_action="attach_existing",
        changed_paths=(),
    )

    assert plan.decision == "hold"
    assert "changed paths are required" in plan.blockers[0]


def test_rejects_unsafe_branch_path_repo_and_secret_inputs():
    with pytest.raises(ServerProjectGitReviewError, match="branch"):
        build_project_git_review_plan(
            record=_record(),
            worker_branch="feature/demo",
            changed_paths=("README.md",),
        )

    with pytest.raises(ServerProjectGitReviewError, match="forward slashes"):
        build_project_git_review_plan(
            record=_record(),
            repo_action="attach_existing",
            changed_paths=(r"src\app.py",),
        )

    with pytest.raises(ServerProjectGitReviewError, match="secret material"):
        build_project_git_review_plan(
            record=_record(),
            repo_action="attach_existing",
            changed_paths=("README.md",),
            commit_message="feat: add token=abc123",
        )


def test_rejects_odysseus_repo_name_from_record_payload():
    registry = ServerProjectRegistry()
    with pytest.raises(Exception, match="Odysseus"):
        registry.create_project(
            project_title="Demo",
            repo_name="odysseus",
            created_at="2026-06-27T10:00:00Z",
        )


def test_source_has_no_live_git_runtime():
    source = Path("src/server_project_git_review.py").read_text(encoding="utf-8")

    forbidden = ("subprocess", "git push", "git reset", "git clean", "requests", "httpx", "paramiko", "shell=True")
    for fragment in forbidden:
        assert fragment not in source
