"""Machine projections: provider.spec.json -> agent.json and agent.md.

Both are derived from the same spec as guide.md. `agent.json` keeps values + provenance;
`agent.md` is a paste-ready brief. Neither adds facts.
"""

from __future__ import annotations

from .spec import walk_facts


def _v(fact: dict | None):
    return None if not isinstance(fact, dict) else fact.get("value")


def _provenance(spec: dict) -> dict:
    out = {}
    for path, fact in walk_facts(spec):
        if fact.get("value") is not None and fact.get("source_url"):
            out[path] = fact["source_url"]
    return out


def render_agent_json(spec: dict) -> dict:
    auth = spec.get("auth") or {}
    oauth = auth.get("oauth")
    api_key = auth.get("api_key")
    oauth_out = None
    if oauth:
        oauth_out = {
            "authorization_url": _v(oauth["authorization_url"]),
            "token_url": _v(oauth["token_url"]),
            "discovery_url": _v(oauth["discovery_url"]),
            "pkce": _v(oauth["pkce"]),
            "client_secret_required": _v(oauth["client_secret_required"]),
            "refresh_token": _v(oauth["refresh_token"]),
            "redirect_uri_rules": _v(oauth["redirect_uri_rules"]),
            "consent_screen": _v(oauth["consent_screen"]),
            "scopes": [
                {"name": _v(scope["name"]), "description": _v(scope["description"]),
                 "sensitivity": scope.get("sensitivity")}
                for scope in oauth.get("scopes", [])
            ],
        }
    key_out = None
    if api_key:
        key_out = {
            "where_to_create_url": _v(api_key["where_to_create_url"]),
            "header_name": _v(api_key["header_name"]),
            "prefix": _v(api_key["prefix"]),
            "env_var": api_key["env_var"],
            "rotation": _v(api_key["rotation"]),
        }

    return {
        "schema_version": spec["schema_version"],
        "generator": spec["generator"],
        "generated_at": spec["generated_at"],
        "status": spec["status"],
        "provider": {
            "id": spec["provider"]["id"],
            "name": spec["provider"]["name"],
            "domains": spec["provider"]["domains"],
            "console": spec["provider"]["console"],
            "docs": spec["provider"]["docs"],
        },
        "availability": spec["resolution"]["availability"],
        "auth": {"methods": auth.get("methods", []), "primary": auth.get("primary"),
                 "oauth": oauth_out, "api_key": key_out},
        "env": spec.get("env", []),
        "setup": {
            "time": spec["setup"]["time"],
            "steps": spec["setup"].get("steps", []),
            "verification": spec["setup"].get("verification", []),
            "blockers": spec["setup"].get("blockers", []),
        },
        "unknown_fields": spec.get("unknown_fields", []),
        "warnings": spec.get("warnings", []),
        "notes": spec.get("notes", []),
        "sources": spec.get("sources", []),
        "provenance": _provenance(spec),
    }


def render_agent_md(spec: dict) -> str:
    provider = spec["provider"]
    time = spec["setup"]["time"]
    auth = spec.get("auth") or {}
    oauth = auth.get("oauth") or {}
    api_key = auth.get("api_key") or {}
    lines = [
        f"# {provider['name']} integration brief (for an agent)",
        "",
        "Use this as the source of truth. If a value is `null`, it was not stated in the fetched docs — ask the "
        "user or fetch the provider's docs again. Do not invent endpoints, scopes or console steps.",
        "",
        f"- Availability: `{spec['resolution']['availability']}`",
        f"- Status: `{spec['status']}`",
        f"- Time: {time['likely_minutes']} min likely "
        f"({time['range_min_minutes']}–{time['range_max_minutes']}), confidence `{time['confidence']}`"
        + (f", review wait ~{time['review_wait_days']} days" if time.get("review_wait_days") else ""),
        f"- Methods: {', '.join(auth.get('methods', [])) or 'none'} (primary: {auth.get('primary')})",
        "",
        "## Environment",
        "",
    ]
    env = spec.get("env") or []
    if env:
        for item in env:
            lines.append(f"- `{item['name']}` — {item['description']}"
                         + (" (secret)" if item["secret"] else "")
                         + (" (required)" if item["required"] else ""))
    else:
        lines.append("- none")
    lines.extend(["", "## Auth", ""])
    if oauth:
        lines.extend([
            "```text",
            f"authorization_url: {oauth['authorization_url'].get('value')}",
            f"token_url: {oauth['token_url'].get('value')}",
            f"discovery_url: {oauth['discovery_url'].get('value')}",
            f"pkce: {oauth['pkce'].get('value')}",
            f"client_secret_required: {oauth['client_secret_required'].get('value')}",
            f"refresh_token: {oauth['refresh_token'].get('value')}",
            "```",
            "",
            f"Redirect URI rules: {oauth['redirect_uri_rules'].get('value') or 'null'}",
            "",
            f"Consent screen: {oauth['consent_screen'].get('value') or 'null'}",
            "",
            "### Scopes",
            "",
        ])
        scopes = oauth.get("scopes") or []
        if scopes:
            for scope in scopes:
                lines.append(f"- `{scope['name']['value']}` — {scope['description']['value']} "
                             f"({scope.get('sensitivity', 'unknown')})")
        else:
            lines.append("- none extracted")
    elif api_key:
        lines.extend([
            "```text",
            f"where_to_create_url: {api_key['where_to_create_url'].get('value')}",
            f"header_name: {api_key['header_name'].get('value')}",
            f"prefix: {api_key['prefix'].get('value')}",
            f"env_var: {api_key['env_var']}",
            "```",
        ])
    else:
        lines.append("No public auth method was confirmed.")
    lines.extend(["", "## Setup steps", ""])
    steps = spec["setup"].get("steps") or []
    if not steps:
        lines.append("No console steps were extracted; this brief is partial.")
    else:
        for step in steps:
            lines.append(f"{step['order']}. **{step['title']}** — {step['detail']}"
                         + (f" (source: {step['url']})" if step.get("url") else ""))
    lines.extend(["", "## Verify", ""])
    verification = spec["setup"].get("verification") or []
    if not verification:
        lines.append("No verification command was templated.")
    else:
        for item in verification:
            lines.append(f"{item['order']}. {item['method']}: `{item.get('command') or ''}`")
            lines.append(f"   Expected: {item['expected']}")
    lines.extend(["", "## Known blockers", ""])
    blockers = spec["setup"].get("blockers") or []
    if not blockers:
        lines.append("No class blockers known.")
    else:
        for blocker in blockers:
            lines.append(f"- **{blocker['symptom']}** ({blocker['likelihood']}): {blocker['cause']} Fix: {blocker['fix']}")
    lines.extend(["", "## Unknowns", ""])
    unknown = spec.get("unknown_fields") or []
    if not unknown:
        lines.append("None.")
    else:
        for path in unknown:
            lines.append(f"- `{path}`")
    lines.extend(["", "## Sources", ""])
    for src in spec.get("sources", []):
        lines.append(f"- {src['url']} (fetched {src.get('fetched_at', '?')})")
    lines.append("")
    return "\n".join(lines)
