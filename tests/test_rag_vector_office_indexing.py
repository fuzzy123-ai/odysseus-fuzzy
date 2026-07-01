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
