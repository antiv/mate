import os
import hashlib
import logging
from datetime import datetime, timezone
from fastapi import Request, HTTPException, Depends, status

from shared.utils.database_client import get_database_client
from shared.utils.models import PersonalAccessToken, User
from shared.utils.auth_utils import verify_token
from server.auth import AUTH_USERNAME, AUTH_PASSWORD, is_basic_auth_logged_out

logger = logging.getLogger(__name__)

# Default roles allowed to access the OpenAI API endpoints
DEFAULT_ALLOWED_ROLES = {"admin", "developer"}

def get_allowed_roles() -> set:
    """Retrieve allowed roles from environment variables or use default."""
    env_roles = os.getenv("ALLOWED_API_ROLES")
    if env_roles:
        return {r.strip().lower() for r in env_roles.split(",") if r.strip()}
    return DEFAULT_ALLOWED_ROLES

def roles_allow_api(user_id: str, roles: list) -> bool:
    """True if a user with these roles may use the API: an admin, or a role in ALLOWED_API_ROLES."""
    if user_id == AUTH_USERNAME or "admin" in roles:
        return True
    return any(role in roles for role in get_allowed_roles())


def dashboard_user_may_hold_token(request: Request) -> bool:
    """True if the signed-in dashboard user may create personal access tokens.

    The same rule get_pat_user applies when a token is used, so a user is not
    offered a token the API would refuse.
    """
    from server.auth import get_dashboard_auth_user, is_admin_user
    from shared.utils.user_service import get_user_service

    user_id = get_dashboard_auth_user(request)
    if not user_id:
        return False
    if is_admin_user(request):
        return True
    try:
        roles = get_user_service().get_user_roles(user_id)
    except Exception as exc:
        logger.warning("Could not read the roles of %s: %s", user_id, exc)
        return False
    return roles_allow_api(user_id, roles)


def verify_pat_hash(token: str) -> str:
    """Compute the SHA-256 hash of a token string."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

async def get_pat_user(request: Request) -> User:
    """
    FastAPI dependency to authenticate requests using:
    1. Personal Access Token (PAT) in Authorization Bearer header
    2. Session tokens for local dashboard/widget requests
    3. Basic auth for admin/testing
    
    Verifies that the user has the required roles (e.g. admin or developer).
    """
    auth_header = request.headers.get("Authorization", "")
    db = get_database_client()
    session = db.get_session()
    
    if not session:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database connection failed"
        )
        
    try:
        user_record = None
        
        # 1. Bearer Token Authentication
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            
            # Check if it's an in-memory session token first (for dashboard UI calls)
            if verify_token(token):
                # Return a virtual admin user or lookup AUTH_USERNAME in DB
                user_record = session.query(User).filter_by(user_id=AUTH_USERNAME).first()
                if not user_record:
                    # Create a temporary virtual user object if admin doesn't exist in DB yet
                    user_record = User(user_id=AUTH_USERNAME, roles='["admin"]')
            else:
                # Treat as Personal Access Token (PAT)
                token_hash = verify_pat_hash(token)
                token_record = session.query(PersonalAccessToken).filter_by(token_hash=token_hash).first()
                
                if token_record:
                    # Check expiration
                    if token_record.expires_at and token_record.expires_at.replace(tzinfo=timezone.utc) < datetime.now(timezone.utc):
                        raise HTTPException(
                            status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Personal Access Token has expired",
                            headers={"WWW-Authenticate": "Bearer"}
                        )
                    
                    # Update last_used_at
                    token_record.last_used_at = datetime.now(timezone.utc)
                    session.commit()
                    
                    user_record = token_record.user
                    logger.debug("Successfully authenticated PAT for user: %s", user_record.user_id)
                else:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid Personal Access Token",
                        headers={"WWW-Authenticate": "Bearer"}
                    )
                    
        # 2. Basic Auth Authentication (e.g. local scripts or testing)
        elif auth_header.startswith("Basic "):
            import base64
            try:
                encoded_credentials = auth_header[6:]
                decoded_credentials = base64.b64decode(encoded_credentials).decode("utf-8")
                username, password = decoded_credentials.split(":", 1)
                
                if is_basic_auth_logged_out(username, password):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Session logged out. Please login again.",
                        headers={"WWW-Authenticate": 'Basic realm="MATE"'}
                    )
                    
                if username == AUTH_USERNAME and password == AUTH_PASSWORD:
                    # Authenticated as main admin
                    user_record = session.query(User).filter_by(user_id=AUTH_USERNAME).first()
                    if not user_record:
                        user_record = User(user_id=AUTH_USERNAME, roles='["admin"]')
            except Exception:
                pass
                
        # 3. Fallback to encrypted cookie session (e.g. if accessing through dashboard page)
        else:
            try:
                session_data = request.session.get("user")
                if session_data:
                    user_id = session_data.get("user_id") or session_data.get("email")
                    if user_id:
                        user_record = session.query(User).filter_by(user_id=user_id).first()
            except Exception:
                pass

        # If no user authenticated, throw 401
        if not user_record:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing or invalid authentication credentials",
                headers={"WWW-Authenticate": 'Bearer, Basic realm="MATE"'}
            )
            
        # 4. Role Authorization Check
        if not roles_allow_api(user_record.user_id, user_record.get_roles()):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"User roles {user_record.get_roles()} are not authorized to use the API (allowed: {list(get_allowed_roles())})"
            )
            
        return user_record
        
    finally:
        session.close()
