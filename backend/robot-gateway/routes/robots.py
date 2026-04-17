"""Robot and module CRUD endpoints."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from auth import require_service_api_key
from db import get_conn
from observability.logger import get_runtime_logger
from schemas import (
    ModuleIn,
    ModuleOut,
    ModuleUpdateIn,
    RobotIn,
    RobotOut,
    RobotUpdateIn,
)

logger = get_runtime_logger(__name__)


def create_robots_router() -> APIRouter:
    router = APIRouter()

    # ------------------------------------------------------------------
    # Robots
    # ------------------------------------------------------------------

    @router.post("/robots", response_model=RobotOut, status_code=201)
    def register_robot(
        body: RobotIn,
        _: None = Depends(require_service_api_key),
    ):
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO robots (robot_id, name, description, tags, metadata)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING robot_id, name, description, status, tags, metadata,
                          last_seen_at, registered_at, updated_at
                """,
                (
                    body.robot_id,
                    body.name,
                    body.description,
                    body.tags,
                    json.dumps(body.metadata),
                ),
            )
            row = cur.fetchone()
            conn.commit()

        return {**dict(row), "modules": []}

    @router.get("/robots", response_model=list[RobotOut])
    def list_robots(_: None = Depends(require_service_api_key)):
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT robot_id, name, description, status, tags, metadata,
                       last_seen_at, registered_at, updated_at
                FROM robots ORDER BY registered_at DESC
                """
            )
            robots = [dict(row) for row in cur.fetchall()]

            if robots:
                robot_ids = [r["robot_id"] for r in robots]
                cur.execute(
                    """
                    SELECT module_id, robot_id, name, module_type, status, capabilities,
                           metadata, last_seen_at, registered_at, updated_at
                    FROM robot_modules
                    WHERE robot_id = ANY(%s)
                    ORDER BY registered_at
                    """,
                    (robot_ids,),
                )
                modules_by_robot: dict[str, list[dict[str, Any]]] = {}
                for row in cur.fetchall():
                    modules_by_robot.setdefault(row["robot_id"], []).append(dict(row))

                for robot in robots:
                    robot["modules"] = modules_by_robot.get(robot["robot_id"], [])
            else:
                for robot in robots:
                    robot["modules"] = []

        return robots

    @router.get("/robots/{robot_id}", response_model=RobotOut)
    def get_robot(robot_id: str, _: None = Depends(require_service_api_key)):
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT robot_id, name, description, status, tags, metadata,
                       last_seen_at, registered_at, updated_at
                FROM robots WHERE robot_id = %s
                """,
                (robot_id,),
            )
            robot = cur.fetchone()
            if not robot:
                raise HTTPException(status_code=404, detail="Robot not found")

            cur.execute(
                """
                SELECT module_id, robot_id, name, module_type, status, capabilities,
                       metadata, last_seen_at, registered_at, updated_at
                FROM robot_modules WHERE robot_id = %s ORDER BY registered_at
                """,
                (robot_id,),
            )
            modules = [dict(row) for row in cur.fetchall()]

        return {**dict(robot), "modules": modules}

    @router.put("/robots/{robot_id}", response_model=RobotOut)
    def update_robot(
        robot_id: str,
        body: RobotUpdateIn,
        _: None = Depends(require_service_api_key),
    ):
        updates = body.model_dump(exclude_none=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        set_clauses = []
        params: list[Any] = []
        for field, value in updates.items():
            if field == "metadata":
                set_clauses.append("metadata = %s")
                params.append(json.dumps(value))
            else:
                set_clauses.append(f"{field} = %s")
                params.append(value)
        set_clauses.append("updated_at = NOW()")
        params.append(robot_id)

        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE robots SET {', '.join(set_clauses)}
                WHERE robot_id = %s
                RETURNING robot_id, name, description, status, tags, metadata,
                          last_seen_at, registered_at, updated_at
                """,
                params,
            )
            robot = cur.fetchone()
            if not robot:
                raise HTTPException(status_code=404, detail="Robot not found")
            conn.commit()

            cur.execute(
                """
                SELECT module_id, robot_id, name, module_type, status, capabilities,
                       metadata, last_seen_at, registered_at, updated_at
                FROM robot_modules WHERE robot_id = %s ORDER BY registered_at
                """,
                (robot_id,),
            )
            modules = [dict(row) for row in cur.fetchall()]

        return {**dict(robot), "modules": modules}

    @router.delete("/robots/{robot_id}", status_code=204)
    def delete_robot(robot_id: str, _: None = Depends(require_service_api_key)):
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM robots WHERE robot_id = %s RETURNING robot_id", (robot_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Robot not found")
            conn.commit()

    # ------------------------------------------------------------------
    # Modules
    # ------------------------------------------------------------------

    @router.post("/robots/{robot_id}/modules", response_model=ModuleOut, status_code=201)
    def register_module(
        robot_id: str,
        body: ModuleIn,
        _: None = Depends(require_service_api_key),
    ):
        with get_conn() as conn, conn.cursor() as cur:
            # Verify robot exists
            cur.execute("SELECT 1 FROM robots WHERE robot_id = %s", (robot_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Robot not found")

            cur.execute(
                """
                INSERT INTO robot_modules
                    (module_id, robot_id, name, module_type, capabilities, metadata)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING module_id, robot_id, name, module_type, status, capabilities,
                          metadata, last_seen_at, registered_at, updated_at
                """,
                (
                    body.module_id,
                    robot_id,
                    body.name,
                    body.module_type,
                    body.capabilities,
                    json.dumps(body.metadata),
                ),
            )
            row = cur.fetchone()
            conn.commit()

        return dict(row)

    @router.get("/robots/{robot_id}/modules", response_model=list[ModuleOut])
    def list_modules(robot_id: str, _: None = Depends(require_service_api_key)):
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT module_id, robot_id, name, module_type, status, capabilities,
                       metadata, last_seen_at, registered_at, updated_at
                FROM robot_modules WHERE robot_id = %s ORDER BY registered_at
                """,
                (robot_id,),
            )
            return [dict(row) for row in cur.fetchall()]

    @router.put("/robots/{robot_id}/modules/{module_id}", response_model=ModuleOut)
    def update_module(
        robot_id: str,
        module_id: str,
        body: ModuleUpdateIn,
        _: None = Depends(require_service_api_key),
    ):
        updates = body.model_dump(exclude_none=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update")

        set_clauses = []
        params: list[Any] = []
        for field, value in updates.items():
            if field == "metadata":
                set_clauses.append("metadata = %s")
                params.append(json.dumps(value))
            else:
                set_clauses.append(f"{field} = %s")
                params.append(value)
        set_clauses.append("updated_at = NOW()")
        params.extend([robot_id, module_id])

        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE robot_modules SET {', '.join(set_clauses)}
                WHERE robot_id = %s AND module_id = %s
                RETURNING module_id, robot_id, name, module_type, status, capabilities,
                          metadata, last_seen_at, registered_at, updated_at
                """,
                params,
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Module not found")
            conn.commit()

        return dict(row)

    @router.delete("/robots/{robot_id}/modules/{module_id}", status_code=204)
    def delete_module(
        robot_id: str,
        module_id: str,
        _: None = Depends(require_service_api_key),
    ):
        with get_conn() as conn, conn.cursor() as cur:
            cur.execute(
                """
                DELETE FROM robot_modules
                WHERE robot_id = %s AND module_id = %s
                RETURNING module_id
                """,
                (robot_id, module_id),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Module not found")
            conn.commit()

    return router
