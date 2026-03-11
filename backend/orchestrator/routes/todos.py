from __future__ import annotations

import todos as todos_service
from auth import get_current_user
from fastapi import APIRouter, Depends, HTTPException, Query
from schemas import TodoIn, TodoStatusUpdateIn


def create_todos_router() -> APIRouter:
    router = APIRouter()

    @router.post("/ingest/todo")
    @router.post("/mobile/ingest/todo")
    def ingest_todo(todo: TodoIn, user: dict = Depends(get_current_user)):
        todos_service.ingest_todo(todo)
        return {"ok": True, "id": todo.todo_id}

    @router.get("/todos")
    def list_todos(
        user: dict = Depends(get_current_user),
        open_only: bool = Query(default=False),
        order: str | None = Query(default=None),
    ):
        return {"todos": todos_service.list_todos(open_only=open_only, order=order)}

    @router.get("/mobile/todos")
    def list_mobile_todos(
        user: dict = Depends(get_current_user),
        order: str | None = Query(default=None),
    ):
        return {"todos": todos_service.list_todos(open_only=True, order=order)}

    @router.get("/todos/{todo_id}")
    @router.get("/mobile/todos/{todo_id}")
    def get_todo(todo_id: str, user: dict = Depends(get_current_user)):
        todo = todos_service.get_todo(todo_id)
        if not todo:
            raise HTTPException(status_code=404, detail="Todo not found")
        return todo

    @router.patch("/todos/{todo_id}/status")
    @router.patch("/mobile/todos/{todo_id}/status")
    def update_todo_status(
        todo_id: str,
        payload: TodoStatusUpdateIn,
        user: dict = Depends(get_current_user),
    ):
        updated = todos_service.update_todo_status(todo_id, payload.status)
        if not updated:
            raise HTTPException(status_code=404, detail="Todo not found")
        return {"ok": True}

    @router.delete("/todos/{todo_id}")
    def delete_todo(todo_id: str, user: dict = Depends(get_current_user)):
        deleted = todos_service.delete_todo(todo_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Todo not found")
        return {"ok": True}

    return router
