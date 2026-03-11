from __future__ import annotations

from time import perf_counter
from typing import Any

import skills
import telegram_bot
from agent.state import AgentState
from auth import get_current_user, require_service_api_key
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from observability.logger import get_runtime_logger
from schemas import ToolRunIn, ToolRunOut
from tools.handlers import get_handler
from tools.registry import get_registry
from tools.validators.pre_execution import PreExecutionValidator

logger = get_runtime_logger(__name__)


def create_automation_router() -> APIRouter:
    router = APIRouter()

    @router.get("/skills")
    def list_skills(user: dict = Depends(get_current_user)):
        registry = skills.get_registry()
        skill_list = registry.list_skills()
        return {
            "skills": [s.to_dict() for s in skill_list],
            "total": len(skill_list),
        }

    @router.get("/skills/{skill_name}")
    def get_skill(skill_name: str, user: dict = Depends(get_current_user)):
        registry = skills.get_registry()
        skill_obj = registry.get_skill(skill_name)
        if not skill_obj:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
        return skill_obj.to_dict()

    @router.post("/skills/match")
    def match_skills(
        query: str = Query(..., description="User query to match against skills"),
        max_skills: int = Query(2, ge=1, le=5, description="Maximum skills to return"),
        min_confidence: float = Query(
            0.5,
            ge=0.0,
            le=1.0,
            description="Minimum confidence threshold",
        ),
        user: dict = Depends(get_current_user),
    ):
        registry = skills.get_registry()
        matches = registry.find_matching_skills(
            query,
            max_skills=max_skills,
            min_confidence=min_confidence,
        )
        return {
            "query": query,
            "matches": [m.to_dict() for m in matches],
            "total_matches": len(matches),
        }

    @router.get("/skills/stats")
    def get_skills_stats(user: dict = Depends(get_current_user)):
        registry = skills.get_registry()
        return registry.get_stats()

    @router.post("/skills/reload")
    def reload_skills(user: dict = Depends(get_current_user)):
        from skills.registry import reload_registry

        count = reload_registry()
        return {"reloaded": count, "message": f"Reloaded {count} skills"}

    @router.post("/tools/run", response_model=ToolRunOut)
    def run_tool(payload: ToolRunIn, user: dict = Depends(get_current_user)):
        user_email = user.get("email")
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")

        registry = get_registry()
        contract = registry.get_contract(payload.tool_name)
        if not contract:
            raise HTTPException(status_code=404, detail=f"Unknown tool: {payload.tool_name}")

        validator = PreExecutionValidator(registry)
        validation = validator.validate(payload.tool_name, payload.args)
        if not validation.valid:
            raise HTTPException(status_code=400, detail=validation.to_message())

        normalized_args = contract.normalize(payload.args)
        handler = get_handler(payload.tool_name)
        if handler is None:
            raise HTTPException(status_code=500, detail=f"Tool handler not found: {payload.tool_name}")

        state = AgentState(goal=f"tool_run:{payload.tool_name}")
        llm_model_override = str(payload.llm_model or "").strip() or None
        logger.info(
            "[tools/run] Running tool=%s user=%s llm_model=%s",
            payload.tool_name,
            user_email,
            llm_model_override or "default",
        )
        search_limit = normalized_args.get("limit")
        if not isinstance(search_limit, int):
            search_limit = 30

        start = perf_counter()
        try:
            result = handler(
                normalized_args,
                state=state,
                question="",
                search_limit=search_limit,
                user_email=user_email,
                conversation_history=None,
                llm_model=llm_model_override,
            )
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Tool execution failed: {exc}") from exc
        duration_ms = (perf_counter() - start) * 1000

        return ToolRunOut(
            tool_name=payload.tool_name,
            args=payload.args,
            normalized_args=normalized_args,
            result=result,
            duration_ms=duration_ms,
            llm_model=llm_model_override,
        )

    @router.post("/webhooks/telegram/messages")
    async def handle_telegram_messages(
        payload: dict[str, Any],
        request: Request,
    ):
        try:
            return telegram_bot.process_update(
                payload,
                secret_token=request.headers.get("X-Telegram-Bot-Api-Secret-Token"),
            )
        except telegram_bot.TelegramAuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except telegram_bot.TelegramConfigError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except telegram_bot.TelegramProcessingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except telegram_bot.TelegramUploadError as exc:
            logger.exception("[telegram_bot] upload error=%s", exc)
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @router.get("/agents/emergency-stock/run")
    def run_emergency_stock_endpoint(
        _=Depends(require_service_api_key),
    ):
        from agents.emergency_stock.executor import handle_emergency_stock_request

        result = handle_emergency_stock_request()
        if result.get("status") == "error":
            raise HTTPException(status_code=500, detail=result.get("message", "Unknown error"))
        return result

    return router
