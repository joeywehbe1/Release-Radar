"""Discord embeds and webhook delivery."""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests

from radar.models import artwork_600, clean_title, primary_artist_display

log = logging.getLogger(__name__)

USERNAME = "Release Radar"
COLORS = {"new": 0x2ECC71, "viral": 0xFF5A1F, "soon": 0x9B59B6, "info": 0x5865F2}
STOREFRONT_NAMES = {"nz": "New Zealand", "au": "Australia", "us": "the US", "gb": "the UK", "ca": "Canada"}


# ---------------------------------------------------------------- formatting helpers

def fmt_num(n: float | None) -> str:
    if n is None:
        return "?"
    for div, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= div:
            return f"{n / div:.1f}".rstrip("0").rstrip(".") + suffix
    return str(int(n))


def _trunc(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def score_bar(score: float) -> str:
    filled = max(0, min(10, round(score / 10)))
    return "▰" * filled + "▱" * (10 - filled)


def release_kind(track: dict) -> str:
    coll = track.get("collection") or ""
    count = track.get("track_count") or 1
    if coll.endswith(" - Single"):
        return "Single"
    if coll.endswith(" - EP"):
        return "EP"
    if count <= 3:
        return "Single"
    return "EP" if count <= 7 else "Album"


def _collection_name(track: dict) -> str:
    return re.sub(r"\s+-\s+(?:Single|EP)$", "", track.get("collection") or track["title"])


def listen_links(t: dict) -> str:
    query = f"{primary_artist_display(t['artist'])} {clean_title(t['title'])}"
    links = []
    if t.get("apple_url"):
        links.append(f"[Apple Music]({t['apple_url']})")
    links.append(f"[Spotify](https://open.spotify.com/search/{quote(query, safe='')})")
    yt = t.get("youtube") or {}
    links.append(f"[YouTube]({yt['url']})" if yt.get("url")
                 else f"[YouTube](https://www.youtube.com/results?search_query={quote(query, safe='')})")
    dz = t.get("deezer") or {}
    links.append(f"[Deezer]({dz['url']})" if dz.get("url")
                 else f"[Deezer](https://www.deezer.com/search/{quote(query, safe='')})")
    return " · ".join(links)


def signal_lines(t: dict) -> list[str]:
    pos = t.get("pos") or {}
    parts = t.get("parts") or {}
    climbs = parts.get("climbs") or {}

    def climb(src: str) -> str:
        c = climbs.get(src)
        return " · 🆕 new entry" if c == "new" else (f" · ▲{c}" if c else "")

    lines = []
    if "apple_top" in pos:
        lines.append(f"🍎 Apple Music US Top 100 **#{pos['apple_top']}**{climb('apple_top')}")
    genre_pos = sorted((p, s) for s, p in pos.items() if s.startswith("genre:"))
    for p, s in genre_pos[:2]:
        lines.append(f"🍎 iTunes {s.split(':', 1)[1]} chart **#{p}**{climb(s)}")
    if len(genre_pos) > 2:
        lines.append(f"🍎 …plus {len(genre_pos) - 2} more genre charts")
    yt = t.get("youtube") or {}
    if "youtube" in pos or yt.get("views"):
        bits = []
        if "youtube" in pos:
            bits.append(f"trending **#{pos['youtube']}**{climb('youtube')}")
        if yt.get("views"):
            bits.append(f"{fmt_num(yt['views'])} views")
        if parts.get("vph"):
            bits.append(f"{fmt_num(parts['vph'])}/hr")
        lines.append("▶️ YouTube " + " · ".join(bits))
    dz = t.get("deezer") or {}
    if "deezer_chart" in pos:
        lines.append(f"🎧 Deezer chart **#{pos['deezer_chart']}**{climb('deezer_chart')}")
    elif dz.get("rank"):
        lines.append(f"🎧 Deezer popularity {fmt_num(dz['rank'])}")
    rd = t.get("reddit") or {}
    if rd.get("sub"):
        lines.append(f"💬 [FRESH] post trending on [r/{rd['sub']}]({rd['url']})" if rd.get("url")
                     else f"💬 [FRESH] post trending on r/{rd['sub']}")
    return lines


# ---------------------------------------------------------------- payloads

def alert_payload(alert, now: int, cfg: dict) -> dict:
    t = alert.track
    genre = f" · {t['genre']}" if t.get("genre") else ""
    if alert.kind == "new":
        kind = release_kind(t)
        if kind == "Single":
            title, url = f"{t['artist']} — {t['title']}", t.get("apple_url")
            desc = "**Single**"
        else:
            artist = t.get("collection_artist") or primary_artist_display(t["artist"])
            title, url = f"{artist} — {_collection_name(t)}", t.get("collection_url") or t.get("apple_url")
            desc = f"**{kind}** · {t['track_count']} tracks"
            names = [f"*{x['title']}*" for x in alert.tracks[:3]]
            if names:
                desc += "\nIncludes " + " · ".join(names)
        if alert.also:
            desc += "\nAlso out: " + " · ".join(f"*{x['title']}*" for x in alert.also[:3])
        live, home = t.get("live") or {}, cfg["country"]
        early = [cc for cc in live if cc != home]
        if early and home not in live:
            where = ", ".join(STOREFRONT_NAMES.get(cc, cc.upper()) for cc in early)
            desc += f"\n🌏 Out now in {where} · reaches {STOREFRONT_NAMES.get(home, home.upper())} at midnight local time"
        author = f"🆕 NEW DROP{genre}"
    elif alert.kind == "soon":
        artist = primary_artist_display(t["artist"])
        title, url = f"{artist} — {_collection_name(t)}", t.get("collection_url")
        count = t.get("track_count") or 1
        desc = f"**{release_kind(t)}**" + (f" · {count} tracks" if count > 1 else "")
        desc += f"\nOut **<t:{t['release']}:D>** (<t:{t['release']}:R>) · just listed for pre-order"
        embed = {
            "author": {"name": _trunc(f"📅 COMING SOON{genre}", 256)},
            "title": _trunc(title, 256), "description": desc, "color": COLORS["soon"],
            "fields": [
                {"name": "Artist heat", "value": f"{round(alert.heat)}/100", "inline": True},
                {"name": "Pre-add", "value": f"[Apple Music]({url})" if url else "Apple Music", "inline": True},
            ],
            "footer": {"text": cfg.get("footer", USERNAME)}, "timestamp": _iso(now),
        }
        if url:
            embed["url"] = url
        art = artwork_600(t.get("artwork"))
        if art:
            embed["thumbnail"] = {"url": art}
        return {"username": USERNAME, "embeds": [embed], "allowed_mentions": {"parse": []}}
    else:
        title = f"{t['artist']} — {t['title']}"
        url = t.get("apple_url") or (t.get("youtube") or {}).get("url")
        desc = f"Viral score **{round(alert.score)}**/100  {score_bar(alert.score)}"
        author = f"🔥 GOING VIRAL{genre}"

    fields = [
        {"name": "Released", "value": f"<t:{t['release']}:R>", "inline": True},
        {"name": "Viral score", "value": f"{round(alert.score)}/100", "inline": True},
        {"name": "Artist heat", "value": f"{round(alert.heat)}/100", "inline": True},
    ]
    signals = signal_lines(t)
    if signals:
        fields.append({"name": "Signals", "value": _trunc("\n".join(signals), 1024), "inline": False})
    fields.append({"name": "Listen", "value": _trunc(listen_links(t), 1024), "inline": False})

    embed = {
        "author": {"name": _trunc(author, 256)},
        "title": _trunc(title, 256),
        "description": _trunc(desc, 4096),
        "color": COLORS[alert.kind],
        "fields": fields,
        "footer": {"text": cfg.get("footer", USERNAME)},
        "timestamp": _iso(now),
    }
    if url:
        embed["url"] = url
    art = artwork_600(t.get("artwork"))
    if art:
        embed["thumbnail"] = {"url": art}
    return {"username": USERNAME, "embeds": [embed], "allowed_mentions": {"parse": []}}


def online_payload(result, now: int, cfg: dict, top_n: int | None = None) -> dict:
    top_n = top_n or cfg["alerts"]["bootstrap_top_n"]
    s = result.stats
    sources = [f"**{len(cfg['genres']) + 1}** Apple Music charts", f"**{s.get('watched artists', 0)}** hot artists"]
    if s.get("deezer"):
        sources.append("Deezer")
    if s.get("youtube"):
        sources.append("YouTube trending")
    if s.get("reddit"):
        sources.append("Reddit [FRESH] posts")
    lines = [
        "Watching " + ", ".join(sources) + ".",
        "🆕 **NEW DROP**: a fresh release from an artist who's charting right now",
        "🔥 **GOING VIRAL**: a new song gaining fast momentum across platforms",
        "📅 **COMING SOON**: a hot artist's release, the moment it's listed for pre-order",
    ]
    top = result.ranked[:top_n]
    if top:
        lines += ["", "**Hottest new releases right now**"]
        for i, (score, t) in enumerate(top, start=1):
            name = f"{t['artist']} — {t['title']}"
            name = f"[{name}]({t['apple_url']})" if t.get("apple_url") else name
            lines.append(f"{i}. **{name}** · score {round(score)} · released <t:{t['release']}:R>")
    embed = {
        "title": "✅ Release Radar is online",
        "description": _trunc("\n".join(lines), 4096),
        "color": COLORS["info"],
        "footer": {"text": cfg.get("footer", USERNAME)},
        "timestamp": _iso(now),
    }
    return {"username": USERNAME, "embeds": [embed], "allowed_mentions": {"parse": []}}


def test_payload(cfg: dict) -> dict:
    embed = {
        "title": "✅ Webhook connected",
        "description": "Release Radar can post to this channel.",
        "color": COLORS["info"],
        "footer": {"text": cfg.get("footer", USERNAME)},
        "timestamp": _iso(int(time.time())),
    }
    return {"username": USERNAME, "embeds": [embed], "allowed_mentions": {"parse": []}}


# ---------------------------------------------------------------- delivery

class WebhookNotifier:
    def __init__(self, url: str, session: requests.Session) -> None:
        self._url = url
        self._session = session

    def send(self, payload: dict) -> bool:
        for _ in range(4):
            try:
                resp = self._session.post(self._url, params={"wait": "true"}, json=payload, timeout=20)
            except requests.RequestException as exc:
                # The exception text would contain the webhook URL, so only log its type.
                log.error("Discord post failed: %s", type(exc).__name__)
                return False
            if resp.status_code == 429:
                try:
                    retry_after = float(resp.json().get("retry_after", 2))
                except ValueError:
                    retry_after = 2.0
                time.sleep(min(retry_after, 30) + 0.25)
                continue
            if 200 <= resp.status_code < 300:
                return True
            log.error("Discord rejected the message: HTTP %s %s", resp.status_code, resp.text[:300])
            return False
        log.error("Discord kept rate-limiting; giving up on this message")
        return False


class PrintNotifier:
    """Dry-run stand-in: prints a readable version of each message."""

    def send(self, payload: dict) -> bool:
        print(render_text(payload))
        return True


def render_text(payload: dict) -> str:
    out = []
    for e in payload.get("embeds", []):
        out.append("─" * 70)
        if e.get("author"):
            out.append(e["author"]["name"])
        out.append(f"{e.get('title', '')}   {e.get('url', '')}".rstrip())
        if e.get("description"):
            out.append(e["description"])
        for f in e.get("fields", []):
            out.append(f"  [{f['name']}] " + f["value"].replace("\n", "\n    "))
    return "\n".join(out)
