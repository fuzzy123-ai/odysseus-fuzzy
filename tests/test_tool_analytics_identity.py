import pytest

from src.builtin_tool_catalog import (
    HISTORICAL_TOOL_ALIASES,
    build_tool_analytics_identity_contract,
    resolve_tool_analytics_identity,
)
from src.runtime_tool_status import build_dynamic_tool_descriptor
from src.tool_catalog import (
    ToolAnalyticsIdentityContractV1,
    ToolCatalogError,
)


def test_public_contract_resolves_canonical_identity_and_exact_event_fields():
    contract = build_tool_analytics_identity_contract()
    identity = contract.resolve("read_file")

    assert contract.CONTRACT_ID == "odysseus.tool_analytics_identity.v1"
    assert identity.to_event_fields() == {
        "tool_analytics_id": "read_file",
        "tool_family": "code_filesystem",
        "tool_source": "builtin",
    }
    assert identity.to_dict()["resolution"] == "canonical"
    assert identity.to_dict()["canonical_tool_id"] == "read_file"
    assert identity.to_dict()["alias_applied"] is False


def test_historical_alias_and_canonical_name_cannot_double_count():
    contract = build_tool_analytics_identity_contract()
    canonical = contract.resolve("manage_personal_docs")
    legacy = contract.resolve("manage_rag")

    assert HISTORICAL_TOOL_ALIASES == {"manage_rag": "manage_personal_docs"}
    assert legacy.analytics_id == canonical.analytics_id == "manage_personal_docs"
    assert legacy.family == canonical.family
    assert legacy.source == canonical.source
    assert legacy.to_dict()["resolution"] == "historical_alias"
    assert legacy.to_dict()["alias_applied"] is True
    assert legacy.to_event_fields() == canonical.to_event_fields()


@pytest.mark.parametrize(
    ("source", "expected_id"),
    [
        ("plugin", "dynamic.plugin.unclassified"),
        ("mcp", "dynamic.mcp.unclassified"),
        ("provider", "dynamic.provider.unclassified"),
        ("legacy", "legacy.unclassified"),
    ],
)
def test_unknown_identity_uses_non_personal_source_bucket(source, expected_id):
    private_runtime_id = "mcp__alice@example.test__session-private-note"

    identity = resolve_tool_analytics_identity(private_runtime_id, source=source)
    payload = identity.to_dict()

    assert identity.analytics_id == expected_id
    assert identity.family.value == "unclassified_dynamic"
    assert identity.canonical_tool_id is None
    assert identity.source_bucket is True
    assert "alice" not in repr(payload)
    assert "example.test" not in repr(payload)
    assert "session-private-note" not in repr(payload)
    assert payload["owner_identity_visible"] is False
    assert payload["session_identity_visible"] is False
    assert payload["source_identity_visible"] is False
    assert payload["raw_content_visible"] is False


def test_unknown_tools_in_same_dynamic_source_share_only_the_bounded_source_class():
    contract = build_tool_analytics_identity_contract()

    first = contract.resolve("owner-a-private-tool", source="plugin")
    second = contract.resolve("owner-b-private-tool", source="plugin")

    assert first.to_event_fields() == second.to_event_fields()
    assert first.analytics_id == "dynamic.plugin.unclassified"


def test_unreviewed_dynamic_descriptor_cannot_leak_tool_or_source_identity():
    descriptor = build_dynamic_tool_descriptor(
        "private_owner_tool",
        source="mcp",
        source_id="alice.example",
        description="Private owner operation.",
    )

    identity = build_tool_analytics_identity_contract().resolve_descriptor(descriptor)

    assert identity.analytics_id == "dynamic.mcp.unclassified"
    assert "private_owner_tool" not in repr(identity.to_dict())
    assert "alice.example" not in repr(identity.to_dict())


def test_unknown_builtin_fails_closed_without_echoing_the_supplied_identity():
    private_identity = "unknown_owner_alice"

    with pytest.raises(ToolCatalogError) as exc_info:
        resolve_tool_analytics_identity(private_identity, source="builtin")

    assert "unknown built-in analytics identity" in str(exc_info.value)
    assert private_identity not in str(exc_info.value)


def test_historical_alias_and_retired_analytics_ids_are_reserved_against_reuse():
    contract = build_tool_analytics_identity_contract()

    with pytest.raises(ToolCatalogError, match="cannot be recycled"):
        ToolAnalyticsIdentityContractV1.create(
            contract.catalog,
            historical_aliases={"read_file": "bash"},
        )
    with pytest.raises(ToolCatalogError, match="retired analytics identity cannot be reused"):
        ToolAnalyticsIdentityContractV1.create(
            contract.catalog,
            retired_analytics_ids={"read_file"},
        )


def test_contract_audit_is_aggregate_and_contains_only_bounded_source_buckets():
    audit = build_tool_analytics_identity_contract().audit_dict()

    assert audit["contract"] == "odysseus.tool_analytics_identity.v1"
    assert audit["descriptor_count"] == 84
    assert audit["historical_alias_count"] == 1
    assert audit["retired_analytics_id_count"] == 0
    assert dict(audit["dynamic_source_buckets"]) == {
        "legacy": "legacy.unclassified",
        "mcp": "dynamic.mcp.unclassified",
        "plugin": "dynamic.plugin.unclassified",
        "provider": "dynamic.provider.unclassified",
    }
    assert audit["owner_identity_visible"] is False
    assert audit["session_identity_visible"] is False
    assert audit["source_identity_visible"] is False
    assert audit["raw_content_visible"] is False
