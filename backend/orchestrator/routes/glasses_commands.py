from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from auth import get_current_user
from glasses_commands import GlassesGateCommand, execute


class GlassesCommandIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: GlassesGateCommand
    request_id: UUID
    captured_at_ms: int = Field(gt=0)


def create_glasses_commands_router() -> APIRouter:
    router = APIRouter(prefix="/mobile/glasses/commands")

    @router.post("")
    def command(payload: GlassesCommandIn, user: dict = Depends(get_current_user)):
        try:
            return execute(user["email"], payload.command, payload.request_id, payload.captured_at_ms)
        except ValueError as exc:
            raise HTTPException(409, "Command request ID already used") from exc

    return router
