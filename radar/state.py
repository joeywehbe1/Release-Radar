"""Persistent state: tracked songs, artist heat, what was already alerted. Stored as one JSON file."""
from __future__ import annotations

import json
import os
from pathlib import Path

DAY = 86400
HISTORY_DAYS = 3        # chart snapshots kept per song (momentum looks back 12-24h)
UNSEEN_DAYS = 3         # forget songs no source has reported for this long
CACHE_DAYS = 30


def empty() -> dict:
    return {
        "version": 1,
        "created": None,      # set after the first (bootstrap) run is delivered
        "last_run": None,
        "tracks": {},         # key -> song record
        "artists": {},        # Apple artist id -> {name, heat, heat_ts, last_charted, manual}
        "releases": {},       # "rel:<collection id>" -> time a NEW DROP was posted
        "sent": [],           # [time, kind, key] for daily caps
        "dates": {},          # "apple:<id>" / "deezer:<id>" -> [release time, cached at]
    }


def load(path: Path) -> dict:
    if not path.is_file() or path.stat().st_size == 0:
        return empty()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for key, value in empty().items():
        data.setdefault(key, value)
    return data


def save(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, separators=(",", ":"), ensure_ascii=False)
    os.replace(tmp, path)


def is_bootstrap(state: dict) -> bool:
    return state.get("created") is None


def prune(state: dict, now: int, cfg: dict) -> None:
    window = cfg["alerts"]["viral_max_age_days"] * DAY
    tracks = state["tracks"]
    for key in list(tracks):
        t = tracks[key]
        release = t.get("release")
        if now - t["last_seen"] > UNSEEN_DAYS * DAY or (release and now - release > window + 2 * DAY and not t.get("old")):
            del tracks[key]
            continue
        # Songs known to be old are kept (without history) so they aren't re-checked every run.
        t["hist"] = [] if t.get("old") else [h for h in t.get("hist", []) if now - h[0] <= HISTORY_DAYS * DAY]

    keep_artists = cfg["watchlist"]["hot_artist_days"] * DAY
    for aid in list(state["artists"]):
        a = state["artists"][aid]
        if not a.get("manual") and now - (a.get("last_charted") or 0) > keep_artists:
            del state["artists"][aid]

    state["releases"] = {k: v for k, v in state["releases"].items() if now - v <= CACHE_DAYS * DAY}
    state["sent"] = [s for s in state["sent"] if now - s[0] <= 2 * DAY]
    state["dates"] = {k: v for k, v in state["dates"].items() if now - v[1] <= CACHE_DAYS * DAY}
