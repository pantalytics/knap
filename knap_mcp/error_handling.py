"""Error types and the sanitizer that decides what a client is told.

A tool error is read out loud to a user by an AI, so a good one says what went
wrong and what to do next in one sentence. `RevisionMismatch` becomes "re-read
the note", not "412".

The sanitizer exists for one reason specific to this product: **an absolute path
is not ours to hand out.** A vault lives at
`/srv/knap/prod/41/second-brain/tree/...` on a hosted deployment, and an
exception that carries that string tells a caller the storage layout, the tenant
id and where to aim the next attempt. Vault-relative paths are fine, they are
what the client passed in. Absolute ones are replaced.
"""

from __future__ import annotations

import re
from typing import Optional

from .providers.protocol import (
    NoteExistsError,
    NoteNotFoundError,
    PathNotAllowedError,
    ProviderError,
    QuotaExceeded,
    RevisionMismatch,
    VaultNotFoundError,
)


class KnapError(Exception):
    """Base for errors raised by the server rather than by a backend."""


class ConfigurationError(KnapError):
    """The server is not configured to run. Raised at startup, not per call."""


class ValidationError(KnapError):
    """The caller's arguments do not make sense. Never retried by a client."""


class ConfirmationRequired(KnapError):
    """A destructive tool was called without ``confirm=true``.

    Its own type so the message can be identical in shape across every
    destructive tool: an AI that learns the phrasing once handles all of them.
    """

    def __init__(self, action: str, detail: str = ""):
        message = (
            f"{action} needs confirm=true. Show the user exactly what will change and get "
            "their approval first, then call again with confirm=true."
        )
        super().__init__(f"{message} {detail}".strip())


# Two patterns, because an absolute path arrives in two shapes and a single
# greedy one gets both wrong.
#
# Quoted is the common case: the standard library writes
# "No such file or directory: '/srv/knap/prod/41/vault/note.md'". The quotes are
# the boundary, so the whole path goes even when the filename contains spaces --
# and note filenames usually do.
_QUOTED_PATH_RE = re.compile(r"""(['"])((?:/|[A-Za-z]:[\\/])[^'"\n]*)\1""")

# Bare is the rest. Two rules earn their place. The lookbehind means the leading
# "/" must start a token, so a *vault-relative* path is left alone --
# "Areas/Work/Acme.md" is what the client passed in and blanking it makes every
# error useless. And spaces are excluded, because a class that included them ate
# the rest of the sentence: "Areas/Work/Acme.md does not exist" came out as
# "Areas<path>".
_BARE_PATH_RE = re.compile(
    r"""(?<![\w.'"])(?:/[\w.@+~-]+){2,}/?|(?<![\w:])[A-Za-z]:[\\/][\w\\/.@+~-]+"""
)

_HOME_RE = re.compile(r"/(?:home|Users|root)/[^/\s]+")


def sanitize(message: str) -> str:
    """Strip absolute paths out of an error message.

    Applied to everything that leaves the server as an error, including messages
    from the standard library: an ``OSError`` from ``open()`` carries the full
    path it failed on, and that is the most common way this leaks.

    Vault-relative paths are deliberately kept. They are what the caller handed
    in, so they tell them nothing they did not already know, and removing them
    would leave an AI reporting "a note could not be found" with no way to say
    which.
    """
    if not message:
        return message
    cleaned = _HOME_RE.sub("<home>", message)
    cleaned = _QUOTED_PATH_RE.sub("<path>", cleaned)
    return _BARE_PATH_RE.sub("<path>", cleaned)


def describe(exc: BaseException) -> str:
    """The sentence a client sees for an exception.

    Every branch is here rather than at the raise sites so the wording is
    consistent, and so adding a backend cannot accidentally invent a new dialect
    of the same error.
    """
    if isinstance(exc, RevisionMismatch):
        return (
            f"{exc.path} changed since you read it, so the write was refused rather than "
            "overwriting somebody's edit. Read the note again and rebuild your change on "
            "what is there now. Do not retry without expected_rev."
        )
    if isinstance(exc, NoteNotFoundError):
        return sanitize(str(exc)) or "That note does not exist."
    if isinstance(exc, NoteExistsError):
        return sanitize(str(exc))
    if isinstance(exc, PathNotAllowedError):
        # The provider's message already avoids echoing the path. Sanitized
        # anyway, because defence in depth is cheap and this is the one class of
        # error where a leak matters.
        return sanitize(str(exc))
    if isinstance(exc, QuotaExceeded):
        return sanitize(str(exc))
    if isinstance(exc, VaultNotFoundError):
        return sanitize(str(exc)) or "That vault is not available."
    if isinstance(exc, (ValidationError, ConfirmationRequired, ConfigurationError)):
        return sanitize(str(exc))
    if isinstance(exc, ProviderError):
        return sanitize(str(exc)) or "The vault could not complete that operation."
    if isinstance(exc, PermissionError):
        return "The vault refused that operation: permission denied."
    if isinstance(exc, OSError):
        return f"The vault could not complete that operation: {sanitize(str(exc))}"
    return sanitize(str(exc)) or exc.__class__.__name__


def error_type(exc: BaseException) -> str:
    """A stable label for analytics. Never carries a path or any content."""
    return exc.__class__.__name__


class ToolError(KnapError):
    """What a tool raises after any failure, carrying a client-safe message."""

    def __init__(self, exc: BaseException, *, tool: str = ""):
        self.original = exc
        self.tool = tool
        self.error_type = error_type(exc)
        super().__init__(describe(exc))


def as_tool_error(exc: BaseException, *, tool: str = "") -> ToolError:
    if isinstance(exc, ToolError):
        return exc
    return ToolError(exc, tool=tool)


def optional_int(value: Optional[int], *, name: str, minimum: int = 0) -> Optional[int]:
    """Validate an optional integer argument from a client."""
    if value is None:
        return None
    if value < minimum:
        raise ValidationError(f"{name} must be at least {minimum}")
    return value
