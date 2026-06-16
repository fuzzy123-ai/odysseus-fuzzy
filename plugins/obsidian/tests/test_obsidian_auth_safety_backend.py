import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv(
    "ODYSSEUS_ROOT",
    os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")),
)

for _p in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backend.routes as obsidian_routes


def _token_request(scopes, owner="alice", current_user="mallory"):
    return SimpleNamespace(
        state=SimpleNamespace(
            api_token=True,
            api_token_owner=owner,
            api_token_scopes=scopes,
            current_user=current_user,
            api_token_id="tok_123",
            api_token_prefix="ody",
        )
    )


def test_current_owner_prefers_api_token_owner_over_session_user():
    request = _token_request(["vault:read"], owner="alice", current_user="mallory")

    assert obsidian_routes.current_owner(request) == "alice"


@pytest.mark.asyncio
async def test_query_layer_route_passes_token_owner(monkeypatch):
    seen = {}

    monkeypatch.setattr(obsidian_routes, "get_unlocked_vault_path", lambda _request: "vault")

    async def fake_answer_query_async(vault_dir, q, **kwargs):
        seen["vault_dir"] = vault_dir
        seen["query"] = q
        seen["owner"] = kwargs["owner"]
        return {"answer": "ok", "citations": [], "summary": {}, "answer_mode": "extractive"}

    monkeypatch.setattr(obsidian_routes, "answer_query_async", fake_answer_query_async)

    result = await obsidian_routes.query_layer_route(
        _token_request(["vault:read"], owner="alice", current_user="mallory"),
        "blob path",
        top_k=3,
        path_prefix="",
        answer_mode="auto",
    )

    assert result["answer"] == "ok"
    assert seen == {"vault_dir": "vault", "query": "blob path", "owner": "alice"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route_name", "kwargs"),
    [
        ("memory_automation_run_route", {"force": False}),
        ("rebuild_proof_run_route", {"q": "blob path", "top_k": 3}),
        (
            "external_upgrade_proof_run_route",
            {"q": "blob path", "top_k": 3, "path_prefix": "", "export_password": "pw"},
        ),
    ],
)
async def test_write_routes_check_scope_before_unlock(monkeypatch, route_name, kwargs):
    def fail_if_unlocked(_request):
        raise AssertionError("write scope should be validated before touching the vault")

    monkeypatch.setattr(obsidian_routes, "get_unlocked_vault_path", fail_if_unlocked)

    route = getattr(obsidian_routes, route_name)
    with pytest.raises(HTTPException) as exc:
        await route(_token_request(["vault:read"]), **kwargs)

    assert exc.value.status_code == 403
    assert exc.value.detail == "API token missing required scope: vault:write"
