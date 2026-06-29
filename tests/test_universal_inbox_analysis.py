import json

import pytest

from src.universal_inbox_analysis import (
    UniversalInboxAnalysisError,
    build_universal_inbox_file_analysis_packet,
    evaluate_universal_inbox_file_analysis_policy,
)


def test_public_file_allows_api_or_local_and_abstract_memory():
    policy = evaluate_universal_inbox_file_analysis_policy(
        {
            "filename": "readme.md",
            "classification": "public",
            "document_type": "reference",
            "extraction_status": "completed",
        },
        settings={"dsgvo_mode": False},
    )

    assert policy.status == "go"
    assert policy.api_model_allowed is True
    assert policy.local_model_required is False
    assert policy.memory_write_allowed is True
    assert policy.raptor_write_allowed is True


def test_dsgvo_mode_forces_local_only_even_for_private_file():
    policy = evaluate_universal_inbox_file_analysis_policy(
        {
            "filename": "note.txt",
            "classification": "private",
            "document_type": "reference",
            "extraction_status": "completed",
        },
        settings={"dsgvo_mode": True},
    )

    assert policy.status == "go"
    assert policy.dsgvo_mode is True
    assert policy.local_only_required is True
    assert policy.local_model_required is True
    assert policy.api_model_allowed is False
    assert policy.memory_write_allowed is True


def test_sensitive_source_requires_review_before_memory_and_raptor_write():
    policy = evaluate_universal_inbox_file_analysis_policy(
        {
            "filename": "arbeitsblatt.txt",
            "source_labels": ["Privat"],
            "document_type": "reference",
            "extraction_status": "completed",
        },
        settings={"dsgvo_mode": False},
    )

    assert policy.status == "review"
    assert policy.classification == "sensitive"
    assert policy.local_only_required is True
    assert policy.api_model_allowed is False
    assert policy.memory_write_allowed is False
    assert policy.raptor_write_allowed is False
    assert "sensitive_memory_requires_explicit_review" in policy.review_reasons


def test_secret_hint_blocks_memory_without_explicit_secret_review():
    policy = evaluate_universal_inbox_file_analysis_policy(
        {
            "filename": "config.txt",
            "classification": "private",
            "document_type": "reference",
            "extraction_status": "completed",
            "text_sample": "password = super-private",
        }
    )

    assert policy.status == "review"
    assert policy.classification == "secret"
    assert policy.local_only_required is True
    assert policy.memory_write_allowed is False
    assert policy.raptor_write_allowed is False
    assert "secret_memory_requires_explicit_review" in policy.review_reasons


def test_dangerous_or_raw_persistence_is_no_go():
    decision = evaluate_universal_inbox_file_analysis_policy(
        {
            "filename": "setup.exe",
            "classification": "private",
            "document_type": "reference",
            "extraction_status": "blocked",
            "dangerous": True,
            "raw_content_persisted": True,
        }
    )

    assert decision.status == "no_go"
    assert "dangerous_file_blocked" in decision.no_go_reasons
    assert "raw_content_persistence" in decision.no_go_reasons
    assert decision.memory_write_allowed is False
    assert decision.raptor_write_allowed is False


def test_analysis_packet_serializes_only_abstraction_not_raw_text():
    packet = build_universal_inbox_file_analysis_packet(
        {
            "filename": "rechnung.txt",
            "source_channel": "telegram",
            "classification": "private",
            "extraction_status": "completed",
            "extractor": "plain_text",
            "raw_text": "PRIVATE RAW TEXT",
        },
        text_sample="Rechnung fuer Projekt Alpha mit privatem Inhalt",
        settings={"dsgvo_mode": True},
    )

    payload = packet.to_dict()
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["policy"]["classification"] == "sensitive"
    assert payload["policy"]["local_only_required"] is True
    assert payload["raw_content_visible"] is False
    assert payload["raw_content_persisted"] is False
    assert payload["abstract"]["document_type"] == "invoice"
    assert "PRIVATE RAW TEXT" not in encoded
    assert "Rechnung fuer Projekt Alpha" not in encoded
    assert "raw_text" not in encoded
    assert "text_sample" not in encoded


def test_invalid_metadata_token_is_rejected_before_serialization():
    with pytest.raises(UniversalInboxAnalysisError):
        build_universal_inbox_file_analysis_packet(
            {
                "filename": "note.txt",
                "source_channel": "telegram; rm",
                "classification": "public",
                "document_type": "reference",
                "extraction_status": "completed",
            }
        )
