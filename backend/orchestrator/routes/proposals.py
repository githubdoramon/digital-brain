from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import contact_enrichment_proposals
import intelligence
from auth import get_current_user


class ContactProposalAcceptance(BaseModel):
    contact_resolutions: dict[str, str] = Field(default_factory=dict, max_length=10)


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
            "proposals": (
                contact_enrichment_proposals.list_proposals(_user_email(user))
                + intelligence.list_proposals(_user_email(user))
            )
        }

    @router.post("/mobile/proposals/{proposal_id}/accept")
    def accept_mobile_proposal(
        proposal_id: str,
        body: ContactProposalAcceptance | None = None,
        user: dict = Depends(get_current_user),
    ):
        result = (
            intelligence.resolve_proposal(
                _user_email(user),
                proposal_id,
                accept=True,
                contact_resolutions=body.contact_resolutions if body else None,
            )
            if proposal_id.startswith("intel_")
            else contact_enrichment_proposals.accept_proposal(_user_email(user), proposal_id)
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Proposal not found")
        if not result.get("accepted"):
            detail = {
                "source_changed": "The source has changed. Dismiss this suggestion and review the latest information.",
                "task_changed": "This task has changed. Review the task before accepting a suggestion.",
                "related_item_changed": "A related person or project has changed. Review this suggestion again.",
                "already_resolved": "This suggestion has already been handled.",
                "contact_changed": "This contact has changed. Review the latest details before accepting.",
                "contact_resolution_required": "Choose the person for each suggested contact change.",
            }.get(result.get("reason"), "This suggestion is no longer available.")
            raise HTTPException(status_code=409, detail=detail)
        return result

    @router.post("/mobile/proposals/{proposal_id}/dismiss")
    def dismiss_mobile_proposal(proposal_id: str, user: dict = Depends(get_current_user)):
        dismissed = (
            intelligence.resolve_proposal(_user_email(user), proposal_id, accept=False)
            if proposal_id.startswith("intel_")
            else contact_enrichment_proposals.dismiss_proposal(_user_email(user), proposal_id)
        )
        if not dismissed:
            raise HTTPException(status_code=404, detail="Pending proposal not found")
        return {"ok": True, "proposal_id": proposal_id}

    return router
