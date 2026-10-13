from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

import contact_enrichment_proposals
from auth import get_current_user


def _user_email(user: dict[str, Any]) -> str:
    email = str(user.get("email") or user.get("user_email") or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="User email is missing")
    return email


def create_proposals_router() -> APIRouter:
    router = APIRouter()

    @router.get("/mobile/proposals")
    def list_mobile_proposals(user: dict = Depends(get_current_user)):
        return {
            "proposals": contact_enrichment_proposals.list_proposals(_user_email(user))
        }

    @router.post("/mobile/proposals/{proposal_id}/accept")
    def accept_mobile_proposal(proposal_id: str, user: dict = Depends(get_current_user)):
        result = contact_enrichment_proposals.accept_proposal(_user_email(user), proposal_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Proposal not found")
        if not result.get("accepted"):
            raise HTTPException(status_code=409, detail=result.get("reason") or "Proposal is resolved")
        return result

    @router.post("/mobile/proposals/{proposal_id}/dismiss")
    def dismiss_mobile_proposal(proposal_id: str, user: dict = Depends(get_current_user)):
        if not contact_enrichment_proposals.dismiss_proposal(_user_email(user), proposal_id):
            raise HTTPException(status_code=404, detail="Pending proposal not found")
        return {"ok": True, "proposal_id": proposal_id}

    return router
