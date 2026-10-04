from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

from src.harbour_authority import AuthenticatedPrincipalIssuer
from src.planning_definition_contract import compute_roadmap_content_hash
from src.planning_roadmap_migration import (
    AUTHORITY_PINS,
    DOCS_ONLY_AUTHORITY_MAP_ID,
    DOCS_ONLY_AUTHORITY_PINS,
    REPO_ONLY_AUTHORITY_MAP_ID,
    REPO_ONLY_AUTHORITY_PINS,
    HISTORICAL_AUTHORITY_MAP_ID,
    HISTORICAL_AUTHORITY_PINS,
    H1R04_ACCEPTED_DOCUMENT_PATH,
    H1R04_ACCEPTED_DOCUMENT_RAW_BLOB,
    H1R05_ACCEPTANCE_RECEIPT_PATH,
    H1R05_ACCEPTANCE_RECEIPT_RAW_BLOB,
    MIGRATION_MANIFEST_SCHEMA_V1,
    MIGRATION_MANIFEST_SCHEMA_V2,
    SUCCESSOR_AUTHORITY_MAP_ID,
    SUCCESSOR_AUTHORITY_PINS,
    FROZEN_MEMBERS,
    FROZEN_MEMBER_SHA256_VARIANTS,
    accept_h1r03_migration,
    build_h1r03_migration_manifest,
    map_legacy_roadmap,
    build_h1r03_structural_census,
    canonical_json_bytes,
    authority_map_relation,
    current_authority_map,
    normalize_legacy_timestamp,
    resolve_migration_manifest_identity,
    strict_json_object,
    _authority_blob,
    _git_blob,
    _raw_git_blob,
)
from src.planning_revision_store import (
    DurablePlanningRevisionStore,
    DurablePlanningStoreError,
    PlanningRevisionStore,
)


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
AUTH_CONTEXT = "sha256:" + hashlib.sha256(b"h1r03-tests").hexdigest()
LOCKED = {
    "abc-roadmap-continuation-runtime": ("sha256:0db5d8366b33797230dece409cce1a76c6e6301e3273970d4618bff3b055ac15", "sha256:0d89d920b7fae57e8bf3342f008137bbb6147d2ee95b2a6d6b000a850129e103"),
    "abc-roadmap-reconciliation-import": ("sha256:7233bc3c95d5b53a01d5143ae5db92d6ac8899de684f2e15801ab2eec79b55f2", "sha256:147eae56ffaf6c661f210c141daaace7ef99ada30cee07814bc5f61d4364a1c1"),
    "odysseus-multiagent-roadmap": ("sha256:2ee78369a0af2f51350045f62354246319edd54d3d58d22046cd9ae33b3629c5", "sha256:1e222b9f1cb2474f540624ad9377f60335a7b81deee7757017bcaaf7ddf303b6"),
    "orca-memory-graph-migration": ("sha256:c81c886668345a9326aff56c0d632deb37e07d236bb5911b64d70b1e85a837eb", "sha256:378c1ff854e11a449c86739c91cca726e4aa65b40fcd94cdaaa96a5ac1162f24"),
    "orca-memory-live-activation": ("sha256:7a158cac8ef2724c8d29d611c4aef9ce382ac53cf654852c931a3e809597877f", "sha256:c0eb597706647e89a44012ce549ac1836af5c5a400b03d859c1de6d34c156e0f"),
    "private-data-memory-ingestion": ("sha256:91feed1c7d22c54ff4c72b90c10fe1b9aa6d6ee5f5e1be61f4b427a3880f6a5e", "sha256:72b724dd775e062f86baab5d1acf5e44cdf576803c230966f0c2bbeb7ac0a9c6"),
}


def _authority(tmp_path: Path):
    issuer = AuthenticatedPrincipalIssuer(
        issuer_id="odysseus-auth", key_id="h1r03-key",
        secret=b"h1r03-test-secret-material-0000000001", clock=lambda: NOW,
    )
    principal = issuer.issue(
        subject_id="migration-operator", owner_id="fuzzy",
        auth_context_digest=AUTH_CONTEXT, ttl_seconds=3600, nonce="h1r03-nonce",
    )
    store = DurablePlanningRevisionStore(
        tmp_path / "store", trusted_issuers=(issuer,), clock=lambda: NOW
    )
    return store, issuer, principal


def _accept(store, principal, manifest, *, repo_root: Path = ROOT):
    return store.accept_migration(
        principal,
        batch_id=manifest["batch_id"],
        current_manifest=lambda: build_h1r03_migration_manifest(repo_root),
    )


def test_six_locked_validator_outputs_and_source_bytes_are_stable() -> None:
    before = {path: (ROOT / path).read_bytes() for path, *_rest in FROZEN_MEMBERS}
    manifest = build_h1r03_migration_manifest(ROOT)
    assert len(manifest["source_items"]) == 6
    assert {
        item["roadmap_id"]: (item["content_hash"], item["payload_sha256"])
        for item in manifest["source_items"]
    } == LOCKED
    assert {path: (ROOT / path).read_bytes() for path in before} == before


def _migration_fixture_root(tmp_path: Path) -> Path:
    fixture_root = tmp_path / "repo"
    paths = set(AUTHORITY_PINS) | set(DOCS_ONLY_AUTHORITY_PINS) | set(REPO_ONLY_AUTHORITY_PINS) | {path for path, *_rest in FROZEN_MEMBERS}
    paths.add("specs/roadmaps/roadmap-reconciliation-report-reduction.v1.json")
    for relative in paths:
        target = fixture_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    return fixture_root


def test_current_reviewed_roadmap_pin_and_manifest_semantics_are_bound() -> None:
    assert AUTHORITY_PINS["docs/plans/harbour-one-product-roadmap.json"] == (
        "e4faf54dcc3a0c971e3f4c8152bbf61b99a35bbf"
    )
    manifest = build_h1r03_migration_manifest(ROOT)
    assert manifest["schema"] == MIGRATION_MANIFEST_SCHEMA_V2
    assert manifest["authority_map_id"] == REPO_ONLY_AUTHORITY_MAP_ID
    assert manifest["authority_pins"] == REPO_ONLY_AUTHORITY_PINS
    assert resolve_migration_manifest_identity({
        "schema": MIGRATION_MANIFEST_SCHEMA_V1,
        "owner_id": "fuzzy", "project_id": "harbour-one", "source_items": [],
        "supporting_relations": [], "normalized_payloads": {},
        "authority_pins": HISTORICAL_AUTHORITY_PINS, "structural_census": [],
    })[0] == HISTORICAL_AUTHORITY_MAP_ID
    assert manifest["owner_id"] == "fuzzy"
    assert manifest["project_id"] == "harbour-one"
    assert [(item["roadmap_id"], item["role"], item["adapter"]) for item in manifest["source_items"]] == [
        (roadmap_id, "primary", adapter) for _path, adapter, roadmap_id, _blob, _sha in FROZEN_MEMBERS
    ]
    assert manifest["supporting_relations"] == [{
        "path": "specs/roadmaps/roadmap-reconciliation-report-reduction.v1.json",
        "role": "supporting_relation_only",
        "target_roadmap_id": "abc-roadmap-reconciliation-import",
        "sha256": "ddf2bc2a5a04017f0616d7bb0857431a70687489de0a9ed0b00a8d393f7954f3",
    }]
    assert manifest["structural_census"]
    assert set(manifest["structural_census"][0]) == {
        "source_id", "path", "kind", "extension", "structural_role",
    }
    assert manifest["supporting_observation"]["count"] == sum(
        row["structural_role"] in {"supporting_plan_source", "control_plane_authority"}
        for row in manifest["structural_census"]
    )


def test_docs_only_manifest_binds_exact_final_roadmap_git_blob_and_progress_tuple() -> None:
    manifest = build_h1r03_migration_manifest(ROOT)
    raw = (ROOT / "docs/plans/harbour-one-product-roadmap.json").read_bytes()
    product = json.loads(raw)
    expected_blob = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
    assert expected_blob == REPO_ONLY_AUTHORITY_PINS["docs/plans/harbour-one-product-roadmap.json"]
    assert manifest["authority_pins"]["docs/plans/harbour-one-product-roadmap.json"] == expected_blob
    assert product["updated_at"] == "2026-10-03"
    assert (product["progress"]["completed"], product["progress"]["total"], product["progress"]["percent"]) == (9, 17, 53)
    assert {"H1R-03", "H1R-04"} <= set(product["progress"]["accepted_slice_ids"])
    assert next(item["status"] for item in product["slices"] if item["id"] == "H1R-03") == "accepted_repo_only_2026-10-02"
    assert next(item["status"] for item in product["slices"] if item["id"] == "H1R-04") == "accepted_docs_only_2026-10-02"
    assert next(item["status"] for item in product["slices"] if item["id"] == "H1R-05") == "accepted_repo_only_2026-10-03"


def test_mcp_first_profile_scopes_interface_without_changing_product_inventory() -> None:
    product = json.loads((ROOT / "docs/plans/harbour-one-product-roadmap.json").read_bytes())
    profile = product["user_authorized_execution_policy"]["first_completion_milestone"]
    assert profile["id"] == "mcp-first-2026-10-02"
    assert profile["status"] == "in_progress"
    assert profile["delivery_interface"] == "mcp"
    assert profile["ui_required"] is False
    assert profile["full_product_inventory_preserved"] is True
    assert "existing persistence,owner/auth/disclosure,execution,timeout,restart,rollback" in profile["required_backend_scope"]
    assert any("authenticated MCP protocol end-to-end" in item for item in profile["completion_requires"])
    assert "temporal/containment" in profile["effective_verification_overrides"]["H1R-15"]
    assert "actual authenticated MCP golden backend flow" in profile["effective_verification_overrides"]["H1R-16"]
    assert "no completion claim and no denominator change" in profile["effective_verification_overrides"]["H1R-14"]
    assert product["progress"]["completed"] == 9
    assert product["progress"]["total"] == 17
    assert product["progress"]["percent"] == 53
    assert len(product["slices"]) == 17
    assert {item["id"]: item["status"] for item in product["slices"]}["H1R-05"] == "accepted_repo_only_2026-10-03"
    assert {item["id"]: item["status"] for item in product["slices"]}["H1R-14"] == "planned_needs_design"
    assert {item["id"]: item["status"] for item in product["slices"]}["H1R-15"] == "planned"
    assert {item["id"]: item["status"] for item in product["slices"]}["H1R-16"] == "planned"


def test_frozen_crlf_sources_keep_historical_git_pin_normalization() -> None:
    crlf_members = {
        "specs/roadmaps/odysseus-multiagent-roadmap.v1.json",
        "specs/roadmaps/orca-memory-live-activation-roadmap.v1.json",
        "specs/roadmaps/private-data-memory-ingestion-roadmap.v1.json",
    }
    for path in crlf_members:
        _adapter, _roadmap_id, expected_blob, _sha = next(
            (adapter, roadmap_id, blob, sha)
            for member_path, adapter, roadmap_id, blob, sha in FROZEN_MEMBERS
            if member_path == path
        )
        raw = (ROOT / path).read_bytes()
        assert hashlib.sha256(raw).hexdigest() in FROZEN_MEMBER_SHA256_VARIANTS[path]
        assert _git_blob(raw) == expected_blob


def test_frozen_lf_checkout_variants_preserve_historical_manifest_identity(tmp_path: Path) -> None:
    fixture_root = _migration_fixture_root(tmp_path)
    expected = build_h1r03_migration_manifest(fixture_root)
    for relative in (
        "specs/roadmaps/odysseus-multiagent-roadmap.v1.json",
        "specs/roadmaps/orca-memory-live-activation-roadmap.v1.json",
        "specs/roadmaps/private-data-memory-ingestion-roadmap.v1.json",
    ):
        source = fixture_root / relative
        raw = source.read_bytes()
        portable_lf = raw.replace(b"\r\n", b"\n")
        source.write_bytes(portable_lf)
    actual = build_h1r03_migration_manifest(fixture_root)
    assert actual["manifest_digest"] == expected["manifest_digest"]
    assert actual["batch_id"] == expected["batch_id"]
    assert actual["source_items"] == expected["source_items"]
    assert actual["normalized_payloads"] == expected["normalized_payloads"]


def test_frozen_mixed_newline_form_is_not_an_allowed_portability_variant(tmp_path: Path) -> None:
    fixture_root = _migration_fixture_root(tmp_path)
    relative = "specs/roadmaps/odysseus-multiagent-roadmap.v1.json"
    source = fixture_root / relative
    portable_lf = source.read_bytes().replace(b"\r\n", b"\n")
    unsupported_mixed = portable_lf.replace(b"\n", b"\r\n", 1)
    assert unsupported_mixed not in {source.read_bytes(), portable_lf}
    source.write_bytes(unsupported_mixed)
    with pytest.raises(DurablePlanningStoreError) as stale:
        build_h1r03_migration_manifest(fixture_root)
    assert stale.value.code == "stale_authority"


def test_successor_v2_product_pin_uses_raw_bytes_without_lf_normalization_fallback() -> None:
    path = "docs/plans/harbour-one-product-roadmap.json"
    expected_blob = SUCCESSOR_AUTHORITY_PINS[path]
    raw = subprocess.run(
        ["git", "cat-file", "blob", expected_blob],
        cwd=ROOT, check=True, capture_output=True,
    ).stdout
    assert _raw_git_blob(raw) == expected_blob
    crlf_variant = raw.replace(b"\n", b"\r\n")
    assert _raw_git_blob(raw) == expected_blob
    assert _git_blob(crlf_variant) == expected_blob
    assert _authority_blob(path, raw, SUCCESSOR_AUTHORITY_MAP_ID) == expected_blob
    assert _authority_blob(path, crlf_variant, SUCCESSOR_AUTHORITY_MAP_ID) != expected_blob


def test_docs_only_identity_freezes_v2_digest_and_closed_forward_relations() -> None:
    from src.planning_revision_store import _migration_digest, _migration_manifest_identity

    manifest = build_h1r03_migration_manifest(ROOT)
    v2 = deepcopy(manifest)
    v2["authority_map_id"] = SUCCESSOR_AUTHORITY_MAP_ID
    v2["authority_pins"] = deepcopy(SUCCESSOR_AUTHORITY_PINS)
    assert _migration_digest(_migration_manifest_identity(v2)) == (
        "sha256:39e847b0350b7efcac5e38b3212fd3ffa8a4bab65ff26a36b3249cb9879f7068"
    )
    assert authority_map_relation(HISTORICAL_AUTHORITY_MAP_ID, SUCCESSOR_AUTHORITY_MAP_ID) == "successor"
    assert authority_map_relation(HISTORICAL_AUTHORITY_MAP_ID, DOCS_ONLY_AUTHORITY_MAP_ID) == "successor"
    assert authority_map_relation(SUCCESSOR_AUTHORITY_MAP_ID, DOCS_ONLY_AUTHORITY_MAP_ID) == "successor"
    assert authority_map_relation(DOCS_ONLY_AUTHORITY_MAP_ID, DOCS_ONLY_AUTHORITY_MAP_ID) == "same"
    assert authority_map_relation(DOCS_ONLY_AUTHORITY_MAP_ID, SUCCESSOR_AUTHORITY_MAP_ID) == "unknown"
    assert authority_map_relation(HISTORICAL_AUTHORITY_MAP_ID, REPO_ONLY_AUTHORITY_MAP_ID) == "successor"
    assert authority_map_relation(SUCCESSOR_AUTHORITY_MAP_ID, REPO_ONLY_AUTHORITY_MAP_ID) == "successor"
    assert authority_map_relation(DOCS_ONLY_AUTHORITY_MAP_ID, REPO_ONLY_AUTHORITY_MAP_ID) == "successor"
    assert authority_map_relation(REPO_ONLY_AUTHORITY_MAP_ID, REPO_ONLY_AUTHORITY_MAP_ID) == "same"
    assert authority_map_relation(REPO_ONLY_AUTHORITY_MAP_ID, DOCS_ONLY_AUTHORITY_MAP_ID) == "unknown"


@pytest.mark.parametrize(
    ("batch_map", "current_map", "expected"),
    [
        (left, right, "same" if left == right else "successor" if left_index < right_index else "unknown")
        for left_index, left in enumerate((HISTORICAL_AUTHORITY_MAP_ID, SUCCESSOR_AUTHORITY_MAP_ID, DOCS_ONLY_AUTHORITY_MAP_ID, REPO_ONLY_AUTHORITY_MAP_ID))
        for right_index, right in enumerate((HISTORICAL_AUTHORITY_MAP_ID, SUCCESSOR_AUTHORITY_MAP_ID, DOCS_ONLY_AUTHORITY_MAP_ID, REPO_ONLY_AUTHORITY_MAP_ID))
    ] + [
        (None, REPO_ONLY_AUTHORITY_MAP_ID, "unknown"),
        (HISTORICAL_AUTHORITY_MAP_ID, None, "unknown"),
        ("unknown-map", REPO_ONLY_AUTHORITY_MAP_ID, "unknown"),
        (REPO_ONLY_AUTHORITY_MAP_ID, "unknown-map", "unknown"),
    ],
)
def test_authority_map_relation_is_a_closed_four_by_four_forward_matrix(batch_map, current_map, expected) -> None:
    assert authority_map_relation(batch_map, current_map) == expected


def test_repo_only_v4_raw_product_and_receipt_pins_are_exact_and_current() -> None:
    product_path = "docs/plans/harbour-one-product-roadmap.json"
    receipt_raw = (ROOT / H1R05_ACCEPTANCE_RECEIPT_PATH).read_bytes()
    product_raw = (ROOT / product_path).read_bytes()
    assert len(REPO_ONLY_AUTHORITY_PINS) == 12
    assert _raw_git_blob(product_raw) == REPO_ONLY_AUTHORITY_PINS[product_path]
    assert _raw_git_blob(receipt_raw) == H1R05_ACCEPTANCE_RECEIPT_RAW_BLOB
    assert _authority_blob(product_path, product_raw, REPO_ONLY_AUTHORITY_MAP_ID) == REPO_ONLY_AUTHORITY_PINS[product_path]
    assert _authority_blob(H1R05_ACCEPTANCE_RECEIPT_PATH, receipt_raw, REPO_ONLY_AUTHORITY_MAP_ID) == H1R05_ACCEPTANCE_RECEIPT_RAW_BLOB


def test_repo_only_v4_receipt_is_a_closed_prepromotion_leaf() -> None:
    receipt = strict_json_object((ROOT / H1R05_ACCEPTANCE_RECEIPT_PATH).read_bytes())
    assert set(receipt) == {
        "schema", "receipt_id", "slice_id", "acceptance_scope", "accepted_candidate",
        "prepromotion_authority", "accepted_inputs", "verification", "independent_review", "not_verified",
    }
    encoded = canonical_json_bytes(receipt).decode("utf-8")
    assert "h1r05-repo-only-v4" not in encoded
    assert "REPO_ONLY_PRODUCT_RAW_BLOB" not in encoded
    assert receipt["prepromotion_authority"]["manifest_digest"] == "sha256:f52fa560c8bb2d0c5495e9421432a96a5a6ee771342eeea9ed36c3f4fba49ebf"
    assert receipt["accepted_inputs"]["input_count"] == len(receipt["accepted_inputs"]["sha256_by_path"]) == 99


def test_prepromotion_v3_manifest_vector_stays_literal_under_v4() -> None:
    from src.planning_revision_store import _migration_digest, _migration_manifest_identity

    current = deepcopy(build_h1r03_migration_manifest(ROOT))
    current["authority_map_id"] = DOCS_ONLY_AUTHORITY_MAP_ID
    current["authority_pins"] = deepcopy(DOCS_ONLY_AUTHORITY_PINS)
    identity = _migration_manifest_identity(current)
    assert _migration_digest(identity) == "sha256:f52fa560c8bb2d0c5495e9421432a96a5a6ee771342eeea9ed36c3f4fba49ebf"


def test_v3_and_v4_maps_use_exact_raw_product_and_document_bytes(tmp_path: Path) -> None:
    product_path = "docs/plans/harbour-one-product-roadmap.json"
    product_raw = (ROOT / product_path).read_bytes()
    document_raw = (ROOT / H1R04_ACCEPTED_DOCUMENT_PATH).read_bytes()
    assert _raw_git_blob(product_raw) == REPO_ONLY_AUTHORITY_PINS[product_path]
    assert DOCS_ONLY_AUTHORITY_PINS[product_path] != REPO_ONLY_AUTHORITY_PINS[product_path]
    v3_product_raw = subprocess.run(
        ["git", "cat-file", "blob", DOCS_ONLY_AUTHORITY_PINS[product_path]],
        cwd=ROOT, check=True, capture_output=True,
    ).stdout
    assert _raw_git_blob(v3_product_raw) == DOCS_ONLY_AUTHORITY_PINS[product_path]
    assert _authority_blob(product_path, v3_product_raw, DOCS_ONLY_AUTHORITY_MAP_ID) == DOCS_ONLY_AUTHORITY_PINS[product_path]
    assert _raw_git_blob(document_raw) == H1R04_ACCEPTED_DOCUMENT_RAW_BLOB
    for map_id in (DOCS_ONLY_AUTHORITY_MAP_ID, REPO_ONLY_AUTHORITY_MAP_ID):
        assert _authority_blob(H1R04_ACCEPTED_DOCUMENT_PATH, document_raw, map_id) == H1R04_ACCEPTED_DOCUMENT_RAW_BLOB
    assert current_authority_map(ROOT) == REPO_ONLY_AUTHORITY_MAP_ID
    fixture = _migration_fixture_root(tmp_path)
    assert current_authority_map(fixture) == REPO_ONLY_AUTHORITY_MAP_ID
    for relative in (product_path, H1R04_ACCEPTED_DOCUMENT_PATH, H1R05_ACCEPTANCE_RECEIPT_PATH):
        drifted_root = tmp_path / ("drift-" + Path(relative).stem)
        _copy_fixture(fixture, drifted_root)
        target = drifted_root / relative
        target.write_bytes(target.read_bytes() + b"\n")
        assert current_authority_map(drifted_root) is None
        with pytest.raises(DurablePlanningStoreError) as stale:
            build_h1r03_migration_manifest(drifted_root)
        assert stale.value.code == "stale_authority"


def _copy_fixture(source: Path, destination: Path) -> None:
    for path in source.rglob("*"):
        if path.is_file():
            target = destination / path.relative_to(source)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())


def test_historical_v1_digest_and_map_vector_remain_frozen() -> None:
    from src.planning_revision_store import _migration_digest, _migration_manifest_identity

    manifest = build_h1r03_migration_manifest(ROOT)
    historical = deepcopy(manifest)
    historical["schema"] = MIGRATION_MANIFEST_SCHEMA_V1
    historical.pop("authority_map_id")
    historical["authority_pins"] = deepcopy(HISTORICAL_AUTHORITY_PINS)
    identity = _migration_manifest_identity(historical)
    digest = _migration_digest(identity)
    assert digest == "sha256:eec20e5e9a4768c1f8b3fe241cbff655d8eda0c4022246153e332ad3f722c1ec"
    assert identity["authority_pins"]["docs/plans/harbour-one-product-roadmap.json"] == "e4faf54dcc3a0c971e3f4c8152bbf61b99a35bbf"
    assert "authority_map_id" not in identity


def test_later_current_roadmap_drift_is_stale_authority(tmp_path: Path) -> None:
    fixture_root = _migration_fixture_root(tmp_path)
    roadmap = fixture_root / "docs/plans/harbour-one-product-roadmap.json"
    roadmap.write_bytes(roadmap.read_bytes() + b"\n")
    with pytest.raises(DurablePlanningStoreError) as stale:
        build_h1r03_migration_manifest(fixture_root)
    assert stale.value.code == "stale_authority"


def test_accept_helper_rechecks_sources_at_locked_commit_boundary(tmp_path: Path, monkeypatch) -> None:
    store, _issuer, principal = _authority(tmp_path)
    manifest = build_h1r03_migration_manifest(ROOT)
    store.prepare_migration(principal, manifest=manifest)
    state_path = next((tmp_path / "store").glob("harbour-planning-state.*.json"))
    before = state_path.read_bytes()
    target = (ROOT / FROZEN_MEMBERS[0][0]).resolve()
    original_read_bytes = Path.read_bytes
    original_refresh = store._refresh_locked
    drifted = False

    def projected_read_bytes(path):
        raw = original_read_bytes(path)
        return raw + b"\n" if drifted and path.resolve() == target else raw

    def refresh_then_drift():
        nonlocal drifted
        original_refresh()
        drifted = True

    monkeypatch.setattr(Path, "read_bytes", projected_read_bytes)
    monkeypatch.setattr(store, "_refresh_locked", refresh_then_drift)
    with pytest.raises(DurablePlanningStoreError) as stale:
        accept_h1r03_migration(
            store, principal, repo_root=ROOT, batch_id=manifest["batch_id"],
        )
    assert stale.value.code == "stale_authority"
    assert drifted is True
    assert store._path.read_bytes() == before


def test_accept_helper_exact_completed_replay_skips_current_manifest_builder(
    tmp_path: Path, monkeypatch,
) -> None:
    import src.planning_roadmap_migration as migration_module

    store, issuer, principal = _authority(tmp_path)
    manifest = build_h1r03_migration_manifest(ROOT)
    store.prepare_migration(principal, manifest=manifest)
    accepted = accept_h1r03_migration(
        store, principal, repo_root=ROOT, batch_id=manifest["batch_id"],
    )
    state_path = next((tmp_path / "store").glob("harbour-planning-state.*.json"))
    accepted_bytes = state_path.read_bytes()
    same_session_new_nonce = issuer.issue(
        subject_id="migration-operator", owner_id="fuzzy",
        auth_context_digest=AUTH_CONTEXT, ttl_seconds=3600, nonce="helper-replay-fresh-nonce",
    )

    def reject_manifest_build(_repo_root):
        pytest.fail("exact completed accept replay rebuilt current source authority")

    monkeypatch.setattr(migration_module, "build_h1r03_migration_manifest", reject_manifest_build)
    replayed = accept_h1r03_migration(
        store, same_session_new_nonce, repo_root=ROOT, batch_id=manifest["batch_id"],
    )
    assert replayed == accepted
    assert state_path.read_bytes() == accepted_bytes


def test_supporting_source_hash_observation_is_persistable_but_not_manifest_identity(tmp_path: Path) -> None:
    fixture_root = _migration_fixture_root(tmp_path)
    generic = "docs/plans/unbound-supporting-observation.json"
    target = fixture_root / generic
    target.write_text('{"plan_id":"unbound-supporting-observation"}\n', encoding="utf-8")
    before = build_h1r03_migration_manifest(fixture_root)
    target.write_text('{"plan_id":"unbound-supporting-observation","value":2}\n', encoding="utf-8")
    after = build_h1r03_migration_manifest(fixture_root)
    assert before["structural_census"] == after["structural_census"]
    assert before["manifest_digest"] == after["manifest_digest"]
    assert before["supporting_observation"] != after["supporting_observation"]


@pytest.mark.parametrize("relative,contents", [
    ("specs/roadmaps/new.v1.json", b'{"plan_id":"new-roadmap"}'),
    ("specs/roadmaps/bad.v1.json", b"{"),
])
def test_new_or_invalid_specs_roadmap_membership_fails_closed(tmp_path: Path, relative: str, contents: bytes) -> None:
    fixture_root = _migration_fixture_root(tmp_path)
    target = fixture_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(contents)
    with pytest.raises(DurablePlanningStoreError) as stale:
        build_h1r03_migration_manifest(fixture_root)
    assert stale.value.code == "stale_authority"


@pytest.mark.parametrize("relative", [path for path, *_rest in FROZEN_MEMBERS])
def test_each_frozen_source_drift_is_stale_authority(tmp_path: Path, relative: str) -> None:
    fixture_root = _migration_fixture_root(tmp_path)
    source = fixture_root / relative
    source.write_bytes(source.read_bytes() + b"\n")
    with pytest.raises(DurablePlanningStoreError) as stale:
        build_h1r03_migration_manifest(fixture_root)
    assert stale.value.code == "stale_authority"


def test_mapping_closes_slice_paths_graph_precedence_and_runtime_fields() -> None:
    manifest = build_h1r03_migration_manifest(ROOT)
    slice_doc = json.loads(manifest["normalized_payloads"]["abc-roadmap-continuation-runtime"])
    assert slice_doc["roadmaps"][0]["nodes"][9]["allowed_paths"][0] == "static"
    assert slice_doc["roadmaps"][0]["nodes"][0]["verification_rule_ids"][0].endswith(":000")
    graph_doc = json.loads(manifest["normalized_payloads"]["odysseus-multiagent-roadmap"])
    assert len(graph_doc["roadmaps"][0]["edges"]) == 40
    encoded = json.dumps(graph_doc)
    for forbidden in ("priority_rank", "completion_state", "status", "abc"):
        assert f'"{forbidden}"' not in encoded


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-06-20", "2026-06-20T00:00:00Z"),
        ("2026-08-11T12:19:00+02:00", "2026-08-11T10:19:00Z"),
        ("2026-06-21T12:00:00", "2026-06-21T12:00:00Z"),
    ],
)
def test_timestamp_normalization(value: str, expected: str) -> None:
    assert normalize_legacy_timestamp(value) == expected


def test_atomic_six_head_accept_replay_restart_rollback_and_aba_fence(tmp_path: Path, monkeypatch) -> None:
    store, issuer, principal = _authority(tmp_path)
    manifest = build_h1r03_migration_manifest(ROOT)
    prepared = store.prepare_migration(principal, manifest=manifest)
    state_path = next((tmp_path / "store").glob("harbour-planning-state.*.json"))
    prepared_bytes = state_path.read_bytes()
    assert store.prepare_migration(principal, manifest=manifest) == prepared
    assert state_path.read_bytes() == prepared_bytes
    with pytest.raises(DurablePlanningStoreError):
        store.get_head(principal, owner_id="fuzzy", project_id="harbour-one", roadmap_id=FROZEN_MEMBERS[0][2])

    import src.planning_revision_store as store_module
    original_atomic_write = store_module._atomic_write_json
    accept_replaces = []

    def counted_atomic_write(path, value):
        accept_replaces.append(path)
        return original_atomic_write(path, value)

    monkeypatch.setattr(store_module, "_atomic_write_json", counted_atomic_write)
    accepted = _accept(store, principal, manifest)
    assert accept_replaces == [state_path]
    accepted_bytes = state_path.read_bytes()
    assert len(accepted.new_revision_ids) == 6
    assert _accept(store, principal, manifest) == accepted
    assert state_path.read_bytes() == accepted_bytes
    disk = json.loads(accepted_bytes)
    assert len(disk["heads"]) == 6
    assert set(disk["head_fences"].values()) == {1}

    restarted = DurablePlanningRevisionStore(
        tmp_path / "store", trusted_issuers=(issuer,), clock=lambda: NOW
    )
    assert restarted.available is True
    rolled = restarted.rollback_migration(principal, batch_id=manifest["batch_id"])
    assert rolled.after_state == "rolled_back"
    final = json.loads(state_path.read_text(encoding="utf-8"))
    assert final["heads"] == {}
    assert set(final["head_fences"].values()) == {2}
    assert len(final["revisions"]) == 6


def test_manifest_tamper_and_phase_cas_failure_preserve_preimage(tmp_path: Path) -> None:
    store, _issuer, principal = _authority(tmp_path)
    manifest = build_h1r03_migration_manifest(ROOT)
    hostile = deepcopy(manifest)
    hostile["source_items"][0]["roadmap_id"] = "substituted"
    with pytest.raises(DurablePlanningStoreError) as invalid:
        store.prepare_migration(principal, manifest=hostile)
    assert invalid.value.code == "validation_failed"
    assert list((tmp_path / "store").glob("harbour-planning-state.*.json")) == []

    store.prepare_migration(principal, manifest=manifest)
    state_path = next((tmp_path / "store").glob("harbour-planning-state.*.json"))
    first = manifest["source_items"][0]
    store.create_project(
        principal, owner_id="fuzzy", project_id="harbour-one",
        roadmap_id=first["roadmap_id"],
        payload_bytes=manifest["normalized_payloads"][first["roadmap_id"]].encode(),
        idempotency_key="competing-single-subject-create", expected_generation=0,
    )
    before = state_path.read_bytes()
    with pytest.raises(DurablePlanningStoreError) as stale:
        _accept(store, principal, manifest)
    assert stale.value.code == "stale_authority"
    assert state_path.read_bytes() == before


def test_duplicate_keys_nonfinite_bad_done_when_and_second_terminal_slash_fail() -> None:
    source = json.loads((ROOT / FROZEN_MEMBERS[0][0]).read_text(encoding="utf-8"))
    source["slices"][0]["done_when"] = [["nested"]]
    with pytest.raises(ValueError):
        map_legacy_roadmap(source, source_path=FROZEN_MEMBERS[0][0], adapter="legacy_slices_v1")
    source = json.loads((ROOT / FROZEN_MEMBERS[0][0]).read_text(encoding="utf-8"))
    source["slices"][0]["allowed_files"] = ["static//"]
    with pytest.raises(ValueError):
        map_legacy_roadmap(source, source_path=FROZEN_MEMBERS[0][0], adapter="legacy_slices_v1")


def test_abort_is_head_free_replayable_and_corrupt_batch_restarts_unavailable(tmp_path: Path) -> None:
    store, issuer, principal = _authority(tmp_path)
    manifest = build_h1r03_migration_manifest(ROOT)
    store.prepare_migration(principal, manifest=manifest)
    aborted = store.abort_migration(principal, batch_id=manifest["batch_id"])
    state_path = next((tmp_path / "store").glob("harbour-planning-state.*.json"))
    before = state_path.read_bytes()
    assert aborted.after_state == "aborted"
    assert store.abort_migration(principal, batch_id=manifest["batch_id"]) == aborted
    assert state_path.read_bytes() == before
    state = json.loads(before)
    assert state["heads"] == {} and state["head_fences"] == {}

    state["migration_batches"][manifest["batch_id"]]["unknown"] = True
    state_path.write_text(json.dumps(state, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    corrupt = state_path.read_bytes()
    restarted = DurablePlanningRevisionStore(
        tmp_path / "store", trusted_issuers=(issuer,), clock=lambda: NOW
    )
    assert restarted.available is False
    with pytest.raises(DurablePlanningStoreError) as disabled:
        restarted.prepare_migration(principal, manifest=manifest)
    assert disabled.value.code == "validation_failed"
    assert state_path.read_bytes() == corrupt


def test_migration_replay_uses_stable_session_identity_and_receipt_readback_is_detached(tmp_path: Path) -> None:
    store, issuer, principal = _authority(tmp_path)
    manifest = build_h1r03_migration_manifest(ROOT)
    prepared = store.prepare_migration(principal, manifest=manifest)
    state_path = next((tmp_path / "store").glob("harbour-planning-state.*.json"))
    prepared_bytes = state_path.read_bytes()
    same_session_new_nonce = issuer.issue(
        subject_id="migration-operator", owner_id="fuzzy",
        auth_context_digest=AUTH_CONTEXT, ttl_seconds=3600, nonce="new-fresh-nonce",
    )
    assert store.prepare_migration(same_session_new_nonce, manifest=manifest) == prepared
    assert state_path.read_bytes() == prepared_bytes

    other_session = issuer.issue(
        subject_id="migration-operator", owner_id="fuzzy",
        auth_context_digest="sha256:" + hashlib.sha256(b"different-session").hexdigest(),
        ttl_seconds=3600, nonce="other-session-nonce",
    )
    with pytest.raises(DurablePlanningStoreError) as conflict:
        _accept(store, other_session, manifest)
    assert conflict.value.code == "idempotency_conflict"
    assert state_path.read_bytes() == prepared_bytes

    accepted = _accept(store, same_session_new_nonce, manifest)
    accepted_bytes = state_path.read_bytes()
    rotated_issuer = AuthenticatedPrincipalIssuer(
        issuer_id="odysseus-auth", key_id="h1r03-key-rotated",
        secret=b"h1r03-test-secret-material-rotated1", clock=lambda: NOW,
    )
    rotated_principal = rotated_issuer.issue(
        subject_id="migration-operator", owner_id="fuzzy",
        auth_context_digest=AUTH_CONTEXT, ttl_seconds=3600, nonce="rotated-key-nonce",
    )
    restarted = DurablePlanningRevisionStore(
        tmp_path / "store", trusted_issuers=(rotated_issuer,), clock=lambda: NOW
    )
    assert _accept(restarted, rotated_principal, manifest) == accepted
    assert state_path.read_bytes() == accepted_bytes
    receipt = store.read_migration_receipt(
        other_session, owner_id="fuzzy", receipt_id=accepted.receipt_id,
    )
    receipt["after_subjects"].clear()
    again = store.read_migration_receipt(
        same_session_new_nonce, owner_id="fuzzy", receipt_id=accepted.receipt_id,
    )
    assert len(again["after_subjects"]) == 6
    assert not {"principal_ref", "principal_identity", "nonce", "attestation"} & set(again)
    with pytest.raises(DurablePlanningStoreError) as hidden:
        store.read_migration_receipt(same_session_new_nonce, owner_id="other", receipt_id=accepted.receipt_id)
    assert hidden.value.code == "owner_scope_denied"


def test_accepted_projection_records_follow_current_head_ancestry(tmp_path: Path) -> None:
    store, issuer, principal = _authority(tmp_path)
    manifest = build_h1r03_migration_manifest(ROOT)
    store.prepare_migration(principal, manifest=manifest)
    _accept(store, principal, manifest)
    item = manifest["source_items"][0]
    roadmap_id = item["roadmap_id"]
    head = store.get_head(principal, owner_id="fuzzy", project_id="harbour-one", roadmap_id=roadmap_id)
    first = json.loads(store.read_payload(principal, owner_id="fuzzy", revision_id=head.current_revision_id))
    roadmap = first["roadmaps"][0]
    roadmap["revision"] = 2
    roadmap["title"] = "Durable appended title"
    roadmap["content_hash"] = compute_roadmap_content_hash(roadmap)
    first["project"]["latest_approved_revision"][roadmap_id] = {
        "revision": 2, "content_hash": roadmap["content_hash"],
    }
    second_bytes = json.dumps(first, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()
    appended = store.append_revision(
        principal, owner_id="fuzzy", project_id="harbour-one", roadmap_id=roadmap_id,
        payload_bytes=second_bytes, expected_head=head, idempotency_key="h1r03-append-test-0001",
    )
    _batch, records, bindings = store.read_accepted_migration_records(
        principal, owner_id="fuzzy", project_id="harbour-one"
    )
    assert len([record for owner, doc, record in records if doc["roadmaps"][0]["roadmap_id"] == roadmap_id]) == 2
    assert any(doc["roadmaps"][0]["title"] == "Durable appended title" for _owner, doc, _ref in records)
    binding = next(item for item in bindings if item["roadmap_id"] == roadmap_id)
    assert binding["accepted_revision"] == 1
    assert binding["current_revision"] == 2
    assert binding["approved_revision"] == 1
    assert binding["current_revision_id"] == appended.new_revision_id
    assert appended.after_head.generation == 2
    projection = PlanningRevisionStore(records, trusted_head_bindings=bindings)
    default_view = projection.get_roadmap("fuzzy", "harbour-one", roadmap_id)
    assert default_view["roadmap"]["revision"] == 1
    assert projection.get_roadmap("fuzzy", "harbour-one", roadmap_id, revision=2)["roadmap"]["revision"] == 2
    summary = next(
        item for item in projection.get_project("fuzzy", "harbour-one")["roadmaps"]
        if item["roadmap_id"] == roadmap_id
    )
    assert summary["newest_revision"] == 2
    assert summary["latest_approved_revision"] == 1


def test_restart_rejects_duplicate_accepted_subject_vector_collapsed_to_one_head(
    tmp_path: Path,
) -> None:
    store, issuer, principal = _authority(tmp_path)
    manifest = build_h1r03_migration_manifest(ROOT)
    store.prepare_migration(principal, manifest=manifest)
    _accept(store, principal, manifest)
    state_path = next((tmp_path / "store").glob("harbour-planning-state.*.json"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    batch = state["migration_batches"][manifest["batch_id"]]
    accepted_receipt = state["receipts"][batch["accepted_receipt_id"]]
    first = deepcopy(batch["accepted_subjects"][0])
    duplicate_vector = [deepcopy(first) for _ in range(6)]
    batch["accepted_subjects"] = duplicate_vector
    accepted_receipt["after_subjects"] = deepcopy(duplicate_vector)
    output = {
        "after_state": accepted_receipt["after_state"],
        "after_subjects": duplicate_vector,
        "new_revision_ids": accepted_receipt["new_revision_ids"],
    }
    canonical = json.dumps(
        output, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    accepted_receipt["output_digest"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    state["heads"] = {first["subject_key"]: deepcopy(first["head"])}
    state["head_fences"] = {first["subject_key"]: 1}
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    corrupt = state_path.read_bytes()

    restarted = DurablePlanningRevisionStore(
        tmp_path / "store", trusted_issuers=(issuer,), clock=lambda: NOW
    )
    assert restarted.available is False
    assert state_path.read_bytes() == corrupt
