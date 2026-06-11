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


def test_obsidian_graph_current_node_click_returns_to_editor():
    main_js = (ROOT / "plugins" / "obsidian" / "frontend" / "main.js").read_text(encoding="utf-8")

    assert "async function activateGraphNode(path)" in main_js
    assert "path === currentNotePath && currentViewMode === 'graph'" in main_js
    assert "setViewMode('document');" in main_js
    assert "node.addEventListener('click', () => activateGraphNode(node.dataset.path))" in main_js
    assert "activateGraphNode(node.dataset.path);" in main_js


def test_obsidian_file_tree_selects_and_renames_folders():
    main_js = (ROOT / "plugins" / "obsidian" / "frontend" / "main.js").read_text(encoding="utf-8")
    style = (ROOT / "plugins" / "obsidian" / "frontend" / "style.css").read_text(encoding="utf-8")

    assert "let selectedTreePath = null" in main_js
    assert "let inlineRenamePath = null" in main_js
    assert "function selectTreeItem(path)" in main_js
    assert "selectedTreePath === node.path" in main_js
    assert "function startInlineRenameFolder(path)" in main_js
    assert "async function commitInlineRenameFolder(oldPath, nextName)" in main_js
    assert "tree-rename-button" in main_js
    assert "tree-rename-input" in main_js
    assert "Rename selected folder" in main_js
    assert "async function promptRenameSelectedItem()" in main_js
    assert "startInlineRenameFolder(oldPath);" in main_js
    assert "Renamed folder" in main_js
    assert "e.key === 'F2'" in main_js
    assert "e.key === 'Escape'" in main_js
    assert "e.key === 'Enter'" in main_js
    assert "document.getElementById('obsidian-rename-note')?.addEventListener('click', promptRenameSelectedItem)" in main_js
    assert "Rename folder to:" not in main_js
    assert ".tree-rename-button" in style
    assert ".tree-rename-input" in style


def test_obsidian_panel_and_sidebar_split_are_resizable():
    main_js = (ROOT / "plugins" / "obsidian" / "frontend" / "main.js").read_text(encoding="utf-8")
    style = (ROOT / "plugins" / "obsidian" / "frontend" / "style.css").read_text(encoding="utf-8")

    assert 'id="obsidian-panel-resize-handle"' in main_js
    assert 'aria-label="Panel Resize Handle"' in main_js
    assert 'id="obsidian-split-resize-handle"' in main_js
    assert 'aria-label="Split Resize Handle"' in main_js
    assert "function setupObsidianResizers()" in main_js
    assert "odysseus.obsidian.panelWidth" in main_js
    assert "odysseus.obsidian.sidebarWidth" in main_js
    assert "setPointerCapture" in main_js
    assert "pointermove" in main_js
    assert "--obsidian-panel-width" in style
    assert "--obsidian-sidebar-width" in style
    assert ".obsidian-panel-resize-handle" in style
    assert ".obsidian-split-resize-handle" in style
    mobile_body = style[style.index("@media (max-width: 640px)"):]
    assert ".obsidian-panel-resize-handle,\n  .obsidian-split-resize-handle" in mobile_body
    assert "display: none" in mobile_body


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
        '<select id="obsidian-project-folder"',
        'id="obsidian-project-title"',
        'id="obsidian-project-kind"',
        'id="obsidian-project-description"',
        'id="obsidian-project-focus"',
        'id="obsidian-project-improve-description"',
        'id="obsidian-project-preview"',
        'id="obsidian-project-apply"',
        'id="obsidian-project-gamedev-draft"',
        'id="obsidian-project-preview-panel"',
    ):
        assert marker in main_js

    assert "function previewProjectPlan({ conceptApproved = false, approvedConcept = '' } = {})" in main_js
    assert "function improveProjectDescription()" in main_js
    assert "function createGameDevDraft()" in main_js
    assert "function renderGameDevDraftPanel(draftPayload)" in main_js
    assert "function isGameDevProjectKind(kind)" in main_js
    assert "function syncProjectPlanPreviewEdits()" in main_js
    assert "data-project-field=\"content\"" in main_js
    assert "data-project-field=\"outline\"" in main_js
    assert "function applyProjectPlan()" in main_js
    assert "function renderProjectFolderOptions()" in main_js
    assert "function loadProjectTemplateOptions()" in main_js
    assert "NEW_PROJECT_FOLDER_SENTINEL" in main_js
    assert "Create new project folder" in main_js
    assert "Create plan" in main_js
    assert "Creating plan and writing AI content sequentially" in main_js
    assert "GameDev concept draft" in main_js
    assert "Approve & create plan" in main_js
    assert "Regenerate draft" in main_js
    assert "fetch('/api/plugins/obsidian/project-plan/preview'" in main_js
    assert "fetch('/api/plugins/obsidian/project-plan/improve-description'" in main_js
    assert "fetch('/api/plugins/obsidian/project-plan/gamedev-draft'" in main_js
    assert "fetch('/api/plugins/obsidian/project-plan/apply'" in main_js
    assert "fetch('/api/plugins/obsidian/project-plan/templates')" in main_js
    assert "generate_content: true" in main_js
    assert "approved_concept: approvedConcept" in main_js
    assert "concept_approved: conceptApproved" in main_js
    assert "custom_focus" in main_js
    assert "Create this project structure in the vault?" in main_js
    assert "projectPlanPreview" in main_js
    assert "data-project-conflicts" in main_js
    assert ".obsidian-project-ai-btn" in style
    assert ".obsidian-project-gamedev-draft" in style
    assert ".obsidian-gamedev-draft-text" in style
    assert ".obsidian-project-file-editor" in style
    assert ".obsidian-project-file-grid" in style
    assert ".obsidian-project-wide-field" in style
    assert "#obsidian-project-folder,\n#obsidian-project-title,\n#obsidian-project-kind" in style
    assert "min-height: 42px" in style
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
    header_toggle_css = re.search(r"\.obsidian-header-view-toggle\s*\{(?P<body>.*?)\n  \}", mobile_body, re.S)
    assert header_toggle_css
    assert "display: none" not in header_toggle_css.group("body")
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
