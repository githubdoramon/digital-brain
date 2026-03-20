from __future__ import annotations

import inspect
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import cast

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
MARKDOWN_EXTRACTION_KWARGS: dict[str, object] = {
    "page_chunks": True,
    "header": False,
    "footer": False,
    "use_ocr": True,
    "force_ocr": False,
    "table_strategy": "lines_strict",
}
SUPPORTED_IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".gif",
    ".tiff",
    ".tif",
    ".webp",
}


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
    if _is_image_document(suffix=suffix, mime_type=mime_type):
        return _parse_image(path)
    return _parse_binary_fallback(path)


def _is_image_document(*, suffix: str, mime_type: str | None) -> bool:
    if mime_type and mime_type.startswith("image/"):
        return True
    return suffix in SUPPORTED_IMAGE_EXTENSIONS


def _parse_image(path: Path) -> DocumentParseResult:
    ocr_text = _extract_text_from_image_with_tesseract(path)
    if ocr_text is None:
        return DocumentParseResult(
            text="",
            raw_text="",
            parser_used="image_no_text",
            warnings=["image_ocr_unavailable_or_failed"],
            metadata={"ocr_engine": "tesseract", "ocr_applied": False},
        )

    text = ocr_text.strip()
    warnings: list[str] = []
    if not text:
        warnings.append("image_ocr_no_text")
    return DocumentParseResult(
        text=text,
        raw_text=text,
        parser_used="image_ocr_tesseract",
        warnings=warnings,
        sections=_derive_sections_from_text(text),
        metadata={"ocr_engine": "tesseract", "ocr_applied": True},
    )


def _extract_text_from_image_with_tesseract(path: Path) -> str | None:
    tesseract_binary = shutil.which("tesseract")
    if not tesseract_binary:
        return None

    command = [tesseract_binary, str(path), "stdout"]
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.CalledProcessError as exc:
        logger.warning(
            "[documents] image OCR failed for %s: %s",
            path,
            (exc.stderr or str(exc)).strip(),
        )
        return None
    except Exception as exc:
        logger.warning("[documents] image OCR failed for %s: %s", path, exc, exc_info=exc)
        return None

    return result.stdout or ""


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
    layout_enabled = _try_enable_pymupdf_layout()
    markdown_result = _parse_pdf_pymupdf4llm(path, layout_enabled=layout_enabled)
    if markdown_result is not None and markdown_result.text.strip():
        return markdown_result

    if markdown_result is not None:
        logger.info(
            "[documents] pymupdf4llm produced empty text for %s; trying PyMuPDF markdown",
            path,
        )
    pymupdf_markdown_result = _parse_pdf_pymupdf_markdown(path, layout_enabled=layout_enabled)
    if pymupdf_markdown_result is not None and pymupdf_markdown_result.text.strip():
        if markdown_result is not None:
            pymupdf_markdown_result.warnings.extend(markdown_result.warnings)
            pymupdf_markdown_result.warnings.append(
                "pymupdf4llm_empty_fallback_to_pymupdf_markdown"
            )
        return pymupdf_markdown_result

    if pymupdf_markdown_result is not None:
        logger.info(
            "[documents] pymupdf markdown produced empty text for %s; falling back to PyMuPDF blocks",
            path,
        )

    block_result = _parse_pdf_pymupdf_blocks(path)
    if markdown_result is not None:
        block_result.warnings.extend(markdown_result.warnings)
    if pymupdf_markdown_result is not None:
        block_result.warnings.extend(pymupdf_markdown_result.warnings)
    if markdown_result is not None or pymupdf_markdown_result is not None:
        block_result.warnings.append("markdown_fallback_to_blocks")
    return block_result


def _parse_pdf_pymupdf_markdown(
    path: Path,
    *,
    layout_enabled: bool,
) -> DocumentParseResult | None:
    try:
        import pymupdf  # type: ignore
    except Exception:
        logger.info("[documents] PyMuPDF not available; skipping PyMuPDF markdown parser")
        return None

    to_markdown = getattr(pymupdf, "to_markdown", None)
    if not callable(to_markdown):
        logger.info("[documents] pymupdf.to_markdown not available; skipping PyMuPDF markdown")
        return None

    return _parse_pdf_with_markdown_extractor(
        path,
        extractor=to_markdown,
        parser_name="pdf_layout_pymupdf_markdown",
        empty_warning="pymupdf_markdown_no_text",
        layout_enabled=layout_enabled,
    )


def _parse_pdf_pymupdf4llm(
    path: Path,
    *,
    layout_enabled: bool,
) -> DocumentParseResult | None:
    try:
        import pymupdf4llm  # type: ignore
    except Exception:
        logger.info("[documents] pymupdf4llm not available; falling back to PyMuPDF blocks")
        return None

    return _parse_pdf_with_markdown_extractor(
        path,
        extractor=pymupdf4llm.to_markdown,
        parser_name="pdf_layout_pymupdf4llm",
        empty_warning="pymupdf4llm_no_text",
        layout_enabled=layout_enabled,
    )


def _parse_pdf_with_markdown_extractor(
    path: Path,
    *,
    extractor: object,
    parser_name: str,
    empty_warning: str,
    layout_enabled: bool,
) -> DocumentParseResult | None:
    if not callable(extractor):
        return None

    try:
        import pymupdf  # type: ignore
    except Exception as exc:
        logger.warning(
            "[documents] PyMuPDF not available for markdown parsing %s: %s",
            path,
            exc,
            exc_info=exc,
        )
        return None

    try:
        doc = pymupdf.open(str(path))
    except Exception as exc:
        logger.warning("[documents] pymupdf open failed for %s: %s", path, exc, exc_info=exc)
        return None

    markdown_kwargs: dict[str, object] = {}
    try:
        markdown_kwargs = _supported_markdown_kwargs(extractor)
        markdown_output = extractor(doc, **markdown_kwargs)
    except ValueError as exc:
        if not _is_empty_table_sequence_error(exc):
            logger.warning(
                "[documents] markdown parse failed parser=%s path=%s error=%s",
                parser_name,
                path,
                exc,
                exc_info=exc,
            )
            return None
        try:
            markdown_output, markdown_kwargs = _retry_markdown_without_tables(
                extractor,
                doc,
                base_kwargs=markdown_kwargs,
                parser_name=parser_name,
                path=path,
            )
        except Exception as retry_exc:
            logger.warning(
                "[documents] markdown table parse retry failed parser=%s path=%s error=%s; falling back",
                parser_name,
                path,
                retry_exc,
            )
            return None
    except Exception as exc:
        logger.warning(
            "[documents] markdown parse failed parser=%s path=%s error=%s",
            parser_name,
            path,
            exc,
            exc_info=exc,
        )
        return None
    finally:
        try:
            doc.close()
        except Exception:
            pass

    pages = _extract_markdown_pages(markdown_output)
    raw_text = "\n\n".join(page for page in pages if page).strip()
    if not raw_text and isinstance(markdown_output, str):
        raw_text = markdown_output.strip()

    warnings: list[str] = []
    if not raw_text:
        warnings.append(empty_warning)
    return DocumentParseResult(
        text=raw_text,
        raw_text=raw_text,
        parser_used=parser_name,
        warnings=warnings,
        pages=pages,
        sections=_derive_sections_from_markdown(raw_text),
        metadata={
            "page_count": len(pages),
            "layout_enabled": layout_enabled,
            "markdown_kwargs": markdown_kwargs,
            "ocr_requested": bool(markdown_kwargs.get("use_ocr")),
            "ocr_forced": bool(markdown_kwargs.get("force_ocr")),
        },
    )


def _supported_markdown_kwargs(extractor: object) -> dict[str, object]:
    if not callable(extractor):
        return {}

    try:
        parameters = inspect.signature(extractor).parameters
    except (TypeError, ValueError):
        return {"page_chunks": True}

    supported: dict[str, object] = {}
    accepts_var_kwargs = any(
        param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values()
    )
    for key, value in MARKDOWN_EXTRACTION_KWARGS.items():
        if accepts_var_kwargs or key in parameters:
            supported[key] = value
    return supported


def _is_empty_table_sequence_error(exc: ValueError) -> bool:
    message = str(exc).lower()
    return "empty sequence" in message and (
        "min() arg" in message or "max() arg" in message or "bbox" in message or "table" in message
    )


def _retry_markdown_without_tables(
    extractor: object,
    doc: object,
    *,
    base_kwargs: dict[str, object],
    parser_name: str,
    path: Path,
) -> tuple[object, dict[str, object]]:
    if not callable(extractor):
        raise TypeError("extractor is not callable")

    retry_kwargs = dict(base_kwargs)
    if "table_strategy" in retry_kwargs:
        retry_kwargs["table_strategy"] = None
    elif "ignore_graphics" in retry_kwargs:
        retry_kwargs["ignore_graphics"] = True
    else:
        raise ValueError("table parser failed and no safe retry knobs available")

    try:
        output = cast(Callable[..., object], extractor)(doc, **retry_kwargs)
    except Exception as exc:
        logger.info(
            "[documents] markdown table-disabled retry failed parser=%s path=%s error=%s",
            parser_name,
            path,
            exc,
        )
        raise

    logger.info(
        "[documents] markdown parser recovered with table-disabled retry parser=%s path=%s",
        parser_name,
        path,
    )
    return output, retry_kwargs


def _try_enable_pymupdf_layout() -> bool:
    try:
        import_module("pymupdf.layout")
        return True
    except Exception:
        logger.info("[documents] pymupdf.layout not available; continuing without layout module")
        return False


def _parse_pdf_pymupdf_blocks(path: Path) -> DocumentParseResult:
    try:
        import pymupdf  # type: ignore
    except Exception:
        logger.info("[documents] PyMuPDF not available; using pdfminer for PDF parsing")
        return _parse_pdf_pdfminer(path, parser_name="pdfminer")

    pages: list[str] = []
    warnings: list[str] = []
    try:
        doc = pymupdf.open(str(path))
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
        parser_used="pdf_layout_pymupdf_blocks",
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
        if _looks_like_text_binary(data):
            text = data.decode("utf-8", errors="ignore")
        else:
            text = ""
            warnings.append("binary_non_text_file")
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


def _looks_like_text_binary(data: bytes) -> bool:
    if not data:
        return True
    if b"\x00" in data:
        return False

    sample = data[:4096]
    try:
        decoded = sample.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return False

    control_count = 0
    for char in decoded:
        if ord(char) < 32 and char not in {"\n", "\r", "\t"}:
            control_count += 1
    return (control_count / max(1, len(decoded))) < 0.02


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


def _derive_sections_from_markdown(text: str) -> list[ParsedSection]:
    lines = [line.rstrip() for line in text.splitlines()]
    sections: list[ParsedSection] = []
    current_title: str | None = None
    current_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current_lines:
                current_lines.append("")
            continue
        if stripped.startswith("#"):
            _flush_section(sections, current_title, current_lines)
            current_title = stripped.lstrip("#").strip() or None
            current_lines = []
            continue
        current_lines.append(stripped)

    _flush_section(sections, current_title, current_lines)
    if sections:
        return sections
    return _derive_sections_from_text(text)


def _extract_markdown_pages(markdown_output: object) -> list[str]:
    if isinstance(markdown_output, str):
        cleaned = markdown_output.strip()
        return [cleaned] if cleaned else []
    if isinstance(markdown_output, list):
        pages: list[str] = []
        for item in markdown_output:
            if isinstance(item, dict):
                candidates = [item.get("text"), item.get("md"), item.get("markdown")]
                page_text = next(
                    (
                        value.strip()
                        for value in candidates
                        if isinstance(value, str) and value.strip()
                    ),
                    "",
                )
                if page_text:
                    pages.append(page_text)
                continue
            if isinstance(item, str) and item.strip():
                pages.append(item.strip())
        return pages
    return []


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
