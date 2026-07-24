from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = ROOT / ".github" / "workflows"
CI_PATH = WORKFLOW_DIR / "ci.yml"
QUALITY_GATE_PATH = WORKFLOW_DIR / "quality-gate.yml"
DOCKER_PUBLISH_PATH = WORKFLOW_DIR / "docker-publish.yml"
QUARANTINE_PATH = ROOT / "tests" / "ci_quarantine.json"

REQUIRED_GATE_JOBS = {"python-syntax", "node-syntax", "python-tests"}
REUSABLE_GATE = "./.github/workflows/quality-gate.yml"
QUARANTINE_FIELDS = {
    "nodeid",
    "owner",
    "reason",
    "issue",
    "created_at",
    "expires_at",
}


def _load_workflow(path: Path) -> dict[str, Any]:
    # BaseLoader follows GitHub's treatment of the key `on`; SafeLoader uses
    # YAML 1.1 and incorrectly converts it to boolean True.
    value = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(value, dict), f"{path.name} must contain a YAML mapping"
    return value


def _needs(job: dict[str, Any]) -> set[str]:
    value = job.get("needs", [])
    if isinstance(value, str):
        return {value}
    return set(value)


def _job_ancestors(jobs: dict[str, Any], job_id: str) -> set[str]:
    found: set[str] = set()
    pending = list(_needs(jobs[job_id]))
    while pending:
        dependency = pending.pop()
        assert dependency in jobs, f"{job_id} needs unknown job {dependency}"
        if dependency in found:
            continue
        found.add(dependency)
        pending.extend(_needs(jobs[dependency]))
    return found


def _all_run_commands(workflow: dict[str, Any]) -> list[str]:
    return [
        step["run"]
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "run" in step
    ]


def test_ci_runs_the_reusable_gate_for_pull_requests_dev_and_main() -> None:
    workflow = _load_workflow(CI_PATH)
    triggers = workflow["on"]

    assert "pull_request" in triggers
    assert set(triggers["push"]["branches"]) == {"dev", "main"}
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == {"quality-gate"}
    assert workflow["jobs"]["quality-gate"]["uses"] == REUSABLE_GATE
    assert "workflow_run" not in CI_PATH.read_text(encoding="utf-8")


def test_reusable_gate_has_blocking_syntax_and_full_regression_jobs() -> None:
    workflow = _load_workflow(QUALITY_GATE_PATH)

    assert set(workflow["on"]) == {"workflow_call"}
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["jobs"]) == REQUIRED_GATE_JOBS

    serialized = json.dumps(workflow)
    assert "continue-on-error" not in serialized

    for job in workflow["jobs"].values():
        checkout = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@"))
        assert checkout["with"]["ref"] == "${{ github.sha }}"
        assert checkout["with"]["persist-credentials"] == "false"

    commands = _all_run_commands(workflow)
    assert any(command.startswith("python -m compileall -q ") for command in commands)
    assert any("node --check" in command for command in commands)
    assert "python -m pytest -q --maxfail=1" in commands
    assert not any(
        forbidden in command
        for command in commands
        for forbidden in ("--ignore", "--deselect", "--lf", "--failed-first", "|| true")
    )


def test_docker_publication_has_a_same_run_exact_commit_gate() -> None:
    workflow = _load_workflow(DOCKER_PUBLISH_PATH)
    source = DOCKER_PUBLISH_PATH.read_text(encoding="utf-8")
    jobs = workflow["jobs"]

    assert set(workflow["on"]["push"]["branches"]) == {"dev", "main"}
    assert "workflow_run" not in source
    assert jobs["quality-gate"]["uses"] == REUSABLE_GATE
    assert jobs["quality-gate"]["permissions"] == {"contents": "read"}
    assert _needs(jobs["build"]) == {"quality-gate"}
    assert _needs(jobs["merge"]) == {"quality-gate", "build"}

    publishing_jobs = {
        job_id
        for job_id, job in jobs.items()
        if job.get("permissions", {}).get("packages") == "write"
        or "push=true" in json.dumps(job)
        or "imagetools create" in json.dumps(job)
    }
    assert publishing_jobs == {"build", "merge"}
    for job_id in publishing_jobs:
        assert "quality-gate" in _job_ancestors(jobs, job_id)
        assert "always()" not in json.dumps(jobs[job_id])
        assert "failure()" not in json.dumps(jobs[job_id])


def test_required_workflows_have_no_advisory_escape_hatch() -> None:
    for path in (CI_PATH, QUALITY_GATE_PATH, DOCKER_PUBLISH_PATH):
        workflow = _load_workflow(path)
        serialized = json.dumps(workflow)
        assert "continue-on-error" not in serialized, path.name


def test_quarantine_entries_are_traceable_visible_and_expire_within_30_days() -> None:
    document = json.loads(QUARANTINE_PATH.read_text(encoding="utf-8"))
    policy = document["policy"]
    entries = document["entries"]

    assert document["schema_version"] == 1
    assert policy["maximum_lifetime_days"] == 30
    assert set(policy["required_entry_fields"]) == QUARANTINE_FIELDS
    assert isinstance(entries, list)

    today = date.today()
    nodeids: set[str] = set()
    for entry in entries:
        assert set(entry) == QUARANTINE_FIELDS
        assert all(isinstance(entry[field], str) and entry[field].strip() for field in QUARANTINE_FIELDS)
        assert entry["nodeid"].startswith("tests/")
        assert not any(character in entry["nodeid"] for character in "*?[]")
        assert entry["nodeid"] not in nodeids
        nodeids.add(entry["nodeid"])

        created_at = date.fromisoformat(entry["created_at"])
        expires_at = date.fromisoformat(entry["expires_at"])
        assert created_at <= today < expires_at
        assert expires_at <= created_at + timedelta(days=policy["maximum_lifetime_days"])

    # The required workflow always invokes the complete suite. Quarantine is
    # bounded ownership metadata, never a hidden pytest exclusion list.
    gate_source = QUALITY_GATE_PATH.read_text(encoding="utf-8")
    pytest_command = next(command for command in _all_run_commands(_load_workflow(QUALITY_GATE_PATH)) if "pytest" in command)
    assert pytest_command == "python -m pytest -q --maxfail=1"
    pytest_args = pytest_command.split()[3:]
    assert not any(flag in pytest_args for flag in ("--ignore", "--deselect", "-k", "-m"))
