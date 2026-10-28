# Google integration setup

**Verdict:** Self-serve OAuth app · **Status:** complete · **Resolution:** high confidence (alias)

Generated 2026-10-27T23:45:38+00:00 from 3 source(s). Console steps are quoted from the provider's docs; the console may have changed.

## Time and prerequisites

**35 min likely** (range 20–50 min). Confidence: **low**. Drivers: consent screen, workspace admin may be involved.

| Environment variable | Required | Secret | Purpose |
|---|---|---|---|
| `GOOGLE_CLIENT_ID` | yes | no | OAuth client id |
| `GOOGLE_CLIENT_SECRET` | yes | yes | OAuth client secret |

**Console:** <https://console.cloud.google.com/apis/credentials>

## Setup steps

### 1. Open the API Library in the Google API Console

Navigate to the API Library in the Google API Console.

_Source: https://developers.google.com/identity/protocols/oauth2/web-server_

### 2. Select or create a project

If prompted, select a project, or create a new one.

_Source: https://developers.google.com/identity/protocols/oauth2/web-server_

### 3. Enable the desired API

Find the API you want to enable, select it, and click the Enable button.

_Source: https://developers.google.com/identity/protocols/oauth2/web-server_

### 4. Enable billing if prompted

If prompted, enable billing for the project.

_Source: https://developers.google.com/identity/protocols/oauth2/web-server_

### 5. Accept the API's Terms of Service

If prompted, read and accept the API's Terms of Service.

_Source: https://developers.google.com/identity/protocols/oauth2/web-server_

### 6. Go to the Clients page

Navigate to the Clients page in the API Console to manage OAuth clients.

_Source: https://developers.google.com/identity/protocols/oauth2/web-server_

### 7. Click Create Client

Click the Create Client button to start creating new credentials.

_Source: https://developers.google.com/identity/protocols/oauth2/web-server_

### 8. Select Web application application type

Choose the Web application application type for the client.

_Source: https://developers.google.com/identity/protocols/oauth2/web-server_

### 9. Fill in the form and click Create

Fill in the form, including authorized redirect URIs, and click Create.

_Source: https://developers.google.com/identity/protocols/oauth2/web-server_

### 10. Download the client_secret.json file

After creating the credentials, download the client_secret.json file from the API Console.

_Source: https://developers.google.com/identity/protocols/oauth2/web-server_

### 11. Open the Google API Console

Visit the Google API Console to obtain OAuth 2.0 credentials such as a client ID and client secret that are known to both Google and your application.

_Source: https://developers.google.com/identity/protocols/oauth2_

## Verify

**1. BROWSER**

```bash
open "https://accounts.google.com/o/oauth2/v2/auth?client_id=${GOOGLE_CLIENT_ID}&redirect_uri=${REDIRECT_URI}&response_type=code&scope=openid%20email%20profile"
```

Expected: The provider shows its consent screen; after approval the browser lands on your redirect URI with a ?code= parameter.

**2. CURL**

```bash
curl -sS -X POST "https://oauth2.googleapis.com/token" -H "Accept: application/json" -d "client_id=${GOOGLE_CLIENT_ID}" -d "client_secret=${GOOGLE_CLIENT_SECRET}" -d "code=$CODE" -d "redirect_uri=$REDIRECT_URI" -d "grant_type=authorization_code"
```

Expected: A JSON response containing access_token (and refresh_token if you requested offline access).

## Auth details

| Field | Value | Provenance |
|---|---|---|
| Authorization URL | `https://accounts.google.com/o/oauth2/v2/auth` |  [source](https://accounts.google.com/.well-known/openid-configuration) |
| Token URL | `https://oauth2.googleapis.com/token` |  [source](https://accounts.google.com/.well-known/openid-configuration) |
| Discovery URL | `https://accounts.google.com/.well-known/openid-configuration` |  [source](https://accounts.google.com/.well-known/openid-configuration) |
| PKCE | _Not stated in the docs._ | — |
| Client secret required | Yes |  [source](https://developers.google.com/identity/protocols/oauth2/web-server) |
| Refresh token | _Not stated in the docs._ | — |
| Redirect URI rules | These endpoints must adhere to Google's validation rules. For testing, you can specify URIs that refer to the local machine, such as http://localhost:8080. |  [source](https://developers.google.com/identity/protocols/oauth2/web-server) |
| Consent screen | If your public application uses scopes that permit access to certain user data, it must complete a verification process. If you see unverified app on the screen when testing your application, you must submit a verification request to remove it. |  [source](https://developers.google.com/identity/protocols/oauth2/web-server) |

## Scopes

| Scope | What it allows | Sensitivity | Source |
|---|---|---|---|
| `openid` | Supported scope listed in the OIDC discovery document. | normal | [docs](https://accounts.google.com/.well-known/openid-configuration) |
| `email` | Supported scope listed in the OIDC discovery document. | normal | [docs](https://accounts.google.com/.well-known/openid-configuration) |
| `profile` | Supported scope listed in the OIDC discovery document. | normal | [docs](https://accounts.google.com/.well-known/openid-configuration) |

## If it breaks

| Symptom | Cause | Fix | Likelihood |
|---|---|---|---|
| **Google shows 'This app isn't verified' or blocks a sensitive scope** | The consent screen is in testing, or sensitive/restricted scopes need verification. | Add the account under test users while testing. For production, publish the consent screen and submit for verification; restricted scopes may need a security assessment. | common |
| **access_denied while the OAuth consent screen is in Testing** | The signed-in account is not on the test-user list. | Add the exact Google account under APIs & Services → OAuth consent screen → Test users, then retry in an incognito window. | common |
| **redirect_uri_mismatch, or the provider says the redirect URI is not registered** | The callback URL sent in the request does not exactly match a registered redirect URI. | Register the exact scheme, host, port and path in the provider console. No wildcards. Match the trailing slash. Use 127.0.0.1 rather than localhost if the provider is strict. | common |
| **invalid_scope, or the consent screen omits a scope you requested** | The scope is not enabled for this app, is spelled differently, or was not granted by the user. | Enable the scope in the provider console, request only configured scopes, then re-run consent. Check for read vs write variants. | common |
| **invalid_client, 401 unauthorized_client, or 'client authentication failed'** | Client ID/secret pair is wrong, the secret was rotated, or a public client is sending a secret. | Copy both values from the same app. Rotate the secret and redeploy. Public/native clients should use PKCE and no client secret. | common |
| **access_denied after the consent screen** | The user declined, is not an allowed test user, or the app is blocked by policy. | Add the account as a test user, use the correct workspace, or complete the provider's app verification. Try an incognito window to avoid a stale session. | common |
| **admin_approval_required / consent_required** | The workspace or tenant requires an administrator to grant consent. | Ask a workspace admin to approve the app, or test in a personal workspace you control. | occasional |
| **The token response has access_token but no refresh_token** | Offline access was not requested, consent was previously granted, or the provider only issues refresh tokens to certain client types. | Request offline access (and a consent prompt if needed), revoke the app grant, then retry. Store the refresh token server-side. | common |

## What we could not verify

These fields were not stated clearly enough in the fetched docs. They are deliberately blank; do not fill them from memory.

- `auth.oauth.pkce`
- `auth.oauth.refresh_token`

## Warnings

- **FIELD_UNVERIFIED** — Some details weren't stated in the docs and are marked as such.

## Sources

- [Using OAuth 2.0 for Web Server Applications  |  Authorization  |  Google for Developers](https://developers.google.com/identity/protocols/oauth2/web-server) — fetched 2026-10-27T23:44:32+00:00, `sha256:`
- [Using OAuth 2.0 to Access Google APIs  |  Authorization  |  Google for Developers](https://developers.google.com/identity/protocols/oauth2) — fetched 2026-10-27T23:44:33+00:00, `sha256:`
- [OIDC discovery document](https://accounts.google.com/.well-known/openid-configuration) — fetched 2026-10-27T23:44:33+00:00, `sha256:`

---
Generated by `guidegen` 0.2.0 · model `deepseek-ai/DeepSeek-V4-Flash-0731` · prompt `p2`.
Time is a heuristic until this class is calibrated by running the setup once.