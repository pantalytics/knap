"""Fixtures: a seeded vault on disk, and a fake provider for the unit suite.

The seeded vault is this project's GreenMail. Squirrel needs a container to test
a mail backend honestly; a vault is a directory, so the integration layer costs a
tmpdir and there is no excuse for skipping it.

The vault below is deliberately awkward in the ways real vaults are: a note with
frontmatter in flow style, two notes sharing a basename in different folders, a
wikilink that resolves through an alias, a link that resolves to nothing, a
wikilink inside a code fence that must not count, and a nested tag.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

from knap_mcp.config import KnapConfig
from knap_mcp.providers.protocol import (
    FolderInfo,
    NoteDetail,
    NoteRef,
    NoteSummary,
    TagCount,
    VaultInfo,
)

VAULT_FILES: Dict[str, str] = {
    ".obsidian/daily-notes.json": json.dumps(
        {"folder": "Journal", "format": "YYYY-MM-DD", "template": "Templates/Daily.md"}
    ),
    ".obsidian/app.json": json.dumps({"attachmentFolderPath": "Attachments"}),
    "Templates/Daily.md": "# {{date}}\n\n## Captured\n\n## Log\n",
    "Areas/Work/Acme.md": (
        "---\n"
        "status: active\n"
        "tags:\n"
        "  - client\n"
        "  - project/acme\n"
        "aliases: [Acme Corp, ACME]\n"
        "due: 2026-09-01\n"
        "---\n"
        "# Acme\n"
        "\n"
        "The renewal is due. See [[Meeting notes]] and [[Something unwritten]].\n"
        "\n"
        "## Log\n"
        "\n"
        "- Kickoff done\n"
        "\n"
        "## Next\n"
        "\n"
        "- Send the quote\n"
    ),
    "Areas/Work/Meetings/index.md": "# Work meetings\n\nSee [[Acme Corp]].\n",
    "Projects/Meeting notes.md": (
        "# Meeting notes\n"
        "\n"
        "Spoke to [[Acme Corp]] about the renewal. #meeting #project/acme\n"
        "\n"
        "```\n"
        "This [[link in a fence]] is documentation, not a link.\n"
        "```\n"
        "\n"
        "Inline `[[also not a link]]` here.\n"
    ),
    "Projects/index.md": "# Projects\n\n- [[Meeting notes]]\n",
    "index.md": "# Home\n\n- [[Areas/Work/Acme]]\n- ![[Attachments/diagram.png]]\n",
    "Attachments/diagram.png": "not really a png, but bytes are bytes",
    "Archive/.trash-me.md": "# hidden by convention, not by dot-prefix\n",
    ".trash/Deleted thing.md": "# already deleted\n",
}


@pytest.fixture
def vault_root(tmp_path: Path) -> Path:
    """A seeded vault directory."""
    root = tmp_path / "vault"
    for rel, content in VAULT_FILES.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


@pytest.fixture
def config(vault_root: Path) -> KnapConfig:
    return KnapConfig(vault_path=str(vault_root), vault_name="Test vault")


@pytest.fixture
def provider(vault_root: Path):
    """A connected filesystem provider over the seeded vault."""
    from knap_mcp.providers.filesystem.provider import FilesystemVaultProvider

    instance = FilesystemVaultProvider(vault_root, name="Test vault")
    instance.connect()
    yield instance
    instance.disconnect()


class FakeVaultProvider:
    """In-memory provider for the unit suite. Touches no filesystem.

    Exists so the tool layer can be tested for the things that are the tool
    layer's job -- clamping a page size, refusing a destructive call without
    confirmation, mapping a dataclass onto the wire model -- without a single
    file being written. When one of those tests fails it is the tool layer that
    is wrong, which is the whole value of the split.
    """

    def __init__(self, notes: Optional[Dict[str, str]] = None):
        self.notes: Dict[str, str] = dict(notes or {"Note.md": "# Note\n\nBody.\n"})
        self.calls: List[Tuple[str, tuple, dict]] = []
        self._connected = True

    # -- recording ---------------------------------------------------------- #

    def _record(self, name: str, *args, **kwargs) -> None:
        self.calls.append((name, args, kwargs))

    def call_names(self) -> List[str]:
        return [name for name, _, _ in self.calls]

    def last_call(self, name: str) -> Tuple[tuple, dict]:
        for called, args, kwargs in reversed(self.calls):
            if called == name:
                return args, kwargs
        raise AssertionError(f"{name} was never called")

    # -- protocol ----------------------------------------------------------- #

    @property
    def is_authenticated(self) -> bool:
        return self._connected

    @property
    def vault_id(self) -> str:
        return "fake"

    @property
    def vault_name(self) -> str:
        return "Fake vault"

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def info(self) -> VaultInfo:
        return VaultInfo(id="fake", name="Fake vault", default=True, note_count=len(self.notes))

    def list_folders(self, *, include_hidden: bool = False) -> List[FolderInfo]:
        self._record("list_folders", include_hidden=include_hidden)
        return [FolderInfo(path="Folder", note_count=1)]

    def list_notes(self, folder="", *, recursive=True, include_hidden=False, limit=50, offset=0):
        self._record(
            "list_notes",
            folder,
            recursive=recursive,
            include_hidden=include_hidden,
            limit=limit,
            offset=offset,
        )
        return [self._summary(path) for path in sorted(self.notes)][offset : offset + limit], len(
            self.notes
        )

    def search(self, query=None, **kwargs):
        self._record("search", query, **kwargs)
        limit = kwargs.get("limit", 25)
        offset = kwargs.get("offset", 0)
        hits = [self._summary(path) for path in sorted(self.notes)]
        return hits[offset : offset + limit], len(hits)

    def read(self, path: str, *, max_chars: int = 20000) -> NoteDetail:
        self._record("read", path, max_chars=max_chars)
        body = self.notes[path]
        return NoteDetail(
            path=path,
            title=path.removesuffix(".md"),
            rev="rev-1",
            size=len(body),
            modified="2026-08-03T00:00:00+00:00",
            frontmatter={},
            body=body[:max_chars],
            body_length=len(body),
            truncated=len(body) > max_chars,
            tags=[],
            links=[],
            headings=["Note"],
        )

    def read_chunk(self, path: str, offset: int, length: int):
        self._record("read_chunk", path, offset, length)
        body = self.notes[path]
        return body[offset : offset + length], len(body), "rev-1"

    def write(self, path, body, *, mode="create", frontmatter=None, expected_rev=None) -> NoteRef:
        self._record("write", path, body, mode=mode, expected_rev=expected_rev)
        self.notes[path] = body
        return NoteRef(path=path, rev="rev-2", size=len(body))

    def patch_section(self, path, heading, content, *, mode="replace", expected_rev=None):
        self._record("patch_section", path, heading, content, mode=mode, expected_rev=expected_rev)
        return NoteRef(path=path, rev="rev-2", size=len(content))

    def set_properties(self, path, properties, *, expected_rev=None) -> NoteRef:
        self._record("set_properties", path, properties, expected_rev=expected_rev)
        return NoteRef(path=path, rev="rev-2", size=1)

    def move(self, path, destination, *, update_links=True):
        from knap_mcp.providers.protocol import MoveResult

        self._record("move", path, destination, update_links=update_links)
        return MoveResult(path=path, destination=destination, rev="rev-2", relinked=["Other.md"])

    def delete(self, path: str) -> None:
        self._record("delete", path)
        self.notes.pop(path, None)

    def resolve_link(self, target, *, from_path=""):
        self._record("resolve_link", target, from_path=from_path)
        return f"{target}.md" if f"{target}.md" in self.notes else None

    def backlinks(self, path: str) -> List[NoteSummary]:
        self._record("backlinks", path)
        return []

    def links(self, path: str, *, include_unresolved: bool = True):
        self._record("links", path, include_unresolved=include_unresolved)
        return []

    def tags(self, *, prefix: str = "") -> List[TagCount]:
        self._record("tags", prefix=prefix)
        return [TagCount(tag="meeting", count=2)]

    def periodic_note(self, kind="daily", date=None, *, create=False):
        self._record("periodic_note", kind, date, create=create)
        return "Journal/2026-08-03.md", create

    def read_binary(self, path: str):
        from knap_mcp.providers.protocol import AttachmentPayload

        self._record("read_binary", path)
        return AttachmentPayload(path=path, content_type="image/png", size=3, content=b"abc")

    def write_binary(self, path: str, content: bytes, *, overwrite: bool = False):
        self._record("write_binary", path, content, overwrite=overwrite)
        return NoteRef(path=path, rev="rev-2", size=len(content))

    def _summary(self, path: str) -> NoteSummary:
        return NoteSummary(
            path=path,
            title=path.removesuffix(".md"),
            rev="rev-1",
            size=len(self.notes[path]),
            modified="2026-08-03T00:00:00+00:00",
        )


@pytest.fixture
def fake_provider() -> FakeVaultProvider:
    return FakeVaultProvider()


@pytest.fixture
def handler(fake_provider: FakeVaultProvider):
    """A tool handler wired to the fake provider, with tools registered."""
    from knap_mcp.server import create_fastmcp_app
    from knap_mcp.tools import register_tools

    app = create_fastmcp_app()
    return register_tools(app, fake_provider, KnapConfig(skip_validation=True))


@pytest.fixture
def obsidian_edits():
    """Stand in for the customer typing in Obsidian while the AI works.

    A fixture rather than a helper in one test file, because both halves of the
    provider suite need it: the write tests to prove a stale ``expected_rev`` is
    refused, and the index tests to prove an outside edit is picked up.

    It bumps the mtime past the current one explicitly rather than trusting the
    clock. On a filesystem with coarse mtime granularity the edit can land in the
    same tick, and then the test passes for the wrong reason: the rev would look
    unchanged because the hash happened to differ, not because the check works.
    """
    import os

    from knap_mcp.providers.filesystem import markdown as md

    def edit(path: Path, extra: str) -> None:
        path.write_text(path.read_text() + extra, encoding="utf-8")
        stamp = path.stat().st_mtime_ns + 1_000_000_000
        os.utime(path, ns=(stamp, stamp))
        assert md.read_text(path)[3] == stamp

    return edit
