"""Pydantic result models: what a tool hands back to an MCP client.

Separate from the protocol's dataclasses on purpose. Those are transport-neutral
so a backend depends on nothing above it; these are the wire shape, and they
carry things a backend has no business knowing about, like whether a search hit
its scan cap or which of several vaults answered.

Field descriptions are part of the product. They arrive in the client's tool
schema, so this is where a model learns that ``rev`` has to come back on an
overwrite and that ``relinked`` is worth telling the user about.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class VaultRef(BaseModel):
    id: str = Field(description="Pass this as the `vault` argument on any tool.")
    name: str
    default: bool = Field(description="Used when the `vault` argument is omitted.")
    note_count: Optional[int] = None
    size_bytes: Optional[int] = None


class VaultList(BaseModel):
    vaults: List[VaultRef]
    total: int


class FolderRef(BaseModel):
    path: str = Field(description="Vault-relative, '/' separated, no leading slash.")
    note_count: int = Field(description="Notes directly in this folder, not in its subfolders.")


class FolderList(BaseModel):
    folders: List[FolderRef]
    total: int
    vault: str


class NoteRefResult(BaseModel):
    """What a write hands back."""

    path: str
    rev: str = Field(
        description=(
            "Revision marker. Keep it: a later body-replacing write must pass it back as "
            "expected_rev, and a mismatch means somebody edited the note in Obsidian in "
            "the meantime."
        )
    )
    size: int
    modified: Optional[str] = None
    vault: str = ""
    created: bool = Field(
        default=False, description="True when this call created the note rather than changing it."
    )


class NoteSummaryResult(BaseModel):
    path: str
    title: str
    rev: str
    size: int
    modified: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    excerpt: str = Field(
        default="", description="Text around the match, or the note's opening for a listing."
    )


class NoteList(BaseModel):
    notes: List[NoteSummaryResult]
    total: int = Field(description="Notes matching in total, not the size of this page.")
    limit: int
    offset: int
    folder: str = ""
    vault: str = ""


class SearchResult(BaseModel):
    notes: List[NoteSummaryResult]
    total: int = Field(description="Notes matching in total, not the size of this page.")
    limit: int
    offset: int
    vault: str = ""
    scan_truncated: bool = Field(
        default=False,
        description=(
            "True when the vault was too large to search exhaustively and the scan stopped "
            "early. Say so rather than presenting the result as complete."
        ),
    )


class LinkResult(BaseModel):
    target: str = Field(description="The link text as written in the note.")
    resolved_path: Optional[str] = Field(
        default=None,
        description=(
            "The note it points at, or null when nothing matches. A null is not an error: "
            "an unresolved link is how a vault records an intention."
        ),
    )
    anchor: str = ""
    embed: bool = False
    alias: str = ""


class NoteContent(BaseModel):
    path: str
    title: str
    rev: str
    size: int
    modified: Optional[str] = None
    frontmatter: Dict[str, Any] = Field(default_factory=dict)
    body: str
    body_length: int = Field(description="Full length of the body, even when truncated.")
    truncated: bool = Field(
        description="True when the body was cut short. Page the rest with vault_read_chunk."
    )
    tags: List[str] = Field(default_factory=list)
    links: List[LinkResult] = Field(default_factory=list)
    headings: List[str] = Field(
        default_factory=list,
        description="Pass one of these as `heading` to vault_patch_section.",
    )
    vault: str = ""


class ChunkResult(BaseModel):
    path: str
    text: str
    offset: int
    length: int
    body_length: int
    rev: str = Field(description="Compare across chunks: a change means the note moved under you.")
    vault: str = ""


class BacklinkResult(BaseModel):
    path: str
    notes: List[NoteSummaryResult]
    total: int
    vault: str = ""


class OutgoingLinkResult(BaseModel):
    path: str
    links: List[LinkResult]
    total: int
    unresolved: int = Field(description="How many point at a note that does not exist.")
    vault: str = ""


class TagResult(BaseModel):
    tag: str
    count: int


class TagList(BaseModel):
    tags: List[TagResult]
    total: int
    vault: str = ""


class MoveResultModel(BaseModel):
    path: str
    destination: str
    rev: str
    relinked: List[str] = Field(
        default_factory=list,
        description=(
            "Notes whose links were rewritten to follow the move. Tell the user how many "
            "notes were touched: it is the part they cannot see."
        ),
    )
    vault: str = ""


class DeleteResult(BaseModel):
    path: str
    deleted: bool
    detail: str = Field(
        default="",
        description="Where the note went, so the user can be told it is recoverable.",
    )
    vault: str = ""


class PeriodicNoteResult(BaseModel):
    path: str
    kind: str
    date: str
    exists: bool
    created: bool
    vault: str = ""


class AttachmentResult(BaseModel):
    path: str
    content_type: str
    size: int
    content_base64: str
    vault: str = ""
