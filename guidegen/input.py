"""Parse and validate the user's query before anything else touches it.

The query is either a website/domain or a free-text app name. Rules:

* at most 200 characters; no control characters, bidi overrides or zero-width characters;
* a URL must be http/https, carry no username/password, and use port 80/443 or none;
* a host must be a registrable domain: lowercase, IDNA, at least one dot, a real TLD,
  and never an IP literal or a ``.local`` / ``.internal`` style name;
* bare ``host/path`` input is accepted (``tally.so/settings/api`` -> ``tally.so``);
* everything else is treated as an app name and left to the resolver.

``host_status`` is the SSRF gate used by the probe: it resolves the host and rejects
private, loopback, link-local, multicast, reserved and unspecified addresses. It is a
check-then-fetch, so it also re-checks every redirect hop in ``probe.py``.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from .errors import GuideError

MAX_INPUT_CHARS = 200
BAD_CHARS_RE = re.compile(r"[\x00-\x1f\x7f\u200b-\u200f\u202a-\u202e\ufeff]")
SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*):")
HOST_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")
DENY_SCHEMES = {"file", "javascript", "data", "mailto", "ftp", "ftps", "ws", "wss", "about", "chrome", "blob"}
INTERNAL_SUFFIXES = (".local", ".internal", ".home.arpa", ".localhost", ".lan", ".intranet")


@dataclass(frozen=True)
class Query:
    raw: str
    kind: str  # "domain" | "text"
    value: str


def parse_query(raw: str | None) -> Query:
    if not isinstance(raw, str):
        raise GuideError("INPUT_EMPTY", "input is not a string")
    text = raw.strip()
    if not text:
        raise GuideError("INPUT_EMPTY", "blank input")
    if len(text) > MAX_INPUT_CHARS:
        raise GuideError("INPUT_TOO_LONG", f"{len(text)} chars > {MAX_INPUT_CHARS}")
    if BAD_CHARS_RE.search(text):
        raise GuideError("INPUT_CONTROL_CHARS", "control/zero-width characters in input")
    if text.startswith("//"):
        raise GuideError("INPUT_BAD_HOST", "scheme-relative URL")

    if "://" in text:
        return _parse_url(text)

    scheme = SCHEME_RE.match(text)
    if scheme and scheme.group(1).lower() in DENY_SCHEMES:
        raise GuideError("INPUT_BAD_SCHEME", f"scheme {scheme.group(1)!r}")

    candidate = re.split(r"[/?#]", text, 1)[0]
    if candidate.lower() == "localhost":
        raise GuideError("INPUT_BAD_HOST", "localhost")
    if candidate and ("." in candidate and " " not in candidate):
        return Query(raw=raw, kind="domain", value=_normalize_host(candidate))

    return Query(raw=raw, kind="text", value=" ".join(text.split()))


def _parse_url(text: str) -> Query:
    parts = urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise GuideError("INPUT_BAD_SCHEME", f"scheme {scheme!r}")
    if parts.username or parts.password:
        raise GuideError("INPUT_CREDENTIALS", "userinfo in URL")
    host = parts.hostname
    if not host:
        raise GuideError("INPUT_BAD_HOST", "URL without a host")
    normalized = _normalize_host(host)
    try:
        port = parts.port
    except ValueError as exc:
        raise GuideError("INPUT_BAD_PORT", "non-numeric port") from exc
    if port not in (None, 80, 443):
        raise GuideError("INPUT_BAD_PORT", f"port {port}")
    return Query(raw=text, kind="domain", value=normalized)


def _normalize_host(host: str) -> str:
    value = host.strip().strip(".").lower()
    if value.startswith("www."):
        value = value[4:]
    if not value:
        raise GuideError("INPUT_BAD_HOST", "empty host")
    try:
        value = value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise GuideError("INPUT_BAD_HOST", "IDNA conversion failed") from exc
    if len(value) > 253:
        raise GuideError("INPUT_BAD_HOST", "host longer than 253 chars")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise GuideError("INPUT_BAD_HOST", "IP literal")
    if not HOST_RE.match(value):
        raise GuideError("INPUT_BAD_HOST", "not a registrable domain")
    tld = value.rsplit(".", 1)[1]
    if not (tld.startswith("xn--") or (tld.isalpha() and len(tld) >= 2)):
        raise GuideError("INPUT_BAD_HOST", f"bad TLD {tld!r}")
    if value == "localhost" or value.endswith(INTERNAL_SUFFIXES):
        raise GuideError("INPUT_BAD_HOST", "internal-only host")
    return value


def _ip_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return ip.is_global and not ip.is_multicast


def host_status(host: str) -> str:
    """Return 'public', 'blocked' or 'unresolved' for a host name or IP literal."""
    value = host.strip().strip(".").lower()
    if not value:
        return "blocked"
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        return "public" if _ip_public(ip) else "blocked"

    try:
        infos = socket.getaddrinfo(value, None, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        return "unresolved"
    addresses = []
    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(info[4][0]))
        except ValueError:
            return "blocked"
    if not addresses:
        return "unresolved"
    return "public" if all(_ip_public(ip) for ip in addresses) else "blocked"


def assert_public_host(host: str) -> None:
    status = host_status(host)
    if status == "blocked":
        raise GuideError("HOST_BLOCKED", f"{host!r} resolves to a private or reserved address")
    if status == "unresolved":
        raise GuideError("DNS_FAILED", f"{host!r} does not resolve")
