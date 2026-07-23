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
