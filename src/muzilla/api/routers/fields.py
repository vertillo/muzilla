"""GET /api/fields — the canonical field registry (docs/product-spec.md)."""

from __future__ import annotations

from fastapi import APIRouter

from muzilla.api.schemas.fields import FieldListOut
from muzilla.services import fields as fields_service

router = APIRouter(tags=["fields"])


@router.get("/fields", response_model=FieldListOut)
async def list_fields() -> FieldListOut:
    return FieldListOut(items=fields_service.list_fields())  # type: ignore[arg-type]
