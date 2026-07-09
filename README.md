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
uv run commentstrip file.py [file2.py ...]       # prints stripped bytes to stdout
uv run commentstrip -i file.py [file2.py ...]     # strips in place
```

By default the transformed bytes are written to `sys.stdout.buffer` and the input files are left
untouched. Pass `-i`/`--in-place` to overwrite each file instead (same convention as `black`/
`autopep8`).

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
