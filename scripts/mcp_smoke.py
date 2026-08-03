"""End-to-end smoke test: a real MCP client talking to Knap over stdio.

Everything else in the test suite calls the tool functions in-process. This
launches the server the way Claude Desktop does -- a subprocess, stdio transport,
a real initialize handshake -- and drives it through the flow a customer's first
session actually takes: look around, read something, capture a thought, and be
refused when a destructive call arrives without confirmation.

It exists because in-process tests cannot catch the things that break a real
client: a log line written to stdout instead of stderr corrupts the protocol
stream, and a tool whose return type will not serialize fails only when something
tries to send it.

    python scripts/mcp_smoke.py [--vault PATH] [--keep]

With no --vault it builds a throwaway one, so this runs anywhere.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

SEED: Dict[str, str] = {
    ".obsidian/daily-notes.json": json.dumps({"folder": "Journal", "format": "YYYY-MM-DD"}),
    "Areas/Work/Acme.md": (
        "---\nstatus: active\ntags: [client, project/acme]\naliases: [Acme Corp]\n---\n"
        "# Acme\n\nRenewal is due. See [[Meeting notes]].\n\n## Log\n\n- Kickoff done\n"
    ),
    "Projects/Meeting notes.md": "# Meeting notes\n\nSpoke to [[Acme Corp]]. #meeting\n",
    "index.md": "# Home\n\n- [[Areas/Work/Acme]]\n",
}

PASS = "  ok  "
FAIL = " FAIL "
_failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{PASS if condition else FAIL}] {label}" + (f" -- {detail}" if detail else ""))
    if not condition:
        _failures.append(label)


def seed(root: Path) -> None:
    for rel, content in SEED.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def payload(result: Any) -> Dict[str, Any]:
    """Pull the structured result out of a CallToolResult."""
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except ValueError:
                return {"text": text}
    return {}


async def run(vault: Path) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "knap_mcp"],
        env={
            "KNAP_VAULT_PATH": str(vault),
            "KNAP_VAULT_NAME": "Smoke vault",
            "KNAP_MCP_TRANSPORT": "stdio",
            "PYTHONPATH": str(REPO_ROOT),
        },
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            check("initialize handshake", init.serverInfo.name == "knap", init.serverInfo.name)
            check(
                "handshake carries the instructions",
                bool(init.instructions) and "vault_" in (init.instructions or ""),
            )

            listed = await session.list_tools()
            names = [tool.name for tool in listed.tools]
            check("all 19 tools advertised", len(names) == 19, f"{len(names)} tools")
            check("tools are namespaced vault_*", all(n.startswith("vault_") for n in names))

            vaults = payload(await session.call_tool("vault_list_vaults", {}))
            check(
                "vault_list_vaults names the vault",
                vaults.get("vaults", [{}])[0].get("name") == "Smoke vault",
                str(vaults),
            )

            tags = payload(await session.call_tool("vault_list_tags", {}))
            tag_names = {row["tag"] for row in tags.get("tags", [])}
            check(
                "vault_list_tags expands nested tags",
                {"project", "project/acme", "client", "meeting"} <= tag_names,
                str(sorted(tag_names)),
            )

            found = payload(await session.call_tool("vault_search", {"tag": "project"}))
            check(
                "vault_search by tag finds notes",
                found.get("total", 0) >= 1,
                str(found.get("total")),
            )

            note = payload(await session.call_tool("vault_read_note", {"path": "Acme Corp"}))
            check(
                "vault_read_note resolves an alias to a path",
                note.get("path") == "Areas/Work/Acme.md",
                str(note.get("path")),
            )
            check("read hands back a rev", bool(note.get("rev")))
            resolved = {link["target"]: link["resolved_path"] for link in note.get("links", [])}
            check(
                "links come back resolved",
                resolved.get("Meeting notes") == "Projects/Meeting notes.md",
                str(resolved),
            )

            daily = payload(await session.call_tool("vault_daily_note", {"create": True}))
            check(
                "daily note lands in the vault's own folder",
                str(daily.get("path", "")).startswith("Journal/"),
                str(daily.get("path")),
            )

            captured = payload(
                await session.call_tool(
                    "vault_append_note",
                    {"path": daily["path"], "content": "- a thought from the train"},
                )
            )
            check("capture appends to today's note", bool(captured.get("rev")), str(captured))
            check(
                "the thought is on disk",
                "a thought from the train" in (vault / daily["path"]).read_text(),
            )

            patched = payload(
                await session.call_tool(
                    "vault_append_note",
                    {
                        "path": "Areas/Work/Acme.md",
                        "content": "- called them back",
                        "section": "Log",
                    },
                )
            )
            body = (vault / "Areas" / "Work" / "Acme.md").read_text()
            check(
                "section append lands inside the section",
                "- called them back" in body,
                str(patched)[:60],
            )
            check("the frontmatter survived a section append", body.startswith("---\n"))

            refused = await session.call_tool(
                "vault_delete_note", {"path": "Projects/Meeting notes.md"}
            )
            check(
                "delete without confirm is refused",
                bool(refused.isError) and "confirm=true" in str(payload(refused)),
                str(payload(refused))[:80],
            )
            check(
                "and the note is still there",
                (vault / "Projects" / "Meeting notes.md").exists(),
            )

            escaped = await session.call_tool("vault_read_note", {"path": "../../../etc/passwd"})
            message = str(payload(escaped))
            check("a path escape is refused", bool(escaped.isError), message[:80])
            check("and the refusal leaks no absolute path", "/etc" not in message, message[:80])

            moved = payload(
                await session.call_tool(
                    "vault_move_note",
                    {
                        "path": "Projects/Meeting notes.md",
                        "destination": "Archive/Renamed notes.md",
                        "confirm": True,
                    },
                )
            )
            check(
                "move rewrites the links that pointed at it",
                moved.get("relinked") == ["Areas/Work/Acme.md"],
                str(moved.get("relinked")),
            )
            check(
                "and the link now names the new note",
                "[[Renamed notes]]" in (vault / "Areas" / "Work" / "Acme.md").read_text(),
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP handshake smoke test for Knap.")
    parser.add_argument("--vault", help="Use this vault instead of a throwaway one.")
    parser.add_argument("--keep", action="store_true", help="Do not delete the throwaway vault.")
    args = parser.parse_args()

    if args.vault:
        vault = Path(args.vault).expanduser().resolve()
        temporary = None
    else:
        temporary = Path(tempfile.mkdtemp(prefix="knap-smoke-"))
        vault = temporary / "vault"
        vault.mkdir()
        seed(vault)

    print(f"Vault: {vault}\n")
    try:
        asyncio.run(run(vault))
    finally:
        if temporary and not args.keep:
            shutil.rmtree(temporary, ignore_errors=True)

    print()
    if _failures:
        print(f"{len(_failures)} check(s) failed: {', '.join(_failures)}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
