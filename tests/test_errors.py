import unittest

from guidegen.errors import ERRORS, STAGES, GuideError, spec, user_payload

FORBIDDEN_IN_USER_TEXT = (
    "llm",
    "schema",
    "token",
    "api key",
    "sqlite",
    "stack",
    "traceback",
    "prompt",
    "exception",
    "http",
    "json",
    "model",
)


class ErrorTaxonomyTests(unittest.TestCase):
    def test_every_code_is_complete(self) -> None:
        for code, s in ERRORS.items():
            with self.subTest(code=code):
                self.assertEqual(code, s.code)
                self.assertIn(s.stage, STAGES)
                self.assertGreaterEqual(s.status, 400)
                self.assertLess(s.status, 600)
                self.assertTrue(s.user_message.endswith("."))
                self.assertTrue(s.operator_hint)

    def test_user_messages_do_not_leak_internals(self) -> None:
        for code, s in ERRORS.items():
            low = s.user_message.lower()
            with self.subTest(code=code):
                for word in FORBIDDEN_IN_USER_TEXT:
                    self.assertNotIn(word, low)
                self.assertNotIn("_", s.user_message)

    def test_user_payload_has_no_operator_detail(self) -> None:
        payload = user_payload("DOCS_FORBIDDEN", job_id="job-123")
        self.assertEqual(set(payload["error"]), {"code", "stage", "message", "retryable", "job_id"})
        self.assertNotIn("operator_hint", payload["error"])
        self.assertNotIn("detail", payload["error"])

    def test_unknown_code_fails_loudly(self) -> None:
        with self.assertRaises(KeyError):
            spec("NOT_A_CODE")

    def test_guide_error_carries_code(self) -> None:
        err = GuideError("JOB_TIMEOUT", "worker exceeded 120s at stage=extract")
        self.assertEqual(err.code, "JOB_TIMEOUT")
        self.assertEqual(err.spec.status, 504)
        self.assertIn("JOB_TIMEOUT", str(err))


if __name__ == "__main__":
    unittest.main()
