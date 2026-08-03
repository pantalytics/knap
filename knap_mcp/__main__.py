"""CLI entry point: pick a transport, open a vault, serve.

``--vault`` is here because it is the argument people actually want on the
command line, and because a Claude Desktop config is easier to read with the path
in the command than with an env block beside it. It sets the same environment
variable the config reads, so there is exactly one way a vault path reaches the
server.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from . import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="knap-mcp",
        description="Knap, for Obsidian. An MCP server over an Obsidian vault.",
        epilog=(
            "Configure with a .env file or environment variables (see .env.example). "
            "The HTTP transport has no authentication: keep it on loopback."
        ),
    )
    parser.add_argument("--version", action="version", version=f"knap-mcp {__version__}")
    parser.add_argument(
        "--vault",
        metavar="PATH",
        help="Path to the vault directory. Overrides KNAP_VAULT_PATH.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        help="Transport. Defaults to KNAP_MCP_TRANSPORT, else stdio.",
    )
    parser.add_argument("--host", help="HTTP host. Defaults to KNAP_MCP_HOST, else localhost.")
    parser.add_argument("--port", type=int, help="HTTP port. Defaults to KNAP_MCP_PORT, else 8000.")
    parser.add_argument("--env-file", metavar="PATH", help="Load configuration from this file.")
    parser.add_argument("--log-level", help="DEBUG, INFO, WARNING, ERROR or CRITICAL.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Open the vault, report what is in it, and exit without serving.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # Flags become environment variables before the config is loaded, so the
    # precedence is flag, then .env, then environment, in one place instead of
    # two code paths that can disagree.
    if args.vault:
        os.environ["KNAP_VAULT_PATH"] = str(Path(args.vault).expanduser())
    if args.transport:
        os.environ["KNAP_MCP_TRANSPORT"] = args.transport
    if args.host:
        os.environ["KNAP_MCP_HOST"] = args.host
    if args.port:
        os.environ["KNAP_MCP_PORT"] = str(args.port)
    if args.log_level:
        os.environ["KNAP_MCP_LOG_LEVEL"] = args.log_level.upper()

    from .config import load_config
    from .error_handling import ConfigurationError
    from .logging_config import logging_config
    from .providers.protocol import ProviderError

    try:
        config = load_config(Path(args.env_file) if args.env_file else None)
    except ValueError as exc:
        # A configuration problem is a message to a person at a terminal, not a
        # traceback: they mistyped a path or have no .env yet.
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    logging_config.setup(config.log_level)

    from .server import KnapMCPServer

    try:
        server = KnapMCPServer(config)
        if args.check:
            return _check(server)
        if config.transport == "stdio":
            asyncio.run(server.run_stdio())
        else:
            asyncio.run(server.run_http(host=config.host, port=config.port))
    except (ProviderError, ConfigurationError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    return 0


def _check(server) -> int:
    """Open the vault and print what is in it.

    The first thing to run against a real vault, and the thing to run when a
    client reports that the server "does not work": it separates "the vault is
    not readable" from "the client is not connecting" without any MCP involved.
    """
    server._ensure_vault()
    status = server.get_health_status()
    vault = status["vault"]
    print(f"Vault:  {vault['name']}")
    print(f"Notes:  {vault.get('note_count', '?')}")
    size = vault.get("size_bytes")
    if isinstance(size, int):
        print(f"Size:   {size / 1_048_576:.1f} MiB of markdown")
    provider = server.provider
    tags = provider.tags()[:8] if provider else []
    if tags:
        print("Tags:   " + ", ".join(f"#{tag.tag} ({tag.count})" for tag in tags))
    print(f"Status: {status['status']}")
    return 0 if status["status"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
