from datetime import datetime, timedelta, timezone

import pytest

from src.mcp_client_profiles import (
    MCP_CLIENT_PROFILE_SCHEMA,
    McpClientProfile,
    McpClientProfileError,
    build_mcp_client_profile,
)


NOW = datetime(2026, 7, 6, 12, 0, tzinfo=timezone.utc)


def test_disabled_profile_maps_to_default_deny_options():
    profile = McpClientProfile.create(
        client_id="codex-local",
        label="Codex local",
        scopes=["private_reads", "filesystem_reads", "generic_api", "owner_scoped_writes"],
        enabled=False,
    )

    options = profile.to_policy_options(now=NOW)

    assert options.allow_private_reads is False
    assert options.allow_filesystem_reads is False
    assert options.allow_generic_api is False
    assert options.allow_owner_scoped_writes is False
    assert options.expose_all is False


def test_enabled_profile_translates_scopes_to_policy_options():
    profile = McpClientProfile.create(
        client_id="codex-local",
        label="Codex local",
        owner="alice",
        scopes=["owner_scoped_writes", "private_reads", "owner_scoped_writes"],
        enabled=True,
        reason="local review session",
        expires_at=NOW + timedelta(hours=1),
    )

    options = profile.to_policy_options(now=NOW)

    assert profile.scopes == ("owner_scoped_writes", "private_reads")
    assert options.allow_owner_scoped_writes is True
    assert options.allow_private_reads is True
    assert options.allow_filesystem_reads is False
    assert options.allow_generic_api is False


def test_enabled_sensitive_profile_requires_expiry_and_reason():
    with pytest.raises(McpClientProfileError, match="expires_at"):
        McpClientProfile.create(
            client_id="client",
            label="Client",
            owner="alice",
            scopes=["private_reads"],
            enabled=True,
            reason="review",
        )

    with pytest.raises(McpClientProfileError, match="reason"):
        McpClientProfile.create(
            client_id="client",
            label="Client",
            owner="alice",
            scopes=["owner_scoped_writes"],
            enabled=True,
        )


def test_expired_profile_is_rejected_when_enabled():
    with pytest.raises(McpClientProfileError, match="expired"):
        McpClientProfile.create(
            client_id="client",
            label="Client",
            owner="alice",
            scopes=["private_reads"],
            enabled=True,
            reason="review",
            expires_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
        )


def test_payload_builder_accepts_boolean_scope_flags():
    profile = build_mcp_client_profile({
        "id": "client",
        "name": "Client",
        "owner": "alice",
        "enabled": True,
        "private_reads": True,
        "filesystem_reads": True,
        "reason": "bounded review",
        "expires_at": (NOW + timedelta(hours=2)).isoformat(),
    })

    assert profile.client_id == "client"
    assert profile.scopes == ("filesystem_reads", "private_reads")
    assert profile.to_policy_options(now=NOW).allow_filesystem_reads is True


def test_public_payload_is_redacted_and_marks_sensitive_scopes():
    profile = McpClientProfile.create(
        client_id="client",
        label="Client",
        owner="alice",
        scopes=["private_reads", "generic_api"],
        enabled=True,
        reason="bounded review",
        expires_at=NOW + timedelta(hours=1),
        created_at=NOW,
    )

    payload = profile.to_public_dict(now=NOW)

    assert payload["schema"] == MCP_CLIENT_PROFILE_SCHEMA
    assert payload["active"] is True
    assert payload["sensitive_scopes"] == ("generic_api", "private_reads")
    assert payload["token_value_visible"] is False
    assert payload["secret_value_visible"] is False
    assert payload["expose_all_supported"] is False
