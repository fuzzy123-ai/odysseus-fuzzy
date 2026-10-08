"""Subject-bound MCP adapter for the Harbour clarification inbox.

This module is deliberately narrower than the browser clarification API.  It
accepts one already-authenticated token identity, repeats the live bearer-row
proof while account authority is held, and exposes only closed metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from contextlib import nullcontext
import hashlib
import json
import re
from typing import TYPE_CHECKING, Any, Mapping

from core.auth import AuthSubjectIdentityError
from core.database import ApiToken, Document, get_db_session
from src.auth_helpers import SessionPrincipal
from src.clarification_store import (
    ClarificationInboxItem,
    ClarificationInboxSnapshot,
    ClarificationStore,
    ClarificationStoreError,
)
from src.harbour_knowledge_store import KNOWLEDGE_KINDS

if TYPE_CHECKING:
    from src.planning_revision_store import PlanningSubjectBoundReceipt


MCP_HARBOUR_INBOX_SCOPE = "mcp:harbour:inbox"
HARBOUR_INBOX_TOOL_NAMES = (
    "harbour_inbox_list",
    "harbour_inbox_pause",
    "harbour_inbox_reopen",
    "harbour_inbox_cancel",
)
HARBOUR_INBOX_ACTION_TOOLS = frozenset(HARBOUR_INBOX_TOOL_NAMES[1:])
_PUBLIC_KEY = re.compile(r"^[A-Za-z0-9_.:@/-]{1,180}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{8,120}$")
_CLARIFICATION_UTC_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{6})?Z$"
)
_KNOWLEDGE_UTC_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$"
)
_OPEN_CLARIFICATION_STATUSES = frozenset({
    "clarifying", "understanding_review", "ready_for_plan", "paused", "blocked",
})
_REQUIRED_ACTION = {
    "clarifying": "answer_required_questions",
    "understanding_review": "review_understanding",
    "ready_for_plan": "review_plan",
    "paused": "reopen_or_cancel",
    "blocked": "resolve_blocker",
    "planning": "wait_for_plan",
    "cancelled": "none",
    "expired": "none",
}


class HarbourInboxError(ValueError):
    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class HarbourInboxOutcome:
    payload: dict[str, Any]
    authority_finalized: bool


@dataclass(frozen=True, slots=True, repr=False)
class PlanningInboxItem:
    """Detached, SID-bound Planning metadata for the unified Inbox."""

    current_revision: int
    approved_revision: int
    record: str
    receipt: PlanningSubjectBoundReceipt | None = None

    def __repr__(self) -> str:
        return "PlanningInboxItem(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class PlanningInboxSnapshot:
    """Closed Planning capture; it deliberately retains no provider mappings."""

    owner_binding_digest: str
    accepted_batch_binding_digest: str
    head_boundary_digest: str
    items: tuple[PlanningInboxItem, ...]
    clipped: bool
    binding_digest: str

    def __repr__(self) -> str:
        return "PlanningInboxSnapshot(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class DocumentInboxItem:
    item_handle: str
    binding_digest: str
    current_revision: int
    document_id: str
    canonical_row: tuple[Any, ...]

    def __repr__(self) -> str:
        return "DocumentInboxItem(<redacted>)"


@dataclass(frozen=True, slots=True, repr=False)
class DocumentInboxSnapshot:
    whole_count: int
    eligible_count: int
    binding_digest: str
    items: tuple[DocumentInboxItem, ...]
    clipped: bool

    def __repr__(self) -> str:
        return "DocumentInboxSnapshot(<redacted>)"


def build_harbour_inbox_tool_contracts() -> tuple[dict[str, Any], ...]:
    action_properties = {
        "clarification_id": {"type": "string", "minLength": 8, "maxLength": 120},
        "expected_version": {"type": "integer", "minimum": 1, "maximum": 1_000_000},
        "idempotency_key": {"type": "string", "minLength": 1, "maxLength": 180},
    }
    return (
        {
            "name": "harbour_inbox_list",
            "description": "List the current administrator's open clarification metadata.",
            "inputSchema": {
                "oneOf": [
                    {"type": "object", "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "additionalProperties": False},
                    {"type": "object", "properties": {"view": {"const": "unified"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "required": ["view"], "additionalProperties": False},
                    {"type": "object", "properties": {"view": {"const": "typed"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "required": ["view"], "additionalProperties": False},
                    {"type": "object", "properties": {"view": {"const": "unified_receipts"}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}, "required": ["view", "limit"], "additionalProperties": False},
                    {"type": "object", "properties": {"view": {"const": "receipt"}, "receipt_ref": {"oneOf": [_clarification_receipt_ref_schema(), _knowledge_receipt_ref_schema(), _planning_receipt_ref_schema()]}}, "required": ["view", "receipt_ref"], "additionalProperties": False},
                ],
            },
        },
        *(
            {
                "name": name,
                "description": f"{name.rsplit('_', 1)[-1].capitalize()} one owned clarification run.",
                "inputSchema": {
                    "type": "object",
                    "properties": dict(action_properties),
                    "required": list(action_properties),
                    "additionalProperties": False,
                },
            }
            for name in HARBOUR_INBOX_TOOL_NAMES[1:]
        ),
    )


class HarbourInboxService:
    """Execute one Inbox operation beneath the account/token authority lock."""

    def __init__(self, *, auth_manager: Any, bearer_proof: Any, session_factory: Any = None,
                 planning_runtime: Any = None, knowledge_composition: Any = None,
                 upload_handler: Any = None) -> None:
        self._auth_manager = auth_manager
        self._bearer_proof = bearer_proof
        self._session_factory = session_factory
        self._planning_runtime = planning_runtime
        self._knowledge_composition = knowledge_composition
        self._upload_handler = upload_handler

    def tool_contracts(self) -> tuple[dict[str, Any], ...]:
        return build_harbour_inbox_tool_contracts()

    def dispatch(self, name: str, arguments: Mapping[str, Any], identity: Any) -> HarbourInboxOutcome:
        if name not in HARBOUR_INBOX_TOOL_NAMES or type(arguments) is not dict:
            raise HarbourInboxError("invalid_params")
        parsed = _validate_arguments(name, arguments)
        try:
            with self._auth_manager.guard_current_admin_subject(identity.owner_subject_id) as current_alias:
                self._verify_live_token(identity, current_alias)
                principal = SessionPrincipal(
                    owner=current_alias,
                    creator_subject_id=identity.owner_subject_id,
                    authority="mcp_subject",
                )
                store = ClarificationStore(session_factory=self._session_factory)
                if name == "harbour_inbox_list":
                    result = self._list(principal, identity, parsed)
                else:
                    operation = name.rsplit("_", 1)[-1]
                    namespaced_key = _mcp_action_key(parsed["idempotency_key"])
                    mutate = {
                        "pause": store.pause_run,
                        "reopen": store.reopen_run,
                        "cancel": store.cancel_run,
                    }[operation]
                    write = mutate(
                        principal=principal,
                        clarification_id=parsed["clarification_id"],
                        expected_version=parsed["expected_version"],
                        idempotency_key=namespaced_key,
                    )
                    result = _closed_action_result(operation, write)
                # Encode while authority remains held.  The result contains only
                # fixed keys and already-bounded scalar metadata, so successful
                # commit cannot be followed by an uncontrolled serialization
                # failure outside this guard.
                encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
                if len(encoded) > 64 * 1024:
                    raise HarbourInboxError("inbox_failed")
                # Lists are read outcomes: the transport must retain its
                # post-read authentication/deadline fences. Actions preserve
                # the committed-finalization behavior from the accepted C API.
                # The accepted C list is a finalized compatibility response.
                # Only the explicitly opted-in unified read remains subject to
                # transport post-read fences.
                return HarbourInboxOutcome(
                    result, name != "harbour_inbox_list"
                    or parsed.get("view") not in {"unified", "unified_receipts", "receipt", "typed"},
                )
        except AuthSubjectIdentityError:
            raise HarbourInboxError("unauthorized") from None
        except ClarificationStoreError as exc:
            if exc.code == "idempotency_conflict":
                raise HarbourInboxError("idempotency_conflict") from None
            if exc.code in {
                "invalid_clarification_id", "invalid_expected_version", "expected_version_out_of_range",
            }:
                raise HarbourInboxError("invalid_params") from None
            if exc.code in {"version_conflict", "run_not_found"}:
                raise HarbourInboxError(exc.code) from None
            raise HarbourInboxError("inbox_failed") from None

    def recover_action(
        self, name: str, arguments: Mapping[str, Any], identity: Any,
    ) -> HarbourInboxOutcome:
        """Recover a committed MCP action receipt without retrying mutation."""
        if name not in HARBOUR_INBOX_ACTION_TOOLS or type(arguments) is not dict:
            raise HarbourInboxError("invalid_params")
        parsed = _validate_arguments(name, arguments)
        operation = name.rsplit("_", 1)[-1]
        try:
            with self._auth_manager.guard_current_admin_subject(identity.owner_subject_id) as current_alias:
                self._verify_live_token(identity, current_alias)
                principal = SessionPrincipal(
                    owner=current_alias,
                    creator_subject_id=identity.owner_subject_id,
                    authority="mcp_subject",
                )
                recovered = ClarificationStore(
                    session_factory=self._session_factory,
                ).read_mcp_inbox_action_result(
                    principal=principal,
                    clarification_id=parsed["clarification_id"],
                    operation=operation,
                    expected_version=parsed["expected_version"],
                    idempotency_key=_mcp_action_key(parsed["idempotency_key"]),
                )
                if recovered is None:
                    raise HarbourInboxError("inbox_failed")
                result = _closed_action_result(operation, recovered)
                encoded = json.dumps(
                    result, sort_keys=True, separators=(",", ":"), allow_nan=False,
                ).encode("utf-8")
                if len(encoded) > 64 * 1024:
                    raise HarbourInboxError("inbox_failed")
                return HarbourInboxOutcome(result, True)
        except AuthSubjectIdentityError:
            raise HarbourInboxError("unauthorized") from None
        except ClarificationStoreError as exc:
            if exc.code == "idempotency_conflict":
                raise HarbourInboxError("idempotency_conflict") from None
            if exc.code in {
                "invalid_clarification_id", "invalid_expected_version",
                "expected_version_out_of_range",
            }:
                raise HarbourInboxError("invalid_params") from None
            if exc.code in {"version_conflict", "run_not_found"}:
                raise HarbourInboxError(exc.code) from None
            raise HarbourInboxError("inbox_failed") from None

    def _list(self, principal: SessionPrincipal, identity: Any, parsed: Mapping[str, Any]) -> dict[str, Any]:
        if parsed.get("view") is None:
            items = ClarificationStore(session_factory=self._session_factory).list_inbox_metadata(
                principal=principal, limit=parsed["limit"],
            )
            return {"schema": "odysseus.harbour_inbox_list.v1", "items": list(items), "count": len(items),
                    "authority": "mcp_subject", "freshness": "current"}
        if parsed["view"] == "receipt":
            return self._read_receipt(principal, identity, parsed["receipt_ref"])
        if parsed["view"] == "typed":
            return self._list_typed(principal, identity, parsed["limit"])
        # One reservation spans every producer capture, projection validation,
        # and every revalidation.  A close/key/selection change therefore
        # cannot race a partially validated unified response.
        if self._knowledge_composition is None:
            return self._list_unified(principal, identity, parsed["limit"], view=parsed["view"], knowledge_reserved=False)
        try:
            with self._knowledge_composition.guard_inbox_read():
                return self._list_unified(principal, identity, parsed["limit"], view=parsed["view"], knowledge_reserved=True)
        except HarbourInboxError:
            raise
        except Exception:
            raise HarbourInboxError("inbox_failed") from None

    def _list_unified(self, principal: SessionPrincipal, identity: Any, limit: int, *, view: str,
                      knowledge_reserved: bool) -> dict[str, Any]:
        clarification = ClarificationStore(session_factory=self._session_factory).capture_inbox_snapshot(
            principal=principal, limit=limit,
        )
        captures: dict[str, Any] = {"clarification": clarification}
        unavailable: set[str] = set()
        if self._planning_runtime is None:
            unavailable.add("planning")
        else:
            try:
                captures["planning"] = self._planning_runtime.capture_inbox_snapshot(
                    identity=identity, limit=limit, include_receipts=(view == "unified_receipts"),
                )
            except Exception as exc:
                # Only the app-owned Planning runtime can report the fixed
                # expected-absence code. Corruption, foreign bindings, stale
                # reads, and every unknown failure fail the whole request.
                from src.mcp_authenticated_dispatch import McpDispatchFailure
                if type(exc) is McpDispatchFailure and exc.code == "planning_unavailable":
                    unavailable.add("planning")
                else:
                    raise HarbourInboxError("inbox_failed") from None
        if self._knowledge_composition is None:
            unavailable.add("knowledge")
        else:
            if knowledge_reserved is not True:
                raise HarbourInboxError("inbox_failed") from None
            try:
                captures["knowledge"] = self._knowledge_composition.capture_inbox_snapshot(
                    identity=identity, limit=limit, _reservation_held=True,
                )
            except Exception:
                raise HarbourInboxError("inbox_failed") from None
        result = _unified_payload(captures, unavailable, limit)
        enriched = None
        if view == "unified_receipts":
            # Enrichment is part of the captured read.  The source checks that
            # follow validate the complete enriched result before it escapes.
            enriched = _unified_receipts_payload(result, captures, principal, self._session_factory)
        # Revalidate every available source even if its projected prefix is
        # empty. A configured source becoming absent is a failed request.
        again = ClarificationStore(session_factory=self._session_factory).capture_inbox_snapshot(
            principal=principal, limit=limit, expected_boundary_digest=clarification.boundary_digest,
        )
        if again.binding_digest != clarification.binding_digest:
            raise HarbourInboxError("inbox_failed")
        if "planning" in captures:
            try:
                rebound = self._planning_runtime.capture_inbox_snapshot(
                    identity=identity, limit=limit,
                    expected_binding_digest=captures["planning"].binding_digest,
                    include_receipts=(view == "unified_receipts"),
                )
                if rebound != captures["planning"]:
                    raise HarbourInboxError("inbox_failed")
            except Exception:
                raise HarbourInboxError("inbox_failed") from None
        if "knowledge" in captures:
            try:
                rebound = self._knowledge_composition.capture_inbox_snapshot(
                    identity=identity, limit=limit, expected_capture=captures["knowledge"],
                    _reservation_held=True,
                )
                if rebound != captures["knowledge"]:
                    raise HarbourInboxError("inbox_failed")
            except Exception:
                raise HarbourInboxError("inbox_failed") from None
        if view == "unified":
            return result
        if view != "unified_receipts":
            raise HarbourInboxError("inbox_failed")
        if enriched is None:
            raise HarbourInboxError("inbox_failed")
        return enriched

    def _list_typed(self, principal: SessionPrincipal, identity: Any, limit: int) -> dict[str, Any]:
        """Prepare the v3 read seam; native dispatch adds the matching planning opt-in later."""
        if self._planning_runtime is None or self._knowledge_composition is None:
            raise HarbourInboxError("inbox_failed")
        try:
            clarification = ClarificationStore(session_factory=self._session_factory).capture_inbox_snapshot(
                principal=principal, limit=limit,
            )
            document = _capture_document_inbox_snapshot(
                self._session_factory, principal.creator_subject_id, limit,
            )
            upload_configured = self._upload_handler is not None
            upload = None
            if upload_configured:
                upload = self._upload_handler.capture_inbox_snapshot(
                    owner_alias=principal.owner,
                    creator_subject_id=principal.creator_subject_id,
                    limit=limit,
                )
                upload = _validate_upload_capture(upload, limit)
            with self._knowledge_composition.guard_inbox_read():
                planning = self._planning_runtime.capture_inbox_snapshot(
                    identity=identity, limit=limit, include_receipts=True, typed=True,
                )
                knowledge = self._knowledge_composition.capture_inbox_snapshot(
                    identity=identity, limit=limit, _reservation_held=True,
                )
                captures = {
                    "clarification": clarification, "planning": planning, "knowledge": knowledge, "document": document,
                }
                if upload_configured:
                    captures["upload"] = upload
                payload = _typed_payload(captures, limit)
                again = ClarificationStore(session_factory=self._session_factory).capture_inbox_snapshot(
                    principal=principal, limit=limit, expected_boundary_digest=clarification.boundary_digest,
                )
                rebound_document = _capture_document_inbox_snapshot(self._session_factory, principal.creator_subject_id, limit)
                rebound_planning = self._planning_runtime.capture_inbox_snapshot(
                    identity=identity, limit=limit, expected_binding_digest=planning.binding_digest,
                    include_receipts=True, typed=True,
                )
                rebound_knowledge = self._knowledge_composition.capture_inbox_snapshot(
                    identity=identity, limit=limit, expected_capture=knowledge, _reservation_held=True,
                )
                rebound_upload = None
                if upload_configured:
                    rebound_upload = self._upload_handler.capture_inbox_snapshot(
                        owner_alias=principal.owner,
                        creator_subject_id=principal.creator_subject_id,
                        limit=limit,
                        expected_capture=upload,
                    )
                    rebound_upload = _validate_upload_capture(rebound_upload, limit)
        except HarbourInboxError:
            raise
        except Exception:
            raise HarbourInboxError("inbox_failed") from None
        _validate_document_snapshot_subject(document, principal.creator_subject_id)
        _validate_document_snapshot_subject(rebound_document, principal.creator_subject_id)
        if (again.binding_digest != clarification.binding_digest or rebound_document != document
                or rebound_planning != planning or rebound_knowledge != knowledge
                or upload_configured and rebound_upload != upload):
            raise HarbourInboxError("inbox_failed")
        return payload

    def _read_receipt(
        self, principal: SessionPrincipal, identity: Any, receipt_ref: Mapping[str, Any],
    ) -> dict[str, Any]:
        source = receipt_ref["source"]
        if source == "clarification":
            receipt = ClarificationStore(session_factory=self._session_factory).read_inbox_action_receipt(
                principal=principal,
                clarification_id=receipt_ref["clarification_id"],
                event_version=receipt_ref["event_version"],
                expected_current_version=receipt_ref["expected_current_version"],
                expected_receipt_handle=receipt_ref["receipt_handle"],
            )
            if receipt is None:
                raise HarbourInboxError("inbox_failed")
            return _closed_clarification_receipt(receipt)
        if source == "planning":
            if self._planning_runtime is None:
                raise HarbourInboxError("inbox_failed")
            try:
                with (
                    self._knowledge_composition.guard_inbox_read()
                    if self._knowledge_composition is not None
                    else nullcontext()
                ):
                    capture = self._planning_runtime.capture_inbox_snapshot(
                        identity=identity, limit=100, include_receipts=True,
                    )
                    _validate_planning_capture(capture, 100, require_receipts=True)
                    item = _find_planning_receipt_item(capture, receipt_ref)
                    if item.receipt is None:
                        raise HarbourInboxError("inbox_failed")
                    result = _closed_planning_receipt(item)
                    rebound = self._planning_runtime.capture_inbox_snapshot(
                        identity=identity, limit=100,
                        expected_binding_digest=capture.binding_digest, include_receipts=True,
                    )
                    if rebound != capture:
                        raise HarbourInboxError("inbox_failed")
                    return result
            except HarbourInboxError:
                raise
            except Exception:
                raise HarbourInboxError("inbox_failed") from None
        if source != "knowledge" or self._knowledge_composition is None:
            raise HarbourInboxError("inbox_failed")
        try:
            with self._knowledge_composition.guard_inbox_read():
                capture = self._knowledge_composition.capture_inbox_snapshot(
                    identity=identity, limit=100, _reservation_held=True,
                )
                _validate_knowledge_capture(capture, 100)
                item = _find_knowledge_receipt_item(capture, receipt_ref)
                result = _closed_knowledge_receipt(capture, item)
                rebound = self._knowledge_composition.capture_inbox_snapshot(
                    identity=identity, limit=100, expected_capture=capture, _reservation_held=True,
                )
                if rebound != capture:
                    raise HarbourInboxError("inbox_failed")
                return result
        except HarbourInboxError:
            raise
        except Exception:
            raise HarbourInboxError("inbox_failed") from None

    def _verify_live_token(self, identity: Any, current_alias: str) -> None:
        from src.mcp_authenticated_dispatch import McpBearerProof
        if (
            getattr(identity, "scope", None) != MCP_HARBOUR_INBOX_SCOPE
            or getattr(identity, "owner_alias", None) != current_alias
            or type(self._bearer_proof) is not McpBearerProof
            or self._bearer_proof._is_issued() is not True
        ):
            raise HarbourInboxError("unauthorized")
        factory = self._session_factory
        context = factory() if factory is not None else get_db_session()
        if factory is not None:
            class _SessionContext:
                def __init__(self, session: Any) -> None: self.session = session
                def __enter__(self) -> Any: return self.session
                def __exit__(self, *_args: Any) -> None: self.session.close()
            context = _SessionContext(context)
        with context as db:
            row = db.query(ApiToken).filter(ApiToken.id == identity.token_id).first()
            if (
                row is None
                or row.is_active is not True
                or row.scopes != MCP_HARBOUR_INBOX_SCOPE
                or row.owner != current_alias
                or row.owner_subject_id != identity.owner_subject_id
                or not self._bearer_proof.matches_hash(row.token_hash)
            ):
                raise HarbourInboxError("unauthorized")


def _mcp_action_key(public_key: Any) -> str:
    if type(public_key) is not str or _PUBLIC_KEY.fullmatch(public_key) is None:
        raise HarbourInboxError("invalid_params")
    return "mcp-hi-v1:" + hashlib.sha256(public_key.encode("utf-8")).hexdigest()


def _validate_arguments(name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    if name == "harbour_inbox_list":
        view = arguments.get("view")
        if view is None:
            if set(arguments) - {"limit"}:
                raise HarbourInboxError("invalid_params")
            limit = arguments.get("limit", 25)
            if type(limit) is not int or not 1 <= limit <= 100:
                raise HarbourInboxError("invalid_params")
            return {"limit": limit, "view": None}
        if type(view) is not str:
            raise HarbourInboxError("invalid_params")
        if view == "unified":
            if set(arguments) - {"view", "limit"}:
                raise HarbourInboxError("invalid_params")
            limit = arguments.get("limit", 25)
            if type(limit) is not int or not 1 <= limit <= 100:
                raise HarbourInboxError("invalid_params")
            return {"limit": limit, "view": view}
        if view == "typed":
            if set(arguments) - {"view", "limit"}:
                raise HarbourInboxError("invalid_params")
            limit = arguments.get("limit", 25)
            if type(limit) is not int or not 1 <= limit <= 100:
                raise HarbourInboxError("invalid_params")
            return {"limit": limit, "view": view}
        if view == "unified_receipts":
            if set(arguments) != {"view", "limit"} or type(arguments.get("limit")) is not int:
                raise HarbourInboxError("invalid_params")
            if not 1 <= arguments["limit"] <= 100:
                raise HarbourInboxError("invalid_params")
            return {"limit": arguments["limit"], "view": view}
        if view == "receipt":
            if set(arguments) != {"view", "receipt_ref"}:
                raise HarbourInboxError("invalid_params")
            return {"view": view, "receipt_ref": _validate_receipt_ref(arguments["receipt_ref"])}
        raise HarbourInboxError("invalid_params")
    if set(arguments) != {"clarification_id", "expected_version", "idempotency_key"}:
        raise HarbourInboxError("invalid_params")
    clarification_id = arguments.get("clarification_id")
    version = arguments.get("expected_version")
    key = arguments.get("idempotency_key")
    if (
        type(clarification_id) is not str
        or _SAFE_ID.fullmatch(clarification_id) is None
        or type(version) is not int
        or not 1 <= version <= 1_000_000
        or type(key) is not str
        or _PUBLIC_KEY.fullmatch(key) is None
    ):
        raise HarbourInboxError("invalid_params")
    return {"clarification_id": clarification_id, "expected_version": version, "idempotency_key": key}


def _validate_receipt_ref(value: Any) -> dict[str, Any]:
    if type(value) is not dict or type(value.get("source")) is not str:
        raise HarbourInboxError("invalid_params")
    source = value["source"]
    if source == "clarification":
        required = {"source", "clarification_id", "event_version", "expected_current_version", "receipt_handle"}
        if set(value) != required or type(value.get("clarification_id")) is not str:
            raise HarbourInboxError("invalid_params")
        if _SAFE_ID.fullmatch(value["clarification_id"]) is None:
            raise HarbourInboxError("invalid_params")
        for key in ("event_version", "expected_current_version"):
            if type(value.get(key)) is not int or not 1 <= value[key] <= 1_000_001:
                raise HarbourInboxError("invalid_params")
        handle = value["receipt_handle"]
        if handle is not None and (type(handle) is not str or re.fullmatch(r"hir1:c:[0-9a-f]{64}", handle) is None):
            raise HarbourInboxError("invalid_params")
        return dict(value)
    if source == "knowledge":
        required = {"source", "item_handle", "binding_digest", "receipt_handle"}
        if set(value) != required:
            raise HarbourInboxError("invalid_params")
        if (
            type(value.get("item_handle")) is not str
            or re.fullmatch(r"hi1:knowledge:[0-9a-f]{64}", value["item_handle"]) is None
            or type(value.get("binding_digest")) is not str
            or type(value.get("receipt_handle")) is not str
        ):
            raise HarbourInboxError("invalid_params")
        try:
            _capture_digest(value["binding_digest"], prefixed=True)
            _capture_digest(value["receipt_handle"], prefixed=True)
        except HarbourInboxError:
            raise HarbourInboxError("invalid_params") from None
        return dict(value)
    if source == "planning":
        required = {"source", "item_handle", "binding_digest", "receipt_handle"}
        if set(value) != required:
            raise HarbourInboxError("invalid_params")
        if (
            type(value.get("item_handle")) is not str
            or re.fullmatch(r"hi1:planning:[0-9a-f]{64}", value["item_handle"]) is None
            or type(value.get("binding_digest")) is not str
            or re.fullmatch(r"[0-9a-f]{64}", value["binding_digest"]) is None
            or type(value.get("receipt_handle")) is not str
        ):
            raise HarbourInboxError("invalid_params")
        try:
            _capture_digest(value["receipt_handle"], prefixed=True)
        except HarbourInboxError:
            raise HarbourInboxError("invalid_params") from None
        return dict(value)
    raise HarbourInboxError("invalid_params")


def _clarification_receipt_ref_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {
        "source": {"const": "clarification"},
        "clarification_id": {"type": "string", "minLength": 8, "maxLength": 120},
        "event_version": {"type": "integer", "minimum": 1, "maximum": 1_000_001},
        "expected_current_version": {"type": "integer", "minimum": 1, "maximum": 1_000_001},
        "receipt_handle": {"type": ["string", "null"]},
    }, "required": ["source", "clarification_id", "event_version", "expected_current_version", "receipt_handle"], "additionalProperties": False}


def _knowledge_receipt_ref_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {
        "source": {"const": "knowledge"},
        "item_handle": {"type": "string", "pattern": "^hi1:knowledge:[0-9a-f]{64}$"},
        "binding_digest": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
        "receipt_handle": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
    }, "required": ["source", "item_handle", "binding_digest", "receipt_handle"], "additionalProperties": False}


def _planning_receipt_ref_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {
        "source": {"const": "planning"},
        "item_handle": {"type": "string", "pattern": "^hi1:planning:[0-9a-f]{64}$"},
        "binding_digest": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "receipt_handle": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
    }, "required": ["source", "item_handle", "binding_digest", "receipt_handle"], "additionalProperties": False}


def _unified_receipts_payload(
    payload: Mapping[str, Any], captures: Mapping[str, Any], principal: SessionPrincipal, session_factory: Any,
) -> dict[str, Any]:
    """Add exact receipt references only after every producer revalidation."""
    if payload.get("schema") != "odysseus.harbour_inbox_unified_list.v1":
        raise HarbourInboxError("inbox_failed")
    if "planning" in captures:
        _validate_planning_capture(captures["planning"], 100, require_receipts=True)
    result = dict(payload)
    result["items"] = [dict(item) for item in payload.get("items", ())]
    clarification = _validate_clarification_capture(captures["clarification"], 100)
    by_id = {item.clarification_id: item for item in clarification.items}
    store = ClarificationStore(session_factory=session_factory)
    aggregate_envelope_bytes = 0
    for item in result["items"]:
        source = item.get("source")
        if source == "planning":
            captured = next(
                (candidate for candidate in captures["planning"].items
                 if "hi1:planning:" + _unified_digest("planning", candidate.record, candidate.record)
                 == item.get("item_handle")),
                None,
            )
            if captured is None or item.get("binding_digest") != captured.record:
                raise HarbourInboxError("inbox_failed")
            item["receipt_ref"] = None if captured.receipt is None else {
                "source": "planning", "item_handle": item["item_handle"],
                "binding_digest": item["binding_digest"],
                "receipt_handle": captured.receipt.receipt_handle,
            }
        elif source == "knowledge":
            item["receipt_ref"] = {
                "source": "knowledge", "item_handle": item["item_handle"],
                "binding_digest": item["binding_digest"], "receipt_handle": item["receipt_handle"],
            }
        elif source == "clarification":
            captured = by_id.get(item.get("clarification_id"))
            if captured is None or captured.expected_version != item.get("expected_version"):
                raise HarbourInboxError("inbox_failed")
            receipt = store.read_inbox_action_receipt(
                principal=principal, clarification_id=captured.clarification_id,
                event_version=captured.expected_version,
                expected_current_version=captured.expected_version, expected_receipt_handle=None,
            )
            if receipt is not None:
                aggregate_envelope_bytes += receipt.envelope_bytes
                if aggregate_envelope_bytes > 1_048_576:
                    raise HarbourInboxError("budget_exceeded")
            item["receipt_ref"] = None if receipt is None else {
                "source": "clarification", "clarification_id": captured.clarification_id,
                "event_version": receipt.event_version,
                "expected_current_version": captured.expected_version,
                "receipt_handle": receipt.receipt_handle,
            }
        else:
            raise HarbourInboxError("inbox_failed")
    result["schema"] = "odysseus.harbour_inbox_unified_list.v2"
    while len(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")) > 32_768 and result["items"]:
        result["items"].pop()
        result["count"] = len(result["items"])
        result["clipped"] = True
        result["complete"] = False
    if len(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")) > 32_768:
        raise HarbourInboxError("budget_exceeded")
    return result


def _find_knowledge_receipt_item(capture: Any, receipt_ref: Mapping[str, Any]) -> Any:
    _validate_knowledge_capture(capture, 100)
    for item in capture.items:
        handle = "hi1:knowledge:" + _unified_digest("knowledge", item.binding_digest, item.signal_handle)
        if handle == receipt_ref["item_handle"]:
            if item.binding_digest != receipt_ref["binding_digest"] or item.receipt_handle != receipt_ref["receipt_handle"]:
                raise HarbourInboxError("inbox_failed")
            return item
    raise HarbourInboxError("inbox_failed")


def _find_planning_receipt_item(capture: Any, receipt_ref: Mapping[str, Any]) -> PlanningInboxItem:
    _validate_planning_capture(capture, 100, require_receipts=True)
    for item in capture.items:
        handle = "hi1:planning:" + _unified_digest("planning", item.record, item.record)
        if handle == receipt_ref["item_handle"]:
            if item.record != receipt_ref["binding_digest"]:
                raise HarbourInboxError("inbox_failed")
            if item.receipt is None or item.receipt.receipt_handle != receipt_ref["receipt_handle"]:
                raise HarbourInboxError("inbox_failed")
            return item
    raise HarbourInboxError("inbox_failed")


def _receipt_base(*, source: str, receipt_handle: str, binding: str, producer: str,
                  operation: str, occurred_at: str) -> dict[str, Any]:
    return {
        "schema": "odysseus.harbour_inbox_receipt.v1", "source": source, "owner": "self",
        "authority": "mcp_subject", "receipt_handle": receipt_handle,
        "current_binding_digest": binding, "producer": producer, "operation": operation,
        "occurred_at": occurred_at, "verified": True, "freshness": "captured_revalidated",
        "authorizes_mutation": False,
        "redaction_flags": {"content": True, "identity": True, "events": True},
    }


def _closed_clarification_receipt(receipt: Any) -> dict[str, Any]:
    value = _receipt_base(
        source="clarification", receipt_handle=receipt.receipt_handle,
        binding=receipt.current_binding_digest, producer="clarification_store",
        operation=receipt.operation, occurred_at=receipt.occurred_at,
    )
    value.update({"event_version": receipt.event_version, "committed_status": receipt.committed_status})
    return value


def _closed_knowledge_receipt(capture: Any, item: Any) -> dict[str, Any]:
    value = _receipt_base(
        source="knowledge", receipt_handle=item.receipt_handle, binding=capture.binding_digest,
        producer="harbour_knowledge_active_runtime", operation="purge_requested", occurred_at=item.occurred_at,
    )
    value.update({"generation": item.generation, "revision": item.revision,
                  "verified_d0_event_digest": item.event_digest})
    return value


def _closed_planning_receipt(item: PlanningInboxItem) -> dict[str, Any]:
    receipt = item.receipt
    if receipt is None:
        raise HarbourInboxError("inbox_failed")
    return _receipt_base(
        source="planning", receipt_handle=receipt.receipt_handle,
        binding=receipt.current_binding_digest, producer=receipt.producer,
        operation=receipt.operation, occurred_at=receipt.occurred_at,
    )


def _closed_action_result(operation: str, write: Any) -> dict[str, Any]:
    run = write.run
    status = str(run["status"])
    return {
        "schema": "odysseus.harbour_inbox_action.v1",
        "action": operation,
        "item": {
            "clarification_id": str(run["clarification_id"]),
            "source": "clarification_store",
            "owner": "self",
            "status": status,
            "version": int(run["version"]),
            "ready_for_plan": bool(run["ready_for_plan"]),
            "required_action": _REQUIRED_ACTION[status],
            "due": None,
            "authority": "mcp_subject",
            "event_receipt_present": True,
            "freshness": "committed_receipt",
            "updated_at": str(run["updated_at"]),
            "redaction_flags": {"content": True, "identity": True, "events": True},
        },
        "idempotent_replay": bool(write.idempotent_replay),
    }


def _canonical_unified_digest(domain: str, value: Mapping[str, Any]) -> str:
    """Hash a fixed ASCII JSON value with unambiguous field boundaries."""
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        raise HarbourInboxError("inbox_failed") from None
    prefix = domain.encode("ascii")
    return hashlib.sha256(
        len(prefix).to_bytes(4, "big") + prefix + len(encoded).to_bytes(4, "big") + encoded
    ).hexdigest()


def _unified_digest(source: str, binding: str, identity: str) -> str:
    if type(source) is not str or type(binding) is not str or type(identity) is not str:
        raise HarbourInboxError("inbox_failed")
    return _canonical_unified_digest("odysseus.harbour.unified_inbox.item.v1", {
        "binding": binding, "identity": identity, "source": source,
    })


def _capture_digest(value: Any, *, prefixed: bool = False) -> str:
    if type(value) is not str:
        raise HarbourInboxError("inbox_failed")
    raw = value[7:] if prefixed and value.startswith("sha256:") else value
    if (prefixed and not value.startswith("sha256:")) or len(raw) != 64 or any(
        char not in "0123456789abcdef" for char in raw
    ):
        raise HarbourInboxError("inbox_failed")
    return value


def _validate_clarification_timestamp(value: Any) -> str:
    if type(value) is not str or _CLARIFICATION_UTC_TIMESTAMP.fullmatch(value) is None:
        raise HarbourInboxError("inbox_failed")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except (ValueError, OverflowError):
        raise HarbourInboxError("inbox_failed") from None
    if parsed.tzinfo != timezone.utc or parsed.isoformat().replace("+00:00", "Z") != value:
        raise HarbourInboxError("inbox_failed")
    return value


def _validate_knowledge_timestamp(value: Any) -> str:
    if type(value) is not str or _KNOWLEDGE_UTC_TIMESTAMP.fullmatch(value) is None:
        raise HarbourInboxError("inbox_failed")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except (ValueError, OverflowError):
        raise HarbourInboxError("inbox_failed") from None
    if parsed.tzinfo != timezone.utc or parsed.isoformat(timespec="microseconds").replace("+00:00", "Z") != value:
        raise HarbourInboxError("inbox_failed")
    return value


def _validate_clarification_capture(value: Any, limit: int) -> ClarificationInboxSnapshot:
    if (type(value) is not ClarificationInboxSnapshot or type(value.items) is not tuple
            or len(value.items) > limit or type(value.clipped) is not bool):
        raise HarbourInboxError("inbox_failed")
    for digest in (value.owner_binding_digest, value.boundary_digest, value.binding_digest):
        _capture_digest(digest)
    for item in value.items:
        if (
            type(item) is not ClarificationInboxItem or type(item.clarification_id) is not str
            or _SAFE_ID.fullmatch(item.clarification_id) is None or type(item.status) is not str
            or item.status not in _OPEN_CLARIFICATION_STATUSES
            or type(item.expected_version) is not int or isinstance(item.expected_version, bool)
            or not 1 <= item.expected_version <= 1_000_001 or type(item.ready_for_plan) is not bool
        ):
            raise HarbourInboxError("inbox_failed")
        _validate_clarification_timestamp(item.updated_at)
        _capture_digest(item.binding_digest)
    return value


def _validate_planning_capture(
    value: Any, limit: int, *, require_receipts: bool = False,
) -> PlanningInboxSnapshot:
    if (type(value) is not PlanningInboxSnapshot or type(value.items) is not tuple
            or len(value.items) > limit or type(value.clipped) is not bool
            or (require_receipts and value.clipped)):
        raise HarbourInboxError("inbox_failed")
    for digest in (
        value.owner_binding_digest, value.accepted_batch_binding_digest,
        value.head_boundary_digest, value.binding_digest,
    ):
        _capture_digest(digest)
    for item in value.items:
        if (
            type(item) is not PlanningInboxItem or type(item.current_revision) is not int
            or type(item.approved_revision) is not int or isinstance(item.current_revision, bool)
            or isinstance(item.approved_revision, bool) or item.approved_revision < 1
            or item.current_revision <= item.approved_revision
        ):
            raise HarbourInboxError("inbox_failed")
        _capture_digest(item.record)
        receipt = item.receipt
        if receipt is None:
            continue
        from src.planning_revision_store import PlanningSubjectBoundReceipt
        if (
            type(receipt) is not PlanningSubjectBoundReceipt
            or type(receipt.receipt_handle) is not str
            or type(receipt.current_binding_digest) is not str
            or type(receipt.operation) is not str
            or type(receipt.producer) is not str
            or receipt.producer != "harbour_planning_store"
            or receipt.operation not in {"append_revision", "approve_revision", "revert_head"}
            or type(receipt.verified) is not bool or receipt.verified is not True
            or type(receipt.authorizes_mutation) is not bool or receipt.authorizes_mutation is not False
        ):
            raise HarbourInboxError("inbox_failed")
        _capture_digest(receipt.receipt_handle, prefixed=True)
        _capture_digest(receipt.current_binding_digest, prefixed=True)
        _validate_knowledge_timestamp(receipt.occurred_at)
    return value


def _validate_knowledge_capture(value: Any, limit: int) -> Any:
    from src.harbour_knowledge_active_runtime import KnowledgeInboxSignal, KnowledgeInboxSnapshot

    if (type(value) is not KnowledgeInboxSnapshot or type(value.items) is not tuple
            or len(value.items) > limit or type(value.clipped) is not bool):
        raise HarbourInboxError("inbox_failed")
    for digest in (
        value.owner_binding_digest, value.selection_binding_digest,
        value.canonical_generation_digest, value.signal_snapshot_digest,
        value.captured_head_boundary, value.binding_digest,
    ):
        _capture_digest(digest, prefixed=True)
    for item in value.items:
        if (
            type(item) is not KnowledgeInboxSignal or type(item.signal_handle) is not str
            or type(item.signal_type) is not str or item.signal_type != "purge_requested"
            or type(item.state) is not str or item.state != "purge_pending"
            or type(item.knowledge_kind) is not str
            or type(item.generation) is not int or isinstance(item.generation, bool) or item.generation < 1
            or type(item.revision) is not int or isinstance(item.revision, bool) or item.revision < 1
        ):
            raise HarbourInboxError("inbox_failed")
        if item.knowledge_kind not in KNOWLEDGE_KINDS:
            raise HarbourInboxError("inbox_failed")
        _validate_knowledge_timestamp(item.occurred_at)
        for digest in (item.signal_handle, item.event_digest, item.receipt_handle, item.binding_digest):
            _capture_digest(digest, prefixed=True)
    return value


def _unified_payload(captures: Mapping[str, Any], unavailable: set[str], limit: int) -> dict[str, Any]:
    """Build the frozen, content-free unified projection deterministically."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise HarbourInboxError("inbox_failed")
    public: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    redaction = {"content": True, "identity": True, "events": True}
    for source in ("clarification", "planning", "knowledge"):
        capture = captures.get(source)
        if source in unavailable:
            source_rows.append({"source": source, "status": "unavailable", "clipped": False, "binding_digest": None})
            continue
        if source == "clarification":
            capture = _validate_clarification_capture(capture, limit)
        elif source == "planning":
            capture = _validate_planning_capture(capture, limit)
        else:
            capture = _validate_knowledge_capture(capture, limit)
        binding = capture.binding_digest
        clipped = capture.clipped
        source_rows.append({"source": source, "status": "captured_revalidated", "clipped": clipped,
                            "binding_digest": binding})
        items = capture.items
        for item in items:
            if source == "clarification":
                item_binding, opaque = item.binding_digest, item.clarification_id
                public.append({"item_handle": "hi1:clarification:" + _unified_digest(source, item_binding, opaque),
                    "source": source, "owner": "self", "signal_type": "open_clarification", "status": item.status,
                    "required_action": _REQUIRED_ACTION[item.status], "binding_digest": item_binding, "due": None,
                    "receipt_handle": None, "redaction_flags": dict(redaction), "clarification_id": item.clarification_id,
                    "expected_version": item.expected_version, "ready_for_plan": item.ready_for_plan, "updated_at": item.updated_at})
            elif source == "planning":
                item_binding, opaque = item.record, item.record
                public.append({"item_handle": "hi1:planning:" + _unified_digest(source, item_binding, opaque),
                    "source": source, "owner": "self", "signal_type": "revision_ahead_of_approval", "status": "pending_review",
                    "required_action": "review_revision", "binding_digest": item_binding, "due": None,
                    "receipt_handle": None, "redaction_flags": dict(redaction), "current_revision": item.current_revision,
                    "approved_revision": item.approved_revision})
            else:
                item_binding, opaque = item.binding_digest, item.signal_handle
                public.append({"item_handle": "hi1:knowledge:" + _unified_digest(source, item_binding, opaque),
                    "source": source, "owner": "self", "signal_type": "purge_requested", "status": "purge_pending",
                    "required_action": "review_purge_request", "binding_digest": item_binding, "due": None,
                    "receipt_handle": item.receipt_handle, "redaction_flags": dict(redaction), "knowledge_kind": item.knowledge_kind,
                    "generation": item.generation, "revision": item.revision, "occurred_at": item.occurred_at})
    rank = {"clarification": 0, "planning": 1, "knowledge": 2}
    public.sort(key=lambda item: (rank[item["source"]], item["item_handle"]))
    # `limit` is per producer.  Preserve all three bounded prefixes, then
    # apply the distinct global ceiling before byte clipping.
    clipped = any(row["clipped"] for row in source_rows) or len(public) > 300
    public = public[:300]
    payload = {"schema": "odysseus.harbour_inbox_unified_list.v1", "items": public, "count": len(public),
        "sources": source_rows, "complete": not unavailable and not clipped, "clipped": clipped,
        "authority": "mcp_subject", "freshness": "captured_revalidated", "cross_source_atomic": False}
    # Byte clipping removes the deterministic suffix, keeping a closed valid
    # envelope. An envelope that cannot fit is a fixed budget failure.
    while len(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")) > 32_768 and payload["items"]:
        payload["items"].pop()
        payload["count"] = len(payload["items"])
        payload["clipped"] = True
        payload["complete"] = False
    if len(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")) > 32_768:
        raise HarbourInboxError("budget_exceeded")
    return payload


def _document_timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if type(value) is not datetime:
        raise HarbourInboxError("inbox_failed")
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _document_row_bytes(row: tuple[Any, ...], subject_id: str) -> bytes:
    if type(row) is not tuple or len(row) != 11:
        raise HarbourInboxError("inbox_failed")
    document_id, creator, session_id, version, active, archived, verdict, source_kind, source_hash, created, updated = row
    if (type(document_id) is not str or type(creator) is not str or creator != subject_id
            or type(version) is not int or isinstance(version, bool) or version < 1
            or type(active) is not bool or type(archived) is not bool
            or verdict is not None and type(verdict) is not str
            or type(session_id) is not str and session_id is not None
            or type(source_kind) is not str or not source_kind
            or type(source_hash) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", source_hash) is None):
        raise HarbourInboxError("inbox_failed")
    values = [document_id, creator, session_id, version, active, archived, verdict, source_kind, source_hash,
              _document_timestamp(created), _document_timestamp(updated)]
    return json.dumps(values, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _frame(value: bytes) -> bytes:
    return len(value).to_bytes(8, "big") + value


def _capture_document_inbox_snapshot(factory: Any, subject_id: str, limit: int) -> DocumentInboxSnapshot:
    if type(subject_id) is not str or type(limit) is not int or not 1 <= limit <= 100:
        raise HarbourInboxError("inbox_failed")
    context = factory() if factory is not None else get_db_session()
    if factory is not None:
        class _SessionContext:
            def __init__(self, session: Any) -> None: self.session = session
            def __enter__(self) -> Any: return self.session
            def __exit__(self, *_args: Any) -> None: self.session.close()
        context = _SessionContext(context)
    digest = hashlib.sha256(b"odysseus.harbour_inbox.document_source.v3\0" + _frame(subject_id.encode("utf-8")))
    retained: list[tuple[str, DocumentInboxItem]] = []
    whole_count = eligible_count = 0
    with context as db:
        rows = db.query(Document.id, Document.creator_subject_id, Document.session_id, Document.version_count,
                        Document.is_active, Document.archived, Document.tidy_verdict, Document.source_kind,
                        Document.source_ref_hash, Document.created_at, Document.updated_at).filter(
                            Document.creator_subject_id == subject_id,
                            Document.creator_subject_id.is_not(None),
                            Document.source_kind.is_not(None),
                            Document.source_ref_hash.is_not(None),
                        ).order_by(Document.id).yield_per(100)
        for row in rows:
            captured = tuple(row)
            canonical = _document_row_bytes(captured, subject_id)
            document_id, _creator, _session_id, version, _active, archived, _verdict, _source_kind, _source_hash, _created, _updated = captured
            digest.update(_frame(canonical)); whole_count += 1
            if archived is not False:
                continue
            eligible_count += 1
            item_digest = hashlib.sha256(b"odysseus.harbour_inbox.document_item.v3\0" + _frame(subject_id.encode("utf-8")) + _frame(canonical)).hexdigest()
            binding = "sha256:" + item_digest
            item = DocumentInboxItem(
                "hi1:document:" + _unified_digest("document", binding, document_id), binding, version,
                document_id, captured,
            )
            retained.append((item.item_handle, item)); retained.sort(key=lambda value: value[0])
            if len(retained) > limit:
                retained.pop()
    return DocumentInboxSnapshot(whole_count, eligible_count, "sha256:" + digest.hexdigest(),
                                 tuple(item for _handle, item in retained), eligible_count > limit)


def _validate_upload_capture(value: Any, limit: int) -> Any:
    from src.upload_handler import UploadInboxItem, UploadInboxSnapshot

    if (
        type(value) is not UploadInboxSnapshot
        or type(value.selected_count) is not int
        or isinstance(value.selected_count, bool)
        or value.selected_count < 0
        or type(value.items) is not tuple
        or len(value.items) != min(value.selected_count, limit)
        or type(value.clipped) is not bool
        or value.clipped is not (value.selected_count > limit)
    ):
        raise HarbourInboxError("inbox_failed")
    _capture_digest(value.binding_digest, prefixed=True)
    previous = None
    for item in value.items:
        if (
            type(item) is not UploadInboxItem
            or type(item.item_handle) is not str
            or re.fullmatch(r"hi1:upload:[0-9a-f]{64}", item.item_handle) is None
            or type(item.binding_digest) is not str
            or item.binding_digest != "sha256:" + item.item_handle.removeprefix("hi1:upload:")
            or type(item.index_key) is not str
            or type(item.upload_id) is not str
            or type(item.canonical_row) is not bytes
            or not item.canonical_row
            or len(item.canonical_row) > 64 * 1024
            or previous is not None and item.item_handle <= previous
        ):
            raise HarbourInboxError("inbox_failed")
        previous = item.item_handle
    return value


def _typed_payload(captures: Mapping[str, Any], limit: int) -> dict[str, Any]:
    """Closed v3 projection. Existing v1/v2 projections and receipts remain untouched."""
    document = captures.get("document")
    if type(document) is not DocumentInboxSnapshot or type(limit) is not int or not 1 <= limit <= 100:
        raise HarbourInboxError("inbox_failed")
    for document_item in document.items:
        if type(document_item) is not DocumentInboxItem:
            raise HarbourInboxError("inbox_failed")
        if type(document_item.canonical_row) is not tuple or len(document_item.canonical_row) != 11:
            raise HarbourInboxError("inbox_failed")
        creator_subject_id = document_item.canonical_row[1]
        canonical = _document_row_bytes(document_item.canonical_row, creator_subject_id)
        if (document_item.document_id != document_item.canonical_row[0]
                or document_item.current_revision != document_item.canonical_row[3]
                or document_item.item_handle != "hi1:document:" + _unified_digest(
                    "document", document_item.binding_digest, document_item.document_id,
                )
                or document_item.binding_digest != "sha256:" + hashlib.sha256(
                    b"odysseus.harbour_inbox.document_item.v3\0"
                    + _frame(document_item.canonical_row[1].encode("utf-8")) + _frame(canonical)
                ).hexdigest()):
            raise HarbourInboxError("inbox_failed")
    upload = None
    if "upload" in captures:
        upload = _validate_upload_capture(captures["upload"], limit)
    # Reuse frozen validators for the three established producers. Typed requests
    # deliberately fail closed rather than publish an unavailable-source shape.
    base = _unified_payload({key: captures[key] for key in ("clarification", "planning", "knowledge")}, set(), limit)
    redaction = {"content": True, "identity": True, "events": True}
    items = []
    planning_receipts = {
        item.record: item.receipt for item in captures["planning"].items
    }
    for item in base["items"]:
        item = dict(item); item.pop("due")
        receipt_ref = item.pop("receipt_handle")
        if item["source"] in {"planning", "knowledge"}:
            receipt = planning_receipts.get(item["binding_digest"])
            if item["source"] == "knowledge":
                receipt = receipt_ref
            receipt_ref = None if receipt is None else {
                "source": item["source"], "item_handle": item["item_handle"],
                "binding_digest": item["binding_digest"],
                "receipt_handle": receipt.receipt_handle if item["source"] == "planning" else receipt,
            }
        item["due_state"] = "unscheduled"; item["authority"] = {"access": "read", "owner_subsystem": {"clarification": "clarification_store", "planning": "harbour_planning_store", "knowledge": "harbour_knowledge_store"}[item["source"]], "mutation_supported": item["source"] == "clarification"}
        item["receipt_ref"] = receipt_ref
        items.append(item)
    for item in document.items:
        items.append({"item_handle": item.item_handle, "source": "document", "owner": "self", "signal_type": "materialized_document", "status": "available", "required_action": "none", "due_state": "unscheduled", "authority": {"access": "read", "owner_subsystem": "document_workbench", "mutation_supported": False}, "binding_digest": item.binding_digest, "receipt_ref": None, "redaction_flags": dict(redaction), "current_revision": item.current_revision})
    if upload is not None:
        for item in upload.items:
            items.append({"item_handle": item.item_handle, "source": "upload", "owner": "self", "signal_type": "raw_upload", "status": "available", "required_action": "none", "due_state": "unscheduled", "authority": {"access": "read", "owner_subsystem": "upload_handler", "mutation_supported": False}, "binding_digest": item.binding_digest, "receipt_ref": None, "redaction_flags": dict(redaction)})
    rank = {"clarification": 0, "planning": 1, "knowledge": 2, "document": 3, "upload": 4}; items.sort(key=lambda item: (rank[item["source"]], item["item_handle"])); clipped = base["clipped"] or document.clipped or upload is not None and upload.clipped or len(items) > 300; items = items[:300]
    sources = [*base["sources"], {"source": "document", "status": "captured_revalidated", "clipped": document.clipped, "binding_digest": document.binding_digest}]
    if upload is not None:
        sources.append({"source": "upload", "status": "captured_revalidated", "clipped": upload.clipped, "binding_digest": upload.binding_digest})
    payload = {"schema": "odysseus.harbour_inbox_unified_list.v3", "items": items, "count": len(items), "sources": sources, "complete": not clipped, "clipped": clipped, "authority": "mcp_subject", "freshness": "captured_revalidated", "cross_source_atomic": False}
    while len(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")) > 32768 and payload["items"]:
        payload["items"].pop(); payload["count"] = len(payload["items"]); payload["clipped"] = True; payload["complete"] = False
    if len(json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")) > 32768:
        raise HarbourInboxError("budget_exceeded")
    return payload


def _validate_document_snapshot_subject(document: Any, subject_id: str) -> None:
    if type(document) is not DocumentInboxSnapshot or type(subject_id) is not str:
        raise HarbourInboxError("inbox_failed")
    for item in document.items:
        if type(item) is not DocumentInboxItem or type(item.canonical_row) is not tuple or len(item.canonical_row) != 11:
            raise HarbourInboxError("inbox_failed")
        _document_row_bytes(item.canonical_row, subject_id)
