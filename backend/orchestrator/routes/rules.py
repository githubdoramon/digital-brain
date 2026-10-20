"""Private explicit instructions, separate from learned knowledge."""

from fastapi import APIRouter, Depends, HTTPException, Response

import user_rules
from auth import get_current_user


def create_rules_router():
    def private(response: Response):
        response.headers["Cache-Control"] = "no-store"

    router = APIRouter(prefix="/mobile/rules", tags=["rules"], dependencies=[Depends(private)])

    @router.get("")
    def listing(user=Depends(get_current_user)):
        return {"rules": user_rules.list_rules(user["email"])}

    @router.delete("/{rule_id}", status_code=204)
    def remove(rule_id: str, user=Depends(get_current_user)):
        if not user_rules.delete_rule(user["email"], rule_id):
            raise HTTPException(404, "Instruction not found")
        return Response(status_code=204, headers={"Cache-Control": "no-store"})

    return router
