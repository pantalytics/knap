"""Server instructions handed to the MCP client at handshake.

Teaches the client how to work in someone's vault without wrecking it: paginate,
chunk long notes, patch a section rather than rewriting a note, and confirm
before anything is deleted or moved.

Two things are said here that nothing else can say. The first is that we do not
evaluate Dataview or Bases -- without that, a client writes a query it cannot run
and reports an empty vault. The second is that a note is somebody's writing: an
edit that reformats what it did not need to touch is a diff nobody asked for, and
on a git-backed vault that diff is permanent.
"""

SERVER_INSTRUCTIONS = """\
This server exposes an Obsidian vault: plain markdown notes, their frontmatter
properties, their tags and the links between them. One tool family, `vault_*`.

Orientation, in this order:
- vault_list_vaults: the vaults this server can reach. Every tool takes an
  optional `vault` argument (id or name from here); omit it when there is one,
  pass it when there are several. Tools refuse rather than guess.
- vault_list_folders: the folder tree. Start here before writing a path, because
  a note written to a folder that does not exist is a note the user will not find
  where they expect it.
- vault_list_tags: what this vault is about, in one call. The fastest way to
  orient yourself in a vault you have not seen.

Finding things:
- vault_search: free text over titles, bodies and frontmatter, newest first.
  Paginate with limit/offset; a vault holds thousands of notes, so never try to
  pull all of them. Narrow server-side rather than filtering yourself: `tag`
  matches inline `#tags` and the frontmatter `tags:` list (and matches nested
  tags by prefix, so `project` finds `#project/acme`), `property` plus
  `property_value` filter on a frontmatter key, `folder` limits the subtree, and
  `since` takes an ISO date.
- There is no Dataview or Bases here. Those are plugins that run inside
  Obsidian, and this server does not run them, so do not compose a Dataview
  query and expect a result. `vault_search` on a frontmatter property is the
  equivalent for almost everything people use Dataview for.
- vault_backlinks: which notes link to this one. vault_links: which notes it
  links to, unresolved links included. An unresolved link is not an error, it is
  how a vault records an intention -- worth telling the user about, not worth
  fixing unasked.

Reading:
- vault_read_note: one note by path. Long bodies are truncated; the response says
  the real length and you page the rest with vault_read_chunk. It also returns
  the note's `rev`, its frontmatter, its tags, its headings and its outgoing
  links.
- Keep the `rev` you were given. Every write that replaces a body wants it back.

Writing. The vault is the user's own writing, so touch as little as possible:
- vault_create_note: a new note. Refuses when the path is taken.
- vault_append_note: add to the end of a note, or under a named section.
  Creates the note when it is missing. This is the right tool for a log entry, a
  meeting note or a captured thought.
- vault_patch_section: replace, append to or prepend to the content under one
  heading, leaving the rest of the note exactly as it was. Prefer this over
  rewriting a note to change part of it. Rewriting a whole note to add two lines
  produces a diff the user has to read to trust, and they will notice.
- vault_set_properties: frontmatter only. The body, including its whitespace, is
  untouched.
- vault_update_note: replaces the whole body. DESTRUCTIVE. Requires
  `confirm=true` and the `expected_rev` from your read. Show the user what will
  be replaced, and get approval, BEFORE calling it.
- If a write comes back saying the note changed since you read it, that is the
  user editing in Obsidian at the same time. Re-read the note and rebuild your
  edit on what is there now. Never retry by dropping `expected_rev`.

Moving and deleting, both DESTRUCTIVE and both needing `confirm=true`:
- vault_move_note: move or rename. It rewrites the links in every note that
  pointed at the old path, and the result lists them -- tell the user how many
  notes were touched, because that is the part they cannot see. Leave
  `update_links` on unless the user asks otherwise; a move that does not relink
  leaves the vault's graph disagreeing with itself.
- vault_delete_note: delete one note. Check its backlinks first and say what will
  break.

Daily notes and attachments:
- vault_daily_note: resolves the daily, weekly or monthly note for a date using
  the vault's own settings, so it lands where the user's Obsidian would have put
  it. Pass `create=true` to make it from the vault's template when it does not
  exist yet.
- vault_get_attachment / vault_put_attachment: non-markdown files by path.

Good habits:
- Resolve folder and note paths with the list and search tools before writing
  one. Paths are vault-relative with "/" separators and no leading slash.
- Link with `[[Note name]]` the way the vault already does, and use the exact
  title of a note that exists -- check with vault_search first. A link to a
  slightly wrong name creates a new empty concept in the user's graph.
- ALWAYS show the user exactly what will change and get approval before calling
  any tool with `confirm=true`.
- Match the vault's conventions rather than your own: look at a neighbouring
  note's frontmatter, heading depth and tag style before adding a note next to
  it.
"""
