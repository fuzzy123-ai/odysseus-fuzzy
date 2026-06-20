from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "homeserver" / "install-auto-update-timer.sh"


def test_auto_update_timer_installs_safe_systemd_units():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "odysseus-auto-update.service" in script
    assert "odysseus-auto-update.timer" in script
    assert "EnvironmentFile=-$ENV_FILE" in script
    assert "OnCalendar=$SCHEDULE" in script
    assert "ODYSSEUS_AUTO_UPDATE_SCHEDULE:-*-*-* 04:20:00" in script
    assert "Persistent=true" in script
    assert "RandomizedDelaySec=20m" in script
    assert "After=network-online.target odysseus-homeserver-backup.service" in script


def test_auto_update_wrapper_checks_before_backup_and_backs_up_before_pull():
    script = SCRIPT.read_text(encoding="utf-8")

    fetch_index = script.index("git fetch --prune --tags")
    no_update_index = script.index("already current")
    stale_runtime_index = script.index("runtime is stale or unavailable")
    snapshot_index = script.index("ops/homeserver/pre-update-snapshot.sh")
    pull_index = script.index("git pull --ff-only")
    metadata_index = script.index("ops/homeserver/update-odysseus-version-env.sh")
    compose_index = script.index("compose_up", metadata_index)

    assert fetch_index < no_update_index < stale_runtime_index < snapshot_index
    assert snapshot_index < pull_index < metadata_index < compose_index
    assert 'curl -fsS "$APP_URL/api/version" 2>/dev/null || true' in script
    assert "git merge-base --is-ancestor" in script
    assert "worktree is dirty; refusing scheduled update" in script
    assert "version API does not report deployed commit" in script


def test_auto_update_wrapper_uses_podman_and_never_docker():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "podman compose up -d --build" in script
    assert "podman-compose up -d --build" in script
    assert "podman image prune -f" in script
    assert "docker" not in script.lower()
