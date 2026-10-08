"""Shared HTTP session with retries, plus a simple per-service throttle."""
from __future__ import annotations

import threading
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
    """Keeps at least `interval` seconds between the starts of calls that share a name
    (thread-safe, so parallel lookups still respect a service's rate limit)."""

    def __init__(self) -> None:
        self._next: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, name: str, interval: float) -> None:
        with self._lock:
            now = time.monotonic()
            start = max(now, self._next.get(name, now))
            self._next[name] = start + interval
        if start > now:
            time.sleep(start - now)


def get_json(session: requests.Session, url: str, **kwargs) -> dict:
    """GET and decode JSON; raises RuntimeError with the status (never the full URL) on failure."""
    resp = session.get(url, timeout=TIMEOUT, **kwargs)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} from {resp.url.split('?')[0]}")
    return resp.json()
