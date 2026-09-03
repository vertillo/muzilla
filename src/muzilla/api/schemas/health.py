"""Wire contracts for liveness, readiness and runtime capabilities."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class CapabilityOut(BaseModel):
    name: str
    state: Literal["available", "unavailable", "disabled"]
    enabled: bool
    available: bool
    detail: str


class CapabilitiesOut(BaseModel):
    replaygain: CapabilityOut
    fingerprint: CapabilityOut


class ReadinessOut(BaseModel):
    status: Literal["ready", "not_ready"]
    capabilities: CapabilitiesOut
