"""Pydantic schema for /api/fields — the canonical field registry, so
the frontend never hardcodes a duplicate field list."""

from __future__ import annotations

from pydantic import BaseModel


class FieldInfoOut(BaseModel):
    model_config = {"from_attributes": True}

    name: str
    label: str
    type: str
    category: str
    editable: bool
    multi_valued: bool
    default_strip: bool


class FieldListOut(BaseModel):
    items: list[FieldInfoOut]
