import pytest

from src.github_issue_fields import (
    GitHubIssueFieldError,
    IssueFieldDefinition,
    build_issue_field_projection,
    projection_to_write_report,
    validate_issue_fields,
)


def test_default_issue_fields_validate_and_normalize():
    fields = validate_issue_fields(
        {
            "Type": "Bug",
            "priority": "HIGH",
            "effort": "medium",
            "area": "Cookbook Runtime",
            "status": "triage",
            "target-date": "2026-07-10",
            "duplicate_of": "#123",
        }
    )

    assert fields == {
        "type": "bug",
        "priority": "high",
        "effort": "medium",
        "area": "cookbook-runtime",
        "status": "triage",
        "target_date": "2026-07-10",
        "duplicate_of": "#123",
    }


def test_unknown_fields_fail_closed_unless_configured():
    with pytest.raises(GitHubIssueFieldError, match="unknown issue field"):
        validate_issue_fields({"customer_impact": "high"})

    configured = {
        "customer_impact": IssueFieldDefinition(
            name="customer_impact",
            field_type="single_select",
            allowed_values=("high", "medium", "low"),
            github_field_name="Customer impact",
            label_prefix="impact/",
        )
    }

    assert validate_issue_fields({"customer impact": "High"}, definitions=configured) == {
        "customer_impact": "high"
    }


def test_invalid_values_dates_issue_refs_and_secret_markers_are_rejected():
    bad_payloads = [
        {"priority": "critical"},
        {"target_date": "07/10/2026"},
        {"duplicate_of": "https://github.com/org/repo/issues/1"},
        {"area": "Authorization: Bearer github_pat_secret"},
    ]

    for payload in bad_payloads:
        with pytest.raises(GitHubIssueFieldError):
            validate_issue_fields(payload)


def test_projection_prefers_github_issue_fields_when_ids_are_available():
    projections = build_issue_field_projection(
        {"priority": "high", "area": "memory", "target_date": "2026-07-10"},
        github_field_ids={
            "priority": "PVTSSF_priority",
            "target_date": "PVTSSF_target",
        },
    )

    assert [projection.to_dict() for projection in projections] == [
        {
            "field": "area",
            "value": "memory",
            "method": "label",
            "target": "area/memory",
        },
        {
            "field": "priority",
            "value": "high",
            "method": "github_field",
            "target": "PVTSSF_priority",
        },
        {
            "field": "target_date",
            "value": "2026-07-10",
            "method": "github_field",
            "target": "PVTSSF_target",
        },
    ]


def test_projection_uses_label_fallback_or_local_only_deterministically():
    projections = build_issue_field_projection(
        {"priority": "low", "target_date": "2026-07-10"},
        github_field_ids={},
    )

    assert [projection.to_dict() for projection in projections] == [
        {
            "field": "priority",
            "value": "low",
            "method": "label",
            "target": "priority/low",
        },
        {
            "field": "target_date",
            "value": "2026-07-10",
            "method": "local_only",
            "target": "Target date",
        },
    ]


def test_projection_can_disable_label_fallback_for_preview_only_mode():
    projections = build_issue_field_projection(
        {"priority": "urgent", "area": "MCP Debugging"},
        allow_label_fallback=False,
    )

    assert [projection.to_dict() for projection in projections] == [
        {
            "field": "area",
            "value": "mcp-debugging",
            "method": "local_only",
            "target": "Area",
        },
        {
            "field": "priority",
            "value": "urgent",
            "method": "local_only",
            "target": "Priority",
        },
    ]


def test_projection_write_report_is_redacted_and_structured():
    projections = build_issue_field_projection({"type": "feature", "priority": "medium"})

    assert projection_to_write_report(projections) == (
        {
            "field": "priority",
            "method": "label",
            "status": "planned",
            "target": "priority/medium",
            "error_redacted": "",
        },
        {
            "field": "type",
            "method": "label",
            "status": "planned",
            "target": "type/feature",
            "error_redacted": "",
        },
    )
