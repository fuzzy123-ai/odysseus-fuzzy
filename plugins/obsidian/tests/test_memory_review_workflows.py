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
from backend.memory_review import (
    MemoryReviewPlan,
    MemoryReviewRequest,
    MemoryReviewValidationError,
    build_memory_review_plan,
    validate_memory_review_plan,
)
from plugin import (
    handle_graph,
    handle_history,
    handle_memory_review_apply,
    handle_memory_review_preview,
)


def test_memory_review_preview_reuses_tags_links_and_validates_schema():
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "Projects"), exist_ok=True)
        with open(os.path.join(tmpdir, "Projects", "Demo.md"), "w", encoding="utf-8") as f:
            f.write("# Demo\n\n#project/demo #type/project\n\nGraph memory review context.")

        plan = build_memory_review_plan(tmpdir, MemoryReviewRequest(
            candidate={
                "title": "Graph memory decision",
                "content": "Memory review should save graph decisions into Demo context.",
                "source": "chat",
                "source_ref": "thread-123",
            },
            action="save_to_obsidian",
            target_folder="Memory Review",
            note_type="decision",
            project="Demo",
            tags=["#project/demo"],
            link_paths=["Projects/Demo.md"],
        ))

        assert plan.action == "save_to_obsidian"
        assert plan.conflicts == []
        assert plan.files[0].path.startswith("Memory Review/")
        assert plan.files[0].frontmatter["source"] == "chat"
        assert "#project/demo" in plan.files[0].tags
        assert "#type/decision" in plan.files[0].tags
        assert "[[Projects/Demo]]" in plan.files[0].links
        assert any(item.path == "Projects/Demo.md" for item in plan.suggested_notes)
        assert plan.relationships[0].target == "Projects/Demo.md"

        payload = plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()
        bad = MemoryReviewPlan(**payload)
        bad.files[0].path = "../escape.md"
        with pytest.raises(MemoryReviewValidationError):
            validate_memory_review_plan(tmpdir, bad)

        bad = MemoryReviewPlan(**payload)
        bad.files[0].tags = ["#memory", "#status/review"]
        with pytest.raises(MemoryReviewValidationError):
            validate_memory_review_plan(tmpdir, bad)


@pytest.mark.asyncio
async def test_memory_review_tools_apply_create_append_history_and_graph(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: tmpdir)
        os.makedirs(os.path.join(tmpdir, "Projects"), exist_ok=True)
        with open(os.path.join(tmpdir, "Projects", "Demo.md"), "w", encoding="utf-8") as f:
            f.write("# Demo\n\n#project/demo\n\nExisting graph context.")

        preview = await handle_memory_review_preview(json.dumps({
            "candidate": {
                "title": "Demo retention decision",
                "content": "Keep the memory review workflow linked to Demo.",
                "source": "chat",
                "source_ref": "chat:42",
            },
            "action": "save_to_obsidian",
            "target_folder": "Memory Review",
            "note_type": "decision",
            "project": "Demo",
            "tags": ["#project/demo"],
            "link_paths": ["Projects/Demo.md"],
        }), owner="alice")
        assert preview["exit_code"] == 0
        plan = json.loads(preview["output"])
        assert plan["files"][0]["links"] == ["[[Projects/Demo]]"]

        blocked = await handle_memory_review_apply(json.dumps({"plan": plan}), owner="alice")
        assert blocked["exit_code"] == 1
        assert "Confirmation required" in blocked["error"]

        applied = await handle_memory_review_apply(json.dumps({"plan": plan, "confirm": True}), owner="alice")
        assert applied["exit_code"] == 0
        result = json.loads(applied["output"])
        created_path = result["created_files"][0]
        assert os.path.exists(os.path.join(tmpdir, created_path.replace("/", os.sep)))

        graph_res = await handle_graph("{}", owner="alice")
        graph = json.loads(graph_res["output"])["graph"]
        edge_types = {edge["type"] for edge in graph["edges"]}
        assert "wiki_link" in edge_types
        assert "relates_to" in edge_types

        append_preview = await handle_memory_review_preview(json.dumps({
            "candidate": {
                "title": "Append insight",
                "content": "Append this insight to Demo instead of creating another note.",
                "source": "manual",
            },
            "action": "append_to_note",
            "target_note": "Projects/Demo.md",
            "tags": ["#project/demo"],
        }), owner="alice")
        append_plan = json.loads(append_preview["output"])
        append_res = await handle_memory_review_apply(json.dumps({"plan": append_plan, "confirm": True}), owner="alice")
        assert append_res["exit_code"] == 0
        with open(os.path.join(tmpdir, "Projects", "Demo.md"), "r", encoding="utf-8") as f:
            assert "Append this insight" in f.read()

        memory_only = await handle_memory_review_preview(json.dumps({
            "candidate": {"content": "Keep only in Odysseus memory.", "source": "chat"},
            "action": "memory_only",
        }), owner="alice")
        memory_plan = json.loads(memory_only["output"])
        memory_result = await handle_memory_review_apply(json.dumps({"plan": memory_plan}), owner="alice")
        assert memory_result["exit_code"] == 0
        assert json.loads(memory_result["output"])["created_files"] == []

        queue_preview = await handle_memory_review_preview(json.dumps({
            "candidate": {
                "title": "Queue this memory",
                "content": "This needs a later storage decision before it becomes settled knowledge.",
                "source": "chat",
            },
            "action": "review_queue",
            "project": "Demo",
            "tags": ["#project/demo"],
            "link_paths": ["Projects/Demo.md"],
        }), owner="alice")
        assert queue_preview["exit_code"] == 0
        queue_plan = json.loads(queue_preview["output"])
        assert queue_plan["action"] == "review_queue"
        assert queue_plan["files"][0]["path"].startswith("AI Memory/Review Queue/")

        queue_blocked = await handle_memory_review_apply(json.dumps({"plan": queue_plan}), owner="alice")
        assert queue_blocked["exit_code"] == 1
        assert "Confirmation required" in queue_blocked["error"]

        queue_applied = await handle_memory_review_apply(json.dumps({"plan": queue_plan, "confirm": True}), owner="alice")
        assert queue_applied["exit_code"] == 0
        queue_result = json.loads(queue_applied["output"])
        queue_path = queue_result["created_files"][0]
        with open(os.path.join(tmpdir, *queue_path.split("/")), "r", encoding="utf-8") as f:
            queue_content = f.read()
        assert "later storage decision" in queue_content

        history_res = await handle_history('{"limit": 20}', owner="alice")
        assert "obsidian_memory_review_apply" in history_res["output"]


@pytest.mark.asyncio
async def test_memory_review_apply_route_conflicts_return_409_before_writes(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, "Projects"), exist_ok=True)
        with open(os.path.join(tmpdir, "Projects", "Demo.md"), "w", encoding="utf-8") as f:
            f.write("# Demo\n\n#project/demo\n")

        plan = build_memory_review_plan(tmpdir, MemoryReviewRequest(
            candidate={
                "title": "Demo retention decision",
                "content": "Keep the memory review workflow linked to Demo.",
                "source": "chat",
            },
            action="save_to_obsidian",
            target_folder="Memory Review",
            note_type="decision",
            project="Demo",
            tags=["#project/demo"],
            link_paths=["Projects/Demo.md"],
        ))
        conflict_path = os.path.join(tmpdir, *plan.files[0].path.split("/"))
        os.makedirs(os.path.dirname(conflict_path), exist_ok=True)
        with open(conflict_path, "w", encoding="utf-8") as f:
            f.write("# Existing conflict target\n")

        plan_payload = plan.model_dump() if hasattr(plan, "model_dump") else plan.dict()
        request = SimpleNamespace(state=SimpleNamespace(api_token=False))

        monkeypatch.setattr(obsidian_routes, "get_unlocked_vault_path", lambda request: tmpdir)

        def fail_apply(*args, **kwargs):
            raise AssertionError("apply_memory_review_plan should not run when conflicts are present")

        monkeypatch.setattr(obsidian_routes, "apply_memory_review_plan", fail_apply)

        with pytest.raises(HTTPException) as exc:
            await obsidian_routes.memory_review_apply(
                obsidian_routes.MemoryReviewApplyRequest(plan=plan_payload, confirm=True),
                request,
            )

        assert exc.value.status_code == 409
        assert exc.value.detail["message"] == "Memory review plan has file conflicts"
        assert exc.value.detail["conflicts"]
        with open(conflict_path, "r", encoding="utf-8") as f:
            assert f.read() == "# Existing conflict target\n"
        assert not os.path.exists(os.path.join(tmpdir, ".obsidian", "history.json"))
