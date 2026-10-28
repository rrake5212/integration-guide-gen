import json
import unittest
from unittest.mock import patch

from guidegen.errors import GuideError, spec, user_payload
from guidegen.extract import OAUTH_PATHS, schema_for
from guidegen.llm import LLMClient
from guidegen.pipeline import build_spec
from guidegen.registry import Registry
from guidegen.research import research


class FakeClient:
    model = "fake"
    session_id = "s"

    def chat_json(self, *args, **kwargs):
        raise AssertionError("no model call expected")


class ErrorPathTests(unittest.TestCase):
    def assert_code(self, ctx, code: str) -> None:
        self.assertEqual(ctx.exception.code, code)
        payload = user_payload(code)
        self.assertEqual(payload["error"]["message"], spec(code).user_message)
        self.assertNotIn("detail", payload["error"])
        self.assertNotIn("hint", payload["error"])

    def test_docs_403_maps_to_docs_forbidden(self) -> None:
        provider = Registry().provider("github")
        with patch("guidegen.research.fetch_document", side_effect=GuideError("DOCS_FORBIDDEN")):
            with self.assertRaises(GuideError) as ctx:
                research(provider, FakeClient(), max_chunks=1)
        self.assert_code(ctx, "DOCS_FORBIDDEN")

    def test_docs_429_maps_to_docs_rate_limited(self) -> None:
        provider = Registry().provider("github")
        with patch("guidegen.research.fetch_document", side_effect=GuideError("DOCS_RATE_LIMITED")):
            with self.assertRaises(GuideError) as ctx:
                research(provider, FakeClient(), max_chunks=1)
        self.assert_code(ctx, "DOCS_RATE_LIMITED")

    def _llm(self) -> LLMClient:
        return LLMClient(provider="deepinfra", base_url="https://example.invalid/v1/openai",
                         api_key="test-key", model="test-model", session_id="ses_test")

    def test_schema_invalid_output_is_llm_bad_output(self) -> None:
        client = self._llm()
        body = json.dumps({"choices": [{"message": {"content": json.dumps({"auth.oauth.pkce": {"value": "true"}})}}]})
        with patch.object(client, "_post", return_value=(body, 1, 1)):
            with self.assertRaises(GuideError) as ctx:
                client.chat_json([{"role": "user", "content": "x"}], schema_for(OAUTH_PATHS), "facts")
        self.assert_code(ctx, "LLM_BAD_OUTPUT")

    def test_refusal_field_is_llm_refused(self) -> None:
        client = self._llm()
        body = json.dumps({"choices": [{"message": {"refusal": "I can't help with that."}}]})
        with patch.object(client, "_post", return_value=(body, 1, 1)):
            with self.assertRaises(GuideError) as ctx:
                client.chat_json([{"role": "user", "content": "x"}], schema_for(OAUTH_PATHS), "facts")
        self.assert_code(ctx, "LLM_REFUSED")

    def test_refusal_text_is_llm_refused(self) -> None:
        client = self._llm()
        body = json.dumps({"choices": [{"message": {"content": "I cannot read this page."}}]})
        with patch.object(client, "_post", return_value=(body, 1, 1)):
            with self.assertRaises(GuideError) as ctx:
                client.chat_json([{"role": "user", "content": "x"}], schema_for(OAUTH_PATHS), "facts")
        self.assert_code(ctx, "LLM_REFUSED")

    def test_ambiguous_name_never_calls_model(self) -> None:
        with self.assertRaises(GuideError) as ctx:
            build_spec("tally")
        self.assert_code(ctx, "AMBIGUOUS_PROVIDER")

    def test_blocked_host_fails_before_network(self) -> None:
        with self.assertRaises(GuideError) as ctx:
            build_spec("http://127.0.0.1")
        self.assert_code(ctx, "INPUT_BAD_HOST")

    def test_unknown_provider_has_clean_verdict(self) -> None:
        with self.assertRaises(GuideError) as ctx:
            build_spec("does-not-exist.invalid")
        self.assert_code(ctx, "PROVIDER_UNKNOWN")
