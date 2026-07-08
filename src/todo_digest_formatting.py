"""Formatting helpers for todo digest notification bodies."""

from __future__ import annotations

import re


_NOTIFICATION_PREFIX_RE = re.compile(r"^\[Odysseus\]\[[^\]]+\]\s+(?:scheduled_task|todo_digest):\s*", re.IGNORECASE)
_NOTIFICATION_TASK_ID_RE = re.compile(r"(?:\n|\s)+task_id=sha256:[0-9a-f]{8,64}\s*$", re.IGNORECASE)


def format_todo_digest_notification_body(body: str) -> str:
    """Clean legacy flat todo-digest notification text for user-facing channels."""
    text = str(body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    text = _NOTIFICATION_PREFIX_RE.sub("", text).strip()
    text = _NOTIFICATION_TASK_ID_RE.sub("", text).strip()
    if "\n" in text:
        return collapse_repeated_open_item_list_prefixes(text).strip()
    for header in ("Overdue:", "Due today:", "Pinned:", "Open items:"):
        text = re.sub(rf"\s+{re.escape(header)}\s*", f"\n\n{header}\n", text)
    text = re.sub(r"(?<!\n)\s-\s+", "\n- ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return collapse_repeated_open_item_list_prefixes(text).strip()


def collapse_repeated_open_item_list_prefixes(text: str) -> str:
    """Render repeated ``List: item`` bullets under one list heading.

    The todo digest usually renders checklist items as ``- List: Item`` so
    mixed lists stay unambiguous. When a digest contains several items from the
    same list, repeating that prefix in every line makes Telegram reminders
    noisy. This keeps single-item and mixed-list digests unchanged, and groups
    only one-list multi-item Open items sections.
    """
    body = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = body.split("\n")
    out: list[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        out.append(line)
        if line.strip() != "Open items:":
            index += 1
            continue

        index += 1
        segment: list[str] = []
        while index < len(lines):
            current = lines[index]
            stripped = current.strip()
            if stripped.endswith(":") and not stripped.startswith("- "):
                break
            segment.append(current)
            index += 1

        out.extend(_collapse_open_items_segment(segment))

    return "\n".join(out)


def _collapse_open_items_segment(lines: list[str]) -> list[str]:
    bullet_indexes: list[int] = []
    parsed: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("- "):
            continue
        item = stripped[2:].strip()
        title, separator, text = item.partition(": ")
        if not separator or not title.strip() or not text.strip():
            return lines
        bullet_indexes.append(index)
        parsed.append((title.strip(), text.strip()))

    if len(parsed) < 2:
        return lines
    titles = {title for title, _text in parsed}
    if len(titles) != 1:
        return lines

    first_bullet = bullet_indexes[0]
    last_bullet = bullet_indexes[-1]
    if bullet_indexes != list(range(first_bullet, last_bullet + 1)):
        return lines

    title = parsed[0][0]
    grouped = [f"{title}:"] + [f"- {text}" for _title, text in parsed]
    return lines[:first_bullet] + grouped + lines[last_bullet + 1:]
