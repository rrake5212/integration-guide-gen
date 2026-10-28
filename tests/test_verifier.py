import json
import unittest
from pathlib import Path

from guidegen.fetch import html_to_text
from guidegen.verify import verify_all, verify_quotes, verify_rendered, verify_spec

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "data" / "golden"


def load(name: str):
    base = GOLDEN / name
    spec = json.loads((base / "spec.json").read_text())
    human = (base / "guide.md").read_text()
    agent = json.loads((base / "agent.json").read_text())
    agent_md = (base / "agent.md").read_text()
    return spec, human, agent, agent_md


class GoldenTests(unittest.TestCase):
    def test_expected_assertions(self) -> None:
        expected = json.loads((GOLDEN / "expected.json").read_text())
        for name, want in expected.items():
            spec, human, agent, agent_md = load(name)
            self.assertEqual(spec["provider"]["id"], want["provider"])
            self.assertEqual(spec["status"], want["status"])
            self.assertGreaterEqual(spec["quality"]["facts_verified"], want["min_verified"])
            self.assertEqual(len(spec["setup"]["steps"]), want["steps"])
            self.assertEqual(len(spec["setup"]["blockers"]), want["blockers"])
            self.assertEqual([w["code"] for w in spec["warnings"]], want["warnings"])
            self.assertEqual(verify_all(spec, human, agent, agent_md), [])

    def test_every_golden_spec_has_no_secret_material(self) -> None:
        for name in ("github", "google"):
            spec, human, agent, agent_md = load(name)
            self.assertEqual(verify_rendered(spec, human, agent, agent_md), [])

    def test_quote_provenance_against_stored_docs(self) -> None:
        job = ROOT / "data" / "jobs" / "job_15d800aa7a"
        spec = json.loads((job / "spec.json").read_text())
        text_by_url = {}
        for index, url in enumerate(spec["provider"]["docs"]):
            html = (job / "docs" / f"{index}.html").read_text(encoding="utf-8", errors="replace")
            text_by_url[url] = html_to_text(html)
        self.assertEqual(verify_quotes(spec, text_by_url), [])


class MutationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec, self.human, self.agent, self.agent_md = load("github")

    def test_missing_value_in_guide_is_caught(self) -> None:
        broken = self.human.replace("GITHUB_CLIENT_ID", "REMOVED")
        issues = verify_rendered(self.spec, broken, self.agent, self.agent_md)
        self.assertTrue(any("GITHUB_CLIENT_ID" in issue or "missing" in issue for issue in issues))

    def test_missing_step_is_caught(self) -> None:
        broken = self.human.replace(self.spec["setup"]["steps"][0]["detail"], "gone")
        issues = verify_rendered(self.spec, broken, self.agent, self.agent_md)
        self.assertTrue(any("step 1 detail" in issue for issue in issues))

    def test_duplicate_source_and_bad_hash_are_caught(self) -> None:
        broken = json.loads(json.dumps(self.spec))
        broken["sources"].append(dict(broken["sources"][0]))
        broken["sources"][0]["doc_hash"] = "md5:abc"
        issues = verify_spec(broken)
        self.assertTrue(any("duplicate source" in issue for issue in issues))
        self.assertTrue(any("invalid doc_hash" in issue for issue in issues))

    def test_secret_in_output_is_caught(self) -> None:
        broken = self.human + "\nsk-abcdefghijklmnopqrstuvwxyz123456"
        issues = verify_rendered(self.spec, broken, self.agent, self.agent_md)
        self.assertTrue(any("secret material" in issue for issue in issues))

    def test_fabricated_quote_is_caught(self) -> None:
        broken = json.loads(json.dumps(self.spec))
        broken["auth"]["oauth"]["token_url"]["quote"] = "this sentence is not in the docs"
        issues = verify_quotes(broken, {broken["auth"]["oauth"]["token_url"]["source_url"]: "something else"})
        self.assertTrue(any("quote not found" in issue for issue in issues))
