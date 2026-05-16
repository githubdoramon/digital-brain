"""Authentication middleware for Google OAuth JWT validation."""

import os

from fastapi import Header, HTTPException, status
from google.auth.transport import requests
from google.oauth2 import id_token

from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)


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


def get_allowed_users() -> set[str]:
    """Get the required set of allowed user emails."""
    allowlist = os.environ.get("ALLOWED_USERS", "").strip()
    if not allowlist:
        raise ValueError("ALLOWED_USERS must be configured and non-empty unless DEV_BYPASS_AUTH is enabled")
    users = {email.strip() for email in allowlist.split(",") if email.strip()}
    if not users:
        raise ValueError("ALLOWED_USERS must contain at least one email address")
    return users


ALLOWED_USERS = set() if DEV_BYPASS_AUTH else get_allowed_users()
ORCHESTRATOR_API_KEY = os.environ.get("ORCHESTRATOR_API_KEY")


def require_service_api_key(
    x_service_api_key: str = Header(default="", alias="x-service-api-key"),
) -> None:
    """Validate internal service API key for service-to-service endpoints."""
    if not ORCHESTRATOR_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Service API key is not configured",
        )
    if x_service_api_key != ORCHESTRATOR_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid service API key",
        )


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
        idinfo = id_token.verify_oauth2_token(token, requests.Request(), GOOGLE_CLIENT_ID)

        # Token is valid, return user info
        return idinfo
    except ValueError as e:
        logger.warning("Invalid authentication token", extra={"error": str(e)})
        # Invalid token
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication token: {e!s}",
        )
    except Exception as e:
        logger.exception("Authentication failed")
        # Other errors
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication failed: {e!s}",
        )


def check_user_allowed(email: str) -> None:
    """
    Check if user email is in allowlist.

    Args:
        email: User's email address

    Raises:
        HTTPException: If user is not allowed
    """
    if email not in ALLOWED_USERS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Your account is not authorized.",
        )


async def get_current_user(authorization: str | None = Header(None)) -> dict:
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
        logger.warning("Missing authorization header")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Extract token from "Bearer <token>"
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        logger.warning("Invalid authorization header format")
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
        logger.warning("Token missing email")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token does not contain email",
        )

    check_user_allowed(email)

    return user_info
