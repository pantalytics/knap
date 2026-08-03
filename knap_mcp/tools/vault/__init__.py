"""Vault tools, one mixin per area, composed onto ``VaultToolHandler``.

Split this way for the reason CLAUDE.md gives: roughly 500 lines per file. The
areas are also how somebody actually works in a vault, which is why the handler
registers them in this order -- look around, find, read, write, tidy up.
"""
