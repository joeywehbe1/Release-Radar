"""One radar cycle: collect -> merge -> enrich -> snapshot -> score -> decide. Delivery is a separate step."""
from __future__ import annotations

import logging
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import requests

from radar import notify, scoring
from radar.http import Throttle, make_session
from radar.models import (
    SOURCE_PRIORITY, Candidate, clean_title, compile_patterns, matches_any, primary_artist,
    primary_artist_display, title_norm, titles_match,
)
from radar.sources import apple, deezer, reddit, youtube
from radar.state import is_bootstrap

log = logging.getLogger(__name__)

HOUR = 3600
DAY = 86400
SNAPSHOT_EVERY = 3 * HOUR
APPLE_SEARCHES_PER_RUN = 10


class LiveFetchers:
    """Talks to the real services. Tests pass a fake object with the same methods."""

    def __init__(self, cfg: dict, session: requests.Session | None = None) -> None:
        self.cfg = cfg
        self.session = session or make_session()
        self.throttle = Throttle()
        self.cc = cfg["country"]
        self.pause = cfg["watchlist"]["itunes_pause_seconds"]

    def charts(self) -> list[Candidate]:
        out: list[Candidate] = []
        try:
            out += apple.most_played(self.session, self.cc)
        except Exception as exc:
            log.warning("Apple Music top 100 failed: %s", exc)
        for name, gid in self.cfg["genres"].items():
            self.throttle.wait("apple_rss", 0.3)
            try:
                out += apple.genre_chart(self.session, self.cc, name, gid)
            except Exception as exc:
                log.warning("iTunes %s chart failed: %s", name, exc)
        return out

    def watchlist(self, artist_ids: list[str]) -> list[Candidate]:
        w = self.cfg["watchlist"]
        return apple.latest_songs(self.session, self.cc, artist_ids, batch_size=w["batch_size"],
                                  per_artist=w["per_artist_limit"], throttle=self.throttle, pause=self.pause)

    def apple_release_dates(self, track_ids: list[str]) -> dict[str, int]:
        return apple.release_dates(self.session, self.cc, track_ids, throttle=self.throttle, pause=self.pause)

    def apple_search(self, artist: str, title: str) -> Candidate | None:
        return apple.search_song(self.session, self.cc, artist, title, throttle=self.throttle, pause=self.pause)

    def deezer_chart(self) -> list[Candidate]:
        return deezer.chart(self.session, self.throttle)

    def deezer_release(self, deezer_id: str) -> int | None:
        return deezer.track_release(self.session, self.throttle, deezer_id)

    def deezer_find(self, artist: str, title: str) -> dict | None:
        return deezer.find(self.session, self.throttle, artist, title)

    def youtube(self) -> list[Candidate]:
        if not self.cfg.get("youtube_key"):
            log.info("YouTube skipped: no YOUTUBE_API_KEY set")
            return []
        return youtube.trending_music(self.session, self.cfg["youtube_key"], self.cc, self.cfg["youtube"]["max_results"])

    def reddit(self) -> list[Candidate]:
        return reddit.fresh_posts(self.session, self.cfg["reddit"]["subreddits"])


@dataclass
class Alert:
    kind: str                 # "new" (NEW DROP) or "viral" (GOING VIRAL)
    key: str                  # "rel:<collection id>" for new drops, the song key for viral
    score: float
    heat: float
    track: dict               # the lead song
    tracks: list[dict] = field(default_factory=list)  # every song in a new release
    also: list[dict] = field(default_factory=list)    # lead songs of the artist's other same-day releases
    release_keys: list[str] = field(default_factory=list)


@dataclass
class CycleResult:
    bootstrap: bool
    alerts: list[Alert]                  # what to post now (after caps)
    eligible: list[Alert]                # everything that qualified (before caps)
    ranked: list[tuple[float, dict]]     # recent songs, best score first
    stats: dict


# ---------------------------------------------------------------- the cycle

def run_cycle(cfg: dict, state: dict, fetchers, now: int) -> CycleResult:
    stats: dict[str, int] = {}
    window = cfg["alerts"]["viral_max_age_days"] * DAY
    manual_heat = cfg["watchlist"]["manual_artist_heat"]

    def collect(name, fn, *args) -> list[Candidate]:
        try:
            cands = fn(*args)
        except Exception as exc:  # one dead source never breaks the run
            log.warning("%s failed: %s", name, exc)
            cands = []
        stats[name] = len(cands)
        log.info("%-9s %4d entries", name, len(cands))
        return cands

    charts = collect("charts", fetchers.charts)
    _fill_apple_dates(state, charts, fetchers, now)
    scoring.update_artist_heat(state, charts, now, manual_heat)
    _sync_manual_artists(state, cfg["manual_artists"], now)
    watch_ids = _hot_artist_ids(state, now, cfg)
    stats["watched artists"] = len(watch_ids)
    watch = collect("watchlist", fetchers.watchlist, watch_ids)
    dz = collect("deezer", fetchers.deezer_chart)
    _fill_deezer_dates(state, dz, fetchers, now, cfg)
    yt = collect("youtube", fetchers.youtube)
    rd = collect("reddit", fetchers.reddit)

    for t in state["tracks"].values():
        t["pos"] = {}  # positions are re-read every run
    junk = compile_patterns(cfg["filters"]["exclude_title"])
    bad_artists = {a.lower() for a in cfg["filters"]["exclude_artists"]}
    index = _build_index(state)
    for c in charts + watch + dz + yt:
        _ingest(state, index, c, now, window, junk, bad_artists)
    for c in rd:
        _ingest_reddit(index, c, now)

    _enrich_apple(state, fetchers, now, window)
    _enrich_deezer(state, fetchers, now, window, cfg)
    _snapshot(state, now)

    history_ok = state["created"] is not None and now - state["created"] >= scoring.CLIMB_LOOKBACK
    ranked = _score_all(state, now, window, history_ok, cfg, manual_heat)
    eligible = _decide(state, ranked, now, cfg)
    return CycleResult(is_bootstrap(state), _apply_caps(state, eligible, now, cfg), eligible, ranked, stats)


def deliver(result: CycleResult, state: dict, notifier, now: int, cfg: dict) -> bool:
    """Post alerts and record them. The first run only posts an 'online' summary."""
    if result.bootstrap:
        for a in result.eligible:  # already-out songs shouldn't flood the channel
            _mark(state, a, now)
        ok = notifier.send(notify.online_payload(result, now, cfg))
        if ok:
            state["created"] = now
        return ok
    all_ok = True
    for i, a in enumerate(result.alerts):
        if i:
            time.sleep(1)
        if notifier.send(notify.alert_payload(a, now, cfg)):
            _mark(state, a, now)
            state["sent"].append([now, a.kind, a.key, a.track["anorm"]])
        else:
            all_ok = False
    return all_ok


# ---------------------------------------------------------------- collection helpers

def _fill_apple_dates(state: dict, cands: list[Candidate], fetchers, now: int) -> None:
    """Some genre-chart entries have no release date; look those up by track id (cached)."""
    cache = state["dates"]
    missing = []
    for c in cands:
        if c.release is None and c.apple_id:
            hit = cache.get(f"apple:{c.apple_id}")
            if hit is not None:
                c.release = hit[0]
            else:
                missing.append(c.apple_id)
    missing = list(dict.fromkeys(missing))[:300]
    if not missing:
        return
    try:
        found = fetchers.apple_release_dates(missing)
    except Exception as exc:
        log.warning("Apple release-date lookup failed: %s", exc)
        return
    for tid in missing:
        cache[f"apple:{tid}"] = [found.get(tid), now]
    for c in cands:
        if c.release is None and c.apple_id in found:
            c.release = found[c.apple_id]


def _fill_deezer_dates(state: dict, cands: list[Candidate], fetchers, now: int, cfg: dict) -> None:
    """Deezer's chart has no release dates; fetch them for songs we don't already know (cached)."""
    cache = state["dates"]
    budget = cfg["deezer"]["date_lookups_per_run"]
    for c in cands:
        key = f"deezer:{c.extra.get('deezer_id')}"
        if key in cache:
            c.release = cache[key][0]
            continue
        if budget <= 0 or f"{primary_artist(c.artist)}|{title_norm(c.title)}" in state["tracks"]:
            continue
        budget -= 1
        try:
            c.release = fetchers.deezer_release(c.extra["deezer_id"])
        except Exception as exc:
            log.warning("Deezer release date failed: %s", exc)
            return
        cache[key] = [c.release, now]


def _sync_manual_artists(state: dict, manual_ids: list[str], now: int) -> None:
    manual = set(manual_ids)
    for aid, a in state["artists"].items():
        a["manual"] = aid in manual
    for aid in manual:
        state["artists"].setdefault(aid, {"name": None, "heat": 0.0, "heat_ts": now, "last_charted": None, "manual": True})


def _hot_artist_ids(state: dict, now: int, cfg: dict) -> list[str]:
    mh = cfg["watchlist"]["manual_artist_heat"]
    ranked = sorted(state["artists"].items(), key=lambda kv: scoring.effective_heat(kv[1], now, mh), reverse=True)
    manual = [aid for aid, a in ranked if a.get("manual")]
    others = [aid for aid, a in ranked if not a.get("manual")]
    return manual + others[:max(0, cfg["watchlist"]["max_artists"] - len(manual))]


# ---------------------------------------------------------------- merging

def _build_index(state: dict) -> dict[str, list[dict]]:
    index: dict[str, list[dict]] = defaultdict(list)
    for t in state["tracks"].values():
        index[t["anorm"]].append(t)
    return index


def _find(index: dict, anorm: str, tnorm: str) -> dict | None:
    for t in index.get(anorm, ()):
        if titles_match(t["tnorm"], tnorm):
            return t
    return None


def _ingest(state: dict, index: dict, c: Candidate, now: int, window: int, junk, bad_artists: set) -> None:
    if matches_any(c.title, junk) or c.artist.strip().lower() in bad_artists \
            or c.extra.get("collection_artist", "").lower() in bad_artists:
        return
    anorm, tnorm = primary_artist(c.artist), title_norm(c.title)
    if not anorm or not tnorm:
        return
    track = _find(index, anorm, tnorm)
    if c.release is not None and now - c.release > window:
        # Apple says it's an older song: make sure a YouTube/Deezer copy isn't treated as new.
        if track is not None and SOURCE_PRIORITY.get(c.kind, 0) >= 3:
            track["old"] = True
        return
    if c.release is not None and c.release > now + HOUR:
        return  # not out yet
    if track is None:
        if c.release is None:
            return  # can't prove it's new
        key = f"{anorm}|{tnorm}"
        track = state["tracks"][key] = {
            "key": key, "anorm": anorm, "tnorm": tnorm, "first_seen": now, "release": None,
            "pos": {}, "hist": [], "alerted": {},
        }
        index[anorm].append(track)
    _merge(track, c, now)


def _merge(track: dict, c: Candidate, now: int) -> None:
    prio = SOURCE_PRIORITY.get(c.kind, 0)
    if prio > track.get("prio", -1):
        track["artist"] = c.artist
        track["title"] = c.title if prio >= 3 else clean_title(c.title)
        track["prio"] = prio
    if c.release is not None and (prio > track.get("rprio", -1) or (prio == track.get("rprio") and c.release < track["release"])):
        track["release"], track["rprio"] = c.release, prio
    if prio >= 3:
        for name in ("artist_id", "apple_id", "genre", "collection", "collection_id", "collection_url", "track_count"):
            if getattr(c, name):
                track[name] = getattr(c, name)
        if c.url:
            track["apple_url"] = c.url
        if c.extra.get("collection_artist"):
            track["collection_artist"] = c.extra["collection_artist"]
    if c.artwork and (prio >= 3 or not track.get("artwork")):
        track["artwork"] = c.artwork
    if c.position is not None and c.kind in ("apple_top", "genre", "deezer_chart", "youtube"):
        prev = track["pos"].get(c.source)
        track["pos"][c.source] = min(prev, c.position) if prev else c.position
    if c.kind == "watchlist":
        track["wl_seen"] = now
    elif c.kind == "deezer_chart":
        track["deezer"] = {"id": c.extra["deezer_id"], "url": c.extra.get("deezer_url"),
                           "rank": c.extra.get("deezer_rank"), "ts": now}
    elif c.kind == "youtube":
        old = track.get("youtube") or {}
        views = c.extra.get("views") or 0
        # Several videos can match one song (official, lyric...): keep the most-watched.
        if old.get("ts") != now or views >= (old.get("views") or 0):
            track["youtube"] = {"id": c.extra["video_id"], "url": c.extra["yt_url"], "views": c.extra.get("views"),
                                "published": c.extra.get("published"), "ts": now}
    track["last_seen"] = now


def _ingest_reddit(index: dict, c: Candidate, now: int) -> None:
    """[FRESH] posts only boost songs we already know (a single, or every song of a matching release)."""
    anorm, tnorm = primary_artist(c.artist), title_norm(c.title)
    targets = []
    track = _find(index, anorm, tnorm)
    if track is not None:
        targets = [track]
    elif c.extra.get("is_release"):
        targets = [t for t in index.get(anorm, ()) if t.get("collection") and titles_match(title_norm(t["collection"]), tnorm)]
    for t in targets:
        t["reddit"] = {"sub": c.extra["sub"], "url": c.extra.get("url"), "ts": now}


def _enrich_apple(state: dict, fetchers, now: int, window: int) -> None:
    """Songs first seen on YouTube/Deezer: find them on Apple Music for links, art and the real release date."""
    todo = [t for t in state["tracks"].values()
            if t["last_seen"] == now and not t.get("apple_id") and not t.get("old")
            and now - t.get("apple_checked", 0) > DAY]
    todo.sort(key=lambda t: -((t.get("youtube") or {}).get("views") or 0))
    for t in todo[:APPLE_SEARCHES_PER_RUN]:
        t["apple_checked"] = now
        try:
            c = fetchers.apple_search(primary_artist_display(t["artist"]), clean_title(t["title"]))
        except Exception as exc:
            log.warning("Apple search failed: %s", exc)
            return
        if c is None:
            continue
        if c.release and now - c.release > window:
            t["old"] = True
            continue
        _merge(t, c, now)


def _enrich_deezer(state: dict, fetchers, now: int, window: int, cfg: dict) -> None:
    """Deezer popularity rank for recent songs (refreshed every 6h, never-checked songs first)."""
    todo = [t for t in state["tracks"].values()
            if t["last_seen"] == now and t.get("release") and now - t["release"] <= window and not t.get("old")
            and now - (t.get("deezer") or {}).get("ts", 0) > 6 * HOUR]
    todo.sort(key=lambda t: (t.get("deezer") is not None, -len(t["pos"])))
    for t in todo[:cfg["deezer"]["rank_lookups_per_run"]]:
        try:
            found = fetchers.deezer_find(primary_artist_display(t["artist"]), clean_title(t["title"]))
        except Exception as exc:
            log.warning("Deezer search failed: %s", exc)
            return
        t["deezer"] = {**(found or {}), "ts": now}


def _snapshot(state: dict, now: int) -> None:
    """History for momentum: [time, positions, youtube views], when positions change or every 3h."""
    for t in state["tracks"].values():
        if t["last_seen"] != now or t.get("old"):
            continue
        yt = t.get("youtube") or {}
        views = yt.get("views") if yt.get("ts") == now else None
        hist = t.setdefault("hist", [])
        if not hist or hist[-1][1] != t["pos"] or now - hist[-1][0] >= SNAPSHOT_EVERY:
            hist.append([now, dict(t["pos"]), views])


# ---------------------------------------------------------------- scoring and decisions

def _score_all(state: dict, now: int, window: int, history_ok: bool, cfg: dict, manual_heat: float) -> list[tuple[float, dict]]:
    heat_by_name = {}
    for a in state["artists"].values():
        if a.get("name"):
            heat_by_name[primary_artist(a["name"])] = scoring.effective_heat(a, now, manual_heat)
    ranked = []
    for t in state["tracks"].values():
        if t["last_seen"] != now or t.get("old") or not t.get("release") or now - t["release"] > window:
            continue
        artist = state["artists"].get(t.get("artist_id") or "")
        heat = scoring.effective_heat(artist, now, manual_heat) if artist else heat_by_name.get(t["anorm"], 0.0)
        t["heat"] = round(heat, 1)
        t["score"], t["parts"] = scoring.score_track(t, heat, now, history_ok, cfg)
        ranked.append((t["score"], t))
    ranked.sort(key=lambda x: x[0], reverse=True)
    return ranked


def _decide(state: dict, ranked: list[tuple[float, dict]], now: int, cfg: dict) -> list[Alert]:
    al = cfg["alerts"]
    new_drop_junk = compile_patterns(cfg["filters"]["exclude_new_drop_title"])
    groups: dict[str, list[dict]] = defaultdict(list)
    for _, t in ranked:
        if t.get("wl_seen") != now or t["alerted"].get("new"):
            continue
        if now - t["release"] > al["new_drop_max_age_hours"] * HOUR or t["heat"] < al["new_drop_min_heat"]:
            continue
        if matches_any(t["title"], new_drop_junk):
            continue
        gkey = f"rel:{t.get('collection_id') or t['key']}"
        if gkey not in state["releases"]:
            groups[gkey].append(t)
    # One NEW DROP per artist per run: the biggest release leads, other same-day releases ride along.
    by_artist: dict[str, list[tuple[str, list[dict]]]] = defaultdict(list)
    for gkey, ts in groups.items():
        by_artist[ts[0].get("artist_id") or ts[0]["anorm"]].append((gkey, ts))
    alerts = []
    for releases in by_artist.values():
        releases.sort(key=lambda r: ((r[1][0].get("track_count") or 1), r[1][0]["score"]), reverse=True)
        lead = releases[0][1][0]
        extras = [r[1][0] for r in releases[1:]]
        alerts.append(Alert("new", releases[0][0], lead["score"], lead["heat"], lead, releases[0][1],
                            also=extras, release_keys=[g for g, _ in releases]))

    in_new_drop = {t["key"] for a in alerts for t in a.tracks + a.also}
    for score, t in ranked:
        if score < al["viral_threshold"]:
            break
        if t["alerted"].get("viral") or t["key"] in in_new_drop or not t["parts"]["moving"]:
            continue
        new_at = t["alerted"].get("new")
        if new_at and now - new_at < al["viral_after_new_drop_hours"] * HOUR:
            continue
        alerts.append(Alert("viral", t["key"], score, t["heat"], t, [t]))
    return alerts


def _apply_caps(state: dict, eligible: list[Alert], now: int, cfg: dict) -> list[Alert]:
    al = cfg["alerts"]
    recent = [s for s in state["sent"] if now - s[0] < DAY]
    left = {
        "new": al["max_new_drops_per_day"] - sum(1 for s in recent if s[1] == "new"),
        "viral": al["max_viral_per_day"] - sum(1 for s in recent if s[1] == "viral"),
    }
    per_artist = Counter(s[3] for s in recent if s[1] == "viral" and len(s) > 3)
    out = []
    for a in sorted((a for a in eligible if a.kind == "viral"), key=lambda a: a.score, reverse=True):
        if left["viral"] <= 0:
            break
        if per_artist[a.track["anorm"]] >= al["max_viral_per_artist_per_day"]:
            continue
        per_artist[a.track["anorm"]] += 1
        left["viral"] -= 1
        out.append(a)
    for a in sorted((a for a in eligible if a.kind == "new"), key=lambda a: (a.heat, a.score), reverse=True):
        if left["new"] <= 0:
            break
        left["new"] -= 1
        out.append(a)
    return out[:al["max_per_run"]]


def _mark(state: dict, a: Alert, now: int) -> None:
    if a.kind == "new":
        for key in a.release_keys or [a.key]:
            state["releases"][key] = now
        for t in a.tracks + a.also:
            t["alerted"]["new"] = now
    else:
        a.track["alerted"]["viral"] = now
