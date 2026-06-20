from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent
_INDEX = (_REPO / "static" / "index.html").read_text(encoding="utf-8")
_ADMIN = (_REPO / "static" / "js" / "admin.js").read_text(encoding="utf-8")


def test_system_panel_contains_updates_backups_card():
    system_start = _INDEX.index('data-settings-panel="system"')
    logs_start = _INDEX.index('id="settings-system-logs-card"', system_start)
    system_prefix = _INDEX[system_start:logs_start]

    assert 'id="settings-system-updates-card"' in system_prefix
    assert 'id="adm-system-update-status"' in system_prefix
    assert 'id="adm-system-update-check"' in system_prefix
    assert 'id="adm-system-backup-now"' in system_prefix
    assert 'id="adm-system-update-now"' in system_prefix


def test_admin_js_loads_update_status_and_hooks_version_label():
    assert "async function loadSystemUpdateStatus" in _ADMIN
    assert "'/api/admin/system/update-status'" in _ADMIN
    assert "'/api/admin/system/update-check'" in _ADMIN
    assert "initSystemUpdateStatus" in _ADMIN
    assert "async function runSystemUpdateAction" in _ADMIN
    assert "'/api/admin/system/backup-now'" in _ADMIN
    assert "'/api/admin/system/update-now'" in _ADMIN
    assert "uiModule.styledConfirm" in _ADMIN
    assert "versionLabel.classList.add('system-link')" in _ADMIN
    assert "settings-system-updates-card" in _ADMIN
