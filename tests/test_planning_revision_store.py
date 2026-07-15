from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.planning_definition_contract import validate_planning_definition
from src.planning_revision_store import (
    TEMPORARY_REPOSITORY_MARKER,
    TEMPORARY_REPOSITORY_SCHEMA,
    PlanningRevisionRepository,
    PlanningRevisionRepositoryError,
)
from tests.test_planning_definition_projection import definition_fixture


OWNER = "alice"
PROJECT = "project-a"
ROADMAP = "roadmap-a"


def _repository(tmp_path: Path) -> tuple[PlanningRevisionRepository, Path, dict]:
    root = tmp_path / "temporary-planning-repository"
    root.mkdir()
    (root / TEMPORARY_REPOSITORY_MARKER).write_text(
        TEMPORARY_REPOSITORY_SCHEMA + "\n",
        encoding="utf-8",
    )
    document = definition_fixture(PROJECT, ROADMAP, include_draft=False)
    source = root / "definition.json"
    source.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    repository = PlanningRevisionRepository(
        root,
        owner=OWNER,
        cursor_secret=b"0123456789abcdef0123456789abcdef",
    )
    return repository, source, document


def _base(document: dict) -> tuple[int, str]:
    roadmap = document["roadmaps"][0]
    return roadmap["revision"], roadmap["content_hash"]


def _create(
    repository: PlanningRevisionRepository,
    document: dict,
    *,
    key: str = "create-key-0001",
    changes: dict | None = None,
) -> dict:
    revision, content_hash = _base(document)
    return repository.create_draft(
        OWNER,
        PROJECT,
        ROADMAP,
        base_revision=revision,
        base_hash=content_hash,
        idempotency_key=key,
        changes=changes
        or {"operation": "update", "set": {"title": "Accepted title"}},
    )


def _validate(repository: PlanningRevisionRepository, draft: dict) -> dict:
    return repository.validate_draft(
        OWNER,
        PROJECT,
        ROADMAP,
        draft["draft_id"],
        expected_draft_version=draft["draft_version"],
    )


def _accept(
    repository: PlanningRevisionRepository,
    draft: dict,
    validation: dict,
    *,
    key: str = "accept-key-0001",
) -> dict:
    return repository.act_on_draft(
        OWNER,
        PROJECT,
        ROADMAP,
        draft["draft_id"],
        action="accept",
        expected_draft_version=validation["draft_version"],
        idempotency_key=key,
    )


def _read(source: Path) -> dict:
    return json.loads(source.read_text(encoding="utf-8"))


def test_repository_requires_exact_temporary_marker_and_root_file(tmp_path: Path) -> None:
    root = tmp_path / "not-authorized"
    root.mkdir()
    (root / "definition.json").write_text("{}", encoding="utf-8")

    with pytest.raises(PlanningRevisionRepositoryError) as missing_marker:
        PlanningRevisionRepository(root, owner=OWNER)
    assert missing_marker.value.code == "planning_write_gate_required"

    (root / TEMPORARY_REPOSITORY_MARKER).write_text("wrong", encoding="utf-8")
    with pytest.raises(PlanningRevisionRepositoryError) as wrong_marker:
        PlanningRevisionRepository(root, owner=OWNER)
    assert wrong_marker.value.code == "planning_write_gate_required"

    (root / TEMPORARY_REPOSITORY_MARKER).write_text(
        TEMPORARY_REPOSITORY_SCHEMA,
        encoding="utf-8",
    )
    with pytest.raises(PlanningRevisionRepositoryError) as traversal:
        PlanningRevisionRepository(root, owner=OWNER, definition_file="../definition.json")
    assert traversal.value.code == "invalid_definition_file"


def test_create_validate_accept_readback_and_undo_restore_exact_previous_hash(
    tmp_path: Path,
) -> None:
    repository, source, initial = _repository(tmp_path)
    initial_bytes = source.read_bytes()
    initial_hash = initial["roadmaps"][0]["content_hash"]

    draft = _create(repository, initial)
    validation = _validate(repository, draft)
    assert source.read_bytes() == initial_bytes

    accepted = _accept(repository, draft, validation)
    applied = _read(source)
    validate_planning_definition(applied)
    latest = applied["project"]["latest_approved_revision"][ROADMAP]
    accepted_roadmap = next(
        item for item in applied["roadmaps"] if item["revision"] == accepted["accepted_revision"]
    )

    assert accepted["readback_verified"] is True
    assert accepted["accepted_revision"] == 2
    assert accepted["accepted_hash"] == latest["content_hash"]
    assert accepted_roadmap["title"] == "Accepted title"
    assert accepted_roadmap["content_hash"] == accepted["accepted_hash"]

    undone = repository.undo_apply(
        OWNER,
        accepted["undo_id"],
        idempotency_key="undo-key-0001",
    )
    restored = _read(source)
    retained = next(
        item for item in restored["roadmaps"] if item["revision"] == accepted["accepted_revision"]
    )

    validate_planning_definition(restored)
    assert undone["restored_hash"] == initial_hash
    assert restored["project"]["latest_approved_revision"][ROADMAP]["content_hash"] == initial_hash
    assert retained["content_hash"] == accepted["accepted_hash"]
    assert undone["accepted_hash_retained"] == accepted["accepted_hash"]


def test_create_action_and_undo_idempotency_return_original_receipt_across_restart(
    tmp_path: Path,
) -> None:
    repository, _source, initial = _repository(tmp_path)
    draft = _create(repository, initial)
    duplicate_draft = _create(repository, initial)
    validation = _validate(repository, draft)
    accepted = _accept(repository, draft, validation)
    duplicate_accept = _accept(repository, draft, validation)

    restarted = PlanningRevisionRepository(
        repository.root,
        owner=OWNER,
        cursor_secret=b"0123456789abcdef0123456789abcdef",
    )
    duplicate_after_restart = _accept(restarted, draft, validation)
    undone = restarted.undo_apply(
        OWNER,
        accepted["undo_id"],
        idempotency_key="undo-key-0002",
    )
    duplicate_undo = restarted.undo_apply(
        OWNER,
        accepted["undo_id"],
        idempotency_key="undo-key-0002",
    )

    assert duplicate_draft == draft
    assert duplicate_accept == accepted
    assert duplicate_after_restart == accepted
    assert duplicate_undo == undone


def test_reused_idempotency_key_with_different_request_is_rejected(tmp_path: Path) -> None:
    repository, _source, initial = _repository(tmp_path)
    _create(repository, initial, key="same-key-0001")

    with pytest.raises(PlanningRevisionRepositoryError) as raised:
        _create(
            repository,
            initial,
            key="same-key-0001",
            changes={"operation": "update", "set": {"title": "Different"}},
        )

    assert raised.value.code == "idempotency_conflict"


def test_failed_validation_leaves_definition_source_byte_identical(tmp_path: Path) -> None:
    repository, source, initial = _repository(tmp_path)
    before = source.read_bytes()
    invalid_nodes = deepcopy(initial["roadmaps"][0]["nodes"])
    invalid_nodes[0]["run_id"] = "runtime-leak"
    draft = _create(
        repository,
        initial,
        changes={"operation": "update", "set": {"nodes": invalid_nodes}},
    )

    with pytest.raises(PlanningRevisionRepositoryError) as raised:
        _validate(repository, draft)

    assert raised.value.code == "runtime_field_forbidden"
    assert source.read_bytes() == before


def test_concurrent_drafts_conflict_after_first_atomic_accept(tmp_path: Path) -> None:
    repository, source, initial = _repository(tmp_path)
    first = _create(repository, initial, key="create-first-0001")
    second = _create(
        repository,
        initial,
        key="create-second-0001",
        changes={"operation": "update", "set": {"title": "Second title"}},
    )
    first_validation = _validate(repository, first)
    second_validation = _validate(repository, second)
    second_instance = PlanningRevisionRepository(
        repository.root,
        owner=OWNER,
        cursor_secret=b"0123456789abcdef0123456789abcdef",
    )

    def attempt(selected_repository, draft, validation, key):
        try:
            return _accept(selected_repository, draft, validation, key=key)
        except PlanningRevisionRepositoryError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda args: attempt(*args),
                (
                    (repository, first, first_validation, "accept-first-0001"),
                    (second_instance, second, second_validation, "accept-second-0001"),
                ),
            )
        )

    accepted = [item for item in results if isinstance(item, dict)]
    rejected = [item for item in results if isinstance(item, PlanningRevisionRepositoryError)]
    assert len(accepted) == 1
    assert len(rejected) == 1
    assert rejected[0].code == "base_revision_conflict"
    assert _read(source)["project"]["latest_approved_revision"][ROADMAP]["content_hash"] == accepted[0]["accepted_hash"]


def test_draft_version_conflict_and_discard_never_mutate_source(tmp_path: Path) -> None:
    repository, source, initial = _repository(tmp_path)
    before = source.read_bytes()
    draft = _create(repository, initial)

    with pytest.raises(PlanningRevisionRepositoryError) as wrong_version:
        repository.validate_draft(
            OWNER,
            PROJECT,
            ROADMAP,
            draft["draft_id"],
            expected_draft_version=99,
        )
    assert wrong_version.value.code == "draft_version_conflict"

    discarded = repository.act_on_draft(
        OWNER,
        PROJECT,
        ROADMAP,
        draft["draft_id"],
        action="discard",
        expected_draft_version=1,
        idempotency_key="discard-key-0001",
    )
    duplicate = repository.act_on_draft(
        OWNER,
        PROJECT,
        ROADMAP,
        draft["draft_id"],
        action="discard",
        expected_draft_version=1,
        idempotency_key="discard-key-0001",
    )

    assert discarded == duplicate
    assert discarded["source_mutated"] is False
    assert source.read_bytes() == before


def test_tombstone_then_restore_creates_immutable_definition_revisions(tmp_path: Path) -> None:
    repository, source, initial = _repository(tmp_path)
    tombstone = _create(
        repository,
        initial,
        key="create-tombstone-0001",
        changes={"operation": "tombstone", "set": {}},
    )
    tombstone_validation = _validate(repository, tombstone)
    tombstone_receipt = _accept(
        repository,
        tombstone,
        tombstone_validation,
        key="accept-tombstone-0001",
    )
    tombstoned = _read(source)
    assert ROADMAP not in tombstoned["project"]["latest_approved_revision"]
    tombstone_revision = next(
        item for item in tombstoned["roadmaps"] if item["revision"] == 2
    )
    assert tombstone_revision["revision_state"] == "tombstoned"

    restore = repository.create_draft(
        OWNER,
        PROJECT,
        ROADMAP,
        base_revision=tombstone_receipt["accepted_revision"],
        base_hash=tombstone_receipt["accepted_hash"],
        idempotency_key="create-restore-0001",
        changes={"operation": "restore", "set": {}, "restore_revision": 1},
    )
    restore_validation = _validate(repository, restore)
    restored_receipt = _accept(
        repository,
        restore,
        restore_validation,
        key="accept-restore-0001",
    )
    restored = _read(source)

    validate_planning_definition(restored)
    assert restored_receipt["accepted_revision"] == 3
    assert restored["project"]["latest_approved_revision"][ROADMAP]["revision"] == 3
    assert next(item for item in restored["roadmaps"] if item["revision"] == 2)["content_hash"] == tombstone_receipt["accepted_hash"]
    assert next(item for item in restored["roadmaps"] if item["revision"] == 3)["revision_state"] == "approved"


def test_state_persistence_failure_rolls_back_atomic_source_apply(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository, source, initial = _repository(tmp_path)
    draft = _create(repository, initial)
    validation = _validate(repository, draft)
    before = source.read_bytes()

    def fail_state(_state):
        raise OSError("simulated state persistence failure")

    monkeypatch.setattr(repository, "_save_state", fail_state)
    with pytest.raises(OSError):
        _accept(repository, draft, validation)

    assert source.read_bytes() == before
    validate_planning_definition(_read(source))


def test_owner_scope_is_indistinguishable_from_missing_project(tmp_path: Path) -> None:
    repository, _source, initial = _repository(tmp_path)
    revision, content_hash = _base(initial)

    with pytest.raises(PlanningRevisionRepositoryError) as raised:
        repository.create_draft(
            "bob",
            PROJECT,
            ROADMAP,
            base_revision=revision,
            base_hash=content_hash,
            idempotency_key="owner-scope-0001",
            changes={"operation": "update", "set": {"title": "No access"}},
        )

    assert raised.value.code == "project_not_found"


@pytest.mark.parametrize(
    "changes",
    [
        {"operation": "update", "set": {}},
        {"operation": "tombstone", "set": {"title": "not allowed"}},
        {"operation": "restore", "set": {}, "restore_revision": 0},
        {"operation": "update", "set": {"revision": 2}},
        {"operation": "run", "set": {}},
    ],
)
def test_structural_diff_contract_is_bounded_and_closed(tmp_path: Path, changes: dict) -> None:
    repository, _source, initial = _repository(tmp_path)

    with pytest.raises(PlanningRevisionRepositoryError):
        _create(repository, initial, changes=changes)
