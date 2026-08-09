"""Parsing and writing a note. The details that corrupt somebody's writing.

Three claims in ``markdown.py`` are load-bearing and each has a section here:
code is not content, writing frontmatter is not re-dumping it, and a write is
atomic.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

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


class TestEditFrontmatter:
    """The promise: the body and every untouched property come out byte-identical."""

    def test_an_untouched_property_keeps_its_exact_formatting(self) -> None:
        raw = "---\naliases: [Acme Corp, ACME]\nstatus: active\n---\n# Acme\n"
        new = md.edit_frontmatter(raw, {"status": "done"})
        assert "aliases: [Acme Corp, ACME]" in new  # still flow style, not re-dumped
        assert "status: done" in new
        assert new.endswith("# Acme\n")

    def test_a_block_list_stays_a_block_list(self) -> None:
        raw = "---\ntags:\n  - one\n  - two\nstatus: a\n---\nBody\n"
        new = md.edit_frontmatter(raw, {"status": "b"})
        assert "tags:\n  - one\n  - two\n" in new

    def test_the_body_is_never_reflowed(self) -> None:
        body = "# Title\n\n\n\nOdd    spacing   kept.\n\n- a\n"
        raw = f"---\na: 1\n---\n{body}"
        new = md.edit_frontmatter(raw, {"a": 2})
        assert new.endswith(body)

    def test_none_removes_a_key_and_its_continuation(self) -> None:
        raw = "---\ntags:\n  - one\n  - two\nstatus: a\n---\nBody\n"
        new = md.edit_frontmatter(raw, {"tags": None})
        assert "tags" not in new
        assert "one" not in new
        assert "status: a" in new

    def test_a_new_key_is_appended(self) -> None:
        raw = "---\na: 1\n---\nBody\n"
        new = md.edit_frontmatter(raw, {"b": "two"})
        assert new == "---\na: 1\nb: two\n---\nBody\n"

    def test_frontmatter_is_created_when_there_is_none(self) -> None:
        new = md.edit_frontmatter("# Title\n", {"status": "active"})
        assert new.startswith("---\n")
        assert md.parse(new).frontmatter == {"status": "active"}
        assert new.endswith("# Title\n")

    def test_removing_a_key_that_was_never_there_is_not_an_error(self) -> None:
        raw = "---\na: 1\n---\nBody\n"
        assert md.edit_frontmatter(raw, {"zzz": None}) == raw

    def test_no_changes_returns_the_input(self) -> None:
        raw = "---\na: 1\n---\nBody\n"
        assert md.edit_frontmatter(raw, {}) == raw

    @pytest.mark.parametrize(
        "value",
        [
            "true",
            "false",
            "null",
            "yes",
            "no",
            "1.5",
            "42",
            "2026-01-01",
            "a: b",
            "",
            "  x",
            # A hash after a space opens a YAML comment, so an unquoted
            # `Budget #2026 review` used to reach disk and read back as
            # `Budget`, with the rest of the title silently gone. The leading
            # `#` was handled; this one is the middle of an ordinary sentence,
            # and `#` in a title or a tag is not exotic in an Obsidian vault.
            "Budget #2026 review",
            "release #3 notes",
            "tab\t#comment",
            "trailing hash #",
        ],
    )
    def test_a_string_that_yaml_would_misread_is_quoted(self, value: str) -> None:
        """The round trip is what matters: what we write must read back equal.

        Writing `status: true` for the string "true" hands the vault a boolean,
        and the property silently changes type.

        Start from a note that already HAS frontmatter, or this tests the wrong
        code. `edit` sends a note without a block straight to `_dump_block`, so
        PyYAML does the quoting and our own `_scalar` never runs. The `#` bug
        lived on the in-place path and an earlier version of these cases passed
        against the broken code for exactly that reason.
        """
        raw = "---\nkeep: me\n---\nBody\n"
        new = md.edit_frontmatter(raw, {"status": value})
        assert md.parse(new).frontmatter["status"] == value
        assert md.parse(new).frontmatter["keep"] == "me"

    def test_a_list_of_scalars_round_trips(self) -> None:
        new = md.edit_frontmatter("Body\n", {"tags": ["one", "two/three"]})
        assert md.parse(new).frontmatter["tags"] == ["one", "two/three"]

    def test_a_nested_value_falls_back_to_a_re_dump_but_stays_correct(self) -> None:
        """The escape hatch: correct content, and a formatting diff we accept."""
        raw = "---\na: 1\n---\nBody\n"
        new = md.edit_frontmatter(raw, {"nested": {"x": [1, 2]}})
        parsed = md.parse(new)
        assert parsed.frontmatter["nested"] == {"x": [1, 2]}
        assert parsed.frontmatter["a"] == 1
        assert parsed.body == "Body\n"

    def test_a_value_with_a_quote_survives(self) -> None:
        new = md.edit_frontmatter("Body\n", {"title": 'He said "no"'})
        assert md.parse(new).frontmatter["title"] == 'He said "no"'

    def test_what_we_write_is_what_pyyaml_reads(self) -> None:
        """Belt and braces on the hand-rolled scalar writer.

        Note the `raw` with a block in it: without one this goes to `_dump_block`
        and tests PyYAML rather than us. See the parametrized case above.
        """
        values = {
            "a": "true",
            "b": "x: y",
            "c": "-dash",
            "d": "#hash",
            "e": "100%",
            "f": "Budget #2026 review",
        }
        new = md.edit_frontmatter("---\nkeep: me\n---\nBody\n", values)
        inner = new.split("---\n")[1]
        assert yaml.safe_load(inner) == {"keep": "me", **values}

    def test_a_list_item_with_a_hash_keeps_everything_after_it(self) -> None:
        """The list path stringifies each item and quotes it the same way."""
        tags = ["proj #1", "ok", "#lead", "a: b"]
        new = md.edit_frontmatter("---\nkeep: me\n---\nBody\n", {"tags": tags})
        assert md.parse(new).frontmatter["tags"] == tags

    @pytest.mark.parametrize(
        "value",
        ["plain", "two words", "path/to/note", "CamelCase", "with-dash", "e-mail@host"],
    )
    def test_an_ordinary_string_is_still_written_bare(self, value: str) -> None:
        """The quoting must stay narrow, or every note gains quotes it did not have.

        `vault_set_properties` promises a minimal diff, and a value that suddenly
        acquires quotes is noise in every diff the customer reads after it.
        """
        new = md.edit_frontmatter("---\nkeep: me\n---\nBody\n", {"status": value})
        assert f"status: {value}\n" in new

    @pytest.mark.parametrize("value", ["2026-02-30", "2026-13-01", "2026-01-01 25:00:00"])
    def test_a_mistyped_date_does_not_reach_the_caller_as_a_crash(self, value: str) -> None:
        """PyYAML raises ValueError, not YAMLError, when it builds a date.

        `2026-02-30` is a plausible typo, and asking the parser whether a value
        round-trips means catching everything the parser can throw. Treating only
        YAMLError as "no" turned a bad property into an exception out of
        `vault_set_properties`.
        """
        new = md.edit_frontmatter("---\nkeep: me\n---\nBody\n", {"due": value})
        assert md.parse(new).frontmatter["due"] == value

    @pytest.mark.parametrize("value", ["line1\rline2", "vertical\x0btab", "form\x0cfeed"])
    def test_a_control_character_falls_back_rather_than_folding_to_a_space(
        self, value: str
    ) -> None:
        """Quoting is not automatically safe, so the quoted form is checked too.

        Escaping covers backslash, quote and newline. A carriage return survives
        into the double-quoted scalar and YAML folds it back to a space, so the
        value read back short a character and nobody was told.
        """
        new = md.edit_frontmatter("---\nkeep: me\n---\nBody\n", {"note": value})
        parsed = md.parse(new)
        assert parsed.frontmatter["note"] == value
        assert parsed.frontmatter["keep"] == "me"


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
