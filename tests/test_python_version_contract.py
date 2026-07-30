from __future__ import annotations

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
PYTHON_BASELINE = "3.11"


def test_python_311_baseline_contract_is_consistent():
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == PYTHON_BASELINE

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    docker_bases = re.findall(r"^FROM python:([^\s]+)", dockerfile, flags=re.MULTILINE)
    assert docker_bases == [f"{PYTHON_BASELINE}-slim", f"{PYTHON_BASELINE}-slim"]

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Supported Python baseline: 3.11." in readme
    assert not re.search(r"Python 3\.11\+", readme)

    setup_python_steps = []
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        contents = workflow.read_text(encoding="utf-8")
        for step in re.split(r"^\s*-\s+", contents, flags=re.MULTILINE):
            if re.search(r"^\s*uses:\s*actions/setup-python@", step, flags=re.MULTILINE):
                setup_python_steps.append((workflow, step))

    assert setup_python_steps, "expected at least one actions/setup-python workflow step"
    for workflow, step in setup_python_steps:
        assert re.search(
            rf"^\s*python-version:\s*['\"]?{re.escape(PYTHON_BASELINE)}['\"]?\s*(?:#.*)?$",
            step,
            flags=re.MULTILINE,
        ), f"{workflow.relative_to(ROOT)} must pin actions/setup-python to {PYTHON_BASELINE}"
