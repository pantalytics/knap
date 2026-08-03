"""vault_search -- the tool that stands in for Dataview."""

from __future__ import annotations

from typing import Optional

from ...schemas import SearchResult
from .._common import clamp_limit, clamp_offset, run_blocking
from ._base import VaultToolBase
from ._shared import READ_ONLY, summary_result


class QueryToolsMixin(VaultToolBase):
    """Search a vault by text, tag, frontmatter property, folder and date."""

    def _register_query_tools(self):
        @self.app.tool(title="Search Vault", annotations=READ_ONLY)
        async def vault_search(
            query: Optional[str] = None,
            folder: str = "",
            tag: Optional[str] = None,
            property: Optional[str] = None,  # noqa: A002 - the vault's own word for it
            property_value: Optional[str] = None,
            since: Optional[str] = None,
            limit: Optional[int] = None,
            offset: int = 0,
            include_hidden: bool = False,
            vault: Optional[str] = None,
        ) -> SearchResult:
            """Search the vault, most recently modified first.

            Narrow the search here rather than pulling a page and filtering it
            yourself: every argument below is applied server-side, and `total`
            comes back as the number of matches so you can tell the user how
            much there is.

            There is no Dataview or Bases here. Those run inside Obsidian and
            this server does not run them, so do not compose a Dataview query
            and expect a result. `property` plus `property_value` is the
            equivalent for almost everything people use Dataview for: "every
            note where status is active" is
            `property="status", property_value="active"`.

            Args:
                query: Free text, matched against the title, the body, the
                    aliases and the frontmatter. Text inside fenced code blocks
                    does not match, on purpose. Omit to list by the filters
                    alone.
                folder: Limit to this folder and its subfolders.
                tag: A tag, with or without the leading "#". Matches both inline
                    `#tags` and the frontmatter `tags:` list, and matches nested
                    tags by prefix, so "project" finds `#project/acme`.
                property: A frontmatter key. On its own this asks which notes
                    have the property at all, which is a different and useful
                    question.
                property_value: Require the property to equal this. Matches an
                    item of a list-valued property too.
                since: ISO date (YYYY-MM-DD). Only notes modified on or after it.
                limit: Page size. Defaults to the server default, capped at the
                    maximum.
                offset: Matches to skip, for paging.
                include_hidden: Include dot folders such as `.obsidian`.
                vault: Which vault. Omit when only one is configured.
            """
            provider, sub = await self._get_provider(vault)
            effective_limit = clamp_limit(limit, self.default_limit, self.max_limit)
            effective_offset = clamp_offset(offset)
            notes, total = await run_blocking(
                provider,
                provider.search,
                query,
                folder=folder,
                tag=tag,
                prop=property,
                prop_value=property_value,
                since=since,
                include_hidden=include_hidden,
                limit=effective_limit,
                offset=effective_offset,
            )
            self._track_usage(sub, "vault_search")
            return SearchResult(
                notes=[summary_result(note) for note in notes],
                total=total,
                limit=effective_limit,
                offset=effective_offset,
                vault=provider.vault_id,
                # A capped scan that reported itself as a complete answer would
                # have the client tell the user their vault does not contain
                # something it does.
                scan_truncated=bool(getattr(provider, "scan_was_truncated", False)),
            )

        _ = vault_search


__all__ = ["QueryToolsMixin"]
