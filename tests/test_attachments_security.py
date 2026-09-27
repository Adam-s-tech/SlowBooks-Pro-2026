"""Path-traversal + validation regression tests for the attachments route.

Covers the fix for CodeQL py/path-injection alert #19.
"""

import io


def _upload(
    client,
    entity_type,
    entity_id,
    filename,
    content=b"hi",
    content_type="application/pdf",
):
    return client.post(
        f"/api/attachments/{entity_type}/{entity_id}",
        files={"file": (filename, io.BytesIO(content), content_type)},
    )


def test_rejects_unknown_entity_type(client, seed_accounts):
    # A URL-safe non-whitelisted type reaches our handler (the /../../../etc
    # variant is normalized by Starlette's router to a 404 before it does).
    r = _upload(client, "sneaky", 1, "x.pdf")
    assert r.status_code == 400
    assert "Invalid entity type" in r.json()["detail"]


def test_rejects_path_traversal_filename(client, seed_accounts):
    from pathlib import Path

    from app.services import storage

    # A checkout whose suite ran before 2.18 still has that era's files in
    # app/static/uploads: what matters is that this upload writes none.
    app_static = Path(__file__).resolve().parents[1] / "app" / "static"
    roots = (storage.files_root(), app_static)

    def on_disk():
        return {
            f: f.stat().st_mtime_ns for root in roots for f in root.rglob("secret.pdf")
        }

    before = on_disk()
    # Path(...).name strips directory prefixes; this verifies the fallback still holds.
    r = _upload(client, "invoice", 1, "../../secret.pdf")
    # Either the filename gets stripped to "secret.pdf" and accepted,
    # or it's rejected. Either way nothing lands on disk at all: the file is
    # kept in the company's database, under the stripped name.
    assert r.status_code in (201, 400)
    if r.status_code == 201:
        assert r.json()["filename"] == "secret.pdf"
    assert on_disk() == before


def test_rejects_disallowed_mime(client, seed_accounts):
    r = _upload(client, "invoice", 1, "evil.html", content_type="text/html")
    assert r.status_code == 400
    assert "not allowed" in r.json()["detail"]


def test_rejects_disallowed_extension(client, seed_accounts):
    r = _upload(client, "invoice", 1, "evil.exe", content_type="application/pdf")
    assert r.status_code == 400
    assert "extension" in r.json()["detail"].lower()


def test_accepts_valid_pdf(client, seed_accounts):
    r = _upload(
        client,
        "invoice",
        42,
        "report.pdf",
        content=b"%PDF-1.4\n",
        content_type="application/pdf",
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["filename"] == "report.pdf"
    # the file is a row of the company's own stored files, not a path in a
    # folder every company shared (2.18.0)
    assert body["file_path"].startswith("stored_files/")
    assert body["from_shared_folder"] is False and body["missing"] is False


def test_filename_special_chars_sanitized(client, seed_accounts):
    # Characters outside the safe set get replaced with _
    r = _upload(client, "invoice", 1, "weird;|$name.pdf")
    assert r.status_code == 201, r.text
    assert ";" not in r.json()["filename"]
    assert "|" not in r.json()["filename"]
