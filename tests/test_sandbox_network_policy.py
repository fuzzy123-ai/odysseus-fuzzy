import pytest

from src.sandbox_network_policy import SandboxNetworkPolicyError, build_sandbox_network_policy


def test_network_policy_defaults_to_none():
    policy = build_sandbox_network_policy()

    assert policy.mode == "none"
    assert policy.allowlist == ()
    assert policy.allowed is True
    assert policy.to_dict()["raw_content_visible"] is False


def test_network_policy_requires_allowlist_hosts():
    blocked = build_sandbox_network_policy(mode="allowlist")

    assert blocked.allowed is False
    assert "allowlist_required" in blocked.reasons


def test_network_policy_accepts_bounded_public_hosts():
    policy = build_sandbox_network_policy(mode="allowlist", allowlist=["https://example.org/path", "api.example.org:443"])

    assert policy.allowed is True
    assert policy.allowlist == ("example.org", "api.example.org:443")


def test_network_policy_blocks_fullweb_and_loopback():
    fullweb = build_sandbox_network_policy(mode="fullweb")
    assert fullweb.allowed is False
    assert "fullweb_requires_separate_live_gate" in fullweb.reasons

    with pytest.raises(SandboxNetworkPolicyError):
        build_sandbox_network_policy(mode="allowlist", allowlist=["localhost"])
