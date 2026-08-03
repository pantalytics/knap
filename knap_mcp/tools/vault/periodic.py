"""vault_daily_note -- the other half of capture."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ...schemas import PeriodicNoteResult
from .._common import run_blocking
from ._base import VaultToolBase
from ._shared import ADDITIVE_WRITE


class PeriodicToolsMixin(VaultToolBase):
    """Resolve today's note, or any date's, using the vault's own settings."""

    def _register_periodic_tools(self):
        @self.app.tool(title="Daily Note", annotations=ADDITIVE_WRITE)
        async def vault_daily_note(
            date: Optional[str] = None,
            kind: str = "daily",
            create: bool = False,
            vault: Optional[str] = None,
        ) -> PeriodicNoteResult:
            """Find the daily, weekly or monthly note for a date. Optionally create it.

            The path comes from the vault's own periodic-notes settings, so the
            note lands exactly where the user's Obsidian would have put it. A
            note in the right folder with a slightly different filename is not
            their daily note, it is a second one, and they will find it a week
            later with three days of notes in it.

            Then vault_append_note against the path this returns is how a
            captured thought reaches today's note.

            If the vault has no periodic notes configured, this says so. Do not
            invent a path: tell the user to switch on Daily Notes in Obsidian, so
            the whole vault agrees where they go.

            Args:
                date: ISO date (YYYY-MM-DD), or "today", "yesterday",
                    "tomorrow". Defaults to today, UTC.
                kind: "daily" (default), "weekly" or "monthly". Weekly and
                    monthly need the Periodic Notes plugin.
                create: Create the note from the vault's template if it does not
                    exist. Not destructive: it only ever adds a note.
                vault: Which vault. Omit when only one is configured.
            """
            provider, sub = await self._get_provider(vault, writes=create)
            if kind not in ("daily", "weekly", "monthly"):
                from ...error_handling import ValidationError

                raise ValidationError("kind must be 'daily', 'weekly' or 'monthly'")
            path, created = await run_blocking(
                provider, provider.periodic_note, kind, date, create=create
            )
            exists = created or bool(await run_blocking(provider, _exists, provider, path))
            self._track_usage(sub, "vault_daily_note")
            return PeriodicNoteResult(
                path=path,
                kind=kind,
                date=date or datetime.now(timezone.utc).date().isoformat(),
                exists=exists,
                created=created,
                vault=provider.vault_id,
            )

        _ = vault_daily_note


def _exists(provider, path: str) -> bool:
    """Whether the resolved note is actually there.

    Asked separately because a resolve with ``create=False`` returns the path a
    note *would* have, which is the useful answer, and the client still needs to
    know whether there is anything in it yet.
    """
    index = getattr(provider, "index", None)
    if index is None:
        return False
    try:
        return index.exists(path)
    except Exception:  # noqa: BLE001 - a probe, never a reason to fail the call
        return False


__all__ = ["PeriodicToolsMixin"]
