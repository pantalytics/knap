"""vault_backlinks, vault_links, vault_list_tags -- the graph, which is the point.

Why someone uses Obsidian rather than a folder of text files. A tool set that can
read and write notes but not see what connects them is a file manager.
"""

from __future__ import annotations

from typing import Optional

from ...schemas import BacklinkResult, OutgoingLinkResult, TagList, TagResult
from .._common import require_text, run_blocking
from ._base import VaultToolBase
from ._shared import READ_ONLY, link_results, summary_result


class GraphToolsMixin(VaultToolBase):
    """What links here, what this links to, and what the vault is about."""

    def _register_graph_tools(self):
        @self.app.tool(title="Note Backlinks", annotations=READ_ONLY)
        async def vault_backlinks(
            path: str,
            vault: Optional[str] = None,
        ) -> BacklinkResult:
            """The notes that link to this one.

            The honest way to answer "what do I know about X": the note itself
            plus everything that referenced it. Also what to check before
            deleting or moving a note, so you can tell the user what depends on
            it.

            Args:
                path: Vault-relative path, or a link target.
                vault: Which vault. Omit when only one is configured.
            """
            provider, sub = await self._get_provider(vault)
            require_text(path, "path")
            notes = await run_blocking(provider, provider.backlinks, path)
            self._track_usage(sub, "vault_backlinks")
            return BacklinkResult(
                path=path,
                notes=[summary_result(note) for note in notes],
                total=len(notes),
                vault=provider.vault_id,
            )

        @self.app.tool(title="Note Links", annotations=READ_ONLY)
        async def vault_links(
            path: str,
            include_unresolved: bool = True,
            vault: Optional[str] = None,
        ) -> OutgoingLinkResult:
            """The links out of this note, including the ones that go nowhere.

            An unresolved link is not a fault to fix. It is how a vault records
            an intention: somebody wrote `[[Thing I should write up]]` because
            they meant to. Worth mentioning to the user, not worth creating
            unasked.

            Args:
                path: Vault-relative path, or a link target.
                include_unresolved: Keep links that match no note. True by
                    default, because those are usually the interesting ones.
                vault: Which vault. Omit when only one is configured.
            """
            provider, sub = await self._get_provider(vault)
            require_text(path, "path")
            links = await run_blocking(
                provider, provider.links, path, include_unresolved=include_unresolved
            )
            self._track_usage(sub, "vault_links")
            return OutgoingLinkResult(
                path=path,
                links=link_results(links),
                total=len(links),
                unresolved=sum(1 for link in links if not link.resolved_path),
                vault=provider.vault_id,
            )

        @self.app.tool(title="List Tags", annotations=READ_ONLY)
        async def vault_list_tags(
            prefix: str = "",
            vault: Optional[str] = None,
        ) -> TagList:
            """Every tag in the vault with its note count, most used first.

            The cheapest way to understand a vault you have not seen: the tags
            are the user's own words for what they keep in it. Nested tags count
            for their parents, so `#project/acme` also counts as `#project`.

            Args:
                prefix: Only tags starting with this. Pass "project" to see the
                    children of a nested tag.
                vault: Which vault. Omit when only one is configured.
            """
            provider, sub = await self._get_provider(vault)
            tags = await run_blocking(provider, provider.tags, prefix=prefix)
            self._track_usage(sub, "vault_list_tags")
            return TagList(
                tags=[TagResult(tag=tag.tag, count=tag.count) for tag in tags],
                total=len(tags),
                vault=provider.vault_id,
            )

        _ = (vault_backlinks, vault_links, vault_list_tags)


__all__ = ["GraphToolsMixin"]
