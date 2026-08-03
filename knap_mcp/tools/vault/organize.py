"""vault_move_note, vault_delete_note -- the two that need confirming."""

from __future__ import annotations

from typing import Optional

from ...schemas import DeleteResult, MoveResultModel
from .._common import require_confirm, require_text, run_blocking
from ._base import VaultToolBase
from ._shared import DESTRUCTIVE_WRITE


class OrganizeToolsMixin(VaultToolBase):
    """Move and delete, both with the graph in mind."""

    def _register_organize_tools(self):
        @self.app.tool(title="Move Or Rename Note", annotations=DESTRUCTIVE_WRITE)
        async def vault_move_note(
            path: str,
            destination: str,
            update_links: bool = True,
            confirm: bool = False,
            vault: Optional[str] = None,
        ) -> MoveResultModel:
            """Move or rename a note, rewriting the links that pointed at it. DESTRUCTIVE.

            Needs `confirm=true`. Renaming is the same operation: pass a new
            filename as the destination.

            `relinked` in the result lists every note whose links were rewritten
            to follow the move. Tell the user how many notes were touched, because
            that is the part they cannot see, and a move that quietly edits nine
            other files is how a tool loses their trust.

            Leave `update_links` on. Obsidian rewrites links on its own moves, so
            a move that does not leaves the vault's graph disagreeing with itself
            and links that go nowhere.

            Args:
                path: The note to move, vault-relative or a link target.
                destination: Where it goes, vault-relative. Missing folders are
                    created. Refuses if a note is already there.
                update_links: Rewrite inbound links. True by default and should
                    stay true unless the user asked otherwise.
                confirm: Must be true. Get the user's approval first.
                vault: Which vault. Omit when only one is configured.
            """
            provider, sub = await self._get_provider(vault, writes=True)
            require_text(path, "path")
            require_text(destination, "destination")
            require_confirm(
                confirm,
                f"Moving {path} to {destination}",
                "Notes linking to it will be rewritten to follow the move."
                if update_links
                else "Links pointing at the old path will be left broken.",
            )
            result = await run_blocking(
                provider, provider.move, path, destination, update_links=update_links
            )
            self._track_usage(sub, "vault_move_note")
            return MoveResultModel(
                path=result.path,
                destination=result.destination,
                rev=result.rev,
                relinked=list(result.relinked),
                vault=provider.vault_id,
            )

        @self.app.tool(title="Delete Note", annotations=DESTRUCTIVE_WRITE)
        async def vault_delete_note(
            path: str,
            confirm: bool = False,
            vault: Optional[str] = None,
        ) -> DeleteResult:
            """Delete a note. DESTRUCTIVE.

            Needs `confirm=true`. The note is moved to the vault's `.trash`
            rather than removed, which is what Obsidian itself does, so it is
            recoverable. Say so when you report back.

            Check vault_backlinks first and tell the user what will point at
            nothing afterwards. A note with fifteen backlinks is usually a note
            somebody meant to keep.

            Args:
                path: The note to delete, vault-relative or a link target.
                confirm: Must be true. Get the user's approval first.
                vault: Which vault. Omit when only one is configured.
            """
            provider, sub = await self._get_provider(vault, writes=True)
            require_text(path, "path")
            require_confirm(
                confirm,
                f"Deleting {path}",
                "It moves to the vault's .trash folder, so it can be recovered. "
                "Check its backlinks first and say what will break.",
            )
            await run_blocking(provider, provider.delete, path)
            self._track_usage(sub, "vault_delete_note")
            return DeleteResult(
                path=path,
                deleted=True,
                detail="Moved to the vault's .trash folder, so it can be recovered from Obsidian.",
                vault=provider.vault_id,
            )

        _ = (vault_move_note, vault_delete_note)


__all__ = ["OrganizeToolsMixin"]
