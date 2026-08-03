"""vault_list_vaults, vault_list_folders, vault_list_notes -- orientation."""

from __future__ import annotations

from typing import Optional

from ...schemas import FolderList, FolderRef, NoteList, VaultList, VaultRef
from .._common import clamp_limit, clamp_offset, run_blocking
from ._base import VaultToolBase
from ._shared import READ_ONLY, summary_result


class BrowseToolsMixin(VaultToolBase):
    """Where a client starts: which vault, which folders, which notes."""

    def _register_browse_tools(self):
        @self.app.tool(title="List Vaults", annotations=READ_ONLY)
        async def vault_list_vaults() -> VaultList:
            """List the vaults this server can reach.

            Every other tool takes an optional `vault` argument (the id or name
            from here). Omit it when there is one vault; pass it when there are
            several, because the tools refuse rather than guess which you meant.
            """
            rows = await self._list_vaults()
            return VaultList(
                vaults=[
                    VaultRef(
                        id=row["id"],
                        name=row["name"],
                        default=bool(row.get("default")),
                        note_count=row.get("note_count"),
                        size_bytes=row.get("size_bytes"),
                    )
                    for row in rows
                ],
                total=len(rows),
            )

        @self.app.tool(title="List Folders", annotations=READ_ONLY)
        async def vault_list_folders(
            include_hidden: bool = False,
            vault: Optional[str] = None,
        ) -> FolderList:
            """The vault's folder tree, with a note count per folder.

            Worth calling before you write a path: a note written to a folder
            that does not match the vault's own structure is a note the user
            will not find where they expect it.

            Args:
                include_hidden: Include `.obsidian`, `.trash` and other dot
                    folders. Those are configuration rather than notes, so they
                    are out by default, but `.obsidian` is where a vault's
                    plugins and settings live if that is what you were asked.
                vault: Which vault (id or name from vault_list_vaults). Omit
                    when only one is configured.
            """
            provider, sub = await self._get_provider(vault)
            folders = await run_blocking(
                provider, provider.list_folders, include_hidden=include_hidden
            )
            self._track_usage(sub, "vault_list_folders")
            return FolderList(
                folders=[
                    FolderRef(path=folder.path, note_count=folder.note_count) for folder in folders
                ],
                total=len(folders),
                vault=provider.vault_id,
            )

        @self.app.tool(title="List Notes", annotations=READ_ONLY)
        async def vault_list_notes(
            folder: str = "",
            recursive: bool = True,
            limit: Optional[int] = None,
            offset: int = 0,
            include_hidden: bool = False,
            vault: Optional[str] = None,
        ) -> NoteList:
            """List notes, most recently modified first.

            Paginated. A vault holds thousands of notes, so never try to pull
            all of them: `total` tells you how many there are and you page with
            limit/offset. If you are looking for something specific, vault_search
            is the tool, not this one followed by sifting.

            Args:
                folder: Limit to this folder (vault-relative, "/" separated).
                    Omit for the whole vault.
                recursive: Include subfolders. True by default.
                limit: Page size. Defaults to the server default, capped at the
                    maximum.
                offset: Notes to skip, for paging.
                include_hidden: Include dot folders such as `.obsidian`.
                vault: Which vault. Omit when only one is configured.
            """
            provider, sub = await self._get_provider(vault)
            effective_limit = clamp_limit(limit, self.default_limit, self.max_limit)
            effective_offset = clamp_offset(offset)
            notes, total = await run_blocking(
                provider,
                provider.list_notes,
                folder,
                recursive=recursive,
                include_hidden=include_hidden,
                limit=effective_limit,
                offset=effective_offset,
            )
            self._track_usage(sub, "vault_list_notes")
            return NoteList(
                notes=[summary_result(note) for note in notes],
                total=total,
                limit=effective_limit,
                offset=effective_offset,
                folder=folder,
                vault=provider.vault_id,
            )

        # Referenced so linters see the registered closures as used.
        _ = (vault_list_vaults, vault_list_folders, vault_list_notes)


__all__ = ["BrowseToolsMixin"]
