#!/usr/bin/env python3
"""Build and verify the content-free USI reconciliation inventory."""

from __future__ import annotations

import argparse
import ast
from collections import Counter
from dataclasses import asdict, dataclass
import json
from pathlib import Path, PurePosixPath
import sys
from typing import Iterable, Sequence


SCHEMA_VERSION = 1
BASELINE_DATE = "2026-07-13"
INVENTORY_KIND = "odysseus.unified_source_index_runtime_inventory"
CLASSIFICATIONS = frozenset(
    {"domain_truth", "index_truth", "projection", "observation", "legacy"}
)
WRITER_POLICIES = frozenset({"required", "read_only", "contract_only", "derived_in_memory"})


@dataclass(frozen=True, slots=True)
class Component:
    component_id: str
    name: str
    store_kind: str
    classification: str
    canonical_owner: str
    source_paths: tuple[str, ...]
    writer_policy: str
    migration_action: str


@dataclass(frozen=True, slots=True)
class DirectWriter:
    component_id: str
    path: str
    symbol: str
    operation: str


@dataclass(frozen=True, slots=True)
class ToolIdentity:
    tool_id: str
    canonical_owner: str
    state: str
    required_surfaces: tuple[tuple[str, str], ...]
    intent: str


@dataclass(frozen=True, slots=True)
class Hotfile:
    path: str
    owner_track: str
    concern: str


@dataclass(frozen=True, slots=True)
class NonStoreBoundary:
    boundary_id: str
    paths: tuple[str, ...]
    reason: str
    usi_rule: str


@dataclass(frozen=True, slots=True)
class MigrationRisk:
    risk_id: str
    owner_track: str
    paths: tuple[str, ...]
    finding: str
    required_resolution: str


COMPONENTS: tuple[Component, ...] = (
    Component("agent_run_events", "Agent run event ledger", "job_store", "observation", "Agent Runtime", ("src/agent_run_ledger.py",), "required", "exclude_from_usi_job_truth"),
    Component("agent_task_events", "Agent task event ledger", "job_store", "observation", "Agent Runtime", ("src/agent_task_ledger.py",), "required", "exclude_from_usi_job_truth"),
    Component("ai_activity_ledger", "AI activity ledger", "observation_store", "observation", "AI Activity", ("src/ai_activity_ledger.py",), "required", "retain_as_content_free_observation"),
    Component("ai_lens_projection", "AI Lens in-memory observations and projections", "runtime_projection", "observation", "AI Lens", ("src/ai_lens_service.py", "src/ai_lens_events.py", "src/ai_lens_projection.py", "src/ai_lens_graph.py"), "required", "consume_usi_refs_without_becoming_truth"),
    Component("ai_lens_replay", "AI Lens replay snapshots", "observation_store", "observation", "AI Lens Replay", ("src/ai_lens_replay.py",), "required", "retain_as_bounded_replay_artifact"),
    Component("background_shell_jobs", "Detached shell job registry", "job_store", "legacy", "Chat Background Jobs", ("src/bg_jobs.py",), "required", "exclude_from_usi_job_truth"),
    Component("bigdata_checkpoint_ledger", "Nextcloud and big-data checkpoint ledger", "job_store", "index_truth", "AppendOnlyBigDataLedger and source providers", ("src/bigdata_ledger_contract.py",), "required", "adapt_as_ingestion_checkpoint_evidence"),
    Component("git_repository", "Git repository and commit facts", "source_store", "domain_truth", "Git Adapter and Local Forge", ("src/repo_git_adapter.py",), "read_only", "reference_read_only"),
    Component("memory_file", "Approved personal memory records", "source_store", "domain_truth", "MemoryManager and memory lifecycle", ("src/memory.py",), "required", "adapt_read_only"),
    Component("memory_legacy_text", "Legacy personal memory text file", "source_store", "legacy", "MemoryManager legacy importer", ("src/memory.py",), "read_only", "import_with_owner_review_then_retire"),
    Component("memory_provenance", "Memory provenance ledger", "lineage_store", "observation", "Memory Lifecycle", ("src/memory_provenance_ledger.py",), "required", "map_as_evidence_not_source_truth"),
    Component("memory_vector", "Personal memory semantic index", "vector_store", "projection", "MemoryVectorStore", ("src/memory_vector.py",), "required", "retain_until_usi_projection_parity"),
    Component("memory_vector_legacy", "Unsuffixed personal memory Chroma collection", "vector_store", "legacy", "Embedding lane migration", ("src/embedding_lanes.py", "src/memory_vector.py"), "required", "verify_backfill_then_retire"),
    Component("orca_derived_index", "ORCA derived chunk index", "chunk_store", "legacy", "ORCA Vault", ("plugins/obsidian/backend/derived_index.py",), "required", "retire_after_usi_query_parity"),
    Component("orca_memory_ledger", "ORCA discovery and indexing ledger", "job_store", "index_truth", "ORCA Vault", ("plugins/obsidian/backend/memory_ledger.py",), "required", "map_checkpoint_then_retire_duplicate_state"),
    Component("orca_manual_relationships", "ORCA manual relationships", "graph_store", "domain_truth", "ORCA Vault Model", ("plugins/obsidian/backend/vault_model.py",), "required", "adapt_as_evidence_bound_relations"),
    Component("orca_query_cache", "ORCA query result cache", "query_cache_store", "legacy", "ORCA Query Layer", ("plugins/obsidian/backend/query_layer.py",), "required", "replace_with_bounded_usi_cache"),
    Component("orca_raptor_artifacts", "ORCA RAPTOR artifacts", "graph_store", "projection", "ORCA RAPTOR", ("plugins/obsidian/backend/raptor_rebuild.py",), "required", "bind_to_immutable_usi_derived_runs"),
    Component("orca_vault", "Owner-scoped vault files", "source_store", "domain_truth", "ORCA Vault and vault policy", ("plugins/obsidian/backend/vault_service.py",), "required", "adapt_read_only"),
    Component("orca_vector_cache", "ORCA whole-note embedding cache", "vector_store", "projection", "ORCA Vault Model", ("plugins/obsidian/backend/vault_model.py",), "required", "drop_and_rebuild_from_usi_sources"),
    Component("personal_docs_keyword", "Personal Docs keyword index", "chunk_store", "legacy", "PersonalDocsManager", ("src/personal_docs.py",), "required", "retire_after_usi_lexical_parity"),
    Component("personal_docs_registry", "Personal Docs source registry", "source_store", "domain_truth", "PersonalDocsManager", ("src/personal_docs.py",), "required", "migrate_to_owner_scoped_registration"),
    Component("personal_docs_sources", "Registered Personal Docs files", "source_store", "domain_truth", "Filesystem owners through PersonalDocsManager", ("src/personal_docs.py",), "read_only", "register_external_sources_without_implicit_content_moves"),
    Component("planning_memory_projection", "Planning-to-Memory projection", "chunk_store", "projection", "Planning and Memory Lifecycle", ("src/planning_source_memory.py",), "required", "retain_only_when_projection_adds_value"),
    Component("planning_sources", "Planning roadmap and project sources", "source_store", "domain_truth", "Planning stores and Planning MCP", ("src/planning_source_inventory.py", "src/planning_mcp_service.py"), "read_only", "adapt_read_only"),
    Component("project_versions", "Immutable project version manifests", "lineage_store", "domain_truth", "ProjectVersionStore and Local Forge", ("src/project_version_store.py",), "required", "reference_read_only_from_usi"),
    Component("rag_chroma", "Personal document Chroma collections", "vector_store", "projection", "VectorRAG", ("src/rag_vector.py",), "required", "retain_as_rebuildable_usi_projection"),
    Component("rag_chroma_legacy", "Unsuffixed personal document Chroma collection", "vector_store", "legacy", "Embedding lane migration", ("src/embedding_lanes.py", "src/rag_vector.py"), "required", "verify_backfill_then_retire"),
    Component("repo_registry", "Registered repository identities", "source_store", "domain_truth", "RepoRegistry", ("src/repo_registry.py",), "required", "reference_read_only_from_usi"),
    Component("research_results", "Owner-scoped research result files", "source_store", "domain_truth", "ResearchStorageMixin", ("src/research_handler_storage.py", "src/task_scheduler.py", "routes/research_routes.py"), "required", "adapt_owner_approved_artifacts_and_consolidate_writers"),
    Component("research_runtime_jobs", "Process-local research execution state", "job_store", "legacy", "ResearchHandler", ("src/research_handler.py", "src/research_handler_storage.py"), "required", "keep_outside_index_job_truth_and_add_restart_safe_owner"),
    Component("sandbox_job_events", "Sandbox job event ledger", "job_store", "observation", "Sandbox Runtime", ("src/sandbox_job_ledger.py",), "required", "exclude_from_usi_job_truth"),
    Component("scheduled_task_definitions", "Recurring and event task definitions", "job_store", "domain_truth", "ScheduledTask ORM and task routes", ("src/task_scheduler.py", "routes/task_routes.py"), "required", "exclude_from_usi_job_truth"),
    Component("scheduled_task_runs", "Durable scheduled task attempts", "job_store", "domain_truth", "TaskRun ORM and TaskScheduler", ("src/task_scheduler.py",), "required", "exclude_from_usi_index_job_truth"),
    Component("universal_inbox_raptorgraph_events", "Universal Inbox RaptorGraph mutation events", "graph_store", "observation", "Universal Inbox", ("src/universal_inbox_raptorgraph_store.py",), "required", "map_as_projection_evidence_not_graph_truth"),
)


DIRECT_WRITERS: tuple[DirectWriter, ...] = (
    DirectWriter("agent_run_events", "src/agent_run_ledger.py", "append_event", "append redacted run event"),
    DirectWriter("agent_run_events", "src/agent_run_ledger.py", "clear_events", "clear one run ledger"),
    DirectWriter("agent_task_events", "src/agent_task_ledger.py", "append_task_record", "append redacted task event"),
    DirectWriter("ai_activity_ledger", "src/ai_activity_ledger.py", "append_ai_activity", "append content-free activity record"),
    DirectWriter("ai_lens_projection", "src/ai_lens_service.py", "AiLensService.ingest", "append bounded in-memory observation"),
    DirectWriter("ai_lens_projection", "src/ai_lens_service.py", "AiLensService.ingest_batch", "append bounded observation batch"),
    DirectWriter("ai_lens_projection", "src/ai_lens_service.py", "AiLensService.clear_session", "clear in-memory observation session"),
    DirectWriter("ai_lens_projection", "src/ai_lens_service.py", "AiLensEventEmitter.emit", "emit bounded observation into service"),
    DirectWriter("ai_lens_replay", "src/ai_lens_replay.py", "AiLensReplayStore.persist", "persist bounded replay snapshot"),
    DirectWriter("ai_lens_replay", "src/ai_lens_replay.py", "AiLensReplayStore.delete_expired", "delete expired replay snapshots"),
    DirectWriter("background_shell_jobs", "src/bg_jobs.py", "_save", "persist detached job registry"),
    DirectWriter("background_shell_jobs", "src/bg_jobs.py", "launch", "create detached shell job"),
    DirectWriter("background_shell_jobs", "src/bg_jobs.py", "refresh", "reconcile detached job state"),
    DirectWriter("background_shell_jobs", "src/bg_jobs.py", "mark_followed_up", "mark follow-up consumed"),
    DirectWriter("background_shell_jobs", "src/bg_jobs.py", "kill", "terminate and persist detached job state"),
    DirectWriter("bigdata_checkpoint_ledger", "src/bigdata_ledger_contract.py", "AppendOnlyBigDataLedger.append_intent", "append checkpoint intent"),
    DirectWriter("bigdata_checkpoint_ledger", "src/bigdata_ledger_contract.py", "AppendOnlyBigDataLedger.append_commit", "append checkpoint commit"),
    DirectWriter("bigdata_checkpoint_ledger", "src/bigdata_ledger_contract.py", "AppendOnlyBigDataLedger.append_record", "append checkpoint transition"),
    DirectWriter("memory_file", "src/memory.py", "MemoryManager.save", "persist approved memory records"),
    DirectWriter("memory_file", "src/memory.py", "MemoryManager.ensure_file_exists", "initialize memory truth file"),
    DirectWriter("memory_file", "src/memory.py", "MemoryManager.add_entry", "add approved memory record"),
    DirectWriter("memory_file", "src/memory.py", "MemoryManager.claim_ownerless", "assign legacy ownership"),
    DirectWriter("memory_file", "src/memory.py", "MemoryManager.increment_uses", "update use counters"),
    DirectWriter("memory_provenance", "src/memory_provenance_ledger.py", "record_memory_provenance", "append provenance event"),
    DirectWriter("memory_vector", "src/memory_vector.py", "MemoryVectorStore.add", "upsert vector projection"),
    DirectWriter("memory_vector", "src/memory_vector.py", "MemoryVectorStore.remove", "delete vector projection"),
    DirectWriter("memory_vector", "src/memory_vector.py", "MemoryVectorStore.rebuild", "rebuild vector projection"),
    DirectWriter("memory_vector_legacy", "src/embedding_lanes.py", "migrate_legacy_collection", "backfill lane collections from legacy collection"),
    DirectWriter("orca_derived_index", "plugins/obsidian/backend/derived_index.py", "build_derived_index", "rebuild compatibility chunks"),
    DirectWriter("orca_memory_ledger", "plugins/obsidian/backend/memory_ledger.py", "ensure_memory_ledger", "create ledger schema"),
    DirectWriter("orca_memory_ledger", "plugins/obsidian/backend/memory_ledger.py", "sync_memory_ledger", "synchronize discovery checkpoint"),
    DirectWriter("orca_memory_ledger", "plugins/obsidian/backend/memory_ledger.py", "mark_source_indexed", "record indexing completion"),
    DirectWriter("orca_memory_ledger", "plugins/obsidian/backend/memory_ledger.py", "mark_source_failed", "record indexing failure"),
    DirectWriter("orca_manual_relationships", "plugins/obsidian/backend/vault_model.py", "save_manual_relationships", "persist relationship truth"),
    DirectWriter("orca_manual_relationships", "plugins/obsidian/backend/vault_model.py", "add_manual_relationship", "add relationship truth"),
    DirectWriter("orca_manual_relationships", "plugins/obsidian/backend/vault_model.py", "remove_manual_relationship", "remove relationship truth"),
    DirectWriter("orca_query_cache", "plugins/obsidian/backend/query_layer.py", "_save_cache_unlocked", "persist bounded query cache"),
    DirectWriter("orca_query_cache", "plugins/obsidian/backend/query_layer.py", "answer_query_async", "write synthesized query cache entry"),
    DirectWriter("orca_raptor_artifacts", "plugins/obsidian/backend/raptor_rebuild.py", "rebuild_raptor_artifacts", "rebuild graph and summary projection"),
    DirectWriter("orca_raptor_artifacts", "plugins/obsidian/backend/raptor_rebuild.py", "_atomic_write_json", "persist RAPTOR projection artifact"),
    DirectWriter("orca_vault", "plugins/obsidian/backend/vault_service.py", "write_file", "write owner-scoped vault file"),
    DirectWriter("orca_vault", "plugins/obsidian/backend/vault_service.py", "create_file", "create owner-scoped vault file"),
    DirectWriter("orca_vault", "plugins/obsidian/backend/vault_service.py", "update_file", "update owner-scoped vault file"),
    DirectWriter("orca_vault", "plugins/obsidian/backend/vault_service.py", "delete_file", "soft-delete owner-scoped vault file"),
    DirectWriter("orca_vault", "plugins/obsidian/backend/vault_service.py", "create_folder", "create vault folder"),
    DirectWriter("orca_vault", "plugins/obsidian/backend/vault_service.py", "delete_folder", "delete vault folder"),
    DirectWriter("orca_vault", "plugins/obsidian/backend/vault_service.py", "rename_item", "rename vault item"),
    DirectWriter("orca_vault", "plugins/obsidian/backend/vault_service.py", "merge_frontmatter", "update vault metadata"),
    DirectWriter("orca_vault", "plugins/obsidian/backend/vault_service.py", "batch_operations", "apply bounded vault mutations"),
    DirectWriter("orca_vault", "plugins/obsidian/backend/vault_service.py", "purge_trash", "purge expired vault trash"),
    DirectWriter("orca_vault", "plugins/obsidian/backend/vault_service.py", "purge_all_vault_trash", "purge expired trash across vaults"),
    DirectWriter("orca_vector_cache", "plugins/obsidian/backend/vault_model.py", "_save_embedding_cache", "persist whole-note embedding cache"),
    DirectWriter("orca_vector_cache", "plugins/obsidian/backend/vault_model.py", "build_vault_embedding_index", "refresh whole-note embedding cache"),
    DirectWriter("personal_docs_keyword", "src/personal_docs.py", "PersonalDocsManager.refresh_index", "rebuild in-memory keyword index"),
    DirectWriter("personal_docs_keyword", "src/personal_docs.py", "load_personal_index", "build in-memory keyword chunks"),
    DirectWriter("personal_docs_registry", "src/personal_docs.py", "PersonalDocsManager.save_directories", "persist source directory registry"),
    DirectWriter("personal_docs_registry", "src/personal_docs.py", "PersonalDocsManager._save_excluded", "persist source exclusions"),
    DirectWriter("personal_docs_registry", "src/personal_docs.py", "PersonalDocsManager.add_directory", "register source directory"),
    DirectWriter("personal_docs_registry", "src/personal_docs.py", "PersonalDocsManager.remove_directory", "remove source directory registration"),
    DirectWriter("personal_docs_registry", "src/personal_docs.py", "PersonalDocsManager.rename_directory", "rename source directory registration"),
    DirectWriter("personal_docs_registry", "src/personal_docs.py", "PersonalDocsManager.exclude_file", "persist source exclusion"),
    DirectWriter("planning_memory_projection", "src/planning_source_memory.py", "ingest_planning_sources_to_memory", "project accepted planning sources"),
    DirectWriter("planning_memory_projection", "src/planning_source_memory.py", "project_accepted_planning_memory", "project accepted planning packet"),
    DirectWriter("project_versions", "src/project_version_store.py", "ProjectVersionStore.reserve_version", "reserve immutable version"),
    DirectWriter("project_versions", "src/project_version_store.py", "ProjectVersionStore.persist_version", "persist immutable version"),
    DirectWriter("project_versions", "src/project_version_store.py", "ProjectVersionStore.mark_failed", "persist failed reservation state"),
    DirectWriter("rag_chroma", "src/rag_vector.py", "VectorRAG.add_document", "add semantic projection"),
    DirectWriter("rag_chroma", "src/rag_vector.py", "VectorRAG.add_documents_batch", "batch-add semantic projection"),
    DirectWriter("rag_chroma", "src/rag_vector.py", "VectorRAG.rename_owner", "rewrite projection ownership"),
    DirectWriter("rag_chroma", "src/rag_vector.py", "VectorRAG.rebuild_index", "rebuild semantic projection"),
    DirectWriter("rag_chroma", "src/rag_vector.py", "VectorRAG.index_personal_documents", "project personal documents"),
    DirectWriter("rag_chroma", "src/rag_vector.py", "VectorRAG.remove_directory", "delete projected directory"),
    DirectWriter("rag_chroma", "src/rag_vector.py", "VectorRAG.reindex_directory", "replace projected directory"),
    DirectWriter("rag_chroma", "src/rag_vector.py", "VectorRAG.delete_by_source", "delete projected source"),
    DirectWriter("rag_chroma_legacy", "src/embedding_lanes.py", "migrate_legacy_collection", "backfill lane collections from legacy collection"),
    DirectWriter("repo_registry", "src/repo_registry.py", "RepoRegistry.add", "register repository"),
    DirectWriter("repo_registry", "src/repo_registry.py", "RepoRegistry.put", "replace repository record"),
    DirectWriter("repo_registry", "src/repo_registry.py", "RepoRegistry.forget", "remove repository record"),
    DirectWriter("repo_registry", "src/repo_registry.py", "RepoRegistry.save_json", "persist registry"),
    DirectWriter("research_results", "src/research_handler_storage.py", "ResearchStorageMixin.clear_result", "mark result consumed"),
    DirectWriter("research_results", "src/research_handler_storage.py", "ResearchStorageMixin._save_result", "persist research result"),
    DirectWriter("research_results", "src/research_handler_storage.py", "ResearchStorageMixin.hide_image", "persist hidden image preference"),
    DirectWriter("research_results", "src/research_handler_storage.py", "ResearchStorageMixin.unhide_all_images", "clear hidden image preferences"),
    DirectWriter("research_results", "src/task_scheduler.py", "TaskScheduler._execute_research_task", "persist scheduled research result through bypass path"),
    DirectWriter("research_results", "routes/research_routes.py", "setup_research_routes.research_archive", "archive research result"),
    DirectWriter("research_results", "routes/research_routes.py", "setup_research_routes.research_delete", "delete research result"),
    DirectWriter("research_runtime_jobs", "src/research_handler.py", "ResearchHandler.start_research", "start process-local research job"),
    DirectWriter("research_runtime_jobs", "src/research_handler_storage.py", "ResearchStorageMixin.cancel_research", "cancel process-local research job"),
    DirectWriter("sandbox_job_events", "src/sandbox_job_ledger.py", "SandboxJobLedger.append", "append sandbox job event"),
    DirectWriter("sandbox_job_events", "src/sandbox_job_ledger.py", "SandboxJobLedger.record", "record sandbox job transition"),
    DirectWriter("scheduled_task_definitions", "routes/task_routes.py", "setup_task_routes.create_task", "create scheduled task"),
    DirectWriter("scheduled_task_definitions", "routes/task_routes.py", "setup_task_routes.update_task", "update scheduled task"),
    DirectWriter("scheduled_task_definitions", "routes/task_routes.py", "setup_task_routes.delete_task", "delete scheduled task"),
    DirectWriter("scheduled_task_definitions", "routes/task_routes.py", "setup_task_routes.pause_task", "pause scheduled task"),
    DirectWriter("scheduled_task_definitions", "routes/task_routes.py", "setup_task_routes.resume_task", "resume scheduled task"),
    DirectWriter("scheduled_task_definitions", "routes/task_routes.py", "setup_task_routes.revert_task", "revert scheduled task"),
    DirectWriter("scheduled_task_definitions", "src/task_scheduler.py", "TaskScheduler.ensure_defaults", "create task defaults"),
    DirectWriter("scheduled_task_definitions", "src/task_scheduler.py", "TaskScheduler.ensure_assistant_defaults", "create assistant task defaults"),
    DirectWriter("scheduled_task_runs", "src/task_scheduler.py", "TaskScheduler._execute_task", "create queued task attempt"),
    DirectWriter("scheduled_task_runs", "src/task_scheduler.py", "TaskScheduler._execute_task_locked", "persist task execution state"),
    DirectWriter("scheduled_task_runs", "src/task_scheduler.py", "TaskScheduler._set_run_progress", "update task run progress"),
    DirectWriter("scheduled_task_runs", "src/task_scheduler.py", "TaskScheduler._mark_run_aborted", "record task run abort"),
    DirectWriter("universal_inbox_raptorgraph_events", "src/universal_inbox_raptorgraph_store.py", "UniversalInboxRaptorGraphEventStore.append", "append idempotent graph mutation evidence"),
)


TOOL_IDENTITIES: tuple[ToolIdentity, ...] = (
    ToolIdentity("commit_project", "Project Versioning through TAX", "active", (("src/tool_index.py", "discovery index"), ("src/tool_schema_definitions.py", "public schema"), ("src/agent_tools/__init__.py", "handler registry")), "sole public commit authority"),
    ToolIdentity("get_workspace", "TAX Descriptor v2", "active", (("src/tool_index.py", "discovery index"), ("src/tool_schema_definitions.py", "public schema"), ("src/agent_tools/__init__.py", "handler registry")), "workspace navigation remains separate from retrieval"),
    ToolIdentity("glob", "TAX Descriptor v2", "active", (("src/tool_index.py", "discovery index"), ("src/tool_schema_definitions.py", "public schema"), ("src/agent_tools/__init__.py", "handler registry")), "exact path discovery remains a canonical evidence tool"),
    ToolIdentity("grep", "TAX Descriptor v2", "active", (("src/tool_index.py", "discovery index"), ("src/tool_schema_definitions.py", "public schema"), ("src/agent_tools/__init__.py", "handler registry")), "exact code search remains a canonical evidence tool"),
    ToolIdentity("ls", "TAX Descriptor v2", "active", (("src/tool_index.py", "discovery index"), ("src/tool_schema_definitions.py", "public schema"), ("src/agent_tools/__init__.py", "handler registry")), "exact directory navigation remains separate from retrieval"),
    ToolIdentity("manage_documents", "TAX Descriptor v2", "active", (("src/tool_index.py", "discovery index"), ("src/tool_schema_definitions.py", "public schema"), ("src/tool_execution.py", "dispatch")), "domain document operations remain domain-owned"),
    ToolIdentity("manage_embeddings", "TAX Descriptor v2", "active", (("src/tool_index.py", "discovery index"), ("src/tool_schema_definitions.py", "public schema"), ("src/tool_execution.py", "dispatch")), "manage projection configuration; never query knowledge"),
    ToolIdentity("manage_memory", "TAX Descriptor v2", "active", (("src/tool_index.py", "discovery index"), ("src/tool_schema_definitions.py", "public schema"), ("src/tool_execution.py", "dispatch")), "memory mutation and review remain domain-owned"),
    ToolIdentity("manage_personal_docs", "TAX Descriptor v2", "active", (("src/tool_index.py", "discovery index"), ("src/tool_schema_definitions.py", "public schema"), ("src/tool_execution.py", "dispatch")), "manage source configuration; never query knowledge"),
    ToolIdentity("manage_repos", "Repo Registry and Project Versioning through TAX", "active", (("src/tool_index.py", "discovery index"), ("src/tool_schema_definitions.py", "public schema"), ("src/tool_execution.py", "dispatch")), "repository registration and bounded Git facts remain domain-owned"),
    ToolIdentity("manage_research", "TAX Descriptor v2", "active", (("src/tool_index.py", "discovery index"), ("src/tool_schema_definitions.py", "public schema"), ("src/tool_execution.py", "dispatch")), "research lifecycle remains domain-owned"),
    ToolIdentity("query_knowledge", "TAX Descriptor v2 with USI provider", "planned_absent", (("src/tool_catalog.py", "descriptor owner"), ("src/tool_index.py", "discovery index"), ("src/tool_schema_definitions.py", "public schema"), ("src/tool_execution.py", "dispatch"), ("src/agent_tools/__init__.py", "handler registry")), "future single bounded federated query tool"),
    ToolIdentity("read_file", "TAX Descriptor v2", "active", (("src/tool_catalog.py", "descriptor policy"), ("src/tool_index.py", "discovery index"), ("src/tool_schema_definitions.py", "public schema"), ("src/agent_tools/__init__.py", "handler registry")), "exact raw evidence reader remains separate from retrieval"),
    ToolIdentity("recent_changes", "TAX Descriptor v2", "active", (("src/tool_index.py", "discovery index"), ("src/tool_schema_definitions.py", "public schema"), ("src/tool_execution.py", "dispatch")), "local change intelligence remains a repository-domain projection"),
    ToolIdentity("search_chats", "TAX Descriptor v2", "active", (("src/tool_index.py", "discovery index"), ("src/tool_schema_definitions.py", "public schema"), ("src/tool_execution.py", "dispatch")), "chat lookup remains owner-scoped until its adapter wave"),
    ToolIdentity("web_fetch", "TAX Descriptor v2", "active", (("src/tool_index.py", "discovery index"), ("src/tool_schema_definitions.py", "public schema"), ("src/agent_tools/__init__.py", "handler registry")), "named URL retrieval remains separate from persistent knowledge"),
    ToolIdentity("web_search", "TAX Descriptor v2", "active", (("src/tool_index.py", "discovery index"), ("src/tool_schema_definitions.py", "public schema"), ("src/agent_tools/__init__.py", "handler registry")), "live web discovery remains separate from persistent knowledge"),
)


HOTFILES: tuple[Hotfile, ...] = (
    Hotfile("src/tool_catalog.py", "TAX", "canonical tool descriptor and risk identity"),
    Hotfile("src/tool_index.py", "TAX", "tool discovery identity"),
    Hotfile("src/tool_schema_definitions.py", "TAX", "public tool schema identity"),
    Hotfile("src/tool_execution.py", "TAX/TUA", "dispatch and canonical invocation boundary"),
    Hotfile("src/agent_tools/__init__.py", "TAX", "agent handler registry"),
    Hotfile("src/tool_security.py", "TAX", "public and privileged tool reachability"),
    Hotfile("routes/model_routes.py", "TAX", "runtime tool status API projection"),
    Hotfile("src/database.py", "TUA", "canonical invocation persistence schema"),
    Hotfile("routes/diagnostics_routes.py", "TUA", "bounded usage diagnostics"),
    Hotfile("static/js/admin.js", "TAX/TUA", "admin tool metadata and aggregate analytics"),
    Hotfile("src/llm_core.py", "GMI", "local model runtime boundary"),
    Hotfile("src/local_model_scheduler.py", "GMI", "local maintenance scheduling boundary"),
    Hotfile("src/model_context.py", "GMI", "context probing and cache boundary"),
    Hotfile("src/chat_helpers.py", "GMI", "chat-side model maintenance integration"),
    Hotfile("src/observability_metrics.py", "GMI/GRO", "shared Prometheus registry and exporter boundary"),
    Hotfile("src/repo_registry.py", "Project Versioning", "repository identity truth"),
    Hotfile("src/repo_git_adapter.py", "Project Versioning", "read-only Git evidence"),
    Hotfile("src/project_version_store.py", "Project Versioning", "immutable version manifests"),
    Hotfile("app.py", "UIR", "application composition root"),
    Hotfile("src/app_initializer.py", "UIR", "service initialization"),
    Hotfile("src/chat_processor.py", "UIR", "query consumer cutover"),
    Hotfile("src/context_orchestrator.py", "UIR", "single bounded context provider boundary"),
    Hotfile("src/ai_interaction.py", "UIR", "global retrieval references"),
    Hotfile("src/rag_singleton.py", "UIR", "legacy RAG lifecycle"),
    Hotfile("src/personal_docs.py", "UIR/UDA/ULO", "source lifecycle and legacy query path"),
)


NON_STORE_BOUNDARIES: tuple[NonStoreBoundary, ...] = (
    NonStoreBoundary("ai_lens_graph_contracts", ("src/ai_lens_events.py", "src/ai_lens_projection.py", "src/ai_lens_graph.py"), "schema and bounded projections, not persisted graph truth", "consume USI references through AiLensService"),
    NonStoreBoundary("memory_provider_facade", ("src/memory_provider.py",), "adapter over MemoryManager and MemoryVectorStore", "do not create another memory store"),
    NonStoreBoundary("memory_store_interface_vocabulary", ("src/memory_store_interfaces.py",), "interface specification without a common runtime implementation", "USI-02 implements protocols without claiming domain truth"),
    NonStoreBoundary("nextcloud_intake_entry", ("src/nextcloud_intake_ledger.py", "src/nextcloud_review_queue.py"), "DTO and in-memory review projection, not the durable checkpoint ledger", "adapt AppendOnlyBigDataLedger instead"),
    NonStoreBoundary("progressive_graph_api", ("src/progressive_graph_api.py",), "bounded graph API contract, not a graph database", "bind later to a USI projection provider"),
    NonStoreBoundary("rag_manager_facade", ("src/rag_manager.py",), "compatibility wrapper over VectorRAG", "retire only after query parity"),
    NonStoreBoundary("universal_inbox_runtime_envelopes", ("src/universal_inbox_flow_state.py", "src/universal_inbox_pipeline.py"), "runtime payloads returned to callers without persistence", "persist only explicit JobStore checkpoints"),
)


MIGRATION_RISKS: tuple[MigrationRisk, ...] = (
    MigrationRisk("chroma_occurrence_identity_collision", "USI/UDA", ("src/rag_vector.py",), "current vector IDs can collapse identical chunks without occurrence identity", "rebuild projections from stable USI source-version-chunk IDs"),
    MigrationRisk("parallel_retrieval_injection", "UIR", ("src/chat_processor.py", "src/context_orchestrator.py"), "direct memory and personal-RAG injection coexists with the context provider boundary", "cut consumers over before activating query_knowledge"),
    MigrationRisk("personal_docs_owner_scope", "UDA/ULO", ("src/personal_docs.py",), "source registry persists paths without owner scope", "migrate registration and deletion lifecycle with explicit owner IDs"),
    MigrationRisk("personal_docs_remove_delegate_missing", "UIR/UDA/ULO", ("src/personal_docs.py", "src/rag_manager.py"), "PersonalDocsManager calls a missing RAGManager.remove_directory delegate", "repair or bypass the compatibility facade before deletion parity"),
    MigrationRisk("tool_identity_normalization_split", "TAX", ("src/tool_catalog.py",), "descriptor and manifest normalization can spell query_knowledge differently", "freeze one canonical ID before USI-09 registration"),
    MigrationRisk("tool_projection_parity_drift", "TAX", ("src/tool_catalog.py", "src/tool_index.py", "src/tool_schema_definitions.py", "src/agent_tools/__init__.py"), "schema, tag and discovery projections currently have different cardinalities", "pass TAX parity before adding the USI provider"),
    MigrationRisk("tool_alias_analytics_split", "TAX/TUA", ("plugins/obsidian/backend/tool_specs.py", "src/tool_security.py", "src/tool_execution.py"), "Vault aliases and native/MCP aliases can split one logical invocation identity", "canonicalize aliases once in TAX and count once in TUA"),
)


def _violation(code: str, entity: str, detail: str) -> dict[str, str]:
    return {"code": code, "entity": entity, "detail": detail}


def _duplicates(values: Iterable[str]) -> list[str]:
    counts = Counter(values)
    return sorted(value for value, count in counts.items() if count > 1)


def _resolve_repo_path(root: Path, relative: str) -> Path | None:
    pure = PurePosixPath(relative)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        return None
    candidate = (root / Path(*pure.parts)).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def _python_facts(path: Path) -> tuple[set[str], set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    symbols: set[str] = set()

    def collect(body: Sequence[ast.stmt], prefix: str = "") -> None:
        for node in body:
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            qualified = f"{prefix}.{node.name}" if prefix else node.name
            symbols.add(qualified)
            collect(node.body, qualified)

    collect(tree.body)
    literals = {node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)}
    return symbols, literals


def audit_inventory(
    root: Path,
    *,
    components: Sequence[Component] = COMPONENTS,
    writers: Sequence[DirectWriter] = DIRECT_WRITERS,
    tools: Sequence[ToolIdentity] = TOOL_IDENTITIES,
    hotfiles: Sequence[Hotfile] = HOTFILES,
    boundaries: Sequence[NonStoreBoundary] = NON_STORE_BOUNDARIES,
    risks: Sequence[MigrationRisk] = MIGRATION_RISKS,
    execution: dict | None = None,
) -> dict:
    """Return a deterministic inventory without importing runtime modules."""
    root = root.resolve()
    violations: list[dict[str, str]] = []
    facts: dict[str, tuple[set[str], set[str]]] = {}

    def inspect_path(relative: str, entity: str) -> tuple[set[str], set[str]] | None:
        target = _resolve_repo_path(root, relative)
        if target is None:
            violations.append(_violation("unsafe_path", entity, relative))
            return None
        if not target.is_file():
            violations.append(_violation("missing_path", entity, relative))
            return None
        if target.suffix != ".py":
            return set(), set()
        if relative not in facts:
            try:
                facts[relative] = _python_facts(target)
            except (OSError, SyntaxError, UnicodeError) as exc:
                violations.append(_violation("unreadable_python", entity, f"{relative}: {type(exc).__name__}"))
                return None
        return facts[relative]

    for duplicate in _duplicates(item.component_id for item in components):
        violations.append(_violation("duplicate_component", duplicate, "component id must be unique"))
    component_map = {item.component_id: item for item in components}
    for item in components:
        if not item.canonical_owner.strip():
            violations.append(_violation("ownerless_store", item.component_id, "canonical owner is required"))
        if item.classification not in CLASSIFICATIONS:
            violations.append(_violation("invalid_classification", item.component_id, item.classification))
        if item.writer_policy not in WRITER_POLICIES:
            violations.append(_violation("invalid_writer_policy", item.component_id, item.writer_policy))
        if not item.source_paths:
            violations.append(_violation("missing_source_path", item.component_id, "at least one source path is required"))
        for path in item.source_paths:
            inspect_path(path, item.component_id)

    writer_keys = (f"{item.component_id}:{item.path}:{item.symbol}" for item in writers)
    for duplicate in _duplicates(writer_keys):
        violations.append(_violation("duplicate_writer", duplicate, "writer declaration must be unique"))
    writers_by_component = Counter(item.component_id for item in writers)
    for item in components:
        if item.writer_policy == "required" and not writers_by_component[item.component_id]:
            violations.append(_violation("undocumented_writer", item.component_id, "writer-required store has no declared direct writer"))
    for item in writers:
        component = component_map.get(item.component_id)
        if component is None:
            violations.append(_violation("unknown_writer_component", item.component_id, item.symbol))
            continue
        if item.path not in component.source_paths:
            violations.append(_violation("writer_outside_component", item.component_id, item.path))
        found = inspect_path(item.path, f"{item.component_id}:{item.symbol}")
        if found is not None and item.symbol not in found[0]:
            violations.append(_violation("missing_writer_symbol", item.component_id, f"{item.path}:{item.symbol}"))

    for duplicate in _duplicates(item.tool_id for item in tools):
        violations.append(_violation("duplicate_tool_identity", duplicate, "one canonical tool identity is allowed"))
    for item in tools:
        if not item.canonical_owner.strip():
            violations.append(_violation("ownerless_tool_identity", item.tool_id, "canonical owner is required"))
        if item.state not in {"active", "planned_absent"}:
            violations.append(_violation("invalid_tool_state", item.tool_id, item.state))
        seen_on: list[str] = []
        for path, _role in item.required_surfaces:
            found = inspect_path(path, item.tool_id)
            if found is not None and item.tool_id in found[1]:
                seen_on.append(path)
            elif found is not None and item.state == "active":
                violations.append(_violation("missing_tool_surface", item.tool_id, path))
        if item.state == "planned_absent" and seen_on:
            violations.append(_violation("planned_tool_already_present", item.tool_id, ", ".join(sorted(seen_on))))

    for duplicate in _duplicates(item.path for item in hotfiles):
        violations.append(_violation("duplicate_hotfile", duplicate, "hotfile path must be unique"))
    for item in hotfiles:
        if not item.owner_track.strip():
            violations.append(_violation("ownerless_hotfile", item.path, "owner track is required"))
        inspect_path(item.path, item.path)

    for duplicate in _duplicates(item.boundary_id for item in boundaries):
        violations.append(_violation("duplicate_non_store_boundary", duplicate, "boundary id must be unique"))
    for item in boundaries:
        for path in item.paths:
            inspect_path(path, item.boundary_id)
    for duplicate in _duplicates(item.risk_id for item in risks):
        violations.append(_violation("duplicate_migration_risk", duplicate, "risk id must be unique"))
    for item in risks:
        if not item.owner_track.strip():
            violations.append(_violation("ownerless_migration_risk", item.risk_id, "owner track is required"))
        for path in item.paths:
            inspect_path(path, item.risk_id)

    component_rows = [asdict(item) for item in sorted(components, key=lambda value: value.component_id)]
    for row in component_rows:
        row["source_paths"] = list(row["source_paths"])
    writer_rows = [asdict(item) | {"evidence": "python_symbol"} for item in sorted(writers, key=lambda value: (value.component_id, value.path, value.symbol))]
    tool_rows = []
    for item in sorted(tools, key=lambda value: value.tool_id):
        row = asdict(item)
        row["required_surfaces"] = [
            {"path": path, "role": role} for path, role in item.required_surfaces
        ]
        tool_rows.append(row)
    hotfile_rows = [asdict(item) | {"edit_policy": "single_owner_handoff"} for item in sorted(hotfiles, key=lambda value: value.path)]
    boundary_rows = [asdict(item) for item in sorted(boundaries, key=lambda value: value.boundary_id)]
    risk_rows = [asdict(item) for item in sorted(risks, key=lambda value: value.risk_id)]
    for row in boundary_rows + risk_rows:
        row["paths"] = list(row["paths"])
    classification_counts = Counter(item.classification for item in components)
    kind_counts = Counter(item.store_kind for item in components)
    ordered_violations = sorted(violations, key=lambda item: (item["code"], item["entity"], item["detail"]))

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": INVENTORY_KIND,
        "updated_at": BASELINE_DATE,
        "execution": execution or {},
        "summary": {
            "component_count": len(components),
            "direct_writer_count": len(writers),
            "tool_identity_count": len(tools),
            "hotfile_count": len(hotfiles),
            "non_store_boundary_count": len(boundaries),
            "migration_risk_count": len(risks),
            "classification_counts": dict(sorted(classification_counts.items())),
            "store_kind_counts": dict(sorted(kind_counts.items())),
            "migration_candidate_count": sum(not item.migration_action.startswith("exclude_from_usi") for item in components),
            "private_corpus_accessed": False,
            "runtime_modules_imported": False,
            "violation_count": len(ordered_violations),
            "clean": not ordered_violations,
        },
        "components": component_rows,
        "direct_writers": writer_rows,
        "tool_identities": tool_rows,
        "hotfiles": hotfile_rows,
        "non_store_boundaries": boundary_rows,
        "migration_risks": risk_rows,
        "violations": ordered_violations,
    }


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read inventory {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"inventory must be a JSON object: {path}")
    return value


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, default=Path("docs/plans/unified-source-index-runtime-inventory.json"))
    parser.add_argument("--check", action="store_true", help="fail if the persisted inventory is stale or invalid")
    parser.add_argument("--print", action="store_true", dest="print_payload", help="print the computed inventory")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    existing: dict = {}
    if output.is_file():
        try:
            existing = _read_json(output)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
    payload = audit_inventory(root, execution=existing.get("execution", {}))
    if args.print_payload:
        print(json.dumps(payload, indent=2))
    if args.check:
        if not existing:
            print(f"USI inventory missing: {output}", file=sys.stderr)
            return 1
        if payload != existing:
            print(f"USI inventory drift: regenerate {output}", file=sys.stderr)
            return 1
    else:
        _write_json(output, payload)
    if payload["violations"]:
        for item in payload["violations"]:
            print(f"{item['code']}: {item['entity']}: {item['detail']}", file=sys.stderr)
        return 1
    print(
        "USI overlap inventory clean: "
        f"{payload['summary']['component_count']} components, "
        f"{payload['summary']['direct_writer_count']} writers, "
        f"{payload['summary']['tool_identity_count']} tool identities"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
