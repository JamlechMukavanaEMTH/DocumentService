"""Tests for document CRUD, upload, download, versions, delete."""

import asyncio
import io
import uuid

from httpx import ASGITransport, AsyncClient

from app.api.deps import AsyncSessionLocal, engine
from tests.conftest import cleanup_app, make_app_with_session, truncate_tables


def _run_with_client(test_fn):
    """Helper: create session+app+client, run test_fn (sync), cleanup."""

    async def _inner():
        await engine.dispose()
        async with AsyncSessionLocal() as session:
            app = make_app_with_session(session)
            transport = ASGITransport(app=app)
            ac = AsyncClient(transport=transport, base_url="http://testserver")
            await ac.__aenter__()
            try:
                await test_fn(ac)
            finally:
                await ac.__aexit__(None, None, None)
                cleanup_app(app)
                await truncate_tables(session)
        await engine.dispose()

    asyncio.run(_inner())


def test_upload_document(prepare_database):
    """Upload a single document returns 201 and metadata."""

    async def test(ac):
        files = {"file": ("hello.txt", io.BytesIO(b"hello world"), "text/plain")}
        resp = await ac.post("/api/v1/documents/upload", files=files)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["filename"] == "hello.txt"
        assert data["mime_type"] == "text/plain"
        assert data["size_bytes"] == 11
        assert data["status"] == "ocr_pending"
        assert "id" in data
        assert "sha256" in data

    _run_with_client(test)


def test_upload_exceeds_size_limit(prepare_database, monkeypatch):
    """Upload larger than MAX_FILE_SIZE_BYTES returns 413."""
    from app.core import config
    monkeypatch.setattr(config.settings, "MAX_FILE_SIZE_BYTES", 5)

    async def test(ac):
        files = {"file": ("big.txt", io.BytesIO(b"this is too long"), "text/plain")}
        resp = await ac.post("/api/v1/documents/upload", files=files)
        assert resp.status_code == 413, resp.text

    _run_with_client(test)


def test_upload_duplicate_is_deduplicated(prepare_database):
    """Uploading identical content twice returns the same document (dedup)."""

    async def test(ac):
        files = {"file": ("dup.txt", io.BytesIO(b"duplicate content"), "text/plain")}
        r1 = await ac.post("/api/v1/documents/upload", files=files)
        assert r1.status_code == 201
        id1 = r1.json()["id"]

        r2 = await ac.post("/api/v1/documents/upload", files=files)
        assert r2.status_code == 201
        id2 = r2.json()["id"]
        assert id1 == id2

    _run_with_client(test)


def test_list_documents(prepare_database):
    """List endpoint returns paginated results."""

    async def test(ac):
        await ac.post("/api/v1/documents/upload", files={"file": ("a.txt", io.BytesIO(b"aaa"), "text/plain")})
        await ac.post("/api/v1/documents/upload", files={"file": ("b.txt", io.BytesIO(b"bbb"), "text/plain")})

        resp = await ac.get("/api/v1/documents/")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    _run_with_client(test)


def test_get_document(prepare_database):
    """Get a single document by ID."""

    async def test(ac):
        upload = await ac.post(
            "/api/v1/documents/upload",
            files={"file": ("getme.txt", io.BytesIO(b"content"), "text/plain")},
        )
        doc_id = upload.json()["id"]

        resp = await ac.get(f"/api/v1/documents/{doc_id}")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["id"] == doc_id
        assert data["filename"] == "getme.txt"

    _run_with_client(test)


def test_get_nonexistent_document(prepare_database):
    """Get a document that doesn't exist returns 404."""

    async def test(ac):
        resp = await ac.get(f"/api/v1/documents/{uuid.uuid4()}")
        assert resp.status_code == 404, resp.text

    _run_with_client(test)


def test_download_document(prepare_database):
    """Download returns the stored file content."""

    async def test(ac):
        upload = await ac.post(
            "/api/v1/documents/upload",
            files={"file": ("download.txt", io.BytesIO(b"download me"), "text/plain")},
        )
        doc_id = upload.json()["id"]

        resp = await ac.get(f"/api/v1/documents/{doc_id}/download")
        assert resp.status_code == 200, resp.text
        assert resp.content == b"download me"

    _run_with_client(test)


def test_update_document_metadata(prepare_database):
    """Update filename and department."""

    async def test(ac):
        upload = await ac.post(
            "/api/v1/documents/upload",
            files={"file": ("old.txt", io.BytesIO(b"x"), "text/plain")},
        )
        doc_id = upload.json()["id"]

        resp = await ac.put(
            f"/api/v1/documents/{doc_id}",
            json={"filename": "new.txt", "department_id": "finance"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["filename"] == "new.txt"
        assert data["department_id"] == "finance"

    _run_with_client(test)


def test_delete_document(prepare_database):
    """Soft-delete returns 204, subsequent GET returns 404."""

    async def test(ac):
        upload = await ac.post(
            "/api/v1/documents/upload",
            files={"file": ("todelete.txt", io.BytesIO(b"x"), "text/plain")},
        )
        doc_id = upload.json()["id"]

        resp = await ac.delete(f"/api/v1/documents/{doc_id}")
        assert resp.status_code == 204, resp.text

        resp = await ac.get(f"/api/v1/documents/{doc_id}")
        assert resp.status_code == 404, resp.text

    _run_with_client(test)


def test_upload_version(prepare_database):
    """Upload a new version increments version_no."""

    async def test(ac):
        upload = await ac.post(
            "/api/v1/documents/upload",
            files={"file": ("v1.txt", io.BytesIO(b"version one"), "text/plain")},
        )
        doc_id = upload.json()["id"]

        resp = await ac.post(
            f"/api/v1/documents/{doc_id}/versions",
            files={"file": ("v2.txt", io.BytesIO(b"version two"), "text/plain")},
            data={"change_note": "second version"},
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["version_no"] == 1
        assert data["size_bytes"] == 11

    _run_with_client(test)


def test_list_versions(prepare_database):
    """List versions returns version history."""

    async def test(ac):
        upload = await ac.post(
            "/api/v1/documents/upload",
            files={"file": ("base.txt", io.BytesIO(b"base"), "text/plain")},
        )
        doc_id = upload.json()["id"]

        await ac.post(
            f"/api/v1/documents/{doc_id}/versions",
            files={"file": ("v.txt", io.BytesIO(b"v"), "text/plain")},
            data={"change_note": "note"},
        )

        resp = await ac.get(f"/api/v1/documents/{doc_id}/versions")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert len(data) == 1
        assert data[0]["change_note"] == "note"

    _run_with_client(test)


def test_bulk_upload_mixed(prepare_database):
    """Bulk upload accepts valid files, skips duplicates, rejects bad ones."""

    async def test(ac):
        files = [
            ("files", ("ok1.txt", io.BytesIO(b"ok one"), "text/plain")),
            ("files", ("ok2.txt", io.BytesIO(b"ok two"), "text/plain")),
        ]
        resp = await ac.post("/api/v1/documents/bulk-upload", files=files)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["accepted"] == 2
        assert data["skipped"] == 0
        assert data["failed"] == 0

        resp = await ac.post(
            "/api/v1/documents/bulk-upload",
            files=[("files", ("ok1.txt", io.BytesIO(b"ok one"), "text/plain"))],
        )
        data = resp.json()
        assert data["skipped"] == 1
        assert data["accepted"] == 0

    _run_with_client(test)


def test_unsupported_media_type(prepare_database):
    """Unsupported mime type returns 415."""

    async def test(ac):
        files = {"file": ("script.sh", io.BytesIO(b"echo hi"), "application/x-shellscript")}
        resp = await ac.post("/api/v1/documents/upload", files=files)
        assert resp.status_code == 415, resp.text

    _run_with_client(test)
