"""Bounded authenticated HTTP dispatch for the seven Planning read tools."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import json
import math
import re
import time
import unicodedata
from typing import Any, Mapping
from urllib.parse import unquote

import bcrypt
from fastapi import HTTPException, Request

from core.database import ApiToken, get_db_session
from src.harbour_authority import AuthenticatedPrincipalIssuer
from src.planning_revision_store import (
    DurablePlanningRevisionStore,
    DurablePlanningStoreError,
    PlanningRevisionStore,
    PlanningRevisionStoreError,
)


MCP_PLANNING_SCOPE = "mcp:planning:read"
MCP_HARBOUR_INBOX_SCOPE = "mcp:harbour:inbox"
MCP_AUTHENTICATED_SCOPES = frozenset((MCP_PLANNING_SCOPE, MCP_HARBOUR_INBOX_SCOPE))
MCP_BODY_MAX_BYTES = 256 * 1024
MCP_BATCH_MAX = 8
MCP_JSON_DEPTH_MAX = 16
MCP_JSON_NODES_MAX = 4_096
MCP_ARGUMENTS_MAX_BYTES = 32 * 1024
MCP_RESULT_MAX_BYTES = 64 * 1024
MCP_AGGREGATE_MAX_BYTES = 256 * 1024
MCP_TOTAL_BUDGET_SECONDS = 10.0

PLANNING_TOOL_NAMES = (
    "planning_list_roadmaps",
    "planning_read_roadmap",
    "planning_search_roadmaps",
    "planning_get_context_pack",
    "planning_graph_summary",
    "planning_read_gate_definitions",
    "planning_create_agent_handoff",
)
_REPO_PROJECTION_TOOLS = frozenset(PLANNING_TOOL_NAMES[:5])
_DURABLE_DEFINITION_TOOLS = frozenset(PLANNING_TOOL_NAMES[5:])
_PROJECTION_SCHEMAS = {
    "planning_list_roadmaps": "odysseus.planning.roadmap_list.v1",
    "planning_read_roadmap": "odysseus.planning.roadmap_read.v1",
    "planning_search_roadmaps": "odysseus.planning.roadmap_search.v1",
    "planning_get_context_pack": "odysseus.planning.context_pack.v1",
    "planning_graph_summary": "odysseus.planning.graph_summary.v1",
}
_FIXED_ERRORS = {
    "unauthorized": (-32001, "Unauthorized"),
    "stale_authentication": (-32002, "Authentication changed during request"),
    "disabled": (-32000, "MCP server is disabled"),
    "invalid_request": (-32600, "Invalid Request"),
    "parse_error": (-32700, "Parse error"),
    "method_not_found": (-32601, "Method not found"),
    "invalid_params": (-32602, "Invalid params"),
    "planning_unavailable": (-32010, "Planning read is unavailable"),
    "planning_failed": (-32011, "Planning read failed"),
    "inbox_failed": (-32014, "Harbour inbox operation failed"),
    "idempotency_conflict": (-32009, "Idempotency conflict"),
    "version_conflict": (-32015, "Clarification version conflict"),
    "run_not_found": (-32016, "Clarification not found"),
    "budget_exceeded": (-32012, "Request budget exceeded"),
    "timeout": (-32013, "Request timed out"),
}


class McpDispatchFailure(Exception):
    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        self.code = code if code in _FIXED_ERRORS else "planning_failed"
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class McpAuthenticatedIdentity:
    token_id: str
    owner_alias: str
    owner_subject_id: str
    cache_generation: int
    security_action_revision: int
    scope: str = MCP_PLANNING_SCOPE


_BEARER_PROOF_ISSUER = object()


class McpBearerProof:
    """Private immutable request proof; representation never exposes material."""

    __slots__ = ("__raw", "__issuer")

    def __init__(self, raw: bytes, *, _issuer: object) -> None:
        if type(raw) is not bytes or _issuer is not _BEARER_PROOF_ISSUER:
            raise ValueError("invalid bearer proof")
        object.__setattr__(self, "_McpBearerProof__raw", bytes(raw))
        object.__setattr__(self, "_McpBearerProof__issuer", _issuer)

    def __setattr__(self, _name: str, _value: Any) -> None:
        raise AttributeError("bearer proof is immutable")

    def __init_subclass__(cls, **_kwargs: Any) -> None:
        raise TypeError("bearer proof cannot be subclassed")

    def __repr__(self) -> str:
        return "<McpBearerProof redacted>"

    def matches_hash(self, persisted_hash: Any) -> bool:
        try:
            return isinstance(persisted_hash, str) and bcrypt.checkpw(
                self.__raw, persisted_hash.encode("utf-8")
            )
        except (TypeError, ValueError):
            return False

    def _is_issued(self) -> bool:
        return self.__issuer is _BEARER_PROOF_ISSUER


def _inbox_canonical_digest(domain: str, value: object) -> str:
    """Canonical, domain-separated Inbox binding; values are ASCII JSON only."""
    try:
        prefix = ("odysseus.harbour.inbox.planning.v1\0" + domain).encode("ascii")
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, UnicodeError):
        raise McpDispatchFailure("planning_failed") from None
    return hashlib.sha256(
        len(prefix).to_bytes(4, "big") + prefix + len(encoded).to_bytes(4, "big") + encoded
    ).hexdigest()


def _closed_inbox_json(value: object, depth: int = 0) -> object:
    """Detach exact builtin JSON before it becomes an Inbox binding input."""
    if depth > 16:
        raise McpDispatchFailure("planning_failed")
    kind = type(value)
    if value is None or kind in {str, bool, int, float}:
        return value
    if kind is list:
        if len(value) > 10_000:
            raise McpDispatchFailure("planning_failed")
        return tuple(_closed_inbox_json(item, depth + 1) for item in value)
    if kind is dict:
        if len(value) > 10_000 or any(type(key) is not str for key in value):
            raise McpDispatchFailure("planning_failed")
        return {key: _closed_inbox_json(value[key], depth + 1) for key in sorted(value)}
    raise McpDispatchFailure("planning_failed")


class AppOwnedPlanningRuntime:
    """Shared app-owned Planning composition consumed by browser routes and MCP."""

    def __init__(
        self,
        *,
        repo_root: str,
        legacy_store: PlanningRevisionStore,
        durable_store: DurablePlanningRevisionStore | None,
        issuer: AuthenticatedPrincipalIssuer | None,
    ) -> None:
        self.repo_root = repo_root
        self.legacy_store = legacy_store
        self.durable_store = durable_store
        self.issuer = issuer

    def dispatch(self, name: str, arguments: Mapping[str, Any], identity: McpAuthenticatedIdentity) -> dict[str, Any]:
        from mcp_servers.planning_server import call_planning_tool_contract

        if name not in PLANNING_TOOL_NAMES:
            raise McpDispatchFailure("invalid_params")
        definition_store = self.legacy_store
        definition_owner = identity.owner_alias
        if name in _DURABLE_DEFINITION_TOOLS:
            definition_store, definition_owner = self._accepted_store(name, arguments, identity)
        payload = call_planning_tool_contract(
            name,
            arguments,
            repo_root=self.repo_root,
            preview_chars=0,
            definition_store=definition_store,
            definition_owner=definition_owner,
        )
        if not isinstance(payload, dict) or payload.get("status") == "error":
            raise McpDispatchFailure("planning_failed")
        if name in _REPO_PROJECTION_TOOLS:
            _validate_projection(name, payload)
            return {
                "schema": "odysseus.mcp.tool_result.v1",
                "tool": name,
                "origin": {"kind": "repository_projection", "source_of_truth": False},
                "payload": payload,
            }
        _validate_accepted_definition(name, arguments, payload)
        return {
            "schema": "odysseus.mcp.tool_result.v1",
            "tool": name,
            "origin": {
                "kind": "accepted_durable_definition",
                "source_of_truth": name == "planning_read_gate_definitions",
                "derived_from_accepted_durable": name == "planning_create_agent_handoff",
                "project_id": payload["project_id"],
                "roadmap_id": payload["roadmap_id"],
                "revision": payload["revision"],
                "content_hash": payload["content_hash"],
            },
            "payload": payload,
        }

    def capture_inbox_snapshot(self, *, identity: McpAuthenticatedIdentity, limit: int,
                               expected_binding_digest: str | None = None) -> "PlanningInboxSnapshot":
        """Read the accepted subject-bound Planning migration as Inbox metadata.

        This deliberately uses the runtime's existing durable issuer and
        accepted-reader path; it never consults the legacy definition store.
        """
        if type(limit) is not int or not 1 <= limit <= 100:
            raise McpDispatchFailure("invalid_params")
        try:
            # Import lazily to preserve the existing authenticated-dispatch /
            # Inbox-service dependency direction during module initialization.
            from src.harbour_inbox_service import PlanningInboxItem, PlanningInboxSnapshot
            if self.durable_store is None or self.issuer is None:
                raise McpDispatchFailure("planning_unavailable")
            context = "mcp-planning-read-v1\0" + identity.token_id + "\0" + identity.owner_subject_id
            principal = self.issuer.issue(subject_id=identity.owner_subject_id, owner_id=identity.owner_alias,
                auth_context_digest="sha256:" + hashlib.sha256(context.encode("utf-8")).hexdigest(), ttl_seconds=60)
            owner, batch, records, heads = self.durable_store.read_subject_bound_accepted_migration_records(
                principal, project_id="harbour-one",
            )
            if (owner != "fuzzy" or type(batch) is not dict or type(records) is not list
                    or type(heads) is not list or len(records) > 10_000 or len(heads) > 10_000):
                raise McpDispatchFailure("planning_failed")
            batch = _closed_inbox_json(batch)
            # The durable reader has already authenticated its full accepted
            # chain.  Re-encode only a closed, scalar boundary here; never use
            # repr(), attribute fallback, or a partial prefix as a binding.
            head_by_roadmap: dict[str, tuple[int, int, str, str]] = {}
            canonical_heads: list[dict[str, Any]] = []
            required_head_fields = {
                "owner_id", "project_id", "roadmap_id", "accepted_revision_id",
                "accepted_revision", "accepted_content_sha256", "current_revision_id",
                "current_revision", "current_content_sha256", "approved_revision_id",
                "approved_revision", "approved_content_sha256", "generation",
            }
            for head in heads:
                if type(head) is not dict or set(head) != required_head_fields:
                    raise McpDispatchFailure("planning_failed")
                roadmap_id = head["roadmap_id"]
                current, approved, generation = (
                    head["current_revision"], head["approved_revision"], head["generation"],
                )
                accepted = head["accepted_revision"]
                if (head["owner_id"] != "fuzzy" or head["project_id"] != "harbour-one"
                        or type(roadmap_id) is not str or not roadmap_id
                        or type(accepted) is not int or type(current) is not int or type(approved) is not int
                        or type(generation) is not int
                        or type(accepted) is bool or type(current) is bool or type(approved) is bool
                        or type(generation) is bool or not 1 <= generation <= 2**63 - 1
                        or accepted != 1 or approved < accepted or current < approved
                        or not all(type(head[key]) is str and head[key]
                                   for key in ("accepted_revision_id", "accepted_content_sha256",
                                               "current_revision_id", "current_content_sha256",
                                               "approved_revision_id", "approved_content_sha256"))
                        or roadmap_id in head_by_roadmap):
                    raise McpDispatchFailure("planning_failed")
                head_by_roadmap[roadmap_id] = (current, approved, head["current_revision_id"],
                                               head["approved_revision_id"])
                canonical_heads.append({key: head[key] for key in sorted(required_head_fields)})
            record_refs: dict[str, list[str]] = {key: [] for key in head_by_roadmap}
            for record in records:
                if (type(record) is not tuple or len(record) != 3
                        or type(record[0]) is not str or record[0] != "fuzzy"
                        or type(record[1]) is not dict or type(record[2]) is not str
                        or not record[2].startswith("durable:")):
                    raise McpDispatchFailure("planning_failed")
                document = _closed_inbox_json(record[1])
                project = document.get("project")
                roadmaps = document.get("roadmaps")
                if (type(project) is not dict or project.get("project_id") != "harbour-one"
                        or type(roadmaps) is not tuple or len(roadmaps) != 1
                        or type(roadmaps[0]) is not dict):
                    raise McpDispatchFailure("planning_failed")
                roadmap_id = roadmaps[0].get("roadmap_id")
                if type(roadmap_id) is not str or roadmap_id not in record_refs:
                    raise McpDispatchFailure("planning_failed")
                record_refs[roadmap_id].append(record[2])
            if not head_by_roadmap or any(not refs for refs in record_refs.values()):
                raise McpDispatchFailure("planning_failed")
            source_binding = _inbox_canonical_digest("source", {
                "batch": batch,
                "heads": sorted(canonical_heads, key=lambda value: value["roadmap_id"]),
                "owner": identity.owner_subject_id,
            })
            normalized: list[PlanningInboxItem] = []
            for roadmap_id, (current, approved, current_id, approved_id) in head_by_roadmap.items():
                if current > approved:
                    record = _inbox_canonical_digest("item", {
                        "approved_revision_id": approved_id, "current_revision_id": current_id,
                        "owner": identity.owner_subject_id, "roadmap_id": roadmap_id,
                        "records": sorted(record_refs[roadmap_id]), "source_binding": source_binding,
                    })
                    normalized.append(PlanningInboxItem(current, approved, record))
            normalized.sort(key=lambda item: (item.current_revision, item.approved_revision, item.record))
            clipped = len(normalized) > limit
            items = tuple(normalized[:limit])
            binding = _inbox_canonical_digest("binding", {
                "batch": batch, "heads": sorted(canonical_heads, key=lambda value: value["roadmap_id"]),
                "items": [
                    {"approved_revision": item.approved_revision, "current_revision": item.current_revision,
                     "record": item.record}
                    for item in items
                ], "owner": identity.owner_subject_id, "source_binding": source_binding,
            })
            if expected_binding_digest is not None and expected_binding_digest != binding:
                raise McpDispatchFailure("stale_authentication")
            return PlanningInboxSnapshot(
                _inbox_canonical_digest("owner", {"subject": identity.owner_subject_id}),
                _inbox_canonical_digest("batch", batch),
                _inbox_canonical_digest("heads", sorted(canonical_heads, key=lambda value: value["roadmap_id"])),
                items, clipped, binding,
            )
        except McpDispatchFailure:
            raise
        except DurablePlanningStoreError as exc:
            if exc.code == "not_found":
                raise McpDispatchFailure("planning_unavailable") from None
            raise McpDispatchFailure("planning_failed") from None
        except PlanningRevisionStoreError:
            raise McpDispatchFailure("planning_failed") from None
        except Exception:
            raise McpDispatchFailure("planning_failed") from None
    def _accepted_store(
        self,
        name: str,
        arguments: Mapping[str, Any],
        identity: McpAuthenticatedIdentity,
    ) -> tuple[PlanningRevisionStore, str]:
        if self.durable_store is None or self.issuer is None:
            raise McpDispatchFailure("planning_unavailable")
        project_id = arguments.get("project_id")
        context = "mcp-planning-read-v1\0" + identity.token_id + "\0" + identity.owner_subject_id
        principal = self.issuer.issue(
            subject_id=identity.owner_subject_id,
            owner_id=identity.owner_alias,
            auth_context_digest="sha256:" + hashlib.sha256(context.encode("utf-8")).hexdigest(),
            ttl_seconds=60,
        )
        try:
            stable_owner, _batch, records, head_bindings = self.durable_store.read_subject_bound_accepted_migration_records(
                principal, project_id=project_id,
            )
            if stable_owner != "fuzzy":
                raise McpDispatchFailure("planning_unavailable")
            return PlanningRevisionStore(
                records,
                origin_state="live",
                origin_reason="h1r03_durable_migration_accepted",
                trusted_head_bindings=head_bindings,
            ), stable_owner
        except McpDispatchFailure:
            raise
        except (PlanningRevisionStoreError, TypeError, ValueError):
            raise McpDispatchFailure("planning_unavailable") from None


def capture_mcp_bearer_proof(request: Request) -> McpBearerProof:
    """Copy the middleware-authenticated bearer without exposing it elsewhere."""
    try:
        state = request.state
        authorization = request.headers.get("authorization")
        if state.api_token is not True or not isinstance(authorization, str):
            raise ValueError
        scheme, separator, raw = authorization.partition(" ")
        if scheme != "Bearer" or separator != " " or not raw.startswith("ody_"):
            raise ValueError
        return McpBearerProof(raw.encode("utf-8"), _issuer=_BEARER_PROOF_ISSUER)
    except Exception:
        raise HTTPException(status_code=401, detail="MCP authentication failed") from None


def authenticate_mcp_request(
    request: Request,
    bearer_proof: McpBearerProof | None = None,
) -> McpAuthenticatedIdentity:
    """Bind middleware authentication to the persisted exact-scope token row."""
    try:
        state = request.state
        if (
            state.api_token is not True
            or tuple(state.api_token_scopes or ()) not in tuple((scope,) for scope in MCP_AUTHENTICATED_SCOPES)
            or "authorization" not in request.headers
            or getattr(state, "api_token_cache_generation", None)
            != getattr(request.app.state, "_token_cache_generation", None)
        ):
            raise ValueError
        token_id = state.api_token_id
        scope = tuple(state.api_token_scopes)[0]
        cached_subject = state.api_token_owner_subject_id
        cache_generation = state.api_token_cache_generation
        proof = bearer_proof if type(bearer_proof) is McpBearerProof else capture_mcp_bearer_proof(request)
        state._mcp_bearer_proof = proof
        manager = request.app.state.auth_manager
        security_revision = manager.security_action_revision()
        with manager.guard_current_admin_subject(cached_subject) as current_alias:
            with get_db_session() as db:
                row = db.query(ApiToken).filter(ApiToken.id == token_id).first()
                if (
                    row is None
                    or row.is_active is not True
                    or row.scopes != scope
                    or row.owner_subject_id != cached_subject
                    or row.owner != current_alias
                    or getattr(state, "api_token_owner", None) != current_alias
                    or not isinstance(row.owner, str)
                    or not row.owner
                    or not proof.matches_hash(row.token_hash)
                ):
                    raise ValueError
                owner_alias = row.owner
        if (
            getattr(request.app.state, "_token_cache_generation", None) != cache_generation
            or getattr(request.app.state, "_token_cache_dirty", True)
            or manager.security_action_revision() != security_revision
        ):
            raise ValueError
        return McpAuthenticatedIdentity(
            token_id=token_id,
            owner_alias=owner_alias,
            owner_subject_id=cached_subject,
            cache_generation=cache_generation,
            security_action_revision=security_revision,
            scope=scope,
        )
    except Exception:
        raise HTTPException(status_code=401, detail="MCP authentication failed") from None


def parse_json_body(raw: bytes) -> Any:
    if type(raw) is not bytes or len(raw) > MCP_BODY_MAX_BYTES:
        raise McpDispatchFailure("budget_exceeded")
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
        raise McpDispatchFailure("parse_error") from None
    _check_tree(value)
    return value


async def read_bounded_body(request: Request, started: float) -> bytes:
    raw = bytearray()
    try:
        async with asyncio.timeout(max(0.001, MCP_TOTAL_BUDGET_SECONDS - (time.monotonic() - started))):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > MCP_BODY_MAX_BYTES:
                    raise McpDispatchFailure("budget_exceeded")
                raw.extend(chunk)
    except TimeoutError:
        raise McpDispatchFailure("timeout") from None
    return bytes(raw)


async def dispatch_http_payload(
    payload: Any,
    *,
    identity: McpAuthenticatedIdentity,
    runtime: AppOwnedPlanningRuntime,
    started: float,
    audit: Any = None,
    fence: Any = None,
    planning_reads_enabled: bool = True,
) -> dict[str, Any] | list[dict[str, Any]] | None:
    is_batch = type(payload) is list
    messages = payload if is_batch else [payload]
    if is_batch and (not messages or len(messages) > MCP_BATCH_MAX):
        raise McpDispatchFailure("budget_exceeded")
    if identity.scope == MCP_HARBOUR_INBOX_SCOPE and _contains_inbox_action(messages):
        # Action notifications and all action-containing batches are rejected
        # before the first service call, so no prefix of a request can commit.
        if is_batch or any("id" not in message for message in messages if type(message) is dict):
            raise McpDispatchFailure("invalid_request")
    replies: list[dict[str, Any]] = []
    authority_finalized = False
    unified_read_seen = False
    for message in messages:
        _run_fence(fence, _message_method(message))
        if time.monotonic() - started >= MCP_TOTAL_BUDGET_SECONDS:
            raise McpDispatchFailure("timeout")
        dispatched = await _dispatch_message(
            message, identity, runtime, started,
            planning_reads_enabled=planning_reads_enabled,
            fence=fence,
        )
        finalized = type(dispatched) is _AuthorityFinalizedReply
        reply = dispatched.reply if finalized else dispatched
        authority_finalized = authority_finalized or finalized
        unified_read_seen = unified_read_seen or _is_unified_inbox_read(message, identity)
        if not finalized:
            _run_fence(fence, _message_method(message))
        if reply is not None:
            replies.append(reply)
        if callable(audit) and not finalized:
            audit(message)
    if not replies:
        return None
    if is_batch and len(_encode(replies)) > MCP_AGGREGATE_MAX_BYTES:
        raise McpDispatchFailure("budget_exceeded")
    public_result = replies if is_batch else replies[0]
    # A committed action is final per RPC, but cannot suppress final HTTP
    # authority/deadline fences for a mixed batch that also returned data.
    return _AuthorityFinalizedPayload(public_result) if authority_finalized and not unified_read_seen else public_result


async def _dispatch_message(
    message: Any,
    identity: McpAuthenticatedIdentity,
    runtime: AppOwnedPlanningRuntime,
    started: float,
    *,
    planning_reads_enabled: bool,
    fence: Any,
) -> dict[str, Any] | None:
    if type(message) is not dict or set(message) - {"jsonrpc", "id", "method", "params"}:
        return _rpc_error(None, "invalid_request")
    message_id = message.get("id")
    if "id" in message and not _valid_id(message_id):
        return _rpc_error(None, "invalid_request")
    if message.get("jsonrpc") != "2.0" or type(message.get("method")) is not str:
        return _rpc_error(message_id if "id" in message else None, "invalid_request")
    method = message["method"]
    _run_fence(fence, method)
    notification = "id" not in message
    params = message.get("params", {})
    inbox_error_type: type[Exception] | None = None
    if type(params) is not dict:
        return None if notification else _rpc_error(message_id, "invalid_params")
    try:
        if identity.scope == MCP_HARBOUR_INBOX_SCOPE:
            from src.harbour_inbox_service import HarbourInboxError

            inbox_error_type = HarbourInboxError
        if method.startswith("notifications/"):
            return None
        if method == "initialize":
            if set(params) - {"protocolVersion", "capabilities", "clientInfo"}:
                raise McpDispatchFailure("invalid_params")
            result = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "odysseus", "version": "0.1.0"},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            if params:
                raise McpDispatchFailure("invalid_params")
            if identity.scope == MCP_HARBOUR_INBOX_SCOPE:
                result = {"tools": list(runtime.tool_contracts())}
            else:
                if not planning_reads_enabled:
                    raise McpDispatchFailure("planning_unavailable")
                from mcp_servers.planning_server import build_planning_tool_contracts

                result = {"tools": list(build_planning_tool_contracts())}
        elif method == "tools/call":
            if set(params) != {"name", "arguments"}:
                raise McpDispatchFailure("invalid_params")
            name, arguments = params["name"], params["arguments"]
            if type(name) is not str or type(arguments) is not dict:
                raise McpDispatchFailure("invalid_params")
            inbox_outcome_type: type[Any] | None = None
            if identity.scope == MCP_HARBOUR_INBOX_SCOPE:
                from src.harbour_inbox_service import HARBOUR_INBOX_TOOL_NAMES, HarbourInboxOutcome

                inbox_outcome_type = HarbourInboxOutcome
                if name not in HARBOUR_INBOX_TOOL_NAMES:
                    raise McpDispatchFailure("invalid_params")
            elif name not in PLANNING_TOOL_NAMES:
                raise McpDispatchFailure("invalid_params")
            elif not planning_reads_enabled:
                raise McpDispatchFailure("planning_unavailable")
            if len(_encode(arguments)) > MCP_ARGUMENTS_MAX_BYTES:
                raise McpDispatchFailure("budget_exceeded")
            if name == "planning_read_roadmap":
                preview_chars = arguments.get("include_raw_preview_chars", 0)
                if type(preview_chars) is not int or preview_chars != 0:
                    raise McpDispatchFailure("invalid_params")
            _run_fence(fence, method)
            work = asyncio.create_task(asyncio.to_thread(runtime.dispatch, name, arguments, identity))
            try:
                remaining = MCP_TOTAL_BUDGET_SECONDS - (time.monotonic() - started)
                payload = await asyncio.wait_for(asyncio.shield(work), timeout=max(0.001, remaining))
            except TimeoutError:
                # Wait for the synchronous filesystem operation to finish before
                # returning. This avoids abandoned worker threads; a blocked
                # filesystem cancellation guarantee remains explicitly outside
                # this foundation's verified claim.
                payload = await _await_thread_to_completion(work)
                if (inbox_outcome_type is None or type(payload) is not inbox_outcome_type
                        or payload.authority_finalized is not True):
                    raise McpDispatchFailure("timeout") from None
            except asyncio.CancelledError:
                await _await_thread_to_completion(work)
                raise
            is_inbox_outcome = (
                inbox_outcome_type is not None and type(payload) is inbox_outcome_type
            )
            authority_finalized = is_inbox_outcome and payload.authority_finalized is True
            if not authority_finalized:
                _run_fence(fence, method)
            # Every exact outcome is a transport wrapper.  A unified read is
            # deliberately non-finalized, but it still carries its payload.
            public_payload = payload.payload if is_inbox_outcome else payload
            encoded_payload = _encode(public_payload)
            if len(encoded_payload) > MCP_RESULT_MAX_BYTES:
                raise McpDispatchFailure("budget_exceeded")
            result = {
                "content": [{"type": "text", "text": encoded_payload.decode("utf-8")}],
                "isError": False,
            }
        else:
            if notification:
                return None
            return _rpc_error(message_id, "method_not_found")
    except McpDispatchFailure as exc:
        if notification:
            return None
        return _rpc_error(message_id, exc.code)
    except Exception as exc:
        if inbox_error_type is not None and isinstance(exc, inbox_error_type):
            if notification:
                return None
            return _rpc_error(
                message_id,
                exc.code if exc.code in _FIXED_ERRORS else "inbox_failed",
            )
        if notification:
            return None
        return _rpc_error(message_id, "planning_failed")
    if notification:
        return None
    reply = {"jsonrpc": "2.0", "id": message_id, "result": result}
    if len(_encode(reply)) > MCP_RESULT_MAX_BYTES:
        return _rpc_error(message_id, "budget_exceeded")
    if method == "tools/call" and locals().get("authority_finalized", False):
        return _AuthorityFinalizedReply(reply)
    return reply


def dispatch_failure_response(code: str, *, message_id: Any = None) -> dict[str, Any]:
    return _rpc_error(message_id, code)


async def _await_thread_to_completion(work: asyncio.Task[Any]) -> Any:
    """Join a synchronous worker fully, even when the request is cancelled again."""
    current = asyncio.current_task()
    cancellation_seen = False
    while True:
        try:
            result = await asyncio.shield(work)
            if cancellation_seen:
                raise asyncio.CancelledError
            return result
        except asyncio.CancelledError:
            if work.done():
                if cancellation_seen:
                    raise
                raise
            cancellation_seen = True
            if current is not None:
                current.uncancel()


@dataclass(frozen=True, slots=True)
class _AuthorityFinalizedReply:
    reply: dict[str, Any]
    authority_finalized: bool = True


@dataclass(frozen=True, slots=True)
class _AuthorityFinalizedPayload:
    payload: dict[str, Any]


def _contains_inbox_action(messages: list[Any]) -> bool:
    try:
        from src.harbour_inbox_service import HARBOUR_INBOX_ACTION_TOOLS
    except Exception:
        raise McpDispatchFailure("planning_failed") from None
    for message in messages:
        if type(message) is not dict or message.get("method") != "tools/call":
            continue
        params = message.get("params")
        if type(params) is dict and params.get("name") in HARBOUR_INBOX_ACTION_TOOLS:
            return True
    return False


def _is_unified_inbox_read(message: Any, identity: McpAuthenticatedIdentity) -> bool:
    if identity.scope != MCP_HARBOUR_INBOX_SCOPE or type(message) is not dict:
        return False
    params = message.get("params")
    return (
        message.get("method") == "tools/call"
        and type(params) is dict
        and params.get("name") == "harbour_inbox_list"
        and type(params.get("arguments")) is dict
        and params["arguments"].get("view") in {"unified", "unified_receipts", "receipt"}
    )


def _validate_projection(name: str, payload: dict[str, Any]) -> None:
    exact = {
        "planning_list_roadmaps": {"schema", "read_only", "writes_supported", "query", "filters", "summary", "roadmaps"},
        "planning_read_roadmap": {"schema", "read_only", "writes_supported", "source", "roadmap", "logical_ids", "source_refs", "dependency_hints", "slices", "gates", "validation", "raw_json_preview", "raw_json_truncated", "absolute_paths_visible"},
        "planning_search_roadmaps": {"schema", "read_only", "query", "summary", "results"},
        "planning_get_context_pack": {"schema", "read_only", "writes_supported", "roadmap_ref", "task", "active_node_id", "roadmap", "slices", "gates", "dependency_hints", "source_refs", "roadmap_lens", "memory_included", "memory_source", "memory", "memory_summary", "validation", "raw_content_included", "absolute_paths_visible", "clipped", "payload_bytes"},
        "planning_graph_summary": {"schema", "read_only", "writes_performed", "roadmap_ref", "depth", "nodes", "edges", "summary", "source_refs", "raw_content_included", "absolute_paths_visible"},
    }[name]
    try:
        if type(payload) is not dict or set(payload) != exact:
            raise ValueError
        _require_text(payload["schema"], 80)
        if payload["schema"] != _PROJECTION_SCHEMAS[name] or payload["read_only"] is not True:
            raise ValueError
        if name in {"planning_list_roadmaps", "planning_read_roadmap", "planning_get_context_pack"} and payload["writes_supported"] is not False:
            raise ValueError
        if name == "planning_graph_summary" and payload["writes_performed"] is not False:
            raise ValueError

        if name == "planning_list_roadmaps":
            _require_text(payload["query"], 200)
            filters = payload["filters"]
            if type(filters) is not dict or set(filters) != {"kind", "status"}:
                raise ValueError
            _require_text(filters["kind"], 100)
            _require_text(filters["status"], 80)
            rows = payload["roadmaps"]
            _validate_record_rows(rows, 100)
            summary = payload["summary"]
            if type(summary) is not dict or set(summary) != {"total_roadmaps", "total_matches", "returned", "clipped"}:
                raise ValueError
            for key in ("total_roadmaps", "total_matches", "returned"):
                _require_int(summary[key], 0, None)
            if type(summary["clipped"]) is not bool or summary["returned"] != len(rows) or summary["total_roadmaps"] < summary["total_matches"] or summary["total_matches"] < summary["returned"]:
                raise ValueError
            if summary["clipped"] != (summary["total_matches"] > summary["returned"]):
                raise ValueError

        elif name == "planning_search_roadmaps":
            _require_text(payload["query"], 200)
            rows = payload["results"]
            _validate_record_rows(rows, 100)
            summary = payload["summary"]
            if type(summary) is not dict or set(summary) != {"matches", "returned", "clipped"}:
                raise ValueError
            _require_int(summary["matches"], 0, None)
            _require_int(summary["returned"], 0, 100)
            if type(summary["clipped"]) is not bool or summary["returned"] != len(rows) or summary["matches"] < summary["returned"] or summary["clipped"] != (summary["matches"] > summary["returned"]):
                raise ValueError

        elif name == "planning_read_roadmap":
            if payload["raw_json_preview"] != "" or payload["raw_json_truncated"] is not False or payload["absolute_paths_visible"] is not False:
                raise ValueError
            _validate_record_summary(payload["source"])
            _validate_roadmap_projection(payload["roadmap"])
            ids = payload["logical_ids"]
            if type(ids) is not dict or set(ids) != {"project_id", "roadmap_id", "derived"}:
                raise ValueError
            _require_project_id(ids["project_id"])
            _require_project_id(ids["roadmap_id"])
            if type(ids["derived"]) is not bool or ids["project_id"] != payload["roadmap"]["project_id"] or ids["roadmap_id"] != payload["roadmap"]["roadmap_id"]:
                raise ValueError
            _validate_string_array(payload["source_refs"], 24, 240, safe_refs=True)
            _validate_string_array(payload["dependency_hints"], 24, 240, safe_refs=True)
            _validate_slice_gate_lists(payload["slices"], payload["gates"])
            _validate_validation(payload["validation"])

        elif name == "planning_get_context_pack":
            if payload["raw_content_included"] is not False or payload["absolute_paths_visible"] is not False or type(payload["clipped"]) is not bool:
                raise ValueError
            _require_int(payload["payload_bytes"], 0, 65_536)
            ref = payload["roadmap_ref"]
            if type(ref) is not dict or set(ref) != {"source_id", "source_ref", "source_hash", "project_id", "roadmap_id", "ids_derived"}:
                raise ValueError
            _require_text(ref["source_id"], 120)
            _require_repo_ref(ref["source_ref"], 240)
            _require_hash(ref["source_hash"])
            _require_project_id(ref["project_id"])
            _require_project_id(ref["roadmap_id"])
            if type(ref["ids_derived"]) is not bool:
                raise ValueError
            _require_text(payload["task"], 500)
            _require_text(payload["active_node_id"], 120)
            roadmap = payload["roadmap"]
            _validate_roadmap_projection(roadmap)
            if roadmap["project_id"] != ref["project_id"] or roadmap["roadmap_id"] != ref["roadmap_id"]:
                raise ValueError
            _validate_slice_gate_lists(payload["slices"], payload["gates"])
            _validate_string_array(payload["source_refs"], 24, 240, safe_refs=True)
            _validate_string_array(payload["dependency_hints"], 24, 240, safe_refs=True)
            _validate_validation(payload["validation"])
            _validate_lens(payload["roadmap_lens"])
            # HTTP MCP cannot request or expose memory, even if a private source
            # happens to provide a structurally valid preview.
            if payload["memory_included"] is not False or payload["memory_source"] != "not_requested" or payload["memory"] != []:
                raise ValueError
            summary = payload["memory_summary"]
            if type(summary) is not dict or set(summary) != {"requested", "source", "candidates", "accepted", "matched", "deduplicated", "returned", "rejected", "truncated", "incomplete"}:
                raise ValueError
            if summary != {"requested": False, "source": "not_requested", "candidates": 0, "accepted": 0, "matched": 0, "deduplicated": 0, "returned": 0, "rejected": 0, "truncated": False, "incomplete": False}:
                raise ValueError
            actual_payload_bytes = len(json.dumps(
                payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8", "strict"))
            if payload["payload_bytes"] != actual_payload_bytes:
                raise ValueError

        else:
            if payload["raw_content_included"] is not False or payload["absolute_paths_visible"] is not False:
                raise ValueError
            ref = payload["roadmap_ref"]
            if type(ref) is not dict or set(ref) != {"project_id", "roadmap_id", "derived"}:
                raise ValueError
            _require_project_id(ref["project_id"])
            _require_project_id(ref["roadmap_id"])
            if type(ref["derived"]) is not bool:
                raise ValueError
            _require_int(payload["depth"], 0, 3)
            nodes, edges = payload["nodes"], payload["edges"]
            if type(nodes) is not list or len(nodes) > 100 or type(edges) is not list or len(edges) > 200:
                raise ValueError
            for node in nodes:
                if type(node) is not dict or set(node) != {"id", "kind", "label"}:
                    raise ValueError
                _require_text(node["id"], 160)
                if node["kind"] not in {"roadmap", "slice", "gate"}:
                    raise ValueError
                _require_text(node["label"], 180)
            for edge in edges:
                if type(edge) is not dict or set(edge) != {"id", "source_id", "target_id", "kind"}:
                    raise ValueError
                _require_text(edge["id"], 160)
                _require_text(edge["source_id"], 160)
                _require_text(edge["target_id"], 160)
                if edge["kind"] not in {"contains", "depends_on", "has_gate", "blocks"}:
                    raise ValueError
            summary = payload["summary"]
            if type(summary) is not dict or set(summary) != {"nodes", "edges", "clipped"}:
                raise ValueError
            _require_int(summary["nodes"], 0, 100)
            _require_int(summary["edges"], 0, 200)
            if summary["nodes"] != len(nodes) or summary["edges"] != len(edges) or type(summary["clipped"]) is not bool:
                raise ValueError
            _validate_string_array(payload["source_refs"], 24, 240, safe_refs=True)
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise McpDispatchFailure("planning_failed") from None


def _require_text(value: Any, maximum: int, *, allow_empty: bool = True) -> None:
    if type(value) is not str or len(value) > maximum or "\x00" in value or (not allow_empty and not value):
        raise ValueError
    try:
        value.encode("utf-8", "strict")
    except UnicodeEncodeError:
        raise ValueError from None


def _require_int(value: Any, minimum: int, maximum: int | None) -> None:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError


def _require_project_id(value: Any) -> None:
    _require_text(value, 64, allow_empty=False)
    if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", value) is None:
        raise ValueError


def _require_repo_ref(value: Any, maximum: int) -> None:
    _validate_public_reference(value, maximum, allow_logical=False)


def _require_hash(value: Any) -> None:
    if type(value) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ValueError


def _validate_string_array(value: Any, maximum_count: int, maximum_length: int, *, safe_refs: bool = False) -> None:
    if type(value) is not list or len(value) > maximum_count:
        raise ValueError
    for item in value:
        if safe_refs:
            _require_public_ref(item, maximum_length)
        else:
            _require_text(item, maximum_length)


def _require_public_ref(value: Any, maximum: int) -> None:
    _validate_public_reference(value, maximum, allow_logical=True)


def _validate_public_reference(value: Any, maximum: int, *, allow_logical: bool) -> None:
    _require_text(value, maximum, allow_empty=False)
    if value != value.strip():
        raise ValueError
    normalized = unicodedata.normalize("NFKC", unquote(value, errors="strict")).replace("\\", "/")
    if (not normalized or len(normalized) > maximum or any(ord(char) < 32 for char in normalized)
            or any(char in normalized for char in "*?[]")
            or normalized.startswith(("/", "~"))
            or re.match(r"^[A-Za-z]:", normalized)):
        raise ValueError
    logical = re.fullmatch(r"(?:project|roadmap|gate|slice|todo|repo-plan):([A-Za-z0-9._/-]+)", normalized)
    if logical is not None:
        if not allow_logical:
            raise ValueError
        if any(part in {"", ".", ".."} for part in logical.group(1).split("/")):
            raise ValueError
        return
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", normalized) or ":" in normalized:
        raise ValueError
    if len(normalized) > maximum or any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise ValueError


def _require_count_map(value: Any, maximum: int) -> None:
    if type(value) is not dict:
        raise ValueError
    for key, count in value.items():
        _require_text(key, 80, allow_empty=False)
        _require_int(count, 0, maximum)


def _require_score(value: Any) -> None:
    if type(value) not in {int, float} or not -1_000_000 <= value <= 1_000_000:
        raise ValueError
    if type(value) is float and not math.isfinite(value):
        raise ValueError


def _validate_record_rows(rows: Any, maximum: int) -> None:
    if type(rows) is not list or len(rows) > maximum:
        raise ValueError
    for row in rows:
        _validate_record_summary(row)


def _validate_record_summary(value: dict[str, Any]) -> None:
    fields = {"source_id", "source_ref", "source_hash", "project_id", "roadmap_id", "ids_derived", "title", "goal", "kind", "status", "size_bytes", "valid_json", "preview", "repo_relative", "absolute_path_recorded"}
    if type(value) is not dict or set(value) != fields:
        raise ValueError
    _require_text(value["source_id"], 120, allow_empty=False)
    _require_repo_ref(value["source_ref"], 240)
    _require_hash(value["source_hash"])
    _require_project_id(value["project_id"])
    _require_project_id(value["roadmap_id"])
    if type(value["ids_derived"]) is not bool:
        raise ValueError
    for key, maximum in (("title", 200), ("goal", 300), ("kind", 120), ("status", 80), ("preview", 0)):
        _require_text(value[key], maximum)
    _require_int(value["size_bytes"], 0, 2_000_000)
    if type(value["valid_json"]) is not bool or value["repo_relative"] is not True or value["absolute_path_recorded"] is not False:
        raise ValueError


def _validate_roadmap_projection(value: dict[str, Any]) -> None:
    fields = {"schema_version", "kind", "project_id", "roadmap_id", "plan_id", "title", "goal", "status", "revision", "created_at", "updated_at"}
    if type(value) is not dict or set(value) != fields:
        raise ValueError
    _require_int(value["schema_version"], 1, 1)
    _require_text(value["kind"], 120, allow_empty=False)
    _require_project_id(value["project_id"])
    _require_project_id(value["roadmap_id"])
    _require_text(value["plan_id"], 120)
    _require_text(value["title"], 200)
    _require_text(value["goal"], 500)
    _require_text(value["status"], 80)
    if value["revision"] is not None:
        _require_int(value["revision"], 1, None)
    _require_text(value["created_at"], 40)
    _require_text(value["updated_at"], 40)


def _validate_slice_gate_lists(slices: Any, gates: Any) -> None:
    slice_keys = {"id", "title", "objective", "class", "status", "owner", "dependencies", "gates", "source_refs"}
    gate_keys = {"id", "class", "status", "decision_needed", "blocks", "risk_if_bypassed"}
    if type(slices) is not list or len(slices) > 24 or type(gates) is not list or len(gates) > 24:
        raise ValueError
    for item in slices:
        if type(item) is not dict or set(item) != slice_keys:
            raise ValueError
        _require_text(item["id"], 120)
        _require_text(item["title"], 180)
        _require_text(item["objective"], 360)
        for key in ("class", "status", "owner"):
            _require_text(item[key], 80)
        _validate_string_array(item["dependencies"], 12, 180)
        _validate_string_array(item["gates"], 12, 180)
        _validate_string_array(item["source_refs"], 12, 240, safe_refs=True)
    for item in gates:
        if type(item) is not dict or set(item) != gate_keys:
            raise ValueError
        _require_text(item["id"], 120)
        _require_text(item["class"], 80)
        _require_text(item["status"], 80)
        _require_text(item["decision_needed"], 360)
        _validate_string_array(item["blocks"], 12, 180)
        _require_text(item["risk_if_bypassed"], 240)


def _validate_lens(lens: Any) -> None:
    if type(lens) is not dict:
        raise ValueError
    base = {"schema", "available", "projection", "evidence_ref", "evidence_hash", "nodes", "edges", "aggregates", "claimable_node_ids", "clipped", "incomplete", "source_of_truth", "raw_content_included"}
    available = lens.get("available")
    extra = {"status", "active_node_id"} if available is True else {"reason", "slice_count", "gate_count", "dependency_count"}
    if set(lens) != base | extra or lens.get("schema") != "odysseus.planning.roadmap_lens_summary.v1":
        raise ValueError
    if type(available) is not bool or lens["source_of_truth"] is not False or lens["raw_content_included"] is not False:
        raise ValueError
    _require_text(lens["projection"], 80, allow_empty=False)
    _require_repo_ref(lens["evidence_ref"], 240)
    _require_hash(lens["evidence_hash"])
    for key in ("clipped", "incomplete"):
        if type(lens[key]) is not bool:
            raise ValueError
    if lens["clipped"] and not lens["incomplete"]:
        raise ValueError
    for key, cap in (("nodes", 72), ("edges", 144), ("aggregates", 24)):
        if type(lens[key]) is not list or len(lens[key]) > cap:
            raise ValueError
    for node in lens["nodes"]:
        if type(node) is not dict or set(node) != {"node_id", "label", "node_type", "score"}:
            raise ValueError
        _require_text(node["node_id"], 80, allow_empty=False)
        _require_text(node["label"], 80)
        _require_text(node["node_type"], 80, allow_empty=False)
        _require_score(node["score"])
    for edge in lens["edges"]:
        if type(edge) is not dict or set(edge) != {"edge_id", "source_id", "target_id", "edge_type", "score"}:
            raise ValueError
        for key in ("edge_id", "source_id", "target_id", "edge_type"):
            _require_text(edge[key], 80, allow_empty=False)
        _require_score(edge["score"])
    for aggregate in lens["aggregates"]:
        if type(aggregate) is not dict or set(aggregate) != {"aggregate_id", "label", "count"}:
            raise ValueError
        _require_text(aggregate["aggregate_id"], 80, allow_empty=False)
        _require_text(aggregate["label"], 80)
        _require_int(aggregate["count"], 0, None)
    _validate_string_array(lens["claimable_node_ids"], 72, 80)
    if available:
        _require_text(lens["status"], 80)
        _require_text(lens["active_node_id"], 80)
    else:
        _require_text(lens["reason"], 120, allow_empty=False)
        for key in ("slice_count", "gate_count", "dependency_count"):
            _require_int(lens[key], 0, None)
        if lens["nodes"] or lens["edges"] or lens["aggregates"] or lens["claimable_node_ids"]:
            raise ValueError


def _validate_validation(value: Any) -> None:
    base = {"schema", "read_only", "writes_performed", "valid", "errors", "warnings", "summary"}
    if type(value) is not dict or frozenset(value) not in {frozenset(base), frozenset(base | {"details_truncated"})}:
        raise ValueError
    if value["schema"] != "odysseus.planning.roadmap_validation.v1" or value["read_only"] is not True or value["writes_performed"] is not False or type(value["valid"]) is not bool:
        raise ValueError
    details_truncated = "details_truncated" in value
    if details_truncated and value["details_truncated"] is not True:
        raise ValueError
    for field in ("errors", "warnings"):
        items = value[field]
        if type(items) is not list or len(items) > 200:
            raise ValueError
        for item in items:
            if type(item) is not dict or set(item) != {"code", "field", "message"}:
                raise ValueError
            _require_text(item["code"], 80, allow_empty=False)
            _require_text(item["field"], 120)
            _require_text(item["message"], 180)
    summary = value["summary"]
    if type(summary) is not dict or set(summary) != {"errors", "warnings"}:
        raise ValueError
    _require_int(summary["errors"], 0, None)
    _require_int(summary["warnings"], 0, None)
    if details_truncated:
        if summary["errors"] < len(value["errors"]) or summary["warnings"] < len(value["warnings"]):
            raise ValueError
    elif summary["errors"] != len(value["errors"]) or summary["warnings"] != len(value["warnings"]):
        raise ValueError
    if value["valid"] != (summary["errors"] == 0):
        raise ValueError


def _validate_accepted_definition(name: str, arguments: Mapping[str, Any], payload: dict[str, Any]) -> None:
    if payload.get("project_id") != arguments.get("project_id") or payload.get("roadmap_id") != arguments.get("roadmap_id"):
        raise McpDispatchFailure("planning_failed")
    revision, content_hash = payload.get("revision"), payload.get("content_hash")
    if type(revision) is not int or revision < 1 or type(content_hash) is not str or not _valid_sha256(content_hash):
        raise McpDispatchFailure("planning_failed")
    if name == "planning_read_gate_definitions":
        if (
            set(payload) != {
                "schema_id", "project_id", "roadmap_id", "revision", "content_hash",
                "node_id", "gate_definitions", "read_only", "writes_performed",
            }
            or
            payload.get("schema_id") != "odysseus.planning.gate_definitions.v2"
            or payload.get("read_only") is not True
            or payload.get("writes_performed") is not False
            or type(payload.get("gate_definitions")) is not list
            or len(payload.get("gate_definitions", [])) > 200
            or payload.get("node_id") != arguments.get("node_id", "")
        ):
            raise McpDispatchFailure("planning_failed")
        from src.planning_definition_contract import validate_approval_scope_schema
        try:
            for gate in payload["gate_definitions"]:
                if type(gate) is not dict or set(gate) != {
                    "gate_id", "kind", "title", "blocks", "decision_needed", "safe_default",
                    "approval_scope_schema", "required_verification_rule_ids",
                }:
                    raise ValueError
                if type(gate["blocks"]) is not list or len(gate["blocks"]) > 200:
                    raise ValueError
                if type(gate["required_verification_rule_ids"]) is not list or len(gate["required_verification_rule_ids"]) > 200:
                    raise ValueError
                for field, maximum in (("gate_id", 128), ("kind", 80), ("title", 200), ("decision_needed", 4_000), ("safe_default", 4_000)):
                    if type(gate[field]) is not str or len(gate[field].encode("utf-8")) > maximum:
                        raise ValueError
                validate_approval_scope_schema(gate["approval_scope_schema"], path="$.gate_definitions[].approval_scope_schema")
        except Exception:
            raise McpDispatchFailure("planning_failed") from None
    elif (
        set(payload) != {
            "schema_id", "project_id", "roadmap_id", "revision", "content_hash",
            "title", "requested_entrypoint", "composer_text", "launch_authorized", "read_only",
        }
        or
        payload.get("schema_id") != "odysseus.agent.plan_handoff.v1"
        or payload.get("read_only") is not True
        or payload.get("launch_authorized") is not False
        or payload.get("requested_entrypoint") != "/abc"
        or type(payload.get("title")) is not str
        or not payload.get("title", "").strip()
        or len(payload.get("title", "")) > 200
        or any(ord(char) < 32 for char in payload.get("title", ""))
        or payload.get("composer_text") != (
            f"/abc run roadmap:{payload['roadmap_id']}@{revision} hash:{content_hash}"
        )
    ):
        raise McpDispatchFailure("planning_failed")
    if name == "planning_read_gate_definitions":
        identifier = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
        try:
            if payload["node_id"] != "" and (type(payload["node_id"]) is not str or identifier.fullmatch(payload["node_id"]) is None):
                raise ValueError
            for gate in payload["gate_definitions"]:
                if identifier.fullmatch(gate["gate_id"]) is None:
                    raise ValueError
                for item in gate["blocks"] + gate["required_verification_rule_ids"]:
                    if type(item) is not str or identifier.fullmatch(item) is None:
                        raise ValueError
        except (KeyError, TypeError, ValueError):
            raise McpDispatchFailure("planning_failed") from None


def _valid_sha256(value: str) -> bool:
    return len(value) == 71 and value.startswith("sha256:") and all(char in "0123456789abcdef" for char in value[7:])


def _message_method(message: Any) -> str | None:
    return message.get("method") if type(message) is dict and type(message.get("method")) is str else None


def _run_fence(fence: Any, method: str | None = None) -> None:
    if not callable(fence):
        return
    try:
        try:
            fence(method)
        except TypeError:
            fence()
    except McpDispatchFailure:
        raise
    except Exception:
        raise McpDispatchFailure("stale_authentication") from None


def _rpc_error(message_id: Any, code: str) -> dict[str, Any]:
    number, message = _FIXED_ERRORS.get(code, _FIXED_ERRORS["planning_failed"])
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": number, "message": message}}


def _valid_id(value: Any) -> bool:
    return (type(value) is int and -(2**53) < value < 2**53) or (
        type(value) is str and 0 < len(value) <= 64 and value.isprintable()
    )


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("invalid JSON constant")


def _check_tree(value: Any) -> None:
    stack: list[tuple[Any, int]] = [(value, 0)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MCP_JSON_NODES_MAX or depth > MCP_JSON_DEPTH_MAX:
            raise McpDispatchFailure("budget_exceeded")
        if type(item) is dict:
            for key, child in item.items():
                nodes += 1
                if type(key) is not str or nodes > MCP_JSON_NODES_MAX:
                    raise McpDispatchFailure("budget_exceeded")
                stack.append((child, depth + 1))
        elif type(item) is list:
            stack.extend((child, depth + 1) for child in item)
        elif item is not None and type(item) not in {str, int, float, bool}:
            raise McpDispatchFailure("parse_error")


def _encode(value: Any) -> bytes:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        return encoded.encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise McpDispatchFailure("planning_failed") from None


__all__ = [
    "AppOwnedPlanningRuntime",
    "MCP_AGGREGATE_MAX_BYTES",
    "MCP_ARGUMENTS_MAX_BYTES",
    "MCP_BODY_MAX_BYTES",
    "MCP_PLANNING_SCOPE",
    "MCP_RESULT_MAX_BYTES",
    "MCP_TOTAL_BUDGET_SECONDS",
    "McpAuthenticatedIdentity",
    "McpDispatchFailure",
    "authenticate_mcp_request",
    "dispatch_failure_response",
    "dispatch_http_payload",
    "parse_json_body",
    "read_bounded_body",
]
