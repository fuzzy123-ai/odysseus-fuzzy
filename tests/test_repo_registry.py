import json
from pathlib import Path

import pytest

from src.repo_registry import (
    RepoRecord,
    RepoRegistry,
    RepoRegistryError,
    RepoRemote,
    redact_remote_url,
)


def _record(**overrides):
    values = {
        "title": "Kundenportal Repo",
        "owner": "fuzzy123-ai",
        "workspace_root": "projects/kundenportal",
        "project_root": "projects/kundenportal/repo",
        "created_at": "2026-06-28T10:00:00Z",
    }
    values.update(overrides)
    return RepoRecord.create(**values)


def test_private_repo_defaults_to_local_only_and_read_actions():
    record = _record()

    assert record.repo_id == "kundenportal-repo"
    assert record.privacy_class == "private"
    assert record.provider_scope == "local_only"
    assert record.allowed_actions == (
        "status",
        "log",
        "diff_stat",
        "changed_paths",
        "remotes",
        "changes",
        "change_history",
    )


def test_public_repo_defaults_to_default_provider_scope():
    record = _record(title="Public Demo", privacy_class="public")

    assert record.provider_scope == "default"


def test_remote_redacts_credentials_query_and_fragment():
    remote = RepoRemote.create(
        name="fuzzy",
        url="https://x-access-token:secret-value@github.com/fuzzy123-ai/demo.git?token=abc#main",
        purpose="fork",
        push_policy="push_allowed",
    )

    assert remote.url_redacted == "https://github.com/fuzzy123-ai/demo.git"
    assert "secret" not in remote.url_redacted
    assert "token" not in remote.url_redacted
    assert redact_remote_url("git@github.com:fuzzy123-ai/demo.git") == "git@github.com:fuzzy123-ai/demo.git"


def test_sensitive_repo_rejects_external_provider_scope():
    with pytest.raises(RepoRegistryError, match="sensitive repos"):
        _record(
            title="Private Archive",
            privacy_class="sensitive",
            provider_scope="external_allowed",
        )


def test_project_root_must_stay_inside_workspace_root():
    with pytest.raises(RepoRegistryError, match="project_root"):
        _record(
            workspace_root="projects/kundenportal",
            project_root="projects/other/repo",
        )


def test_absolute_host_paths_and_secret_like_text_are_rejected():
    with pytest.raises(RepoRegistryError, match="absolute paths"):
        _record(project_root=r"C:\Users\nkatz\secret")

    with pytest.raises(RepoRegistryError, match="secret material"):
        _record(title="Demo password=supersecret")


def test_push_action_requires_explicit_push_allowed_remote():
    with pytest.raises(RepoRegistryError, match="push action"):
        _record(
            allowed_actions=["status", "push"],
            remotes=[
                RepoRemote.create(
                    name="origin",
                    url="https://github.com/upstream/demo.git",
                    purpose="origin",
                )
            ],
        )

    record = _record(
        allowed_actions=["status", "push"],
        remotes=[
            RepoRemote.create(
                name="fuzzy",
                url="https://github.com/fuzzy123-ai/demo.git",
                purpose="fork",
                push_policy="push_allowed",
            )
        ],
    )

    assert "push" in record.allowed_actions


def test_registry_roundtrips_through_atomic_json(tmp_path: Path):
    registry = RepoRegistry()
    registry.add(
        _record(
            remotes=[
                RepoRemote.create(
                    name="fuzzy",
                    url="https://github.com/fuzzy123-ai/kundenportal.git",
                    purpose="fork",
                    push_policy="push_allowed",
                )
            ],
            allowed_actions=["status", "push"],
        )
    )

    path = tmp_path / "repo-registry.json"
    registry.save_json(path)
    loaded = RepoRegistry.load_json(path)
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert loaded.get("kundenportal-repo").remotes[0].name == "fuzzy"
    assert loaded.get("kundenportal-repo").allowed_actions == ("status", "push")


def test_registry_rejects_duplicate_repo_id_and_path():
    registry = RepoRegistry()
    registry.add(_record(repo_id="demo-one"))

    with pytest.raises(RepoRegistryError, match="repo already exists"):
        registry.add(_record(repo_id="demo-one", project_root="projects/kundenportal/other"))

    with pytest.raises(RepoRegistryError, match="repo path already registered"):
        registry.add(_record(repo_id="demo-two"))


def test_audit_summary_omits_paths_and_remote_urls():
    registry = RepoRegistry()
    registry.add(
        _record(
            remotes=[
                RepoRemote.create(
                    name="fuzzy",
                    url="https://github.com/fuzzy123-ai/kundenportal.git",
                    push_policy="push_allowed",
                )
            ]
        )
    )

    summary = registry.audit_summary()
    dumped = json.dumps(summary)

    assert summary["repo_count"] == 1
    assert summary["repos"][0]["push_remote_count"] == 1
    assert "projects/kundenportal" not in dumped
    assert "github.com" not in dumped


def test_source_uses_atomic_json_and_no_live_git_runtime():
    source = Path("src/repo_registry.py").read_text(encoding="utf-8")

    assert "atomic_write_json" in source
    forbidden = ("subprocess", "gitpython", "dulwich", "requests", "httpx", "paramiko", "podman", "docker")
    for fragment in forbidden:
        assert fragment not in source.lower()
