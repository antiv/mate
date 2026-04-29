# OAuth 2.0 / OIDC Single Sign-On

MATE supports native Google (OIDC) and GitHub (OAuth 2.0) login using the [Authlib](https://docs.authlib.org/) library.  
Both providers use the **Authorization Code Flow with PKCE**, and both can be active at the same time.  
HTTP Basic Auth is always available alongside SSO — you do not have to choose.

---

## Contents

1. [How it works](#how-it-works)
2. [Quick setup](#quick-setup)
3. [Google provider setup](#google-provider-setup)
4. [GitHub provider setup](#github-provider-setup)
5. [Environment variable reference](#environment-variable-reference)
6. [User provisioning and RBAC](#user-provisioning-and-rbac)
7. [Session security](#session-security)
8. [Enterprise access restrictions](#enterprise-access-restrictions)
9. [Adding future providers](#adding-future-providers)
10. [Troubleshooting](#troubleshooting)

---

## How it works

```
Browser                 MATE (port 8000)              Google / GitHub
  │                           │                              │
  │  GET /auth/login/google   │                              │
  │──────────────────────────▶│                              │
  │                           │  redirect to authorization   │
  │◀──────────────────────────│──────────────────────────────▶
  │                           │                              │
  │  user approves, redirect  │                              │
  │  GET /auth/callback/google│                              │
  │──────────────────────────▶│  POST exchange code/token   │
  │                           │──────────────────────────────▶
  │                           │◀─────────────────────────────│
  │                           │  fetch /userinfo or /user    │
  │                           │──────────────────────────────▶
  │                           │◀─────────────────────────────│
  │                           │                              │
  │                           │  upsert row in users table   │
  │                           │  set encrypted session cookie│
  │  redirect /dashboard      │                              │
  │◀──────────────────────────│                              │
```

1. **Login redirect** (`/auth/login/{provider}`) — MATE builds the authorization URL including a PKCE `code_challenge` and a random `state`, stores both in the encrypted session, then redirects the browser to the provider.
2. **Callback** (`/auth/callback/{provider}`) — MATE verifies the `state`, exchanges the authorization code for tokens, fetches the user's profile, upserts the row in the `users` table (creating it with `OAUTH_DEFAULT_ROLE` on first login), and stores identity in the session cookie.
3. **Subsequent requests** — `server/auth.py` reads `request.session["user"]` to authenticate the user. No passwords are stored or transmitted.

---

## Quick setup

```bash
# .env (minimum for Google SSO)
GOOGLE_CLIENT_ID=123456789-xxx.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=GOCSPX-xxxxx
SECRET_KEY=<run: python -c "import secrets; print(secrets.token_hex(32))">
OAUTH_DEFAULT_ROLE=user
```

Restart MATE and open `http://localhost:8000/login` — the "Sign in with Google" button appears automatically.

---

## Google provider setup

### 1. Create a Google Cloud project

1. Go to [console.cloud.google.com](https://console.cloud.google.com/) → **Select or create a project**.
2. Enable the **Google People API** (or at minimum **openid** scope is sufficient for email + profile).

### 2. Configure the OAuth consent screen

1. **APIs & Services → OAuth consent screen**
2. Choose **External** (for any Google account) or **Internal** (G Suite / Workspace users only — easiest for enterprise).
3. Fill in **App name**, **User support email**, **Developer contact email**.
4. Under **Scopes** add: `openid`, `email`, `profile`.
5. If External, add yourself under **Test users** while the app is in testing mode.
6. Save and continue.

### 3. Create credentials

1. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
2. **Application type**: Web application
3. **Authorized redirect URIs** — add:
   - `http://localhost:8000/auth/callback/google` (local dev)
   - `https://your-domain.com/auth/callback/google` (production)
4. Click **Create** and copy the **Client ID** and **Client Secret**.

### 4. Configure MATE

```bash
GOOGLE_CLIENT_ID=<Client ID>
GOOGLE_CLIENT_SECRET=<Client Secret>
# GOOGLE_CONF_URL defaults to https://accounts.google.com/.well-known/openid-configuration
# Override only if using a custom OIDC provider or Workspace domain
```

---

## GitHub provider setup

### 1. Create an OAuth App

1. Go to **GitHub → Settings → Developer settings → OAuth Apps → New OAuth App**  
   (or for organizations: **Organization → Settings → Developer settings → OAuth Apps**)
2. Fill in:
   - **Application name**: MATE Dashboard
   - **Homepage URL**: `https://your-domain.com`
   - **Authorization callback URL**:
     - `http://localhost:8000/auth/callback/github` (local dev)
     - `https://your-domain.com/auth/callback/github` (production)
3. Click **Register application**.
4. On the next screen click **Generate a new client secret** and copy both the **Client ID** and **Client Secret**.

### 2. Configure MATE

```bash
GITHUB_CLIENT_ID=<Client ID>
GITHUB_CLIENT_SECRET=<Client Secret>
```

> **Note**: GitHub OAuth Apps do not support PKCE natively, but Authlib handles this transparently; the flow remains secure via the server-side client secret.

---

## Environment variable reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `GOOGLE_CLIENT_ID` | For Google SSO | — | OAuth client ID from Google Console |
| `GOOGLE_CLIENT_SECRET` | For Google SSO | — | OAuth client secret from Google Console |
| `GOOGLE_CONF_URL` | No | `https://accounts.google.com/.well-known/openid-configuration` | OIDC discovery URL; override for Workspace or custom OIDC |
| `GITHUB_CLIENT_ID` | For GitHub SSO | — | OAuth client ID from GitHub Settings |
| `GITHUB_CLIENT_SECRET` | For GitHub SSO | — | OAuth client secret from GitHub Settings |
| `SECRET_KEY` | **Yes, in production** | Random (new per restart) | Signs and verifies the encrypted session cookie. A random key means sessions are invalidated on restart. |
| `OAUTH_DEFAULT_ROLE` | No | `user` | Role assigned to newly provisioned SSO users. Common values: `user`, `admin` |
| `SESSION_SECURE_COOKIE` | No | `false` | Set to `true` behind HTTPS to add the `Secure` flag to the session cookie |

---

## User provisioning and RBAC

On each successful OAuth login MATE **upserts** a row in the `users` table:

| Column | Value |
|---|---|
| `user_id` | Email address (or `github:<login>` when the GitHub account has no verified public email) |
| `email` | Verified email from the provider |
| `display_name` | Full name from the provider profile |
| `oauth_provider` | `google` or `github` |
| `roles` | `["<OAUTH_DEFAULT_ROLE>"]` on first login; unchanged on subsequent logins |

### Changing a user's role after first login

From the dashboard → **Users** → find the user → edit roles.

Or directly in the database:

```sql
UPDATE users SET roles = '["admin"]' WHERE user_id = 'alice@example.com';
```

### Restricting which users can access MATE

By default any valid Google or GitHub account can log in and be provisioned. See [Enterprise access restrictions](#enterprise-access-restrictions) to limit this.

---

## Session security

Sessions are stored in a **signed, encrypted cookie** (Starlette `SessionMiddleware` backed by `itsdangerous`):

- **httponly**: the cookie is not accessible to JavaScript
- **SameSite=Lax**: prevents the cookie from being sent on cross-site POST requests (CSRF protection)
- **Secure** (opt-in via `SESSION_SECURE_COOKIE=true`): the cookie is only sent over HTTPS

The session stores:

```json
{
  "user_id": "alice@example.com",
  "display_name": "Alice Smith",
  "email": "alice@example.com",
  "provider": "google"
}
```

**Important**: set a static `SECRET_KEY` in production. Without it MATE generates a random key on startup and all existing sessions become invalid every time the server restarts.

```bash
# Generate a suitable key
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## Enterprise access restrictions

The callback handler in `server/oauth_routes.py` receives the full user profile before writing to the database. You can add domain or organisation checks there.

### Restrict to a Google Workspace domain

Edit `server/oauth_routes.py`, inside the `if provider == "google":` block:

```python
ALLOWED_DOMAIN = os.getenv("OAUTH_ALLOWED_DOMAIN", "")   # e.g. "yourcompany.com"

if provider == "google":
    info = token.get("userinfo") or {}
    email = info.get("email", "")

    # --- domain restriction ---
    if ALLOWED_DOMAIN and not email.endswith(f"@{ALLOWED_DOMAIN}"):
        logger.warning("Blocked OAuth login from outside domain: %s", email)
        return RedirectResponse(url="/login?error=domain_not_allowed")
    # --------------------------

    user_id = email or info.get("sub", "")
    display_name = info.get("name") or email or user_id
```

Alternatively, set the consent screen to **Internal** in Google Cloud (restricts to your Workspace organisation at the provider level without any code change).

### Restrict to a GitHub Organisation

```python
ALLOWED_GITHUB_ORG = os.getenv("OAUTH_ALLOWED_GITHUB_ORG", "")   # e.g. "myorg"

elif provider == "github":
    # ... existing code to get login, email ...

    # --- org restriction ---
    if ALLOWED_GITHUB_ORG:
        orgs_resp = await client.get("user/orgs", token=token)
        if orgs_resp.status_code == 200:
            org_logins = {o["login"] for o in orgs_resp.json()}
            if ALLOWED_GITHUB_ORG not in org_logins:
                logger.warning("Blocked GitHub login — not in org %s: %s", ALLOWED_GITHUB_ORG, login)
                return RedirectResponse(url="/login?error=org_not_allowed")
        else:
            return RedirectResponse(url="/login?error=org_check_failed")
    # ----------------------
```

> **Scope note**: the `read:org` scope must be added to the GitHub OAuth App's requested scopes for the org membership check to work.

---

## Adding future providers

Any standard OIDC provider (Okta, Azure AD, Keycloak, Auth0, …) can be registered by adding a block to `_get_oauth()` in `server/oauth_routes.py`:

```python
# Example: Okta
oauth.register(
    name="okta",
    client_id=os.getenv("OKTA_CLIENT_ID"),
    client_secret=os.getenv("OKTA_CLIENT_SECRET"),
    server_metadata_url=f"https://{os.getenv('OKTA_DOMAIN')}/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)
```

Then handle the new provider name inside `oauth_callback` (the Google branch already handles any standard OIDC provider that returns `userinfo`).

---

## Troubleshooting

### "OAuth provider is not configured" (HTTP 400)

The env vars for that provider are not set. Check that both `*_CLIENT_ID` and `*_CLIENT_SECRET` are present and the server has been restarted after adding them.

### "auth_failed" redirect after approving

Usually a state/PKCE mismatch caused by the session not persisting between the login redirect and the callback. Check:

- `SECRET_KEY` is set and the same value across all instances
- The callback URL registered in Google/GitHub Console matches exactly the URL MATE receives (scheme, host, port, path — no trailing slash difference)

### "profile_fetch_failed" redirect

MATE successfully exchanged the code but couldn't read the user profile. Usually a scope issue:

- Google: ensure `email` and `profile` scopes are included in the consent screen
- GitHub: the `user:email` scope is required; make sure it is listed in the OAuth App settings

### Sessions lost on server restart

`SECRET_KEY` is not set — MATE falls back to a random key. Set a static value in `.env`.

### Google shows "This app is blocked" or "unverified app"

Your OAuth consent screen is in testing/unverified mode. Either add users to the **Test users** list, or publish the app through Google's verification process for production use.
