# Knap

**Knap, for Obsidian.** An MCP server over an Obsidian vault. Your notes are plain
markdown on disk; this hands them to Claude, ChatGPT or any other MCP client, with
the links, tags and frontmatter intact.

Knapping is the craft of striking flakes off obsidian to shape a blade. *Knap* is
also Dutch for clever. Both fit.

> **Status: planning.** The design is settled and written down in
> [docs/plan.md](docs/plan.md); the implementation starts at Phase 1. What exists
> in this repo today is the provider contract, the configuration, the FastMCP
> factory and the handshake instructions -- the seams everything else hangs off.

## What it does

- **Read and search** notes by text, tag, folder, frontmatter property or date,
  paginated, so a vault with thousands of notes stays usable.
- **Write like a person would.** Append to a section, patch one heading, set a
  frontmatter property. Rewriting a whole note to change two lines is possible
  and is not the default.
- **Keep the graph honest.** Backlinks, outgoing links, unresolved links, and a
  move that rewrites every note pointing at the old path.
- **Daily notes** resolved from the vault's own settings, so a note lands where
  your Obsidian would have put it.

Deliberately not included: Dataview and Bases evaluation. Those run inside
Obsidian. Searching frontmatter properties covers most of what they get used for,
and the server says so at the handshake rather than letting a client write a
query it cannot run.

## Two writers

Your Obsidian and the AI edit the same files. Every read hands back a revision
marker, every body-replacing write requires it back, and a mismatch is a refusal
rather than a merge. Writes are atomic, because a half-written note looks exactly
like data loss. Delete, move and body-replacing update ask for confirmation;
create, append and patch do not.

## Open core

This is the public package: one vault, from environment variables, over stdio or
HTTP. The hosted multi-tenant service -- vaults on Hetzner, Zitadel login, git
remotes, per-workspace isolation, usage diagnostics -- lives in the private
`knap-mcp-admin` repo and extends this one through documented seams only.

Same split as [odoo-mcp-pro](https://github.com/pantalytics/odoo-mcp-pro) and
[squirrel-mcp](https://github.com/pantalytics/squirrel-mcp).

## Development

```bash
make install
make lint
make test
```

Read [CLAUDE.md](CLAUDE.md) for the architecture and conventions, and
[docs/plan.md](docs/plan.md) for the phase plan and the open decisions.

By [Pantalytics](https://pantalytics.com).
