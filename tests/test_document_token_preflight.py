"""RMT8 contract tests: synthetic, content-free token inventory snapshots only."""

from __future__ import annotations

import inspect
import hashlib
import json
import math

import pytest

try:
    from src import document_token_inventory_contract as contract
except ImportError:  # Tests-first evidence when the claimed source is absent.
    contract = None


def _api():
    assert contract is not None, "claimed source src.document_token_inventory_contract.py is absent"
    return contract


def _document(*, mode="exact", content_tokens=10, status="counted", reason=None, tasks=None):
    return {
        "source_digest": "sha256:" + "a" * 64,
        "extraction_profile_ref": "extract-v1",
        "tokenizer_ref": "offline-tokenizer",
        "tokenizer_revision": "r1",
        "tokenizer_digest": "sha256:" + "b" * 64,
        "tokenizer_mode": mode,
        "status": status,
        "blocked_reason": reason,
        "characters": 12,
        "utf8_bytes": 14,
        "content_tokens": content_tokens if status == "counted" else None,
        "tasks": tasks if tasks is not None else [{
            "task_ref": "embed-v1",
            "prompt_contract_ref": "prompt-v1",
            "special_tokens_included": 2,
            "effective_input_tokens": (content_tokens if status == "counted" else 0) + 5,
            "model_context_tokens": 8,
        }],
        "chunk_budget_tokens": 6,
        "chunk_overlap_tokens": 2,
    }


def _project(files, skipped_files=()):
    skipped_files = list(skipped_files)
    manifest = {
        "files": [{"file_ref": file["file_ref"], "source_digest": file["source_digest"]} for file in sorted(files, key=lambda item: item["file_ref"])],
        "skipped_files": [{"file_ref": file["file_ref"], "reason": file["reason"]} for file in sorted(skipped_files, key=lambda item: item["file_ref"])],
    }
    return {
        "source_digest": "sha256:" + hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
        "extraction_profile_ref": "extract-v1",
        "tokenizer_ref": "offline-tokenizer",
        "tokenizer_revision": "r1",
        "tokenizer_digest": "sha256:" + "b" * 64,
        "tokenizer_mode": "exact",
        "status": "counted",
        "blocked_reason": None,
        "chunk_budget_tokens": 6,
        "chunk_overlap_tokens": 2,
        "files": files,
        "skipped_files": skipped_files,
    }


def _file(ref, document, language="python", type_ref="code"):
    return {"file_ref": "sha256:" + ref * 64, "language_ref": language, "type_ref": type_ref, **document}


def test_rmt8_public_api_constants_signatures_and_exact_field_sets():
    c = _api()
    assert c.DOCUMENT_TOKEN_INVENTORY_SCHEMA == "odysseus.document_token_inventory.v1"
    assert c.MAX_TOKEN_INVENTORY_CANONICAL_BYTES == 4_194_304
    assert list(inspect.signature(c.project_document_token_inventory).parameters) == ["snapshot"]
    assert list(inspect.signature(c.project_project_token_inventory).parameters) == ["snapshot"]
    report = c.project_document_token_inventory(_document())
    assert set(report) == set(c.DOCUMENT_TOKEN_INVENTORY_FIELDS)
    assert set(report["tasks"][0]) == set(c.TASK_TOKEN_INVENTORY_FIELDS)
    assert c.canonical_token_inventory_bytes(report).startswith(b'{"blocked_reason":null')


def test_rmt8_document_fixture_matrix_is_deterministic_after_normalization_before_chunking():
    c = _api()
    fixtures = [_document(content_tokens=0), _document(content_tokens=3), _document(content_tokens=10)]
    assert [c.project_document_token_inventory(value) for value in fixtures] == [
        c.project_document_token_inventory(value) for value in fixtures
    ]
    for field, value in (("source_digest", "sha256:" + "A" * 64), ("tokenizer_digest", "sha256:" + "1" * 63), ("tokenizer_ref", "not/a/ref")):
        snapshot = _document()
        snapshot[field] = value
        with pytest.raises(c.DocumentTokenInventoryError):
            c.project_document_token_inventory(snapshot)


def test_rmt8_content_and_task_effective_counts_are_separate_and_context_overrun_never_truncates():
    c = _api()
    report = c.project_document_token_inventory(_document(content_tokens=10))
    assert report["content_tokens"] == 10
    assert report["tasks"][0]["effective_input_tokens"] == 15
    assert report["tasks"][0]["model_context_tokens"] == 8
    assert report["truncated"] is False


def test_rmt8_counted_effective_input_includes_content_and_special_tokens():
    c = _api()
    equality = _document(content_tokens=10)
    equality["tasks"][0]["effective_input_tokens"] = 12
    assert c.project_document_token_inventory(equality)["tasks"][0]["effective_input_tokens"] == 12

    for effective in (2, 11):
        under_bound = _document(content_tokens=10)
        under_bound["tasks"][0]["effective_input_tokens"] = effective
        with pytest.raises(c.DocumentTokenInventoryError) as error:
            c.project_document_token_inventory(under_bound)
        assert str(error.value) == "invalid_task"

    overflow = _document(content_tokens=c.MAX_TOKEN_INVENTORY_COUNT, tasks=[{
        "task_ref": "embed-v1",
        "prompt_contract_ref": "prompt-v1",
        "special_tokens_included": 1,
        "effective_input_tokens": c.MAX_TOKEN_INVENTORY_COUNT,
        "model_context_tokens": 8,
    }])
    with pytest.raises(c.DocumentTokenInventoryError) as overflow_error:
        c.project_document_token_inventory(overflow)
    assert str(overflow_error.value) == "invalid_task"

    hostile = _document(content_tokens=10)
    hostile["tasks"][0]["effective_input_tokens"] = True
    with pytest.raises(c.DocumentTokenInventoryError) as hostile_error:
        c.project_document_token_inventory(hostile)
    assert str(hostile_error.value) == "invalid_task"


def test_rmt8_blocked_tasks_allow_declared_special_tokens_with_zero_effective_input():
    c = _api()
    blocked_task = {
        "task_ref": "embed-v1",
        "prompt_contract_ref": "prompt-v1",
        "special_tokens_included": 2,
        "effective_input_tokens": 0,
        "model_context_tokens": 8,
    }
    blocked = _document(
        status="exact_count_blocked",
        reason="tokenizer_asset_missing",
        tasks=[blocked_task],
    )
    report = c.project_document_token_inventory(blocked)
    assert report["content_tokens"] is None
    assert report["tasks"] == [blocked_task]

    nonzero = _document(
        status="exact_count_blocked",
        reason="tokenizer_asset_missing",
        tasks=[{**blocked_task, "effective_input_tokens": 1}],
    )
    fallback = _document(
        mode="estimated_fallback",
        status="exact_count_blocked",
        reason="tokenizer_asset_missing",
        tasks=[blocked_task],
    )
    content_present = {**blocked, "content_tokens": 1}
    unknown_reason = {**blocked, "blocked_reason": "unknown"}
    for invalid in (nonzero, fallback, content_present, unknown_reason):
        with pytest.raises(c.DocumentTokenInventoryError) as error:
            c.project_document_token_inventory(invalid)
        assert str(error.value) in {"invalid_task", "invalid_snapshot"}


def test_rmt8_chunk_projection_uses_content_count_and_overlap_with_empty_document_zero_chunks():
    c = _api()
    empty = c.project_document_token_inventory(_document(content_tokens=0, tasks=[]))
    populated = c.project_document_token_inventory(_document(content_tokens=10))
    assert (empty["minimum_chunk_count"], empty["projected_chunk_count"]) == (0, 0)
    assert (populated["minimum_chunk_count"], populated["projected_chunk_count"]) == (2, 2)


def test_rmt8_estimated_fallback_never_projects_as_exact_and_blocked_exact_counts_are_null_with_fixed_reason():
    c = _api()
    fallback = c.project_document_token_inventory(_document(mode="estimated_fallback"))
    assert fallback["tokenizer_mode"] == "estimated_fallback"
    assert fallback["status"] == "counted"
    blocked = c.project_document_token_inventory(_document(status="exact_count_blocked", reason="tokenizer_asset_missing", tasks=[]))
    assert blocked["content_tokens"] is None
    assert blocked["minimum_chunk_count"] is None
    assert blocked["projected_chunk_count"] is None
    bad_blocked_content = _document(status="exact_count_blocked", reason="tokenizer_asset_missing", tasks=[])
    bad_blocked_content["content_tokens"] = 1
    for snapshot in (_document(mode="estimated_fallback", status="exact_count_blocked", reason="tokenizer_asset_missing", tasks=[]), _document(reason="tokenizer_asset_missing"), bad_blocked_content):
        with pytest.raises(c.DocumentTokenInventoryError):
            c.project_document_token_inventory(snapshot)


def test_rmt8_project_sums_individually_counted_files_without_delimiter_tokens():
    c = _api()
    first, second = _document(content_tokens=10), _document(content_tokens=3)
    report = c.project_project_token_inventory(_project([_file("d", first), _file("c", second)]))
    assert report["content_tokens"] == 13
    assert report["characters"] == 24
    assert report["utf8_bytes"] == 28
    assert report["projected_chunk_count"] == 3
    assert [file["file_ref"] for file in report["files"]] == ["sha256:" + "c" * 64, "sha256:" + "d" * 64]
    assert report["tasks"] == [{"task_ref": "embed-v1", "prompt_contract_ref": "prompt-v1", "special_tokens_included": 4, "effective_input_tokens": 23, "model_context_tokens": 8}]


def test_rmt8_project_language_type_file_and_skipped_reason_aggregates_are_exact():
    c = _api()
    report = c.project_project_token_inventory(_project(
        [_file("d", _document(content_tokens=3), "python", "code"), _file("c", _document(content_tokens=2), "python", "code")],
        [{"file_ref": "sha256:" + "e" * 64, "reason": "binary_file"}],
    ))
    assert report["included_file_count"] == 2
    assert report["skipped_file_count"] == 1
    assert report["language_aggregates"] == [{"ref": "python", "file_count": 2, "characters": 24, "utf8_bytes": 28, "content_tokens": 5, "tasks": 2, "minimum_chunk_count": 2, "projected_chunk_count": 2}]
    assert report["type_aggregates"][0]["ref"] == "code"
    assert report["skipped_reason_counts"] == [{"reason": "binary_file", "file_count": 1}]
    bad = _project([_file("c", _document())], [{"file_ref": "sha256:" + "c" * 64, "reason": "binary_file"}])
    with pytest.raises(c.DocumentTokenInventoryError):
        c.project_project_token_inventory(bad)


def test_rmt8_reports_are_content_free_and_reject_raw_path_content_snippet_or_token_id_shapes():
    c = _api()
    for key in ("path", "raw_path", "content", "snippet", "token_ids"):
        snapshot = _document()
        snapshot[key] = "private"
        with pytest.raises(c.DocumentTokenInventoryError):
            c.project_document_token_inventory(snapshot)
    report = c.project_document_token_inventory(_document())
    encoded = c.canonical_token_inventory_bytes(report).decode("utf-8")
    assert "private" not in encoded


def test_rmt8_hostile_exact_type_nonfinite_unicode_duplicate_and_budget_boundaries_fail_content_free():
    c = _api()
    class HostileDict(dict):
        pass
    calls = []
    class CollidingKey:
        def __hash__(self):
            return hash("source_digest")
        def __eq__(self, other):
            calls.append(other)
            raise BaseException("must not escape")
    for snapshot in (HostileDict(_document()), {**_document(), "characters": True}, {**_document(), "source_digest": "e\u0301" * 80}, {**_document(), "content_tokens": c.MAX_TOKEN_INVENTORY_COUNT + 1}):
        with pytest.raises(c.DocumentTokenInventoryError) as error:
            c.project_document_token_inventory(snapshot)
        assert str(error.value) in c.DOCUMENT_TOKEN_INVENTORY_ERROR_CODES
    with pytest.raises(c.DocumentTokenInventoryError):
        c.canonical_token_inventory_bytes({"x": math.nan})
    with pytest.raises(c.DocumentTokenInventoryError) as first:
        c.project_document_token_inventory({CollidingKey(): "ignored"})
    with pytest.raises(c.DocumentTokenInventoryError) as second:
        c.project_document_token_inventory({CollidingKey(): "ignored"})
    assert calls == []
    assert first.value is not second.value
    assert first.value.__cause__ is None and first.value.__suppress_context__ is True


def test_rmt8_canonical_bytes_are_stable_bounded_and_detached_from_post_call_mutation():
    c = _api()
    snapshot = _document()
    report = c.project_document_token_inventory(snapshot)
    original = c.canonical_token_inventory_bytes(report)
    snapshot["tasks"][0]["effective_input_tokens"] = 999
    assert c.canonical_token_inventory_bytes(report) == original
    assert c.canonical_token_inventory_bytes(report) == c.canonical_token_inventory_bytes(dict(reversed(list(report.items()))))
    forged = dict(report)
    forged["content_tokens"] = 11
    with pytest.raises(c.DocumentTokenInventoryError):
        c.canonical_token_inventory_bytes(forged)
    with pytest.raises(c.DocumentTokenInventoryError):
        c.canonical_token_inventory_bytes({"x": "a" * (c.MAX_TOKEN_INVENTORY_CANONICAL_BYTES + 1)})


def test_rmt8_import_and_projection_use_no_estimator_asset_model_provider_download_file_network_private_or_live_effect():
    c = _api()
    assert c.project_document_token_inventory(_document())["provider_call_used"] is False
    assert c.project_project_token_inventory(_project([]))["runtime_download_used"] is False
    source = inspect.getsource(c)
    for forbidden in ("token_estimator", "open(", "requests", "socket", "subprocess", "threading", "provider"):
        assert forbidden not in source or forbidden == "provider"
