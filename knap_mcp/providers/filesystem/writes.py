"""The write half of the filesystem backend: create, patch, properties, move, delete.

A mixin rather than a module of functions, and split out of ``provider.py`` for
the same reason the tools are split: the 500-line budget, and because these five
methods share one set of rules that reads better in one place.

The rules, all three enforced here rather than a layer up:

* ``expected_rev`` is checked against what is on disk before a body is replaced,
  because the customer's own edit in Obsidian is the one we do not get to lose;
* every write goes through ``markdown.atomic_write``, because a half-written note
  is indistinguishable from data loss to Obsidian's watcher, to git and to the
  LiveSync bridge;
* a move rewrites the links that pointed at the old path *before* it reports
  success, and reports which notes it touched.

It reaches for ``self._root``, ``self.index``, ``self._note_path``,
``self._check_rev``, ``self._compose`` and ``self._link_forms``, all of which
``FilesystemVaultProvider`` provides.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from ...logging_config import get_logger
from ..protocol import (
    MoveResult,
    NoteExistsError,
    NoteNotFoundError,
    NoteRef,
    PatchMode,
    ProviderError,
    WriteMode,
)
from . import markdown as md
from . import paths as vault_paths

if TYPE_CHECKING:  # pragma: no cover - annotations only
    from .index import VaultIndex

logger = get_logger(__name__)


def _iso(mtime_ns: int) -> str:
    return datetime.fromtimestamp(mtime_ns / 1_000_000_000, tz=timezone.utc).isoformat()


class WriteOperationsMixin:
    """Everything that changes a note on disk."""

    # Provided by FilesystemVaultProvider; declared so the checker resolves them.
    _root: Path
    index: "VaultIndex"

    def _note_path(self, path: str, *, must_exist: bool = False) -> Tuple[str, Path]:
        raise NotImplementedError

    def _check_rev(
        self,
        rel: str,
        absolute: Path,
        expected_rev: Optional[str],
        *,
        required: bool,
        actual: Optional[str] = None,
    ) -> None:
        raise NotImplementedError

    def _compose(self, frontmatter_raw: str, body: str) -> str:
        raise NotImplementedError

    def _link_forms(self, rel: str) -> List[str]:
        raise NotImplementedError

    # -- write -------------------------------------------------------------- #

    def write(
        self,
        path: str,
        body: str,
        *,
        mode: WriteMode = "create",
        frontmatter: Optional[Dict[str, Any]] = None,
        expected_rev: Optional[str] = None,
    ) -> NoteRef:
        rel, absolute = self._note_path(path)
        exists = absolute.exists()

        if mode == "create" and exists:
            raise NoteExistsError(
                f"{rel} already exists. Use mode 'overwrite' to replace it, "
                "or 'append' to add to it."
            )
        if mode == "overwrite":
            if not exists:
                raise NoteNotFoundError(f"{rel} does not exist")
            self._check_rev(rel, absolute, expected_rev, required=True)

        if exists and mode in ("append", "prepend"):
            current, _, _, _ = md.read_text(absolute)
            parsed = md.parse(current)
            if mode == "append":
                # One blank line between what was there and what is added, and
                # only when the note does not already end in one. An append that
                # glues itself onto the last sentence is a corrupted paragraph.
                separator = "" if parsed.body.endswith("\n\n") or not parsed.body else "\n"
                new_body = f"{parsed.body.rstrip(chr(10))}\n{separator}{body}"
            else:
                new_body = f"{body}\n\n{parsed.body.lstrip(chr(10))}"
            text = self._compose(parsed.frontmatter_raw, new_body)
        else:
            text = self._compose("", body)

        if frontmatter:
            text = md.edit_frontmatter(text, frontmatter)

        vault_paths.ensure_parent(absolute)
        rev, size, mtime_ns = md.atomic_write(absolute, text)
        self.index.invalidate(rel)
        return NoteRef(path=rel, rev=rev, size=size, modified=_iso(mtime_ns))

    def patch_section(
        self,
        path: str,
        heading: str,
        content: str,
        *,
        mode: PatchMode = "replace",
        expected_rev: Optional[str] = None,
    ) -> NoteRef:
        rel, absolute = self._note_path(path, must_exist=True)
        current, current_rev, _, _ = md.read_text(absolute)
        self._check_rev(rel, absolute, expected_rev, required=False, actual=current_rev)

        note = md.parse(current)
        span = md.section_span(note, heading)
        if span is None:
            available = ", ".join(md.headings_of(note)[:12]) or "none"
            raise NoteNotFoundError(
                f"{rel} has no heading {heading!r}. Headings in this note: {available}"
            )
        start, end, _level = span
        existing = note.body[start:end]

        if mode == "replace":
            replacement = content.rstrip("\n") + "\n"
        elif mode == "append":
            trimmed = existing.rstrip("\n")
            replacement = (f"{trimmed}\n" if trimmed else "") + content.rstrip("\n") + "\n"
        else:
            trimmed = existing.lstrip("\n")
            replacement = content.rstrip("\n") + "\n" + (trimmed if trimmed else "")

        # A section that ran to the next heading kept the blank line before it.
        # Putting it back is what stops a patch from slowly eating the spacing
        # of a note it is called on every day.
        if end < len(note.body) and not replacement.endswith("\n\n"):
            replacement += "\n"

        new_body = note.body[:start] + replacement + note.body[end:]
        text = self._compose(note.frontmatter_raw, new_body)
        rev, size, mtime_ns = md.atomic_write(absolute, text)
        self.index.invalidate(rel)
        return NoteRef(path=rel, rev=rev, size=size, modified=_iso(mtime_ns))

    def set_properties(
        self,
        path: str,
        properties: Dict[str, Any],
        *,
        expected_rev: Optional[str] = None,
    ) -> NoteRef:
        rel, absolute = self._note_path(path, must_exist=True)
        current, current_rev, _, _ = md.read_text(absolute)
        self._check_rev(rel, absolute, expected_rev, required=False, actual=current_rev)
        text = md.edit_frontmatter(current, properties)
        if text == current:
            entry = self.index.get(rel)
            stat = absolute.stat()
            return NoteRef(
                path=rel,
                rev=entry.rev if entry else current_rev,
                size=stat.st_size,
                modified=_iso(stat.st_mtime_ns),
            )
        rev, size, mtime_ns = md.atomic_write(absolute, text)
        self.index.invalidate(rel)
        return NoteRef(path=rel, rev=rev, size=size, modified=_iso(mtime_ns))

    # -- organize ----------------------------------------------------------- #

    def move(self, path: str, destination: str, *, update_links: bool = True) -> MoveResult:
        rel, absolute = self._note_path(path, must_exist=True)
        dest_rel, dest_absolute = self._note_path(destination)
        if dest_absolute.exists():
            raise NoteExistsError(f"{dest_rel} already exists")
        if dest_rel == rel:
            raise ProviderError("Source and destination are the same note")

        # Collected before the move, because after it the index no longer knows
        # anything resolves to the old path.
        inbound = self.index.notes_linking_to(rel) if update_links else []
        old_forms = self._link_forms(rel)

        vault_paths.ensure_parent(dest_absolute)
        shutil.move(str(absolute), str(dest_absolute))
        self.index.invalidate(rel)
        self.index.invalidate(dest_rel)
        self.index.refresh(force=True)

        new_form = self.index.shortest_unique_form(dest_rel)
        relinked: List[str] = []
        # A link already spelled the way it should be spelled afterwards is not a
        # link to rewrite. Without this, moving Projects/Note.md to Areas/Note.md
        # "rewrites" every [[Note]] to [[Note]]: identical bytes, a bumped mtime,
        # a commit on the synced vault, and a result claiming we touched nine
        # notes that we did not. `relinked` is reported to the user, so a false
        # positive there is worse than no report at all.
        replacements = {form: new_form for form in old_forms if form != new_form}
        if update_links and inbound and replacements:
            for holder in inbound:
                if holder == rel:
                    continue  # a note that linked to itself moved with it
                holder_absolute = self._root / holder
                if not holder_absolute.exists():
                    continue
                try:
                    text, _, _, _ = md.read_text(holder_absolute)
                    rewritten, count = md.rewrite_links(text, replacements)
                except OSError:
                    continue
                if count:
                    md.atomic_write(holder_absolute, rewritten)
                    self.index.invalidate(holder)
                    relinked.append(holder)

        self.index.refresh(force=True)
        entry = self.index.get(dest_rel)
        return MoveResult(
            path=rel,
            destination=dest_rel,
            rev=entry.rev if entry else md.read_text(dest_absolute)[1],
            relinked=sorted(relinked),
        )

    def delete(self, path: str) -> None:
        """Delete a note by moving it into the vault's ``.trash``.

        Not ``unlink``. Obsidian's own default is to move a deleted note to
        `.trash`, and a hosted vault has no desktop recycle bin behind it, so a
        tool that really removes the bytes is a tool whose one irreversible
        mistake is unrecoverable. `.trash` is excluded from listing and search,
        so a deleted note is gone as far as every other tool is concerned.
        """
        rel, absolute = self._note_path(path, must_exist=True)
        trash = self._root / ".trash"
        target = trash / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            target = target.with_name(f"{target.stem} {stamp}{target.suffix}")
        shutil.move(str(absolute), str(target))
        self.index.invalidate(rel)
        logger.info("Moved a note to .trash (%s bytes)", target.stat().st_size)
