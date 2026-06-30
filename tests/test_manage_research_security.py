import asyncio
import json

from src import tool_implementations as ti
from src.tool_domains import media_research_contacts as mrc


def _write_report(root, rid, **payload):
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{rid}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_manage_research_delete_requires_confirmation(tmp_path, monkeypatch):
    data_dir = tmp_path / "deep_research"
    report = _write_report(
        data_dir,
        "alice-report",
        owner="alice",
        query="Alice research",
        result="Alice body",
    )
    monkeypatch.setattr(mrc, "DEEP_RESEARCH_DIR", str(data_dir))

    result = asyncio.run(ti.do_manage_research(
        json.dumps({"action": "delete", "id": "alice-report"}),
        owner="alice",
    ))

    assert result["status"] == "confirmation_required"
    assert result["requires_confirmation"] is True
    assert report.exists()


def test_manage_research_filters_list_and_read_by_owner(tmp_path, monkeypatch):
    data_dir = tmp_path / "deep_research"
    _write_report(data_dir, "alice-report", owner="alice", query="Alice", result="Alice body", completed_at=2)
    _write_report(data_dir, "bob-report", owner="bob", query="Bob", result="Bob body", completed_at=3)
    _write_report(data_dir, "legacy-report", query="Legacy", result="Legacy body", completed_at=4)
    monkeypatch.setattr(mrc, "DEEP_RESEARCH_DIR", str(data_dir))

    listed = asyncio.run(ti.do_manage_research(json.dumps({"action": "list"}), owner="alice"))
    output = listed["output"]
    assert "Alice" in output
    assert "Bob" not in output
    assert "Legacy" not in output

    bob_read = asyncio.run(ti.do_manage_research(
        json.dumps({"action": "read", "id": "bob-report"}),
        owner="alice",
    ))
    assert bob_read["error"] == "Research 'bob-report' not found."

    legacy_read = asyncio.run(ti.do_manage_research(
        json.dumps({"action": "read", "id": "legacy-report"}),
        owner="alice",
    ))
    assert legacy_read["error"] == "Research 'legacy-report' not found."


def test_manage_research_delete_respects_owner_scope(tmp_path, monkeypatch):
    data_dir = tmp_path / "deep_research"
    bob_report = _write_report(
        data_dir,
        "bob-report",
        owner="bob",
        query="Bob",
        result="Bob body",
    )
    alice_report = _write_report(
        data_dir,
        "alice-report",
        owner="alice",
        query="Alice",
        result="Alice body",
    )
    monkeypatch.setattr(mrc, "DEEP_RESEARCH_DIR", str(data_dir))

    cross_owner = asyncio.run(ti.do_manage_research(
        json.dumps({"action": "delete", "id": "bob-report", "confirmed": True}),
        owner="alice",
    ))
    assert cross_owner["error"] == "Research 'bob-report' not found."
    assert bob_report.exists()

    own_delete = asyncio.run(ti.do_manage_research(
        json.dumps({"action": "delete", "id": "alice-report", "confirmed": True}),
        owner="alice",
    ))
    assert own_delete["exit_code"] == 0
    assert not alice_report.exists()
