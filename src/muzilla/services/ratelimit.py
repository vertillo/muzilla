"""In-process fixed-window rate limiter.

Built for POST /api/auth/login (docs/PLAN.md §12c, step 2.2) but kept
generic and dependency-free so a future limited endpoint — or a CLI
path, per the layering contract (api/cli import services, never the
reverse) — can reuse it. Single-process, in-memory: sufficient for this
project's single-container, single-worker deployment model; a
multi-instance deployment would need a shared store instead.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class _Window:
    count: int
    window_start: float


@dataclass
class FixedWindowLimiter:
    """`limit` attempts per `window_seconds`, keyed by an arbitrary string
    (a client IP in the login case). A window resets by starting a new
    one lazily on the first request after the old one expires, rather
    than a background sweep — nothing to schedule, and idle keys cost
    nothing between windows."""

    limit: int
    window_seconds: float
    _windows: dict[str, _Window] = field(default_factory=dict)

    def check(self, key: str, *, now: float | None = None) -> float | None:
        """Returns None if the request is allowed (and records it), or
        the number of seconds until the caller should retry."""
        current = now if now is not None else time.monotonic()
        window = self._windows.get(key)

        if window is None or current - window.window_start >= self.window_seconds:
            self._windows[key] = _Window(count=1, window_start=current)
            return None

        if window.count >= self.limit:
            return self.window_seconds - (current - window.window_start)

        window.count += 1
        return None

    def reset(self, key: str) -> None:
        self._windows.pop(key, None)
