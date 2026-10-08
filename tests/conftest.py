import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from radar.config import load_config  # noqa: E402
from radar.models import Candidate  # noqa: E402

T0 = 1_791_000_000  # a fixed "now" for deterministic tests
HOUR = 3600
DAY = 86400


@pytest.fixture
def cfg():
    c = copy.deepcopy(load_config())
    c["manual_artists"] = []
    c["youtube_key"] = ""
    return c


def chart(artist, title, pos, *, source="apple_top", artist_id="1", release=T0 - DAY, apple_id=None):
    return Candidate(source=source, artist=artist, title=title, position=pos, release=release,
                     artist_id=artist_id, apple_id=apple_id or f"{artist_id}-{title}",
                     url=f"https://music.apple.com/us/song/{artist_id}", genre="Pop",
                     artwork="https://is1-ssl.mzstatic.com/x/100x100bb.jpg")


def song(artist, title, *, artist_id="1", release=T0 - 2 * HOUR, collection=None, collection_id=None, track_count=1):
    return Candidate(source="watchlist", artist=artist, title=title, release=release, artist_id=artist_id,
                     apple_id=f"{artist_id}-{title}", url=f"https://music.apple.com/us/song/{title}",
                     genre="Pop", collection=collection or f"{title} - Single",
                     collection_id=collection_id or f"c-{artist_id}-{title}",
                     collection_url="https://music.apple.com/us/album/x", track_count=track_count,
                     extra={"collection_artist": artist})


def release(artist, title, *, artist_id="1", release=T0 - 2 * HOUR, collection_id=None, track_count=1):
    """An artist's release as Apple's release lookup returns it (album, EP or '<title> - Single')."""
    cid = collection_id or f"c-{artist_id}-{title}"
    return Candidate(source="release", artist=artist, title=title, release=release, artist_id=artist_id,
                     genre="Pop", collection=title, collection_id=cid, collection_url=f"https://music.apple.com/us/album/{cid}",
                     track_count=track_count, artwork="https://is1-ssl.mzstatic.com/x/100x100bb.jpg",
                     extra={"collection_artist": artist})


def out(playable, total, titles=("Track 1", "Track 2", "Track 3")):
    """Playability of one release in one storefront."""
    return {"playable": playable, "total": total, "titles": list(titles)[:5]}


def video(artist, title, views, *, published=T0 - DAY, pos=1):
    return Candidate(source="youtube", artist=artist, title=title, position=pos, release=published,
                     extra={"video_id": f"v-{title}", "yt_url": f"https://www.youtube.com/watch?v={title}",
                            "views": views, "published": published, "raw_title": f"{artist} - {title}"})


class FakeFetchers:
    """Same interface as pipeline.LiveFetchers; each field is a callable returning fresh candidates."""

    def __init__(self, charts=(), watch=(), dz=(), yt=(), rd=(), deezer_ranks=None, apple_search=None,
                 releases=(), status=None):
        self._charts, self._watch, self._dz, self._yt, self._rd = charts, watch, dz, yt, rd
        self._releases = releases
        self._status = status  # {storefront: {collection id: out(...)}}; default: everything is out in the US
        self.deezer_ranks = deezer_ranks or {}
        self.searches = apple_search or {}
        self.calls = []

    def charts(self):
        self.calls.append("charts")
        return [copy.copy(c) for c in self._charts]

    def releases(self, artist_ids):
        self.calls.append("releases")
        return [copy.copy(c) for c in self._releases if c.artist_id in artist_ids]

    def playability(self, collection_ids):
        self.calls.append("playability")
        if self._status is None:
            counts = {c.collection_id: c.track_count or 1 for c in self._releases}
            return {"us": {cid: out(counts[cid], counts[cid]) for cid in collection_ids if cid in counts}}
        return {cc: {cid: s for cid, s in by_id.items() if cid in collection_ids} for cc, by_id in self._status.items()}

    def watchlist(self, artist_ids):
        return [copy.copy(c) for c in self._watch if c.artist_id in artist_ids]

    def apple_release_dates(self, ids):
        return {}

    def apple_search(self, artist, title):
        return copy.copy(self.searches.get((artist, title)))

    def deezer_chart(self):
        return [copy.copy(c) for c in self._dz]

    def deezer_release(self, deezer_id):
        return None

    def deezer_find(self, artist, title):
        rank = self.deezer_ranks.get((artist, title))
        return {"id": "dz", "url": "https://www.deezer.com/track/1", "rank": rank} if rank else None

    def youtube(self):
        return [copy.copy(c) for c in self._yt]

    def reddit(self):
        return [copy.copy(c) for c in self._rd]


class RecordingNotifier:
    def __init__(self, ok=True):
        self.payloads = []
        self.ok = ok

    def send(self, payload):
        self.payloads.append(payload)
        return self.ok
