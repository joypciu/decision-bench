from fastapi.testclient import TestClient


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
