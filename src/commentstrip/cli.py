"""CLI entry point: ``commentstrip [-i] file.py [file2.py ...]``."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from commentstrip.core import strip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="commentstrip",
        description="Losslessly strip Python comments and docstring-like statements.",
    )
    parser.add_argument("paths", nargs="+", metavar="FILE", help="Python file(s) to strip.")
    parser.add_argument(
        "-i",
        "--in-place",
        action="store_true",
        help="Write the stripped source back to each file instead of printing to stdout.",
    )
    args = parser.parse_args(argv)

    for raw_path in args.paths:
        path = Path(raw_path)
        stripped = strip(path.read_bytes())
        if args.in_place:
            path.write_bytes(stripped)
        else:
            sys.stdout.buffer.write(stripped)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
