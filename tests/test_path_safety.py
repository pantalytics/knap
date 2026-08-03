"""Path confinement. The security regression file.

This is the one vulnerability class that ends with one customer reading another
customer's notes, so it gets its own file and every case that has ever been used
against a file-serving application stays in it.

Two halves, both needed:

* the string half, which runs without a filesystem and pins what ``normalize``
  refuses;
* the resolved half, which builds real symlinks in a tmpdir, because a symlink
  inside the vault pointing out of it passes every string check there is and is
  the case a blacklist cannot catch.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from knap_mcp.providers.filesystem import paths
from knap_mcp.providers.protocol import PathNotAllowedError


class TestNormalizeRefusals:
    """Everything ``normalize`` must refuse, and why it is on the list."""

    @pytest.mark.parametrize(
        "candidate",
        [
            "../secrets.md",
            "../../etc/passwd",
            "Notes/../../etc/passwd",
            "Notes/../../../etc/passwd",
            "a/b/../../../outside.md",
            # A climb that comes back is legitimate to the OS and is still a
            # caller who does not know where they are pointing.
            "Notes/../Notes/fine.md",
            "..",
            "../",
            "./../x.md",
        ],
    )
    def test_dot_dot_is_refused_everywhere(self, candidate: str) -> None:
        with pytest.raises(PathNotAllowedError):
            paths.normalize(candidate)

    @pytest.mark.parametrize(
        "candidate",
        [
            "/etc/passwd",
            "/",
            "//etc/passwd",
            "C:\\Windows\\System32\\config\\SAM",
            "c:/windows/system32",
        ],
    )
    def test_absolute_paths_are_refused(self, candidate: str) -> None:
        with pytest.raises(PathNotAllowedError):
            paths.normalize(candidate)

    def test_backslash_cannot_smuggle_a_climb(self) -> None:
        """Windows separators normalize, and must not become a way past the check.

        ``..\\..\\etc`` would split into one harmless-looking segment if the
        separator were not normalized before the ``..`` check.
        """
        with pytest.raises(PathNotAllowedError):
            paths.normalize("..\\..\\etc\\passwd")

    @pytest.mark.parametrize("candidate", ["", "   ", ".", "./", "./."])
    def test_empty_and_bare_dot_are_refused(self, candidate: str) -> None:
        with pytest.raises(PathNotAllowedError):
            paths.normalize(candidate)

    def test_null_byte_is_refused(self) -> None:
        """A NUL truncates the path at the C layer, so 'a.md\\x00.png' is 'a.md'."""
        with pytest.raises(PathNotAllowedError):
            paths.normalize("note.md\x00.png")

    @pytest.mark.parametrize("candidate", ["note\n.md", "note\t.md", "note\x7f.md"])
    def test_control_characters_are_refused(self, candidate: str) -> None:
        with pytest.raises(PathNotAllowedError):
            paths.normalize(candidate)

    @pytest.mark.parametrize("candidate", ["note.md.", "Folder./note.md", "a/b."])
    def test_a_trailing_dot_is_refused(self, candidate: str) -> None:
        """Windows drops a trailing dot, so "note.md." and "note.md" are one file.

        Two spellings of one file means two revisions of one file, which is a way
        past an ``expected_rev`` check rather than something a client types by
        accident. Refused rather than trimmed for exactly that reason.
        """
        with pytest.raises(PathNotAllowedError):
            paths.normalize(candidate)


class TestNormalizeAccepts:
    """What must keep working. A confinement check that refuses real notes is a bug."""

    @pytest.mark.parametrize(
        "candidate,expected",
        [
            ("note.md", "note.md"),
            ("Areas/Work/Acme.md", "Areas/Work/Acme.md"),
            ("Areas//Work/Acme.md", "Areas/Work/Acme.md"),
            ("Areas/./Work/Acme.md", "Areas/Work/Acme.md"),
            ("Areas\\Work\\Acme.md", "Areas/Work/Acme.md"),
            (" Areas/Work/Acme.md ", "Areas/Work/Acme.md"),
            # Trimmed per segment, not just at the ends of the whole string:
            # Windows strips this at create time, so it is the same file.
            ("Folder /note.md", "Folder/note.md"),
            ("Areas/ Work /Acme.md", "Areas/Work/Acme.md"),
            # An internal space is an ordinary filename and must survive.
            ("note two.md", "note two.md"),
            ("Reünie 2026.md", "Reünie 2026.md"),
            ("100% done.md", "100% done.md"),
            ("note (draft).md", "note (draft).md"),
            (".obsidian/app.json", ".obsidian/app.json"),
        ],
    )
    def test_ordinary_paths_pass(self, candidate: str, expected: str) -> None:
        assert paths.normalize(candidate) == expected

    def test_unicode_is_normalized_to_nfc(self) -> None:
        """macOS hands back NFD, Linux stores what it was given.

        Without normalizing, the same note name arrives in two encodings and a
        rev computed on one platform never matches on the other.
        """
        decomposed = "Reu\u0308nie.md"  # u + combining diaeresis
        composed = "Re\u00fcnie.md"  # precomposed u-umlaut
        assert paths.normalize(decomposed) == paths.normalize(composed)


class TestResolveInVault:
    """The resolved half: real directories, real symlinks."""

    @pytest.fixture
    def root(self, tmp_path: Path) -> Path:
        vault = tmp_path / "vault"
        (vault / "Areas").mkdir(parents=True)
        (vault / "Areas" / "note.md").write_text("# note\n")
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secrets.md").write_text("# not yours\n")
        return vault

    def test_a_note_inside_resolves(self, root: Path) -> None:
        assert paths.resolve_in_vault(root, "Areas/note.md").is_file()

    def test_the_vault_root_itself_is_refused(self, root: Path) -> None:
        """'.' normalizes to nothing and must not hand back the root directory."""
        with pytest.raises(PathNotAllowedError):
            paths.resolve_in_vault(root, ".")

    @pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
    def test_a_symlinked_file_pointing_out_is_refused(self, root: Path, tmp_path: Path) -> None:
        """The case a string check cannot see.

        'escape.md' is a perfectly ordinary vault-relative path. It is only after
        the OS resolves it that it turns out to be a file in another directory.
        """
        (root / "escape.md").symlink_to(tmp_path / "outside" / "secrets.md")
        with pytest.raises(PathNotAllowedError):
            paths.resolve_in_vault(root, "escape.md")

    @pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
    def test_a_symlinked_directory_pointing_out_is_refused(
        self, root: Path, tmp_path: Path
    ) -> None:
        """The same trick one level up, which is the version people actually use."""
        (root / "Linked").symlink_to(tmp_path / "outside", target_is_directory=True)
        with pytest.raises(PathNotAllowedError):
            paths.resolve_in_vault(root, "Linked/secrets.md")

    @pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
    def test_a_symlink_staying_inside_is_allowed(self, root: Path) -> None:
        """Confinement, not a symlink ban. A link within the vault is fine."""
        (root / "alias.md").symlink_to(root / "Areas" / "note.md")
        assert paths.resolve_in_vault(root, "alias.md").is_file()

    def test_a_sibling_directory_with_a_shared_prefix_is_refused(self, tmp_path: Path) -> None:
        """`/vaults/acme-evil` is not inside `/vaults/acme`.

        A `startswith` on the string forms thinks it is, which is exactly how one
        tenant reaches another when vault directories are named after customers.
        """
        acme = tmp_path / "vaults" / "acme"
        evil = tmp_path / "vaults" / "acme-evil"
        acme.mkdir(parents=True)
        evil.mkdir(parents=True)
        (evil / "notes.md").write_text("# theirs\n")
        assert str(evil).startswith(str(acme))  # the trap this guards
        with pytest.raises(PathNotAllowedError):
            paths.resolve_in_vault(acme, "../acme-evil/notes.md")

    def test_must_exist_refuses_a_missing_note(self, root: Path) -> None:
        with pytest.raises(PathNotAllowedError):
            paths.resolve_in_vault(root, "nope.md", must_exist=True)

    def test_a_path_for_a_note_not_yet_written_resolves(self, root: Path) -> None:
        """A create has to be able to validate a path before the file exists."""
        target = paths.resolve_in_vault(root, "Areas/new note.md")
        assert not target.exists()
        assert str(target).startswith(str(root.resolve()))


class TestErrorMessages:
    """What the caller is told, which on a hosted deployment is a security property."""

    def test_the_message_never_carries_the_resolved_path(self, tmp_path: Path) -> None:
        """An error naming the real location answers the probe it was sent to make."""
        vault = tmp_path / "srv" / "knap" / "prod" / "41" / "vault"
        vault.mkdir(parents=True)
        with pytest.raises(PathNotAllowedError) as caught:
            paths.resolve_in_vault(vault, "../../../../etc/passwd")
        message = str(caught.value)
        assert "srv" not in message
        assert str(tmp_path) not in message

    def test_a_symlink_escape_and_a_dot_escape_read_the_same(self, tmp_path: Path) -> None:
        """Whether the escape was spelled with dots or built from a symlink is
        not the caller's business: telling them narrows the search."""
        vault = tmp_path / "vault"
        vault.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (vault / "escape.md").symlink_to(outside)

        with pytest.raises(PathNotAllowedError) as via_symlink:
            paths.resolve_in_vault(vault, "escape.md/x.md")
        assert "outside the vault" in str(via_symlink.value)


class TestHiddenAndNotes:
    @pytest.mark.parametrize(
        "candidate,hidden",
        [
            (".obsidian/app.json", True),
            (".trash/gone.md", True),
            ("Areas/.hidden/note.md", True),
            ("Areas/Work/note.md", False),
            ("Archive/.trash-me.md", True),
        ],
    )
    def test_is_hidden(self, candidate: str, hidden: bool) -> None:
        assert paths.is_hidden(candidate) is hidden

    @pytest.mark.parametrize(
        "candidate,note",
        [("a.md", True), ("a.MD", True), ("a.markdown", False), ("a.png", False), ("a", False)],
    )
    def test_is_note(self, candidate: str, note: bool) -> None:
        assert paths.is_note(candidate) is note


class TestWalkNotes:
    def test_dot_directories_are_skipped_by_default(self, vault_root: Path) -> None:
        found = {paths.to_relative(vault_root, p) for p in paths.walk_notes(vault_root)}
        assert "Areas/Work/Acme.md" in found
        assert not any(rel.startswith(".trash/") for rel in found)

    def test_include_hidden_reaches_them(self, vault_root: Path) -> None:
        found = {
            paths.to_relative(vault_root, p)
            for p in paths.walk_notes(vault_root, include_hidden=True)
        }
        assert ".trash/Deleted thing.md" in found

    @pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
    def test_a_symlinked_note_is_not_listed_twice(self, vault_root: Path) -> None:
        """A symlink inside the vault is legal to read and is not a second note."""
        (vault_root / "alias.md").symlink_to(vault_root / "Areas" / "Work" / "Acme.md")
        found = [paths.to_relative(vault_root, p) for p in paths.walk_notes(vault_root)]
        assert len(found) == len(set(found))
        assert "alias.md" not in found
