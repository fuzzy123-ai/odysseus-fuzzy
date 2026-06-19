from src.gamedev_project_profile import (
    GODOT_WRITE_EXTENSIONS,
    build_gamedev_command_plan,
    decide_gamedev_command_intent,
    godot_mount_profile,
    is_broad_host_root,
    validate_gamedev_mount_profile,
)


def test_godot_mount_profile_includes_required_extensions_and_backup():
    profile = godot_mount_profile(name="Canyon", host_path=r"E:\Canyoning", owner="fuzzy")

    assert profile["virtual_path"] == "/mnt/canyon-racer"
    assert profile["owner"] == "fuzzy"
    assert profile["write_policy"]["backup"] is True
    for ext in (".gd", ".tscn", ".tres", ".godot", ".gdshader"):
        assert ext in profile["write_policy"]["allowed_extensions"]


def test_validate_gamedev_mount_profile_accepts_safe_profile():
    profile = godot_mount_profile(name="Canyon", host_path=r"E:\Canyoning")

    result = validate_gamedev_mount_profile(profile)

    assert result.ok is True
    assert result.reasons == ()


def test_validate_gamedev_mount_profile_reports_missing_godot_extensions():
    profile = godot_mount_profile(name="Canyon", host_path=r"E:\Canyoning")
    profile["write_policy"]["allowed_extensions"] = [".txt", ".md"]

    result = validate_gamedev_mount_profile(profile)

    assert result.ok is False
    assert ".gd" in result.missing_extensions
    assert set(result.missing_extensions).issubset(set(GODOT_WRITE_EXTENSIONS))
    assert "missing_godot_extensions" in result.reasons


def test_validate_gamedev_mount_profile_rejects_shell_like_tools():
    profile = godot_mount_profile(name="Canyon", host_path=r"E:\Canyoning")
    profile["allowed_tools"].append("powershell")

    result = validate_gamedev_mount_profile(profile)

    assert result.ok is False
    assert result.shell_like_tools == ("powershell",)
    assert "shell_like_tools_not_allowed" in result.reasons


def test_validate_gamedev_mount_profile_requires_backup_for_writable_mounts():
    profile = godot_mount_profile(name="Canyon", host_path=r"E:\Canyoning")
    profile["write_policy"]["backup"] = False

    result = validate_gamedev_mount_profile(profile)

    assert result.ok is False
    assert result.backup_disabled is True
    assert "write_policy_backup_disabled" in result.reasons


def test_broad_host_roots_are_not_valid_project_mounts():
    assert is_broad_host_root(r"E:\\") is True
    assert is_broad_host_root("/") is True
    assert is_broad_host_root(r"E:\Canyoning") is False


def test_gamedev_command_gate_accepts_named_readonly_intent():
    decision = decide_gamedev_command_intent("inspect_project")

    assert decision.allowed is True
    assert decision.risk == "read_only"
    assert decision.reason == "allowed_named_intent"


def test_gamedev_command_gate_rejects_freeform_shell():
    decision = decide_gamedev_command_intent("powershell -Command dir")

    assert decision.allowed is False
    assert decision.reason == "unknown_command_intent"


def test_gamedev_command_gate_requires_operator_go_for_export():
    blocked = decide_gamedev_command_intent("godot_export")
    allowed = decide_gamedev_command_intent("godot_export", operator_go=True)

    assert blocked.allowed is False
    assert blocked.operator_go_required is True
    assert allowed.allowed is True
    assert allowed.operator_go_required is True


def test_gamedev_command_plan_uses_configured_argv_only():
    plan = build_gamedev_command_plan(
        "godot_lint",
        {"godot_lint": ["godot", "--headless", "--path", "/mnt/canyon-racer/canyon-race", "--quit"]},
    )

    assert plan.allowed is True
    assert plan.argv[0] == "godot"
    assert plan.reason == "allowed_named_command_plan"


def test_gamedev_command_plan_rejects_shell_like_executables():
    plan = build_gamedev_command_plan(
        "godot_lint",
        {"godot_lint": ["powershell", "-Command", "godot --version"]},
    )

    assert plan.allowed is False
    assert plan.reason == "shell_like_executable_not_allowed"


def test_gamedev_command_plan_rejects_unconfigured_string_commands():
    plan = build_gamedev_command_plan("godot_lint", {"godot_lint": "godot --version"})  # type: ignore[arg-type]

    assert plan.allowed is False
    assert plan.reason == "command_not_configured_as_argv"


def test_gamedev_command_plan_requires_virtual_mount_cwd():
    plan = build_gamedev_command_plan("inspect_project", {"inspect_project": ["godot", "--version"]}, cwd_virtual_path=r"E:\Canyoning")

    assert plan.allowed is False
    assert plan.reason == "cwd_must_be_virtual_mount"
