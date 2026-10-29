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

No key => NullSearch, and the finder simply stops after the free rungs. Nothing here
raises on a search failure: a dead search backend degrades the fallback, not the run.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Protocol

USER_AGENT = "oauth-guide-gen/0.1 (surface finder)"
SERPER_ENDPOINT = "https://google.serper.dev/search"
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
TIMEOUT = 12.0


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


def from_env(env: dict[str, str] | None = None) -> SearchProvider:
    """Pick an adapter from env; fall back to the other vendor's key, then to null."""
    env = dict(os.environ if env is None else env)
    preferred = (env.get("SEARCH_PROVIDER") or "serper").strip().lower()
    serper_key = (env.get("SERPER_API_KEY") or "").strip()
    brave_key = (env.get("BRAVE_API_KEY") or "").strip()
    order = [preferred, "serper", "brave"]
    for choice in order:
        if choice == "serper" and serper_key:
            return SerperSearch(serper_key)
        if choice == "brave" and brave_key:
            return BraveSearch(brave_key)
    return NullSearch()
