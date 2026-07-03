from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, GitHubIssueRecord
from src.github_issue_index import (
    InMemoryGitHubIssueIndexBackend,
    build_issue_index_document,
    query_github_issue_index,
    reindex_github_issues,
)


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _issue(
    issue_id: str,
    *,
    owner: str = "alice",
    repository: str = "fuzzy123-ai/odysseus-fuzzy",
    external_id: str = "1",
    title: str = "Inbox regression",
    body: str = "Telegram attachment was not indexed into memory.",
    state: str = "open",
    labels: list[str] | None = None,
):
    return GitHubIssueRecord(
        id=issue_id,
        owner=owner,
        provider="github",
        repository=repository,
        external_id=external_id,
        title=title,
        body=body,
        state=state,
        labels_json=labels or ["area/inbox", "priority/high"],
        url=f"https://github.example/issues/{external_id}",
    )


def test_issue_index_document_normalizes_and_bounds_text():
    issue = _issue(
        "issue-1",
        body="  line one\n\nline two  " + ("x" * 200),
        labels=["area/inbox", "area/inbox", "priority/high"],
    )

    document = build_issue_index_document(issue, text_limit=120)

    assert document.id == "github_issue:alice:fuzzy123-ai/odysseus-fuzzy:1"
    assert len(document.text) <= 120
    assert "\n\n" not in document.text
    assert document.text.endswith("...")
    assert document.metadata["owner"] == "alice"
    assert document.metadata["repository"] == "fuzzy123-ai/odysseus-fuzzy"
    assert document.metadata["labels"] == ("area/inbox", "priority/high")


def test_reindex_is_idempotent_and_owner_repo_scoped():
    db = _session()
    db.add_all(
        [
            _issue("alice-1", owner="alice", external_id="1"),
            _issue("alice-2", owner="alice", external_id="2", state="closed"),
            _issue("bob-1", owner="bob", external_id="1"),
            _issue("other-repo", owner="alice", repository="other/repo", external_id="3"),
        ]
    )
    db.commit()
    backend = InMemoryGitHubIssueIndexBackend()

    first = reindex_github_issues(
        db,
        backend,
        owner="alice",
        repository="fuzzy123-ai/odysseus-fuzzy",
    )
    second = reindex_github_issues(
        db,
        backend,
        owner="alice",
        repository="fuzzy123-ai/odysseus-fuzzy",
    )

    assert first.indexed == 2
    assert second.indexed == 2
    assert backend.count() == 2


def test_query_applies_owner_repo_and_closed_filters():
    db = _session()
    db.add_all(
        [
            _issue("alice-open", owner="alice", external_id="1", body="memory inbox attachment failed"),
            _issue("alice-closed", owner="alice", external_id="2", body="memory inbox attachment failed", state="closed"),
            _issue("bob-open", owner="bob", external_id="3", body="memory inbox attachment failed"),
            _issue("other-repo", owner="alice", repository="other/repo", external_id="4", body="memory inbox attachment failed"),
        ]
    )
    db.commit()
    backend = InMemoryGitHubIssueIndexBackend()
    reindex_github_issues(db, backend, owner="alice", repository="fuzzy123-ai/odysseus-fuzzy")
    reindex_github_issues(db, backend, owner="bob", repository="fuzzy123-ai/odysseus-fuzzy")
    reindex_github_issues(db, backend, owner="alice", repository="other/repo")

    open_only = query_github_issue_index(
        backend,
        "memory inbox attachment",
        owner="alice",
        repository="fuzzy123-ai/odysseus-fuzzy",
        include_closed=False,
    )
    with_closed = query_github_issue_index(
        backend,
        "memory inbox attachment",
        owner="alice",
        repository="fuzzy123-ai/odysseus-fuzzy",
        include_closed=True,
    )

    assert [match.metadata["external_id"] for match in open_only] == ["1"]
    assert [match.metadata["external_id"] for match in with_closed] == ["1", "2"]
    assert all(match.metadata["owner"] == "alice" for match in with_closed)
    assert all(match.metadata["repository"] == "fuzzy123-ai/odysseus-fuzzy" for match in with_closed)


def test_reindex_can_exclude_closed_issues():
    db = _session()
    db.add_all([
        _issue("open", external_id="1"),
        _issue("closed", external_id="2", state="closed"),
    ])
    db.commit()
    backend = InMemoryGitHubIssueIndexBackend()

    result = reindex_github_issues(
        db,
        backend,
        owner="alice",
        repository="fuzzy123-ai/odysseus-fuzzy",
        include_closed=False,
    )

    assert result.indexed == 1
    assert backend.count() == 1
