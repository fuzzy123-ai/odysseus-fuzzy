"""Telegram-safe formatting helpers for bot replies."""

from __future__ import annotations

from dataclasses import dataclass
import html
import re
from typing import Any


_TELEGRAM_MAX_MESSAGE_CHARS = 4096
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_TAG_RE = re.compile(r"<[^>]+>")
_ALLOWED_SIMPLE_TAG_RE = re.compile(
    r"</?(?:b|strong|i|em|u|s|strike|del|tg-spoiler|code|pre|blockquote)>"
)
_ALLOWED_LINK_OPEN_RE = re.compile(r'<a href="https?://[^"<>\s]+">')
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")


class TelegramFormattingError(ValueError):
    """Raised when Telegram formatting cannot be produced safely."""


@dataclass(frozen=True, slots=True)
class TelegramRenderedMessage:
    html: str
    plaintext: str
    parse_mode: str
    formatting_mode: str
    valid_html: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "parse_mode": self.parse_mode,
            "formatting_mode": self.formatting_mode,
            "valid_html": self.valid_html,
            "html_length": len(self.html),
            "plaintext_length": len(self.plaintext),
        }


def render_telegram_markdown(markdown: str) -> TelegramRenderedMessage:
    """Render assistant Markdown into Telegram-safe HTML, or safe plaintext."""

    plaintext = _normalize_text(markdown)
    rendered = _render_blocks(plaintext)
    if validate_telegram_html(rendered):
        return TelegramRenderedMessage(
            html=rendered,
            plaintext=plaintext,
            parse_mode="HTML",
            formatting_mode="html",
            valid_html=True,
        )
    return TelegramRenderedMessage(
        html=html.escape(plaintext),
        plaintext=plaintext,
        parse_mode="",
        formatting_mode="plaintext_fallback",
        valid_html=False,
    )


def validate_telegram_html(value: str) -> bool:
    """Return whether generated HTML uses only the supported Telegram subset."""

    for tag in _TAG_RE.findall(str(value or "")):
        if _ALLOWED_SIMPLE_TAG_RE.fullmatch(tag):
            continue
        if _ALLOWED_LINK_OPEN_RE.fullmatch(tag) or tag == "</a>":
            continue
        return False
    return True


def chunk_telegram_html(value: str, *, max_chars: int = _TELEGRAM_MAX_MESSAGE_CHARS) -> tuple[str, ...]:
    """Split Telegram HTML/plaintext into bounded chunks."""

    text = str(value or "")
    if not text.strip():
        raise TelegramFormattingError("telegram message text is empty")
    if max_chars < 256:
        raise TelegramFormattingError("max_chars is too small for Telegram chunking")
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        split_at = max(remaining.rfind("\n", 0, max_chars + 1), remaining.rfind(" ", 0, max_chars + 1))
        if split_at < max_chars // 2:
            split_at = max_chars
        chunk = remaining[:split_at].rstrip()
        if not chunk:
            chunk = remaining[:max_chars]
            split_at = max_chars
        chunks.append(chunk)
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return tuple(chunks)


def _normalize_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise TelegramFormattingError("telegram message text is empty")
    return text.strip()


def _render_blocks(markdown: str) -> str:
    lines = markdown.split("\n")
    rendered: list[str] = []
    code_lines: list[str] = []
    in_code = False
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                rendered.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
                code_lines = []
                in_code = False
            else:
                in_code = True
            index += 1
            continue
        if in_code:
            code_lines.append(line)
            index += 1
            continue
        if _is_table_start(lines, index):
            table_lines = [line]
            index += 1
            while index < len(lines) and "|" in lines[index]:
                table_lines.append(lines[index])
                index += 1
            rendered.append(f"<pre>{html.escape(chr(10).join(table_lines))}</pre>")
            continue
        if not stripped:
            rendered.append("")
        elif stripped.startswith("#"):
            rendered.append(f"<b>{_render_inline(stripped.lstrip('#').strip())}</b>")
        elif stripped.startswith(">"):
            rendered.append(f"<blockquote>{_render_inline(stripped.lstrip('>').strip())}</blockquote>")
        else:
            rendered.append(_render_inline(line))
        index += 1
    if in_code:
        rendered.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
    return "\n".join(rendered).strip()


def _is_table_start(lines: list[str], index: int) -> bool:
    if "|" not in lines[index] or index + 1 >= len(lines):
        return False
    return bool(_TABLE_SEPARATOR_RE.match(lines[index + 1]))


def _render_inline(text: str) -> str:
    parts: list[str] = []
    cursor = 0
    for match in _LINK_RE.finditer(text):
        parts.append(_render_inline_basic(text[cursor:match.start()]))
        label, href = match.group(1), match.group(2)
        if href.startswith(("http://", "https://")):
            parts.append(f'<a href="{html.escape(href, quote=True)}">{_render_inline_basic(label)}</a>')
        else:
            parts.append(_render_inline_basic(match.group(0)))
        cursor = match.end()
    parts.append(_render_inline_basic(text[cursor:]))
    return "".join(parts)


def _render_inline_basic(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`\n]+?)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\|\|(.+?)\|\|", r"<tg-spoiler>\1</tg-spoiler>", escaped)
    escaped = re.sub(r"~~(.+?)~~", r"<s>\1</s>", escaped)
    escaped = re.sub(r"__(.+?)__", r"<u>\1</u>", escaped)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<i>\1</i>", escaped)
    return escaped
