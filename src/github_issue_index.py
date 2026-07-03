"""Index contract for GitHub issue duplicate search.

This module prepares synced issue records for an embedding/vector backend
without selecting a live provider. The default in-memory backend is deterministic
and useful for tests, dry-runs and future duplicate-service fallbacks.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Protocol, Sequence

from sqlalchemy.orm import Session

from core.database import GitHubIssueRecord


DEFAULT_ISSUE_INDEX_TEXT_LIMIT = 8_000
_WHITESPACE_RE = re.compile(r"\s+")
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._-]*")


@dataclass(frozen=True, slots=True)
class GitHubIssueIndexDocument:
    id: str
    text: str
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class GitHubIssueIndexMatch:
    id: str
    score: float
    text: str
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class GitHubIssueReindexResult:
    owner: str
    repository: str
    indexed: int
    include_closed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner": self.owner,
            "repository": self.repository,
            "indexed": self.indexed,
            "include_closed": self.include_closed,
        }


class GitHubIssueIndexBackend(Protocol):
    def upsert_documents(self, documents: Sequence[GitHubIssueIndexDocument]) -> int:
        """Idempotently insert or replace issue documents."""

    def query(
        self,
        query_text: str,
        *,
        owner: str,
        repository: str,
        top_k: int,
        include_closed: bool,
    ) -> tuple[GitHubIssueIndexMatch, ...]:
        """Return matches constrained to owner/repository."""


class InMemoryGitHubIssueIndexBackend:
    """Small deterministic backend for repo-only tests and dry-runs."""

    def __init__(self) -> None:
        self._documents: dict[str, GitHubIssueIndexDocument] = {}

    def upsert_documents(self, documents: Sequence[GitHubIssueIndexDocument]) -> int:
        for document in documents:
            self._documents[document.id] = document
        return len(documents)

    def query(
        self,
        query_text: str,
        *,
        owner: str,
        repository: str,
        top_k: int,
        include_closed: bool,
    ) -> tuple[GitHubIssueIndexMatch, ...]:
        query_tokens = _tokens(query_text)
        matches: list[GitHubIssueIndexMatch] = []
        for document in self._documents.values():
            metadata = document.metadata
            if metadata.get("owner") != owner or metadata.get("repository") != repository:
                continue
            if not include_closed and metadata.get("state") == "closed":
                continue
            document_tokens = _tokens(document.text)
            overlap = len(query_tokens & document_tokens)
            if overlap <= 0:
                continue
            score = overlap / max(len(query_tokens), 1)
            matches.append(
                GitHubIssueIndexMatch(
                    id=document.id,
                    score=score,
                    text=document.text,
                    metadata=dict(metadata),
                )
            )
        matches.sort(key=lambda match: (-match.score, match.id))
        return tuple(matches[: max(int(top_k), 0)])

    def count(self) -> int:
        return len(self._documents)


def reindex_github_issues(
    db: Session,
    backend: GitHubIssueIndexBackend,
    *,
    owner: str,
    repository: str,
    include_closed: bool = True,
    text_limit: int = DEFAULT_ISSUE_INDEX_TEXT_LIMIT,
) -> GitHubIssueReindexResult:
    safe_owner = _required_text(owner, field_name="owner")
    safe_repository = _required_text(repository, field_name="repository")
    query = db.query(GitHubIssueRecord).filter(
        GitHubIssueRecord.owner == safe_owner,
        GitHubIssueRecord.repository == safe_repository,
    )
    if not include_closed:
        query = query.filter(GitHubIssueRecord.state != "closed")
    records = query.order_by(GitHubIssueRecord.external_id.asc(), GitHubIssueRecord.id.asc()).all()
    documents = tuple(
        build_issue_index_document(record, text_limit=text_limit)
        for record in records
    )
    indexed = backend.upsert_documents(documents)
    return GitHubIssueReindexResult(
        owner=safe_owner,
        repository=safe_repository,
        indexed=indexed,
        include_closed=include_closed,
    )


def query_github_issue_index(
    backend: GitHubIssueIndexBackend,
    query_text: str,
    *,
    owner: str,
    repository: str,
    top_k: int = 5,
    include_closed: bool = True,
) -> tuple[GitHubIssueIndexMatch, ...]:
    safe_query = _required_text(query_text, field_name="query_text")
    safe_owner = _required_text(owner, field_name="owner")
    safe_repository = _required_text(repository, field_name="repository")
    return backend.query(
        _compact_text(safe_query, limit=DEFAULT_ISSUE_INDEX_TEXT_LIMIT),
        owner=safe_owner,
        repository=safe_repository,
        top_k=max(int(top_k), 0),
        include_closed=include_closed,
    )


def build_issue_index_document(
    issue: GitHubIssueRecord,
    *,
    text_limit: int = DEFAULT_ISSUE_INDEX_TEXT_LIMIT,
) -> GitHubIssueIndexDocument:
    labels = _labels(issue.labels_json)
    external_id = _optional_text(issue.external_id) or issue.id
    text = "\n".join(
        part
        for part in (
            f"Issue {external_id}: {_optional_text(issue.title)}",
            f"state: {_optional_text(issue.state) or 'open'}",
            f"labels: {', '.join(labels)}" if labels else "",
            _optional_text(issue.body),
        )
        if part
    )
    metadata = {
        "owner": _required_text(issue.owner, field_name="owner"),
        "repository": _required_text(issue.repository, field_name="repository"),
        "provider": _optional_text(issue.provider) or "github",
        "external_id": external_id,
        "state": _optional_text(issue.state) or "open",
        "url": _optional_text(issue.url),
        "labels": labels,
    }
    return GitHubIssueIndexDocument(
        id=f"github_issue:{metadata['owner']}:{metadata['repository']}:{metadata['external_id']}",
        text=_compact_text(text, limit=text_limit),
        metadata=metadata,
    )


def _labels(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    labels: list[str] = []
    for item in value:
        text = _optional_text(item)
        if text and text not in labels:
            labels.append(text)
    return tuple(labels)


def _required_text(value: Any, *, field_name: str) -> str:
    text = _optional_text(value)
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _optional_text(value: Any) -> str:
    return _WHITESPACE_RE.sub(" ", str(value or "")).strip()


def _compact_text(value: Any, *, limit: int) -> str:
    text = _optional_text(value)
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)].rstrip() + "..."


def _tokens(value: str) -> set[str]:
    return set(_TOKEN_RE.findall(value.lower()))
