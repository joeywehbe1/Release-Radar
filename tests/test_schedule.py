import time

from radar.models import parse_time
from radar.schedule import window_end

BURSTS = {"zones": ["Pacific/Auckland", "America/New_York"], "lead_minutes": 50,
          "tail_minutes": 40, "friday_tail_minutes": 100}


def end_at(utc: str):
    end = window_end(parse_time(utc), BURSTS)
    return None if end is None else time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(end))


def test_new_zealand_midnight_in_daylight_time():
    # NZDT (UTC+13): Friday Oct 9 starts at 11:00 UTC on Thursday Oct 8 -> long Friday window
    assert end_at("2026-10-08T10:20:00Z") == "2026-10-08T12:40Z"
    assert end_at("2026-10-08T12:30:00Z") == "2026-10-08T12:40Z"
    assert end_at("2026-10-08T12:45:00Z") is None


def test_us_midnight_follows_daylight_saving():
    # EDT (UTC-4): Friday Oct 9 starts 04:00 UTC
    assert end_at("2026-10-09T03:30:00Z") == "2026-10-09T05:40Z"
    # EST (UTC-5) in December: a Tuesday midnight at 05:00 UTC -> normal 40-minute tail
    assert end_at("2026-12-01T04:30:00Z") == "2026-12-01T05:40Z"
    assert end_at("2026-12-01T03:59:00Z") is None


def test_quiet_hours_have_no_window():
    assert end_at("2026-10-08T18:00:00Z") is None
    assert end_at("2026-10-08T00:00:00Z") is None
