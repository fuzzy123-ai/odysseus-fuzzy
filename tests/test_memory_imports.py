"""Regression tests for memory import-path compatibility."""


def test_services_memory_manager_is_canonical_src_class():
    from services.memory import MemoryManager as package_manager
    from services.memory.memory import MemoryManager as module_manager
    from src.memory import MemoryManager as canonical_manager

    assert module_manager is canonical_manager
    assert package_manager is canonical_manager
    assert hasattr(package_manager, "increment_uses")
    assert hasattr(package_manager, "claim_ownerless")


def test_services_memory_vector_is_canonical_src_class():
    from services.memory import MemoryVectorStore as package_vector_store
    from services.memory.memory_vector import MemoryVectorStore as module_vector_store
    from src.memory_vector import MemoryVectorStore as canonical_vector_store

    assert module_vector_store is canonical_vector_store
    assert package_vector_store is canonical_vector_store


def test_memory_service_uses_canonical_manager_api(tmp_path):
    import asyncio

    from services.memory import MemoryService

    service = MemoryService(str(tmp_path))

    remembered = asyncio.run(service.remember("User prefers dark mode", session_id="sess-1"))
    assert remembered.text == "User prefers dark mode"
    assert remembered.session_id == "sess-1"
    assert remembered.metadata["correlation_id"].startswith("sha256:")
    assert remembered.metadata["runtime_event"]["surface"] == "memory"
    assert remembered.metadata["runtime_event"]["component"] == "memory_service"
    assert remembered.metadata["runtime_event"]["event_type"] == "memory_remember"
    assert remembered.metadata["runtime_event"]["raw_content_visible"] is False
    assert "User prefers dark mode" not in str(remembered.metadata["runtime_event"])

    all_memories = service.get_all()
    assert [m.id for m in all_memories] == [remembered.id]

    recalled = asyncio.run(service.recall("dark mode", top_k=5))
    assert [m.id for m in recalled.memories] == [remembered.id]
    assert recalled.memories[0].metadata["runtime_event"]["event_type"] == "memory_recall"
    assert recalled.memories[0].metadata["runtime_event"]["metadata"]["retrieval_count"] == 1
    assert "dark mode" not in str(recalled.memories[0].metadata["runtime_event"])

    assert service.delete(remembered.id) is True
    assert service.last_runtime_event["event_type"] == "memory_delete"
    assert service.last_runtime_event["status"] == "success"
    assert service.delete(remembered.id) is False
    assert service.last_runtime_event["status"] == "skipped"
    assert service.get_all() == []


def test_canonical_manager_keeps_ownerless_claim_helper(tmp_path):
    from src.memory import MemoryManager

    manager = MemoryManager(str(tmp_path))
    entry = manager.add_entry("User likes compact code reviews")
    manager.save([entry])

    manager.claim_ownerless("alice")

    memories = manager.load_all()
    assert memories[0]["owner"] == "alice"
