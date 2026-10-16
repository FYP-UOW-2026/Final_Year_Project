"""Labels built from server data must read as dates, not raw Firestore objects."""

import datetime

from bioaudit.gui.formatting import format_when, severity_summary


def test_firestore_timestamp_becomes_a_readable_date():
    seconds = 1791880978
    expected = datetime.datetime.fromtimestamp(seconds).strftime("%d %b %Y, %H:%M")
    assert format_when({"_seconds": seconds, "_nanoseconds": 239000000}) == expected


def test_iso_string_is_accepted_too():
    # The same moment as an ISO string, so a later server-side fix keeps working.
    assert format_when("2026-10-13T08:42:58Z") == format_when({"_seconds": 1791880978})


def test_missing_or_unknown_values_do_not_leak_raw_data():
    assert format_when(None) == "unknown time"
    assert format_when({}) == "unknown time"
    assert "_seconds" not in format_when({"_nanoseconds": 5})


def test_severity_counts_read_as_words():
    assert severity_summary({"high": 2, "low": 1, "medium": 0}) == "2 High, 1 Low"
    assert severity_summary({}) == "no findings"
