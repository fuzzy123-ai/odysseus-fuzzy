import json

from scripts.architecture_import_map import (
    ARCHITECTURE_IMPORT_MAP_SCHEMA,
    build_import_map,
    classify_module_domain,
)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_architecture_import_map_parses_local_edges_without_importing_modules(tmp_path):
    _write(
        tmp_path / "src" / "memory_lifecycle.py",
        "import os\nfrom src.universal_inbox_flow_state import build\nfrom .memory_policy import check\n",
    )
    _write(tmp_path / "src" / "memory_policy.py", "VALUE = 1\n")
    _write(tmp_path / "src" / "universal_inbox_flow_state.py", "VALUE = 2\n")
    _write(tmp_path / "routes" / "review_gate_routes.py", "from src.memory_lifecycle import VALUE\n")

    payload = build_import_map(tmp_path, scan_dirs=("src", "routes"))
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["schema"] == ARCHITECTURE_IMPORT_MAP_SCHEMA
    assert payload["scanned_file_count"] == 4
    assert payload["module_count"] == 4
    assert payload["parse_error_count"] == 0
    assert payload["local_cross_domain_edge_count"] >= 1
    assert payload["domains"]["memory"]["module_count"] == 2
    assert payload["domains"]["inbox"]["module_count"] == 1
    assert payload["files_moved"] is False
    assert payload["imports_executed"] is False
    assert payload["side_effects"] == ("none",)
    assert "PRIVATE" not in encoded


def test_architecture_import_map_records_parse_errors_without_failing_scan(tmp_path):
    _write(tmp_path / "src" / "agent_loop.py", "from src.memory_lifecycle import x\n")
    _write(tmp_path / "src" / "bad_module.py", "def nope(:\n")

    payload = build_import_map(tmp_path, scan_dirs=("src",))

    assert payload["scanned_file_count"] == 2
    assert payload["module_count"] == 1
    assert payload["parse_error_count"] == 1
    assert payload["parse_errors"][0]["module"] == "src.bad_module"


def test_classify_module_domain_matches_candidate_boundaries():
    assert classify_module_domain("src.agent_loop") == "agent"
    assert classify_module_domain("src.orchestration_dashboard") == "orchestration"
    assert classify_module_domain("src.memory_lifecycle") == "memory"
    assert classify_module_domain("src.universal_inbox_flow_state") == "inbox"
    assert classify_module_domain("src.operator_dashboard.snapshot") == "ops"
    assert classify_module_domain("plugins.telegram.plugin") == "plugins"
    assert classify_module_domain("routes.version_one_readiness_routes") == "release"
    assert classify_module_domain("json") == "external"
