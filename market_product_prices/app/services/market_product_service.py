from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date

from fastapi import UploadFile

from app.core.config import get_settings
from app.core.database import get_connection, row_to_dict, utc_now_db
from app.services.market_ocr_client import extract_market_cards
from app.services.market_validation_service import normalize_product_name, product_key_from_name, validate_extracted_item
from app.services.storage_service import save_uploaded_image, sha256_bytes


async def create_ocr_batch_from_uploads(
    uploads: list[UploadFile],
    *,
    source_name: str,
    uploaded_by: str | None,
) -> dict:
    settings = get_settings()
    files: list[dict] = []
    for upload in uploads:
        data = await upload.read()
        if not data:
            raise ValueError("Uno de los archivos esta vacio.")
        if len(data) > settings.max_upload_bytes:
            raise ValueError(f"{upload.filename or 'imagen'} supera el tamano maximo permitido.")
        files.append(
            {
                "filename": upload.filename or "market-image.jpg",
                "content_type": upload.content_type or "image/jpeg",
                "data": data,
                "sha256": sha256_bytes(data),
            }
        )

    worker_payload = await asyncio.to_thread(extract_market_cards, files)
    return await asyncio.to_thread(_persist_worker_payload, files, worker_payload, source_name, uploaded_by)


def _persist_worker_payload(files: list[dict], worker_payload: dict, source_name: str, uploaded_by: str | None) -> dict:
    files_by_name = {item["filename"]: item for item in files}
    items = worker_payload.get("items") or []
    errors = worker_payload.get("errors") or []

    with get_connection() as conn:
        batch_id = _insert_batch(conn, source_name, uploaded_by, len(files))
        documents_ok = 0
        documents_failed = 0
        documents_pending = 0
        persisted_items = []

        for item in items:
            filename = item.get("sourceFilename") or ""
            source_file = files_by_name.get(filename)
            if not source_file:
                documents_failed += 1
                continue

            validation = validate_extracted_item(conn, item)
            document_status = "pending_review" if validation.requires_review else "processed"
            storage = save_uploaded_image(source_file["data"], filename, source_file["sha256"])
            document_id = _insert_document(
                conn,
                batch_id=batch_id,
                status=document_status,
                filename=filename,
                image_sha256=source_file["sha256"],
                image_path=storage["path"],
                item=item,
            )
            row_id = _insert_row(conn, document_id=document_id, item=item, validation=validation)
            price_id = None
            if not validation.requires_review and validation.product_id:
                price_id = _insert_price_history(
                    conn,
                    product_id=validation.product_id,
                    document_id=document_id,
                    row_id=row_id,
                    item=item,
                    source_name=source_name,
                )

            if validation.requires_review:
                documents_pending += 1
            else:
                documents_ok += 1

            persisted_items.append(
                {
                    "documentId": document_id,
                    "rowId": row_id,
                    "priceId": price_id,
                    "status": document_status,
                    "requiresReview": validation.requires_review,
                    "reviewReason": validation.review_reason,
                    "name": item.get("name"),
                    "price": item.get("price"),
                    "date": item.get("date"),
                }
            )

        for error in errors:
            documents_failed += 1
            filename = error.get("filename") or ""
            _insert_failed_document(conn, batch_id=batch_id, error=error, source_file=files_by_name.get(filename))

        status = _batch_status(documents_ok, documents_pending, documents_failed)
        _finish_batch(
            conn,
            batch_id=batch_id,
            status=status,
            documents_ok=documents_ok,
            documents_failed=documents_failed,
            documents_pending=documents_pending,
        )

    return {
        "ok": documents_failed == 0,
        "batchId": batch_id,
        "status": status,
        "totalDocuments": len(files),
        "documentsOk": documents_ok,
        "documentsPendingReview": documents_pending,
        "documentsFailed": documents_failed,
        "items": persisted_items,
        "workerErrors": errors,
    }


def list_batches(limit: int = 20) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM ocr_batches
                ORDER BY created_at DESC, batch_id DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cursor.fetchall()
    return [row_to_dict(row) for row in rows if row]


def get_batch_detail(batch_id: int) -> dict | None:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM ocr_batches WHERE batch_id = %s", (batch_id,))
            batch = row_to_dict(cursor.fetchone())
            if not batch:
                return None

            cursor.execute(
                """
                SELECT d.*, r.row_id, r.detected_name, r.observed_date, r.price,
                       r.currency, r.unit, r.high_price, r.low_price,
                       r.requires_review, r.review_reason, r.validation_status,
                       r.approved_product_id, r.approved_price,
                       p.canonical_name AS approved_product_name
                FROM ocr_documents d
                LEFT JOIN ocr_rows r ON r.document_id = d.document_id
                LEFT JOIN products p ON p.product_id = r.approved_product_id
                WHERE d.batch_id = %s
                ORDER BY d.document_id ASC
                """,
                (batch_id,),
            )
            documents = [row_to_dict(row) for row in cursor.fetchall()]
    return {"batch": batch, "documents": documents}


def review_ocr_row(
    row_id: int,
    *,
    approved: bool,
    canonical_name: str | None,
    approved_price: float | None,
    notes: str | None,
    created_by: str | None,
) -> dict:
    with get_connection() as conn:
        row = _get_row_for_review(conn, row_id)
        if not row:
            raise LookupError("Fila OCR no encontrada.")
        if row["validation_status"] in {"approved", "corrected"} and not row["requires_review"]:
            return _review_result(conn, row_id)

        if not approved:
            _reject_row(conn, row, notes, created_by)
            _refresh_document_and_batch_status(conn, row["document_id"])
            return _review_result(conn, row_id)

        final_name = (canonical_name or row.get("detected_name") or "").strip()
        if not final_name:
            raise ValueError("Debe indicar el nombre del producto.")

        final_price = approved_price if approved_price is not None else row.get("price")
        if final_price is None:
            raise ValueError("Debe indicar el precio aprobado.")

        normalized = normalize_product_name(final_name)
        product = _find_or_create_product(conn, final_name, normalized, row, created_by)
        _ensure_alias(conn, product["product_id"], final_name, normalized)
        if row.get("detected_name"):
            _ensure_alias(conn, product["product_id"], row["detected_name"], normalize_product_name(row["detected_name"]))

        _approve_row(conn, row, product["product_id"], final_price, notes, canonical_name)
        price_id = _insert_price_history(
            conn,
            product_id=product["product_id"],
            document_id=row["document_id"],
            row_id=row_id,
            item={
                "date": row["observed_date"].isoformat() if hasattr(row["observed_date"], "isoformat") else row["observed_date"],
                "price": final_price,
                "currency": row.get("currency") or "USD",
                "unit": row.get("unit") or "kg",
                "high": row.get("high_price"),
                "low": row.get("low_price"),
                "changeValue": row.get("variation_value"),
                "changePercent": row.get("variation_percent"),
            },
            source_name=row.get("batch_source_name") or "SMM Screenshot OCR",
        )
        _insert_review_event(
            conn,
            document_id=row["document_id"],
            row_id=row_id,
            product_id=product["product_id"],
            price_id=price_id,
            action="corrected_row" if canonical_name or approved_price is not None else "approved_row",
            previous_value={"status": row["validation_status"], "review_reason": row["review_reason"]},
            new_value={"product_id": product["product_id"], "price_id": price_id, "price": final_price},
            notes=notes,
            created_by=created_by,
        )
        _refresh_document_and_batch_status(conn, row["document_id"])
        return _review_result(conn, row_id)


def discard_ocr_document(document_id: int) -> dict:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT *
                FROM ocr_documents
                WHERE document_id = %s
                LIMIT 1
                """,
                (document_id,),
            )
            document = cursor.fetchone()
            if not document:
                raise LookupError("Documento OCR no encontrado.")
            if document["status"] == "rejected":
                _recount_batch(conn, document.get("batch_id"))
                return row_to_dict(document)
            if document["status"] != "failed":
                raise ValueError("Solo se pueden descartar documentos fallidos sin fila OCR.")

            cursor.execute(
                """
                UPDATE ocr_documents
                SET status = 'rejected',
                    reviewed_at = %s
                WHERE document_id = %s
                """,
                (utc_now_db(), document_id),
            )
            _insert_review_event(
                conn,
                document_id=document_id,
                row_id=None,
                product_id=None,
                price_id=None,
                action="rejected_document",
                previous_value={"status": document["status"], "error_message": document.get("error_message")},
                new_value={"status": "rejected"},
                notes="Documento fallido descartado desde SMM OCR.",
                created_by=None,
            )
            _recount_batch(conn, document.get("batch_id"))
            cursor.execute(
                """
                SELECT *
                FROM ocr_documents
                WHERE document_id = %s
                LIMIT 1
                """,
                (document_id,),
            )
            return row_to_dict(cursor.fetchone()) or {}


def list_products(*, active_only: bool = False) -> list[dict]:
    filters = ["1 = 1"]
    if active_only:
        filters.append("is_active = 1")
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT *
                FROM products
                WHERE {' AND '.join(filters)}
                ORDER BY canonical_name ASC
                """
            )
            rows = cursor.fetchall()
    return [row_to_dict(row) for row in rows if row]


def list_latest_prices() -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT p.*, h.price_id, h.observed_date, h.price, h.currency, h.unit,
                       h.high_price, h.low_price, h.variation_value, h.variation_percent,
                       h.source_name AS price_source_name, h.created_at AS price_created_at
                FROM products p
                INNER JOIN (
                    SELECT ph.*
                    FROM price_history ph
                    INNER JOIN (
                        SELECT product_id, MAX(observed_date) AS latest_date
                        FROM price_history
                        WHERE status = 'approved'
                        GROUP BY product_id
                    ) latest ON latest.product_id = ph.product_id
                             AND latest.latest_date = ph.observed_date
                    WHERE ph.status = 'approved'
                ) h ON h.product_id = p.product_id
                ORDER BY p.canonical_name ASC
                """
            )
            rows = cursor.fetchall()
    return [row_to_dict(row) for row in rows if row]


def list_product_history(product_id: int, desde: date | None = None, hasta: date | None = None) -> list[dict]:
    filters = ["product_id = %s", "status = 'approved'"]
    params: list[object] = [product_id]
    if desde:
        filters.append("observed_date >= %s")
        params.append(desde)
    if hasta:
        filters.append("observed_date <= %s")
        params.append(hasta)

    with get_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT *
                FROM price_history
                WHERE {' AND '.join(filters)}
                ORDER BY observed_date ASC, price_id ASC
                """,
                params,
            )
            rows = cursor.fetchall()
    return [row_to_dict(row) for row in rows if row]


def _insert_batch(conn, source_name: str, uploaded_by: str | None, total_documents: int) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO ocr_batches (status, source_name, uploaded_by, total_documents)
            VALUES ('processing', %s, %s, %s)
            """,
            (source_name, uploaded_by, total_documents),
        )
        return int(cursor.lastrowid)


def _insert_document(conn, *, batch_id: int, status: str, filename: str, image_sha256: str, image_path: str, item: dict) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO ocr_documents (
                batch_id, status, original_filename, image_sha256, image_path,
                image_width, image_height, image_size, raw_ocr_text,
                raw_worker_json, processed_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                batch_id,
                status,
                filename,
                image_sha256,
                image_path,
                (item.get("image") or {}).get("width"),
                (item.get("image") or {}).get("height"),
                (item.get("image") or {}).get("bytes"),
                item.get("rawText"),
                json.dumps(item, ensure_ascii=False),
                utc_now_db(),
            ),
        )
        return int(cursor.lastrowid)


def _insert_failed_document(conn, *, batch_id: int, error: dict, source_file: dict | None = None) -> int:
    error_hash = hashlib.sha256(
        json.dumps(error, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    storage = None
    if source_file:
        storage = save_uploaded_image(source_file["data"], source_file["filename"], source_file["sha256"])
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO ocr_documents (
                batch_id, status, original_filename, image_sha256, image_path,
                image_size, raw_worker_json, error_message
            )
            VALUES (%s, 'failed', %s, %s, %s, %s, %s, %s)
            """,
            (
                batch_id,
                error.get("filename"),
                source_file["sha256"] if source_file else error_hash,
                storage["path"] if storage else None,
                len(source_file["data"]) if source_file else None,
                json.dumps(error, ensure_ascii=False),
                error.get("message"),
            ),
        )
        return int(cursor.lastrowid)


def _insert_row(conn, *, document_id: int, item: dict, validation) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO ocr_rows (
                document_id, product_id, suggested_product_id, approved_product_id,
                detected_name, normalized_name, specification, observed_date,
                price, approved_price, currency, unit, high_price, low_price,
                variation_value, variation_percent, confidence, raw_text,
                requires_review, review_reason, validation_status, validation_notes
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                document_id,
                validation.product_id,
                validation.suggested_product_id,
                validation.product_id if not validation.requires_review else None,
                item.get("name"),
                item.get("nameNormalized"),
                item.get("specification"),
                item.get("date"),
                item.get("price"),
                item.get("price") if not validation.requires_review else None,
                item.get("currency"),
                item.get("unit"),
                item.get("high"),
                item.get("low"),
                item.get("changeValue"),
                item.get("changePercent"),
                item.get("confidence"),
                item.get("rawText"),
                1 if validation.requires_review else 0,
                validation.review_reason,
                validation.validation_status,
                validation.validation_notes,
            ),
        )
        return int(cursor.lastrowid)


def _insert_price_history(conn, *, product_id: int, document_id: int, row_id: int, item: dict, source_name: str) -> int:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO price_history (
                product_id, observed_date, price, currency, unit, high_price,
                low_price, variation_value, variation_percent, source_name,
                ocr_document_id, ocr_row_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                price = VALUES(price),
                high_price = VALUES(high_price),
                low_price = VALUES(low_price),
                variation_value = VALUES(variation_value),
                variation_percent = VALUES(variation_percent),
                ocr_document_id = VALUES(ocr_document_id),
                ocr_row_id = VALUES(ocr_row_id)
            """,
            (
                product_id,
                item.get("date"),
                item.get("price"),
                item.get("currency") or "USD",
                item.get("unit") or "kg",
                item.get("high"),
                item.get("low"),
                item.get("changeValue"),
                item.get("changePercent"),
                source_name,
                document_id,
                row_id,
            ),
        )
        if cursor.lastrowid:
            return int(cursor.lastrowid)
        cursor.execute(
            """
            SELECT price_id
            FROM price_history
            WHERE product_id = %s
              AND observed_date = %s
              AND source_name = %s
            LIMIT 1
            """,
            (product_id, item.get("date"), source_name),
        )
        row = cursor.fetchone()
        return int(row["price_id"]) if row else 0


def _finish_batch(conn, *, batch_id: int, status: str, documents_ok: int, documents_failed: int, documents_pending: int) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE ocr_batches
            SET status = %s,
                documents_ok = %s,
                documents_failed = %s,
                documents_pending_review = %s,
                finished_at = %s
            WHERE batch_id = %s
            """,
            (status, documents_ok, documents_failed, documents_pending, utc_now_db(), batch_id),
        )


def _batch_status(documents_ok: int, documents_pending: int, documents_failed: int) -> str:
    if documents_failed and not documents_ok and not documents_pending:
        return "failed"
    if documents_pending and not documents_failed:
        return "pending_review"
    if documents_failed or documents_pending:
        return "partial"
    return "processed"


def _get_row_for_review(conn, row_id: int) -> dict | None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT r.*, d.batch_id, b.source_name AS batch_source_name
            FROM ocr_rows r
            INNER JOIN ocr_documents d ON d.document_id = r.document_id
            LEFT JOIN ocr_batches b ON b.batch_id = d.batch_id
            WHERE r.row_id = %s
            LIMIT 1
            """,
            (row_id,),
        )
        return cursor.fetchone()


def _find_or_create_product(conn, canonical_name: str, normalized: str, row: dict, created_by: str | None) -> dict:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT *
            FROM products
            WHERE normalized_name = %s
            LIMIT 1
            """,
            (normalized,),
        )
        existing = cursor.fetchone()
        if existing:
            return existing

        product_key = _unique_product_key(conn, product_key_from_name(canonical_name))
        product_type = _infer_product_type(canonical_name)
        market_region = "India" if "FOB INDIA" in normalized else None
        origin_country = "India" if "FOB INDIA" in normalized else None
        cursor.execute(
            """
            INSERT INTO products (
                product_key, base_metal, canonical_name, normalized_name,
                specification, product_type, market_region, origin_country,
                source_name, default_currency, default_unit
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                product_key,
                "tungsten" if "TUNGSTEN" in normalized else "market",
                canonical_name,
                normalized,
                row.get("specification"),
                product_type,
                market_region,
                origin_country,
                row.get("batch_source_name") or "SMM Screenshot OCR",
                row.get("currency") or "USD",
                row.get("unit") or "kg",
            ),
        )
        product_id = int(cursor.lastrowid)
        _insert_review_event(
            conn,
            document_id=row["document_id"],
            row_id=row["row_id"],
            product_id=product_id,
            price_id=None,
            action="created_product",
            previous_value=None,
            new_value={"product_id": product_id, "canonical_name": canonical_name},
            notes="Producto creado desde revisión OCR.",
            created_by=created_by,
        )
        cursor.execute("SELECT * FROM products WHERE product_id = %s", (product_id,))
        return cursor.fetchone()


def _unique_product_key(conn, base_key: str) -> str:
    key = base_key[:110] or "market_product"
    candidate = key
    suffix = 2
    with conn.cursor() as cursor:
        while True:
            cursor.execute("SELECT product_id FROM products WHERE product_key = %s", (candidate,))
            if not cursor.fetchone():
                return candidate
            suffix_text = f"_{suffix}"
            candidate = f"{key[:120 - len(suffix_text)]}{suffix_text}"
            suffix += 1


def _ensure_alias(conn, product_id: int, alias_text: str, normalized_alias: str) -> None:
    if not normalized_alias:
        return
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO product_aliases (
                product_id, alias_text, normalized_alias, source, match_rule
            )
            VALUES (%s, %s, %s, 'reviewed', 'manual_link')
            ON DUPLICATE KEY UPDATE
                product_id = VALUES(product_id),
                alias_text = VALUES(alias_text)
            """,
            (product_id, alias_text, normalized_alias),
        )


def _approve_row(conn, row: dict, product_id: int, final_price: float, notes: str | None, canonical_name: str | None) -> None:
    status = "corrected" if canonical_name or final_price != row.get("price") else "approved"
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE ocr_rows
            SET approved_product_id = %s,
                approved_price = %s,
                requires_review = 0,
                review_reason = 'none',
                validation_status = %s,
                validation_notes = %s,
                reviewed_at = %s
            WHERE row_id = %s
            """,
            (product_id, final_price, status, notes, utc_now_db(), row["row_id"]),
        )


def _reject_row(conn, row: dict, notes: str | None, created_by: str | None) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE ocr_rows
            SET requires_review = 0,
                validation_status = 'rejected',
                validation_notes = %s,
                reviewed_at = %s
            WHERE row_id = %s
            """,
            (notes, utc_now_db(), row["row_id"]),
        )
    _insert_review_event(
        conn,
        document_id=row["document_id"],
        row_id=row["row_id"],
        product_id=row.get("approved_product_id") or row.get("product_id"),
        price_id=None,
        action="corrected_row",
        previous_value={"status": row["validation_status"]},
        new_value={"status": "rejected"},
        notes=notes,
        created_by=created_by,
    )


def _refresh_document_and_batch_status(conn, document_id: int) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT d.batch_id,
                   SUM(CASE WHEN r.validation_status = 'rejected' THEN 1 ELSE 0 END) AS rejected_rows,
                   SUM(CASE WHEN r.requires_review = 1 AND r.validation_status <> 'rejected' THEN 1 ELSE 0 END) AS pending_rows
            FROM ocr_documents d
            LEFT JOIN ocr_rows r ON r.document_id = d.document_id
            WHERE d.document_id = %s
            GROUP BY d.batch_id
            """,
            (document_id,),
        )
        status_row = cursor.fetchone()
        if not status_row:
            return
        rejected_rows = int(status_row.get("rejected_rows") or 0)
        pending_rows = int(status_row.get("pending_rows") or 0)
        document_status = "rejected" if rejected_rows else "pending_review" if pending_rows else "processed"
        cursor.execute(
            """
            UPDATE ocr_documents
            SET status = %s,
                reviewed_at = %s
            WHERE document_id = %s
            """,
            (document_status, utc_now_db(), document_id),
        )
        _recount_batch(conn, status_row.get("batch_id"))


def _recount_batch(conn, batch_id: int | None) -> None:
    if not batch_id:
        return
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT
              SUM(CASE WHEN status = 'processed' THEN 1 ELSE 0 END) AS ok_count,
              SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
              SUM(CASE WHEN status = 'pending_review' THEN 1 ELSE 0 END) AS pending_count,
              SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected_count
            FROM ocr_documents
            WHERE batch_id = %s
            """,
            (batch_id,),
        )
        counts = cursor.fetchone() or {}
        ok_count = int(counts.get("ok_count") or 0)
        true_failed_count = int(counts.get("failed_count") or 0)
        rejected_count = int(counts.get("rejected_count") or 0)
        pending_count = int(counts.get("pending_count") or 0)
        if rejected_count and not ok_count and not pending_count and not true_failed_count:
            status = "rejected"
        elif true_failed_count or pending_count:
            status = "partial"
        else:
            status = "processed"
        _finish_batch(
            conn,
            batch_id=batch_id,
            status=status,
            documents_ok=ok_count,
            documents_failed=true_failed_count,
            documents_pending=pending_count,
        )


def _review_result(conn, row_id: int) -> dict:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            SELECT r.*, p.canonical_name AS approved_product_name,
                   ph.price_id
            FROM ocr_rows r
            LEFT JOIN products p ON p.product_id = r.approved_product_id
            LEFT JOIN price_history ph ON ph.ocr_row_id = r.row_id
            WHERE r.row_id = %s
            LIMIT 1
            """,
            (row_id,),
        )
        return row_to_dict(cursor.fetchone()) or {}


def _insert_review_event(
    conn,
    *,
    document_id: int | None,
    row_id: int | None,
    product_id: int | None,
    price_id: int | None,
    action: str,
    previous_value: dict | None,
    new_value: dict | None,
    notes: str | None,
    created_by: str | None,
) -> None:
    with conn.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO review_events (
                document_id, row_id, product_id, price_id, action,
                previous_value, new_value, notes, created_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                document_id,
                row_id,
                product_id,
                price_id,
                action,
                json.dumps(previous_value, ensure_ascii=False) if previous_value is not None else None,
                json.dumps(new_value, ensure_ascii=False) if new_value is not None else None,
                notes,
                created_by,
            ),
        )


def _infer_product_type(name: str) -> str | None:
    normalized = normalize_product_name(name)
    if "DRILL" in normalized and "BIT" in normalized:
        return "drill_bits"
    if "INSERT" in normalized:
        return "inserts"
    if "BLADE" in normalized:
        return "blades"
    if "BAR" in normalized:
        return "bar"
    if "SCRAP" in normalized:
        return "scrap"
    return None
