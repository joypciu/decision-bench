from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

MAX_BYTES = 8_000_000
TEXT_SUFFIXES = {".md", ".txt", ".html", ".htm", ".csv", ".json", ".xml"}
PDF_SUFFIXES = {".pdf"}
SUPPORTED = TEXT_SUFFIXES | PDF_SUFFIXES


@dataclass
class DocumentResult:
    filename: str
    kind: str
    markdown: str


def extract_document(data: bytes, filename: str) -> DocumentResult:
    if not data:
        raise ValueError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise ValueError("Files are limited to 8 MB.")
    suffix = Path(filename or "upload.txt").suffix.lower()
    if suffix not in SUPPORTED:
        raise ValueError("Supported files: md, txt, html, csv, json, xml, pdf.")
    if suffix in {".md", ".txt"}:
        text = data.decode("utf-8", errors="replace").strip()
    else:
        text = _markitdown(data, suffix)
    if not text:
        raise ValueError("No text could be read from that file.")
    return DocumentResult(filename=Path(filename).name, kind=suffix.lstrip("."), markdown=text[:50_000])


def _markitdown(data: bytes, suffix: str) -> str:
    from markitdown import MarkItDown

    result = MarkItDown().convert_stream(io.BytesIO(data), file_extension=suffix)
    return (getattr(result, "markdown", None) or getattr(result, "text_content", "") or "").strip()
