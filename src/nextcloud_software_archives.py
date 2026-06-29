"""Dry-run software-bundle archive planning for Nextcloud ingestion.

The planner works from inventory metadata only. It detects folders that look
like software/toolchain bundles, prepares a ZIP + sidecar metadata plan, and can
record that plan in the append-only big-data ledger. It never reads file
contents, creates archives, deletes originals, or writes Nextcloud metadata.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import hashlib
import re
from typing import Any, Iterable, Mapping

from src.bigdata_ledger_contract import (
    AppendOnlyBigDataLedger,
    BigDataLedgerItem,
    BigDataLedgerRecord,
)


PLAN_SCHEMA = "odysseus.nextcloud.software_archive_plan.v1"
EXECUTABLE_SUFFIXES = frozenset(
    {
        ".exe",
        ".dll",
        ".msi",
        ".bat",
        ".cmd",
        ".ps1",
        ".sh",
        ".scr",
        ".com",
        ".jar",
    }
)
SOFTWARE_MARKER_SEGMENTS = frozenset(
    {
        ".venv",
        "bin",
        "build",
        "dist",
        "drivers",
        "driver",
        "hardware",
        "jdk",
        "jre",
        "lib",
        "libs",
        "node_modules",
        "packages",
        "runtime",
        "target",
        "toolchain",
        "tools",
        "venv",
    }
)
SOFTWARE_HINT_TOKENS = frozenset(
    {
        "arduino",
        "bridgebuilder",
        "electron",
        "energia",
        "esbuild",
        "fences",
        "installer",
        "java",
        "msp430",
        "node",
        "pointofix",
        "python",
        "setup",
        "toolchain",
    }
)
_UNSAFE_PATH_CHARS = set('<>:"|?*')
_SAFE_TOKEN_RE = re.compile(r"[^a-zA-Z0-9._-]+")
_TECHNICAL_LEAF_MARKERS = frozenset({"bin", "build", "dist", "drivers", "driver", "lib", "libs", "target", "tools"})


@dataclass(frozen=True, slots=True)
class NextcloudSoftwareBundleProfile:
    folder_path: str
    bundle_kind: str
    file_count: int
    executable_count: int
    size_bytes: int
    executable_suffix_counts: Mapping[str, int]
    top_extensions: tuple[Mapping[str, Any], ...]
    marker_segments: tuple[str, ...]
    sample_paths: tuple[str, ...]
    reason_codes: tuple[str, ...]
    review_required: bool = True

    @property
    def size_mib(self) -> float:
        return round(self.size_bytes / (1024 * 1024), 2)

    @property
    def executable_ratio(self) -> float:
        if self.file_count <= 0:
            return 0.0
        return round(self.executable_count / self.file_count, 4)

    def memory_summary(self) -> str:
        return (
            f"Software bundle at {self.folder_path} classified as {self.bundle_kind}; "
            f"{self.file_count} files, {self.executable_count} executable/library-like files, "
            f"{self.size_mib} MiB. Archive plan is metadata-only and review-gated."
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": PLAN_SCHEMA,
            "folder_path": self.folder_path,
            "bundle_kind": self.bundle_kind,
            "file_count": self.file_count,
            "executable_count": self.executable_count,
            "executable_ratio": self.executable_ratio,
            "size_bytes": self.size_bytes,
            "size_mib": self.size_mib,
            "executable_suffix_counts": dict(self.executable_suffix_counts),
            "top_extensions": tuple(dict(item) for item in self.top_extensions),
            "marker_segments": self.marker_segments,
            "sample_paths": self.sample_paths,
            "reason_codes": self.reason_codes,
            "review_required": self.review_required,
            "memory_summary": self.memory_summary(),
        }


@dataclass(frozen=True, slots=True)
class NextcloudSoftwareArchivePlan:
    profile: NextcloudSoftwareBundleProfile
    archive_path: str
    sidecar_path: str
    actions: tuple[str, ...] = ("create_zip", "write_sidecar", "memory_profile")
    dry_run: bool = True
    writes_performed: bool = False
    delete_original: bool = False
    overwrite_existing: bool = False
    execution_allowed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": PLAN_SCHEMA,
            "profile": self.profile.to_dict(),
            "archive_path": self.archive_path,
            "sidecar_path": self.sidecar_path,
            "actions": self.actions,
            "dry_run": self.dry_run,
            "writes_performed": self.writes_performed,
            "delete_original": self.delete_original,
            "overwrite_existing": self.overwrite_existing,
            "execution_allowed": self.execution_allowed,
        }


@dataclass(frozen=True, slots=True)
class SoftwareArchivePlanningResult:
    planned: int
    skipped_existing: int
    interrupted: bool
    plans: tuple[NextcloudSoftwareArchivePlan, ...]
    ledger_summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "planned": self.planned,
            "skipped_existing": self.skipped_existing,
            "interrupted": self.interrupted,
            "plans": tuple(plan.to_dict() for plan in self.plans),
            "ledger_summary": dict(self.ledger_summary),
        }


def build_nextcloud_software_archive_plans(
    records: Iterable[BigDataLedgerRecord | Mapping[str, Any]],
    *,
    source_id: str | None = None,
    target_root: str = "Software Archives",
    min_executable_files: int = 2,
    min_executable_ratio: float = 0.12,
    max_sample_paths: int = 8,
) -> tuple[NextcloudSoftwareArchivePlan, ...]:
    """Build review-gated archive plans from completed inventory metadata."""

    inventory = tuple(_inventory_records(records, source_id=source_id))
    if min_executable_files <= 0:
        raise ValueError("min_executable_files must be positive")
    if min_executable_ratio < 0 or min_executable_ratio > 1:
        raise ValueError("min_executable_ratio must be between 0 and 1")

    candidates = _candidate_prefixes(
        inventory,
        min_executable_files=min_executable_files,
        min_executable_ratio=min_executable_ratio,
    )
    selected = _select_non_overlapping(candidates)
    return tuple(
        _plan_for_candidate(
            candidate,
            target_root=target_root,
            max_sample_paths=max_sample_paths,
        )
        for candidate in selected
    )


def plan_nextcloud_software_archive_metadata(
    *,
    ledger_path: str,
    source_id: str,
    target_root: str = "Software Archives",
    batch_limit: int | None = None,
    min_executable_files: int = 2,
    min_executable_ratio: float = 0.12,
) -> SoftwareArchivePlanningResult:
    """Append dry-run ZIP + sidecar analysis records to the big-data ledger."""

    source = str(source_id or "").strip()
    if not source:
        raise ValueError("source_id must not be empty")
    if batch_limit is not None and int(batch_limit) < 0:
        raise ValueError("batch_limit must be non-negative")

    ledger = AppendOnlyBigDataLedger(ledger_path)
    latest = ledger.latest_state()
    all_plans = build_nextcloud_software_archive_plans(
        latest.values(),
        source_id=source,
        target_root=target_root,
        min_executable_files=min_executable_files,
        min_executable_ratio=min_executable_ratio,
    )
    limit = int(batch_limit) if batch_limit is not None else None
    selected = all_plans[:limit] if limit is not None else all_plans
    existing = {
        record.item.item_id
        for record in latest.values()
        if record.stage == "analysis"
        and record.item.source_id == source
        and record.metadata.get("planner") == "nextcloud_software_archive"
    }

    planned = skipped_existing = 0
    emitted: list[NextcloudSoftwareArchivePlan] = []
    for plan in selected:
        item = _archive_item(plan, source_id=source)
        if item.item_id in existing:
            skipped_existing += 1
            continue
        record = BigDataLedgerRecord.create(
            item,
            stage="analysis",
            status="needs_review",
            metadata={
                "planner": "nextcloud_software_archive",
                "dry_run": True,
                "review_required": True,
                "archive_path": plan.archive_path,
                "sidecar_path": plan.sidecar_path,
                "actions": plan.actions,
                "delete_original": False,
                "overwrite_existing": False,
                "profile": plan.profile.to_dict(),
            },
        )
        ledger.append_record(record)
        existing.add(item.item_id)
        planned += 1
        emitted.append(plan)

    return SoftwareArchivePlanningResult(
        planned=planned,
        skipped_existing=skipped_existing,
        interrupted=limit is not None and len(selected) < len(all_plans),
        plans=tuple(emitted),
        ledger_summary=ledger.summary(),
    )


def _inventory_records(
    records: Iterable[BigDataLedgerRecord | Mapping[str, Any]],
    *,
    source_id: str | None,
) -> Iterable[BigDataLedgerRecord]:
    for record in records:
        parsed = record if isinstance(record, BigDataLedgerRecord) else BigDataLedgerRecord.from_mapping(record)
        if parsed.stage != "inventory" or parsed.status != "completed":
            continue
        if source_id is not None and parsed.item.source_id != source_id:
            continue
        yield parsed


def _candidate_prefixes(
    records: tuple[BigDataLedgerRecord, ...],
    *,
    min_executable_files: int,
    min_executable_ratio: float,
) -> list[dict[str, Any]]:
    by_prefix: dict[str, list[BigDataLedgerRecord]] = {}
    for record in records:
        parts = record.item.relative_path.split("/")
        if len(parts) <= 1:
            prefix = record.item.relative_path
            by_prefix.setdefault(prefix, []).append(record)
            continue
        max_depth = min(len(parts) - 1, 6)
        for depth in range(1, max_depth + 1):
            prefix = "/".join(parts[:depth])
            by_prefix.setdefault(prefix, []).append(record)

    candidates: list[dict[str, Any]] = []
    for prefix, members in by_prefix.items():
        executable_count = sum(1 for member in members if _suffix(member.item.relative_path) in EXECUTABLE_SUFFIXES)
        if executable_count <= 0:
            continue
        marker_segments = _marker_segments(prefix) or _member_marker_segments(members)
        ratio = executable_count / max(len(members), 1)
        single_installer = len(members) == 1 and _suffix(members[0].item.relative_path) in EXECUTABLE_SUFFIXES
        if not single_installer and executable_count < min_executable_files:
            continue
        if not single_installer and ratio < min_executable_ratio and not marker_segments:
            continue
        candidates.append(
            {
                "prefix": prefix,
                "members": tuple(members),
                "executable_count": executable_count,
                "ratio": ratio,
                "marker_segments": marker_segments,
                "score": _candidate_score(prefix, members, executable_count, ratio, marker_segments),
            }
        )
    candidates.sort(key=lambda item: (-item["score"], item["prefix"].count("/"), item["prefix"]))
    return candidates


def _select_non_overlapping(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    selected_prefixes: list[str] = []
    for candidate in candidates:
        prefix = candidate["prefix"]
        if any(_is_same_or_child(prefix, chosen) or _is_same_or_child(chosen, prefix) for chosen in selected_prefixes):
            continue
        selected.append(candidate)
        selected_prefixes.append(prefix)
    selected.sort(key=lambda item: item["prefix"])
    return selected


def _plan_for_candidate(
    candidate: Mapping[str, Any],
    *,
    target_root: str,
    max_sample_paths: int,
) -> NextcloudSoftwareArchivePlan:
    prefix = str(candidate["prefix"])
    members = tuple(candidate["members"])
    executable_counts = _suffix_counts(
        member.item.relative_path
        for member in members
        if _suffix(member.item.relative_path) in EXECUTABLE_SUFFIXES
    )
    profile = NextcloudSoftwareBundleProfile(
        folder_path=prefix,
        bundle_kind=_bundle_kind(prefix, tuple(candidate["marker_segments"])),
        file_count=len(members),
        executable_count=int(candidate["executable_count"]),
        size_bytes=sum(member.item.size_bytes for member in members),
        executable_suffix_counts=executable_counts,
        top_extensions=_top_extensions(members),
        marker_segments=tuple(candidate["marker_segments"]),
        sample_paths=tuple(member.item.relative_path for member in sorted(members, key=lambda item: item.item.relative_path)[:max_sample_paths]),
        reason_codes=_reason_codes(prefix, members, int(candidate["executable_count"]), tuple(candidate["marker_segments"])),
    )
    archive_path = _archive_path(prefix, target_root=target_root)
    return NextcloudSoftwareArchivePlan(
        profile=profile,
        archive_path=archive_path,
        sidecar_path=f"{archive_path}.odysseus.json",
    )


def _archive_item(plan: NextcloudSoftwareArchivePlan, *, source_id: str) -> BigDataLedgerItem:
    return BigDataLedgerItem(
        provider="nextcloud",
        source_id=source_id,
        relative_path=plan.archive_path,
        size_bytes=plan.profile.size_bytes,
        mtime="2026-06-29T00:00:00Z",
        content_hash="sha256:" + _metadata_digest(plan.profile),
    )


def _metadata_digest(profile: NextcloudSoftwareBundleProfile) -> str:
    payload = "|".join(
        (
            profile.folder_path,
            str(profile.file_count),
            str(profile.executable_count),
            str(profile.size_bytes),
            ",".join(profile.sample_paths),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _candidate_score(
    prefix: str,
    members: tuple[BigDataLedgerRecord, ...],
    executable_count: int,
    ratio: float,
    marker_segments: tuple[str, ...],
) -> float:
    hint_bonus = 12 if _hint_tokens(prefix) else 0
    marker_bonus = min(len(marker_segments), 4) * 16
    single_installer_bonus = 25 if len(members) == 1 else 0
    depth_penalty = prefix.count("/") * 1.5
    leaf = prefix.rsplit("/", 1)[-1].casefold()
    technical_leaf_penalty = 24 if leaf in _TECHNICAL_LEAF_MARKERS else 0
    return (
        executable_count * 2
        + ratio * 50
        + marker_bonus
        + hint_bonus
        + single_installer_bonus
        - depth_penalty
        - technical_leaf_penalty
    )


def _bundle_kind(prefix: str, marker_segments: tuple[str, ...]) -> str:
    lowered = prefix.casefold()
    markers = set(marker_segments)
    if "node_modules" in markers:
        return "node_dependency_bundle"
    if {"msp430", "energia", "arduino", "toolchain"} & _hint_tokens(prefix):
        return "toolchain_bundle"
    if PurePosixPath(prefix).suffix.lower() in EXECUTABLE_SUFFIXES or {"setup", "installer"} & _hint_tokens(prefix):
        return "installer_package"
    if "jre" in markers or "jdk" in markers or "java" in lowered:
        return "runtime_bundle"
    return "software_bundle"


def _reason_codes(
    prefix: str,
    members: tuple[BigDataLedgerRecord, ...],
    executable_count: int,
    marker_segments: tuple[str, ...],
) -> tuple[str, ...]:
    reasons: list[str] = ["binary_files_excluded_from_memory"]
    if executable_count >= 2:
        reasons.append("multiple_executable_files")
    if marker_segments:
        reasons.append("software_marker_folder")
    if len(members) == 1:
        reasons.append("single_installer_or_binary")
    if _hint_tokens(prefix):
        reasons.append("software_name_hint")
    return tuple(dict.fromkeys(reasons))


def _top_extensions(records: tuple[BigDataLedgerRecord, ...]) -> tuple[Mapping[str, Any], ...]:
    buckets: dict[str, dict[str, int]] = {}
    for record in records:
        suffix = _suffix(record.item.relative_path) or "[no extension]"
        bucket = buckets.setdefault(suffix, {"count": 0, "size_bytes": 0})
        bucket["count"] += 1
        bucket["size_bytes"] += record.item.size_bytes
    rows = [
        {"extension": extension, "count": values["count"], "size_bytes": values["size_bytes"]}
        for extension, values in buckets.items()
    ]
    rows.sort(key=lambda item: (-int(item["count"]), str(item["extension"])))
    return tuple(rows[:12])


def _suffix_counts(paths: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in paths:
        suffix = _suffix(path)
        counts[suffix] = counts.get(suffix, 0) + 1
    return dict(sorted(counts.items()))


def _suffix(path: str) -> str:
    return PurePosixPath(path).suffix.lower()


def _marker_segments(path: str) -> tuple[str, ...]:
    markers = []
    for segment in path.split("/"):
        normalized = segment.strip().casefold()
        if normalized in SOFTWARE_MARKER_SEGMENTS:
            markers.append(normalized)
    return tuple(dict.fromkeys(markers))


def _member_marker_segments(records: tuple[BigDataLedgerRecord, ...]) -> tuple[str, ...]:
    markers: list[str] = []
    for record in records:
        markers.extend(_marker_segments(record.item.relative_path))
    return tuple(dict.fromkeys(markers))


def _hint_tokens(path: str) -> frozenset[str]:
    tokens = frozenset(
        token
        for token in re.split(r"[^a-zA-Z0-9]+", path.casefold())
        if token
    )
    return tokens & SOFTWARE_HINT_TOKENS


def _archive_path(folder_path: str, *, target_root: str) -> str:
    root = _normalize_relative_path(target_root)
    slug = _slug(folder_path)
    return f"{root}/{slug}.zip"


def _slug(value: str) -> str:
    cleaned = _SAFE_TOKEN_RE.sub("-", value).strip(".-_").lower()
    return cleaned[:140].strip("-") or "software-bundle"


def _normalize_relative_path(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        raise ValueError("path must not be empty")
    if raw.startswith(("/", "~")) or re.match(r"^[A-Za-z]:/", raw):
        raise ValueError("path must be relative")
    parts = [part.strip() for part in raw.split("/") if part.strip() and part.strip() != "."]
    if not parts or any(part == ".." for part in parts):
        raise ValueError("path must not contain traversal segments")
    for part in parts:
        if any(ord(ch) < 32 for ch in part):
            raise ValueError("path contains control characters")
        if any(ch in _UNSAFE_PATH_CHARS for ch in part):
            raise ValueError("path contains unsafe segment")
    return "/".join(parts)


def _is_same_or_child(path: str, parent: str) -> bool:
    return path == parent or path.startswith(parent.rstrip("/") + "/")
