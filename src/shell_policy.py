"""Shell command risk classification for UX and audit surfaces.

This module is deliberately not a sandbox. It gives the UI and ledgers a stable
policy vocabulary while real isolation remains the job of the execution layer.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import shlex


SAFE_COMMANDS = {
    "cat",
    "echo",
    "find",
    "git",
    "grep",
    "head",
    "ls",
    "node",
    "npm",
    "pwd",
    "python",
    "python3",
    "pytest",
    "rg",
    "tail",
    "wc",
    "which",
}

BLOCKED_COMMANDS = {
    "chroot",
    "insmod",
    "modprobe",
    "mount",
    "nc",
    "netcat",
    "ssh",
    "telnet",
    "umount",
}

CAUTION_COMMANDS = {
    "cargo",
    "cmake",
    "curl",
    "docker",
    "git",
    "go",
    "make",
    "npm",
    "pip",
    "pip3",
    "python3",
    "wget",
}

DANGER_RE = re.compile(
    r"(^|\s)(rm\s+(-[A-Za-z]*r[A-Za-z]*f|-rf|-fr)\b|dd\s+|mkfs(\.\w+)?\b|"
    r"chmod\s+777\b|chown\s+|sudo\b|kill(all)?\b|shutdown\b|reboot\b|"
    r":\s*\(\s*\)\s*\{|\s>\s*/dev/(sd|hd|nvme|mapper/))",
    re.IGNORECASE,
)
PIPE_TO_SHELL_RE = re.compile(
    r"\b(base64\s+(-d|--decode)|curl\b|wget\b).*(\||&&).*\b(sh|bash|zsh|powershell)\b",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class ShellPolicyDecision:
    tier: str
    reason: str
    command: str
    leading_command: str | None
    requires_confirmation: bool
    blocked: bool
    audit: bool

    def to_dict(self) -> dict:
        return asdict(self)


def classify_shell_command(command: str | None) -> ShellPolicyDecision:
    """Classify a shell command into safe/caution/danger/blocked tiers."""

    raw = str(command or "")
    normalized = raw.strip()
    leading = _leading_command(normalized)
    tier = "caution"
    reason = "unknown_command"

    if not normalized:
        tier = "blocked"
        reason = "empty_command"
    elif leading in BLOCKED_COMMANDS:
        tier = "blocked"
        reason = "blocked_command"
    elif DANGER_RE.search(normalized) or PIPE_TO_SHELL_RE.search(normalized):
        tier = "danger"
        reason = "dangerous_pattern"
    elif _is_safe_command(leading, normalized):
        tier = "safe"
        reason = "known_safe_command"
    elif leading in CAUTION_COMMANDS:
        tier = "caution"
        reason = "known_caution_command"

    return ShellPolicyDecision(
        tier=tier,
        reason=reason,
        command=normalized,
        leading_command=leading,
        requires_confirmation=tier == "danger",
        blocked=tier == "blocked",
        audit=tier in {"caution", "danger", "blocked"},
    )


def _leading_command(command: str) -> str | None:
    if not command:
        return None
    try:
        parts = shlex.split(command, posix=True)
    except ValueError:
        return None
    if not parts:
        return None
    return parts[0].rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()


def _is_safe_command(leading: str | None, command: str) -> bool:
    if leading not in SAFE_COMMANDS:
        return False
    if leading == "git":
        return bool(re.match(r"^git\s+(status|diff|log|show)\b", command, re.IGNORECASE))
    if leading == "npm":
        return bool(re.match(r"^npm\s+(test|run\s+test)\b", command, re.IGNORECASE))
    if leading in {"python", "python3"}:
        return bool(re.match(r"^python3?\s+(-V|--version|-m\s+pytest\b)", command, re.IGNORECASE))
    if leading == "node":
        return bool(re.match(r"^node\s+(--version|-v|--check\b)", command, re.IGNORECASE))
    return True
