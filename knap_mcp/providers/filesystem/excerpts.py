"""The opening of a note: what a listing shows when there is no match to quote.

A search hit can quote the text around the query. A listing has no query, so the
only honest thing to show is how the note starts, and both `protocol.NoteSummary`
and `schemas.NoteSummaryResult` promise exactly that.

It sits in its own module because the two callers that need it are on opposite
sides of the backend and neither owns it: `index.py` derives it while it has the
note parsed anyway, and `search.py` hands it back for a filter-only query. The
obvious third home, `markdown.py` next to the other `*_of(note)` scanners, is 18
lines from the 500-line budget, and this is not the change that should spend them.
"""

from __future__ import annotations

import re

from . import markdown as md

#: How much of the opening to keep. Long enough to tell two notes apart, short
#: enough that a page of fifty of them is not a wall of text.
OPENING_CHARS = 160

#: Lines of prose to draw the opening from. More than a couple, because the first
#: line of a note is often a single short sentence; few enough that the cost is a
#: slice rather than a scan of a long note.
OPENING_LINES = 4


def opening_of(note: md.ParsedNote, *, chars: int = OPENING_CHARS) -> str:
    """The first prose of a note, flattened to one line.

    The frontmatter is already off ``body``. Leading headings go too, because
    "# Meeting notes" on a note titled "Meeting notes" tells a client nothing
    that `title` did not already tell it, and a summary whose excerpt repeats its
    own title is the same as having no excerpt.

    Whitespace is collapsed for the same reason it is collapsed around a search
    match: this lands in a listing as one line, and a note that opens with an
    indented list or a table should not arrive full of gaps.
    """
    lines = note.body.strip().split("\n")
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("#")):
        lines.pop(0)
    opening = re.sub(r"\s+", " ", " ".join(lines[:OPENING_LINES])).strip()
    return opening[:chars] + ("..." if len(opening) > chars else "")


__all__ = ["OPENING_CHARS", "OPENING_LINES", "opening_of"]
