from dataclasses import dataclass
from pathlib import Path

import pytest

from src.odysseus_updater_preflight import build_odysseus_updater_preflight_report


@dataclass(frozen=True, slots=True)
class _WorktreeSnapshot:
    dirty: bool | None
    staged_files: tuple[str, ...]
    allowed_staged_files: tuple[str, ...]
    hotfile_conflict: bool | None = None


@dataclass(frozen=True, slots=True)
class _BranchSnapshot:
    current_branch: str | None
    expected_branch: str | None
    branch_candidates: tuple[str, ...]
    detached: bool | None
    ahead: int | None
    behind: int | None


def _base_worktree(**overrides):
    snapshot = {
        "dirty": False,
        "staged_files": ("src/odysseus_updater_preflight.py",),
        "allowed_staged_files": (
            "src/odysseus_updater_preflight.py",
            "tests/test_odysseus_updater_preflight.py",
        ),
        "hotfile_conflict": False,
    }
    snapshot.update(overrides)
    return snapshot


def _base_branch(**overrides):
    snapshot = {
        "current_branch": "codex/upd2-preflight-validator",
        "expected_branch": "codex/upd2-preflight-validator",
        "branch_candidates": ("codex/upd2-preflight-validator",),
        "detached": False,
        "ahead": 0,
        "behind": 0,
    }
    snapshot.update(overrides)
    return snapshot


def _base_env(**overrides):
    snapshot = {
        "required_names": (
            "ODYSSEUS_INTERNAL_TOKEN",
            "ODYSSEUS_UPDATE_CHANNEL",
        ),
        "present_names": (
            "ODYSSEUS_INTERNAL_TOKEN",
            "ODYSSEUS_UPDATE_CHANNEL",
            "UNRELATED_FLAG",
        ),
    }
    snapshot.update(overrides)
    return snapshot


def _base_backup(**overrides):
    snapshot = {"mount_ready": True}
    snapshot.update(overrides)
    return snapshot


def test_ready_report_accepts_dataclass_and_dict_snapshots():
    report = build_odysseus_updater_preflight_report(
        worktree_snapshot=_WorktreeSnapshot(
            dirty=False,
            staged_files=("src/odysseus_updater_preflight.py",),
            allowed_staged_files=(
                "src/odysseus_updater_preflight.py",
                "tests/test_odysseus_updater_preflight.py",
            ),
            hotfile_conflict=False,
        ),
        branch_snapshot=_BranchSnapshot(
            current_branch="codex/upd2-preflight-validator",
            expected_branch="codex/upd2-preflight-validator",
            branch_candidates=("codex/upd2-preflight-validator",),
            detached=False,
            ahead=0,
            behind=0,
        ),
        env_snapshot=_base_env(),
        backup_snapshot=_base_backup(),
    )

    assert report.status == "ready"
    assert report.blockers == ()
    assert report.to_dict()["env"]["missing_required_names"] == []


def test_dirty_worktree_and_ahead_branch_degrade_to_partial():
    report = build_odysseus_updater_preflight_report(
        worktree_snapshot=_base_worktree(dirty=True),
        branch_snapshot=_base_branch(ahead=2),
        env_snapshot=_base_env(),
        backup_snapshot=_base_backup(),
    )

    assert report.status == "partial"
    assert "worktree is dirty outside the staged updater slice" in report.reasons
    assert "branch is ahead of its tracked reference" in report.reasons
    assert report.blockers == ()


def test_foreign_staged_files_block_the_slice():
    report = build_odysseus_updater_preflight_report(
        worktree_snapshot=_base_worktree(
            staged_files=(
                "src/odysseus_updater_preflight.py",
                "src/unrelated_module.py",
            )
        ),
        branch_snapshot=_base_branch(),
        env_snapshot=_base_env(),
        backup_snapshot=_base_backup(),
    )

    assert report.status == "blocked"
    assert report.blockers == ("foreign staged files are mixed into the updater slice",)
    assert report.to_dict()["worktree"]["foreign_staged_files"] == ["src/unrelated_module.py"]


def test_hotfile_conflict_blocks_even_without_foreign_files():
    report = build_odysseus_updater_preflight_report(
        worktree_snapshot=_base_worktree(hotfile_conflict=True),
        branch_snapshot=_base_branch(),
        env_snapshot=_base_env(),
        backup_snapshot=_base_backup(),
    )

    assert report.status == "blocked"
    assert "hotfile conflict is present in the supplied worktree snapshot" in report.blockers


def test_branch_ambiguity_and_missing_snapshot_fields_defer():
    report = build_odysseus_updater_preflight_report(
        worktree_snapshot=_base_worktree(dirty=None),
        branch_snapshot=_base_branch(
            current_branch=None,
            branch_candidates=("main", "release"),
            ahead=None,
            behind=None,
        ),
        env_snapshot=_base_env(),
        backup_snapshot=_base_backup(mount_ready=None),
    )

    assert report.status == "deferred"
    assert "branch snapshot is ambiguous across multiple candidates" in report.reasons
    assert "ahead/behind counters are incomplete" in report.reasons
    assert "backup mount readiness was not supplied" in report.reasons


def test_behind_branch_missing_env_names_and_backup_gate_false_block():
    report = build_odysseus_updater_preflight_report(
        worktree_snapshot=_base_worktree(),
        branch_snapshot=_base_branch(behind=3),
        env_snapshot=_base_env(present_names=("ODYSSEUS_INTERNAL_TOKEN",)),
        backup_snapshot=_base_backup(mount_ready=False),
    )

    assert report.status == "blocked"
    assert "branch is behind its tracked reference" in report.blockers
    assert "required environment names are missing from the supplied snapshot" in report.blockers
    assert "backup gate is not ready in the supplied snapshot" in report.blockers


def test_expected_branch_mismatch_is_deferred():
    report = build_odysseus_updater_preflight_report(
        worktree_snapshot=_base_worktree(),
        branch_snapshot=_base_branch(
            current_branch="codex/wrong-slice",
            expected_branch="codex/upd2-preflight-validator",
        ),
        env_snapshot=_base_env(),
        backup_snapshot=_base_backup(),
    )

    assert report.status == "deferred"
    assert "current branch does not match the expected updater branch" in report.reasons


def test_source_stays_offline_and_does_not_pull_runtime_secrets():
    source = Path("src/odysseus_updater_preflight.py").read_text(encoding="utf-8")

    forbidden_fragments = (
        "import subprocess",
        "from subprocess",
        "import requests",
        "from requests",
        "import git",
        "from git",
        "import telegram",
        "from telegram",
        "import nextcloud",
        "from nextcloud",
        "os.system",
        ".run(",
        ".getenv(",
        "dotenv",
    )

    for fragment in forbidden_fragments:
        assert fragment not in source


def test_invalid_snapshot_shape_is_rejected():
    with pytest.raises(ValueError, match="worktree_snapshot must be a mapping or dataclass"):
        build_odysseus_updater_preflight_report(
            worktree_snapshot="not-a-snapshot",
            branch_snapshot=_base_branch(),
            env_snapshot=_base_env(),
            backup_snapshot=_base_backup(),
        )
