"""Small live GitHub REST read client for issue sync.

The client is read-only and intentionally narrow: it lists issues for one
repository through GitHub's REST API and converts them into the provider-agnostic
sync item model. Authentication is supplied by server-side environment only.
"""

from __future__ import annotations

from datetime import datetime
import json
import re
from typing import Any, Callable
import urllib.error
import urllib.parse
import urllib.request

from src.github_issue_sync import GitHubIssueSyncError, GitHubIssueSyncItem, GitHubIssueSyncPage


_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class GitHubRestIssueReadClient:
    def __init__(
        self,
        *,
        token: str = "",
        allow_unauthenticated_public: bool = False,
        api_base: str = "https://api.github.com",
        max_items: int = 50,
        per_page: int = 100,
        timeout: int = 15,
        opener: Callable[[urllib.request.Request, int], Any] | None = None,
    ) -> None:
        self.token = str(token or "").strip()
        self.allow_unauthenticated_public = bool(allow_unauthenticated_public)
        self.api_base = str(api_base or "https://api.github.com").rstrip("/")
        self.max_items = max(0, min(int(max_items), 500))
        self.per_page = max(1, min(int(per_page), 100))
        self.timeout = max(1, min(int(timeout), 60))
        self._opener = opener or self._default_open
        self._returned = 0
        if not self.token and not self.allow_unauthenticated_public:
            raise GitHubIssueSyncError("server-side GitHub credentials are not configured")

    def list_issues_page(
        self,
        *,
        repository: str,
        since: datetime | None,
        cursor: str | None,
    ) -> GitHubIssueSyncPage:
        safe_repository = _validate_repository(repository)
        if self._returned >= self.max_items:
            return GitHubIssueSyncPage(issues=(), next_cursor=None)

        page = max(int(cursor or "1"), 1)
        remaining = max(self.max_items - self._returned, 0)
        per_page = min(self.per_page, remaining)
        params: dict[str, str] = {
            "state": "all",
            "per_page": str(per_page),
            "page": str(page),
            "sort": "updated",
            "direction": "asc",
        }
        if since is not None:
            params["since"] = since.isoformat(timespec="seconds") + "Z"
        url = f"{self.api_base}/repos/{safe_repository}/issues?{urllib.parse.urlencode(params)}"
        payload = self._request_json(url)
        if not isinstance(payload, list):
            raise GitHubIssueSyncError("GitHub issue sync returned an unexpected payload")

        issues: list[GitHubIssueSyncItem] = []
        for raw in payload:
            if not isinstance(raw, dict) or "pull_request" in raw:
                continue
            issues.append(_item_from_payload(raw))
        self._returned += len(issues)
        next_cursor = str(page + 1) if len(payload) >= per_page and self._returned < self.max_items else None
        return GitHubIssueSyncPage(issues=tuple(issues), next_cursor=next_cursor)

    def _request_json(self, url: str) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "odysseus-github-issue-sync",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with self._opener(request, self.timeout) as response:
                raw = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:240]
            raise GitHubIssueSyncError(f"GitHub issue sync failed with HTTP {exc.code}: {_redact(body)}") from exc
        except Exception as exc:
            raise GitHubIssueSyncError(_redact(str(exc))) from exc
        try:
            return json.loads(raw or "null")
        except json.JSONDecodeError as exc:
            raise GitHubIssueSyncError("GitHub issue sync returned invalid JSON") from exc

    @staticmethod
    def _default_open(request: urllib.request.Request, timeout: int) -> Any:
        return urllib.request.urlopen(request, timeout=timeout)


def _validate_repository(repository: str) -> str:
    safe_repository = str(repository or "").strip()
    if not _REPOSITORY_RE.fullmatch(safe_repository):
        raise GitHubIssueSyncError("repository must be an owner/repo slug")
    owner, name = safe_repository.split("/", 1)
    if owner in {".", ".."} or name in {".", ".."} or ".." in owner or ".." in name:
        raise GitHubIssueSyncError("repository must be an owner/repo slug")
    return safe_repository


def _item_from_payload(raw: dict[str, Any]) -> GitHubIssueSyncItem:
    labels = []
    for label in raw.get("labels") or []:
        if isinstance(label, dict) and label.get("name"):
            labels.append(str(label["name"]))
        elif isinstance(label, str):
            labels.append(label)
    return GitHubIssueSyncItem(
        external_id=str(raw.get("number") or raw.get("id") or "").strip(),
        external_node_id=str(raw.get("node_id") or "").strip(),
        title=str(raw.get("title") or "").strip(),
        body=str(raw.get("body") or ""),
        state=str(raw.get("state") or "open"),
        labels=tuple(labels),
        author=str((raw.get("user") or {}).get("login") or ""),
        url=str(raw.get("html_url") or ""),
        updated_at=_parse_github_datetime(raw.get("updated_at")),
    )


def _parse_github_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _redact(message: str) -> str:
    return re.sub(
        r"(ghp_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|bearer\s+[A-Za-z0-9._-]+|authorization:\s*\S+)",
        "[redacted-secret]",
        message or "",
        flags=re.IGNORECASE,
    )
