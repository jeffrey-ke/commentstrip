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
import contextlib
import io
import shutil
import tempfile
from pathlib import Path

from commentstrip import cli
from commentstrip.core import find_comments, strip, strip_and_report

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


def test_strip_and_report_agrees_with_strip_across_all_fixtures():
    # strip_and_report() drives strip()'s output via strip_and_report(source)[0] — this is a
    # regression guard on that "single traversal, matches and removal never drift apart" contract.
    for name in _fixture_names():
        before = (FIXTURES / f"{name}_before.py").read_bytes()
        stripped, _matches = strip_and_report(before)
        assert stripped == strip(before), f"{name}: strip_and_report()[0] != strip()"


def test_find_comments_trailing_comment():
    before = (FIXTURES / "trailing_comment_before.py").read_bytes()
    matches = find_comments(before)
    assert len(matches) == 1
    match = matches[0]
    assert match.text == "# trailing comment"
    assert match.start_line == 1
    assert match.end_line == 1


def test_find_comments_function_docstring_is_one_match_not_two():
    before = (FIXTURES / "function_docstring_before.py").read_bytes()
    matches = find_comments(before)
    assert len(matches) == 1, "the whole docstring statement is one match, not per-token"
    match = matches[0]
    assert match.start_line == match.end_line == 2
    assert match.text == '    """Just a docstring, nothing else."""'


def test_find_comments_semicolon_code_then_comment_drops_only_the_string():
    # `x = 1; "comment"` — only the string is comment-like; `x = 1` must never appear in the match.
    before = (FIXTURES / "semicolon_code_then_comment_before.py").read_bytes()
    matches = find_comments(before)
    assert len(matches) == 1
    match = matches[0]
    assert match.start_line == match.end_line == 1
    assert "x = 1" not in match.text
    assert '"comment"' in match.text


def test_find_comments_semicolon_comment_then_code_drops_only_the_string():
    # `"comment"; x = 1` — same, with the string first on the line this time.
    before = (FIXTURES / "semicolon_comment_then_code_before.py").read_bytes()
    matches = find_comments(before)
    assert len(matches) == 1
    match = matches[0]
    assert match.start_line == match.end_line == 1
    assert "x = 1" not in match.text
    assert '"comment"' in match.text


def test_find_comments_multiline_docstring_spans_and_round_trips():
    before_bytes = (FIXTURES / "disorganized_docstring_before.py").read_bytes()
    before_lines = before_bytes.decode("utf-8").splitlines()
    matches = find_comments(before_bytes)
    assert len(matches) == 1
    match = matches[0]
    assert match.start_line == 3
    assert match.end_line == 4
    text_lines = match.text.split("\n")
    assert len(text_lines) == match.end_line - match.start_line + 1
    # Every physical line of the match round-trips byte-for-byte against the original source.
    for i, line in enumerate(text_lines):
        assert line == before_lines[match.start_line - 1 + i]


def _temp_copy_of_fixture(fixture_name: str) -> Path:
    tmpdir = Path(tempfile.mkdtemp(prefix="commentstrip_test_"))
    dest = tmpdir / f"{fixture_name}.py"
    shutil.copy(FIXTURES / f"{fixture_name}_before.py", dest)
    return dest


def test_cli_default_mode_lists_matches_and_leaves_file_untouched():
    path = _temp_copy_of_fixture("trailing_comment")
    try:
        original = path.read_bytes()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = cli.main([str(path)])
        assert exit_code == 0
        assert path.read_bytes() == original
        assert stdout.getvalue().splitlines() == [f"{path}:1: # trailing comment"]
    finally:
        shutil.rmtree(path.parent)


def test_cli_remove_mode_writes_stripped_content_and_reports_count():
    path = _temp_copy_of_fixture("trailing_comment")
    try:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = cli.main(["--remove", str(path)])
        assert exit_code == 0
        assert path.read_bytes() == (FIXTURES / "trailing_comment_after.py").read_bytes()
        assert stdout.getvalue().splitlines() == [f"{path}: removed 1 comment(s)"]
    finally:
        shutil.rmtree(path.parent)


def test_cli_remove_mode_on_no_comments_writes_unchanged_and_exits_1():
    path = _temp_copy_of_fixture("no_comments")
    try:
        original = path.read_bytes()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = cli.main(["--remove", str(path)])
        assert exit_code == 1, "zero matches found anywhere -> exit 1, even though file was written"
        assert path.read_bytes() == original
        assert stdout.getvalue().splitlines() == [f"{path}: removed 0 comment(s)"]
    finally:
        shutil.rmtree(path.parent)


def test_cli_remove_dry_run_leaves_file_untouched_and_prints_matches_plus_summary():
    path = _temp_copy_of_fixture("trailing_comment")
    try:
        original = path.read_bytes()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = cli.main(["--remove", "--dry-run", str(path)])
        assert exit_code == 0
        assert path.read_bytes() == original
        assert stdout.getvalue().splitlines() == [
            f"{path}:1: # trailing comment",
            f"{path}: would remove 1 comment(s) (dry run, nothing written)",
        ]
    finally:
        shutil.rmtree(path.parent)


def test_cli_dry_run_without_remove_is_a_usage_error():
    path = _temp_copy_of_fixture("trailing_comment")
    try:
        stderr = io.StringIO()
        raised = None
        try:
            with contextlib.redirect_stderr(stderr):
                cli.main(["--dry-run", str(path)])
        except SystemExit as e:
            raised = e
        assert raised is not None, "expected SystemExit"
        assert raised.code == 2
        assert "--dry-run requires --remove" in stderr.getvalue()
    finally:
        shutil.rmtree(path.parent)


if __name__ == "__main__":
    test_all_fixtures_strip_byte_exact()
    test_idempotent_on_already_stripped_output()
    test_strip_and_report_agrees_with_strip_across_all_fixtures()
    test_find_comments_trailing_comment()
    test_find_comments_function_docstring_is_one_match_not_two()
    test_find_comments_semicolon_code_then_comment_drops_only_the_string()
    test_find_comments_semicolon_comment_then_code_drops_only_the_string()
    test_find_comments_multiline_docstring_spans_and_round_trips()
    test_cli_default_mode_lists_matches_and_leaves_file_untouched()
    test_cli_remove_mode_writes_stripped_content_and_reports_count()
    test_cli_remove_mode_on_no_comments_writes_unchanged_and_exits_1()
    test_cli_remove_dry_run_leaves_file_untouched_and_prints_matches_plus_summary()
    test_cli_dry_run_without_remove_is_a_usage_error()
    print("ok")
