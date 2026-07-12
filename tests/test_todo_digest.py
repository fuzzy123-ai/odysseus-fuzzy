import asyncio
import json
from types import SimpleNamespace

from src.builtin_actions import BUILTIN_ACTIONS, _todo_digest_from_notes
from src.todo_digest_formatting import collapse_repeated_open_item_list_prefixes


def _note(**kwargs):
    base = {
        "title": "List",
        "note_type": "checklist",
        "items": json.dumps([{"text": "Open item", "done": False}]),
        "pinned": False,
        "archived": False,
        "label": None,
        "due_date": None,
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
