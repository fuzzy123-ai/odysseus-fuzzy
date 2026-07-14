from __future__ import annotations

import json
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
ROADMAP_PATH = ROOT / "docs" / "plans" / "system-assurance-runtime-hardening-roadmap.json"
MASTER_PATH = ROOT / "docs" / "plans" / "system-optimization-master-roadmap.md"
REGRESSION_QUEUE_PATH = ROOT / "docs" / "plans" / "regression-queue.json"

EXPECTED_FINDING_IDS = {
    "SAR-F01-ci-absent",
    "SAR-F02-static-release-baseline",
    "SAR-F03-response-cache-fifo",
    "SAR-F04-dead-host-persistence",
    "SAR-F05-tailscale-cache-no-ttl",
    "SAR-F06-http2-disabled",
    "SAR-F07-agent-loop-db-query",
    "SAR-F08-in-memory-rate-limiter",
    "SAR-F09-context-hard-max",
    "SAR-F10-token-estimator",
    "SAR-F11-rag-import-fallback",
    "SAR-F12-orchestration-dry-run",
    "SAR-F13-universal-inbox-offline",
    "SAR-F14-provider-proof-roadmap-stale",
    "SAR-F15-source-view-open",
    "SAR-F16-nextcloud-roadmap-stale",
    "SAR-F17-external-install-upgrade-ambiguity",
    "SAR-F18-background-job-file-state",
    "SAR-F19-rotating-log-multiprocess",
    "SAR-F20-telegram-voice-offline",
}

REQUIRED_TOP_LEVEL = {
    "schema_version",
    "kind",
    "roadmap_id",
    "title",
    "created_at",
    "updated_at",
    "status",
    "abc_mode",
    "master_roadmap",
    "canonical_open_work_queue",
    "implementation_run_state",
    "goal",
    "queue_scope",
    "mutation_authority",
    "route",
    "authoring_execution",
    "evidence_baseline",
    "disposition_values",
    "finding_dispositions",
    "frozen_decisions",
    "architecture_contract",
    "external_dependencies",
    "dependency_dag",
    "slice_contract_fields",
    "slice_queue",
    "gate_queue",
    "long_run_boundary",
    "non_goals",
    "global_stop_rules",
    "completion_contract",
    "verification",
    "recommended_next_step",
}

REQUIRED_SLICE_FIELDS = {
    "id",
    "execution_status",
    "mutation_class",
    "priority",
    "owner",
    "depends_on",
    "allowed_paths",
    "hotfiles",
    "objective",
    "deliverables",
    "tests",
    "evidence_required",
    "gate",
    "done_when",
    "stop_rules",
    "forbidden_actions",
}

SAR_06P_ID = "SAR-06P-model-hint-hardlimit-propagation"
SAR_06P_PRODUCTION_PATHS = [
    "src/token_estimator.py",
    "src/context_compactor.py",
    "src/context_orchestrator.py",
    "src/plugin_system.py",
    "plugins/obsidian/backend/context_provider.py",
    "src/chat_processor.py",
    "src/agent_loop_orchestration.py",
    "src/delegate_tool.py",
    "src/agent_loop.py",
    "routes/chat_helpers.py",
    "routes/chat_routes.py",
    "routes/history_routes.py",
    "core/session_manager.py",
]
SAR_06P_TEST_PATHS = [
    "tests/test_context_compactor_model_hint_propagation.py",
    "tests/test_agent_loop_model_hint_budget.py",
    "tests/test_chat_routes_model_hint_usage.py",
    "tests/test_context_orchestrator_model_hint_budget.py",
    "tests/test_history_routes_model_hint_usage.py",
    "tests/test_chat_helpers_model_hint_budget.py",
    "tests/test_session_manager_model_hint_budget.py",
    "tests/test_context_provider_model_hint_budget.py",
    "plugins/obsidian/tests/test_context_provider_model_hint_budget.py",
    "tests/test_dynamic_context_budget.py",
    "tests/test_chat_helpers.py",
    "tests/test_kv_cache_invalidation_2927.py",
    "tests/test_review_regressions.py",
    "tests/test_compaction_summary_failure.py",
    "tests/test_history_compact_tool_calls.py",
    "tests/test_token_budget_model_aware.py",
    "tests/test_plugin_system.py",
    "tests/test_delegate_tool.py",
]
SAR_06P_HOTFILES = [
    "src/agent_loop.py",
    "routes/chat_helpers.py",
    "routes/chat_routes.py",
    "tests/test_chat_helpers.py",
]
SAR_06P_NO_HINT_ALLOWLIST = [
    "core/session_serialization.py::estimate_message_tokens_dict",
    "scripts/performance_baseline.py::profile_long_chat::estimate_probe",
    "scripts/performance_baseline.py::profile_long_chat::after_tokens",
]
SAR_06P_EVIDENCE = [
    "AST contract proves every productive direct estimate_tokens call passes model_hint except exactly core/session_serialization.py::estimate_message_tokens_dict, scripts/performance_baseline.py::profile_long_chat::estimate_probe and scripts/performance_baseline.py::profile_long_chat::after_tokens",
    "trim and compaction cover plain text, Unicode, JSON and tool calls; every trimmable result is at or below the selected model-aware budget",
    "no-hint calls preserve legacy estimates, trimming behavior and the 32000 automatic default",
    "dialog adjacency and assistant tool-call to tool-result pairing are preserved",
    "Agent loop, Chat helper, Chat route and History route evidence records the exact selected model; Chat fallback priority is _actual_model then _answered_by then _requested_model",
    "SessionManager ignores an intentionally wrong metadata.estimated_tokens cache when model_hint is present, reuses and persists the legacy scalar when model_hint is absent and never mutates that scalar during a model switch",
    "legacy four-argument providers remain compatible; only accepts_model_hint opt-in providers receive exactly one keyword and an internal TypeError propagates without retry",
    "Obsidian provider subbudgets are model-aware and bounded",
    "all tests run offline without downloads, network access or provider calls",
]
SAR_06P_DONE_WHEN = (
    "PASS only if the explicit four-file handoff and atomic single-owner claim are "
    "recorded, every productive hardlimit callsite selects the current model except "
    "the exact three-entry no-hint allowlist, trimming and provider subbudgets stay "
    "within the selected model budget, dialog and tool pairing remain intact, legacy "
    "no-hint and four-argument-provider behavior remain unchanged, SessionManager "
    "never persists a model-specific scalar, the automatic default remains 32000 and "
    "every declared focused and compatibility test is green."
)
SAR_06P_STOP_RULES = [
    "Stop before any write until explicit file-level handoff is acknowledged for src/agent_loop.py, routes/chat_helpers.py, routes/chat_routes.py and tests/test_chat_helpers.py and one serialized owner holds the full atomic claim.",
    "Stop if any handoff hash changed, a foreign staged file exists or a foreign hunk overlaps an allowed path.",
    "Stop if a model-specific token estimate is persisted in metadata.estimated_tokens.",
    "Stop if the legacy four-argument provider ABI breaks or a broad TypeError retry is introduced.",
    "Stop on any legacy no-hint regression, dialog or tool-pair loss, or trimmable result above its selected budget.",
    "Stop if implementation requires a dependency, download, network or provider call, path expansion or change to the 32000 automatic default.",
    "Leave MODEL_HINT_PROPAGATION_GATE unresolved if any focused, compatibility or AST contract check is red.",
]
SAR_06P_FORBIDDEN_ACTIONS = [
    "editing any allowed path before the four-file handoff",
    "model-specific metadata.estimated_tokens persistence",
    "breaking the four-argument provider ABI",
    "broad TypeError retry",
    "runtime tokenizer download",
    "provider token-count or network call",
    "global context-window increase",
    "unrelated refactor or path expansion",
    "push",
    "live action",
]


def _load() -> dict:
    return json.loads(ROADMAP_PATH.read_text(encoding="utf-8"))


def _assert_repo_relative(value: str) -> None:
    path = PurePosixPath(value)
    assert value
    assert not path.is_absolute()
    assert ".." not in path.parts
    assert ":" not in value
    assert "\\" not in value


def test_top_level_contract_and_master_link_are_frozen() -> None:
    roadmap = _load()

    assert REQUIRED_TOP_LEVEL <= roadmap.keys()
    assert roadmap["schema_version"] == 1
    assert roadmap["kind"] == "odysseus.system_assurance_runtime_hardening_roadmap"
    assert roadmap["roadmap_id"] == "SAR"
    assert roadmap["abc_mode"] == "Standard ABC"
    assert roadmap["status"] == (
        "implementation_in_progress_named_roadmap_registration_pending"
    )
    assert roadmap["master_roadmap"] == "docs/plans/system-optimization-master-roadmap.md"
    assert roadmap["implementation_run_state"] == (
        "docs/plans/system-assurance-runtime-hardening-run-state.json"
    )
    assert roadmap["queue_scope"]["generic_open_work_visibility"] == (
        "disabled_until_registration_handoff"
    )
    assert roadmap["queue_scope"]["registration_state"] == (
        "deferred_to_avoid_foreign_master_overlap"
    )

    master = MASTER_PATH.read_text(encoding="utf-8")
    assert "system-assurance-runtime-hardening-roadmap.json" in master
    assert "System Assurance And Runtime Hardening" in master


def test_every_verified_finding_has_one_non_conflicting_disposition() -> None:
    roadmap = _load()
    findings = roadmap["finding_dispositions"]
    finding_ids = [item["id"] for item in findings]
    slices = {item["id"] for item in roadmap["slice_queue"]}

    assert set(finding_ids) == EXPECTED_FINDING_IDS
    assert len(finding_ids) == len(set(finding_ids))

    allowed = set(roadmap["disposition_values"])
    for item in findings:
        assert item["disposition"] in allowed
        assert item["verdict"]
        assert item["required_outcome"]
        assert len(item["slice_ids"]) == len(set(item["slice_ids"]))
        assert set(item["slice_ids"]) <= slices

        if item["disposition"] in {"no_change", "existing_roadmap_owner"}:
            assert item["slice_ids"] == []
        else:
            assert item["slice_ids"]

        if item["disposition"] == "existing_roadmap_owner":
            assert item.get("roadmap_refs")
            for ref in item["roadmap_refs"]:
                _assert_repo_relative(ref)
                assert (ROOT / ref).exists(), ref


def test_slice_contract_is_complete_and_dormant_until_registration() -> None:
    roadmap = _load()
    declared_fields = set(roadmap["slice_contract_fields"])
    slices = roadmap["slice_queue"]
    slice_ids = [item["id"] for item in slices]
    gate_ids = {item["gate_id"] for item in roadmap["gate_queue"]}

    assert declared_fields == REQUIRED_SLICE_FIELDS
    assert len(slice_ids) == len(set(slice_ids))
    assert all(item_id.startswith("SAR-") for item_id in slice_ids)

    for item in slices:
        assert set(item) == REQUIRED_SLICE_FIELDS
        assert "status" not in item
        assert "class" not in item
        assert item["execution_status"] in {
            "done_in_this_artifact",
                "planned",
                "planned_waiting_hotfile_handoff",
                "in_progress_serial_claim_active",
                "blocked_environment",
            "partial_implemented_waiting_SAR-06P",
            "partial_implemented_focused_tested_waiting_full_suite_environment_and_foreign_regression",
            "implemented_focused_tested",
        }
        assert item["mutation_class"] in {"safe_offline", "repo_only"}
        assert item["priority"] in {"P0", "P1", "P2"}
        assert item["owner"] in {"Alice", "Bob", "Charlie"}
        assert item["objective"]
        assert item["deliverables"]
        assert item["tests"]
        assert item["evidence_required"]
        assert item["stop_rules"]
        assert item["forbidden_actions"]
        assert item["done_when"].startswith("PASS only if ")

        for path in item["allowed_paths"] + item["hotfiles"]:
            _assert_repo_relative(path)

        gate_id = item["gate"]["id"]
        assert gate_id == "none" or gate_id in gate_ids
        assert item["gate"]["safe_default"]


def test_dependency_dag_matches_slice_dependencies_and_is_acyclic() -> None:
    roadmap = _load()
    slices = {item["id"]: item for item in roadmap["slice_queue"]}
    dag = roadmap["dependency_dag"]
    nodes = set(dag["nodes"])
    edges = {tuple(edge) for edge in dag["edges"]}

    assert nodes == set(slices)
    expected_edges = {
        (dependency, item["id"])
        for item in slices.values()
        for dependency in item["depends_on"]
    }
    assert edges == expected_edges

    incoming = {node: 0 for node in nodes}
    outgoing = {node: set() for node in nodes}
    for source, target in edges:
        assert source in nodes
        assert target in nodes
        incoming[target] += 1
        outgoing[source].add(target)

    frontier = sorted(node for node, count in incoming.items() if count == 0)
    visited: list[str] = []
    while frontier:
        node = frontier.pop(0)
        visited.append(node)
        for target in sorted(outgoing[node]):
            incoming[target] -= 1
            if incoming[target] == 0:
                frontier.append(target)
                frontier.sort()

    assert set(visited) == nodes


def test_parallel_frontiers_cover_each_slice_once_without_path_overlap() -> None:
    roadmap = _load()
    slices = {item["id"]: item for item in roadmap["slice_queue"]}
    seen: list[str] = []

    for frontier in roadmap["dependency_dag"]["parallel_frontiers_after_registration"]:
        ids = frontier["slice_ids"]
        seen.extend(ids)
        for index, left_id in enumerate(ids):
            left_paths = set(slices[left_id]["allowed_paths"])
            for right_id in ids[index + 1 :]:
                right_paths = set(slices[right_id]["allowed_paths"])
                assert not left_paths & right_paths, (left_id, right_id)

    assert len(seen) == len(set(seen))
    assert set(seen) == set(slices)


def test_sar_06p_model_hint_hardlimit_contract_is_exact_and_completed() -> None:
    roadmap = _load()
    slices = {item["id"]: item for item in roadmap["slice_queue"]}
    sar_06 = slices["SAR-06-model-aware-token-estimation"]
    sar_06p = slices[SAR_06P_ID]

    finding = next(
        item
        for item in roadmap["finding_dispositions"]
        if item["id"] == "SAR-F10-token-estimator"
    )
    assert finding["slice_ids"] == [
        "SAR-06-model-aware-token-estimation",
        SAR_06P_ID,
    ]
    assert finding["required_outcome"] == (
        "Route model_hint through deterministic tokenizer adapters and every "
        "productive hardlimit callsite while retaining a conservative offline "
        "fallback and the exact model-neutral legacy allowlist."
    )

    assert sar_06["execution_status"] == "implemented_focused_tested"
    assert sar_06p["execution_status"] == "implemented_focused_tested"
    assert sar_06p["mutation_class"] == "repo_only"
    assert sar_06p["priority"] == "P0"
    assert sar_06p["owner"] == "Bob"
    assert sar_06p["depends_on"] == ["SAR-06-model-aware-token-estimation"]
    assert sar_06p["allowed_paths"] == (
        SAR_06P_PRODUCTION_PATHS + SAR_06P_TEST_PATHS
    )
    assert sar_06p["hotfiles"] == SAR_06P_HOTFILES

    allowlist_statement = (
        "AST no-hint allowlist contains exactly " + ", ".join(
            SAR_06P_NO_HINT_ALLOWLIST[:-1]
        ) + " and " + SAR_06P_NO_HINT_ALLOWLIST[-1]
    )
    assert allowlist_statement in sar_06p["deliverables"]
    assert sar_06p["evidence_required"] == SAR_06P_EVIDENCE
    assert sar_06p["done_when"] == SAR_06P_DONE_WHEN
    assert sar_06p["stop_rules"] == SAR_06P_STOP_RULES
    assert sar_06p["forbidden_actions"] == SAR_06P_FORBIDDEN_ACTIONS

    read_only_exceptions = {
        item.split("::", 1)[0] for item in SAR_06P_NO_HINT_ALLOWLIST
    }
    assert read_only_exceptions == {
        "core/session_serialization.py",
        "scripts/performance_baseline.py",
    }
    assert read_only_exceptions.isdisjoint(sar_06p["allowed_paths"])
    assert "src/model_context.py" not in sar_06p["allowed_paths"]

    focused_command, regression_command, diff_command = sar_06p["tests"]
    assert "--import-mode=importlib" in focused_command
    for path in SAR_06P_TEST_PATHS[:9]:
        assert path in focused_command
    for path in [
        "tests/test_context_compactor.py",
        "tests/test_context_dialog_preservation.py",
        "tests/test_compact_truncate_tool_call_args.py",
        "tests/test_context_compactor_nonstring.py",
        "tests/test_compaction_summary_failure.py",
        "tests/test_dynamic_context_budget.py",
        "tests/test_chat_helpers.py",
        "tests/test_kv_cache_invalidation_2927.py",
        "tests/test_review_regressions.py",
        "tests/test_history_compact_tool_calls.py",
        "tests/test_session_manager.py",
        "tests/test_context_orchestrator.py",
        "tests/test_context_orchestrator_boundaries.py",
        "tests/test_memory_provenance_context_orchestrator.py",
        "tests/test_plugin_system.py",
        "tests/test_delegate_tool.py",
        "tests/test_model_context.py",
        "tests/test_estimate_tokens_tool_calls.py",
        "tests/test_context_budget.py",
        "tests/test_token_budget.py",
        "tests/test_token_budget_model_aware.py",
        "plugins/obsidian/tests/test_context_provider_backend.py",
        "tests/test_performance_baseline.py",
    ]:
        assert path in regression_command
    assert diff_command.startswith("git diff --check -- ")
    for path in sar_06p["allowed_paths"]:
        assert path in diff_command

    gates = {item["gate_id"]: item for item in roadmap["gate_queue"]}
    gate = gates["SAR-MODEL-HINT-HOTFILE-HANDOFF"]
    assert sar_06p["gate"] == {
        "id": "SAR-MODEL-HINT-HOTFILE-HANDOFF",
        "safe_default": (
            "The explicit handoff was consumed by one serialized owner and the "
            "slice is focused- and compatibility-green. Keep it closed unless a "
            "dedicated regression claim reopens it."
        ),
    }
    assert gate["gate_class"] == "coordination"
    assert gate["state"] == "satisfied_explicit_serial_handoff_and_green_evidence"
    assert gate["blocks"] == []
    assert "user selected SAR-06P explicitly" in gate["decision_needed"]
    assert "one serialized owner" in gate["decision_needed"]
    assert "dedicated regression claim" in gate["safe_default"]

    dag = roadmap["dependency_dag"]
    edges = {tuple(edge) for edge in dag["edges"]}
    assert SAR_06P_ID in dag["nodes"]
    assert ("SAR-06-model-aware-token-estimation", SAR_06P_ID) in edges
    assert (SAR_06P_ID, "SAR-11-integration-closeout") in edges
    assert SAR_06P_ID in slices["SAR-11-integration-closeout"]["depends_on"]

    containing_frontiers = [
        item
        for item in dag["parallel_frontiers_after_registration"]
        if SAR_06P_ID in item["slice_ids"]
    ]
    assert len(containing_frontiers) == 1
    assert containing_frontiers[0]["frontier"] == 2
    assert "was executed serially" in containing_frontiers[0]["rule"]
    assert "explicit file-level handoff" in containing_frontiers[0]["rule"]


def test_implementation_status_resume_contract_skips_verified_slices() -> None:
    roadmap = _load()
    statuses = {
        item["id"]: item["execution_status"]
        for item in roadmap["slice_queue"]
    }

    assert statuses["SAR-01-binding-ci-and-publish-provenance"] == "blocked_environment"
    assert statuses["SAR-03-tailscale-cache-expiry"] == (
        "implemented_focused_tested"
    )
    assert statuses["SAR-04-runtime-topology-contract"] == (
        "implemented_focused_tested"
    )
    assert statuses["SAR-05-background-job-transaction-boundary"] == (
        "implemented_focused_tested"
    )
    assert statuses["SAR-06-model-aware-token-estimation"] == "implemented_focused_tested"
    assert statuses[SAR_06P_ID] == "implemented_focused_tested"
    assert statuses["SAR-07-response-cache-lru-ttl"] == "implemented_focused_tested"
    assert statuses["SAR-08-canonical-rag-import"] == (
        "implemented_focused_tested"
    )
    assert statuses["SAR-09-measured-optimization-decisions"] == (
        "implemented_focused_tested"
    )

    next_step = roadmap["recommended_next_step"]
    assert "Resume from docs/plans/system-assurance-runtime-hardening-run-state.json" in next_step
    assert "never rerun completed SAR-03, SAR-08 or SAR-09" in next_step
    assert "SAR-01 parked as blocked_environment" in next_step
    assert "SAR-06P, SAR-04, SAR-05 and SAR-07 are focused-green" in next_step
    assert "IP-SAR-SERIAL-CLOSEOUT consumed its single allowed" in next_step
    assert "255 tests passed, 2 skipped" in next_step
    assert "REG-20260714-002" in next_step
    assert "hand off serially to TLR-01" in next_step
    assert "IP-SAR-SERIAL-CLOSEOUT" in next_step
    assert "docs/plans/regression-queue.json" in next_step


def test_sar04_names_the_real_shipped_launcher_inventory() -> None:
    roadmap = _load()
    slices = {item["id"]: item for item in roadmap["slice_queue"]}
    sar04 = slices["SAR-04-runtime-topology-contract"]

    shipped_launchers = {
        "Dockerfile",
        "launch-windows.ps1",
        "run-server-windows.ps1",
        "launcher.py",
        "build-macos-app.sh",
        "start-macos.sh",
        "odysseus-ui.service",
    }
    assert shipped_launchers <= set(sar04["allowed_paths"])
    assert "ops/homeserver/odysseus-ui.service" not in sar04["allowed_paths"]
    assert all((ROOT / path).is_file() for path in shipped_launchers)
    diff_check = next(command for command in sar04["tests"] if command.startswith("git diff --check"))
    assert all(path in diff_check for path in shipped_launchers)


def test_full_suite_policy_routes_independent_failures_to_regression_queue() -> None:
    roadmap = _load()
    integration = roadmap["verification"]["full_suite_integration_point"]
    queue = json.loads(REGRESSION_QUEUE_PATH.read_text(encoding="utf-8"))

    assert integration == {
        "id": "IP-SAR-SERIAL-CLOSEOUT",
        "after": [
            "SAR-06P-model-hint-hardlimit-propagation",
            "SAR-04-runtime-topology-contract",
            "SAR-05-background-job-transaction-boundary",
            "SAR-07-response-cache-lru-ttl",
        ],
        "maximum_runs": 1,
        "regression_queue": "docs/plans/regression-queue.json",
        "routing_rule": (
            "A failure attributable to one of the four slices blocks that slice. "
            "An independent or not-yet-attributed failure is queued and does not "
            "expand SAR automatically."
        ),
    }
    assert queue["kind"] == "odysseus.independent_regression_queue"
    assert queue["deduplication_key"]
    assert [item["id"] for item in queue["suite_policy"]["integration_points"]] == [
        "IP-SAR-SERIAL-CLOSEOUT",
        "IP-TLR-06-CLOSEOUT",
        "IP-PLANNING-INTEGRATION-CLOSEOUT",
    ]
    assert queue["items"][0]["id"] == "REG-20260714-001"
    assert queue["items"][0]["state"] == "queued"
    assert queue["items"][0]["source_probe_id"] == (
        "SAR-01-FULL-SUITE-DIAGNOSTIC-45"
    )
    assert [item["id"] for item in queue["items"]] == [
        "REG-20260714-001",
        "REG-20260714-002",
    ]
    environment_item = queue["items"][1]
    assert environment_item["state"] == "blocked_environment"
    assert environment_item["source_probe_id"] == "IP-SAR-SERIAL-CLOSEOUT"
    assert environment_item["test_node"] == (
        "tests/test_agent_migration_manifest.py::"
        "test_collect_skill_dir_skips_symlinked_skill_markdown"
    )


def test_gates_and_long_run_authority_are_unambiguous() -> None:
    roadmap = _load()
    gates = roadmap["gate_queue"]
    gate_ids = [item["gate_id"] for item in gates]

    assert len(gate_ids) == len(set(gate_ids))
    assert {
        "SAR-REGISTRATION-HANDOFF",
        "SAR-RELEASE-EVIDENCE-CONTRACT-READY",
        "SAR-GITHUB-REMOTE-GO",
        "SAR-01-FULL-SUITE-ENVIRONMENT",
        "SAR-EXTERNAL-RELEASE-EVIDENCE-GO",
        "SAR-MULTIWORKER-GO",
        "SAR-HTTP2-BENCHMARK-GO",
        "SAR-TEMPORAL-LONG-RUN-EVIDENCE",
        "SAR-RAG-HOTFILE-HANDOFF",
        "SAR-LLM-HOTFILE-HANDOFF",
        "SAR-MODEL-HINT-HOTFILE-HANDOFF",
        "SAR-FULL-SUITE-FOREIGN-LLM-API-DRIFT",
        "SAR-FULL-SUITE-ARCHIVED-SESSION-PATCHPOINT-DRIFT",
    } == set(gate_ids)
    for gate in gates:
        assert gate["gate_class"]
        assert gate["state"]
        if gate["state"].startswith("satisfied"):
            assert gate["blocks"] == []
        else:
            assert gate["blocks"]
        assert gate["decision_needed"]
        assert gate["safe_default"]

    boundary = roadmap["long_run_boundary"]
    assert boundary["sole_runtime_owner"] == (
        "docs/plans/temporal-light-agent-execution-roadmap.json"
    )
    assert "TLR-09" in boundary["completion_rule"]
    assert "TLR-10" in boundary["completion_rule"]
    assert "TLR-11" in boundary["completion_rule"]
    assert any("never use bg_jobs" in item["gate"]["safe_default"] for item in roadmap["slice_queue"])


def test_roadmap_persists_no_private_host_path_or_raw_queue_activation() -> None:
    roadmap = _load()
    serialized = json.dumps(roadmap, sort_keys=True)

    assert "C:/Users/" not in serialized
    assert "C:\\\\Users\\\\" not in serialized
    assert "/home/" not in serialized
    assert "/Users/" not in serialized
    assert '"class": "repo_only"' not in serialized
    assert '"status": "planned"' not in serialized
