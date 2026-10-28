"""Regenerate the synthetic spec fixtures.

The fixtures are structural examples, not real provider data. Quality counters and the
unknown-field ledger are computed with the same walker the validator uses, so the
fixtures cannot drift from the rules.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from guidegen.spec import walk_facts  # noqa: E402

HERE = Path(__file__).resolve().parent
DOC = "https://example.com/docs/oauth"
SC = "https://example.com/docs/scopes"


def F(value, quote, url):  # noqa: N802 - tiny fixture helper
    return {"value": value, "quote": quote, "source_url": url}


def finish(spec: dict) -> dict:
    unknown = sorted(path for path, fact in walk_facts(spec) if fact["value"] is None)
    spec["unknown_fields"] = unknown
    facts = list(walk_facts(spec))
    spec["quality"].update(
        facts_total=len(facts),
        facts_verified=sum(1 for _, fact in facts if fact["value"] is not None),
        unknown_count=len(unknown),
    )
    return spec


def oauth_spec() -> dict:
    sources = [
        {"url": DOC, "title": "Acme Notes OAuth", "fetched_at": "2026-10-27T22:30:00Z",
         "http_status": 200, "doc_hash": "sha256:" + "a" * 64},
        {"url": SC, "title": "Acme Notes scopes", "fetched_at": "2026-10-27T22:30:00Z",
         "http_status": 200, "doc_hash": "sha256:" + "b" * 64},
    ]
    return finish({
        "schema_version": "1.0",
        "generator": {"name": "guidegen", "version": "0.1.0",
                      "model": "deepseek-ai/DeepSeek-V4.1-Flash-1017", "prompt_version": "p1"},
        "generated_at": "2026-10-27T22:30:00Z",
        "status": "complete",
        "provider": {"id": "acme-notes", "name": "Acme Notes", "domains": ["acme-notes.example"],
                     "docs": [DOC, SC], "console": "https://example.com/console", "discovery": None,
                     "note": "Synthetic fixture for schema tests."},
        "resolution": {"input": "acme-notes.example", "normalized": "acme-notes.example", "kind": "domain",
                       "decision": "resolved", "matched_by": "domain", "confidence": "high",
                       "availability": "self_serve_oauth", "alternatives": [], "notes": []},
        "auth": {
            "methods": ["oauth2"], "primary": "oauth2",
            "oauth": {
                "authorization_url": F("https://example.com/oauth/authorize",
                                       "Send users to https://example.com/oauth/authorize.", DOC),
                "token_url": F("https://example.com/oauth/token",
                               "Exchange the code at https://example.com/oauth/token.", DOC),
                "discovery_url": F(None, None, None),
                "scopes": [
                    {"name": F("read", "The read scope grants read-only access to notes.", SC),
                     "description": F("Read your notes.", "Read your notes.", SC), "sensitivity": "normal"},
                    {"name": F("write", "The write scope lets the app create and edit notes.", SC),
                     "description": F("Create and edit notes.", "Create and edit notes.", SC),
                     "sensitivity": "sensitive"},
                ],
                "pkce": F(True, "PKCE is required for every authorization request.", DOC),
                "client_secret_required": F(True, "Every app must present a client secret at the token endpoint.", DOC),
                "refresh_token": F(True, "Refresh tokens are issued with the authorization code.", DOC),
                "redirect_uri_rules": F("Must be https and match the registered URI exactly.",
                                        "The redirect URI must be https and match the registered URI exactly.", DOC),
                "consent_screen": F("Users see a consent screen listing the requested scopes.",
                                    "Users see a consent screen listing the requested scopes.", DOC),
            },
            "api_key": None,
        },
        "setup": {
            "time": {"likely_minutes": 20, "range_min_minutes": 10, "range_max_minutes": 30,
                     "review_wait_days": 0, "drivers": ["create the app", "copy the credentials"],
                     "confidence": "high"},
            "prerequisites": [
                F("An Acme Notes account", "You need an Acme Notes account.", DOC),
                F("A verified workspace", "Only verified workspaces can create apps.", DOC),
            ],
            "steps": [
                {"order": 1, "title": "Open the developer console",
                 "detail": "Go to the Acme Notes developer console and sign in.", "url": "https://example.com/console"},
                {"order": 2, "title": "Create an app",
                 "detail": "Choose Create app, name it, and set the redirect URI.", "url": "https://example.com/console"},
                {"order": 3, "title": "Copy the credentials",
                 "detail": "Copy the client id and client secret into your environment.", "url": None},
                {"order": 4, "title": "Authorize a test user",
                 "detail": "Send a test user through the authorization URL and exchange the code.", "url": None},
            ],
            "verification": [
                {"order": 1, "method": "curl",
                 "command": "curl -s -X POST https://example.com/oauth/token -d grant_type=authorization_code",
                 "expected": "A JSON body with access_token and refresh_token."},
                {"order": 2, "method": "browser", "command": None,
                 "expected": "The consent screen lists read and write."},
            ],
            "blockers": [
                {"id": "redirect_uri_mismatch", "symptom": "invalid_redirect_uri on authorize",
                 "cause": "The URI differs from the registered one.",
                 "fix": "Register the exact https URI, including the path.",
                 "severity": "blocker", "likelihood": "common"},
                {"id": "missing_secret", "symptom": "invalid_client at the token endpoint",
                 "cause": "The client secret was not sent.", "fix": "Send the secret in the request body.",
                 "severity": "blocker", "likelihood": "occasional"},
            ],
        },
        "env": [
            {"name": "ACME_CLIENT_ID", "description": "OAuth client id", "required": True, "secret": False},
            {"name": "ACME_CLIENT_SECRET", "description": "OAuth client secret", "required": True, "secret": True},
        ],
        "sources": sources,
        "quality": {"facts_total": 0, "facts_verified": 0, "unknown_count": 0, "confidence": "high"},
        "unknown_fields": [],
        "warnings": [{"code": "FIELD_UNVERIFIED",
                      "message": "Some details weren't stated in the docs and are marked as such."}],
        "notes": ["Synthetic fixture for schema tests; not a real provider."],
    })


def apikey_spec() -> dict:
    doc = "https://example.com/docs/api"
    return finish({
        "schema_version": "1.0",
        "generator": {"name": "guidegen", "version": "0.1.0", "model": None, "prompt_version": "p1"},
        "generated_at": "2026-10-27T22:30:00Z",
        "status": "partial",
        "provider": {"id": "acme-keys", "name": "Acme Keys", "domains": ["acme-keys.example"],
                     "docs": [doc], "console": "https://example.com/keys", "discovery": None,
                     "note": "Synthetic fixture for schema tests."},
        "resolution": {"input": "acme-keys.example", "normalized": "acme-keys.example", "kind": "domain",
                       "decision": "resolved", "matched_by": "domain", "confidence": "high",
                       "availability": "api_key_only", "alternatives": [], "notes": []},
        "auth": {
            "methods": ["api_key"], "primary": "api_key", "oauth": None,
            "api_key": {
                "where_to_create_url": F("https://example.com/keys",
                                         "Create keys at https://example.com/keys.", doc),
                "header_name": F("Authorization", "Send the key in the Authorization header.", doc),
                "prefix": F("Bearer", "Prefix the key with Bearer.", doc),
                "env_var": "ACME_API_KEY",
                "rotation": F(None, None, None),
            },
        },
        "setup": {
            "time": {"likely_minutes": None, "range_min_minutes": None, "range_max_minutes": None,
                     "review_wait_days": None, "drivers": [], "confidence": "none"},
            "prerequisites": [F("An Acme Keys account", "You need an Acme Keys account.", doc)],
            "steps": [
                {"order": 1, "title": "Create a key",
                 "detail": "Open the keys page and choose Create key.", "url": "https://example.com/keys"},
                {"order": 2, "title": "Store the key",
                 "detail": "Put the key in ACME_API_KEY on the server.", "url": None},
            ],
            "verification": [
                {"order": 1, "method": "curl",
                 "command": "curl -s https://api.example.com/whoami -H \"Authorization: Bearer $ACME_API_KEY\"",
                 "expected": "A JSON body with your account id."},
            ],
            "blockers": [
                {"id": "key_revoked", "symptom": "401 on every call",
                 "cause": "The key was revoked or rotated.",
                 "fix": "Create a new key and update the environment.",
                 "severity": "blocker", "likelihood": "occasional"},
            ],
        },
        "env": [{"name": "ACME_API_KEY", "description": "API key", "required": True, "secret": True}],
        "sources": [{"url": doc, "title": "Acme Keys API", "fetched_at": "2026-10-27T22:30:00Z",
                     "http_status": 200, "doc_hash": "sha256:" + "c" * 64}],
        "quality": {"facts_total": 0, "facts_verified": 0, "unknown_count": 0, "confidence": "low"},
        "unknown_fields": [],
        "warnings": [
            {"code": "TIME_UNKNOWN",
             "message": "Setup time is an estimate: the docs don't state review timelines."},
            {"code": "FIELD_UNVERIFIED",
             "message": "Some details weren't stated in the docs and are marked as such."},
        ],
        "notes": ["Synthetic fixture for schema tests; not a real provider."],
    })


def main() -> None:
    for name, spec in (("example-oauth.spec.json", oauth_spec()),
                       ("example-apikey.spec.json", apikey_spec())):
        (HERE / name).write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {name}: {spec['quality']}")


if __name__ == "__main__":
    main()
