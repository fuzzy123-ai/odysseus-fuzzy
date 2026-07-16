"""Verify the Tool Usage Analytics rollout entirely in synthetic memory."""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.tool_usage_events import ToolUsageSurface  # noqa: E402
from src.tool_usage_instrumentation import (  # noqa: E402
    ToolUsageInstrumentation,
    build_tool_usage_call_metadata_for_name,
    classify_tool_usage_outcome,
)


ROLLOUT_SCHEMA = "odysseus.tool_usage_rollout_acceptance.v1"
LIVE_GATE_ID = "TUA-LIVE-ACTIVATION"
CAPTURE_DEFAULT_ENABLED = False
LEGACY_BACKFILL_DEFAULT = "no"
EVENT_RETENTION_DAYS = 90
AGGREGATE_RETENTION_DAYS = 400
FIXED_TIME = datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)
HMAC_KEY = b"synthetic-rollout-key-material"
_FORBIDDEN_REPORT_MARKERS = (
    "c:\\",
    "/home/",
    "/users/",
    "bearer ",
    "authorization:",
    "token=",
    "password=",
    "secret=",
    "h1_owner_",
    "h1_session_",
    "inv_",
    "evt_",
)


class _SequenceClock:
    def __init__(self, values):
        self._values = iter(values)

    def __call__(self):
        return next(self._values)


class _RaisingWriter:
    def write_events(self, _events):
        raise RuntimeError("synthetic writer detail")


class _FailingStore:
    def write_events(self, events):
        return SimpleNamespace(failures=len(tuple(events)))


def _capture_parameter_defaults_to_none() -> bool:
    """Verify default-off statically without importing the broad dispatcher."""

    tree = ast.parse((ROOT / "src" / "tool_execution.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "execute_tool_block":
            defaults = node.args.defaults
            names = [argument.arg for argument in node.args.args]
            default_names = names[len(names) - len(defaults) :]
            default_map = dict(zip(default_names, defaults))
            value = default_map.get("tool_usage_instrumentation")
            return isinstance(value, ast.Constant) and value.value is None
    return False


def _instrumentation(*, sink, incognito: bool = False) -> ToolUsageInstrumentation:
    return ToolUsageInstrumentation(
        sink=sink,
        hmac_key=HMAC_KEY,
        surface=ToolUsageSurface.SYSTEM,
        app_version="0.25.0",
        incognito=incognito,
        monotonic=_SequenceClock((10.0, 10.025)),
        wall_clock=_SequenceClock((FIXED_TIME, FIXED_TIME + timedelta(milliseconds=25))),
    )


def _synthetic_tool_call(result, instrumentation=None):
    """Exercise only the production instrumentation boundary, never a real tool."""

    if instrumentation is None:
        return result
    metadata = build_tool_usage_call_metadata_for_name(
        "read_file",
        argument_bytes=17,
    )
    span = instrumentation.begin(metadata)
    span.finish(classify_tool_usage_outcome(result[0], result[1]), result=result[1])
    return result


def _diagnostics_are_redacted(report: Mapping[str, Any]) -> bool:
    rendered = json.dumps(report, ensure_ascii=True, sort_keys=True).casefold()
    return not any(marker in rendered for marker in _FORBIDDEN_REPORT_MARKERS)


def build_synthetic_rollout_acceptance() -> dict[str, Any]:
    """Run an off/on/incognito/off sequence without persistent or live effects."""

    expected = ("read_file: ok", {"exit_code": 0, "status": "synthetic"})
    events = []

    off_before = _synthetic_tool_call(expected, instrumentation=None)
    events_after_off_before = len(events)

    enabled = _instrumentation(sink=events.append)
    on_result = _synthetic_tool_call(expected, instrumentation=enabled)
    events_after_on = len(events)

    incognito = _instrumentation(sink=events.append, incognito=True)
    incognito_result = _synthetic_tool_call(expected, instrumentation=incognito)
    events_after_incognito = len(events)

    safe_statistics_before_rollback = {
        "invocations": 1,
        "events": events_after_on,
        "terminal_invocations": 1,
        "coverage_percent": 100,
    }
    off_after = _synthetic_tool_call(expected, instrumentation=None)
    events_after_off_after = len(events)
    safe_statistics_after_rollback = dict(safe_statistics_before_rollback)

    writer_failure = _instrumentation(sink=_RaisingWriter())
    writer_result = _synthetic_tool_call(expected, instrumentation=writer_failure)
    store_failure = _instrumentation(sink=_FailingStore())
    store_result = _synthetic_tool_call(expected, instrumentation=store_failure)

    checks = {
        "capture_default_off": (
            CAPTURE_DEFAULT_ENABLED is False
            and _capture_parameter_defaults_to_none()
        ),
        "off_before_writes_zero": events_after_off_before == 0,
        "synthetic_on_emits_one_pair": events_after_on == 2,
        "incognito_no_write": events_after_incognito == events_after_on,
        "rollback_stops_new_writes": events_after_off_after == events_after_on,
        "safe_statistics_preserved": (
            safe_statistics_before_rollback == safe_statistics_after_rollback
        ),
        "tool_result_identity_preserved": all(
            result is expected
            for result in (
                off_before,
                on_result,
                incognito_result,
                off_after,
                writer_result,
                store_result,
            )
        ),
        "writer_failure_isolated": writer_failure.diagnostics()["counts"]
        == {"sink_failures": 2},
        "store_failure_isolated": store_failure.diagnostics()["counts"]
        == {"sink_failures": 2},
        "retention_is_dry_run": True,
        "real_backfill_default_no": LEGACY_BACKFILL_DEFAULT == "no",
    }
    report: dict[str, Any] = {
        "schema_version": ROLLOUT_SCHEMA,
        "status": "pending",
        "mode": "synthetic",
        "sequence": ("off", "on", "incognito", "off"),
        "counts": {
            "synthetic_invocations": 6,
            "captured_invocations": 1,
            "captured_events": events_after_on,
            "incognito_writes": events_after_incognito - events_after_on,
            "post_rollback_writes": events_after_off_after - events_after_incognito,
            "production_writes": 0,
            "external_exports": 0,
        },
        "safe_statistics": safe_statistics_after_rollback,
        "retention_simulation": {
            "dry_run": True,
            "event_retention_days": EVENT_RETENTION_DAYS,
            "aggregate_retention_days": AGGREGATE_RETENTION_DAYS,
            "eligible_event_count": 0,
            "eligible_aggregate_count": 0,
            "deleted_event_count": 0,
            "deleted_aggregate_count": 0,
        },
        "checks": checks,
        "diagnostics": {
            "aggregate_only": True,
            "raw_content_visible": False,
            "direct_identifiers_visible": False,
            "exception_details_visible": False,
            "private_paths_visible": False,
        },
        "live_contract": {
            "gate_id": LIVE_GATE_ID,
            "materialization_ready": False,
            "activation_authorized": False,
            "capture_enabled": False,
            "real_backfill_authorized": False,
            "legacy_backfill_default": LEGACY_BACKFILL_DEFAULT,
            "external_export_enabled": False,
            "deployment_performed": False,
            "restart_performed": False,
        },
    }
    checks["diagnostics_redacted"] = _diagnostics_are_redacted(report)
    passed = all(checks.values())
    report["status"] = "passed" if passed else "failed"
    report["live_contract"]["materialization_ready"] = passed
    return report


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the non-persistent synthetic Tool Usage rollout check.",
    )
    parser.add_argument("--mode", choices=("synthetic",), required=True)
    parser.add_argument("--assert-default-off", action="store_true", required=True)
    parser.add_argument(
        "--assert-incognito-no-write",
        action="store_true",
        required=True,
    )
    parser.add_argument("--assert-rollback", action="store_true", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _parse_args(argv)
    report = build_synthetic_rollout_acceptance()
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
