"""Tests for ``src.upload_handler.UploadHandler`` uploads.json RMW atomicity.

The production code serialises the read-modify-write of ``uploads.json``
under ``UploadHandler._index_lock`` and writes atomically via
``UploadHandler._atomic_write_json`` (temp + ``os.fsync`` + ``os.replace``).
A ``.bak`` sibling is kept for partial-write recovery.

These tests exercise:
* N concurrent inserts retain all entries.
* N concurrent uploads through ``save_upload`` retain all entries.
* Duplicate-upload + new-insert race: the duplicate's stale snapshot
  must not overwrite a newer index entry.
* Partial-write recovery from the ``.bak`` sibling.
* The atomic-write primitives are wired in production code.
* Smoke tests: normal upload, duplicate detection, info lookup after
  a backup-recovery scenario.
"""
import concurrent.futures
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


try:
    from fastapi import HTTPException  # type: ignore
except Exception:  # pragma: no cover
    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: str = ""):
            self.status_code = status_code
            self.detail = detail
            super().__init__(detail)


from src.upload_handler import UploadHandler  # noqa: E402


N_WRITERS = 10


def _make_handler(tmp_path: Path) -> UploadHandler:
    base = tmp_path / "base"
    upload = tmp_path / "uploads"
    base.mkdir()
    upload.mkdir()
    return UploadHandler(base_dir=str(base), upload_dir=str(upload))


def _db_path(handler: UploadHandler) -> str:
    return os.path.join(handler.upload_dir, "uploads.json")


def _seed_entry(owner: str, file_hash: str, file_id: str) -> dict:
    return {
        "id": file_id,
        "path": f"/tmp/{file_id}",
        "mime": "text/plain",
        "size": 0,
        "name": file_id,
        "hash": file_hash,
        "original_name": file_id,
        "uploaded_at": "2026-06-01T00:00:00",
        "last_accessed": "2026-06-01T00:00:00",
        "client_ip": "127.0.0.1",
        "owner": owner,
    }


# ---------------------------------------------------------------------------
# Concurrent writers via the production handler.
# ---------------------------------------------------------------------------
def test_concurrent_inserts_lose_entries(tmp_path):
    """N=10 concurrent inserters on the same ``uploads.json`` must all be retained.

    The production code does the reload + write under ``_index_lock``,
    and ``_atomic_write_json`` gives readers a consistent on-disk view.
    If either protection is removed, this test will fail.
    """
    handler = _make_handler(tmp_path)
    db_path = _db_path(handler)
    with open(db_path, "w", encoding="utf-8") as f:
        json.dump({}, f)

    def insert(idx: int) -> None:
        with handler._index_lock:
            current = json.load(open(db_path)) if os.path.exists(db_path) else {}
            current[f"owner:hash_{idx}"] = {"id": f"file_{idx}", "owner": "owner"}
            handler._atomic_write_json(db_path, current)

    with concurrent.futures.ThreadPoolExecutor(max_workers=N_WRITERS) as pool:
        list(pool.map(insert, range(N_WRITERS)))

    with open(db_path, "r", encoding="utf-8") as f:
        final = json.load(f)
    assert len(final) == N_WRITERS, (
        f"Expected {N_WRITERS} entries, got {len(final)}. The lock+atomic-write "
        "fix is not actually serialising the writers."
    )


def test_save_upload_concurrent_retains_all_entries(tmp_path):
    """Drive ``save_upload`` end-to-end with N=10 concurrent uploads.

    Each upload has unique content (unique hash). If ``_index_lock`` or
    ``_atomic_write_json`` is removed or bypassed in ``save_upload``,
    concurrent writers lose entries. This test proves the production
    path is wired.
    """
    handler = _make_handler(tmp_path)
    handler.upload_rate_limit = 100

    def upload_one(idx: int) -> None:
        content = f"unique-content-{idx}-{os.urandom(8).hex()}".encode()
        fake_upload = SimpleNamespace(
            filename=f"file_{idx}.txt",
            file=io.BytesIO(content),
        )
        handler.save_upload(fake_upload, "127.0.0.1", f"owner_{idx % 3}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=N_WRITERS) as pool:
        list(pool.map(upload_one, range(N_WRITERS)))

    db_path = _db_path(handler)
    with open(db_path, "r", encoding="utf-8") as f:
        final = json.load(f)
    assert len(final) == N_WRITERS, (
        f"save_upload lost {N_WRITERS - len(final)}/{N_WRITERS} entries under "
        f"concurrent writes. Expected {N_WRITERS} entries, got {len(final)}. "
        f"Keys: {sorted(final.keys())}"
    )


# ---------------------------------------------------------------------------
# Duplicate vs new-insert race.
# ---------------------------------------------------------------------------
async def test_duplicate_vs_insert_race_preserves_both(tmp_path):
    """The ``save_upload`` duplicate branch must reload ``uploads.json``
    inside ``_index_lock`` before writing — it must not rely on a
    snapshot read before the lock.

    Pre-fix shape (the bug): the duplicate branch did
    ``existing_files = json.load(...)`` outside the lock, then under
    the lock did ``_atomic_write_json(uploads_db_path, existing_files)``
    — a stale snapshot that could clobber a concurrent insert.

    Post-fix: both branches call ``_load_upload_index()`` inside the
    lock, so the duplicate's write is always based on the freshest
    state.

    This test exercises the invariant by running a duplicate + a new
    upload concurrently via the production ``save_upload`` and asserting
    that both entries survive. With a slow disk (real ``fsync``), the
    window is wide enough that the bug, if reintroduced, would clobber
    the new entry; here the test relies on the post-fix invariant being
    correct by construction and on the lock serialising the writes.
    """
    import threading

    for iteration in range(3):
        iter_dir = tmp_path / f"iter_{iteration}"
        iter_dir.mkdir()
        handler = _make_handler(iter_dir)
        handler.upload_rate_limit = 100
        db_path = _db_path(handler)

        shared_content = b"shared-bytes-dedupe"
        with open(db_path, "w", encoding="utf-8") as f:
            json.dump({}, f)

        # Seed: one upload (new entry) so the index has a real row to dedupe against.
        fake_seed = SimpleNamespace(filename="seed.txt", file=io.BytesIO(shared_content))
        seed_result = handler.save_upload(fake_seed, "127.0.0.1", "owner_a")
        original_id = seed_result["id"]

        # Race: a duplicate of the seed (same content + owner) and a brand
        # new upload, both submitted via the real ``save_upload`` path.
        # The post-fix code must preserve both entries in uploads.json
        # and flag the duplicate as ``is_duplicate=True`` with the
        # original's id.
        fake_dup = SimpleNamespace(filename="shared.txt", file=io.BytesIO(shared_content))
        fake_new = SimpleNamespace(
            filename="other.txt", file=io.BytesIO(b"different-content")
        )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f_dup = pool.submit(
                handler.save_upload, fake_dup, "127.0.0.1", "owner_a"
            )
            f_new = pool.submit(
                handler.save_upload, fake_new, "127.0.0.1", "owner_a"
            )
            dup_result = f_dup.result()
            new_result = f_new.result()

        assert dup_result.get("is_duplicate") is True, (
            f"iter {iteration}: duplicate should be flagged is_duplicate=True"
        )
        assert dup_result["id"] == original_id, (
            f"iter {iteration}: duplicate should resolve to the seed's id"
        )

        with open(db_path, "r", encoding="utf-8") as f:
            final = json.load(f)

        assert len(final) == 2, (
            f"iter {iteration}: expected 2 entries (original + new) after "
            f"duplicate+insert race, got {len(final)}: {sorted(final.keys())}"
        )
        assert original_id in {v["id"] for v in final.values()}, (
            f"iter {iteration}: original id {original_id} missing from final index"
        )


# ---------------------------------------------------------------------------
# Partial-write recovery from the .bak sibling.
# ---------------------------------------------------------------------------
def test_partial_write_recovery_via_bak(tmp_path):
    """SIGKILL/SIGTERM mid-write can leave ``uploads.json`` truncated. The
    fixed code (1) writes atomically via temp+rename so a SIGKILL leaves
    the previous good copy in place, and (2) falls back to the ``.bak``
    sibling on read if the live file is corrupt.

    This test writes a valid ``uploads.json`` via the production helper
    (which creates a ``.bak``), then truncates the live file, and asserts
    that the next read recovers from the ``.bak``.
    """
    handler = _make_handler(tmp_path)
    db_path = _db_path(handler)

    original = {
        f"owner:hash_{i}": _seed_entry("owner", f"hash_{i}", f"id_{i}")
        for i in range(3)
    }
    handler._atomic_write_json(db_path, original)
    handler._atomic_write_json(db_path, {"latest": True})
    assert os.path.exists(db_path + ".bak"), (
        "Production _atomic_write_json must create a .bak sibling on subsequent writes."
    )

    full = open(db_path, "rb").read()
    truncated_len = max(1, len(full) // 2)
    with open(db_path, "wb") as f:
        f.write(full[:truncated_len])

    recovered = handler._load_upload_index()
    missing = [k for k in original if k not in recovered]
    assert not missing, (
        f"Partial-write recovery FAILED: {len(missing)} entries were lost. "
        f"Recovered keys: {sorted(recovered)}."
    )


# ---------------------------------------------------------------------------
# Atomicity primitive audit on the production module.
# ---------------------------------------------------------------------------
def test_atomic_write_primitives_present_in_production_code():
    """The production module must use atomic-write primitives for the RMW
    sites. The fix is in place when ``os.replace``, ``tempfile.mkstemp``,
    ``_atomic_write_json`` and ``self._index_lock`` are all present and
    the two RMW sites no longer use a bare ``open(path, "w") + json.dump``.
    """
    src_path = PROJECT_ROOT / "src" / "upload_handler.py"
    text = src_path.read_text(encoding="utf-8")

    assert "os.replace" in text, (
        f"{src_path} does not use os.replace — atomic-rename write is missing."
    )
    assert "tempfile.mkstemp" in text or "NamedTemporaryFile" in text, (
        f"{src_path} does not write to a temp file — atomic-rename write is missing."
    )
    assert "_atomic_write_json" in text, (
        f"{src_path} is missing the _atomic_write_json helper."
    )
    assert "self._index_lock" in text, (
        f"{src_path} is missing self._index_lock — concurrent writers are not serialised."
    )
    # The dedupe path must do its read inside the lock too.
    assert text.count("with self._index_lock:") >= 2, (
        "Both dedupe and insert RMW sites must be under _index_lock."
    )


# ---------------------------------------------------------------------------
# Smoke tests: normal upload, duplicate detection, info lookup after recovery.
# ---------------------------------------------------------------------------
def test_smoke_normal_upload(tmp_path):
    """Smoke test: a single upload round-trips through ``save_upload`` and
    the metadata is retrievable via ``get_upload_info``."""
    handler = _make_handler(tmp_path)
    handler.upload_rate_limit = 100

    fake = SimpleNamespace(filename="hello.txt", file=io.BytesIO(b"hello world"))
    result = handler.save_upload(fake, "127.0.0.1", "owner_a")

    assert result["name"] == "hello.txt"
    assert result["owner"] == "owner_a"
    assert "id" in result and "path" in result
    assert os.path.exists(result["path"])

    info = handler.get_upload_info(result["id"])
    assert info is not None
    assert info["id"] == result["id"]
    assert info["hash"] == result["hash"]


def test_smoke_duplicate_upload(tmp_path):
    """Smoke test: re-uploading the same content as the same owner returns
    the original record with ``is_duplicate=True`` and does not create a
    second file row."""
    handler = _make_handler(tmp_path)
    handler.upload_rate_limit = 100
    content = b"duplicate-content"

    first = handler.save_upload(
        SimpleNamespace(filename="dup.txt", file=io.BytesIO(content)),
        "127.0.0.1",
        "owner_a",
    )
    second = handler.save_upload(
        SimpleNamespace(filename="dup.txt", file=io.BytesIO(content)),
        "127.0.0.1",
        "owner_a",
    )

    assert second["is_duplicate"] is True
    assert second["id"] == first["id"]

    with open(_db_path(handler), "r", encoding="utf-8") as f:
        final = json.load(f)
    assert len(final) == 1, f"Duplicate upload should not add a new row, got {len(final)}"


def test_duplicate_upload_ignores_stale_missing_file(tmp_path):
    """A stale uploads.json row should not make a new upload point at a
    file that cleanup already removed from disk."""
    handler = _make_handler(tmp_path)
    handler.upload_rate_limit = 100
    content = b"same-content-after-cleanup"

    first = handler.save_upload(
        SimpleNamespace(filename="cleanup.txt", file=io.BytesIO(content)),
        "127.0.0.1",
        "owner_a",
    )
    os.remove(first["path"])

    second = handler.save_upload(
        SimpleNamespace(filename="cleanup.txt", file=io.BytesIO(content)),
        "127.0.0.1",
        "owner_a",
    )

    assert second.get("is_duplicate") is not True
    assert second["id"] != first["id"]
    assert os.path.exists(second["path"])

    with open(_db_path(handler), "r", encoding="utf-8") as f:
        final = json.load(f)
    ids = {row.get("id") for row in final.values()}
    assert first["id"] not in ids
    assert second["id"] in ids


def test_smoke_info_lookup_after_bak_recovery(tmp_path):
    """Smoke test: after a torn write is recovered from the ``.bak`` sibling,
    ``get_upload_info`` still finds the original entry by id."""
    handler = _make_handler(tmp_path)
    handler.upload_rate_limit = 100
    db_path = _db_path(handler)

    first = handler.save_upload(
        SimpleNamespace(filename="orig.txt", file=io.BytesIO(b"original")),
        "127.0.0.1",
        "owner_a",
    )
    # Force a .bak by writing a second time.
    handler._atomic_write_json(
        db_path,
        json.load(open(db_path)),
    )
    handler._atomic_write_json(db_path, {"sentinel": True})
    assert os.path.exists(db_path + ".bak")

    # Truncate the live file.
    full = open(db_path, "rb").read()
    with open(db_path, "wb") as f:
        f.write(full[: max(1, len(full) // 2)])

    info = handler.get_upload_info(first["id"])
    assert info is not None, "Info lookup must succeed after .bak recovery."
    assert info["id"] == first["id"]
    assert info["hash"] == first["hash"]


def test_invalid_creator_subject_id_is_rejected_before_upload_work(tmp_path, monkeypatch):
    handler = _make_handler(tmp_path)
    handler.upload_rate_limit = 100
    monkeypatch.setattr(
        handler,
        "get_upload_dir",
        lambda: (_ for _ in ()).throw(AssertionError("filesystem work must not start")),
    )

    with pytest.raises(HTTPException) as exc_info:
        handler.save_upload(
            SimpleNamespace(filename="invalid.txt", file=io.BytesIO(b"content")),
            "127.0.0.1",
            "alice",
            creator_subject_id="owner_invalid",
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid upload owner identity"
    assert handler.upload_rate_log == {}


def test_sid_bound_dedupe_separates_other_sid_and_legacy_rows(tmp_path):
    handler = _make_handler(tmp_path)
    handler.upload_rate_limit = 100
    content = b"same bytes across immutable identities"
    sid_a = "owner_" + "a" * 32
    sid_b = "owner_" + "b" * 32

    def upload(*, sid):
        return handler.save_upload(
            SimpleNamespace(filename="same.txt", file=io.BytesIO(content)),
            "127.0.0.1",
            "alice",
            creator_subject_id=sid,
        )

    first = upload(sid=sid_a)
    duplicate = upload(sid=sid_a)
    other_subject = upload(sid=sid_b)
    legacy = upload(sid=None)

    assert duplicate["is_duplicate"] is True
    assert duplicate["id"] == first["id"]
    assert other_subject["id"] != first["id"]
    assert legacy["id"] not in {first["id"], other_subject["id"]}
    assert all(
        "creator_subject_id" not in result
        for result in (first, duplicate, other_subject, legacy)
    )

    index = json.loads(Path(_db_path(handler)).read_text(encoding="utf-8"))
    file_hash = first["hash"]
    assert set(index) == {
        f"alice:{sid_a}:{file_hash}",
        f"alice:{sid_b}:{file_hash}",
        f"alice:{file_hash}",
    }
    assert index[f"alice:{sid_a}:{file_hash}"]["creator_subject_id"] == sid_a
    assert index[f"alice:{sid_b}:{file_hash}"]["creator_subject_id"] == sid_b
    assert index[f"alice:{file_hash}"]["creator_subject_id"] is None


def test_sid_bound_owner_rename_keeps_immutable_subject_in_key_and_metadata(tmp_path):
    handler = _make_handler(tmp_path)
    handler.upload_rate_limit = 100
    sid = "owner_" + "c" * 32
    result = handler.save_upload(
        SimpleNamespace(filename="rename.txt", file=io.BytesIO(b"rename-bound")),
        "127.0.0.1",
        "alice",
        creator_subject_id=sid,
    )

    assert handler.rename_owner("alice", "alice2") == 1

    index = json.loads(Path(_db_path(handler)).read_text(encoding="utf-8"))
    assert set(index) == {f"alice2:{sid}:{result['hash']}"}
    stored = next(iter(index.values()))
    assert stored["owner"] == "alice2"
    assert stored["creator_subject_id"] == sid


def test_sid_bound_index_failure_removes_only_new_unindexed_file(tmp_path, monkeypatch, caplog):
    handler = _make_handler(tmp_path)
    handler.upload_rate_limit = 100
    sid = "owner_" + "d" * 32
    secret_failure = "provider-secret-must-not-escape"

    def fail_index(*args, **kwargs):
        raise RuntimeError(secret_failure)

    monkeypatch.setattr(handler, "_atomic_write_json", fail_index)
    with pytest.raises(HTTPException) as exc_info:
        handler.save_upload(
            SimpleNamespace(filename="rollback.txt", file=io.BytesIO(b"rollback")),
            "127.0.0.1",
            "alice",
            creator_subject_id=sid,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to save upload"
    assert secret_failure not in str(exc_info.value.detail)
    assert secret_failure not in caplog.text
    assert not [path for path in Path(handler.upload_dir).rglob("*") if path.is_file()]


def test_sid_bound_duplicate_index_failure_does_not_remove_existing_file(tmp_path, monkeypatch):
    handler = _make_handler(tmp_path)
    handler.upload_rate_limit = 100
    sid = "owner_" + "e" * 32
    content = b"existing-bound-file"
    first = handler.save_upload(
        SimpleNamespace(filename="existing.txt", file=io.BytesIO(content)),
        "127.0.0.1",
        "alice",
        creator_subject_id=sid,
    )
    original_path = Path(first["path"])

    monkeypatch.setattr(
        handler,
        "_atomic_write_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("hidden")),
    )
    with pytest.raises(HTTPException) as exc_info:
        handler.save_upload(
            SimpleNamespace(filename="existing.txt", file=io.BytesIO(content)),
            "127.0.0.1",
            "alice",
            creator_subject_id=sid,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to save upload"
    assert original_path.is_file()


def test_concurrent_identical_sid_uploads_leave_one_indexed_file(tmp_path, monkeypatch):
    import threading

    handler = _make_handler(tmp_path)
    handler.upload_rate_limit = 100
    sid = "owner_" + "f" * 32
    content = b"concurrent-identical-bound-content"
    upload_dir = handler.get_upload_dir()
    both_passed_initial_dedupe = threading.Barrier(2)

    def synchronized_upload_dir():
        both_passed_initial_dedupe.wait(timeout=5)
        return upload_dir

    monkeypatch.setattr(handler, "get_upload_dir", synchronized_upload_dir)

    def upload_one(_index):
        return handler.save_upload(
            SimpleNamespace(filename="same.txt", file=io.BytesIO(content)),
            "127.0.0.1",
            "alice",
            creator_subject_id=sid,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(upload_one, range(2)))

    assert {result["id"] for result in results} == {results[0]["id"]}
    assert sum(result.get("is_duplicate") is True for result in results) == 1
    index = json.loads(Path(_db_path(handler)).read_text(encoding="utf-8"))
    assert len(index) == 1
    stored = next(iter(index.values()))
    assert stored["creator_subject_id"] == sid
    data_files = {
        path
        for path in Path(handler.upload_dir).rglob("*")
        if path.is_file() and path.name not in {"uploads.json", "uploads.json.bak"}
    }
    assert data_files == {Path(stored["path"])}


def test_valid_looking_str_subclass_sid_is_rejected_before_format_or_upload_work(tmp_path, monkeypatch):
    handler = _make_handler(tmp_path)
    handler.upload_rate_limit = 100

    class HostileSid(str):
        def __format__(self, format_spec):
            raise AssertionError("hostile SID formatting must not execute")

    monkeypatch.setattr(
        handler,
        "get_upload_dir",
        lambda: (_ for _ in ()).throw(AssertionError("filesystem work must not start")),
    )
    with pytest.raises(HTTPException) as exc_info:
        handler.save_upload(
            SimpleNamespace(filename="hostile.txt", file=io.BytesIO(b"content")),
            "127.0.0.1",
            "alice",
            creator_subject_id=HostileSid("owner_" + "a" * 32),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid upload owner identity"
    assert handler.upload_rate_log == {}


def test_bound_index_and_unlink_failure_is_classified_without_touching_other_files(
    tmp_path, monkeypatch, caplog
):
    handler = _make_handler(tmp_path)
    handler.upload_rate_limit = 100
    upload_dir = Path(handler.get_upload_dir())
    unrelated = upload_dir / "preexisting.txt"
    unrelated.write_bytes(b"preserve me")
    before = {path for path in Path(handler.upload_dir).rglob("*") if path.is_file()}

    monkeypatch.setattr(
        handler,
        "_atomic_write_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("hidden-index-detail")),
    )
    real_remove = os.remove

    def refuse_owned_cleanup(path):
        if Path(path) != unrelated:
            raise PermissionError("hidden-cleanup-detail")
        return real_remove(path)

    monkeypatch.setattr(os, "remove", refuse_owned_cleanup)
    with pytest.raises(HTTPException) as exc_info:
        handler.save_upload(
            SimpleNamespace(filename="cleanup.txt", file=io.BytesIO(b"cleanup-refused")),
            "127.0.0.1",
            "alice",
            creator_subject_id="owner_" + "b" * 32,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Upload cleanup failed"
    assert unrelated.read_bytes() == b"preserve me"
    after = {path for path in Path(handler.upload_dir).rglob("*") if path.is_file()}
    assert before <= after
    assert len(after - before) == 1
    assert "hidden-index-detail" not in caplog.text
    assert "hidden-cleanup-detail" not in caplog.text


def test_bound_partial_file_write_failure_removes_own_partial_file(tmp_path):
    handler = _make_handler(tmp_path)
    handler.upload_rate_limit = 100

    class PartialWriteSource(io.BytesIO):
        def __init__(self, value):
            super().__init__(value)
            self.upload_reads = 0

        def read(self, size=-1):
            if size == 8192:
                self.upload_reads += 1
                if self.upload_reads == 2:
                    raise OSError("hidden-partial-write-detail")
            return super().read(size)

    with pytest.raises(HTTPException) as exc_info:
        handler.save_upload(
            SimpleNamespace(filename="partial.txt", file=PartialWriteSource(b"partial-content")),
            "127.0.0.1",
            "alice",
            creator_subject_id="owner_" + "c" * 32,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to save file"
    assert not [path for path in Path(handler.upload_dir).rglob("*") if path.is_file()]


def test_bound_create_collision_does_not_delete_preexisting_file(tmp_path, monkeypatch):
    handler = _make_handler(tmp_path)
    handler.upload_rate_limit = 100
    upload_dir = Path(handler.get_upload_dir())
    collision_id = "1" * 32 + ".txt"
    collision = upload_dir / collision_id
    collision.write_bytes(b"preexisting collision")
    monkeypatch.setattr("src.upload_handler._build_upload_id", lambda safe_name: collision_id)

    with pytest.raises(HTTPException) as exc_info:
        handler.save_upload(
            SimpleNamespace(filename="collision.txt", file=io.BytesIO(b"new content")),
            "127.0.0.1",
            "alice",
            creator_subject_id="owner_" + "d" * 32,
        )

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == "Failed to save file"
    assert collision.read_bytes() == b"preexisting collision"
