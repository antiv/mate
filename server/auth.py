"""
Authentication dependencies for MATE auth server.

Provides FastAPI dependency functions for verifying credentials,
bearer tokens, and extracting authenticated users from requests.

Priority order for dashboard routes:
  1. Encrypted session cookie (OAuth users and basic-auth sessions)
  2. Bearer token in Authorization header
  3. HTTP Basic credentials in Authorization header
  4. auth_token cookie (legacy JS-set cookie)
"""

import logging
import base64
from urllib.parse import quote
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials, HTTPBearer, HTTPAuthorizationCredentials

from shared.utils.auth_utils import (
    verify_token, is_basic_auth_logged_out,
)

logger = logging.getLogger(__name__)

security = HTTPBasic()
bearer_scheme = HTTPBearer()

AUTH_USERNAME: str = ""
AUTH_PASSWORD: str = ""


def configure_auth(username: str, password: str):
    """Set the credentials used for authentication checks."""
    global AUTH_USERNAME, AUTH_PASSWORD
    AUTH_USERNAME = username
    AUTH_PASSWORD = password


def _get_session_user(request: Request):
    """Return the display identifier stored in the encrypted session, or None."""
    try:
        user = request.session.get("user")
        if user:
            return user.get("display_name") or user.get("user_id")
    except Exception:
        pass
    return None


def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    """Verify basic auth credentials.

    This does NOT check logged-out status because it is used for explicit
    login attempts.  Logged-out status only prevents automatic
    browser-sent credentials in get_auth_user / get_dashboard_auth_user.
    """
    if credentials.username == AUTH_USERNAME and credentials.password == AUTH_PASSWORD:
        return credentials
    raise HTTPException(
        status_code=401,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Basic"},
    )


def verify_bearer_token(credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme)):
    """Verify bearer token."""
    if not verify_token(credentials.credentials):
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return credentials


def get_auth_user(request: Request):
    """Get authenticated user from session, Bearer token, or Basic auth.

    Used for ADK proxy routes and admin docs; triggers browser popup for Basic auth.
    """
    # 1. Encrypted session (OAuth or basic-auth session)
    session_user = _get_session_user(request)
    if session_user:
        return session_user

    auth_header = request.headers.get("Authorization", "")

    # 2. Bearer token
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if verify_token(token):
            return AUTH_USERNAME
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3. Basic auth
    if auth_header.startswith("Basic "):
        try:
            encoded_credentials = auth_header[6:]
            decoded_credentials = base64.b64decode(encoded_credentials).decode("utf-8")
            username, password = decoded_credentials.split(":", 1)

            if is_basic_auth_logged_out(username, password):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Session logged out. Please login again.",
                    headers={"WWW-Authenticate": 'Basic realm="MATE"'},
                )

            if username == AUTH_USERNAME and password == AUTH_PASSWORD:
                return username
        except HTTPException:
            raise
        except Exception:
            pass

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": 'Basic realm="MATE"'},
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing authentication credentials",
        headers={"WWW-Authenticate": 'Basic realm="MATE"'},
    )


def get_dashboard_auth_user(request: Request):
    """Get authenticated user for dashboard routes without triggering browser popup.

    Returns a display name / identifier if authenticated, or None if not.
    Checks (in order): encrypted session → Bearer token → Basic auth header → auth_token cookie.
    """
    # 1. Encrypted session (OAuth or basic-auth session)
    session_user = _get_session_user(request)
    if session_user:
        return session_user

    auth_header = request.headers.get("Authorization", "")

    # 2. Bearer token
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if verify_token(token):
            return AUTH_USERNAME
        logger.debug("Dashboard auth: bearer token invalid")
        return None

    # 3. Basic auth header
    if auth_header.startswith("Basic "):
        try:
            encoded_credentials = auth_header[6:]
            decoded_credentials = base64.b64decode(encoded_credentials).decode("utf-8")
            username, password = decoded_credentials.split(":", 1)

            if is_basic_auth_logged_out(username, password):
                logger.debug("Dashboard auth: basic auth logged out for %s", username)
                return None

            if username == AUTH_USERNAME and password == AUTH_PASSWORD:
                return username
        except Exception:
            pass

    # 4. Legacy auth_token cookie (JS-set after basic auth login)
    token = request.cookies.get("auth_token")
    if token:
        if verify_token(token):
            return AUTH_USERNAME
        logger.debug("Dashboard auth: cookie token invalid")

    return None


def require_dashboard_auth(request: Request):
    """Require authentication for dashboard routes.

    Redirects to login page if not authenticated.
    """
    username = get_dashboard_auth_user(request)
    if username is None:
        redirect_url = str(request.url.path)
        if request.url.query:
            redirect_url += "?" + request.url.query
        encoded_redirect = quote(redirect_url, safe="")
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": f"/login?redirect={encoded_redirect}"},
        )
    return username
