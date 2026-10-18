"""Document routes: upload, list, get, download, versions, delete."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_db
from app.core.config import settings
from app.models.audit_log import AuditLog
from app.models.document import Document, DocumentStatus
from app.models.ocr_result import OcrResult
from app.schemas.document import (
    AuditLogList,
    BulkUploadItem,
    BulkUploadResponse,
    DocumentList,
    DocumentResponse,
    DocumentUpdate,
    OcrResponse,
    OcrTriggerResponse,
    VersionResponse,
)
from app.services.document import (
    compute_sha256,
    create_document,
    create_version,
    find_by_sha256,
    get_document,
    soft_delete_document,
    update_document,
)
from app.services.storage import get_storage

router = APIRouter()


def _allowed_mime(mime_type: str | None) -> bool:
    if not mime_type:
        return True  # treat unspecified/empty as octet-stream
    allowed = {
        "application/pdf",
        "application/octet-stream",
        "image/png",
        "image/jpeg",
        "image/tiff",
        "image/bmp",
        "text/plain",
        "text/csv",
        "application/json",
        "text/xml",
        "application/xml",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    return mime_type in allowed


@router.post(
    "/upload",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a single document",
)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    compression_level: int | None = Form(default=None),
    owner_id: str | None = Form(default=None),
    department_id: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    if file.content_type and not _allowed_mime(file.content_type):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported media type: {file.content_type}",
        )

    data = await file.read()
    if len(data) > settings.MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File exceeds maximum allowed size",
        )

    level = compression_level if compression_level is not None else settings.DEFAULT_COMPRESSION_LEVEL

    doc, _algo = await create_document(
        db,
        filename=file.filename or "unnamed",
        mime_type=file.content_type or "application/octet-stream",
        data=data,
        compression_level=level,
        owner_id=owner_id,
        department_id=department_id,
        status=DocumentStatus.ocr_pending,
    )
    await db.commit()

    # Enqueue OCR via ARQ (best-effort; falls back to inline if Redis unavailable).
    redis = getattr(request.app.state, "redis", None)
    if redis is not None:
        try:
            await redis.enqueue_job("ocr_task", str(doc.id))
        except Exception:  # noqa: BLE001
            pass  # OCR can be triggered manually via POST /{id}/ocr

    return doc  # type: ignore[return-value]


@router.post(
    "/bulk-upload",
    response_model=BulkUploadResponse,
    summary="Bulk upload multiple documents",
)
async def bulk_upload_documents(
    request: Request,
    files: list[UploadFile] = File(...),
    compression_level: int | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
) -> BulkUploadResponse:
    level = compression_level if compression_level is not None else settings.DEFAULT_COMPRESSION_LEVEL
    items: list[BulkUploadItem] = []
    accepted = skipped = failed = 0

    redis = getattr(request.app.state, "redis", None)

    for file in files:
        filename = file.filename or "unnamed"
        try:
            if file.content_type and not _allowed_mime(file.content_type):
                items.append(
                    BulkUploadItem(
                        filename=filename,
                        status="failed",
                        error=f"Unsupported media type: {file.content_type}",
                    )
                )
                failed += 1
                continue

            data = await file.read()
            if len(data) > settings.MAX_FILE_SIZE_BYTES:
                items.append(
                    BulkUploadItem(
                        filename=filename,
                        status="failed",
                        error="File exceeds maximum allowed size",
                    )
                )
                failed += 1
                continue

            sha256 = await compute_sha256(data)
            existing = await find_by_sha256(db, sha256)
            if existing is not None:
                items.append(
                    BulkUploadItem(
                        filename=filename,
                        status="skipped_duplicate",
                        document_id=existing.id,
                    )
                )
                skipped += 1
                continue

            doc, _algo = await create_document(
                db,
                filename=filename,
                mime_type=file.content_type or "application/octet-stream",
                data=data,
                compression_level=level,
                status=DocumentStatus.ocr_pending,
            )
            await db.commit()

            if redis is not None:
                try:
                    await redis.enqueue_job("ocr_task", str(doc.id))
                except Exception:  # noqa: BLE001
                    pass

            items.append(
                BulkUploadItem(
                    filename=filename,
                    status="accepted",
                    document_id=doc.id,
                )
            )
            accepted += 1
        except Exception as exc:  # noqa: BLE001 - one file fails, others continue
            await db.rollback()
            items.append(BulkUploadItem(filename=filename, status="failed", error=str(exc)))
            failed += 1

    return BulkUploadResponse(
        items=items,
        accepted=accepted,
        skipped=skipped,
        failed=failed,
    )


@router.get(
    "/",
    response_model=DocumentList,
    summary="List documents with optional full-text search and filters",
)
async def list_documents(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    search: str | None = Query(default=None, description="Full-text search query (filename + OCR content)"),
    mime_type: str | None = Query(default=None),
    status: DocumentStatus | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> DocumentList:
    query = select(Document).options(selectinload(Document.versions))
    count_query = select(func.count()).select_from(Document)

    if search:
        search_cond = sa_text(
            "search_vector @@ plainto_tsquery('english', :q)"
        ).bindparams(q=search)
        query = query.where(search_cond)
        count_query = count_query.where(search_cond)
        # Rank results by relevance
        rank = func.ts_rank_cd(
            Document.search_vector, func.plainto_tsquery('english', search)
        )
        query = query.order_by(rank.desc(), Document.created_at.desc())
    else:
        query = query.order_by(Document.created_at.desc())

    if mime_type:
        query = query.where(Document.mime_type == mime_type)
        count_query = count_query.where(Document.mime_type == mime_type)

    if status is not None:
        query = query.where(Document.status == status)
        count_query = count_query.where(Document.status == status)

    total = (await db.execute(count_query)).scalar_one()
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    docs = result.scalars().all()

    return DocumentList(
        items=list(docs),  # type: ignore[arg-type]
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/search",
    response_model=DocumentList,
    summary="Dedicated full-text search endpoint with relevance ranking",
)
async def search_documents(
    q: str = Query(..., description="Full-text search query"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    mime_type: str | None = Query(default=None),
    status: DocumentStatus | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> DocumentList:
    ts_query = func.plainto_tsquery('english', q)
    rank = func.ts_rank_cd(Document.search_vector, ts_query).label("rank")

    search_cond = sa_text(
        "search_vector @@ plainto_tsquery('english', :q)"
    ).bindparams(q=q)

    query = (
        select(Document, rank)
        .options(selectinload(Document.versions))
        .where(search_cond)
        .order_by(rank.desc(), Document.created_at.desc())
    )
    count_query = select(func.count()).select_from(Document).where(search_cond)

    if mime_type:
        query = query.where(Document.mime_type == mime_type)
        count_query = count_query.where(Document.mime_type == mime_type)

    if status is not None:
        query = query.where(Document.status == status)
        count_query = count_query.where(Document.status == status)

    total = (await db.execute(count_query)).scalar_one()
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    rows = result.all()
    docs = [row[0] for row in rows]

    return DocumentList(
        items=list(docs),
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Get document metadata",
)
async def get_document_route(document_id: UUID, db: AsyncSession = Depends(get_db)) -> DocumentResponse:
    doc = await get_document(db, str(document_id))
    if doc is None or doc.status == DocumentStatus.deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return doc  # type: ignore[return-value]


@router.get(
    "/{document_id}/download",
    summary="Download document file",
)
async def download_document(document_id: UUID, db: AsyncSession = Depends(get_db)) -> StreamingResponse:
    doc = await get_document(db, str(document_id))
    if doc is None or doc.status == DocumentStatus.deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    storage = get_storage()
    data = await storage.get(doc.stored_path)

    from app.models.document import CompressionAlgo
    from app.services.compression import decompress

    payload = decompress(data, CompressionAlgo(doc.compression_algo))

    headers = {}
    if doc.compression_algo != CompressionAlgo.none:
        headers["Content-Encoding"] = doc.compression_algo.value
        headers["X-Original-Size"] = str(doc.original_size_bytes or 0)
        headers["X-Compressed-Size"] = str(doc.compressed_size_bytes or 0)

    return StreamingResponse(
        iter([payload]),
        media_type=doc.mime_type,
        headers={
            "Content-Disposition": f'attachment; filename="{doc.filename}"',
            **headers,
        },
    )


@router.post(
    "/{document_id}/ocr",
    response_model=OcrTriggerResponse,
    summary="Trigger OCR on a document (enqueues ARQ job)",
)
async def trigger_ocr(
    document_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> OcrTriggerResponse:
    doc = await get_document(db, str(document_id))
    if doc is None or doc.status == DocumentStatus.deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    if doc.status == DocumentStatus.ocr_done:
        return OcrTriggerResponse(
            document_id=doc.id,
            status="already_done",
        )
    if doc.status == DocumentStatus.ocr_failed:
        return OcrTriggerResponse(
            document_id=doc.id,
            status="already_failed",
        )

    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        # ARQ unavailable — run OCR inline as a fallback.
        from app.worker.tasks import ocr_task

        result = await ocr_task({}, str(document_id))
        return OcrTriggerResponse(
            document_id=doc.id,
            status=result.get("status", "queued"),
        )

    # Enqueue ARQ job.
    job = await redis.enqueue_job("ocr_task", str(document_id))
    return OcrTriggerResponse(
        document_id=doc.id,
        status="queued",
        job_id=str(job.job_id) if job is not None else None,
    )


@router.get(
    "/{document_id}/ocr",
    response_model=OcrResponse,
    summary="Get OCR result for a document",
)
async def get_ocr(document_id: UUID, db: AsyncSession = Depends(get_db)) -> OcrResponse:
    doc = await get_document(db, str(document_id))
    if doc is None or doc.status == DocumentStatus.deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    result = await db.execute(
        select(OcrResult).where(OcrResult.document_id == doc.id).order_by(OcrResult.processed_at.desc()).limit(1)
    )
    ocr = result.scalar_one_or_none()
    if ocr is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="OCR result not found. Trigger OCR first.",
        )
    return ocr  # type: ignore[return-value]


@router.put(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Update document metadata",
)
async def update_document_route(
    document_id: UUID,
    payload: DocumentUpdate,
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    doc = await get_document(db, str(document_id))
    if doc is None or doc.status == DocumentStatus.deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    updated = await update_document(db, doc, payload)
    await db.commit()
    await db.refresh(updated)
    return updated  # type: ignore[return-value]


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a document",
)
async def delete_document(document_id: UUID, db: AsyncSession = Depends(get_db)) -> None:
    doc = await get_document(db, str(document_id))
    if doc is None or doc.status == DocumentStatus.deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    await soft_delete_document(db, doc)
    await db.commit()


@router.get(
    "/{document_id}/versions",
    response_model=list[VersionResponse],
    summary="List document versions",
)
async def list_versions(document_id: UUID, db: AsyncSession = Depends(get_db)) -> list[VersionResponse]:
    doc = await get_document(db, str(document_id))
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    return doc.versions  # type: ignore[return-value]


@router.post(
    "/{document_id}/versions",
    response_model=dict,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a new version of a document",
)
async def upload_version(
    document_id: UUID,
    file: UploadFile = File(...),
    change_note: str | None = Form(default=None),
    uploaded_by: str | None = Form(default=None),
    compression_level: int | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    doc = await get_document(db, str(document_id))
    if doc is None or doc.status == DocumentStatus.deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    data = await file.read()
    if len(data) > settings.MAX_FILE_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File exceeds maximum allowed size",
        )

    level = compression_level if compression_level is not None else settings.DEFAULT_COMPRESSION_LEVEL
    version, _algo = await create_version(
        db,
        document=doc,
        data=data,
        mime_type=file.content_type or "application/octet-stream",
        compression_level=level,
        uploaded_by=uploaded_by,
        change_note=change_note,
    )
    await db.commit()
    return {
        "id": str(version.id),
        "version_no": version.version_no,
        "size_bytes": version.size_bytes,
        "created_at": version.created_at.isoformat(),
    }


@router.get(
    "/admin/audit-logs",
    response_model=AuditLogList,
    summary="List audit log entries (compliance)",
)
async def list_audit_logs(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    action: str | None = Query(default=None, description="Filter by action type"),
    document_id: UUID | None = Query(default=None, description="Filter by document ID"),
    db: AsyncSession = Depends(get_db),
) -> AuditLogList:
    query = select(AuditLog).order_by(AuditLog.created_at.desc())
    count_query = select(func.count()).select_from(AuditLog)

    if action is not None:
        query = query.where(AuditLog.action == action)
        count_query = count_query.where(AuditLog.action == action)

    if document_id is not None:
        query = query.where(AuditLog.document_id == document_id)
        count_query = count_query.where(AuditLog.document_id == document_id)

    total = (await db.execute(count_query)).scalar_one()
    offset = (page - 1) * page_size
    query = query.offset(offset).limit(page_size)

    result = await db.execute(query)
    items = result.scalars().all()

    return AuditLogList(
        items=list(items),  # type: ignore[arg-type]
        total=total,
        page=page,
        page_size=page_size,
    )
