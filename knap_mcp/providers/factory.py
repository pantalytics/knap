"""Backend selection from config.

One function, and it stays one function: the tool layer must never import a
concrete backend, and this is the seam that lets it not. The hosted package does
not use this at all -- it builds a provider per workspace in its own handler --
which is why the signature takes a config rather than reading the environment.
"""

from __future__ import annotations

from ..config import KnapConfig
from ..error_handling import ConfigurationError
from .protocol import VaultProvider


def create_vault_provider(config: KnapConfig) -> VaultProvider:
    """Build the provider named by ``config.vault_provider``."""
    if config.vault_provider == "filesystem":
        from .filesystem.provider import FilesystemVaultProvider

        return FilesystemVaultProvider(
            config.vault_root,
            vault_id="default",
            name=config.display_name,
            max_body_chars=config.max_body_chars,
            max_attachment_bytes=config.max_attachment_bytes,
            max_scan_notes=config.max_scan_notes,
        )
    raise ConfigurationError(
        f"Unknown vault provider {config.vault_provider!r}. Supported: filesystem"
    )
