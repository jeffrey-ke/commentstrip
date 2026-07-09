"""Golden-file test suite for commentstrip.core.strip.

Globs every tests/fixtures/*_before.py, runs strip() on it, and asserts a byte-exact match
against the paired *_after.py. Both the input and the output are ast.parse()d — this catches
malformed fixtures *and* verifies the transform never produces invalid Python.

One documented exception: non_utf8_encoding_cookie loses its self-declared (non-UTF-8) encoding
cookie as part of stripping (the cookie is itself a `#` comment, stripped like any other) — see
the README caveat. Its *output* bytes are valid Python only if decoded with the ORIGINAL encoding;
decoded with the PEP 263 default (UTF-8, since no cookie survives) they raise a UnicodeDecodeError.
That's expected, not a bug, so this one fixture is exempted from the output-side ast.parse() check
— and, for the same reason, from the idempotency check (running strip() a second time means
libcst re-parsing those now-cookie-less bytes as UTF-8, which raises before it ever gets to
re-stripping anything).
"""
import ast
from pathlib import Path

from commentstrip.core import strip

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# Fixtures whose *output* is intentionally not self-decodable as UTF-8 — see module docstring.
_SKIP_OUTPUT_PARSE = {"non_utf8_encoding_cookie"}
_SKIP_IDEMPOTENCY = {"non_utf8_encoding_cookie"}


def _fixture_names() -> list[str]:
    return sorted(p.name[: -len("_before.py")] for p in FIXTURES.glob("*_before.py"))


def test_all_fixtures_strip_byte_exact():
    names = _fixture_names()
    assert names, "no fixtures found"
    for name in names:
        before = (FIXTURES / f"{name}_before.py").read_bytes()
        after = (FIXTURES / f"{name}_after.py").read_bytes()

        ast.parse(before)  # input must always be valid Python

        got = strip(before)
        assert got == after, f"{name}: strip() output does not match golden"

        if name not in _SKIP_OUTPUT_PARSE:
            ast.parse(got)  # output must always be valid Python too


def test_idempotent_on_already_stripped_output():
    names = _fixture_names()
    for name in names:
        if name in _SKIP_IDEMPOTENCY:
            continue
        after = (FIXTURES / f"{name}_after.py").read_bytes()
        assert strip(after) == after, f"{name}: strip() is not idempotent on its own output"


if __name__ == "__main__":
    test_all_fixtures_strip_byte_exact()
    test_idempotent_on_already_stripped_output()
    print("ok")
