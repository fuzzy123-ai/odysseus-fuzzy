"""Core bounded context: Planning v2, Gates, Execution Authority & Evidence Receipts.

This module consolidates the durable planning store, execution claims ledger,
authority snapshots, and cryptographic verification receipts.
"""

from __future__ import annotations

from src.planning_revision_store import (
    PlanningRevisionStore,
    PlanningRevisionStoreError,
    PlanningRevisionRepository,
    PlanningRevisionRepositoryError,
    PlanningHead,
    PlanningRevision,
    PlanningReceipt,
    PlanningAcceptedHeadBinding,
    PlanningAcceptedMigrationBundle,
)
from src.harbor_planning_projection import (
    HarborPlanningProjection,
    HarborPlanningProjectionError,
    build_harbor_planning_projection,
)
from src.execution_authority_snapshot import (
    ExecutionAuthoritySnapshotError,
    build_execution_authority_snapshot,
    validate_execution_authority_snapshot,
)
from src.execution_authority_ledger import (
    ExecutionAuthorityLedgerError,
    build_execution_authority_ledger,
    validate_execution_authority_ledger,
    acquire_execution_claim,
    renew_execution_claim_lease,
    decide_execution_claim_gate,
    record_execution_claim_evidence,
    release_execution_claim,
    execution_authority_ledger_reference,
    project_execution_authority_ledger,
)
from src.agent_verification_receipt import (
    ReceiptError,
    RepositoryBinding,
    build_verification_receipt,
    repository_binding,
    validate_verification_receipt,
)

# Canonical aliases for typed core authority
ExecutionAuthorityLedger = build_execution_authority_ledger
AgentVerificationReceipt = build_verification_receipt

__all__ = [
    # Planning v2
    "PlanningRevisionStore",
    "PlanningRevisionStoreError",
    "PlanningRevisionRepository",
    "PlanningRevisionRepositoryError",
    "PlanningHead",
    "PlanningRevision",
    "PlanningReceipt",
    "PlanningAcceptedHeadBinding",
    "PlanningAcceptedMigrationBundle",
    "HarborPlanningProjection",
    "HarborPlanningProjectionError",
    "build_harbor_planning_projection",
    # Execution Authority
    "ExecutionAuthorityLedger",
    "build_execution_authority_ledger",
    "validate_execution_authority_ledger",
    "acquire_execution_claim",
    "renew_execution_claim_lease",
    "decide_execution_claim_gate",
    "record_execution_claim_evidence",
    "release_execution_claim",
    "execution_authority_ledger_reference",
    "project_execution_authority_ledger",
    "ExecutionAuthorityLedgerError",
    "build_execution_authority_snapshot",
    "validate_execution_authority_snapshot",
    "ExecutionAuthoritySnapshotError",
    # Verification & Receipts
    "AgentVerificationReceipt",
    "ReceiptError",
    "RepositoryBinding",
    "build_verification_receipt",
    "repository_binding",
    "validate_verification_receipt",
]
