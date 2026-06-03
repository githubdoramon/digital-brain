from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

import generated_pdfs
from auth import get_current_user


def create_generated_pdfs_router() -> APIRouter:
    router = APIRouter()

    @router.get("/generated-pdfs/{artifact_id}/download")
    @router.get("/mobile/generated-pdfs/{artifact_id}/download")
    def download_generated_pdf(artifact_id: str, user: dict = Depends(get_current_user)):
        info = generated_pdfs.get_generated_pdf_file(
            artifact_id,
            user_email=str(user.get("email") or "").strip() or None,
        )
        if not info:
            raise HTTPException(status_code=404, detail="Generated PDF not found")
        file_path = info.get("file_path")
        if not file_path or not os.path.exists(file_path):
            raise HTTPException(status_code=404, detail="Generated PDF unavailable")
        return FileResponse(
            file_path,
            media_type=info.get("file_mime") or generated_pdfs.PDF_MIME_TYPE,
            filename=info.get("filename") or "generated.pdf",
        )

    return router
