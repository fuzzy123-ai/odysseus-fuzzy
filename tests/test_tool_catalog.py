from dataclasses import FrozenInstanceError

import pytest

from src.agent_identity import AgentIdentity
from src.context_capsule import ContextCapsule
from src.tool_catalog import (
    ToolCatalogError,
    ToolAvailability,
    ToolDescriptor,
    ToolDescriptorV2,
    ToolDescriptorV2Index,
    ToolEffectClass,
    ToolFamily,
    ToolLifecycle,
    ToolManifest,
    ToolPermission,
    ToolRiskLevel,
    ToolSelectionRequest,
    ToolSelectionResult,
    ToolSource,
    ToolVisibility,
    build_tool_manifests_from_function_schemas,
    select_deferred_tool_schemas,
    validate_tool_lifecycle_transition,
)


def _identity(role_id: str = "backend-owner") -> AgentIdentity:
    return AgentIdentity.create(
        agent_id="Bob Worker",
        role_id=role_id,
        project_id="Odysseus Fork",
        memory_scope="Shared Memory",
        workspace_scope="Repo Root",
        run_id="Run 42",
    )


def _capsule() -> ContextCapsule:
    return ContextCapsule.create(
        capsule_id="AS4B Capsule",
        objective="Select a compact backend tool set.",
        agent_identity=_identity(),
        allowed_files=["src/tool_catalog.py", "tests/test_tool_catalog.py"],
        blocked_files=[],
        inputs={"mode": "backend-only"},
        expected_outputs=["tool catalog", "tests"],
        tests=["python -m pytest tests/test_tool_catalog.py"],
        handoff_format=["Agent: Bob"],
        stop_conditions=["stop on contract conflict"],
        evidence_required=["green pytest"],
    )


def _v2(**overrides) -> ToolDescriptorV2:
    values = {
        "tool_id": "read_file",
        "analytics_id": "read-file",
        "display_name": "Read file",
        "description": "Read a repository file.",
        "family": ToolFamily.CODE_FILESYSTEM,
        "source": ToolSource.BUILTIN,
        "lifecycle": ToolLifecycle.ACTIVE,
        "availability": ToolAvailability.AVAILABLE,
        "default_enabled": True,
        "default_visibility": ToolVisibility.VISIBLE,
        "risk_level": ToolRiskLevel.SAFE,
        "permission": ToolPermission.OWNER,
        "effect_class": ToolEffectClass.READ,
        "requires_confirmation": False,
        "schema_ref": "function:read_file",
        "handler_ref": "tool_execution:read_file",
        "prompt_ref": "tool_index:read_file",
        "aliases": ("read-file-legacy",),
        "feature_flag": "tool-catalog-v2",
        "introduced_in": "0.24",
    }
    values.update(overrides)
    return ToolDescriptorV2.create(**values)


def test_matching_safe_tools_become_visible():
    request = ToolSelectionRequest.create(
        agent_identity=_identity(),
        context_capsule=_capsule(),
        requested_capabilities=["pytest", "git-read"],
    )
    catalog = [
        ToolDescriptor.create(
            tool_id="git-read",
            label="Git Read",
            capabilities=["git-read"],
            risk_level=ToolRiskLevel.SAFE,
            requires_approval=False,
            allowed_roles=["backend-owner"],
            blocked_scopes=[],
            summary="Read git metadata only.",
        ),
        ToolDescriptor.create(
            tool_id="pytest-runner",
            label="Pytest Runner",
            capabilities=["pytest"],
            risk_level=ToolRiskLevel.SAFE,
            requires_approval=False,
            allowed_roles=["backend-owner"],
            blocked_scopes=[],
            summary="Run focused test commands.",
        ),
    ]

    result = ToolSelectionResult.select(request=request, catalog=catalog)

    assert [tool.tool_id for tool in result.visible_tools] == ["git-read", "pytest-runner"]
    assert result.blocked_tools == ()


def test_role_mismatch_prevents_visible():
    request = ToolSelectionRequest.create(
        agent_identity=_identity(role_id="planner"),
        context_capsule=_capsule(),
        requested_capabilities=["pytest"],
    )
    catalog = [
        ToolDescriptor.create(
            tool_id="pytest-runner",
            label="Pytest Runner",
            capabilities=["pytest"],
            risk_level=ToolRiskLevel.SAFE,
            requires_approval=False,
            allowed_roles=["backend-owner"],
            blocked_scopes=[],
            summary="Run focused test commands.",
        )
    ]

    result = ToolSelectionResult.select(request=request, catalog=catalog)

    assert result.visible_tools == ()
    assert result.blocked_tools == ()
    assert result.warnings == ("hidden:pytest-runner:role-mismatch",)


def test_requires_approval_is_marked_not_visible():
    request = ToolSelectionRequest.create(
        agent_identity=_identity(),
        context_capsule=_capsule(),
        requested_capabilities=["shell-write"],
    )
    catalog = [
        ToolDescriptor.create(
            tool_id="shell-write",
            label="Shell Write",
            capabilities=["shell-write"],
            risk_level=ToolRiskLevel.DANGEROUS,
            requires_approval=True,
            allowed_roles=["backend-owner"],
            blocked_scopes=[],
            summary="Execute write-capable shell commands with long explanation that should be trimmed from dumps.",
        )
    ]

    result = ToolSelectionResult.select(request=request, catalog=catalog)

    assert result.visible_tools == ()
    assert [tool.tool_id for tool in result.blocked_tools] == ["shell-write"]
    assert result.warnings == ("approval:shell-write",)


def test_blocked_scope_wins_over_visibility():
    capsule = _capsule()
    request = ToolSelectionRequest.create(
        agent_identity=_identity(),
        context_capsule=capsule,
        requested_capabilities=["git-read"],
    )
    catalog = [
        ToolDescriptor.create(
            tool_id="git-read",
            label="Git Read",
            capabilities=["git-read"],
            risk_level=ToolRiskLevel.SAFE,
            requires_approval=False,
            allowed_roles=["backend-owner"],
            blocked_scopes=[f"capsule-{capsule.capsule_id}"],
            summary="Read git metadata only.",
        )
    ]

    result = ToolSelectionResult.select(request=request, catalog=catalog)

    assert result.visible_tools == ()
    assert [tool.tool_id for tool in result.blocked_tools] == ["git-read"]
    assert result.warnings == (f"blocked:git-read:capsule-{capsule.capsule_id}",)


def test_result_is_deterministic_sorted_and_budgeted_without_long_descriptions_in_summary():
    request = ToolSelectionRequest.create(
        agent_identity=_identity(),
        context_capsule=_capsule(),
        requested_capabilities=["pytest", "git-read", "lint"],
    )
    catalog = [
        ToolDescriptor.create(
            tool_id="zz-lint",
            label="Lint Runner",
            capabilities=["lint"],
            risk_level=ToolRiskLevel.SAFE,
            requires_approval=False,
            allowed_roles=["backend-owner"],
            blocked_scopes=[],
            summary="Very long summary " * 30,
        ),
        ToolDescriptor.create(
            tool_id="aa-git-read",
            label="Git Read",
            capabilities=["git-read"],
            risk_level=ToolRiskLevel.SAFE,
            requires_approval=False,
            allowed_roles=["backend-owner"],
            blocked_scopes=[],
            summary="Read git metadata only.",
        ),
        ToolDescriptor.create(
            tool_id="mm-pytest",
            label="Pytest Runner",
            capabilities=["pytest"],
            risk_level=ToolRiskLevel.SAFE,
            requires_approval=False,
            allowed_roles=["backend-owner"],
            blocked_scopes=[],
            summary="Run focused tests.",
        ),
    ]

    result = ToolSelectionResult.select(request=request, catalog=catalog)
    summary = result.audit_summary()

    assert [tool.tool_id for tool in result.visible_tools] == ["aa-git-read", "mm-pytest", "zz-lint"]
    assert result.prompt_budget_estimate > 0
    assert summary["visible_count"] == 3
    assert summary["prompt_budget_estimate"] == result.prompt_budget_estimate
    assert "Very long summary" not in repr(summary)


def test_descriptor_requires_nonempty_capabilities():
    with pytest.raises(ToolCatalogError):
        ToolDescriptor.create(
            tool_id="empty-tool",
            label="Empty Tool",
            capabilities=[],
            risk_level=ToolRiskLevel.SAFE,
            requires_approval=False,
            allowed_roles=[],
            blocked_scopes=[],
            summary="No capabilities.",
        )


def test_tool_manifest_from_function_schema_is_compact_and_schema_referenced():
    schema = {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a file to disk with exact content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
            },
        },
    }

    manifest = ToolManifest.from_function_schema(schema, visibility_state=ToolVisibility.VISIBLE)
    payload = manifest.compact_prompt_dict()
    audit = manifest.audit_summary()

    assert manifest.tool_id == "write_file"
    assert manifest.family == "filesystem"
    assert manifest.risk_level == ToolRiskLevel.DANGEROUS
    assert manifest.schema_ref == "function:write_file"
    assert "write" in manifest.capabilities
    assert payload["visibility_state"] == "visible"
    assert "parameters" not in payload
    assert "properties" not in repr(payload)
    assert audit["raw_schema_visible"] is False
    assert audit["raw_content_visible"] is False
    assert audit["token_value_visible"] is False


def test_tool_manifest_builder_sorts_and_deduplicates_function_schemas():
    schemas = [
        {"type": "function", "function": {"name": "web_search", "description": "Search the web.", "parameters": {}}},
        {"type": "function", "function": {"name": "ask_user", "description": "Ask for a decision.", "parameters": {}}},
        {"type": "function", "function": {"name": "web_search", "description": "Duplicate.", "parameters": {}}},
    ]

    manifests = build_tool_manifests_from_function_schemas(schemas)

    assert [item.tool_id for item in manifests] == ["ask_user", "web_search"]
    assert manifests[0].visibility_state == ToolVisibility.HIDDEN
    assert manifests[1].family == "network"
    assert manifests[1].risk_level == ToolRiskLevel.ELEVATED


def test_tool_manifest_rejects_unsafe_tool_ids():
    with pytest.raises(ToolCatalogError):
        ToolManifest.create(
            tool_id="../secret",
            family="filesystem",
            short_description="Unsafe id.",
            capabilities=["read"],
            risk_level=ToolRiskLevel.SAFE,
            schema_ref="function:secret",
        )


def test_deferred_schema_selection_sends_only_relevant_full_schemas():
    schemas = [
        {"type": "function", "function": {"name": "read_file", "description": "Read files.", "parameters": {"x": "full"}}},
        {"type": "function", "function": {"name": "write_file", "description": "Write files.", "parameters": {"x": "full"}}},
        {"type": "function", "function": {"name": "web_search", "description": "Search web.", "parameters": {"x": "full"}}},
    ]

    result = select_deferred_tool_schemas(
        schemas,
        relevant_tool_ids=["read_file"],
        required_tool_ids=["web_search"],
    )
    audit = result.audit_summary()

    assert [schema["function"]["name"] for schema in result.selected_schemas] == ["read_file", "web_search"]
    assert result.selected_schema_refs == ("function:read_file", "function:web_search")
    assert result.deferred_schema_refs == ("function:write_file",)
    visibility = {item.tool_id: item.visibility_state for item in result.manifests}
    assert visibility == {
        "read_file": ToolVisibility.VISIBLE,
        "web_search": ToolVisibility.VISIBLE,
        "write_file": ToolVisibility.HIDDEN,
    }
    assert audit["raw_schema_visible"] is False
    assert audit["selected_schema_count"] == 2


def test_deferred_schema_selection_blocks_disabled_tools_even_when_relevant():
    schemas = [
        {"type": "function", "function": {"name": "bash", "description": "Run shell.", "parameters": {}}},
        {"type": "function", "function": {"name": "ask_user", "description": "Ask.", "parameters": {}}},
    ]

    result = select_deferred_tool_schemas(
        schemas,
        relevant_tool_ids=["bash", "ask_user"],
        disabled_tool_ids=["bash"],
    )

    assert [schema["function"]["name"] for schema in result.selected_schemas] == ["ask_user"]
    assert result.blocked_schema_refs == ("function:bash",)
    visibility = {item.tool_id: item.visibility_state for item in result.manifests}
    assert visibility["bash"] == ToolVisibility.BLOCKED
    assert visibility["ask_user"] == ToolVisibility.VISIBLE


def test_deferred_schema_selection_adds_admin_schemas_only_when_needed():
    schemas = [
        {"type": "function", "function": {"name": "manage_memory", "description": "Memory.", "parameters": {}}},
        {"type": "function", "function": {"name": "manage_settings", "description": "Settings.", "parameters": {}}},
    ]

    normal = select_deferred_tool_schemas(
        schemas,
        relevant_tool_ids=["manage_memory"],
        admin_tool_ids=["manage_settings"],
        needs_admin=False,
    )
    admin = select_deferred_tool_schemas(
        schemas,
        relevant_tool_ids=["manage_memory"],
        admin_tool_ids=["manage_settings"],
        needs_admin=True,
    )

    assert normal.selected_schema_refs == ("function:manage_memory",)
    assert normal.deferred_schema_refs == ("function:manage_settings",)
    assert admin.selected_schema_refs == ("function:manage_memory", "function:manage_settings")


def test_deferred_schema_selection_requires_relevant_ids_unless_explicit_fallback():
    schemas = [
        {"type": "function", "function": {"name": "ask_user", "description": "Ask.", "parameters": {}}},
        {"type": "function", "function": {"name": "web_fetch", "description": "Fetch.", "parameters": {}}},
    ]

    strict = select_deferred_tool_schemas(schemas, relevant_tool_ids=None)
    fallback = select_deferred_tool_schemas(schemas, relevant_tool_ids=None, allow_full_fallback=True)

    assert strict.selected_schemas == ()
    assert strict.deferred_schema_refs == ("function:ask_user", "function:web_fetch")
    assert strict.warnings == ("schema_selection_requires_relevant_tool_ids",)
    assert [schema["function"]["name"] for schema in fallback.selected_schemas] == ["ask_user", "web_fetch"]
    assert fallback.warnings == ("fallback_full_schema_selection",)


def test_descriptor_v2_normalizes_controlled_fields_and_emits_safe_audit_summary():
    descriptor = _v2(aliases=("z-legacy", "a-legacy"))

    assert descriptor.schema_version == "odysseus.tool_descriptor.v2"
    assert descriptor.family == ToolFamily.CODE_FILESYSTEM
    assert descriptor.source == ToolSource.BUILTIN
    assert descriptor.lifecycle == ToolLifecycle.ACTIVE
    assert descriptor.availability == ToolAvailability.AVAILABLE
    assert descriptor.effect_class == ToolEffectClass.READ
    assert descriptor.aliases == ("a-legacy", "z-legacy")

    audit = descriptor.audit_summary()
    assert audit["raw_content_visible"] is False
    assert audit["callable_visible"] is False
    assert audit["tool_arguments_visible"] is False
    assert audit["tool_results_visible"] is False
    assert audit["secret_values_visible"] is False
    assert "display_name" not in audit
    assert "description" not in audit
    assert "Read a repository file" not in repr(audit)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("family", "other"),
        ("source", "unknown-provider"),
        ("lifecycle", "retired"),
        ("availability", "maybe"),
        ("default_visibility", "sometimes"),
        ("risk_level", "critical"),
        ("permission", "superuser"),
        ("effect_class", "remote_shell"),
    ],
)
def test_descriptor_v2_rejects_values_outside_controlled_enums(field, value):
    with pytest.raises(ToolCatalogError):
        _v2(**{field: value})


@pytest.mark.parametrize("analytics_id", ["Read-File", "read_file", "read file", "../read-file", ""])
def test_descriptor_v2_rejects_noncanonical_analytics_ids(analytics_id):
    with pytest.raises(ToolCatalogError):
        _v2(analytics_id=analytics_id)


@pytest.mark.parametrize("field", ["default_enabled", "requires_confirmation"])
def test_descriptor_v2_rejects_non_boolean_policy_flags(field):
    with pytest.raises(ToolCatalogError):
        _v2(**{field: "false"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_ref", "/private/schema"),
        ("handler_ref", "C:/private/handler"),
        ("prompt_ref", "https://invalid.example/prompt"),
        ("feature_flag", "flag with dynamic content"),
    ],
)
def test_descriptor_v2_rejects_nonstatic_or_absolute_references(field, value):
    with pytest.raises(ToolCatalogError):
        _v2(**{field: value})


@pytest.mark.parametrize(
    "aliases",
    [
        ("read_file",),
        ("same-alias", "same-alias"),
        ("../unsafe",),
    ],
)
def test_descriptor_v2_rejects_invalid_alias_sets(aliases):
    with pytest.raises(ToolCatalogError):
        _v2(aliases=aliases)


@pytest.mark.parametrize(
    "lifecycle",
    [
        ToolLifecycle.DEFERRED,
        ToolLifecycle.EXPERIMENTAL,
        ToolLifecycle.DEPRECATED,
        ToolLifecycle.BLOCKED,
    ],
)
def test_descriptor_v2_default_off_lifecycles_cannot_be_enabled(lifecycle):
    values = {"lifecycle": lifecycle}
    if lifecycle == ToolLifecycle.DEPRECATED:
        values["deprecated_in"] = "0.25"
    with pytest.raises(ToolCatalogError):
        _v2(**values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("availability", ToolAvailability.UNAVAILABLE),
        ("default_visibility", ToolVisibility.HIDDEN),
        ("default_visibility", ToolVisibility.BLOCKED),
        ("default_visibility", ToolVisibility.UNAVAILABLE),
    ],
)
def test_descriptor_v2_unavailable_or_hidden_tools_cannot_be_enabled(field, value):
    with pytest.raises(ToolCatalogError):
        _v2(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("risk_level", ToolRiskLevel.DANGEROUS),
        ("effect_class", ToolEffectClass.EXTERNAL_WRITE),
        ("effect_class", ToolEffectClass.DESTRUCTIVE),
    ],
)
def test_descriptor_v2_effectful_contracts_require_confirmation(field, value):
    with pytest.raises(ToolCatalogError):
        _v2(**{field: value, "requires_confirmation": False})


def test_descriptor_v2_deprecation_metadata_is_lifecycle_bound():
    with pytest.raises(ToolCatalogError):
        _v2(lifecycle=ToolLifecycle.DEPRECATED, default_enabled=False)
    with pytest.raises(ToolCatalogError):
        _v2(deprecated_in="0.25")

    descriptor = _v2(
        lifecycle=ToolLifecycle.DEPRECATED,
        default_enabled=False,
        deprecated_in="0.25",
    )
    assert descriptor.deprecated_in == "0.25"


def test_descriptor_v2_dynamic_unknown_tool_is_conservative_and_default_off():
    descriptor = ToolDescriptorV2.conservative_dynamic(
        tool_id="plugin:unclassified",
        display_name="Unclassified plugin tool",
        description="Awaiting a reviewed taxonomy mapping.",
    )

    assert descriptor.family == ToolFamily.UNCLASSIFIED_DYNAMIC
    assert descriptor.source == ToolSource.DYNAMIC
    assert descriptor.lifecycle == ToolLifecycle.BLOCKED
    assert descriptor.availability == ToolAvailability.UNKNOWN
    assert descriptor.default_enabled is False
    assert descriptor.default_visibility == ToolVisibility.HIDDEN
    assert descriptor.risk_level == ToolRiskLevel.ELEVATED
    assert descriptor.permission == ToolPermission.ADMIN
    assert descriptor.effect_class == ToolEffectClass.CONTROL
    assert descriptor.requires_confirmation is True


def test_v1_manifest_is_deterministically_readable_as_descriptor_v2():
    manifest = ToolManifest.create(
        tool_id="write_file",
        family="filesystem",
        short_description="Write a repository file.",
        capabilities=("write",),
        risk_level=ToolRiskLevel.DANGEROUS,
        schema_ref="function:write_file",
        visibility_state=ToolVisibility.REQUIRES_APPROVAL,
    )

    first = ToolDescriptorV2.from_v1_manifest(manifest)
    second = ToolDescriptorV2.from_v1_manifest(manifest)

    assert first == second
    assert first.analytics_id == "write-file"
    assert first.family == ToolFamily.CODE_FILESYSTEM
    assert first.source == ToolSource.LEGACY
    assert first.effect_class == ToolEffectClass.LOCAL_WRITE
    assert first.requires_confirmation is True
    assert first.default_enabled is False


def test_descriptor_v2_index_resolves_aliases_without_changing_analytics_identity():
    descriptor = _v2()
    index = ToolDescriptorV2Index.build([descriptor])

    assert index.resolve("read_file") is descriptor
    assert index.resolve("read-file-legacy") is descriptor
    assert index.resolve("missing") is None
    assert index.resolve("read-file-legacy").analytics_id == "read-file"
    assert index.audit_summary()["raw_content_visible"] is False


@pytest.mark.parametrize(
    "descriptors",
    [
        [_v2(), _v2(analytics_id="second-id")],
        [_v2(), _v2(tool_id="second_tool")],
        [_v2(), _v2(tool_id="second_tool", analytics_id="second-id", aliases=("read_file",))],
        [_v2(), _v2(tool_id="second_tool", analytics_id="second-id", aliases=("read-file-legacy",))],
    ],
)
def test_descriptor_v2_index_fails_closed_on_id_or_alias_collisions(descriptors):
    with pytest.raises(ToolCatalogError):
        ToolDescriptorV2Index.build(descriptors)


def test_descriptor_v2_analytics_identity_is_immutable():
    descriptor = _v2()
    with pytest.raises(FrozenInstanceError):
        descriptor.analytics_id = "replacement-id"


def test_descriptor_v2_lifecycle_transitions_fail_closed():
    assert (
        validate_tool_lifecycle_transition(ToolLifecycle.CONTEXTUAL, ToolLifecycle.ACTIVE)
        == ToolLifecycle.ACTIVE
    )
    with pytest.raises(ToolCatalogError):
        validate_tool_lifecycle_transition(ToolLifecycle.DEPRECATED, ToolLifecycle.ACTIVE)
    with pytest.raises(ToolCatalogError):
        validate_tool_lifecycle_transition(ToolLifecycle.BLOCKED, ToolLifecycle.CONTEXTUAL)
    with pytest.raises(ToolCatalogError):
        validate_tool_lifecycle_transition("retired", ToolLifecycle.ACTIVE)
