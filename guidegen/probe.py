"""Live probe for a host: is there OAuth/OIDC discovery, and is the site reachable?

Two discovery documents are tried, because providers publish either one:

* ``/.well-known/openid-configuration`` — OIDC Discovery 1.0
* ``/.well-known/oauth-authorization-server`` — OAuth 2.0 Authorization Server Metadata
  (RFC 8414). Plenty of SaaS products publish only this, which is why an OIDC-only probe
  reports "no OAuth surface" for a provider that has a perfectly good one (Prisma is the
  case that exposed it: ``auth.prisma.io/.well-known/oauth-authorization-server`` is 200,
  ``/.well-known/openid-configuration`` 404s).

The metadata server is often **not** the site root. Root first, then the usual auth hosts
(``auth.``, ``login.``, ``accounts.``, ``id.``). Every candidate is SSRF-checked; a guessed
host that does not resolve is skipped, never fatal.

Deliberately stdlib-only and read-only. It asserts nothing about client registration:
discovery proves the provider runs OAuth/OIDC, not that you can self-register an app.

SSRF guard: ``assert_public_host`` rejects private/loopback/link-local/metadata addresses
before the first request, and ``_GuardedRedirectHandler`` re-checks every redirect hop and
refuses non-https redirects. The body is capped at 200 KB and the timeout is bounded, and
the whole probe runs under a wall-clock budget so a dead subdomain cannot stall resolution.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .errors import GuideError
from .input import assert_public_host, host_status

USER_AGENT = "oauth-guide-gen/0.1 (setup-guide resolver)"
MAX_REDIRECTS = 3
MAX_BODY_BYTES = 200_000

OIDC_PATH = "/.well-known/openid-configuration"
OAUTH_AS_PATH = "/.well-known/oauth-authorization-server"
METADATA_SUBDOMAINS = ("auth", "login", "accounts", "id")

# Wall clock for the whole probe. Root documents are tried first and are what most
# providers answer; the subdomain sweep only runs while there is budget left.
PROBE_BUDGET_SECONDS = 6.0
REQUEST_TIMEOUT = 2.5


class _GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001 - urllib signature
        parts = urlsplit(newurl)
        if parts.scheme != "https":
            raise GuideError("HOST_BLOCKED", f"redirect to non-https URL: {newurl[:120]}")
        host = parts.hostname
        if not host:
            raise GuideError("HOST_BLOCKED", "redirect without a host")
        assert_public_host(host)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_GuardedRedirectHandler())


def _get(url: str, timeout: float) -> tuple[int, bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with _OPENER.open(req, timeout=timeout) as resp:
        return resp.status, resp.read(MAX_BODY_BYTES), resp.geturl()


def _candidates(host: str):
    """(host, path) pairs in priority order: root documents, then the usual auth hosts."""
    yield host, OIDC_PATH
    yield host, OAUTH_AS_PATH
    for sub in METADATA_SUBDOMAINS:
        candidate = f"{sub}.{host}"
        yield candidate, OIDC_PATH
        yield candidate, OAUTH_AS_PATH


def _metadata(url: str, body: bytes, candidate_host: str) -> dict | None:
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    authorization_endpoint = data.get("authorization_endpoint")
    if not authorization_endpoint:
        return None
    return {
        "discovery": True,
        "discovery_url": url,
        "metadata": "oidc" if url.endswith(OIDC_PATH) else "oauth-authorization-server",
        "metadata_host": candidate_host,
        "issuer": data.get("issuer"),
        "authorization_endpoint": authorization_endpoint,
        "token_endpoint": data.get("token_endpoint"),
        "scopes_supported": data.get("scopes_supported"),
        "code_challenge_methods_supported": data.get("code_challenge_methods_supported"),
    }


def probe_domain(host: str, timeout: float = REQUEST_TIMEOUT) -> dict:
    assert_public_host(host)
    deadline = time.monotonic() + PROBE_BUDGET_SECONDS

    for candidate_host, path in _candidates(host):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if candidate_host != host and host_status(candidate_host) != "public":
            continue  # a guessed host that is private or does not resolve is simply absent
        url = f"https://{candidate_host}{path}"
        try:
            status, body, _ = _get(url, min(timeout, remaining))
        except GuideError:
            raise
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError):
            continue
        if status != 200:
            continue
        found = _metadata(url, body, candidate_host)
        if found:
            return found

    try:
        status, _, final_url = _get(f"https://{host}/", timeout)
        return {"discovery": False, "homepage_status": status, "homepage_url": final_url}
    except GuideError:
        raise
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        return {"discovery": False, "homepage_status": None, "error": type(exc).__name__}
