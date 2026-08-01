"""Normalized provider failure taxonomy.

Adapters raise these errors instead of leaking transport-specific exceptions to
job handlers.  ``None`` remains the protocol value for an ordinary missing
resource (for example LRCLIB HTTP 404), while failures carry whether retrying
the individual item is meaningful.
"""

from __future__ import annotations


class ProviderError(RuntimeError):
    outcome = "permanent_error"
    retryable = False


class ProviderNotFound(ProviderError):
    outcome = "not_found"


class ProviderTransientError(ProviderError):
    outcome = "transient_error"
    retryable = True


class ProviderPermanentError(ProviderError):
    outcome = "permanent_error"


__all__ = [
    "ProviderError",
    "ProviderNotFound",
    "ProviderPermanentError",
    "ProviderTransientError",
]
