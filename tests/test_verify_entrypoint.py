from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify.py"
WORKFLOW_DIR = ROOT / ".github" / "workflows"


def _load_verify():
    spec = importlib.util.spec_from_file_location("odysseus_verify", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git_repo(tmp_path: Path) -> Path:
    root = tmp_path / "verify-repo"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "tracked.txt").write_text("safe fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Verify Test",
            "-c",
            "user.email=verify@example.invalid",
            "commit",
            "-qm",
            "initial",
        ],
        cwd=root,
        check=True,
    )
    return root


def test_registry_has_stable_cross_platform_lane_names_and_order() -> None:
    verify = _load_verify()
    registry = verify.build_check_registry()

    assert tuple(verify.LANES) == ("guards-only", "fast", "full", "ui")
    assert tuple(registry) == verify.CHECK_ORDER
    assert set(verify.LANES["guards-only"]) < set(verify.LANES["fast"])
    assert "pytest_full" in verify.LANES["full"]
    assert "visual_evidence" in verify.LANES["ui"]
    for spec in registry.values():
        assert spec.timeout_seconds > 0
        assert spec.required is True
        assert not any(
            token.lower() in {"bash", "cmd", "cmd.exe", "powershell", "sh"}
            for token in spec.command
        )


def test_unknown_lane_and_missing_registry_check_fail_closed() -> None:
    verify = _load_verify()

    with pytest.raises(verify.VerificationConfigError, match="unknown"):
        verify.resolve_lane("unknown")
    registry = verify.build_check_registry()
    registry.pop("changed_json_parse")
    with pytest.raises(verify.VerificationConfigError, match="unavailable"):
        verify.resolve_lane("guards-only", registry=registry)


def test_dry_run_records_exact_checks_without_claiming_evidence() -> None:
    verify = _load_verify()
    report, exit_code = verify.run_lane("guards-only", dry_run=True)

    assert exit_code == verify.VerifyExitCode.PASSED
    assert report["status"] == "planned"
    assert report["strongest_evidence_level"] == "none"
    assert [item["check_id"] for item in report["checks"]] == list(
        verify.LANES["guards-only"]
    )
    assert {item["status"] for item in report["checks"]} == {"planned"}
    assert "integration_not_verified" in report["verification_limits"]
    assert "receipt" not in report


def test_dry_run_and_injected_runner_cannot_use_receipt_execution_path(
    tmp_path,
) -> None:
    verify = _load_verify()

    with pytest.raises(SystemExit) as dry_run:
        verify.main(["--lane", "guards-only", "--dry-run", "--receipt"])
    assert dry_run.value.code == 2

    def injected(spec):
        return verify.CheckOutcome(spec.check_id, verify.CheckStatus.PASSED, 0, 0)

    report, exit_code = verify.run_lane(
        "guards-only",
        root=tmp_path,
        check_runner=injected,
    )
    assert exit_code == verify.VerifyExitCode.PASSED
    assert "receipt" not in report


def test_real_lane_receipt_requires_stable_pre_and_post_binding(tmp_path) -> None:
    verify = _load_verify()
    root = _git_repo(tmp_path)

    report, exit_code = verify.run_lane_with_receipt("guards-only", root=root)

    assert exit_code == verify.VerifyExitCode.PASSED
    assert report["receipt"]["result"] == "passed"
    assert report["receipt"]["binding"]["workspace_state"] == "clean_head"
    assert str(root) not in json.dumps(report["receipt"], sort_keys=True)


def test_repository_change_during_lane_prevents_receipt(tmp_path, monkeypatch) -> None:
    verify = _load_verify()
    root = _git_repo(tmp_path)

    def mutating_run_lane(lane, **_kwargs):
        (root / "tracked.txt").write_text("changed during checks\n", encoding="utf-8")
        checks = [
            {
                "check_id": check_id,
                "kind": "internal",
                "command": ["internal"],
                "timeout_seconds": 30,
                "evidence_level": "static",
                "required": True,
                "status": "passed",
                "returncode": 0,
                "duration_ms": 0,
                "details": {},
            }
            for check_id in verify.LANES[lane]
        ]
        return (
            {
                "schema": verify.VERIFY_RUN_SCHEMA,
                "lane": lane,
                "status": "passed",
                "exit_code": 0,
                "strongest_evidence_level": "static",
                "checks": checks,
                "verification_limits": list(verify.LANE_LIMITS[lane]),
            },
            verify.VerifyExitCode.PASSED,
        )

    monkeypatch.setattr(verify, "run_lane", mutating_run_lane)
    with pytest.raises(verify.ReceiptError, match="changed during verification"):
        verify.run_lane_with_receipt("guards-only", root=root)


def test_failed_and_unavailable_required_checks_have_distinct_exit_codes() -> None:
    verify = _load_verify()

    def failed(spec):
        status = (
            verify.CheckStatus.FAILED
            if spec.check_id == "changed_json_parse"
            else verify.CheckStatus.PASSED
        )
        return verify.CheckOutcome(spec.check_id, status, 1, 1)

    def unavailable(spec):
        status = (
            verify.CheckStatus.UNAVAILABLE
            if spec.check_id == "changed_python_compile"
            else verify.CheckStatus.PASSED
        )
        return verify.CheckOutcome(spec.check_id, status, None, 1)

    failed_report, failed_code = verify.run_lane(
        "guards-only",
        check_runner=failed,
    )
    unavailable_report, unavailable_code = verify.run_lane(
        "guards-only",
        check_runner=unavailable,
    )

    assert failed_code == verify.VerifyExitCode.FAILED
    assert failed_report["status"] == "failed"
    assert failed_report["strongest_evidence_level"] == "none"
    assert unavailable_code == verify.VerifyExitCode.UNAVAILABLE
    assert unavailable_report["status"] == "unavailable"
    assert unavailable_report["strongest_evidence_level"] == "none"


def test_check_runner_must_return_the_requested_check_id() -> None:
    verify = _load_verify()

    def mismatched(spec):
        return verify.CheckOutcome("different", verify.CheckStatus.PASSED, 0, 0)

    with pytest.raises(verify.VerificationConfigError, match="mismatched"):
        verify.run_lane("guards-only", check_runner=mismatched)


def test_changed_python_and_json_guards_are_behavioral(tmp_path, monkeypatch) -> None:
    verify = _load_verify()
    valid_python = tmp_path / "valid.py"
    invalid_python = tmp_path / "invalid.py"
    valid_json = tmp_path / "valid.json"
    invalid_json = tmp_path / "invalid.json"
    valid_python.write_text("answer = 42\n", encoding="utf-8")
    valid_json.write_text('{"ok": true}\n', encoding="utf-8")

    monkeypatch.setattr(
        verify,
        "_changed_paths",
        lambda root: (valid_python, valid_json),
    )
    verify._compile_changed_python(tmp_path)
    verify._parse_changed_json(tmp_path)

    invalid_python.write_text("def broken(:\n", encoding="utf-8")
    monkeypatch.setattr(
        verify,
        "_changed_paths",
        lambda root: (invalid_python,),
    )
    with pytest.raises(SyntaxError):
        verify._compile_changed_python(tmp_path)

    invalid_json.write_text('{"broken":', encoding="utf-8")
    monkeypatch.setattr(
        verify,
        "_changed_paths",
        lambda root: (invalid_json,),
    )
    with pytest.raises(json.JSONDecodeError):
        verify._parse_changed_json(tmp_path)


def test_ui_lane_fails_closed_without_visual_evidence(tmp_path) -> None:
    verify = _load_verify()
    visual_spec = verify.build_check_registry()["visual_evidence"]

    unavailable = verify.run_check(
        visual_spec,
        root=tmp_path,
        visual_evidence=None,
    )
    assert unavailable.status == verify.CheckStatus.UNAVAILABLE

    evidence_dir = tmp_path / "test-results"
    evidence_dir.mkdir()
    artifact = evidence_dir / "evidence.png"
    artifact.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic-image-evidence")
    available = verify.run_check(
        visual_spec,
        root=tmp_path,
        visual_evidence=artifact,
    )

    assert available.status == verify.CheckStatus.PASSED
    assert available.details["available"] is True
    assert available.details["media_type"] == "png"
    assert len(available.details["sha256"]) == 64
    assert str(artifact) not in json.dumps(available.to_dict(visual_spec))

    renamed_private_file = evidence_dir / "not-an-image.png"
    renamed_private_file.write_text("synthetic-private-value", encoding="utf-8")
    rejected = verify.run_check(
        visual_spec,
        root=tmp_path,
        visual_evidence=renamed_private_file,
    )
    assert rejected.status == verify.CheckStatus.FAILED
    assert rejected.details == {}

    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\nsynthetic")
    unavailable_outside = verify.run_check(
        visual_spec,
        root=tmp_path,
        visual_evidence=outside,
    )
    assert unavailable_outside.status == verify.CheckStatus.UNAVAILABLE


def test_subprocess_checks_suppress_raw_output(tmp_path, monkeypatch) -> None:
    verify = _load_verify()
    spec = verify.build_check_registry()["pytest_fast"]
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(verify.subprocess, "run", fake_run)
    verify._run_subprocess_check(spec, root=tmp_path)

    assert observed["stdout"] is subprocess.DEVNULL
    assert observed["stderr"] is subprocess.DEVNULL
    assert observed["stdin"] is subprocess.DEVNULL
    assert observed["command"][0] == sys.executable


def test_timeout_fails_closed_without_raw_details(tmp_path, monkeypatch) -> None:
    verify = _load_verify()
    spec = verify.build_check_registry()["pytest_fast"]

    def timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs["timeout"], output=b"private")

    monkeypatch.setattr(verify.subprocess, "run", timeout)
    outcome = verify.run_check(spec, root=tmp_path, visual_evidence=None)

    assert outcome.status == verify.CheckStatus.FAILED
    assert outcome.returncode == 1
    assert outcome.details == {}
    assert "private" not in json.dumps(outcome.to_dict(spec))


def test_cli_lists_registry_and_dry_run_is_content_free() -> None:
    listed = subprocess.run(
        [sys.executable, str(SCRIPT), "--list"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    planned = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--lane",
            "guards-only",
            "--dry-run",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert listed.returncode == 0
    assert json.loads(listed.stdout)["schema"] == "odysseus.verify_registry.v1"
    assert planned.returncode == 0
    report = json.loads(planned.stdout)
    assert report["status"] == "planned"
    assert str(ROOT) not in planned.stdout


def test_ci_uses_the_reusable_direct_full_pytest_gate_without_weakening_checks() -> None:
    ci_source = (WORKFLOW_DIR / "ci.yml").read_text(encoding="utf-8")
    quality_gate_source = (WORKFLOW_DIR / "quality-gate.yml").read_text(
        encoding="utf-8"
    )

    assert "branches: [dev, main]" in ci_source
    assert "pull_request:" in ci_source
    assert "uses: ./.github/workflows/quality-gate.yml" in ci_source
    assert "contents: read" in ci_source

    assert "continue-on-error" not in quality_gate_source
    assert "python -m compileall -q app.py core routes src services scripts tests" in quality_gate_source
    assert "node --check" in quality_gate_source
    assert quality_gate_source.count("python -m pytest -q --maxfail=1") == 1
    assert "scripts/verify.py" not in quality_gate_source
    assert "permissions:\n  contents: read" in quality_gate_source
    assert "contents: write" not in quality_gate_source
    assert quality_gate_source.count("persist-credentials: false") == 3
    assert quality_gate_source.count("ref: ${{ github.sha }}") == 3
    assert not any(
        forbidden in quality_gate_source
        for forbidden in ("--ignore", "--deselect", "--lf", "--failed-first", "|| true")
    )
