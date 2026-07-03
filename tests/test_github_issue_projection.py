import pytest

from src.github_issue_projection import (
    GitHubIssueProjectField,
    GitHubIssueProjectionError,
    InMemoryGitHubIssueFieldCache,
    apply_github_issue_projection,
    prepare_github_issue_projection,
)


class FakeProjectionClient:
    def __init__(self, fields=None, *, fail_field_write=False):
        self.fields = tuple(fields or ())
        self.fail_field_write = fail_field_write
        self.list_calls = []
        self.field_writes = []
        self.label_writes = []

    def list_issue_fields(self, *, owner: str, repository: str):
        self.list_calls.append((owner, repository))
        return self.fields

    def set_issue_field(self, *, issue_node_id: str, field_id: str, value: str) -> None:
        if self.fail_field_write:
            raise RuntimeError("Authorization: Bearer github_pat_secret leaked upstream")
        self.field_writes.append((issue_node_id, field_id, value))

    def add_issue_label(self, *, issue_ref: str, label: str) -> None:
        self.label_writes.append((issue_ref, label))


def test_projection_caches_field_ids_per_owner_and_repository():
    client = FakeProjectionClient([
        GitHubIssueProjectField(name="Priority", field_id="PVTSSF_priority"),
    ])
    cache = InMemoryGitHubIssueFieldCache()

    first = prepare_github_issue_projection(
        client=client,
        cache=cache,
        owner="alice",
        repository="fuzzy123-ai/odysseus-fuzzy",
        issue_ref="#10",
        issue_node_id="node-10",
        fields={"priority": "high"},
    )
    second = prepare_github_issue_projection(
        client=client,
        cache=cache,
        owner="alice",
        repository="fuzzy123-ai/odysseus-fuzzy",
        issue_ref="#11",
        issue_node_id="node-11",
        fields={"priority": "low"},
    )
    other_repo = prepare_github_issue_projection(
        client=client,
        cache=cache,
        owner="alice",
        repository="fuzzy123-ai/other",
        issue_ref="#12",
        issue_node_id="node-12",
        fields={"priority": "medium"},
    )

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert other_repo.cache_hit is False
    assert client.list_calls == [
        ("alice", "fuzzy123-ai/odysseus-fuzzy"),
        ("alice", "fuzzy123-ai/other"),
    ]


def test_projection_applies_github_fields_and_label_fallbacks():
    client = FakeProjectionClient([
        GitHubIssueProjectField(name="Priority", field_id="PVTSSF_priority"),
        GitHubIssueProjectField(name="Target date", field_id="PVTSSF_target"),
    ])
    cache = InMemoryGitHubIssueFieldCache()
    plan = prepare_github_issue_projection(
        client=client,
        cache=cache,
        owner="alice",
        repository="fuzzy123-ai/odysseus-fuzzy",
        issue_ref="#10",
        issue_node_id="node-10",
        fields={"priority": "high", "area": "Memory", "target_date": "2026-07-10"},
    )

    preview = apply_github_issue_projection(client=client, plan=plan)
    applied = apply_github_issue_projection(client=client, plan=plan, apply=True)

    assert [item["status"] for item in preview.write_report] == ["planned", "planned", "planned"]
    assert client.field_writes == [
        ("node-10", "PVTSSF_priority", "high"),
        ("node-10", "PVTSSF_target", "2026-07-10"),
    ]
    assert client.label_writes == [("#10", "area/memory")]
    assert applied.applied_count == 3
    assert applied.failed_count == 0


def test_projection_missing_non_label_field_skips_cleanly():
    client = FakeProjectionClient([])
    cache = InMemoryGitHubIssueFieldCache()
    plan = prepare_github_issue_projection(
        client=client,
        cache=cache,
        owner="alice",
        repository="fuzzy123-ai/odysseus-fuzzy",
        issue_ref="#10",
        issue_node_id="node-10",
        fields={"target_date": "2026-07-10"},
    )

    result = apply_github_issue_projection(client=client, plan=plan, apply=True)

    assert result.write_report == (
        {
            "field": "target_date",
            "method": "local_only",
            "status": "skipped",
            "target": "Target date",
            "error_redacted": "missing provider field",
        },
    )


def test_projection_errors_are_redacted_per_field():
    client = FakeProjectionClient(
        [GitHubIssueProjectField(name="Priority", field_id="PVTSSF_priority")],
        fail_field_write=True,
    )
    cache = InMemoryGitHubIssueFieldCache()
    plan = prepare_github_issue_projection(
        client=client,
        cache=cache,
        owner="alice",
        repository="fuzzy123-ai/odysseus-fuzzy",
        issue_ref="#10",
        issue_node_id="node-10",
        fields={"priority": "high"},
    )

    result = apply_github_issue_projection(client=client, plan=plan, apply=True)

    assert result.failed_count == 1
    assert result.write_report[0]["error_redacted"] == "RuntimeError: redacted"
    assert "github_pat" not in str(result.to_dict())
    assert "Bearer" not in str(result.to_dict())


def test_projection_rejects_secret_like_scopes_and_targets():
    client = FakeProjectionClient([])
    cache = InMemoryGitHubIssueFieldCache()

    with pytest.raises(GitHubIssueProjectionError):
        prepare_github_issue_projection(
            client=client,
            cache=cache,
            owner="alice",
            repository="Authorization: Bearer github_pat_secret",
            issue_ref="#10",
            fields={"priority": "high"},
        )
