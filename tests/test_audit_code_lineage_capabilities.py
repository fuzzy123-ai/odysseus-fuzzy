from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from scripts.audit_code_lineage_capabilities import (
    AUDIT_SCHEMA,
    CodeLineageAuditError,
    audit_code_lineage_capabilities,
    main,
    render_markdown,
)


ROOT = Path(__file__).resolve().parents[1]


def _synthetic_root(tmp_path):
    root = tmp_path / "repo"
    source = root / "src"
    source.mkdir(parents=True)
    (source / "repo_git_adapter.py").write_text(
        """
import subprocess
class RepoGitAdapter:
    def status(self): pass
    def current_branch(self): pass
    def log(self): pass
    def changed_paths(self): pass
    def diff_stat(self): pass
    def remotes(self): pass
    def snapshot(self): pass
def git_read_command_is_allowed(): pass
def run_git_read_subprocess_command():
    return subprocess.run([\"git\", \"status\"])
""".strip(),
        encoding="utf-8",
    )
    (source / "project_version_store.py").write_text(
        """
class ProjectVersionStore:
    def load_version(self): pass
    def verify_version(self): pass
    def iter_verified_versions(self): pass
""".strip(),
        encoding="utf-8",
    )
    (source / "project_forge_local.py").write_text(
        """
import subprocess
class LocalProjectForge:
    def store_commit(self): return subprocess.run([\"git\", \"cat-file\"])
    def verify_version(self): pass
""".strip(),
        encoding="utf-8",
    )
    (source / "repo_commit_runner.py").write_text(
        "import subprocess\ndef commit(): return subprocess.run(['git', 'commit'])\n",
        encoding="utf-8",
    )
    (source / "repo_push_runner.py").write_text(
        "import subprocess\ndef push(): return subprocess.run(['git', 'push'])\n",
        encoding="utf-8",
    )
    (source / "repo_registry.py").write_text("class RepoRegistry: pass\n", encoding="utf-8")
    return root


def test_current_repository_audit_is_go_for_clt01_contract_only():
    report = audit_code_lineage_capabilities(ROOT)

    assert report.schema == AUDIT_SCHEMA
    assert report.status == "go_clt_01_contract_only"
    assert report.missing_required_capabilities == ()
    assert report.source_file_count > 100
    assert report.git_commands_executed == 0
    assert report.subprocesses_executed == 0
    assert report.live_actions == 0
    assert report.default_user_language == "first observable in available history"
    assert report.audit_digest.startswith("sha256:")
    assert len(report.audit_digest) == 71


def test_audit_reuses_current_canonical_read_and_version_authorities():
    report = audit_code_lineage_capabilities(ROOT)
    capabilities = {(item.module, item.api): item for item in report.capabilities}

    assert capabilities[("src/repo_git_adapter.py", "RepoGitAdapter.log")].decision == "reuse"
    assert capabilities[("src/repo_git_adapter.py", "RepoGitAdapter.changed_paths")].capability == "working_tree_changed_paths"
    assert capabilities[("src/project_version_store.py", "ProjectVersionStore.verify_version")].decision == "reuse"
    assert capabilities[("src/project_forge_local.py", "LocalProjectForge.store_commit")].capability == "retain_existing_commit"


def test_missing_history_facts_are_extensions_not_new_git_paths():
    report = audit_code_lineage_capabilities(ROOT)
    decisions = {item.capability: item.decision for item in report.extension_decisions}

    assert decisions["bounded_revision_graph"] == "extend_repo_git_adapter_after_owner_handoff"
    assert decisions["historical_path_changes"] == "extend_repo_git_adapter_after_owner_handoff"
    assert decisions["blob_and_object_metadata"] == "extend_repo_git_adapter_after_owner_handoff"
    assert decisions["history_boundary_state"] == "extend_repo_git_adapter_after_owner_handoff"
    assert decisions["immutable_project_versions"] == "reuse_project_version_store"
    assert decisions["repo_identity_and_roots"] == "reuse_repo_registry"
    assert decisions["commit_or_push"] == "out_of_scope_existing_mutation_authorities_only"


def test_direct_process_boundaries_are_relative_and_classified():
    report = audit_code_lineage_capabilities(ROOT)

    assert report.direct_git_boundaries
    assert all(not Path(item.module).is_absolute() for item in report.direct_git_boundaries)
    by_module = {item.module: item for item in report.direct_git_boundaries}
    assert by_module["src/repo_git_adapter.py"].role == "canonical_read_adapter"
    assert by_module["src/project_forge_local.py"].role == "canonical_commit_retention_and_verification"
    assert by_module["src/repo_commit_runner.py"].role == "canonical_commit_mutation_boundary"
    assert all(
        item.decision in {"retain_existing_boundary", "route_to_canonical_adapter"}
        for item in report.direct_git_boundaries
    )


def test_audit_never_calls_subprocess_or_git(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("static audit attempted subprocess execution")

    monkeypatch.setattr(subprocess, "run", forbidden)
    report = audit_code_lineage_capabilities(ROOT)

    assert report.status == "go_clt_01_contract_only"
    assert report.git_commands_executed == 0


def test_time_confidence_uncertainty_and_privacy_language_is_frozen():
    report = audit_code_lineage_capabilities(ROOT)
    times = dict(report.time_semantics)
    confidence = dict(report.confidence_semantics)

    assert set(times) == {
        "first_seen_at",
        "history_first_observed_at",
        "authored_at",
        "committed_at",
        "indexed_at",
        "valid_from",
        "valid_to",
    }
    assert "never topology order" in times["authored_at"]
    assert confidence["semantic_candidate"] == "discovery_hint_never_accepted_alone"
    assert "shallow_history_means_earliest_reachable_not_creation" in report.uncertainty_rules
    assert "author_name_and_email_excluded_by_default" in report.privacy_rules
    assert "absolute_paths_source_bodies_and_raw_git_output_excluded" in report.privacy_rules


def test_current_and_historical_scopes_are_separate_and_bounded():
    report = audit_code_lineage_capabilities(ROOT)
    scopes = dict(report.query_scopes)

    assert set(scopes) == {"current", "historical", "deleted", "all_history"}
    assert "current USI snapshot" in scopes["current"]
    assert "bounded revision range" in scopes["historical"]
    assert "never an unbounded default" in scopes["all_history"]


def test_report_json_is_content_free_and_deterministic():
    first = audit_code_lineage_capabilities(ROOT)
    second = audit_code_lineage_capabilities(ROOT)
    payload = first.to_dict()
    rendered = json.dumps(payload, sort_keys=True)

    assert first.audit_digest == second.audit_digest
    assert str(ROOT) not in rendered
    assert "@example.com" not in rendered
    assert "git_commands_executed" in payload
    assert payload["git_commands_executed"] == 0
    assert payload["missing_required_capability_count"] == 0


def test_markdown_contains_decisions_without_raw_source_or_identity():
    markdown = render_markdown(audit_code_lineage_capabilities(ROOT))

    assert "# Code Lineage Capability Audit" in markdown
    assert "extend_repo_git_adapter_after_owner_handoff" in markdown
    assert "first observable in available history" in markdown
    assert str(ROOT) not in markdown
    assert "author@example.com" not in markdown


def test_cli_json_and_markdown_are_machine_readable(capsys):
    assert main(("--root", str(ROOT), "--format", "json")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == AUDIT_SCHEMA
    assert payload["status"] == "go_clt_01_contract_only"

    assert main(("--root", str(ROOT), "--format", "markdown")) == 0
    assert capsys.readouterr().out.startswith("# Code Lineage Capability Audit\n")


def test_synthetic_uncatalogued_git_subprocess_is_flagged_for_routing(tmp_path):
    root = _synthetic_root(tmp_path)
    (root / "src" / "new_history_reader.py").write_text(
        "import subprocess\ndef scan(): return subprocess.run(['git', 'log', '--all'])\n",
        encoding="utf-8",
    )
    report = audit_code_lineage_capabilities(root)

    candidate = next(
        item for item in report.direct_git_boundaries if item.module == "src/new_history_reader.py"
    )
    assert candidate.function == "scan"
    assert candidate.role == "uncatalogued_direct_git_process_boundary"
    assert candidate.decision == "route_to_canonical_adapter"
    assert report.duplicate_review_boundaries == (candidate,)


def test_unrelated_subprocess_is_not_mislabeled_from_module_git_text(tmp_path):
    root = _synthetic_root(tmp_path)
    (root / "src" / "not_git.py").write_text(
        '"""This module mentions git only in documentation."""\n'
        "import subprocess\n"
        "def render():\n"
        "    command = ['tesseract', '--version']\n"
        "    return subprocess.run(command)\n",
        encoding="utf-8",
    )
    report = audit_code_lineage_capabilities(root)

    assert all(item.module != "src/not_git.py" for item in report.direct_git_boundaries)


def test_missing_required_api_blocks_next_contract(tmp_path):
    root = _synthetic_root(tmp_path)
    path = root / "src" / "repo_git_adapter.py"
    path.write_text(
        path.read_text(encoding="utf-8").replace("    def log(self): pass\n", ""),
        encoding="utf-8",
    )
    report = audit_code_lineage_capabilities(root)

    assert report.status == "blocked_missing_canonical_api"
    missing = {(item.module, item.api) for item in report.missing_required_capabilities}
    assert ("src/repo_git_adapter.py", "RepoGitAdapter.log") in missing


def test_missing_canonical_module_fails_closed(tmp_path):
    root = _synthetic_root(tmp_path)
    (root / "src" / "repo_registry.py").unlink()

    with pytest.raises(CodeLineageAuditError, match="required canonical modules"):
        audit_code_lineage_capabilities(root)


def test_oversized_source_file_fails_closed(tmp_path):
    root = _synthetic_root(tmp_path)
    (root / "src" / "oversized.py").write_text("x" * (2 * 1024 * 1024 + 1), encoding="utf-8")

    with pytest.raises(CodeLineageAuditError, match="size bound"):
        audit_code_lineage_capabilities(root)
