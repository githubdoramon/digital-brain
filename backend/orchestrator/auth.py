"""Authentication middleware for Google OAuth JWT validation."""
import os
from typing import Optional

from fastapi import Header, HTTPException, status
from google.auth.transport import requests
from google.oauth2 import id_token


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


DEV_BYPASS_AUTH = _env_flag("DEV_BYPASS_AUTH")
DEV_USER_EMAIL = os.environ.get("DEV_USER_EMAIL", "").strip()

# Get Google Client ID from environment
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
if not GOOGLE_CLIENT_ID and not DEV_BYPASS_AUTH:
    raise ValueError("GOOGLE_CLIENT_ID is not set")
if DEV_BYPASS_AUTH and not DEV_USER_EMAIL:
    raise ValueError("DEV_USER_EMAIL is required when DEV_BYPASS_AUTH is enabled")

# Parse allowed users from environment
def get_allowed_users() -> Optional[set[str]]:
    """Get set of allowed user emails, or None if all users allowed."""
    allowlist = os.environ.get("ALLOWED_USERS", "").strip()
    if not allowlist:
        return None
    return {email.strip() for email in allowlist.split(",") if email.strip()}


ALLOWED_USERS = get_allowed_users()


def verify_google_token(token: str) -> dict:
    """
    Verify Google ID token and return user info.

    Args:
        token: Google ID token from frontend

    Returns:
        dict with user info (email, name, etc.)

    Raises:
        HTTPException: If token is invalid or expired
    """
    try:
        # Verify the token
        idinfo = id_token.verify_oauth2_token(
            token,
            requests.Request(),
            GOOGLE_CLIENT_ID
        )

        # Token is valid, return user info
        return idinfo
    except ValueError as e:
        # Invalid token
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication token: {str(e)}",
        )
    except Exception as e:
        # Other errors
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication failed: {str(e)}",
        )


def check_user_allowed(email: str) -> None:
    """
    Check if user email is in allowlist.

    Args:
        email: User's email address

    Raises:
        HTTPException: If user is not allowed
    """
    if ALLOWED_USERS is None:
        # No allowlist configured, allow all users
        return

    if email not in ALLOWED_USERS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Your account is not authorized.",
        )


async def get_current_user(authorization: Optional[str] = Header(None)) -> dict:
    """
    FastAPI dependency to get and validate current user from JWT.

    Args:
        authorization: Authorization header with Bearer token

    Returns:
        dict with user info

    Raises:
        HTTPException: If token is missing, invalid, or user not allowed
    """
    if DEV_BYPASS_AUTH:
        return {
            "email": DEV_USER_EMAIL,
            "user_email": DEV_USER_EMAIL,
            "name": DEV_USER_EMAIL.split("@")[0],
        }

    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Extract token from "Bearer <token>"
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Expected: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = parts[1]

    # Verify token and get user info
    user_info = verify_google_token(token)

    # Check if user is allowed
    email = user_info.get("email")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token does not contain email",
        )

    check_user_allowed(email)

    return user_info
