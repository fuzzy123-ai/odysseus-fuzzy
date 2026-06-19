from src.repo_link_hygiene import (
    REPO_ROLE_BY_SLUG,
    build_repository_link_hygiene_report,
)


DOC_PATHS = (
    "README.md",
    "CONTRIBUTING.md",
    "package.json",
    "docs/setup.md",
    "docs/index.html",
    "docs/plans/abc-prioritized-execution-roadmap.md",
    "docs/plans/origin-publish-hygiene.md",
)


def test_release_docs_only_use_known_repository_slugs():
    report = build_repository_link_hygiene_report(DOC_PATHS)

    assert report.status == "clean"
    assert report.unknown_slugs == ()
    assert report.typo_hits == ()
    assert {finding.role for finding in report.findings}.issubset(
        {"original", "fork", "plugin", "external_dependency"}
    )


def test_original_and_fork_roles_are_explicitly_distinct():
    assert REPO_ROLE_BY_SLUG["pewdiepie-archdaemon/odysseus"] == "original"
    assert REPO_ROLE_BY_SLUG["fuzzy123-ai/odysseus-fuzzy"] == "fork"

    report = build_repository_link_hygiene_report(DOC_PATHS)
    role_by_slug = {finding.slug: finding.role for finding in report.findings}

    assert role_by_slug["pewdiepie-archdaemon/odysseus"] == "original"
    assert role_by_slug["fuzzy123-ai/odysseus-fuzzy"] == "fork"


def test_unknown_repo_slug_blocks_report(tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text("clone https://github.com/example/odysseus-shadow.git\n", encoding="utf-8")

    report = build_repository_link_hygiene_report((doc,))

    assert report.status == "blocked"
    assert report.unknown_slugs == ("example/odysseus-shadow",)


def test_known_typo_variant_blocks_report(tmp_path):
    doc = tmp_path / "doc.md"
    doc.write_text("clone https://github.com/example/odyseus.git\n", encoding="utf-8")

    report = build_repository_link_hygiene_report((doc,))

    assert report.status == "blocked"
    assert any("odyseus" in hit for hit in report.typo_hits)
