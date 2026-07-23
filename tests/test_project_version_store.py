from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

import pytest

from src.project_version_store import (
    ProjectVersionConflictError,
    ProjectVersionIntegrityError,
    ProjectVersionStore,
    ProjectVersionStoreError,
    canonical_json_bytes,
    owner_key_for,
)


FIXED_NOW = datetime(2026, 7, 13, 8, 9, 10, 987654, tzinfo=timezone.utc)
ARTIFACT = {"path": "dist/game.zip", "sha256": "sha256:" + "b" * 64, "size": 42}


def _store(tmp_path) -> ProjectVersionStore:
    return ProjectVersionStore(root=tmp_path / "forge", clock=lambda: FIXED_NOW)


def _request(commit_sha: str = "a" * 40) -> dict[str, object]:
    return {
        "commit_sha": commit_sha,
        "version_label": "v1",
        "change_notes": ["Initial persistent version"],
        "policy_snapshot": {"schema": "policy.v1", "forge_mode": "local"},
        "artifacts": [ARTIFACT],
    }


def _persist(store: ProjectVersionStore, *, owner_id: str = "alice", repo_id: str = "game"):
    reservation = store.reserve_version(
        owner_id=owner_id,
        repo_id=repo_id,
        idempotency_key="request-1",
        request_payload=_request(),
    )
    stored = store.persist_version(
        reservation=reservation,
        commit_sha="a" * 40,
        version_label="v1",
        change_notes=("Initial persistent version",),
        policy_snapshot={"schema": "policy.v1", "forge_mode": "local"},
        artifacts=(ARTIFACT,),
    )
    return reservation, stored


def test_owner_scoped_layout_and_immutable_canonical_manifest(tmp_path) -> None:
    store = _store(tmp_path)
    reservation, stored = _persist(store)
    owner_key = owner_key_for("alice")
    project = tmp_path / "forge" / "owners" / owner_key / "projects" / "game"
    manifest_path = project / "versions" / reservation.version_id / "manifest.json"

    assert owner_key.startswith("own_") and len(owner_key) == 36
    assert (project / "repository.git").name == "repository.git"
    assert stored.created_at == "2026-07-13T08:09:10Z"
    assert stored.version_id.startswith("pv_")
    assert stored.transaction_id.startswith("pct_")
    assert manifest_path.read_bytes() == canonical_json_bytes(stored.manifest)
    assert stored.manifest_sha256 == "sha256:" + hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    assert "manifest_sha256" not in stored.manifest
    assert stored.manifest["version_label"] == "v1"
    assert stored.manifest["artifacts"][0]["sha256"] == "sha256:" + "b" * 64

    pointer = json.loads((project / "current.json").read_text(encoding="utf-8"))
    assert pointer["manifest_sha256"] == stored.manifest_sha256
    assert store.verify_version(owner_id="alice", repo_id="game", version_id=stored.version_id) == stored


def test_idempotent_replay_and_conflict_are_request_bound(tmp_path) -> None:
    store = _store(tmp_path)
    reservation, stored = _persist(store)

    replay = store.reserve_version(
        owner_id="alice",
        repo_id="game",
        idempotency_key="request-1",
        request_payload=_request(),
    )
    assert replay.replay is True
    assert replay.version_id == stored.version_id
    assert replay.transaction_id == reservation.transaction_id

    with pytest.raises(ProjectVersionConflictError, match="different request"):
        store.reserve_version(
            owner_id="alice",
            repo_id="game",
            idempotency_key="request-1",
            request_payload=_request("c" * 40),
        )


def test_manifest_tamper_fails_closed_while_current_pointer_is_only_a_cache(tmp_path) -> None:
    store = _store(tmp_path)
    reservation, stored = _persist(store)
    project = store.project_root(owner_id="alice", repo_id="game")

    (project / "current.json").write_text("{}\n", encoding="utf-8")
    assert store.verify_version(owner_id="alice", repo_id="game", version_id=stored.version_id) == stored

    manifest_path = project / "versions" / reservation.version_id / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["commit_sha"] = "c" * 40
    manifest_path.write_bytes(canonical_json_bytes(payload))
    with pytest.raises(ProjectVersionIntegrityError, match="hash"):
        store.verify_version(owner_id="alice", repo_id="game", version_id=stored.version_id)


def test_incomplete_staging_is_not_a_version_and_finished_version_is_idempotent_but_immutable(tmp_path) -> None:
    store = _store(tmp_path)
    reservation, stored = _persist(store)
    project = store.project_root(owner_id="alice", repo_id="game")
    incomplete_id = "pv_" + "d" * 32
    staging = project / ".staging" / ("pct_" + "e" * 32)
    staging.mkdir(parents=True)
    (staging / "manifest.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ProjectVersionIntegrityError, match="incomplete or missing"):
        store.load_version(owner_id="alice", repo_id="game", version_id=incomplete_id)
    assert store.persist_version(
        reservation=reservation,
        commit_sha=stored.commit_sha,
        version_label="v1",
        change_notes=("Initial persistent version",),
        policy_snapshot={"schema": "policy.v1", "forge_mode": "local"},
        artifacts=(ARTIFACT,),
    ) == stored
    with pytest.raises(ProjectVersionConflictError, match="reserved request"):
        store.persist_version(reservation=reservation, commit_sha=stored.commit_sha, version_label="v2")


def test_restart_reconciles_existing_manifest_and_mutable_records(tmp_path) -> None:
    store = _store(tmp_path)
    reservation, stored = _persist(store)
    project = store.project_root(owner_id="alice", repo_id="game")
    idempotency_path = project / "idempotency" / f"{reservation.idempotency_digest}.json"
    idempotency = json.loads(idempotency_path.read_text(encoding="utf-8"))
    idempotency["status"] = "created"
    idempotency.pop("manifest_sha256", None)
    idempotency_path.write_bytes(canonical_json_bytes(idempotency))

    resumed = store.reserve_version(
        owner_id="alice",
        repo_id="game",
        idempotency_key="request-1",
        request_payload=_request(),
    )
    assert resumed.replay is False
    assert store.persist_version(
        reservation=resumed,
        commit_sha=stored.commit_sha,
        version_label="v1",
        change_notes=("Initial persistent version",),
        policy_snapshot={"schema": "policy.v1", "forge_mode": "local"},
        artifacts=(ARTIFACT,),
    ) == stored
    assert store.reserve_version(
        owner_id="alice",
        repo_id="game",
        idempotency_key="request-1",
        request_payload=_request(),
    ).replay is True


def test_iter_verified_versions_uses_fail_closed_public_verification(tmp_path) -> None:
    store = _store(tmp_path)
    _, stored = _persist(store)

    assert store.iter_verified_versions(owner_id="alice", repo_id="game") == (stored,)

    versions = store.project_root(owner_id="alice", repo_id="game") / "versions"
    (versions / "unexpected").mkdir()
    with pytest.raises(ProjectVersionIntegrityError, match="invalid version identity"):
        store.iter_verified_versions(owner_id="alice", repo_id="game")


@pytest.mark.parametrize("owner_id", ["../alice", "alice/bob", "token=not-a-token"])
def test_unsafe_owner_is_blocked(tmp_path, owner_id: str) -> None:
    with pytest.raises((ProjectVersionStoreError, ValueError)):
        _store(tmp_path).project_root(owner_id=owner_id, repo_id="game")


@pytest.mark.parametrize("repo_id", ["../game", "game/subdir", "..", "-option"])
def test_traversal_and_unsafe_repo_ids_are_blocked(tmp_path, repo_id: str) -> None:
    with pytest.raises(ProjectVersionStoreError):
        _store(tmp_path).project_root(owner_id="alice", repo_id=repo_id)


@pytest.mark.parametrize(
    "artifacts",
    [
        ({"path": "../secret.txt", "sha256": "b" * 64, "size": 1},),
        ({"path": ".env", "sha256": "b" * 64, "size": 1},),
        ({"path": "dist/a.zip", "sha256": "not-a-hash", "size": 1},),
        ({"path": "dist/a.zip", "sha256": "b" * 64, "size": 1, "provider_raw": "x"},),
    ],
)
def test_unsafe_artifact_metadata_is_blocked(tmp_path, artifacts) -> None:
    store = _store(tmp_path)
    reservation = store.reserve_version(
        owner_id="alice",
        repo_id="game",
        idempotency_key="unsafe-artifact",
        request_payload=_request(),
    )
    with pytest.raises((ProjectVersionStoreError, ValueError)):
        store.persist_version(reservation=reservation, commit_sha="a" * 40, artifacts=artifacts)


def test_secret_or_provider_raw_policy_snapshot_is_blocked(tmp_path) -> None:
    store = _store(tmp_path)
    reservation = store.reserve_version(
        owner_id="alice",
        repo_id="game",
        idempotency_key="unsafe-policy",
        request_payload=_request(),
    )
    with pytest.raises(ProjectVersionStoreError, match="unsafe provider or secret"):
        store.persist_version(
            reservation=reservation,
            commit_sha="a" * 40,
            policy_snapshot={"github": {"credential_url": "https://example.invalid/private"}},
        )
