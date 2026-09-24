from fastapi.testclient import TestClient
import io


def test_text_documents_extract_end_to_end(app):
    client = TestClient(app)
    page = client.get("/documents")
    assert page.status_code == 200
    assert "Documents" in page.text

    markdown = client.post(
        "/documents",
        files={"file": ("notes.md", b"# Auth\n\nBypass auth in auth.py\n", "text/markdown")},
    )
    assert markdown.status_code == 200
    assert "Bypass auth" in markdown.text

    plain = client.post(
        "/documents",
        files={"file": ("notes.txt", b"Hello from a text file\n", "text/plain")},
    )
    assert "Hello from a text file" in plain.text

    table = client.post(
        "/documents",
        files={"file": ("rows.csv", b"name,risk\nauth,high\n", "text/csv")},
    )
    assert table.status_code == 200
    assert "auth" in table.text

    payload = client.post(
        "/documents",
        files={"file": ("case.json", b'{"verdict": "block"}', "application/json")},
    )
    assert "block" in payload.text

    rejected = client.post(
        "/documents",
        files={"file": ("bin.exe", b"MZ", "application/octet-stream")},
    )
    assert rejected.status_code == 400


def _pdf_with_text(text: str) -> bytes:
    content = f"BT /F1 18 Tf 40 80 Td ({text}) Tj ET\n".encode("ascii")
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]

    def add(number: int, body: bytes) -> None:
        offsets.append(len(out))
        out.extend(f"{number} 0 obj\n".encode())
        out.extend(body)
        out.extend(b"\nendobj\n")

    add(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    add(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    add(3, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>")
    add(4, f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"endstream")
    add(5, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    xref = len(out)
    out.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode())
    out.extend(f"trailer<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return bytes(out)


def test_pdf_extracts_page_text(app):
    client = TestClient(app)
    response = client.post(
        "/documents",
        files={"file": ("note.pdf", _pdf_with_text("Hello PDF"), "application/pdf")},
    )
    assert response.status_code == 200
    assert "Hello PDF" in response.text


def test_image_ocr_reads_rendered_text(app):
    import os

    pytest = __import__("pytest")
    pytest.importorskip("winocr")
    font_path = r"C:\Windows\Fonts\arial.ttf"
    if not os.path.exists(font_path):
        pytest.skip("Arial is not installed")
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (640, 180), "white")
    draw = ImageDraw.Draw(image)
    draw.text((30, 50), "Hello OCR", fill="black", font=ImageFont.truetype(font_path, 64))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    client = TestClient(app)
    response = client.post(
        "/documents",
        files={"file": ("scan.png", buffer.getvalue(), "image/png")},
    )
    assert response.status_code == 200
    assert "Hello OCR" in response.text
