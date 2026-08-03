"""The tool layer, against the fake provider. No filesystem.

Tests what is the tool layer's own job: clamping a page size, refusing a
destructive call without confirmation, passing arguments through unmangled, and
mapping a dataclass onto the wire model. When one of these fails it is the tool
layer that is wrong, which is the point of testing it separately from a real
vault.

Tools are called through the FastMCP app rather than by reaching for the closure,
so the registration, the schema and the argument names are exercised too. A tool
renamed by accident turns these red.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Dict

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from knap_mcp.error_handling import ConfirmationRequired

EXPECTED_TOOLS = [
    "vault_list_vaults",
    "vault_list_folders",
    "vault_list_notes",
    "vault_search",
    "vault_read_note",
    "vault_read_chunk",
    "vault_create_note",
    "vault_append_note",
    "vault_patch_section",
    "vault_set_properties",
    "vault_update_note",
    "vault_move_note",
    "vault_delete_note",
    "vault_backlinks",
    "vault_links",
    "vault_list_tags",
    "vault_daily_note",
    "vault_get_attachment",
    "vault_put_attachment",
]


async def call(handler, name: str, **kwargs: Any) -> Dict[str, Any]:
    """Call a tool the way a client does, and hand back the parsed result."""
    result = await handler.app.call_tool(name, kwargs)
    payload = result[1] if isinstance(result, tuple) else result
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str):
        return json.loads(payload)
    raise AssertionError(f"unexpected result shape from {name}: {type(payload)}")


class TestRegistration:
    async def test_every_tool_is_registered_under_its_documented_name(self, handler) -> None:
        """The names are the API. A rename is a breaking change for every client."""
        registered = [tool.name for tool in await handler.app.list_tools()]
        assert registered == EXPECTED_TOOLS

    async def test_reads_are_annotated_read_only(self, handler) -> None:
        by_name = {tool.name: tool for tool in await handler.app.list_tools()}
        for name in ("vault_search", "vault_read_note", "vault_backlinks", "vault_list_tags"):
            assert by_name[name].annotations.readOnlyHint is True
            assert by_name[name].annotations.destructiveHint is False

    async def test_only_the_three_irreversible_tools_are_destructive(self, handler) -> None:
        """Principle 4, as a test.

        Marking the additive writes destructive too would be easy to defend and
        would teach every client that the confirm prompt is a formality.
        """
        by_name = {tool.name: tool for tool in await handler.app.list_tools()}
        destructive = {
            name
            for name, tool in by_name.items()
            if tool.annotations and tool.annotations.destructiveHint
        }
        assert destructive == {"vault_update_note", "vault_move_note", "vault_delete_note"}

    async def test_every_tool_has_a_description(self, handler) -> None:
        """The descriptions reach the model and are how it learns the rules."""
        for tool in await handler.app.list_tools():
            assert tool.description and len(tool.description) > 80, tool.name


class TestConfirmation:
    """A destructive tool called without confirm=true must refuse, every time."""

    @pytest.mark.parametrize(
        "name,kwargs",
        [
            ("vault_update_note", {"path": "Note.md", "body": "x", "expected_rev": "rev-1"}),
            ("vault_move_note", {"path": "Note.md", "destination": "Other.md"}),
            ("vault_delete_note", {"path": "Note.md"}),
        ],
    )
    async def test_refused_without_confirm(self, handler, name, kwargs) -> None:
        with pytest.raises(ToolError) as caught:
            await call(handler, name, **kwargs)
        assert "confirm=true" in str(caught.value)

    @pytest.mark.parametrize(
        "name,kwargs",
        [
            ("vault_update_note", {"path": "Note.md", "body": "x", "expected_rev": "rev-1"}),
            ("vault_move_note", {"path": "Note.md", "destination": "Other.md"}),
            ("vault_delete_note", {"path": "Note.md"}),
        ],
    )
    async def test_allowed_with_confirm(self, handler, name, kwargs) -> None:
        await call(handler, name, confirm=True, **kwargs)

    async def test_nothing_reaches_the_provider_when_refused(self, handler, fake_provider) -> None:
        """A refusal must happen before the write, not after it."""
        with pytest.raises(ToolError):
            await call(handler, "vault_delete_note", path="Note.md")
        assert "delete" not in fake_provider.call_names()

    async def test_the_message_says_what_to_do_next(self, handler) -> None:
        """An AI reads this out loud, so it has to be an instruction."""
        with pytest.raises(ToolError) as caught:
            await call(handler, "vault_delete_note", path="Note.md")
        message = str(caught.value)
        assert "approval" in message
        assert "call again with confirm=true" in message

    async def test_the_additive_writes_need_no_confirmation(self, handler) -> None:
        await call(handler, "vault_create_note", path="New.md", body="x")
        await call(handler, "vault_append_note", path="New.md", content="y")
        await call(handler, "vault_set_properties", path="New.md", properties={"a": 1})
        await call(handler, "vault_patch_section", path="New.md", heading="H", content="z")

    async def test_overwriting_an_attachment_needs_confirmation(self, handler) -> None:
        payload = base64.b64encode(b"x").decode()
        await call(handler, "vault_put_attachment", path="a.png", content_base64=payload)
        with pytest.raises(ToolError) as caught:
            await call(
                handler,
                "vault_put_attachment",
                path="a.png",
                content_base64=payload,
                overwrite=True,
            )
        assert "confirm=true" in str(caught.value)


class TestLimits:
    async def test_a_missing_limit_uses_the_server_default(self, handler, fake_provider) -> None:
        await call(handler, "vault_list_notes")
        _, kwargs = fake_provider.last_call("list_notes")
        assert kwargs["limit"] == handler.default_limit

    async def test_an_oversized_limit_is_clamped(self, handler, fake_provider) -> None:
        """A client asking for ten thousand notes gets the maximum, not a refusal."""
        await call(handler, "vault_search", limit=100000)
        _, kwargs = fake_provider.last_call("search")
        assert kwargs["limit"] == handler.max_limit

    async def test_a_zero_or_negative_limit_becomes_one(self, handler, fake_provider) -> None:
        await call(handler, "vault_search", limit=0)
        _, kwargs = fake_provider.last_call("search")
        assert kwargs["limit"] == 1

    async def test_a_negative_offset_becomes_zero(self, handler, fake_provider) -> None:
        await call(handler, "vault_search", offset=-5)
        _, kwargs = fake_provider.last_call("search")
        assert kwargs["offset"] == 0

    async def test_the_result_reports_the_limit_actually_used(self, handler) -> None:
        """A client paging on a number it did not get would skip notes."""
        result = await call(handler, "vault_search", limit=100000)
        assert result["limit"] == handler.max_limit


class TestArgumentPassThrough:
    async def test_search_filters_reach_the_provider_unmangled(
        self, handler, fake_provider
    ) -> None:
        await call(
            handler,
            "vault_search",
            query="renewal",
            folder="Areas",
            tag="client",
            property="status",
            property_value="active",
            since="2026-01-01",
        )
        args, kwargs = fake_provider.last_call("search")
        assert args[0] == "renewal"
        assert kwargs["folder"] == "Areas"
        assert kwargs["tag"] == "client"
        # The tool argument is `property`; the protocol's is `prop`, because
        # `property` is a builtin. Pinned so the mapping cannot quietly break.
        assert kwargs["prop"] == "status"
        assert kwargs["prop_value"] == "active"
        assert kwargs["since"] == "2026-01-01"

    async def test_append_with_a_section_becomes_a_patch(self, handler, fake_provider) -> None:
        """Same tool for the user, two provider calls underneath."""
        await call(handler, "vault_append_note", path="Note.md", content="x", section="Log")
        args, kwargs = fake_provider.last_call("patch_section")
        assert args[1] == "Log"
        assert kwargs["mode"] == "append"

    async def test_append_without_a_section_is_a_plain_append(self, handler, fake_provider) -> None:
        await call(handler, "vault_append_note", path="Note.md", content="x")
        _, kwargs = fake_provider.last_call("write")
        assert kwargs["mode"] == "append"

    async def test_update_passes_the_expected_rev_through(self, handler, fake_provider) -> None:
        await call(
            handler,
            "vault_update_note",
            path="Note.md",
            body="x",
            expected_rev="rev-1",
            confirm=True,
        )
        _, kwargs = fake_provider.last_call("write")
        assert kwargs["mode"] == "overwrite"
        assert kwargs["expected_rev"] == "rev-1"

    async def test_move_defaults_to_rewriting_links(self, handler, fake_provider) -> None:
        await call(handler, "vault_move_note", path="A.md", destination="B.md", confirm=True)
        _, kwargs = fake_provider.last_call("move")
        assert kwargs["update_links"] is True

    async def test_an_invalid_patch_mode_is_refused(self, handler) -> None:
        with pytest.raises(ToolError) as caught:
            await call(
                handler,
                "vault_patch_section",
                path="N.md",
                heading="H",
                content="x",
                mode="sideways",
            )
        assert "replace" in str(caught.value)

    async def test_an_invalid_periodic_kind_is_refused(self, handler) -> None:
        with pytest.raises(ToolError) as caught:
            await call(handler, "vault_daily_note", kind="fortnightly")
        assert "daily" in str(caught.value)

    async def test_empty_required_text_is_refused(self, handler) -> None:
        with pytest.raises(ToolError):
            await call(handler, "vault_read_note", path="   ")

    async def test_set_properties_needs_at_least_one_key(self, handler) -> None:
        with pytest.raises(ToolError) as caught:
            await call(handler, "vault_set_properties", path="Note.md", properties={})
        assert "at least one" in str(caught.value)


class TestResultShapes:
    async def test_a_write_hands_back_the_rev(self, handler) -> None:
        """Without it the client cannot make the next overwrite."""
        result = await call(handler, "vault_create_note", path="New.md", body="x")
        assert result["rev"] == "rev-2"
        assert result["created"] is True

    async def test_a_move_reports_what_it_relinked(self, handler) -> None:
        result = await call(
            handler, "vault_move_note", path="A.md", destination="B.md", confirm=True
        )
        assert result["relinked"] == ["Other.md"]

    async def test_a_delete_says_where_the_note_went(self, handler) -> None:
        """So the user can be told it is recoverable."""
        result = await call(handler, "vault_delete_note", path="Note.md", confirm=True)
        assert result["deleted"] is True
        assert "trash" in result["detail"].lower()

    async def test_a_read_reports_truncation_and_the_real_length(self, handler) -> None:
        result = await call(handler, "vault_read_note", path="Note.md", max_chars=4)
        assert result["truncated"] is True
        assert result["body_length"] > 4

    async def test_a_search_reports_whether_the_scan_was_capped(self, handler) -> None:
        result = await call(handler, "vault_search", query="x")
        assert result["scan_truncated"] is False

    async def test_an_attachment_comes_back_base64(self, handler) -> None:
        result = await call(handler, "vault_get_attachment", path="a.png")
        assert base64.b64decode(result["content_base64"]) == b"abc"

    async def test_invalid_base64_is_refused_before_the_write(self, handler, fake_provider) -> None:
        with pytest.raises(ToolError) as caught:
            await call(handler, "vault_put_attachment", path="a.png", content_base64="not base64!!")
        assert "base64" in str(caught.value)
        assert "write_binary" not in fake_provider.call_names()

    async def test_every_result_names_the_vault_that_answered(self, handler) -> None:
        """With several vaults configured, a client has to know which one replied."""
        for name, kwargs in (
            ("vault_list_notes", {}),
            ("vault_search", {}),
            ("vault_read_note", {"path": "Note.md"}),
            ("vault_list_tags", {}),
        ):
            result = await call(handler, name, **kwargs)
            assert result["vault"] == "fake"


class TestVaultSelection:
    async def test_list_vaults_reports_the_configured_one(self, handler) -> None:
        result = await call(handler, "vault_list_vaults")
        assert result["total"] == 1
        assert result["vaults"][0]["id"] == "fake"
        assert result["vaults"][0]["default"] is True

    async def test_the_vault_name_is_accepted_as_well_as_the_id(self, handler) -> None:
        await call(handler, "vault_list_tags", vault="fake")
        await call(handler, "vault_list_tags", vault="Fake vault")

    async def test_an_unknown_vault_is_refused_rather_than_ignored(self, handler) -> None:
        """Quietly serving the only vault we have is how an AI writes to the
        wrong place."""
        with pytest.raises(ToolError) as caught:
            await call(handler, "vault_list_tags", vault="somebody-elses-vault")
        assert "vault_list_vaults" in str(caught.value)


class TestUsageHook:
    async def test_the_hook_fires_on_success(self, handler, monkeypatch) -> None:
        seen = []
        monkeypatch.setattr(
            type(handler), "_track_usage", lambda self, sub, tool: seen.append((sub, tool))
        )
        await call(handler, "vault_list_tags")
        assert seen == [("stdio", "vault_list_tags")]

    async def test_the_hook_does_not_fire_on_failure(self, handler, monkeypatch) -> None:
        """Documented behaviour, and why the hosted package does analytics one
        level down: this hook cannot see a failed call."""
        seen = []
        monkeypatch.setattr(
            type(handler), "_track_usage", lambda self, sub, tool: seen.append(tool)
        )
        with pytest.raises(ToolError):
            await call(handler, "vault_delete_note", path="Note.md")
        assert seen == []


class TestConfirmationRequiredError:
    def test_the_wording_is_identical_across_tools(self) -> None:
        """One phrasing an AI learns once, for every destructive tool."""
        first = str(ConfirmationRequired("Deleting X"))
        second = str(ConfirmationRequired("Moving Y"))
        assert first.split("needs confirm=true")[1] == second.split("needs confirm=true")[1]
