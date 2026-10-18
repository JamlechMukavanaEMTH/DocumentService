"""Document business logic (CRUD, versioning, deduplication)."""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import safe_storage_key
from app.models.document import CompressionAlgo, Document, DocumentStatus
from app.models.document_version import DocumentVersion
from app.schemas.document import DocumentUpdate
from app.services.compression import compress
from app.services.storage import StorageBackend, get_storage


async def compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def find_by_sha256(session: AsyncSession, sha256: str) -> Document | None:
    result = await session.execute(select(Document).where(Document.sha256 == sha256))
    return result.scalar_one_or_none()


async def create_document(
    session: AsyncSession,
    *,
    filename: str,
    mime_type: str,
    data: bytes,
    compression_level: int = 6,
    owner_id: str | None = None,
    department_id: str | None = None,
    status: DocumentStatus = DocumentStatus.ready,
) -> tuple[Document, CompressionAlgo]:
    """Validate, compress, store, and persist a document.

    Returns the created Document and the compression algorithm used.
    Skips storage if an identical SHA-256 already exists (dedup).
    """
    sha256 = await compute_sha256(data)

    existing = await find_by_sha256(session, sha256)
    if existing is not None:
        return existing, existing.compression_algo

    result = compress(data, mime_type, level=compression_level)

    storage: StorageBackend = get_storage()
    key = safe_storage_key(sha256, filename)
    stored = await storage.save(key, result.data)

    doc = Document(
        filename=filename,
        stored_path=stored.path,
        mime_type=mime_type,
        size_bytes=stored.size_bytes,
        original_size_bytes=result.original_size,
        compressed_size_bytes=result.compressed_size,
        compression_ratio=result.compression_ratio,
        compression_algo=result.algorithm,
        sha256=sha256,
        owner_id=owner_id,
        department_id=department_id,
        status=status,
    )
    session.add(doc)
    await session.flush()
    return doc, result.algorithm


async def get_document(session: AsyncSession, document_id: str) -> Document | None:
    result = await session.execute(
        select(Document)
        .options(selectinload(Document.versions))
        .where(Document.id == document_id)
    )
    return result.scalar_one_or_none()


async def update_document(
    session: AsyncSession,
    document: Document,
    payload: DocumentUpdate,
) -> Document:
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(document, field, value)
    await session.flush()
    return document


async def soft_delete_document(session: AsyncSession, document: Document) -> None:
    document.status = DocumentStatus.deleted
    await session.flush()


async def create_version(
    session: AsyncSession,
    document: Document,
    *,
    data: bytes,
    mime_type: str,
    compression_level: int = 6,
    uploaded_by: str | None = None,
    change_note: str | None = None,
) -> tuple[DocumentVersion, CompressionAlgo]:
    """Store a new version of an existing document."""
    sha256 = await compute_sha256(data)
    result = compress(data, mime_type, level=compression_level)

    storage: StorageBackend = get_storage()
    key = safe_storage_key(sha256, document.filename)
    stored = await storage.save(key, result.data)

    # Determine next version number.
    existing = await session.execute(
        select(DocumentVersion.version_no)
        .where(DocumentVersion.document_id == document.id)
        .order_by(DocumentVersion.version_no.desc())
    )
    last = existing.scalar_one_or_none()
    next_no = (last or 0) + 1

    version = DocumentVersion(
        document_id=document.id,
        version_no=next_no,
        stored_path=stored.path,
        size_bytes=stored.size_bytes,
        uploaded_by=uploaded_by,
        change_note=change_note,
    )
    session.add(version)
    await session.flush()

    # Update the head document record to point at latest version payload.
    document.stored_path = stored.path
    document.size_bytes = stored.size_bytes
    document.mime_type = mime_type
    document.original_size_bytes = result.original_size
    document.compressed_size_bytes = result.compressed_size
    document.compression_ratio = result.compression_ratio
    document.compression_algo = result.algorithm
    document.sha256 = sha256
    await session.flush()
    return version, result.algorithm
