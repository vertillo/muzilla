"""Exposes domain.fields (the canonical field registry) to api/cli, so
the frontend's manual tag editor learns field names/types/editability from one
place instead of hardcoding a duplicate list.
"""

from __future__ import annotations

from dataclasses import dataclass

from muzilla.domain import fields as field_registry


@dataclass(frozen=True, slots=True)
class FieldInfo:
    name: str
    label: str
    type: str
    category: str
    editable: bool
    multi_valued: bool
    default_strip: bool


def list_fields() -> list[FieldInfo]:
    return [
        FieldInfo(
            name=f.name,
            label=f.label,
            type=f.type.value,
            category=f.category.value,
            editable=f.editable,
            multi_valued=f.type.value == "multi_text",
            default_strip=f.default_strip,
        )
        for f in field_registry.FIELDS.values()
    ]
