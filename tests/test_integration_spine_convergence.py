from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import pytest

import scripts.validate_integration_spine_convergence as validator


REPO_ROOT = Path(__file__).resolve().parents[1]
ROADMAP = REPO_ROOT / "docs/plans/parallel-roadmap-execution-round7.json"
VALIDATOR = REPO_ROOT / "scripts/validate_integration_spine_convergence.py"


@dataclass(frozen=True, slots=True)
class SyntheticAuthorityWorld:
    authority: validator.AuthoritySnapshotBundle
    pins: Mapping[str, str]
    evidence_path: str
    baseline_blob: str

    @staticmethod
    def _copy_json(value: Any) -> Any:
        """Copy only exact JSON values; reject protocol lookalikes fail-closed."""

        if type(value) is dict:
            if any(type(key) is not str for key in value):
                raise ValueError("invalid_explicit_fixture_document")
            return {
                key: SyntheticAuthorityWorld._copy_json(item)
                for key, item in value.items()
            }
        if type(value) is list:
            return [SyntheticAuthorityWorld._copy_json(item) for item in value]
        if type(value) in {str, int, float, bool} or value is None:
            return value
        raise ValueError("invalid_explicit_fixture_document")

    @classmethod
    def from_explicit(
        cls,
        *,
        documents: dict[str, dict[str, Any]],
        blob_ids: dict[str, str],
        pins: dict[str, str],
        evidence_path: str,
        baseline_blob: str,
    ) -> "SyntheticAuthorityWorld":
        """Build from supplied in-memory authority only; never discover it."""

        if not (
            type(documents) is dict
            and type(blob_ids) is dict
            and type(pins) is dict
            and type(evidence_path) is str
            and type(baseline_blob) is str
            and validator.GIT_BLOB_ID.fullmatch(baseline_blob)
            and evidence_path in pins
            and validator.GIT_BLOB_ID.fullmatch(pins[evidence_path])
            and set(documents).issubset(blob_ids)
            and all(
                type(path) is str
                and type(blob_id) is str
                and validator.GIT_BLOB_ID.fullmatch(blob_id)
                for values in (blob_ids, pins)
                for path, blob_id in values.items()
            )
        ):
            raise ValueError("invalid_explicit_fixture_authority")
        copied_documents = {
            path: validator._freeze_json(cls._copy_json(document))
            for path, document in documents.items()
            if type(path) is str and type(document) is dict
        }
        if len(copied_documents) != len(documents):
            raise ValueError("invalid_explicit_fixture_authority")
        authority = validator.AuthoritySnapshotBundle(
            documents=MappingProxyType(copied_documents),
            blob_ids=MappingProxyType(dict(blob_ids)),
            paths=tuple(sorted(copied_documents)),
        )
        return cls(
            authority=authority,
            pins=MappingProxyType(dict(pins)),
            evidence_path=evidence_path,
            baseline_blob=baseline_blob,
        )

    def terminal_v2(
        self,
        run_id: str,
        allowed_paths: list[str],
        *,
        authority_revision: int,
    ) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "claim_id": run_id,
            "claim_class": "repo_claim_v2",
            "slice_id": f"slice-{run_id}",
            "owner": "Bob/Terra fixture",
            "thread_id": "fixture-thread",
            "state": "released_blocked",
            "allowed_paths": sorted(allowed_paths),
            "hotfiles": [],
            "read_only_pins": dict(self.pins),
            "authority_revision": authority_revision,
            "live_actions": False,
            "released_at": "2026-08-03T11:10:00+02:00",
        }

    @staticmethod
    def replace_active_authority(data: dict[str, Any]) -> None:
        """Start a synthetic active fixture from no production authority."""

        data["external_lane_claims"] = []
        data["long_run_state"]["active_claims"] = []
        data["long_run_state"]["agent_leases"] = []

    def running_v2(
        self,
        data: dict[str, Any],
        run_id: str,
        allowed_paths: list[str],
        *,
        read_only_pins: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.replace_active_authority(data)
        return self._append_running_v2(
            data, run_id, allowed_paths, read_only_pins=read_only_pins
        )

    def _append_running_v2(
        self,
        data: dict[str, Any],
        run_id: str,
        allowed_paths: list[str],
        *,
        read_only_pins: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        revision = data["long_run_state"]["revision"]
        source_id = f"{run_id}-terminal-source"
        source = self.terminal_v2(
            source_id,
            allowed_paths,
            authority_revision=revision - 2,
        )
        pins = dict(self.pins)
        pins.update(read_only_pins or {})
        claim = {
            **source,
            "run_id": run_id,
            "claim_id": run_id,
            "slice_id": f"slice-{run_id}",
            "state": "running",
            "acquired_at": "2026-08-03T11:20:00+02:00",
            "lease_expires_at": "2026-08-03T13:20:00+02:00",
            "dependencies": [],
            "read_only_pins": pins,
            "regression_tests": [
                {
                    "id": f"{run_id}-focused",
                    "tier": "focused",
                    "argv": ["venv\\Scripts\\python.exe", "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests\\test_parallel_roadmap_execution.py", "--basetemp", f".abc-test-tmp/{run_id}/focused"],
                },
                {
                    "id": f"{run_id}-dependency",
                    "tier": "dependency",
                    "argv": ["venv\\Scripts\\python.exe", "-B", "scripts\\validate_parallel_roadmap_execution.py", "--format", "json"],
                },
                {
                    "id": f"{run_id}-central",
                    "tier": "central",
                    "argv": ["venv\\Scripts\\python.exe", "-B", "scripts\\roadmap_multi_agent_guidance.py", "--format", "json"],
                },
                {
                    "id": f"{run_id}-compile",
                    "tier": "focused",
                    "argv": ["venv\\Scripts\\python.exe", "-B", "-m", "py_compile", "scripts\\validate_parallel_roadmap_execution.py", "tests\\test_parallel_roadmap_execution.py"],
                },
            ],
            "pytest_basetemp": {"focused": f".abc-test-tmp/{run_id}/focused"},
            "baseline_blobs": {
                path: self.baseline_blob for path in sorted(allowed_paths)
            },
            "baseline_readback": "materialized_exact_binary_readback_pass",
            "new_evidence_writer_paths": [],
            "claim_transfer": {
                "from_claim_id": source_id,
                "source_released_at": source["released_at"],
                "transferred_at": "2026-08-03T11:20:00+02:00",
                "gap_preserved_no_writer": True,
                "architecture_handoff": "current_authority_fixture",
            },
            "authority_revision": {
                "claimed_against_round7_revision": revision - 1,
                "registered_round7_revision": revision,
            },
            "candidate_blobs": {},
            "candidate_state": "not_frozen",
            "verification_tiers": {"focused": "fixture", "dependency": "fixture", "central": "fixture", "expensive_live": "not_run_user_gated"},
            "correction_budget": {"limit_per_invariant": 1, "used": 0, "failure_class": None, "after_repeat": "stop_and_rearchitect"},
            "stale_invalidation": sorted(validator.ABC_V2_STALE_TRIGGERS),
            "hostile_matrix": ["fixture hostile boundary"],
            "acceptance": ["fixture acceptance"],
            "forbidden": ["live"],
        }
        claim.pop("released_at", None)
        data["external_lane_claims"].extend((source, claim))
        data["long_run_state"]["active_claims"].append(run_id)
        data["long_run_state"]["agent_leases"].append(
            {"claim_id": run_id, "owner": claim["owner"], "expires_at": claim["lease_expires_at"]}
        )
        return claim

    def parallel_running_v2(
        self,
        data: dict[str, Any],
        claims: list[tuple[str, list[str]]],
    ) -> tuple[dict[str, Any], ...]:
        self.replace_active_authority(data)
        return tuple(
            self._append_running_v2(data, run_id, paths)
            for run_id, paths in claims
        )


_EXPLICIT_FIXTURE_EVIDENCE_PATH = validator.R7_EVIDENCE_PATH
_EXPLICIT_FIXTURE_BLOB_IDS = {
    validator.GUIDANCE_PATH: "d32a31405ed66b48bd630f61e9c209f5e61a1b31",
    validator.ROUND6_PATH: "a6cae9fd07f809ef4aec06b84a9b818f6166df05",
    _EXPLICIT_FIXTURE_EVIDENCE_PATH: "34ae26dfbcd9ebf350b62d478d55c38f0d0eae07",
}
_EXPLICIT_FIXTURE_PINS = {
    **_EXPLICIT_FIXTURE_BLOB_IDS,
    "scripts/roadmap_multi_agent_guidance.py": "8973fbdcb9b7cb7eec5ae9f03dd8f9acffdd544c",
    "scripts/validate_parallel_roadmap_execution.py": "22d5be1db6f58906d9fa5132c46a5f358daae300",
    "tests/test_parallel_roadmap_execution.py": "1504f56759b71c69157dcfdadc5ca172d8cae9f1",
    "tests/test_roadmap_multi_agent_guidance.py": "a81c6b4695eb8cfa74c9fcd18bdea8aaf61eeb61",
}
_EXPLICIT_FIXTURE_BASELINE = "33c1ce317ab34ad8f7fb3784ff96efd84468d6eb"


def _current_authority_factory(
    *, documents: dict[str, dict[str, Any]] | None = None
) -> SyntheticAuthorityWorld:
    """Construct a fresh world from caller-injected in-memory authority only."""

    return SyntheticAuthorityWorld.from_explicit(
        documents={} if documents is None else documents,
        blob_ids=_EXPLICIT_FIXTURE_BLOB_IDS,
        pins=_EXPLICIT_FIXTURE_PINS,
        evidence_path=_EXPLICIT_FIXTURE_EVIDENCE_PATH,
        baseline_blob=_EXPLICIT_FIXTURE_BASELINE,
    )


def _rolling_projection_fixture_documents() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Return the entire rolling-projection authority from explicit literals."""

    forge = "docs/plans/forge-usi-code-source-adapter-roadmap.md"
    members = [f"docs/plans/active-{index:02d}.md" for index in range(29)] + [forge]
    members.sort()
    old_members = [path for path in members if path != forge]
    frontiers = [{"roadmap_path": path, "claimable": False} for path in members]
    old_frontiers = [{"roadmap_path": path, "claimable": False} for path in old_members]
    waves = [
        {"implementation_slice_ids": ["a", "b", "c"]},
        {"implementation_slice_ids": ["d", "e"]},
        {"implementation_slice_ids": ["f", "g"]},
    ]
    source_blob, old_blob = "a" * 40, "b" * 40
    new_blob = _EXPLICIT_FIXTURE_BLOB_IDS[validator.ROUND6_PATH]
    digest = validator._member_set_digest(members)
    old_digest = validator._member_set_digest(old_members)
    guidance = {
        "roadmaps": [{"path": path, "profile": "active_fixture"} for path in members],
        "analysis_summary": {"round6_parallel_execution": {
            "inventory_member_set_digest": digest, "inventory_member_count": 30,
            "active_roadmap_count": 30, "normalized_frontier_count": 30,
            "current_claimable_frontier_count": 0, "wave_count": 3,
            "scheduled_repo_slice_count": 7, "inventory_source_blob": source_blob,
            "inventory_denominator_change": {
                "old_count": 29, "new_count": 30, "old_digest": old_digest,
                "new_digest": digest, "old_round6_blob": old_blob,
                "new_round6_blob": new_blob, "added": [forge], "removed": [],
                "scope": "inventory_membership_only_terminal_waves_and_seven_scheduled_slices_unchanged",
            },
        }},
    }
    round6 = {"roadmap_frontiers": frontiers, "waves": waves, "active_claims": []}
    evidence = {"inventory_receipt": {
        "inventory_mode": "rolling_projection", "member_count": 30,
        "member_set_digest": digest, "terminal_waves_unchanged": True,
        "scheduled_slice_count_unchanged": 7,
    }}
    documents = {
        _EXPLICIT_FIXTURE_EVIDENCE_PATH: evidence,
        validator.GUIDANCE_PATH: guidance,
        validator.ROUND6_PATH: round6,
    }
    materialized = {
        source_blob: guidance,
        old_blob: {"roadmap_frontiers": old_frontiers, "waves": waves, "active_claims": []},
        new_blob: round6,
    }
    return documents, materialized


def _current_running_fixture(
    factory: SyntheticAuthorityWorld, run_id: str
) -> dict[str, Any]:
    """Append one running v2 claim to a literal empty authority world."""

    data = {
        "external_lane_claims": [],
        "long_run_state": {
            "revision": 17,
            "active_claims": [],
            "agent_leases": [],
        },
    }
    factory.running_v2(
        data,
        run_id,
        ["src/coding_agent_backend.py", "tests/test_coding_agent_backend.py"],
    )
    return data


def _explicit_active_fixture_authority(
    data: dict[str, Any],
    factory: SyntheticAuthorityWorld,
    *,
    documents: Mapping[str, Mapping[str, Any]] | None = None,
    additional_blob_ids: Mapping[str, str] | None = None,
) -> validator.AuthoritySnapshotBundle:
    """Inject the synthetic active world without inspecting repository state."""

    claims = [
        claim
        for claim in data["external_lane_claims"]
        if type(claim) is dict and claim.get("state") == "running"
    ]
    blob_ids = dict(factory.pins)
    blob_ids.update(additional_blob_ids or {})
    for claim in claims:
        writer_blobs = (
            claim.get("candidate_blobs")
            if claim.get("candidate_state") == "frozen_for_independent_qa"
            else claim.get("baseline_blobs")
        )
        if type(writer_blobs) is dict:
            blob_ids.update(writer_blobs)
    explicit_documents: dict[str, Mapping[str, Any]] = {
        validator.OPEN_WORK_PATH: validator._freeze_json(
            {"current_position": {"external_lane_claims": data["external_lane_claims"]}}
        )
    }
    explicit_documents.update(
        {
            path: validator._freeze_json(document)
            for path, document in (documents or {}).items()
        }
    )
    return validator.AuthoritySnapshotBundle(
        documents=MappingProxyType(explicit_documents),
        blob_ids=MappingProxyType(blob_ids),
        paths=tuple(sorted(blob_ids)),
    )


def test_synthetic_authority_world_is_explicit_and_discovery_free(monkeypatch) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("productive discovery")

    documents = {"fixture.json": {"nested": {"items": ["before"]}}}
    blob_ids = {"fixture.json": "a" * 40, "evidence.json": "b" * 40}
    pins = {"fixture.json": "a" * 40, "evidence.json": "b" * 40}
    monkeypatch.setattr(validator, "_load_json_snapshot", forbidden)
    monkeypatch.setattr(validator, "_path_blob_id", forbidden)
    monkeypatch.setattr(subprocess, "check_output", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)

    injected_factory = _current_authority_factory()
    assert injected_factory.authority.paths == ()
    world = SyntheticAuthorityWorld.from_explicit(
        documents=documents,
        blob_ids=blob_ids,
        pins=pins,
        evidence_path="evidence.json",
        baseline_blob="c" * 40,
    )
    documents["fixture.json"]["nested"]["items"][0] = "after"
    pins["fixture.json"] = "d" * 40
    assert validator._thaw_json(world.authority.get("fixture.json")) == {
        "nested": {"items": ["before"]}
    }
    assert world.pins["fixture.json"] == "a" * 40

    class HostileDocument(dict):
        @property
        def __class__(self) -> type[dict[str, Any]]:
            raise AssertionError("hostile type access")

    with pytest.raises(ValueError, match="invalid_explicit_fixture_authority"):
        SyntheticAuthorityWorld.from_explicit(
            documents={"fixture.json": HostileDocument()},
            blob_ids=blob_ids,
            pins=pins,
            evidence_path="evidence.json",
            baseline_blob="c" * 40,
        )


def test_synthetic_authority_factory_builds_before_productive_claims_are_replaced(
    monkeypatch,
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("fixture construction performed productive discovery")

    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    productive_ids = {claim["run_id"] for claim in data["external_lane_claims"]}
    monkeypatch.setattr(validator, "_load_json_snapshot", forbidden)
    monkeypatch.setattr(validator, "_path_blob_id", forbidden)
    monkeypatch.setattr(subprocess, "check_output", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    factory = _current_authority_factory()
    fixture = factory.running_v2(
        data,
        "fixture-empty-authority-world",
        ["src/fixture.py", "tests/test_fixture.py"],
    )

    fixture_ids = {claim["run_id"] for claim in data["external_lane_claims"]}
    assert productive_ids.isdisjoint(fixture_ids)
    assert fixture_ids == {
        "fixture-empty-authority-world",
        "fixture-empty-authority-world-terminal-source",
    }
    assert data["long_run_state"]["active_claims"] == [fixture["run_id"]]


def test_synthetic_authority_factory_forbids_file_and_git_access_during_construction(
    monkeypatch,
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("fixture construction accessed repository authority")

    monkeypatch.setattr(validator, "_load_json_snapshot", forbidden)
    monkeypatch.setattr(validator, "_path_blob_id", forbidden)
    monkeypatch.setattr(subprocess, "check_output", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)

    world = _current_authority_factory()

    assert world.authority.documents == {}
    assert world.authority.raw_bytes == {}


def _claim_origin_fixture(
    mode: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], validator.AuthoritySnapshotBundle, dict[str, Any]]:
    """Construct one isolated origin world from supplied bytes only."""

    factory = _current_authority_factory()
    data = {
        "external_lane_claims": [],
        "long_run_state": {"revision": 17, "active_claims": [], "agent_leases": []},
        "architecture_packets": {
            "R7-claim-origin-compatibility": {
                "handoff_id": "fixture_claim_origin_compatibility"
            }
        },
    }
    revision = 17
    allowed = (
        ["src/fixture_new_product.py", "tests/test_fixture_new_product.py"]
        if mode == validator.NEW_PRODUCT_PATHS_MODE
        else ["src/fixture_legacy.py", "tests/test_fixture_legacy.py"]
    )
    factory.replace_active_authority(data)
    claim = factory._append_running_v2(data, "fixture-origin", allowed)
    claim["authority_revision"] = {
        "claimed_against_round7_revision": revision - 1,
        "registered_round7_revision": revision,
    }
    claim["architecture_revision"] = "FIXTURE-ORIGIN-ARCH-v1"
    compat = data["architecture_packets"]["R7-claim-origin-compatibility"]
    handoff = compat["handoff_id"]
    source = next(
        item
        for item in data["external_lane_claims"]
        if item.get("run_id") == "fixture-origin-terminal-source"
    )
    blobs = {path: factory.baseline_blob for path in allowed}
    blob_ids = {**factory.pins, **blobs}
    absent_paths: tuple[str, ...] = ()
    if mode == validator.LEGACY_TERMINAL_UPGRADE_MODE:
        source.pop("claim_class")
        source.pop("authority_revision")
        source["candidate_writer_blobs"] = dict(blobs)
        claim["baseline_blobs"] = dict(blobs)
        claim["claim_transfer"] = {
            "from_claim_id": source["run_id"],
            "source_released_at": source["released_at"],
            "transferred_at": "2026-08-03T11:20:00+02:00",
            "gap_preserved_no_writer": True,
            "architecture_handoff": handoff,
            "origin_mode": validator.LEGACY_TERMINAL_UPGRADE_MODE,
            "source_registry": validator.LEGACY_OPEN_WORK_SOURCE_REGISTRY,
        }
    else:
        data["external_lane_claims"].remove(source)
        claim["baseline_blobs"] = {path: "absent" for path in allowed}
        claim["baseline_readback"] = (
            validator.CAPTURED_ABSENT_PATHS_READBACK_PASS
        )
        claim["new_evidence_writer_paths"] = []
        claim["claim_transfer"] = {
            "origin_mode": validator.NEW_PRODUCT_PATHS_MODE,
            "source_registry": None,
            "from_claim_id": None,
            "source_released_at": None,
            "transferred_at": "2026-08-03T11:20:00+02:00",
            "gap_preserved_no_writer": True,
            "architecture_handoff": handoff,
            "new_product_writer_paths": list(allowed),
        }
        blob_ids = dict(factory.pins)
        absent_paths = tuple(allowed)
    packet = {
        "schema_version": 1,
        "kind": validator.CLAIM_ORIGIN_PACKET_KIND,
        "state": "frozen",
        "architect": "root/Sol fixture",
        "slice_id": claim["slice_id"],
        "origin_mode": mode,
        "architecture_revision": claim["architecture_revision"],
        "handoff_id": handoff,
        "allowed_paths": list(allowed),
        "baseline_blobs": dict(claim["baseline_blobs"]),
        "source_claim_id": source["run_id"]
        if mode == validator.LEGACY_TERMINAL_UPGRADE_MODE
        else None,
        "new_product_writer_paths": list(allowed)
        if mode == validator.NEW_PRODUCT_PATHS_MODE
        else [],
    }
    packet["architecture_digest_sha256"] = validator._manifest_digest(packet)
    claim["architecture_digest_sha256"] = packet["architecture_digest_sha256"]
    data["architecture_packets"][claim["slice_id"]] = packet
    open_work = {
        "current_position": {
            "external_lane_claims": [source]
            if mode == validator.LEGACY_TERMINAL_UPGRADE_MODE
            else [claim]
        }
    }
    authority = validator.AuthoritySnapshotBundle(
        documents=MappingProxyType(
            {validator.OPEN_WORK_PATH: validator._freeze_json(open_work)}
        ),
        blob_ids=MappingProxyType(blob_ids),
        paths=tuple(sorted(blob_ids)),
        absent_paths=absent_paths,
        absent_paths_readback=(
            validator.CAPTURED_ABSENT_PATHS_READBACK_PASS
            if mode == validator.NEW_PRODUCT_PATHS_MODE
            else "not_applicable"
        ),
    )
    sources = (
        {source["run_id"]: source}
        if mode == validator.LEGACY_TERMINAL_UPGRADE_MODE
        else {}
    )
    return claim, sources, authority, data


def _validate_claim_origin_fixture(
    claim: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    authority: validator.AuthoritySnapshotBundle,
    roadmap: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    *,
    materialized_git_blob=None,
    validate_active_freeze: bool = False,
) -> list[str]:
    monkeypatch.setattr(
        validator,
        "_materialized_git_blob",
        materialized_git_blob or (lambda *_args: True),
    )
    errors: list[str] = []
    typed = validator._validate_abc_claim_v2(
        claim,
        index=0,
        repo_root=REPO_ROOT,
        authority=authority,
        authority_revision=17,
        claims_by_run_id={**sources, claim["run_id"]: claim},
        active_run_ids={claim["run_id"]},
        leases_by_claim={claim["run_id"]: [{"owner": claim["owner"]}]},
        errors=errors,
        roadmap=roadmap,
    )
    if validate_active_freeze and typed is not None:
        validator._validate_active_freeze_binding(
            claim,
            typed,
            repo_root=REPO_ROOT,
            authority=authority,
            errors=errors,
        )
    return errors


def _validate_running_v2_fixture(
    claim: dict[str, Any],
    data: dict[str, Any],
    factory: SyntheticAuthorityWorld,
    monkeypatch: pytest.MonkeyPatch,
    *,
    additional_blob_ids: Mapping[str, str] | None = None,
) -> list[str]:
    """Validate one injected active world without loading productive authority."""

    monkeypatch.setattr(validator, "_materialized_git_blob", lambda *_args: True)
    errors: list[str] = []
    claims = {
        item["run_id"]: item
        for item in data["external_lane_claims"]
        if type(item) is dict and type(item.get("run_id")) is str
    }
    leases: dict[str, list[dict[str, Any]]] = {}
    for lease in data["long_run_state"]["agent_leases"]:
        if type(lease) is dict and type(lease.get("claim_id")) is str:
            leases.setdefault(lease["claim_id"], []).append(lease)
    validator._validate_abc_claim_v2(
        claim,
        index=0,
        repo_root=REPO_ROOT,
        authority=_explicit_active_fixture_authority(
            data, factory, additional_blob_ids=additional_blob_ids
        ),
        authority_revision=data["long_run_state"]["revision"],
        claims_by_run_id=claims,
        active_run_ids=set(data["long_run_state"]["active_claims"]),
        leases_by_claim=leases,
        errors=errors,
    )
    return errors


def _materialized_repo_genesis_fixture(
    *, frozen: bool = False
) -> tuple[dict[str, Any], validator.AuthoritySnapshotBundle, dict[str, Any]]:
    """Build an all-existing genesis claim from explicit in-memory bytes only."""

    factory = _current_authority_factory()
    data = {
        "external_lane_claims": [],
        "long_run_state": {"revision": 17, "active_claims": [], "agent_leases": []},
        "architecture_packets": {},
    }
    allowed = ["src/existing_fixture.py", "tests/test_existing_fixture.py"]
    payloads = {
        "src/existing_fixture.py": b"source fixture\n",
        "tests/test_existing_fixture.py": b"test fixture\n",
    }
    baselines = {
        path: validator._git_blob_id(payload) for path, payload in payloads.items()
    }
    claim = factory.running_v2(data, "fixture-materialized-repo-genesis", allowed)
    data["external_lane_claims"] = [claim]
    claim.update({
        "slice_id": "fixture-materialized-repo-genesis",
        "authority_revision": {
            "claimed_against_round7_revision": 16,
            "registered_round7_revision": 17,
        },
        "baseline_blobs": dict(baselines),
        "baseline_readback": "materialized_exact_binary_readback_pass",
        "new_evidence_writer_paths": [],
        "architecture_revision": "fixture-materialized-repo-genesis-v1",
        "claim_transfer": {
            "origin_mode": validator.MATERIALIZED_REPO_GENESIS_MODE,
            "source_registry": None,
            "from_claim_id": None,
            "source_released_at": None,
            "transferred_at": "2026-08-03T11:20:00+02:00",
            "gap_preserved_no_writer": True,
            "architecture_handoff": "fixture_materialized_repo_genesis",
            "new_product_writer_paths": [],
        },
    })
    packet_path = "docs/plans/fixture-materialized-repo-genesis-architecture.json"
    correction_budget = {
        "failure_class": "fixture_packet_separation",
        "limit": 1,
        "on_repeat": "release_blocked",
        "used": 0,
    }
    packet = {
        "acceptance": ["stable packet remains separate from candidate Evidence"],
        "allowed_paths": list(allowed),
        "architect": "root/Sol fixture",
        "architecture_revision": claim["architecture_revision"],
        "baseline_blobs": dict(baselines),
        "baseline_readback": claim["baseline_readback"],
        "correction_budget": correction_budget,
        "forbidden": ["productive discovery"],
        "hostile_matrix": ["packet and Evidence aliasing"],
        "kind": "odysseus.sol_materialized_repo_genesis_architecture",
        "named_tests": [
            "tests/test_integration_spine_convergence.py::fixture-stable-freeze",
            "tests/test_integration_spine_convergence.py::fixture-packet-binding",
        ],
        "origin_mode": validator.MATERIALIZED_REPO_GENESIS_MODE,
        "published_round7_revision": 16,
        "schema_version": 1,
        "slice_id": claim["slice_id"],
        "state": "frozen",
        "stop_rule": "release on repeated fixture failure",
        "threat_model": "separate immutable architecture from mutable Evidence",
        "verification_tiers": {
            "focused": "fixture",
            "dependency": "fixture",
            "central": "fixture",
            "expensive_live": "not required",
            "independent_qa": "fixture",
            "sol_review": "fixture",
        },
    }
    packet_raw = json.dumps(
        packet,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    packet_blob = validator._git_blob_id(packet_raw)
    digest = validator._materialized_repo_genesis_packet_digest(packet)
    assert digest is not None
    claim["architecture_digest_sha256"] = digest
    claim["candidate_evidence_path"] = factory.evidence_path
    claim["candidate_evidence_manifest_blob"] = claim["read_only_pins"][
        factory.evidence_path
    ]
    claim["read_only_pins"][packet_path] = packet_blob
    data["architecture_packets"][claim["slice_id"]] = {
        "schema_version": 1,
        "kind": "odysseus.sol_architecture_reference",
        "state": "frozen",
        "architect": "root/Sol fixture",
        "slice_id": claim["slice_id"],
        "architecture_revision": claim["architecture_revision"],
        "architecture_digest_sha256": digest,
        "packet_path": packet_path,
        "packet_blob": packet_blob,
        "allowed_paths": list(allowed),
        "threat_model": packet["threat_model"],
        "correction_budget": correction_budget,
        "stop_rule": packet["stop_rule"],
    }
    evidence: dict[str, Any] = {
        "inventory_receipt": {"inventory_mode": "rolling_projection"}
    }
    if frozen:
        _freeze_origin_claim(claim, baselines)
        claim["candidate_frozen_at"] = "2026-08-03T18:50:00+02:00"
        claim["candidate_set_digest_sha256"] = validator._candidate_set_digest(
            baselines
        )
        evidence = {
            "schema_version": 1,
            "kind": "odysseus.abc_claim_evidence",
            "claim_id": claim["claim_id"],
            "thread_id": claim["thread_id"],
            "authority_revision": claim["authority_revision"],
            "candidate_writer_paths": claim["allowed_paths"],
            "baseline_blobs": claim["baseline_blobs"],
            "baseline_readback": claim["baseline_readback"],
            "candidate_blobs": claim["candidate_blobs"],
            "candidate_state": claim["candidate_state"],
            "candidate_frozen_at": claim["candidate_frozen_at"],
            "candidate_set_digest_sha256": claim["candidate_set_digest_sha256"],
            "candidate_command_manifest_digest_sha256": claim[
                "candidate_command_manifest_digest_sha256"
            ],
            "candidate_pytest_node_manifest_digest_sha256": claim[
                "candidate_pytest_node_manifest_digest_sha256"
            ],
            "candidate_pytest_node_ids": claim["candidate_pytest_node_ids"],
            "read_only_pins": {
                path: blob_id
                for path, blob_id in claim["read_only_pins"].items()
                if path != factory.evidence_path
            },
            "stale_invalidation": claim["stale_invalidation"],
            "verification_tiers": claim["verification_tiers"],
            "correction_budget": claim["correction_budget"],
            "inventory_receipt": {"inventory_mode": "rolling_projection"},
            "live_actions": False,
        }
        evidence_raw = json.dumps(
            evidence,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        evidence_blob = validator._git_blob_id(evidence_raw)
        claim["read_only_pins"][factory.evidence_path] = evidence_blob
        claim["candidate_evidence_path"] = factory.evidence_path
        claim["candidate_evidence_manifest_blob"] = evidence_blob
    else:
        evidence_raw = json.dumps(evidence, sort_keys=True).encode("utf-8")
    blob_ids = {**claim["read_only_pins"], **baselines}
    authority = validator.AuthoritySnapshotBundle(
        documents=MappingProxyType(
            {
                validator.OPEN_WORK_PATH: validator._freeze_json(
                    {"current_position": {"external_lane_claims": [claim]}}
                ),
                factory.evidence_path: validator._freeze_json(evidence),
                packet_path: validator._freeze_json(packet),
            }
        ),
        blob_ids=MappingProxyType(blob_ids),
        paths=tuple(sorted(blob_ids)),
        raw_bytes=MappingProxyType(
            {**payloads, packet_path: packet_raw, factory.evidence_path: evidence_raw}
        ),
    )
    return claim, authority, data


def _r4_standard_v2_world(
    *, frozen: bool = False
) -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    validator.AuthoritySnapshotBundle,
    dict[str, Any],
]:
    """Build the exact packet-bound R4 v2 world from injected bytes only."""

    factory = _current_authority_factory()
    data = {
        "external_lane_claims": [],
        "long_run_state": {"revision": 17, "active_claims": [], "agent_leases": []},
    }
    allowed = ["src/r4_fixture.py", "tests/test_r4_fixture.py"]
    payloads = {
        "src/r4_fixture.py": b"r4 source fixture\n",
        "tests/test_r4_fixture.py": b"r4 test fixture\n",
    }
    baselines = {
        path: validator._git_blob_id(payload) for path, payload in payloads.items()
    }
    claim = factory.running_v2(data, "fixture-r4-packet-bound-v2", allowed)
    source = data["external_lane_claims"][0]
    claim.update(
        {
            "slice_id": validator.R4_PACKET_BOUND_V2_SLICE,
            "authority_revision": {
                "claimed_against_round7_revision": 16,
                "registered_round7_revision": 17,
            },
            "baseline_blobs": dict(baselines),
            "architecture_revision": "FIXTURE-R4-PACKET-BOUND-V2-v1",
        }
    )
    source.update(
        {
            "candidate_state": "frozen_for_independent_qa",
            "candidate_blobs": dict(baselines),
        }
    )
    packet_path = "docs/plans/fixture-r4-packet-bound-v2-architecture.json"
    correction_budget = {
        "failure_class": "fixture_r4_packet_binding",
        "limit": 1,
        "on_repeat": "release_blocked",
        "used": 0,
    }
    packet = {
        "acceptance": ["ordinary v2 is bound to one stable packet"],
        "allowed_paths": list(allowed),
        "architect": "root/Sol fixture",
        "architecture_revision": claim["architecture_revision"],
        "baseline_blobs": dict(baselines),
        "baseline_readback": claim["baseline_readback"],
        "correction_budget": correction_budget,
        "forbidden": ["generic control expansion"],
        "hostile_matrix": ["packet authority and terminal baseline drift"],
        "kind": "odysseus.sol_materialized_repo_genesis_architecture",
        "named_tests": [
            "tests/test_integration_spine_convergence.py::test_r4_standard_v2_requires_stable_packet_authority",
        ],
        "origin_mode": "v2",
        "published_round7_revision": 16,
        "schema_version": 1,
        "slice_id": validator.R4_PACKET_BOUND_V2_SLICE,
        "state": "frozen",
        "stop_rule": "release on repeated R4 fixture failure",
        "threat_model": "bind one standard-v2 successor to one stable packet",
        "verification_tiers": {
            "focused": "fixture",
            "dependency": "fixture",
            "central": "fixture",
            "expensive_live": "not required",
            "independent_qa": "fixture",
            "sol_review": "fixture",
        },
    }
    packet_raw = json.dumps(
        packet,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    packet_blob = validator._git_blob_id(packet_raw)
    digest = validator._materialized_repo_genesis_packet_digest(packet)
    assert digest is not None
    claim["architecture_digest_sha256"] = digest
    claim["read_only_pins"][packet_path] = packet_blob
    claim["candidate_evidence_path"] = factory.evidence_path
    claim["candidate_evidence_manifest_blob"] = claim["read_only_pins"][
        factory.evidence_path
    ]
    reference = {
        "schema_version": 1,
        "kind": "odysseus.sol_architecture_reference",
        "state": "frozen",
        "architect": "root/Sol fixture",
        "slice_id": validator.R4_PACKET_BOUND_V2_SLICE,
        "architecture_revision": claim["architecture_revision"],
        "architecture_digest_sha256": digest,
        "packet_path": packet_path,
        "packet_blob": packet_blob,
        "allowed_paths": list(allowed),
        "threat_model": packet["threat_model"],
        "correction_budget": correction_budget,
        "stop_rule": packet["stop_rule"],
    }
    roadmap = {
        "architecture_packets": {validator.R4_PACKET_BOUND_V2_SLICE: reference}
    }
    evidence: dict[str, Any] = {
        "inventory_receipt": {"inventory_mode": "rolling_projection"}
    }
    if frozen:
        _freeze_origin_claim(claim, baselines)
        claim["candidate_frozen_at"] = "2026-08-03T18:50:00+02:00"
        claim["candidate_set_digest_sha256"] = validator._candidate_set_digest(
            baselines
        )
        evidence = {
            "schema_version": 1,
            "kind": "odysseus.abc_claim_evidence",
            "claim_id": claim["claim_id"],
            "thread_id": claim["thread_id"],
            "authority_revision": claim["authority_revision"],
            "candidate_writer_paths": claim["allowed_paths"],
            "baseline_blobs": claim["baseline_blobs"],
            "baseline_readback": claim["baseline_readback"],
            "candidate_blobs": claim["candidate_blobs"],
            "candidate_state": claim["candidate_state"],
            "candidate_frozen_at": claim["candidate_frozen_at"],
            "candidate_set_digest_sha256": claim["candidate_set_digest_sha256"],
            "candidate_command_manifest_digest_sha256": claim[
                "candidate_command_manifest_digest_sha256"
            ],
            "candidate_pytest_node_manifest_digest_sha256": claim[
                "candidate_pytest_node_manifest_digest_sha256"
            ],
            "candidate_pytest_node_ids": claim["candidate_pytest_node_ids"],
            "read_only_pins": {
                path: blob_id
                for path, blob_id in claim["read_only_pins"].items()
                if path != factory.evidence_path
            },
            "stale_invalidation": claim["stale_invalidation"],
            "verification_tiers": claim["verification_tiers"],
            "correction_budget": claim["correction_budget"],
            "inventory_receipt": {"inventory_mode": "rolling_projection"},
            "live_actions": False,
        }
    evidence_raw = json.dumps(
        evidence,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    evidence_blob = validator._git_blob_id(evidence_raw)
    if frozen:
        claim["read_only_pins"][factory.evidence_path] = evidence_blob
        claim["candidate_evidence_manifest_blob"] = evidence_blob
    blob_ids = {**claim["read_only_pins"], **baselines}
    authority = validator.AuthoritySnapshotBundle(
        documents=MappingProxyType(
            {
                validator.OPEN_WORK_PATH: validator._freeze_json(
                    {"current_position": {"external_lane_claims": [source, claim]}}
                ),
                factory.evidence_path: validator._freeze_json(evidence),
                packet_path: validator._freeze_json(packet),
            }
        ),
        blob_ids=MappingProxyType(blob_ids),
        paths=tuple(sorted(blob_ids)),
        raw_bytes=MappingProxyType(
            {**payloads, packet_path: packet_raw, factory.evidence_path: evidence_raw}
        ),
    )
    return claim, {source["run_id"]: source}, authority, roadmap


def test_r4_standard_v2_requires_stable_packet_authority(monkeypatch) -> None:
    claim, sources, authority, roadmap = _r4_standard_v2_world()

    assert _validate_claim_origin_fixture(
        claim, sources, authority, roadmap, monkeypatch
    ) == []


@pytest.mark.parametrize(
    "case",
    (
        "delete",
        "digest",
        "candidate_evidence_alias",
        "casefold_evidence_directory_alias",
        "blob",
        "semantic",
    ),
)
def test_r4_standard_v2_rejects_packet_delete_digest_path_blob_and_semantic_drift(
    monkeypatch, case
) -> None:
    claim, sources, authority, roadmap = _r4_standard_v2_world()
    reference = roadmap["architecture_packets"][validator.R4_PACKET_BOUND_V2_SLICE]
    packet_path = reference["packet_path"]
    if case == "delete":
        roadmap["architecture_packets"].pop(validator.R4_PACKET_BOUND_V2_SLICE)
    elif case == "digest":
        claim["architecture_digest_sha256"] = "sha256:" + "0" * 64
    elif case == "candidate_evidence_alias":
        reference["packet_path"] = claim["candidate_evidence_path"]
        reference["packet_blob"] = claim["read_only_pins"][
            claim["candidate_evidence_path"]
        ]
    elif case == "casefold_evidence_directory_alias":
        alias = "DOCS/PLANS/EVIDENCE/fixture-r4-packet-bound-v2-architecture.json"
        packet = authority.get(packet_path)
        packet_raw = authority.raw_bytes[packet_path]
        documents = dict(authority.documents)
        documents[alias] = packet
        blob_ids = dict(authority.blob_ids)
        blob_ids[alias] = reference["packet_blob"]
        raw_bytes = dict(authority.raw_bytes)
        raw_bytes[alias] = packet_raw
        claim["read_only_pins"][alias] = reference["packet_blob"]
        reference["packet_path"] = alias
        authority = validator.AuthoritySnapshotBundle(
            documents=MappingProxyType(documents),
            blob_ids=MappingProxyType(blob_ids),
            paths=tuple(sorted(blob_ids)),
            raw_bytes=MappingProxyType(raw_bytes),
        )
    elif case == "blob":
        reference["packet_blob"] = "0" * 40
    else:
        packet = validator._thaw_json(authority.get(packet_path))
        packet["threat_model"] = "semantically drifted after capture"
        documents = dict(authority.documents)
        documents[packet_path] = validator._freeze_json(packet)
        authority = validator.AuthoritySnapshotBundle(
            documents=MappingProxyType(documents),
            blob_ids=authority.blob_ids,
            paths=authority.paths,
            raw_bytes=authority.raw_bytes,
        )

    errors = _validate_claim_origin_fixture(
        claim, sources, authority, roadmap, monkeypatch
    )

    assert any("claim transfer is not exact, terminal" in error for error in errors)


@pytest.mark.parametrize("case", ("candidate_state", "candidate_blobs"))
def test_r4_standard_v2_requires_terminal_r3_candidate_baseline_continuity(
    monkeypatch, case
) -> None:
    claim, sources, authority, roadmap = _r4_standard_v2_world()
    source = next(iter(sources.values()))
    if case == "candidate_state":
        source["candidate_state"] = "blocked_after_freeze"
    else:
        source["candidate_blobs"] = dict(source["candidate_blobs"])
        source["candidate_blobs"][claim["allowed_paths"][0]] = "0" * 40

    errors = _validate_claim_origin_fixture(
        claim, sources, authority, roadmap, monkeypatch
    )

    assert any("claim transfer is not exact, terminal" in error for error in errors)


def test_r4_standard_v2_stable_packet_survives_standard_active_freeze(
    monkeypatch,
) -> None:
    claim, sources, authority, roadmap = _r4_standard_v2_world(frozen=True)

    assert _validate_claim_origin_fixture(
        claim,
        sources,
        authority,
        roadmap,
        monkeypatch,
        validate_active_freeze=True,
    ) == []


def test_materialized_repo_genesis_stable_packet_survives_standard_freeze(
    monkeypatch,
) -> None:
    claim, authority, roadmap = _materialized_repo_genesis_fixture()
    assert _validate_claim_origin_fixture(
        claim, {}, authority, roadmap, monkeypatch
    ) == []

    frozen, frozen_authority, frozen_roadmap = _materialized_repo_genesis_fixture(
        frozen=True
    )
    assert _validate_claim_origin_fixture(
        frozen,
        {},
        frozen_authority,
        frozen_roadmap,
        monkeypatch,
        validate_active_freeze=True,
    ) == []


@pytest.mark.parametrize(
    "case",
    ("source_registry", "new_product_path", "absent_baseline", "blob_drift"),
)
def test_materialized_repo_genesis_rejects_non_null_or_absent_hostile_shapes(
    monkeypatch, case
) -> None:
    claim, authority, roadmap = _materialized_repo_genesis_fixture(
        frozen=case == "blob_drift"
    )
    if case == "source_registry":
        claim["claim_transfer"]["source_registry"] = "forbidden"
    elif case == "new_product_path":
        claim["claim_transfer"]["new_product_writer_paths"] = [
            claim["allowed_paths"][0]
        ]
    elif case == "absent_baseline":
        claim["baseline_blobs"][claim["allowed_paths"][0]] = "absent"
    else:
        drifted = dict(authority.blob_ids)
        drifted[claim["allowed_paths"][0]] = "0" * 40
        authority = validator.AuthoritySnapshotBundle(
            documents=authority.documents,
            blob_ids=MappingProxyType(drifted),
            paths=authority.paths,
            raw_bytes=authority.raw_bytes,
        )

    errors = _validate_claim_origin_fixture(claim, {}, authority, roadmap, monkeypatch)

    assert any(
        "claim transfer is not exact, terminal and revision-bound" in error
        or "baseline blob is not materialized" in error
        or "frozen candidate does not bind current writer bytes bijectively" in error
        for error in errors
    )


@pytest.mark.parametrize(
    "case", ("packet_digest", "packet_evidence_alias", "reference_binding")
)
def test_materialized_repo_genesis_rejects_packet_evidence_alias_and_binding_drift(
    monkeypatch, case
) -> None:
    claim, authority, roadmap = _materialized_repo_genesis_fixture()
    reference = roadmap["architecture_packets"][claim["slice_id"]]
    packet_path = reference["packet_path"]
    if case == "packet_digest":
        packet = validator._thaw_json(authority.get(packet_path))
        packet["threat_model"] = "different frozen packet"
        documents = dict(authority.documents)
        documents[packet_path] = validator._freeze_json(packet)
        authority = validator.AuthoritySnapshotBundle(
            documents=MappingProxyType(documents),
            blob_ids=authority.blob_ids,
            paths=authority.paths,
            raw_bytes=authority.raw_bytes,
        )
    elif case == "packet_evidence_alias":
        reference["packet_path"] = claim["candidate_evidence_path"]
        reference["packet_blob"] = claim["read_only_pins"][
            claim["candidate_evidence_path"]
        ]
    else:
        reference["allowed_paths"] = list(reversed(claim["allowed_paths"]))

    errors = _validate_claim_origin_fixture(
        claim, {}, authority, roadmap, monkeypatch
    )

    assert any("claim transfer is not exact, terminal" in error for error in errors)


def test_materialized_repo_genesis_fixture_uses_no_productive_reads(
    monkeypatch,
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("fixture construction accessed productive authority")

    monkeypatch.setattr(validator, "_load", forbidden)
    monkeypatch.setattr(validator, "_load_auxiliary", forbidden)
    monkeypatch.setattr(validator, "_load_json_snapshot", forbidden)
    monkeypatch.setattr(validator, "_path_blob_id", forbidden)
    monkeypatch.setattr(subprocess, "check_output", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    claim, authority, roadmap = _materialized_repo_genesis_fixture()

    assert _validate_claim_origin_fixture(
        claim,
        {},
        authority,
        roadmap,
        monkeypatch,
        materialized_git_blob=lambda *_args: True,
    ) == []
    active = authority.get(validator.OPEN_WORK_PATH)["current_position"][
        "external_lane_claims"
    ]
    assert [item["claim_id"] for item in active] == [claim["claim_id"]]


def test_target_fixture_builders_are_explicit_and_zero_read(monkeypatch) -> None:
    """Guard the four R2 fixture worlds before any synthetic claim append."""

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("target fixture builder accessed productive authority")

    monkeypatch.setattr(validator, "_load_json_document", forbidden)
    monkeypatch.setattr(validator, "_load_auxiliary", forbidden)
    monkeypatch.setattr(validator, "_path_blob_id", forbidden)
    monkeypatch.setattr(subprocess, "check_output", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    documents, materialized = _rolling_projection_fixture_documents()
    factory = _current_authority_factory()
    data = {
        "external_lane_claims": [{"run_id": "productive-claim"}],
        "long_run_state": {
            "revision": 17,
            "active_claims": ["productive-claim"],
            "agent_leases": [{"claim_id": "productive-claim"}],
        },
    }
    first, second = factory.parallel_running_v2(
        data,
        [("fixture-uda", ["src/uda.py"]), ("fixture-fca", ["src/fca.py"])],
    )

    assert set(documents) == {
        factory.evidence_path, validator.GUIDANCE_PATH, validator.ROUND6_PATH
    }
    assert len(materialized) == 3
    assert [item["run_id"] for item in data["external_lane_claims"]] == [
        "fixture-uda-terminal-source", "fixture-uda",
        "fixture-fca-terminal-source", "fixture-fca",
    ]
    assert data["long_run_state"]["active_claims"] == [first["run_id"], second["run_id"]]


def test_hostile_matrix_current_running_fixture_uses_no_productive_reads(
    monkeypatch,
) -> None:
    """Prove the hostile-matrix primary world is literal and discovery-free."""

    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("current-running fixture accessed productive authority")

    monkeypatch.setattr(validator, "_load_json_document", forbidden)
    monkeypatch.setattr(validator, "_load_auxiliary", forbidden)
    monkeypatch.setattr(validator, "_path_blob_id", forbidden)
    monkeypatch.setattr(subprocess, "check_output", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(Path, "read_bytes", forbidden)
    run_id = "fixture-current-authority-running"
    data = _current_running_fixture(_current_authority_factory(), run_id)

    assert [item["run_id"] for item in data["external_lane_claims"]] == [
        f"{run_id}-terminal-source",
        run_id,
    ]
    assert data["long_run_state"]["active_claims"] == [run_id]
    assert [
        lease["claim_id"] for lease in data["long_run_state"]["agent_leases"]
    ] == [run_id]


def _legacy_authority_with(
    authority: validator.AuthoritySnapshotBundle,
    *,
    source: dict[str, Any] | None = None,
    writer_blobs: dict[str, str] | None = None,
) -> validator.AuthoritySnapshotBundle:
    documents = dict(authority.documents)
    if source is not None:
        documents[validator.OPEN_WORK_PATH] = validator._freeze_json(
            {"current_position": {"external_lane_claims": [source]}}
        )
    blob_ids = dict(authority.blob_ids)
    blob_ids.update(writer_blobs or {})
    return validator.AuthoritySnapshotBundle(
        documents=MappingProxyType(documents),
        blob_ids=MappingProxyType(blob_ids),
        paths=tuple(sorted(blob_ids)),
        raw_bytes=authority.raw_bytes,
        absent_paths=authority.absent_paths,
        absent_paths_readback=authority.absent_paths_readback,
    )


def _freeze_origin_claim(claim: dict[str, Any], candidates: dict[str, str]) -> None:
    node_ids = [
        "tests/test_integration_spine_convergence.py::legacy_baseline_lifecycle_fixture"
    ]
    commands = [
        {"id": item["id"], "tier": item["tier"], "argv": item["argv"]}
        for item in claim["regression_tests"]
    ]
    claim.update(
        {
            "candidate_blobs": dict(candidates),
            "candidate_state": "frozen_for_independent_qa",
            "candidate_command_manifest_digest_sha256": validator._manifest_digest(
                commands
            ),
            "candidate_pytest_node_manifest_digest_sha256": validator._node_manifest_digest(
                node_ids
            ),
            "candidate_pytest_node_ids": node_ids,
        }
    )


def test_claim_origin_compatibility_accepts_explicit_legacy_and_new_product_worlds(
    monkeypatch,
) -> None:
    def forbidden(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("productive discovery")

    monkeypatch.setattr(validator, "_load_json_snapshot", forbidden)
    monkeypatch.setattr(validator, "_path_blob_id", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    for mode in (
        validator.LEGACY_TERMINAL_UPGRADE_MODE,
        validator.NEW_PRODUCT_PATHS_MODE,
    ):
        claim, sources, authority, roadmap = _claim_origin_fixture(mode)
        assert _validate_claim_origin_fixture(
            claim, sources, authority, roadmap, monkeypatch
        ) == []


def test_legacy_terminal_upgrade_registration_baseline_is_valid_before_edit(
    monkeypatch,
) -> None:
    claim, sources, authority, roadmap = _claim_origin_fixture(
        validator.LEGACY_TERMINAL_UPGRADE_MODE
    )
    source = next(iter(sources.values()))

    assert source["candidate_writer_blobs"] == claim["baseline_blobs"]
    assert {
        path: authority.blob_ids[path] for path in claim["allowed_paths"]
    } == claim["baseline_blobs"]
    assert _validate_claim_origin_fixture(
        claim, sources, authority, roadmap, monkeypatch
    ) == []


def test_legacy_terminal_upgrade_allows_legitimate_running_writer_edit(
    monkeypatch,
) -> None:
    claim, sources, authority, roadmap = _claim_origin_fixture(
        validator.LEGACY_TERMINAL_UPGRADE_MODE
    )
    edited_blobs = {
        path: ("d" if index == 0 else "e") * 40
        for index, path in enumerate(claim["allowed_paths"])
    }
    edited_authority = _legacy_authority_with(
        authority, writer_blobs=edited_blobs
    )
    materialized: list[str] = []

    def materialized_baseline(_repo_root: Path, blob_id: str) -> bool:
        materialized.append(blob_id)
        return blob_id in set(claim["baseline_blobs"].values())

    assert _validate_claim_origin_fixture(
        claim,
        sources,
        edited_authority,
        roadmap,
        monkeypatch,
        materialized_git_blob=materialized_baseline,
    ) == []
    assert set(materialized) == set(claim["baseline_blobs"].values())
    assert set(edited_blobs.values()).isdisjoint(materialized)


@pytest.mark.parametrize(
    ("case", "expected"),
    (
        ("missing", "claim transfer is not exact, terminal and revision-bound"),
        ("extra", "claim transfer is not exact, terminal and revision-bound"),
        ("mismatch", "claim transfer is not exact, terminal and revision-bound"),
        ("invalid", "claim transfer is not exact, terminal and revision-bound"),
        ("unmaterialized", "baseline blob is not materialized"),
        ("absent", "claim transfer is not exact, terminal and revision-bound"),
        ("readback", "baseline readback evidence is invalid"),
        ("source_mirror", "claim transfer is not exact, terminal and revision-bound"),
    ),
)
def test_legacy_terminal_upgrade_rejects_invalid_acquisition_baseline_matrix(
    monkeypatch,
    case,
    expected,
) -> None:
    claim, sources, authority, roadmap = _claim_origin_fixture(
        validator.LEGACY_TERMINAL_UPGRADE_MODE
    )
    source = next(iter(sources.values()))
    first_path = claim["allowed_paths"][0]
    materialized = None
    remirror = False
    if case == "missing":
        source["candidate_writer_blobs"].pop(first_path)
        remirror = True
    elif case == "extra":
        source["candidate_writer_blobs"]["src/extra.py"] = "f" * 40
        remirror = True
    elif case == "mismatch":
        source["candidate_writer_blobs"][first_path] = "d" * 40
        remirror = True
    elif case == "invalid":
        source["candidate_writer_blobs"][first_path] = "invalid"
        remirror = True
    elif case == "unmaterialized":
        materialized = lambda _root, _blob_id: False
    elif case == "absent":
        source["candidate_writer_blobs"][first_path] = "absent"
        remirror = True
    elif case == "readback":
        claim["baseline_readback"] = "unchecked"
    elif case == "source_mirror":
        source["owner"] = "different source owner"
    if remirror:
        authority = _legacy_authority_with(authority, source=source)

    errors = _validate_claim_origin_fixture(
        claim,
        sources,
        authority,
        roadmap,
        monkeypatch,
        materialized_git_blob=materialized,
    )

    assert any(expected in error for error in errors)


def test_legacy_terminal_upgrade_rejects_unfrozen_candidate_publication(
    monkeypatch,
) -> None:
    claim, sources, authority, roadmap = _claim_origin_fixture(
        validator.LEGACY_TERMINAL_UPGRADE_MODE
    )
    claim["candidate_blobs"] = {
        path: "d" * 40 for path in claim["allowed_paths"]
    }

    errors = _validate_claim_origin_fixture(
        claim, sources, authority, roadmap, monkeypatch
    )

    assert any("unfrozen candidate must not publish blobs" in error for error in errors)


def test_legacy_terminal_upgrade_frozen_candidate_binds_current_edited_bytes(
    monkeypatch,
) -> None:
    claim, sources, authority, roadmap = _claim_origin_fixture(
        validator.LEGACY_TERMINAL_UPGRADE_MODE
    )
    current = {
        path: ("d" if index == 0 else "e") * 40
        for index, path in enumerate(claim["allowed_paths"])
    }
    _freeze_origin_claim(claim, current)
    authority = _legacy_authority_with(authority, writer_blobs=current)

    assert _validate_claim_origin_fixture(
        claim, sources, authority, roadmap, monkeypatch
    ) == []


def test_legacy_terminal_upgrade_frozen_candidate_rejects_old_baseline(
    monkeypatch,
) -> None:
    claim, sources, authority, roadmap = _claim_origin_fixture(
        validator.LEGACY_TERMINAL_UPGRADE_MODE
    )
    current = {
        path: ("d" if index == 0 else "e") * 40
        for index, path in enumerate(claim["allowed_paths"])
    }
    _freeze_origin_claim(claim, claim["baseline_blobs"])
    authority = _legacy_authority_with(authority, writer_blobs=current)

    errors = _validate_claim_origin_fixture(
        claim, sources, authority, roadmap, monkeypatch
    )

    assert any(
        "frozen candidate does not bind current writer bytes bijectively" in error
        for error in errors
    )


def test_legacy_terminal_upgrade_rejects_post_freeze_writer_drift(
    monkeypatch,
) -> None:
    claim, sources, authority, roadmap = _claim_origin_fixture(
        validator.LEGACY_TERMINAL_UPGRADE_MODE
    )
    frozen = {
        path: ("d" if index == 0 else "e") * 40
        for index, path in enumerate(claim["allowed_paths"])
    }
    drifted = {path: "f" * 40 for path in claim["allowed_paths"]}
    _freeze_origin_claim(claim, frozen)
    authority = _legacy_authority_with(authority, writer_blobs=drifted)

    errors = _validate_claim_origin_fixture(
        claim, sources, authority, roadmap, monkeypatch
    )

    assert any(
        "frozen candidate does not bind current writer bytes bijectively" in error
        for error in errors
    )


@pytest.mark.parametrize(
    ("mode", "mutate"),
    (
        (
            validator.LEGACY_TERMINAL_UPGRADE_MODE,
            lambda claim, sources, authority, roadmap: claim["claim_transfer"].__setitem__(
                "unexpected", True
            ),
        ),
        (
            validator.LEGACY_TERMINAL_UPGRADE_MODE,
            lambda claim, sources, authority, roadmap: claim["claim_transfer"].__setitem__(
                "source_registry", "open_work.external_lane_claims"
            ),
        ),
        (
            validator.LEGACY_TERMINAL_UPGRADE_MODE,
            lambda claim, sources, authority, roadmap: sources[
                "fixture-origin-terminal-source"
            ].__setitem__("authority_revision", {"registered_round7_revision": 1}),
        ),
        (
            validator.NEW_PRODUCT_PATHS_MODE,
            lambda claim, sources, authority, roadmap: claim["claim_transfer"].__setitem__(
                "from_claim_id", "fabricated"
            ),
        ),
        (
            validator.NEW_PRODUCT_PATHS_MODE,
            lambda claim, sources, authority, roadmap: claim["claim_transfer"].__setitem__(
                "new_product_writer_paths", ["docs/plans/not-product.json"]
            ),
        ),
        (
            validator.NEW_PRODUCT_PATHS_MODE,
            lambda claim, sources, authority, roadmap: roadmap["architecture_packets"][
                claim["slice_id"]
            ].__setitem__("state", "draft"),
        ),
    ),
)
def test_claim_origin_compatibility_rejects_hostile_variants(
    monkeypatch, mode, mutate
) -> None:
    claim, sources, authority, roadmap = _claim_origin_fixture(mode)
    mutate(claim, sources, authority, roadmap)
    errors = _validate_claim_origin_fixture(
        claim, sources, authority, roadmap, monkeypatch
    )
    assert any(
        "claim transfer is not exact, terminal and revision-bound" in error
        or "new-product absent baseline is not explicit and captured" in error
        for error in errors
    )


def test_claim_origin_architecture_packet_is_digest_and_semantic_binding_closed() -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    packet = data["architecture_packets"]["R7-claim-origin-compatibility"]
    matches = [
        item
        for item in data["external_lane_claims"]
        if item.get("slice_id") == packet["slice_id"]
        and item.get("architecture_revision") == packet["architecture_revision"]
    ]
    assert len(matches) == 1
    claim = matches[0]
    state_pair = (claim.get("state"), claim.get("candidate_state"))
    if claim.get("state") == "running":
        assert state_pair in {
            ("running", "not_frozen"),
            ("running", "frozen_for_independent_qa"),
        }
        assert claim["run_id"] in data["long_run_state"]["active_claims"]
        assert sum(
            lease.get("claim_id") == claim["run_id"]
            for lease in data["long_run_state"]["agent_leases"]
        ) == 1
    else:
        assert state_pair in {
            ("released_accepted", "frozen_for_independent_qa"),
            ("released_blocked", "blocked_before_freeze"),
            ("released_blocked", "blocked_after_freeze"),
            ("released_pending_post_release_qa", "frozen_for_independent_qa"),
        }
        assert type(claim.get("released_at")) is str
        assert claim["run_id"] not in data["long_run_state"]["active_claims"]
        assert all(
            lease.get("claim_id") != claim["run_id"]
            for lease in data["long_run_state"]["agent_leases"]
        )
    assert validator._claim_origin_packet_binding_is_exact(claim, data)

    wrong_revision = json.loads(json.dumps(claim))
    wrong_revision["architecture_revision"] = "wrong-revision"
    wrong_slice = json.loads(json.dumps(claim))
    wrong_slice["slice_id"] = "wrong-slice"
    identity_candidates = [wrong_revision, wrong_slice, claim]
    assert [
        item
        for item in identity_candidates
        if item.get("slice_id") == packet["slice_id"]
        and item.get("architecture_revision") == packet["architecture_revision"]
    ] == [claim]

    for recompute, mutate in (
        (False, lambda packet: packet.__setitem__("architecture_digest_sha256", "sha256:" + "0" * 64)),
        (True, lambda packet: packet.__setitem__("unknown", True)),
        (True, lambda packet: packet.__setitem__("allowed_paths", ["src/semantic-drift.py"])),
        (True, lambda packet: packet.__setitem__("baseline_blobs", {})),
        (True, lambda packet: packet.__setitem__("origin_mode", "legacy_terminal_upgrade")),
        (True, lambda packet: packet.__setitem__("source_claim_id", "other-source")),
        (True, lambda packet: packet.__setitem__("new_product_writer_paths", ["src/new.py"])),
        (True, lambda packet: packet.__setitem__("handoff_id", "different-handoff")),
        (True, lambda packet: packet.__setitem__("state", "draft")),
        (True, lambda packet: packet.__setitem__("architect", "Terra fixture")),
    ):
        drifted = json.loads(json.dumps(data))
        packet = drifted["architecture_packets"][claim["slice_id"]]
        mutate(packet)
        if recompute:
            packet.pop("architecture_digest_sha256", None)
            packet["architecture_digest_sha256"] = validator._manifest_digest(packet)
        assert validator._claim_origin_packet_binding_is_exact(claim, drifted) is False


def test_new_product_origin_requires_exact_absence_readback_without_changing_existing_modes(
    monkeypatch,
) -> None:
    claim, sources, authority, roadmap = _claim_origin_fixture(
        validator.NEW_PRODUCT_PATHS_MODE
    )
    assert _validate_claim_origin_fixture(
        claim, sources, authority, roadmap, monkeypatch
    ) == []
    materialized_readback = json.loads(json.dumps(claim))
    materialized_readback["baseline_readback"] = "materialized_exact_binary_readback_pass"
    errors = _validate_claim_origin_fixture(
        materialized_readback, sources, authority, roadmap, monkeypatch
    )
    assert any("new-product baseline readback is not exact" in error for error in errors)
    missing_readback = validator.AuthoritySnapshotBundle(
        documents=authority.documents,
        blob_ids=authority.blob_ids,
        paths=authority.paths,
        absent_paths=authority.absent_paths,
        absent_paths_readback="not_applicable",
    )
    errors = _validate_claim_origin_fixture(
        claim, sources, missing_readback, roadmap, monkeypatch
    )
    assert any("new-product absent baseline is not explicit and captured" in error for error in errors)

    overlap = {
        "run_id": "other-terminal-claim",
        "state": "released_blocked",
        "allowed_paths": list(claim["allowed_paths"]),
    }
    errors = _validate_claim_origin_fixture(
        claim, {overlap["run_id"]: overlap}, authority, roadmap, monkeypatch
    )
    assert any("new-product absent baseline is not explicit and captured" in error for error in errors)
    open_work_overlap = validator.AuthoritySnapshotBundle(
        documents=MappingProxyType(
            {
                validator.OPEN_WORK_PATH: validator._freeze_json(
                    {"current_position": {"external_lane_claims": [overlap]}}
                )
            }
        ),
        blob_ids=authority.blob_ids,
        paths=authority.paths,
        absent_paths=authority.absent_paths,
        absent_paths_readback=authority.absent_paths_readback,
    )
    errors = _validate_claim_origin_fixture(
        claim, {}, open_work_overlap, roadmap, monkeypatch
    )
    assert any("new-product absent baseline is not explicit and captured" in error for error in errors)

    legacy_claim, legacy_sources, legacy_authority, legacy_roadmap = _claim_origin_fixture(
        validator.LEGACY_TERMINAL_UPGRADE_MODE
    )
    assert _validate_claim_origin_fixture(
        legacy_claim, legacy_sources, legacy_authority, legacy_roadmap, monkeypatch
    ) == []


def test_production_capture_records_absent_non_json_paths_and_rejects_entries(
    monkeypatch, tmp_path
) -> None:
    target = tmp_path / "src" / "absent_product.py"
    target.parent.mkdir()
    claim = {
        "state": "running",
        "allowed_paths": ["src/absent_product.py"],
        "baseline_blobs": {"src/absent_product.py": "absent"},
        "read_only_pins": {},
    }
    snapshot = validator._json_snapshot_from_bytes(b"{}", role="fixture")
    monkeypatch.setattr(
        validator,
        "_load_json_snapshot",
        lambda *_args, **_kwargs: snapshot,
    )
    original_lstat = Path.lstat
    calls: list[Path] = []

    def missing_once(path: Path):
        calls.append(path)
        if path == target:
            raise FileNotFoundError
        raise AssertionError("unexpected lstat")

    monkeypatch.setattr(Path, "lstat", missing_once)
    errors: list[str] = []
    captured = validator._capture_v2_authority_documents(
        [claim], repo_root=tmp_path, errors=errors
    )
    assert errors == []
    assert calls == [target]
    assert captured.absent_paths == ("src/absent_product.py",)
    assert (
        captured.absent_paths_readback
        == validator.CAPTURED_ABSENT_PATHS_READBACK_PASS
    )

    target.write_text("present", encoding="utf-8")
    present_calls: list[Path] = []

    def present_once(path: Path):
        present_calls.append(path)
        if path == target:
            return original_lstat(path)
        raise AssertionError("unexpected lstat")

    monkeypatch.setattr(Path, "lstat", present_once)
    errors = []
    present = validator._capture_v2_authority_documents(
        [claim], repo_root=tmp_path, errors=errors
    )
    assert present_calls == [target]
    assert present.absent_paths == ()
    assert present.absent_paths_readback == "not_applicable"
    assert errors == ["src/absent_product.py declared absent path is materialized"]

    target.unlink()
    target.mkdir()
    directory_calls: list[Path] = []

    def directory_once(path: Path):
        directory_calls.append(path)
        if path == target:
            return original_lstat(path)
        raise AssertionError("unexpected lstat")

    monkeypatch.setattr(Path, "lstat", directory_once)
    errors = []
    directory = validator._capture_v2_authority_documents(
        [claim], repo_root=tmp_path, errors=errors
    )
    assert directory_calls == [target]
    assert directory.absent_paths == ()
    assert errors == ["src/absent_product.py declared absent path is materialized"]

    symlink_calls: list[Path] = []

    def symlink_success_once(path: Path):
        symlink_calls.append(path)
        if path == target:
            return object()
        raise AssertionError("unexpected lstat")

    monkeypatch.setattr(Path, "lstat", symlink_success_once)
    errors = []
    symlink_success = validator._capture_v2_authority_documents(
        [claim], repo_root=tmp_path, errors=errors
    )
    assert symlink_calls == [target]
    assert symlink_success.absent_paths == ()
    assert errors == ["src/absent_product.py declared absent path is materialized"]

    inaccessible_calls: list[Path] = []

    def inaccessible_once(path: Path):
        inaccessible_calls.append(path)
        if path == target:
            raise PermissionError
        raise AssertionError("unexpected lstat")

    monkeypatch.setattr(Path, "lstat", inaccessible_once)
    errors = []
    inaccessible = validator._capture_v2_authority_documents(
        [claim], repo_root=tmp_path, errors=errors
    )
    assert inaccessible_calls == [target]
    assert inaccessible.absent_paths == ()
    assert inaccessible.absent_paths_readback == "not_applicable"
    assert errors == ["src/absent_product.py declared absent path is unavailable"]

    other_error_calls: list[Path] = []

    def other_error_once(path: Path):
        other_error_calls.append(path)
        if path == target:
            raise OSError("fixture")
        raise AssertionError("unexpected lstat")

    monkeypatch.setattr(Path, "lstat", other_error_once)
    errors = []
    other_error = validator._capture_v2_authority_documents(
        [claim], repo_root=tmp_path, errors=errors
    )
    assert other_error_calls == [target]
    assert other_error.absent_paths == ()
    assert errors == ["src/absent_product.py declared absent path is unavailable"]


def test_new_product_overlap_requires_exact_running_mirror_and_fails_closed() -> None:
    claim, _sources, authority, _roadmap = _claim_origin_fixture(
        validator.NEW_PRODUCT_PATHS_MODE
    )
    claims = {claim["run_id"]: claim}
    assert validator._new_product_paths_are_unclaimed(
        tuple(claim["allowed_paths"]),
        run_id=claim["run_id"],
        claims_by_run_id=claims,
        authority=authority,
    )

    assert not validator._new_product_paths_are_unclaimed(
        tuple(claim["allowed_paths"]),
        run_id=claim["run_id"],
        claims_by_run_id={},
        authority=authority,
    )

    def with_open_work(entries: list[Any]) -> validator.AuthoritySnapshotBundle:
        return validator.AuthoritySnapshotBundle(
            documents=MappingProxyType(
                {
                    validator.OPEN_WORK_PATH: validator._freeze_json(
                        {"current_position": {"external_lane_claims": entries}}
                    )
                }
            ),
            blob_ids=authority.blob_ids,
            paths=authority.paths,
            absent_paths=authority.absent_paths,
            absent_paths_readback=authority.absent_paths_readback,
        )

    drifted_mirror = json.loads(json.dumps(claim))
    drifted_mirror["state"] = "released_blocked"
    assert not validator._new_product_paths_are_unclaimed(
        tuple(claim["allowed_paths"]),
        run_id=claim["run_id"],
        claims_by_run_id=claims,
        authority=with_open_work([drifted_mirror]),
    )

    for foreign_state in ("released_blocked", "foreign_state"):
        foreign = {
            "run_id": f"foreign-{foreign_state}",
            "state": foreign_state,
            "allowed_paths": list(claim["allowed_paths"]),
        }
        assert not validator._new_product_paths_are_unclaimed(
            tuple(claim["allowed_paths"]),
            run_id=claim["run_id"],
            claims_by_run_id={**claims, foreign["run_id"]: foreign},
            authority=with_open_work([claim, foreign]),
        )

    malformed = {"run_id": "malformed", "state": "released_blocked", "allowed_paths": ["tests/z.py", "src/a.py"]}
    assert not validator._new_product_paths_are_unclaimed(
        tuple(claim["allowed_paths"]),
        run_id=claim["run_id"],
        claims_by_run_id=claims,
        authority=with_open_work([claim, malformed]),
    )


def test_synthetic_absence_requires_explicit_bundle_while_production_lstats_once(
    monkeypatch, tmp_path
) -> None:
    relative_path = "src/synthetic_absent.py"
    target = tmp_path / relative_path
    claim = {
        "state": "running",
        "allowed_paths": [relative_path],
        "baseline_blobs": {relative_path: "absent"},
        "read_only_pins": {},
    }
    lstat_calls: list[Path] = []

    def forbidden_lstat(path: Path):
        lstat_calls.append(path)
        raise AssertionError("synthetic lstat")

    monkeypatch.setattr(validator, "_load", lambda _path: {})
    monkeypatch.setattr(validator, "_load_auxiliary", lambda _path: {})
    monkeypatch.setattr(validator, "_path_blob_id", lambda _path: "a" * 40)
    monkeypatch.setattr(Path, "lstat", forbidden_lstat)
    errors: list[str] = []
    synthetic = validator._capture_v2_authority_documents(
        [claim], repo_root=tmp_path, errors=errors
    )
    assert lstat_calls == []
    assert synthetic.absent_paths == ()
    assert synthetic.absent_paths_readback == "not_applicable"
    assert errors == [f"{relative_path} synthetic absence authority is unavailable"]

    explicit = validator.AuthoritySnapshotBundle(
        documents=MappingProxyType({}),
        blob_ids=MappingProxyType({}),
        paths=(),
        absent_paths=(relative_path,),
        absent_paths_readback=validator.CAPTURED_ABSENT_PATHS_READBACK_PASS,
    )
    assert explicit.absent_paths == (relative_path,)

    monkeypatch.setattr(validator, "_load", validator._DEFAULT_LOAD)
    monkeypatch.setattr(validator, "_load_auxiliary", validator._DEFAULT_LOAD_AUXILIARY)
    snapshot = validator._json_snapshot_from_bytes(b"{}", role="fixture")
    monkeypatch.setattr(validator, "_load_json_snapshot", lambda *_args, **_kwargs: snapshot)
    productive_calls: list[Path] = []

    def absent_once(path: Path):
        productive_calls.append(path)
        if path == target:
            raise FileNotFoundError
        raise AssertionError("unexpected lstat")

    monkeypatch.setattr(Path, "lstat", absent_once)
    errors = []
    productive = validator._capture_v2_authority_documents(
        [claim], repo_root=tmp_path, errors=errors
    )
    assert errors == []
    assert productive_calls == [target]
    assert productive.absent_paths == (relative_path,)
    assert productive.absent_paths_readback == validator.CAPTURED_ABSENT_PATHS_READBACK_PASS


def _new_product_capture_claim(
    paths: list[str], *, frozen: bool, payloads: Mapping[str, bytes] | None = None
) -> dict[str, Any]:
    candidates = {
        path: validator._git_blob_id((payloads or {})[path])
        for path in paths
    } if frozen else {}
    return {
        "state": "running",
        "candidate_state": (
            "frozen_for_independent_qa" if frozen else "not_frozen"
        ),
        "allowed_paths": list(paths),
        "baseline_blobs": {path: "absent" for path in paths},
        "baseline_readback": validator.CAPTURED_ABSENT_PATHS_READBACK_PASS,
        "candidate_blobs": candidates,
        "new_evidence_writer_paths": [],
        "read_only_pins": {},
        "claim_transfer": {
            "origin_mode": validator.NEW_PRODUCT_PATHS_MODE,
            "source_registry": None,
            "from_claim_id": None,
            "source_released_at": None,
            "transferred_at": "2026-08-08T20:00:00+02:00",
            "gap_preserved_no_writer": True,
            "architecture_handoff": "new_product_capture_fixture",
            "new_product_writer_paths": list(paths),
        },
    }


def _capture_with_json_authority_stub(
    claim: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[validator.AuthoritySnapshotBundle, list[str]]:
    snapshot = validator._json_snapshot_from_bytes(b"{}", role="fixture")
    monkeypatch.setattr(
        validator, "_load_json_snapshot", lambda *_args, **_kwargs: snapshot
    )
    errors: list[str] = []
    authority = validator._capture_v2_authority_documents(
        [claim], repo_root=tmp_path, errors=errors
    )
    return authority, errors


def test_new_product_running_not_frozen_lstats_each_absent_path_once(
    monkeypatch, tmp_path
) -> None:
    paths = ["src/new_a.py", "tests/test_new_a.py"]
    targets = [tmp_path / path for path in paths]
    claim = _new_product_capture_claim(paths, frozen=False)
    calls: list[Path] = []

    def absent_once(path: Path):
        calls.append(path)
        if path in targets:
            raise FileNotFoundError
        raise AssertionError("unexpected lstat")

    monkeypatch.setattr(Path, "lstat", absent_once)
    authority, errors = _capture_with_json_authority_stub(
        claim, tmp_path, monkeypatch
    )
    assert errors == []
    assert calls == targets
    assert authority.absent_paths == tuple(paths)
    assert authority.absent_paths_readback == validator.CAPTURED_ABSENT_PATHS_READBACK_PASS
    assert all(path not in authority.blob_ids for path in paths)


def test_new_product_running_frozen_captures_each_current_writer_once_with_absent_baseline(
    monkeypatch, tmp_path
) -> None:
    payloads = {
        "src/new_a.py": b"print('a')\n",
        "tests/test_new_a.py": b"def test_a(): pass\n",
    }
    for path, payload in payloads.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
    claim = _new_product_capture_claim(sorted(payloads), frozen=True, payloads=payloads)
    original_capture = validator._capture_file_bytes
    calls: list[Path] = []

    def capture_once(path: Path):
        calls.append(path)
        return original_capture(path)

    monkeypatch.setattr(validator, "_capture_file_bytes", capture_once)
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda _path: (_ for _ in ()).throw(AssertionError("frozen lstat")),
    )
    authority, errors = _capture_with_json_authority_stub(
        claim, tmp_path, monkeypatch
    )
    assert errors == []
    assert calls == [tmp_path / path for path in sorted(payloads)]
    assert authority.absent_paths == ()
    assert authority.absent_paths_readback == "not_applicable"
    assert {
        path: authority.raw_bytes[path] for path in sorted(payloads)
    } == payloads
    assert {
        path: authority.blob_ids[path] for path in sorted(payloads)
    } == claim["candidate_blobs"]


def test_new_product_frozen_rejects_missing_unreadable_oversized_and_candidate_evidence_drift(
    monkeypatch, tmp_path
) -> None:
    path = "src/frozen_product.py"
    payloads = {path: b"candidate\n"}
    claim = _new_product_capture_claim([path], frozen=True, payloads=payloads)
    for code in ("read_failed", "limit_exceeded"):
        monkeypatch.setattr(
            validator,
            "_capture_file_bytes",
            lambda _path, code=code: (_ for _ in ()).throw(
                validator._JsonDocumentError(code)
            ),
        )
        _authority, errors = _capture_with_json_authority_stub(
            claim, tmp_path, monkeypatch
        )
        assert errors == [f"{path} is unavailable: {code}"]

    product_claim, _sources, old_authority, roadmap = _claim_origin_fixture(
        validator.NEW_PRODUCT_PATHS_MODE
    )
    current_payloads = {
        product_path: f"{product_path}\n".encode("utf-8")
        for product_path in product_claim["allowed_paths"]
    }
    candidates = {
        product_path: validator._git_blob_id(payload)
        for product_path, payload in current_payloads.items()
    }
    _freeze_origin_claim(product_claim, candidates)
    current_documents = dict(old_authority.documents)
    current_documents[validator.OPEN_WORK_PATH] = validator._freeze_json(
        {"current_position": {"external_lane_claims": [product_claim]}}
    )
    current_authority = validator.AuthoritySnapshotBundle(
        documents=MappingProxyType(current_documents),
        blob_ids=MappingProxyType({**dict(old_authority.blob_ids), **candidates}),
        paths=tuple(sorted({*old_authority.paths, *candidates})),
        raw_bytes=MappingProxyType(current_payloads),
    )
    assert _validate_claim_origin_fixture(
        product_claim,
        {product_claim["run_id"]: product_claim},
        current_authority,
        roadmap,
        monkeypatch,
    ) == []
    drifted = validator.AuthoritySnapshotBundle(
        documents=current_authority.documents,
        blob_ids=MappingProxyType(
            {**dict(current_authority.blob_ids), product_claim["allowed_paths"][0]: "0" * 40}
        ),
        paths=current_authority.paths,
        raw_bytes=current_authority.raw_bytes,
    )
    errors = _validate_claim_origin_fixture(
        product_claim,
        {product_claim["run_id"]: product_claim},
        drifted,
        roadmap,
        monkeypatch,
    )
    assert any("new-product absent baseline is not explicit and captured" in error for error in errors)
    assert any("frozen candidate does not bind current writer bytes" in error for error in errors)


def test_new_product_malformed_or_overlapping_capture_modes_fail_closed(
    monkeypatch, tmp_path
) -> None:
    path = "src/overlap.py"
    payloads = {path: b"overlap\n"}
    frozen = _new_product_capture_claim([path], frozen=True, payloads=payloads)
    unfrozen = _new_product_capture_claim([path], frozen=False)
    snapshot = validator._json_snapshot_from_bytes(b"{}", role="fixture")
    monkeypatch.setattr(
        validator, "_load_json_snapshot", lambda *_args, **_kwargs: snapshot
    )
    monkeypatch.setattr(
        validator,
        "_capture_file_bytes",
        lambda _path: (_ for _ in ()).throw(AssertionError("overlap capture")),
    )
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda _path: (_ for _ in ()).throw(AssertionError("overlap lstat")),
    )
    errors: list[str] = []
    authority = validator._capture_v2_authority_documents(
        [frozen, unfrozen], repo_root=tmp_path, errors=errors
    )
    assert errors == ["authority capture path belongs to overlapping modes"]
    assert path not in authority.blob_ids
    assert authority.absent_paths == ()

    malformed = json.loads(json.dumps(frozen))
    malformed["claim_transfer"]["unexpected"] = True
    monkeypatch.setattr(Path, "lstat", lambda _path: object())
    errors = []
    validator._capture_v2_authority_documents(
        [malformed], repo_root=tmp_path, errors=errors
    )
    assert errors == [f"{path} declared absent path is materialized"]


def test_classic_v2_legacy_and_new_evidence_capture_semantics_are_unchanged(
    monkeypatch, tmp_path
) -> None:
    factory = _current_authority_factory()
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    classic = factory.running_v2(
        data,
        "classic-v2-capture-fixture",
        ["scripts/validate_parallel_roadmap_execution.py"],
    )
    assert validator._claim_origin_variant(classic) == "v2"
    assert set(classic["claim_transfer"]) == validator.ABC_V2_TRANSFER_FIELDS

    legacy, sources, authority, roadmap = _claim_origin_fixture(
        validator.LEGACY_TERMINAL_UPGRADE_MODE
    )
    assert _validate_claim_origin_fixture(
        legacy, sources, authority, roadmap, monkeypatch
    ) == []

    evidence_path = "docs/plans/evidence/new-capture.json"
    claim = {
        "state": "running",
        "candidate_state": "not_frozen",
        "allowed_paths": [evidence_path],
        "baseline_blobs": {evidence_path: "absent"},
        "new_evidence_writer_paths": [evidence_path],
        "read_only_pins": {},
    }
    target = tmp_path / evidence_path
    calls: list[Path] = []

    def absent_once(path: Path):
        calls.append(path)
        if path == target:
            raise FileNotFoundError
        raise AssertionError("unexpected lstat")

    monkeypatch.setattr(Path, "lstat", absent_once)
    captured, errors = _capture_with_json_authority_stub(claim, tmp_path, monkeypatch)
    assert errors == []
    assert calls == [target]
    assert captured.absent_paths == (evidence_path,)


def test_r6_exact_slice_selects_only_its_own_architecture_packet() -> None:
    roadmap = json.loads(ROADMAP.read_text(encoding="utf-8"))
    historical = next(
        item
        for item in roadmap["external_lane_claims"]
        if item.get("slice_id") == validator.R6_VALIDATOR_SLICE
    )
    direct = next(
        item
        for item in roadmap["external_lane_claims"]
        if item.get("slice_id") == validator.R6_DIRECT_VALIDATOR_SLICE
    )
    assert validator._r6_architecture_binding(historical, roadmap) == (
        historical["architecture_revision"], historical["architecture_digest_sha256"]
    )
    assert validator._r6_architecture_binding(direct, roadmap) == (
        direct["architecture_revision"], direct["architecture_digest_sha256"]
    )
    alias = dict(direct)
    alias["slice_id"] = validator.R6_VALIDATOR_SLICE + "-alias"
    assert validator._r6_is_validator_claim(alias) is False
    assert validator._r6_architecture_binding(alias, roadmap) is None


def _run(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VALIDATOR), "--roadmap", str(path), "--format", "json"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def _r5_frozen_fixture() -> tuple[dict[str, Any], dict[str, Any], validator.AuthoritySnapshotBundle]:
    """Build a receipt candidate without inheriting current active authority."""

    roadmap = json.loads(ROADMAP.read_text(encoding="utf-8"))
    claim = next(
        item
        for item in roadmap["external_lane_claims"]
        if item.get("slice_id") == validator.R5_VALIDATOR_SLICE
    )
    # The checked-in R5 record is now receiptless terminal history.  This
    # helper deliberately reconstructs its old pre-freeze unit fixture rather
    # than letting terminal acceptance prose leak into the legacy R5 core.
    claim.pop("acceptance_evidence", None)
    candidates = {
        path: validator._path_blob_id(REPO_ROOT / path)
        for path in claim["allowed_paths"]
    }
    assert all(candidates.values())
    evidence_path = next(
        path for path in claim["read_only_pins"] if path.startswith("docs/plans/evidence/")
    )
    dependency_pins = {
        path: blob
        for path, blob in claim["read_only_pins"].items()
        if path != evidence_path
    }
    packet = roadmap["architecture_packets"][validator.R5_ARCHITECTURE_PACKET]
    node_ids = ["tests/test_integration_spine_convergence.py::r5_freeze_fixture"]
    claim.update(
        {
            "candidate_blobs": candidates,
            "candidate_state": "frozen_for_independent_qa",
            "candidate_evidence_path": evidence_path,
            "candidate_evidence_manifest_blob": claim["read_only_pins"][evidence_path],
            "candidate_frozen_at": "2026-08-03T21:30:00+02:00",
            "candidate_set_digest_sha256": validator._candidate_set_digest(candidates),
            "candidate_command_manifest_digest_sha256": validator._manifest_digest(
                [
                    {"id": item["id"], "tier": item["tier"], "argv": item["argv"]}
                    for item in claim["regression_tests"]
                ]
            ),
            "candidate_pytest_node_manifest_digest_sha256": validator._node_manifest_digest(node_ids),
            "candidate_pytest_node_ids": node_ids,
            "architecture_revision": packet["architecture_revision"],
            "architecture_digest_sha256": packet["digest_sha256"],
            "freeze_receipt_blob": "a" * 40,
        }
    )
    evidence = {
        "candidate_blobs": candidates,
        "read_only_pins": dependency_pins,
        "inventory_receipt": {"mode": "fixture"},
    }
    core = validator._r5_immutable_claim_core(claim)
    assert core is not None
    commands = validator._r5_structured_command_manifest(claim)
    assert commands is not None
    receipt = {
        "schema_version": 1,
        "kind": validator.R5_FREEZE_RECEIPT_KIND,
        "claim_id": claim["claim_id"],
        "architecture_revision": claim["architecture_revision"],
        "architecture_digest_sha256": claim["architecture_digest_sha256"],
        "authority_revision": claim["authority_revision"],
        "immutable_claim_core": core,
        "immutable_claim_core_digest_sha256": validator._manifest_digest(core),
        "candidate_blobs": candidates,
        "candidate_set_digest_sha256": validator._candidate_set_digest(candidates),
        "candidate_evidence_path": evidence_path,
        "candidate_evidence_manifest_blob": claim["candidate_evidence_manifest_blob"],
        "dependency_pins": dependency_pins,
        "command_manifest": commands,
        "command_manifest_digest_sha256": validator._manifest_digest(commands),
        "pytest_node_ids": node_ids,
        "pytest_node_manifest_digest_sha256": validator._node_manifest_digest(node_ids),
        "inventory_receipt": evidence["inventory_receipt"],
        "correction_ledger": claim["correction_budget"],
    }
    blob_ids = dict(claim["read_only_pins"])
    blob_ids.update(candidates)
    authority = validator.AuthoritySnapshotBundle(
        documents=MappingProxyType({evidence_path: MappingProxyType(evidence)}),
        blob_ids=MappingProxyType(blob_ids),
        paths=tuple(sorted(blob_ids)),
    )
    return claim, receipt, authority


def _with_running_a1(data: dict) -> dict:
    running = data["waves"][0]["slices"][0]
    running["status"] = "running_exact_claim"
    running["execution_claim"]["state"] = "running"
    for key in ("released_at", "release_reason", "blocker", "evidence"):
        running["execution_claim"].pop(key, None)
    data["waves"][0]["status"] = "running"
    run_id = running["execution_claim"]["run_id"]
    data["long_run_state"]["active_claims"].append(run_id)
    data["long_run_state"]["agent_leases"].append(
        {
            "claim_id": run_id,
            "owner": running["execution_claim"]["owner"],
            "expires_at": running["execution_claim"]["lease_expires_at"],
        }
    )
    data["completion"]["active_implementation_claims"] = 1
    return data


def test_round7_integration_spine_is_valid() -> None:
    result = _run(ROADMAP)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["ok"] is True


def test_historical_r7_records_remain_terminal_and_noncurrent() -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    claims = [
        item for item in data["external_lane_claims"]
        if item.get("slice_id") == validator.R7_VALIDATOR_SLICE
    ]
    assert claims
    assert all(
        claim.get("state") == "released_blocked"
        and claim.get("candidate_state") == "blocked_before_freeze"
        for claim in claims
    )


def test_historical_r7_claim_cannot_alias_the_direct_r6_recovery() -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    claim = next(
        item for item in data["external_lane_claims"]
        if item.get("slice_id") == validator.R7_VALIDATOR_SLICE
    )
    assert validator._r6_is_validator_claim(claim) is False
    assert validator._r6_architecture_binding(claim, data) is None


def test_r7_basetemp_preparation_rejects_foreign_claim() -> None:
    assert validator._prepare_claim_basetemp(REPO_ROOT, "foreign-claim", "focused") is None


def test_validator_rejects_claimable_implementation_slice(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    data["waves"][0]["slices"][0]["claimable"] = True
    monkeypatch.setattr(validator, "_load", lambda _path: data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("claimable during authoring" in item for item in errors)


def test_validator_rejects_running_slice_without_active_claim(monkeypatch) -> None:
    data = _with_running_a1(json.loads(ROADMAP.read_text(encoding="utf-8")))
    running = data["waves"][0]["slices"][0]
    running.pop("execution_claim")
    monkeypatch.setattr(validator, "_load", lambda _path: data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("lacks a claim" in item for item in errors)


def test_validator_rejects_active_claim_count_drift(monkeypatch) -> None:
    data = _with_running_a1(json.loads(ROADMAP.read_text(encoding="utf-8")))
    data["completion"]["active_implementation_claims"] = 0
    monkeypatch.setattr(validator, "_load", lambda _path: data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("active claim count disagrees" in item for item in errors)


def test_validator_rejects_accepted_slice_without_released_claim(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    accepted = data["waves"][0]["slices"][0]
    accepted["status"] = "accepted_released"
    accepted["execution_claim"]["state"] = "running"
    data["completion"]["active_implementation_claims"] = 0
    monkeypatch.setattr(validator, "_load", lambda _path: data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("lacks a released claim" in item for item in errors)


def test_validator_rejects_foreign_claim_path(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    data["waves"][0]["slices"][0]["execution_claim"]["allowed_paths"].append(
        "app.py"
    )
    monkeypatch.setattr(validator, "_load", lambda _path: data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("claim paths differ" in item for item in errors)


def test_validator_binds_running_claim_to_long_run_lease(monkeypatch) -> None:
    data = _with_running_a1(json.loads(ROADMAP.read_text(encoding="utf-8")))
    running = data["waves"][0]["slices"][0]
    running["execution_claim"].pop("run_id")
    data["long_run_state"]["active_claims"] = ["WRONG"]
    monkeypatch.setattr(validator, "_load", lambda _path: data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("claim identity is incomplete" in item for item in errors)
    assert any("absent from long-run state" in item for item in errors)


def test_validator_rejects_wave_state_drift(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    data["waves"][0]["status"] = "nonsense"
    monkeypatch.setattr(validator, "_load", lambda _path: data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("wave has unexpected status" in item for item in errors)


def test_json_and_vnext_authority_hostile_matrix(tmp_path, monkeypatch) -> None:
    path = tmp_path / "duplicate.json"
    path.write_text('{"kind":"first","kind":"second"}', encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate JSON object key"):
        validator._load(path)

    hostile_documents = [
        (b'{"kind":"first","kind":"second"}', "duplicate_json_key"),
        (b'{"payload":"never echo me"', "malformed_json"),
        (b'["never echo me"]', "non_object_root"),
        (b'{"value":NaN}', "nonfinite_json"),
        (b'\xffnever echo me', "malformed_json"),
    ]
    for raw, expected_error in hostile_documents:
        path = tmp_path / "hostile.json"
        path.write_bytes(raw)
        result = _run(path)
        assert result.returncode == 1
        assert result.stderr == ""
        payload = json.loads(result.stdout)
        assert payload["ok"] is False
        assert payload["errors"] == [expected_error]
        assert "never echo me" not in result.stdout

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b'{"padding":"' + b"x" * validator.MAX_JSON_BYTES + b'"}')
    with pytest.raises(validator._JsonDocumentError) as too_large:
        validator._load(oversized)
    assert too_large.value.code == "limit_exceeded"
    deep = b'{"value":' + b"[" * (validator.MAX_JSON_DEPTH + 2) + b"0" + b"]" * (validator.MAX_JSON_DEPTH + 2) + b"}"
    with pytest.raises(validator._JsonDocumentError) as too_deep:
        validator._json_snapshot_from_bytes(deep, role="round7")
    assert too_deep.value.code == "limit_exceeded"
    with pytest.raises(validator._JsonDocumentError) as missing:
        validator._load(tmp_path / "missing.json")
    assert missing.value.code == "read_failed"
    for role in ("round7", "open_work", "evidence", "guidance", "round6", "git_blob"):
        snapshot = validator._json_snapshot_from_bytes(b'{"items":[1]}', role=role)
        assert snapshot.role == role
        with pytest.raises(TypeError):
            snapshot.root["items"] = ()
        assert snapshot.root["items"] == (1,)

    factory = _current_authority_factory()
    control_documents, materialized_documents = _rolling_projection_fixture_documents()
    monkeypatch.setattr(
        validator,
        "_materialized_json_blob",
        lambda _root, blob_id: materialized_documents.get(blob_id),
    )
    active_run_id = "fixture-current-authority-running"

    def current_running_fixture() -> dict:
        return _current_running_fixture(factory, active_run_id)

    def active_claim(data: dict) -> dict:
        return next(
            item
            for item in data["external_lane_claims"]
            if item.get("run_id") == active_run_id
        )

    def validate_active_world(
        data: dict[str, Any],
        *,
        documents: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> list[str]:
        """Exercise active v2, freeze and projection gates on injected data."""

        explicit_authority = _explicit_active_fixture_authority(
            data,
            factory,
            documents=control_documents if documents is None else documents,
        )
        claims = {
            item["run_id"]: item
            for item in data["external_lane_claims"]
            if type(item) is dict and type(item.get("run_id")) is str
        }
        leases: dict[str, list[dict[str, Any]]] = {}
        for lease in data["long_run_state"]["agent_leases"]:
            if type(lease) is dict and type(lease.get("claim_id")) is str:
                leases.setdefault(lease["claim_id"], []).append(lease)
        errors: list[str] = []
        typed_claims: list[tuple[dict[str, Any], validator.AbcClaimV2]] = []
        for index, raw_claim in enumerate(data["external_lane_claims"]):
            if type(raw_claim) is not dict or raw_claim.get("state") != "running":
                continue
            allowed_scope, allowed_valid = validator._collect_repository_paths(
                raw_claim.get("allowed_paths", [])
            )
            pin_scope, pins_valid = validator._collect_repository_paths(
                list(raw_claim.get("read_only_pins", {}))
                if type(raw_claim.get("read_only_pins")) is dict
                else None
            )
            if (
                allowed_valid
                and pins_valid
                and validator._first_path_scope_overlap(pin_scope, allowed_scope)
            ):
                errors.append(
                    f"external lane read-only pins overlap writer paths: {index}"
                )
            typed = validator._validate_abc_claim_v2(
                raw_claim,
                index=index,
                repo_root=REPO_ROOT,
                authority=explicit_authority,
                authority_revision=data["long_run_state"]["revision"],
                claims_by_run_id=claims,
                active_run_ids=set(data["long_run_state"]["active_claims"]),
                leases_by_claim=leases,
                errors=errors,
            )
            if typed is not None:
                typed_claims.append((raw_claim, typed))
                validator._validate_active_freeze_binding(
                    raw_claim,
                    typed,
                    repo_root=REPO_ROOT,
                    authority=explicit_authority,
                    errors=errors,
                )
        validator._validate_rolling_projection(
            typed_claims,
            repo_root=REPO_ROOT,
            authority=explicit_authority,
            errors=errors,
        )
        return errors

    claim_mutations = (
        (lambda claim: claim.pop("claim_class"), "fields are missing, aliased"),
        (lambda claim: claim.__setitem__("unknown_alias", True), "fields are missing, aliased"),
        (lambda claim: claim.__setitem__("thread_id", []), "typed identity is invalid"),
        (lambda claim: claim["baseline_blobs"].__setitem__(claim["allowed_paths"][0], "0" * 40), "baseline blob is not materialized"),
        (lambda claim: claim["authority_revision"].__setitem__("registered_round7_revision", 47), "authority revision is invalid"),
        (lambda claim: claim["regression_tests"][0]["argv"].__setitem__(0, "git"), "closed local Python boundary"),
        (lambda claim: claim["regression_tests"][0]["argv"].insert(7, "tests\\test_chat_route_tool_policy.py"), "command artifact escapes claim writer"),
        (lambda claim: claim["regression_tests"][3]["argv"].append("scripts\\roadmap_safe_queue_audit.py"), "command artifact escapes claim writer"),
        (lambda claim: claim["regression_tests"].pop(2), "do not cover focused, dependency and central"),
        (lambda claim: claim["regression_tests"][0]["argv"].__setitem__(-1, "C:\\shared"), "not an allowlisted validator invocation"),
        (lambda claim: claim["claim_transfer"].__setitem__("unexpected", True), "claim transfer is not exact"),
        (lambda claim: (claim["baseline_blobs"].__setitem__(claim["allowed_paths"][0], "absent"), claim.__setitem__("new_evidence_writer_paths", [claim["allowed_paths"][0]])), "absent baseline is reserved"),
        (lambda claim: claim["read_only_pins"].pop("scripts/validate_parallel_roadmap_execution.py"), "read-only pin set is missing mandatory pins or exact Evidence identity"),
        (lambda claim: claim["read_only_pins"].__setitem__("src/unified_source_index_contract.py", "0" * 40), "read-only pin is stale or malformed"),
        (lambda claim: claim["read_only_pins"].__setitem__("C:\\hostile", "a" * 40), "read-only pin is stale or malformed"),
        (lambda claim: claim["read_only_pins"].__setitem__(claim["allowed_paths"][0], factory.baseline_blob), "external lane read-only pins overlap writer paths"),
        (lambda claim: claim["stale_invalidation"].pop(), "stale invalidation set is not closed"),
        (lambda claim: claim["candidate_blobs"].__setitem__(claim["allowed_paths"][0], "0" * 40), "unfrozen candidate must not publish blobs"),
        (lambda claim: claim["correction_budget"].__setitem__("used", 2), "correction budget is invalid"),
    )
    for mutate, expected in claim_mutations:
        data = current_running_fixture()
        mutate(active_claim(data))
        errors = validate_active_world(data)
        assert any(expected in error for error in errors), (expected, errors)

    data = current_running_fixture()
    claim = active_claim(data)
    source_id = claim["claim_transfer"]["from_claim_id"]
    source = next(
        item for item in data["external_lane_claims"] if item.get("run_id") == source_id
    )
    source["state"] = "running"
    errors = validate_active_world(data)
    assert any("claim transfer is not exact, terminal" in error for error in errors)

    data = current_running_fixture()
    new_path = "docs/plans/evidence/new-fixture-evidence.json"
    new_claim = _running_external_claim(data, "ABC-new-evidence-fixture", [new_path])
    new_claim["owner"] = "root/Sol fixture"
    data["long_run_state"]["agent_leases"][-1]["owner"] = new_claim["owner"]
    new_claim["baseline_blobs"][new_path] = "absent"
    new_claim["new_evidence_writer_paths"] = [new_path]
    source = next(
        item
        for item in data["external_lane_claims"]
        if item.get("run_id") == new_claim["claim_transfer"]["from_claim_id"]
    )
    explicit_authority = validator.AuthoritySnapshotBundle(
        documents=MappingProxyType(
            {
                validator.OPEN_WORK_PATH: validator._freeze_json(
                    {"current_position": {"external_lane_claims": data["external_lane_claims"]}}
                )
            }
        ),
        blob_ids=MappingProxyType(dict(new_claim["read_only_pins"])),
        paths=tuple(sorted(new_claim["read_only_pins"])),
        absent_paths=(new_path,),
        absent_paths_readback=validator.CAPTURED_ABSENT_PATHS_READBACK_PASS,
    )
    errors = []
    validator._validate_abc_claim_v2(
        new_claim,
        index=0,
        repo_root=REPO_ROOT,
        authority=explicit_authority,
        authority_revision=data["long_run_state"]["revision"],
        claims_by_run_id={source["run_id"]: source, new_claim["run_id"]: new_claim},
        active_run_ids={new_claim["run_id"]},
        leases_by_claim={new_claim["run_id"]: data["long_run_state"]["agent_leases"]},
        errors=errors,
    )
    assert errors == []

    data = current_running_fixture()
    claim = active_claim(data)
    claim.pop("claim_class")
    protected_alias = "DOCS/plans/open-work-completion-master-roadmap.json"
    claim["allowed_paths"] = [protected_alias]
    claim["hotfiles"] = [protected_alias]
    errors = validate_active_world(data)
    assert any("protected writer scope requires root/Sol ownership" in error for error in errors)

    evidence_path = factory.evidence_path
    guidance = control_documents[validator.GUIDANCE_PATH]
    round6 = control_documents[validator.ROUND6_PATH]
    data = current_running_fixture()
    claim = active_claim(data)
    candidate_blobs = {
        path: ("d" if index == 0 else "e") * 40
        for index, path in enumerate(claim["allowed_paths"])
    }
    digest = validator._candidate_set_digest(candidate_blobs)
    command_manifest = [
        {"id": item["id"], "tier": item["tier"], "argv": item["argv"]}
        for item in claim["regression_tests"]
    ]
    command_digest = validator._manifest_digest(command_manifest)
    node_ids = ["tests/test_parallel_roadmap_execution.py::fixture"]
    node_digest = validator._node_manifest_digest(node_ids)
    claim["candidate_blobs"] = candidate_blobs
    claim["candidate_state"] = "frozen_for_independent_qa"
    claim["candidate_evidence_path"] = evidence_path
    claim["candidate_evidence_manifest_blob"] = claim["read_only_pins"][evidence_path]
    claim["candidate_set_digest_sha256"] = digest
    claim["candidate_command_manifest_digest_sha256"] = command_digest
    claim["candidate_pytest_node_manifest_digest_sha256"] = node_digest
    claim["candidate_pytest_node_ids"] = node_ids
    claim["candidate_frozen_at"] = "2026-08-03T18:50:00+02:00"
    inventory_receipt = control_documents[evidence_path]["inventory_receipt"]
    evidence = {
        "schema_version": 1,
        "kind": "odysseus.abc_claim_evidence",
        "claim_id": active_run_id,
        "thread_id": claim["thread_id"],
        "authority_revision": claim["authority_revision"],
        "candidate_writer_paths": claim["allowed_paths"],
        "baseline_blobs": claim["baseline_blobs"],
        "baseline_readback": claim["baseline_readback"],
        "candidate_blobs": candidate_blobs,
        "candidate_state": "frozen_for_independent_qa",
        "candidate_frozen_at": claim["candidate_frozen_at"],
        "candidate_set_digest_sha256": digest,
        "candidate_command_manifest_digest_sha256": command_digest,
        "candidate_pytest_node_manifest_digest_sha256": node_digest,
        "candidate_pytest_node_ids": node_ids,
        "read_only_pins": {
            path: blob_id
            for path, blob_id in claim["read_only_pins"].items()
            if path != evidence_path
        },
        "stale_invalidation": claim["stale_invalidation"],
        "verification_tiers": claim["verification_tiers"],
        "correction_budget": claim["correction_budget"],
        "inventory_receipt": inventory_receipt,
        "live_actions": False,
    }
    monkeypatch.setattr(validator, "_materialized_git_blob", lambda _root, _blob: True)
    frozen_documents = {
        evidence_path: evidence,
        validator.GUIDANCE_PATH: guidance,
        validator.ROUND6_PATH: round6,
    }
    errors = validate_active_world(data, documents=frozen_documents)
    assert errors == []

    evidence["unexpected"] = True
    errors = validate_active_world(data, documents=frozen_documents)
    assert any("Evidence schema is not closed and exact" in error for error in errors)
    evidence.pop("unexpected")

    stale = evidence["stale_invalidation"]
    evidence["stale_invalidation"] = stale[:-1]
    errors = validate_active_world(data, documents=frozen_documents)
    assert any("Evidence stale set differs" in error for error in errors)
    evidence["stale_invalidation"] = stale

    evidence["candidate_blobs"] = dict(candidate_blobs)
    evidence["candidate_blobs"][claim["allowed_paths"][0]] = "0" * 40
    errors = validate_active_world(data, documents=frozen_documents)
    assert any("not one exact bijection" in error for error in errors)
    evidence["candidate_blobs"] = candidate_blobs

    evidence["read_only_pins"] = dict(claim["read_only_pins"])
    errors = validate_active_world(data, documents=frozen_documents)
    assert any("not one exact bijection" in error for error in errors)

    data = current_running_fixture()
    guidance = json.loads(json.dumps(control_documents[validator.GUIDANCE_PATH]))
    guidance["analysis_summary"]["round6_parallel_execution"]["inventory_member_count"] = 29
    errors = validate_active_world(
        data,
        documents={
            evidence_path: control_documents[evidence_path],
            validator.GUIDANCE_PATH: guidance,
            validator.ROUND6_PATH: round6,
        },
    )
    assert any("rolling projection count, digest" in error for error in errors)

    guidance = json.loads(json.dumps(control_documents[validator.GUIDANCE_PATH]))
    guidance["analysis_summary"]["round6_parallel_execution"]["inventory_source_blob"] = (
        guidance["analysis_summary"]["round6_parallel_execution"][
            "inventory_denominator_change"
        ]["old_round6_blob"]
    )
    errors = validate_active_world(
        data,
        documents={
            evidence_path: control_documents[evidence_path],
            validator.GUIDANCE_PATH: guidance,
            validator.ROUND6_PATH: round6,
        },
    )
    assert any("source Guidance blob disagrees" in error for error in errors)

    guidance = json.loads(json.dumps(control_documents[validator.GUIDANCE_PATH]))
    guidance["analysis_summary"]["round6_parallel_execution"][
        "inventory_denominator_change"
    ]["added"] = []
    errors = validate_active_world(
        data,
        documents={
            evidence_path: control_documents[evidence_path],
            validator.GUIDANCE_PATH: guidance,
            validator.ROUND6_PATH: round6,
        },
    )
    assert any("denominator provenance is not exact" in error for error in errors)

    data = current_running_fixture()
    errors = validate_active_world(data)
    assert errors == []
    loads: dict[str, int] = {}

    def load_once(candidate: Path) -> dict:
        loads[candidate.name] = loads.get(candidate.name, 0) + 1
        return validator._load_json_document(candidate)

    monkeypatch.setattr(validator, "_load_auxiliary", load_once)
    assert loads == {}


def test_abc_v2_accepts_extra_current_product_pins_and_preserves_classic_shape(
    monkeypatch,
) -> None:
    factory = _current_authority_factory()
    run_id = "fixture-product-pin-subset"
    product_path = "src/unified_source_index_contract.py"

    def fixture(*, extra_product_pin: bool) -> tuple[dict, dict]:
        data = {
            "external_lane_claims": [],
            "long_run_state": {"revision": 17, "active_claims": [], "agent_leases": []},
        }
        claim = factory.running_v2(
            data,
            run_id,
            ["src/coding_agent_backend.py", "tests/test_coding_agent_backend.py"],
        )
        if extra_product_pin:
            claim["read_only_pins"][product_path] = "e" * 40
        return data, claim

    classic, classic_claim = fixture(extra_product_pin=False)
    classic_keys = set(classic_claim)
    assert _validate_running_v2_fixture(classic_claim, classic, factory, monkeypatch) == []
    assert set(classic_claim) == classic_keys
    assert "origin_mode" not in classic_claim["claim_transfer"]

    expanded, expanded_claim = fixture(extra_product_pin=True)
    assert _validate_running_v2_fixture(
        expanded_claim,
        expanded,
        factory,
        monkeypatch,
        additional_blob_ids={product_path: "e" * 40},
    ) == []
    assert set(expanded_claim["read_only_pins"]) == (
        set(factory.pins) | {product_path}
    )


def test_abc_v2_extra_product_pin_rejects_cross_claim_writer_overlap(
    monkeypatch,
) -> None:
    factory = _current_authority_factory()
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    first, second = factory.parallel_running_v2(
        data,
        [
            ("fixture-product-pin-reader", ["src/coding_agent_backend.py"]),
            ("fixture-product-pin-writer", ["tests/test_coding_agent_backend.py"]),
        ],
    )
    writer_path = second["allowed_paths"][0]
    first["read_only_pins"][writer_path] = validator._path_blob_id(
        REPO_ROOT / writer_path
    )
    open_work = {
        "current_position": {"external_lane_claims": data["external_lane_claims"]}
    }
    monkeypatch.setattr(
        validator,
        "_load",
        lambda candidate: open_work
        if candidate.name == "open-work-completion-master-roadmap.json"
        else data,
    )
    monkeypatch.setattr(validator, "_load_auxiliary", validator._load_json_document)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("active read-only pin collides with writer" in error for error in errors)


def test_validator_rejects_planned_slice_claim_object(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    planned_slice = next(
        item
        for wave in data["waves"]
        for item in wave["slices"]
        if item["status"] == "planned_nonclaimable"
    )
    planned_slice["execution_claim"] = {}
    monkeypatch.setattr(validator, "_load", lambda _path: data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("planned implementation slice has a claim object" in item for item in errors)


def test_validator_rejects_orphan_agent_lease(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    data["long_run_state"]["agent_leases"].append(
        {"claim_id": "ORPHAN", "owner": "nobody", "expires_at": "never"}
    )
    monkeypatch.setattr(validator, "_load", lambda _path: data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("leases do not exactly match" in item for item in errors)


def test_validator_rejects_supporting_claim_scope_expansion(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    data["runtime_state_validator_claim"]["allowed_paths"].append("app.py")
    monkeypatch.setattr(validator, "_load", lambda _path: data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("supporting claim paths differ" in item for item in errors)


def test_validator_rejects_live_enabled_claim(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    data["waves"][0]["slices"][0]["execution_claim"]["live_actions"] = True
    monkeypatch.setattr(validator, "_load", lambda _path: data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("permits live actions" in item for item in errors)


def _released_external_claim(run_id: str, allowed_paths: list[str]) -> dict:
    return {
        "run_id": run_id,
        "slice_id": f"slice-{run_id}",
        "owner": "Bob/Terra",
        "state": "released_accepted",
        "allowed_paths": allowed_paths,
        "hotfiles": [],
        "read_only_pins": {},
        "live_actions": False,
        "released_at": "2026-08-03T11:00:00+02:00",
    }


def _running_external_claim(
    data: dict,
    run_id: str,
    allowed_paths: list[str],
    *,
    read_only_pins: dict[str, str] | None = None,
) -> dict:
    return _current_authority_factory().running_v2(
        data,
        run_id,
        allowed_paths,
        read_only_pins=read_only_pins,
    )


def test_running_external_claim_replaces_productive_active_authority() -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    productive_run_id = "fixture-productive-active"
    data["external_lane_claims"] = [
        {
            "run_id": productive_run_id,
            "owner": "Root/Sol",
            "state": "running",
        }
    ]
    data["long_run_state"]["active_claims"] = [productive_run_id]
    data["long_run_state"]["agent_leases"] = [
        {
            "claim_id": productive_run_id,
            "owner": "Root/Sol",
            "expires_at": "2026-08-08T15:30:00+02:00",
        }
    ]
    assert data["long_run_state"]["active_claims"] == [productive_run_id]
    assert data["long_run_state"]["agent_leases"][0]["claim_id"] == productive_run_id

    synthetic = _running_external_claim(data, "fixture-isolated", ["src/a.py"])

    assert data["long_run_state"]["active_claims"] == [synthetic["run_id"]]
    assert [lease["claim_id"] for lease in data["long_run_state"]["agent_leases"]] == [
        synthetic["run_id"]
    ]
    assert productive_run_id not in {
        claim["run_id"]
        for claim in data["external_lane_claims"]
        if type(claim) is dict and type(claim.get("run_id")) is str
    }


def _activate_supporting_claim(data: dict, supporting_name: str) -> dict:
    running_external_ids = {
        claim.get("run_id")
        for claim in data["external_lane_claims"]
        if isinstance(claim, dict) and claim.get("state") == "running"
    }
    for claim in data["external_lane_claims"]:
        if isinstance(claim, dict) and claim.get("run_id") in running_external_ids:
            claim["state"] = "released_blocked"
            claim["released_at"] = "2026-08-03T17:50:00+02:00"
    data["long_run_state"]["active_claims"] = [
        run_id
        for run_id in data["long_run_state"]["active_claims"]
        if run_id not in running_external_ids
    ]
    data["long_run_state"]["agent_leases"] = [
        lease
        for lease in data["long_run_state"]["agent_leases"]
        if lease.get("claim_id") not in running_external_ids
    ]

    support = data[supporting_name]
    support["state"] = "running"
    support.pop("released_at", None)
    support.pop("release_reason", None)
    data["long_run_state"]["active_claims"].append(support["run_id"])
    data["long_run_state"]["agent_leases"].append(
        {
            "claim_id": support["run_id"],
            "owner": support["owner"],
            "expires_at": support["lease_expires_at"],
        }
    )
    return support


def _load_round7_and_open_work(
    monkeypatch,
    data: dict,
    open_work: dict | None = None,
    authority: validator.AuthoritySnapshotBundle | None = None,
) -> None:
    if open_work is None:
        open_work = {
            "current_position": {"external_lane_claims": data["external_lane_claims"]}
        }
    monkeypatch.setattr(
        validator,
        "_load",
        lambda path: open_work
        if path.name == "open-work-completion-master-roadmap.json"
        else data,
    )
    if authority is not None:
        monkeypatch.setattr(
            validator,
            "_capture_v2_authority_documents",
            lambda *_args, **_kwargs: authority,
        )


def test_validator_rejects_external_claim_projection_drift(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    data["external_lane_claims"] = [_released_external_claim("external-a", ["src/a.py"])]
    open_work = {"current_position": {"external_lane_claims": []}}
    monkeypatch.setattr(
        validator,
        "_load",
        lambda path: open_work if path.name == "open-work-completion-master-roadmap.json" else data,
    )

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("external claim projection disagrees" in item for item in errors)


def test_validator_rejects_external_writer_overlap(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    _current_authority_factory().parallel_running_v2(
        data,
        [
            ("external-a", ["src/shared.py"]),
            ("external-b", ["src/shared.py"]),
        ],
    )
    _load_round7_and_open_work(monkeypatch, data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("active claim writer paths overlap" in item for item in errors)


def test_validator_allows_released_claim_path_reuse(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    data["external_lane_claims"] = [
        _released_external_claim("external-old", ["src/shared.py"]),
        _released_external_claim("external-new", ["src/shared.py"]),
    ]
    _load_round7_and_open_work(monkeypatch, data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert not any("active claim writer paths overlap" in item for item in errors)


def test_terminal_v2_history_is_inactive_for_current_factory(monkeypatch) -> None:
    data = {
        "external_lane_claims": [],
        "long_run_state": {"revision": 17, "active_claims": [], "agent_leases": []},
    }
    factory = _current_authority_factory()
    factory.replace_active_authority(data)
    terminal = factory.terminal_v2(
        "fixture-terminal-history",
        ["src/shared-lifecycle.py"],
        authority_revision=data["long_run_state"]["revision"] - 2,
    )
    data["external_lane_claims"].append(terminal)
    running = factory.running_v2(
        data,
        "fixture-running-after-terminal",
        ["src/shared-lifecycle.py"],
    )

    assert _validate_running_v2_fixture(running, data, factory, monkeypatch) == []


def test_validator_rejects_external_overlap_with_supporting_claim(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    _activate_supporting_claim(data, "external_lane_validator_claim")
    _running_external_claim(
        data,
        "external-a",
        ["scripts/validate_integration_spine_convergence.py"],
    )
    _load_round7_and_open_work(monkeypatch, data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("active claim writer paths overlap" in item for item in errors)


def test_validator_rejects_external_overlap_with_running_round7_claim(monkeypatch) -> None:
    data = _with_running_a1(json.loads(ROADMAP.read_text(encoding="utf-8")))
    a1_path = data["waves"][0]["slices"][0]["execution_claim"]["allowed_paths"][0]
    _running_external_claim(data, "external-a", [a1_path])
    _load_round7_and_open_work(monkeypatch, data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("active claim writer paths overlap" in item for item in errors)


def test_validator_rejects_pin_collision_with_another_writer(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    blob_id = "a" * 40
    factory = _current_authority_factory()
    factory.replace_active_authority(data)
    factory._append_running_v2(
        data,
        "external-reader",
        ["src/reader.py"],
        read_only_pins={"src/shared.py": blob_id},
    )
    factory._append_running_v2(data, "external-writer", ["src/shared.py"])
    _load_round7_and_open_work(monkeypatch, data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("active read-only pin collides with writer" in item for item in errors)


def test_validator_rejects_pin_collision_with_supporting_writer(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    _activate_supporting_claim(data, "external_lane_validator_claim")
    _running_external_claim(
        data,
        "external-reader",
        ["src/reader.py"],
        read_only_pins={"scripts/validate_integration_spine_convergence.py": "b" * 40},
    )
    _load_round7_and_open_work(monkeypatch, data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("active read-only pin collides with writer" in item for item in errors)


def test_validator_rejects_duplicate_external_run_id(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    data["external_lane_claims"] = [
        _released_external_claim("external-a", ["src/a.py"]),
        _released_external_claim("external-a", ["src/b.py"]),
    ]
    _load_round7_and_open_work(monkeypatch, data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("claim id is duplicated" in item for item in errors)


def test_validator_rejects_active_run_id_reused_from_supporting_claim(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    support = _activate_supporting_claim(data, "external_lane_validator_claim")
    claim = _running_external_claim(data, support["run_id"], ["src/a.py"])
    data["long_run_state"]["active_claims"].pop()
    data["long_run_state"]["agent_leases"].pop()
    claim["owner"] = support["owner"]
    claim["lease_expires_at"] = support["lease_expires_at"]
    _load_round7_and_open_work(monkeypatch, data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("active claim run ids are not globally unique" in item for item in errors)


def test_validator_rejects_malformed_external_paths_and_pins(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    claim = _released_external_claim("external-a", [""])
    claim["read_only_pins"] = {"": {"unexpected": "shape"}}
    data["external_lane_claims"] = [claim]
    _load_round7_and_open_work(monkeypatch, data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("writer path is malformed" in item for item in errors)
    assert any("read-only pins are malformed" in item for item in errors)


def test_validator_rejects_missing_external_projection_field(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    _load_round7_and_open_work(monkeypatch, data, {"current_position": {}})

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("external claim projection is missing" in item for item in errors)


def test_validator_rejects_external_running_claim_without_timestamps(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    claim = _running_external_claim(data, "external-a", ["src/a.py"])
    claim.pop("acquired_at")
    claim["lease_expires_at"] = None
    data["long_run_state"]["agent_leases"][-1]["expires_at"] = None
    _load_round7_and_open_work(monkeypatch, data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("external running claim timestamps are invalid" in item for item in errors)


def test_validator_rejects_case_insensitive_writer_alias(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    _current_authority_factory().parallel_running_v2(
        data,
        [
            ("external-upper", ["src/Shared.py"]),
            ("external-lower", ["SRC/shared.PY"]),
        ],
    )
    _load_round7_and_open_work(monkeypatch, data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("active claim writer paths overlap" in item for item in errors)


def test_validator_rejects_writer_directory_ancestry(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    _current_authority_factory().parallel_running_v2(
        data,
        [
            ("external-directory", ["src/components"]),
            ("external-file", ["src/components/widget.py"]),
        ],
    )
    _load_round7_and_open_work(monkeypatch, data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("active claim writer paths overlap" in item for item in errors)


def test_validator_rejects_pin_case_alias_and_ancestry(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    factory = _current_authority_factory()
    factory.replace_active_authority(data)
    factory._append_running_v2(
        data,
        "external-reader",
        ["src/reader.py"],
        read_only_pins={"SRC/shared": "c" * 40},
    )
    factory._append_running_v2(
        data, "external-writer", ["src/SHARED/file.py"]
    )
    _load_round7_and_open_work(monkeypatch, data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("active read-only pin collides with writer" in item for item in errors)


def test_validator_rejects_canonical_hotfile_aliases(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    claim = _released_external_claim("external-a", ["src/a.py"])
    claim["hotfiles"] = ["src/a.py", "SRC/A.PY"]
    data["external_lane_claims"] = [claim]
    _load_round7_and_open_work(monkeypatch, data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("external lane hotfiles escape allowed paths" in item for item in errors)


def test_validator_rejects_internal_pin_ancestry_alias(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    claim = _released_external_claim("external-a", ["src/reader.py"])
    claim["read_only_pins"] = {
        "src/components": "a" * 40,
        "SRC/components/file.py": "b" * 40,
    }
    data["external_lane_claims"] = [claim]
    _load_round7_and_open_work(monkeypatch, data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("external lane read-only pins are malformed" in item for item in errors)


def test_validator_rejects_non_strict_lease_window(monkeypatch) -> None:
    for expires_at in (
        "2026-08-03T11:20:00+02:00",
        "2026-08-03T11:19:59+02:00",
    ):
        data = json.loads(ROADMAP.read_text(encoding="utf-8"))
        claim = _running_external_claim(data, "external-a", ["src/a.py"])
        claim["lease_expires_at"] = expires_at
        data["long_run_state"]["agent_leases"][-1]["expires_at"] = expires_at
        _load_round7_and_open_work(monkeypatch, data)
        errors = validator.validate(ROADMAP, REPO_ROOT)
        assert any(
            "external running claim timestamps are invalid" in item for item in errors
        )


def test_validator_rejects_historical_cross_category_run_id_reuse(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    historical_run_id = data["runtime_state_validator_claim"]["run_id"]
    data["external_lane_claims"] = [
        _released_external_claim(historical_run_id, ["src/historical.py"])
    ]
    _load_round7_and_open_work(monkeypatch, data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("globally unique across claim records" in item for item in errors)


def test_validator_accepts_parallel_uda02_and_fca00_fixture(monkeypatch) -> None:
    data = {
        "external_lane_claims": [],
        "long_run_state": {"revision": 17, "active_claims": [], "agent_leases": []},
    }
    factory = _current_authority_factory()
    claims = factory.parallel_running_v2(
        data,
        [
            (
                "ABC-UDA02-fixture",
                [
                    "src/unified_source_index_change_signal.py",
                    "src/unified_source_index_change_outbox.py",
                    "tests/test_unified_source_index_change_signal.py",
                ],
            ),
            (
                "ABC-FCA00-fixture",
                [
                    "src/unified_source_index_sources/forge_code.py",
                    "src/repo_git_adapter.py",
                    "tests/test_unified_source_index_forge_code_source.py",
                    "tests/test_repo_git_adapter.py",
                ],
            ),
        ],
    )
    assert all(
        _validate_running_v2_fixture(claim, data, factory, monkeypatch) == []
        for claim in claims
    )


def test_validator_rejects_released_supporting_claim_without_identity(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    support = data["runtime_state_validator_claim"]
    for key in ("run_id", "slice_id", "owner"):
        support.pop(key)
    _load_round7_and_open_work(monkeypatch, data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("supporting claim identity is incomplete" in item for item in errors)


def test_validator_requires_exact_supporting_hotfiles(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    data["external_lane_validator_claim"]["hotfiles"] = []
    _load_round7_and_open_work(monkeypatch, data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("supporting claim hotfiles differ" in item for item in errors)


def test_validator_rejects_external_live_actions(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    claim = _released_external_claim("external-a", ["src/a.py"])
    claim["live_actions"] = True
    data["external_lane_claims"] = [claim]
    open_work = {"current_position": {"external_lane_claims": data["external_lane_claims"]}}
    monkeypatch.setattr(
        validator,
        "_load",
        lambda path: open_work if path.name == "open-work-completion-master-roadmap.json" else data,
    )

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("external lane claim permits live actions" in item for item in errors)


def test_validator_requires_actual_composition_paths(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    cutover = data["waves"][2]["slices"][4]
    cutover["future_claim_paths"].remove("src/tool_execution.py")
    monkeypatch.setattr(validator, "_load", lambda _path: data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("missing required actual composition paths" in item for item in errors)


def test_validator_requires_forge_and_lifecycle_predecessors(monkeypatch) -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    forge = data["waves"][1]["slices"][3]
    forge["depends_on"].remove("FCA-06A")
    lifecycle = data["waves"][3]["slices"][0]
    lifecycle["depends_on"].remove("ULO-02")
    monkeypatch.setattr(validator, "_load", lambda _path: data)

    errors = validator.validate(ROADMAP, REPO_ROOT)

    assert any("FCA-06A" in item for item in errors)
    assert any("ULO-02" in item for item in errors)


def test_r4_paths_reject_unicode_windows_and_hostile_aliases() -> None:
    assert validator._repository_path_key("src/caf\u00e9.py") == ("src", "caf\u00e9.py")
    assert validator._repository_path_key("src/cafe\u0301.py") is None
    for alias in (
        "src/CON.py",
        "src/aux ",
        "src/component.",
        "C:/outside.py",
        "src\\separator.py",
    ):
        assert validator._repository_path_key(alias) is None

    class HostileList(list):
        pass

    paths, valid = validator._collect_repository_paths(HostileList(["src/a.py"]))
    assert paths == {}
    assert valid is False


def test_r4_byte_capture_is_immutable_after_writer_drift(tmp_path) -> None:
    artifact = tmp_path / "candidate.py"
    artifact.write_bytes(b"first")
    captured, captured_blob = validator._capture_file_bytes(artifact)
    artifact.write_bytes(b"second")

    assert captured == b"first"
    assert captured_blob == validator._git_blob_id(b"first")
    assert captured_blob != validator._path_blob_id(artifact)


def test_r4_authority_bundle_captures_each_json_authority_once(monkeypatch) -> None:
    roadmap = json.loads(ROADMAP.read_text(encoding="utf-8"))
    claim = next(
        item
        for item in roadmap["external_lane_claims"]
        if item.get("claim_id") == "ABC-R7-VALIDATOR-R4-20260803T202708+0200"
    )
    original = validator._load_json_snapshot
    captures: dict[str, int] = {}

    def capture_once(path: Path, *, role: str) -> validator.JsonSnapshot:
        key = path.relative_to(REPO_ROOT).as_posix()
        captures[key] = captures.get(key, 0) + 1
        return original(path, role=role)

    monkeypatch.setattr(validator, "_load_json_snapshot", capture_once)
    errors: list[str] = []
    bundle = validator._capture_v2_authority_documents(
        [claim], repo_root=REPO_ROOT, errors=errors
    )

    assert errors == []
    assert all(count == 1 for count in captures.values())
    assert set(bundle.blob_ids) == {
        validator.OPEN_WORK_PATH,
        validator.GUIDANCE_PATH,
        validator.ROUND6_PATH,
    }
    assert not any(path.startswith("docs/plans/evidence/") for path in bundle.paths)
    assert set(claim["allowed_paths"]).isdisjoint(bundle.raw_bytes)


def test_r4_real_serialized_evidence_binds_dependencies_without_self_hash(
    monkeypatch,
) -> None:
    evidence_path = "docs/plans/evidence/r4-real-serialized-fixture.json"
    candidate_blobs = {
        "README.md": subprocess.check_output(
            ["git", "rev-parse", "HEAD:README.md"], cwd=REPO_ROOT, text=True
        ).strip()
    }
    dependency_pins = dict(_current_authority_factory().pins)
    product_pin_path = "src/unified_source_index_contract.py"
    dependency_pins[product_pin_path] = subprocess.check_output(
        ["git", "rev-parse", f"HEAD:{product_pin_path}"], cwd=REPO_ROOT, text=True
    ).strip()
    node_ids = ["tests/test_integration_spine_convergence.py::serialized_evidence"]
    command_digest = validator._manifest_digest([])
    node_digest = validator._node_manifest_digest(node_ids)
    authority_revision = {
        "claimed_against_round7_revision": 53,
        "registered_round7_revision": 54,
    }
    verification_tiers = {
        "focused": "fixture",
        "dependency": "fixture",
        "central": "fixture",
        "expensive_live": "not_run_user_gated",
    }
    correction_budget = {
        "limit_per_invariant": 1,
        "used": 0,
        "failure_class": None,
        "after_repeat": "stop_and_rearchitect",
    }

    def serialized_snapshot(read_only_pins: dict[str, str]) -> validator.JsonSnapshot:
        evidence = {
            "schema_version": 1,
            "kind": "odysseus.abc_claim_evidence",
            "claim_id": "r4-serialized-fixture",
            "thread_id": "fixture-thread",
            "authority_revision": authority_revision,
            "candidate_writer_paths": ["README.md"],
            "baseline_blobs": candidate_blobs,
            "baseline_readback": "materialized_exact_binary_readback_pass",
            "candidate_blobs": candidate_blobs,
            "candidate_state": "frozen_for_independent_qa",
            "candidate_frozen_at": "2026-08-03T20:30:00+02:00",
            "candidate_set_digest_sha256": validator._candidate_set_digest(
                candidate_blobs
            ),
            "candidate_command_manifest_digest_sha256": command_digest,
            "candidate_pytest_node_manifest_digest_sha256": node_digest,
            "candidate_pytest_node_ids": node_ids,
            "read_only_pins": read_only_pins,
            "stale_invalidation": sorted(validator.ABC_V2_STALE_TRIGGERS),
            "verification_tiers": verification_tiers,
            "correction_budget": correction_budget,
            "inventory_receipt": {},
            "live_actions": False,
        }
        return validator._json_snapshot_from_bytes(
            json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            role="real_serialized_evidence",
        )

    def binding(snapshot: validator.JsonSnapshot) -> tuple[dict, validator.AbcClaimV2, validator.AuthoritySnapshotBundle]:
        pins = {**dependency_pins, evidence_path: snapshot.blob_id}
        claim = {
            "candidate_evidence_path": evidence_path,
            "candidate_evidence_manifest_blob": snapshot.blob_id,
            "candidate_set_digest_sha256": validator._candidate_set_digest(candidate_blobs),
            "candidate_command_manifest_digest_sha256": command_digest,
            "candidate_pytest_node_manifest_digest_sha256": node_digest,
            "candidate_pytest_node_ids": node_ids,
            "candidate_frozen_at": "2026-08-03T20:30:00+02:00",
            "authority_revision": authority_revision,
            "thread_id": "fixture-thread",
            "baseline_blobs": candidate_blobs,
            "baseline_readback": "materialized_exact_binary_readback_pass",
            "candidate_blobs": candidate_blobs,
            "candidate_state": "frozen_for_independent_qa",
            "read_only_pins": pins,
            "stale_invalidation": sorted(validator.ABC_V2_STALE_TRIGGERS),
            "verification_tiers": verification_tiers,
            "correction_budget": correction_budget,
        }
        typed = validator.AbcClaimV2(
            run_id="r4-serialized-fixture",
            slice_id="fixture",
            owner="Bob/Terra fixture",
            allowed_paths=("README.md",),
            hotfiles=(),
            pins=tuple(sorted(pins.items())),
            baselines=tuple(sorted(candidate_blobs.items())),
            commands=(),
            candidate_blobs=tuple(sorted(candidate_blobs.items())),
            candidate_state="frozen_for_independent_qa",
            stale_invalidation=frozenset(validator.ABC_V2_STALE_TRIGGERS),
            command_manifest_digest=command_digest,
            pytest_node_manifest_digest=node_digest,
            pytest_node_ids=tuple(node_ids),
        )
        authority = validator.AuthoritySnapshotBundle(
            documents=MappingProxyType({evidence_path: snapshot.root}),
            blob_ids=MappingProxyType({evidence_path: snapshot.blob_id}),
            paths=(evidence_path,),
            raw_bytes=MappingProxyType({evidence_path: snapshot.raw}),
        )
        return claim, typed, authority

    monkeypatch.setattr(validator, "_materialized_git_blob", lambda _root, _blob: True)
    claim, typed, authority = binding(serialized_snapshot(dependency_pins))
    errors: list[str] = []
    validator._validate_active_freeze_binding(
        claim, typed, repo_root=REPO_ROOT, authority=authority, errors=errors
    )
    assert errors == []
    assert claim["read_only_pins"][evidence_path] == authority.blob_ids[evidence_path]
    assert evidence_path not in authority.get(evidence_path)["read_only_pins"]

    drifted_pins = dict(dependency_pins)
    drifted_pins[next(iter(drifted_pins))] = "0" * 40
    claim, typed, authority = binding(serialized_snapshot(drifted_pins))
    validator._validate_active_freeze_binding(
        claim, typed, repo_root=REPO_ROOT, authority=authority, errors=errors
    )
    assert errors == ["active writer bytes, Evidence map and digest are not one exact bijection"]


def test_r4_terminal_receipt_requires_identical_pre_and_post_bindings() -> None:
    candidate_blobs = {
        "README.md": subprocess.check_output(
            ["git", "rev-parse", "HEAD:README.md"], cwd=REPO_ROOT, text=True
        ).strip()
    }
    command_manifest = [
        {
            "id": "focused",
            "tier": "focused",
            "argv": ["venv\\Scripts\\python.exe", "-B", "-m", "py_compile", "README.md"],
        }
    ]
    node_ids = ["tests/test_integration_spine_convergence.py::terminal"]
    bindings = {
        "candidate_set_digest_sha256": validator._candidate_set_digest(candidate_blobs),
        "command_manifest_digest_sha256": validator._manifest_digest(command_manifest),
        "pytest_node_manifest_digest_sha256": validator._node_manifest_digest(node_ids),
    }
    claim = {
        "candidate_blobs": candidate_blobs,
        "candidate_set_digest_sha256": bindings["candidate_set_digest_sha256"],
        "candidate_command_manifest_digest_sha256": bindings[
            "command_manifest_digest_sha256"
        ],
        "candidate_pytest_node_manifest_digest_sha256": bindings[
            "pytest_node_manifest_digest_sha256"
        ],
        "candidate_pytest_node_ids": node_ids,
        "terminal_receipt": {
            "candidate_blobs": candidate_blobs,
            "candidate_set_digest_sha256": bindings["candidate_set_digest_sha256"],
            "command_manifest": command_manifest,
            "command_manifest_digest_sha256": bindings["command_manifest_digest_sha256"],
            "pytest_node_ids": node_ids,
            "pytest_node_manifest_digest_sha256": bindings[
                "pytest_node_manifest_digest_sha256"
            ],
            "pre_release": dict(bindings),
            "post_release": dict(bindings),
        }
    }
    errors: list[str] = []
    validator._validate_terminal_receipt(claim, repo_root=REPO_ROOT, errors=errors)
    assert errors == []

    claim["terminal_receipt"]["post_release"]["candidate_set_digest_sha256"] = (
        "sha256:" + "0" * 64
    )
    validator._validate_terminal_receipt(claim, repo_root=REPO_ROOT, errors=errors)
    assert errors == ["terminal R4 receipt does not bind one candidate and manifest set"]


def test_r4_pending_release_requires_one_frozen_terminal_object(monkeypatch) -> None:
    def pending_release(data: dict) -> dict:
        terminal_history_ids = {
            item["run_id"]
            for item in data["external_lane_claims"]
            if item.get("slice_id")
            in {validator.R5_VALIDATOR_SLICE, validator.R6_VALIDATOR_SLICE}
        }
        data["external_lane_claims"] = [
            item
            for item in data["external_lane_claims"]
            if item.get("run_id") not in terminal_history_ids
        ]
        data["long_run_state"]["active_claims"] = [
            run_id
            for run_id in data["long_run_state"]["active_claims"]
            if run_id not in terminal_history_ids
        ]
        data["long_run_state"]["agent_leases"] = [
            lease
            for lease in data["long_run_state"]["agent_leases"]
            if lease.get("claim_id") not in terminal_history_ids
        ]
        claim = next(
            item
            for item in data["external_lane_claims"]
            if item.get("claim_id") == "ABC-R7-VALIDATOR-R4-20260803T202708+0200"
        )
        candidate_blobs = {
            path: validator._path_blob_id(REPO_ROOT / path)
            for path in claim["allowed_paths"]
        }
        command_manifest = [
            {"id": item["id"], "tier": item["tier"], "argv": item["argv"]}
            for item in claim["regression_tests"]
        ]
        node_ids = ["tests/test_integration_spine_convergence.py::pending_release"]
        bindings = {
            "candidate_set_digest_sha256": validator._candidate_set_digest(
                candidate_blobs
            ),
            "command_manifest_digest_sha256": validator._manifest_digest(
                command_manifest
            ),
            "pytest_node_manifest_digest_sha256": validator._node_manifest_digest(
                node_ids
            ),
        }
        claim.update(
            {
                "state": "released_pending_post_release_qa",
                "released_at": "2026-08-03T21:00:00+02:00",
                "review_result": validator.R4_PENDING_REVIEW_RESULT,
                "candidate_state": "frozen_for_independent_qa",
                "candidate_blobs": candidate_blobs,
                "candidate_set_digest_sha256": bindings[
                    "candidate_set_digest_sha256"
                ],
                "candidate_command_manifest_digest_sha256": bindings[
                    "command_manifest_digest_sha256"
                ],
                "candidate_pytest_node_manifest_digest_sha256": bindings[
                    "pytest_node_manifest_digest_sha256"
                ],
                "candidate_pytest_node_ids": node_ids,
                "terminal_receipt": {
                    "candidate_blobs": candidate_blobs,
                    "candidate_set_digest_sha256": bindings[
                        "candidate_set_digest_sha256"
                    ],
                    "command_manifest": command_manifest,
                    "command_manifest_digest_sha256": bindings[
                        "command_manifest_digest_sha256"
                    ],
                    "pytest_node_ids": node_ids,
                    "pytest_node_manifest_digest_sha256": bindings[
                        "pytest_node_manifest_digest_sha256"
                    ],
                    "pre_release": dict(bindings),
                    "post_release": dict(bindings),
                },
            }
        )
        data["long_run_state"]["active_claims"] = [
            run_id
            for run_id in data["long_run_state"]["active_claims"]
            if run_id != claim["run_id"]
        ]
        data["long_run_state"]["agent_leases"] = [
            lease
            for lease in data["long_run_state"]["agent_leases"]
            if lease["claim_id"] != claim["run_id"]
        ]
        return claim

    monkeypatch.setattr(validator, "_materialized_git_blob", lambda _root, _blob: True)
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    claim = pending_release(data)
    _load_round7_and_open_work(monkeypatch, data)
    errors = validator.validate(ROADMAP, REPO_ROOT)
    assert errors == []

    for field, drifted_value in (
        ("candidate_blobs", {"README.md": "0" * 40}),
        ("candidate_set_digest_sha256", "sha256:" + "0" * 64),
        ("candidate_command_manifest_digest_sha256", "sha256:" + "0" * 64),
        ("candidate_pytest_node_manifest_digest_sha256", "sha256:" + "0" * 64),
        ("candidate_pytest_node_ids", ["tests/test_integration_spine_convergence.py::drift"]),
    ):
        data = json.loads(ROADMAP.read_text(encoding="utf-8"))
        claim = pending_release(data)
        claim[field] = drifted_value
        _load_round7_and_open_work(monkeypatch, data)
        errors = validator.validate(ROADMAP, REPO_ROOT)
        assert errors == [
            "terminal R4 receipt does not bind one candidate and manifest set"
        ]

    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    claim = pending_release(data)
    claim["state"] = "released_accepted"
    claim["review_result"] = validator.R4_ACCEPTED_REVIEW_RESULT
    _load_round7_and_open_work(monkeypatch, data)
    assert validator.validate(ROADMAP, REPO_ROOT) == []

    claim["review_result"] = validator.R4_PENDING_REVIEW_RESULT
    assert validator.validate(ROADMAP, REPO_ROOT) == [
        "terminal R4 release lifecycle result is invalid"
    ]


def test_r4_public_errors_are_closed_deduplicated_and_bounded() -> None:
    public = validator._public_error_codes(["untrusted path: never echo"] * 100)

    assert public == ["validation_failed"]
    assert len(json.dumps(public).encode("utf-8")) <= validator.MAX_PUBLIC_ERROR_BYTES

    private_roadmap = Path(r"C:\private\NeverEcho\secret.json")
    result = _run(private_roadmap)
    assert result.returncode == 1
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["roadmap"] == "round7_authority"
    assert "NeverEcho" not in result.stdout
    assert "secret.json" not in result.stdout


def test_r4_callable_helpers_reject_hostile_subclasses_before_use() -> None:
    class HostileString(str):
        def replace(self, *_args, **_kwargs):
            raise RuntimeError("must not call hostile replace")

    class HostileList(list):
        def __iter__(self):
            raise RuntimeError("must not iterate hostile list")

    assert validator._parse_iso_timestamp(HostileString("2026-08-03T12:00:00+02:00")) is None
    assert validator._paths_are_canonical(HostileList(["a", "b"])) is False


def test_r5_root_materialized_receipt_binds_complete_core(monkeypatch) -> None:
    claim, root_receipt, authority = _r5_frozen_fixture()
    monkeypatch.setattr(
        validator, "_materialized_freeze_receipt_blob", lambda *_args: root_receipt
    )
    roadmap = json.loads(ROADMAP.read_text(encoding="utf-8"))
    errors: list[str] = []
    validator._validate_r5_freeze_receipt(
        claim,
        roadmap=roadmap,
        repo_root=REPO_ROOT,
        authority=authority,
        current_capture_required=True,
        terminal_phase=None,
        errors=errors,
    )
    assert errors == []

    coordinated_claim = json.loads(json.dumps(claim))
    coordinated_claim["candidate_blobs"][coordinated_claim["allowed_paths"][0]] = "b" * 40
    coordinated_claim["candidate_set_digest_sha256"] = validator._candidate_set_digest(
        coordinated_claim["candidate_blobs"]
    )
    coordinated_receipt = json.loads(json.dumps(root_receipt))
    coordinated_core = validator._r5_immutable_claim_core(coordinated_claim)
    assert coordinated_core is not None
    coordinated_receipt["immutable_claim_core"] = coordinated_core
    coordinated_receipt["immutable_claim_core_digest_sha256"] = validator._manifest_digest(
        coordinated_core
    )
    coordinated_receipt["candidate_blobs"] = coordinated_claim["candidate_blobs"]
    coordinated_receipt["candidate_set_digest_sha256"] = coordinated_claim[
        "candidate_set_digest_sha256"
    ]
    assert coordinated_receipt != root_receipt

    errors = []
    validator._validate_r5_freeze_receipt(
        coordinated_claim,
        roadmap=roadmap,
        repo_root=REPO_ROOT,
        authority=authority,
        current_capture_required=True,
        terminal_phase=None,
        errors=errors,
    )
    assert errors == ["R5 freeze receipt does not bind the complete immutable claim core"]


def test_r5_receipt_loader_rejects_unusable_receipt_objects(monkeypatch) -> None:
    claim, root_receipt, authority = _r5_frozen_fixture()
    roadmap = json.loads(ROADMAP.read_text(encoding="utf-8"))
    original_loader = validator._materialized_freeze_receipt_blob

    for unusable in (
        None,
        {"malformed": True},
        {**root_receipt, "claim_id": "another-claim"},
    ):
        monkeypatch.setattr(
            validator,
            "_materialized_freeze_receipt_blob",
            lambda *_args, value=unusable: value,
        )
        errors: list[str] = []
        validator._validate_r5_freeze_receipt(
            claim,
            roadmap=roadmap,
            repo_root=REPO_ROOT,
            authority=authority,
            current_capture_required=True,
            terminal_phase=None,
            errors=errors,
        )
        assert errors == [
            "R5 freeze receipt is missing, nonblob, malformed or outside the closed schema"
        ] or errors == ["R5 freeze receipt does not bind the complete immutable claim core"]

    class Completed:
        returncode = 0
        stdout = str(validator.MAX_ARTIFACT_BYTES + 1).encode("ascii")

    monkeypatch.setattr(
        validator, "_materialized_freeze_receipt_blob", original_loader
    )
    monkeypatch.setattr(validator, "_materialized_git_blob", lambda *_args: True)
    monkeypatch.setattr(validator.subprocess, "run", lambda *_args, **_kwargs: Completed())
    assert validator._materialized_freeze_receipt_blob(REPO_ROOT, "a" * 40) is None


def test_r5_pending_and_accepted_release_bindings_recheck_current_capture(monkeypatch) -> None:
    claim, root_receipt, authority = _r5_frozen_fixture()
    roadmap = json.loads(ROADMAP.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        validator, "_materialized_freeze_receipt_blob", lambda *_args: root_receipt
    )
    claim.update(
        {
            "state": "released_pending_post_release_qa",
            "released_at": "2026-08-03T21:40:00+02:00",
            "review_result": validator.R5_PENDING_REVIEW_RESULT,
            "terminal_verification": {
                "phase": validator.R5_PENDING_REVIEW_RESULT,
                "candidate_set_digest_sha256": root_receipt["candidate_set_digest_sha256"],
                "command_manifest_digest_sha256": root_receipt[
                    "command_manifest_digest_sha256"
                ],
                "pytest_node_manifest_digest_sha256": root_receipt[
                    "pytest_node_manifest_digest_sha256"
                ],
            },
        }
    )
    errors: list[str] = []
    validator._validate_r5_freeze_receipt(
        claim,
        roadmap=roadmap,
        repo_root=REPO_ROOT,
        authority=authority,
        current_capture_required=True,
        terminal_phase=validator.R5_PENDING_REVIEW_RESULT,
        errors=errors,
    )
    assert errors == []

    drifted_blobs = dict(authority.blob_ids)
    drifted_blobs[claim["allowed_paths"][0]] = "0" * 40
    drifted_authority = validator.AuthoritySnapshotBundle(
        documents=authority.documents,
        blob_ids=MappingProxyType(drifted_blobs),
        paths=authority.paths,
    )
    errors = []
    validator._validate_r5_freeze_receipt(
        claim,
        roadmap=roadmap,
        repo_root=REPO_ROOT,
        authority=drifted_authority,
        current_capture_required=True,
        terminal_phase=validator.R5_PENDING_REVIEW_RESULT,
        errors=errors,
    )
    assert errors == ["R5 pending receipt current capture differs from the frozen authority"]

    claim["state"] = "released_accepted"
    claim["review_result"] = validator.R5_ACCEPTED_REVIEW_RESULT
    claim["terminal_verification"]["phase"] = validator.R5_ACCEPTED_REVIEW_RESULT
    errors = []
    validator._validate_r5_freeze_receipt(
        claim,
        roadmap=roadmap,
        repo_root=REPO_ROOT,
        authority=drifted_authority,
        current_capture_required=False,
        terminal_phase=validator.R5_ACCEPTED_REVIEW_RESULT,
        errors=errors,
    )
    assert errors == []

    claim["terminal_verification"]["pytest_node_manifest_digest_sha256"] = "sha256:" + "0" * 64
    errors = []
    validator._validate_r5_freeze_receipt(
        claim,
        roadmap=roadmap,
        repo_root=REPO_ROOT,
        authority=authority,
        current_capture_required=False,
        terminal_phase=validator.R5_ACCEPTED_REVIEW_RESULT,
        errors=errors,
    )
    assert errors == ["R5 terminal verification is not bound to the Root freeze receipt"]


def test_r5_closed_cli_rejects_every_token_class_and_internal_fault(monkeypatch, capsys) -> None:
    cases = (
        ("--unknown", "never-echo"),
        ("--format", "json", "--format", "json"),
        ("--roadmap",),
        ("--format", "json", "--roadmap", "never-echo"),
        ("--format", "never-echo"),
    )
    for tokens in cases:
        result = subprocess.run(
            [sys.executable, str(VALIDATOR), *tokens],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1
        assert result.stderr == ""
        assert json.loads(result.stdout) == {
            "errors": ["invalid_cli"],
            "ok": False,
            "roadmap": "round7_authority",
        }
        assert "never-echo" not in result.stdout

    assert validator.main(["--roadmap", "\ud800", "--format", "json"]) == 1
    surrogate = capsys.readouterr()
    assert surrogate.err == ""
    assert json.loads(surrogate.out)["errors"] == ["invalid_cli"]

    def injected_failure(*_args: Any, **_kwargs: Any) -> list[str]:
        raise RuntimeError("never-echo-internal")

    monkeypatch.setattr(validator, "validate", injected_failure)
    assert validator.main(["--format", "json"]) == 1
    injected = capsys.readouterr()
    assert injected.err == ""
    assert json.loads(injected.out)["errors"] == ["internal_error"]
    assert "never-echo-internal" not in injected.out


def test_r5_synthetic_active_fixtures_replace_production_authority() -> None:
    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    factory = _current_authority_factory()
    production_ids = {
        claim["run_id"]
        for claim in data["external_lane_claims"]
        if type(claim) is dict and type(claim.get("run_id")) is str
    }
    assert "ABC-R7-VALIDATOR-R5-20260803T212415+0200" in production_ids

    factory.replace_active_authority(data)
    fixtures = factory.parallel_running_v2(
        data,
        [
            ("fixture-one", ["src/coding_agent_backend.py"]),
            ("fixture-two", ["tests/test_coding_agent_backend.py"]),
        ],
    )
    fixture_ids = {claim["run_id"] for claim in fixtures}
    assert set(data["long_run_state"]["active_claims"]) == fixture_ids
    assert fixture_ids.isdisjoint(production_ids)
    assert all(
        claim["run_id"] not in production_ids
        for claim in data["external_lane_claims"]
    )


def _r6_frozen_fixture() -> tuple[
    dict[str, Any], dict[str, dict[str, Any]], validator.AuthoritySnapshotBundle
]:
    """Build one isolated F/P/A/B-ready fixture without production authority."""

    roadmap = json.loads(ROADMAP.read_text(encoding="utf-8"))
    claim = next(
        item
        for item in roadmap["external_lane_claims"]
        if item.get("slice_id") == validator.R6_VALIDATOR_SLICE
    )
    # The checked-in R6 record is terminal history.  Rebuild the fixture from
    # the single pre-freeze pair instead of retaining any release, review,
    # receipt, terminal, or candidate-evidence authority from production.
    for field in (
        "acceptance_evidence",
        "candidate_evidence_manifest_blob",
        "candidate_evidence_path",
        "candidate_frozen_at",
        "candidate_set_digest_sha256",
        "candidate_command_manifest_digest_sha256",
        "candidate_pytest_node_manifest_digest_sha256",
        "candidate_pytest_node_ids",
        "freeze_receipt_blob",
        "released_at",
        "released_reason",
        "review_result",
        "terminal_transition_blob",
        "terminal_evidence_digest_sha256",
        "terminal_verification",
    ):
        claim.pop(field, None)
    claim["state"] = "running"
    claim["candidate_state"] = "frozen_for_independent_qa"
    claim["candidate_blobs"] = {}
    evidence_path = next(
        path for path in claim["read_only_pins"] if path.startswith("docs/plans/evidence/")
    )
    candidates = {
        path: ("a" if index == 0 else "b") * 40
        for index, path in enumerate(claim["allowed_paths"])
    }
    node_ids = ["tests/test_integration_spine_convergence.py::r6_fixture"]
    claim.update(
        {
            "candidate_blobs": candidates,
            "candidate_state": "frozen_for_independent_qa",
            "candidate_evidence_path": evidence_path,
            "candidate_evidence_manifest_blob": claim["read_only_pins"][evidence_path],
            "candidate_frozen_at": "2026-08-06T10:00:00+02:00",
            "candidate_set_digest_sha256": validator._candidate_set_digest(candidates),
            "candidate_command_manifest_digest_sha256": validator._manifest_digest(
                [
                    {"id": item["id"], "tier": item["tier"], "argv": item["argv"]}
                    for item in claim["regression_tests"]
                ]
            ),
            "candidate_pytest_node_manifest_digest_sha256": validator._node_manifest_digest(node_ids),
            "candidate_pytest_node_ids": node_ids,
            "freeze_receipt_blob": "f" * 40,
        }
    )
    evidence = {
        "candidate_blobs": candidates,
        "read_only_pins": {
            path: blob
            for path, blob in claim["read_only_pins"].items()
            if path != evidence_path
        },
        "inventory_receipt": {"mode": "fixture"},
    }
    core = validator._r6_immutable_claim_core(claim)
    commands = validator._r5_structured_command_manifest(claim)
    assert core is not None and commands is not None
    freeze = {
        "schema_version": 1,
        "kind": validator.R6_FREEZE_RECEIPT_KIND,
        "claim_id": claim["claim_id"],
        "architecture_revision": claim["architecture_revision"],
        "architecture_digest_sha256": claim["architecture_digest_sha256"],
        "authority_revision": claim["authority_revision"],
        "immutable_claim_core": core,
        "immutable_claim_core_digest_sha256": validator._manifest_digest(core),
        "candidate_blobs": candidates,
        "candidate_set_digest_sha256": claim["candidate_set_digest_sha256"],
        "candidate_evidence_path": evidence_path,
        "candidate_evidence_manifest_blob": claim["candidate_evidence_manifest_blob"],
        "dependency_pins": evidence["read_only_pins"],
        "command_manifest": commands,
        "command_manifest_digest_sha256": claim[
            "candidate_command_manifest_digest_sha256"
        ],
        "pytest_node_ids": node_ids,
        "pytest_node_manifest_digest_sha256": claim[
            "candidate_pytest_node_manifest_digest_sha256"
        ],
        "evidence_digest_sha256": validator._manifest_digest(evidence),
        "inventory_receipt": evidence["inventory_receipt"],
        "correction_ledger": claim["correction_budget"],
    }
    blob_ids = dict(claim["read_only_pins"])
    blob_ids.update(candidates)
    authority = validator.AuthoritySnapshotBundle(
        documents=MappingProxyType({evidence_path: MappingProxyType(evidence)}),
        blob_ids=MappingProxyType(blob_ids),
        paths=tuple(sorted(blob_ids)),
    )
    return claim, {claim["freeze_receipt_blob"]: freeze}, authority


def _r6_transition(
    claim: Mapping[str, Any],
    freeze: Mapping[str, Any],
    *,
    previous: str,
    phase: str,
    evidence_seed: str,
) -> dict[str, Any]:
    expected = validator._r6_transition_expectation(phase)
    assert expected is not None
    from_state, from_candidate, to_state, to_candidate, outcome = expected
    return {
        "schema_version": 1,
        "kind": validator.R6_TRANSITION_RECEIPT_KIND,
        "claim_id": claim["claim_id"],
        "architecture_revision": claim["architecture_revision"],
        "architecture_digest_sha256": claim["architecture_digest_sha256"],
        "authority_revision": claim["authority_revision"],
        "freeze_receipt_blob": claim["freeze_receipt_blob"],
        "previous_receipt_blob": previous,
        "from_state": from_state,
        "from_candidate_state": from_candidate,
        "to_state": to_state,
        "to_candidate_state": to_candidate,
        "outcome_code": outcome,
        "candidate_set_digest_sha256": freeze["candidate_set_digest_sha256"],
        "command_manifest_digest_sha256": freeze["command_manifest_digest_sha256"],
        "pytest_node_manifest_digest_sha256": freeze[
            "pytest_node_manifest_digest_sha256"
        ],
        "evidence_digest_sha256": "sha256:" + evidence_seed * 64,
        "observed_at": "2026-08-06T10:10:00+02:00",
        "correction_ledger": claim["correction_budget"],
    }


def _r6_terminalize(
    claim: dict[str, Any],
    receipts: dict[str, dict[str, Any]],
    *,
    phase: str,
) -> None:
    freeze_blob = claim["freeze_receipt_blob"]
    freeze = receipts[freeze_blob]
    pending_blob = "c" * 40
    receipts[pending_blob] = _r6_transition(
        claim,
        freeze,
        previous=freeze_blob,
        phase=validator.R6_PENDING_OUTCOME,
        evidence_seed="1",
    )
    if phase == validator.R6_PENDING_OUTCOME:
        chain_head = pending_blob
    else:
        chain_head = "d" * 40 if phase == validator.R6_ACCEPTED_OUTCOME else "e" * 40
        receipts[chain_head] = _r6_transition(
            claim,
            freeze,
            previous=pending_blob,
            phase=phase,
            evidence_seed="2" if phase == validator.R6_ACCEPTED_OUTCOME else "3",
        )
    transition = receipts[chain_head]
    to_state = transition["to_state"]
    to_candidate = transition["to_candidate_state"]
    claim.update(
        {
            "state": to_state,
            "candidate_state": to_candidate,
            "released_at": "2026-08-06T10:10:00+02:00",
            "released_reason": "fixture",
            "review_result": phase,
            "terminal_transition_blob": chain_head,
            "terminal_evidence_digest_sha256": transition["evidence_digest_sha256"],
            "terminal_verification": {
                "phase": phase,
                "freeze_receipt_blob": freeze_blob,
                "transition_receipt_blob": chain_head,
                "candidate_set_digest_sha256": transition["candidate_set_digest_sha256"],
                "command_manifest_digest_sha256": transition[
                    "command_manifest_digest_sha256"
                ],
                "pytest_node_manifest_digest_sha256": transition[
                    "pytest_node_manifest_digest_sha256"
                ],
                "evidence_digest_sha256": transition["evidence_digest_sha256"],
            },
        }
    )


def test_r6_freeze_and_three_materialized_phase_chains_are_closed(monkeypatch) -> None:
    claim, receipts, authority = _r6_frozen_fixture()
    roadmap = json.loads(ROADMAP.read_text(encoding="utf-8"))
    evidence_blob = claim["candidate_evidence_manifest_blob"]
    evidence = authority.get(claim["candidate_evidence_path"])
    assert evidence is not None
    monkeypatch.setattr(validator, "_materialized_git_blob", lambda *_args: True)
    monkeypatch.setattr(
        validator,
        "_materialized_freeze_receipt_blob",
        lambda _root, blob: receipts.get(blob),
    )
    monkeypatch.setattr(
        validator,
        "_materialized_json_blob",
        lambda _root, blob: dict(evidence) if blob == evidence_blob else None,
    )

    errors: list[str] = []
    validator._validate_r6_lifecycle(
        claim,
        roadmap=roadmap,
        repo_root=REPO_ROOT,
        authority=authority,
        current_capture_required=True,
        errors=errors,
    )
    assert errors == []

    for phase in (
        validator.R6_PENDING_OUTCOME,
        validator.R6_ACCEPTED_OUTCOME,
        validator.R6_BLOCKED_AFTER_FREEZE_OUTCOME,
    ):
        terminal_claim = json.loads(json.dumps(claim))
        terminal_receipts = json.loads(json.dumps(receipts))
        _r6_terminalize(terminal_claim, terminal_receipts, phase=phase)
        monkeypatch.setattr(
            validator,
            "_materialized_freeze_receipt_blob",
            lambda _root, blob, values=terminal_receipts: values.get(blob),
        )
        errors = []
        validator._validate_r6_lifecycle(
            terminal_claim,
            roadmap=roadmap,
            repo_root=REPO_ROOT,
            authority=None,
            current_capture_required=False,
            errors=errors,
        )
        assert errors == []


def test_r6_rejects_rewritten_chain_and_current_authority_history(monkeypatch) -> None:
    claim, receipts, authority = _r6_frozen_fixture()
    roadmap = json.loads(ROADMAP.read_text(encoding="utf-8"))
    evidence_blob = claim["candidate_evidence_manifest_blob"]
    evidence = authority.get(claim["candidate_evidence_path"])
    assert evidence is not None
    monkeypatch.setattr(validator, "_materialized_git_blob", lambda *_args: True)
    monkeypatch.setattr(
        validator,
        "_materialized_json_blob",
        lambda _root, blob: dict(evidence) if blob == evidence_blob else None,
    )

    _r6_terminalize(claim, receipts, phase=validator.R6_ACCEPTED_OUTCOME)
    receipts[claim["terminal_transition_blob"]]["previous_receipt_blob"] = claim[
        "freeze_receipt_blob"
    ]
    monkeypatch.setattr(
        validator,
        "_materialized_freeze_receipt_blob",
        lambda _root, blob: receipts.get(blob),
    )
    errors: list[str] = []
    validator._validate_r6_lifecycle(
        claim,
        roadmap=roadmap,
        repo_root=REPO_ROOT,
        authority=None,
        current_capture_required=False,
        errors=errors,
    )
    assert errors == ["R6 transition receipt is missing, nonblob, malformed or outside the closed schema"]

    data = json.loads(ROADMAP.read_text(encoding="utf-8"))
    r5 = next(item for item in data["external_lane_claims"] if validator._r5_is_validator_claim(item))
    r5["candidate_state"] = "not_frozen"
    errors = []
    validator._validate_r5_blocked_before_freeze(r5, errors)
    assert errors == ["R5 blocked-before-freeze history is not receiptless and exact"]
