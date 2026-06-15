from pathlib import Path
import re
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_main_app_loads_plugin_ui_loader():
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    assert '<script type="module" src="/api/plugins/ui-loader.js"></script>' in index


def test_obsidian_frontend_javascript_syntax_is_valid():
    node = shutil.which("node")
    if not node:
        return
    result = subprocess.run(
        [node, "--check", str(ROOT / "plugins" / "obsidian" / "frontend" / "main.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_obsidian_plugin_loader_is_auth_exempt():
    app_py = (ROOT / "app.py").read_text(encoding="utf-8")
    prefix_match = re.search(r"AUTH_EXEMPT_PREFIXES\s*=\s*\[(.*?)\]", app_py, re.S)

    assert '"/api/plugins/ui-loader.js"' in app_py
    assert prefix_match
    assert '"/api/plugins/obsidian"' not in prefix_match.group(1)


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


def test_obsidian_phase6_cytoscape_graph_renderer_contract():
    main_js = (ROOT / "plugins" / "obsidian" / "frontend" / "main.js").read_text(encoding="utf-8")
    style = (ROOT / "plugins" / "obsidian" / "frontend" / "style.css").read_text(encoding="utf-8")
    cytoscape_asset = ROOT / "plugins" / "obsidian" / "frontend" / "cytoscape.min.js"

    assert cytoscape_asset.exists()
    assert "const OBSIDIAN_GRAPH_RENDERER_KEY = 'odysseus.obsidian.graphRenderer'" in main_js
    assert "const OBSIDIAN_CYTOSCAPE_ASSET = '/api/plugins/obsidian/web/cytoscape.min.js'" in main_js
    assert "function loadCytoscape()" in main_js
    assert "async function renderCytoscapeGraph(graph, prepared)" in main_js
    assert "function renderSvgGraphFallback(graph, prepared)" in main_js
    assert "const OBSIDIAN_GRAPH_WHEEL_SENSITIVITY = 0.55" in main_js
    assert "function isVaultRootSelected()" in main_js
    assert "function graphFocusPath()" in main_js
    assert "const focusPath = graphFocusPath();" in main_js
    assert "return isVaultRootSelected() ? '' : (currentNotePath || '')" in main_js
    assert "if (isVaultRootSelected()) return null;" in main_js
    assert "wheelSensitivity: OBSIDIAN_GRAPH_WHEEL_SENSITIVITY" in main_js
    assert "if (currentViewMode === 'graph') {\n          renderGraphView();" in main_js
    assert "Cytoscape graph failed, falling back to SVG" in main_js
    assert "function prepareGraphData(graphData)" in main_js
    assert "function directFolderForPath(path)" in main_js
    assert "node => node.type === 'folder'" in main_js
    assert "parent: parent || undefined" in main_js
    assert "node[type = \"folder\"]" in main_js
    assert "node[type = \"markdown\"]" in main_js
    assert "obsidian-focused-project-folder" in main_js
    assert "obsidian-focused-project-node" in main_js
    assert "avoidOverlapPadding" in main_js
    assert "componentSpacing: 110" in main_js
    assert ".obsidian-graph-canvas" in style
    assert ".obsidian-graph-renderer-badge" not in style
    assert 'id="obsidian-relationship-add"' not in main_js
    assert 'id="obsidian-relationship-delete"' not in main_js


def test_obsidian_file_tree_selects_and_renames_folders():
    main_js = (ROOT / "plugins" / "obsidian" / "frontend" / "main.js").read_text(encoding="utf-8")
    style = (ROOT / "plugins" / "obsidian" / "frontend" / "style.css").read_text(encoding="utf-8")

    assert "let selectedTreePath = null" in main_js
    assert "let inlineRenamePath = null" in main_js
    assert "function selectTreeItem(path)" in main_js
    assert "selectedTreePath === node.path" in main_js
    assert "const isFolderSelected = node.is_dir && selectedTreePath === node.path" in main_js
    assert "const isFileSelected = !node.is_dir && isSelected" in main_js
    assert "function startInlineRenameItem(path)" in main_js
    assert "function startInlineRenameFolder(path)" in main_js
    assert "function inlineRenameTargetPath(oldPath, trimmedName, isFolder)" in main_js
    assert "oldPath.toLowerCase().endsWith('.md')" in main_js
    assert "`${trimmedName}.md`" in main_js
    assert "async function commitInlineRenameItem(oldPath, nextName)" in main_js
    assert "async function commitInlineRenameFolder(oldPath, nextName)" in main_js
    assert "tree-rename-button" in main_js
    assert "tree-delete-button" in main_js
    assert "async function deleteNote(path)" in main_js
    assert "await deleteNote(node.path);" in main_js
    assert "await refreshSearchResults();" in main_js
    assert "async function refreshSearchResults()" in main_js
    assert "/api/plugins/obsidian/search?q=" in main_js
    assert "if (selectedTreePath === path)" in main_js
    assert "if (currentNotePath === path)" in main_js
    assert "document.getElementById('obsidian-editor-container')?.classList.add('hidden')" in main_js
    assert "tree-rename-input" in main_js
    assert "Rename selected folder" in main_js
    assert "Rename selected note" in main_js
    assert "Delete selected note" in main_js
    assert "async function promptRenameSelectedItem()" in main_js
    assert "startInlineRenameItem(oldPath);" in main_js
    assert "Renamed folder" in main_js
    assert "e.key === 'F2'" in main_js
    assert "e.key === 'Escape'" in main_js
    assert "e.stopPropagation();" in main_js
    assert "e.key === 'Enter'" in main_js
    assert "search-result-actions" in main_js
    assert "Rename selected search result" in main_js
    assert "Delete selected search result" in main_js
    assert "renderSearchResults(results);" in main_js
    assert 'id="obsidian-rename-note"' not in main_js
    assert 'id="obsidian-delete-note"' not in main_js
    assert "Rename folder to:" not in main_js
    assert ".tree-rename-button" in style
    assert ".tree-delete-button" in style
    assert ".tree-rename-button:focus-visible" in style
    assert ".tree-delete-button:focus-visible" in style
    assert ".search-result-actions" in style
    assert ".obsidian-editor-actions" not in style
    assert ".tree-rename-input" in style


def test_obsidian_new_note_expands_target_folder():
    main_js = (ROOT / "plugins" / "obsidian" / "frontend" / "main.js").read_text(encoding="utf-8")

    new_note_body = main_js.split("// New Note", 1)[1].split("// New Folder", 1)[0]
    assert "const dir = currentTargetFolder();" in new_note_body
    assert "expandedFolders.add(dir);" in new_note_body
    assert new_note_body.index("expandedFolders.add(dir);") < new_note_body.index("await loadVaultFiles();")
    assert new_note_body.index("await loadVaultFiles();") < new_note_body.index("await openNote(fullPath);")


def test_obsidian_panel_and_sidebar_split_are_resizable():
    main_js = (ROOT / "plugins" / "obsidian" / "frontend" / "main.js").read_text(encoding="utf-8")
    style = (ROOT / "plugins" / "obsidian" / "frontend" / "style.css").read_text(encoding="utf-8")

    assert 'id="obsidian-panel-resize-handle"' in main_js
    assert 'aria-label="Panel Resize Handle"' in main_js
    assert 'id="obsidian-split-resize-handle"' in main_js
    assert 'aria-label="Split Resize Handle"' in main_js
    assert "function setupObsidianResizers()" in main_js
    assert "function setObsidianPanelCssVar(name, value)" in main_js
    assert "target.style.setProperty(name, value)" in main_js
    assert "odysseus.obsidian.panelWidth" in main_js
    assert "odysseus.obsidian.sidebarWidth" in main_js
    assert "handle.classList.add('resizing')" in main_js
    assert "handle.classList.remove('resizing')" in main_js
    assert "setPointerCapture" in main_js
    assert "pointermove" in main_js
    assert "--obsidian-panel-width" in style
    assert "--obsidian-sidebar-width" in style
    assert ".obsidian-panel-resize-handle" in style
    assert ".obsidian-split-resize-handle" in style
    assert ".obsidian-panel-resize-handle.resizing::after" in style
    assert ".obsidian-split-resize-handle.resizing::after" in style
    assert "body.obsidian-resizing .obsidian-panel-resize-handle::after" not in style
    assert "body.obsidian-resizing .obsidian-split-resize-handle::after" not in style
    mobile_body = style[style.index("@media (max-width: 640px)"):]
    assert ".obsidian-panel-resize-handle,\n  .obsidian-split-resize-handle" in mobile_body
    assert "display: none" in mobile_body


def test_obsidian_surface_modes_contract():
    main_js = (ROOT / "plugins" / "obsidian" / "frontend" / "main.js").read_text(encoding="utf-8")
    style = (ROOT / "plugins" / "obsidian" / "frontend" / "style.css").read_text(encoding="utf-8")

    assert "const OBSIDIAN_SURFACE_MODE_KEY = 'odysseus.obsidian.surfaceMode'" in main_js
    assert "style.css?v=project-sessions-v1" in main_js
    assert "const OBSIDIAN_SURFACE_DEFAULT = 'sidebar'" in main_js
    assert "const OBSIDIAN_SURFACE_MODES = ['sidebar', 'overlay', 'fullscreen']" in main_js
    assert "function normalizeSurfaceMode(mode)" in main_js
    assert "function getStoredSurfaceMode()" in main_js
    assert "function initializeClosedObsidianSurface" in main_js
    assert "function changeObsidianSurfaceMode(mode)" in main_js
    assert 'id="obsidian-modal"' in main_js
    assert 'data-obsidian-surface-mode="sidebar"' in main_js
    assert 'data-obsidian-surface-mode="overlay"' in main_js
    assert 'data-obsidian-surface-mode="fullscreen"' in main_js
    assert "Window mode" in main_js
    assert "Modals.register(OBSIDIAN_MODAL_ID" in main_js
    assert "label: 'Obsidian'" in main_js
    assert "let minimizedSurfaceMode = null" in main_js
    assert "const mode = normalizeSurfaceMode(minimizedSurfaceMode || getStoredSurfaceMode())" in main_js
    assert "Modals.minimize(OBSIDIAN_MODAL_ID)" in main_js
    assert "Modals.restore(OBSIDIAN_MODAL_ID)" in main_js
    assert "makeWindowDraggable(modal" in main_js
    assert 'class="obsidian-panel-header modal-header"' in main_js
    assert "content?.querySelector('.obsidian-panel-header')" in main_js
    assert "content?.querySelector('.obsidian-panel-title')" not in main_js
    assert "body.obsidian-fullscreen #obsidian-panel" in style
    assert "body.obsidian-surface-overlay .obsidian-panel-content" in style
    assert "#obsidian-modal.obsidian-overlay-fullscreen .obsidian-panel-content" in style
    assert '#minimized-dock:has(.minimized-dock-chip[data-modal-id="obsidian-modal"])' in style
    fullscreen_content = re.search(r"body\.obsidian-fullscreen \.obsidian-panel-content\s*\{(?P<body>.*?)\n\}", style, re.S)
    assert fullscreen_content
    assert "height: 100%" in fullscreen_content.group("body")
    assert "max-height: none" in fullscreen_content.group("body")
    mobile_body = style[style.index("@media (max-width: 640px)"):]
    assert "body.obsidian-surface-overlay #obsidian-panel" in mobile_body
    assert "body.obsidian-fullscreen #obsidian-panel" in mobile_body
    assert "body.obsidian-surface-overlay .obsidian-panel-content" in mobile_body
    assert "body.obsidian-fullscreen .obsidian-panel-content" in mobile_body


def test_obsidian_closed_boot_does_not_activate_overlay_or_fullscreen():
    main_js = (ROOT / "plugins" / "obsidian" / "frontend" / "main.js").read_text(encoding="utf-8")
    style = (ROOT / "plugins" / "obsidian" / "frontend" / "style.css").read_text(encoding="utf-8")

    init_body = re.search(r"function init\(\) \{(?P<body>.*?)\n\}", main_js, re.S)
    assert init_body
    assert "initializeClosedObsidianSurface();" in init_body.group("body")
    assert "applyObsidianSurfaceMode(getStoredSurfaceMode())" not in init_body.group("body")

    closed_body = re.search(r"function initializeClosedObsidianSurface.*?\{(?P<body>.*?)\n\}", main_js, re.S)
    assert closed_body
    assert "clearObsidianSurfaceClasses();" in closed_body.group("body")
    assert "document.body.classList.remove('obsidian-open')" in closed_body.group("body")
    assert "getObsidianModal()?.classList.add('hidden')" in closed_body.group("body")

    panel_css = re.search(r"#obsidian-panel\s*\{(?P<body>.*?)\n\}", style, re.S)
    assert panel_css
    assert "--obsidian-app-height: calc(100dvh - var(--obsidian-app-top))" in panel_css.group("body")
    assert "height: var(--obsidian-app-height)" in panel_css.group("body")
    assert "pointer-events: none" in panel_css.group("body")

    open_panel_css = re.search(r"body\.obsidian-open #obsidian-panel\s*\{(?P<body>.*?)\n\}", style, re.S)
    assert open_panel_css
    assert "pointer-events: auto" in open_panel_css.group("body")
    assert "body.obsidian-open.obsidian-fullscreen #obsidian-panel" in style


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
    assert 'role="radiogroup" aria-label="Window mode"' in main_js
    assert "syncSurfaceModeControls(normalized)" in main_js
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
    assert "function renderProjectCustomSelect(type, items = null)" in main_js
    assert "function projectSelectIcon(type, key = '')" in main_js
    assert "function projectFileGenerationState(file)" in main_js
    assert "function projectFileGenerationLabel(state)" in main_js
    assert 'data-project-select-trigger="folder"' in main_js
    assert 'data-project-select-trigger="kind"' in main_js
    assert "NEW_PROJECT_FOLDER_SENTINEL" in main_js
    assert "Plan new project folder" in main_js
    assert "VAULT_ROOT_TREE_PATH" in main_js
    assert "Vault root" in main_js
    assert "function projectSessionDebugHtml()" in main_js
    assert "function starPositions(prepared, width, height)" in main_js
    assert "function projectHubNode(nodes)" in main_js
    assert "Create plan preview" in main_js
    assert "Create files" in main_js
    assert "Creating plan and writing AI content sequentially" in main_js
    assert "GameDev concept draft" in main_js
    assert "Approve & create plan" in main_js
    assert "Regenerate draft" in main_js
    assert "fetch('/api/plugins/obsidian/project-plan/preview'" in main_js
    assert "fetch('/api/plugins/obsidian/project-plan/sessions'" in main_js
    assert "project-plan/sessions/${encodeURIComponent(sessionId)}/preview-stream" in main_js
    assert "project-plan/sessions/${encodeURIComponent(activeProjectPlanSessionId)}/apply" in main_js
    assert "fetch('/api/plugins/obsidian/project-plan/improve-description'" in main_js
    assert "fetch('/api/plugins/obsidian/project-plan/gamedev-draft'" in main_js
    assert "'/api/plugins/obsidian/project-plan/apply'" in main_js
    assert "fetch('/api/plugins/obsidian/project-plan/templates')" in main_js
    assert "generate_content: true" in main_js
    assert "approved_concept: approvedConcept" in main_js
    assert "concept_approved: conceptApproved" in main_js
    assert "custom_focus" in main_js
    assert "Create these project files in the vault?" in main_js
    assert "projectPlanPreview" in main_js
    assert "projectPlanSessions" in main_js
    assert "activeProjectPlanSessionId" in main_js
    assert "function openProjectPlanSession(sessionId)" in main_js
    assert "function treeWithProjectPlanSessions(nodes)" in main_js
    assert "data-project-session-cancel" in main_js
    assert "data-project-relationship-index" in main_js
    assert "data-project-conflicts" in main_js
    assert "data-project-generation-state" in main_js
    assert "__generationState = 'wip'" in main_js
    assert "__generationState = 'done'" in main_js
    assert 'data-project-index="${index}" data-project-generation-state' in main_js
    assert ".obsidian-project-ai-btn" in style
    assert ".obsidian-project-select-trigger" in style
    assert ".obsidian-project-select-option" in style
    assert ".obsidian-project-file-state-done" in style
    assert ".obsidian-project-file-state-wip" in style
    assert ".obsidian-project-file-state-open" in style
    assert ".tree-project-session" in style
    assert ".tree-root-node" in style
    assert ".tree-project-session-spinner" in style
    assert ".obsidian-project-session-progress" in style
    assert ".obsidian-project-debug" in style
    assert ".obsidian-project-relationships" in style
    assert ".obsidian-project-session-bar" in style
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


def test_obsidian_markdown_preview_links_and_tags_contract():
    main_js = (ROOT / "plugins" / "obsidian" / "frontend" / "main.js").read_text(encoding="utf-8")
    style = (ROOT / "plugins" / "obsidian" / "frontend" / "style.css").read_text(encoding="utf-8")

    assert "import { mdToHtml, renderMermaid } from '/static/js/markdown.js'" in main_js
    assert 'id="obsidian-rendered-preview"' in main_js
    assert "function normalizeMarkdownTags(content)" in main_js
    assert "function renderEditorPreview(content)" in main_js
    assert "function enhancePreviewTags(container)" in main_js
    assert "function handleRenderedPreviewClick(e)" in main_js
    assert "function openTagDetails(tag, anchor)" in main_js
    assert "function tagMetaPath(tag)" in main_js
    assert "Tags/${clean}.md" in main_js
    assert "href.startsWith('#obsidian-link-')" in main_js
    assert "resolveMarkdownFileLink(href)" in main_js
    assert "normalizeMarkdownTags(original)" in main_js
    assert "renderEditorPreview(textarea.value)" in main_js
    assert "data-obsidian-tag" in main_js
    assert ".obsidian-tag-badge" in style
    assert ".obsidian-tag-detail-popover" in style
    assert ".obsidian-tag-meta-action" in style


def test_obsidian_phase5_memory_review_ui_contract():
    main_js = (ROOT / "plugins" / "obsidian" / "frontend" / "main.js").read_text(encoding="utf-8")
    style = (ROOT / "plugins" / "obsidian" / "frontend" / "style.css").read_text(encoding="utf-8")

    for marker in (
        'id="obsidian-memory-review"',
        'id="obsidian-memory-review-panel"',
        'id="obsidian-memory-title"',
        'id="obsidian-memory-action"',
        'id="obsidian-memory-save-to"',
        'id="obsidian-memory-save-to-label"',
        'id="obsidian-memory-folder"',
        'id="obsidian-memory-note"',
        'id="obsidian-memory-tags"',
        'id="obsidian-memory-tag-entry"',
        'id="obsidian-memory-tag-menu"',
        'id="obsidian-memory-destination-picker"',
        'data-memory-picker-tab="folders"',
        'data-memory-picker-tab="notes"',
        'id="obsidian-memory-content"',
        'id="obsidian-memory-preview"',
        'id="obsidian-memory-apply"',
        'id="obsidian-memory-preview-panel"',
    ):
        assert marker in main_js

    assert "Title (optional)" in main_js
    assert "Save one reviewed insight into your vault" in main_js
    assert "Insight to save" in main_js
    assert "Preview changes" in main_js
    assert "Apply to vault" in main_js
    assert 'id="obsidian-memory-folder" type="hidden"' in main_js
    assert 'id="obsidian-memory-note" type="hidden"' in main_js
    assert "Target folder, e.g. Memory Review" not in main_js
    assert "Existing note path for append/link" not in main_js
    assert "Tags, comma separated" not in main_js
    assert "function openMemoryDestinationPicker()" in main_js
    assert "function renderMemoryDestinationPicker()" in main_js
    assert "flattenNotes(vaultFiles)" in main_js
    assert "function addMemoryTag(value)" in main_js
    assert "function updateMemoryTagSuggestions()" in main_js
    assert "await getVaultTags()" in main_js
    assert "e.key === 'Enter'" in main_js
    assert "Generated markdown" in main_js
    assert "Title source" in main_js
    assert "function previewMemoryReview()" in main_js
    assert "function applyMemoryReview()" in main_js
    assert "fetch('/api/plugins/obsidian/memory-review/preview'" in main_js
    assert "fetch('/api/plugins/obsidian/memory-review/apply'" in main_js
    assert "Apply this memory review to the vault?" in main_js
    assert "memoryReviewPreview" in main_js
    assert "data-memory-conflicts" in main_js
    assert ".obsidian-memory-review-panel" in style
    assert ".obsidian-memory-save-to" in style
    assert ".obsidian-memory-tag-chip" in style
    assert ".obsidian-memory-preview-summary" in style


def test_obsidian_memory_tree_audit_ui_contract():
    main_js = (ROOT / "plugins" / "obsidian" / "frontend" / "main.js").read_text(encoding="utf-8")
    style = (ROOT / "plugins" / "obsidian" / "frontend" / "style.css").read_text(encoding="utf-8")

    for marker in (
        'id="obsidian-memory-tree"',
        'id="obsidian-memory-tree-panel"',
        'id="obsidian-memory-tree-refresh"',
        'id="obsidian-memory-tree-content"',
        'data-memory-tree-tab="tree"',
        'data-memory-tree-tab="audit"',
        'data-memory-tree-tab="quarantine"',
        'data-memory-tree-tab="raptor"',
    ):
        assert marker in main_js

    assert "function showMemoryTreePanel()" in main_js
    assert "function loadMemoryTreeDashboard()" in main_js
    assert "function renderMemoryTreeOverview()" in main_js
    assert "function renderKnowledgeAudit()" in main_js
    assert "function renderQuarantineList()" in main_js
    assert "function renderRaptorStatus()" in main_js
    assert "function renderUnifiedMemoryStatus()" in main_js
    assert "function renderMemoryReadinessFamilies()" in main_js
    assert "function renderMemoryReadinessGaps()" in main_js
    assert "function memoryReadinessGapNames()" in main_js
    assert "function raptorDirtySourceRecords(lineage = {})" in main_js
    assert "function raptorMissingSourceRecords(lineage = {})" in main_js
    assert "function raptorTaintedSourceRecords(lineage = {})" in main_js
    assert "function renderRetrievalIsolationNotice(" in main_js
    assert "function renderMemoryRecordLineage(" in main_js
    assert "function renderMemoryRecordIsolation(" in main_js
    assert "shortMemorySourceHash" in main_js
    assert 'data-memory-isolation="true"' in main_js
    assert 'data-memory-lineage="true"' in main_js
    assert 'data-memory-record-isolation="true"' in main_js
    assert "Default retrieval isolation" in main_js
    assert "function memoryFreshnessFilteringState(report = {})" in main_js
    assert "report.filtering_state || report.summary?.filtering_state" in main_js
    assert "String(rawState).replace(/_/g, '-')" in main_js
    assert 'data-memory-isolation-state="${escapeHtml(filtering.state)}"' in main_js
    assert "active filtering" in main_js
    assert "audit only" in main_js
    assert "Default context is unchanged because hybrid Freshness filtering is off." in main_js
    assert "default_retrieval" in main_js
    assert "summary.default_retrieval" in main_js
    assert "summary.isolated" in main_js
    assert "summary.isolation_counts" in main_js
    assert "{ label: 'Default retrieval', value: summary.default_retrieval || 0 }" in main_js
    assert "{ label: 'Isolated', value: summary.isolated || 0 }" in main_js
    assert "memoryStatusReport.summary || {}" in main_js
    assert 'data-memory-readiness-state="${escapeHtml(state)}"' in main_js
    assert "const gate = memoryStatusReport.readiness_gate || summary.readiness_gate || {}" in main_js
    assert "const retrievalPolicy = memoryStatusReport.retrieval_policy || summary.retrieval_policy || {}" in main_js
    assert "const freshnessIsolationFlags = memoryStatusReport.freshness_isolation_flags || summary.freshness_isolation_flags || {}" in main_js
    assert "activeFreshnessIsolationFlags.length ? activeFreshnessIsolationFlags.join(', ') : 'clear'" in main_js
    assert "const raptorLineageFlags = memoryStatusReport.raptor_lineage_flags || summary.raptor_lineage_flags || {}" in main_js
    assert "activeRaptorLineageFlags.length ? activeRaptorLineageFlags.join(', ') : 'clear'" in main_js
    assert "const raptorWriteGate = memoryStatusReport.raptor_write_gate || summary.raptor_write_gate || {}" in main_js
    assert "RAPTOR write gate" in main_js
    assert "{ label: 'Gate', value: gate.state || 'unknown' }" in main_js
    assert "Freshness isolation" in main_js
    assert "RAPTOR lineage" in main_js
    assert "memoryStatusReport?.readiness_gate?.gaps || memoryStatusReport?.summary?.readiness_gate?.gaps" in main_js
    assert "summary.readiness_families ?? summary.families" in main_js
    assert "summary.status_families ?? Object.keys(memoryStatusReport.families || {}).length" in main_js
    assert "summary.ready_families ?? 0}/${summary.readiness_families ?? summary.families ?? 0} families ready" in main_js
    assert "summary.filtering_state || memoryStatusReport.filtering_state || 'unknown'" in main_js
    assert "Default filtered" in main_js
    assert "retrievalPolicy.default_retrieval_is_filtered ? 'yes' : 'no'" in main_js
    assert "summary?.readiness_gap_names" in main_js
    assert "memoryStatusReport?.readiness_by_family || {}" in main_js
    assert 'data-memory-family-ready="${signal.ready ? \'true\' : \'false\'}"' in main_js
    assert "memoryTreeReport.readiness" in main_js
    assert "const gate = memoryTreeReport.readiness_gate || summary.readiness_gate || {}" in main_js
    assert "{ label: 'Gate', value: gate.state || 'unknown' }" in main_js
    assert "{ label: 'Gaps', value: summary.readiness_gaps ?? (readiness.gaps || []).length }" in main_js
    assert "renderMemoryStatusCounts('Readiness gaps'" in main_js
    assert "knowledgeAuditReport.readiness" in main_js
    assert "const gate = knowledgeAuditReport.readiness_gate || summary.readiness_gate || {}" in main_js
    assert "quarantineReport.readiness" in main_js
    assert "const gate = quarantineReport.readiness_gate || quarantineReport.summary?.readiness_gate || {}" in main_js
    assert "{ label: 'Gate state', value: gate.state || 'unknown' }" in main_js
    assert "{ label: 'Readiness', value: summary.readiness_state || readiness.state || 'unknown' }" in main_js
    assert "{ label: 'Readiness', value: quarantineReport.summary?.readiness_state || readiness.state || 'unknown' }" in main_js
    assert "renderMemoryStatusCounts('Review gaps'" in main_js
    assert "renderMemoryStatusCounts('Isolated statuses', summary.isolation_counts || {})" in main_js
    assert "renderMemoryStatusCounts('Statuses', summary.status_counts || {})" in main_js
    assert "needsReview: (quarantineReport.items || []).filter(item => item.channel === 'needs_review')" in main_js
    assert "renderMemoryStatusCounts('By channel', quarantineReport.summary?.by_channel || {})" in main_js
    assert "isolation_reason" in main_js
    assert "source_mtime" in main_js
    assert "source_hash" in main_js
    assert "fetchMemoryDashboardJson('/api/plugins/obsidian/memory/status')" in main_js
    assert "fetchMemoryDashboardJson('/api/plugins/obsidian/memory-tree/analyze')" in main_js
    assert "fetchMemoryDashboardJson('/api/plugins/obsidian/knowledge-audit')" in main_js
    assert "fetchMemoryDashboardJson('/api/plugins/obsidian/quarantine')" in main_js
    assert "fetchMemoryDashboardJson('/api/plugins/obsidian/raptor/status')" in main_js
    assert "const summary = raptorReport.summary || {}" in main_js
    assert "const gate = raptorReport.readiness_gate || summary.readiness_gate || {}" in main_js
    assert "const writeGate = raptorReport.write_gate || summary.write_gate || {}" in main_js
    assert "const lineageFlags = raptorReport.lineage_flags || summary.lineage_flags || {}" in main_js
    assert "activeLineageFlags.length ? activeLineageFlags.join(', ') : 'clear'" in main_js
    assert "summary.source_count" in main_js
    assert "summary.dirty_sources" in main_js
    assert "summary.missing_sources" in main_js
    assert "summary.tainted_sources" in main_js
    assert "summary.invalid_sources" in main_js
    assert "raptorReport.readiness" in main_js
    assert "summary.readiness_state" in main_js
    assert "summary.readiness_gaps" in main_js
    assert "Review gaps:" in main_js
    assert "Write gate" in main_js
    assert "Write gate:" in main_js
    assert "writeGate.gaps" in main_js
    assert "summary.writes_supported" in main_js
    assert "Lineage flags" in main_js
    assert "summary.writes_supported ?? raptorReport.writes_supported" in main_js
    assert "renderMemoryWarnings(raptorReport)" in main_js
    assert "lineage.dirty_sources" in main_js
    assert "lineage.missing_sources" in main_js
    assert "lineage.tainted_sources" in main_js
    assert "Dirty sources" in main_js
    assert "Missing sources" in main_js
    assert "Tainted sources" in main_js
    assert ".obsidian-memory-tree-panel" in style
    assert ".obsidian-memory-tree-tabs" in style
    assert ".obsidian-memory-tree-metrics" in style
    assert ".obsidian-memory-tree-card" in style
    assert ".obsidian-memory-status-band" in style
    assert ".obsidian-memory-status-families" in style
    assert ".obsidian-memory-status-gaps" in style
    assert ".obsidian-memory-isolation" in style
    assert '.obsidian-memory-isolation[data-memory-isolation-state="active"] > small' in style
    assert ".obsidian-memory-lineage" in style
    assert ".obsidian-memory-record-isolation" in style


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
