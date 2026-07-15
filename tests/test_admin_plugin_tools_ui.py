from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_admin_tools_list_preserves_dynamic_plugin_metadata():
    admin_js = (ROOT / "static" / "js" / "admin.js").read_text(encoding="utf-8")

    assert "tool.source === 'plugin'" in admin_js
    assert "name: t.display_name || t.name || t.id" in admin_js
    assert "desc: t.description || t.desc || ''" in admin_js
    assert "TOOL_FAMILY_PRESENTATION.unclassified_dynamic" in admin_js
    assert "unclassified_dynamic: { label: 'Plugins'" in admin_js
    assert "cat: t.cat || t.category || 'Other'" not in admin_js
