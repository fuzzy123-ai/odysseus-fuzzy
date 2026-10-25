"""Offline-only contract tests for the RMT9 tokenizer asset manifest boundary."""

import hashlib
import importlib
import inspect
import json
import struct

import pytest


def _contract():
    return importlib.import_module("src.offline_tokenizer_asset_contract")


def _fixtures():
    license_bytes = b"synthetic license\n"
    tokenizer_bytes = b"synthetic tokenizer\n"
    files = {"LICENSE": license_bytes, "tokenizer.json": tokenizer_bytes}
    manifest = {
        "schema": "odysseus.offline_tokenizer_asset_manifest.v1",
        "tokenizer_ref": "synthetic-tokenizer",
        "tokenizer_revision": "r1",
        "source_uri": None,
        "files": [
            {"path": "tokenizer.json", "role": "tokenizer_model", "size_bytes": len(tokenizer_bytes), "sha256": "sha256:" + hashlib.sha256(tokenizer_bytes).hexdigest()},
            {"path": "LICENSE", "role": "license", "size_bytes": len(license_bytes), "sha256": "sha256:" + hashlib.sha256(license_bytes).hexdigest()},
        ],
    }
    return manifest, files


def _pins():
    return "approval:sha256:" + "a" * 64, "sha256:" + "b" * 64


def _raises_code(call, code):
    contract = _contract()
    with pytest.raises(contract.OfflineTokenizerAssetContractError) as caught:
        call(contract)
    assert caught.value.code == code
    assert str(caught.value) == code
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_rmt9_p0a_public_api_exports_signatures_and_exact_field_sets():
    contract = _contract()
    expected_exports = {
        "OFFLINE_TOKENIZER_ASSET_MANIFEST_SCHEMA", "OFFLINE_TOKENIZER_ASSET_VERIFICATION_SCHEMA",
        "OFFLINE_TOKENIZER_ASSET_MANIFEST_FIELDS", "OFFLINE_TOKENIZER_ASSET_FILE_FIELDS",
        "OFFLINE_TOKENIZER_ASSET_REPORT_FIELDS", "OFFLINE_TOKENIZER_ASSET_ROLES",
        "MANDATORY_OFFLINE_TOKENIZER_ASSET_ROLES", "OFFLINE_TOKENIZER_ASSET_ERROR_CODES",
        "MAX_OFFLINE_TOKENIZER_ASSET_MANIFEST_BYTES", "MAX_OFFLINE_TOKENIZER_ASSET_REPORT_BYTES",
        "MAX_OFFLINE_TOKENIZER_ASSET_FILES", "MAX_OFFLINE_TOKENIZER_ASSET_FILE_BYTES",
        "MAX_OFFLINE_TOKENIZER_ASSET_BUNDLE_BYTES", "OfflineTokenizerAssetContractError",
        "canonical_unsigned_offline_tokenizer_asset_manifest_bytes", "verify_offline_tokenizer_asset_bundle",
        "canonical_offline_tokenizer_asset_verification_bytes",
    }
    assert expected_exports <= set(dir(contract))
    assert contract.OFFLINE_TOKENIZER_ASSET_MANIFEST_FIELDS == ("schema", "tokenizer_ref", "tokenizer_revision", "source_uri", "files")
    assert contract.OFFLINE_TOKENIZER_ASSET_FILE_FIELDS == ("path", "role", "size_bytes", "sha256")
    assert contract.OFFLINE_TOKENIZER_ASSET_REPORT_FIELDS == ("schema", "status", "approval_ref", "unsigned_manifest_digest", "bundle_digest", "manifest", "file_count", "total_bytes", "mandatory_roles", "bytes_verified", "external_approval_pin_consumed", "filesystem_used", "network_used", "runtime_download_used", "tokenizer_import_used", "provider_call_used", "live_action_authorized")
    assert tuple(inspect.signature(contract.canonical_unsigned_offline_tokenizer_asset_manifest_bytes).parameters) == ("manifest",)
    assert str(inspect.signature(contract.verify_offline_tokenizer_asset_bundle)) == "(manifest: object, files: object, /, *, expected_approval_ref: object, expected_bundle_digest: object) -> dict[str, object]"


def test_rmt9_p0a_unsigned_manifest_canonicalization_sorts_and_detaches():
    contract = _contract()
    manifest, _ = _fixtures()
    first = contract.canonical_unsigned_offline_tokenizer_asset_manifest_bytes(manifest)
    manifest["files"].reverse()
    manifest["tokenizer_ref"] = "changed-after-call"
    second = contract.canonical_unsigned_offline_tokenizer_asset_manifest_bytes(_fixtures()[0])
    assert first == second
    decoded = json.loads(first)
    assert [item["path"] for item in decoded["files"]] == ["LICENSE", "tokenizer.json"]
    assert first == json.dumps(decoded, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def test_rmt9_p0a_external_pins_are_mandatory_and_manifest_cannot_self_approve():
    manifest, files = _fixtures()
    approval, digest = _pins()
    _raises_code(lambda c: c.verify_offline_tokenizer_asset_bundle(manifest, files, expected_approval_ref=None, expected_bundle_digest=digest), "invalid_pin")
    manifest["approval_ref"] = approval
    _raises_code(lambda c: c.verify_offline_tokenizer_asset_bundle(manifest, files, expected_approval_ref=approval, expected_bundle_digest=digest), "invalid_manifest")


def test_rmt9_p0a_source_uri_is_none_or_bounded_canonical_public_https():
    manifest, _ = _fixtures()
    manifest["source_uri"] = "https://assets.example.org/tokenizer/v1"
    assert json.loads(_contract().canonical_unsigned_offline_tokenizer_asset_manifest_bytes(manifest))["source_uri"] == manifest["source_uri"]
    for bad in ("http://assets.example.org/x", "https://localhost/x", "https://127.0.0.1/x", "https://assets.example.org:443/x", "https://assets.example.org/a%20b", "https://assets.example.org/a b", "https://assets.example.org/a\x1fb", "https://assets.example.invalid/x", "https://assets.example.example/x", "https://assets.example.onion/x", "https://assets.example.localhost/x", "https://assets.example.org/a?b=c", "https://ASSETS.example.org/x", "https://assets.example.org/a//b"):
        item, _ = _fixtures()
        item["source_uri"] = bad
        _raises_code(lambda c, item=item: c.canonical_unsigned_offline_tokenizer_asset_manifest_bytes(item), "invalid_source_uri")


def test_rmt9_p0a_files_are_exact_path_to_exact_bytes_with_unique_roles_paths_and_casefolds():
    manifest, files = _fixtures()
    approval, digest = _pins()
    for path in ("LICENSE", "license", "CON", "CON.txt", "aux.json", "PRN.md", "NUL.bin", "COM1.model", "LPT9.config", "dir/../x", "dir\\x", "x."):
        item, supplied = _fixtures()
        item["files"][0]["path"] = path
        supplied = {path if key == "tokenizer.json" else key: value for key, value in supplied.items()}
        _raises_code(lambda c, item=item, supplied=supplied: c.verify_offline_tokenizer_asset_bundle(item, supplied, expected_approval_ref=approval, expected_bundle_digest=digest), "invalid_path")
    _raises_code(lambda c: c.verify_offline_tokenizer_asset_bundle(manifest, {"LICENSE": b"x", "tokenizer.json": bytearray(b"x")}, expected_approval_ref=approval, expected_bundle_digest=digest), "invalid_files")


def test_rmt9_p0a_mandatory_license_and_tokenizer_files_are_nonempty_and_verified_before_approval():
    manifest, files = _fixtures()
    approval, digest = _pins()
    manifest["files"] = manifest["files"][:1]
    files.pop("tokenizer.json")
    _raises_code(lambda c: c.verify_offline_tokenizer_asset_bundle(manifest, files, expected_approval_ref=approval, expected_bundle_digest=digest), "invalid_manifest")
    manifest, files = _fixtures()
    empty_digest = "sha256:" + hashlib.sha256(b"").hexdigest()
    manifest["files"] = [
        {"path": "LICENSE", "role": "license", "size_bytes": 0, "sha256": empty_digest},
        {"path": "tokenizer.json", "role": "tokenizer_model", "size_bytes": 0, "sha256": empty_digest},
    ]
    files = {"LICENSE": b"", "tokenizer.json": b""}
    _raises_code(lambda c: c.canonical_unsigned_offline_tokenizer_asset_manifest_bytes(manifest), "invalid_manifest")


def test_rmt9_p0a_file_size_digest_missing_unexpected_and_bundle_mismatch_fail_closed():
    manifest, files = _fixtures()
    approval, digest = _pins()
    _raises_code(lambda c: c.verify_offline_tokenizer_asset_bundle(manifest, {"LICENSE": files["LICENSE"]}, expected_approval_ref=approval, expected_bundle_digest=digest), "missing_file")
    supplied = dict(files, extra=b"x")
    _raises_code(lambda c: c.verify_offline_tokenizer_asset_bundle(manifest, supplied, expected_approval_ref=approval, expected_bundle_digest=digest), "unexpected_file")
    manifest["files"][0]["sha256"] = "sha256:" + "0" * 64
    _raises_code(lambda c: c.verify_offline_tokenizer_asset_bundle(manifest, files, expected_approval_ref=approval, expected_bundle_digest=digest), "file_digest_mismatch")


def test_rmt9_p0a_unsigned_manifest_and_bundle_digest_framing_matches_known_vectors():
    contract = _contract()
    manifest, files = _fixtures()
    canonical = contract.canonical_unsigned_offline_tokenizer_asset_manifest_bytes(manifest)
    assert len(canonical) == 434
    assert hashlib.sha256(canonical).hexdigest() == "e1181fe1b4284a144a74f7b5b668fa5f49d4ebd82ec393c98133fd7cbb94d44b"
    unsigned = "sha256:" + hashlib.sha256(b"odysseus.offline_tokenizer_asset_manifest.v1\0" + struct.pack(">Q", len(canonical)) + canonical).hexdigest()
    assert unsigned == "sha256:2da522394c367e62bfab83c409545afc5d1426ee92802c4d208d37af5ad0fa51"
    bundle = b"odysseus.offline_tokenizer_asset_bundle.v1\0" + struct.pack(">Q", len(canonical)) + canonical + struct.pack(">I", 2)
    for path in sorted(files):
        raw = files[path]
        bundle += struct.pack(">H", len(path.encode("utf-8"))) + path.encode("utf-8") + struct.pack(">Q", len(raw)) + hashlib.sha256(raw).digest()
    expected_bundle = "sha256:6779039266c84c14308553dbd7c3024c36bd2afca630cb824c18e284fd4ad773"
    assert "sha256:" + hashlib.sha256(bundle).hexdigest() == expected_bundle
    report = contract.verify_offline_tokenizer_asset_bundle(manifest, files, expected_approval_ref="approval:sha256:" + "c" * 64, expected_bundle_digest=expected_bundle)
    assert report["unsigned_manifest_digest"] == unsigned
    assert report["bundle_digest"] == expected_bundle


def test_rmt9_p0a_report_fields_are_content_free_and_canonical_revalidation_rehashes_files_and_pins():
    contract = _contract()
    manifest, files = _fixtures()
    canonical = contract.canonical_unsigned_offline_tokenizer_asset_manifest_bytes(manifest)
    bundle = b"odysseus.offline_tokenizer_asset_bundle.v1\0" + struct.pack(">Q", len(canonical)) + canonical + struct.pack(">I", 2)
    for path in sorted(files):
        bundle += struct.pack(">H", len(path.encode())) + path.encode() + struct.pack(">Q", len(files[path])) + hashlib.sha256(files[path]).digest()
    approval = "approval:sha256:" + "d" * 64
    digest = "sha256:" + hashlib.sha256(bundle).hexdigest()
    report = contract.verify_offline_tokenizer_asset_bundle(manifest, files, expected_approval_ref=approval, expected_bundle_digest=digest)
    assert tuple(report) == contract.OFFLINE_TOKENIZER_ASSET_REPORT_FIELDS
    rendered = contract.canonical_offline_tokenizer_asset_verification_bytes(report, files, expected_approval_ref=approval, expected_bundle_digest=digest)
    assert rendered == json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    changed = dict(files)
    changed["LICENSE"] += b"!"
    _raises_code(lambda c: c.canonical_offline_tokenizer_asset_verification_bytes(report, changed, expected_approval_ref=approval, expected_bundle_digest=digest), "size_mismatch")


def test_rmt9_p0a_hostile_keys_protocol_subclasses_baseexception_and_alias_paths_do_not_escape():
    class HostileStr(str):
        def __len__(self): raise BaseException("no")
    class HostileCode(str):
        def __eq__(self, other): raise BaseException("no")
    class ExactStr(str): pass
    class HostileDict(dict): pass
    _raises_code(lambda c: c.canonical_unsigned_offline_tokenizer_asset_manifest_bytes(HostileDict()), "invalid_manifest")
    _raises_code(lambda c: c.canonical_unsigned_offline_tokenizer_asset_manifest_bytes({"schema": HostileStr("x")}), "invalid_manifest")
    manifest, _ = _fixtures()
    manifest["schema"] = ExactStr("odysseus.offline_tokenizer_asset_manifest.v1")
    _raises_code(lambda c: c.canonical_unsigned_offline_tokenizer_asset_manifest_bytes(manifest), "invalid_manifest")
    manifest, _ = _fixtures()
    manifest["files"][0]["path"] = "caf\u00e9"
    _raises_code(lambda c: c.canonical_unsigned_offline_tokenizer_asset_manifest_bytes(manifest), "invalid_path")
    manifest, _ = _fixtures()
    manifest["source_uri"] = "https://assets.example.org/caf\u00e9"
    _raises_code(lambda c: c.canonical_unsigned_offline_tokenizer_asset_manifest_bytes(manifest), "invalid_source_uri")
    manifest, _ = _fixtures()
    manifest["files"][0]["path"] = "A"
    manifest["files"].append({"path": "a", "role": "normalizer_config", "size_bytes": 1, "sha256": "sha256:" + "0" * 64})
    _raises_code(lambda c: c.canonical_unsigned_offline_tokenizer_asset_manifest_bytes(manifest), "invalid_path")
    public = _contract().OfflineTokenizerAssetContractError(HostileCode("invalid_manifest"))
    assert public.code == "verification_failed"
    assert str(public) == "verification_failed"


def test_rmt9_p0a_mutation_order_and_large_byte_budgets_are_deterministic():
    contract = _contract()
    manifest, files = _fixtures()
    base = contract.canonical_unsigned_offline_tokenizer_asset_manifest_bytes(manifest)
    for _ in range(3):
        manifest["files"].reverse()
        assert contract.canonical_unsigned_offline_tokenizer_asset_manifest_bytes(manifest) == base
    oversized, _ = _fixtures()
    oversized["tokenizer_ref"] = "x" * 129
    _raises_code(lambda c: c.canonical_unsigned_offline_tokenizer_asset_manifest_bytes(oversized), "budget_exceeded")


def test_rmt9_p0a_import_has_no_filesystem_tokenizer_network_download_provider_private_or_live_effect(monkeypatch):
    import builtins
    calls = []
    original_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        if name.split(".")[0] in {"os", "pathlib", "requests", "urllib", "transformers", "tokenizers"}:
            calls.append(name)
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    importlib.reload(_contract())
    assert calls == []
