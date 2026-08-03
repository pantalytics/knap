"""Usage-tracking stub.

A no-op so the public package works standalone with no analytics dependency. The
real tracker lives in the private admin package (``usage.install_usage_logging``
plus a PostHog client), which is also where the per-workspace consent switch
decides whether anything is recorded at all.

One rule the admin implementation inherits from here and must not relax: an event
never carries a note path or note content. ``Clients/Acme/2026 renewal.md`` is a
fact about somebody's business, so a path is hashed or dropped. The useful pair
for diagnosing a failure is the tool name and the error type.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def track_event(
    event: str,
    *,
    subject: Optional[str] = None,
    properties: Optional[Dict[str, Any]] = None,
) -> None:
    """Record a usage event. No-op in the public package."""
