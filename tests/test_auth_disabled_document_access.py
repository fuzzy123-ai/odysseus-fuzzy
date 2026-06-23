import pytest
from fastapi import HTTPException

from routes.document_helpers import _verify_doc_owner


class _Doc:
    owner = "alice"
    session_id = "session-1"


def test_verify_doc_owner_allows_none_user_when_auth_disabled(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    _verify_doc_owner(None, _Doc(), None)


def test_verify_doc_owner_rejects_none_user_when_auth_enabled(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    with pytest.raises(HTTPException) as exc_info:
        _verify_doc_owner(None, _Doc(), None)
    assert exc_info.value.status_code == 403


def test_verify_doc_owner_rejects_wrong_owner(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "true")
    with pytest.raises(HTTPException) as exc_info:
        _verify_doc_owner(None, _Doc(), "bob")
    assert exc_info.value.status_code == 404
