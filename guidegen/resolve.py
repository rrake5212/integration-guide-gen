"""Resolve a free-text app name or website/domain to an OAuth provider.

Rules, in order (cheapest signal first):

1. domain match (``tally.so`` -> Tally, ``api.github.com`` -> GitHub)
2. exact alias match on provider names / product aliases (``google sheets`` -> Google)
3. exact toolkit-name match -> slug prefix -> provider (``GOOGLESHEETS`` -> Google)
4. fuzzy match; a tie is a STOP, never a guess (bare ``tally`` -> ask forms vs ERP)
5. unknown domain: optional live probe (OIDC discovery) -> ``oidc_only`` or ``unknown``

``ask`` and ``unknown`` are first-class outcomes. The resolver never silently picks a
provider when the evidence is ambiguous.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass, field

from .input import assert_public_host, parse_query
from .registry import Provider, Registry

FUZZY_FLOOR = 0.80
TIE_MARGIN = 0.05


def _clean(text: str) -> str:
    return " ".join(text.lower().replace("_", " ").split())


@dataclass
class Candidate:
    id: str
    name: str
    domain: str | None
    availability: str
    score: float
    matched_by: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "domain": self.domain,
            "availability": self.availability,
            "score": round(self.score, 3),
            "matched_by": self.matched_by,
        }


@dataclass
class Resolution:
    raw: str
    normalized: str
    kind: str  # "domain" | "text"
    decision: str  # "resolved" | "ask" | "unknown"
    provider: Provider | None
    matched_by: str | None
    confidence: str  # "high" | "medium" | "low" | "none"
    availability: str
    alternatives: list[Candidate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return {"resolved": 0, "ask": 2, "unknown": 3}[self.decision]

    def to_dict(self) -> dict:
        return {
            "input": self.raw,
            "normalized": self.normalized,
            "kind": self.kind,
            "decision": self.decision,
            "matched_by": self.matched_by,
            "confidence": self.confidence,
            "provider": self.provider.to_dict() if self.provider else None,
            "alternatives": [c.to_dict() for c in self.alternatives],
            "oauth_availability": self.availability,
            "notes": list(self.notes),
        }


class Resolver:
    def __init__(self, registry: Registry | None = None, probe=None) -> None:
        self.registry = registry or Registry()
        self.probe = probe

    # ------------------------------------------------------------------ public
    def resolve(self, raw: str) -> Resolution:
        query = parse_query(raw)
        if query.kind == "domain":
            return self._resolve_domain(raw, query.value)
        return self._resolve_text(raw, _clean(query.value))

    # ------------------------------------------------------------- normalising
    def _normalize(self, raw: str) -> tuple[str, str]:
        query = parse_query(raw)
        return query.kind, query.value

    # ------------------------------------------------------------------ domain
    def _match_domain(self, host: str) -> Provider | None:
        best: tuple[int, Provider] | None = None
        for provider in self.registry.providers.values():
            for domain in provider.domains:
                if host == domain or host.endswith("." + domain):
                    if best is None or len(domain) > best[0]:
                        best = (len(domain), provider)
        return best[1] if best else None

    def _resolve_domain(self, raw: str, host: str) -> Resolution:
        provider = self._match_domain(host)
        if provider:
            return Resolution(
                raw=raw,
                normalized=host,
                kind="domain",
                decision="resolved",
                provider=provider,
                matched_by="domain",
                confidence="high",
                availability=provider.availability,
            )

        notes = [f"no registry provider owns {host}"]
        if self.probe is None:
            notes.append("no live probe enabled; cannot verify OIDC/OAuth availability")
            return Resolution(raw, host, "domain", "unknown", None, None, "none", "unknown", [], notes)

        assert_public_host(host)
        info = self.probe(host) or {}
        if info.get("discovery"):
            synthetic = Provider(
                id=host,
                name=host,
                domains=(host,),
                aliases=(),
                slug_prefixes=(),
                availability="oidc_only",
                docs=(),
                discovery=info.get("discovery_url"),
                note="OIDC discovery found; self-serve client registration is not verified.",
            )
            notes.append("OIDC discovery found; client registration still needs verifying")
            return Resolution(
                raw, host, "domain", "resolved", synthetic, "discovery", "medium",
                "oidc_only", [], notes,
            )

        status = info.get("homepage_status")
        if status:
            notes.append(f"site reachable (HTTP {status}) but no OIDC discovery at /.well-known/openid-configuration")
        else:
            notes.append("site not reachable and no OIDC discovery")
        notes.append("no public OAuth/OIDC surface proven; treat as unknown until docs confirm")
        return Resolution(raw, host, "domain", "unknown", None, None, "none", "unknown", [], notes)

    # -------------------------------------------------------------------- text
    def _resolve_text(self, raw: str, text: str) -> Resolution:
        # 1. exact alias / provider-name match
        ids = self.registry.by_alias.get(text, set())
        if len(ids) == 1:
            provider = self.registry.provider(next(iter(ids)))
            return Resolution(raw, text, "text", "resolved", provider, "alias", "high", provider.availability)
        if len(ids) > 1:
            alts = [
                Candidate(
                    id=pid,
                    name=self.registry.provider(pid).name,
                    domain=self.registry.provider(pid).domains[0] if self.registry.provider(pid).domains else None,
                    availability=self.registry.provider(pid).availability,
                    score=1.0,
                    matched_by="alias",
                )
                for pid in sorted(ids)
            ]
            return Resolution(
                raw, text, "text", "ask", None, "alias", "none", "unknown", alts,
                [f"{len(alts)} providers claim the name {text!r}; pick one with --pick"],
            )

        # 2. exact toolkit name -> slug prefix -> provider
        slug = self.registry.toolkit_slug(text)
        if slug:
            provider = self.registry.provider_for_slug(slug)
            if provider:
                return Resolution(
                    raw, text, "text", "resolved", provider, "toolkit", "medium",
                    provider.availability, [], [f"matched toolkit {slug}"],
                )

        # 3. fuzzy
        scored: list[tuple[float, Provider, str]] = []
        for provider in self.registry.providers.values():
            best = (0.0, "")
            for candidate in (provider.name.lower(), *provider.aliases):
                ratio = difflib.SequenceMatcher(None, text, candidate).ratio()
                if ratio > best[0]:
                    best = (ratio, candidate)
            if best[0] >= FUZZY_FLOOR:
                scored.append((best[0], provider, best[1]))

        if not scored:
            return Resolution(
                raw, text, "text", "unknown", None, None, "none", "unknown", [],
                ["nothing in the registry is close; pass a website/domain so it can be probed"],
            )

        scored.sort(key=lambda row: -row[0])
        top_score, top_provider, top_candidate = scored[0]
        if len(scored) > 1 and (top_score - scored[1][0]) < TIE_MARGIN:
            alts = [
                Candidate(p.id, p.name, p.domains[0] if p.domains else None, p.availability, s, "fuzzy")
                for s, p, _ in scored[:3]
            ]
            return Resolution(
                raw, text, "text", "ask", None, "fuzzy", "none", "unknown", alts,
                [f"ambiguous name: {', '.join(c.name for c in alts)}; pick one with --pick"],
            )

        confidence = "medium" if top_score >= 0.90 else "low"
        return Resolution(
            raw, text, "text", "resolved", top_provider, "fuzzy", confidence,
            top_provider.availability, [], [f"fuzzy match {text!r} ~ {top_candidate!r} ({top_score:.2f})"],
        )
