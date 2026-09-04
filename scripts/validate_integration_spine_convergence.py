"""Validate the Harbor One Round-7 integration-spine roadmap."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


EXPECTED_WAVES = [
    "ROUND7-A-AUTHORITY-CONVERGENCE",
    "ROUND7-B-RUNTIME-COMPOSITION",
    "ROUND7-C-WORKER-AND-GATE-CONVERGENCE",
    "ROUND7-D-LIFECYCLE-CONVERGENCE",
    "ROUND7-E-GOLDEN-E2E",
]

EXPECTED_AUTHORING_PATHS = {
    "docs/plans/parallel-roadmap-execution-round7.json",
    "docs/plans/open-work-completion-master-roadmap.json",
    "docs/plans/multi-agent-execution-guidance.json",
    "scripts/validate_integration_spine_convergence.py",
    "tests/test_integration_spine_convergence.py",
}

EXPECTED_SUPPORTING_CLAIM_PATHS = {
    "runtime_state_validator_claim": {
        "docs/plans/parallel-roadmap-execution-round7.json",
        "scripts/validate_integration_spine_convergence.py",
        "tests/test_integration_spine_convergence.py",
    },
    "external_lane_validator_claim": {
        "docs/plans/parallel-roadmap-execution-round7.json",
        "docs/plans/open-work-completion-master-roadmap.json",
        "scripts/validate_integration_spine_convergence.py",
        "tests/test_integration_spine_convergence.py",
    },
}
EXPECTED_SUPPORTING_CLAIM_HOTFILES = {
    "runtime_state_validator_claim": set(),
    "external_lane_validator_claim": {
        "docs/plans/parallel-roadmap-execution-round7.json",
        "docs/plans/open-work-completion-master-roadmap.json",
    },
}

IMPLEMENTATION_SLICE_STATES = {
    "planned_nonclaimable",
    "running_exact_claim",
    "accepted_released",
    "blocked",
}
ACTIVE_IMPLEMENTATION_SLICE_STATES = {"running_exact_claim"}
WAVE_STATES = {"planned_nonclaimable", "running", "accepted_released", "blocked"}
GIT_BLOB_ID = re.compile(r"[0-9a-f]{40}")
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 200_000
MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
MAX_REPOSITORY_PATH_BYTES = 4096
MAX_REPOSITORY_COMPONENT_BYTES = 255
MAX_PUBLIC_ERROR_COUNT = 64
MAX_PUBLIC_ERROR_BYTES = 8192
WINDOWS_RESERVED_COMPONENTS = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
WINDOWS_LATIN1_SUPERSCRIPT_DEVICE_DIGITS = {"\u00b9", "\u00b2", "\u00b3"}

HISTORICAL_R1_EVIDENCE_PATH = "docs/plans/evidence/abc-round7-central-claim.json"
GUIDANCE_PATH = "docs/plans/multi-agent-execution-guidance.json"
OPEN_WORK_PATH = "docs/plans/open-work-completion-master-roadmap.json"
ROUND6_PATH = "docs/plans/parallel-roadmap-execution-round6.json"
HISTORICAL_R1_CENTRAL_SLICE = "R7-central-router-process-hardening-and-failed-candidate-handoff"
ABC_VERIFICATION_TIERS = {"focused", "dependency", "central", "expensive_live"}
ABC_V2_REQUIRED_FIELDS = {
    "acceptance",
    "acquired_at",
    "allowed_paths",
    "authority_revision",
    "baseline_blobs",
    "baseline_readback",
    "candidate_blobs",
    "candidate_state",
    "claim_class",
    "claim_id",
    "claim_transfer",
    "correction_budget",
    "dependencies",
    "forbidden",
    "hostile_matrix",
    "hotfiles",
    "lease_expires_at",
    "live_actions",
    "owner",
    "pytest_basetemp",
    "read_only_pins",
    "regression_tests",
    "run_id",
    "slice_id",
    "stale_invalidation",
    "state",
    "thread_id",
    "verification_tiers",
}
ABC_V2_OPTIONAL_FIELDS = {
    "acceptance_evidence",
    "candidate_evidence_manifest_blob",
    "candidate_evidence_path",
    "candidate_frozen_at",
    "candidate_set_digest_sha256",
    "candidate_command_manifest_digest_sha256",
    "candidate_pytest_node_manifest_digest_sha256",
    "candidate_pytest_node_ids",
    "released_at",
    "released_reason",
    "review_result",
    "terminal_receipt",
    "new_evidence_writer_paths",
    "architecture_revision",
    "architecture_digest_sha256",
    "freeze_receipt_blob",
    "terminal_transition_blob",
    "terminal_evidence_digest_sha256",
    "terminal_verification",
}
ABC_V2_STALE_TRIGGERS = {
    "authority_or_projection_revision_drift",
    "candidate_writer_blob_drift",
    "claim_lease_or_transfer_drift",
    "command_contract_or_basetemp_drift",
    "read_only_pin_drift",
    "rolling_inventory_member_count_digest_or_denominator_drift",
}
ROOT_PROTECTED_WRITER_PATHS = {
    GUIDANCE_PATH,
    OPEN_WORK_PATH,
    ROUND6_PATH,
    "docs/plans/parallel-roadmap-execution-round7.json",
}
ABC_V2_REQUIRED_PIN_PATHS = {
    GUIDANCE_PATH,
    ROUND6_PATH,
    "scripts/roadmap_multi_agent_guidance.py",
    "scripts/validate_parallel_roadmap_execution.py",
    "tests/test_parallel_roadmap_execution.py",
    "tests/test_roadmap_multi_agent_guidance.py",
}
ABC_V2_TRANSFER_FIELDS = {
    "from_claim_id",
    "source_released_at",
    "transferred_at",
    "gap_preserved_no_writer",
    "architecture_handoff",
}
ABC_V2_LEGACY_TERMINAL_UPGRADE_FIELDS = ABC_V2_TRANSFER_FIELDS | {
    "origin_mode",
    "source_registry",
}
ABC_V2_NEW_PRODUCT_PATHS_FIELDS = {
    "origin_mode",
    "source_registry",
    "from_claim_id",
    "source_released_at",
    "transferred_at",
    "gap_preserved_no_writer",
    "architecture_handoff",
    "new_product_writer_paths",
}
LEGACY_TERMINAL_UPGRADE_MODE = "legacy_terminal_upgrade"
LEGACY_OPEN_WORK_SOURCE_REGISTRY = "open_work.current_position.external_lane_claims"
NEW_PRODUCT_PATHS_MODE = "new_product_paths"
MATERIALIZED_REPO_GENESIS_MODE = "materialized_repo_genesis"
MATERIALIZED_REPO_GENESIS_PARTITION_MODE = (
    "materialized_repo_genesis_partition_v1"
)
MATERIALIZED_REPO_GENESIS_PARTITION_READBACK_FIELDS = {
    "schema_version",
    "mode",
    "materialized_paths",
    "materialized_readback",
    "genesis_paths",
    "genesis_readback",
}
R4_PACKET_BOUND_V2_SLICE = "R7-materialized-repo-genesis-direct-r4"
MATERIALIZED_REPO_GENESIS_PACKET_FIELDS = {
    "schema_version",
    "kind",
    "state",
    "architect",
    "slice_id",
    "architecture_revision",
    "architecture_digest_sha256",
    "packet_path",
    "packet_blob",
    "allowed_paths",
    "threat_model",
    "correction_budget",
    "stop_rule",
}
MATERIALIZED_REPO_GENESIS_ARCHITECTURE_FIELDS = {
    "acceptance",
    "allowed_paths",
    "architect",
    "architecture_revision",
    "baseline_blobs",
    "baseline_readback",
    "correction_budget",
    "forbidden",
    "hostile_matrix",
    "kind",
    "named_tests",
    "origin_mode",
    "published_round7_revision",
    "schema_version",
    "slice_id",
    "state",
    "stop_rule",
    "threat_model",
    "verification_tiers",
}
CLAIM_ORIGIN_PACKET_FIELDS = {
    "schema_version",
    "kind",
    "state",
    "architect",
    "slice_id",
    "origin_mode",
    "architecture_revision",
    "architecture_digest_sha256",
    "handoff_id",
    "allowed_paths",
    "baseline_blobs",
    "source_claim_id",
    "new_product_writer_paths",
}
CLAIM_ORIGIN_PACKET_KIND = "odysseus.abc_claim_origin_architecture"
CAPTURED_ABSENT_PATHS_READBACK_PASS = "captured_absent_paths_exact_readback_pass"
ACTIVE_FREEZE_EVIDENCE_FIELDS = {
    "schema_version",
    "kind",
    "claim_id",
    "thread_id",
    "authority_revision",
    "candidate_writer_paths",
    "baseline_blobs",
    "baseline_readback",
    "candidate_blobs",
    "candidate_state",
    "candidate_frozen_at",
    "candidate_set_digest_sha256",
    "candidate_command_manifest_digest_sha256",
    "candidate_pytest_node_manifest_digest_sha256",
    "candidate_pytest_node_ids",
    "read_only_pins",
    "stale_invalidation",
    "verification_tiers",
    "correction_budget",
    "inventory_receipt",
    "live_actions",
}
TERMINAL_RECEIPT_FIELDS = {
    "candidate_blobs",
    "candidate_set_digest_sha256",
    "command_manifest",
    "command_manifest_digest_sha256",
    "pytest_node_ids",
    "pytest_node_manifest_digest_sha256",
    "pre_release",
    "post_release",
}
TERMINAL_RECEIPT_BINDING_FIELDS = {
    "candidate_set_digest_sha256",
    "command_manifest_digest_sha256",
    "pytest_node_manifest_digest_sha256",
}
R4_TERMINAL_CLAIM_BINDING_FIELDS = {
    "candidate_blobs",
    "candidate_set_digest_sha256",
    "candidate_command_manifest_digest_sha256",
    "candidate_pytest_node_manifest_digest_sha256",
    "candidate_pytest_node_ids",
}
R4_PENDING_REVIEW_RESULT = "pre_release_verified_post_release_pending"
R4_ACCEPTED_REVIEW_RESULT = "post_release_verified"

R5_VALIDATOR_SLICE = "R7-validator-root-materialized-freeze-receipt-and-closed-cli-r5"
R5_ARCHITECTURE_PACKET = "R7-validator-root-materialized-freeze-receipt-and-closed-cli-r5"
R5_FREEZE_RECEIPT_KIND = "odysseus.abc_root_freeze_receipt_r5"
R5_FREEZE_RECEIPT_FIELDS = {
    "schema_version",
    "kind",
    "claim_id",
    "architecture_revision",
    "architecture_digest_sha256",
    "authority_revision",
    "immutable_claim_core",
    "immutable_claim_core_digest_sha256",
    "candidate_blobs",
    "candidate_set_digest_sha256",
    "candidate_evidence_path",
    "candidate_evidence_manifest_blob",
    "dependency_pins",
    "command_manifest",
    "command_manifest_digest_sha256",
    "pytest_node_ids",
    "pytest_node_manifest_digest_sha256",
    "inventory_receipt",
    "correction_ledger",
}
R5_FROZEN_CLAIM_EXTRA_FIELDS = {
    "new_evidence_writer_paths",
    "candidate_evidence_manifest_blob",
    "candidate_evidence_path",
    "candidate_frozen_at",
    "candidate_set_digest_sha256",
    "candidate_command_manifest_digest_sha256",
    "candidate_pytest_node_manifest_digest_sha256",
    "candidate_pytest_node_ids",
    "architecture_revision",
    "architecture_digest_sha256",
    "freeze_receipt_blob",
}
R5_TERMINAL_VERIFICATION_FIELDS = {
    "phase",
    "candidate_set_digest_sha256",
    "command_manifest_digest_sha256",
    "pytest_node_manifest_digest_sha256",
}
R5_IMMUTABLE_CLAIM_FIELDS = (
    ABC_V2_REQUIRED_FIELDS
    - {"state"}
    | (R5_FROZEN_CLAIM_EXTRA_FIELDS - {"freeze_receipt_blob"})
)
R5_PENDING_REVIEW_RESULT = "pre_release_verified_post_release_pending"
R5_ACCEPTED_REVIEW_RESULT = "post_release_verified"

# R6 deliberately does not reuse the R5 terminal representation.  R5 was
# released before a candidate freeze and is therefore historical, receiptless
# data.  R6 has one immutable Root-materialized freeze object (F) followed by
# a phase-specific materialized transition object (P, A, or B).  Keeping the
# two representations distinct prevents a historical terminal record from
# becoming current authority merely because it still contains old pins.
R6_VALIDATOR_SLICE = "R7-validator-freeze-and-transition-receipt-lifecycle-r6"
R6_ARCHITECTURE_PACKET = R6_VALIDATOR_SLICE
# The direct recovery deliberately reuses the R6 F/P/A/B representation, but
# its architecture packet is a separate, exact identity.  There is no alias or
# caller-selected fallback between historical and recovery authority.
R6_DIRECT_VALIDATOR_SLICE = "R7-validator-r6-direct-isolated-authority-recovery"
R6_DIRECT_ARCHITECTURE_PACKET = R6_DIRECT_VALIDATOR_SLICE
R6_FREEZE_RECEIPT_KIND = "odysseus.abc_root_freeze_receipt_r6"
R6_TRANSITION_RECEIPT_KIND = "odysseus.abc_root_transition_receipt_r6"
R6_FREEZE_RECEIPT_FIELDS = {
    "schema_version",
    "kind",
    "claim_id",
    "architecture_revision",
    "architecture_digest_sha256",
    "authority_revision",
    "immutable_claim_core",
    "immutable_claim_core_digest_sha256",
    "candidate_blobs",
    "candidate_set_digest_sha256",
    "candidate_evidence_path",
    "candidate_evidence_manifest_blob",
    "dependency_pins",
    "command_manifest",
    "command_manifest_digest_sha256",
    "pytest_node_ids",
    "pytest_node_manifest_digest_sha256",
    "evidence_digest_sha256",
    "inventory_receipt",
    "correction_ledger",
}
R6_TRANSITION_RECEIPT_FIELDS = {
    "schema_version",
    "kind",
    "claim_id",
    "architecture_revision",
    "architecture_digest_sha256",
    "authority_revision",
    "freeze_receipt_blob",
    "previous_receipt_blob",
    "from_state",
    "from_candidate_state",
    "to_state",
    "to_candidate_state",
    "outcome_code",
    "candidate_set_digest_sha256",
    "command_manifest_digest_sha256",
    "pytest_node_manifest_digest_sha256",
    "evidence_digest_sha256",
    "observed_at",
    "correction_ledger",
}
R6_FROZEN_CLAIM_EXTRA_FIELDS = {
    "new_evidence_writer_paths",
    "candidate_evidence_manifest_blob",
    "candidate_evidence_path",
    "candidate_frozen_at",
    "candidate_set_digest_sha256",
    "candidate_command_manifest_digest_sha256",
    "candidate_pytest_node_manifest_digest_sha256",
    "candidate_pytest_node_ids",
    "architecture_revision",
    "architecture_digest_sha256",
}
R6_IMMUTABLE_CLAIM_FIELDS = (
    ABC_V2_REQUIRED_FIELDS
    - {"state"}
    | R6_FROZEN_CLAIM_EXTRA_FIELDS
)
R6_TERMINAL_VERIFICATION_FIELDS = {
    "phase",
    "freeze_receipt_blob",
    "transition_receipt_blob",
    "candidate_set_digest_sha256",
    "command_manifest_digest_sha256",
    "pytest_node_manifest_digest_sha256",
    "evidence_digest_sha256",
}
R6_PENDING_OUTCOME = "post_release_qa_pending"
R6_ACCEPTED_OUTCOME = "post_release_verified"
R6_BLOCKED_AFTER_FREEZE_OUTCOME = "blocked_after_freeze"
R6_BLOCKED_BEFORE_FREEZE_OUTCOME = "blocked_before_freeze"

# R7 is intentionally a new representation: a running writer is never also a
# frozen candidate.  Root records the registration anchor outside the claim and
# transports its identity through the existing claim-transfer field.
R7_VALIDATOR_SLICE = "R7-validator-synthetic-authority-closed-lifecycle-evidence-r7"
R7_ARCHITECTURE_REVISION = "ABC-R7-VALIDATOR-R7-SOL-ARCH-v3"
R7_ARCHITECTURE_DIGEST = "sha256:e6a84571d4f8d3b0ee9acee77c98e398bc3cafa56de63e6baf8655680d175799"
R7_EVIDENCE_PATH = "docs/plans/evidence/abc-round7-validator-r7.json"
R7_ANCHOR_KIND = "odysseus.abc_root_registration_anchor_r7"
R7_ANCHOR_FIELDS = {
    "schema_version", "kind", "claim_id", "slice_id", "architecture_revision",
    "architecture_digest_sha256", "authority_revision", "allowed_paths",
    "writer_baseline_blobs", "read_only_pins", "root_only_paths",
    "inventory_receipt", "historical_and_current_correction_ledger",
}
R7_LEGAL_PAIRS = {
    ("running", "not_frozen"),
    ("released_pending_post_release_qa", "frozen_for_independent_qa"),
    ("released_accepted", "accepted"),
    ("released_blocked", "blocked_before_freeze"),
    ("released_blocked", "blocked_after_freeze"),
}


PathKey = tuple[str, ...]


class _DuplicateJsonKeyError(ValueError):
    pass


class _JsonDocumentError(ValueError):
    """Content-free JSON boundary error."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code if code in {
            "invalid_json_shape",
            "limit_exceeded",
            "malformed_json",
            "non_object_root",
            "nonfinite_json",
            "read_failed",
        } else "read_failed"
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class JsonSnapshot:
    role: str
    blob_id: str
    root: Mapping[str, Any]
    raw: bytes = field(repr=False, compare=False, default=b"")


@dataclass(frozen=True, slots=True)
class CommandSpec:
    command_id: str
    tier: str
    argv: tuple[str, ...]
    artifacts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    """Validation-time Python identity captured once for one complete claim."""

    executable: str
    prefix: str
    base_prefix: str
    executable_realpath: str


@dataclass(frozen=True, slots=True)
class AbcClaimV2:
    run_id: str
    slice_id: str
    owner: str
    allowed_paths: tuple[str, ...]
    hotfiles: tuple[str, ...]
    pins: tuple[tuple[str, str], ...]
    baselines: tuple[tuple[str, str], ...]
    commands: tuple[CommandSpec, ...]
    candidate_blobs: tuple[tuple[str, str], ...]
    candidate_state: str
    stale_invalidation: frozenset[str]
    command_manifest_digest: str | None = None
    pytest_node_manifest_digest: str | None = None
    pytest_node_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AuthoritySnapshotBundle:
    """One immutable, read-once view of every active v2 authority document."""

    documents: Mapping[str, Mapping[str, Any]]
    blob_ids: Mapping[str, str]
    paths: tuple[str, ...]
    raw_bytes: Mapping[str, bytes] = field(
        default_factory=lambda: MappingProxyType({}), repr=False, compare=False
    )
    absent_paths: tuple[str, ...] = ()
    absent_paths_readback: str = "not_applicable"

    def get(self, path: str) -> Mapping[str, Any] | None:
        return self.documents.get(path)


def _is_windows_latin1_superscript_device_alias(stem: str) -> bool:
    prefix = stem[:3]
    return (
        len(stem) == 4
        and all(ord(character) < 128 for character in prefix)
        and prefix.casefold() in {"com", "lpt"}
        and stem[3] in WINDOWS_LATIN1_SUPERSCRIPT_DEVICE_DIGITS
    )


def _repository_path_key(value: Any) -> PathKey | None:
    """Return one exact safe identity, never raising on hostile Unicode."""

    if type(value) is not str:
        return None
    try:
        if not value or "\\" in value:
            return None
        if value != unicodedata.normalize("NFC", value):
            return None
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            return None
        if len(value.encode("utf-8", errors="strict")) > MAX_REPOSITORY_PATH_BYTES:
            return None
        if value.startswith(("/", "~")) or ":" in value:
            return None
        parts = value.split("/")
        if any(
            part in {"", ".", ".."}
            or (part.startswith(".") and not (index == 0 and part == ".github"))
            or part.endswith((".", " "))
            or len(part.encode("utf-8", errors="strict"))
            > MAX_REPOSITORY_COMPONENT_BYTES
            or part.split(".", 1)[0].casefold() in WINDOWS_RESERVED_COMPONENTS
            or _is_windows_latin1_superscript_device_alias(part.split(".", 1)[0])
            for index, part in enumerate(parts)
        ):
            return None
        return tuple(part.casefold() for part in parts)
    except (UnicodeError, ValueError, OverflowError):
        return None


def _collect_repository_paths(
    values: Any, *, require_nonempty: bool = False
) -> tuple[dict[PathKey, str], bool]:
    if type(values) is not list or (require_nonempty and not values):
        return {}, False
    paths: dict[PathKey, str] = {}
    for value in values:
        key = _repository_path_key(value)
        if key is None or key in paths:
            return {}, False
        paths[key] = value
    return paths, True


def _paths_are_canonical(values: Any) -> bool:
    return (
        type(values) is list
        and all(type(value) is str for value in values)
        and values == sorted(values)
    )


def _first_path_scope_overlap(
    left: dict[PathKey, str], right: dict[PathKey, str]
) -> tuple[str, str] | None:
    for left_key, left_raw in left.items():
        for right_key, right_raw in right.items():
            shorter = min(len(left_key), len(right_key))
            if left_key[:shorter] == right_key[:shorter]:
                return left_raw, right_raw
    return None


def _has_internal_path_scope_overlap(paths: dict[PathKey, str]) -> bool:
    items = list(paths.items())
    for index, (left_key, _left_raw) in enumerate(items):
        for right_key, _right_raw in items[index + 1 :]:
            shorter = min(len(left_key), len(right_key))
            if left_key[:shorter] == right_key[:shorter]:
                return True
    return False


def _parse_iso_timestamp(value: Any) -> datetime | None:
    if type(value) is not str or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _has_strict_lease_window(acquired: Any, expires: Any) -> bool:
    acquired_at = _parse_iso_timestamp(acquired)
    expires_at = _parse_iso_timestamp(expires)
    return acquired_at is not None and expires_at is not None and acquired_at < expires_at

REQUIRED_COMPOSITION_PATHS = {
    "R7-B2-generation-and-policy-bound-retrieval": {
        "src/unified_source_index_query.py",
        "src/unified_source_index_query_contract.py",
        "tests/test_unified_source_index_query.py",
        "tests/test_unified_source_index_query_contract.py",
    },
    "R7-B4-forge-two-call-exact-read": {
        "src/knowledge_query_tool_provider.py",
        "src/knowledge_query_runtime.py",
        "src/agent_tools/knowledge_tools.py",
        "src/code_retrieval_certification.py",
        "tests/test_knowledge_query_two_call_code_retrieval.py",
        "tests/test_code_retrieval_certification.py",
    },
    "R7-A3-authority-projections-and-drift-audit": {
        "routes/roadmap_routes.py",
        "tests/test_roadmap_routes.py",
    },
    "R7-C2-fake-and-injected-worker-convergence": {
        "src/tool_execution.py",
        "src/delegate_tool.py",
        "tests/test_subagent_tool_selection.py",
        "tests/test_delegate_tool.py",
    },
    "R7-C5-worker-and-gate-composition-cutover": {
        "app.py",
        "src/agent_loop.py",
        "src/worker_runtime_composition.py",
        "src/tool_execution.py",
        "src/subagent_runtime.py",
        "src/delegate_tool.py",
        "routes/coding_agent_routes.py",
    },
}

EXTERNAL_DEPENDENCY_SOURCES = {
    "UIR-06": "docs/plans/unified-source-index-runtime-integration-roadmap.md",
    "UIR-09": "docs/plans/unified-source-index-runtime-integration-roadmap.md",
    "UIR-10": "docs/plans/unified-source-index-runtime-integration-roadmap.md",
    "CBM-07A": "docs/plans/codebase-memory-integration-roadmap.md",
    "LCG-03A": "docs/plans/lens-code-graph-roadmap.md",
    "FCA-00": "docs/plans/forge-usi-code-source-adapter-roadmap.md",
    "FCA-01": "docs/plans/forge-usi-code-source-adapter-roadmap.md",
    "FCA-02": "docs/plans/forge-usi-code-source-adapter-roadmap.md",
    "FCA-05": "docs/plans/forge-usi-code-source-adapter-roadmap.md",
    "FCA-06": "docs/plans/forge-usi-code-source-adapter-roadmap.md",
    "FCA-06A": "docs/plans/forge-usi-code-source-adapter-roadmap.md",
    "ULO-02": "docs/plans/unified-source-index-data-lifecycle-operations-roadmap.md",
    "ULO-03": "docs/plans/unified-source-index-data-lifecycle-operations-roadmap.md",
    "ULO-04": "docs/plans/unified-source-index-data-lifecycle-operations-roadmap.md",
    "ULO-05": "docs/plans/unified-source-index-data-lifecycle-operations-roadmap.md",
    "ULO-08": "docs/plans/unified-source-index-data-lifecycle-operations-roadmap.md",
    "ULO-09": "docs/plans/unified-source-index-data-lifecycle-operations-roadmap.md",
    "ULO-10": "docs/plans/unified-source-index-data-lifecycle-operations-roadmap.md",
    "ULO-11": "docs/plans/unified-source-index-data-lifecycle-operations-roadmap.md",
    "ULO-12": "docs/plans/unified-source-index-data-lifecycle-operations-roadmap.md",
    "ULO-13": "docs/plans/unified-source-index-data-lifecycle-operations-roadmap.md",
    "ULO-14": "docs/plans/unified-source-index-data-lifecycle-operations-roadmap.md",
}

REQUIRED_EXTERNAL_DEPENDENCIES = {
    "R7-B2-generation-and-policy-bound-retrieval": {
        "UIR-06",
        "UIR-09",
        "CBM-07A",
        "LCG-03A",
    },
    "R7-B4-forge-two-call-exact-read": {
        "UIR-09",
        "UIR-10",
        "FCA-00",
        "FCA-01",
        "FCA-02",
        "FCA-05",
        "FCA-06",
        "FCA-06A",
    },
    "R7-D1-immediate-query-ineligibility": {"ULO-02"},
    "R7-D2-projection-purge-and-rebuild": {"ULO-03"},
    "R7-D3-rename-delete-and-lineage-cascade": {"ULO-04", "ULO-05"},
    "R7-D4-backup-restore-and-generation-swap": {"ULO-08", "ULO-09"},
    "R7-D5-lifecycle-negative-e2e": {
        "ULO-10",
        "ULO-11",
        "ULO-12",
        "ULO-13",
        "ULO-14",
    },
}


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_nonfinite_json(_value: str) -> None:
    raise _JsonDocumentError("nonfinite_json")


def _validate_json_tree(value: Any) -> None:
    nodes = 0
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise _JsonDocumentError("limit_exceeded")
        if type(current) is dict:
            if any(type(key) is not str for key in current):
                raise _JsonDocumentError("invalid_json_shape")
            stack.extend((item, depth + 1) for item in current.values())
        elif type(current) is list:
            stack.extend((item, depth + 1) for item in current)
        elif type(current) is float:
            if not math.isfinite(current):
                raise _JsonDocumentError("nonfinite_json")
        elif type(current) not in {str, int, bool, type(None)}:
            raise _JsonDocumentError("invalid_json_shape")


def _freeze_json(value: Any) -> Any:
    if type(value) is dict:
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_thaw_json(item) for item in value]
    return value


def _json_snapshot_from_bytes(
    raw: bytes, *, role: str, max_bytes: int = MAX_JSON_BYTES
) -> JsonSnapshot:
    if (
        type(raw) is not bytes
        or type(max_bytes) is not int
        or not 0 < max_bytes <= MAX_ARTIFACT_BYTES
        or len(raw) > max_bytes
    ):
        raise _JsonDocumentError("limit_exceeded")
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json,
        )
    except _DuplicateJsonKeyError:
        raise
    except _JsonDocumentError:
        raise
    except (json.JSONDecodeError, RecursionError):
        raise _JsonDocumentError("malformed_json") from None
    except UnicodeError:
        raise _JsonDocumentError("malformed_json") from None
    if not isinstance(value, dict):
        raise _JsonDocumentError("non_object_root")
    _validate_json_tree(value)
    try:
        root = _freeze_json(value)
    except RecursionError:
        raise _JsonDocumentError("limit_exceeded") from None
    return JsonSnapshot(role=role, blob_id=_git_blob_id(raw), root=root, raw=raw)


def _load_json_snapshot(path: Path, *, role: str) -> JsonSnapshot:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_JSON_BYTES + 1)
    except (OSError, UnicodeError, ValueError):
        raise _JsonDocumentError("read_failed") from None
    return _json_snapshot_from_bytes(raw, role=role)


def _load_json_document(path: Path) -> dict[str, Any]:
    return _thaw_json(_load_json_snapshot(path, role="roadmap").root)


def _load(path: Path) -> dict[str, Any]:
    return _load_json_document(path)


def _load_auxiliary(path: Path) -> dict[str, Any]:
    """Separate seam so roadmap fixture monkeypatches cannot replace pins."""

    return _thaw_json(_load_json_snapshot(path, role=path.name).root)


_DEFAULT_LOAD = _load
_DEFAULT_LOAD_AUXILIARY = _load_auxiliary


def _capture_file_bytes(path: Path) -> tuple[bytes, str]:
    """Capture one bounded immutable artifact without a follow-up path read."""

    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_ARTIFACT_BYTES + 1)
    except (OSError, UnicodeError, ValueError):
        raise _JsonDocumentError("read_failed") from None
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise _JsonDocumentError("limit_exceeded")
    return raw, _git_blob_id(raw)


def _git_blob_id(raw: bytes) -> str:
    header = f"blob {len(raw)}\0".encode("ascii")
    return hashlib.sha1(header + raw).hexdigest()


def _path_blob_id(path: Path) -> str | None:
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_JSON_BYTES + 1)
    except (OSError, UnicodeError, ValueError):
        return None
    return _git_blob_id(raw) if len(raw) <= MAX_JSON_BYTES else None


def _materialized_git_blob(repo_root: Path, blob_id: Any) -> bool:
    if not isinstance(blob_id, str) or not GIT_BLOB_ID.fullmatch(blob_id):
        return False
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "cat-file", "-e", f"{blob_id}^{{blob}}"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def _materialized_json_blob(repo_root: Path, blob_id: Any) -> dict[str, Any] | None:
    if not _materialized_git_blob(repo_root, blob_id):
        return None
    try:
        size_result = subprocess.run(
            ["git", "-C", str(repo_root), "cat-file", "-s", blob_id],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        if size_result.returncode != 0:
            return None
        size = int(size_result.stdout.decode("ascii", errors="strict").strip())
        if not 0 <= size <= MAX_JSON_BYTES:
            return None
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "cat-file", "blob", blob_id],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        if completed.returncode != 0 or len(completed.stdout) != size:
            return None
        snapshot = _json_snapshot_from_bytes(completed.stdout, role="git_blob")
    except (
        OSError,
        subprocess.SubprocessError,
        UnicodeError,
        ValueError,
        _DuplicateJsonKeyError,
        _JsonDocumentError,
    ):
        return None
    return _thaw_json(snapshot.root)


def _materialized_freeze_receipt_blob(
    repo_root: Path, blob_id: Any
) -> dict[str, Any] | None:
    """Read one bounded receipt blob without forwarding Git output.

    Receipt identity is authority, so this function deliberately captures the
    exact bytes from ``git cat-file`` once and never substitutes HEAD or a path
    read.  Every subprocess diagnostic remains private.
    """

    if not _materialized_git_blob(repo_root, blob_id):
        return None
    try:
        size_result = subprocess.run(
            ["git", "-C", str(repo_root), "cat-file", "-s", blob_id],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        if size_result.returncode != 0:
            return None
        size = int(size_result.stdout.decode("ascii", errors="strict").strip())
        if not 0 <= size <= MAX_ARTIFACT_BYTES:
            return None
        completed = subprocess.run(
            ["git", "-C", str(repo_root), "cat-file", "blob", blob_id],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        if completed.returncode != 0 or len(completed.stdout) != size:
            return None
        snapshot = _json_snapshot_from_bytes(
            completed.stdout,
            role="r5_freeze_receipt",
            max_bytes=MAX_ARTIFACT_BYTES,
        )
    except (
        OSError,
        subprocess.SubprocessError,
        UnicodeError,
        ValueError,
        _DuplicateJsonKeyError,
        _JsonDocumentError,
    ):
        return None
    return _thaw_json(snapshot.root)


def _member_set_digest(values: list[str]) -> str:
    raw = json.dumps(
        values,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8", errors="strict")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _valid_string_list(value: Any, *, nonempty: bool = False) -> bool:
    return (
        isinstance(value, list)
        and (not nonempty or bool(value))
        and all(type(item) is str and item for item in value)
        and len(value) == len(set(value))
    )


def _valid_abc_baseline_readback(value: Any) -> bool:
    if value == "materialized_exact_binary_readback_pass":
        return True
    return (
        isinstance(value, dict)
        and value.get("pre_transfer_materialized") is True
        and value.get("post_transfer_materialized") is True
        and value.get("post_transfer_readback") == "exact_binary_readback_pass"
    )


def _materialized_repo_genesis_partition_readback(
    claim: Mapping[str, Any],
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """Return one exact baseline partition only for materialized genesis."""

    if _claim_origin_variant(claim) != MATERIALIZED_REPO_GENESIS_MODE:
        return None
    allowed = claim.get("allowed_paths")
    baselines = claim.get("baseline_blobs")
    transfer = claim.get("claim_transfer")
    readback = claim.get("baseline_readback")
    if not (
        type(allowed) is list
        and allowed == sorted(allowed)
        and _valid_string_list(allowed, nonempty=True)
        and type(baselines) is dict
        and set(baselines) == set(allowed)
        and type(transfer) is dict
        and type(readback) is dict
        and set(readback) == MATERIALIZED_REPO_GENESIS_PARTITION_READBACK_FIELDS
        and type(readback.get("schema_version")) is int
        and readback.get("schema_version") == 1
        and readback.get("mode") == MATERIALIZED_REPO_GENESIS_PARTITION_MODE
        and readback.get("materialized_readback")
        == "materialized_exact_binary_readback_pass"
        and readback.get("genesis_readback")
        == CAPTURED_ABSENT_PATHS_READBACK_PASS
    ):
        return None
    materialized = readback.get("materialized_paths")
    genesis = readback.get("genesis_paths")
    if not (
        _valid_string_list(materialized)
        and _valid_string_list(genesis)
        and materialized == sorted(materialized)
        and genesis == sorted(genesis)
        and not set(materialized) & set(genesis)
        and set(materialized) | set(genesis) == set(allowed)
        and len(materialized) + len(genesis) == len(allowed)
        and all(
            type(baselines.get(path)) is str
            and GIT_BLOB_ID.fullmatch(baselines[path])
            for path in materialized
        )
        and all(baselines.get(path) == "absent" for path in genesis)
        and transfer.get("new_product_writer_paths") == genesis
    ):
        return None
    return tuple(materialized), tuple(genesis)


def _materialized_repo_genesis_product_paths_are_allowed(paths: Any) -> bool:
    """Admit canonical source, test or non-authority docs/plans genesis paths."""

    if type(paths) is not list:
        return False
    for path in paths:
        key = _repository_path_key(path)
        if key is None:
            return False
        if key[0] in {"src", "tests"}:
            continue
        if key[:2] == ("docs", "plans") and not _protected_writer_scope((path,)):
            continue
        return False
    return True


def _protected_writer_scope(paths: tuple[str, ...]) -> bool:
    protected = {
        _repository_path_key(path) for path in ROOT_PROTECTED_WRITER_PATHS
    }
    evidence_prefix = _repository_path_key("docs/plans/evidence")
    for path in paths:
        key = _repository_path_key(path)
        if key in protected or (
            key is not None
            and evidence_prefix is not None
            and key[: len(evidence_prefix)] == evidence_prefix
        ):
            return True
    return False


def _is_evidence_json_path(path: Any) -> bool:
    """Classify Evidence receipts through the canonical path key."""

    key = _repository_path_key(path)
    prefix = _repository_path_key("docs/plans/evidence")
    return bool(
        key is not None
        and prefix is not None
        and len(key) > len(prefix)
        and key[: len(prefix)] == prefix
        and key[-1].endswith(".json")
    )


def _capture_runtime_identity(
    *, errors: list[str], label: str
) -> RuntimeIdentity | None:
    """Capture the process identity once without asserting its provenance."""

    try:
        executable = sys.executable
        prefix = sys.prefix
        base_prefix = sys.base_prefix
        if not all(
            type(value) is str
            for value in (executable, prefix, base_prefix)
        ):
            raise TypeError("non_string_runtime_identity")
        executable_realpath = os.path.realpath(executable)
        if type(executable_realpath) is not str:
            raise TypeError("non_string_runtime_realpath")
    except Exception:
        errors.append(
            f"{label} runtime identity is unavailable for closed command validation"
        )
        return None
    return RuntimeIdentity(
        executable=executable,
        prefix=prefix,
        base_prefix=base_prefix,
        executable_realpath=executable_realpath,
    )


def _runtime_identity_matches(identity: RuntimeIdentity) -> bool:
    """Compare live process identity with one immutable validation capture."""

    try:
        return bool(
            type(sys.executable) is str
            and type(sys.prefix) is str
            and type(sys.base_prefix) is str
            and sys.executable == identity.executable
            and sys.prefix == identity.prefix
            and sys.base_prefix == identity.base_prefix
            and os.path.realpath(sys.executable) == identity.executable_realpath
        )
    except Exception:
        return False


def _command_spec(
    value: Any,
    *,
    run_id: str,
    basetemps: dict[str, str],
    artifact_scope: frozenset[PathKey],
    runtime_identity: RuntimeIdentity,
    errors: list[str],
    label: str,
) -> CommandSpec | None:
    if not isinstance(value, dict) or set(value) != {"id", "tier", "argv"}:
        errors.append(f"{label} command spec has a nonclosed shape")
        return None
    command_id = value.get("id")
    tier = value.get("tier")
    argv = value.get("argv")
    if (
        type(command_id) is not str
        or not command_id
        or tier not in ABC_VERIFICATION_TIERS - {"expensive_live"}
        or not _valid_string_list(argv, nonempty=True)
    ):
        errors.append(f"{label} command identity, tier or argv is invalid")
        return None
    args = tuple(argv)
    exact_current_virtualenv = bool(
        args[0] == runtime_identity.executable
        and os.path.isabs(runtime_identity.executable)
        and runtime_identity.prefix != runtime_identity.base_prefix
    )
    if (
        not (
            args[0] == "venv\\Scripts\\python.exe"
            or exact_current_virtualenv
        )
        or any(any(token in arg for token in ("\n", "\r", ";", "&&", "||", "|", ">", "<")) for arg in args)
        or any("://" in arg for arg in args)
        or any(arg.casefold() in {"git", "push", "deploy", "curl", "wget", "pip"} for arg in args)
    ):
        errors.append(f"{label} command argv escapes the closed local Python boundary")
        return None

    safe = False
    artifacts: tuple[str, ...] = ()
    if len(args) >= 9 and args[1:6] == ("-B", "-m", "pytest", "-q", "-p") and args[6] == "no:cacheprovider":
        if args.count("--basetemp") == 1:
            marker = args.index("--basetemp")
            tests = args[7:marker]
            temp = args[marker + 1 :] if marker + 1 < len(args) else ()
            expected_temp = basetemps.get(tier)
            artifacts = tuple(test.replace("\\", "/") for test in tests)
            safe = (
                bool(tests)
                and len(temp) == 1
                and temp[0] == expected_temp
                and all(
                    (normalized := test.replace("\\", "/")).startswith("tests/")
                    and normalized.endswith(".py")
                    and _repository_path_key(normalized) is not None
                    for test in tests
                )
            )
    elif len(args) >= 5 and args[1:4] == ("-B", "-m", "py_compile"):
        artifacts = tuple(item.replace("\\", "/") for item in args[4:])
        safe = all(
            (normalized := item.replace("\\", "/")).startswith(("scripts/", "tests/"))
            and normalized.endswith(".py")
            and _repository_path_key(normalized) is not None
            for item in args[4:]
        )
    elif len(args) >= 4 and args[1] == "-B":
        script = args[2].replace("\\", "/")
        artifacts = (script,)
        remainder = args[3:]
        safe_remainders = {
            "scripts/validate_integration_spine_convergence.py": {
                ("--format", "json"),
            },
            "scripts/validate_parallel_roadmap_execution.py": {
                ("--format", "json"),
            },
            "scripts/roadmap_multi_agent_guidance.py": {
                ("--format", "json"),
                ("--roadmap", "docs\\plans\\open-work-completion-master-roadmap.json", "--format", "json"),
                ("--roadmap", "docs\\plans\\open-work-completion-master-roadmap.json", "--format", "markdown"),
            },
        }
        safe = script in {
            "scripts/roadmap_multi_agent_guidance.py",
            "scripts/validate_integration_spine_convergence.py",
            "scripts/validate_parallel_roadmap_execution.py",
        } and remainder in safe_remainders[script]
    if not safe:
        errors.append(f"{label} command argv is not an allowlisted validator invocation")
        return None
    artifact_keys = {_repository_path_key(item) for item in artifacts}
    if None in artifact_keys or not artifact_keys.issubset(artifact_scope):
        errors.append(f"{label} command artifact escapes claim writer and read-only-pin scope")
        return None
    return CommandSpec(
        command_id=command_id,
        tier=tier,
        argv=args,
        artifacts=artifacts,
    )


def _materialized_repo_genesis_packet_digest(packet: Any) -> str | None:
    """Hash one explicit genesis packet with its frozen UTF-8 representation."""

    try:
        packet = _thaw_json(packet)
        _validate_json_tree(packet)
        raw = json.dumps(
            packet,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, _JsonDocumentError, RecursionError, UnicodeError):
        return None
    if len(raw) > MAX_ARTIFACT_BYTES:
        return None
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _claim_origin_packet_binding_is_exact(
    claim: Mapping[str, Any],
    roadmap: Mapping[str, Any],
    authority: AuthoritySnapshotBundle | None = None,
) -> bool:
    """Bind an origin claim to one closed packet and its recomputed digest."""

    transfer = claim.get("claim_transfer")
    variant = _claim_origin_variant(claim)
    packets = roadmap.get("architecture_packets")
    packet = (
        packets.get(claim.get("slice_id"))
        if type(packets) is dict and type(claim.get("slice_id")) is str
        else None
    )
    if type(transfer) is not dict or type(packet) is not dict:
        return False
    packet_bound_v2 = (
        variant == "v2" and claim.get("slice_id") == R4_PACKET_BOUND_V2_SLICE
    )
    if variant == MATERIALIZED_REPO_GENESIS_MODE or packet_bound_v2:
        packet_path = packet.get("packet_path")
        packet_blob = packet.get("packet_blob")
        pins = claim.get("read_only_pins")
        architecture = authority.get(packet_path) if (
            authority is not None and type(packet_path) is str
        ) else None
        architecture = (
            _thaw_json(architecture)
            if isinstance(architecture, Mapping)
            else None
        )
        recomputed_digest = _materialized_repo_genesis_packet_digest(architecture)
        allowed = claim.get("allowed_paths")
        baselines = claim.get("baseline_blobs")
        revision = claim.get("authority_revision")
        candidate_evidence_path = claim.get("candidate_evidence_path")
        packet_key = _repository_path_key(packet_path)
        candidate_evidence_key = _repository_path_key(candidate_evidence_path)
        evidence_prefix_key = _repository_path_key("docs/plans/evidence")
        packet_evidence_overlap = (
            _first_path_scope_overlap(
                {packet_key: packet_path},
                {candidate_evidence_key: candidate_evidence_path},
            )
            if packet_key is not None and candidate_evidence_key is not None
            else ("invalid", "invalid")
        )
        scoped_paths, scoped_paths_valid = _collect_repository_paths(
            [packet_path, *(allowed if type(allowed) is list else [])]
            if type(packet_path) is str
            else []
        )
        packet_raw = (
            authority.raw_bytes.get(packet_path)
            if authority is not None and type(packet_path) is str
            else None
        )
        architecture_budget = (
            architecture.get("correction_budget")
            if type(architecture) is dict
            else None
        )
        architecture_tiers = (
            architecture.get("verification_tiers")
            if type(architecture) is dict
            else None
        )
        return bool(
            set(packet) == MATERIALIZED_REPO_GENESIS_PACKET_FIELDS
            and packet.get("schema_version") == 1
            and packet.get("kind") == "odysseus.sol_architecture_reference"
            and packet.get("state") == "frozen"
            and type(packet.get("architect")) is str
            and packet["architect"].startswith("root/Sol")
            and packet.get("slice_id") == claim.get("slice_id")
            and packet.get("architecture_revision")
            == claim.get("architecture_revision")
            and packet.get("architecture_digest_sha256")
            == claim.get("architecture_digest_sha256")
            and type(packet.get("architecture_digest_sha256")) is str
            and re.fullmatch(r"sha256:[0-9a-f]{64}", packet["architecture_digest_sha256"])
            and type(packet_path) is str
            and packet_path.startswith("docs/plans/")
            and packet_key is not None
            and type(candidate_evidence_path) is str
            and candidate_evidence_path.startswith("docs/plans/evidence/")
            and candidate_evidence_key is not None
            and evidence_prefix_key is not None
            and packet_key[: len(evidence_prefix_key)] != evidence_prefix_key
            and candidate_evidence_key[: len(evidence_prefix_key)]
            == evidence_prefix_key
            and packet_evidence_overlap is None
            and scoped_paths_valid
            and not _has_internal_path_scope_overlap(scoped_paths)
            and type(packet_blob) is str
            and GIT_BLOB_ID.fullmatch(packet_blob)
            and type(pins) is dict
            and pins.get(packet_path) == packet_blob
            and authority is not None
            and authority.blob_ids.get(packet_path) == packet_blob
            and type(packet_raw) is bytes
            and _git_blob_id(packet_raw) == packet_blob
            and type(architecture) is dict
            and set(architecture)
            == MATERIALIZED_REPO_GENESIS_ARCHITECTURE_FIELDS
            and architecture.get("schema_version") == 1
            and architecture.get("kind")
            == "odysseus.sol_materialized_repo_genesis_architecture"
            and architecture.get("state") == "frozen"
            and type(architecture.get("architect")) is str
            and architecture["architect"].startswith("root/Sol")
            and architecture.get("slice_id") == claim.get("slice_id")
            and architecture.get("origin_mode")
            == ("v2" if packet_bound_v2 else MATERIALIZED_REPO_GENESIS_MODE)
            and architecture.get("architecture_revision")
            == claim.get("architecture_revision")
            and type(revision) is dict
            and architecture.get("published_round7_revision")
            == revision.get("claimed_against_round7_revision")
            and architecture.get("allowed_paths") == allowed
            and architecture.get("baseline_blobs") == baselines
            and architecture.get("baseline_readback")
            == claim.get("baseline_readback")
            and _valid_string_list(architecture.get("acceptance"), nonempty=True)
            and _valid_string_list(architecture.get("forbidden"), nonempty=True)
            and _valid_string_list(architecture.get("hostile_matrix"), nonempty=True)
            and _valid_string_list(architecture.get("named_tests"), nonempty=True)
            and type(architecture.get("threat_model")) is str
            and bool(architecture.get("threat_model"))
            and type(architecture_budget) is dict
            and set(architecture_budget)
            == {"failure_class", "limit", "on_repeat", "used"}
            and type(architecture_budget.get("failure_class")) is str
            and architecture_budget.get("limit") == 1
            and architecture_budget.get("used") == 0
            and type(architecture_budget.get("on_repeat")) is str
            and type(architecture_tiers) is dict
            and set(architecture_tiers)
            == {"focused", "dependency", "central", "expensive_live", "independent_qa", "sol_review"}
            and all(type(value) is str and value for value in architecture_tiers.values())
            and type(architecture.get("stop_rule")) is str
            and bool(architecture.get("stop_rule"))
            and recomputed_digest == packet.get("architecture_digest_sha256")
            and recomputed_digest == claim.get("architecture_digest_sha256")
            and packet.get("architect") == architecture.get("architect")
            and packet.get("allowed_paths") == architecture.get("allowed_paths")
            and packet.get("threat_model") == architecture.get("threat_model")
            and packet.get("correction_budget")
            == architecture.get("correction_budget")
            and packet.get("stop_rule") == architecture.get("stop_rule")
        )
    digest = packet.get("architecture_digest_sha256")
    digest_basis = dict(packet)
    digest_basis.pop("architecture_digest_sha256", None)
    expected_origin = (
        "control_compatibility"
        if claim.get("slice_id") == "R7-claim-origin-compatibility" and variant == "v2"
        else variant
    )
    expected_source = (
        transfer.get("from_claim_id")
        if variant in {"v2", LEGACY_TERMINAL_UPGRADE_MODE}
        else None
    )
    expected_new_paths = (
        transfer.get("new_product_writer_paths")
        if variant in {NEW_PRODUCT_PATHS_MODE, MATERIALIZED_REPO_GENESIS_MODE}
        else []
    )
    return bool(
        set(packet) == CLAIM_ORIGIN_PACKET_FIELDS
        and packet.get("schema_version") == 1
        and packet.get("kind") == CLAIM_ORIGIN_PACKET_KIND
        and packet.get("state") == "frozen"
        and type(packet.get("architect")) is str
        and packet["architect"].startswith("root/Sol")
        and packet.get("slice_id") == claim.get("slice_id")
        and packet.get("origin_mode") == expected_origin
        and packet.get("architecture_revision") == claim.get("architecture_revision")
        and packet.get("architecture_digest_sha256")
        == claim.get("architecture_digest_sha256")
        and type(digest) is str
        and re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
        and _manifest_digest(digest_basis) == digest
        and packet.get("handoff_id") == transfer.get("architecture_handoff")
        and packet.get("allowed_paths") == claim.get("allowed_paths")
        and packet.get("baseline_blobs") == claim.get("baseline_blobs")
        and packet.get("source_claim_id") == expected_source
        and packet.get("new_product_writer_paths") == expected_new_paths
    )


def _open_work_legacy_source(
    authority: AuthoritySnapshotBundle, source_id: str
) -> dict[str, Any] | None:
    """Resolve one legacy source only from the captured Open Work projection."""

    open_work = authority.get(OPEN_WORK_PATH)
    current_position = (
        open_work.get("current_position") if isinstance(open_work, Mapping) else None
    )
    projected = (
        current_position.get("external_lane_claims")
        if isinstance(current_position, Mapping)
        else None
    )
    if type(projected) is not tuple:
        return None
    matches = [
        item
        for item in projected
        if isinstance(item, Mapping) and item.get("run_id") == source_id
    ]
    if len(matches) != 1:
        return None
    candidate = _thaw_json(matches[0])
    return candidate if type(candidate) is dict else None


def _new_product_paths_are_unclaimed(
    allowed: tuple[str, ...],
    *,
    run_id: str,
    claims_by_run_id: Mapping[str, dict[str, Any]],
    authority: AuthoritySnapshotBundle,
) -> bool:
    """Require one exact current mirror and reject every foreign overlap."""

    allowed_keys, allowed_valid = _collect_repository_paths(list(allowed))
    current = claims_by_run_id.get(run_id)
    if not (
        allowed_valid
        and type(current) is dict
        and current.get("run_id") == run_id
        and current.get("state") == "running"
        and current.get("allowed_paths") == list(allowed)
    ):
        return False

    legal_states = {
                "running",
                "released_pending_post_release_qa",
                "released_accepted",
                "released_blocked",
    }

    def invalid_or_overlapping(claims: Any, *, current_mirror_required: bool) -> bool:
        if not isinstance(claims, (list, tuple)):
            return True
        current_mirrors = 0
        for raw in claims:
            other = _thaw_json(raw) if isinstance(raw, Mapping) else None
            if not (
                type(other) is dict
                and type(other.get("run_id")) is str
                and bool(other["run_id"])
                and other.get("state") in legal_states
                and _paths_are_canonical(other.get("allowed_paths"))
                and other.get("allowed_paths")
            ):
                return True
            other_paths, other_valid = _collect_repository_paths(
                other.get("allowed_paths")
            )
            if not other_valid:
                return True
            if other["run_id"] == run_id:
                if other != current:
                    return True
                current_mirrors += 1
                continue
            if _first_path_scope_overlap(allowed_keys, other_paths):
                return True
        return current_mirror_required and current_mirrors != 1

    open_work = authority.get(OPEN_WORK_PATH)
    current_position = (
        open_work.get("current_position") if isinstance(open_work, Mapping) else None
    )
    projected = (
        current_position.get("external_lane_claims")
        if isinstance(current_position, Mapping)
        else None
    )
    round7_claims = []
    for key, value in claims_by_run_id.items():
        if type(key) is not str or not key or not isinstance(value, Mapping):
            return False
        thawed = _thaw_json(value)
        if type(thawed) is not dict or thawed.get("run_id") != key:
            return False
        round7_claims.append(thawed)
    return not invalid_or_overlapping(
        round7_claims, current_mirror_required=True
    ) and not invalid_or_overlapping(projected, current_mirror_required=True)


def _claim_origin_variant(
    claim: Mapping[str, Any]
) -> str | None:
    """Classify the exact closed transfer shape without aliasing v2."""

    transfer = claim.get("claim_transfer")
    if type(transfer) is not dict:
        return None
    if set(transfer) == ABC_V2_TRANSFER_FIELDS:
        return "v2"
    if (
        set(transfer) == ABC_V2_LEGACY_TERMINAL_UPGRADE_FIELDS
        and transfer.get("origin_mode") == LEGACY_TERMINAL_UPGRADE_MODE
        and transfer.get("source_registry") == LEGACY_OPEN_WORK_SOURCE_REGISTRY
    ):
        return LEGACY_TERMINAL_UPGRADE_MODE
    if (
        set(transfer) == ABC_V2_NEW_PRODUCT_PATHS_FIELDS
        and transfer.get("origin_mode") == NEW_PRODUCT_PATHS_MODE
        and transfer.get("source_registry") is None
        and transfer.get("from_claim_id") is None
        and transfer.get("source_released_at") is None
    ):
        return NEW_PRODUCT_PATHS_MODE
    if (
        set(transfer) == ABC_V2_NEW_PRODUCT_PATHS_FIELDS
        and transfer.get("origin_mode") == MATERIALIZED_REPO_GENESIS_MODE
        and transfer.get("source_registry") is None
        and transfer.get("from_claim_id") is None
        and transfer.get("source_released_at") is None
    ):
        return MATERIALIZED_REPO_GENESIS_MODE
    return None


def _validate_abc_claim_v2(
    claim: dict[str, Any],
    *,
    index: int,
    repo_root: Path,
    authority: AuthoritySnapshotBundle,
    authority_revision: Any,
    claims_by_run_id: Mapping[str, dict[str, Any]],
    active_run_ids: set[str],
    leases_by_claim: Mapping[str, list[dict[str, Any]]],
    errors: list[str],
    roadmap: Mapping[str, Any] | None = None,
) -> AbcClaimV2 | None:
    label = f"external ABC claim {index}"
    raw_allowed = claim.get("allowed_paths")
    if (
        type(raw_allowed) is list
        and all(type(path) is str for path in raw_allowed)
        and _protected_writer_scope(tuple(raw_allowed))
        and not (
            type(claim.get("owner")) is str
            and claim["owner"].startswith("root/Sol")
        )
    ):
        errors.append(f"{label} protected writer scope requires root/Sol ownership")
    keys = set(claim)
    if not ABC_V2_REQUIRED_FIELDS.issubset(keys) or keys - (
        ABC_V2_REQUIRED_FIELDS | ABC_V2_OPTIONAL_FIELDS
    ):
        errors.append(f"{label} fields are missing, aliased or outside the closed v2 schema")
        return None
    run_id = claim.get("run_id")
    if (
        type(run_id) is not str
        or not run_id
        or claim.get("claim_id") != run_id
        or claim.get("claim_class") != "repo_claim_v2"
        or type(claim.get("slice_id")) is not str
        or not claim.get("slice_id")
        or type(claim.get("owner")) is not str
        or not claim.get("owner")
        or type(claim.get("thread_id")) is not str
        or not claim.get("thread_id")
        or claim.get("state") != "running"
    ):
        errors.append(f"{label} typed identity is invalid")
        return None

    for field, nonempty in (
        ("dependencies", False),
        ("allowed_paths", True),
        ("hotfiles", False),
        ("hostile_matrix", True),
        ("acceptance", True),
        ("forbidden", True),
        ("stale_invalidation", True),
    ):
        if not _valid_string_list(claim.get(field), nonempty=nonempty):
            errors.append(f"{label} {field} must be an explicit duplicate-free string list")
    allowed = tuple(claim.get("allowed_paths", []))
    hotfiles = tuple(claim.get("hotfiles", []))
    allowed_keys, allowed_valid = _collect_repository_paths(list(allowed), require_nonempty=True)
    if (
        not allowed_valid
        or _has_internal_path_scope_overlap(allowed_keys)
        or list(allowed) != sorted(allowed)
        or not set(hotfiles).issubset(allowed)
        or list(hotfiles) != sorted(hotfiles)
    ):
        errors.append(f"{label} writer paths or hotfiles are not canonical and exact")
    if _protected_writer_scope(allowed) and not claim["owner"].startswith("root/Sol"):
        errors.append(f"{label} protected writer scope requires root/Sol ownership")

    pins = claim.get("read_only_pins")
    if type(pins) is not dict:
        errors.append(f"{label} read_only_pins must be an explicit object")
        pins = {}
    for pin_path, blob_id in pins.items():
        if (
            _repository_path_key(pin_path) is None
            or type(blob_id) is not str
            or not GIT_BLOB_ID.fullmatch(blob_id)
            or authority.blob_ids.get(pin_path) != blob_id
        ):
            errors.append(f"{label} read-only pin is stale or malformed")
    evidence_pin_paths = {
        path
        for path in pins
        if _is_evidence_json_path(path)
    }
    if len(evidence_pin_paths) != 1 or not ABC_V2_REQUIRED_PIN_PATHS.issubset(pins):
        errors.append(
            f"{label} read-only pin set is missing mandatory pins or exact Evidence identity"
        )
    baselines = claim.get("baseline_blobs")
    if type(baselines) is not dict or set(baselines) != set(allowed):
        errors.append(f"{label} baseline_blobs must cover allowed_paths exactly")
        baselines = {}
    elif any(
        blob_id != "absent" and not _materialized_git_blob(repo_root, blob_id)
        for blob_id in baselines.values()
    ):
        errors.append(f"{label} baseline blob is not materialized")
    absent_paths = {
        path for path, blob_id in baselines.items() if blob_id == "absent"
    }
    new_evidence_paths = claim.get("new_evidence_writer_paths", [])
    evidence_prefix = _repository_path_key("docs/plans/evidence")
    new_evidence_keys, new_evidence_valid = _collect_repository_paths(
        new_evidence_paths
    )
    transfer_variant = _claim_origin_variant(claim)
    materialized_partition = (
        _materialized_repo_genesis_partition_readback(claim)
        if transfer_variant == MATERIALIZED_REPO_GENESIS_MODE
        else None
    )
    if transfer_variant in {NEW_PRODUCT_PATHS_MODE, MATERIALIZED_REPO_GENESIS_MODE}:
        transfer = claim.get("claim_transfer")
        product_paths = transfer.get("new_product_writer_paths") if type(transfer) is dict else None
        product_keys, product_valid = _collect_repository_paths(product_paths)
        candidate_state = claim.get("candidate_state")
        unfrozen_absence_is_exact = bool(
            candidate_state == "not_frozen"
            and authority.absent_paths_readback
            == CAPTURED_ABSENT_PATHS_READBACK_PASS
            and set(product_paths or ()).issubset(set(authority.absent_paths))
            and all(path not in authority.blob_ids for path in product_paths or ())
        )
        frozen_current_is_exact = bool(
            candidate_state == "frozen_for_independent_qa"
            and _frozen_new_product_capture_paths(claim)
            == (
                allowed
                if transfer_variant == NEW_PRODUCT_PATHS_MODE
                else tuple(sorted(absent_paths))
            )
            and set(product_paths or ()).issubset(set(authority.blob_ids))
            and set(product_paths or ()).issubset(set(authority.raw_bytes))
            and not set(product_paths or ()) & set(authority.absent_paths)
            and all(
                type(authority.raw_bytes.get(path)) is bytes
                and authority.blob_ids.get(path)
                == _git_blob_id(authority.raw_bytes[path])
                and authority.blob_ids.get(path)
                == claim.get("candidate_blobs", {}).get(path)
                for path in product_paths or ()
            )
        )
        if not (
            new_evidence_valid
            and new_evidence_paths == []
            and product_valid
            and product_paths == sorted(product_paths)
            and (
                product_paths == list(allowed)
                if transfer_variant == NEW_PRODUCT_PATHS_MODE
                else set(product_paths) == absent_paths
            )
            and set(product_paths) == absent_paths
            and (
                _materialized_repo_genesis_product_paths_are_allowed(product_paths)
                if transfer_variant == MATERIALIZED_REPO_GENESIS_MODE
                else all(key and key[0] in {"src", "tests"} for key in product_keys)
            )
            and (not absent_paths or unfrozen_absence_is_exact or frozen_current_is_exact)
            and _new_product_paths_are_unclaimed(
                allowed,
                run_id=run_id,
                claims_by_run_id=claims_by_run_id,
                authority=authority,
            )
        ):
            errors.append(
                f"{label} new-product absent baseline is not explicit and captured"
            )
    elif (
        not new_evidence_valid
        or new_evidence_paths != sorted(new_evidence_paths)
        or set(new_evidence_paths) != absent_paths
        or any(
            evidence_prefix is None
            or key[: len(evidence_prefix)] != evidence_prefix
            for key in new_evidence_keys
        )
    ):
        errors.append(
            f"{label} absent baseline is reserved for explicit new Evidence writers"
        )
    if (
        transfer_variant == NEW_PRODUCT_PATHS_MODE
        and claim.get("baseline_readback")
        != CAPTURED_ABSENT_PATHS_READBACK_PASS
    ):
        errors.append(f"{label} new-product baseline readback is not exact")
    elif transfer_variant == MATERIALIZED_REPO_GENESIS_MODE and materialized_partition is None:
        errors.append(f"{label} baseline readback evidence is invalid")
    elif (
        transfer_variant not in {NEW_PRODUCT_PATHS_MODE, MATERIALIZED_REPO_GENESIS_MODE}
        and not _valid_abc_baseline_readback(claim.get("baseline_readback"))
    ):
        errors.append(f"{label} baseline readback evidence is invalid")

    revision = claim.get("authority_revision")
    if not (
        type(revision) is dict
        and set(revision) == {"claimed_against_round7_revision", "registered_round7_revision"}
        and type(revision.get("claimed_against_round7_revision")) is int
        and type(revision.get("registered_round7_revision")) is int
        and revision["registered_round7_revision"] == revision["claimed_against_round7_revision"] + 1
        and revision["registered_round7_revision"] == authority_revision
    ):
        errors.append(f"{label} authority revision is invalid")
    transfer = claim.get("claim_transfer")
    transfer_valid = transfer_variant is not None
    if transfer_variant == "v2" and type(transfer) is dict:
        claimed_revision = (
            revision.get("claimed_against_round7_revision")
            if isinstance(revision, dict)
            else None
        )
        source_id = transfer.get("from_claim_id")
        source = claims_by_run_id.get(source_id) if isinstance(source_id, str) else None
        source_released = _parse_iso_timestamp(transfer.get("source_released_at"))
        transferred = _parse_iso_timestamp(transfer.get("transferred_at"))
        acquired = _parse_iso_timestamp(claim.get("acquired_at"))
        source_allowed = source.get("allowed_paths") if isinstance(source, dict) else None
        source_authority = source.get("authority_revision") if isinstance(source, dict) else None
        if isinstance(source_authority, dict):
            source_authority = source_authority.get("registered_round7_revision")
        transfer_valid = bool(
            source_id
            and source_id != run_id
            and isinstance(source, dict)
            and source.get("state") in {"released_accepted", "released_blocked"}
            and source.get("released_at") == transfer.get("source_released_at")
            and source_id not in active_run_ids
            and not leases_by_claim.get(source_id)
            and transfer.get("gap_preserved_no_writer") is True
            and type(transfer.get("architecture_handoff")) is str
            and bool(transfer.get("architecture_handoff"))
            and source_released is not None
            and transferred is not None
            and acquired is not None
            and source_released <= transferred <= acquired
            and source_allowed == list(allowed)
            and type(source_authority) is int
            and type(claimed_revision) is int
            and source_authority < claimed_revision
        )
        if claim.get("slice_id") == "R7-claim-origin-compatibility":
            transfer_valid = transfer_valid and _claim_origin_packet_binding_is_exact(
                claim, roadmap or {}
            )
        if claim.get("slice_id") == R4_PACKET_BOUND_V2_SLICE:
            source_candidates = (
                source.get("candidate_blobs") if isinstance(source, dict) else None
            )
            transfer_valid = bool(
                transfer_valid
                and source.get("candidate_state") == "frozen_for_independent_qa"
                and type(source_candidates) is dict
                and source_candidates == baselines
                and set(source_candidates) == set(allowed)
                and _claim_origin_packet_binding_is_exact(
                    claim, roadmap or {}, authority
                )
            )
    elif transfer_variant == LEGACY_TERMINAL_UPGRADE_MODE and type(transfer) is dict:
        source_id = transfer.get("from_claim_id")
        source = claims_by_run_id.get(source_id) if type(source_id) is str else None
        mirrored_source = _open_work_legacy_source(authority, source_id) if type(source_id) is str else None
        source_released = _parse_iso_timestamp(transfer.get("source_released_at"))
        transferred = _parse_iso_timestamp(transfer.get("transferred_at"))
        acquired = _parse_iso_timestamp(claim.get("acquired_at"))
        source_candidates = source.get("candidate_writer_blobs") if type(source) is dict else None
        transfer_valid = bool(
            source_id
            and source_id != run_id
            and type(source) is dict
            and source == mirrored_source
            and source.get("state") in {"released_accepted", "released_blocked"}
            and "claim_class" not in source
            and "authority_revision" not in source
            and source.get("released_at") == transfer.get("source_released_at")
            and source_id not in active_run_ids
            and not leases_by_claim.get(source_id)
            and transfer.get("gap_preserved_no_writer") is True
            and type(transfer.get("architecture_handoff")) is str
            and bool(transfer.get("architecture_handoff"))
            and source_released is not None
            and transferred is not None
            and acquired is not None
            and source_released <= transferred <= acquired
            and source.get("allowed_paths") == list(allowed)
            and type(source_candidates) is dict
            and source_candidates == baselines
            and set(source_candidates) == set(allowed)
            and all(
                type(blob_id) is str
                and GIT_BLOB_ID.fullmatch(blob_id)
                and _materialized_git_blob(repo_root, blob_id)
                for path, blob_id in source_candidates.items()
            )
            and not absent_paths
            and _claim_origin_packet_binding_is_exact(
                claim, roadmap or {}, authority
            )
        )
    elif transfer_variant == NEW_PRODUCT_PATHS_MODE and type(transfer) is dict:
        transferred = _parse_iso_timestamp(transfer.get("transferred_at"))
        acquired = _parse_iso_timestamp(claim.get("acquired_at"))
        transfer_valid = bool(
            transfer.get("gap_preserved_no_writer") is True
            and type(transfer.get("architecture_handoff")) is str
            and bool(transfer.get("architecture_handoff"))
            and transferred is not None
            and acquired is not None
            and transferred <= acquired
            and _claim_origin_packet_binding_is_exact(claim, roadmap or {})
        )
    elif transfer_variant == MATERIALIZED_REPO_GENESIS_MODE and type(transfer) is dict:
        transferred = _parse_iso_timestamp(transfer.get("transferred_at"))
        acquired = _parse_iso_timestamp(claim.get("acquired_at"))
        product_paths = transfer.get("new_product_writer_paths")
        product_keys, product_valid = _collect_repository_paths(product_paths)
        unfrozen_absence_is_exact = bool(
            claim.get("candidate_state") == "not_frozen"
            and authority.absent_paths_readback
            == CAPTURED_ABSENT_PATHS_READBACK_PASS
            and set(product_paths or ()).issubset(set(authority.absent_paths))
            and all(path not in authority.blob_ids for path in product_paths or ())
        )
        frozen_current_is_exact = bool(
            claim.get("candidate_state") == "frozen_for_independent_qa"
            and _frozen_new_product_capture_paths(claim)
            == tuple(sorted(absent_paths))
            and set(product_paths or ()).issubset(set(authority.blob_ids))
            and set(product_paths or ()).issubset(set(authority.raw_bytes))
            and not set(product_paths or ()) & set(authority.absent_paths)
            and all(
                type(authority.raw_bytes.get(path)) is bytes
                and authority.blob_ids.get(path)
                == _git_blob_id(authority.raw_bytes[path])
                and authority.blob_ids.get(path)
                == claim.get("candidate_blobs", {}).get(path)
                for path in product_paths or ()
            )
        )
        materialized_paths = (
            materialized_partition[0] if materialized_partition is not None else ()
        )
        registration_bytes_are_exact = bool(
            claim.get("candidate_state") == "frozen_for_independent_qa"
            or (
                claim.get("candidate_state") == "not_frozen"
                and materialized_partition is not None
                and all(
                    type(authority.raw_bytes.get(path)) is bytes
                    and authority.blob_ids.get(path) == baselines.get(path)
                    and authority.blob_ids.get(path)
                    == _git_blob_id(authority.raw_bytes[path])
                    for path in materialized_paths
                )
            )
        )
        transfer_valid = bool(
            transfer.get("source_registry") is None
            and transfer.get("from_claim_id") is None
            and transfer.get("source_released_at") is None
            and product_valid
            and product_paths == sorted(product_paths)
            and set(product_paths) == absent_paths
            and materialized_partition is not None
            and tuple(product_paths) == materialized_partition[1]
            and _materialized_repo_genesis_product_paths_are_allowed(product_paths)
            and (not absent_paths or unfrozen_absence_is_exact or frozen_current_is_exact)
            and transfer.get("gap_preserved_no_writer") is True
            and type(transfer.get("architecture_handoff")) is str
            and bool(transfer.get("architecture_handoff"))
            and transferred is not None
            and acquired is not None
            and transferred <= acquired
            and registration_bytes_are_exact
            and _new_product_paths_are_unclaimed(
                allowed,
                run_id=run_id,
                claims_by_run_id=claims_by_run_id,
                authority=authority,
            )
            and _claim_origin_packet_binding_is_exact(
                claim, roadmap or {}, authority
            )
        )
    if not transfer_valid:
        errors.append(f"{label} claim transfer is not exact, terminal and revision-bound")
    if not _has_strict_lease_window(claim.get("acquired_at"), claim.get("lease_expires_at")):
        errors.append(f"{label} lease window is invalid")

    basetemps = claim.get("pytest_basetemp")
    if type(basetemps) is not dict or not basetemps:
        errors.append(f"{label} pytest basetemp map is missing")
        basetemps = {}
    elif any(
        type(tier) is not str
        or tier not in ABC_VERIFICATION_TIERS - {"expensive_live"}
        or type(path) is not str
        or not path.startswith(f".abc-test-tmp/{run_id}/")
        or "\\" in path
        or ".." in path.split("/")
        for tier, path in basetemps.items()
    ) or len(set(basetemps.values())) != len(basetemps):
        errors.append(f"{label} pytest basetemp map is not deterministic and claim-scoped")
    commands: list[CommandSpec] = []
    artifact_scope = frozenset(
        key
        for path in (*allowed, *pins.keys())
        if (key := _repository_path_key(path)) is not None
    )
    regression_tests = claim.get("regression_tests")
    if type(regression_tests) is not list or not regression_tests:
        errors.append(f"{label} regression_tests must contain structured command specs")
    else:
        runtime_identity = _capture_runtime_identity(errors=errors, label=label)
        runtime_drifted = False
        if runtime_identity is not None:
            for command in regression_tests:
                if not _runtime_identity_matches(runtime_identity):
                    errors.append(
                        f"{label} runtime identity drifted during claim command validation"
                    )
                    runtime_drifted = True
                    break
                parsed = _command_spec(
                    command,
                    run_id=run_id,
                    basetemps=basetemps,
                    artifact_scope=artifact_scope,
                    runtime_identity=runtime_identity,
                    errors=errors,
                    label=label,
                )
                if parsed is not None:
                    commands.append(parsed)
            if (
                not runtime_drifted
                and not _runtime_identity_matches(runtime_identity)
            ):
                errors.append(
                    f"{label} runtime identity drifted during claim command validation"
                )
                runtime_drifted = True
        if runtime_drifted:
            commands.clear()
        if len({item.command_id for item in commands}) != len(commands):
            errors.append(f"{label} command ids are duplicated")
        if {item.tier for item in commands} != {"focused", "dependency", "central"}:
            errors.append(f"{label} command specs do not cover focused, dependency and central tiers")
        pytest_tiers = {
            item.tier
            for item in commands
            if len(item.argv) > 3 and item.argv[1:4] == ("-B", "-m", "pytest")
        }
        if set(basetemps) != pytest_tiers:
            errors.append(f"{label} basetemp map does not bind every pytest command tier exactly")

    tiers = claim.get("verification_tiers")
    if type(tiers) is not dict or set(tiers) != ABC_VERIFICATION_TIERS or not all(
        type(value) is str and value for value in tiers.values()
    ) or tiers.get("expensive_live") != "not_run_user_gated":
        errors.append(f"{label} verification tiers are incomplete")
    budget = claim.get("correction_budget")
    if not (
        type(budget) is dict
        and set(budget) == {"limit_per_invariant", "used", "failure_class", "after_repeat"}
        and budget.get("limit_per_invariant") == 1
        and type(budget.get("used")) is int
        and 0 <= budget["used"] <= 1
        and (budget.get("failure_class") is None or type(budget.get("failure_class")) is str)
        and budget.get("after_repeat") == "stop_and_rearchitect"
    ):
        errors.append(f"{label} correction budget is invalid")
    stale = claim.get("stale_invalidation")
    if type(stale) is not list or set(stale) != ABC_V2_STALE_TRIGGERS or len(stale) != len(ABC_V2_STALE_TRIGGERS):
        errors.append(f"{label} stale invalidation set is not closed and exact")
    if claim.get("live_actions") is not False:
        errors.append(f"{label} must keep live actions closed")

    candidate_blobs = claim.get("candidate_blobs")
    candidate_state = claim.get("candidate_state")
    command_manifest_digest = _manifest_digest(_command_manifest(tuple(commands)))
    node_ids = claim.get("candidate_pytest_node_ids")
    node_manifest_digest = _node_manifest_digest(node_ids)
    if type(candidate_blobs) is not dict:
        errors.append(f"{label} candidate_blobs must be an explicit object")
        candidate_blobs = {}
    if candidate_state == "not_frozen":
        if candidate_blobs:
            errors.append(f"{label} unfrozen candidate must not publish blobs")
    elif candidate_state == "frozen_for_independent_qa":
        if set(candidate_blobs) != set(allowed) or any(
            type(blob_id) is not str
            or not GIT_BLOB_ID.fullmatch(blob_id)
            or authority.blob_ids.get(path) != blob_id
            for path, blob_id in candidate_blobs.items()
        ):
            errors.append(f"{label} frozen candidate does not bind current writer bytes bijectively")
        if not (
            command_manifest_digest
            and claim.get("candidate_command_manifest_digest_sha256")
            == command_manifest_digest
            and node_manifest_digest
            and claim.get("candidate_pytest_node_manifest_digest_sha256")
            == node_manifest_digest
        ):
            errors.append(f"{label} frozen candidate manifest binding is invalid")
    else:
        errors.append(f"{label} candidate state is invalid for an active claim")

    return AbcClaimV2(
        run_id=run_id,
        slice_id=claim["slice_id"],
        owner=claim["owner"],
        allowed_paths=allowed,
        hotfiles=hotfiles,
        pins=tuple(sorted(pins.items())),
        baselines=tuple(sorted(baselines.items())),
        commands=tuple(commands),
        candidate_blobs=tuple(sorted(candidate_blobs.items())),
        candidate_state=str(candidate_state),
        stale_invalidation=frozenset(stale if isinstance(stale, list) else ()),
        command_manifest_digest=command_manifest_digest,
        pytest_node_manifest_digest=node_manifest_digest,
        pytest_node_ids=tuple(node_ids) if type(node_ids) is list else (),
    )


def _auxiliary_load_error(label: str, error: BaseException) -> str:
    if isinstance(error, _DuplicateJsonKeyError):
        return f"{label} contains a duplicate JSON key"
    if isinstance(error, _JsonDocumentError):
        return f"{label} is unavailable: {error.code}"
    return f"{label} is unavailable: read_failed"


def _historical_terminal_r1_evidence_diagnostic(
    roadmap: dict[str, Any],
    *,
    repo_root: Path,
    errors: list[str],
) -> None:
    """Legacy decoder retained only as non-authoritative historical diagnostics.

    The snapshot-bound R2 pipeline never calls this function.  In particular,
    a terminal R1 claim or manifest cannot supply active freeze authority.
    """
    external_claims = roadmap.get("external_lane_claims", [])
    central_claims = [
        claim
        for claim in external_claims
        if isinstance(claim, dict) and claim.get("slice_id") == HISTORICAL_R1_CENTRAL_SLICE
    ]
    if len(central_claims) != 1:
        errors.append("central process claim must exist exactly once")
        return
    central_claim = central_claims[0]

    pinned_evidence_ids = {
        claim.get("read_only_pins", {}).get(HISTORICAL_R1_EVIDENCE_PATH)
        for claim in external_claims
        if isinstance(claim, dict)
        and claim.get("state") == "running"
        and isinstance(claim.get("read_only_pins"), dict)
        and HISTORICAL_R1_EVIDENCE_PATH in claim["read_only_pins"]
    }
    if len(pinned_evidence_ids) != 1:
        errors.append("central evidence pin is missing or inconsistent")
        expected_evidence_blob = None
    else:
        expected_evidence_blob = next(iter(pinned_evidence_ids))

    documents: dict[str, dict[str, Any]] = {}
    for label, relative_path in (
        ("central evidence", HISTORICAL_R1_EVIDENCE_PATH),
        ("guidance", GUIDANCE_PATH),
        ("Round-6 roadmap", ROUND6_PATH),
    ):
        try:
            documents[label] = _load_auxiliary(repo_root / relative_path)
        except (_DuplicateJsonKeyError, _JsonDocumentError) as error:
            errors.append(_auxiliary_load_error(label, error))
    if set(documents) != {"central evidence", "guidance", "Round-6 roadmap"}:
        return
    evidence = documents["central evidence"]
    guidance = documents["guidance"]
    round6 = documents["Round-6 roadmap"]

    evidence_blob = _path_blob_id(repo_root / HISTORICAL_R1_EVIDENCE_PATH)
    if (
        not isinstance(expected_evidence_blob, str)
        or evidence_blob != expected_evidence_blob
        or not _materialized_git_blob(repo_root, expected_evidence_blob)
    ):
        errors.append("central evidence blob disagrees with the active pin")

    evidence_authority = evidence.get("authority_revision")
    central_authority = central_claim.get("authority_revision")
    transfer = central_claim.get("claim_transfer")
    if (
        evidence.get("kind") != "odysseus.abc_claim_evidence"
        or evidence.get("claim_id") != central_claim.get("run_id")
        or evidence.get("thread_id") != central_claim.get("thread_id")
        or not isinstance(evidence_authority, dict)
        or not isinstance(central_authority, dict)
        or evidence_authority.get("round7_long_run_revision")
        != central_authority.get("round7_long_run_revision")
        or not isinstance(transfer, dict)
        or evidence_authority.get("claim_transfer_at") != transfer.get("transferred_at")
    ):
        errors.append("central evidence identity or authority revision disagrees")

    writer_paths = evidence.get("candidate_writer_paths")
    candidate_blobs = evidence.get("candidate_blobs")
    if not (
        _valid_string_list(writer_paths, nonempty=True)
        and isinstance(candidate_blobs, dict)
        and set(candidate_blobs) == set(writer_paths)
        and all(
            isinstance(blob_id, str) and GIT_BLOB_ID.fullmatch(blob_id)
            for blob_id in candidate_blobs.values()
        )
        and writer_paths == central_claim.get("candidate_writer_paths")
    ):
        errors.append("central evidence candidate writer paths or blobs are invalid")
    else:
        canonical = json.dumps(
            candidate_blobs,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
        expected_digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
        if evidence.get("dirty_diff_digest_sha256") != expected_digest:
            errors.append("central evidence candidate-set digest disagrees")

    if (
        evidence.get("candidate_state") != "frozen_for_independent_qa"
        or not isinstance(evidence.get("candidate_frozen_at"), str)
        or not evidence.get("candidate_frozen_at")
        or not _valid_string_list(evidence.get("stale_invalidation"), nonempty=True)
    ):
        errors.append("central evidence freeze or stale-invalidation contract is invalid")

    evidence_tiers = evidence.get("verification_tiers")
    evidence_budget = evidence.get("correction_budget")
    if not (
        isinstance(evidence_tiers, dict)
        and set(evidence_tiers) == ABC_VERIFICATION_TIERS
        and all(isinstance(value, str) and value for value in evidence_tiers.values())
        and evidence_tiers.get("expensive_live") == "not_run_user_gated"
        and isinstance(evidence_budget, dict)
        and evidence_budget.get("limit_per_invariant") == 1
        and type(evidence_budget.get("used")) is int
        and 0 <= evidence_budget["used"] <= 1
        and evidence_budget.get("after_repeat") == "stop_and_rearchitect"
        and evidence.get("live_actions") is False
    ):
        errors.append("central evidence verification, budget or live boundary is invalid")

    evidence_baselines = evidence.get("baseline_blobs")
    if not (
        isinstance(evidence_baselines, dict)
        and set(evidence_baselines) == set(writer_paths or [])
        and all(
            isinstance(blob_id, str) and _materialized_git_blob(repo_root, blob_id)
            for blob_id in evidence_baselines.values()
        )
        and evidence.get("baseline_readback") == "materialized_exact_binary_readback_pass"
    ):
        errors.append("central evidence baselines are not materialized and read back")

    evidence_pins = evidence.get("read_only_pins")
    if not (
        isinstance(evidence_pins, dict)
        and len(evidence_pins) == 7
        and all(
            isinstance(path, str)
            and isinstance(blob_id, str)
            and GIT_BLOB_ID.fullmatch(blob_id)
            and _materialized_git_blob(repo_root, blob_id)
            for path, blob_id in evidence_pins.items()
        )
    ):
        errors.append("central evidence read-only pins are not exact materialized blobs")

    if not (
        isinstance(central_claim.get("compare_before_update"), str)
        and central_claim.get("compare_before_update")
        and isinstance(central_claim.get("claim_transfer"), dict)
        and central_claim["claim_transfer"].get("old_scope_preserved") is True
        and central_claim.get("correction_budget") == evidence.get("correction_budget")
    ):
        errors.append("central claim transfer, compare-before-update or budget disagrees")

    if not (
        central_claim.get("claim_id") == central_claim.get("run_id")
        and _valid_string_list(central_claim.get("allowed_paths"), nonempty=True)
        and _valid_string_list(central_claim.get("hotfiles"))
        and _valid_string_list(central_claim.get("regression_tests"), nonempty=True)
        and _valid_string_list(central_claim.get("hostile_matrix"), nonempty=True)
        and isinstance(central_claim.get("pytest_basetemp"), str)
        and central_claim["pytest_basetemp"].startswith(".abc-test-tmp/")
        and central_claim.get("run_id") in central_claim["pytest_basetemp"]
        and central_claim.get("live_actions") is False
    ):
        errors.append("central claim ABC arrays, basetemp or live boundary is invalid")

    summary = guidance.get("analysis_summary", {}).get("round6_parallel_execution")
    receipt = evidence.get("inventory_receipt")
    roadmaps = guidance.get("roadmaps")
    frontiers = round6.get("roadmap_frontiers")
    if not (
        isinstance(summary, dict)
        and isinstance(receipt, dict)
        and isinstance(roadmaps, list)
        and isinstance(frontiers, list)
    ):
        errors.append("Round-6 rolling inventory authority is incomplete")
        return

    member_paths = sorted(
        {
            entry["path"]
            for entry in roadmaps
            if isinstance(entry, dict)
            and isinstance(entry.get("path"), str)
            and str(entry.get("profile", "")).startswith("active_")
        }
    )
    frontier_paths = sorted(
        entry.get("roadmap_path")
        for entry in frontiers
        if isinstance(entry, dict) and isinstance(entry.get("roadmap_path"), str)
    )
    member_digest = _member_set_digest(member_paths)
    if not (
        summary.get("inventory_mode") == "rolling_projection"
        and receipt.get("inventory_mode") == "rolling_projection"
        and summary.get("inventory_member_count") == 30
        and receipt.get("member_count") == 30
        and summary.get("inventory_member_set_digest") == member_digest
        and receipt.get("member_set_digest") == member_digest
        and member_paths == frontier_paths
    ):
        errors.append("Round-6 rolling inventory member set, count or digest disagrees")

    source_blob_id = summary.get("inventory_source_blob")
    source_guidance = _materialized_json_blob(repo_root, source_blob_id)
    source_members = (
        sorted(
            {
                entry["path"]
                for entry in source_guidance.get("roadmaps", [])
                if isinstance(entry, dict)
                and isinstance(entry.get("path"), str)
                and str(entry.get("profile", "")).startswith("active_")
            }
        )
        if isinstance(source_guidance, dict)
        else []
    )
    if len(source_members) != 30 or _member_set_digest(source_members) != member_digest:
        errors.append("Round-6 rolling inventory source blob disagrees")

    change = summary.get("inventory_denominator_change")
    old_round6 = (
        _materialized_json_blob(repo_root, change.get("old_round6_blob"))
        if isinstance(change, Mapping)
        else None
    )
    new_round6 = (
        _materialized_json_blob(repo_root, change.get("new_round6_blob"))
        if isinstance(change, Mapping)
        else None
    )
    old_paths = (
        sorted(item.get("roadmap_path") for item in old_round6.get("roadmap_frontiers", []))
        if isinstance(old_round6, dict)
        else []
    )
    new_paths = (
        sorted(item.get("roadmap_path") for item in new_round6.get("roadmap_frontiers", []))
        if isinstance(new_round6, dict)
        else []
    )
    scheduled_count = sum(
        len(wave.get("implementation_slice_ids", []))
        for wave in new_round6.get("waves", [])
    ) if isinstance(new_round6, dict) else -1
    if not (
        isinstance(change, Mapping)
        and change.get("old_count") == 29
        and change.get("new_count") == 30
        and change.get("added") == ["docs/plans/forge-usi-code-source-adapter-roadmap.md"]
        and change.get("removed") == []
        and len(old_paths) == 29
        and len(new_paths) == 30
        and set(new_paths) - set(old_paths) == {"docs/plans/forge-usi-code-source-adapter-roadmap.md"}
        and not (set(old_paths) - set(new_paths))
        and isinstance(old_round6, dict)
        and isinstance(new_round6, dict)
        and old_round6.get("waves") == new_round6.get("waves")
        and scheduled_count == 7
        and receipt.get("terminal_waves_unchanged") is True
        and receipt.get("scheduled_slice_count_unchanged") == 7
    ):
        errors.append("Round-6 denominator or terminal schedule receipt disagrees")


def _candidate_set_digest(candidate_blobs: Mapping[str, str]) -> str:
    canonical = json.dumps(
        dict(candidate_blobs),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _manifest_digest(value: Any) -> str | None:
    """Return a bounded canonical digest for exact decoded JSON values."""

    try:
        _validate_json_tree(value)
        raw = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, _JsonDocumentError, RecursionError):
        return None
    if len(raw) > MAX_ARTIFACT_BYTES:
        return None
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _command_manifest(commands: tuple[CommandSpec, ...]) -> list[dict[str, Any]]:
    return [
        {"id": item.command_id, "tier": item.tier, "argv": list(item.argv)}
        for item in commands
    ]


def _node_manifest_digest(node_ids: Any) -> str | None:
    if (
        type(node_ids) is not list
        or not node_ids
        or len(node_ids) > MAX_JSON_NODES
        or any(type(node_id) is not str or not node_id for node_id in node_ids)
        or node_ids != sorted(node_ids)
        or len(node_ids) != len(set(node_ids))
    ):
        return None
    return _manifest_digest(node_ids)


def _validate_terminal_receipt(
    claim: dict[str, Any], *, repo_root: Path, errors: list[str]
) -> None:
    """Bind the R4 terminal handoff to the same frozen candidate/manifests."""

    receipt = claim.get("terminal_receipt")
    if type(receipt) is not dict or set(receipt) != TERMINAL_RECEIPT_FIELDS:
        errors.append("terminal R4 receipt is missing or outside the closed schema")
        return
    candidates = receipt.get("candidate_blobs")
    command_manifest = receipt.get("command_manifest")
    node_ids = receipt.get("pytest_node_ids")
    candidate_digest = (
        _candidate_set_digest(candidates) if type(candidates) is dict else None
    )
    command_digest = _manifest_digest(command_manifest)
    node_digest = _node_manifest_digest(node_ids)
    pre_release = receipt.get("pre_release")
    post_release = receipt.get("post_release")
    terminal_claim_bindings = {
        field: claim.get(field) for field in R4_TERMINAL_CLAIM_BINDING_FIELDS
    }
    receipt_claim_bindings = {
        "candidate_blobs": candidates,
        "candidate_set_digest_sha256": candidate_digest,
        "candidate_command_manifest_digest_sha256": command_digest,
        "candidate_pytest_node_manifest_digest_sha256": node_digest,
        "candidate_pytest_node_ids": node_ids,
    }
    if not (
        type(candidates) is dict
        and candidates
        and all(
            _repository_path_key(path) is not None
            and type(blob_id) is str
            and GIT_BLOB_ID.fullmatch(blob_id)
            and _materialized_git_blob(repo_root, blob_id)
            for path, blob_id in candidates.items()
        )
        and type(command_manifest) is list
        and command_manifest
        and type(node_ids) is list
        and candidate_digest == receipt.get("candidate_set_digest_sha256")
        and command_digest == receipt.get("command_manifest_digest_sha256")
        and node_digest == receipt.get("pytest_node_manifest_digest_sha256")
        and type(pre_release) is dict
        and type(post_release) is dict
        and set(pre_release) == TERMINAL_RECEIPT_BINDING_FIELDS
        and set(post_release) == TERMINAL_RECEIPT_BINDING_FIELDS
        and pre_release == post_release
        and pre_release
        == {
            "candidate_set_digest_sha256": candidate_digest,
            "command_manifest_digest_sha256": command_digest,
            "pytest_node_manifest_digest_sha256": node_digest,
        }
        and terminal_claim_bindings == receipt_claim_bindings
    ):
        errors.append(
            "terminal R4 receipt does not bind one candidate and manifest set"
        )


def _validate_active_freeze_binding(
    claim: dict[str, Any],
    typed: AbcClaimV2,
    *,
    repo_root: Path,
    authority: AuthoritySnapshotBundle,
    errors: list[str],
) -> None:
    if typed.candidate_state != "frozen_for_independent_qa":
        return
    evidence_path = claim.get("candidate_evidence_path")
    evidence_blob = claim.get("candidate_evidence_manifest_blob")
    candidate_blobs = dict(typed.candidate_blobs)
    if (
        _repository_path_key(evidence_path) is None
        or not str(evidence_path).startswith("docs/plans/evidence/")
        or type(evidence_blob) is not str
        or not GIT_BLOB_ID.fullmatch(evidence_blob)
        or dict(typed.pins).get(evidence_path) != evidence_blob
        or authority.blob_ids.get(evidence_path) != evidence_blob
    ):
        errors.append("active frozen claim has no exact external Evidence snapshot")
        return
    evidence = authority.get(evidence_path)
    if not isinstance(evidence, Mapping):
        errors.append("active freeze Evidence snapshot is unavailable")
        return
    if set(evidence) != ACTIVE_FREEZE_EVIDENCE_FIELDS:
        errors.append("active freeze Evidence schema is not closed and exact")
    writer_paths = evidence.get("candidate_writer_paths")
    evidence_blobs = evidence.get("candidate_blobs")
    digest = _candidate_set_digest(candidate_blobs)
    dependency_pins = {
        path: blob_id for path, blob_id in typed.pins if path != evidence_path
    }
    if tuple(evidence.get("stale_invalidation", ())) != tuple(
        claim.get("stale_invalidation", ())
    ):
        errors.append("active freeze Evidence stale set differs from the claim")
    if not (
        type(evidence.get("schema_version")) is int
        and evidence.get("schema_version") == 1
        and evidence.get("kind") == "odysseus.abc_claim_evidence"
        and evidence.get("claim_id") == typed.run_id
        and evidence.get("thread_id") == claim.get("thread_id")
        and evidence.get("authority_revision") == claim.get("authority_revision")
        and tuple(writer_paths or ()) == typed.allowed_paths
        and isinstance(evidence.get("baseline_blobs"), Mapping)
        and dict(evidence.get("baseline_blobs", {})) == dict(typed.baselines)
        and _thaw_json(evidence.get("baseline_readback"))
        == claim.get("baseline_readback")
        and isinstance(evidence_blobs, Mapping)
        and dict(evidence_blobs) == candidate_blobs
        and evidence.get("candidate_set_digest_sha256") == digest
        and claim.get("candidate_set_digest_sha256") == digest
        and evidence.get("candidate_command_manifest_digest_sha256")
        == typed.command_manifest_digest
        and claim.get("candidate_command_manifest_digest_sha256")
        == typed.command_manifest_digest
        and evidence.get("candidate_pytest_node_manifest_digest_sha256")
        == typed.pytest_node_manifest_digest
        and claim.get("candidate_pytest_node_manifest_digest_sha256")
        == typed.pytest_node_manifest_digest
        and tuple(evidence.get("candidate_pytest_node_ids", ()))
        == typed.pytest_node_ids
        and tuple(claim.get("candidate_pytest_node_ids", ()))
        == typed.pytest_node_ids
        and evidence.get("candidate_state") == "frozen_for_independent_qa"
        and evidence.get("candidate_frozen_at") == claim.get("candidate_frozen_at")
        and _parse_iso_timestamp(evidence.get("candidate_frozen_at")) is not None
        and isinstance(evidence.get("read_only_pins"), Mapping)
        # The Evidence object's blob identity is bound by the claim's separate
        # Evidence pin above.  Including that blob inside the serialized
        # document would require an impossible hash fixed point.
        and dict(evidence.get("read_only_pins", {})) == dependency_pins
        and isinstance(evidence.get("verification_tiers"), Mapping)
        and dict(evidence.get("verification_tiers", {})) == claim.get("verification_tiers")
        and isinstance(evidence.get("correction_budget"), Mapping)
        and dict(evidence.get("correction_budget", {})) == claim.get("correction_budget")
        and isinstance(evidence.get("inventory_receipt"), Mapping)
        and evidence.get("live_actions") is False
        and all(
            _materialized_git_blob(repo_root, blob_id)
            for blob_id in candidate_blobs.values()
        )
    ):
        errors.append("active writer bytes, Evidence map and digest are not one exact bijection")


def _r5_is_validator_claim(claim: Any) -> bool:
    return type(claim) is dict and claim.get("slice_id") == R5_VALIDATOR_SLICE


def _r5_immutable_claim_core(claim: Any) -> dict[str, Any] | None:
    """Extract the only claim content a Root receipt is allowed to bind.

    The locator of the receipt itself and terminal observations are deliberately
    excluded: including either would create a self-hash or allow a transition
    record to mutate the frozen implementation authority.
    """

    if type(claim) is not dict or not R5_IMMUTABLE_CLAIM_FIELDS.issubset(claim):
        return None
    transition_fields = {
        "state",
        "freeze_receipt_blob",
        "released_at",
        "released_reason",
        "release_reason",
        "review_result",
        "terminal_verification",
    }
    if set(claim) - (R5_IMMUTABLE_CLAIM_FIELDS | transition_fields):
        return None
    core = {field: claim[field] for field in sorted(R5_IMMUTABLE_CLAIM_FIELDS)}
    return core if _manifest_digest(core) is not None else None


def _r5_structured_command_manifest(claim: Any) -> list[dict[str, Any]] | None:
    if type(claim) is not dict or type(claim.get("regression_tests")) is not list:
        return None
    commands: list[dict[str, Any]] = []
    for item in claim["regression_tests"]:
        if (
            type(item) is not dict
            or set(item) != {"id", "tier", "argv"}
            or type(item.get("id")) is not str
            or not item["id"]
            or item.get("tier") not in ABC_VERIFICATION_TIERS - {"expensive_live"}
            or not _valid_string_list(item.get("argv"), nonempty=True)
        ):
            return None
        commands.append(
            {"id": item["id"], "tier": item["tier"], "argv": list(item["argv"])}
        )
    return commands if commands and len({item["id"] for item in commands}) == len(commands) else None


def _r5_architecture_binding(
    claim: dict[str, Any], roadmap: Mapping[str, Any]
) -> tuple[str, str] | None:
    packets = roadmap.get("architecture_packets")
    if type(packets) is not dict:
        return None
    packet = packets.get(R5_ARCHITECTURE_PACKET)
    if type(packet) is not dict:
        return None
    revision = packet.get("architecture_revision")
    digest = packet.get("digest_sha256")
    if (
        type(revision) is not str
        or not revision
        or type(digest) is not str
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
        or claim.get("architecture_revision") != revision
        or claim.get("architecture_digest_sha256") != digest
    ):
        return None
    return revision, digest


def _r5_evidence_and_pin_binding(
    claim: dict[str, Any], authority: AuthoritySnapshotBundle
) -> tuple[str, str, dict[str, str], Mapping[str, Any]] | None:
    evidence_path = claim.get("candidate_evidence_path")
    evidence_blob = claim.get("candidate_evidence_manifest_blob")
    pins = claim.get("read_only_pins")
    if (
        _repository_path_key(evidence_path) is None
        or type(evidence_path) is not str
        or not evidence_path.startswith("docs/plans/evidence/")
        or type(evidence_blob) is not str
        or not GIT_BLOB_ID.fullmatch(evidence_blob)
        or type(pins) is not dict
        or pins.get(evidence_path) != evidence_blob
    ):
        return None
    dependency_pins = {path: blob for path, blob in pins.items() if path != evidence_path}
    if (
        not dependency_pins
        or any(
            _repository_path_key(path) is None
            or type(blob) is not str
            or not GIT_BLOB_ID.fullmatch(blob)
            for path, blob in dependency_pins.items()
        )
    ):
        return None
    evidence = authority.get(evidence_path)
    if not isinstance(evidence, Mapping):
        return None
    return evidence_path, evidence_blob, dependency_pins, evidence


def _r5_terminal_verification_matches(
    claim: dict[str, Any], receipt: Mapping[str, Any], expected_phase: str
) -> bool:
    terminal = claim.get("terminal_verification")
    return (
        type(terminal) is dict
        and set(terminal) == R5_TERMINAL_VERIFICATION_FIELDS
        and terminal.get("phase") == expected_phase
        and terminal.get("candidate_set_digest_sha256")
        == receipt.get("candidate_set_digest_sha256")
        and terminal.get("command_manifest_digest_sha256")
        == receipt.get("command_manifest_digest_sha256")
        and terminal.get("pytest_node_manifest_digest_sha256")
        == receipt.get("pytest_node_manifest_digest_sha256")
    )


def _validate_r5_freeze_receipt(
    claim: dict[str, Any],
    *,
    roadmap: Mapping[str, Any],
    repo_root: Path,
    authority: AuthoritySnapshotBundle,
    current_capture_required: bool,
    terminal_phase: str | None,
    errors: list[str],
) -> None:
    """Validate the closed Root-materialized R5 transition authority."""

    core = _r5_immutable_claim_core(claim)
    architecture = _r5_architecture_binding(claim, roadmap)
    receipt_blob = claim.get("freeze_receipt_blob")
    if (
        core is None
        or architecture is None
        or type(receipt_blob) is not str
        or not GIT_BLOB_ID.fullmatch(receipt_blob)
    ):
        errors.append("R5 freeze receipt claim core or architecture binding is invalid")
        return
    receipt = _materialized_freeze_receipt_blob(repo_root, receipt_blob)
    if type(receipt) is not dict or set(receipt) != R5_FREEZE_RECEIPT_FIELDS:
        errors.append("R5 freeze receipt is missing, nonblob, malformed or outside the closed schema")
        return
    commands = _r5_structured_command_manifest(claim)
    candidates = claim.get("candidate_blobs")
    node_ids = claim.get("candidate_pytest_node_ids")
    candidate_digest = _candidate_set_digest(candidates) if type(candidates) is dict else None
    command_digest = _manifest_digest(commands)
    node_digest = _node_manifest_digest(node_ids)
    binding = _r5_evidence_and_pin_binding(claim, authority)
    if binding is None:
        errors.append("R5 freeze receipt Evidence or dependency-pin binding is invalid")
        return
    evidence_path, evidence_blob, dependency_pins, evidence = binding
    core_digest = _manifest_digest(core)
    revision, architecture_digest = architecture
    receipt_matches = (
        receipt.get("schema_version") == 1
        and receipt.get("kind") == R5_FREEZE_RECEIPT_KIND
        and receipt.get("claim_id") == claim.get("claim_id")
        and receipt.get("architecture_revision") == revision
        and receipt.get("architecture_digest_sha256") == architecture_digest
        and receipt.get("authority_revision") == claim.get("authority_revision")
        and receipt.get("immutable_claim_core") == core
        and receipt.get("immutable_claim_core_digest_sha256") == core_digest
        and receipt.get("candidate_blobs") == candidates
        and receipt.get("candidate_set_digest_sha256") == candidate_digest
        and receipt.get("candidate_evidence_path") == evidence_path
        and receipt.get("candidate_evidence_manifest_blob") == evidence_blob
        and receipt.get("dependency_pins") == dependency_pins
        and receipt.get("command_manifest") == commands
        and receipt.get("command_manifest_digest_sha256") == command_digest
        and receipt.get("pytest_node_ids") == node_ids
        and receipt.get("pytest_node_manifest_digest_sha256") == node_digest
        and receipt.get("inventory_receipt") == evidence.get("inventory_receipt")
        and receipt.get("correction_ledger") == claim.get("correction_budget")
    )
    if not receipt_matches:
        errors.append("R5 freeze receipt does not bind the complete immutable claim core")
        return
    if current_capture_required:
        current_candidates = {
            path: authority.blob_ids.get(path)
            for path in claim.get("allowed_paths", [])
        }
        current_pins = {
            path: authority.blob_ids.get(path)
            for path in claim.get("read_only_pins", {})
        }
        if (
            current_candidates != candidates
            or current_pins != claim.get("read_only_pins")
            or authority.blob_ids.get(evidence_path) != evidence_blob
            or evidence.get("candidate_blobs") != candidates
            or evidence.get("read_only_pins") != dependency_pins
        ):
            errors.append("R5 pending receipt current capture differs from the frozen authority")
            return
    if terminal_phase is not None and not _r5_terminal_verification_matches(
        claim, receipt, terminal_phase
    ):
        errors.append("R5 terminal verification is not bound to the Root freeze receipt")


def _r6_is_validator_claim(claim: Any) -> bool:
    return type(claim) is dict and claim.get("slice_id") in {
        R6_VALIDATOR_SLICE,
        R6_DIRECT_VALIDATOR_SLICE,
    }


def _is_sha256_digest(value: Any) -> bool:
    return type(value) is str and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None


def _r6_immutable_claim_core(claim: Any) -> dict[str, Any] | None:
    """Return the phase-independent claim core anchored by immutable F.

    Receipt locators, release observations and the mutable lifecycle pair are
    intentionally excluded.  Everything else needed to reproduce the frozen
    candidate -- including paths, pins, commands, Evidence identity and the
    correction ledger -- is copied into F exactly once.
    """

    if type(claim) is not dict or not R6_IMMUTABLE_CLAIM_FIELDS.issubset(claim):
        return None
    transition_fields = {
        "state",
        "freeze_receipt_blob",
        "terminal_transition_blob",
        "terminal_evidence_digest_sha256",
        "released_at",
        "released_reason",
        "review_result",
        "terminal_verification",
        "acceptance_evidence",
    }
    if set(claim) - (R6_IMMUTABLE_CLAIM_FIELDS | transition_fields):
        return None
    core = {field: claim[field] for field in sorted(R6_IMMUTABLE_CLAIM_FIELDS)}
    # B is the only permitted terminal phase that changes candidate_state.  F
    # always records the pre-transition frozen candidate; otherwise B could
    # never be checked against the identical immutable F it is required to
    # retain.
    if core.get("candidate_state") == R6_BLOCKED_AFTER_FREEZE_OUTCOME:
        core["candidate_state"] = "frozen_for_independent_qa"
    return core if _manifest_digest(core) is not None else None


def _r6_architecture_binding(
    claim: dict[str, Any], roadmap: Mapping[str, Any]
) -> tuple[str, str] | None:
    if type(claim) is not dict:
        return None
    packet_key = {
        R6_VALIDATOR_SLICE: R6_ARCHITECTURE_PACKET,
        R6_DIRECT_VALIDATOR_SLICE: R6_DIRECT_ARCHITECTURE_PACKET,
    }.get(claim.get("slice_id"))
    if packet_key is None:
        return None
    packets = roadmap.get("architecture_packets")
    if type(packets) is not dict:
        return None
    packet = packets.get(packet_key)
    if type(packet) is not dict:
        return None
    revision = packet.get("architecture_revision")
    digest = packet.get("digest_sha256")
    if (
        type(revision) is not str
        or not revision
        or not _is_sha256_digest(digest)
        or claim.get("architecture_revision") != revision
        or claim.get("architecture_digest_sha256") != digest
    ):
        return None
    return revision, digest


def _r6_frozen_claim_bindings(
    claim: dict[str, Any],
    *,
    repo_root: Path,
) -> tuple[
    dict[str, str],
    list[dict[str, Any]],
    list[str],
    str,
    str,
    str,
    str,
    str,
] | None:
    """Validate the phase-independent frozen candidate representation."""

    if claim.get("candidate_state") not in {
        "frozen_for_independent_qa",
        R6_BLOCKED_AFTER_FREEZE_OUTCOME,
    }:
        return None
    allowed = claim.get("allowed_paths")
    candidates = claim.get("candidate_blobs")
    commands = _r5_structured_command_manifest(claim)
    node_ids = claim.get("candidate_pytest_node_ids")
    pins = claim.get("read_only_pins")
    evidence_path = claim.get("candidate_evidence_path")
    evidence_blob = claim.get("candidate_evidence_manifest_blob")
    candidate_digest = _candidate_set_digest(candidates) if type(candidates) is dict else None
    command_digest = _manifest_digest(commands)
    node_digest = _node_manifest_digest(node_ids)
    if not (
        type(allowed) is list
        and _collect_repository_paths(allowed, require_nonempty=True)[1]
        and _paths_are_canonical(allowed)
        and type(candidates) is dict
        and set(candidates) == set(allowed)
        and all(
            type(blob_id) is str
            and GIT_BLOB_ID.fullmatch(blob_id)
            and _materialized_git_blob(repo_root, blob_id)
            for blob_id in candidates.values()
        )
        and type(pins) is dict
        and _repository_path_key(evidence_path) is not None
        and type(evidence_path) is str
        and evidence_path.startswith("docs/plans/evidence/")
        and type(evidence_blob) is str
        and GIT_BLOB_ID.fullmatch(evidence_blob)
        and pins.get(evidence_path) == evidence_blob
        and all(
            _repository_path_key(path) is not None
            and type(blob_id) is str
            and GIT_BLOB_ID.fullmatch(blob_id)
            for path, blob_id in pins.items()
        )
        and candidate_digest is not None
        and candidate_digest == claim.get("candidate_set_digest_sha256")
        and command_digest is not None
        and command_digest == claim.get("candidate_command_manifest_digest_sha256")
        and node_digest is not None
        and node_digest == claim.get("candidate_pytest_node_manifest_digest_sha256")
        and type(node_ids) is list
        and _parse_iso_timestamp(claim.get("candidate_frozen_at")) is not None
    ):
        return None
    dependency_pins = {path: blob for path, blob in pins.items() if path != evidence_path}
    if not dependency_pins:
        return None
    return (
        dict(candidates),
        commands,
        list(node_ids),
        candidate_digest,
        command_digest,
        node_digest,
        evidence_path,
        evidence_blob,
    )


def _r6_validate_freeze_receipt(
    claim: dict[str, Any],
    *,
    roadmap: Mapping[str, Any],
    repo_root: Path,
    authority: AuthoritySnapshotBundle | None,
    current_capture_required: bool,
    errors: list[str],
) -> dict[str, Any] | None:
    """Validate immutable F, using only materialized data for terminal history."""

    core = _r6_immutable_claim_core(claim)
    architecture = _r6_architecture_binding(claim, roadmap)
    bindings = _r6_frozen_claim_bindings(claim, repo_root=repo_root)
    receipt_blob = claim.get("freeze_receipt_blob")
    if (
        core is None
        or architecture is None
        or bindings is None
        or type(receipt_blob) is not str
        or not GIT_BLOB_ID.fullmatch(receipt_blob)
    ):
        errors.append("R6 freeze receipt claim core or architecture binding is invalid")
        return None
    receipt = _materialized_freeze_receipt_blob(repo_root, receipt_blob)
    if type(receipt) is not dict or set(receipt) != R6_FREEZE_RECEIPT_FIELDS:
        errors.append("R6 freeze receipt is missing, nonblob, malformed or outside the closed schema")
        return None
    (
        candidates,
        commands,
        node_ids,
        candidate_digest,
        command_digest,
        node_digest,
        evidence_path,
        evidence_blob,
    ) = bindings
    pins = claim["read_only_pins"]
    dependency_pins = {path: blob for path, blob in pins.items() if path != evidence_path}
    revision, architecture_digest = architecture
    evidence = _materialized_json_blob(repo_root, evidence_blob)
    evidence_digest = _manifest_digest(evidence)
    matches = (
        receipt.get("schema_version") == 1
        and receipt.get("kind") == R6_FREEZE_RECEIPT_KIND
        and receipt.get("claim_id") == claim.get("claim_id")
        and receipt.get("architecture_revision") == revision
        and receipt.get("architecture_digest_sha256") == architecture_digest
        and receipt.get("authority_revision") == claim.get("authority_revision")
        and receipt.get("immutable_claim_core") == core
        and receipt.get("immutable_claim_core_digest_sha256") == _manifest_digest(core)
        and receipt.get("candidate_blobs") == candidates
        and receipt.get("candidate_set_digest_sha256") == candidate_digest
        and receipt.get("candidate_evidence_path") == evidence_path
        and receipt.get("candidate_evidence_manifest_blob") == evidence_blob
        and receipt.get("dependency_pins") == dependency_pins
        and receipt.get("command_manifest") == commands
        and receipt.get("command_manifest_digest_sha256") == command_digest
        and receipt.get("pytest_node_ids") == node_ids
        and receipt.get("pytest_node_manifest_digest_sha256") == node_digest
        and receipt.get("evidence_digest_sha256") == evidence_digest
        and isinstance(evidence, Mapping)
        and receipt.get("inventory_receipt") == evidence.get("inventory_receipt")
        and receipt.get("correction_ledger") == claim.get("correction_budget")
    )
    if not matches:
        errors.append("R6 freeze receipt does not bind the complete immutable claim core")
        return None
    if current_capture_required:
        if authority is None:
            errors.append("R6 pending receipt current authority capture is unavailable")
            return None
        current_candidates = {path: authority.blob_ids.get(path) for path in claim["allowed_paths"]}
        current_pins = {path: authority.blob_ids.get(path) for path in pins}
        current_evidence = authority.get(evidence_path)
        if (
            current_candidates != candidates
            or current_pins != pins
            or authority.blob_ids.get(evidence_path) != evidence_blob
            or not isinstance(current_evidence, Mapping)
            or _manifest_digest(_thaw_json(current_evidence)) != evidence_digest
            or current_evidence.get("candidate_blobs") != candidates
            or current_evidence.get("read_only_pins") != dependency_pins
        ):
            errors.append("R6 pending receipt current capture differs from frozen authority")
            return None
    return receipt


def _r6_transition_expectation(
    phase: str,
) -> tuple[str, str, str, str, str] | None:
    if phase == R6_PENDING_OUTCOME:
        return (
            "running",
            "frozen_for_independent_qa",
            "released_pending_post_release_qa",
            "frozen_for_independent_qa",
            R6_PENDING_OUTCOME,
        )
    if phase == R6_ACCEPTED_OUTCOME:
        return (
            "released_pending_post_release_qa",
            "frozen_for_independent_qa",
            "released_accepted",
            "frozen_for_independent_qa",
            R6_ACCEPTED_OUTCOME,
        )
    if phase == R6_BLOCKED_AFTER_FREEZE_OUTCOME:
        return (
            "released_pending_post_release_qa",
            "frozen_for_independent_qa",
            "released_blocked",
            "blocked_after_freeze",
            R6_BLOCKED_AFTER_FREEZE_OUTCOME,
        )
    return None


def _r6_validate_transition_receipt(
    receipt_blob: Any,
    *,
    phase: str,
    previous_blob: str | None,
    freeze_blob: str,
    freeze_receipt: Mapping[str, Any],
    claim: Mapping[str, Any],
    repo_root: Path,
    errors: list[str],
) -> dict[str, Any] | None:
    """Validate exactly one P/A/B object without reading mutable paths."""

    if type(receipt_blob) is not str or not GIT_BLOB_ID.fullmatch(receipt_blob):
        errors.append("R6 transition receipt pointer is invalid")
        return None
    receipt = _materialized_freeze_receipt_blob(repo_root, receipt_blob)
    expected = _r6_transition_expectation(phase)
    if type(receipt) is not dict or set(receipt) != R6_TRANSITION_RECEIPT_FIELDS or expected is None:
        errors.append("R6 transition receipt is missing, nonblob, malformed or outside the closed schema")
        return None
    from_state, from_candidate, to_state, to_candidate, outcome = expected
    if not (
        receipt.get("schema_version") == 1
        and receipt.get("kind") == R6_TRANSITION_RECEIPT_KIND
        and receipt.get("claim_id") == claim.get("claim_id")
        and receipt.get("architecture_revision") == claim.get("architecture_revision")
        and receipt.get("architecture_digest_sha256") == claim.get("architecture_digest_sha256")
        and receipt.get("authority_revision") == claim.get("authority_revision")
        and receipt.get("freeze_receipt_blob") == freeze_blob
        and (
            receipt.get("previous_receipt_blob") == previous_blob
            if previous_blob is not None
            else type(receipt.get("previous_receipt_blob")) is str
            and GIT_BLOB_ID.fullmatch(receipt["previous_receipt_blob"])
        )
        and receipt.get("from_state") == from_state
        and receipt.get("from_candidate_state") == from_candidate
        and receipt.get("to_state") == to_state
        and receipt.get("to_candidate_state") == to_candidate
        and receipt.get("outcome_code") == outcome
        and receipt.get("candidate_set_digest_sha256")
        == freeze_receipt.get("candidate_set_digest_sha256")
        and receipt.get("command_manifest_digest_sha256")
        == freeze_receipt.get("command_manifest_digest_sha256")
        and receipt.get("pytest_node_manifest_digest_sha256")
        == freeze_receipt.get("pytest_node_manifest_digest_sha256")
        and _is_sha256_digest(receipt.get("evidence_digest_sha256"))
        and _parse_iso_timestamp(receipt.get("observed_at")) is not None
        and receipt.get("correction_ledger") == claim.get("correction_budget")
    ):
        errors.append("R6 transition receipt does not bind the required F-linked phase")
        return None
    return receipt


def _r6_validate_terminal_verification(
    claim: Mapping[str, Any],
    *,
    phase: str,
    freeze_blob: str,
    transition_blob: str,
    transition: Mapping[str, Any],
    errors: list[str],
) -> None:
    terminal = claim.get("terminal_verification")
    if not (
        type(terminal) is dict
        and set(terminal) == R6_TERMINAL_VERIFICATION_FIELDS
        and terminal.get("phase") == phase
        and terminal.get("freeze_receipt_blob") == freeze_blob
        and terminal.get("transition_receipt_blob") == transition_blob
        and terminal.get("candidate_set_digest_sha256")
        == transition.get("candidate_set_digest_sha256")
        and terminal.get("command_manifest_digest_sha256")
        == transition.get("command_manifest_digest_sha256")
        and terminal.get("pytest_node_manifest_digest_sha256")
        == transition.get("pytest_node_manifest_digest_sha256")
        and terminal.get("evidence_digest_sha256")
        == transition.get("evidence_digest_sha256")
        and claim.get("terminal_evidence_digest_sha256")
        == transition.get("evidence_digest_sha256")
    ):
        errors.append("R6 terminal verification is not bound to the materialized transition receipt")


def _validate_r6_lifecycle(
    claim: dict[str, Any],
    *,
    roadmap: Mapping[str, Any],
    repo_root: Path,
    authority: AuthoritySnapshotBundle | None,
    current_capture_required: bool,
    errors: list[str],
) -> None:
    """Enforce the closed six-pair R6 lifecycle and F/P/A/B chain."""

    state = claim.get("state")
    candidate_state = claim.get("candidate_state")
    if state == "running":
        if candidate_state == "not_frozen":
            forbidden = {
                "freeze_receipt_blob",
                "terminal_transition_blob",
                "terminal_evidence_digest_sha256",
                "released_at",
                "released_reason",
                "review_result",
                "terminal_verification",
            }
            if forbidden & set(claim):
                errors.append("R6 unfrozen running claim publishes terminal receipt data")
            if _r6_architecture_binding(claim, roadmap) is None:
                errors.append("R6 running claim architecture binding is invalid")
            return
        if candidate_state != "frozen_for_independent_qa":
            errors.append("R6 running claim candidate state is outside the lifecycle algebra")
            return
        _r6_validate_freeze_receipt(
            claim,
            roadmap=roadmap,
            repo_root=repo_root,
            authority=authority,
            current_capture_required=current_capture_required,
            errors=errors,
        )
        return

    if (
        state == "released_blocked"
        and candidate_state == R6_BLOCKED_BEFORE_FREEZE_OUTCOME
    ):
        forbidden = {
            "freeze_receipt_blob",
            "terminal_transition_blob",
            "terminal_evidence_digest_sha256",
            "candidate_evidence_manifest_blob",
            "candidate_evidence_path",
            "candidate_frozen_at",
            "candidate_set_digest_sha256",
            "candidate_command_manifest_digest_sha256",
            "candidate_pytest_node_manifest_digest_sha256",
            "candidate_pytest_node_ids",
            "architecture_revision",
            "architecture_digest_sha256",
            "terminal_verification",
        }
        if (
            claim.get("candidate_blobs") != {}
            or claim.get("review_result") != R6_BLOCKED_BEFORE_FREEZE_OUTCOME
            or forbidden & set(claim)
        ):
            errors.append("R6 blocked-before-freeze claim is not receiptless and exact")
        return

    terminal_phase = (
        R6_PENDING_OUTCOME
        if state == "released_pending_post_release_qa"
        and candidate_state == "frozen_for_independent_qa"
        else R6_ACCEPTED_OUTCOME
        if state == "released_accepted" and candidate_state == "frozen_for_independent_qa"
        else R6_BLOCKED_AFTER_FREEZE_OUTCOME
        if state == "released_blocked" and candidate_state == R6_BLOCKED_AFTER_FREEZE_OUTCOME
        else None
    )
    if terminal_phase is None:
        errors.append("R6 terminal state and candidate state are outside the lifecycle algebra")
        return
    expected_review = {
        R6_PENDING_OUTCOME: R6_PENDING_OUTCOME,
        R6_ACCEPTED_OUTCOME: R6_ACCEPTED_OUTCOME,
        R6_BLOCKED_AFTER_FREEZE_OUTCOME: R6_BLOCKED_AFTER_FREEZE_OUTCOME,
    }[terminal_phase]
    if claim.get("review_result") != expected_review:
        errors.append("R6 terminal review result is invalid")
        return
    freeze_blob = claim.get("freeze_receipt_blob")
    if type(freeze_blob) is not str or not GIT_BLOB_ID.fullmatch(freeze_blob):
        errors.append("R6 terminal freeze receipt pointer is invalid")
        return
    freeze_receipt = _r6_validate_freeze_receipt(
        claim,
        roadmap=roadmap,
        repo_root=repo_root,
        authority=authority,
        current_capture_required=current_capture_required,
        errors=errors,
    )
    if freeze_receipt is None:
        return
    chain_head = claim.get("terminal_transition_blob")
    if terminal_phase == R6_PENDING_OUTCOME:
        transition = _r6_validate_transition_receipt(
            chain_head,
            phase=R6_PENDING_OUTCOME,
            previous_blob=freeze_blob,
            freeze_blob=freeze_blob,
            freeze_receipt=freeze_receipt,
            claim=claim,
            repo_root=repo_root,
            errors=errors,
        )
    else:
        transition = _r6_validate_transition_receipt(
            chain_head,
            phase=terminal_phase,
            previous_blob=None,  # Validate the exact P predecessor next.
            freeze_blob=freeze_blob,
            freeze_receipt=freeze_receipt,
            claim=claim,
            repo_root=repo_root,
            errors=errors,
        )
        if transition is not None:
            pending_blob = transition.get("previous_receipt_blob")
            pending = _r6_validate_transition_receipt(
                pending_blob,
                phase=R6_PENDING_OUTCOME,
                previous_blob=freeze_blob,
                freeze_blob=freeze_blob,
                freeze_receipt=freeze_receipt,
                claim=claim,
                repo_root=repo_root,
                errors=errors,
            )
            if pending is None:
                transition = None
    if transition is not None:
        _r6_validate_terminal_verification(
            claim,
            phase=terminal_phase,
            freeze_blob=freeze_blob,
            transition_blob=chain_head,
            transition=transition,
            errors=errors,
        )


def _validate_r5_blocked_before_freeze(claim: dict[str, Any], errors: list[str]) -> None:
    """Normalize R5's failed pre-freeze attempt without reviving its authority."""

    forbidden = {
        "freeze_receipt_blob",
        "terminal_transition_blob",
        "candidate_evidence_manifest_blob",
        "candidate_evidence_path",
        "candidate_frozen_at",
        "candidate_set_digest_sha256",
        "candidate_command_manifest_digest_sha256",
        "candidate_pytest_node_manifest_digest_sha256",
        "candidate_pytest_node_ids",
        "architecture_revision",
        "architecture_digest_sha256",
        "terminal_verification",
    }
    if not (
        claim.get("state") == "released_blocked"
        and claim.get("candidate_state") == R6_BLOCKED_BEFORE_FREEZE_OUTCOME
        and claim.get("candidate_blobs") == {}
        and claim.get("review_result") == R6_BLOCKED_BEFORE_FREEZE_OUTCOME
        and not (forbidden & set(claim))
    ):
        errors.append("R5 blocked-before-freeze history is not receiptless and exact")


def _validate_current_rolling_projection(
    *,
    repo_root: Path,
    authority: AuthoritySnapshotBundle,
    errors: list[str],
) -> None:
    guidance = authority.get(GUIDANCE_PATH)
    round6 = authority.get(ROUND6_PATH)
    open_work = authority.get(OPEN_WORK_PATH)
    if not all(isinstance(item, Mapping) for item in (guidance, round6, open_work)):
        errors.append("current rolling projection snapshots are unavailable")
        return
    assert isinstance(guidance, Mapping)
    assert isinstance(round6, Mapping)
    assert isinstance(open_work, Mapping)
    roadmaps = guidance.get("roadmaps")
    frontiers = round6.get("roadmap_frontiers")
    waves = round6.get("waves")
    guidance_summary = guidance.get("analysis_summary", {}).get("round6_parallel_execution")
    open_work_summary = open_work.get("current_position", {}).get("round6_parallel_execution")
    if not all(
        (
            isinstance(roadmaps, tuple),
            isinstance(frontiers, tuple),
            isinstance(waves, tuple),
            isinstance(guidance_summary, Mapping),
            isinstance(open_work_summary, Mapping),
        )
    ):
        errors.append("current rolling projection sources have an invalid shape")
        return
    active_members = [
        item["path"]
        for item in roadmaps
        if isinstance(item, Mapping)
        and type(item.get("path")) is str
        and str(item.get("profile", "")).startswith("active_")
    ]
    frontier_paths = [
        item["roadmap_path"]
        for item in frontiers
        if isinstance(item, Mapping) and type(item.get("roadmap_path")) is str
    ]
    unknown_roles = [
        item.get("membership_role")
        for item in frontiers
        if isinstance(item, Mapping)
        and (
            not isinstance(item.get("membership_role", "active"), str)
            or item.get("membership_role", "active")
            not in {"active", "retained_terminal_wave_evidence"}
        )
    ]
    active_frontiers = [
        item
        for item in frontiers
        if isinstance(item, Mapping) and item.get("membership_role", "active") == "active"
    ]
    retained_frontiers = [
        item
        for item in frontiers
        if isinstance(item, Mapping)
        and item.get("membership_role") == "retained_terminal_wave_evidence"
    ]
    active_frontier_paths = [item.get("roadmap_path") for item in active_frontiers]
    harbour_frontiers = [
        item
        for item in active_frontiers
        if item.get("roadmap_path") == "docs/plans/harbour-one-product-roadmap.json"
    ]
    harbour_identity_valid = bool(
        len(harbour_frontiers) == 1
        and isinstance(harbour_frontiers[0].get("slice"), Mapping)
        and harbour_frontiers[0]["slice"].get("id") == "H1R-04"
    )
    scheduled_ids: set[str] = set()
    for wave in waves:
        if not isinstance(wave, Mapping):
            continue
        state = wave.get("state")
        if isinstance(state, str) and (
            state.startswith("repo_accepted") or state.startswith("blocked_")
        ):
            slice_ids = wave.get("implementation_slice_ids")
            if isinstance(slice_ids, tuple):
                scheduled_ids.update(item for item in slice_ids if isinstance(item, str))
    retained_valid = True
    for frontier in retained_frontiers:
        path = frontier.get("roadmap_path")
        matching_profiles = [
            item.get("profile")
            for item in roadmaps
            if isinstance(item, Mapping) and item.get("path") == path
        ]
        contract = frontier.get("slice")
        state = contract.get("state") if isinstance(contract, Mapping) else None
        slice_id = contract.get("id") if isinstance(contract, Mapping) else None
        retained_valid = retained_valid and bool(
            matching_profiles == ["historical_done"]
            and isinstance(contract, Mapping)
            and contract.get("claimable") is False
            and isinstance(state, str)
            and state.startswith(("accepted_", "repo_accepted", "released_"))
            and slice_id == "ACPR-12-transactional-worktree-and-safe-tool-capsules"
            and slice_id in scheduled_ids
        )
    active_members = sorted(active_members)
    active_frontier_paths = sorted(active_frontier_paths)
    digest = _member_set_digest(active_members)
    expected_digest = digest
    common_fields = {
        "active_roadmap_count": 26,
        "active_frontier_count": 26,
        "retained_terminal_frontier_count": 1,
        "total_frontier_count": 27,
        "normalized_frontier_count": 27,
        "current_claimable_frontier_count": 0,
        "scheduled_repo_slice_count": 7,
        "wave_count": 3,
    }
    if not (
        not unknown_roles
        and len(active_members) == len(set(active_members)) == 26
        and len(frontier_paths) == len(set(frontier_paths)) == 27
        and all(_repository_path_key(path) is not None for path in frontier_paths)
        and active_frontier_paths == active_members
        and harbour_identity_valid
        and len(retained_frontiers) == 1
        and retained_valid
        and digest == expected_digest
        and round6.get("active_claims") == ()
        and all(guidance_summary.get(key) == value for key, value in common_fields.items())
        and guidance_summary.get("inventory_mode") == "rolling_projection"
        and isinstance(guidance_summary.get("inventory_source_blob"), str)
        and GIT_BLOB_ID.fullmatch(guidance_summary["inventory_source_blob"])
        and guidance_summary.get("inventory_member_count") == 26
        and guidance_summary.get("inventory_member_set_digest") == expected_digest
        and all(open_work_summary.get(key) == value for key, value in common_fields.items())
    ):
        errors.append("current rolling projection membership, roles, counts or digest disagrees")

    reconciliation = guidance_summary.get("active_membership_reconciliation")
    source_guidance = _materialized_json_blob(
        repo_root, guidance_summary.get("inventory_source_blob")
    )
    source_members = []
    if isinstance(source_guidance, dict):
        source_members = sorted(
            item["path"]
            for item in source_guidance.get("roadmaps", [])
            if isinstance(item, dict)
            and isinstance(item.get("path"), str)
            and str(item.get("profile", "")).startswith("active_")
        )
    baseline_round6 = (
        _materialized_json_blob(repo_root, reconciliation.get("baseline_round6_blob"))
        if isinstance(reconciliation, Mapping)
        else None
    )
    baseline_paths = sorted(
        item["roadmap_path"]
        for item in baseline_round6.get("roadmap_frontiers", [])
        if isinstance(item, dict) and isinstance(item.get("roadmap_path"), str)
    ) if isinstance(baseline_round6, dict) else []
    current_round6 = _thaw_json(round6)
    current_outside_frontiers = dict(current_round6)
    current_outside_frontiers.pop("roadmap_frontiers", None)
    baseline_outside_frontiers = dict(baseline_round6 or {})
    baseline_outside_frontiers.pop("roadmap_frontiers", None)
    retained_paths = sorted(
        item["roadmap_path"]
        for item in retained_frontiers
        if isinstance(item.get("roadmap_path"), str)
    )
    baseline_by_path = {
        item.get("roadmap_path"): item
        for item in (baseline_round6 or {}).get("roadmap_frontiers", [])
        if isinstance(item, dict)
    }
    current_by_path = {
        item.get("roadmap_path"): item
        for item in current_round6.get("roadmap_frontiers", [])
        if isinstance(item, dict)
    }
    retained_path = "docs/plans/autonomous-coding-production-readiness-roadmap.json"
    record_added = (
        reconciliation.get("frontier_added", ())
        if isinstance(reconciliation, Mapping)
        and isinstance(reconciliation.get("frontier_added", ()), (list, tuple))
        else ()
    )
    record_removed = (
        reconciliation.get("frontier_removed", ())
        if isinstance(reconciliation, Mapping)
        and isinstance(reconciliation.get("frontier_removed", ()), (list, tuple))
        else ()
    )
    record_retained = (
        reconciliation.get("retained_terminal_paths", ())
        if isinstance(reconciliation, Mapping)
        and isinstance(reconciliation.get("retained_terminal_paths", ()), (list, tuple))
        else ()
    )
    if not (
        isinstance(reconciliation, Mapping)
        and _thaw_json(reconciliation)
        == _thaw_json(open_work_summary.get("active_membership_reconciliation"))
        and reconciliation.get("corrected_round6_blob")
        == authority.blob_ids.get(ROUND6_PATH)
        and reconciliation.get("architecture_path")
        == "docs/plans/evidence/abc-round6-guidance-membership-reconciliation-architecture-20260904.json"
        and reconciliation.get("baseline_guidance_blob")
        == guidance_summary.get("inventory_source_blob")
        and source_members == active_members
        and reconciliation.get("baseline_guidance_active_count") == 26
        and reconciliation.get("baseline_guidance_active_digest") == expected_digest
        and reconciliation.get("baseline_round6_total_frontier_count") == len(baseline_paths) == 28
        and reconciliation.get("baseline_round6_untyped_frontier_digest")
        == _member_set_digest(baseline_paths)
        and reconciliation.get("corrected_guidance_active_count") == 26
        and reconciliation.get("corrected_guidance_active_digest") == expected_digest
        and reconciliation.get("corrected_round6_active_frontier_count") == 26
        and reconciliation.get("corrected_round6_retained_terminal_frontier_count") == 1
        and reconciliation.get("corrected_round6_total_frontier_count") == 27
        and current_outside_frontiers == baseline_outside_frontiers
        and tuple(record_added)
        == ("docs/plans/harbour-one-product-roadmap.json",)
        and set(frontier_paths) - set(baseline_paths)
        == set(record_added)
        and tuple(record_removed)
        == (
            "docs/plans/coding-agent-orchestration-consolidation-roadmap.md",
            "docs/plans/odysseus-harbor-one-autonomous-workspace-roadmap.md",
        )
        and set(baseline_paths) - set(frontier_paths)
        == set(record_removed)
        and _thaw_json(reconciliation.get("frontier_role_reclassified"))
        == [{
            "path": "docs/plans/autonomous-coding-production-readiness-roadmap.json",
            "from": "untyped_active_assumption",
            "to": "retained_terminal_wave_evidence",
        }]
        and tuple(record_retained)
        == ("docs/plans/autonomous-coding-production-readiness-roadmap.json",)
        and retained_paths == list(record_retained)
        and isinstance(baseline_by_path.get(retained_path), dict)
        and baseline_by_path[retained_path].get("membership_role") is None
        and isinstance(current_by_path.get(retained_path), dict)
        and current_by_path[retained_path].get("membership_role")
        == "retained_terminal_wave_evidence"
        and reconciliation.get("historical_records_preserved") is True
        and reconciliation.get("scope")
        == "current_active_membership_and_retained_terminal_provenance_only"
    ):
        errors.append("current active-membership reconciliation receipt disagrees")


def _validate_rolling_projection(
    typed_claims: list[tuple[dict[str, Any], AbcClaimV2]],
    *,
    repo_root: Path,
    authority: AuthoritySnapshotBundle,
    errors: list[str],
) -> None:
    _validate_current_rolling_projection(
        repo_root=repo_root,
        authority=authority,
        errors=errors,
    )
    if not typed_claims:
        return
    pin_maps = [dict(typed.pins) for _raw, typed in typed_claims]
    if (
        any(GUIDANCE_PATH not in pins or ROUND6_PATH not in pins for pins in pin_maps)
        or len({pins.get(GUIDANCE_PATH) for pins in pin_maps}) != 1
        or len({pins.get(ROUND6_PATH) for pins in pin_maps}) != 1
    ):
        errors.append("active v2 claims do not pin Guidance and Round-6 projection authority")
        return
    evidence_by_claim: dict[str, str] = {}
    canonical_evidence_paths: dict[PathKey, str] = {}
    for (_raw, typed), pins in zip(typed_claims, pin_maps, strict=True):
        evidence_paths = [
            path
            for path in pins
            if _is_evidence_json_path(path)
        ]
        if len(evidence_paths) != 1:
            errors.append(
                "active v2 claim does not bind exactly one Evidence receipt"
            )
            return
        evidence_path = evidence_paths[0]
        evidence_key = _repository_path_key(evidence_path)
        if (
            evidence_key is None
            or evidence_key in canonical_evidence_paths
            or typed.run_id in evidence_by_claim
        ):
            errors.append(
                "active v2 claims do not bind one-to-one canonical Evidence receipts"
            )
            return
        canonical_evidence_paths[evidence_key] = evidence_path
        evidence_by_claim[typed.run_id] = evidence_path
    if (
        len(evidence_by_claim) != len(typed_claims)
        or len(canonical_evidence_paths) != len(typed_claims)
        or _has_internal_path_scope_overlap(canonical_evidence_paths)
    ):
        errors.append(
            "active v2 claims do not bind one-to-one canonical Evidence receipts"
        )
        return
    evidence_documents = [
        authority.get(evidence_by_claim[typed.run_id])
        for _raw, typed in typed_claims
    ]
    guidance = authority.get(GUIDANCE_PATH)
    round6 = authority.get(ROUND6_PATH)
    if not all(
        isinstance(item, Mapping)
        for item in (*evidence_documents, guidance, round6)
    ):
        errors.append("rolling projection snapshots are unavailable")
        return
    assert isinstance(guidance, Mapping)
    assert isinstance(round6, Mapping)
    summary = guidance.get("analysis_summary", {}).get("round6_parallel_execution")
    receipt_fields = {
        "inventory_mode",
        "member_count",
        "member_set_digest",
        "terminal_waves_unchanged",
        "scheduled_slice_count_unchanged",
    }
    receipts: list[dict[str, Any]] = []
    receipt_bytes: list[bytes] = []
    try:
        for evidence in evidence_documents:
            frozen_receipt = evidence.get("inventory_receipt")
            receipt = (
                _thaw_json(frozen_receipt)
                if isinstance(frozen_receipt, Mapping)
                else None
            )
            _validate_json_tree(receipt)
            if not (
                type(receipt) is dict
                and set(receipt) == receipt_fields
                and type(receipt.get("inventory_mode")) is str
                and type(receipt.get("member_count")) is int
                and type(receipt.get("member_set_digest")) is str
                and type(receipt.get("terminal_waves_unchanged")) is bool
                and type(receipt.get("scheduled_slice_count_unchanged")) is int
            ):
                raise _JsonDocumentError("invalid_shape")
            receipts.append(receipt)
            receipt_bytes.append(
                json.dumps(
                    receipt,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
            )
    except (
        TypeError,
        ValueError,
        UnicodeError,
        RecursionError,
        _JsonDocumentError,
    ):
        errors.append("active v2 Evidence receipts do not share one exact inventory")
        return
    if not receipts or any(raw != receipt_bytes[0] for raw in receipt_bytes[1:]):
        errors.append("active v2 Evidence receipts do not share one exact inventory")
        return
    receipt = receipts[0]
    assert isinstance(receipt, Mapping)
    roadmaps = guidance.get("roadmaps")
    frontiers = round6.get("roadmap_frontiers")
    waves = round6.get("waves")
    if not all(
        (
            isinstance(summary, Mapping),
            isinstance(roadmaps, tuple),
            isinstance(frontiers, tuple),
            isinstance(waves, tuple),
        )
    ):
        errors.append("rolling projection sources have an invalid shape")
        return

    def active_members(document: Mapping[str, Any]) -> list[str]:
        entries = document.get("roadmaps")
        if not isinstance(entries, (list, tuple)):
            return []
        members = [
            item["path"]
            for item in entries
            if isinstance(item, Mapping)
            and type(item.get("path")) is str
            and str(item.get("profile", "")).startswith("active_")
        ]
        return sorted(members) if len(members) == len(set(members)) else []

    def frontier_members(document: Mapping[str, Any]) -> list[str]:
        entries = document.get("roadmap_frontiers")
        if not isinstance(entries, (list, tuple)):
            return []
        members = [
            item["roadmap_path"]
            for item in entries
            if isinstance(item, Mapping)
            and type(item.get("roadmap_path")) is str
            and item.get("membership_role", "active") == "active"
        ]
        return sorted(members) if len(members) == len(set(members)) else []

    def all_frontier_members(document: Mapping[str, Any]) -> list[str]:
        entries = document.get("roadmap_frontiers")
        if not isinstance(entries, (list, tuple)):
            return []
        members = [
            item["roadmap_path"]
            for item in entries
            if isinstance(item, Mapping) and type(item.get("roadmap_path")) is str
        ]
        return sorted(members) if len(members) == len(set(members)) else []

    def scheduled_count(document: Mapping[str, Any]) -> int:
        wave_items = document.get("waves")
        if not isinstance(wave_items, (list, tuple)):
            return -1
        if any(
            not isinstance(wave, Mapping)
            or not isinstance(wave.get("implementation_slice_ids"), (list, tuple))
            for wave in wave_items
        ):
            return -1
        return sum(len(wave["implementation_slice_ids"]) for wave in wave_items)

    members = active_members(guidance)
    frontier_paths = frontier_members(round6)
    all_frontier_paths = all_frontier_members(round6)
    digest = _member_set_digest(members)
    claimable = sum(item.get("claimable") is True for item in frontiers if isinstance(item, Mapping))
    scheduled = scheduled_count(round6)
    receipt_fields = {
        "inventory_mode",
        "member_count",
        "member_set_digest",
        "terminal_waves_unchanged",
        "scheduled_slice_count_unchanged",
    }
    if not (
        len(members) == 26
        and len(frontier_paths) == 26
        and members == frontier_paths
        and len(all_frontier_paths) == 27
        and digest == summary.get("inventory_member_set_digest")
        and summary.get("inventory_member_count") == 26
        and summary.get("active_roadmap_count") == 26
        and summary.get("active_frontier_count") == 26
        and summary.get("retained_terminal_frontier_count") == 1
        and summary.get("total_frontier_count") == 27
        and summary.get("normalized_frontier_count") == 27
        and summary.get("current_claimable_frontier_count") == 0
        and summary.get("wave_count") == 3
        and summary.get("scheduled_repo_slice_count") == 7
        and claimable == 0
        and len(waves) == 3
        and scheduled == 7
        and round6.get("active_claims") == ()
        and set(receipt) == receipt_fields
        and receipt.get("inventory_mode") == "rolling_projection"
        and receipt.get("member_count") == 26
        and receipt.get("member_set_digest") == digest
        and receipt.get("terminal_waves_unchanged") is True
        and receipt.get("scheduled_slice_count_unchanged") == 7
    ):
        errors.append("rolling projection count, digest, claimability, waves or schedule disagrees")

    source_blob_id = summary.get("inventory_source_blob")
    source_guidance = _materialized_json_blob(repo_root, source_blob_id)
    source_members = (
        active_members(source_guidance) if isinstance(source_guidance, dict) else []
    )
    if not (
        isinstance(source_blob_id, str)
        and GIT_BLOB_ID.fullmatch(source_blob_id)
        and source_members == members
        and len(source_members) == 26
        and _member_set_digest(source_members) == digest
    ):
        errors.append("rolling projection source Guidance blob disagrees")

    change = summary.get("inventory_denominator_change")
    reconciliation = summary.get("active_membership_reconciliation")
    change_fields = {
        "old_count",
        "new_count",
        "old_digest",
        "new_digest",
        "old_round6_blob",
        "new_round6_blob",
        "added",
        "removed",
        "scope",
    }
    old_round6 = (
        _materialized_json_blob(repo_root, change.get("old_round6_blob"))
        if isinstance(change, Mapping)
        else None
    )
    new_round6 = (
        _materialized_json_blob(repo_root, change.get("new_round6_blob"))
        if isinstance(change, Mapping)
        else None
    )
    old_paths = all_frontier_members(old_round6) if isinstance(old_round6, dict) else []
    new_paths = all_frontier_members(new_round6) if isinstance(new_round6, dict) else []
    deferred_paths = (
        "docs/plans/agent-maintenance-safety-harness-roadmap.json",
        "docs/plans/live-contextual-layer-roadmap.json",
    )
    if not (
        isinstance(change, Mapping)
        and set(change) == change_fields
        and change.get("old_count") == len(old_paths) == 30
        and change.get("new_count") == len(new_paths) == 28
        and change.get("old_digest") == _member_set_digest(old_paths)
        and change.get("new_digest")
        == _member_set_digest(new_paths)
        == (
            reconciliation.get("baseline_round6_untyped_frontier_digest")
            if isinstance(reconciliation, Mapping)
            else None
        )
        and tuple(change.get("added", ())) == ()
        and tuple(change.get("removed", ())) == deferred_paths
        and not (set(new_paths) - set(old_paths))
        and set(old_paths) - set(new_paths) == set(deferred_paths)
        and pin_maps[0][ROUND6_PATH] == authority.blob_ids.get(ROUND6_PATH)
        and isinstance(old_round6, dict)
        and isinstance(new_round6, dict)
        and old_round6.get("waves")
        == new_round6.get("waves")
        == _thaw_json(waves)
        and scheduled_count(old_round6) == scheduled_count(new_round6) == 7
        and new_round6.get("active_claims") == []
        and sum(
            item.get("claimable") is True
            for item in new_round6.get("roadmap_frontiers", [])
            if isinstance(item, dict)
        ) == 0
        and change.get("scope")
        == "inventory_membership_only_terminal_waves_and_seven_scheduled_slices_unchanged"
    ):
        errors.append("rolling projection denominator provenance is not exact")


def _capture_v2_authority_documents(
    raw_claims: list[Any],
    *,
    repo_root: Path,
    errors: list[str],
    primary_snapshot: JsonSnapshot | None = None,
) -> AuthoritySnapshotBundle:
    """Capture every active-claim authority input once before semantic use.

    The normal runtime path never uses ``_path_blob_id`` after this point.  The
    two loader seams remain only for deterministic in-memory unit fixtures.
    """

    paths = {OPEN_WORK_PATH, GUIDANCE_PATH, ROUND6_PATH}
    declared_absent_paths: set[str] = set()
    frozen_current_paths: set[str] = set()
    for raw in raw_claims:
        if type(raw) is not dict:
            continue
        state = raw.get("state")
        is_r6 = _r6_is_validator_claim(raw)
        if not (
            state == "running"
            or (
                is_r6
                and state == "released_pending_post_release_qa"
                and raw.get("candidate_state") == "frozen_for_independent_qa"
            )
        ):
            continue
        pins = raw.get("read_only_pins")
        if type(pins) is dict:
            paths.update(
                pin_path for pin_path in pins if _repository_path_key(pin_path)
            )
        allowed = raw.get("allowed_paths")
        if type(allowed) is list:
            paths.update(path for path in allowed if _repository_path_key(path))
        baselines = raw.get("baseline_blobs")
        if type(baselines) is dict:
            absent_baselines = {
                candidate_path
                for candidate_path, blob_id in baselines.items()
                if blob_id == "absent" and _repository_path_key(candidate_path)
            }
            if _frozen_new_product_capture_paths(raw) == tuple(sorted(absent_baselines)):
                frozen_current_paths.update(absent_baselines)
            else:
                declared_absent_paths.update(absent_baselines)
        evidence_path = raw.get("candidate_evidence_path")
        if _repository_path_key(evidence_path):
            paths.add(evidence_path)

    documents: dict[str, Mapping[str, Any]] = {}
    blob_ids: dict[str, str] = {}
    raw_bytes: dict[str, bytes] = {}
    captured_absent_paths: set[str] = set()
    if primary_snapshot is not None:
        blob_ids["docs/plans/parallel-roadmap-execution-round7.json"] = (
            primary_snapshot.blob_id
        )
        raw_bytes["docs/plans/parallel-roadmap-execution-round7.json"] = (
            primary_snapshot.raw
        )

    synthetic_primary = _load is not _DEFAULT_LOAD
    synthetic_auxiliary = _load_auxiliary is not _DEFAULT_LOAD_AUXILIARY
    overlapping_capture_paths = declared_absent_paths & frozen_current_paths
    if overlapping_capture_paths:
        errors.append("authority capture path belongs to overlapping modes")
    for relative_path in sorted(paths):
        absolute_path = repo_root / relative_path
        try:
            if relative_path in overlapping_capture_paths:
                continue
            if relative_path in frozen_current_paths:
                if synthetic_primary or synthetic_auxiliary:
                    errors.append(
                        f"{relative_path} synthetic current authority is unavailable"
                    )
                    continue
                raw, blob_id = _capture_file_bytes(absolute_path)
                raw_bytes[relative_path] = raw
                blob_ids[relative_path] = blob_id
            elif relative_path in declared_absent_paths:
                if synthetic_primary or synthetic_auxiliary:
                    errors.append(
                        f"{relative_path} synthetic absence authority is unavailable"
                    )
                    continue
                try:
                    absolute_path.lstat()
                except FileNotFoundError:
                    captured_absent_paths.add(relative_path)
                    continue
                except OSError:
                    errors.append(f"{relative_path} declared absent path is unavailable")
                    continue
                errors.append(f"{relative_path} declared absent path is materialized")
                continue
            elif relative_path.endswith(".json"):
                if relative_path == OPEN_WORK_PATH and synthetic_primary:
                    loaded = _load(absolute_path)
                    snapshot = None
                elif synthetic_auxiliary:
                    loaded = _load_auxiliary(absolute_path)
                    snapshot = None
                else:
                    snapshot = _load_json_snapshot(absolute_path, role=relative_path)
                    loaded = _thaw_json(snapshot.root)
                if type(loaded) is not dict:
                    raise _JsonDocumentError("non_object_root")
                documents[relative_path] = _freeze_json(loaded)
                if snapshot is None:
                    # Synthetic unit fixtures have no filesystem byte identity;
                    # retain the real fixture file identity only in that mode.
                    blob_id = _path_blob_id(absolute_path)
                    if blob_id is None:
                        raise _JsonDocumentError("read_failed")
                else:
                    blob_id = snapshot.blob_id
                    raw_bytes[relative_path] = snapshot.raw
                blob_ids[relative_path] = blob_id
            else:
                try:
                    raw, blob_id = _capture_file_bytes(absolute_path)
                except _JsonDocumentError:
                    if _load is not _DEFAULT_LOAD:
                        # Fixture-only pseudo writer paths model queue overlap;
                        # production never takes this compatibility branch.
                        continue
                    raise
                raw_bytes[relative_path] = raw
                blob_ids[relative_path] = blob_id
        except (_DuplicateJsonKeyError, _JsonDocumentError) as error:
            errors.append(_auxiliary_load_error(relative_path, error))
    ordered_paths = tuple(sorted(blob_ids))
    absent_paths_readback = (
        CAPTURED_ABSENT_PATHS_READBACK_PASS
        if declared_absent_paths and captured_absent_paths == declared_absent_paths
        else "not_applicable"
    )
    return AuthoritySnapshotBundle(
        documents=MappingProxyType(dict(documents)),
        blob_ids=MappingProxyType(dict(blob_ids)),
        paths=ordered_paths,
        raw_bytes=MappingProxyType(dict(raw_bytes)),
        absent_paths=tuple(sorted(captured_absent_paths)),
        absent_paths_readback=absent_paths_readback,
    )


def _frozen_new_product_capture_paths(raw: Any) -> tuple[str, ...] | None:
    """Select only one exact frozen new-product writer capture."""

    variant = _claim_origin_variant(raw)
    partition = (
        _materialized_repo_genesis_partition_readback(raw)
        if variant == MATERIALIZED_REPO_GENESIS_MODE
        else None
    )
    if not (
        type(raw) is dict
        and raw.get("state") == "running"
        and raw.get("candidate_state") == "frozen_for_independent_qa"
        and _claim_origin_variant(raw)
        in {NEW_PRODUCT_PATHS_MODE, MATERIALIZED_REPO_GENESIS_MODE}
    ):
        return None
    allowed = raw.get("allowed_paths")
    baselines = raw.get("baseline_blobs")
    candidates = raw.get("candidate_blobs")
    transfer = raw.get("claim_transfer")
    allowed_keys, allowed_valid = _collect_repository_paths(allowed)
    if not (
        allowed_valid
        and type(allowed) is list
        and allowed == sorted(allowed)
        and len(allowed_keys) == len(allowed)
        and type(baselines) is dict
        and set(baselines) == set(allowed)
        and all(
            blob_id == "absent"
            or (type(blob_id) is str and GIT_BLOB_ID.fullmatch(blob_id))
            for blob_id in baselines.values()
        )
        and type(candidates) is dict
        and set(candidates) == set(allowed)
        and all(
            type(blob_id) is str and GIT_BLOB_ID.fullmatch(blob_id)
            for blob_id in candidates.values()
        )
        and type(transfer) is dict
        and type(transfer.get("new_product_writer_paths")) is list
        and transfer.get("new_product_writer_paths")
        == sorted(transfer["new_product_writer_paths"])
        and set(transfer["new_product_writer_paths"])
        == {path for path, blob_id in baselines.items() if blob_id == "absent"}
        and raw.get("new_evidence_writer_paths", []) == []
        and (
            raw.get("baseline_readback") == CAPTURED_ABSENT_PATHS_READBACK_PASS
            if variant == NEW_PRODUCT_PATHS_MODE
            else partition is not None
        )
    ):
        return None
    return (
        partition[1]
        if partition is not None
        else tuple(sorted(
            path for path, blob_id in baselines.items() if blob_id == "absent"
        ))
    )


def _r7_is_validator_claim(claim: Any) -> bool:
    return type(claim) is dict and claim.get("slice_id") == R7_VALIDATOR_SLICE


def _r7_handoff_anchor(value: Any) -> str | None:
    """Parse the only backwards-compatible R transport, without aliases."""

    if type(value) is not str:
        return None
    match = re.fullmatch(
        r"abc-r7-arch-v3\|revision=" + re.escape(R7_ARCHITECTURE_REVISION)
        + r"\|digest=" + re.escape(R7_ARCHITECTURE_DIGEST)
        + r"\|anchor=([0-9a-f]{40})",
        value,
    )
    return match.group(1) if match else None


def _r7_anchor_matches(
    claim: Mapping[str, Any], anchor: Mapping[str, Any]
) -> bool:
    receipt = anchor.get("inventory_receipt")
    return (
        set(anchor) == R7_ANCHOR_FIELDS
        and anchor.get("schema_version") == 1
        and anchor.get("kind") == R7_ANCHOR_KIND
        and anchor.get("claim_id") == claim.get("claim_id")
        and anchor.get("slice_id") == R7_VALIDATOR_SLICE
        and anchor.get("architecture_revision") == R7_ARCHITECTURE_REVISION
        and anchor.get("architecture_digest_sha256") == R7_ARCHITECTURE_DIGEST
        and anchor.get("authority_revision") == {
            "claimed_against_round7_revision": 64,
            "registered_round7_revision": 65,
        }
        and anchor.get("allowed_paths") == claim.get("allowed_paths")
        and anchor.get("writer_baseline_blobs") == claim.get("baseline_blobs")
        and anchor.get("read_only_pins") == claim.get("read_only_pins")
        and isinstance(anchor.get("root_only_paths"), list)
        and anchor.get("root_only_paths") == [
            R7_EVIDENCE_PATH, OPEN_WORK_PATH,
            "docs/plans/parallel-roadmap-execution-round7.json",
        ]
        and isinstance(receipt, Mapping)
        and set(receipt) == {
            "inventory_mode", "member_count", "member_set_digest",
            "terminal_waves_unchanged", "scheduled_slice_count_unchanged",
        }
        and receipt.get("inventory_mode") == "rolling_projection"
        and receipt.get("member_count") == 30
        and receipt.get("terminal_waves_unchanged") is True
        and receipt.get("scheduled_slice_count_unchanged") == 7
        and anchor.get("historical_and_current_correction_ledger") is not None
    )


def _validate_r7_lifecycle(
    claim: Mapping[str, Any], *, roadmap: Mapping[str, Any], repo_root: Path,
    errors: list[str],
) -> None:
    """Validate R7's registration firewall and closed lifecycle entry state."""

    state_pair = (claim.get("state"), claim.get("candidate_state"))
    if state_pair not in R7_LEGAL_PAIRS:
        errors.append("R7 lifecycle pair is outside the closed algebra")
        return
    packet = roadmap.get("architecture_packets", {})
    registration = packet.get(R7_VALIDATOR_SLICE) if type(packet) is dict else None
    transfer = claim.get("claim_transfer")
    anchor_blob = _r7_handoff_anchor(
        transfer.get("architecture_handoff") if type(transfer) is dict else None
    )
    if not (
        claim.get("architecture_revision") == R7_ARCHITECTURE_REVISION
        and claim.get("architecture_digest_sha256") == R7_ARCHITECTURE_DIGEST
        and isinstance(registration, Mapping)
        and registration.get("architecture_revision") == R7_ARCHITECTURE_REVISION
        and registration.get("digest_sha256") == R7_ARCHITECTURE_DIGEST
        and registration.get("claim_id") == claim.get("claim_id")
        and registration.get("evidence_path") == R7_EVIDENCE_PATH
        and registration.get("registration_anchor_blob") == anchor_blob
        and claim.get("read_only_pins", {}).get(R7_EVIDENCE_PATH)
        == registration.get("evidence_blob")
        and type(anchor_blob) is str
    ):
        errors.append("R7 architecture registration or R transport is invalid")
        return
    anchor = _materialized_json_blob(repo_root, anchor_blob)
    if type(anchor) is not dict or not _r7_anchor_matches(claim, anchor):
        errors.append("R7 registration anchor is missing, malformed or not claim-bound")
        return
    if state_pair == ("running", "not_frozen"):
        forbidden = {
            "freeze_receipt_blob", "terminal_transition_blob",
            "terminal_evidence_digest_sha256", "released_at", "released_reason",
            "review_result", "terminal_verification", "acceptance_evidence",
        }
        if forbidden & set(claim) or claim.get("candidate_blobs") != {}:
            errors.append("R7 unfrozen running claim publishes frozen or terminal authority")


def _prepare_claim_basetemp(repo_root: Path, claim_id: str, tier: str) -> str | None:
    """Create only the exact claim parent; reject aliases and reparse points."""

    if claim_id != "ABC-R7-VALIDATOR-R7-REV65" or tier not in {"focused", "central"}:
        return None
    root = repo_root / ".abc-test-tmp"
    parent = root / claim_id
    leaf = parent / tier
    try:
        for path in (repo_root, root, parent):
            if path.exists() and (path.is_symlink() or os.path.islink(path)):
                return None
        root.mkdir(exist_ok=True)
        parent.mkdir(exist_ok=True)
        if leaf.exists() or leaf.is_symlink() or os.path.islink(leaf):
            return None
        return leaf.relative_to(repo_root).as_posix()
    except (OSError, ValueError):
        return None


def validate(path: Path, repo_root: Path) -> list[str]:
    errors: list[str] = []
    try:
        if _load is _DEFAULT_LOAD:
            primary_snapshot = _load_json_snapshot(path, role="round7")
            roadmap = _thaw_json(primary_snapshot.root)
        else:
            # In-memory loader injection is an isolated unit-test seam.  Real
            # validation always starts from the immutable byte snapshot above.
            primary_snapshot = None
            roadmap = _load(path)
    except _DuplicateJsonKeyError:
        return ["Round-7 roadmap contains a duplicate JSON key"]
    except _JsonDocumentError as error:
        return [f"Round-7 roadmap is unavailable: {error.code}"]

    if roadmap.get("kind") != "odysseus.integration_spine_execution_round":
        errors.append("unexpected roadmap kind")
    if roadmap.get("round_id") != "ROUND7-HARBOR-ONE-INTEGRATION-CONVERGENCE":
        errors.append("unexpected round id")

    claim = roadmap.get("authoring_claim", {})
    if set(claim.get("allowed_paths", [])) != EXPECTED_AUTHORING_PATHS:
        errors.append("authoring claim does not own the exact five authoring paths")
    if claim.get("live_actions") is not False:
        errors.append("authoring claim must forbid live actions")

    authority = roadmap.get("authority_model", {})
    if set(authority) != {
        "definition_authority_snapshot",
        "execution_authority_snapshot",
        "projections",
        "existing_reuse",
    }:
        errors.append("two-layer authority model is incomplete")

    gates = roadmap.get("gate_taxonomy", {})
    if set(gates) != {
        "autonomous_repo_gate",
        "automatic_policy_gate",
        "user_ui_acceptance_gate",
        "user_live_gate",
    }:
        errors.append("gate taxonomy must separate repo, policy, UI and live gates")

    source_roadmaps = roadmap.get("source_roadmaps", [])
    if len(source_roadmaps) != len(set(source_roadmaps)):
        errors.append("source roadmap list contains duplicates")
    for source in source_roadmaps:
        if not (repo_root / source).is_file():
            errors.append(f"missing source roadmap: {source}")

    preconditions = roadmap.get("preconditions", [])
    if [item.get("id") for item in preconditions] != [
        "R7-P0-accepted-rejected-checkout-separation",
        "R7-P1-status-and-router-drift-normalization",
    ]:
        errors.append("checkout separation and status normalization must be first")

    waves = roadmap.get("waves", [])
    wave_ids = [wave.get("id") for wave in waves]
    if wave_ids != EXPECTED_WAVES:
        errors.append("roadmap must contain the five convergence waves in order")

    claim_identity_records: list[tuple[str, str]] = []

    def record_claim_identity(label: str, claim: Any) -> None:
        if isinstance(claim, dict) and claim.get("run_id"):
            claim_identity_records.append((label, claim["run_id"]))

    record_claim_identity("authoring_claim", roadmap.get("authoring_claim"))
    for item in preconditions:
        record_claim_identity(f"precondition:{item.get('id', '')}", item.get("execution_claim"))
    for wave in waves:
        for item in wave.get("slices", []):
            record_claim_identity(f"slice:{item.get('id', '')}", item.get("execution_claim"))
    for supporting_name in EXPECTED_SUPPORTING_CLAIM_PATHS:
        record_claim_identity(supporting_name, roadmap.get(supporting_name))
    for index, claim in enumerate(roadmap.get("external_lane_claims", [])):
        record_claim_identity(f"external:{index}", claim)
    identity_run_ids = [run_id for _label, run_id in claim_identity_records]
    if len(identity_run_ids) != len(set(identity_run_ids)):
        errors.append("claim run ids are not globally unique across claim records")

    long_run = roadmap.get("long_run_state", {})
    active_run_ids = long_run.get("active_claims", [])
    if len(active_run_ids) != len(set(active_run_ids)):
        errors.append("long-run active claim ids contain duplicates")
    leases = long_run.get("agent_leases", [])
    leases_by_claim: dict[str, list[dict[str, Any]]] = {}
    for lease in leases:
        leases_by_claim.setdefault(lease.get("claim_id", ""), []).append(lease)
    if set(leases_by_claim) != set(active_run_ids) or any(
        len(items) != 1 for items in leases_by_claim.values()
    ):
        errors.append("long-run leases do not exactly match active claims")

    all_nodes: dict[str, dict[str, Any]] = {}
    active_implementation_slice_count = 0
    started_implementation_slice_count = 0
    active_implementation_run_ids: set[str] = set()
    active_writer_claims: list[tuple[str, str, dict[PathKey, str]]] = []
    active_read_only_pins: list[tuple[str, str, dict[PathKey, str]]] = []
    active_claim_identities: list[tuple[str, str, str]] = []
    for item in preconditions:
        all_nodes[item.get("id", "")] = item
    for wave in waves:
        all_nodes[wave.get("id", "")] = wave
        child_statuses: list[str] = []
        for item in wave.get("slices", []):
            item_id = item.get("id", "")
            if item_id in all_nodes:
                errors.append(f"duplicate node id: {item_id}")
            all_nodes[item_id] = item
            if item.get("claimable") is not False:
                errors.append(f"implementation slice is claimable during authoring: {item_id}")
            status = item.get("status")
            child_statuses.append(status)
            if status not in IMPLEMENTATION_SLICE_STATES:
                errors.append(f"implementation slice has unexpected status: {item_id}")
                continue
            claim_present = "execution_claim" in item
            claim = item.get("execution_claim")
            if status == "planned_nonclaimable":
                if claim_present:
                    errors.append(f"planned implementation slice has a claim object: {item_id}")
                continue

            started_implementation_slice_count += 1
            if not isinstance(claim, dict):
                errors.append(f"started implementation slice lacks a claim: {item_id}")
                continue
            run_id = claim.get("run_id")
            claim_state = claim.get("state")
            if claim.get("live_actions") is not False:
                errors.append(f"implementation claim permits live actions: {item_id}")
            if not run_id or claim.get("slice_id") != item_id or not claim.get("owner"):
                errors.append(f"implementation claim identity is incomplete: {item_id}")
            allowed_paths = set(claim.get("allowed_paths", []))
            canonical_allowed_paths, canonical_paths_valid = _collect_repository_paths(
                claim.get("allowed_paths", []), require_nonempty=True
            )
            if not canonical_paths_valid or _has_internal_path_scope_overlap(
                canonical_allowed_paths
            ):
                errors.append(f"implementation claim paths are malformed or overlap: {item_id}")
            if status == "running_exact_claim" and not _paths_are_canonical(
                claim.get("allowed_paths", [])
            ):
                errors.append(f"implementation claim paths are not canonical: {item_id}")
            future_paths = set(item.get("future_claim_paths", []))
            if allowed_paths != future_paths:
                errors.append(f"implementation claim paths differ from future claim paths: {item_id}")
            implementation_hotfiles = claim.get("hotfiles", [])
            _implementation_hotfile_paths, implementation_hotfiles_valid = (
                _collect_repository_paths(implementation_hotfiles)
            )
            if (
                not implementation_hotfiles_valid
                or not set(implementation_hotfiles).issubset(allowed_paths)
            ):
                errors.append(f"implementation claim hotfiles escape allowed paths: {item_id}")
            if status == "running_exact_claim" and not _paths_are_canonical(
                implementation_hotfiles
            ):
                errors.append(f"implementation claim hotfiles are not canonical: {item_id}")

            if status == "running_exact_claim":
                active_implementation_slice_count += 1
                if run_id:
                    active_implementation_run_ids.add(run_id)
                    active_writer_claims.append(
                        (item_id, run_id, canonical_allowed_paths)
                    )
                    active_claim_identities.append((item_id, run_id, item_id))
                if not _has_strict_lease_window(
                    claim.get("acquired_at"), claim.get("lease_expires_at")
                ):
                    errors.append(f"running implementation claim timestamps are invalid: {item_id}")
                if claim_state not in {"claimed", "running"}:
                    errors.append(f"running implementation slice lacks an active claim: {item_id}")
                if run_id not in active_run_ids:
                    errors.append(f"running implementation claim is absent from long-run state: {item_id}")
                matching_leases = leases_by_claim.get(run_id, [])
                if len(matching_leases) != 1:
                    errors.append(f"running implementation claim lacks one exact lease: {item_id}")
                else:
                    lease = matching_leases[0]
                    if (
                        lease.get("owner") != claim.get("owner")
                        or lease.get("expires_at") != claim.get("lease_expires_at")
                        or not claim.get("acquired_at")
                    ):
                        errors.append(f"running implementation claim lease is inconsistent: {item_id}")
            elif status == "accepted_released":
                if claim_state != "released_accepted" or not claim.get("released_at"):
                    errors.append(f"accepted implementation slice lacks a released claim: {item_id}")
                if run_id in active_run_ids or leases_by_claim.get(run_id):
                    errors.append(f"accepted implementation claim remains active: {item_id}")
            elif status == "blocked":
                if (
                    claim_state not in {"released_blocked", "blocked"}
                    or not claim.get("released_at")
                ):
                    errors.append(f"blocked implementation slice lacks a blocked claim: {item_id}")
                if run_id in active_run_ids or leases_by_claim.get(run_id):
                    errors.append(f"blocked implementation claim remains active: {item_id}")

        wave_status = wave.get("status")
        if wave_status not in WAVE_STATES:
            errors.append(f"wave has unexpected status: {wave.get('id', '')}")
        elif child_statuses:
            if all(status == "planned_nonclaimable" for status in child_statuses):
                expected_wave_status = "planned_nonclaimable"
            elif all(status == "accepted_released" for status in child_statuses):
                expected_wave_status = "accepted_released"
            elif any(status == "blocked" for status in child_statuses) and not any(
                status == "running_exact_claim" for status in child_statuses
            ):
                expected_wave_status = "blocked"
            else:
                expected_wave_status = "running"
            if wave_status != expected_wave_status:
                errors.append(
                    f"wave status disagrees with child slice states: {wave.get('id', '')}"
                )

    supporting_run_ids: set[str] = set()
    for supporting_name, expected_paths in EXPECTED_SUPPORTING_CLAIM_PATHS.items():
        supporting_claim = roadmap.get(supporting_name)
        if not isinstance(supporting_claim, dict):
            errors.append(f"missing supporting claim record: {supporting_name}")
            continue
        support_run_id = supporting_claim.get("run_id")
        support_state = supporting_claim.get("state")
        support_paths_value = supporting_claim.get("allowed_paths", [])
        support_allowed_paths = (
            set(support_paths_value) if isinstance(support_paths_value, list) else set()
        )
        support_canonical_paths, support_canonical_valid = _collect_repository_paths(
            support_paths_value, require_nonempty=True
        )
        if (
            not support_run_id
            or not supporting_claim.get("slice_id")
            or not supporting_claim.get("owner")
        ):
            errors.append(f"supporting claim identity is incomplete: {supporting_name}")
        if (
            not isinstance(support_paths_value, list)
            or len(support_paths_value) != len(support_allowed_paths)
            or support_allowed_paths != expected_paths
            or not support_canonical_valid
            or _has_internal_path_scope_overlap(support_canonical_paths)
            or (
                support_state == "running"
                and not _paths_are_canonical(support_paths_value)
            )
        ):
            errors.append(f"supporting claim paths differ from declared scope: {supporting_name}")
        support_hotfiles = supporting_claim.get("hotfiles", [])
        expected_hotfiles = EXPECTED_SUPPORTING_CLAIM_HOTFILES[supporting_name]
        if (
            not isinstance(support_hotfiles, list)
            or len(support_hotfiles) != len(set(support_hotfiles))
            or set(support_hotfiles) != expected_hotfiles
            or (
                support_state == "running"
                and not _paths_are_canonical(support_hotfiles)
            )
        ):
            errors.append(f"supporting claim hotfiles differ from declared scope: {supporting_name}")
        if supporting_claim.get("live_actions") is not False:
            errors.append("supporting claim permits live actions")
        if support_state == "running":
            if not support_run_id or not supporting_claim.get("slice_id"):
                errors.append("running supporting claim identity is incomplete")
            else:
                supporting_run_ids.add(support_run_id)
                active_writer_claims.append(
                    (supporting_name, support_run_id, support_canonical_paths)
                )
                active_claim_identities.append(
                    (supporting_name, support_run_id, supporting_claim.get("slice_id"))
                )
                if not _has_strict_lease_window(
                    supporting_claim.get("acquired_at"),
                    supporting_claim.get("lease_expires_at"),
                ):
                    errors.append(
                        f"running supporting claim timestamps are invalid: {supporting_name}"
                    )
                matching_leases = leases_by_claim.get(support_run_id, [])
                if support_run_id not in active_run_ids or len(matching_leases) != 1:
                    errors.append("running supporting claim is not bound to long-run state")
                elif (
                    matching_leases[0].get("owner") != supporting_claim.get("owner")
                    or matching_leases[0].get("expires_at")
                    != supporting_claim.get("lease_expires_at")
                ):
                    errors.append("running supporting claim lease is inconsistent")
        elif support_state in {"released_accepted", "released_blocked"}:
            if not supporting_claim.get("released_at"):
                errors.append("released supporting claim lacks release metadata")
            if support_run_id in active_run_ids or leases_by_claim.get(support_run_id):
                errors.append("released supporting claim remains active")
        else:
            errors.append("supporting claim has unexpected state")

    external_run_ids: set[str] = set()
    seen_external_run_ids: set[str] = set()
    typed_active_claims: list[tuple[dict[str, Any], AbcClaimV2]] = []
    external_claims = roadmap.get("external_lane_claims", [])
    if not isinstance(external_claims, list):
        errors.append("external lane claims must be a list")
        external_claims = []
    authority_bundle = _capture_v2_authority_documents(
        external_claims,
        repo_root=repo_root,
        errors=errors,
        primary_snapshot=primary_snapshot,
    )
    claims_by_run_id = {
        claim["run_id"]: claim
        for claim in external_claims
        if isinstance(claim, dict)
        and type(claim.get("run_id")) is str
        and claim.get("run_id")
    }
    active_run_id_set = {
        run_id for run_id in active_run_ids if type(run_id) is str
    } if isinstance(active_run_ids, list) else set()
    for index, external_claim in enumerate(external_claims):
        path = f"$.external_lane_claims[{index}]"
        if not isinstance(external_claim, dict):
            errors.append(f"external lane claim is not an object: {index}")
            continue
        run_id = external_claim.get("run_id")
        state = external_claim.get("state")
        allowed_paths = external_claim.get("allowed_paths", [])
        hotfiles = external_claim.get("hotfiles", [])
        pins = external_claim.get("read_only_pins", {})
        if (
            not run_id
            or not external_claim.get("slice_id")
            or not external_claim.get("owner")
            or not isinstance(allowed_paths, list)
            or not allowed_paths
            or len(allowed_paths) != len(set(allowed_paths))
        ):
            errors.append(f"external lane claim identity or paths are incomplete: {index}")
            continue
        if run_id in seen_external_run_ids:
            errors.append(f"external lane claim id is duplicated: {run_id}")
        seen_external_run_ids.add(run_id)
        canonical_allowed_paths, canonical_paths_valid = _collect_repository_paths(
            allowed_paths, require_nonempty=True
        )
        if not canonical_paths_valid or _has_internal_path_scope_overlap(
            canonical_allowed_paths
        ):
            errors.append(f"external lane writer path is malformed: {index}")
        if state == "running" and not _paths_are_canonical(allowed_paths):
            errors.append(f"external lane writer paths are not canonical: {index}")
        _external_hotfile_paths, external_hotfiles_valid = _collect_repository_paths(
            hotfiles
        )
        if (
            not external_hotfiles_valid
            or not set(hotfiles).issubset(allowed_paths)
            or (state == "running" and not _paths_are_canonical(hotfiles))
        ):
            errors.append(f"external lane hotfiles escape allowed paths: {index}")
        pin_paths: dict[PathKey, str] = {}
        pins_valid = isinstance(pins, dict)
        if pins_valid:
            for pin_path, blob_id in pins.items():
                pin_key = _repository_path_key(pin_path)
                if (
                    pin_key is None
                    or pin_key in pin_paths
                    or not isinstance(blob_id, str)
                    or not GIT_BLOB_ID.fullmatch(blob_id)
                ):
                    pins_valid = False
                    break
                pin_paths[pin_key] = pin_path
        if pins_valid and _has_internal_path_scope_overlap(pin_paths):
            pins_valid = False
        if not pins_valid:
            errors.append(f"external lane read-only pins are malformed: {index}")
            pin_paths = {}
        if _first_path_scope_overlap(pin_paths, canonical_allowed_paths):
            errors.append(f"external lane read-only pins overlap writer paths: {index}")
        if external_claim.get("live_actions") is not False:
            errors.append(f"external lane claim permits live actions: {index}")
        if state == "running":
            typed_claim = _validate_abc_claim_v2(
                external_claim,
                index=index,
                repo_root=repo_root,
                authority=authority_bundle,
                authority_revision=long_run.get("revision"),
                claims_by_run_id=claims_by_run_id,
                active_run_ids=active_run_id_set,
                leases_by_claim=leases_by_claim,
                errors=errors,
                roadmap=roadmap,
            )
            if typed_claim is not None:
                typed_active_claims.append((external_claim, typed_claim))
                if _r6_is_validator_claim(external_claim):
                    _validate_r6_lifecycle(
                        external_claim,
                        roadmap=roadmap,
                        repo_root=repo_root,
                        authority=authority_bundle,
                        current_capture_required=True,
                        errors=errors,
                    )
                elif _r7_is_validator_claim(external_claim):
                    _validate_r7_lifecycle(
                        external_claim,
                        roadmap=roadmap,
                        repo_root=repo_root,
                        errors=errors,
                    )
            external_run_ids.add(run_id)
            active_writer_claims.append((path, run_id, canonical_allowed_paths))
            active_read_only_pins.append((path, run_id, pin_paths))
            active_claim_identities.append(
                (path, run_id, external_claim.get("slice_id"))
            )
            if not _has_strict_lease_window(
                external_claim.get("acquired_at"), external_claim.get("lease_expires_at")
            ):
                errors.append(f"external running claim timestamps are invalid: {index}")
            matching_leases = leases_by_claim.get(run_id, [])
            if run_id not in active_run_ids or len(matching_leases) != 1:
                errors.append(f"external running claim is not bound to long-run state: {index}")
            elif (
                matching_leases[0].get("owner") != external_claim.get("owner")
                or matching_leases[0].get("expires_at")
                != external_claim.get("lease_expires_at")
            ):
                errors.append(f"external running claim lease is inconsistent: {index}")
        elif state in {
            "released_pending_post_release_qa",
            "released_accepted",
            "released_blocked",
        }:
            if not external_claim.get("released_at"):
                errors.append(f"external released claim lacks release metadata: {index}")
            if run_id in active_run_ids or leases_by_claim.get(run_id):
                errors.append(f"external released claim remains active: {index}")
            if (
                external_claim.get("slice_id")
                == "R7-validator-snapshot-atomicity-and-terminal-receipt-r4"
            ):
                _validate_terminal_receipt(
                    external_claim, repo_root=repo_root, errors=errors
                )
                expected_review_result = (
                    R4_PENDING_REVIEW_RESULT
                    if state == "released_pending_post_release_qa"
                    else R4_ACCEPTED_REVIEW_RESULT
                    if state == "released_accepted"
                    else None
                )
                if (
                    expected_review_result is not None
                    and external_claim.get("review_result") != expected_review_result
                ):
                    errors.append("terminal R4 release lifecycle result is invalid")
            elif _r5_is_validator_claim(external_claim):
                _validate_r5_blocked_before_freeze(external_claim, errors)
            elif _r6_is_validator_claim(external_claim):
                _validate_r6_lifecycle(
                    external_claim,
                    roadmap=roadmap,
                    repo_root=repo_root,
                    authority=authority_bundle,
                    current_capture_required=(
                        state == "released_pending_post_release_qa"
                    ),
                    errors=errors,
                )
        else:
            errors.append(f"external lane claim has unexpected state: {index}")

    active_identity_run_ids = [run_id for _name, run_id, _slice_id in active_claim_identities]
    if len(active_identity_run_ids) != len(set(active_identity_run_ids)):
        errors.append("active claim run ids are not globally unique")
    active_identity_slice_ids = [
        slice_id for _name, _run_id, slice_id in active_claim_identities
    ]
    if len(active_identity_slice_ids) != len(set(active_identity_slice_ids)):
        errors.append("active claim slice ids are not globally unique")

    for index, (left_name, _left_run_id, left_paths) in enumerate(active_writer_claims):
        for right_name, _right_run_id, right_paths in active_writer_claims[index + 1 :]:
            overlap = _first_path_scope_overlap(left_paths, right_paths)
            if overlap:
                errors.append(
                    "active claim writer paths overlap: "
                    f"{left_name} / {right_name} / {overlap[0]} / {overlap[1]}"
                )
    for pin_name, _pin_run_id, pin_paths in active_read_only_pins:
        for writer_name, _writer_run_id, writer_paths in active_writer_claims:
            overlap = _first_path_scope_overlap(pin_paths, writer_paths)
            if overlap:
                errors.append(
                    "active read-only pin collides with writer: "
                    f"{pin_name} / {writer_name} / {overlap[0]} / {overlap[1]}"
                )

    open_work = authority_bundle.get(OPEN_WORK_PATH)
    if not isinstance(open_work, Mapping):
        errors.append("Open Work is unavailable: read_failed")
        open_work = {}
    current_position = open_work.get("current_position")
    if not isinstance(current_position, Mapping) or "external_lane_claims" not in current_position:
        errors.append("Open Work external claim projection is missing")
        projected_external_claims = None
    else:
        projected_external_claims = _thaw_json(current_position["external_lane_claims"])
    if projected_external_claims != external_claims:
        errors.append("Open Work external claim projection disagrees with long-run state")

    for raw_claim, typed_claim in typed_active_claims:
        _validate_active_freeze_binding(
            raw_claim,
            typed_claim,
            repo_root=repo_root,
            authority=authority_bundle,
            errors=errors,
        )
    _validate_rolling_projection(
        typed_active_claims,
        repo_root=repo_root,
        authority=authority_bundle,
        errors=errors,
    )

    expected_active_run_ids = (
        active_implementation_run_ids | supporting_run_ids | external_run_ids
    )
    if set(active_run_ids) != expected_active_run_ids:
        errors.append("long-run active claims disagree with bound running claims")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visited:
            return
        if node_id in visiting:
            errors.append(f"internal dependency cycle at {node_id}")
            return
        visiting.add(node_id)
        for dependency in all_nodes[node_id].get("depends_on", []):
            if dependency in all_nodes:
                visit(dependency)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in all_nodes:
        if node_id:
            visit(node_id)

    for slice_id, required_paths in REQUIRED_COMPOSITION_PATHS.items():
        actual_paths = set(all_nodes.get(slice_id, {}).get("future_claim_paths", []))
        missing_paths = sorted(required_paths - actual_paths)
        if missing_paths:
            errors.append(
                f"{slice_id} is missing required actual composition paths: "
                + ", ".join(missing_paths)
            )

    for slice_id, required_dependencies in REQUIRED_EXTERNAL_DEPENDENCIES.items():
        actual_dependencies = set(all_nodes.get(slice_id, {}).get("depends_on", []))
        missing_dependencies = sorted(required_dependencies - actual_dependencies)
        if missing_dependencies:
            errors.append(
                f"{slice_id} is missing required external dependencies: "
                + ", ".join(missing_dependencies)
            )

    for dependency, source_path in EXTERNAL_DEPENDENCY_SOURCES.items():
        source = repo_root / source_path
        if not source.is_file():
            errors.append(f"missing external dependency source: {source_path}")
            continue
        if dependency not in source.read_text(encoding="utf-8"):
            errors.append(
                f"external dependency {dependency} is not declared by {source_path}"
            )

    scheduling = roadmap.get("scheduling", {})
    if scheduling.get("parallel_after_runtime_composition") != [
        "ROUND7-C-WORKER-AND-GATE-CONVERGENCE",
        "ROUND7-D-LIFECYCLE-CONVERGENCE",
    ]:
        errors.append("worker/gate and lifecycle convergence must be the parallel middle waves")

    completion = roadmap.get("completion", {})
    if completion.get("implementation_started") is not bool(
        started_implementation_slice_count
    ):
        errors.append("completion implementation_started disagrees with slice states")
    if completion.get("active_implementation_claims") != active_implementation_slice_count:
        errors.append("completion active claim count disagrees with slice states")
    if completion.get("live_actions") is not False:
        errors.append("roadmap authoring must have no live actions")

    return errors


def _public_error_codes(errors: list[str]) -> list[str]:
    """Convert internal diagnostics into the closed, bounded CLI contract."""

    code_by_prefix = {
        "invalid_cli": "invalid_cli",
        "internal_error": "internal_error",
        "Round-7 roadmap contains a duplicate JSON key": "duplicate_json_key",
        "Round-7 roadmap is unavailable: malformed_json": "malformed_json",
        "Round-7 roadmap is unavailable: non_object_root": "non_object_root",
        "Round-7 roadmap is unavailable: nonfinite_json": "nonfinite_json",
        "Round-7 roadmap is unavailable: limit_exceeded": "limit_exceeded",
        "Round-7 roadmap is unavailable: read_failed": "read_failed",
    }
    codes: list[str] = []
    for error in errors:
        code = next(
            (value for prefix, value in code_by_prefix.items() if error == prefix),
            "validation_failed",
        )
        if code not in codes:
            codes.append(code)
        if len(codes) >= MAX_PUBLIC_ERROR_COUNT:
            break
    encoded = json.dumps(codes, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    return codes if len(encoded) <= MAX_PUBLIC_ERROR_BYTES else ["validation_failed"]


def _closed_cli_tokens(argv: Any) -> tuple[str, str] | None:
    """Parse the complete public protocol without argparse or token echo."""

    if type(argv) is not list or any(type(token) is not str for token in argv):
        return None
    try:
        if any(
            not token
            or len(token.encode("utf-8", errors="strict")) > MAX_REPOSITORY_PATH_BYTES
            for token in argv
        ):
            return None
    except UnicodeError:
        return None
    default_roadmap = "docs/plans/parallel-roadmap-execution-round7.json"
    if argv == []:
        return default_roadmap, "text"
    if len(argv) == 2 and argv[0] == "--format" and argv[1] in {"text", "json"}:
        return default_roadmap, argv[1]
    if len(argv) == 2 and argv[0] == "--roadmap":
        return argv[1], "text"
    if (
        len(argv) == 4
        and argv[0] == "--roadmap"
        and argv[2] == "--format"
        and argv[3] in {"text", "json"}
    ):
        return argv[1], argv[3]
    return None


def _emit_closed_result(
    *, format_name: str, errors: list[str], internal_failure: bool = False
) -> int:
    public_errors = ["internal_error"] if internal_failure else _public_error_codes(errors)
    result = {
        "ok": False if internal_failure else not errors,
        "roadmap": "round7_authority",
        "errors": public_errors,
    }
    if format_name == "json":
        sys.stdout.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    elif result["ok"]:
        sys.stdout.write("PASS: Harbor One Round-7 integration spine is structurally valid\n")
    else:
        for error in public_errors:
            sys.stdout.write(f"FAIL: {error}\n")
    return 0 if result["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    """Own every public failure mode with a bounded, content-free result."""

    format_name = "json"
    try:
        raw_argv = sys.argv[1:] if argv is None else argv
        if (
            type(raw_argv) is list
            and len(raw_argv) == 7
            and raw_argv[0] == "--prepare-basetemp"
            and raw_argv[1] == "--claim-id"
            and raw_argv[3] == "--tier"
            and raw_argv[5] == "--format"
            and raw_argv[6] == "json"
        ):
            repo_root = Path(__file__).resolve().parents[1]
            prepared = _prepare_claim_basetemp(repo_root, raw_argv[2], raw_argv[4])
            return _emit_closed_result(
                format_name="json",
                errors=[] if prepared is not None else ["invalid_basetemp"],
            )
        parsed = _closed_cli_tokens(raw_argv)
        if parsed is None:
            return _emit_closed_result(format_name="json", errors=["invalid_cli"])
        roadmap_token, format_name = parsed
        repo_root = Path(__file__).resolve().parents[1]
        roadmap_path = Path(roadmap_token)
        if not roadmap_path.is_absolute():
            roadmap_path = repo_root / roadmap_path
        errors = validate(roadmap_path, repo_root)
        return _emit_closed_result(format_name=format_name, errors=errors)
    except BaseException:
        # This is the sole owned projection for parser, filesystem, Git,
        # serialization and validator faults.  Do not write exception data to
        # stderr or reflect it in stdout.
        return _emit_closed_result(
            format_name=format_name,
            errors=["internal_error"],
            internal_failure=True,
        )


if __name__ == "__main__":
    raise SystemExit(main())
