"""Closed, identity-preserving timing profile for the native global-64 oracle.

The original fixture and test remain the authority.  This module calls that
test once with its exact fixture and observes selected Python code objects via
``sys.setprofile``.  It never replaces a class, instance, descriptor, method,
clock, provider, lease, TTL, fence, or oracle.  No frame locals, arguments,
subprocess output, exception text, or raw profiler records are retained or
emitted.

The sole temporary replacement is ``src.mcp_context_authority._fail``.  That
module helper is outside the native method-identity pins; the observer records
one allowlisted fixed code and immediately calls the exact prior helper from a
``finally`` block.  The prior helper and any prior current-thread profiler are
restored before the diagnostic is emitted.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
import threading
import time
from types import FunctionType

import pytest

from src.context_manifest_producer import CombinedContextManifestProducer
from src.forge_context_metadata import ForgeContextMetadata
from src.forge_context_reader import ImmutableForgeContextReader
from src.mcp_context_authority import (
    McpContextAuthority,
    McpContextAuthorityError,
    McpContextBinding,
    McpContextReadLease,
)
import src.mcp_context_authority as authority_module
from src.mcp_private_context_confirmation import (
    McpPrivateContextConfirmationError,
    McpPrivateContextConfirmationService,
    PreparedRequest,
)
from src.project_forge_local import LocalProjectForge
import tests.test_mcp_consent_global_capacity as global_capacity


native_global_capacity_fixture = global_capacity.native_global_capacity_fixture

_PREFIX = "ODYSSEUS_GLOBAL64_PROFILE_V1="
_SCHEMA = "odysseus.test.global64_isolated_profile.v1"
_ORIGINAL_SHA256 = "e7dfe80c8f399845a97aae955e3074afd3d37f809f606e5954a10809679d4e29"
_AUTHORITY_CODES = frozenset({
    "unauthorized",
    "stale_authentication",
    "disabled",
    "context_unavailable",
    "cancelled",
    "timeout",
    "invalid_request",
})
_TERMINAL_KINDS = frozenset({
    "none",
    "McpContextAuthorityError",
    "McpPrivateContextConfirmationError",
    "AssertionError",
    "other",
})
_TERMINAL_STAGES = frozenset({
    "not_started",
    "issue_initial",
    "preflight",
    "prepare_retained",
    "post_prepare",
    "issue_incoming",
    "prepare_incoming",
    "complete",
})
_DURATION_LIMIT_SECONDS = 7_200.0
_COUNT_LIMIT = 100_000


def _method_code(owner: type, name: str):
    value = owner.__dict__.get(name)
    if type(value) is not FunctionType:
        raise RuntimeError("invalid profile target")
    return value.__code__


_ISSUE_CODE = global_capacity._issue_native_handle.__code__
_PREPARE_CODE = _method_code(McpPrivateContextConfirmationService, "prepare_intent")
_TARGET_CODES = {
    _method_code(CombinedContextManifestProducer, "capture_mcp_snapshot"): "producer_snapshot",
    _method_code(ForgeContextMetadata, "capture_version_metadata_mcp"): "forge_capture",
    _method_code(ForgeContextMetadata, "_authority_snapshot"): "authority_snapshot",
    _method_code(ImmutableForgeContextReader, "inventory"): "inventory",
    _method_code(LocalProjectForge, "_run_git"): "local_forge_git",
    _method_code(ImmutableForgeContextReader, "_run_git_stream"): "reader_git",
}
_METRIC_NAMES = tuple(_TARGET_CODES.values())


def _original_hash_matches() -> bool:
    try:
        path = Path(global_capacity.__file__).resolve(strict=True)
        return hashlib.sha256(path.read_bytes()).hexdigest() == _ORIGINAL_SHA256
    except BaseException:
        return False


class _FirstAuthorityCode:
    __slots__ = ("_code", "_lock")

    def __init__(self) -> None:
        self._code = "none"
        self._lock = threading.Lock()

    def record(self, code: object) -> None:
        safe = code if type(code) is str and code in _AUTHORITY_CODES else "unauthorized"
        with self._lock:
            if self._code == "none":
                self._code = safe

    def snapshot(self) -> str:
        with self._lock:
            return self._code


class _Profile:
    __slots__ = (
        "authority",
        "active",
        "counts",
        "totals",
        "initial_issue_completed_count",
        "initial_issue_started",
        "initial_issue_duration_s",
        "invalid",
        "leases",
        "oldest_survivor_age_before_incoming_s",
        "oldest_survivor_present_before_incoming",
        "incoming_lease_age_before_capacity_s",
        "incoming_lease_present_before_capacity",
        "prepare_attempted_count",
        "prepare_durations",
        "stage",
    )

    def __init__(self, authority: McpContextAuthority) -> None:
        if type(authority) is not McpContextAuthority:
            raise ValueError("invalid profile authority")
        self.authority = authority
        # Retain only the frame identity integer.  Holding a frame object would
        # also retain its locals, which this closed diagnostic forbids.
        self.active: dict[int, tuple[object, float]] = {}
        self.counts = {name: 0 for name in _METRIC_NAMES}
        self.totals = {name: 0.0 for name in _METRIC_NAMES}
        self.initial_issue_completed_count = 0
        self.initial_issue_started: float | None = None
        self.initial_issue_duration_s: float | None = None
        self.invalid = False
        self.leases: list[McpContextReadLease] = []
        self.oldest_survivor_age_before_incoming_s: float | None = None
        self.oldest_survivor_present_before_incoming: bool | None = None
        self.incoming_lease_age_before_capacity_s: float | None = None
        self.incoming_lease_present_before_capacity: bool | None = None
        self.prepare_attempted_count = 0
        self.prepare_durations: list[float] = []
        self.stage = "not_started"

    @staticmethod
    def _elapsed(start: float, end: float) -> float:
        value = float(end) - float(start)
        if not math.isfinite(value) or not 0.0 <= value <= _DURATION_LIMIT_SECONDS:
            raise ValueError("invalid profile duration")
        return value

    @staticmethod
    def _lease_issued_monotonic(lease: object) -> float:
        if not _original_hash_matches() or type(lease) is not McpContextReadLease:
            raise ValueError("invalid profiled lease")
        value = object.__getattribute__(
            lease, "_McpContextReadLease__issued_monotonic",
        )
        if type(value) not in {int, float} or isinstance(value, bool):
            raise ValueError("invalid profiled lease")
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("invalid profiled lease")
        return result

    def _lease_present(self, lease: object) -> bool:
        if not _original_hash_matches() or type(lease) is not McpContextReadLease:
            raise ValueError("invalid profiled lease")
        authority = self.authority
        if type(authority) is not McpContextAuthority:
            raise ValueError("invalid profile authority")
        with authority._lock:
            row = authority._leases.get(id(lease))
            return bool(row is not None and row[0]() is lease)

    def _mark_capacity_ages(self, now: float) -> None:
        if len(self.leases) != 65:
            self.invalid = True
            return
        oldest = self.leases[1]
        incoming = self.leases[64]
        self.oldest_survivor_age_before_incoming_s = self._elapsed(
            self._lease_issued_monotonic(oldest), now,
        )
        self.oldest_survivor_present_before_incoming = self._lease_present(oldest)
        self.incoming_lease_age_before_capacity_s = self._elapsed(
            self._lease_issued_monotonic(incoming), now,
        )
        self.incoming_lease_present_before_capacity = self._lease_present(incoming)

    def __call__(self, frame: object, event: str, arg: object) -> None:
        try:
            code = frame.f_code
            relevant = code is _ISSUE_CODE or code is _PREPARE_CODE or code in _TARGET_CODES
            if not relevant:
                return
            now = time.perf_counter()
            if event == "call":
                self.active[id(frame)] = (code, now)
                if code is _ISSUE_CODE:
                    issue_number = len(self.leases) + 1
                    if issue_number <= 64:
                        self.stage = "issue_initial"
                        if issue_number == 1:
                            self.initial_issue_started = now
                    else:
                        self.stage = "issue_incoming"
                elif code is _PREPARE_CODE:
                    self.prepare_attempted_count += 1
                    if self.prepare_attempted_count <= 64:
                        self.stage = "prepare_retained"
                    else:
                        self.stage = "prepare_incoming"
                        self._mark_capacity_ages(time.monotonic())
                else:
                    name = _TARGET_CODES[code]
                    self.counts[name] += 1
                return
            if event != "return":
                return
            row = self.active.pop(id(frame), None)
            if row is None or row[0] is not code:
                self.invalid = True
                return
            elapsed = self._elapsed(row[1], now)
            if code in _TARGET_CODES:
                self.totals[_TARGET_CODES[code]] += elapsed
                return
            if code is _ISSUE_CODE:
                if (
                    type(arg) is tuple
                    and len(arg) == 2
                    and type(arg[0]) is McpContextBinding
                    and type(arg[1]) is McpContextReadLease
                ):
                    self.leases.append(arg[1])
                    if len(self.leases) <= 64:
                        self.initial_issue_completed_count += 1
                        if len(self.leases) == 64:
                            if self.initial_issue_started is None:
                                self.invalid = True
                            else:
                                self.initial_issue_duration_s = self._elapsed(
                                    self.initial_issue_started, now,
                                )
                            self.stage = "preflight"
                return
            if code is _PREPARE_CODE and type(arg) is PreparedRequest:
                self.prepare_durations.append(elapsed)
                if len(self.prepare_durations) == 64:
                    self.stage = "post_prepare"
        except BaseException:
            self.invalid = True

    def payload(
        self,
        *,
        first_authority_code: str,
        terminal_kind: str,
        test_elapsed_s: float | None,
        profile_installed: bool,
        previous_profile_present: bool,
        original_sha256_match: bool,
    ) -> dict[str, object]:
        completed = len(self.prepare_durations)
        complete = (
            not self.invalid
            and original_sha256_match
            and profile_installed
            and self.stage == "complete"
            and self.initial_issue_completed_count == 64
            and completed == 64
            and self.prepare_attempted_count == 65
            and self.oldest_survivor_age_before_incoming_s is not None
            and self.incoming_lease_age_before_capacity_s is not None
        )
        status = "complete" if complete else "invalid" if self.invalid else "partial"
        timings = tuple(self.prepare_durations)
        payload: dict[str, object] = {
            "schema": _SCHEMA,
            "case": "retained-global-64",
            "diagnostic_status": status,
            "terminal_kind": terminal_kind,
            "terminal_stage": self.stage,
            "first_authority_code": first_authority_code,
            "raw_data_emitted": False,
            "original_sha256_match": original_sha256_match,
            "profile_installed": profile_installed,
            "previous_profile_present": previous_profile_present,
            "test_elapsed_s": _rounded(test_elapsed_s),
            "initial_issue_completed_count": self.initial_issue_completed_count,
            "initial_issue_duration_s": _rounded(self.initial_issue_duration_s),
            "prepare_attempted_count": self.prepare_attempted_count,
            "prepare_completed_count": completed,
            "prepare_total_s": _rounded(sum(timings)) if timings else None,
            "prepare_first_s": _rounded(timings[0]) if timings else None,
            "prepare_median_s": _rounded(statistics.median(timings)) if timings else None,
            "prepare_max_s": _rounded(max(timings)) if timings else None,
            "oldest_survivor_age_before_incoming_s": _rounded(
                self.oldest_survivor_age_before_incoming_s,
            ),
            "oldest_survivor_present_before_incoming": (
                self.oldest_survivor_present_before_incoming
            ),
            "incoming_lease_age_before_capacity_s": _rounded(
                self.incoming_lease_age_before_capacity_s,
            ),
            "incoming_lease_present_before_capacity": (
                self.incoming_lease_present_before_capacity
            ),
        }
        for name in _METRIC_NAMES:
            payload[f"{name}_count"] = self.counts[name]
            payload[f"{name}_total_s"] = _rounded(self.totals[name])
        return _validate_payload(payload)


def _rounded(value: float | None) -> float | None:
    if value is None:
        return None
    result = round(float(value), 6)
    if not math.isfinite(result) or not 0.0 <= result <= _DURATION_LIMIT_SECONDS:
        raise ValueError("invalid profile duration")
    return result


_BASE_KEYS = frozenset({
    "schema",
    "case",
    "diagnostic_status",
    "terminal_kind",
    "terminal_stage",
    "first_authority_code",
    "raw_data_emitted",
    "original_sha256_match",
    "profile_installed",
    "previous_profile_present",
    "test_elapsed_s",
    "initial_issue_completed_count",
    "initial_issue_duration_s",
    "prepare_attempted_count",
    "prepare_completed_count",
    "prepare_total_s",
    "prepare_first_s",
    "prepare_median_s",
    "prepare_max_s",
    "oldest_survivor_age_before_incoming_s",
    "oldest_survivor_present_before_incoming",
    "incoming_lease_age_before_capacity_s",
    "incoming_lease_present_before_capacity",
})
_COUNT_KEYS = frozenset({
    "initial_issue_completed_count",
    "prepare_attempted_count",
    "prepare_completed_count",
    *(f"{name}_count" for name in _METRIC_NAMES),
})
_BOOLEAN_KEYS = frozenset({
    "raw_data_emitted",
    "original_sha256_match",
    "profile_installed",
    "previous_profile_present",
    "oldest_survivor_present_before_incoming",
    "incoming_lease_present_before_capacity",
})
_DURATION_KEYS = frozenset({
    "test_elapsed_s",
    "initial_issue_duration_s",
    "prepare_total_s",
    "prepare_first_s",
    "prepare_median_s",
    "prepare_max_s",
    "oldest_survivor_age_before_incoming_s",
    "incoming_lease_age_before_capacity_s",
    *(f"{name}_total_s" for name in _METRIC_NAMES),
})
_PAYLOAD_KEYS = _BASE_KEYS | _COUNT_KEYS | _DURATION_KEYS


def _validate_payload(payload: dict[str, object]) -> dict[str, object]:
    if type(payload) is not dict or set(payload) != _PAYLOAD_KEYS:
        raise ValueError("invalid profile schema")
    if payload["schema"] != _SCHEMA or payload["case"] != "retained-global-64":
        raise ValueError("invalid profile identity")
    if payload["diagnostic_status"] not in {"complete", "partial", "invalid"}:
        raise ValueError("invalid profile status")
    if payload["terminal_kind"] not in _TERMINAL_KINDS:
        raise ValueError("invalid terminal kind")
    if payload["terminal_stage"] not in _TERMINAL_STAGES:
        raise ValueError("invalid terminal stage")
    if payload["first_authority_code"] not in _AUTHORITY_CODES | {"none", "not_observed"}:
        raise ValueError("invalid authority code")
    for name in _COUNT_KEYS:
        value = payload[name]
        if type(value) is not int or not 0 <= value <= _COUNT_LIMIT:
            raise ValueError("invalid profile count")
    for name in _DURATION_KEYS:
        value = payload[name]
        if value is not None and (
            type(value) is not float
            or not math.isfinite(value)
            or not 0.0 <= value <= _DURATION_LIMIT_SECONDS
        ):
            raise ValueError("invalid profile duration")
    for name in _BOOLEAN_KEYS:
        value = payload[name]
        if value is not None and type(value) is not bool:
            raise ValueError("invalid profile boolean")
    if payload["raw_data_emitted"] is not False:
        raise ValueError("invalid raw-data marker")
    return payload


def _invalid_payload() -> dict[str, object]:
    payload: dict[str, object] = {
        "schema": _SCHEMA,
        "case": "retained-global-64",
        "diagnostic_status": "invalid",
        "terminal_kind": "other",
        "terminal_stage": "not_started",
        "first_authority_code": "not_observed",
        "raw_data_emitted": False,
        "original_sha256_match": False,
        "profile_installed": False,
        "previous_profile_present": bool(sys.getprofile() is not None),
    }
    for name in _COUNT_KEYS:
        payload[name] = 0
    for name in _DURATION_KEYS:
        payload[name] = None
    for name in _BOOLEAN_KEYS - {
        "raw_data_emitted",
        "original_sha256_match",
        "profile_installed",
        "previous_profile_present",
    }:
        payload[name] = None
    return _validate_payload(payload)


def _terminal_kind(error: BaseException | None) -> str:
    if error is None:
        return "none"
    if type(error) is McpContextAuthorityError:
        return "McpContextAuthorityError"
    if type(error) is McpPrivateContextConfirmationError:
        return "McpPrivateContextConfirmationError"
    if type(error) is AssertionError:
        return "AssertionError"
    return "other"


def _emit(profile: _Profile | None, **values: object) -> None:
    try:
        payload = _invalid_payload() if profile is None else profile.payload(**values)
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except BaseException:
        encoded = json.dumps(
            _invalid_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    try:
        print(_PREFIX + encoded, flush=True)
    except BaseException:
        pass


def test_profiled_native_service_retained_global_registry_rejects_distinct_sixty_fifth_owner_lease(
    native_global_capacity_fixture,
):
    """Run the exact original oracle once and emit only a closed scalar profile."""
    fixture = native_global_capacity_fixture
    original_sha256_match = _original_hash_matches()
    assert original_sha256_match
    authority = fixture["authority"]
    profile = _Profile(authority)
    observer = _FirstAuthorityCode()
    previous_profile = sys.getprofile()
    previous_fail = authority_module._fail
    profile_installed = False
    fail_observer_installed = False
    terminal_error: BaseException | None = None
    started = time.perf_counter()

    def observed_fail(code: str) -> None:
        try:
            observer.record(code)
        finally:
            previous_fail(code)

    try:
        if (
            type(previous_fail) is FunctionType
            and previous_fail.__module__ == authority_module.__name__
            and previous_fail.__name__ == "_fail"
            and all(value is not previous_fail for value in McpContextAuthority.__dict__.values())
        ):
            authority_module._fail = observed_fail
            fail_observer_installed = True
        else:
            profile.invalid = True
        if previous_profile is None:
            sys.setprofile(profile)
            profile_installed = True
        else:
            profile.invalid = True
        global_capacity.test_native_service_retained_global_registry_rejects_distinct_sixty_fifth_owner_lease(
            fixture,
        )
        profile.stage = "complete"
    except BaseException as error:
        terminal_error = error
        raise
    finally:
        if profile_installed:
            try:
                sys.setprofile(previous_profile)
            except BaseException:
                profile.invalid = True
        if fail_observer_installed:
            try:
                authority_module._fail = previous_fail
            except BaseException:
                profile.invalid = True
        elapsed = None
        try:
            elapsed = _Profile._elapsed(started, time.perf_counter())
        except BaseException:
            profile.invalid = True
        try:
            _emit(
                profile,
                first_authority_code=observer.snapshot(),
                terminal_kind=_terminal_kind(terminal_error),
                test_elapsed_s=elapsed,
                profile_installed=profile_installed,
                previous_profile_present=previous_profile is not None,
                original_sha256_match=original_sha256_match,
            )
        except BaseException:
            pass

