"""Authentication for robot-gateway."""

from __future__ import annotations

import os

from fastapi import Header, HTTPException, status
from google.auth.transport import requests
from google.oauth2 import id_token

from config import ORCHESTRATOR_API_KEY
from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


DEV_BYPASS_AUTH = _env_flag("DEV_BYPASS_AUTH")
DEV_USER_EMAIL = os.environ.get("DEV_USER_EMAIL", "").strip()
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")

if not GOOGLE_CLIENT_ID and not DEV_BYPASS_AUTH:
    raise ValueError("GOOGLE_CLIENT_ID is not set")
if DEV_BYPASS_AUTH and not DEV_USER_EMAIL:
    raise ValueError("DEV_USER_EMAIL is required when DEV_BYPASS_AUTH is enabled")


def get_allowed_users() -> set[str]:
    allowlist = os.environ.get("ALLOWED_USERS", "").strip()
    if not allowlist:
        raise ValueError("ALLOWED_USERS must be configured and non-empty unless DEV_BYPASS_AUTH is enabled")
    users = {email.strip() for email in allowlist.split(",") if email.strip()}
    if not users:
        raise ValueError("ALLOWED_USERS must contain at least one email address")
    return users


ALLOWED_USERS = set() if DEV_BYPASS_AUTH else get_allowed_users()


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
    try:
        return id_token.verify_oauth2_token(token, requests.Request(), GOOGLE_CLIENT_ID)
    except ValueError as exc:
        logger.warning("Invalid authentication token", extra={"error": str(exc)})
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid authentication token: {exc!s}",
        ) from exc
    except Exception as exc:
        logger.exception("Authentication failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication failed: {exc!s}",
        ) from exc


def check_user_allowed(email: str) -> None:
    if email not in ALLOWED_USERS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Your account is not authorized.",
        )


async def get_current_user(authorization: str | None = Header(None)) -> dict:
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

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization header format. Expected: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_info = verify_google_token(parts[1])
    email = user_info.get("email")
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token does not contain email",
        )

    check_user_allowed(email)
    return user_info
