"""Reddit: '[FRESH]' posts in the top-of-day RSS feeds of music subreddits (a buzz signal)."""
from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET

import requests

from radar.http import TIMEOUT
from radar.models import Candidate, split_artist_title

log = logging.getLogger(__name__)

FEED = "https://www.reddit.com/r/{sub}/top/.rss"
ATOM = "{http://www.w3.org/2005/Atom}"
_FRESH = re.compile(r"^\s*\[\s*FRESH([^\]]*)\]\s*", re.I)


def fresh_posts(session: requests.Session, subreddits: list[str]) -> list[Candidate]:
    out = []
    for i, sub in enumerate(subreddits):
        if i:
            time.sleep(3)  # anonymous RSS is rate-limited
        try:
            # Plain requests.get (no retry adapter): retrying a 429 only digs the rate-limit hole deeper.
            resp = requests.get(FEED.format(sub=sub), params={"t": "day", "limit": 50}, timeout=TIMEOUT,
                                headers={"User-Agent": session.headers["User-Agent"]})
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            root = ET.fromstring(resp.content)
        except Exception as exc:
            log.warning("reddit r/%s failed: %s", sub, exc)
            continue
        for rank, entry in enumerate(root.findall(f"{ATOM}entry"), start=1):
            title = (entry.findtext(f"{ATOM}title") or "").strip()
            m = _FRESH.match(title)
            if not m:
                continue
            artist, song = split_artist_title(title[m.end():])
            if not artist:
                continue
            link = entry.find(f"{ATOM}link")
            out.append(Candidate(
                source="reddit", artist=artist, title=song, position=rank,
                extra={
                    "sub": sub, "url": link.get("href") if link is not None else None,
                    "is_release": bool(re.search(r"album|ep|mixtape", m.group(1), re.I)),
                },
            ))
    return out
