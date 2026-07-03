from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from core.database import (
    Base,
    GitHubIssueDuplicateCandidate,
    GitHubIssueFieldValue,
    GitHubIssueRecord,
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
):
    return GitHubIssueRecord(
        id=issue_id,
        owner=owner,
        provider="github",
        repository=repository,
        external_id=external_id,
        external_node_id=f"node-{issue_id}",
        title=f"Issue {issue_id}",
        body="redacted test body",
        state="open",
        url=f"https://github.com/{repository}/issues/{external_id}",
        labels_json=["priority/high", "area/memory"],
        author="octocat",
    )


def test_issue_records_are_owner_scoped_and_json_labels_roundtrip():
    db = _session()
    db.add_all([
        _issue("alice-1", owner="alice", external_id="1"),
        _issue("bob-1", owner="bob", external_id="1"),
    ])
    db.commit()

    alice_issues = db.query(GitHubIssueRecord).filter_by(owner="alice").all()
    bob_issues = db.query(GitHubIssueRecord).filter_by(owner="bob").all()

    assert [issue.id for issue in alice_issues] == ["alice-1"]
    assert [issue.id for issue in bob_issues] == ["bob-1"]
    assert alice_issues[0].labels_json == ["priority/high", "area/memory"]


def test_field_values_roundtrip_as_json_and_cascade_with_issue():
    db = _session()
    issue = _issue("issue-1")
    issue.field_values.append(
        GitHubIssueFieldValue(
            id="field-1",
            owner="alice",
            field_name="priority",
            value_json={"value": "high", "source": "triage"},
            source="agent",
            confidence=92,
        )
    )
    db.add(issue)
    db.commit()

    stored = db.query(GitHubIssueFieldValue).one()
    assert stored.value_json == {"value": "high", "source": "triage"}
    assert stored.issue.id == "issue-1"

    db.delete(issue)
    db.commit()
    assert db.query(GitHubIssueFieldValue).count() == 0


def test_external_issue_identity_is_unique_per_owner_provider_repository():
    db = _session()
    db.add(_issue("issue-1", owner="alice", external_id="42"))
    db.commit()

    db.add(_issue("issue-duplicate", owner="alice", external_id="42"))
    with pytest.raises(IntegrityError):
        db.commit()

    db.rollback()
    db.add(_issue("issue-other-owner", owner="bob", external_id="42"))
    db.commit()

    assert db.query(GitHubIssueRecord).count() == 2


def test_duplicate_candidate_decision_preserves_audit_evidence():
    db = _session()
    source = _issue("source", external_id="10")
    candidate = _issue("candidate", external_id="11")
    db.add_all([source, candidate])
    db.commit()

    duplicate = GitHubIssueDuplicateCandidate(
        id="dup-1",
        owner="alice",
        repository="fuzzy123-ai/odysseus-fuzzy",
        source_issue_id=source.id,
        candidate_issue_id=candidate.id,
        score=914,
        reason="same traceback and area label",
    )
    db.add(duplicate)
    db.commit()

    duplicate.decision = "accepted"
    duplicate.decided_at = datetime(2026, 7, 3, 12, 0, 0)
    db.commit()

    stored = db.query(GitHubIssueDuplicateCandidate).one()
    assert stored.decision == "accepted"
    assert stored.score == 914
    assert stored.reason == "same traceback and area label"
    assert stored.source_issue.id == "source"
    assert stored.candidate_issue.id == "candidate"

    stored.decision = "rejected"
    db.commit()
    assert db.query(GitHubIssueDuplicateCandidate).one().decision == "rejected"
