# Obsidian Pro -- plan and setup

Status: **draft for approval**. Nothing here is built yet. Three decisions at the
bottom of this file change what Phase 1 looks like; the rest is settled by the
two products this one copies.

## What it is

An Obsidian vault, hosted on Hetzner, that an AI can reach over MCP. The vault
is plain markdown on our disk; `/mcp` is the endpoint Claude, ChatGPT and any
other MCP client authenticate to; the Obsidian app on the customer's laptop and
phone keeps agreeing with it over a sync transport we own.

Primary user: us. Built so it can be sold, which means every decision that would
be cheap now and expensive with fifty customers (tenancy, secrets at rest, usage
diagnostics, staging) is taken now, exactly as `squirrel-mcp-admin` took them.

Same open-core split as the other two:

| | public package | private hosted layer |
|---|---|---|
| Odoo | `odoo-mcp-pro` | `odoo-mcp-pro-admin` |
| Mail/cal/contacts | `squirrel-mcp` | `squirrel-mcp-admin` |
| **Vaults** | **`obsidian-pro`** (this repo) | **`obsidian-pro-admin`** (Phase 2) |

This repo is the public package and is private today. It should flip to public
at v0.1.0, the way the other two are, or the open-core story is a claim rather
than a fact. See Decision 3.

## What we copy, and from where

This was read, not guessed. The list is here so a reviewer can check the claim.

| Concern | Copied from | Landing here as |
|---|---|---|
| FastMCP construction seam | `squirrel_mcp/server.py:create_fastmcp_app` | `obsidian_mcp/server.py`, same signature |
| Provider protocol as the swappable seam | `squirrel_mcp/providers/protocol.py` | `obsidian_mcp/providers/protocol.py`, `VaultProvider` |
| Tools as mixins on one handler | `squirrel_mcp/tools/handler.py` + `tools/mail/*` | `obsidian_mcp/tools/handler.py` + `tools/vault/*` |
| Per-tenant resolution hook | `MailToolHandler._get_provider` | `VaultToolHandler._get_provider` |
| Usage hook (success path only) | `MailToolHandler._track_usage` | same name, same contract |
| Authenticated subject contextvar | `tools._common._current_sub` | same |
| Server instructions at handshake | `squirrel_mcp/knowledge.py` | `obsidian_mcp/knowledge.py` |
| Blocking calls off the event loop | `tools/_common.run_blocking` + per-provider lock | same |
| Zitadel OIDC login + PKCE + end_session | `squirrel_mcp_admin/auth.py` | `obsidian_pro_admin/auth.py` |
| Bearer introspection (RFC 7662, 60s cache) | `squirrel_mcp_admin/oauth.py` | same file, renamed |
| OAuth discovery + RFC 7591 DCR | `oauth_routes.py` / `dcr.py` | ported, same Zitadel apps |
| AI connector catalog + live status | `connectors.py` | ported, copy changed |
| Onboarding metro map | `onboarding.py` | five stations, last one is a vault question |
| Admin UI shell | `templates/brand_base.html`, `_brand_head.html`, `nav.js`, `_confirm.html`, `_logos.html` | ported 1:1 |
| Brand tokens | `_brand_head.html` | accent `#5b58d8` light / `#9b99ff` dark, bg `#001d21`, Murecho |
| Tailwind build, committed stylesheet | `tailwind.config.js` + `scripts/build_css.sh` | same, CI fails on a stale file |
| Fernet secrets at rest | `encryption.py` | same |
| PostHog usage + error tracking | `usage.py` / `errors.py` / `analytics.py` | same, per-workspace consent |
| Deploy: one Caddy edge + prod/staging stacks on one box | `deploy/docker-compose.{edge,stack}.yml` | same shape, plus a data volume |
| CI: test -> build -> staging -> smoke -> prod, with rollback | `.github/workflows/{ci,deploy}.yml` | same |
| Ship-from-a-phone procedure | `squirrel-mcp-admin/CLAUDE.md` | same `gh pr checks --watch --fail-fast &&` rule |
| Skills as `skill://` MCP resources | `odoo-mcp-pro/mcp_server_odoo/skills.py` | Phase 4, see below |

Two places where Squirrel and odoo-mcp-pro disagree, and which one wins:

- **Deploy shape.** odoo-mcp-pro-admin does blue-green flip-flop with a 30s
  drain. Squirrel does one long-lived Caddy plus two compose projects
  (prod/staging) on the same box, with `pg_dump` before an up and a rollback to
  the previous image on an unhealthy container. **Squirrel wins.** It is the
  newer of the two, it gives staging for free, and it is the one a phone can
  drive end to end.
- **Rules layout.** odoo-mcp-pro-admin splits `CLAUDE.md` into
  `.claude/rules/*.md` imports; Squirrel keeps one long file. **odoo wins** once
  this repo's `CLAUDE.md` passes roughly 200 lines, not before.

## Architecture

```
                        ┌──────────────────────────────┐
                        │  Claude / ChatGPT / n8n / …   │
                        └──────────────┬───────────────┘
                                       │ MCP streamable-http
                                       │ OAuth 2.1 + PKCE (Zitadel)
                        ┌──────────────▼───────────────┐
                        │      Caddy (one, shared)     │
                        │  TLS · /mcp · /git · /       │
                        └──────┬────────────────┬──────┘
                     prod      │                │   staging
              ┌───────────────▼─────┐   ┌───────▼──────────────┐
              │ obsidian-prod-admin │   │obsidian-staging-admin│
              │  FastMCP  +  FastAPI│   │                      │
              └───┬─────────┬───────┘   └──────────────────────┘
                  │         │
      ┌───────────▼──┐  ┌───▼────────────────┐   ┌─────────────┐
      │  PostgreSQL  │  │  vault volume      │   │  Zitadel    │
      │ workspaces,  │  │ /srv/obsidian/…    │   │  (shared    │
      │ vaults,      │  │  repo.git + tree/  │   │  with the   │
      │ connectors   │  │                    │   │  other two) │
      └──────────────┘  └────────┬───────────┘   └─────────────┘
                                 │ git over HTTPS
                        ┌────────▼─────────┐   ┌──────────────┐
                        │ Obsidian desktop │   │   PostHog    │
                        │  (obsidian-git)  │   │ usage+errors │
                        └──────────────────┘   └──────────────┘
```

### Public package: `obsidian_mcp/`

```
obsidian_mcp/
  __main__.py            CLI: argparse, transport selection
  server.py              create_fastmcp_app() + ObsidianMCPServer (stdio/http)
  config.py              ObsidianConfig dataclass + env loading
  knowledge.py           SERVER_INSTRUCTIONS handed to the client at handshake
  schemas.py             pydantic result models
  error_handling.py      error hierarchy
  error_sanitizer.py     message scrubbing (paths are the thing to scrub here)
  logging_config.py      structured logging to stderr
  usage.py               no-op track_event stub; the real one lives in admin
  providers/
    protocol.py          VaultProvider (typing.Protocol) + value objects
    factory.py           picks the backend from OBSIDIAN_VAULT_PROVIDER
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
| `vault_append_note` | Appends to the end, or under `section`. Creates the note when missing. Not destructive. |
| `vault_update_note` | Replaces the body. **Destructive**: `confirm=true` + `expected_rev`. |
| `vault_patch_section` | Replace/append/prepend under one heading. The tool that makes an AI edit read like a human edit. |
| `vault_set_properties` | Frontmatter only, body untouched. |
| `vault_move_note` | **Destructive.** Rewrites inbound links by default and reports which notes changed. |
| `vault_delete_note` | **Destructive.** |
| `vault_backlinks` | What links here. |
| `vault_links` | What this links to, unresolved links included -- an unresolved link is usually the interesting one. |
| `vault_list_tags` | Tag counts, for orientation in someone else's vault. |
| `vault_daily_note` | Resolves the daily/weekly/monthly note for a date from the vault's Periodic Notes settings, creating it from the template when asked. |
| `vault_get_attachment` / `vault_put_attachment` | Binaries by path. |

Seven design calls that look incidental and are not:

1. **Wikilinks are the product.** `[[Note]]` resolution is Obsidian's, not ours:
   shortest-unique-path, `aliases:` frontmatter, `#heading` and `^block`
   anchors, `![[embed]]`. A move that does not rewrite inbound links silently
   breaks the graph, and the graph is why someone uses Obsidian rather than a
   folder of text files. `vault_move_note` rewrites by default and says what it
   touched.
2. **Two writers, always.** The customer's Obsidian and the AI write the same
   file. Every read hands back a `rev` (mtime + content hash); every
   body-replacing write takes `expected_rev` and refuses on a mismatch rather
   than clobbering. Optimistic concurrency, no locks, and the failure is a
   sentence the AI can read out loud.
3. **Every write is atomic.** Temp file in the same directory, then
   `os.replace`. Obsidian's file watcher and every sync transport cope badly
   with a half-written note, and a truncated note looks exactly like data loss.
4. **Confirm before it cannot be got back.** Squirrel's line, applied here:
   delete, move and body-replacing update need `confirm=true` and are flagged
   `destructiveHint`. Create-new, append, patch-section and set-properties do
   not, because they are additive and reversible -- gating them would only teach
   clients that the confirm prompt is noise. This holds regardless of whether
   the hosting layer has git underneath it; the tool contract does not get to
   depend on that.
5. **No path leaves the vault.** Every path argument goes through
   `paths.resolve_in_vault`: resolve, then confirm it is still under the root,
   refuse symlinks pointing out, refuse absolute paths and `..`. This is the
   SSRF-guard of a file-backed server and the one vulnerability class that ends
   with one customer reading another's notes. It gets its own regression file.
6. **`.obsidian/` is configuration, not content.** Excluded from listing and
   search by default, reachable on purpose, because "which plugins does this
   vault use" is a real question and the answer is in there. Same for `.trash/`.
7. **We do not evaluate Dataview or Bases.** A query language embedded in notes
   needs its plugin. What `vault_search` does instead is frontmatter properties,
   which covers most of what people actually ask Dataview for. Saying so in the
   handshake instructions is cheaper than a client inventing a query it cannot
   run.

### Hosted layer: `obsidian-pro-admin/`

File-for-file the Squirrel admin package, with mail replaced by vaults:

- `__main__` -> `mcp_server.run_multi_tenant()` -- composition root: the public
  FastMCP factory with the multi-tenant handler, the admin FastAPI app mounted
  under it, one uvicorn.
- `mcp_handlers.MultiTenantVaultToolHandler` -- overrides `_get_provider` to
  resolve a per-workspace vault directory from Postgres, cached per (subject,
  vault); `_list_vaults`; `_track_usage`.
- `storage.py` -- **new, and the part Squirrel has no equivalent of.** The
  per-workspace directory on the data volume, the quota check on every write,
  and the fact that the admin process is the only thing that ever computes a
  vault path from a workspace id.
- `git_sync.py` -- **new.** See "Sync" below.
- `app.py` / `routes.py` / `templates/` -- four pages: **Home** (metro map),
  **AI connectors**, **Vaults**, **Settings**.
- `auth.py`, `oauth.py`, `oauth_routes.py`, `dcr.py`, `connectors.py`,
  `onboarding.py`, `encryption.py`, `analytics.py`, `usage.py`, `errors.py`,
  `db/` -- ported.

Pages, and what each is for:

- **Home** (`/`, where login lands) is the metro line: sign up · sign in ·
  connect an AI · add a vault · ask the first question. First two drawn done
  because they are. Last station reads `workspaces.last_tool_call_at`.
- **AI connectors** (`/connectors`) -- the AI clients that talk to us. Table
  plus tile picker plus per-client instructions, flipping to Connected while the
  customer watches.
- **Vaults** (`/vaults`) -- the vaults we hold. Per vault: size and note count
  against quota, the git remote URL with a token shown once, last sync, and the
  last few commits. **Adding one asks a single question: where does it come
  from.** Three answers, and they are the whole product surface: start empty,
  push an existing vault to the remote we just made, or import from a git URL
  we clone once.
- **Settings** -- workspace name, usage diagnostics switch. The plan and the
  signed-in account are facts, not choices.

### Data model

```
accounts            the person (Zitadel subject)
workspaces          the tenant boundary; plan, analytics_enabled, last_tool_call_at
teams               exists so members can be switched on later, no UI creates one
workspace_members   membership + role
vaults              workspace_id, slug, display_name, quota_bytes, size_bytes,
                    note_count, default (one per workspace), created_at
vault_remotes       vault_id, kind ('git'), token_enc (Fernet), last_push_at,
                    last_pull_at, head_sha
ai_connectors       one row per AI client per workspace, live status + call count
```

`vaults.slug` is ours, not the customer's: it is a path component. The display
name is theirs and is allowed to contain anything.

### Sync: how the Obsidian app agrees with the server

The honest summary of the research: this is the only genuinely risky choice in
the whole plan, because none of the three options is both robust and good on
mobile.

- **Git.** Rock solid on desktop via `obsidian-git`. Versioning, diffs and an
  undo history for free, which for a notes product is worth as much as the sync.
  On mobile `obsidian-git` runs `isomorphic-git` in the app, has no SSH, and is
  documented as unstable with a vault-size limit. ([obsidian-git mobile
  implementation](https://deepwiki.com/Vinzent03/obsidian-git/3.2-mobile-implementation))
- **Self-hosted LiveSync (CouchDB).** The best experience by a distance:
  near-real-time, solid on mobile, feels like Obsidian Sync. But it stores
  chunks and metadata rather than files, the format is undocumented, and
  end-to-end encryption has to be **off** for us to read a note at all. Writing
  into it server-side means reimplementing that format; `obsidian-git-livesync`
  proves it is possible and is also the whole warning label.
  ([obsidian-livesync](https://github.com/vrtmrz/obsidian-livesync),
  [obsidian-git-livesync](https://github.com/ecstatic-pirate/obsidian-git-livesync))
- **WebDAV + Remotely Save.** Plain files on our disk, which is perfect, and
  mobile support including iOS. But Remotely Save is reported unmaintained in
  2026 and iOS WebDAV has known failures. Building on it means our sync layer's
  bus factor is someone else's abandoned repo.
  ([remotely-save](https://github.com/remotely-save/remotely-save))

**Recommendation: git for v1**, with a first-party plugin as the Phase 3 answer
to mobile. Reasons, in order: the files stay plain markdown so the MCP side is
trivial and total; `pantalytics-second-brain` is already a git-backed vault, so
v1 fits how we already work; a merge conflict in git is a thing with a name and
a tool, where a conflict in a bespoke sync layer is a support case with two
copies of a note and no history.

Shape:

```
/srv/obsidian/{stack}/{workspace_id}/{vault_slug}/
    repo.git/     bare. What Obsidian clones and pushes to.
    tree/         working tree. What the MCP tools read and write.
```

- Customers clone `https://<host>/git/{vault_id}.git`, authenticating with a
  per-vault token (Fernet-encrypted, shown once, rotatable). Caddy `forward_auth`
  asks the admin app whether that token may touch that vault, then proxies to
  `git-http-backend`.
- An MCP write lands in `tree/`, is committed as `ai: <tool> <path>` and pushed
  to `repo.git`, so the customer's next pull sees it.
- A push from the customer runs a `post-receive` hook that fast-forwards
  `tree/` and invalidates the index.
- **On a real conflict we keep both sides** and say so, writing
  `<name> (conflict 2026-08-03).md` next to the original. An AI that silently
  resolves someone's merge conflict is worse than one that hands them two files
  and a sentence.

### Deploy

Squirrel's, exactly, plus the thing a notes product cannot skip.

- One long-lived Caddy compose project holding 80/443 and both certificates.
- Two app stacks, same image, same box, separate databases and separate volumes:
  `obsidian-prod-*` and `obsidian-staging-*`.
- `deploy/remote-deploy.sh`: pull, `pg_dump`, up, health-check, roll back to the
  previous image if unhealthy.
- Merge to `main` -> CI -> build -> staging -> `scripts/smoke_public.py` -> prod.
  Production physically cannot receive red code because each job `needs:` the one
  before it.
- `/healthz` reports the running commit, and this deployment's answer to "is the
  data volume mounted and writable", because an env file being correct and the
  running process having read it are different facts.

Two deliberate differences from Squirrel:

1. **The vault volume is an external named volume, not a compose-project one.**
   Squirrel's Postgres volume is namespaced per project on purpose, so a staging
   deploy cannot touch prod. Here that is not enough: a `docker compose down -v`
   in the wrong shell must not be able to delete a customer's notes. External
   volume, declared once, never in a compose file that a deploy tears down.
2. **Off-box backups from day one.** `restic` to Hetzner Storage Box, hourly,
   with a restore rehearsed before the first customer. Losing a Postgres row is
   an outage; losing someone's second brain is the end of the product. Sizing:
   a CPX-class box plus a Hetzner Volume, which is resizable -- notes are tiny,
   attachments are not.

### Security posture, stated up front

- **No auto-provisioning on an unknown subject.** The Zitadel instance is shared
  with odoo-mcp-pro and Squirrel, so any of those products' tokens introspects
  here. Squirrel learned this the hard way and now hard-rejects; we start
  rejecting. A subject with no workspace gets an error, not a free vault.
- **Path confinement is the audit.** `tests/test_path_safety.py` in the public
  package and `test_tenant_isolation.py` in admin, the second one proving over
  real HTTP that a valid session for workspace A pointed at workspace B's vault
  id misses on read, write, move and delete.
- **Secrets at rest**: git tokens Fernet-encrypted, with a test that reads the
  raw column to prove it.
- **Note content never leaves the box except to the AI the customer connected.**
  Which means the error and usage streams need a harder scrubber than Squirrel's:
  a note path is content (`Clients/Acme/2026 renewal.md` is a fact about a
  customer's business), so paths get hashed in analytics events and replaced in
  error reports. Squirrel keeps hosts in error reports because a host is the
  first thing you need to fix a mailbox; the equivalent here is the *tool* and
  the *error type*, never the path.
- Accepted risk, authenticated only: an imported git URL is a user-supplied
  outbound fetch, so it goes through the `_is_unreachable_host()` SSRF guard
  before we clone it.

### Testing

Squirrel's layers, one per honest question:

- **Unit** (`tests/`) -- `FakeVaultProvider`, no filesystem.
- **Integration** (`tests/integration/`, marker `integration`) -- a real seeded
  vault in a tmpdir, exercising the actual filesystem provider: wikilink
  resolution, link rewriting on move, atomic write under a concurrent writer,
  and every path-escape attempt. The GreenMail analogue is just a directory, so
  this layer is cheap and there is no excuse for skipping it.
- **Docker smoke** -- MCP handshake plus `vault_list_notes` against the
  container.
- **Playwright** -- `web/console.html` drives the container in a browser.
- **Admin `make test-db`** -- migrations, tenant isolation, path isolation over
  HTTP, secrets at rest, analytics consent, connector SQL, and git-token
  rotation. CI runs it with `REQUIRE_DB=1` so a lost database is a failure
  rather than a skip that reports green.

## Phases

**Phase 1 -- the public package, useful on its own.** `VaultProvider` protocol,
filesystem backend, the nineteen tools, stdio and http transports, unit and
integration suites, Dockerfile, CI. Done when `python -m obsidian_mcp` over
stdio lets Claude Code read and write `pantalytics-second-brain` and the path
safety suite is green. No Hetzner, no Postgres, no login.

**Phase 2 -- hosted, single tenant, ours.** `obsidian-pro-admin`: Postgres,
Zitadel login (new `obsidian-admin` and `obsidian-introspector` apps on the
shared org and project), the multi-tenant handler, the four pages, git remote
plus `forward_auth`, the data volume, restic backups, the prod and staging
stacks, the deploy pipeline. Done when our own vault lives on the box, Obsidian
desktop syncs to it, and Claude on the phone can answer a question from it.

**Phase 3 -- good enough for someone else.** OAuth discovery and DCR so
claude.ai and ChatGPT can connect themselves, connector status, the onboarding
metro map, usage and error diagnostics in PostHog with the consent switch,
quotas, and mobile sync -- which is where the first-party plugin gets decided
for real rather than in the abstract.

**Phase 4 -- sellable.** Metering (the odoo-mcp-pro per-day model, not a
free/paid read-write split), Stripe, and vault **skills**: `skill://` resources
in the odoo-mcp-pro shape, teaching a client how to work in a Zettelkasten, how
to run a weekly review, how to file a meeting note. That is the feature that
makes this more than a filesystem with OAuth, and it is the one odoo-mcp-pro
already proved people use.

## Decisions I need from you

I have assumed an answer for each so Phase 1 is not blocked. Say the word and
the assumption changes.

1. **Sync transport for v1.** Assumed: **git**, with mobile deferred to Phase 3
   and a first-party plugin as the likely answer. The alternative worth arguing
   is going straight at LiveSync, accepting the undocumented format, because
   mobile is where you actually read your notes.
2. **Headless Obsidian on the server.** Assumed: **no, not in any phase yet**.
   The vault is files; we read the files. Running actual Obsidian per customer
   in a container buys Dataview and Bases evaluation and costs a GUI stream, a
   container and a gigabyte of RAM per tenant, plus a licensing question. The
   architecture does not block it later.
3. **Naming and repo visibility.** Assumed: **repo stays `obsidian-pro`, product
   name still open, and this repo goes public at v0.1.0**. Obsidian's developer
   policy is narrower than it looks -- it forbids a name that suggests
   first-party, and "Obsidian Pro" reads exactly like a paid tier of Obsidian.
   For our own use that is nothing; at the point of selling it is a letter.
   Squirrel already shows the pattern that avoids it: an own name, with "for
   Obsidian" as the description rather than the brand.
   ([Obsidian developer policies](https://docs.obsidian.md/Developer+policies))

## Sources

- [obsidian-livesync](https://github.com/vrtmrz/obsidian-livesync)
- [obsidian-git-livesync](https://github.com/ecstatic-pirate/obsidian-git-livesync)
- [obsidian-git mobile implementation](https://deepwiki.com/Vinzent03/obsidian-git/3.2-mobile-implementation)
- [remotely-save](https://github.com/remotely-save/remotely-save)
- [Obsidian developer policies](https://docs.obsidian.md/Developer+policies)
- [Obsidian brand guidelines](https://obsidian.md/brand)
