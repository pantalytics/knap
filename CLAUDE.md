# CLAUDE.md -- Instructions for Claude Code

## What this project is

**Knap** (`knap-mcp`) -- an MCP server that hands an Obsidian vault to Claude and
any other MCP client. The vault is plain markdown on disk; the tools read, search,
write and relink it.

The name is the product's one joke and it earns its keep: knapping is the craft of
striking flakes off obsidian to shape a blade, and *knap* is Dutch for clever. It
deliberately does not lead with "Obsidian", because Obsidian's developer policy
forbids a name that suggests a first-party product, and "Obsidian Pro" reads
exactly like a paid tier of Obsidian itself. Say "Knap, for Obsidian" -- the
vault app is what we work on, not what we are called.

**This file describes this package and nothing else.** `knap-mcp` is the public
half of an open-core split, the same one as `odoo-mcp-pro` /
`odoo-mcp-pro-admin` and `squirrel-mcp` / `squirrel-mcp-admin`. A private
package imports this one as a tag-pinned dependency and adds the hosted,
multi-tenant service on top of it, through the seams listed below and no others.

Two consequences, and they are the whole reason this section exists:

- **Do not design this package around the hosted layer.** Its architecture is
  not described here, it changes on its own schedule, and a decision taken there
  is not a decision here. If a change needs something from it, that is a change
  to the seam contract below, and it gets agreed on both sides before it is
  built.
- **Do not put anything private in this repo.** No customer names, no
  infrastructure hostnames, no roadmap for the hosted service. This package is
  meant to be public at v0.1.0 and everything in it should already read as if it
  were.

**It is built and green**: the `VaultProvider` protocol, the filesystem backend,
all nineteen `vault_*` tools, stdio and streamable-http transports, 309 tests,
an MCP handshake smoke test that drives the server as a subprocess, and a
container that CI builds and then proves serves a mounted vault. What is left
here is maintenance and the occasional tool. The build ahead is in the private
package.

## Design principles

1. **The vault is the boss.** Notes are files. We do not own a database of
   content, we do not cache a copy, and Obsidian remains free to edit every byte
   under us. The server is a stateless view over a directory. Plain markdown on
   disk stays the source of truth wherever this runs: anything that syncs a
   vault is a transport projecting onto those files, never a store in its own
   right. That invariant is what keeps search cheap and the phone possible.
2. **Swappable backends.** Tools only ever touch the `VaultProvider` protocol,
   never a concrete filesystem call. The filesystem backend satisfies it today;
   a git-object or object-storage backend can satisfy it later without the tools
   changing.
3. **Two writers, always.** The customer's Obsidian and the AI write the same
   file. Reads hand back a `rev`; body-replacing writes take `expected_rev` and
   refuse on a mismatch. Every write is atomic (temp file in the same directory,
   then `os.replace`) because a half-written note is indistinguishable from data
   loss.
4. **Confirm before it cannot be got back.** Delete, move and body-replacing
   update need `confirm=true` and are flagged `destructiveHint`. Create, append,
   patch-section and set-properties do not: they are additive and reversible,
   and gating them would only teach clients that the confirm prompt is noise.
   The line is "could the user not get this back", not "is this a write".
5. **No path leaves the vault.** Every path argument goes through
   `providers/filesystem/paths.py`. This is the one vulnerability class that
   ends with one customer reading another's notes, so it has its own regression
   file and no shortcuts.
6. **No fallbacks.** Explicit config or a clear error, never a guessed vault
   path.
7. **Open core.** The public package works standalone (stdio, one vault from
   env); the private package adds SaaS features via documented seams only, and
   never forks this code.

## Key architecture facts

- `providers/protocol.py` -- `VaultProvider` (typing.Protocol) plus
  transport-neutral dataclasses. Every backend satisfies it.
- `providers/filesystem/` -- the only place that knows about the filesystem.
  `paths.py` (confinement), `markdown.py` (scanning: links, headings, tags,
  atomic write, revs), `frontmatter.py` (writing properties without rewriting the
  block), `index.py` (lazy link/tag/property index, invalidated on mtime, never
  rebuilt per call), `search.py`, `periodic.py` (daily notes from the vault's own
  settings), `writes.py` (the write half, as a mixin), `provider.py`.
- **The index holds hidden notes and the lookups exclude them.** Indexing with
  `include_hidden=False` meant `include_hidden=True` on a search had nothing to
  find, because the notes were never there. So the walk takes everything, and
  link resolution, backlinks and tag counts skip dot paths: a link must not
  resolve into `.trash`, and a deleted note's tags are not the vault's tags.
- **Wikilink resolution is Obsidian's, not ours.** `[[Note]]` resolves by
  shortest unique path, honours `aliases:` frontmatter, and carries `#heading`
  and `^block` anchors and `![[embed]]` form. A move that does not rewrite
  inbound links silently breaks the graph, which is the whole reason someone
  uses Obsidian over a folder of text files -- so `move` rewrites by default and
  reports which notes it touched.
- `.obsidian/` and `.trash/` are excluded from listing and search by default and
  reachable on purpose: "which plugins does this vault use" is a real question
  and the answer is in there.
- **We do not evaluate Dataview or Bases.** A query language embedded in notes
  needs its plugin. `vault_search` offers frontmatter properties instead, which
  covers most of what people ask Dataview for, and `knowledge.py` says so at the
  handshake rather than letting a client invent a query it cannot run.
- Blocking filesystem calls run off the event loop via
  `tools/_common.run_blocking` (per-provider `asyncio.Lock`).
- Single-tenant: one vault from env vars (stdio or HTTP). The hosted
  multi-tenant deployment lives in the private admin package.

## Open-core extension contract

The admin package subclasses/imports these -- rename only in coordination with it:

- `server.create_fastmcp_app(*, auth=None, token_verifier=None, extra_instructions=None)`
  -- single source of truth for FastMCP construction.
- `tools.handler.VaultToolHandler._get_provider` -- the hook admin overrides to
  resolve a per-workspace vault from the authenticated subject.
- `tools.handler.VaultToolHandler._list_vaults` -- the hook admin overrides to
  list a workspace's vaults.
- `tools.handler.VaultToolHandler._track_usage` -- usage-tracking hook (no-op here).
- `tools._common._current_sub` -- contextvar carrying the authenticated subject.
- `usage.track_event` -- no-op stub here; the real tracker lives in admin.
- `config.KnapConfig` and `server.SERVER_VERSION`.

## Conventions

- Follow existing style (ruff configured in `pyproject.toml`). `ruff format` +
  `ruff check` must pass.
- Tools never import a concrete backend -- only `providers.protocol`.
- Secrets only via env / `.env` (gitignored). No hardcoded fallbacks.
- Keep filesystem code out of the tool layer.
- **Max 500 lines per Python file**, enforced by `scripts/check_max_lines.py` in
  CI. Extract a module rather than raising the limit. No baseline and no
  exemptions: this package starts under the budget, so going over is an error
  rather than a ratchet (odoo-mcp-pro needs the ratchet, we do not).
- The tool mixins declare their seam in `tools/vault/_base.py`, so `ty` resolves
  `self._get_provider` and a typo like `self._trak_usage` is still an error.
  squirrel-mcp switches `unresolved-attribute` off instead; that also hides the
  typo.
- **No em-dashes in user-facing text.** Use a hyphen, comma, or period.
- **User-facing copy must read as human-written.** Run the `humanizer` skill over
  any UI text, tool description or handshake instruction before shipping it.
- Analytics and error reports must never carry a note path or note content: a
  path like `Clients/Acme/2026 renewal.md` is a fact about someone's business.
  Hash it or drop it. This is stricter than Squirrel's scrubber, which keeps
  hosts because a host is what you need to fix a mailbox; here the useful pair
  is the tool and the error type.

## Development

```bash
make install                    # uv venv + dev deps
make lint                       # ruff + ty
make test                       # the whole suite: fake provider + a seeded tmpdir vault
make smoke                      # MCP handshake over stdio against a throwaway vault
make check VAULT=~/vaults/mine  # open a real vault and report what is in it
make docker-build               # build the container image
make test-all
```

## Key files

| File | Role |
|------|------|
| `server.py` | `create_fastmcp_app()` factory, FastMCP setup, stdio/HTTP runners |
| `__main__.py` | CLI entry: argparse, transport selection |
| `config.py` | `KnapConfig` dataclass + env loading |
| `providers/protocol.py` | `VaultProvider` protocol + value objects |
| `providers/filesystem/` | The backend: paths, markdown, frontmatter, index, search, periodic, writes |
| `providers/factory.py` | Backend selection from config |
| `tools/handler.py` | `VaultToolHandler` (mixins) + `register_tools` |
| `tools/vault/` | Tools as mixins: browse, query, read, write, organize, graph, periodic, attachments |
| `tools/_common.py` | `run_blocking`, logger, limits, `_current_sub` |
| `schemas.py` | Pydantic result models |
| `knowledge.py` | Server instructions handed to the MCP client |
| `usage.py` | Usage-tracking stub (full version in admin package) |
| `scripts/mcp_smoke.py` | Real MCP client against the server as a subprocess |
| `scripts/healthcheck.py` | Container liveness (a 4xx from /mcp is healthy) |
| `scripts/check_max_lines.py` | The 500-line budget, enforced |
