"""Custom SQLAlchemy column types shared across models."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import JSON
from sqlalchemy.types import TypeDecorator


class JSONList(TypeDecorator[tuple[str, ...]]):
    """Stores an ordered tuple of strings as a JSON array.

    Used for multi-valued fields (artists, genres) where order is
    meaningful and must round-trip exactly.
    """

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> list[str] | None:
        if value is None:
            return None
        return list(value)

    def process_result_value(self, value: Any, dialect: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        return tuple(value)


class JSONDict(TypeDecorator[dict[str, str]]):
    """Stores a str->str dict as JSON. Used for extra_tags (the long tail
    of raw tag frames not mapped to a canonical field)."""

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> dict[str, str] | None:
        if value is None:
            return None
        return dict(value)

    def process_result_value(self, value: Any, dialect: Any) -> dict[str, str]:
        if value is None:
            return {}
        return dict(value)


def dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))
