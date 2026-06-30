from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routes.codex_helpers import (
    DOCS_WRITE_SCOPES,
    EMAIL_SEND_SCOPES,
    build_capabilities_payload,
    scope_owner,
    scope_owner_all,
)


def _token_request(*, owner="alice", scopes=None):
    return SimpleNamespace(
        state=SimpleNamespace(
            api_token=True,
            api_token_owner=owner,
            api_token_scopes=list(scopes or []),
        )
    )


def test_build_capabilities_payload_respects_token_scopes_and_availability():
    payload = build_capabilities_payload(
        token_scopes={"email:send", "documents:write"},
        has_token=True,
        memory_available=False,
        calendar_available=True,
        documents_available=True,
    )

    assert payload["integration"] == "codex"
    assert payload["tools"]["email"]["send"] is True
    assert payload["tools"]["email"]["read"] is True
    assert payload["tools"]["memory"]["read"] is False
    assert payload["tools"]["memory"]["available"] is False
    assert payload["tools"]["calendar"]["available"] is True
    assert payload["tools"]["documents"]["write"] is True


def test_scope_owner_accepts_any_allowed_scope_and_rejects_missing_scope():
    assert scope_owner(_token_request(scopes=["email:send"]), EMAIL_SEND_SCOPES) == "alice"

    with pytest.raises(HTTPException) as exc:
        scope_owner(_token_request(scopes=["memory:read"]), EMAIL_SEND_SCOPES)

    assert exc.value.status_code == 403
    assert "email:send" in exc.value.detail


def test_scope_owner_all_requires_every_scope():
    request = _token_request(scopes=["email:send", "documents:write"])

    assert scope_owner_all(request, EMAIL_SEND_SCOPES | DOCS_WRITE_SCOPES) == "alice"

    with pytest.raises(HTTPException) as exc:
        scope_owner_all(_token_request(scopes=["email:send"]), EMAIL_SEND_SCOPES | DOCS_WRITE_SCOPES)

    assert exc.value.status_code == 403
    assert "documents:write" in exc.value.detail
