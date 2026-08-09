"""Writing frontmatter without rewriting it.

Split out of ``markdown.py`` because it is a self-contained job with a rule of its
own, and because that file was over the 500-line budget.

The rule: ``vault_set_properties`` promises the body and every property it was not
asked about come out byte-identical. PyYAML round-trips *values* correctly and
formatting not at all -- it reorders keys, requotes strings and collapses block
lists into flow lists -- so re-dumping the block to change one property breaks
that promise quietly. On a synced, git-backed vault the resulting noise is
permanent and shows up in every diff the customer reads.

So ``edit`` finds the lines a key occupies and replaces those, leaving the rest of
the file alone. The narrowness is deliberate: only scalars and lists of scalars
are hand-written, because those are the shapes a tool actually sets and the shapes
where a hand-rolled line is provably what YAML will read back. Anything nested
falls through to a full re-dump, which is the honest trade -- correct content, and
a formatting diff the caller can see.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import yaml

FRONTMATTER_FENCE = "---"


def edit(
    raw: str,
    changes: Dict[str, Any],
    *,
    frontmatter: Dict[str, Any],
    frontmatter_raw: str,
    body: str,
) -> str:
    """Merge properties into a note's frontmatter, changing as few bytes as possible.

    A value of ``None`` removes the key. Scalars and lists of scalars are edited
    in place, line by line, so a note whose frontmatter uses block lists and
    single quotes keeps using them. Anything shaped differently, and any note
    whose YAML we could not parse, falls back to re-dumping the block, which is
    the honest trade: correct content, and a formatting diff the caller can see.

    The point of all this is that ``vault_set_properties`` promises the body and
    the rest of the frontmatter come out untouched. A re-dump breaks that promise
    quietly, and on a git-backed vault the noise never goes away.
    """
    if not changes:
        return raw

    if not frontmatter_raw:
        block = _dump_block({k: v for k, v in changes.items() if v is not None})
        return f"{block}{body}" if body else block

    lines = frontmatter_raw.split("\n")
    inner = lines[1:-1]  # between the fences
    remaining = dict(changes)

    edited: List[str] = []
    i = 0
    while i < len(inner):
        line = inner[i]
        key = _key_of(line)
        block_len = 1
        if key is not None:
            # A key's value may continue onto indented lines (a block list or a
            # nested map). Those belong to it and go with it.
            while i + block_len < len(inner) and _is_continuation(inner[i + block_len]):
                block_len += 1
        if key is not None and key in remaining:
            value = remaining.pop(key)
            if value is not None:
                rendered = _render(key, value)
                if rendered is None:
                    return _rebuild_block(frontmatter, body, changes)
                edited.extend(rendered)
            # value is None: drop the key and its continuation lines
        else:
            edited.extend(inner[i : i + block_len])
        i += block_len

    for key, value in remaining.items():
        if value is None:
            continue  # removing a key that was not there is not an error
        rendered = _render(key, value)
        if rendered is None:
            return _rebuild_block(frontmatter, body, changes)
        edited.extend(rendered)

    new_block = "\n".join([FRONTMATTER_FENCE, *edited, FRONTMATTER_FENCE])
    tail = raw[len(frontmatter_raw) :]
    return new_block + tail


def _rebuild_block(frontmatter: Dict[str, Any], body: str, changes: Dict[str, Any]) -> str:
    """Last resort: re-dump the frontmatter with PyYAML, keeping the body."""
    merged = dict(frontmatter)
    for key, value in changes.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    block = _dump_block(merged)
    return f"{block}{body}" if body else block


def _dump_block(data: Dict[str, Any]) -> str:
    if not data:
        return ""
    dumped = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    return f"{FRONTMATTER_FENCE}\n{dumped}{FRONTMATTER_FENCE}\n"


def _key_of(line: str) -> Optional[str]:
    """The key a frontmatter line declares, or None if it is not a key line."""
    if not line or line[0] in " \t#-":
        return None
    match = re.match(r"^([^:\s][^:]*):(?:\s|$)", line)
    return match.group(1).strip() if match else None


def _is_continuation(line: str) -> bool:
    """Whether this line belongs to the key above it."""
    return bool(line) and (line[0] in " \t" or line.lstrip().startswith("- "))


def _render(key: str, value: Any) -> Optional[List[str]]:
    """Render one property as frontmatter lines, or None if we should not try.

    Deliberately narrow. A scalar and a list of scalars are the shapes a tool
    actually sets, and they are the shapes where a hand-rolled line is provably
    the same as what YAML would parse. Anything nested goes to PyYAML, because
    getting quoting wrong here writes a value the vault will read back
    differently.
    """
    if isinstance(value, bool):
        return [f"{key}: {'true' if value else 'false'}"]
    if isinstance(value, (int, float)):
        return [f"{key}: {value}"]
    if isinstance(value, str):
        rendered = _scalar(value)
        return None if rendered is None else [f"{key}: {rendered}"]
    if isinstance(value, list):
        if not value:
            return [f"{key}: []"]
        if not all(isinstance(item, (str, int, float, bool)) for item in value):
            return None
        items = [_scalar(str(item)) for item in value]
        if any(item is None for item in items):
            return None
        return [f"{key}:", *[f"  - {item}" for item in items]]
    return None


def _scalar(value: str) -> Optional[str]:
    """Write a string as a YAML scalar, or None if we cannot do it safely.

    The list below is the cheap answer and ``_reads_back`` is the deciding one.
    Keeping both is deliberate: the list settles the overwhelming majority
    without a parse and documents the cases worth knowing about, and the round
    trip catches whatever the list forgot. It forgot ``#`` for a long time -- a
    hash after a space opens a comment, so ``Budget #2026 review`` was written
    unquoted and read back as ``Budget``, with the rest gone from the file.

    Quoting is not automatically the safe answer either, which is why the quoted
    form is checked too. Escaping covers backslash, quote and newline, so a
    carriage return survives into the double-quoted scalar and YAML folds it to
    a space on the way back. When neither form round-trips we return None and
    the caller re-dumps the block with PyYAML: a formatting diff the customer
    can see, which is the trade this module already makes for nested values and
    is strictly better than writing something that reads back wrong.
    """
    if value == "":
        return '""'
    needs_quotes = (
        value.strip() != value
        or value[0] in "!&*-?|>%@`{}[],#\"'"
        or ": " in value
        or value.endswith(":")
        or "\n" in value
        or " #" in value
        or "\t#" in value
        or value.lower() in ("true", "false", "null", "yes", "no", "on", "off", "~")
        or _looks_numeric(value)
    )
    if not needs_quotes and _reads_back(value, value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    quoted = f'"{escaped}"'
    return quoted if _reads_back(quoted, value) else None


def _reads_back(written: str, value: str) -> bool:
    """Does ``written``, as the value of a key, parse back as ``value``?

    The one question that matters, asked of the parser instead of answered from
    memory. A note is the customer's own writing, so the interesting inputs are
    the ones nobody thought of, and the parser is the only thing that knows them
    all.

    Every exception counts as "no". PyYAML raises more than ``YAMLError`` here:
    its timestamp constructor calls ``datetime.date``, so a mistyped date like
    ``2026-02-30`` comes out as a ``ValueError`` and would otherwise travel all
    the way up through ``vault_set_properties`` into the caller's face.
    """
    try:
        return yaml.safe_load(f"x: {written}") == {"x": value}
    except Exception:  # noqa: BLE001 - any failure to parse means "do not write this"
        return False


def _looks_numeric(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False
