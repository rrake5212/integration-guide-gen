"""Live probe for an unknown host: is there OIDC discovery, and is the site reachable?

Deliberately stdlib-only and read-only. It asserts nothing about client registration:
discovery proves the provider runs OIDC, not that you can self-register an app.

SSRF guard: ``assert_public_host`` rejects private/loopback/link-local/metadata addresses
before the first request, and ``_GuardedRedirectHandler`` re-checks every redirect hop and
refuses non-https redirects. The body is capped at 200 KB and the timeout is bounded.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .errors import GuideError
from .input import assert_public_host

USER_AGENT = "oauth-guide-gen/0.1 (setup-guide resolver)"
MAX_REDIRECTS = 3
MAX_BODY_BYTES = 200_000


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


def probe_domain(host: str, timeout: float = 8.0) -> dict:
    assert_public_host(host)
    discovery_url = f"https://{host}/.well-known/openid-configuration"
    try:
        status, body, _ = _get(discovery_url, timeout)
        if status == 200:
            data = json.loads(body.decode("utf-8", "replace"))
            if data.get("authorization_endpoint") and data.get("token_endpoint"):
                return {
                    "discovery": True,
                    "discovery_url": discovery_url,
                    "issuer": data.get("issuer"),
                    "authorization_endpoint": data.get("authorization_endpoint"),
                    "token_endpoint": data.get("token_endpoint"),
                }
    except GuideError:
        raise
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError, OSError):
        pass

    try:
        status, _, final_url = _get(f"https://{host}/", timeout)
        return {"discovery": False, "homepage_status": status, "homepage_url": final_url}
    except GuideError:
        raise
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as exc:
        return {"discovery": False, "homepage_status": None, "error": type(exc).__name__}
