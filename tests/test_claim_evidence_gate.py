from pathlib import Path

from src.claim_evidence_gate import build_claim_evidence_correction, evaluate_response_claims
from src.tool_transaction_ledger import ToolTransaction


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
