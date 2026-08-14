from __future__ import annotations

import ast
from dataclasses import fields
import inspect

import pytest

import src.unified_source_index_sources.knowledge as knowledge_module
from src.native_knowledge_store import (
    KnowledgeAccessDenied,
    KnowledgeGenerationMismatch,
    KnowledgeNotFound,
    KnowledgeStoreSnapshot,
    KnowledgeTombstoned,
    NativeKnowledgeStore,
)
from src.unified_source_index_contract import ContentPolicy, SourceKind, content_hash
from src.unified_source_index_owner_scope import OwnerScope
from src.unified_source_index_sources.knowledge import (
    KNOWLEDGE_SOURCE_ADAPTER_VERSION,
    KNOWLEDGE_SOURCE_BINDING_SCHEMA,
    KNOWLEDGE_SOURCE_EXACT_READER_BOUNDARY,
    KNOWLEDGE_SOURCE_LOCATOR_SCHEMA,
    KNOWLEDGE_SOURCE_RECORD_EVIDENCE_SCHEMA,
    KNOWLEDGE_SOURCE_UNAVAILABLE_OBSERVATION_SCHEMA,
    KnowledgeRecordLocator,
    KnowledgeSourceAdapter,
    KnowledgeSourceAdapterError,
    KnowledgeSourceAuthorityBinding,
    KnowledgeUnavailableReason,
    create_knowledge_source_authority_binding,
    knowledge_source_capability_manifest,
    knowledge_source_registration,
)


OWNER_SCOPE = OwnerScope.for_subject_id("owner_0123456789abcdef0123456789abcdef")
POLICY_EVIDENCE_REF = "sha256:" + "a" * 64
REVIEW_EVIDENCE_REF = "sha256:" + "b" * 64
OBSERVED_AT = "2026-08-13T12:00:00Z"


def _store() -> NativeKnowledgeStore:
    store = NativeKnowledgeStore()
    store.create(owner_id="alice", knowledge_id="one", policy="read", content="first body")
    store.create(owner_id="alice", knowledge_id="two", policy="read", content="second body")
    return store


def _binding(store: NativeKnowledgeStore) -> KnowledgeSourceAuthorityBinding:
    return create_knowledge_source_authority_binding(
        owner_scope=OWNER_SCOPE,
        store=store,
        native_owner_id="alice",
        native_policy="read",
        review_status="accepted",
        policy_evidence_ref=POLICY_EVIDENCE_REF,
        review_evidence_ref=REVIEW_EVIDENCE_REF,
        observed_at=OBSERVED_AT,
    )


def _adapter(store: NativeKnowledgeStore | None = None):
    store = _store() if store is None else store
    binding = _binding(store)
    adapter = KnowledgeSourceAdapter(
        binding=binding,
        store=store,
        native_owner_id="alice",
        native_policy="read",
    )
    expected = {
        "expected_binding_digest": binding.binding_digest,
        "expected_export_digest": binding.export_digest,
        "expected_store_generation": binding.store_generation,
    }
    return adapter, binding, store, expected


def _locator_for_content(
    adapter: KnowledgeSourceAdapter,
    expected: dict[str, object],
    content: str = "first body",
) -> KnowledgeRecordLocator:
    wanted_hash = content_hash(content)
    for item in adapter.discover(limit=99, **expected).items:
        if item.source_version.content_hash == wanted_hash:
            return item.locator
    raise AssertionError("fixture locator was not discovered")


def _assert_error(error: KnowledgeSourceAdapterError, code: str) -> None:
    assert type(error) is KnowledgeSourceAdapterError
    assert error.code == code
    assert error.args == (code,)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert BaseException.__cause__.__get__(error, type(error)) is None
    assert BaseException.__context__.__get__(error, type(error)) is None
    assert error.__suppress_context__ is False
    assert repr(error) == f"KnowledgeSourceAdapterError(code={code!r})"


def _assert_ambient_error(operation, code: str, marker: str) -> KnowledgeSourceAdapterError:
    ambient = RuntimeError(marker)
    try:
        raise ambient
    except RuntimeError:
        with pytest.raises(KnowledgeSourceAdapterError) as caught:
            operation()
    _assert_error(caught.value, code)
    assert caught.value is not ambient
    return caught.value


def test_v3_public_api_schemas_and_exact_types_bind_store_generation():
    adapter, binding, _, expected = _adapter()

    assert KNOWLEDGE_SOURCE_ADAPTER_VERSION == "v3"
    assert KNOWLEDGE_SOURCE_BINDING_SCHEMA.endswith(".v3")
    assert KNOWLEDGE_SOURCE_RECORD_EVIDENCE_SCHEMA.endswith(".v3")
    assert KNOWLEDGE_SOURCE_LOCATOR_SCHEMA.endswith(".v3")
    assert KNOWLEDGE_SOURCE_UNAVAILABLE_OBSERVATION_SCHEMA.endswith(".v3")
    assert KNOWLEDGE_SOURCE_EXACT_READER_BOUNDARY == "native_knowledge_store.read_exact_at_generation"
    assert type(binding.store_generation) is int
    assert binding.store_generation >= 0

    assert "store_generation" in {item.name for item in fields(type(binding))}
    page = adapter.discover(**expected)
    descriptor = page.items[0]
    assert page.store_generation == binding.store_generation
    assert descriptor.locator.store_generation == binding.store_generation
    assert descriptor.evidence.store_generation == binding.store_generation

    for method_name in ("discover", "observe_version", "extract", "read_exact", "observe_unavailable"):
        parameters = inspect.signature(getattr(KnowledgeSourceAdapter, method_name)).parameters
        assert "expected_store_generation" in parameters
        assert parameters["expected_store_generation"].kind is inspect.Parameter.KEYWORD_ONLY
    assert "reason" not in inspect.signature(KnowledgeSourceAdapter.observe_unavailable).parameters

    with pytest.raises(KnowledgeSourceAdapterError) as caught:
        KnowledgeSourceAuthorityBinding(
            owner_scope=binding.owner_scope,
            owner_ref=binding.owner_ref,
            policy_ref=binding.policy_ref,
            export_digest=binding.export_digest,
            store_generation=True,
            observed_at=binding.observed_at,
            policy_evidence_ref=binding.policy_evidence_ref,
            review_evidence_ref=binding.review_evidence_ref,
            adapter_id=binding.adapter_id,
            adapter_version=binding.adapter_version,
            adapter_generation=binding.adapter_generation,
        )
    _assert_error(caught.value, "invalid_authority")


def test_v3_binding_and_construction_capture_one_exact_generation(monkeypatch):
    store = _store()
    original_capture = NativeKnowledgeStore.capture
    captures: list[KnowledgeStoreSnapshot] = []

    def counted_capture(self, **kwargs):
        captured = original_capture(self, **kwargs)
        captures.append(captured)
        return captured

    monkeypatch.setattr(NativeKnowledgeStore, "capture", counted_capture)
    binding = _binding(store)
    assert len(captures) == 1
    assert binding.store_generation == captures[0].generation == store.generation

    object.__setattr__(captures[0].records[0], "content", "hostile detached mutation")
    adapter = KnowledgeSourceAdapter(
        binding=binding,
        store=store,
        native_owner_id="alice",
        native_policy="read",
    )
    assert len(captures) == 2

    expected = {
        "expected_binding_digest": binding.binding_digest,
        "expected_export_digest": binding.export_digest,
        "expected_store_generation": binding.store_generation,
    }
    original_binding_digest = binding.binding_digest
    object.__setattr__(binding, "binding_digest", "sha256:" + "f" * 64)
    page = adapter.discover(**expected)
    assert page.binding_digest == original_binding_digest

    stale_binding = _binding(store)
    store.create(owner_id="alice", knowledge_id="three", policy="read", content="third body")
    with pytest.raises(KnowledgeSourceAdapterError) as caught:
        KnowledgeSourceAdapter(
            binding=stale_binding,
            store=store,
            native_owner_id="alice",
            native_policy="read",
        )
    _assert_error(caught.value, "invalid_authority")


def test_v3_discovery_extract_and_version_observation_reject_generation_drift(monkeypatch):
    adapter, binding, store, expected = _adapter()
    original_capture = NativeKnowledgeStore.capture
    capture_count = 0

    def counted_capture(self, **kwargs):
        nonlocal capture_count
        capture_count += 1
        return original_capture(self, **kwargs)

    monkeypatch.setattr(NativeKnowledgeStore, "capture", counted_capture)
    page = adapter.discover(**expected)
    descriptor = adapter.observe_version(page.items[0].locator.record_ref, **expected)
    occurrence = adapter.extract(page.items[0].locator.record_ref, **expected)
    assert capture_count == 3
    assert page.store_generation == binding.store_generation
    assert descriptor.locator.store_generation == binding.store_generation
    assert descriptor.evidence.store_generation == binding.store_generation
    assert occurrence.locator.store_generation == binding.store_generation
    assert occurrence.evidence.store_generation == binding.store_generation

    for invalid_generation in (True, -1, binding.store_generation - 1, binding.store_generation + 1, "2"):
        invalid = dict(expected, expected_store_generation=invalid_generation)
        with pytest.raises(KnowledgeSourceAdapterError) as caught:
            adapter.discover(**invalid)
        _assert_error(caught.value, "stale_authority")

    store.create(owner_id="alice", knowledge_id="three", policy="read", content="third body")
    operations = (
        lambda: adapter.discover(**expected),
        lambda: adapter.observe_version(page.items[0].locator.record_ref, **expected),
        lambda: adapter.extract(page.items[0].locator.record_ref, **expected),
    )
    for operation in operations:
        with pytest.raises(KnowledgeSourceAdapterError) as caught:
            operation()
        _assert_error(caught.value, "stale_authority")


def test_v3_exact_read_uses_one_generation_fence_before_content_return(monkeypatch):
    adapter, _, store, expected = _adapter()
    locator = _locator_for_content(adapter, expected)
    original_capture = NativeKnowledgeStore.capture
    original_read = NativeKnowledgeStore.read_exact_at_generation
    capture_count = 0
    read_count = 0

    def counted_capture(self, **kwargs):
        nonlocal capture_count
        capture_count += 1
        return original_capture(self, **kwargs)

    def counted_read(self, **kwargs):
        nonlocal read_count
        read_count += 1
        return original_read(self, **kwargs)

    monkeypatch.setattr(NativeKnowledgeStore, "capture", counted_capture)
    monkeypatch.setattr(NativeKnowledgeStore, "read_exact_at_generation", counted_read)
    exact = adapter.read_exact(locator, **expected)
    assert exact.content == "first body"
    assert exact.content_hash == content_hash(exact.content)
    assert exact.locator.store_generation == expected["expected_store_generation"]
    assert exact.evidence.store_generation == expected["expected_store_generation"]
    assert capture_count == read_count == 1

    monkeypatch.setattr(NativeKnowledgeStore, "capture", original_capture)
    monkeypatch.setattr(NativeKnowledgeStore, "read_exact_at_generation", original_read)
    adapter, _, store, expected = _adapter()
    locator = _locator_for_content(adapter, expected)
    mutated = False

    def capture_then_mutate(self, **kwargs):
        nonlocal mutated
        captured = original_capture(self, **kwargs)
        if not mutated:
            mutated = True
            store.create(owner_id="alice", knowledge_id="three", policy="read", content="third body")
        return captured

    monkeypatch.setattr(NativeKnowledgeStore, "capture", capture_then_mutate)
    with pytest.raises(KnowledgeSourceAdapterError) as caught:
        adapter.read_exact(locator, **expected)
    _assert_error(caught.value, "stale_authority")

    monkeypatch.setattr(NativeKnowledgeStore, "capture", original_capture)
    adapter, _, store, expected = _adapter()
    locator = _locator_for_content(adapter, expected)

    def capture_then_idempotent_create(self, **kwargs):
        captured = original_capture(self, **kwargs)
        store.create(owner_id="alice", knowledge_id="one", policy="read", content="first body")
        return captured

    monkeypatch.setattr(NativeKnowledgeStore, "capture", capture_then_idempotent_create)
    assert adapter.read_exact(locator, **expected).content == "first body"


def test_v3_unavailability_requires_exact_successor_and_derives_reason(monkeypatch):
    adapter, binding, store, expected = _adapter()
    locator = _locator_for_content(adapter, expected)
    store.tombstone(
        owner_id="alice",
        knowledge_id="one",
        policy="read",
        version=locator.version,
        version_id=locator.version_id,
    )
    deleted = adapter.observe_unavailable(locator, **expected)
    assert deleted.reason is KnowledgeUnavailableReason.DELETED
    assert deleted.prior_store_generation == binding.store_generation
    assert deleted.observed_store_generation == binding.store_generation + 1
    assert deleted.prior_locator_digest == locator.locator_digest

    adapter, binding, store, expected = _adapter()
    locator = _locator_for_content(adapter, expected)
    store.create(owner_id="alice", knowledge_id="one", policy="other", content="policy transition")
    changed = adapter.observe_unavailable(locator, **expected)
    assert changed.reason is KnowledgeUnavailableReason.ACCESS_CHANGED
    assert changed.prior_store_generation == binding.store_generation
    assert changed.observed_store_generation == binding.store_generation + 1

    adapter, binding, store, expected = _adapter()
    locator = _locator_for_content(adapter, expected)
    store.create(owner_id="alice", knowledge_id="one", policy="other", content="policy transition")

    def missing_exact(self, **kwargs):
        raise KnowledgeNotFound("owner/content/path must not escape")

    monkeypatch.setattr(NativeKnowledgeStore, "read_exact_at_generation", missing_exact)
    missing = adapter.observe_unavailable(locator, **expected)
    assert missing.reason is KnowledgeUnavailableReason.NOT_FOUND
    assert missing.prior_store_generation == binding.store_generation
    assert missing.observed_store_generation == binding.store_generation + 1


def test_v3_unavailability_rejects_interleaved_second_generation(monkeypatch):
    adapter, _, store, expected = _adapter()
    locator = _locator_for_content(adapter, expected)
    store.create(owner_id="alice", knowledge_id="one", policy="read", content="first body")
    with pytest.raises(KnowledgeSourceAdapterError) as caught:
        adapter.observe_unavailable(locator, **expected)
    _assert_error(caught.value, "stale_authority")

    adapter, _, store, expected = _adapter()
    locator = _locator_for_content(adapter, expected)
    store.create(owner_id="alice", knowledge_id="one", policy="other", content="policy transition")
    store.create(owner_id="alice", knowledge_id="three", policy="read", content="unrelated")
    with pytest.raises(KnowledgeSourceAdapterError) as caught:
        adapter.observe_unavailable(locator, **expected)
    _assert_error(caught.value, "stale_authority")

    adapter, _, store, expected = _adapter()
    locator = _locator_for_content(adapter, expected)
    store.create(owner_id="alice", knowledge_id="one", policy="other", content="policy transition")
    original_successor = NativeKnowledgeStore.capture_exact_successor
    mutated = False

    def successor_then_mutate(self, **kwargs):
        nonlocal mutated
        captured = original_successor(self, **kwargs)
        if not mutated:
            mutated = True
            store.create(owner_id="alice", knowledge_id="three", policy="read", content="unrelated")
        return captured

    monkeypatch.setattr(NativeKnowledgeStore, "capture_exact_successor", successor_then_mutate)
    with pytest.raises(KnowledgeSourceAdapterError) as caught:
        adapter.observe_unavailable(locator, **expected)
    _assert_error(caught.value, "stale_authority")


def test_v3_native_errors_are_fresh_content_free_cause_and_context_free(monkeypatch):
    adapter, _, _, expected = _adapter()
    locator = _locator_for_content(adapter, expected)

    def denied(self, **kwargs):
        raise KnowledgeAccessDenied("alice first body C:/private/token.txt")

    monkeypatch.setattr(NativeKnowledgeStore, "read_exact_at_generation", denied)
    errors = []
    for _ in range(2):
        with pytest.raises(KnowledgeSourceAdapterError) as caught:
            adapter.read_exact(locator, **expected)
        errors.append(caught.value)
        _assert_error(caught.value, "access_denied")
        assert "alice" not in repr(caught.value)
        assert "first body" not in repr(caught.value)
        assert "private" not in repr(caught.value)
    assert errors[0] is not errors[1]

    def unexpected(self, **kwargs):
        raise RuntimeError("secret-shaped private provider response")

    monkeypatch.setattr(NativeKnowledgeStore, "read_exact_at_generation", unexpected)
    with pytest.raises(KnowledgeSourceAdapterError) as caught:
        adapter.read_exact(locator, **expected)
    _assert_error(caught.value, "knowledge_source_adapter_failed")

    with pytest.raises(KnowledgeSourceAdapterError) as caught:
        adapter.observe_version("knowledge:record:" + "f" * 64, **expected)
    _assert_error(caught.value, "record_not_found")

    for fatal in (KeyboardInterrupt(), SystemExit()):
        def stop(self, _fatal=fatal, **kwargs):
            raise _fatal

        monkeypatch.setattr(NativeKnowledgeStore, "read_exact_at_generation", stop)
        with pytest.raises(type(fatal)):
            adapter.read_exact(locator, **expected)


def test_v3_detached_alias_subclass_and_forged_snapshot_attacks_fail_closed(monkeypatch):
    class StoreSubclass(NativeKnowledgeStore):
        pass

    with pytest.raises(KnowledgeSourceAdapterError) as caught:
        _binding(StoreSubclass())
    _assert_error(caught.value, "invalid_authority")

    adapter, _, _, expected = _adapter()
    locator = _locator_for_content(adapter, expected)

    class LocatorSubclass(KnowledgeRecordLocator):
        pass

    with pytest.raises(KnowledgeSourceAdapterError) as caught:
        LocatorSubclass(
            record_ref=locator.record_ref,
            version=locator.version,
            version_id=locator.version_id,
            export_digest=locator.export_digest,
            store_generation=locator.store_generation,
            binding_digest=locator.binding_digest,
            text_range=locator.text_range,
            locator_digest=locator.locator_digest,
            field_ref=locator.field_ref,
            schema=locator.schema,
        )
    _assert_error(caught.value, "invalid_request")

    original_generation = locator.store_generation
    object.__setattr__(locator, "store_generation", original_generation + 1)
    with pytest.raises(KnowledgeSourceAdapterError) as caught:
        adapter.read_exact(locator, **expected)
    _assert_error(caught.value, "invalid_request")

    adapter, _, _, expected = _adapter()
    original_capture = NativeKnowledgeStore.capture

    def forged_capture(self, **kwargs):
        captured = original_capture(self, **kwargs)
        object.__setattr__(captured, "generation", True)
        return captured

    monkeypatch.setattr(NativeKnowledgeStore, "capture", forged_capture)
    with pytest.raises(KnowledgeSourceAdapterError) as caught:
        adapter.discover(**expected)
    _assert_error(caught.value, "invalid_snapshot")

    store = _store()

    def forged_record_capture(self, **kwargs):
        captured = original_capture(self, **kwargs)
        object.__setattr__(captured.records[0], "content", "forged content")
        return captured

    monkeypatch.setattr(NativeKnowledgeStore, "capture", forged_record_capture)
    with pytest.raises(KnowledgeSourceAdapterError) as caught:
        _binding(store)
    _assert_error(caught.value, "invalid_snapshot")


def test_v3_preserves_locator_membership_empty_relations_and_no_caller_reason():
    adapter, binding, _, expected = _adapter()
    page = adapter.discover(**expected)
    descriptor = page.items[0]
    observed = adapter.observe_version(descriptor.locator.record_ref, **expected)
    occurrence = adapter.extract(descriptor.locator.record_ref, **expected)

    assert observed == descriptor
    assert descriptor.source.source_kind is SourceKind.OTHER
    assert descriptor.source.content_policy is ContentPolicy.REFERENCE_ONLY
    assert descriptor.source_version.revision_ref == descriptor.locator.version_id
    assert descriptor.source_version.version_observed_at == binding.observed_at
    assert descriptor.locator.record_ref == descriptor.evidence.record_ref == descriptor.source.canonical_ref
    assert descriptor.locator.store_generation == descriptor.evidence.store_generation == binding.store_generation
    assert occurrence.relations == ()
    assert occurrence.evidence.relation_count == 0
    assert occurrence.chunk.content is None
    assert occurrence.chunk.content_policy is ContentPolicy.REFERENCE_ONLY
    for value in (page, descriptor, occurrence, descriptor.locator, descriptor.evidence):
        assert "first body" not in repr(value)
        assert "second body" not in repr(value)
        assert "alice" not in repr(value)

    with pytest.raises(TypeError):
        adapter.observe_unavailable(
            descriptor.locator,
            reason=KnowledgeUnavailableReason.DELETED,
            **expected,
        )

    other_store = NativeKnowledgeStore()
    other_store.create(owner_id="alice", knowledge_id="one", policy="read", content="foreign body")
    other, _, _, other_expected = _adapter(other_store)
    foreign_locator = _locator_for_content(other, other_expected, "foreign body")
    with pytest.raises(KnowledgeSourceAdapterError) as caught:
        adapter.read_exact(foreign_locator, **expected)
    _assert_error(caught.value, "invalid_request")


def test_v3_uses_only_generation_fenced_store_calls_and_has_zero_external_effects(monkeypatch):
    source = inspect.getsource(knowledge_module)
    tree = ast.parse(source)
    called_store_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and ast.unparse(node.func.value) in {"store", "self._store"}
        )
    }
    assert called_store_attributes == {
        "capture",
        "capture_exact_successor",
        "read_exact_at_generation",
    }
    assert "._lock" not in source
    assert "._generation" not in source
    assert "._versions" not in source
    assert "._tombstones" not in source
    for forbidden in ("personal_docs", "memory", "plugins", "provider", "filesystem", "network"):
        assert f"import {forbidden}" not in source

    adapter, _, _, expected = _adapter()
    locator = _locator_for_content(adapter, expected)

    def forbidden_call(self, **kwargs):
        raise AssertionError("forbidden legacy or mutation call")

    monkeypatch.setattr(NativeKnowledgeStore, "export", forbidden_call)
    monkeypatch.setattr(NativeKnowledgeStore, "read_exact", forbidden_call)
    monkeypatch.setattr(NativeKnowledgeStore, "create", forbidden_call)
    monkeypatch.setattr(NativeKnowledgeStore, "tombstone", forbidden_call)
    assert adapter.discover(**expected).store_generation == expected["expected_store_generation"]
    assert adapter.read_exact(locator, **expected).content == "first body"

    manifest = knowledge_source_capability_manifest()
    registration = knowledge_source_registration()
    assert manifest.adapter_version == "v3"
    assert manifest.productive_default_enabled is False
    assert manifest.content_policy is ContentPolicy.REFERENCE_ONLY
    assert registration.factory is None
    assert registration.manifest == manifest


def test_v3_public_errors_clear_raw_ambient_except_context():
    adapter, binding, store, expected = _adapter()
    invalid_binding = {
        "owner_scope": binding.owner_scope,
        "owner_ref": binding.owner_ref,
        "policy_ref": binding.policy_ref,
        "export_digest": binding.export_digest,
        "store_generation": True,
        "observed_at": binding.observed_at,
        "policy_evidence_ref": binding.policy_evidence_ref,
        "review_evidence_ref": binding.review_evidence_ref,
        "adapter_id": binding.adapter_id,
        "adapter_version": binding.adapter_version,
        "adapter_generation": binding.adapter_generation,
    }
    operations = (
        ("invalid_request", lambda: adapter.discover(cursor=-1, **expected)),
        ("invalid_request", lambda: adapter.observe_version("invalid", **expected)),
        ("invalid_request", lambda: adapter.extract("invalid", **expected)),
        ("invalid_request", lambda: adapter.read_exact(object(), **expected)),
        ("invalid_request", lambda: adapter.observe_unavailable(object(), **expected)),
        (
            "invalid_authority",
            lambda: create_knowledge_source_authority_binding(
                owner_scope=OWNER_SCOPE,
                store=store,
                native_owner_id="alice",
                native_policy="read",
                review_status="rejected",
                policy_evidence_ref=POLICY_EVIDENCE_REF,
                review_evidence_ref=REVIEW_EVIDENCE_REF,
                observed_at=OBSERVED_AT,
            ),
        ),
        ("invalid_authority", lambda: KnowledgeSourceAuthorityBinding(**invalid_binding)),
        (
            "invalid_authority",
            lambda: KnowledgeSourceAdapter(
                binding=binding,
                store=object(),
                native_owner_id="alice",
                native_policy="read",
            ),
        ),
    )

    errors = [
        _assert_ambient_error(operation, code, f"ambient-private-{index}")
        for index, (code, operation) in enumerate(operations)
    ]
    assert len({id(error) for error in errors}) == len(errors)


def test_v3_public_errors_clear_raw_finally_and_with_context():
    adapter, _, _, expected = _adapter()
    errors: list[KnowledgeSourceAdapterError] = []

    finally_ambient = RuntimeError("finally-private-context")
    try:
        try:
            raise finally_ambient
        finally:
            with pytest.raises(KnowledgeSourceAdapterError) as caught:
                adapter.discover(cursor=-1, **expected)
            errors.append(caught.value)
    except RuntimeError as propagated:
        assert propagated is finally_ambient
    _assert_error(errors[-1], "invalid_request")

    with_ambient = RuntimeError("with-private-context")

    class CleanupProbe:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            assert exc_type is RuntimeError
            assert exc_value is with_ambient
            assert traceback is with_ambient.__traceback__
            try:
                raise RuntimeError("nested-private-context")
            except RuntimeError:
                with pytest.raises(KnowledgeSourceAdapterError) as caught:
                    adapter.observe_version("invalid", **expected)
            errors.append(caught.value)
            return True

    with CleanupProbe():
        raise with_ambient
    _assert_error(errors[-1], "invalid_request")
    assert errors[0] is not errors[1]


def test_v3_native_error_mapping_never_retains_ambient_or_native_exception(monkeypatch):
    adapter, _, _, expected = _adapter()
    locator = _locator_for_content(adapter, expected)
    cases = (
        (KnowledgeAccessDenied("native-private-access"), "access_denied"),
        (KnowledgeNotFound("native-private-not-found"), "record_not_found"),
        (KnowledgeTombstoned("native-private-tombstone"), "tombstoned"),
        (KnowledgeGenerationMismatch(), "stale_authority"),
        (RuntimeError("native-private-unknown"), "knowledge_source_adapter_failed"),
    )
    errors = []

    for index, (native_error, code) in enumerate(cases):
        def fail_native(self, _native_error=native_error, **kwargs):
            raise _native_error

        monkeypatch.setattr(NativeKnowledgeStore, "read_exact_at_generation", fail_native)
        error = _assert_ambient_error(
            lambda: adapter.read_exact(locator, **expected),
            code,
            f"ambient-private-native-{index}",
        )
        assert error is not native_error
        assert "private" not in repr(error)
        errors.append(error)

    assert len({id(error) for error in errors}) == len(errors)
