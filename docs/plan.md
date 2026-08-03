# Knap -- plan and setup

Status: **Phase 1 built and green.** The public package works standalone: nineteen
tools over stdio or HTTP against a vault on disk, 309 tests, an MCP handshake
smoke test. Phases 2 to 4 below are still the plan.

## What it is

**Knap, for Obsidian.**

An Obsidian vault, hosted on Hetzner, that an AI can reach over MCP **from a
phone**. The vault is plain markdown on our disk; `/mcp` is the endpoint Claude,
ChatGPT and any other MCP client authenticate to; the Obsidian app on the
customer's laptop and phone keeps agreeing with it over a sync transport we own.

Primary user: us. Built so it can be sold, which means every decision that would
be cheap now and expensive with fifty customers (tenancy, secrets at rest, usage
diagnostics, staging) is taken now, exactly as `squirrel-mcp-admin` took them.

Same open-core split as the other two:

| | public package | private hosted layer |
|---|---|---|
| Odoo | `odoo-mcp-pro` | `odoo-mcp-pro-admin` |
| Mail/cal/contacts | `squirrel-mcp` | `squirrel-mcp-admin` |
| **Vaults** | **`knap-mcp`** (this repo) | **`knap-mcp-admin`** (Phase 2) |

This repo is the public package and is private today. It should flip to public
at v0.1.0, the way the other two are, or the open-core story is a claim rather
than a fact.

## Mobile is the constraint, not a nice-to-have

Everything below follows from one fact, which was checked rather than assumed:

**On iOS, Obsidian can only open a vault inside its own app container or its own
iCloud container.** It cannot be pointed at an arbitrary folder in Files. So no
file-sync tool on the phone can put notes where Obsidian iOS will read them. The
only mechanism that reaches an iOS vault is **a plugin running inside Obsidian**.
([feature request, still open](https://forum.obsidian.md/t/feature-open-an-existing-vault-that-is-not-in-icloud-obsidian-folder-but-rather-inside-documents/53585),
[iCloud container behaviour](https://21obsidian.com/en/blog/obsidian-icloud-sync))

Android is looser: the vault still lives in the app sandbox, but Syncthing can
bookmark that folder, or the vault can sit in `Documents`. So Android has a
plain-file escape hatch that iOS does not.
([Syncthing + Obsidian on Android](https://softhints.com/how-to-sync-obsidian-notes-across-multiple-devices-with-syncthing-a-step-by-step-guide/))

Three consequences, and they decide the architecture:

1. **Git is out as the sync answer.** `obsidian-git` on mobile runs
   `isomorphic-git` inside the app, has no SSH, and is documented as unstable
   with a vault-size limit. It stays in the design, but as the history and
   backup mirror and the desktop escape hatch, not as how a phone gets its
   notes. ([mobile implementation](https://deepwiki.com/Vinzent03/obsidian-git/3.2-mobile-implementation))
2. **A plugin is not optional, it is the only mechanism.** Which means either
   Self-hosted LiveSync (mature, exists, mobile-proven) or one we write. Writing
   a correct two-way file sync is the hard part of this whole product, and
   LiveSync being complex and Remotely Save being buggy is the evidence, not a
   coincidence. **We use LiveSync.**
3. **The phone works before any of that.** Claude on iOS talks MCP over HTTPS to
   the hosted vault. Asking your notes a question and capturing a thought are
   both MCP tool calls and need no Obsidian app and no sync at all. That is
   Phase 2, and it is the shortest path to "it works on my phone".

## Architecture

Plain markdown on disk is the source of truth, permanently. Every sync transport
is a projection onto it. That single invariant is what keeps mobile possible: if
CouchDB were the store, search would be slow and the index would be a rewrite;
if git were the store, iOS would be impossible.

```
  Obsidian iOS / Android / desktop
            │
            │  Self-hosted LiveSync plugin (the only thing that reaches an iOS vault)
            ▼
     CouchDB, one database per vault
            │
            │  bridge (ours) -- the only component that knows LiveSync's format
            ▼
     tree/  plain .md on a Hetzner volume   ◄────── MCP tools ──────  Claude on your phone
            │
            └── git mirror: version history, off-box backup, desktop escape hatch
```

Why a bridge to files rather than a CouchDB-backed provider:

- Search and the link index over files are cheap. Over chunked CouchDB documents
  they are neither cheap nor testable.
- LiveSync's format knowledge stays in one process, the same way
  `providers/filesystem/` is the only thing that knows about the filesystem. When
  the format changes, one component breaks.
- Git can sit on `tree/` for free, which gives the version history LiveSync does
  not have. If the bridge ever breaks, the notes are still plain files.

### The two risks of this design, named up front

1. **The sync loop.** The bridge writes to CouchDB, LiveSync's change feed echoes
   it back, the bridge writes to disk, the file watcher fires, and it writes to
   CouchDB again. This is *the* bug in this design. It needs a per-document
   revision map and an origin marker, and a test that writes from both ends and
   asserts the system settles rather than oscillates.
2. **Format drift.** LiveSync's chunk format is versioned and undocumented, and
   `obsidian-git-livesync` exists precisely because writing into it from outside
   is not a supported thing to do. Mitigation: pin the plugin version we support,
   name it in the panel, and test the bridge against a real CouchDB and that
   pinned version in CI. ([obsidian-livesync](https://github.com/vrtmrz/obsidian-livesync),
   [obsidian-git-livesync](https://github.com/ecstatic-pirate/obsidian-git-livesync))

And one thing to say out loud rather than bury: **end-to-end encryption is off.**
That is not a LiveSync cost, it is inherent to the product. If an AI reads your
notes on our server, the notes are plaintext to our server. The honest version of
that belongs on the marketing site, not in a FAQ.

### What we copy, and from where

This was read, not guessed. The list is here so a reviewer can check the claim.

| Concern | Copied from | Landing here as |
|---|---|---|
| FastMCP construction seam | `squirrel_mcp/server.py:create_fastmcp_app` | `knap_mcp/server.py`, same signature |
| Provider protocol as the swappable seam | `squirrel_mcp/providers/protocol.py` | `knap_mcp/providers/protocol.py`, `VaultProvider` |
| Tools as mixins on one handler | `squirrel_mcp/tools/handler.py` + `tools/mail/*` | `knap_mcp/tools/handler.py` + `tools/vault/*` |
| Per-tenant resolution hook | `MailToolHandler._get_provider` | `VaultToolHandler._get_provider` |
| Usage hook (success path only) | `MailToolHandler._track_usage` | same name, same contract |
| Authenticated subject contextvar | `tools._common._current_sub` | same |
| Server instructions at handshake | `squirrel_mcp/knowledge.py` | `knap_mcp/knowledge.py` |
| Blocking calls off the event loop | `tools/_common.run_blocking` + per-provider lock | same |
| Zitadel OIDC login + PKCE + end_session | `squirrel_mcp_admin/auth.py` | `knap_mcp_admin/auth.py` |
| Bearer introspection (RFC 7662, 60s cache) | `squirrel_mcp_admin/oauth.py` | same file, renamed |
| OAuth discovery + RFC 7591 DCR | `oauth_routes.py` / `dcr.py` | ported, same Zitadel apps |
| AI connector catalog + live status | `connectors.py` | ported, copy changed |
| Onboarding metro map | `onboarding.py` | five stations, last one is a vault question |
| Admin UI shell, incl. mobile drawer | `templates/brand_base.html`, `_brand_head.html`, `nav.js`, `_confirm.html`, `_logos.html` | ported 1:1 |
| Brand tokens | `_brand_head.html` | accent `#5b58d8` light / `#9b99ff` dark, bg `#001d21`, Murecho |
| Tailwind build, committed stylesheet | `tailwind.config.js` + `scripts/build_css.sh` | same, CI fails on a stale file |
| Fernet secrets at rest | `encryption.py` | same |
| PostHog usage + error tracking | `usage.py` / `errors.py` / `analytics.py` | same, per-workspace consent |
| Deploy: one Caddy edge + prod/staging stacks on one box | `deploy/docker-compose.{edge,stack}.yml` | same shape, plus CouchDB and a data volume |
| CI: test -> build -> staging -> smoke -> prod, with rollback | `.github/workflows/{ci,deploy}.yml` | same |
| Ship-from-a-phone procedure | `squirrel-mcp-admin/CLAUDE.md` | same `gh pr checks --watch --fail-fast &&` rule |
| Skills as `skill://` MCP resources | `odoo-mcp-pro/mcp_server_odoo/skills.py` | Phase 4 |

Two places where Squirrel and odoo-mcp-pro disagree, and which one wins:

- **Deploy shape.** odoo-mcp-pro-admin does blue-green flip-flop with a 30s
  drain. Squirrel does one long-lived Caddy plus two compose projects on the same
  box, with `pg_dump` before an up and a rollback to the previous image on an
  unhealthy container. **Squirrel wins:** newer, gives staging for free, and it
  is the one a phone can drive end to end.
- **Rules layout.** odoo-mcp-pro-admin splits `CLAUDE.md` into
  `.claude/rules/*.md`; Squirrel keeps one long file. **odoo wins** once this
  repo's `CLAUDE.md` passes roughly 200 lines, not before.

### Public package: `knap_mcp/`

```
knap_mcp/
  __main__.py            CLI: argparse, transport selection
  server.py              create_fastmcp_app() + KnapMCPServer (stdio/http)
  config.py              KnapConfig dataclass + env loading
  knowledge.py           SERVER_INSTRUCTIONS handed to the client at handshake
  schemas.py             pydantic result models
  error_handling.py      error hierarchy
  error_sanitizer.py     message scrubbing (paths are the thing to scrub here)
  logging_config.py      structured logging to stderr
  usage.py               no-op track_event stub; the real one lives in admin
  providers/
    protocol.py          VaultProvider (typing.Protocol) + value objects
    factory.py           picks the backend from KNAP_VAULT_PROVIDER
    filesystem/
      provider.py        the only file that knows about the filesystem
      paths.py           vault-root confinement; every path goes through it
      markdown.py        frontmatter, wikilinks, headings, atomic write
      index.py           lazy link/tag/property index, invalidated on mtime
      search.py          content and property search
  tools/
    _common.py           run_blocking, per-provider lock, limits, _current_sub
    handler.py           VaultToolHandler (mixins) + register_tools
    vault/
      browse.py          list_folders, list_notes, list_vaults
      query.py           search
      read.py            read_note, read_chunk
      write.py           create_note, append_note, update_note, patch_section,
                         set_properties
      organize.py        move_note, delete_note
      graph.py           backlinks, links, list_tags
      periodic.py        daily_note
      attachments.py     get_attachment, put_attachment
```

Standalone it runs over stdio against one vault directory from the environment.
That is immediately useful without any of the hosted layer: point it at
`pantalytics-second-brain` and Claude Code can read and write it today.

### The sync seam

`SyncTransport` sits beside `VaultProvider` in the hosted package, so Phase 3 is
an implementation rather than a rewrite. Both transports project onto the same
`tree/`:

```python
class SyncTransport(Protocol):
    def bootstrap(self, vault: Vault) -> None: ...      # create the remote side
    def push(self, vault: Vault, paths: list[str]) -> None: ...   # our write outward
    def status(self, vault: Vault) -> SyncStatus: ...   # what the panel shows
    def teardown(self, vault: Vault) -> None: ...
```

- `GitTransport` -- bare repo per vault, `git-http-backend` behind Caddy
  `forward_auth`, per-vault token Fernet-encrypted and shown once. History,
  off-box backup, and the desktop escape hatch.
- `LiveSyncTransport` -- one CouchDB database per vault, plus the bridge process.
  This is the one that reaches a phone.

A vault may run both at once, and normally will.

### Tool surface

One namespace, `vault_*`. Not `obsidian_*`: the tools only ever touch the
protocol, and a plain markdown folder or a Logseq graph satisfies it too.

| Tool | Notes |
|---|---|
| `vault_list_vaults` | The Squirrel `mail_list_accounts` analogue. Every tool takes an optional `vault`; omit it with one, required with several. |
| `vault_list_folders` | Folder tree, so the client can resolve a path before writing one. |
| `vault_list_notes` | Paginated. A vault holds 10k notes; nothing returns all of them. |
| `vault_search` | Free text, plus `tag`, `property` (frontmatter key/value) and `since`. Paginated, `total` = matches. |
| `vault_read_note` | Body truncated at a limit, with frontmatter, tags, outgoing links and a `rev`. |
| `vault_read_chunk` | Pages a long note. Same shape as `mail_read_chunk`. |
| `vault_create_note` | Refuses when the path exists. Not destructive, so no confirm. |
| `vault_append_note` | Appends to the end, or under `section`. Creates the note when missing. Not destructive. **The phone's most-used tool.** |
| `vault_update_note` | Replaces the body. **Destructive**: `confirm=true` + `expected_rev`. |
| `vault_patch_section` | Replace/append/prepend under one heading. The tool that makes an AI edit read like a human edit. |
| `vault_set_properties` | Frontmatter only, body untouched. |
| `vault_move_note` | **Destructive.** Rewrites inbound links by default and reports which notes changed. |
| `vault_delete_note` | **Destructive.** |
| `vault_backlinks` | What links here. |
| `vault_links` | What this links to, unresolved links included -- an unresolved link is usually the interesting one. |
| `vault_list_tags` | Tag counts, for orientation in someone else's vault. |
| `vault_daily_note` | Resolves the daily/weekly/monthly note for a date from the vault's own Periodic Notes settings, creating it from the template when asked. **The other half of phone capture.** |
| `vault_get_attachment` / `vault_put_attachment` | Binaries by path. |

Seven design calls that look incidental and are not:

1. **Wikilinks are the product.** `[[Note]]` resolution is Obsidian's, not ours:
   shortest-unique-path, `aliases:` frontmatter, `#heading` and `^block`
   anchors, `![[embed]]`. A move that does not rewrite inbound links silently
   breaks the graph, and the graph is why someone uses Obsidian rather than a
   folder of text files. `vault_move_note` rewrites by default and says what it
   touched.
2. **Two writers, always** -- and with a phone in the mix, three. Every read
   hands back a `rev` (mtime + content hash); every body-replacing write takes
   `expected_rev` and refuses on a mismatch rather than clobbering. Optimistic
   concurrency, no locks, and the failure is a sentence the AI can read out loud.
3. **Every write is atomic.** Temp file in the same directory, then
   `os.replace`. Obsidian's file watcher, the LiveSync bridge and git all cope
   badly with a half-written note, and a truncated note looks exactly like data
   loss.
4. **Confirm before it cannot be got back.** Delete, move and body-replacing
   update need `confirm=true` and are flagged `destructiveHint`. Create-new,
   append, patch-section and set-properties do not, because they are additive and
   reversible -- gating them would only teach clients that the confirm prompt is
   noise. This matters more on a phone, where a confirmation is a tap on a small
   screen and every unnecessary one trains the habit of tapping through.
5. **No path leaves the vault.** Every path argument goes through
   `paths.resolve_in_vault`: resolve, then confirm it is still under the root,
   refuse symlinks pointing out, refuse absolute paths and `..`. This is the
   SSRF-guard of a file-backed server and the one vulnerability class that ends
   with one customer reading another's notes. It gets its own regression file.
6. **`.obsidian/` is configuration, not content.** Excluded from listing and
   search by default, reachable on purpose, because "which plugins does this
   vault use" is a real question -- and on a hosted vault it is also how the
   panel checks whether LiveSync is installed and at which version.
7. **We do not evaluate Dataview or Bases.** A query language embedded in notes
   needs its plugin. What `vault_search` does instead is frontmatter properties,
   which covers most of what people actually ask Dataview for. Saying so in the
   handshake instructions is cheaper than a client inventing a query it cannot
   run.

### Hosted layer: `knap-mcp-admin/`

File-for-file the Squirrel admin package, with mail replaced by vaults:

- `__main__` -> `mcp_server.run_multi_tenant()` -- composition root: the public
  FastMCP factory with the multi-tenant handler, the admin FastAPI app mounted
  under it, one uvicorn.
- `mcp_handlers.MultiTenantVaultToolHandler` -- overrides `_get_provider` to
  resolve a per-workspace vault directory from Postgres, cached per (subject,
  vault); `_list_vaults`; `_track_usage`.
- `storage.py` -- **new.** The per-workspace directory on the data volume, the
  quota check on every write, and the fact that the admin process is the only
  thing that ever computes a vault path from a workspace id.
- `sync/` -- **new.** `transport.py` (the protocol above), `git.py`,
  `livesync/` (`couch.py` for replication, `chunks.py` for the format,
  `bridge.py` for the loop-safe two-way projection).
- `app.py` / `routes.py` / `templates/` -- five pages, below.
- `auth.py`, `oauth.py`, `oauth_routes.py`, `dcr.py`, `connectors.py`,
  `onboarding.py`, `encryption.py`, `analytics.py`, `usage.py`, `errors.py`,
  `db/` -- ported.

Pages. Squirrel's panel is already responsive (hamburger plus slide-over
drawer), which ports as-is, but phone-first here means more than that: the
panel is something you use *on* the phone, not something that survives being
opened on one.

- **Home** (`/`, where login lands) is the metro line: sign up · sign in ·
  connect an AI · add a vault · ask the first question. First two drawn done
  because they are. Last station reads `workspaces.last_tool_call_at`.
- **Capture** (`/capture`) -- **new, and the reason the phone is worth it.** One
  text box that appends to today's daily note, with the send button under your
  thumb. No AI, no Obsidian app, no sync involved: a POST that calls
  `vault_daily_note` and `vault_append_note`. It is the smallest thing in the
  product and probably the most used. Installable as a PWA so it is one tap from
  the home screen.
- **AI connectors** (`/connectors`) -- the AI clients that talk to us, with
  instructions written to be followable *on a phone*: copy the MCP URL, open
  Claude iOS settings, paste. The desktop-only wording is a bug here.
- **Vaults** (`/vaults`) -- per vault: size and note count against quota, sync
  status per transport, the git remote with its token shown once, and the
  CouchDB URL plus credentials for the LiveSync plugin, as a QR code, because
  typing a CouchDB URL and key on a phone keyboard is where this flow otherwise
  dies. **Adding a vault asks one question: where does it come from.** Start
  empty, push an existing vault to the remote we just made, or import from a git
  URL we clone once.
- **Settings** -- workspace name, usage diagnostics switch.

### Data model

```
accounts            the person (Zitadel subject)
workspaces          the tenant boundary; plan, analytics_enabled, last_tool_call_at
teams               exists so members can be switched on later, no UI creates one
workspace_members   membership + role
vaults              workspace_id, slug, display_name, quota_bytes, size_bytes,
                    note_count, default (one per workspace), created_at
vault_transports    vault_id, kind ('git' | 'livesync'), enabled, secret_enc
                    (Fernet: git token or CouchDB password), endpoint,
                    last_push_at, last_pull_at, head_sha, couch_seq, status
ai_connectors       one row per AI client per workspace, live status + call count
```

`vaults.slug` is ours, not the customer's: it is a path component. The display
name is theirs and may contain anything. `couch_seq` is the bridge's place in the
CouchDB change feed and is the thing that must survive a container restart, or
the bridge replays a vault's whole history on every deploy.

### Deploy

Squirrel's shape, plus what a notes product cannot skip.

- One long-lived Caddy compose project holding 80/443 and both certificates.
- Two app stacks, same image, same box, separate databases and volumes:
  `knap-mcpd-*` and `obsidian-staging-*`. CouchDB joins each stack.
- `deploy/remote-deploy.sh`: pull, `pg_dump`, up, health-check, roll back to the
  previous image if unhealthy.
- Merge to `main` -> CI -> build -> staging -> `scripts/smoke_public.py` -> prod.
  Production physically cannot receive red code because each job `needs:` the one
  before it.
- `/healthz` reports the running commit, whether the data volume is mounted and
  writable, and whether the bridge is caught up. An env file being correct and
  the running process having read it are different facts, and this one is asked
  from a phone right after an edit.

Three deliberate differences from Squirrel:

1. **The vault volume is an external named volume**, declared once and never
   inside a compose file that a deploy tears down. Squirrel namespaces its
   Postgres volume per project so staging cannot touch prod; here that is not
   enough, because a `docker compose down -v` in the wrong shell must not be
   able to delete a customer's notes.
2. **Off-box backups from day one.** `restic` to a Hetzner Storage Box, hourly,
   covering `tree/`, the git mirrors and CouchDB, with a restore rehearsed before
   the first customer. Losing a Postgres row is an outage; losing someone's
   second brain is the end of the product.
3. **CouchDB is a stateful service we now operate.** It needs its own volume, its
   own backup, a memory limit so it cannot starve the app on a shared box, and
   CORS configured for the Obsidian clients. It is the biggest operational cost
   of choosing mobile, and it is worth it.

Sizing: a CPX-class box plus a Hetzner Volume, which is resizable. Notes are
tiny; attachments and CouchDB's revision history are not.

### Security posture, stated up front

- **No auto-provisioning on an unknown subject.** The Zitadel instance is shared
  with odoo-mcp-pro and Squirrel, so any of those products' tokens introspects
  here. Squirrel learned this the hard way and now hard-rejects; we start
  rejecting. A subject with no workspace gets an error, not a free vault.
- **Path confinement is the audit.** `tests/test_path_safety.py` in the public
  package, and `test_tenant_isolation.py` in admin proving over real HTTP that a
  valid session for workspace A pointed at workspace B's vault id misses on read,
  write, move and delete.
- **One CouchDB database per vault, with its own user.** A shared database with
  document-level filtering is one misconfigured filter away from handing a phone
  somebody else's notes. Per-database credentials make the isolation the same
  kind of thing as the filesystem isolation: structural, not conditional.
- **Secrets at rest**: git tokens and CouchDB passwords Fernet-encrypted, with a
  test that reads the raw column to prove it.
- **A note path is content.** `Clients/Acme/2026 renewal.md` is a fact about
  somebody's business, so paths are hashed in analytics events and replaced in
  error reports. Stricter than Squirrel's scrubber, which keeps hosts because a
  host is what you need to fix a mailbox; here the useful pair is the tool and
  the error type.
- Accepted risk, authenticated only: an imported git URL is a user-supplied
  outbound fetch, so it goes through the `_is_unreachable_host()` SSRF guard
  before we clone it.

### Testing

Squirrel's layers, one per honest question:

- **Unit** (`tests/`) -- `FakeVaultProvider`, no filesystem.
- **Integration** (`tests/integration/`, marker `integration`) -- a real seeded
  vault in a tmpdir: wikilink resolution, link rewriting on move, atomic write
  under a concurrent writer, and every path-escape attempt. The GreenMail
  analogue is just a directory, so this layer is cheap and there is no excuse for
  skipping it.
- **Bridge tests** (admin, needs Docker) -- a real CouchDB and the pinned
  LiveSync format. Two assertions carry the design: a write from either end
  arrives at the other, and **a write from either end settles rather than
  echoing**. The second one is the sync loop, and a mock cannot prove it.
- **Docker smoke** -- MCP handshake plus `vault_list_notes` against the
  container.
- **Admin `make test-db`** -- migrations, tenant isolation over HTTP, secrets at
  rest, analytics consent, connector SQL, token rotation. CI runs it with
  `REQUIRE_DB=1` so a lost database is a failure rather than a skip that reports
  green.

## Phases

**Phase 1 -- the public package, useful on its own. DONE.** `VaultProvider`
protocol, filesystem backend, the nineteen tools, stdio and streamable-http, 309
tests, Dockerfile, CI. `python -m knap_mcp --vault PATH` serves a vault to Claude
Code today, and `scripts/mcp_smoke.py` drives the server as a subprocess through
a real MCP handshake.

Five things the tests found that the design had wrong, all fixed and pinned:

* `[[#Log]]` was read as a link to a note named "#Log", so every note with a
  table of contents reported a broken link.
* `[text](#section)` grew a tag per entry, for the same reason.
* The index was walked with `include_hidden=False`, which meant
  `include_hidden=True` on a search had nothing to find.
* `[[index]]` inside a subfolder resolved to the vault-root `index.md` instead of
  the sibling.
* A move "relinked" notes whose links were already correct: identical bytes, a
  bumped mtime, and a result claiming we touched files we had not.

The container is verified too, by CI rather than locally: this sandbox has no
Docker daemon, so the first real build of the image was the `docker` job, which
also starts a container against a mounted vault and waits for it to answer its own
healthcheck. Green on the first run.

One step is deliberately local: `--check` against the real
`pantalytics-second-brain`. Pulling a private second brain into a build sandbox is
not a thing to do casually, so that is the one thing to run on your own machine.

**Phase 2 -- the phone, without any sync.** `knap-mcp-admin`: Postgres,
Zitadel login, the multi-tenant handler, the pages, the git transport for
desktop, the data volume, restic, prod and staging, the deploy pipeline, and the
Capture PWA. Done when Claude on the iPhone answers a question from our vault and
a thought typed on the train lands in today's daily note. **This is already "it
works on mobile" for the reason the product exists.**

**Phase 3 -- the Obsidian app on the phone.** CouchDB per vault, the bridge, the
QR-code handoff to the LiveSync plugin, sync status in the panel, and the bridge
test suite. Done when a note written on the iPhone in Obsidian is readable by the
MCP within seconds, and the reverse, and neither one echoes.

**Phase 4 -- sellable.** Metering (the odoo-mcp-pro per-day model, not a
free/paid read-write split), Stripe, and vault **skills**: `skill://` resources
in the odoo-mcp-pro shape, teaching a client how to work in a Zettelkasten, how
to run a weekly review, how to file a meeting note. That is what makes this more
than a filesystem with OAuth, and odoo-mcp-pro already proved people use it.

## Decisions

1. **Sync transport. Settled: LiveSync plus a server-side bridge**, because on
   iOS a plugin is the only mechanism that exists. Git demotes to history,
   backup and the desktop escape hatch. Our own plugin stays the fallback if the
   bridge turns out to fight the format, and the `SyncTransport` seam is there so
   that is a swap rather than a rewrite.
2. **Headless Obsidian on the server. Settled: no.** It would buy Dataview and
   Bases evaluation and cost a container, a GUI stream and a gigabyte of RAM per
   tenant, plus a licensing question. The architecture does not block it later.
3. **Naming. Settled: Knap.** Knapping is the craft of striking flakes off
   obsidian to shape a blade, and *knap* is Dutch for clever. Two languages, both
   pointing at the product, four letters, and the obsidian reference is specific
   without using the trademark. The tagline carries the rest: **Knap, for
   Obsidian**. Obsidian's developer policy forbids a name suggesting a
   first-party product, and "Obsidian Pro" read exactly like a paid tier of
   Obsidian itself -- nothing for our own use, a letter at the point of selling.
   `Scry` and `Jackdaw` were the runners-up and both are taken in adjacent
   categories (Scry AI does enterprise knowledge search; Jackdaw is a Mac
   productivity app); `Memex` and `Lodestone` stay as fallbacks.
   ([Obsidian developer policies](https://docs.obsidian.md/Developer+policies))

   Landed in the code: distribution `knap-mcp`, package `knap_mcp`, FastMCP
   server name `knap`, env prefix `KNAP_*`, config class `KnapConfig`, private
   layer `knap-mcp-admin`. Exactly Squirrel's shape (`squirrel-mcp` /
   `squirrel_mcp` / `SQUIRREL_*` / `squirrel-mcp-admin`). The tool prefix stays
   `vault_*` rather than `knap_*`, for the same reason Squirrel's tools are
   `mail_*`: the tools name what they touch, not who ships them.

   **Two things left, both outside a commit.** The GitHub repo is still called
   `obsidian-pro` and wants renaming to `knap-mcp` (GitHub redirects the old
   remote, so nothing breaks in the meantime). And a trademark-register check has
   not been done -- what was checked is whether the name is already used in an
   adjacent category, which is not the same question.

## Sources

- [Obsidian iOS: vault must be in the app's own container](https://forum.obsidian.md/t/feature-open-an-existing-vault-that-is-not-in-icloud-obsidian-folder-but-rather-inside-documents/53585)
- [Obsidian iCloud container behaviour](https://21obsidian.com/en/blog/obsidian-icloud-sync)
- [Syncthing + Obsidian on Android](https://softhints.com/how-to-sync-obsidian-notes-across-multiple-devices-with-syncthing-a-step-by-step-guide/)
- [obsidian-livesync](https://github.com/vrtmrz/obsidian-livesync)
- [obsidian-git-livesync](https://github.com/ecstatic-pirate/obsidian-git-livesync)
- [obsidian-git mobile implementation](https://deepwiki.com/Vinzent03/obsidian-git/3.2-mobile-implementation)
- [remotely-save](https://github.com/remotely-save/remotely-save)
- [Obsidian developer policies](https://docs.obsidian.md/Developer+policies)
- [Obsidian brand guidelines](https://obsidian.md/brand)
