from src.tool_domains.repo_output import (
    repo_changes_output,
    repo_commit_output,
    repo_forge_output,
    repo_push_output,
    repo_status_output,
)


def test_repo_status_output_formats_clean_and_dirty_entries():
    assert repo_status_output({"repo_id": "demo", "branch_line": "## main", "entries": []}) == (
        "Status for `demo`:\n## main\n- clean"
    )

    assert "- M README.md" in repo_status_output(
        {"repo_id": "demo", "branch_line": "## main", "entries": ["M README.md"]}
    )


def test_repo_commit_output_reports_blockers_or_committed_paths():
    blocked = repo_commit_output(
        {"status": "blocked", "plan": {"repo_id": "demo", "decision": "needs_go", "blockers": ["missing gate"]}}
    )
    assert "Warum blockiert:" in blocked
    assert "- missing gate" in blocked

    ready = repo_commit_output(
        {"status": "committed", "plan": {"repo_id": "demo", "decision": "go"}, "committed_paths": ["README.md"]}
    )
    assert "Committed paths: README.md" in ready


def test_repo_push_and_forge_outputs_include_targets_and_metadata():
    pushed = repo_push_output(
        {
            "status": "pushed",
            "pushed_ref": "fuzzy/dev",
            "plan": {"repo_id": "demo", "decision": "go", "remote_name": "fuzzy", "branch_name": "dev", "commit_sha": "abc"},
        }
    )
    assert "Target: fuzzy/dev @ abc." in pushed
    assert "Pushed ref: fuzzy/dev" in pushed

    forged = repo_forge_output(
        {
            "status": "fetched",
            "plan": {"repo_id": "demo", "decision": "go", "provider": "github", "namespace": "fuzzy", "repo_name": "demo"},
            "metadata": {"default_branch": "main", "issue_count": 2, "pull_request_count": 1, "permissions": ["read"]},
        }
    )
    assert "Metadata: default_branch=main, issues=2, prs=1, permissions=read" in forged


def test_repo_changes_output_mentions_redacted_memory_event():
    output = repo_changes_output(
        {
            "persisted": True,
            "snapshot": {"repo_id": "demo", "id": "snap-1"},
            "project_context": {"context_lines": ["changed README.md"]},
        }
    )

    assert "Snapshot: `snap-1`." in output
    assert "- changed README.md" in output
    assert "raw diffs are not included" in output
