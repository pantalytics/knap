"""The filesystem provider: opening a vault, browsing it, resolving links, reading.

The layer where the product's promises about *reading* are either true or not,
above all Obsidian's link resolution. The write side lives in
test_provider_writes.py, because one file for both went over the line budget.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knap_mcp.providers.filesystem.provider import FilesystemVaultProvider
from knap_mcp.providers.protocol import ProviderError


class TestConnect:
    def test_a_missing_directory_is_refused_at_connect(self, tmp_path: Path) -> None:
        """Not on the first tool call: a server that starts and then errors on
        everything reads as broken, one that refuses to start says why."""
        with pytest.raises(ProviderError):
            FilesystemVaultProvider(tmp_path / "nope").connect()

    def test_info_counts_the_notes(self, provider) -> None:
        info = provider.info()
        assert info.note_count > 0
        assert info.name == "Test vault"


class TestBrowse:
    def test_folders_include_ancestors_that_hold_no_notes(self, provider) -> None:
        """A tree a client can navigate, not a list of leaves.

        `Areas` holds no notes of its own; without it the client cannot see that
        `Areas/Work` exists below something.
        """
        folders = {folder.path for folder in provider.list_folders()}
        assert "Areas" in folders
        assert "Areas/Work" in folders
        assert "Areas/Work/Meetings" in folders

    def test_dot_folders_are_out_by_default_and_reachable_on_request(self, provider) -> None:
        assert not any(f.path.startswith(".") for f in provider.list_folders())
        with_hidden = {f.path for f in provider.list_folders(include_hidden=True)}
        assert ".trash" in with_hidden

    def test_notes_are_newest_first(self, provider) -> None:
        notes, _ = provider.list_notes(limit=100)
        stamps = [note.modified for note in notes]
        assert stamps == sorted(stamps, reverse=True)

    def test_a_folder_filter_is_applied(self, provider) -> None:
        notes, total = provider.list_notes("Areas/Work", limit=100)
        assert total == len(notes)
        assert all(note.path.startswith("Areas/Work/") for note in notes)

    def test_non_recursive_stops_at_one_level(self, provider) -> None:
        notes, _ = provider.list_notes("Areas/Work", recursive=False, limit=100)
        assert {note.path for note in notes} == {"Areas/Work/Acme.md"}

    def test_total_is_matches_not_page_size(self, provider) -> None:
        notes, total = provider.list_notes(limit=2)
        assert len(notes) == 2
        assert total > 2


class TestLinkResolution:
    """Obsidian's order, and each case is a real vault."""

    def test_an_exact_path_resolves(self, provider) -> None:
        assert provider.resolve_link("Areas/Work/Acme.md") == "Areas/Work/Acme.md"

    def test_an_exact_path_without_the_extension_resolves(self, provider) -> None:
        assert provider.resolve_link("Areas/Work/Acme") == "Areas/Work/Acme.md"

    def test_a_bare_basename_resolves(self, provider) -> None:
        assert provider.resolve_link("Acme") == "Areas/Work/Acme.md"

    def test_an_alias_resolves(self, provider) -> None:
        """A resolver that skips aliases reports every aliased link as broken."""
        assert provider.resolve_link("Acme Corp") == "Areas/Work/Acme.md"
        assert provider.resolve_link("ACME") == "Areas/Work/Acme.md"

    def test_resolution_is_case_insensitive(self, provider) -> None:
        assert provider.resolve_link("acme") == "Areas/Work/Acme.md"

    def test_an_ambiguous_basename_prefers_a_sibling(self, provider) -> None:
        """Two notes called index.md is normal; a link from one folder means the
        local one."""
        assert provider.resolve_link("index", from_path="Projects/Meeting notes.md") == (
            "Projects/index.md"
        )
        assert provider.resolve_link("index", from_path="Areas/Work/Meetings/index.md") == (
            "Areas/Work/Meetings/index.md"
        )

    def test_an_ambiguous_basename_with_no_sibling_takes_the_shallowest(self, provider) -> None:
        assert provider.resolve_link("index", from_path="Templates/Daily.md") == "index.md"

    def test_an_unresolvable_target_is_none(self, provider) -> None:
        assert provider.resolve_link("Something unwritten") is None

    def test_an_empty_target_is_none(self, provider) -> None:
        assert provider.resolve_link("") is None


class TestReadAndSearch:
    def test_read_reports_resolved_and_unresolved_links(self, provider) -> None:
        note = provider.read("Areas/Work/Acme.md")
        by_target = {link.target: link.resolved_path for link in note.links}
        assert by_target["Meeting notes"] == "Projects/Meeting notes.md"
        assert by_target["Something unwritten"] is None

    def test_read_truncates_and_reports_the_real_length(self, provider) -> None:
        note = provider.read("Areas/Work/Acme.md", max_chars=10)
        assert note.truncated
        assert len(note.body) == 10
        assert note.body_length > 10

    def test_a_path_that_is_really_a_link_target_is_followed(self, provider) -> None:
        """A client that read `[[Acme Corp]]` out of a note passes that, not a path."""
        assert provider.read("Acme Corp").path == "Areas/Work/Acme.md"

    def test_read_chunk_pages_the_body(self, provider) -> None:
        whole = provider.read("Areas/Work/Acme.md", max_chars=100000).body
        first, length, _ = provider.read_chunk("Areas/Work/Acme.md", 0, 20)
        second, _, _ = provider.read_chunk("Areas/Work/Acme.md", 20, 20)
        assert length == len(whole)
        assert first + second == whole[:40]

    def test_search_by_tag_honours_nesting(self, provider) -> None:
        notes, _ = provider.search(tag="project")
        assert "Areas/Work/Acme.md" in {note.path for note in notes}

    def test_search_by_property_presence(self, provider) -> None:
        notes, _ = provider.search(prop="due")
        assert {note.path for note in notes} == {"Areas/Work/Acme.md"}

    def test_search_by_property_value(self, provider) -> None:
        assert provider.search(prop="status", prop_value="active")[1] == 1
        assert provider.search(prop="status", prop_value="done")[1] == 0

    def test_search_matches_an_alias(self, provider) -> None:
        notes, _ = provider.search("Acme Corp")
        assert "Areas/Work/Acme.md" in {note.path for note in notes}

    def test_search_does_not_match_inside_a_code_fence(self, provider) -> None:
        """The note contains "link in a fence" only inside a fence."""
        assert provider.search("link in a fence")[1] == 0

    def test_search_skips_dot_folders_by_default(self, provider) -> None:
        assert provider.search("already deleted")[1] == 0
        assert provider.search("already deleted", include_hidden=True)[1] == 1

    def test_a_bad_since_is_refused_rather_than_ignored(self, provider) -> None:
        """Dropping the filter silently would answer a question nobody asked."""
        with pytest.raises(ValueError):
            provider.search(since="last tuesday")


class TestGraph:
    def test_backlinks_find_the_linking_note(self, provider) -> None:
        paths = {note.path for note in provider.backlinks("Areas/Work/Acme.md")}
        assert paths == {"Projects/Meeting notes.md", "Areas/Work/Meetings/index.md", "index.md"}

    def test_backlinks_ignore_a_link_in_a_fence(self, provider) -> None:
        provider.write("Fenced.md", "```\n[[Acme]]\n```\n", mode="create")
        paths = {note.path for note in provider.backlinks("Areas/Work/Acme.md")}
        assert "Fenced.md" not in paths

    def test_a_note_linking_the_same_target_twice_is_one_backlink(self, provider) -> None:
        """The dedupe moved from scanning the holder list to a set beside it.

        The list was scanned per link, which is quadratic on the note every vault
        has: the index or MOC note the whole vault points at. Order and
        uniqueness both have to survive the change.
        """
        provider.write(
            "Repeater.md",
            "See [[Acme]] and again [[Acme]] and [[Areas/Work/Acme]] once more.\n",
            mode="create",
        )
        paths = [note.path for note in provider.backlinks("Areas/Work/Acme.md")]
        assert paths.count("Repeater.md") == 1
        assert len(paths) == len(set(paths))

    def test_backlink_order_is_stable_across_refreshes(self, provider) -> None:
        first = [note.path for note in provider.backlinks("Areas/Work/Acme.md")]
        provider.index.refresh(force=True)
        assert [note.path for note in provider.backlinks("Areas/Work/Acme.md")] == first

    def test_links_include_unresolved_by_default(self, provider) -> None:
        links = provider.links("Areas/Work/Acme.md")
        assert any(link.resolved_path is None for link in links)
        resolved_only = provider.links("Areas/Work/Acme.md", include_unresolved=False)
        assert all(link.resolved_path for link in resolved_only)

    def test_tags_are_counted_with_parents(self, provider) -> None:
        counts = {tag.tag: tag.count for tag in provider.tags()}
        assert counts["project"] == 2  # Acme and Meeting notes
        assert counts["project/acme"] == 2
        assert counts["client"] == 1

    def test_a_tag_prefix_narrows(self, provider) -> None:
        tags = {tag.tag for tag in provider.tags(prefix="project")}
        assert tags == {"project", "project/acme"}

    def test_tags_are_most_used_first(self, provider) -> None:
        counts = [tag.count for tag in provider.tags()]
        assert counts == sorted(counts, reverse=True)


class TestIndexFreshness:
    def test_a_note_written_by_obsidian_is_picked_up(self, provider) -> None:
        """The index cannot be built once and trusted: Obsidian edits under us."""
        assert provider.search("written outside")[1] == 0
        (provider.root / "Outside.md").write_text("# written outside the server\n")
        provider.index.refresh(force=True)
        assert provider.search("written outside")[1] == 1

    def test_a_note_deleted_by_obsidian_falls_out(self, provider) -> None:
        (provider.root / "Projects" / "index.md").unlink()
        provider.index.refresh(force=True)
        assert provider.resolve_link("Projects/index") is None

    def test_our_own_write_is_visible_immediately(self, provider) -> None:
        """No interval to wait out: a search right after a write must see it."""
        provider.write("Immediate.md", "# unique-marker-xyz\n", mode="create")
        assert provider.search("unique-marker-xyz")[1] == 1

    def test_an_edited_note_is_re_read_not_kept(self, provider, obsidian_edits) -> None:
        obsidian_edits(provider.root / "index.md", "\nnew-content-marker\n")
        provider.index.refresh(force=True)
        assert provider.search("new-content-marker")[1] == 1
