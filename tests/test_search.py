import json
import unittest
from pathlib import Path

from guidegen.search import (
    BraveSearch,
    CachedSearch,
    NullSearch,
    SearchApiSearch,
    SearchResult,
    SerperSearch,
    from_env,
)


class SerperTests(unittest.TestCase):
    def test_parses_organic_and_sends_the_key(self) -> None:
        seen: dict = {}

        def http(method, url, headers, body, timeout):
            seen.update({"method": method, "url": url, "headers": headers, "body": body})
            return 200, json.dumps({"organic": [
                {"title": "OAuth", "link": "https://developers.example.com/docs/oauth", "snippet": "s"},
            ]})

        results = SerperSearch("k-123", http=http).search("example oauth", limit=5)
        self.assertEqual([r.url for r in results], ["https://developers.example.com/docs/oauth"])
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["headers"]["X-API-KEY"], "k-123")
        self.assertEqual(json.loads(seen["body"])["q"], "example oauth")

    def test_http_failure_degrades_to_empty(self) -> None:
        provider = SerperSearch("k", http=lambda *a, **k: (429, ""))
        self.assertEqual(provider.search("x"), [])
        self.assertIn("429", provider.last_error or "")

    def test_requires_a_key(self) -> None:
        with self.assertRaises(ValueError):
            SerperSearch("")


class BraveTests(unittest.TestCase):
    def test_parses_web_results(self) -> None:
        def http(method, url, headers, body, timeout):
            self.assertEqual(method, "GET")
            self.assertIn("q=example+oauth", url)
            self.assertEqual(headers["X-Subscription-Token"], "b-key")
            return 200, json.dumps({"web": {"results": [
                {"title": "Authentication", "url": "https://developer.example.com/docs/auth", "description": "d"},
            ]}})

        results = BraveSearch("b-key", http=http).search("example oauth")
        self.assertEqual(results[0].url, "https://developer.example.com/docs/auth")


class SearchApiTests(unittest.TestCase):
    def test_parses_organic_results_and_sends_the_key_in_the_query(self) -> None:
        seen: dict = {}

        def http(method, url, headers, body, timeout):
            seen.update({"method": method, "url": url, "headers": headers})
            return 200, json.dumps({"organic_results": [
                {"title": "Authentication", "link": "https://developers.example.com/docs/auth", "snippet": "s"},
            ]})

        results = SearchApiSearch("k-9", http=http).search("example oauth", limit=5)
        self.assertEqual([r.url for r in results], ["https://developers.example.com/docs/auth"])
        self.assertEqual(seen["method"], "GET")
        self.assertIn("engine=google", seen["url"])
        self.assertIn("q=example+oauth", seen["url"])
        self.assertIn("api_key=k-9", seen["url"])

    def test_http_failure_degrades_to_empty(self) -> None:
        provider = SearchApiSearch("k", http=lambda *a, **k: (401, ""))
        self.assertEqual(provider.search("x"), [])
        self.assertIn("401", provider.last_error or "")

    def test_requires_a_key(self) -> None:
        with self.assertRaises(ValueError):
            SearchApiSearch("")


class FakeProvider:
    name = "fake"

    def __init__(self, rows=None) -> None:
        self.calls: list[str] = []
        self.rows = rows if rows is not None else [SearchResult("T", "https://developers.example.com/docs/oauth")]
        self.last_error: str | None = None

    def search(self, query: str, limit: int = 5):
        self.calls.append(query)
        return self.rows[:limit]


class CachedSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _wrap(self, inner, **kw):
        return CachedSearch(
            inner,
            cache_dir=self.root / "cache",
            budget_path=self.root / "budget.json",
            **kw,
        )

    def test_an_identical_query_is_free_the_second_time(self) -> None:
        inner = FakeProvider()
        cached = self._wrap(inner)
        first = cached.search("example oauth", limit=5)
        second = cached.search("example oauth", limit=5)
        self.assertEqual([r.url for r in first], [r.url for r in second])
        self.assertEqual(len(inner.calls), 1)
        self.assertEqual(cached.used_today, 1)

    def test_a_different_query_spends_again(self) -> None:
        inner = FakeProvider()
        cached = self._wrap(inner)
        cached.search("one")
        cached.search("two")
        self.assertEqual(len(inner.calls), 2)
        self.assertEqual(cached.used_today, 2)

    def test_the_daily_cap_stops_calls_and_explains_itself(self) -> None:
        inner = FakeProvider()
        cached = self._wrap(inner, daily_cap=2)
        cached.search("one")
        cached.search("two")
        self.assertEqual(cached.search("three"), [])
        self.assertEqual(len(inner.calls), 2)
        self.assertIn("daily search budget", cached.last_error or "")

    def test_a_cache_hit_still_works_after_the_cap_is_reached(self) -> None:
        inner = FakeProvider()
        cached = self._wrap(inner, daily_cap=1)
        cached.search("one")
        self.assertEqual(len(cached.search("one")), 1)  # served from cache, no spend
        self.assertEqual(cached.used_today, 1)

    def test_an_expired_entry_is_refetched(self) -> None:
        inner = FakeProvider()
        cached = self._wrap(inner, ttl=0.0)
        cached.search("one")
        cached.search("one")
        self.assertEqual(len(inner.calls), 2)

    def test_an_empty_result_is_not_cached(self) -> None:
        inner = FakeProvider(rows=[])
        cached = self._wrap(inner)
        cached.search("one")
        cached.search("one")
        self.assertEqual(len(inner.calls), 2)


class FactoryTests(unittest.TestCase):
    def test_no_keys_is_null(self) -> None:
        self.assertIsInstance(from_env({}), NullSearch)

    def test_the_adapter_is_wrapped_in_the_cache_by_default(self) -> None:
        provider = from_env({"SERPER_API_KEY": "x"})
        self.assertIsInstance(provider, CachedSearch)
        self.assertEqual(provider.name, "serper")

    def test_search_cache_off_returns_the_bare_adapter(self) -> None:
        provider = from_env({"SERPER_API_KEY": "x", "SEARCH_CACHE": "off"})
        self.assertIsInstance(provider, SerperSearch)

    def test_searchapi_is_used_when_its_key_is_the_only_one(self) -> None:
        self.assertEqual(from_env({"SEARCHAPI_API_KEY": "x"}).name, "searchapi")

    def test_serper_key(self) -> None:
        self.assertEqual(from_env({"SERPER_API_KEY": "x"}).name, "serper")

    def test_preferred_vendor_wins(self) -> None:
        env = {"SEARCH_PROVIDER": "brave", "SERPER_API_KEY": "x", "BRAVE_API_KEY": "y"}
        self.assertEqual(from_env(env).name, "brave")

    def test_falls_back_to_another_vendor(self) -> None:
        env = {"SEARCH_PROVIDER": "brave", "SEARCHAPI_API_KEY": "x"}
        self.assertEqual(from_env(env).name, "searchapi")

    def test_the_daily_budget_can_be_set_from_env(self) -> None:
        provider = from_env({"SERPER_API_KEY": "x", "SEARCH_DAILY_BUDGET": "5"})
        self.assertEqual(provider._daily_cap, 5)

    def test_null_provider_never_calls_out(self) -> None:
        self.assertEqual(NullSearch().search("anything"), [])


if __name__ == "__main__":
    unittest.main()
