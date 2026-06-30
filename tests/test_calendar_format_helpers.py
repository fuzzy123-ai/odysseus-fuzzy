from datetime import date, datetime, timezone, timedelta

import pytest

from routes.calendar_format_helpers import (
    _ics_escape,
    _ics_naive_dtstart,
    _resolve_base_uid,
    _safe_ics_filename,
)


def test_ics_naive_dtstart_matches_storage_shape():
    aware = datetime(2026, 6, 15, 15, 0, tzinfo=timezone(timedelta(hours=2)))
    assert _ics_naive_dtstart(aware) == datetime(2026, 6, 15, 13, 0)
    assert _ics_naive_dtstart(date(2026, 6, 15)) == datetime(2026, 6, 15, 0, 0)


def test_ics_escape_text_value():
    assert _ics_escape("A;B,C\\D\nE") == "A\\;B\\,C\\\\D\\nE"


def test_safe_ics_filename_is_header_safe():
    assert _safe_ics_filename("../Bad Calendar\r\nX") == "Bad_Calendar__X.ics"
    assert _safe_ics_filename("") == "calendar.ics"


def test_resolve_base_uid_compound_and_invalid():
    assert _resolve_base_uid("evt-1::2026-06-15") == "evt-1"
    assert _resolve_base_uid("evt-1") == "evt-1"
    with pytest.raises(ValueError):
        _resolve_base_uid("")
    with pytest.raises(ValueError):
        _resolve_base_uid("::2026-06-15")
