"""Writing frontmatter without rewriting it.

Split out of `test_markdown.py` for the same reason `frontmatter.py` was split
out of `markdown.py`: it is a self-contained job with a rule of its own, and the
file was over the 500-line budget.

The rule is that `vault_set_properties` promises the body and every property it
was not asked about come out byte-identical, so the block is edited line by line
instead of re-dumped. That only works if a hand-rolled line reads back as the
value it was written for, which is what most of this file is about.
"""

from __future__ import annotations

import pytest
import yaml

from knap_mcp.providers.filesystem import markdown as md


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
