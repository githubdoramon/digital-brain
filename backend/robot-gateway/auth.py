"""Authentication for robot-gateway.

Service-to-service: require_service_api_key (used by all current routes)

Mobile (future): When /mobile/ routes are added, add google-auth to
requirements.txt and implement get_current_user following the pattern in
backend/orchestrator/auth.py (Google OAuth JWT validation + ALLOWED_USERS).
The frontend middleware already bypasses session auth for
/api/robot-gateway/mobile/ paths carrying a Bearer token.
"""

from fastapi import Header, HTTPException, status

from config import ORCHESTRATOR_API_KEY


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
