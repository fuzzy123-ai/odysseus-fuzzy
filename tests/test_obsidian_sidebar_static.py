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
