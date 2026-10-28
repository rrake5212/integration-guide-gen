import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from guidegen.cli import main
from guidegen.errors import GuideError
from guidegen.input import assert_public_host, host_status, parse_query
from guidegen.resolve import Resolver


class ParseQueryTests(unittest.TestCase):
    def assert_code(self, raw, code: str) -> None:
        with self.assertRaises(GuideError) as ctx:
            parse_query(raw)
        self.assertEqual(ctx.exception.code, code)

    def test_empty_and_whitespace(self) -> None:
        self.assert_code("", "INPUT_EMPTY")
        self.assert_code("   ", "INPUT_EMPTY")
        self.assert_code(None, "INPUT_EMPTY")

    def test_too_long(self) -> None:
        self.assert_code("a" * 201, "INPUT_TOO_LONG")

    def test_control_and_zero_width_chars(self) -> None:
        self.assert_code("tally\x00.so", "INPUT_CONTROL_CHARS")
        self.assert_code("tally\u200b.so", "INPUT_CONTROL_CHARS")

    def test_non_web_schemes(self) -> None:
        self.assert_code("file:///etc/passwd", "INPUT_BAD_SCHEME")
        self.assert_code("javascript:alert(1)", "INPUT_BAD_SCHEME")
        self.assert_code("data:text/html,hi", "INPUT_BAD_SCHEME")
        self.assert_code("mailto:a@b.com", "INPUT_BAD_SCHEME")

    def test_credentials_are_rejected(self) -> None:
        self.assert_code("https://user:pass@example.com", "INPUT_CREDENTIALS")

    def test_bad_ports(self) -> None:
        self.assert_code("https://tally.so:8443/x", "INPUT_BAD_PORT")
        self.assert_code("https://tally.so:abc/x", "INPUT_BAD_PORT")

    def test_internal_and_ip_hosts(self) -> None:
        self.assert_code("http://localhost:8080", "INPUT_BAD_HOST")
        self.assert_code("http://127.0.0.1", "INPUT_BAD_HOST")
        self.assert_code("http://169.254.169.254/latest/meta-data", "INPUT_BAD_HOST")
        self.assert_code("http://printer.local", "INPUT_BAD_HOST")
        self.assert_code("//example.com", "INPUT_BAD_HOST")
        self.assert_code("https://exa mple.com", "INPUT_BAD_HOST")

    def test_domains(self) -> None:
        self.assertEqual(parse_query("tally.so").value, "tally.so")
        self.assertEqual(parse_query("https://api.github.com/user").value, "api.github.com")
        self.assertEqual(parse_query("http://www.tally.so/x").value, "tally.so")
        self.assertEqual(parse_query("tally.so/settings/api").value, "tally.so")
        self.assertEqual(parse_query("https://example.com.").value, "example.com")

    def test_idn(self) -> None:
        self.assertEqual(parse_query("https://bücher.example").value, "xn--bcher-kva.example")

    def test_text(self) -> None:
        self.assertEqual(parse_query("google sheets").kind, "text")
        self.assertEqual(parse_query("  Tally Forms  ").value, "Tally Forms")
        self.assertEqual(parse_query("notin").kind, "text")


class HostStatusTests(unittest.TestCase):
    def test_private_and_reserved_ips_are_blocked(self) -> None:
        for host in ("127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254",
                     "100.64.0.1", "0.0.0.0", "224.0.0.1"):
            with self.subTest(host=host):
                self.assertEqual(host_status(host), "blocked")

    def test_public_ips(self) -> None:
        self.assertEqual(host_status("8.8.8.8"), "public")
        self.assertEqual(host_status("1.1.1.1"), "public")

    def test_unresolved(self) -> None:
        self.assertEqual(host_status("does-not-exist.invalid"), "unresolved")

    @patch("guidegen.input.socket.getaddrinfo")
    def test_domain_resolving_private_is_blocked(self, fake) -> None:
        fake.return_value = [(2, 1, 6, "", ("10.0.0.5", 0))]
        self.assertEqual(host_status("internal.example"), "blocked")

    @patch("guidegen.input.socket.getaddrinfo")
    def test_domain_resolving_public(self, fake) -> None:
        fake.return_value = [(2, 1, 6, "", ("93.184.216.34", 0))]
        self.assertEqual(host_status("example.com"), "public")

    def test_assert_public_host(self) -> None:
        with self.assertRaises(GuideError) as ctx:
            assert_public_host("127.0.0.1")
        self.assertEqual(ctx.exception.code, "HOST_BLOCKED")
        with self.assertRaises(GuideError) as ctx:
            assert_public_host("does-not-exist.invalid")
        self.assertEqual(ctx.exception.code, "DNS_FAILED")


class ResolverInputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.resolver = Resolver(probe=None)

    def test_bad_inputs_raise_guide_errors(self) -> None:
        cases = {
            "": "INPUT_EMPTY",
            "file:///etc/passwd": "INPUT_BAD_SCHEME",
            "http://localhost:8080": "INPUT_BAD_HOST",
            "https://user:pass@example.com": "INPUT_CREDENTIALS",
            "https://tally.so:8443": "INPUT_BAD_PORT",
        }
        for raw, code in cases.items():
            with self.subTest(raw=raw), self.assertRaises(GuideError) as ctx:
                self.resolver.resolve(raw)
            self.assertEqual(ctx.exception.code, code)

    def test_good_inputs_still_resolve(self) -> None:
        self.assertEqual(self.resolver.resolve("tally.so").provider.id, "tally")
        self.assertEqual(self.resolver.resolve("google sheets").provider.id, "google")
        self.assertEqual(self.resolver.resolve("http://www.tally.so/settings/api").provider.id, "tally")


class CliErrorTests(unittest.TestCase):
    def test_empty_input_is_clean(self) -> None:
        err = StringIO()
        with redirect_stderr(err):
            code = main([""])
        self.assertEqual(code, 4)
        self.assertIn("Enter an app name", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())

    def test_file_scheme_is_json_and_leaks_no_internals(self) -> None:
        out = StringIO()
        with redirect_stdout(out):
            code = main(["file:///etc/passwd", "--json"])
        self.assertEqual(code, 4)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["error"]["code"], "INPUT_BAD_SCHEME")
        self.assertEqual(set(payload["error"]), {"code", "stage", "message", "retryable"})
        self.assertNotIn("Traceback", out.getvalue())


if __name__ == "__main__":
    unittest.main()
