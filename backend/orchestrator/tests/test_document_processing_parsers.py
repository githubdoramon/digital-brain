from __future__ import annotations

from pathlib import Path

from document_processing.normalization import normalize_document
from document_processing.parsers import parse_document
from document_processing.types import DocumentParseResult


def test_image_upload_without_ocr_does_not_binary_decode(monkeypatch, tmp_path: Path):
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"\xff\xd8\xff\xe0" + (b"\x00" * 128))
    monkeypatch.setattr("document_processing.parsers.shutil.which", lambda _name: None)

    result = parse_document(image_path, "image/jpeg")

    assert result.parser_used == "image_no_text"
    assert result.text == ""
    assert "image_ocr_unavailable_or_failed" in result.warnings


def test_binary_fallback_rejects_non_text_payload(tmp_path: Path):
    blob_path = tmp_path / "blob.bin"
    blob_path.write_bytes(b"\x00\x01\x02\xff\xfe\xfd")

    result = parse_document(blob_path, "application/octet-stream")

    assert result.parser_used == "binary_fallback"
    assert result.text == ""
    assert "binary_non_text_file" in result.warnings


def test_normalize_document_strips_nul_from_raw_text():
    parsed = DocumentParseResult(
        text="ok",
        raw_text="hello\x00world",
        parser_used="test_parser",
    )

    normalized = normalize_document(parsed, max_chars=100)

    assert normalized.raw_extracted_text == "helloworld"
