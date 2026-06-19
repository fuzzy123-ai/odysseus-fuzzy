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
    latest_commit = _remote_head(remote, ref)

    update_available = None
    status = "unknown"
    if commit and latest_commit:
        update_available = commit != latest_commit
        status = "outdated" if update_available else "current"

    payload = {
        "version": APP_VERSION,
        "commit": short_commit,
        "branch": branch,
        "remote": remote,
        "remote_ref": ref,
        "latest_commit": latest_commit[:12] if latest_commit else None,
        "update_available": update_available,
        "status": status,
    }
    _CACHE["payload"] = payload
    _CACHE["expires_at"] = now + _CACHE_TTL_SECONDS
    return dict(payload)
