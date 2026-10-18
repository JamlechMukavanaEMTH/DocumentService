"""Security utilities: filename sanitization, path-traversal guards."""

from __future__ import annotations

import re
import unicodedata

_DANGEROUS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_filename(filename: str) -> str:
    """Return a safe filesystem-friendly filename.

    Strips directory components, dangerous characters, and control chars.
    Never returns an empty string (falls back to ``unnamed``).
    """
    # Remove any directory traversal.
    name = filename.split("/")[-1].split("\\")[-1]
    # Normalize unicode (e.g. fullwidth slash) then strip dangerous chars.
    name = unicodedata.normalize("NFKC", name)
    name = _DANGEROUS_CHARS.sub("_", name)
    name = name.strip(" .")
    if not name or name in {".", ".."}:
        name = "unnamed"
    return name


def safe_storage_key(sha256: str, filename: str) -> str:
    """Build a sharded storage key from a content hash + filename."""
    from app.services.storage import _shard_key

    safe = sanitize_filename(filename)
    return _shard_key(sha256, safe)
