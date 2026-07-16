from dataclasses import FrozenInstanceError

import pytest

from src.builtin_tool_catalog import (
    CATALOG_TOOL_IDS,
    build_builtin_analytics_identity_contract,
)
from src.tool_catalog import (
    ToolAnalyticsIdentity,
    ToolAvailability,
    ToolCatalogError,
    ToolDescriptorV2,
    ToolDescriptorV2Index,
    ToolEffectClass,
    ToolFamily,
    ToolLifecycle,
    ToolPermission,
    ToolRiskLevel,
    ToolSource,
    ToolVisibility,
)


def _descriptor(**overrides) -> ToolDescriptorV2:
    values = {
        "tool_id": "read_file",
        "analytics_id": "read-file",
        "display_name": "Read file",
        "description": "Read a repository file.",
        "family": ToolFamily.CODE_FILESYSTEM,
        "source": ToolSource.BUILTIN,
        "lifecycle": ToolLifecycle.ACTIVE,
        "availability": ToolAvailability.AVAILABLE,
        "default_enabled": False,
        "default_visibility": ToolVisibility.VISIBLE,
        "risk_level": ToolRiskLevel.SAFE,
        "permission": ToolPermission.OWNER,
        "effect_class": ToolEffectClass.READ,
        "requires_confirmation": False,
        "schema_ref": "function:read_file",
        "handler_ref": "agent_tools:read_file",
        "prompt_ref": "tool_index:read_file",
        "aliases": ("legacy_read_file",),
        "introduced_in": "legacy-v1",
    }
    values.update(overrides)
    return ToolDescriptorV2.create(**values)


def test_aliases_resolve_to_one_canonical_counting_identity():
    descriptor_index = ToolDescriptorV2Index.build([_descriptor()])

    contract = descriptor_index.analytics_identity_contract()
    canonical = contract.resolve("read_file")
    legacy = contract.resolve("legacy_read_file")

    assert canonical is legacy
    assert canonical.analytics_id == "read-file"
    assert contract.counting_key_for("read_file") == "read-file"
    assert contract.counting_key_for("legacy_read_file") == "read-file"
    assert contract.counting_key_for("missing") is None
    assert len(contract.identities) == 1
    assert contract.to_public_dict()["alias_count"] == 1


def test_published_analytics_id_reservation_blocks_recycling():
    retired = _descriptor(
        lifecycle=ToolLifecycle.DEPRECATED,
        deprecated_in="0.24",
    )
    old_contract = ToolDescriptorV2Index.build(
        [retired]
    ).analytics_identity_contract()
    reservations = dict(old_contract.analytics_id_reservations)
    replacement = _descriptor(
        tool_id="replacement_reader",
        aliases=(),
    )

    with pytest.raises(ToolCatalogError, match="permanently reserved"):
        ToolDescriptorV2Index.build(
            [replacement]
        ).analytics_identity_contract(
            historical_reservations=reservations,
        )


def test_original_tool_can_carry_its_reserved_identity_forward():
    contract = ToolDescriptorV2Index.build([_descriptor()]).analytics_identity_contract(
        historical_reservations={"read-file": "read_file"},
    )

    assert contract.analytics_id_reservations == (("read-file", "read_file"),)


def test_historical_alias_remains_losslessly_resolvable():
    descriptor_without_current_alias = _descriptor(aliases=())

    contract = ToolDescriptorV2Index.build(
        [descriptor_without_current_alias]
    ).analytics_identity_contract(
        historical_alias_targets={"legacy_read_file": "read_file"},
    )

    assert contract.resolve("legacy_read_file") == contract.resolve("read_file")
    assert contract.alias_targets == (("legacy_read_file", "read_file"),)


def test_historical_alias_cannot_be_reassigned_or_orphaned():
    replacement = _descriptor(
        tool_id="replacement_reader",
        analytics_id="replacement-reader",
        aliases=("legacy_read_file",),
    )

    with pytest.raises(ToolCatalogError, match="permanently assigned"):
        ToolDescriptorV2Index.build(
            [_descriptor(aliases=()), replacement]
        ).analytics_identity_contract(
            historical_alias_targets={"legacy_read_file": "read_file"},
        )

    with pytest.raises(ToolCatalogError, match="must remain a canonical identity"):
        ToolDescriptorV2Index.build(
            [replacement]
        ).analytics_identity_contract(
            historical_alias_targets={"old_reader": "read_file"},
        )


def test_unknown_dynamic_identity_is_bounded_and_non_personal():
    descriptor = ToolDescriptorV2.conservative_dynamic(
        tool_id="dynamic:unclassified",
        display_name="Operator supplied label",
        description="Runtime supplied description.",
    )

    identity = descriptor.analytics_identity()
    public = identity.to_public_dict()

    assert identity.family == ToolFamily.UNCLASSIFIED_DYNAMIC
    assert identity.source == ToolSource.DYNAMIC
    assert public == {
        "schema_version": "odysseus.tool_analytics_identity.v1",
        "tool_id": "dynamic:unclassified",
        "analytics_id": "dynamic-unclassified",
        "family": "unclassified_dynamic",
        "source": "dynamic",
        "aliases": (),
        "retired": False,
    }


def test_public_contract_has_only_content_free_identity_fields():
    contract = ToolDescriptorV2Index.build([_descriptor()]).analytics_identity_contract()
    payload = contract.to_public_dict()

    assert set(payload["identities"][0]) == {
        "schema_version",
        "tool_id",
        "analytics_id",
        "family",
        "source",
        "aliases",
        "retired",
    }
    assert payload["raw_content_visible"] is False
    assert payload["owner_data_visible"] is False
    assert payload["session_data_visible"] is False
    assert payload["provider_payload_visible"] is False
    assert payload["secret_values_visible"] is False


def test_builtin_catalog_has_one_public_identity_and_reservation_per_tool():
    descriptions = {
        tool_id: f"Use the {tool_id.replace('_', ' ')} built-in capability."
        for tool_id in CATALOG_TOOL_IDS
    }

    contract = build_builtin_analytics_identity_contract(descriptions)

    assert contract.schema_version == "odysseus.tool_analytics_identity.v1"
    assert len(contract.identities) == len(CATALOG_TOOL_IDS) == 84
    assert len(contract.analytics_id_reservations) == 84
    assert {identity.source for identity in contract.identities} == {
        ToolSource.BUILTIN
    }
    assert contract.to_public_dict()["identity_count"] == 84


@pytest.mark.parametrize(
    "reservations",
    [
        {"Read_File": "read_file"},
        {"read-file": "unsafe tool id"},
    ],
)
def test_malformed_historical_reservations_fail_closed(reservations):
    with pytest.raises(ToolCatalogError):
        ToolDescriptorV2Index.build([_descriptor()]).analytics_identity_contract(
            historical_reservations=reservations,
        )


def test_public_identity_values_are_immutable():
    identity = _descriptor().analytics_identity()

    assert isinstance(identity, ToolAnalyticsIdentity)
    with pytest.raises(FrozenInstanceError):
        identity.analytics_id = "replacement-id"
