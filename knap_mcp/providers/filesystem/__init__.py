"""The filesystem vault backend.

The only package that knows a vault is a directory. ``paths`` confines every
path to the vault root, ``markdown`` reads and writes a note, ``index`` keeps the
link and tag graph, ``search`` answers queries, ``periodic`` resolves daily notes
from the vault's own settings, and ``provider`` composes them into a
``VaultProvider``.
"""

from .provider import FilesystemVaultProvider

__all__ = ["FilesystemVaultProvider"]
