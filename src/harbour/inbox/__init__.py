"""Inbox bounded context: Universal Inbox & Document Subject-Scope.

This module consolidates the Harbour inbox service, document subject-scope authority,
and request authority guards.
"""

from __future__ import annotations

from src.harbour_inbox_service import (
    HarbourInboxService,
    HarbourInboxError,
    HarbourInboxOutcome,
    PlanningInboxItem,
    PlanningInboxSnapshot,
    DocumentInboxItem,
    DocumentInboxSnapshot,
)
from src.document_subject_scope import (
    DocumentRequestAuthority,
    DocumentAuthorityLease,
    capture_document_request_authority,
    guard_document_request_authority,
    stamp_new_document,
)

# Canonical alias
HarbourInboxServiceError = HarbourInboxError

__all__ = [
    # Harbour Inbox Service
    "HarbourInboxService",
    "HarbourInboxError",
    "HarbourInboxServiceError",
    "HarbourInboxOutcome",
    "PlanningInboxItem",
    "PlanningInboxSnapshot",
    "DocumentInboxItem",
    "DocumentInboxSnapshot",
    # Document Subject Scope
    "DocumentRequestAuthority",
    "DocumentAuthorityLease",
    "capture_document_request_authority",
    "guard_document_request_authority",
    "stamp_new_document",
]
