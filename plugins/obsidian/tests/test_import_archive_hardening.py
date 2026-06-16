import os
import sys
import tempfile
import zipfile
from io import BytesIO

import pytest


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv("ODYSSEUS_ROOT", os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")))

for _p in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend.import_export import import_vault, validate_archive_member
from backend.vault_security import VaultSecurityError


def _zip_bytes(entries):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, data in entries:
            zf.writestr(path, data)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "member",
    [
        ".obsidian/relationships.json",
        ".obsidian/project_planning_sessions.json",
        ".obsidian/odysseus/derived_index.json",
        "nested/odysseus-vault.json",
        "nested/vault.bin",
    ],
)
def test_validate_archive_member_rejects_reserved_internal_paths(member):
    with pytest.raises(VaultSecurityError):
        validate_archive_member(member)


def test_import_vault_rejects_case_insensitive_duplicate_paths():
    archive = _zip_bytes(
        [
            ("Docs/Blob.md", b"# Blob\n"),
            ("docs/blob.md", b"# Blob duplicate\n"),
        ]
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        with pytest.raises(VaultSecurityError) as exc:
            import_vault(tmpdir, archive)

        assert "duplicate paths" in str(exc.value)
        assert not os.path.exists(os.path.join(tmpdir, "Docs", "Blob.md"))
        assert not os.path.exists(os.path.join(tmpdir, "docs", "blob.md"))


def test_import_vault_rejects_conflicts_without_partial_write():
    archive = _zip_bytes(
        [
            ("Existing.md", b"# Existing replacement\n"),
            ("Fresh.md", b"# Fresh\n"),
        ]
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "Existing.md"), "w", encoding="utf-8") as handle:
            handle.write("# Existing\n")

        with pytest.raises(VaultSecurityError) as exc:
            import_vault(tmpdir, archive)

        assert "Import conflict: Existing.md" == str(exc.value)
        assert not os.path.exists(os.path.join(tmpdir, "Fresh.md"))
        with open(os.path.join(tmpdir, "Existing.md"), "r", encoding="utf-8") as handle:
            assert handle.read() == "# Existing\n"
