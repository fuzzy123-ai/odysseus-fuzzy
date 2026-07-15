from __future__ import annotations

import pytest

from src.project_forge_contract import (
    ForgeProvider,
    ForgeProviderCapabilities,
    ProjectCommitRequest,
    ProjectCommitResult,
    ProjectForgeContractError,
    ProviderStatus,
)


def _commit_payload() -> dict[str, object]:
    return {
        "repo_id": "my-game",
        "title": "Persist sandbox artifacts",
        "description": "Store generated deliverables and link them to the project version.",
        "version_label": "v0.4",
        "change_notes": ["Add persistent output mount", "Record artifact manifest"],
        "reviewed_paths": ["src/project.py", "tests/test_project.py"],
        "checks_passed": True,
        "content_reviewed": True,
        "confirmed": True,
    }


def test_provider_capabilities_round_trip_and_unknown_capability_is_blocked() -> None:
    provider = ForgeProvider.create(provider="nextcloud", capabilities=("readable_tree", "manifest", "restore"))

    assert ForgeProvider.from_dict(provider.to_dict()) == provider
    assert provider.capabilities.supports("readable_tree") is True
    assert ForgeProviderCapabilities.create(provider="github", capabilities=()).capabilities == ()

    with pytest.raises(ProjectForgeContractError, match="unsupported capability"):
        ForgeProviderCapabilities.create(provider="github", capabilities=("force_push",))


def test_commit_request_round_trip_has_no_provider_selector() -> None:
    request = ProjectCommitRequest.from_dict(_commit_payload())

    assert request.ready_for_commit is True
    assert request.blockers == ()
    assert ProjectCommitRequest.from_dict(request.to_dict()) == request
    assert "provider" not in request.to_dict()

    with pytest.raises(ProjectForgeContractError, match="unknown fields: provider"):
        ProjectCommitRequest.from_dict({**_commit_payload(), "provider": "github"})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("description", "token=not-a-real-token"),
        ("description", "See https://user:password@example.invalid/repo"),
        ("description", "Generated at C:\\Users\\person\\project"),
        ("reviewed_paths", ["../outside.txt"]),
        ("reviewed_paths", [".git/config"]),
        ("reviewed_paths", ["/home/person/private.txt"]),
    ],
)
def test_commit_request_blocks_secret_credentials_and_private_paths(field: str, value: object) -> None:
    payload = _commit_payload()
    payload[field] = value

    with pytest.raises(ProjectForgeContractError):
        ProjectCommitRequest.from_dict(payload)


def test_commit_result_matches_transaction_contract_and_round_trips() -> None:
    result = ProjectCommitResult.create(
        transaction_id="pct_example123",
        repo_id="my-game",
        commit_sha="a" * 40,
        local_status="committed",
        provider_statuses={
            "github": "synced",
            "nextcloud": ProviderStatus.create(
                provider="nextcloud",
                status="sync_pending",
                retryable=True,
            ),
        },
        overall_status="partial",
        retry_scheduled=True,
    )

    payload = result.to_dict()
    assert payload["schema"] == "odysseus.project_commit_transaction.v1"
    assert payload["provider_statuses"] == {"nextcloud": "sync_pending", "github": "synced"}
    assert payload["retryable_providers"] == ["nextcloud"]
    restored = ProjectCommitResult.from_dict(payload)
    assert restored.to_dict() == payload
    assert restored.status_for("nextcloud").status == "sync_pending"


def test_result_derives_overall_and_retry_status_and_requires_full_sha() -> None:
    result = ProjectCommitResult.create(
        transaction_id="pct_derive123",
        repo_id="my-game",
        commit_sha="b" * 40,
        local_status="committed",
        provider_statuses={"github": "sync_pending"},
    )

    assert result.overall_status == "sync_pending"
    assert result.retry_scheduled is True
    with pytest.raises(ProjectForgeContractError, match="Git object id"):
        ProjectCommitResult.create(
            transaction_id="pct_shortsha123",
            repo_id="my-game",
            commit_sha="b" * 7,
            local_status="committed",
        )


def test_terminal_provider_failure_retries_only_when_explicitly_retryable() -> None:
    terminal = ProjectCommitResult.create(
        transaction_id="pct_terminal123",
        repo_id="my-game",
        commit_sha="c" * 40,
        local_status="committed",
        provider_statuses={"github": {"status": "failed", "retryable": False}},
    )
    retryable = ProjectCommitResult.create(
        transaction_id="pct_retryable123",
        repo_id="my-game",
        commit_sha="d" * 40,
        local_status="committed",
        provider_statuses={"github": {"status": "failed", "retryable": True}},
    )

    assert terminal.retry_scheduled is False
    assert retryable.retry_scheduled is True
    assert ProjectCommitResult.from_dict(terminal.to_dict()) == terminal
    assert ProjectCommitResult.from_dict(retryable.to_dict()) == retryable
    with pytest.raises(ProjectForgeContractError, match="Git object id"):
        ProjectCommitResult.create(
            transaction_id="pct_uppercase123",
            repo_id="my-game",
            commit_sha="A" * 40,
            local_status="committed",
        )


def test_reviewed_paths_are_limited_to_eighty_exact_file_references() -> None:
    payload = _commit_payload()
    payload["reviewed_paths"] = [f"src/file_{index}.py" for index in range(81)]
    with pytest.raises(ProjectForgeContractError, match="max length 80"):
        ProjectCommitRequest.from_dict(payload)

    payload["reviewed_paths"] = ["src/"]
    with pytest.raises(ProjectForgeContractError, match="exact file"):
        ProjectCommitRequest.from_dict(payload)


def test_direct_commit_request_factory_rejects_string_list_fields() -> None:
    payload = _commit_payload()

    with pytest.raises(ProjectForgeContractError, match="reviewed_paths must be a list"):
        ProjectCommitRequest.create(**{**payload, "reviewed_paths": "src/project.py"})

    with pytest.raises(ProjectForgeContractError, match="change_notes must be a list"):
        ProjectCommitRequest.create(**{**payload, "change_notes": "one note"})

    payload["reviewed_paths"] = ["src/*.py"]
    with pytest.raises(ProjectForgeContractError, match="unsupported characters or patterns"):
        ProjectCommitRequest.from_dict(payload)


def test_provider_status_rejects_secret_detail_and_unknown_status() -> None:
    with pytest.raises(ProjectForgeContractError, match="secret material"):
        ProviderStatus.create(provider="github", status="failed", detail="password=hunter22")
    with pytest.raises(ProjectForgeContractError, match="unsupported provider status"):
        ProviderStatus.create(provider="github", status="silently_overwritten")
    with pytest.raises(ProjectForgeContractError, match="retryable provider status"):
        ProviderStatus.create(provider="github", status="synced", retryable=True)
