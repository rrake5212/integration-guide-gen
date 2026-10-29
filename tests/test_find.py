import unittest

from guidegen.catalog import CatalogEntry, domain_candidates, first_party, parse_auth
from guidegen.errors import GuideError
from guidegen.fetch import Document
from guidegen.find import Finder, find, infer_domains, score_candidate, setup_hits, surface_kind, verify
from guidegen.search import SearchResult


def doc(url: str, title: str = "", text: str = "") -> Document:
    return Document(url=url, title=title, text=text, raw=f"<html><head><title>{title}</title></head><body>{text}</body></html>",
                    fetched_at="2026-10-29T00:00:00+00:00", http_status=200, sha256="0" * 64)


SETUP = ("Create an app in the developer console. Copy the client id and client secret. "
         "Add a redirect URI. Choose scopes for the authorization code flow. ")


class FakeFetcher:
    def __init__(self, pages: dict[str, Document]):
        self.pages = pages
        self.calls: list[str] = []

    def __call__(self, url: str) -> Document:
        self.calls.append(url)
        if url in self.pages:
            return self.pages[url]
        raise GuideError("DOCS_NOT_FOUND", f"HTTP 404 for {url}")


class RankerTests(unittest.TestCase):
    def test_docs_auth_page_beats_product_marketing_page(self) -> None:
        good = score_candidate("https://developer.example.com/docs/authentication",
                               title="Authentication | Example Developers", text=SETUP, brand="example.com")
        bad = score_candidate("https://example.com/blog/oauth-is-fun",
                              title="Why we love OAuth", text=SETUP, brand="example.com")
        self.assertGreater(good[0], bad[0])
        self.assertGreaterEqual(good[0], 9)

    def test_generic_overview_page_is_not_a_surface(self) -> None:
        score, _, reasons = score_candidate("https://developers.example.com/guides/get-started/overview",
                                            title="Overview", text="Welcome to the platform", brand="example.com")
        self.assertLess(score, 7)
        self.assertTrue(any("generic landing" in r for r in reasons))

    def test_other_product_surface_is_not_usable(self) -> None:
        cand = verify(doc("https://docs.example.com/agentic/wallet/oauth", "Wallet OAuth", SETUP),
                      rung="sitemap", brand="Example")
        self.assertTrue(cand.blocked)
        self.assertFalse(cand.usable)
        self.assertTrue(any("different product surface" in r for r in cand.reasons))

    def test_a_page_that_only_says_oauth_is_not_usable(self) -> None:
        weak = doc("https://developer.example.com/docs/oauth", "OAuth",
                   "OAuth is an authorization framework. Read the overview to learn more.")
        self.assertLess(len(setup_hits(weak.text)), 3)

    def test_kinds(self) -> None:
        self.assertEqual(surface_kind("https://docs.example.com/connect/oauth"), "connect_oauth")
        self.assertEqual(surface_kind("https://example.com/app-marketplace/oauth"), "app_marketplace")
        self.assertEqual(surface_kind("https://docs.example.com/docs/api-keys"), "api_key_settings")
        self.assertEqual(surface_kind("https://docs.example.com/docs/authentication"), "oauth_app_registration")


class LadderTests(unittest.TestCase):
    def test_llms_rung_stops_the_ladder(self) -> None:
        pages = {
            "https://example.com/llms.txt": doc("https://example.com/llms.txt", "llms",
                                                "- [OAuth 2.0 authentication](https://developers.example.com/docs/oauth)"),
            "https://developers.example.com/docs/oauth": doc(
                "https://developers.example.com/docs/oauth", "OAuth 2.0 authentication | Example", SETUP),
        }
        fetcher = FakeFetcher(pages)
        result = find("example", brand="Example", domains=["example.com"], fetcher=fetcher, max_pages=20)
        self.assertEqual(result.status, "resolved")
        best = result.best
        assert best is not None
        self.assertEqual(best.url, "https://developers.example.com/docs/oauth")
        self.assertLessEqual(result.pages_fetched, 3)

    def test_two_credible_surfaces_ask_instead_of_pick(self) -> None:
        connect = ("https://docs.example.com/connect/oauth", "Connect OAuth reference", SETUP)
        market = ("https://docs.example.com/marketplace/oauth", "Marketplace OAuth", SETUP)
        pages = {
            "https://docs.example.com/sitemap.xml": doc(
                "https://docs.example.com/sitemap.xml", "sitemap",
                "<urlset><url><loc>https://docs.example.com/connect/oauth</loc></url>"
                "<url><loc>https://docs.example.com/marketplace/oauth</loc></url></urlset>"),
            connect[0]: doc(connect[0], connect[1], connect[2]),
            market[0]: doc(market[0], market[1], market[2]),
        }
        result = find("example", brand="Example", domains=["example.com"], fetcher=FakeFetcher(pages), max_pages=20)
        self.assertEqual(result.status, "needs_review")
        self.assertIn("two credible surfaces", " ".join(result.notes))

    def test_search_only_runs_on_the_residual_and_needs_verification(self) -> None:
        class Searcher:
            name = "fake"

            def __init__(self) -> None:
                self.queries: list[str] = []

            def search(self, query: str, limit: int = 5):
                self.queries.append(query)
                return [SearchResult("Authentication", "https://developer.example.com/docs/auth", "")]

        searcher = Searcher()
        pages = {"https://developer.example.com/docs/auth": doc(
            "https://developer.example.com/docs/auth", "Authentication | Example", SETUP)}
        result = find("example", brand="Example", domains=["example.com"], fetcher=FakeFetcher(pages),
                      searcher=searcher, max_pages=20, allow_search=True)
        self.assertEqual(len(searcher.queries), 1)
        self.assertEqual(result.status, "resolved")

    def test_search_is_not_called_when_the_free_ladder_already_won(self) -> None:
        class Searcher:
            name = "fake"
            calls = 0

            def search(self, query: str, limit: int = 5):
                self.calls += 1
                return []

        searcher = Searcher()
        pages = {
            "https://example.com/llms.txt": doc("https://example.com/llms.txt", "llms",
                                                "- [Authentication](https://docs.example.com/authentication)"),
            "https://docs.example.com/authentication": doc(
                "https://docs.example.com/authentication", "Authentication | Example", SETUP),
        }
        find("example", brand="Example", domains=["example.com"], fetcher=FakeFetcher(pages),
             searcher=searcher, max_pages=10)
        self.assertEqual(searcher.calls, 0)

    def test_a_rejected_search_key_is_visible_not_silent(self) -> None:
        class Broken:
            name = "serper"
            last_error = "serper HTTP 403"

            def search(self, query: str, limit: int = 5):
                return []

        result = find("example", brand="Example", domains=["example.com"],
                      fetcher=FakeFetcher({}), searcher=Broken(), max_pages=6)
        self.assertTrue(any("403" in n for n in result.notes), result.notes)

    def test_search_rejects_third_party_hosts(self) -> None:
        class Searcher:
            name = "fake"

            def search(self, query: str, limit: int = 5):
                return [
                    SearchResult("Configuration guide", "https://developers.apideck.com/apis/crm/hubspot/configuration", ""),
                    SearchResult("Setup OAuth", "https://www.reform.app/blog/setup-oauth-hubspot-api", ""),
                ]

        result = find("hubspot", brand="HubSpot", domains=["hubspot.com"],
                      fetcher=FakeFetcher({}), searcher=Searcher(), max_pages=4)
        self.assertEqual(result.surfaces, [])
        self.assertEqual(result.status, "unknown")

    def test_no_signal_is_unknown_not_a_guess(self) -> None:
        result = find("example", brand="Example", domains=["example.com"],
                      fetcher=FakeFetcher({}), max_pages=8)
        self.assertEqual(result.status, "unknown")
        self.assertIsNone(result.best)

    def test_wellknown_metadata_alone_is_needs_review(self) -> None:
        pages = {"https://example.com/.well-known/openid-configuration": Document(
            url="https://example.com/.well-known/openid-configuration", title="oidc",
            text='{"authorization_endpoint": "https://example.com/authorize", "token_endpoint": "https://example.com/token"}',
            raw="{}", fetched_at="now", http_status=200, sha256="1" * 64)}
        result = find("example", brand="Example", domains=["example.com"], fetcher=FakeFetcher(pages), max_pages=20)
        self.assertEqual(result.status, "needs_review")
        self.assertTrue(any(c.kind == "discovery" for c in result.surfaces))


class DomainInferenceTests(unittest.TestCase):
    def test_only_a_host_that_names_the_brand_is_accepted(self) -> None:
        pages = {"https://acme.com/": doc("https://acme.com/", "Acme - automation", "Acme homepage")}
        self.assertEqual(infer_domains("Acme", "ACME", fetcher=FakeFetcher(pages)), ["acme.com"])

    def test_unrelated_host_is_rejected(self) -> None:
        pages = {"https://acme.com/": doc("https://acme.com/", "Parked domain", "buy this domain")}
        self.assertEqual(infer_domains("Acme", "ACME", fetcher=FakeFetcher(pages)), [])

    def test_candidates_are_bounded(self) -> None:
        self.assertLessEqual(len(domain_candidates("Google Sheets", "GOOGLESHEETS")), 6)


class CatalogTests(unittest.TestCase):
    def test_auth_families(self) -> None:
        self.assertEqual(parse_auth("OAUTH2, API_KEY"), ("oauth2", "api_key"))
        self.assertEqual(CatalogEntry("X", "X", ("oauth2",)).availability, "self_serve_oauth")
        self.assertEqual(CatalogEntry("X", "X", ("api_key",)).availability, "api_key")
        self.assertEqual(CatalogEntry("X", "X", ()).availability, "unknown")

    def test_auth_label(self) -> None:
        entry = CatalogEntry("X", "X", ("oauth2", "api_key"))
        self.assertEqual(entry.label, "OAuth 2.0 + API keys")

    def test_first_party_strips_docs_subdomain(self) -> None:
        self.assertTrue(first_party("developers.example.com", ["Example"]))
        self.assertFalse(first_party("other.org", ["Example"]))


if __name__ == "__main__":
    unittest.main()
