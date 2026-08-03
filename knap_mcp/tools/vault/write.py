"""The write tools, and the line between the additive ones and the destructive one.

vault_create_note, vault_append_note, vault_patch_section and
vault_set_properties are additive: they cannot lose what was already in the note,
so they take no confirmation. vault_update_note replaces a body, which can, so it
takes both `confirm=true` and `expected_rev`.

That line is the whole of principle 4. Gating the additive four as well would be
easy to defend in a meeting and would teach every client that the confirm prompt
is a formality to tap through, which is exactly how the one that matters gets
approved without being read.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ...schemas import NoteRefResult
from .._common import require_confirm, require_text, run_blocking
from ._base import VaultToolBase
from ._shared import ADDITIVE_WRITE, DESTRUCTIVE_WRITE


class WriteToolsMixin(VaultToolBase):
    """Create, append, patch, set properties, and replace."""

    def _register_write_tools(self):
        @self.app.tool(title="Create Note", annotations=ADDITIVE_WRITE)
        async def vault_create_note(
            path: str,
            body: str = "",
            properties: Optional[Dict[str, Any]] = None,
            vault: Optional[str] = None,
        ) -> NoteRefResult:
            """Create a new note. Refuses if the path is already taken.

            No confirmation needed: a new note takes nothing away. Missing
            folders are created, the way Obsidian creates them when you type a
            path into it.

            Write the note the way the vault already writes notes. Look at a
            neighbouring note first: its frontmatter keys, its heading depth, its
            tag style. Link with `[[Note name]]` using the exact title of a note
            that exists, checking with vault_search when unsure, because a link
            to a slightly wrong name creates a new empty concept in the user's
            graph rather than a connection.

            Args:
                path: Vault-relative path. The ".md" suffix is optional.
                body: Markdown body, without the frontmatter block.
                properties: Frontmatter properties as a mapping, if any.
                vault: Which vault. Omit when only one is configured.
            """
            provider, sub = await self._get_provider(vault, writes=True)
            require_text(path, "path")
            ref = await run_blocking(
                provider,
                provider.write,
                path,
                body,
                mode="create",
                frontmatter=properties,
            )
            self._track_usage(sub, "vault_create_note")
            return _ref(ref, provider, created=True)

        @self.app.tool(title="Append To Note", annotations=ADDITIVE_WRITE)
        async def vault_append_note(
            path: str,
            content: str,
            section: Optional[str] = None,
            vault: Optional[str] = None,
        ) -> NoteRefResult:
            """Add to a note, at the end or under a named heading. Creates it if missing.

            The right tool for a log entry, a meeting note, a captured thought.
            No confirmation needed: nothing above the addition is touched.

            With `section`, this appends inside that heading's section rather than
            at the end of the file, which is what someone would actually do with
            a "## Log" heading in a project note.

            Args:
                path: Vault-relative path, or a link target.
                content: Markdown to add.
                section: Heading to append under. The note must already have it;
                    vault_read_note lists a note's headings. Omit to append at
                    the end of the note.
                vault: Which vault. Omit when only one is configured.
            """
            provider, sub = await self._get_provider(vault, writes=True)
            require_text(path, "path")
            require_text(content, "content")
            if section and section.strip():
                ref = await run_blocking(
                    provider, provider.patch_section, path, section, content, mode="append"
                )
            else:
                ref = await run_blocking(provider, provider.write, path, content, mode="append")
            self._track_usage(sub, "vault_append_note")
            return _ref(ref, provider)

        @self.app.tool(title="Patch Note Section", annotations=ADDITIVE_WRITE)
        async def vault_patch_section(
            path: str,
            heading: str,
            content: str,
            mode: str = "replace",
            expected_rev: Optional[str] = None,
            vault: Optional[str] = None,
        ) -> NoteRefResult:
            """Change the content under one heading, leaving the rest of the note alone.

            Prefer this over vault_update_note whenever you are changing part of
            a note. Rewriting a whole note to change a section produces a diff
            the user has to read to trust, and on a synced vault that diff is
            permanent.

            A section runs from its heading to the next heading at the same level
            or higher, so appending under "## Log" lands inside Log rather than
            after whatever subsection came last.

            Args:
                path: Vault-relative path, or a link target.
                heading: The heading text, without the "#". Case-insensitive.
                    vault_read_note returns a note's headings.
                content: Markdown for the section.
                mode: "replace" (default), "append" or "prepend", relative to
                    what is under the heading now.
                expected_rev: The `rev` from your read. Optional here, and worth
                    passing: with it, a concurrent edit in Obsidian is refused
                    instead of silently overwritten.
                vault: Which vault. Omit when only one is configured.
            """
            provider, sub = await self._get_provider(vault, writes=True)
            require_text(path, "path")
            require_text(heading, "heading")
            if mode not in ("replace", "append", "prepend"):
                from ...error_handling import ValidationError

                raise ValidationError("mode must be 'replace', 'append' or 'prepend'")
            ref = await run_blocking(
                provider,
                provider.patch_section,
                path,
                heading,
                content,
                mode=mode,
                expected_rev=expected_rev,
            )
            self._track_usage(sub, "vault_patch_section")
            return _ref(ref, provider)

        @self.app.tool(title="Set Note Properties", annotations=ADDITIVE_WRITE)
        async def vault_set_properties(
            path: str,
            properties: Dict[str, Any],
            expected_rev: Optional[str] = None,
            vault: Optional[str] = None,
        ) -> NoteRefResult:
            """Merge frontmatter properties. The body is not touched.

            A value of null removes that property. Properties not named are left
            as they are, and the note's other frontmatter keeps its exact
            formatting: this edits the lines it must rather than rewriting the
            block.

            Args:
                path: Vault-relative path, or a link target.
                properties: Keys to set. Use null as a value to remove a key.
                expected_rev: The `rev` from your read. Optional, and worth
                    passing.
                vault: Which vault. Omit when only one is configured.
            """
            provider, sub = await self._get_provider(vault, writes=True)
            require_text(path, "path")
            if not properties:
                from ...error_handling import ValidationError

                raise ValidationError("properties must name at least one key")
            ref = await run_blocking(
                provider, provider.set_properties, path, properties, expected_rev=expected_rev
            )
            self._track_usage(sub, "vault_set_properties")
            return _ref(ref, provider)

        @self.app.tool(title="Replace Note Body", annotations=DESTRUCTIVE_WRITE)
        async def vault_update_note(
            path: str,
            body: str,
            expected_rev: str,
            confirm: bool = False,
            properties: Optional[Dict[str, Any]] = None,
            vault: Optional[str] = None,
        ) -> NoteRefResult:
            """Replace a note's whole body. DESTRUCTIVE.

            Needs `confirm=true` and the `expected_rev` from your read. Show the
            user what is being replaced and get their approval before calling
            with confirm=true.

            Consider vault_patch_section first. This tool is for the case where
            the note really is being rewritten, not for changing part of one.

            If it comes back saying the note changed since you read it, the user
            is editing in Obsidian right now. Read the note again and rebuild the
            change on what is there. Never retry by dropping expected_rev.

            Args:
                path: Vault-relative path, or a link target.
                body: The new body, replacing everything after the frontmatter.
                expected_rev: The `rev` from your read of this note. Required.
                confirm: Must be true. Get the user's approval first.
                properties: Frontmatter properties to merge, if any. Omitting
                    this leaves the existing frontmatter alone.
                vault: Which vault. Omit when only one is configured.
            """
            provider, sub = await self._get_provider(vault, writes=True)
            require_text(path, "path")
            require_text(expected_rev, "expected_rev")
            require_confirm(
                confirm,
                f"Replacing the body of {path}",
                "Everything currently in the note below the frontmatter will be replaced.",
            )
            ref = await run_blocking(
                provider,
                provider.write,
                path,
                body,
                mode="overwrite",
                frontmatter=properties,
                expected_rev=expected_rev,
            )
            self._track_usage(sub, "vault_update_note")
            return _ref(ref, provider)

        _ = (
            vault_create_note,
            vault_append_note,
            vault_patch_section,
            vault_set_properties,
            vault_update_note,
        )


def _ref(ref, provider, *, created: bool = False) -> NoteRefResult:
    return NoteRefResult(
        path=ref.path,
        rev=ref.rev,
        size=ref.size,
        modified=ref.modified,
        vault=provider.vault_id,
        created=created,
    )


__all__ = ["WriteToolsMixin"]
