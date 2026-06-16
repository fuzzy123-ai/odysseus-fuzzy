import json
import os
import sys
import tempfile
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv("ODYSSEUS_ROOT", os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")))

for _p in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import backend.routes as obsidian_routes
from backend import vault_service
from backend.memory_capture import MemoryCaptureApplyRequest, MemoryCaptureRequest, build_memory_capture_plan
from backend.memory_review import MemoryReviewApplyRequest, MemoryReviewRequest, build_memory_review_plan
from backend.memory_spark import SparkAnalyzeRequest, SparkApplyRequest, build_spark_plan
from backend.project_planning import (
    GameDevConceptDraftRequest,
    ProjectDescriptionImproveRequest,
    ProjectPlanRequest,
    build_project_plan,
)
from backend.vault_security import lock_vault, set_password
from plugin import (
    handle_add_relationship,
    handle_graph,
    handle_history,
    handle_knowledge_audit,
    handle_list_notes,
    handle_list_tags,
    handle_memory_capture_apply,
    handle_memory_capture_preview,
    handle_memory_review_apply,
    handle_memory_review_preview,
    handle_memory_status,
    handle_memory_tree_analyze,
    handle_memory_tree_status,
    handle_project_plan_apply,
    handle_project_plan_gamedev_draft,
    handle_project_plan_improve_description,
    handle_project_plan_preview,
    handle_project_plan_templates,
    handle_quarantine_list,
    handle_raptor_status,
    handle_read_note,
    handle_search_notes,
    handle_spark_analyze,
    handle_spark_apply,
    handle_spark_plan,
    handle_write_note,
    handle_delete_note,
)


@pytest.mark.asyncio
async def test_locked_vault_blocks_ai_file_access(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: tmpdir)
        with open(os.path.join(tmpdir, "Project.md"), "w", encoding="utf-8") as f:
            f.write("# Project")
        set_password(tmpdir, "strong password")
        lock_vault(tmpdir)

        res = await handle_read_note('{"path": "Project.md"}')

        assert res["exit_code"] == 1
        assert "locked" in res["error"].lower()


@pytest.mark.asyncio
async def test_locked_vault_blocks_all_actions(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: tmpdir)
        project_plan = build_project_plan(
            tmpdir,
            ProjectPlanRequest(title="Proj", kind="software", description="desc"),
        )
        memory_review_plan = build_memory_review_plan(
            tmpdir,
            MemoryReviewRequest(candidate={"content": "memory"}, action="save_to_obsidian"),
        )
        memory_capture_plan = build_memory_capture_plan(
            tmpdir,
            MemoryCaptureRequest(content="capture me", source="agent"),
        )
        spark_plan = build_spark_plan(tmpdir, SparkAnalyzeRequest(limit=10))
        set_password(tmpdir, "strong password")
        lock_vault(tmpdir)

        checks = [
            handle_list_notes(""),
            handle_write_note('{"path": "test.md", "content": "hello"}'),
            handle_delete_note('{"path": "test.md", "confirm": true}'),
            handle_list_tags(""),
            handle_graph(""),
            handle_add_relationship('{"source": "a.md", "target": "b.md", "confirm": true}'),
            handle_search_notes('{"query": "hello"}'),
            handle_history(""),
            handle_project_plan_preview('{"title": "Proj", "kind": "software", "description": "desc"}'),
            handle_project_plan_templates(""),
            handle_project_plan_improve_description(json.dumps({
                "title": "Proj",
                "kind": "software",
                "description": "desc",
            })),
            handle_project_plan_gamedev_draft(json.dumps({
                "title": "Proj",
                "description": "desc",
                "kind": "game_dev",
            })),
            handle_project_plan_apply(json.dumps({
                "plan": project_plan.model_dump(),
                "confirm": True,
            })),
            handle_memory_review_preview('{"candidate": {"content": "memory"}, "action": "save_to_obsidian"}'),
            handle_memory_review_apply(json.dumps({
                "plan": memory_review_plan.model_dump(),
                "confirm": True,
            })),
            handle_memory_capture_preview(json.dumps({
                "content": "capture me",
                "source": "agent",
            })),
            handle_memory_capture_apply(json.dumps({
                "plan": memory_capture_plan.model_dump(),
                "confirm": True,
            })),
            handle_spark_analyze(json.dumps({"limit": 10})),
            handle_spark_plan(json.dumps({"limit": 10})),
            handle_spark_apply(json.dumps({
                "plan": spark_plan.model_dump(),
                "confirm": True,
                "selected_action_ids": [],
            })),
            handle_memory_status(""),
            handle_memory_tree_status(""),
            handle_memory_tree_analyze(json.dumps({"limit": 10})),
            handle_knowledge_audit(""),
            handle_quarantine_list(""),
            handle_raptor_status(""),
        ]

        for res in [await item for item in checks]:
            assert res["exit_code"] == 1
            assert "locked" in res["error"].lower()


@pytest.mark.asyncio
async def test_locked_vault_blocks_route_level_content_surfaces(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(vault_service, "vault_path_for_owner", lambda owner: tmpdir)
        with open(os.path.join(tmpdir, "Secret.md"), "w", encoding="utf-8") as f:
            f.write("# Secret\n\nhidden memory")
        set_password(tmpdir, "strong password")
        lock_vault(tmpdir)
        project_plan = build_project_plan(
            tmpdir,
            ProjectPlanRequest(title="Locked Project", kind="software", description="Should stay hidden."),
        )
        memory_review_plan = build_memory_review_plan(
            tmpdir,
            MemoryReviewRequest(candidate={"content": "locked memory"}, action="save_to_obsidian"),
        )
        memory_capture_plan = build_memory_capture_plan(
            tmpdir,
            MemoryCaptureRequest(content="locked capture", source="agent"),
        )
        spark_apply_request = SparkApplyRequest(
            plan=build_spark_plan(tmpdir, SparkAnalyzeRequest(limit=10)),
            confirm=True,
            selected_action_ids=[],
        )
        request = SimpleNamespace(
            state=SimpleNamespace(current_user="alice", api_token=False),
            app=SimpleNamespace(state=SimpleNamespace(auth_manager=None)),
            client=SimpleNamespace(host="127.0.0.1"),
        )

        async def assert_locked(awaitable):
            with pytest.raises(HTTPException) as exc:
                await awaitable
            assert exc.value.status_code == 423
            assert "locked" in str(exc.value.detail).lower()

        await assert_locked(obsidian_routes.list_files(request))
        await assert_locked(obsidian_routes.search_vault("hidden", request))
        await assert_locked(obsidian_routes.list_tags(request))
        await assert_locked(obsidian_routes.graph_vault(request))
        await assert_locked(obsidian_routes.project_plan_templates(request))
        await assert_locked(obsidian_routes.project_plan_sessions(request))
        await assert_locked(obsidian_routes.project_plan_session_get("missing", request))
        await assert_locked(obsidian_routes.project_plan_session_delete("missing", request))
        await assert_locked(obsidian_routes.project_plan_session_create(
            obsidian_routes.ProjectPlanSessionCreateRequest(
                request=ProjectPlanRequest(
                    title="Locked Project",
                    kind="software",
                    description="Should not inspect a locked vault.",
                )
            ),
            request,
        ))
        await assert_locked(obsidian_routes.project_plan_session_preview_stream(
            "missing",
            obsidian_routes.ProjectPlanSessionPreviewRequest(
                request=ProjectPlanRequest(
                    title="Locked Project",
                    kind="software",
                    description="Should not inspect a locked vault.",
                )
            ),
            request,
        ))
        await assert_locked(obsidian_routes.project_plan_session_apply(
            "missing",
            obsidian_routes.ProjectPlanSessionApplyRequest(plan=project_plan, confirm=True),
            request,
        ))
        await assert_locked(obsidian_routes.project_plan_preview(ProjectPlanRequest(
            title="Locked Project",
            kind="software",
            description="Should not inspect a locked vault.",
        ), request))
        await assert_locked(obsidian_routes.project_plan_preview_stream(ProjectPlanRequest(
            title="Locked Project",
            kind="software",
            description="Should not inspect a locked vault.",
        ), request))
        await assert_locked(obsidian_routes.project_plan_improve_description(
            ProjectDescriptionImproveRequest(
                title="Locked Project",
                kind="software",
                description="Should not inspect a locked vault.",
            ),
            request,
        ))
        await assert_locked(obsidian_routes.project_plan_gamedev_draft(
            GameDevConceptDraftRequest(
                title="Locked Project",
                description="Should not inspect a locked vault.",
                kind="game_dev",
            ),
            request,
        ))
        await assert_locked(obsidian_routes.project_plan_apply(
            obsidian_routes.ProjectPlanApplyRequest(plan=project_plan, confirm=True),
            request,
        ))
        await assert_locked(obsidian_routes.memory_review_preview(MemoryReviewRequest(
            candidate={"content": "locked memory"},
            action="save_to_obsidian",
        ), request))
        await assert_locked(obsidian_routes.memory_review_apply(
            MemoryReviewApplyRequest(plan=memory_review_plan, confirm=True),
            request,
        ))
        await assert_locked(obsidian_routes.memory_capture_preview(MemoryCaptureRequest(
            content="locked capture",
            source="agent",
        ), request))
        await assert_locked(obsidian_routes.memory_capture_apply(
            MemoryCaptureApplyRequest(plan=memory_capture_plan, confirm=True),
            request,
        ))
        await assert_locked(obsidian_routes.spark_analyze(SparkAnalyzeRequest(limit=10), request))
        await assert_locked(obsidian_routes.spark_plan(SparkAnalyzeRequest(limit=10), request))
        await assert_locked(obsidian_routes.spark_apply(spark_apply_request, request))
        await assert_locked(obsidian_routes.memory_tree(request))
        await assert_locked(obsidian_routes.memory_status_route(request))
        await assert_locked(obsidian_routes.memory_tree_analyze(request, limit=10))
        await assert_locked(obsidian_routes.knowledge_audit(request))
        await assert_locked(obsidian_routes.quarantine(request))
        await assert_locked(obsidian_routes.raptor_status_route(request))
