from __future__ import annotations

import json
from collections.abc import Iterator, Mapping

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
        command_sources=("repository-safe-probe --json",),
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
        "/usr/bin/env",
        "bash -lc env",
        "sh -c 'printenv'",
        "bash -lc \"$(env)\"",
        "set",
        "cmd.exe /c set",
        "cmd.exe /c \"set\"",
        "declare -p",
        "compgen -e",
        "Get-ChildItem Env:",
        "Get-Item Env:API_KEY",
        "powershell -Command Get-Content Env:API_KEY",
        "$env:API_KEY",
        "Get-Content .env",
        "cat .env.production",
        "python -c \"open('.env').read()\"",
        "python -c \"import os; print(os.environ)\"",
        "node -e \"console.log(process.env)\"",
        "cat /proc/self/environ",
        "[System.Environment]::GetEnvironmentVariables()",
        "[Environment]::GetEnvironmentVariable('API_KEY')",
        "docker inspect service --format {{json .Config.Env}}",
        "docker container inspect service",
        "docker --context local inspect service",
        "podman inspect service --format {{.Config.Env}}",
        "systemctl show service -p Environment",
        "systemctl --user show service -p Environment",
        "docker compose config",
        "docker --context local compose config",
        "podman compose config",
    ),
)
def test_raw_secret_bearing_diagnostic_sources_are_rejected(command) -> None:
    assert diagnostic_source_is_forbidden(command) is True


@pytest.mark.parametrize(
    "command",
    (
        "repository-safe-probe --environment-state ok",
        "service-health --setpoint 3",
        "dockerless-probe --json",
    ),
)
def test_safe_command_tokens_do_not_trigger_raw_source_patterns(command) -> None:
    assert diagnostic_source_is_forbidden(command) is False


def test_command_refusal_never_echoes_command_or_payload(contract) -> None:
    result = project_diagnostic(
        contract,
        {"configured": CANARY},
        command_source=f"printenv {CANARY}",
    )

    assert result.refusal_code == DiagnosticRefusalCode.RAW_SOURCE_FORBIDDEN
    assert CANARY not in result.to_json()


def test_only_exact_registered_command_source_is_accepted(contract) -> None:
    accepted = project_diagnostic(
        contract,
        {"configured": True},
        command_source="repository-safe-probe --json",
    )
    refused = project_diagnostic(
        contract,
        {"configured": True},
        command_source=f"repository-safe-probe --json {CANARY}",
    )

    assert accepted.status == DiagnosticProjectionStatus.ACCEPTED
    assert (
        refused.refusal_code
        == DiagnosticRefusalCode.COMMAND_SOURCE_NOT_ALLOWLISTED
    )
    assert CANARY not in refused.to_json()


def test_command_source_contract_is_bounded_and_rejects_raw_sources() -> None:
    with pytest.raises(ValueError, match="forbidden source"):
        DiagnosticContract(
            source_id="unsafe_command",
            command_sources=("docker inspect service",),
        )
    with pytest.raises(ValueError, match="bounded tuple"):
        DiagnosticContract(
            source_id="too_many_commands",
            command_sources=tuple(f"safe-probe-{index}" for index in range(17)),
        )
    with pytest.raises(ValueError, match="invalid source"):
        DiagnosticContract(
            source_id="control_character",
            command_sources=("safe-probe\nprintenv HOME",),
        )


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


class _ExplodingMapping(Mapping[str, object]):
    def __getitem__(self, key: str) -> object:
        raise AssertionError("custom mapping must not be consumed")

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("custom mapping must not be consumed")

    def __len__(self) -> int:
        raise AssertionError("custom mapping must not be consumed")


def test_custom_mapping_is_refused_before_any_user_code_runs(contract) -> None:
    result = project_diagnostic(contract, _ExplodingMapping())

    assert result.refusal_code == DiagnosticRefusalCode.NARROWER_EVIDENCE_REQUIRED


def test_scalar_subclasses_are_not_treated_as_fixed_safe_types(contract) -> None:
    class CustomInt(int):
        pass

    class CustomStr(str):
        pass

    count_result = project_diagnostic(
        contract,
        {"healthy_services": CustomInt(3)},
    )
    state_result = project_diagnostic(
        contract,
        {"state": CustomStr("ok")},
    )

    assert count_result.refusal_code == DiagnosticRefusalCode.INVALID_SAFE_TYPE
    assert state_result.refusal_code == DiagnosticRefusalCode.STATE_NOT_ALLOWLISTED


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
        command_source="repository-safe-probe --json",
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
        command_source="repository-safe-probe --json",
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


@pytest.mark.parametrize(
    "command_source",
    (None, "different-safe-probe --json"),
)
def test_subprocess_requires_a_registered_command_source(
    contract,
    command_source,
) -> None:
    result = project_subprocess_diagnostic(
        contract.source_id,
        {"configured": True},
        returncode=0,
        registry={contract.source_id: contract},
        command_source=command_source,
    )

    assert (
        result.refusal_code
        == DiagnosticRefusalCode.COMMAND_SOURCE_NOT_ALLOWLISTED
    )


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


@pytest.mark.parametrize(
    "tool,content",
    (
        ("bash", f"printenv {CANARY}"),
        ("bash", f"type .env {CANARY}"),
        ("bash", f"docker inspect service-{CANARY}"),
        ("bash", f"docker compose config {CANARY}"),
        ("python", f"import os; print(os.getenv('{CANARY}'))"),
    ),
)
def test_tool_execution_rejects_raw_diagnostic_sources_without_echo(
    tool,
    content,
) -> None:
    from src.tool_execution import _secret_safe_diagnostic_source_refusal

    refusal = _secret_safe_diagnostic_source_refusal(tool, content)
    serialized = json.dumps(refusal, sort_keys=True)

    assert refusal is not None
    assert refusal["diagnostic"]["refusal_code"] == "raw_source_forbidden"
    assert content not in serialized
    assert CANARY not in serialized


def test_tool_execution_allows_non_raw_registered_probe_shape() -> None:
    from src.tool_execution import _secret_safe_diagnostic_source_refusal

    assert (
        _secret_safe_diagnostic_source_refusal(
            "bash",
            "repository-safe-probe --json",
        )
        is None
    )


@pytest.mark.asyncio
async def test_tool_execution_blocks_raw_source_before_dispatch(monkeypatch) -> None:
    from types import SimpleNamespace

    import src.tool_execution as tool_execution

    monkeypatch.setattr(tool_execution, "_owner_is_admin", lambda _owner: True)
    description, result = await tool_execution._execute_tool_block_impl(
        SimpleNamespace(
            tool_type="bash",
            content=f"printenv {CANARY}",
        ),
        owner="synthetic-admin",
    )
    serialized = json.dumps(result, sort_keys=True)

    assert description == "bash: BLOCKED by diagnostic safety policy"
    assert result["exit_code"] == 1
    assert CANARY not in serialized


def test_route_projections_drop_unknown_raw_content() -> None:
    from routes.diagnostics_routes import (
        _project_log_summary,
        _project_research_probe,
        _project_tool_usage_report,
        _project_youtube_probe,
    )

    report = _project_tool_usage_report(
        {
            "schema": "odysseus.tool_usage_analytics.v1",
            "calls": 1,
            "raw_payload": CANARY,
            "quality": {"invocation_count": 1, "raw_payload": CANARY},
            "rows": [
                {
                    "tool_analytics_id": "bash",
                    "invocation_count": 1,
                    "raw_argument": CANARY,
                }
            ],
        }
    )
    payloads = (
        report,
        _project_log_summary(
            status="available",
            log_file_present=True,
            sampled_line_count=1,
        ),
        _project_youtube_probe(
            status="available",
            transcript_present=True,
            transcript_char_count=1,
        ),
        _project_research_probe(
            status="available",
            response_present=True,
            response_char_count=1,
        ),
    )

    assert all(CANARY not in json.dumps(payload, sort_keys=True) for payload in payloads)
    assert report["raw_content_visible"] is False
    assert report["quality"]["raw_content_visible"] is False
    assert set(report["rows"][0]) == {
        "day",
        "tool_analytics_id",
        "tool_family",
        "tool_source",
        "surface",
        "status",
        "invocation_count",
        "duration_count",
        "duration_total_ms",
        "distinct_owner_count",
        "distinct_session_count",
        "retry_count",
        "unknown_identity_count",
    }


def test_diagnostic_routes_never_return_raw_log_or_probe_content(
    monkeypatch,
    tmp_path,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import routes.diagnostics_routes as diagnostics_routes

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "app.log").write_text(CANARY + "\n", encoding="utf-8")

    async def synthetic_transcript(_url, _video_id):
        return {"success": True, "transcript": CANARY}

    class SyntheticResearchHandler:
        async def call_research_service(self, _query, _endpoint, _model):
            return CANARY

    monkeypatch.setattr(diagnostics_routes, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(diagnostics_routes, "require_admin", lambda _request: None)
    monkeypatch.setattr(
        diagnostics_routes,
        "extract_youtube_id",
        lambda _url: "synthetic-video",
    )
    monkeypatch.setattr(
        diagnostics_routes,
        "extract_transcript_async",
        synthetic_transcript,
    )
    app = FastAPI()
    app.include_router(
        diagnostics_routes.setup_diagnostics_routes(
            None,
            False,
            SyntheticResearchHandler(),
        )
    )
    client = TestClient(app)

    responses = (
        client.get("/api/diagnostics/logs"),
        client.get("/api/test/youtube", params={"url": CANARY}),
        client.post("/api/test-research", data={"query": CANARY}),
    )
    serialized = json.dumps([response.json() for response in responses], sort_keys=True)

    assert all(response.status_code == 200 for response in responses)
    assert CANARY not in serialized
    assert '"logs"' not in serialized
    assert "preview" not in serialized
