"""Release windows: most music goes live at local midnight, in New Zealand first and the US ~17h later."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def window_end(now: float, bursts: dict) -> float | None:
    """If `now` falls inside a release window (from `lead_minutes` before local midnight in any of
    `zones` to `tail_minutes` after, longer when that midnight starts a Friday), return when the
    window ends (epoch seconds); otherwise None. Daylight saving is handled by the time zone."""
    t = datetime.fromtimestamp(now, timezone.utc)
    ends = []
    for zone in bursts["zones"]:
        local = t.astimezone(ZoneInfo(zone))
        for days in (0, 1):  # the midnight that started today, and the coming one
            day = (local + timedelta(days=days)).date()
            midnight = datetime(day.year, day.month, day.day, tzinfo=ZoneInfo(zone))
            tail = bursts["friday_tail_minutes"] if day.weekday() == 4 else bursts["tail_minutes"]
            start = midnight - timedelta(minutes=bursts["lead_minutes"])
            end = midnight + timedelta(minutes=tail)
            if start <= t <= end:
                ends.append(end.timestamp())
    return max(ends) if ends else None
