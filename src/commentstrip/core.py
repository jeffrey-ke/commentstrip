"""Losslessly strip Python comments and comment-like orphan string statements.

Built on ``libcst`` (a lossless concrete-syntax-tree library): every byte that is not part of a
``#``-comment or a comment-like standalone string-statement is preserved byte-for-byte, including
whitespace, line endings, BOM, and encoding.
"""
from __future__ import annotations

from typing import Sequence

import libcst as cst


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


def _filter_small_stmts(
    body: Sequence[cst.BaseSmallStatement],
) -> tuple[cst.BaseSmallStatement, ...]:
    """Drop comment-like small-statements from a flat semicolon-joined statement sequence.

    If the surviving last item's ``.semicolon`` is an explicit ``cst.Semicolon`` (not
    ``MaybeSentinel.DEFAULT``), reset it — otherwise a dangling ``x = 1; `` survives with stray
    trailing whitespace once whatever followed it on the line is gone.
    """
    kept = tuple(stmt for stmt in body if not _is_comment_like_small_stmt(stmt))
    if kept and not isinstance(kept[-1].semicolon, cst.MaybeSentinel):
        kept = kept[:-1] + (kept[-1].with_changes(semicolon=cst.MaybeSentinel.DEFAULT),)
    return kept


def _is_whole_line_comment(stmt: cst.BaseStatement) -> bool:
    """True if every small-statement on this line is comment-like (drop the whole line)."""
    if not isinstance(stmt, cst.SimpleStatementLine):
        return False
    return all(_is_comment_like_small_stmt(s) for s in stmt.body)


def _filter_block_body(
    body: Sequence[cst.BaseStatement],
) -> tuple[tuple[cst.BaseStatement, ...], tuple[cst.EmptyLine, ...]]:
    """Drop whole comment-only ``SimpleStatementLine`` entries from a block's statement list.

    Returns the filtered body plus any ``leading_lines`` orphaned by a dropped *trailing*
    statement — the caller carries these onto the block's own footer. A dropped statement's
    ``leading_lines`` are otherwise carried onto the next surviving statement, so a blank line
    that was spacing *around* the removed statement doesn't silently collapse adjacent code
    together.
    """
    kept: list[cst.BaseStatement] = []
    carried: tuple[cst.EmptyLine, ...] = ()

    for stmt in body:
        if _is_whole_line_comment(stmt):
            carried = carried + tuple(stmt.leading_lines)
            continue
        if carried:
            stmt = stmt.with_changes(leading_lines=carried + tuple(stmt.leading_lines))
            carried = ()
        kept.append(stmt)

    return tuple(kept), carried


class _CommentStripper(cst.CSTTransformer):
    def leave_TrailingWhitespace(
        self, original_node: cst.TrailingWhitespace, updated_node: cst.TrailingWhitespace
    ) -> cst.TrailingWhitespace:
        # inline/trailing "# comment" after code on a line
        if original_node.comment is not None:
            return updated_node.with_changes(comment=None, whitespace=cst.SimpleWhitespace(""))
        return updated_node

    def leave_EmptyLine(
        self, original_node: cst.EmptyLine, updated_node: cst.EmptyLine
    ):
        # comment-only line (or blank line with a comment) — drop the whole line, no dangling blank
        if original_node.comment is not None:
            return cst.RemoveFromParent()
        return updated_node

    def leave_SimpleStatementLine(
        self, original_node: cst.SimpleStatementLine, updated_node: cst.SimpleStatementLine
    ) -> cst.SimpleStatementLine:
        new_body = _filter_small_stmts(updated_node.body)
        if not new_body:
            # Fully comment-like line (e.g. a bare docstring): leave untouched here — the parent
            # block/module body filter drops the whole line (and carries its leading_lines).
            return updated_node
        return updated_node.with_changes(body=new_body)

    def leave_SimpleStatementSuite(
        self, original_node: cst.SimpleStatementSuite, updated_node: cst.SimpleStatementSuite
    ) -> cst.SimpleStatementSuite:
        # The one-liner `if True: "x"` form: no block wrapper, no leading_lines/footer — the
        # suite *is* the whole body, so an emptied suite substitutes `pass` directly.
        new_body = _filter_small_stmts(updated_node.body)
        if not new_body:
            new_body = (cst.Pass(),)
        return updated_node.with_changes(body=new_body)

    def leave_IndentedBlock(
        self, original_node: cst.IndentedBlock, updated_node: cst.IndentedBlock
    ) -> cst.IndentedBlock:
        new_body, carried = _filter_block_body(updated_node.body)
        if not new_body:
            new_body = (cst.SimpleStatementLine(body=[cst.Pass()]),)
        footer = carried + tuple(updated_node.footer) if carried else updated_node.footer
        return updated_node.with_changes(body=new_body, footer=footer)

    def leave_Module(
        self, original_node: cst.Module, updated_node: cst.Module
    ) -> cst.Module:
        new_body, carried = _filter_block_body(updated_node.body)
        footer = carried + tuple(updated_node.footer) if carried else updated_node.footer
        return updated_node.with_changes(body=new_body, footer=footer)


def strip(source: bytes) -> bytes:
    """Strip comments and comment-like orphan-string statements from Python ``source``.

    Bytes in, bytes out: BOM, CRLF, and the source's inferred encoding round-trip for free via
    libcst's own ``Module.encoding`` inference.
    """
    tree = cst.parse_module(source)
    return tree.visit(_CommentStripper()).bytes


def strip_text(source: str) -> str:
    """Convenience ``str`` wrapper around :func:`strip`. The tested/canonical path is bytes-based."""
    return strip(source.encode("utf-8")).decode("utf-8")
