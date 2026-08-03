"""Search over a vault: text, tags, frontmatter properties, dates.

The index answers everything except full text, because tags, properties and
titles are already in memory. Only a free-text query opens files, and it opens
them in index order with a cap, so a query against a vault of thousands of notes
is bounded work that reports its own limit rather than a scan that looks like a
hang.

Ordering is newest first, always. Relevance ranking would sometimes be better
and would make two identical queries answer differently as a vault grows, and
`knowledge.py` promises the client newest first.

This is also the module that stands in for Dataview. We do not evaluate Dataview
or Bases, so `prop` / `prop_value` is the supported way to ask "every note where
status is active", which is most of what people reach for Dataview to do.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Tuple

from . import markdown as md
from . import paths as vault_paths
from .index import NoteEntry, VaultIndex

#: Characters of context either side of a match in an excerpt.
EXCERPT_RADIUS = 90


@dataclass
class Hit:
    entry: NoteEntry
    excerpt: str


class VaultSearch:
    def __init__(self, root: Path, index: VaultIndex, *, max_scan_notes: int = 20000):
        self.root = root
        self.index = index
        self.max_scan_notes = max_scan_notes
        #: True when the last query stopped at the cap. Reported to the caller so
        #: a partial answer is never presented as a complete one.
        self.truncated_scan = False

    def query(
        self,
        text: Optional[str] = None,
        *,
        folder: str = "",
        tag: Optional[str] = None,
        prop: Optional[str] = None,
        prop_value: Optional[str] = None,
        since: Optional[str] = None,
        include_hidden: bool = False,
        limit: int = 25,
        offset: int = 0,
    ) -> Tuple[List[Hit], int]:
        """Run a search. Returns (page of hits, total matching).

        ``total`` is matches, not notes scanned. A client showing "12 of 340"
        needs the first number to mean something.
        """
        self.truncated_scan = False
        entries = self.index.all_notes()

        folder_prefix = vault_paths.normalize(folder).rstrip("/") + "/" if folder.strip() else ""
        since_ns = _since_to_ns(since)
        needle = (text or "").strip().lower()
        wanted_tag = tag.strip().lstrip("#").lower() if tag else None

        # Cheap filters first, so the expensive one runs over as few notes as
        # possible. Ordering the pipeline this way is the difference between
        # opening 30 files and opening 10,000.
        candidates: List[NoteEntry] = []
        for entry in entries:
            if not include_hidden and vault_paths.is_hidden(entry.path):
                continue
            if folder_prefix and not entry.path.startswith(folder_prefix):
                continue
            if since_ns is not None and entry.mtime_ns < since_ns:
                continue
            if wanted_tag is not None and not _has_tag(entry, wanted_tag):
                continue
            if prop is not None and not _has_property(entry, prop, prop_value):
                continue
            candidates.append(entry)

        candidates.sort(key=lambda item: item.mtime_ns, reverse=True)

        if not needle:
            page = candidates[offset : offset + limit]
            return [Hit(entry=entry, excerpt=_opening(self.root, entry)) for entry in page], len(
                candidates
            )

        hits: List[Hit] = []
        scanned = 0
        for entry in candidates:
            if scanned >= self.max_scan_notes:
                self.truncated_scan = True
                break
            scanned += 1
            excerpt = self._match(entry, needle)
            if excerpt is not None:
                hits.append(Hit(entry=entry, excerpt=excerpt))

        page = hits[offset : offset + limit]
        return page, len(hits)

    def _match(self, entry: NoteEntry, needle: str) -> Optional[str]:
        """Match a note against free text, returning an excerpt or None.

        The title and the frontmatter are checked before the file is opened,
        which is what makes a title search over a large vault cost no reads at
        all.
        """
        if needle in entry.title.lower():
            return _excerpt_from(entry.title, needle) or entry.title
        if needle in entry.path.lower():
            return entry.path
        for alias in entry.aliases:
            if needle in alias.lower():
                return f"alias: {alias}"
        for key, value in entry.properties.items():
            if needle in str(key).lower() or needle in str(value).lower():
                return f"{key}: {value}"

        try:
            text, _, _, _ = md.read_text(self.root / entry.path)
        except OSError:
            return None
        note = md.parse(text)
        # Searched against the code-stripped copy so a hit inside a fenced block
        # does not surface, but the excerpt is cut from the real body: offsets
        # are shared between the two by construction.
        lowered = note.body_scannable.lower()
        position = lowered.find(needle)
        if position == -1:
            return None
        return _window(note.body, position, len(needle))

    def _opening(self, entry: NoteEntry) -> str:
        return _opening(self.root, entry)


def _opening(root: Path, entry: NoteEntry, chars: int = 160) -> str:
    """The first prose of a note, for a listing with no search term.

    Skips the frontmatter and the leading H1, because "# Meeting notes" under a
    note called "Meeting notes" tells a client nothing it does not have.
    """
    try:
        text, _, _, _ = md.read_text(root / entry.path)
    except OSError:
        return ""
    body = md.parse(text).body.strip()
    lines = [line for line in body.split("\n")]
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("#")):
        lines.pop(0)
    opening = " ".join(line.strip() for line in lines[:4]).strip()
    return opening[:chars] + ("..." if len(opening) > chars else "")


def _window(body: str, position: int, length: int) -> str:
    start = max(0, position - EXCERPT_RADIUS)
    end = min(len(body), position + length + EXCERPT_RADIUS)
    fragment = body[start:end].replace("\n", " ").strip()
    fragment = re.sub(r"\s+", " ", fragment)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(body) else ""
    return f"{prefix}{fragment}{suffix}"


def _excerpt_from(text: str, needle: str) -> Optional[str]:
    position = text.lower().find(needle)
    if position == -1:
        return None
    return _window(text, position, len(needle))


def _has_tag(entry: NoteEntry, wanted: str) -> bool:
    """Tag match, honouring nesting: 'project' matches '#project/acme'."""
    for tag in entry.tags:
        lowered = tag.lower()
        if lowered == wanted or lowered.startswith(wanted + "/"):
            return True
    return False


def _has_property(entry: NoteEntry, key: str, value: Optional[str]) -> bool:
    """Frontmatter filter. Without a value this asks whether the key is present.

    "Does this note have a ``due`` property at all" is a real question and a
    different one from "is ``due`` equal to something", so an absent ``value``
    means presence rather than an empty-string comparison.
    """
    if key not in entry.properties:
        return False
    if value is None:
        return True
    actual = entry.properties[key]
    wanted = value.strip().lower()
    if isinstance(actual, list):
        return any(str(item).strip().lower() == wanted for item in actual)
    return _stringify(actual).strip().lower() == wanted


def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _since_to_ns(since: Optional[str]) -> Optional[int]:
    """ISO date or datetime to an mtime bound in nanoseconds.

    A bad date is refused rather than ignored. Silently dropping the filter
    would answer a question the client did not ask, and it would look like the
    vault has notes it does not.
    """
    if not since or not since.strip():
        return None
    raw = since.strip()
    try:
        if len(raw) == 10:
            parsed = datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
        else:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        raise ValueError(f"since must be an ISO date (YYYY-MM-DD), got {raw!r}") from None
    return int(parsed.timestamp() * 1_000_000_000)
