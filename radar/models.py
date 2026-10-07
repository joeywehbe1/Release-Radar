"""Candidate records from sources, plus the text normalization used to match songs across platforms."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Candidate:
    source: str                     # "apple_top", "genre:<name>", "watchlist", "deezer_chart", "youtube", "reddit"
    artist: str
    title: str
    position: int | None = None     # chart position (1 = top), if the source is a chart
    release: int | None = None      # release time, epoch seconds
    genre: str | None = None
    artist_id: str | None = None    # Apple artist id
    apple_id: str | None = None     # Apple track id
    url: str | None = None          # Apple Music track URL
    artwork: str | None = None
    collection: str | None = None
    collection_id: str | None = None
    collection_url: str | None = None
    track_count: int | None = None
    extra: dict = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return self.source.split(":", 1)[0]


# Name/title priority when sources disagree: Apple's metadata is the cleanest.
SOURCE_PRIORITY = {"apple_top": 3, "genre": 3, "watchlist": 3, "apple_search": 3, "deezer_chart": 2, "youtube": 1, "reddit": 0}

_FEAT_PAREN = re.compile(r"\s*[(\[]\s*(?:feat\.?|ft\.?|featuring|with)\s+[^)\]]*[)\]]", re.I)
_FEAT_TAIL = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+.*$", re.I)
_TAGS = re.compile(
    r"\s*[(\[{][^)\]}]*\b(?:official|oficial|officiel|lyrics?|letra|visuali[sz]er|audio|video|videoclip|clip"
    r"|explicit|clean|mv|m/v|hd|4k)\b[^)\]}]*[)\]}]",
    re.I,
)
_HANDLES = re.compile(r"\s*@[\w.]+")                          # "@ArtistOficial" mentions in video titles
_INVISIBLE = re.compile("[‎‏‪-‮⁦-⁩]")  # bidi marks YouTube titles carry
_SUFFIX = re.compile(r"\s+-\s+(?:single|ep)$", re.I)
_ARTIST_SPLIT = re.compile(
    r"\s*(?:,|&|\+|/|\bx\b|\bfeat\.?|\bft\.?|\bfeaturing\b|\bwith\b|\bvs\.?)\s*", re.I
)
_DASH = re.compile(r"\s+[-–—]\s+")
_CHANNEL_SUFFIX = re.compile(r"(?:\s*-\s*topic|vevo|\s+official)$", re.I)
_VARIANT_WORDS = {"remix", "mix", "edit", "version", "acoustic", "live", "sped", "slowed", "instrumental", "demo", "remaster", "remastered"}


def fold(text: str) -> str:
    """Lowercase, strip accents, and reduce to space-separated word characters."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    text = text.replace("&", " and ")
    return " ".join(re.sub(r"[^\w]+", " ", text).split())


def clean_title(title: str) -> str:
    """Drop featured-artist credits, video/explicit tags and '- Single' suffixes."""
    t = _HANDLES.sub("", _INVISIBLE.sub("", title)).split(" | ")[0]
    t = _FEAT_PAREN.sub("", t)
    t = _TAGS.sub("", t)
    t = _SUFFIX.sub("", t)
    t = _FEAT_TAIL.sub("", t)
    return t.strip() or title.strip()


def primary_artist_display(artist: str) -> str:
    return _ARTIST_SPLIT.split(artist.strip(), maxsplit=1)[0].strip() or artist.strip()


def primary_artist(artist: str) -> str:
    return fold(primary_artist_display(artist))


def title_norm(title: str) -> str:
    return fold(clean_title(title))


def make_key(artist: str, title: str) -> str:
    return f"{primary_artist(artist)}|{title_norm(title)}"


def titles_match(a: str, b: str) -> bool:
    """Normalized titles are the same song: equal, or one contains the other as whole words
    (e.g. 'dracula' vs 'dracula feat jennie'), as long as both are the same variant."""
    if a == b:
        return True
    if min(len(a), len(b)) < 4:
        return False
    if _VARIANT_WORDS & set(a.split()) != _VARIANT_WORDS & set(b.split()):
        return False
    return f" {a} " in f" {b} " or f" {b} " in f" {a} "


def split_artist_title(text: str, fallback_artist: str = "") -> tuple[str, str]:
    """'Artist - Title (Official Video)' -> ('Artist', 'Title (Official Video)')."""
    parts = _DASH.split(text.strip(), maxsplit=1)
    if len(parts) == 2 and parts[0] and parts[1]:
        return parts[0].strip(), parts[1].strip()
    return fallback_artist, text.strip()


def clean_channel(channel: str) -> str:
    return _CHANNEL_SUFFIX.sub("", channel.strip()).strip()


def compile_patterns(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p, re.I) for p in patterns]


def matches_any(text: str, patterns: list[re.Pattern]) -> bool:
    return any(p.search(text) for p in patterns)


def parse_time(value: str | None) -> int | None:
    """ISO date or datetime ('2026-10-02', '2026-10-02T07:00:00Z', '...-07:00') -> epoch seconds."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def artwork_600(url: str | None) -> str | None:
    """Apple artwork URLs end in e.g. '/100x100bb.jpg'; ask for a 600px version instead."""
    if not url:
        return None
    return re.sub(r"/\d+x\d+[a-z]*(?:-\d+)?\.(?:jpg|png|webp)$", "/600x600bb.jpg", url)
