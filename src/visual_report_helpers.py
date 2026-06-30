"""Markdown and media helpers for visual research reports."""

from __future__ import annotations

import html
import json
import logging
import re
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
import markdown
import nh3

logger = logging.getLogger(__name__)

# Tags/attributes permitted in rendered research-report HTML. Starts from nh3's
# safe defaults (which drop <script>, inline event handlers, and javascript:
# URLs) and adds back only the formatting the report itself emits.
_REPORT_ALLOWED_TAGS = set(nh3.ALLOWED_TAGS) | {"details", "summary"}
_REPORT_ALLOWED_ATTRS = {k: set(v) for k, v in nh3.ALLOWED_ATTRIBUTES.items()}
for _h in ("h1", "h2", "h3", "h4", "h5", "h6"):
    _REPORT_ALLOWED_ATTRS.setdefault(_h, set()).add("id")
for _t in ("span", "code", "pre", "div", "table", "td", "th"):
    _REPORT_ALLOWED_ATTRS.setdefault(_t, set()).add("class")
for _t in ("td", "th"):
    _REPORT_ALLOWED_ATTRS.setdefault(_t, set()).add("align")
_REPORT_ALLOWED_ATTRS.setdefault("a", set()).update({"href", "title", "target", "rel"})
_REPORT_ALLOWED_ATTRS.setdefault("img", set()).update({"src", "alt", "title"})


def _autolink_urls(md_text: str) -> str:
    """Convert bare URLs to markdown links before processing."""
    if not isinstance(md_text, str):
        return md_text
    return re.sub(
        r'(?<!\]\()(?<!\()(https?://[^\s\)<>]+)',
        r'[\1](\1)',
        md_text,
    )


def _md_to_html(md_text: str) -> str:
    """Convert untrusted report markdown to sanitized HTML."""
    md_text = _autolink_urls(md_text)
    result = markdown.markdown(
        md_text,
        extensions=["extra", "codehilite", "toc", "tables", "sane_lists"],
        extension_configs={
            "codehilite": {"css_class": "code", "guess_lang": False},
            "toc": {"marker": "", "toc_depth": "2-3"},
        },
    )
    result = re.sub(
        r'<a href="(https?://)',
        r'<a target="_blank" rel="noopener noreferrer" href="\1',
        result,
    )
    return nh3.clean(
        result,
        tags=_REPORT_ALLOWED_TAGS,
        attributes=_REPORT_ALLOWED_ATTRS,
        link_rel=None,
    )


def _extract_headings(md_text: str) -> List[Dict[str, str]]:
    """Pull h2/h3 headings from markdown for table of contents."""
    if not isinstance(md_text, str):
        return []
    headings = []
    seen_slugs: Dict[str, int] = {}

    def _plain_heading_text(text: str) -> str:
        text = text.strip().rstrip("#").strip()
        text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'\1', text)
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        text = re.sub(r'\[([^\]]+)\]\[[^\]]+\]', r'\1', text)
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'[`*_~]+', '', text)
        text = html.unescape(text)
        return re.sub(r'\s+', ' ', text).strip()

    def _make_slug(text: str) -> str:
        slug = re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')
        if not slug:
            slug = "section"
        if slug in seen_slugs:
            seen_slugs[slug] += 1
            slug = f"{slug}-{seen_slugs[slug]}"
        else:
            seen_slugs[slug] = 0
        return slug

    for match in re.finditer(r'^(#{2,3})\s+(.+)$', md_text, re.MULTILINE):
        level = len(match.group(1))
        text = _plain_heading_text(match.group(2))
        if text:
            headings.append({"level": level, "text": text, "slug": _make_slug(text)})
    if not headings:
        for match in re.finditer(r'^\*\*([^*]+)\*\*\s*$', md_text, re.MULTILINE):
            text = _plain_heading_text(match.group(1)).rstrip(':')
            if 3 < len(text) < 80:
                headings.append({"level": 2, "text": text, "slug": _make_slug(text)})
    return headings


def _apply_heading_ids(report_html: str, headings: List[Dict[str, str]]) -> str:
    """Force rendered h2/h3 IDs to match the generated sidebar links."""
    if not headings:
        return report_html

    soup = BeautifulSoup(report_html, "html.parser")
    rendered_headings = soup.find_all(["h2", "h3"])
    for element, heading in zip(rendered_headings, headings):
        expected_name = f"h{heading['level']}"
        if element.name != expected_name:
            logger.debug(
                "Visual report heading level mismatch: rendered %s for TOC %s",
                element.name,
                expected_name,
            )
        element["id"] = heading["slug"]
    if len(rendered_headings) != len(headings):
        logger.debug(
            "Visual report heading count mismatch: rendered=%s toc=%s",
            len(rendered_headings),
            len(headings),
        )
    return str(soup)


_IMG_OVERLAY_BTNS = (
    '<button class="img-reroll-btn" type="button" title="Swap for another image">'
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 11-2.12-9.36L23 10"/></svg>'
    '</button>'
    '<button class="img-hide-btn" type="button" title="Hide image">'
    '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>'
    '</button>'
)


def _inject_images(report_html: str, images: List[str]) -> Tuple[str, int]:
    """Insert OG images between h2 sections and return ``(html, consumed)``."""
    if not images:
        return report_html, 0

    h2_positions = [match.end() for match in re.finditer(r'</h2>', report_html)]
    if not h2_positions:
        return report_html, 0

    img_idx = 0
    insert_after = h2_positions[1::2]
    for pos in reversed(insert_after):
        if img_idx >= len(images):
            break
        img_url = images[img_idx]
        img_idx += 1
        url_esc = html.escape(img_url)
        figure = (
            f'\n<figure class="section-image" data-img-url="{url_esc}">'
            f'<img src="{url_esc}" alt="" loading="lazy" '
            f'onerror="this.parentElement.style.display=\'none\'">'
            f'{_IMG_OVERLAY_BTNS}'
            f'</figure>\n'
        )
        report_html = report_html[:pos] + figure + report_html[pos:]

    return report_html, img_idx


_GENERIC_HEADINGS = {
    "report", "deep research report", "research",
    "executive summary", "summary", "tl;dr",
    "introduction", "overview", "abstract",
    "findings", "key findings", "results",
    "conclusion", "conclusions", "table of contents",
}


def _extract_report_title(markdown_text: str, fallback: str):
    """Pull a non-generic title from the first useful report heading."""
    if not markdown_text:
        return fallback, markdown_text

    candidates = []
    for level, pattern in ((1, r'^# +(.+?)\s*$'), (2, r'^## +(.+?)\s*$')):
        for match in re.finditer(pattern, markdown_text, re.MULTILINE):
            cand = match.group(1).strip().rstrip('#').strip()
            if cand and cand.lower() not in _GENERIC_HEADINGS:
                candidates.append((level, match, cand))

    candidates.sort(key=lambda item: (item[0], item[1].start()))
    if candidates:
        _level, match, title = candidates[0]
        stripped = markdown_text[:match.start()] + markdown_text[match.end():]
        return title, stripped.lstrip()
    return fallback, markdown_text


_ICON_LOGO_RE = re.compile(r'/(icon|logo|favicon)([._/-]|$)', re.IGNORECASE)


def _is_icon_or_logo_url(url: str) -> bool:
    """Return true when a URL path points at an icon/logo/favicon asset."""
    return bool(_ICON_LOGO_RE.search(url or ""))


def _json_for_script(value) -> str:
    """JSON-encode a value safe to embed inside a <script> block."""
    return json.dumps(value).replace("</", "<\\/")


def json_dumps_str(s: str) -> str:
    """JSON-encode a string so it's safe to embed inside a <script> block."""
    return _json_for_script(s)
