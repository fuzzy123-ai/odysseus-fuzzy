from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def test_main_app_loads_plugin_ui_loader():
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert '<script type="module" src="/api/plugins/ui-loader.js"></script>' in index


def test_obsidian_frontend_registers_sidebar_and_standalone_mode():
    main_js = (ROOT / "plugins" / "obsidian" / "frontend" / "main.js").read_text(encoding="utf-8")
    style = (ROOT / "plugins" / "obsidian" / "frontend" / "style.css").read_text(encoding="utf-8")

    assert "function isStandaloneMode()" in main_js
    assert "window.ODYSSEUS_OBSIDIAN_STANDALONE" in main_js
    assert "document.body.classList.toggle('obsidian-standalone', standalone)" in main_js
    assert "#tool-obsidian-btn, #rail-obsidian" in main_js
    assert "body.obsidian-standalone #obsidian-panel" in style


def test_obsidian_frontend_smoke_contract_has_rendered_app_parts():
    main_js = (ROOT / "plugins" / "obsidian" / "frontend" / "main.js").read_text(encoding="utf-8")
    style = (ROOT / "plugins" / "obsidian" / "frontend" / "style.css").read_text(encoding="utf-8")

    required_dom = [
        'id="obsidian-panel"',
        'id="obsidian-file-tree"',
        'id="obsidian-editor-toolbar"',
        'id="obsidian-textarea"',
        'id="obsidian-graph-view"',
        'id="obsidian-header-view-toggle"',
        'id="obsidian-settings-menu"',
    ]
    for marker in required_dom:
        assert marker in main_js

    assert "openPanel();" in main_js
    assert "loadVaultFiles();" in main_js
    assert "renderGraphView();" in main_js
    assert "const toolbar = document.getElementById('obsidian-editor-toolbar')" in main_js
    assert "toolbar?.classList.toggle('hidden', currentViewMode === 'graph')" in main_js
    assert ".obsidian-panel-content" in style
    assert ".obsidian-graph-controls" in style
    assert ".obsidian-settings-menu" in style


def test_obsidian_phase3_settings_menu_contract():
    main_js = (ROOT / "plugins" / "obsidian" / "frontend" / "main.js").read_text(encoding="utf-8")

    ordered_controls = [
        'id="obsidian-header-view-toggle"',
        'id="obsidian-settings-toggle"',
        'id="obsidian-panel-minimize"',
    ]
    last_seen = -1
    for marker in ordered_controls:
        pos = main_js.find(marker)
        assert pos > last_seen
        last_seen = pos

    for action in (
        'data-settings-action="import"',
        'data-settings-action="export"',
        'data-settings-action="set-password"',
        'data-settings-action="remove-password"',
        'data-settings-action="reset-graph"',
    ):
        assert action in main_js

    assert 'id="obsidian-import-input"' in main_js
    assert 'accept=".zip,application/zip"' in main_js
    assert "closeSettingsMenu();" in main_js
    assert "if (e.key === 'Escape')" in main_js
    assert "#obsidian-settings-menu, #obsidian-settings-toggle" in main_js


def test_obsidian_phase4_project_planning_ui_contract():
    main_js = (ROOT / "plugins" / "obsidian" / "frontend" / "main.js").read_text(encoding="utf-8")
    style = (ROOT / "plugins" / "obsidian" / "frontend" / "style.css").read_text(encoding="utf-8")

    for marker in (
        'id="obsidian-project-plan"',
        'id="obsidian-project-planner"',
        'id="obsidian-project-folder"',
        'id="obsidian-project-title"',
        'id="obsidian-project-kind"',
        'id="obsidian-project-description"',
        'id="obsidian-project-preview"',
        'id="obsidian-project-apply"',
        'id="obsidian-project-preview-panel"',
    ):
        assert marker in main_js

    assert "function previewProjectPlan()" in main_js
    assert "function applyProjectPlan()" in main_js
    assert "fetch('/api/plugins/obsidian/project-plan/preview'" in main_js
    assert "fetch('/api/plugins/obsidian/project-plan/apply'" in main_js
    assert "Create this project structure in the vault?" in main_js
    assert "projectPlanPreview" in main_js
    assert "data-project-conflicts" in main_js
    assert ".obsidian-project-planner" in style
    assert ".obsidian-project-form" in style
    assert ".obsidian-project-conflicts" in style


def test_obsidian_phase5_memory_review_ui_contract():
    main_js = (ROOT / "plugins" / "obsidian" / "frontend" / "main.js").read_text(encoding="utf-8")
    style = (ROOT / "plugins" / "obsidian" / "frontend" / "style.css").read_text(encoding="utf-8")

    for marker in (
        'id="obsidian-memory-review"',
        'id="obsidian-memory-review-panel"',
        'id="obsidian-memory-title"',
        'id="obsidian-memory-action"',
        'id="obsidian-memory-folder"',
        'id="obsidian-memory-note"',
        'id="obsidian-memory-tags"',
        'id="obsidian-memory-content"',
        'id="obsidian-memory-preview"',
        'id="obsidian-memory-apply"',
        'id="obsidian-memory-preview-panel"',
    ):
        assert marker in main_js

    assert "function previewMemoryReview()" in main_js
    assert "function applyMemoryReview()" in main_js
    assert "fetch('/api/plugins/obsidian/memory-review/preview'" in main_js
    assert "fetch('/api/plugins/obsidian/memory-review/apply'" in main_js
    assert "Apply this memory review to the vault?" in main_js
    assert "memoryReviewPreview" in main_js
    assert "data-memory-conflicts" in main_js
    assert ".obsidian-memory-review-panel" in style


def test_obsidian_phase3_password_prompts_do_not_render_password_values():
    main_js = (ROOT / "plugins" / "obsidian" / "frontend" / "main.js").read_text(encoding="utf-8")

    assert "Set or replace password protection for this vault?" in main_js
    assert "Remove password protection from this vault?" in main_js
    assert "Export password:" in main_js
    assert "Current vault password:" in main_js
    assert "Archive password, if needed:" in main_js
    assert "showToast('Vault password updated')" in main_js
    assert "showToast('Vault password removed')" in main_js
    assert "showToast(password" not in main_js
    assert "innerHTML = password" not in main_js


def test_obsidian_phase3_mobile_keeps_graph_switch_and_settings_usable():
    style = (ROOT / "plugins" / "obsidian" / "frontend" / "style.css").read_text(encoding="utf-8")

    marker = "@media (max-width: 640px)"
    assert marker in style
    mobile_body = style[style.index(marker):]
    assert ".obsidian-header-view-toggle" in mobile_body
    assert "display: none" not in mobile_body
    assert "font-size: 0" in mobile_body
    assert ".obsidian-settings-menu" in mobile_body
    assert "max-width: calc(100vw - 16px)" in mobile_body
    assert ".obsidian-graph-controls" in mobile_body
    assert "flex-wrap: wrap" in mobile_body


def test_obsidian_autocomplete_is_caret_positioned_and_context_filtered():
    main_js = (ROOT / "plugins" / "obsidian" / "frontend" / "main.js").read_text(encoding="utf-8")
    style = (ROOT / "plugins" / "obsidian" / "frontend" / "style.css").read_text(encoding="utf-8")

    assert "function positionAutocompleteMenu" in main_js
    assert "textarea.selectionStart" in main_js
    assert "marker.getBoundingClientRect()" in main_js
    assert "function isInSuppressedAutocompleteContext" in main_js
    assert "fenceCount % 2 === 1" in main_js
    assert "https?:\\/\\/" in main_js
    autocomplete_css = re.search(r"\.obsidian-autocomplete\s*\{(?P<body>.*?)\n\}", style, re.S)
    assert autocomplete_css
    assert "bottom:" not in autocomplete_css.group("body")
    assert "top: 10px" in autocomplete_css.group("body")


def test_obsidian_app_page_boots_standalone_panel():
    from plugins.obsidian.backend.routes import APP_HTML

    assert 'data-obsidian-standalone="true"' in APP_HTML
    assert "window.ODYSSEUS_OBSIDIAN_STANDALONE = true" in APP_HTML
    assert 'import "/api/plugins/obsidian/web/main.js"' in APP_HTML
    assert 'document.readyState === "loading"' in APP_HTML
