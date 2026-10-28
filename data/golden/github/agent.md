# GitHub integration brief (for an agent)

Use this as the source of truth. If a value is `null`, it was not stated in the fetched docs — ask the user or fetch the provider's docs again. Do not invent endpoints, scopes or console steps.

- Availability: `self_serve_oauth`
- Status: `complete`
- Time: 15 min likely (10–25), confidence `low`
- Methods: oauth2 (primary: oauth2)

## Environment

- `GITHUB_CLIENT_ID` — OAuth client id (required)
- `GITHUB_CLIENT_SECRET` — OAuth client secret (secret) (required)

## Auth

```text
authorization_url: https://github.com/login/oauth/authorize
token_url: https://github.com/login/oauth/access_token
discovery_url: None
pkce: None
client_secret_required: True
refresh_token: True
```

Redirect URI rules: When wildcard matching is enabled, the redirect URL's host (excluding subdomains) and port must exactly match the callback URL, and the redirect URL's path must reference a subdirectory of the callback URL.

Consent screen: null

### Scopes

- `offline_access` — Requesting this scope opts in to expiring tokens and refresh tokens for an individual sign-in. (unknown)
- `(no scope)` — Grants read-only access to public information (including user profile info, repository info, and gists) (sensitive)
- `repo` — Grants full access to public and private repositories including read and write access to code, commit statuses, repository invitations, collaborators, deployment statuses, and repository webhooks. Note: In addition to repository related resources, the `repo` scope also grants access to manage org... (sensitive)
- `repo:status` — Grants read/write access to commit statuses in public and private repositories. This scope is only necessary to grant other users or services access to private repository commit statuses without granting access to the code. (sensitive)
- `repo_deployment` — Grants access to deployment statuses for public and private repositories. This scope is only necessary to grant other users or services access to deployment statuses, without granting access to the code. (sensitive)
- `public_repo` — Limits access to public repositories. That includes read/write access to code, commit statuses, repository projects, collaborators, and deployment statuses for public repositories and organizations. Also required for starring public repositories. (sensitive)
- `repo:invite` — Grants accept/decline abilities for invitations to collaborate on a repository. This scope is only necessary to grant other users or services access to invites without granting access to the code. (sensitive)
- `security_events` — Grants: read and write access to security events in the code scanning API. This scope is only necessary to grant other users or services access to security events without granting access to the code. (sensitive)
- `admin:repo_hook` — Grants read, write, ping, and delete access to repository hooks in public or private repositories. The `repo` and `public_repo` scopes grant full access to repositories, including repository hooks. Use the `admin:repo_hook` scope to limit access to only repository hooks. (sensitive)
- `write:repo_hook` — Grants read, write, and ping access to hooks in public or private repositories. (sensitive)
- `read:repo_hook` — Grants read and ping access to hooks in public or private repositories. (sensitive)
- `admin:org` — Fully manage the organization and its teams, projects, and memberships. (sensitive)
- `write:org` — Read and write access to organization membership and organization projects. (sensitive)
- `read:org` — Read-only access to organization membership, organization projects, and team membership. (normal)
- `admin:public_key` — Fully manage public keys. (sensitive)
- `write:public_key` — Create, list, and view details for public keys. (sensitive)
- `read:public_key` — List and view details for public keys. (normal)
- `admin:org_hook` — Grants read, write, ping, and delete access to organization hooks. Note: OAuth tokens will only be able to perform these actions on organization hooks which were created by the OAuth app. Personal access tokens will only be able to perform these actions on organization hooks created by a user. (sensitive)
- `gist` — Grants write access to gists. (sensitive)
- `user` — Grants read/write access to profile info only. Note that this scope includes `user:email` and `user:follow`. (sensitive)
- `read:user` — Grants access to read a user's profile data. (normal)
- `user:email` — Grants read access to a user's email addresses. (normal)
- `user:follow` — Grants access to follow or unfollow other users. (unknown)
- `project` — Grants read/write access to user and organization projects. (sensitive)
- `read:project` — Grants read only access to user and organization projects. (normal)
- `delete_repo` — Grants access to delete adminable repositories. (sensitive)
- `write:packages` — Grants access to upload or publish a package in GitHub Packages. For more information, see Publishing a package. (sensitive)
- `read:packages` — Grants access to download or install packages from GitHub Packages. For more information, see Installing a package. (normal)
- `delete:packages` — Grants access to delete packages from GitHub Packages. For more information, see Deleting and restoring a package. (sensitive)
- `admin:gpg_key` — Fully manage GPG keys. (sensitive)
- `write:gpg_key` — Create, list, and view details for GPG keys. (sensitive)
- `read:gpg_key` — List and view details for GPG keys. (normal)
- `codespace` — Grants the ability to create and manage codespaces. Codespaces can expose a GITHUB_TOKEN which may have a different set of scopes. For more information, see Security in GitHub Codespaces. (sensitive)
- `workflow` — Grants the ability to add and update GitHub Actions workflow files. Workflow files can be committed without this scope if the same file (with both the same path and contents) exists on another branch in the same repository. Workflow files can expose `GITHUB_TOKEN` which may have a different set of s (sensitive)
- `read:audit_log` — Read audit log data. (normal)

## Setup steps

1. **Navigate to Settings** — In the upper-right corner of any page on GitHub, click your profile picture, then click Settings. (source: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app)
2. **Open Developer settings** — In the left sidebar, click Developer settings. (source: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app)
3. **Open OAuth apps** — In the left sidebar, click OAuth apps. (source: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app)
4. **Start creating a new OAuth app** — Click New OAuth App. If you haven't created an app before, this button will say, Register a new application. (source: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app)
5. **Enable device flow (optional)** — If your OAuth app will use the device flow to identify and authorize users, click Enable Device Flow. (source: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app)
6. **Adjust token expiration setting** — Expire user access tokens is enabled by default. If your app's authentication code hasn't been updated to support short-lived tokens, uncheck the box until support is added. (source: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app)
7. **Register the application** — Click Register application. (source: https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app)

## Verify

1. browser: `open "https://github.com/login/oauth/authorize?client_id=${GITHUB_CLIENT_ID}&redirect_uri=${REDIRECT_URI}&response_type=code&scope=offline_access%20%28no%20scope%29%20repo%20repo%3Astatus%20repo_deployment%20public_repo"`
   Expected: The provider shows its consent screen; after approval the browser lands on your redirect URI with a ?code= parameter.
2. curl: `curl -sS -X POST "https://github.com/login/oauth/access_token" -H "Accept: application/json" -d "client_id=${GITHUB_CLIENT_ID}" -d "client_secret=${GITHUB_CLIENT_SECRET}" -d "code=$CODE" -d "redirect_uri=$REDIRECT_URI" -d "grant_type=authorization_code"`
   Expected: A JSON response containing access_token (and refresh_token if you requested offline access).

## Known blockers

- **bad_verification_code from the token endpoint** (common): The code expired (10 minutes), was already exchanged, or belongs to another client. Fix: Restart the authorization redirect and exchange the new code once, immediately. Check the client ID matches the app that issued the code.
- **incorrect_client_credentials** (common): Client ID or secret does not match the GitHub OAuth app. Fix: Copy the client ID and generate a new client secret in the same OAuth app; update both server-side values together.
- **redirect_uri_mismatch, or the provider says the redirect URI is not registered** (common): The callback URL sent in the request does not exactly match a registered redirect URI. Fix: Register the exact scheme, host, port and path in the provider console. No wildcards. Match the trailing slash. Use 127.0.0.1 rather than localhost if the provider is strict.
- **invalid_scope, or the consent screen omits a scope you requested** (common): The scope is not enabled for this app, is spelled differently, or was not granted by the user. Fix: Enable the scope in the provider console, request only configured scopes, then re-run consent. Check for read vs write variants.
- **invalid_client, 401 unauthorized_client, or 'client authentication failed'** (common): Client ID/secret pair is wrong, the secret was rotated, or a public client is sending a secret. Fix: Copy both values from the same app. Rotate the secret and redeploy. Public/native clients should use PKCE and no client secret.
- **access_denied after the consent screen** (common): The user declined, is not an allowed test user, or the app is blocked by policy. Fix: Add the account as a test user, use the correct workspace, or complete the provider's app verification. Try an incognito window to avoid a stale session.
- **The token response has access_token but no refresh_token** (common): Offline access was not requested, consent was previously granted, or the provider only issues refresh tokens to certain client types. Fix: Request offline access (and a consent prompt if needed), revoke the app grant, then retry. Store the refresh token server-side.
- **invalid_grant with a PKCE verification error** (occasional): The code_verifier does not match the code_challenge, or the challenge was reused. Fix: Generate a fresh verifier per authorization request, use S256, keep it until the token exchange, and never reuse an authorization code.

## Unknowns

- `auth.oauth.consent_screen`
- `auth.oauth.discovery_url`
- `auth.oauth.pkce`

## Sources

- https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app (fetched 2026-10-28T00:15:33+00:00)
- https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps (fetched 2026-10-28T00:15:33+00:00)
- https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps (fetched 2026-10-28T00:15:33+00:00)
