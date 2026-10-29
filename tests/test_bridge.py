"""The bridge: catalogue entry -> located surface -> Provider (PLAN §13.6/§13.8).

Two properties matter and both are tested here: a stored surface costs no network at all, and a
FIND run records what it found so the next request for the same name is free.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from guidegen.bridge import availability_for, provider_from_surface, provider_for_catalogue
from guidegen.catalog import CatalogEntry
from guidegen.errors import GuideError
from guidegen.fetch import Document
from guidegen.find import Candidate, FindResult
from guidegen.surfaces import Surface, SurfaceStore


def doc(url: str, title: str = "", text: str = "") -> Document:
    return Document(url=url, title=title, text=text,
                    raw=f"<html><head><title>{title}</title></head><body>{text}</body></html>",
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


class RefusingFetcher:
    def __call__(self, url: str) -> Document:
        raise AssertionError(f"network was used for {url}")


def entry(slug: str = "EXAMPLE", name: str = "Example", families: tuple[str, ...] = ("oauth2",)) -> CatalogEntry:
    return CatalogEntry(slug=slug, name=name, auth_families=families)


class AvailabilityTests(unittest.TestCase):
    def test_the_catalogue_word_api_key_becomes_the_spec_word(self) -> None:
        self.assertEqual(availability_for(entry(families=("api_key",))), "api_key_only")

    def test_oauth_stays_self_serve(self) -> None:
        self.assertEqual(availability_for(entry(families=("oauth2",))), "self_serve_oauth")

    def test_no_known_family_is_unknown(self) -> None:
        self.assertEqual(availability_for(entry(families=())), "unknown")


class ProviderFromSurfaceTests(unittest.TestCase):
    def test_the_located_page_is_the_providers_only_doc(self) -> None:
        surface = Surface(kind="oauth_app_registration", docs_url="https://developers.example.com/docs/oauth",
                          title="OAuth", rung="search", score=11)
        provider = provider_from_surface(entry(), surface, ["example.com"])
        self.assertEqual(provider.id, "example")
        self.assertEqual(provider.name, "Example")
        self.assertEqual(provider.docs, ("https://developers.example.com/docs/oauth",))
        self.assertEqual(provider.availability, "self_serve_oauth")
        self.assertIn("FIND", provider.note or "")


class StoredSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SurfaceStore(path=Path(self.tmp.name) / "surfaces.json", providers={"providers": []})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _seed(self, docs_url: str, accepted_by: str = "human") -> None:
        self.store.propose(
            FindResult(query="airtable", brand="Airtable", domains=["airtable.com"], status="resolved",
                       surfaces=[Candidate(url=docs_url, rung="llms", kind="oauth_app_registration",
                                           score=11, title="OAuth", verified=True)]),
            entry_id="airtable", name="Airtable", domains=["airtable.com"], auth_families=["oauth2"],
        )
        if accepted_by == "human":
            self.store.confirm("airtable")

    def test_a_stored_surface_is_used_without_any_network(self) -> None:
        self._seed("https://airtable.com/developers/web/guides/oauth")
        provider = provider_for_catalogue(entry("AIRTABLE", "Airtable"), store=self.store, fetcher=RefusingFetcher())
        self.assertEqual(provider.docs, ("https://airtable.com/developers/web/guides/oauth",))
        self.assertIn("surfaces.json", provider.note or "")

    def test_a_human_confirmed_entry_is_not_replaced_by_a_new_find(self) -> None:
        self._seed("https://airtable.com/developers/web/guides/oauth")
        provider_for_catalogue(entry("AIRTABLE", "Airtable"), store=self.store, fetcher=RefusingFetcher())
        self.assertEqual(self.store.get("airtable").surfaces[0].docs_url,
                         "https://airtable.com/developers/web/guides/oauth")


class SeedFallbackTests(unittest.TestCase):
    def test_the_runtime_store_starts_from_the_packaged_seed_and_writes_to_the_volume(self) -> None:
        # the container case: image tree read-only, volume empty on first run
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "runtime" / "surfaces.json"
            with patch("guidegen.surfaces.SURFACES_PATH", target):
                store = SurfaceStore(providers={"providers": []})
                self.assertIn("google", set(store.all()))      # served from the seed
                store.save()
            self.assertTrue(target.exists())
            self.assertIn("google", json.loads(target.read_text())["entries"])

    def test_an_explicit_path_never_inherits_the_seed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = SurfaceStore(path=Path(tmp) / "surfaces.json", providers={"providers": []})
            self.assertEqual(set(store.all()), set())


class FindPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = SurfaceStore(path=Path(self.tmp.name) / "surfaces.json", providers={"providers": []})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_find_locates_the_docs_and_records_them_for_next_time(self) -> None:
        pages = {
            "https://example.com/": doc("https://example.com/", "Example — build with our API", "home"),
            "https://example.com/llms.txt": doc(
                "https://example.com/llms.txt", "llms",
                "- [OAuth 2.0 authentication](https://developers.example.com/docs/oauth)"),
            "https://developers.example.com/docs/oauth": doc(
                "https://developers.example.com/docs/oauth", "OAuth 2.0 authentication | Example", SETUP),
        }
        fetcher = FakeFetcher(pages)
        provider = provider_for_catalogue(entry(), store=self.store, fetcher=fetcher, searcher=None)

        self.assertEqual(provider.docs, ("https://developers.example.com/docs/oauth",))
        self.assertEqual(provider.availability, "self_serve_oauth")
        stored = self.store.get("example")
        self.assertIsNotNone(stored)
        self.assertEqual(stored.status, "resolved")
        self.assertEqual(stored.surfaces[0].docs_url, "https://developers.example.com/docs/oauth")
        self.assertEqual(stored.accepted_by, "auto")          # usable now, still in pending() for a human
        self.assertIn(stored, self.store.pending())

    def test_a_read_only_store_degrades_to_a_note_instead_of_killing_the_job(self) -> None:
        pages = {
            "https://example.com/": doc("https://example.com/", "Example — build with our API", "home"),
            "https://example.com/llms.txt": doc(
                "https://example.com/llms.txt", "llms",
                "- [OAuth 2.0 authentication](https://developers.example.com/docs/oauth)"),
            "https://developers.example.com/docs/oauth": doc(
                "https://developers.example.com/docs/oauth", "OAuth 2.0 authentication | Example", SETUP),
        }
        def refuse() -> None:
            raise PermissionError(13, "Permission denied")
        self.store.save = refuse  # the container case: the image tree is root-owned
        provider = provider_for_catalogue(entry(), store=self.store, fetcher=FakeFetcher(pages), searcher=None)
        self.assertEqual(provider.docs, ("https://developers.example.com/docs/oauth",))
        self.assertIn("not persisted", provider.note or "")

    def test_nothing_found_is_a_clean_provider_unknown(self) -> None:
        fetcher = FakeFetcher({"https://example.com/": doc("https://example.com/", "Example", "home")})
        with self.assertRaises(GuideError) as ctx:
            provider_for_catalogue(entry(), store=self.store, fetcher=fetcher, searcher=None, max_pages=4)
        self.assertEqual(ctx.exception.code, "PROVIDER_UNKNOWN")
        self.assertEqual(self.store.get("example").status, "unknown")


if __name__ == "__main__":
    unittest.main()
