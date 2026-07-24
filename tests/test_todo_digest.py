import asyncio
import json
from types import SimpleNamespace

import pytest

from src.builtin_actions import BUILTIN_ACTIONS, _todo_digest_from_notes, _todo_digest_item_postcondition_from_notes
from src.todo_digest_receipts import redact_ref, validate_todo_digest_receipt
from src.todo_digest_formatting import collapse_repeated_open_item_list_prefixes


def _note(**kwargs):
    base = {
        "id": "list-alpha",
        "title": "List",
        "note_type": "checklist",
        "items": json.dumps([{"text": "Open item", "done": False}]),
        "pinned": False,
        "archived": False,
        "label": None,
        "due_date": None,
        "id": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_todo_digest_includes_open_due_and_pinned_items():
    digest = _todo_digest_from_notes([
        _note(title="Pinned", pinned=True),
        _note(title="Done", items=json.dumps([{"text": "Hidden", "done": True}])),
        _note(title="Overdue", due_date="2000-01-01T09:00:00"),
    ])

    assert "Todo digest" in digest
    assert "- Pinned" in digest
    assert "- Pinned: Open item" in digest
    assert "- Overdue" in digest
    assert "Hidden" not in digest


def test_todo_digest_supports_label_filter():
    digest = _todo_digest_from_notes([
        _note(title="Work", label="work"),
        _note(title="Private", label="private"),
    ], label="work")

    assert "Work: Open item" in digest
    assert "Private" not in digest


def test_todo_digest_groups_repeated_items_from_one_list():
    digest = _todo_digest_from_notes([
        _note(
            title="Zentrale To-Do-Liste",
            items=json.dumps([
                {"text": "Termin mit Herr Assel und Macro koordinieren per E-Mail", "done": False},
                {"text": "ASV Noten ueberpruefen", "done": False},
            ]),
        ),
    ])

    assert (
        "Open items:\n"
        "Zentrale To-Do-Liste:\n"
        "- Termin mit Herr Assel und Macro koordinieren per E-Mail\n"
        "- ASV Noten ueberpruefen"
    ) in digest
    assert "- Zentrale To-Do-Liste:" not in digest


def test_todo_digest_formatting_collapses_existing_multiline_body():
    body = (
        "Todo digest\n\n"
        "Open items:\n"
        "- Zentrale To-Do-Liste: Termin mit Herr Assel und Macro koordinieren per E-Mail\n"
        "- Zentrale To-Do-Liste: ASV Noten ueberpruefen"
    )

    assert collapse_repeated_open_item_list_prefixes(body) == (
        "Todo digest\n\n"
        "Open items:\n"
        "Zentrale To-Do-Liste:\n"
        "- Termin mit Herr Assel und Macro koordinieren per E-Mail\n"
        "- ASV Noten ueberpruefen"
    )


def test_todo_digest_action_is_registered():
    assert "todo_digest" in BUILTIN_ACTIONS
    result, ok = asyncio.run(BUILTIN_ACTIONS["todo_digest"]("", limit=1))
    assert isinstance(result, str)
    assert isinstance(ok, bool)


def _postcondition(notes, *, action="add", list_ref="target-list", item_ref="target-item", current=None):
    refs = (
        redact_ref("owner", "alice"), redact_ref("list", list_ref), redact_ref("item", item_ref), f"operation:{action}",
    )
    state = current if current is not None else {"exists": action != "remove", "done": {"add": False, "reopen": False, "complete": True, "remove": None}[action]}
    return _todo_digest_item_postcondition_from_notes(
        notes, list_ref=list_ref, item_ref=item_ref, action=action, evidence_refs=refs, current_state=state,
    )


def test_postcondition_preserves_pinned_and_legacy_open_entry_positions_without_content():
    pinned = _note(id="private-pinned-list", title="private pinned title", note_type="note", pinned=True)
    target = _note(id="private-target-list", title="private target", items=json.dumps([{"id": "private-target-item", "text": "private target text", "done": False}]))
    receipt = _postcondition([pinned, target], list_ref="private-target-list", item_ref="private-target-item")

    assert validate_todo_digest_receipt(receipt)
    assert receipt["selection_position"] == 1
    assert receipt["selected_open_item_count"] == 2
    assert set(receipt) == {
        "schema", "claim_type", "action", "transaction_status", "verified", "evidence_refs",
        "current_state", "included", "selection_position", "open_item_count", "selected_open_item_count",
        "limit", "label_filter_active", "list_filter_active", "builder_date", "builder_clock",
        "snapshot_hash", "raw_content_visible", "receipt_ref",
    }
    for raw in ("alice", "private-pinned-list", "private-target-list", "private-target-item", "private pinned title", "private target", "private target text"):
        assert raw not in repr(receipt)

    legacy = _note(id="private-legacy-list", items=json.dumps([{"text": "private legacy", "done": False}]))
    receipt = _postcondition([legacy, target], list_ref="private-target-list", item_ref="private-target-item")
    assert receipt["selection_position"] == 1
    assert receipt["selected_open_item_count"] == 2


def test_postcondition_fails_closed_for_limit_target_duplicates_and_wrong_list():
    before = [_note(id=f"list-{index}", items=json.dumps([{"id": f"item-{index}", "text": "open", "done": False}])) for index in range(20)]
    target = _note(id="target-list", items=json.dumps([{"id": "target-item", "text": "target", "done": False}]))
    assert _postcondition([*before, target]) is None

    duplicate = _note(id="target-list", items=json.dumps([
        {"id": "target-item", "text": "one", "done": False}, {"id": "target-item", "text": "two", "done": False},
    ]))
    assert _postcondition([duplicate]) is None
    assert _postcondition([target], list_ref="wrong-list") is None


def test_postcondition_rejects_archived_or_non_checklist_target_lists():
    archived = _note(id="target-list", archived=True)
    non_checklist = _note(id="target-list", note_type="note", pinned=True)

    assert _postcondition([archived], action="remove") is None
    assert _postcondition([non_checklist], action="remove") is None


def test_postcondition_complete_and_remove_exclusions_are_target_specific():
    completed = _note(id="target-list", items=json.dumps([{"id": "target-item", "text": "done", "done": True}]))
    complete = _postcondition([completed], action="complete")
    removed = _postcondition([_note(id="target-list", items="[]")], action="remove")

    assert complete["claim_type"] == removed["claim_type"] == "todo_digest_excludes"
    assert complete["receipt_ref"] != removed["receipt_ref"]


@pytest.mark.parametrize("limit", [0, -1, 99])
def test_todo_digest_legacy_limit_slice_semantics_are_unchanged(limit):
    notes = [_note(title="One"), _note(title="Two")]
    digest = _todo_digest_from_notes(notes, limit=limit)

    expected = ["One: Open item", "Two: Open item"][:limit]
    assert all(value in digest for value in expected)
    assert all(value not in digest for value in {"One: Open item", "Two: Open item"} - set(expected))
