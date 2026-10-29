import copy
import json
import unittest
from pathlib import Path

from guidegen.pipeline import render_bundle
from guidegen.spec import validate_spec

ROOT = Path(__file__).resolve().parents[1]
SPECS = [ROOT / "data" / "jobs" / "job_15d800aa7a" / "spec.json",
         ROOT / "data" / "jobs" / "job_a81ee54c01" / "spec.json"]


class RenderTests(unittest.TestCase):
    def test_human_guide_contains_steps_sources_and_unknowns(self) -> None:
        spec = json.loads(SPECS[0].read_text())
        human, agent, agent_md = render_bundle(spec)
        self.assertIn("# GitHub integration setup", human)
        self.assertIn("GITHUB_CLIENT_ID", human)
        self.assertIn("## Setup steps", human)
        self.assertIn("## If it breaks", human)
        self.assertIn("## Sources", human)
        self.assertIn("auth.oauth.pkce", human)
        self.assertEqual(validate_spec(spec), [])
        self.assertEqual(agent["auth"]["oauth"]["authorization_url"],
                         spec["auth"]["oauth"]["authorization_url"]["value"])
        self.assertEqual(len(agent["setup"]["steps"]), len(spec["setup"]["steps"]))
        self.assertEqual(agent["unknown_fields"], spec["unknown_fields"])
        self.assertIn("Do not invent", agent_md)
        self.assertIn(spec["auth"]["oauth"]["token_url"]["value"], agent_md)

    def test_conflicting_sources_are_shown_in_the_guide(self) -> None:
        spec = json.loads(SPECS[0].read_text())
        spec = copy.deepcopy(spec)
        spec["notes"] = list(spec.get("notes") or []) + [
            "auth.oauth.authorization_url: 'https://connect.example.com/oauth/authorize' "
            "vs 'https://marketplace.example.com/oauth/v2/authorize' (kept first)",
        ]
        human, _agent, _agent_md = render_bundle(spec)
        self.assertIn("## Sources that disagree", human)
        self.assertIn("marketplace.example.com", human)

    def test_no_conflict_section_when_sources_agree(self) -> None:
        spec = json.loads(SPECS[0].read_text())
        human, _agent, _agent_md = render_bundle(spec)
        self.assertNotIn("## Sources that disagree", human)

    def test_google_guide_has_scopes_and_verification(self) -> None:
        spec = json.loads(SPECS[1].read_text())
        human, agent, _ = render_bundle(spec)
        self.assertIn("openid", human)
        self.assertIn("## Verify", human)
        self.assertEqual(len(agent["setup"]["verification"]), 2)

    def test_no_key_material_is_rendered(self) -> None:
        spec = json.loads(SPECS[0].read_text())
        human, agent, agent_md = render_bundle(spec)
        for text in (human, agent_md, json.dumps(agent)):
            self.assertNotIn("DEEPINFRA_API_KEY", text)
            self.assertNotIn("oc_sk_", text)

    def test_doc_text_is_html_escaped(self) -> None:
        spec = copy.deepcopy(json.loads(SPECS[0].read_text()))
        spec["setup"]["steps"][0]["detail"] = "<script>alert(1)</script>"
        human, _, _ = render_bundle(spec)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", human)
        self.assertNotIn("<script>alert(1)</script>", human)
