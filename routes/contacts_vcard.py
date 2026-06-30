"""vCard parsing and export helpers for contacts routes."""

import csv
import io
import re
import uuid
from typing import Dict, List, Optional

# ── vCard parsing ──

def _vunesc(value: str) -> str:
    """Reverse _vesc() — turn escaped vCard text back into the raw value.
    Order matters: handle \\n/\\, /\\; first, backslash-unescape last."""
    if not value:
        return value
    out = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            if nxt in ("n", "N"):
                out.append("\n")
            elif nxt in (",", ";", "\\"):
                out.append(nxt)
            else:
                out.append(nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _parse_vcards(text: str) -> List[Dict]:
    """Parse a stream of vCards into dicts with name, email, phone."""
    contacts = []
    for block in re.split(r"BEGIN:VCARD", text):
        if not block.strip():
            continue
        contact = {"name": "", "emails": [], "phones": [], "uid": "", "address": ""}
        for line in block.split("\n"):
            line = line.strip()
            # Strip an optional RFC 6350 group prefix (e.g. "item1.EMAIL;...")
            # that Apple Contacts / iCloud / many CardDAV servers emit by
            # default — without this the property-name checks below miss those
            # lines and silently drop the email / phone. The group token only
            # precedes the property name, so it is safe to strip for matching
            # and value extraction, and a no-op for non-grouped lines.
            name_part = re.sub(r"^[A-Za-z0-9-]+\.", "", line, count=1)
            if name_part.startswith("FN:") or name_part.startswith("FN;"):
                contact["name"] = _vunesc(name_part.split(":", 1)[1]) if ":" in name_part else ""
            elif name_part.startswith("EMAIL"):
                # Handle EMAIL:foo@bar OR EMAIL;TYPE=...:foo@bar OR EMAIL;PREF=1:foo@bar
                if ":" in name_part:
                    email_addr = _vunesc(name_part.split(":", 1)[1])
                    if email_addr and email_addr not in contact["emails"]:
                        contact["emails"].append(email_addr)
            elif name_part.startswith("TEL"):
                if ":" in name_part:
                    phone = _vunesc(name_part.split(":", 1)[1])
                    if phone and phone not in contact["phones"]:
                        contact["phones"].append(phone)
            elif name_part.startswith("ADR"):
                # vCard ADR is 7 semicolon-separated components:
                # post-office-box;extended-address;street;locality;region;postal-code;country.
                # Recover a human-readable string by joining non-empty
                # components with ", ".
                if ":" in name_part:
                    raw = name_part.split(":", 1)[1]
                    parts = [_vunesc(p).strip() for p in raw.split(";")]
                    contact["address"] = ", ".join(p for p in parts if p)
            elif name_part.startswith("UID:"):
                contact["uid"] = _vunesc(name_part[4:])
        if contact["name"] or contact["emails"]:
            contacts.append(contact)
    return contacts


def _vesc(value: str) -> str:
    """Escape a vCard property VALUE per RFC 6350 §3.4: backslash, comma,
    semicolon, and newlines. Without this, a name like 'Sekisui House,Ltd'
    or any value containing a newline produces a malformed vCard (broken
    N/FN fields) or could inject arbitrary properties."""
    return (
        (value or "")
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace("\r", "")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def _build_vcard(name: str, email: str, uid: Optional[str] = None,
                 emails: Optional[List[str]] = None,
                 phones: Optional[List[str]] = None,
                 address: Optional[str] = None) -> str:
    """Build a vCard. Accepts either a single `email` (legacy callers) or
    full `emails`/`phones` lists (edit path). The first email is marked
    PREF=1. All values are RFC-6350-escaped."""
    if not uid:
        uid = str(uuid.uuid4())
    # Normalize email lists — `email` arg is a convenience for single-email
    # creation; `emails` (if given) is authoritative.
    email_list = [e.strip() for e in (emails if emails is not None else ([email] if email else [])) if e and e.strip()]
    phone_list = [p.strip() for p in (phones or []) if p and p.strip()]
    # Try to split name into first/last
    parts = name.strip().split()
    if len(parts) >= 2:
        first = parts[0]
        last = " ".join(parts[1:])
    else:
        first = name
        last = ""
    # N field is structured (5 components separated by ';') — escape each
    # component individually so a comma in the name doesn't split it.
    n_field = f"{_vesc(last)};{_vesc(first)};;;"
    lines = [
        "BEGIN:VCARD",
        "VERSION:4.0",
        f"UID:{_vesc(uid)}",
        f"FN:{_vesc(name)}",
        f"N:{n_field}",
    ]
    for i, em in enumerate(email_list):
        # First email is the preferred one.
        lines.append(f"EMAIL;PREF=1:{_vesc(em)}" if i == 0 else f"EMAIL:{_vesc(em)}")
    for ph in phone_list:
        lines.append(f"TEL:{_vesc(ph)}")
    # Address: stuff the whole human-readable string into the street
    # component of ADR. vCard ADR has 7 semicolon-separated components:
    # post-office-box;extended-address;street;locality;region;postal-code;country.
    addr = (address or "").strip()
    if addr:
        lines.append(f"ADR:;;{_vesc(addr)};;;;")
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


# ── In-memory cache ──


def _contacts_to_vcf(contacts: List[Dict]) -> str:
    return "".join(
        _build_vcard(
            c.get("name") or ((c.get("emails") or [""])[0].split("@")[0] if c.get("emails") else "Contact"),
            "",
            uid=c.get("uid") or str(uuid.uuid4()),
            emails=c.get("emails") or [],
            phones=c.get("phones") or [],
        )
        for c in contacts
    )


def _contacts_to_csv(contacts: List[Dict]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["name", "email", "phone"])
    for c in contacts:
        emails = c.get("emails") or [""]
        phones = c.get("phones") or [""]
        max_len = max(len(emails), len(phones), 1)
        for i in range(max_len):
            writer.writerow([
                c.get("name") or "",
                emails[i] if i < len(emails) else "",
                phones[i] if i < len(phones) else "",
            ])
    return out.getvalue()


