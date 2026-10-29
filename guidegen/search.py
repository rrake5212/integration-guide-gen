"""Swappable web-search fallback (gate B/D, approved 2026-10-29).

Search is only ever used for the FIND residual — providers that the free ladder
(llms.txt, sitemap, nav, well-known, path guesses) could not resolve — and its results
pass through the same ranker and the same content verification as every other candidate.
It is never a fact source and never the first rung.

Two adapters, chosen by env so the provider can be swapped without code changes:

    SEARCH_PROVIDER=serper  SERPER_API_KEY=...     Google-backed SERP
    SEARCH_PROVIDER=brave   BRAVE_API_KEY=...      Brave's own index

Prices checked on the vendors' own pages, 2026-10-29:
    Serper  2,500 free queries, no card; prepaid packs 50k @ $1.00/1k (50 qps),
            500k @ $0.75/1k (100 qps), 2.5M @ $0.50/1k (200 qps), 12.5M @ $0.30/1k.
            Credits valid 6 months.
    Brave   $5.00 per 1,000 requests, $5 free credits every month (~1,000 requests),
            50 requests/second on the Search plan.
A single provider resolution costs one query and is then cached in surfaces.json, so a
few hundred providers costs well under a dollar on either.

Spend guards (2026-10-29): every adapter returned by ``from_env`` is wrapped in
``CachedSearch`` — an identical query inside 7 days costs nothing, and a daily cap
(``SEARCH_DAILY_BUDGET``, default 200) stops the process before a loop or a test run can
eat the free plan. ``SEARCH_CACHE=off`` bypasses both for debugging.

No key => NullSearch, and the finder simply stops after the free rungs. Nothing here
raises on a search failure: a dead search backend degrades the fallback, not the run.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Protocol

USER_AGENT = "oauth-guide-gen/0.1 (surface finder)"
SERPER_ENDPOINT = "https://google.serper.dev/search"
SEARCHAPI_ENDPOINT = "https://www.searchapi.io/api/v1/search"
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
TIMEOUT = 12.0

# Search costs money per call: 2,500 free Serper queries, then ~$1/1k. Two guards live here so
# testing can never eat the budget a real user needs: a 7-day result cache and a daily cap.
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CACHE_DIR = DATA_DIR / "cache" / "search"
BUDGET_PATH = DATA_DIR / "cache" / "search-budget.json"
CACHE_TTL = 7 * 24 * 3600
DEFAULT_DAILY_BUDGET = 200


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str = ""

    def to_dict(self) -> dict:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, limit: int = 5) -> list[SearchResult]: ...


class NullSearch:
    """The default. No key, no call, no dependency."""

    name = "null"
    last_error: str | None = None

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:  # noqa: ARG002
        return []


def _http(method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float):
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(2_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except Exception:  # noqa: BLE001 - a search failure must not break the run
        return 0, ""


class SerperSearch:
    name = "serper"

    def __init__(self, api_key: str, http: Callable[..., tuple[int, str]] = _http) -> None:
        if not api_key:
            raise ValueError("SerperSearch needs an API key")
        self._key = api_key
        self._http = http
        self.last_error: str | None = None

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        payload = json.dumps({"q": query, "num": max(1, min(limit, 10))}).encode()
        status, text = self._http(
            "POST", SERPER_ENDPOINT,
            {"X-API-KEY": self._key, "Content-Type": "application/json", "User-Agent": USER_AGENT},
            payload, TIMEOUT,
        )
        if status != 200 or not text:
            self.last_error = f"serper HTTP {status}"
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            self.last_error = "serper returned non-JSON"
            return []
        out = []
        for row in (data.get("organic") or [])[:limit]:
            if row.get("link"):
                out.append(SearchResult(title=row.get("title") or "", url=row["link"], snippet=row.get("snippet") or ""))
        return out


class SearchApiSearch:
    """searchapi.io — Google results, ``api_key`` query param, ``organic_results`` payload.

    Prices read from the vendor's pricing page 2026-10-29: Developer $40/mo for 10,000
    searches ($4/1k), Production $100/mo for 35,000 ($3/1k), down to $1/1k at 5M. Searches
    are billed per successful call, and the provider's dashboard shows the balance.
    """

    name = "searchapi"

    def __init__(self, api_key: str, http: Callable[..., tuple[int, str]] = _http) -> None:
        if not api_key:
            raise ValueError("SearchApiSearch needs an API key")
        self._key = api_key
        self._http = http
        self.last_error: str | None = None

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        params = urllib.parse.urlencode({
            "engine": "google",
            "q": query,
            "num": max(1, min(limit, 10)),
            "api_key": self._key,
        })
        status, text = self._http(
            "GET", f"{SEARCHAPI_ENDPOINT}?{params}",
            {"Accept": "application/json", "User-Agent": USER_AGENT}, None, TIMEOUT,
        )
        if status != 200 or not text:
            self.last_error = f"searchapi HTTP {status}"
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            self.last_error = "searchapi returned non-JSON"
            return []
        out = []
        for row in (data.get("organic_results") or [])[:limit]:
            if row.get("link"):
                out.append(SearchResult(title=row.get("title") or "", url=row["link"], snippet=row.get("snippet") or ""))
        return out


class BraveSearch:
    name = "brave"

    def __init__(self, api_key: str, http: Callable[..., tuple[int, str]] = _http) -> None:
        if not api_key:
            raise ValueError("BraveSearch needs an API key")
        self._key = api_key
        self._http = http
        self.last_error: str | None = None

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        params = urllib.parse.urlencode({"q": query, "count": max(1, min(limit, 20))})
        status, text = self._http(
            "GET", f"{BRAVE_ENDPOINT}?{params}",
            {"X-Subscription-Token": self._key, "Accept": "application/json", "User-Agent": USER_AGENT},
            None, TIMEOUT,
        )
        if status != 200 or not text:
            self.last_error = f"brave HTTP {status}"
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            self.last_error = "brave returned non-JSON"
            return []
        rows = ((data.get("web") or {}).get("results")) or []
        return [
            SearchResult(title=row.get("title") or "", url=row.get("url") or "", snippet=row.get("description") or "")
            for row in rows[:limit] if row.get("url")
        ]


class CachedSearch:
    """Reuse a query's results for a week and stop at a daily call cap.

    The free Serper plan is 2,500 queries — a spend worth protecting. Two rules enforce it:
    an identical query inside ``ttl`` costs nothing, and every ATTEMPT (hit or miss) is
    counted against ``daily_cap`` so a broken key cannot burn the month's budget in a loop.
    The cap is deliberately global (one process-wide counter file), because the FIND layer
    is the only caller: one query per unresolved provider, and surfaces.json caches the
    answer forever after.
    """

    def __init__(
        self,
        inner: SearchProvider,
        *,
        cache_dir: Path | None = None,
        budget_path: Path | None = None,
        ttl: float = CACHE_TTL,
        daily_cap: int | None = None,
    ) -> None:
        self._inner = inner
        self.name = inner.name
        self.last_error: str | None = None
        self._cache_dir = cache_dir or CACHE_DIR
        self._budget_path = budget_path or BUDGET_PATH
        self._ttl = ttl
        self._daily_cap = DEFAULT_DAILY_BUDGET if daily_cap is None else daily_cap

    # ------------------------------------------------------------------ budget
    def _budget(self) -> dict:
        today = date.today().isoformat()
        try:
            data = json.loads(self._budget_path.read_text())
        except Exception:  # noqa: BLE001
            data = {}
        if data.get("date") != today:
            data = {"date": today, "used": 0}
        return data

    def _spend(self) -> None:
        data = self._budget()
        data["used"] = int(data.get("used") or 0) + 1
        try:
            self._budget_path.parent.mkdir(parents=True, exist_ok=True)
            self._budget_path.write_text(json.dumps(data))
        except Exception:  # noqa: BLE001 - the counter must never break a run
            pass

    @property
    def used_today(self) -> int:
        return int(self._budget().get("used") or 0)

    @property
    def remaining(self) -> int:
        return max(0, self._daily_cap - self.used_today)

    # ------------------------------------------------------------------- cache
    def _cache_path(self, query: str, limit: int) -> Path:
        digest = hashlib.sha256(f"{self.name}|{limit}|{query}".encode()).hexdigest()[:32]
        return self._cache_dir / f"{digest}.json"

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        path = self._cache_path(query, limit)
        try:
            cached = json.loads(path.read_text())
            if time.time() - float(cached.get("at") or 0) <= self._ttl:
                return [SearchResult(**row) for row in cached.get("results") or []]
        except Exception:  # noqa: BLE001 - a missing/corrupt cache entry is a miss
            pass

        if self.used_today >= self._daily_cap:
            self.last_error = f"daily search budget reached ({self._daily_cap}); free rungs only"
            return []

        self._spend()
        results = self._inner.search(query, limit)
        if results:
            try:
                self._cache_dir.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"at": time.time(), "query": query, "results": [r.to_dict() for r in results]}))
            except Exception:  # noqa: BLE001
                pass
        return results


def from_env(env: dict[str, str] | None = None) -> SearchProvider:
    """Pick an adapter from env; fall back to any other configured key, then to null.

    The chosen adapter is wrapped in :class:`CachedSearch` unless ``SEARCH_CACHE=off``, so a
    repeated provider costs no query and the daily cap protects the free plan.
    """
    env = dict(os.environ if env is None else env)
    preferred = (env.get("SEARCH_PROVIDER") or "serper").strip().lower()
    searchapi_key = (env.get("SEARCHAPI_API_KEY") or "").strip()
    serper_key = (env.get("SERPER_API_KEY") or "").strip()
    brave_key = (env.get("BRAVE_API_KEY") or "").strip()
    chosen: SearchProvider | None = None
    for choice in [preferred, "searchapi", "serper", "brave"]:
        if choice == "searchapi" and searchapi_key:
            chosen = SearchApiSearch(searchapi_key)
        elif choice == "serper" and serper_key:
            chosen = SerperSearch(serper_key)
        elif choice == "brave" and brave_key:
            chosen = BraveSearch(brave_key)
        if chosen:
            break
    if chosen is None:
        return NullSearch()
    if (env.get("SEARCH_CACHE") or "on").strip().lower() == "off":
        return chosen
    cap_raw = (env.get("SEARCH_DAILY_BUDGET") or "").strip()
    cap = int(cap_raw) if cap_raw.isdigit() else None
    return CachedSearch(chosen, daily_cap=cap)
