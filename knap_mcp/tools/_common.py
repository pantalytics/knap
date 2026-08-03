"""Shared plumbing for the tool layer.

``run_blocking`` is the important one. Filesystem calls block, and an MCP server
is an asyncio application, so every provider call goes to a thread. It also holds
a per-provider lock, which is not about thread safety of the standard library but
about the index: two concurrent calls to one provider would refresh and mutate
the same in-memory index, and the cheapest correct answer is to let one finish.

``_current_sub`` is the contextvar the hosted package sets per request. Here it is
always ``"stdio"``. It exists in the public package so the tool bodies that read
it are the same code in both.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import Any, Callable, Dict, Optional, TypeVar

from ..error_handling import ValidationError, as_tool_error
from ..logging_config import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

#: The authenticated subject for the current request. Overridden per request by
#: the hosted package; a single constant here.
_current_sub: ContextVar[Optional[str]] = ContextVar("_current_sub", default=None)

_LOCKS: "Dict[int, asyncio.Lock]" = {}


def _lock_for(provider: Any) -> asyncio.Lock:
    key = id(provider)
    lock = _LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[key] = lock
        if len(_LOCKS) > 512:
            # Bounded: a long-lived hosted process resolves a provider per
            # workspace and would otherwise accumulate a lock per vault
            # forever. Dropping a lock nobody is holding is safe; the next call
            # makes a new one.
            for stale in [k for k in list(_LOCKS) if k != key][:256]:
                if not _LOCKS[stale].locked():
                    del _LOCKS[stale]
    return lock


async def run_blocking(provider: Any, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run a blocking provider call off the event loop, serialized per provider."""
    async with _lock_for(provider):
        return await asyncio.to_thread(func, *args, **kwargs)


def clamp_limit(limit: Optional[int], default: int, maximum: int) -> int:
    """Page size a client asked for, brought inside what the server will serve."""
    effective = default if limit is None else limit
    return max(1, min(effective, maximum))


def clamp_offset(offset: Optional[int]) -> int:
    return max(0, offset or 0)


def require_confirm(confirm: bool, action: str, detail: str = "") -> None:
    """Refuse a destructive call that arrived without confirmation."""
    from ..error_handling import ConfirmationRequired

    if not confirm:
        raise ConfirmationRequired(action, detail)


def require_text(value: Optional[str], name: str) -> str:
    if value is None or not str(value).strip():
        raise ValidationError(f"{name} is required")
    return str(value)


__all__ = [
    "_current_sub",
    "as_tool_error",
    "clamp_limit",
    "clamp_offset",
    "logger",
    "require_confirm",
    "require_text",
    "run_blocking",
]
