"""
Shared authentication utilities for MATE (Multi-Agent Tree Engine)
Provides token verification that can be used by both auth_server and dashboard_server
"""

import logging
import hashlib
from datetime import datetime, timedelta
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

# In-memory token storage (shared across all modules)
_active_tokens = set()

# Track logged-out basic auth credentials (credential_hash -> logout_time)
# Credentials are rejected for LOGOUT_EXPIRY_MINUTES after logout
_logged_out_credentials: Dict[str, datetime] = {}
LOGOUT_EXPIRY_MINUTES = 5  # Reject logged-out credentials for 5 minutes

def generate_token() -> str:
    """Generate a secure random token."""
    import secrets
    token = secrets.token_urlsafe(32)
    _active_tokens.add(token)
    logger.debug("Token generated, active_tokens count: %d", len(_active_tokens))
    return token

def verify_token(token: str) -> bool:
    """Verify if a token is valid."""
    is_valid = token in _active_tokens
    if not is_valid:
        logger.debug("Token verification failed, active_tokens count: %d", len(_active_tokens))
    return is_valid

def revoke_token(token: str):
    """Revoke a token."""
    _active_tokens.discard(token)

def _hash_credentials(username: str, password: str) -> str:
    """Create a hash of username:password for tracking logged-out sessions."""
    credential_string = f"{username}:{password}"
    return hashlib.sha256(credential_string.encode()).hexdigest()

def logout_basic_auth(username: str, password: str):
    """Mark basic auth credentials as logged out.
    These credentials will be rejected for LOGOUT_EXPIRY_MINUTES."""
    credential_hash = _hash_credentials(username, password)
    _logged_out_credentials[credential_hash] = datetime.now()
    logger.debug("Logged out basic auth for user: %s", username)
    # Clean up old entries
    _cleanup_logged_out_credentials()

def is_basic_auth_logged_out(username: str, password: str) -> bool:
    """Check if basic auth credentials are logged out."""
    credential_hash = _hash_credentials(username, password)
    
    if credential_hash not in _logged_out_credentials:
        return False
    
    logout_time = _logged_out_credentials[credential_hash]
    expiry_time = logout_time + timedelta(minutes=LOGOUT_EXPIRY_MINUTES)
    
    # If expired, remove from set and allow login
    if datetime.now() > expiry_time:
        del _logged_out_credentials[credential_hash]
        return False
    
    # Still within logout period, reject
    return True

def clear_logged_out_status(username: str, password: str):
    """Clear logged-out status for credentials when user successfully logs in."""
    credential_hash = _hash_credentials(username, password)
    if credential_hash in _logged_out_credentials:
        del _logged_out_credentials[credential_hash]
        logger.debug("Cleared logged-out status for user: %s", username)

def _cleanup_logged_out_credentials():
    """Remove expired logged-out credentials."""
    now = datetime.now()
    expired_keys = [
        key for key, logout_time in _logged_out_credentials.items()
        if now > logout_time + timedelta(minutes=LOGOUT_EXPIRY_MINUTES)
    ]
    for key in expired_keys:
        del _logged_out_credentials[key]

def get_active_tokens():
    """Get the active tokens set (for debugging/monitoring)."""
    return _active_tokens

# Export active_tokens for backward compatibility
active_tokens = _active_tokens

