"""Assemble quote-verified research into a provider.spec.json and validate it.

This is deliberately deterministic: no model call here. Missing facts become explicit
null facts plus an entry in ``unknown_fields``; the spec validator recomputes the quality
counters and refuses to pass if the ledger and the document disagree.
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import quote

from .blockers import select_blockers
from .effort import estimate
from .errors import WARNINGS
from .extract import FACT_PATHS
from .registry import Provider
from .research import ResearchResult
from .resolve import Resolution
from .spec import SpecError, assert_valid, walk_facts

PROMPT_VERSION = "p2"


def _null() -> dict:
    return {"value": None, "quote": None, "source_url": None}


def _fact(facts: dict[str, dict], path: str) -> dict:
    raw = facts.get(path)
    if raw is None:
        return _null()
    kind = FACT_PATHS[path][0]
    value = raw["value"]
    if kind == "bool":
        value = str(value).lower() == "true"
    return {"value": value, "quote": raw["quote"], "source_url": raw["source_url"]}


def _env_prefix(provider: Provider) -> str:
    return provider.id.upper().replace("-", "_")


def _verification(oauth: dict | None, env: list[dict]) -> list[dict]:
    if not oauth:
        return []
    auth_url = (oauth.get("authorization_url") or {}).get("value")
    token_url = (oauth.get("token_url") or {}).get("value")
    if not auth_url or not token_url or len(env) < 2:
        return []
    client_id, client_secret = env[0]["name"], env[1]["name"]
    scopes = [scope["name"]["value"] for scope in oauth.get("scopes", [])][:6]
    scope_param = f"&scope={quote(' '.join(scopes))}" if scopes else ""
    joiner = "&" if "?" in auth_url else "?"
    auth_command = (f'open "{auth_url}{joiner}client_id=${{{client_id}}}'
                    f'&redirect_uri=${{REDIRECT_URI}}&response_type=code{scope_param}"')
    token_command = (f'curl -sS -X POST "{token_url}" -H "Accept: application/json" '
                     f'-d "client_id=${{{client_id}}}" -d "client_secret=${{{client_secret}}}" '
                     f'-d "code=$CODE" -d "redirect_uri=$REDIRECT_URI" -d "grant_type=authorization_code"')
    return [
        {
            "order": 1,
            "method": "browser",
            "command": auth_command,
            "expected": "The provider shows its consent screen; after approval the browser lands on "
                        "your redirect URI with a ?code= parameter.",
        },
        {
            "order": 2,
            "method": "curl",
            "command": token_command,
            "expected": "A JSON response containing access_token (and refresh_token if you requested "
                        "offline access).",
        },
    ]


def assemble(provider: Provider, resolution: Resolution, research: ResearchResult,
             model: str | None) -> dict:
    facts = research.facts
    has_oauth = any(path in facts for path in FACT_PATHS if path.startswith("auth.oauth."))
    has_api_key = any(path in facts for path in FACT_PATHS if path.startswith("auth.api_key."))

    availability = provider.availability
    notes: list[str] = []
    endpoints_ok = ("auth.oauth.authorization_url" in facts and "auth.oauth.token_url" in facts)
    if availability in ("self_serve_oauth", "approval_gated") and not endpoints_ok:
        availability = "unknown"
        notes.append("Authorization/token endpoints were not found in the docs; availability left unknown.")
    if availability == "oidc_only" and (research.discovery is None or not endpoints_ok):
        availability = "unknown"
        notes.append("OIDC discovery did not yield both endpoints; availability left unknown.")

    methods: list[str] = []
    primary: str | None = None
    oauth = None
    api_key = None
    env: list[dict] = []

    if availability == "api_key_only" or (availability not in ("self_serve_oauth", "approval_gated", "oidc_only") and has_api_key):
        methods = ["api_key"]
        primary = "api_key"
        api_key = {
            "where_to_create_url": _fact(facts, "auth.api_key.where_to_create_url"),
            "header_name": _fact(facts, "auth.api_key.header_name"),
            "prefix": _fact(facts, "auth.api_key.prefix"),
            "env_var": _env_prefix(provider) + "_API_KEY",
            "rotation": _fact(facts, "auth.api_key.rotation"),
        }
        env = [{"name": api_key["env_var"], "description": "API key", "required": True, "secret": True}]
    elif availability in ("self_serve_oauth", "approval_gated", "oidc_only") or (availability == "unknown" and has_oauth):
        methods = ["oidc"] if availability == "oidc_only" else ["oauth2"]
        primary = methods[0]
        discovery_value = _null()
        if research.discovery is not None:
            discovery_value = {
                "value": provider.discovery or research.discovery.url,
                "quote": provider.discovery or research.discovery.url,
                "source_url": research.discovery.url,
            }
        oauth = {
            "authorization_url": _fact(facts, "auth.oauth.authorization_url"),
            "token_url": _fact(facts, "auth.oauth.token_url"),
            "discovery_url": discovery_value,
            "scopes": [
                {
                    "name": {"value": scope["name"], "quote": scope["quote"], "source_url": scope["source_url"]},
                    "description": {"value": scope["description"], "quote": scope["quote"], "source_url": scope["source_url"]},
                    "sensitivity": scope["sensitivity"],
                }
                for scope in research.scopes
            ],
            "pkce": _fact(facts, "auth.oauth.pkce"),
            "client_secret_required": _fact(facts, "auth.oauth.client_secret_required"),
            "refresh_token": _fact(facts, "auth.oauth.refresh_token"),
            "redirect_uri_rules": _fact(facts, "auth.oauth.redirect_uri_rules"),
            "consent_screen": _fact(facts, "auth.oauth.consent_screen"),
        }
        env = [
            {"name": _env_prefix(provider) + "_CLIENT_ID", "description": "OAuth client id", "required": True, "secret": False},
            {"name": _env_prefix(provider) + "_CLIENT_SECRET", "description": "OAuth client secret", "required": True, "secret": True},
        ]
    elif availability == "no_public_api":
        methods = ["none"]

    sources: list[dict] = []
    seen: set[str] = set()
    for document in research.docs:
        if document.url not in seen:
            sources.append(document.source())
            seen.add(document.url)
    if research.discovery is not None and research.discovery.url not in seen:
        sources.append(research.discovery.source())

    steps = [
        {"order": index, "title": step["title"], "detail": step["detail"], "url": step["source_url"]}
        for index, step in enumerate(research.steps, start=1)
    ]

    spec = {
        "schema_version": "1.0",
        "generator": {"name": "guidegen", "version": "0.2.0", "model": model,
                      "prompt_version": PROMPT_VERSION},
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "partial",
        "provider": {
            "id": provider.id,
            "name": provider.name,
            "domains": list(provider.domains),
            "docs": list(provider.docs),
            "console": provider.console,
            "discovery": provider.discovery,
            "note": provider.note,
        },
        "resolution": {
            "input": resolution.raw,
            "normalized": resolution.normalized,
            "kind": resolution.kind,
            "decision": resolution.decision,
            "matched_by": resolution.matched_by or "pick",
            "confidence": resolution.confidence,
            "availability": availability,
            "alternatives": [],
            "notes": list(resolution.notes),
        },
        "auth": {"methods": methods, "primary": primary, "oauth": oauth, "api_key": api_key},
        "setup": {
            "time": {"likely_minutes": None, "range_min_minutes": None, "range_max_minutes": None,
                     "review_wait_days": None, "drivers": [], "confidence": "none"},
            "prerequisites": [], "steps": steps, "verification": [], "blockers": [],
        },
        "env": env,
        "sources": sources,
        "quality": {"facts_total": 0, "facts_verified": 0, "unknown_count": 0,
                    "confidence": "medium"},
        "unknown_fields": [],
        "warnings": [],
        "notes": (["Steps and time are generated from the provider's docs; time is a heuristic."]
                  + notes + research.conflicts),
    }

    docs_text = "\n".join(document.text for document in research.docs)
    spec["setup"]["time"] = estimate(provider, spec, docs_text)
    spec["setup"]["verification"] = _verification(oauth, env)
    spec["setup"]["blockers"] = select_blockers(spec, docs_text)

    guide_ok = bool(steps) and bool(sources) and (endpoints_ok if oauth else has_api_key)
    spec["status"] = "complete" if guide_ok else "partial"

    all_facts = list(walk_facts(spec))
    unknown_paths = sorted(path for path, fact in all_facts if fact["value"] is None)
    spec["unknown_fields"] = unknown_paths
    spec["quality"].update(
        facts_total=len(all_facts),
        facts_verified=sum(1 for _, fact in all_facts if fact["value"] is not None),
        unknown_count=len(unknown_paths),
        confidence="medium" if len(all_facts) - len(unknown_paths) >= 4 else "low",
    )

    warnings = []
    if spec["setup"]["time"]["likely_minutes"] is None:
        warnings.append({"code": "TIME_UNKNOWN", "message": WARNINGS["TIME_UNKNOWN"]})
    if unknown_paths:
        warnings.append({"code": "FIELD_UNVERIFIED", "message": WARNINGS["FIELD_UNVERIFIED"]})
    if any(conflict.startswith(("auth.oauth.authorization_url", "auth.oauth.token_url"))
           for conflict in research.conflicts):
        warnings.append({"code": "ENDPOINT_CONFLICT", "message": WARNINGS["ENDPOINT_CONFLICT"]})
    if spec["status"] != "complete":
        warnings.append({"code": "PARTIAL_GUIDE", "message": WARNINGS["PARTIAL_GUIDE"]})
    spec["warnings"] = warnings

    try:
        assert_valid(spec)
    except SpecError as exc:
        raise SpecError([f"assembly produced an invalid spec: {issue}" for issue in exc.issues]) from exc
    return spec
