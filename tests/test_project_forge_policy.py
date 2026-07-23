from __future__ import annotations

from unittest.mock import patch

import pytest

from src.project_forge_policy import (
    GitHubForgePolicy,
    NextcloudForgePolicy,
    ProjectForgePolicy,
    ProjectForgePolicyError,
    ProjectForgePolicyStore,
    resolve_commit_providers,
)


def test_local_policy_has_no_external_sync_targets() -> None:
    policy = ProjectForgePolicy()

    assert policy.configured_providers == ("local",)
    assert resolve_commit_providers(policy) == ()


def test_nextcloud_primary_policy_uses_same_dispatch_contract() -> None:
    policy = ProjectForgePolicy(forge_mode="nextcloud")

    assert policy.configured_providers == ("local", "nextcloud")
    assert resolve_commit_providers(policy) == ("nextcloud",)
    assert policy.nextcloud.client_side_encryption is False
    assert policy.nextcloud.include_readable_tree is True


def test_github_with_optional_nextcloud_dual_backup_round_trips() -> None:
    policy = ProjectForgePolicy(
        forge_mode="github",
        sync_on_commit=True,
        backup_providers=("nextcloud",),
        nextcloud=NextcloudForgePolicy(include_git_bundle=True),
        github=GitHubForgePolicy(push_branch=True),
    )

    assert policy.configured_providers == ("local", "github", "nextcloud")
    assert resolve_commit_providers(policy) == ("github", "nextcloud")
    assert ProjectForgePolicy.from_dict(policy.to_dict()) == policy

    with patch("src.project_forge_policy.atomic_write_json") as writer:
        policy.save_json("forge-policy.json")
    writer.assert_called_once_with("forge-policy.json", policy.to_dict(), indent=2)


def test_sync_disabled_retains_configuration_without_dispatching() -> None:
    policy = ProjectForgePolicy(forge_mode="github", sync_on_commit=False, backup_providers=("nextcloud",))

    assert policy.configured_providers == ("local", "github", "nextcloud")
    assert resolve_commit_providers(policy) == ()


@pytest.mark.parametrize(
    "payload",
    [
        {"schema": "odysseus.project_forge_policy.v1", "forge_mode": "gitlab"},
        {
            "schema": "odysseus.project_forge_policy.v1",
            "forge_mode": "local",
            "backup_providers": ["nextcloud"],
        },
        {
            "schema": "odysseus.project_forge_policy.v1",
            "forge_mode": "github",
            "backup_providers": ["dropbox"],
        },
        {
            "schema": "odysseus.project_forge_policy.v1",
            "forge_mode": "nextcloud",
            "nextcloud": {"client_side_encryption": True},
        },
        {
            "schema": "odysseus.project_forge_policy.v1",
            "forge_mode": "github",
            "github": {"credential_url": "https://user:pass@example.invalid/repo"},
        },
    ],
)
def test_policy_rejects_unsupported_or_unsafe_configuration(payload: dict[str, object]) -> None:
    with pytest.raises(ProjectForgePolicyError):
        ProjectForgePolicy.from_dict(payload)


def test_policy_requires_exact_schema_and_boolean_types() -> None:
    with pytest.raises(ProjectForgePolicyError, match="schema"):
        ProjectForgePolicy.from_dict({"schema": "odysseus.project_forge_policy.v2"})
    with pytest.raises(ProjectForgePolicyError, match="boolean"):
        ProjectForgePolicy.from_dict(
            {
                "schema": "odysseus.project_forge_policy.v1",
                "forge_mode": "github",
                "sync_on_commit": "true",
            }
        )


def test_owner_scoped_policy_store_defaults_local_and_roundtrips(tmp_path) -> None:
    store = ProjectForgePolicyStore(root=tmp_path / "policies")

    assert store.load_policy(owner_id="alice@example.test", repo_id="demo").forge_mode == "local"
    saved = store.save_policy(
        owner_id="alice@example.test",
        repo_id="demo",
        policy=ProjectForgePolicy(
            forge_mode="github",
            backup_providers=("nextcloud",),
        ),
    )

    assert saved.forge_mode == "github"
    assert saved.backup_providers == ("nextcloud",)
    assert store.load_policy(owner_id="bob@example.test", repo_id="demo").forge_mode == "local"
    files = list((tmp_path / "policies").rglob("*.json"))
    assert len(files) == 1
    assert "alice@example.test" not in str(files[0])


def test_policy_store_fails_closed_on_invalid_persisted_payload(tmp_path) -> None:
    store = ProjectForgePolicyStore(root=tmp_path / "policies")
    store.save_policy(
        owner_id="owner@example.test",
        repo_id="demo",
        policy=ProjectForgePolicy(),
    )
    path = next((tmp_path / "policies").rglob("*.json"))
    path.write_text('{"schema":"unsupported"}', encoding="utf-8")

    with pytest.raises(ProjectForgePolicyError):
        store.load_policy(owner_id="owner@example.test", repo_id="demo")
