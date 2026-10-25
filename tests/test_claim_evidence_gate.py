from pathlib import Path

import pytest

from src.claim_evidence_gate import build_claim_evidence_correction, evaluate_response_claims
from src.todo_digest_receipts import (
    TODO_DIGEST_RECEIPT_FIELD,
    build_todo_digest_membership_receipt,
    todo_digest_receipts_from_postconditions,
)
from src.todo_digest_schedule_receipts import TODO_DIGEST_SCHEDULE_RECEIPT_FIELD, build_todo_digest_schedule_receipt
from src.tool_transaction_ledger import ToolTransaction


def _todo_event(action: str) -> dict:
    current_state = {"add": False, "complete": True, "reopen": False, "remove": None}[action]
    receipt = {
        "schema": "odysseus.todo_semantic_receipt.v1",
        "action": action,
        "operation": action,
        "claim_type": {
            "add": "todo_item_created",
            "complete": "todo_item_completed",
            "reopen": "todo_item_reopened",
            "remove": "todo_item_removed",
        }[action],
        "verified": True,
        "transaction_status": "committed",
        "open_count": 1,
        "previous_state": None if action == "add" else False,
        "current_state": current_state,
        "evidence_refs": (
            "owner:2bd806c97f0e00af",
            "list:fedcba9876543210",
            "item:0011223344556677",
            f"operation:{action}",
        ),
    }
    return {"tool": "manage_todos", "action": action, "todo_semantic_receipt": receipt}


def _todo_list_event() -> dict:
    return {
        "tool": "manage_todos",
        "action": "list",
        "todo_semantic_receipt": {
            "schema": "odysseus.todo_semantic_receipt.v1",
            "action": "list",
            "operation": "list",
            "claim_type": "todo_list_read",
            "verified": True,
            "transaction_status": "read_verified",
            "open_count": 1,
            "previous_state": None,
            "current_state": None,
            "evidence_refs": (
                "owner:0123456789abcdef",
                "list:fedcba9876543210",
                "operation:list",
            ),
        },
    }


def _todo_digest_event(action: str) -> dict:
    event = _todo_event(action)
    receipt = event["todo_semantic_receipt"]
    included = action in {"add", "reopen"}
    digest = build_todo_digest_membership_receipt(
        action=action, evidence_refs=receipt["evidence_refs"],
        current_state={"exists": action != "remove", "done": {"add": False, "reopen": False, "complete": True, "remove": None}[action]},
        included=included, selection_position=0 if included else None,
        open_item_count=1, selected_open_item_count=1 if included else 0, limit=20,
        label_filter_active=False, list_filter_active=False, builder_date="2026-07-24",
        snapshot_manifest={
            "schema": "odysseus.todo_digest_snapshot.v1", "builder_date": "2026-07-24",
            "builder_clock": "naive_local", "limit": 20, "label_filter_active": False,
            "list_filter_active": False,
            "selected": ([{"list_ref": receipt["evidence_refs"][1], "item_ref": receipt["evidence_refs"][2], "position": 0, "done": False}] if included else []),
        },
    )
    event[TODO_DIGEST_RECEIPT_FIELD] = digest
    return event


def _todo_next_digest_event(*, include_membership: bool = True, include_schedule: bool = True) -> dict:
    event = _todo_digest_event("add")
    if not include_membership:
        event.pop(TODO_DIGEST_RECEIPT_FIELD)
    if include_schedule:
        from datetime import datetime
        event[TODO_DIGEST_SCHEDULE_RECEIPT_FIELD] = build_todo_digest_schedule_receipt(
            owner="alice", candidates=[{
                "id": "private-task", "owner": "alice", "task_type": "action", "action": "todo_digest",
                "trigger_type": "schedule", "schedule": "cron", "status": "active",
                "cron_expression": "0 9 * * 1,2,3,4,5", "scheduled_time": "09:00", "next_run": datetime(2026, 7, 25),
            }], now_utc=datetime(2026, 7, 24),
        )
    return event


def _legacy_todo_digest_event() -> dict:
    """Build a redacted Telegram-v1 compatibility event for parser parity tests."""
    receipts = todo_digest_receipts_from_postconditions(
        {
            "claim_type": "todo_digest_contains",
            "list_ref": "todo-list:v1:0123456789abcdef:list-alpha",
            "item_ref": "todo-item:v1:itm_0123456789abcdef",
            "included": True,
            "current_state": {"exists": True, "done": False},
            "projection_ref": "todo-digest-projection:v1:" + "a" * 32,
            "transaction_status": "projected",
            "verified": True,
            "evidence_refs": ["notes-digest-readback:v1:" + "a" * 32],
        },
        {
            "claim_type": "todo_digest_schedule_active",
            "status": "active",
            "schedule_ref": "todo-digest-schedule:v1:" + "b" * 12,
            "next_run": "2026-07-25T09:00:00",
            "verified": True,
            "evidence_refs": ["scheduled-task-readback:v1:" + "b" * 12],
        },
    )
    return {
        "tool": "manage_todos",
        "exit_code": 0,
        "todo_digest_receipts": [receipt.to_dict() for receipt in receipts],
    }


@pytest.mark.parametrize(
    ("text", "action", "claim"),
    [
        ("The todo item appears in the digest.", "add", "todo_digest_contains"),
        ("Die Aufgabe erscheint im Digest.", "reopen", "todo_digest_contains"),
        ("The todo item is excluded from the digest.", "complete", "todo_digest_excludes"),
        ("Die Aufgabe ist nicht im Digest enthalten.", "remove", "todo_digest_excludes"),
    ],
)
def test_digest_membership_claims_require_matching_closed_postconditions(tmp_path: Path, text: str, action: str, claim: str):
    report = evaluate_response_claims(text, [_todo_digest_event(action)], repo_root=tmp_path)

    assert [(item.claim_type, item.status) for item in report.findings] == [(claim, "supported")]


@pytest.mark.parametrize(
    "text",
    [
        "I will make the todo item appear in the digest tomorrow.",
        "I'll ensure the todo item appears in the digest tomorrow.",
        "I won't make the todo item appear in the digest tomorrow.",
        "I won\u2019t make the todo item appear in the digest tomorrow.",
        "We\u2019ll ensure the todo item appears in the digest tomorrow.",
        "Wir werden daf\u00fcr sorgen, dass die Aufgabe morgen im Digest erscheint.",
        "Ich werde nicht daf\u00fcr sorgen, dass die Aufgabe morgen im Digest erscheint.",
        "Ich werde dafür sorgen, dass die Aufgabe morgen im Digest erscheint.",
        "\"The todo item appears in the digest.\"",
        "Can you make the todo item appear in the digest?",
    ],
)
def test_digest_membership_ignores_future_quoted_and_requested_language(tmp_path: Path, text: str):
    assert evaluate_response_claims(text, [_todo_digest_event("add")], repo_root=tmp_path).findings == ()


def test_digest_membership_never_proves_schedule_or_delivery(tmp_path: Path):
    report = evaluate_response_claims(
        "Die Aufgabe erscheint morgen im Digest.", [_todo_digest_event("add")], repo_root=tmp_path
    )

    assert {item.claim_type for item in report.unsupported} == {"todo_digest_schedule_active"}


def test_digest_claim_language_handles_next_no_longer_and_negation_boundaries(tmp_path: Path):
    next_report = evaluate_response_claims(
        "The todo item appears in the next digest.", [_todo_digest_event("add")], repo_root=tmp_path
    )
    exclusion = evaluate_response_claims(
        "The todo item no longer appears in the digest.", [_todo_digest_event("complete")], repo_root=tmp_path
    )

    assert [(item.claim_type, item.status) for item in next_report.findings] == [
        ("todo_digest_contains", "supported"), ("todo_digest_schedule_active", "unsupported"),
    ]
    assert [(item.claim_type, item.status) for item in exclusion.findings] == [("todo_digest_excludes", "supported")]
    for text in ("The item is not excluded from the digest.", "The item never appears in the digest.", "Die Aufgabe ist nicht ausgeschlossen im Digest."):
        assert evaluate_response_claims(text, [_todo_digest_event("complete")], repo_root=tmp_path).findings == ()


def test_timed_future_digest_assertion_is_schedule_unsupported_but_requests_are_ignored(tmp_path: Path):
    concrete = evaluate_response_claims(
        "The todo item will appear in the digest tomorrow.", [_todo_digest_event("add")], repo_root=tmp_path
    )
    request = evaluate_response_claims(
        "Can you make the todo item appear in the digest tomorrow?", [_todo_digest_event("add")], repo_root=tmp_path
    )
    plain_future = evaluate_response_claims(
        "The todo item will appear in the digest.", [_todo_digest_event("add")], repo_root=tmp_path
    )

    assert [(item.claim_type, item.status) for item in concrete.findings] == [("todo_digest_schedule_active", "unsupported")]
    assert request.findings == plain_future.findings == ()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("I will make the todo item appear in the digest tomorrow.", []),
        ("I'll ensure the todo item appears in the digest tomorrow.", []),
        ("I won't make the todo item appear in the digest tomorrow.", []),
        ("I won\u2019t make the todo item appear in the digest tomorrow.", []),
        ("We\u2019ll ensure the todo item appears in the digest tomorrow.", []),
        ("Wir werden daf\u00fcr sorgen, dass die Aufgabe morgen im Digest erscheint.", []),
        ("Ich werde nicht daf\u00fcr sorgen, dass die Aufgabe morgen im Digest erscheint.", []),
        ("\"The todo item appears in the digest.\"", []),
        ("Can you make the todo item appear in the digest tomorrow?", []),
        ("The todo item never appears in the digest tomorrow.", []),
        ("The todo item will appear in the digest.", []),
        ("The todo item appears in the digest.", [("todo_digest_contains", "supported")]),
        ("The todo item will appear in the digest tomorrow.", [("todo_digest_schedule_active", "unsupported")]),
        ("The todo item appears in the digest at 09:00.", [("todo_digest_schedule_active", "unsupported")]),
    ],
)
def test_legacy_digest_receipt_parser_matches_current_sentence_guards(tmp_path: Path, text: str, expected: list[tuple[str, str]]):
    report = evaluate_response_claims(text, [_legacy_todo_digest_event()], repo_root=tmp_path)

    assert [(item.claim_type, item.status) for item in report.findings] == expected


def test_exact_time_and_delivery_words_stay_unsupported_even_with_schedule_receipt(tmp_path: Path):
    for text in ("The todo item appears in the digest at 09:00.", "The todo item appears in the digest and was delivered on Telegram.", "The todo item appears in the digest after provider execution."):
        report = evaluate_response_claims(text, [_todo_next_digest_event()], repo_root=tmp_path)
        assert [(item.claim_type, item.status) for item in report.findings] == [("todo_digest_schedule_active", "unsupported")]


def test_next_digest_claim_requires_independent_membership_and_schedule_receipts(tmp_path: Path):
    text = "The todo item appears in the next digest."
    both = evaluate_response_claims(text, [_todo_next_digest_event()], repo_root=tmp_path)
    no_membership = evaluate_response_claims(text, [_todo_next_digest_event(include_membership=False)], repo_root=tmp_path)
    no_schedule = evaluate_response_claims(text, [_todo_next_digest_event(include_schedule=False)], repo_root=tmp_path)

    assert [(item.claim_type, item.status) for item in both.findings] == [
        ("todo_digest_contains", "supported"), ("todo_digest_schedule_active", "supported"),
    ]
    assert [(item.claim_type, item.status) for item in no_membership.findings] == [
        ("todo_digest_contains", "unsupported"), ("todo_digest_schedule_active", "unsupported"),
    ]
    assert [(item.claim_type, item.status) for item in no_schedule.findings] == [
        ("todo_digest_contains", "supported"), ("todo_digest_schedule_active", "unsupported"),
    ]


def test_next_digest_schedule_receipt_must_share_one_event_and_owner(tmp_path: Path):
    from datetime import datetime
    from hashlib import sha256
    text = "The todo item appears in the next digest."
    split = evaluate_response_claims(text, [_todo_next_digest_event(include_schedule=False), _todo_next_digest_event(include_membership=False)], repo_root=tmp_path)
    other_owner = _todo_next_digest_event(include_membership=False, include_schedule=False)
    bob_ref = f"owner:{sha256(b'bob').hexdigest()[:16]}"
    other_owner["todo_semantic_receipt"]["evidence_refs"] = (bob_ref, *other_owner["todo_semantic_receipt"]["evidence_refs"][1:])
    other_owner[TODO_DIGEST_SCHEDULE_RECEIPT_FIELD] = build_todo_digest_schedule_receipt(
        owner="bob", candidates=[{
            "id": "bob-task", "owner": "bob", "task_type": "action", "action": "todo_digest",
            "trigger_type": "schedule", "schedule": "cron", "status": "active",
            "cron_expression": "0 9 * * 1-5", "scheduled_time": "09:00", "next_run": datetime(2026, 7, 25),
        }], now_utc=datetime(2026, 7, 24),
    )
    cross_owner = evaluate_response_claims(text, [_todo_next_digest_event(include_schedule=False), other_owner], repo_root=tmp_path)

    expected = [("todo_digest_contains", "supported"), ("todo_digest_schedule_active", "unsupported")]
    assert [(item.claim_type, item.status) for item in split.findings] == expected
    assert [(item.claim_type, item.status) for item in cross_owner.findings] == expected


def test_exact_schedule_implications_override_generic_next_schedule_support_in_any_order(tmp_path: Path):
    generic = "The todo item appears in the next digest."
    expected = [("todo_digest_contains", "supported"), ("todo_digest_schedule_active", "unsupported")]
    for exact in ("The todo item appears in the digest after provider execution.", "The todo item appears in the digest after it runs.", "Die Aufgabe erscheint im Digest und wird versendet."):
        for text in (f"{generic} {exact}", f"{exact} {generic}"):
            report = evaluate_response_claims(text, [_todo_next_digest_event()], repo_root=tmp_path)
            assert [(item.claim_type, item.status) for item in report.findings] == expected


def test_file_creation_claim_requires_file_or_tool_evidence(tmp_path: Path):
    report = evaluate_response_claims("Ich habe `pong.py` erstellt.", [], repo_root=tmp_path)

    assert report.ok is False
    assert report.unsupported[0].claim_type == "file_changed"


def test_file_creation_claim_accepts_existing_file(tmp_path: Path):
    (tmp_path / "pong.py").write_text("print('pong')\n", encoding="utf-8")

    report = evaluate_response_claims("Ich habe `pong.py` erstellt.", [], repo_root=tmp_path)

    assert report.ok is True
    assert report.findings[0].evidence == ("pong.py",)


def test_test_success_claim_requires_successful_test_command(tmp_path: Path):
    report = evaluate_response_claims(
        "Ich habe die Tests ausgefuehrt, sie sind durchgelaufen.",
        [{"tool": "bash", "command": "python -m pytest tests/test_demo.py", "output": "1 failed", "exit_code": 1}],
        repo_root=tmp_path,
    )

    assert report.ok is False
    assert report.unsupported[0].claim_type == "command_passed"


def test_test_success_claim_accepts_green_test_command(tmp_path: Path):
    report = evaluate_response_claims(
        "Ich habe die Tests ausgefuehrt, sie sind durchgelaufen.",
        [{"tool": "bash", "command": "python -m pytest tests/test_demo.py", "output": "1 passed", "exit_code": 0}],
        repo_root=tmp_path,
    )

    assert report.ok is True


def test_telegram_screenshot_claim_separates_dispatch_and_artifact(tmp_path: Path):
    artifact = tmp_path / "data" / "reports" / "autonomous_coding_agent" / "pong" / "screen.png"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"\x89PNG\r\n\x1a\n")
    report = evaluate_response_claims(
        "Ich habe den Screenshot `data/reports/autonomous_coding_agent/pong/screen.png` per Telegram geschickt.",
        [{"tool": "telegram_document_reply", "command": "send", "output": "sent ok", "exit_code": 0}],
        repo_root=tmp_path,
    )

    assert report.ok is True
    assert {item.claim_type for item in report.findings} == {"telegram_sent", "artifact_exists"}


def test_correction_mentions_unsupported_claim_types(tmp_path: Path):
    report = evaluate_response_claims("Ich habe `pong.py` erstellt.", [], repo_root=tmp_path)

    correction = build_claim_evidence_correction(report)

    assert "file_changed" in correction
    assert "nicht verifiziert" in correction


def test_test_claim_correction_names_exact_unsupported_assertion_without_persisting_it(tmp_path: Path):
    assertion = "Das Ganze ist schon getestet in 3 neuen Tests."
    report = evaluate_response_claims(assertion, [], repo_root=tmp_path)

    correction = build_claim_evidence_correction(report)

    assert report.ok is False
    assert report.unsupported[0].claim_type == "command_passed"
    assert f"Nicht verifiziert: \u201e{assertion}\u201c" in correction
    assert "erfolgreicher Testlauf" in correction
    assert assertion not in repr(report.to_dict())
    assert assertion not in repr(report)


def test_test_success_claim_accepts_verified_transaction_without_raw_tool_event(tmp_path: Path):
    tx = ToolTransaction.create(
        surface="agent",
        tool="bash",
        claim_type="command_passed",
        status="succeeded",
        evidence_refs=["exit_code:0", "command:sha256:abc123"],
        exit_code=0,
        command="python -m pytest tests/test_demo.py",
    )

    report = evaluate_response_claims(
        "Ich habe die Tests ausgefuehrt, sie sind durchgelaufen.",
        [],
        repo_root=tmp_path,
        tool_transactions=[tx.to_dict()],
    )

    assert report.ok is True


def test_failed_transaction_does_not_support_success_claim(tmp_path: Path):
    tx = ToolTransaction.create(
        surface="agent",
        tool="bash",
        claim_type="command_passed",
        status="failed",
        evidence_refs=["exit_code:1"],
        exit_code=1,
        command="python -m pytest tests/test_demo.py",
    )

    report = evaluate_response_claims(
        "Ich habe die Tests ausgefuehrt, sie sind durchgelaufen.",
        [],
        repo_root=tmp_path,
        tool_transactions=[tx.to_dict()],
    )

    assert report.ok is False
    assert report.unsupported[0].claim_type == "command_passed"


def _artifact_event(**claims):
    return {
        "tool": "publish_artifact",
        "exit_code": 0,
        "artifact_evidence": {
            "artifact_id": "a" * 32 + ".png",
            "artifact_hash": "b" * 64,
            **claims,
        },
    }


def test_visual_and_download_claims_require_typed_artifact_evidence(tmp_path: Path):
    response = "Visual inspection: verified. Download is ready."

    unsupported = evaluate_response_claims(response, [], repo_root=tmp_path)
    supported = evaluate_response_claims(
        response,
        [
            _artifact_event(
                visual_inspected={"status": "verified"},
                download_ready={"status": "verified"},
            )
        ],
        repo_root=tmp_path,
    )

    assert {item.claim_type for item in unsupported.unsupported} == {"visual_inspected", "download_ready"}
    assert supported.ok is True
    assert all("sha256" in item.evidence[0] for item in supported.findings)


def test_headless_claim_does_not_imply_interactive_preview(tmp_path: Path):
    event = _artifact_event(headless_tested={"status": "verified"})
    report = evaluate_response_claims(
        "Headless verification passed and the game is playable here.",
        [event],
        repo_root=tmp_path,
    )

    by_type = {item.claim_type: item for item in report.findings}
    assert by_type["headless_tested"].supported is True
    assert by_type["interactive_preview_ready"].supported is False


def test_negated_visual_statement_is_not_treated_as_success_claim(tmp_path: Path):
    report = evaluate_response_claims(
        "The screenshot was not visually inspected.",
        [],
        repo_root=tmp_path,
    )

    assert report.findings == ()


def test_failed_download_statement_is_not_treated_as_ready_claim(tmp_path: Path):
    report = evaluate_response_claims(
        "I couldn't create a download link.",
        [],
        repo_root=tmp_path,
    )

    assert report.findings == ()


@pytest.mark.parametrize(
    ("text", "event", "claim_type"),
    [
        ("I added a todo item.", _todo_event("add"), "todo_item_created"),
        ("Ich habe eine Todo-Aufgabe erstellt.", _todo_event("add"), "todo_item_created"),
        ("I checked off the task.", _todo_event("complete"), "todo_item_completed"),
        ("Ich habe die Aufgabe erledigt.", _todo_event("complete"), "todo_item_completed"),
        ("I reopened the todo item.", _todo_event("reopen"), "todo_item_reopened"),
        ("Ich habe die Aufgabe wieder geöffnet.", _todo_event("reopen"), "todo_item_reopened"),
        ("I deleted the todo item.", _todo_event("remove"), "todo_item_removed"),
        ("Ich habe den Todo-Eintrag gelöscht.", _todo_event("remove"), "todo_item_removed"),
        ("I showed the todo list.", _todo_list_event(), "todo_list_read"),
        ("Ich habe die Todo-Liste angezeigt.", _todo_list_event(), "todo_list_read"),
    ],
)
def test_todo_success_claims_require_matching_verified_semantic_receipts(
    tmp_path: Path, text: str, event: dict, claim_type: str
):
    report = evaluate_response_claims(text, [event], repo_root=tmp_path)

    assert report.ok is True
    assert [(finding.claim_type, finding.status) for finding in report.findings] == [
        (claim_type, "supported")
    ]
    assert all("private" not in evidence for evidence in report.findings[0].evidence)


@pytest.mark.parametrize(
    ("text", "event", "claim_type"),
    [
        ("I completed the todo item.", _todo_event("add"), "todo_item_completed"),
        ("Ich habe die Todo-Liste angezeigt.", _todo_event("complete"), "todo_list_read"),
    ],
)
def test_todo_claims_do_not_cross_match_actions_or_list_reads(
    tmp_path: Path, text: str, event: dict, claim_type: str
):
    report = evaluate_response_claims(text, [event], repo_root=tmp_path)

    assert report.ok is False
    assert [(finding.claim_type, finding.status) for finding in report.findings] == [
        (claim_type, "unsupported")
    ]


@pytest.mark.parametrize(
    ("events", "tool_transactions"),
    [
        ((), ()),
        (({"tool": "manage_todos", "action": "complete", "exit_code": 0},), ()),
        (({"tool": "manage_todos", "action": "complete", "todo_semantic_receipt": {}},), ()),
        (
            (),
            (
                ToolTransaction.create(
                    surface="agent",
                    tool="manage_todos",
                    claim_type="tool_execution",
                    status="verified",
                    evidence_refs=["owner:0123456789abcdef"],
                    exit_code=0,
                ).to_dict(),
            ),
        ),
        (
            (),
            (
                ToolTransaction.create(
                    surface="agent",
                    tool="manage_todos",
                    claim_type="todo_item_completed",
                    status="failed",
                    evidence_refs=["owner:0123456789abcdef"],
                    exit_code=1,
                ).to_dict(),
            ),
        ),
    ],
)
def test_todo_claims_stay_unsupported_without_verified_matching_evidence(
    tmp_path: Path, events: tuple, tool_transactions: tuple
):
    report = evaluate_response_claims(
        "I completed the todo item.",
        events,
        repo_root=tmp_path,
        tool_transactions=tool_transactions,
    )

    assert [(finding.claim_type, finding.status) for finding in report.findings] == [
        ("todo_item_completed", "unsupported")
    ]


@pytest.mark.parametrize(
    "text",
    [
        "I have not completed the todo item.",
        "If I would have completed the todo item, the list would be empty.",
        "I want you to mark the todo item as done.",
        "I will have completed the todo item tomorrow.",
        "Read the todo list.",
        "I have done a review of the todo list.",
        "\"I completed the todo item.\"",
        "'I completed the todo item.'",
        "`I completed the todo item.`",
        "The user reported that I completed the todo item.",
        "The user says Todo item completed.",
        "According to the log, Todo item completed.",
        "I asked whether the todo item was completed.",
        "Ich fragte, ob die Aufgabe erledigt wurde.",
    ],
)
def test_todo_claim_detection_ignores_non_success_language(tmp_path: Path, text: str):
    report = evaluate_response_claims(text, [_todo_event("complete")], repo_root=tmp_path)

    assert not any(finding.claim_type.startswith("todo_") for finding in report.findings)


def test_todo_claim_before_a_later_future_statement_remains_supported(tmp_path: Path):
    report = evaluate_response_claims(
        "I completed the todo item and will send a summary tomorrow.",
        [_todo_event("complete")],
        repo_root=tmp_path,
    )

    assert [(finding.claim_type, finding.status) for finding in report.findings] == [
        ("todo_item_completed", "supported")
    ]


def test_todo_context_binds_to_the_nearest_action_only(tmp_path: Path):
    report = evaluate_response_claims(
        "I completed the migration and listed the todo items.",
        [_todo_list_event()],
        repo_root=tmp_path,
    )

    assert [(finding.claim_type, finding.status) for finding in report.findings] == [
        ("todo_list_read", "supported")
    ]


@pytest.mark.parametrize(
    ("text", "event", "claim_type"),
    [
        ("Todo item added.", _todo_event("add"), "todo_item_created"),
        ("Die Aufgabe ist erledigt.", _todo_event("complete"), "todo_item_completed"),
        ("Todo-Eintrag gelöscht.", _todo_event("remove"), "todo_item_removed"),
        ("Todo-Liste angezeigt.", _todo_list_event(), "todo_list_read"),
    ],
)
def test_todo_resultative_claims_require_matching_verified_transactions(
    tmp_path: Path, text: str, event: dict, claim_type: str
):
    report = evaluate_response_claims(text, [event], repo_root=tmp_path)

    assert [(finding.claim_type, finding.status) for finding in report.findings] == [
        (claim_type, "supported")
    ]


@pytest.mark.parametrize(
    ("text", "events", "claim_type"),
    [
        ("Todo item added.", (), "todo_item_created"),
        ("Todo-Liste angezeigt.", (_todo_event("complete"),), "todo_list_read"),
    ],
)
def test_todo_resultative_claims_stay_unsupported_without_exact_evidence(
    tmp_path: Path, text: str, events: tuple, claim_type: str
):
    report = evaluate_response_claims(text, events, repo_root=tmp_path)

    assert [(finding.claim_type, finding.status) for finding in report.findings] == [
        (claim_type, "unsupported")
    ]


def test_multiple_todo_success_claims_are_checked_independently(tmp_path: Path):
    report = evaluate_response_claims(
        "I added a todo item. Ich habe die Aufgabe erledigt. I removed the todo item.",
        [_todo_event("add"), _todo_event("complete"), _todo_event("remove")],
        repo_root=tmp_path,
    )

    assert {(finding.claim_type, finding.status) for finding in report.findings} == {
        ("todo_item_created", "supported"),
        ("todo_item_completed", "supported"),
        ("todo_item_removed", "supported"),
    }


def test_todo_reports_and_corrections_do_not_expose_raw_todo_values(tmp_path: Path):
    raw_text = "I completed the todo item secret-todo-text in private-list-id."
    report = evaluate_response_claims(raw_text, [], repo_root=tmp_path)
    correction = build_claim_evidence_correction(report)

    assert report.unsupported[0].claim_type == "todo_item_completed"
    assert "secret-todo-text" not in repr(report.to_dict())
    assert "private-list-id" not in correction


def _github_duplicate_result(*, count: int = 1, blocks: bool = False) -> dict:
    return {
        "action": "duplicate_search",
        "status": "duplicates_checked_local",
        "persisted": False,
        "github_issue_duplicates": {
            "owner": "private-owner",
            "repository": "private/repository",
            "blocks_auto_create": blocks,
            "candidates": [
                {
                    "external_id": f"private-{index}",
                    "score": 900,
                    "reason": "private candidate text",
                    "state": "open",
                    "labels": ["private-label"],
                    "url": "https://example.invalid/private",
                    "blocks_auto_create": blocks,
                }
                for index in range(count)
            ],
        },
        "index": {"private": "index"},
        "recorded_candidates": 0,
        "exit_code": 0,
    }


def _github_duplicate_event(*, count: int = 1, blocks: bool = False) -> dict:
    from src import agent_loop

    forward = getattr(agent_loop, "_github_issue_tool_event_forward")
    return forward(_github_duplicate_result(count=count, blocks=blocks))


def test_github_issue_claim_language_ignores_negated_hypothetical_and_quoted_text():
    from src import claim_evidence_gate

    findings_for = getattr(claim_evidence_gate, "_github_issue_claim_findings")
    event = _github_duplicate_event()
    texts = (
        "I checked no GitHub issue duplicates locally.",
        "I did not check GitHub issue duplicates locally.",
        "I never checked GitHub issue duplicates locally.",
        "I checked GitHub issue duplicates locally without searching.",
        "If I checked GitHub issue duplicates locally, I would report it.",
        "Maybe I checked GitHub issue duplicates locally.",
        'The user said "I checked GitHub issue duplicates locally."',
        "According to the report, GitHub issue duplicates were checked locally.",
        "`I checked GitHub issue duplicates locally.`",
    )

    for text in texts:
        assert findings_for(text, (event,)) == ()


def test_github_issue_create_set_fields_and_sync_claims_remain_unsupported():
    from src import claim_evidence_gate

    findings_for = getattr(claim_evidence_gate, "_github_issue_claim_findings")
    cases = (
        ("I created the GitHub issue.", "create_triaged", "github_issue_created"),
        ("I set the GitHub issue fields.", "set_fields", "github_issue_fields_set"),
        ("I synced the GitHub issues.", "sync", "github_issue_synced"),
    )
    for text, action, claim_type in cases:
        event = {"tool": "manage_github_issues", "action": action, "status": "success", "exit_code": 0}
        findings = findings_for(text, (event,))
        assert [(item.claim_type, item.status, item.evidence) for item in findings] == [
            (claim_type, "unsupported", ()),
        ]


def test_github_issue_duplicate_claim_does_not_borrow_authority_across_mixed_clauses():
    from src import claim_evidence_gate

    findings_for = getattr(claim_evidence_gate, "_github_issue_claim_findings")
    event = _github_duplicate_event()
    assert findings_for(
        "I checked GitHub issue duplicates locally, but I did not check GitHub issue duplicates locally.",
        (event,),
    ) == ()
    assert findings_for(
        'The report said "I did not check GitHub issue duplicates locally."; I checked GitHub issue duplicates locally.',
        (event,),
    )[0].status == "supported"
    assert findings_for(
        '"I checked GitHub issue duplicates locally"; I did not check GitHub issue duplicates locally.',
        (event,),
    ) == ()
    for text in (
        "I checked GitHub issue duplicates locally, but I did not.",
        "I checked GitHub issue duplicates locally, but maybe not.",
    ):
        assert findings_for(text, (event,)) == ()
    for actor in ("I", "We"):
        for apostrophe in ("'", "\u2019"):
            contradiction = f"{actor} didn{apostrophe}t"
            for separator in (". ", ", but "):
                assert findings_for(
                    f"{actor} checked GitHub issue duplicates locally{separator}{contradiction}.",
                    (event,),
                ) == ()
                unrelated = findings_for(
                    f"{actor} checked GitHub issue duplicates locally{separator}"
                    f"{contradiction} persist anything.",
                    (event,),
                )
                assert [
                    (item.claim_type, item.status) for item in unrelated
                ] == [("github_issue_duplicates_checked", "supported")]


def test_github_issue_duplicate_claim_rejects_generic_exit_zero_and_live_gate_packets():
    from src import claim_evidence_gate

    findings_for = getattr(claim_evidence_gate, "_github_issue_claim_findings")
    packets = (
        {"tool": "manage_github_issues", "action": "duplicate_search", "exit_code": 0},
        {"tool": "manage_github_issues", "action": "duplicate_search", "status": "needs_live_go", "exit_code": 0},
        {"tool": "manage_github_issues", "action": "duplicate_search", "status": "confirmation_required", "exit_code": 0},
        {"tool": "manage_github_issues", "action": "create_triaged", "status": "blocked_by_duplicate_candidate", "exit_code": 0},
    )
    for packet in packets:
        findings = findings_for("I checked GitHub issue duplicates locally.", (packet,))
        assert [(item.claim_type, item.status, item.evidence) for item in findings] == [
            ("github_issue_duplicates_checked", "unsupported", ()),
        ]


def test_github_issue_duplicate_claim_requires_affirmative_self_performed_full_clause():
    from src import claim_evidence_gate

    findings_for = getattr(claim_evidence_gate, "_github_issue_claim_findings")
    event = _github_duplicate_event()
    for text in (
        "I checked GitHub issue duplicates locally.",
        "I searched GitHub issues for duplicates locally.",
        "We checked GitHub issue duplicates locally.",
        "We searched GitHub issues for duplicates locally.",
    ):
        findings = findings_for(text, (event,))
        assert [(item.claim_type, item.status) for item in findings] == [
            ("github_issue_duplicates_checked", "supported"),
        ]
    assert findings_for("The tool checked GitHub issue duplicates locally.", (event,)) == ()
    assert findings_for("The search completed locally.", (event,)) == ()
    assert findings_for("The user said I checked GitHub issue duplicates locally.", (event,)) == ()
    assert findings_for("I checked GitHub issue duplicates locally.", ())[0].status == "unsupported"


def test_github_issue_duplicate_claim_requires_exact_verified_receipt():
    from src import claim_evidence_gate

    findings_for = getattr(claim_evidence_gate, "_github_issue_claim_findings")
    text = "I checked GitHub issue duplicates locally."
    for count in (0, 1000):
        findings = findings_for(text, (_github_duplicate_event(count=count),))
        assert findings[0].status == "supported"
        assert findings[0].evidence == (f"github-issue-duplicate-search:{count}:0",)

    event = _github_duplicate_event()
    for key, value in (
        ("schema", "wrong"),
        ("action", "sync"),
        ("status", "confirmation_required"),
        ("persisted", True),
        ("candidate_count", 1001),
        ("blocks_auto_create", 0),
        ("exit_code", True),
    ):
        malformed = {**event, "github_issue_duplicate_search_receipt": dict(event["github_issue_duplicate_search_receipt"])}
        malformed["github_issue_duplicate_search_receipt"][key] = value
        assert findings_for(text, (malformed,))[0].status == "unsupported"


def test_github_issue_event_forward_is_content_free_and_detached():
    from src import agent_loop

    forward = getattr(agent_loop, "_github_issue_tool_event_forward")
    source = _github_duplicate_result(count=2, blocks=True)
    event = forward(source)
    assert event == {
        "tool": "manage_github_issues",
        "action": "duplicate_search",
        "github_issue_duplicate_search_receipt": {
            "schema": "odysseus.github_issue_duplicate_search_receipt.v1",
            "action": "duplicate_search",
            "status": "duplicates_checked_local",
            "persisted": False,
            "candidate_count": 2,
            "blocks_auto_create": True,
            "exit_code": 0,
        },
    }
    assert "private" not in repr(event)
    source["github_issue_duplicates"]["candidates"].clear()
    source["github_issue_duplicates"]["blocks_auto_create"] = False
    assert event["github_issue_duplicate_search_receipt"]["candidate_count"] == 2
    assert event["github_issue_duplicate_search_receipt"]["blocks_auto_create"] is True
    assert forward(source) is not event


def test_github_issue_receipt_rejects_hostile_mapping_wrong_types_extra_fields_and_mutation():
    from collections.abc import Mapping
    from src import claim_evidence_gate

    validate = getattr(claim_evidence_gate, "_validated_github_issue_duplicate_search_receipt")
    event = _github_duplicate_event()
    receipt = event["github_issue_duplicate_search_receipt"]
    snapshot = validate(event)
    assert snapshot == receipt
    receipt["candidate_count"] = 999
    assert snapshot["candidate_count"] == 1

    class HostileMapping(Mapping):
        def __getitem__(self, key):
            raise RuntimeError("private callback")

        def __iter__(self):
            raise RuntimeError("private callback")

        def __len__(self):
            raise RuntimeError("private callback")

    class HostileString(str):
        def __eq__(self, other):
            raise RuntimeError("private callback")

    assert validate(HostileMapping()) is None
    hostile = _github_duplicate_event()
    hostile["github_issue_duplicate_search_receipt"]["schema"] = HostileString(
        "odysseus.github_issue_duplicate_search_receipt.v1"
    )
    assert validate(hostile) is None
    extra = _github_duplicate_event()
    extra["github_issue_duplicate_search_receipt"]["private"] = "leak"
    assert validate(extra) is None
