from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

from app.core.config import PROJECT_ROOT, get_settings


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_uploaded_image(data: bytes, filename: str | None, image_sha256: str) -> dict:
    settings = get_settings()
    now = datetime.utcnow()
    suffix = Path(filename or "").suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
        suffix = ".jpg"

    safe_name = _safe_name(Path(filename or "market-image").stem)
    target_dir = settings.upload_root / str(now.year) / f"{now.month:02d}"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{image_sha256[:16]}-{safe_name}{suffix}"
    target.write_bytes(data)

    return {
        "path": str(target.resolve().relative_to(PROJECT_ROOT)),
        "size": len(data),
    }


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned[:80] or "market-image"
