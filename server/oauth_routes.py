"""
OAuth 2.0 / OIDC authentication routes for MATE.

Supports Google (OIDC via Authorization Code + PKCE) and GitHub (OAuth 2.0).
Providers are enabled only when their client ID/secret env vars are set.
"""

import json
import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse

logger = logging.getLogger(__name__)

router = APIRouter()

def _cfg(key: str, default: str = "") -> str:
    """Read an env var at call time so values set after module import are visible."""
    return os.getenv(key, default)


def google_enabled() -> bool:
    return bool(_cfg("GOOGLE_CLIENT_ID") and _cfg("GOOGLE_CLIENT_SECRET"))


def github_enabled() -> bool:
    return bool(_cfg("GITHUB_CLIENT_ID") and _cfg("GITHUB_CLIENT_SECRET"))


# Built lazily so that env vars can be set after module import (e.g. in tests).
# Reset to None whenever you need to pick up changed env vars.
_oauth = None


def _get_oauth():
    global _oauth
    if _oauth is not None:
        return _oauth

    from authlib.integrations.starlette_client import OAuth

    _oauth = OAuth()

    if google_enabled():
        _oauth.register(
            name="google",
            client_id=_cfg("GOOGLE_CLIENT_ID"),
            client_secret=_cfg("GOOGLE_CLIENT_SECRET"),
            server_metadata_url=_cfg(
                "GOOGLE_CONF_URL",
                "https://accounts.google.com/.well-known/openid-configuration",
            ),
            client_kwargs={"scope": "openid email profile"},
        )
        logger.info("OAuth: Google provider registered")

    if github_enabled():
        _oauth.register(
            name="github",
            client_id=_cfg("GITHUB_CLIENT_ID"),
            client_secret=_cfg("GITHUB_CLIENT_SECRET"),
            authorize_url="https://github.com/login/oauth/authorize",
            access_token_url="https://github.com/login/oauth/access_token",
            api_base_url="https://api.github.com/",
            client_kwargs={"scope": "user:email"},
        )
        logger.info("OAuth: GitHub provider registered")

    return _oauth


def _upsert_oauth_user(user_id: str, email: str, display_name: str, provider: str) -> None:
    """Create or update the users table row for an OAuth login."""
    from shared.utils.database_client import get_database_client
    from shared.utils.models import User

    db_client = get_database_client()
    if not db_client:
        return

    session = db_client.get_session()
    if not session:
        return

    try:
        user = session.query(User).filter(User.user_id == user_id).first()
        if user is None:
            user = User(
                user_id=user_id,
                email=email,
                display_name=display_name,
                oauth_provider=provider,
                roles=json.dumps([_cfg("OAUTH_DEFAULT_ROLE", "user")]),
            )
            session.add(user)
            logger.info("Created OAuth user %s via %s", user_id, provider)
        else:
            user.email = email
            user.display_name = display_name
            user.oauth_provider = provider
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.error("Error upserting OAuth user %s: %s", user_id, exc)
    finally:
        session.close()


@router.get("/auth/login/{provider}", tags=["Authentication"])
async def oauth_login(provider: str, request: Request):
    """Redirect the browser to the OAuth provider's authorization page."""
    provider = provider.lower()
    client = getattr(_get_oauth(), provider, None)
    if client is None:
        return JSONResponse(
            {"error": f"OAuth provider '{provider}' is not configured"},
            status_code=400,
        )

    next_url = request.query_params.get("next", "/dashboard")
    request.session["oauth_next"] = next_url

    base_url = str(request.base_url).rstrip("/")
    redirect_uri = f"{base_url}/auth/callback/{provider}"
    return await client.authorize_redirect(request, redirect_uri)


@router.get("/auth/callback/{provider}", tags=["Authentication"])
async def oauth_callback(provider: str, request: Request):
    """Exchange the authorization code, upsert the user, and open a session."""
    provider = provider.lower()
    client = getattr(_get_oauth(), provider, None)
    if client is None:
        return RedirectResponse(url="/login?error=provider_not_configured")

    # Exchange code for token
    try:
        token = await client.authorize_access_token(request)
    except Exception as exc:
        logger.error("OAuth token exchange failed (%s): %s", provider, exc)
        return RedirectResponse(url="/login?error=auth_failed")

    # Extract user identity from provider response
    try:
        if provider == "google":
            info = token.get("userinfo") or {}
            email = info.get("email", "")
            user_id = email or info.get("sub", "")
            display_name = info.get("name") or email or user_id

        elif provider == "github":
            resp = await client.get("user", token=token)
            info = resp.json()
            login = info.get("login", "")
            display_name = info.get("name") or login
            email = info.get("email") or ""

            # Public email may be null; fall back to primary verified email
            if not email:
                emails_resp = await client.get("user/emails", token=token)
                if emails_resp.status_code == 200:
                    email = next(
                        (
                            e["email"]
                            for e in emails_resp.json()
                            if e.get("primary") and e.get("verified")
                        ),
                        "",
                    )

            user_id = email if email else f"github:{login}"

        else:
            return RedirectResponse(url="/login?error=unknown_provider")

    except Exception as exc:
        logger.error("Failed to fetch OAuth profile (%s): %s", provider, exc)
        return RedirectResponse(url="/login?error=profile_fetch_failed")

    _upsert_oauth_user(user_id, email, display_name, provider)

    request.session["user"] = {
        "user_id": user_id,
        "display_name": display_name,
        "email": email,
        "provider": provider,
    }

    try:
        from shared.utils.audit_service import log, ACTION_LOGIN, RESOURCE_AUTH
        log(user_id, ACTION_LOGIN, RESOURCE_AUTH, details={"method": f"oauth:{provider}"}, request=request)
    except Exception:
        pass

    next_url = request.session.pop("oauth_next", "/dashboard")
    return RedirectResponse(url=next_url)
