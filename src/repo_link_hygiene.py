"""Offline repository link hygiene helpers for release documentation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

_GITHUB_REPO_PATTERN = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)")

REPO_ROLE_BY_SLUG = {
    "pewdiepie-archdaemon/odysseus": "original",
    "fuzzy123-ai/odysseus-fuzzy": "fork",
    "fuzzy123-ai/odysseus-plugin-obsidian": "plugin",
    "filosottile/mkcert": "external_dependency",
}

TYPO_PATTERNS = (
    "odyseus",
    "odysues",
    "odysseuss",
    "odysseus-fuzzie",
)


def _normalize_text(value: Any, *, field_name: str, allow_empty: bool = False) -> str:
    text = " ".join(str(value or "").strip().split())
    if not allow_empty and not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


@dataclass(frozen=True, slots=True)
class RepositoryLinkFinding:
    path: str
    line: int
    slug: str
    role: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "line": self.line,
            "slug": self.slug,
            "role": self.role,
        }


@dataclass(frozen=True, slots=True)
class RepositoryLinkHygieneReport:
    status: str
    findings: tuple[RepositoryLinkFinding, ...]
    unknown_slugs: tuple[str, ...]
    typo_hits: tuple[str, ...]

    @property
    def is_clean(self) -> bool:
        return self.status == "clean"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "findings": [finding.to_dict() for finding in self.findings],
            "unknown_slugs": list(self.unknown_slugs),
            "typo_hits": list(self.typo_hits),
        }


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        text = _normalize_text(value, field_name="repo_link_item", allow_empty=True)
        if text and text not in result:
            result.append(text)
    return tuple(result)


def _normalize_slug(value: str) -> str:
    slug = value.rstrip(".,;:)'\"").lower()
    return slug.removesuffix(".git")


def build_repository_link_hygiene_report(paths: Iterable[str | Path]) -> RepositoryLinkHygieneReport:
    findings: list[RepositoryLinkFinding] = []
    unknown_slugs: list[str] = []
    typo_hits: list[str] = []

    for raw_path in paths:
        path = Path(raw_path)
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        for typo in TYPO_PATTERNS:
            if typo in lowered:
                typo_hits.append(f"{path}:{typo}")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for match in _GITHUB_REPO_PATTERN.finditer(line):
                slug = _normalize_slug(match.group(1))
                role = REPO_ROLE_BY_SLUG.get(slug)
                if role is None:
                    unknown_slugs.append(slug)
                    role = "unknown"
                findings.append(
                    RepositoryLinkFinding(
                        path=str(path).replace("\\", "/"),
                        line=line_number,
                        slug=slug,
                        role=role,
                    )
                )

    status = "clean" if not unknown_slugs and not typo_hits else "blocked"
    return RepositoryLinkHygieneReport(
        status=status,
        findings=tuple(findings),
        unknown_slugs=_dedupe(unknown_slugs),
        typo_hits=_dedupe(typo_hits),
    )
