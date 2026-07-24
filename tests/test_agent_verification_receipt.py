from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess

import pytest

from src.agent_verification_receipt import (
    RECEIPT_SCHEMA,
    ReceiptError,
    build_verification_receipt,
    repository_binding,
    validate_verification_receipt,
)


CANARY = "synthetic-private-canary-never-emit"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "tracked.py").write_text("answer = 1\n", encoding="utf-8")
    _git(root, "add", "tracked.py")
    _git(
        root,
        "-c",
        "user.name=Receipt Test",
        "-c",
        "user.email=receipt@example.invalid",
        "commit",
        "-qm",
        "initial",
    )
    return root


def _report(*statuses: str) -> dict:
    checks = []
    for index, status in enumerate(statuses or ("passed",), start=1):
        checks.append(
            {
                "check_id": f"check_{index}",
                "kind": "subprocess",
                "command": ["private", CANARY],
                "timeout_seconds": 30,
                "evidence_level": "static",
                "required": True,
                "status": status,
                "returncode": 0,
                "duration_ms": 999,
                "details": {
                    "stdout": CANARY,
                    "stderr": CANARY,
                    "exception": CANARY,
                    "api_payload": CANARY,
                    "environment_value": CANARY,
                    "private_path": CANARY,
                },
            }
        )
    return {
        "schema": "odysseus.verify_run.v1",
        "lane": "guards-only",
        "status": "passed",
        "exit_code": 0,
        "strongest_evidence_level": "static",
        "checks": checks,
        "verification_limits": ["integration_not_verified"],
    }


def _build(root: Path, report: dict | None = None) -> dict:
    before = repository_binding(root)
    after = repository_binding(root)
    return build_verification_receipt(
        report or _report("passed"),
        binding_before=before,
        binding_after=after,
    )


def test_clean_receipt_is_deterministic_content_free_and_current(tmp_path) -> None:
    root = _repo(tmp_path)

    first = _build(root)
    second = _build(root)
    encoded = json.dumps(first, sort_keys=True)

    assert first == second
    assert first["schema"] == RECEIPT_SCHEMA
    assert first["binding"]["workspace_state"] == "clean_head"
    assert first["binding"]["dirty_diff_digest"] is None
    assert first["result"] == "passed"
    assert first["content_free"] is True
    assert first["passed_checks"] == ["check_1"]
    assert first["failed_checks"] == []
    assert first["skipped_checks"] == []
    assert first["unavailable_checks"] == []
    assert CANARY not in encoded
    assert str(root) not in encoded
    for forbidden in (
        "stdout",
        "stderr",
        "exception",
        "api_payload",
        "environment_value",
        "private_path",
        "command",
        "details",
        "duration_ms",
    ):
        assert forbidden not in encoded
    validate_verification_receipt(first, root=root, expected_lane="guards-only")


def test_dirty_binding_changes_for_tracked_and_untracked_content(tmp_path) -> None:
    root = _repo(tmp_path)
    (root / "tracked.py").write_text("answer = 2\n", encoding="utf-8")
    tracked = repository_binding(root)
    (root / "tracked.py").write_text("answer = 3\n", encoding="utf-8")
    changed = repository_binding(root)
    (root / "new.py").write_text("new_answer = 4\n", encoding="utf-8")
    untracked = repository_binding(root)

    assert tracked.workspace_state == "dirty_diff"
    assert len(tracked.dirty_diff_digest or "") == 64
    assert tracked.head_revision == changed.head_revision == untracked.head_revision
    assert len({tracked.dirty_diff_digest, changed.dirty_diff_digest, untracked.dirty_diff_digest}) == 3


@pytest.mark.parametrize(
    "relative,content",
    (
        (".env", "SAFE_FIXTURE=not-used\n"),
        ("private.pem", "synthetic fixture\n"),
        ("ordinary.txt", "api_key=synthetic-credential-value\n"),
        ("ordinary.txt", "-----BEGIN PRIVATE KEY-----\nsynthetic\n"),
    ),
)
def test_sensitive_dirty_content_fails_before_receipt_digest(
    tmp_path,
    relative,
    content,
) -> None:
    root = _repo(tmp_path)
    (root / relative).write_text(content, encoding="utf-8")

    with pytest.raises(ReceiptError, match="sensitive|credential") as raised:
        repository_binding(root)

    assert CANARY not in str(raised.value)
    assert content.strip() not in str(raised.value)


@pytest.mark.parametrize("tracked", (False, True))
def test_private_prefixed_path_components_fail_closed(tmp_path, tracked) -> None:
    root = _repo(tmp_path)
    relative = Path("private_customer") / "customer.txt"
    candidate = root / relative
    candidate.parent.mkdir()
    candidate.write_text("ordinary fixture\n", encoding="utf-8")
    if tracked:
        _git(root, "add", relative.as_posix())
        _git(
            root,
            "-c",
            "user.name=Receipt Test",
            "-c",
            "user.email=receipt@example.invalid",
            "commit",
            "-qm",
            "private path fixture",
        )
        candidate.write_text("changed ordinary fixture\n", encoding="utf-8")

    with pytest.raises(ReceiptError, match="sensitive"):
        repository_binding(root)


def test_untracked_symlink_fails_closed_when_supported(tmp_path) -> None:
    root = _repo(tmp_path)
    target = root / "ordinary.txt"
    target.write_text("ordinary fixture\n", encoding="utf-8")
    link = root / "ordinary-link.txt"
    try:
        link.symlink_to(target.name)
    except OSError:
        pytest.skip("unprivileged symlinks are unavailable on this Windows host")

    with pytest.raises(ReceiptError, match="links"):
        repository_binding(root)


def test_untracked_link_admission_is_deterministically_fail_closed(
    tmp_path,
    monkeypatch,
) -> None:
    root = _repo(tmp_path)
    candidate = root / "ordinary.txt"
    candidate.write_text("ordinary fixture\n", encoding="utf-8")
    original = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == candidate or original(path),
    )

    with pytest.raises(ReceiptError, match="links"):
        repository_binding(root)


@pytest.mark.parametrize(
    "statuses,result,projection",
    (
        (("passed", "failed"), "failed", "failed_checks"),
        (("passed", "unavailable"), "unavailable", "unavailable_checks"),
        (("passed", "planned"), "not_verified", "skipped_checks"),
    ),
)
def test_failed_unavailable_skipped_and_not_verified_are_distinct(
    tmp_path,
    statuses,
    result,
    projection,
) -> None:
    root = _repo(tmp_path)
    receipt = _build(root, _report(*statuses))

    assert receipt["result"] == result
    assert receipt[projection] == ["check_2"]
    assert receipt["strongest_evidence_level"] == "none"
    assert "lane_not_fully_verified" in receipt["not_verified"]
    validate_verification_receipt(receipt, root=root)


def test_unknown_check_state_fails_closed(tmp_path) -> None:
    root = _repo(tmp_path)

    with pytest.raises(ReceiptError, match="unknown state"):
        _build(root, _report("invented"))


def test_builder_and_validator_enforce_check_limit(tmp_path) -> None:
    root = _repo(tmp_path)
    oversized_report = _report(*(["passed"] * 129))
    with pytest.raises(ReceiptError, match="checks are invalid"):
        _build(root, oversized_report)

    oversized_receipt = _build(root)
    oversized_receipt["checks"] = [
        {
            "check_id": f"check_{index}",
            "required": True,
            "status": "passed",
            "evidence_level": "static",
        }
        for index in range(1, 130)
    ]
    with pytest.raises(ReceiptError, match="checks are invalid"):
        validate_verification_receipt(oversized_receipt, root=root)


def test_strongest_evidence_is_derived_and_overstatement_is_rejected(tmp_path) -> None:
    root = _repo(tmp_path)
    report = _report("passed")
    report["strongest_evidence_level"] = "live"
    receipt = _build(root, report)

    assert receipt["strongest_evidence_level"] == "static"
    receipt["strongest_evidence_level"] = "live"
    with pytest.raises(ReceiptError, match="evidence level"):
        validate_verification_receipt(receipt, root=root)


def test_ui_combined_evidence_requires_contract_and_visual(tmp_path) -> None:
    root = _repo(tmp_path)
    report = _report("passed", "passed")
    report["checks"][0]["evidence_level"] = "ui_contract"
    report["checks"][1]["evidence_level"] = "visual"
    combined = _build(root, report)
    assert combined["strongest_evidence_level"] == "ui_contract_plus_visual_artifact"

    report["checks"][1]["evidence_level"] = "static"
    contract_only = _build(root, report)
    assert contract_only["strongest_evidence_level"] == "ui_contract"


def test_pre_post_binding_change_prevents_receipt(tmp_path) -> None:
    root = _repo(tmp_path)
    before = repository_binding(root)
    (root / "tracked.py").write_text("answer = 2\n", encoding="utf-8")
    after = repository_binding(root)

    with pytest.raises(ReceiptError, match="changed during verification"):
        build_verification_receipt(
            _report("passed"),
            binding_before=before,
            binding_after=after,
        )


def test_stale_tampered_and_explicit_agent_metadata_are_rejected(tmp_path) -> None:
    root = _repo(tmp_path)
    receipt = _build(root)

    tampered = deepcopy(receipt)
    tampered["not_verified"].append("extra_not_verified")
    with pytest.raises(ReceiptError, match="tampered"):
        validate_verification_receipt(tampered, root=root)

    explicit_agent = deepcopy(receipt)
    explicit_agent["producer"]["authored_by"] = "agent"
    with pytest.raises(ReceiptError, match="producer"):
        validate_verification_receipt(explicit_agent, root=root)

    (root / "tracked.py").write_text("answer = 9\n", encoding="utf-8")
    with pytest.raises(ReceiptError, match="stale"):
        validate_verification_receipt(receipt, root=root)


def test_unknown_raw_fields_are_rejected_even_with_recomputed_digest(tmp_path) -> None:
    root = _repo(tmp_path)
    receipt = _build(root)
    receipt["stdout"] = CANARY

    with pytest.raises(ReceiptError, match="fields"):
        validate_verification_receipt(receipt, root=root)


def test_schema_is_closed_and_declares_only_known_states() -> None:
    schema_path = (
        Path(__file__).resolve().parents[1]
        / "specs"
        / "agent-maintenance-verification-receipt.v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    encoded = json.dumps(schema, sort_keys=True)

    assert schema["additionalProperties"] is False
    assert set(schema["properties"]["result"]["enum"]) == {
        "passed",
        "failed",
        "unavailable",
        "not_verified",
    }
    assert set(
        schema["properties"]["checks"]["items"]["properties"]["status"]["enum"]
    ) == {"passed", "failed", "skipped", "unavailable"}
    assert "live" not in schema["properties"]["strongest_evidence_level"]["enum"]
    assert schema["properties"]["checks"]["maxItems"] == 128
    for forbidden in ("stdout", "stderr", "exception", "api_payload", "environment_value"):
        assert forbidden not in encoded
