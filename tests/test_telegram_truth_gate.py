import json
import re
from pathlib import Path

import pytest

from src.telegram_truth_gate import gate_telegram_reply_text


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "telegram_truth_runtime_failures.json"


@pytest.mark.parametrize("case", json.loads(FIXTURE_PATH.read_text(encoding="utf-8")))
def test_redacted_failure_corpus_contains_no_private_material(case: dict):
    raw = json.dumps(case, ensure_ascii=False)

    assert case["contains_private_content"] is False
    assert not re.search(r"sk-[A-Za-z0-9_-]{10,}", raw)
    assert not re.search(r"\b\d{8,}\b", raw)
    assert "redacted-token" not in raw


@pytest.mark.parametrize(
    "text,expected_claim",
    [
        ("Ich habe pygame installiert, alles fertig!", "dependency_installed"),
        ("Der Delegate hat eine falsche Rueckmeldung gegeben.", "delegate_alibi"),
        ("Ich habe `pong.py` erstellt.", "file_changed"),
        ("Ich habe den Screenshot `screenshot.png` per Telegram geschickt.", "telegram_sent"),
    ],
)
def test_truth_gate_marks_unverified_success_claims(tmp_path: Path, text: str, expected_claim: str):
    result = gate_telegram_reply_text(text, [], repo_root=tmp_path)

    assert result.status == "unknown"
    assert "nicht verifiziert" in result.text
    assert expected_claim in {finding.claim_type for finding in result.findings}


def test_truth_gate_strips_jubilation_for_unknown_status(tmp_path: Path):
    result = gate_telegram_reply_text(
        "Geschafft! Screenshot gesendet. \U0001f389",
        [],
        repo_root=tmp_path,
    )

    lowered = result.text.lower()
    assert "nicht verifiziert" in lowered
    assert "geschafft" not in lowered
    assert "\U0001f389" not in result.text


def test_truth_gate_keeps_plain_blocked_reply_plain(tmp_path: Path):
    result = gate_telegram_reply_text(
        "Blockiert: pygame ist in der Sandbox nicht verfuegbar.",
        [],
        repo_root=tmp_path,
    )

    assert result.status == "verified"
    assert result.text == "Blockiert: pygame ist in der Sandbox nicht verfuegbar."
