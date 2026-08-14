import pytest

from src.unified_source_index_owner_scope import (
    OwnerAlias,
    OwnerScope,
    OwnerScopeError,
    OwnerScopeRegistry,
)


ALICE_SUBJECT = "owner_0123456789abcdef0123456789abcdef"
BOB_SUBJECT = "owner_fedcba9876543210fedcba9876543210"


def _scope(subject_id=ALICE_SUBJECT):
    return OwnerScope.for_subject_id(subject_id)


def test_subject_backed_scope_is_opaque_stable_and_not_a_username():
    scope = _scope()

    assert scope.value == f"owner:{ALICE_SUBJECT}"
    assert scope.subject_id == ALICE_SUBJECT
    assert "alice" not in scope.value
    assert _scope() == scope


def test_rename_chain_preserves_owner_scope_and_locator_for_legacy_refs():
    owner = _scope()
    registry = OwnerScopeRegistry(
        (
            OwnerAlias("alice", owner, 1),
            OwnerAlias("alice-renamed", owner, 2, previous_alias="alice"),
            OwnerAlias("alice-final", owner, 3, previous_alias="alice-renamed"),
        )
    )

    migrated = [
        registry.migrate_legacy_source_ref(f"user:{alias}", "document:opaque-locator")
        for alias in ("alice", "alice-renamed", "alice-final")
    ]

    assert {item.owner_scope for item in migrated} == {owner}
    assert {item.locator for item in migrated} == {"document:opaque-locator"}
    assert registry.alias_version("alice-final") == 3


def test_alias_reuse_cross_owner_duplicate_and_cycle_cases_fail_closed():
    alice = _scope()
    bob = _scope(BOB_SUBJECT)

    with pytest.raises(OwnerScopeError, match="reused"):
        OwnerScopeRegistry((OwnerAlias("shared", alice, 1), OwnerAlias("shared", bob, 2)))
    with pytest.raises(OwnerScopeError, match="duplicate alias"):
        OwnerScopeRegistry((OwnerAlias("alice", alice, 1), OwnerAlias("alice", alice, 2)))
    with pytest.raises(OwnerScopeError, match="cycle"):
        OwnerScopeRegistry(
            (
                OwnerAlias("alice", alice, 1, previous_alias="alice-renamed"),
                OwnerAlias("alice-renamed", alice, 2, previous_alias="alice"),
            )
        )


@pytest.mark.parametrize(
    "alias",
    [" Alice", "alice ", "Alice", "a/lice", "a\\lice", "alice\nnext", "аlice", ""],
)
def test_unsafe_alias_normalization_is_rejected(alias):
    with pytest.raises(OwnerScopeError, match="normalization"):
        OwnerAlias(alias, _scope(), 1)


@pytest.mark.parametrize(
    "value",
    ["user:alice", "owner:alice", "owner:owner_BAD", "owner:owner_0123456789abcdef0123456789abcdef "],
)
def test_scope_and_subject_validation_fail_closed(value):
    with pytest.raises(OwnerScopeError):
        OwnerScope(value)
    with pytest.raises(OwnerScopeError):
        OwnerScope.for_subject_id(value)


def test_subject_id_is_not_a_complete_owner_scope():
    with pytest.raises(OwnerScopeError):
        OwnerScope(ALICE_SUBJECT)
    assert OwnerScope.for_subject_id(ALICE_SUBJECT) == _scope()


def test_stale_alias_and_expected_owner_fence_cannot_cross_boundaries():
    alice = _scope()
    bob = _scope(BOB_SUBJECT)
    registry = OwnerScopeRegistry((OwnerAlias("alice", alice, 1), OwnerAlias("bob", bob, 1)))

    with pytest.raises(OwnerScopeError, match="unknown or stale"):
        registry.migrate_legacy_owner_scope("user:former-alice")
    with pytest.raises(OwnerScopeError, match="outside expected"):
        registry.resolve_alias("alice", expected_owner_scope=bob)
    with pytest.raises(OwnerScopeError, match="exact user"):
        registry.migrate_legacy_owner_scope("user:Alice")


def test_migration_does_not_read_or_rewrite_locator_content():
    registry = OwnerScopeRegistry((OwnerAlias("alice", _scope(), 1),))
    locator = "path:unexamined/private document locator"

    migrated = registry.migrate_legacy_source_ref("user:alice", locator)

    assert migrated.owner_scope == _scope()
    assert migrated.locator is locator
