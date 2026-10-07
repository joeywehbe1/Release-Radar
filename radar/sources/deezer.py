"""Deezer: chart positions and per-track popularity rank (0 - ~1,000,000). No key needed."""
from __future__ import annotations

import requests

from radar.http import Throttle, get_json
from radar.models import Candidate, parse_time, primary_artist, title_norm, titles_match

CHART = "https://api.deezer.com/chart/0/tracks"
SEARCH = "https://api.deezer.com/search"
TRACK = "https://api.deezer.com/track/{id}"
PAUSE = 0.15  # Deezer allows 50 requests / 5 seconds


def _get(session: requests.Session, url: str, throttle: Throttle, **kwargs) -> dict:
    throttle.wait("deezer", PAUSE)
    data = get_json(session, url, **kwargs)
    if isinstance(data, dict) and "error" in data:  # Deezer reports errors with HTTP 200
        raise RuntimeError(f"Deezer error: {data['error'].get('message', data['error'])}")
    return data


def chart(session: requests.Session, throttle: Throttle) -> list[Candidate]:
    data = _get(session, CHART, throttle, params={"limit": 100})
    out = []
    for i, t in enumerate(data.get("data", []), start=1):
        out.append(Candidate(
            source="deezer_chart", artist=t["artist"]["name"], title=t["title"], position=i,
            artwork=(t.get("album") or {}).get("cover_xl"),
            extra={"deezer_id": str(t["id"]), "deezer_url": t.get("link"), "deezer_rank": t.get("rank")},
        ))
    return out


def track_release(session: requests.Session, throttle: Throttle, deezer_id: str) -> int | None:
    return parse_time(_get(session, TRACK.format(id=deezer_id), throttle).get("release_date"))


def find(session: requests.Session, throttle: Throttle, artist: str, title: str) -> dict | None:
    """Best Deezer match for a song: {'id', 'url', 'rank'} or None."""
    # Deezer's advanced syntax (artist:"x" track:"y") now returns nothing; plain text works
    # and the artist/title check below rejects wrong hits.
    data = _get(session, SEARCH, throttle, params={"q": f"{artist} {title}", "limit": 10})
    want_artist, want_title = primary_artist(artist), title_norm(title)
    for t in data.get("data", []):
        if primary_artist(t["artist"]["name"]) == want_artist and titles_match(title_norm(t["title"]), want_title):
            return {"id": str(t["id"]), "url": t.get("link"), "rank": t.get("rank")}
    return None
