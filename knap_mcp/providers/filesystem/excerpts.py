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
from typing import List

from . import markdown as md

#: How much of the opening to keep. Long enough to tell two notes apart, short
#: enough that a page of fifty of them is not a wall of text.
OPENING_CHARS = 160

#: Lines of prose to draw the opening from. More than a couple, because the first
#: line of a note is often a single short sentence; few enough that the cost is a
#: slice rather than a scan of a long note. Blank lines do not count against it:
#: markdown is written double-spaced, so counting raw lines would have meant two
#: lines of prose on most notes and four on the ones that happen to be dense.
OPENING_LINES = 4

# An ATX heading, mirroring the form `markdown._HEADING_RE` accepts: hashes then
# whitespace then text. Deliberately not `startswith("#")`, which would read a
# note opening on the inline tag `#meeting` as a heading and skip the line.
_HEADING_LINE_RE = re.compile(r"^#{1,6}([ \t]|$)")


def opening_of(note: md.ParsedNote, *, chars: int = OPENING_CHARS) -> str:
    """The lead-in of a note, flattened to one line.

    The frontmatter is already off ``body``. Headings above the prose go too,
    because "# Meeting notes" on a note titled "Meeting notes" tells a client
    nothing that `title` did not already tell it, and a summary whose excerpt
    repeats its own title is the same as having no excerpt. The first heading
    *after* the prose ends it instead: that is where the lead-in stops and the
    note's sections begin, and "## Log" is structure, not a description.

    Code is not content, the rule `markdown.py` states and every scanner beside
    this one follows, so the code-blanked copy decides which lines are prose and
    the real body supplies their text. The two share their line structure by
    construction, the same trick `title_of` uses to quote a heading it matched
    on the copy. Without it a note that opens on a fenced example was described
    to the client as "```".

    Whitespace is collapsed for the same reason it is collapsed around a search
    match: this lands in a listing as one line, and a note that opens with an
    indented list or a table should not arrive full of gaps.
    """
    prose: List[str] = []
    # Not strict: the two are the same length by construction, and if that ever
    # stops being true the scanners that resolve links are the place to hear
    # about it. A listing should not raise over the shape of a summary field.
    for line, scannable in zip(
        note.body.split("\n"), note.body_scannable.split("\n"), strict=False
    ):
        if not scannable.strip():
            continue  # blank, or a line that was nothing but code
        if _HEADING_LINE_RE.match(scannable):
            if prose:
                break
            continue
        prose.append(line)
        if len(prose) >= OPENING_LINES:
            break
    opening = re.sub(r"\s+", " ", " ".join(prose)).strip()
    return opening[:chars] + ("..." if len(opening) > chars else "")


__all__ = ["OPENING_CHARS", "OPENING_LINES", "opening_of"]
