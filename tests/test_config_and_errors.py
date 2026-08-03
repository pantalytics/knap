"""Configuration, error sanitizing, and the CLI's --check.

The sanitizer tests are the ones that matter here. On a hosted deployment an
error carrying `/srv/knap/prod/41/second-brain/tree/...` tells the caller the
storage layout and the tenant id, and every one of those strings arrives for free
inside an `OSError` from `open()`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from knap_mcp.config import KnapConfig, load_config, reset_config
from knap_mcp.error_handling import (
    ConfirmationRequired,
    ValidationError,
    describe,
    sanitize,
)
from knap_mcp.providers.protocol import (
    NoteNotFoundError,
    PathNotAllowedError,
    RevisionMismatch,
)


class TestConfigValidation:
    def test_a_vault_path_is_required(self) -> None:
        """No fallbacks: guessing a directory means opening one nobody named."""
        with pytest.raises(ValueError, match="KNAP_VAULT_PATH"):
            KnapConfig()

    def test_a_non_directory_is_refused(self, tmp_path: Path) -> None:
        note = tmp_path / "note.md"
        note.write_text("x")
        with pytest.raises(ValueError, match="not a directory"):
            KnapConfig(vault_path=str(note))

    def test_skip_validation_is_how_the_hosted_layer_builds_one(self) -> None:
        """Multi-tenant has no single vault at startup, so validation is skipped."""
        assert KnapConfig(skip_validation=True).vault_path == ""

    def test_the_display_name_falls_back_to_the_directory(self, vault_root: Path) -> None:
        assert KnapConfig(vault_path=str(vault_root)).display_name == "vault"

    def test_an_explicit_name_wins(self, vault_root: Path) -> None:
        assert KnapConfig(vault_path=str(vault_root), vault_name="Second brain").display_name == (
            "Second brain"
        )

    def test_a_default_limit_above_the_maximum_is_refused(self, vault_root: Path) -> None:
        with pytest.raises(ValueError, match="cannot exceed"):
            KnapConfig(vault_path=str(vault_root), default_limit=200, max_limit=100)

    @pytest.mark.parametrize("port", [0, -1, 70000])
    def test_a_bad_port_is_refused(self, vault_root: Path, port: int) -> None:
        with pytest.raises(ValueError, match="between 1 and 65535"):
            KnapConfig(vault_path=str(vault_root), port=port)

    def test_an_unknown_log_level_is_refused(self, vault_root: Path) -> None:
        with pytest.raises(ValueError, match="log level"):
            KnapConfig(vault_path=str(vault_root), log_level="CHATTY")

    def test_an_unknown_provider_is_refused(self, vault_root: Path) -> None:
        with pytest.raises(ValueError, match="KNAP_VAULT_PROVIDER"):
            KnapConfig(vault_path=str(vault_root), vault_provider="magic")

    def test_a_tilde_in_the_path_is_expanded(self, vault_root: Path, monkeypatch) -> None:
        monkeypatch.setenv("HOME", str(vault_root.parent))
        config = KnapConfig(vault_path="~/vault")
        assert config.vault_root == vault_root.resolve()


class TestConfigFromEnv:
    def test_the_environment_is_read(self, vault_root: Path, monkeypatch) -> None:
        reset_config()
        monkeypatch.setenv("KNAP_VAULT_PATH", str(vault_root))
        monkeypatch.setenv("KNAP_MCP_MAX_LIMIT", "42")
        monkeypatch.setenv("KNAP_MCP_TRANSPORT", "streamable-http")
        config = load_config()
        assert config.max_limit == 42
        assert config.transport == "streamable-http"

    def test_a_non_numeric_integer_is_a_clear_error(self, vault_root: Path, monkeypatch) -> None:
        reset_config()
        monkeypatch.setenv("KNAP_VAULT_PATH", str(vault_root))
        monkeypatch.setenv("KNAP_MCP_MAX_LIMIT", "loads")
        with pytest.raises(ValueError, match="valid integer"):
            load_config()

    def test_a_missing_env_file_is_named(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="not found"):
            load_config(tmp_path / "absent.env")


class TestSanitize:
    """Absolute paths must not leave the server. Relative ones are the client's own."""

    @pytest.mark.parametrize(
        "message,must_be_gone",
        [
            (
                "[Errno 2] No such file or directory: '/srv/knap/prod/41/vault/tree/note.md'",
                ["/srv", "prod", "41", "tree"],
            ),
            # Unquoted, with a space in the filename: the tenant directory is the
            # part that must not survive.
            ("cannot open /var/lib/knap/tenant-9/secret plans.md", ["/var", "tenant-9"]),
            ("failed at C:\\Users\\rutger\\vaults\\second-brain\\note.md", ["C:", "rutger"]),
            ("stat failed on /srv/knap/staging/7/vault", ["/srv", "staging"]),
        ],
    )
    def test_an_absolute_path_is_replaced(self, message: str, must_be_gone: list) -> None:
        cleaned = sanitize(message)
        for fragment in must_be_gone:
            assert fragment not in cleaned, f"{fragment!r} survived in {cleaned!r}"
        assert "<path>" in cleaned or "<home>" in cleaned

    def test_the_sentence_around_a_relative_path_is_not_eaten(self) -> None:
        """A class that allowed spaces turned this into "Areas<path>"."""
        assert sanitize("Areas/Work/Acme.md does not exist") == "Areas/Work/Acme.md does not exist"

    def test_a_home_directory_loses_the_username(self, tmp_path: Path) -> None:
        assert "rutger" not in sanitize("/home/rutger/vaults/x/note.md")

    def test_a_vault_relative_path_survives(self) -> None:
        """The client passed it in; hiding it would make errors unusable."""
        assert "Areas/Work/Acme.md" in sanitize("Areas/Work/Acme.md does not exist")

    def test_an_empty_message_is_fine(self) -> None:
        assert sanitize("") == ""


class TestDescribe:
    def test_a_revision_mismatch_says_what_to_do(self) -> None:
        """The most important error in the product, read out loud by an AI."""
        message = describe(RevisionMismatch("Areas/Acme.md", "old", "new"))
        assert "changed since you read it" in message
        assert "Read the note again" in message
        assert "Do not retry without expected_rev" in message

    def test_a_path_refusal_stays_scrubbed(self) -> None:
        message = describe(PathNotAllowedError("Path not allowed: resolves outside the vault"))
        assert "/srv" not in message
        assert "outside the vault" in message

    def test_an_oserror_is_reported_without_its_path(self) -> None:
        exc = OSError(2, "No such file or directory")
        exc.filename = "/srv/knap/prod/41/vault/note.md"
        assert "/srv" not in describe(OSError(f"{exc}: '{exc.filename}'"))

    def test_a_permission_error_is_plain_language(self) -> None:
        assert "permission denied" in describe(PermissionError(13, "Permission denied"))

    def test_a_note_not_found_keeps_its_message(self) -> None:
        assert "Nope.md" in describe(NoteNotFoundError("Nope.md does not exist"))

    def test_a_validation_error_passes_through(self) -> None:
        assert describe(ValidationError("path is required")) == "path is required"

    def test_a_confirmation_error_says_to_get_approval(self) -> None:
        assert "approval" in describe(ConfirmationRequired("Deleting a note"))


class TestCheckCommand:
    def test_check_reports_the_vault_and_exits_zero(self, vault_root: Path, capsys) -> None:
        """The first thing to run against a real vault, and what separates
        "the vault is unreadable" from "the client is not connecting"."""
        from knap_mcp.__main__ import main

        reset_config()
        code = main(["--vault", str(vault_root), "--check"])
        out = capsys.readouterr().out
        assert code == 0
        assert "Notes:" in out
        assert "healthy" in out

    def test_check_reports_the_tags_it_found(self, vault_root: Path, capsys) -> None:
        from knap_mcp.__main__ import main

        reset_config()
        main(["--vault", str(vault_root), "--check"])
        assert "#project" in capsys.readouterr().out

    def test_a_bad_vault_path_is_a_message_not_a_traceback(self, tmp_path: Path, capsys) -> None:
        from knap_mcp.__main__ import main

        reset_config()
        code = main(["--vault", str(tmp_path / "nope"), "--check"])
        assert code == 2
        assert "Configuration error" in capsys.readouterr().err

    def test_the_health_snapshot_never_carries_the_vault_path(self, vault_root: Path) -> None:
        """`/healthz` answers without a session on a hosted deployment."""
        from knap_mcp.server import KnapMCPServer

        reset_config()
        server = KnapMCPServer(KnapConfig(vault_path=str(vault_root)))
        server._ensure_vault()
        status = server.get_health_status()
        assert str(vault_root) not in repr(status)
        assert status["vault"]["name"] == "vault"
