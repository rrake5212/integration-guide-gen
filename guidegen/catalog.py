"""Route any app name to its catalogue record, and guess its domain.

Gate A (approved 2026-10-29): the 1,688-row toolkit snapshot is the ROUTER. It answers
"does this app exist, what is its canonical name, which auth families does it support"
for free, offline, with no curated row required. It is still never a fact source — the
guide's facts keep coming from the provider's own docs, quoted (PLAN §10/§13.2).

A catalogue row has no domain, and the FIND stage needs one. Domain inference is a
separate, verified step: propose hosts from the slug/name, then accept only a host that
answers 200 and names the brand on its own page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

AUTH_FAMILY_ORDER = ("oauth2", "s2s_oauth2", "oauth1", "api_key", "bearer_token", "basic", "jwt")


@dataclass(frozen=True)
class CatalogEntry:
    slug: str
    name: str
    auth_families: tuple[str, ...]
    tools: int = 0
    triggers: int = 0
    managed: str | None = None

    @property
    def availability(self) -> str:
        """Provisional availability from the auth families, never a verified verdict."""
        if any(f.startswith(("oauth2", "s2s_oauth2", "oauth1")) for f in self.auth_families):
            return "self_serve_oauth"
        if "api_key" in self.auth_families:
            return "api_key"
        return "unknown"

    @property
    def label(self) -> str:
        names = {
            "oauth2": "OAuth 2.0",
            "s2s_oauth2": "OAuth 2.0 (server-to-server)",
            "oauth1": "OAuth 1.0",
            "api_key": "API keys",
            "bearer_token": "bearer tokens",
            "basic": "basic auth",
            "jwt": "JWT",
        }
        return " + ".join(names.get(f, f) for f in self.auth_families) or "unknown"

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "name": self.name,
            "auth_families": list(self.auth_families),
            "availability": self.availability,
            "tools": self.tools,
            "triggers": self.triggers,
        }


def parse_auth(raw: object) -> tuple[str, ...]:
    """``"OAUTH2, API_KEY"`` -> ``("oauth2", "api_key")``, ordered and de-duped."""
    if not raw:
        return ()
    if isinstance(raw, (list, tuple)):
        parts = [str(x) for x in raw]
    else:
        parts = re.split(r"[,;|/]+", str(raw))
    seen: list[str] = []
    for part in parts:
        value = re.sub(r"[\s-]+", "_", part.strip().lower())
        if value and value not in seen:
            seen.append(value)
    return tuple(sorted(seen, key=lambda v: AUTH_FAMILY_ORDER.index(v) if v in AUTH_FAMILY_ORDER else 99))


def _token(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def domain_candidates(name: str, slug: str) -> list[str]:
    """Cheap host proposals from the catalogue's own name/slug. Verified later."""
    tokens: list[str] = []
    for raw in (slug, name):
        token = _token(raw)
        if token and token not in tokens:
            tokens.append(token)
    hyphen = re.sub(r"\s+", "-", name.strip().lower()) if name else ""
    if hyphen and _token(hyphen) and hyphen not in tokens:
        tokens.append(hyphen)
    out: list[str] = []
    for token in tokens[:2]:
        for tld in (".com", ".io", ".app", ".co"):
            host = f"{token}{tld}"
            if host not in out:
                out.append(host)
    return out[:6]


def first_party(host: str, names: list[str] | tuple[str, ...]) -> bool:
    """True when ``host`` looks owned by one of ``names`` (registrable-label match)."""
    labels = host.lower().split(".")
    label = labels[0]
    if label in {"www", "docs", "developer", "developers", "api", "app", "auth", "login", "help", "support", "platform"} and len(labels) > 2:
        label = labels[1]
    for name in names:
        if not name:
            continue
        token = _token(str(name).split(".")[0])
        if token and token == _token(label):
            return True
    return False


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


@dataclass
class RouteOutcome:
    query: str
    decision: str  # "curated" | "catalogue" | "domain" | "unknown"
    entry: CatalogEntry | None = None
    provider_id: str | None = None
    domains: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "decision": self.decision,
            "catalogue": self.entry.to_dict() if self.entry else None,
            "provider_id": self.provider_id,
            "domains": list(self.domains),
            "notes": list(self.notes),
        }
