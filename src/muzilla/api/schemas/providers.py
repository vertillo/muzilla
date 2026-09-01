"""Pydantic response models for GET /api/providers/status."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ProviderStatusOut(BaseModel):
    model_config = {"from_attributes": True}

    provider: str
    enabled: bool
    requires_auth: bool
    token_configured: bool
    live: bool
    externally_managed: bool = False
    last_success_at: datetime | None
    last_error_at: datetime | None
    last_error_detail: str | None
    rate_limited: bool
    state: Literal[
        "disabled",
        "not_configured",
        "checking",
        "operational",
        "temporary_unavailable",
        "invalid_credentials",
    ]
    last_checked_at: datetime | None


class ProviderStatusListOut(BaseModel):
    items: Sequence[ProviderStatusOut]
