from pathlib import Path

import pytest

from src.claim_evidence_gate import build_claim_evidence_correction, evaluate_response_claims
from src.todo_digest_receipts import TODO_DIGEST_RECEIPT_FIELD, build_todo_digest_membership_receipt
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
            "owner:0123456789abcdef",
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

    assert [(item.claim_type, item.status) for item in next_report.findings] == [("todo_digest_schedule_active", "unsupported")]
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
