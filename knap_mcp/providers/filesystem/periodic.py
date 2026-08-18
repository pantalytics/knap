"""Daily, weekly and monthly notes, resolved from the vault's own settings.

The whole point of this module is that a daily note must land exactly where the
customer's Obsidian would have put it. A note in the right folder with a
slightly different filename format is not their daily note, it is a second one,
and they will find it a week later with three days of captured thoughts in it.

So nothing is guessed. Settings come from what is actually in the vault, in the
order Obsidian reads them:

1. `.obsidian/plugins/periodic-notes/data.json` -- the Periodic Notes plugin,
   which is the only source for weekly and monthly.
2. `.obsidian/daily-notes.json` -- the core Daily Notes plugin.
3. Nothing configured, in which case a read reports that and a create refuses,
   rather than inventing `YYYY-MM-DD.md` in the vault root.

Case 3 comes in two shapes and they need different sentences. A vault with an
`.obsidian` folder and no periodic-notes settings in it really does have the
plugin switched off, and saying so is useful. A vault with no `.obsidian` folder
at all has told us nothing, and a backend is free to hand over notes and
attachments without it, so "the plugin is off" would be a guess. Worse, it is a
guess that sends somebody to a setting that is probably already right, and they
come back to the same sentence.

Moment.js format tokens are what Obsidian stores, so a small translator lives
here. Only the tokens people actually put in a daily-note format are supported;
anything else is reported rather than approximated, because a filename that is
nearly right is the failure this module exists to prevent.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from ...logging_config import get_logger
from ..protocol import (
    PeriodicKind,
    PeriodicNotesNotConfigured,
    ProviderError,
    VaultSettingsUnavailable,
)
from . import markdown as md
from . import paths as vault_paths

if TYPE_CHECKING:  # pragma: no cover
    from .provider import FilesystemVaultProvider

logger = get_logger(__name__)

DEFAULT_FORMATS: Dict[str, str] = {
    "daily": "YYYY-MM-DD",
    "weekly": "gggg-[W]ww",
    "monthly": "YYYY-MM",
}


def resolve(
    provider: "FilesystemVaultProvider",
    kind: PeriodicKind = "daily",
    when: Optional[str] = None,
    *,
    create: bool = False,
) -> Tuple[str, bool]:
    """Resolve the periodic note for a date. Returns (path, created)."""
    if kind not in DEFAULT_FORMATS:
        raise ProviderError(f"Unknown periodic note kind {kind!r}")

    settings = read_settings(provider.root, kind)
    if settings is None:
        raise _nothing_to_read(provider.root, kind)

    target = _parse_date(when)
    filename = format_moment(settings["format"], target, kind)
    folder = settings["folder"].strip("/")
    rel = f"{folder}/{filename}.md" if folder else f"{filename}.md"
    rel = vault_paths.normalize(rel)

    absolute = vault_paths.resolve_in_vault(provider.root, rel)
    if absolute.exists():
        return rel, False
    if not create:
        return rel, False

    body = _template_body(provider.root, settings.get("template", ""), target)
    vault_paths.ensure_parent(absolute)
    md.atomic_write(absolute, body)
    provider.index.invalidate(rel)
    logger.info("Created a %s note from the vault's own settings", kind)
    return rel, True


def read_settings(root: Path, kind: str) -> Optional[Dict[str, Any]]:
    """The vault's folder, format and template for this kind, or None.

    Periodic Notes wins over Daily Notes when both are present, which is the
    order Obsidian itself applies: a vault with the plugin installed is using
    the plugin's settings.
    """
    plugin = _read_json(root / ".obsidian" / "plugins" / "periodic-notes" / "data.json")
    if isinstance(plugin, dict):
        section = plugin.get(kind)
        if isinstance(section, dict) and section.get("enabled"):
            return {
                "folder": str(section.get("folder") or ""),
                "format": str(section.get("format") or DEFAULT_FORMATS[kind]),
                "template": str(section.get("template") or ""),
            }

    if kind == "daily":
        core = _read_json(root / ".obsidian" / "daily-notes.json")
        if isinstance(core, dict):
            # The file existing IS the configuration: Obsidian only writes it
            # once the plugin has been touched, and an empty object means
            # "enabled, all defaults".
            return {
                "folder": str(core.get("folder") or ""),
                "format": str(core.get("format") or DEFAULT_FORMATS["daily"]),
                "template": str(core.get("template") or ""),
            }
    return None


def _nothing_to_read(root: Path, kind: str) -> PeriodicNotesNotConfigured:
    """The error for a kind we could not resolve, and which of the two it is.

    The test is the settings folder itself, not the file for this kind: a vault
    that has `.obsidian` and no daily-notes.json has the plugin switched off,
    and one without `.obsidian` has not said.
    """
    if not (root / ".obsidian").is_dir():
        return VaultSettingsUnavailable(
            "There are no Obsidian settings in this vault, so nothing here says where the "
            f"{kind} notes go. The plugin may well be on: some vaults arrive as notes and "
            "attachments only, and the settings do not come with them. Ask the user which "
            "folder they are in and what the filenames look like, then use that path."
        )
    return PeriodicNotesNotConfigured(
        f"This vault has no {kind} notes configured. Switch on the core Daily Notes "
        "plugin (or Periodic Notes for weekly and monthly) in Obsidian first, so the "
        "note lands where the rest of them are."
    )


def _read_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A settings file we cannot parse is the same as none: we are not going
        # to half-read somebody's plugin config and act on the half.
        return None


def _parse_date(when: Optional[str]) -> date:
    if not when or not when.strip():
        return datetime.now(timezone.utc).date()
    raw = when.strip().lower()
    today = datetime.now(timezone.utc).date()
    # "today" and "yesterday" arrive from an AI far more often than an ISO date
    # does, and refusing them would push the date arithmetic onto a client that
    # does not know the server's timezone.
    if raw == "today":
        return today
    if raw == "yesterday":
        return today - timedelta(days=1)
    if raw == "tomorrow":
        return today + timedelta(days=1)
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        raise ProviderError(
            f"date must be an ISO date (YYYY-MM-DD), 'today', 'yesterday' or "
            f"'tomorrow', got {when!r}"
        ) from None


# Longest tokens first, so YYYY is not eaten as YY+YY. Everything Obsidian's
# default formats use, plus the tokens that show up in vaults in practice.
_MOMENT_TOKENS = [
    ("YYYY", "%Y"),
    ("MMMM", "%B"),
    ("MMM", "%b"),
    ("MM", "%m"),
    ("DDDD", "%j"),
    ("DD", "%d"),
    ("dddd", "%A"),
    ("ddd", "%a"),
    ("gggg", "ISOYEAR"),
    ("GGGG", "ISOYEAR"),
    ("YY", "%y"),
    ("ww", "ISOWEEK"),
    ("WW", "ISOWEEK"),
    ("Do", "DAYORDINAL"),
    ("D", "%-d"),
    ("M", "%-m"),
    ("w", "ISOWEEK1"),
    ("W", "ISOWEEK1"),
]


def format_moment(fmt: str, value: date, kind: str = "daily") -> str:
    """Render a Moment.js date format the way Obsidian would.

    Literals in square brackets pass through untouched, which is what makes
    ``gggg-[W]ww`` come out as ``2026-W32`` rather than with the W treated as a
    token.
    """
    iso_year, iso_week, _ = value.isocalendar()
    out: list[str] = []
    i = 0
    while i < len(fmt):
        if fmt[i] == "[":
            close = fmt.find("]", i)
            if close == -1:
                out.append(fmt[i + 1 :])
                break
            out.append(fmt[i + 1 : close])
            i = close + 1
            continue
        for token, directive in _MOMENT_TOKENS:
            if fmt.startswith(token, i):
                if directive == "ISOYEAR":
                    out.append(f"{iso_year:04d}")
                elif directive == "ISOWEEK":
                    out.append(f"{iso_week:02d}")
                elif directive == "ISOWEEK1":
                    out.append(str(iso_week))
                elif directive == "DAYORDINAL":
                    out.append(_ordinal(value.day))
                elif directive.startswith("%-"):
                    out.append(str(int(value.strftime(f"%{directive[2]}"))))
                else:
                    out.append(value.strftime(directive))
                i += len(token)
                break
        else:
            out.append(fmt[i])
            i += 1
    rendered = "".join(out)
    if not rendered.strip():
        raise ProviderError(
            f"The vault's {kind} note format {fmt!r} produced an empty filename. "
            "Check the format in Obsidian's settings."
        )
    # A format may legitimately contain "/" to nest by month. Anything else that
    # cannot be a filename is the vault's problem to fix, and saying so beats
    # writing a note under a mangled name.
    if re.search(r'[\\:*?"<>|]', rendered):
        raise ProviderError(
            f"The vault's {kind} note format {fmt!r} produced {rendered!r}, which is not a "
            "usable filename."
        )
    return rendered


def _ordinal(day: int) -> str:
    if 11 <= day % 100 <= 13:
        return f"{day}th"
    return f"{day}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(day % 10, 'th') }"


def _template_body(root: Path, template: str, value: date) -> str:
    """The new note's contents: the vault's template, or empty.

    Only the two date placeholders every template uses are filled in. Templater
    syntax is left alone rather than half-evaluated: a note full of executed
    JavaScript that the plugin would have rendered differently is worse than one
    the plugin can still render itself.
    """
    if not template.strip():
        return ""
    rel = template.strip()
    if not vault_paths.is_note(rel):
        rel = f"{rel}.md"
    try:
        absolute = vault_paths.resolve_in_vault(root, rel, must_exist=True)
        text, _, _, _ = md.read_text(absolute)
    except (OSError, ProviderError):
        logger.info("Periodic template %r is missing; created an empty note", template)
        return ""

    def substitute(match: re.Match) -> str:
        fmt = (match.group(1) or "").strip(': "')
        try:
            return format_moment(fmt or "YYYY-MM-DD", value)
        except ProviderError:
            return match.group(0)

    return re.sub(r"\{\{\s*date(:[^}]*)?\s*\}\}", substitute, text, flags=re.I)
