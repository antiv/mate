"""
Authentication API routes for MATE auth server.

Handles token generation, revocation, login/logout, and the login page.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from shared.utils.auth_utils import (
    generate_token, verify_token, revoke_token, active_tokens,
    logout_basic_auth, clear_logged_out_status,
)
from server.auth import (
    verify_credentials, verify_bearer_token, get_auth_user,
    AUTH_USERNAME, AUTH_PASSWORD,
)

logger = logging.getLogger(__name__)

router = APIRouter()

project_root = Path(__file__).parent.parent


@router.get("/login", tags=["Dashboard - Pages"])
async def login_page(request: Request):
    """Login page for dashboard (no auth required)."""
    templates_dir = project_root / "templates"
    templates = Jinja2Templates(directory=str(templates_dir))
    return templates.TemplateResponse("login.html", {"request": request})


@router.post("/auth/token", tags=["Authentication"])
async def generate_auth_token(credentials: HTTPBasicCredentials = Depends(verify_credentials)):
    """Generate a Bearer token for authenticated users."""
    clear_logged_out_status(credentials.username, credentials.password)
    token = generate_token()
    try:
        from shared.utils.audit_service import log, ACTION_LOGIN, RESOURCE_AUTH
        log(credentials.username, ACTION_LOGIN, RESOURCE_AUTH, details={"method": "token"}, request=None)
    except Exception as e:
        logger.debug("Audit log login: %s", e)
    logger.debug("Token generated, active_tokens count: %d", len(active_tokens))
    return {"access_token": token, "token_type": "bearer", "username": credentials.username}


@router.post("/auth/revoke", tags=["Authentication"])
async def revoke_auth_token(token: str, credentials=Depends(verify_bearer_token)):
    """Revoke a Bearer token."""
    revoke_token(token)
    return {"message": "Token revoked successfully"}


@router.delete("/auth/token", tags=["Authentication"])
async def revoke_auth_token_delete(request: Request, username: str = Depends(get_auth_user)):
    """Revoke the current Bearer token."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        revoke_token(token)
        return {"message": "Token revoked successfully"}
    return {"message": "No token to revoke"}


@router.post("/auth/logout", tags=["Authentication"])
async def logout(request: Request):
    """Logout endpoint - revokes token and invalidates basic auth (no auth required)."""
    import base64

    username = None
    password = None

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        logger.debug("Logout: revoking bearer token")
        revoke_token(token)
    elif auth_header.startswith("Basic "):
        try:
            encoded_credentials = auth_header[6:]
            decoded_credentials = base64.b64decode(encoded_credentials).decode("utf-8")
            username, password = decoded_credentials.split(":", 1)
            if username == AUTH_USERNAME and password == AUTH_PASSWORD:
                logger.debug("Logout: logging out basic auth for %s", username)
                logout_basic_auth(username, password)
        except Exception as e:
            logger.debug("Logout: error parsing basic auth: %s", e)

    cookies = request.cookies
    token = cookies.get("auth_token")
    if token:
        logger.debug("Logout: revoking cookie token")
        revoke_token(token)

    try:
        body = await request.json()
        if not username and "username" in body and "password" in body:
            username = body["username"]
            password = body["password"]
            if username == AUTH_USERNAME and password == AUTH_PASSWORD:
                logger.debug("Logout: logging out basic auth from body for %s", username)
                logout_basic_auth(username, password)
    except Exception:
        pass

    if not username:
        if token or auth_header.startswith("Bearer "):
            logger.debug("Logout: fallback - marking configured credentials as logged out")
            logout_basic_auth(AUTH_USERNAME, AUTH_PASSWORD)

    try:
        from shared.utils.audit_service import log, ACTION_LOGOUT, RESOURCE_AUTH
        log(username or AUTH_USERNAME, ACTION_LOGOUT, RESOURCE_AUTH, details={"method": "logout"}, request=request)
    except Exception as e:
        logger.debug("Audit log logout: %s", e)

    response = JSONResponse({"message": "Logged out successfully"})
    response.set_cookie("auth_token", "", max_age=0, path="/", httponly=False, samesite="lax")
    response.set_cookie("auth_token", "", max_age=0, path="/dashboard", httponly=False, samesite="lax")
    response.set_cookie("auth_username", "", max_age=0, path="/", httponly=False, samesite="lax")
    response.set_cookie("auth_password", "", max_age=0, path="/", httponly=False, samesite="lax")
    return response
