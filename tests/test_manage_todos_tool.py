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


def test_facade_attaches_a_semantic_receipt_only_after_a_valid_success(monkeypatch):
    from src import calendar_capability_service
    class SemanticService:
        def complete(self, **_kwargs):
            return SimpleNamespace(
                to_dict=lambda: {
                    "list_ref": "private-list-id",
                    "item_ref": "private-item-id",
                    "operation": "complete",
                    "previous_state": False,
                    "current_state": True,
                    "open_count": 0,
                    "transaction_status": "committed",
                    "verified": True,
                    "evidence_refs_redacted": (
                        "owner:0123456789abcdef",
                        "list:fedcba9876543210",
                        "item:0011223344556677",
                        "operation:complete",
                    ),
                }
            )

    monkeypatch.setattr(todos, "_service_factory", SemanticService)
    monkeypatch.setattr(calendar_capability_service, "build_todo_digest_schedule_postcondition", lambda **_kwargs: None)

    result = _run({"action": "complete", "list_ref": "x", "item_ref": "y"})

    receipt = result["todo_semantic_receipt"]
    assert receipt["claim_type"] == "todo_item_completed"
    assert "private" not in repr(receipt)


def test_facade_attaches_only_a_valid_fresh_digest_postcondition(monkeypatch):
    from src import builtin_actions
    from src import calendar_capability_service
    from src.todo_digest_receipts import build_todo_digest_membership_receipt

    class SemanticService:
        def add(self, **_kwargs):
            return SimpleNamespace(
                to_dict=lambda: {
                    "list_ref": "list-1", "item_ref": "item-9", "operation": "add",
                    "previous_state": None, "current_state": False, "open_count": 1,
                    "transaction_status": "committed", "verified": True,
                    "evidence_refs_redacted": (
                        "owner:0123456789abcdef", "list:fedcba9876543210",
                        "item:0011223344556677", "operation:add",
                    ),
                }
            )

    calls = []
    def fresh(**kwargs):
        calls.append(kwargs)
        refs = kwargs["evidence_refs"]
        return build_todo_digest_membership_receipt(
            action="add", evidence_refs=refs, current_state={"exists": True, "done": False},
            included=True, selection_position=0, open_item_count=1, selected_open_item_count=1,
            limit=20, label_filter_active=False, list_filter_active=False, builder_date="2026-07-24",
            snapshot_manifest={
                "schema": "odysseus.todo_digest_snapshot.v1", "builder_date": "2026-07-24",
                "builder_clock": "naive_local", "limit": 20, "label_filter_active": False,
                "list_filter_active": False,
                "selected": [{"list_ref": refs[1], "item_ref": refs[2], "position": 0, "done": False}],
            },
        )

    monkeypatch.setattr(todos, "_service_factory", SemanticService)
    monkeypatch.setattr(builtin_actions, "build_todo_digest_item_postcondition", fresh)
    monkeypatch.setattr(calendar_capability_service, "build_todo_digest_schedule_postcondition", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("private schedule failure")))

    result = _run({"action": "add", "list_ref": "list-1", "text": "private", "idempotency_key": "key-1"})

    assert len(calls) == 1
    assert result["todo_digest_receipt"]["claim_type"] == "todo_digest_contains"
    assert result["todo_semantic_receipt"]["claim_type"] == "todo_item_created"
    assert "todo_digest_schedule_receipt" not in result
    assert "private" not in repr(result["todo_digest_receipt"])


def test_facade_attaches_a_valid_owner_bound_schedule_receipt_without_db_read(monkeypatch):
    from datetime import datetime
    from hashlib import sha256
    from src import builtin_actions
    from src import calendar_capability_service
    from src.todo_digest_schedule_receipts import build_todo_digest_schedule_receipt

    class SemanticService:
        def add(self, **_kwargs):
            return SimpleNamespace(to_dict=lambda: {
                "list_ref": "list-1", "item_ref": "item-9", "operation": "add", "previous_state": None,
                "current_state": False, "open_count": 1, "transaction_status": "committed", "verified": True,
                "evidence_refs_redacted": (f"owner:{sha256(b'alice').hexdigest()[:16]}", "list:fedcba9876543210", "item:0011223344556677", "operation:add"),
            })

    def schedule(**_kwargs):
        return build_todo_digest_schedule_receipt(owner="alice", candidates=[{
            "id": "private-task", "owner": "alice", "task_type": "action", "action": "todo_digest",
            "trigger_type": "schedule", "schedule": "cron", "status": "active",
            "cron_expression": "0 9 * * 1-5", "scheduled_time": "09:00", "next_run": datetime(2026, 7, 25),
        }], now_utc=datetime(2026, 7, 24))

    monkeypatch.setattr(todos, "_service_factory", SemanticService)
    monkeypatch.setattr(builtin_actions, "build_todo_digest_item_postcondition", lambda **_kwargs: None)
    monkeypatch.setattr(calendar_capability_service, "build_todo_digest_schedule_postcondition", schedule)
    result = _run({"action": "add", "list_ref": "list-1", "text": "private", "idempotency_key": "key-1"})

    assert result["todo_semantic_receipt"]["claim_type"] == "todo_item_created"
    assert result["todo_digest_schedule_receipt"]["claim_type"] == "todo_digest_schedule_active"
    assert "private-task" not in repr(result["todo_digest_schedule_receipt"])


def test_facade_attaches_read_verified_list_receipt_with_facade_redacted_refs(monkeypatch):
    service = RecordingService()
    monkeypatch.setattr(todos, "_service_factory", lambda: service)

    result = _run({"action": "list", "list_ref": "private-list-id"}, owner="alice")

    receipt = result["todo_semantic_receipt"]
    assert receipt["claim_type"] == "todo_list_read"
    assert receipt["transaction_status"] == "read_verified"
    assert receipt["evidence_refs"] == (
        "owner:2bd806c97f0e00af",
        "list:bb400c2a12242213",
        "operation:list",
    )
    assert "private-list-id" not in repr(receipt)


def test_facade_does_not_attach_a_receipt_to_an_invalid_success(monkeypatch):
    class InvalidReceiptService:
        def complete(self, **_kwargs):
            return SimpleNamespace(
                to_dict=lambda: {
                    "operation": "complete",
                    "current_state": True,
                    "open_count": 0,
                    "transaction_status": "failed",
                    "verified": True,
                    "evidence_refs_redacted": ("operation:complete",),
                }
            )

    monkeypatch.setattr(todos, "_service_factory", InvalidReceiptService)

    result = _run({"action": "complete", "list_ref": "x", "item_ref": "y"})

    assert "todo_semantic_receipt" not in result
