"""Losslessly strip Python comments and comment-like orphan string statements.

Built on ``libcst`` (a lossless concrete-syntax-tree library): every byte that is not part of a
``#``-comment or a comment-like standalone string-statement is preserved byte-for-byte, including
whitespace, line endings, BOM, and encoding.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import libcst as cst
from libcst.metadata import PositionProvider


@dataclass(frozen=True)
class CommentMatch:
    """One comment/comment-like statement found (and, when stripping, removed) from a file.

    ``text`` is the exact matched source text, verbatim, as it appears in the original file:
    including its own indentation for statement-level matches (docstrings, orphan strings), and
    including the leading ``#`` for hash comments. ``start_line``/``end_line`` are 1-indexed
    physical line numbers (equal for single-line matches; a multi-line docstring spans both).
    """

    start_line: int
    end_line: int
    text: str


def _is_comment_like(expr: cst.BaseExpression) -> bool:
    """True if ``expr`` is a plain (non-byte, non-f) string or concatenation thereof.

    These are statement-expression values with no side effects — real docstrings and "orphan"
    block-comment-style strings are the same CST shape (``Expr`` whose value is a plain string).
    """
    if isinstance(expr, cst.FormattedString):
        return False  # f"{x}" has side effects — never a comment
    if isinstance(expr, cst.SimpleString):
        return "b" not in expr.prefix.lower()  # excludes b"...", rb"...", br"...", B"...", ...
    if isinstance(expr, cst.ConcatenatedString):
        return _is_comment_like(expr.left) and _is_comment_like(expr.right)
    return False  # 42, x == 1, etc. — never touched


def _is_comment_like_small_stmt(stmt: cst.BaseSmallStatement) -> bool:
    return isinstance(stmt, cst.Expr) and _is_comment_like(stmt.value)


def _small_stmt_keep_mask(body: Sequence[cst.BaseSmallStatement]) -> tuple[bool, ...]:
    """True for each small-statement that should survive (i.e. is NOT comment-like)."""
    return tuple(not _is_comment_like_small_stmt(stmt) for stmt in body)


def _apply_small_stmt_mask(
    body: Sequence[cst.BaseSmallStatement], mask: Sequence[bool]
) -> tuple[cst.BaseSmallStatement, ...]:
    """Drop the small-statements masked out, positionally, from a semicolon-joined sequence.

    If the surviving last item's ``.semicolon`` is an explicit ``cst.Semicolon`` (not
    ``MaybeSentinel.DEFAULT``), reset it — otherwise a dangling ``x = 1; `` survives with stray
    trailing whitespace once whatever followed it on the line is gone.

    ``mask`` is computed separately (see ``_small_stmt_keep_mask``) so a caller can compute it
    against the pre-transform ``original_node.body`` (which carries ``PositionProvider``
    metadata) while applying it here to the post-transform ``updated_node.body``.
    """
    kept = tuple(stmt for stmt, keep in zip(body, mask) if keep)
    if kept and not isinstance(kept[-1].semicolon, cst.MaybeSentinel):
        kept = kept[:-1] + (kept[-1].with_changes(semicolon=cst.MaybeSentinel.DEFAULT),)
    return kept


def _is_whole_line_comment(stmt: cst.BaseStatement) -> bool:
    """True if every small-statement on this line is comment-like (drop the whole line)."""
    if not isinstance(stmt, cst.SimpleStatementLine):
        return False
    return all(_is_comment_like_small_stmt(s) for s in stmt.body)


def _filter_block_body(
    original_body: Sequence[cst.BaseStatement],
    updated_body: Sequence[cst.BaseStatement],
) -> tuple[tuple[cst.BaseStatement, ...], tuple[cst.EmptyLine, ...], tuple[cst.BaseStatement, ...]]:
    """Drop whole comment-only ``SimpleStatementLine`` entries from a block's statement list.

    Returns the filtered body, any ``leading_lines`` orphaned by a dropped *trailing* statement
    (the caller carries these onto the block's own footer), and the ORIGINAL (pre-transform,
    metadata-bearing) statement nodes that were dropped, so the caller can record a
    ``CommentMatch`` for each using ``original_node``-keyed ``PositionProvider`` metadata rather
    than the freshly-rebuilt ``updated_body`` copies (which carry no metadata of their own).

    ``original_body`` and ``updated_body`` are the same statements pre- and post-child-transform,
    positionally aligned. A dropped statement's *updated* ``leading_lines`` (already stripped of
    any comment-only ``EmptyLine``s by the child traversal) are carried onto the next surviving
    statement, so a blank line that was spacing *around* the removed statement doesn't silently
    collapse adjacent code together.
    """
    kept: list[cst.BaseStatement] = []
    dropped: list[cst.BaseStatement] = []
    carried: tuple[cst.EmptyLine, ...] = ()

    for orig_stmt, upd_stmt in zip(original_body, updated_body):
        if _is_whole_line_comment(orig_stmt):
            dropped.append(orig_stmt)
            carried = carried + tuple(upd_stmt.leading_lines)
            continue
        if carried:
            upd_stmt = upd_stmt.with_changes(leading_lines=carried + tuple(upd_stmt.leading_lines))
            carried = ()
        kept.append(upd_stmt)

    return tuple(kept), carried, tuple(dropped)


class _CommentStripper(cst.CSTTransformer):
    """Removes comments/comment-like statements AND records a ``CommentMatch`` for each.

    Recording happens inline, in the same hooks that decide what to remove, so the emitted
    ``self.matches`` list can never drift from what actually gets stripped (no separate "finder"
    pass with its own copy of the removal logic).

    Needs ``PositionProvider`` metadata (hence ``METADATA_DEPENDENCIES`` + being driven via
    ``cst.MetadataWrapper(tree).visit(self)`` rather than a bare ``tree.visit(self)``) and the
    original pre-transform ``tree`` — both to call ``tree.code_for_node(node)`` (recovers a
    node's own exact source text) and to read raw source lines (recovers a statement's leading
    indentation, which ``code_for_node`` alone does not include).
    """

    METADATA_DEPENDENCIES = (PositionProvider,)

    def __init__(self, tree: cst.Module) -> None:
        super().__init__()
        self._tree = tree
        self._source_lines = tree.code.splitlines()
        self.matches: list[CommentMatch] = []

    def _record_hash_comment(self, comment: cst.Comment) -> None:
        pos = self.get_metadata(PositionProvider, comment)
        self.matches.append(CommentMatch(pos.start.line, pos.end.line, comment.value))

    def _record_statement(self, node: cst.CSTNode) -> None:
        pos = self.get_metadata(PositionProvider, node)

        # code_for_node() renders the node's own tokens but not the indentation that precedes it
        # (indentation is applied by the enclosing block's codegen state, not stored on the node)
        # — recover it verbatim from the original source line instead of assuming N spaces. Only
        # do this when the node truly opens the physical line (everything before its start column
        # is whitespace): a small-statement dropped mid-line (e.g. the "comment" in `x = 1;
        # "comment"`) is preceded by sibling code on that line, not indentation, and prepending
        # that would fold the sibling code into the match text.
        prefix = self._source_lines[pos.start.line - 1][: pos.start.column]
        indent = prefix if prefix.strip() == "" else ""

        # SimpleStatementLine (case 5/6) carries its own `leading_lines` (blank/comment lines
        # immediately above it) as a node field, so code_for_node() would render those too —
        # but PositionProvider's start position already skips past them to the statement's own
        # first token. Drop them so the two agree; small-statements (case 3/4) have no such field.
        if hasattr(node, "leading_lines"):
            node = node.with_changes(leading_lines=())

        text = indent + self._tree.code_for_node(node)

        # A whole SimpleStatementLine (case 5/6) renders its own trailing line terminator as part
        # of its body — that newline is the boundary to the *next* line, not part of this match's
        # content (small-statements from case 3/4 have no trailing_whitespace field of their own,
        # so this is a no-op for them). Strip exactly one, CRLF-aware, so `end_line - start_line +
        # 1` always equals the number of physical lines `text` actually spans.
        if text.endswith("\r\n"):
            text = text[:-2]
        elif text.endswith("\n"):
            text = text[:-1]

        self.matches.append(CommentMatch(pos.start.line, pos.end.line, text))

    def leave_TrailingWhitespace(
        self, original_node: cst.TrailingWhitespace, updated_node: cst.TrailingWhitespace
    ) -> cst.TrailingWhitespace:
        # inline/trailing "# comment" after code on a line
        if original_node.comment is not None:
            self._record_hash_comment(original_node.comment)
            return updated_node.with_changes(comment=None, whitespace=cst.SimpleWhitespace(""))
        return updated_node

    def leave_EmptyLine(
        self, original_node: cst.EmptyLine, updated_node: cst.EmptyLine
    ):
        # comment-only line (or blank line with a comment) — drop the whole line, no dangling blank
        if original_node.comment is not None:
            self._record_hash_comment(original_node.comment)
            return cst.RemoveFromParent()
        return updated_node

    def leave_SimpleStatementLine(
        self, original_node: cst.SimpleStatementLine, updated_node: cst.SimpleStatementLine
    ) -> cst.SimpleStatementLine:
        # Mask is computed against original_node.body (carries PositionProvider metadata) —
        # updated_node.body is a freshly-rebuilt copy (even where nothing changed) with none.
        mask = _small_stmt_keep_mask(original_node.body)
        if not any(mask):
            # Fully comment-like line (e.g. a bare docstring): leave untouched here — the parent
            # block/module body filter (leave_IndentedBlock/leave_Module) drops the whole line,
            # carries its leading_lines, AND records its match exactly once. Recording here too
            # would double-count it.
            return updated_node
        for orig_stmt, keep in zip(original_node.body, mask):
            if not keep:
                self._record_statement(orig_stmt)
        new_body = _apply_small_stmt_mask(updated_node.body, mask)
        return updated_node.with_changes(body=new_body)

    def leave_SimpleStatementSuite(
        self, original_node: cst.SimpleStatementSuite, updated_node: cst.SimpleStatementSuite
    ) -> cst.SimpleStatementSuite:
        # The one-liner `if True: "x"` form: no block wrapper, no leading_lines/footer — the
        # suite *is* the whole body, so an emptied suite substitutes `pass` directly, and (unlike
        # leave_SimpleStatementLine) there's no parent to defer recording to: record here always.
        mask = _small_stmt_keep_mask(original_node.body)
        for orig_stmt, keep in zip(original_node.body, mask):
            if not keep:
                self._record_statement(orig_stmt)
        new_body = _apply_small_stmt_mask(updated_node.body, mask)
        if not new_body:
            new_body = (cst.Pass(),)
        return updated_node.with_changes(body=new_body)

    def leave_IndentedBlock(
        self, original_node: cst.IndentedBlock, updated_node: cst.IndentedBlock
    ) -> cst.IndentedBlock:
        new_body, carried, dropped = _filter_block_body(original_node.body, updated_node.body)
        for stmt in dropped:
            self._record_statement(stmt)
        if not new_body:
            new_body = (cst.SimpleStatementLine(body=[cst.Pass()]),)
        footer = carried + tuple(updated_node.footer) if carried else updated_node.footer
        return updated_node.with_changes(body=new_body, footer=footer)

    def leave_Module(
        self, original_node: cst.Module, updated_node: cst.Module
    ) -> cst.Module:
        new_body, carried, dropped = _filter_block_body(original_node.body, updated_node.body)
        for stmt in dropped:
            self._record_statement(stmt)
        footer = carried + tuple(updated_node.footer) if carried else updated_node.footer
        return updated_node.with_changes(body=new_body, footer=footer)


def strip_and_report(source: bytes) -> tuple[bytes, list[CommentMatch]]:
    """Strip ``source`` and report every comment/comment-like statement that was removed.

    A single traversal drives both: the returned matches are exactly what ``strip()`` removed,
    never a second independently-computed listing.
    """
    tree = cst.parse_module(source)
    transformer = _CommentStripper(tree)
    stripped = cst.MetadataWrapper(tree).visit(transformer).bytes
    matches = sorted(transformer.matches, key=lambda m: (m.start_line, m.end_line))
    return stripped, matches


def strip(source: bytes) -> bytes:
    """Strip comments and comment-like orphan-string statements from Python ``source``.

    Bytes in, bytes out: BOM, CRLF, and the source's inferred encoding round-trip for free via
    libcst's own ``Module.encoding`` inference.
    """
    return strip_and_report(source)[0]


def find_comments(source: bytes) -> list[CommentMatch]:
    """List every comment/comment-like statement ``strip(source)`` would remove, without writing."""
    return strip_and_report(source)[1]


def strip_text(source: str) -> str:
    """Convenience ``str`` wrapper around :func:`strip`. The tested/canonical path is bytes-based."""
    return strip(source.encode("utf-8")).decode("utf-8")
