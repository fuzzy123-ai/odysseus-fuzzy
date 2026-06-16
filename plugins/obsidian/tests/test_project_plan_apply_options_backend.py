import os
import sys
import tempfile
from types import SimpleNamespace

import pytest


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv("ODYSSEUS_ROOT", os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")))

for _p in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backend.routes as obsidian_routes
from backend.project_planning import ProjectPlanRequest, build_project_plan


@pytest.mark.asyncio
async def test_project_plan_apply_route_normalizes_overwrite_paths_before_conflict_gate(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "Projects", "Demo"), exist_ok=True)
        conflict_path = os.path.join(tmpdir, "Projects", "Demo", "00 Projektuebersicht.md")
        with open(conflict_path, "w", encoding="utf-8") as fh:
            fh.write("# Existing overview\n")

        plan = build_project_plan(
            tmpdir,
            ProjectPlanRequest(
                target_folder="Projects/Demo",
                title="Demo",
                description="Allow overwrite through normalized route inputs.",
                kind="software",
            ),
        )
        request = SimpleNamespace(state=SimpleNamespace(api_token=False))

        monkeypatch.setattr(obsidian_routes, "get_unlocked_vault_path", lambda _request: tmpdir)
        monkeypatch.setattr(obsidian_routes, "current_owner", lambda _request: "alice")
        monkeypatch.setattr(obsidian_routes, "_require_vault_scope", lambda _request, _scope: "alice")

        result = await obsidian_routes.project_plan_apply(
            obsidian_routes.ProjectPlanApplyRequest(
                plan=plan,
                confirm=True,
                confirm_conflicts=True,
                overwrite_paths=["  Projects\\Demo\\00 Projektuebersicht.md  "],
            ),
            request,
        )

        assert result["success"] is True
        assert result["overwritten_files"] == ["Projects/Demo/00 Projektuebersicht.md"]
