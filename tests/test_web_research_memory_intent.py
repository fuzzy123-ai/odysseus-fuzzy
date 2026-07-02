from src.web_research_memory_intent import build_web_research_memory_intent
from src.web_research_memory_packet import WebResearchMemoryPacket


def test_web_research_memory_intent_requires_review_before_record():
    packet = WebResearchMemoryPacket.create(
        topic="ASV BW Hilfe",
        summary="Die Hilfeseite beschreibt zentrale Abläufe.",
        source_refs=[{"url": "https://asv-bw.de/hilfe", "title": "Hilfe"}],
        confidence=0.8,
    )

    intent = build_web_research_memory_intent(packet=packet, author_model="gemma4:e4b", reviewed=False)

    assert intent.status == "review"
    assert intent.memory_records == ()
    assert intent.raptorgraph_candidates[0]["truth_write_allowed"] is False


def test_web_research_memory_intent_creates_internal_refs_after_review():
    packet = WebResearchMemoryPacket.create(
        topic="ASV BW Hilfe",
        summary="Die Hilfeseite beschreibt zentrale Abläufe.",
        source_refs=[{"url": "https://asv-bw.de/hilfe", "title": "Hilfe"}],
        confidence=0.8,
    )

    intent = build_web_research_memory_intent(packet=packet, author_model="gemma4:e4b", reviewed=True)
    payload = intent.to_dict()

    assert payload["status"] == "ready"
    assert payload["memory_records"][0]["internal_ref"]["uri"].startswith("odysseus://memory/")
    assert payload["raptorgraph_candidates"][0]["internal_ref"]["uri"].startswith("odysseus://raptor/node/")
