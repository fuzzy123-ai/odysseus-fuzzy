"""Same-thread native browser-to-page tests for the MCP consent bridge."""
from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
import json
import time
import weakref

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from routes.auth_routes import SESSION_COOKIE
from src.mcp_private_context_confirmation import (
    _CODES,
    McpPrivateContextConfirmationError,
    McpPrivateContextConfirmationService,
)
from src.private_context_confirmation_app import (
    CookieConfirmationAuthority,
    ContextSelection,
)
from src.mcp_private_context_confirmation_app import McpPrivateContextConfirmationApp
import tests.test_mcp_context_metadata as metadata_tests
from tests.test_mcp_context_metadata import _lease, native_mcp_fixture
from tests.test_mcp_private_context_confirmation import (
    _entry_snapshot, _entry_unchanged, _entry_fully_scrubbed,
)


def _headers():
    return {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": ""}


def _assert_success(response):
    if response.status_code != 200:
        payload = response.json()
        code = (payload.get("error")
                if type(payload) is dict and set(payload) == {"error"}
                and type(payload.get("error")) is str
                and payload["error"] in _CODES else "unclassified_response")
        pytest.fail(f"unexpected_response:{code}")
    return response


@pytest.fixture
def consent_app(tmp_path, monkeypatch, request, planning_repo_root=None):
    """Construct every native object inside TestClient's lifespan portal thread."""
    application = FastAPI()

    @application.exception_handler(McpPrivateContextConfirmationError)
    async def consent_error(_request, exc):
        return JSONResponse(status_code=403, content={"error": exc.code})

    @asynccontextmanager
    async def lifespan(app):
        original_install = metadata_tests._install_real_mcp_authority
        original_gensalt = metadata_tests.bcrypt.gensalt
        install_calls = 0
        gensalt_calls = 0

        def install_with_fast_token_hashes(fixture):
            nonlocal install_calls, gensalt_calls
            install_calls += 1
            assert install_calls == 1

            def gensalt_rounds_4():
                nonlocal gensalt_calls
                gensalt_calls += 1
                return original_gensalt(rounds=4)

            with monkeypatch.context() as patch:
                patch.setattr(metadata_tests.bcrypt, "gensalt", gensalt_rounds_4)
                return original_install(fixture)

        fixture_body = (
            native_mcp_fixture.__wrapped__(tmp_path, monkeypatch)
            if planning_repo_root is None
            else native_mcp_fixture.__wrapped__(
                tmp_path,
                monkeypatch,
                planning_repo_root,
            )
        )
        try:
            with monkeypatch.context() as patch:
                patch.setattr(
                    metadata_tests, "_install_real_mcp_authority",
                    install_with_fast_token_hashes,
                )
                fixture = next(fixture_body)
            assert install_calls == 1
            assert gensalt_calls == 2
        except BaseException:
            try:
                next(fixture_body)
            except StopIteration:
                pass
            raise
        controlled_clock = getattr(request, "param", False) is True
        clock = ({"wall": datetime.now(timezone.utc), "mono": time.monotonic()}
                 if controlled_clock else None)
        service_clock = ({
            "wall_time": lambda: clock["wall"],
            "monotonic": lambda: clock["mono"],
        } if clock is not None else {})
        app.state.wall_clock_hook = None

        def wall_clock():
            hook = app.state.wall_clock_hook
            if hook is not None:
                hook()
            return clock["wall"]

        if clock is not None:
            service_clock["wall_time"] = wall_clock
        service = McpPrivateContextConfirmationService(
            auth_manager=fixture["auth"], authority=fixture["authority"],
            producer=fixture["combined"],
            cookie_authority=CookieConfirmationAuthority(fixture["auth"]),
            session_factory=fixture["session_factory"],
            **service_clock,
        )
        bridge = McpPrivateContextConfirmationApp(
            service=service, auth_manager=fixture["auth"],
            session_factory=fixture["session_factory"],
        )
        app.state.fixture = fixture
        app.state.service = service
        app.state.bridge = bridge
        app.state.auth_manager = fixture["auth"]
        app.state.clock = clock
        app.state.wall_clock_hook = None
        app.state.final_patch = None
        app.state.final_hook_fired = 0
        app.state.substitution = None
        app.state.retained_entry = None
        app.state.retained_before = None
        app.state.secondary_entry = None
        app.state.secondary_request_id = None
        app.state.secondary_slots = None
        app.state.page_boundary_mode = None
        app.state.before_execution_hook = None
        app.state.callback = None
        app.state.capture_published = False
        app.state.receipt_published = False
        app.state.pre_exchange_exit_scrubbed = False
        additional_lease = None

        def prepare_secondary_entry(label="secondary-preview"):
            nonlocal additional_lease
            additional_lease = _lease(app.state.fixture, label, token_index=1)
            prepared = service.prepare_intent(
                additional_lease, app.state.fixture["selection"],
            )
            app.state.additional_lease = additional_lease
            app.state.secondary_request_id = prepared.request_id
            app.state.secondary_entry = service._entries[prepared.request_id]
            app.state.secondary_slots = _entry_snapshot(app.state.secondary_entry)
            return prepared

        @contextmanager
        def substitution(stage):
            armed = app.state.substitution
            if armed is None or armed["stage"] != stage:
                yield
                return
            method = ("_cached_browser_submission"
                      if stage in {"challenge", "exchange"}
                      else "_readback_ack_under_guard")
            def shadow(*_args, **_kwargs):
                return None
            with monkeypatch.context() as patch:
                if armed["mode"] == "class":
                    replacement = staticmethod(shadow) if method == "_cached_browser_submission" else shadow
                    patch.setattr(McpPrivateContextConfirmationApp, method, replacement)
                else:
                    patch.setattr(bridge, method, shadow, raising=False)
                yield

        @app.post("/test/prepare")
        async def prepare(request: Request):
            fixture = request.app.state.fixture
            if request.headers.get("authorization") != "Bearer " + fixture["mcp_raw_tokens"][0]:
                raise McpPrivateContextConfirmationError("invalid_binding")
            lease = _lease(fixture, "same-thread-positive")
            request.app.state.lease = lease
            target = next(item for item in fixture["selection"].items if item.source == "planning")
            with CookieConfirmationAuthority(fixture["auth"]).hold(
                fixture["cookie"], request.app.state.lease.binding.chat_session_id,
            ) as binding:
                descriptors = fixture["durable_source"].resolve(
                    binding, ContextSelection((target,)),
                )
                descriptor = next(
                    (item for item in descriptors if item.selection() == target), None,
                )
                if descriptor is None:
                    raise McpPrivateContextConfirmationError("invalid_context")
                durable_store = fixture["planning_producer"]._runtime.durable_store
                request.app.state.oracle_payloads = {
                    target.selection_ref: durable_store.read_payload(
                        fixture["principal"], owner_id="fuzzy",
                        revision_id=descriptor.durable_revision_id,
                    )
                }
            prepared = service.prepare_intent(lease, fixture["selection"])
            request.app.state.request_id = prepared.request_id
            return {"request_id": prepared.request_id, "item_count": prepared.item_count}

        @app.post("/test/arm_substitution")
        async def arm_substitution(request: Request):
            body = await request.json()
            if (type(body) is not dict or set(body) != {"stage", "mode"}
                    or body["stage"] not in {"challenge", "exchange", "permit"}
                    or body["mode"] not in {"class", "instance"}):
                raise McpPrivateContextConfirmationError("invalid_request")
            if request.app.state.secondary_entry is None:
                prepare_secondary_entry("substitution-secondary")
            request.app.state.substitution = {"stage": body["stage"], "mode": body["mode"]}
            request.app.state.retained_entry = service._entries[request.app.state.request_id]
            request.app.state.retained_before = _entry_snapshot(request.app.state.retained_entry)
            return {"armed": True}

        @app.post("/test/arm_callback")
        async def arm_callback(request: Request):
            nonlocal additional_lease
            additional_lease = _lease(request.app.state.fixture, "callback-reentry", token_index=1)
            request.app.state.additional_lease = additional_lease
            request.app.state.retained_entry = service._entries[request.app.state.request_id]
            request.app.state.retained_before = _entry_snapshot(request.app.state.retained_entry)
            request.app.state.callback_permit_invalid = False
            request.app.state.capture_published = False
            request.app.state.receipt_published = False
            selection = request.app.state.fixture["selection"]

            def reentry(_page):
                try:
                    service.prepare_intent(additional_lease, selection)
                except McpPrivateContextConfirmationError as error:
                    if error.code != "permit_invalid":
                        raise
                    request.app.state.callback_permit_invalid = True
                    return None
                raise AssertionError("callback reentry unexpectedly succeeded")

            request.app.state.callback = reentry
            return {"armed": True}


        @app.post("/test/arm_callback_matrix")
        async def arm_callback_matrix(request: Request):
            body = await request.json()
            cases = {"bool", "list", "object", "coroutine", "generator", "awaitable",
                     "async_generator", "exception", "cancellation", "primary_revoke"}
            if type(body) is not dict or set(body) != {"kind"} or body["kind"] not in cases:
                raise McpPrivateContextConfirmationError("invalid_request")
            kind = body["kind"]
            prepare_secondary_entry("callback-matrix-secondary")
            request.app.state.retained_entry = service._entries[request.app.state.request_id]
            request.app.state.retained_before = _entry_snapshot(request.app.state.retained_entry)
            request.app.state.callback_calls = 0
            request.app.state.deferred_body_calls = 0

            async def coroutine_body():
                request.app.state.deferred_body_calls += 1
                return None

            def generator_body():
                request.app.state.deferred_body_calls += 1
                yield None

            async def async_generator_body():
                request.app.state.deferred_body_calls += 1
                yield None

            class DeferredAwaitable:
                def __await__(self):
                    request.app.state.deferred_body_calls += 1
                    yield None
                    return None

            def callback(_page):
                request.app.state.callback_calls += 1
                if kind == "bool":
                    return True
                if kind == "list":
                    return []
                if kind == "object":
                    return object()
                if kind == "coroutine":
                    return coroutine_body()
                if kind == "generator":
                    return generator_body()
                if kind == "async_generator":
                    return async_generator_body()
                if kind == "awaitable":
                    return DeferredAwaitable()
                if kind == "exception":
                    raise RuntimeError("test-only callback failure")
                if kind == "cancellation":
                    from asyncio import CancelledError
                    raise CancelledError()
                service.revoke(request.app.state.request_id)
                return None

            request.app.state.callback = callback
            return {"armed": True}

        @app.post("/test/callback_matrix_readback")
        async def callback_matrix_readback(request: Request):
            return {"callback_once": request.app.state.callback_calls == 1,
                    "deferred_body_not_executed": request.app.state.deferred_body_calls == 0,
                    "capture_not_published": (not request.app.state.capture_published
                        and not hasattr(request.app.state, "capture_ref")),
                    "completion_receipt_not_published": (not request.app.state.receipt_published
                        and not hasattr(request.app.state, "capture_receipt"))}

        @app.post("/test/drop_actual_permit")
        async def drop_actual_permit(request: Request):
            import gc
            prepare_secondary_entry("permit-gc-secondary")
            request.app.state.retained_entry = service._entries[request.app.state.request_id]
            request.app.state.retained_before = _entry_snapshot(request.app.state.retained_entry)
            permit = service.issue_permit(request.app.state.request_id)
            assert len(service._permits) == 1
            permit_ref = weakref.ref(permit)
            assert service._entries[request.app.state.request_id].permit_ref() is permit
            del permit
            gc.collect()
            return {"actual_permit_was_registered": True, "permit_collected": permit_ref() is None}

        @app.post("/test/replace_attached_cookie")
        async def replace_attached_cookie(request: Request):
            prepare_secondary_entry("replacement-cookie-secondary")
            request.app.state.retained_entry = service._entries[request.app.state.request_id]
            request.app.state.retained_before = _entry_snapshot(request.app.state.retained_entry)
            auth = fixture["auth"]
            original = fixture["cookie"]
            replacement = auth.create_session("fuzzy", "Password-123")
            assert type(replacement) is str and replacement
            left, right = auth.strict_session_identity(original), auth.strict_session_identity(replacement)
            same_subject = left is not None and right is not None and left[1] == right[1]
            distinct = replacement != original
            assert same_subject and distinct
            response = JSONResponse({"distinct_cookie": distinct, "same_subject": same_subject})
            response.set_cookie(SESSION_COOKIE, replacement)
            return response

        @app.post("/test/replacement_cookie_readback")
        async def replacement_cookie_readback(request: Request):
            return {"browser_receipt_not_published": not hasattr(request.app.state, "receipt"),
                    "capture_not_published": (not request.app.state.capture_published
                        and not hasattr(request.app.state, "capture_ref")),
                    "completion_receipt_not_published": (not request.app.state.receipt_published
                        and not hasattr(request.app.state, "capture_receipt"))}

        @app.post("/test/arm_ack_db_failure")
        async def arm_ack_db_failure(request: Request):
            body = await request.json()
            if (type(body) is not dict or set(body) != {"mode"}
                    or type(body["mode"]) is not str
                    or body["mode"] not in {"commit_before", "commit_uncertain", "rollback_failure", "close_failure", "slow_commit"}
                    or request.app.state.clock is None):
                raise McpPrivateContextConfirmationError("invalid_request")
            from sqlalchemy.orm import Session as SqlSession
            from core.database import ChatMessage
            prepare_secondary_entry("ack-db-secondary")
            request.app.state.retained_entry = service._entries[request.app.state.request_id]
            request.app.state.retained_before = _entry_snapshot(request.app.state.retained_entry)
            mode = body["mode"]
            stats = {"commit": 0, "rollback": 0, "close": 0}
            request.app.state.ack_db_stats = stats
            ack_db = None
            original_commit, original_rollback, original_close = SqlSession.commit, SqlSession.rollback, SqlSession.close

            def commit(db):
                nonlocal ack_db
                candidates = [row for row in db.new if type(row) is ChatMessage
                              and row.session_id == request.app.state.lease.binding.chat_session_id]
                if len(candidates) != 2 or {row.role for row in candidates} != {"user", "assistant"}:
                    return original_commit(db)
                assert ack_db is None
                ack_db = db
                stats["commit"] += 1
                if mode in {"commit_before", "rollback_failure"}:
                    raise RuntimeError("test-only before commit")
                original_commit(db)
                if mode == "commit_uncertain":
                    raise RuntimeError("test-only after commit")
                if mode == "slow_commit":
                    request.app.state.clock["wall"] += timedelta(seconds=301)
                    request.app.state.clock["mono"] += 301.0
                return None

            def rollback(db):
                if db is ack_db:
                    stats["rollback"] += 1
                    if mode == "rollback_failure":
                        raise RuntimeError("test-only rollback failure")
                return original_rollback(db)

            def close(db):
                if db is ack_db:
                    stats["close"] += 1
                    original_close(db)
                    if mode == "close_failure":
                        raise RuntimeError("test-only close failure")
                    return None
                return original_close(db)

            patch_context = monkeypatch.context()
            patch = patch_context.__enter__()
            patch.setattr(SqlSession, "commit", commit)
            patch.setattr(SqlSession, "rollback", rollback)
            patch.setattr(SqlSession, "close", close)
            request.app.state.final_patch = patch_context
            return {"armed": True}

        @app.post("/test/ack_db_failure_readback")
        async def ack_db_failure_readback(request: Request):
            return {"stats": request.app.state.ack_db_stats,
                    "browser_receipt_not_published": not hasattr(request.app.state, "receipt"),
                    "capture_not_published": (not request.app.state.capture_published
                        and not hasattr(request.app.state, "capture_ref")),
                    "completion_receipt_not_published": (not request.app.state.receipt_published
                        and not hasattr(request.app.state, "capture_receipt")),
                    "fault_patch_released": request.app.state.final_patch is None}

        @app.post("/test/arm_page_boundary")
        async def arm_page_boundary(request: Request):
            body = await request.json()
            if (type(body) is not dict or set(body) != {"mode"}
                    or type(body["mode"]) is not str
                    or body["mode"] not in {"partial", "eof", "past_eof", "target_oob", "nonplanning"}):
                raise McpPrivateContextConfirmationError("invalid_request")
            prepare_secondary_entry("page-boundary-secondary")
            request.app.state.retained_entry = service._entries[request.app.state.request_id]
            request.app.state.retained_before = _entry_snapshot(request.app.state.retained_entry)
            request.app.state.page_boundary_mode = body["mode"]
            return {"armed": True}

        @app.post("/test/arm_page_tamper")
        async def arm_page_tamper(request: Request):
            body = await request.json()
            fields = {"page_bytes", "total_bytes", "offset", "end", "whole_sha256",
                      "page_sha256", "manifest_digest", "byteform"}
            if (type(body) is not dict or set(body) != {"field"}
                    or type(body["field"]) is not str or body["field"] not in fields):
                raise McpPrivateContextConfirmationError("invalid_request")
            prepare_secondary_entry("page-tamper-secondary")
            request.app.state.retained_entry = service._entries[request.app.state.request_id]
            request.app.state.retained_before = _entry_snapshot(request.app.state.retained_entry)
            request.app.state.page_mutations = 0

            def callback(page):
                field = body["field"]
                old = getattr(page, field)
                if field == "page_bytes":
                    value = b"test-only mutation" + old
                elif field in {"total_bytes", "offset", "end"}:
                    value = old + 1
                elif field == "byteform":
                    value = "test_only_invalid_byteform"
                elif field == "manifest_digest":
                    assert type(old) is str and len(old) == 64
                    assert all(char in "0123456789abcdef" for char in old)
                    value = ("0" if old[0] != "0" else "1") + old[1:]
                else:
                    assert type(old) is str and old.startswith("sha256:") and len(old) == 71
                    value = old[:7] + ("0" if old[7] != "0" else "1") + old[8:]
                assert value != old
                object.__setattr__(page, field, value)
                request.app.state.page_mutations += 1
                return None

            request.app.state.callback = callback
            return {"armed": True}

        @app.post("/test/page_tamper_readback")
        async def page_tamper_readback(request: Request):
            return {"mutation_once": request.app.state.page_mutations == 1,
                    "capture_not_published": (not request.app.state.capture_published
                        and not hasattr(request.app.state, "capture_ref")),
                    "completion_receipt_not_published": (not request.app.state.receipt_published
                        and not hasattr(request.app.state, "capture_receipt"))}

        @app.post("/test/arm_row_tamper")
        async def arm_row_tamper(request: Request):
            body = await request.json()
            kinds = {"delete", "role", "content", "session", "timestamp", "signature",
                     "metadata_extra", "metadata_duplicate", "metadata_type"}
            if (type(body) is not dict or set(body) != {"kind", "role", "stage"}
                    or any(type(value) is not str for value in body.values())
                    or body["kind"] not in kinds or body["role"] not in {"user", "assistant"}
                    or body["stage"] not in {"permit", "before_execution", "callback"}):
                raise McpPrivateContextConfirmationError("invalid_request")
            prepare_secondary_entry("row-tamper-secondary")
            request.app.state.retained_entry = service._entries[request.app.state.request_id]
            request.app.state.retained_before = _entry_snapshot(request.app.state.retained_entry)
            request.app.state.row_mutations = 0

            def mutate():
                from core.database import ChatMessage, Session as DbSession
                from datetime import timedelta
                receipt = request.app.state.receipt
                row_id = (receipt.user_message_id if body["role"] == "user"
                          else receipt.acknowledgement_message_id)
                db = fixture["session_factory"]()
                try:
                    row = db.query(ChatMessage).filter(ChatMessage.id == row_id).one()
                    kind = body["kind"]
                    if kind == "delete":
                        db.delete(row)
                    elif kind == "role":
                        row.role = "system"
                    elif kind == "content":
                        row.content += " test-only mutation"
                    elif kind == "session":
                        parent = db.query(DbSession).filter(DbSession.id == row.session_id).one()
                        other = DbSession(id="row-tamper-other", name="test-only", endpoint_url="local",
                                          model="test-only", owner=parent.owner)
                        db.add(other)
                        db.flush()
                        row.session_id = other.id
                    elif kind == "timestamp":
                        row.timestamp += timedelta(seconds=1)
                    elif kind == "signature":
                        row.meta_data = json.dumps({"mcp_private_context_envelope": "mpce2." + "0" * 64})
                    elif kind == "metadata_extra":
                        metadata = json.loads(row.meta_data)
                        metadata["test_only_extra"] = True
                        row.meta_data = json.dumps(metadata)
                    elif kind == "metadata_duplicate":
                        value = json.loads(row.meta_data)["mcp_private_context_envelope"]
                        item = json.dumps("mcp_private_context_envelope") + ":" + json.dumps(value)
                        row.meta_data = "{" + item + "," + item + "}"
                    else:
                        row.meta_data = "[]"
                    db.commit()
                    request.app.state.row_mutations += 1
                finally:
                    db.close()

            if body["stage"] == "permit":
                mutate()
            elif body["stage"] == "before_execution":
                request.app.state.before_execution_hook = mutate
            else:
                def callback(_page):
                    mutate()
                    return None
                request.app.state.callback = callback
            return {"armed": True}

        @app.post("/test/row_tamper_readback")
        async def row_tamper_readback(request: Request):
            return {"mutation_once": request.app.state.row_mutations == 1,
                    "capture_not_published": (not request.app.state.capture_published
                        and not hasattr(request.app.state, "capture_ref")),
                    "completion_receipt_not_published": (not request.app.state.receipt_published
                        and not hasattr(request.app.state, "capture_receipt"))}

        @app.post("/test/arm_late_lookup")
        async def arm_late_lookup(request: Request):
            body = await request.json()
            if type(body) is not dict or set(body) != {"mode"} or body["mode"] not in {"class", "instance"}:
                raise McpPrivateContextConfirmationError("invalid_request")
            prepare_secondary_entry("late-secondary")
            request.app.state.substitution = {"stage": "late_lookup", "mode": body["mode"]}
            request.app.state.retained_entry = service._entries[request.app.state.request_id]
            request.app.state.retained_before = _entry_snapshot(request.app.state.retained_entry)
            return {"armed": True}

        @app.post("/test/arm_final_pin")
        async def arm_final_pin(request: Request):
            body = await request.json()
            if type(body) is not dict or set(body) != {"mode"} or body["mode"] not in {"class", "instance"}:
                raise McpPrivateContextConfirmationError("invalid_request")
            if request.app.state.secondary_entry is None:
                prepare_secondary_entry("final-secondary")
            request.app.state.substitution = {"stage": "final_pin", "mode": body["mode"]}
            request.app.state.retained_entry = service._entries[request.app.state.request_id]
            request.app.state.retained_before = _entry_snapshot(request.app.state.retained_entry)
            mode = body["mode"]

            def fire_once():
                entry = service._entries.get(request.app.state.request_id)
                if (entry is None or entry.state != "disclosing"
                        or entry.ack_attestation is None):
                    return
                request.app.state.wall_clock_hook = None
                request.app.state.final_hook_fired += 1
                patch_context = monkeypatch.context()
                patch = patch_context.__enter__()
                request.app.state.final_patch = patch_context
                def shadow(*_args, **_kwargs):
                    return None
                if mode == "class":
                    patch.setattr(McpPrivateContextConfirmationApp, "_cached_browser_submission", staticmethod(shadow))
                else:
                    patch.setattr(bridge, "_cached_browser_submission", shadow, raising=False)
                request.app.state.final_patch = patch_context

            request.app.state.wall_clock_hook = fire_once
            return {"armed": True}

        @app.post("/test/prepare_secondary")
        async def prepare_secondary(request: Request):
            prepare_secondary_entry()
            return {"prepared": True}

        @app.post("/test/substitution_readback")
        async def substitution_readback(request: Request):
            entry = request.app.state.retained_entry
            secondary = request.app.state.secondary_entry
            secondary_unchanged = (secondary is not None
                and service._entries.get(request.app.state.secondary_request_id) is secondary
                and _entry_unchanged(secondary, request.app.state.secondary_slots))
            return {
                "request_absent": request.app.state.request_id not in service._entries,
                "all42_scrubbed": _entry_fully_scrubbed(entry, request.app.state.retained_before),
                "cookie_empty": entry is not None and entry.cookie == "",
                "selection_cleared": entry is not None and entry.selection is None,
                "cookie_snapshot_cleared": entry is not None and entry.cookie_snapshot is None,
                "mcp_snapshot_cleared": entry is not None and entry.mcp_snapshot is None,
                "receipt_cleared": entry is not None and entry.receipt is None,
                "ack_descriptor_cleared": entry is not None and entry.ack_descriptor is None,
                "ack_attestation_cleared": entry is not None and entry.ack_attestation is None,
                "lease_ref_cleared": entry is not None and entry.lease_ref is None,
                "binding_ref_cleared": entry is not None and entry.binding_ref is None,
                "permit_ref_cleared": entry is not None and entry.permit_ref is None,
                "permits_empty": not service._permits,
                "exchanges_empty": not service._exchanges,
                "completions_empty": not service._completions,
                "callback_permit_invalid": getattr(request.app.state, "callback_permit_invalid", False),
                "capture_absent": not request.app.state.capture_published,
                "receipt_absent": not request.app.state.receipt_published,
                "secondary_unchanged": secondary_unchanged,
                "pre_exchange_exit_scrubbed": request.app.state.pre_exchange_exit_scrubbed,
                "final_hook_fired_once": request.app.state.final_hook_fired == 1,
            }

        @app.post("/test/pair_readback")
        async def pair_readback(request: Request):
            from core.database import ChatMessage, Session as DbSession
            db = fixture["session_factory"]()
            try:
                session_id = request.app.state.lease.binding.chat_session_id
                rows = db.query(ChatMessage).filter(ChatMessage.session_id == session_id).all()
                parent = db.query(DbSession).filter(DbSession.id == session_id).one()
                return {
                    "pair_count": len(rows),
                    "parent_count": parent.message_count,
                    "roles_fixed": {row.role for row in rows} == {"user", "assistant"},
                }
            finally:
                db.close()

        @app.post("/api/context/mcp/challenge")
        async def challenge(request: Request):
            await request.body()
            request_id = request.app.state.request_id
            with substitution("challenge"):
                result = bridge.issue_authenticated_challenge(request, request_id)
            request.app.state.challenge = result.token
            original = getattr(request.app.state, "expiry_deadlines", None)
            return {"token": result.token, "expires_at": result.expires_at.isoformat(),
                    "deadline_pair_unchanged": original is not None and (
                        request.app.state.service._entries[request_id].deadline,
                        request.app.state.service._entries[request_id].mono_deadline,
                    ) == original,
                    "challenge_expires_at_matches": original is not None and
                    result.expires_at == original[0]}

        @app.post("/test/resolve_authenticated_challenge")
        async def resolve_authenticated_challenge(request: Request):
            body = await request.json()
            request_id = bridge.resolve_authenticated_challenge(
                body.get("challenge"),
                body.get("owner_sid"),
                body.get("chat_session_id"),
            )
            return {"request_id": request_id}

        @app.post("/api/context/mcp/revoke")
        async def revoke_authenticated(request: Request):
            await request.body()
            body = json.loads(request._body)
            bridge.revoke_authenticated(request, body.get("request_id"))
            fixture["authority"].recheck_lease(request.app.state.lease)
            return {
                "status": "revoked",
                "entry_absent": body.get("request_id") not in service._entries,
                "lease_retained": True,
            }

        @app.post("/test/clock_near")
        async def clock_near(request: Request):
            entry = service._entries[request.app.state.request_id]
            original = (entry.deadline, entry.mono_deadline)
            request.app.state.expiry_deadlines = original
            clock = request.app.state.clock
            if clock is None:
                raise McpPrivateContextConfirmationError("invalid_context")
            clock["wall"] = original[0] - timedelta(seconds=1)
            clock["mono"] = original[1] - 1.0
            return {"clock_near": True}

        @app.post("/test/clock_advance")
        async def clock_advance(request: Request):
            original = request.app.state.expiry_deadlines
            clock = request.app.state.clock
            if clock is None or original is None:
                raise McpPrivateContextConfirmationError("invalid_context")
            clock["wall"] = original[0] + timedelta(seconds=1)
            clock["mono"] = original[1] + 1.0
            return {"clock_advanced": True}

        @app.post("/test/expiry_readback")
        async def expiry_readback(request: Request):
            return {"entry_absent": request.app.state.request_id not in service._entries}

        async def chat(request: Request):
            content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type == "application/json":
                body = await request.json()
                await request.form()
                message = body.get("message")
            else:
                form = await request.form()
                message = form.get("message")
            if not isinstance(message, str):
                raise McpPrivateContextConfirmationError("invalid_binding")
            request_id = request.app.state.request_id
            token = request.app.state.challenge
            late = request.app.state.substitution
            original_app = request.scope.get("app")
            if late is not None and late["stage"] == "late_lookup":
                class AppStateProxy:
                    def __init__(self, original):
                        self._original = original
                        self._installed = False

                    @property
                    def state(self):
                        if not self._installed:
                            self._installed = True
                            method = "_cached_browser_submission"
                            def shadow(*_args, **_kwargs):
                                return None
                            patch_context = monkeypatch.context()
                            patch = patch_context.__enter__()
                            self._patch = patch_context
                            if late["mode"] == "class":
                                patch.setattr(McpPrivateContextConfirmationApp, method, staticmethod(shadow))
                            else:
                                patch.setattr(bridge, method, shadow, raising=False)
                        return self._original.state

                request.scope["app"] = AppStateProxy(original_app)
            with substitution("exchange"):
                try:
                    with bridge.hold_authenticated_human_exchange(
                        request, request_id, token, raw_user_command=message,
                    ) as exchange:
                        try:
                            receipt = service.disclose(
                                request_id, token, raw_user_command=message,
                                human_exchange=exchange,
                            )
                        except McpPrivateContextConfirmationError:
                            # Inspect the retained exact entry before the exchange guard exits.
                            failed_entry = request.app.state.retained_entry
                            request.app.state.pre_exchange_exit_scrubbed = (
                                request_id not in service._entries
                                and _entry_fully_scrubbed(failed_entry, request.app.state.retained_before)
                            )
                            raise
                finally:
                    proxy = request.scope.get("app")
                    if hasattr(proxy, "_patch"):
                        proxy._patch.__exit__(None, None, None)
                    request.scope["app"] = original_app
                    if request.app.state.final_patch is not None:
                        request.app.state.final_patch.__exit__(None, None, None)
                        request.app.state.final_patch = None
            request.app.state.receipt = receipt
            request.app.state.original_chat = message
            return {"request_id": receipt.request_id,
                    "user_message_id": receipt.user_message_id,
                    "acknowledgement_message_id": receipt.acknowledgement_message_id}

        @app.post("/test/readback")
        async def readback(request: Request):
            receipt = request.app.state.receipt
            db = fixture["session_factory"]()
            try:
                from core.database import ChatMessage, Session as DbSession
                session_id = request.app.state.lease.binding.chat_session_id
                rows = db.query(ChatMessage).filter(
                    ChatMessage.session_id == session_id
                ).order_by(ChatMessage.id).all()
                parent = db.query(DbSession).filter(DbSession.id == session_id).one()
                ids = (str(receipt.user_message_id), str(receipt.acknowledgement_message_id))
                pair = [row for row in rows if str(row.id) in ids]
                metadata = [json.loads(row.meta_data) for row in pair]
                by_role = {row.role: row for row in pair}
                verified = (
                    len(rows) == 2 and len(pair) == 2 and
                    {str(row.id) for row in pair} == set(ids) and
                    {row.role for row in pair} == {"user", "assistant"} and
                    all(row.session_id == session_id for row in pair) and
                    by_role["user"].content == request.app.state.original_chat and
                    by_role["assistant"].content == "MCP private context disclosure confirmed." and
                    parent.message_count == 2 and
                    all(type(value) is dict and set(value) == {"mcp_private_context_envelope"}
                        and type(value["mcp_private_context_envelope"]) is str
                        for value in metadata)
                )
            finally:
                db.close()
            return {"pair_verified": verified, "owned_row_count": len(pair)}

        application.add_api_route("/api/chat", chat, methods=["POST"])
        application.add_api_route("/api/chat_stream", chat, methods=["POST"])

        @application.post("/test/consume")
        async def consume(request: Request):
            with substitution("permit"):
                permit = service.issue_permit(request.app.state.request_id)
            request.app.state.permit = permit
            target_index = next(
                index for index, item in enumerate(request.app.state.fixture["selection"].items)
                if item.source == "planning"
            )
            target = request.app.state.fixture["selection"].items[target_index]
            cookie_authority = CookieConfirmationAuthority(request.app.state.fixture["auth"])
            with cookie_authority.hold(
                request.app.state.fixture["cookie"],
                request.app.state.lease.binding.chat_session_id,
            ) as binding:
                payload = request.app.state.oracle_payloads[target.selection_ref]
                snapshot = service._entries[request.app.state.request_id].cookie_snapshot
                expected_digest = snapshot.target_manifest_digests[target_index]
            page_limit = service._entries[request.app.state.request_id].page_bytes
            mode = request.app.state.page_boundary_mode
            read_offset = 1 if mode == "partial" else len(payload) if mode == "eof" else len(payload) + 1 if mode == "past_eof" else 0
            execution_target = (len(fixture["selection"].items) if mode == "target_oob" else
                next(index for index, item in enumerate(fixture["selection"].items) if item.source != "planning")
                if mode == "nonplanning" else target_index)
            hook = request.app.state.before_execution_hook
            request.app.state.before_execution_hook = None
            if hook is not None:
                hook()
            capture = service._execute_planning_page(
                permit, target_index=execution_target, offset=read_offset,
                validate_detached=request.app.state.callback,
            )
            request.app.state.capture_published = True
            request.app.state.capture_ref = weakref.ref(capture)
            request.app.state.capture_receipt = capture.receipt
            request.app.state.capture_entry = service._entries[request.app.state.request_id]
            page = capture.page
            import hashlib
            document = json.loads(payload.decode("utf-8"))
            canonical = json.dumps(
                document, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            ).encode("utf-8")
            assert canonical == payload
            whole = "sha256:" + hashlib.sha256(payload).hexdigest()
            expected_end = min(len(payload), read_offset + page_limit)
            assert page.total_bytes == len(payload)
            assert page.offset == read_offset and page.end == expected_end
            expected_page = payload[read_offset:expected_end]
            assert page.page_bytes == expected_page
            assert page.whole_sha256 == whole
            assert page.page_sha256 == "sha256:" + hashlib.sha256(expected_page).hexdigest()
            assert page.manifest_digest == expected_digest
            receipt = capture.receipt
            request.app.state.receipt_published = True
            assert type(receipt).__name__ == "_PlanningCompletionReceipt"
            try:
                service._execute_planning_page(permit, target_index=target_index, offset=0)
            except McpPrivateContextConfirmationError as error:
                permit_replay_code = error.code
            else:
                permit_replay_code = "missing_error"
            service._consume_planning_completion(capture)
            try:
                service._consume_planning_completion(capture)
            except McpPrivateContextConfirmationError as error:
                replay_code = error.code
            else:
                replay_code = "missing_error"
            return {"total_bytes": page.total_bytes, "offset": page.offset,
                    "end": page.end, "whole_sha256": page.whole_sha256,
                    "page_sha256": page.page_sha256, "page_bytes": len(page.page_bytes),
                    "durable_revision_document": page.byteform == "durable_revision_document",
                    "oracle_equal": page.page_bytes == expected_page,
                    "manifest_equal": page.manifest_digest == expected_digest,
                    "receipt_opaque": type(receipt).__name__ == "_PlanningCompletionReceipt",
                    "completion_consumed": True, "completion_replay": replay_code,
                    "permit_replay": permit_replay_code}

        @app.post("/test/capture_gc")
        async def capture_gc(request: Request):
            import gc
            permit = service.issue_permit(request.app.state.request_id)
            target_index = next(
                index for index, item in enumerate(request.app.state.fixture["selection"].items)
                if item.source == "planning"
            )
            target = request.app.state.fixture["selection"].items[target_index]
            with CookieConfirmationAuthority(request.app.state.fixture["auth"]).hold(
                request.app.state.fixture["cookie"],
                request.app.state.lease.binding.chat_session_id,
            ) as binding:
                payload = request.app.state.oracle_payloads[target.selection_ref]
                snapshot = service._entries[request.app.state.request_id].cookie_snapshot
                expected_digest = snapshot.target_manifest_digests[target_index]
            capture = service._execute_planning_page(
                permit, target_index=target_index, offset=0,
            )
            page = capture.page
            import hashlib
            assert page.page_bytes == payload[page.offset:page.end]
            assert page.whole_sha256 == "sha256:" + hashlib.sha256(payload).hexdigest()
            assert page.page_sha256 == "sha256:" + hashlib.sha256(page.page_bytes).hexdigest()
            assert page.manifest_digest == expected_digest
            entry = service._entries[request.app.state.request_id]
            before_gc = _entry_snapshot(entry)
            receipt = capture.receipt
            capture_ref = weakref.ref(capture)
            receipt_ref = weakref.ref(receipt)
            assert entry.state == "consumed"
            assert service._completions[id(receipt)].capture_ref() is capture
            assert service._completions[id(receipt)].receipt_ref() is receipt
            request.app.state.capture_entry = entry
            request.app.state.capture_ref = capture_ref
            request.app.state.capture_receipt = receipt
            del capture
            gc.collect()
            completion = service._completions.get(id(receipt))
            return {
                "all42_scrubbed": _entry_fully_scrubbed(entry, before_gc),
                "capture_dead": capture_ref() is None,
                "receipt_alive": receipt_ref() is receipt,
                "completion_absent": completion is None,
                "request_absent": request.app.state.request_id not in service._entries,
                "cookie_empty": entry.cookie == "",
                "selection_cleared": entry.selection is None,
                "cookie_snapshot_cleared": entry.cookie_snapshot is None,
                "mcp_snapshot_cleared": entry.mcp_snapshot is None,
                "receipt_cleared": entry.receipt is None,
                "ack_descriptor_cleared": entry.ack_descriptor is None,
                "ack_attestation_cleared": entry.ack_attestation is None,
                "lease_ref_cleared": entry.lease_ref is None,
                "binding_ref_cleared": entry.binding_ref is None,
                "permit_ref_cleared": entry.permit_ref is None,
                "secondary_unchanged": (
                    service._entries.get(request.app.state.secondary_request_id) is request.app.state.secondary_entry
                    and _entry_unchanged(request.app.state.secondary_entry, request.app.state.secondary_slots)
                ),
            }

        try:
            yield
        finally:
            try:
                service.close()
            finally:
                # The native fixture owns the authority and all of its leases.
                try:
                    next(fixture_body)
                except StopIteration:
                    pass

    application.router.lifespan_context = lifespan
    return application


@pytest.mark.parametrize(("path", "transport"), [
    ("/api/chat", "json"), ("/api/chat_stream", "form"), ("/api/chat_stream", "json"),
])
def test_real_authenticated_browser_ack_permit_and_verify(consent_app, path, transport):
    with TestClient(consent_app) as client:
        fixture = client.app.state.fixture
        token = fixture["mcp_raw_tokens"][0]
        prepared = _assert_success(client.post(
            "/test/prepare", headers={"Authorization": "Bearer " + token}))
        request_id = prepared.json()["request_id"]
        client.cookies.set(SESSION_COOKIE, fixture["cookie"])
        challenge = _assert_success(client.post(
            "/api/context/mcp/challenge", headers=_headers(), json={"request_id": request_id},
        ))
        challenge_token = challenge.json()["token"]
        command = "/mcp-context-disclose " + challenge_token
        body = {"message": command, "session": "chat-owned"}
        if transport == "json":
            response = _assert_success(client.post(path, headers=_headers(), json=body))
        else:
            response = _assert_success(client.post(path, headers=_headers(), data=body))
        receipt = response.json()
        assert receipt["request_id"] == request_id
        assert receipt["user_message_id"] != receipt["acknowledgement_message_id"]
        readback = _assert_success(client.post("/test/readback"))
        assert readback.json() == {"pair_verified": True, "owned_row_count": 2}
        assert challenge_token not in response.text

        page = _assert_success(client.post("/test/consume"))
        value = page.json()
        assert value["offset"] == 0
        assert value["end"] == value["page_bytes"]
        assert value["total_bytes"] >= value["end"]
        assert len(value["whole_sha256"]) == 71 and value["whole_sha256"].startswith("sha256:")
        assert len(value["page_sha256"]) == 71 and value["page_sha256"].startswith("sha256:")
        assert value["oracle_equal"] is True
        assert value["manifest_equal"] is True
        assert value["durable_revision_document"] is True
        assert value["receipt_opaque"] is True
        assert value["completion_replay"] == "permit_invalid"
        assert value["permit_replay"] == "permit_invalid"


def _scrubbed_readback():
    return {
        "request_absent": True,
        "all42_scrubbed": True,
        "cookie_empty": True,
        "selection_cleared": True,
        "cookie_snapshot_cleared": True,
        "mcp_snapshot_cleared": True,
        "receipt_cleared": True,
        "ack_descriptor_cleared": True,
        "ack_attestation_cleared": True,
        "lease_ref_cleared": True,
        "binding_ref_cleared": True,
        "permit_ref_cleared": True,
        "permits_empty": True,
        "exchanges_empty": True,
        "completions_empty": True,
        "callback_permit_invalid": False,
        "capture_absent": True,
        "receipt_absent": True,
        "secondary_unchanged": True,
        "pre_exchange_exit_scrubbed": False,
        "final_hook_fired_once": False,
    }


@pytest.mark.parametrize(("stage", "mode"), [
    ("challenge", "class"), ("challenge", "instance"),
    ("exchange", "class"), ("exchange", "instance"),
    ("permit", "class"), ("permit", "instance"),
])
def test_portal_substitution_scrubs_denied_request(consent_app, stage, mode):
    with TestClient(consent_app) as client:
        fixture = client.app.state.fixture
        token = fixture["mcp_raw_tokens"][0]
        prepared = _assert_success(client.post(
            "/test/prepare", headers={"Authorization": "Bearer " + token}))
        request_id = prepared.json()["request_id"]
        client.cookies.set(SESSION_COOKIE, fixture["cookie"])
        if stage == "challenge":
            _assert_success(client.post(
                "/test/arm_substitution", json={"stage": stage, "mode": mode}))
            denied = client.post(
                "/api/context/mcp/challenge", headers=_headers(),
                json={"request_id": request_id},
            )
        else:
            challenge = _assert_success(client.post(
                "/api/context/mcp/challenge", headers=_headers(),
                json={"request_id": request_id},
            )).json()
            command = "/mcp-context-disclose " + challenge["token"]
            body = {"message": command, "session": "chat-owned"}
            if stage == "exchange":
                _assert_success(client.post(
                    "/test/arm_substitution", json={"stage": stage, "mode": mode}))
                denied = client.post("/api/chat", headers=_headers(), json=body)
            else:
                _assert_success(client.post("/api/chat", headers=_headers(), json=body))
                _assert_success(client.post(
                    "/test/arm_substitution", json={"stage": stage, "mode": mode}))
                denied = client.post("/test/consume")
        assert denied.status_code == 403
        assert denied.json() == {"error": "invalid_binding"}
        readback = _assert_success(client.post("/test/substitution_readback"))
        assert readback.json() == _scrubbed_readback()


def test_portal_callback_prepare_reentry_revokes_execution(consent_app):
    with TestClient(consent_app) as client:
        fixture = client.app.state.fixture
        token = fixture["mcp_raw_tokens"][0]
        prepared = _assert_success(client.post(
            "/test/prepare", headers={"Authorization": "Bearer " + token}))
        request_id = prepared.json()["request_id"]
        client.cookies.set(SESSION_COOKIE, fixture["cookie"])
        challenge = _assert_success(client.post(
            "/api/context/mcp/challenge", headers=_headers(),
            json={"request_id": request_id},
        )).json()
        command = "/mcp-context-disclose " + challenge["token"]
        _assert_success(client.post(
            "/api/chat", headers=_headers(),
            json={"message": command, "session": "chat-owned"},
        ))
        _assert_success(client.post("/test/arm_callback"))
        denied = client.post("/test/consume")
        assert denied.status_code == 403
        assert denied.json() == {"error": "revoked"}
        readback = _assert_success(client.post("/test/substitution_readback")).json()
        expected = _scrubbed_readback()
        expected["callback_permit_invalid"] = True
        expected["secondary_unchanged"] = False
        assert readback == expected


@pytest.mark.parametrize("mode", ["class", "instance"])
def test_regression_late_request_scope_lookup_scrubs_primary(consent_app, mode):
    with TestClient(consent_app) as client:
        fixture = client.app.state.fixture
        token = fixture["mcp_raw_tokens"][0]
        prepared = _assert_success(client.post(
            "/test/prepare", headers={"Authorization": "Bearer " + token}))
        request_id = prepared.json()["request_id"]
        client.cookies.set(SESSION_COOKIE, fixture["cookie"])
        challenge = _assert_success(client.post(
            "/api/context/mcp/challenge", headers=_headers(),
            json={"request_id": request_id},
        )).json()
        _assert_success(client.post("/test/arm_late_lookup", json={"mode": mode}))
        denied = client.post(
            "/api/chat", headers=_headers(),
            json={"message": "/mcp-context-disclose " + challenge["token"], "session": "chat-owned"},
        )
        assert denied.status_code == 403
        assert denied.json() == {"error": "invalid_binding"}
        readback = _assert_success(client.post("/test/substitution_readback")).json()
        expected = _scrubbed_readback()
        expected["pre_exchange_exit_scrubbed"] = False
        assert readback == expected


@pytest.mark.parametrize(("consent_app", "mode"), [(True, "class"), (True, "instance")], indirect=["consent_app"])
def test_regression_final_pin_after_commit_scrubs_primary(consent_app, mode):
    with TestClient(consent_app) as client:
        fixture = client.app.state.fixture
        token = fixture["mcp_raw_tokens"][0]
        prepared = _assert_success(client.post(
            "/test/prepare", headers={"Authorization": "Bearer " + token}))
        request_id = prepared.json()["request_id"]
        client.cookies.set(SESSION_COOKIE, fixture["cookie"])
        challenge = _assert_success(client.post(
            "/api/context/mcp/challenge", headers=_headers(),
            json={"request_id": request_id},
        )).json()
        command = "/mcp-context-disclose " + challenge["token"]
        _assert_success(client.post("/test/prepare_secondary"))
        _assert_success(client.post("/test/arm_final_pin", json={"mode": mode}))
        denied = client.post(
            "/api/chat", headers=_headers(),
            json={"message": command, "session": "chat-owned"},
        )
        assert denied.status_code == 403
        assert denied.json() == {"error": "invalid_binding"}
        readback = _assert_success(client.post("/test/substitution_readback")).json()
        expected = _scrubbed_readback()
        expected["pre_exchange_exit_scrubbed"] = True
        expected["final_hook_fired_once"] = True
        assert readback == expected
        assert _assert_success(client.post("/test/pair_readback")).json() == {
            "pair_count": 2, "parent_count": 2, "roles_fixed": True,
        }


@pytest.mark.parametrize("path", ["/api/chat"])
def test_regression_capture_gc_retains_receipt(consent_app, path):
    with TestClient(consent_app) as client:
        fixture = client.app.state.fixture
        token = fixture["mcp_raw_tokens"][0]
        prepared = _assert_success(client.post(
            "/test/prepare", headers={"Authorization": "Bearer " + token}))
        request_id = prepared.json()["request_id"]
        client.cookies.set(SESSION_COOKIE, fixture["cookie"])
        challenge = _assert_success(client.post(
            "/api/context/mcp/challenge", headers=_headers(),
            json={"request_id": request_id},
        )).json()
        command = "/mcp-context-disclose " + challenge["token"]
        _assert_success(client.post(
            path, headers=_headers(),
            json={"message": command, "session": "chat-owned"},
        ))
        _assert_success(client.post("/test/prepare_secondary"))
        assert _assert_success(client.post("/test/capture_gc")).json() == {
            "all42_scrubbed": True,
            "capture_dead": True, "receipt_alive": True, "completion_absent": True,
            "request_absent": True, "cookie_empty": True, "selection_cleared": True,
            "cookie_snapshot_cleared": True, "mcp_snapshot_cleared": True,
            "receipt_cleared": True, "ack_descriptor_cleared": True,
            "ack_attestation_cleared": True, "lease_ref_cleared": True,
            "binding_ref_cleared": True, "permit_ref_cleared": True,
            "secondary_unchanged": True,
        }


@pytest.mark.parametrize("consent_app", [True], indirect=True)
def test_portal_expiry_prunes_request(consent_app):
    with TestClient(consent_app) as client:
        fixture = client.app.state.fixture
        token = fixture["mcp_raw_tokens"][0]
        prepared = _assert_success(client.post(
            "/test/prepare", headers={"Authorization": "Bearer " + token}))
        request_id = prepared.json()["request_id"]
        client.cookies.set(SESSION_COOKIE, fixture["cookie"])
        assert _assert_success(client.post("/test/clock_near")).json() == {"clock_near": True}
        challenge = _assert_success(client.post(
            "/api/context/mcp/challenge", headers=_headers(), json={"request_id": request_id},
        )).json()
        assert challenge["deadline_pair_unchanged"] is True
        assert challenge["challenge_expires_at_matches"] is True
        command = "/mcp-context-disclose " + challenge["token"]
        assert _assert_success(client.post("/test/clock_advance")).json() == {"clock_advanced": True}
        expired = client.post(
            "/api/chat", headers=_headers(),
            json={"message": command, "session": "chat-owned"},
        )
        assert expired.status_code == 403
        assert expired.json() == {"error": "invalid_request"}
        assert _assert_success(client.post("/test/expiry_readback")).json() == {"entry_absent": True}


@pytest.mark.parametrize("path", ["/api/chat", "/api/chat_stream"])
def test_bridge_rejects_bearer_without_browser_provenance(consent_app, path):
    with TestClient(consent_app) as client:
        fixture = client.app.state.fixture
        token = fixture["mcp_raw_tokens"][0]
        prepared = _assert_success(client.post(
            "/test/prepare", headers={"Authorization": "Bearer " + token}))
        request_id = prepared.json()["request_id"]
        client.cookies.set(SESSION_COOKIE, fixture["cookie"])
        challenge = _assert_success(client.post(
            "/api/context/mcp/challenge", headers=_headers(), json={"request_id": request_id},
        ))
        command = "/mcp-context-disclose " + challenge.json()["token"]
        response = client.post(
            path, json={"message": command, "session": "chat-owned"},
            headers={**_headers(), "Authorization": "Bearer rejected"},
        )
    assert response.status_code == 403
    assert response.json() == {"error": "invalid_binding"}


def test_prepare_rejects_invalid_bearer(consent_app):
    with TestClient(consent_app) as client:
        response = client.post("/test/prepare", headers={"Authorization": "Bearer rejected"})
    assert response.status_code == 403
    assert response.json() == {"error": "invalid_binding"}


def test_challenge_body_rejects_extra_and_duplicate_keys(consent_app):
    with TestClient(consent_app) as client:
        fixture = client.app.state.fixture
        token = fixture["mcp_raw_tokens"][0]
        prepared = client.post("/test/prepare", headers={"Authorization": "Bearer " + token})
        request_id = prepared.json()["request_id"]
        client.cookies.set(SESSION_COOKIE, fixture["cookie"])
        headers = {**_headers(), "Content-Type": "application/json"}
        extra = client.post(
            "/api/context/mcp/challenge", headers=headers,
            content=json.dumps({"request_id": request_id, "extra": 1}).encode(),
        )
        assert extra.status_code == 403
        assert extra.json() == {"error": "invalid_binding"}
        duplicate = client.post(
            "/api/context/mcp/challenge", headers=headers,
            content=("{\"request_id\":\"%s\",\"request_id\":\"%s\"}" %
                     (request_id, request_id)).encode(),
        )
        assert duplicate.status_code == 403
        assert duplicate.json() == {"error": "invalid_binding"}


def test_authenticated_challenge_resolution_and_browser_revoke_keep_native_lease(consent_app):
    with TestClient(consent_app) as client:
        fixture = client.app.state.fixture
        prepared = _assert_success(client.post(
            "/test/prepare",
            headers={"Authorization": "Bearer " + fixture["mcp_raw_tokens"][0]},
        ))
        request_id = prepared.json()["request_id"]
        client.cookies.set(SESSION_COOKIE, fixture["cookie"])
        challenge = _assert_success(client.post(
            "/api/context/mcp/challenge",
            json={"request_id": request_id},
            headers=_headers(),
        )).json()["token"]
        owner_sid = fixture["auth"].subject_id_for_username("fuzzy")
        resolved = _assert_success(client.post(
            "/test/resolve_authenticated_challenge",
            json={
                "challenge": challenge,
                "owner_sid": owner_sid,
                "chat_session_id": fixture["chat_id"],
            },
        ))
        assert resolved.json() == {"request_id": request_id}
        wrong_chat = client.post(
            "/test/resolve_authenticated_challenge",
            json={
                "challenge": challenge,
                "owner_sid": owner_sid,
                "chat_session_id": "chat-owned-2",
            },
        )
        assert wrong_chat.status_code == 403
        revoked = _assert_success(client.post(
            "/api/context/mcp/revoke",
            json={"request_id": request_id},
            headers=_headers(),
        ))
        assert revoked.json() == {
            "status": "revoked",
            "entry_absent": True,
            "lease_retained": True,
        }


@pytest.mark.parametrize("kind", [
    "bool", "list", "object", "coroutine", "generator", "awaitable",
    "async_generator", "exception", "cancellation", "primary_revoke",
])
def test_native_callback_failure_never_publishes_or_scrubs_secondary(consent_app, kind):
    with TestClient(consent_app) as client:
        fixture = client.app.state.fixture
        prepared = _assert_success(client.post(
            "/test/prepare", headers={"Authorization": "Bearer " + fixture["mcp_raw_tokens"][0]}))
        request_id = prepared.json()["request_id"]
        client.cookies.set(SESSION_COOKIE, fixture["cookie"])
        challenge = _assert_success(client.post(
            "/api/context/mcp/challenge", headers=_headers(),
            json={"request_id": request_id})).json()
        _assert_success(client.post(
            "/api/chat", headers=_headers(),
            json={"message": "/mcp-context-disclose " + challenge["token"], "session": "chat-owned"}))
        _assert_success(client.post("/test/arm_callback_matrix", json={"kind": kind}))
        denied = client.post("/test/consume")
        assert denied.status_code == 403
        expected = "context_stale" if kind in {"exception", "cancellation", "primary_revoke"} else "invalid_request"
        assert denied.json() == {"error": expected}
        assert _assert_success(client.post("/test/callback_matrix_readback")).json() == {
            "callback_once": True, "deferred_body_not_executed": True,
            "capture_not_published": True, "completion_receipt_not_published": True}
        assert _assert_success(client.post("/test/substitution_readback")).json() == _scrubbed_readback()
        assert _assert_success(client.post("/test/pair_readback")).json() == {
            "pair_count": 2, "parent_count": 2, "roles_fixed": True}


@pytest.mark.parametrize("kind", [
    "delete", "role", "content", "session", "timestamp", "signature",
    "metadata_extra", "metadata_duplicate", "metadata_type",
])
@pytest.mark.parametrize("role", ["user", "assistant"])
@pytest.mark.parametrize("stage", ["permit", "before_execution", "callback"])
def test_native_row_tamper_scrubs_primary_without_publication(consent_app, kind, role, stage):
    with TestClient(consent_app) as client:
        fixture = client.app.state.fixture
        prepared = _assert_success(client.post(
            "/test/prepare", headers={"Authorization": "Bearer " + fixture["mcp_raw_tokens"][0]}))
        request_id = prepared.json()["request_id"]
        client.cookies.set(SESSION_COOKIE, fixture["cookie"])
        challenge = _assert_success(client.post(
            "/api/context/mcp/challenge", headers=_headers(),
            json={"request_id": request_id})).json()
        _assert_success(client.post(
            "/api/chat", headers=_headers(),
            json={"message": "/mcp-context-disclose " + challenge["token"], "session": "chat-owned"}))
        assert _assert_success(client.post("/test/readback")).json() == {
            "pair_verified": True, "owned_row_count": 2}
        _assert_success(client.post("/test/arm_row_tamper", json={"kind": kind, "role": role, "stage": stage}))
        denied = client.post("/test/consume")
        assert denied.status_code == 403
        assert denied.json() == {"error": "readback_failed"}
        assert _assert_success(client.post("/test/row_tamper_readback")).json() == {
            "mutation_once": True, "capture_not_published": True,
            "completion_receipt_not_published": True}
        assert _assert_success(client.post("/test/substitution_readback")).json() == _scrubbed_readback()


@pytest.mark.parametrize("field", [
    "page_bytes", "total_bytes", "offset", "end", "whole_sha256",
    "page_sha256", "manifest_digest", "byteform",
])
def test_native_detached_page_tamper_scrubs_without_completion_publication(consent_app, field):
    with TestClient(consent_app) as client:
        fixture = client.app.state.fixture
        prepared = _assert_success(client.post(
            "/test/prepare", headers={"Authorization": "Bearer " + fixture["mcp_raw_tokens"][0]}))
        request_id = prepared.json()["request_id"]
        client.cookies.set(SESSION_COOKIE, fixture["cookie"])
        challenge = _assert_success(client.post(
            "/api/context/mcp/challenge", headers=_headers(),
            json={"request_id": request_id})).json()
        _assert_success(client.post(
            "/api/chat", headers=_headers(),
            json={"message": "/mcp-context-disclose " + challenge["token"], "session": "chat-owned"}))
        assert _assert_success(client.post("/test/readback")).json() == {
            "pair_verified": True, "owned_row_count": 2}
        _assert_success(client.post("/test/arm_page_tamper", json={"field": field}))
        denied = client.post("/test/consume")
        assert denied.status_code == 403
        assert denied.json() == {"error": "context_stale"}
        assert _assert_success(client.post("/test/page_tamper_readback")).json() == {
            "mutation_once": True, "capture_not_published": True,
            "completion_receipt_not_published": True}
        assert _assert_success(client.post("/test/substitution_readback")).json() == _scrubbed_readback()
        assert _assert_success(client.post("/test/pair_readback")).json() == {
            "pair_count": 2, "parent_count": 2, "roles_fixed": True}


@pytest.mark.parametrize("mode", ["partial", "eof", "past_eof", "target_oob", "nonplanning"])
def test_native_page_boundary_uses_exact_source_range(consent_app, mode):
    with TestClient(consent_app) as client:
        fixture = client.app.state.fixture
        prepared = _assert_success(client.post(
            "/test/prepare", headers={"Authorization": "Bearer " + fixture["mcp_raw_tokens"][0]}))
        request_id = prepared.json()["request_id"]
        client.cookies.set(SESSION_COOKIE, fixture["cookie"])
        challenge = _assert_success(client.post(
            "/api/context/mcp/challenge", headers=_headers(),
            json={"request_id": request_id})).json()
        _assert_success(client.post(
            "/api/chat", headers=_headers(),
            json={"message": "/mcp-context-disclose " + challenge["token"], "session": "chat-owned"}))
        assert _assert_success(client.post("/test/readback")).json() == {
            "pair_verified": True, "owned_row_count": 2}
        _assert_success(client.post("/test/arm_page_boundary", json={"mode": mode}))
        service = client.app.state.service
        entry = service._entries[request_id]
        original_deadlines = (entry.deadline, entry.mono_deadline,
                              entry.ack_deadline, entry.ack_mono_deadline)
        response = client.post("/test/consume")
        if mode in {"past_eof", "target_oob", "nonplanning"}:
            assert response.status_code == 403
            assert response.json() == {"error": "invalid_request"}
            assert _assert_success(client.post("/test/substitution_readback")).json() == _scrubbed_readback()
        else:
            value = _assert_success(response).json()
            assert value["oracle_equal"] and value["manifest_equal"] and value["durable_revision_document"]
            assert value["completion_consumed"] and value["receipt_opaque"]
            assert value["completion_replay"] == value["permit_replay"] == "permit_invalid"
            if mode == "eof":
                assert value["offset"] == value["end"] == value["total_bytes"]
                assert value["page_bytes"] == 0
            else:
                assert value["offset"] == 1
                assert value["end"] == min(value["total_bytes"], 1 + 16384)
                assert value["page_bytes"] == value["end"] - 1
            lifecycle = _assert_success(client.post("/test/substitution_readback")).json()
            assert service._entries[request_id] is entry and entry.state == "disclosed"
            assert original_deadlines == (entry.deadline, entry.mono_deadline,
                                          entry.ack_deadline, entry.ack_mono_deadline)
            assert not lifecycle["request_absent"] and not lifecycle["all42_scrubbed"]
            assert lifecycle["secondary_unchanged"] and lifecycle["permit_ref_cleared"]
            assert lifecycle["permits_empty"] and lifecycle["exchanges_empty"] and lifecycle["completions_empty"]
            # A target EOF may precede other selected sources. The coordinator
            # explicitly revokes at final pack EOF (or cancels a partial pack).
            service.revoke(request_id)
            lifecycle = _assert_success(client.post("/test/substitution_readback")).json()
            assert lifecycle["request_absent"] and lifecycle["all42_scrubbed"] and lifecycle["secondary_unchanged"]
            assert lifecycle["permits_empty"] and lifecycle["exchanges_empty"] and lifecycle["completions_empty"]
        assert _assert_success(client.post("/test/pair_readback")).json() == {
            "pair_count": 2, "parent_count": 2, "roles_fixed": True}


@pytest.mark.parametrize("consent_app", [True], indirect=True)
@pytest.mark.parametrize("mode", ["commit_before", "commit_uncertain", "rollback_failure", "close_failure", "slow_commit"])
def test_native_ack_database_failure_keeps_pair_inert(consent_app, mode):
    with TestClient(consent_app) as client:
        fixture = client.app.state.fixture
        prepared = _assert_success(client.post(
            "/test/prepare", headers={"Authorization": "Bearer " + fixture["mcp_raw_tokens"][0]}))
        request_id = prepared.json()["request_id"]
        client.cookies.set(SESSION_COOKIE, fixture["cookie"])
        challenge = _assert_success(client.post(
            "/api/context/mcp/challenge", headers=_headers(),
            json={"request_id": request_id})).json()
        _assert_success(client.post("/test/arm_ack_db_failure", json={"mode": mode}))
        response = client.post(
            "/api/chat", headers=_headers(),
            json={"message": "/mcp-context-disclose " + challenge["token"], "session": "chat-owned"})
        assert response.status_code == 403
        assert response.json() == {"error": "revoked" if mode == "slow_commit" else "persistence_failed"}
        assert _assert_success(client.post("/test/ack_db_failure_readback")).json() == {
            "stats": {"commit": 1, "rollback": int(mode in {"commit_before", "commit_uncertain", "rollback_failure"}), "close": 1},
            "browser_receipt_not_published": True, "capture_not_published": True,
            "completion_receipt_not_published": True, "fault_patch_released": True}
        expected = _scrubbed_readback()
        expected["pre_exchange_exit_scrubbed"] = True
        assert _assert_success(client.post("/test/substitution_readback")).json() == expected
        committed = mode in {"commit_uncertain", "close_failure", "slow_commit"}
        assert _assert_success(client.post("/test/pair_readback")).json() == {
            "pair_count": 2 if committed else 0, "parent_count": 2 if committed else 0,
            "roles_fixed": committed}


@pytest.mark.parametrize(("path", "wire"), [
    ("/api/chat", "json"), ("/api/chat_stream", "form"), ("/api/chat_stream", "json"),
])
def test_native_replacement_cookie_denied_before_ack(consent_app, path, wire):
    with TestClient(consent_app) as client:
        fixture = client.app.state.fixture
        prepared = _assert_success(client.post(
            "/test/prepare", headers={"Authorization": "Bearer " + fixture["mcp_raw_tokens"][0]}))
        request_id = prepared.json()["request_id"]
        client.cookies.set(SESSION_COOKIE, fixture["cookie"])
        challenge = _assert_success(client.post(
            "/api/context/mcp/challenge", headers=_headers(),
            json={"request_id": request_id})).json()
        # Avoid duplicate cookie names from mixed host-only/domain jar entries.
        client.cookies.clear()
        assert _assert_success(client.post("/test/replace_attached_cookie")).json() == {
            "distinct_cookie": True, "same_subject": True}
        data = {"message": "/mcp-context-disclose " + challenge["token"], "session": "chat-owned"}
        response = client.post(path, headers=_headers(), **({"json": data} if wire == "json" else {"data": data}))
        assert response.status_code == 403
        assert response.json() == {"error": "invalid_binding"}
        assert _assert_success(client.post("/test/replacement_cookie_readback")).json() == {
            "browser_receipt_not_published": True, "capture_not_published": True,
            "completion_receipt_not_published": True}
        assert _assert_success(client.post("/test/substitution_readback")).json() == _scrubbed_readback()
        assert _assert_success(client.post("/test/pair_readback")).json() == {
            "pair_count": 0, "parent_count": 0, "roles_fixed": False}



def test_native_permit_gc_scrubs_exact_request_without_prune_or_consume(consent_app):
    with TestClient(consent_app) as client:
        fixture = client.app.state.fixture
        prepared = _assert_success(client.post(
            "/test/prepare", headers={"Authorization": "Bearer " + fixture["mcp_raw_tokens"][0]}))
        request_id = prepared.json()["request_id"]
        client.cookies.set(SESSION_COOKIE, fixture["cookie"])
        challenge = _assert_success(client.post(
            "/api/context/mcp/challenge", headers=_headers(),
            json={"request_id": request_id})).json()
        _assert_success(client.post(
            "/api/chat", headers=_headers(),
            json={"message": "/mcp-context-disclose " + challenge["token"], "session": "chat-owned"}))
        assert _assert_success(client.post("/test/readback")).json() == {
            "pair_verified": True, "owned_row_count": 2}
        assert _assert_success(client.post("/test/drop_actual_permit")).json() == {
            "actual_permit_was_registered": True, "permit_collected": True}
        assert _assert_success(client.post("/test/substitution_readback")).json() == _scrubbed_readback()
        assert _assert_success(client.post("/test/pair_readback")).json() == {
            "pair_count": 2, "parent_count": 2, "roles_fixed": True}
