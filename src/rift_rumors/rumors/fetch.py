"""A small cached, throttled HTTP fetcher for feeds (same ideas as the Leaguepedia client).

- Every response body is saved under `cache_dir`, keyed by a hash of the URL.
- `max_age_s=None` means never re-fetch (right for date-bounded historical
  searches). Live searches pass a max age so they refresh.
- 429/5xx responses back off exponentially instead of hammering the server.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx

USER_AGENT = "rift-rumors/0.1 (personal esports research project)"


class FetchError(RuntimeError):
    pass


class CachedFetcher:
    def __init__(
        self,
        cache_dir: Path,
        *,
        min_interval_s: float = 4.0,
        max_retries: int = 5,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = print,
    ):
        self.cache_dir = cache_dir
        self.min_interval_s = min_interval_s
        self.max_retries = max_retries
        self.http = http or httpx.Client(
            headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True
        )
        self.sleep = sleep
        self.log = log
        self._last = 0.0
        self.requests_made = 0

    def get(self, url: str, *, max_age_s: float | None = None) -> tuple[str, str]:
        """Return (body, fetched_at ISO). Served from cache when fresh enough."""
        path = self.cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:24] + ".json")
        if path.exists():
            cached = json.loads(path.read_text())
            if max_age_s is None or time.time() - cached["fetched_at_epoch"] < max_age_s:
                return cached["body"], cached["fetched_at"]

        body = self._request(url)
        fetched_at = datetime.now(UTC).isoformat(timespec="seconds")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "url": url,
                    "fetched_at": fetched_at,
                    "fetched_at_epoch": time.time(),
                    "body": body,
                }
            )
        )
        return body, fetched_at

    def _request(self, url: str) -> str:
        for attempt in range(self.max_retries):
            wait = self.min_interval_s - (time.monotonic() - self._last)
            if wait > 0:
                self.sleep(wait)
            self._last = time.monotonic()
            self.requests_made += 1
            try:
                response = self.http.get(url)
            except httpx.TransportError as e:
                self._backoff(attempt, e.__class__.__name__)
                continue
            if response.status_code == 429 or response.status_code >= 500:
                self._backoff(attempt, f"HTTP {response.status_code}")
                continue
            if response.status_code != 200:
                raise FetchError(f"HTTP {response.status_code} for {url}")
            return response.text
        raise FetchError(f"Gave up after {self.max_retries} attempts: {url}")

    def _backoff(self, attempt: int, reason: str) -> None:
        wait = min(15 * 2**attempt, 300)
        self.log(f"feed fetch: {reason}; waiting {wait}s (attempt {attempt + 1})")
        self.sleep(wait)
