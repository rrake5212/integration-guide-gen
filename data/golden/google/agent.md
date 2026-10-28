# Google integration brief (for an agent)

Use this as the source of truth. If a value is `null`, it was not stated in the fetched docs — ask the user or fetch the provider's docs again. Do not invent endpoints, scopes or console steps.

- Availability: `self_serve_oauth`
- Status: `complete`
- Time: 35 min likely (20–50), confidence `low`
- Methods: oauth2 (primary: oauth2)

## Environment

- `GOOGLE_CLIENT_ID` — OAuth client id (required)
- `GOOGLE_CLIENT_SECRET` — OAuth client secret (secret) (required)

## Auth

```text
authorization_url: https://accounts.google.com/o/oauth2/v2/auth
token_url: https://oauth2.googleapis.com/token
discovery_url: https://accounts.google.com/.well-known/openid-configuration
pkce: None
client_secret_required: True
refresh_token: None
```

Redirect URI rules: These endpoints must adhere to Google's validation rules. For testing, you can specify URIs that refer to the local machine, such as http://localhost:8080.

Consent screen: If your public application uses scopes that permit access to certain user data, it must complete a verification process. If you see unverified app on the screen when testing your application, you must submit a verification request to remove it.

### Scopes

- `openid` — Supported scope listed in the OIDC discovery document. (normal)
- `email` — Supported scope listed in the OIDC discovery document. (normal)
- `profile` — Supported scope listed in the OIDC discovery document. (normal)

## Setup steps

1. **Open the API Library in the Google API Console** — Navigate to the API Library in the Google API Console. (source: https://developers.google.com/identity/protocols/oauth2/web-server)
2. **Select or create a project** — If prompted, select a project, or create a new one. (source: https://developers.google.com/identity/protocols/oauth2/web-server)
3. **Enable the desired API** — Find the API you want to enable, select it, and click the Enable button. (source: https://developers.google.com/identity/protocols/oauth2/web-server)
4. **Enable billing if prompted** — If prompted, enable billing for the project. (source: https://developers.google.com/identity/protocols/oauth2/web-server)
5. **Accept the API's Terms of Service** — If prompted, read and accept the API's Terms of Service. (source: https://developers.google.com/identity/protocols/oauth2/web-server)
6. **Go to the Clients page** — Navigate to the Clients page in the API Console to manage OAuth clients. (source: https://developers.google.com/identity/protocols/oauth2/web-server)
7. **Click Create Client** — Click the Create Client button to start creating new credentials. (source: https://developers.google.com/identity/protocols/oauth2/web-server)
8. **Select Web application application type** — Choose the Web application application type for the client. (source: https://developers.google.com/identity/protocols/oauth2/web-server)
9. **Fill in the form and click Create** — Fill in the form, including authorized redirect URIs, and click Create. (source: https://developers.google.com/identity/protocols/oauth2/web-server)
10. **Download the client_secret.json file** — After creating the credentials, download the client_secret.json file from the API Console. (source: https://developers.google.com/identity/protocols/oauth2/web-server)
11. **Open the Google API Console** — Visit the Google API Console to obtain OAuth 2.0 credentials such as a client ID and client secret that are known to both Google and your application. (source: https://developers.google.com/identity/protocols/oauth2)

## Verify

1. browser: `open "https://accounts.google.com/o/oauth2/v2/auth?client_id=${GOOGLE_CLIENT_ID}&redirect_uri=${REDIRECT_URI}&response_type=code&scope=openid%20email%20profile"`
   Expected: The provider shows its consent screen; after approval the browser lands on your redirect URI with a ?code= parameter.
2. curl: `curl -sS -X POST "https://oauth2.googleapis.com/token" -H "Accept: application/json" -d "client_id=${GOOGLE_CLIENT_ID}" -d "client_secret=${GOOGLE_CLIENT_SECRET}" -d "code=$CODE" -d "redirect_uri=$REDIRECT_URI" -d "grant_type=authorization_code"`
   Expected: A JSON response containing access_token (and refresh_token if you requested offline access).

## Known blockers

- **Google shows 'This app isn't verified' or blocks a sensitive scope** (common): The consent screen is in testing, or sensitive/restricted scopes need verification. Fix: Add the account under test users while testing. For production, publish the consent screen and submit for verification; restricted scopes may need a security assessment.
- **access_denied while the OAuth consent screen is in Testing** (common): The signed-in account is not on the test-user list. Fix: Add the exact Google account under APIs & Services → OAuth consent screen → Test users, then retry in an incognito window.
- **redirect_uri_mismatch, or the provider says the redirect URI is not registered** (common): The callback URL sent in the request does not exactly match a registered redirect URI. Fix: Register the exact scheme, host, port and path in the provider console. No wildcards. Match the trailing slash. Use 127.0.0.1 rather than localhost if the provider is strict.
- **invalid_scope, or the consent screen omits a scope you requested** (common): The scope is not enabled for this app, is spelled differently, or was not granted by the user. Fix: Enable the scope in the provider console, request only configured scopes, then re-run consent. Check for read vs write variants.
- **invalid_client, 401 unauthorized_client, or 'client authentication failed'** (common): Client ID/secret pair is wrong, the secret was rotated, or a public client is sending a secret. Fix: Copy both values from the same app. Rotate the secret and redeploy. Public/native clients should use PKCE and no client secret.
- **access_denied after the consent screen** (common): The user declined, is not an allowed test user, or the app is blocked by policy. Fix: Add the account as a test user, use the correct workspace, or complete the provider's app verification. Try an incognito window to avoid a stale session.
- **admin_approval_required / consent_required** (occasional): The workspace or tenant requires an administrator to grant consent. Fix: Ask a workspace admin to approve the app, or test in a personal workspace you control.
- **The token response has access_token but no refresh_token** (common): Offline access was not requested, consent was previously granted, or the provider only issues refresh tokens to certain client types. Fix: Request offline access (and a consent prompt if needed), revoke the app grant, then retry. Store the refresh token server-side.

## Unknowns

- `auth.oauth.pkce`
- `auth.oauth.refresh_token`

## Sources

- https://developers.google.com/identity/protocols/oauth2/web-server (fetched 2026-10-27T23:44:32+00:00)
- https://developers.google.com/identity/protocols/oauth2 (fetched 2026-10-27T23:44:33+00:00)
- https://accounts.google.com/.well-known/openid-configuration (fetched 2026-10-27T23:44:33+00:00)
