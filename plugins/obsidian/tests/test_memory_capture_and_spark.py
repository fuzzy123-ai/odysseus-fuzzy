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

from backend import vault_service
from backend.memory_capture import (
    MemoryCaptureRequest,
    apply_memory_capture_plan,
    build_memory_capture_plan,
)
from backend.memory_spark import (
    SparkAnalyzeRequest,
    SparkApplyRequest,
    analyze_memory_health,
    apply_spark_plan,
    build_spark_plan,
)
from plugin import (
    handle_memory_capture_apply,
    handle_memory_capture_preview,
    handle_spark_apply,
    handle_spark_plan,
)


def test_memory_capture_preview_normalizes_without_writing():
    with tempfile.TemporaryDirectory() as tmpdir:
        req = MemoryCaptureRequest(
            content="Entscheidung: Externe KI nutzt Token -> User -> genau eine Vault.",
            source="agent",
            tags=["ai memory", "#obsidian"],
        )

        plan = build_memory_capture_plan(tmpdir, req)

        assert plan.kind == "decision"
        assert plan.action == "update_canonical"
        assert plan.target_path == "AI Memory/02 Entscheidungen.md"
        assert "#type/decision" in plan.tags
        assert not os.path.exists(os.path.join(tmpdir, "AI Memory"))


def test_memory_capture_apply_writes_confirmed_plan():
    with tempfile.TemporaryDirectory() as tmpdir:
        req = MemoryCaptureRequest(
            content="Regel: MCP-Clients duerfen keinen owner aus Tool-Argumenten setzen.",
            kind="rule",
            source="agent",
            confidence="high",
        )
        plan = build_memory_capture_plan(tmpdir, req)

        result = apply_memory_capture_plan(tmpdir, plan, owner="alice", actor={"source": "test"})

        assert result["success"] is True
        target = os.path.join(tmpdir, "AI Memory", "02 Entscheidungen.md")
        with open(target, "r", encoding="utf-8") as handle:
            content = handle.read()
        assert "MCP-Clients duerfen keinen owner" in content
        assert "type: canonical" in content


def test_memory_capture_routes_medium_duplicate_to_review_queue():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_service.create_file(
            tmpdir,
            "Existing.md",
            "# Token Vault Rule\n\nToken User Vault Zugriff ist sicherheitsrelevant.",
            owner="alice",
            tool="test",
        )

        plan = build_memory_capture_plan(
            tmpdir,
            MemoryCaptureRequest(
                title="Token Vault Rule",
                content="Token User Vault Zugriff ist sicherheitsrelevant.",
                kind="rule",
                source="agent",
            ),
        )

        assert plan.action in {"discard_duplicate", "review_queue"}
        assert plan.duplicate_candidates


def test_spark_analyze_and_plan_find_memory_health_actions():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_service.create_file(tmpdir, "Loose.md", "# Loose\n\nNo links yet. #memory", owner="alice", tool="test")

        health = analyze_memory_health(tmpdir, SparkAnalyzeRequest(limit=100))
        plan = build_spark_plan(tmpdir, SparkAnalyzeRequest(limit=100))

        assert health.total_notes == 1
        assert "Loose.md" in health.orphan_notes
        assert any(action.type == "update_canonical" for action in plan.actions)
        assert all(action.risk in {"low", "medium", "high"} for action in plan.actions)


def test_spark_apply_skips_high_risk_and_applies_selected_safe_actions():
    with tempfile.TemporaryDirectory() as tmpdir:
        vault_service.create_file(tmpdir, "Loose.md", "# Loose\n\nNo links yet. #memory", owner="alice", tool="test")
        plan = build_spark_plan(tmpdir, SparkAnalyzeRequest(limit=100))
        safe = next(action for action in plan.actions if action.operations and action.risk != "high")
        high = next((action for action in plan.actions if action.risk == "high"), None)
        selected = [safe.id] + ([high.id] if high else [])

        result = apply_spark_plan(
            tmpdir,
            SparkApplyRequest(plan=plan, confirm=True, selected_action_ids=selected),
            owner="alice",
            actor={"source": "test"},
        )

        assert result["success"] is True
        assert safe.id in result["applied_actions"]
        assert os.path.exists(os.path.join(tmpdir, safe.target_path.replace("/", os.sep)))


@pytest.mark.asyncio
async def test_memory_capture_and_spark_tool_apply_require_confirmation(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr("plugin.get_vault_path_by_owner", lambda owner: tmpdir)

        capture_preview = await handle_memory_capture_preview(json.dumps({
            "content": "Regel: Memory capture apply needs an explicit confirm gate.",
            "kind": "rule",
            "source": "agent",
            "confidence": "high",
        }), owner="alice")
        assert capture_preview["exit_code"] == 0
        capture_plan = json.loads(capture_preview["output"])

        blocked_capture = await handle_memory_capture_apply(json.dumps({"plan": capture_plan}), owner="alice")
        assert blocked_capture["exit_code"] == 1
        assert "Confirmation required" in blocked_capture["error"]
        assert not os.path.exists(os.path.join(tmpdir, "AI Memory"))

        confirmed_capture = await handle_memory_capture_apply(
            json.dumps({"plan": capture_plan, "confirm": True}),
            owner="alice",
        )
        assert confirmed_capture["exit_code"] == 0
        assert json.loads(confirmed_capture["output"])["success"] is True

        vault_service.create_file(tmpdir, "Loose.md", "# Loose\n\nNo links yet. #memory", owner="alice", tool="test")
        spark_plan = await handle_spark_plan('{"limit": 100}', owner="alice")
        assert spark_plan["exit_code"] == 0
        spark_payload = json.loads(spark_plan["output"])
        safe_action = next(
            action for action in spark_payload["actions"]
            if action["operations"] and action["risk"] != "high"
        )

        blocked_spark = await handle_spark_apply(json.dumps({
            "plan": spark_payload,
            "selected_action_ids": [safe_action["id"]],
        }), owner="alice")
        assert blocked_spark["exit_code"] == 1
        assert "Confirmation required" in blocked_spark["error"]

        confirmed_spark = await handle_spark_apply(json.dumps({
            "plan": spark_payload,
            "confirm": True,
            "selected_action_ids": [safe_action["id"]],
        }), owner="alice")
        assert confirmed_spark["exit_code"] == 0
        assert safe_action["id"] in json.loads(confirmed_spark["output"])["applied_actions"]
