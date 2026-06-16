from src.release_followup_matrix import build_release_followup_matrix
from src.release_slice_router import ReleaseFollowupSlice


def test_matrix_groups_slices_by_owner_and_parallelism():
    slices = (
        _slice("REL-test-vault-rebuild-evidence", "Alice", True),
        _slice("REL-provider-proof-evidence", "Bob", False),
        _slice("REL-partial-manual-evidence-closeout", "Charlie", False),
    )

    matrix = build_release_followup_matrix(slices)

    assert [queue.owner for queue in matrix.queues] == ["Alice", "Bob", "Charlie"]
    assert matrix.parallel_batch_ids == ("REL-test-vault-rebuild-evidence",)
    assert matrix.sequential_gate_ids == (
        "REL-provider-proof-evidence",
        "REL-partial-manual-evidence-closeout",
    )


def test_matrix_preserves_owner_slice_order():
    slices = (
        _slice("bob-first", "Bob", False),
        _slice("alice-docs", "Alice", True),
        _slice("bob-second", "Bob", True),
    )

    matrix = build_release_followup_matrix(slices)
    bob_queue = next(queue for queue in matrix.queues if queue.owner == "Bob")

    assert bob_queue.slice_ids == ("bob-first", "bob-second")
    assert bob_queue.parallel_safe_slice_ids == ("bob-second",)
    assert bob_queue.sequential_slice_ids == ("bob-first",)


def test_matrix_places_unknown_owners_after_known_owners():
    slices = (
        _slice("delta-slice", "Delta", True),
        _slice("alice-slice", "Alice", True),
        _slice("eve-slice", "Eve", False),
    )

    matrix = build_release_followup_matrix(slices)

    assert [queue.owner for queue in matrix.queues] == ["Alice", "Delta", "Eve"]


def test_empty_matrix_is_valid():
    matrix = build_release_followup_matrix(())

    assert matrix.queues == ()
    assert matrix.parallel_batch_ids == ()
    assert matrix.sequential_gate_ids == ()


def test_matrix_to_dict_is_stable():
    matrix = build_release_followup_matrix(
        (
            _slice("alice", "Alice", True),
            _slice("bob", "Bob", False),
        )
    )

    assert matrix.to_dict() == {
        "queues": (
            {
                "owner": "Alice",
                "slice_ids": ("alice",),
                "parallel_safe_slice_ids": ("alice",),
                "sequential_slice_ids": (),
            },
            {
                "owner": "Bob",
                "slice_ids": ("bob",),
                "parallel_safe_slice_ids": (),
                "sequential_slice_ids": ("bob",),
            },
        ),
        "parallel_batch_ids": ("alice",),
        "sequential_gate_ids": ("bob",),
    }


def _slice(slice_id: str, owner: str, parallel_safe: bool) -> ReleaseFollowupSlice:
    return ReleaseFollowupSlice(
        slice_id=slice_id,
        owner=owner,
        title=slice_id,
        scope=("docs/plans/1.0-evidence-release-checklist.md",),
        exit_criteria="done",
        parallel_safe=parallel_safe,
    )
