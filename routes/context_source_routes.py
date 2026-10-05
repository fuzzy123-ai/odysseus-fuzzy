"""Constructor-owned HTTP transport for exact authenticated context reads."""
from __future__ import annotations

import asyncio
from contextlib import nullcontext
import hashlib
import json
from typing import Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from core.auth import AuthManager
from routes.auth_routes import SESSION_COOKIE
from src.auth_helpers import strict_session_principal
from src.context_manifest_producer import CombinedContextManifestProducer
from src.context_pack_assembler import ContextPackAssembler, ContextPackAssemblerError
from src.forge_context_metadata import ForgeContextMetadata
from src.forge_context_page import ForgeContextPageConsumer, ForgeContextPageRequest
from src.harbour_knowledge_active_runtime import (
    KnowledgeContextManifestProducer, KnowledgeContextPageConsumer, KnowledgeContextPageRequest,
)
from src.mcp_authenticated_dispatch import MCP_BODY_MAX_BYTES, parse_json_body
from src.planning_context_reader import (
    PlanningContextManifestProducer, PlanningContextPageConsumer, PlanningContextPageRequest,
)
from src.private_context_confirmation import ContextManifest, ManifestItem
from src.private_context_confirmation_app import (
    ChatConfirmationRepository, ContextSelection, CookieConfirmationAuthority,
    ForgeSelectionLocator, PrivateContextConfirmationApp, SourceSelection,
)

_DENIED_HEADERS = (
    "authorization", "forwarded", "x-forwarded-for", "x-forwarded-host", "x-real-ip",
    "cf-connecting-ip", "cf-ray", "cf-visitor", "x-odysseus-internal-token",
    "x-odysseus-owner", "x-internal-token", "x-tool-token",
)
_MAX_INT = (1 << 53) - 1


def _error(status: int, code: str) -> HTTPException:
    return HTTPException(status, detail={"code": code})


def _object(value: object, required: set[str], optional: set[str] | None = None) -> dict:
    if type(value) is not dict or not required <= value.keys() or value.keys() - required - (optional or set()):
        raise _error(400, "invalid_request")
    return value


def _integer(value: object, *, minimum: int = 0, maximum: int = _MAX_INT) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _error(400, "invalid_request")
    return value


def _locator(value: object) -> ForgeSelectionLocator:
    body = _object(value, {"repo_id", "version_id", "path"})
    return ForgeSelectionLocator(body["repo_id"], body["version_id"], body["path"])


def _selection(value: object) -> ContextSelection:
    if type(value) is not list or not 1 <= len(value) <= 300:
        raise _error(400, "invalid_request")
    items = []
    for entry in value:
        item = _object(entry, {"source", "selection_ref", "revision_ref"}, {"forge_locator"})
        locator = _locator(item["forge_locator"]) if "forge_locator" in item else None
        items.append(SourceSelection(item["source"], item["selection_ref"], item["revision_ref"], locator))
    return ContextSelection(tuple(items))


def _selection_json(selection: ContextSelection) -> list[dict]:
    result = []
    for item in selection.items:
        row = {"source": item.source, "selection_ref": item.selection_ref, "revision_ref": item.revision_ref}
        if item.forge_locator is not None:
            locator = item.forge_locator
            row["forge_locator"] = {"repo_id": locator.repo_id, "version_id": locator.version_id, "path": locator.path}
        result.append(row)
    return result


def _canonical(item: ManifestItem) -> str:
    return json.dumps(item._canonical(), sort_keys=True, separators=(",", ":"))


async def _read_body(request: Request) -> object:
    raw = bytearray()
    try:
        async for part in request.stream():
            if len(raw) + len(part) > MCP_BODY_MAX_BYTES:
                raise _error(413, "body_too_large")
            raw.extend(part)
        return parse_json_body(bytes(raw))
    except HTTPException:
        raise
    except Exception:
        raise _error(400, "invalid_request") from None


def create_context_source_router(
    *, auth_manager: AuthManager, session_factory: Callable,
    manifest_producer: CombinedContextManifestProducer,
    confirmation_app: PrivateContextConfirmationApp,
    forge: ForgeContextMetadata, planning: PlanningContextManifestProducer,
    knowledge: KnowledgeContextManifestProducer,
    forge_pages: ForgeContextPageConsumer, planning_pages: PlanningContextPageConsumer,
    knowledge_pages: KnowledgeContextPageConsumer,
) -> APIRouter:
    """Bind concrete services once; no request can supply an authority object."""
    if (
        type(auth_manager) is not AuthManager or not callable(session_factory)
        or type(manifest_producer) is not CombinedContextManifestProducer
        or type(confirmation_app) is not PrivateContextConfirmationApp
        or type(forge) is not ForgeContextMetadata
        or type(planning) is not PlanningContextManifestProducer
        or type(knowledge) is not KnowledgeContextManifestProducer
        or type(forge_pages) is not ForgeContextPageConsumer
        or type(planning_pages) is not PlanningContextPageConsumer
        or type(knowledge_pages) is not KnowledgeContextPageConsumer
        or manifest_producer._auth_manager is not auth_manager
        or manifest_producer._forge is not forge or manifest_producer._planning is not planning
        or manifest_producer._knowledge is not knowledge
        or confirmation_app._producer is not manifest_producer
        or confirmation_app._authority._auth_manager is not auth_manager
        or confirmation_app._repository._session_factory is not session_factory
        or forge_pages._metadata is not forge or forge_pages._producer is not manifest_producer
        or forge_pages._confirmation is not confirmation_app or forge_pages._session_factory is not session_factory
        or planning_pages._producer is not planning or planning_pages._confirmation_app is not confirmation_app
        or knowledge_pages._composition is not knowledge._composition or knowledge_pages._confirmation is not confirmation_app
        or any(page._authority._auth_manager is not auth_manager for page in (forge_pages, planning_pages, knowledge_pages))
        or any(page._repository._session_factory is not session_factory for page in (forge_pages, planning_pages, knowledge_pages))
    ):
        raise ValueError("context services unavailable") from None
    authority = confirmation_app._authority.fork()
    if (
        type(authority) is not CookieConfirmationAuthority
        or authority._auth_manager is not auth_manager
    ):
        raise ValueError("context services unavailable") from None
    repository = ChatConfirmationRepository(session_factory, authority)
    pack_assembler = ContextPackAssembler(
        manifest_producer=manifest_producer,
        confirmation_app=confirmation_app,
        forge_pages=forge_pages,
        planning_pages=planning_pages,
        knowledge_pages=knowledge_pages,
    )
    router = APIRouter(prefix="/api/context", tags=["context"])

    def transport(request: Request) -> str:
        if request.app.state.auth_manager is not auth_manager or auth_manager.is_configured is not True:
            raise _error(401, "not_authenticated")
        if request.query_params or getattr(request.state, "api_token", False) or any(request.headers.get(name) for name in _DENIED_HEADERS):
            raise _error(403, "invalid_transport")
        origin = request.headers.get("origin")
        if not origin or origin.rstrip("/") != f"{request.url.scheme}://{request.url.netloc}".rstrip("/"):
            raise _error(403, "invalid_origin")
        fetch_site = request.headers.get("sec-fetch-site")
        if fetch_site is not None and fetch_site != "same-origin":
            raise _error(403, "invalid_origin")
        principal = strict_session_principal(request)
        if auth_manager.is_admin(principal.owner) is not True:
            raise _error(403, "not_authorized")
        token = request.cookies.get(SESSION_COOKIE)
        if type(token) is not str:
            raise _error(401, "not_authenticated")
        return token

    def capture(binding, selection: ContextSelection) -> tuple[tuple[ManifestItem, ...], tuple[str, ...]]:
        items = manifest_producer.capture_metadata(binding, selection)
        native = []
        digests = {}
        for source in ("planning", "knowledge"):
            selected = tuple(item for item in selection.items if item.source == source)
            if not selected:
                continue
            partition = ContextSelection(selected)
            if source == "planning":
                part = planning.capture(binding, partition)
                native.extend(part.items)
            else:
                part = knowledge.capture_metadata(binding, partition)
                native.extend(ManifestItem(
                    item.source, item.selection_ref, item.revision_ref, item.boundary_sha256,
                    item.record_sha256, item.payload_sha256, item.classification, item.provenance_ref,
                ) for item in part.items)
            for target in selected:
                digests[target] = part.digest
        for target in selection.items:
            if target.source != "forge":
                continue
            locator = target.forge_locator
            if type(locator) is not ForgeSelectionLocator:
                raise _error(400, "invalid_request")
            part = forge.capture_version_metadata(binding, locator.repo_id, locator.version_id, (locator.path,))
            if (part.item.selection_ref, part.item.revision_ref) != (target.selection_ref, target.revision_ref):
                raise _error(409, "context_unavailable")
            native.append(part.item)
            digests[target] = part.digest
        if tuple(sorted(native, key=_canonical)) != items:
            raise _error(409, "context_changed")
        return items, tuple(digests[target] for target in selection.items)

    def verify(cookie, chat, selection, items, disclosure_id) -> None:
        if any(item.classification == "private" for item in items):
            _integer(disclosure_id, minimum=1)
            confirmation_app.verify_disclosure(cookie, chat, disclosure_id, selection, expected_subset=ContextManifest(items))

    def worker(request: Request, operation: str, raw: object) -> tuple[bytes, str, dict[str, str]]:
        try:
            cookie = transport(request)
            if operation == "pack":
                body = _object(raw, {"chat_session_id", "selection", "page_bytes", "cursor"}, {"disclosure_message_id"})
                chat = body["chat_session_id"]
                if type(chat) is not str:
                    raise _error(400, "invalid_request")
                selection = _selection(body["selection"])
                page_bytes = _integer(body["page_bytes"], minimum=1, maximum=1_000_000)
                cursor = body["cursor"]
                if cursor is not None and type(cursor) is not str:
                    raise _error(400, "invalid_request")
                disclosure_id = body.get("disclosure_message_id")
                if disclosure_id is not None:
                    _integer(disclosure_id, minimum=1)
                try:
                    payload = pack_assembler.page(
                        cookie, chat, selection, page_bytes, cursor, disclosure_id,
                    )
                except ContextPackAssemblerError as error:
                    code = "invalid_request" if error.code == "invalid_request" else "context_unavailable"
                    raise _error(400 if code == "invalid_request" else 409, code) from None
                transport(request)
                return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(), "application/json", {"Cache-Control": "no-store"}
            optional = {"forge_locators"} if operation == "metadata" else ({"disclosure_message_id"} if operation == "page" else set())
            required = {"chat_session_id"} if operation == "metadata" else {"chat_session_id", "selection"}
            if operation == "page":
                required |= {"target_index", "expected_manifest_digest", "offset", "limit"}
            body = _object(raw, required, optional | ({"selection"} if operation == "metadata" else set()))
            chat = body["chat_session_id"]
            if type(chat) is not str:
                raise _error(400, "invalid_request")
            if operation == "metadata" and (("selection" in body) == ("forge_locators" in body)):
                raise _error(400, "invalid_request")
            lock = confirmation_app._lock if operation == "challenge" else nullcontext()
            with lock:
                with authority.hold(cookie, chat) as binding:
                    repository.assert_chat_owned(binding)
                    if "forge_locators" in body:
                        locators = body["forge_locators"]
                        if type(locators) is not list or not 1 <= len(locators) <= 300:
                            raise _error(400, "invalid_request")
                        selected = []
                        for entry in locators:
                            locator = _locator(entry)
                            part = forge.capture_version_metadata(binding, locator.repo_id, locator.version_id, (locator.path,))
                            selected.append(SourceSelection("forge", part.item.selection_ref, part.item.revision_ref, locator))
                        selection = ContextSelection(tuple(sorted(selected, key=lambda item: (item.source, item.selection_ref, item.revision_ref))))
                    else:
                        selection = _selection(body["selection"])
                    first, page_digests = capture(binding, selection)
                    headers = {"Cache-Control": "no-store"}
                    media_type = "application/json"
                    if operation == "challenge":
                        challenge = confirmation_app.issue_for_selection(cookie, chat, selection)
                        payload = {"challenge_token": challenge.token, "command": "/context-disclose " + challenge.token,
                                   "expires_at": challenge.expires_at.isoformat()}
                    elif operation == "metadata":
                        private = any(item.classification == "private" for item in first)
                        payload = {"selection": _selection_json(selection), "items": [item._canonical() for item in first],
                                   "targets": [{"target_index": index, "manifest_digest": digest} for index, digest in enumerate(page_digests)],
                                   "requires_private_confirmation": private,
                                   "manifest_digest": ContextManifest(first).digest if private else None}
                    else:
                        index = _integer(body["target_index"], maximum=len(selection.items) - 1)
                        target = selection.items[index]
                        expected = body["expected_manifest_digest"]
                        if type(expected) is not str or expected != page_digests[index]:
                            raise _error(409, "context_changed")
                        offset = _integer(body["offset"])
                        limit = _integer(body["limit"], minimum=1, maximum=1_000_000 if target.source == "forge" else 1024 * 1024)
                        disclosure_id = body.get("disclosure_message_id")
                        if disclosure_id is not None:
                            _integer(disclosure_id, minimum=1)
                        verify(cookie, chat, selection, first, disclosure_id)
                        if target.source == "planning":
                            _integer(disclosure_id, minimum=1)
                            page = planning_pages.read_page(cookie, PlanningContextPageRequest(selection, target, chat, expected, offset, limit, disclosure_id))
                            encoded, total, byteform = page.page_bytes, page.total_bytes, "durable_revision_document"
                        elif target.source == "knowledge":
                            page = knowledge_pages.read_page(cookie, KnowledgeContextPageRequest(selection, target, chat, expected, offset, limit, disclosure_id))
                            encoded, total, byteform = page.payload_bytes, page.total, "knowledge_payload"
                        else:
                            page = forge_pages.read_page(cookie, ForgeContextPageRequest(selection, target, chat, expected, offset, limit, disclosure_id))
                            encoded, total, byteform = page.payload_bytes, page.total, "git_blob"
                        if type(encoded) is not bytes or len(encoded) > limit or page.end - page.offset != len(encoded):
                            raise _error(409, "context_unavailable")
                        if page.page_sha256.removeprefix("sha256:") != hashlib.sha256(encoded).hexdigest():
                            raise _error(409, "context_unavailable")
                        headers.update({"X-Context-Total": str(total), "X-Context-Offset": str(page.offset), "X-Context-End": str(page.end),
                                        "X-Context-Whole-SHA256": page.whole_sha256.removeprefix("sha256:"),
                                        "X-Context-Page-SHA256": page.page_sha256.removeprefix("sha256:"),
                                        "X-Context-Manifest-SHA256": page.manifest_digest.removeprefix("sha256:"), "X-Context-Byteform": byteform})
                        media_type = "application/octet-stream"
                    final, final_digests = capture(binding, selection)
                    if final != first or final_digests != page_digests:
                        raise _error(409, "context_changed")
                    if operation == "page":
                        verify(cookie, chat, selection, final, body.get("disclosure_message_id"))
                    else:
                        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
                    transport(request)
                    authority.recheck(binding)
                    repository.assert_chat_owned(binding)
                    return bytes(encoded), media_type, headers
        except HTTPException:
            raise
        except Exception:
            raise _error(409, "context_unavailable") from None

    async def respond(request: Request, operation: str) -> Response:
        try:
            transport(request)
            body = await _read_body(request)
            encoded, media, headers = await asyncio.to_thread(worker, request, operation, body)
            return Response(encoded, media_type=media, headers=headers)
        except HTTPException as error:
            # Only the fixed error projection crosses the HTTP boundary.
            code = error.detail.get("code") if type(error.detail) is dict else None
            allowed = {"invalid_request", "body_too_large", "not_authenticated", "not_authorized", "invalid_transport", "invalid_origin", "context_unavailable", "context_changed"}
            if code not in allowed:
                code = "not_authenticated" if error.status_code == 401 else "context_unavailable"
            return Response(json.dumps({"error": {"code": code}}, separators=(",", ":")), status_code=error.status_code,
                            media_type="application/json", headers={"Cache-Control": "no-store"})
        except Exception:
            return Response('{"error":{"code":"context_unavailable"}}', status_code=409,
                            media_type="application/json", headers={"Cache-Control": "no-store"})

    @router.post("/metadata")
    async def metadata(request: Request):
        return await respond(request, "metadata")

    @router.post("/challenge")
    async def challenge(request: Request):
        return await respond(request, "challenge")

    @router.post("/page")
    async def page(request: Request):
        return await respond(request, "page")

    @router.post("/pack")
    async def pack(request: Request):
        return await respond(request, "pack")

    return router
