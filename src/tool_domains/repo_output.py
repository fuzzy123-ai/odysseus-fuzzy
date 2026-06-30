"""Output formatting helpers for repo management tools."""

from typing import Any, Dict


def repo_commit_output(report: Dict[str, Any]) -> str:
    plan = report.get("plan") or {}
    lines = [
        f"Repo commit {report.get('status')} for `{plan.get('repo_id')}`.",
        f"Decision: {plan.get('decision')}.",
    ]
    blockers = report.get("blockers") or plan.get("blockers") or []
    if blockers:
        lines.append("Warum blockiert:")
        lines.extend(f"- {item}" for item in blockers)
    else:
        lines.append(f"Committed paths: {', '.join(report.get('committed_paths') or plan.get('changed_paths') or [])}")
    next_decision = plan.get("next_human_decision")
    if next_decision:
        lines.append(f"Next: {next_decision}")
    return "\n".join(lines)


def repo_push_output(report: Dict[str, Any]) -> str:
    plan = report.get("plan") or {}
    lines = [
        f"Repo push {report.get('status')} for `{plan.get('repo_id')}`.",
        f"Decision: {plan.get('decision')}.",
        f"Target: {plan.get('remote_name')}/{plan.get('branch_name')} @ {plan.get('commit_sha')}.",
    ]
    blockers = report.get("blockers") or plan.get("blockers") or []
    if blockers:
        lines.append("Warum blockiert:")
        lines.extend(f"- {item}" for item in blockers)
    else:
        lines.append(f"Pushed ref: {report.get('pushed_ref') or 'not executed'}")
    next_decision = plan.get("next_human_decision")
    if next_decision:
        lines.append(f"Next: {next_decision}")
    return "\n".join(lines)


def repo_forge_output(report: Dict[str, Any]) -> str:
    plan = report.get("plan") or {}
    lines = [
        f"Repo forge {report.get('status')} for `{plan.get('repo_id')}`.",
        f"Decision: {plan.get('decision')}.",
        f"Provider: {plan.get('provider')}/{plan.get('namespace')}/{plan.get('repo_name')}.",
    ]
    blockers = report.get("blockers") or plan.get("blockers") or []
    if blockers:
        lines.append("Warum blockiert:")
        lines.extend(f"- {item}" for item in blockers)
    else:
        metadata = report.get("metadata") or {}
        if metadata:
            lines.append(
                "Metadata: default_branch={branch}, issues={issues}, prs={prs}, permissions={permissions}".format(
                    branch=metadata.get("default_branch"),
                    issues=metadata.get("issue_count"),
                    prs=metadata.get("pull_request_count"),
                    permissions=", ".join(metadata.get("permissions") or []),
                )
            )
        else:
            lines.append("Metadata fetch is planned but not executed.")
    next_decision = plan.get("next_human_decision")
    if next_decision:
        lines.append(f"Next: {next_decision}")
    return "\n".join(lines)


def repo_changes_output(report: Dict[str, Any]) -> str:
    snapshot = report.get("snapshot") or {}
    context = report.get("project_context") or {}
    lines = [
        f"Repo changes collected for `{snapshot.get('repo_id')}`.",
        f"Snapshot: `{snapshot.get('id')}`.",
        f"Persisted: {bool(report.get('persisted'))}.",
    ]
    duplicate_of = report.get("duplicate_of")
    if duplicate_of:
        lines.append(f"Duplicate of: `{duplicate_of}`.")
    for item in context.get("context_lines") or []:
        lines.append(f"- {item}")
    lines.append("Memory/RaptorGraph: prepared redacted project-context event; raw diffs are not included.")
    return "\n".join(lines)


def repo_status_output(status: Dict[str, Any]) -> str:
    lines = [f"Status for `{status.get('repo_id')}`:", status.get("branch_line") or "branch unknown"]
    entries = status.get("entries") or []
    if entries:
        lines.extend(f"- {entry}" for entry in entries)
    else:
        lines.append("- clean")
    return "\n".join(lines)
