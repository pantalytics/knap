"""Enforce the per-file line budget.

CLAUDE.md says roughly 500 lines per Python file, and a convention that only lives
in prose is a convention that drifts. odoo-mcp-pro enforces the same rule with the
same kind of script, for the same reason: the budget is what makes somebody
extract a module instead of growing one, and nobody notices a file crossing the
line while they are in the middle of the change that crossed it.

No baseline file and no exemptions, deliberately. odoo-mcp-pro needs a ratchet
because it has legacy files already over budget; this package starts under it, so
the honest configuration is that going over is simply an error.

    python scripts/check_max_lines.py [--limit 500] [PATH ...]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_LIMIT = 500
DEFAULT_PATHS = ("knap_mcp", "tests", "scripts")


def offenders(paths: tuple[str, ...], limit: int) -> list[tuple[Path, int]]:
    found: list[tuple[Path, int]] = []
    for root in paths:
        base = Path(root)
        if not base.exists():
            continue
        for file in sorted(base.rglob("*.py")):
            if "__pycache__" in file.parts:
                continue
            count = len(file.read_text(encoding="utf-8").splitlines())
            if count > limit:
                found.append((file, count))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail when a Python file is too long.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("paths", nargs="*", default=list(DEFAULT_PATHS))
    args = parser.parse_args()

    over = offenders(tuple(args.paths or DEFAULT_PATHS), args.limit)
    if not over:
        print(f"All files within {args.limit} lines.")
        return 0

    print(f"Files over the {args.limit}-line budget:", file=sys.stderr)
    for file, count in over:
        print(f"  {file}: {count}", file=sys.stderr)
    print(
        "\nExtract a module rather than raising the limit. The budget is what makes\n"
        "that happen, so moving it defeats the point.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
