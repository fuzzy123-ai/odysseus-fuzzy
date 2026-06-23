import time

from routes.email_pollers import _CAL_ACTION_ARRAY_RE


def _matches(s):
    return [m.group() for m in _CAL_ACTION_ARRAY_RE.finditer(s)]


def test_extracts_action_array_from_prose():
    s = 'Here:\n[{"action":"create","title":"Standup","date":"2026-07-01T09:00"}]\nDone'
    assert _matches(s) == ['[{"action":"create","title":"Standup","date":"2026-07-01T09:00"}]']


def test_extracts_multi_object_array():
    s = 'prose [{"action":"create","title":"A"},{"action":"cancel","uid":"x"}] tail'
    assert _matches(s) == ['[{"action":"create","title":"A"},{"action":"cancel","uid":"x"}]']


def test_bracket_in_string_value_still_extracts():
    s = '[{"action":"create","title":"Meeting [urgent]","date":"x"}]'
    assert _matches(s) == [s]


def test_adversarial_input_is_fast():
    evil = '[{"action"},{' + '}},{{' * 100_000
    start = time.perf_counter()
    _CAL_ACTION_ARRAY_RE.search(evil)
    assert time.perf_counter() - start < 1.0
