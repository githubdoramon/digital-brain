from __future__ import annotations

from typing import TYPE_CHECKING, Any

import generated_pdfs

if TYPE_CHECKING:
    from agent.state import AgentState


def handle_create_pdf(
    args: dict[str, Any],
    state: AgentState | None = None,
    user_email: str | None = None,
    **kwargs,
) -> dict[str, Any]:
    _ = kwargs
    try:
        artifact = generated_pdfs.create_generated_pdf(
            title=str(args.get("title") or ""),
            body_markdown=str(args.get("body_markdown") or ""),
            filename=args.get("filename") if isinstance(args.get("filename"), str) else None,
            user_email=user_email,
        )
    except generated_pdfs.PdfGenerationError as exc:
        return {"success": False, "error": str(exc)}
    except Exception as exc:
        return {"success": False, "error": f"Failed to create PDF: {exc}"}

    result: dict[str, Any] = {
        "success": True,
        "artifact": {
            "artifact_id": artifact.artifact_id,
            "title": artifact.title,
            "filename": artifact.filename,
            "file_mime": artifact.file_mime,
            "file_size": artifact.file_size,
            "download_url": artifact.download_url,
            "web_download_url": artifact.web_download_url,
            "mobile_download_url": artifact.mobile_download_url,
        },
    }

    if state is not None:
        state.add_action(f"Created PDF: {artifact.title}")

    if bool(args.get("ingest_as_document")):
        ingest_args = {
            "artifact_id": artifact.artifact_id,
            "title": args.get("document_title") or artifact.title,
            "tags": args.get("tags") if isinstance(args.get("tags"), list) else [],
            "contact_ids": args.get("contact_ids") if isinstance(args.get("contact_ids"), list) else [],
            "description": args.get("description") if isinstance(args.get("description"), str) else None,
        }
        ingest_result = _ingest_pdf_document(ingest_args, user_email=user_email)
        result["document"] = ingest_result
        if ingest_result.get("success") and state is not None:
            document = ingest_result.get("document") or {}
            state.add_action(f"Ingested generated PDF as document: {document.get('document_id')}")

    return result


def handle_ingest_generated_pdf(
    args: dict[str, Any],
    state: AgentState | None = None,
    user_email: str | None = None,
    **kwargs,
) -> dict[str, Any]:
    _ = kwargs
    result = _ingest_pdf_document(args, user_email=user_email)
    if result.get("success") and state is not None:
        document = result.get("document") or {}
        state.add_action(f"Ingested generated PDF as document: {document.get('document_id')}")
    return result


def _ingest_pdf_document(args: dict[str, Any], *, user_email: str | None) -> dict[str, Any]:
    try:
        document = generated_pdfs.ingest_generated_pdf(
            artifact_id=str(args.get("artifact_id") or ""),
            title=args.get("title") if isinstance(args.get("title"), str) else None,
            tags=args.get("tags") if isinstance(args.get("tags"), list) else [],
            contact_ids=args.get("contact_ids") if isinstance(args.get("contact_ids"), list) else [],
            description=args.get("description") if isinstance(args.get("description"), str) else None,
            user_email=user_email,
        )
    except generated_pdfs.PdfGenerationError as exc:
        return {"success": False, "error": str(exc)}
    except Exception as exc:
        return {"success": False, "error": f"Failed to ingest generated PDF: {exc}"}

    return {
        "success": True,
        "document": {
            "document_id": document.get("document_id"),
            "title": document.get("title"),
            "file_name": document.get("file_name"),
            "file_mime": document.get("file_mime"),
            "file_size": document.get("file_size"),
            "download_url": document.get("download_url"),
            "tags": document.get("tags") or [],
            "description": document.get("description"),
        },
    }
