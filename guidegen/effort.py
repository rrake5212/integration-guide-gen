"""Time estimates: class base bands, provider overrides, docs friction flags.

These are heuristics, not facts. The output is always a range + likely value + drivers and
confidence. Confidence stays ``low`` until that class is calibrated by running the setup once.
"""

from __future__ import annotations

from .registry import Provider

# provider-specific base minutes (min, likely, max); falls back to the class band
PROVIDER_BASE = {
    "google": (20, 30, 45),
    "github": (10, 15, 25),
    "slack": (20, 35, 60),
    "notion": (15, 25, 40),
    "meta": (45, 90, 180),
    "tally": (5, 10, 15),
    "tally-erp": (10, 20, 30),
}

CLASS_BASE = {
    "api_key_only": (5, 10, 15),
    "self_serve_oauth": (15, 25, 40),
    "approval_gated": (45, 90, 180),
    "oidc_only": (15, 30, 60),
    "no_public_api": (0, 0, 0),
    "unknown": (None, None, None),
}

# (docs phrase, driver label, review days if it applies)
FRICTION = (
    ("sensitive scope", "sensitive scopes may need review", 7),
    ("restricted scope", "restricted scopes may need review", 7),
    ("business verification", "business verification", 3),
    ("app review", "app review", 3),
    ("admin consent", "workspace admin consent", 1),
    ("workspace", "workspace admin may be involved", 0),
    ("test users", "test users must be added", 0),
    ("paid plan", "a paid plan may be required", 0),
    ("billing account", "billing may be required", 0),
    ("sandbox", "sandbox setup", 0),
)


def _sensitive_scopes(spec: dict) -> bool:
    oauth = (spec.get("auth") or {}).get("oauth") or {}
    return any((scope.get("sensitivity") or "") == "sensitive" for scope in oauth.get("scopes", []))


def estimate(provider: Provider, spec: dict, docs_text: str) -> dict:
    availability = (spec.get("resolution") or {}).get("availability") or provider.availability
    if availability == "unknown":
        base = CLASS_BASE["unknown"]
    else:
        base = PROVIDER_BASE.get(provider.id) or CLASS_BASE.get(availability) or CLASS_BASE["unknown"]
    min_minutes, likely, max_minutes = base

    lowered = (docs_text or "").lower()
    drivers: list[str] = []
    review_days = 0
    for phrase, driver, days in FRICTION:
        if phrase in lowered:
            if phrase in ("sensitive scope", "restricted scope") and not _sensitive_scopes(spec):
                continue
            if phrase == "app review" and not (availability == "approval_gated" or _sensitive_scopes(spec)):
                continue
            if driver not in drivers:
                drivers.append(driver)
            review_days = max(review_days, days)

    if availability == "approval_gated":
        drivers.append("provider approval")
        review_days = max(review_days, 3)
    if availability in ("self_serve_oauth", "approval_gated", "oidc_only"):
        drivers.append("consent screen")
    if (spec.get("auth") or {}).get("api_key"):
        drivers.append("find the right workspace and role")

    if min_minutes is None:
        return {
            "likely_minutes": None,
            "range_min_minutes": None,
            "range_max_minutes": None,
            "review_wait_days": None,
            "drivers": drivers,
            "confidence": "none",
        }

    # friction adds a little wall-clock time but never changes the class band wildly
    extra = min(15, 5 * max(0, len(drivers) - 1))
    likely = likely + extra
    max_minutes = max_minutes + extra
    min_minutes = min(min_minutes, likely)
    max_minutes = max(max_minutes, likely)

    return {
        "likely_minutes": likely,
        "range_min_minutes": min_minutes,
        "range_max_minutes": max_minutes,
        "review_wait_days": review_days,
        "drivers": sorted(set(drivers)),
        "confidence": "low" if availability != "no_public_api" else "none",
    }
