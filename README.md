# commentstrip

Losslessly strip comments and docstring-like statements from Python source, using
[libcst](https://libcst.readthedocs.io/) (Meta's lossless concrete-syntax-tree library) as the
parse/render engine. Every byte that isn't a comment or a comment-like statement is left
untouched — no reformatting, no reflowing, no `black`-style opinions.

It strips exactly two things:

1. **`#`-comments** — every `#`-introduced comment anywhere in the file: full-line, trailing/inline,
   inside multi-line brackets, the shebang line, and the encoding-cookie line. There is no
   special-casing; every `#` comment is removed the same way.
2. **Orphan string-literal statement expressions** — a standalone string statement with no side
   effects. This covers real docstrings (module/class/function) and "orphan" block-comment-style
   strings written anywhere else, since they're the same syntax shape: an `Expr` statement whose
   value is a plain string. F-strings-as-statements are kept (they evaluate expressions) and
   byte-strings-as-statements are kept (not conventionally documentation).

If stripping empties a block's body, a `pass` statement is substituted so the result stays valid
Python.

## CLI usage

```
uv run commentstrip file.py [file2.py ...]                  # list matches, grep -n style
uv run commentstrip --remove file.py [file2.py ...]          # strip in place
uv run commentstrip --remove --dry-run file.py [file2.py ...]  # preview a --remove, write nothing
```

Three modes:

- **No flags** — for each file, list every comment/comment-like match it contains, one line per
  physical line the match spans, `grep -n` style: `path:line: matched text`. Nothing is written to
  disk.
- **`--remove`** — strip each file and overwrite it in place, then print one summary line per file:
  `path: removed N comment(s)`. Does not print the match listing.
- **`--remove --dry-run`** — preview a `--remove` without writing anything: prints the same
  match listing as the no-flags mode, followed by `path: would remove N comment(s) (dry run,
  nothing written)`.

`--dry-run` without `--remove` is a usage error (there is nothing to preview) and exits 2 with a
usage message.

Exit codes follow `grep` convention: `0` if at least one match was found across all files
processed, `1` if zero matches were found anywhere (and no errors occurred), `2` if any file
couldn't be read — in that case the error is printed to stderr and the remaining files are still
processed, but the run still exits `2` overall.

## Caveats

- **No magic-comment special-casing.** Comments that mean something to other tools — `# noqa`,
  `# type: ignore`, `# pragma: no cover`, `# pylint: disable=...`, and so on — are stripped like any
  other `#` comment. `commentstrip` has no concept of "significant" comments; running it will
  silently remove suppression/pragma comments along with everything else.
- **Non-UTF-8 encoding cookies lose their self-declaration — and this can break the output file.**
  libcst infers the source encoding from a `# -*- coding: ... -*-` cookie on line 1 or 2, decodes
  with it, and re-renders bytes in that *same* original encoding — the cookie comment itself is
  stripped like any other `#` comment, since there's no special-casing. So for a non-UTF-8-declared
  file, the output bytes are still correctly encoded in the *original* encoding, but nothing in the
  file says so anymore. A tool that decodes Python source per PEP 263 (Python's own interpreter
  included) defaults to UTF-8 when no cookie is present, so if the original encoding wasn't UTF-8
  and the source contains non-ASCII bytes, the stripped file will fail to decode/import — even
  though `commentstrip` produced technically-valid bytes in the original encoding. If you rely on a
  non-UTF-8 encoding cookie, either don't strip that file or re-add the cookie afterward.
