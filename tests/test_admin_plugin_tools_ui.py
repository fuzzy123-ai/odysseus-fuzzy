from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_admin_tools_list_preserves_dynamic_plugin_metadata():
    admin_js = (ROOT / "static" / "js" / "admin.js").read_text(encoding="utf-8")

    assert "name: t.name || t.id" in admin_js
    assert "desc: t.desc || t.description || ''" in admin_js
    assert "cat: t.cat || t.category || 'Other'" in admin_js
    assert "'Plugins'" in admin_js
