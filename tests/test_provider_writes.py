"""The filesystem provider: writing, patching, moving, deleting, periodic notes.

Where the two-writers rule and the relink-on-move promise are either kept or not.
The read side lives in test_provider.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from knap_mcp.providers.filesystem.provider import FilesystemVaultProvider
from knap_mcp.providers.protocol import (
    NoteExistsError,
    NoteNotFoundError,
    PathNotAllowedError,
    ProviderError,
    RevisionMismatch,
)


class TestWrite:
    def test_create_then_read_back(self, provider) -> None:
        ref = provider.write("New/Note.md", "# New\n\nBody.\n", mode="create")
        assert ref.path == "New/Note.md"
        assert provider.read("New/Note.md").body == "# New\n\nBody.\n"

    def test_create_makes_missing_folders(self, provider) -> None:
        """Obsidian creates folders when you type a path, so we do too."""
        provider.write("A/B/C/Deep.md", "x\n", mode="create")
        assert (provider.root / "A" / "B" / "C" / "Deep.md").is_file()

    def test_create_refuses_an_existing_path(self, provider) -> None:
        with pytest.raises(NoteExistsError):
            provider.write("Areas/Work/Acme.md", "x", mode="create")

    def test_create_with_properties(self, provider) -> None:
        provider.write("New/Note.md", "Body\n", mode="create", frontmatter={"status": "draft"})
        assert provider.read("New/Note.md").frontmatter == {"status": "draft"}

    def test_append_keeps_the_frontmatter_and_what_was_there(self, provider) -> None:
        before = provider.read("Areas/Work/Acme.md")
        provider.write("Areas/Work/Acme.md", "- appended\n", mode="append")
        after = provider.read("Areas/Work/Acme.md")
        assert after.frontmatter == before.frontmatter
        assert "- Send the quote" in after.body
        assert after.body.rstrip().endswith("- appended")

    def test_append_does_not_glue_onto_the_last_sentence(self, provider) -> None:
        provider.write("Glue.md", "First line.", mode="create")
        provider.write("Glue.md", "Second line.", mode="append")
        assert provider.read("Glue.md").body == "First line.\n\nSecond line."

    def test_append_creates_the_note_when_missing(self, provider) -> None:
        provider.write("Fresh.md", "content\n", mode="append")
        assert provider.read("Fresh.md").body.strip() == "content"

    def test_prepend_puts_it_first(self, provider) -> None:
        provider.write("P.md", "original\n", mode="create")
        provider.write("P.md", "newest", mode="prepend")
        assert provider.read("P.md").body.startswith("newest")

    def test_overwrite_needs_a_rev(self, provider) -> None:
        with pytest.raises(RevisionMismatch):
            provider.write("Areas/Work/Acme.md", "wiped", mode="overwrite")

    def test_overwrite_with_the_right_rev_succeeds(self, provider) -> None:
        note = provider.read("Areas/Work/Acme.md")
        provider.write("Areas/Work/Acme.md", "replaced\n", mode="overwrite", expected_rev=note.rev)
        assert provider.read("Areas/Work/Acme.md").body == "replaced\n"

    def test_overwrite_with_a_stale_rev_is_refused(self, provider, obsidian_edits) -> None:
        """The two-writers rule, and the whole reason `rev` exists.

        Standing in for the customer typing in Obsidian while the AI works.
        """
        note = provider.read("Areas/Work/Acme.md")
        stale_rev = note.rev
        obsidian_edits(provider.root / "Areas" / "Work" / "Acme.md", "\n- typed by the human\n")
        with pytest.raises(RevisionMismatch) as caught:
            provider.write("Areas/Work/Acme.md", "wiped", mode="overwrite", expected_rev=stale_rev)
        assert caught.value.expected == stale_rev
        assert caught.value.actual != stale_rev
        # And the human's words are still there.
        assert "typed by the human" in provider.read("Areas/Work/Acme.md").body

    def test_overwrite_keeps_the_frontmatter_when_none_is_passed(self, provider) -> None:
        """The body is replaced; the properties are a separate argument and stay.

        This is what the tool promises the caller, and the reason it matters is
        retrieval: `type`, `tags` and `aliases` are how an AI finds a note again.
        A model that reads a note, rewrites the prose and writes it back is doing
        the ordinary thing, and it never sees the frontmatter go.
        """
        before = provider.read("Areas/Work/Acme.md")
        assert before.frontmatter, "fixture must have frontmatter for this to mean anything"

        provider.write(
            "Areas/Work/Acme.md", "new body\n", mode="overwrite", expected_rev=before.rev
        )

        after = provider.read("Areas/Work/Acme.md")
        assert after.body == "new body\n"
        assert after.frontmatter == before.frontmatter

    def test_overwrite_still_merges_properties_when_they_are_passed(self, provider) -> None:
        """Keeping the block is not the same as refusing to change it."""
        before = provider.read("Areas/Work/Acme.md")
        provider.write(
            "Areas/Work/Acme.md",
            "new body\n",
            mode="overwrite",
            expected_rev=before.rev,
            frontmatter={"status": "closed"},
        )

        after = provider.read("Areas/Work/Acme.md")
        assert after.frontmatter["status"] == "closed"
        assert after.body == "new body\n"
        # Everything the caller did not name is still there.
        for key, value in before.frontmatter.items():
            if key != "status":
                assert after.frontmatter[key] == value

    def test_overwrite_on_a_note_without_frontmatter_adds_none(self, provider) -> None:
        """No block in, no block out. Preserving must not mean inventing."""
        provider.write("Plain.md", "first\n", mode="create")
        note = provider.read("Plain.md")
        provider.write("Plain.md", "second\n", mode="overwrite", expected_rev=note.rev)

        after = provider.read("Plain.md")
        assert after.body == "second\n"
        assert not after.frontmatter

    def test_a_path_escape_is_refused_on_write(self, provider) -> None:
        with pytest.raises(PathNotAllowedError):
            provider.write("../outside.md", "x", mode="create")


class TestPatchSection:
    def test_replace_leaves_the_rest_of_the_note_alone(self, provider) -> None:
        provider.patch_section("Areas/Work/Acme.md", "Log", "- replaced\n")
        body = provider.read("Areas/Work/Acme.md").body
        assert "- replaced" in body
        assert "- Kickoff done" not in body
        assert "- Send the quote" in body  # the Next section untouched
        assert "## Next" in body

    def test_append_adds_under_the_heading_not_at_the_end(self, provider) -> None:
        provider.patch_section("Areas/Work/Acme.md", "Log", "- second entry\n", mode="append")
        body = provider.read("Areas/Work/Acme.md").body
        assert body.index("- second entry") < body.index("## Next")
        assert "- Kickoff done" in body

    def test_prepend_puts_it_at_the_top_of_the_section(self, provider) -> None:
        provider.patch_section("Areas/Work/Acme.md", "Log", "- newest\n", mode="prepend")
        body = provider.read("Areas/Work/Acme.md").body
        assert body.index("- newest") < body.index("- Kickoff done")

    def test_a_missing_heading_lists_the_ones_that_exist(self, provider) -> None:
        """The error has to be actionable: an AI that guessed a heading needs the
        real ones, not just a refusal."""
        with pytest.raises(NoteNotFoundError) as caught:
            provider.patch_section("Areas/Work/Acme.md", "Nope", "x")
        message = str(caught.value)
        assert "Log" in message and "Next" in message

    def test_the_frontmatter_survives(self, provider) -> None:
        before = provider.read("Areas/Work/Acme.md").frontmatter
        provider.patch_section("Areas/Work/Acme.md", "Log", "- x\n")
        assert provider.read("Areas/Work/Acme.md").frontmatter == before

    def test_repeated_appends_do_not_eat_the_spacing(self, provider) -> None:
        """A tool called every day must not slowly reflow the note it writes to."""
        for i in range(4):
            provider.patch_section("Areas/Work/Acme.md", "Log", f"- entry {i}\n", mode="append")
        body = provider.read("Areas/Work/Acme.md").body
        assert "\n\n## Next" in body
        assert all(f"- entry {i}" in body for i in range(4))

    def test_a_stale_rev_is_refused_when_given(self, provider, obsidian_edits) -> None:
        note = provider.read("Areas/Work/Acme.md")
        obsidian_edits(provider.root / "Areas" / "Work" / "Acme.md", "\nhuman text\n")
        with pytest.raises(RevisionMismatch):
            provider.patch_section("Areas/Work/Acme.md", "Log", "x", expected_rev=note.rev)


class TestSetProperties:
    def test_a_property_is_merged_and_the_body_untouched(self, provider) -> None:
        before = provider.read("Areas/Work/Acme.md").body
        provider.set_properties("Areas/Work/Acme.md", {"status": "done"})
        after = provider.read("Areas/Work/Acme.md")
        assert after.frontmatter["status"] == "done"
        assert after.frontmatter["due"] is not None  # untouched keys survive
        assert after.body == before

    def test_the_flow_style_of_an_untouched_key_is_preserved(self, provider) -> None:
        """The promise that stops a property change from being a whole-file diff."""
        provider.set_properties("Areas/Work/Acme.md", {"status": "done"})
        raw = (provider.root / "Areas" / "Work" / "Acme.md").read_text()
        assert "aliases: [Acme Corp, ACME]" in raw

    def test_none_removes_a_property(self, provider) -> None:
        provider.set_properties("Areas/Work/Acme.md", {"due": None})
        assert "due" not in provider.read("Areas/Work/Acme.md").frontmatter

    def test_setting_the_same_value_does_not_rewrite_the_file(self, provider) -> None:
        """No-op writes bump an mtime, wake every sync client and make a commit."""
        target = provider.root / "Areas" / "Work" / "Acme.md"
        before = target.stat().st_mtime_ns
        provider.set_properties("Areas/Work/Acme.md", {"status": "active"})
        assert target.stat().st_mtime_ns == before


class TestMove:
    def test_inbound_links_follow_the_move(self, provider) -> None:
        """Renaming so the basename changes: every link must be rewritten.

        Both notes that linked to it are reported: Acme in prose, and the
        Projects index in a list. Reporting only one would mean telling the user
        we touched one file when we touched two.
        """
        result = provider.move("Projects/Meeting notes.md", "Projects/Renamed notes.md")
        assert result.relinked == ["Areas/Work/Acme.md", "Projects/index.md"]
        body = provider.read("Areas/Work/Acme.md").body
        assert "[[Renamed notes]]" in body
        assert "[[Meeting notes]]" not in body
        assert "[[Renamed notes]]" in provider.read("Projects/index.md").body

    def test_a_link_already_spelled_correctly_is_not_rewritten(self, provider) -> None:
        """Moving between folders keeps a unique basename valid.

        Rewriting it anyway is identical bytes, a bumped mtime, a commit on the
        synced vault, and a result claiming notes were touched that were not --
        and `relinked` is read out to the user.
        """
        target = provider.root / "Areas" / "Work" / "Acme.md"
        before_mtime = target.stat().st_mtime_ns
        before_body = target.read_text()
        result = provider.move("Projects/Meeting notes.md", "Areas/Work/Meeting notes.md")
        assert result.relinked == []
        assert target.read_text() == before_body
        assert target.stat().st_mtime_ns == before_mtime

    def test_the_alias_a_link_used_still_resolves_afterwards(self, provider) -> None:
        """`Meeting notes` links to Acme by its alias, and the alias moves with it."""
        provider.move("Areas/Work/Acme.md", "Archive/Acme.md")
        assert provider.resolve_link("Acme Corp") == "Archive/Acme.md"

    def test_moving_onto_an_existing_note_is_refused(self, provider) -> None:
        with pytest.raises(NoteExistsError):
            provider.move("Projects/index.md", "index.md")

    def test_moving_to_the_same_path_is_refused(self, provider) -> None:
        with pytest.raises(ProviderError):
            provider.move("index.md", "index.md")

    def test_update_links_off_leaves_them_broken(self, provider) -> None:
        """Offered, almost always wrong, and pinned so it stays a choice."""
        provider.move("Projects/Meeting notes.md", "Renamed.md", update_links=False)
        body = provider.read("Areas/Work/Acme.md").body
        assert "[[Meeting notes]]" in body
        assert provider.resolve_link("Meeting notes") is None

    def test_a_link_inside_a_code_fence_is_not_rewritten(self, provider) -> None:
        provider.write("Doc.md", "Syntax:\n\n```\n[[Meeting notes]]\n```\n", mode="create")
        provider.move("Projects/Meeting notes.md", "Projects/Renamed.md")
        assert "[[Meeting notes]]" in provider.read("Doc.md").body

    def test_the_moved_note_keeps_its_content(self, provider) -> None:
        before = provider.read("Projects/Meeting notes.md").body
        provider.move("Projects/Meeting notes.md", "Archive/Old notes.md")
        assert provider.read("Archive/Old notes.md").body == before


class TestDelete:
    def test_a_deleted_note_goes_to_trash_rather_than_away(self, provider) -> None:
        """A hosted vault has no desktop recycle bin behind it."""
        provider.delete("Projects/index.md")
        assert not (provider.root / "Projects" / "index.md").exists()
        assert (provider.root / ".trash" / "Projects" / "index.md").is_file()

    def test_a_deleted_note_disappears_from_search(self, provider) -> None:
        provider.write("Doomed.md", "# unique-doomed-marker\n", mode="create")
        assert provider.search("unique-doomed-marker")[1] == 1
        provider.delete("Doomed.md")
        assert provider.search("unique-doomed-marker")[1] == 0

    def test_a_deleted_note_is_still_reachable_with_include_hidden(self, provider) -> None:
        """`.trash` is excluded, not erased: "what did I just delete" is answerable."""
        provider.write("Doomed.md", "# unique-doomed-marker\n", mode="create")
        provider.delete("Doomed.md")
        assert provider.search("unique-doomed-marker", include_hidden=True)[1] == 1

    def test_a_deleted_note_no_longer_resolves_as_a_link(self, provider) -> None:
        """A trashed note is in the index and out of link resolution."""
        provider.write("Doomed.md", "# Doomed\n", mode="create")
        assert provider.resolve_link("Doomed") == "Doomed.md"
        provider.delete("Doomed.md")
        assert provider.resolve_link("Doomed") is None

    def test_deleting_twice_does_not_clobber_the_first_copy(self, provider) -> None:
        provider.write("Twice.md", "first version\n", mode="create")
        provider.delete("Twice.md")
        provider.write("Twice.md", "second version\n", mode="create")
        provider.delete("Twice.md")
        trashed = list((provider.root / ".trash").glob("Twice*.md"))
        assert len(trashed) == 2

    def test_deleting_a_missing_note_is_an_error(self, provider) -> None:
        with pytest.raises((NoteNotFoundError, PathNotAllowedError)):
            provider.delete("Nope.md")


class TestPeriodicNotes:
    def test_the_path_comes_from_the_vault_settings(self, provider) -> None:
        path, created = provider.periodic_note("daily", "2026-08-03")
        assert path == "Journal/2026-08-03.md"
        assert not created

    def test_create_uses_the_vault_template(self, provider) -> None:
        path, created = provider.periodic_note("daily", "2026-08-03", create=True)
        assert created
        body = provider.read(path).body
        assert "## Captured" in body
        assert "2026-08-03" in body  # {{date}} substituted

    def test_creating_twice_does_not_replace_the_note(self, provider) -> None:
        path, _ = provider.periodic_note("daily", "2026-08-03", create=True)
        provider.write(path, "- a captured thought\n", mode="append")
        again, created = provider.periodic_note("daily", "2026-08-03", create=True)
        assert again == path
        assert not created
        assert "a captured thought" in provider.read(path).body

    def test_today_and_yesterday_are_understood(self, provider) -> None:
        """An AI passes these far more often than an ISO date."""
        assert provider.periodic_note("daily", "today")[0].startswith("Journal/")
        assert (
            provider.periodic_note("daily", "yesterday")[0]
            != provider.periodic_note("daily", "today")[0]
        )

    def test_a_vault_with_no_settings_says_so(self, tmp_path: Path) -> None:
        """Rather than inventing YYYY-MM-DD.md in the vault root."""
        from knap_mcp.providers.filesystem.periodic import PeriodicNotesNotConfigured

        root = tmp_path / "bare"
        root.mkdir()
        (root / "note.md").write_text("# x\n")
        bare = FilesystemVaultProvider(root)
        bare.connect()
        with pytest.raises(PeriodicNotesNotConfigured):
            bare.periodic_note("daily")

    def test_weekly_needs_the_periodic_notes_plugin(self, provider) -> None:
        from knap_mcp.providers.filesystem.periodic import PeriodicNotesNotConfigured

        with pytest.raises(PeriodicNotesNotConfigured):
            provider.periodic_note("weekly")

    def test_the_periodic_notes_plugin_wins_over_daily_notes(self, provider) -> None:
        plugin = provider.root / ".obsidian" / "plugins" / "periodic-notes"
        plugin.mkdir(parents=True)
        (plugin / "data.json").write_text(
            json.dumps(
                {
                    "daily": {"enabled": True, "folder": "Daily", "format": "YYYY/MM/DD"},
                    "weekly": {"enabled": True, "folder": "Weekly", "format": "gggg-[W]ww"},
                }
            )
        )
        assert provider.periodic_note("daily", "2026-08-03")[0] == "Daily/2026/08/03.md"
        assert provider.periodic_note("weekly", "2026-08-03")[0] == "Weekly/2026-W32.md"


class TestMomentFormats:
    """The translator, because a filename that is nearly right is the failure."""

    @pytest.mark.parametrize(
        "fmt,expected",
        [
            ("YYYY-MM-DD", "2026-08-03"),
            ("YYYY/MM/DD", "2026/08/03"),
            ("gggg-[W]ww", "2026-W32"),
            ("YYYY-MM", "2026-08"),
            ("DD-MM-YYYY", "03-08-2026"),
            ("YYYY-MM-DD dddd", "2026-08-03 Monday"),
            ("MMMM Do, YYYY", "August 3rd, 2026"),
            ("[Daily] YYYY-MM-DD", "Daily 2026-08-03"),
            ("YY-MM-DD", "26-08-03"),
        ],
    )
    def test_formats_render_like_moment(self, fmt: str, expected: str) -> None:
        from datetime import date

        from knap_mcp.providers.filesystem.periodic import format_moment

        assert format_moment(fmt, date(2026, 8, 3)) == expected

    def test_a_format_producing_an_unusable_filename_is_refused(self) -> None:
        from datetime import date

        from knap_mcp.providers.filesystem.periodic import format_moment

        with pytest.raises(ProviderError):
            format_moment("[bad:name]", date(2026, 8, 3))


class TestAttachments:
    def test_a_binary_round_trips(self, provider) -> None:
        provider.write_binary("Attachments/new.png", b"\x89PNG\r\n\x1a\n")
        payload = provider.read_binary("Attachments/new.png")
        assert payload.content == b"\x89PNG\r\n\x1a\n"
        assert payload.content_type == "image/png"

    def test_writing_over_an_existing_file_needs_overwrite(self, provider) -> None:
        with pytest.raises(NoteExistsError):
            provider.write_binary("Attachments/diagram.png", b"x")

    def test_a_file_over_the_ceiling_is_refused_with_a_clear_message(self, provider) -> None:
        provider.max_attachment_bytes = 8
        with pytest.raises(ProviderError) as caught:
            provider.write_binary("Attachments/big.bin", b"0123456789")
        assert "limit" in str(caught.value)

    def test_reading_over_the_ceiling_is_refused(self, provider) -> None:
        provider.max_attachment_bytes = 4
        with pytest.raises(ProviderError):
            provider.read_binary("Attachments/diagram.png")

    def test_an_escape_is_refused(self, provider) -> None:
        with pytest.raises(PathNotAllowedError):
            provider.read_binary("../../etc/passwd")
