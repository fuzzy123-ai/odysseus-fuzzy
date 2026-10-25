from __future__ import annotations

import asyncio
import ast
import json
import os
from pathlib import Path
import threading
from contextlib import contextmanager

import pytest

import src.mcp_context_causal_observation as observation


@pytest.fixture(autouse=True)
def _reset_observer_state(monkeypatch):
    monkeypatch.setattr(observation, "_pending_claimed", False)
    monkeypatch.setattr(observation, "_capsule", None)
    monkeypatch.setattr(observation, "_collection_finished", False)
    monkeypatch.setattr(observation, "_content_state", None)
    yield


def _invalid() -> dict[str, object]:
    return observation.fixed_invalid_projection()


def _record(
    literal: str,
    start: float,
    end: float,
    path: tuple[str, ...],
    parent: int | None,
) -> dict[str, object]:
    return {
        "literal": literal,
        "start": start,
        "end": end,
        "depth": len(path),
        "path": path,
        "parent": parent,
    }


def _projection_capsule(
    records: list[dict[str, object]],
    *,
    outer: str = "wait_for_timeout_catch",
    lower: float = 2.0,
    upper: float = 2.0625,
) -> dict[str, object]:
    return {
        "invalid": False,
        "overflow": False,
        "records": records,
        "started": len(records),
        "completed": len(records),
        "deadline_lower": lower,
        "deadline_upper": upper,
        "outer_branch": outer,
        "request_bound": True,
        "seal_count": 1,
        "pid": os.getpid(),
        "thread": threading.current_thread(),
        "thread_ident": threading.get_ident(),
        "wrapper_task": object(),
        "downstream_task": object(),
        "runtime_context_open": False,
    }


def _prefix_records(count: int) -> list[dict[str, object]]:
    traversal = observation._NORMAL_TRAVERSAL[:count]
    paths = [path for path, _ in traversal]
    records: list[dict[str, object]] = []
    for index, (path, parent) in enumerate(traversal):
        last_descendant = max(
            candidate
            for candidate in range(index, count)
            if paths[candidate][:len(path)] == path
        )
        records.append(_record(
            path[-1], float(index * 10), float((last_descendant + 1) * 10 - 1),
            path, parent,
        ))
    return records


def _valid_projection(
    *, outer: str = "exception", event_count: int = 0,
) -> dict[str, object]:
    value = _invalid()
    value.update({
        "measurement": "inconclusive",
        "outer_branch": outer,
        "deadline_relation": (
            "ambiguous" if outer in {
                "wait_for_timeout_catch", "downstream_timeout_observed",
                "timeout_catch_ambiguous",
            } else "not_applicable"
        ),
        "request_binding": "exact",
        "lifecycle_binding": "exact",
        "thread_binding": "exact",
        "task_binding": "distinct_bound_roles",
        "event_count": event_count,
    })
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _enable(monkeypatch) -> None:
    monkeypatch.setenv(observation.DIAGNOSTIC_FLAG, "value-is-ignored")
    monkeypatch.setattr(observation.sys, "version_info", (3, 14, 0))


@contextmanager
def _span(literal: str):
    binding = observation.span_begin(literal)
    assert binding is not None
    try:
        yield
    finally:
        observation.span_end(binding)


def _drive_capture_tree() -> None:
    with _span("snapshot.admission_scope"):
        with _span("snapshot.knowledge_reservation_scope"):
            for phase in ("first", "final"):
                phase_binding = observation.capture_phase_enter(phase)
                assert phase_binding is not None
                try:
                    for source in ("forge", "planning", "knowledge"):
                        source_binding = observation.snapshot_source_begin(source)
                        assert source_binding is not None
                        observation.span_end(source_binding)
                finally:
                    observation.capture_phase_exit(phase_binding)


def _drive_fence(literal: str) -> None:
    with _span(literal):
        with _span("fence.guards_scope"):
            for cookie in ("fence.cookie_first", "fence.cookie_last"):
                with _span(cookie):
                    pass
            for mcp in ("fence.mcp_first", "fence.mcp_last"):
                with _span(mcp):
                    _drive_capture_tree()
            with _span("fence.readback"):
                pass


def _drive_full_native_traversal() -> None:
    with _span("transport.runtime_handle"):
        with _span("manifest.begin"):
            pass
        with _span("manifest.capture"):
            _drive_capture_tree()
        _drive_fence("manifest.fence_1")
        with _span("manifest.encode"):
            pass
        _drive_fence("manifest.fence_2")
        with _span("manifest.finish"):
            pass


async def _distinct_runtime(scope, body):
    async def downstream():
        binding = observation.runtime_context_enter(
            scope, "harbour_context_manifest", "manifest-1",
        )
        assert binding is not None
        try:
            return await body()
        finally:
            observation.runtime_context_exit(binding)

    return await asyncio.create_task(downstream())


async def _execute_observed(scope, call_next, timeout: float):
    capsule = observation.wrapper_enter(scope, "/api/plugins/mcp", timeout)
    assert capsule is not None
    caught: dict[str, BaseException] = {}

    async def observed_call_next():
        observation.timeout_context_enter(scope, timeout)
        try:
            response = await call_next()
        except asyncio.TimeoutError as error:
            caught["inner"] = error
            observation.downstream_timeout(scope)
            raise
        observation.response_start(scope, response.status_code)
        return response

    observation.wrapper_before_wait(scope, timeout)
    try:
        outcome = ("return", await asyncio.wait_for(observed_call_next(), timeout=timeout))
    except asyncio.TimeoutError as error:
        caught["outer"] = error
        observation.wrapper_timeout_catch(scope)
        outcome = ("timeout", error)
    except asyncio.CancelledError as error:
        caught["outer"] = error
        observation.wrapper_cancelled(scope)
        raise
    except BaseException as error:
        caught["outer"] = error
        observation.wrapper_exception(scope)
        outcome = ("exception", error)
    finally:
        observation.seal(scope)
    return outcome, caught, observation.collect_projection(), capsule


def test_fixed_invalid_projection_has_exact_twelve_key_contract():
    value = _invalid()
    assert len(value) == 12
    assert value == observation.parse_projection_bytes(observation.encode_projection(value))
    assert len(observation.encode_projection(value)) <= observation.MAX_PROJECTION_BYTES


def test_fixed_invalid_projection_is_returned_as_a_fresh_copy():
    first = _invalid()
    first["operation_path"].append("manifest.begin")
    assert _invalid()["operation_path"] == []


def test_projection_encoding_is_compact_sorted_and_bounded():
    encoded = observation.encode_projection(_invalid())
    assert encoded == _canonical(_invalid())
    assert b" " not in encoded and b"\n" not in encoded
    assert 0 < len(encoded) <= 768


def test_strict_parser_rejects_duplicate_keys():
    encoded = observation.encode_projection(_invalid())
    duplicate = encoded[:-1] + b',"schema":"odysseus.h7_manifest_observation.v1"}'
    assert observation.parse_projection_bytes(duplicate) == _invalid()


def test_strict_parser_rejects_leading_bytes():
    assert observation.parse_projection_bytes(b" " + observation.encode_projection(_invalid())) == _invalid()


def test_strict_parser_rejects_trailing_bytes():
    assert observation.parse_projection_bytes(observation.encode_projection(_invalid()) + b"\n") == _invalid()


def test_strict_parser_rejects_invalid_utf8():
    assert observation.parse_projection_bytes(b"\xff") == _invalid()


def test_strict_parser_rejects_oversized_input_at_769_bytes():
    assert observation.parse_projection_bytes(b"x" * 769) == _invalid()


def test_strict_parser_rejects_json_constants():
    assert observation.parse_projection_bytes(b'{"value":NaN}') == _invalid()


def test_strict_parser_rejects_bool_as_integer():
    value = _invalid()
    value["event_count"] = True
    assert observation.parse_projection_bytes(_canonical(value)) == _invalid()


def test_strict_parser_rejects_unknown_enum_literal():
    value = _invalid()
    value["outer_branch"] = "unknown"
    assert observation.parse_projection_bytes(_canonical(value)) == _invalid()


def test_cross_field_rule_requires_zero_occurrence_for_empty_path():
    value = _invalid()
    value["operation_occurrence"] = 1
    assert observation.parse_projection_bytes(_canonical(value)) == _invalid()


def test_cross_field_rule_requires_nonempty_path_for_positive_occurrence():
    value = _invalid()
    value["operation_path"] = ["manifest.begin"]
    assert observation.parse_projection_bytes(_canonical(value)) == _invalid()


def test_cross_field_rule_rejects_nominal_measurement_with_wrong_outer_branch():
    value = _invalid()
    value.update({
        "measurement": "nominal_deadline_overlap_observed",
        "outer_branch": "handler_response_504",
        "deadline_relation": "instrumented_nominal_overlap_ge1s",
        "operation_path": ["manifest.begin"],
        "operation_occurrence": 1,
        "request_binding": "exact",
        "lifecycle_binding": "exact",
        "thread_binding": "exact",
        "task_binding": "distinct_bound_roles",
    })
    assert observation.parse_projection_bytes(_canonical(value)) == _invalid()


def test_valid_noninvalid_projection_round_trips_canonically():
    value = _valid_projection(outer="exception", event_count=2)
    assert observation.parse_projection_bytes(observation.encode_projection(value)) == value


def test_nominal_projection_requires_event_count_reaching_exact_native_path():
    path, _ = observation._NORMAL_TRAVERSAL[10]
    value = _valid_projection(outer="wait_for_timeout_catch", event_count=10)
    value.update({
        "measurement": "nominal_deadline_overlap_observed",
        "deadline_relation": "instrumented_nominal_overlap_ge1s",
        "operation_path": list(path),
        "operation_occurrence": 1,
    })
    assert observation.parse_projection_bytes(_canonical(value)) == _invalid()


@pytest.mark.parametrize(
    "outer",
    ["response_start_returned", "handler_response_504"],
    ids=["returned_other_status", "returned_504"],
)
def test_parser_accepts_closed_returned_prefixes_without_private_status(outer):
    for event_count in (0, 2, 59):
        value = _valid_projection(outer=outer, event_count=event_count)
        assert observation.parse_projection_bytes(_canonical(value)) == value


@pytest.mark.parametrize(
    "outer",
    ["response_start_returned", "handler_response_504"],
    ids=["returned_other_status", "returned_504"],
)
def test_parser_rejects_returned_prefix_beyond_frozen_traversal(outer):
    value = _valid_projection(outer=outer, event_count=60)
    assert observation.parse_projection_bytes(_canonical(value)) == _invalid()


def test_parser_rejects_returned_prefix_with_timeout_relation():
    value = _valid_projection(outer="response_start_returned", event_count=2)
    value["deadline_relation"] = "ambiguous"
    assert observation.parse_projection_bytes(_canonical(value)) == _invalid()


def test_same_timeout_owner_and_runtime_task_is_fixed_invalid():
    capsule = _projection_capsule([
        _record("manifest.capture", 0.0, 4.0, ("manifest.capture",), None),
    ])
    capsule["downstream_task"] = capsule["wrapper_task"]
    assert observation._projection(capsule) == _invalid()


@pytest.mark.parametrize(
    ("key", "replacement"),
    [("pid", -1), ("thread", object()), ("thread_ident", -1)],
)
def test_pid_or_thread_mismatch_is_fixed_invalid(key, replacement):
    capsule = _projection_capsule([])
    capsule[key] = replacement
    assert observation._projection(capsule) == _invalid()


def test_open_span_or_incomplete_record_is_fixed_invalid():
    records = [_record("manifest.begin", 0.0, 1.0, ("manifest.begin",), None)]
    records[0]["end"] = None
    assert observation._projection(_projection_capsule(records)) == _invalid()


def test_improper_parent_nesting_is_fixed_invalid():
    records = [
        _record("transport.runtime_handle", 0.0, 1.0, ("transport.runtime_handle",), None),
        _record(
            "manifest.begin", 0.5, 2.0,
            ("transport.runtime_handle", "manifest.begin"), 0,
        ),
    ]
    assert observation._projection(_projection_capsule(records)) == _invalid()


def test_completed_record_overflow_is_fixed_invalid():
    records = [
        _record("manifest.begin", float(index), float(index) + 0.5, ("manifest.begin",), None)
        for index in range(65)
    ]
    assert observation._projection(_projection_capsule(records)) == _invalid()


def test_depth_overflow_is_fixed_invalid():
    path = tuple(["manifest.begin"] * 9)
    records = [_record("manifest.begin", 0.0, 4.0, path, None)]
    assert observation._projection(_projection_capsule(records)) == _invalid()


@pytest.mark.asyncio
async def test_inactive_mode_does_not_arm_scope(monkeypatch):
    monkeypatch.delenv(observation.DIAGNOSTIC_FLAG, raising=False)
    scope: dict[object, object] = {}
    assert observation.wrapper_enter(scope, "/api/plugins/mcp", 45.0) is None
    assert scope == {}


@pytest.mark.asyncio
async def test_wrong_path_does_not_claim_pending_capsule(monkeypatch):
    _enable(monkeypatch)
    scope: dict[object, object] = {}
    assert observation.wrapper_enter(scope, "/api/chat", 45.0) is None
    assert scope == {}


@pytest.mark.asyncio
async def test_wrong_method_and_rpc_do_not_bind_runtime(monkeypatch):
    _enable(monkeypatch)
    scope: dict[object, object] = {}
    observation.wrapper_enter(scope, "/api/plugins/mcp", 45.0)
    observation.wrapper_before_wait(scope, 45.0)
    assert observation.runtime_context_enter(scope, "harbour_context_prepare", "manifest-1") is None
    assert observation.runtime_context_enter(scope, "harbour_context_manifest", "manifest-2") is None
    observation.seal(scope)
    assert observation._capsule is None


@pytest.mark.asyncio
async def test_runtime_binding_rejects_timeout_owner_as_downstream(monkeypatch):
    _enable(monkeypatch)
    scope: dict[object, object] = {}
    capsule = observation.wrapper_enter(scope, "/api/plugins/mcp", 45.0)
    observation.wrapper_before_wait(scope, 45.0)
    observation.timeout_context_enter(scope, 45.0)
    assert observation.runtime_context_enter(
        scope, "harbour_context_manifest", "manifest-1",
    ) is None
    assert capsule["invalid"] is True
    observation.wrapper_exception(scope)
    observation.seal(scope)
    assert observation.collect_projection() == _invalid()


@pytest.mark.asyncio
async def test_capture_helpers_reject_inherited_context_on_wrong_task(monkeypatch):
    _enable(monkeypatch)
    scope: dict[object, object] = {}
    capsule = observation.wrapper_enter(scope, "/api/plugins/mcp", 45.0)
    observation.wrapper_before_wait(scope, 45.0)
    observation.timeout_context_enter(scope, 45.0)

    async def downstream():
        binding = observation.runtime_context_enter(
            scope, "harbour_context_manifest", "manifest-1",
        )
        assert binding is not None
        try:
            async def wrong_task():
                return observation.capture_phase_enter("first")
            assert await asyncio.create_task(wrong_task()) is None
        finally:
            observation.runtime_context_exit(binding)

    await asyncio.create_task(downstream())
    assert capsule["invalid"] is True
    observation.wrapper_exception(scope)
    observation.seal(scope)
    assert observation.collect_projection() == _invalid()


@pytest.mark.asyncio
async def test_wrapper_hooks_reject_loop_and_task_mismatch(monkeypatch):
    _enable(monkeypatch)
    scope: dict[object, object] = {}
    capsule = observation.wrapper_enter(scope, "/api/plugins/mcp", 45.0)
    observation.wrapper_before_wait(scope, 45.0)
    observation.timeout_context_enter(scope, 45.0)

    async def wrong_task():
        observation.response_start(scope, 200)

    await asyncio.create_task(wrong_task())
    assert capsule["invalid"] is True
    capsule["invalid"] = False
    capsule["loop"] = object()
    observation.wrapper_exception(scope)
    assert capsule["invalid"] is True
    observation.seal(scope)


@pytest.mark.asyncio
async def test_second_arm_is_rejected_while_first_is_pending(monkeypatch):
    _enable(monkeypatch)
    first: dict[object, object] = {}
    second: dict[object, object] = {}
    assert observation.wrapper_enter(first, "/api/plugins/mcp", 45.0) is not None
    assert observation.wrapper_enter(second, "/api/plugins/mcp", 45.0) is None
    observation.seal(first)


@pytest.mark.asyncio
async def test_response_cancel_and_exception_branches_are_fixed_literals(monkeypatch):
    _enable(monkeypatch)
    scope: dict[object, object] = {}
    capsule = observation.wrapper_enter(scope, "/api/plugins/mcp", 45.0)
    observation.response_start(scope, 504)
    assert capsule["outer_branch"] == "handler_response_504"
    observation.wrapper_cancelled(scope)
    assert capsule["outer_branch"] == "cancelled"
    observation.wrapper_exception(scope)
    assert capsule["outer_branch"] == "exception"
    observation.seal(scope)


def test_serializer_failure_returns_only_fixed_invalid(monkeypatch):
    monkeypatch.setattr(observation.json, "dumps", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError()))
    encoded = observation.encode_projection({"unexpected": object()})
    assert b"odysseus.h7_manifest_observation.v1" in encoded
    assert len(encoded) <= 768


def test_repeated_collection_invalidates_replay(monkeypatch):
    value = _valid_projection(outer="exception", event_count=0)
    capsule = {
        "phase": "sealed", "collect_count": 0, "invalid": False,
        "projection": value, "records": [], "pid": 1, "thread": object(),
        "thread_ident": 1, "loop": object(), "wrapper_task": object(),
        "downstream_task": object(), "wrapper_entry": 1.0,
        "deadline_lower": 2.0, "deadline_upper": 2.1,
    }
    monkeypatch.setattr(observation, "_capsule", capsule)
    monkeypatch.setattr(observation, "_collection_finished", False)
    assert observation.collect_projection() == value
    assert observation.collect_projection() == _invalid()
    assert capsule["phase"] == "collected"
    assert capsule["records"] == [] and capsule["wrapper_task"] is None
    assert capsule["_response_status_class"] is None


@pytest.mark.asyncio
async def test_clock_helper_failure_does_not_escape(monkeypatch):
    monkeypatch.setenv(observation.DIAGNOSTIC_FLAG, "ignored")
    monkeypatch.setattr(
        observation.asyncio, "get_running_loop",
        lambda: (_ for _ in ()).throw(RuntimeError()),
    )
    scope: dict[object, object] = {}
    assert observation.wrapper_enter(scope, "/api/plugins/mcp", 45.0) is None
    assert scope == {}


@pytest.mark.asyncio
async def test_full_native_hook_path_uses_distinct_task_and_emits_exact_59(monkeypatch):
    _enable(monkeypatch)
    scope: dict[object, object] = {}
    response = type("Response", (), {"status_code": 200, "body": object()})()
    seen: dict[str, object] = {}

    async def body():
        _drive_full_native_traversal()
        capsule = observation._capsule
        records = capsule["records"]
        seen["count"] = len(records)
        seen["depth"] = max(record["depth"] for record in records)
        seen["occurrence"] = max(
            sum(1 for record in records if record["literal"] == literal)
            for literal in observation.SPAN_LITERALS
        )
        seen["cursor"] = any("cursor" in record["literal"] for record in records)
        return response

    outcome, _, projection, capsule = await _execute_observed(
        scope, lambda: _distinct_runtime(scope, body), 45.0,
    )
    assert outcome == ("return", response)
    assert seen == {"count": 59, "depth": 7, "occurrence": 5, "cursor": False}
    assert projection == _valid_projection(outer="response_start_returned", event_count=59)
    assert capsule["wrapper_task"] is None and capsule["downstream_task"] is None
    assert capsule["_response_status_class"] is None


@pytest.mark.asyncio
async def test_actual_started_hook_accepts_64th_and_rejects_65th_without_truncation(
    monkeypatch,
):
    _enable(monkeypatch)
    scope: dict[object, object] = {}
    capsule = observation.wrapper_enter(scope, "/api/plugins/mcp", 45.0)
    observation.wrapper_before_wait(scope, 45.0)
    observation.timeout_context_enter(scope, 45.0)

    async def downstream():
        binding = observation.runtime_context_enter(
            scope, "harbour_context_manifest", "manifest-1",
        )
        assert binding is not None
        try:
            for _ in range(64):
                span = observation.span_begin("transport.runtime_handle")
                assert span is not None
                observation.span_end(span)
            assert len(capsule["records"]) == 64
            assert capsule["started"] == 64 and capsule["completed"] == 64
            assert observation.span_begin("transport.runtime_handle") is None
            assert len(capsule["records"]) == 64
            assert capsule["started"] == 64 and capsule["completed"] == 64
        finally:
            observation.runtime_context_exit(binding)

    await asyncio.create_task(downstream())
    assert capsule["overflow"] is True and capsule["invalid"] is True
    observation.wrapper_exception(scope)
    observation.seal(scope)
    assert observation.collect_projection() == _invalid()


@pytest.mark.asyncio
async def test_actual_nested_hooks_accept_depth_8_and_reject_depth_9_without_truncation(
    monkeypatch,
):
    _enable(monkeypatch)
    scope: dict[object, object] = {}
    capsule = observation.wrapper_enter(scope, "/api/plugins/mcp", 45.0)
    observation.wrapper_before_wait(scope, 45.0)
    observation.timeout_context_enter(scope, 45.0)

    async def downstream():
        binding = observation.runtime_context_enter(
            scope, "harbour_context_manifest", "manifest-1",
        )
        assert binding is not None
        spans = []
        try:
            for _ in range(8):
                span = observation.span_begin("transport.runtime_handle")
                assert span is not None
                spans.append(span)
            assert len(capsule["records"]) == 8 and capsule["started"] == 8
            assert max(record["depth"] for record in capsule["records"]) == 8
            assert observation.span_begin("transport.runtime_handle") is None
            assert len(capsule["records"]) == 8 and capsule["started"] == 8
        finally:
            for span in reversed(spans):
                observation.span_end(span)
            observation.runtime_context_exit(binding)

    await asyncio.create_task(downstream())
    assert capsule["completed"] == 8
    assert capsule["overflow"] is True and capsule["invalid"] is True
    observation.wrapper_exception(scope)
    observation.seal(scope)
    assert observation.collect_projection() == _invalid()


@pytest.mark.asyncio
async def test_returned_504_preserves_response_and_body_identity(monkeypatch):
    _enable(monkeypatch)
    scope: dict[object, object] = {}
    body_identity = object()
    response = type("Response", (), {"status_code": 504, "body": body_identity})()

    async def body():
        _drive_full_native_traversal()
        return response

    outcome, _, projection, _ = await _execute_observed(
        scope, lambda: _distinct_runtime(scope, body), 45.0,
    )
    assert outcome[0] == "return" and outcome[1] is response
    assert outcome[1].body is body_identity
    assert projection["outer_branch"] == "handler_response_504"
    assert projection["event_count"] == 59


@pytest.mark.parametrize(
    ("status_code", "outer"),
    [(504, "handler_response_504"), (418, "response_start_returned")],
    ids=["early_504", "early_other_status"],
)
@pytest.mark.asyncio
async def test_early_error_response_prefix_preserves_response_and_body_identity(
    monkeypatch, status_code, outer,
):
    _enable(monkeypatch)
    scope: dict[object, object] = {}
    body_identity = object()
    response = type(
        "Response", (), {"status_code": status_code, "body": body_identity},
    )()

    async def body():
        with _span("transport.runtime_handle"):
            with _span("manifest.begin"):
                pass
        return response

    outcome, _, projection, _ = await _execute_observed(
        scope, lambda: _distinct_runtime(scope, body), 45.0,
    )
    assert outcome == ("return", response)
    assert outcome[1].body is body_identity
    assert projection == _valid_projection(outer=outer, event_count=2)


@pytest.mark.asyncio
async def test_exact_200_short_prefix_is_private_producer_invalid(monkeypatch):
    _enable(monkeypatch)
    scope: dict[object, object] = {}
    response = type("Response", (), {"status_code": 200})()

    async def body():
        with _span("transport.runtime_handle"):
            with _span("manifest.begin"):
                pass
        return response

    outcome, _, projection, capsule = await _execute_observed(
        scope, lambda: _distinct_runtime(scope, body), 45.0,
    )
    assert outcome == ("return", response)
    assert projection == _invalid()
    assert capsule["_response_status_class"] is None


@pytest.mark.asyncio
async def test_bool_status_is_private_nonnormal_returned_prefix(monkeypatch):
    _enable(monkeypatch)
    scope: dict[object, object] = {}
    response = type("Response", (), {"status_code": True})()

    async def body():
        with _span("transport.runtime_handle"):
            with _span("manifest.begin"):
                pass
        return response

    outcome, _, projection, _ = await _execute_observed(
        scope, lambda: _distinct_runtime(scope, body), 45.0,
    )
    assert outcome == ("return", response)
    assert projection == _valid_projection(outer="response_start_returned", event_count=2)


@pytest.mark.asyncio
async def test_early_exception_prefix_is_legal_and_same_object_is_observed(monkeypatch):
    _enable(monkeypatch)
    scope: dict[object, object] = {}
    error = RuntimeError("not serialized")

    async def body():
        with _span("transport.runtime_handle"):
            with _span("manifest.begin"):
                raise error

    outcome, caught, projection, _ = await _execute_observed(
        scope, lambda: _distinct_runtime(scope, body), 45.0,
    )
    assert outcome == ("exception", error)
    assert caught["outer"] is error
    assert projection == _valid_projection(outer="exception", event_count=2)


@pytest.mark.asyncio
async def test_real_wait_for_own_expiry_is_only_outer_timeout_catch(monkeypatch):
    _enable(monkeypatch)
    scope: dict[object, object] = {}

    async def body():
        with _span("transport.runtime_handle"):
            await asyncio.sleep(60)

    outcome, caught, projection, _ = await _execute_observed(
        scope, lambda: _distinct_runtime(scope, body), 0.01,
    )
    assert outcome[0] == "timeout"
    assert "inner" not in caught
    assert projection["outer_branch"] == "wait_for_timeout_catch"
    assert projection["measurement"] == "inconclusive"


@pytest.mark.asyncio
async def test_real_downstream_timeout_is_same_identity_and_bare_propagation(monkeypatch):
    _enable(monkeypatch)
    scope: dict[object, object] = {}
    error = asyncio.TimeoutError("not serialized")

    async def body():
        with _span("transport.runtime_handle"):
            raise error

    outcome, caught, projection, _ = await _execute_observed(
        scope, lambda: _distinct_runtime(scope, body), 45.0,
    )
    assert outcome == ("timeout", error)
    assert caught["inner"] is error and caught["outer"] is error
    assert projection["outer_branch"] == "downstream_timeout_observed"
    assert projection["measurement"] == "inconclusive"


@pytest.mark.asyncio
async def test_cancellation_cleanup_timeout_is_ambiguous(monkeypatch):
    _enable(monkeypatch)
    scope: dict[object, object] = {}
    cleanup_error = asyncio.TimeoutError("not serialized")

    async def body():
        with _span("transport.runtime_handle"):
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                raise cleanup_error

    outcome, caught, projection, _ = await _execute_observed(
        scope, lambda: _distinct_runtime(scope, body), 0.01,
    )
    assert outcome[0] == "timeout"
    assert caught["inner"] is cleanup_error
    assert projection["outer_branch"] == "timeout_catch_ambiguous"


@pytest.mark.asyncio
async def test_timeout_cancellation_suppression_preserves_normal_response(monkeypatch):
    _enable(monkeypatch)
    scope: dict[object, object] = {}
    response = type("Response", (), {"status_code": 200})()

    async def body():
        _drive_full_native_traversal()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            return response

    outcome, caught, projection, _ = await _execute_observed(
        scope, lambda: _distinct_runtime(scope, body), 0.01,
    )
    assert outcome == ("return", response)
    assert "inner" not in caught
    assert projection["outer_branch"] == "response_start_returned"


@pytest.mark.asyncio
async def test_external_cancellation_propagates_and_records_cancelled(monkeypatch):
    _enable(monkeypatch)
    scope: dict[object, object] = {}
    entered = asyncio.Event()

    async def body():
        with _span("transport.runtime_handle"):
            entered.set()
            await asyncio.sleep(60)

    task = asyncio.create_task(
        _execute_observed(scope, lambda: _distinct_runtime(scope, body), 45.0),
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    projection = observation.collect_projection()
    assert projection["outer_branch"] == "cancelled"
    assert projection["measurement"] == "inconclusive"


@pytest.mark.asyncio
async def test_real_cancellation_count_change_marks_downstream_timeout_ambiguous(monkeypatch):
    _enable(monkeypatch)
    scope: dict[object, object] = {}
    capsule = observation.wrapper_enter(scope, "/api/plugins/mcp", 45.0)
    observation.wrapper_before_wait(scope, 45.0)
    observation.timeout_context_enter(scope, 45.0)
    task = asyncio.current_task()
    task.cancel()
    try:
        await asyncio.sleep(0)
    except asyncio.CancelledError:
        observation.downstream_timeout(scope)
    finally:
        task.uncancel()
    assert capsule["outer_branch"] == "timeout_catch_ambiguous"
    observation.seal(scope)


@pytest.mark.asyncio
async def test_runtime_context_success_resets_all_contextvars(monkeypatch):
    _enable(monkeypatch)
    scope: dict[object, object] = {}
    capsule = observation.wrapper_enter(scope, "/api/plugins/mcp", 45.0)
    observation.wrapper_before_wait(scope, 45.0)
    observation.timeout_context_enter(scope, 45.0)

    async def downstream():
        binding = observation.runtime_context_enter(
            scope, "harbour_context_manifest", "manifest-1",
        )
        assert binding is not None
        with _span("transport.runtime_handle"):
            pass
        observation.runtime_context_exit(binding)
        return (
            observation._ACTIVE_CAPSULE.get(), observation._SPAN_STACK.get(),
            observation._CAPTURE_PHASE.get(), asyncio.current_task(),
        )

    active, stack, phase, downstream_task = await asyncio.create_task(downstream())
    assert (active, stack, phase) == (None, (), None)
    assert downstream_task is capsule["downstream_task"]
    assert downstream_task is not capsule["wrapper_task"]
    observation.wrapper_exception(scope)
    observation.seal(scope)
    projection = observation.collect_projection()
    assert projection == _valid_projection(outer="exception", event_count=1)


@pytest.mark.parametrize("failure", ["stack", "phase"])
@pytest.mark.asyncio
async def test_runtime_context_partial_set_failure_rolls_back_each_set_token(
    monkeypatch, failure,
):
    _enable(monkeypatch)
    scope: dict[object, object] = {}
    observation.wrapper_enter(scope, "/api/plugins/mcp", 45.0)
    observation.wrapper_before_wait(scope, 45.0)
    observation.timeout_context_enter(scope, 45.0)
    calls: list[str] = []

    class Variable:
        def __init__(self, name):
            self.name = name

        def set(self, value):
            calls.append("set:" + self.name)
            if self.name == failure:
                raise RuntimeError("helper")
            return self.name + "-token"

        def reset(self, token):
            calls.append("reset:" + self.name)

    monkeypatch.setattr(observation, "_ACTIVE_CAPSULE", Variable("active"))
    monkeypatch.setattr(observation, "_SPAN_STACK", Variable("stack"))
    monkeypatch.setattr(observation, "_CAPTURE_PHASE", Variable("phase"))

    async def downstream():
        return observation.runtime_context_enter(
            scope, "harbour_context_manifest", "manifest-1",
        )

    assert await asyncio.create_task(downstream()) is None
    if failure == "stack":
        assert calls == ["set:active", "set:stack", "reset:active"]
    else:
        assert calls == [
            "set:active", "set:stack", "set:phase", "reset:stack", "reset:active",
        ]


@pytest.mark.asyncio
async def test_runtime_context_post_set_state_failure_rolls_back_all_tokens(monkeypatch):
    _enable(monkeypatch)
    scope: dict[object, object] = {}
    capsule = observation.wrapper_enter(scope, "/api/plugins/mcp", 45.0)
    assert type(capsule) is dict
    observation.wrapper_before_wait(scope, 45.0)
    observation.timeout_context_enter(scope, 45.0)
    calls: list[str] = []
    lock_entries = 0

    class Variable:
        def __init__(self, name):
            self.name = name

        def set(self, value):
            calls.append("set:" + self.name)
            return self.name + "-token"

        def reset(self, token):
            calls.append("reset:" + self.name)

    original_lock = observation._LOCK

    class Lock:
        def __enter__(self):
            nonlocal lock_entries
            lock_entries += 1
            if lock_entries == 2:
                raise RuntimeError("state")
            return original_lock.__enter__()

        def __exit__(self, *args):
            return original_lock.__exit__(*args)

    monkeypatch.setattr(observation, "_LOCK", Lock())
    monkeypatch.setattr(observation, "_ACTIVE_CAPSULE", Variable("active"))
    monkeypatch.setattr(observation, "_SPAN_STACK", Variable("stack"))
    monkeypatch.setattr(observation, "_CAPTURE_PHASE", Variable("phase"))

    async def downstream():
        return (
            observation.runtime_context_enter(
                scope, "harbour_context_manifest", "manifest-1",
            ),
            asyncio.current_task(),
        )

    downstream_task = asyncio.create_task(downstream())
    result, actual_task = await downstream_task
    assert result is None
    assert actual_task is downstream_task
    assert actual_task is not capsule["wrapper_task"]
    assert calls == [
        "set:active", "set:stack", "set:phase",
        "reset:phase", "reset:stack", "reset:active",
    ]
    assert lock_entries == 3
    assert capsule["invalid"] is True
    assert capsule["downstream_task"] is None
    assert capsule["request_bound"] is False
    assert capsule["runtime_context_open"] is False


def test_runtime_context_exit_role_mismatch_attempts_every_reset(monkeypatch):
    calls: list[str] = []

    class Variable:
        def __init__(self, name, value):
            self.name, self.value = name, value

        def get(self):
            return self.value

        def reset(self, token):
            calls.append(self.name)
            if self.name == "stack":
                raise RuntimeError("helper")

    capsule = {
        "invalid": False, "runtime_context_open": True,
        "pid": os.getpid(), "thread": threading.current_thread(),
        "thread_ident": threading.get_ident(), "loop": object(),
        "wrapper_task": object(), "downstream_task": object(),
    }
    monkeypatch.setattr(observation, "_CAPTURE_PHASE", Variable("phase", None))
    monkeypatch.setattr(observation, "_SPAN_STACK", Variable("stack", ()))
    monkeypatch.setattr(observation, "_ACTIVE_CAPSULE", Variable("active", capsule))
    observation.runtime_context_exit((capsule, "a", "s", "p"))
    assert calls == ["phase", "stack", "active"]
    assert capsule["invalid"] is True and capsule["runtime_context_open"] is True


@pytest.mark.parametrize(
    ("lower", "upper"),
    [
        (float("nan"), 2.0), (2.0, float("nan")),
        (float("inf"), 2.0), (2.0, float("inf")),
        (2.1, 2.0), (2.0, 2.1001),
    ],
)
def test_invalid_deadline_clocks_and_entry_gap_are_fixed_invalid(lower, upper):
    capsule = _projection_capsule([], lower=lower, upper=upper)
    assert observation._projection(capsule) == _invalid()


def test_equal_deadline_bounds_are_allowed_but_equal_span_start_is_not_overlap():
    path, parent = observation._NORMAL_TRAVERSAL[0]
    records = [_record(path[-1], 2.0, 4.0, path, parent)]
    value = observation._projection(_projection_capsule(records, lower=2.0, upper=2.0))
    assert value["measurement"] == "inconclusive"
    assert value["operation_path"] == []


def test_pure_child_gap_rejects_false_coarse_ancestor_attribution():
    root_path, root_parent = observation._NORMAL_TRAVERSAL[0]
    child_path, child_parent = observation._NORMAL_TRAVERSAL[1]
    records = [
        _record(root_path[-1], 0.0, 4.0, root_path, root_parent),
        _record(child_path[-1], 0.1, 0.5, child_path, child_parent),
    ]
    value = observation._projection(
        _projection_capsule(records, lower=1.0, upper=1.0625),
    )
    assert value["measurement"] == "inconclusive"
    assert value["operation_path"] == [] and value["operation_occurrence"] == 0


@pytest.mark.parametrize(
    ("child_start", "child_end"),
    [(0.5, 1.05), (1.05, 1.5), (0.5, 1.5)],
    ids=["overlaps_lower", "straddles_upper", "insufficient_margin"],
)
def test_positive_child_overlap_preserves_coarse_parent_without_child_margin(
    child_start, child_end,
):
    root_path, root_parent = observation._NORMAL_TRAVERSAL[0]
    child_path, child_parent = observation._NORMAL_TRAVERSAL[1]
    records = [
        _record(root_path[-1], 0.0, 4.0, root_path, root_parent),
        _record(child_path[-1], child_start, child_end, child_path, child_parent),
    ]
    value = observation._projection(
        _projection_capsule(records, lower=1.0, upper=1.0625),
    )
    assert value["measurement"] == "nominal_deadline_overlap_observed"
    assert value["operation_path"] == list(root_path)
    assert value["operation_occurrence"] == 1


@pytest.mark.parametrize(
    ("child_start", "child_end"),
    [(0.1, 1.0), (1.0625, 1.5)],
    ids=["ends_at_lower", "starts_at_upper"],
)
def test_equal_boundary_child_contact_is_not_positive_overlap(child_start, child_end):
    root_path, root_parent = observation._NORMAL_TRAVERSAL[0]
    child_path, child_parent = observation._NORMAL_TRAVERSAL[1]
    records = [
        _record(root_path[-1], 0.0, 4.0, root_path, root_parent),
        _record(child_path[-1], child_start, child_end, child_path, child_parent),
    ]
    value = observation._projection(
        _projection_capsule(records, lower=1.0, upper=1.0625),
    )
    assert value["measurement"] == "inconclusive"
    assert value["operation_path"] == [] and value["operation_occurrence"] == 0


@pytest.mark.asyncio
async def test_actual_full_hooks_project_and_parse_fifth_terminal_unique_full_path(
    monkeypatch,
):
    _enable(monkeypatch)
    scope: dict[object, object] = {}
    capsule = observation.wrapper_enter(scope, "/api/plugins/mcp", 45.0)
    observation.wrapper_before_wait(scope, 45.0)
    observation.timeout_context_enter(scope, 45.0)
    selected: dict[str, object] = {}

    async def downstream():
        binding = observation.runtime_context_enter(
            scope, "harbour_context_manifest", "manifest-1",
        )
        assert binding is not None
        try:
            _drive_full_native_traversal()
            records = capsule["records"]
            assert [record["path"] for record in records] == [
                path for path, _ in observation._NORMAL_TRAVERSAL
            ]
            controlled = _prefix_records(len(records))
            for record, fixed in zip(records, controlled, strict=True):
                record["start"] = fixed["start"]
                record["end"] = fixed["end"]
            occurrences = [
                index
                for index, record in enumerate(records)
                if record["literal"] == "snapshot.first.forge"
            ]
            assert len(occurrences) == 5
            winner = records[occurrences[-1]]
            selected["path"] = list(winner["path"])
            capsule["deadline_lower"] = winner["start"] + 1.0
            capsule["deadline_upper"] = winner["start"] + 1.05
        finally:
            observation.runtime_context_exit(binding)

    await asyncio.create_task(downstream())
    observation.wrapper_timeout_catch(scope)
    observation.seal(scope)
    projection = observation.collect_projection()
    assert projection["measurement"] == "nominal_deadline_overlap_observed"
    assert projection["operation_path"] == selected["path"]
    assert projection["operation_occurrence"] == 5
    assert observation.parse_projection_bytes(
        observation.encode_projection(projection),
    ) == projection


def test_started_completed_and_runtime_open_invariants_fail_closed():
    path, parent = observation._NORMAL_TRAVERSAL[0]
    capsule = _projection_capsule([_record(path[-1], 0.0, 1.0, path, parent)])
    capsule["started"] = 2
    assert observation._projection(capsule) == _invalid()
    capsule["started"] = 1
    capsule["runtime_context_open"] = True
    assert observation._projection(capsule) == _invalid()


def test_nonfinite_reversed_and_crossing_record_clocks_fail_closed():
    path0, parent0 = observation._NORMAL_TRAVERSAL[0]
    path1, parent1 = observation._NORMAL_TRAVERSAL[1]
    for start, end in ((float("nan"), 1.0), (0.0, float("inf")), (2.0, 1.0)):
        capsule = _projection_capsule([_record(path0[-1], start, end, path0, parent0)])
        assert observation._projection(capsule) == _invalid()
    records = [
        _record(path0[-1], 0.0, 3.0, path0, parent0),
        _record(path1[-1], 2.0, 4.0, path1, parent1),
    ]
    assert observation._projection(_projection_capsule(records)) == _invalid()


def test_projection_rejects_impossible_terminal_occurrence_and_event_count():
    path, _ = observation._NORMAL_TRAVERSAL[10]
    value = _valid_projection(outer="wait_for_timeout_catch", event_count=11)
    value.update({
        "measurement": "nominal_deadline_overlap_observed",
        "deadline_relation": "instrumented_nominal_overlap_ge1s",
        "operation_path": list(path),
        "operation_occurrence": 2,
    })
    assert observation.parse_projection_bytes(_canonical(value)) == _invalid()


def test_missing_child_artifact_reads_as_fixed_invalid(tmp_path):
    assert observation.read_child_projection(tmp_path) == _invalid()


def test_oversized_child_artifact_reads_as_fixed_invalid(tmp_path):
    target = tmp_path / observation.CHILD_ARTIFACT_BASENAME
    target.write_bytes(b"x" * 769)
    assert observation.read_child_projection(tmp_path) == _invalid()


def test_canonical_child_artifact_round_trips(tmp_path):
    target = tmp_path / observation.CHILD_ARTIFACT_BASENAME
    target.write_bytes(observation.encode_projection(_invalid()))
    assert observation.read_child_projection(tmp_path) == _invalid()


def test_reparse_child_artifact_is_rejected_without_read(monkeypatch, tmp_path):
    target = tmp_path / observation.CHILD_ARTIFACT_BASENAME
    target.write_bytes(observation.encode_projection(_invalid()))
    monkeypatch.setattr(observation, "_reparse", lambda path: path == target)
    assert observation.read_child_projection(tmp_path) == _invalid()


def test_child_artifact_collision_is_not_overwritten(monkeypatch, tmp_path):
    monkeypatch.setenv(observation.DIAGNOSTIC_FLAG, "ignored")
    script = tmp_path / "child.py"
    script.write_text("pass\n", encoding="utf-8")
    target = tmp_path / observation.CHILD_ARTIFACT_BASENAME
    target.write_bytes(b"foreign")
    assert observation.write_child_projection_exclusive(script) is False
    assert target.read_bytes() == b"foreign"


def test_inactive_child_writer_creates_no_artifact(monkeypatch, tmp_path):
    monkeypatch.delenv(observation.DIAGNOSTIC_FLAG, raising=False)
    script = tmp_path / "child.py"
    script.write_text("pass\n", encoding="utf-8")
    assert observation.write_child_projection_exclusive(script) is False
    assert not (tmp_path / observation.CHILD_ARTIFACT_BASENAME).exists()


def test_active_child_writer_exclusively_creates_one_artifact(monkeypatch, tmp_path):
    monkeypatch.setenv(observation.DIAGNOSTIC_FLAG, "ignored")
    monkeypatch.setattr(observation, "collect_projection", _invalid)
    script = tmp_path / "child.py"
    script.write_text("pass\n", encoding="utf-8")
    assert observation.write_child_projection_exclusive(script) is True
    target = tmp_path / observation.CHILD_ARTIFACT_BASENAME
    assert target.read_bytes() == observation.encode_projection(_invalid())
    assert observation.write_child_projection_exclusive(script) is False


def test_controller_rejects_any_nonfixed_junit_slot(tmp_path):
    assert observation.controller_projection_path(
        Path(__file__).resolve().parents[1], tmp_path / "other.xml",
    ) is None


def test_controller_writer_rejects_same_basename_outside_fixed_slot(tmp_path):
    target = tmp_path / observation.CONTROLLER_EXPORT_BASENAME
    assert observation.write_controller_projection_exclusive(target, _invalid()) is False
    assert not target.exists()


def test_controller_projection_round_trip_uses_only_fixed_noarg_path(monkeypatch, tmp_path):
    root = tmp_path
    parent = root / observation.CONTROLLER_JUNIT_RELATIVE.parent
    parent.mkdir(parents=True)
    target = parent / observation.CONTROLLER_EXPORT_BASENAME
    value = _valid_projection(outer="exception", event_count=0)
    target.write_bytes(observation.encode_projection(value))
    monkeypatch.setattr(observation, "_controller_source_root", lambda: root)
    assert observation.read_controller_projection() == value


def test_controller_projection_missing_oversize_and_reparse_are_fixed_invalid(
    monkeypatch, tmp_path,
):
    root = tmp_path
    parent = root / observation.CONTROLLER_JUNIT_RELATIVE.parent
    parent.mkdir(parents=True)
    target = parent / observation.CONTROLLER_EXPORT_BASENAME
    monkeypatch.setattr(observation, "_controller_source_root", lambda: root)
    assert observation.read_controller_projection() == _invalid()
    target.write_bytes(b"x" * (observation.MAX_PROJECTION_BYTES + 1))
    assert observation.read_controller_projection() == _invalid()
    monkeypatch.setattr(observation, "_reparse", lambda path: path == target)
    assert observation.read_controller_projection() == _invalid()


def test_controller_path_escape_and_collision_are_rejected_without_overwrite(
    monkeypatch, tmp_path,
):
    root = tmp_path
    parent = root / observation.CONTROLLER_JUNIT_RELATIVE.parent
    parent.mkdir(parents=True)
    target = parent / observation.CONTROLLER_EXPORT_BASENAME
    target.write_bytes(b"foreign")
    monkeypatch.setattr(observation, "_controller_source_root", lambda: root)
    assert observation.controller_projection_path(root, tmp_path / "elsewhere.xml") is None
    assert observation.controller_projection_path(
        root, root / observation.CONTROLLER_JUNIT_RELATIVE,
    ) is None
    assert observation.write_controller_projection_exclusive(target, _invalid()) is False
    assert target.read_bytes() == b"foreign"


def test_controller_io_failure_returns_false_without_alternate_path(monkeypatch, tmp_path):
    root = tmp_path
    parent = root / observation.CONTROLLER_JUNIT_RELATIVE.parent
    parent.mkdir(parents=True)
    target = parent / observation.CONTROLLER_EXPORT_BASENAME
    monkeypatch.setattr(observation, "_controller_source_root", lambda: root)
    original_open = Path.open

    def failing_open(path, *args, **kwargs):
        if path == target:
            raise OSError("io")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)
    assert observation.write_controller_projection_exclusive(target, _invalid()) is False
    assert not target.exists()


def test_active_child_invalid_path_collects_and_clears_private_references(
    monkeypatch,
):
    monkeypatch.setenv(observation.DIAGNOSTIC_FLAG, "ignored")
    capsule = {
        "phase": "claimed", "collect_count": 0,
        "wrapper_task": object(), "downstream_task": object(),
        "pid": 1, "thread": object(), "thread_ident": 1, "loop": object(),
        "wrapper_entry": 1.0, "timeout": 45.0,
        "deadline_lower": 46.0, "deadline_upper": 46.0,
        "records": [], "projection": None,
    }
    monkeypatch.setattr(observation, "_capsule", capsule)
    monkeypatch.setattr(observation, "_pending_claimed", True)
    assert observation.write_child_projection_exclusive(Path("relative-child.py")) is False
    assert observation._capsule is None and observation._pending_claimed is False
    assert capsule["wrapper_task"] is None and capsule["downstream_task"] is None
    assert capsule["_response_status_class"] is None


def test_active_child_reparse_and_io_failures_create_no_alternate_artifact(
    monkeypatch, tmp_path,
):
    monkeypatch.setenv(observation.DIAGNOSTIC_FLAG, "ignored")
    script = tmp_path / "child.py"
    script.write_text("pass\n", encoding="utf-8")
    target = tmp_path / observation.CHILD_ARTIFACT_BASENAME
    calls: list[str] = []
    monkeypatch.setattr(
        observation, "collect_projection",
        lambda: calls.append("collect") or _invalid(),
    )
    monkeypatch.setattr(observation, "_reparse", lambda path: path == script)
    assert observation.write_child_projection_exclusive(script) is False
    assert calls == ["collect"] and not target.exists()
    monkeypatch.setattr(observation, "_reparse", lambda path: False)
    original_open = Path.open

    def failing_open(path, *args, **kwargs):
        if path == target:
            raise OSError("io")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)
    assert observation.write_child_projection_exclusive(script) is False
    assert calls == ["collect", "collect"] and not target.exists()


def test_inactive_child_path_does_not_collect_or_create(monkeypatch, tmp_path):
    monkeypatch.delenv(observation.DIAGNOSTIC_FLAG, raising=False)
    monkeypatch.setattr(
        observation, "collect_projection",
        lambda: (_ for _ in ()).throw(AssertionError("inactive")),
    )
    script = tmp_path / "child.py"
    script.write_text("pass\n", encoding="utf-8")
    assert observation.write_child_projection_exclusive(script) is False
    assert not (tmp_path / observation.CHILD_ARTIFACT_BASENAME).exists()


def test_e2e_companion_export_is_best_effort_and_never_asserted():
    source = Path(__file__).with_name("test_app_mcp_authenticated_e2e.py").read_text(
        encoding="utf-8",
    )
    assert "def export_h7_companion_best_effort():" in source
    assert "assert write_controller_projection_exclusive(" not in source
    assert source.count("export_h7_companion_best_effort()") == 3


def test_declared_span_inventory_is_exact_and_content_free():
    assert len(observation.SPAN_LITERALS) == 23
    assert all(type(item) is str and 1 <= len(item) <= 48 for item in observation.SPAN_LITERALS)
    assert "manifest.cursor_verify" in observation.SPAN_LITERALS
    assert "manifest.cursor_issue" in observation.SPAN_LITERALS


def test_frozen_normal_traversal_shape_is_exact_and_cursor_free():
    paths = [path for path, _ in observation._NORMAL_TRAVERSAL]
    assert len(paths) == 59
    assert max(map(len, paths)) == 7
    assert max(paths.count(path) for path in paths) == 1
    literal_counts = {
        literal: sum(path[-1] == literal for path in paths)
        for literal in observation.SPAN_LITERALS
    }
    assert max(literal_counts.values()) == 5
    assert literal_counts["manifest.cursor_verify"] == 0
    assert literal_counts["manifest.cursor_issue"] == 0


@pytest.mark.asyncio
async def test_native_source_composed_graph_matches_observer_and_driver(monkeypatch):
    source_root = Path(__file__).resolve().parents[1]

    def parsed(relative: str) -> ast.Module:
        return ast.parse((source_root / relative).read_text(encoding="utf-8"))

    def function(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
        matches = [
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ]
        assert len(matches) == 1
        return matches[0]

    def call_name(call: ast.Call) -> str | None:
        if isinstance(call.func, ast.Name):
            return call.func.id
        if isinstance(call.func, ast.Attribute):
            return call.func.attr
        return None

    def literal_calls(node: ast.AST, callee: str) -> list[tuple[int, str]]:
        result = []
        for child in ast.walk(node):
            if not isinstance(child, ast.Call) or call_name(child) != callee:
                continue
            assert child.args and isinstance(child.args[0], ast.Constant)
            assert type(child.args[0].value) is str
            result.append((child.lineno, child.args[0].value))
        return sorted(result)

    def called_names(node: ast.AST) -> set[str]:
        return {
            name for child in ast.walk(node)
            if isinstance(child, ast.Call) and (name := call_name(child)) is not None
        }

    def assigned_attribute(tree: ast.AST, name: str) -> tuple[str, str]:
        matches = [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)
        ]
        assert len(matches) == 1 and isinstance(matches[0], ast.Attribute)
        assert isinstance(matches[0].value, ast.Name)
        return matches[0].value.id, matches[0].attr

    def span_pairs(node: ast.AST) -> list[tuple[str, set[str]]]:
        tries = [child for child in ast.walk(node) if isinstance(child, ast.Try)]
        result = []
        for line, literal in literal_calls(node, "_observation_span_begin"):
            following = min(
                (candidate for candidate in tries if candidate.lineno > line),
                key=lambda candidate: candidate.lineno,
            )
            result.append((
                literal,
                called_names(ast.Module(body=following.body, type_ignores=[])),
            ))
        return result

    transport = parsed("src/mcp_context_request_transport.py")
    coordinator = parsed("src/mcp_context_pack_coordinator.py")
    producer = parsed("src/context_manifest_producer.py")
    confirmation = parsed("src/mcp_private_context_confirmation.py")

    assert [literal for _, literal in literal_calls(
        transport, "observation_span_begin",
    )] == ["transport.runtime_handle"]
    root_literal = "transport.runtime_handle"

    publish_pairs = span_pairs(function(coordinator, "publish_manifest"))
    assert [literal for literal, _ in publish_pairs] == [
        "manifest.begin", "manifest.capture", "manifest.cursor_verify",
        "manifest.cursor_issue", "manifest.fence_1", "manifest.encode",
        "manifest.fence_2", "manifest.finish",
    ]
    normal_publish_pairs = [
        pair for pair in publish_pairs
        if pair[0] not in {"manifest.cursor_verify", "manifest.cursor_issue"}
    ]
    assert assigned_attribute(coordinator, "_TRUSTED_CAPTURE") == (
        "CombinedContextManifestProducer", "capture_mcp_snapshot",
    )
    assert assigned_attribute(coordinator, "_TRUSTED_FENCE") == (
        "McpPrivateContextConfirmationService", "_fence_disclosed_publication",
    )
    assert assigned_attribute(coordinator, "_TRUSTED_COORDINATOR_CAPTURE") == (
        "McpContextPackCoordinator", "_capture",
    )
    assert assigned_attribute(coordinator, "_TRUSTED_COORDINATOR_FENCE") == (
        "McpContextPackCoordinator", "_fence",
    )
    assert assigned_attribute(confirmation, "_TRUSTED_FENCE_CAPTURE_COOKIE") == (
        "McpPrivateContextConfirmationService", "_capture_cookie",
    )
    assert assigned_attribute(confirmation, "_TRUSTED_FENCE_CAPTURE_MCP") == (
        "McpPrivateContextConfirmationService", "_capture_mcp",
    )
    assert "_TRUSTED_CAPTURE" in called_names(function(coordinator, "_capture"))
    assert "_TRUSTED_FENCE" in called_names(function(coordinator, "_fence"))

    cookie_capture = function(confirmation, "_capture_cookie")
    mcp_capture = function(confirmation, "_capture_mcp")
    assert "capture_snapshot" in called_names(cookie_capture)
    assert "capture_mcp_snapshot" in called_names(mcp_capture)
    assert literal_calls(cookie_capture, "_observation_span_begin") == []
    assert literal_calls(function(producer, "capture_snapshot"), "_observation_span_begin") == []

    mcp_snapshot = function(producer, "capture_mcp_snapshot")
    snapshot_scopes = [
        literal for _, literal in literal_calls(mcp_snapshot, "_observation_span_begin")
    ]
    phases = [
        literal for _, literal in literal_calls(
            mcp_snapshot, "_observation_capture_phase_enter",
        )
    ]
    native_sources = [
        literal for _, literal in literal_calls(
            function(producer, "_capture_native_mcp"),
            "_observation_snapshot_source_begin",
        )
    ]
    assert snapshot_scopes == [
        "snapshot.admission_scope", "snapshot.knowledge_reservation_scope",
    ]
    assert phases == ["first", "final"]
    assert native_sources == ["forge", "planning", "knowledge"]

    fence_pairs = span_pairs(function(confirmation, "_fence_disclosed_publication"))
    assert [literal for literal, _ in fence_pairs] == [
        "fence.guards_scope", "fence.cookie_first", "fence.cookie_last",
        "fence.mcp_first", "fence.mcp_last", "fence.readback",
    ]
    for literal, targets in fence_pairs[1:3]:
        assert literal.startswith("fence.cookie_")
        assert "_TRUSTED_FENCE_CAPTURE_COOKIE" in targets
        assert "_TRUSTED_FENCE_CAPTURE_MCP" not in targets
    for literal, targets in fence_pairs[3:5]:
        assert literal.startswith("fence.mcp_")
        assert "_TRUSTED_FENCE_CAPTURE_MCP" in targets
        assert "_TRUSTED_FENCE_CAPTURE_COOKIE" not in targets

    def add_snapshot(rows: list[tuple[str, ...]], parent: tuple[str, ...]) -> None:
        admission = parent + (snapshot_scopes[0],)
        reservation = admission + (snapshot_scopes[1],)
        rows.extend((admission, reservation))
        rows.extend(
            reservation + (f"snapshot.{phase}.{source}",)
            for phase in phases
            for source in native_sources
        )

    def add_fence(rows: list[tuple[str, ...]], parent: tuple[str, ...]) -> None:
        guards = parent + (fence_pairs[0][0],)
        rows.append(guards)
        for literal, targets in fence_pairs[1:]:
            leaf = guards + (literal,)
            rows.append(leaf)
            if "_TRUSTED_FENCE_CAPTURE_MCP" in targets:
                add_snapshot(rows, leaf)

    source_paths = [(root_literal,)]
    for literal, targets in normal_publish_pairs:
        path = (root_literal, literal)
        source_paths.append(path)
        if "_TRUSTED_COORDINATOR_CAPTURE" in targets:
            add_snapshot(source_paths, path)
        elif "_TRUSTED_COORDINATOR_FENCE" in targets:
            add_fence(source_paths, path)

    assert len(source_paths) == 59
    assert max(map(len, source_paths)) == 7
    assert max(
        sum(path[-1] == literal for path in source_paths)
        for literal in observation.SPAN_LITERALS
    ) == 5
    indices: dict[tuple[str, ...], int] = {}
    source_traversal = []
    for path in source_paths:
        source_traversal.append((path, indices.get(path[:-1]) if len(path) > 1 else None))
        indices[path] = len(source_traversal) - 1
    assert tuple(source_traversal) == observation._NORMAL_TRAVERSAL

    _enable(monkeypatch)
    scope: dict[object, object] = {}
    capsule = observation.wrapper_enter(scope, "/api/plugins/mcp", 45.0)
    observation.wrapper_before_wait(scope, 45.0)
    observation.timeout_context_enter(scope, 45.0)

    async def downstream() -> list[tuple[str, ...]]:
        binding = observation.runtime_context_enter(
            scope, "harbour_context_manifest", "manifest-1",
        )
        assert binding is not None
        try:
            _drive_full_native_traversal()
            return [record["path"] for record in capsule["records"]]
        finally:
            observation.runtime_context_exit(binding)

    assert await asyncio.create_task(downstream()) == source_paths
    observation.response_start(scope, 200)
    observation.seal(scope)
    assert observation.collect_projection() == _valid_projection(
        outer="response_start_returned", event_count=59,
    )
    assert capsule["records"] == []
    assert capsule["wrapper_task"] is None and capsule["downstream_task"] is None
    assert capsule["_response_status_class"] is None


def test_inactive_app_branch_retains_direct_wait_for_source_shape():
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    assert "if observation is None:" in source
    assert "return await _asyncio.wait_for(call_next(request), timeout=REQUEST_HARD_TIMEOUT)" in source
    assert source.count("status_code=504") >= 2


def test_active_app_source_places_bounds_and_bare_rethrow_at_exact_sites():
    source = (Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    observed = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "observed_call_next"
    )
    first = observed.body[0]
    assert isinstance(first, ast.Expr) and isinstance(first.value, ast.Call)
    assert isinstance(first.value.func, ast.Name)
    assert first.value.func.id == "_h7_observation_timeout_context_enter"
    inner_try = observed.body[1]
    timeout_handler = next(
        handler for handler in inner_try.handlers
        if isinstance(handler.type, ast.Attribute) and handler.type.attr == "TimeoutError"
    )
    assert isinstance(timeout_handler.body[-1], ast.Raise)
    assert timeout_handler.body[-1].exc is None
    dispatch = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "dispatch"
    )
    active_try = next(
        node for node in dispatch.body if isinstance(node, ast.Try)
        and any(
            isinstance(item, ast.Return) and isinstance(item.value, ast.Await)
            for item in node.body
        )
    )
    assert isinstance(active_try.body[0], ast.Expr)
    assert isinstance(active_try.body[0].value, ast.Call)
    assert active_try.body[0].value.func.id == "_h7_observation_before_wait"
    assert isinstance(active_try.body[1], ast.Return)
    propagated = [
        handler for handler in active_try.handlers
        if isinstance(handler.type, (ast.Attribute, ast.Name))
        and getattr(handler.type, "attr", getattr(handler.type, "id", ""))
        in {"CancelledError", "BaseException"}
    ]
    assert len(propagated) == 2
    assert all(
        isinstance(handler.body[-1], ast.Raise) and handler.body[-1].exc is None
        for handler in propagated
    )


def test_projection_never_contains_ids_timestamps_exceptions_or_content():
    forbidden = {
        "id", "timestamp", "duration", "exception", "credential", "content",
        "environment", "provider", "pid", "thread_id",
    }
    assert forbidden.isdisjoint(_invalid())


def test_content_rejection_direct_runtime_event_maps_closed_locus_and_clears_exception(tmp_path):
    target = tmp_path / observation.CONTENT_REJECTION_BASENAME

    def reject():
        raise RuntimeError("secret sentinel must never be projected")

    reject.__code__ = reject.__code__.replace(co_firstlineno=3140)

    assert observation.arm_content_rejection_observation(str(target))
    assert observation.begin_content_rejection_observation(1, "forge")
    captured = None
    try:
        reject()
    except RuntimeError as exc:
        captured = exc
        line = exc.__traceback__.tb_next.tb_lineno
        assert line == 3141
        code_map = ((reject.__code__, line, line + 1, "runtime", "runtime.content_preflight"),)
        assert observation.record_content_rejection(
            exc, "harbour_context_page", code_map,
        )
    assert captured is not None
    assert observation.end_content_rejection_observation(403)
    value = observation.read_content_rejection_projection(target)
    assert value["schema"] == observation.CONTENT_REJECTION_SCHEMA
    assert value["inner_map_status"] == "exact"
    assert value["inner_source_class"] == "runtime"
    assert value["inner_branch"] == "runtime.content_preflight"
    assert b"secret" not in target.read_bytes()

    class PinnedBoundaryError(RuntimeError):
        pass

    def wrapped_reject(error_type):
        try:
            reject()
        except RuntimeError:
            raise error_type() from None

    wrapped = None
    try:
        wrapped_reject(PinnedBoundaryError)
    except PinnedBoundaryError as exc:
        wrapped = exc
    assert wrapped is not None
    wrapper_traceback = wrapped.__traceback__
    while wrapper_traceback.tb_frame.f_code is not wrapped_reject.__code__:
        wrapper_traceback = wrapper_traceback.tb_next
    wrapper_line = wrapper_traceback.tb_lineno
    pinned_map = code_map + ((
        "transparent_wrapper", PinnedBoundaryError, wrapped_reject.__code__,
        wrapper_line, wrapper_line + 1,
    ),)
    assert observation._native_exception_projection(wrapped, pinned_map)[:4] == (
        "exact", "runtime", "runtime.content_preflight",
        "runtime.content_preflight.L" + str(line),
    )

    unrelated = None
    try:
        wrapped_reject(ValueError)
    except ValueError as exc:
        unrelated = exc
    assert unrelated is not None
    assert unrelated.__suppress_context__ is True
    assert observation._native_exception_projection(unrelated, pinned_map) == (
        "unmapped", "unknown", "unknown", "unknown", "none",
    )


def test_content_rejection_app_graph_and_all_success_lifecycles_seal_once(tmp_path):
    failure = tmp_path / observation.CONTENT_REJECTION_BASENAME
    assert observation.arm_content_rejection_observation(str(failure))
    assert observation.begin_content_rejection_observation(2, "knowledge")
    assert observation.record_app_graph_absent()
    assert observation.end_content_rejection_observation(403)
    graph = observation.read_content_rejection_projection(failure)
    assert graph["route_class"] == "app_graph_absent"
    assert graph["release_scope"] == "observer_disarmed"
    assert not observation.end_content_rejection_observation(403)

    failure.unlink()
    assert observation.arm_content_rejection_observation(str(failure))
    for ordinal, source in enumerate(("forge", "knowledge", "planning"), 1):
        assert observation.begin_content_rejection_observation(ordinal, source)
        assert observation.end_content_rejection_observation(200)
    assert observation.finalize_content_rejection_success()
    success = observation.read_content_rejection_projection(failure)
    assert success["outcome"] == "all_success"
    assert success["release_scope"] == "observer_disarmed"
    assert not observation.finalize_content_rejection_success()


def test_content_rejection_projection_rejects_hostile_shapes_replay_and_collision_without_secret_surfaces(tmp_path):
    native_rules = tuple(
        row for row in observation._CONTENT_LOCUS_RULES
        if row[1].startswith("authority.lease_recheck.")
        or row[1].startswith("authority.binding_recheck.")
    )
    expected_native_rules = (
        ("authority", "authority.lease_recheck.snapshot", ((1600, 1602),)),
        ("authority", "authority.lease_recheck.state_lookup", ((1602, 1607),)),
        ("authority", "authority.lease_recheck.cancelled_marker", ((1607, 1608),)),
        ("authority", "authority.lease_recheck.missing", ((1608, 1609),)),
        ("authority", "authority.lease_recheck.cancelled_row", ((1609, 1611),)),
        ("authority", "authority.lease_recheck.clock_read", ((1611, 1617),)),
        ("authority", "authority.lease_recheck.clock_validity", ((1617, 1619),)),
        ("authority", "authority.lease_recheck.expiry", ((1619, 1622),)),
        ("authority", "authority.lease_recheck.binding", ((1622, 1624),)),
        ("authority", "authority.lease_recheck.propagation", ((1624, 1630),)),
        ("authority", "authority.binding_recheck.snapshot", ((1490, 1492),)),
        ("authority", "authority.binding_recheck.identity", ((1492, 1500),)),
        ("authority", "authority.binding_recheck.proof_ownership", ((1500, 1508),)),
        ("authority", "authority.binding_recheck.live_precheck", ((1508, 1510),)),
        ("authority", "authority.binding_recheck.proof_shape", ((1510, 1511),)),
        ("authority", "authority.binding_recheck.authority_state", ((1511, 1512),)),
        ("authority", "authority.binding_recheck.live_postcheck", ((1512, 1513),)),
        ("authority", "authority.binding_recheck.final_ownership", ((1513, 1526),)),
    )
    assert native_rules == expected_native_rules
    lease_lines = {
        line for _source, branch, ranges in native_rules
        if branch.startswith("authority.lease_recheck.")
        for first, stop in ranges for line in range(first, stop)
    }
    binding_lines = {
        line for _source, branch, ranges in native_rules
        if branch.startswith("authority.binding_recheck.")
        for first, stop in ranges for line in range(first, stop)
    }
    assert lease_lines == set(range(1600, 1630))
    assert binding_lines == set(range(1490, 1526))
    valid_native_projection = {
        "schema": observation.CONTENT_REJECTION_SCHEMA,
        "observer_status": "complete", "outcome": "failure",
        "response_class": "403", "failing_request_ordinal": 1,
        "page_source_class": "planning",
        "route_class": "runtime_exception_conversion",
        "inner_map_status": "exact", "inner_source_class": "authority",
        "inner_branch": "authority.lease_recheck.snapshot",
        "inner_locus_id": "authority.lease_recheck.snapshot.L1600",
        "observer_rejection_reason": "none", "event_thread_consistent": True,
        "observation_scope_closed": True, "release_scope": "observer_disarmed",
        "global_mask_untouched": True,
    }
    for source_class, branch, ((first, stop),) in native_rules:
        for line in (first, stop - 1):
            candidate = dict(
                valid_native_projection,
                inner_source_class=source_class,
                inner_branch=branch,
                inner_locus_id=branch + ".L" + str(line),
            )
            assert observation._content_projection_valid(candidate)
        for line in (first - 1, stop):
            candidate = dict(
                valid_native_projection,
                inner_source_class=source_class,
                inner_branch=branch,
                inner_locus_id=branch + ".L" + str(line),
            )
            assert not observation._content_projection_valid(candidate)

    target = tmp_path / observation.CONTENT_REJECTION_BASENAME
    target.write_bytes(b"occupied")
    assert not observation.arm_content_rejection_observation(str(target))
    assert observation.parse_content_rejection_projection(
        b'{"schema":"secret sentinel"}',
    )["observer_status"] == "invalid"
    mixed_invalid = observation._content_invalid("unknown")
    mixed_invalid["response_class"] = "403"
    with pytest.raises(ValueError, match="invalid content rejection projection"):
        observation.encode_content_rejection_projection(mixed_invalid)

    def raise_at_depth(depth):
        if depth:
            return raise_at_depth(depth - 1)
        raise RuntimeError("bounded traceback")

    def captured(depth):
        try:
            raise_at_depth(depth)
        except RuntimeError as exc:
            return exc

    assert observation._native_exception_projection(captured(29), ()) == (
        "unmapped", "unknown", "unknown", "unknown", "none",
    )
    assert observation._native_exception_projection(captured(30), ()) == (
        "unmapped", "unknown", "unknown", "unknown", "none",
    )
    assert observation._native_exception_projection(captured(31), ()) == (
        "invalid", "invalid", "invalid", "invalid", "traceback_truncated",
    )
    target.unlink()
    assert observation.arm_content_rejection_observation(str(target))
    assert observation.begin_content_rejection_observation(1, "planning")
    assert not observation.begin_content_rejection_observation(2, "forge")
    assert observation.end_content_rejection_observation(403)
    encoded = target.read_bytes()
    assert len(encoded) <= observation.CONTENT_REJECTION_MAX_BYTES
    assert b"secret" not in encoded
    assert observation.read_content_rejection_projection(target)["observer_status"] == "invalid"

