import json
from urllib.error import HTTPError

import pytest

from src.github_issue_live_client import GitHubRestIssueReadClient
from src.github_issue_sync import GitHubIssueSyncError


class _Response:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def close(self):
        pass


def test_live_client_requires_server_auth_or_public_gate():
    with pytest.raises(GitHubIssueSyncError, match="credentials"):
        GitHubRestIssueReadClient()


def test_live_client_maps_github_issues_and_skips_pull_requests():
    seen = {}

    def opener(request, timeout):
        seen["url"] = request.full_url
        seen["auth"] = request.headers.get("Authorization")
        return _Response(
            [
                {
                    "number": 7,
                    "node_id": "NODE",
                    "title": "Inbox bug",
                    "body": "Public issue body",
                    "state": "open",
                    "labels": [{"name": "area/inbox"}],
                    "user": {"login": "octocat"},
                    "html_url": "https://github.example/issues/7",
                    "updated_at": "2026-07-05T10:11:12Z",
                },
                {
                    "number": 8,
                    "title": "PR should not sync",
                    "pull_request": {},
                },
            ]
        )

    client = GitHubRestIssueReadClient(
        token="ghp_server_side",
        max_items=5,
        opener=opener,
    )
    page = client.list_issues_page(repository="fuzzy123-ai/odysseus-fuzzy", since=None, cursor=None)

    assert "state=all" in seen["url"]
    assert seen["auth"] == "Bearer ghp_server_side"
    assert len(page.issues) == 1
    issue = page.issues[0]
    assert issue.external_id == "7"
    assert issue.title == "Inbox bug"
    assert issue.labels == ("area/inbox",)
    assert issue.author == "octocat"


def test_live_client_redacts_http_token_errors():
    def opener(request, timeout):
        raise HTTPError(
            request.full_url,
            401,
            "bad",
            {},
            _Response({"message": "bad Authorization: Bearer ghp_secret"}),
        )

    client = GitHubRestIssueReadClient(token="ghp_server_side", opener=opener)
    with pytest.raises(GitHubIssueSyncError) as exc_info:
        client.list_issues_page(repository="fuzzy123-ai/odysseus-fuzzy", since=None, cursor=None)

    message = str(exc_info.value)
    assert "ghp_secret" not in message
    assert "Bearer" not in message
    assert "[redacted-secret]" in message


def test_live_client_can_fallback_to_public_read_when_server_token_is_bad():
    seen_auth = []

    def opener(request, timeout):
        seen_auth.append(request.headers.get("Authorization"))
        if request.headers.get("Authorization"):
            raise HTTPError(
                request.full_url,
                401,
                "bad",
                {},
                _Response({"message": "bad Authorization: Bearer ghp_secret"}),
            )
        return _Response([{"number": 3, "title": "Public issue"}])

    client = GitHubRestIssueReadClient(
        token="ghp_server_side",
        allow_unauthenticated_public=True,
        opener=opener,
    )
    page = client.list_issues_page(repository="fuzzy123-ai/odysseus-fuzzy", since=None, cursor=None)

    assert seen_auth == ["Bearer ghp_server_side", None]
    assert client.auth_mode == "public_unauthenticated"
    assert [issue.external_id for issue in page.issues] == ["3"]


def test_live_client_rejects_non_slug_repository():
    client = GitHubRestIssueReadClient(allow_unauthenticated_public=True)
    with pytest.raises(GitHubIssueSyncError, match="owner/repo"):
        client.list_issues_page(repository="../bad", since=None, cursor=None)
