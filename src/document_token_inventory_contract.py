"""Content-free RMT8 projections for already-counted token inventory snapshots.

This module deliberately does not tokenize, extract, discover assets, or perform
any I/O.  It validates a small exact-built-in snapshot and returns detached JSON
data suitable for an offline preflight report.
"""

from __future__ import annotations

import json
import hashlib
import math
import unicodedata


DOCUMENT_TOKEN_INVENTORY_SCHEMA = "odysseus.document_token_inventory.v1"
MAX_TOKEN_INVENTORY_CANONICAL_BYTES = 4_194_304
MAX_TOKEN_INVENTORY_COUNT = 9_007_199_254_740_991
MAX_TOKEN_INVENTORY_DEPTH = 8
MAX_TOKEN_INVENTORY_NODES = 100_000
MAX_TOKEN_INVENTORY_REF_CHARS = 128
MAX_TOKEN_INVENTORY_TASKS = 16
MAX_TOKEN_INVENTORY_FILES = 4_096
MAX_TOKEN_INVENTORY_AGGREGATES = 512
MAX_TOKEN_INVENTORY_SKIPPED = 4_096

DOCUMENT_TOKEN_INVENTORY_ERROR_CODES = frozenset({
    "invalid_snapshot", "invalid_count", "invalid_task", "invalid_project", "budget_exceeded", "projection_failed",
})
BLOCKED_REASONS = frozenset({
    "tokenizer_asset_missing", "tokenizer_asset_not_approved", "tokenizer_asset_revision_mismatch",
    "tokenizer_asset_digest_mismatch", "unsupported_tokenizer", "unsupported_prompt_contract",
    "extraction_safety_limit_exceeded", "normalization_failed", "count_budget_exceeded",
})
SKIPPED_REASONS = frozenset({
    "binary_file", "vendor_file", "generated_file", "excluded_by_profile", "unsupported_media_type",
    "extraction_failed", "normalization_failed", "file_safety_limit_exceeded", "duplicate_content",
})

DOCUMENT_TOKEN_INVENTORY_FIELDS = (
    "schema", "source_type", "status", "blocked_reason", "source_digest", "extraction_profile_ref",
    "tokenizer_ref", "tokenizer_revision", "tokenizer_digest", "tokenizer_mode", "characters", "utf8_bytes",
    "content_tokens", "tasks", "chunk_budget_tokens", "chunk_overlap_tokens", "minimum_chunk_count",
    "projected_chunk_count", "truncated", "raw_content_visible", "cleartext_paths_visible", "snippets_visible",
    "token_ids_visible", "provider_call_used", "runtime_download_used", "live_action_authorized", "reindex_authorized",
)
TASK_TOKEN_INVENTORY_FIELDS = (
    "task_ref", "prompt_contract_ref", "special_tokens_included", "effective_input_tokens", "model_context_tokens",
)
FILE_TOKEN_INVENTORY_FIELDS = (
    "file_ref", "source_digest", "extraction_profile_ref", "language_ref", "type_ref", "characters", "utf8_bytes",
    "content_tokens", "tasks", "minimum_chunk_count", "projected_chunk_count",
)
AGGREGATE_TOKEN_INVENTORY_FIELDS = (
    "ref", "file_count", "characters", "utf8_bytes", "content_tokens", "tasks", "minimum_chunk_count", "projected_chunk_count",
)
PROJECT_TOKEN_INVENTORY_FIELDS = DOCUMENT_TOKEN_INVENTORY_FIELDS + (
    "included_file_count", "skipped_file_count", "files", "language_aggregates", "type_aggregates", "skipped_files", "skipped_reason_counts",
)


class DocumentTokenInventoryError(ValueError):
    """A deliberately content-free public error."""


def _error(code: str) -> DocumentTokenInventoryError:
    return DocumentTokenInventoryError(code if code in DOCUMENT_TOKEN_INVENTORY_ERROR_CODES else "projection_failed")


def _capture(value: object, depth: int = 0, nodes: list[int] | None = None) -> object:
    """Copy only exact JSON builtins before any semantic lookup or callback."""
    if nodes is None:
        nodes = [0]
    if depth > MAX_TOKEN_INVENTORY_DEPTH:
        raise _error("budget_exceeded")
    nodes[0] += 1
    if nodes[0] > MAX_TOKEN_INVENTORY_NODES:
        raise _error("budget_exceeded")
    kind = type(value)
    if kind is dict:
        copied: dict[str, object] = {}
        for key, child in value.items():
            if type(key) is not str or not _identifier(key):
                raise _error("invalid_snapshot")
            copied[key] = _capture(child, depth + 1, nodes)
        return copied
    if kind is list:
        return [_capture(child, depth + 1, nodes) for child in value]
    if kind is str:
        if len(value) > MAX_TOKEN_INVENTORY_REF_CHARS:
            raise _error("budget_exceeded")
        if unicodedata.normalize("NFC", value) != value:
            raise _error("invalid_snapshot")
        return value
    if kind is int:
        if abs(value) > MAX_TOKEN_INVENTORY_COUNT:
            raise _error("invalid_count")
        return value
    if kind is bool or value is None:
        return value
    raise _error("invalid_snapshot")


def _mapping(value: object, code: str) -> dict[str, object]:
    if type(value) is not dict:
        raise _error(code)
    return value


def _exact_keys(value: dict[str, object], fields: tuple[str, ...] | set[str], code: str) -> None:
    if set(value) != set(fields):
        raise _error(code)


def _ref(value: object, code: str) -> str:
    if type(value) is not str or not _identifier(value):
        raise _error(code)
    return value


def _identifier(value: str) -> bool:
    if not value or len(value) > MAX_TOKEN_INVENTORY_REF_CHARS or unicodedata.normalize("NFC", value) != value:
        return False
    first = value[0]
    if not ("A" <= first <= "Z" or "a" <= first <= "z" or "0" <= first <= "9"):
        return False
    return all("A" <= char <= "Z" or "a" <= char <= "z" or "0" <= char <= "9" or char in "._:@+-" for char in value)


def _digest(value: object, code: str) -> str:
    if type(value) is not str or len(value) != 71 or not value.startswith("sha256:"):
        raise _error(code)
    for char in value[7:]:
        if char not in "0123456789abcdef":
            raise _error(code)
    return value


def _count(value: object, code: str) -> int:
    if type(value) is not int or value < 0 or value > MAX_TOKEN_INVENTORY_COUNT:
        raise _error(code)
    return value


def _sum(values: list[int], code: str) -> int:
    total = 0
    for value in values:
        if value < 0 or value > MAX_TOKEN_INVENTORY_COUNT - total:
            raise _error(code)
        total += value
    return total


def _chunk_counts(content_tokens: int, budget: int, overlap: int) -> tuple[int, int]:
    if budget <= 0 or overlap < 0 or overlap >= budget:
        raise _error("invalid_count")
    if content_tokens == 0:
        return 0, 0
    minimum = 1 + (content_tokens - 1) // budget
    stride = budget - overlap
    projected = 1 + (max(0, content_tokens - budget) + stride - 1) // stride
    return minimum, projected


def _tasks(value: object, content_tokens: int, blocked: bool) -> list[dict[str, object]]:
    if type(value) is not list or len(value) > MAX_TOKEN_INVENTORY_TASKS:
        raise _error("invalid_task")
    tasks: list[dict[str, object]] = []
    task_refs: set[str] = set()
    for item in value:
        task = _mapping(item, "invalid_task")
        _exact_keys(task, TASK_TOKEN_INVENTORY_FIELDS, "invalid_task")
        special = _count(task["special_tokens_included"], "invalid_task")
        effective = _count(task["effective_input_tokens"], "invalid_task")
        context = _count(task["model_context_tokens"], "invalid_task")
        if blocked:
            if effective != 0:
                raise _error("invalid_task")
        elif effective < _sum([content_tokens, special], "invalid_task"):
            raise _error("invalid_task")
        task_ref = _ref(task["task_ref"], "invalid_task")
        if task_ref in task_refs:
            raise _error("invalid_task")
        task_refs.add(task_ref)
        tasks.append({
            "task_ref": task_ref,
            "prompt_contract_ref": _ref(task["prompt_contract_ref"], "invalid_task"),
            "special_tokens_included": special,
            "effective_input_tokens": effective,
            "model_context_tokens": context,
        })
    return tasks


_DOCUMENT_INPUT_FIELDS = (
    "source_digest", "extraction_profile_ref", "tokenizer_ref", "tokenizer_revision", "tokenizer_digest",
    "tokenizer_mode", "status", "blocked_reason", "characters", "utf8_bytes", "content_tokens", "tasks",
    "chunk_budget_tokens", "chunk_overlap_tokens",
)


def _document(snapshot: object) -> dict[str, object]:
    data = _mapping(snapshot, "invalid_snapshot")
    _exact_keys(data, _DOCUMENT_INPUT_FIELDS, "invalid_snapshot")
    status = data["status"]
    mode = data["tokenizer_mode"]
    if status not in ("counted", "exact_count_blocked") or mode not in ("exact", "estimated_fallback"):
        raise _error("invalid_snapshot")
    blocked = status == "exact_count_blocked"
    reason = data["blocked_reason"]
    if blocked:
        if mode != "exact" or reason not in BLOCKED_REASONS or data["content_tokens"] is not None:
            raise _error("invalid_snapshot")
        content: int | None = None
    else:
        if reason is not None:
            raise _error("invalid_snapshot")
        content = _count(data["content_tokens"], "invalid_count")
    budget = _count(data["chunk_budget_tokens"], "invalid_count")
    overlap = _count(data["chunk_overlap_tokens"], "invalid_count")
    tasks = _tasks(data["tasks"], content or 0, blocked)
    minimum, projected = (None, None) if blocked else _chunk_counts(content or 0, budget, overlap)
    return {
        "schema": DOCUMENT_TOKEN_INVENTORY_SCHEMA, "source_type": "document", "status": status, "blocked_reason": reason,
        "source_digest": _digest(data["source_digest"], "invalid_snapshot"),
        "extraction_profile_ref": _ref(data["extraction_profile_ref"], "invalid_snapshot"),
        "tokenizer_ref": _ref(data["tokenizer_ref"], "invalid_snapshot"),
        "tokenizer_revision": _ref(data["tokenizer_revision"], "invalid_snapshot"),
        "tokenizer_digest": _digest(data["tokenizer_digest"], "invalid_snapshot"), "tokenizer_mode": mode,
        "characters": _count(data["characters"], "invalid_count"), "utf8_bytes": _count(data["utf8_bytes"], "invalid_count"),
        "content_tokens": content, "tasks": tasks, "chunk_budget_tokens": budget, "chunk_overlap_tokens": overlap,
        "minimum_chunk_count": minimum, "projected_chunk_count": projected, "truncated": False,
        "raw_content_visible": False, "cleartext_paths_visible": False, "snippets_visible": False, "token_ids_visible": False,
        "provider_call_used": False, "runtime_download_used": False, "live_action_authorized": False, "reindex_authorized": False,
    }


def project_document_token_inventory(snapshot: object, /) -> dict[str, object]:
    """Project one already-normalized, already-counted document snapshot."""
    try:
        return _document(_capture(snapshot))
    except DocumentTokenInventoryError as error:
        raise _error(str(error)) from None
    except BaseException:
        raise _error("projection_failed") from None


def _aggregate(files: list[dict[str, object]], key: str) -> list[dict[str, object]]:
    groups: dict[str, list[dict[str, object]]] = {}
    for file in files:
        ref = file[key]
        assert type(ref) is str
        groups.setdefault(ref, []).append(file)
    if len(groups) > MAX_TOKEN_INVENTORY_AGGREGATES:
        raise _error("budget_exceeded")
    result: list[dict[str, object]] = []
    for ref in sorted(groups):
        records = groups[ref]
        result.append({
            "ref": ref, "file_count": len(records),
            "characters": _sum([record["characters"] for record in records], "invalid_project"),
            "utf8_bytes": _sum([record["utf8_bytes"] for record in records], "invalid_project"),
            "content_tokens": _sum([record["content_tokens"] for record in records], "invalid_project"),
            "tasks": _sum([len(record["tasks"]) for record in records], "invalid_project"),
            "minimum_chunk_count": _sum([record["minimum_chunk_count"] for record in records], "invalid_project"),
            "projected_chunk_count": _sum([record["projected_chunk_count"] for record in records], "invalid_project"),
        })
    return result


def project_project_token_inventory(snapshot: object, /) -> dict[str, object]:
    """Project a project as checked sums of individually counted file snapshots."""
    try:
        data = _mapping(_capture(snapshot), "invalid_project")
        fields = set(_DOCUMENT_INPUT_FIELDS) - {"characters", "utf8_bytes", "content_tokens", "tasks"}
        fields.update(("files", "skipped_files"))
        _exact_keys(data, fields, "invalid_project")
        files_input = data["files"]
        skipped = data["skipped_files"]
        if type(files_input) is not list or type(skipped) is not list or len(files_input) > MAX_TOKEN_INVENTORY_FILES or len(skipped) > MAX_TOKEN_INVENTORY_SKIPPED:
            raise _error("budget_exceeded")
        base_input = {name: data[name] for name in _DOCUMENT_INPUT_FIELDS if name in data}
        base_input.update({"characters": 0, "utf8_bytes": 0, "content_tokens": 0, "tasks": []})
        base = _document(base_input)
        if base["status"] != "counted":
            raise _error("invalid_project")
        files: list[dict[str, object]] = []
        file_refs: set[str] = set()
        common_fields = ("extraction_profile_ref", "tokenizer_ref", "tokenizer_revision", "tokenizer_digest", "tokenizer_mode", "status", "chunk_budget_tokens", "chunk_overlap_tokens")
        task_contract: list[dict[str, object]] | None = None
        for item in files_input:
            raw = _mapping(item, "invalid_project")
            _exact_keys(raw, set(_DOCUMENT_INPUT_FIELDS) | {"file_ref", "language_ref", "type_ref"}, "invalid_project")
            doc_input = {name: raw[name] for name in _DOCUMENT_INPUT_FIELDS}
            report = _document(doc_input)
            if report["status"] != "counted":
                raise _error("invalid_project")
            if any(report[field] != base[field] for field in common_fields):
                raise _error("invalid_project")
            file_ref = _digest(raw["file_ref"], "invalid_project")
            if file_ref in file_refs:
                raise _error("invalid_project")
            file_refs.add(file_ref)
            contract = [{key: task[key] for key in ("task_ref", "prompt_contract_ref", "special_tokens_included", "model_context_tokens")} for task in report["tasks"]]
            if task_contract is None:
                task_contract = contract
            elif task_contract != contract:
                raise _error("invalid_project")
            files.append({
                "file_ref": file_ref, "source_digest": report["source_digest"],
                "extraction_profile_ref": report["extraction_profile_ref"], "language_ref": _ref(raw["language_ref"], "invalid_project"),
                "type_ref": _ref(raw["type_ref"], "invalid_project"), "characters": report["characters"], "utf8_bytes": report["utf8_bytes"],
                "content_tokens": report["content_tokens"], "tasks": report["tasks"], "minimum_chunk_count": report["minimum_chunk_count"],
                "projected_chunk_count": report["projected_chunk_count"],
            })
        files.sort(key=lambda record: record["file_ref"])
        clean_skipped: list[dict[str, object]] = []
        reasons: dict[str, int] = {}
        skipped_refs: set[str] = set()
        for item in skipped:
            skipped_item = _mapping(item, "invalid_project")
            _exact_keys(skipped_item, ("file_ref", "reason"), "invalid_project")
            reason = skipped_item["reason"]
            if reason not in SKIPPED_REASONS:
                raise _error("invalid_project")
            skipped_ref = _digest(skipped_item["file_ref"], "invalid_project")
            if skipped_ref in skipped_refs or skipped_ref in file_refs:
                raise _error("invalid_project")
            skipped_refs.add(skipped_ref)
            clean_skipped.append({"file_ref": skipped_ref, "reason": reason})
            reasons[reason] = reasons.get(reason, 0) + 1
        clean_skipped.sort(key=lambda record: record["file_ref"])
        manifest = {"files": [{"file_ref": record["file_ref"], "source_digest": record["source_digest"]} for record in files], "skipped_files": clean_skipped}
        manifest_digest = "sha256:" + hashlib.sha256(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if base["source_digest"] != manifest_digest:
            raise _error("invalid_project")
        characters = _sum([record["characters"] for record in files], "invalid_project")
        utf8_bytes = _sum([record["utf8_bytes"] for record in files], "invalid_project")
        content = _sum([record["content_tokens"] for record in files], "invalid_project")
        minimum = _sum([record["minimum_chunk_count"] for record in files], "invalid_project")
        projected = _sum([record["projected_chunk_count"] for record in files], "invalid_project")
        project_tasks: list[dict[str, object]] = []
        for index, item in enumerate(task_contract or []):
            matching = [record["tasks"][index] for record in files]
            project_tasks.append({
                **item,
                "special_tokens_included": _sum([task["special_tokens_included"] for task in matching], "invalid_project"),
                "effective_input_tokens": _sum([task["effective_input_tokens"] for task in matching], "invalid_project"),
            })
        base.update({
            "source_type": "project", "characters": characters, "utf8_bytes": utf8_bytes, "content_tokens": content,
            "tasks": project_tasks, "minimum_chunk_count": minimum, "projected_chunk_count": projected,
            "included_file_count": len(files), "skipped_file_count": len(clean_skipped), "files": files,
            "language_aggregates": _aggregate(files, "language_ref"), "type_aggregates": _aggregate(files, "type_ref"),
            "skipped_files": clean_skipped, "skipped_reason_counts": [{"reason": reason, "file_count": reasons[reason]} for reason in sorted(reasons)],
        })
        return base
    except DocumentTokenInventoryError as error:
        raise _error(str(error)) from None
    except BaseException:
        raise _error("projection_failed") from None


def _validate_report(report: object) -> object:
    data = _mapping(report, "invalid_snapshot")
    source_type = data.get("source_type")
    if source_type == "document":
        _exact_keys(data, DOCUMENT_TOKEN_INVENTORY_FIELDS, "invalid_snapshot")
        snapshot = {name: data[name] for name in _DOCUMENT_INPUT_FIELDS}
        if _document(snapshot) != data:
            raise _error("invalid_snapshot")
        return data
    if source_type != "project":
        raise _error("invalid_snapshot")
    _exact_keys(data, PROJECT_TOKEN_INVENTORY_FIELDS, "invalid_project")
    files = data["files"]
    if type(files) is not list:
        raise _error("invalid_project")
    raw_files: list[dict[str, object]] = []
    for item in files:
        file = _mapping(item, "invalid_project")
        _exact_keys(file, FILE_TOKEN_INVENTORY_FIELDS, "invalid_project")
        raw = {name: data[name] for name in _DOCUMENT_INPUT_FIELDS}
        raw.update({
            "source_digest": file["source_digest"], "characters": file["characters"], "utf8_bytes": file["utf8_bytes"],
            "content_tokens": file["content_tokens"], "tasks": file["tasks"], "file_ref": file["file_ref"],
            "language_ref": file["language_ref"], "type_ref": file["type_ref"],
        })
        raw_files.append(raw)
    project_input = {name: data[name] for name in _DOCUMENT_INPUT_FIELDS if name not in {"characters", "utf8_bytes", "content_tokens", "tasks"}}
    project_input.update({"files": raw_files, "skipped_files": data["skipped_files"]})
    if project_project_token_inventory(project_input) != data:
        raise _error("invalid_project")
    return data


def canonical_token_inventory_bytes(report: object, /) -> bytes:
    """Return bounded, compact, sorted UTF-8 JSON for a detached report tree."""
    try:
        captured = _validate_report(_capture(report))
        encoded = json.dumps(captured, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_TOKEN_INVENTORY_CANONICAL_BYTES:
            raise _error("budget_exceeded")
        return encoded
    except DocumentTokenInventoryError as error:
        raise _error(str(error)) from None
    except (TypeError, ValueError, OverflowError):
        raise _error("invalid_snapshot") from None
    except BaseException:
        raise _error("projection_failed") from None
