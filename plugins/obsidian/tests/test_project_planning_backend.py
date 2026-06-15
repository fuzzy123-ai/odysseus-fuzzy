import json
import os
import sys
import tempfile

import pytest


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv("ODYSSEUS_ROOT", os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")))

for _p in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import plugin as obsidian_plugin
from backend.project_planning import (
    ProjectPlanApplyRequest,
    ProjectPlanRequest,
    ProjectPlanValidationError,
    apply_project_plan,
    build_project_plan,
    prepare_project_plan_for_apply,
)
from backend import routes as obsidian_routes


def test_prepare_project_plan_for_apply_rejects_unknown_selected_paths():
    with tempfile.TemporaryDirectory() as tmpdir:
        plan = build_project_plan(
            tmpdir,
            ProjectPlanRequest(
                target_folder="Projects/Demo",
                title="Demo",
                description="Test selective project plan apply.",
                kind="generic",
            ),
        )

        with pytest.raises(ProjectPlanValidationError, match="Selected project plan paths do not exist"):
            prepare_project_plan_for_apply(tmpdir, plan, selected_paths=["Projects/Demo/Missing.md"])


def test_apply_project_plan_selected_paths_skip_unselected_conflicts_and_relationships():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "Projects", "Demo"), exist_ok=True)
        with open(os.path.join(tmpdir, "Projects", "Demo", "00 Projektuebersicht.md"), "w", encoding="utf-8") as fh:
            fh.write("# Existing overview\n")

        plan = build_project_plan(
            tmpdir,
            ProjectPlanRequest(
                target_folder="Projects/Demo",
                title="Demo",
                description="Selective apply should not be blocked by unselected conflicts.",
                kind="generic",
            ),
        )
        selected = ["Projects/Demo/03 Entscheidungen.md"]

        prepared = prepare_project_plan_for_apply(tmpdir, plan, selected_paths=selected)
        assert [planned.path for planned in prepared.files] == selected
        assert prepared.conflicts == []
        assert prepared.relationships == []
        assert any("selected project files" in warning for warning in prepared.warnings)

        result = apply_project_plan(tmpdir, plan, selected_paths=selected)

        assert result["created_files"] == selected
        assert result["relationships"] == []
        assert os.path.exists(os.path.join(tmpdir, "Projects", "Demo", "03 Entscheidungen.md"))
        assert not os.path.exists(os.path.join(tmpdir, "Projects", "Demo", "01 Ziele.md"))


@pytest.mark.asyncio
async def test_project_plan_apply_tool_honors_selected_paths(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(obsidian_plugin, "get_vault_path_by_owner", lambda owner: tmpdir)

        preview = await obsidian_plugin.handle_project_plan_preview(json.dumps({
            "target_folder": "Projects/Demo",
            "title": "Demo App",
            "description": "Create only one approved planning file.",
            "kind": "generic",
        }), owner="alice")
        assert preview["exit_code"] == 0
        plan = json.loads(preview["output"])

        applied = await obsidian_plugin.handle_project_plan_apply(json.dumps({
            "plan": plan,
            "confirm": True,
            "selected_paths": ["Projects/Demo/03 Entscheidungen.md"],
        }), owner="alice")

        assert applied["exit_code"] == 0
        result = json.loads(applied["output"])
        assert result["created_files"] == ["Projects/Demo/03 Entscheidungen.md"]
        assert os.path.exists(os.path.join(tmpdir, "Projects", "Demo", "03 Entscheidungen.md"))
        assert not os.path.exists(os.path.join(tmpdir, "Projects", "Demo", "00 Projektuebersicht.md"))


@pytest.mark.asyncio
async def test_project_plan_session_apply_honors_selected_paths(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(obsidian_routes, "get_unlocked_vault_path", lambda request: tmpdir)
        monkeypatch.setattr(obsidian_routes, "current_owner", lambda request: "alice")

        created = await obsidian_routes.project_plan_session_create(
            obsidian_routes.ProjectPlanSessionCreateRequest(
                request=ProjectPlanRequest(
                    target_folder="Projects/Demo",
                    title="Demo",
                    description="Apply only the approved decision note.",
                    kind="generic",
                )
            ),
            object(),
        )
        plan = build_project_plan(
            tmpdir,
            ProjectPlanRequest(
                target_folder="Projects/Demo",
                title="Demo",
                description="Apply only the approved decision note.",
                kind="generic",
            ),
        )
        obsidian_routes._update_project_plan_session(
            tmpdir,
            created["id"],
            plan=plan.model_dump() if hasattr(plan, "model_dump") else plan.dict(),
            status="ready",
        )

        request = type("Request", (), {"state": type("State", (), {"api_token": False})()})()
        result = await obsidian_routes.project_plan_session_apply(
            created["id"],
            obsidian_routes.ProjectPlanSessionApplyRequest(
                confirm=True,
                selected_paths=["Projects/Demo/03 Entscheidungen.md"],
            ),
            request,
        )

        assert result["success"] is True
        assert result["created_files"] == ["Projects/Demo/03 Entscheidungen.md"]
        assert result["session"]["status"] == "created"
        assert os.path.exists(os.path.join(tmpdir, "Projects", "Demo", "03 Entscheidungen.md"))
        assert not os.path.exists(os.path.join(tmpdir, "Projects", "Demo", "00 Projektuebersicht.md"))


def test_project_plan_apply_tool_schema_mentions_selected_paths():
    class _Ctx:
        def __init__(self):
            self.tools = []
            self.logger = type("Logger", (), {"warning": staticmethod(lambda *args, **kwargs: None)})()

        def add_router(self, router):
            return None

        def register_tool(self, spec):
            self.tools.append(spec)

    ctx = _Ctx()
    obsidian_plugin.setup(ctx)
    apply_tool = next(tool for tool in ctx.tools if tool["name"] == "obsidian_project_plan_apply")
    properties = apply_tool["schema"]["function"]["parameters"]["properties"]

    assert "selected_paths" in properties
    assert properties["selected_paths"]["type"] == "array"
