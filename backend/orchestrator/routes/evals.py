from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from auth import get_current_user
from evals import list_eval_flows, run_eval_flow
from observability.logger import get_runtime_logger
from schemas import EvalRunIn

logger = get_runtime_logger(__name__)


def create_evals_router() -> APIRouter:
    router = APIRouter()

    @router.get("/evals/flows")
    def list_flows(user: dict = Depends(get_current_user)):
        del user
        return {"flows": list_eval_flows()}

    @router.post("/evals/run")
    async def run_eval(payload: EvalRunIn, user: dict = Depends(get_current_user)):
        user_email = str(user.get("email") or "").strip()
        if not user_email:
            raise HTTPException(status_code=400, detail="Authenticated user email missing")

        normalized_model = str(payload.llm_model or "").strip() or None
        logger.info(
            "[evals.run] flow_id=%s llm_model=%r repetitions=%s user=%s",
            payload.flow_id,
            normalized_model,
            payload.repetitions,
            user_email,
        )

        try:
            return await run_eval_flow(
                flow_id=payload.flow_id,
                llm_model=normalized_model,
                repetitions=payload.repetitions,
                user_email=user_email,
                discard_first_attempt=payload.discard_first_attempt,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router
