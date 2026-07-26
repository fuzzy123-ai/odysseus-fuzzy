#!/usr/bin/env python3
"""Teach the deployed security reporter about verified auto-update context."""

from __future__ import annotations

import argparse
import ast
import os
import shutil
import tempfile
from pathlib import Path


MARKER = 'AUTO_UPDATE_UNIT = "odysseus-auto-update.service"'
PATCH_VERSION_MARKER = "AUTO_UPDATE_CONTEXT_PATCH_VERSION = 2"

CONSTANT_ANCHOR = """KEY_PREFIXES = ("ssh-", "ecdsa-", "sk-")
"""
V1_CONSTANT_REPLACEMENT = """KEY_PREFIXES = ("ssh-", "ecdsa-", "sk-")
AUTO_UPDATE_UNIT = "odysseus-auto-update.service"
AUTO_UPDATE_MACHINE = "homebase@"
AUTO_UPDATE_EXECUTABLE = "/home/homebase/.local/bin/odysseus-auto-update.sh"
"""
CONSTANT_REPLACEMENT = (
    V1_CONSTANT_REPLACEMENT + "AUTO_UPDATE_CONTEXT_PATCH_VERSION = 2\n"
)

FUNCTION_ANCHOR = """
def telegram_target(values: dict[str, str]) -> str | None:
"""
FUNCTION_REPLACEMENT = """
def verified_auto_update_active() -> bool:
    result = run(
        [
            "systemctl",
            "--user",
            f"--machine={AUTO_UPDATE_MACHINE}",
            "show",
            AUTO_UPDATE_UNIT,
            "--property=ActiveState",
            "--property=SubState",
            "--property=ExecMainPID",
            "--property=ExecStart",
        ],
        timeout=10,
    )
    if result.returncode != 0:
        return False
    properties = {}
    for line in result.stdout.splitlines():
        name, separator, value = line.partition("=")
        if separator:
            properties[name] = value
    try:
        main_pid = int(properties.get("ExecMainPID", "0"))
    except ValueError:
        return False
    return (
        properties.get("ActiveState") in {"active", "activating"}
        and properties.get("SubState") in {"start", "running", "exited"}
        and main_pid > 0
        and f"path={AUTO_UPDATE_EXECUTABLE}" in properties.get("ExecStart", "")
    )


def telegram_target(values: dict[str, str]) -> str | None:
"""

ALERTS_ANCHOR = """    alerts: list[str] = []
    firewall_events = [
"""
ALERTS_REPLACEMENT = """    alerts: list[str] = []
    maintenance_events: list[str] = []
    firewall_events = [
"""

AUDIT_ANCHOR = """    for key_name, count in sorted(audit_counts.items()):
        if key_name == "odysseus_privileged_exec":
            continue
        alerts.append(f"Audit-Ereignis {key_name}: {count} Änderung(en)")

    if alerts:
        timestamp = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
        message = "🚨 Debian-Sicherheitsmeldung\\n" + "\\n".join(f"- {item}" for item in alerts)
        message += f"\\nZeit: {timestamp}\\nKeine automatische Gegenmaßnahme ausgeführt."
        if dry_run:
            print(message)
        elif not send_telegram(message):
            print("telegram_delivery_failed", file=sys.stderr)
            return 1
"""
V1_AUDIT_REPLACEMENT = """    auto_update_active = verified_auto_update_active()
    for key_name, count in sorted(audit_counts.items()):
        if key_name == "odysseus_privileged_exec":
            continue
        if key_name == "odysseus_app_env" and auto_update_active:
            maintenance_events.append(
                f"Automatisches Odysseus-Update läuft; erwartete Änderung "
                f"an {key_name}: {count}"
            )
            continue
        alerts.append(f"Audit-Ereignis {key_name}: {count} Änderung(en)")

    if alerts or maintenance_events:
        timestamp = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
        items = maintenance_events + alerts
        if alerts:
            message = "🚨 Debian-Sicherheitsmeldung\\n"
            footer = "Keine automatische Gegenmaßnahme ausgeführt."
        else:
            message = "🔄 Debian-Wartungsmeldung\\n"
            footer = "Sicherheitsmonitor aktiv; keine Gegenmaßnahme erforderlich."
        message += "\\n".join(f"- {item}" for item in items)
        message += f"\\nZeit: {timestamp}\\n{footer}"
        if dry_run:
            print(message)
        elif not send_telegram(message):
            print("telegram_delivery_failed", file=sys.stderr)
            return 1
"""
AUDIT_REPLACEMENT = """    auto_update_active = verified_auto_update_active()
    for key_name, count in sorted(audit_counts.items()):
        if key_name == "odysseus_privileged_exec":
            continue
        if key_name == "odysseus_app_env" and auto_update_active:
            maintenance_events.append(
                f"Auto-Update-Kontext aktiv; {key_name}-Ereignis bleibt "
                f"sicherheitsrelevant: {count}"
            )
        alerts.append(f"Audit-Ereignis {key_name}: {count} Änderung(en)")

    if alerts:
        timestamp = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
        items = maintenance_events + alerts
        message = "🚨 Debian-Sicherheitsmeldung\\n"
        message += "\\n".join(f"- {item}" for item in items)
        message += (
            f"\\nZeit: {timestamp}"
            "\\nKeine automatische Gegenmaßnahme ausgeführt."
        )
        if dry_run:
            print(message)
        elif not send_telegram(message):
            print("telegram_delivery_failed", file=sys.stderr)
            return 1
"""

STATUS_ANCHOR = """    print(f"security_watch_ok alerts={len(alerts)}")
"""
STATUS_REPLACEMENT = """    print(
        f"security_watch_ok alerts={len(alerts)} "
        f"maintenance_events={len(maintenance_events)}"
    )
"""

V1_REPLACEMENTS = (
    (CONSTANT_ANCHOR, V1_CONSTANT_REPLACEMENT),
    (FUNCTION_ANCHOR, FUNCTION_REPLACEMENT),
    (ALERTS_ANCHOR, ALERTS_REPLACEMENT),
    (AUDIT_ANCHOR, V1_AUDIT_REPLACEMENT),
    (STATUS_ANCHOR, STATUS_REPLACEMENT),
)
PATCH_REPLACEMENTS = (
    (CONSTANT_ANCHOR, CONSTANT_REPLACEMENT),
    (FUNCTION_ANCHOR, FUNCTION_REPLACEMENT),
    (ALERTS_ANCHOR, ALERTS_REPLACEMENT),
    (AUDIT_ANCHOR, AUDIT_REPLACEMENT),
    (STATUS_ANCHOR, STATUS_REPLACEMENT),
)
REQUIRED_POSTCONDITIONS = (
    PATCH_VERSION_MARKER,
    MARKER,
    "def verified_auto_update_active() -> bool:",
    "maintenance_events: list[str] = []",
    "auto_update_active = verified_auto_update_active()",
    'if key_name == "odysseus_app_env" and auto_update_active:',
    "maintenance_events={len(maintenance_events)}",
)
FORBIDDEN_POSTCONDITIONS = (
    "🔄 Debian-Wartungsmeldung",
    "erwartete Änderung",
)


def _replace_exact(
    source: str, replacements: tuple[tuple[str, str], ...]
) -> str:
    patched = source
    for anchor, replacement in replacements:
        count = patched.count(anchor)
        if count != 1:
            raise RuntimeError(
                f"expected exactly one patch anchor, found {count}: {anchor[:60]!r}"
            )
        patched = patched.replace(anchor, replacement, 1)
    return patched


def _audit_contract(
    tree: ast.Module,
) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef, ast.For, ast.If]:
    watch_functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "watch"
    ]
    if len(watch_functions) != 1:
        raise RuntimeError(
            "patched reporter failed audit control-flow validation: "
            "expected one watch function"
        )
    watch = watch_functions[0]
    audit_loops = []
    for node in ast.walk(watch):
        if not isinstance(node, ast.For):
            continue
        iterator_names = {
            item.id
            for item in ast.walk(node.iter)
            if isinstance(item, ast.Name)
        }
        target_names = {
            item.id
            for item in ast.walk(node.target)
            if isinstance(item, ast.Name)
        }
        if "audit_counts" in iterator_names and {
            "key_name",
            "count",
        } <= target_names:
            audit_loops.append(node)
    if len(audit_loops) != 1:
        raise RuntimeError(
            "patched reporter failed audit control-flow validation: "
            "expected one audit-count loop"
        )
    audit_loop = audit_loops[0]
    guards = []
    for statement in audit_loop.body:
        if not isinstance(statement, ast.If):
            continue
        test_names = {
            item.id
            for item in ast.walk(statement.test)
            if isinstance(item, ast.Name)
        }
        test_constants = {
            item.value
            for item in ast.walk(statement.test)
            if isinstance(item, ast.Constant)
        }
        if {"key_name", "auto_update_active"} <= test_names and (
            "odysseus_app_env" in test_constants
        ):
            guards.append(statement)
    if len(guards) != 1:
        raise RuntimeError(
            "patched reporter failed audit control-flow validation: "
            "expected one auto-update context guard"
        )
    return watch, audit_loop, guards[0]


def _is_append_statement(statement: ast.stmt, target: str) -> bool:
    return (
        isinstance(statement, ast.Expr)
        and isinstance(statement.value, ast.Call)
        and isinstance(statement.value.func, ast.Attribute)
        and statement.value.func.attr == "append"
        and isinstance(statement.value.func.value, ast.Name)
        and statement.value.func.value.id == target
    )


def _validate_audit_control_flow(tree: ast.Module) -> None:
    watch, audit_loop, context_guard = _audit_contract(tree)
    if any(
        isinstance(item, ast.Continue) for item in ast.walk(context_guard)
    ):
        raise RuntimeError(
            "patched reporter failed audit control-flow validation: "
            "auto-update context still skips the security alert"
        )
    if not any(
        isinstance(item, ast.Call)
        and isinstance(item.func, ast.Attribute)
        and item.func.attr == "append"
        and isinstance(item.func.value, ast.Name)
        and item.func.value.id == "maintenance_events"
        for item in ast.walk(context_guard)
    ):
        raise RuntimeError(
            "patched reporter failed audit control-flow validation: "
            "maintenance context append is missing"
        )
    guard_index = audit_loop.body.index(context_guard)
    if not any(
        _is_append_statement(statement, "alerts")
        for statement in audit_loop.body[guard_index + 1 :]
    ):
        raise RuntimeError(
            "patched reporter failed audit control-flow validation: "
            "security alert append is not unconditional after context"
        )
    message_literals = {
        item.value
        for item in ast.walk(watch)
        if isinstance(item, ast.Constant) and isinstance(item.value, str)
    }
    if not any(
        "Debian-Sicherheitsmeldung" in value
        for value in message_literals
    ):
        raise RuntimeError(
            "patched reporter failed audit control-flow validation: "
            "security notification header is missing"
        )


def _validate_patched_source(source: str) -> str:
    for snippet in REQUIRED_POSTCONDITIONS:
        count = source.count(snippet)
        if count != 1:
            raise RuntimeError(
                "patched reporter failed structural validation: "
                f"expected one required postcondition, found {count}"
            )
    for snippet in FORBIDDEN_POSTCONDITIONS:
        if snippet in source:
            raise RuntimeError(
                "patched reporter failed structural validation: "
                "legacy downgrade path remains"
            )
    try:
        tree = ast.parse(source, filename="<odysseus-security-reporter>")
    except (SyntaxError, ValueError, TypeError) as exc:
        raise RuntimeError(
            "patched reporter failed Python syntax validation"
        ) from exc
    _validate_audit_control_flow(tree)
    return source


def _remove_context_guard_continue(source: str) -> str:
    try:
        tree = ast.parse(source, filename="<odysseus-security-reporter>")
    except (SyntaxError, ValueError, TypeError) as exc:
        raise RuntimeError(
            "legacy auto-update patch failed Python syntax validation"
        ) from exc
    _watch, _audit_loop, context_guard = _audit_contract(tree)
    all_continues = [
        item
        for item in ast.walk(context_guard)
        if isinstance(item, ast.Continue)
    ]
    direct_continues = [
        item
        for item in context_guard.body
        if isinstance(item, ast.Continue)
    ]
    if not all_continues:
        return source
    if len(all_continues) != 1 or len(direct_continues) != 1:
        raise RuntimeError(
            "legacy auto-update patch has ambiguous continue control flow"
        )
    continuation = direct_continues[0]
    if continuation.end_lineno is None:
        raise RuntimeError(
            "legacy auto-update patch has no bounded continue span"
        )
    lines = source.splitlines(keepends=True)
    del lines[continuation.lineno - 1 : continuation.end_lineno]
    return "".join(lines)


def _upgrade_v1_source(source: str) -> str:
    required_v1 = (
        V1_CONSTANT_REPLACEMENT,
        FUNCTION_REPLACEMENT,
        ALERTS_REPLACEMENT,
        V1_AUDIT_REPLACEMENT,
        STATUS_REPLACEMENT,
    )
    if all(source.count(snippet) == 1 for snippet in required_v1):
        upgraded = source.replace(
            V1_CONSTANT_REPLACEMENT, CONSTANT_REPLACEMENT, 1
        )
        upgraded = upgraded.replace(
            V1_AUDIT_REPLACEMENT, AUDIT_REPLACEMENT, 1
        )
        return _validate_patched_source(upgraded)
    if source.count(V1_CONSTANT_REPLACEMENT) != 1:
        raise RuntimeError(
            "legacy auto-update patch is incomplete or has drifted"
        )
    if any(
        snippet in source for snippet in FORBIDDEN_POSTCONDITIONS
    ):
        raise RuntimeError(
            "legacy auto-update patch contains an unknown downgrade path"
        )
    try:
        upgraded = _remove_context_guard_continue(source)
    except RuntimeError as exc:
        raise RuntimeError(
            "legacy auto-update patch is incomplete or has drifted"
        ) from exc
    upgraded = upgraded.replace(
        V1_CONSTANT_REPLACEMENT, CONSTANT_REPLACEMENT, 1
    )
    return _validate_patched_source(upgraded)


def patch_source(source: str) -> str:
    if PATCH_VERSION_MARKER in source:
        return _validate_patched_source(source)
    if MARKER in source:
        return _upgrade_v1_source(source)
    return _validate_patched_source(
        _replace_exact(source, PATCH_REPLACEMENTS)
    )


def write_atomic(target: Path, content: str) -> None:
    original_stat = target.stat()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(
            descriptor, "w", encoding="utf-8", newline="\n"
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, original_stat.st_mode)
        os.chown(temporary, original_stat.st_uid, original_stat.st_gid)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        type=Path,
        default=Path("/usr/local/sbin/odysseus-security-reporter"),
    )
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    source = args.target.read_text(encoding="utf-8")
    patched = patch_source(source)
    if args.check:
        print("already_patched" if patched == source else "patch_ready")
        return 0
    if patched == source:
        print("already_patched")
        return 0
    if args.backup:
        if args.backup.exists():
            raise RuntimeError(f"backup already exists: {args.backup}")
        shutil.copy2(args.target, args.backup)
    write_atomic(args.target, patched)
    print("patched")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
