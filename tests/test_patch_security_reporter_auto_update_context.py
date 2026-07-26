import datetime as dt
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest


SCRIPT = (
    Path(__file__).parents[1]
    / "ops"
    / "homeserver"
    / "patch-security-reporter-auto-update-context.py"
)


def _module():
    return runpy.run_path(str(SCRIPT), run_name="security_reporter_patcher")


def _fixture(module: dict) -> str:
    return "\n".join(
        [
            module["CONSTANT_ANCHOR"],
            "def run(argv, *, timeout=30):\n    raise NotImplementedError\n",
            module["FUNCTION_ANCHOR"],
            "    return None\n",
            "def watch():",
            module["ALERTS_ANCHOR"],
            "        item for item in []",
            "    ]",
            module["AUDIT_ANCHOR"],
            module["STATUS_ANCHOR"],
        ]
    )


def _v1_source(module: dict) -> str:
    source = _fixture(module)
    for anchor, replacement in module["V1_REPLACEMENTS"]:
        source = source.replace(anchor, replacement, 1)
    return source


def _systemd_result(*, active: bool = True, malformed: bool = False):
    if malformed:
        return SimpleNamespace(returncode=0, stdout="ExecMainPID=invalid\n")
    if not active:
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "ExecMainPID=95481\n"
                "ExecStart={ path=/home/homebase/.local/bin/"
                "odysseus-auto-update.sh ; }\n"
                "ActiveState=inactive\n"
                "SubState=dead\n"
            ),
        )
    return SimpleNamespace(
        returncode=0,
        stdout=(
            "ExecMainPID=42\n"
            "ExecStart={ path=/home/homebase/.local/bin/"
            "odysseus-auto-update.sh ; }\n"
            "ActiveState=activating\n"
            "SubState=start\n"
        ),
    )


def _run_watch(
    module: dict,
    *,
    audit_counts: dict[str, int],
    systemd_result,
) -> None:
    namespace: dict = {}
    patched = module["patch_source"](_fixture(module))
    exec(compile(patched, "<patched-reporter>", "exec"), namespace)
    namespace.update(
        audit_counts=audit_counts,
        dry_run=True,
        dt=dt,
        run=lambda _argv, *, timeout=10: systemd_result,
        send_telegram=lambda _message: pytest.fail(
            "dry-run reporter must not send"
        ),
    )
    namespace["watch"]()


def test_patch_is_complete_compilable_and_idempotent():
    module = _module()
    patched = module["patch_source"](_fixture(module))

    compile(patched, "<patched-reporter>", "exec")
    assert module["PATCH_VERSION_MARKER"] in patched
    assert module["MARKER"] in patched
    assert "def verified_auto_update_active()" in patched
    assert "Auto-Update-Kontext aktiv;" in patched
    assert "🔄 Debian-Wartungsmeldung" not in patched
    assert "erwartete Änderung" not in patched
    assert module["patch_source"](patched) == patched


def test_patch_upgrades_complete_v1_without_preserving_downgrade():
    module = _module()

    upgraded = module["patch_source"](_v1_source(module))

    assert module["PATCH_VERSION_MARKER"] in upgraded
    assert "Auto-Update-Kontext aktiv;" in upgraded
    assert "🔄 Debian-Wartungsmeldung" not in upgraded
    assert "erwartete Änderung" not in upgraded
    assert module["patch_source"](upgraded) == upgraded


def test_patch_upgrades_drifted_v1_by_removing_only_guard_continue():
    module = _module()
    drifted = _v1_source(module).replace(
        "Automatisches Odysseus-Update läuft; erwartete Änderung ",
        "Auto-Update-Kontext aktiv; Ereignis ",
        1,
    ).replace(
        "🔄 Debian-Wartungsmeldung",
        "🚨 Debian-Sicherheitsmeldung",
        1,
    )

    upgraded = module["patch_source"](drifted)
    tree = module["ast"].parse(upgraded)
    _watch, _loop, guard = module["_audit_contract"](tree)

    assert module["PATCH_VERSION_MARKER"] in upgraded
    assert not any(
        isinstance(item, module["ast"].Continue)
        for item in module["ast"].walk(guard)
    )
    assert module["patch_source"](upgraded) == upgraded


def test_patch_rejects_partial_legacy_marker_instead_of_attesting_complete():
    module = _module()
    partial = _fixture(module).replace(
        module["CONSTANT_ANCHOR"],
        module["V1_CONSTANT_REPLACEMENT"],
        1,
    )

    with pytest.raises(RuntimeError, match="incomplete or has drifted"):
        module["patch_source"](partial)


def test_patch_rejects_corrupted_v2_postcondition():
    module = _module()
    patched = module["patch_source"](_fixture(module))
    corrupted = patched.replace(
        "def verified_auto_update_active() -> bool:",
        "def incomplete_auto_update_check() -> bool:",
        1,
    )

    with pytest.raises(RuntimeError, match="structural validation"):
        module["patch_source"](corrupted)


def test_patch_fails_closed_when_expected_source_anchor_is_missing():
    module = _module()
    source = _fixture(module).replace(module["STATUS_ANCHOR"], "")

    with pytest.raises(RuntimeError, match="expected exactly one patch anchor"):
        module["patch_source"](source)


def test_active_auto_update_adds_context_but_keeps_security_alert(
    capsys,
):
    module = _module()

    _run_watch(
        module,
        audit_counts={"odysseus_app_env": 1},
        systemd_result=_systemd_result(active=True),
    )

    output = capsys.readouterr().out
    assert "🚨 Debian-Sicherheitsmeldung" in output
    assert "Auto-Update-Kontext aktiv" in output
    assert "Ereignis bleibt sicherheitsrelevant" in output
    assert "Audit-Ereignis odysseus_app_env: 1 Änderung(en)" in output
    assert "🔄 Debian-Wartungsmeldung" not in output
    assert "security_watch_ok alerts=1 maintenance_events=1" in output


@pytest.mark.parametrize(
    "systemd_result",
    [
        _systemd_result(active=False),
        _systemd_result(malformed=True),
        SimpleNamespace(returncode=1, stdout=""),
    ],
)
def test_unverified_auto_update_context_never_downgrades_alert(
    systemd_result,
    capsys,
):
    module = _module()

    _run_watch(
        module,
        audit_counts={"odysseus_app_env": 2},
        systemd_result=systemd_result,
    )

    output = capsys.readouterr().out
    assert "🚨 Debian-Sicherheitsmeldung" in output
    assert "Audit-Ereignis odysseus_app_env: 2 Änderung(en)" in output
    assert "Auto-Update-Kontext aktiv" not in output
    assert "security_watch_ok alerts=1 maintenance_events=0" in output


def test_active_context_keeps_additional_audit_findings_security_relevant(
    capsys,
):
    module = _module()

    _run_watch(
        module,
        audit_counts={"odysseus_app_env": 1, "unexpected_rule": 3},
        systemd_result=_systemd_result(active=True),
    )

    output = capsys.readouterr().out
    assert output.count("🚨 Debian-Sicherheitsmeldung") == 1
    assert "Audit-Ereignis odysseus_app_env: 1 Änderung(en)" in output
    assert "Audit-Ereignis unexpected_rule: 3 Änderung(en)" in output
    assert "security_watch_ok alerts=2 maintenance_events=1" in output
