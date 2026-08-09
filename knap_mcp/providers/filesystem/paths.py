"""Vault-root confinement. Every path argument in the server passes through here.

This is the one vulnerability class that ends with one customer reading another
customer's notes, so it is a separate module with a separate regression file
(``tests/integration/test_path_safety.py``) and no shortcuts.

The rule is not "reject suspicious-looking input". Blacklists lose. The rule is:
resolve the path the way the operating system will, then refuse it unless the
result is still inside the vault. Resolving first is what catches the cases a
string check cannot see, above all a symlink inside the vault pointing out of it.

Vault-relative paths are the currency: "/" separated, no leading slash, no
``..``. That is the shape the protocol documents, the shape the tools hand in,
and the shape everything hands back.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Iterable

from ..protocol import PathNotAllowedError

#: Directories excluded from listing and search unless asked for. `.obsidian` is
#: configuration, `.trash` is what Obsidian already deleted, `.git` belongs to
#: whatever is syncing the vault. All three are reachable with
#: ``include_hidden``, because "which plugins does this vault use" is a real
#: question and the answer is in `.obsidian`.
HIDDEN_PREFIX = "."

#: Extension we treat as a note. Everything else is an attachment.
NOTE_SUFFIX = ".md"


def _reject(reason: str) -> "PathNotAllowedError":
    """Build the error.

    The message names the rule that was broken and never echoes the resolved
    path. On a hosted deployment an error that says where the vault actually
    lives is a probe answering itself.
    """
    return PathNotAllowedError(f"Path not allowed: {reason}")


def normalize(rel: str) -> str:
    """Vault-relative path in canonical form, or raise.

    Pure string work: no filesystem access, so this is also what validates a
    path for a note that does not exist yet.
    """
    if not isinstance(rel, str):
        raise _reject("not a string")
    raw = rel.strip()
    if not raw:
        raise _reject("empty")
    if "\x00" in raw:
        raise _reject("contains a null byte")

    # Windows separators are accepted on the way in and normalized, so a client
    # on Windows works, but they cannot be used to smuggle a segment past the
    # ".." check below by spelling it "..\\".
    raw = raw.replace("\\", "/")

    # An absolute path is never vault-relative. Checked before splitting, since
    # "/etc/passwd" splits into segments that each look harmless.
    if raw.startswith("/"):
        raise _reject("absolute paths are not vault-relative")
    if len(raw) > 1 and raw[1] == ":":
        raise _reject("drive letters are not vault-relative")

    segments: list[str] = []
    for segment in raw.split("/"):
        if segment in ("", "."):
            continue  # "a//b" and "a/./b" are just "a/b"
        if segment == "..":
            # Not resolved-then-checked but refused outright. A path that climbs
            # and comes back ("a/../b") is legitimate to the OS and is still a
            # client that does not know where it is pointing.
            raise _reject("'..' is not allowed in a vault path")
        if any(ord(c) < 32 or ord(c) == 127 for c in segment):
            raise _reject("path segments may not contain control characters")
        # Surrounding whitespace is trimmed rather than refused, per segment and
        # not just on the whole string. Windows strips it at create time, so
        # "Folder /note.md" and "Folder/note.md" are one file there; normalizing
        # to the form that will actually exist beats refusing a path a client got
        # from a listing. Trimming only the ends of the whole string, which is
        # what this used to do, made the last segment behave differently from
        # every other one.
        segment = segment.strip()
        if not segment or segment == ".":
            continue
        if segment.endswith("."):
            # Never trimmed, always refused. Windows drops a trailing dot, so
            # "note.md." and "note.md" would be one file with two revisions, and
            # that is a way to get past an expected_rev check rather than a
            # spelling a client arrives at by accident.
            raise _reject("path segments may not end with a dot")
        if segment == "..":
            raise _reject("'..' is not allowed in a vault path")
        segments.append(segment)

    if not segments:
        raise _reject("empty after normalization")

    # macOS hands back decomposed unicode (NFD) while Linux stores whatever it
    # was given, so the same note name can arrive in two encodings. Normalizing
    # to NFC means a rev computed on one platform still matches on the other.
    return unicodedata.normalize("NFC", "/".join(segments))


def resolve_in_vault(root: Path, rel: str, *, must_exist: bool = False) -> Path:
    """Absolute path for a vault-relative one, confined to ``root``.

    The order matters and is the whole point: normalize the string, join, let the
    OS resolve every symlink, and only then ask whether the answer is still
    inside the vault. A symlink in the vault pointing at ``/etc`` passes every
    string check there is and fails this one.
    """
    normalized = normalize(rel)
    root_resolved = root.resolve()
    candidate = (root_resolved / normalized).resolve()

    if candidate == root_resolved:
        raise _reject("refers to the vault root itself")
    if not _is_within(candidate, root_resolved):
        # Deliberately the same message as a '..' rejection: whether the escape
        # was spelled with dots or built out of a symlink is not the caller's
        # business, and telling them narrows the search for a way through.
        raise _reject("resolves outside the vault")

    if must_exist and not candidate.exists():
        raise _reject("does not exist")
    return candidate


def _is_within(candidate: Path, root: Path) -> bool:
    """Whether ``candidate`` is at or under ``root``.

    ``Path.is_relative_to`` arrived in 3.9 and is what this would be, but
    comparing resolved ``parts`` says the same thing on every version and cannot
    be fooled by a prefix that is not a path boundary: ``/vaults/acme-evil`` is
    not inside ``/vaults/acme``, and a plain ``startswith`` on strings thinks it
    is.
    """
    return candidate.parts[: len(root.parts)] == root.parts


def to_relative(root: Path, absolute: Path) -> str:
    """Vault-relative form of an absolute path inside the vault.

    Raises when the path is outside, so this cannot be used to hand a caller a
    path we would have refused on the way in.
    """
    root_resolved = root.resolve()
    resolved = absolute.resolve()
    if not _is_within(resolved, root_resolved):
        raise _reject("resolves outside the vault")
    return unicodedata.normalize("NFC", resolved.relative_to(root_resolved).as_posix())


def relative_to_walked_root(root_resolved: Path, absolute: Path) -> str:
    """Vault-relative form of a path that ``walk_notes`` itself produced.

    ``to_relative`` is the one to use for a path that came from a caller: it
    resolves both sides and refuses anything that lands outside, and that check
    is the vulnerability class the whole module exists for. This is the other
    case. ``walk_notes`` starts at ``root.resolve()`` and skips symlinks
    outright, so every path it yields is already under the resolved root and got
    there without traversing a link. Resolving it again asks the kernel to
    confirm something the walk guaranteed, once per note, and on a vault of a
    few thousand notes that realpath storm is most of what an index refresh
    costs.

    Pass ``root_resolved`` already resolved, once, by the caller. Only feed this
    paths from the walk. The guarantee is a POSIX one: ``resolve`` does not cross
    a bind mount, and ``walk_notes`` refuses symlinks. A Windows junction is
    neither, so on Windows this is a check worth keeping rather than skipping.

    Misuse still raises ``PathNotAllowedError`` like everything else here, rather
    than the ``ValueError`` ``relative_to`` would give: comparing ``parts``
    costs nothing, and a module whose whole job is one error type should not have
    one entrance that throws a different one.
    """
    if not _is_within(absolute, root_resolved):
        raise _reject("is not under the vault root")
    return unicodedata.normalize("NFC", absolute.relative_to(root_resolved).as_posix())


def is_hidden(rel: str) -> bool:
    """Whether any segment of the path is a dot entry.

    Covers `.obsidian`, `.trash`, `.git` and anything else the vault keeps
    beside its notes, without maintaining a list of names that will be out of
    date the moment a plugin invents one.
    """
    return any(part.startswith(HIDDEN_PREFIX) for part in rel.split("/") if part)


def is_note(rel: str) -> bool:
    """Whether this path is a markdown note rather than an attachment."""
    return rel.lower().endswith(NOTE_SUFFIX)


def ensure_parent(path: Path) -> None:
    """Create the parent directory of a note about to be written.

    Obsidian creates folders implicitly when you type a path into it, so a tool
    that refuses to do the same would be a tool that behaves differently from
    the app it is standing in for.
    """
    path.parent.mkdir(parents=True, exist_ok=True)


def walk_notes(
    root: Path,
    *,
    subfolder: str = "",
    include_hidden: bool = False,
) -> Iterable[Path]:
    """Yield every note under ``root``, skipping dot directories by default.

    ``os.scandir`` under the hood via ``Path.iterdir``, and no file is opened:
    this is the cheap half of indexing, and it is why a refresh over ten
    thousand notes costs milliseconds rather than a read of the whole vault.
    """
    start = resolve_in_vault(root, subfolder) if subfolder else root.resolve()
    if not start.is_dir():
        return
    stack = [start]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except (PermissionError, OSError):
            # A directory we cannot read is not a reason to fail the whole
            # listing. The vault is somebody else's filesystem.
            continue
        for entry in entries:
            name = entry.name
            if not include_hidden and name.startswith(HIDDEN_PREFIX):
                continue
            if entry.is_symlink():
                # Followed only if it lands inside the vault. Rather than
                # resolving here and re-checking, skip: a symlinked note would
                # otherwise appear twice in a listing, once per name.
                continue
            if entry.is_dir():
                stack.append(entry)
            elif is_note(name):
                yield entry
