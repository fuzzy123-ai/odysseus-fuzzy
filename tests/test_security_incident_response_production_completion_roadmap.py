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
    active_claims = [claim for claim in plan["claims"] if claim["state"] == "claimed"]
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

    assert sirp["status"] == (
        "ops_alert_c2_accepted_repo_only_d_transactional_executor_next"
    )

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
            "OPS-ALERT-C2 accepted repo-only",
            "SEC190 Compose capability observation terminal ok",
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
                "ops_alert_c2_accepted_waiting_exact_publication_and_required_ci"
            ),
            "dependency_order": [
            "publish the accepted OPS-ALERT-C2 paths and require terminal green Required CI",
            (
                "implement and deep-review the dedicated transactional app-only D "
                "executor, strict transport and independent readback"
            ),
            (
                "bind a complete one-use D deployment packet to exact revisions, C2 "
                "configuration schema, rollback and readback"
            ),
            "bind and execute D deployment with rollback and independent readback",
            "obtain fresh strict redacted runtime readiness",
            "bind one exact E delivery packet",
            (
                "send exactly once and independently read back durable receipt plus "
                "human confirmation"
            ),
        ],
    }

    sec152 = sirp["latest_sec152_transport_v2_observation_packet"]
    assert sec152["packet_path"] == (
        "docs/plans/security-incident-response-sec152-compose-v2-observation-packet.md"
    )
    assert (ROOT / sec152["packet_path"]).is_file()
    assert sec152["published_binding"] == {
        "remote": "fuzzy",
        "branch": "dev",
        "revision": "67f0737de5bccdb5b8841e4ad9deee3df0107b74",
        "tree": "05e35c526939ff277ad8d74e276b97f2c782ad98",
        "observer_sha256": (
            "af8e688ae86e1406a55f51d521f746d15b9300d399caeaa4698e53bd133bd46c"
        ),
        "transport_sha256": (
            "fdbbb0a5103eca34d0a1b96e55f34d45f34ef7e83493fa1f7cafe3c772de44a3"
        ),
        "remote_readback": "passed_during_SEC150_publication",
    }
    assert sec152["transport_schema"] == (
        "odysseus.redacted_podman_compose_capability_transport.v2"
    )
    assert sec152["exact_command"] == (
        r"C:\Users\nkatz\odysseus\venv\Scripts\python.exe "
        r"ops\homeserver\redacted_podman_compose_capability_transport.py"
    )
    assert sec152["arguments"] == 0
    assert sec152["limits"] == {
        "maximum_invocations": 1,
        "maximum_results": 1,
        "outer_timeout_seconds": 30,
        "follow_on_queries_or_commands": 0,
        "retries": 0,
        "expires_at": "run_end",
    }
    assert sec152["accepted_result_families"] == [
        (
            "strict transport-generated v2 six-key blocked envelope with exact "
            "allowed pair and canonical digest"
        ),
        (
            "strict preserved observer v1 ok, needs_live_observation, generic "
            "blocked or diagnostic blocked envelope returned unchanged"
        ),
    ]
    for authority_key in (
        "live_observation_authority",
        "public_ip_query_authority",
        "deploy_authority",
        "delivery_or_send_authority",
        "external_actions_started",
        "git_actions_started",
    ):
        assert sec152[authority_key] is False
    assert sec152["deep_review"]["result"] == "accepted"

    sec154 = sirp["latest_sec154_compose_capability_remediation_strategy"]
    assert sec154["strategy_path"] == (
        "docs/plans/security-incident-response-sec154-compose-capability-remediation-strategy.md"
    )
    assert (ROOT / sec154["strategy_path"]).is_file()
    assert sec154["terminal_evidence_binding"] == {
        "grant_id": "SEC153-TRANSPORT-V2-OBSERVE-20260729",
        "status": "needs_live_observation",
        "reason_code": "semantic_proof_insufficient",
        "missing_proofs": ["source_up_no_deps_guard_missing"],
        "retry_permitted": False,
        "evidence_sha256": (
            "b34ebfd08cd39d721b105aa85b7a4442d98c1b48a07f8b95fc9c2a72390d5968"
        ),
    }
    assert sec154["diagnosis"] == (
        "real Debian podman-compose 1.3.0 compose_up capability gap, not an "
        "observer false negative"
    )
    assert sec154["selected_strategy"] == (
        "preserve fail-closed recognition and prepare a separately gated replacement "
        "or upgrade to an officially supported Compose implementation with exact "
        "provider-chain provenance and offline compose_up no-deps semantic proof"
    )
    assert sec154["selected_gates"] == [
        "A_repo_only_candidate_selection_contract",
        "B_offline_implementation_and_fixture_acceptance",
        "C_reviewed_publication",
        "D_separately_authorized_package_or_host_change_with_rollback_and_redacted_access_readback",
        "E_new_one_use_capability_observation",
    ]
    assert sec154["next_candidate_contract_path"] == (
        "docs/plans/security-incident-response-sec155-compose-candidate-selection-contract.md"
    )
    assert sec154["rejected_shortcuts"] == [
        "weaken observer or AST recognizer",
        "edit or replace installed system files in place",
        "automatically install or upgrade a package",
        "broaden transactional deployment to whole-stack Compose up",
    ]
    assert sec154["fallback"] == (
        "Direct Podman executor requires a separate owner decision and roadmap slice"
    )
    for authority_key in (
        "implementation_authority",
        "candidate_research_or_network_authority",
        "package_or_host_change_authority",
        "live_observation_authority",
        "public_ip_query_authority",
        "deploy_authority",
        "delivery_or_send_authority",
        "external_actions_started",
        "git_actions_started",
    ):
        assert sec154[authority_key] is False
    assert sec154["deep_review"]["result"] == "accepted"
    for correction in (
        "real_gap_not_false_negative",
        "debian_1_3_0_adverse_fixture_preserved",
        "candidate_version_not_invented",
        "provider_chain_binding_required",
        "gate_A_contract_only_scope",
        "sequential_package_rollback_readback_gates",
        "package_change_has_no_compose_container_service_or_deploy_authority",
        "C2_before_D_preserved",
        "ascii_and_diff_integrity",
    ):
        assert sec154["deep_review"][correction] == "passed"

    sec155 = sirp["latest_sec155_compose_candidate_selection_contract"]
    assert sec155["contract_path"] == (
        "docs/plans/security-incident-response-sec155-compose-candidate-selection-contract.md"
    )
    assert (ROOT / sec155["contract_path"]).is_file()
    assert sec155["candidate_status"] == "unselected"
    for candidate_key in (
        "candidate_identity",
        "candidate_version",
        "candidate_distribution_channel",
        "provider_chain",
    ):
        assert sec155[candidate_key] is None
    assert sec155["allowed_candidate_result_statuses"] == [
        "unselected",
        "eligible",
        "rejected",
        "blocked",
    ]
    assert sec155["required_future_evidence"] == [
        "exact implementation identity and supported distribution channel",
        "complete entrypoint and delegated provider chain",
        "exact package or artifact identity, version and architecture when applicable",
        "immutable digest, checksum or signed identity with approved verification mechanism",
        "bounded installed-identity predicates through a redacted fixed-key readback",
        "offline synthetic source fixture bound to the selected provider behavior",
        "deterministic AST proof for service-only no-deps and dependency-expanding opposite branch",
        "independent service-selection, no-build and force-recreate proof",
        "Debian podman-compose 1.3.0 adverse fixture preserving needs_live_observation and source_up_no_deps_guard_missing",
    ]
    assert sec155["eligibility_rule"] == (
        "eligible is prohibited until one future separately authorized read-only "
        "official-provenance exercise produces the complete fixed-key evidence envelope "
        "and a durable owner decision selects exactly one candidate"
    )
    assert sec155["gate_b_paths_after_owner_selection_only"] == [
        "ops/homeserver/redacted_podman_compose_capability_observation.py",
        "ops/homeserver/redacted_podman_compose_capability_transport.py",
        "tests/test_homeserver_redacted_podman_compose_capability_observation.py",
        "tests/test_homeserver_redacted_podman_compose_capability_transport.py",
    ]
    for authority_key in (
        "implementation_authority",
        "candidate_research_or_network_authority",
        "package_or_host_change_authority",
        "live_observation_authority",
        "public_ip_query_authority",
        "deploy_authority",
        "delivery_or_send_authority",
        "git_actions_started",
        "external_actions_started",
    ):
        assert sec155[authority_key] is False
    assert sec155["deep_review"]["result"] == "accepted"

    frontier = sirp["next_frontier"]
    assert frontier["claim_status_now"] == (
        "ops_alert_c2_accepted_waiting_exact_publication_and_required_ci"
    )
    assert frontier["dependency_order"] == [
        "publish the accepted OPS-ALERT-C2 paths and require terminal green Required CI",
        (
            "implement and deep-review the dedicated transactional app-only D "
            "executor, strict transport and independent readback"
        ),
        (
            "bind a complete one-use D deployment packet to exact revisions, C2 "
            "configuration schema, rollback and readback"
        ),
        "bind and execute D deployment with rollback and independent readback",
        "obtain fresh strict redacted runtime readiness",
        "bind one exact E delivery packet",
        (
            "send exactly once and independently read back durable receipt plus "
            "human confirmation"
        ),
    ]
    assert sec155["next_action"] == (
        "Obtain separate owner authority for one bounded read-only official-provenance "
        "exercise. No candidate, version, channel or provider is selected."
    )

    sec156 = sirp["latest_sec156_compose_candidate_provenance_readonly_packet"]
    assert sec156["packet_path"] == (
        "docs/plans/security-incident-response-sec156-compose-candidate-"
        "provenance-readonly-packet.md"
    )
    assert (ROOT / sec156["packet_path"]).is_file()
    assert sec156["template_bindable_now"] is False
    assert sec156["candidate_status"] == "unselected"
    assert sec156["evaluation_subject"] is None
    assert sec156["approved_origins"] == []
    assert sec156["instantiation_status"] == "waiting_on_user"
    assert sec156["future_go_binding_rule"] == (
        "plain go binds only when immediately responding to a fully instantiated "
        "accepted packet with one exact non-null evaluation subject and a non-empty "
        "exact approved-origin allowlist"
    )
    assert sec156["future_limits"] == {
        "candidate_subjects": 1,
        "substitutions": 0,
        "maximum_requests": 12,
        "maximum_opened_or_searched_pages": 8,
        "maximum_inspected_bodies": 4,
        "maximum_body_bytes": 524288,
        "maximum_aggregate_body_bytes": 2097152,
        "maximum_approved_origins": 3,
        "maximum_wall_clock_seconds": 600,
        "execution_attempts": 1,
        "retries": 0,
    }
    assert sec156["permitted_source_classes"] == [
        "official project or vendor documentation",
        "official signed release metadata or official public immutable source",
        "official operating-system distribution metadata",
        "official language package index project and release metadata",
    ]
    assert sec156["permitted_operations_after_future_go_only"] == [
        "unauthenticated read-only HTTPS GET within approved origins",
        "unauthenticated read-only HTTPS HEAD within approved origins",
        "domain-restricted search within approved origins",
        "open within approved origins",
    ]
    for authority_key in (
        "open_web_candidate_discovery_authority",
        "network_authority",
        "package_or_host_change_authority",
        "runtime_or_provider_authority",
        "live_observation_authority",
        "public_ip_query_authority",
        "deploy_authority",
        "delivery_or_send_authority",
        "git_actions_started",
        "external_actions_started",
    ):
        assert sec156[authority_key] is False
    assert sec156["deep_review"]["first_handoff_unfilled_go_binding_rejected"] is True
    assert (
        sec156["deep_review"]["language_package_index_metadata_only_source_class"]
        == "passed"
    )
    assert sec156["deep_review"]["result"] == "accepted"
    assert sec156["next_action"] == (
        "Obtain the owner's nomination of one exact evidence-gathering subject, then "
        "prepare the exact approved-origin instantiation repo-only. No network request "
        "is authorized."
    )

    sec157 = sirp["latest_sec157_compose_candidate_provenance_readonly_instantiation"]
    assert sec157["status"] == "repo_only_instantiation_and_test_binding_deep_review_passed"
    assert sec157["instantiation_path"] == (
        "docs/plans/security-incident-response-sec157-compose-candidate-"
        "provenance-readonly-instantiation.md"
    )
    assert (ROOT / sec157["instantiation_path"]).is_file()
    assert sec157["base_packet_path"] == sec156["packet_path"]
    assert (ROOT / sec157["base_packet_path"]).is_file()
    assert sec157["candidate_status"] == "unselected"
    assert sec157["evaluation_subject"] == "containers/podman-compose upstream project"
    assert sec157["nomination_is_adoption_selection"] is False
    assert sec157["approved_origins"] == [
        "https://github.com",
        "https://raw.githubusercontent.com",
        "https://pypi.org",
    ]
    assert sec157["approved_path_boundaries"] == [
        (
            "https://github.com exact /containers/podman-compose or descendants "
            "beginning /containers/podman-compose/"
        ),
        (
            "https://raw.githubusercontent.com prefix "
            "/containers/podman-compose/<40-hex-full-commit-sha>/"
            "<nonempty-source-path>"
        ),
        (
            "https://pypi.org exact /pypi/podman-compose/json or prefix "
            "/project/podman-compose/"
        ),
    ]
    assert sec157["approved_source_classes"] == [
        "official project or vendor documentation",
        "official signed release metadata or official public immutable source",
        "official operating-system distribution metadata",
        "official language package index project and release metadata",
    ]
    assert sec157["future_limits"] == sec156["future_limits"]
    assert sec157["expires_at"] is None
    assert sec157["current_user_go_consumption"] == (
        "consumed_for_repo_only_instantiation_not_reusable_for_provenance_requests"
    )
    assert sec157["future_go_required"] is True
    assert sec157["future_go_binding_rule"] == (
        "one new plain go must immediately respond to the accepted SEC157 "
        "instantiation before a one-use expiring read-only provenance ledger may "
        "be created"
    )
    for authority_key in (
        "network_authority",
        "package_or_host_change_authority",
        "runtime_or_provider_authority",
        "live_observation_authority",
        "public_ip_query_authority",
        "deploy_authority",
        "delivery_or_send_authority",
        "git_actions_started",
        "external_actions_started",
    ):
        assert sec157[authority_key] is False
    assert (
        sec157["deep_review"]["first_handoff_source_class_and_boundary_rejected"]
        is True
    )
    for correction in (
        "pypi_metadata_only_source_class_after_correction",
        "github_exact_repository_boundary_after_correction",
        "raw_github_full_commit_sha_after_correction",
        "candidate_nomination_not_selection",
        "one_subject_zero_substitution",
        "budget_and_expiry_binding",
        "current_go_non_reuse",
        "authority_boundaries",
        "ascii_and_diff_integrity",
    ):
        assert sec157["deep_review"][correction] == "passed"
    assert sec157["deep_review"]["result"] == "accepted"
    assert sec157["next_action"] == (
        "Wait for one new plain go immediately responding to the accepted SEC157 "
        "instantiation. No provenance request is authorized yet."
    )

    sec158 = sirp["latest_sec158_compose_candidate_provenance_transport"]
    assert sec158["status"] == (
        "offline_transport_and_instantiation_correction_deep_review_passed"
    )
    assert sec158["transport_path"] == (
        "ops/homeserver/redacted_compose_candidate_provenance.py"
    )
    assert sec158["evaluation_subject"] == "containers/podman-compose upstream project"
    assert sec158["default_disabled"] is True
    assert sec158["network_executed"] is False
    assert sec158["candidate_status"] == "unselected"
    assert sec158["limits"] == sec156["future_limits"]
    assert sec158["deep_review"]["first_handoff_rejected"] is True
    assert sec158["deep_review"]["result"] == "accepted"

    sec159 = sirp["latest_sec159_compose_candidate_provenance_egress_recovery"]
    assert sec159["status"] == "live_provenance_completed_candidate_selected"
    assert sec159["prior_terminal_status"] == "fetch_error_before_any_body_received"
    assert sec159["candidate_status"] == "selected"
    assert sec159["selected_candidate"] == {
        "implementation_identity": "containers/podman-compose",
        "version": "1.6.0",
        "package_or_artifact_identity": "podman_compose-1.6.0.tar.gz",
        "immutable_identity": (
            "sha256:c83fd9bcbaa635100d581ce52a7a4b712ee0d457481232aff392efe3ebc5a217"
        ),
        "supported_distribution_channel": "pypi.org project metadata",
        "entrypoint_provider_chain": (
            "podman-compose CLI -> podman_compose.py:compose_up -> podman CLI"
        ),
        "provenance_evidence_sha256": (
            "74cea20633c55a55640b7fd4ac42348e7f186c64208645f76c09df7e0e84ddee"
        ),
    }
    assert sec159["owner_selection_decision"]["decision"] == (
        "selected_for_gate_b_offline_fixture_and_later_bounded_host_change"
    )
    for authority_key in (
        "selection_is_install_authority",
        "selection_is_host_change_authority",
        "selection_is_deploy_authority",
    ):
        assert sec159["owner_selection_decision"][authority_key] is False
    assert sec159["network_executed"] is True
    assert sec159["strict_envelope_validation"] == "passed"
    assert sec159["attempts_remaining_after_this_ledger"] == 0
    assert sec159["next_action"] == (
        "Implement and deep-review the Gate-B offline observer and transport fixtures "
        "for the selected immutable candidate. Debian 1.3.0 remains the negative "
        "needs_live_observation fixture."
    )

    sec160 = sirp["latest_sec160_selected_candidate_observer"]
    assert sec160["status"] == "selected_candidate_observer_deep_review_passed"
    assert sec160["selected_version"] == "1.6.0"
    assert sec160["observer_path"] == (
        "ops/homeserver/redacted_podman_compose_capability_observation.py"
    )
    assert sec160["observer_sha256"] == (
        "c4a48afb4d6c92e94f96ce3c13cf200cfadfadaf6b8710e1ce8977791c713f09"
    )
    assert sec160["identity_binding"] == (
        "all Compose commands resolve adjacent to sys.executable and source audit uses "
        "that exact interpreter; paths are never serialized"
    )
    assert sec160["debian_1_3_0_adverse_fixture"] == {
        "version_result": "blocked_version_mismatch",
        "semantic_result": "needs_live_observation",
        "missing_proofs": ["source_up_no_deps_guard_missing"],
        "can_produce_ok": False,
    }
    assert sec160["deep_review"]["first_handoff_rejected_for_identity_ambiguity"] is True
    for correction in (
        "selected_version_binding",
        "interpreter_and_compose_binary_identity",
        "fixed_key_redaction",
        "semantic_ast_gates",
        "debian_1_3_0_adverse_fixture",
    ):
        assert sec160["deep_review"][correction] == "passed"
    assert sec160["deep_review"]["focused_tests"] == "19 passed"
    assert sec160["deep_review"]["result"] == "accepted"

    sec161 = sirp["latest_sec161_selected_candidate_transport"]
    assert sec161["status"] == "selected_candidate_transport_deep_review_passed"
    assert sec161["transport_path"] == (
        "ops/homeserver/redacted_podman_compose_capability_transport.py"
    )
    assert sec161["transport_sha256"] == (
        "bd6d20e43b75508a32707f7935360476441446be4e145057731935ce9c97cb54"
    )
    assert sec161["selected_version"] == "1.6.0"
    assert sec161["observer_sha256"] == sec160["observer_sha256"]
    assert sec161["published_ref"] == "refs/remotes/fuzzy/dev"
    assert sec161["remote_observer_interpreter"] == (
        "/home/homebase/.local/share/odysseus-compose-1.6.0/bin/python"
    )
    for correction in (
        "published_blob_digest_enforcement",
        "dedicated_interpreter_binding",
        "selected_version_binding",
        "old_1_3_0_success_rejection",
        "strict_schema_and_return_code_binding",
        "no_retry",
        "historical_schema_compatibility",
    ):
        assert sec161["deep_review"][correction] == "passed"
    assert sec161["deep_review"]["focused_tests"] == "19 passed"
    assert sec161["deep_review"]["result"] == "accepted"

    sec162 = sirp["latest_sec162_selected_candidate_host_change_transport"]
    assert sec162["status"] == "default_disabled_host_change_transport_deep_review_passed"
    assert sec162["transport_path"] == (
        "ops/homeserver/redacted_compose_candidate_host_change.py"
    )
    assert sec162["transport_sha256"] == (
        "36332245325d1b5e852b1336d00f2e0e41a495442e351ee5fd74fefa9834d624"
    )
    assert sec162["test_path"] == "tests/test_redacted_compose_candidate_host_change.py"
    assert sec162["test_sha256"] == (
        "3843f90bac58fe5da89bd85d42c6a2214d00638cb1626ddf3369445f2d946d10"
    )
    assert sec162["selected_package"] == "podman-compose==1.6.0"
    assert sec162["selected_sdist_sha256"] == (
        "c83fd9bcbaa635100d581ce52a7a4b712ee0d457481232aff392efe3ebc5a217"
    )
    assert sec162["target_class"] == "dedicated homebase venv odysseus-compose-1.6.0"
    assert sec162["future_grant_id"] == "SEC162-COMPOSE-CANDIDATE-HOST-CHANGE-GO"
    assert sec162["default_disabled"] is True
    assert sec162["max_grant_seconds"] == 600
    assert sec162["attempts"] == 1
    assert sec162["retries"] == 0
    assert sec162["provider_boundary"] == (
        "isolated pip with explicit https://pypi.org/simple, exact sdist hash, no "
        "deps, no binary, no build isolation, no inherited pip configuration or "
        "environment"
    )
    assert sec162["atomicity_and_rollback"] == (
        "exact real parent, Linux renameat2 RENAME_NOREPLACE, opaque dev/inode "
        "ownership tokens, delete only attempt-owned temp or target, unknown "
        "ownership never deleted"
    )
    for rejected_handoff in (
        "first_handoff_rejected_for_environment_and_no_clobber",
        "second_handoff_rejected_for_publish_identity_unknown",
        "third_handoff_rejected_for_none_token_equality",
    ):
        assert sec162["deep_review"][rejected_handoff] is True
    for correction in (
        "default_disabled",
        "fixed_subprocess_environment",
        "exact_hash_and_provider_boundary",
        "parent_and_no_clobber_boundary",
        "owned_rollback_only",
        "publish_outcome_unknown_fail_closed",
        "cross_field_envelope_validation",
    ):
        assert sec162["deep_review"][correction] == "passed"
    assert sec162["deep_review"]["focused_tests"] == "19 passed"
    assert sec162["deep_review"]["py_compile"] == "passed"
    assert sec162["deep_review"]["result"] == "accepted"
    for authority_key in (
        "external_actions_started",
        "package_or_host_change_authority",
        "live_observation_authority",
        "public_ip_query_authority",
        "deploy_authority",
        "delivery_or_send_authority",
    ):
        assert sec162[authority_key] is False

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
        "SEC146-COMPOSE-OBSERVE-20260729",
            "SEC153-TRANSPORT-V2-OBSERVE-20260729",
            "SEC158-COMPOSE-CANDIDATE-PROVENANCE-READONLY-20260730",
            "SEC159-COMPOSE-CANDIDATE-PROVENANCE-EGRESS-RECOVERY-20260730",
            "SEC163-ISOLATED-GATE-B-PUBLISH-20260730",
            "SEC164-CROSS-PLATFORM-BLOB-REPAIR-PUBLISH-20260730",
            "SEC165-SELECTED-COMPOSE-HOST-CHANGE-20260730",
        "SEC166-POST-HOST-CHANGE-COMPOSE-OBSERVE-20260730",
        "SEC169-HOST-STATE-READBACK-PUBLISH-20260730",
        "SEC170-ONE-USE-HOST-STATE-READBACK-20260730",
        "SEC172-PYTHON-PACKAGING-OBSERVER-PUBLISH-20260730",
        "SEC173-ONE-USE-PYTHON-PACKAGING-OBSERVE-20260730",
        "SEC175-HOST-CHANGE-IDENTITY-REPAIR-PUBLISH-20260730",
        "SEC176-REPAIRED-COMPOSE-HOST-CHANGE-20260730",
        "SEC177-POST-RECOVERY-COMPOSE-OBSERVE-20260730",
        "SEC179-OBSERVER-METADATA-REPAIR-PUBLISH-20260730",
        "SEC180-POST-METADATA-REPAIR-COMPOSE-OBSERVE-20260730",
        "SEC182-SOURCE-AST-REPAIR-PUBLISH-20260730",
        "SEC183-POST-SOURCE-AST-REPAIR-COMPOSE-OBSERVE-20260730",
        "SEC184-OFFICIAL-GITHUB-SOURCE-READONLY-20260730",
        "SEC184-OFFICIAL-GITHUB-STRUCTURAL-PROJECTION-20260730",
        "SEC184-OFFICIAL-GITHUB-VERIFIED-SHALLOW-CHECKOUT-20260730",
        "SEC192-CURRENT-INCIDENT-RECOVERY-20260828",
        "SEC194-ROOT-HELPER-UPGRADE-20260828",
        "SEC195-ONE-SHOT-BACKUP-AND-SNAPSHOT-VERIFY-20260828",
        "SEC196-CURRENT-INCIDENT-SNAPSHOT-PROOF-20260828",
        "SEC197-CURRENT-ABF8-INCIDENT-RECOVERY-20260828",
        }
    sec192 = live_go_by_id["SEC192-CURRENT-INCIDENT-RECOVERY-20260828"]
    assert sec192["status"] == "used_completed_recovered"
    assert sec192["consumption_status"] == "consumed_terminal_recovered"
    assert sec192["consumed"] is True
    assert sec192["limits"]["maximum_invocations"] == 1
    assert sec192["limits"]["retries"] == 0
    assert sec192["incident_recovery_authority"] is True
    assert sec192["backup_authority"] is False
    assert sec192["helper_upgrade_authority"] is False
    assert sec192["deploy_authority"] is False
    assert sec192["terminal_result"]["status"] == "recovered"
    assert sec192["terminal_result"]["evidence_sha256"] == (
        "f806b239c7b81a07af855e7f20eb91bce5c46cb26949c0936555fbf3bfe23991"
    )
    sec194 = live_go_by_id["SEC194-ROOT-HELPER-UPGRADE-20260828"]
    assert sec194["status"] == "used_completed_upgraded"
    assert sec194["consumption_status"] == "consumed_terminal_upgraded"
    assert sec194["consumed"] is True
    assert sec194["limits"]["maximum_invocations"] == 1
    assert sec194["limits"]["retries"] == 0
    assert sec194["helper_upgrade_authority"] is True
    assert sec194["backup_authority"] is False
    assert sec194["snapshot_observation_authority"] is False
    assert sec194["deploy_authority"] is False
    assert sec194["terminal_result"]["status"] == "upgraded"
    assert sec194["terminal_result"]["helper_upgraded"] is True
    assert sec194["terminal_result"]["readback_upgraded"] is True
    assert sec194["terminal_result"]["rollback_attempted"] is False
    assert sec194["terminal_result"]["retry_permitted"] is False
    assert sec194["terminal_result"]["evidence_sha256"] == (
        "99e4132115c0aad8e87d3b3f0c9f214e2897e99d74ef89419284a98fd740526b"
    )
    sec195 = live_go_by_id["SEC195-ONE-SHOT-BACKUP-AND-SNAPSHOT-VERIFY-20260828"]
    assert sec195["status"] == "used_terminal_backup_unknown_snapshot_not_invoked"
    assert sec195["consumption_status"] == "consumed_terminal_backup_unknown"
    assert sec195["consumed"] is True
    assert sec195["limits"]["maximum_backup_invocations"] == 1
    assert sec195["limits"]["backup_timeout_seconds"] == 1860
    assert sec195["limits"]["maximum_snapshot_observations"] == 1
    assert sec195["limits"]["retries"] == 0
    assert sec195["backup_authority"] is True
    assert sec195["snapshot_observation_authority"] is True
    assert sec195["helper_upgrade_authority"] is False
    assert sec195["deploy_authority"] is False
    assert sec195["snapshot_observation_status"] == "not_invoked_backup_not_completed"
    assert sec195["terminal_result"]["status"] == "unknown"
    assert sec195["terminal_result"]["error_code"] == "start_failed"
    assert sec195["terminal_result"]["arm_created"] is True
    assert sec195["terminal_result"]["unit_invoked"] is True
    assert sec195["terminal_result"]["backup_succeeded"] is False
    assert sec195["terminal_result"]["unit_inactive"] is False
    assert sec195["terminal_result"]["arm_cleanup_succeeded"] is False
    assert sec195["terminal_result"]["manual_recovery_required"] is True
    assert sec195["terminal_result"]["retry_permitted"] is False
    assert sec195["terminal_result"]["evidence_sha256"] == (
        "6da575cd40a5ea6bab45e2849893f2350fa33bee859429e43db18779eeec1cf1"
    )
    sec196 = live_go_by_id["SEC196-CURRENT-INCIDENT-SNAPSHOT-PROOF-20260828"]
    assert sec196["status"] == "used_completed_snapshot_stale"
    assert sec196["consumption_status"] == "consumed_terminal_snapshot_stale"
    assert sec196["consumed"] is True
    assert sec196["limits"]["maximum_invocations"] == 1
    assert sec196["limits"]["outer_timeout_seconds"] == 30
    assert sec196["limits"]["retries"] == 0
    assert sec196["current_incident_observation_authority"] is True
    assert sec196["snapshot_observation_authority"] is True
    assert sec196["incident_recovery_authority"] is False
    assert sec196["backup_authority"] is False
    assert sec196["deploy_authority"] is False
    assert sec196["terminal_result"]["status"] == "blocked"
    assert sec196["terminal_result"]["error_code"] == "snapshot_stale"
    assert sec196["terminal_result"]["evidence_sha256"] == (
        "7b0ff041c02d43c5ba331eb3e3b24e651df68dbbaee52adcd86df2399f8c32d1"
    )
    sec197 = live_go_by_id["SEC197-CURRENT-ABF8-INCIDENT-RECOVERY-20260828"]
    assert sec197["status"] == "used_terminal_recovery_preflight_blocked"
    assert sec197["consumption_status"] == "consumed_terminal_recovery_preflight_blocked"
    assert sec197["consumed"] is True
    assert sec197["limits"]["maximum_invocations"] == 1
    assert sec197["limits"]["outer_timeout_seconds"] == 45
    assert sec197["limits"]["retries"] == 0
    assert sec197["incident_recovery_authority"] is True
    assert sec197["backup_authority"] is False
    assert sec197["snapshot_observation_authority"] is False
    assert sec197["deploy_authority"] is False
    assert sec197["external_mutation_performed"] is False
    assert sec197["terminal_result"]["status"] == "blocked"
    assert sec197["terminal_result"]["error_code"] == "preflight_failed"
    assert sec197["terminal_result"]["recovery_invoked"] is False
    assert sec197["terminal_result"]["arm_removed"] is False
    assert sec197["terminal_result"]["unit_reset"] is False
    assert sec197["terminal_result"]["retry_permitted"] is False
    assert sec197["terminal_result"]["evidence_sha256"] == (
        "b1da6fc5fe9c3f9ea7e2a1cedc57f797dce571e18bdff9d431484c4727fa4e6e"
    )
    sec182 = live_go_by_id["SEC182-SOURCE-AST-REPAIR-PUBLISH-20260730"]
    assert sec182["status"] == "used_completed_remote_readback"
    assert sec182["consumed"] is True
    assert sec182["limits"]["expected_remote_parent"] == (
        "2de55c9747ae37062b5641a995265c5bd3b8f2e5"
    )
    assert sec182["limits"]["path_count"] == 6
    assert sec182["push_counter"] == 1
    sec183 = live_go_by_id["SEC183-POST-SOURCE-AST-REPAIR-COMPOSE-OBSERVE-20260730"]
    assert sec183["status"] == "used_terminal_needs_live_observation"
    assert sec183["consumed"] is True
    assert sec183["invocation_counter"] == 1
    assert sec183["terminal_result"]["evidence_sha256"] == (
        "d004b673a1dc2f861a03a367ab91fae7f62d3a810e81bbf4093ce25cb4068049"
    )
    sec184 = live_go_by_id["SEC184-OFFICIAL-GITHUB-SOURCE-READONLY-20260730"]
    assert sec184["status"] == "used_completed_exact_identity_and_replay"
    assert sec184["consumed"] is True
    assert sec184["limits"]["writes"] == 0
    assert sec184["terminal_result"]["commit_sha"] == (
        "0f6537e9cfa38f6035ac57c1716b6d55dbaf3ca4"
    )
    projection = live_go_by_id[
        "SEC184-OFFICIAL-GITHUB-STRUCTURAL-PROJECTION-20260730"
    ]
    assert projection["status"] == "used_completed_structural_projection"
    assert projection["consumed"] is True
    assert projection["limits"]["raw_source_output"] == 0
    checkout = live_go_by_id[
        "SEC184-OFFICIAL-GITHUB-VERIFIED-SHALLOW-CHECKOUT-20260730"
    ]
    assert checkout["status"] == "used_completed_verified_checkout"
    assert checkout["consumed"] is True
    assert checkout["limits"]["maximum_clones"] == 1
    assert checkout["terminal_result"]["canonical_source_sha256"] == (
        "10df1662477a673dc803c03e89c1bc1fba6c8c091e716fb6c7dd09c0081e1255"
    )
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

    sec146 = live_go_by_id["SEC146-COMPOSE-OBSERVE-20260729"]
    assert sec146["status"] == "used"
    assert sec146["consumption_status"] == "consumed_terminal_blocked"
    assert sec146["invocation_counter"] == 1
    assert sec146["result_counter"] == 1
    assert sec146["limits"]["maximum_invocations"] == 1
    assert sec146["limits"]["maximum_results"] == 1
    assert sec146["limits"]["retries"] == 0
    assert sec146["reuse_permitted"] is False
    assert sec146["terminal_result"]["status"] == "blocked"
    assert sec146["terminal_result"]["error_code"] == "transport_failed"
    assert sec146["terminal_result"]["retry_permitted"] is False
    assert (
        sec146["terminal_result"]["evidence_sha256"]
        == sec143["terminal_result"]["evidence_sha256"]
    )
    assert sec146["public_ip_query_authority"] is False
    assert sec146["deploy_authority"] is False
    assert sec146["delivery_or_send_authority"] is False

    sec153 = live_go_by_id["SEC153-TRANSPORT-V2-OBSERVE-20260729"]
    assert sec153["status"] == "used"
    assert sec153["consumption_status"] == (
        "consumed_terminal_needs_live_observation"
    )
    assert sec153["consumed"] is True
    assert sec153["invocation_counter"] == 1
    assert sec153["result_counter"] == 1
    assert sec153["limits"] == {
        "arguments": 0,
        "maximum_invocations": 1,
        "maximum_results": 1,
        "outer_timeout_seconds": 30,
        "follow_on_queries": 0,
        "retries": 0,
    }
    assert sec153["reuse_permitted"] is False
    assert sec153["terminal_result"] == {
        "result_family": "strict_preserved_observer_v1",
        "schema_id": "odysseus.redacted_podman_compose_capability_observation.v1",
        "status": "needs_live_observation",
        "reason_code": "semantic_proof_insufficient",
        "missing_proofs": ["source_up_no_deps_guard_missing"],
        "retry_permitted": False,
        "evidence_sha256": (
            "b34ebfd08cd39d721b105aa85b7a4442d98c1b48a07f8b95fc9c2a72390d5968"
        ),
        "offline_strict_validation": "passed",
    }
    assert sec153["public_ip_query_authority"] is False
    assert sec153["deploy_authority"] is False
    assert sec153["delivery_or_send_authority"] is False

    latest_sec153 = sirp["latest_sec153_transport_v2_observation_run"]
    assert latest_sec153["status"] == (
        "single_observation_terminal_needs_live_observation_no_retry"
    )
    assert latest_sec153["invocation_counter"] == 1
    assert latest_sec153["result_counter"] == 1
    assert latest_sec153["grant_consumption"] == {
        "status": "consumed_terminal_needs_live_observation",
        "invocations": 1,
        "results": 1,
        "retries": 0,
        "follow_on_queries": 0,
        "reuse_permitted": False,
    }
    assert latest_sec153["terminal_result"]["capability_supported"] is False
    assert latest_sec153["terminal_result"]["deploy_authority_granted"] is False
    assert (
        latest_sec153["terminal_result"]["delivery_or_send_authority_granted"]
        is False
    )
    assert latest_sec153["public_ip_query_authority"] is False
    assert latest_sec153["deploy_authority"] is False
    assert latest_sec153["delivery_or_send_authority"] is False

    sec158_provenance = live_go_by_id[
        "SEC158-COMPOSE-CANDIDATE-PROVENANCE-READONLY-20260730"
    ]
    assert sec158_provenance["status"] == "used_terminal_blocked"
    assert sec158_provenance["consumption_status"] == (
        "consumed_terminal_fetch_error_at_local_network_boundary"
    )
    assert sec158_provenance["consumed"] is True
    assert sec158_provenance["invocation_counter"] == 1
    assert sec158_provenance["result_counter"] == 1
    assert sec158_provenance["external_action_executed"] is True
    assert sec158_provenance["reuse_permitted"] is False
    assert sec158_provenance["terminal_result"] == {
        "schema_id": "odysseus.redacted_compose_candidate_provenance.v1",
        "status": "blocked",
        "candidate_status": "blocked",
        "execution_status": "fetch_error",
        "request_count": 1,
        "page_count": 1,
        "body_count": 0,
        "body_bytes": 0,
        "origin_count": 1,
        "retry_permitted": False,
        "redaction_status": "fixed_key_only",
        "evidence_sha256": (
            "95fd4f9e5b71867b9d62a8419b019910b2e771064fff2930ce5c649627856536"
        ),
    }
    for authority_key in (
        "package_or_host_change_authority",
        "live_observation_authority",
        "public_ip_query_authority",
        "deploy_authority",
        "delivery_or_send_authority",
    ):
        assert sec158_provenance[authority_key] is False

    sec159_provenance = live_go_by_id[
        "SEC159-COMPOSE-CANDIDATE-PROVENANCE-EGRESS-RECOVERY-20260730"
    ]
    assert sec159_provenance["status"] == "used_completed"
    assert sec159_provenance["consumption_status"] == (
        "consumed_terminal_completed_candidate_eligible"
    )
    assert sec159_provenance["consumed"] is True
    assert sec159_provenance["invocation_counter"] == 1
    assert sec159_provenance["result_counter"] == 1
    assert sec159_provenance["external_action_executed"] is True
    assert sec159_provenance["reuse_permitted"] is False
    assert sec159_provenance["terminal_result"] == {
        "schema_id": "odysseus.redacted_compose_candidate_provenance.v1",
        "status": "completed",
        "candidate_status": "eligible",
        "required_field_status": "complete",
        "provider_chain_status": "complete",
        "immutable_identity_status": "verified",
        "signature_verification_status": "unavailable",
        "version": "1.6.0",
        "package_or_artifact_identity": "podman_compose-1.6.0.tar.gz",
        "immutable_identity": (
            "sha256:c83fd9bcbaa635100d581ce52a7a4b712ee0d457481232aff392efe3ebc5a217"
        ),
        "request_count": 4,
        "page_count": 4,
        "body_count": 4,
        "body_bytes": 243602,
        "origin_count": 3,
        "retry_permitted": False,
        "redaction_status": "fixed_key_only",
        "evidence_sha256": (
            "74cea20633c55a55640b7fd4ac42348e7f186c64208645f76c09df7e0e84ddee"
        ),
    }
    for authority_key in (
        "package_or_host_change_authority",
        "live_observation_authority",
        "public_ip_query_authority",
        "deploy_authority",
        "delivery_or_send_authority",
    ):
        assert sec159_provenance[authority_key] is False

    sec163_publish = live_go_by_id["SEC163-ISOLATED-GATE-B-PUBLISH-20260730"]
    assert sec163_publish["status"] == "used_terminal_validation_failed_before_push"
    assert sec163_publish["consumption_status"] == "consumed_terminal_no_push"
    assert sec163_publish["consumed"] is True
    assert sec163_publish["limits"] == {
        "maximum_local_commits": 2,
        "maximum_clean_worktrees": 1,
        "maximum_cherry_picks": 2,
        "maximum_pushes": 1,
        "force_pushes": 0,
        "remote": "fuzzy",
        "branch": "dev",
        "expected_remote_parent": "67f0737de5bccdb5b8841e4ad9deee3df0107b74",
        "path_count": 15,
    }
    assert sec163_publish["local_content_commit"] == "3992ea9b"
    assert sec163_publish["isolated_content_commit"] == "72231897"
    assert sec163_publish["local_commit_counter"] == 2
    assert sec163_publish["clean_worktree_counter"] == 1
    assert sec163_publish["cherry_pick_counter"] == 2
    assert sec163_publish["push_counter"] == 0
    assert sec163_publish["force_push_counter"] == 0
    assert sec163_publish["external_action_executed"] is True
    assert sec163_publish["terminal_result"] == {
        "status": "blocked",
        "stage": "clean_worktree_gate_b_validation",
        "passed_tests": 81,
        "failed_tests": 1,
        "failure_class": "platform_dependent_worktree_line_endings",
        "push_executed": False,
    }
    assert sec163_publish["reuse_permitted"] is False
    for authority_key in (
        "package_or_host_change_authority",
        "live_observation_authority",
        "public_ip_query_authority",
        "deploy_authority",
        "delivery_or_send_authority",
    ):
        assert sec163_publish[authority_key] is False

    sec164_publish = live_go_by_id[
        "SEC164-CROSS-PLATFORM-BLOB-REPAIR-PUBLISH-20260730"
    ]
    assert sec164_publish["status"] == "used_completed_remote_readback"
    assert sec164_publish["consumption_status"] == "consumed_terminal_completed"
    assert sec164_publish["consumed"] is True
    assert sec164_publish["limits"] == {
        "maximum_local_commits": 1,
        "maximum_new_worktrees": 0,
        "maximum_cherry_picks": 1,
        "maximum_pushes": 1,
        "force_pushes": 0,
        "remote": "fuzzy",
        "branch": "dev",
        "expected_remote_parent": "67f0737de5bccdb5b8841e4ad9deee3df0107b74",
        "path_count": 3,
    }
    assert sec164_publish["local_commit_counter"] == 1
    assert sec164_publish["new_worktree_counter"] == 0
    assert sec164_publish["cherry_pick_counter"] == 1
    assert sec164_publish["push_counter"] == 1
    assert sec164_publish["force_push_counter"] == 0
    assert sec164_publish["external_action_executed"] is True
    assert sec164_publish["terminal_result"] == {
        "status": "completed",
        "remote_revision": "c5850998889660eb8074f94c45948548e26331f6",
        "remote_tree": "5cc3c261a0f2a01c195a6f0ef39c25d2f961c247",
        "expected_base_is_ancestor": True,
        "changed_path_count": 15,
        "telegram_commit_is_ancestor": False,
        "gate_b_tests": "82 passed",
        "observer_sha256": (
            "c4a48afb4d6c92e94f96ce3c13cf200cfadfadaf6b8710e1ce8977791c713f09"
        ),
        "transport_sha256": (
            "bd6d20e43b75508a32707f7935360476441446be4e145057731935ce9c97cb54"
        ),
        "host_change_sha256": (
            "36332245325d1b5e852b1336d00f2e0e41a495442e351ee5fd74fefa9834d624"
        ),
    }
    assert sec164_publish["reuse_permitted"] is False
    for authority_key in (
        "package_or_host_change_authority",
        "live_observation_authority",
        "public_ip_query_authority",
        "deploy_authority",
        "delivery_or_send_authority",
    ):
        assert sec164_publish[authority_key] is False

    sec165_host_change = live_go_by_id[
        "SEC165-SELECTED-COMPOSE-HOST-CHANGE-20260730"
    ]
    assert sec165_host_change["status"] == "used_terminal_unknown_readback"
    assert sec165_host_change["consumption_status"] == "consumed_terminal_no_retry"
    assert sec165_host_change["limits"] == {
        "maximum_invocations": 1,
        "maximum_results": 1,
        "maximum_wall_clock_seconds": 300,
        "retries": 0,
        "package_substitutions": 0,
        "target_substitutions": 0,
    }
    assert sec165_host_change["invocation_counter"] == 1
    assert sec165_host_change["result_counter"] == 0
    assert sec165_host_change["retry_counter"] == 0
    assert sec165_host_change["external_action_executed"] is True
    assert sec165_host_change["terminal_result"] == {
        "status": "unknown_readback",
        "reason_code": "local_validator_name_mismatch",
        "host_change_repeated": False,
        "retry_permitted": False,
    }
    assert sec165_host_change["reuse_permitted"] is False
    for authority_key in (
        "live_observation_authority",
        "public_ip_query_authority",
        "deploy_authority",
        "delivery_or_send_authority",
    ):
        assert sec165_host_change[authority_key] is False

    sec166_observe = live_go_by_id[
        "SEC166-POST-HOST-CHANGE-COMPOSE-OBSERVE-20260730"
    ]
    assert sec166_observe["status"] == "used_terminal_blocked"
    assert sec166_observe["consumption_status"] == "consumed_terminal_no_retry"
    assert sec166_observe["limits"] == {
        "arguments": 0,
        "maximum_invocations": 1,
        "maximum_results": 1,
        "outer_timeout_seconds": 30,
        "follow_on_queries": 0,
        "retries": 0,
    }
    assert sec166_observe["invocation_counter"] == 1
    assert sec166_observe["result_counter"] == 1
    assert sec166_observe["external_action_executed"] is True
    assert sec166_observe["terminal_result"] == {
        "schema_id": "odysseus.redacted_podman_compose_capability_transport.v2",
        "status": "blocked",
        "error_code": "transport_failed",
        "diagnostic_code": "ssh_unexpected_returncode",
        "retry_permitted": False,
        "evidence_sha256": (
            "e2cbb8609cf9c38b5844fcd66d53a3596b8d7b5b662647b5e08c3c4aaf289a3f"
        ),
    }
    assert sec166_observe["reuse_permitted"] is False
    for authority_key in (
        "public_ip_query_authority",
        "deploy_authority",
        "delivery_or_send_authority",
    ):
        assert sec166_observe[authority_key] is False

    sec169_publish = live_go_by_id["SEC169-HOST-STATE-READBACK-PUBLISH-20260730"]
    assert sec169_publish["status"] == "used_completed_remote_readback"
    assert sec169_publish["consumption_status"] == "consumed_terminal_completed"
    assert sec169_publish["limits"] == {
        "maximum_local_commits": 1,
        "maximum_new_worktrees": 0,
        "maximum_cherry_picks": 1,
        "maximum_pushes": 1,
        "force_pushes": 0,
        "remote": "fuzzy",
        "branch": "dev",
        "expected_remote_parent": "c5850998889660eb8074f94c45948548e26331f6",
        "path_count": 4,
    }
    assert sec169_publish["local_commit_counter"] == 1
    assert sec169_publish["new_worktree_counter"] == 0
    assert sec169_publish["cherry_pick_counter"] == 1
    assert sec169_publish["push_counter"] == 1
    assert sec169_publish["force_push_counter"] == 0
    assert sec169_publish["external_action_executed"] is True
    assert sec169_publish["terminal_result"] == {
        "status": "completed",
        "remote_revision": "dca9d7267f350263d62e10c60edfbf496c323c5c",
        "remote_parent": "c5850998889660eb8074f94c45948548e26331f6",
        "remote_tree": "db4ee88795bb7bd8dd0773a8824136f57facbb82",
        "changed_path_count": 4,
        "readback_sha256": (
            "f5d2324b67875324fd98ade26636b5d6b4ae3167c935483afce3d6683070e19e"
        ),
        "test_sha256": (
            "b56e595e3d9615c159faac2f1b49a8f565dc694ea0974c325022f26e502241ac"
        ),
        "clean_tests": "8 passed",
    }
    assert sec169_publish["reuse_permitted"] is False
    for authority_key in (
        "package_or_host_change_authority",
        "live_observation_authority",
        "public_ip_query_authority",
        "deploy_authority",
        "delivery_or_send_authority",
    ):
        assert sec169_publish[authority_key] is False

    sec170_readback = live_go_by_id[
        "SEC170-ONE-USE-HOST-STATE-READBACK-20260730"
    ]
    assert sec170_readback["status"] == "used_completed"
    assert sec170_readback["consumption_status"] == "consumed_terminal_completed"
    assert sec170_readback["limits"] == {
        "arguments": 0,
        "maximum_invocations": 1,
        "maximum_results": 1,
        "outer_timeout_seconds": 30,
        "follow_on_queries": 0,
        "retries": 0,
    }
    assert sec170_readback["invocation_counter"] == 1
    assert sec170_readback["result_counter"] == 1
    assert sec170_readback["external_action_executed"] is True
    assert sec170_readback["terminal_result"] == {
        "schema_id": "odysseus.redacted_compose_candidate_host_readback.v1",
        "status": "observed",
        "state": "target_absent",
        "expected_user": True,
        "target_exists": False,
        "target_is_directory": False,
        "target_is_symlink": False,
        "temp_exists": False,
        "temp_is_directory": False,
        "temp_is_symlink": False,
        "venv_python_regular": False,
        "venv_python_executable": False,
        "podman_compose_distribution_present": False,
        "exact_version_1_6_0": False,
        "evidence_sha256": (
            "7d0527bc873e8bcba1129a4ee50702b853187d02f0e5935d04d9aef5c2072cea"
        ),
    }
    assert sec170_readback["reuse_permitted"] is False
    for authority_key in (
        "package_or_host_change_authority",
        "public_ip_query_authority",
        "deploy_authority",
        "delivery_or_send_authority",
    ):
        assert sec170_readback[authority_key] is False

    sec172_publish = live_go_by_id[
        "SEC172-PYTHON-PACKAGING-OBSERVER-PUBLISH-20260730"
    ]
    assert sec172_publish["status"] == "used_completed_remote_readback"
    assert sec172_publish["consumption_status"] == "consumed_terminal_completed"
    assert sec172_publish["limits"] == {
        "maximum_local_commits": 1,
        "maximum_new_worktrees": 0,
        "maximum_cherry_picks": 1,
        "maximum_pushes": 1,
        "force_pushes": 0,
        "remote": "fuzzy",
        "branch": "dev",
        "expected_remote_parent": "dca9d7267f350263d62e10c60edfbf496c323c5c",
        "path_count": 4,
    }
    assert sec172_publish["local_commit_counter"] == 1
    assert sec172_publish["new_worktree_counter"] == 0
    assert sec172_publish["cherry_pick_counter"] == 1
    assert sec172_publish["push_counter"] == 1
    assert sec172_publish["force_push_counter"] == 0
    assert sec172_publish["external_action_executed"] is True
    assert sec172_publish["terminal_result"] == {
        "status": "completed",
        "remote_revision": "1652a16badd7ebd70e0ca4611fc6d2fc5d4afb3c",
        "remote_parent": "dca9d7267f350263d62e10c60edfbf496c323c5c",
        "remote_tree": "052aa6fca97ff9c5b77d641913ef720e3f6cabad",
        "changed_path_count": 4,
        "observer_sha256": (
            "6a307e92416c7dcfa3d0f845c10120e1121ebc481d5d84cc1701e9f21fe355ec"
        ),
        "test_sha256": (
            "28ddd233545a9e3179c3aa7ec1aa48c9fef462c7e0e1fc635bb18f3b81732b97"
        ),
        "clean_tests": "6 passed",
    }
    assert sec172_publish["reuse_permitted"] is False
    for authority_key in (
        "package_or_host_change_authority",
        "live_observation_authority",
        "public_ip_query_authority",
        "deploy_authority",
        "delivery_or_send_authority",
    ):
        assert sec172_publish[authority_key] is False

    sec173_observe = live_go_by_id[
        "SEC173-ONE-USE-PYTHON-PACKAGING-OBSERVE-20260730"
    ]
    assert sec173_observe["status"] == "used_completed"
    assert sec173_observe["consumption_status"] == "consumed_terminal_completed"
    assert sec173_observe["limits"] == {
        "arguments": 0,
        "maximum_invocations": 1,
        "maximum_results": 1,
        "outer_timeout_seconds": 30,
        "follow_on_queries": 0,
        "retries": 0,
    }
    assert sec173_observe["invocation_counter"] == 1
    assert sec173_observe["result_counter"] == 1
    assert sec173_observe["external_action_executed"] is True
    assert sec173_observe["terminal_result"] == {
        "schema_id": "odysseus.redacted_python_packaging_capability_observation.v1",
        "status": "observed",
        "state": "observed",
        "expected_user": True,
        "venv_module_present": True,
        "ensurepip_module_present": True,
        "pip_module_present": True,
        "setuptools_module_present": True,
        "wheel_module_present": True,
        "evidence_sha256": (
            "6bff1537283ce95b982a935ae2e23c347b72013786735f68eddecc4b43539f5c"
        ),
    }
    assert sec173_observe["reuse_permitted"] is False
    for authority_key in (
        "package_or_host_change_authority",
        "public_ip_query_authority",
        "deploy_authority",
        "delivery_or_send_authority",
    ):
        assert sec173_observe[authority_key] is False

    sec175_publish = live_go_by_id[
        "SEC175-HOST-CHANGE-IDENTITY-REPAIR-PUBLISH-20260730"
    ]
    assert sec175_publish["status"] == "used_completed_remote_readback"
    assert sec175_publish["consumption_status"] == "consumed_terminal_completed"
    assert sec175_publish["limits"] == {
        "maximum_local_commits": 1,
        "maximum_new_worktrees": 0,
        "maximum_cherry_picks": 1,
        "maximum_pushes": 1,
        "force_pushes": 0,
        "remote": "fuzzy",
        "branch": "dev",
        "expected_remote_parent": "1652a16badd7ebd70e0ca4611fc6d2fc5d4afb3c",
        "path_count": 4,
    }
    assert sec175_publish["local_commit_counter"] == 1
    assert sec175_publish["new_worktree_counter"] == 0
    assert sec175_publish["cherry_pick_counter"] == 1
    assert sec175_publish["push_counter"] == 1
    assert sec175_publish["force_push_counter"] == 0
    assert sec175_publish["external_action_executed"] is True
    assert sec175_publish["terminal_result"] == {
        "status": "completed",
        "remote_revision": "8fdc1cdebebb720537863ea0de8182155ce03e6a",
        "remote_parent": "1652a16badd7ebd70e0ca4611fc6d2fc5d4afb3c",
        "remote_tree": "0a40029cebf94b9425de0a47026e6b56cb7df119",
        "changed_path_count": 4,
        "host_change_sha256": (
            "1ee598d06043d2d0b0e1331caca0249a09e3a14f3f88804ccca010e04596497a"
        ),
        "test_sha256": (
            "90f1201479f76eea0d192539295a0a5a6d04780770c82428d9d6b5422c52b248"
        ),
        "clean_tests": "21 passed",
    }
    assert sec175_publish["reuse_permitted"] is False
    for authority_key in (
        "package_or_host_change_authority",
        "live_observation_authority",
        "public_ip_query_authority",
        "deploy_authority",
        "delivery_or_send_authority",
    ):
        assert sec175_publish[authority_key] is False

    sec176_host_change = live_go_by_id[
        "SEC176-REPAIRED-COMPOSE-HOST-CHANGE-20260730"
    ]
    assert sec176_host_change["status"] == "used_completed"
    assert sec176_host_change["consumption_status"] == "consumed_terminal_completed"
    assert sec176_host_change["limits"] == {
        "maximum_invocations": 1,
        "maximum_results": 1,
        "maximum_wall_clock_seconds": 300,
        "retries": 0,
        "package_substitutions": 0,
        "target_substitutions": 0,
    }
    assert sec176_host_change["invocation_counter"] == 1
    assert sec176_host_change["result_counter"] == 1
    assert sec176_host_change["retry_counter"] == 0
    assert sec176_host_change["external_action_executed"] is True
    assert sec176_host_change["terminal_result"] == {
        "schema_id": "odysseus.redacted_compose_candidate_host_change.v1",
        "status": "completed",
        "phase": "completed",
        "attempt_consumed": True,
        "retry_permitted": False,
        "rollback_performed": False,
        "target_published": True,
        "evidence_sha256": (
            "22e7de4668398cf47402e6f4d7ed5771a31c2264070855788dc0e346fbadf21e"
        ),
    }
    assert sec176_host_change["reuse_permitted"] is False
    for authority_key in (
        "live_observation_authority",
        "public_ip_query_authority",
        "deploy_authority",
        "delivery_or_send_authority",
    ):
        assert sec176_host_change[authority_key] is False

    sec177_observe = live_go_by_id[
        "SEC177-POST-RECOVERY-COMPOSE-OBSERVE-20260730"
    ]
    assert sec177_observe["status"] == "used_terminal_blocked"
    assert sec177_observe["consumption_status"] == "consumed_terminal_no_retry"
    assert sec177_observe["limits"] == {
        "arguments": 0,
        "maximum_invocations": 1,
        "maximum_results": 1,
        "outer_timeout_seconds": 30,
        "follow_on_queries": 0,
        "retries": 0,
    }
    assert sec177_observe["invocation_counter"] == 1
    assert sec177_observe["result_counter"] == 1
    assert sec177_observe["external_action_executed"] is True
    assert sec177_observe["terminal_result"] == {
        "schema_id": "odysseus.redacted_podman_compose_capability_observation.v1",
        "status": "blocked",
        "error_code": "version_unavailable",
        "retry_permitted": False,
        "evidence_sha256": (
            "5555082038da7de498df7b466c2490f347b977b2ef7cd0b370c924adcea285ad"
        ),
    }
    assert sec177_observe["reuse_permitted"] is False
    for authority_key in (
        "public_ip_query_authority",
        "deploy_authority",
        "delivery_or_send_authority",
    ):
        assert sec177_observe[authority_key] is False

    sec179_publish = live_go_by_id[
        "SEC179-OBSERVER-METADATA-REPAIR-PUBLISH-20260730"
    ]
    assert sec179_publish["status"] == "used_completed_remote_readback"
    assert sec179_publish["consumption_status"] == "consumed_terminal_completed"
    assert sec179_publish["limits"] == {
        "maximum_local_commits": 1,
        "maximum_new_worktrees": 0,
        "maximum_cherry_picks": 1,
        "maximum_pushes": 1,
        "force_pushes": 0,
        "remote": "fuzzy",
        "branch": "dev",
        "expected_remote_parent": "8fdc1cdebebb720537863ea0de8182155ce03e6a",
        "path_count": 6,
    }
    assert sec179_publish["local_commit_counter"] == 1
    assert sec179_publish["new_worktree_counter"] == 0
    assert sec179_publish["cherry_pick_counter"] == 1
    assert sec179_publish["push_counter"] == 1
    assert sec179_publish["force_push_counter"] == 0
    assert sec179_publish["external_action_executed"] is True
    assert sec179_publish["terminal_result"] == {
        "status": "completed",
        "remote_revision": "2de55c9747ae37062b5641a995265c5bd3b8f2e5",
        "remote_parent": "8fdc1cdebebb720537863ea0de8182155ce03e6a",
        "remote_tree": "220c50798b6e44c56bff1780ee56dd048204f2b3",
        "changed_path_count": 6,
        "observer_sha256": (
            "9c30ecf74af6d58b9553591e66ca509d1511e53d840a6ff6860f66c8e8482454"
        ),
        "transport_sha256": (
            "b5622820fe6ae28c0c6bdc4aa1a0cb9678499628938024a7dacd17b9fd76cbf5"
        ),
        "clean_tests": "39 passed",
    }
    assert sec179_publish["reuse_permitted"] is False
    for authority_key in (
        "package_or_host_change_authority",
        "live_observation_authority",
        "public_ip_query_authority",
        "deploy_authority",
        "delivery_or_send_authority",
    ):
        assert sec179_publish[authority_key] is False

    sec180_observe = live_go_by_id[
        "SEC180-POST-METADATA-REPAIR-COMPOSE-OBSERVE-20260730"
    ]
    assert sec180_observe["status"] == "used_terminal_blocked"
    assert sec180_observe["consumption_status"] == "consumed_terminal_no_retry"
    assert sec180_observe["limits"] == {
        "arguments": 0,
        "maximum_invocations": 1,
        "maximum_results": 1,
        "outer_timeout_seconds": 30,
        "follow_on_queries": 0,
        "retries": 0,
    }
    assert sec180_observe["invocation_counter"] == 1
    assert sec180_observe["result_counter"] == 1
    assert sec180_observe["external_action_executed"] is True
    assert sec180_observe["terminal_result"] == {
        "schema_id": "odysseus.redacted_podman_compose_capability_observation.v1",
        "status": "blocked",
        "error_code": "help_unavailable",
        "retry_permitted": False,
        "evidence_sha256": (
            "9c4efc9d44525c9bc08e564035ba97db83bbb9c59c67429568bca75c0be9a6d7"
        ),
    }
    assert sec180_observe["reuse_permitted"] is False
    for authority_key in (
        "public_ip_query_authority",
        "deploy_authority",
        "delivery_or_send_authority",
    ):
        assert sec180_observe[authority_key] is False

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

    recorded_hashes: dict[str, str] = {}
    for relative_path, expected_hash in evidence["file_sha256"].items():
        _assert_repo_relative(relative_path)
        assert (ROOT / relative_path).is_file()
        assert len(expected_hash) == 64
        int(expected_hash, 16)
        recorded_hashes[relative_path] = expected_hash

    aggregate_payload = "".join(
        f"{path}\0{recorded_hashes[path]}\n" for path in sorted(recorded_hashes)
    ).encode("utf-8")
    assert hashlib.sha256(aggregate_payload).hexdigest() == evidence["aggregate_sha256"]


def test_sec188_publication_is_terminal_and_records_the_distinct_ci_blocker() -> None:
    sirp = _load(SIRP_PATH)
    claim = next(
        claim
        for claim in sirp["claims"]
        if claim["slice_id"]
        == "SEC188-exact-mcp1-pin-publication-and-github-readback"
    )
    assert claim["state"] == "released"
    assert claim["expected_remote_parent"] == (
        "1c2df18124ee946e6942f25cc6ec74709188a6b7"
    )

    handoff = sirp["latest_sec188_exact_mcp1_pin_publication_handoff"]
    assert handoff["commit"] == "089002d2715122e13b5eaa5f2fccedae83aef29e"
    assert handoff["parent"] == "1c2df18124ee946e6942f25cc6ec74709188a6b7"
    assert handoff["remote"] == "fuzzy/dev"
    assert handoff["changed_paths"] == [
        "requirements.txt",
        "tests/test_python_version_contract.py",
    ]
    assert set(handoff["remote_readback"].values()) == {True}

    actions = handoff["github_actions"]
    assert actions["run_id"] == 30553849019
    assert actions["head_sha"] == handoff["commit"]
    assert actions["fresh_python311_dependency_install"] == "passed"
    assert actions["python_syntax"] == "passed"
    assert actions["javascript_syntax"] == "passed"
    assert actions["full_pytest"] == "failed"
    assert actions["failing_node"] == (
        "tests/test_audit_unified_source_index_runtime.py::"
        "test_committed_runtime_inventory_matches_deterministic_static_ast_scan"
    )
    assert actions["introduced_by_sec187_or_sec188"] is False
    assert handoff["diagnosis"]["approval_boundary"].startswith(
        "Treat this as a distinct CI fix"
    )
