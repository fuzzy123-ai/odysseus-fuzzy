from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.auth_helpers import require_api_token_scope, scoped_effective_user
from routes import chat_routes, session_routes


def _request(*, scopes=None, owner="alice", api_token=True):
    return SimpleNamespace(
        state=SimpleNamespace(
            current_user="browser-user" if not api_token else "api",
            api_token=api_token,
            api_token_owner=owner,
            api_token_scopes=list(scopes or []),
        )
    )


def test_require_api_token_scope_allows_cookie_callers():
    req = _request(api_token=False, scopes=[])

    require_api_token_scope(req, "chat")


def test_require_api_token_scope_blocks_wrong_scope():
    req = _request(scopes=["todos:read"])

    with pytest.raises(HTTPException) as exc:
        require_api_token_scope(req, "chat")

    assert exc.value.status_code == 403
    assert "chat" in exc.value.detail


def test_scoped_effective_user_uses_token_owner_when_scope_matches():
    req = _request(scopes=["chat"], owner="alice")

    assert scoped_effective_user(req, "chat") == "alice"


@pytest.mark.parametrize("resolver", [
    chat_routes._chat_effective_user,
    session_routes._chat_effective_user,
])
def test_chat_and_session_routes_require_chat_scope_for_api_tokens(resolver):
    req = _request(scopes=["todos:read"], owner="alice")

    with pytest.raises(HTTPException) as exc:
        resolver(req)

    assert exc.value.status_code == 403


@pytest.mark.parametrize("resolver", [
    chat_routes._chat_effective_user,
    session_routes._chat_effective_user,
])
def test_chat_and_session_routes_allow_chat_scoped_api_tokens(resolver):
    req = _request(scopes=["chat"], owner="alice")

    assert resolver(req) == "alice"
