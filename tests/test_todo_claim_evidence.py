from src.claim_evidence_gate import evaluate_response_claims
from src.todo_receipts import (
    TodoReceipt,
    render_todo_receipt_response,
    todo_receipts_from_tool_events,
    todo_receipts_from_tool_result,
)
from src.tool_transaction_ledger import ToolTransaction, transactions_from_tool_events


LIST_REF = "todo-list:v1:0123456789abcdef:list-alpha"
ITEM_REF = "todo-item:v1:itm_0123456789abcdef"


def _mutation_result(
    action="add",
    *,
    current_state=None,
    status="committed",
    verified=True,
    exit_code=0,
    item_ref=ITEM_REF,
):
    defaults = {
        "add": {"exists": True, "done": False},
        "complete": {"exists": True, "done": True},
        "reopen": {"exists": True, "done": False},
        "remove": {"exists": False, "done": None},
    }
    return {
        "action": action,
        "operation": action,
        "list_ref": LIST_REF,
        "item_ref": item_ref,
        "previous_state": {"exists": False, "done": None},
        "current_state": current_state or defaults[action],
        "open_count": 2,
        "transaction_status": status,
        "verified": verified,
        "evidence_refs": ["notes-readback:v1:abc123def456"],
        "exit_code": exit_code,
        "text": "private task text must not enter a receipt",
    }


def _event(receipt: TodoReceipt, *, exit_code=0):
    return {
        "tool": "manage_todos",
        "command": "<manage_todos>{}</manage_todos>",
        "output": "",
        "exit_code": exit_code,
        "todo_receipts": [receipt.to_dict()],
    }


def test_mutation_receipt_is_semantic_redacted_and_typed():
    receipt = todo_receipts_from_tool_result(_mutation_result())[0]

    assert receipt.claim_type == "todo_item_created"
    assert receipt.verified is True
    assert receipt.receipt_ref.startswith("todo-receipt:v1:")
    assert receipt.to_dict()["raw_content_visible"] is False
    assert "private task text" not in repr(receipt.to_dict())


def test_completed_receipt_requires_done_postcondition():
    receipt = todo_receipts_from_tool_result(
        _mutation_result("complete", current_state={"exists": True, "done": False})
    )[0]

    assert receipt.claim_type == "todo_item_completed"
    assert receipt.verified is False
    assert "nicht verifiziert" in render_todo_receipt_response((receipt,))


def test_generic_tool_success_cannot_support_todo_success_claim():
    generic = ToolTransaction.create(
        surface="agent",
        tool="manage_todos",
        claim_type="tool_execution",
        status="succeeded",
        evidence_refs=["exit_code:0"],
        exit_code=0,
    )

    report = evaluate_response_claims(
        "Todo gespeichert.",
        [],
        tool_transactions=[generic.to_dict()],
    )

    assert report.ok is False
    assert report.unsupported[0].claim_type == "todo_item_created"


def test_matching_verified_receipt_supports_claim_and_deterministic_render():
    receipt = todo_receipts_from_tool_result(_mutation_result("complete"))[0]
    event = _event(receipt)

    report = evaluate_response_claims("Todo erledigt.", [event])
    rendered = render_todo_receipt_response(todo_receipts_from_tool_events([event]))

    assert report.ok is True
    assert report.findings[0].claim_type == "todo_item_completed"
    assert receipt.receipt_ref in report.findings[0].evidence
    assert rendered == f"Todo verifiziert erledigt. Referenz: `{ITEM_REF}`. Offene Todos: 2."


def test_plural_todo_claim_requires_matching_unique_receipt_count():
    first = todo_receipts_from_tool_result(_mutation_result("add"))[0]
    second = todo_receipts_from_tool_result(
        _mutation_result(
            "add",
            item_ref="todo-item:v1:itm_fedcba9876543210",
        )
    )[0]

    under_evidenced = evaluate_response_claims(
        "2 Todos gespeichert.",
        [_event(first)],
    )
    fully_evidenced = evaluate_response_claims(
        "Beide Aufgaben gespeichert.",
        [_event(first), _event(second)],
    )
    duplicate_replay = evaluate_response_claims(
        "2 Todos gespeichert.",
        [_event(first), _event(first)],
    )
    separate_singular_claims = evaluate_response_claims(
        "Ein Todo gespeichert. Ein Todo gespeichert.",
        [_event(first)],
    )
    separate_singular_claims_with_two_receipts = evaluate_response_claims(
        "Ein Todo gespeichert. Ein Todo gespeichert.",
        [_event(first), _event(second)],
    )
    out_of_range = evaluate_response_claims(
        "1000001 Todos gespeichert.",
        [_event(first), _event(second)],
    )
    implicit_plural = evaluate_response_claims("Todos gespeichert.", [_event(first)])
    no_evidence = evaluate_response_claims("Two todos added.", [])
    generic_tasks = evaluate_response_claims("Two scheduler tasks added.", [])
    generic_task = evaluate_response_claims("One scheduler task added.", [])

    assert under_evidenced.ok is False
    assert under_evidenced.unsupported[0].claim_type == "todo_item_created"
    assert "fewer unique" in under_evidenced.unsupported[0].reason
    assert fully_evidenced.ok is True
    assert fully_evidenced.findings[0].claim_type == "todo_item_created"
    assert duplicate_replay.ok is False
    assert "fewer unique" in duplicate_replay.unsupported[0].reason
    assert separate_singular_claims.ok is False
    assert "fewer unique" in separate_singular_claims.unsupported[0].reason
    assert separate_singular_claims_with_two_receipts.ok is True
    assert out_of_range.ok is False
    assert "out-of-range" in out_of_range.unsupported[0].reason
    assert implicit_plural.ok is False
    assert no_evidence.ok is False
    assert generic_tasks.findings == ()
    assert generic_task.findings == ()


def test_todo_claim_count_words_are_exact_and_fail_closed():
    first = todo_receipts_from_tool_result(_mutation_result("add"))[0]
    second = todo_receipts_from_tool_result(
        _mutation_result("add", item_ref="todo-item:v1:itm_fedcba9876543210")
    )[0]

    one = evaluate_response_claims("One todo saved.", [_event(first)])
    under_evidenced = tuple(
        evaluate_response_claims(text, [_event(first)])
        for text in (
            "Two todos saved.",
            "Zwei Todos gespeichert.",
            "Both todos saved.",
            "Beide Todos gespeichert.",
            "0 Todos gespeichert.",
        )
    )
    fully_evidenced = evaluate_response_claims("Two todos saved.", [_event(first), _event(second)])
    todo_item_plural = evaluate_response_claims("Two todo items saved.", [_event(first)])
    todo_task_plural = evaluate_response_claims("Two Todo tasks saved.", [_event(first)])
    huge_numeric_claim = evaluate_response_claims(
        f"{'9' * 128} Todos gespeichert.",
        [_event(first), _event(second)],
    )

    assert one.ok is True
    assert all(report.ok is False for report in under_evidenced)
    assert all(report.unsupported[0].claim_type == "todo_item_created" for report in under_evidenced)
    assert "out-of-range" in under_evidenced[-1].unsupported[0].reason
    assert fully_evidenced.ok is True
    assert todo_item_plural.ok is False
    assert todo_task_plural.ok is False
    assert huge_numeric_claim.ok is False
    assert "out-of-range" in huge_numeric_claim.unsupported[0].reason


def test_mismatched_receipt_does_not_support_a_different_todo_claim():
    created = todo_receipts_from_tool_result(_mutation_result("add"))[0]

    report = evaluate_response_claims("Todo erledigt.", [_event(created)])

    assert report.ok is False
    assert report.unsupported[0].claim_type == "todo_item_completed"


def test_failed_or_ambiguous_receipt_never_becomes_verified_transaction():
    receipt = todo_receipts_from_tool_result(
        _mutation_result("complete", status="ambiguous", verified=False, exit_code=1)
    )[0]
    transactions = transactions_from_tool_events([_event(receipt, exit_code=1)])

    assert receipt.verified is False
    assert transactions[0].claim_type == "todo_item_completed"
    assert transactions[0].verified_done is False
    assert transactions[0].status.value == "blocked"


def test_wrong_tool_or_failed_event_cannot_spoof_a_verified_receipt():
    receipt = todo_receipts_from_tool_result(_mutation_result("add"))[0]
    wrong_tool = _event(receipt)
    wrong_tool["tool"] = "manage_memory"

    wrong_tool_report = evaluate_response_claims("Todo gespeichert.", [wrong_tool])
    failed_event_report = evaluate_response_claims(
        "Todo gespeichert.",
        [_event(receipt, exit_code=1)],
    )

    assert wrong_tool_report.ok is False
    assert failed_event_report.ok is False


def test_verified_receipt_becomes_typed_ledger_transaction_without_raw_text():
    receipt = todo_receipts_from_tool_result(_mutation_result("remove"))[0]
    transactions = transactions_from_tool_events([_event(receipt)])

    assert len(transactions) == 1
    assert transactions[0].claim_type == "todo_item_removed"
    assert transactions[0].verified_done is True
    assert "private task text" not in repr(transactions[0].to_dict())


def test_list_read_receipt_uses_snapshot_version_and_omits_item_text():
    result = {
        "action": "list",
        "lists": [
            {
                "list_ref": LIST_REF,
                "title": "Private list title",
                "items": [{"item_ref": ITEM_REF, "text": "private item", "done": False}],
                "open_count": 1,
                "version": "abcdef1234567890",
            }
        ],
        "open_count": 1,
        "exit_code": 0,
    }

    receipt = todo_receipts_from_tool_result(result)[0]

    assert receipt.claim_type == "todo_list_read"
    assert receipt.verified is True
    assert "private" not in repr(receipt.to_dict()).lower()
    assert render_todo_receipt_response((receipt,)) == "Todo-Liste verifiziert gelesen. Offene Todos: 1."
