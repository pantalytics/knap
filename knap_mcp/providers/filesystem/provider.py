"""The filesystem vault backend. Satisfies ``VaultProvider``.

The only place in the package that knows a vault is a directory. Everything above
this file talks to the protocol.

Three rules are enforced here rather than in the tool layer, because the tool
layer does not know what a vault root is and should not learn:

* every path goes through ``paths.resolve_in_vault`` before anything touches it;
* every body-replacing write checks ``expected_rev`` against what is on disk and
  refuses on a mismatch, because the customer's own edit in Obsidian is the one
  we do not get to lose;
* every write is atomic, and a move rewrites the links that pointed at the old
  path before it reports success.
"""

from __future__ import annotations

import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from ...logging_config import get_logger
from ..protocol import (
    AttachmentPayload,
    FolderInfo,
    LinkRef,
    NoteDetail,
    NoteExistsError,
    NoteNotFoundError,
    NoteRef,
    NoteSummary,
    PeriodicKind,
    ProviderError,
    RevisionMismatch,
    TagCount,
    VaultInfo,
)
from . import markdown as md
from . import paths as vault_paths
from . import periodic as periodic_notes
from .index import NoteEntry, VaultIndex
from .search import VaultSearch
from .writes import WriteOperationsMixin

logger = get_logger(__name__)


class FilesystemVaultProvider(WriteOperationsMixin):
    """A vault that is a directory of markdown files."""

    def __init__(
        self,
        root: Path,
        *,
        vault_id: str = "default",
        name: str = "",
        max_body_chars: int = 20000,
        max_attachment_bytes: int = 10 * 1024 * 1024,
        max_scan_notes: int = 20000,
    ):
        self._root = Path(root).expanduser().resolve()
        self._vault_id = vault_id
        self._name = name or self._root.name
        self.max_body_chars = max_body_chars
        self.max_attachment_bytes = max_attachment_bytes
        self._connected = False
        self.index = VaultIndex(self._root)
        self.search_engine = VaultSearch(self._root, self.index, max_scan_notes=max_scan_notes)

    # -- lifecycle ---------------------------------------------------------- #

    @property
    def is_authenticated(self) -> bool:
        return self._connected

    @property
    def vault_id(self) -> str:
        return self._vault_id

    @property
    def vault_name(self) -> str:
        return self._name

    @property
    def root(self) -> Path:
        return self._root

    def connect(self) -> None:
        if not self._root.is_dir():
            raise ProviderError(f"Vault directory does not exist: {self._root}")
        # Readability is checked now rather than on the first tool call, so a
        # permissions problem surfaces at startup where somebody is looking.
        try:
            next(iter(self._root.iterdir()), None)
        except OSError as exc:
            raise ProviderError(f"Vault directory is not readable: {exc}") from exc
        self._connected = True
        self.index.refresh(force=True)
        logger.info("Opened vault %r (%s notes)", self._name, len(list(self.index.note_paths())))

    def disconnect(self) -> None:
        self._connected = False

    def info(self) -> VaultInfo:
        notes = self.index.all_notes()
        return VaultInfo(
            id=self._vault_id,
            name=self._name,
            default=True,
            note_count=len(notes),
            size_bytes=sum(entry.size for entry in notes),
        )

    # -- browse ------------------------------------------------------------- #

    def list_folders(self, *, include_hidden: bool = False) -> List[FolderInfo]:
        counts = self.index.folders()
        out = [
            FolderInfo(path=folder, note_count=count)
            for folder, count in counts.items()
            if folder and (include_hidden or not vault_paths.is_hidden(folder))
        ]
        out.sort(key=lambda item: item.path.lower())
        return out

    def list_notes(
        self,
        folder: str = "",
        *,
        recursive: bool = True,
        include_hidden: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[NoteSummary], int]:
        prefix = ""
        if folder.strip():
            prefix = vault_paths.normalize(folder).rstrip("/") + "/"

        matching: List[NoteEntry] = []
        for entry in self.index.all_notes():
            if not include_hidden and vault_paths.is_hidden(entry.path):
                continue
            if prefix:
                if not entry.path.startswith(prefix):
                    continue
                if not recursive and "/" in entry.path[len(prefix) :]:
                    continue
            elif not recursive and "/" in entry.path:
                continue
            matching.append(entry)

        matching.sort(key=lambda item: item.mtime_ns, reverse=True)
        page = matching[offset : offset + limit]
        return [self._summary(entry) for entry in page], len(matching)

    # -- search ------------------------------------------------------------- #

    def search(
        self,
        query: Optional[str] = None,
        *,
        folder: str = "",
        tag: Optional[str] = None,
        prop: Optional[str] = None,
        prop_value: Optional[str] = None,
        since: Optional[str] = None,
        include_hidden: bool = False,
        limit: int = 25,
        offset: int = 0,
    ) -> Tuple[List[NoteSummary], int]:
        hits, total = self.search_engine.query(
            query,
            folder=folder,
            tag=tag,
            prop=prop,
            prop_value=prop_value,
            since=since,
            include_hidden=include_hidden,
            limit=limit,
            offset=offset,
        )
        return [self._summary(hit.entry, excerpt=hit.excerpt) for hit in hits], total

    @property
    def scan_was_truncated(self) -> bool:
        """Whether the last search stopped at the scan cap."""
        return self.search_engine.truncated_scan

    # -- read --------------------------------------------------------------- #

    def read(self, path: str, *, max_chars: int = 0) -> NoteDetail:
        rel, absolute = self._note_path(path, must_exist=True)
        text, rev, size, mtime_ns = md.read_text(absolute)
        note = md.parse(text)
        limit = max_chars or self.max_body_chars
        body = note.body
        truncated = len(body) > limit
        return NoteDetail(
            path=rel,
            title=md.title_of(rel, note),
            rev=rev,
            size=size,
            modified=_iso(mtime_ns),
            frontmatter=dict(note.frontmatter),
            body=body[:limit] if truncated else body,
            body_length=len(body),
            truncated=truncated,
            tags=md.tags_of(note),
            links=self._link_refs(rel, note),
            headings=md.headings_of(note),
        )

    def read_chunk(self, path: str, offset: int, length: int) -> Tuple[str, int, str]:
        rel, absolute = self._note_path(path, must_exist=True)
        text, rev, _, _ = md.read_text(absolute)
        body = md.parse(text).body
        start = max(0, offset)
        return body[start : start + max(0, length)], len(body), rev

    # -- graph -------------------------------------------------------------- #

    def resolve_link(self, target: str, *, from_path: str = "") -> Optional[str]:
        return self.index.resolve(target, from_path=from_path)

    def backlinks(self, path: str) -> List[NoteSummary]:
        rel, _ = self._note_path(path, must_exist=True)
        return [self._summary(entry) for entry in self.index.backlinks(rel)]

    def links(self, path: str, *, include_unresolved: bool = True) -> List[LinkRef]:
        rel, absolute = self._note_path(path, must_exist=True)
        note = md.parse(md.read_text(absolute)[0])
        refs = self._link_refs(rel, note)
        if include_unresolved:
            return refs
        return [ref for ref in refs if ref.resolved_path]

    def tags(self, *, prefix: str = "") -> List[TagCount]:
        return [TagCount(tag=tag, count=count) for tag, count in self.index.tag_counts(prefix)]

    # -- periodic notes ----------------------------------------------------- #

    def periodic_note(
        self,
        kind: PeriodicKind = "daily",
        date: Optional[str] = None,
        *,
        create: bool = False,
    ) -> Tuple[str, bool]:
        return periodic_notes.resolve(self, kind, date, create=create)

    # -- attachments -------------------------------------------------------- #

    def read_binary(self, path: str) -> AttachmentPayload:
        rel = vault_paths.normalize(path)
        absolute = vault_paths.resolve_in_vault(self._root, rel, must_exist=True)
        if not absolute.is_file():
            raise NoteNotFoundError(f"{rel} is not a file")
        size = absolute.stat().st_size
        if size > self.max_attachment_bytes:
            raise ProviderError(
                f"{rel} is {size} bytes, over the {self.max_attachment_bytes} byte limit. "
                "Raise KNAP_MCP_MAX_ATTACHMENT_BYTES if this is expected."
            )
        content_type = mimetypes.guess_type(rel)[0] or "application/octet-stream"
        return AttachmentPayload(
            path=rel, content_type=content_type, size=size, content=absolute.read_bytes()
        )

    def write_binary(self, path: str, content: bytes, *, overwrite: bool = False) -> NoteRef:
        rel = vault_paths.normalize(path)
        absolute = vault_paths.resolve_in_vault(self._root, rel)
        if absolute.exists() and not overwrite:
            raise NoteExistsError(f"{rel} already exists")
        if len(content) > self.max_attachment_bytes:
            raise ProviderError(
                f"{len(content)} bytes is over the {self.max_attachment_bytes} byte limit"
            )
        vault_paths.ensure_parent(absolute)
        # Same atomicity as a note: a half-written image is a broken embed, and
        # Obsidian will have cached the broken version by the time we finish.
        rev, size, mtime_ns = md.atomic_write_bytes(absolute, content)
        return NoteRef(path=rel, rev=rev, size=size, modified=_iso(mtime_ns))

    # -- internals ---------------------------------------------------------- #

    def _note_path(self, path: str, *, must_exist: bool = False) -> Tuple[str, Path]:
        """Normalize a note path, default the ``.md`` suffix, and confine it.

        Defaulting the suffix is not sloppiness: a client that has just read
        `[[Meeting notes]]` out of a link naturally passes "Meeting notes", and
        refusing that would make every graph tool a two-step dance.
        """
        rel = vault_paths.normalize(path)
        if not vault_paths.is_note(rel):
            rel = f"{rel}.md"
        absolute = vault_paths.resolve_in_vault(self._root, rel, must_exist=False)
        if must_exist and not absolute.is_file():
            resolved = self.index.resolve(path)
            if resolved:
                # The caller handed us a link target rather than a path. Follow
                # it, since that is what they meant and what Obsidian would do.
                return resolved, vault_paths.resolve_in_vault(self._root, resolved)
            raise NoteNotFoundError(f"{rel} does not exist")
        return rel, absolute

    def _check_rev(
        self,
        rel: str,
        absolute: Path,
        expected_rev: Optional[str],
        *,
        required: bool,
        actual: Optional[str] = None,
    ) -> None:
        """Refuse a write built on a stale read.

        ``required`` separates the two cases in the protocol: replacing a body
        must present a rev, while a section patch or a property change may, and
        is checked only if it does. Either way a mismatch is a refusal. Resolving
        it by writing anyway would mean the AI overwriting whatever the customer
        typed in Obsidian in the meantime.
        """
        if expected_rev is None:
            if required:
                raise RevisionMismatch(rel, "(none supplied)", actual or "")
            return
        current = actual if actual is not None else md.read_text(absolute)[1]
        if current != expected_rev:
            raise RevisionMismatch(rel, expected_rev, current)

    def _compose(self, frontmatter_raw: str, body: str) -> str:
        if not frontmatter_raw:
            return body
        return f"{frontmatter_raw}\n{body}"

    def _summary(self, entry: NoteEntry, *, excerpt: str = "") -> NoteSummary:
        return NoteSummary(
            path=entry.path,
            title=entry.title,
            rev=entry.rev,
            size=entry.size,
            modified=_iso(entry.mtime_ns),
            tags=list(entry.tags),
            excerpt=excerpt,
        )

    def _link_refs(self, rel: str, note: md.ParsedNote) -> List[LinkRef]:
        return md.to_link_refs(
            md.raw_links_of(note),
            lambda target: self.index.resolve(target, from_path=rel),
        )

    def _link_forms(self, rel: str) -> List[str]:
        """Every spelling a link to this note might currently use.

        A move has to find them all: the bare stem, the path with and without
        `.md`, and any alias the note declares. Missing one leaves a link
        pointing at nothing, which is precisely the failure the relink exists to
        prevent.
        """
        entry = self.index.get(rel)
        forms = {rel, rel[:-3] if vault_paths.is_note(rel) else rel}
        if entry:
            forms.add(entry.stem)
            forms.update(entry.aliases)
        return [form for form in forms if form]


def _iso(mtime_ns: int) -> str:
    return datetime.fromtimestamp(mtime_ns / 1_000_000_000, tz=timezone.utc).isoformat()
