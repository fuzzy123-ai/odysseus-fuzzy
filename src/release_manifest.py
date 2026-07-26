"""Build and validate revision-bound, redacted Odysseus release manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "odysseus.release_manifest.v1"
DEFAULT_MAX_COMMITS = 100
MAX_COMMITS = 200
MAX_PATHS_PER_COMMIT = 100
MAX_MANIFEST_BYTES = 2_000_000
_COMMIT_MARKER = "ODYSSEUS_RELEASE_COMMIT"
_HEX_REVISION = re.compile(r"^[0-9a-f]{40,64}$")
_SAFE_REF = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
_SKIP_PREFIXES = (
    ".agents/",
    ".codex/",
    ".codex-remote-attachments/",
    ".git/",
    ".impeccable/",
    ".pytest-tmp",
    ".tmp/",
    "backups/",
    "data/",
    "logs/",
    "output/",
)
_SKIP_EXACT_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    ".netrc",
    "id_dsa",
    "id_ed25519",
    "id_rsa",
}
_SKIP_SUFFIXES = (
    ".key",
    ".log",
    ".p12",
    ".pem",
    ".pfx",
    ".tmp",
)
_CHANGE_CATEGORIES = {
    "feat": "Features",
    "fix": "Fixes",
    "perf": "Performance",
    "security": "Security",
    "test": "Quality",
    "ci": "Operations",
    "build": "Operations",
    "chore": "Operations",
    "docs": "Documentation",
    "refactor": "Engineering",
}
_CONVENTIONAL_SUBJECT = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]+)\))?!?:\s*(?P<title>.+)$",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(token|password|secret|api[_-]?key)\s*[=:]\s*\S+"
)
_KNOWN_TOKEN = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,})\b"
)


class ReleaseManifestError(RuntimeError):
    """A bounded release-manifest build or validation error."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _run_git(repo: Path, *args: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=20,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReleaseManifestError("git_unavailable") from exc
    if completed.returncode != 0:
        raise ReleaseManifestError("git_query_failed")
    output = completed.stdout or ""
    if len(output.encode("utf-8")) > MAX_MANIFEST_BYTES * 4:
        raise ReleaseManifestError("git_output_too_large")
    return output


def _single_line(value: Any, *, limit: int) -> str:
    text = "".join(
        character if character.isprintable() else " "
        for character in str(value or "")
    )
    return " ".join(text.split())[:limit]


def _safe_ref_name(value: str | None) -> str | None:
    ref = _single_line(value, limit=200)
    if not ref or not _SAFE_REF.fullmatch(ref) or ".." in ref:
        return None
    return ref


def _redacted_subject(value: Any) -> str:
    subject = _single_line(value, limit=240)
    subject = _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}=<redacted>",
        subject,
    )
    return _KNOWN_TOKEN.sub("<redacted-token>", subject)


def is_safe_release_path(value: Any) -> bool:
    path = str(value or "").strip().replace("\\", "/")
    if (
        not path
        or path.startswith("/")
        or ":" in path
        or "\x00" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        return False
    filename = path.rsplit("/", 1)[-1].lower()
    return not (
        any(path.startswith(prefix) for prefix in _SKIP_PREFIXES)
        or filename.startswith(".env")
        or (
            filename.startswith("secrets.env")
            and filename != "secrets.env.example"
        )
        or filename in _SKIP_EXACT_FILENAMES
        or any(filename.endswith(suffix) for suffix in _SKIP_SUFFIXES)
    )


def classify_release_area(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").lower()
    if normalized.startswith("plugins/telegram/") or "telegram" in normalized:
        return "Telegram"
    if (
        normalized.startswith("plugins/obsidian/")
        or "obsidian" in normalized
        or "raptor" in normalized
    ):
        return "Memory/RaptorGraph"
    if (
        normalized.startswith("static/")
        or normalized.startswith("routes/")
        or normalized == "app.py"
    ):
        return "UI/API"
    if normalized.startswith("docs/"):
        return "Docs/Roadmaps"
    if normalized.startswith("tests/"):
        return "Tests"
    if "nextcloud" in normalized:
        return "Nextcloud"
    if "release" in normalized or "roadmap" in normalized:
        return "Release/MVP"
    if "memory" in normalized:
        return "Memory"
    if "secure" in normalized or "provider" in normalized:
        return "Security/Providers"
    return "Core"


def classify_release_subject(subject: str) -> dict[str, str | None]:
    normalized = _redacted_subject(subject)
    match = _CONVENTIONAL_SUBJECT.match(normalized)
    if not match:
        return {
            "category": "Changes",
            "scope": None,
            "title": normalized,
        }
    change_type = match.group("type").lower()
    return {
        "category": _CHANGE_CATEGORIES.get(change_type, "Changes"),
        "scope": _single_line(match.group("scope"), limit=80) or None,
        "title": _single_line(match.group("title"), limit=240),
    }


def _parse_release_log(output: str) -> list[dict[str, Any]]:
    commits: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in output.splitlines():
        if raw_line.startswith(f"{_COMMIT_MARKER}\t"):
            parts = raw_line.split("\t", 3)
            if len(parts) != 4:
                raise ReleaseManifestError("invalid_git_log_record")
            revision = parts[1].strip().lower()
            if not _HEX_REVISION.fullmatch(revision):
                raise ReleaseManifestError("invalid_git_revision")
            redacted_subject = _redacted_subject(parts[3])
            subject = classify_release_subject(redacted_subject)
            current = {
                "revision": revision,
                "short_revision": revision[:12],
                "authored_at": _single_line(parts[2], limit=80),
                "subject": redacted_subject,
                "title": subject["title"],
                "category": subject["category"],
                "scope": subject["scope"],
                "areas": [],
                "paths": [],
                "path_count": 0,
            }
            commits.append(current)
            continue
        if current is None:
            continue
        path = raw_line.strip().replace("\\", "/")
        if not is_safe_release_path(path):
            continue
        current["path_count"] += 1
        if len(current["paths"]) < MAX_PATHS_PER_COMMIT:
            current["paths"].append(path)
    for commit in commits:
        commit["areas"] = sorted(
            {classify_release_area(path) for path in commit["paths"]}
        )
    return commits


def _content_digest(document: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in document.items()
        if key != "content_sha256"
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _parse_timestamp(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ReleaseManifestError("invalid_timestamp")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseManifestError("invalid_timestamp") from exc


def _revisions_match(expected: str, actual: str) -> bool:
    left = str(expected or "").strip().lower()
    right = str(actual or "").strip().lower()
    return bool(left and right and (left.startswith(right) or right.startswith(left)))


def validate_release_manifest(
    document: Any,
    *,
    expected_revision: str | None = None,
) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ReleaseManifestError("invalid_document")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ReleaseManifestError("unsupported_schema")
    revision = str(document.get("revision") or "").strip().lower()
    if not _HEX_REVISION.fullmatch(revision):
        raise ReleaseManifestError("invalid_revision")
    if expected_revision and not _revisions_match(expected_revision, revision):
        raise ReleaseManifestError("revision_mismatch")
    _parse_timestamp(document.get("generated_at"))
    if document.get("short_revision") != revision[:12]:
        raise ReleaseManifestError("invalid_short_revision")
    ref = document.get("ref")
    if ref is not None and _safe_ref_name(str(ref)) != ref:
        raise ReleaseManifestError("invalid_ref")
    commits = document.get("commits")
    if not isinstance(commits, list) or len(commits) > MAX_COMMITS:
        raise ReleaseManifestError("invalid_commits")
    if not commits:
        raise ReleaseManifestError("empty_commits")
    for commit in commits:
        if not isinstance(commit, dict):
            raise ReleaseManifestError("invalid_commit")
        commit_revision = str(commit.get("revision") or "").strip().lower()
        if not _HEX_REVISION.fullmatch(commit_revision):
            raise ReleaseManifestError("invalid_commit_revision")
        _parse_timestamp(commit.get("authored_at"))
        if not str(commit.get("subject") or "").strip():
            raise ReleaseManifestError("invalid_commit_subject")
        paths = commit.get("paths")
        if not isinstance(paths, list) or len(paths) > MAX_PATHS_PER_COMMIT:
            raise ReleaseManifestError("invalid_commit_paths")
        if any(not is_safe_release_path(path) for path in paths):
            raise ReleaseManifestError("unsafe_commit_path")
    if commits[0].get("revision") != revision:
        raise ReleaseManifestError("head_commit_mismatch")
    if commits[0].get("authored_at") != document.get("generated_at"):
        raise ReleaseManifestError("head_timestamp_mismatch")
    coverage = document.get("coverage")
    if not isinstance(coverage, dict):
        raise ReleaseManifestError("invalid_coverage")
    if coverage.get("commit_count") != len(commits):
        raise ReleaseManifestError("invalid_coverage_count")
    expected_digest = _content_digest(document)
    if document.get("content_sha256") != expected_digest:
        raise ReleaseManifestError("content_digest_mismatch")
    return document


def build_release_manifest(
    *,
    repo_root: str | os.PathLike[str],
    revision: str = "HEAD",
    ref: str | None = None,
    max_commits: int = DEFAULT_MAX_COMMITS,
) -> dict[str, Any]:
    repo = Path(repo_root).resolve()
    limit = max(1, min(int(max_commits), MAX_COMMITS))
    resolved_revision = _run_git(
        repo,
        "rev-parse",
        "--verify",
        f"{revision}^{{commit}}",
    ).strip().lower()
    if not _HEX_REVISION.fullmatch(resolved_revision):
        raise ReleaseManifestError("invalid_revision")
    log = _run_git(
        repo,
        "log",
        resolved_revision,
        f"--max-count={limit}",
        "--first-parent",
        "--diff-merges=first-parent",
        "--date=iso-strict",
        f"--pretty=format:{_COMMIT_MARKER}%x09%H%x09%cI%x09%s",
        "--name-only",
        "--no-renames",
    )
    commits = _parse_release_log(log)
    if not commits or commits[0]["revision"] != resolved_revision:
        raise ReleaseManifestError("head_commit_missing")
    shallow = _run_git(
        repo,
        "rev-parse",
        "--is-shallow-repository",
    ).strip().lower() == "true"
    all_areas = sorted(
        {
            area
            for commit in commits
            for area in commit.get("areas") or []
        }
    )
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "revision": resolved_revision,
        "short_revision": resolved_revision[:12],
        "ref": _safe_ref_name(ref),
        "generated_at": commits[0]["authored_at"],
        "areas": all_areas,
        "commits": commits,
        "coverage": {
            "history_mode": "first_parent",
            "max_commits": limit,
            "commit_count": len(commits),
            "history_truncated": shallow or len(commits) == limit,
            "newest_at": commits[0]["authored_at"],
            "oldest_at": commits[-1]["authored_at"],
        },
    }
    document["content_sha256"] = _content_digest(document)
    return validate_release_manifest(
        document,
        expected_revision=resolved_revision,
    )


def write_release_manifest(
    document: dict[str, Any],
    output_path: str | os.PathLike[str],
) -> Path:
    validated = validate_release_manifest(document)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.",
        dir=output.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(
            descriptor,
            "w",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            json.dump(
                validated,
                handle,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def read_release_manifest(
    manifest_path: str | os.PathLike[str],
    *,
    expected_revision: str | None = None,
) -> tuple[dict[str, Any] | None, str]:
    path = Path(manifest_path)
    try:
        if not path.is_file():
            return None, "missing"
        if path.stat().st_size > MAX_MANIFEST_BYTES:
            return None, "too_large"
        document = json.loads(path.read_text(encoding="utf-8"))
        validated = validate_release_manifest(
            document,
            expected_revision=expected_revision,
        )
    except ReleaseManifestError as exc:
        return None, exc.code
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, "unreadable"
    return validated, "ready"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision", default="HEAD")
    parser.add_argument("--ref")
    parser.add_argument(
        "--max-commits",
        type=int,
        default=DEFAULT_MAX_COMMITS,
    )
    args = parser.parse_args(argv)
    document = build_release_manifest(
        repo_root=args.repo,
        revision=args.revision,
        ref=args.ref,
        max_commits=args.max_commits,
    )
    output = write_release_manifest(document, args.output)
    print(
        json.dumps(
            {
                "status": "written",
                "revision": document["revision"],
                "commit_count": len(document["commits"]),
                "output": output.as_posix(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
