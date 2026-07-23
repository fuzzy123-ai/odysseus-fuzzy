import asyncio
import json
from types import SimpleNamespace

import pytest

from src.tool_domains import todos
from src.todo_domain_service import (
    TodoAmbiguousMatchError,
    TodoConflictError,
    TodoDataError,
    TodoIdempotencyConflictError,
    TodoNotFoundError,
    TodoValidationError,
)


class RecordingService:
    def __init__(self):
        self.calls = []

    def _receipt(self, action, **kwargs):
        self.calls.append((action, kwargs))
        return SimpleNamespace(
            to_dict=lambda: {
                "list_ref": kwargs["list_ref"],
                "item_ref": kwargs.get("item_ref", "item-1"),
                "open_count": 1,
            }
        )

    def list(self, **kwargs):
        self.calls.append(("list", kwargs))
        return SimpleNamespace(
            to_dict=lambda: {
                "list_ref": kwargs["list_ref"],
                "items": [{"item_ref": "item-1", "completed": False}],
                "open_count": 1,
            }
        )

    def add(self, **kwargs):
        return self._receipt("add", **kwargs)

    def complete(self, **kwargs):
        return self._receipt("complete", **kwargs)

    def reopen(self, **kwargs):
        return self._receipt("reopen", **kwargs)

    def remove(self, **kwargs):
        return self._receipt("remove", **kwargs)


def _run(payload, owner="alice"):
    return asyncio.run(todos.do_manage_todos(json.dumps(payload), owner=owner))


def test_add_forwards_exact_owner_refs_and_content_free_receipt(monkeypatch):
    service = RecordingService()
    monkeypatch.setattr(todos, "_service_factory", lambda: service)

    result = _run(
        {
            "action": "add",
            "list_ref": "full-list-id",
            "text": "private todo text",
            "idempotency_key": "add-1",
        }
    )

    assert result == {
        "status": "ok",
        "action": "add",
        "exit_code": 0,
        "list_ref": "full-list-id",
        "item_ref": "item-1",
        "open_count": 1,
    }
    assert service.calls == [
        (
            "add",
            {
                "owner": "alice",
                "list_ref": "full-list-id",
                "text": "private todo text",
                "idempotency_key": "add-1",
            },
        )
    ]
    assert "private todo text" not in repr(result)


@pytest.mark.parametrize(
    ("requested_action", "canonical_action"),
    [
        ("create", "add"),
        ("new", "add"),
        ("done", "complete"),
        ("finish", "complete"),
        ("undo", "reopen"),
        ("uncomplete", "reopen"),
    ],
)
def test_non_destructive_aliases_dispatch_to_their_canonical_service_method(
    monkeypatch, requested_action, canonical_action
):
    service = RecordingService()
    monkeypatch.setattr(todos, "_service_factory", lambda: service)
    payload = {"action": requested_action, "list_ref": "list-1"}
    if canonical_action == "add":
        payload.update(text="item", idempotency_key="key-1")
    else:
        payload["item_ref"] = "item-9"

    result = _run(payload)

    assert result["action"] == canonical_action
    assert service.calls[0][0] == canonical_action


@pytest.mark.parametrize("action", ["list", "complete", "reopen"])
def test_non_add_actions_forward_exact_selectors(monkeypatch, action):
    service = RecordingService()
    monkeypatch.setattr(todos, "_service_factory", lambda: service)
    payload = {"action": action, "list_ref": "list-1"}
    if action != "list":
        payload.update(item_ref="item-9", text="selector text")

    _run(payload, owner="owner-2")

    _, call = service.calls[0]
    assert call["owner"] == "owner-2"
    assert call["list_ref"] == "list-1"
    if action != "list":
        assert call["item_ref"] == "item-9"
        assert call["text"] == "selector text"


@pytest.mark.parametrize("confirmation", [None, False, "true", 1])
def test_remove_requires_a_literal_boolean_confirmation_before_service_factory(
    monkeypatch, confirmation
):
    monkeypatch.setattr(
        todos,
        "_service_factory",
        lambda: (_ for _ in ()).throw(AssertionError("factory must not run")),
    )
    payload = {"action": "delete", "list_ref": "list-1", "item_ref": "item-9"}
    if confirmation is not None:
        payload["confirmed"] = confirmation

    result = _run(payload)

    assert result == {
        "status": "confirmation_required",
        "requires_confirmation": True,
        "action": "remove",
        "exit_code": 0,
    }


@pytest.mark.parametrize("confirmation_key", ["confirmed", "confirm"])
def test_confirmed_remove_is_dispatched(monkeypatch, confirmation_key):
    service = RecordingService()
    monkeypatch.setattr(todos, "_service_factory", lambda: service)

    result = _run(
        {
            "action": "delete",
            "list_ref": "list-1",
            "item_ref": "item-9",
            confirmation_key: True,
        }
    )

    assert result["action"] == "remove"
    assert service.calls[0][0] == "remove"


@pytest.mark.parametrize("content", ["not json", "[]", '{"action": "unknown"}'])
def test_invalid_content_is_rejected_without_constructing_the_service(monkeypatch, content):
    monkeypatch.setattr(
        todos,
        "_service_factory",
        lambda: (_ for _ in ()).throw(AssertionError("factory must not run")),
    )

    result = asyncio.run(todos.do_manage_todos(content, owner="alice"))

    expected_code = "invalid_action" if "unknown" in content else "invalid_arguments"
    assert result == {
        "status": "rejected",
        "error": expected_code,
        "error_code": expected_code,
        "exit_code": 1,
    }


@pytest.mark.parametrize(
    ("error", "error_code"),
    [
        (TodoValidationError(), "invalid_arguments"),
        (TodoNotFoundError(), "not_found"),
        (TodoIdempotencyConflictError(), "idempotency_conflict"),
        (TodoConflictError(), "conflict"),
        (TodoDataError(), "invalid_data"),
        (RuntimeError("private service details"), "todo_error"),
    ],
)
def test_service_errors_are_mapped_to_public_content_free_codes(monkeypatch, error, error_code):
    class FailingService:
        def list(self, **_kwargs):
            raise error

    monkeypatch.setattr(todos, "_service_factory", FailingService)

    result = _run({"action": "list", "list_ref": "list-1"})

    assert result == {
        "status": "rejected",
        "error": error_code,
        "error_code": error_code,
        "exit_code": 1,
    }
    assert "private service details" not in repr(result)


def test_ambiguous_item_error_exposes_only_candidate_refs(monkeypatch):
    class FailingService:
        def complete(self, **_kwargs):
            raise TodoAmbiguousMatchError(["item-1", "item-2"])

    monkeypatch.setattr(todos, "_service_factory", FailingService)

    result = _run(
        {"action": "complete", "list_ref": "list-1", "text": "private selector"}
    )

    assert result == {
        "status": "rejected",
        "error": "ambiguous_item",
        "error_code": "ambiguous_item",
        "exit_code": 1,
        "candidate_refs": ["item-1", "item-2"],
    }
    assert "private selector" not in repr(result)
