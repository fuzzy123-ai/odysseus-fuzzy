from src import personal_docs
from src.rag_vector import DEFAULT_FILE_EXTENSIONS, VectorRAG


def test_vectorrag_indexes_office_files_via_office_extractor(tmp_path, monkeypatch):
    docx = tmp_path / "sample.docx"
    docx.write_bytes(b"\xe1\x00not utf8 office bytes")
    added = []

    def fake_extract(path):
        assert path == str(docx)
        return "Office extraction marker for vector rag."

    def fake_add_document(chunk, metadata):
        added.append((chunk, metadata))
        return True

    monkeypatch.setattr(personal_docs, "extract_office_text", fake_extract)
    rag = VectorRAG.__new__(VectorRAG)
    rag.add_document = fake_add_document

    result = rag.index_personal_documents(str(tmp_path), owner="telegram")

    assert ".docx" in DEFAULT_FILE_EXTENSIONS
    assert result["success"] is True
    assert result["indexed_count"] == 1
    assert result["indexed_files_count"] == 1
    assert result["failed_count"] == 0
    assert added[0][0] == "Office extraction marker for vector rag."
    assert added[0][1]["type"] == ".docx"
    assert added[0][1]["owner"] == "telegram"


class _FakeCollection:
    def get(self, include=None):
        return {
            "ids": ["a", "b", "c"],
            "metadatas": [
                {"owner": "homebase", "source": "/private/path/a.md", "filename": "a.md", "type": ".md"},
                {"owner": "homebase", "source": "/private/path/b.docx", "filename": "b.docx", "type": ".docx"},
                {"owner": "telegram", "source": "/private/path/c.pdf", "filename": "c.pdf", "type": ".pdf"},
            ],
        }


def test_vectorrag_owner_inventory_is_redacted_and_owner_scoped():
    rag = VectorRAG.__new__(VectorRAG)
    rag._healthy = True
    rag._lanes = []
    rag._collection = _FakeCollection()

    inventory = rag.owner_inventory(owner="homebase")

    assert inventory["healthy"] is True
    assert inventory["chunk_count"] == 2
    assert inventory["source_count"] == 2
    assert inventory["type_counts"] == {".docx": 1, ".md": 1}
    assert inventory["private_content_visible"] is False
    assert inventory["source_paths_visible"] is False
    assert inventory["filenames_visible"] is False
    assert "/private/path" not in str(inventory)
    assert "a.md" not in str(inventory)
