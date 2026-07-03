"""Duplicate candidate service for GitHub issue drafts.

The service consumes the repo-local issue index and produces compact, auditable
candidate previews. It does not create, close, label, or update GitHub issues.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any, Sequence

from sqlalchemy.orm import Session

from core.database import GitHubIssueDuplicateCandidate, GitHubIssueRecord
from src.github_issue_index import GitHubIssueIndexBackend, GitHubIssueIndexMatch, query_github_issue_index


DEFAULT_DUPLICATE_TOP_K = 3
DEFAULT_HIGH_CONFIDENCE_THRESHOLD = 850
_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True, slots=True)
class GitHubIssueDraft:
    title: str
    body: str = ""
    labels: tuple[str, ...] = ()
    external_id: str = ""


@dataclass(frozen=True, slots=True)
class GitHubIssueDuplicatePreview:
    external_id: str
    score: int
    reason: str
    state: str
    labels: tuple[str, ...]
    url: str
    blocks_auto_create: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "external_id": self.external_id,
            "score": self.score,
            "reason": self.reason,
            "state": self.state,
            "labels": list(self.labels),
            "url": self.url,
            "blocks_auto_create": self.blocks_auto_create,
        }


@dataclass(frozen=True, slots=True)
class GitHubIssueDuplicateReport:
    owner: str
    repository: str
    candidates: tuple[GitHubIssueDuplicatePreview, ...]
    blocks_auto_create: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "repository": self.repository,
            "blocks_auto_create": self.blocks_auto_create,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


def find_duplicate_candidates(
    backend: GitHubIssueIndexBackend,
    draft: GitHubIssueDraft,
    *,
    owner: str,
    repository: str,
    top_k: int = DEFAULT_DUPLICATE_TOP_K,
    include_closed: bool = True,
    high_confidence_threshold: int = DEFAULT_HIGH_CONFIDENCE_THRESHOLD,
) -> GitHubIssueDuplicateReport:
    safe_owner = _required_text(owner, field_name="owner")
    safe_repository = _required_text(repository, field_name="repository")
    normalized_draft = _normalize_draft(draft)
    requested = max(int(top_k), 0)
    raw_matches = query_github_issue_index(
        backend,
        _draft_query_text(normalized_draft),
        owner=safe_owner,
        repository=safe_repository,
        top_k=max(requested * 4, requested),
        include_closed=include_closed,
    )
    candidates = tuple(
        _preview_from_match(match, threshold=high_confidence_threshold)
        for match in _rank_matches(raw_matches, draft_external_id=normalized_draft.external_id)[:requested]
    )
    return GitHubIssueDuplicateReport(
        owner=safe_owner,
        repository=safe_repository,
        candidates=candidates,
        blocks_auto_create=any(candidate.blocks_auto_create for candidate in candidates),
    )


def record_duplicate_candidates(
    db: Session,
    *,
    owner: str,
    repository: str,
    source_issue_id: str,
    report: GitHubIssueDuplicateReport,
) -> int:
    """Persist local duplicate evidence for an existing source issue.

    This writes only to the Odysseus database. It intentionally preserves prior
    accepted/rejected decisions and updates only pending candidate evidence.
    """

    safe_owner = _required_text(owner, field_name="owner")
    safe_repository = _required_text(repository, field_name="repository")
    safe_source_id = _required_text(source_issue_id, field_name="source_issue_id")
    source = db.query(GitHubIssueRecord).filter_by(id=safe_source_id, owner=safe_owner, repository=safe_repository).one()
    written = 0
    for preview in report.candidates:
        candidate = (
            db.query(GitHubIssueRecord)
            .filter_by(
                owner=safe_owner,
                repository=safe_repository,
                external_id=preview.external_id,
            )
            .one_or_none()
        )
        if candidate is None or candidate.id == source.id:
            continue
        record_id = _candidate_record_id(safe_owner, safe_repository, source.id, candidate.id)
        existing = db.query(GitHubIssueDuplicateCandidate).filter_by(id=record_id).one_or_none()
        if existing is None:
            existing = GitHubIssueDuplicateCandidate(
                id=record_id,
                owner=safe_owner,
                repository=safe_repository,
                source_issue_id=source.id,
                candidate_issue_id=candidate.id,
            )
            db.add(existing)
        elif existing.decision != "pending":
            continue
        existing.score = preview.score
        existing.reason = preview.reason
        existing.decision = "pending"
        written += 1
    db.commit()
    return written


def _rank_matches(
    matches: Sequence[GitHubIssueIndexMatch],
    *,
    draft_external_id: str,
) -> tuple[GitHubIssueIndexMatch, ...]:
    filtered = [
        match
        for match in matches
        if _metadata_text(match, "external_id") and _metadata_text(match, "external_id") != draft_external_id
    ]
    return tuple(sorted(filtered, key=_rank_key))


def _rank_key(match: GitHubIssueIndexMatch) -> tuple[float, str]:
    state = _metadata_text(match, "state")
    open_bonus = 0.05 if state != "closed" else 0.0
    return (-(float(match.score) + open_bonus), _metadata_text(match, "external_id"))


def _preview_from_match(
    match: GitHubIssueIndexMatch,
    *,
    threshold: int,
) -> GitHubIssueDuplicatePreview:
    score = max(0, min(1000, int(round(float(match.score) * 1000))))
    state = _metadata_text(match, "state") or "open"
    labels = _metadata_labels(match.metadata.get("labels"))
    external_id = _metadata_text(match, "external_id")
    reason = _reason(external_id=external_id, score=score, state=state, labels=labels)
    return GitHubIssueDuplicatePreview(
        external_id=external_id,
        score=score,
        reason=reason,
        state=state,
        labels=labels,
        url=_metadata_text(match, "url"),
        blocks_auto_create=score >= threshold,
    )


def _reason(*, external_id: str, score: int, state: str, labels: tuple[str, ...]) -> str:
    label_text = ", ".join(labels[:3]) if labels else "no shared labels recorded"
    return f"possible duplicate of #{external_id}; score={score}; state={state}; labels={label_text}"


def _normalize_draft(draft: GitHubIssueDraft) -> GitHubIssueDraft:
    return GitHubIssueDraft(
        title=_required_text(draft.title, field_name="title"),
        body=_optional_text(draft.body),
        labels=tuple(label for label in (_optional_text(item) for item in draft.labels) if label),
        external_id=_optional_text(draft.external_id),
    )


def _draft_query_text(draft: GitHubIssueDraft) -> str:
    parts = [draft.title]
    if draft.labels:
        parts.append("labels: " + ", ".join(draft.labels))
    if draft.body:
        parts.append(draft.body)
    return "\n".join(parts)


def _metadata_text(match: GitHubIssueIndexMatch, key: str) -> str:
    return _optional_text(match.metadata.get(key))


def _metadata_labels(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(label for label in (_optional_text(item) for item in value) if label)


def _required_text(value: Any, *, field_name: str) -> str:
    text = _optional_text(value)
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _optional_text(value: Any) -> str:
    return _WHITESPACE_RE.sub(" ", str(value or "")).strip()


def _candidate_record_id(owner: str, repository: str, source_id: str, candidate_id: str) -> str:
    raw = f"{owner}\0{repository}\0{source_id}\0{candidate_id}".encode("utf-8")
    return f"ghdup_{hashlib.sha256(raw).hexdigest()[:32]}"
