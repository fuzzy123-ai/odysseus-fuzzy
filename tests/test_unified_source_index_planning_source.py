from __future__ import annotations

from dataclasses import replace
import json
import traceback
from pathlib import Path

import pytest

from src.planning_definition_contract import compute_roadmap_content_hash
from src.planning_mcp_service import PlanningMcpService
from src.planning_revision_store import PlanningRevisionStore
from src.unified_source_index_contract import (
    Classification,
    ContentPolicy,
    EntityKind,
    RelationKind,
    SourceKind,
    TextRangeLocator,
    content_hash,
)
from src.unified_source_index_owner_scope import OwnerScope
from src.unified_source_index_source_capability import (
    OwnerScopeRequirement,
    ProviderConstraint,
    QueryCapability,
    SourceAdapterOperation,
)
from src.unified_source_index_sources.planning import (
    MAX_PROJECTION_UTF8_BYTES,
    PLANNING_SOURCE_ADAPTER_ID,
    PLANNING_SOURCE_ADAPTER_VERSION,
    PLANNING_SOURCE_EXACT_READER_BOUNDARY,
    PlanningCharacterRangeRequest,
    PlanningSourceAdapter,
    PlanningSourceAdapterError,
    planning_source_capability_manifest,
    planning_source_registration,
)


OWNER_SCOPE = OwnerScope("owner:owner_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
OWNER_REF = "synthetic-owner"
OBSERVED_AT = "2026-09-07T04:00:00Z"


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _node(
    node_id: str,
    *,
    depends_on: list[str] | None = None,
    gate_ids: list[str] | None = None,
) -> dict:
    return {
        "node_id": node_id,
        "kind": "work",
        "title": f"Node {node_id}",
        "objective": "Ship one bounded unit.",
        "depends_on": list(depends_on or []),
        "gate_ids": list(gate_ids or []),
        "deliverables": [],
        "allowed_paths": [],
        "blocked_paths": [],
        "capability_requirements": [],
        "verification_rule_ids": ["definition-tests"],
    }


def _roadmap(
    roadmap_id: str,
    *,
    nodes: list[dict] | None = None,
    edges: list[dict] | None = None,
    gates: list[dict] | None = None,
    source_refs: list[str] | None = None,
) -> dict:
    selected_nodes = nodes or [_node("service-contract", gate_ids=["definition-go"])]
    selected_gates = (
        gates
        if gates is not None
        else [
            {
                "gate_id": "definition-go",
                "kind": "repo",
                "title": "Definition gate",
                "blocks": [selected_nodes[0]["node_id"]],
                "decision_needed": "Confirm the immutable contract.",
                "safe_default": "Keep it read-only.",
                "approval_scope_schema": {
                    "type": "object",
                    "properties": {"approved": {"type": "boolean"}},
                    "required": ["approved"],
                    "additionalProperties": False,
                },
                "required_verification_rule_ids": ["definition-tests"],
            }
        ]
    )
    roadmap = {
        "roadmap_id": roadmap_id,
        "project_id": "demo-project",
        "revision": 1,
        "content_hash": "sha256:" + ("0" * 64),
        "revision_state": "approved",
        "title": f"Roadmap {roadmap_id}",
        "objective": "Ship a bounded definition service.",
        "assumptions": [],
        "constraints": ["No runtime state in Planning."],
        "nodes": selected_nodes,
        "edges": list(edges or []),
        "gates": selected_gates,
        "done_contract": {
            "required_node_ids": [item["node_id"] for item in selected_nodes],
            "required_gate_ids": [item["gate_id"] for item in selected_gates],
            "verification_rules": [
                {
                    "rule_id": "definition-tests",
                    "kind": "test",
                    "description": "The focused tests pass.",
                }
            ],
            "completion_rule": "all_required_nodes_and_gates",
        },
        "source_refs": list(source_refs or []),
        "created_at": "2026-09-07T03:00:00Z",
        "updated_at": "2026-09-07T03:30:00Z",
    }
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    return roadmap


def _definition(roadmaps: list[dict]) -> dict:
    return {
        "schema_id": "odysseus.planning.definition.v2",
        "project": {
            "project_id": "demo-project",
            "title": "Synthetic project",
            "objective": "Exercise the adapter.",
            "scope": {"in": ["Planning definitions"], "out": ["runtime state"]},
            "constraints": [],
            "roadmap_refs": [item["roadmap_id"] for item in roadmaps],
            "latest_approved_revision": {
                item["roadmap_id"]: {
                    "revision": item["revision"],
                    "content_hash": item["content_hash"],
                }
                for item in roadmaps
                if item["revision_state"] == "approved"
            },
            "draft_refs": [],
        },
        "roadmaps": roadmaps,
    }


def _service(
    tmp_path: Path,
    definition: dict,
    *,
    source_ref: str = "docs/plans/synthetic.json",
) -> PlanningMcpService:
    source = tmp_path.joinpath(*source_ref.split("/"))
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(definition, ensure_ascii=False), encoding="utf-8")
    store = PlanningRevisionStore(
        [(OWNER_REF, definition, source_ref)],
        cursor_secret=b"synthetic-planning-adapter-secret",
    )
    return PlanningMcpService(
        tmp_path,
        definition_store=store,
        definition_owner=OWNER_REF,
    )


def _adapter(tmp_path: Path, definition: dict) -> PlanningSourceAdapter:
    return PlanningSourceAdapter(
        service=_service(tmp_path, definition),
        owner_scope=OWNER_SCOPE,
        expected_owner_ref=OWNER_REF,
        observed_at=OBSERVED_AT,
    )


def _first(adapter: PlanningSourceAdapter):
    binding = adapter.binding
    page = adapter.discover(
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    return binding, page.items[0]


def test_manifest_is_complete_default_off_and_factory_free() -> None:
    manifest = planning_source_capability_manifest()
    assert manifest.adapter_id == PLANNING_SOURCE_ADAPTER_ID == "planning.approved_json"
    assert manifest.adapter_version == PLANNING_SOURCE_ADAPTER_VERSION == "v1"
    assert manifest.domain_id == "planning"
    assert manifest.source_kind is SourceKind.PLANNING
    assert manifest.classification_ceiling is Classification.PRIVATE
    assert manifest.content_policy is ContentPolicy.INLINE_LOCAL
    assert manifest.owner_scope_requirement is OwnerScopeRequirement.IMMUTABLE_OPAQUE
    assert manifest.provider_constraint is ProviderConstraint.LOCAL_ACCEPTED_BOUNDARY
    assert manifest.query_capability is QueryCapability.EXACT_READER
    assert set(manifest.operations) == set(SourceAdapterOperation)
    assert manifest.exact_reader_boundary == PLANNING_SOURCE_EXACT_READER_BOUNDARY
    assert manifest.productive_default_enabled is False
    registration = planning_source_registration()
    assert registration.manifest == manifest
    assert registration.factory is None


def test_discover_binds_owner_scope_and_distinguishes_shared_json_rows(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path, _definition([_roadmap("alpha"), _roadmap("beta")]))
    binding = adapter.binding
    first = adapter.discover(
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
        limit=1,
    )
    second = adapter.discover(
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
        cursor=1,
    )
    descriptors = first.items + second.items
    assert binding.owner_scope == OWNER_SCOPE
    assert first.next_cursor == 1
    assert len({item.evidence.logical_ref for item in descriptors}) == 2
    assert len({item.source.source_id for item in descriptors}) == 2
    assert {item.source.canonical_ref for item in descriptors} == {
        item.evidence.logical_ref for item in descriptors
    }
    assert {item.evidence.source_ref for item in descriptors} == {
        "docs/plans/synthetic.json"
    }
    assert all(item.source.owner_scope == OWNER_SCOPE.value for item in descriptors)


def test_extract_exact_ranges_and_native_graph_relationships(tmp_path: Path) -> None:
    nodes = [
        _node("first", gate_ids=["release-go"]),
        _node("second", depends_on=["first"]),
    ]
    gates = [
        {
            "gate_id": "release-go",
            "kind": "repo",
            "title": "Release gate",
            "blocks": ["second"],
            "decision_needed": "Confirm release.",
            "safe_default": "Wait.",
            "approval_scope_schema": {
                "type": "object",
                "additionalProperties": False,
            },
            "required_verification_rule_ids": ["definition-tests"],
        }
    ]
    edges = [{"from": "second", "to": "first", "kind": "depends_on"}]
    adapter = _adapter(
        tmp_path,
        _definition([_roadmap("graph", nodes=nodes, gates=gates, edges=edges)]),
    )
    binding, descriptor = _first(adapter)
    occurrence = adapter.extract(
        descriptor.evidence.logical_ref,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    assert "".join(chunk.content or "" for chunk in occurrence.chunks)
    assert occurrence.splitter_version == "rag_exact_character_ranges_v1"
    assert all(type(chunk.locator) is TextRangeLocator for chunk in occurrence.chunks)
    entity_by_key = {entity.natural_key: entity for entity in occurrence.entities}
    assert entity_by_key["project:demo-project"].entity_kind is EntityKind.OTHER
    assert entity_by_key["project:demo-project"].label == "demo-project"
    assert entity_by_key["node:first"].entity_kind is EntityKind.TASK
    entity_refs = {entity.ref() for entity in occurrence.entities}
    assert all(
        relation.source_ref in entity_refs and relation.target_ref in entity_refs
        for relation in occurrence.relations
    )
    assert {relation.relation_kind for relation in occurrence.relations} >= {
        RelationKind.BELONGS_TO,
        RelationKind.RELATED_TO,
        RelationKind.REFERENCES,
    }
    assert {relation.method_ref for relation in occurrence.relations} >= {
        "planning.roadmap.belongs_to_project",
        "planning.node.belongs_to_roadmap",
        "planning.gate.belongs_to_roadmap",
        "planning.node.depends_on",
        "planning.edge.depends_on",
        "planning.gate.blocks",
        "planning.node.gate_reference",
    }


def test_exact_read_is_unicode_python_character_slice_and_request_bound(
    tmp_path: Path,
) -> None:
    roadmap = _roadmap("unicode")
    roadmap["objective"] = "alpha \U0001f600 e\u0301 omega"
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    adapter = _adapter(tmp_path, _definition([roadmap]))
    binding, descriptor = _first(adapter)
    occurrence = adapter.extract(
        descriptor.evidence.logical_ref,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    full = "".join(chunk.content or "" for chunk in occurrence.chunks)
    start = full.index("\U0001f600")
    text_range = TextRangeLocator(start, start + 3)
    request = PlanningCharacterRangeRequest(descriptor.projection_locator, text_range)
    exact = adapter.read_exact(
        request,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    assert exact.content == full[start : start + 3] == "\U0001f600 e"
    assert exact.request.text_range == text_range
    assert "alpha" not in repr(exact)


def test_exact_read_rejects_request_evidence_record_identity_mismatch(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path, _definition([_roadmap("exact-record-identity")]))
    binding, descriptor = _first(adapter)
    original_request = PlanningCharacterRangeRequest(
        descriptor.projection_locator,
        TextRangeLocator(0, 1),
    )
    exact = adapter.read_exact(
        original_request,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )

    def assert_invalid_snapshot(call) -> None:
        with pytest.raises(
            PlanningSourceAdapterError,
            match="^invalid_snapshot$",
        ) as raised:
            call()
        assert raised.value.code == "invalid_snapshot"
        assert raised.value.__cause__ is None
        assert raised.value.__context__ is None

    alternate_requests = {}
    for field_name, alternate_value in (
        ("source_id", "alternate-source-id"),
        ("source_version_id", "alternate-source-version-id"),
    ):
        locator = replace(
            exact.request.projection_locator,
            **{field_name: alternate_value, "locator_digest": ""},
        )
        request = replace(
            exact.request,
            projection_locator=locator,
            request_digest="",
        )
        alternate_requests[field_name] = request
        assert_invalid_snapshot(lambda request=request: replace(exact, request=request))

        evidence = replace(exact.evidence, **{field_name: alternate_value})
        assert_invalid_snapshot(
            lambda evidence=evidence: replace(exact, evidence=evidence)
        )

    bypassed_locator = exact.request.projection_locator
    alternate_request = alternate_requests["source_id"]
    object.__setattr__(
        bypassed_locator,
        "source_id",
        alternate_request.projection_locator.source_id,
    )
    object.__setattr__(
        bypassed_locator,
        "locator_digest",
        alternate_request.projection_locator.locator_digest,
    )
    object.__setattr__(
        exact.request,
        "request_digest",
        alternate_request.request_digest,
    )
    assert_invalid_snapshot(lambda: replace(exact))


def test_domain_and_projection_hashes_are_separate(tmp_path: Path) -> None:
    roadmap = _roadmap("hashes")
    adapter = _adapter(tmp_path, _definition([roadmap]))
    _binding, descriptor = _first(adapter)
    assert descriptor.evidence.domain_content_hash == roadmap["content_hash"]
    assert (
        descriptor.evidence.canonical_projection_hash
        == descriptor.source_version.content_hash
    )
    assert (
        descriptor.evidence.domain_content_hash
        != descriptor.evidence.canonical_projection_hash
    )


def test_initial_draft_only_unavailable_semantics(tmp_path: Path) -> None:
    roadmap = _roadmap("draft")
    roadmap["revision_state"] = "draft"
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    definition = _definition([roadmap])
    definition["project"]["latest_approved_revision"] = {}
    adapter = _adapter(tmp_path, definition)
    binding = adapter.binding
    logical_ref = PlanningSourceAdapter.logical_ref("demo-project", "draft")
    unavailable = adapter.observe_unavailable(
        logical_ref,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    assert unavailable.reason == "ineligible_no_approved_revision"
    assert unavailable.logical_ref == logical_ref
    with pytest.raises(PlanningSourceAdapterError, match="unavailable_state_unproven"):
        adapter.observe_unavailable(
            PlanningSourceAdapter.logical_ref("demo-project", "never-declared"),
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )


def test_available_row_cannot_be_observed_unavailable(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, _definition([_roadmap("available")]))
    binding, descriptor = _first(adapter)
    with pytest.raises(PlanningSourceAdapterError, match="record_still_available"):
        adapter.observe_unavailable(
            descriptor.evidence.logical_ref,
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )


def test_detached_binding_and_mutated_request_are_revalidated(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, _definition([_roadmap("mutations")]))
    binding, descriptor = _first(adapter)
    detached = adapter.binding
    object.__setattr__(detached, "owner_ref", "tampered")
    assert adapter.binding.owner_ref == OWNER_REF
    request = PlanningCharacterRangeRequest(
        descriptor.projection_locator,
        TextRangeLocator(0, 1),
    )
    object.__setattr__(
        request.projection_locator,
        "logical_ref",
        "planning:roadmap:sha256:" + ("0" * 64),
    )
    with pytest.raises(PlanningSourceAdapterError, match="invalid_request"):
        adapter.read_exact(
            request,
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )


def test_wrong_authority_and_non_exact_inputs_fail_closed(tmp_path: Path) -> None:
    service = _service(tmp_path, _definition([_roadmap("authority")]))

    class ScopeSubclass(OwnerScope):
        pass

    with pytest.raises(PlanningSourceAdapterError, match="invalid_authority"):
        PlanningSourceAdapter(
            service=service,
            owner_scope=ScopeSubclass(OWNER_SCOPE.value),
            expected_owner_ref=OWNER_REF,
            observed_at=OBSERVED_AT,
        )
    adapter = PlanningSourceAdapter(
        service=service,
        owner_scope=OWNER_SCOPE,
        expected_owner_ref=OWNER_REF,
        observed_at=OBSERVED_AT,
    )
    with pytest.raises(PlanningSourceAdapterError, match="stale_authority"):
        adapter.discover(
            expected_binding_digest="sha256:" + ("0" * 64),
            expected_snapshot_digest=adapter.binding.snapshot_digest,
        )
    with pytest.raises(PlanningSourceAdapterError, match="invalid_request"):
        adapter.discover(
            expected_binding_digest=adapter.binding.binding_digest,
            expected_snapshot_digest=adapter.binding.snapshot_digest,
            cursor=True,
        )


def _large_definition(target_projection_chars: int = 750_079) -> dict:
    nodes = [
        _node(
            f"n{index:04d}",
            depends_on=[f"n{index - 1:04d}"] if index else [],
        )
        for index in range(1_000)
    ]
    roadmap = _roadmap("large", nodes=nodes, gates=[])
    projection = {
        "schema_id": "odysseus.planning.owner_source_projection.v1",
        "project_id": "demo-project",
        "roadmap": roadmap,
    }
    delta = target_projection_chars - len(_canonical(projection))
    assert delta > 0
    for node in nodes:
        added = min(delta, 3_500)
        node["objective"] += "x" * added
        delta -= added
        if delta == 0:
            break
    assert delta == 0
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    assert len(_canonical(projection)) == target_projection_chars
    return _definition([roadmap])


def test_large_projection_reconstructs_from_exactly_three_shared_ranges(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path, _large_definition())
    binding, descriptor = _first(adapter)
    occurrence = adapter.extract(
        descriptor.evidence.logical_ref,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    assert len(occurrence.chunks) == 3
    reconstructed = "".join(chunk.content or "" for chunk in occurrence.chunks)
    assert len(reconstructed) == 750_079
    assert occurrence.chunks[0].locator == TextRangeLocator(0, 262_144)
    assert occurrence.chunks[-1].locator.end_char == 750_079


def test_exact_reads_support_the_full_projection_envelope_and_preflight_ranges(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path, _large_definition())
    adapter = PlanningSourceAdapter(
        service=service,
        owner_scope=OWNER_SCOPE,
        expected_owner_ref=OWNER_REF,
        observed_at=OBSERVED_AT,
    )
    binding, descriptor = _first(adapter)
    capture_calls = 0
    read_calls = 0

    def forbidden_capture(*_args, **_kwargs):
        nonlocal capture_calls
        capture_calls += 1
        raise AssertionError("capture must not run for an impossible range")

    def forbidden_read(*_args, **_kwargs):
        nonlocal read_calls
        read_calls += 1
        raise AssertionError("read must not run for an impossible range")

    with monkeypatch.context() as scoped:
        scoped.setattr(PlanningMcpService, "capture_owner_source_snapshot", forbidden_capture)
        scoped.setattr(PlanningMcpService, "read_owner_source_projection", forbidden_read)
        with pytest.raises(PlanningSourceAdapterError, match="^invalid_request$"):
            PlanningCharacterRangeRequest(
                descriptor.projection_locator,
                TextRangeLocator(0, MAX_PROJECTION_UTF8_BYTES + 1),
            )
    assert capture_calls == read_calls == 0

    operation_args = {
        "expected_binding_digest": binding.binding_digest,
        "expected_snapshot_digest": binding.snapshot_digest,
    }
    medium_request = PlanningCharacterRangeRequest(
        descriptor.projection_locator,
        TextRangeLocator(100_000, 400_000),
    )
    medium = adapter.read_exact(medium_request, **operation_args)
    assert len(medium.content) == 300_000

    full_request = PlanningCharacterRangeRequest(
        descriptor.projection_locator,
        TextRangeLocator(0, 750_079),
    )
    full = adapter.read_exact(full_request, **operation_args)
    assert len(full.content) == 750_079
    assert full.content[100_000:400_000] == medium.content


def test_public_errors_and_outer_reprs_are_content_free(tmp_path: Path) -> None:
    private_marker = "PRIVATE-PROJECTION-MARKER"
    roadmap = _roadmap("privacy")
    roadmap["objective"] = private_marker
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    adapter = _adapter(tmp_path, _definition([roadmap]))
    binding, descriptor = _first(adapter)
    occurrence = adapter.extract(
        descriptor.evidence.logical_ref,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    assert all(
        private_marker not in repr(value)
        for value in (binding, descriptor, occurrence)
    )
    error = PlanningSourceAdapterError(private_marker)
    assert error.code == "planning_source_adapter_failed"
    assert private_marker not in str(error)


def test_moving_identical_projection_preserves_source_identity(tmp_path: Path) -> None:
    definition = _definition([_roadmap("portable")])
    first = PlanningSourceAdapter(
        service=_service(
            tmp_path / "first",
            definition,
            source_ref="docs/plans/first.json",
        ),
        owner_scope=OWNER_SCOPE,
        expected_owner_ref=OWNER_REF,
        observed_at=OBSERVED_AT,
    )
    second = PlanningSourceAdapter(
        service=_service(
            tmp_path / "second",
            definition,
            source_ref="specs/roadmaps/moved.json",
        ),
        owner_scope=OWNER_SCOPE,
        expected_owner_ref=OWNER_REF,
        observed_at=OBSERVED_AT,
    )
    _first_binding, first_descriptor = _first(first)
    _second_binding, second_descriptor = _first(second)
    assert first_descriptor.source.source_id == second_descriptor.source.source_id
    assert (
        first_descriptor.source_version.source_version_id
        == second_descriptor.source_version.source_version_id
    )
    assert (
        first_descriptor.evidence.canonical_projection_hash
        == second_descriptor.evidence.canonical_projection_hash
    )
    assert first_descriptor.evidence.source_ref != second_descriptor.evidence.source_ref


def test_newer_draft_does_not_replace_latest_approved(tmp_path: Path) -> None:
    approved = _roadmap("draft-preservation")
    draft = json.loads(json.dumps(approved))
    draft["revision"] = 2
    draft["revision_state"] = "draft"
    draft["title"] = "Newer draft"
    draft["content_hash"] = compute_roadmap_content_hash(draft)
    definition = _definition([approved])
    definition["roadmaps"].append(draft)
    definition["project"]["draft_refs"] = [
        {
            "draft_id": "draft-draft-preservation",
            "roadmap_id": "draft-preservation",
            "base_revision": 1,
            "base_hash": approved["content_hash"],
        }
    ]
    adapter = _adapter(tmp_path, definition)
    _binding, descriptor = _first(adapter)
    assert descriptor.evidence.approved_revision == 1
    assert descriptor.evidence.domain_content_hash == approved["content_hash"]


def test_service_replacement_and_changed_snapshot_are_stale(
    tmp_path: Path,
) -> None:
    definition = _definition([_roadmap("stale")])
    original = _service(tmp_path / "original", definition)
    adapter = PlanningSourceAdapter(
        service=original,
        owner_scope=OWNER_SCOPE,
        expected_owner_ref=OWNER_REF,
        observed_at=OBSERVED_AT,
    )
    binding = adapter.binding
    replacement = _service(tmp_path / "replacement", definition)
    adapter._service = replacement
    with pytest.raises(PlanningSourceAdapterError, match="stale_authority"):
        adapter.discover(
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )

    changed_root = tmp_path / "changed"
    changed_service = _service(changed_root, definition)
    changed_adapter = PlanningSourceAdapter(
        service=changed_service,
        owner_scope=OWNER_SCOPE,
        expected_owner_ref=OWNER_REF,
        observed_at=OBSERVED_AT,
    )
    changed_binding = changed_adapter.binding
    changed = json.loads(json.dumps(definition))
    changed["roadmaps"][0]["title"] = "Changed after binding"
    changed["roadmaps"][0]["content_hash"] = compute_roadmap_content_hash(
        changed["roadmaps"][0]
    )
    changed_root.joinpath("docs", "plans", "synthetic.json").write_text(
        json.dumps(changed),
        encoding="utf-8",
    )
    with pytest.raises(PlanningSourceAdapterError, match="stale_authority"):
        changed_adapter.discover(
            expected_binding_digest=changed_binding.binding_digest,
            expected_snapshot_digest=changed_binding.snapshot_digest,
        )


def test_row_limit_fails_before_any_source_record_construction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path, _definition([_roadmap("row-limit")]))
    captured = service.capture_owner_source_snapshot(OWNER_REF)
    row = {
        "project_id": "demo-project",
        "roadmap_id": "draft-only",
        "eligibility": "ineligible_no_approved_revision",
        "revision": None,
        "revision_state": "",
        "content_hash": "",
        "updated_at": "",
        "source_ref": "",
    }
    oversized = dict(captured)
    oversized["descriptors"] = []
    oversized["ineligible"] = [dict(row) for _ in range(101)]
    oversized["eligible_count"] = 0
    oversized["ineligible_count"] = 101

    def capture_oversized(self, expected_owner_ref, *, max_sources=100):
        return oversized

    monkeypatch.setattr(
        PlanningMcpService,
        "capture_owner_source_snapshot",
        capture_oversized,
    )
    with pytest.raises(PlanningSourceAdapterError, match="source_limit_exceeded"):
        PlanningSourceAdapter(
            service=service,
            owner_scope=OWNER_SCOPE,
            expected_owner_ref=OWNER_REF,
            observed_at=OBSERVED_AT,
        )


def test_projection_and_graph_limits_fail_before_output_record_construction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import src.unified_source_index_sources.planning as planning_source

    service = _service(tmp_path / "projection", _definition([_roadmap("projection")]))
    adapter = PlanningSourceAdapter(
        service=service,
        owner_scope=OWNER_SCOPE,
        expected_owner_ref=OWNER_REF,
        observed_at=OBSERVED_AT,
    )
    binding, descriptor = _first(adapter)
    captured = service.capture_owner_source_snapshot(OWNER_REF)
    raw_read = service.read_owner_source_projection(
        captured["descriptors"][0],
        expected_owner_ref=OWNER_REF,
        expected_snapshot_digest=captured["snapshot_digest"],
    )
    oversized_read = json.loads(json.dumps(raw_read))
    oversized_read["projection"]["roadmap"]["objective"] = "x" * 2_000_001

    class ExplodingChunk:
        @classmethod
        def create(cls, *args, **kwargs):
            raise AssertionError("output record constructed before projection preflight")

    monkeypatch.setattr(planning_source, "ChunkRecord", ExplodingChunk)
    monkeypatch.setattr(
        PlanningMcpService,
        "read_owner_source_projection",
        lambda self, descriptor, *, expected_owner_ref, expected_snapshot_digest: oversized_read,
    )
    with pytest.raises(PlanningSourceAdapterError, match="projection_limit_exceeded"):
        adapter.extract(
            descriptor.evidence.logical_ref,
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )

    monkeypatch.undo()
    graph_service = _service(
        tmp_path / "graph",
        _definition([_roadmap("graph-limit")]),
    )
    graph_adapter = PlanningSourceAdapter(
        service=graph_service,
        owner_scope=OWNER_SCOPE,
        expected_owner_ref=OWNER_REF,
        observed_at=OBSERVED_AT,
    )
    graph_binding, graph_descriptor = _first(graph_adapter)
    graph_capture = graph_service.capture_owner_source_snapshot(OWNER_REF)
    graph_read = graph_service.read_owner_source_projection(
        graph_capture["descriptors"][0],
        expected_owner_ref=OWNER_REF,
        expected_snapshot_digest=graph_capture["snapshot_digest"],
    )
    graph_read = json.loads(json.dumps(graph_read))
    graph_read["projection"]["roadmap"]["nodes"] = [
        _node(f"over{index:04d}") for index in range(4_095)
    ]
    graph_read["projection"]["roadmap"]["edges"] = []
    graph_read["projection"]["roadmap"]["gates"] = []
    monkeypatch.setattr(planning_source, "MAX_PRIMITIVE_NODES", 250_000)
    monkeypatch.setattr(planning_source, "ChunkRecord", ExplodingChunk)
    monkeypatch.setattr(
        PlanningMcpService,
        "read_owner_source_projection",
        lambda self, descriptor, *, expected_owner_ref, expected_snapshot_digest: graph_read,
    )
    with pytest.raises(PlanningSourceAdapterError, match="graph_limit_exceeded"):
        graph_adapter.extract(
            graph_descriptor.evidence.logical_ref,
            expected_binding_digest=graph_binding.binding_digest,
            expected_snapshot_digest=graph_binding.snapshot_digest,
        )



def test_public_errors_detach_upstream_context_at_all_entrypoint_shapes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import src.unified_source_index_sources.planning as planning_source

    marker = "UPSTREAM-SENSITIVE-MARKER"
    service = _service(tmp_path / "service", _definition([_roadmap("cause-free")]))
    adapter = PlanningSourceAdapter(
        service=service,
        owner_scope=OWNER_SCOPE,
        expected_owner_ref=OWNER_REF,
        observed_at=OBSERVED_AT,
    )
    binding, descriptor = _first(adapter)
    request = PlanningCharacterRangeRequest(
        descriptor.projection_locator,
        TextRangeLocator(0, 1),
    )

    def explode(*_args, **_kwargs):
        raise RuntimeError(marker)

    def assert_cause_free(call) -> None:
        with pytest.raises(PlanningSourceAdapterError) as captured:
            call()
        error = captured.value
        assert error.__cause__ is None
        assert error.__context__ is None
        rendered = "".join(
            traceback.format_exception(type(error), error, error.__traceback__, chain=True)
        )
        assert marker not in rendered

    with monkeypatch.context() as scoped:
        scoped.setattr(planning_source, "_capture_snapshot", explode)
        assert_cause_free(
            lambda: PlanningSourceAdapter(
                service=service,
                owner_scope=OWNER_SCOPE,
                expected_owner_ref=OWNER_REF,
                observed_at=OBSERVED_AT,
            )
        )

    with monkeypatch.context() as scoped:
        scoped.setattr(planning_source, "_binding_values", explode)
        assert_cause_free(lambda: replace(binding))

    with monkeypatch.context() as scoped:
        scoped.setattr(planning_source, "_identifier", explode)
        assert_cause_free(lambda: PlanningSourceAdapter.logical_ref("project", "roadmap"))

    operation_args = {
        "expected_binding_digest": binding.binding_digest,
        "expected_snapshot_digest": binding.snapshot_digest,
    }
    operations = (
        lambda: adapter.discover(**operation_args),
        lambda: adapter.observe_version(descriptor.evidence.logical_ref, **operation_args),
        lambda: adapter.extract(descriptor.evidence.logical_ref, **operation_args),
        lambda: adapter.read_exact(request, **operation_args),
        lambda: adapter.observe_unavailable(descriptor.evidence.logical_ref, **operation_args),
    )
    with monkeypatch.context() as scoped:
        scoped.setattr(PlanningSourceAdapter, "_operation_capture", explode)
        for operation in operations:
            assert_cause_free(operation)


def test_occurrence_rejects_forged_chunk_text_and_span_mismatch(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, _definition([_roadmap("chunk-binding")]))
    binding, descriptor = _first(adapter)
    occurrence = adapter.extract(
        descriptor.evidence.logical_ref,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    original = occurrence.chunks[0]
    replacement_character = "x" if original.content[0] != "x" else "y"
    forged_text = replacement_character * len(original.content)
    forged_content = replace(
        original,
        content=forged_text,
        content_hash=content_hash(forged_text),
    )
    forged_span = replace(
        original,
        locator=TextRangeLocator(
            original.locator.start_char,
            original.locator.end_char - 1,
        ),
        chunk_id="",
    )
    for forged in (forged_content, forged_span):
        with pytest.raises(PlanningSourceAdapterError, match="invalid_snapshot"):
            replace(occurrence, chunks=(forged,) + occurrence.chunks[1:])


def test_occurrence_graph_is_exactly_derived_from_bound_projection(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, _definition([_roadmap("graph-binding")]))
    binding, descriptor = _first(adapter)
    occurrence = adapter.extract(
        descriptor.evidence.logical_ref,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )

    forged_entities = tuple(
        replace(
            entity,
            content_hash=content_hash("forged:" + entity.natural_key),
            entity_id="",
        )
        for entity in occurrence.entities
    )
    replacements = {
        original.ref(): forged
        for original, forged in zip(occurrence.entities, forged_entities)
    }
    forged_relations = []
    for relation in occurrence.relations:
        source = replacements[relation.source_ref]
        target = replacements[relation.target_ref]
        evidence_by_key = {
            (item.record_kind.value, item.record_id): item
            for item in (source.evidence_ref(), target.evidence_ref())
        }
        endpoint_evidence = tuple(
            evidence_by_key[key] for key in sorted(evidence_by_key)
        )
        forged_relations.append(
            replace(
                relation,
                source_ref=source.ref(),
                target_ref=target.ref(),
                evidence_refs=endpoint_evidence,
                relation_id="",
            )
        )
    with pytest.raises(PlanningSourceAdapterError, match="invalid_snapshot"):
        replace(
            occurrence,
            entities=forged_entities,
            relations=tuple(forged_relations),
        )

    roadmap_relation_index = next(
        index
        for index, relation in enumerate(occurrence.relations)
        if relation.method_ref == "planning.roadmap.belongs_to_project"
    )
    roadmap_relation = occurrence.relations[roadmap_relation_index]
    drifted_relation = replace(
        roadmap_relation,
        relation_kind=RelationKind.RELATED_TO,
        method_ref="planning.edge.blocks",
        relation_id="",
    )
    with pytest.raises(PlanningSourceAdapterError, match="invalid_snapshot"):
        replace(
            occurrence,
            relations=(
                occurrence.relations[:roadmap_relation_index]
                + (drifted_relation,)
                + occurrence.relations[roadmap_relation_index + 1 :]
            ),
        )

    for entities, relations in (
        (tuple(reversed(occurrence.entities)), occurrence.relations),
        (occurrence.entities, occurrence.relations[:-1]),
        (occurrence.entities + (occurrence.entities[-1],), occurrence.relations),
    ):
        with pytest.raises(PlanningSourceAdapterError, match="invalid_snapshot"):
            replace(occurrence, entities=entities, relations=relations)


def test_occurrence_reconstruction_is_bounded_before_graph_construction(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import src.unified_source_index_sources.planning as planning_source

    adapter = _adapter(tmp_path, _definition([_roadmap("graph-parse")]))
    binding, descriptor = _first(adapter)
    base = adapter.extract(
        descriptor.evidence.logical_ref,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )

    def coherent_occurrence_text(content: str):
        aggregate_hash = content_hash(content)
        version = replace(
            base.source_version,
            revision_ref=f"approved:{base.evidence.approved_revision}:{aggregate_hash}",
            content_hash=aggregate_hash,
            source_version_id="",
        )
        evidence = replace(
            base.evidence,
            canonical_projection_hash=aggregate_hash,
            source_version_id=version.source_version_id,
        )
        locator = replace(
            base.projection_locator,
            source_version_id=version.source_version_id,
            canonical_projection_hash=aggregate_hash,
            locator_digest="",
        )
        chunk = type(base.chunks[0]).create(
            version,
            locator=TextRangeLocator(0, len(content)),
            extractor_profile_ref=base.chunks[0].extractor_profile_ref,
            content_hash=aggregate_hash,
            content=content,
            classification=Classification.PRIVATE,
            content_policy=ContentPolicy.INLINE_LOCAL,
        )
        return replace(
            base,
            source_version=version,
            chunks=(chunk,),
            entities=(),
            relations=(),
            projection_locator=locator,
            evidence=evidence,
        )

    def forbidden_graph(*_args, **_kwargs):
        raise AssertionError("graph records constructed before projection validation")

    original_projection = "".join(chunk.content for chunk in base.chunks)
    noncanonical = json.dumps(json.loads(original_projection), ensure_ascii=False, indent=1)
    overdepth = "[" * 33 + "0" + "]" * 33
    overnodes = "[" + ",".join("0" for _ in range(100_001)) + "]"
    with monkeypatch.context() as scoped:
        scoped.setattr(planning_source, "_graph_records", forbidden_graph)
        for content in ("{", noncanonical, overdepth, overnodes):
            with pytest.raises(PlanningSourceAdapterError, match="invalid_snapshot"):
                coherent_occurrence_text(content)


def test_nested_records_preserve_exact_planning_policy_and_provider(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path, _definition([_roadmap("nested-policy")]))
    binding, descriptor = _first(adapter)
    occurrence = adapter.extract(
        descriptor.evidence.logical_ref,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )

    assert occurrence.source.provider_ref == PLANNING_SOURCE_ADAPTER_ID
    assert occurrence.source_version.provider_ref == PLANNING_SOURCE_ADAPTER_ID
    assert occurrence.source.owner_scope == occurrence.source_version.owner_scope == OWNER_SCOPE.value
    assert occurrence.source.classification is Classification.PRIVATE
    assert occurrence.source_version.classification is Classification.PRIVATE
    assert occurrence.source.content_policy is ContentPolicy.INLINE_LOCAL
    assert occurrence.source_version.content_policy is ContentPolicy.INLINE_LOCAL
    assert occurrence.source_version.policy_evidence == occurrence.source.policy_evidence()
    for record in occurrence.chunks + occurrence.entities:
        assert record.owner_scope == OWNER_SCOPE.value
        assert record.classification is Classification.PRIVATE
        assert record.content_policy is ContentPolicy.INLINE_LOCAL
        assert record.policy_evidence == occurrence.source_version.policy_evidence_ref()
    for relation in occurrence.relations:
        assert relation.owner_scope == OWNER_SCOPE.value
        assert relation.classification is Classification.PRIVATE
        assert relation.content_policy is ContentPolicy.METADATA_ONLY
        assert all(
            evidence.source_id == occurrence.source.source_id
            and evidence.source_version_id == occurrence.source_version.source_version_id
            and evidence.policy_evidence.owner_scope == OWNER_SCOPE.value
            and evidence.policy_evidence.classification is Classification.PRIVATE
            and evidence.policy_evidence.content_policy is ContentPolicy.INLINE_LOCAL
            for evidence in relation.evidence_refs
        )

    mismatched_version = replace(
        descriptor.source_version,
        provider_ref="unrelated.provider",
    )
    with pytest.raises(PlanningSourceAdapterError, match="invalid_snapshot"):
        replace(descriptor, source_version=mismatched_version)

    drifted_entity = replace(
        occurrence.entities[0],
        classification=Classification.SENSITIVE,
    )
    with pytest.raises(PlanningSourceAdapterError, match="invalid_snapshot"):
        replace(
            occurrence,
            entities=(drifted_entity,) + occurrence.entities[1:],
        )


def test_belongs_to_edges_point_from_member_to_container(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path, _definition([_roadmap("belongs-direction")]))
    binding, descriptor = _first(adapter)
    occurrence = adapter.extract(
        descriptor.evidence.logical_ref,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    natural_key_by_ref = {entity.ref(): entity.natural_key for entity in occurrence.entities}
    belongs_to = {
        (
            natural_key_by_ref[relation.source_ref],
            natural_key_by_ref[relation.target_ref],
            relation.method_ref,
        )
        for relation in occurrence.relations
        if relation.relation_kind is RelationKind.BELONGS_TO
    }
    assert belongs_to == {
        (
            "roadmap:belongs-direction",
            "project:demo-project",
            "planning.roadmap.belongs_to_project",
        ),
        (
            "node:service-contract",
            "roadmap:belongs-direction",
            "planning.node.belongs_to_roadmap",
        ),
        (
            "gate:definition-go",
            "roadmap:belongs-direction",
            "planning.gate.belongs_to_roadmap",
        ),
    }



def test_self_edge_evidence_is_canonical_unique_and_endpoint_complete(
    tmp_path: Path,
) -> None:
    nodes = [_node("first"), _node("second"), _node("third")]
    edges = [
        {"from": "first", "to": "first", "kind": "blocks"},
        {"from": "first", "to": "first", "kind": "unlocks"},
        {"from": "second", "to": "first", "kind": "blocks"},
    ]
    adapter = _adapter(
        tmp_path,
        _definition([_roadmap("self-evidence", nodes=nodes, edges=edges, gates=[])]),
    )
    binding, descriptor = _first(adapter)
    occurrence = adapter.extract(
        descriptor.evidence.logical_ref,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    entity_by_ref = {entity.ref(): entity for entity in occurrence.entities}
    edge_relations = [
        relation
        for relation in occurrence.relations
        if relation.method_ref in {"planning.edge.blocks", "planning.edge.unlocks"}
    ]
    self_edges = [
        relation for relation in edge_relations if relation.source_ref == relation.target_ref
    ]
    assert {relation.method_ref for relation in self_edges} == {
        "planning.edge.blocks",
        "planning.edge.unlocks",
    }
    assert all(len(relation.evidence_refs) == 1 for relation in self_edges)

    ordinary = next(
        relation
        for relation in edge_relations
        if entity_by_ref[relation.source_ref].natural_key == "node:second"
        and entity_by_ref[relation.target_ref].natural_key == "node:first"
    )
    ordinary_keys = tuple(
        (evidence.record_kind.value, evidence.record_id)
        for evidence in ordinary.evidence_refs
    )
    assert len(ordinary.evidence_refs) == 2
    assert ordinary_keys == tuple(sorted(ordinary_keys))

    ordinary_index = occurrence.relations.index(ordinary)
    missing_endpoint = replace(ordinary, evidence_refs=ordinary.evidence_refs[:1])
    with pytest.raises(PlanningSourceAdapterError, match="invalid_snapshot"):
        replace(
            occurrence,
            relations=(
                occurrence.relations[:ordinary_index]
                + (missing_endpoint,)
                + occurrence.relations[ordinary_index + 1 :]
            ),
        )

    unrelated = next(
        entity for entity in occurrence.entities if entity.natural_key == "node:third"
    )
    unrelated_third = replace(
        ordinary,
        evidence_refs=ordinary.evidence_refs + (unrelated.evidence_ref(),),
    )
    with pytest.raises(PlanningSourceAdapterError, match="invalid_snapshot"):
        replace(
            occurrence,
            relations=(
                occurrence.relations[:ordinary_index]
                + (unrelated_third,)
                + occurrence.relations[ordinary_index + 1 :]
            ),
        )


def test_occurrence_enforces_aggregate_utf8_budget_before_join(tmp_path: Path) -> None:
    adapter = _adapter(tmp_path / "over", _definition([_roadmap("utf8-budget")]))
    binding, descriptor = _first(adapter)
    base = adapter.extract(
        descriptor.evidence.logical_ref,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )

    parts = tuple("é" * 125_001 for _ in range(8))
    aggregate_hash = content_hash("".join(parts))
    version = replace(
        base.source_version,
        revision_ref=f"approved:{base.evidence.approved_revision}:{aggregate_hash}",
        content_hash=aggregate_hash,
        source_version_id="",
    )
    evidence = replace(
        base.evidence,
        canonical_projection_hash=aggregate_hash,
        source_version_id=version.source_version_id,
    )
    locator = replace(
        base.projection_locator,
        source_version_id=version.source_version_id,
        canonical_projection_hash=aggregate_hash,
        locator_digest="",
    )
    chunks = []
    start = 0
    for part in parts:
        end = start + len(part)
        chunks.append(
            type(base.chunks[0]).create(
                version,
                locator=TextRangeLocator(start, end),
                extractor_profile_ref=base.chunks[0].extractor_profile_ref,
                content_hash=content_hash(part),
                content=part,
                classification=Classification.PRIVATE,
                content_policy=ContentPolicy.INLINE_LOCAL,
            )
        )
        start = end
    with pytest.raises(PlanningSourceAdapterError, match="invalid_snapshot"):
        replace(
            base,
            source_version=version,
            chunks=tuple(chunks),
            entities=(),
            relations=(),
            projection_locator=locator,
            evidence=evidence,
        )

    import src.unified_source_index_sources.planning as planning_source

    projection = json.loads("".join(chunk.content for chunk in base.chunks))
    roadmap = projection["roadmap"]
    roadmap["objective"] += "x" * (
        MAX_PROJECTION_UTF8_BYTES - len(_canonical(projection))
    )
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    projection_text = _canonical(projection)
    assert len(projection_text.encode("utf-8")) == MAX_PROJECTION_UTF8_BYTES

    projection_hash = content_hash(projection_text)
    exact_version = replace(
        base.source_version,
        revision_ref=f"approved:{base.evidence.approved_revision}:{projection_hash}",
        content_hash=projection_hash,
        source_version_id="",
    )
    exact_evidence = replace(
        base.evidence,
        domain_content_hash=roadmap["content_hash"],
        canonical_projection_hash=projection_hash,
        source_version_id=exact_version.source_version_id,
    )
    exact_locator = replace(
        base.projection_locator,
        source_version_id=exact_version.source_version_id,
        canonical_projection_hash=projection_hash,
        locator_digest="",
    )
    exact_chunks = []
    for start in range(0, len(projection_text), 262_144):
        end = min(start + 262_144, len(projection_text))
        part = projection_text[start:end]
        exact_chunks.append(
            type(base.chunks[0]).create(
                exact_version,
                locator=TextRangeLocator(start, end),
                extractor_profile_ref=base.chunks[0].extractor_profile_ref,
                content_hash=content_hash(part),
                content=part,
                classification=Classification.PRIVATE,
                content_policy=ContentPolicy.INLINE_LOCAL,
            )
        )
    exact_entities, exact_relations = planning_source._graph_records(
        exact_version,
        projection,
        projection_text,
    )
    exact_occurrence = replace(
        base,
        source_version=exact_version,
        chunks=tuple(exact_chunks),
        entities=exact_entities,
        relations=exact_relations,
        projection_locator=exact_locator,
        evidence=exact_evidence,
    )
    reconstructed = "".join(chunk.content for chunk in exact_occurrence.chunks)
    assert len(reconstructed.encode("utf-8")) == MAX_PROJECTION_UTF8_BYTES



@pytest.mark.parametrize("operation_name", ("observe_version", "extract"))
def test_logical_ref_is_validated_before_recapture(
    tmp_path: Path,
    monkeypatch,
    operation_name: str,
) -> None:
    adapter = _adapter(tmp_path, _definition([_roadmap(f"preflight-{operation_name}")]))
    binding, descriptor = _first(adapter)
    capture_calls = 0

    def forbidden_capture(*_args, **_kwargs):
        nonlocal capture_calls
        capture_calls += 1
        raise AssertionError("capture must not run for an invalid public request")

    class StringSubclass(str):
        pass

    invalid_refs = (
        object(),
        StringSubclass(descriptor.evidence.logical_ref),
        "x" * 97,
        "planning:roadmap:sha256:" + ("g" * 64),
    )
    operation = getattr(adapter, operation_name)
    arguments = {
        "expected_binding_digest": binding.binding_digest,
        "expected_snapshot_digest": binding.snapshot_digest,
    }
    with monkeypatch.context() as scoped:
        scoped.setattr(
            PlanningMcpService,
            "capture_owner_source_snapshot",
            forbidden_capture,
        )
        for invalid_ref in invalid_refs:
            with pytest.raises(PlanningSourceAdapterError, match="^invalid_request$") as raised:
                operation(invalid_ref, **arguments)
            assert raised.value.code == "invalid_request"
    assert capture_calls == 0

    result = operation(descriptor.evidence.logical_ref, **arguments)
    if operation_name == "observe_version":
        assert result == descriptor
    else:
        assert result.evidence == descriptor.evidence
        assert result.chunks


def test_discover_cursor_is_bounded_before_recapture(
    tmp_path: Path,
    monkeypatch,
) -> None:
    adapter = _adapter(tmp_path, _definition([_roadmap("preflight-cursor")]))
    binding, descriptor = _first(adapter)
    capture_calls = 0

    def forbidden_capture(*_args, **_kwargs):
        nonlocal capture_calls
        capture_calls += 1
        raise AssertionError("capture must not run for an invalid public request")

    class IntSubclass(int):
        pass

    arguments = {
        "expected_binding_digest": binding.binding_digest,
        "expected_snapshot_digest": binding.snapshot_digest,
    }
    with monkeypatch.context() as scoped:
        scoped.setattr(
            PlanningMcpService,
            "capture_owner_source_snapshot",
            forbidden_capture,
        )
        for invalid_cursor in (2, 101, 10**100, IntSubclass(1), True, -1):
            with pytest.raises(PlanningSourceAdapterError, match="^invalid_request$") as raised:
                adapter.discover(cursor=invalid_cursor, **arguments)
            assert raised.value.code == "invalid_request"
    assert capture_calls == 0

    page = adapter.discover(cursor=0, **arguments)
    assert page.items == (descriptor,)
    terminal = adapter.discover(cursor=1, **arguments)
    assert terminal.items == ()
    assert terminal.next_cursor is None


def test_unknown_ref_and_stale_digest_preserve_authority_precedence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path, _definition([_roadmap("preflight-precedence")]))
    adapter = PlanningSourceAdapter(
        service=service,
        owner_scope=OWNER_SCOPE,
        expected_owner_ref=OWNER_REF,
        observed_at=OBSERVED_AT,
        max_sources=1,
    )
    binding = adapter.binding
    unknown_ref = PlanningSourceAdapter.logical_ref("demo-project", "unknown")
    original_capture = PlanningMcpService.capture_owner_source_snapshot
    capture_calls = 0

    def counted_capture(self, expected_owner_ref, *, max_sources=100):
        nonlocal capture_calls
        capture_calls += 1
        return original_capture(self, expected_owner_ref, max_sources=max_sources)

    monkeypatch.setattr(
        PlanningMcpService,
        "capture_owner_source_snapshot",
        counted_capture,
    )
    with pytest.raises(PlanningSourceAdapterError, match="^record_not_found$"):
        adapter.observe_version(
            unknown_ref,
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )
    assert capture_calls == 1

    capture_calls = 0
    with pytest.raises(PlanningSourceAdapterError, match="^stale_authority$"):
        adapter.observe_version(
            unknown_ref,
            expected_binding_digest="sha256:" + ("0" * 64),
            expected_snapshot_digest=binding.snapshot_digest,
        )
    assert capture_calls == 0
