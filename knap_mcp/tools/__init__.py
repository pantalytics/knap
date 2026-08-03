"""MCP tool layer. Imports the protocol, never a concrete backend."""

from .handler import VaultToolHandler, register_tools

__all__ = ["VaultToolHandler", "register_tools"]
