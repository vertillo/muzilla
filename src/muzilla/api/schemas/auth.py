from __future__ import annotations

from pydantic import BaseModel


class LoginRequest(BaseModel):
    password: str


class AuthStatusOut(BaseModel):
    enabled: bool
    authenticated: bool
    csrf_token: str
