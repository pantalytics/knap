"""vault_read_note, vault_read_chunk -- reading one note, including a long one."""

from __future__ import annotations

from typing import Optional

from ...schemas import ChunkResult, NoteContent
from .._common import require_text, run_blocking
from ._base import VaultToolBase
from ._shared import READ_ONLY, link_results


class ReadToolsMixin(VaultToolBase):
    """Read a note, and page a note too long to return in one call."""

    def _register_read_tools(self):
        @self.app.tool(title="Read Note", annotations=READ_ONLY)
        async def vault_read_note(
            path: str,
            max_chars: Optional[int] = None,
            vault: Optional[str] = None,
        ) -> NoteContent:
            """Read one note: body, frontmatter, tags, headings and outgoing links.

            Keep the `rev` from the result. Any later write that replaces the body
            has to pass it back as `expected_rev`, and that is what stops you
            overwriting an edit the user made in Obsidian while you were working.

            A long body comes back truncated, with `truncated: true` and the real
            length in `body_length`. Page the rest with vault_read_chunk rather
            than raising max_chars to swallow a whole note.

            `path` may also be a link target: if you read `[[Meeting notes]]` out
            of another note, pass "Meeting notes" and it resolves the way
            Obsidian would.

            Args:
                path: Vault-relative path, or a link target. The ".md" suffix is
                    optional.
                max_chars: Override the server's truncation limit for this call.
                vault: Which vault. Omit when only one is configured.
            """
            provider, sub = await self._get_provider(vault)
            require_text(path, "path")
            note = await run_blocking(
                provider, provider.read, path, max_chars=max_chars or self.max_body_chars
            )
            self._track_usage(sub, "vault_read_note")
            return NoteContent(
                path=note.path,
                title=note.title,
                rev=note.rev,
                size=note.size,
                modified=note.modified,
                frontmatter=note.frontmatter,
                body=note.body,
                body_length=note.body_length,
                truncated=note.truncated,
                tags=list(note.tags),
                links=link_results(note.links),
                headings=list(note.headings),
                vault=provider.vault_id,
            )

        @self.app.tool(title="Read Note Chunk", annotations=READ_ONLY)
        async def vault_read_chunk(
            path: str,
            offset: int = 0,
            length: int = 20000,
            vault: Optional[str] = None,
        ) -> ChunkResult:
            """Read a slice of a note's body, for a note too long to read at once.

            Offsets are into the body, after the frontmatter. The `rev` comes
            back on every chunk: if it changes between two calls the note was
            edited underneath you, and stitching the chunks together would
            produce text that never existed. Start again rather than continuing.

            Args:
                path: Vault-relative path, or a link target.
                offset: Characters to skip from the start of the body.
                length: How many characters to return.
                vault: Which vault. Omit when only one is configured.
            """
            provider, sub = await self._get_provider(vault)
            require_text(path, "path")
            text, body_length, rev = await run_blocking(
                provider, provider.read_chunk, path, max(0, offset), max(1, length)
            )
            self._track_usage(sub, "vault_read_chunk")
            return ChunkResult(
                path=path,
                text=text,
                offset=max(0, offset),
                length=len(text),
                body_length=body_length,
                rev=rev,
                vault=provider.vault_id,
            )

        _ = (vault_read_note, vault_read_chunk)


__all__ = ["ReadToolsMixin"]
