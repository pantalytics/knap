"""Provider-agnostic vault interface.

``VaultProvider`` is the seam that makes backends swappable: the tool layer only
ever touches this protocol and the plain dataclasses below, never a concrete
filesystem call. The filesystem backend satisfies it today; a git-object or
object-storage backend can satisfy it later without any change to the tools.

The dataclasses are deliberately transport-neutral (no pydantic, no MCP types) so
a provider implementation depends on nothing above it. The tool layer maps them
to the pydantic response models in ``schemas.py``.

Two things in here are load-bearing and easy to mistake for detail:

``NoteRef.rev`` is how two writers stay out of each other's way. The customer's
Obsidian and the AI edit the same file, so every read hands back a revision
marker and every body-replacing write takes the one it expects. A mismatch is a
refusal, not a merge -- see ``RevisionMismatch``.

``resolve_link`` is Obsidian's link resolution, not ours. ``[[Note]]`` resolves by
shortest unique path and honours the ``aliases:`` frontmatter key, and a link may
carry a ``#heading`` or ``^block`` anchor. Getting this wrong does not fail
loudly; it quietly returns the wrong note, or breaks the graph on a move.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Protocol, Tuple, runtime_checkable

# --------------------------------------------------------------------------- #
# Errors raised by provider implementations. The tool layer catches these and
# re-raises them as sanitized MCP errors.
# --------------------------------------------------------------------------- #


class ProviderError(Exception):
    """Base error for any vault-provider failure."""


class VaultNotFoundError(ProviderError):
    """The requested vault is not configured, or not this tenant's."""


class NoteNotFoundError(ProviderError):
    """The requested note or attachment does not exist."""


class NoteExistsError(ProviderError):
    """A create was asked for a path that is already taken."""


class PathNotAllowedError(ProviderError):
    """A path argument resolved outside the vault root, or onto a refused entry.

    Raised by the backend's path confinement, never by the tool layer. Absolute
    paths, ``..`` segments and symlinks leading out of the vault all land here.
    The message must not echo the resolved path back: on a hosted deployment
    that is a probe telling the caller where the vault lives.
    """


class RevisionMismatch(ProviderError):
    """The note changed since the caller read it.

    Carries the revision the caller expected and the one on disk, so the tool
    layer can tell the client to re-read rather than guess. Never resolved by
    overwriting: the customer's own edit is the one we do not get to lose.
    """

    def __init__(self, path: str, expected: str, actual: str):
        super().__init__(f"{path} changed since it was read")
        self.path = path
        self.expected = expected
        self.actual = actual


class QuotaExceeded(ProviderError):
    """The write would take the vault past its size or note-count limit.

    Standalone this never fires (there is no quota on your own disk); the hosted
    backend raises it. It lives here so the tool layer has one error to report
    rather than learning what a plan is.
    """


class PeriodicNotesNotConfigured(ProviderError):
    """The vault has no periodic-notes settings for this kind.

    Its own error type because it is not a failure so much as an answer: the
    client should tell the user to switch the plugin on, not retry.
    """


class VaultSettingsUnavailable(PeriodicNotesNotConfigured):
    """The vault carries no Obsidian settings, so there is nothing to read them from.

    A backend may hand over notes and attachments without the ``.obsidian``
    folder, and then no setting in it can be read either way. A subclass rather
    than a sibling so that anything already catching the parent keeps working,
    and its own type because the two answers are different: telling somebody the
    plugin is off is a claim we cannot make about settings we cannot see, and it
    sends them to change something that is probably already right.
    """


# --------------------------------------------------------------------------- #
# Transport-neutral value objects.
# --------------------------------------------------------------------------- #

#: Where a write puts its content relative to what is already there.
WriteMode = Literal["create", "overwrite", "append", "prepend"]

#: How ``patch_section`` treats the content already under the heading.
PatchMode = Literal["replace", "append", "prepend"]

#: Which periodic note is meant. Obsidian's Periodic Notes vocabulary.
PeriodicKind = Literal["daily", "weekly", "monthly"]


@dataclass
class VaultInfo:
    """One vault this server can resolve, for ``vault_list_vaults``."""

    id: str
    name: str
    default: bool = False
    note_count: Optional[int] = None
    size_bytes: Optional[int] = None


@dataclass
class FolderInfo:
    path: str  # vault-relative, "/" separated, no leading slash
    note_count: int = 0


@dataclass
class NoteRef:
    """A note's identity and revision. What a write hands back.

    ``rev`` is opaque to callers and to the tool layer: it is whatever the
    backend can compare cheaply and will not repeat after an edit (the
    filesystem backend uses mtime plus a content hash, because mtime alone has
    one-second granularity on some filesystems and two edits inside one second
    is exactly what an AI does).
    """

    path: str
    rev: str
    size: int
    modified: Optional[str] = None  # ISO 8601


@dataclass
class LinkRef:
    """One wikilink or markdown link found in a note.

    ``target`` is the raw link text as written. ``resolved_path`` is the note it
    points at, or ``None`` when nothing matches -- an unresolved link is usually
    the interesting one, so it is reported rather than dropped.
    """

    target: str
    resolved_path: Optional[str]
    anchor: str = ""  # "#Heading" or "^block-id", without the marker
    embed: bool = False  # written as ![[...]]
    alias: str = ""  # the display text of [[target|alias]]


@dataclass
class NoteSummary:
    """A note as it appears in a listing or a search hit."""

    path: str
    title: str  # the H1, else the frontmatter title, else the filename stem
    rev: str
    size: int
    modified: Optional[str]
    tags: List[str] = field(default_factory=list)
    excerpt: str = ""  # around the match for a search hit, the opening otherwise


@dataclass
class NoteDetail:
    """One note, read.

    ``body`` may be truncated at the configured limit; ``body_length`` is the
    real length and the client pages the rest with ``read_chunk``. Same contract
    as Squirrel's ``mail_read`` / ``mail_read_chunk``, for the same reason: a
    single note can be longer than a context window.
    """

    path: str
    title: str
    rev: str
    size: int
    modified: Optional[str]
    frontmatter: Dict[str, object]
    body: str
    body_length: int
    truncated: bool
    tags: List[str] = field(default_factory=list)
    links: List[LinkRef] = field(default_factory=list)
    headings: List[str] = field(default_factory=list)


@dataclass
class TagCount:
    tag: str
    count: int


@dataclass
class AttachmentPayload:
    path: str
    content_type: str
    size: int
    content: bytes


@dataclass
class MoveResult:
    """What a move actually did.

    ``relinked`` is the list of notes whose inbound links were rewritten. It is
    part of the result rather than a log line because the client has to be able
    to tell the user "and it updated these nine notes" -- a move that touches
    nine other files without saying so is the kind of surprise that loses trust
    in the whole tool set.
    """

    path: str
    destination: str
    rev: str
    relinked: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# The protocol.
# --------------------------------------------------------------------------- #


@runtime_checkable
class VaultProvider(Protocol):
    """Interface every vault backend must satisfy.

    Implementations are synchronous (filesystem calls block). The tool layer runs
    them off the event loop via ``run_blocking`` and serializes calls to the same
    provider with a lock, so implementations need not be thread-safe themselves.

    Every ``path`` argument is vault-relative, "/" separated, without a leading
    slash, and every implementation is responsible for confining it to the vault
    root before touching anything. That check belongs to the backend, not to the
    tools: the tools do not know what a root is.
    """

    @property
    def is_authenticated(self) -> bool: ...

    @property
    def vault_id(self) -> str:
        """Stable identifier of the vault this provider serves."""
        ...

    @property
    def vault_name(self) -> str:
        """Human name of the vault, for the client to say out loud."""
        ...

    def connect(self) -> None:
        """Open the vault. Raises ProviderError when it is not usable."""
        ...

    def disconnect(self) -> None:
        """Release it. Must be safe to call when not connected."""
        ...

    # -- browse ------------------------------------------------------------- #

    def list_folders(self, *, include_hidden: bool = False) -> List[FolderInfo]:
        """Every folder holding at least one note.

        ``include_hidden`` reaches ``.obsidian/`` and ``.trash/``, which are
        configuration rather than content and are therefore out by default.
        """
        ...

    def list_notes(
        self,
        folder: str = "",
        *,
        recursive: bool = True,
        include_hidden: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[NoteSummary], int]:
        """A page of notes, newest first. Returns (page, total matching)."""
        ...

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
        """Search the vault. Returns (page of summaries, total matching count).

        ``query`` is free text matched against the title, the body and the
        frontmatter. ``tag`` matches both ``#inline`` tags and the frontmatter
        ``tags:`` list, and matches a nested tag by prefix, because
        ``#project/acme`` is a ``#project``. ``prop`` / ``prop_value`` filter on
        a frontmatter key, which is the honest answer to most of what people
        reach for Dataview to do -- we do not evaluate Dataview. ``since`` is an
        ISO date (YYYY-MM-DD) lower bound on the modification time.
        """
        ...

    # -- read --------------------------------------------------------------- #

    def read(self, path: str, *, max_chars: int = 20000) -> NoteDetail:
        """Read one note, truncating the body at ``max_chars``."""
        ...

    def read_chunk(self, path: str, offset: int, length: int) -> Tuple[str, int, str]:
        """A slice of a note's body. Returns (text, total body length, rev).

        The rev comes back so a client paging a long note can tell that the note
        changed underneath it rather than silently stitching two versions
        together.
        """
        ...

    # -- write -------------------------------------------------------------- #

    def write(
        self,
        path: str,
        body: str,
        *,
        mode: WriteMode = "create",
        frontmatter: Optional[Dict[str, object]] = None,
        expected_rev: Optional[str] = None,
    ) -> NoteRef:
        """Write a note.

        ``mode="create"`` raises ``NoteExistsError`` on an existing path.
        ``"overwrite"`` replaces the body and requires ``expected_rev``: it is the
        one mode that can lose someone's words, so it does not get to run on a
        stale read. ``"append"`` and ``"prepend"`` create the note when missing
        and ignore ``expected_rev``, because adding to the end of a note cannot
        destroy what is above it.

        ``frontmatter`` merges into what is there. Passing ``None`` leaves it
        alone; that is deliberately different from passing ``{}``, which is
        "no properties".

        Implementations write atomically: a temp file in the same directory, then
        ``os.replace``. Obsidian's file watcher and every sync transport cope
        badly with a partially written note, and a truncated note is
        indistinguishable from data loss.
        """
        ...

    def patch_section(
        self,
        path: str,
        heading: str,
        content: str,
        *,
        mode: PatchMode = "replace",
        expected_rev: Optional[str] = None,
    ) -> NoteRef:
        """Change the content under one heading, leaving the rest untouched.

        The tool that makes an AI edit read like a human edit: appending to
        "## Log" in a project note is what someone would actually do, where
        rewriting the whole note to add two lines is not. ``heading`` matches on
        its text, at any level, first occurrence. A section ends at the next
        heading of the same or a higher level.
        """
        ...

    def set_properties(
        self,
        path: str,
        properties: Dict[str, object],
        *,
        expected_rev: Optional[str] = None,
    ) -> NoteRef:
        """Merge frontmatter properties. A value of ``None`` removes the key.

        Body untouched, including its exact whitespace: a properties change that
        reformats someone's note shows up as a diff nobody asked for, and on a
        git-backed vault that diff is permanent.
        """
        ...

    # -- organize ----------------------------------------------------------- #

    def move(self, path: str, destination: str, *, update_links: bool = True) -> MoveResult:
        """Move or rename a note, rewriting inbound links by default.

        Not rewriting is offered (``update_links=False``) and is almost always
        wrong: Obsidian rewrites on its own moves, so a move that does not is a
        vault whose graph quietly disagrees with itself.
        """
        ...

    def delete(self, path: str) -> None:
        """Delete a note. The backend decides whether that means ``.trash/``."""
        ...

    # -- graph -------------------------------------------------------------- #

    def resolve_link(self, target: str, *, from_path: str = "") -> Optional[str]:
        """Resolve a wikilink target to a note path, Obsidian's way.

        Shortest unique path first, then ``aliases:`` frontmatter, then a
        same-folder match relative to ``from_path``. Returns ``None`` when
        nothing matches, which is a real and common state -- an unresolved link
        is how a vault records an intention.
        """
        ...

    def backlinks(self, path: str) -> List[NoteSummary]:
        """Notes that link to this one."""
        ...

    def links(self, path: str, *, include_unresolved: bool = True) -> List[LinkRef]:
        """Links out of this note."""
        ...

    def tags(self, *, prefix: str = "") -> List[TagCount]:
        """Tags in the vault with their counts, most used first."""
        ...

    # -- periodic notes ----------------------------------------------------- #

    def periodic_note(
        self,
        kind: PeriodicKind = "daily",
        date: Optional[str] = None,
        *,
        create: bool = False,
    ) -> Tuple[str, bool]:
        """Resolve the periodic note for a date. Returns (path, created).

        Folder, filename format and template come from the vault's own settings
        (``.obsidian/daily-notes.json`` or the Periodic Notes plugin), because a
        daily note that lands somewhere other than where the customer's Obsidian
        would have put it is a second daily note, not a daily note. Nothing is
        guessed: with no settings and no ``create``, this reports that the vault
        has no periodic notes configured. A backend that carries no ``.obsidian``
        folder at all raises ``VaultSettingsUnavailable`` instead, because it
        cannot tell an unconfigured vault from a configured one.
        """
        ...

    # -- attachments -------------------------------------------------------- #

    def read_binary(self, path: str) -> AttachmentPayload:
        """Read a non-markdown file (image, pdf, audio) by path."""
        ...

    def write_binary(self, path: str, content: bytes, *, overwrite: bool = False) -> NoteRef:
        """Write a non-markdown file. Refuses an existing path unless told."""
        ...
