from types import SimpleNamespace

from src.chat_processor import ChatProcessor


class _Memory:
    def __init__(self, entries):
        self.entries = entries
        self.used_ids = []

    def load(self, owner=None):
        return list(self.entries)

    def increment_uses(self, ids):
        self.used_ids.extend(ids)


class _RAG:
    def __init__(self, results):
        self.results = results

    def search(self, message, k=5, owner=None):
        return list(self.results)


class _Docs:
    def __init__(self, results=None):
        self.rag_manager = _RAG(results or [])


def _session(endpoint_url="https://api.openai.com/v1/chat/completions", model="gpt-4o"):
    return SimpleNamespace(id="session-1", endpoint_url=endpoint_url, model=model)


def _joined_content(preface):
    return "\n".join(msg.get("content", "") for msg in preface)


def test_normal_chat_keeps_private_pinned_memory_and_rag_context():
    processor = ChatProcessor(
        memory_manager=_Memory([
            {
                "id": "mem-private",
                "text": "Private project preference",
                "pinned": True,
                "metadata": {"classification": "private"},
            }
        ]),
        personal_docs_manager=_Docs([
            {
                "id": "doc-private",
                "document": "Private planning document",
                "similarity": 0.91,
                "metadata": {"filename": "plan.md", "classification": "private"},
            }
        ]),
    )

    preface, rag_sources, _ = processor.build_context_preface(
        message="planning",
        session=_session(),
        owner="alice",
        use_context_providers=False,
    )

    content = _joined_content(preface)
    assert "Private project preference" in content
    assert "Private planning document" in content
    assert rag_sources[0]["filename"] == "plan.md"


def test_normal_chat_blocks_sensitive_memory_and_rag_context():
    processor = ChatProcessor(
        memory_manager=_Memory([
            {
                "id": "mem-sensitive",
                "text": "Sensitive payroll memory",
                "pinned": True,
                "metadata": {"classification": "sensitive"},
            }
        ]),
        personal_docs_manager=_Docs([
            {
                "id": "doc-sensitive",
                "document": "Sensitive invoice document",
                "similarity": 0.91,
                "metadata": {"filename": "invoice.pdf", "classification": "sensitive"},
            }
        ]),
    )

    preface, rag_sources, _ = processor.build_context_preface(
        message="invoice payroll",
        session=_session(),
        owner="alice",
        use_context_providers=False,
    )

    content = _joined_content(preface)
    assert "Sensitive payroll memory" not in content
    assert "Sensitive invoice document" not in content
    assert rag_sources == []


def test_global_dsgvo_blocks_retrieval_context_for_external_session(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "enabled")
    processor = ChatProcessor(
        memory_manager=_Memory([
            {
                "id": "mem-private",
                "text": "Private memory over external model",
                "pinned": True,
                "metadata": {"classification": "private"},
            }
        ]),
        personal_docs_manager=_Docs([
            {
                "id": "doc-private",
                "document": "Private document over external model",
                "similarity": 0.91,
                "metadata": {"filename": "private.md", "classification": "private"},
            }
        ]),
    )

    preface, rag_sources, _ = processor.build_context_preface(
        message="private",
        session=_session(endpoint_url="https://api.openai.com/v1/chat/completions", model="gpt-4o"),
        owner="alice",
        use_context_providers=False,
    )

    content = _joined_content(preface)
    assert "Private memory over external model" not in content
    assert "Private document over external model" not in content
    assert rag_sources == []


def test_global_dsgvo_allows_retrieval_context_for_local_session(monkeypatch):
    monkeypatch.setenv("ODYSSEUS_DSGVO_MODE", "enabled")
    processor = ChatProcessor(
        memory_manager=_Memory([
            {
                "id": "mem-sensitive",
                "text": "Sensitive local-only memory",
                "pinned": True,
                "metadata": {"classification": "sensitive"},
            }
        ]),
        personal_docs_manager=_Docs([
            {
                "id": "doc-sensitive",
                "document": "Sensitive local-only document",
                "similarity": 0.91,
                "metadata": {"filename": "sensitive.md", "classification": "sensitive"},
            }
        ]),
    )

    preface, rag_sources, _ = processor.build_context_preface(
        message="sensitive",
        session=_session(endpoint_url="http://localhost:11434/v1/chat/completions", model="local-chat"),
        owner="alice",
        use_context_providers=False,
    )

    content = _joined_content(preface)
    assert "Sensitive local-only memory" in content
    assert "Sensitive local-only document" in content
    assert rag_sources[0]["filename"] == "sensitive.md"
