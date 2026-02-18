from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from docx import Document as DocxDocument
from pdfminer.high_level import extract_text as extract_pdf_text

from observability.logger import get_runtime_logger

from .types import DocumentParseResult, ParsedSection

logger = get_runtime_logger(__name__)

ENABLE_OCR_FALLBACK = os.getenv("DOCUMENT_ENABLE_OCR_FALLBACK", "true").lower() in {
    "1",
    "true",
    "yes",
}
LOW_TEXT_DENSITY_CHARS_PER_PAGE = int(os.getenv("DOCUMENT_LOW_TEXT_DENSITY_CHARS_PER_PAGE", "160"))


def parse_document(path: Path, mime_type: str | None) -> DocumentParseResult:
    suffix = path.suffix.lower()
    if suffix == ".pdf" or mime_type == "application/pdf":
        return _parse_pdf(path)
    if suffix == ".docx" or mime_type in {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }:
        return _parse_docx(path)
    if suffix in {".txt", ".md"} or (mime_type and mime_type.startswith("text/")):
        return _parse_plain_text(path)
    return _parse_binary_fallback(path)


def _parse_pdf(path: Path) -> DocumentParseResult:
    primary = _parse_pdf_layout(path)
    if not _is_low_text_density(primary):
        return primary

    if not ENABLE_OCR_FALLBACK:
        primary.warnings.append("low_text_density_pdf_and_ocr_disabled")
        return primary

    ocr_result = _parse_pdf_with_ocr_fallback(path)
    if ocr_result is None:
        primary.warnings.append("low_text_density_pdf_and_ocr_unavailable")
        return primary
    if len(ocr_result.text.strip()) <= len(primary.text.strip()):
        ocr_result.warnings.append("ocr_result_not_better_than_layout")
        return primary
    return ocr_result


def _parse_pdf_layout(path: Path) -> DocumentParseResult:
    try:
        import fitz  # type: ignore
    except Exception:
        logger.info("[documents] PyMuPDF not available; using pdfminer for PDF parsing")
        return _parse_pdf_pdfminer(path, parser_name="pdfminer")

    pages: list[str] = []
    warnings: list[str] = []
    try:
        doc = fitz.open(str(path))
        for page in doc:
            blocks = page.get_text("blocks")
            blocks_sorted = sorted(
                blocks, key=lambda block: (round(float(block[1]), 1), float(block[0]))
            )
            lines: list[str] = []
            for block in blocks_sorted:
                text = str(block[4]).strip()
                if not text:
                    continue
                lines.extend(part.strip() for part in text.splitlines() if part.strip())
            pages.append("\n".join(lines).strip())
        doc.close()
    except Exception as exc:
        logger.warning("[documents] PyMuPDF parse failed for %s: %s", path, exc, exc_info=exc)
        return _parse_pdf_pdfminer(path, parser_name="pdfminer_fallback_after_pymupdf_error")

    raw_text = "\n\n".join(page for page in pages if page).strip()
    sections = _derive_sections_from_text(raw_text)
    if not raw_text:
        warnings.append("pdf_layout_parser_no_text")
    return DocumentParseResult(
        text=raw_text,
        raw_text=raw_text,
        parser_used="pdf_layout_pymupdf",
        warnings=warnings,
        pages=pages,
        sections=sections,
        metadata={"page_count": len(pages)},
    )


def _parse_pdf_pdfminer(path: Path, *, parser_name: str) -> DocumentParseResult:
    warnings: list[str] = []
    try:
        text = extract_pdf_text(path)
    except Exception as exc:
        logger.warning("[documents] pdfminer parse failed for %s: %s", path, exc, exc_info=exc)
        text = ""
        warnings.append("pdfminer_parse_error")
    pages = _split_pdfminer_pages(text)
    sections = _derive_sections_from_text(text)
    if not text.strip():
        warnings.append("pdfminer_no_text")
    return DocumentParseResult(
        text=text,
        raw_text=text,
        parser_used=parser_name,
        warnings=warnings,
        pages=pages,
        sections=sections,
        metadata={"page_count": len(pages)},
    )


def _parse_pdf_with_ocr_fallback(path: Path) -> DocumentParseResult | None:
    ocr_binary = shutil.which("ocrmypdf")
    if not ocr_binary:
        return None

    with tempfile.TemporaryDirectory(prefix="doc_ocr_") as temp_dir:
        output_path = Path(temp_dir) / "ocr_output.pdf"
        command = [
            ocr_binary,
            "--skip-text",
            "--force-ocr",
            "--output-type",
            "pdf",
            str(path),
            str(output_path),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True)
        except Exception as exc:
            logger.warning("[documents] OCR fallback failed for %s: %s", path, exc, exc_info=exc)
            return None

        parsed = _parse_pdf_layout(output_path)
        parsed.parser_used = "pdf_ocr_fallback"
        parsed.ocr_applied = True
        if not parsed.warnings:
            parsed.warnings = []
        parsed.warnings.append("ocr_applied")
        return parsed


def _parse_docx(path: Path) -> DocumentParseResult:
    sections: list[ParsedSection] = []
    warnings: list[str] = []
    lines: list[str] = []
    current_heading: str | None = None
    current_lines: list[str] = []
    try:
        doc = DocxDocument(str(path))
        for paragraph in doc.paragraphs:
            text = (paragraph.text or "").strip()
            if not text:
                continue
            style_name = (paragraph.style.name or "").lower() if paragraph.style else ""
            is_heading = style_name.startswith("heading") or _looks_like_heading(text)
            if is_heading:
                _flush_section(sections, current_heading, current_lines)
                current_heading = text
                current_lines = []
                lines.append(text)
                continue
            lines.append(text)
            current_lines.append(text)
        _flush_section(sections, current_heading, current_lines)
    except Exception as exc:
        logger.warning("[documents] DOCX parse failed for %s: %s", path, exc, exc_info=exc)
        warnings.append("docx_parse_error")
        return _parse_binary_fallback(path)

    full_text = "\n".join(lines).strip()
    if not full_text:
        warnings.append("docx_no_text")
    return DocumentParseResult(
        text=full_text,
        raw_text=full_text,
        parser_used="docx_parser",
        warnings=warnings,
        sections=sections,
    )


def _parse_plain_text(path: Path) -> DocumentParseResult:
    warnings: list[str] = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as handle:
            text = handle.read()
    except Exception as exc:
        logger.warning("[documents] plain text parse failed for %s: %s", path, exc, exc_info=exc)
        text = ""
        warnings.append("plain_text_parse_error")
    sections = _derive_sections_from_text(text)
    return DocumentParseResult(
        text=text,
        raw_text=text,
        parser_used="plain_text",
        warnings=warnings,
        sections=sections,
    )


def _parse_binary_fallback(path: Path) -> DocumentParseResult:
    warnings: list[str] = []
    try:
        with open(path, "rb") as handle:
            data = handle.read()
        text = data.decode("utf-8", errors="ignore")
    except Exception as exc:
        logger.warning(
            "[documents] binary fallback parse failed for %s: %s", path, exc, exc_info=exc
        )
        text = ""
        warnings.append("binary_decode_error")
    return DocumentParseResult(
        text=text,
        raw_text=text,
        parser_used="binary_fallback",
        warnings=warnings,
    )


def _split_pdfminer_pages(text: str) -> list[str]:
    if not text:
        return []
    pages = [page.strip() for page in text.split("\x0c")]
    return [page for page in pages if page]


def _derive_sections_from_text(text: str) -> list[ParsedSection]:
    lines = [line.strip() for line in text.splitlines()]
    sections: list[ParsedSection] = []
    current_title: str | None = None
    current_lines: list[str] = []

    for line in lines:
        if not line:
            continue
        if _looks_like_heading(line):
            _flush_section(sections, current_title, current_lines)
            current_title = line
            current_lines = []
            continue
        current_lines.append(line)

    _flush_section(sections, current_title, current_lines)

    if sections:
        return sections
    cleaned_text = "\n".join(line for line in lines if line).strip()
    if not cleaned_text:
        return []
    return [ParsedSection(title=None, text=cleaned_text)]


def _looks_like_heading(text: str) -> bool:
    cleaned = text.strip()
    if not cleaned:
        return False
    if len(cleaned) > 80:
        return False
    if re.fullmatch(r"[A-Z0-9\s\-:/()]{3,}", cleaned):
        return True
    if cleaned.endswith(":") and len(cleaned.split()) <= 10:
        return True
    numbered = re.match(r"^(\d+(?:\.\d+){0,3})\s+[A-Z][^\n]{1,70}$", cleaned)
    if numbered:
        return True
    return False


def _flush_section(
    sections: list[ParsedSection],
    title: str | None,
    section_lines: list[str],
) -> None:
    if not section_lines:
        return
    section_text = "\n".join(section_lines).strip()
    if not section_text:
        return
    sections.append(
        ParsedSection(
            title=title,
            text=section_text,
        )
    )
    section_lines.clear()


def _is_low_text_density(result: DocumentParseResult) -> bool:
    pages = [page for page in result.pages if page.strip()]
    if not pages:
        return len(result.text.strip()) < LOW_TEXT_DENSITY_CHARS_PER_PAGE
    average_chars = len(result.text.strip()) / max(1, len(pages))
    return average_chars < LOW_TEXT_DENSITY_CHARS_PER_PAGE
