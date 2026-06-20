import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone


_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ODYSSEUS_ROOT = os.getenv("ODYSSEUS_ROOT", os.path.abspath(os.path.join(_ROOT, "..", "..", "..", "..", "..", "odysseus")))

for _p in (_ODYSSEUS_ROOT, os.path.dirname(_ROOT), _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backend import vault_service


def _write_trash_file(vault_dir: str, date_name: str, rel_path: str = "note.md") -> None:
    target = os.path.join(vault_dir, vault_service.TRASH_DIR, date_name, rel_path)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("# Deleted\n")


def test_purge_trash_handles_large_date_sets_without_touching_recent_entries():
    with tempfile.TemporaryDirectory() as tmpdir:
        now = datetime.now(timezone.utc)
        old_dates = [(now - timedelta(days=45 + index)).strftime("%Y-%m-%d") for index in range(40)]
        recent_dates = [(now - timedelta(days=index)).strftime("%Y-%m-%d") for index in range(5)]

        for index, date_name in enumerate(old_dates):
            _write_trash_file(tmpdir, date_name, f"folder-{index}/deleted.md")
        for index, date_name in enumerate(recent_dates):
            _write_trash_file(tmpdir, date_name, f"recent-{index}.md")
        _write_trash_file(tmpdir, "not-a-date", "ignored.md")

        result = vault_service.purge_trash(tmpdir, retention_days=30)

        assert result["purged"] == len(set(old_dates))
        assert result["errors"] == 0
        assert result["skipped"] >= len(set(recent_dates))
        trash_root = os.path.join(tmpdir, vault_service.TRASH_DIR)
        remaining = set(os.listdir(trash_root))
        assert set(old_dates).isdisjoint(remaining)
        assert set(recent_dates) <= remaining
        assert "not-a-date" in remaining


def test_purge_trash_skips_non_directory_entries():
    with tempfile.TemporaryDirectory() as tmpdir:
        trash_root = os.path.join(tmpdir, vault_service.TRASH_DIR)
        os.makedirs(trash_root, exist_ok=True)
        marker = os.path.join(trash_root, "2020-01-01")
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write("not a directory")

        result = vault_service.purge_trash(tmpdir, retention_days=30)

        assert result == {"purged": 0, "errors": 0, "skipped": 1}
        assert os.path.isfile(marker)
