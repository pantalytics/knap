"""Parsing and writing a note. The details that corrupt somebody's writing.

Three claims in ``markdown.py`` are load-bearing and each has a section here:
code is not content, a write is atomic, and reading a note for scanning sees
exactly what reading it properly would. Writing frontmatter is the fourth and it
lives in ``test_frontmatter.py``, beside the module it covers.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from knap_mcp.providers.filesystem import markdown as md


class TestFrontmatterParsing:
    def test_no_frontmatter_leaves_the_body_alone(self) -> None:
        note = md.parse("# Title\n\nBody.\n")
        assert note.frontmatter == {}
        assert note.body == "# Title\n\nBody.\n"

    def test_frontmatter_is_parsed_and_split_off(self) -> None:
        note = md.parse("---\nstatus: active\ntags: [a, b]\n---\n# Title\n")
        assert note.frontmatter == {"status": "active", "tags": ["a", "b"]}
        assert note.body == "# Title\n"

    def test_an_unterminated_fence_is_not_frontmatter(self) -> None:
        """A stray '---' at the top of a note is a horizontal rule.

        Treating it as an unclosed header would swallow the whole document.
        """
        raw = "---\n# Actually just a rule\n\nMore text.\n"
        note = md.parse(raw)
        assert note.frontmatter == {}
        assert note.body == raw

    def test_broken_yaml_is_tolerated(self) -> None:
        """Obsidian keeps working with frontmatter it cannot parse, so we do too.

        The note has no properties as far as search is concerned; refusing to
        open it at all would be worse.
        """
        note = md.parse("---\nthis: [is: not: valid\n---\n# Title\n")
        assert note.frontmatter == {}
        assert note.body == "# Title\n"

    def test_a_rule_further_down_is_not_frontmatter(self) -> None:
        note = md.parse("# Title\n\n---\n\nAfter a rule.\n")
        assert note.frontmatter == {}
        assert not note.has_frontmatter


class TestCodeIsNotContent:
    """A note documenting syntax has no links and no tags in that block."""

    def test_a_wikilink_in_a_fence_is_not_a_link(self) -> None:
        note = md.parse("Real [[One]].\n\n```\nFake [[Two]].\n```\n")
        targets = [link.target for link in md.raw_links_of(note)]
        assert targets == ["One"]

    def test_a_wikilink_in_inline_code_is_not_a_link(self) -> None:
        note = md.parse("Real [[One]] and `[[Two]]` quoted.\n")
        targets = [link.target for link in md.raw_links_of(note)]
        assert targets == ["One"]

    def test_a_tag_in_a_fence_is_not_a_tag(self) -> None:
        note = md.parse("#real\n\n```bash\n# not a tag, a shell comment\n#alsonot\n```\n")
        assert md.tags_of(note) == ["real"]

    def test_a_tilde_fence_counts_too(self) -> None:
        note = md.parse("Real [[One]].\n\n~~~\n[[Two]]\n~~~\n")
        assert [link.target for link in md.raw_links_of(note)] == ["One"]

    def test_stripping_preserves_offsets(self) -> None:
        """The scannable copy must be the same length as the body.

        Every excerpt and every link rewrite is computed on the stripped text and
        applied to the real one, so a length change would silently corrupt notes.
        """
        body = "a [[x]]\n\n```\n[[y]]\nmore\n```\n\ntail `z` end\n"
        note = md.parse(body)
        assert len(note.body_scannable) == len(note.body)


class TestLinks:
    def test_alias_and_anchor_are_split_out(self) -> None:
        note = md.parse("See [[Areas/Acme#Log|the log]].\n")
        (link,) = md.raw_links_of(note)
        assert link.target == "Areas/Acme"
        assert link.anchor == "Log"
        assert link.alias == "the log"
        assert not link.embed

    def test_a_block_anchor_is_recognised(self) -> None:
        note = md.parse("See [[Note^abc123]].\n")
        (link,) = md.raw_links_of(note)
        assert link.target == "Note"
        assert link.anchor == "abc123"

    def test_an_embed_is_marked(self) -> None:
        note = md.parse("![[Attachments/diagram.png]]\n")
        (link,) = md.raw_links_of(note)
        assert link.embed
        assert link.target == "Attachments/diagram.png"

    def test_a_heading_only_link_points_inside_this_note(self) -> None:
        """[[#Log]] is not a link to another note and must not be reported as one."""
        note = md.parse("Jump to [[#Log]].\n")
        assert md.raw_links_of(note) == []

    def test_a_markdown_link_to_a_note_counts(self) -> None:
        note = md.parse("See [the note](Areas/Acme.md).\n")
        (link,) = md.raw_links_of(note)
        assert link.target == "Areas/Acme.md"

    def test_a_url_is_not_a_vault_link(self) -> None:
        note = md.parse("[site](https://example.com) [mail](mailto:a@b.c)\n")
        assert md.raw_links_of(note) == []

    def test_a_percent_encoded_markdown_target_is_decoded(self) -> None:
        note = md.parse("[x](Areas/Meeting%20notes.md)\n")
        (link,) = md.raw_links_of(note)
        assert link.target == "Areas/Meeting notes.md"


class TestRewriteLinks:
    def test_only_the_target_changes(self) -> None:
        """The alias, the anchor and the surrounding text come out untouched."""
        raw = "Before [[Old name#Log|the log]] after.\n"
        new, count = md.rewrite_links(raw, {"Old name": "New name"})
        assert count == 1
        assert new == "Before [[New name#Log|the log]] after.\n"

    def test_several_links_in_one_note(self) -> None:
        raw = "[[A]] then [[B]] then [[A]] again.\n"
        new, count = md.rewrite_links(raw, {"A": "Z"})
        assert count == 2
        assert new == "[[Z]] then [[B]] then [[Z]] again.\n"

    def test_frontmatter_is_not_touched(self) -> None:
        raw = "---\nrelated: Old\n---\n[[Old]]\n"
        new, count = md.rewrite_links(raw, {"Old": "New"})
        assert count == 1
        assert new == "---\nrelated: Old\n---\n[[New]]\n"

    def test_a_link_in_a_fence_is_not_rewritten(self) -> None:
        raw = "[[Old]]\n\n```\n[[Old]]\n```\n"
        new, count = md.rewrite_links(raw, {"Old": "New"})
        assert count == 1
        assert new == "[[New]]\n\n```\n[[Old]]\n```\n"

    def test_nothing_to_do_returns_the_input_unchanged(self) -> None:
        raw = "[[A]]\n"
        new, count = md.rewrite_links(raw, {"B": "C"})
        assert count == 0
        assert new is raw


class TestSections:
    RAW = "# Title\n\n## Log\n\n- one\n\n### Sub\n\n- deep\n\n## Next\n\n- later\n"

    def test_a_section_ends_at_the_next_same_or_higher_heading(self) -> None:
        note = md.parse(self.RAW)
        start, end, level = md.section_span(note, "Log")
        assert level == 2
        content = note.body[start:end]
        assert "- one" in content
        assert "- deep" in content  # a subsection belongs to its parent
        assert "- later" not in content

    def test_matching_is_case_insensitive(self) -> None:
        note = md.parse(self.RAW)
        assert md.section_span(note, "log") is not None
        assert md.section_span(note, "## LOG") is not None

    def test_a_missing_heading_is_none(self) -> None:
        assert md.section_span(md.parse(self.RAW), "Nope") is None

    def test_headings_are_listed_in_order(self) -> None:
        assert md.headings_of(md.parse(self.RAW)) == ["Title", "Log", "Sub", "Next"]


class TestTags:
    def test_frontmatter_and_inline_tags_combine(self) -> None:
        note = md.parse("---\ntags: [alpha]\n---\nBody #beta and #gamma/delta\n")
        assert set(md.tags_of(note)) == {"alpha", "beta", "gamma/delta"}

    def test_the_legacy_tag_key_counts(self) -> None:
        note = md.parse("---\ntag: alpha\n---\nBody\n")
        assert md.tags_of(note) == ["alpha"]

    def test_a_space_separated_string_is_split(self) -> None:
        note = md.parse("---\ntags: one two\n---\nBody\n")
        assert set(md.tags_of(note)) == {"one", "two"}

    @pytest.mark.parametrize("text", ["#1", "#2026", "a#notatag", "](#anchor)"])
    def test_things_that_are_not_tags(self, text: str) -> None:
        assert md.tags_of(md.parse(f"Body {text} end\n")) == []

    def test_duplicates_collapse_case_insensitively(self) -> None:
        note = md.parse("---\ntags: [Alpha]\n---\n#alpha #ALPHA\n")
        assert md.tags_of(note) == ["Alpha"]


class TestTitle:
    def test_frontmatter_title_wins(self) -> None:
        note = md.parse("---\ntitle: Real Title\n---\n# Heading\n")
        assert md.title_of("file.md", note) == "Real Title"

    def test_then_the_first_h1(self) -> None:
        note = md.parse("## Sub\n\n# The H1\n")
        assert md.title_of("file.md", note) == "The H1"

    def test_then_the_filename(self) -> None:
        note = md.parse("Just body.\n")
        assert md.title_of("Areas/Meeting notes.md", note) == "Meeting notes"

    def test_an_h1_in_a_fence_is_not_the_title(self) -> None:
        note = md.parse("```\n# Not a heading\n```\n")
        assert md.title_of("Real name.md", note) == "Real name"


class TestAtomicWrite:
    def test_writing_and_reading_round_trips(self, tmp_path: Path) -> None:
        target = tmp_path / "note.md"
        rev, size, _ = md.atomic_write(target, "# Hello\n")
        text, read_rev, read_size, _ = md.read_text(target)
        assert text == "# Hello\n"
        assert read_rev == rev
        assert read_size == size

    def test_no_temp_files_are_left_behind(self, tmp_path: Path) -> None:
        md.atomic_write(tmp_path / "note.md", "# Hello\n")
        assert [p.name for p in tmp_path.iterdir()] == ["note.md"]

    def test_a_failed_write_leaves_no_temp_file(self, tmp_path: Path, monkeypatch) -> None:
        """The cleanup path. A crash mid-write must not litter the vault.

        A vault that slowly fills with `.knap-*.tmp` files is a vault whose
        listing and search get noisier every time something goes wrong.
        """
        monkeypatch.setattr(md.os, "replace", _boom)
        with pytest.raises(RuntimeError):
            md.atomic_write(tmp_path / "note.md", "# Hello\n")
        assert list(tmp_path.iterdir()) == []

    def test_the_original_is_intact_after_a_failed_write(self, tmp_path: Path, monkeypatch) -> None:
        """Atomicity's actual promise: the old note or the new one, never neither."""
        target = tmp_path / "note.md"
        md.atomic_write(target, "# Original\n")
        monkeypatch.setattr(md.os, "replace", _boom)
        with pytest.raises(RuntimeError):
            md.atomic_write(target, "# Replacement\n")
        assert target.read_text() == "# Original\n"

    @pytest.mark.skipif(os.name == "nt", reason="POSIX modes")
    def test_permissions_are_carried_over(self, tmp_path: Path) -> None:
        """A vault with tightened modes must not loosen one note at a time."""
        target = tmp_path / "note.md"
        md.atomic_write(target, "# One\n")
        os.chmod(target, 0o600)
        md.atomic_write(target, "# Two\n")
        assert (target.stat().st_mode & 0o777) == 0o600

    def test_bytes_go_through_the_same_path(self, tmp_path: Path) -> None:
        target = tmp_path / "diagram.png"
        md.atomic_write_bytes(target, b"\x89PNG\r\n")
        assert target.read_bytes() == b"\x89PNG\r\n"


class TestRev:
    def test_the_same_bytes_at_the_same_time_give_the_same_rev(self) -> None:
        assert md.compute_rev(b"abc", 111) == md.compute_rev(b"abc", 111)

    def test_different_bytes_give_a_different_rev(self) -> None:
        assert md.compute_rev(b"abc", 111) != md.compute_rev(b"abd", 111)

    def test_two_writes_inside_one_mtime_tick_still_differ(self) -> None:
        """Why the hash is in there at all.

        Some filesystems have one-second mtime granularity, and writing twice
        inside one second is exactly what an AI does. On mtime alone the second
        write would be invisible to an ``expected_rev`` check.
        """
        assert md.compute_rev(b"first", 111) != md.compute_rev(b"second", 111)

    def test_a_latin1_note_is_readable_rather_than_an_error(self, tmp_path: Path) -> None:
        """One badly encoded note years ago should not break the tool that opens it."""
        target = tmp_path / "old.md"
        target.write_bytes(b"# Caf\xe9\n")
        text, _, _, _ = md.read_text(target)
        assert "Caf" in text


def _boom(*args, **kwargs):
    raise RuntimeError("simulated failure")


class TestReadingForScanning:
    """The two shortcuts search takes, and the promise that they change nothing.

    Search opens every candidate note. It used to hash each one to build a `rev`
    it discards, and YAML-parse a frontmatter block it had already compared
    against the index before opening the file. Both are skippable; neither may
    change what search sees.
    """

    RAW = "---\ntype: meeting\ntags:\n  - a\n---\n# Title\n\nprose\n\n```\ncode\n```\n"

    def test_load_properties_false_changes_only_the_properties(self) -> None:
        full = md.parse(self.RAW)
        lean = md.parse(self.RAW, load_properties=False)
        assert lean.body == full.body
        assert lean.body_scannable == full.body_scannable
        assert lean.frontmatter_raw == full.frontmatter_raw
        assert lean.raw == full.raw
        assert lean.frontmatter == {}
        assert full.frontmatter == {"type": "meeting", "tags": ["a"]}

    @pytest.mark.parametrize(
        "raw",
        [
            "no frontmatter at all\n",
            "---\nunterminated: block\n\nbody\n",
            "---\n---\nempty block\n",
            "---\n: not: valid: yaml:\n---\nbody\n",
            "",
        ],
    )
    def test_the_two_agree_on_the_body_for_awkward_notes(self, raw: str) -> None:
        full = md.parse(raw)
        lean = md.parse(raw, load_properties=False)
        assert (lean.body, lean.body_scannable) == (full.body, full.body_scannable)

    def test_read_body_matches_read_texts_text(self, tmp_path) -> None:
        note = tmp_path / "n.md"
        note.write_text(self.RAW, encoding="utf-8")
        text, _rev, _size, _mtime = md.read_text(note)
        assert md.read_body(note) == text

    def test_read_body_decodes_the_same_way_on_a_non_utf8_note(self, tmp_path) -> None:
        """A vault that picked up a Latin-1 note years ago still opens."""
        note = tmp_path / "n.md"
        note.write_bytes(b"caf\xe9 notes\n")
        text, _rev, _size, _mtime = md.read_text(note)
        assert md.read_body(note) == text
