"""YouTube Data API: the trending music chart (videos.list chart=mostPopular, category 10 = Music)."""
from __future__ import annotations

import requests

from radar.http import TIMEOUT
from radar.models import Candidate, clean_channel, clean_title, parse_time, primary_artist, split_artist_title

VIDEOS = "https://www.googleapis.com/youtube/v3/videos"


def trending_music(session: requests.Session, api_key: str, region: str, max_results: int) -> list[Candidate]:
    out: list[Candidate] = []
    page_token = None
    # The key goes in a header, so it can never leak into logs via a URL.
    headers = {"X-Goog-Api-Key": api_key}
    while len(out) < max_results:
        params = {
            "part": "snippet,statistics", "chart": "mostPopular", "videoCategoryId": "10",
            "regionCode": region.upper(), "maxResults": min(50, max_results - len(out)),
        }
        if page_token:
            params["pageToken"] = page_token
        resp = session.get(VIDEOS, params=params, headers=headers, timeout=TIMEOUT)
        if resp.status_code != 200:
            try:
                reason = resp.json()["error"]["message"]
            except Exception:
                reason = resp.reason
            raise RuntimeError(f"YouTube API HTTP {resp.status_code}: {reason}")
        data = resp.json()
        for item in data.get("items", []):
            snip, stats = item["snippet"], item.get("statistics", {})
            channel = clean_channel(snip.get("channelTitle", ""))
            artist, title = split_artist_title(snip["title"], fallback_artist=channel)
            if channel and primary_artist(clean_title(title)) == primary_artist(channel) != primary_artist(artist):
                artist, title = channel, artist  # "Song - Artist" order, e.g. on the artist's own channel
            views = stats.get("viewCount")
            out.append(Candidate(
                source="youtube", artist=artist, title=clean_title(title), position=len(out) + 1,
                release=parse_time(snip.get("publishedAt")),
                extra={
                    "video_id": item["id"], "yt_url": f"https://www.youtube.com/watch?v={item['id']}",
                    "views": int(views) if views is not None else None,
                    "published": parse_time(snip.get("publishedAt")), "raw_title": snip["title"],
                },
            ))
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return out
