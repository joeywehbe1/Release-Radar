"""Loads config.toml, the optional artists.txt watchlist, and secrets from the environment."""
from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_WEBHOOK_RE = re.compile(
    r"^https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/api/(?:v\d+/)?webhooks/\d+/[\w-]+$"
)


def load_dotenv(path: Path) -> None:
    """Minimal .env reader: KEY=value lines; real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def load_manual_artists(path: Path) -> list[str]:
    """Apple artist ids from artists.txt (one per line, '#' starts a comment)."""
    if not path.is_file():
        return []
    ids = []
    for line in path.read_text(encoding="utf-8").splitlines():
        token = line.split("#", 1)[0].strip()
        if token.isdigit():
            ids.append(token)
    return ids


def load_config(path: Path | None = None) -> dict:
    with open(path or ROOT / "config.toml", "rb") as f:
        cfg = tomllib.load(f)
    load_dotenv(ROOT / ".env")
    cfg["manual_artists"] = load_manual_artists(ROOT / "artists.txt")
    cfg["youtube_key"] = os.environ.get("YOUTUBE_API_KEY", "").strip()
    return cfg


def webhook_url() -> str:
    """The Discord webhook URL from the environment. Raises if missing or malformed."""
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not url:
        raise SystemExit("DISCORD_WEBHOOK_URL is not set (add it to .env or your GitHub secrets).")
    if not _WEBHOOK_RE.match(url):
        raise SystemExit("DISCORD_WEBHOOK_URL doesn't look like a Discord webhook URL.")
    return url
