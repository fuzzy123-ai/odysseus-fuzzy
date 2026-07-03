from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base, GitHubIssueDuplicateCandidate, GitHubIssueRecord
from src.github_issue_duplicates import (
    GitHubIssueDraft,
    find_duplicate_candidates,
    record_duplicate_candidates,
)
from src.github_issue_index import InMemoryGitHubIssueIndexBackend, reindex_github_issues


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)()


def _issue(
    issue_id: str,
    *,
    external_id: str,
    title: str = "Telegram inbox attachment fails",
    body: str = "File upload through Telegram is not processed by the universal inbox memory pipeline.",
    state: str = "open",
    labels: list[str] | None = None,
):
    return GitHubIssueRecord(
        id=issue_id,
        owner="alice",
        provider="github",
        repository="fuzzy123-ai/odysseus-fuzzy",
        external_id=external_id,
        title=title,
        body=body,
        state=state,
        labels_json=labels or ["area/inbox", "priority/high"],
        url=f"https://github.example/issues/{external_id}",
    )


def _indexed_backend(db):
    backend = InMemoryGitHubIssueIndexBackend()
    reindex_github_issues(db, backend, owner="alice", repository="fuzzy123-ai/odysseus-fuzzy")
    return backend


def test_duplicate_report_returns_top_three_with_score_and_reason():
    db = _session()
    db.add_all(
        [
            _issue("one", external_id="1"),
            _issue("two", external_id="2", body="Telegram upload creates no inbox memory item."),
            _issue("three", external_id="3", body="Universal inbox upload failure from Telegram."),
            _issue("four", external_id="4", body="Calendar reminder digest did not send."),
        ]
    )
    db.commit()

    report = find_duplicate_candidates(
        _indexed_backend(db),
        GitHubIssueDraft(
            title="Telegram inbox attachment fails",
            body="Telegram file upload is not processed by universal inbox memory.",
            labels=("area/inbox",),
        ),
        owner="alice",
        repository="fuzzy123-ai/odysseus-fuzzy",
        top_k=3,
    )

    assert len(report.candidates) == 3
    assert all(candidate.score > 0 for candidate in report.candidates)
    assert all("possible duplicate of #" in candidate.reason for candidate in report.candidates)
    assert report.candidates[0].external_id == "1"


def test_open_issue_ranks_ahead_of_equivalent_closed_issue():
    db = _session()
    db.add_all(
        [
            _issue("closed", external_id="1", state="closed"),
            _issue("open", external_id="2", state="open"),
        ]
    )
    db.commit()

    report = find_duplicate_candidates(
        _indexed_backend(db),
        GitHubIssueDraft(
            title="Telegram inbox attachment fails",
            body="File upload through Telegram is not processed by the universal inbox memory pipeline.",
        ),
        owner="alice",
        repository="fuzzy123-ai/odysseus-fuzzy",
        top_k=2,
    )

    assert [candidate.external_id for candidate in report.candidates] == ["2", "1"]


def test_high_confidence_candidate_blocks_auto_create():
    db = _session()
    db.add(_issue("one", external_id="1"))
    db.commit()

    report = find_duplicate_candidates(
        _indexed_backend(db),
        GitHubIssueDraft(
            title="Telegram inbox attachment fails",
            body="File upload through Telegram is not processed by the universal inbox memory pipeline.",
            labels=("area/inbox", "priority/high"),
        ),
        owner="alice",
        repository="fuzzy123-ai/odysseus-fuzzy",
    )

    assert report.blocks_auto_create is True
    assert report.candidates[0].blocks_auto_create is True
    assert report.candidates[0].score >= 850


def test_existing_draft_external_id_is_excluded():
    db = _session()
    db.add_all([_issue("one", external_id="1"), _issue("two", external_id="2")])
    db.commit()

    report = find_duplicate_candidates(
        _indexed_backend(db),
        GitHubIssueDraft(
            title="Telegram inbox attachment fails",
            body="File upload through Telegram is not processed by the universal inbox memory pipeline.",
            external_id="1",
        ),
        owner="alice",
        repository="fuzzy123-ai/odysseus-fuzzy",
        top_k=3,
    )

    assert [candidate.external_id for candidate in report.candidates] == ["2"]


def test_duplicate_candidates_can_be_recorded_without_overwriting_decisions():
    db = _session()
    source = _issue("source", external_id="10")
    candidate = _issue("candidate", external_id="11")
    db.add_all([source, candidate])
    db.commit()
    report = find_duplicate_candidates(
        _indexed_backend(db),
        GitHubIssueDraft(title=source.title, body=source.body, external_id=source.external_id),
        owner="alice",
        repository="fuzzy123-ai/odysseus-fuzzy",
    )

    written = record_duplicate_candidates(
        db,
        owner="alice",
        repository="fuzzy123-ai/odysseus-fuzzy",
        source_issue_id=source.id,
        report=report,
    )

    assert written == 1
    stored = db.query(GitHubIssueDuplicateCandidate).one()
    assert stored.source_issue_id == source.id
    assert stored.candidate_issue_id == candidate.id
    assert stored.score == report.candidates[0].score
    assert stored.reason == report.candidates[0].reason

    stored.decision = "accepted"
    db.commit()
    assert record_duplicate_candidates(
        db,
        owner="alice",
        repository="fuzzy123-ai/odysseus-fuzzy",
        source_issue_id=source.id,
        report=report,
    ) == 0
    assert db.query(GitHubIssueDuplicateCandidate).one().decision == "accepted"
