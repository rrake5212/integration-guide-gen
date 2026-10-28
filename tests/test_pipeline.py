import os
import unittest
from unittest.mock import patch

from guidegen.assemble import assemble
from guidegen.errors import GuideError
from guidegen.extract import (OAUTH_PATHS, extract_chunk, extract_scopes, looks_like_scope_table,
                              schema_for)
from guidegen.fetch import Document
from guidegen.llm import LLMClient, LLMResult
from guidegen.registry import Registry
from guidegen.research import ResearchResult
from guidegen.resolve import Resolution
from guidegen.spec import validate_spec

DOC = "https://example.com/docs"
TEXT = """Authorization endpoint: https://example.com/oauth/authorize
Token endpoint: https://example.com/oauth/token
PKCE is required for every app.
The client secret is required at the token exchange.
Refresh tokens are issued with offline access.
The redirect URI must exactly match the registered URI.
Users see a consent screen listing the requested scopes."""

SCOPE_TEXT = "## Available scopes\n" + "\n".join(
    f"| `scope{i}` | Grants access to resource {i} |" for i in range(8)
)


class FakeClient:
    model = "fake-model"

    def __init__(self, data: dict) -> None:
        self.data = data
        self.calls: list[dict] = []

    def chat_json(self, messages, schema, schema_name, max_tokens=3000, temperature=0.0) -> LLMResult:
        self.calls.append({"schema": schema, "name": schema_name, "messages": messages})
        return LLMResult(data=self.data, model=self.model, ms=5, usage={"total_tokens": 10}, attempts=1)


def facts_payload(**overrides) -> dict:
    payload = {path: {"value": None, "quote": None} for path in OAUTH_PATHS}
    payload.update(overrides)
    return payload


def good_payload() -> dict:
    return facts_payload(**{
        "auth.oauth.authorization_url": {
            "value": "https://example.com/oauth/authorize",
            "quote": "Authorization endpoint: https://example.com/oauth/authorize",
        },
        "auth.oauth.token_url": {
            "value": "https://example.com/oauth/token",
            "quote": "Token endpoint: https://example.com/oauth/token",
        },
        "auth.oauth.pkce": {"value": "true", "quote": "PKCE is required for every app."},
        "auth.oauth.client_secret_required": {
            "value": "true", "quote": "The client secret is required at the token exchange.",
        },
        "auth.oauth.refresh_token": {
            "value": "true", "quote": "Refresh tokens are issued with offline access.",
        },
        "auth.oauth.redirect_uri_rules": {
            "value": "Must match exactly.",
            "quote": "The redirect URI must exactly match the registered URI.",
        },
    })


class ExtractTests(unittest.TestCase):
    def test_schema_for_only_contains_requested_paths(self) -> None:
        schema = schema_for(OAUTH_PATHS)
        self.assertEqual(schema["required"], OAUTH_PATHS)
        self.assertEqual(set(schema["properties"]), set(OAUTH_PATHS))
        self.assertNotIn("auth.api_key.rotation", schema["properties"])

    def test_extract_returns_quote_verified_facts(self) -> None:
        result = extract_chunk(FakeClient(good_payload()), "Example", TEXT, DOC, OAUTH_PATHS)
        self.assertEqual(result.facts["auth.oauth.pkce"]["value"], "true")
        self.assertEqual(len(result.facts), 6)
        self.assertEqual(result.unknown_fields, ["auth.oauth.consent_screen"])

    def test_fabricated_quote_is_dropped(self) -> None:
        payload = good_payload()
        payload["auth.oauth.token_url"] = {
            "value": "https://example.com/oauth/token",
            "quote": "This sentence is not in the document.",
        }
        result = extract_chunk(FakeClient(payload), "Example", TEXT, DOC, OAUTH_PATHS)
        self.assertNotIn("auth.oauth.token_url", result.facts)
        self.assertIn("auth.oauth.token_url", result.unknown_fields)

    def test_url_must_appear_in_text(self) -> None:
        payload = good_payload()
        payload["auth.oauth.token_url"] = {
            "value": "https://evil.example/token",
            "quote": "Token endpoint: https://example.com/oauth/token",
        }
        result = extract_chunk(FakeClient(payload), "Example", TEXT, DOC, OAUTH_PATHS)
        self.assertNotIn("auth.oauth.token_url", result.facts)

    def test_scope_table_detection(self) -> None:
        self.assertTrue(looks_like_scope_table(SCOPE_TEXT))
        self.assertFalse(looks_like_scope_table(TEXT))
        self.assertFalse(looks_like_scope_table("scope " * 50))

    def test_extract_scopes(self) -> None:
        data = {"scopes": [
            {"name": "scope0", "description": "Grants access to resource 0",
             "quote": "| `scope0` | Grants access to resource 0 |"},
            {"name": "scope1", "description": "Grants access to resource 1",
             "quote": "| `scope1` | Grants access to resource 1 |"},
        ]}
        result = extract_scopes(FakeClient(data), "Example", SCOPE_TEXT, DOC)
        self.assertEqual([scope["name"] for scope in result.scopes], ["scope0", "scope1"])
        self.assertEqual(result.scopes[0]["source_url"], DOC)


class AssembleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = Registry().provider("github")
        self.resolution = Resolution(
            raw="github", normalized="github", kind="text", decision="resolved",
            provider=self.provider, matched_by="alias", confidence="high",
            availability=self.provider.availability,
        )
        self.document = Document(url=DOC, title="Docs", text=TEXT, raw=TEXT,
                                 fetched_at="2026-10-27T23:00:00Z", http_status=200,
                                 sha256="a" * 64)
        self.extraction = extract_chunk(FakeClient(good_payload()), "Example", TEXT, DOC, OAUTH_PATHS)
        scope_data = {"scopes": [
            {"name": "read", "description": "Read access", "quote": "| `scope0` | Grants access to resource 0 |"},
        ]}
        self.scope_result = extract_scopes(FakeClient(scope_data), "Example", SCOPE_TEXT, DOC)

    def _research(self, facts=None, scopes=None) -> ResearchResult:
        return ResearchResult(
            docs=[self.document], discovery=None,
            facts=facts if facts is not None else self.extraction.facts,
            scopes=scopes if scopes is not None else [],
            unknown_fields=[],
        )

    def test_valid_partial_spec(self) -> None:
        spec = assemble(self.provider, self.resolution, self._research(), "fake-model")
        self.assertEqual(validate_spec(spec), [])
        self.assertEqual(spec["status"], "partial")
        self.assertEqual(spec["auth"]["oauth"]["authorization_url"]["value"],
                         "https://example.com/oauth/authorize")
        self.assertEqual([item["name"] for item in spec["env"]],
                         ["GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET"])
        self.assertEqual(spec["unknown_fields"], ["auth.oauth.consent_screen", "auth.oauth.discovery_url"])

    def test_missing_endpoints_degrade_to_unknown(self) -> None:
        facts = {"auth.oauth.pkce": {"value": "true", "quote": "PKCE is required for every app.",
                                     "source_url": DOC}}
        spec = assemble(self.provider, self.resolution, self._research(facts=facts), "fake-model")
        self.assertEqual(validate_spec(spec), [])
        self.assertEqual(spec["resolution"]["availability"], "unknown")
        self.assertIsNone(spec["auth"]["oauth"]["authorization_url"]["value"])

    def test_scope_facts_are_counted(self) -> None:
        spec = assemble(self.provider, self.resolution, self._research(scopes=self.scope_result.scopes),
                        "fake-model")
        self.assertEqual(len(spec["auth"]["oauth"]["scopes"]), 1)
        self.assertEqual(spec["quality"]["facts_total"], spec["quality"]["facts_verified"] + spec["quality"]["unknown_count"])


class ClientConfigTests(unittest.TestCase):
    def test_missing_key_raises_clear_error(self) -> None:
        with patch.dict(os.environ, {"LLM_PROVIDER": "deepinfra"}, clear=True):
            with self.assertRaises(GuideError) as ctx:
                LLMClient.from_env()
        self.assertEqual(ctx.exception.code, "LLM_NOT_CONFIGURED")


if __name__ == "__main__":
    unittest.main()
