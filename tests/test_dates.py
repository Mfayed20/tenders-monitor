from datetime import datetime, timedelta

from utils.dates import KSA_TZ, is_closing_soon, is_new_tender, parse_date


def test_parse_ambiguous_numeric_date_returns_none():
    assert parse_date("04/05/2026") is None


def test_parse_iso_date_still_works():
    parsed = parse_date("2026-04-05")

    assert parsed is not None
    assert parsed.strftime("%Y-%m-%d") == "2026-04-05"


def test_parse_dayfirst_pm_time_preserves_afternoon_hour():
    parsed = parse_date("14/06/2026 02:30 PM")

    assert parsed is not None
    assert parsed.hour == 14
    assert parsed.minute == 30


def test_future_publish_date_is_not_new():
    future_date = datetime.now(KSA_TZ) + timedelta(days=1)

    assert is_new_tender(future_date, hours=168) is False


def test_old_publish_date_is_rejected():
    old_date = datetime.now(KSA_TZ) - timedelta(days=10)

    assert is_new_tender(old_date, hours=168) is False


def test_missing_dates_remain_conservative():
    assert is_new_tender(None) is True
    assert is_closing_soon(None) is True


def test_parse_iso_datetime_is_never_read_day_first():
    # KSATendersGate's API gives "2026-10-11T10:46:23"; read day-first it became 10 November
    # and the tender was dropped as "published in the future".
    parsed = parse_date("2026-10-11T10:46:23")
    assert (parsed.year, parsed.month, parsed.day, parsed.hour) == (2026, 10, 11, 10)
    assert parse_date("2026-10-11T10:46:23Z").utcoffset().total_seconds() == 0
