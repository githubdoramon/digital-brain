from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import generated_pdfs
from agent.controller import AgentController
from agent.state import AgentState, ToolCallRecord

pytest.importorskip("pymupdf")


def test_create_generated_pdf_stores_owner_scoped_downloadable_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(generated_pdfs, "PDF_STORAGE_DIR", tmp_path)

    artifact = generated_pdfs.create_generated_pdf(
        title="Quarterly Inventory",
        body_markdown=(
            "## Stock\n\n"
            "| Item | Count |\n"
            "| --- | --- |\n"
            "| Widget A | 12 |\n"
            "| Widget B | 8 |\n"
        ),
        filename="inventory report.pdf",
        user_email="user@example.com",
    )

    assert artifact.artifact_id.startswith("pdf:")
    assert artifact.filename == "inventory-report.pdf"
    assert artifact.download_url.endswith(f"/{artifact.artifact_id}/download")
    assert artifact.web_download_url.endswith(f"/{artifact.artifact_id}/download")
    assert artifact.mobile_download_url.endswith(f"/{artifact.artifact_id}/download")
    assert artifact.file_size > 0
    assert (tmp_path / artifact.file_path.split("/")[-1]).read_bytes().startswith(b"%PDF")

    owner_file = generated_pdfs.get_generated_pdf_file(
        artifact.artifact_id,
        user_email="user@example.com",
    )
    other_user_file = generated_pdfs.get_generated_pdf_file(
        artifact.artifact_id,
        user_email="other@example.com",
    )

    assert owner_file is not None
    assert owner_file["filename"] == "inventory-report.pdf"
    assert other_user_file is None


def test_ingest_generated_pdf_uses_regular_document_ingest_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(generated_pdfs, "PDF_STORAGE_DIR", tmp_path)
    artifact = generated_pdfs.create_generated_pdf(
        title="Travel Checklist",
        body_markdown="- Passport\n- Boarding pass",
        user_email="user@example.com",
    )
    captured = {}

    def fake_ingest_document(**kwargs):
        upload = kwargs["upload"]
        captured["title"] = kwargs["title"]
        captured["tags"] = kwargs["tags"]
        captured["user_email"] = kwargs["user_email"]
        captured["filename"] = upload.filename
        captured["content_type"] = upload.content_type
        captured["bytes"] = upload.file.read(4)
        return {
            "document_id": "doc:generated",
            "title": kwargs["title"],
            "file_name": upload.filename,
            "file_mime": upload.content_type,
            "file_size": 123,
            "download_url": "/documents/doc:generated/download",
            "tags": kwargs["tags"],
            "description": kwargs["description"],
        }

    monkeypatch.setitem(sys.modules, "documents", SimpleNamespace(ingest_document=fake_ingest_document))

    document = generated_pdfs.ingest_generated_pdf(
        artifact_id=artifact.artifact_id,
        title="Checklist PDF",
        tags=["Travel"],
        user_email="user@example.com",
    )

    assert document["document_id"] == "doc:generated"
    assert captured == {
        "title": "Checklist PDF",
        "tags": ["Travel"],
        "user_email": "user@example.com",
        "filename": "Travel-Checklist.pdf",
        "content_type": "application/pdf",
        "bytes": b"%PDF",
    }


def test_ingest_generated_pdf_rejects_other_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(generated_pdfs, "PDF_STORAGE_DIR", tmp_path)
    artifact = generated_pdfs.create_generated_pdf(
        title="Private Summary",
        body_markdown="Private content",
        user_email="user@example.com",
    )

    with pytest.raises(generated_pdfs.PdfGenerationError, match="not found"):
        generated_pdfs.ingest_generated_pdf(
            artifact_id=artifact.artifact_id,
            user_email="other@example.com",
        )


def test_controller_derives_generated_file_metadata_from_create_pdf_tool():
    state = AgentState(goal="Create a PDF")
    state.record_tool_call(
        ToolCallRecord(
            tool_name="create_pdf",
            arguments={"title": "Summary", "body_markdown": "Content"},
            result={
                "success": True,
                "artifact": {
                    "artifact_id": "pdf:11111111111111111111111111111111",
                    "title": "Summary",
                    "filename": "Summary.pdf",
                    "file_mime": "application/pdf",
                    "file_size": 42,
                    "download_url": "/generated-pdfs/pdf:11111111111111111111111111111111/download",
                    "web_download_url": "/api/orchestrator/generated-pdfs/pdf:11111111111111111111111111111111/download",
                    "mobile_download_url": "/mobile/generated-pdfs/pdf:11111111111111111111111111111111/download",
                },
            },
            duration_ms=5,
            success=True,
        )
    )

    files = AgentController._build_generated_files(AgentController.__new__(AgentController), state)

    assert files == [
        {
            "kind": "generated_pdf",
            "artifact_id": "pdf:11111111111111111111111111111111",
            "title": "Summary",
            "filename": "Summary.pdf",
            "file_mime": "application/pdf",
            "file_size": 42,
            "download_url": "/generated-pdfs/pdf:11111111111111111111111111111111/download",
            "web_download_url": "/api/orchestrator/generated-pdfs/pdf:11111111111111111111111111111111/download",
            "mobile_download_url": "/mobile/generated-pdfs/pdf:11111111111111111111111111111111/download",
        }
    ]
