"""Optional local OCR helpers for Universal Inbox extraction.

The adapter is intentionally local-only. It never calls hosted providers and it
only returns OCR text to the in-memory extraction packet.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any, Mapping


class UniversalInboxOcrUnavailable(RuntimeError):
    """Raised when the configured local OCR runtime is unavailable."""


@dataclass(frozen=True)
class UniversalInboxOcrSettings:
    enabled: bool = False
    engine: str = "tesseract_cli"
    tesseract_cmd: str = "tesseract"
    language: str = "deu+eng"
    timeout_seconds: float = 30.0
    max_chars: int = 12000
    max_pdf_pages: int = 3
    max_images_per_page: int = 1

    def policy_context(self) -> dict[str, Any]:
        return {
            "local_only": True,
            "external_ocr_requested": False,
            "ocr_engine": self.engine,
        }


def load_universal_inbox_ocr_settings(
    env: Mapping[str, str] | None = None,
) -> UniversalInboxOcrSettings:
    source = env or os.environ
    enabled = _bool_value(
        source.get("UNIVERSAL_INBOX_OCR_ENABLED")
        or source.get("TELEGRAM_ATTACHMENT_OCR_ENABLED")
        or ""
    )
    return UniversalInboxOcrSettings(
        enabled=enabled,
        engine=str(source.get("UNIVERSAL_INBOX_OCR_ENGINE") or "tesseract_cli").strip() or "tesseract_cli",
        tesseract_cmd=str(source.get("TESSERACT_CMD") or source.get("TESSERACT_PATH") or "tesseract").strip() or "tesseract",
        language=str(source.get("UNIVERSAL_INBOX_OCR_LANG") or "deu+eng").strip() or "deu+eng",
        timeout_seconds=_float_value(source.get("UNIVERSAL_INBOX_OCR_TIMEOUT_SECONDS"), 30.0, minimum=1.0, maximum=300.0),
        max_chars=_int_value(source.get("UNIVERSAL_INBOX_OCR_MAX_CHARS"), 12000, minimum=256, maximum=100000),
        max_pdf_pages=_int_value(source.get("UNIVERSAL_INBOX_OCR_MAX_PDF_PAGES"), 3, minimum=1, maximum=50),
        max_images_per_page=_int_value(source.get("UNIVERSAL_INBOX_OCR_MAX_IMAGES_PER_PAGE"), 1, minimum=0, maximum=20),
    )


def build_universal_inbox_ocr_adapter(
    settings: UniversalInboxOcrSettings | None = None,
):
    selected = settings or load_universal_inbox_ocr_settings()
    if not selected.enabled:
        return None
    if selected.engine != "tesseract_cli":
        raise UniversalInboxOcrUnavailable(f"unsupported_ocr_engine:{selected.engine}")
    return LocalTesseractOcrAdapter(selected)


class LocalTesseractOcrAdapter:
    def __init__(self, settings: UniversalInboxOcrSettings) -> None:
        self.settings = settings

    def __call__(
        self,
        source: Path,
        page_number: int | None,
        context: Mapping[str, Any],
    ) -> str:
        del context
        self._require_tesseract()
        if page_number is None:
            return self._ocr_image(source)
        with tempfile.TemporaryDirectory(prefix="odysseus-uix-ocr-") as tmp:
            image_path = Path(tmp) / f"page-{page_number}.png"
            self._render_pdf_page(source, page_number, image_path)
            return self._ocr_image(image_path)

    def _require_tesseract(self) -> None:
        command = self.settings.tesseract_cmd
        if Path(command).is_file():
            return
        if shutil.which(command):
            return
        raise UniversalInboxOcrUnavailable("tesseract_not_found")

    def _render_pdf_page(self, source: Path, page_number: int, image_path: Path) -> None:
        if self._render_pdf_page_with_poppler(source, page_number, image_path):
            return
        try:
            import fitz  # PyMuPDF, optional
        except Exception as exc:
            raise UniversalInboxOcrUnavailable(f"pdf_renderer_not_available:{type(exc).__name__}") from exc

        try:
            with fitz.open(str(source)) as document:
                page = document.load_page(page_number - 1)
                pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                pixmap.save(str(image_path))
        except Exception as exc:
            raise UniversalInboxOcrUnavailable(f"pdf_page_render_failed:{type(exc).__name__}") from exc

    def _render_pdf_page_with_poppler(self, source: Path, page_number: int, image_path: Path) -> bool:
        if not shutil.which("pdftoppm"):
            return False
        output_prefix = image_path.with_suffix("")
        command = [
            "pdftoppm",
            "-f",
            str(page_number),
            "-l",
            str(page_number),
            "-singlefile",
            "-png",
            "-r",
            "200",
            str(source),
            str(output_prefix),
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.settings.timeout_seconds,
            )
        except (subprocess.TimeoutExpired, OSError):
            return False
        if completed.returncode != 0:
            return False
        return image_path.exists()

    def _ocr_image(self, image_path: Path) -> str:
        command = [
            self.settings.tesseract_cmd,
            str(image_path),
            "stdout",
            "-l",
            self.settings.language,
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.settings.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise UniversalInboxOcrUnavailable("tesseract_timeout") from exc
        except OSError as exc:
            raise UniversalInboxOcrUnavailable(f"tesseract_exec_failed:{type(exc).__name__}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or "").strip().splitlines()[0:1]
            suffix = f":{detail[0][:80]}" if detail else ""
            raise UniversalInboxOcrUnavailable(f"tesseract_failed{suffix}")
        return str(completed.stdout or "")[: self.settings.max_chars]


def _bool_value(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "go", "enabled"}


def _int_value(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def _float_value(value: Any, default: float, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))
