from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
SIRP_PATH = ROOT / "docs/plans/security-incident-response-production-completion-roadmap.json"
EVIDENCE_PATH = ROOT / "docs/plans/security-incident-response-production-completion-evidence.json"
OPEN_WORK_PATH = ROOT / "docs/plans/open-work-completion-master-roadmap.json"
GUIDANCE_PATH = ROOT / "docs/plans/multi-agent-execution-guidance.json"
TRP_PATH = ROOT / "docs/plans/faster-whisper-transcription-protocol-roadmap.json"
LIVE_EVIDENCE_PATH = (
    ROOT / "docs/plans/security-incident-response-live-observe-delivery-evidence.md"
)
PUBLISH_PACKET_PATH = (
    ROOT / "docs/plans/security-incident-response-publish-authorization-packet.md"
)

SIRP_00 = "SIRP-00-roadmap-authority-and-gap-freeze"
SIRP_01 = "SIRP-01-durable-incident-action-store"
SIRP_02 = "SIRP-02-evidence-broker-and-redaction-boundary"
SIRP_03 = "SIRP-03-classifier-and-explanation-contract"
SIRP_04 = "SIRP-04-store-backed-mcp-and-api"
SIRP_05 = "SIRP-05-operator-auth-commands-and-delivery"
SIRP_06 = "SIRP-06-executor-kernel-and-preflight"
SIRP_07 = "SIRP-07-crowdsec-typed-executor"
SIRP_08 = "SIRP-08-session-invalidation-typed-executor"
SIRP_09 = "SIRP-09-verification-rollback-and-audit"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_repo_relative(path: str) -> None:
    candidate = PurePosixPath(path)
    assert path
    assert "\\" not in path
    assert not candidate.is_absolute()
    assert ".." not in candidate.parts
    assert "*" not in path
    assert "?" not in path


def _ancestor_map(slice_by_id: dict[str, dict]) -> dict[str, set[str]]:
    memo: dict[str, set[str]] = {}

    def ancestors(slice_id: str, visiting: set[str]) -> set[str]:
        if slice_id in memo:
            return memo[slice_id]
        assert slice_id not in visiting, f"dependency cycle at {slice_id}"
        result: set[str] = set()
        next_visiting = visiting | {slice_id}
        for dependency in slice_by_id[slice_id]["depends_on"]:
            result.add(dependency)
            result.update(ancestors(dependency, next_visiting))
        memo[slice_id] = result
        return result

    for slice_id in slice_by_id:
        ancestors(slice_id, set())
    return memo


def test_sirp_has_complete_uniform_slice_gate_and_dag_contract() -> None:
    plan = _load(SIRP_PATH)

    required_top_level = {
        "completion_denominator",
        "claims",
        "route",
        "handoff",
        "authority",
        "dependency_dag",
        "baseline_evidence_and_gaps",
        "target_architecture",
        "exact_explanation_contract",
        "source_contracts",
        "evidence_contract",
        "global_stop_rules",
        "verification_commands",
        "autonomy_modes",
        "capability_matrix",
        "hard_invariants",
        "action_policy_disposition",
        "slice_queue",
        "gate_queue",
        "definition_of_done",
        "completion_matrix",
        "next_frontier",
    }
    assert required_top_level <= plan.keys()
    assert plan["completion_denominator"]["track"] == "assisted_security_response_100_percent"
    assert "automatic_or_autonomous_lockdown" in plan["completion_denominator"]["does_not_mean"]
    assert plan["handoff"]["evidence_reference"] == EVIDENCE_PATH.relative_to(ROOT).as_posix()
    assert plan["latest_sirp07_sirp08_run_start"]["slice_ids"] == [
        SIRP_07,
        SIRP_08,
    ]

    slices = plan["slice_queue"]
    assert len(slices) == 16
    slice_by_id = {item["id"]: item for item in slices}
    assert len(slice_by_id) == len(slices)
    assert set(plan["dependency_dag"]["nodes"]) == set(slice_by_id)

    required_slice_fields = set(plan["slice_contract_fields"])
    assert required_slice_fields
    for item in slices:
        assert set(item) == required_slice_fields
        assert isinstance(item["priority"], int)
        assert isinstance(item["claimable"], bool)
        assert item["completion_matrix"]["product_complete"] == "no"
        assert set(item["hotfiles"]) <= set(item["allowed_paths"])
        for path in item["allowed_paths"]:
            _assert_repo_relative(path)

    gate_by_id = {item["id"]: item for item in plan["gate_queue"]}
    assert len(gate_by_id) == len(plan["gate_queue"])
    for item in slices:
        assert set(item["depends_on"]) <= set(slice_by_id)
        assert set(item["gate_requirements"]) <= set(gate_by_id)

    declared_edges = {tuple(edge) for edge in plan["dependency_dag"]["edges"]}
    dependency_edges = {
        (dependency, item["id"])
        for item in slices
        for dependency in item["depends_on"]
    }
    assert declared_edges == dependency_edges

    ancestors = _ancestor_map(slice_by_id)
    for index, left in enumerate(slices):
        for right in slices[index + 1 :]:
            overlap = set(left["allowed_paths"]) & set(right["allowed_paths"])
            if overlap:
                assert (
                    left["id"] in ancestors[right["id"]]
                    or right["id"] in ancestors[left["id"]]
                ), f"same-wave path overlap: {left['id']} / {right['id']} / {sorted(overlap)}"


def test_sirp_covers_explanation_sources_actions_and_bounded_real_executors() -> None:
    plan = _load(SIRP_PATH)
    slice_by_id = {item["id"]: item for item in plan["slice_queue"]}
    gate_ids = {item["id"] for item in plan["gate_queue"]}

    expected_explanation_fields = {
        "auth_outcome",
        "principal_ref",
        "source_familiarity",
        "session_created",
        "affected_session_refs",
        "containment_state",
        "evidence_freshness",
        "known_unknowns",
        "raw_content_visible=false",
    }
    assert expected_explanation_fields <= set(plan["exact_explanation_contract"]["required_fields"])

    expected_sources = {
        "authentication events",
        "CrowdSec decisions",
        "reverse proxy summary",
        "Prometheus and Loki",
        "runtime envelopes",
        "Debian diagnostic readiness",
    }
    assert {item["source"] for item in plan["source_contracts"]} == expected_sources
    debian_contract = next(
        item["contract"]
        for item in plan["source_contracts"]
        if item["source"] == "Debian diagnostic readiness"
    )
    assert "odysseus-homeserver-probe" in debian_contract
    assert "caller-supplied remote command" in debian_contract

    expected_prepare_types = {
        "crowdsec_temp_block",
        "crowdsec_unblock",
        "service_restart",
        "scheduler_pause",
        "scheduler_retry",
        "raptorgraph_maintenance_restart",
        "nextcloud_import_retry",
        "token_rotation_prepare",
        "session_invalidate_prepare",
        "cloudflare_tunnel_change",
        "deploy_rollback",
        "log_level_increase",
    }
    dispositions = {
        item["type"]: item for item in plan["action_policy_disposition"]
    }
    assert expected_prepare_types <= set(dispositions)
    for item in dispositions.values():
        assert item["disposition"] in {
            "typed_executor",
            "manual_handoff",
            "never_allowed_in_SIRP",
        }
        if item["gate"] != "none":
            assert item["gate"] in gate_ids

    mcp_slice = slice_by_id["SIRP-04-store-backed-mcp-and-api"]
    assert "mcp_servers/debug_server.py" in mcp_slice["allowed_paths"]
    assert any("persisted incidents server-side" in item for item in mcp_slice["acceptance"])

    kernel = slice_by_id["SIRP-06-executor-kernel-and-preflight"]
    assert kernel["gate_requirements"] == []
    assert any("default-disabled typed execute route" in item for item in kernel["acceptance"])

    crowdsec = slice_by_id["SIRP-07-crowdsec-typed-executor"]
    session = slice_by_id["SIRP-08-session-invalidation-typed-executor"]
    assert "production-capable but default-disabled" in crowdsec["goal"]
    assert "production-capable but default-disabled" in session["goal"]
    assert "crowdsec-remediation-go" in slice_by_id["SIRP-13-crowdsec-canary"]["gate_requirements"]
    assert (
        "security-incident-session-invalidation-go"
        in slice_by_id["SIRP-14-session-invalidation-canary"]["gate_requirements"]
    )

    for item in plan["slice_queue"]:
        if item["class"] == "needs_live_go":
            assert item["claimable"] is False
            assert item["completion_matrix"]["tested"] == "no"
    active_claims = [claim for claim in plan["claims"] if claim["state"] == "active"]
    assert active_claims == []
    observe_claim = next(
        claim
        for claim in plan["claims"]
        if claim["run_id"] == "ABC-SEC120-20260728-SIRP12-OBSERVE"
    )
    assert observe_claim["state"] == "released"
    assert observe_claim["slice_id"] == "SIRP-12-observe-packet-preflight"
    assert observe_claim["external_action_executed"] is True
    assert observe_claim["mutation_performed"] is False
    assert observe_claim["live_actions"] is True
    assert observe_claim["allowed_paths"] == [
        "docs/plans/security-incident-response-live-observe-delivery-evidence.md",
        "docs/plans/security-incident-response-production-completion-roadmap.json",
        "docs/plans/security-incident-response-production-completion-evidence.json",
        "docs/plans/open-work-completion-master-roadmap.json",
        "docs/plans/multi-agent-execution-guidance.json",
        "tests/test_security_incident_response_production_completion_roadmap.py",
    ]
    decision_claim = next(
        claim
        for claim in plan["claims"]
        if claim["run_id"]
        == "ABC-SEC120-20260728-SIRP12-DELIVERY-READINESS-DECISION"
    )
    assert decision_claim["state"] == "released"
    assert decision_claim["live_actions"] is False
    assert decision_claim["external_action_executed"] is False
    assert decision_claim["allowed_paths"] == observe_claim["allowed_paths"]
    assert all(
        claim["state"] == "released" for claim in plan["claims"] if claim not in active_claims
    )
    for index, left in enumerate(active_claims):
        for right in active_claims[index + 1 :]:
            assert not set(left["allowed_paths"]) & set(right["allowed_paths"])


def test_sirp_is_registered_once_with_exact_next_frontier_and_trp_is_released() -> None:
    sirp = _load(SIRP_PATH)
    open_work = _load(OPEN_WORK_PATH)
    guidance = _load(GUIDANCE_PATH)
    trp = _load(TRP_PATH)

    source = "docs/plans/security-incident-response-production-completion-roadmap.json"
    assert open_work["source_of_truth"].count(source) == 1

    lane = next(item for item in open_work["completion_lanes"] if item["id"] == "OWM-7")
    assert lane["source_roadmap"] == source
    assert lane["safe_default"].endswith("keep_delivery_and_all_effectful_paths_disabled")

    queue_items = [
        item for item in open_work["abc_execution_queue"] if item["id"].startswith("SIRP-")
    ]
    assert [item["id"] for item in queue_items] == [
        SIRP_00,
        SIRP_01,
        SIRP_02,
        SIRP_03,
        SIRP_04,
        SIRP_05,
        SIRP_06,
        SIRP_07,
        SIRP_08,
        SIRP_09,
        "SIRP-10-offline-integration-and-attack-matrix",
        "SIRP-11-activation-packet-and-runbooks",
        "SIRP-12-live-observe-and-delivery",
    ]
    assert queue_items[0]["status"] == "accepted_2026-07-27"
    assert queue_items[0]["claimable"] is False
    assert queue_items[1]["status"] == "accepted_2026-07-27"
    assert queue_items[1]["claimable"] is False
    assert queue_items[2]["status"] == "accepted_2026-07-27"
    assert queue_items[2]["claimable"] is False
    assert queue_items[3]["status"] == "accepted_2026-07-27"
    assert queue_items[3]["claimable"] is False
    assert queue_items[3]["claim_blocker"] is None
    assert queue_items[4]["status"] == "accepted_2026-07-27"
    assert queue_items[4]["claimable"] is False
    assert queue_items[4]["claim_blocker"] is None
    assert queue_items[5]["status"] == "accepted_2026-07-27"
    assert queue_items[5]["claimable"] is False
    assert queue_items[5]["gate_requirements"] == []
    assert queue_items[5]["claim_blocker"] is None
    assert queue_items[6]["status"] == "accepted_2026-07-27"
    assert queue_items[6]["claimable"] is False
    assert queue_items[6]["gate_requirements"] == []
    assert queue_items[6]["claim_blocker"] is None
    assert queue_items[7]["status"] == "accepted_2026-07-28"
    assert queue_items[7]["claimable"] is False
    assert queue_items[7]["gate_requirements"] == []
    assert queue_items[7]["claim_blocker"] is None
    assert queue_items[8]["status"] == "accepted_2026-07-28"
    assert queue_items[8]["claimable"] is False
    assert queue_items[8]["gate_requirements"] == []
    assert queue_items[8]["claim_blocker"] is None
    assert queue_items[9]["status"] == "accepted_2026-07-28"
    assert queue_items[9]["claimable"] is False
    assert queue_items[9]["gate_requirements"] == []
    assert queue_items[9]["claim_blocker"] is None
    assert queue_items[10]["status"] == "accepted_2026-07-28"
    assert queue_items[10]["claimable"] is False
    assert queue_items[10]["gate_requirements"] == []
    assert queue_items[10]["claim_blocker"] == (
        "accepted after one bounded fix cycle and two deep Sol review rounds"
    )
    assert queue_items[11]["status"] == "accepted_2026-07-28"
    assert queue_items[11]["claimable"] is False
    assert queue_items[11]["gate_requirements"] == []
    assert queue_items[11]["claim_blocker"] == (
        "accepted after one bounded fix cycle and two deep Sol review rounds"
    )
    assert queue_items[12]["status"] == (
        "observe_completed_B_C_accepted_D0A_wrapper_ready_waiting_publish"
    )
    assert queue_items[12]["claimable"] is False
    assert queue_items[12]["authorization_status"] == (
        "observe_used_consumed_delivery_not_go_deploy_not_go"
    )
    assert queue_items[12]["sirp12_started"] is True
    assert queue_items[12]["observe_external_action_executed"] is True
    assert queue_items[12]["observe_mutation_performed"] is False
    assert queue_items[12]["invocation_counter"] == 1
    assert queue_items[12]["result_counter"] == 1

    slice_by_id = {item["id"]: item for item in sirp["slice_queue"]}
    assert slice_by_id[SIRP_00]["status"] == "accepted_2026-07-27"
    assert slice_by_id[SIRP_00]["claimable"] is False
    assert slice_by_id[SIRP_01]["status"] == "accepted_2026-07-27"
    assert slice_by_id[SIRP_01]["claimable"] is False
    assert slice_by_id[SIRP_01]["gate_requirements"] == []
    assert slice_by_id[SIRP_02]["status"] == "accepted_2026-07-27"
    assert slice_by_id[SIRP_02]["claimable"] is False
    assert slice_by_id[SIRP_02]["blockers"] == []
    assert slice_by_id[SIRP_03]["status"] == "accepted_2026-07-27"
    assert slice_by_id[SIRP_03]["claimable"] is False
    assert slice_by_id[SIRP_03]["blockers"] == []
    assert slice_by_id[SIRP_04]["status"] == "accepted_2026-07-27"
    assert slice_by_id[SIRP_04]["claimable"] is False
    assert slice_by_id[SIRP_04]["blockers"] == []
    assert slice_by_id[SIRP_05]["status"] == "accepted_2026-07-27"
    assert slice_by_id[SIRP_05]["claimable"] is False
    assert slice_by_id[SIRP_05]["gate_requirements"] == [
        "SIRP-OPERATOR-AUTH-GO"
    ]
    assert slice_by_id[SIRP_05]["blockers"] == []
    assert slice_by_id[SIRP_06]["status"] == "accepted_2026-07-27"
    assert slice_by_id[SIRP_06]["claimable"] is False
    assert slice_by_id[SIRP_06]["blockers"] == []
    assert slice_by_id[SIRP_07]["status"] == "accepted_2026-07-28"
    assert slice_by_id[SIRP_07]["claimable"] is False
    assert slice_by_id[SIRP_08]["status"] == "accepted_2026-07-28"
    assert slice_by_id[SIRP_08]["claimable"] is False
    assert slice_by_id[SIRP_09]["status"] == "accepted_2026-07-28"
    assert slice_by_id[SIRP_09]["claimable"] is False
    sirp_10 = slice_by_id["SIRP-10-offline-integration-and-attack-matrix"]
    assert sirp_10["status"] == "accepted_2026-07-28"
    assert sirp_10["claimable"] is False
    assert sirp_10["blockers"] == []
    sirp_11 = slice_by_id["SIRP-11-activation-packet-and-runbooks"]
    assert sirp_11["status"] == "accepted_2026-07-28"
    assert sirp_11["claimable"] is False
    sirp_12 = slice_by_id["SIRP-12-live-observe-and-delivery"]
    assert sirp_12["status"] == (
        "observe_completed_B_C_accepted_D_packet_ready_waiting_deploy_live_go"
    )
    assert sirp_12["claimable"] is False
    assert sirp["next_frontier"] == {
        "slice": "SIRP-12-live-observe-and-delivery",
        "preconditions": [
            "SIRP-11-activation-packet-and-runbooks accepted",
            "SIRP-12A-R2 strict readiness contract accepted",
            "OPS-ALERT-B accepted",
            "OPS-ALERT-C accepted",
            (
                "SEC140 observer published at "
                "9ea87e67464015cedbeeaada9117899edcab3ae2 with independent "
                "remote readback"
            ),
        ],
        "gate_requirements": [
            "one new action-specific read-only Compose capability observation grant",
            "OPS-ALERT-DELIVERY-GO",
            "observability-live-smoke-go",
            "debian-observability-live-go",
            "log-retention-policy-go",
            "deploy-live-go",
        ],
        "owner_of_queue_registration": "root",
        "claim_status_now": (
            "sec144_fix_accepted_waiting_repo_only_publication_readiness_and_separate_git_authority"
        ),
        "dependency_order": [
            "prepare publication readiness for the accepted SEC144 transport fix",
            (
                "publish the corrected revision under separate Git authority and "
                "obtain a new separately reviewed one-use observation packet"
            ),
            "observe the corrected published Compose capability once without retry",
            "resolve any remaining real deployment blocker",
            "bind and execute D deployment with rollback and independent readback",
            "obtain fresh strict redacted runtime readiness",
            "bind one exact E delivery packet",
            (
                "send exactly once and independently read back durable receipt plus "
                "human confirmation"
            ),
        ],
    }

    guidance_entries = [item for item in guidance["roadmaps"] if item["path"] == source]
    assert len(guidance_entries) == 1
    assert guidance_entries[0]["profile"] == "gate_only"
    assert guidance_entries[0]["state"] == (
        "ops_alert_D0A_fixed_wrapper_deep_accepted_waiting_path_scoped_publish"
    )
    assert len(guidance["roadmaps"]) == guidance["inventory"]["total_count"]
    assert sum(guidance["analysis_summary"]["profile_counts"].values()) == len(
        guidance["roadmaps"]
    )

    assert trp["status"].startswith("deferred_by_operator_priority_2026-07-27")
    trp_05 = next(item for item in trp["slice_queue"] if item["id"].startswith("TRP-05-"))
    assert trp_05["claimable"] is False
    assert trp_05["status"] == "deferred_by_operator_priority_2026-07-27"
    trp_claim = next(item for item in trp["claims"] if item["slice_id"] == trp_05["id"])
    assert trp_claim["state"] == "released"
    assert trp_claim["release_reason"] == (
        "superseded_by_SIRP_operator_priority_preserved_dirty_diff"
    )


def test_sirp11_packets_are_bounded_non_authorizing_and_secret_safe() -> None:
    claimed_docs = (
        ROOT / "docs/plans/security-incident-response-activation-packet.md",
        ROOT / "docs/runbooks/security-incident-response.md",
        ROOT / "docs/runbooks/crowdsec-remediation.md",
        ROOT / "docs/runbooks/telegram-security-incident.md",
        ROOT / "docs/plans/ops-security-console-live-runbook.md",
    )
    assert all(path.is_file() for path in claimed_docs)
    documents = {path: path.read_text(encoding="utf-8") for path in claimed_docs}
    packet = documents[claimed_docs[0]]
    combined = "\n".join(documents.values())

    for heading in (
        "## Observe packet",
        "## Delivery packet",
        "## CrowdSec packet",
        "## Session packet",
        "## Temporal-closure packet",
        "## Deployment boundary",
        "## Redacted handoff card",
    ):
        assert heading in packet
    for required in (
        "Target class and bounded scope",
        "Timeout and grant expiry",
        "Grant status and revocation",
        "Required evidence",
        "Rollback/recovery",
        "Independent readback",
        "Abort/stop conditions",
        "Operator decision and post-action status",
        "unused | used | expired | revoked",
        "execution must start before expiry",
        "grants no Go",
        "must not be used as a reusable approval",
    ):
        assert required in packet

    canonical_gate_ids = {
        "OPS-ALERT-DELIVERY-GO",
        "observability-live-smoke-go",
        "debian-observability-live-go",
        "log-retention-policy-go",
        "deploy-live-go",
        "OPS-REMEDIATION-GO",
        "crowdsec-remediation-go",
        "mcp-remediation-tools-go",
        "security-incident-session-invalidation-go",
        "security-incident-temporal-closure-go",
    }
    assert {gate for gate in canonical_gate_ids if gate in combined} == canonical_gate_ids

    fixed_probe = "ssh -F ops/homeserver/ssh_config odysseus-homeserver-probe"
    assert combined.count(fixed_probe) == 3
    assert len(re.findall(r"\bssh\s+-", combined, flags=re.IGNORECASE)) == 4
    d0_command = (
        "ssh -F ops/homeserver/ssh_config odysseus-homeserver "
        "'cd /opt/odysseus && exec python3 "
        "ops/homeserver/redacted_predeploy_observation.py'"
    )
    assert packet.count(d0_command) == 1
    assert "GO ABC-SEC123 D0 PREDEPLOY READ-ONLY OBSERVATION ONCE <=30S EXPIRES RUN_END" in packet
    assert "WAITING_PATH_SCOPED_PUBLISH_AND_EXACT_GO" in packet
    assert "`rollback_snapshot_available` are exactly `true`" in packet
    assert "rollback_snapshot_observation_evidence_sha256" in packet
    assert "run-backup-gate-evidence.sh` is excluded because it" in packet
    assert len(re.findall(r"odysseus-homeserver(?!-probe)", combined)) == 3

    sensitive_shape_counts = (
        len(
            re.findall(
                r"(?i)\b(?:api[_-]?key|secret|token|password|authorization|cookie)"
                r"\b\s*[:=]\s*(?!<|none\b|null\b|redacted\b)\S+",
                combined,
            )
        ),
        len(re.findall(r"(?i)\bbearer\s+[A-Za-z0-9._~-]{12,}", combined)),
        len(
            re.findall(
                r"\bssh-(?:rsa|ed25519)\s+[A-Za-z0-9+/]{40,}={0,3}",
                combined,
            )
        ),
        len(re.findall(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", combined)),
    )
    assert sensitive_shape_counts == (0, 0, 0, 0)


def test_sirp12_observe_packet_is_consumed_exactly_once_and_projection_is_bounded() -> None:
    sirp = _load(SIRP_PATH)
    open_work = _load(OPEN_WORK_PATH)
    expected_gate_ids = {
        "OPS-ALERT-DELIVERY-GO",
        "observability-live-smoke-go",
        "debian-observability-live-go",
        "log-retention-policy-go",
        "deploy-live-go",
    }
    gates = {
        item["id"]: item for item in sirp["gate_queue"] if item["id"] in expected_gate_ids
    }
    assert set(gates) == expected_gate_ids
    packet_gate_ids = {
        "observability-live-smoke-go",
        "debian-observability-live-go",
        "log-retention-policy-go",
    }
    packet_gates = {gate_id: gates[gate_id] for gate_id in packet_gate_ids}
    assert {gate["authorization_status"] for gate in packet_gates.values()} == {"used"}
    assert {gate["status"] for gate in packet_gates.values()} == {"used"}
    assert {gate["consumption_status"] for gate in packet_gates.values()} == {
        "consumed"
    }
    assert {gate["scope_status"] for gate in packet_gates.values()} == {
        "packet_scoped_used_consumed"
    }
    assert all(gate["reuse_permitted"] is False for gate in packet_gates.values())
    assert all(gate["missing_fields"] == [] for gate in packet_gates.values())
    assert gates["OPS-ALERT-DELIVERY-GO"]["status"] == "open"
    assert gates["OPS-ALERT-DELIVERY-GO"]["authorization_status"] == "not_go"
    assert gates["OPS-ALERT-DELIVERY-GO"]["delivery_started"] is False
    assert gates["OPS-ALERT-DELIVERY-GO"]["delivery_performed"] is False
    assert gates["OPS-ALERT-DELIVERY-GO"]["new_live_go_created"] is False
    assert gates["deploy-live-go"]["status"] == "independent"
    assert gates["deploy-live-go"]["authorization_status"] == "not_go"
    assert gates["deploy-live-go"]["sirp12_execution_authority"] is False

    record = sirp["latest_sirp12_gate_request_decision_record"]
    assert record["requested_gate_ids"] == [
        "OPS-ALERT-DELIVERY-GO",
        "observability-live-smoke-go",
        "debian-observability-live-go",
        "log-retention-policy-go",
        "deploy-live-go",
    ]
    assert record["authorization_status"] == "not_go"
    assert record["sirp12_claimable"] is False
    assert record["sirp12_started"] is False
    assert record["live_actions"] is False
    assert set(record["missing_fields"]) == {"observe", "retention", "delivery", "deploy"}

    proposal = record["proposed_next_single_packet"]
    assert proposal["status"] == "draft_not_go"
    assert proposal["packet_kind"] == "observe_only_debian_fixed_redacted_readiness"
    assert proposal["proposed_timeout_seconds"] == 30
    assert proposal["maximum_result_count"] == 1
    assert proposal["command_boundary"] == (
        "ssh -F ops/homeserver/ssh_config odysseus-homeserver-probe"
    )
    assert "delivery" in proposal["explicit_exclusions"]
    assert "deployment" in proposal["explicit_exclusions"]

    queue_item = next(
        item
        for item in open_work["abc_execution_queue"]
        if item["id"] == "SIRP-12-live-observe-and-delivery"
    )
    assert queue_item["authorization_status"] == (
        "observe_used_consumed_delivery_not_go_deploy_not_go"
    )
    assert queue_item["claimable"] is False
    assert queue_item["sirp12_started"] is True
    assert queue_item["observe_external_action_executed"] is True
    assert queue_item["observe_mutation_performed"] is False

    live_go = sirp["live_go"]
    live_go_by_id = {item["id"]: item for item in live_go}
    assert set(live_go_by_id) == {
        "SIRP12-OBSERVE-PACKET-20260728",
        "SEC143-COMPOSE-OBSERVE-20260729",
    }
    packet = live_go_by_id["SIRP12-OBSERVE-PACKET-20260728"]
    assert packet["id"] == "SIRP12-OBSERVE-PACKET-20260728"
    assert packet["action"] == "other/read_only_observation"
    assert packet["artifact_or_inputs"] == (
        "ssh -F ops/homeserver/ssh_config odysseus-homeserver-probe"
    )
    assert packet["limits"] == {
        "maximum_invocations": 1,
        "maximum_results": 1,
        "outer_timeout_seconds": 30,
        "follow_on_queries": 0,
    }
    assert packet["status"] == "used"
    assert packet["consumption_status"] == "consumed"
    assert packet["invocation_counter"] == 1
    assert packet["result_counter"] == 1
    assert packet["retry_counter"] == 0
    assert packet["follow_on_query_counter"] == 0
    assert packet["external_action_executed"] is True
    assert packet["mutation_performed"] is False
    assert packet["reuse_permitted"] is False
    assert packet["delivery_authority"] is False
    assert packet["deploy_authority"] is False

    sec143 = live_go_by_id["SEC143-COMPOSE-OBSERVE-20260729"]
    assert sec143["status"] == "used"
    assert sec143["consumption_status"] == "consumed_terminal_blocked"
    assert sec143["invocation_counter"] == 1
    assert sec143["result_counter"] == 1
    assert sec143["limits"]["maximum_invocations"] == 1
    assert sec143["limits"]["maximum_results"] == 1
    assert sec143["limits"]["retries"] == 0
    assert sec143["reuse_permitted"] is False
    assert sec143["terminal_result"]["status"] == "blocked"
    assert sec143["terminal_result"]["error_code"] == "transport_failed"
    assert sec143["terminal_result"]["retry_permitted"] is False
    assert sec143["deploy_authority"] is False
    assert sec143["delivery_or_send_authority"] is False

    evidence_text = LIVE_EVIDENCE_PATH.read_text(encoding="utf-8")
    projection = json.loads(
        evidence_text.split("```json\n", 1)[1].split("\n```", 1)[0]
    )
    assert projection == {
        "schema_id": "odysseus.homeserver.redacted_runtime_probe.v1",
        "status": "ok",
        "container_running": True,
        "environment_entry_count": 102,
        "credential_presence": {
            "DATA_BRAVE_API_KEY": False,
            "EMBEDDING_API_KEY": False,
            "GH_TOKEN": True,
            "GITHUB_TOKEN": True,
            "GOOGLE_API_KEY": False,
            "HF_TOKEN": False,
            "HUGGING_FACE_HUB_TOKEN": False,
            "NEXTCLOUD_WEBDAV_APP_PASSWORD": True,
            "ODYSSEUS_ADMIN_PASSWORD": True,
            "ODYSSEUS_INTERNAL_TOKEN": True,
            "OPENAI_API_KEY": False,
            "SERPER_API_KEY": False,
            "TAVILY_API_KEY": False,
            "TELEGRAM_BOT_TOKEN": True,
        },
        "unknown_sensitive_key_count": 1,
        "raw_environment_visible": False,
        "secret_values_visible": False,
    }
    receipt = json.loads(
        evidence_text.split("```json\n", 2)[2].split("\n```", 1)[0]
    )
    assert receipt == {
        "status": "succeeded",
        "schema_validation": "pass",
        "wrapper_exit_status": 0,
        "invocation_count": 1,
        "result_count": 1,
        "retry_count": 0,
        "follow_on_query_count": 0,
        "raw_output_retained": False,
        "external_action_executed": True,
        "mutation_performed": False,
    }

    decision_record = sirp["latest_sirp12_delivery_readiness_decision_record"]
    assert decision_record["status"] == (
        "blocked_missing_allowlisted_redacted_delivery_readiness_contract"
    )
    assert decision_record["authorization_status"] == "not_go"
    assert decision_record["delivery_started"] is False
    assert decision_record["delivery_performed"] is False
    assert decision_record["live_actions"] is False
    assert decision_record["new_live_go_created"] is False
    assert "not action-specific send authority" in (
        decision_record["user_steering_interpretation"]
    )
    findings = "\n".join(decision_record["contract_findings"])
    assert "dry-run-only" in findings
    assert "TELEGRAM_AGENT_REPLY_ENABLED" in findings
    assert "opaque configured target" in findings
    assert "single-use grant" in findings
    assert "independent durable correlation readback" in findings
    frontier = decision_record["smallest_safe_frontier"]
    assert frontier["kind"] == (
        "repo_only_delivery_readiness_and_bound_adapter_contract"
    )
    assert frontier["required_projection"]["fields"] == [
        "telegram_token_present: boolean",
        "opaque_target_configured: boolean",
        "agent_reply_enabled: boolean",
        "send_ready: boolean derived only from the fixed prerequisites",
        "raw_target_visible: false",
        "secret_values_visible: false",
    ]
    assert "one single-use delivery grant and explicit expiry" in (
        frontier["required_action_binding"]
    )


def test_d0_publish_packet_is_exact_not_go_and_path_scoped() -> None:
    plan = _load(SIRP_PATH)
    packet = PUBLISH_PACKET_PATH.read_text(encoding="utf-8")
    record = plan["latest_ops_alert_d0_publish_packet"]

    assert record["status"] == "not_go_blocked_mixed_tracked_hunks"
    assert record["authorization_status"] == "not_go"
    assert record["git_facts"]["staged_path_count"] == 0
    assert record["git_facts"]["push_destination"] == "fuzzy/dev"
    assert record["backup_readiness_precedes_publish"] is False
    assert record["d0_live_go_available"] is False
    assert "GO ABC-SEC125 D0PUBLISH STAGE COMMIT PUSH FUZZY DEV ONCE EXPIRES RUN_END" in packet
    assert (
        "Status: `NOT_AUTHORIZED_FUTURE_GO_REQUESTABLE_AFTER_MANIFEST`"
        in packet
    )
    assert "`origin` is forbidden" in packet
    assert "All transcription, STT, LLM" in packet
    stop = plan["latest_ops_alert_d0_publish_reconstruction_stop"]
    assert stop["status"] == "stopped_end_of_day_no_reconstruction_artifact"
    assert stop["temporary_alternate_index"]["candidate_paths_added"] == 0
    assert stop["temporary_alternate_index"]["patch_created"] is False
    assert stop["temporary_alternate_index"]["manifest_created"] is False
    assert stop["real_index"]["mutated"] is False
    assert stop["authorization_status"] == "not_go"
    assert stop["active_claims_after_stop"] == 0
    blocker = plan["latest_ops_alert_d0_publish_reconstruction_blocker"]
    assert blocker["status"] == "blocked_exact_atomic_mixed_shared_authority_hunks"
    assert blocker["attribution"]["tracked_path_count"] == 29
    assert blocker["attribution"]["code_test_paths_fully_attributable"] == 23
    assert blocker["attribution"]["documentation_paths_reviewed"] == 6
    assert blocker["attribution"]["untracked_whole_file_candidates_present"] == 44
    assert blocker["attribution"]["accepted_complete_candidate"] is False
    assert blocker["temporary_alternate_index"]["candidate_paths_added"] == 0
    assert blocker["temporary_alternate_index"]["patch_created"] is False
    assert blocker["temporary_alternate_index"]["manifest_created"] is False
    assert blocker["authority_reconciliation"]["real_index_mutated"] is False
    assert blocker["authorization_status"] == "not_go"
    assert blocker["active_claims_after_handoff"] == 0
    assert {
        item["path"] for item in blocker["exact_blockers"]
    } == {
        "docs/plans/multi-agent-execution-guidance.json",
        "docs/plans/open-work-completion-master-roadmap.json",
    }
    assert "Reconstruction R2 exact blocker" in packet
    semantic = plan["latest_ops_alert_d0_publish_semantic_json_split"]
    assert semantic["status"] == (
        "semantic_candidate_ready_future_go_requestable_not_authorized"
    )
    assert semantic["semantic_split"]["allowlist_record_count"] == 45
    assert semantic["semantic_split"]["duplicate_or_ambiguous_selectors"] == 0
    assert semantic["semantic_split"]["mixed_worktree_files_edited"] is False
    assert semantic["candidate"]["payload_path_count"] == 73
    assert semantic["candidate"]["tracked_path_count"] == 29
    assert semantic["candidate"]["untracked_path_count"] == 44
    assert semantic["candidate"]["semantic_json_path_count"] == 2
    assert semantic["candidate"]["code_test_tracked_path_count"] == 23
    assert semantic["candidate"]["runbook_tracked_path_count"] == 4
    assert semantic["candidate"]["patch_covered_closure_path_count"] == 75
    assert semantic["candidate"]["durable_closure_path_count"] == 76
    assert semantic["authority"]["real_index_mutated"] is False
    assert semantic["authorization_status"] == "not_authorized"
    assert semantic["future_go_phrase_status"] == (
        "stale_and_ineffective_after_SEC128_candidate_change"
    )
    assert semantic["active_claims_after_handoff"] == 0
    sec128 = plan["latest_backup_snapshot_readonly_observation_readiness"]
    assert sec128["status"] == (
        "accepted_repo_only_waiting_expanded_path_scoped_publish_and_exact_observation_go"
    )
    assert sec128["wrapper_contract"] == {
        "host_local_only": True,
        "maximum_invocations_per_future_grant": 1,
        "outer_timeout_seconds": 30,
        "inner_timeout_seconds": 20,
        "retry_count": 0,
        "fallback_commands": 0,
        "restic_mode": "snapshots_json_no_lock_read_only",
        "raw_output_retained": False,
        "source_redacted": True,
    }
    assert sec128["expanded_candidate"]["payload_path_count"] == 76
    assert sec128["expanded_candidate"]["tracked_path_count"] == 29
    assert sec128["expanded_candidate"]["untracked_path_count"] == 47
    assert sec128["expanded_candidate"]["patch_covered_closure_path_count"] == 78
    assert sec128["expanded_candidate"]["durable_candidate_path_count"] == 79
    assert sec128["prior_publish_phrase_status"] == (
        "stale_and_ineffective_after_SEC128_candidate_change"
    )
    assert sec128["authorization_status"] == "not_authorized"
    assert sec128["future_publish_phrase_status"] == (
        "stale_and_ineffective_after_SEC129_candidate_change"
    )
    assert sec128["external_actions"] is False
    assert sec128["git_actions"] is False
    assert sec128["active_claims_after_handoff"] == 0
    assert "SEC128 expanded semantic closure" in packet
    assert (
        "GO ABC-SEC128 D0PUBLISH EXPANDED STAGE COMMIT PUSH FUZZY DEV ONCE EXPIRES RUN_END"
        in packet
    )
    sec129 = plan["latest_predeploy_backup_creation_contract"]
    assert sec129["status"] == (
        "accepted_repo_only_waiting_expanded_path_scoped_publish_preflight_evidence_and_exact_backup_go"
    )
    assert sec129["wrapper_contract"] == {
        "host_local_only": True,
        "maximum_backup_invocations_per_future_grant": 1,
        "outer_timeout_seconds": 1860,
        "inner_backup_timeout_seconds": 1800,
        "readback_timeout_seconds": 20,
        "retry_count": 0,
        "concurrent_lock_required": True,
        "fixed_backup_script": "backup_homeserver_pre_update_v1",
        "fixed_readback": "restic_latest_pre_update_json_no_lock_v1",
        "raw_output_retained": False,
        "source_redacted": True,
        "unknown_after_invocation_is_terminal": True,
    }
    assert sec129["expanded_candidate"]["payload_path_count"] == 79
    assert sec129["expanded_candidate"]["tracked_path_count"] == 29
    assert sec129["expanded_candidate"]["untracked_path_count"] == 50
    assert sec129["expanded_candidate"]["patch_covered_closure_path_count"] == 81
    assert sec129["expanded_candidate"]["durable_candidate_path_count"] == 82
    assert sec129["prior_publish_phrase_status"] == (
        "stale_and_ineffective_after_SEC129_candidate_change"
    )
    assert sec129["authorization_status"] == "not_authorized"
    assert sec129["future_publish_phrase_status"] == (
        "stale_and_ineffective_after_SEC130_candidate_change"
    )
    assert sec129["future_backup_go_phrase_status"] == (
        "requestable_only_after_publication_and_valid_D0_SEC128_preflight_evidence_not_granted"
    )
    assert sec129["external_actions"] is False
    assert sec129["git_actions"] is False
    assert sec129["active_claims_after_handoff"] == 0
    assert "SEC129 expanded semantic closure" in packet
    assert (
        "GO ABC-SEC129 D0PUBLISH EXPANDED STAGE COMMIT PUSH FUZZY DEV ONCE EXPIRES RUN_END"
        in packet
    )
    assert (
        "GO ABC-SEC129 PREDEPLOY BACKUP CREATE ONCE <=1860S EXPIRES RUN_END"
        in packet
    )
    sec130 = plan["latest_d0_backup_evidence_composition"]
    assert sec130["status"] == (
        "accepted_repo_only_waiting_unchanged_path_count_expanded_publish_and_separate_exact_d0_go"
    )
    assert sec130["wrapper_contract"] == {
        "host_local_only": True,
        "outer_timeout_seconds": 30,
        "base_command_count": 9,
        "base_command_timeout_seconds": 1,
        "backup_observer_timeout_seconds": 20,
        "maximum_total_component_budget_seconds": 29,
        "backup_observer_invocations": 1,
        "maximum_restic_invocations": 1,
        "retry_count": 0,
        "in_process_composition": True,
        "independent_source_schema_and_digest_validation": True,
        "raw_output_retained": False,
        "source_redacted": True,
        "invalid_or_unknown_evidence_is_terminal": True,
    }
    assert sec130["expanded_candidate"] == {
        "payload_path_count": 79,
        "tracked_path_count": 29,
        "untracked_path_count": 50,
        "sec130_added_path_count": 0,
        "sec130_updated_already_included_path_count": 3,
        "patch_covered_closure_path_count": 81,
        "durable_candidate_path_count": 82,
        "binding_manifest": "docs/plans/security-incident-response-publish-semantic-manifest.json",
        "deterministic_patch": "docs/plans/security-incident-response-publish-semantic.patch",
        "self_reference_policy": (
            "SEC130 changes three already-included D0 paths, so the path counts remain "
            "79 payload, 81 patch-covered closure and 82 durable. The transport patch "
            "excludes only itself."
        ),
    }
    assert sec130["prior_publish_phrase_status"] == (
        "stale_and_ineffective_after_SEC130_candidate_change"
    )
    assert sec130["authorization_status"] == "not_authorized"
    assert sec130["future_publish_phrase_status"] == (
        "stale_and_ineffective_after_SEC131_candidate_change"
    )
    assert sec130["external_actions"] is False
    assert sec130["git_actions"] is False
    assert sec130["active_claims_after_handoff"] == 0
    assert "SEC130 unchanged-count expanded semantic closure" in packet
    assert (
        "GO ABC-SEC130 D0PUBLISH EXPANDED STAGE COMMIT PUSH FUZZY DEV ONCE EXPIRES RUN_END"
        in packet
    )
    sec131 = plan["latest_transactional_deploy_rollback_strategy"]
    assert sec131["status"] == "needs_live_observation_repo_only_no_deploy_executor"
    assert sec131["capability_observer_contract"] == {
        "host_local_only": True,
        "outer_timeout_seconds": 15,
        "inner_total_component_budget_seconds": 5,
        "fixed_command_count": 5,
        "per_command_timeout_seconds": 1,
        "maximum_invocations_per_future_grant": 1,
        "retry_count": 0,
        "expected_podman_compose_version": "1.3.0",
        "parser_help_is_not_semantic_proof": True,
        "installed_source_audit_is_ast_scoped": True,
        "raw_output_retained": False,
        "source_redacted": True,
    }
    assert sec131["expanded_candidate"]["payload_path_count"] == 82
    assert sec131["expanded_candidate"]["tracked_path_count"] == 29
    assert sec131["expanded_candidate"]["untracked_path_count"] == 53
    assert sec131["expanded_candidate"]["patch_covered_closure_path_count"] == 84
    assert sec131["expanded_candidate"]["durable_candidate_path_count"] == 85
    assert sec131["transport_artifact_repair"] == {
        "initial_candidate_rejected": True,
        "reason": (
            "The first SEC131 manifest advertised 82/84/85 while the versioned "
            "transport patch still represented SEC130 and omitted the three new "
            "SEC131 paths."
        ),
        "required_source": (
            "exact HEAD 4240aeb9ef34351a02a45736e4eb4b43f1e85177 plus "
            "the reviewed 84-path closure"
        ),
        "required_replay": (
            "fresh alternate index seeded from exact HEAD, git apply --cached, "
            "exact 84-path diff inventory and identical closure tree"
        ),
        "manifest_counts_alone_sufficient": False,
        "real_index_mutation": False,
    }
    assert sec131["prior_publish_phrase_status"] == (
        "stale_and_ineffective_after_SEC131_candidate_change"
    )
    assert sec131["authorization_status"] == "not_authorized"
    assert sec131["future_publish_phrase_status"] == (
        "requestable_only_after_final_SEC131_manifest_readback_not_granted"
    )
    assert sec131["deploy_phrase_status"] == (
        "not_requestable_missing_capability_evidence_and_owner_bound_packet"
    )
    assert sec131["external_actions"] is False
    assert sec131["git_actions"] is False
    assert sec131["active_claims_after_handoff"] == 0
    assert "SEC131 needs-live-observation expanded semantic closure" in packet
    assert "Manifest counts alone are never sufficient evidence." in packet
    assert (
        "GO ABC-SEC131 D0PUBLISH EXPANDED STAGE COMMIT PUSH FUZZY DEV ONCE EXPIRES RUN_END"
        in packet
    )
    assert (
        "GO ABC-SEC131 PODMAN COMPOSE CAPABILITY READ-ONLY OBSERVATION ONCE <=15S EXPIRES RUN_END"
        in packet
    )
    deploy_packet = (
        ROOT / "docs/plans/security-incident-response-transactional-deploy-packet.md"
    ).read_text(encoding="utf-8")
    for required in (
        "Status: `needs_live_observation`",
        "defines no deploy executor",
        "reason_code",
        "deployment_capability_supported",
        "exactly one bounded rollback",
        "git merge --ff-only <exact-new>",
        "dependency-service pull or recreation",
        "retry_permitted=false",
    ):
        assert required in deploy_packet

    transport_patch = (
        ROOT / "docs/plans/security-incident-response-publish-semantic.patch"
    ).read_text(encoding="utf-8")
    for required_path in (
        "docs/plans/security-incident-response-transactional-deploy-packet.md",
        "ops/homeserver/redacted_podman_compose_capability_observation.py",
        "tests/test_homeserver_redacted_podman_compose_capability_observation.py",
    ):
        assert f"diff --git a/{required_path} b/{required_path}" in transport_patch


def test_sirp_handoff_evidence_manifest_matches_current_claimed_files() -> None:
    evidence = _load(EVIDENCE_PATH)

    assert evidence["kind"] == (
        "odysseus.security_incident_response_production_completion_planning_evidence"
    )
    assert evidence["run_id"] == "ABC-SEC123-20260728-OPS-ALERT-CLOSURE"
    assert evidence["slices"] == [
        "OPS-ALERT-B-production-transport-body-target-resolution",
        "OPS-ALERT-C-app-route-fake-composition-packaging",
        "OPS-ALERT-D-predeploy-parity-and-exact-deployment-packet",
        "OPS-ALERT-D0-predeploy-readonly-observation-packet",
        "OPS-ALERT-D0A-fixed-predeploy-observation-wrapper",
        "OPS-ALERT-D0PUBLISH-PACKET",
        "OPS-ALERT-D0PUBLISH-RECONSTRUCTION-STOP",
        "OPS-ALERT-D0PUBLISH-RECONSTRUCTION-R2-BLOCKER",
        "OPS-ALERT-D0PUBLISH-SEMANTIC-JSON-SPLIT",
        "SEC128-BACKUP-SNAPSHOT-READONLY-OBSERVATION-READINESS",
        "SEC129-PREDEPLOY-BACKUP-CREATION-CONTRACT",
        "SEC130-D0-BACKUP-EVIDENCE-COMPOSITION",
        "SEC131-TRANSACTIONAL-DEPLOY-ROLLBACK-STRATEGY",
    ]
    assert evidence["status"] == (
        "SEC131_needs_live_observation_transport_repaired_expanded_publish_candidate_ready_future_go_requestable_not_authorized"
    )
    assert EVIDENCE_PATH.relative_to(ROOT).as_posix() not in evidence["file_sha256"]

    actual_hashes: dict[str, str] = {}
    for relative_path, expected_hash in evidence["file_sha256"].items():
        _assert_repo_relative(relative_path)
        actual_hash = hashlib.sha256((ROOT / relative_path).read_bytes()).hexdigest()
        assert actual_hash == expected_hash
        actual_hashes[relative_path] = actual_hash

    aggregate_payload = "".join(
        f"{path}\0{actual_hashes[path]}\n" for path in sorted(actual_hashes)
    ).encode("utf-8")
    assert hashlib.sha256(aggregate_payload).hexdigest() == evidence["aggregate_sha256"]
