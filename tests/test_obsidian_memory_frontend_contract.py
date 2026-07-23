from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_obsidian_memory_dashboard_uses_unified_status_contract():
    source = (ROOT / "plugins" / "obsidian" / "frontend" / "main.js").read_text(encoding="utf-8")
    styles = (ROOT / "plugins" / "obsidian" / "frontend" / "style.css").read_text(encoding="utf-8")

    assert "fetchMemoryDashboardJson('status', pluginApi('/memory/status'))" in source
    assert "const ORCA_API_PREFIX = '/api/plugins/orca'" in source
    assert "function renderUnifiedMemoryStatus()" in source
    assert "summary?.readiness_gap_names" in source
    assert "renderMemoryReadinessGaps()" in source
    assert "summary.readiness_families" in source
    assert "summary.status_families" in source
    assert "obsidian-memory-status-gaps" in source
    assert ".obsidian-memory-status-gaps" in styles
    assert "data-memory-family-ready" in source


def test_memory_dashboard_keeps_five_readbacks_isolated_and_uses_post_contract():
    source = (ROOT / "plugins" / "obsidian" / "frontend" / "main.js").read_text(encoding="utf-8")
    routes = (ROOT / "plugins" / "obsidian" / "backend" / "routes.py").read_text(encoding="utf-8")
    load_body = source.split("async function loadMemoryTreeDashboard()", 1)[1].split(
        "\nfunction showMemoryTreePanel()", 1
    )[0]

    assert "Promise.allSettled" in load_body
    assert "Promise.all([" not in load_body
    assert load_body.count("promise: fetchMemoryDashboardJson(") == 5
    assert "pluginApi('/memory-tree/analyze'), { method: 'POST' }" in load_body
    assert '@router.post("/memory-tree/analyze")' in routes
    assert '@router.get("/memory-tree/analyze")' not in routes
    assert "if (result.status === 'fulfilled')" in load_body
    assert "readback.apply(result.value)" in load_body
    assert "memoryTreeErrors = nextErrors" in load_body


def test_memory_dashboard_exposes_section_errors_and_compact_readback_fields():
    source = (ROOT / "plugins" / "obsidian" / "frontend" / "main.js").read_text(encoding="utf-8")
    error_body = source.split("function renderMemorySectionError", 1)[1].split(
        "\nasync function loadMemoryTreeDashboard", 1
    )[0]

    assert 'data-lens-state="error"' in error_body
    assert 'aria-live="polite"' in error_body
    assert "<button" not in error_body
    for section in ("status", "tree", "audit", "quarantine", "raptor"):
        assert f"renderMemorySectionError('{section}'" in source
    for label in ("Status p95", "Query cache", "Last rebuild", "Sample age"):
        assert f"label: '{label}'" in source
    assert "MEMORY_DASHBOARD_SAMPLE_LIMIT = 60" in source
