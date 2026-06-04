"""The canonical machine-readable ``.qp`` grammar (``qp.lark``).

The file ships with the package as the **normative grammar** of the format. It is *not* the
production parser — that remains the hand-written, zero-dependency recursive-descent parser in
:mod:`qprogram.serialization.parser` — but CI executes it (``tests/test_grammar.py``, dev-only
``lark`` dependency) to guarantee the two never drift: everything the writer emits parses under
the grammar, and syntactically malformed inputs are rejected by both.

:func:`grammar_text` returns the grammar source; :func:`parser` builds the reference Lark parser
(LALR + a 2-space :class:`~lark.indenter.Indenter`) when ``lark`` is installed.
"""

from __future__ import annotations

from importlib import resources
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lark import Lark

GRAMMAR_RESOURCE = "qp.lark"


def grammar_text() -> str:
    """Return the source of the canonical ``qp.lark`` grammar."""
    return (resources.files("qprogram.grammar") / GRAMMAR_RESOURCE).read_text(encoding="utf-8")


def parser() -> Lark:
    """Build the reference Lark parser for the grammar (requires the ``lark`` package).

    LALR with a 2-space indentation postlexer — the executable form of the grammar used by the
    CI cross-check. ``.qp`` is strictly line-based, so no bracket types suppress newlines.

    Raises:
        ModuleNotFoundError: When ``lark`` is not installed (it is a dev-only dependency of
            qprogram; install it explicitly to use the reference parser).
    """
    from lark import Lark  # noqa: PLC0415 — optional dependency
    from lark.indenter import Indenter  # noqa: PLC0415

    class _QpIndenter(Indenter):
        NL_type = "_NL"
        OPEN_PAREN_types: tuple[str, ...] = ()  # .qp is strictly line-based
        CLOSE_PAREN_types: tuple[str, ...] = ()
        INDENT_type = "_INDENT"
        DEDENT_type = "_DEDENT"
        tab_len = 8

    return Lark(grammar_text(), parser="lalr", postlex=_QpIndenter(), maybe_placeholders=True)


def parse_text(text: str) -> object:
    """Parse ``text`` with the reference parser, returning the Lark tree.

    Normalizes a missing trailing newline first (the grammar, like Lark's own indented-language
    examples, expects every statement to end in one; the production writer always emits it).
    """
    if not text.endswith("\n"):
        text += "\n"
    return parser().parse(text)


__all__ = ["GRAMMAR_RESOURCE", "grammar_text", "parse_text", "parser"]
