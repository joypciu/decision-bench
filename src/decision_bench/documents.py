from __future__ import annotations

import io
import shutil
from dataclasses import dataclass
from pathlib import Path

MAX_BYTES = 8_000_000
TEXT_SUFFIXES = {".md", ".txt", ".html", ".htm", ".csv", ".json", ".xml"}
PDF_SUFFIXES = {".pdf"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
OFFICE_SUFFIXES = {".docx", ".pptx", ".xlsx", ".xls"}
SUPPORTED = TEXT_SUFFIXES | PDF_SUFFIXES | IMAGE_SUFFIXES | OFFICE_SUFFIXES


@dataclass
class DocumentResult:
    filename: str
    kind: str
    markdown: str


def extract_document(data: bytes, filename: str, crop: tuple[int, int, int, int] | None = None, page: int = 1) -> DocumentResult:
    if not data:
        raise ValueError("The file is empty.")
    if len(data) > MAX_BYTES:
        raise ValueError("Files are limited to 8 MB.")
    suffix = Path(filename or "upload.txt").suffix.lower()
    if suffix not in SUPPORTED:
        raise ValueError("Supported files: md, txt, html, csv, json, xml, pdf, images, docx, pptx, xlsx.")
    if suffix in {".md", ".txt"}:
        text = data.decode("utf-8", errors="replace").strip()
    elif suffix in IMAGE_SUFFIXES:
        if crop is not None:
            data = crop_image(data, crop)
        text = ocr_image(data)
    elif suffix in PDF_SUFFIXES and crop is not None:
        text = ocr_image(crop_pdf(data, page=page, box=crop))
    elif suffix in PDF_SUFFIXES:
        text = _markitdown(data, suffix)
    else:
        text = _markitdown(data, suffix)
    if not text:
        raise ValueError("No text could be read from that file.")
    return DocumentResult(filename=Path(filename).name, kind=suffix.lstrip("."), markdown=text[:50_000])


def crop_pdf(data: bytes, page: int, box: tuple[int, int, int, int]) -> bytes:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(data)
    if page < 1 or page > len(document):
        raise ValueError("That PDF page does not exist.")
    scale = 2
    rendered = document[page - 1].render(scale=scale).to_pil()
    left, top, right, bottom = (int(value * scale) for value in box)
    if left < 0 or top < 0 or right > rendered.width or bottom > rendered.height or left >= right or top >= bottom:
        raise ValueError("Crop box is outside the PDF page.")
    cropped = rendered.crop((left, top, right, bottom))
    buffer = io.BytesIO()
    cropped.save(buffer, format="PNG")
    return buffer.getvalue()


def crop_image(data: bytes, box: tuple[int, int, int, int]) -> bytes:
    from PIL import Image

    image = Image.open(io.BytesIO(data))
    left, top, right, bottom = box
    if left < 0 or top < 0 or right > image.width or bottom > image.height or left >= right or top >= bottom:
        raise ValueError("Crop box is outside the image.")
    cropped = image.crop((left, top, right, bottom))
    buffer = io.BytesIO()
    cropped.save(buffer, format="PNG")
    return buffer.getvalue()


def ocr_image(data: bytes) -> str:
    from PIL import Image

    image = Image.open(io.BytesIO(data))
    text = _recognize(image).strip()
    if not text:
        raise ValueError("OCR found no text in that image.")
    return text


def _recognize(image) -> str:
    text = _windows_ocr(image)
    if text:
        return text
    if shutil.which("tesseract"):
        import pytesseract

        return pytesseract.image_to_string(image)
    raise ValueError("No OCR engine is available. On Windows, install the winocr package.")


def _windows_ocr(image) -> str:
    try:
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        import winocr
    except Exception:
        return ""

    def run() -> str:
        result = asyncio.run(winocr.recognize_pil(image.convert("RGB"), "en"))
        return (getattr(result, "text", "") or "").strip()

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(run).result()


def _markitdown(data: bytes, suffix: str) -> str:
    from markitdown import MarkItDown

    result = MarkItDown().convert_stream(io.BytesIO(data), file_extension=suffix)
    return (getattr(result, "markdown", None) or getattr(result, "text_content", "") or "").strip()
