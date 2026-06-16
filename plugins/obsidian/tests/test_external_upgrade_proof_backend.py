import os
import shutil
import sys
import tempfile


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv("ODYSSEUS_ROOT", os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")))

for _p in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend.external_upgrade_proof import (
    collect_distribution_layout,
    collect_external_upgrade_proof,
    collect_version_sync,
)


PLUGIN_ROOT = _ROOT


def test_external_upgrade_proof_detects_version_sync_for_current_plugin_root():
    payload = collect_version_sync(PLUGIN_ROOT)

    assert payload["match"] is True
    assert payload["plugin_json_version"] == "0.10.0-rc.1"
    assert payload["plugin_py_version"] == "0.10.0-rc.1"


def test_external_upgrade_proof_detects_forbidden_distribution_artifacts():
    with tempfile.TemporaryDirectory() as tmpdir:
        plugin_copy = os.path.join(tmpdir, "obsidian")
        shutil.copytree(
            PLUGIN_ROOT,
            plugin_copy,
            ignore=shutil.ignore_patterns(".git", ".pytest_cache", "__pycache__"),
        )
        os.makedirs(os.path.join(plugin_copy, "__pycache__"), exist_ok=True)

        payload = collect_distribution_layout(plugin_copy)

        assert payload["ready"] is False
        assert payload["missing_entries"] == []
        assert payload["forbidden_entries"] == ["__pycache__"]
        assert payload["ignored_runtime_artifacts"] == []


def test_external_upgrade_proof_collects_distribution_and_rebuild_evidence():
    with tempfile.TemporaryDirectory() as vault_dir:
        os.makedirs(os.path.join(vault_dir, "Projects"), exist_ok=True)
        with open(os.path.join(vault_dir, "Projects", "Blob.md"), "w", encoding="utf-8") as handle:
            handle.write("# Blob\n\nExternal proof should rebuild with citations.\n")

        payload = collect_external_upgrade_proof(
            PLUGIN_ROOT,
            vault_dir,
            query="blob citations",
            top_k=5,
            path_prefix="Projects",
        )

        assert payload["version_sync"]["match"] is True
        assert payload["distribution_layout"]["ready"] is True
        assert payload["distribution_layout"]["ignored_runtime_artifacts"] in ([], ["__pycache__"])
        assert payload["plain_export_import"]["encrypted"] is False
        assert payload["encrypted_export_import"]["encrypted"] is True
        assert payload["plain_export_import"]["import_result"]["imported_files"] >= 1
        assert payload["encrypted_export_import"]["import_result"]["imported_files"] >= 1
        assert payload["plain_export_import"]["rebuild_proof"]["summary"]["query_citations"] >= 1
        assert payload["encrypted_export_import"]["rebuild_proof"]["summary"]["query_citations"] >= 1
        assert payload["summary"]["ready"] is True
