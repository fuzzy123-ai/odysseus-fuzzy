"""Ephemeral Worktree Manager for Harbour Worker Gateway.

Provides path-isolated, ephemeral git worktrees for concurrent worker execution,
ensuring strict decoupling from the main repository workspace and main process.
"""

from __future__ import annotations

from pathlib import Path
import re
import shutil
import subprocess
from typing import Any

from src.harbour.gateway.coordinator import (
    HarbourWorkerGatewayError,
    WorkerGatewayIsolationError,
)

_SAFE_IDENTIFIER_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


class EphemeralWorktreeManager:
    """Manages ephemeral git worktrees for isolated worker tasks."""

    def __init__(
        self,
        repo_root: str | Path | None = None,
        worktree_base_dir: str | Path | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve() if repo_root is not None else Path.cwd().resolve()
        if worktree_base_dir is not None:
            wb = Path(worktree_base_dir)
            self.worktree_base_dir = wb.resolve() if wb.is_absolute() else (self.repo_root / wb).resolve()
        else:
            self.worktree_base_dir = (self.repo_root / ".worktrees").resolve()

    def _sanitize_identifier(self, value: str, label: str) -> str:
        """Sanitize an identifier to alphanumeric, hyphen, and underscore characters."""
        if not isinstance(value, str):
            raise HarbourWorkerGatewayError(f"Invalid {label}: must be a string")
        cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", value.strip()).strip("_-")
        if not cleaned:
            raise HarbourWorkerGatewayError(
                f"Invalid {label}: must contain at least one alphanumeric character after sanitization"
            )
        return cleaned

    def validate_isolation(self, target_path: str | Path) -> Path:
        """Enforces that a target path never points to the main repository workspace or .git."""
        target = Path(target_path).resolve()
        base = self.repo_root.resolve()

        if target == base:
            raise WorkerGatewayIsolationError(
                "main_process_execution_forbidden: worktree_path cannot be the main repository workspace"
            )

        git_dir = (base / ".git").resolve()
        if target == git_dir or git_dir in target.parents:
            raise WorkerGatewayIsolationError(
                "main_process_execution_forbidden: worktree_path cannot be inside .git directory"
            )

        if target == target.parent:
            raise WorkerGatewayIsolationError("worktree target cannot be the filesystem root")

        return target

    def create_worktree(
        self,
        worker_id: str,
        task_id: str,
        base_ref: str = "HEAD",
    ) -> Path:
        """Create an ephemeral isolated git worktree with dedicated branch."""
        clean_worker_id = self._sanitize_identifier(worker_id, "worker_id")
        clean_task_id = self._sanitize_identifier(task_id, "task_id")

        target_path = (self.worktree_base_dir / f"worker_{clean_worker_id}_{clean_task_id}").resolve()
        self.validate_isolation(target_path)

        branch_name = f"worker/{clean_worker_id}/{clean_task_id}"

        # Ensure base directory exists
        self.worktree_base_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            "git",
            "worktree",
            "add",
            "-b",
            branch_name,
            str(target_path),
            str(base_ref),
        ]

        try:
            proc = subprocess.run(
                cmd,
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception as exc:
            raise HarbourWorkerGatewayError(
                f"Failed to execute git worktree creation command: {type(exc).__name__}"
            ) from exc

        if proc.returncode != 0:
            err_line = proc.stderr.strip().splitlines()[-1] if proc.stderr else f"exit code {proc.returncode}"
            # Check for isolation errors in error output
            if "already exists" in err_line.lower():
                raise HarbourWorkerGatewayError(
                    f"git worktree add failed: destination path or branch already exists for task '{clean_task_id}'"
                )
            raise HarbourWorkerGatewayError(f"git worktree add failed: {err_line}")

        return target_path

    def teardown_worktree(
        self,
        worktree_path: str | Path,
        delete_branch: bool = True,
    ) -> bool:
        """Teardown an ephemeral isolated git worktree and prune associated branch."""
        target = self.validate_isolation(worktree_path)

        # Attempt to determine associated branch prior to removal
        branch_name: str | None = None
        if delete_branch:
            for wt in self.list_active_worktrees():
                if Path(wt["worktree"]).resolve() == target:
                    branch_name = wt.get("branch")
                    break

            if not branch_name and target.name.startswith("worker_"):
                # Fallback to standard naming convention worker_{worker_id}_{task_id}
                parts = target.name.split("_", 2)
                if len(parts) == 3:
                    branch_name = f"worker/{parts[1]}/{parts[2]}"

        # Remove git worktree
        try:
            proc_rm = subprocess.run(
                ["git", "worktree", "remove", "--force", str(target)],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception as exc:
            raise HarbourWorkerGatewayError(
                f"Failed to remove git worktree: {type(exc).__name__}"
            ) from exc

        if proc_rm.returncode != 0 and target.exists():
            # If git worktree remove failed but directory remains, clean manually
            try:
                shutil.rmtree(target, ignore_errors=True)
            except Exception:
                pass

        # Delete branch if requested
        if delete_branch and branch_name:
            subprocess.run(
                ["git", "branch", "-D", branch_name],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )

        # Prune worktree metadata
        subprocess.run(
            ["git", "worktree", "prune"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )

        return True

    def list_active_worktrees(self) -> list[dict[str, Any]]:
        """List active git worktrees by parsing porcelain output."""
        try:
            proc = subprocess.run(
                ["git", "worktree", "list", "--porcelain"],
                cwd=self.repo_root,
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception as exc:
            raise HarbourWorkerGatewayError(
                f"Failed to list git worktrees: {type(exc).__name__}"
            ) from exc

        if proc.returncode != 0:
            return []

        worktrees: list[dict[str, Any]] = []
        current: dict[str, Any] = {}

        for raw_line in proc.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                if current.get("worktree"):
                    worktrees.append(current)
                    current = {}
                continue

            if line.startswith("worktree "):
                if current.get("worktree"):
                    worktrees.append(current)
                    current = {}
                wt_path = line[len("worktree "):].strip()
                resolved = str(Path(wt_path).resolve())
                current["worktree"] = resolved
                current["path"] = resolved
                current["head"] = ""
                current["branch"] = None
                current["detached"] = False
            elif line.startswith("HEAD "):
                current["head"] = line[len("HEAD "):].strip()
            elif line.startswith("branch "):
                ref = line[len("branch "):].strip()
                current["ref"] = ref
                current["branch"] = ref.removeprefix("refs/heads/")
            elif line == "detached":
                current["detached"] = True
                current["branch"] = None
            elif line == "bare":
                current["bare"] = True
            elif line == "locked":
                current["locked"] = True
            elif line == "prunable":
                current["prunable"] = True

        if current.get("worktree"):
            worktrees.append(current)

        return worktrees


__all__ = ["EphemeralWorktreeManager"]
