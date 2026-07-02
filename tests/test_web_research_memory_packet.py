import pytest

from src.web_research_memory_packet import WebResearchMemoryPacket


def test_web_research_memory_packet_is_redacted_and_source_linked():
    packet = WebResearchMemoryPacket.create(
        topic="ASV BW Hilfe",
        summary="Die Hilfeseite beschreibt zentrale Abläufe.",
        source_refs=[{"url": "https://asv-bw.de/hilfe", "title": "Hilfe"}],
        confidence=0.8,
        gaps=["Kein Loginbereich geprüft"],
    )

    payload = packet.to_dict()

    assert payload["raw_content_visible"] is False
    assert payload["source_refs"][0]["evidence_hash"].startswith("sha256:")


def test_web_research_memory_packet_rejects_secret_text():
    with pytest.raises(ValueError):
        WebResearchMemoryPacket.create(
            topic="Secret",
            summary="api_key=abc",
            source_refs=[{"url": "https://example.test"}],
        )
