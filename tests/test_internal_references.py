import pytest

from src.internal_references import (
    InternalReferenceError,
    build_internal_reference,
    build_internal_reference_dict,
    parse_chat_href,
    parse_internal_uri,
    reference_markdown,
)


def test_builds_memory_reference_with_canonical_uri_and_chat_href():
    ref = build_internal_reference("memory", "uix-abc123", label="Open memory")

    assert ref.uri == "odysseus://memory/uix-abc123"
    assert ref.chat_href == "#memory-uix-abc123"
    assert ref.raw_content_visible is False
    assert ref.to_dict()["schema"] == "odysseus.internal_reference.v1"


def test_encodes_non_anchor_safe_ids_without_losing_entity_id():
    ref = build_internal_reference("rag_source", "bigdata:nextcloud-main:abc", label="RAG source")

    assert ref.uri == "odysseus://rag/source/bigdata%3Anextcloud-main%3Aabc"
    assert ref.chat_href.startswith("#rag-source-b64-")
    assert parse_chat_href(ref.chat_href).entity_id == "bigdata:nextcloud-main:abc"
    assert parse_internal_uri(ref.uri).entity_id == "bigdata:nextcloud-main:abc"


def test_supports_raptor_and_graph_reference_families():
    assert build_internal_reference_dict("raptor_node", "node-1")["chat_href"] == "#raptor-node-node-1"
    assert build_internal_reference_dict("raptor_edge", "edge-1")["chat_href"] == "#raptor-edge-edge-1"
    assert build_internal_reference_dict("rag_chunk", "chunk-1")["chat_href"] == "#rag-chunk-chunk-1"
    assert build_internal_reference_dict("graph_node", "node-2")["chat_href"] == "#graph-node-node-2"
    assert build_internal_reference_dict("graph_edge", "edge-2")["chat_href"] == "#graph-edge-edge-2"
    assert build_internal_reference_dict("graph_query", "query-2")["chat_href"] == "#graph-query-query-2"


def test_reference_markdown_round_trips_through_safe_href():
    ref = build_internal_reference("memory", "mem-1", label="Memory")

    assert reference_markdown(ref) == "[Memory](#memory-mem-1)"


@pytest.mark.parametrize(
    "entity_id",
    [
        "",
        "C:/Users/private/file.txt",
        "api_key=secret-value",
        "Bearer abcdefghijkl",
    ],
)
def test_rejects_empty_pathlike_or_secret_ids(entity_id):
    with pytest.raises(InternalReferenceError):
        build_internal_reference("memory", entity_id)
