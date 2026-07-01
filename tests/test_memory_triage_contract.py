from src.memory_triage_contract import (
    memory_triage_enum_instruction,
    normalize_memory_classification,
    normalize_memory_document_type,
    normalize_memory_write_intent_status,
)
from src.universal_inbox_analysis import build_universal_inbox_file_analysis_packet


def test_memory_triage_contract_normalizes_gemma_style_labels():
    assert normalize_memory_classification("Technical Procedure Update") == "private"
    assert normalize_memory_classification("Financial/Billing") == "sensitive"
    assert normalize_memory_classification("Ephemeral Interaction") == "public"
    assert normalize_memory_document_type("Operational Directive", text="Podman statt Docker") == "project"
    assert normalize_memory_document_type("Invoice Fragment") == "invoice"
    assert normalize_memory_write_intent_status("Confirmed") == "ready"
    assert normalize_memory_write_intent_status("pending_review") == "review"
    assert normalize_memory_write_intent_status("None") == "skipped"


def test_enum_instruction_pins_values_for_local_model_prompts():
    text = memory_triage_enum_instruction()

    assert "public, private, sensitive, secret" in text
    assert "project, invoice, worksheet, transient, reference" in text
    assert "ready, review, blocked, skipped" in text


def test_universal_inbox_analysis_maps_podman_decision_to_project():
    packet = build_universal_inbox_file_analysis_packet(
        {
            "filename": "server-note.txt",
            "source_channel": "telegram",
            "classification": "Technical Procedure Update",
            "extraction_status": "completed",
            "extractor": "plain_text",
        },
        text_sample="Odysseus nutzt Podman statt Docker fuer Server Operations.",
        settings={"dsgvo_mode": False},
    ).to_dict()

    assert packet["policy"]["classification"] == "private"
    assert packet["document_type"] == "project"
