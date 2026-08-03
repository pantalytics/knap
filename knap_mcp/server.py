"""Knap MCP server: FastMCP construction and the standalone runners.

``create_fastmcp_app`` is the single source of truth for FastMCP construction and
is the seam the private admin package reuses for its multi-tenant entry point.
Keep its signature aligned with ``squirrel_mcp.server.create_fastmcp_app``: the
two admin packages are the same code with a different provider underneath, and a
signature that drifts here is a rewrite there.

``KnapMCPServer`` is the standalone half: one vault from the environment, served
over stdio or streamable HTTP. The hosted package does not use it at all.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from mcp.server import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .config import KnapConfig, get_config
from .error_handling import ConfigurationError
from .knowledge import SERVER_INSTRUCTIONS
from .logging_config import get_logger, logging_config, perf_logger
from .providers import ProviderError, VaultProvider
from .providers.factory import create_vault_provider
from .tools import register_tools

logger = get_logger(__name__)

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


class KnapMCPServer:
    """Standalone server: owns the FastMCP app and one vault provider."""

    def __init__(self, config: Optional[KnapConfig] = None):
        self.config = config or get_config()
        logging_config.setup(self.config.log_level)

        self.provider: Optional[VaultProvider] = None
        self.tool_handler = None

        self.app = create_fastmcp_app()
        logger.info("Initialized Knap MCP Server v%s", SERVER_VERSION)

    def _ensure_vault(self) -> None:
        """Open the vault, so a bad path or permissions fail at startup.

        Surfaced here rather than on the first tool call, because a client that
        connects successfully and then errors on everything looks like a broken
        server, while a server that refuses to start says what is wrong.
        """
        if self.provider is not None:
            return
        try:
            with perf_logger.track_operation("vault_open"):
                provider = create_vault_provider(self.config)
                provider.connect()
            self.provider = provider
        except (ProviderError, ConfigurationError):
            raise

    def _register_tools(self) -> None:
        self.tool_handler = register_tools(self.app, self.provider, self.config)

    def _cleanup(self) -> None:
        if self.provider is not None:
            try:
                self.provider.disconnect()
            except Exception as exc:  # noqa: BLE001 - best effort on the way out
                logger.error("Error closing the vault: %s", exc)
        self.provider = None
        self.tool_handler = None

    async def run_stdio(self) -> None:
        """Run over stdio: Claude Desktop, Claude Code, any local client."""
        try:
            with perf_logger.track_operation("server_startup"):
                self._ensure_vault()
                self._register_tools()
            logger.info("Starting Knap over stdio")
            await self.app.run_stdio_async()
        except KeyboardInterrupt:
            logger.info("Interrupted")
        finally:
            self._cleanup()

    async def run_http(self, host: str = "localhost", port: int = 8000) -> None:
        """Run over streamable HTTP. Unauthenticated: put auth in front of it.

        The public package deliberately has no auth. Exposing a vault this way on
        anything but loopback means anyone who can reach the port can read and
        rewrite somebody's notes, which is what the hosted package's Zitadel
        layer exists to solve.
        """
        try:
            with perf_logger.track_operation("server_startup"):
                self._ensure_vault()
                self._register_tools()
            logger.info("Starting Knap over HTTP on %s:%s", host, port)

            self.app.settings.host = host
            self.app.settings.port = port
            if host == "0.0.0.0":  # noqa: S104 - explicit opt-in for LAN exposure
                logger.warning(
                    "Binding 0.0.0.0 with no authentication: anyone who can reach this "
                    "port can read and write the vault."
                )
                self.app.settings.transport_security = TransportSecuritySettings(
                    enable_dns_rebinding_protection=False
                )

            asgi_app = self.app.streamable_http_app()

            if os.getenv("KNAP_DEV_CORS", "").lower() == "true":
                from starlette.middleware.cors import CORSMiddleware

                asgi_app = CORSMiddleware(
                    asgi_app,
                    allow_origins=["*"],
                    allow_methods=["*"],
                    allow_headers=["*"],
                    expose_headers=["mcp-session-id"],
                )
                logger.warning("KNAP_DEV_CORS=true: permissive CORS enabled (dev only)")

            import uvicorn

            uvicorn_config = uvicorn.Config(
                asgi_app, host=host, port=port, log_level=self.config.log_level.lower()
            )
            await uvicorn.Server(uvicorn_config).serve()
        except KeyboardInterrupt:
            logger.info("Interrupted")
        finally:
            self._cleanup()

    def get_health_status(self) -> Dict[str, Any]:
        """Health snapshot. The hosted package serves a richer one at /healthz."""
        connected = bool(self.provider and self.provider.is_authenticated)
        status: Dict[str, Any] = {
            "status": "healthy" if connected else "unhealthy",
            "version": SERVER_VERSION,
            "git_commit": GIT_COMMIT,
            "vault": {
                "connected": connected,
                "provider": self.config.vault_provider,
                # The vault's *name*, never its path: a health endpoint is the
                # one thing on a hosted deployment that answers without a
                # session, so it does not get to describe the filesystem.
                "name": self.config.display_name,
            },
        }
        if connected:
            info = getattr(self.provider, "info", None)
            if callable(info):
                snapshot = info()
                status["vault"]["note_count"] = snapshot.note_count
                status["vault"]["size_bytes"] = snapshot.size_bytes
        return status
