"""Human-readable projection: provider.spec.json -> guide.md.

Every value comes from the spec; a null fact is rendered as an explicit unknown, never guessed.
"""

from __future__ import annotations

UNKNOWN = "_Not stated in the docs._"


def _md(text: object) -> str:
    """Escape HTML in doc-derived text before it reaches a markdown renderer."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def value(fact: dict | None, default: str = UNKNOWN) -> str:
    if not isinstance(fact, dict):
        return default
    raw = fact.get("value")
    if raw is None:
        return default
    if isinstance(raw, bool):
        return "Yes" if raw else "No"
    return _md(raw)


def source(fact: dict | None) -> str:
    if isinstance(fact, dict) and fact.get("source_url"):
        return f" [source]({fact['source_url']})"
    return ""


def cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def _availability(spec: dict) -> str:
    return {
        "self_serve_oauth": "Self-serve OAuth app",
        "approval_gated": "Approval-gated OAuth app",
        "api_key_only": "API key only",
        "oidc_only": "OIDC only",
        "no_public_api": "No public API",
        "unknown": "Availability unclear",
    }.get(spec["resolution"]["availability"], spec["resolution"]["availability"])


def _time(spec: dict) -> str:
    time = spec["setup"]["time"]
    if time.get("likely_minutes") is None:
        return "Unknown — the docs do not give enough signal to estimate."
    line = (f"**{time['likely_minutes']} min likely** "
            f"(range {time['range_min_minutes']}–{time['range_max_minutes']} min)")
    if time.get("review_wait_days"):
        line += f", plus about **{time['review_wait_days']} days** of provider review"
    line += f". Confidence: **{time['confidence']}**."
    if time.get("drivers"):
        line += " Drivers: " + ", ".join(time["drivers"]) + "."
    return line


def _scopes(spec: dict) -> str:
    oauth = (spec.get("auth") or {}).get("oauth") or {}
    scopes = oauth.get("scopes") or []
    if not scopes:
        return "_No scopes were extracted. Choose them in the provider console for the APIs you call._"
    lines = ["| Scope | What it allows | Sensitivity | Source |", "|---|---|---|---|"]
    for scope in scopes:
        lines.append(
            f"| `{cell(value(scope['name']))}` | {cell(value(scope['description']))} | "
            f"{cell(scope.get('sensitivity', 'unknown'))} | [docs]({scope['name'].get('source_url')}) |"
        )
    return "\n".join(lines)


def _auth(spec: dict) -> str:
    auth = spec.get("auth") or {}
    if auth.get("oauth"):
        oauth = auth["oauth"]
        rows = [
            ("Authorization URL", f"`{value(oauth['authorization_url'])}`", source(oauth["authorization_url"])),
            ("Token URL", f"`{value(oauth['token_url'])}`", source(oauth["token_url"])),
            ("Discovery URL", f"`{value(oauth['discovery_url'])}`", source(oauth["discovery_url"])),
            ("PKCE", value(oauth["pkce"]), source(oauth["pkce"])),
            ("Client secret required", value(oauth["client_secret_required"]), source(oauth["client_secret_required"])),
            ("Refresh token", value(oauth["refresh_token"]), source(oauth["refresh_token"])),
            ("Redirect URI rules", value(oauth["redirect_uri_rules"]), source(oauth["redirect_uri_rules"])),
            ("Consent screen", value(oauth["consent_screen"]), source(oauth["consent_screen"])),
        ]
        lines = ["| Field | Value | Provenance |", "|---|---|---|"]
        for label, val, src in rows:
            lines.append(f"| {label} | {cell(val)} | {src or '—'} |")
        return "\n".join(lines)

    key = (auth.get("api_key") or {})
    rows = [
        ("Create key at", f"`{value(key['where_to_create_url'])}`", source(key["where_to_create_url"])),
        ("Header", f"`{value(key['header_name'])}`", source(key["header_name"])),
        ("Prefix", f"`{value(key['prefix'])}`", source(key["prefix"])),
        ("Rotation", value(key["rotation"]), source(key["rotation"])),
        ("Environment variable", f"`{key['env_var']}`", ""),
    ]
    lines = ["| Field | Value | Provenance |", "|---|---|---|"]
    for label, val, src in rows:
        lines.append(f"| {label} | {cell(val)} | {src or '—'} |")
    return "\n".join(lines)


def render_human(spec: dict) -> str:
    provider = spec["provider"]
    time = spec["setup"]["time"]
    warnings = spec.get("warnings") or []
    lines: list[str] = [
        f"# {provider['name']} integration setup",
        "",
        f"**Verdict:** {_availability(spec)} · **Status:** {spec['status']} · "
        f"**Resolution:** {spec['resolution']['confidence']} confidence ({spec['resolution']['matched_by']})",
        "",
        f"Generated {spec['generated_at']} from {len(spec['sources'])} source(s). "
        "Console steps are quoted from the provider's docs; the console may have changed.",
        "",
        "## Time and prerequisites",
        "",
        _time(spec),
        "",
    ]

    env = spec.get("env") or []
    if env:
        lines.extend(["| Environment variable | Required | Secret | Purpose |", "|---|---|---|---|"])
        for item in env:
            lines.append(f"| `{cell(item['name'])}` | {'yes' if item['required'] else 'no'} | "
                         f"{'yes' if item['secret'] else 'no'} | {cell(item['description'])} |")
        lines.append("")

    prereqs = spec["setup"].get("prerequisites") or []
    if prereqs:
        lines.extend(["### Before you start", ""])
        for item in prereqs:
            lines.append(f"- {value(item)}")
        lines.append("")

    if provider.get("console"):
        lines.extend([f"**Console:** <{provider['console']}>", ""])

    lines.extend(["## Setup steps", ""])
    steps = spec["setup"].get("steps") or []
    if not steps:
        lines.extend(["_No console steps were extracted. Use the provider's own setup page; this guide is partial._", ""])
    else:
        for step in steps:
            lines.append(f"### {step['order']}. {_md(step['title'])}")
            lines.append("")
            lines.append(_md(step["detail"]))
            if step.get("url"):
                lines.append("")
                lines.append(f"_Source: {step['url']}_")
            lines.append("")

    lines.extend(["## Verify", ""])
    verification = spec["setup"].get("verification") or []
    if not verification:
        lines.extend(["_No verification command could be templated from this spec._", ""])
    else:
        for item in verification:
            lines.append(f"**{item['order']}. {item['method'].upper()}**")
            lines.append("")
            if item.get("command"):
                lines.extend(["```bash", item["command"], "```", ""])
            lines.append(f"Expected: {item['expected']}")
            lines.append("")

    lines.extend(["## Auth details", "", _auth(spec), "", "## Scopes", "", _scopes(spec), ""])

    lines.extend(["## If it breaks", ""])
    blockers = spec["setup"].get("blockers") or []
    if not blockers:
        lines.extend(["_No blockers are known for this class yet._", ""])
    else:
        lines.extend(["| Symptom | Cause | Fix | Likelihood |", "|---|---|---|---|"])
        for blocker in blockers:
            lines.append(
                f"| **{cell(blocker['symptom'])}** | {cell(blocker['cause'])} | {cell(blocker['fix'])} | "
                f"{cell(blocker['likelihood'])} |"
            )
        lines.append("")

    unknown = spec.get("unknown_fields") or []
    if unknown:
        lines.extend(["## What we could not verify", "",
                      "These fields were not stated clearly enough in the fetched docs. They are deliberately blank; "
                      "do not fill them from memory.", ""])
        for path in unknown:
            lines.append(f"- `{path}`")
        lines.append("")

    if warnings:
        lines.extend(["## Warnings", ""])
        for warning in warnings:
            lines.append(f"- **{warning['code']}** — {warning['message']}")
        lines.append("")

    lines.extend(["## Sources", ""])
    for src in spec["sources"]:
        lines.append(f"- [{_md(src.get('title') or src['url'])}]({src['url']}) — fetched {src.get('fetched_at', '?')}, "
                     f"`sha256:{str(src.get('sha256', ''))[:12]}`")
    lines.append("")
    lines.extend([
        "---",
        f"Generated by `{spec['generator']['name']}` {spec['generator']['version']} · "
        f"model `{spec['generator'].get('model') or 'unknown'}` · prompt `{spec['generator']['prompt_version']}`.",
        "Time is a heuristic until this class is calibrated by running the setup once.",
    ])
    return "\n".join(lines)
