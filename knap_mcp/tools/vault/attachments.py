"""vault_get_attachment, vault_put_attachment -- the non-markdown half of a vault."""

from __future__ import annotations

import base64
import binascii
from typing import Optional

from ...schemas import AttachmentResult, NoteRefResult
from .._common import require_confirm, require_text, run_blocking
from ._base import VaultToolBase
from ._shared import ADDITIVE_WRITE, READ_ONLY


class AttachmentToolsMixin(VaultToolBase):
    """Images, PDFs and everything else a note embeds."""

    def _register_attachment_tools(self):
        @self.app.tool(title="Get Attachment", annotations=READ_ONLY)
        async def vault_get_attachment(
            path: str,
            vault: Optional[str] = None,
        ) -> AttachmentResult:
            """Read a non-markdown file from the vault, base64 encoded.

            Attachment paths come out of a note's embeds: `![[diagram.png]]` in a
            note read with vault_read_note appears in `links` with
            `embed: true`, and its `resolved_path` is what to pass here.

            There is a size ceiling, so a large video in the vault comes back as
            a clear refusal rather than an enormous response.

            Args:
                path: Vault-relative path to the file, extension included.
                vault: Which vault. Omit when only one is configured.
            """
            provider, sub = await self._get_provider(vault)
            require_text(path, "path")
            payload = await run_blocking(provider, provider.read_binary, path)
            self._track_usage(sub, "vault_get_attachment")
            return AttachmentResult(
                path=payload.path,
                content_type=payload.content_type,
                size=payload.size,
                content_base64=base64.b64encode(payload.content).decode("ascii"),
                vault=provider.vault_id,
            )

        @self.app.tool(title="Put Attachment", annotations=ADDITIVE_WRITE)
        async def vault_put_attachment(
            path: str,
            content_base64: str,
            overwrite: bool = False,
            confirm: bool = False,
            vault: Optional[str] = None,
        ) -> NoteRefResult:
            """Write a non-markdown file into the vault.

            Creating a new file needs no confirmation. Overwriting an existing
            one does, because unlike a note there is no `.trash` copy and no
            text to reconstruct it from: `overwrite=true` also requires
            `confirm=true`.

            Put it where the vault already keeps its attachments. vault_list_folders
            usually makes that obvious, and Obsidian's own setting for it lives in
            `.obsidian/app.json` if you need to be sure.

            Args:
                path: Vault-relative path including the extension.
                content_base64: The file's bytes, base64 encoded.
                overwrite: Replace an existing file. Requires confirm=true.
                confirm: Required when overwrite is true.
                vault: Which vault. Omit when only one is configured.
            """
            provider, sub = await self._get_provider(vault, writes=True)
            require_text(path, "path")
            require_text(content_base64, "content_base64")
            if overwrite:
                require_confirm(
                    confirm,
                    f"Overwriting the file at {path}",
                    "An attachment has no .trash copy, so the current file cannot be "
                    "recovered afterwards.",
                )
            try:
                content = base64.b64decode(content_base64, validate=True)
            except (binascii.Error, ValueError):
                from ...error_handling import ValidationError

                raise ValidationError("content_base64 is not valid base64") from None
            ref = await run_blocking(
                provider, provider.write_binary, path, content, overwrite=overwrite
            )
            self._track_usage(sub, "vault_put_attachment")
            return NoteRefResult(
                path=ref.path,
                rev=ref.rev,
                size=ref.size,
                modified=ref.modified,
                vault=provider.vault_id,
                created=not overwrite,
            )

        # vault_put_attachment is annotated additive rather than destructive
        # because that is what it is by default. The overwrite path is the
        # exception and gates itself on confirm=true, which is the same line the
        # note tools draw: the annotation describes the tool, the confirm
        # describes the call.
        _ = (vault_get_attachment, vault_put_attachment)


__all__ = ["AttachmentToolsMixin"]
