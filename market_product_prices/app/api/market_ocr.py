from __future__ import annotations

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel

from app.services.market_product_service import (
    create_ocr_batch_from_uploads,
    discard_ocr_document,
    get_batch_detail,
    list_batches,
    review_ocr_row,
)


router = APIRouter(prefix="/market/ocr", tags=["market-ocr"])


class OcrRowReviewPayload(BaseModel):
    approved: bool = True
    canonicalName: str | None = None
    approvedPrice: float | None = None
    notes: str | None = None
    createdBy: str | None = None


@router.post("/uploads")
async def upload_market_ocr_images(
    files: list[UploadFile] = File(...),
    source_name: str = Form("SMM Screenshot OCR"),
    uploaded_by: str | None = Form(None),
) -> dict:
    if not files:
        raise HTTPException(status_code=400, detail="Debe enviar al menos una imagen.")
    try:
        return await create_ocr_batch_from_uploads(
            files,
            source_name=source_name,
            uploaded_by=uploaded_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/batches")
def get_ocr_batches(limit: int = Query(20, ge=1, le=100)) -> dict:
    return {"items": list_batches(limit=limit)}


@router.get("/batches/{batch_id}")
def get_ocr_batch(batch_id: int) -> dict:
    item = get_batch_detail(batch_id)
    if not item:
        raise HTTPException(status_code=404, detail="Carga OCR no encontrada.")
    return item


@router.post("/documents/{document_id}/discard")
def discard_market_ocr_document(document_id: int) -> dict:
    try:
        return {"item": discard_ocr_document(document_id)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/rows/{row_id}/review")
def review_market_ocr_row(row_id: int, payload: OcrRowReviewPayload) -> dict:
    try:
        return {
            "item": review_ocr_row(
                row_id,
                approved=payload.approved,
                canonical_name=payload.canonicalName,
                approved_price=payload.approvedPrice,
                notes=payload.notes,
                created_by=payload.createdBy,
            )
        }
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
