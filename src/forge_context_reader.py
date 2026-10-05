"""Immutable, read-only content reader for retained Forge bare repositories.

The reader deliberately accepts a version-bound inventory request and never
opens a worktree.  Git's NUL framed plumbing is parsed as bytes and every
returned range is tied to its tree object, blob type, size and SHA-256.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.repo_git_adapter import (
    FORGE_CONTENT_RANGE_SCHEMA_V2, ForgeContentRange, ForgeExactReaderReference,
    MAX_FORGE_FILE_BYTES, MAX_FORGE_SNAPSHOT_FILES, ForgeSnapshotAuthorityBinding,
    ForgeSnapshotError, ForgeSnapshotFile, ForgeSnapshotInventory, ForgeSnapshotRequest,
)

_HEX = re.compile(rb"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_PATH_BAD = re.compile(rb"(^/|\x00|\r|\n|\\|(^|/)\.\.(/|$))")
_ZERO_SHA256 = "sha256:" + "0" * 64


class ForgeContextReaderError(ValueError):
    """A fixed, non-diagnostic failure code from the Git boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class RetainedForgeVersion:
    repository: Path
    owner_scope: str
    repo_id: str
    version_id: str
    commit_sha: str
    manifest_sha256: str
    authority_binding: ForgeSnapshotAuthorityBinding


class ImmutableForgeContextReader:
    """Read exactly one verified commit from a bare Forge repository.

    ``RetainedForgeVersion`` is obtained by the route after it verifies the
    SID-owned manifest and retained ref.  This class does no registration,
    commit, provider, worktree or filesystem-source operation.
    """

    def __init__(self, retained: RetainedForgeVersion, *, timeout_seconds: int = 8) -> None:
        if type(retained) is not RetainedForgeVersion or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 8:
            raise ForgeContextReaderError("invalid_reader_request")
        self._retained = retained
        self._timeout = timeout_seconds
        # Inventories are deliberately keyed by the requested public paths.
        # A context packet must never make its selected code page depend on
        # hashing unrelated blobs merely because they share a commit.
        self._inventories: dict[tuple[str, ...] | None, ForgeSnapshotInventory] = {}
        self._last_inventory: ForgeSnapshotInventory | None = None
        self._objects: dict[str, str] = {}

    def inventory(self, request: ForgeSnapshotRequest, *, paths: tuple[str, ...] | list[str] | None = None) -> ForgeSnapshotInventory:
        self._match_request(request)
        requested = self._requested_paths(paths)
        cached = self._inventories.get(requested)
        if cached is not None:
            self._last_inventory = cached
            return cached
        commit = self._git(("cat-file", "-t", self._retained.commit_sha)).strip()
        if commit != b"commit":
            self._fail("immutable_commit_invalid")
        tree = self._git(("rev-parse", f"{self._retained.commit_sha}^{{tree}}"), max_output=128).strip()
        if not _HEX.fullmatch(tree):
            self._fail("immutable_tree_invalid")
        raw = self._git(("ls-tree", "-r", "-z", "-l", self._retained.commit_sha), max_output=32 * 1024 * 1024)
        if raw:
            if not raw.endswith(b"\0"):
                self._fail("git_framing_invalid")
            frames = raw.split(b"\0")
            if any(not frame for frame in frames[:-1]):
                self._fail("git_framing_invalid")
            frames = frames[:-1]
        else:
            frames = []
        files: list[ForgeSnapshotFile] = []
        available: dict[str, tuple[bytes, int, ForgeSnapshotFile]] = {}
        seen: set[str] = set()
        for frame in frames:
            try:
                meta, path = frame.split(b"\t", 1)
                mode, obj_type, object_id, size_field = meta.split(b" ", 3)
                text_path = path.decode("utf-8", "strict")
            except (UnicodeError, ValueError):
                self._fail("git_framing_invalid")
            size = size_field.lstrip(b" ")
            if not size or len(size) > 19 or any(byte < ord("0") or byte > ord("9") for byte in size):
                self._fail("git_framing_invalid")
            byte_count = int(size)
            if byte_count > MAX_FORGE_FILE_BYTES:
                self._fail("git_framing_invalid")
            if mode == b"120000":
                self._fail("symlink_not_readable")
            if mode == b"160000":
                self._fail("gitlink_not_readable")
            if mode not in {b"100644", b"100755"} or obj_type != b"blob" or not _HEX.fullmatch(object_id):
                self._fail("git_framing_invalid")
            if _PATH_BAD.search(path):
                self._fail("git_framing_invalid")
            canonical = _path(text_path)
            try:
                descriptor = ForgeSnapshotFile(canonical, _ZERO_SHA256, byte_count)
            except ForgeSnapshotError:
                self._fail("git_framing_invalid")
            if descriptor.path != canonical:
                self._fail("git_framing_invalid")
            key = canonical.casefold()
            if key in seen:
                self._fail("git_framing_invalid")
            seen.add(key)
            available[canonical] = (object_id, byte_count, descriptor)
        selected = tuple(sorted(available)) if requested is None else requested
        if len(selected) > MAX_FORGE_SNAPSHOT_FILES:
            self._fail("inventory_invalid")
        if requested is not None and any(path not in available for path in requested):
            self._fail("requested_path_invalid")
        for canonical in selected:
            object_id, byte_count, _descriptor = available[canonical]
            digest = self._blob_sha256(object_id, byte_count)
            try:
                files.append(ForgeSnapshotFile(canonical, digest, byte_count))
            except ForgeSnapshotError:
                self._fail("inventory_invalid")
            self._objects[canonical] = object_id.decode("ascii")
        try:
            inventory = ForgeSnapshotInventory(
                self._retained.owner_scope, self._retained.repo_id, self._retained.version_id,
                self._retained.commit_sha, self._retained.manifest_sha256,
                self._retained.authority_binding, tuple(files),
            )
            self._inventories[requested] = inventory
            self._last_inventory = inventory
            return inventory
        except ForgeSnapshotError:
            self._fail("inventory_invalid")

    def exact_reader_reference(self, request: ForgeSnapshotRequest, *, path: str, max_bytes: int) -> ForgeExactReaderReference:
        inventory = self._inventory_for_path(request, path)
        try:
            entry = inventory.file(path)
            return ForgeExactReaderReference(request.owner_scope, request.repo_id, request.version_id,
                request.commit_sha, inventory.snapshot_digest, entry.path, entry.content_sha256,
                max_bytes, request.authority_binding)
        except (ForgeSnapshotError, ValueError):
            self._fail("exact_reference_invalid")

    def read_exact_range(self, request: ForgeSnapshotRequest, *, path: str, offset: int, max_bytes: int) -> ForgeContentRange:
        if type(offset) is not int or type(max_bytes) is not int or offset < 0 or not 0 <= max_bytes <= 1_000_000:
            self._fail("range_invalid")
        inventory = self._inventory_for_path(request, path)
        try:
            entry = inventory.file(path)
        except ForgeSnapshotError:
            self._fail("range_invalid")
        if offset > entry.byte_count or (entry.byte_count and (offset == entry.byte_count or max_bytes == 0)):
            self._fail("range_invalid")
        if not entry.byte_count and (offset != 0 or max_bytes != 0):
            self._fail("range_invalid")
        length = min(max_bytes, entry.byte_count - offset)
        object_id = self._objects.get(entry.path)
        if object_id is None:
            self._fail("immutable_object_invalid")
        digest, content = self._stream_blob(object_id, entry.byte_count, offset=offset, length=length)
        if digest != entry.content_sha256:
            self._fail("hash_mismatch")
        if len(content) != length:
            self._fail("short_read")
        return ForgeContentRange(FORGE_CONTENT_RANGE_SCHEMA_V2, request.owner_scope, request.repo_id,
            request.version_id, request.commit_sha, inventory.snapshot_digest, entry.path,
            entry.content_sha256, entry.byte_count, offset, content, request.authority_binding)

    def _blob_sha256(self, object_id: bytes, expected_size: int) -> str:
        digest, _ = self._stream_blob(object_id.decode("ascii"), expected_size, offset=0, length=0)
        return digest

    def _stream_blob(self, object_id: str, expected_size: int, *, offset: int, length: int) -> tuple[str, bytes]:
        """Hash one blob in bounded chunks while retaining only its selected range."""
        digest = hashlib.sha256()
        selected = bytearray()
        position = 0

        def consume(chunk: bytes) -> bool:
            nonlocal position
            digest.update(chunk)
            chunk_end = position + len(chunk)
            if length and chunk_end > offset and position < offset + length:
                selected.extend(chunk[max(0, offset - position):min(len(chunk), offset + length - position)])
            position = chunk_end
            # Git metadata has already supplied the exact size; do not let a
            # hostile or corrupt cat-file stream run beyond that bound.
            return position <= expected_size

        self._run_git_stream(("cat-file", "blob", object_id), consume, output_limit=expected_size)
        if position != expected_size:
            self._fail("short_read")
        return "sha256:" + digest.hexdigest(), bytes(selected)

    def _match_request(self, request: ForgeSnapshotRequest) -> None:
        r = self._retained
        if type(request) is not ForgeSnapshotRequest or (
            request.owner_scope, request.repo_id, request.version_id, request.commit_sha,
            request.authority_binding.to_dict(),
        ) != (r.owner_scope, r.repo_id, r.version_id, r.commit_sha, r.authority_binding.to_dict()):
            self._fail("immutable_request_mismatch")

    def _git(self, arguments: tuple[str, ...], *, max_output: int = 1_000_001) -> bytes:
        repo = self._retained.repository
        if not repo.is_dir() or not (repo / "HEAD").exists():
            self._fail("bare_repository_unavailable")
        output = bytearray()

        def consume(chunk: bytes) -> bool:
            output.extend(chunk)
            return len(output) <= max_output

        self._run_git_stream(arguments, consume, output_limit=max_output)
        return bytes(output)

    def _inventory_for_path(self, request: ForgeSnapshotRequest, path: str) -> ForgeSnapshotInventory:
        """Use the active selected-path inventory, or create a one-path one."""
        # Never return a cached inventory until the complete immutable request
        # has been re-bound.  A reader can outlive a route attempt and cached
        # bytes must not be relabelled under a different owner/version/commit.
        self._match_request(request)
        try:
            canonical = _path(path)
        except ForgeContextReaderError:
            self._fail("range_invalid")
        current = self._last_inventory
        if current is not None:
            try:
                current.file(canonical)
                return current
            except ForgeSnapshotError:
                pass
        return self.inventory(request, paths=(canonical,))

    @staticmethod
    def _requested_paths(paths: tuple[str, ...] | list[str] | None) -> tuple[str, ...] | None:
        if paths is None:
            return None
        if type(paths) not in {tuple, list} or not 1 <= len(paths) <= 64:
            raise ForgeContextReaderError("requested_path_invalid")
        try:
            result = tuple(sorted(_path(value) for value in paths))
        except ForgeContextReaderError:
            raise ForgeContextReaderError("requested_path_invalid") from None
        if len(result) != len(set(result)) or len(result) > 64:
            raise ForgeContextReaderError("requested_path_invalid")
        return result

    def _run_git_stream(self, arguments: tuple[str, ...], consume: Any, *, output_limit: int | None = None) -> None:
        """Spool stdout then parse it with a deadline and bounded memory.

        The process-owning thread polls the OS pipe itself.  It first writes a
        seekable binary temporary spool, then feeds bounded chunks to the
        caller.  Thus no reader thread can survive an exceptional close and
        blob content never becomes one whole in-memory response.
        """
        process: Any | None = None
        stream: Any | None = None
        spool: Any | None = None
        selector: Any | None = None
        output_size = 0
        failure: BaseException | None = None
        reap_failure: BaseException | None = None
        termination_observed = False
        deadline = time.monotonic() + self._timeout
        try:
            spool = tempfile.TemporaryFile(mode="w+b")
            process = subprocess.Popen(
                ("git", "--no-replace-objects", "--git-dir", os.fspath(self._retained.repository), *arguments),
                shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            )
            stream = process.stdout
            if stream is None:
                raise OSError("missing stdout")
            if os.name == "nt":
                # Windows select() does not accept anonymous process pipes.
                # PeekNamedPipe lets this same owner poll their byte count
                # without a reader thread, then read only ready bytes.
                while True:
                    if deadline - time.monotonic() <= 0:
                        raise subprocess.TimeoutExpired(arguments, self._timeout)
                    exited = getattr(process, "poll", lambda: None)()
                    available = _windows_pipe_available(stream)
                    if available is None:
                        if exited is None:
                            time.sleep(0.01)
                            continue
                        chunk = (getattr(stream, "read1", None) or stream.read)(64 * 1024)
                    elif available:
                        chunk = (getattr(stream, "read1", None) or stream.read)(min(64 * 1024, available))
                    elif exited is not None:
                        break
                    else:
                        time.sleep(0.01)
                        continue
                    if not isinstance(chunk, bytes):
                        raise OSError("invalid stdout")
                    if not chunk:
                        if exited is not None:
                            break
                        continue
                    output_size += len(chunk)
                    if output_limit is not None and output_size > output_limit:
                        raise OSError("output bound exceeded")
                    spool.write(chunk)
            else:
                import selectors

                selector = selectors.DefaultSelector()
                selector.register(stream, selectors.EVENT_READ)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(arguments, self._timeout)
                    events = selector.select(timeout=min(remaining, 0.05))
                    if not events:
                        continue
                    for key, _event in events:
                        chunk = os.read(key.fd, 64 * 1024)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        output_size += len(chunk)
                        if output_limit is not None and output_size > output_limit:
                            raise OSError("output bound exceeded")
                        spool.write(chunk)
            remaining = deadline - time.monotonic()
            if remaining <= 0 or process.wait(timeout=remaining) != 0:
                raise subprocess.TimeoutExpired(arguments, self._timeout)
            spool.seek(0)
            while True:
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(arguments, self._timeout)
                chunk = spool.read(64 * 1024)
                if not chunk:
                    break
                if consume(chunk) is not True:
                    raise OSError("output bound exceeded")
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(arguments, self._timeout)
        except BaseException as exc:
            failure = exc
        finally:
            # Reap before the spool is closed on every error path, including
            # interrupts and failures raised by a consumer callback.  A
            # failed wait remains a failed operation; it is never treated as
            # a successful cleanup merely because kill() returned.
            if process is None:
                termination_observed = True
            elif failure is None:
                # The bounded process.wait above completed successfully.
                termination_observed = True
            if process is not None and failure is not None:
                try:
                    self._terminate_and_reap(process)
                    termination_observed = True
                except BaseException as exc:
                    reap_failure = exc
            if selector is not None:
                selector.close()
            if stream is not None and termination_observed:
                try:
                    stream.close()
                except OSError:
                    pass
            if spool is not None and termination_observed:
                spool.close()
            elif spool is not None and reap_failure is None:
                reap_failure = OSError("git_process_reap_failed")
        if failure is not None or reap_failure is not None:
            self._fail("git_subprocess_failed")

    @staticmethod
    def _terminate_process_tree(process: Any) -> None:
        pid = getattr(process, "pid", None)
        if os.name == "nt" and type(pid) is int and pid > 0:
            try:
                subprocess.run(
                    ("taskkill", "/PID", str(pid), "/T", "/F"),
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    check=False, timeout=1,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        try:
            process.kill()
        except (OSError, ProcessLookupError):
            pass

    def _terminate_and_reap(self, process: Any) -> None:
        self._terminate_process_tree(process)
        try:
            result = process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired) as exc:
            # A tree termination can race the first bounded wait.  Retry the
            # tree termination and wait once more before declaring cleanup
            # unverified; callers close resources only after this returns.
            self._terminate_process_tree(process)
            try:
                result = process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired) as retry_exc:
                raise OSError("git_process_reap_failed") from retry_exc
        if result is None:
            raise OSError("git_process_reap_failed")
        stream = getattr(process, "stdout", None)
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass

    @staticmethod
    def _fail(code: str) -> None:
        raise ForgeContextReaderError(code)


def _path(value: str) -> str:
    if type(value) is not str or not value or "\\" in value or value.startswith("/") or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ForgeContextReaderError("git_framing_invalid")
    return value


def _windows_pipe_available(stream: Any) -> int | None:
    """Return immediately-readable Windows pipe bytes, or None for a test double."""
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes

        available = wintypes.DWORD()
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(stream.fileno()))
        if not ctypes.windll.kernel32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
            raise OSError("PeekNamedPipe failed")
        return int(available.value)
    except (AttributeError, OSError, TypeError, ValueError):
        return None
