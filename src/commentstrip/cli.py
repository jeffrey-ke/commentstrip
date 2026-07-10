"""CLI entry point: ``commentstrip [--remove [--dry-run]] file.py [file2.py ...]``.

Three valid modes:
  (no flags)          list every comment/comment-like match, grep -n style. Nothing written.
  --remove            strip each file and overwrite it in place; print a one-line summary each.
  --remove --dry-run  print the same match listing as the no-flags mode, plus a "would remove"
                       summary; nothing written.
``--dry-run`` without ``--remove`` is a usage error (there's nothing to preview).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from commentstrip.core import CommentMatch, find_comments, strip_and_report


def _print_matches(path: str, matches: list[CommentMatch]) -> None:
    """Print each match grep -n style: one line per PHYSICAL LINE it spans, path-prefixed."""
    for match in matches:
        for i, line in enumerate(match.text.split("\n")):
            print(f"{path}:{match.start_line + i}: {line}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="commentstrip",
        description="Losslessly list or strip Python comments and docstring-like statements.",
    )
    parser.add_argument("paths", nargs="+", metavar="FILE", help="Python file(s) to process.")
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Strip comments and write the result back to each file in place.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="With --remove, preview matches/counts without writing anything.",
    )
    args = parser.parse_args(argv)

    if args.dry_run and not args.remove:
        parser.error("--dry-run requires --remove")

    total_matches = 0
    any_errors = False

    for raw_path in args.paths:
        path = Path(raw_path)
        try:
            source = path.read_bytes()
        except OSError as e:
            print(f"{raw_path}: {e}", file=sys.stderr)
            any_errors = True
            continue

        if not args.remove:
            matches = find_comments(source)
            _print_matches(raw_path, matches)
        elif args.dry_run:
            stripped, matches = strip_and_report(source)
            _print_matches(raw_path, matches)
            print(f"{raw_path}: would remove {len(matches)} comment(s) (dry run, nothing written)")
        else:
            stripped, matches = strip_and_report(source)
            path.write_bytes(stripped)
            print(f"{raw_path}: removed {len(matches)} comment(s)")

        total_matches += len(matches)

    if any_errors:
        return 2
    return 0 if total_matches > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
