from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ADMIN_JS = ROOT / "static" / "js" / "admin.js"
HAS_NODE = shutil.which("node") is not None


def _source() -> str:
    return ADMIN_JS.read_text(encoding="utf-8")


def _usage_helpers() -> str:
    source = _source()
    return source.split("const TOOL_USAGE_WINDOWS", 1)[1].split(
        "async function loadBuiltinTools", 1
    )[0]


def _run_node(body: str) -> dict:
    helpers = "const TOOL_USAGE_WINDOWS" + _usage_helpers()
    script = f"""
function esc(value) {{
  return String(value).replace(/[&<>\"']/g, character => ({{
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '\"': '&quot;', "'": '&#39;'
  }})[character]);
}}
{helpers}
async function run() {{
{body}
}}
run().then(value => console.log(JSON.stringify(value)))
  .catch(error => {{ console.error(error); process.exit(2); }});
"""
    result = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        capture_output=True,
        text=True,
        cwd=ROOT,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_existing_tool_surface_uses_bounded_aggregate_api_without_dom_identifiers():
    source = _source()
    usage = source.split("const TOOL_USAGE_WINDOWS", 1)[1].split(
        "async function loadMcpServers", 1
    )[0]

    assert "= Object.freeze([7, 30])" in usage
    assert "TOOL_USAGE_MAX_CONCURRENT_REQUESTS = 4" in usage
    assert "new URLSearchParams" in usage
    assert "/api/diagnostics/tool-usage?" in usage
    assert "credentials: 'same-origin'" in usage
    assert "data-tool-usage-window" in usage
    assert "data-tool-usage-category-summary" in usage
    assert "data-tool-usage-view" in usage
    assert 'data-tool-usage-category-summary role="status" aria-live="polite"' in usage
    assert 'data-tool-usage-view role="group" aria-label="Aggregate usage"' in usage
    assert "target.title = target.textContent" in usage
    assert "Aggregate only Â· no raw-event drilldown" in usage
    assert "data-tool-analytics-id" not in usage
    assert "data-analytics-id" not in usage
    assert "/raw" not in usage


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_usage_models_keep_deferred_zero_and_not_instrumented_distinct():
    result = _run_node(
        """
const active = { analytics_id: 'read-file', lifecycle: 'active', default_policy: 'enabled' };
const observed = _toolUsageModel(active, {
  summary: {
    calls: 8,
    pseudonymous_distinct_session_count: 3,
    status_counts: { succeeded: 6, failed: 2 },
    status_rates: { succeeded: 0.75 },
    duration_p50_ms: 125,
    duration_p95_ms: 1250,
    coverage_rate: 1,
  },
  quality: { query_complete: true },
});
return {
  deferred: _toolUsageCatalogState({ analytics_id: 'send-email', lifecycle: 'deferred' }),
  defaultOff: _toolUsageCatalogState({ analytics_id: 'manage-calendar', analytics_state: 'default_off' }),
  notInstrumented: _toolUsageCatalogState({ lifecycle: 'active' }),
  explicitNotInstrumented: _toolUsageCatalogState({ analytics_id: 'legacy-tool', instrumentation_status: 'not_instrumented' }),
  zero: _toolUsageModel(active, { summary: { calls: 0 }, quality: { query_complete: true } }).state,
  observed,
  range: _toolUsageDateRange(7, new Date('2026-07-16T19:30:00Z')),
  html: _toolUsageStateHtml(observed, 7),
};
"""
    )

    assert result["deferred"] == "deferred_default_off"
    assert result["defaultOff"] == "deferred_default_off"
    assert result["notInstrumented"] == "not_instrumented"
    assert result["explicitNotInstrumented"] == "not_instrumented"
    assert result["zero"] == "zero_usage"
    assert result["observed"] == {
        "state": "observed",
        "calls": 8,
        "sessions": 3,
        "succeeded": 6,
        "successRate": 0.75,
        "p50": 125,
        "p95": 1250,
        "coverage": 1,
        "queryComplete": True,
    }
    assert result["range"] == {"start": "2026-07-10", "end": "2026-07-16"}
    assert "8</strong> calls" in result["html"]
    assert "3 sessions" in result["html"]
    assert "75% success" in result["html"]
    assert "p50 125 ms" in result["html"]
    assert "p95 1.3 s" in result["html"]
    assert "read-file" not in result["html"]
    assert "pseudonymous" not in result["html"]


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_analytics_fetch_is_admin_scoped_fail_closed_and_skips_gated_tools():
    result = _run_node(
        """
const requests = [];
globalThis.fetch = async (url, options) => {
  requests.push({ url, options });
  return {
    ok: true,
    async json() {
      return {
        schema_version: 'odysseus.tool_usage_analytics.v1',
        summary: {
          calls: 1,
          pseudonymous_distinct_session_count: 1,
          status_counts: { succeeded: 1 },
          status_rates: { succeeded: 1 },
          duration_p50_ms: 10,
          duration_p95_ms: 10,
          coverage_rate: 1,
          private_reference_for_test: 'must-not-render',
        },
        quality: { query_complete: true },
      };
    },
  };
};
const deferred = await _fetchToolUsageAggregate({ analytics_id: 'send-email', lifecycle: 'deferred' }, 7);
const active = await _fetchToolUsageAggregate({ analytics_id: 'read-file', lifecycle: 'active' }, 30);
return { requests, deferred, active, html: _toolUsageStateHtml(active, 30) };
"""
    )

    assert result["deferred"] == {"state": "deferred_default_off"}
    assert result["active"]["state"] == "observed"
    assert len(result["requests"]) == 1
    request = result["requests"][0]
    assert request["options"] == {"credentials": "same-origin"}
    assert request["url"].startswith("/api/diagnostics/tool-usage?")
    assert "start=2026-" in request["url"]
    assert "end=2026-" in request["url"]
    assert "tool=read-file" in request["url"]
    assert "limit=200" in request["url"]
    assert "must-not-render" not in result["html"]
    assert "send-email" not in result["html"]


def test_usage_copy_and_family_summary_preserve_safe_empty_states_and_existing_toggles():
    source = _source()

    for state in (
        "deferred_default_off",
        "zero_usage",
        "not_instrumented",
        "analytics_unavailable",
    ):
        assert state in source
    for copy in (
        "Deferred / default off",
        "Zero usage",
        "Not instrumented",
        "Analytics unavailable",
        "coverage",
        "p50",
        "p95",
        "reporting",
    ):
        assert copy in source
    assert "statusCounts.succeeded" in source
    assert "pseudonymous_distinct_session_count" in source
    assert "_renderToolUsageCategorySummary" in source
    assert "input[data-tool-id]" in source
    assert "body: JSON.stringify({ disabled })" in source
    assert "Changes were reverted." in source
