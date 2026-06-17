from dataclasses import replace

from src.release_morning_payload import build_current_release_morning_payload
from src.release_morning_payload_digest import current_release_morning_payload_digest, release_morning_payload_digest


def test_release_morning_payload_digest_is_stable_for_same_payload():
    payload = build_current_release_morning_payload()

    assert release_morning_payload_digest(payload) == release_morning_payload_digest(payload)
    assert len(release_morning_payload_digest(payload)) == 64


def test_release_morning_payload_digest_changes_when_payload_changes():
    payload = build_current_release_morning_payload()
    changed = replace(payload, brief_markdown=payload.brief_markdown + "\n")

    assert release_morning_payload_digest(payload) != release_morning_payload_digest(changed)


def test_current_release_morning_payload_digest_uses_current_payload():
    assert current_release_morning_payload_digest() == release_morning_payload_digest(build_current_release_morning_payload())
