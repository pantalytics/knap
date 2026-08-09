"""Reading and writing an Obsidian note: frontmatter, wikilinks, headings, tags.

Three things in here are the difference between a tool someone trusts with their
notes and one they stop using.

**Code is not content.** Every scanner strips fenced blocks and inline code
first. A note that documents `[[wikilink]]` syntax inside a code fence has no
links in it, and a move that "fixes" that fence has corrupted a document.

**Writing frontmatter is not re-dumping it.** ``edit_frontmatter`` edits the
lines it must and leaves every other byte alone; ``frontmatter.py`` holds the
mechanics and the argument for them.

**A write is atomic or it is data loss.** Obsidian's file watcher, the LiveSync
bridge and git all read whatever is on disk the moment they notice a change, and
a half-written note is indistinguishable from a truncated one.
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from ..protocol import LinkRef
from . import frontmatter as _frontmatter

FRONTMATTER_FENCE = "---"

# Fenced code (``` or ~~~, any length >= 3), then inline code (`...`). Replaced
# with blanks of the same length rather than deleted, so every offset in the
# stripped text still lines up with the original: an excerpt or a link rewrite
# computed on the stripped copy applies to the real note.
_FENCE_RE = re.compile(r"^(?P<fence>```+|~~~+)[^\n]*\n.*?(?:^(?P=fence)[^\n]*$|\Z)", re.M | re.S)
_INLINE_CODE_RE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.S)

# [[target#anchor|alias]] and ![[embed]]. The negative lookbehind keeps a
# wikilink that is itself inside a markdown link from matching twice.
_WIKILINK_RE = re.compile(r"(?P<embed>!?)\[\[(?P<body>[^\[\]\n]+)\]\]")
# [text](target) with a target that is not a URL and not a bare anchor.
_MDLINK_RE = re.compile(r"(?P<embed>!?)\[(?P<text>[^\]\n]*)\]\((?P<target>[^)\s#][^)\n]*)\)")
_URL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")

_HEADING_RE = re.compile(r"^(?P<hashes>#{1,6})[ \t]+(?P<text>.+?)[ \t]*#*[ \t]*$", re.M)

# An inline #tag. Obsidian requires at least one character that is not a digit,
# which is what keeps "#1" and "#2026" from being tags, and tags may nest with
# "/".
#
# The lookbehind is what stops a link from becoming a tag. A "#" after a word
# character is a URL fragment or a heading anchor (`Note.md#Heading`), and a "#"
# after "(" is a markdown link to an anchor (`[text](#section)`) -- which is how
# every note with a table of contents used to grow a tag per entry.
_TAG_RE = re.compile(r"(?<![\w/#\)\(\]\[])#(?![0-9]+(?:\s|$))([\w/-]*[A-Za-z_-][\w/-]*)")


@dataclass
class ParsedNote:
    """A note, taken apart. ``raw`` is the file exactly as it was on disk."""

    raw: str
    frontmatter: Dict[str, Any] = field(default_factory=dict)
    #: The frontmatter block including both fences, "" when there is none.
    frontmatter_raw: str = ""
    #: Everything after the frontmatter.
    body: str = ""
    #: ``body`` with code blanked out. Same length, so offsets are shared.
    body_scannable: str = ""

    @property
    def has_frontmatter(self) -> bool:
        return bool(self.frontmatter_raw)


def strip_code(text: str) -> str:
    """Blank out fenced and inline code, preserving length and line structure."""

    def blank(match: re.Match) -> str:
        return "".join("\n" if c == "\n" else " " for c in match.group(0))

    without_fences = _FENCE_RE.sub(blank, text)
    return _INLINE_CODE_RE.sub(blank, without_fences)


def read_body(path: Path) -> str:
    """Read a note for scanning: the text, and none of the bookkeeping.

    ``read_text`` also hashes the whole file to build a ``rev``, which is the
    right thing when the caller is going to hand that rev to a client and
    exactly wasted work when it is not. Search opens every candidate note and
    uses none of it.
    """
    data = path.read_bytes()
    return unicodedata.normalize("NFC", data.decode("utf-8", errors="replace"))


def parse(raw: str, *, load_properties: bool = True) -> ParsedNote:
    """Split a note into frontmatter and body.

    A frontmatter block only counts at the very start of the file and only when
    it closes. An unterminated ``---`` is a horizontal rule in someone's note,
    not a broken header, and treating it as one would swallow their document.

    ``load_properties=False`` skips the YAML parse and leaves ``frontmatter``
    empty. The block is still split off the body, so ``body`` and
    ``body_scannable`` are unchanged; only the parsed mapping is missing. Search
    wants exactly that, because the index already holds the properties and it
    has compared them before it ever opens the file.
    """
    note = ParsedNote(raw=raw)
    if not raw.startswith(FRONTMATTER_FENCE):
        note.body = raw
        note.body_scannable = strip_code(raw)
        return note

    lines = raw.split("\n")
    if lines[0].strip() != FRONTMATTER_FENCE:
        note.body = raw
        note.body_scannable = strip_code(raw)
        return note

    close = None
    for i in range(1, len(lines)):
        if lines[i].strip() in (FRONTMATTER_FENCE, "..."):
            close = i
            break
    if close is None:
        note.body = raw
        note.body_scannable = strip_code(raw)
        return note

    note.frontmatter_raw = "\n".join(lines[: close + 1])
    note.body = "\n".join(lines[close + 1 :])
    if note.body.startswith("\n"):
        note.body = note.body[1:]
    note.body_scannable = strip_code(note.body)

    if not load_properties:
        return note

    inner = "\n".join(lines[1:close])
    try:
        loaded = yaml.safe_load(inner) if inner.strip() else None
    except yaml.YAMLError:
        # Obsidian tolerates frontmatter it cannot parse and so do we: the note
        # keeps working, it just has no properties as far as search is
        # concerned. Refusing to read the note at all would be worse.
        loaded = None
    note.frontmatter = loaded if isinstance(loaded, dict) else {}
    return note


def title_of(rel_path: str, note: ParsedNote) -> str:
    """The note's title: frontmatter ``title``, else the first H1, else the stem.

    Obsidian itself shows the filename, so the filename is the fallback rather
    than the first choice: a vault that sets a ``title`` property means it.
    """
    fm_title = note.frontmatter.get("title")
    if isinstance(fm_title, str) and fm_title.strip():
        return fm_title.strip()
    for match in _HEADING_RE.finditer(note.body_scannable):
        if len(match.group("hashes")) == 1:
            # Read the text from the real body, not the blanked copy.
            start, end = match.span("text")
            return note.body[start:end].strip()
    stem = rel_path.rsplit("/", 1)[-1]
    return stem[:-3] if stem.lower().endswith(".md") else stem


def headings_of(note: ParsedNote) -> List[str]:
    """Every heading, in document order, read from the real body."""
    out = []
    for match in _HEADING_RE.finditer(note.body_scannable):
        start, end = match.span("text")
        out.append(note.body[start:end].strip())
    return out


def tags_of(note: ParsedNote) -> List[str]:
    """Tags from the frontmatter and from the body, deduplicated, without '#'.

    Obsidian accepts ``tags:`` as a list or as a single space/comma separated
    string, and both ``tags`` and the legacy ``tag``. All of them count, because
    a vault that used the old spelling still has those tags.
    """
    found: List[str] = []
    seen = set()

    def add(value: str) -> None:
        cleaned = value.strip().lstrip("#").strip("/")
        if not cleaned:
            return
        key = cleaned.lower()
        if key not in seen:
            seen.add(key)
            found.append(cleaned)

    for key in ("tags", "tag"):
        raw = note.frontmatter.get(key)
        if isinstance(raw, str):
            for piece in re.split(r"[,\s]+", raw):
                add(piece)
        elif isinstance(raw, list):
            for piece in raw:
                if isinstance(piece, (str, int, float)):
                    add(str(piece))

    for match in _TAG_RE.finditer(note.body_scannable):
        add(match.group(1))
    return found


@dataclass
class RawLink:
    """A link as it sits in the file, with the offsets needed to rewrite it."""

    target: str
    anchor: str
    alias: str
    embed: bool
    wiki: bool
    #: Span of the target text alone, inside ``body``.
    start: int
    end: int


def raw_links_of(note: ParsedNote) -> List[RawLink]:
    """Every link in the body, with spans, scanning the code-stripped copy.

    Offsets index ``note.body``, which is what makes ``rewrite_links`` a
    byte-precise edit rather than a search and replace over the whole note.
    """
    links: List[RawLink] = []

    for match in _WIKILINK_RE.finditer(note.body_scannable):
        body_start, body_end = match.span("body")
        inner = note.body[body_start:body_end]
        target_part, _, alias = inner.partition("|")
        target, anchor = _split_anchor(target_part)
        if not target.strip():
            continue  # "[[#Heading]]" points inside this note, not at another
        leading = len(target_part) - len(target_part.lstrip())
        links.append(
            RawLink(
                target=target.strip(),
                anchor=anchor,
                alias=alias.strip(),
                embed=bool(match.group("embed")),
                wiki=True,
                start=body_start + leading,
                end=body_start + leading + len(target.strip()),
            )
        )

    for match in _MDLINK_RE.finditer(note.body_scannable):
        target_start, target_end = match.span("target")
        target_raw = note.body[target_start:target_end].strip()
        if _URL_RE.match(target_raw) or target_raw.startswith("//"):
            continue  # http:, mailto:, obsidian: are not vault paths
        # A markdown link may quote its target and may carry a title after it.
        target_part = target_raw.split(' "')[0].strip()
        target, anchor = _split_anchor(_unescape_md(target_part))
        if not target:
            continue
        links.append(
            RawLink(
                target=target,
                anchor=anchor,
                alias=note.body[match.span("text")[0] : match.span("text")[1]],
                embed=bool(match.group("embed")),
                wiki=False,
                start=target_start,
                end=target_start + len(target_part),
            )
        )

    links.sort(key=lambda link: link.start)
    return links


def _split_anchor(target: str) -> Tuple[str, str]:
    """``Note#Heading`` -> ("Note", "Heading"); ``Note^block`` -> ("Note", "block").

    An anchor at position zero means the link points inside the note it is in
    (``[[#Log]]``, ``[[^blockid]]``), so the note part comes back empty and the
    caller drops it. Requiring the marker past position zero, which is what this
    did, reported ``[[#Log]]`` as a link to a note literally named "#Log": never
    resolvable, and counted as a broken link in every note with a table of
    contents.
    """
    for marker in ("#^", "#", "^"):
        idx = target.find(marker)
        if idx >= 0:
            return target[:idx], target[idx + len(marker) :]
    return target, ""


def _unescape_md(target: str) -> str:
    """Markdown link targets percent-encode spaces and wrap in <> when odd."""
    from urllib.parse import unquote

    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1]
    return unquote(target)


def to_link_refs(links: List[RawLink], resolver) -> List[LinkRef]:
    """Map raw links onto the protocol's value object, resolving each target."""
    return [
        LinkRef(
            target=link.target,
            resolved_path=resolver(link.target),
            anchor=link.anchor,
            embed=link.embed,
            alias=link.alias,
        )
        for link in links
    ]


def rewrite_links(raw: str, replacements: Dict[str, str]) -> Tuple[str, int]:
    """Rewrite link targets in a note. Returns (new text, number rewritten).

    ``replacements`` maps an old target *as written* to its new spelling, so the
    caller decides how a link should be respelled after a move (Obsidian's own
    choice is the shortest unique form, not the full path). Edits are applied
    back to front so every span stays valid, and only the target text is
    touched: the alias, the anchor and the surrounding characters come out
    exactly as they went in.
    """
    note = parse(raw)
    links = raw_links_of(note)
    if not links:
        return raw, 0

    offset = len(raw) - len(note.body)
    body = note.body
    count = 0
    for link in sorted(links, key=lambda item: item.start, reverse=True):
        new_target = replacements.get(link.target)
        if new_target is None:
            continue
        body = body[: link.start] + new_target + body[link.end :]
        count += 1
    if not count:
        return raw, 0
    return raw[:offset] + body, count


def edit_frontmatter(raw: str, changes: Dict[str, Any]) -> str:
    """Merge frontmatter properties into a note, changing as few bytes as possible.

    Parses, then hands the three pieces to ``frontmatter.edit``. The split keeps
    that module free of any dependency on this one, so the frontmatter writer can
    be read, tested and reasoned about without the link and tag scanners in the
    way.
    """
    note = parse(raw)
    return _frontmatter.edit(
        raw,
        changes,
        frontmatter=note.frontmatter,
        frontmatter_raw=note.frontmatter_raw,
        body=note.body,
    )


def section_span(note: ParsedNote, heading: str) -> Optional[Tuple[int, int, int]]:
    """Locate a section by heading text. Returns (content start, content end, level).

    A section runs from just after its heading line to the next heading at the
    same level or higher, which is what makes appending under "## Log" land
    inside Log rather than at the end of whatever subsection came last. Matching
    is case-insensitive on the heading text, because nobody retypes a heading's
    capitalisation to append to it.
    """
    wanted = heading.strip().lstrip("#").strip().lower()
    matches = list(_HEADING_RE.finditer(note.body_scannable))
    for index, match in enumerate(matches):
        start, end = match.span("text")
        if note.body[start:end].strip().lower() != wanted:
            continue
        level = len(match.group("hashes"))
        content_start = match.end()
        if content_start < len(note.body) and note.body[content_start] == "\n":
            content_start += 1
        content_end = len(note.body)
        for later in matches[index + 1 :]:
            if len(later.group("hashes")) <= level:
                content_end = later.start()
                break
        return content_start, content_end, level
    return None


def compute_rev(content: bytes, mtime_ns: int) -> str:
    """The revision marker handed to clients and demanded back on an overwrite.

    Both halves earn their place. The hash is what actually detects a change,
    since mtime has one-second granularity on some filesystems and an AI writes
    twice inside one second. The mtime is what makes the index able to skip a
    file without hashing it. Truncated to 16 hex characters: this guards against
    a concurrent edit, not against a forger.
    """
    return f"{mtime_ns}-{hashlib.sha256(content).hexdigest()[:16]}"


def read_text(path: Path) -> Tuple[str, str, int, int]:
    """Read a note. Returns (text, rev, size, mtime_ns).

    Decoded as UTF-8 with replacement rather than strictly. A vault that picked
    up one Latin-1 note years ago should not have a tool that refuses to open
    it; the replacement character is visible and fixable, an exception is not.
    """
    data = path.read_bytes()
    stat = path.stat()
    text = unicodedata.normalize("NFC", data.decode("utf-8", errors="replace"))
    return text, compute_rev(data, stat.st_mtime_ns), stat.st_size, stat.st_mtime_ns


def atomic_write(path: Path, text: str) -> Tuple[str, int, int]:
    """Write a note atomically. Returns (rev, size, mtime_ns)."""
    return atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_bytes(path: Path, data: bytes) -> Tuple[str, int, int]:
    """Write bytes atomically. Returns (rev, size, mtime_ns).

    Temp file in the *same directory* (so ``os.replace`` is a rename inside one
    filesystem and therefore atomic), fsync before the rename (so a power loss
    cannot leave the rename durable and the contents not), and the original
    file's permissions carried over (so a vault with tightened modes does not
    quietly loosen one note at a time).

    Attachments go through here too: a half-written image is a broken embed, and
    Obsidian will have cached the broken version before we finish.
    """
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)

    mode = None
    if path.exists():
        try:
            mode = path.stat().st_mode & 0o777
        except OSError:
            mode = None

    tmp_fd, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=".knap-", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(tmp_fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    try:
        # Durability of the rename itself. Skipped silently where the platform
        # will not open a directory (Windows).
        dir_fd = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass

    stat = path.stat()
    return compute_rev(data, stat.st_mtime_ns), stat.st_size, stat.st_mtime_ns
