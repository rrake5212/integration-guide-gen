import unittest

from guidegen.registry import Registry
from guidegen.resolve import Resolver


def no_discovery(host: str) -> dict:
    return {"discovery": False, "homepage_status": 200, "homepage_url": f"https://{host}/"}


class ResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.resolver = Resolver(probe=no_discovery)

    # 1
    def test_domain_input_resolves(self) -> None:
        res = self.resolver.resolve("tally.so")
        self.assertEqual(res.decision, "resolved")
        self.assertEqual(res.provider.id, "tally")
        self.assertEqual(res.matched_by, "domain")
        self.assertEqual(res.confidence, "high")
        self.assertEqual(res.availability, "api_key_only")

    # 2
    def test_bare_tally_asks_instead_of_guessing(self) -> None:
        res = self.resolver.resolve("tally")
        self.assertEqual(res.decision, "ask")
        self.assertEqual({c.id for c in res.alternatives}, {"tally", "tally-erp"})
        self.assertEqual(res.exit_code, 2)

    # 3
    def test_qualified_tally_name_resolves(self) -> None:
        res = self.resolver.resolve("Tally Forms")
        self.assertEqual(res.decision, "resolved")
        self.assertEqual(res.provider.id, "tally")

    # 4
    def test_toolkit_name_resolves_to_provider(self) -> None:
        res = self.resolver.resolve("Google Sheets")
        self.assertEqual(res.decision, "resolved")
        self.assertEqual(res.provider.id, "google")

    # 5
    def test_full_url_uses_host(self) -> None:
        res = self.resolver.resolve("https://api.github.com/user")
        self.assertEqual(res.decision, "resolved")
        self.assertEqual(res.provider.id, "github")

    # 6
    def test_unknown_domain_is_unknown_not_guessed(self) -> None:
        res = self.resolver.resolve("example.com")
        self.assertEqual(res.decision, "unknown")
        self.assertIsNone(res.provider)
        self.assertEqual(res.availability, "unknown")
        self.assertEqual(res.exit_code, 3)
        self.assertTrue(any("no OIDC/OAuth discovery" in n for n in res.notes))

    # 7
    def test_typo_resolves_by_fuzzy_with_medium_confidence(self) -> None:
        res = self.resolver.resolve("gogle")
        self.assertEqual(res.decision, "resolved")
        self.assertEqual(res.provider.id, "google")
        self.assertEqual(res.matched_by, "fuzzy")
        self.assertIn(res.confidence, {"medium", "low"})

    def test_unrelated_name_is_unknown(self) -> None:
        res = self.resolver.resolve("salesforce")
        self.assertEqual(res.decision, "unknown")

    def test_prisma_name_resolves_from_the_curated_registry(self) -> None:
        res = self.resolver.resolve("prisma")
        self.assertEqual(res.decision, "resolved")
        self.assertEqual(res.provider.id, "prisma")
        self.assertEqual(res.matched_by, "alias")
        self.assertEqual(res.availability, "self_serve_oauth")

    def test_prisma_domain_resolves_without_probing(self) -> None:
        res = self.resolver.resolve("prisma.io")
        self.assertEqual(res.decision, "resolved")
        self.assertEqual(res.provider.id, "prisma")
        self.assertEqual(res.matched_by, "domain")

    def test_registry_loaded(self) -> None:
        registry = Registry()
        self.assertGreaterEqual(len(registry.providers), 7)
        self.assertEqual(len(registry.toolkits), 1688)
        self.assertEqual(registry.provider_for_slug("GOOGLESHEETS").id, "google")
        self.assertEqual(registry.provider_for_slug("WHATSAPP").id, "meta")
        self.assertEqual(registry.provider_for_slug("TALLY").id, "tally")
        self.assertEqual(registry.provider_for_slug("PRISMA").id, "prisma")


if __name__ == "__main__":
    unittest.main()
