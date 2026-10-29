import json
import tempfile
import unittest
from pathlib import Path

from guidegen.find import Candidate, FindResult
from guidegen.surfaces import Entry, Surface, SurfaceStore


def result(status: str, surfaces: list[Candidate], brand: str = "Acme") -> FindResult:
    return FindResult(query=brand.lower(), brand=brand, domains=["acme.com"], status=status,
                      surfaces=surfaces, rungs=["llms"], pages_fetched=3, notes=["n"])


def cand(url: str, score: int = 9, kind: str = "oauth_app_registration", verified: bool = True) -> Candidate:
    return Candidate(url=url, rung="llms", kind=kind, score=score, title="Authentication",
                     verified=verified, hits=["client_id", "client_secret", "redirect_uri"])


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "surfaces.json"
        self.store = SurfaceStore(path=self.path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_curated_providers_are_seeded_and_resolved(self) -> None:
        self.assertGreaterEqual(len(self.store.all()), 9)
        github = self.store.get("github")
        assert github is not None
        self.assertEqual(github.status, "resolved")
        self.assertEqual(github.accepted_by, "human")

    def test_resolved_find_is_auto_accepted_and_persists(self) -> None:
        entry = self.store.propose(result("resolved", [cand("https://developer.acme.com/docs/oauth")]),
                                   entry_id="acme", name="Acme", domains=["acme.com"])
        self.assertEqual(entry.status, "resolved")
        self.assertEqual(entry.accepted_by, "auto")
        self.store.save()
        again = SurfaceStore(path=self.path).get("acme")
        assert again is not None
        self.assertEqual(again.docs_url, "https://developer.acme.com/docs/oauth")

    def test_needs_review_keeps_proposals_and_waits(self) -> None:
        entry = self.store.propose(result("needs_review", [
            cand("https://docs.acme.com/connect/oauth", 9, "connect_oauth"),
            cand("https://docs.acme.com/marketplace/oauth", 9, "app_marketplace"),
        ]), entry_id="acme", name="Acme", domains=["acme.com"])
        self.assertEqual(entry.status, "needs_review")
        self.assertEqual(len(entry.proposals), 2)
        self.assertEqual(entry.surfaces, [])
        self.assertIn(entry.id, [e.id for e in self.store.pending()])

    def test_human_confirm_freezes_the_answer(self) -> None:
        self.store.propose(result("needs_review", [
            cand("https://docs.acme.com/connect/oauth", 9, "connect_oauth"),
            cand("https://docs.acme.com/marketplace/oauth", 8, "app_marketplace"),
        ]), entry_id="acme", name="Acme", domains=["acme.com"])
        entry = self.store.confirm("acme", 1)
        self.assertEqual(entry.accepted_by, "human")
        self.assertEqual(entry.docs_url, "https://docs.acme.com/marketplace/oauth")
        self.assertEqual(entry.status, "resolved")

    def test_a_later_auto_result_never_overwrites_a_human_answer(self) -> None:
        self.store.propose(result("needs_review", [cand("https://docs.acme.com/marketplace/oauth", 8, "app_marketplace")]),
                           entry_id="acme", name="Acme", domains=["acme.com"])
        self.store.confirm("acme", 0)
        stored = self.store.propose(result("resolved", [cand("https://docs.acme.com/connect/oauth", 9, "connect_oauth")]),
                                    entry_id="acme", name="Acme", domains=["acme.com"])
        self.assertEqual(stored.docs_url, "https://docs.acme.com/marketplace/oauth")
        self.assertEqual(stored.accepted_by, "human")

    def test_status_and_notes_are_recorded(self) -> None:
        entry = self.store.propose(result("unknown", []), entry_id="ghost", name="Ghost")
        self.assertEqual(entry.status, "unknown")
        self.assertEqual(entry.find["pages_fetched"], 3)

    def test_mark_stale(self) -> None:
        self.store.propose(result("resolved", [cand("https://developer.acme.com/docs/oauth")]),
                           entry_id="acme", name="Acme", domains=["acme.com"])
        entry = self.store.mark_stale("acme", "404 on re-verify")
        self.assertEqual(entry.status, "stale")
        self.assertIn("404 on re-verify", entry.notes)

    def test_confirm_out_of_range(self) -> None:
        self.store.propose(result("needs_review", [cand("https://docs.acme.com/x")]), entry_id="acme", name="Acme")
        with self.assertRaises(IndexError):
            self.store.confirm("acme", 5)

    def test_file_shape_is_diffable(self) -> None:
        self.store.propose(result("resolved", [cand("https://developer.acme.com/docs/oauth")]),
                           entry_id="acme", name="Acme", domains=["acme.com"])
        self.store.save()
        raw = json.loads(self.path.read_text())
        self.assertEqual(raw["version"], 1)
        entries = raw["entries"]
        assert entries is not None
        self.assertIn("acme", entries)
        self.assertEqual(entries["acme"]["surfaces"][0]["docs_url"],
                         "https://developer.acme.com/docs/oauth")


if __name__ == "__main__":
    unittest.main()
