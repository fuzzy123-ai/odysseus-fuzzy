"""Focused contract tests for the default-off Planning composite source."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from dataclasses import replace
from src.unified_source_index_adapters import (
    AdapterCapability, AdapterScope, DiscoveryItem, DiscoveryPage,
    ExtractionProfile, ExtractionResult, SourceAdapterError,
    validate_adapter_output,
)
from src.unified_source_index_contract import (
    IndexJobKind, IndexJobRecord, SourceScope, TextRangeLocator,
)
from src.unified_source_index_jobs import UnifiedSourceIndexJobRuntime
from src.unified_source_index_lexical import UnifiedSourceIndexLexicalRetriever
from src.unified_source_index_query_contract import LexicalMatchMode, LexicalQuery
from src.unified_source_index_sqlite import SQLiteUnifiedSourceIndexStore
from src.planning_definition_contract import compute_roadmap_content_hash
from src.planning_mcp_service import PlanningMcpService
from src.planning_revision_store import PlanningRevisionStore
from src.unified_source_index_contract import Classification, ContentPolicy, SourceKind
from src.unified_source_index_owner_scope import OwnerScope
from src.unified_source_index_source_capability import (
    OwnerScopeRequirement,
    ProviderConstraint,
    QueryCapability,
    SourceAdapterOperation,
)
from src.unified_source_index_source_registry import (
    SourceAdapterRegistry,
    SourceAdapterRegistryError,
)
from src.unified_source_index_sources.planning import (
    PLANNING_SOURCE_ADAPTER_ID,
    PlanningCharacterRangeRequest, PlanningDiscoveryPage,
    PlanningSourceDescriptor, PlanningSourceOccurrence,
    planning_source_registration,
)
from src.unified_source_index_sources.planning_composite import (
    MAX_PLANNING_COMPOSITE_SOURCES,
    PLANNING_COMPOSITE_SOURCE_ADAPTER_ID,
    PLANNING_COMPOSITE_SOURCE_ADAPTER_VERSION,
    PLANNING_COMPOSITE_SOURCE_EXACT_READER_BOUNDARY,
    PlanningCompositeDiscoveryPage, PlanningCompositeSourceAdapter,
    PlanningCompositeSourceAdapterError, PlanningCompositeSourceDescriptor,
    PlanningCompositeSourceRoute,
    planning_composite_source_capability_manifest,
    planning_composite_source_registration,
)
from src.unified_source_index_sources.planning_markdown import (
    PLANNING_MARKDOWN_SOURCE_ADAPTER_ID,
    PlanningMarkdownCharacterRangeRequest, PlanningMarkdownDiscoveryPage,
    PlanningMarkdownSourceDescriptor, PlanningMarkdownSourceOccurrence,
)


OWNER_REF = "o"
OWNER_SCOPE = OwnerScope("owner:owner_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
OBSERVED_AT = "2026-09-07T12:00:00Z"


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _roadmap(index: int, source_refs: list[str]) -> dict[str, object]:
    node_id = f"node-{index}"
    roadmap: dict[str, object] = {
        "roadmap_id": f"r{index}",
        "project_id": "p",
        "revision": 1,
        "content_hash": "sha256:" + "0" * 64,
        "revision_state": "approved",
        "title": f"Roadmap {index}",
        "objective": f"Composite JSON keyword jsonterm{index}.",
        "assumptions": [],
        "constraints": [],
        "nodes": [{
            "node_id": node_id,
            "kind": "work",
            "title": f"Node {index}",
            "objective": "Ship one bounded unit.",
            "depends_on": [],
            "gate_ids": [],
            "deliverables": [],
            "allowed_paths": [],
            "blocked_paths": [],
            "capability_requirements": [],
            "verification_rule_ids": ["tests"],
        }],
        "edges": [],
        "gates": [],
        "done_contract": {
            "required_node_ids": [node_id],
            "required_gate_ids": [],
            "verification_rules": [{
                "rule_id": "tests", "kind": "test", "description": "Tests pass."
            }],
            "completion_rule": "all_required_nodes_and_gates",
        },
        "source_refs": source_refs,
        "created_at": "2026-09-07T10:00:00Z",
        "updated_at": "2026-09-07T11:00:00Z",
    }
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    return roadmap


def _service(tmp_path: Path, *, json_count: int = 2, markdown_count: int = 2):
    roadmaps = []
    selections = []
    for index in range(json_count):
        refs: list[str] = []
        if index < markdown_count:
            source_ref = "docs/plans/a.md"
            raw = b"Markdown keyword mdterm0."
            path = tmp_path.joinpath(*source_ref.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            refs.append(source_ref)
            selections.append({
                "owner_ref": OWNER_REF,
                "project_id": "p",
                "roadmap_id": f"r{index}",
                "source_ref": source_ref,
                "expected_body_sha256": _sha(raw),
            })
        roadmaps.append(_roadmap(index, refs))
    definition = {
        "schema_id": "odysseus.planning.definition.v2",
        "project": {
            "project_id": "p",
            "title": "Composite fixture",
            "objective": "Exercise both accepted Planning sources.",
            "scope": {"in": ["Planning"], "out": ["runtime state"]},
            "constraints": [],
            "roadmap_refs": [item["roadmap_id"] for item in roadmaps],
            "latest_approved_revision": {
                item["roadmap_id"]: {
                    "revision": item["revision"],
                    "content_hash": item["content_hash"],
                }
                for item in roadmaps
            },
            "draft_refs": [],
        },
        "roadmaps": roadmaps,
    }
    planning_ref = "docs/plans/p.json"
    planning_path = tmp_path.joinpath(*planning_ref.split("/"))
    planning_path.parent.mkdir(parents=True, exist_ok=True)
    planning_path.write_text(json.dumps(definition), encoding="utf-8")
    store = PlanningRevisionStore(
        [(OWNER_REF, definition, planning_ref)],
        cursor_secret=b"planning-composite-test-secret",
    )
    return PlanningMcpService(
        tmp_path,
        definition_store=store,
        definition_owner=OWNER_REF,
        owner_markdown_selections=tuple(selections),
    )


def _adapter(tmp_path: Path, **counts) -> PlanningCompositeSourceAdapter:
    return PlanningCompositeSourceAdapter(
        service=_service(tmp_path, **counts),
        owner_scope=OWNER_SCOPE,
        expected_owner_ref=OWNER_REF,
        observed_at=OBSERVED_AT,
    )


def test_manifest_is_default_off_factory_free_and_alternative_for_planning() -> None:
    manifest = planning_composite_source_capability_manifest()
    assert manifest.adapter_id == PLANNING_COMPOSITE_SOURCE_ADAPTER_ID == "planning.approved_sources"
    assert manifest.adapter_version == PLANNING_COMPOSITE_SOURCE_ADAPTER_VERSION == "v1"
    assert manifest.domain_id == "planning"
    assert manifest.source_kind is SourceKind.PLANNING
    assert manifest.classification_ceiling is Classification.PRIVATE
    assert manifest.content_policy is ContentPolicy.INLINE_LOCAL
    assert manifest.owner_scope_requirement is OwnerScopeRequirement.IMMUTABLE_OPAQUE
    assert manifest.provider_constraint is ProviderConstraint.LOCAL_ACCEPTED_BOUNDARY
    assert manifest.query_capability is QueryCapability.EXACT_READER
    assert set(manifest.operations) == set(SourceAdapterOperation)
    assert manifest.exact_reader_boundary == PLANNING_COMPOSITE_SOURCE_EXACT_READER_BOUNDARY
    assert manifest.productive_default_enabled is False
    registration = planning_composite_source_registration()
    assert registration.manifest == manifest
    assert registration.factory is None
    assert SourceAdapterRegistry((registration,)).for_domain("planning") == manifest
    with pytest.raises(SourceAdapterRegistryError, match="duplicate domain"):
        SourceAdapterRegistry((planning_source_registration(), registration))


def test_discovery_preserves_exact_child_dtos_and_deterministic_order(tmp_path) -> None:
    adapter = _adapter(tmp_path)
    binding = adapter.binding
    page = adapter.discover(
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    assert len(page.items) == 4
    keys = [
        (
            item.descriptor.source.canonical_ref,
            item.descriptor.source.provider_ref,
            item.descriptor.source.source_id,
            item.descriptor.source_version.source_version_id,
        )
        for item in page.items
    ]
    assert keys == sorted(keys)
    assert {type(item.descriptor) for item in page.items} == {
        PlanningSourceDescriptor,
        PlanningMarkdownSourceDescriptor,
    }
    assert {item.route.child_adapter_id for item in page.items} == {
        PLANNING_SOURCE_ADAPTER_ID,
        PLANNING_MARKDOWN_SOURCE_ADAPTER_ID,
    }


def test_declared_124_budget_and_large_real_service_merge(tmp_path) -> None:
    adapter = _adapter(tmp_path, json_count=50, markdown_count=24)
    binding = adapter.binding
    page = adapter.discover(
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    assert MAX_PLANNING_COMPOSITE_SOURCES == 124
    assert len(page.items) == 74


def _all(adapter: PlanningCompositeSourceAdapter):
    binding = adapter.binding
    page = adapter.discover(
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    return binding, page.items


def test_typed_routes_preserve_child_results_and_exact_reads(tmp_path) -> None:
    adapter = _adapter(tmp_path, json_count=1, markdown_count=1)
    binding, items = _all(adapter)
    json_item = next(item for item in items if type(item.descriptor) is PlanningSourceDescriptor)
    markdown_item = next(
        item for item in items if type(item.descriptor) is PlanningMarkdownSourceDescriptor
    )
    json_observed = adapter.observe_version(
        json_item.route,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    markdown_observed = adapter.observe_version(
        markdown_item.route,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    assert type(json_observed) is PlanningSourceDescriptor
    assert type(markdown_observed) is PlanningMarkdownSourceDescriptor
    assert json_observed.source.provider_ref == PLANNING_SOURCE_ADAPTER_ID
    assert markdown_observed.source.provider_ref == PLANNING_MARKDOWN_SOURCE_ADAPTER_ID
    json_occurrence = adapter.extract(
        json_item.route,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    markdown_occurrence = adapter.extract(
        markdown_item.route,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    assert type(json_occurrence) is PlanningSourceOccurrence
    assert type(markdown_occurrence) is PlanningMarkdownSourceOccurrence
    json_request = PlanningCharacterRangeRequest(
        json_item.descriptor.projection_locator,
        json_occurrence.chunks[0].locator,
    )
    markdown_request = PlanningMarkdownCharacterRangeRequest(
        markdown_item.descriptor.body_locator,
        TextRangeLocator(0, len("Markdown keyword mdterm0.")),
    )
    assert adapter.read_exact(
        json_request,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    ).content == json_occurrence.chunks[0].content
    assert adapter.read_exact(
        markdown_request,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    ).content == "Markdown keyword mdterm0."


def test_markdown_unavailable_never_infers_absence(tmp_path) -> None:
    adapter = _adapter(tmp_path, json_count=1, markdown_count=1)
    binding, items = _all(adapter)
    route = next(
        item.route for item in items
        if item.route.child_adapter_id == PLANNING_MARKDOWN_SOURCE_ADAPTER_ID
    )
    with pytest.raises(
        PlanningCompositeSourceAdapterError, match="unavailable_state_unproven"
    ):
        adapter.observe_unavailable(
            route,
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )


def test_nonselected_markdown_drift_after_json_read_is_stale(
    tmp_path, monkeypatch
) -> None:
    adapter = _adapter(tmp_path, json_count=1, markdown_count=1)
    binding, items = _all(adapter)
    json_route = next(
        item.route for item in items
        if item.route.child_adapter_id == PLANNING_SOURCE_ADAPTER_ID
    )
    service = adapter._service
    original = service.read_owner_source_projection

    def mutate_markdown_after_json(*args, **kwargs):
        result = original(*args, **kwargs)
        (tmp_path / "docs" / "plans" / "a.md").write_text(
            "changed after selected JSON read", encoding="utf-8"
        )
        return result

    monkeypatch.setattr(service, "read_owner_source_projection", mutate_markdown_after_json)
    with pytest.raises(PlanningCompositeSourceAdapterError, match="stale_authority"):
        adapter.extract(
            json_route,
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )


class _BehaviorBomb:
    def __init__(self):
        self.calls = 0

    def __eq__(self, other):
        self.calls += 1
        raise AssertionError("hostile equality invoked")

    @property
    def logical_ref(self):
        self.calls += 1
        raise AssertionError("hostile locator getter invoked")


def test_hostile_nested_request_and_retained_owner_fail_before_behavior(tmp_path) -> None:
    adapter = _adapter(tmp_path, json_count=1, markdown_count=1)
    binding, items = _all(adapter)
    item = next(value for value in items if type(value.descriptor) is PlanningSourceDescriptor)
    occurrence = adapter.extract(
        item.route,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    request = PlanningCharacterRangeRequest(
        item.descriptor.projection_locator, occurrence.chunks[0].locator
    )
    hostile_locator = _BehaviorBomb()
    object.__setattr__(request, "projection_locator", hostile_locator)
    with pytest.raises(PlanningCompositeSourceAdapterError, match="invalid_request"):
        adapter.read_exact(
            request,
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )
    assert hostile_locator.calls == 0

    hostile_owner = _BehaviorBomb()
    object.__setattr__(adapter, "_owner_ref", hostile_owner)
    with pytest.raises(PlanningCompositeSourceAdapterError, match="stale_authority"):
        _ = adapter.binding
    assert hostile_owner.calls == 0


def test_route_repr_never_emits_caller_controlled_ref() -> None:
    sentinel = "sentinel-private-route-text"
    route = PlanningCompositeSourceRoute(PLANNING_SOURCE_ADAPTER_ID, sentinel)
    assert sentinel not in repr(route)


def test_child_page_boundary_rejects_hostile_overcount_before_traversal(
    tmp_path, monkeypatch
) -> None:
    from src.unified_source_index_sources.planning import PlanningSourceAdapter

    adapter = _adapter(tmp_path, json_count=1, markdown_count=1)
    binding = adapter.binding
    bomb = _BehaviorBomb()

    class HostilePage:
        @property
        def items(self):
            bomb.calls += 1
            raise AssertionError("hostile page getter invoked")

    monkeypatch.setattr(PlanningSourceAdapter, "discover", lambda *args, **kwargs: HostilePage())
    with pytest.raises(PlanningCompositeSourceAdapterError, match="invalid_snapshot"):
        adapter.discover(
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )
    assert bomb.calls == 0

    forged = object.__new__(PlanningDiscoveryPage)
    object.__setattr__(forged, "items", tuple(bomb for _ in range(101)))
    object.__setattr__(forged, "next_cursor", None)
    object.__setattr__(forged, "snapshot_digest", binding.json_binding.snapshot_digest)
    object.__setattr__(forged, "binding_digest", binding.json_binding.binding_digest)
    monkeypatch.setattr(PlanningSourceAdapter, "discover", lambda *args, **kwargs: forged)
    with pytest.raises(PlanningCompositeSourceAdapterError, match="invalid_snapshot"):
        adapter.discover(
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )
    assert bomb.calls == 0


def test_binding_and_page_dtos_are_detached_and_reject_duplicates(tmp_path) -> None:
    adapter = _adapter(tmp_path, json_count=1, markdown_count=1)
    binding, items = _all(adapter)
    assert not hasattr(binding, "__dict__")
    assert adapter.binding == binding
    assert adapter.binding is not binding
    rebuilt = PlanningCompositeSourceDescriptor(items[0].route, items[0].descriptor)
    assert rebuilt == items[0]
    assert rebuilt.descriptor is not items[0].descriptor
    with pytest.raises(PlanningCompositeSourceAdapterError, match="invalid_snapshot"):
        PlanningCompositeDiscoveryPage(
            (items[0], items[0]), None,
            binding.snapshot_digest, binding.binding_digest,
        )
    foreign = object.__new__(PlanningDiscoveryPage)
    json_items = tuple(
        item.descriptor for item in items
        if type(item.descriptor) is PlanningSourceDescriptor
    )
    object.__setattr__(foreign, "items", json_items)
    object.__setattr__(foreign, "next_cursor", None)
    object.__setattr__(foreign, "snapshot_digest", "sha256:" + "f" * 64)
    object.__setattr__(foreign, "binding_digest", "sha256:" + "e" * 64)
    from src.unified_source_index_sources.planning import PlanningSourceAdapter
    original = PlanningSourceAdapter.discover
    try:
        PlanningSourceAdapter.discover = lambda *args, **kwargs: foreign
        with pytest.raises(PlanningCompositeSourceAdapterError, match="invalid_snapshot"):
            adapter.discover(
                expected_binding_digest=binding.binding_digest,
                expected_snapshot_digest=binding.snapshot_digest,
            )
    finally:
        PlanningSourceAdapter.discover = original


class _OneChildGenericAdapter:
    def __init__(self, descriptor, occurrence, profile_ref):
        self.descriptor = descriptor
        self.occurrence = occurrence
        self.profile_ref = profile_ref
        self.capability = AdapterCapability(
            adapter_id=descriptor.source.provider_ref,
            adapter_version="v1",
            owner_scope=descriptor.source.owner_scope,
            domain_kind="planning",
            source_kind=SourceKind.PLANNING,
            content_policy=ContentPolicy.INLINE_LOCAL,
            classification_ceiling=Classification.PRIVATE,
            supports_exact_reads=True,
            max_discovery_page=1,
            max_extract_items=1000,
        )

    def describe_capability(self):
        return self.capability

    def discover(self, scope, *, cursor, limit, time_budget_ms):
        assert type(scope) is AdapterScope
        if cursor:
            return DiscoveryPage(())
        return DiscoveryPage((
            DiscoveryItem(
                self.descriptor.source,
                self.descriptor.source_version.content_hash,
            ),
        ))

    def observe_version(self, source_ref):
        assert source_ref == self.descriptor.source.ref()
        return self.descriptor.source_version

    def extract(self, source_version, profile: ExtractionProfile):
        assert source_version == self.descriptor.source_version
        assert profile.profile_ref == self.profile_ref
        return ExtractionResult(
            source_version,
            self.occurrence.chunks,
            self.occurrence.entities,
            self.occurrence.relations,
        )

    def read_exact(self, request, policy_context):
        raise AssertionError("outside test-local ingestion proof")

    def observe_unavailable(self, source_ref, reason):
        raise AssertionError("outside test-local ingestion proof")


def test_generic_mismatch_and_two_job_store_lexical_compatibility(tmp_path) -> None:
    adapter = _adapter(tmp_path / "source", json_count=1, markdown_count=1)
    binding, items = _all(adapter)
    occurrences = {
        item.route.child_adapter_id: adapter.extract(
            item.route,
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )
        for item in items
    }
    json_item = next(item for item in items if type(item.descriptor) is PlanningSourceDescriptor)
    json_occurrence = occurrences[PLANNING_SOURCE_ADAPTER_ID]
    composite_generic = AdapterCapability(
        adapter_id=PLANNING_COMPOSITE_SOURCE_ADAPTER_ID,
        adapter_version="v1",
        owner_scope=OWNER_SCOPE.value,
        domain_kind="planning",
        source_kind=SourceKind.PLANNING,
        content_policy=ContentPolicy.INLINE_LOCAL,
        classification_ceiling=Classification.PRIVATE,
        supports_exact_reads=True,
    )
    with pytest.raises(SourceAdapterError, match="provider identity"):
        validate_adapter_output(
            composite_generic,
            AdapterScope(OWNER_SCOPE.value, Classification.PRIVATE),
            DiscoveryItem(
                json_item.descriptor.source,
                json_item.descriptor.source_version.content_hash,
            ),
            json_item.descriptor.source_version,
            ExtractionResult(
                json_item.descriptor.source_version,
                json_occurrence.chunks,
                json_occurrence.entities,
                json_occurrence.relations,
            ),
        )

    profile_ref = planning_composite_source_capability_manifest().job_profile_ref
    store = SQLiteUnifiedSourceIndexStore(tmp_path / "compat.db")
    runtime = UnifiedSourceIndexJobRuntime(store)
    for index, item in enumerate(items):
        bridge = _OneChildGenericAdapter(
            item.descriptor,
            occurrences[item.route.child_adapter_id],
            profile_ref,
        )
        job = IndexJobRecord.create(
            job_kind=IndexJobKind.EXTRACTION,
            source_scope=SourceScope.create((item.descriptor.source.policy_evidence(),)),
            request_ref=f"request:composite-{index}",
            profile_ref=profile_ref,
            max_items=1,
            time_budget_ms=1000,
        )
        runtime.register(job)
        result = runtime.run_to_completion(
            job.job_id,
            owner_scope=OWNER_SCOPE.value,
            worker_id=f"worker.composite-{index}",
            adapter=bridge,
            now="2026-09-07T12:30:00Z",
        )
        assert result.terminal is True

    retriever = UnifiedSourceIndexLexicalRetriever(store)
    for query_text, item in (
        ("jsonterm0", json_item),
        ("mdterm0", next(
            value for value in items
            if type(value.descriptor) is PlanningMarkdownSourceDescriptor
        )),
    ):
        page = retriever.search(LexicalQuery(
            owner_scope=OWNER_SCOPE.value,
            query_text=query_text,
            match_mode=LexicalMatchMode.TOKEN,
            classification_ceiling=Classification.PRIVATE,
            source_ids=(item.descriptor.source.source_id,),
        ))
        assert page.items
        assert {result.source_ref.record_id for result in page.items} == {
            item.descriptor.source.source_id
        }
        assert {result.source_version_ref.record_id for result in page.items} == {
            item.descriptor.source_version.source_version_id
        }
        expected_chunks = {
            chunk.chunk_id for chunk in occurrences[item.route.child_adapter_id].chunks
        }
        assert {result.chunk_ref.record_id for result in page.items} <= expected_chunks


def test_public_child_dtos_fill_exact_composite_124_page_budget(tmp_path) -> None:
    from src.unified_source_index_sources.planning import PlanningSourceAdapter
    from src.unified_source_index_sources.planning_markdown import (
        PlanningMarkdownSourceAdapter,
    )

    json_adapter = PlanningSourceAdapter(
        service=_service(tmp_path / "json", json_count=100, markdown_count=0),
        owner_scope=OWNER_SCOPE,
        expected_owner_ref=OWNER_REF,
        observed_at=OBSERVED_AT,
    )
    json_binding = json_adapter.binding
    json_page = json_adapter.discover(
        expected_binding_digest=json_binding.binding_digest,
        expected_snapshot_digest=json_binding.snapshot_digest,
        limit=100,
    )
    markdown_adapter = PlanningMarkdownSourceAdapter(
        service=_service(tmp_path / "markdown", json_count=24, markdown_count=24),
        owner_scope=OWNER_SCOPE,
        expected_owner_ref=OWNER_REF,
        observed_at=OBSERVED_AT,
    )
    markdown_binding = markdown_adapter.binding
    markdown_page = markdown_adapter.discover(
        expected_binding_digest=markdown_binding.binding_digest,
        expected_snapshot_digest=markdown_binding.snapshot_digest,
        limit=24,
    )
    items = tuple(
        PlanningCompositeSourceDescriptor(
            PlanningCompositeSourceRoute(
                descriptor.source.provider_ref,
                descriptor.evidence.logical_ref,
            ),
            descriptor,
        )
        for descriptor in (*json_page.items, *markdown_page.items)
    )
    page = PlanningCompositeDiscoveryPage(
        items,
        None,
        "sha256:" + "a" * 64,
        "sha256:" + "b" * 64,
    )
    assert len(page.items) == MAX_PLANNING_COMPOSITE_SOURCES == 124
    with pytest.raises(PlanningCompositeSourceAdapterError, match="invalid_snapshot"):
        PlanningCompositeDiscoveryPage(
            page.items + (page.items[0],),
            None,
            page.snapshot_digest,
            page.binding_digest,
        )


def test_constructor_cross_capture_drift_is_stale(tmp_path, monkeypatch) -> None:
    service = _service(tmp_path, json_count=1, markdown_count=1)
    original = service.capture_owner_source_snapshot
    calls = {"count": 0}

    def drift_after_child_construction(*args, **kwargs):
        calls["count"] += 1
        result = original(*args, **kwargs)
        if calls["count"] > 1:
            result["snapshot_digest"] = "sha256:" + "f" * 64
        return result

    monkeypatch.setattr(
        service, "capture_owner_source_snapshot", drift_after_child_construction
    )
    with pytest.raises(PlanningCompositeSourceAdapterError, match="stale_authority"):
        PlanningCompositeSourceAdapter(
            service=service,
            owner_scope=OWNER_SCOPE,
            expected_owner_ref=OWNER_REF,
            observed_at=OBSERVED_AT,
        )


def test_same_value_binding_replacement_remains_valid(tmp_path) -> None:
    adapter = _adapter(tmp_path, json_count=1, markdown_count=1)
    binding = adapter.binding
    object.__setattr__(adapter, "_binding", replace(binding))
    page = adapter.discover(
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    assert len(page.items) == 2


@pytest.mark.parametrize("drift_when", ["before", "after"])
def test_nonselected_json_drift_fences_markdown_read(
    tmp_path, monkeypatch, drift_when
) -> None:
    adapter = _adapter(tmp_path, json_count=1, markdown_count=1)
    binding, items = _all(adapter)
    markdown_route = next(
        item.route for item in items
        if item.route.child_adapter_id == PLANNING_MARKDOWN_SOURCE_ADAPTER_ID
    )
    planning_path = tmp_path / "docs" / "plans" / "p.json"
    if drift_when == "before":
        planning_path.write_text("{}", encoding="utf-8")
    else:
        service = adapter._service
        original = service.read_owner_markdown_body

        def mutate_json_after_markdown(*args, **kwargs):
            result = original(*args, **kwargs)
            planning_path.write_text("{}", encoding="utf-8")
            return result

        monkeypatch.setattr(service, "read_owner_markdown_body", mutate_json_after_markdown)
    with pytest.raises(PlanningCompositeSourceAdapterError, match="stale_authority"):
        adapter.extract(
            markdown_route,
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )


def test_empty_markdown_remains_graph_free_with_zero_chunks(tmp_path) -> None:
    from tests.test_planning_mcp_service import _owner_markdown_test_service

    service, _store, _definition, _body_path = _owner_markdown_test_service(
        tmp_path, b""
    )
    adapter = PlanningCompositeSourceAdapter(
        service=service,
        owner_scope=OWNER_SCOPE,
        expected_owner_ref="owner-1",
        observed_at=OBSERVED_AT,
    )
    binding, items = _all(adapter)
    markdown = next(
        item for item in items
        if item.route.child_adapter_id == PLANNING_MARKDOWN_SOURCE_ADAPTER_ID
    )
    occurrence = adapter.extract(
        markdown.route,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    assert occurrence.chunks == ()
    assert occurrence.entities == ()
    assert occurrence.relations == ()


def test_json_proven_unavailable_is_delegated(tmp_path) -> None:
    from src.unified_source_index_sources.planning import PlanningSourceAdapter

    roadmap = _roadmap(0, [])
    roadmap["revision_state"] = "draft"
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    definition = {
        "schema_id": "odysseus.planning.definition.v2",
        "project": {
            "project_id": "p",
            "title": "Draft only",
            "objective": "Prove unavailable state.",
            "scope": {"in": ["Planning"], "out": ["runtime state"]},
            "constraints": [],
            "roadmap_refs": ["r0"],
            "latest_approved_revision": {},
            "draft_refs": [{"draft_id": "d0", "roadmap_id": "r0", "base_revision": 1, "base_hash": roadmap["content_hash"]}],
        },
        "roadmaps": [roadmap],
    }
    planning_ref = "docs/plans/p.json"
    path = tmp_path.joinpath(*planning_ref.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(definition), encoding="utf-8")
    store = PlanningRevisionStore(
        [(OWNER_REF, definition, planning_ref)],
        cursor_secret=b"planning-composite-unavailable-secret",
    )
    adapter = PlanningCompositeSourceAdapter(
        service=PlanningMcpService(
            tmp_path,
            definition_store=store,
            definition_owner=OWNER_REF,
        ),
        owner_scope=OWNER_SCOPE,
        expected_owner_ref=OWNER_REF,
        observed_at=OBSERVED_AT,
    )
    binding = adapter.binding
    observation = adapter.observe_unavailable(
        PlanningCompositeSourceRoute(
            PLANNING_SOURCE_ADAPTER_ID,
            PlanningSourceAdapter.logical_ref("p", "r0"),
        ),
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    assert observation.reason == "ineligible_no_approved_revision"


def test_composite_page_rejects_mixed_child_owner_refs(tmp_path) -> None:
    from tests.test_planning_mcp_service import _owner_markdown_test_service
    from src.unified_source_index_sources.planning import PlanningSourceAdapter
    from src.unified_source_index_sources.planning_markdown import (
        PlanningMarkdownSourceAdapter,
    )

    json_adapter = PlanningSourceAdapter(
        service=_service(tmp_path / "json", json_count=1, markdown_count=0),
        owner_scope=OWNER_SCOPE,
        expected_owner_ref=OWNER_REF,
        observed_at=OBSERVED_AT,
    )
    json_binding = json_adapter.binding
    json_descriptor = json_adapter.discover(
        expected_binding_digest=json_binding.binding_digest,
        expected_snapshot_digest=json_binding.snapshot_digest,
    ).items[0]
    markdown_service, _store, _definition, _path = _owner_markdown_test_service(
        tmp_path / "markdown", b"mixed owner"
    )
    markdown_adapter = PlanningMarkdownSourceAdapter(
        service=markdown_service,
        owner_scope=OWNER_SCOPE,
        expected_owner_ref="owner-1",
        observed_at=OBSERVED_AT,
    )
    markdown_binding = markdown_adapter.binding
    markdown_descriptor = markdown_adapter.discover(
        expected_binding_digest=markdown_binding.binding_digest,
        expected_snapshot_digest=markdown_binding.snapshot_digest,
    ).items[0]
    items = (
        PlanningCompositeSourceDescriptor(
            PlanningCompositeSourceRoute(
                PLANNING_SOURCE_ADAPTER_ID,
                json_descriptor.evidence.logical_ref,
            ),
            json_descriptor,
        ),
        PlanningCompositeSourceDescriptor(
            PlanningCompositeSourceRoute(
                PLANNING_MARKDOWN_SOURCE_ADAPTER_ID,
                markdown_descriptor.evidence.logical_ref,
            ),
            markdown_descriptor,
        ),
    )
    with pytest.raises(PlanningCompositeSourceAdapterError, match="invalid_snapshot"):
        PlanningCompositeDiscoveryPage(
            items,
            None,
            "sha256:" + "a" * 64,
            "sha256:" + "b" * 64,
        )


class _BindingComparisonBomb:
    def __init__(self):
        self.calls = 0

    def __eq__(self, other):
        self.calls += 1
        raise AssertionError("hostile child-binding equality invoked")

    def __ne__(self, other):
        self.calls += 1
        raise AssertionError("hostile child-binding inequality invoked")


@pytest.mark.parametrize("child_kind", ["json", "markdown"])
def test_pin_revalidates_child_binding_before_comparison(
    tmp_path, monkeypatch, child_kind
) -> None:
    from src.unified_source_index_sources.planning import PlanningSourceAdapter
    from src.unified_source_index_sources.planning_markdown import (
        PlanningMarkdownSourceAdapter,
    )

    adapter = _adapter(tmp_path, json_count=1, markdown_count=1)
    child_type = (
        PlanningSourceAdapter
        if child_kind == "json"
        else PlanningMarkdownSourceAdapter
    )
    bomb = _BindingComparisonBomb()
    monkeypatch.setattr(child_type, "binding", property(lambda _child: bomb))

    with pytest.raises(PlanningCompositeSourceAdapterError, match="stale_authority"):
        _ = adapter.binding
    assert bomb.calls == 0


@pytest.mark.parametrize("child_kind", ["json", "markdown"])
def test_fence_revalidates_child_binding_before_comparison(
    tmp_path, monkeypatch, child_kind
) -> None:
    from src.unified_source_index_sources.planning import PlanningSourceAdapter
    from src.unified_source_index_sources.planning_markdown import (
        PlanningMarkdownSourceAdapter,
    )

    adapter = _adapter(tmp_path, json_count=1, markdown_count=1)
    binding = adapter.binding
    child_type = (
        PlanningSourceAdapter
        if child_kind == "json"
        else PlanningMarkdownSourceAdapter
    )
    original_binding = child_type.binding
    accesses = {"count": 0}
    bomb = _BindingComparisonBomb()

    def staged_binding(child):
        accesses["count"] += 1
        if accesses["count"] == 3:
            return bomb
        return original_binding.__get__(child, child_type)

    monkeypatch.setattr(child_type, "binding", property(staged_binding))
    with pytest.raises(PlanningCompositeSourceAdapterError, match="stale_authority"):
        adapter.discover(
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )
    assert accesses["count"] == 3
    assert bomb.calls == 0


def test_paged_runtime_preserves_real_source04_child_graphs_and_profiles(tmp_path):
    from tests.test_unified_source_index_jobs import (
        _stored, _record_key, _records_digest,
    )
    from src.unified_source_index_adapters import PagedExtractionPage
    from src.unified_source_index_contract import ChunkRecord, RecordKind

    from tests.test_unified_source_index_planning_composite_source import (
        OWNER_SCOPE,
        _OneChildGenericAdapter,
        _adapter as source04_adapter,
        _all as source04_all,
    )

    composite = source04_adapter(tmp_path / "source04", json_count=1, markdown_count=1)
    binding, items = source04_all(composite)
    occurrences = {
        item.route.child_adapter_id: composite.extract(
            item.route,
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )
        for item in items
    }
    job_profile = "root.job.generation-v1"

    class PagedPlanningBridge(_OneChildGenericAdapter):
        def __init__(self, descriptor, occurrence):
            super().__init__(descriptor, occurrence, job_profile)
            self.capability = replace(
                self.capability,
                required_job_profile_ref=job_profile,
            )
            self.records = tuple(sorted(
                (*occurrence.chunks, *occurrence.entities, *occurrence.relations),
                key=_record_key,
            ))
            self.page_calls = 0
            self.legacy_extract_calls = 0

        def extract(self, source_version, profile):
            self.legacy_extract_calls += 1
            raise AssertionError("paged Source04 bridge must not call legacy extract")

        def extract_page(self, source_version, profile, *, cursor, sequence_start):
            self.page_calls += 1
            assert source_version == self.descriptor.source_version
            assert profile.profile_ref == job_profile
            assert 1 <= profile.max_items <= 100
            start = int(cursor) if cursor else 0
            end = min(start + 2, len(self.records))
            page_records = self.records[start:end]
            next_cursor = str(end) if end < len(self.records) else ""
            return PagedExtractionPage(
                source_version,
                sequence_start,
                page_records,
                next_cursor,
                None if next_cursor else len(self.records),
                "" if next_cursor else _records_digest(self.records),
            )

    store = SQLiteUnifiedSourceIndexStore(tmp_path / "source04-paged.db")
    runtime = UnifiedSourceIndexJobRuntime(store)
    expected_profiles = set()
    for index, item in enumerate(items):
        occurrence = occurrences[item.route.child_adapter_id]
        bridge = PagedPlanningBridge(item.descriptor, occurrence)
        job = IndexJobRecord.create(
            job_kind=IndexJobKind.EXTRACTION,
            source_scope=SourceScope.create((item.descriptor.source.policy_evidence(),)),
            request_ref=f"request:source04-paged-{index}",
            profile_ref=job_profile,
            max_items=1,
            time_budget_ms=60_000,
        )
        runtime.register(job)
        result = runtime.run_to_completion(
            job.job_id,
            owner_scope=OWNER_SCOPE.value,
            worker_id=f"worker.source04-paged-{index}",
            adapter=bridge,
            now="2026-09-07T12:30:00Z",
            lease_seconds=120,
        )

        assert result.terminal is True
        assert bridge.legacy_extract_calls == 0
        assert bridge.page_calls == (len(bridge.records) + 1) // 2
        assert _stored(
            store, RecordKind.SOURCE, item.descriptor.source.source_id,
            owner=OWNER_SCOPE.value,
        ).record == item.descriptor.source
        assert _stored(
            store,
            RecordKind.SOURCE_VERSION,
            item.descriptor.source_version.source_version_id,
            owner=OWNER_SCOPE.value,
        ).record == item.descriptor.source_version
        for expected in bridge.records:
            kind_value, record_id = _record_key(expected)
            stored = _stored(
                store, RecordKind(kind_value), record_id, owner=OWNER_SCOPE.value
            )
            assert stored.record == expected
            assert stored.record_id == record_id
            if type(expected) is ChunkRecord or hasattr(expected, "extractor_profile_ref"):
                expected_profiles.add(expected.extractor_profile_ref)
                assert expected.extractor_profile_ref != job.profile_ref

    assert expected_profiles == {
        "planning.approved_json.exact.v1",
        "planning.approved_json.graph.v1",
        "planning.approved_markdown_body.exact.v1",
    }
