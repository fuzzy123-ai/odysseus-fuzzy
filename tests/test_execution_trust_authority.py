from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json

import pytest

import src.execution_trust_authority as trust_module
from src.execution_trust_authority import (
    ATTESTATION_AUTHORITY_REF_SCHEMA_ID,
    EXECUTION_TRUST_AUTHORITY_RECEIPT_SCHEMA_ID,
    MAX_AUTHORITY_BYTES,
    MAX_AUTHORITY_REVISION,
    MAX_CAPABILITY_REFS,
    MAX_GATE_IDS,
    MAX_PRINCIPALS,
    MAX_VERIFICATION_POLICIES,
    ExecutionTrustAuthorityError,
    build_execution_trust_authority_payload,
    execution_trust_authority_reference,
    resolve_execution_capability_refs,
    resolve_execution_gate_ids,
    resolve_execution_independent_reviewer,
    resolve_execution_principal,
    resolve_execution_verification_policy,
    validate_execution_trust_authority,
    validate_execution_trust_authority_json,
)


DEFINITION_HASH = "sha256:" + ("a" * 64)
FOREIGN_DEFINITION_HASH = "sha256:" + ("b" * 64)
VERIFIER_HASH = "sha256:" + ("c" * 64)
OBSERVED = "2026-08-03T15:00:00+02:00"


def _principal(
    principal_id: str,
    subject: str,
    role: str,
    *,
    valid_from: str = "2026-08-03T12:00:00+02:00",
    valid_until: str = "2026-08-03T20:00:00+02:00",
) -> dict:
    verifier_binding = hashlib.sha256(principal_id.encode("utf-8")).hexdigest()
    return {
        "principal_id": principal_id,
        "subject_digest": "sha256:" + (subject * 64),
        "role": role,
        "verifier_binding_digest": "sha256:" + verifier_binding,
        "valid_from": valid_from,
        "valid_until": valid_until,
    }


def _candidate(
    *,
    definition_hash: str = DEFINITION_HASH,
    authority_revision: int = 1,
    capabilities: list[str] | None = None,
    gates: list[str] | None = None,
    policies: list[dict] | None = None,
    principals: list[dict] | None = None,
) -> dict:
    return build_execution_trust_authority_payload(
        authority_id="authority:round7",
        authority_revision=authority_revision,
        definition_snapshot_hash=definition_hash,
        capability_catalog=["repo-read", "repo-write"] if capabilities is None else capabilities,
        gate_catalog=["gate-static", "gate-ui"] if gates is None else gates,
        verification_policies=(
            [
                {"verification_rule_id": "rule-deep", "required_reviewer_role": "deep_review"},
                {"verification_rule_id": "rule-static", "required_reviewer_role": "independent_qa"},
            ]
            if policies is None
            else policies
        ),
        principals=(
            [
                _principal("owner:alice", "1", "claim_owner"),
                _principal("reviewer:bob", "2", "independent_qa"),
                _principal("reviewer:sol", "3", "deep_review"),
            ]
            if principals is None
            else principals
        ),
        attestation_authority_ref={
            "schema_id": ATTESTATION_AUTHORITY_REF_SCHEMA_ID,
            "authority_id": "attestation:local",
            "authority_revision": 1,
            "verifier_digest": VERIFIER_HASH,
        },
        valid_from="2026-08-03T12:00:00+02:00",
        valid_until="2026-08-03T20:00:00+02:00",
    )


def _rehash(payload: dict, field: str = "authority_hash") -> None:
    unsigned = {key: value for key, value in payload.items() if key != field}
    encoded = json.dumps(unsigned, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()
    payload[field] = "sha256:" + hashlib.sha256(encoded).hexdigest()


def _receipt(payload: dict | None = None, *, observed_at: str = OBSERVED) -> tuple[bytes, str, dict]:
    current = payload or _candidate()
    receipt = validate_execution_trust_authority(
        current,
        expected_authority_hash=current["authority_hash"],
        expected_definition_snapshot_hash=DEFINITION_HASH,
        observed_at=observed_at,
    )
    envelope = json.loads(receipt)
    return receipt, envelope["receipt_hash"], current


def _pins(receipt_hash: str, payload: dict, *, observed_at: str = OBSERVED) -> dict:
    return {
        "expected_receipt_hash": receipt_hash,
        "expected_authority_hash": payload["authority_hash"],
        "expected_definition_snapshot_hash": DEFINITION_HASH,
        "observed_at": observed_at,
    }


def test_receipt_is_deterministic_exact_bytes_and_candidate_has_no_authority_surface() -> None:
    payload = _candidate()
    first, first_hash, _ = _receipt(payload)
    second, second_hash, _ = _receipt(deepcopy(payload))

    assert type(first) is bytes
    assert first == second
    assert first_hash == second_hash
    envelope = json.loads(first)
    assert envelope["schema_id"] == EXECUTION_TRUST_AUTHORITY_RECEIPT_SCHEMA_ID
    assert envelope["authority_payload"] == payload
    assert not hasattr(trust_module, "ExecutionTrustAuthority")
    assert not any(callable(value) for value in payload.values())


def test_constructor_object_new_state_swap_and_method_override_have_no_authorizing_object() -> None:
    receipt, receipt_hash, payload = _receipt()

    class ForgedResolver:
        def resolve_execution_capability_refs(self, *_args, **_kwargs):
            return ("shell-admin",)

    class BytesAlias(bytes):
        pass

    for forged in (ForgedResolver(), BytesAlias(receipt), bytearray(receipt), memoryview(receipt)):
        with pytest.raises(ExecutionTrustAuthorityError) as raised:
            resolve_execution_capability_refs(
                forged,  # type: ignore[arg-type]
                ["repo-read"],
                **_pins(receipt_hash, payload),
            )
        assert raised.value.code == "invalid_receipt_bytes"

    with pytest.raises((AttributeError, TypeError)):
        object.__setattr__(receipt, "authority", "forged")


def test_independence_resolution_does_not_dispatch_through_patchable_public_resolvers(
    monkeypatch,
) -> None:
    receipt, receipt_hash, payload = _receipt()
    monkeypatch.setattr(
        trust_module,
        "resolve_execution_principal",
        lambda *_args, **_kwargs: {
            "principal_id": "forged",
            "subject_digest": "sha256:" + ("f" * 64),
            "role": "independent_qa",
        },
    )
    monkeypatch.setattr(
        trust_module,
        "resolve_execution_verification_policy",
        lambda *_args, **_kwargs: {
            "verification_rule_id": "forged",
            "required_reviewer_role": "independent_qa",
        },
    )
    reviewer = resolve_execution_independent_reviewer(
        receipt,
        owner_principal_id="owner:alice",
        reviewer_principal_id="reviewer:bob",
        verification_rule_id="rule-static",
        **_pins(receipt_hash, payload),
    )
    assert reviewer["principal_id"] == "reviewer:bob"


def test_all_resolvers_revalidate_external_pins_and_current_observation() -> None:
    receipt, receipt_hash, payload = _receipt()
    pins = _pins(receipt_hash, payload)

    assert resolve_execution_capability_refs(receipt, ["repo-read", "repo-write"], **pins) == (
        "repo-read",
        "repo-write",
    )
    assert resolve_execution_gate_ids(receipt, ["gate-static"], **pins) == ("gate-static",)
    assert resolve_execution_verification_policy(receipt, "rule-static", **pins) == {
        "verification_rule_id": "rule-static",
        "required_reviewer_role": "independent_qa",
    }
    assert resolve_execution_principal(receipt, "owner:alice", required_role="claim_owner", **pins)["role"] == "claim_owner"
    assert resolve_execution_independent_reviewer(
        receipt,
        owner_principal_id="owner:alice",
        reviewer_principal_id="reviewer:bob",
        verification_rule_id="rule-static",
        **pins,
    )["principal_id"] == "reviewer:bob"
    assert execution_trust_authority_reference(receipt, **pins)["authority_hash"] == payload["authority_hash"]

    for bad_key, bad_value, code in [
        ("expected_receipt_hash", "sha256:" + ("f" * 64), "untrusted_receipt"),
        ("expected_authority_hash", "sha256:" + ("f" * 64), "untrusted_authority"),
        ("expected_definition_snapshot_hash", FOREIGN_DEFINITION_HASH, "foreign_definition"),
    ]:
        bad = dict(pins)
        bad[bad_key] = bad_value
        with pytest.raises(ExecutionTrustAuthorityError) as raised:
            resolve_execution_capability_refs(receipt, ["repo-read"], **bad)
        assert raised.value.code == code


def test_missing_none_and_scalar_subclass_trust_inputs_fail_closed() -> None:
    receipt, receipt_hash, payload = _receipt()

    class HostileStr(str):
        def __eq__(self, other):
            raise AssertionError("operator must not escape")

    for key, value, code in [
        ("expected_receipt_hash", None, "invalid_hash"),
        ("expected_authority_hash", HostileStr(payload["authority_hash"]), "invalid_hash"),
        ("expected_definition_snapshot_hash", None, "invalid_hash"),
        ("observed_at", HostileStr(OBSERVED), "invalid_timestamp"),
    ]:
        pins = _pins(receipt_hash, payload)
        pins[key] = value
        with pytest.raises(ExecutionTrustAuthorityError) as raised:
            resolve_execution_gate_ids(receipt, ["gate-static"], **pins)
        assert raised.value.code == code
    with pytest.raises(TypeError):
        resolve_execution_gate_ids(
            receipt,
            ["gate-static"],
            expected_receipt_hash=receipt_hash,
            expected_authority_hash=payload["authority_hash"],
            expected_definition_snapshot_hash=DEFINITION_HASH,
        )


def test_self_rehashed_authority_and_receipt_fail_retained_external_pins() -> None:
    receipt, receipt_hash, payload = _receipt()
    forged_authority = deepcopy(payload)
    forged_authority["capability_catalog"].append("shell-admin")
    forged_authority["capability_catalog"].sort()
    _rehash(forged_authority)
    with pytest.raises(ExecutionTrustAuthorityError) as authority:
        validate_execution_trust_authority(
            forged_authority,
            expected_authority_hash=payload["authority_hash"],
            expected_definition_snapshot_hash=DEFINITION_HASH,
            observed_at=OBSERVED,
        )
    assert authority.value.code == "untrusted_authority"

    forged_receipt = json.loads(receipt)
    forged_receipt["validated_observed_at"] = "2026-08-03T14:00:00+02:00"
    _rehash(forged_receipt, "receipt_hash")
    forged_bytes = json.dumps(forged_receipt, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    with pytest.raises(ExecutionTrustAuthorityError) as receipt_error:
        resolve_execution_capability_refs(
            forged_bytes,
            ["repo-read"],
            **_pins(receipt_hash, payload),
        )
    assert receipt_error.value.code == "untrusted_receipt"


def test_temporal_start_expiry_stale_rollback_equivalent_offset_and_reviewer_expiry() -> None:
    payload = _candidate()
    receipt, receipt_hash, _ = _receipt(payload, observed_at="2026-08-03T12:00:00+02:00")
    assert resolve_execution_capability_refs(
        receipt,
        ["repo-read"],
        **_pins(receipt_hash, payload, observed_at="2026-08-03T10:00:00Z"),
    ) == ("repo-read",)
    with pytest.raises(ExecutionTrustAuthorityError) as stale:
        resolve_execution_capability_refs(
            receipt,
            ["repo-read"],
            **_pins(receipt_hash, payload, observed_at="2026-08-03T11:59:59+02:00"),
        )
    assert stale.value.code == "stale_observation"
    with pytest.raises(ExecutionTrustAuthorityError) as expiry:
        resolve_execution_capability_refs(
            receipt,
            ["repo-read"],
            **_pins(receipt_hash, payload, observed_at="2026-08-03T20:00:00+02:00"),
        )
    assert expiry.value.code == "authority_inactive"
    with pytest.raises(ExecutionTrustAuthorityError) as validate_expiry:
        validate_execution_trust_authority(
            payload,
            expected_authority_hash=payload["authority_hash"],
            expected_definition_snapshot_hash=DEFINITION_HASH,
            observed_at="2026-08-03T20:00:00+02:00",
        )
    assert validate_expiry.value.code == "authority_inactive"

    principals = [
        _principal("owner:alice", "1", "claim_owner"),
        _principal("reviewer:bob", "2", "independent_qa", valid_until="2026-08-03T16:00:00+02:00"),
        _principal("reviewer:sol", "3", "deep_review"),
    ]
    short = _candidate(principals=principals)
    short_receipt, short_hash, _ = _receipt(short)
    with pytest.raises(ExecutionTrustAuthorityError) as reviewer:
        resolve_execution_independent_reviewer(
            short_receipt,
            owner_principal_id="owner:alice",
            reviewer_principal_id="reviewer:bob",
            verification_rule_id="rule-static",
            **_pins(short_hash, short, observed_at="2026-08-03T16:00:00+02:00"),
        )
    assert reviewer.value.code == "principal_inactive"


def test_unknown_catalog_rule_principal_and_wrong_role_fail() -> None:
    receipt, receipt_hash, payload = _receipt()
    pins = _pins(receipt_hash, payload)
    actions = [
        (lambda: resolve_execution_capability_refs(receipt, ["shell-admin"], **pins), "unknown_capability"),
        (lambda: resolve_execution_gate_ids(receipt, ["gate-live"], **pins), "unknown_gate"),
        (lambda: resolve_execution_verification_policy(receipt, "rule-unknown", **pins), "unknown_verification_rule"),
        (lambda: resolve_execution_principal(receipt, "reviewer:unknown", **pins), "unknown_principal"),
        (lambda: resolve_execution_principal(receipt, "reviewer:bob", required_role="deep_review", **pins), "principal_role_mismatch"),
    ]
    for action, code in actions:
        with pytest.raises(ExecutionTrustAuthorityError) as raised:
            action()
        assert raised.value.code == code


def test_same_subject_alias_cannot_manufacture_independence() -> None:
    payload = _candidate(
        principals=[
            _principal("owner:alice", "1", "claim_owner"),
            _principal("reviewer:alias", "1", "independent_qa"),
            _principal("reviewer:bob", "2", "independent_qa"),
            _principal("reviewer:sol", "3", "deep_review"),
        ]
    )
    receipt, receipt_hash, _ = _receipt(payload)
    with pytest.raises(ExecutionTrustAuthorityError) as raised:
        resolve_execution_independent_reviewer(
            receipt,
            owner_principal_id="owner:alice",
            reviewer_principal_id="reviewer:alias",
            verification_rule_id="rule-static",
            **_pins(receipt_hash, payload),
        )
    assert raised.value.code == "reviewer_not_independent"


@pytest.mark.parametrize(
    ("principals", "code"),
    [
        (
            [
                _principal("Owner:Alice", "1", "claim_owner"),
                _principal("owner:alice", "2", "claim_owner"),
                _principal("reviewer:bob", "3", "independent_qa"),
                _principal("reviewer:sol", "4", "deep_review"),
            ],
            "duplicate_principal_identity",
        ),
        (
            [
                _principal("owner:alice", "1", "claim_owner"),
                _principal("reviewer:bob-deep", "2", "deep_review"),
                _principal("reviewer:bob-qa", "2", "independent_qa"),
            ],
            "principal_role_conflict",
        ),
    ],
)
def test_case_alias_and_subject_role_collision_fail(principals: list[dict], code: str) -> None:
    principals.sort(key=lambda item: item["principal_id"])
    with pytest.raises(ExecutionTrustAuthorityError) as raised:
        _candidate(principals=principals)
    assert raised.value.code == code


def test_verifier_collision_nfc_alias_and_duplicate_catalogs_fail() -> None:
    principals = [
        _principal("owner:alice", "1", "claim_owner"),
        _principal("reviewer:bob", "2", "independent_qa"),
        _principal("reviewer:sol", "3", "deep_review"),
    ]
    principals[1]["verifier_binding_digest"] = principals[0]["verifier_binding_digest"]
    with pytest.raises(ExecutionTrustAuthorityError) as verifier:
        _candidate(principals=principals)
    assert verifier.value.code == "verifier_binding_conflict"
    with pytest.raises(ExecutionTrustAuthorityError) as nfc:
        _candidate(capabilities=["repo-read", "re\u0301po"])
    assert nfc.value.code == "invalid_identifier"
    with pytest.raises(ExecutionTrustAuthorityError) as duplicate:
        _candidate(gates=["gate-static", "gate-static"])
    assert duplicate.value.code == "non_canonical_value"


@pytest.mark.parametrize(
    ("field", "values"),
    [
        ("capabilities", [f"cap-{index:04d}" for index in range(MAX_CAPABILITY_REFS + 1)]),
        ("gates", [f"gate-{index:04d}" for index in range(MAX_GATE_IDS + 1)]),
        (
            "policies",
            [
                {"verification_rule_id": f"rule-{index:04d}", "required_reviewer_role": "independent_qa"}
                for index in range(MAX_VERIFICATION_POLICIES + 1)
            ],
        ),
        (
            "principals",
            [_principal(f"owner-{index:04d}", f"{index % 10}", "claim_owner") for index in range(MAX_PRINCIPALS + 1)],
        ),
    ],
)
def test_collection_budget_plus_one_fails(field: str, values: list) -> None:
    with pytest.raises(ExecutionTrustAuthorityError) as raised:
        _candidate(**{field: values})
    assert raised.value.code == "authority_budget_exceeded"


def test_revision_identifier_global_byte_depth_node_and_item_plus_one_fail() -> None:
    with pytest.raises(ExecutionTrustAuthorityError) as revision:
        _candidate(authority_revision=MAX_AUTHORITY_REVISION + 1)
    assert revision.value.code == "invalid_integer"
    with pytest.raises(ExecutionTrustAuthorityError) as identifier:
        build_execution_trust_authority_payload(
            authority_id="a" * 193,
            authority_revision=1,
            definition_snapshot_hash=DEFINITION_HASH,
            capability_catalog=["a"],
            gate_catalog=["a"],
            verification_policies=[{"verification_rule_id": "a", "required_reviewer_role": "independent_qa"}],
            principals=[_principal("a", "1", "claim_owner"), _principal("b", "2", "independent_qa")],
            attestation_authority_ref={"schema_id": ATTESTATION_AUTHORITY_REF_SCHEMA_ID, "authority_id": "a", "authority_revision": 1, "verifier_digest": VERIFIER_HASH},
            valid_from="2026-08-03T12:00:00+02:00",
            valid_until="2026-08-03T20:00:00+02:00",
        )
    assert identifier.value.code == "invalid_identifier"

    for replacement in (
        "x" * (MAX_AUTHORITY_BYTES + 1),
        [[[[[[[[[0]]]]]]]]],
        [[0] * 8 for _ in range(512)],
        list(range(513)),
    ):
        payload = _candidate()
        payload["attestation_authority_ref"] = {"x": replacement}
        with pytest.raises(ExecutionTrustAuthorityError) as raised:
            validate_execution_trust_authority(
                payload,
                expected_authority_hash=payload["authority_hash"],
                expected_definition_snapshot_hash=DEFINITION_HASH,
                observed_at=OBSERVED,
            )
        assert raised.value.code == "authority_budget_exceeded"


def test_boundary_sized_catalogs_policies_and_principals_remain_valid() -> None:
    principals = [_principal(f"owner-{index:04d}", f"{index % 10}", "claim_owner") for index in range(MAX_PRINCIPALS - 1)]
    principals.append(_principal("reviewer-9999", "f", "independent_qa"))
    principals.sort(key=lambda item: item["principal_id"])
    payload = _candidate(
        capabilities=[f"cap-{index:04d}" for index in range(MAX_CAPABILITY_REFS)],
        gates=[f"gate-{index:04d}" for index in range(MAX_GATE_IDS)],
        policies=[{"verification_rule_id": f"rule-{index:04d}", "required_reviewer_role": "independent_qa"} for index in range(MAX_VERIFICATION_POLICIES)],
        principals=principals,
    )
    receipt, receipt_hash, _ = _receipt(payload)
    assert len(resolve_execution_capability_refs(receipt, payload["capability_catalog"], **_pins(receipt_hash, payload))) == MAX_CAPABILITY_REFS


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_json_boundary_rejects_nonfinite_values_anywhere(token: str) -> None:
    payload = _candidate()
    encoded = json.dumps(payload, sort_keys=True).encode()
    poisoned = encoded[:-1] + b',"unused_numeric":' + token.encode() + b"}"
    with pytest.raises(ExecutionTrustAuthorityError) as raised:
        validate_execution_trust_authority_json(
            poisoned,
            expected_authority_hash=payload["authority_hash"],
            expected_definition_snapshot_hash=DEFINITION_HASH,
            observed_at=OBSERVED,
        )
    assert raised.value.code == "invalid_json_bytes"


def test_duplicate_json_noncanonical_receipt_and_oversized_json_fail() -> None:
    with pytest.raises(ExecutionTrustAuthorityError) as duplicate:
        validate_execution_trust_authority_json(
            b'{"schema_id":"one","schema_id":"two"}',
            expected_authority_hash="sha256:" + ("0" * 64),
            expected_definition_snapshot_hash=DEFINITION_HASH,
            observed_at=OBSERVED,
        )
    assert duplicate.value.code == "duplicate_json_key"
    with pytest.raises(ExecutionTrustAuthorityError) as oversized:
        validate_execution_trust_authority_json(
            b" " * (MAX_AUTHORITY_BYTES + 1),
            expected_authority_hash="sha256:" + ("0" * 64),
            expected_definition_snapshot_hash=DEFINITION_HASH,
            observed_at=OBSERVED,
        )
    assert oversized.value.code == "invalid_json_bytes"

    receipt, receipt_hash, payload = _receipt()
    with pytest.raises(ExecutionTrustAuthorityError) as noncanonical:
        resolve_execution_gate_ids(
            b" " + receipt,
            ["gate-static"],
            **_pins(receipt_hash, payload),
        )
    assert noncanonical.value.code == "non_canonical_receipt"


class _HostileMapping(Mapping):
    def __iter__(self):
        raise AssertionError("hostile iterator must not escape")

    def __len__(self):
        raise AssertionError("hostile length must not escape")

    def __getitem__(self, key):
        raise AssertionError("hostile lookup must not escape")

    def items(self):
        raise AssertionError("hostile items must not escape")


class _HostileSequence(Sequence):
    def __len__(self):
        raise AssertionError("hostile length must not escape")

    def __getitem__(self, index):
        raise AssertionError("hostile lookup must not escape")


def test_hostile_mapping_sequence_and_mutation_isolation() -> None:
    with pytest.raises(ExecutionTrustAuthorityError) as mapping:
        validate_execution_trust_authority(
            _HostileMapping(),
            expected_authority_hash="sha256:" + ("0" * 64),
            expected_definition_snapshot_hash=DEFINITION_HASH,
            observed_at=OBSERVED,
        )
    assert mapping.value.code == "capture_failed"

    receipt, receipt_hash, payload = _receipt()
    with pytest.raises(ExecutionTrustAuthorityError) as sequence:
        resolve_execution_capability_refs(receipt, _HostileSequence(), **_pins(receipt_hash, payload))
    assert sequence.value.code == "capture_failed"

    original = bytes(receipt)
    payload["capability_catalog"].append("shell-admin")
    assert receipt == original
    assert resolve_execution_capability_refs(receipt, ["repo-read"], **_pins(receipt_hash, json.loads(receipt)["authority_payload"])) == ("repo-read",)


def test_no_auth_signature_provider_persistence_network_dispatch_or_live_surface() -> None:
    source = trust_module.__file__
    text = open(source, encoding="utf-8").read()
    forbidden_imports = ("requests", "socket", "subprocess", "sqlalchemy", "pathlib", "os")
    assert all(f"import {name}" not in text for name in forbidden_imports)
    assert "open(" not in text
    assert "dispatch(" not in text


def test_r3_public_api_schema_fields_budgets_and_exports_are_exact() -> None:
    assert trust_module.__all__ == [
        "ATTESTATION_AUTHORITY_REF_SCHEMA_ID",
        "EXECUTION_TRUST_AUTHORITY_RECEIPT_SCHEMA_ID",
        "EXECUTION_TRUST_AUTHORITY_REF_SCHEMA_ID",
        "EXECUTION_TRUST_AUTHORITY_SCHEMA_ID",
        "MAX_AUTHORITY_BYTES",
        "MAX_AUTHORITY_DEPTH",
        "MAX_AUTHORITY_NODES",
        "MAX_AUTHORITY_REVISION",
        "MAX_CAPABILITY_REFS",
        "MAX_GATE_IDS",
        "MAX_PRINCIPALS",
        "MAX_RECEIPT_BYTES",
        "MAX_VERIFICATION_POLICIES",
        "ExecutionTrustAuthorityError",
        "build_execution_trust_authority_payload",
        "execution_trust_authority_reference",
        "resolve_execution_capability_refs",
        "resolve_execution_gate_ids",
        "resolve_execution_independent_reviewer",
        "resolve_execution_principal",
        "resolve_execution_verification_policy",
        "validate_execution_trust_authority",
        "validate_execution_trust_authority_json",
    ]
    assert trust_module.EXECUTION_TRUST_AUTHORITY_SCHEMA_ID == "odysseus.execution_trust_authority.v1"
    assert trust_module.EXECUTION_TRUST_AUTHORITY_REF_SCHEMA_ID == "odysseus.execution_trust_authority_ref.v1"
    assert trust_module.EXECUTION_TRUST_AUTHORITY_RECEIPT_SCHEMA_ID == "odysseus.execution_trust_authority_receipt.v1"
    assert trust_module.ATTESTATION_AUTHORITY_REF_SCHEMA_ID == "odysseus.reviewer_attestation_authority_ref.v1"
    assert (
        trust_module.MAX_AUTHORITY_BYTES,
        trust_module.MAX_AUTHORITY_DEPTH,
        trust_module.MAX_AUTHORITY_NODES,
        trust_module.MAX_AUTHORITY_REVISION,
        trust_module.MAX_CAPABILITY_REFS,
        trust_module.MAX_GATE_IDS,
        trust_module.MAX_PRINCIPALS,
        trust_module.MAX_RECEIPT_BYTES,
        trust_module.MAX_VERIFICATION_POLICIES,
    ) == (256_000, 8, 4_096, 2_147_483_647, 256, 256, 128, 257_024, 256)

    payload = _candidate()
    receipt, receipt_hash, _ = _receipt(payload)
    reference = execution_trust_authority_reference(receipt, **_pins(receipt_hash, payload))
    assert set(payload) == {
        "schema_id",
        "authority_id",
        "authority_revision",
        "definition_snapshot_hash",
        "capability_catalog",
        "gate_catalog",
        "verification_policies",
        "principals",
        "attestation_authority_ref",
        "valid_from",
        "valid_until",
        "authority_hash",
    }
    assert set(payload["attestation_authority_ref"]) == {
        "schema_id",
        "authority_id",
        "authority_revision",
        "verifier_digest",
    }
    assert all(set(item) == {"verification_rule_id", "required_reviewer_role"} for item in payload["verification_policies"])
    assert all(
        set(item)
        == {
            "principal_id",
            "subject_digest",
            "role",
            "verifier_binding_digest",
            "valid_from",
            "valid_until",
        }
        for item in payload["principals"]
    )
    assert set(json.loads(receipt)) == {
        "schema_id",
        "authority_payload",
        "authority_hash",
        "definition_snapshot_hash",
        "validated_observed_at",
        "receipt_hash",
    }
    assert set(reference) == {
        "schema_id",
        "authority_id",
        "authority_revision",
        "definition_snapshot_hash",
        "authority_hash",
    }


def test_r3_exact_builtin_capture_rejects_protocol_and_scalar_subclasses_without_callbacks() -> None:
    callbacks: list[str] = []

    class HostileMapping(Mapping):
        @property
        def __class__(self):
            callbacks.append("mapping.__class__")
            raise AssertionError("mapping callback text")

        def __iter__(self):
            callbacks.append("mapping.__iter__")
            raise AssertionError("mapping callback text")

        def __len__(self):
            callbacks.append("mapping.__len__")
            raise AssertionError("mapping callback text")

        def __getitem__(self, key):
            callbacks.append("mapping.__getitem__")
            raise AssertionError("mapping callback text")

        def items(self):
            callbacks.append("mapping.items")
            raise AssertionError("mapping callback text")

    class HostileSequence(Sequence):
        @property
        def __class__(self):
            callbacks.append("sequence.__class__")
            raise AssertionError("sequence callback text")

        def __len__(self):
            callbacks.append("sequence.__len__")
            raise AssertionError("sequence callback text")

        def __getitem__(self, index):
            callbacks.append("sequence.__getitem__")
            raise AssertionError("sequence callback text")

    class HostileDict(dict):
        def items(self):
            callbacks.append("dict.items")
            raise AssertionError("dict callback text")

    class HostileList(list):
        def __iter__(self):
            callbacks.append("list.__iter__")
            raise AssertionError("list callback text")

    class HostileTuple(tuple):
        def __iter__(self):
            callbacks.append("tuple.__iter__")
            raise AssertionError("tuple callback text")

    class HostileStr(str):
        def encode(self, *args, **kwargs):
            callbacks.append("str.encode")
            raise AssertionError("str callback text")

    class HostileInt(int):
        def bit_length(self):
            callbacks.append("int.bit_length")
            raise AssertionError("int callback text")

    receipt, receipt_hash, payload = _receipt()
    operations = [
        lambda: validate_execution_trust_authority(
            HostileMapping(),
            expected_authority_hash=payload["authority_hash"],
            expected_definition_snapshot_hash=DEFINITION_HASH,
            observed_at=OBSERVED,
        ),
        lambda: resolve_execution_capability_refs(receipt, HostileSequence(), **_pins(receipt_hash, payload)),
        lambda: validate_execution_trust_authority(
            HostileDict(payload),
            expected_authority_hash=payload["authority_hash"],
            expected_definition_snapshot_hash=DEFINITION_HASH,
            observed_at=OBSERVED,
        ),
        lambda: resolve_execution_gate_ids(receipt, HostileList(["gate-static"]), **_pins(receipt_hash, payload)),
        lambda: resolve_execution_gate_ids(receipt, HostileTuple(("gate-static",)), **_pins(receipt_hash, payload)),
        lambda: build_execution_trust_authority_payload(
            authority_id=HostileStr("authority:round7"),
            authority_revision=1,
            definition_snapshot_hash=DEFINITION_HASH,
            capability_catalog=["repo-read"],
            gate_catalog=["gate-static"],
            verification_policies=[{"verification_rule_id": "rule-static", "required_reviewer_role": "independent_qa"}],
            principals=[_principal("owner:alice", "1", "claim_owner"), _principal("reviewer:bob", "2", "independent_qa")],
            attestation_authority_ref={"schema_id": ATTESTATION_AUTHORITY_REF_SCHEMA_ID, "authority_id": "attestation:local", "authority_revision": 1, "verifier_digest": VERIFIER_HASH},
            valid_from="2026-08-03T12:00:00+02:00",
            valid_until="2026-08-03T20:00:00+02:00",
        ),
        lambda: build_execution_trust_authority_payload(
            authority_id="authority:round7",
            authority_revision=HostileInt(1),
            definition_snapshot_hash=DEFINITION_HASH,
            capability_catalog=["repo-read"],
            gate_catalog=["gate-static"],
            verification_policies=[{"verification_rule_id": "rule-static", "required_reviewer_role": "independent_qa"}],
            principals=[_principal("owner:alice", "1", "claim_owner"), _principal("reviewer:bob", "2", "independent_qa")],
            attestation_authority_ref={"schema_id": ATTESTATION_AUTHORITY_REF_SCHEMA_ID, "authority_id": "attestation:local", "authority_revision": 1, "verifier_digest": VERIFIER_HASH},
            valid_from="2026-08-03T12:00:00+02:00",
            valid_until="2026-08-03T20:00:00+02:00",
        ),
    ]
    for operation in operations:
        with pytest.raises(ExecutionTrustAuthorityError) as raised:
            operation()
        assert raised.value.code == "capture_failed"
    assert callbacks == []


def test_r3_hostile_class_property_fails_closed_at_every_container_boundary() -> None:
    callbacks: list[str] = []

    class HostileClassProperty:
        @property
        def __class__(self):
            callbacks.append("__class__")
            raise AssertionError("hostile class callback text")

    hostile = HostileClassProperty()
    receipt, receipt_hash, payload = _receipt()

    def build_with(**replacement):
        values = {
            "authority_id": "authority:round7",
            "authority_revision": 1,
            "definition_snapshot_hash": DEFINITION_HASH,
            "capability_catalog": ["repo-read"],
            "gate_catalog": ["gate-static"],
            "verification_policies": [{"verification_rule_id": "rule-static", "required_reviewer_role": "independent_qa"}],
            "principals": [_principal("owner:alice", "1", "claim_owner"), _principal("reviewer:bob", "2", "independent_qa")],
            "attestation_authority_ref": {"schema_id": ATTESTATION_AUTHORITY_REF_SCHEMA_ID, "authority_id": "attestation:local", "authority_revision": 1, "verifier_digest": VERIFIER_HASH},
            "valid_from": "2026-08-03T12:00:00+02:00",
            "valid_until": "2026-08-03T20:00:00+02:00",
        }
        values.update(replacement)
        return build_execution_trust_authority_payload(**values)

    operations = [
        lambda: validate_execution_trust_authority(hostile, expected_authority_hash=payload["authority_hash"], expected_definition_snapshot_hash=DEFINITION_HASH, observed_at=OBSERVED),
        lambda: build_with(capability_catalog=hostile),
        lambda: build_with(gate_catalog=hostile),
        lambda: build_with(verification_policies=hostile),
        lambda: build_with(verification_policies=[hostile]),
        lambda: build_with(principals=hostile),
        lambda: build_with(principals=[hostile]),
        lambda: build_with(attestation_authority_ref=hostile),
        lambda: resolve_execution_capability_refs(receipt, hostile, **_pins(receipt_hash, payload)),
        lambda: resolve_execution_gate_ids(receipt, hostile, **_pins(receipt_hash, payload)),
    ]
    for operation in operations:
        with pytest.raises(ExecutionTrustAuthorityError) as raised:
            operation()
        assert raised.value.code == "capture_failed"
        assert raised.value.path.startswith("$")
    assert callbacks == []


def test_r3_public_errors_are_fresh_bounded_content_free_and_suppress_context() -> None:
    receipt, receipt_hash, payload = _receipt()
    invalid_build = {
        "authority_id": object(),
        "authority_revision": 1,
        "definition_snapshot_hash": DEFINITION_HASH,
        "capability_catalog": ["repo-read"],
        "gate_catalog": ["gate-static"],
        "verification_policies": [{"verification_rule_id": "rule-static", "required_reviewer_role": "independent_qa"}],
        "principals": [_principal("owner:alice", "1", "claim_owner"), _principal("reviewer:bob", "2", "independent_qa")],
        "attestation_authority_ref": {"schema_id": ATTESTATION_AUTHORITY_REF_SCHEMA_ID, "authority_id": "attestation:local", "authority_revision": 1, "verifier_digest": VERIFIER_HASH},
        "valid_from": "2026-08-03T12:00:00+02:00",
        "valid_until": "2026-08-03T20:00:00+02:00",
    }
    operations = [
        lambda: build_execution_trust_authority_payload(**invalid_build),
        lambda: validate_execution_trust_authority(object(), expected_authority_hash=payload["authority_hash"], expected_definition_snapshot_hash=DEFINITION_HASH, observed_at=OBSERVED),
        lambda: validate_execution_trust_authority_json(b"", expected_authority_hash=payload["authority_hash"], expected_definition_snapshot_hash=DEFINITION_HASH, observed_at=OBSERVED),
        lambda: execution_trust_authority_reference(b"", **_pins(receipt_hash, payload)),
        lambda: resolve_execution_capability_refs(receipt, object(), **_pins(receipt_hash, payload)),
        lambda: resolve_execution_gate_ids(receipt, object(), **_pins(receipt_hash, payload)),
        lambda: resolve_execution_verification_policy(receipt, object(), **_pins(receipt_hash, payload)),
        lambda: resolve_execution_principal(receipt, object(), **_pins(receipt_hash, payload)),
        lambda: resolve_execution_independent_reviewer(receipt, owner_principal_id=object(), reviewer_principal_id="reviewer:bob", verification_rule_id="rule-static", **_pins(receipt_hash, payload)),
    ]
    for operation in operations:
        captured = []
        for _ in range(2):
            with pytest.raises(ExecutionTrustAuthorityError) as raised:
                operation()
            captured.append(raised.value)
        assert captured[0] is not captured[1]
        for error in captured:
            assert type(error) is ExecutionTrustAuthorityError
            assert type(error.code) is str and error.code in {
                "capture_failed",
                "invalid_identifier",
                "invalid_json_bytes",
                "invalid_receipt_bytes",
            }
            assert type(error.path) is str and error.path.startswith("$") and len(error.path) <= 256
            assert type(error.detail) is str and len(error.detail) <= 256
            assert "callback text" not in error.detail
            assert error.__cause__ is None
            assert error.__context__ is None
            assert error.__suppress_context__ is True

    marker = "UNTRUSTED-DICTIONARY-KEY-MUST-NOT-LEAK"
    for key in (marker, "k" * 300):
        with pytest.raises(ExecutionTrustAuthorityError) as raised:
            validate_execution_trust_authority(
                {key: object()},
                expected_authority_hash=payload["authority_hash"],
                expected_definition_snapshot_hash=DEFINITION_HASH,
                observed_at=OBSERVED,
            )
        error = raised.value
        assert error.code == "capture_failed"
        assert marker not in error.path
        assert marker not in error.detail
        assert marker not in str(error)
        assert key not in error.path
        assert len(error.path) <= 256
        assert len(error.detail) <= 256
        assert error.__cause__ is None
        assert error.__context__ is None
        assert error.__suppress_context__ is True


def test_r3_capture_is_single_read_detached_and_mutation_safe() -> None:
    capabilities = ["repo-read", "repo-write"]
    gates = ["gate-static", "gate-ui"]
    policies = [
        {"verification_rule_id": "rule-deep", "required_reviewer_role": "deep_review"},
        {"verification_rule_id": "rule-static", "required_reviewer_role": "independent_qa"},
    ]
    principals = [
        _principal("owner:alice", "1", "claim_owner"),
        _principal("reviewer:bob", "2", "independent_qa"),
        _principal("reviewer:sol", "3", "deep_review"),
    ]
    attestation = {"schema_id": ATTESTATION_AUTHORITY_REF_SCHEMA_ID, "authority_id": "attestation:local", "authority_revision": 1, "verifier_digest": VERIFIER_HASH}
    payload = build_execution_trust_authority_payload(
        authority_id="authority:round7",
        authority_revision=1,
        definition_snapshot_hash=DEFINITION_HASH,
        capability_catalog=capabilities,
        gate_catalog=gates,
        verification_policies=policies,
        principals=principals,
        attestation_authority_ref=attestation,
        valid_from="2026-08-03T12:00:00+02:00",
        valid_until="2026-08-03T20:00:00+02:00",
    )
    frozen_payload = deepcopy(payload)
    capabilities[:] = ["shell-admin"]
    gates[:] = ["gate-live"]
    policies[0]["required_reviewer_role"] = "independent_qa"
    principals[0]["subject_digest"] = "sha256:" + ("f" * 64)
    attestation["authority_id"] = "attestation:changed"
    assert payload == frozen_payload

    receipt, receipt_hash, _ = _receipt(payload)
    original_receipt = bytes(receipt)
    payload["capability_catalog"][:] = ["shell-admin"]
    payload["principals"][0]["subject_digest"] = "sha256:" + ("e" * 64)
    assert receipt == original_receipt

    receipt_payload = json.loads(receipt)["authority_payload"]
    values = ["repo-read", "repo-write"]
    resolved = resolve_execution_capability_refs(receipt, values, **_pins(receipt_hash, receipt_payload))
    values[:] = ["shell-admin"]
    assert resolved == ("repo-read", "repo-write")
    principal = resolve_execution_principal(receipt, "reviewer:bob", **_pins(receipt_hash, receipt_payload))
    principal["role"] = "claim_owner"
    assert resolve_execution_principal(receipt, "reviewer:bob", **_pins(receipt_hash, receipt_payload))["role"] == "independent_qa"
    reference = execution_trust_authority_reference(receipt, **_pins(receipt_hash, receipt_payload))
    reference["authority_id"] = "forged"
    assert execution_trust_authority_reference(receipt, **_pins(receipt_hash, receipt_payload))["authority_id"] == "authority:round7"
