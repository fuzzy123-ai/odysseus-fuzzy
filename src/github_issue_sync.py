"""Read-only GitHub issue sync into Odysseus issue records.

The sync adapter is deliberately provider-IO agnostic. A caller supplies a
client with a paginated read method; this module validates and persists the
returned issue summaries without handling tokens or performing GitHub writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Protocol, Sequence

from sqlalchemy.orm import Session

from core.database import GitHubIssueRecord


_SECRET_RE = re.compile(
    r"(ghp_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|bearer\s+[A-Za-z0-9._-]+|authorization:\s*\S+)",
    re.IGNORECASE,
)


class GitHubIssueSyncError(RuntimeError):
    """Raised when a read-only issue sync fails with a redacted reason."""


@dataclass(frozen=True, slots=True)
class GitHubIssueSyncItem:
    external_id: str
    title: str
    body: str = ""
    state: str = "open"
    labels: tuple[str, ...] = ()
    author: str = ""
    url: str = ""
    external_node_id: str = ""
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class GitHubIssueSyncPage:
    issues: tuple[GitHubIssueSyncItem, ...]
    next_cursor: str | None = None


class GitHubIssueReadClient(Protocol):
    def list_issues_page(
        self,
        *,
        repository: str,
        since: datetime | None,
        cursor: str | None,
    ) -> GitHubIssueSyncPage:
        """Return one read-only page of issues."""


@dataclass(frozen=True, slots=True)
class GitHubIssueSyncResult:
    repository: str
    fetched: int
    created: int
    updated: int
    closed: int
    next_since: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "fetched": self.fetched,
            "created": self.created,
            "updated": self.updated,
            "closed": self.closed,
            "next_since": self.next_since.isoformat() if self.next_since else None,
        }


def sync_github_issues(
    db: Session,
    *,
    owner: str,
    repository: str,
    client: GitHubIssueReadClient,
    provider: str = "github",
    since: datetime | None = None,
    now: datetime | None = None,
) -> GitHubIssueSyncResult:
    """Sync provider issues into owner-scoped local records.

    ``since`` defaults to the newest local ``last_synced_at`` for the owner,
    provider and repository, which makes repeated runs incremental. The client
    remains responsible for applying the watermark to the provider query.
    """

    safe_owner = _clean_required(owner, field_name="owner")
    safe_repository = _clean_required(repository, field_name="repository")
    safe_provider = _clean_required(provider, field_name="provider")
    sync_started_at = _normalize_datetime(now) or _utcnow()
    watermark = _normalize_datetime(since) or _latest_sync_watermark(
        db,
        owner=safe_owner,
        provider=safe_provider,
        repository=safe_repository,
    )
    cursor: str | None = None
    fetched = 0
    created = 0
    updated = 0
    closed = 0
    newest_seen = watermark

    try:
        while True:
            page = client.list_issues_page(
                repository=safe_repository,
                since=watermark,
                cursor=cursor,
            )
            for item in page.issues:
                fetched += 1
                normalized = _normalize_item(item)
                existing = _find_issue(
                    db,
                    owner=safe_owner,
                    provider=safe_provider,
                    repository=safe_repository,
                    external_id=normalized.external_id,
                )
                if existing is None:
                    existing = GitHubIssueRecord(
                        id=_record_id(safe_owner, safe_provider, safe_repository, normalized.external_id),
                        owner=safe_owner,
                        provider=safe_provider,
                        repository=safe_repository,
                        external_id=normalized.external_id,
                    )
                    db.add(existing)
                    created += 1
                else:
                    updated += 1
                _apply_issue(existing, normalized, synced_at=sync_started_at)
                if existing.state == "closed":
                    closed += 1
                if normalized.updated_at and (newest_seen is None or normalized.updated_at > newest_seen):
                    newest_seen = normalized.updated_at
            if not page.next_cursor:
                break
            cursor = page.next_cursor
    except Exception as exc:  # pragma: no cover - exact client types vary
        db.rollback()
        raise GitHubIssueSyncError(_redact_error(str(exc))) from exc

    db.commit()
    return GitHubIssueSyncResult(
        repository=safe_repository,
        fetched=fetched,
        created=created,
        updated=updated,
        closed=closed,
        next_since=newest_seen or sync_started_at,
    )


def _latest_sync_watermark(
    db: Session,
    *,
    owner: str,
    provider: str,
    repository: str,
) -> datetime | None:
    return (
        db.query(GitHubIssueRecord.last_synced_at)
        .filter(
            GitHubIssueRecord.owner == owner,
            GitHubIssueRecord.provider == provider,
            GitHubIssueRecord.repository == repository,
            GitHubIssueRecord.last_synced_at.isnot(None),
        )
        .order_by(GitHubIssueRecord.last_synced_at.desc())
        .limit(1)
        .scalar()
    )


def _find_issue(
    db: Session,
    *,
    owner: str,
    provider: str,
    repository: str,
    external_id: str,
) -> GitHubIssueRecord | None:
    return (
        db.query(GitHubIssueRecord)
        .filter_by(
            owner=owner,
            provider=provider,
            repository=repository,
            external_id=external_id,
        )
        .one_or_none()
    )


def _apply_issue(
    record: GitHubIssueRecord,
    item: GitHubIssueSyncItem,
    *,
    synced_at: datetime,
) -> None:
    record.external_node_id = item.external_node_id or None
    record.title = item.title
    record.body = item.body or None
    record.state = item.state
    record.url = item.url or None
    record.labels_json = list(item.labels)
    record.author = item.author or None
    record.last_synced_at = item.updated_at or synced_at


def _normalize_item(item: GitHubIssueSyncItem) -> GitHubIssueSyncItem:
    return GitHubIssueSyncItem(
        external_id=_clean_required(item.external_id, field_name="external_id"),
        external_node_id=_clean_optional(item.external_node_id),
        title=_clean_required(item.title, field_name="title"),
        body=_clean_optional(item.body),
        state=_normalize_state(item.state),
        url=_clean_optional(item.url),
        author=_clean_optional(item.author),
        labels=_normalize_labels(item.labels),
        updated_at=_normalize_datetime(item.updated_at),
    )


def _normalize_labels(labels: Sequence[Any]) -> tuple[str, ...]:
    clean: list[str] = []
    for label in labels:
        text = _clean_optional(label)
        if text and text not in clean:
            clean.append(text)
    return tuple(clean)


def _normalize_state(value: Any) -> str:
    text = _clean_optional(value).lower()
    if text in {"closed", "done", "resolved"}:
        return "closed"
    return "open"


def _normalize_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _clean_required(value: Any, *, field_name: str) -> str:
    text = _clean_optional(value)
    if not text:
        raise GitHubIssueSyncError(f"{field_name} is required")
    return text


def _clean_optional(value: Any) -> str:
    return " ".join(str(value or "").split())


def _record_id(owner: str, provider: str, repository: str, external_id: str) -> str:
    raw = f"{owner}\0{provider}\0{repository}\0{external_id}".encode("utf-8")
    return f"ghissue_{hashlib.sha256(raw).hexdigest()[:32]}"


def _redact_error(message: str) -> str:
    text = _SECRET_RE.sub("[redacted-secret]", message or "")
    return text or "github issue sync failed"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
