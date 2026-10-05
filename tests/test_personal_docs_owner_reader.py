from __future__ import annotations

import ast
import dataclasses
import os
from pathlib import Path

import pytest

import src.personal_docs_owner_reader as pds


ROOT_REF = "sha256:" + "a" * 64


def make_reader(root: Path, owner: str = "owner-alpha") -> pds.PersonalDocsOwnerReader:
    return pds.PersonalDocsOwnerReader(
        owner_id=owner, root_ref=ROOT_REF, root_path=str(root)
    )


def code(error: pytest.ExceptionInfo[pds.PersonalDocsOwnerReaderError]) -> str:
    return error.value.code


def clone(
    item: pds.PersonalDocsFileDescriptor,
) -> pds.PersonalDocsFileDescriptor:
    return pds.PersonalDocsFileDescriptor(
        item.relative_path,
        item.locator,
        item.mime_type,
        item.extractor_profile,
        item.byte_size,
        item.char_size,
        item.source_ref,
        item.revision_ref,
        item.extracted_content_hash,
        item.binding_digest,
        item.descriptor_digest,
    )


def test_constructor_is_zero_filesystem_and_rejects_invalid_primitives(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "docs"
    root.mkdir()

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("constructor performed filesystem I/O")

    for name in ("lstat", "stat", "scandir", "open", "mkdir"):
        if hasattr(pds.os, name):
            monkeypatch.setattr(pds.os, name, forbidden)
    assert type(make_reader(root)) is pds.PersonalDocsOwnerReader

    invalid = [
        ("", ROOT_REF, str(root)),
        ("x" * 257, ROOT_REF, str(root)),
        ("bad\nowner", ROOT_REF, str(root)),
        ("owner", "A" * 64, str(root)),
        ("owner", "a" * 64, str(root)),
        ("owner", "sha256:" + ROOT_REF, str(root)),
        ("owner", ROOT_REF, "relative"),
        ("owner", ROOT_REF, str(root) + os.sep),
        ("owner", ROOT_REF, r"\\server\share"),
        ("owner", ROOT_REF, r"\\?\C:\private"),
    ]
    for owner_id, root_ref, root_path in invalid:
        with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
            pds.PersonalDocsOwnerReader(
                owner_id=owner_id, root_ref=root_ref, root_path=root_path
            )
        assert code(captured) in {"invalid_request", "invalid_root"}

    class Text(str):
        pass

    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        pds.PersonalDocsOwnerReader(
            owner_id=Text("owner"), root_ref=ROOT_REF, root_path=str(root)
        )
    assert code(captured) == "invalid_request"


def test_deterministic_capture_mime_utf8_empty_exclusions_and_duplicates(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    nested = root / "nested"
    nested.mkdir(parents=True)
    fixtures = {
        root / "b.MD": "# Heading\r\ncafé\n",
        root / "A.txt": "\ufeffalpha\r\nbeta\n",
        root / "empty.JSON": "",
        root / "copy-one.txt": "duplicate",
        nested / "copy-two.TXT": "duplicate",
        nested / "excluded_files.json": '{"nested":true}\n',
        root / "Žeta.md": "unicode",
    }
    for path, content in fixtures.items():
        path.write_text(content, encoding="utf-8", newline="")
    (root / "ignored.bin").write_bytes(b"\x00\xff")
    (root / "INDEXED_DIRECTORIES.JSON").write_text("registry", encoding="utf-8")
    (root / "excluded_files.json").write_text("registry", encoding="utf-8")

    reader = make_reader(root)
    first = reader.capture()
    second = make_reader(root).capture()
    paths = tuple(item.relative_path for item in first.descriptors)
    assert paths == tuple(sorted(paths, key=lambda item: item.encode("utf-8")))
    assert set(paths) == {
        "A.txt", "b.MD", "copy-one.txt", "empty.JSON",
        "nested/copy-two.TXT", "nested/excluded_files.json", "Žeta.md",
    }
    assert first.snapshot_digest == second.snapshot_digest
    assert first.binding_digest == second.binding_digest
    assert first.owner_ref.startswith("sha256:")
    assert first.snapshot_digest != make_reader(root, "owner-beta").capture().snapshot_digest

    by_path = {item.relative_path: item for item in first.descriptors}
    assert by_path["A.txt"].mime_type == "text/plain; charset=utf-8"
    assert by_path["b.MD"].mime_type == "text/markdown; charset=utf-8"
    assert by_path["empty.JSON"].mime_type == "application/json"
    exact = reader.read_exact(by_path["A.txt"])
    assert exact.content == "\ufeffalpha\r\nbeta\n"
    assert exact.byte_size == len(exact.content.encode("utf-8"))
    assert exact.char_size == len(exact.content)
    assert reader.read_exact(by_path["empty.JSON"]).content == ""

    left = by_path["copy-one.txt"]
    right = by_path["nested/copy-two.TXT"]
    assert left.revision_ref == right.revision_ref
    assert left.extracted_content_hash == right.extracted_content_hash
    assert left.source_ref != right.source_ref
    assert left.descriptor_digest != right.descriptor_digest


def test_snapshot_counts_unsupported_files_and_binds_census(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    (root / "guide.md").write_text("supported", encoding="utf-8")
    (root / "archive.pdf").write_bytes(b"opaque")
    snapshot = make_reader(root).capture()
    assert snapshot.unsupported_count == 1
    assert snapshot.unsupported_digest.startswith("sha256:")
    assert len(snapshot.descriptors) == 1
    assert snapshot.snapshot_digest != make_reader(root, "owner-beta").capture().snapshot_digest


def test_repr_errors_and_evidence_omit_private_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "owner-private-root"
    root.mkdir()
    private = "VERY_PRIVATE_CONTENT"
    (root / "private-name.txt").write_text(private, encoding="utf-8")
    reader = make_reader(root, "secret-owner")
    snapshot = reader.capture()
    descriptor = snapshot.descriptors[0]
    exact = reader.read_exact(descriptor)
    rendered = " ".join(
        [repr(snapshot), repr(descriptor), repr(descriptor.locator), repr(exact)]
    )
    for value in ("secret-owner", str(root), "private-name.txt", private):
        assert value not in rendered

    def explode(_: str) -> object:
        raise OSError(f"raw-os-error {root} {private}")

    monkeypatch.setattr(pds.os, "scandir", explode)
    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        reader.capture()
    assert code(captured) == "unsafe_filesystem"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "raw-os-error" not in repr(captured.value)


def test_recapture_forgery_tamper_revocation_and_recovery(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    (root / "note.txt").write_text("first", encoding="utf-8")
    reader = make_reader(root)
    first = reader.capture()
    old = first.descriptors[0]
    forged = clone(old)

    second = reader.capture()
    current = second.descriptors[0]
    assert second.snapshot_digest == first.snapshot_digest
    for rejected in (old, forged):
        with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
            reader.read_exact(rejected)
        assert code(captured) == "invalid_descriptor"

    object.__setattr__(current, "byte_size", current.byte_size + 1)
    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        reader.read_exact(current)
    assert code(captured) == "invalid_descriptor"

    fresh = reader.capture().descriptors[0]
    bad = root / "bad.md"
    bad.write_bytes(b"\xff")
    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        reader.capture()
    assert code(captured) == "invalid_utf8"
    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        reader.read_exact(fresh)
    assert code(captured) == "authority_unavailable"
    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        reader.observe_unavailable(fresh)
    assert code(captured) == "authority_unavailable"
    bad.unlink()
    recovered = reader.capture().descriptors[0]
    assert reader.read_exact(recovered).content == "first"


def test_exact_read_and_complete_unavailable_matrix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    target = root / "note.txt"
    target.write_text("original", encoding="utf-8")

    reader = make_reader(root)
    descriptor = reader.capture().descriptors[0]
    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        reader.observe_unavailable(descriptor)
    assert code(captured) == "still_available"

    changed_reader = make_reader(root)
    changed = changed_reader.capture().descriptors[0]
    target.write_text("changed-content", encoding="utf-8")
    assert changed_reader.observe_unavailable(changed).reason == "changed"
    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        changed_reader.read_exact(changed)
    assert code(captured) == "changed"

    missing_reader = make_reader(root)
    missing = missing_reader.capture().descriptors[0]
    target.unlink()
    assert missing_reader.observe_unavailable(missing).reason == "missing"

    target.write_text("replacement-base", encoding="utf-8")
    replaced_reader = make_reader(root)
    replaced = replaced_reader.capture().descriptors[0]
    target.unlink()
    target.write_text("replacement-base", encoding="utf-8")
    assert replaced_reader.observe_unavailable(replaced).reason == "unsafe_or_replaced"

    denied_reader = make_reader(root)
    denied = denied_reader.capture().descriptors[0]
    real_lstat = pds.os.lstat

    def deny(path: str) -> os.stat_result:
        if os.path.normcase(str(path)) == os.path.normcase(str(target)):
            raise PermissionError("private-name")
        return real_lstat(path)

    monkeypatch.setattr(pds.os, "lstat", deny)
    observation = denied_reader.observe_unavailable(denied)
    assert observation.reason == "access_denied"
    assert "private-name" not in repr(observation)


def test_snapshot_export_tamper_cannot_alter_unavailable_evidence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "snapshot-seal"
    root.mkdir()
    target = root / "note.txt"
    target.write_text("original", encoding="utf-8")
    replacements: tuple[tuple[str, object], ...] = (
        ("snapshot_digest", "sha256:" + "b" * 64),
        ("binding_digest", "sha256:" + "c" * 64),
        ("descriptors", ()),
    )
    for field, replacement in replacements:
        target.write_text("original", encoding="utf-8")
        reader = make_reader(root)
        snapshot = reader.capture()
        descriptor = snapshot.descriptors[0]
        expected_snapshot = snapshot.snapshot_digest
        expected_binding = snapshot.binding_digest
        object.__setattr__(snapshot, field, replacement)
        target.write_text("changed-content", encoding="utf-8")
        observation = reader.observe_unavailable(descriptor)
        assert observation.reason == "changed"
        assert observation.snapshot_digest == expected_snapshot
        assert observation.binding_digest == expected_binding


def test_descriptor_tamper_during_read_exact_cannot_forge_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "read-race"
    root.mkdir()
    (root / "note.txt").write_text("content", encoding="utf-8")
    reader = make_reader(root)
    descriptor = reader.capture().descriptors[0]
    real_read = pds.os.read
    mutated = False

    def tampering_read(file_descriptor: int, amount: int) -> bytes:
        nonlocal mutated
        raw = real_read(file_descriptor, amount)
        if not mutated:
            mutated = True
            forged_source = "personal-docs:file:" + "d" * 64
            forged_locator = pds.PersonalDocsWholeFileLocator(
                forged_source, descriptor.revision_ref, 0, descriptor.char_size
            )
            object.__setattr__(descriptor, "source_ref", forged_source)
            object.__setattr__(descriptor, "locator", forged_locator)
        return raw

    monkeypatch.setattr(pds.os, "read", tampering_read)
    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        reader.read_exact(descriptor)
    assert code(captured) == "invalid_descriptor"


def test_descriptor_tamper_during_unavailable_probe_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "probe-race"
    root.mkdir()
    (root / "note.txt").write_text("content", encoding="utf-8")
    reader = make_reader(root)
    descriptor = reader.capture().descriptors[0]
    real_read = pds.os.read
    mutated = False

    def tampering_read(file_descriptor: int, amount: int) -> bytes:
        nonlocal mutated
        raw = real_read(file_descriptor, amount)
        if not mutated:
            mutated = True
            object.__setattr__(descriptor, "revision_ref", "sha256:" + "e" * 64)
        return raw

    monkeypatch.setattr(pds.os, "read", tampering_read)
    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        reader.observe_unavailable(descriptor)
    assert code(captured) == "invalid_descriptor"


def test_open_flags_and_no_bytes_before_fstat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    (root / "note.txt").write_text("content", encoding="utf-8")
    real_open, real_fstat, real_read = pds.os.open, pds.os.fstat, pds.os.read
    state: dict[str, object] = {"fstat": 0, "flags": []}

    def tracked_open(path: str, flags: int) -> int:
        state["flags"].append(flags)  # type: ignore[union-attr]
        return real_open(path, flags)

    def tracked_fstat(descriptor: int) -> os.stat_result:
        state["fstat"] = int(state["fstat"]) + 1
        return real_fstat(descriptor)

    def tracked_read(descriptor: int, amount: int) -> bytes:
        assert int(state["fstat"]) > 0
        return real_read(descriptor, amount)

    monkeypatch.setattr(pds.os, "open", tracked_open)
    monkeypatch.setattr(pds.os, "fstat", tracked_fstat)
    monkeypatch.setattr(pds.os, "read", tracked_read)
    make_reader(root).capture()
    flags_seen = state["flags"]
    assert flags_seen
    for name in ("O_NONBLOCK", "O_NOFOLLOW", "O_BINARY", "O_NOINHERIT"):
        flag = getattr(pds.os, name, 0)
        if type(flag) is int and flag:
            assert all(value & flag for value in flags_seen)  # type: ignore[union-attr]


def test_final_rescan_drift_and_root_replacement_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    (root / "note.txt").write_text("content", encoding="utf-8")
    real_read = pds._stable_read
    mutated = False

    def drift(*args: object, **kwargs: object) -> object:
        nonlocal mutated
        result = real_read(*args, **kwargs)
        if not mutated:
            mutated = True
            (root / "late.bin").write_bytes(b"x")
        return result

    monkeypatch.setattr(pds, "_stable_read", drift)
    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        make_reader(root).capture()
    assert code(captured) == "filesystem_changed"
    monkeypatch.undo()

    (root / "late.bin").unlink()
    reader = make_reader(root)
    issued = reader.capture().descriptors[0]
    old_root = tmp_path / "old-docs"
    root.rename(old_root)
    root.mkdir()
    (root / "note.txt").write_text("content", encoding="utf-8")
    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        reader.capture()
    assert code(captured) == "unsafe_filesystem"
    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        reader.read_exact(issued)
    assert code(captured) == "authority_unavailable"


class ModifiedStat:
    def __init__(self, original: os.stat_result, **overrides: int) -> None:
        self.original = original
        self.overrides = overrides

    def __getattr__(self, name: str) -> object:
        if name in self.overrides:
            return self.overrides[name]
        return getattr(self.original, name)


def test_direntry_device_inode_disagreement_uses_canonical_lstat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "identity"
    root.mkdir()
    (root / "note.txt").write_text("content", encoding="utf-8")
    real_entry_stat = pds._entry_stat

    class DivergentIdentity:
        def __init__(self, original: os.stat_result) -> None:
            self.original = original
            self.st_dev = int(original.st_dev) + 1
            self.st_ino = int(original.st_ino) + 1

        def __getattr__(self, name: str) -> object:
            return getattr(self.original, name)

    monkeypatch.setattr(
        pds,
        "_entry_stat",
        lambda entry: DivergentIdentity(real_entry_stat(entry)),
    )
    reader = make_reader(root)
    descriptor = reader.capture().descriptors[0]
    assert reader.read_exact(descriptor).content == "content"


@pytest.mark.skipif(os.name != "nt", reason="Windows stat API semantics")
def test_windows_cross_api_ctime_split_preserves_exact_channel_checks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "handle-ctime"
    root.mkdir()
    (root / "note.txt").write_text("content", encoding="utf-8")
    real_fstat = pds.os.fstat

    def split_ctime(descriptor: int) -> object:
        original = real_fstat(descriptor)
        return ModifiedStat(
            original, st_ctime_ns=int(original.st_ctime_ns) + 10_000_000
        )

    monkeypatch.setattr(pds.os, "fstat", split_ctime)
    reader = make_reader(root)
    descriptor = reader.capture().descriptors[0]
    assert reader.read_exact(descriptor).content == "content"


@pytest.mark.skipif(os.name != "nt", reason="Windows stat API semantics")
@pytest.mark.parametrize(
    "field",
    (
        "st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns",
        "st_birthtime_ns", "st_file_attributes",
    ),
)
def test_windows_cross_api_identity_mismatch_fails_before_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    root = tmp_path / field
    root.mkdir()
    (root / "note.txt").write_text("content", encoding="utf-8")
    real_fstat = pds.os.fstat
    reads = 0

    def mismatched(descriptor: int) -> object:
        original = real_fstat(descriptor)
        current = int(getattr(original, field))
        if field == "st_mode":
            replacement = current ^ 1
        elif field == "st_file_attributes":
            replacement = current | getattr(
                pds.stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
            )
        else:
            replacement = current + 1
        return ModifiedStat(original, **{field: replacement})

    def tracked_read(descriptor: int, amount: int) -> bytes:
        nonlocal reads
        reads += 1
        return b""

    monkeypatch.setattr(pds.os, "fstat", mismatched)
    monkeypatch.setattr(pds.os, "read", tracked_read)
    with pytest.raises(pds.PersonalDocsOwnerReaderError):
        make_reader(root).capture()
    assert reads == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows stat API semantics")
def test_windows_same_handle_ctime_drift_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "handle-drift"
    root.mkdir()
    (root / "note.txt").write_text("content", encoding="utf-8")
    real_fstat = pds.os.fstat
    calls = 0

    def drifting(descriptor: int) -> object:
        nonlocal calls
        calls += 1
        original = real_fstat(descriptor)
        return ModifiedStat(original, st_ctime_ns=int(original.st_ctime_ns) + calls)

    monkeypatch.setattr(pds.os, "fstat", drifting)
    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        make_reader(root).capture()
    assert code(captured) == "filesystem_changed"


@pytest.mark.skipif(os.name != "nt", reason="Windows stat API semantics")
def test_windows_path_ctime_drift_fails_before_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "path-drift"
    root.mkdir()
    target = root / "note.txt"
    target.write_text("content", encoding="utf-8")
    real_lstat = pds.os.lstat
    target_calls = 0
    reads = 0

    def drifting(path: str) -> object:
        nonlocal target_calls
        original = real_lstat(path)
        if os.path.normcase(str(path)) == os.path.normcase(str(target)):
            target_calls += 1
            if target_calls >= 2:
                return ModifiedStat(
                    original, st_ctime_ns=int(original.st_ctime_ns) + 1
                )
        return original

    def tracked_read(descriptor: int, amount: int) -> bytes:
        nonlocal reads
        reads += 1
        return b""

    monkeypatch.setattr(pds.os, "lstat", drifting)
    monkeypatch.setattr(pds.os, "read", tracked_read)
    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        make_reader(root).capture()
    assert code(captured) == "filesystem_changed"
    assert reads == 0


@pytest.mark.skipif(os.name != "nt", reason="Windows stat API semantics")
def test_windows_real_500_file_capture_and_read(tmp_path: Path) -> None:
    root = tmp_path / "real-identity"
    root.mkdir()
    for index in range(500):
        (root / f"file-{index:03d}.txt").write_text(
            f"content-{index}", encoding="utf-8"
        )
    reader = make_reader(root)
    snapshot = reader.capture()
    assert len(snapshot.descriptors) == 500
    for index, descriptor in enumerate(snapshot.descriptors):
        assert reader.read_exact(descriptor).content == f"content-{index}"


def test_symlink_and_platform_reparse_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    link = root / "link.txt"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("real symlinks unavailable")
    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        make_reader(root).capture()
    assert code(captured) == "unsafe_filesystem"

    link.unlink()
    (root / "regular.txt").write_text("safe", encoding="utf-8")
    real_entry_stat = pds._entry_stat

    class Reparse:
        st_file_attributes = 0x400

        def __init__(self, original: os.stat_result) -> None:
            self.original = original

        def __getattr__(self, name: str) -> object:
            return getattr(self.original, name)

    monkeypatch.setattr(
        pds, "_entry_stat", lambda entry: Reparse(real_entry_stat(entry))
    )
    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        make_reader(root).capture()
    assert code(captured) == "unsafe_filesystem"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO is POSIX-only")
def test_fifo_is_rejected_without_open(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    os.mkfifo(root / "pipe.txt")
    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        make_reader(root).capture()
    assert code(captured) == "unsafe_filesystem"


class FakeEntry:
    def __init__(self, name: str, info: os.stat_result) -> None:
        self.name = name
        self.info = info

    def stat(self, *, follow_symlinks: bool) -> os.stat_result:
        assert follow_symlinks is False
        return self.info


@pytest.mark.parametrize(
    ("kind", "count", "suffix"),
    [
        ("entries", pds.MAX_ENTRIES + 1, ".bin"),
        ("files", pds.MAX_FILES + 1, ".txt"),
        ("directories", pds.MAX_DIRECTORIES, ""),
    ],
)
def test_entry_file_and_directory_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    count: int,
    suffix: str,
) -> None:
    root = tmp_path / kind
    root.mkdir()
    sample = root / "sample.bin"
    sample.write_bytes(b"")
    info = os.lstat(root if kind == "directories" else sample)
    entries = [FakeEntry(f"entry-{index:05d}{suffix}", info) for index in range(count)]
    real_lstat = pds._lstat

    def fake_lstat(path: str, missing_reason: str = "missing") -> os.stat_result:
        if os.path.basename(str(path)).startswith("entry-"):
            return info
        return real_lstat(path, missing_reason)

    monkeypatch.setattr(pds, "_lstat", fake_lstat)
    monkeypatch.setattr(pds, "_scandir", lambda _, __: entries)
    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        make_reader(root).capture()
    assert code(captured) == "limit_exceeded"


class CountingIterator:
    def __init__(self, info: os.stat_result) -> None:
        self.info = info
        self.yielded = 0
        self.closed = False

    def __iter__(self) -> CountingIterator:
        return self

    def __next__(self) -> FakeEntry:
        self.yielded += 1
        return FakeEntry(f"entry-{self.yielded:05d}.bin", self.info)

    def close(self) -> None:
        self.closed = True


def test_scandir_stops_at_limit_plus_one_and_closes_iterator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "bounded-enumeration"
    root.mkdir()
    sample = root / "sample.bin"
    sample.write_bytes(b"")
    iterator = CountingIterator(os.lstat(sample))
    monkeypatch.setattr(pds.os, "scandir", lambda _: iterator)
    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        make_reader(root).capture()
    assert code(captured) == "limit_exceeded"
    assert iterator.yielded == pds.MAX_ENTRIES + 1
    assert iterator.closed is True

def test_size_total_depth_and_frozen_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert (
        pds.MAX_FILES, pds.MAX_ENTRIES, pds.MAX_DIRECTORIES,
        pds.MAX_RELATIVE_DEPTH, pds.MAX_RELATIVE_PATH_UTF8_BYTES,
        pds.MAX_FILE_BYTES, pds.MAX_TOTAL_FILE_BYTES,
    ) == (1_000, 10_000, 1_000, 32, 4_096, 8_388_608, 67_108_864)

    root = tmp_path / "oversize"
    root.mkdir()
    large = root / "large.txt"
    with large.open("wb") as stream:
        stream.seek(pds.MAX_FILE_BYTES)
        stream.write(b"x")
    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        make_reader(root).capture()
    assert code(captured) == "limit_exceeded"

    total_root = tmp_path / "total"
    total_root.mkdir()
    sample = total_root / "sample.txt"
    with sample.open("wb") as stream:
        stream.seek(pds.MAX_FILE_BYTES - 1)
        stream.write(b"x")
    info = os.lstat(sample)
    real_lstat = pds._lstat

    def fake_total_lstat(
        path: str,
        missing_reason: str = "missing",
    ) -> os.stat_result:
        if os.path.basename(str(path)).startswith("file-"):
            return info
        return real_lstat(path, missing_reason)

    monkeypatch.setattr(pds, "_lstat", fake_total_lstat)
    monkeypatch.setattr(
        pds, "_scandir",
        lambda _, __: [FakeEntry(f"file-{index}.txt", info) for index in range(9)],
    )
    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        make_reader(total_root).capture()
    assert code(captured) == "limit_exceeded"
    monkeypatch.undo()

    depth_root = tmp_path / "depth"
    depth_root.mkdir()
    current = depth_root
    for _ in range(pds.MAX_RELATIVE_DEPTH + 1):
        current = current / "d"
        current.mkdir()
    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        make_reader(depth_root).capture()
    assert code(captured) == "limit_exceeded"


@pytest.mark.parametrize("operation", ("read_exact", "observe_unavailable"))
def test_reentrant_failed_capture_revokes_existing_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    root = tmp_path / operation
    root.mkdir()
    (root / "note.txt").write_text("content", encoding="utf-8")
    reader = make_reader(root)
    descriptor = reader.capture().descriptors[0]
    real_read = pds.os.read
    nested_codes: list[str] = []

    def nested_capture(file_descriptor: int, amount: int) -> bytes:
        raw = real_read(file_descriptor, amount)
        if not nested_codes:
            with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
                reader.capture()
            nested_codes.append(code(captured))
        return raw

    monkeypatch.setattr(pds.os, "read", nested_capture)
    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        getattr(reader, operation)(descriptor)
    assert code(captured) == "authority_unavailable"
    assert nested_codes == ["operation_in_progress"]
    with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
        reader.read_exact(descriptor)
    assert code(captured) == "authority_unavailable"


def test_non_reentrant_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    (root / "note.txt").write_text("content", encoding="utf-8")
    reader = make_reader(root)
    real_scan = pds._scan_tree
    observed: list[str] = []

    def nested(*args: object, **kwargs: object) -> object:
        if not observed:
            with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
                reader.capture()
            observed.append(code(captured))
        return real_scan(*args, **kwargs)

    monkeypatch.setattr(pds, "_scan_tree", nested)
    assert reader.capture().descriptors
    assert observed == ["operation_in_progress"]


def test_all_descriptor_fields_tamper_fail_and_rows_are_frozen(
    tmp_path: Path,
) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    (root / "note.txt").write_text("content", encoding="utf-8")
    fields = (
        "relative_path", "locator", "mime_type", "extractor_profile", "byte_size",
        "char_size", "source_ref", "revision_ref", "extracted_content_hash",
        "binding_digest", "descriptor_digest",
    )
    last: pds.PersonalDocsFileDescriptor | None = None
    for name in fields:
        reader = make_reader(root)
        item = reader.capture().descriptors[0]
        last = item
        original = getattr(item, name)
        replacement: object = (
            original + 1 if name in {"byte_size", "char_size"}
            else object() if name == "locator"
            else "other.txt" if name == "relative_path"
            else "x"
        )
        object.__setattr__(item, name, replacement)
        with pytest.raises(pds.PersonalDocsOwnerReaderError) as captured:
            reader.read_exact(item)
        assert code(captured) == "invalid_descriptor"
    assert last is not None and not hasattr(last, "__dict__")
    with pytest.raises(dataclasses.FrozenInstanceError):
        last.relative_path = "cannot-write.txt"  # type: ignore[misc]


def test_ast_has_no_manager_provider_index_runtime_or_write_dependency() -> None:
    path = Path(pds.__file__)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", 1)[0])
    assert imported <= {
        "__future__", "dataclasses", "hashlib", "os", "re", "stat", "typing",
    }
    lowered = source.lower()
    for forbidden in (
        "personaldocsmanager", "rag_text_chunking", "unified_source_index",
        "requests", "subprocess", "socket", "sqlite", "chroma",
    ):
        assert forbidden not in lowered
    forbidden_calls = {
        "write", "write_text", "write_bytes", "mkdir", "makedirs",
        "remove", "unlink", "rename",
    }
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert called.isdisjoint(forbidden_calls)
