from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_obsidian_memory_dashboard_uses_unified_status_contract():
    source = (ROOT / "plugins" / "obsidian" / "frontend" / "main.js").read_text(encoding="utf-8")
    styles = (ROOT / "plugins" / "obsidian" / "frontend" / "style.css").read_text(encoding="utf-8")

    assert "fetchMemoryDashboardJson(pluginApi('/memory/status'))" in source
    assert "const ORCA_API_PREFIX = '/api/plugins/orca'" in source
    assert "function renderUnifiedMemoryStatus()" in source
    assert "summary?.readiness_gap_names" in source
    assert "renderMemoryReadinessGaps()" in source
    assert "summary.readiness_families" in source
    assert "summary.status_families" in source
    assert "obsidian-memory-status-gaps" in source
    assert ".obsidian-memory-status-gaps" in styles
    assert "data-memory-family-ready" in source
