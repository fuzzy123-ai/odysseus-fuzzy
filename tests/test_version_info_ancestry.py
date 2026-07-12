from __future__ import annotations

from pathlib import Path

import pytest

from src import version_info


@pytest.mark.parametrize(
    ("commit", "latest", "ancestry", "expected"),
    [
        ("same", "same", {}, (False, "current", "same")),
        ("local", "remote", {("remote", "local"): True}, (False, "current", "ahead")),
        (
            "local",
            "remote",
            {("remote", "local"): False, ("local", "remote"): True},
            (True, "outdated", "behind"),
        ),
        (
            "local",
            "remote",
            {("remote", "local"): False, ("local", "remote"): False},
            (True, "diverged", "diverged"),
        ),
        ("local", "remote", {}, (True, "outdated", "unknown")),
        (None, "remote", {}, (None, "unknown", "unknown")),
    ],
)
def test_classify_version_relation(monkeypatch, commit, latest, ancestry, expected):
    monkeypatch.setattr(
        version_info,
        "_git_is_ancestor",
        lambda ancestor, descendant: ancestry.get((ancestor, descendant)),
    )

    assert version_info._classify_version_relation(commit, latest) == expected


def test_version_payload_reports_ahead_release_as_current(monkeypatch):
    values = {
        ("rev-parse", "HEAD"): "local-release",
        ("rev-parse", "--short", "HEAD"): "local123",
        ("rev-parse", "--abbrev-ref", "HEAD"): "dev",
        ("config", "--get", "branch.dev.remote"): "fuzzy",
        ("config", "--get", "branch.dev.merge"): "refs/heads/dev",
    }
    monkeypatch.setattr(version_info, "_git", lambda args, timeout=2.0: values.get(tuple(args)))
    monkeypatch.setattr(version_info, "_remote_head", lambda remote, ref: "upstream-base")
    monkeypatch.setattr(
        version_info,
        "_git_is_ancestor",
        lambda ancestor, descendant: (ancestor, descendant) == ("upstream-base", "local-release"),
    )
    monkeypatch.setitem(version_info._CACHE, "payload", None)
    monkeypatch.setitem(version_info._CACHE, "expires_at", 0.0)

    payload = version_info.get_version_info(force_refresh=True)

    assert payload["status"] == "current"
    assert payload["relation"] == "ahead"
    assert payload["update_available"] is False


def test_version_payload_uses_matching_container_relation_hint(monkeypatch):
    monkeypatch.setattr(version_info, "_git", lambda args, timeout=2.0: None)
    monkeypatch.setattr(version_info, "_remote_head", lambda remote, ref: None)
    monkeypatch.setattr(version_info, "_git_is_ancestor", lambda ancestor, descendant: None)
    monkeypatch.setenv("ODYSSEUS_GIT_COMMIT", "local-release")
    monkeypatch.setenv("ODYSSEUS_GIT_SHORT_COMMIT", "local123")
    monkeypatch.setenv("ODYSSEUS_GIT_BRANCH", "dev")
    monkeypatch.setenv("ODYSSEUS_GIT_REMOTE_URL", "https://example.invalid/repo.git")
    monkeypatch.setenv("ODYSSEUS_GIT_REMOTE_REF", "refs/heads/dev")
    monkeypatch.setenv("ODYSSEUS_GIT_LATEST_COMMIT", "upstream-base")
    monkeypatch.setenv("ODYSSEUS_GIT_RELATION", "ahead")
    monkeypatch.setitem(version_info._CACHE, "payload", None)
    monkeypatch.setitem(version_info._CACHE, "expires_at", 0.0)

    payload = version_info.get_version_info(force_refresh=True)

    assert payload["commit"] == "local123"
    assert payload["latest_commit"] == "upstream-bas"
    assert payload["status"] == "current"
    assert payload["relation"] == "ahead"
    assert payload["update_available"] is False


def test_homeserver_version_relation_is_forwarded_to_container():
    root = Path(version_info.BASE_DIR)
    script = (root / "ops" / "homeserver" / "update-odysseus-version-env.sh").read_text(
        encoding="utf-8"
    )
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")

    assert 'git merge-base --is-ancestor "$LATEST_COMMIT" "$COMMIT"' in script
    assert '"ODYSSEUS_GIT_LATEST_COMMIT": sys.argv[7]' in script
    assert '"ODYSSEUS_GIT_RELATION": sys.argv[8]' in script
    assert "ODYSSEUS_GIT_LATEST_COMMIT=${ODYSSEUS_GIT_LATEST_COMMIT:-}" in compose
    assert "ODYSSEUS_GIT_RELATION=${ODYSSEUS_GIT_RELATION:-}" in compose
