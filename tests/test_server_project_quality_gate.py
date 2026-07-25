from pathlib import Path

import pytest

from src.server_project_quality_gate import (
    ProjectQualityGateEvidence,
    ProjectQualityGateSpec,
    ServerProjectQualityGateError,
    build_project_quality_gate_evidence,
    build_project_quality_gate_bundle,
)
from src.server_project_registry import ServerProjectRegistry


def _record():
    registry = ServerProjectRegistry()
    return registry.create_project(
        project_title="Kundenportal MVP",
        project_type="app",
        created_at="2026-06-27T10:00:00Z",
    )


def _green_evidence(spec, digest_pair="a1"):
    return build_project_quality_gate_evidence(
        spec=spec,
        state="green",
        result_label="pass",
        checked_at="2026-06-27T10:00:00Z",
        summary=f"verified {spec.gate_type} receipt is green",
        evidence_digest="sha256:" + (digest_pair * 32),
    )


def _default_specs(record):
    return tuple(
        result.spec
        for result in build_project_quality_gate_bundle(record=record).results
    )


def _green_default_evidence(specs):
    return tuple(
        _green_evidence(spec, digest_pair)
        for spec, digest_pair in zip(specs, ("a1", "b2", "c3"), strict=True)
    )


def test_default_project_quality_gates_hold_for_missing_execution_evidence():
    bundle = build_project_quality_gate_bundle(record=_record())

    assert bundle.decision == "hold"
    assert bundle.deploy_gate_ready is False
    assert bundle.focused_tests_green is False
    assert bundle.required_gate_count == 3
    assert bundle.ready_gate_count == 0
    assert bundle.blockers == (
        "focused_tests: structured immutable evidence is missing",
        "build_evidence: structured immutable evidence is missing",
        "smoke_tests: structured immutable evidence is missing",
    )
    assert [result.spec.gate_type for result in bundle.results] == ["test", "build", "smoke"]
    command_results = [
        result for result in bundle.results if result.command_plan is not None
    ]
    evidence_result = bundle.results[1]
    assert all(
        "command_execution" in result.command_plan.blocked_live_actions
        for result in command_results
    )
    assert {
        result.command_plan.command.redacted_log_policy
        for result in command_results
    } == {"command-only-no-secrets"}
    assert evidence_result.command_plan is None
    assert evidence_result.evidence is None
    assert evidence_result.to_dict()["gate_mode"] == "structured_evidence"


def test_green_immutable_gate_evidence_makes_default_bundle_plan_ready():
    record = _record()
    specs = _default_specs(record)
    bundle = build_project_quality_gate_bundle(
        record=record,
        evidence_inputs=_green_default_evidence(specs),
    )

    assert bundle.decision == "plan_ready"
    assert bundle.deploy_gate_ready is True
    assert bundle.focused_tests_green is True
    assert bundle.ready_gate_count == 3
    assert bundle.blockers == ()
    assert bundle.results[1].command_plan is None
    assert bundle.results[1].evidence is not None
    assert bundle.results[1].evidence.ready is True
    assert bundle.results[1].evidence.evidence_digest.startswith("sha256:")


def test_custom_focused_pytest_gate_is_plan_ready():
    spec = ProjectQualityGateSpec.create(
        gate_id="unit",
        gate_type="test",
        command_text="python -m pytest tests/test_server_project_quality_gate.py -q",
        timeout_seconds=120,
    )

    bundle = build_project_quality_gate_bundle(
        record=_record(),
        gate_specs=(spec,),
        evidence_inputs=(
            _green_evidence(spec),
        ),
    )

    assert bundle.decision == "plan_ready"
    assert bundle.results[0].command_plan is not None
    assert bundle.results[0].command_plan.command.command_class == "focused_pytest"


def test_unbounded_or_non_pytest_test_gate_is_blocked():
    spec = ProjectQualityGateSpec.create(
        gate_id="npm_test",
        gate_type="test",
        command_text="npm test",
        timeout_seconds=120,
    )

    bundle = build_project_quality_gate_bundle(record=_record(), gate_specs=(spec,))

    assert bundle.decision == "blocked"
    assert bundle.ready_gate_count == 0
    assert "npm_test" in bundle.blockers[0]


def test_network_host_and_destructive_commands_are_blocked():
    specs = (
        {"gate_id": "network", "gate_type": "test", "command_text": "curl https://example.invalid"},
        {"gate_id": "host", "gate_type": "smoke", "command_text": "podman compose up -d"},
        {"gate_id": "destructive", "gate_type": "test", "command_text": "git reset --hard HEAD"},
    )

    bundle = build_project_quality_gate_bundle(record=_record(), gate_specs=specs)

    assert bundle.decision == "blocked"
    assert bundle.ready_gate_count == 0
    assert {result.command_plan.command.command_class for result in bundle.results} == {
        "blocked_network",
        "blocked_host_command",
        "blocked_destructive",
    }


def test_optional_blocked_gate_does_not_block_required_deploy_gate():
    specs = (
        ProjectQualityGateSpec.create(
            gate_id="unit",
            gate_type="test",
            command_text="python -m pytest tests/test_server_project_quality_gate.py -q",
        ),
        ProjectQualityGateSpec.create(
            gate_id="optional_network",
            gate_type="test",
            command_text="curl https://example.invalid",
            required=False,
        ),
    )

    bundle = build_project_quality_gate_bundle(
        record=_record(),
        gate_specs=specs,
        evidence_inputs=(
            _green_evidence(specs[0]),
        ),
    )

    assert bundle.decision == "plan_ready"
    assert bundle.required_gate_count == 1
    assert bundle.ready_gate_count == 1
    assert bundle.blockers == ()


def test_build_gate_requires_separate_requirement_and_receipt():
    spec = ProjectQualityGateSpec.create(
        gate_id="build",
        gate_type="build",
        evidence_requirement="verified build artifact receipt required",
    )

    bundle = build_project_quality_gate_bundle(
        record=_record(),
        gate_specs=(spec,),
    )

    assert bundle.decision == "hold"
    assert bundle.results[0].command_plan is None
    assert bundle.results[0].evidence is None
    assert "structured immutable evidence is missing" in bundle.blockers[0]

    with pytest.raises(
        ServerProjectQualityGateError,
        match="must not define command_text",
    ):
        ProjectQualityGateSpec.create(
            gate_id="build",
            gate_type="build",
            command_text="git push fuzzy dev",
            evidence_requirement="verified build artifact receipt required",
        )


def test_evidence_inputs_reject_invalid_digest_duplicate_and_wrong_gate():
    record = _record()
    specs = _default_specs(record)
    valid_test = _green_evidence(specs[0])
    valid_build = _green_evidence(specs[1])

    with pytest.raises(ServerProjectQualityGateError, match="evidence_digest"):
        ProjectQualityGateEvidence.create(
            **{
                **valid_build.to_dict(),
                "evidence_digest": "sha256:not-a-digest",
            }
        )

    with pytest.raises(ServerProjectQualityGateError, match="duplicate"):
        build_project_quality_gate_bundle(
            record=record,
            evidence_inputs=(
                valid_build,
                valid_build,
            ),
        )

    with pytest.raises(ServerProjectQualityGateError, match="evidence_kind"):
        build_project_quality_gate_bundle(
            record=record,
            evidence_inputs=(
                ProjectQualityGateEvidence.create(
                    **{
                        **valid_test.to_dict(),
                        "evidence_kind": "build_artifact",
                    }
                ),
            ),
        )

    with pytest.raises(ServerProjectQualityGateError, match="unknown"):
        build_project_quality_gate_bundle(
            record=record,
            evidence_inputs=(
                ProjectQualityGateEvidence.create(
                    **{
                        **valid_build.to_dict(),
                        "gate_id": "unknown_gate",
                        "evidence_kind": "external_evidence",
                    }
                ),
            ),
        )


def test_direct_dataclass_inputs_are_revalidated_before_readiness():
    forged_evidence = ProjectQualityGateEvidence(
        gate_id="build_evidence",
        evidence_kind="build_artifact",
        subject_digest="sha256:" + ("0f" * 32),
        state="green",
        result_label="pass",
        checked_at="not-a-timestamp",
        summary="forged",
        evidence_digest="sha256:not-a-digest",
    )
    forged_spec = ProjectQualityGateSpec(
        gate_id="unit",
        gate_type="test",
        command_text="python -m pytest tests/test_server_project_quality_gate.py -q",
        evidence_requirement=None,
        timeout_seconds=60,
        required="false",  # type: ignore[arg-type]
    )

    with pytest.raises(ServerProjectQualityGateError, match="evidence_digest"):
        build_project_quality_gate_bundle(
            record=_record(),
            evidence_inputs=(forged_evidence,),
        )
    with pytest.raises(ServerProjectQualityGateError, match="required must be a boolean"):
        build_project_quality_gate_bundle(
            record=_record(),
            gate_specs=(forged_spec,),
        )


def test_evidence_receipt_is_bound_to_exact_gate_specification():
    original = ProjectQualityGateSpec.create(
        gate_id="unit",
        gate_type="test",
        command_text="python -m pytest tests/test_server_project_quality_gate.py -q",
    )
    changed = ProjectQualityGateSpec.create(
        gate_id="unit",
        gate_type="test",
        command_text="python -m pytest tests/test_server_project_deploy_handoff.py -q",
    )
    stale_receipt = _green_evidence(original)

    with pytest.raises(ServerProjectQualityGateError, match="subject_digest"):
        build_project_quality_gate_bundle(
            record=_record(),
            gate_specs=(changed,),
            evidence_inputs=(stale_receipt,),
        )


@pytest.mark.parametrize(
    "field,value,error",
    (
        ("timeout_seconds", True, "timeout_seconds must be an integer"),
        ("timeout_seconds", "60", "timeout_seconds must be an integer"),
        ("timeout_seconds", 301, "between 1 and 300"),
        ("required", "false", "required must be a boolean"),
    ),
)
def test_project_gate_types_and_bounds_match_hardened_runner(field, value, error):
    kwargs = {
        "gate_id": "unit",
        "gate_type": "test",
        "command_text": "python -m pytest tests/test_server_project_quality_gate.py -q",
        field: value,
    }

    with pytest.raises(ServerProjectQualityGateError, match=error):
        ProjectQualityGateSpec.create(**kwargs)


def test_rejects_secret_like_or_host_path_gate_text():
    with pytest.raises(ServerProjectQualityGateError, match="secret material"):
        ProjectQualityGateSpec.create(
            gate_id="secret",
            gate_type="test",
            command_text="python -m pytest TOKEN=abc123 tests/test_demo.py",
        )

    with pytest.raises(ServerProjectQualityGateError, match="absolute paths"):
        ProjectQualityGateSpec.create(
            gate_id="path",
            gate_type="test",
            command_text=r"python -m pytest D:\Sensitive\test_demo.py",
        )


def test_bundle_requires_at_least_one_gate():
    with pytest.raises(ServerProjectQualityGateError, match="at least one"):
        build_project_quality_gate_bundle(record=_record(), gate_specs=())


def test_bundle_rejects_duplicate_gate_ids():
    duplicate_specs = (
        {
            "gate_id": "unit",
            "gate_type": "test",
            "command_text": "python -m pytest tests/test_server_project_quality_gate.py -q",
        },
        {
            "gate_id": "unit",
            "gate_type": "smoke",
            "command_text": "python -m pytest tests/test_server_project_quality_gate.py -q",
        },
    )

    with pytest.raises(ServerProjectQualityGateError, match="duplicate quality gate_id"):
        build_project_quality_gate_bundle(
            record=_record(),
            gate_specs=duplicate_specs,
        )


def test_source_has_no_live_execution_runtime():
    source = Path("src/server_project_quality_gate.py").read_text(encoding="utf-8")

    forbidden = ("subprocess", "requests", "httpx", "paramiko", "shell=True", "os.system")
    for fragment in forbidden:
        assert fragment not in source
