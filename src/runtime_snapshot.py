"""Small runtime capability snapshot for agent boot context."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from src.agent_sandbox_contract import DEFAULT_SANDBOX_CAPABILITIES


_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")


def build_runtime_snapshot(*, repo_root: Path | str | None = None) -> dict[str, Any]:
    root = Path(repo_root or Path.cwd())
    commit = _read_git_commit(root)
    return {
        "schema": "odysseus.runtime_snapshot.v1",
        "commit": commit[:8] if commit else "unknown",
        "sandbox": tuple(DEFAULT_SANDBOX_CAPABILITIES),
        "telegram": ("text_reply", "photo_artifact_reply"),
        "delegate": "read_only_analysis_only",
        "claim_evidence_gate": "active_post_stream",
        "limits": (
            "no_pre_send_claim_blocking_yet",
            "artifact_integrity_p1_pending",
            "live_actions_still_operator_gated",
        ),
    }


def runtime_snapshot_context_message(*, repo_root: Path | str | None = None) -> dict[str, Any]:
    snapshot = build_runtime_snapshot(repo_root=repo_root)
    content = render_runtime_snapshot(snapshot)
    return {
        "role": "user",
        "content": content,
        "metadata": {"source": "runtime_snapshot", "trusted": True},
        "_protected": True,
    }


def render_runtime_snapshot(snapshot: dict[str, Any]) -> str:
    sandbox = ",".join(str(item) for item in snapshot.get("sandbox") or ())
    telegram = ",".join(str(item) for item in snapshot.get("telegram") or ())
    limits = ",".join(str(item) for item in snapshot.get("limits") or ())
    return (
        "## Odysseus runtime snapshot\n"
        f"- version: {snapshot.get('commit') or 'unknown'}\n"
        f"- sandbox: {sandbox}\n"
        f"- telegram: {telegram}\n"
        f"- delegate: {snapshot.get('delegate') or 'unknown'}\n"
        f"- claim_evidence_gate: {snapshot.get('claim_evidence_gate') or 'unknown'}\n"
        f"- limits: {limits}\n"
        "- local changes/patch notes source: use recent_changes"
    )


def _read_git_commit(root: Path) -> str:
    git_dir = root / ".git"
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if _SHA_RE.fullmatch(head):
        return head.lower()
    if not head.startswith("ref: "):
        return ""
    ref = head.split(" ", 1)[1].strip()
    if not ref or ".." in ref.split("/") or ref.startswith("/"):
        return ""
    try:
        value = (git_dir / ref).read_text(encoding="utf-8").strip()
    except OSError:
        value = _read_packed_ref(git_dir / "packed-refs", ref)
    return value.lower() if _SHA_RE.fullmatch(value or "") else ""


def _read_packed_ref(path: Path, ref: str) -> str:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#") or line.startswith("^"):
                continue
            parts = line.split(" ", 1)
            if len(parts) == 2 and parts[1].strip() == ref:
                return parts[0].strip()
    except OSError:
        return ""
    return ""
