"""Tests for Harbour Modular Monolith architecture (Stufe 1 & 2) and MCP-First primary interface."""

from __future__ import annotations

import importlib
import pytest


def test_harbour_subpackages_importable():
    """All 6 src.harbour.* bounded context subpackages and src.contrib must be importable."""
    packages = [
        "src.harbour",
        "src.harbour.core",
        "src.harbour.memory",
        "src.harbour.gateway",
        "src.harbour.inbox",
        "src.harbour.adapters",
        "src.harbour.mcp",
        "src.contrib",
    ]
    for pkg in packages:
        mod = importlib.import_module(pkg)
        assert mod is not None, f"Failed to import {pkg}"


def test_harbour_architecture_manifest():
    """Architecture manifest must accurately report schema, primary interface MCP, and frozen UI H1R-14."""
    from src.harbour import (
        HARBOUR_ARCHITECTURE_SCHEMA,
        PRIMARY_INTERFACE,
        UI_STATUS,
        BOUNDED_CONTEXTS,
        HARBOUR_LAYERS,
        get_harbour_architecture_manifest,
    )

    assert HARBOUR_ARCHITECTURE_SCHEMA == "odysseus.harbour.modular_monolith.v1"
    assert PRIMARY_INTERFACE == "mcp"
    assert UI_STATUS == "deferred_frozen_h1r14"

    manifest = get_harbour_architecture_manifest()
    assert manifest["schema"] == "odysseus.harbour.modular_monolith.v1"
    assert manifest["primary_interface"] == "mcp"
    assert manifest["ui_status"] == "deferred_frozen_h1r14"

    # Verify all 6 bounded contexts exist in manifest
    expected_contexts = {"core", "memory", "inbox", "mcp", "gateway", "adapters"}
    assert set(manifest["bounded_contexts"].keys()) == expected_contexts

    # Verify layers structure
    expected_layers = {
        "core_platform",
        "primary_interface_mcp",
        "execution_runtimes",
        "periphery_adapters",
        "quarantined_contrib",
    }
    assert set(manifest["layers"].keys()) == expected_layers
    assert manifest["layers"]["core_platform"] == ["core", "memory", "inbox"]
    assert manifest["layers"]["primary_interface_mcp"] == ["mcp"]
    assert manifest["layers"]["execution_runtimes"] == ["gateway"]
    assert manifest["layers"]["periphery_adapters"] == ["adapters"]
    assert manifest["layers"]["quarantined_contrib"] == ["contrib"]


def test_core_symbols_identity():
    """Core package re-exports must be identical to original implementations."""
    import src.harbour.core as core_pkg
    import src.planning_revision_store as orig_planning
    import src.harbor_planning_projection as orig_projection
    import src.execution_authority_snapshot as orig_snapshot
    import src.execution_authority_ledger as orig_ledger
    import src.agent_verification_receipt as orig_receipt

    assert core_pkg.PlanningRevisionStore is orig_planning.PlanningRevisionStore
    assert core_pkg.build_harbor_planning_projection is orig_projection.build_harbor_planning_projection
    assert core_pkg.HarborPlanningProjection is orig_projection.HarborPlanningProjection
    assert core_pkg.build_execution_authority_snapshot is orig_snapshot.build_execution_authority_snapshot
    assert core_pkg.build_execution_authority_ledger is orig_ledger.build_execution_authority_ledger
    assert core_pkg.ExecutionAuthorityLedger is orig_ledger.build_execution_authority_ledger
    assert core_pkg.build_verification_receipt is orig_receipt.build_verification_receipt
    assert core_pkg.AgentVerificationReceipt is orig_receipt.build_verification_receipt
    assert core_pkg.validate_verification_receipt is orig_receipt.validate_verification_receipt


def test_memory_symbols_identity():
    """Memory package re-exports must be identical to original implementations."""
    import src.harbour.memory as memory_pkg
    import src.unified_source_index_runtime as orig_runtime
    import src.unified_source_index_job_runner as orig_runner
    import src.unified_source_index_runtime_status as orig_status
    import src.unified_source_index_source_registry as orig_registry
    import src.unified_source_index_sources.planning as orig_src_planning
    import src.unified_source_index_sources.planning_markdown as orig_src_md
    import src.unified_source_index_sources.planning_composite as orig_src_composite
    import src.context_manifest_producer as orig_context

    assert memory_pkg.KnowledgeRuntime is orig_runtime.KnowledgeRuntime
    assert memory_pkg.CooperativeIndexJobPump is orig_runner.CooperativeIndexJobPump
    assert memory_pkg.project_unified_source_index_runtime_status is orig_status.project_unified_source_index_runtime_status
    assert memory_pkg.SourceAdapterRegistry is orig_registry.SourceAdapterRegistry
    assert memory_pkg.PlanningSourceAdapter is orig_src_planning.PlanningSourceAdapter
    assert memory_pkg.PlanningMarkdownSourceAdapter is orig_src_md.PlanningMarkdownSourceAdapter
    assert memory_pkg.PlanningCompositeSourceAdapter is orig_src_composite.PlanningCompositeSourceAdapter
    assert memory_pkg.CombinedContextManifestProducer is orig_context.CombinedContextManifestProducer


def test_gateway_symbols_and_execution_policy():
    """Gateway package re-exports must match originals and enforce sandboxed, lease-based policy."""
    import src.harbour.gateway as gateway_pkg
    import src.coding_agent_backend as orig_backend
    import src.coding_agent_runner_state as orig_runner_state
    import src.coding_agent_sandbox_bridge as orig_bridge
    import src.headless_write_agent_state as orig_headless

    assert gateway_pkg.CodingAgentBackend is orig_backend
    assert gateway_pkg.CodingAgentRunnerState is orig_runner_state.CodingRunnerState
    assert gateway_pkg.CodingAgentSandboxBridge is orig_bridge
    assert gateway_pkg.HeadlessWriteAgentState is orig_headless.HeadlessWriteAgentStateStore

    policy = gateway_pkg.WORKER_EXECUTION_POLICY
    assert policy["schema"] == "odysseus.harbour.worker_execution_policy.v1"
    assert policy["isolation_mode"] == "git_worktree_sandbox_pod"
    assert policy["lease_required"] is True
    assert policy["direct_main_process_execution_allowed"] is False
    assert policy["sandbox_dispatch_required"] is True
    assert policy["worktree_isolation_required"] is True


def test_inbox_symbols_identity():
    """Inbox package re-exports must be identical to original implementations."""
    import src.harbour.inbox as inbox_pkg
    import src.harbour_inbox_service as orig_inbox
    import src.document_subject_scope as orig_scope

    assert inbox_pkg.HarbourInboxService is orig_inbox.HarbourInboxService
    assert inbox_pkg.capture_document_request_authority is orig_scope.capture_document_request_authority
    assert inbox_pkg.guard_document_request_authority is orig_scope.guard_document_request_authority
    assert inbox_pkg.stamp_new_document is orig_scope.stamp_new_document


def test_adapters_manifest_and_symbols():
    """Adapters package re-exports must match originals and provide periphery manifest."""
    import src.harbour.adapters as adapters_pkg
    import src.nextcloud_knowledge_ingestion as orig_nextcloud
    import src.forge_context_reader as orig_forge

    assert adapters_pkg.NextcloudKnowledgeIngestionError is orig_nextcloud.NextcloudKnowledgeIngestionError
    assert adapters_pkg.NextcloudKnowledgeIngestionResult is orig_nextcloud.NextcloudKnowledgeIngestionResult
    assert adapters_pkg.ForgeContextReaderError is orig_forge.ForgeContextReaderError
    assert adapters_pkg.RetainedForgeVersion is orig_forge.RetainedForgeVersion
    assert adapters_pkg.ImmutableForgeContextReader is orig_forge.ImmutableForgeContextReader

    manifest = adapters_pkg.get_periphery_adapters_manifest()
    assert manifest["schema"] == "odysseus.harbour.adapter_metadata.v1"
    assert set(manifest["adapters"].keys()) == {"telegram", "nextcloud", "forge", "email_caldav"}
    assert manifest["adapters"]["telegram"]["capabilities"] == ["chat_streaming", "command_dispatch", "pc_control", "inbox_bridge"]
    assert manifest["adapters"]["forge"]["enabled_by_default"] is True


def test_mcp_first_surface_manifest_and_symbols():
    """MCP package re-exports must match originals and provide full MCP-First surface manifest."""
    import src.harbour.mcp as mcp_pkg
    import src.mcp_context_app as orig_mcp_app
    import src.mcp_context_authority as orig_mcp_auth
    import src.mcp_private_context_confirmation as orig_mcp_conf
    import src.planning_mcp_service as orig_mcp_planning

    assert mcp_pkg.McpContextApp is orig_mcp_app.McpContextApp
    assert mcp_pkg.install_mcp_context_app is orig_mcp_app.install_mcp_context_app
    assert mcp_pkg.McpContextAuthority is orig_mcp_auth.McpContextAuthority
    assert mcp_pkg.McpPrivateContextConfirmationService is orig_mcp_conf.McpPrivateContextConfirmationService
    assert mcp_pkg.PlanningMcpService is orig_mcp_planning.PlanningMcpService

    import src.harbour.mcp.dispatcher as orig_dispatcher
    assert mcp_pkg.HarbourMcpDispatcher is orig_dispatcher.HarbourMcpDispatcher
    assert mcp_pkg.HarbourMcpDispatchError is orig_dispatcher.HarbourMcpDispatchError
    assert mcp_pkg.build_harbour_mcp_tool_contracts is orig_dispatcher.build_harbour_mcp_tool_contracts

    manifest = mcp_pkg.get_mcp_first_surface_manifest()
    assert manifest["schema"] == "odysseus.harbour.mcp_first_surface.v1"
    assert manifest["primary_interface"] == "mcp"
    assert manifest["ui_status"] == "deferred_frozen_h1r14"

    # 4 canonical namespaces
    expected_namespaces = [
        "harbour_planning",
        "harbour_memory",
        "harbour_gateway",
        "harbour_inbox",
    ]
    assert manifest["namespaces"] == expected_namespaces

    # Scopes
    assert "mcp:planning:read" in manifest["scopes"]
    assert "mcp:harbour:context:read" in manifest["scopes"]
    assert "mcp:harbour:gateway" in manifest["scopes"]
    assert "mcp:harbour:inbox" in manifest["scopes"]

    # Tool definitions
    tools = manifest["tools"]
    for ns in expected_namespaces:
        matching_tools = [name for name, t in tools.items() if t["namespace"] == ns]
        assert len(matching_tools) >= 3, f"Namespace {ns} must have at least 3 tools"
        for tool_name in matching_tools:
            tool_meta = tools[tool_name]
            assert "description" in tool_meta
            assert "bound_service" in tool_meta
            assert "scope" in tool_meta
            assert "input_schema" in tool_meta


def test_contrib_quarantine_controls():
    """Contrib package must quarantine niche features and toggle via env controls."""
    from src.contrib import (
        CONTRIB_QUARANTINE_SCHEMA,
        QUARANTINED_FEATURES,
        QUARANTINED_FEATURE_IDS,
        is_contrib_enabled,
        get_quarantine_manifest,
    )

    assert CONTRIB_QUARANTINE_SCHEMA == "odysseus.contrib.quarantine_manifest.v1"
    assert len(QUARANTINED_FEATURES) >= 5

    expected_quarantined_ids = {
        "local_model_serving_cookbook",
        "image_tools_background_removal_worker",
        "experimental_tts_stt_protocols",
        "frontpage_v2_window_manager",
        "harbor_one_web_ui_h1r14",
    }
    assert expected_quarantined_ids.issubset(set(QUARANTINED_FEATURE_IDS))

    # Test is_contrib_enabled logic
    # Default (empty env) -> True
    assert is_contrib_enabled({}) is True

    # ODYSSEUS_MCP_FIRST_MODE=1 -> False (Scope-Diät)
    assert is_contrib_enabled({"ODYSSEUS_MCP_FIRST_MODE": "1"}) is False
    assert is_contrib_enabled({"ODYSSEUS_MCP_FIRST_MODE": "true"}) is False

    # Explicit override with ODYSSEUS_ENABLE_CONTRIB=1
    assert is_contrib_enabled({"ODYSSEUS_MCP_FIRST_MODE": "1", "ODYSSEUS_ENABLE_CONTRIB": "1"}) is True

    # Explicit disable
    assert is_contrib_enabled({"ODYSSEUS_ENABLE_CONTRIB": "0"}) is False
    assert is_contrib_enabled({"ODYSSEUS_ENABLE_CONTRIB": "false"}) is False

    # Test get_quarantine_manifest
    manifest_default = get_quarantine_manifest({})
    assert manifest_default["schema"] == CONTRIB_QUARANTINE_SCHEMA
    assert manifest_default["mcp_first_mode_enforced"] is False
    assert manifest_default["contrib_enabled"] is True
    assert manifest_default["quarantined_feature_count"] == len(QUARANTINED_FEATURES)

    manifest_mcp = get_quarantine_manifest({"ODYSSEUS_MCP_FIRST_MODE": "1"})
    assert manifest_mcp["mcp_first_mode_enforced"] is True
    assert manifest_mcp["contrib_enabled"] is False


@pytest.mark.asyncio
async def test_lifecycle_supervision_contract():
    """Lifecycle supervision module provides task supervision, bounded cancellation, and re-exports on app."""
    import asyncio
    from pathlib import Path
    import ast
    import src.harbour.lifecycle_supervision as supervision

    assert supervision._STARTUP_TASK_SHUTDOWN_TIMEOUT_SECONDS == 2.0

    # Verify app.py re-exports all lifecycle supervision functions
    app_source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    app_ast = ast.parse(app_source)
    imported_names = set()
    for node in ast.walk(app_ast):
        if isinstance(node, ast.ImportFrom) and node.module == "src.harbour.lifecycle_supervision":
            for alias in node.names:
                imported_names.add(alias.asname or alias.name)

    expected_reexports = {
        "_shutdown_registered_startup_tasks",
        "_stop_dedicated_startup_task",
        "_await_recent_changes_snapshot_shutdown",
        "_observe_finished_startup_tasks",
        "_retain_registered_startup_task",
    }
    assert expected_reexports.issubset(imported_names)

    # Test observe_finished_startup_tasks
    async def ok_task():
        return 42

    async def fail_task():
        raise ValueError("simulated_failure")

    t_ok = asyncio.create_task(ok_task())
    t_fail = asyncio.create_task(fail_task())
    await asyncio.gather(t_ok, return_exceptions=True)
    await asyncio.gather(t_fail, return_exceptions=True)

    assert supervision.observe_finished_startup_tasks((t_ok,)) == 0
    assert supervision.observe_finished_startup_tasks((t_fail,)) == 1
    assert supervision.observe_finished_startup_tasks((t_ok, t_fail)) == 1

    # Test retain_registered_startup_task on dummy state
    class DummyState:
        def __init__(self):
            self._startup_tasks = []

    state = DummyState()
    supervision.retain_registered_startup_task(t_ok, app_state=state)
    assert state._startup_tasks == [t_ok]
    supervision.retain_registered_startup_task(t_ok, app_state=state)
    assert len(state._startup_tasks) == 1

    # Test stop_dedicated_startup_task
    async def lingering_task():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            pass

    t_lingering = asyncio.create_task(lingering_task())
    state._startup_tasks.append(t_lingering)
    stopped = await supervision.stop_dedicated_startup_task(t_lingering, label="test-task", app_state=state)
    assert stopped is True
    assert t_lingering.done()
    assert t_lingering not in state._startup_tasks


@pytest.mark.asyncio
async def test_maintenance_tasks_contract():
    """Maintenance tasks module exports required warm-up procedures and background loops."""
    import asyncio
    from src.harbour import maintenance_tasks

    expected_symbols = [
        "warmup_tool_index",
        "warmup_endpoints",
        "keepalive_loop",
        "ensure_default_tasks",
        "backfill_legacy_skill_owners",
        "null_owner_sweep_loop",
        "skill_audit_nightly_loop",
        "vault_trash_purge_loop",
        "schedule_background_maintenance_tasks",
    ]
    for sym in expected_symbols:
        assert hasattr(maintenance_tasks, sym), f"Missing symbol {sym}"

    # Test scheduling background maintenance tasks offline
    tasks_offline: list[asyncio.Task] = []
    maintenance_tasks.schedule_background_maintenance_tasks(
        tasks_offline,
        startup_offline=True,
    )
    # Offline should only schedule 3 tasks: null_owner_sweep, skill_audit, vault_trash_purge
    assert len(tasks_offline) == 3
    for t in tasks_offline:
        t.cancel()
    await asyncio.gather(*tasks_offline, return_exceptions=True)

    # Test scheduling background maintenance tasks online
    tasks_online: list[asyncio.Task] = []
    maintenance_tasks.schedule_background_maintenance_tasks(
        tasks_online,
        startup_offline=False,
    )
    # Online should schedule 6 tasks (3 warmup/keepalive + 3 periodic loops)
    assert len(tasks_online) == 6
    for t in tasks_online:
        t.cancel()
    await asyncio.gather(*tasks_online, return_exceptions=True)

    # Test backfill with None skills_manager
    maintenance_tasks.backfill_legacy_skill_owners(None)
