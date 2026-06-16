import os
import sys
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv("ODYSSEUS_ROOT", os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")))

for _p in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backend.routes as obsidian_routes


@pytest.mark.asyncio
async def test_locked_vault_routes_return_423_across_plugin_surfaces(monkeypatch):
    request = SimpleNamespace(state=SimpleNamespace(api_token=False))

    def locked(_request):
        raise HTTPException(status_code=423, detail="Vault is locked")

    monkeypatch.setattr(obsidian_routes, "get_unlocked_vault_path", locked)

    route_calls = [
        lambda: obsidian_routes.list_files(request),
        lambda: obsidian_routes.search_vault("blob", request),
        lambda: obsidian_routes.list_tags(request),
        lambda: obsidian_routes.graph_vault(request),
        lambda: obsidian_routes.project_plan_templates(request),
        lambda: obsidian_routes.project_plan_sessions(request),
        lambda: obsidian_routes.memory_tree(request),
        lambda: obsidian_routes.memory_status_route(request),
        lambda: obsidian_routes.query_layer_status_route(request),
    ]

    for call in route_calls:
        with pytest.raises(HTTPException) as exc:
            await call()
        assert exc.value.status_code == 423
        assert exc.value.detail == "Vault is locked"
