from __future__ import annotations

import json

import pytest

import src.execution_authority_snapshot as snapshot_module
from tests.test_execution_authority_ledger import (
    HASH_A,
    LEASE_AT,
    OBSERVED,
    _acquire,
    _authority_kwargs,
    _definition_ref,
    _ledger,
)


_R3_SNAPSHOT_FUNCTIONS = {
    "bind_compare_only_authority_sources",
    "build_compare_only_authority_snapshot",
    "build_execution_authority_snapshot",
    "validate_compare_only_authority_snapshot",
    "validate_execution_authority_snapshot",
}


def _require_snapshot_r3_api() -> None:
    missing = sorted(name for name in _R3_SNAPSHOT_FUNCTIONS if not hasattr(snapshot_module, name))
    assert not missing, f"missing R3 snapshot API: {missing}"
    assert not hasattr(snapshot_module, "adapt_compare_only_open_work_round7")


def _assert_code(call, code: str) -> None:
    with pytest.raises(snapshot_module.ExecutionAuthoritySnapshotError) as raised:
        call()
    assert raised.value.code == code
    assert raised.value.args == (code,)
    assert raised.value.__cause__ is raised.value.__context__ is None


def test_r3_native_snapshot_is_rebuilt_from_full_ledger_and_requires_byte_equality() -> None:
    _require_snapshot_r3_api()
    ledger, receipt, pins = _ledger()
    ledger = _acquire(ledger, receipt, pins)
    snapshot = snapshot_module.build_execution_authority_snapshot(
        _definition_ref(), ledger, receipt,
        projection_versions={"authority": 1}, observed_at=OBSERVED, **pins,
    )
    assert snapshot_module.validate_execution_authority_snapshot(
        snapshot,
        definition_snapshot_ref=_definition_ref(), ledger_bytes=ledger, trust_receipt=receipt,
        projection_versions={"authority": 1}, observed_at=OBSERVED, **pins,
    ) == snapshot
    forged = json.loads(snapshot)
    forged["dispatch_ready_claim_ids"] = ["claim:forged"]
    _assert_code(
        lambda: snapshot_module.validate_execution_authority_snapshot(
            json.dumps(forged, separators=(",", ":"), sort_keys=True).encode(),
            definition_snapshot_ref=_definition_ref(), ledger_bytes=ledger, trust_receipt=receipt,
            projection_versions={"authority": 1}, observed_at=OBSERVED, **pins,
        ),
        "snapshot_mismatch",
    )


def test_r3_compare_only_binding_hashes_opaque_router_bytes_and_has_no_authority_fields() -> None:
    _require_snapshot_r3_api()
    binding = snapshot_module.bind_compare_only_authority_sources(b"opaque\x00open-work", b"opaque\xffround7")
    payload = json.loads(binding)
    assert set(payload) == {"schema_id", "mode", "open_work_digest", "round7_digest", "binding_hash"}
    assert payload["mode"] == "compare_only"
    snapshot = snapshot_module.build_compare_only_authority_snapshot(
        _definition_ref(), binding, projection_versions={"compare": 1}
    )
    document = json.loads(snapshot)
    assert set(document) == {"schema_id", "definition_snapshot_ref", "source_binding", "projection_versions", "snapshot_hash"}
    forbidden = {"claims", "leases", "gates", "capabilities", "readiness", "counts", "cursor", "head_hash", "authority_hash", "ledger_ref"}
    assert forbidden.isdisjoint(document)
    assert forbidden.isdisjoint(document["source_binding"])


def test_r3_compare_only_binding_rejects_subclasses_overflow_and_digest_substitution() -> None:
    _require_snapshot_r3_api()

    class HostileBytes(bytes):
        pass

    _assert_code(
        lambda: snapshot_module.bind_compare_only_authority_sources(HostileBytes(b"a"), b"b"),
        "invalid_type",
    )
    _assert_code(
        lambda: snapshot_module.bind_compare_only_authority_sources(
            b"a" * (snapshot_module.MAX_COMPARE_SOURCE_BYTES_EACH + 1), b"b"
        ),
        "budget_exceeded",
    )
    original_open = b"open-a"
    round7 = b"round7"
    binding = snapshot_module.bind_compare_only_authority_sources(original_open, round7)
    snapshot = snapshot_module.build_compare_only_authority_snapshot(
        _definition_ref(), binding, projection_versions={"compare": 1}
    )
    _assert_code(
        lambda: snapshot_module.validate_compare_only_authority_snapshot(
            snapshot,
            definition_snapshot_ref=_definition_ref(), open_work_bytes=b"open-b", round7_bytes=round7,
            projection_versions={"compare": 1},
        ),
        "source_binding_mismatch",
    )


def test_r3_snapshot_aggregate_budgets_close_at_max_plus_one() -> None:
    _require_snapshot_r3_api()
    maximum = {f"projection:{index:02d}": 1 for index in range(snapshot_module.MAX_PROJECTION_VERSIONS)}
    binding = snapshot_module.bind_compare_only_authority_sources(
        b"a" * snapshot_module.MAX_COMPARE_SOURCE_BYTES_EACH,
        b"b" * snapshot_module.MAX_COMPARE_SOURCE_BYTES_EACH,
    )
    assert snapshot_module.build_compare_only_authority_snapshot(
        _definition_ref(), binding, projection_versions=maximum
    )
    overflow = dict(maximum)
    overflow["projection:overflow"] = 1
    _assert_code(
        lambda: snapshot_module.build_compare_only_authority_snapshot(
            _definition_ref(), binding, projection_versions=overflow
        ),
        "budget_exceeded",
    )


def test_r3_lease_boundary_fork_stale_trust_and_projection_version_drift_fail_closed() -> None:
    _require_snapshot_r3_api()
    ledger, receipt, pins = _ledger()
    ledger = _acquire(ledger, receipt, pins)
    at_expiry = snapshot_module.build_execution_authority_snapshot(
        _definition_ref(), ledger, receipt,
        projection_versions={"authority": 1}, observed_at=LEASE_AT, **pins,
    )
    assert json.loads(at_expiry)["dispatch_ready_claim_ids"] == []
    _assert_code(
        lambda: snapshot_module.validate_execution_authority_snapshot(
            at_expiry,
            definition_snapshot_ref=_definition_ref(), ledger_bytes=ledger, trust_receipt=receipt,
            projection_versions={"authority": 2}, observed_at=LEASE_AT, **pins,
        ),
        "snapshot_mismatch",
    )
    _assert_code(
        lambda: snapshot_module.build_execution_authority_snapshot(
            _definition_ref(), ledger, receipt,
            projection_versions={"authority": 1},
            observed_at="2026-08-03T12:59:59+02:00", **pins,
        ),
        "stale_authority",
    )
