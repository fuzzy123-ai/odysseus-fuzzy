from __future__ import annotations

from copy import deepcopy
import json

import pytest

from src.code_intelligence_contract import (
    CodeEdgeMapping,
    CodeFileMapping,
    CodeIntelligenceContractError,
    CodeLocation,
    CodeSymbolKind,
    CodeSymbolMapping,
    ExtractionEvidence,
    ExtractionMethod,
    mapping_from_dict,
    mapping_from_json,
    normalize_repo_relative_path,
    symbol_natural_key,
    validate_mapping_batch,
)
from src.unified_source_index_contract import (
    Classification,
    ContentPolicy,
    EntityKind,
    EntityRecord,
    RelationKind,
    RelationRecord,
    SourceKind,
    SourceRecord,
    SourceVersionRecord,
    canonical_json,
    content_hash,
)


NOW = "2026-07-18T10:00:00Z"
SIGNATURE_A = "sha256:" + "a" * 64
SIGNATURE_B = "sha256:" + "b" * 64


def _evidence(*, incomplete: bool = False) -> ExtractionEvidence:
    return ExtractionEvidence(
        method=ExtractionMethod.CBM_PARSER,
        confidence=0.95,
        extractor_name="cbm",
        extractor_version="0.9.0",
        incomplete_parse=incomplete,
    )


def _source_version(path: str, *, body: str = "same body", revision: str = "git:abc123"):
    source = SourceRecord(
        owner_scope="user:alice",
        source_kind=SourceKind.CODE,
        canonical_ref=f"repo:demo/{path}",
        classification=Classification.PRIVATE,
        content_policy=ContentPolicy.REFERENCE_ONLY,
        provider_ref="local-git",
    )
    version = SourceVersionRecord.create(
        source,
        revision_ref=revision,
        content_hash=content_hash(body),
        version_observed_at=NOW,
    )
    return source, version


def _file(
    path: str = "src/main.py",
    *,
    body: str = "same body",
    engine_project_ref: str = "project-1",
    engine_file_ref: str = "file-1",
    revision: str = "git:abc123",
):
    source, version = _source_version(path, body=body, revision=revision)
    mapping = CodeFileMapping.create(
        source,
        version,
        repo_id="demo",
        relative_path=path,
        byte_length=len(body.encode("utf-8")),
        engine_project_ref=engine_project_ref,
        engine_file_ref=engine_file_ref,
        evidence=_evidence(),
    )
    return source, version, mapping


def _symbol(
    file_mapping: CodeFileMapping,
    version: SourceVersionRecord,
    *,
    qualified_name: str = "Service.run",
    signature: str = SIGNATURE_A,
    start_line: int = 2,
    start_byte: int = 5,
    engine_symbol_ref: str = "symbol-1",
    kind: CodeSymbolKind = CodeSymbolKind.METHOD,
    evidence: ExtractionEvidence | None = None,
):
    extraction = evidence or _evidence()
    location = CodeLocation(
        file_mapping.relative_path,
        start_byte,
        start_byte + 20,
        start_line,
        0,
        start_line + 2,
        1,
    )
    entity = EntityRecord.create(
        version,
        entity_kind=EntityKind.SYMBOL,
        natural_key=symbol_natural_key(kind, qualified_name, signature),
        locator=location.to_usi_locator(),
        extractor_profile_ref=extraction.extractor_profile_ref,
        content_hash=content_hash(f"symbol-{qualified_name}-{start_line}"),
        label=qualified_name,
    )
    mapping = CodeSymbolMapping.create(
        file_mapping,
        entity,
        symbol_kind=kind,
        qualified_name=qualified_name,
        signature_fingerprint=signature,
        location=location,
        engine_symbol_ref=engine_symbol_ref,
        evidence=extraction,
    )
    return entity, mapping


def test_utf8_paths_are_nfc_normalized_and_ranges_are_half_open():
    decomposed = "src/Cafe\u0301.py"
    location = CodeLocation(decomposed, 2, 12, 3, 4, 5, 0)

    assert location.relative_path == "src/Caf\u00e9.py"
    assert normalize_repo_relative_path(decomposed) == location.relative_path
    assert location.to_usi_locator().to_dict() == {
        "kind": "code_range",
        "path": "src/Caf\u00e9.py",
        "start_line": 3,
        "start_column": 4,
        "end_line": 5,
        "end_column": 0,
    }
    assert location.encoding == "utf-8"


@pytest.mark.parametrize(
    "path",
    [
        "C:\\Users\\alice\\repo\\main.py",
        "/home/alice/repo/main.py",
        "~/repo/main.py",
        "../main.py",
        "src/../main.py",
        "src//main.py",
        "src\\main.py",
        "src/./main.py",
        "src/\x00main.py",
    ],
)
def test_absolute_traversal_or_noncanonical_paths_fail_closed(path: str):
    with pytest.raises(CodeIntelligenceContractError):
        CodeLocation(path, 0, 1, 1, 0, 1, 1)


@pytest.mark.parametrize(
    "values",
    [
        (0, 0, 1, 0, 1, 1),
        (2, 1, 1, 0, 1, 1),
        (0, 1, 2, 0, 1, 1),
        (0, 1, 1, 4, 1, 4),
    ],
)
def test_empty_or_reversed_byte_line_column_ranges_fail_closed(values):
    with pytest.raises(CodeIntelligenceContractError):
        CodeLocation("src/main.py", *values)


def test_file_mapping_binds_exact_usi_source_and_version():
    source, version, mapping = _file()

    assert mapping.source_id == source.source_id
    assert mapping.source_version_id == version.source_version_id
    assert mapping.content_hash == version.content_hash
    assert mapping.effective_file_ref == "file-1"
    assert mapping.fallback_key.startswith("cbm_file_")
    assert "C:\\" not in canonical_json(mapping.to_dict())


def test_file_fallback_is_stable_when_upstream_ids_change():
    source, version, first = _file(engine_project_ref="project-old", engine_file_ref="file-old")
    second = CodeFileMapping.create(
        source,
        version,
        repo_id="demo",
        relative_path="src/main.py",
        byte_length=9,
        engine_project_ref="project-new",
        engine_file_ref="file-new",
        evidence=_evidence(),
    )

    assert first.fallback_key == second.fallback_key
    assert first.engine_file_ref != second.engine_file_ref


def test_file_create_rejects_mismatched_repo_path_or_version_parent():
    source, version = _source_version("src/main.py")
    other_source, other_version = _source_version("src/other.py")

    with pytest.raises(CodeIntelligenceContractError, match="canonical_ref"):
        CodeFileMapping.create(
            source,
            version,
            repo_id="demo",
            relative_path="src/other.py",
            byte_length=9,
            engine_project_ref="project-1",
            evidence=_evidence(),
        )
    with pytest.raises(CodeIntelligenceContractError, match="belong"):
        CodeFileMapping.create(
            source,
            other_version,
            repo_id="demo",
            relative_path="src/main.py",
            byte_length=9,
            engine_project_ref="project-1",
            evidence=_evidence(),
        )
    assert other_source.source_id != source.source_id


def test_duplicate_symbol_names_at_different_locations_remain_distinct():
    _source, version, file_mapping = _file()
    first_entity, first = _symbol(file_mapping, version, start_line=2, start_byte=5)
    second_entity, second = _symbol(
        file_mapping,
        version,
        start_line=20,
        start_byte=80,
        engine_symbol_ref="symbol-2",
    )

    files, symbols, edges = validate_mapping_batch(
        [file_mapping], [second, first], []
    )
    assert files == (file_mapping,)
    assert not edges
    assert first.qualified_name == second.qualified_name
    assert first_entity.entity_id != second_entity.entity_id
    assert first.fallback_key != second.fallback_key
    assert {item.entity_id for item in symbols} == {
        first_entity.entity_id,
        second_entity.entity_id,
    }


def test_overloads_with_same_name_use_signature_and_locator_identity():
    _source, version, file_mapping = _file()
    first_entity, first = _symbol(file_mapping, version, signature=SIGNATURE_A)
    second_entity, second = _symbol(
        file_mapping,
        version,
        signature=SIGNATURE_B,
        start_line=8,
        start_byte=40,
        engine_symbol_ref="symbol-overload",
    )

    assert first.qualified_name == second.qualified_name
    assert first.signature_fingerprint != second.signature_fingerprint
    assert first_entity.natural_key != second_entity.natural_key
    assert first.fallback_key != second.fallback_key


def test_identical_files_and_moved_paths_never_collapse_source_identity():
    _source_a, _version_a, original = _file(
        "src/a.py", engine_file_ref="file-a"
    )
    _source_b, _version_b, identical = _file(
        "src/b.py", engine_file_ref="file-b"
    )
    _source_c, _version_c, moved = _file(
        "lib/a.py", engine_file_ref="file-moved", revision="git:def456"
    )

    files, _symbols, _edges = validate_mapping_batch(
        [moved, identical, original], [], []
    )
    assert len({item.content_hash for item in files}) == 1
    assert len({item.source_id for item in files}) == 3
    assert len({item.fallback_key for item in files}) == 3


def test_symbol_mapping_requires_exact_entity_ancestry_locator_and_natural_key():
    _source, version, file_mapping = _file()
    entity, mapping = _symbol(file_mapping, version)
    wrong_location = CodeLocation("src/main.py", 40, 50, 8, 0, 9, 0)

    with pytest.raises(CodeIntelligenceContractError, match="locator"):
        CodeSymbolMapping.create(
            file_mapping,
            entity,
            symbol_kind=CodeSymbolKind.METHOD,
            qualified_name="Service.run",
            signature_fingerprint=SIGNATURE_A,
            location=wrong_location,
            evidence=_evidence(),
        )
    with pytest.raises(CodeIntelligenceContractError, match="natural_key"):
        CodeSymbolMapping.create(
            file_mapping,
            entity,
            symbol_kind=CodeSymbolKind.METHOD,
            qualified_name="Service.stop",
            signature_fingerprint=SIGNATURE_A,
            location=mapping.location,
            evidence=_evidence(),
        )


def test_symbol_fallback_stays_stable_when_engine_refs_change():
    _source, version, file_mapping = _file()
    entity, first = _symbol(file_mapping, version, engine_symbol_ref="old-id")
    changed_file = deepcopy(file_mapping.to_dict())
    changed_file["engine_project_ref"] = "project-new"
    changed_file["engine_file_ref"] = "file-new"
    changed_mapping = CodeFileMapping.from_dict(changed_file)
    second = CodeSymbolMapping.create(
        changed_mapping,
        entity,
        symbol_kind=CodeSymbolKind.METHOD,
        qualified_name="Service.run",
        signature_fingerprint=SIGNATURE_A,
        location=first.location,
        engine_symbol_ref="new-id",
        evidence=_evidence(),
    )

    assert first.fallback_key == second.fallback_key


def test_edge_maps_exact_usi_relation_and_has_stable_fallback():
    _source, version, file_mapping = _file()
    first_entity, first_symbol = _symbol(file_mapping, version)
    second_entity, second_symbol = _symbol(
        file_mapping,
        version,
        qualified_name="Store.save",
        start_line=10,
        start_byte=50,
        engine_symbol_ref="symbol-2",
    )
    extraction = _evidence(incomplete=True)
    relation = RelationRecord.create(
        first_entity.ref(),
        second_entity.ref(),
        relation_kind=RelationKind.CALLS,
        method_ref=extraction.extractor_profile_ref,
        confidence=0.95,
        evidence_refs=(first_entity.evidence_ref(), second_entity.evidence_ref()),
    )
    first = CodeEdgeMapping.create(
        relation,
        engine_project_ref="project-1",
        engine_edge_ref="edge-old",
        evidence=extraction,
    )
    second = CodeEdgeMapping.create(
        relation,
        engine_project_ref="project-2",
        engine_edge_ref="edge-new",
        evidence=extraction,
    )

    files, symbols, edges = validate_mapping_batch(
        [file_mapping], [first_symbol, second_symbol], [first]
    )
    assert files and symbols and edges == (first,)
    assert first.relation_id == relation.relation_id
    assert first.source_entity_id == first_entity.entity_id
    assert first.target_entity_id == second_entity.entity_id
    assert first.fallback_key == second.fallback_key
    assert first.evidence.incomplete_parse is True
    tampered = first.to_dict()
    tampered["relation_id"] = "usi_relation_" + "c" * 64
    with pytest.raises(CodeIntelligenceContractError, match="relation_id"):
        mapping_from_dict(tampered)


def test_mapping_round_trips_are_strict_deterministic_and_content_free():
    _source, version, file_mapping = _file()
    _entity, symbol = _symbol(file_mapping, version)

    for record in (file_mapping, symbol):
        rendered = canonical_json(record.to_dict())
        assert mapping_from_json(rendered) == record
        assert mapping_from_dict(json.loads(rendered)) == record
        assert mapping_from_json(rendered).to_dict() == record.to_dict()
        assert "absolute_path" not in rendered
        assert "host_path" not in rendered
        assert "source_text" not in rendered


def test_duplicate_unknown_missing_or_tampered_serialization_fails_closed():
    _source, _version, file_mapping = _file()
    rendered = canonical_json(file_mapping.to_dict())
    duplicate = rendered.replace("{", '{"schema":"duplicate",', 1)
    with pytest.raises(CodeIntelligenceContractError):
        mapping_from_json(duplicate)

    unknown = file_mapping.to_dict()
    unknown["raw_path"] = "hidden"
    with pytest.raises(CodeIntelligenceContractError, match="unknown"):
        mapping_from_dict(unknown)

    missing = file_mapping.to_dict()
    del missing["source_id"]
    with pytest.raises(CodeIntelligenceContractError, match="missing"):
        mapping_from_dict(missing)

    tampered = file_mapping.to_dict()
    tampered["fallback_key"] = "cbm_file_" + "f" * 64
    with pytest.raises(CodeIntelligenceContractError, match="canonical"):
        mapping_from_dict(tampered)


def test_tampered_usi_source_version_and_entity_identity_fail_closed():
    _source, version, file_mapping = _file()
    _entity, symbol = _symbol(file_mapping, version)

    wrong_source = file_mapping.to_dict()
    wrong_source["source_id"] = "usi_source_" + "f" * 64
    with pytest.raises(CodeIntelligenceContractError, match="source_id"):
        mapping_from_dict(wrong_source)

    wrong_version = file_mapping.to_dict()
    wrong_version["source_version_id"] = "usi_version_" + "e" * 64
    with pytest.raises(CodeIntelligenceContractError, match="source_version_id"):
        mapping_from_dict(wrong_version)

    wrong_entity = symbol.to_dict()
    wrong_entity["entity_id"] = "usi_entity_" + "d" * 64
    with pytest.raises(CodeIntelligenceContractError, match="entity_id"):
        mapping_from_dict(wrong_entity)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("engine_project_ref", "C:\\repo"),
        ("engine_file_ref", "/home/alice/repo/main.py"),
        ("engine_file_ref", "src/main.py"),
    ],
)
def test_upstream_refs_cannot_smuggle_host_or_source_paths(field: str, value: str):
    source, version = _source_version("src/main.py")
    arguments = {
        "repo_id": "demo",
        "relative_path": "src/main.py",
        "byte_length": 9,
        "engine_project_ref": "project-1",
        "engine_file_ref": "file-1",
        "evidence": _evidence(),
    }
    arguments[field] = value

    with pytest.raises(CodeIntelligenceContractError):
        CodeFileMapping.create(source, version, **arguments)


def test_invalid_extraction_evidence_fails_closed():
    with pytest.raises(CodeIntelligenceContractError):
        ExtractionEvidence("unknown", 0.5, "cbm", "0.9.0", False)
    with pytest.raises(CodeIntelligenceContractError):
        ExtractionEvidence(ExtractionMethod.CBM_PARSER, float("nan"), "cbm", "0.9.0", False)
    with pytest.raises(CodeIntelligenceContractError):
        ExtractionEvidence(ExtractionMethod.CBM_PARSER, 0.5, "cbm", "0.9.0", 1)


def test_batch_rejects_ambiguous_engine_file_refs_and_repo_paths():
    _source_a, _version_a, first = _file(
        "src/a.py", engine_file_ref="same-file-id"
    )
    _source_b, _version_b, second = _file(
        "src/b.py", engine_file_ref="same-file-id"
    )
    with pytest.raises(CodeIntelligenceContractError, match="engine file"):
        validate_mapping_batch([first, second], [], [])

    with pytest.raises(CodeIntelligenceContractError, match="source_id"):
        CodeFileMapping(
            repo_id=first.repo_id,
            engine_project_ref="project-2",
            engine_file_ref="other-file-id",
            owner_scope=second.owner_scope,
            source_id=second.source_id,
            source_version_id=second.source_version_id,
            relative_path=first.relative_path,
            revision_ref=second.revision_ref,
            content_hash=second.content_hash,
            byte_length=second.byte_length,
            evidence=second.evidence,
        )


def test_batch_rejects_symbols_without_file_ancestry_and_edges_without_endpoints():
    _source, version, file_mapping = _file()
    first_entity, first_symbol = _symbol(file_mapping, version)
    second_entity, second_symbol = _symbol(
        file_mapping,
        version,
        qualified_name="Store.save",
        start_line=10,
        start_byte=50,
        engine_symbol_ref="symbol-2",
    )
    extraction = _evidence()
    relation = RelationRecord.create(
        first_entity.ref(),
        second_entity.ref(),
        relation_kind=RelationKind.CALLS,
        method_ref=extraction.extractor_profile_ref,
        confidence=0.95,
        evidence_refs=(first_entity.evidence_ref(), second_entity.evidence_ref()),
    )
    edge = CodeEdgeMapping.create(
        relation, engine_project_ref="project-1", evidence=extraction
    )

    with pytest.raises(CodeIntelligenceContractError, match="unknown file"):
        validate_mapping_batch([], [first_symbol], [])
    with pytest.raises(CodeIntelligenceContractError, match="endpoint"):
        validate_mapping_batch([file_mapping], [first_symbol], [edge])
    assert second_symbol.entity_id == second_entity.entity_id
