"""Pure, offline verification boundary for pinned tokenizer asset bundles.

This module deliberately accepts only exact built-in containers and immutable
bytes.  It neither locates assets nor accepts an asset: callers must supply the
bytes and both detached approval pins.
"""

import hashlib
import json
import re
import struct
import unicodedata


OFFLINE_TOKENIZER_ASSET_MANIFEST_SCHEMA = "odysseus.offline_tokenizer_asset_manifest.v1"
OFFLINE_TOKENIZER_ASSET_VERIFICATION_SCHEMA = "odysseus.offline_tokenizer_asset_verification.v1"
OFFLINE_TOKENIZER_ASSET_MANIFEST_FIELDS = ("schema", "tokenizer_ref", "tokenizer_revision", "source_uri", "files")
OFFLINE_TOKENIZER_ASSET_FILE_FIELDS = ("path", "role", "size_bytes", "sha256")
OFFLINE_TOKENIZER_ASSET_REPORT_FIELDS = (
    "schema", "status", "approval_ref", "unsigned_manifest_digest", "bundle_digest", "manifest",
    "file_count", "total_bytes", "mandatory_roles", "bytes_verified", "external_approval_pin_consumed",
    "filesystem_used", "network_used", "runtime_download_used", "tokenizer_import_used", "provider_call_used",
    "live_action_authorized",
)
OFFLINE_TOKENIZER_ASSET_ROLES = ("license", "tokenizer_model", "tokenizer_config", "special_tokens", "added_tokens", "normalizer_config")
MANDATORY_OFFLINE_TOKENIZER_ASSET_ROLES = ("license", "tokenizer_model")
OFFLINE_TOKENIZER_ASSET_ERROR_CODES = (
    "invalid_manifest", "invalid_files", "invalid_path", "invalid_source_uri", "invalid_pin", "budget_exceeded",
    "missing_file", "unexpected_file", "size_mismatch", "file_digest_mismatch", "bundle_digest_mismatch",
    "invalid_report", "verification_failed",
)
MAX_OFFLINE_TOKENIZER_ASSET_MANIFEST_BYTES = 65536
MAX_OFFLINE_TOKENIZER_ASSET_REPORT_BYTES = 131072
MAX_OFFLINE_TOKENIZER_ASSET_FILES = 8
MAX_OFFLINE_TOKENIZER_ASSET_FILE_BYTES = 33554432
MAX_OFFLINE_TOKENIZER_ASSET_BUNDLE_BYTES = 67108864

_REF_LIMIT = 128
_URI_LIMIT = 2048
_PATH_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_URI_PATH_SEGMENT_RE = re.compile(r"[A-Za-z0-9._~-]+\Z")
_HEX_RE = re.compile(r"[0-9a-f]{64}\Z")
_RESERVED = {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"}


class _ContractFailure(Exception):
    def __init__(self, code: object) -> None:
        self.code = code if type(code) is str and code in OFFLINE_TOKENIZER_ASSET_ERROR_CODES else "verification_failed"


class OfflineTokenizerAssetContractError(Exception):
    """A content-free failure from this contract's public boundary."""

    def __init__(self, code: object) -> None:
        self.code = code if type(code) is str and code in OFFLINE_TOKENIZER_ASSET_ERROR_CODES else "verification_failed"
        super().__init__(self.code)


def _fail(code: str) -> None:
    raise _ContractFailure(code)


def _public_error(error: BaseException) -> OfflineTokenizerAssetContractError:
    if type(error) is _ContractFailure:
        return OfflineTokenizerAssetContractError(error.code)
    return OfflineTokenizerAssetContractError("verification_failed")


def _exact_mapping(value: object, code: str) -> dict:
    if type(value) is not dict:
        _fail(code)
    return value


def _exact_fields(value: dict, fields: tuple[str, ...], code: str) -> None:
    if len(value) != len(fields):
        _fail(code)
    for key in value:
        if type(key) is not str or key not in fields:
            _fail(code)
    for field in fields:
        if field not in value:
            _fail(code)


def _bounded_ascii(value: object, code: str) -> str:
    if type(value) is not str:
        _fail(code)
    if not value or len(value) > _REF_LIMIT:
        _fail("budget_exceeded" if len(value) > _REF_LIMIT else code)
    try:
        value.encode("ascii")
    except UnicodeError:
        _fail(code)
    return value


def _digest_text(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _validate_digest(value: object, code: str) -> str:
    if type(value) is not str or not value.startswith("sha256:") or _HEX_RE.fullmatch(value[7:]) is None:
        _fail(code)
    return value


def _validate_path(value: object) -> str:
    if type(value) is not str:
        _fail("invalid_path")
    try:
        raw = value.encode("ascii")
    except UnicodeError:
        _fail("invalid_path")
    if not raw or len(raw) > 240 or "\\" in value or value.startswith("/") or ":" in value:
        _fail("invalid_path")
    segments = value.split("/")
    if len(segments) > 8:
        _fail("invalid_path")
    for segment in segments:
        if not segment or len(segment.encode("ascii")) > 64 or _PATH_RE.fullmatch(segment) is None:
            _fail("invalid_path")
        if segment in {".", ".."} or segment.endswith((".", " ")) or segment.split(".", 1)[0].upper() in _RESERVED:
            _fail("invalid_path")
    return value


def _validate_source_uri(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        _fail("invalid_source_uri")
    if unicodedata.normalize("NFC", value) != value:
        _fail("invalid_source_uri")
    try:
        raw = value.encode("ascii")
    except UnicodeError:
        _fail("invalid_source_uri")
    if len(raw) > _URI_LIMIT or not value.startswith("https://"):
        _fail("invalid_source_uri")
    rest = value[8:]
    if any(token in rest for token in ("@", ":", "?", "#", "%", "\\")) or "/" not in rest:
        _fail("invalid_source_uri")
    host, path = rest.split("/", 1)
    if not host or host.lower() != host or host.count(".") < 1 or host.endswith((".invalid", ".example", ".onion", ".localhost", ".local", ".internal", ".test")):
        _fail("invalid_source_uri")
    labels = host.split(".")
    if any(not label or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
        _fail("invalid_source_uri")
    if all(part.isdigit() for part in labels) or host in {"localhost", "0.0.0.0"}:
        _fail("invalid_source_uri")
    segments = path.split("/")
    if any(not segment or segment in {".", ".."} or _URI_PATH_SEGMENT_RE.fullmatch(segment) is None for segment in segments):
        _fail("invalid_source_uri")
    return value


def _normalise_manifest(manifest: object) -> tuple[bytes, dict, list[dict]]:
    source = _exact_mapping(manifest, "invalid_manifest")
    _exact_fields(source, OFFLINE_TOKENIZER_ASSET_MANIFEST_FIELDS, "invalid_manifest")
    if type(source["schema"]) is not str or source["schema"] != OFFLINE_TOKENIZER_ASSET_MANIFEST_SCHEMA:
        _fail("invalid_manifest")
    tokenizer_ref = _bounded_ascii(source["tokenizer_ref"], "invalid_manifest")
    tokenizer_revision = _bounded_ascii(source["tokenizer_revision"], "invalid_manifest")
    source_uri = _validate_source_uri(source["source_uri"])
    supplied_files = source["files"]
    if type(supplied_files) is not list:
        _fail("invalid_manifest")
    if not supplied_files:
        _fail("invalid_manifest")
    if len(supplied_files) > MAX_OFFLINE_TOKENIZER_ASSET_FILES:
        _fail("budget_exceeded")
    records: list[dict] = []
    seen_paths: set[str] = set()
    seen_casefolds: set[str] = set()
    seen_roles: set[str] = set()
    for supplied in supplied_files:
        item = _exact_mapping(supplied, "invalid_manifest")
        _exact_fields(item, OFFLINE_TOKENIZER_ASSET_FILE_FIELDS, "invalid_manifest")
        path = _validate_path(item["path"])
        role = item["role"]
        if type(role) is not str or role not in OFFLINE_TOKENIZER_ASSET_ROLES or role in seen_roles:
            _fail("invalid_manifest")
        size = item["size_bytes"]
        if type(size) is not int or size < 0 or (role in MANDATORY_OFFLINE_TOKENIZER_ASSET_ROLES and size == 0):
            _fail("invalid_manifest")
        if size > MAX_OFFLINE_TOKENIZER_ASSET_FILE_BYTES:
            _fail("budget_exceeded")
        digest = _validate_digest(item["sha256"], "invalid_manifest")
        folded = path.casefold()
        if path in seen_paths or folded in seen_casefolds:
            _fail("invalid_path")
        seen_paths.add(path)
        seen_casefolds.add(folded)
        seen_roles.add(role)
        records.append({"path": path, "role": role, "size_bytes": size, "sha256": digest})
    if not set(MANDATORY_OFFLINE_TOKENIZER_ASSET_ROLES).issubset(seen_roles):
        _fail("invalid_manifest")
    records.sort(key=lambda item: item["path"].encode("utf-8"))
    normalised = {"schema": OFFLINE_TOKENIZER_ASSET_MANIFEST_SCHEMA, "tokenizer_ref": tokenizer_ref, "tokenizer_revision": tokenizer_revision, "source_uri": source_uri, "files": records}
    encoded = json.dumps(normalised, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_OFFLINE_TOKENIZER_ASSET_MANIFEST_BYTES:
        _fail("budget_exceeded")
    return encoded, normalised, records


def canonical_unsigned_offline_tokenizer_asset_manifest_bytes(manifest: object, /) -> bytes:
    """Return the immutable compact canonical unsigned manifest representation."""
    failure = None
    try:
        return _normalise_manifest(manifest)[0]
    except BaseException as error:
        failure = _public_error(error)
    raise failure from None


def _capture_files(files: object, records: list[dict]) -> dict[str, bytes]:
    source = _exact_mapping(files, "invalid_files")
    if len(source) > MAX_OFFLINE_TOKENIZER_ASSET_FILES:
        _fail("budget_exceeded")
    captured: dict[str, bytes] = {}
    for path in source:
        if type(path) is not str:
            _fail("invalid_files")
        _validate_path(path)
        value = source[path]
        if type(value) is not bytes:
            _fail("invalid_files")
        if len(value) > MAX_OFFLINE_TOKENIZER_ASSET_FILE_BYTES:
            _fail("budget_exceeded")
        captured[path] = value
    expected_paths = {record["path"] for record in records}
    actual_paths = set(captured)
    missing = expected_paths - actual_paths
    if missing:
        _fail("missing_file")
    if actual_paths - expected_paths:
        _fail("unexpected_file")
    total = 0
    for record in records:
        raw = captured[record["path"]]
        if len(raw) != record["size_bytes"]:
            _fail("size_mismatch")
        if _digest_text(raw) != record["sha256"]:
            _fail("file_digest_mismatch")
        total += len(raw)
        if total > MAX_OFFLINE_TOKENIZER_ASSET_BUNDLE_BYTES:
            _fail("budget_exceeded")
    return captured


def _pins(approval: object, digest: object) -> tuple[str, str]:
    if type(approval) is not str or not approval.startswith("approval:sha256:") or _HEX_RE.fullmatch(approval[16:]) is None:
        _fail("invalid_pin")
    return approval, _validate_digest(digest, "invalid_pin")


def _bundle_digest(manifest_bytes: bytes, records: list[dict], files: dict[str, bytes]) -> str:
    framed = bytearray(b"odysseus.offline_tokenizer_asset_bundle.v1\0")
    framed.extend(struct.pack(">Q", len(manifest_bytes)))
    framed.extend(manifest_bytes)
    framed.extend(struct.pack(">I", len(records)))
    for record in records:
        path = record["path"].encode("utf-8")
        raw = files[record["path"]]
        framed.extend(struct.pack(">H", len(path)))
        framed.extend(path)
        framed.extend(struct.pack(">Q", len(raw)))
        framed.extend(hashlib.sha256(raw).digest())
    return _digest_text(bytes(framed))


def _verify(manifest: object, files: object, approval: object, expected_bundle: object) -> dict[str, object]:
    manifest_bytes, normalised, records = _normalise_manifest(manifest)
    captured = _capture_files(files, records)
    approval_ref, bundle_pin = _pins(approval, expected_bundle)
    bundle_digest = _bundle_digest(manifest_bytes, records, captured)
    if bundle_digest != bundle_pin:
        _fail("bundle_digest_mismatch")
    unsigned_digest = _digest_text(b"odysseus.offline_tokenizer_asset_manifest.v1\0" + struct.pack(">Q", len(manifest_bytes)) + manifest_bytes)
    return {
        "schema": OFFLINE_TOKENIZER_ASSET_VERIFICATION_SCHEMA,
        "status": "verified",
        "approval_ref": approval_ref,
        "unsigned_manifest_digest": unsigned_digest,
        "bundle_digest": bundle_digest,
        "manifest": normalised,
        "file_count": len(records),
        "total_bytes": sum(len(captured[record["path"]]) for record in records),
        "mandatory_roles": list(MANDATORY_OFFLINE_TOKENIZER_ASSET_ROLES),
        "bytes_verified": True,
        "external_approval_pin_consumed": True,
        "filesystem_used": False,
        "network_used": False,
        "runtime_download_used": False,
        "tokenizer_import_used": False,
        "provider_call_used": False,
        "live_action_authorized": False,
    }


def verify_offline_tokenizer_asset_bundle(manifest: object, files: object, /, *, expected_approval_ref: object, expected_bundle_digest: object) -> dict[str, object]:
    """Rehash exact supplied bytes and return a detached, effect-free report."""
    failure = None
    try:
        return _verify(manifest, files, expected_approval_ref, expected_bundle_digest)
    except BaseException as error:
        failure = _public_error(error)
    raise failure from None


def canonical_offline_tokenizer_asset_verification_bytes(report: object, files: object, /, *, expected_approval_ref: object, expected_bundle_digest: object) -> bytes:
    """Reverify a report's manifest and supplied bytes before canonicalizing it."""
    failure = None
    try:
        supplied = _exact_mapping(report, "invalid_report")
        _exact_fields(supplied, OFFLINE_TOKENIZER_ASSET_REPORT_FIELDS, "invalid_report")
        computed = _verify(supplied["manifest"], files, expected_approval_ref, expected_bundle_digest)
        for field in OFFLINE_TOKENIZER_ASSET_REPORT_FIELDS:
            actual = supplied[field]
            expected = computed[field]
            if field == "manifest":
                if _normalise_manifest(actual)[0] != _normalise_manifest(expected)[0]:
                    _fail("invalid_report")
            elif type(actual) is not type(expected) or actual != expected:
                _fail("invalid_report")
        encoded = json.dumps(computed, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")
        if len(encoded) > MAX_OFFLINE_TOKENIZER_ASSET_REPORT_BYTES:
            _fail("budget_exceeded")
        return encoded
    except BaseException as error:
        failure = _public_error(error)
    raise failure from None
