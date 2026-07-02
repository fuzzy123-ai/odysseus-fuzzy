"""Optional local OCR helpers for Universal Inbox extraction.

The adapter is intentionally local-only. It never calls hosted providers and it
only returns OCR text to the in-memory extraction packet.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any, Mapping


class UniversalInboxOcrUnavailable(RuntimeError):
    """Raised when the configured local OCR runtime is unavailable."""


_LONG_ALNUM_TOKEN_RE = re.compile(r"\b[A-Za-z0-9][A-Za-z0-9_-]{7,}\b")


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
        best_text = ""
        last_error: UniversalInboxOcrUnavailable | None = None
        with tempfile.TemporaryDirectory(prefix="odysseus-uix-ocr-img-") as tmp:
            for candidate in self._image_candidates(image_path, Path(tmp)):
                for psm in _tesseract_psm_modes(candidate):
                    try:
                        text = self._run_tesseract(candidate, psm=psm)
                    except UniversalInboxOcrUnavailable as exc:
                        last_error = exc
                        continue
                    if _ocr_score(text) > _ocr_score(best_text):
                        best_text = text
        if best_text.strip():
            return _normalize_ocr_text(best_text)[: self.settings.max_chars]
        if last_error is not None:
            raise last_error
        return ""

    def _image_candidates(self, image_path: Path, tmp_dir: Path) -> tuple[Path, ...]:
        candidates = [image_path]
        try:
            from PIL import Image, ImageFilter, ImageOps
        except Exception:
            return tuple(candidates)

        try:
            with Image.open(image_path) as original:
                base = ImageOps.exif_transpose(original).convert("RGB")
        except Exception:
            return tuple(candidates)

        width, height = base.size
        if width < 32 or height < 32:
            return tuple(candidates)

        crop_specs = (
            ("full", (0.0, 0.0, 1.0, 1.0)),
            ("center", (0.12, 0.18, 0.88, 0.86)),
            ("device_body", (0.18, 0.24, 0.86, 0.72)),
            ("lower_label", (0.34, 0.50, 0.78, 0.72)),
        )
        for index, (name, box) in enumerate(crop_specs, start=1):
            left = int(width * box[0])
            top = int(height * box[1])
            right = int(width * box[2])
            bottom = int(height * box[3])
            if right - left < 24 or bottom - top < 24:
                continue
            crop = base.crop((left, top, right, bottom))
            scale = _ocr_scale_for(crop.size)
            resized = crop.resize((crop.size[0] * scale, crop.size[1] * scale))
            gray = ImageOps.grayscale(resized)
            contrasted = ImageOps.autocontrast(gray)
            variants = (("gray", contrasted.filter(ImageFilter.SHARPEN)),)
            for variant_name, image in variants:
                target = tmp_dir / f"{index:02d}-{name}-{variant_name}.png"
                try:
                    image.save(target)
                except Exception:
                    continue
                candidates.append(target)
        return tuple(dict.fromkeys(candidates))

    def _run_tesseract(self, image_path: Path, *, psm: str) -> str:
        command = [
            self.settings.tesseract_cmd,
            str(image_path),
            "stdout",
            "-l",
            self.settings.language,
            "--psm",
            psm,
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


def _tesseract_psm_modes(image_path: Path) -> tuple[str, ...]:
    name = image_path.name.lower()
    if "lower_label" in name:
        return ("6", "7", "11")
    if "device_body" in name or "center" in name or "full" in name:
        return ("11",)
    return ("6", "11")


def _ocr_scale_for(size: tuple[int, int]) -> int:
    longest = max(size)
    if longest < 700:
        return 4
    if longest < 1400:
        return 3
    return 2


def _normalize_ocr_text(text: str) -> str:
    lines = []
    seen = set()
    for raw_line in str(text or "").splitlines():
        line = " ".join(raw_line.strip().split())
        if not line:
            continue
        key = line.casefold()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
    return "\n".join(lines)


def _ocr_score(text: str) -> int:
    normalized = _normalize_ocr_text(text)
    clean_lines = _quality_ocr_lines(normalized)
    if not clean_lines:
        return 0
    clean_text = "\n".join(clean_lines)
    alnum = sum(1 for char in clean_text if char.isalnum())
    tokens = _LONG_ALNUM_TOKEN_RE.findall(clean_text)
    mixed_tokens = [
        token
        for token in tokens
        if any(char.isalpha() for char in token) and any(char.isdigit() for char in token)
    ]
    digit_bonus = 20 if any(char.isdigit() for char in clean_text) else 0
    token_bonus = len(tokens) * 60 + len(mixed_tokens) * 220
    line_bonus = min(5, len(clean_lines)) * 4
    line_penalty = max(0, len(clean_lines) - 8) * 18
    symbol_penalty = sum(
        1
        for char in normalized
        if not (char.isalnum() or char.isspace() or char in ".,:;/-_+()[]")
    )
    return min(alnum, 140) + digit_bonus + token_bonus + line_bonus - line_penalty - symbol_penalty


def _quality_ocr_lines(text: str) -> tuple[str, ...]:
    lines = []
    for line in _normalize_ocr_text(text).splitlines():
        visible = [char for char in line if not char.isspace()]
        if len(visible) < 2:
            continue
        alnum = sum(1 for char in visible if char.isalnum())
        if alnum < 2:
            continue
        if alnum / max(1, len(visible)) < 0.45:
            continue
        lines.append(line)
    return tuple(lines)


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
