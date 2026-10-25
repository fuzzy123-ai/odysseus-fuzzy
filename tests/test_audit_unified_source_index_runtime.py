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
    assert categories_by_path["app.py"] == {"active_read", "composition_startup", "lifecycle_owner"}
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


def test_inventory_records_exact_knowledge_runtime_composition_and_lifecycle_seams():
    audit = _audit_module()
    inventory = audit.build_inventory(REPO_ROOT)
    callers = _callers(inventory)

    runtime_seams = [
        caller
        for caller in callers
        if caller["call_type"] in {
            "runtime_factory",
            "runtime_state_store",
            "runtime_lifecycle",
        }
        and caller["method"] in {
            "build_knowledge_runtime",
            "knowledge_runtime",
            "bind_knowledge_runtime",
            "close_knowledge_runtime",
        }
    ]
    assert runtime_seams == [
        {
            "path": "app.py",
            "line": 652,
            "symbol": "<module>",
            "call_type": "runtime_state_store",
            "receiver": "app.state",
            "method": "knowledge_runtime",
            "category": "composition_startup",
            "decision": "adapt",
            "owner_track": "UIR-03",
        },
        {
            "path": "app.py",
            "line": 2159,
            "symbol": "_startup_event",
            "call_type": "runtime_lifecycle",
            "receiver": "knowledge_runtime",
            "method": "bind_knowledge_runtime",
            "category": "lifecycle_owner",
            "decision": "adapt",
            "owner_track": "UIR-03",
        },
        {
            "path": "app.py",
            "line": 2167,
            "symbol": "_shutdown_event",
            "call_type": "runtime_lifecycle",
            "receiver": "knowledge_runtime",
            "method": "close_knowledge_runtime",
            "category": "lifecycle_owner",
            "decision": "adapt",
            "owner_track": "UIR-03",
        },
        {
            "path": "src/app_initializer.py",
            "line": 129,
            "symbol": "initialize_managers",
            "call_type": "runtime_factory",
            "receiver": "",
            "method": "build_knowledge_runtime",
            "category": "composition_startup",
            "decision": "adapt",
            "owner_track": "UIR-03",
        },
    ]
    assert inventory["summary"]["candidate_count"] == 59
    assert inventory["summary"]["category_counts"]["composition_startup"] == 8
    assert inventory["summary"]["category_counts"]["lifecycle_owner"] == 10
    assert inventory["summary"]["unclassified_count"] == 0


def test_knowledge_runtime_seams_fail_closed_for_unknown_paths_and_noncanonical_shapes():
    audit = _audit_module()
    unknown = audit._Visitor("src/unknown_runtime_consumer.py")
    unknown.visit(ast.parse(
        "knowledge_runtime = build_knowledge_runtime(\n"
        "    (knowledge_runtime_config.mode.value, knowledge_runtime_config.runtime_enabled),\n"
        "    planner=None,\n"
        ")\n"
        "app.state.knowledge_runtime = knowledge_runtime\n"
        "bind_knowledge_runtime(knowledge_runtime, set_query_knowledge_planner)\n"
        "close_knowledge_runtime(knowledge_runtime)\n"
    ))
    assert len(unknown.items) == 4
    assert {item["category"] for item in unknown.items} == {"unclassified"}
    assert {item["decision"] for item in unknown.items} == {"unclassified"}
    assert {item["owner_track"] for item in unknown.items} == {""}

    noncanonical_initializer = audit._Visitor("src/app_initializer.py")
    noncanonical_initializer.visit(ast.parse(
        "other_runtime = build_knowledge_runtime(\n"
        "    (knowledge_runtime_config.mode.value, knowledge_runtime_config.runtime_enabled),\n"
        "    planner=None,\n"
        ")\n"
        "knowledge_runtime = build_knowledge_runtime(config, planner=None)\n"
    ))
    assert not [
        item for item in noncanonical_initializer.items
        if item["category"] != "unclassified"
        and item["method"] == "build_knowledge_runtime"
    ]

    noncanonical = audit._Visitor("app.py")
    noncanonical.visit(ast.parse(
        "alias = build_knowledge_runtime\n"
        "knowledge_runtime = alias(config, planner=None)\n"
        "state.knowledge_runtime = knowledge_runtime\n"
        "app.state.runtime = knowledge_runtime\n"
        "runtime_api.bind_knowledge_runtime(knowledge_runtime, binder)\n"
        "runtime_api.close_knowledge_runtime(knowledge_runtime)\n"
        "bind_knowledge_runtime(other_runtime, set_query_knowledge_planner)\n"
        "close_knowledge_runtime(other_runtime)\n"
    ))
    assert not [
        item for item in noncanonical.items
        if item["category"] != "unclassified"
        and item["call_type"] in {
            "runtime_factory",
            "runtime_state_store",
            "runtime_lifecycle",
        }
    ]
