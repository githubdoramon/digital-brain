from __future__ import annotations

import json
import os
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

import documents
from auth import get_current_user, require_service_api_key
from observability.logger import get_runtime_logger
from schemas import DocumentCollection, DocumentDetailOut, DocumentSearchIn, DocumentUpdateIn

logger = get_runtime_logger(__name__)


def create_documents_router(
) -> APIRouter:
    router = APIRouter()

    @router.post("/ingest/document", response_model=DocumentDetailOut)
    async def upload_document(
        title: str | None = Form(None),
        tags: str | None = Form(None),
        description: str | None = Form(None),
        document_date: str | None = Form(None),
        file: UploadFile = File(...),
        user: dict = Depends(get_current_user),
    ):
        try:
            parsed_tags = _parse_tags_payload(tags)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            parsed_date = _parse_iso_datetime(document_date)
        except HTTPException:
            raise
        except Exception:
            parsed_date = None

        try:
            document = documents.ingest_document(
                title=title,
                tags=parsed_tags,
                description=description,
                upload=file,
                document_date=parsed_date,
            )
        except documents.DocumentProcessingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("[documents] Failed to ingest document: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to ingest document") from exc

        return DocumentDetailOut(**document)

    @router.post("/ingest/document/external")
    def ingest_external_document(
        file: UploadFile = File(...),
        _: None = Depends(require_service_api_key),
    ):
        try:
            document = documents.ingest_document(
                title=None,
                tags=None,
                description=None,
                upload=file,
                document_date=None,
            )
        except documents.DocumentProcessingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("[documents] Failed to ingest document: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to ingest document") from exc

        return DocumentDetailOut(**document)

    @router.get("/documents", response_model=DocumentCollection)
    def list_documents(
        limit: int = Query(200, ge=1, le=200),
        offset: int = Query(0, ge=0),
        user: dict = Depends(get_current_user),
    ):
        docs = documents.list_documents(limit=limit, offset=offset)
        return DocumentCollection(documents=docs)

    @router.get("/documents/{document_id}", response_model=DocumentDetailOut)
    def get_document_detail(document_id: str, user: dict = Depends(get_current_user)):
        document = documents.get_document(document_id)
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        return DocumentDetailOut(**document)

    @router.patch("/documents/{document_id}", response_model=DocumentDetailOut)
    def update_document(
        document_id: str,
        payload: DocumentUpdateIn,
        user: dict = Depends(get_current_user),
    ):
        try:
            document = documents.update_document_metadata(
                document_id,
                title=payload.title,
                tags=payload.tags,
                description=payload.description,
                document_date=payload.document_date,
            )
        except documents.DocumentProcessingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("[documents] Failed to update document metadata: %s", exc)
            raise HTTPException(status_code=500, detail="Failed to update document") from exc

        if not document:
            raise HTTPException(status_code=404, detail="Document not found")
        return DocumentDetailOut(**document)

    @router.post("/documents/search", response_model=DocumentCollection)
    def search_documents_endpoint(payload: DocumentSearchIn, user: dict = Depends(get_current_user)):
        limit = payload.limit or 20
        docs = documents.search_documents(payload.query, tags=payload.tags, limit=limit)
        return DocumentCollection(documents=docs)

    @router.delete("/documents/{document_id}")
    def delete_document(document_id: str, user: dict = Depends(get_current_user)):
        deleted = documents.delete_document(document_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Document not found")
        return {"ok": True}

    @router.get("/documents/{document_id}/download")
    def download_document(document_id: str, user: dict = Depends(get_current_user)):
        info = documents.get_document_file(document_id)
        if not info:
            raise HTTPException(status_code=404, detail="Document not found")
        file_path = info.get("file_path")
        if not file_path or not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="File unavailable")
        media_type = info.get("file_mime") or "application/octet-stream"
        filename = info.get("file_name") or document_id
        return FileResponse(file_path, media_type=media_type, filename=filename)

    return router


def _parse_tags_payload(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = [part.strip() for part in raw.split(",")]
    if not isinstance(parsed, list):
        raise ValueError("tags must be an array")
    tags: list[str] = []
    for item in parsed:
        if item is None:
            continue
        candidate = str(item).strip()
        if candidate:
            tags.append(candidate)
    return tags


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid document_date: {value}") from exc
