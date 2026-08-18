from __future__ import annotations

import json
import mimetypes
import uuid
import urllib.error
import urllib.request

from app.core.config import get_settings


def extract_market_cards(files: list[dict]) -> dict:
    settings = get_settings()
    boundary = f"----market-ocr-{uuid.uuid4().hex}"
    body = _build_multipart_body(boundary, files)
    request = urllib.request.Request(
        f"{settings.ocr_worker_url.rstrip('/')}/extract/lme-market-card",
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OCR worker respondio HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"No fue posible conectar con OCR worker: {exc.reason}") from exc


def _build_multipart_body(boundary: str, files: list[dict]) -> bytes:
    chunks: list[bytes] = []
    for file in files:
        filename = file["filename"]
        content_type = file.get("content_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'
                    f"Content-Type: {content_type}\r\n\r\n"
                ).encode("utf-8"),
                file["data"],
                b"\r\n",
            ]
        )
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks)
