"""A polite, cached client for Leaguepedia's Cargo API.

Leaguepedia (lol.fandom.com) keeps its esports data in "Cargo" tables that you
query with an SQL-like API: tables, fields, where, join_on, order_by. It's free
and anonymous, but it rate-limits hard. Ten quick requests is enough to get
blocked for a while. So this client:

1. **Throttles**: waits at least `min_interval_s` between requests.
2. **Backs off**: on a "ratelimited" error it waits 30s, 60s, 120s... and retries.
0. **Logs in** with a bot password. Fandom rate-limits anonymous
   Cargo queries so hard that a single query can be refused after waiting 25 minutes
   (we hit this). Logged-in requests with a confirmed email get usable limits.
3. **Caches every page to disk**: each query result is stored as JSON keyed by a
   hash of its parameters. Re-running a script costs zero requests, and the raw
   data you trained or evaluated on is preserved exactly. That makes the work
   reproducible, which evals depend on.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx

API_URL = "https://lol.fandom.com/api.php"
USER_AGENT = "rift-rumors/0.1 (personal esports research project)"
PAGE_SIZE = 500  # the maximum Cargo returns per request for anonymous users


class LeaguepediaError(RuntimeError):
    pass


class CargoClient:
    def __init__(
        self,
        cache_dir: Path,
        *,
        min_interval_s: float = 3.0,
        max_retries: int = 6,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = print,
        username: str | None = None,
        bot_password: str | None = None,
    ):
        self.cache_dir = cache_dir
        self.min_interval_s = min_interval_s
        self.max_retries = max_retries
        self.http = http or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30)
        self.sleep = sleep
        self.log = log
        self.username = username
        self.bot_password = bot_password
        self._logged_in = False
        self._last_request = 0.0
        self.requests_made = 0

    def login(self) -> None:
        """Log in with a bot password (MediaWiki API:Login, two steps).

        Fandom rate-limits anonymous Cargo queries so hard they're unusable, so
        logged-in requests are the only practical way in. The session cookie stays
        in `self.http`, so later requests are authenticated automatically.
        """
        if self._logged_in or not (self.username and self.bot_password):
            return
        self._throttle()
        token_resp = self.http.get(
            API_URL, params={"action": "query", "meta": "tokens", "type": "login", "format": "json"}
        )
        token = token_resp.json()["query"]["tokens"]["logintoken"]

        self._throttle()
        result = self.http.post(
            API_URL,
            data={
                "action": "login",
                "lgname": self.username,
                "lgpassword": self.bot_password,
                "lgtoken": token,
                "format": "json",
            },
        ).json()["login"]
        if result.get("result") != "Success":
            raise LeaguepediaError(
                f"Leaguepedia login failed ({result.get('result')}): {result.get('reason', '')}. "
                "LEAGUEPEDIA_USERNAME must look like 'YourAccount@BotName'."
            )
        self._logged_in = True
        self.log(f"Leaguepedia: logged in as {self.username}")

    def query(
        self,
        *,
        tables: str,
        fields: str,
        where: str | None = None,
        join_on: str | None = None,
        order_by: str | None = None,
        max_age_s: float | None = None,
    ) -> list[dict]:
        """Run a Cargo query and return every row, following pagination.

        `max_age_s=None` means cached pages never expire, which is right for
        history. For data that's still changing (this offseason), pass a max age
        so the query re-fetches.

        Alias every field to a snake_case name (`"T.Date_Sort=date"`). Cargo
        otherwise returns keys with spaces, which makes them awkward to use.
        """
        params = {
            "action": "cargoquery",
            "format": "json",
            "tables": tables,
            "fields": fields,
            "limit": str(PAGE_SIZE),
        }
        for key, value in (("where", where), ("join_on", join_on), ("order_by", order_by)):
            if value:
                params[key] = value

        rows: list[dict] = []
        offset = 0
        while True:
            page = self._page({**params, "offset": str(offset)}, max_age_s)
            rows.extend(page)
            if len(page) < PAGE_SIZE:
                return rows
            offset += PAGE_SIZE

    # --- internals ----------------------------------------------------------

    def _cache_path(self, params: dict) -> Path:
        key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:20]
        return self.cache_dir / f"{key}.json"

    def _page(self, params: dict, max_age_s: float | None) -> list[dict]:
        path = self._cache_path(params)
        if path.exists():
            cached = json.loads(path.read_text())
            age = time.time() - cached["fetched_at_epoch"]
            if max_age_s is None or age < max_age_s:
                return cached["rows"]

        data = self._request(params)
        rows = [item["title"] for item in data.get("cargoquery", [])]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "params": params,
                    "fetched_at": datetime.now(UTC).isoformat(),
                    "fetched_at_epoch": time.time(),
                    "rows": rows,
                },
                indent=1,
            )
        )
        return rows

    def _throttle(self) -> None:
        wait = self.min_interval_s - (time.monotonic() - self._last_request)
        if wait > 0:
            self.sleep(wait)
        self._last_request = time.monotonic()

    def _request(self, params: dict) -> dict:
        self.login()  # only reached on a cache miss, so cached runs never log in
        for attempt in range(self.max_retries):
            self._throttle()
            self.requests_made += 1
            try:
                response = self.http.get(API_URL, params=params)
            except httpx.TransportError as e:
                self._backoff(attempt, f"network error ({e.__class__.__name__})")
                continue

            if response.status_code == 429 or response.status_code >= 500:
                self._backoff(attempt, f"HTTP {response.status_code}")
                continue
            response.raise_for_status()

            data = response.json()
            error = data.get("error")
            if error is None:
                return data
            if error.get("code") == "ratelimited":
                self._backoff(attempt, "rate limited")
                continue
            raise LeaguepediaError(f"{error.get('code')}: {error.get('info')}")

        hint = (
            ""
            if self._logged_in
            else " Anonymous Cargo queries are effectively blocked: set LEAGUEPEDIA_USERNAME "
            "and LEAGUEPEDIA_BOT_PASSWORD in .env (see .env.example)."
        )
        raise LeaguepediaError(
            f"Gave up after {self.max_retries} attempts (still rate limited?).{hint}"
        )

    def _backoff(self, attempt: int, reason: str) -> None:
        wait = min(30 * 2**attempt, 600)
        self.log(f"Leaguepedia: {reason}; waiting {wait}s (attempt {attempt + 1})")
        self.sleep(wait)
