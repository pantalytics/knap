"""The vault index: what is in it, what links to what, and what a link means.

Two jobs, and the second one is the product.

**Cheap freshness.** Obsidian edits the vault under us, so the index cannot be
built once and trusted. It also cannot be rebuilt per call: a ten thousand note
vault is a few seconds of reading. So a refresh walks directory entries without
opening a single file (a `scandir` tree, milliseconds) and re-parses only the
notes whose size or mtime moved. Deletions fall out of the same walk.

**Obsidian's link resolution, not ours.** `[[Note]]` is not a path. It resolves,
in Obsidian's order: an exact path match, then the note's `aliases:` frontmatter,
then the shortest unique basename, then a sibling of the note the link is in.
Getting this wrong does not raise; it silently returns a different note, or
breaks the graph on a move. That is why it lives here with the data it needs
rather than being guessed at the call site.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from . import excerpts
from . import markdown as md
from . import paths as vault_paths


@dataclass
class NoteEntry:
    """One note as the index knows it. No body: that is read on demand.

    ``excerpt`` is not a body and not a step towards holding one. It is a couple
    of lines, derived at parse time from a body that was in memory anyway, and
    dropped the moment the note's mtime moves -- the same terms the headings and
    the properties beside it are held on.
    """

    path: str
    rev: str
    size: int
    mtime_ns: int
    title: str
    tags: List[str] = field(default_factory=list)
    aliases: List[str] = field(default_factory=list)
    headings: List[str] = field(default_factory=list)
    properties: Dict[str, Any] = field(default_factory=dict)
    #: Link targets exactly as written in the note, in document order.
    link_targets: List[str] = field(default_factory=list)
    #: The note's opening prose, for a listing that has no match to quote.
    excerpt: str = ""

    @property
    def basename(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def stem(self) -> str:
        name = self.basename
        return name[:-3] if name.lower().endswith(".md") else name

    @property
    def folder(self) -> str:
        return self.path.rsplit("/", 1)[0] if "/" in self.path else ""


class VaultIndex:
    """Lazy, mtime-invalidated index over a vault directory.

    Not thread-safe on purpose: the tool layer serializes calls to one provider
    with a lock (``tools/_common.run_blocking``), so paying for locking twice
    would buy nothing.
    """

    #: Do not re-walk more often than this. A burst of tool calls in one
    #: conversation turn should cost one walk, not one per call.
    REFRESH_INTERVAL_S = 1.0

    def __init__(self, root: Path):
        self.root = root.resolve()
        self._notes: Dict[str, NoteEntry] = {}
        self._last_walk: float = 0.0
        self._loaded = False
        # Rebuilt on any change, because a single new note can change what the
        # shortest unique basename for an existing one is.
        self._by_stem: Dict[str, List[str]] = {}
        self._by_path_key: Dict[str, str] = {}
        self._by_alias: Dict[str, str] = {}
        self._backlinks: Dict[str, List[str]] = {}
        # The non-note half of the vault, and it is deliberately kept in its own
        # pair of structures rather than mixed into the four above. A note must
        # keep winning every lookup it wins today: attachments are consulted
        # only where nothing else matched, so nothing that resolves now can
        # start resolving somewhere else because somebody dropped in a PNG.
        self._assets: set[str] = set()
        self._assets_by_key: Dict[str, str] = {}

    # -- freshness ---------------------------------------------------------- #

    def refresh(self, *, force: bool = False) -> None:
        """Bring the index up to date with the filesystem.

        ``force`` skips the interval, and is what a write path uses: having just
        changed a note ourselves, we know the index is stale regardless of when
        the last walk was.
        """
        now = time.monotonic()
        if self._loaded and not force and (now - self._last_walk) < self.REFRESH_INTERVAL_S:
            return

        seen: Dict[str, Tuple[int, int]] = {}
        # Hidden notes are indexed, not skipped. The alternative -- walking with
        # include_hidden=False -- meant `include_hidden=True` on a search or a
        # listing had nothing to find, because the notes were never in the index
        # to begin with. So the walk takes everything and the *lookups* below
        # decide what a hidden note may take part in: not link resolution, not
        # backlinks, not tag counts, because a link must not resolve into
        # `.trash` and a deleted note's tags are not the vault's tags.
        root_resolved = self.root.resolve()
        for absolute in vault_paths.walk_notes(self.root, include_hidden=True):
            try:
                stat = absolute.stat()
            except OSError:
                continue
            rel = vault_paths.relative_to_walked_root(root_resolved, absolute)
            seen[rel] = (stat.st_size, stat.st_mtime_ns)

        changed = False
        for rel, (size, mtime_ns) in seen.items():
            entry = self._notes.get(rel)
            if entry is not None and entry.size == size and entry.mtime_ns == mtime_ns:
                continue
            parsed = self._parse_note(rel)
            if parsed is not None:
                self._notes[rel] = parsed
                changed = True

        for rel in [rel for rel in self._notes if rel not in seen]:
            del self._notes[rel]
            changed = True

        # Attachments are names, not content: nothing is parsed and no mtime is
        # tracked, because the only question ever asked of one is whether a link
        # target means it. Editing an image cannot change that answer.
        assets = {
            vault_paths.relative_to_walked_root(root_resolved, absolute)
            for absolute in vault_paths.walk_attachments(self.root, include_hidden=True)
        }
        if assets != self._assets:
            self._assets = assets
            changed = True

        self._last_walk = now
        self._loaded = True
        if changed or not self._by_path_key:
            self._rebuild_lookups()

    def invalidate(self, rel: str) -> None:
        """Forget one note, so the next refresh re-reads it.

        Used after our own write: the file's mtime did move, but forcing a walk
        for one known path is wasteful, and leaving a stale entry in place means
        the next search misses the words we just wrote.
        """
        self._notes.pop(rel, None)
        self._last_walk = 0.0

    def _parse_note(self, rel: str) -> Optional[NoteEntry]:
        absolute = self.root / rel
        try:
            text, rev, size, mtime_ns = md.read_text(absolute)
        except OSError:
            return None
        note = md.parse(text)
        aliases = _as_list(note.frontmatter.get("aliases") or note.frontmatter.get("alias"))
        return NoteEntry(
            path=rel,
            rev=rev,
            size=size,
            mtime_ns=mtime_ns,
            title=md.title_of(rel, note),
            tags=md.tags_of(note),
            aliases=aliases,
            headings=md.headings_of(note),
            properties=dict(note.frontmatter),
            link_targets=[link.target for link in md.raw_links_of(note)],
            # Derived here rather than when a listing asks for it, because here
            # the body is already in hand. Read on demand it would cost one file
            # open per note listed, and `backlinks` is not paginated: the MOC that
            # the whole vault links to would open the whole vault.
            excerpt=excerpts.opening_of(note),
        )

    def _rebuild_lookups(self) -> None:
        self._by_stem = {}
        self._by_path_key = {}
        self._by_alias = {}
        self._backlinks = {}
        self._assets_by_key = {}

        # Shallowest first, so two files of the same name in different folders
        # resolve to the one nearer the root -- Obsidian's tie-break, and the
        # same one _by_stem is sorted by below.
        for rel in sorted(self._assets, key=lambda p: (p.count("/"), len(p), p)):
            if vault_paths.is_hidden(rel):
                continue  # an embed must not resolve into .trash or .obsidian
            self._assets_by_key.setdefault(rel.lower(), rel)
            self._assets_by_key.setdefault(rel.rsplit("/", 1)[-1].lower(), rel)

        for rel, entry in self._notes.items():
            if vault_paths.is_hidden(rel):
                # In the index so `include_hidden` can find it, out of every
                # lookup so nothing links into `.trash` or counts its tags.
                continue
            self._by_stem.setdefault(entry.stem.lower(), []).append(rel)
            # Both spellings of a path link, since Obsidian accepts either.
            self._by_path_key[rel.lower()] = rel
            self._by_path_key[rel[:-3].lower() if vault_paths.is_note(rel) else rel.lower()] = rel
            for alias in entry.aliases:
                # First writer wins: two notes claiming one alias is the vault's
                # ambiguity, and silently preferring whichever we indexed last
                # would make resolution depend on directory order.
                self._by_alias.setdefault(alias.strip().lower(), rel)

        for paths in self._by_stem.values():
            paths.sort(key=lambda rel: (rel.count("/"), len(rel), rel))

        # `seen` shadows `_backlinks` purely so the duplicate check is a hash
        # lookup. It was `rel not in holders` on the list, which is a scan, and
        # the note that suffers is the one every vault has: the MOC or index
        # note the whole vault links to, whose holder list is as long as the
        # vault. Same order out, same list type, one dict thrown away at the end.
        seen: Dict[str, Set[str]] = {}
        for rel, entry in self._notes.items():
            if vault_paths.is_hidden(rel):
                continue  # a trashed note's links are not backlinks
            for target in entry.link_targets:
                resolved = self.resolve(target, from_path=rel)
                if resolved and resolved != rel:
                    holders = self._backlinks.setdefault(resolved, [])
                    if rel not in seen.setdefault(resolved, set()):
                        seen[resolved].add(rel)
                        holders.append(rel)

    # -- reading ------------------------------------------------------------ #

    def all_notes(self) -> List[NoteEntry]:
        self.refresh()
        return list(self._notes.values())

    def get(self, rel: str) -> Optional[NoteEntry]:
        self.refresh()
        return self._notes.get(rel)

    def folders(self) -> Dict[str, int]:
        """Folder path -> number of notes directly in it."""
        self.refresh()
        counts: Dict[str, int] = {}
        for entry in self._notes.values():
            counts[entry.folder] = counts.get(entry.folder, 0) + 1
            # Ancestors appear even when they hold no notes themselves, so the
            # tree a client sees is navigable rather than a list of leaves.
            folder = entry.folder
            while "/" in folder:
                folder = folder.rsplit("/", 1)[0]
                counts.setdefault(folder, 0)
        return counts

    def tag_counts(self, prefix: str = "") -> List[Tuple[str, int]]:
        """Tag -> note count, most used first.

        A nested tag counts for its parents too: ``#project/acme`` is a
        ``#project``, so asking for ``project`` finds it. Without that, a vault
        that organises by nesting looks like it has no tags at all.
        """
        self.refresh()
        wanted = prefix.strip().lstrip("#").lower()
        counts: Dict[str, int] = {}
        for entry in self._notes.values():
            if vault_paths.is_hidden(entry.path):
                continue  # a deleted note's tags are not the vault's tags
            expanded = set()
            for tag in entry.tags:
                parts = tag.split("/")
                for depth in range(1, len(parts) + 1):
                    expanded.add("/".join(parts[:depth]))
            for tag in expanded:
                if wanted and not tag.lower().startswith(wanted):
                    continue
                counts[tag] = counts.get(tag, 0) + 1
        return sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))

    def backlinks(self, rel: str) -> List[NoteEntry]:
        self.refresh()
        return [self._notes[p] for p in self._backlinks.get(rel, []) if p in self._notes]

    def notes_linking_to(self, rel: str) -> List[str]:
        """Paths of notes with a link resolving to ``rel``. What a move rewrites."""
        self.refresh()
        return list(self._backlinks.get(rel, []))

    # -- resolution --------------------------------------------------------- #

    def resolve(self, target: str, *, from_path: str = "") -> Optional[str]:
        """Resolve a link target to a note path, Obsidian's way.

        The order is Obsidian's and the reason for each step is a real vault:

        1. **An explicit path**, meaning a target containing "/", with or without
           ``.md``. A vault that writes full paths means them, and if that path
           is gone the link is broken rather than quietly re-pointed at whatever
           note shares its basename. ``[[Projects/index]]`` after
           ``Projects/index.md`` is deleted must not silently become the
           ``index.md`` at the root: Obsidian draws it as unresolved and so do
           we.
        2. **A sibling of the linking note.** Checked before the root-level
           match, because ``[[index]]`` written inside ``Projects/`` means
           ``Projects/index.md``, not the ``index.md`` at the vault root. Doing
           the exact-key lookup first, which is what this used to do, made every
           bare link in a subfolder jump to the root note of that name.
        3. **An alias.** A note titled ``2026-Q1 Board Meeting`` with
           ``aliases: [Q1 board]`` is linked to as ``[[Q1 board]]``, and a
           resolver that skips aliases reports that link as broken. After
           siblings, so a real file next door beats another note's nickname.
        4. **Shortest unique basename**, shallowest first, which is Obsidian's
           tie-break.
        """
        if not target:
            return None
        self.refresh()
        needle = target.strip().strip("/")
        if not needle:
            return None
        lowered = needle.lower()

        if "/" in lowered:
            return self._by_path_key.get(lowered) or self._assets_by_key.get(lowered)

        stem = lowered[:-3] if lowered.endswith(".md") else lowered

        if from_path and "/" in from_path:
            folder = from_path.rsplit("/", 1)[0]
            sibling = self._by_path_key.get(f"{folder.lower()}/{stem}")
            if sibling:
                return sibling
            sibling = self._assets_by_key.get(f"{folder.lower()}/{lowered}")
            if sibling:
                return sibling

        alias = self._by_alias.get(lowered)
        if alias:
            return alias

        direct = self._by_path_key.get(stem)
        if direct:
            return direct

        candidates = self._by_stem.get(stem)
        if candidates:
            # Sorted shallowest-then-shortest in _rebuild_lookups.
            return candidates[0]

        # Last, and only here: an attachment. `![[diagram.png]]` carries its
        # extension, so it never reaches this point looking like a note.
        return self._assets_by_key.get(lowered)

    def shortest_unique_form(self, rel: str) -> str:
        """How a link to this note should be written after a move.

        Obsidian writes the basename when it is unique in the vault and the full
        path when it is not, and it drops the ``.md``. Matching that matters
        because the alternative is a move that turns every tidy ``[[Meeting
        notes]]`` into ``[[Areas/Work/Meetings/Meeting notes]]`` -- correct,
        graph intact, and a diff across two hundred notes that nobody wants.
        """
        self.refresh()
        entry = self._notes.get(rel)
        stem = entry.stem if entry else rel.rsplit("/", 1)[-1].removesuffix(".md")
        holders = self._by_stem.get(stem.lower(), [])
        if len(holders) <= 1:
            return stem
        return rel[:-3] if vault_paths.is_note(rel) else rel

    # -- write-side bookkeeping --------------------------------------------- #

    def note_paths(self) -> Iterable[str]:
        self.refresh()
        return self._notes.keys()

    def exists(self, rel: str) -> bool:
        return (self.root / vault_paths.normalize(rel)).exists()


def _as_list(value: Any) -> List[str]:
    """Frontmatter that may be a scalar, a list, or a comma separated string."""
    if value is None:
        return []
    if isinstance(value, str):
        return [piece.strip() for piece in value.split(",") if piece.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]
