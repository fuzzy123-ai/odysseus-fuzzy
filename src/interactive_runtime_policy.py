"""Pure classification policy for native GUI and headless runtime commands.

No command is executed and the returned decision never stores raw command text.
The classifier is deliberately conservative around installs and shell constructs
that can replace a meaningful process exit code with a successful downstream
command.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
from typing import Any


INTERACTIVE_RUNTIME_POLICY_SCHEMA = "odysseus.interactive_runtime_policy.v1"
_MAX_COMMAND_CHARS = 16_000
_MAX_REASON_CODES = 10

_INSTALL_RE = re.compile(
    r"(?:"
    r"\bpip(?:3)?\b[^\r\n;&|]{0,120}\binstall\b|"
    r"\buv\b[^\r\n;&|]{0,120}\b(?:add|install)\b|"
    r"\b(?:apt|apt-get|dnf|yum|apk|pacman|zypper)\b[^\r\n;&|]{0,120}\b(?:add|install)\b|"
    r"\b(?:npm|pnpm|yarn)\b[^\r\n;&|]{0,120}\b(?:add|install|i)\b|"
    r"\b(?:poetry|pdm)\b[^\r\n;&|]{0,120}\badd\b|"
    r"\b(?:winget|choco|scoop|brew|gem|cargo)\b[^\r\n;&|]{0,120}\binstall\b"
    r")",
    re.IGNORECASE,
)
_SINGLE_PIPE_RE = re.compile(r"(?<!\|)\|(?![|=])")
_EXPLICIT_MASK_RE = re.compile(
    r"(?:\|\|\s*(?:true|exit\s+0|:)\b|"
    r"(?:^|[;\r\n])\s*(?:true|exit\s+0)\s*(?:$|[;\r\n])|"
    r"\$LASTEXITCODE\s*=\s*0\b)",
    re.IGNORECASE,
)
_PIPEFAIL_RE = re.compile(r"\bset\s+-o\s+pipefail\b", re.IGNORECASE)
_PYTHON_EXEC_RE = re.compile(
    r"(?:\bpython(?:3(?:\.\d+)*)?(?:\.exe)?|"
    r"(?:^|[\\/;&|()\s\"'])py(?:\.exe)?)(?=[\s\"']|$)",
    re.IGNORECASE,
)
_PYTHON_SCRIPT_RE = re.compile(
    r"(?:^|[\s\"'])([^\s;&|\"']+\.py)(?=$|[\s;&|\"'])",
    re.IGNORECASE,
)
_NATIVE_SCRIPT_NAME_RE = re.compile(
    r"(?:pygame|game|spiel|gui|window|desktop|arcade|kivy|mario)",
    re.IGNORECASE,
)
_GUI_FRAMEWORK_RE = re.compile(
    r"\b(?:pygame(?:-ce)?|tkinter|pyqt\d*|pyside\d*|wxpython|kivy|pyglet|arcade)\b",
    re.IGNORECASE,
)
_GUI_OPERATION_RE = re.compile(
    r"(?:display\.(?:set_mode|flip|update)|pygame\.init\s*\(|"
    r"Tk\s*\(|QApplication\s*\(|\.mainloop\s*\(|\.run\s*\()",
    re.IGNORECASE,
)
_DIRECT_NATIVE_BINARY_RE = re.compile(
    r"(?:^|[\s\"'])(?:[^\s;&|\"']*[\\/])?"
    r"[^\s;&|\"']*(?:game|spiel|gui|arcade)[^\s;&|\"']*\.exe"
    r"(?=$|[\s;&|\"'])",
    re.IGNORECASE,
)
_PIP_PROBE_RE = re.compile(
    r"\bpip(?:3)?\b[^\r\n;&|]{0,100}\b(?:show|list|freeze|check|debug)\b",
    re.IGNORECASE,
)
_EXECUTABLE_PROBE_RE = re.compile(
    r"(?:\b(?:command\s+-v|which|where(?:\.exe)?|get-command)\s+|"
    r"\b(?:python(?:3(?:\.\d+)*)?|py)(?:\.exe)?\s+--version\b)",
    re.IGNORECASE,
)
_PYTHON_CODE_PROBE_RE = re.compile(
    r"(?:\bimport\s+(?:pygame|tkinter|PyQt\d*|PySide\d*)\b|"
    r"\bfind_spec\s*\(|\bmetadata\.version\s*\()",
    re.IGNORECASE,
)
_DUMMY_POWERSHELL_RE = re.compile(
    r"\$env:SDL_VIDEODRIVER\s*=\s*(?:\"dummy\"|'dummy'|dummy\b)",
    re.IGNORECASE,
)
_DUMMY_CMD_RE = re.compile(
    r"\bset\s+(?:\"SDL_VIDEODRIVER=dummy\"|SDL_VIDEODRIVER\s*=\s*dummy)\b",
    re.IGNORECASE,
)
_DUMMY_EXPORT_RE = re.compile(
    r"\bexport\s+SDL_VIDEODRIVER\s*=\s*(?:\"dummy\"|'dummy'|dummy\b)",
    re.IGNORECASE,
)
_DUMMY_INLINE_RE = re.compile(
    r"(?:^|[;&]\s*|\benv\s+)SDL_VIDEODRIVER\s*=\s*"
    r"(?:\"dummy\"|'dummy'|dummy)\s+(?=[^;\r\n]*(?:python|py(?:\.exe)?\s))",
    re.IGNORECASE,
)
_DUMMY_PYTHON_RE = re.compile(
    r"(?:os\.environ\s*\[\s*[\"']SDL_VIDEODRIVER[\"']\s*\]\s*=\s*[\"']dummy[\"']|"
    r"os\.environ\.setdefault\s*\(\s*[\"']SDL_VIDEODRIVER[\"']\s*,\s*[\"']dummy[\"']\s*\))",
    re.IGNORECASE,
)
_DUMMY_RESET_RE = re.compile(
    r"(?:\bunset\s+SDL_VIDEODRIVER\b|"
    r"remove-item\s+env:SDL_VIDEODRIVER\b|"
    r"(?:\$env:|\bset\s+)SDL_VIDEODRIVER\s*=\s*(?![\"']?dummy\b))",
    re.IGNORECASE,
)


class InteractiveRuntimePolicyError(ValueError):
    """Raised when a runtime command cannot be classified safely."""


class InteractiveRuntimeKind(StrEnum):
    INTERACTIVE_NATIVE_GUI_LAUNCH = "interactive_native_gui_launch"
    HEADLESS_CAPTURE = "headless_capture"
    DEPENDENCY_PROBE = "dependency_probe"
    RISKY_INSTALL = "risky_install"
    PIPELINE_MASKING = "pipeline_masking"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class InteractiveRuntimeDecision:
    kind: InteractiveRuntimeKind
    permitted: bool
    requires_separate_gate: bool
    headless: bool
    install_detected: bool
    pipeline_masking_detected: bool
    command_chars: int
    reason_codes: tuple[str, ...]
    recommended_next_action: str
    schema: str = INTERACTIVE_RUNTIME_POLICY_SCHEMA

    @property
    def classification(self) -> InteractiveRuntimeKind:
        return self.kind

    @property
    def allowed(self) -> bool:
        return self.permitted

    def audit_summary(self) -> dict[str, Any]:
        """Return bounded, redacted audit data; never return the command."""

        return {
            "schema": self.schema,
            "kind": self.kind.value,
            "permitted": self.permitted,
            "requires_separate_gate": self.requires_separate_gate,
            "headless": self.headless,
            "install_detected": self.install_detected,
            "pipeline_masking_detected": self.pipeline_masking_detected,
            "command_chars": self.command_chars,
            "reason_codes": self.reason_codes[:_MAX_REASON_CODES],
            "recommended_next_action": self.recommended_next_action,
            "raw_command_visible": False,
            "raw_content_visible": False,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.audit_summary()


def classify_interactive_runtime(
    command: Any,
    *,
    native_gui_hint: bool = False,
) -> InteractiveRuntimeDecision:
    """Classify a command without executing it or retaining its raw text."""

    text = str(command or "")
    if not text.strip():
        raise InteractiveRuntimePolicyError("command must not be empty")
    if len(text) > _MAX_COMMAND_CHARS:
        raise InteractiveRuntimePolicyError(
            f"command exceeds max length {_MAX_COMMAND_CHARS}"
        )

    install_detected = bool(_INSTALL_RE.search(text))
    runtime_execution = _has_runtime_execution(text)
    dependency_probe = _is_dependency_probe(text)
    dummy_driver = _has_effective_dummy_driver(text) and not _DUMMY_RESET_RE.search(text)
    native_launch = _is_native_gui_launch(
        text,
        runtime_execution=runtime_execution,
        native_gui_hint=bool(native_gui_hint),
    )

    has_pipeline = bool(_SINGLE_PIPE_RE.search(text))
    pipeline_masking = bool(_EXPLICIT_MASK_RE.search(text))
    if has_pipeline and not _PIPEFAIL_RE.search(text):
        pipeline_masking = install_detected or runtime_execution or dependency_probe

    reasons: list[str] = []
    if install_detected:
        reasons.append("dependency_install_detected")
    if pipeline_masking:
        reasons.append("exit_status_may_be_masked")
    if dummy_driver:
        reasons.append("sdl_dummy_video_driver")
    if dependency_probe:
        reasons.append("dependency_probe_detected")
    if native_launch:
        reasons.append("native_gui_launch_detected")

    if pipeline_masking:
        kind = InteractiveRuntimeKind.PIPELINE_MASKING
        permitted = False
        requires_gate = False
        headless = False
        action = "rewrite_command_to_preserve_exit_status"
    elif install_detected:
        kind = InteractiveRuntimeKind.RISKY_INSTALL
        permitted = False
        requires_gate = True
        headless = False
        action = "use_dependency_probe_or_request_install_gate"
    elif dependency_probe:
        kind = InteractiveRuntimeKind.DEPENDENCY_PROBE
        permitted = True
        requires_gate = False
        headless = False
        action = "run_bounded_dependency_probe"
    elif dummy_driver and runtime_execution:
        kind = InteractiveRuntimeKind.HEADLESS_CAPTURE
        permitted = True
        requires_gate = False
        headless = True
        reasons.append("bounded_headless_execution_permitted")
        action = "run_bounded_headless_capture"
    elif native_launch:
        kind = InteractiveRuntimeKind.INTERACTIVE_NATIVE_GUI_LAUNCH
        permitted = False
        requires_gate = False
        headless = False
        reasons.append("native_gui_not_visible_in_browser")
        action = "publish_native_download_or_build_browser_preview"
    else:
        kind = InteractiveRuntimeKind.OTHER
        permitted = True
        requires_gate = False
        headless = False
        reasons.append("no_interactive_runtime_signal")
        action = "apply_standard_command_policy"

    return InteractiveRuntimeDecision(
        kind=kind,
        permitted=permitted,
        requires_separate_gate=requires_gate,
        headless=headless,
        install_detected=install_detected,
        pipeline_masking_detected=pipeline_masking,
        command_chars=len(text),
        reason_codes=tuple(reasons[:_MAX_REASON_CODES]),
        recommended_next_action=action,
    )


def classify_interactive_runtime_command(
    command: Any,
    *,
    native_gui_hint: bool = False,
) -> InteractiveRuntimeDecision:
    """Explicitly named alias for tool-policy integration."""

    return classify_interactive_runtime(command, native_gui_hint=native_gui_hint)


def _has_runtime_execution(text: str) -> bool:
    if not _PYTHON_EXEC_RE.search(text):
        return bool(_DIRECT_NATIVE_BINARY_RE.search(text))
    if _PYTHON_SCRIPT_RE.search(text):
        return True
    if re.search(r"\s-c(?:\s|$)", text, re.IGNORECASE):
        return True
    return bool(re.search(r"\s-m\s+(?:pygame|arcade|pyglet)\b", text, re.IGNORECASE))


def _is_dependency_probe(text: str) -> bool:
    if _PIP_PROBE_RE.search(text) or _EXECUTABLE_PROBE_RE.search(text):
        return True
    if not re.search(r"\s-c(?:\s|$)", text, re.IGNORECASE):
        return False
    if not _PYTHON_CODE_PROBE_RE.search(text):
        return False
    return not _GUI_OPERATION_RE.search(text)


def _is_native_gui_launch(
    text: str,
    *,
    runtime_execution: bool,
    native_gui_hint: bool,
) -> bool:
    if not runtime_execution:
        return False
    if native_gui_hint or _DIRECT_NATIVE_BINARY_RE.search(text):
        return True
    if _GUI_OPERATION_RE.search(text):
        return True
    script_match = _PYTHON_SCRIPT_RE.search(text)
    if script_match and _NATIVE_SCRIPT_NAME_RE.search(script_match.group(1)):
        return True
    return bool(_GUI_FRAMEWORK_RE.search(text))


def _has_effective_dummy_driver(text: str) -> bool:
    return bool(
        _DUMMY_POWERSHELL_RE.search(text)
        or _DUMMY_CMD_RE.search(text)
        or _DUMMY_EXPORT_RE.search(text)
        or _DUMMY_INLINE_RE.search(text)
        or _DUMMY_PYTHON_RE.search(text)
    )


__all__ = [
    "INTERACTIVE_RUNTIME_POLICY_SCHEMA",
    "InteractiveRuntimeDecision",
    "InteractiveRuntimeKind",
    "InteractiveRuntimePolicyError",
    "classify_interactive_runtime",
    "classify_interactive_runtime_command",
]
