"""Run the deterministic offline TTD-09 Telegram Todo incident suite."""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "docs" / "plans" / "telegram-todo-incident-regression-manifest.json"
MANIFEST_KIND = "odysseus.telegram_todo_incident_regression_manifest"
_NODE_ID_RE = re.compile(r"^tests/[A-Za-z0-9_./-]+\.py::test_[A-Za-z0-9_]+$")
_CREDENTIAL_ENV_NAMES = {
    "OPENAI_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_ALLOWED_CHAT_IDS",
    "NEXTCLOUD_PASSWORD",
    "NEXTCLOUD_TOKEN",
}
_DISABLED_LIVE_FLAGS = {
    "TELEGRAM_REPLY_ENABLED": "false",
    "TELEGRAM_POLLING_ENABLED": "false",
    "TELEGRAM_WEBHOOK_ENABLED": "false",
    "TELEGRAM_SESSION_ROLLOVER_ENABLED": "false",
    "ODYSSEUS_NETWORK_DISABLED": "true",
}


class IncidentManifestError(ValueError):
    """Raised when the TTD-09 manifest is unsafe or incomplete."""


def load_manifest(path: Path | str = DEFAULT_MANIFEST) -> dict[str, Any]:
    manifest_path = Path(path).resolve()
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise IncidentManifestError(f"cannot read incident manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise IncidentManifestError("incident manifest must be a JSON object")
    return data


def validate_manifest(
    manifest: Mapping[str, Any], *, root: Path = ROOT
) -> tuple[str, ...]:
    if manifest.get("schema_version") != 1 or manifest.get("kind") != MANIFEST_KIND:
        raise IncidentManifestError("unsupported incident manifest schema")
    execution = manifest.get("execution")
    if not isinstance(execution, Mapping):
        raise IncidentManifestError("execution policy is required")
    if execution.get("network") != "forbidden":
        raise IncidentManifestError("incident suite must forbid network access")
    if execution.get("production_data") != "forbidden":
        raise IncidentManifestError("incident suite must forbid production data")
    if execution.get("live_actions") is not False:
        raise IncidentManifestError("incident suite must keep live actions disabled")

    cases = manifest.get("required_cases")
    if not isinstance(cases, list) or not cases:
        raise IncidentManifestError("required_cases must be a non-empty list")
    seen_case_ids: set[str] = set()
    nodeids: list[str] = []
    seen_nodeids: set[str] = set()
    parsed_tests: dict[Path, set[str]] = {}
    resolved_root = root.resolve()
    for case in cases:
        if not isinstance(case, Mapping):
            raise IncidentManifestError("each incident case must be an object")
        case_id = str(case.get("id") or "")
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", case_id):
            raise IncidentManifestError(f"unsafe incident case id: {case_id!r}")
        if case_id in seen_case_ids:
            raise IncidentManifestError(f"duplicate incident case id: {case_id}")
        seen_case_ids.add(case_id)
        targets = case.get("nodeids")
        if not isinstance(targets, list) or not targets:
            raise IncidentManifestError(f"incident case has no nodeids: {case_id}")
        for raw_nodeid in targets:
            nodeid = str(raw_nodeid or "").replace("\\", "/")
            if not _NODE_ID_RE.fullmatch(nodeid):
                raise IncidentManifestError(f"unsafe pytest nodeid: {nodeid!r}")
            relative_path, test_name = nodeid.split("::", 1)
            if ".." in Path(relative_path).parts:
                raise IncidentManifestError(f"pytest node escapes tests root: {nodeid!r}")
            test_path = (resolved_root / relative_path).resolve()
            if resolved_root not in test_path.parents or not test_path.is_file():
                raise IncidentManifestError(f"pytest node file is missing: {relative_path}")
            if test_path not in parsed_tests:
                try:
                    module = ast.parse(test_path.read_text(encoding="utf-8"), filename=str(test_path))
                except (OSError, SyntaxError) as exc:
                    raise IncidentManifestError(f"cannot parse pytest node file: {relative_path}") from exc
                parsed_tests[test_path] = {
                    item.name
                    for item in module.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                }
            if test_name not in parsed_tests[test_path]:
                raise IncidentManifestError(f"pytest node is missing: {nodeid}")
            if nodeid not in seen_nodeids:
                seen_nodeids.add(nodeid)
                nodeids.append(nodeid)
    return tuple(nodeids)


def offline_test_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    environment = dict(source or os.environ)
    for name in _CREDENTIAL_ENV_NAMES:
        environment.pop(name, None)
    environment.update(_DISABLED_LIVE_FLAGS)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def run_manifest_suite(
    manifest_path: Path | str = DEFAULT_MANIFEST,
    *,
    basetemp: Path | str | None = None,
) -> int:
    manifest = load_manifest(manifest_path)
    nodeids = validate_manifest(manifest)
    summary = {
        "schema": "odysseus.telegram_todo_incident_regression_run.v1",
        "suite_id": str(manifest.get("suite_id") or "TTD-09"),
        "case_count": len(manifest["required_cases"]),
        "nodeid_count": len(nodeids),
        "network": "forbidden",
        "production_data": "forbidden",
        "live_actions": False,
    }
    print(json.dumps(summary, sort_keys=True))

    if basetemp is not None:
        target = Path(basetemp).resolve()
        if target.exists():
            raise IncidentManifestError("explicit --basetemp must not already exist")
        if ROOT == target or ROOT in target.parents:
            raise IncidentManifestError("explicit --basetemp must be outside the repository")
        if not target.parent.is_dir():
            raise IncidentManifestError("explicit --basetemp parent must already exist")
        return _run_pytest(nodeids, target)
    with tempfile.TemporaryDirectory(prefix="odysseus-ttd09-") as temporary:
        return _run_pytest(nodeids, Path(temporary))


def _run_pytest(nodeids: Sequence[str], basetemp: Path) -> int:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        *nodeids,
        "--basetemp",
        str(basetemp),
    ]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=offline_test_environment(),
        check=False,
    )
    return int(completed.returncode)


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate or run the offline TTD-09 incident regression manifest."
    )
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--basetemp")
    parser.add_argument("--validate-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        nodeids = validate_manifest(manifest)
        if args.validate_only:
            print(json.dumps({
                "schema": "odysseus.telegram_todo_incident_regression_validation.v1",
                "case_count": len(manifest["required_cases"]),
                "nodeid_count": len(nodeids),
                "valid": True,
            }, sort_keys=True))
            return 0
        return run_manifest_suite(args.manifest, basetemp=args.basetemp)
    except IncidentManifestError as exc:
        print(f"TTD-09 manifest error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
