import unittest

from guidegen.assemble import assemble
from guidegen.blockers import select_blockers
from guidegen.effort import estimate
from guidegen.fetch import Document
from guidegen.llm import LLMResult
from guidegen.registry import Registry
from guidegen.research import ResearchResult, _client_secret_fallback, _redirect_rule_fallback
from guidegen.resolve import Resolution
from guidegen.spec import validate_spec
from guidegen.steps import extract_steps

DOC_URL = "https://docs.example.com/setup"
DOC_TEXT = (
    "## Create an OAuth app\n"
    "Open the provider console and create a new OAuth app.\n"
    "Set the redirect URI to https://app.example.com/callback; it must exactly match the registered callback URL.\n"
    "## Authorize users\n"
    "Send the user to the authorization URL and exchange the code at the token URL.\n"
)


def document() -> Document:
    return Document(url=DOC_URL, title="Setup", text=DOC_TEXT, raw=DOC_TEXT,
                    fetched_at="2026-10-27T00:00:00+00:00", http_status=200, sha256="a" * 64)


def resolution(provider) -> Resolution:
    return Resolution(raw="github", normalized="github", kind="text", decision="resolved",
                      provider=provider, matched_by="alias", confidence="high",
                      availability=provider.availability)


class FakeClient:
    model = "fake-model"

    def __init__(self, data: dict) -> None:
        self.data = data

    def chat_json(self, messages, schema, schema_name, max_tokens=3000, temperature=0.0) -> LLMResult:
        return LLMResult(data=self.data, model=self.model, ms=5, usage={"total_tokens": 10}, attempts=1)


PRISMA_SHAPED_TEXT = (
    "## Authentication\n"
    "The REST API supports service tokens and OAuth 2.0.\n"
    "### Creating an OAuth Application\n"
    "- Navigate to Prisma Console and log in\n"
    "- Click the Integrations tab in the left sidebar\n"
    "- Under \"Published Applications\", click New Application\n"
    "- Fill in your application details:\n"
    "- Name: Your application name\n"
    "- Copy your Client ID and Client Secret immediately\n"
    "### OAuth Authorization Flow\n"
    "#### 1. Redirect users to authorize\n"
    "Send the user to the authorization endpoint with a client_id and redirect_uri.\n"
    "#### 2. Exchange the code at the token URL\n"
    "POST the code to the token endpoint and read access_token from the response.\n"
)


class RecordingClient:
    """Captures the <docs> block the model was given."""

    model = "fake-model"

    def __init__(self, data: dict) -> None:
        self.data = data
        self.prompts: list[str] = []

    def chat_json(self, messages, schema, schema_name, max_tokens=3000, temperature=0.0) -> LLMResult:
        self.prompts.append(messages[-1]["content"])
        return LLMResult(data=self.data, model=self.model, ms=5, usage={"total_tokens": 10}, attempts=1)


class StepSelectionTests(unittest.TestCase):
    """Regression for the Prisma page shape: the console section must win over the runtime flow."""

    def _doc(self) -> Document:
        return Document(url=DOC_URL, title="REST API Authentication", text=PRISMA_SHAPED_TEXT,
                        raw=PRISMA_SHAPED_TEXT, fetched_at="2026-10-28T00:00:00+00:00",
                        http_status=200, sha256="b" * 64)

    def test_bulleted_console_section_is_sent_to_the_model(self) -> None:
        client = RecordingClient({"steps": []})
        extract_steps(client, "Prisma", [self._doc()])
        self.assertTrue(client.prompts, "no call was made")
        sent = client.prompts[0]
        self.assertIn("Published Applications", sent)
        self.assertIn("Creating an OAuth Application", sent)

    def test_runtime_oauth_flow_is_not_sent(self) -> None:
        client = RecordingClient({"steps": []})
        extract_steps(client, "Prisma", [self._doc()])
        sent = client.prompts[0]
        self.assertNotIn("Redirect users to authorize", sent)
        self.assertNotIn("Exchange the code at the token URL", sent)

    def test_stem_heading_matches_the_gerund_form(self) -> None:
        # "Creating an OAuth Application" used to miss a whole-word check for "create"
        client = RecordingClient({"steps": []})
        extract_steps(client, "Prisma", [self._doc()])
        self.assertIn("Creating an OAuth Application", client.prompts[0])


class EffortTests(unittest.TestCase):
    def test_provider_base_and_drivers(self) -> None:
        provider = Registry().provider("github")
        spec = {"resolution": {"availability": "self_serve_oauth"}, "auth": {"oauth": {"scopes": []}}}
        time = estimate(provider, spec, "OAuth consent screen. Add test users.")
        self.assertEqual((time["range_min_minutes"], time["likely_minutes"], time["range_max_minutes"]),
                         (10, 20, 30))
        self.assertIn("consent screen", time["drivers"])
        self.assertIn("test users must be added", time["drivers"])
        self.assertEqual(time["confidence"], "low")

    def test_sensitive_scope_triggers_review_wait(self) -> None:
        provider = Registry().provider("google")
        spec = {"resolution": {"availability": "self_serve_oauth"},
                "auth": {"oauth": {"scopes": [{"sensitivity": "sensitive"}]}}}
        time = estimate(provider, spec, "Sensitive scopes require verification and review.")
        self.assertEqual(time["review_wait_days"], 7)
        self.assertGreaterEqual(time["range_max_minutes"], time["likely_minutes"])

    def test_unknown_availability_has_no_minutes(self) -> None:
        provider = Registry().provider("github")
        spec = {"resolution": {"availability": "unknown"}, "auth": {}}
        time = estimate(provider, spec, "")
        self.assertIsNone(time["likely_minutes"])
        self.assertEqual(time["confidence"], "none")


class BlockerTests(unittest.TestCase):
    def test_oauth_blockers_are_specific_and_unique(self) -> None:
        spec = {"provider": {"id": "google"}, "auth": {"oauth": {"pkce": {"value": None},
                "refresh_token": {"value": None}, "scopes": [{"sensitivity": "sensitive"}]}}}
        blockers = select_blockers(spec, "OAuth app review and verification.")
        ids = [blocker["id"] for blocker in blockers]
        self.assertIn("redirect_uri_mismatch", ids)
        self.assertIn("google_unverified_app", ids)
        self.assertIn("app_verification", ids)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertLessEqual(len(ids), 8)

    def test_api_key_blockers_do_not_include_oauth_rules(self) -> None:
        spec = {"provider": {"id": "tally"}, "auth": {"api_key": {"env_var": "TALLY_API_KEY"}}}
        ids = [blocker["id"] for blocker in select_blockers(spec, "")]
        self.assertIn("wrong_key_or_workspace", ids)
        self.assertIn("key_exposed_client_side", ids)
        self.assertNotIn("redirect_uri_mismatch", ids)


class StepTests(unittest.TestCase):
    def test_redirect_rule_fallback_uses_a_real_sentence(self) -> None:
        fallback = _redirect_rule_fallback([document()])
        self.assertIsNotNone(fallback)
        self.assertIn("redirect uri", fallback["value"].lower())
        self.assertEqual(fallback["source_url"], DOC_URL)

    def test_client_secret_fallback_requires_explicit_required(self) -> None:
        doc = Document(url=DOC_URL, title="Setup", text="| `client_secret` | `string` | Required | The client secret.",
                       raw="", fetched_at="", http_status=200, sha256="a" * 64)
        fallback = _client_secret_fallback([doc])
        self.assertIsNotNone(fallback)
        self.assertEqual(fallback["value"], "true")
        optional = Document(url=DOC_URL, title="Setup", text="| `client_secret` | `string` | Optional |",
                            raw="", fetched_at="", http_status=200, sha256="a" * 64)
        self.assertIsNone(_client_secret_fallback([optional]))

    def test_steps_require_quotes_and_dedupe(self) -> None:
        data = {"steps": [
            {"title": "Create the app", "detail": "Open the console and create an OAuth app.",
             "quote": "Open the provider console and create a new OAuth app."},
            {"title": "Create the app", "detail": "duplicate", "quote": "Open the provider console and create a new OAuth app."},
            {"title": "Set the redirect", "detail": "Use the callback URL.",
             "quote": "Set the redirect URI to https://app.example.com/callback"},
            {"title": "Fabricated", "detail": "not in docs", "quote": "This sentence is not in the documentation."},
        ]}
        result = extract_steps(FakeClient(data), "GitHub", [document()])
        self.assertEqual([step["title"] for step in result.steps], ["Create the app", "Set the redirect"])
        self.assertTrue(all(step["source_url"] == DOC_URL for step in result.steps))


class AssembleTests(unittest.TestCase):
    def _research(self, steps: list[dict]) -> ResearchResult:
        facts = {
            "auth.oauth.authorization_url": {"value": "https://example.com/oauth/authorize",
                                             "quote": "https://example.com/oauth/authorize", "source_url": DOC_URL},
            "auth.oauth.token_url": {"value": "https://example.com/oauth/token",
                                     "quote": "https://example.com/oauth/token", "source_url": DOC_URL},
            "auth.oauth.pkce": {"value": "true", "quote": "PKCE is required", "source_url": DOC_URL},
        }
        scopes = [{"name": "read", "description": "Read data", "quote": "read", "source_url": DOC_URL,
                   "sensitivity": "normal"}]
        return ResearchResult(docs=[document()], discovery=None, facts=facts, scopes=scopes,
                              unknown_fields=[], conflicts=[], notes=[], chunks=[], steps=steps)

    def test_complete_when_steps_and_endpoints_exist(self) -> None:
        provider = Registry().provider("github")
        steps = [{"title": "Create the app", "detail": "Open the console.",
                  "quote": "Open the provider console and create a new OAuth app.", "source_url": DOC_URL}]
        spec = assemble(provider, resolution(provider), self._research(steps), "fake-model")
        self.assertEqual(validate_spec(spec), [])
        self.assertEqual(spec["status"], "complete")
        self.assertEqual(len(spec["setup"]["steps"]), 1)
        self.assertEqual(len(spec["setup"]["verification"]), 2)
        self.assertEqual(spec["setup"]["time"]["confidence"], "low")
        self.assertGreater(len(spec["setup"]["blockers"]), 0)
        self.assertNotIn("PARTIAL_GUIDE", [warning["code"] for warning in spec["warnings"]])

    def test_partial_without_steps(self) -> None:
        provider = Registry().provider("github")
        spec = assemble(provider, resolution(provider), self._research([]), "fake-model")
        self.assertEqual(validate_spec(spec), [])
        self.assertEqual(spec["status"], "partial")
        self.assertIn("PARTIAL_GUIDE", [warning["code"] for warning in spec["warnings"]])
