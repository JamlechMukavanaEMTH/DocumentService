"""Background worker tasks: OCR and thumbnail generation.

Run the worker with:

    arq app.worker.settings.WorkerSettings
"""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
from typing import Any

from pdf2image import convert_from_bytes
from PIL import Image

from app.api.deps import AsyncSessionLocal
from app.core.config import settings
from app.models.document import Document, DocumentStatus
from app.models.ocr_result import OcrResult
from app.services.compression import decompress
from app.services.storage import get_storage

logger = logging.getLogger("docservice.worker")

TESSERACT_CMD = shutil.which("tesseract")


def _run_tesseract(image: Image.Image, language: str) -> tuple[str, float]:
    """Run tesseract on a single PIL image, return (text, avg_confidence)."""
    if TESSERACT_CMD is None:
        raise RuntimeError(
            "tesseract binary not found on PATH. Install tesseract-ocr to enable OCR."
        )

    # Save image to a temp PNG for tesseract.
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    buf.seek(0)

    # tesseract reads from stdin (image) and writes text to stdout.
    proc = subprocess.run(  # noqa: PLW1510
        [TESSERACT_CMD, "stdin", "stdout", "-l", language, "-c", "tessedit_create_hocr=0"],
        input=buf.read(),
        capture_output=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"tesseract failed: {proc.stderr.decode(errors='replace')}")

    text = proc.stdout.decode(errors="replace")
    # Confidence isn't easily extracted from plain stdout; use a heuristic:
    # ratio of non-whitespace chars to total as a rough confidence proxy.
    stripped = text.strip()
    confidence = min(1.0, len(stripped) / max(len(text), 1)) if text else 0.0
    return text, confidence


async def ocr_task(ctx: dict[str, Any], document_id: str, version_id: str | None = None) -> dict[str, Any]:
    """OCR a document: decompress, convert PDF pages to images, run tesseract,
    store extracted text, update document status and full-text search vector."""
    async with AsyncSessionLocal() as session:
        doc = await session.get(Document, document_id)
        if doc is None:
            return {"status": "error", "reason": "document_not_found"}

        # Mark OCR in progress.
        doc.status = DocumentStatus.ocr_pending
        await session.commit()

        try:
            storage = get_storage()
            raw = await storage.get(doc.stored_path)

            # Decompress if needed.
            from app.models.document import CompressionAlgo

            payload = decompress(raw, CompressionAlgo(doc.compression_algo))

            # Convert to images.
            images: list[Image.Image] = []
            if doc.mime_type == "application/pdf":
                images = convert_from_bytes(payload, dpi=200)
            else:
                # Single image file.
                images = [Image.open(io.BytesIO(payload)).convert("RGB")]

            all_text: list[str] = []
            confidences: list[float] = []

            for img in images:
                text, conf = _run_tesseract(img, settings.OCR_LANGUAGE)
                all_text.append(text)
                confidences.append(conf)

            extracted_text = "\n\n--- Page Break ---\n\n".join(all_text)
            avg_confidence = (
                sum(confidences) / len(confidences) if confidences else 0.0
            )

            # Build denormalized search text: filename + OCR content.
            search_text = f"{doc.filename}\n\n{extracted_text}".strip()

            # Persist OCR result.
            ocr = OcrResult(
                document_id=doc.id,
                version_id=version_id,
                extracted_text=extracted_text,
                language=settings.OCR_LANGUAGE,
                confidence=avg_confidence,
            )
            session.add(ocr)

            # Update document with OCR text for full-text search.
            doc.status = DocumentStatus.ocr_done
            doc.search_text = search_text
            await session.commit()

            return {
                "status": "ocr_done",
                "document_id": document_id,
                "characters": len(extracted_text),
                "confidence": round(avg_confidence, 4),
            }

        except Exception as exc:
            logger.exception("OCR failed for document %s", document_id)
            doc.status = DocumentStatus.ocr_failed
            await session.commit()
            return {"status": "ocr_failed", "document_id": document_id, "error": str(exc)}


async def thumbnail_task(ctx: dict[str, Any], document_id: str) -> dict[str, Any]:
    """Generate a small JPEG thumbnail for image/PDF documents."""
    async with AsyncSessionLocal() as session:
        doc = await session.get(Document, document_id)
        if doc is None:
            return {"status": "error", "reason": "document_not_found"}

        try:
            storage = get_storage()
            raw = await storage.get(doc.stored_path)
            from app.models.document import CompressionAlgo

            payload = decompress(raw, CompressionAlgo(doc.compression_algo))

            if doc.mime_type == "application/pdf":
                images = convert_from_bytes(payload, dpi=72, first_page=1, last_page=1)
                img = images[0] if images else None
            else:
                img = Image.open(io.BytesIO(payload)).convert("RGB")

            if img is None:
                return {"status": "skipped", "reason": "no_renderable_content"}

            img.thumbnail((256, 256))
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=80)
            thumb_bytes = out.getvalue()

            thumb_key = f"thumbs/{document_id}.jpg"
            await storage.save(thumb_key, thumb_bytes)
            return {"status": "thumbnail_done", "document_id": document_id}

        except Exception as exc:
            logger.exception("Thumbnail failed for document %s", document_id)
            return {"status": "thumbnail_failed", "document_id": document_id, "error": str(exc)}
