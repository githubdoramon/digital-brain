from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import UploadFile
from starlette.datastructures import Headers

from observability.logger import get_runtime_logger

logger = get_runtime_logger(__name__)

PDF_STORAGE_DIR = Path(os.getenv("PDF_STORAGE_DIR", "/app/storage/generated-pdfs"))
PDF_MIME_TYPE = "application/pdf"

PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0
MARGIN_X = 54.0
MARGIN_Y = 54.0
LINE_HEIGHT_FACTOR = 1.35
MAX_BODY_CHARS = 60000
TABLE_GAP_AFTER = 28.0


class PdfGenerationError(Exception):
    """Raised when a generated PDF cannot be created or ingested."""


@dataclass
class GeneratedPdfArtifact:
    artifact_id: str
    title: str
    filename: str
    file_path: str
    file_mime: str
    file_size: int
    download_url: str
    web_download_url: str
    mobile_download_url: str
    owner_email: str | None
    created_at: str


def create_generated_pdf(
    *,
    title: str,
    body_markdown: str,
    filename: str | None = None,
    user_email: str | None = None,
) -> GeneratedPdfArtifact:
    clean_title = _clean_title(title)
    clean_body = _clean_body(body_markdown)
    artifact_id = f"pdf:{uuid4().hex}"
    final_filename = _normalize_pdf_filename(filename or clean_title)
    path = _ensure_pdf_storage_dir() / f"{artifact_id.replace(':', '_')}.pdf"

    pdf_bytes = _render_pdf_bytes(
        title=clean_title,
        body_markdown=clean_body,
    )
    path.write_bytes(pdf_bytes)

    artifact = GeneratedPdfArtifact(
        artifact_id=artifact_id,
        title=clean_title,
        filename=final_filename,
        file_path=str(path),
        file_mime=PDF_MIME_TYPE,
        file_size=path.stat().st_size,
        download_url=f"/generated-pdfs/{artifact_id}/download",
        web_download_url=f"/api/orchestrator/generated-pdfs/{artifact_id}/download",
        mobile_download_url=f"/mobile/generated-pdfs/{artifact_id}/download",
        owner_email=_normalize_email(user_email),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    _metadata_path(path).write_text(json.dumps(asdict(artifact), indent=2), encoding="utf-8")
    return artifact


def get_generated_pdf_file(
    artifact_id: str,
    *,
    user_email: str | None,
) -> dict[str, Any] | None:
    artifact = _load_artifact(artifact_id)
    if artifact is None:
        return None
    owner = artifact.owner_email
    requester = _normalize_email(user_email) or ""
    if owner and owner != requester:
        return None
    if not Path(artifact.file_path).exists():
        return None
    return asdict(artifact)


def ingest_generated_pdf(
    *,
    artifact_id: str,
    title: str | None = None,
    tags: Sequence[str] | None = None,
    contact_ids: Sequence[str] | None = None,
    description: str | None = None,
    user_email: str | None = None,
) -> dict[str, Any]:
    artifact = _load_artifact(artifact_id)
    if artifact is None:
        raise PdfGenerationError("Generated PDF artifact not found")

    requester = _normalize_email(user_email) or ""
    if artifact.owner_email and artifact.owner_email != requester:
        raise PdfGenerationError("Generated PDF artifact not found")

    source_path = Path(artifact.file_path)
    if not source_path.exists():
        raise PdfGenerationError("Generated PDF file is unavailable")

    upload = UploadFile(
        file=BytesIO(source_path.read_bytes()),
        filename=artifact.filename,
        headers=Headers({"content-type": PDF_MIME_TYPE}),
    )

    import documents

    return documents.ingest_document(
        title=title or artifact.title,
        tags=list(tags or []),
        contact_ids=list(contact_ids or []),
        description=description or f"Generated PDF: {artifact.title}",
        upload=upload,
        document_date=None,
        user_email=requester or None,
    )


def _ensure_pdf_storage_dir() -> Path:
    PDF_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    return PDF_STORAGE_DIR


def _metadata_path(path: Path) -> Path:
    return path.with_suffix(".json")


def _load_artifact(artifact_id: str) -> GeneratedPdfArtifact | None:
    normalized_id = (artifact_id or "").strip()
    if not re.fullmatch(r"pdf:[a-f0-9]{32}", normalized_id):
        return None
    path = _ensure_pdf_storage_dir() / f"{normalized_id.replace(':', '_')}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return GeneratedPdfArtifact(**data)
    except Exception:
        logger.exception("[generated_pdfs] Failed to load artifact metadata id=%s", normalized_id)
        return None


def _clean_title(title: str) -> str:
    clean = " ".join(str(title or "").split())
    if not clean:
        raise PdfGenerationError("title is required")
    return clean[:160]


def _normalize_email(value: str | None) -> str | None:
    normalized = (value or "").strip().lower()
    return normalized or None


def _clean_body(body_markdown: str) -> str:
    body = str(body_markdown or "").strip()
    if not body:
        raise PdfGenerationError("body_markdown is required")
    if len(body) > MAX_BODY_CHARS:
        raise PdfGenerationError(f"body_markdown exceeds {MAX_BODY_CHARS} characters")
    return body


def _normalize_pdf_filename(filename: str) -> str:
    guessed = Path(filename).stem if filename else "generated-pdf"
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", guessed).strip(".-_")
    if not safe_stem:
        safe_stem = "generated-pdf"
    return f"{safe_stem[:80]}.pdf"


def _render_pdf_bytes(
    *,
    title: str,
    body_markdown: str,
) -> bytes:
    try:
        import pymupdf  # type: ignore
    except ImportError:
        try:
            import fitz as pymupdf  # type: ignore
        except ImportError as exc:
            raise PdfGenerationError("PyMuPDF is required to generate PDFs") from exc

    renderer = _PdfRenderer(pymupdf)
    renderer.heading(title, level=1)
    renderer.markdown(body_markdown)
    return renderer.bytes()


class _PdfRenderer:
    def __init__(self, pymupdf_module: Any):
        self.pymupdf = pymupdf_module
        self.doc = pymupdf_module.open()
        self.page = None
        self.y = MARGIN_Y
        self._new_page()

    def bytes(self) -> bytes:
        return self.doc.tobytes(garbage=4, deflate=True)

    def heading(self, text: str, *, level: int) -> None:
        size = 20 if level == 1 else 15 if level == 2 else 12
        self._write_lines(
            self._wrap(text, size, self.content_width),
            font_size=size,
            spacing_after=10 if level == 1 else 7,
        )

    def markdown(self, markdown: str) -> None:
        blocks = _parse_markdown_blocks(markdown)
        for block in blocks:
            kind = block["kind"]
            if kind == "heading":
                self.heading(block["text"], level=block["level"])
            elif kind == "table":
                self._markdown_table(block["rows"])
            elif kind == "list":
                for item in block["items"]:
                    self.paragraph(f"- {item}", indent=12)
                self.y += 4
            else:
                self.paragraph(block["text"])

    def paragraph(self, text: str, *, indent: float = 0.0) -> None:
        lines = self._wrap(text, 10.5, self.content_width - indent)
        self._write_lines(lines, font_size=10.5, x_offset=indent, spacing_after=8)

    @property
    def content_width(self) -> float:
        return PAGE_WIDTH - (MARGIN_X * 2)

    def _new_page(self) -> None:
        self.page = self.doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        self.y = MARGIN_Y

    def _ensure_space(self, height: float) -> None:
        if self.y + height <= PAGE_HEIGHT - MARGIN_Y:
            return
        self._new_page()

    def _write_lines(
        self,
        lines: Sequence[str],
        *,
        font_size: float,
        x_offset: float = 0.0,
        spacing_after: float = 0.0,
    ) -> None:
        line_height = font_size * LINE_HEIGHT_FACTOR
        self._ensure_space(max(line_height, len(lines) * line_height) + spacing_after)
        font_name = "helv"
        for line in lines:
            self._ensure_space(line_height + spacing_after)
            self.page.insert_text(
                (MARGIN_X + x_offset, self.y),
                line,
                fontsize=font_size,
                fontname=font_name,
                color=(0.08, 0.08, 0.08),
            )
            self.y += line_height
        self.y += spacing_after

    def _markdown_table(self, rows: Sequence[Sequence[str]]) -> None:
        self._draw_table(rows)
        self.y += TABLE_GAP_AFTER

    def _draw_table(self, rows: Sequence[Sequence[str]]) -> None:
        if not rows:
            return
        column_count = max(len(row) for row in rows)
        column_width = self.content_width / max(1, column_count)
        font_size = 8.5 if column_count <= 5 else 7.5
        padding = 4.0
        line_height = font_size * LINE_HEIGHT_FACTOR

        for row_index, row in enumerate(rows):
            wrapped_cells = [
                self._wrap(str(row[col]) if col < len(row) else "", font_size, column_width - padding * 2)
                for col in range(column_count)
            ]
            row_height = max(
                line_height + padding * 2,
                max(len(cell) for cell in wrapped_cells) * line_height + padding * 2 + 2,
            )
            self._ensure_space(row_height)
            x = MARGIN_X
            fill = (0.92, 0.94, 0.96) if row_index == 0 else None
            for cell_lines in wrapped_cells:
                rect = self.pymupdf.Rect(x, self.y, x + column_width, self.y + row_height)
                self.page.draw_rect(rect, color=(0.55, 0.58, 0.62), fill=fill, width=0.5)
                text_y = self.y + padding + font_size
                for line in cell_lines:
                    self.page.insert_text(
                        (x + padding, text_y),
                        line,
                        fontsize=font_size,
                        fontname="helv",
                        color=(0.08, 0.08, 0.08),
                    )
                    text_y += line_height
                x += column_width
            self.y += row_height

    def _wrap(self, text: str, font_size: float, max_width: float) -> list[str]:
        words = str(text or "").split()
        if not words:
            return [""]
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if self._text_width(candidate, font_size) <= max_width:
                current = candidate
                continue
            if current:
                lines.append(current)
            if self._text_width(word, font_size) <= max_width:
                current = word
            else:
                chunks = self._break_long_word(word, font_size, max_width)
                lines.extend(chunks[:-1])
                current = chunks[-1] if chunks else ""
        if current:
            lines.append(current)
        return lines or [""]

    def _text_width(self, text: str, font_size: float) -> float:
        try:
            return float(self.pymupdf.get_text_length(text, fontname="helv", fontsize=font_size))
        except Exception:
            return len(text) * font_size * 0.55

    def _break_long_word(self, word: str, font_size: float, max_width: float) -> list[str]:
        chunks: list[str] = []
        current = ""
        for char in word:
            candidate = f"{current}{char}"
            if current and self._text_width(candidate, font_size) > max_width:
                chunks.append(current)
                current = char
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks


def _parse_markdown_blocks(markdown: str) -> list[dict[str, Any]]:
    lines = markdown.replace("\r\n", "\n").split("\n")
    blocks: list[dict[str, Any]] = []
    paragraph_lines: list[str] = []
    index = 0

    def flush_paragraph() -> None:
        if paragraph_lines:
            blocks.append({"kind": "paragraph", "text": " ".join(paragraph_lines).strip()})
            paragraph_lines.clear()

    while index < len(lines):
        line = lines[index].strip()
        if not line:
            flush_paragraph()
            index += 1
            continue
        if line.startswith("#"):
            flush_paragraph()
            level = min(3, len(line) - len(line.lstrip("#")))
            blocks.append({"kind": "heading", "level": level, "text": line[level:].strip()})
            index += 1
            continue
        if _is_table_start(lines, index):
            flush_paragraph()
            table_rows, index = _consume_markdown_table(lines, index)
            blocks.append({"kind": "table", "rows": table_rows})
            continue
        if line.startswith(("- ", "* ")):
            flush_paragraph()
            items: list[str] = []
            while index < len(lines):
                item_line = lines[index].strip()
                if not item_line.startswith(("- ", "* ")):
                    break
                items.append(item_line[2:].strip())
                index += 1
            blocks.append({"kind": "list", "items": items})
            continue
        paragraph_lines.append(line)
        index += 1

    flush_paragraph()
    return blocks


def _is_table_start(lines: Sequence[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    current = lines[index].strip()
    separator = lines[index + 1].strip()
    return "|" in current and re.fullmatch(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?", separator) is not None


def _consume_markdown_table(lines: Sequence[str], index: int) -> tuple[list[list[str]], int]:
    header = _split_table_row(lines[index])
    index += 2
    rows = [header]
    while index < len(lines):
        line = lines[index].strip()
        if "|" not in line:
            break
        rows.append(_split_table_row(line))
        index += 1
    return rows, index


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip().strip("|")
    return [" ".join(cell.strip().split()) for cell in stripped.split("|")]
