import ast
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import time

from scripts.audit_tool_registry_drift import build_inventory
from src.builtin_tool_catalog import (
    AGENT_HANDLER_TOOLS,
    BUILTIN_TOOL_SPECS,
    CATALOG_TOOL_IDS,
    CONFIRMED_ROUTE_REGISTRATION_GAPS,
    DEFAULT_DEFERRED_TOOLS,
    DEFERRED_REGISTRATION_GAPS,
    RAG_ONLY_PROMPT_TOOLS,
    REGISTRATION_GAPS,
    SECURITY_BLOCKED_REGISTRATION_GAPS,
    build_builtin_analytics_identity_contract,
    validate_builtin_projections,
)
from src.runtime_tool_status import build_tool_catalog_projection
from src.tool_catalog import ToolEffectClass, ToolFamily, ToolPermission, ToolSource
from src.tool_policy import DEFAULT_DEFERRED_RUNTIME_TOOLS


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "docs" / "plans" / "tool-taxonomy-acceptance-report.md"


def _assignment_value(relative_path: str, name: str):
    tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return node.value
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            return node.value
    raise AssertionError(f"{name} assignment not found")


def _builtin_descriptions() -> dict[str, str]:
    node = _assignment_value("src/tool_index.py", "BUILTIN_TOOL_DESCRIPTIONS")
    assert isinstance(node, ast.Dict)
    return {
        ast.literal_eval(key): ast.literal_eval(value)
        for key, value in zip(node.keys, node.values)
    }


def test_all_static_builtin_projections_validate_against_one_catalog():
    inventory = build_inventory(ROOT)
    projections = inventory["projections"]

    validate_builtin_projections(
        runtime_tags=projections["runtime_tags"],
        function_schemas=projections["function_schemas"],
        tool_index_entries=projections["tool_index_entries"],
        prompt_sections=projections["prompt_sections"],
        agent_handlers=projections["agent_handlers"],
        dispatcher_condition_ids=projections["dispatcher_condition_ids"],
    )
    catalog = set(CATALOG_TOOL_IDS)
    assert len(catalog) == 85
    assert set(projections["runtime_tags"]) | REGISTRATION_GAPS == catalog
    assert set(projections["function_schemas"]) == catalog - {"generate_image"}
    assert set(projections["tool_index_entries"]) == catalog
    assert set(projections["prompt_sections"]) == catalog - RAG_ONLY_PROMPT_TOOLS
    assert set(projections["agent_handlers"]) == set(AGENT_HANDLER_TOOLS)


def test_six_gaps_and_previous_admin_fallbacks_have_exact_dispositions():
    inventory = build_inventory(ROOT)

    assert REGISTRATION_GAPS == {
        "manage_assistant",
        "manage_embeddings",
        "manage_personal_docs",
        "manage_plugins",
        "manage_presets",
        "tail_serve_output",
    }
    assert CONFIRMED_ROUTE_REGISTRATION_GAPS == {
        "manage_embeddings",
        "manage_personal_docs",
        "manage_plugins",
    }
    assert DEFERRED_REGISTRATION_GAPS == {"manage_assistant", "manage_presets"}
    assert SECURITY_BLOCKED_REGISTRATION_GAPS == {"tail_serve_output"}
    assert inventory["counts"]["admin_metadata"] == 85
    assert inventory["counts"]["runtime_without_admin_metadata"] == 0
    assert inventory["counts"]["stale_admin_metadata"] == 0
    assert "manage_rag" not in inventory["projections"]["admin_metadata"]


def test_api_ui_and_analytics_are_deterministic_catalog_projections():
    descriptions = _builtin_descriptions()
    api_projection = build_tool_catalog_projection(
        disabled_tools=DEFAULT_DEFERRED_RUNTIME_TOOLS,
        builtin_descriptions=descriptions,
    )
    analytics = build_builtin_analytics_identity_contract(descriptions)
    admin_source = (ROOT / "static" / "js" / "admin.js").read_text(
        encoding="utf-8"
    )

    assert api_projection["tool_count"] == 85
    assert {item["id"] for item in api_projection["descriptors"]} == set(
        CATALOG_TOOL_IDS
    )
    assert api_projection["sources"] == (ToolSource.BUILTIN.value,)
    assert {item["family"] for item in api_projection["descriptors"]} <= {
        family.value for family in ToolFamily
    }
    assert analytics.schema_version == "odysseus.tool_analytics_identity.v1"
    assert len(analytics.identities) == len(analytics.analytics_id_reservations) == 85
    assert len({item.analytics_id for item in analytics.identities}) == 85
    assert "const TOOL_META" not in admin_source
    assert "fetch('/api/tools'" in admin_source
    assert "TOOL_FAMILY_PRESENTATION" in admin_source


def test_deferred_families_stay_default_off_in_policy_api_and_ui_state():
    projection = build_tool_catalog_projection(
        disabled_tools=DEFAULT_DEFERRED_RUNTIME_TOOLS,
        builtin_descriptions=_builtin_descriptions(),
    )
    rows = {item["id"]: item for item in projection["descriptors"]}

    assert len(DEFAULT_DEFERRED_TOOLS) == 14
    for tool_id in DEFAULT_DEFERRED_TOOLS:
        assert rows[tool_id]["enabled"] is False
        assert rows[tool_id]["lifecycle"] == "deferred"
    assert {"send_email", "manage_calendar", "manage_contact"} <= set(
        DEFAULT_DEFERRED_RUNTIME_TOOLS
    )


def test_role_effect_and_confirmation_matrix_fails_closed():
    effect_counts = Counter(spec.effect_class for spec in BUILTIN_TOOL_SPECS)
    permission_counts = Counter(spec.permission for spec in BUILTIN_TOOL_SPECS)
    descriptions = _builtin_descriptions()
    descriptors = build_builtin_analytics_identity_contract(descriptions)
    descriptor_index = {
        spec.tool_id: spec.build_descriptor(descriptions[spec.tool_id])
        for spec in BUILTIN_TOOL_SPECS
    }

    assert effect_counts == {
        ToolEffectClass.READ: 23,
        ToolEffectClass.LOCAL_WRITE: 10,
        ToolEffectClass.EXTERNAL_WRITE: 9,
        ToolEffectClass.DESTRUCTIVE: 1,
        ToolEffectClass.CONTROL: 42,
    }
    assert permission_counts == {ToolPermission.OWNER: 75, ToolPermission.ADMIN: 10}
    assert len(descriptors.identities) == 85
    for descriptor in descriptor_index.values():
        if descriptor.effect_class != ToolEffectClass.READ:
            assert descriptor.requires_confirmation is True
        if descriptor.effect_class in {
            ToolEffectClass.EXTERNAL_WRITE,
            ToolEffectClass.DESTRUCTIVE,
        }:
            assert descriptor.default_enabled is False
    tail = descriptor_index["tail_serve_output"]
    assert tail.permission == ToolPermission.ADMIN
    assert tail.default_enabled is False
    assert tail.availability.value == "blocked"


def test_parser_alias_targets_are_static_catalog_consumers():
    parser_source = (ROOT / "src" / "tool_parsing.py").read_text(encoding="utf-8")
    alias_node = _assignment_value("src/tool_parsing.py", "_TOOL_NAME_MAP")
    aliases = ast.literal_eval(alias_node)

    assert set(aliases.values()) <= set(CATALOG_TOOL_IDS)
    assert "manage_rag" not in aliases
    assert "from src.builtin_tool_catalog import catalog_fenced_tool_names" in parser_source
    assert "names.update(catalog_fenced_tool_names())" in parser_source


def test_import_startup_and_projection_budget_are_bounded():
    code = (
        "import sys; import src.tool_catalog; import src.builtin_tool_catalog; "
        "import src.runtime_tool_status; "
        "assert 'src.tool_index' not in sys.modules; "
        "assert 'src.embedding_lanes' not in sys.modules; "
        "assert 'src.agent_tools' not in sys.modules; "
        "assert 'core.database' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    descriptions = _builtin_descriptions()
    started = time.perf_counter()
    projections = [
        build_tool_catalog_projection(
            disabled_tools=DEFAULT_DEFERRED_RUNTIME_TOOLS,
            builtin_descriptions=descriptions,
        )
        for _ in range(25)
    ]
    elapsed = time.perf_counter() - started
    assert elapsed < 2.5
    assert all(projection == projections[0] for projection in projections[1:])
    assert len(json.dumps(projections[0], sort_keys=True).encode("utf-8")) < 256_000


def test_acceptance_report_is_aggregate_and_redacted():
    report = REPORT.read_text(encoding="utf-8")
    lowered = report.casefold()

    assert "status: passed" in lowered
    assert "c:\\" not in report
    assert "bearer " not in lowered
    assert "sk-" not in lowered
    assert "token=" not in lowered
    assert "private_paths_visible: true" not in lowered
    assert "raw_content_visible: true" not in lowered
