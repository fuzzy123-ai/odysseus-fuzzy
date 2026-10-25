from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import inspect
import json
import re

import pytest

from src.planning_mcp_service import PlanningMcpService, PlanningServiceError
from src.rag_text_chunking import EXACT_CHARACTER_SPLITTER_VERSION
from src.unified_source_index_contract import (
    Classification,
    ContentPolicy,
    SourceKind,
    TextRangeLocator,
    content_hash,
)
from src.unified_source_index_owner_scope import OwnerScope
from src.unified_source_index_sources.planning_markdown import (
    MAX_MARKDOWN_BODY_BYTES,
    MAX_PLANNING_MARKDOWN_CHUNKS,
    PLANNING_MARKDOWN_SOURCE_ADAPTER_GENERATION,
    PLANNING_MARKDOWN_SOURCE_ADAPTER_ID,
    PLANNING_MARKDOWN_SOURCE_ADAPTER_VERSION,
    PLANNING_MARKDOWN_SOURCE_EXACT_READER_BOUNDARY,
    PLANNING_MARKDOWN_SOURCE_EXTRACTOR_PROFILE,
    PlanningMarkdownCharacterRangeRequest,
    PlanningMarkdownExactRead,
    PlanningMarkdownSourceAdapter,
    PlanningMarkdownSourceAdapterError,
)


OWNER_SCOPE = OwnerScope("owner:owner_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
OWNER_REF = "synthetic-owner"
OBSERVED_AT = "2026-09-07T08:00:00Z"
POLICY_REF = "docs/plans/harbor-planning-project-storage-contract.md#explicit-approved-markdown-body-reader"
SHA = re.compile(r"^sha256:[0-9a-f]{64}$")
GENERATION = "usi_generation_c98d4a148c15fac19c05c17fbcb771fbad5f143481434f9b2f50af4867815edd"


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _digest(value: object) -> str:
    return _sha_bytes(_canonical_bytes(value))


def _descriptor(
    body: str,
    *,
    owner_ref: str = OWNER_REF,
    project_id: str = "demo-project",
    roadmap_id: str = "core-map",
    source_ref: str = "docs/plans/rendered-handoff.md",
    approved_revision: int = 3,
    domain_content_hash: str = "sha256:" + "1" * 64,
    canonical_projection_hash: str = "sha256:" + "2" * 64,
    planning_source_ref: str = "docs/plans/owner-source.json",
    planning_snapshot_digest: str = "sha256:" + "3" * 64,
    selection_digest: str = "sha256:" + "4" * 64,
) -> dict[str, object]:
    raw = body.encode("utf-8")
    result: dict[str, object] = {
        "schema_id": "odysseus.planning.owner_markdown_descriptor.v1",
        "owner_ref": owner_ref,
        "project_id": project_id,
        "roadmap_id": roadmap_id,
        "approved_revision": approved_revision,
        "domain_content_hash": domain_content_hash,
        "canonical_projection_hash": canonical_projection_hash,
        "planning_source_ref": planning_source_ref,
        "planning_snapshot_digest": planning_snapshot_digest,
        "selection_digest": selection_digest,
        "source_ref": source_ref,
        "body_sha256": _sha_bytes(raw),
        "byte_count": len(raw),
        "classification": "private",
        "content_policy": "inline_local",
        "owner_policy_ref": POLICY_REF,
        "body_exact": True,
        "canonical_truth": False,
    }
    result["version_digest"] = _digest(result)
    return result


def _capture(*descriptors: dict[str, object], owner_ref: str = OWNER_REF) -> dict[str, object]:
    ordered = sorted(
        (deepcopy(item) for item in descriptors),
        key=lambda item: (item["project_id"], item["roadmap_id"], item["source_ref"]),
    )
    planning = (
        ordered[0]["planning_snapshot_digest"] if ordered else "sha256:" + "3" * 64
    )
    selection = ordered[0]["selection_digest"] if ordered else "sha256:" + "4" * 64
    base: dict[str, object] = {
        "schema_id": "odysseus.planning.owner_markdown_capture.v1",
        "owner_ref": owner_ref,
        "selection_mode": "explicit_approved_ref_and_expected_bytes",
        "selection_digest": selection,
        "planning_snapshot_digest": planning,
        "descriptors": ordered,
        "eligible_count": len(ordered),
        "classification": "private",
        "content_policy": "inline_local",
        "owner_policy_ref": POLICY_REF,
        "body_exact": True,
        "canonical_truth": False,
        "read_only": True,
        "writes_performed": False,
    }
    return {**base, "snapshot_digest": _digest(base)}


def _read(capture: dict[str, object], descriptor: dict[str, object], body: str) -> dict[str, object]:
    return {
        "schema_id": "odysseus.planning.owner_markdown_body_read.v1",
        "owner_ref": descriptor["owner_ref"],
        "snapshot_digest": capture["snapshot_digest"],
        "selection_digest": capture["selection_digest"],
        "planning_snapshot_digest": capture["planning_snapshot_digest"],
        "descriptor": deepcopy(descriptor),
        "content": body,
        "body_sha256": descriptor["body_sha256"],
        "byte_count": descriptor["byte_count"],
        "classification": "private",
        "content_policy": "inline_local",
        "owner_policy_ref": POLICY_REF,
        "body_exact": True,
        "canonical_truth": False,
        "read_only": True,
        "writes_performed": False,
    }


def _fake_service(
    monkeypatch: pytest.MonkeyPatch,
    bodies: dict[str, str],
    *,
    capture: dict[str, object] | None = None,
):
    descriptors = tuple(
        _descriptor(body, source_ref=source_ref, roadmap_id=f"roadmap-{index}")
        for index, (source_ref, body) in enumerate(sorted(bodies.items()), 1)
    )
    state = {"capture": deepcopy(capture or _capture(*descriptors))}
    calls = {"capture": 0, "read": 0}

    def capture_owner_markdown_snapshot(self, expected_owner_ref):
        calls["capture"] += 1
        assert expected_owner_ref == OWNER_REF
        return deepcopy(state["capture"])

    def read_owner_markdown_body(
        self, descriptor, *, expected_owner_ref, expected_snapshot_digest
    ):
        calls["read"] += 1
        assert expected_owner_ref == OWNER_REF
        assert expected_snapshot_digest == state["capture"]["snapshot_digest"]
        source_ref = descriptor["source_ref"]
        return _read(state["capture"], descriptor, bodies[source_ref])

    monkeypatch.setattr(
        PlanningMcpService,
        "capture_owner_markdown_snapshot",
        capture_owner_markdown_snapshot,
    )
    monkeypatch.setattr(
        PlanningMcpService,
        "read_owner_markdown_body",
        read_owner_markdown_body,
    )
    service = object.__new__(PlanningMcpService)
    return service, state, calls


def _adapter(monkeypatch: pytest.MonkeyPatch, bodies: dict[str, str]):
    service, state, calls = _fake_service(monkeypatch, bodies)
    adapter = PlanningMarkdownSourceAdapter(
        service=service,
        owner_scope=OWNER_SCOPE,
        expected_owner_ref=OWNER_REF,
        observed_at=OBSERVED_AT,
    )
    return adapter, state, calls


def _first(adapter: PlanningMarkdownSourceAdapter):
    binding = adapter.binding
    page = adapter.discover(
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    return binding, page.items[0]


def test_internal_provider_constants_are_frozen_and_unregistered() -> None:
    import src.unified_source_index_sources.planning_markdown as module
    from src.unified_source_index_source_registry import SourceAdapterRegistry
    from src.unified_source_index_sources.planning import planning_source_registration

    assert PLANNING_MARKDOWN_SOURCE_ADAPTER_ID == "planning.approved_markdown_body"
    assert PLANNING_MARKDOWN_SOURCE_ADAPTER_VERSION == "v1"
    assert PLANNING_MARKDOWN_SOURCE_ADAPTER_GENERATION == GENERATION
    assert PLANNING_MARKDOWN_SOURCE_EXACT_READER_BOUNDARY == "planning.owner_markdown_body_reader"
    assert PLANNING_MARKDOWN_SOURCE_EXTRACTOR_PROFILE == "planning.approved_markdown_body.exact.v1"
    assert "planning_markdown_source_registration" not in module.__dict__
    assert "planning_markdown_source_capability_manifest" not in module.__dict__
    registry = SourceAdapterRegistry((planning_source_registration(),))
    assert registry.for_domain("planning").adapter_id == "planning.approved_json"
    assert all(item.adapter_id != PLANNING_MARKDOWN_SOURCE_ADAPTER_ID for item in registry.manifests())


def test_discover_binds_exact_owner_project_roadmap_and_source_ref(monkeypatch) -> None:
    bodies = {
        "docs/plans/one.md": "same body",
        "docs/plans/two.md": "same body",
    }
    adapter, _state, _calls = _adapter(monkeypatch, bodies)
    binding = adapter.binding
    page = adapter.discover(
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    assert len(page.items) == 2
    first, second = page.items
    assert first.source.canonical_ref != second.source.canonical_ref
    assert first.source.source_id != second.source.source_id
    assert first.source_version.content_hash == second.source_version.content_hash == content_hash("same body")
    assert first.source.source_kind is second.source.source_kind is SourceKind.PLANNING
    assert first.source.provider_ref == PLANNING_MARKDOWN_SOURCE_ADAPTER_ID
    assert first.source.classification is Classification.PRIVATE
    assert first.source.content_policy is ContentPolicy.INLINE_LOCAL
    expected_ref = adapter.logical_ref(
        OWNER_REF,
        first.evidence.project_id,
        first.evidence.roadmap_id,
        first.evidence.source_ref,
    )
    assert first.source.canonical_ref == first.evidence.logical_ref == expected_ref
    assert first.projection_locator is first.body_locator
    assert SHA.fullmatch(first.evidence.revision_digest)
    assert first.source_version.revision_ref == (
        f"approved-markdown:{first.evidence.approved_revision}:{first.evidence.revision_digest}"
    )
    assert binding.adapter_generation == GENERATION


def test_extract_preserves_crlf_bom_unicode_and_has_no_graph(monkeypatch) -> None:
    body = "\ufeff# Exact\r\n\r\nUnicode: \u03bb\U0001f642\r\n"
    adapter, _state, calls = _adapter(monkeypatch, {"docs/plans/exact.md": body})
    binding, descriptor = _first(adapter)
    occurrence = adapter.extract(
        descriptor.evidence.logical_ref,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    assert "".join(chunk.content for chunk in occurrence.chunks) == body
    assert occurrence.source_version.content_hash == content_hash(body)
    assert occurrence.evidence.body_sha256 == content_hash(body)
    assert occurrence.evidence.byte_count == len(body.encode("utf-8"))
    assert occurrence.splitter_version == EXACT_CHARACTER_SPLITTER_VERSION
    assert occurrence.entities == ()
    assert occurrence.relations == ()
    assert calls["read"] == 1
    assert body not in repr(occurrence)


def test_empty_body_is_valid_version_with_zero_chunks_and_no_exact_range(monkeypatch) -> None:
    adapter, _state, calls = _adapter(monkeypatch, {"docs/plans/empty.md": ""})
    binding, descriptor = _first(adapter)
    occurrence = adapter.extract(
        descriptor.evidence.logical_ref,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    assert descriptor.source_version.content_hash == content_hash("")
    assert descriptor.evidence.byte_count == 0
    assert occurrence.chunks == occurrence.entities == occurrence.relations == ()
    before = calls["read"]
    with pytest.raises(Exception):
        TextRangeLocator(0, 0)
    assert calls["read"] == before


def test_two_million_ascii_bytes_use_exactly_eight_shared_ranges(monkeypatch) -> None:
    body = "x" * MAX_MARKDOWN_BODY_BYTES
    adapter, _state, _calls = _adapter(monkeypatch, {"docs/plans/large.md": body})
    binding, descriptor = _first(adapter)
    occurrence = adapter.extract(
        descriptor.evidence.logical_ref,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    assert len(occurrence.chunks) == MAX_PLANNING_MARKDOWN_CHUNKS == 8
    assert occurrence.chunks[0].locator.start_char == 0
    assert occurrence.chunks[-1].locator.end_char == len(body)
    full = PlanningMarkdownCharacterRangeRequest(
        descriptor.body_locator, TextRangeLocator(0, len(body))
    )
    exact = adapter.read_exact(
        full, expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    assert exact.content_hash == content_hash(body)
    assert len(exact.content) == MAX_MARKDOWN_BODY_BYTES
    assert all(
        left.locator.end_char == right.locator.start_char
        for left, right in zip(occurrence.chunks, occurrence.chunks[1:])
    )


def test_exact_read_is_request_and_unicode_character_bound(monkeypatch) -> None:
    body = "\u03bb\U0001f642abc"
    adapter, _state, calls = _adapter(monkeypatch, {"docs/plans/unicode.md": body})
    binding, descriptor = _first(adapter)
    request = PlanningMarkdownCharacterRangeRequest(
        descriptor.body_locator,
        TextRangeLocator(1, 3),
    )
    result = adapter.read_exact(
        request,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    assert isinstance(result, PlanningMarkdownExactRead)
    assert result.content == "\U0001f642a"
    assert result.content_hash == content_hash("\U0001f642a")
    assert body not in repr(result)
    # byte_count is only a safe pre-read upper bound; exact character length is post-read.
    byte_bounded = PlanningMarkdownCharacterRangeRequest(
        descriptor.body_locator,
        TextRangeLocator(len(body), descriptor.evidence.byte_count),
    )
    before = calls["read"]
    with pytest.raises(PlanningMarkdownSourceAdapterError, match="invalid_request"):
        adapter.read_exact(
            byte_bounded,
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )
    assert calls["read"] == before + 1


def test_invalid_range_is_rejected_before_recapture_or_body_read(monkeypatch) -> None:
    body = "abc"
    adapter, _state, calls = _adapter(monkeypatch, {"docs/plans/body.md": body})
    binding, descriptor = _first(adapter)
    baseline = dict(calls)
    with pytest.raises(PlanningMarkdownSourceAdapterError, match="invalid_request"):
        PlanningMarkdownCharacterRangeRequest(
            descriptor.body_locator,
            TextRangeLocator(0, descriptor.evidence.byte_count + 1),
        )
    assert calls == baseline
    with pytest.raises(PlanningMarkdownSourceAdapterError, match="invalid_request"):
        adapter.read_exact(
            object(),
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )
    assert calls == baseline


def test_occurrence_rejects_full_body_and_identity_forgery(monkeypatch) -> None:
    adapter, _state, _calls = _adapter(monkeypatch, {"docs/plans/body.md": "abcdef"})
    binding, descriptor = _first(adapter)
    occurrence = adapter.extract(
        descriptor.evidence.logical_ref,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    bad_chunk = replace(occurrence.chunks[0], content="abcdeg", content_hash=content_hash("abcdeg"))
    with pytest.raises(PlanningMarkdownSourceAdapterError, match="invalid_snapshot"):
        replace(occurrence, chunks=(bad_chunk,))
    with pytest.raises(PlanningMarkdownSourceAdapterError, match="invalid_snapshot"):
        replace(occurrence.evidence, byte_count=occurrence.evidence.byte_count - 1)
    with pytest.raises(PlanningMarkdownSourceAdapterError, match="invalid_snapshot"):
        replace(occurrence, entities=(object(),))


def test_public_dtos_are_closed_detached_and_revalidate_exact_types(monkeypatch) -> None:
    adapter, _state, _calls = _adapter(monkeypatch, {"docs/plans/body.md": "body"})
    binding, descriptor = _first(adapter)
    assert descriptor is not adapter.discover(
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    ).items[0]
    with pytest.raises(PlanningMarkdownSourceAdapterError, match="invalid_snapshot"):
        replace(descriptor.evidence, approved_revision=True)
    with pytest.raises(PlanningMarkdownSourceAdapterError, match="invalid_request"):
        replace(descriptor.body_locator, byte_count=True)
    request = PlanningMarkdownCharacterRangeRequest(
        descriptor.body_locator, TextRangeLocator(0, 1)
    )
    with pytest.raises(PlanningMarkdownSourceAdapterError, match="invalid_request"):
        replace(request, request_digest="sha256:" + "f" * 64)


def test_every_revision_component_changes_version_identity(monkeypatch) -> None:
    base = _descriptor("body")
    fields = (
        "approved_revision",
        "domain_content_hash",
        "canonical_projection_hash",
        "planning_source_ref",
        "planning_snapshot_digest",
        "selection_digest",
        "source_ref",
        "body_sha256",
        "byte_count",
    )
    seen: set[str] = set()
    for index, field in enumerate(fields, 5):
        changed = deepcopy(base)
        if field == "approved_revision":
            changed[field] = index
        elif field == "planning_source_ref":
            changed[field] = f"docs/plans/source-{index}.json"
        elif field == "source_ref":
            changed[field] = f"docs/plans/body-{index}.md"
        elif field == "byte_count":
            changed[field] = index
        else:
            changed[field] = "sha256:" + f"{index:x}" * 64
        changed["version_digest"] = _digest({k: v for k, v in changed.items() if k != "version_digest"})
        capture = _capture(changed)
        service, _state, _calls = _fake_service(
            monkeypatch,
            {str(changed["source_ref"]): "body"},
            capture=capture,
        )
        adapter = PlanningMarkdownSourceAdapter(
            service=service,
            owner_scope=OWNER_SCOPE,
            expected_owner_ref=OWNER_REF,
            observed_at=OBSERVED_AT,
        )
        _binding, descriptor = _first(adapter)
        seen.add(descriptor.source_version.source_version_id)
    assert len(seen) == len(fields)


def test_stale_capture_and_service_replacement_fail_closed(monkeypatch) -> None:
    adapter, state, calls = _adapter(monkeypatch, {"docs/plans/body.md": "body"})
    binding = adapter.binding
    state["capture"]["snapshot_digest"] = "sha256:" + "f" * 64
    with pytest.raises(PlanningMarkdownSourceAdapterError, match="stale_authority"):
        adapter.discover(
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )
    assert calls["read"] == 0
    object.__setattr__(adapter, "_service", object.__new__(PlanningMcpService))
    with pytest.raises(PlanningMarkdownSourceAdapterError, match="stale_authority"):
        adapter.discover(
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )


def test_observe_unavailable_never_emits_absence_evidence(monkeypatch) -> None:
    adapter, _state, calls = _adapter(monkeypatch, {})
    binding = adapter.binding
    ref = adapter.logical_ref(OWNER_REF, "demo-project", "core-map", "docs/plans/x.md")
    with pytest.raises(PlanningMarkdownSourceAdapterError, match="unavailable_state_unproven"):
        adapter.observe_unavailable(
            ref,
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )
    assert calls["capture"] == 2
    assert "PlanningMarkdownUnavailableObservation" not in __import__(
        "src.unified_source_index_sources.planning_markdown", fromlist=["*"]
    ).__dict__


def test_malformed_capture_and_service_errors_are_fixed_and_context_free(monkeypatch) -> None:
    service, state, _calls = _fake_service(monkeypatch, {})
    state["capture"]["foreign"] = "secret body text"
    with pytest.raises(PlanningMarkdownSourceAdapterError) as rejected:
        PlanningMarkdownSourceAdapter(
            service=service,
            owner_scope=OWNER_SCOPE,
            expected_owner_ref=OWNER_REF,
            observed_at=OBSERVED_AT,
        )
    assert rejected.value.code == "invalid_authority"
    assert str(rejected.value) == "invalid_authority"
    assert rejected.value.__cause__ is None
    assert rejected.value.__context__ is None
    assert "secret body text" not in repr(rejected.value)

    def fail_capture(self, expected_owner_ref):
        raise PlanningServiceError("owner_markdown_stale", "private upstream body")

    monkeypatch.setattr(PlanningMcpService, "capture_owner_markdown_snapshot", fail_capture)
    with pytest.raises(PlanningMarkdownSourceAdapterError) as service_rejected:
        PlanningMarkdownSourceAdapter(
            service=object.__new__(PlanningMcpService),
            owner_scope=OWNER_SCOPE,
            expected_owner_ref=OWNER_REF,
            observed_at=OBSERVED_AT,
        )
    assert service_rejected.value.code == "invalid_authority"
    assert service_rejected.value.__cause__ is None
    assert service_rejected.value.__context__ is None
    assert "private" not in repr(service_rejected.value)


def test_adapter_source_uses_only_public_markdown_service_boundary() -> None:
    source = inspect.getsource(PlanningMarkdownSourceAdapter)
    assert "capture_owner_markdown_snapshot" in source
    assert "read_owner_markdown_body" in source
    for forbidden in (
        "capture_owner_source_snapshot",
        "read_owner_source_projection",
        "_capture_owner_markdown_snapshot",
        "_definition_store",
        "repo_root",
        "open(",
        "glob(",
        "walk(",
    ):
        assert forbidden not in source


@pytest.mark.parametrize(
    "bad",
    (None, True, 0, "", "sha256:" + "A" * 64, "sha256:" + "0" * 63),
)
def test_logical_ref_and_digest_inputs_fail_before_service_io(monkeypatch, bad) -> None:
    adapter, _state, calls = _adapter(monkeypatch, {})
    baseline = dict(calls)
    if bad in (None, True, 0, ""):
        with pytest.raises(PlanningMarkdownSourceAdapterError, match="invalid_request"):
            adapter.logical_ref(bad, "project", "roadmap", "docs/plans/a.md")
    with pytest.raises(PlanningMarkdownSourceAdapterError, match="invalid_request"):
        adapter.discover(
            expected_binding_digest=bad,
            expected_snapshot_digest=adapter.binding.snapshot_digest,
        )
    assert calls == baseline


@pytest.mark.parametrize(
    "bad_ref",
    (
        "docs/plans/e\u0301.md",
        "docs/plans/CON.md",
        "docs/plans/a.md:stream",
        "docs\\plans\\a.md",
        "/docs/plans/a.md",
    ),
)
def test_noncanonical_and_windows_alias_refs_are_rejected(bad_ref) -> None:
    with pytest.raises(PlanningMarkdownSourceAdapterError, match="invalid_request"):
        PlanningMarkdownSourceAdapter.logical_ref(
            OWNER_REF, "project", "roadmap", bad_ref
        )


def test_unpaired_unicode_owner_is_fixed_invalid_request() -> None:
    with pytest.raises(PlanningMarkdownSourceAdapterError) as rejected:
        PlanningMarkdownSourceAdapter.logical_ref(
            "\ud800", "project", "roadmap", "docs/plans/a.md"
        )
    assert rejected.value.code == "invalid_request"
    assert rejected.value.__cause__ is None
    assert rejected.value.__context__ is None


def test_forged_body_read_response_is_rejected_without_content_in_error(monkeypatch) -> None:
    body = "private markdown body"
    adapter, state, _calls = _adapter(monkeypatch, {"docs/plans/body.md": body})
    binding, descriptor = _first(adapter)

    def forged_read(self, raw_descriptor, *, expected_owner_ref, expected_snapshot_digest):
        result = _read(state["capture"], raw_descriptor, body)
        result["content"] = body + " changed"
        return result

    monkeypatch.setattr(PlanningMcpService, "read_owner_markdown_body", forged_read)
    with pytest.raises(PlanningMarkdownSourceAdapterError) as rejected:
        adapter.extract(
            descriptor.evidence.logical_ref,
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )
    assert rejected.value.code == "invalid_snapshot"
    assert rejected.value.__cause__ is None
    assert rejected.value.__context__ is None
    assert body not in repr(rejected.value)


def test_service_read_failure_is_stale_and_context_free(monkeypatch) -> None:
    adapter, _state, _calls = _adapter(monkeypatch, {"docs/plans/body.md": "body"})
    binding, descriptor = _first(adapter)

    def fail_read(self, raw_descriptor, *, expected_owner_ref, expected_snapshot_digest):
        raise PlanningServiceError("owner_markdown_stale", "private upstream detail")

    monkeypatch.setattr(PlanningMcpService, "read_owner_markdown_body", fail_read)
    with pytest.raises(PlanningMarkdownSourceAdapterError) as rejected:
        adapter.extract(
            descriptor.evidence.logical_ref,
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )
    assert rejected.value.code == "stale_authority"
    assert rejected.value.__cause__ is None
    assert rejected.value.__context__ is None
    assert "upstream" not in repr(rejected.value)



def test_real_planning_service_public_markdown_boundary_end_to_end(tmp_path) -> None:
    from tests.test_planning_mcp_service import _owner_markdown_test_service

    body = "\ufeff# Integrated\r\n\r\nUnicode: \\u03bb\\U0001f642\r\n"
    service, _store, _definition, _body_path = _owner_markdown_test_service(
        tmp_path, body.encode("utf-8")
    )
    adapter = PlanningMarkdownSourceAdapter(
        service=service,
        owner_scope=OWNER_SCOPE,
        expected_owner_ref="owner-1",
        observed_at=OBSERVED_AT,
    )
    binding = adapter.binding
    page = adapter.discover(
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    assert len(page.items) == 1
    descriptor = page.items[0]
    occurrence = adapter.extract(
        descriptor.evidence.logical_ref,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    assert "".join(chunk.content for chunk in occurrence.chunks) == body
    request = PlanningMarkdownCharacterRangeRequest(
        descriptor.body_locator, TextRangeLocator(0, len(body))
    )
    exact = adapter.read_exact(
        request,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    assert exact.content == body
    assert exact.content_hash == content_hash(body)



class _G1BehaviorBomb:
    def __init__(self) -> None:
        self.calls = 0

    def __eq__(self, other):
        self.calls += 1
        raise AssertionError("hostile equality invoked")

    @property
    def to_dict(self):
        self.calls += 1
        raise AssertionError("hostile serializer property invoked")


class _G1CountingDescriptor(dict):
    def __init__(self, value, counter):
        super().__init__(value)
        self._counter = counter

    def items(self):
        self._counter[0] += 1
        return super().items()


def test_g1_capture_callback_cannot_mutate_retained_observed_context(monkeypatch) -> None:
    adapter, state, calls = _adapter(monkeypatch, {"docs/plans/body.md": "body"})
    binding = adapter.binding

    def mutate_capture(self, expected_owner_ref):
        calls["capture"] += 1
        object.__setattr__(adapter, "_observed_at", "2026-09-07T09:00:00Z")
        return deepcopy(state["capture"])

    monkeypatch.setattr(
        PlanningMcpService, "capture_owner_markdown_snapshot", mutate_capture
    )
    with pytest.raises(PlanningMarkdownSourceAdapterError) as rejected:
        adapter.discover(
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )
    assert rejected.value.code == "stale_authority"


@pytest.mark.parametrize(
    "field",
    ("_expected_owner_ref", "_observed_at", "_binding_fields", "_snapshot"),
)
def test_g1_body_callback_cannot_mutate_any_retained_operation_context(
    monkeypatch, field
) -> None:
    body = "body"
    adapter, state, _calls = _adapter(monkeypatch, {"docs/plans/body.md": body})
    binding, descriptor = _first(adapter)

    def mutate_read(self, raw_descriptor, *, expected_owner_ref, expected_snapshot_digest):
        if field == "_expected_owner_ref":
            object.__setattr__(adapter, field, "changed-owner")
        elif field == "_observed_at":
            object.__setattr__(adapter, field, "2026-09-07T09:00:00Z")
        elif field == "_binding_fields":
            object.__setattr__(
                adapter,
                field,
                adapter._binding_fields[:-1] + ("sha256:" + "f" * 64,),
            )
        else:
            object.__setattr__(
                adapter,
                field,
                replace(adapter._snapshot, snapshot_digest="sha256:" + "f" * 64),
            )
        return _read(state["capture"], raw_descriptor, body)

    monkeypatch.setattr(PlanningMcpService, "read_owner_markdown_body", mutate_read)
    with pytest.raises(PlanningMarkdownSourceAdapterError) as rejected:
        adapter.extract(
            descriptor.evidence.logical_ref,
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )
    assert rejected.value.code == "stale_authority"


def test_g1_binding_revalidates_context_without_hostile_behavior(monkeypatch) -> None:
    adapter, _state, _calls = _adapter(monkeypatch, {})
    hostile = _G1BehaviorBomb()
    object.__setattr__(adapter, "_observed_at", hostile)
    with pytest.raises(PlanningMarkdownSourceAdapterError) as rejected:
        _ = adapter.binding
    assert rejected.value.code == "stale_authority"
    assert hostile.calls == 0


def test_g1_nested_descriptor_subclass_rejected_before_items_or_serialization(
    monkeypatch,
) -> None:
    descriptor = _descriptor("body")
    capture = _capture(descriptor)
    counter = [0]
    capture["descriptors"][0] = _G1CountingDescriptor(descriptor, counter)

    def raw_capture(self, expected_owner_ref):
        return capture

    monkeypatch.setattr(
        PlanningMcpService, "capture_owner_markdown_snapshot", raw_capture
    )
    with pytest.raises(PlanningMarkdownSourceAdapterError) as rejected:
        PlanningMarkdownSourceAdapter(
            service=object.__new__(PlanningMcpService),
            owner_scope=OWNER_SCOPE,
            expected_owner_ref=OWNER_REF,
            observed_at=OBSERVED_AT,
        )
    assert rejected.value.code == "invalid_authority"
    assert counter[0] == 0


@pytest.mark.parametrize("target", ("source", "version", "chunk"))
def test_g1_shared_records_validate_primitives_before_supplied_serializers(
    monkeypatch, target
) -> None:
    adapter, _state, _calls = _adapter(monkeypatch, {"docs/plans/body.md": "body"})
    binding, descriptor = _first(adapter)
    occurrence = adapter.extract(
        descriptor.evidence.logical_ref,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    hostile = _G1BehaviorBomb()
    if target == "source":
        object.__setattr__(descriptor.source, "canonical_ref", hostile)
        call = lambda: replace(descriptor, source=descriptor.source)
    elif target == "version":
        object.__setattr__(descriptor.source_version, "revision_ref", hostile)
        call = lambda: replace(descriptor, source_version=descriptor.source_version)
    else:
        object.__setattr__(occurrence.chunks[0], "content", hostile)
        call = lambda: replace(occurrence, chunks=(occurrence.chunks[0],))
    with pytest.raises(PlanningMarkdownSourceAdapterError):
        call()
    assert hostile.calls == 0


def test_g1_forged_service_error_code_rejected_before_equality(monkeypatch) -> None:
    adapter, _state, _calls = _adapter(monkeypatch, {"docs/plans/body.md": "body"})
    binding, descriptor = _first(adapter)
    hostile = _G1BehaviorBomb()
    forged = PlanningServiceError("owner_markdown_stale", "fixed")
    object.__setattr__(forged, "code", hostile)

    def fail_read(self, raw_descriptor, *, expected_owner_ref, expected_snapshot_digest):
        raise forged

    monkeypatch.setattr(PlanningMcpService, "read_owner_markdown_body", fail_read)
    with pytest.raises(PlanningMarkdownSourceAdapterError) as rejected:
        adapter.extract(
            descriptor.evidence.logical_ref,
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )
    assert rejected.value.code == "planning_markdown_source_adapter_failed"
    assert hostile.calls == 0


def test_g1_evidence_reconstructs_authority_binding_digest(monkeypatch) -> None:
    adapter, _state, _calls = _adapter(monkeypatch, {"docs/plans/body.md": "body"})
    _binding, descriptor = _first(adapter)
    forged_digest = "sha256:" + "f" * 64
    with pytest.raises(PlanningMarkdownSourceAdapterError, match="invalid_snapshot"):
        forged_evidence = replace(
            descriptor.evidence, binding_digest=forged_digest
        )
        forged_locator = replace(
            descriptor.body_locator,
            binding_digest=forged_digest,
            locator_digest="",
        )
        replace(
            descriptor,
            evidence=forged_evidence,
            body_locator=forged_locator,
        )


def test_g1_evidence_reconstructs_service_descriptor_version_digest(monkeypatch) -> None:
    from src.unified_source_index_contract import SourceVersionRecord

    adapter, _state, _calls = _adapter(monkeypatch, {"docs/plans/body.md": "body"})
    _binding, descriptor = _first(adapter)
    evidence = descriptor.evidence
    forged_descriptor_digest = "sha256:" + "f" * 64
    forged_revision_digest = _digest(
        {
            "schema": "odysseus.usi.planning_markdown_source_revision.v1",
            "approved_revision": evidence.approved_revision,
            "domain_content_hash": evidence.domain_content_hash,
            "canonical_projection_hash": evidence.canonical_projection_hash,
            "planning_source_ref": evidence.planning_source_ref,
            "planning_snapshot_digest": evidence.planning_snapshot_digest,
            "selection_digest": evidence.selection_digest,
            "source_ref": evidence.source_ref,
            "descriptor_version_digest": forged_descriptor_digest,
            "markdown_snapshot_digest": evidence.markdown_snapshot_digest,
            "body_sha256": evidence.body_sha256,
            "byte_count": evidence.byte_count,
        }
    )
    revision_ref = (
        f"approved-markdown:{evidence.approved_revision}:{forged_revision_digest}"
    )
    forged_version = SourceVersionRecord.create(
        descriptor.source,
        revision_ref=revision_ref,
        content_hash=evidence.body_sha256,
        version_observed_at=OBSERVED_AT,
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.INLINE_LOCAL,
    )
    with pytest.raises(PlanningMarkdownSourceAdapterError, match="invalid_snapshot"):
        forged_evidence = replace(
            evidence,
            descriptor_version_digest=forged_descriptor_digest,
            revision_digest=forged_revision_digest,
            source_version_id=forged_version.source_version_id,
        )
        forged_locator = replace(
            descriptor.body_locator,
            source_version_id=forged_version.source_version_id,
            descriptor_version_digest=forged_descriptor_digest,
            locator_digest="",
        )
        replace(
            descriptor,
            source_version=forged_version,
            evidence=forged_evidence,
            body_locator=forged_locator,
        )


def test_g1_discovery_page_rejects_unbounded_next_cursor(monkeypatch) -> None:
    adapter, _state, _calls = _adapter(monkeypatch, {"docs/plans/body.md": "body"})
    binding = adapter.binding
    page = adapter.discover(
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    with pytest.raises(PlanningMarkdownSourceAdapterError, match="invalid_snapshot"):
        replace(page, next_cursor=10**100)


def test_g2_body_callback_cannot_replace_retained_context_coherently(
    monkeypatch,
) -> None:
    body = "body"
    adapter, state, _calls = _adapter(monkeypatch, {"docs/plans/body.md": body})
    binding, descriptor = _first(adapter)

    def mutate_read(self, raw_descriptor, *, expected_owner_ref, expected_snapshot_digest):
        result = _read(state["capture"], raw_descriptor, body)
        object.__setattr__(adapter, "_expected_owner_ref", "changed-owner")
        object.__setattr__(
            adapter,
            "_context",
            replace(adapter._context, owner_ref="changed-owner"),
        )
        return result

    monkeypatch.setattr(PlanningMcpService, "read_owner_markdown_body", mutate_read)
    with pytest.raises(PlanningMarkdownSourceAdapterError) as rejected:
        adapter.extract(
            descriptor.evidence.logical_ref,
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )
    assert rejected.value.code == "stale_authority"


def test_g2_body_callback_cannot_mutate_context_in_place(
    monkeypatch,
) -> None:
    body = "body"
    adapter, state, _calls = _adapter(monkeypatch, {"docs/plans/body.md": body})
    binding, descriptor = _first(adapter)

    def mutate_read(self, raw_descriptor, *, expected_owner_ref, expected_snapshot_digest):
        result = _read(state["capture"], raw_descriptor, body)
        object.__setattr__(adapter, "_expected_owner_ref", "changed-owner")
        object.__setattr__(adapter._context, "owner_ref", "changed-owner")
        return result

    monkeypatch.setattr(PlanningMcpService, "read_owner_markdown_body", mutate_read)
    with pytest.raises(PlanningMarkdownSourceAdapterError) as rejected:
        adapter.extract(
            descriptor.evidence.logical_ref,
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )
    assert rejected.value.code == "stale_authority"


def test_g2_capture_callback_cannot_replace_observed_context_coherently(
    monkeypatch,
) -> None:
    adapter, state, _calls = _adapter(monkeypatch, {"docs/plans/body.md": "body"})
    binding = adapter.binding
    changed = "2026-09-07T09:00:00Z"

    def mutate_capture(self, expected_owner_ref):
        object.__setattr__(adapter, "_observed_at", changed)
        object.__setattr__(adapter, "_context", replace(adapter._context, observed_at=changed))
        return deepcopy(state["capture"])

    monkeypatch.setattr(PlanningMcpService, "capture_owner_markdown_snapshot", mutate_capture)
    with pytest.raises(PlanningMarkdownSourceAdapterError) as rejected:
        adapter.discover(
            expected_binding_digest=binding.binding_digest,
            expected_snapshot_digest=binding.snapshot_digest,
        )
    assert rejected.value.code == "stale_authority"


def test_g2_binding_rejects_preoperation_owner_context_mismatch_with_authority(
    monkeypatch,
) -> None:
    adapter, _state, _calls = _adapter(monkeypatch, {})
    object.__setattr__(adapter, "_expected_owner_ref", "changed-owner")
    object.__setattr__(
        adapter,
        "_context",
        replace(adapter._context, owner_ref="changed-owner"),
    )
    with pytest.raises(PlanningMarkdownSourceAdapterError) as rejected:
        _ = adapter.binding
    assert rejected.value.code == "stale_authority"


def test_g2_same_value_context_replacement_during_callback_is_valid(
    monkeypatch,
) -> None:
    adapter, state, _calls = _adapter(monkeypatch, {"docs/plans/body.md": "body"})
    binding = adapter.binding

    def replace_capture(self, expected_owner_ref):
        object.__setattr__(adapter, "_context", replace(adapter._context))
        return deepcopy(state["capture"])

    monkeypatch.setattr(PlanningMcpService, "capture_owner_markdown_snapshot", replace_capture)
    page = adapter.discover(
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    assert page.items[0].evidence.owner_ref == OWNER_REF
    assert page.items[0].source_version.version_observed_at == OBSERVED_AT


def test_g2_exact_read_dto_rejects_nul_content_even_when_hash_matches(
    monkeypatch,
) -> None:
    adapter, _state, _calls = _adapter(monkeypatch, {"docs/plans/body.md": "a"})
    binding, descriptor = _first(adapter)
    request = PlanningMarkdownCharacterRangeRequest(
        descriptor.body_locator, TextRangeLocator(0, 1)
    )
    result = adapter.read_exact(
        request,
        expected_binding_digest=binding.binding_digest,
        expected_snapshot_digest=binding.snapshot_digest,
    )
    with pytest.raises(PlanningMarkdownSourceAdapterError) as rejected:
        replace(result, content="\x00", content_hash=content_hash("\x00"))
    assert rejected.value.code == "invalid_snapshot"


def test_g2_occurrence_dto_rejects_coherent_nul_body(
    monkeypatch,
) -> None:
    from src.rag_text_chunking import EXACT_CHARACTER_SPLITTER_VERSION
    from src.unified_source_index_contract import (
        ChunkRecord,
        Classification,
        ContentPolicy,
        SourceVersionRecord,
    )
    import src.unified_source_index_sources.planning_markdown as planning_markdown

    adapter, _state, _calls = _adapter(monkeypatch, {"docs/plans/body.md": "a"})
    _binding, descriptor = _first(adapter)
    evidence = descriptor.evidence
    nul_hash = content_hash("\x00")
    item = planning_markdown._CapturedDescriptor(
        evidence.owner_ref,
        evidence.project_id,
        evidence.roadmap_id,
        evidence.approved_revision,
        evidence.domain_content_hash,
        evidence.canonical_projection_hash,
        evidence.planning_source_ref,
        evidence.planning_snapshot_digest,
        evidence.selection_digest,
        evidence.source_ref,
        nul_hash,
        1,
        "sha256:" + "0" * 64,
        evidence.logical_ref,
    )
    item = replace(
        item,
        version_digest=planning_markdown._service_descriptor_digest(item),
    )
    revision_digest = planning_markdown._revision_digest(
        item, evidence.markdown_snapshot_digest
    )
    version = SourceVersionRecord.create(
        descriptor.source,
        revision_ref=f"approved-markdown:{evidence.approved_revision}:{revision_digest}",
        content_hash=nul_hash,
        version_observed_at=descriptor.source_version.version_observed_at,
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.INLINE_LOCAL,
    )
    forged_evidence = replace(
        evidence,
        body_sha256=nul_hash,
        byte_count=1,
        descriptor_version_digest=item.version_digest,
        revision_digest=revision_digest,
        source_version_id=version.source_version_id,
    )
    locator = replace(
        descriptor.body_locator,
        source_version_id=version.source_version_id,
        body_sha256=nul_hash,
        byte_count=1,
        descriptor_version_digest=item.version_digest,
        locator_digest="",
    )
    chunk = ChunkRecord.create(
        version,
        locator=TextRangeLocator(0, 1),
        extractor_profile_ref=planning_markdown.PLANNING_MARKDOWN_SOURCE_EXTRACTOR_PROFILE,
        content_hash=nul_hash,
        content="\x00",
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.INLINE_LOCAL,
    )
    with pytest.raises(PlanningMarkdownSourceAdapterError) as rejected:
        planning_markdown.PlanningMarkdownSourceOccurrence(
            descriptor.source,
            version,
            (chunk,),
            (),
            (),
            locator,
            forged_evidence,
            EXACT_CHARACTER_SPLITTER_VERSION,
        )
    assert rejected.value.code == "invalid_snapshot"
