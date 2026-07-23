import json
from pathlib import Path

from scripts.audit_unified_source_index_overlap import (
    Component,
    DirectWriter,
    ToolIdentity,
    audit_inventory,
    main,
)


ROOT = Path(__file__).resolve().parents[1]


def _write_python(root: Path, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _component(**overrides) -> Component:
    values = {
        "component_id": "fixture_store",
        "name": "Fixture store",
        "store_kind": "source_store",
        "classification": "domain_truth",
        "canonical_owner": "FixtureOwner",
        "source_paths": ("store.py",),
        "writer_policy": "read_only",
        "migration_action": "adapt_read_only",
    }
    values.update(overrides)
    return Component(**values)


def _audit_fixture(tmp_path: Path, **overrides) -> dict:
    values = {
        "components": (_component(),),
        "writers": (),
        "tools": (),
        "hotfiles": (),
        "boundaries": (),
        "risks": (),
    }
    values.update(overrides)
    return audit_inventory(tmp_path, **values)


def _codes(report: dict) -> set[str]:
    return {item["code"] for item in report["violations"]}


def test_repository_inventory_is_content_free_and_clean():
    report = audit_inventory(ROOT)

    assert report["summary"]["clean"] is True
    assert report["summary"]["private_corpus_accessed"] is False
    assert report["summary"]["runtime_modules_imported"] is False
    assert report["summary"]["component_count"] >= 35
    assert report["summary"]["direct_writer_count"] >= 85
    assert report["violations"] == []
    assert {item["classification"] for item in report["components"]} == {
        "domain_truth",
        "index_truth",
        "projection",
        "observation",
        "legacy",
    }
    assert next(item for item in report["tool_identities"] if item["tool_id"] == "query_knowledge")["state"] == "active"
    assert "nextcloud_intake_entry" in {
        item["boundary_id"] for item in report["non_store_boundaries"]
    }


def test_ownerless_store_is_rejected(tmp_path):
    _write_python(tmp_path, "store.py", "VALUE = 1\n")

    report = _audit_fixture(
        tmp_path,
        components=(_component(canonical_owner=""),),
    )

    assert "ownerless_store" in _codes(report)


def test_writer_required_store_without_writer_is_rejected(tmp_path):
    _write_python(tmp_path, "store.py", "class Store:\n    def save(self):\n        return None\n")

    report = _audit_fixture(
        tmp_path,
        components=(_component(writer_policy="required"),),
    )

    assert "undocumented_writer" in _codes(report)


def test_declared_writer_symbol_must_exist(tmp_path):
    _write_python(tmp_path, "store.py", "class Store:\n    def read(self):\n        return None\n")
    component = _component(writer_policy="required")
    writer = DirectWriter("fixture_store", "store.py", "Store.save", "persist fixture")

    report = _audit_fixture(tmp_path, components=(component,), writers=(writer,))

    assert "missing_writer_symbol" in _codes(report)


def test_duplicate_tool_identity_is_rejected(tmp_path):
    _write_python(tmp_path, "store.py", "VALUE = 1\n")
    _write_python(tmp_path, "tools.py", "TOOL = 'lookup'\n")
    tool = ToolIdentity(
        "lookup",
        "FixtureTaxonomy",
        "active",
        (("tools.py", "schema"),),
        "fixture lookup",
    )

    report = _audit_fixture(tmp_path, tools=(tool, tool))

    assert "duplicate_tool_identity" in _codes(report)


def test_planned_tool_must_remain_absent_from_runtime_surfaces(tmp_path):
    _write_python(tmp_path, "store.py", "VALUE = 1\n")
    _write_python(tmp_path, "tools.py", "TOOL = 'query_knowledge'\n")
    tool = ToolIdentity(
        "query_knowledge",
        "FixtureTaxonomy",
        "planned_absent",
        (("tools.py", "schema"),),
        "future lookup",
    )

    report = _audit_fixture(tmp_path, tools=(tool,))

    assert "planned_tool_already_present" in _codes(report)


def test_check_mode_detects_persisted_inventory_drift(tmp_path):
    output = tmp_path / "inventory.json"
    assert main(["--root", str(ROOT), "--output", str(output)]) == 0
    assert main(["--root", str(ROOT), "--output", str(output), "--check"]) == 0

    payload = json.loads(output.read_text(encoding="utf-8"))
    payload["summary"]["component_count"] = 0
    output.write_text(json.dumps(payload), encoding="utf-8")

    assert main(["--root", str(ROOT), "--output", str(output), "--check"]) == 1


def test_execution_claim_is_preserved_but_not_inferred(tmp_path):
    _write_python(tmp_path, "store.py", "VALUE = 1\n")
    execution = {"slice_id": "USI-00", "state": "running"}

    report = _audit_fixture(tmp_path, execution=execution)

    assert report["execution"] == execution
