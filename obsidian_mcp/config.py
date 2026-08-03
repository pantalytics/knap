"""Configuration for the Obsidian Pro MCP server.

Loads and validates environment variables (optionally from a .env file) into a
single ``ObsidianConfig``. No hardcoded fallbacks: a missing vault path is a
config error, never a guessed directory. Guessing here would mean opening a
directory the customer did not name, and on a hosted deployment that is somebody
else's vault.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from dotenv import load_dotenv

# Backends this package knows how to build. "filesystem" is a plain vault
# directory -- the only one v1 ships, and the one every hosted vault is too.
SUPPORTED_VAULT_PROVIDERS = ("filesystem",)


@dataclass
class ObsidianConfig:
    """Vault location plus MCP server settings."""

    # Backend selection
    vault_provider: str = "filesystem"

    # The vault. Required unless skip_validation.
    vault_path: str = ""
    # What the client calls it. Defaults to the directory name, which is what
    # Obsidian shows too.
    vault_name: str = ""

    # Behaviour
    log_level: str = "INFO"
    default_limit: int = 25
    max_limit: int = 100
    # A single note can be longer than a context window, so reads truncate and
    # the client pages the rest with vault_read_chunk.
    max_body_chars: int = 20000
    # Ceiling on one attachment, so a 400MB video in someone's vault cannot be
    # asked for by accident.
    max_attachment_bytes: int = 10 * 1024 * 1024
    # Search walks the vault. This caps how many notes one query will open, and
    # the result says when it hit the cap rather than pretending it saw the rest.
    max_scan_notes: int = 20000

    # MCP transport
    transport: Literal["stdio", "streamable-http"] = "stdio"
    host: str = "localhost"
    port: int = 8000

    # Skip validation for multi-tenant mode (no single vault at startup).
    skip_validation: bool = False

    def __post_init__(self):
        """Validate. Nothing is guessed -- the vault path is explicit or absent."""
        if self.skip_validation:
            return

        if self.vault_provider not in SUPPORTED_VAULT_PROVIDERS:
            raise ValueError(
                f"Unknown OBSIDIAN_VAULT_PROVIDER: {self.vault_provider!r}. "
                f"Supported: {', '.join(sorted(SUPPORTED_VAULT_PROVIDERS))}"
            )

        if not self.vault_path:
            raise ValueError("OBSIDIAN_VAULT_PATH is required (the vault directory)")

        root = Path(self.vault_path).expanduser()
        if not root.is_dir():
            raise ValueError(f"OBSIDIAN_VAULT_PATH is not a directory: {self.vault_path}")

        if self.port <= 0 or self.port > 65535:
            raise ValueError("OBSIDIAN_MCP_PORT must be between 1 and 65535")

        if self.default_limit <= 0:
            raise ValueError("OBSIDIAN_MCP_DEFAULT_LIMIT must be positive")
        if self.max_limit <= 0:
            raise ValueError("OBSIDIAN_MCP_MAX_LIMIT must be positive")
        if self.default_limit > self.max_limit:
            raise ValueError("OBSIDIAN_MCP_DEFAULT_LIMIT cannot exceed OBSIDIAN_MCP_MAX_LIMIT")

        valid_log_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if self.log_level.upper() not in valid_log_levels:
            raise ValueError(
                f"Invalid log level: {self.log_level}. "
                f"Must be one of: {', '.join(sorted(valid_log_levels))}"
            )

        valid_transports = {"stdio", "streamable-http"}
        if self.transport not in valid_transports:
            raise ValueError(
                f"Invalid transport: {self.transport}. "
                f"Must be one of: {', '.join(sorted(valid_transports))}"
            )

    @property
    def vault_root(self) -> Path:
        """The vault directory, resolved. Every path check starts from here."""
        return Path(self.vault_path).expanduser().resolve()

    @property
    def display_name(self) -> str:
        """What to call the vault (falls back to the directory name)."""
        return self.vault_name or self.vault_root.name

    @classmethod
    def from_env(cls, env_file: Optional[Path] = None) -> "ObsidianConfig":
        return load_config(env_file)


def _get_int_env(key: str, default: int) -> int:
    value = os.getenv(key)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{key} must be a valid integer") from None


def load_config(env_file: Optional[Path] = None) -> ObsidianConfig:
    """Load configuration from environment variables and an optional .env file."""
    if env_file:
        if not env_file.exists():
            raise ValueError(
                f"Configuration file not found: {env_file}\n"
                "Create a .env file based on .env.example."
            )
        load_dotenv(env_file)
    else:
        default_env = Path(".env")
        if default_env.exists():
            load_dotenv(default_env)
        elif not os.getenv("OBSIDIAN_VAULT_PATH"):
            raise ValueError(
                "No .env file found and OBSIDIAN_VAULT_PATH not set in environment.\n"
                "Create a .env file based on .env.example or set environment variables."
            )

    return ObsidianConfig(
        vault_provider=os.getenv("OBSIDIAN_VAULT_PROVIDER", "filesystem").strip() or "filesystem",
        vault_path=os.getenv("OBSIDIAN_VAULT_PATH", "").strip(),
        vault_name=os.getenv("OBSIDIAN_VAULT_NAME", "").strip(),
        log_level=os.getenv("OBSIDIAN_MCP_LOG_LEVEL", "INFO").strip(),
        default_limit=_get_int_env("OBSIDIAN_MCP_DEFAULT_LIMIT", 25),
        max_limit=_get_int_env("OBSIDIAN_MCP_MAX_LIMIT", 100),
        max_body_chars=_get_int_env("OBSIDIAN_MCP_MAX_BODY_CHARS", 20000),
        max_attachment_bytes=_get_int_env("OBSIDIAN_MCP_MAX_ATTACHMENT_BYTES", 10 * 1024 * 1024),
        max_scan_notes=_get_int_env("OBSIDIAN_MCP_MAX_SCAN_NOTES", 20000),
        transport=os.getenv("OBSIDIAN_MCP_TRANSPORT", "stdio").strip(),  # type: ignore[arg-type]
        host=os.getenv("OBSIDIAN_MCP_HOST", "localhost").strip(),
        port=_get_int_env("OBSIDIAN_MCP_PORT", 8000),
    )


# Singleton configuration instance.
_config: Optional[ObsidianConfig] = None


def get_config() -> ObsidianConfig:
    """Get the singleton configuration instance, loading it on first use."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def set_config(config: ObsidianConfig) -> None:
    """Set the singleton configuration instance (primarily for testing)."""
    global _config
    _config = config


def reset_config() -> None:
    """Reset the singleton configuration instance (primarily for testing)."""
    global _config
    _config = None
