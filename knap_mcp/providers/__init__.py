"""Vault backends.

``protocol`` holds the interface and the value objects; concrete backends live in
their own subpackage and are the only code that knows about a transport (the
filesystem, for the one backend v1 ships). ``factory`` picks one from config.

Re-exported here so the tool layer imports from ``..providers`` and never reaches
into a backend package.
"""

from .protocol import (
    AttachmentPayload,
    FolderInfo,
    LinkRef,
    MoveResult,
    NoteDetail,
    NoteExistsError,
    NoteNotFoundError,
    NoteRef,
    NoteSummary,
    PatchMode,
    PathNotAllowedError,
    PeriodicKind,
    ProviderError,
    QuotaExceeded,
    RevisionMismatch,
    TagCount,
    VaultInfo,
    VaultNotFoundError,
    VaultProvider,
    WriteMode,
)

__all__ = [
    "AttachmentPayload",
    "FolderInfo",
    "LinkRef",
    "MoveResult",
    "NoteDetail",
    "NoteExistsError",
    "NoteNotFoundError",
    "NoteRef",
    "NoteSummary",
    "PatchMode",
    "PathNotAllowedError",
    "PeriodicKind",
    "ProviderError",
    "QuotaExceeded",
    "RevisionMismatch",
    "TagCount",
    "VaultInfo",
    "VaultNotFoundError",
    "VaultProvider",
    "WriteMode",
]
