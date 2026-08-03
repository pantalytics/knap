"""``VaultToolHandler``: every tool, assembled from per-area mixins.

``_get_provider``, ``_list_vaults`` and ``_track_usage`` are the seams the private
admin package overrides. Standalone they return the single env-configured vault.
Renaming any of the three breaks that package, so they are listed in CLAUDE.md as
part of the open-core contract.

Every tool body follows the same three steps, and the uniformity is the point: a
client that learns the shape of one error learns all of them.

    provider, sub = await self._get_provider(vault)
    result = await run_blocking(provider, provider.something, ...)
    self._track_usage(sub, "vault_something")
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from mcp.server.fastmcp import FastMCP

from ..config import KnapConfig
from ..error_handling import ValidationError
from ..providers import VaultNotFoundError, VaultProvider
from ._common import _current_sub, logger
from .vault.attachments import AttachmentToolsMixin
from .vault.browse import BrowseToolsMixin
from .vault.graph import GraphToolsMixin
from .vault.organize import OrganizeToolsMixin
from .vault.periodic import PeriodicToolsMixin
from .vault.query import QueryToolsMixin
from .vault.read import ReadToolsMixin
from .vault.write import WriteToolsMixin


class VaultToolHandler(
    BrowseToolsMixin,
    QueryToolsMixin,
    ReadToolsMixin,
    WriteToolsMixin,
    OrganizeToolsMixin,
    GraphToolsMixin,
    PeriodicToolsMixin,
    AttachmentToolsMixin,
):
    """Handles MCP tool requests for vault operations."""

    def __init__(
        self,
        app: FastMCP,
        provider: Optional[VaultProvider] = None,
        config: Optional[KnapConfig] = None,
    ):
        self.app = app
        self.provider = provider
        self.config = config
        self._register_tools()

    # -- admin seams -------------------------------------------------------- #

    async def _get_provider(
        self,
        vault: Optional[str] = None,
        *,
        writes: bool = False,
    ) -> Tuple[VaultProvider, str]:
        """Resolve the provider + subject for the current request.

        Admin hook: the private package overrides this to resolve a per-workspace
        vault from the authenticated subject, and to refuse a subject it does not
        know rather than provisioning one.

        Standalone there is exactly one vault, and naming a different one is a
        client error rather than something to ignore: quietly serving the only
        vault we have when a specific one was asked for is how an AI writes into
        the wrong place.
        """
        if self.provider is None:
            raise ValidationError("No vault is configured")
        if vault and vault.strip():
            wanted = vault.strip().lower()
            known = {self.provider.vault_id.lower(), self.provider.vault_name.lower()}
            if wanted not in known:
                raise VaultNotFoundError(
                    f"No vault {vault!r} here. This server serves one vault: "
                    f"{self.provider.vault_name!r}. Call vault_list_vaults to see it."
                )
        _current_sub.set("stdio")
        return self.provider, "stdio"

    async def _list_vaults(self) -> List[dict]:
        """The vaults this server can resolve, for ``vault_list_vaults``.

        Admin hook: the private package overrides this to list a workspace's
        vaults. Standalone there is one and it is always the default.
        """
        if self.provider is None:
            return []
        info = getattr(self.provider, "info", None)
        if callable(info):
            snapshot = info()
            return [
                {
                    "id": snapshot.id,
                    "name": snapshot.name,
                    "default": True,
                    "note_count": snapshot.note_count,
                    "size_bytes": snapshot.size_bytes,
                }
            ]
        return [
            {
                "id": self.provider.vault_id,
                "name": self.provider.vault_name,
                "default": True,
                "note_count": None,
                "size_bytes": None,
            }
        ]

    def _track_usage(self, sub: str, tool_name: str) -> None:
        """Usage-tracking hook. No-op here; overridden by the admin package.

        Called on the success path only, which is deliberate and is why the
        hosted package does analytics one level down instead of here: this hook
        cannot see a failed call.
        """

    # -- limits ------------------------------------------------------------- #

    @property
    def default_limit(self) -> int:
        return self.config.default_limit if self.config else 25

    @property
    def max_limit(self) -> int:
        return self.config.max_limit if self.config else 100

    @property
    def max_body_chars(self) -> int:
        return self.config.max_body_chars if self.config else 20000

    def _register_tools(self) -> None:
        # Registration order is the order tools appear to clients, so it reads
        # as the order somebody would actually work: look around, find, read,
        # write, tidy up.
        self._register_browse_tools()
        self._register_query_tools()
        self._register_read_tools()
        self._register_write_tools()
        self._register_organize_tools()
        self._register_graph_tools()
        self._register_periodic_tools()
        self._register_attachment_tools()


def register_tools(
    app: FastMCP,
    provider: Optional[VaultProvider] = None,
    config: Optional[KnapConfig] = None,
) -> VaultToolHandler:
    """Register all vault tools with the FastMCP app."""
    handler = VaultToolHandler(app, provider=provider, config=config)
    logger.info("Registered vault tools")
    return handler
