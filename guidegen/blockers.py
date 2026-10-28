"""Blocker rules: curated by auth class, selected against the assembled spec.

Curated rules carry no provider quote; they are class knowledge, not provider facts. Provider-specific
overrides live in ``PROVIDER_RULES``. The human guide renders the same entries as prose.
"""

from __future__ import annotations

COMMON_OAUTH = [
    {
        "id": "redirect_uri_mismatch",
        "symptom": "redirect_uri_mismatch, or the provider says the redirect URI is not registered",
        "cause": "The callback URL sent in the request does not exactly match a registered redirect URI.",
        "fix": "Register the exact scheme, host, port and path in the provider console. No wildcards. Match the "
               "trailing slash. Use 127.0.0.1 rather than localhost if the provider is strict.",
        "severity": "blocker",
        "likelihood": "common",
    },
    {
        "id": "invalid_scope",
        "symptom": "invalid_scope, or the consent screen omits a scope you requested",
        "cause": "The scope is not enabled for this app, is spelled differently, or was not granted by the user.",
        "fix": "Enable the scope in the provider console, request only configured scopes, then re-run consent. "
               "Check for read vs write variants.",
        "severity": "blocker",
        "likelihood": "common",
    },
    {
        "id": "invalid_client",
        "symptom": "invalid_client, 401 unauthorized_client, or 'client authentication failed'",
        "cause": "Client ID/secret pair is wrong, the secret was rotated, or a public client is sending a secret.",
        "fix": "Copy both values from the same app. Rotate the secret and redeploy. Public/native clients should "
               "use PKCE and no client secret.",
        "severity": "blocker",
        "likelihood": "common",
    },
    {
        "id": "access_denied",
        "symptom": "access_denied after the consent screen",
        "cause": "The user declined, is not an allowed test user, or the app is blocked by policy.",
        "fix": "Add the account as a test user, use the correct workspace, or complete the provider's app "
               "verification. Try an incognito window to avoid a stale session.",
        "severity": "blocker",
        "likelihood": "common",
    },
    {
        "id": "missing_refresh_token",
        "symptom": "The token response has access_token but no refresh_token",
        "cause": "Offline access was not requested, consent was previously granted, or the provider only issues "
                 "refresh tokens to certain client types.",
        "fix": "Request offline access (and a consent prompt if needed), revoke the app grant, then retry. Store "
               "the refresh token server-side.",
        "severity": "warning",
        "likelihood": "common",
    },
    {
        "id": "pkce_verifier_mismatch",
        "symptom": "invalid_grant with a PKCE verification error",
        "cause": "The code_verifier does not match the code_challenge, or the challenge was reused.",
        "fix": "Generate a fresh verifier per authorization request, use S256, keep it until the token exchange, "
               "and never reuse an authorization code.",
        "severity": "blocker",
        "likelihood": "occasional",
    },
    {
        "id": "authorization_code_reused",
        "symptom": "invalid_grant: authorization code expired, already used, or redirect URI mismatch",
        "cause": "Codes are single-use and short-lived; a retry or a second exchange invalidates them.",
        "fix": "Exchange the code once, immediately. On failure restart from the authorization redirect; never "
               "retry the same code.",
        "severity": "blocker",
        "likelihood": "occasional",
    },
    {
        "id": "rate_limited",
        "symptom": "429 Too Many Requests, or the provider asks you to slow down",
        "cause": "Token or API calls exceeded the provider's rate limit.",
        "fix": "Cache the access token until it expires, honour Retry-After, add exponential backoff, and avoid "
               "fetching a new token per request.",
        "severity": "warning",
        "likelihood": "common",
    },
]

PROVIDER_RULES = {
    "google": [
        {
            "id": "google_unverified_app",
            "symptom": "Google shows 'This app isn't verified' or blocks a sensitive scope",
            "cause": "The consent screen is in testing, or sensitive/restricted scopes need verification.",
            "fix": "Add the account under test users while testing. For production, publish the consent screen and "
                   "submit for verification; restricted scopes may need a security assessment.",
            "severity": "blocker",
            "likelihood": "common",
        },
        {
            "id": "google_test_user_missing",
            "symptom": "access_denied while the OAuth consent screen is in Testing",
            "cause": "The signed-in account is not on the test-user list.",
            "fix": "Add the exact Google account under APIs & Services → OAuth consent screen → Test users, then "
                   "retry in an incognito window.",
            "severity": "blocker",
            "likelihood": "common",
        },
    ],
    "github": [
        {
            "id": "github_bad_verification_code",
            "symptom": "bad_verification_code from the token endpoint",
            "cause": "The code expired (10 minutes), was already exchanged, or belongs to another client.",
            "fix": "Restart the authorization redirect and exchange the new code once, immediately. Check the "
                   "client ID matches the app that issued the code.",
            "severity": "blocker",
            "likelihood": "common",
        },
        {
            "id": "github_incorrect_client_credentials",
            "symptom": "incorrect_client_credentials",
            "cause": "Client ID or secret does not match the GitHub OAuth app.",
            "fix": "Copy the client ID and generate a new client secret in the same OAuth app; update both "
                   "server-side values together.",
            "severity": "blocker",
            "likelihood": "common",
        },
    ],
    "slack": [
        {
            "id": "slack_admin_approval",
            "symptom": "The workspace blocks installation or asks for admin approval",
            "cause": "Workspace policy requires an owner/admin to approve the app.",
            "fix": "Ask a workspace owner to approve the app, or install into a sandbox workspace you control.",
            "severity": "blocker",
            "likelihood": "common",
        },
    ],
}

API_KEY_RULES = [
    {
        "id": "wrong_key_or_workspace",
        "symptom": "401 invalid_api_key, or the key works in one workspace but not another",
        "cause": "The key belongs to a different workspace, was revoked, or was copied incompletely.",
        "fix": "Create a key in the workspace that owns the data, copy the whole value, and store it server-side. "
               "Check for leading/trailing spaces.",
        "severity": "blocker",
        "likelihood": "common",
    },
    {
        "id": "key_permission_denied",
        "symptom": "403 insufficient_scope / permission denied with a valid key",
        "cause": "The key's role or scopes do not include the endpoint you are calling.",
        "fix": "Grant the required scope/role to the key, or create a key with the right permissions. Re-check the "
               "endpoint's required scope.",
        "severity": "blocker",
        "likelihood": "common",
    },
    {
        "id": "key_exposed_client_side",
        "symptom": "The key is visible in a browser bundle, mobile app, or git history",
        "cause": "The key was used in client-side code or committed by mistake.",
        "fix": "Rotate the key immediately, move calls behind your server, and add secret scanning/pre-commit "
               "checks. Never ship a key to a browser.",
        "severity": "blocker",
        "likelihood": "occasional",
    },
    {
        "id": "key_rate_limited",
        "symptom": "429 Too Many Requests",
        "cause": "The key hit its plan or per-minute limit.",
        "fix": "Back off, cache responses, and check the plan's limit. Use a separate key per service so one "
               "integration cannot starve another.",
        "severity": "warning",
        "likelihood": "common",
    },
]


def select_blockers(spec: dict, docs_text: str) -> list[dict]:
    auth = spec.get("auth") or {}
    oauth = auth.get("oauth") or {}
    api_key = auth.get("api_key")
    lowered = (docs_text or "").lower()
    out: list[dict] = []

    if oauth:
        provider_id = (spec.get("provider") or {}).get("id", "")
        out.extend(PROVIDER_RULES.get(provider_id, []))
        out.extend(COMMON_OAUTH[:4])
        if (("app review" in lowered or "verification" in lowered)
                and ((spec.get("resolution") or {}).get("availability") == "approval_gated"
                     or (provider_id in ("google", "meta")
                         and any((scope.get("sensitivity") or "") == "sensitive" for scope in oauth.get("scopes", []))))):
            out.append({
                "id": "app_verification",
                "symptom": "The provider says the app is unverified, in review, or blocked",
                "cause": "Sensitive scopes or production use triggered the provider's verification/review.",
                "fix": "Use test users while developing. Submit the app for verification early; it can take days. "
                       "Do not assume review is instant.",
                "severity": "blocker",
                "likelihood": "occasional",
            })
        if "admin consent" in lowered or "workspace" in lowered:
            out.append({
                "id": "admin_consent_blocked",
                "symptom": "admin_approval_required / consent_required",
                "cause": "The workspace or tenant requires an administrator to grant consent.",
                "fix": "Ask a workspace admin to approve the app, or test in a personal workspace you control.",
                "severity": "blocker",
                "likelihood": "occasional",
            })
        if oauth.get("refresh_token", {}).get("value") is not False:
            out.append(COMMON_OAUTH[4])
        if oauth.get("pkce", {}).get("value") is not False:
            out.append(COMMON_OAUTH[5])
        out.extend(COMMON_OAUTH[6:])
    elif api_key:
        out.extend(API_KEY_RULES)

    seen: set[str] = set()
    unique = []
    for blocker in out:
        if blocker["id"] in seen:
            continue
        seen.add(blocker["id"])
        unique.append(blocker)
    return unique[:8]
