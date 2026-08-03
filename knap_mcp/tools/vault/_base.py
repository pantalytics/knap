"""What every tool mixin may assume the composed handler provides.

The mixins are half a class: they call ``self.app.tool(...)`` and
``self._get_provider(...)``, and neither exists until ``VaultToolHandler`` puts
them together. A type checker is right to complain about that, and there are two
ways to answer it.

squirrel-mcp turns the rule off (``unresolved-attribute = "ignore"`` in its ty
config). That works and it also means a genuine typo -- ``self._trak_usage`` --
resolves to nothing and ships. This package declares the seam instead, so the
attributes are real to the checker and a misspelling is an error again.

Nothing here is ever executed: ``VaultToolHandler`` defines all of it, and by the
MRO its definitions win. These bodies raise rather than pass so that a mixin
composed onto something that forgot to implement the seam fails loudly instead of
returning ``None`` into a tool.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:  # pragma: no cover - imported for annotations only
    from mcp.server.fastmcp import FastMCP

    from ...config import KnapConfig
    from ...providers import VaultProvider


class VaultToolBase:
    """The contract the mixins are written against."""

    #: The FastMCP app tools register themselves on.
    app: "FastMCP"
    #: Server configuration, or None when the handler was built without one.
    config: "Optional[KnapConfig]"

    async def _get_provider(
        self,
        vault: Optional[str] = None,
        *,
        writes: bool = False,
    ) -> "Tuple[VaultProvider, str]":
        """Resolve the provider and the authenticated subject for this request."""
        raise NotImplementedError

    async def _list_vaults(self) -> List[dict]:
        """The vaults this server can resolve."""
        raise NotImplementedError

    def _track_usage(self, sub: str, tool_name: str) -> None:
        """Record a successful tool call."""
        raise NotImplementedError

    @property
    def default_limit(self) -> int:
        raise NotImplementedError

    @property
    def max_limit(self) -> int:
        raise NotImplementedError

    @property
    def max_body_chars(self) -> int:
        raise NotImplementedError
