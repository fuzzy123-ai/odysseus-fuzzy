from __future__ import annotations

import hashlib
import inspect
import json

import pytest

import src.execution_authority_ledger as ledger_module
from src.execution_trust_authority import (
    ATTESTATION_AUTHORITY_REF_SCHEMA_ID,
    build_execution_trust_authority_payload,
    validate_execution_trust_authority,
)


HASH_A = "sha256:" + "a" * 64
HASH_B = "sha256:" + "b" * 64
HASH_C = "sha256:" + "c" * 64
RECEIPT_OBSERVED = "2026-08-03T13:00:00+02:00"
OBSERVED = "2026-08-03T19:00:00+02:00"
EVENT_AT = "2026-08-03T14:00:00+02:00"
LEASE_AT = "2026-08-03T18:00:00+02:00"


_R3_FUNCTIONS = {
    "acquire_execution_claim",
    "build_execution_authority_ledger",
    "decide_execution_claim_gate",
    "execution_authority_ledger_reference",
    "project_execution_authority_ledger",
    "record_execution_claim_evidence",
    "release_execution_claim",
    "renew_execution_claim_lease",
    "validate_execution_authority_ledger",
}


def _require_r3_api() -> None:
    missing = sorted(name for name in _R3_FUNCTIONS if not hasattr(ledger_module, name))
    assert not missing, f"missing R3 API: {missing}"
    assert not hasattr(ledger_module, "append_execution_authority_event")


def _definition_ref() -> dict[str, object]:
    return {
        "schema_id": "odysseus.definition_authority_snapshot_ref.v1",
        "project_id": "project:test",
        "roadmap_id": "roadmap:test",
        "planning_revision": 1,
        "planning_content_hash": HASH_A,
        "snapshot_hash": HASH_A,
    }


def _principal(identifier: str, subject: str, role: str) -> dict[str, str]:
    return {
        "principal_id": identifier,
        "subject_digest": "sha256:" + subject * 64,
        "role": role,
        "verifier_binding_digest": "sha256:" + hashlib.sha256(identifier.encode()).hexdigest(),
        "valid_from": "2026-08-03T12:00:00+02:00",
        "valid_until": "2026-08-03T22:00:00+02:00",
    }


def _trust() -> tuple[bytes, dict[str, str]]:
    authority = build_execution_trust_authority_payload(
        authority_id="authority:test",
        authority_revision=1,
        definition_snapshot_hash=HASH_A,
        capability_catalog=["repo-read", "repo-write"],
        gate_catalog=["gate-deep", "gate-static"],
        verification_policies=[
            {"verification_rule_id": "rule-deep", "required_reviewer_role": "deep_review"},
            {"verification_rule_id": "rule-static", "required_reviewer_role": "independent_qa"},
        ],
        principals=[
            _principal("owner:alice", "1", "claim_owner"),
            _principal("reviewer:bob", "2", "independent_qa"),
            _principal("reviewer:sol", "3", "deep_review"),
        ],
        attestation_authority_ref={
            "schema_id": ATTESTATION_AUTHORITY_REF_SCHEMA_ID,
            "authority_id": "attestation:test",
            "authority_revision": 1,
            "verifier_digest": HASH_C,
        },
        valid_from="2026-08-03T12:00:00+02:00",
        valid_until="2026-08-03T22:00:00+02:00",
    )
    receipt = validate_execution_trust_authority(
        authority,
        expected_authority_hash=authority["authority_hash"],
        expected_definition_snapshot_hash=HASH_A,
        observed_at=RECEIPT_OBSERVED,
    )
    return receipt, {
        "expected_trust_receipt_hash": json.loads(receipt)["receipt_hash"],
        "expected_trust_authority_hash": authority["authority_hash"],
    }


def _ledger() -> tuple[bytes, bytes, dict[str, str]]:
    receipt, pins = _trust()
    value = ledger_module.build_execution_authority_ledger(
        ledger_id="ledger:test",
        definition_snapshot_ref=_definition_ref(),
        trust_receipt=receipt,
        observed_at=OBSERVED,
        **pins,
    )
    return value, receipt, pins


def _cursor(value: bytes) -> tuple[int, str]:
    document = json.loads(value)
    return document["cursor"], document["head_hash"]


def _claim_kwargs(**updates: object) -> dict[str, object]:
    result: dict[str, object] = {
        "command_id": "command:acquire",
        "occurred_at": EVENT_AT,
        "claim_id": "claim:a",
        "owner_principal_id": "owner:alice",
        "write_paths": ["src/a.py"],
        "read_only_paths": ["docs/reference.json"],
        "lease_expires_at": LEASE_AT,
        "effect_capability_refs": ["repo-write"],
        "required_gate_ids": ["gate-static"],
        "required_verification_rule_ids": ["rule-static"],
        "live_actions": False,
    }
    result.update(updates)
    return result


def _authority_kwargs(receipt: bytes, pins: dict[str, str], *, observed_at: str = OBSERVED) -> dict[str, object]:
    return {
        "definition_snapshot_ref": _definition_ref(),
        "trust_receipt": receipt,
        "observed_at": observed_at,
        **pins,
    }


def _acquire(value: bytes, receipt: bytes, pins: dict[str, str], **updates: object) -> bytes:
    cursor, head = _cursor(value)
    return ledger_module.acquire_execution_claim(
        value,
        expected_cursor=cursor,
        expected_head_hash=head,
        **_claim_kwargs(**updates),
        **_authority_kwargs(receipt, pins),
    )


def _gate(
    value: bytes,
    receipt: bytes,
    pins: dict[str, str],
    *,
    command_id: str = "command:gate",
    decision: str = "approved",
    occurred_at: str = "2026-08-03T15:00:00+02:00",
) -> bytes:
    cursor, head = _cursor(value)
    return ledger_module.decide_execution_claim_gate(
        value,
        command_id=command_id,
        occurred_at=occurred_at,
        claim_id="claim:a",
        gate_id="gate-static",
        decision=decision,
        decision_digest=HASH_A,
        expected_cursor=cursor,
        expected_head_hash=head,
        **_authority_kwargs(receipt, pins),
    )


def _evidence(
    value: bytes,
    receipt: bytes,
    pins: dict[str, str],
    *,
    command_id: str = "command:evidence",
    evidence_id: str = "evidence:a",
    result: str = "pass",
    reviewer_principal_id: str = "reviewer:bob",
    occurred_at: str = "2026-08-03T16:00:00+02:00",
) -> bytes:
    cursor, head = _cursor(value)
    return ledger_module.record_execution_claim_evidence(
        value,
        command_id=command_id,
        occurred_at=occurred_at,
        claim_id="claim:a",
        evidence_id=evidence_id,
        verification_rule_id="rule-static",
        result=result,
        evidence_digest=HASH_B,
        reviewer_principal_id=reviewer_principal_id,
        expected_cursor=cursor,
        expected_head_hash=head,
        **_authority_kwargs(receipt, pins),
    )


def _release(
    value: bytes,
    receipt: bytes,
    pins: dict[str, str],
    *,
    outcome: str = "accepted",
    review_evidence_id: str | None = "evidence:a",
    command_id: str = "command:release",
) -> bytes:
    cursor, head = _cursor(value)
    return ledger_module.release_execution_claim(
        value,
        command_id=command_id,
        occurred_at="2026-08-03T17:00:00+02:00",
        claim_id="claim:a",
        outcome=outcome,
        review_evidence_id=review_evidence_id,
        expected_cursor=cursor,
        expected_head_hash=head,
        **_authority_kwargs(receipt, pins),
    )


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()


def _framed_hash(domain: str, payload: object) -> str:
    encoded = _canonical(payload)
    framed = domain.encode("ascii") + b"\0" + len(encoded).to_bytes(8, "big") + encoded
    return "sha256:" + hashlib.sha256(framed).hexdigest()


def _rehash(document: dict[str, object]) -> bytes:
    events = document["events"]
    assert isinstance(events, list)
    for event in events:
        assert isinstance(event, dict)
        event["event_hash"] = _framed_hash(
            "odysseus.execution_authority.event.v3",
            {key: item for key, item in event.items() if key != "event_hash"},
        )
    document["head_hash"] = events[-1]["event_hash"] if events else document["head_hash"]
    document["ledger_hash"] = _framed_hash(
        "odysseus.execution_authority.ledger.v3",
        {key: item for key, item in document.items() if key != "ledger_hash"},
    )
    return _canonical(document)


def _assert_code(call, code: str) -> None:
    with pytest.raises(ledger_module.ExecutionAuthorityLedgerError) as raised:
        call()
    assert raised.value.code == code
    assert raised.value.args == (code,)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_r3_public_api_uses_typed_commands_and_has_no_generic_append_or_authority_objects() -> None:
    _require_r3_api()
    expected_functions = _R3_FUNCTIONS
    assert expected_functions <= set(ledger_module.__all__)
    assert ledger_module.EXECUTION_AUTHORITY_LEDGER_SCHEMA_ID == "odysseus.execution_authority_ledger.v3"
    assert ledger_module.EXECUTION_AUTHORITY_EVENT_SCHEMA_ID == "odysseus.execution_authority_event.v3"
    assert ledger_module.MAX_LEDGER_EVENTS == 1024
    assert ledger_module.MAX_EVIDENCE_RECORDS == 1023
    assert ledger_module.MAX_GATE_DECISIONS == 1023
    assert not any(hasattr(ledger_module, name) for name in ("ExecutionAuthorityLedger", "Event", "registry"))
    assert "event_type" not in inspect.signature(ledger_module.acquire_execution_claim).parameters
    assert "payload" not in inspect.signature(ledger_module.acquire_execution_claim).parameters


def test_r3_claim_paths_catalogs_and_owner_are_exact_and_trust_resolved() -> None:
    _require_r3_api()
    value, receipt, pins = _ledger()
    value = _acquire(value, receipt, pins)
    projection = ledger_module.project_execution_authority_ledger(value, **_authority_kwargs(receipt, pins))
    assert projection["active_claims"] == [
        {
            "schema_id": "odysseus.execution_authority_active_claim.v3",
            "claim_id": "claim:a",
            "owner_principal_id": "owner:alice",
            "write_paths": ["src/a.py"],
            "read_only_paths": ["docs/reference.json"],
            "lease_expires_at": LEASE_AT,
            "effect_capability_refs": ["repo-write"],
            "required_gate_ids": ["gate-static"],
            "required_verification_rule_ids": ["rule-static"],
            "live_actions": False,
            "acquired_at": EVENT_AT,
        }
    ]
    fresh, receipt, pins = _ledger()
    _assert_code(lambda: _acquire(fresh, receipt, pins, owner_principal_id="reviewer:bob"), "stale_authority")
    _assert_code(lambda: _acquire(fresh, receipt, pins, effect_capability_refs=["shell-admin"]), "stale_authority")


def test_r3_command_idempotency_is_semantic_and_collision_closed() -> None:
    _require_r3_api()
    original, receipt, pins = _ledger()
    result = _acquire(original, receipt, pins)
    old_cursor, old_head = _cursor(original)
    retry = ledger_module.acquire_execution_claim(
        result,
        expected_cursor=old_cursor,
        expected_head_hash=old_head,
        **_claim_kwargs(),
        **_authority_kwargs(receipt, pins),
    )
    assert retry == result
    _assert_code(
        lambda: ledger_module.acquire_execution_claim(
            result,
            expected_cursor=old_cursor,
            expected_head_hash=old_head,
            **_claim_kwargs(write_paths=["src/changed.py"]),
            **_authority_kwargs(receipt, pins),
        ),
        "idempotency_conflict",
    )


def test_r3_foreign_genesis_event_and_chain_splice_fail_closed() -> None:
    _require_r3_api()
    value, receipt, pins = _ledger()
    value = _acquire(value, receipt, pins)
    foreign = json.loads(value)
    foreign["events"][0]["ledger_id"] = "ledger:foreign"
    _assert_code(
        lambda: ledger_module.validate_execution_authority_ledger(
            _rehash(foreign), **_authority_kwargs(receipt, pins)
        ),
        "foreign_event",
    )
    value = _gate(value, receipt, pins)
    forked = json.loads(value)
    forked["events"][1]["previous_event_hash"] = HASH_A
    _assert_code(
        lambda: ledger_module.validate_execution_authority_ledger(
            _rehash(forked), **_authority_kwargs(receipt, pins)
        ),
        "forked_ledger",
    )


def test_r3_gate_evidence_and_accepted_release_are_claim_derived() -> None:
    _require_r3_api()
    value, receipt, pins = _ledger()
    value = _acquire(value, receipt, pins)
    value = _gate(value, receipt, pins)
    value = _evidence(value, receipt, pins)
    assert ledger_module.project_execution_authority_ledger(
        value,
        **_authority_kwargs(receipt, pins, observed_at="2026-08-03T17:30:00+02:00"),
    )["dispatch_ready_claim_ids"] == ["claim:a"]
    value = _release(value, receipt, pins)
    projection = ledger_module.project_execution_authority_ledger(value, **_authority_kwargs(receipt, pins))
    assert projection["active_claims"] == []
    assert projection["accepted_releases"][0]["claim_id"] == "claim:a"
    assert projection["accepted_releases"][0]["review_evidence_id"] == "evidence:a"
    assert projection["accepted_releases"][0]["released_at"] == "2026-08-03T17:00:00+02:00"


def test_r3_reviewer_authority_is_resolved_and_release_references_existing_pass_evidence() -> None:
    _require_r3_api()
    value, receipt, pins = _ledger()
    value = _acquire(value, receipt, pins)
    value = _gate(value, receipt, pins)
    _assert_code(
        lambda: _evidence(value, receipt, pins, reviewer_principal_id="owner:alice"),
        "stale_authority",
    )
    value = _evidence(value, receipt, pins)
    cursor, head = _cursor(value)
    _assert_code(
        lambda: ledger_module.release_execution_claim(
            value,
            command_id="command:release:missing",
            occurred_at="2026-08-03T17:00:00+02:00",
            claim_id="claim:a",
            outcome="accepted",
            review_evidence_id="evidence:missing",
            expected_cursor=cursor,
            expected_head_hash=head,
            **_authority_kwargs(receipt, pins),
        ),
        "independent_review_missing",
    )


def test_r3_all_active_and_total_budgets_close_at_max_plus_one() -> None:
    _require_r3_api()
    assert ledger_module.MAX_ACTIVE_CLAIMS == 32
    assert ledger_module.MAX_CLAIMS_TOTAL == 512
    assert ledger_module.MAX_ACCEPTED_RELEASES == 341
    assert ledger_module.MAX_RELEASED_CLAIMS_TOTAL == 512
    assert ledger_module.MAX_COMMAND_IDS == ledger_module.MAX_LEDGER_EVENTS == 1024
    value, receipt, pins = _ledger()
    paths = [f"src/{index:03d}.py" for index in range(ledger_module.MAX_PATHS_PER_CLAIM + 1)]
    _assert_code(lambda: _acquire(value, receipt, pins, write_paths=paths, read_only_paths=[]), "budget_exceeded")
    refs = [f"cap:{index:03d}" for index in range(ledger_module.MAX_REFS_PER_CLAIM_PER_KIND + 1)]
    _assert_code(lambda: _acquire(value, receipt, pins, effect_capability_refs=refs), "budget_exceeded")


def test_r3_repo_paths_reject_drives_aliases_ancestry_and_device_names() -> None:
    _require_r3_api()
    value, receipt, pins = _ledger()
    rejected = [
        "/src/a.py", "C:/src/a.py", "//server/share/a.py", "src\\a.py", "src//a.py",
        "src/../a.py", "src/a.py.", "src/CON.txt", "src/com1.py", "src/LPT².txt",
    ]
    for index, path in enumerate(rejected):
        _assert_code(
            lambda path=path, index=index: _acquire(
                value, receipt, pins, command_id=f"command:path:{index}", write_paths=[path], read_only_paths=[]
            ),
            "invalid_repo_path",
        )
    _assert_code(
        lambda: _acquire(value, receipt, pins, write_paths=["src/a", "src/a/file.py"], read_only_paths=[]),
        "active_scope_collision",
    )


def test_r3_exact_builtin_capture_mutation_and_code_only_errors() -> None:
    _require_r3_api()
    value, receipt, pins = _ledger()

    class HostileList(list):
        def __iter__(self):
            raise AssertionError("must not run")

    _assert_code(lambda: _acquire(value, receipt, pins, write_paths=HostileList(["src/a.py"])), "invalid_type")
    mutable = ["src/a.py"]
    result = _acquire(value, receipt, pins, write_paths=mutable)
    mutable[0] = "src/forged.py"
    projection = ledger_module.project_execution_authority_ledger(result, **_authority_kwargs(receipt, pins))
    assert projection["active_claims"][0]["write_paths"] == ["src/a.py"]
    errors = []
    for _ in range(2):
        with pytest.raises(ledger_module.ExecutionAuthorityLedgerError) as raised:
            ledger_module.validate_execution_authority_ledger(b"not-json", **_authority_kwargs(receipt, pins))
        errors.append(raised.value)
        assert raised.value.code == "invalid_document"
        assert raised.value.args == ("invalid_document",)
        assert raised.value.__cause__ is raised.value.__context__ is None
    assert errors[0] is not errors[1]
