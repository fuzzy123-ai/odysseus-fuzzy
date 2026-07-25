#!/usr/bin/env python3
"""Teach the deployed security reporter about verified auto-update context."""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from pathlib import Path


MARKER = "AUTO_UPDATE_UNIT = \"odysseus-auto-update.service\""

CONSTANT_ANCHOR = """KEY_PREFIXES = ("ssh-", "ecdsa-", "sk-")
"""
CONSTANT_REPLACEMENT = """KEY_PREFIXES = ("ssh-", "ecdsa-", "sk-")
AUTO_UPDATE_UNIT = "odysseus-auto-update.service"
AUTO_UPDATE_MACHINE = "homebase@"
AUTO_UPDATE_EXECUTABLE = "/home/homebase/.local/bin/odysseus-auto-update.sh"
"""

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
AUDIT_REPLACEMENT = """    auto_update_active = verified_auto_update_active()
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

STATUS_ANCHOR = """    print(f"security_watch_ok alerts={len(alerts)}")
"""
STATUS_REPLACEMENT = """    print(
        f"security_watch_ok alerts={len(alerts)} "
        f"maintenance_events={len(maintenance_events)}"
    )
"""


def patch_source(source: str) -> str:
    if MARKER in source:
        return source
    replacements = (
        (CONSTANT_ANCHOR, CONSTANT_REPLACEMENT),
        (FUNCTION_ANCHOR, FUNCTION_REPLACEMENT),
        (ALERTS_ANCHOR, ALERTS_REPLACEMENT),
        (AUDIT_ANCHOR, AUDIT_REPLACEMENT),
        (STATUS_ANCHOR, STATUS_REPLACEMENT),
    )
    patched = source
    for anchor, replacement in replacements:
        count = patched.count(anchor)
        if count != 1:
            raise RuntimeError(
                f"expected exactly one patch anchor, found {count}: {anchor[:60]!r}"
            )
        patched = patched.replace(anchor, replacement, 1)
    return patched


def write_atomic(target: Path, content: str) -> None:
    original_stat = target.stat()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
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
