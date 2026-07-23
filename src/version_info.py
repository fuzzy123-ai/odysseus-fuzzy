import os
import subprocess
import time
from typing import Any

from core.constants import APP_VERSION, BASE_DIR

_CACHE_TTL_SECONDS = 300
_CACHE: dict[str, Any] = {"expires_at": 0.0, "payload": None}


def _git(args: list[str], timeout: float = 2.0) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", BASE_DIR, *args],
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _upstream_ref(branch: str | None) -> str | None:
    if not branch:
        return None
    return _git(["config", "--get", f"branch.{branch}.merge"]) or f"refs/heads/{branch}"


def _remote_head(remote: str | None, ref: str | None) -> str | None:
    if not remote or not ref:
        return None
    output = _git(["ls-remote", remote, ref], timeout=3.0)
    if not output:
        return None
    return output.split()[0]


def _git_is_ancestor(ancestor: str, descendant: str, timeout: float = 2.0) -> bool | None:
    """Return whether ``ancestor`` is reachable from ``descendant``.

    ``None`` means Git could not determine the relation, for example because a
    remote commit reported by ``ls-remote`` has not been fetched locally yet.
    """

    try:
        result = subprocess.run(
            ["git", "-C", BASE_DIR, "merge-base", "--is-ancestor", ancestor, descendant],
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
    except Exception:
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None


def _classify_version_relation(
    commit: str | None,
    latest_commit: str | None,
    relation_hint: str | None = None,
) -> tuple[bool | None, str, str]:
    """Return ``(update_available, status, relation)`` for two revisions."""

    if not commit or not latest_commit:
        return None, "unknown", "unknown"
    if commit == latest_commit:
        return False, "current", "same"

    latest_is_ancestor = _git_is_ancestor(latest_commit, commit)
    if latest_is_ancestor is True:
        return False, "current", "ahead"

    commit_is_ancestor = _git_is_ancestor(commit, latest_commit)
    if commit_is_ancestor is True:
        return True, "outdated", "behind"
    if latest_is_ancestor is False and commit_is_ancestor is False:
        return True, "diverged", "diverged"

    hinted = str(relation_hint or "").strip().lower()
    if hinted == "ahead":
        return False, "current", "ahead"
    if hinted == "behind":
        return True, "outdated", "behind"
    if hinted == "diverged":
        return True, "diverged", "diverged"

    # Preserve the conservative legacy signal when Git cannot inspect a remote
    # object locally. The explicit relation keeps the uncertainty visible.
    return True, "outdated", "unknown"


def get_version_info(force_refresh: bool = False) -> dict[str, Any]:
    now = time.monotonic()
    if not force_refresh and _CACHE["payload"] and now < _CACHE["expires_at"]:
        return dict(_CACHE["payload"])

    commit = _git(["rev-parse", "HEAD"]) or os.getenv("ODYSSEUS_GIT_COMMIT") or None
    short_commit = _git(["rev-parse", "--short", "HEAD"]) or os.getenv("ODYSSEUS_GIT_SHORT_COMMIT") or (commit[:8] if commit else None)
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"]) or os.getenv("ODYSSEUS_GIT_BRANCH") or None
    remote = _git(["config", "--get", f"branch.{branch}.remote"]) if branch else None
    remote = remote or os.getenv("ODYSSEUS_GIT_REMOTE_URL") or "origin"
    ref = _upstream_ref(branch) or os.getenv("ODYSSEUS_GIT_REMOTE_REF")
    configured_latest_commit = os.getenv("ODYSSEUS_GIT_LATEST_COMMIT") or None
    latest_commit = _remote_head(remote, ref) or configured_latest_commit
    relation_hint = None
    if configured_latest_commit and latest_commit == configured_latest_commit:
        relation_hint = os.getenv("ODYSSEUS_GIT_RELATION") or None

    update_available, status, relation = _classify_version_relation(
        commit,
        latest_commit,
        relation_hint,
    )

    payload = {
        "version": APP_VERSION,
        "commit": short_commit,
        "branch": branch,
        "remote": remote,
        "remote_ref": ref,
        "latest_commit": latest_commit[:12] if latest_commit else None,
        "update_available": update_available,
        "status": status,
        "relation": relation,
    }
    _CACHE["payload"] = payload
    _CACHE["expires_at"] = now + _CACHE_TTL_SECONDS
    return dict(payload)
