from __future__ import annotations

import json

import pytest

from src.secret_safe_diagnostics import (
    DiagnosticContract,
    DiagnosticProjectionStatus,
    DiagnosticRefusalCode,
    diagnostic_source_is_forbidden,
    project_diagnostic,
    project_exception_diagnostic,
    project_registered_diagnostic,
    project_subprocess_diagnostic,
)


CANARY = "synthetic-canary-never-emit"


@pytest.fixture
def contract() -> DiagnosticContract:
    return DiagnosticContract(
        source_id="maintenance_readiness",
        presence_fields=("configured", "reachable"),
        count_fields=("healthy_services", "failed_checks"),
        state_values={"state": ("ok", "degraded", "unavailable")},
        max_count=100,
    )


def test_fixed_key_boolean_count_and_state_projection(contract) -> None:
    result = project_diagnostic(
        contract,
        {
            "configured": True,
            "reachable": False,
            "healthy_services": 3,
            "failed_checks": 1,
            "state": "degraded",
        },
    )

    assert result.status == DiagnosticProjectionStatus.ACCEPTED
    assert result.to_dict()["presence"] == {
        "configured": True,
        "reachable": False,
    }
    assert result.to_dict()["counts"] == {
        "failed_checks": 1,
        "healthy_services": 3,
    }
    assert result.to_dict()["states"] == {"state": "degraded"}


def test_unknown_payload_key_fails_closed_without_echo(contract) -> None:
    result = project_diagnostic(contract, {"configured": True, CANARY: CANARY})
    serialized = result.to_json()

    assert result.refusal_code == DiagnosticRefusalCode.PAYLOAD_NOT_ALLOWLISTED
    assert CANARY not in serialized
    assert result.to_dict()["presence"] == {}


def test_unknown_source_requires_narrower_registered_contract(contract) -> None:
    result = project_registered_diagnostic(
        CANARY,
        {"configured": True},
        registry={contract.source_id: contract},
    )

    assert result.source_id == "unknown"
    assert result.refusal_code == DiagnosticRefusalCode.UNKNOWN_SOURCE
    assert CANARY not in result.to_json()


@pytest.mark.parametrize(
    "command",
    (
        "env",
        "printenv HOME",
        "set",
        "Get-ChildItem Env:",
        "Get-Item Env:API_KEY",
        "$env:API_KEY",
        "Get-Content .env",
        "python -c \"open('.env').read()\"",
        "docker inspect service --format {{json .Config.Env}}",
        "podman inspect service --format {{.Config.Env}}",
        "systemctl show service -p Environment",
        "docker compose config",
        "podman compose config",
    ),
)
def test_raw_secret_bearing_diagnostic_sources_are_rejected(command) -> None:
    assert diagnostic_source_is_forbidden(command) is True


def test_command_refusal_never_echoes_command_or_payload(contract) -> None:
    result = project_diagnostic(
        contract,
        {"configured": CANARY},
        command_source=f"printenv {CANARY}",
    )

    assert result.refusal_code == DiagnosticRefusalCode.RAW_SOURCE_FORBIDDEN
    assert CANARY not in result.to_json()


@pytest.mark.parametrize("value", (-1, 101, True, "3"))
def test_counts_are_integer_and_bounded(contract, value) -> None:
    result = project_diagnostic(contract, {"healthy_services": value})

    assert result.status == DiagnosticProjectionStatus.REFUSED
    assert result.to_dict()["counts"] == {}


def test_count_and_state_contracts_reject_secret_derived_semantics() -> None:
    with pytest.raises(ValueError, match="forbidden semantic"):
        DiagnosticContract(
            source_id="unsafe_contract",
            count_fields=("token_length",),
        )
    with pytest.raises(ValueError, match="forbidden semantic"):
        DiagnosticContract(
            source_id="unsafe_contract",
            state_values={"secret_hash": ("available",)},
        )
    with pytest.raises(ValueError, match="forbidden semantic"):
        DiagnosticContract(
            source_id="unsafe_contract",
            presence_fields=("token_value",),
        )


def test_sensitive_presence_is_only_a_fixed_boolean() -> None:
    contract = DiagnosticContract(
        source_id="credential_readiness",
        presence_fields=("credential_present",),
    )

    accepted = project_diagnostic(contract, {"credential_present": True})
    refused = project_diagnostic(contract, {"credential_present": CANARY})

    assert accepted.to_dict()["presence"] == {"credential_present": True}
    assert refused.refusal_code == DiagnosticRefusalCode.INVALID_SAFE_TYPE
    assert CANARY not in refused.to_json()


def test_non_allowlisted_state_fails_closed_without_echo(contract) -> None:
    result = project_diagnostic(contract, {"state": CANARY})

    assert result.refusal_code == DiagnosticRefusalCode.STATE_NOT_ALLOWLISTED
    assert CANARY not in result.to_json()


def test_exception_message_is_dropped_before_consumer(contract) -> None:
    result = project_exception_diagnostic(
        contract.source_id,
        RuntimeError(CANARY),
        registry={contract.source_id: contract},
    )

    assert result.refusal_code == DiagnosticRefusalCode.DIAGNOSTIC_FAILED
    assert CANARY not in result.to_json()


def test_subprocess_streams_are_dropped_before_success_projection(contract) -> None:
    result = project_subprocess_diagnostic(
        contract.source_id,
        {"configured": True, "healthy_services": 2, "state": "ok"},
        returncode=0,
        registry={contract.source_id: contract},
        command_source="repository-safe-probe --json",
        stdout=CANARY,
        stderr=CANARY,
    )

    assert result.status == DiagnosticProjectionStatus.ACCEPTED
    assert result.to_dict()["presence"] == {"configured": True}
    assert CANARY not in result.to_json()


def test_subprocess_failure_drops_payload_and_streams(contract) -> None:
    result = project_subprocess_diagnostic(
        contract.source_id,
        {"configured": CANARY},
        returncode=1,
        registry={contract.source_id: contract},
        stdout=CANARY,
        stderr=CANARY,
    )

    assert result.refusal_code == DiagnosticRefusalCode.DIAGNOSTIC_FAILED
    assert CANARY not in result.to_json()


def test_unknown_subprocess_and_exception_sources_fail_closed(contract) -> None:
    registry = {contract.source_id: contract}

    subprocess_result = project_subprocess_diagnostic(
        "unregistered_source",
        {},
        returncode=1,
        registry=registry,
        stderr=CANARY,
    )
    exception_result = project_exception_diagnostic(
        "unregistered_source",
        RuntimeError(CANARY),
        registry=registry,
    )

    assert subprocess_result.source_id == "unknown"
    assert subprocess_result.refusal_code == DiagnosticRefusalCode.UNKNOWN_SOURCE
    assert exception_result.source_id == "unknown"
    assert exception_result.refusal_code == DiagnosticRefusalCode.UNKNOWN_SOURCE
    assert CANARY not in subprocess_result.to_json() + exception_result.to_json()


def test_serialized_contract_has_no_raw_output_channel(contract) -> None:
    serialized = project_diagnostic(contract, {"configured": True}).to_json()
    payload = json.loads(serialized)

    assert set(payload) == {
        "schema",
        "source_id",
        "status",
        "presence",
        "counts",
        "states",
        "refusal_code",
    }
    for forbidden in (
        "stdout",
        "stderr",
        "exception",
        "message",
        "prefix",
        "suffix",
        "length",
        "hash",
        "value",
    ):
        assert forbidden not in serialized
