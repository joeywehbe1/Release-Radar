"""Apple Music / iTunes: US charts, artists' newest releases and songs, playability per storefront, search."""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from radar.http import Throttle, get_json
from radar.models import Candidate, parse_time, primary_artist, title_norm, titles_match

log = logging.getLogger(__name__)

MOST_PLAYED = "https://rss.marketingtools.apple.com/api/v2/{cc}/music/most-played/100/songs.json"
GENRE_CHART = "https://itunes.apple.com/{cc}/rss/topsongs/limit=100/genre={gid}/json"
LOOKUP = "https://itunes.apple.com/lookup"
SEARCH = "https://itunes.apple.com/search"
RELEASE_BATCH = 200   # artist ids per release lookup (Apple's maximum)

_ARTIST_ID = re.compile(r"/(\d+)(?:\?|$)")


def _fresh(params: dict) -> dict:
    """Apple's CDN answers repeated identical lookups from a cache that can be 24h old,
    which would hide new releases. A unique value makes every lookup a fresh one."""
    return {**params, "_": str(time.time_ns())}


def _strip_uo(url: str | None) -> str | None:
    return re.sub(r"[?&]uo=\d+$", "", url) if url else url


def album_url(url: str | None) -> str | None:
    """Collection URLs from search/lookup carry '?i=<track>'; drop it to land on the album page."""
    return url.split("?", 1)[0] if url else url


def most_played(session: requests.Session, cc: str) -> list[Candidate]:
    feed = get_json(session, MOST_PLAYED.format(cc=cc))["feed"]
    out = []
    for i, r in enumerate(feed.get("results", []), start=1):
        genres = [g["name"] for g in r.get("genres", []) if g.get("name") != "Music"]
        out.append(Candidate(
            source="apple_top", artist=r["artistName"], title=r["name"], position=i,
            release=parse_time(r.get("releaseDate")), genre=genres[0] if genres else None,
            artist_id=str(r["artistId"]) if r.get("artistId") else None, apple_id=str(r["id"]),
            url=r.get("url"), artwork=r.get("artworkUrl100"),
        ))
    return out


def genre_chart(session: requests.Session, cc: str, name: str, gid: int) -> list[Candidate]:
    feed = get_json(session, GENRE_CHART.format(cc=cc, gid=gid))["feed"]
    entries = feed.get("entry", [])
    if isinstance(entries, dict):  # a single entry isn't wrapped in a list
        entries = [entries]
    out = []
    for i, e in enumerate(entries, start=1):
        artist = e.get("im:artist", {})
        href = artist.get("attributes", {}).get("href", "")
        m = _ARTIST_ID.search(href)
        images = e.get("im:image") or []
        out.append(Candidate(
            source=f"genre:{name}", artist=artist.get("label", ""), title=e["im:name"]["label"], position=i,
            release=parse_time(e.get("im:releaseDate", {}).get("label")),
            genre=e.get("category", {}).get("attributes", {}).get("label") or name,
            artist_id=m.group(1) if m else None, apple_id=e["id"]["attributes"]["im:id"],
            url=_strip_uo(e["id"].get("label")), artwork=images[-1]["label"] if images else None,
            collection=e.get("im:collection", {}).get("im:name", {}).get("label"),
        ))
    return out


def _track_candidate(r: dict, source: str) -> Candidate:
    return Candidate(
        source=source, artist=r["artistName"], title=r["trackName"],
        release=parse_time(r.get("releaseDate")), genre=r.get("primaryGenreName"),
        artist_id=str(r["artistId"]) if r.get("artistId") else None, apple_id=str(r["trackId"]),
        url=_strip_uo(r.get("trackViewUrl")), artwork=r.get("artworkUrl100"),
        collection=r.get("collectionName"),
        collection_id=str(r["collectionId"]) if r.get("collectionId") else None,
        collection_url=album_url(r.get("collectionViewUrl")), track_count=r.get("trackCount"),
        extra={"collection_artist": r.get("collectionArtistName") or ""},
    )


def latest_songs(session: requests.Session, cc: str, artist_ids: list[str], *, batch_size: int,
                 per_artist: int, throttle: Throttle, pause: float) -> list[Candidate]:
    """Newest songs for each artist id (batched lookups). A failed batch is skipped, not fatal."""
    out = []
    for start in range(0, len(artist_ids), batch_size):
        chunk = artist_ids[start:start + batch_size]
        throttle.wait("itunes", pause)
        try:
            data = get_json(session, LOOKUP, params=_fresh({
                "id": ",".join(chunk), "entity": "song", "sort": "recent",
                "limit": per_artist, "country": cc,
            }))
        except Exception as exc:  # keep going with the other batches
            log.warning("iTunes lookup batch %d failed: %s", start // batch_size + 1, exc)
            continue
        for r in data.get("results", []):
            if r.get("wrapperType") == "track" and r.get("kind") == "song":
                out.append(_track_candidate(r, "watchlist"))
    return out


def latest_releases(session: requests.Session, cc: str, artist_ids: list[str], *,
                    throttle: Throttle, pause: float) -> list[Candidate]:
    """Each artist's 3 newest releases (albums, EPs, singles), including announced pre-orders.
    Batches run side by side to keep quick checks quick; raises only if every batch failed."""
    def lookup(chunk: list[str]) -> dict:
        throttle.wait("itunes", pause)
        return get_json(session, LOOKUP, params=_fresh({
            "id": ",".join(chunk), "entity": "album", "sort": "recent", "limit": 3, "country": cc,
        }))

    chunks = [artist_ids[i:i + RELEASE_BATCH] for i in range(0, len(artist_ids), RELEASE_BATCH)]
    out, errors = [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for future in [pool.submit(lookup, chunk) for chunk in chunks]:
            try:
                data = future.result()
            except Exception as exc:
                errors.append(exc)
                continue
            for r in data.get("results", []):
                if r.get("wrapperType") != "collection" or not r.get("collectionId"):
                    continue
                out.append(Candidate(
                    source="release", artist=r.get("artistName", ""), title=r.get("collectionName", ""),
                    release=parse_time(r.get("releaseDate")), genre=r.get("primaryGenreName"),
                    artist_id=str(r["artistId"]) if r.get("artistId") else None, artwork=r.get("artworkUrl100"),
                    collection=r.get("collectionName"), collection_id=str(r["collectionId"]),
                    collection_url=album_url(r.get("collectionViewUrl")), track_count=r.get("trackCount"),
                    extra={"collection_artist": r.get("artistName", "")},
                ))
    if errors and len(errors) == len(chunks):
        raise errors[0]
    for exc in errors:
        log.warning("iTunes release lookup batch failed: %s", exc)
    return out


def playability(session: requests.Session, cc: str, collection_ids: list[str], *,
                throttle: Throttle, pause: float) -> dict[str, dict]:
    """How much of each release can be played in storefront `cc` right now. Before release day only
    the pre-release singles are playable; once it's out, all of its tracks are.
    Returns {collection id: {"playable": n, "total": n, "titles": first track names}}."""
    out: dict[str, dict] = {}
    for start in range(0, len(collection_ids), 50):
        throttle.wait("itunes", pause)
        data = get_json(session, LOOKUP, params=_fresh({
            "id": ",".join(collection_ids[start:start + 50]), "entity": "song", "limit": 200, "country": cc,
        }))
        tracks = [r for r in data.get("results", []) if r.get("wrapperType") == "track" and r.get("kind") == "song"]
        for r in sorted(tracks, key=lambda r: (r.get("discNumber") or 1, r.get("trackNumber") or 0)):
            s = out.setdefault(str(r["collectionId"]), {"playable": 0, "total": 0, "titles": []})
            s["total"] += 1
            s["playable"] += bool(r.get("isStreamable"))
            if len(s["titles"]) < 5:
                s["titles"].append(r.get("trackName", ""))
    return out


def release_dates(session: requests.Session, cc: str, track_ids: list[str], *,
                  throttle: Throttle, pause: float) -> dict[str, int]:
    """Apple track id -> release time, for chart entries that came without a date."""
    found: dict[str, int] = {}
    for start in range(0, len(track_ids), 150):
        throttle.wait("itunes", pause)
        data = get_json(session, LOOKUP, params=_fresh({"id": ",".join(track_ids[start:start + 150]), "country": cc}))
        for r in data.get("results", []):
            ts = parse_time(r.get("releaseDate"))
            if r.get("trackId") and ts:
                found[str(r["trackId"])] = ts
    return found


def search_song(session: requests.Session, cc: str, artist: str, title: str, *,
                throttle: Throttle, pause: float) -> Candidate | None:
    """Find a song on Apple Music by artist + title (for songs first seen on YouTube/Deezer)."""
    throttle.wait("itunes", pause)
    data = get_json(session, SEARCH, params=_fresh({
        "term": f"{artist} {title}", "media": "music", "entity": "song", "limit": 10, "country": cc,
    }))
    want_artist, want_title = primary_artist(artist), title_norm(title)
    for r in data.get("results", []):
        if r.get("kind") != "song":
            continue
        if primary_artist(r.get("artistName", "")) == want_artist and titles_match(title_norm(r.get("trackName", "")), want_title):
            return _track_candidate(r, "apple_search")
    return None
