import pytest

from app import normalize as nz


@pytest.mark.parametrize("text", ["", None, "TBA", "To be announced", "Rolling", "N/A", "-", "not yet announced",
                                  "sometime next spring", "31 March", "32 Jan 2027", "15 Feb 2027 25:99"])
def test_unknown_deadline_is_never_converted_to_a_date(text):
    d = nz.parse_deadline(text)
    assert d.due_at is None and d.timezone is None


def test_named_abbreviation_timezone():
    d = nz.parse_deadline("31 March 2027, 23:59 IST")
    assert d.due_at == "2027-03-31T18:29:00Z"
    assert d.timezone == "Asia/Kolkata" and not d.tz_assumed and not d.time_assumed


def test_iso_with_offset():
    d = nz.parse_deadline("2027-03-31T23:59:00+05:30")
    assert d.due_at == "2027-03-31T18:29:00Z"
    assert d.timezone == "UTC+05:30"


def test_utc_offset_word_form():
    assert nz.parse_deadline("15 Apr 2027, 18:00 UTC+01:00").due_at == "2027-04-15T17:00:00Z"


def test_iana_zone_and_pm_clock():
    d = nz.parse_deadline("Dec 5, 2026 11:59 PM Pacific/Auckland")
    assert d.timezone == "Pacific/Auckland"
    assert d.due_at == "2026-12-05T10:59:00Z"      # NZDT is UTC+13


def test_est_pm():
    assert nz.parse_deadline("Jan 10, 2027 11:59 PM EST").due_at == "2027-01-11T04:59:00Z"


def test_date_only_uses_source_timezone_and_is_flagged():
    d = nz.parse_deadline("10 Nov 2026", default_tz="Asia/Kolkata")
    assert d.tz_assumed and d.time_assumed
    assert d.due_at == "2026-11-10T18:29:00Z"        # 23:59 IST


def test_numeric_day_first_date():
    assert nz.parse_deadline("05/11/2026 12:00 CET").due_at == "2026-11-05T11:00:00Z"


def test_format_local_roundtrip():
    assert nz.format_local("2027-03-31T18:29:00Z", "Asia/Kolkata") == "31 Mar 2027, 23:59 Asia/Kolkata"
    assert nz.format_local(None, "UTC") is None


def test_level_category_keywords():
    assert nz.normalize_level("Master's and PhD students") == "Postgraduate, Doctoral"
    assert nz.normalize_level("Undergraduate") == "Undergraduate"
    assert nz.normalize_level(None) == "Unspecified"
    assert nz.normalize_category("Merit") == "Merit"
    assert nz.normalize_category(None, "household income below 4 lakh") == "Need-based"
    assert "citizenship" in nz.eligibility_keywords("Indian citizens only")


def test_withdrawn_detection():
    assert nz.is_withdrawn("Withdrawn")
    assert nz.is_withdrawn("Open", "badge cancelled")
    assert not nz.is_withdrawn("Open", None)


def test_hash_depends_on_content_not_formatting():
    assert nz.content_hash({"a": 1, "b": 2}) == nz.content_hash({"b": 2, "a": 1})
    assert nz.content_hash({"a": 1}) != nz.content_hash({"a": 2})
