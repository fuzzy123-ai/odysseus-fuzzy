"""Bounded, read-only exact reader for one Personal Docs owner/root binding."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass, field
from typing import Callable, TypeVar


MAX_FILES = 1_000
MAX_ENTRIES = 10_000
MAX_DIRECTORIES = 1_000
MAX_RELATIVE_DEPTH = 32
MAX_RELATIVE_PATH_UTF8_BYTES = 4_096
MAX_FILE_BYTES = 8 * 1_024 * 1_024
MAX_TOTAL_FILE_BYTES = 64 * 1_024 * 1_024
MAX_OWNER_ID_UTF8_BYTES = 256
MAX_ROOT_PATH_UTF8_BYTES = 4_096
PERSONAL_DOCS_TEXT_PROFILE = "personal_docs.text.strict_utf8.v1"

_SHA256_REF = re.compile(r"^sha256:[0-9a-f]{64}$")
_SOURCE_REF = re.compile(r"^personal-docs:file:[0-9a-f]{64}$")
_MIMES = {
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json",
}
_ROOT_EXCLUSIONS = frozenset({"indexed_directories.json", "excluded_files.json"})
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)
_ERROR_CODES = frozenset(
    {
        "invalid_request", "invalid_root", "invalid_descriptor", "invalid_snapshot",
        "operation_in_progress", "authority_unavailable", "capture_failed",
        "access_denied", "unsafe_filesystem", "filesystem_changed", "limit_exceeded",
        "invalid_utf8", "missing", "unsafe_or_replaced", "changed", "still_available",
    }
)


class PersonalDocsOwnerReaderError(ValueError):
    """Content-free public failure."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        safe = code if type(code) is str and code in _ERROR_CODES else "capture_failed"
        self.code = safe
        super().__init__(safe)

    def __repr__(self) -> str:
        return f"PersonalDocsOwnerReaderError({self.code!r})"


class _Failure(Exception):
    __slots__ = ("reason",)

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__()


def _fail(reason: str) -> None:
    raise _Failure(reason)


def _raise_public(reason: str) -> None:
    raise PersonalDocsOwnerReaderError(reason) from None


def _ascii_lower(value: str) -> str:
    return "".join(
        chr(ord(character) + 32) if "A" <= character <= "Z" else character
        for character in value
    )


def _strict_text(value: object, maximum: int, reason: str) -> tuple[str, bytes]:
    if type(value) is not str or not value:
        _fail(reason)
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeError:
        _fail(reason)
    if len(encoded) > maximum:
        _fail(reason)
    return value, encoded


def _has_control(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _root_path(value: object) -> str:
    root, encoded = _strict_text(value, MAX_ROOT_PATH_UTF8_BYTES, "invalid_root")
    if not encoded or _has_control(root) or "\x00" in root:
        _fail("invalid_root")
    windows_form = _ascii_lower(root.replace("/", "\\"))
    if (
        windows_form.startswith("\\\\")
        or windows_form.startswith("\\?\\")
        or windows_form.startswith("\\.\\")
    ):
        _fail("invalid_root")
    segments = root.replace("\\", "/").split("/")
    if any(segment in {".", ".."} for segment in segments):
        _fail("invalid_root")
    if not os.path.isabs(root) or os.path.normpath(root) != root:
        _fail("invalid_root")
    drive, tail = os.path.splitdrive(root)
    if drive and not tail.startswith(("/", "\\")):
        _fail("invalid_root")
    if os.name == "nt":
        for segment in segments:
            stem = _ascii_lower(segment.split(".", 1)[0].rstrip(" "))
            if stem in _WINDOWS_RESERVED:
                _fail("invalid_root")
    return root


def _relative_path(value: object) -> tuple[str, bytes]:
    relative, encoded = _strict_text(
        value, MAX_RELATIVE_PATH_UTF8_BYTES, "invalid_descriptor"
    )
    parts = relative.split("/")
    if (
        relative.startswith("/")
        or "\\" in relative
        or _has_control(relative)
        or len(parts) > MAX_RELATIVE_DEPTH
        or any(part in {"", ".", ".."} for part in parts)
    ):
        _fail("invalid_descriptor")
    return relative, encoded


def _sha(value: object, reason: str = "invalid_descriptor") -> str:
    if type(value) is not str or _SHA256_REF.fullmatch(value) is None:
        _fail(reason)
    return value


def _source(value: object, reason: str = "invalid_descriptor") -> str:
    if type(value) is not str or _SOURCE_REF.fullmatch(value) is None:
        _fail(reason)
    return value


def _frame(*values: bytes) -> bytes:
    return b"".join(len(value).to_bytes(8, "big") + value for value in values)


def _digest(domain: str, *values: bytes) -> str:
    return "sha256:" + hashlib.sha256(
        _frame(domain.encode("ascii"), *values)
    ).hexdigest()


def _integer(value: int) -> bytes:
    return str(value).encode("ascii")


def _make_source(owner_id: str, root_ref: str, relative_path: str) -> str:
    digest = hashlib.sha256(
        _frame(
            b"personal-docs:file", b"v1", owner_id.encode("utf-8"),
            root_ref.encode("ascii"), relative_path.encode("utf-8"),
        )
    ).hexdigest()
    return "personal-docs:file:" + digest


def _revision(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _content_hash(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True, repr=False)
class PersonalDocsWholeFileLocator:
    source_ref: str
    revision_ref: str
    start_char: int
    end_char: int
    locator_digest: str = ""

    def __post_init__(self) -> None:
        failure: str | None = None
        try:
            source_ref = _source(self.source_ref)
            revision_ref = _sha(self.revision_ref)
            if type(self.start_char) is not int or self.start_char != 0:
                _fail("invalid_descriptor")
            if type(self.end_char) is not int or self.end_char < 0:
                _fail("invalid_descriptor")
            expected = _digest(
                "personal_docs.whole_file_locator.v1", source_ref.encode("ascii"),
                revision_ref.encode("ascii"), _integer(self.start_char),
                _integer(self.end_char),
            )
            if type(self.locator_digest) is not str or (
                self.locator_digest and self.locator_digest != expected
            ):
                _fail("invalid_descriptor")
            object.__setattr__(self, "locator_digest", expected)
        except _Failure as error:
            failure = error.reason
        except Exception:
            failure = "invalid_descriptor"
        if failure is not None:
            _raise_public(failure)

    def __repr__(self) -> str:
        return f"PersonalDocsWholeFileLocator(locator_digest={self.locator_digest!r})"


def _locator_values(locator: object) -> tuple[object, ...]:
    if type(locator) is not PersonalDocsWholeFileLocator:
        _fail("invalid_descriptor")
    values = (
        locator.source_ref, locator.revision_ref, locator.start_char,
        locator.end_char, locator.locator_digest,
    )
    if (
        type(values[0]) is not str or _SOURCE_REF.fullmatch(values[0]) is None
        or type(values[1]) is not str or _SHA256_REF.fullmatch(values[1]) is None
        or type(values[2]) is not int or values[2] != 0
        or type(values[3]) is not int or values[3] < 0
        or type(values[4]) is not str or _SHA256_REF.fullmatch(values[4]) is None
    ):
        _fail("invalid_descriptor")
    return values


@dataclass(frozen=True, slots=True, repr=False)
class PersonalDocsFileDescriptor:
    relative_path: str = field(repr=False)
    locator: PersonalDocsWholeFileLocator
    mime_type: str
    extractor_profile: str
    byte_size: int
    char_size: int
    source_ref: str
    revision_ref: str
    extracted_content_hash: str
    binding_digest: str
    descriptor_digest: str = ""

    def __post_init__(self) -> None:
        failure: str | None = None
        try:
            _, path_bytes = _relative_path(self.relative_path)
            locator = _locator_values(self.locator)
            if type(self.mime_type) is not str or self.mime_type not in _MIMES.values():
                _fail("invalid_descriptor")
            if (
                type(self.extractor_profile) is not str
                or self.extractor_profile != PERSONAL_DOCS_TEXT_PROFILE
                or type(self.byte_size) is not int
                or not 0 <= self.byte_size <= MAX_FILE_BYTES
                or type(self.char_size) is not int or self.char_size < 0
            ):
                _fail("invalid_descriptor")
            source_ref = _source(self.source_ref)
            revision_ref = _sha(self.revision_ref)
            content_hash = _sha(self.extracted_content_hash)
            binding_digest = _sha(self.binding_digest)
            if (
                locator[0] != source_ref or locator[1] != revision_ref
                or locator[3] != self.char_size
            ):
                _fail("invalid_descriptor")
            expected = _digest(
                "personal_docs.file_descriptor.v1", path_bytes,
                source_ref.encode("ascii"), revision_ref.encode("ascii"),
                content_hash.encode("ascii"), self.mime_type.encode("ascii"),
                self.extractor_profile.encode("ascii"), _integer(self.byte_size),
                _integer(self.char_size), str(locator[4]).encode("ascii"),
                binding_digest.encode("ascii"),
            )
            if type(self.descriptor_digest) is not str or (
                self.descriptor_digest and self.descriptor_digest != expected
            ):
                _fail("invalid_descriptor")
            object.__setattr__(self, "descriptor_digest", expected)
        except _Failure as error:
            failure = error.reason
        except Exception:
            failure = "invalid_descriptor"
        if failure is not None:
            _raise_public(failure)

    def __repr__(self) -> str:
        return (
            "PersonalDocsFileDescriptor("
            f"source_ref={self.source_ref!r}, descriptor_digest={self.descriptor_digest!r})"
        )


def _descriptor_values(descriptor: object) -> tuple[object, ...]:
    if type(descriptor) is not PersonalDocsFileDescriptor:
        _fail("invalid_descriptor")
    locator = _locator_values(descriptor.locator)
    values = (
        descriptor.relative_path, locator, descriptor.mime_type,
        descriptor.extractor_profile, descriptor.byte_size, descriptor.char_size,
        descriptor.source_ref, descriptor.revision_ref,
        descriptor.extracted_content_hash, descriptor.binding_digest,
        descriptor.descriptor_digest,
    )
    _relative_path(values[0])
    if (
        type(values[2]) is not str or values[2] not in _MIMES.values()
        or type(values[3]) is not str or values[3] != PERSONAL_DOCS_TEXT_PROFILE
        or type(values[4]) is not int or not 0 <= values[4] <= MAX_FILE_BYTES
        or type(values[5]) is not int or values[5] < 0
        or type(values[6]) is not str or _SOURCE_REF.fullmatch(values[6]) is None
        or any(
            type(item) is not str or _SHA256_REF.fullmatch(item) is None
            for item in values[7:11]
        )
    ):
        _fail("invalid_descriptor")
    return values


@dataclass(frozen=True, slots=True, repr=False)
class PersonalDocsSnapshot:
    descriptors: tuple[PersonalDocsFileDescriptor, ...]
    binding_digest: str
    snapshot_digest: str = ""
    owner_ref: str = ""
    unsupported_count: int = 0
    unsupported_digest: str = ""

    def __post_init__(self) -> None:
        failure = False
        try:
            if (
                type(self.descriptors) is not tuple or len(self.descriptors) > MAX_FILES
                or any(type(item) is not PersonalDocsFileDescriptor for item in self.descriptors)
            ):
                _fail("invalid_snapshot")
            binding_digest = _sha(self.binding_digest, "invalid_snapshot")
            owner_ref = self.owner_ref
            if type(owner_ref) is not str or (owner_ref and _SHA256_REF.fullmatch(owner_ref) is None):
                _fail("invalid_snapshot")
            unsupported_count = self.unsupported_count
            unsupported_digest = self.unsupported_digest
            if (type(unsupported_count) is not int or not 0 <= unsupported_count <= MAX_ENTRIES
                    or (unsupported_digest and _SHA256_REF.fullmatch(unsupported_digest) is None)
                    or (unsupported_count == 0 and unsupported_digest)):
                _fail("invalid_snapshot")
            rows = tuple(_descriptor_values(item) for item in self.descriptors)
            path_keys = tuple(str(row[0]).encode("utf-8") for row in rows)
            if (
                path_keys != tuple(sorted(path_keys)) or len(set(path_keys)) != len(path_keys)
                or any(row[9] != binding_digest for row in rows)
            ):
                _fail("invalid_snapshot")
            expected = _digest(
                "personal_docs.snapshot.v3" if owner_ref else "personal_docs.snapshot.v1",
                *((owner_ref.encode("ascii"), _integer(unsupported_count),
                   (unsupported_digest or _digest("personal_docs.unsupported.v1")).encode("ascii")
                   ) if owner_ref else ()),
                binding_digest.encode("ascii"), _integer(len(rows)),
                *(str(row[10]).encode("ascii") for row in rows),
            )
            if type(self.snapshot_digest) is not str or (
                self.snapshot_digest and self.snapshot_digest != expected
            ):
                _fail("invalid_snapshot")
            object.__setattr__(self, "snapshot_digest", expected)
            object.__setattr__(self, "owner_ref", owner_ref)
            object.__setattr__(self, "unsupported_count", unsupported_count)
            object.__setattr__(self, "unsupported_digest", unsupported_digest)
        except Exception:
            failure = True
        if failure:
            _raise_public("invalid_snapshot")

    def __repr__(self) -> str:
        return (
            f"PersonalDocsSnapshot(file_count={len(self.descriptors)}, "
            f"unsupported_count={self.unsupported_count}, snapshot_digest={self.snapshot_digest!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class PersonalDocsExactRead:
    source_ref: str
    revision_ref: str
    content: str = field(repr=False)
    extracted_content_hash: str
    locator: PersonalDocsWholeFileLocator
    byte_size: int
    char_size: int

    def __post_init__(self) -> None:
        failure = False
        try:
            source_ref = _source(self.source_ref, "invalid_snapshot")
            revision_ref = _sha(self.revision_ref, "invalid_snapshot")
            content_hash = _sha(self.extracted_content_hash, "invalid_snapshot")
            if (
                type(self.content) is not str or type(self.byte_size) is not int
                or type(self.char_size) is not int
            ):
                _fail("invalid_snapshot")
            encoded = self.content.encode("utf-8", "strict")
            locator = _locator_values(self.locator)
            if (
                len(encoded) != self.byte_size or len(self.content) != self.char_size
                or _revision(encoded) != revision_ref
                or _content_hash(self.content) != content_hash
                or locator[0] != source_ref or locator[1] != revision_ref
                or locator[2] != 0 or locator[3] != self.char_size
            ):
                _fail("invalid_snapshot")
        except Exception:
            failure = True
        if failure:
            _raise_public("invalid_snapshot")

    def __repr__(self) -> str:
        return (
            f"PersonalDocsExactRead(source_ref={self.source_ref!r}, "
            f"revision_ref={self.revision_ref!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class PersonalDocsUnavailableObservation:
    source_ref: str
    reason: str
    binding_digest: str
    snapshot_digest: str
    observation_digest: str = ""

    def __post_init__(self) -> None:
        failure = False
        try:
            source_ref = _source(self.source_ref, "invalid_snapshot")
            if type(self.reason) is not str or self.reason not in {
                "missing", "access_denied", "unsafe_or_replaced", "changed",
            }:
                _fail("invalid_snapshot")
            binding_digest = _sha(self.binding_digest, "invalid_snapshot")
            snapshot_digest = _sha(self.snapshot_digest, "invalid_snapshot")
            expected = _digest(
                "personal_docs.unavailable_observation.v1", source_ref.encode("ascii"),
                self.reason.encode("ascii"), binding_digest.encode("ascii"),
                snapshot_digest.encode("ascii"),
            )
            if type(self.observation_digest) is not str or (
                self.observation_digest and self.observation_digest != expected
            ):
                _fail("invalid_snapshot")
            object.__setattr__(self, "observation_digest", expected)
        except Exception:
            failure = True
        if failure:
            _raise_public("invalid_snapshot")

    def __repr__(self) -> str:
        return (
            f"PersonalDocsUnavailableObservation(reason={self.reason!r}, "
            f"observation_digest={self.observation_digest!r})"
        )


_Identity = tuple[int, int, int, int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class _TreeEntry:
    relative_path: str
    kind: str
    identity: _Identity


@dataclass(frozen=True, slots=True)
class _FileCandidate:
    relative_path: str
    full_path: str
    identity: _Identity
    parent_identities: tuple[tuple[str, _Identity], ...]
    mime_type: str


@dataclass(frozen=True, slots=True)
class _TreeScan:
    entries: tuple[_TreeEntry, ...]
    files: tuple[_FileCandidate, ...]
    unsupported_count: int = 0
    unsupported_digest: str = ""


@dataclass(frozen=True, slots=True)
class _StableRead:
    raw: bytes
    text: str


@dataclass(frozen=True, slots=True)
class _DescriptorSeal:
    descriptor: PersonalDocsFileDescriptor
    values: tuple[object, ...]
    file_identity: _Identity
    parent_identities: tuple[tuple[str, _Identity], ...]


@dataclass(frozen=True, slots=True)
class _SnapshotSeal:
    snapshot: PersonalDocsSnapshot
    descriptors: tuple[PersonalDocsFileDescriptor, ...]
    descriptor_values: tuple[tuple[object, ...], ...]
    binding_digest: str
    snapshot_digest: str


def _nanoseconds(value: object, fallback: float) -> int:
    return value if type(value) is int else int(fallback * 1_000_000_000)


def _is_reparse(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    marker = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return type(attributes) is int and bool(attributes & marker)


def _identity(info: os.stat_result) -> _Identity:
    mtime = _nanoseconds(getattr(info, "st_mtime_ns", None), info.st_mtime)
    ctime = _nanoseconds(getattr(info, "st_ctime_ns", None), info.st_ctime)
    birthtime_value = getattr(info, "st_birthtime_ns", None)
    birthtime = ctime if birthtime_value is None else int(birthtime_value)
    return (
        int(info.st_dev), int(info.st_ino), int(info.st_mode), int(info.st_size),
        mtime, ctime, 1 if _is_reparse(info) else 0, birthtime,
    )


def _stable_identity(value: _Identity) -> tuple[int, int, int, int]:
    return value[0], value[1], stat.S_IFMT(value[2]), value[6]


def _path_handle_identity_matches(path: _Identity, handle: _Identity) -> bool:
    if os.name != "nt":
        return path == handle
    return path[:5] == handle[:5] and path[6:] == handle[6:]


def _lstat(path: str, missing_reason: str = "missing") -> os.stat_result:
    try:
        return os.lstat(path)
    except PermissionError:
        _fail("access_denied")
    except FileNotFoundError:
        _fail(missing_reason)
    except OSError:
        _fail("unsafe_or_replaced")


def _ancestor_paths(root_path: str) -> tuple[str, ...]:
    result: list[str] = []
    current = root_path
    while True:
        result.append(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        if not parent:
            _fail("invalid_root")
        current = parent
    result.reverse()
    return tuple(result)


def _capture_root_chain(
    root_path: str,
) -> tuple[tuple[str, tuple[int, int, int, int]], ...]:
    result: list[tuple[str, tuple[int, int, int, int]]] = []
    for path in _ancestor_paths(root_path):
        identity = _identity(_lstat(path, "unsafe_or_replaced"))
        if not stat.S_ISDIR(identity[2]) or identity[6]:
            _fail("unsafe_or_replaced")
        result.append((path, _stable_identity(identity)))
    return tuple(result)


def _verify_root_chain(
    pins: tuple[tuple[str, tuple[int, int, int, int]], ...],
) -> None:
    if not pins:
        _fail("unsafe_or_replaced")
    for path, expected in pins:
        identity = _identity(_lstat(path, "unsafe_or_replaced"))
        if (
            not stat.S_ISDIR(identity[2]) or identity[6]
            or _stable_identity(identity) != expected
        ):
            _fail("unsafe_or_replaced")


def _entry_name(value: object) -> tuple[str, bytes]:
    name, encoded = _strict_text(
        value, MAX_RELATIVE_PATH_UTF8_BYTES, "unsafe_or_replaced"
    )
    if name in {".", ".."} or "/" in name or "\\" in name or _has_control(name):
        _fail("unsafe_or_replaced")
    return name, encoded


def _joined_child(parent: str, name: str) -> str:
    child = os.path.join(parent, name)
    if os.path.normpath(child) != child or os.path.dirname(child) != parent:
        _fail("unsafe_or_replaced")
    return child


def _mime(relative_path: str) -> str | None:
    name = relative_path.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    extension = _ascii_lower(name[dot:]) if dot >= 0 else ""
    return _MIMES.get(extension)


def _root_excluded(relative_path: str) -> bool:
    return "/" not in relative_path and _ascii_lower(relative_path) in _ROOT_EXCLUSIONS


def _scandir(path: str, remaining_entries: int) -> list[object]:
    iterator = None
    result: list[object] = []
    try:
        iterator = os.scandir(path)
        for entry in iterator:
            if len(result) >= remaining_entries:
                _fail("limit_exceeded")
            result.append(entry)
        return result
    except PermissionError:
        _fail("access_denied")
    except FileNotFoundError:
        _fail("filesystem_changed")
    except OSError:
        _fail("unsafe_or_replaced")
    finally:
        close = getattr(iterator, "close", None) if iterator is not None else None
        if close is not None:
            try:
                close()
            except Exception:
                pass


def _entry_stat(entry: object) -> os.stat_result:
    try:
        return entry.stat(follow_symlinks=False)  # type: ignore[attr-defined]
    except PermissionError:
        _fail("access_denied")
    except FileNotFoundError:
        _fail("filesystem_changed")
    except Exception:
        _fail("unsafe_or_replaced")


def _scan_tree(
    root_path: str,
    root_pins: tuple[tuple[str, tuple[int, int, int, int]], ...],
) -> _TreeScan:
    _verify_root_chain(root_pins)
    root_identity = _identity(_lstat(root_path, "unsafe_or_replaced"))
    if not stat.S_ISDIR(root_identity[2]) or root_identity[6]:
        _fail("unsafe_or_replaced")
    entries: list[_TreeEntry] = []
    files: list[_FileCandidate] = []
    directories: dict[str, _Identity] = {"": root_identity}
    stack: list[tuple[str, str, int]] = [("", root_path, 0)]
    entry_count = 0
    directory_count = 1
    file_count = 0
    total_bytes = 0
    unsupported: list[bytes] = []

    while stack:
        relative_dir, full_dir, depth = stack.pop()
        named: list[tuple[bytes, str, object]] = []
        for entry in _scandir(full_dir, MAX_ENTRIES - entry_count):
            try:
                name, name_bytes = _entry_name(entry.name)  # type: ignore[attr-defined]
            except _Failure:
                raise
            except Exception:
                _fail("unsafe_or_replaced")
            named.append((name_bytes, name, entry))
        named.sort(key=lambda item: item[0])

        for _, name, entry in named:
            entry_count += 1
            if entry_count > MAX_ENTRIES:
                _fail("limit_exceeded")
            relative = name if not relative_dir else relative_dir + "/" + name
            relative_depth = depth + 1
            try:
                relative_bytes = relative.encode("utf-8", "strict")
            except UnicodeEncodeError:
                _fail("unsafe_or_replaced")
            if (
                relative_depth > MAX_RELATIVE_DEPTH
                or len(relative_bytes) > MAX_RELATIVE_PATH_UTF8_BYTES
            ):
                _fail("limit_exceeded")
            _relative_path(relative)
            full_path = _joined_child(full_dir, name)
            observed_identity = _identity(_entry_stat(entry))
            identity = _identity(_lstat(full_path, "filesystem_changed"))
            if (
                observed_identity[6]
                or identity[6]
                or stat.S_ISLNK(observed_identity[2])
                or stat.S_ISLNK(identity[2])
                or stat.S_IFMT(observed_identity[2]) != stat.S_IFMT(identity[2])
            ):
                _fail("unsafe_or_replaced")
            if stat.S_ISDIR(identity[2]):
                directory_count += 1
                if directory_count > MAX_DIRECTORIES:
                    _fail("limit_exceeded")
                directories[relative] = identity
                entries.append(_TreeEntry(relative, "directory", identity))
                stack.append((relative, full_path, relative_depth))
                continue
            if not stat.S_ISREG(identity[2]):
                _fail("unsafe_or_replaced")
            entries.append(_TreeEntry(relative, "file", identity))
            mime_type = _mime(relative)
            if mime_type is None:
                unsupported.append(relative.encode("utf-8"))
                continue
            if _root_excluded(relative):
                continue
            file_count += 1
            total_bytes += identity[3]
            if (
                file_count > MAX_FILES or identity[3] > MAX_FILE_BYTES
                or total_bytes > MAX_TOTAL_FILE_BYTES
            ):
                _fail("limit_exceeded")
            parent_rows: list[tuple[str, _Identity]] = [("", directories[""])]
            parent_parts: list[str] = []
            for part in relative.split("/")[:-1]:
                parent_parts.append(part)
                parent_relative = "/".join(parent_parts)
                parent_identity = directories.get(parent_relative)
                if parent_identity is None:
                    _fail("unsafe_or_replaced")
                parent_rows.append((parent_relative, parent_identity))
            files.append(
                _FileCandidate(
                    relative, full_path, identity, tuple(parent_rows), mime_type
                )
            )

    key = lambda item: item.relative_path.encode("utf-8")
    unsupported_digest = ""
    if unsupported:
        unsupported_digest = _digest(
            "personal_docs.unsupported.v1", _integer(len(unsupported)), *unsupported
        )
    return _TreeScan(
        tuple(sorted(entries, key=key)), tuple(sorted(files, key=key)),
        len(unsupported), unsupported_digest,
    )


def _verify_parents(
    root_path: str,
    root_pins: tuple[tuple[str, tuple[int, int, int, int]], ...],
    expected: tuple[tuple[str, _Identity], ...],
) -> None:
    _verify_root_chain(root_pins)
    for relative, expected_identity in expected:
        path = root_path if not relative else os.path.join(root_path, *relative.split("/"))
        identity = _identity(_lstat(path))
        if (
            not stat.S_ISDIR(identity[2]) or identity[6]
            or _stable_identity(identity) != _stable_identity(expected_identity)
        ):
            _fail("unsafe_or_replaced")


def _open_flags() -> int:
    flags = os.O_RDONLY
    for name in ("O_NONBLOCK", "O_NOFOLLOW", "O_BINARY", "O_NOINHERIT"):
        value = getattr(os, name, 0)
        if type(value) is int:
            flags |= value
    return flags


def _read_fd(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while total <= MAX_FILE_BYTES:
        requested = min(64 * 1_024, MAX_FILE_BYTES + 1 - total)
        try:
            chunk = os.read(descriptor, requested)
        except Exception:
            _fail("unsafe_or_replaced")
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    content = b"".join(chunks)
    if len(content) > MAX_FILE_BYTES:
        _fail("limit_exceeded")
    return content


def _stable_read(
    root_path: str,
    root_pins: tuple[tuple[str, tuple[int, int, int, int]], ...],
    candidate: _FileCandidate,
) -> _StableRead:
    _verify_parents(root_path, root_pins, candidate.parent_identities)
    before = _identity(_lstat(candidate.full_path))
    if (
        before[6] or not stat.S_ISREG(before[2])
        or _stable_identity(before) != _stable_identity(candidate.identity)
    ):
        _fail("unsafe_or_replaced")
    if before != candidate.identity:
        _fail("changed")
    if before[3] > MAX_FILE_BYTES:
        _fail("limit_exceeded")

    descriptor: int | None = None
    try:
        try:
            descriptor = os.open(candidate.full_path, _open_flags())
        except PermissionError:
            _fail("access_denied")
        except FileNotFoundError:
            _fail("missing")
        except OSError:
            _fail("unsafe_or_replaced")

        try:
            opened = _identity(os.fstat(descriptor))
        except Exception:
            _fail("unsafe_or_replaced")
        second = _identity(_lstat(candidate.full_path))
        _verify_parents(root_path, root_pins, candidate.parent_identities)
        if (
            opened[6] or second[6] or not stat.S_ISREG(opened[2])
            or not stat.S_ISREG(second[2])
            or not _path_handle_identity_matches(candidate.identity, opened)
            or second != candidate.identity
        ):
            if (
                _stable_identity(opened) != _stable_identity(candidate.identity)
                or _stable_identity(second) != _stable_identity(candidate.identity)
            ):
                _fail("unsafe_or_replaced")
            _fail("changed")

        raw = _read_fd(descriptor)
        try:
            final_opened = _identity(os.fstat(descriptor))
        except Exception:
            _fail("unsafe_or_replaced")
        final_path = _identity(_lstat(candidate.full_path))
        _verify_parents(root_path, root_pins, candidate.parent_identities)
        if (
            final_opened != opened
            or not _path_handle_identity_matches(candidate.identity, final_opened)
            or final_path != candidate.identity
            or len(raw) != candidate.identity[3]
        ):
            if (
                _stable_identity(final_opened) != _stable_identity(candidate.identity)
                or _stable_identity(final_path) != _stable_identity(candidate.identity)
            ):
                _fail("unsafe_or_replaced")
            _fail("changed")
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass

    try:
        text = raw.decode("utf-8", "strict")
    except UnicodeDecodeError:
        _fail("invalid_utf8")
    return _StableRead(raw, text)


def _binding_digest(
    owner_id: str, root_ref: str, root_path: str,
    root_pins: tuple[tuple[str, tuple[int, int, int, int]], ...],
) -> str:
    parts: list[bytes] = [
        owner_id.encode("utf-8"), root_ref.encode("ascii"), root_path.encode("utf-8"),
        *(
            _integer(limit)
            for limit in (
                MAX_FILES, MAX_ENTRIES, MAX_DIRECTORIES, MAX_RELATIVE_DEPTH,
                MAX_RELATIVE_PATH_UTF8_BYTES, MAX_FILE_BYTES, MAX_TOTAL_FILE_BYTES,
            )
        ),
    ]
    for _, identity in root_pins:
        parts.extend(_integer(item) for item in identity)
    return _digest("personal_docs.owner_root_binding.v1", *parts)


def _capture_error(reason: str) -> str:
    if reason in {
        "operation_in_progress", "access_denied", "limit_exceeded",
        "invalid_utf8", "filesystem_changed",
    }:
        return reason
    if reason == "unsafe_or_replaced":
        return "unsafe_filesystem"
    if reason in {"changed", "missing"}:
        return "filesystem_changed"
    return "capture_failed"


_Result = TypeVar("_Result")


class PersonalDocsOwnerReader:
    """Sequential process-local capability for one owner and local root."""

    __slots__ = (
        "_owner_id", "_root_ref", "_root_path", "_root_pins", "_operation_active",
        "_snapshot_seal", "_seals",
    )

    def __init__(self, *, owner_id: str, root_ref: str, root_path: str) -> None:
        failure: str | None = None
        try:
            owner, _ = _strict_text(owner_id, MAX_OWNER_ID_UTF8_BYTES, "invalid_request")
            if _has_control(owner):
                _fail("invalid_request")
            if type(root_ref) is not str or _SHA256_REF.fullmatch(root_ref) is None:
                _fail("invalid_request")
            root = _root_path(root_path)
        except _Failure as error:
            failure = error.reason
            owner = ""
            root = ""
        except Exception:
            failure = "invalid_request"
            owner = ""
            root = ""
        if failure is not None:
            _raise_public(failure)
        self._owner_id = owner
        self._root_ref = root_ref
        self._root_path = root
        self._root_pins: tuple[tuple[str, tuple[int, int, int, int]], ...] = ()
        self._operation_active = False
        self._snapshot_seal: _SnapshotSeal | None = None
        self._seals: dict[int, _DescriptorSeal] = {}

    def _run(self, operation: str, callback: Callable[[], _Result]) -> _Result:
        if self._operation_active:
            if operation == "capture":
                self._snapshot_seal = None
                self._seals = {}
            _raise_public("operation_in_progress")
        self._operation_active = True
        failure: str | None = None
        result: _Result | None = None
        try:
            result = callback()
        except _Failure as error:
            failure = error.reason
        except PersonalDocsOwnerReaderError as error:
            failure = error.code
        except Exception:
            failure = "capture_failed" if operation == "capture" else "changed"
        finally:
            self._operation_active = False
        if failure is not None:
            if operation == "capture":
                self._snapshot_seal = None
                self._seals = {}
                failure = _capture_error(failure)
            _raise_public(failure)
        return result  # type: ignore[return-value]

    def capture(self) -> PersonalDocsSnapshot:
        return self._run("capture", self._capture)

    def _capture(self) -> PersonalDocsSnapshot:
        if not self._root_pins:
            self._root_pins = _capture_root_chain(self._root_path)
        else:
            _verify_root_chain(self._root_pins)
        binding_digest = _binding_digest(
            self._owner_id, self._root_ref, self._root_path, self._root_pins
        )
        first_scan = _scan_tree(self._root_path, self._root_pins)
        descriptors: list[PersonalDocsFileDescriptor] = []
        seal_inputs: list[
            tuple[PersonalDocsFileDescriptor, _Identity, tuple[tuple[str, _Identity], ...]]
        ] = []
        for candidate in first_scan.files:
            captured = _stable_read(self._root_path, self._root_pins, candidate)
            source_ref = _make_source(
                self._owner_id, self._root_ref, candidate.relative_path
            )
            revision_ref = _revision(captured.raw)
            content_hash = _content_hash(captured.text)
            locator = PersonalDocsWholeFileLocator(
                source_ref, revision_ref, 0, len(captured.text)
            )
            descriptor = PersonalDocsFileDescriptor(
                candidate.relative_path, locator, candidate.mime_type,
                PERSONAL_DOCS_TEXT_PROFILE, len(captured.raw), len(captured.text),
                source_ref, revision_ref, content_hash, binding_digest,
            )
            descriptors.append(descriptor)
            seal_inputs.append(
                (descriptor, candidate.identity, candidate.parent_identities)
            )
        second_scan = _scan_tree(self._root_path, self._root_pins)
        if second_scan.entries != first_scan.entries:
            _fail("filesystem_changed")
        snapshot = PersonalDocsSnapshot(
            tuple(descriptors), binding_digest,
            owner_ref=_digest("personal_docs.owner.v1", self._owner_id.encode("utf-8")),
            unsupported_count=first_scan.unsupported_count,
            unsupported_digest=first_scan.unsupported_digest,
        )
        new_seals = {
            id(descriptor): _DescriptorSeal(
                descriptor, _descriptor_values(descriptor),
                file_identity, parent_identities,
            )
            for descriptor, file_identity, parent_identities in seal_inputs
        }
        self._snapshot_seal = _SnapshotSeal(
            snapshot, snapshot.descriptors,
            tuple(_descriptor_values(item) for item in snapshot.descriptors),
            snapshot.binding_digest, snapshot.snapshot_digest,
        )
        self._seals = new_seals
        return snapshot

    def _current_authority(
        self, descriptor: object,
    ) -> tuple[_SnapshotSeal, _DescriptorSeal]:
        if type(descriptor) is not PersonalDocsFileDescriptor:
            _fail("invalid_descriptor")
        snapshot_seal = self._snapshot_seal
        if snapshot_seal is None:
            _fail("authority_unavailable")
        seal = self._seals.get(id(descriptor))
        if (
            seal is None or seal.descriptor is not descriptor
            or _descriptor_values(descriptor) != seal.values
        ):
            _fail("invalid_descriptor")
        if self._snapshot_seal is not snapshot_seal:
            _fail("authority_unavailable")
        return snapshot_seal, seal

    def _require_same_authority(
        self,
        descriptor: PersonalDocsFileDescriptor,
        snapshot_seal: _SnapshotSeal,
        seal: _DescriptorSeal,
    ) -> None:
        current_snapshot, current_seal = self._current_authority(descriptor)
        if current_snapshot is not snapshot_seal:
            _fail("authority_unavailable")
        if current_seal is not seal:
            _fail("invalid_descriptor")

    def _candidate(self, seal: _DescriptorSeal) -> _FileCandidate:
        relative_path = seal.values[0]
        mime_type = seal.values[2]
        if type(relative_path) is not str or type(mime_type) is not str:
            _fail("invalid_descriptor")
        return _FileCandidate(
            relative_path,
            os.path.join(self._root_path, *relative_path.split("/")),
            seal.file_identity, seal.parent_identities, mime_type,
        )

    def read_exact(
        self, descriptor: PersonalDocsFileDescriptor,
    ) -> PersonalDocsExactRead:
        return self._run("read_exact", lambda: self._read_exact(descriptor))

    def _read_exact(
        self, descriptor: PersonalDocsFileDescriptor,
    ) -> PersonalDocsExactRead:
        snapshot_seal, seal = self._current_authority(descriptor)
        values = seal.values
        try:
            captured = _stable_read(
                self._root_path, self._root_pins, self._candidate(seal)
            )
        except _Failure as error:
            self._require_same_authority(descriptor, snapshot_seal, seal)
            if error.reason in {"missing", "access_denied", "unsafe_or_replaced", "changed"}:
                raise
            _fail("changed")
        self._require_same_authority(descriptor, snapshot_seal, seal)
        locator_values = values[1]
        if type(locator_values) is not tuple or len(locator_values) != 5:
            _fail("invalid_descriptor")
        if (
            _revision(captured.raw) != values[7]
            or _content_hash(captured.text) != values[8]
            or len(captured.raw) != values[4]
            or len(captured.text) != values[5]
            or locator_values[2] != 0
            or locator_values[3] != len(captured.text)
        ):
            _fail("changed")
        sealed_locator = PersonalDocsWholeFileLocator(
            locator_values[0], locator_values[1], locator_values[2],
            locator_values[3], locator_values[4],
        )
        return PersonalDocsExactRead(
            values[6], values[7], captured.text, values[8], sealed_locator,
            values[4], values[5],
        )

    def observe_unavailable(
        self, descriptor: PersonalDocsFileDescriptor,
    ) -> PersonalDocsUnavailableObservation:
        return self._run(
            "observe_unavailable", lambda: self._observe_unavailable(descriptor)
        )

    def _observe_unavailable(
        self, descriptor: PersonalDocsFileDescriptor,
    ) -> PersonalDocsUnavailableObservation:
        snapshot_seal, seal = self._current_authority(descriptor)
        values = seal.values
        reason: str | None = None
        try:
            captured = _stable_read(
                self._root_path, self._root_pins, self._candidate(seal)
            )
            if (
                _revision(captured.raw) != values[7]
                or _content_hash(captured.text) != values[8]
                or len(captured.raw) != values[4]
                or len(captured.text) != values[5]
            ):
                reason = "changed"
        except _Failure as error:
            reason = (
                error.reason
                if error.reason in {"missing", "access_denied", "unsafe_or_replaced"}
                else "changed"
            )
        self._require_same_authority(descriptor, snapshot_seal, seal)
        if reason is None:
            _fail("still_available")
        source_ref = values[6]
        binding_digest = values[9]
        if type(source_ref) is not str or type(binding_digest) is not str:
            _fail("invalid_descriptor")
        return PersonalDocsUnavailableObservation(
            source_ref, reason, binding_digest, snapshot_seal.snapshot_digest,
        )


__all__ = [
    "MAX_FILES", "MAX_ENTRIES", "MAX_DIRECTORIES", "MAX_RELATIVE_DEPTH",
    "MAX_RELATIVE_PATH_UTF8_BYTES", "MAX_FILE_BYTES", "MAX_TOTAL_FILE_BYTES",
    "PERSONAL_DOCS_TEXT_PROFILE", "PersonalDocsOwnerReaderError",
    "PersonalDocsWholeFileLocator", "PersonalDocsFileDescriptor",
    "PersonalDocsSnapshot", "PersonalDocsExactRead",
    "PersonalDocsUnavailableObservation", "PersonalDocsOwnerReader",
]
