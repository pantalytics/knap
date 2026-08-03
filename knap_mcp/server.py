"""Knap MCP server: FastMCP construction.

``create_fastmcp_app`` is the single source of truth for FastMCP construction and
is the seam the private admin package reuses for its multi-tenant entry point.
Keep its signature aligned with ``squirrel_mcp.server.create_fastmcp_app``: the
two admin packages are the same code with a different provider underneath, and a
signature that drifts here is a rewrite there.

The stdio/HTTP runners (``KnapMCPServer``) land here in Phase 1, alongside the
filesystem provider and the tool mixins. See docs/plan.md.
"""

from __future__ import annotations

import os

from mcp.server import FastMCP

from .knowledge import SERVER_INSTRUCTIONS

# Must match __version__ in __init__.py and version in pyproject.toml.
SERVER_VERSION = "0.1.0"
GIT_COMMIT = os.environ.get("GIT_COMMIT", "unknown")


def create_fastmcp_app(
    *, auth=None, token_verifier=None, extra_instructions: str | None = None
) -> FastMCP:
    """Create the FastMCP app with this server's canonical settings.

    Used by the standalone runner and by the private admin package's multi-tenant
    entry point. ``stateless_http`` so any replica can serve any request.
    ``extra_instructions`` is appended to the handshake instructions -- the hosted
    layer uses it to teach the client that vaults live in a panel and that a
    workspace can hold more than one.
    """
    instructions = SERVER_INSTRUCTIONS
    if extra_instructions:
        instructions = f"{instructions}\n{extra_instructions}"
    return FastMCP(
        name="knap",
        instructions=instructions,
        auth=auth,
        token_verifier=token_verifier,
        stateless_http=True,
        json_response=True,
    )
