from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _catalog_ui_source() -> str:
    admin_js = (ROOT / "static" / "js" / "admin.js").read_text(encoding="utf-8")
    return admin_js.split("const TOOL_FAMILY_PRESENTATION", 1)[1].split(
        "async function loadMcpServers", 1
    )[0]


def test_admin_tools_use_descriptor_v2_without_manual_metadata():
    source = _catalog_ui_source()

    assert "const TOOL_META" not in source
    assert "TOOL_META[" not in source
    assert "manage_rag:" not in source
    assert "data.descriptors || data.tools || []" in source
    assert "tool.source !== 'mcp'" in source
    assert "t.display_name || t.name || t.id" in source
    assert "t.description || t.desc || ''" in source


def test_admin_tools_map_every_canonical_family_without_other_fallback():
    source = _catalog_ui_source()
    canonical_families = {
        "code_filesystem",
        "search_web",
        "knowledge_memory",
        "documents_media",
        "model_ops",
        "projects_repositories",
        "orchestration_sessions",
        "planning_communication",
        "admin_system",
        "plugins_mcp",
        "external_providers",
        "experimental",
        "unclassified_dynamic",
    }

    for family in canonical_families:
        assert f"{family}: {{" in source
    assert "'Other'" not in source
    assert "\"Other\"" not in source
    assert "TOOL_FAMILY_PRESENTATION.experimental" in source
    assert "TOOL_FAMILY_PRESENTATION.unclassified_dynamic" in source


def test_admin_tools_render_explicit_lifecycle_states_and_only_mutable_toggles():
    source = _catalog_ui_source()

    assert "deferred: { label: 'Deferred'" in source
    assert "experimental: { label: 'Experimental'" in source
    assert "unavailable: { label: 'Unavailable'" in source
    assert "item.settings_mutable !== false" in source
    assert "t.settings_mutable === false ? ''" in source
    assert 'data-tool-state="${esc(t.stateKey)}"' in source
    assert "data-tool-status" in source


def test_admin_tool_catalog_escapes_content_and_supports_keyboard_disclosure():
    source = _catalog_ui_source()

    for value in ("group.label", "t.id", "t.name", "t.desc", "state.label"):
        assert f"esc({value})" in source
    assert 'role="button"' in source
    assert 'tabindex="0"' in source
    assert 'aria-expanded="false"' in source
    assert "header.addEventListener('keydown'" in source
    assert "event.key !== 'Enter' && event.key !== ' '" in source
    assert "error.textContent = message" in source


def test_admin_tool_state_write_validates_response_and_reverts_failed_changes():
    source = _catalog_ui_source()

    assert "const response = await fetch('/api/tools'" in source
    assert "body: JSON.stringify({ disabled })" in source
    assert "if (!response.ok) throw new Error" in source
    assert "chk.checked = previous" in source
    assert "toolChecks.forEach((c, index) => { c.checked = previous[index]; })" in source
    assert "Changes were reverted." in source
