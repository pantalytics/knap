"""Mapping the protocol's dataclasses onto the wire models.

One place, so a field added to a summary appears identically in a listing, a
search result and a backlink list. Three copies of this drift, and the drift
shows up as a client that can read a note it found one way and not another.
"""

from __future__ import annotations

from typing import List

from mcp.types import ToolAnnotations

from ...providers.protocol import LinkRef, NoteSummary
from ...schemas import LinkResult, NoteSummaryResult

#: Reads. `openWorldHint` is False: a vault is a closed, enumerable world, unlike
#: a mailbox on somebody else's server.
READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)

#: Additive writes. Not destructive, so no confirmation: create, append,
#: patch-section and set-properties cannot lose what was already there, and
#: gating them would teach clients that the confirm prompt is noise.
ADDITIVE_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)

#: Writes that can lose something. Every one of these also demands confirm=true.
DESTRUCTIVE_WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False
)


def summary_result(note: NoteSummary) -> NoteSummaryResult:
    return NoteSummaryResult(
        path=note.path,
        title=note.title,
        rev=note.rev,
        size=note.size,
        modified=note.modified,
        tags=list(note.tags),
        excerpt=note.excerpt,
    )


def link_result(link: LinkRef) -> LinkResult:
    return LinkResult(
        target=link.target,
        resolved_path=link.resolved_path,
        anchor=link.anchor,
        embed=link.embed,
        alias=link.alias,
    )


def link_results(links: List[LinkRef]) -> List[LinkResult]:
    return [link_result(link) for link in links]
