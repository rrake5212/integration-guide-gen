# GitHub integration setup

**Verdict:** Self-serve OAuth app · **Status:** complete · **Resolution:** high confidence (alias)

Generated 2026-10-28T00:16:43+00:00 from 3 source(s). Console steps are quoted from the provider's docs; the console may have changed.

## Time and prerequisites

**15 min likely** (range 10–25 min). Confidence: **low**. Drivers: consent screen.

| Environment variable | Required | Secret | Purpose |
|---|---|---|---|
| `GITHUB_CLIENT_ID` | yes | no | OAuth client id |
| `GITHUB_CLIENT_SECRET` | yes | yes | OAuth client secret |

**Console:** <https://github.com/settings/developers>

## Setup steps

### 1. Navigate to Settings

In the upper-right corner of any page on GitHub, click your profile picture, then click Settings.

_Source: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app_

### 2. Open Developer settings

In the left sidebar, click Developer settings.

_Source: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app_

### 3. Open OAuth apps

In the left sidebar, click OAuth apps.

_Source: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app_

### 4. Start creating a new OAuth app

Click New OAuth App. If you haven't created an app before, this button will say, Register a new application.

_Source: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app_

### 5. Enable device flow (optional)

If your OAuth app will use the device flow to identify and authorize users, click Enable Device Flow.

_Source: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app_

### 6. Adjust token expiration setting

Expire user access tokens is enabled by default. If your app's authentication code hasn't been updated to support short-lived tokens, uncheck the box until support is added.

_Source: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app_

### 7. Register the application

Click Register application.

_Source: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app_

## Verify

**1. BROWSER**

```bash
open "https://github.com/login/oauth/authorize?client_id=${GITHUB_CLIENT_ID}&redirect_uri=${REDIRECT_URI}&response_type=code&scope=offline_access%20%28no%20scope%29%20repo%20repo%3Astatus%20repo_deployment%20public_repo"
```

Expected: The provider shows its consent screen; after approval the browser lands on your redirect URI with a ?code= parameter.

**2. CURL**

```bash
curl -sS -X POST "https://github.com/login/oauth/access_token" -H "Accept: application/json" -d "client_id=${GITHUB_CLIENT_ID}" -d "client_secret=${GITHUB_CLIENT_SECRET}" -d "code=$CODE" -d "redirect_uri=$REDIRECT_URI" -d "grant_type=authorization_code"
```

Expected: A JSON response containing access_token (and refresh_token if you requested offline access).

## Auth details

| Field | Value | Provenance |
|---|---|---|
| Authorization URL | `https://github.com/login/oauth/authorize` |  [source](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps) |
| Token URL | `https://github.com/login/oauth/access_token` |  [source](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps) |
| Discovery URL | `_Not stated in the docs._` | — |
| PKCE | _Not stated in the docs._ | — |
| Client secret required | Yes |  [source](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps) |
| Refresh token | Yes |  [source](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps) |
| Redirect URI rules | When wildcard matching is enabled, the redirect URL's host (excluding subdomains) and port must exactly match the callback URL, and the redirect URL's path must reference a subdirectory of the callback URL. |  [source](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps) |
| Consent screen | _Not stated in the docs._ | — |

## Scopes

| Scope | What it allows | Sensitivity | Source |
|---|---|---|---|
| `offline_access` | Requesting this scope opts in to expiring tokens and refresh tokens for an individual sign-in. | unknown | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps) |
| `(no scope)` | Grants read-only access to public information (including user profile info, repository info, and gists) | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `repo` | Grants full access to public and private repositories including read and write access to code, commit statuses, repository invitations, collaborators, deployment statuses, and repository webhooks. Note: In addition to repository related resources, the `repo` scope also grants access to manage org... | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `repo:status` | Grants read/write access to commit statuses in public and private repositories. This scope is only necessary to grant other users or services access to private repository commit statuses without granting access to the code. | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `repo_deployment` | Grants access to deployment statuses for public and private repositories. This scope is only necessary to grant other users or services access to deployment statuses, without granting access to the code. | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `public_repo` | Limits access to public repositories. That includes read/write access to code, commit statuses, repository projects, collaborators, and deployment statuses for public repositories and organizations. Also required for starring public repositories. | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `repo:invite` | Grants accept/decline abilities for invitations to collaborate on a repository. This scope is only necessary to grant other users or services access to invites without granting access to the code. | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `security_events` | Grants: read and write access to security events in the code scanning API. This scope is only necessary to grant other users or services access to security events without granting access to the code. | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `admin:repo_hook` | Grants read, write, ping, and delete access to repository hooks in public or private repositories. The `repo` and `public_repo` scopes grant full access to repositories, including repository hooks. Use the `admin:repo_hook` scope to limit access to only repository hooks. | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `write:repo_hook` | Grants read, write, and ping access to hooks in public or private repositories. | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `read:repo_hook` | Grants read and ping access to hooks in public or private repositories. | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `admin:org` | Fully manage the organization and its teams, projects, and memberships. | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `write:org` | Read and write access to organization membership and organization projects. | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `read:org` | Read-only access to organization membership, organization projects, and team membership. | normal | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `admin:public_key` | Fully manage public keys. | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `write:public_key` | Create, list, and view details for public keys. | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `read:public_key` | List and view details for public keys. | normal | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `admin:org_hook` | Grants read, write, ping, and delete access to organization hooks. Note: OAuth tokens will only be able to perform these actions on organization hooks which were created by the OAuth app. Personal access tokens will only be able to perform these actions on organization hooks created by a user. | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `gist` | Grants write access to gists. | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `user` | Grants read/write access to profile info only. Note that this scope includes `user:email` and `user:follow`. | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `read:user` | Grants access to read a user's profile data. | normal | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `user:email` | Grants read access to a user's email addresses. | normal | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `user:follow` | Grants access to follow or unfollow other users. | unknown | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `project` | Grants read/write access to user and organization projects. | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `read:project` | Grants read only access to user and organization projects. | normal | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `delete_repo` | Grants access to delete adminable repositories. | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `write:packages` | Grants access to upload or publish a package in GitHub Packages. For more information, see Publishing a package. | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `read:packages` | Grants access to download or install packages from GitHub Packages. For more information, see Installing a package. | normal | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `delete:packages` | Grants access to delete packages from GitHub Packages. For more information, see Deleting and restoring a package. | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `admin:gpg_key` | Fully manage GPG keys. | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `write:gpg_key` | Create, list, and view details for GPG keys. | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `read:gpg_key` | List and view details for GPG keys. | normal | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `codespace` | Grants the ability to create and manage codespaces. Codespaces can expose a GITHUB_TOKEN which may have a different set of scopes. For more information, see Security in GitHub Codespaces. | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `workflow` | Grants the ability to add and update GitHub Actions workflow files. Workflow files can be committed without this scope if the same file (with both the same path and contents) exists on another branch in the same repository. Workflow files can expose `GITHUB_TOKEN` which may have a different set of s | sensitive | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |
| `read:audit_log` | Read audit log data. | normal | [docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) |

## If it breaks

| Symptom | Cause | Fix | Likelihood |
|---|---|---|---|
| **bad_verification_code from the token endpoint** | The code expired (10 minutes), was already exchanged, or belongs to another client. | Restart the authorization redirect and exchange the new code once, immediately. Check the client ID matches the app that issued the code. | common |
| **incorrect_client_credentials** | Client ID or secret does not match the GitHub OAuth app. | Copy the client ID and generate a new client secret in the same OAuth app; update both server-side values together. | common |
| **redirect_uri_mismatch, or the provider says the redirect URI is not registered** | The callback URL sent in the request does not exactly match a registered redirect URI. | Register the exact scheme, host, port and path in the provider console. No wildcards. Match the trailing slash. Use 127.0.0.1 rather than localhost if the provider is strict. | common |
| **invalid_scope, or the consent screen omits a scope you requested** | The scope is not enabled for this app, is spelled differently, or was not granted by the user. | Enable the scope in the provider console, request only configured scopes, then re-run consent. Check for read vs write variants. | common |
| **invalid_client, 401 unauthorized_client, or 'client authentication failed'** | Client ID/secret pair is wrong, the secret was rotated, or a public client is sending a secret. | Copy both values from the same app. Rotate the secret and redeploy. Public/native clients should use PKCE and no client secret. | common |
| **access_denied after the consent screen** | The user declined, is not an allowed test user, or the app is blocked by policy. | Add the account as a test user, use the correct workspace, or complete the provider's app verification. Try an incognito window to avoid a stale session. | common |
| **The token response has access_token but no refresh_token** | Offline access was not requested, consent was previously granted, or the provider only issues refresh tokens to certain client types. | Request offline access (and a consent prompt if needed), revoke the app grant, then retry. Store the refresh token server-side. | common |
| **invalid_grant with a PKCE verification error** | The code_verifier does not match the code_challenge, or the challenge was reused. | Generate a fresh verifier per authorization request, use S256, keep it until the token exchange, and never reuse an authorization code. | occasional |

## What we could not verify

These fields were not stated clearly enough in the fetched docs. They are deliberately blank; do not fill them from memory.

- `auth.oauth.consent_screen`
- `auth.oauth.discovery_url`
- `auth.oauth.pkce`

## Warnings

- **FIELD_UNVERIFIED** — Some details weren't stated in the docs and are marked as such.

## Sources

- [Creating an OAuth app - GitHub Docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app) — fetched 2026-10-28T00:15:33+00:00, `sha256:`
- [Authorizing OAuth apps - GitHub Docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps) — fetched 2026-10-28T00:15:33+00:00, `sha256:`
- [Scopes for OAuth apps - GitHub Docs](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps) — fetched 2026-10-28T00:15:33+00:00, `sha256:`

---
Generated by `guidegen` 0.2.0 · model `deepseek-ai/DeepSeek-V4-Flash-0731` · prompt `p2`.
Time is a heuristic until this class is calibrated by running the setup once.