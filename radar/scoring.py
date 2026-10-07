"""Artist heat and the 0-100 'viral potential' score."""
from __future__ import annotations

import math
from collections import defaultdict

from radar.models import Candidate, primary_artist_display

HOUR = 3600
DAY = 86400
HEAT_HALF_LIFE_DAYS = 7
HEAT_CHART_WEIGHT = {"apple_top": 3.0, "genre": 1.0}
MOMENTUM_CHART_WEIGHT = {"apple_top": 1.0, "genre": 0.7, "deezer_chart": 0.7, "youtube": 0.8}
CLIMB_LOOKBACK = 12 * HOUR
MIN_CLIMB = 3          # smaller moves are chart noise
MOVING_CLIMB = 10      # a climb this big (or a new chart entry) counts as real momentum


def _kind(source: str) -> str:
    return source.split(":", 1)[0]


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def _chart_points(position: int) -> float:
    """#1 -> 1.0, #100 -> 0.01."""
    return max(0.0, 1 - (position - 1) / 100)


# ---------------------------------------------------------------- artist heat

def effective_heat(artist: dict, now: int, manual_heat: float) -> float:
    """Stored heat decays with a 7-day half-life; artists from artists.txt never drop below manual_heat."""
    heat = artist.get("heat", 0.0) * 0.5 ** ((now - artist.get("heat_ts", now)) / (HEAT_HALF_LIFE_DAYS * DAY))
    return max(heat, manual_heat) if artist.get("manual") else heat


def _heat_points(position: int) -> float:
    """#1 -> 1.0, #100 -> 0.4: being anywhere on a top-100 chart means something."""
    return 1 - 0.6 * (min(position, 100) - 1) / 99


def update_artist_heat(state: dict, chart_cands: list[Candidate], now: int, manual_heat: float) -> None:
    """Heat 0-100 from the Apple charts an artist is on (best position per chart; the
    streaming top 100 counts 3x a genre chart). One #1 genre hit ~ 39, Apple top 100 ~ 45-78."""
    best: dict[tuple[str, str], int] = {}
    names: dict[str, str] = {}
    for c in chart_cands:
        if c.kind not in HEAT_CHART_WEIGHT or not c.artist_id or c.position is None:
            continue
        slot = (c.artist_id, c.source)
        best[slot] = min(best.get(slot, c.position), c.position)
        names.setdefault(c.artist_id, primary_artist_display(c.artist))
    raw: dict[str, float] = defaultdict(float)
    for (aid, source), position in best.items():
        raw[aid] += HEAT_CHART_WEIGHT[_kind(source)] * _heat_points(position)
    for aid, r in raw.items():
        heat = 100 * (1 - math.exp(-r / 2))
        a = state["artists"].setdefault(aid, {"heat": 0.0, "heat_ts": now})
        if heat >= effective_heat({**a, "manual": False}, now, manual_heat):
            a["heat"], a["heat_ts"] = round(heat, 1), now
        a["name"] = names[aid]
        a["last_charted"] = now


# ---------------------------------------------------------------- score parts

def momentum(track: dict, now: int, history_ok: bool) -> tuple[float, dict]:
    """Chart position, breadth (how many charts) and climb vs ~12h ago. Returns (0-1, climbs)."""
    pos = track.get("pos") or {}
    if not pos:
        return 0.0, {}
    best = max(MOMENTUM_CHART_WEIGHT.get(_kind(s), 0.5) * _chart_points(p) for s, p in pos.items())
    breadth = min(len(pos) / 4, 1.0)
    climbs: dict[str, int | str] = {}
    if history_ok:
        ref = None
        for h in track.get("hist", []):
            if h[0] <= now - CLIMB_LOOKBACK:
                ref = h
        prev = ref[1] if ref else {}
        for s, p in pos.items():
            if s not in prev:
                climbs[s] = "new"
            elif prev[s] - p >= MIN_CLIMB:
                climbs[s] = prev[s] - p
    gains = [(101 - pos[s]) if g == "new" else g for s, g in climbs.items()]
    climb = min(max(gains, default=0) / 50, 1.0)
    return 0.4 * best + 0.3 * climb + 0.3 * breadth, climbs


def youtube_velocity(track: dict, now: int) -> tuple[float, float | None]:
    """Views per hour (recent delta if we have one, else lifetime average), log-scaled:
    1K/hr -> 0, 10K/hr -> 0.5, 100K/hr -> 1."""
    yt = track.get("youtube") or {}
    views = yt.get("views")
    if views is None or now - yt.get("ts", 0) > 6 * HOUR:
        return 0.0, None
    vph = None
    ref = None
    for h in track.get("hist", []):
        if len(h) > 2 and h[2] is not None and now - DAY <= h[0] <= now - HOUR:
            ref = h
            break  # oldest snapshot inside the window gives the steadiest rate
    if ref and views >= ref[2]:
        vph = (views - ref[2]) / ((now - ref[0]) / HOUR)
    elif yt.get("published"):
        vph = views / max((now - yt["published"]) / HOUR, 1.0)
    if vph is None:
        return 0.0, None
    return _clip((math.log10(max(vph, 1.0)) - 3) / 2), vph


def cross_platform(track: dict) -> float:
    """On Apple charts, Deezer (chart or high rank) and YouTube trending: 1 platform -> 0, all 3 -> 1."""
    pos = track.get("pos") or {}
    n = 0.0
    if any(_kind(s) in ("apple_top", "genre") for s in pos):
        n += 1
    if "deezer_chart" in pos:
        n += 1
    elif (track.get("deezer") or {}).get("rank"):
        n += _clip((track["deezer"]["rank"] - 300_000) / 600_000)
    if "youtube" in pos:
        n += 1
    return _clip((n - 1) / 2)


def reddit_buzz(track: dict, now: int) -> float:
    rd = track.get("reddit") or {}
    return 1.0 if rd and now - rd.get("ts", 0) <= 36 * HOUR else 0.0


def recency(release: int, now: int, window_days: float) -> float:
    """Full weight for 3 days after release, sliding to 0.5 at the end of the window."""
    age_h = max(0.0, (now - release) / HOUR)
    if age_h <= 72:
        return 1.0
    span = max(window_days * 24 - 72, 1)
    return max(0.5, 1 - 0.5 * (age_h - 72) / span)


def score_track(track: dict, heat: float, now: int, history_ok: bool, cfg: dict) -> tuple[float, dict]:
    """Viral-potential score 0-100 plus the parts that produced it (for embeds and tuning)."""
    w = cfg["weights"]
    mom, climbs = momentum(track, now, history_ok)
    yt, vph = youtube_velocity(track, now)
    parts = {
        "artist_heat": _clip(heat / 100),
        "chart_momentum": mom,
        "youtube": yt,
        "cross_platform": cross_platform(track),
        "reddit": reddit_buzz(track, now),
    }
    total_w = sum(w[k] for k in parts) or 1
    raw = sum(w[k] * v for k, v in parts.items()) / total_w
    score = 100 * raw * recency(track["release"], now, cfg["alerts"]["viral_max_age_days"])
    detail = {k: round(v, 3) for k, v in parts.items()}
    detail["climbs"] = climbs
    detail["vph"] = round(vph) if vph is not None else None
    # "Going viral" needs acceleration, not just a high, static chart spot.
    detail["moving"] = (any(g == "new" or g >= MOVING_CLIMB for g in climbs.values())
                        or parts["youtube"] >= 0.5 or parts["reddit"] > 0)
    return round(score, 1), detail
