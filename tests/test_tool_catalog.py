from dataclasses import FrozenInstanceError

import pytest

from src.agent_identity import AgentIdentity
from src.context_capsule import ContextCapsule
from src.tool_catalog import (
    ToolAvailability,
    ToolCatalogError,
    ToolAvailability,
    ToolDescriptor,
    ToolDescriptorCatalogV2,
    ToolDescriptorV2,
    ToolEffectClass,
    ToolFamily,
    ToolManifest,
    ToolLifecycle,
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


def _descriptor_v2(**overrides) -> ToolDescriptorV2:
    values = {
        "tool_id": "read_file",
        "display_name": "Read File",
        "description": "Read a workspace file through the bounded file adapter.",
        "family": ToolFamily.CODE_FILESYSTEM,
        "source": ToolSource.BUILTIN,
        "lifecycle": ToolLifecycle.ACTIVE,
        "availability": ToolAvailability.AVAILABLE,
        "default_enabled": True,
        "default_visibility": ToolVisibility.VISIBLE,
        "risk_level": ToolRiskLevel.SAFE,
        "permission": ToolPermission.USER,
        "effect_class": ToolEffectClass.READ,
        "requires_confirmation": False,
        "schema_ref": "function:read_file",
        "handler_ref": "builtin:read_file",
        "prompt_ref": "index:read_file",
        "introduced_in": "0.24.0",
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


def test_descriptor_v2_serializes_controlled_content_free_metadata_deterministically():
    descriptor = _descriptor_v2(
        aliases=["legacy_read_file"],
        feature_flag="tool_catalog_v2",
    )

    audit = descriptor.audit_dict()

    assert audit["contract"] == "odysseus.tool_descriptor.v2"
    assert audit["tool_id"] == "read_file"
    assert audit["analytics_id"] == "read_file"
    assert audit["family"] == "code_filesystem"
    assert audit["source"] == "builtin"
    assert audit["lifecycle"] == "active"
    assert audit["availability"] == "available"
    assert audit["effect_class"] == "read"
    assert audit["default_visibility"] == "visible"
    assert audit["aliases"] == ("legacy_read_file",)
    assert audit["callable_visible"] is False
    assert audit["arguments_visible"] is False
    assert audit["raw_content_visible"] is False
    assert audit["secret_value_visible"] is False
    assert descriptor.audit_json() == descriptor.audit_json()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("family", "Other"),
        ("source", "runtime-maybe"),
        ("lifecycle", "enabled-ish"),
        ("availability", "probably"),
        ("effect_class", "mutation"),
        ("risk_level", "unknown"),
        ("default_visibility", "sometimes"),
        ("permission", "superuser-ish"),
    ],
)
def test_descriptor_v2_rejects_values_outside_controlled_enums(field, value):
    with pytest.raises(ToolCatalogError):
        _descriptor_v2(**{field: value})


def test_descriptor_v2_enforces_lifecycle_availability_and_effect_defaults():
    with pytest.raises(ToolCatalogError):
        _descriptor_v2(lifecycle=ToolLifecycle.DEFERRED, default_enabled=True)
    with pytest.raises(ToolCatalogError):
        _descriptor_v2(
            availability=ToolAvailability.NOT_CONFIGURED,
            availability_reason=None,
            default_enabled=False,
        )
    with pytest.raises(ToolCatalogError):
        _descriptor_v2(
            effect_class=ToolEffectClass.EXTERNAL_WRITE,
            requires_confirmation=False,
        )


def test_unknown_dynamic_tool_defaults_fail_closed():
    descriptor = ToolDescriptorV2.conservative_dynamic(
        tool_id="provider_lookup",
        source=ToolSource.PROVIDER,
        source_id="redacted-provider",
    )

    assert descriptor.family == ToolFamily.UNCLASSIFIED_DYNAMIC
    assert descriptor.lifecycle == ToolLifecycle.EXPERIMENTAL
    assert descriptor.availability == ToolAvailability.UNAVAILABLE
    assert descriptor.default_enabled is False
    assert descriptor.default_visibility == ToolVisibility.UNAVAILABLE
    assert descriptor.risk_level == ToolRiskLevel.DANGEROUS
    assert descriptor.permission == ToolPermission.ADMIN
    assert descriptor.requires_confirmation is True
    assert descriptor.source_id == "redacted-provider"


def test_catalog_aliases_resolve_to_one_immutable_analytics_identity():
    descriptor = _descriptor_v2(aliases=["legacy_read_file", "read_workspace_file"])
    catalog = ToolDescriptorCatalogV2.create([descriptor])

    assert catalog.resolve("read_file") is descriptor
    assert catalog.resolve("legacy_read_file") is descriptor
    assert catalog.resolve("read_workspace_file").analytics_id == "read_file"
    assert catalog.aliases == (
        ("legacy_read_file", "read_file"),
        ("read_workspace_file", "read_file"),
    )


def test_catalog_rejects_alias_and_analytics_collisions():
    first = _descriptor_v2(aliases=["legacy_file"])
    second = _descriptor_v2(
        tool_id="write_file",
        analytics_id="read_file",
        display_name="Write File",
        schema_ref="function:write_file",
        handler_ref="builtin:write_file",
        prompt_ref="index:write_file",
    )
    with pytest.raises(ToolCatalogError, match="analytics_id collision"):
        ToolDescriptorCatalogV2.create([first, second])

    canonical_collision = _descriptor_v2(
        tool_id="legacy_file",
        display_name="Legacy File",
        schema_ref="function:legacy_file",
        handler_ref="builtin:legacy_file",
        prompt_ref="index:legacy_file",
    )
    with pytest.raises(ToolCatalogError, match="alias collides"):
        ToolDescriptorCatalogV2.create([first, canonical_collision])


def test_lifecycle_transitions_preserve_identity_and_fail_closed():
    active = _descriptor_v2()
    deprecated = active.transition_lifecycle(
        ToolLifecycle.DEPRECATED,
        changed_in="0.25.0",
    )
    blocked = deprecated.transition_lifecycle(
        ToolLifecycle.BLOCKED,
        changed_in="0.26.0",
    )

    assert deprecated.analytics_id == active.analytics_id
    assert deprecated.deprecated_in == "0.25.0"
    assert deprecated.default_enabled is False
    assert blocked.analytics_id == active.analytics_id
    assert blocked.default_visibility == ToolVisibility.BLOCKED
    assert blocked.availability == ToolAvailability.DISABLED
    with pytest.raises(ToolCatalogError, match="not allowed"):
        blocked.transition_lifecycle(ToolLifecycle.ACTIVE, changed_in="0.27.0")


def test_v1_manifests_migrate_to_v2_deterministically():
    manifest = ToolManifest.create(
        tool_id="write_file",
        family="filesystem",
        short_description="Write a bounded workspace file.",
        capabilities=["write"],
        risk_level=ToolRiskLevel.DANGEROUS,
        schema_ref="function:write_file",
        visibility_state=ToolVisibility.HIDDEN,
    )

    first = ToolDescriptorV2.from_v1_manifest(manifest)
    second = ToolDescriptorV2.from_v1_manifest(manifest.compact_prompt_dict())
    catalog = ToolDescriptorCatalogV2.from_v1_manifests([manifest])

    assert first.audit_json() == second.audit_json()
    assert first.family == ToolFamily.CODE_FILESYSTEM
    assert first.lifecycle == ToolLifecycle.CONTEXTUAL
    assert first.default_enabled is True
    assert first.effect_class == ToolEffectClass.LOCAL_WRITE
    assert first.permission == ToolPermission.ADMIN
    assert catalog.resolve("write_file").audit_json() == first.audit_json()


def test_descriptor_v2_rejects_secret_like_private_or_callable_content():
    with pytest.raises(ToolCatalogError, match="secret-like"):
        _descriptor_v2(description="Use password=plaintext for the provider.")
    with pytest.raises(ToolCatalogError, match="private-path"):
        _descriptor_v2(description=r"Read C:\Users\alice\private.txt")
    with pytest.raises(ToolCatalogError, match="callable"):
        _descriptor_v2(handler_ref=lambda: None)


def test_non_native_schema_projection_requires_an_explicit_reason():
    descriptor = _descriptor_v2(
        tool_id="generate_image",
        display_name="Generate Image",
        schema_ref=None,
        handler_ref="builtin:generate_image",
        prompt_ref="index:generate_image",
        native_schema=False,
        projection_exception_reason="legacy-non-native-projection",
    )

    assert descriptor.schema_ref is None
    assert descriptor.native_schema is False
    with pytest.raises(ToolCatalogError, match="projection exception"):
        _descriptor_v2(
            tool_id="generate_image",
            display_name="Generate Image",
            schema_ref=None,
            handler_ref="builtin:generate_image",
            prompt_ref="index:generate_image",
            native_schema=False,
            projection_exception_reason=None,
        )
