"""The audit's metric is the number the project quotes — so it gets its own tests.

``tools/find_audit.py`` is not a package; it is loaded by path. Importing it is safe: the
module-level imports only touch guidegen modules, and nothing runs a probe until main().
"""
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))

from find_audit import norm, registrable, verdict  # noqa: E402


class NormaliseTests(unittest.TestCase):
    def test_strips_www_anchors_the_md_variant_and_the_trailing_slash(self) -> None:
        self.assertEqual(norm("https://www.docs.example.com/oauth/#top"), ("example.com", "/oauth"))
        self.assertEqual(norm("https://linear.app/developers/oauth-2-0-authentication.md"),
                         ("linear.app", "/developers/oauth-2-0-authentication"))

    def test_registrable_handles_subdomains_and_two_label_suffixes(self) -> None:
        self.assertEqual(registrable("developers.hubspot.com"), "hubspot.com")
        self.assertEqual(registrable("developer.zendesk.com"), "zendesk.com")


class VerdictTests(unittest.TestCase):
    def test_the_same_page_is_exact(self) -> None:
        self.assertEqual(verdict("https://developer.box.com/guides/authentication/oauth2",
                                 "https://developer.box.com/guides/authentication/oauth2/"), "EXACT")

    def test_the_md_variant_is_exact(self) -> None:
        self.assertEqual(verdict("https://lin.app/developers/oauth.md", "https://lin.app/developers/oauth"), "EXACT")

    def test_an_index_page_of_the_canonical_path_is_exact(self) -> None:
        self.assertEqual(verdict("https://x.com/docs/oauth/index.html", "https://x.com/docs/oauth"), "EXACT")

    def test_a_parent_listing_page_is_only_near(self) -> None:
        # the Zendesk case: /documentation/authentication/ is the hub, not the OAuth token page
        self.assertEqual(
            verdict("https://developer.zendesk.com/documentation/authentication/",
                    "https://developer.zendesk.com/documentation/authentication/creating-and-using-oauth-tokens-with-the-api/"),
            "NEAR",
        )

    def test_a_sibling_surface_on_the_right_host_is_near_not_exact(self) -> None:
        # Stripe Apps OAuth vs Connect OAuth — different product, same docs host
        self.assertEqual(verdict("https://docs.stripe.com/stripe-apps/api-authentication/oauth",
                                 "https://docs.stripe.com/connect/oauth-reference"), "NEAR")

    def test_a_first_party_but_unrelated_page_is_wrong(self) -> None:
        self.assertEqual(verdict("https://shopify.dev/docs/apps/build/pricing", "https://shopify.dev/docs/apps/auth/oauth"), "WRONG")

    def test_another_host_is_wrong(self) -> None:
        self.assertEqual(verdict("https://support.airtable.com/articles/9934989703",
                                 "https://airtable.com/developers/web/guides/oauth"), "WRONG")

    def test_no_page_is_miss(self) -> None:
        self.assertEqual(verdict(None, "https://example.com/docs/oauth"), "MISS")


if __name__ == "__main__":
    unittest.main()
