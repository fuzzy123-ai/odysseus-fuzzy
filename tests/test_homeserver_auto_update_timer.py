from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "homeserver" / "install-auto-update-timer.sh"
COMPOSE = ROOT / "docker-compose.yml"


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
    assert "KillMode=process" in script


def test_auto_update_wrapper_checks_before_backup_and_backs_up_before_pull():
    script = SCRIPT.read_text(encoding="utf-8")

    fetch_index = script.index("git fetch --prune --tags")
    no_update_index = script.index(
        "already current at $short_commit with a valid release manifest"
    )
    manifest_ready_index = script.index(
        'if runtime_release_manifest_matches "$local_commit"; then'
    )
    stale_runtime_index = script.index("runtime is stale or unavailable")
    snapshot_index = script.index("ops/homeserver/pre-update-snapshot.sh")
    pull_index = script.index("git pull --ff-only")
    metadata_index = script.index("ops/homeserver/update-odysseus-version-env.sh")
    build_index = script.index("build_app_image", metadata_index)
    switch_index = script.index("switch_to_built_app", build_index)

    assert fetch_index < manifest_ready_index < no_update_index
    assert no_update_index < stale_runtime_index < snapshot_index
    assert snapshot_index < pull_index < metadata_index < build_index < switch_index
    assert 'curl -fsS "$APP_URL/api/version" 2>/dev/null || true' in script
    assert "git merge-base --is-ancestor" in script
    assert "worktree is dirty; refusing scheduled update" in script
    assert "version API does not report deployed commit" in script


def test_auto_update_wrapper_builds_with_revision_bound_release_manifest():
    script = SCRIPT.read_text(encoding="utf-8")
    compose = COMPOSE.read_text(encoding="utf-8")

    pull_index = script.index("git pull --ff-only")
    metadata_index = script.index("ops/homeserver/update-odysseus-version-env.sh")
    manifest_index = script.index(
        'prepare_release_manifest "$release_revision" "$current_branch"'
    )
    build_index = script.index("build_app_image", manifest_index)
    switch_index = script.index("switch_to_built_app", build_index)

    assert pull_index < metadata_index < manifest_index < build_index < switch_index
    assert 'release_revision="$(git rev-parse HEAD)"' in script
    assert "python3 scripts/generate_release_manifest.py" in script
    assert "--output runtime/release-manifest.json" in script
    assert '--revision "$revision"' in script
    assert '--ref "$ref"' in script
    assert "--max-commits 100" in script
    assert 'export ODYSSEUS_RELEASE_REVISION="$revision"' in script
    assert (
        "ODYSSEUS_RELEASE_REVISION: ${ODYSSEUS_RELEASE_REVISION:-}"
        in compose
    )


def test_auto_update_wrapper_rebuilds_current_revision_when_manifest_is_not_ready():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "runtime_release_manifest_matches()" in script
    assert "from src.release_manifest import read_release_manifest" in script
    assert "expected = sys.argv[1]" in script
    assert 'state == "ready"' in script
    assert 'document.get("revision") == expected' in script
    assert 'runtime_release_manifest_matches "$local_commit"' in script
    assert 'runtime_release_manifest_matches "$release_revision"' in script
    assert (
        "checkout and runtime are current at $short_commit but release manifest "
        "is stale or unavailable; rebuilding deployment"
        in script
    )
    assert (
        "podman exec odysseus_odysseus_1 python -c"
        in script
    )


def test_auto_update_wrapper_refreshes_tool_capability_after_readiness():
    script = SCRIPT.read_text(encoding="utf-8")

    app_wait_index = script.index('wait_http "$APP_URL/" "odysseus app"')
    chroma_wait_index = script.index('wait_http "$CHROMA_URL" "chromadb"')
    version_check_index = script.index("version API does not report deployed commit")
    manifest_check_index = script.index(
        "runtime release manifest does not match deployed commit"
    )
    refresh_log_index = script.index("refreshing tool capability knowledge")
    refresh_index = script.index('refresh_tool_capability_knowledge "$short_commit"')
    completed_index = script.index("scheduled update completed")

    assert app_wait_index < chroma_wait_index < version_check_index
    assert version_check_index < manifest_check_index < refresh_log_index < refresh_index < completed_index
    assert "podman exec odysseus_odysseus_1" in script
    assert 'python scripts/refresh_tool_capability_knowledge.py --reason post-update --commit "$commit"' in script
    assert 'python3 scripts/refresh_tool_capability_knowledge.py --reason post-update --commit "$commit"' in script


def test_auto_update_wrapper_uses_podman_and_never_docker():
    script = SCRIPT.read_text(encoding="utf-8")

    assert 'compose_args=(-f docker-compose.yml)' in script
    assert 'compose_args+=(-f docker-compose.nextcloud.yml)' in script
    assert 'podman compose "${compose_args[@]}" "$@"' in script
    assert 'podman-compose "${compose_args[@]}" "$@"' in script
    assert "compose build odysseus" in script
    assert "compose up -d --no-deps --no-build --force-recreate odysseus" in script
    assert "podman image prune -f" not in script
    assert "docker compose" not in script.lower()
    assert "docker-compose up" not in script.lower()
    assert "docker image" not in script.lower()
    assert "docker run" not in script.lower()


def test_auto_update_wrapper_build_failure_cannot_fall_through_to_switch():
    script = SCRIPT.read_text(encoding="utf-8")

    build_function = script.index("build_app_image()")
    switch_function = script.index("switch_to_built_app()")
    build_call = script.index("build_app_image", switch_function)
    switch_call = script.index("switch_to_built_app", build_call)

    assert build_function < switch_function
    assert "compose build odysseus" in script[build_function:switch_function]
    assert build_call < switch_call
    assert "up -d --build" not in script
    assert "up -d --no-deps --no-build --force-recreate odysseus" in script
