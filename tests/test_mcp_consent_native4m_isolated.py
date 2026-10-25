"""Isolated native consent boundary staging with closed scalar diagnostics.

This module reuses the frozen native-boundary fixture and assertions.  The
exact-limit case adds observation only: real elapsed times and the first fixed
MCP authority error code are emitted as one schema-validated JSON line.  No
clock, lease, provider, guard, descriptor, or service callable is replaced.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
import time

import pytest
from fastapi.testclient import TestClient

from routes.auth_routes import SESSION_COOKIE
from src.mcp_context_authority import McpContextAuthorityError
import src.mcp_context_authority as authority_module
from src.mcp_private_context_confirmation import McpPrivateContextConfirmationError
import tests.test_app_mcp_private_context_confirmation as consent_tests
import tests.test_mcp_consent_native_boundaries as native_boundaries


# Preserve the frozen fixture and both original test identities while keeping
# every payload, provider, guard, clock, TTL, and oracle assertion in the
# pinned native-boundary module.
boundary_consent_app = native_boundaries.boundary_consent_app
_CANONICAL_PAYLOAD_LIMIT = getattr(
    native_boundaries,
    "_CANONICAL_PAYLOAD_LIMIT",
    getattr(native_boundaries, "_OVER_OLD_CANONICAL_CAP", 4 * 1024 * 1024 + 256 * 1024),
)
_PAGE_LIMIT = native_boundaries._PAGE_LIMIT

_DIAGNOSTIC_PREFIX = "ODYSSEUS_NATIVE4M_DIAGNOSTIC_V1="
_DIAGNOSTIC_SCHEMA = "odysseus.test.native4m_isolated_diagnostic.v1"
_AUTHORITY_CODES = frozenset({
    "unauthorized",
    "stale_authentication",
    "disabled",
    "context_unavailable",
    "cancelled",
    "timeout",
    "invalid_request",
})
_TIMING_FIELDS = (
    "prepare_s",
    "challenge_s",
    "ack_s",
    "readback_s",
    "arm_s",
    "permit_page_s",
    "lease_to_prepare_s",
    "lease_to_challenge_s",
    "lease_to_ack_s",
    "lease_to_readback_s",
    "lease_to_permit_page_start_s",
    "lease_to_permit_page_end_s",
)
_BOOLEAN_FIELDS = (
    "lease_present_before_permit_page",
    "lease_present_after_permit_page",
)
_PAYLOAD_FIELDS = frozenset({
    "schema",
    "case",
    "diagnostic_status",
    "first_authority_code",
    "terminal_kind",
    "raw_data_emitted",
    *_TIMING_FIELDS,
    *_BOOLEAN_FIELDS,
})


class _FirstAuthorityCodeObserver:
    """Record one fixed authority code without changing the raised error."""

    __slots__ = ("_first", "_lock")

    def __init__(self) -> None:
        self._first = "none"
        self._lock = threading.Lock()

    def record(self, code: object) -> None:
        safe = code if type(code) is str and code in _AUTHORITY_CODES else "unauthorized"
        with self._lock:
            if self._first == "none":
                self._first = safe

    def snapshot(self) -> str:
        with self._lock:
            return self._first


class _Diagnostic:
    """Mutable test-local collector that emits only an immutable scalar map."""

    __slots__ = ("_values", "_lease_issued_mono", "_terminal_kind")

    def __init__(self) -> None:
        self._values = {name: None for name in (*_TIMING_FIELDS, *_BOOLEAN_FIELDS)}
        self._lease_issued_mono = None
        self._terminal_kind = "none"

    @staticmethod
    def _elapsed(start: float, end: float) -> float:
        value = float(end) - float(start)
        if not math.isfinite(value) or value < 0.0 or value > 3600.0:
            raise ValueError("invalid diagnostic duration")
        return round(value, 6)

    def stage(self, name: str, start: float) -> None:
        try:
            if name not in _TIMING_FIELDS or name.startswith("lease_to_"):
                return
            self._values[name] = self._elapsed(start, time.monotonic())
        except BaseException:
            return

    def bind_lease(self, lease: object) -> None:
        try:
            issued = object.__getattribute__(
                lease, "_McpContextReadLease__issued_monotonic",
            )
            if type(issued) not in {int, float} or isinstance(issued, bool):
                return
            issued = float(issued)
            if not math.isfinite(issued):
                return
            self._lease_issued_mono = issued
        except BaseException:
            return

    def lease_mark(self, name: str) -> None:
        try:
            if name not in _TIMING_FIELDS or not name.startswith("lease_to_"):
                return
            if self._lease_issued_mono is None:
                return
            self._values[name] = self._elapsed(
                self._lease_issued_mono, time.monotonic(),
            )
        except BaseException:
            return

    def lease_presence(self, name: str, client: TestClient) -> None:
        try:
            if name not in _BOOLEAN_FIELDS:
                return
            lease = client.app.state.lease
            authority = client.app.state.fixture["authority"]
            with authority._lock:
                row = authority._leases.get(id(lease))
                present = row is not None and row[0]() is lease
            self._values[name] = bool(present)
        except BaseException:
            return

    def terminal(self, error: BaseException) -> None:
        try:
            if type(error) is McpContextAuthorityError:
                self._terminal_kind = "McpContextAuthorityError"
            elif type(error) is McpPrivateContextConfirmationError:
                self._terminal_kind = "McpPrivateContextConfirmationError"
            elif type(error) is AssertionError:
                self._terminal_kind = "AssertionError"
            else:
                self._terminal_kind = "other"
        except BaseException:
            self._terminal_kind = "other"

    def payload(self, first_authority_code: str) -> dict[str, object]:
        values = dict(self._values)
        status = (
            "complete"
            if all(values[name] is not None for name in _TIMING_FIELDS)
            else "partial"
        )
        payload = {
            "schema": _DIAGNOSTIC_SCHEMA,
            "case": "exact-limit",
            "diagnostic_status": status,
            "first_authority_code": first_authority_code,
            "terminal_kind": self._terminal_kind,
            "raw_data_emitted": False,
            **values,
        }
        return _validate_payload(payload)


def _validate_payload(payload: dict[str, object]) -> dict[str, object]:
    if type(payload) is not dict or set(payload) != _PAYLOAD_FIELDS:
        raise ValueError("invalid diagnostic schema")
    if payload["schema"] != _DIAGNOSTIC_SCHEMA or payload["case"] != "exact-limit":
        raise ValueError("invalid diagnostic identity")
    if payload["diagnostic_status"] not in {"complete", "partial", "invalid"}:
        raise ValueError("invalid diagnostic status")
    if payload["first_authority_code"] not in _AUTHORITY_CODES | {"none", "not_observed"}:
        raise ValueError("invalid diagnostic authority code")
    if payload["terminal_kind"] not in {
        "none",
        "McpContextAuthorityError",
        "McpPrivateContextConfirmationError",
        "AssertionError",
        "other",
        "diagnostic_invalid",
    }:
        raise ValueError("invalid diagnostic terminal kind")
    if payload["raw_data_emitted"] is not False:
        raise ValueError("invalid diagnostic raw-data marker")
    for name in _TIMING_FIELDS:
        value = payload[name]
        if value is not None and (
            type(value) is not float
            or not math.isfinite(value)
            or value < 0.0
            or value > 3600.0
        ):
            raise ValueError("invalid diagnostic duration")
    for name in _BOOLEAN_FIELDS:
        value = payload[name]
        if value is not None and type(value) is not bool:
            raise ValueError("invalid diagnostic boolean")
    return payload


def _invalid_payload() -> dict[str, object]:
    payload = {
        "schema": _DIAGNOSTIC_SCHEMA,
        "case": "exact-limit",
        "diagnostic_status": "invalid",
        "first_authority_code": "not_observed",
        "terminal_kind": "diagnostic_invalid",
        "raw_data_emitted": False,
        **{name: None for name in (*_TIMING_FIELDS, *_BOOLEAN_FIELDS)},
    }
    return _validate_payload(payload)


def _emit_diagnostic(diagnostic: _Diagnostic, observer: _FirstAuthorityCodeObserver) -> None:
    try:
        payload = diagnostic.payload(observer.snapshot())
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except BaseException:
        payload = _invalid_payload()
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    try:
        print(_DIAGNOSTIC_PREFIX + encoded, flush=True)
    except BaseException:
        # Diagnostics must never change the boundary test's result.
        pass


def _install_authority_observer(
    monkeypatch: pytest.MonkeyPatch,
    observer: _FirstAuthorityCodeObserver,
):
    original_fail = authority_module._fail

    def observed_fail(code: str) -> None:
        try:
            observer.record(code)
        finally:
            original_fail(code)

    context = monkeypatch.context()
    patch = context.__enter__()
    patch.setattr(authority_module, "_fail", observed_fail)
    return context


def _browser_ack(client: TestClient, diagnostic: _Diagnostic) -> dict[str, object]:
    fixture = client.app.state.fixture

    started = time.monotonic()
    prepared = consent_tests._assert_success(client.post(
        "/test/prepare",
        headers={"Authorization": "Bearer " + fixture["mcp_raw_tokens"][0]},
    ))
    diagnostic.stage("prepare_s", started)
    diagnostic.bind_lease(client.app.state.lease)
    diagnostic.lease_mark("lease_to_prepare_s")
    request_id = prepared.json()["request_id"]

    client.cookies.set(SESSION_COOKIE, fixture["cookie"])
    started = time.monotonic()
    challenge = consent_tests._assert_success(client.post(
        "/api/context/mcp/challenge",
        headers=consent_tests._headers(),
        json={"request_id": request_id},
    )).json()
    diagnostic.stage("challenge_s", started)
    diagnostic.lease_mark("lease_to_challenge_s")

    started = time.monotonic()
    response = consent_tests._assert_success(client.post(
        "/api/chat",
        headers=consent_tests._headers(),
        json={
            "message": "/mcp-context-disclose " + challenge["token"],
            "session": "chat-owned",
        },
    ))
    diagnostic.stage("ack_s", started)
    diagnostic.lease_mark("lease_to_ack_s")
    receipt = response.json()
    assert receipt["request_id"] == request_id
    assert receipt["user_message_id"] != receipt["acknowledgement_message_id"]
    assert challenge["token"] not in response.text

    started = time.monotonic()
    assert consent_tests._assert_success(client.post("/test/readback")).json() == {
        "pair_verified": True,
        "owned_row_count": 2,
    }
    diagnostic.stage("readback_s", started)
    diagnostic.lease_mark("lease_to_readback_s")
    return fixture


@pytest.mark.parametrize("boundary_consent_app", ["exact-limit"], indirect=True)
def test_native_browser_pages_exact_four_mib_accepted_canonical_revision(
    boundary_consent_app,
    monkeypatch,
):
    diagnostic = _Diagnostic()
    observer = _FirstAuthorityCodeObserver()
    patch_context = _install_authority_observer(monkeypatch, observer)
    try:
        with TestClient(boundary_consent_app) as client:
            fixture = _browser_ack(client, diagnostic)
            oracle = fixture["boundary_oracle"]
            assert len(oracle) == _CANONICAL_PAYLOAD_LIMIT
            expected_whole = "sha256:" + hashlib.sha256(oracle).hexdigest()

            started = time.monotonic()
            consent_tests._assert_success(client.post(
                "/test/arm_page_boundary", json={"mode": "partial"},
            ))
            diagnostic.stage("arm_s", started)
            diagnostic.lease_presence("lease_present_before_permit_page", client)
            diagnostic.lease_mark("lease_to_permit_page_start_s")

            started = time.monotonic()
            try:
                page_response = client.post("/test/consume")
            finally:
                diagnostic.stage("permit_page_s", started)
                diagnostic.lease_mark("lease_to_permit_page_end_s")
                diagnostic.lease_presence("lease_present_after_permit_page", client)
            page = consent_tests._assert_success(page_response).json()
            expected_offset = 1
            expected_end = min(len(oracle), expected_offset + _PAGE_LIMIT)
            expected_page = oracle[expected_offset:expected_end]
            assert page["total_bytes"] == len(oracle)
            assert page["offset"] == expected_offset
            assert page["end"] == expected_end
            assert page["page_bytes"] == len(expected_page) <= _PAGE_LIMIT
            assert page["whole_sha256"] == expected_whole
            assert page["page_sha256"] == "sha256:" + hashlib.sha256(expected_page).hexdigest()
            assert page["oracle_equal"] is True
            assert page["manifest_equal"] is True
            assert page["durable_revision_document"] is True
            assert page["receipt_opaque"] is True
            assert page["completion_consumed"] is True
            assert page["completion_replay"] == "permit_invalid"
            assert page["permit_replay"] == "permit_invalid"
            native_boundaries._assert_consumed_lifecycle(client)
    except BaseException as error:
        diagnostic.terminal(error)
        raise
    finally:
        try:
            patch_context.__exit__(None, None, None)
        except BaseException:
            pass
        _emit_diagnostic(diagnostic, observer)


@pytest.mark.parametrize("boundary_consent_app", ["deep-ancestry"], indirect=True)
def test_native_browser_pages_head_with_more_than_32_accepted_ancestors_at_eof(
    boundary_consent_app,
):
    with TestClient(boundary_consent_app) as client:
        fixture = native_boundaries._browser_ack(client)
        oracle = fixture["boundary_oracle"]
        assert fixture["boundary_ancestor_count"] > 32
        expected_whole = "sha256:" + hashlib.sha256(oracle).hexdigest()
        consent_tests._assert_success(client.post(
            "/test/arm_page_boundary", json={"mode": "eof"},
        ))
        page = consent_tests._assert_success(client.post("/test/consume")).json()
        assert page["total_bytes"] == len(oracle)
        assert page["offset"] == page["end"] == len(oracle)
        assert page["page_bytes"] == 0
        assert page["whole_sha256"] == expected_whole
        assert page["page_sha256"] == "sha256:" + hashlib.sha256(b"").hexdigest()
        assert page["oracle_equal"] is True
        assert page["manifest_equal"] is True
        assert page["durable_revision_document"] is True
        assert page["receipt_opaque"] is True
        assert page["completion_consumed"] is True
        assert page["completion_replay"] == "permit_invalid"
        assert page["permit_replay"] == "permit_invalid"
        native_boundaries._assert_consumed_lifecycle(client)
