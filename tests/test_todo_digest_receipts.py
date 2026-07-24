from copy import deepcopy
from collections.abc import Mapping

from src.todo_digest_receipts import (
    TODO_DIGEST_RECEIPT_FIELD,
    build_todo_digest_membership_receipt,
    digest_receipts_from_tool_events,
    validate_todo_digest_receipt,
    validated_todo_digest_receipt_from_event,
)


REFS = (
    "owner:0123456789abcdef",
    "list:fedcba9876543210",
    "item:0011223344556677",
    "operation:add",
)


def _receipt(**changes):
    values = {
        "action": "add",
        "evidence_refs": REFS,
        "current_state": {"exists": True, "done": False},
        "included": True,
        "selection_position": 0,
        "open_item_count": 2,
        "selected_open_item_count": 1,
        "limit": 20,
        "label_filter_active": False,
        "list_filter_active": False,
        "builder_date": "2026-07-24",
        "snapshot_manifest": {
            "schema": "odysseus.todo_digest_snapshot.v1",
            "builder_date": "2026-07-24",
            "builder_clock": "naive_local",
            "limit": 20,
            "label_filter_active": False,
            "list_filter_active": False,
            "selected": [{"list_ref": REFS[1], "item_ref": REFS[2], "position": 0, "done": False}],
        },
    }
    values.update(changes)
    if not values["included"] and "snapshot_manifest" not in changes:
        values["snapshot_manifest"] = {**values["snapshot_manifest"], "selected": []}
        values["selected_open_item_count"] = 0
    return build_todo_digest_membership_receipt(**values)


def test_contains_receipt_is_deterministic_content_free_and_strict():
    first = _receipt()
    second = _receipt()

    assert first == second
    assert validate_todo_digest_receipt(first) == first
    assert first["claim_type"] == "todo_digest_contains"
    assert first["builder_clock"] == "naive_local"
    assert first["raw_content_visible"] is False
    assert first["receipt_ref"].startswith("sha256:")
    assert "private" not in repr(first)


def test_complete_and_remove_only_issue_exclusion_shapes():
    complete = _receipt(
        action="complete", evidence_refs=(*REFS[:3], "operation:complete"),
        current_state={"exists": True, "done": True}, included=False, selection_position=None,
    )
    removed = _receipt(
        action="remove", evidence_refs=(*REFS[:3], "operation:remove"),
        current_state={"exists": False, "done": None}, included=False, selection_position=None,
    )

    assert complete["claim_type"] == removed["claim_type"] == "todo_digest_excludes"
    assert _receipt(action="reopen", evidence_refs=(*REFS[:3], "operation:reopen"), included=False, selection_position=None) is None


def test_rejects_duplicate_refs_wrong_state_and_manifest_with_raw_content():
    assert _receipt(evidence_refs=(REFS[0], REFS[1], REFS[1], REFS[3])) is None
    assert _receipt(current_state={"exists": True, "done": True}) is None
    assert _receipt(snapshot_manifest={"private_text": "secret"}) is None

    malformed = deepcopy(_receipt())
    malformed[TODO_DIGEST_RECEIPT_FIELD] = "unexpected"
    assert validate_todo_digest_receipt(malformed) is None


def test_receipt_binds_exact_semantic_state_and_rejects_tampering_filters_and_dates():
    receipt = _receipt()
    semantic = {"action": "add", "operation": "add", "verified": True, "current_state": True, "evidence_refs": REFS}
    assert validate_todo_digest_receipt(receipt, semantic_receipt=semantic) is None
    for field, value in (("snapshot_hash", "sha256:" + "0" * 64), ("open_item_count", 3), ("receipt_ref", "sha256:" + "1" * 64), ("raw_content_visible", True)):
        tampered = deepcopy(receipt)
        tampered[field] = value
        assert validate_todo_digest_receipt(tampered) is None
    assert _receipt(label_filter_active=True) is None
    assert _receipt(builder_date="2026-02-30") is None


def test_manifest_metadata_and_ref_roles_must_match_outer_receipt_inputs():
    for field, value in (("builder_date", "2026-07-23"), ("limit", 19), ("label_filter_active", True), ("list_filter_active", True)):
        values = {
            "action": "add", "evidence_refs": REFS, "current_state": {"exists": True, "done": False},
            "included": True, "selection_position": 0, "open_item_count": 1, "selected_open_item_count": 1,
            "limit": 20, "label_filter_active": False, "list_filter_active": False, "builder_date": "2026-07-24",
            "snapshot_manifest": {
                "schema": "odysseus.todo_digest_snapshot.v1", "builder_date": "2026-07-24", "builder_clock": "naive_local",
                "limit": 20, "label_filter_active": False, "list_filter_active": False,
                "selected": [{"list_ref": REFS[1], "item_ref": REFS[2], "position": 0, "done": False}],
            },
        }
        values["snapshot_manifest"][field] = value
        assert build_todo_digest_membership_receipt(**values) is None
    values["snapshot_manifest"] = {**values["snapshot_manifest"], "builder_date": "2026-07-24", "limit": 20, "label_filter_active": False, "list_filter_active": False, "selected": [{"list_ref": REFS[2], "item_ref": REFS[1], "position": 0, "done": False}]}
    assert build_todo_digest_membership_receipt(**values) is None


def test_digest_event_scan_is_bounded_and_rejects_non_sequences(monkeypatch):
    import src.todo_digest_receipts as receipts
    calls = []
    monkeypatch.setattr(receipts, "validated_todo_digest_receipt_from_event", lambda event: calls.append(event) or None)

    assert digest_receipts_from_tool_events([{}] * 65) == ()
    assert len(calls) == 64
    assert digest_receipts_from_tool_events({}) == ()


def test_public_receipt_and_event_validation_fail_closed_for_hostile_inputs():
    class HostileMapping(Mapping):
        def __getitem__(self, _key): raise RuntimeError("hostile")
        def __iter__(self): raise RuntimeError("hostile")
        def __len__(self): return 1
        def get(self, _key, _default=None): raise RuntimeError("hostile")
    class HostileList(list):
        def __getitem__(self, _key): raise RuntimeError("hostile")

    assert validate_todo_digest_receipt(HostileMapping()) is None
    assert validated_todo_digest_receipt_from_event(HostileMapping()) is None
    assert digest_receipts_from_tool_events(HostileList([HostileMapping()])) == ()
