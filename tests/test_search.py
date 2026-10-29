import json
import unittest

from guidegen.search import BraveSearch, NullSearch, SerperSearch, from_env


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


class FactoryTests(unittest.TestCase):
    def test_no_keys_is_null(self) -> None:
        self.assertIsInstance(from_env({}), NullSearch)

    def test_serper_key(self) -> None:
        self.assertIsInstance(from_env({"SERPER_API_KEY": "x"}), SerperSearch)

    def test_preferred_vendor_wins(self) -> None:
        env = {"SEARCH_PROVIDER": "brave", "SERPER_API_KEY": "x", "BRAVE_API_KEY": "y"}
        self.assertIsInstance(from_env(env), BraveSearch)

    def test_falls_back_to_the_other_vendor(self) -> None:
        env = {"SEARCH_PROVIDER": "brave", "SERPER_API_KEY": "x"}
        self.assertIsInstance(from_env(env), SerperSearch)

    def test_null_provider_never_calls_out(self) -> None:
        self.assertEqual(NullSearch().search("anything"), [])


if __name__ == "__main__":
    unittest.main()
