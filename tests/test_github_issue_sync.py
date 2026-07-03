from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, GitHubIssueRecord
from src.github_issue_sync import (
    GitHubIssueSyncError,
    GitHubIssueSyncItem,
    GitHubIssueSyncPage,
    sync_github_issues,
)


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


class FakeGitHubClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def list_issues_page(self, *, repository, since, cursor):
        self.calls.append({"repository": repository, "since": since, "cursor": cursor})
        index = int(cursor or "0")
        issues = tuple(self.pages[index])
        next_cursor = str(index + 1) if index + 1 < len(self.pages) else None
        return GitHubIssueSyncPage(issues=issues, next_cursor=next_cursor)


class FailingGitHubClient:
    def list_issues_page(self, *, repository, since, cursor):
        raise RuntimeError("bad Authorization: Bearer ghp_supersecret")


def test_sync_paginates_and_persists_labels_and_closed_state():
    db = _session()
    client = FakeGitHubClient(
        [
            [
                GitHubIssueSyncItem(
                    external_id="1",
                    title="Inbox bug",
                    body="Synthetic issue body",
                    labels=("area/inbox", "priority/high"),
                    author="octocat",
                    url="https://github.example/issues/1",
                    updated_at=datetime(2026, 7, 3, 8, 0, 0),
                )
            ],
            [
                GitHubIssueSyncItem(
                    external_id="2",
                    title="Closed duplicate",
                    state="closed",
                    labels=("duplicate",),
                    updated_at=datetime(2026, 7, 3, 9, 0, 0),
                )
            ],
        ]
    )

    result = sync_github_issues(
        db,
        owner="alice",
        repository="fuzzy123-ai/odysseus-fuzzy",
        client=client,
        now=datetime(2026, 7, 3, 10, 0, 0),
    )

    assert result.fetched == 2
    assert result.created == 2
    assert result.updated == 0
    assert result.closed == 1
    assert [call["cursor"] for call in client.calls] == [None, "1"]

    stored = db.query(GitHubIssueRecord).order_by(GitHubIssueRecord.external_id).all()
    assert [issue.external_id for issue in stored] == ["1", "2"]
    assert stored[0].labels_json == ["area/inbox", "priority/high"]
    assert stored[1].state == "closed"


def test_sync_updates_existing_issue_and_uses_incremental_watermark():
    db = _session()
    first_client = FakeGitHubClient(
        [[GitHubIssueSyncItem(external_id="5", title="Old title", updated_at=datetime(2026, 7, 2, 12, 0, 0))]]
    )
    sync_github_issues(
        db,
        owner="alice",
        repository="fuzzy123-ai/odysseus-fuzzy",
        client=first_client,
    )

    second_client = FakeGitHubClient(
        [[GitHubIssueSyncItem(external_id="5", title="New title", labels=("status/ready",))]]
    )
    result = sync_github_issues(
        db,
        owner="alice",
        repository="fuzzy123-ai/odysseus-fuzzy",
        client=second_client,
        now=datetime(2026, 7, 3, 10, 0, 0),
    )

    assert result.created == 0
    assert result.updated == 1
    assert second_client.calls[0]["since"] == datetime(2026, 7, 2, 12, 0, 0)

    stored = db.query(GitHubIssueRecord).one()
    assert stored.title == "New title"
    assert stored.labels_json == ["status/ready"]


def test_sync_can_override_incremental_since():
    db = _session()
    client = FakeGitHubClient([[GitHubIssueSyncItem(external_id="9", title="Manual watermark")]])
    explicit_since = datetime(2026, 7, 1, 0, 0, 0)

    sync_github_issues(
        db,
        owner="alice",
        repository="fuzzy123-ai/odysseus-fuzzy",
        client=client,
        since=explicit_since,
    )

    assert client.calls[0]["since"] == explicit_since


def test_token_errors_are_redacted_and_no_partial_rows_are_committed():
    db = _session()

    with pytest.raises(GitHubIssueSyncError) as exc_info:
        sync_github_issues(
            db,
            owner="alice",
            repository="fuzzy123-ai/odysseus-fuzzy",
            client=FailingGitHubClient(),
        )

    message = str(exc_info.value)
    assert "ghp_" not in message
    assert "Bearer" not in message
    assert "[redacted-secret]" in message
    assert db.query(GitHubIssueRecord).count() == 0
