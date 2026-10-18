"""Document model."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.document_version import DocumentVersion
    from app.models.ocr_result import OcrResult


class DocumentStatus(str, PyEnum):
    uploading = "uploading"
    ready = "ready"
    ocr_pending = "ocr_pending"
    ocr_done = "ocr_done"
    ocr_failed = "ocr_failed"
    archived = "archived"
    deleted = "deleted"


class CompressionAlgo(str, PyEnum):
    none = "none"
    gzip = "gzip"
    zlib = "zlib"
    pdf = "pdf"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    stored_path: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    compressed_size_bytes: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    compression_ratio: Mapped[float | None] = mapped_column(
        Numeric(5, 4), nullable=True
    )
    compression_algo: Mapped[CompressionAlgo] = mapped_column(
        Enum(CompressionAlgo), default=CompressionAlgo.gzip, nullable=False
    )
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    owner_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    department_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(
        Enum(DocumentStatus), default=DocumentStatus.uploading, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    # Full-text search support
    search_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    search_vector = mapped_column(TSVECTOR, nullable=True, index=True)

    versions: Mapped[list[DocumentVersion]] = relationship(
        "DocumentVersion", back_populates="document", cascade="all, delete-orphan"
    )
    ocr_results: Mapped[list[OcrResult]] = relationship(
        "OcrResult", back_populates="document", cascade="all, delete-orphan"
    )
