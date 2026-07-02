import pytest

from src.internal_reference_links import InternalReferenceLinksError, build_knowledge_link_targets


def test_builds_clickable_internal_targets_for_memory_and_raptor_records():
    links = build_knowledge_link_targets(
        [
            {"candidate_id": "memcand_abc", "title": "ASV BW Hilfe"},
            {"node_id": "rg_node_abc", "label": "ASV BW Hilfe"},
            {"edge_id": "rg_edge_abc", "relation": "describes"},
        ]
    )

    assert links[0]["kind"] == "memory"
    assert links[0]["uri"].startswith("odysseus://memory/")
    assert links[0]["chat_href"].startswith("#memory-")
    assert links[0]["markdown"].startswith("[ASV BW Hilfe](")
    assert links[1]["uri"].startswith("odysseus://raptor/node/")
    assert links[2]["uri"].startswith("odysseus://raptor/edge/")
    assert all(link["raw_content_visible"] is False for link in links)


def test_internal_link_targets_reject_raw_or_secret_payloads():
    with pytest.raises(InternalReferenceLinksError):
        build_knowledge_link_targets([{"candidate_id": "memcand_abc", "raw_text": "private raw text"}])
    with pytest.raises(InternalReferenceLinksError):
        build_knowledge_link_targets([{"candidate_id": "Bearer secret", "title": "x"}])
