import ast
import hashlib
import importlib.util
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "audit_unified_source_index_runtime.py"
INVENTORY = REPO_ROOT / "docs" / "plans" / "unified-source-index-runtime-caller-inventory.json"


def _audit_module():
    spec = importlib.util.spec_from_file_location("usi_runtime_audit", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _callers(inventory):
    return [dict(zip(inventory["caller_fields"], row, strict=True)) for row in inventory["callers"]]


def test_committed_runtime_inventory_matches_deterministic_static_ast_scan():
    audit = _audit_module()
    expected = audit.build_inventory(REPO_ROOT)
    actual = json.loads(INVENTORY.read_text(encoding="utf-8"))
    assert actual == expected
    assert actual["summary"]["unclassified_count"] == 0
    assert actual["scan"]["imports_executed"] is False
    assert actual["scan"]["private_sources_read"] is False
    assert actual["scan"]["source_bytes_normalization"] == "lf"


def test_canonical_source_bytes_ignore_newline_encoding_for_hash_marker_and_ast():
    audit = _audit_module()
    lf = b"def rag_probe():\n    return rag_manager.search('safe')\n"
    crlf = lf.replace(b"\n", b"\r\n")
    lone_cr = lf.replace(b"\n", b"\r")

    canonical_lf = audit._canonical_source_bytes(lf)
    canonical_crlf = audit._canonical_source_bytes(crlf)
    canonical_lone_cr = audit._canonical_source_bytes(lone_cr)
    assert canonical_lf == canonical_crlf == canonical_lone_cr == lf
    assert (
        hashlib.sha256(canonical_lf).hexdigest()
        == hashlib.sha256(canonical_crlf).hexdigest()
        == hashlib.sha256(canonical_lone_cr).hexdigest()
    )
    assert any(marker in canonical_lf for marker in audit.AST_MARKERS)
    assert ast.dump(ast.parse(canonical_lf), include_attributes=False) == ast.dump(
        ast.parse(canonical_crlf), include_attributes=False
    ) == ast.dump(ast.parse(canonical_lone_cr), include_attributes=False)


def test_inventory_covers_required_runtime_categories_and_dynamic_boundaries():
    audit = _audit_module()
    inventory = audit.build_inventory(REPO_ROOT)
    callers = _callers(inventory)
    categories = set(inventory["summary"]["category_counts"])
    assert {
        "composition_startup", "active_read", "active_write", "lifecycle_owner",
        "compatibility_fallback", "generic_context_boundary", "health_diagnostics",
        "route_admin", "backend_implementation", "scheduler_excluded", "background_excluded",
    } <= categories
    dynamic = [item for item in callers if item["call_type"] in {"dynamic_getattr", "provider_retrieve"}]
    assert dynamic
    assert inventory["classification_rules"]["generic_context_boundary"] == "generic provider dispatch without provider inference"
    assert all(set(item) >= {"path", "symbol", "call_type", "category"} for item in callers)
    assert all(item["decision"] in audit.DECISIONS and item["owner_track"] for item in callers)
    assert all(
        item["decision"] == "exclude" and item["owner_track"]
        for item in inventory["scan"]["exclusions"]
    )


def test_inventory_covers_mandatory_seams_and_rejects_unowned_callers():
    audit = _audit_module()
    inventory = audit.build_inventory(REPO_ROOT)
    categories_by_path = {}
    for caller in _callers(inventory):
        categories_by_path.setdefault(caller["path"], set()).add(caller["category"])
    assert categories_by_path["app.py"] == {"active_read", "composition_startup"}
    assert "composition_startup" in categories_by_path["src/app_initializer.py"]
    assert "active_read" in categories_by_path["src/chat_processor.py"]
    assert "lifecycle_owner" in categories_by_path["src/personal_docs.py"]
    assert categories_by_path["src/context_orchestrator.py"] == {"generic_context_boundary"}
    assert categories_by_path["src/rag_manager.py"] == {"compatibility_fallback"}
    assert categories_by_path["routes/diagnostics_routes.py"] == {"health_diagnostics"}
    exclusions = {item["path"]: item["category"] for item in inventory["scan"]["exclusions"]}
    assert exclusions["src/bg_jobs.py"] == "background_excluded"
    assert exclusions["src/task_scheduler.py"] == "scheduler_excluded"
    category, reason = audit._classify("src/new_runtime_consumer.py", "rag_call", "search")
    assert category == "unclassified"
    assert category not in audit.CATEGORIES
    assert reason
    for path, call_type in (("routes/new_runtime_route.py", "rag_call"), ("src/new_dynamic.py", "dynamic_getattr")):
        category, _ = audit._classify(path, call_type, "search")
        assert category == "unclassified"
