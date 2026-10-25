import hashlib
import subprocess
import threading
import time
from pathlib import Path

import pytest

from src.forge_context_reader import ForgeContextReaderError, ImmutableForgeContextReader, RetainedForgeVersion
from src.forge_context_pack import ForgeContextAuthority, ForgeContextPackComposer, REQUEST_SCHEMA
from src.repo_git_adapter import ForgeSnapshotAuthorityBinding, ForgeSnapshotRequest


def _reader(tmp_path: Path):
    bare = tmp_path / "bare.git"
    work = tmp_path / "work"
    subprocess.run(("git", "init", str(work)), check=True, capture_output=True)
    subprocess.run(("git", "-C", str(work), "config", "user.email", "test@example.invalid"), check=True)
    subprocess.run(("git", "-C", str(work), "config", "user.name", "Test"), check=True)
    (work / "code.py").write_bytes(b"hello immutable forge")
    (work / "unrequested.bin").write_bytes(b"private-but-public-repository-fixture" * 128)
    subprocess.run(("git", "-C", str(work), "add", "code.py", "unrequested.bin"), check=True)
    subprocess.run(("git", "-C", str(work), "commit", "-m", "fixture"), check=True, capture_output=True)
    commit = subprocess.run(("git", "-C", str(work), "rev-parse", "HEAD"), check=True, capture_output=True, text=True).stdout.strip()
    subprocess.run(("git", "clone", "--bare", str(work), str(bare)), check=True, capture_output=True)
    binding = ForgeSnapshotAuthorityBinding("forge.code", "v1", "usi_generation_" + "a" * 64, "fca.forge_code.admission.v1")
    retained = RetainedForgeVersion(bare, "owner:alice", "demo", "pv_" + "a" * 32, commit, "sha256:" + "b" * 64, binding)
    request = ForgeSnapshotRequest("owner:alice", "opaque", "demo", retained.version_id, commit, binding)
    return ImmutableForgeContextReader(retained), request


def test_inventory_and_range_are_bound_to_one_bare_commit(tmp_path):
    reader, request = _reader(tmp_path)
    inventory = reader.inventory(request)
    assert inventory.files[0].path == "code.py"
    assert inventory.files[0].content_sha256 == "sha256:" + hashlib.sha256(b"hello immutable forge").hexdigest()
    page = reader.read_exact_range(request, path="code.py", offset=0, max_bytes=5)
    assert page.content == b"hello"


def test_reader_rejects_mismatched_immutable_request(tmp_path):
    reader, request = _reader(tmp_path)
    wrong = ForgeSnapshotRequest(request.owner_scope, "opaque", request.repo_id, request.version_id, "0" * 40, request.authority_binding)
    with pytest.raises(ValueError, match="immutable_request_mismatch"):
        reader.inventory(wrong)


def test_cached_inventory_is_never_relabelled_for_a_foreign_request(tmp_path):
    reader, request = _reader(tmp_path)
    reader.inventory(request, paths=("code.py",))
    foreign = ForgeSnapshotRequest("owner:bob", request.authorization_ref, request.repo_id,
                                  request.version_id, request.commit_sha, request.authority_binding)
    with pytest.raises(ValueError, match="immutable_request_mismatch"):
        reader.read_exact_range(foreign, path="code.py", offset=0, max_bytes=1)


def test_reader_accepts_executable_and_empty_files_and_keeps_inventory_stable(tmp_path):
    reader, request = _reader(tmp_path)
    work = tmp_path / "work"
    (work / "run.sh").write_bytes(b"#!/bin/sh\necho retained\n")
    (work / "empty.txt").write_bytes(b"")
    subprocess.run(("git", "-C", str(work), "add", "run.sh", "empty.txt"), check=True)
    subprocess.run(("git", "-C", str(work), "update-index", "--chmod=+x", "run.sh"), check=True)
    subprocess.run(("git", "-C", str(work), "commit", "-m", "executable and empty"), check=True, capture_output=True)

    # Existing readers remain bound to their retained commit, while a later
    # retained commit can expose a normal executable and a valid empty blob.
    assert [entry.path for entry in reader.inventory(request).files] == ["code.py", "unrequested.bin"]
    bare = tmp_path / "later.git"
    subprocess.run(("git", "clone", "--bare", str(work), str(bare)), check=True, capture_output=True)
    commit = subprocess.run(("git", "-C", str(work), "rev-parse", "HEAD"), check=True, capture_output=True, text=True).stdout.strip()
    binding = request.authority_binding
    retained = RetainedForgeVersion(bare, request.owner_scope, request.repo_id, request.version_id,
                                    commit, "sha256:" + "b" * 64, binding)
    later_request = ForgeSnapshotRequest(request.owner_scope, "opaque", request.repo_id,
                                         request.version_id, commit, binding)
    later = ImmutableForgeContextReader(retained)
    assert [entry.path for entry in later.inventory(later_request).files] == ["code.py", "empty.txt", "run.sh", "unrequested.bin"]
    empty = later.read_exact_range(later_request, path="empty.txt", offset=0, max_bytes=0)
    assert empty.content == b"" and empty.file_byte_count == 0


def test_reader_streams_only_requested_range_after_inventory(tmp_path):
    reader, request = _reader(tmp_path)
    inventory = reader.inventory(request)
    first = reader.read_exact_range(request, path=inventory.files[0].path, offset=0, max_bytes=5)
    second = reader.read_exact_range(request, path=inventory.files[0].path, offset=5, max_bytes=5)
    assert first.content + second.content == b"hello immu"


def test_selected_inventory_never_hashes_unrequested_blob_content(tmp_path, monkeypatch):
    reader, request = _reader(tmp_path)
    seen = []
    original = reader._blob_sha256

    def record(object_id, expected_size):
        seen.append((object_id, expected_size))
        return original(object_id, expected_size)

    monkeypatch.setattr(reader, "_blob_sha256", record)
    inventory = reader.inventory(request, paths=("code.py",))
    assert [entry.path for entry in inventory.files] == ["code.py"]
    assert len(seen) == 1


def _framed_metadata_reader(tmp_path, monkeypatch, raw):
    reader, request = _reader(tmp_path)
    git_calls = []
    hashed = []

    def fake_git(arguments, *, max_output=1_000_001):
        del max_output
        git_calls.append(arguments)
        if arguments[:2] == ("cat-file", "-t"):
            return b"commit"
        if arguments[:3] == ("ls-tree", "-r", "-z"):
            return raw
        raise AssertionError(arguments)

    monkeypatch.setattr(reader, "_git", fake_git)
    monkeypatch.setattr(
        reader,
        "_blob_sha256",
        lambda object_id, expected_size: hashed.append((object_id, expected_size)) or "sha256:" + "c" * 64,
    )
    return reader, request, git_calls, hashed


def test_inventory_uses_commit_type_and_tree_listing_without_redundant_tree_resolution(tmp_path, monkeypatch):
    raw = (
        b"100644 blob " + b"a" * 40 + b" 1\tselected.txt\0"
        + b"100644 blob " + b"b" * 40 + b" 2\tunselected.txt\0"
    )
    reader, request, git_calls, hashed = _framed_metadata_reader(tmp_path, monkeypatch, raw)

    inventory = reader.inventory(request, paths=("selected.txt",))

    assert git_calls == [
        ("cat-file", "-t", request.commit_sha),
        ("ls-tree", "-r", "-z", "-l", request.commit_sha),
    ]
    assert [entry.path for entry in inventory.files] == ["selected.txt"]
    assert hashed == [(b"a" * 40, 1)]

    wrong_type_reader = ImmutableForgeContextReader(reader._retained)
    wrong_type_calls = []
    wrong_type_hashed = []

    def wrong_type_git(arguments, *, max_output=1_000_001):
        del max_output
        wrong_type_calls.append(arguments)
        if arguments[:2] == ("cat-file", "-t"):
            return b"tree"
        raise AssertionError(arguments)

    monkeypatch.setattr(wrong_type_reader, "_git", wrong_type_git)
    monkeypatch.setattr(
        wrong_type_reader,
        "_blob_sha256",
        lambda object_id, expected_size: wrong_type_hashed.append((object_id, expected_size)) or "sha256:" + "c" * 64,
    )
    with pytest.raises(ForgeContextReaderError) as exc_info:
        wrong_type_reader.inventory(request, paths=("selected.txt",))
    assert exc_info.value.code == "immutable_commit_invalid"
    assert wrong_type_calls == [("cat-file", "-t", request.commit_sha)]
    assert wrong_type_hashed == []


@pytest.mark.parametrize("object_id", [b"a" * 40, b"b" * 64])
def test_reader_accepts_exact_40_or_64_hex_object_ids_before_hash(tmp_path, monkeypatch, object_id):
    raw = b"100644 blob " + object_id + b" 1\texact.txt\0"
    reader, request, _git_calls, hashed = _framed_metadata_reader(tmp_path, monkeypatch, raw)
    inventory = reader.inventory(request)
    assert inventory.files[0].path == "exact.txt"
    assert len(hashed) == 1


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"100644 blob " + b"a" * 40 + b" 1\texact.txt", "git_framing_invalid"),
        (b"100644 blob " + b"a" * 40 + b" 1\texact.txt\0\0", "git_framing_invalid"),
        (b"100644 blob " + b"a" * 41 + b" 1\texact.txt\0", "git_framing_invalid"),
        (b"100644 blob " + b"a" * 63 + b" 1\texact.txt\0", "git_framing_invalid"),
        (b"100644 blob " + b"a" * 40 + b" 1\tbad\rname\0", "git_framing_invalid"),
        (b"100644 blob " + b"a" * 40 + b" 1\tbad\nname\0", "git_framing_invalid"),
        (b"100644 blob " + b"a" * 40 + b" 1\tbad\x00name\0", "git_framing_invalid"),
        (b"100644 blob " + b"a" * 40 + b" 1\tbad\\name\0", "git_framing_invalid"),
        ("100644 blob ".encode() + b"a" * 40 + " 1\te\u0301.txt\0".encode(), "git_framing_invalid"),
        (b"100644 blob " + b"a" * 40 + b" 1\tCON.txt\0", "git_framing_invalid"),
        (b"120000 blob " + b"a" * 40 + b" 1\tlink.txt\0", "symlink_not_readable"),
        (b"160000 commit " + b"a" * 40 + b" 1\tgitlink\0", "gitlink_not_readable"),
        (b"100644 blob " + b"a" * 40 + b" 18446744073709551616\tlarge.txt\0", "git_framing_invalid"),
        (b"100644 blob " + b"a" * 40 + b" -1\tnegative.txt\0", "git_framing_invalid"),
        (b"100644 blob " + b"a" * 40 + b" +1\tplus.txt\0", "git_framing_invalid"),
    ],
)
def test_reader_rejects_malformed_metadata_before_any_blob_hash(tmp_path, monkeypatch, raw, code):
    reader, request, _git_calls, hashed = _framed_metadata_reader(tmp_path, monkeypatch, raw)
    with pytest.raises(ForgeContextReaderError, match=code):
        reader.inventory(request)
    assert hashed == []


def test_reader_rejects_unselected_inventory_overflow_but_hashes_selected_subset(tmp_path, monkeypatch):
    frames = [b"100644 blob " + (f"{index:040x}".encode()) + b" 1\tfile%03d.txt\0" % index for index in range(513)]
    raw = b"".join(frames)
    reader, request, _git_calls, hashed = _framed_metadata_reader(tmp_path, monkeypatch, raw)
    with pytest.raises(ForgeContextReaderError, match="inventory_invalid"):
        reader.inventory(request)
    assert hashed == []

    selected_root = tmp_path / "selected-subset"
    selected_root.mkdir()
    reader, request, _git_calls, hashed = _framed_metadata_reader(selected_root, monkeypatch, raw)
    inventory = reader.inventory(request, paths=("file000.txt",))
    assert [entry.path for entry in inventory.files] == ["file000.txt"]
    assert len(hashed) == 1


def test_reader_rejects_65_requested_paths_before_git(tmp_path, monkeypatch):
    reader, request = _reader(tmp_path)
    git_calls = []
    monkeypatch.setattr(reader, "_git", lambda *args, **kwargs: git_calls.append((args, kwargs)))
    with pytest.raises(ForgeContextReaderError, match="requested_path_invalid"):
        reader.inventory(request, paths=tuple(f"file{index}.txt" for index in range(65)))
    assert git_calls == []


@pytest.mark.parametrize("max_bytes", [1, 1_000_000])
def test_exact_reader_reference_accepts_bounded_max_bytes(tmp_path, max_bytes):
    reader, request = _reader(tmp_path)
    reference = reader.exact_reader_reference(request, path="code.py", max_bytes=max_bytes)
    assert reference.max_bytes == max_bytes


@pytest.mark.parametrize("max_bytes", [0, 1_000_001])
def test_exact_reader_reference_rejects_out_of_bounds_max_bytes(tmp_path, max_bytes):
    reader, request = _reader(tmp_path)
    with pytest.raises(ForgeContextReaderError, match="exact_reference_invalid"):
        reader.exact_reader_reference(request, path="code.py", max_bytes=max_bytes)


def test_reader_reaps_a_hung_pipe_before_the_deadline(tmp_path, monkeypatch):
    reader, _request = _reader(tmp_path)
    reader = ImmutableForgeContextReader(reader._retained, timeout_seconds=1)
    released = threading.Event()
    processes = []

    class Pipe:
        def read1(self, _size):
            released.wait(5)
            return b""

        def close(self):
            released.set()

    class Process:
        def __init__(self):
            self.stdout = Pipe()

        def kill(self):
            self.killed = True
            released.set()

        def wait(self, timeout=None):
            return 0

    def fake_popen(*_args, **_kwargs):
        process = Process()
        processes.append(process)
        return process

    monkeypatch.setattr("src.forge_context_reader.subprocess.Popen", fake_popen)
    started = time.monotonic()
    with pytest.raises(ForgeContextReaderError, match="git_subprocess_failed"):
        reader._run_git_stream(("rev-parse", "HEAD"), lambda _chunk: True)
    assert processes[0].killed is True
    assert time.monotonic() - started < 3


def test_reader_wait_timeout_reaps_before_closing_spool(tmp_path, monkeypatch):
    reader, _request = _reader(tmp_path)
    events = []

    class Spool:
        def write(self, _chunk):
            return None

        def seek(self, _offset):
            return None

        def read(self, _size):
            return b""

        def close(self):
            events.append("spool_close")

    class Pipe:
        def read1(self, _size):
            return b""

        def close(self):
            events.append("stream_close")

    class Process:
        pid = 991337

        def __init__(self):
            self.stdout = Pipe()
            self.wait_calls = 0

        def poll(self):
            return 0

        def wait(self, timeout=None):
            del timeout
            self.wait_calls += 1
            if self.wait_calls == 1:
                events.append("bounded_wait_timeout")
                raise subprocess.TimeoutExpired(("git",), 1)
            if self.wait_calls == 2:
                events.append("reap_timeout")
                raise subprocess.TimeoutExpired(("git",), 1)
            events.append("definitive_reap")
            return 0

        def kill(self):
            events.append("kill")

    process = Process()
    monkeypatch.setattr("src.forge_context_reader.os.name", "nt")
    monkeypatch.setattr("src.forge_context_reader.tempfile.TemporaryFile", lambda **_kwargs: Spool())
    monkeypatch.setattr("src.forge_context_reader.subprocess.Popen", lambda *_a, **_k: process)
    def terminate_tree(value):
        events.append("tree_terminate")
        value.kill()

    monkeypatch.setattr(reader, "_terminate_process_tree", terminate_tree)
    with pytest.raises(ForgeContextReaderError, match="git_subprocess_failed"):
        reader._run_git_stream(("rev-parse", "HEAD"), lambda _chunk: True)

    assert events.index("bounded_wait_timeout") < events.index("kill")
    assert events.index("reap_timeout") < events.index("definitive_reap")
    assert events.index("kill") < events.index("definitive_reap")
    assert events.index("definitive_reap") < events.index("stream_close")
    assert events.index("stream_close") < events.index("spool_close")


def test_reader_keeps_spool_open_when_reap_is_unobserved(tmp_path, monkeypatch):
    reader, _request = _reader(tmp_path)
    events = []

    class Spool:
        def write(self, _chunk):
            return None

        def close(self):
            events.append("spool_close")

    class Pipe:
        def read1(self, _size):
            return b""

        def close(self):
            events.append("stream_close")

    class Process:
        stdout = Pipe()

        def poll(self):
            return 0

        def wait(self, timeout=None):
            del timeout
            raise subprocess.TimeoutExpired(("git",), 1)

        def kill(self):
            events.append("kill")

    process = Process()
    monkeypatch.setattr("src.forge_context_reader.os.name", "nt")
    monkeypatch.setattr("src.forge_context_reader.tempfile.TemporaryFile", lambda **_kwargs: Spool())
    monkeypatch.setattr("src.forge_context_reader.subprocess.Popen", lambda *_a, **_k: process)
    monkeypatch.setattr(reader, "_terminate_process_tree", lambda _value: events.append("tree_terminate"))
    with pytest.raises(ForgeContextReaderError, match="git_subprocess_failed"):
        reader._run_git_stream(("rev-parse", "HEAD"), lambda _chunk: True)

    assert events.count("tree_terminate") == 2
    assert "stream_close" not in events
    assert "spool_close" not in events


@pytest.mark.parametrize("failure_case", ["nonzero_git", "output_limit", "consumer_exception"])
def test_reader_fixed_git_failure_reaps_before_closing_resources(tmp_path, monkeypatch, failure_case):
    reader, _request = _reader(tmp_path)
    events = []

    class Spool:
        def __init__(self):
            self.payload = b"abcdef"
            self.read_calls = 0

        def write(self, chunk):
            self.payload = chunk

        def seek(self, _offset):
            return None

        def read(self, _size):
            self.read_calls += 1
            return self.payload if self.read_calls == 1 else b""

        def close(self):
            events.append("spool_close")

    class Pipe:
        def __init__(self):
            self.calls = 0

        def read1(self, _size):
            self.calls += 1
            return b"" if failure_case == "nonzero_git" or self.calls > 1 else b"abcdef"

        def close(self):
            events.append("stream_close")

    class Process:
        def __init__(self):
            self.stdout = Pipe()
            self.killed = False

        def poll(self):
            return 1 if failure_case == "nonzero_git" else 0

        def wait(self, timeout=None):
            del timeout
            events.append("cleanup_reap" if self.killed else "bounded_wait")
            return 1 if failure_case == "nonzero_git" else 0

        def kill(self):
            self.killed = True
            events.append("kill")

    process = Process()
    monkeypatch.setattr("src.forge_context_reader.os.name", "nt")
    monkeypatch.setattr("src.forge_context_reader.tempfile.TemporaryFile", lambda **_kwargs: Spool())
    monkeypatch.setattr("src.forge_context_reader.subprocess.Popen", lambda *_a, **_k: process)

    def terminate_tree(value):
        events.append("tree_terminate")
        value.kill()

    monkeypatch.setattr(reader, "_terminate_process_tree", terminate_tree)
    consume = (
        (lambda _chunk: (_ for _ in ()).throw(RuntimeError("consumer failed")))
        if failure_case == "consumer_exception"
        else (lambda _chunk: True)
    )
    output_limit = 3 if failure_case == "output_limit" else None
    with pytest.raises(ForgeContextReaderError, match="git_subprocess_failed"):
        reader._run_git_stream(("rev-parse", "HEAD"), consume, output_limit=output_limit)
    assert events.index("tree_terminate") < events.index("kill")
    assert events.index("kill") < events.index("cleanup_reap")
    assert events.index("cleanup_reap") < events.index("stream_close")
    assert events.index("stream_close") < events.index("spool_close")
    assert events.count("cleanup_reap") == 1


def test_reader_rejects_short_binary_stream_without_returning_partial_content(tmp_path, monkeypatch):
    reader, _request = _reader(tmp_path)

    class Pipe:
        def __init__(self): self.calls = 0
        def read1(self, _size):
            self.calls += 1
            return b"\x00\xff" if self.calls == 1 else b""
        def close(self): pass

    class Process:
        stdout = Pipe()
        def poll(self): return 0
        def wait(self, timeout=None): return 0
        def kill(self): pass

    monkeypatch.setattr("src.forge_context_reader.subprocess.Popen", lambda *_a, **_k: Process())
    with pytest.raises(ForgeContextReaderError, match="short_read"):
        reader._stream_blob("a" * 40, 3, offset=0, length=3)


def test_real_binary_reader_resumes_a_typed_pack_cursor(tmp_path):
    reader, request = _reader(tmp_path)
    authority = ForgeContextAuthority("alice", 1, request, reader, None)
    body = {"schema": REQUEST_SCHEMA, "version_id": request.version_id,
            "commit_sha": request.commit_sha, "manifest_sha256": "sha256:" + "b" * 64,
            "code_paths": ["code.py", "unrequested.bin"],
            "planning": {"project_id": "p", "roadmap_id": "r", "revision_id": "1",
                         "content_sha256": "sha256:" + "c" * 64, "head_generation": 1},
            "page_bytes": 3, "cursor": None}
    composer = ForgeContextPackComposer(cursor_key=b"x" * 32)
    first = composer.compose(authority, body)
    body["cursor"] = first["next_cursor"]
    resumed = composer.compose(
        ForgeContextAuthority("alice", 1, request, ImmutableForgeContextReader(reader._retained), None), body
    )
    assert first["code"][0]["offset"] == 0
    assert resumed["code"][0]["offset"] == 3
