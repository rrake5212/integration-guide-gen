import json
import unittest
from unittest import mock

from guidegen import probe
from guidegen.errors import GuideError


def _metadata(authorization_endpoint="https://auth.example.com/authorize", **extra):
    body = {
        "issuer": "https://auth.example.com",
        "authorization_endpoint": authorization_endpoint,
        "token_endpoint": "https://auth.example.com/token",
    }
    body.update(extra)
    return json.dumps(body).encode()


class ProbeCandidateTests(unittest.TestCase):
    """The probe must look at both well-known documents, on the root and the usual auth hosts."""

    def setUp(self) -> None:
        self.calls: list[str] = []
        patcher = mock.patch.object(probe, "host_status", return_value="public")
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fake_get(self, table):
        def _get(url, timeout):
            self.calls.append(url)
            status, body = table.get(url, (404, b""))
            return status, body, url

        return _get

    # 1 — OIDC on the root still wins first
    def test_openid_configuration_on_the_root(self) -> None:
        table = {"https://example.com/.well-known/openid-configuration": (200, _metadata())}
        with mock.patch.object(probe, "_get", self._fake_get(table)):
            info = probe.probe_domain("example.com")
        self.assertTrue(info["discovery"])
        self.assertEqual(info["metadata"], "oidc")
        self.assertEqual(info["discovery_url"], "https://example.com/.well-known/openid-configuration")
        self.assertEqual(info["metadata_host"], "example.com")

    # 2 — the Prisma case: RFC 8414 metadata on a separate auth host
    def test_oauth_authorization_server_metadata_on_the_auth_host(self) -> None:
        table = {
            "https://auth.example.com/.well-known/oauth-authorization-server": (200, _metadata()),
        }
        with mock.patch.object(probe, "_get", self._fake_get(table)):
            info = probe.probe_domain("example.com")
        self.assertTrue(info["discovery"])
        self.assertEqual(info["metadata"], "oauth-authorization-server")
        self.assertEqual(info["metadata_host"], "auth.example.com")
        self.assertEqual(info["authorization_endpoint"], "https://auth.example.com/authorize")
        # root documents were tried before the subdomain sweep
        self.assertEqual(
            self.calls[:2],
            [
                "https://example.com/.well-known/openid-configuration",
                "https://example.com/.well-known/oauth-authorization-server",
            ],
        )

    # 3 — nothing anywhere: still reports homepage reachability
    def test_no_metadata_falls_back_to_homepage_status(self) -> None:
        table = {"https://example.com/": (200, b"<html></html>")}
        with mock.patch.object(probe, "_get", self._fake_get(table)):
            info = probe.probe_domain("example.com")
        self.assertFalse(info["discovery"])
        self.assertEqual(info["homepage_status"], 200)

    # 4 — a 200 body that is not metadata must not count as discovery
    def test_json_without_an_authorization_endpoint_is_ignored(self) -> None:
        table = {
            "https://example.com/.well-known/openid-configuration": (
                200,
                json.dumps({"issuer": "https://example.com"}).encode(),
            ),
            "https://example.com/": (200, b"<html></html>"),
        }
        with mock.patch.object(probe, "_get", self._fake_get(table)):
            info = probe.probe_domain("example.com")
        self.assertFalse(info["discovery"])

    # 5 — guessed hosts that do not resolve are skipped, not fatal
    def test_unresolved_guessed_host_is_skipped(self) -> None:
        def status(host: str) -> str:
            return "public" if host == "example.com" else "unresolved"

        table = {"https://example.com/": (200, b"<html></html>")}
        with mock.patch.object(probe, "host_status", side_effect=status):
            with mock.patch.object(probe, "_get", self._fake_get(table)):
                info = probe.probe_domain("example.com")
        self.assertFalse(info["discovery"])
        self.assertTrue(all("example.com" == c.split("/")[2] for c in self.calls))

    # 6 — a private guessed host is never fetched
    def test_private_guessed_host_is_never_requested(self) -> None:
        def status(host: str) -> str:
            return "public" if host == "example.com" else "blocked"

        table = {"https://example.com/": (200, b"<html></html>")}
        with mock.patch.object(probe, "host_status", side_effect=status):
            with mock.patch.object(probe, "_get", self._fake_get(table)):
                probe.probe_domain("example.com")
        self.assertTrue(all("example.com" == c.split("/")[2] for c in self.calls))

    # 7 — the original host is still SSRF-checked up front
    def test_blocked_host_raises_before_any_request(self) -> None:
        with mock.patch.object(probe, "host_status", return_value="blocked"):
            with self.assertRaises(GuideError) as ctx:
                probe.probe_domain("127.0.0.1")
        self.assertEqual(ctx.exception.code, "HOST_BLOCKED")
        self.assertEqual(self.calls, [])

    # 8 — candidate order is root-first, both documents, then the auth hosts
    def test_candidate_order(self) -> None:
        pairs = list(probe._candidates("example.com"))
        self.assertEqual(pairs[0], ("example.com", probe.OIDC_PATH))
        self.assertEqual(pairs[1], ("example.com", probe.OAUTH_AS_PATH))
        self.assertEqual(pairs[2], ("auth.example.com", probe.OIDC_PATH))
        self.assertEqual(pairs[3], ("auth.example.com", probe.OAUTH_AS_PATH))
        self.assertEqual(len(pairs), 2 + 2 * len(probe.METADATA_SUBDOMAINS))


if __name__ == "__main__":
    unittest.main()
