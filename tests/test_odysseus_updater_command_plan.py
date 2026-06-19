from pathlib import Path

import pytest

from src.odysseus_updater_command_plan import build_odysseus_updater_command_plan


@pytest.mark.parametrize(
    ("plan_type", "expected_command"),
    (
        ("git_fetch", "git fetch --all --tags --prune"),
        (
            "focused_pytest",
            "python -m pytest tests/test_odysseus_updater_command_plan.py -q",
        ),
        (
            "backup_preupdate",
            "backup-tool create --label preupdate --source <reviewed-worktree> --destination [redacted-backup-target]",
        ),
        (
            "podman_compose",
            "podman compose -f <redacted-compose-file> config",
        ),
        (
            "smoke_check",
            "python -m pytest tests/test_odysseus_updater_command_plan.py -q -k smoke",
        ),
        ("hold_note", None),
    ),
)
def test_plan_types_render_deterministic_dry_run_text(plan_type: str, expected_command: str | None):
    plan = build_odysseus_updater_command_plan(plan_type=plan_type)

    assert plan.plan_type == plan_type
    assert plan.dry_run_label == "plan_only"
    assert plan.to_dict() == build_odysseus_updater_command_plan(plan_type=plan_type).to_dict()

    rendered = plan.to_text()
    assert "operator review only" in rendered.lower()
    assert "explicit operator approval" in rendered.lower()
    if expected_command is None:
        assert "## Planned Commands" not in rendered
    else:
        assert expected_command in rendered


def test_redacts_sensitive_inputs_from_focus_and_notes():
    plan = build_odysseus_updater_command_plan(
        plan_type="podman_compose",
        focus_label=r"C:\Users\nkatz\secrets\compose.yaml",
        note="api_key=<redacted-test-sentinel> Bearer <redacted-test-sentinel> backup /srv/private-data https://internal.example.test/run",
    )

    rendered = plan.to_text()

    assert "[redacted-path]" in rendered
    assert "api_key=[redacted]" in rendered
    assert "Bearer [redacted]" in rendered
    assert "[redacted-url]" in rendered
    assert "redacted-test-sentinel" not in rendered
    assert "compose.yaml" not in rendered
    assert "/srv/private-data" not in rendered


def test_rejects_unsupported_plan_type():
    with pytest.raises(ValueError, match="unsupported plan_type"):
        build_odysseus_updater_command_plan(plan_type="deploy_now")


def test_hold_note_is_note_only_and_deterministic():
    plan = build_odysseus_updater_command_plan(
        plan_type="hold_note",
        note="pause until operator confirms focused pytest evidence",
    )

    assert plan.commands == ()
    assert plan.notes == (
        "hold this slice until operator review clears the next action",
        "pause until operator confirms focused pytest evidence",
    )
    assert "## Planned Commands" not in plan.to_text()


def test_module_source_stays_offline_and_runtime_free():
    source = Path("src/odysseus_updater_command_plan.py").read_text(encoding="utf-8")

    forbidden_fragments = (
        "import subprocess",
        "from subprocess",
        "import requests",
        "from requests",
        "import telegram",
        "from telegram",
        "import nextcloud",
        "from nextcloud",
        "import git",
        "from git",
        ".run(",
        "os.system",
    )

    for fragment in forbidden_fragments:
        assert fragment not in source
