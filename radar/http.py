"""Shared HTTP session with retries, plus a simple per-service throttle."""
from __future__ import annotations

import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = "ReleaseRadar/1.0 (Discord new-music alerts)"
TIMEOUT = 25


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    retry = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


class Throttle:
    """Keeps at least `interval` seconds between calls that share a name."""

    def __init__(self) -> None:
        self._last: dict[str, float] = {}

    def wait(self, name: str, interval: float) -> None:
        last = self._last.get(name)
        if last is not None:
            delay = interval - (time.monotonic() - last)
            if delay > 0:
                time.sleep(delay)
        self._last[name] = time.monotonic()


def get_json(session: requests.Session, url: str, **kwargs) -> dict:
    """GET and decode JSON; raises RuntimeError with the status (never the full URL) on failure."""
    resp = session.get(url, timeout=TIMEOUT, **kwargs)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} from {resp.url.split('?')[0]}")
    return resp.json()
