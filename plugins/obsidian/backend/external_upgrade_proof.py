import json
import os
import re
import tempfile
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .import_export import export_vault, import_vault
from .rebuild_proof import run_rebuild_proof


REQUIRED_PLUGIN_ROOT_ENTRIES = ("plugin.py", "plugin.json", "README.md", "frontend", "backend")
FORBIDDEN_PLUGIN_ROOT_ENTRIES = ("__pycache__", ".obsidian", ".pytest_cache")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _plugin_root(plugin_dir: str) -> str:
    return os.path.abspath(plugin_dir)


def _plugin_json_version(plugin_dir: str) -> str:
    path = os.path.join(_plugin_root(plugin_dir), "plugin.json")
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return str(payload.get("version") or "")


def _plugin_py_version(plugin_dir: str) -> str:
    path = os.path.join(_plugin_root(plugin_dir), "plugin.py")
    with open(path, "r", encoding="utf-8") as handle:
        content = handle.read()
    match = re.search(r'"version"\s*:\s*"([^"]+)"', content)
    return match.group(1) if match else ""


def collect_distribution_layout(plugin_dir: str, *, ignore_runtime_artifacts: bool = False) -> Dict[str, Any]:
    root = _plugin_root(plugin_dir)
    present = set(os.listdir(root))
    missing = [name for name in REQUIRED_PLUGIN_ROOT_ENTRIES if name not in present]
    forbidden_present = [name for name in FORBIDDEN_PLUGIN_ROOT_ENTRIES if name in present]
    effective_forbidden = [
        name for name in forbidden_present
        if not (ignore_runtime_artifacts and name == "__pycache__")
    ]
    return {
        "plugin_root": root,
        "required_entries": list(REQUIRED_PLUGIN_ROOT_ENTRIES),
        "missing_entries": missing,
        "forbidden_entries": forbidden_present,
        "ignored_runtime_artifacts": [name for name in forbidden_present if name not in effective_forbidden],
        "ready": not missing and not effective_forbidden,
    }


def collect_version_sync(plugin_dir: str) -> Dict[str, Any]:
    plugin_json_version = _plugin_json_version(plugin_dir)
    plugin_py_version = _plugin_py_version(plugin_dir)
    return {
        "plugin_json_version": plugin_json_version,
        "plugin_py_version": plugin_py_version,
        "match": bool(plugin_json_version and plugin_json_version == plugin_py_version),
    }


def collect_external_upgrade_proof(
    plugin_dir: str,
    vault_dir: str,
    *,
    query: Optional[str] = None,
    top_k: int = 5,
    path_prefix: str = "",
    export_password: str = "external-upgrade-proof",
) -> Dict[str, Any]:
    version_sync = collect_version_sync(plugin_dir)
    distribution_layout = collect_distribution_layout(plugin_dir, ignore_runtime_artifacts=True)

    plain_archive = export_vault(vault_dir)
    with tempfile.TemporaryDirectory() as plain_dst:
        plain_import = import_vault(plain_dst, plain_archive.data)
        plain_rebuild = run_rebuild_proof(
            plain_dst,
            query=query,
            top_k=top_k,
            path_prefix=path_prefix,
        )

    encrypted_archive = export_vault(vault_dir, password=export_password)
    with tempfile.TemporaryDirectory() as encrypted_dst:
        encrypted_import = import_vault(encrypted_dst, encrypted_archive.data, password=export_password)
        encrypted_rebuild = run_rebuild_proof(
            encrypted_dst,
            query=query,
            top_k=top_k,
            path_prefix=path_prefix,
        )

    summary = {
        "version_sync": version_sync["match"],
        "distribution_layout": distribution_layout["ready"],
        "plain_export_import": int(plain_import.get("imported_files") or 0) > 0,
        "encrypted_export_import": int(encrypted_import.get("imported_files") or 0) > 0,
        "plain_rebuild_query_ready": bool((plain_rebuild.get("summary") or {}).get("query_layer_ready", False)),
        "encrypted_rebuild_query_ready": bool((encrypted_rebuild.get("summary") or {}).get("query_layer_ready", False)),
        "plain_query_citations": int((plain_rebuild.get("summary") or {}).get("query_citations") or 0),
        "encrypted_query_citations": int((encrypted_rebuild.get("summary") or {}).get("query_citations") or 0),
    }
    summary["ready"] = all(summary.values()) if summary else False

    return {
        "generated_at": _utc_iso(),
        "plugin_root": _plugin_root(plugin_dir),
        "version_sync": version_sync,
        "distribution_layout": distribution_layout,
        "plain_export_import": {
            "encrypted": plain_archive.encrypted,
            "file_count": plain_archive.file_count,
            "import_result": plain_import,
            "rebuild_proof": plain_rebuild,
        },
        "encrypted_export_import": {
            "encrypted": encrypted_archive.encrypted,
            "file_count": encrypted_archive.file_count,
            "import_result": encrypted_import,
            "rebuild_proof": encrypted_rebuild,
        },
        "summary": summary,
    }
